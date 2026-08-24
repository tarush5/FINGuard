"""Alerts, case management, fraud rings and graph intelligence."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, PaginationDep, SortingDep, require
from app.core.errors import NotFoundError, ValidationError
from app.core.rbac import Permission
from app.db.models.identity import User
from app.db.models.risk import Alert, Case, FraudRing, FraudRingMember
from app.services import audit
from app.services import cases as case_service
from app.services import graph as graph_service
from app.services.serializers import serialize_alert, serialize_ring

router = APIRouter(tags=["fraud"])


class CaseStatusUpdate(BaseModel):
    status: str = Field(
        pattern="^(NEW|INVESTIGATING|ESCALATED|CONFIRMED_FRAUD|FALSE_POSITIVE|RESOLVED)$"
    )
    notes: str | None = Field(default=None, max_length=4000)


class CaseAssign(BaseModel):
    user_id: str = Field(min_length=1, max_length=40)


class CaseNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class AlertStatusUpdate(BaseModel):
    status: str = Field(pattern="^(OPEN|TRIAGED|DISMISSED|ESCALATED)$")


# --------------------------------------------------------------------- alerts


@router.get("/alerts", summary="List alerts")
def list_alerts(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ALERT_READ))],
    page: PaginationDep,
    severity: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    alert_type: Annotated[str | None, Query()] = None,
    customer_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Alert)
    count_stmt = select(func.count()).select_from(Alert)
    conditions = []
    if severity:
        conditions.append(Alert.severity == severity.upper())
    if status_filter:
        conditions.append(Alert.status == status_filter.upper())
    if alert_type:
        conditions.append(Alert.alert_type == alert_type.upper())
    if customer_id:
        conditions.append(Alert.customer_id == customer_id)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(
        stmt.order_by(Alert.created_at.desc()).offset(page.offset).limit(page.limit)
    ).scalars()
    return page.envelope([serialize_alert(alert) for alert in rows], total)


@router.patch("/alerts/{alert_id}", summary="Update alert status")
def update_alert(
    alert_id: str,
    payload: AlertStatusUpdate,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_WRITE))],
) -> dict[str, Any]:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} was not found.", code="ALERT_NOT_FOUND")
    previous, alert.status = alert.status, payload.status
    audit.record(
        db,
        action="alert.status_changed",
        entity_type="ALERT",
        entity_id=alert.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"from": previous, "to": payload.status},
    )
    db.commit()
    return serialize_alert(alert)


# ---------------------------------------------------------------------- cases


@router.get("/cases", summary="List investigation cases")
def list_cases(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_READ))],
    page: PaginationDep,
    sort: SortingDep,
    search: Annotated[str | None, Query(max_length=80)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority: Annotated[str | None, Query()] = None,
    risk_band: Annotated[str | None, Query()] = None,
    assigned_to: Annotated[str | None, Query()] = None,
    mine: Annotated[bool, Query(description="Only cases assigned to me")] = False,
) -> dict[str, Any]:
    stmt = select(Case)
    count_stmt = select(func.count()).select_from(Case)
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                Case.case_number.ilike(like),
                Case.title.ilike(like),
                Case.customer_id.ilike(like),
                Case.primary_transaction_id.ilike(like),
            )
        )
    if status_filter:
        conditions.append(Case.status == status_filter.upper())
    if priority:
        conditions.append(Case.priority == priority.upper())
    if risk_band:
        conditions.append(Case.risk_band == risk_band.upper())
    if assigned_to:
        conditions.append(Case.assigned_to == assigned_to)
    if mine:
        conditions.append(Case.assigned_to == user.id)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = sort.apply(
        stmt,
        {
            "risk_score": Case.risk_score,
            "created_at": Case.created_at,
            "exposure_amount": Case.exposure_amount,
            "sla_due_at": Case.sla_due_at,
        },
        Case.created_at,
    )
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(stmt.offset(page.offset).limit(page.limit)).scalars()
    envelope = page.envelope([case_service.serialize_case(case) for case in rows], total)
    envelope["statuses"] = list(case_service.CASE_STATUSES)
    return envelope


@router.get("/cases/{case_id}", summary="Full case investigation payload")
def get_case(
    case_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_READ))],
) -> dict[str, Any]:
    case = case_service.get_case_or_404(db, case_id)
    return case_service.case_detail(db, case, mask_pii=user.mask_pii)


@router.patch("/cases/{case_id}/status", summary="Move a case through the workflow")
def update_case_status(
    case_id: str,
    payload: CaseStatusUpdate,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_WRITE))],
) -> dict[str, Any]:
    case = case_service.get_case_or_404(db, case_id)
    previous = case.status
    case_service.transition(
        db,
        case,
        status=payload.status,
        actor=user.full_name,
        actor_id=user.id,
        notes=payload.notes,
    )
    audit.record(
        db,
        action="case.status_changed",
        entity_type="CASE",
        entity_id=case.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        reason=payload.notes,
        details={"from": previous, "to": payload.status},
    )
    db.commit()
    return case_service.serialize_case(case)


@router.post("/cases/{case_id}/assign", summary="Assign a case to an analyst")
def assign_case(
    case_id: str,
    payload: CaseAssign,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_ASSIGN))],
) -> dict[str, Any]:
    case = case_service.get_case_or_404(db, case_id)
    assignee = db.get(User, payload.user_id)
    if assignee is None or not assignee.is_active:
        raise ValidationError("The selected analyst does not exist or is inactive.")
    case_service.assign(
        db, case, user_id=assignee.id, user_name=assignee.full_name, actor=user.full_name
    )
    audit.record(
        db,
        action="case.assigned",
        entity_type="CASE",
        entity_id=case.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"assigned_to": assignee.email},
    )
    db.commit()
    return case_service.serialize_case(case)


@router.post(
    "/cases/{case_id}/notes", status_code=status.HTTP_201_CREATED, summary="Add a case note"
)
def add_note(
    case_id: str,
    payload: CaseNoteCreate,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_WRITE))],
) -> dict[str, Any]:
    case = case_service.get_case_or_404(db, case_id)
    note = case_service.add_note(
        db, case, body=payload.body, author_id=user.id, author_name=user.full_name
    )
    audit.record(
        db,
        action="case.note_added",
        entity_type="CASE",
        entity_id=case.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
    )
    db.commit()
    return {
        "id": note.id,
        "case_id": case.id,
        "body": note.body,
        "author_name": note.author_name,
        "is_ai_generated": note.is_ai_generated,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


@router.get("/cases/{case_id}/timeline", summary="Case timeline")
def case_timeline(
    case_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CASE_READ))],
) -> dict[str, Any]:
    case = case_service.get_case_or_404(db, case_id)
    return {"case_id": case.id, "events": case_service.timeline(db, case)}


# ---------------------------------------------------------------- fraud rings


@router.get("/fraud-rings", summary="Detected fraud rings")
def list_rings(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.GRAPH_READ))],
    page: PaginationDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    min_risk: Annotated[float, Query(ge=0, le=100)] = 0.0,
) -> dict[str, Any]:
    stmt = select(FraudRing).where(FraudRing.risk_score >= min_risk)
    count_stmt = select(func.count()).select_from(FraudRing).where(FraudRing.risk_score >= min_risk)
    if status_filter:
        stmt = stmt.where(FraudRing.status == status_filter.upper())
        count_stmt = count_stmt.where(FraudRing.status == status_filter.upper())
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(
        stmt.order_by(FraudRing.risk_score.desc()).offset(page.offset).limit(page.limit)
    ).scalars()
    return page.envelope([serialize_ring(ring) for ring in rows], total)


@router.get("/fraud-rings/{ring_id}", summary="Fraud ring detail with member network")
def get_ring(
    ring_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.GRAPH_READ))],
) -> dict[str, Any]:
    ring = db.get(FraudRing, ring_id)
    if ring is None:
        raise NotFoundError(f"Fraud ring {ring_id} was not found.", code="RING_NOT_FOUND")
    members = [m.entity_id for m in ring.members if m.entity_type == "CUSTOMER"]
    network = graph_service.neighbourhood(db, "customer", members[0], depth=2) if members else None
    return {"ring": serialize_ring(ring), "network": network}


@router.post("/fraud-rings/detect", summary="Run ring detection now")
def detect_rings(
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.GRAPH_READ))],
    min_members: Annotated[int, Query(ge=2, le=50)] = 3,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> dict[str, Any]:
    rings = graph_service.detect_rings(db, min_members=min_members, days=days)
    audit.record(
        db,
        action="fraud_ring.detection_run",
        entity_type="FRAUD_RING",
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"rings_detected": len(rings), "min_members": min_members, "days": days},
    )
    db.commit()
    return {"detected": len(rings), "rings": rings[:25]}


@router.get("/graph/{entity_type}/{entity_id}", summary="Entity network neighbourhood")
def entity_graph(
    entity_type: str,
    entity_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.GRAPH_READ))],
    depth: Annotated[int, Query(ge=1, le=3)] = 2,
    max_nodes: Annotated[int, Query(ge=10, le=500)] = 200,
) -> dict[str, Any]:
    allowed = {"customer", "merchant", "device", "ip"}
    if entity_type.lower() not in allowed:
        raise ValidationError(f"entity_type must be one of {sorted(allowed)}.")
    result = graph_service.neighbourhood(
        db, entity_type.lower(), entity_id, depth=depth, max_nodes=max_nodes
    )
    if not result.get("found"):
        raise NotFoundError(
            f"No graph node exists for {entity_type} {entity_id} in the retained window.",
            code="GRAPH_NODE_NOT_FOUND",
        )
    return result


@router.get("/graph/summary", summary="Graph-wide statistics")
def graph_summary(
    db: DbSession, user: Annotated[Any, Depends(require(Permission.GRAPH_READ))]
) -> dict[str, Any]:
    from app.db.models.core import Device

    shared_devices = int(
        db.execute(
            select(func.count()).select_from(Device).where(Device.distinct_customers > 1)
        ).scalar_one()
        or 0
    )
    blacklisted = int(
        db.execute(
            select(func.count()).select_from(Device).where(Device.is_blacklisted.is_(True))
        ).scalar_one()
        or 0
    )
    rings = db.execute(
        select(
            func.count(FraudRing.id),
            func.coalesce(func.sum(FraudRing.member_count), 0),
            func.coalesce(func.sum(FraudRing.total_amount), 0.0),
            func.coalesce(func.max(FraudRing.risk_score), 0.0),
        )
    ).one()
    members = int(db.execute(select(func.count()).select_from(FraudRingMember)).scalar_one() or 0)
    return {
        "shared_devices": shared_devices,
        "blacklisted_devices": blacklisted,
        "rings": int(rings[0] or 0),
        "ring_members": members,
        "ring_exposure": round(float(rings[2] or 0), 2),
        "highest_ring_risk": round(float(rings[3] or 0), 2),
    }
