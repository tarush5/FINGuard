"""Alert and case management, including the analyst feedback loop."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models.core import Customer, Merchant, Transaction
from app.db.models.identity import Notification
from app.db.models.mlops import FeedbackLabel
from app.db.models.risk import Alert, Case, CaseEvent, CaseNote, Rule, RuleExecution
from app.events.bus import event_bus
from app.events.schemas import Topic, make_event
from app.utils import safe_float, to_utc

logger = get_logger(__name__)

CASE_STATUSES = (
    "NEW",
    "INVESTIGATING",
    "ESCALATED",
    "CONFIRMED_FRAUD",
    "FALSE_POSITIVE",
    "RESOLVED",
)
TERMINAL_STATUSES = {"CONFIRMED_FRAUD", "FALSE_POSITIVE", "RESOLVED"}
# Which transitions an analyst may perform. Enforced server side so the workflow
# cannot be skipped by calling the API directly.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"INVESTIGATING", "ESCALATED", "FALSE_POSITIVE", "CONFIRMED_FRAUD"},
    "INVESTIGATING": {"ESCALATED", "CONFIRMED_FRAUD", "FALSE_POSITIVE", "RESOLVED"},
    "ESCALATED": {"INVESTIGATING", "CONFIRMED_FRAUD", "FALSE_POSITIVE", "RESOLVED"},
    "CONFIRMED_FRAUD": {"RESOLVED"},
    "FALSE_POSITIVE": {"RESOLVED"},
    "RESOLVED": set(),
}

SLA_HOURS = {"CRITICAL": 2, "HIGH": 8, "MEDIUM": 24, "LOW": 72}


def next_case_number(db: Session) -> str:
    count = int(db.execute(select(func.count()).select_from(Case)).scalar_one() or 0)
    return f"FG-{40000 + count + 1}"


def create_alert(
    db: Session,
    *,
    transaction: Transaction,
    severity: str,
    title: str,
    description: str,
    triggered_rules: Sequence[dict[str, Any]] = (),
    alert_type: str = "TRANSACTION_RISK",
    details: dict[str, Any] | None = None,
) -> Alert:
    alert = Alert(
        id=new_id("ALT"),
        transaction_id=transaction.id,
        customer_id=transaction.customer_id,
        merchant_id=transaction.merchant_id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        description=description,
        risk_score=safe_float(transaction.risk_score),
        amount=safe_float(transaction.amount),
        status="OPEN",
        triggered_rules=list(triggered_rules),
        details=details or {},
    )
    db.add(alert)
    db.flush()
    event_bus.publish(
        make_event(
            Topic.ALERTS_CREATED,
            "alert.created",
            {
                "alert_id": alert.id,
                "transaction_id": transaction.id,
                "customer_id": transaction.customer_id,
                "severity": severity,
                "risk_score": alert.risk_score,
                "title": title,
            },
            partition_key=transaction.customer_id,
        )
    )
    return alert


def create_case(
    db: Session,
    *,
    transaction: Transaction | None,
    title: str,
    summary: str,
    risk_band: str,
    risk_score: float,
    alert: Alert | None = None,
    customer_id: str | None = None,
    merchant_id: str | None = None,
    fraud_ring_id: str | None = None,
    opened_by: str = "decision-engine",
    tags: Sequence[str] = (),
    evidence: dict[str, Any] | None = None,
) -> Case:
    priority = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM"}.get(risk_band, "LOW")
    now = utcnow()
    case = Case(
        id=new_id("CASE"),
        case_number=next_case_number(db),
        title=title,
        summary=summary,
        status="NEW",
        priority=priority,
        risk_band=risk_band,
        risk_score=risk_score,
        customer_id=customer_id or (transaction.customer_id if transaction else None),
        merchant_id=merchant_id or (transaction.merchant_id if transaction else None),
        primary_transaction_id=transaction.id if transaction else None,
        fraud_ring_id=fraud_ring_id,
        exposure_amount=safe_float(transaction.amount) if transaction else 0.0,
        transaction_count=1 if transaction else 0,
        opened_by=opened_by,
        sla_due_at=now + timedelta(hours=SLA_HOURS.get(priority, 24)),
        tags=list(tags),
        evidence=evidence or {},
    )
    db.add(case)
    db.flush()

    if alert is not None:
        alert.case_id = case.id
        alert.status = "TRIAGED"

    add_event(
        db,
        case,
        event_type="CASE_CREATED",
        description=f"Case opened automatically from decision {transaction.decision if transaction else 'manual'}.",
        actor=opened_by,
        severity="INFO",
    )
    if transaction is not None:
        seed_timeline_from_transaction(db, case, transaction)

    event_bus.publish(
        make_event(
            Topic.CASES_CREATED,
            "case.created",
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "risk_band": risk_band,
                "risk_score": risk_score,
                "customer_id": case.customer_id,
                "transaction_id": case.primary_transaction_id,
                "title": title,
            },
            partition_key=case.customer_id or case.id,
        )
    )
    return case


def add_event(
    db: Session,
    case: Case,
    *,
    event_type: str,
    description: str,
    actor: str = "system",
    actor_id: str | None = None,
    severity: str = "INFO",
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: Any | None = None,
) -> CaseEvent:
    event = CaseEvent(
        id=new_id("CEV"),
        case_id=case.id,
        occurred_at=occurred_at or utcnow(),
        event_type=event_type,
        actor=actor,
        actor_id=actor_id,
        description=description,
        severity=severity,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
    )
    db.add(event)
    return event


def seed_timeline_from_transaction(db: Session, case: Case, transaction: Transaction) -> None:
    """Reconstruct the lead-up to the flagged transaction as timeline events."""
    occurred = to_utc(transaction.occurred_at) or utcnow()

    if transaction.session_id:
        add_event(
            db,
            case,
            event_type="SESSION_START",
            description=f"Session {transaction.session_id} started on device {transaction.device_id or 'unknown'}.",
            occurred_at=occurred - timedelta(minutes=4),
            entity_type="SESSION",
            entity_id=transaction.session_id,
        )
    if transaction.device_id:
        add_event(
            db,
            case,
            event_type="DEVICE_SEEN",
            description=f"Device {transaction.device_id} used from {transaction.city or transaction.country}.",
            occurred_at=occurred - timedelta(minutes=3),
            entity_type="DEVICE",
            entity_id=transaction.device_id,
        )
    add_event(
        db,
        case,
        event_type="TRANSACTION",
        description=(
            f"{transaction.currency} {float(transaction.amount):,.2f} at merchant "
            f"{transaction.merchant_id} via {transaction.channel}."
        ),
        occurred_at=occurred,
        entity_type="TRANSACTION",
        entity_id=transaction.id,
        severity="WARNING",
        payload={"amount": float(transaction.amount), "channel": transaction.channel},
    )
    add_event(
        db,
        case,
        event_type="RISK_SCORED",
        description=f"Ensemble risk score {float(transaction.risk_score):.1f}/100 ({transaction.risk_band}).",
        occurred_at=occurred + timedelta(seconds=1),
        entity_type="TRANSACTION",
        entity_id=transaction.id,
        severity="WARNING" if transaction.risk_band in {"HIGH", "CRITICAL"} else "INFO",
    )
    add_event(
        db,
        case,
        event_type="DECISION",
        description=f"Decision engine returned {transaction.decision}.",
        occurred_at=occurred + timedelta(seconds=2),
        entity_type="TRANSACTION",
        entity_id=transaction.id,
        severity="CRITICAL" if transaction.decision == "DECLINE" else "WARNING",
    )


def timeline(db: Session, case: Case) -> list[dict[str, Any]]:
    events = db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case.id).order_by(CaseEvent.occurred_at.asc())
    ).scalars()
    return [
        {
            "id": event.id,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "event_type": event.event_type,
            "actor": event.actor,
            "description": event.description,
            "severity": event.severity,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "payload": event.payload,
        }
        for event in events
    ]


def assign(db: Session, case: Case, *, user_id: str, user_name: str, actor: str) -> Case:
    case.assigned_to = user_id
    case.assigned_to_name = user_name
    if case.status == "NEW":
        case.status = "INVESTIGATING"
    add_event(
        db,
        case,
        event_type="ASSIGNED",
        description=f"Case assigned to {user_name}.",
        actor=actor,
        entity_type="USER",
        entity_id=user_id,
    )
    return case


def transition(
    db: Session,
    case: Case,
    *,
    status: str,
    actor: str,
    actor_id: str | None = None,
    notes: str | None = None,
) -> Case:
    if status not in CASE_STATUSES:
        raise ValidationError(f"Unknown case status '{status}'.")
    allowed = ALLOWED_TRANSITIONS.get(case.status, set())
    if status != case.status and status not in allowed:
        raise ConflictError(
            f"Cannot move a case from {case.status} to {status}.",
            details={"allowed": sorted(allowed)},
        )

    previous = case.status
    case.status = status
    if status in TERMINAL_STATUSES:
        case.resolved_at = utcnow()
        case.resolution = status
        case.resolution_notes = notes or case.resolution_notes
    add_event(
        db,
        case,
        event_type="STATUS_CHANGED",
        description=f"Status changed from {previous} to {status}."
        + (f" Note: {notes}" if notes else ""),
        actor=actor,
        actor_id=actor_id,
        severity="CRITICAL" if status == "CONFIRMED_FRAUD" else "INFO",
        payload={"from": previous, "to": status},
    )

    if status in {"CONFIRMED_FRAUD", "FALSE_POSITIVE"}:
        record_feedback(
            db,
            case=case,
            verdict=status,
            actor=actor,
            actor_id=actor_id,
            notes=notes or "",
        )
    return case


def add_note(
    db: Session,
    case: Case,
    *,
    body: str,
    author_id: str | None,
    author_name: str,
    is_ai_generated: bool = False,
) -> CaseNote:
    if not body.strip():
        raise ValidationError("Note body cannot be empty.")
    note = CaseNote(
        id=new_id("NOTE"),
        case_id=case.id,
        author_id=author_id,
        author_name=author_name,
        body=body.strip(),
        is_ai_generated=is_ai_generated,
    )
    db.add(note)
    add_event(
        db,
        case,
        event_type="NOTE_ADDED",
        description=("AI note added." if is_ai_generated else f"Note added by {author_name}."),
        actor=author_name,
        actor_id=author_id,
    )
    return note


def record_feedback(
    db: Session,
    *,
    case: Case,
    verdict: str,
    actor: str,
    actor_id: str | None,
    notes: str = "",
) -> FeedbackLabel | None:
    """Turn an analyst verdict into a labelled training example."""
    if not case.primary_transaction_id:
        return None
    transaction = db.get(Transaction, case.primary_transaction_id)
    if transaction is None:
        return None

    label = 1 if verdict == "CONFIRMED_FRAUD" else 0
    feedback = FeedbackLabel(
        id=new_id("FB"),
        transaction_id=transaction.id,
        case_id=case.id,
        label=label,
        verdict=verdict,
        analyst_id=actor_id,
        analyst_name=actor,
        predicted_probability=safe_float(transaction.fraud_probability),
        model_version=transaction.model_version,
        notes=notes,
    )
    db.add(feedback)

    transaction.is_fraud = bool(label)
    transaction.label_source = "analyst"
    transaction.labelled_at = utcnow()
    if label:
        transaction.fraud_type = transaction.fraud_type or "ANALYST_CONFIRMED"

    customer = db.get(Customer, transaction.customer_id)
    if customer and label:
        customer.confirmed_fraud_count = (customer.confirmed_fraud_count or 0) + 1
        customer.watchlisted = True

    # Credit or debit the rules that fired on this transaction so rule precision
    # reflects analyst outcomes rather than trigger volume.
    executions = db.execute(
        select(RuleExecution).where(
            RuleExecution.transaction_id == transaction.id, RuleExecution.triggered.is_(True)
        )
    ).scalars()
    for execution in executions:
        rule = db.get(Rule, execution.rule_id)
        if rule is None:
            continue
        if label:
            rule.true_positive_count += 1
        else:
            rule.false_positive_count += 1

    event_bus.publish(
        make_event(
            Topic.ANALYST_FEEDBACK,
            "analyst.feedback",
            {
                "case_id": case.id,
                "transaction_id": transaction.id,
                "verdict": verdict,
                "label": label,
                "analyst": actor,
                "predicted_probability": safe_float(transaction.fraud_probability),
                "model_version": transaction.model_version,
            },
            partition_key=transaction.customer_id,
        )
    )
    return feedback


def case_detail(db: Session, case: Case, *, mask_pii: bool = True) -> dict[str, Any]:
    """Assemble the full investigation payload for one case."""
    from app.services.serializers import (
        serialize_customer,
        serialize_merchant,
        serialize_transaction,
    )

    transaction = (
        db.get(Transaction, case.primary_transaction_id) if case.primary_transaction_id else None
    )
    customer = db.get(Customer, case.customer_id) if case.customer_id else None
    merchant = db.get(Merchant, case.merchant_id) if case.merchant_id else None

    related = []
    if customer is not None:
        related = list(
            db.execute(
                select(Transaction)
                .where(Transaction.customer_id == customer.id)
                .order_by(Transaction.occurred_at.desc())
                .limit(25)
            ).scalars()
        )

    notes = list(
        db.execute(
            select(CaseNote).where(CaseNote.case_id == case.id).order_by(CaseNote.created_at.desc())
        ).scalars()
    )
    alerts = list(
        db.execute(
            select(Alert).where(Alert.case_id == case.id).order_by(Alert.created_at.desc())
        ).scalars()
    )

    return {
        "case": serialize_case(case),
        "transaction": serialize_transaction(transaction) if transaction else None,
        "customer": serialize_customer(customer, mask_pii=mask_pii) if customer else None,
        "merchant": serialize_merchant(merchant) if merchant else None,
        "timeline": timeline(db, case),
        "related_transactions": [serialize_transaction(t) for t in related],
        "notes": [
            {
                "id": note.id,
                "author_name": note.author_name,
                "body": note.body,
                "is_ai_generated": note.is_ai_generated,
                "created_at": note.created_at.isoformat() if note.created_at else None,
            }
            for note in notes
        ],
        "alerts": [
            {
                "id": alert.id,
                "severity": alert.severity,
                "title": alert.title,
                "description": alert.description,
                "status": alert.status,
                "risk_score": safe_float(alert.risk_score),
                "created_at": alert.created_at.isoformat() if alert.created_at else None,
            }
            for alert in alerts
        ],
    }


def serialize_case(case: Case) -> dict[str, Any]:
    return {
        "id": case.id,
        "case_number": case.case_number,
        "title": case.title,
        "summary": case.summary,
        "status": case.status,
        "priority": case.priority,
        "risk_band": case.risk_band,
        "risk_score": safe_float(case.risk_score),
        "customer_id": case.customer_id,
        "merchant_id": case.merchant_id,
        "primary_transaction_id": case.primary_transaction_id,
        "fraud_ring_id": case.fraud_ring_id,
        "exposure_amount": safe_float(case.exposure_amount),
        "recovered_amount": safe_float(case.recovered_amount),
        "transaction_count": case.transaction_count,
        "assigned_to": case.assigned_to,
        "assigned_to_name": case.assigned_to_name,
        "opened_by": case.opened_by,
        "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        "resolution": case.resolution,
        "resolution_notes": case.resolution_notes,
        "ai_summary": case.ai_summary,
        "tags": case.tags or [],
        "evidence": case.evidence or {},
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        case = db.execute(select(Case).where(Case.case_number == case_id)).scalar_one_or_none()
    if case is None:
        raise NotFoundError(f"Case {case_id} was not found.", code="CASE_NOT_FOUND")
    return case


def notify(
    db: Session,
    *,
    severity: str,
    category: str,
    title: str,
    body: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    link: str | None = None,
    target_role: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Notification:
    notification = Notification(
        id=new_id("NTF"),
        severity=severity,
        category=category,
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=entity_id,
        link=link,
        target_role=target_role,
        payload=payload or {},
    )
    db.add(notification)
    return notification


def pick_assignee(db: Session) -> tuple[str | None, str | None]:
    """Round-robin-ish assignment across active fraud investigators."""
    from app.db.models.identity import RoleRecord, User

    users = list(
        db.execute(
            select(User)
            .join(User.roles)
            .where(
                User.is_active.is_(True),
                User.is_deleted.is_(False),
                RoleRecord.name.in_(["FRAUD_INVESTIGATOR", "RISK_ANALYST"]),
            )
        )
        .unique()
        .scalars()
    )
    if not users:
        return None, None
    chosen = random.choice(users)
    return chosen.id, chosen.full_name
