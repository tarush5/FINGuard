"""One-click demonstration scenarios.

Each scenario builds real transactions and pushes them through the live decision
path -- the same code that serves ``POST /transactions``.  Nothing is mocked:
the resulting scores, alerts and cases are genuine platform output, and the
response tells you exactly where to look at them in the UI.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require
from app.core.config import settings
from app.core.errors import ValidationError
from app.core.rbac import Permission
from app.db.base import new_id, utcnow
from app.db.models.core import Customer, Merchant
from app.db.models.platform import DemoScenarioRun
from app.db.models.risk import Case
from app.events.bus import event_bus
from app.services import audit
from app.services import cases as case_service
from app.services.pipeline import TransactionInput, process_transaction, publish_pending
from app.utils import safe_float

router = APIRouter(prefix="/demo", tags=["demo"])

SCENARIOS: dict[str, dict[str, str]] = {
    "account_takeover": {
        "name": "Account takeover",
        "narrative": "New device -> impossible travel -> escalating cash-out at high-risk merchants.",
        "expected": "Rules R-GEO-001/R-ATO-001 and R-DEV-001 fire; risk escalates into review or decline.",
    },
    "fraud_ring": {
        "name": "Coordinated fraud ring",
        "narrative": "Several accounts transacting from one shared device and IP within minutes.",
        "expected": "Device and IP fan-out rules fire; graph risk climbs; ring detection groups the accounts.",
    },
    "card_testing": {
        "name": "Card testing",
        "narrative": "A burst of tiny authorisations followed by one large purchase.",
        "expected": "Velocity and card-testing rules fire on the burst; the large purchase inherits the elevated profile.",
    },
    "false_positive": {
        "name": "Legitimate high-value purchase",
        "narrative": "A genuine large purchase is held for review, then cleared by an analyst.",
        "expected": "Case is opened, resolved as FALSE_POSITIVE, and the label enters the retraining set.",
    },
    "model_drift": {
        "name": "Feature drift",
        "narrative": "A batch of transactions with a shifted amount and velocity profile arrives.",
        "expected": "PSI crosses the warning threshold and a drift alert is raised for the data science team.",
    },
}


class ScenarioRequest(BaseModel):
    scenario: str = Field(
        pattern="^(account_takeover|fraud_ring|card_testing|false_positive|model_drift)$"
    )
    intensity: int = Field(default=1, ge=1, le=3, description="Scales the number of events")


def _pick_customers(db: DbSession, count: int, *, min_transactions: int = 15) -> list[Customer]:
    rows = list(
        db.execute(
            select(Customer)
            .where(Customer.transaction_count >= min_transactions, Customer.is_deleted.is_(False))
            .order_by(Customer.transaction_count.desc())
            .limit(max(count * 4, 20))
        ).scalars()
    )
    if len(rows) < count:
        raise ValidationError(
            "Not enough seeded customers with history. Run the seeder first "
            "(`python -m app.datagen.seed --reset`).",
            code="DEMO_DATA_MISSING",
        )
    # Spread the picks so repeat runs do not always hit the same accounts.
    step = max(len(rows) // count, 1)
    return [rows[i * step] for i in range(count)]


def _pick_merchants(db: DbSession, count: int, *, high_risk: bool = False) -> list[Merchant]:
    stmt = select(Merchant).where(Merchant.is_deleted.is_(False))
    if high_risk:
        stmt = stmt.where(Merchant.high_risk_flag.is_(True))
    rows = list(
        db.execute(stmt.order_by(Merchant.risk_score.desc()).limit(max(count, 5))).scalars()
    )
    if not rows:
        rows = list(db.execute(select(Merchant).limit(count)).scalars())
    if not rows:
        raise ValidationError("No merchants are seeded.", code="DEMO_DATA_MISSING")
    return (rows * count)[:count]


# Scenario runners share one transaction boundary; their events are published
# only after the commit (see publish_pending below).
_PENDING: list[Any] = []


def _submit(db: DbSession, payload: TransactionInput) -> dict[str, Any]:
    result = process_transaction(db, payload, commit=False)
    _PENDING.append(result)
    return {
        "transaction_id": result.transaction_id,
        "decision": result.decision,
        "risk_score": result.risk_score,
        "risk_band": result.risk_band,
        "triggered_rules": (
            [
                hit["code"]
                for hit in result.trace.get("stages", [{}, {}])[1]
                .get("detail", {})
                .get("triggered", [])
            ]
            if result.trace
            else []
        ),
    }


def _base_input(customer: Customer, merchant: Merchant, **overrides: Any) -> TransactionInput:
    payload = TransactionInput(
        event_id=f"evt_{uuid.uuid4().hex}",
        transaction_id=f"TXN-{uuid.uuid4().hex[:16].upper()}",
        customer_id=customer.id,
        merchant_id=merchant.id,
        amount=max(safe_float(customer.avg_transaction_amount), 1500.0),
        currency=settings.currency,
        occurred_at=utcnow(),
        country=customer.country,
        city=customer.city,
        latitude=customer.home_latitude,
        longitude=customer.home_longitude,
        merchant_category=merchant.category,
        is_demo=True,
        metadata={"scenario": True},
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


def _run_account_takeover(db: DbSession, intensity: int) -> dict[str, Any]:
    customer = _pick_customers(db, 1)[0]
    merchants = _pick_merchants(db, 3, high_risk=True)
    device = f"D-DEMO{uuid.uuid4().hex[:8].upper()}"
    ip = f"185.{uuid.uuid4().int % 250}.44.19"
    session = f"S-DEMO{uuid.uuid4().hex[:6].upper()}"
    now = utcnow()

    events = []
    # A normal home-city transaction establishes the baseline location.
    events.append(
        _base_input(
            customer,
            merchants[0],
            occurred_at=now - timedelta(minutes=12),
            amount=round(max(safe_float(customer.avg_transaction_amount), 1500.0) * 0.8, 2),
            session_id=session,
        )
    )
    # Then the takeover: new device, far away, escalating amounts.
    for index in range(2 + intensity):
        events.append(
            _base_input(
                customer,
                merchants[index % len(merchants)],
                occurred_at=now - timedelta(minutes=8 - index * 2),
                amount=round(
                    max(safe_float(customer.avg_transaction_amount), 1500.0) * (5 + 2.5 * index), 2
                ),
                device_id=device,
                ip_address=ip,
                city="London",
                country="GB",
                latitude=51.5074,
                longitude=-0.1278,
                channel="WEB",
                session_id=session,
            )
        )
    return {
        "transactions": [_submit(db, event) for event in events],
        "highlight": "The second transaction onward runs from a new device 7,000 km away minutes later.",
    }


def _run_fraud_ring(db: DbSession, intensity: int) -> dict[str, Any]:
    members = _pick_customers(db, 3 + intensity, min_transactions=5)
    merchants = _pick_merchants(db, 2, high_risk=True)
    device = f"D-RINGDEMO{uuid.uuid4().hex[:6].upper()}"
    ip = f"45.{uuid.uuid4().int % 250}.77.9"
    now = utcnow()

    results = []
    for index, member in enumerate(members):
        for step in range(2):
            results.append(
                _submit(
                    db,
                    _base_input(
                        member,
                        merchants[step % len(merchants)],
                        occurred_at=now - timedelta(minutes=25 - index * 3 - step),
                        amount=round(
                            max(safe_float(member.avg_transaction_amount), 2000.0) * 3.2, 2
                        ),
                        device_id=device,
                        ip_address=ip,
                        channel="WEB",
                    ),
                )
            )
    return {
        "transactions": results,
        "shared_device": device,
        "shared_ip": ip,
        "members": [m.id for m in members],
        "highlight": (
            f"{len(members)} accounts transacted from device {device}; run ring detection "
            "to see them clustered."
        ),
    }


def _run_card_testing(db: DbSession, intensity: int) -> dict[str, Any]:
    customer = _pick_customers(db, 1)[0]
    merchant = _pick_merchants(db, 1)[0]
    device = f"D-TESTDEMO{uuid.uuid4().hex[:6].upper()}"
    ip = f"185.{uuid.uuid4().int % 250}.12.4"
    now = utcnow()

    results = []
    for index in range(6 + 3 * intensity):
        results.append(
            _submit(
                db,
                _base_input(
                    customer,
                    merchant,
                    occurred_at=now - timedelta(seconds=240 - index * 20),
                    amount=round(35 + index * 7.5, 2),
                    device_id=device,
                    ip_address=ip,
                    channel="API",
                    payment_method="CARD",
                ),
            )
        )
    results.append(
        _submit(
            db,
            _base_input(
                customer,
                merchant,
                occurred_at=now,
                amount=round(max(safe_float(customer.avg_transaction_amount), 2000.0) * 6, 2),
                device_id=device,
                ip_address=ip,
                channel="WEB",
            ),
        )
    )
    return {
        "transactions": results,
        "highlight": "Small probes accumulate velocity; the final large purchase lands on an already-elevated profile.",
    }


def _run_false_positive(db: DbSession, intensity: int, actor: CurrentUser) -> dict[str, Any]:
    customer = _pick_customers(db, 1)[0]
    merchant = _pick_merchants(db, 1)[0]
    result = _submit(
        db,
        _base_input(
            customer,
            merchant,
            amount=round(max(safe_float(customer.avg_transaction_amount), 3000.0) * 9, 2),
            occurred_at=utcnow(),
            channel="MOBILE_APP",
            metadata={"scenario": True, "legitimate": True},
        ),
    )
    db.commit()

    # Publish now that the row is visible, then let the risk consumer create the
    # alert and case before the analyst verdict is applied.
    publish_pending(_PENDING)
    _PENDING.clear()
    event_bus.drain(timeout=5.0)

    case = db.execute(
        select(Case).where(Case.primary_transaction_id == result["transaction_id"]).limit(1)
    ).scalar_one_or_none()
    resolution = None
    if case is not None:
        case_service.transition(
            db, case, status="INVESTIGATING", actor=actor.full_name, actor_id=actor.id
        )
        case_service.transition(
            db,
            case,
            status="FALSE_POSITIVE",
            actor=actor.full_name,
            actor_id=actor.id,
            notes="Customer confirmed the purchase on a verified call-back. Releasing the hold.",
        )
        resolution = {
            "case_id": case.id,
            "case_number": case.case_number,
            "status": case.status,
        }
    return {
        "transactions": [result],
        "case": resolution,
        "highlight": (
            "The analyst verdict became a labelled training example; see " "ML Studio -> Feedback."
            if resolution
            else "The transaction scored below the case threshold, so no case was opened."
        ),
    }


def _run_model_drift(db: DbSession, intensity: int) -> dict[str, Any]:
    """Inject a batch with a shifted amount/velocity profile, then measure PSI."""
    from app.ml.drift import compute_drift

    customers = _pick_customers(db, 4, min_transactions=5)
    merchants = _pick_merchants(db, 3)
    now = utcnow()
    submitted = []
    for index in range(20 * intensity):
        customer = customers[index % len(customers)]
        merchant = merchants[index % len(merchants)]
        submitted.append(
            _submit(
                db,
                _base_input(
                    customer,
                    merchant,
                    occurred_at=now - timedelta(seconds=index * 45),
                    # Deliberately shifted distribution: much larger amounts.
                    amount=round(max(safe_float(customer.avg_transaction_amount), 2000.0) * 4.5, 2),
                    device_id=f"D-DRIFT{index % 3}{uuid.uuid4().hex[:4].upper()}",
                    ip_address=f"103.{index % 250}.9.{(index % 200) + 1}",
                    channel="API",
                ),
            )
        )
    db.commit()
    publish_pending(_PENDING)
    _PENDING.clear()
    drift_result = compute_drift(db, window_days=1)
    db.commit()
    return {
        "transactions": submitted[:10],
        "transactions_submitted": len(submitted),
        "drift": {
            "status": drift_result.get("status"),
            "features": [
                f for f in drift_result.get("features", []) if f.get("status") != "HEALTHY"
            ][:6],
        },
        "highlight": "Compare the amount distribution against the training baseline in ML Studio -> Monitoring.",
    }


@router.get("/scenarios", summary="Available demonstration scenarios")
def scenarios(
    user: Annotated[CurrentUser, Depends(require(Permission.TRANSACTION_READ))],
) -> dict[str, Any]:
    return {
        "mode": settings.platform_mode,
        "scenarios": [{"key": key, **value} for key, value in SCENARIOS.items()],
    }


@router.post("/run", summary="Run a demonstration scenario end to end")
def run_scenario(
    payload: ScenarioRequest,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.TRANSACTION_INGEST))],
) -> dict[str, Any]:
    started = time.perf_counter()
    spec = SCENARIOS[payload.scenario]
    _PENDING.clear()

    if payload.scenario == "account_takeover":
        outcome = _run_account_takeover(db, payload.intensity)
    elif payload.scenario == "fraud_ring":
        outcome = _run_fraud_ring(db, payload.intensity)
    elif payload.scenario == "card_testing":
        outcome = _run_card_testing(db, payload.intensity)
    elif payload.scenario == "false_positive":
        outcome = _run_false_positive(db, payload.intensity, user)
    else:
        outcome = _run_model_drift(db, payload.intensity)

    db.commit()
    publish_pending(_PENDING)
    _PENDING.clear()
    event_bus.drain(timeout=6.0)

    transaction_ids = [t["transaction_id"] for t in outcome.get("transactions", [])]
    cases = list(
        db.execute(
            select(Case).where(Case.primary_transaction_id.in_(transaction_ids or [""]))
        ).scalars()
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    record = DemoScenarioRun(
        id=new_id("DEMO"),
        scenario_key=payload.scenario,
        scenario_name=spec["name"],
        triggered_by=user.email,
        status="COMPLETED",
        duration_ms=duration_ms,
        transaction_ids=transaction_ids,
        case_ids=[case.id for case in cases],
        outcome={k: v for k, v in outcome.items() if k != "transactions"},
    )
    db.add(record)
    audit.record(
        db,
        action="demo.scenario_run",
        entity_type="DEMO_SCENARIO",
        entity_id=payload.scenario,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"transactions": len(transaction_ids), "cases": len(cases)},
    )
    db.commit()

    return {
        "scenario": payload.scenario,
        "name": spec["name"],
        "narrative": spec["narrative"],
        "expected": spec["expected"],
        "duration_ms": duration_ms,
        **outcome,
        "cases": [
            {
                "id": case.id,
                "case_number": case.case_number,
                "status": case.status,
                "risk_band": case.risk_band,
                "risk_score": safe_float(case.risk_score),
            }
            for case in cases
        ],
        "links": {
            "transactions": "/transactions",
            "cases": "/cases",
            "graph": "/fraud/rings",
            "monitoring": "/ml/monitoring",
        },
    }


@router.get("/runs", summary="Recent scenario runs")
def runs(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.TRANSACTION_READ))],
) -> dict[str, Any]:
    rows = list(
        db.execute(
            select(DemoScenarioRun).order_by(DemoScenarioRun.created_at.desc()).limit(20)
        ).scalars()
    )
    return {
        "items": [
            {
                "id": row.id,
                "scenario": row.scenario_key,
                "name": row.scenario_name,
                "triggered_by": row.triggered_by,
                "status": row.status,
                "duration_ms": safe_float(row.duration_ms),
                "transactions": len(row.transaction_ids or []),
                "cases": row.case_ids or [],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }
