"""Event consumers.

Each handler owns its own database session and is written to be idempotent, so
at-least-once delivery (Kafka commits after the handler runs) cannot create
duplicate alerts or cases.  Failures propagate so the bus can retry and, on
exhaustion, dead-letter the event.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.core import Transaction
from app.db.models.risk import Alert, Case
from app.db.session import session_scope
from app.events.bus import event_bus
from app.events.schemas import EventEnvelope, Topic
from app.services import cases as case_service
from app.services.monitoring import metrics

logger = get_logger(__name__)

SEVERITY_BY_BAND = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}


def on_risk_decided(event: EventEnvelope) -> None:
    """Create the alert and (when the decision requires it) the case."""
    payload = event.payload
    if not payload.get("requires_alert"):
        return
    transaction_id = payload["transaction_id"]

    with session_scope() as db:
        # Idempotency: an alert already exists for this transaction.
        existing = db.execute(
            select(Alert).where(Alert.transaction_id == transaction_id).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return

        transaction = db.get(Transaction, transaction_id)
        if transaction is None:
            # Raise so the bus retries with backoff and, if the row never
            # appears, dead-letters the event for operator visibility.
            raise LookupError(f"Transaction {transaction_id} is not visible yet.")

        band = payload.get("risk_band", "MEDIUM")
        triggered = payload.get("triggered_rules", [])
        top_factors = payload.get("top_factors", [])
        headline = (
            top_factors[0]["label"]
            if top_factors and top_factors[0].get("label")
            else payload.get("reason", "Elevated risk score")
        )
        alert = case_service.create_alert(
            db,
            transaction=transaction,
            severity=SEVERITY_BY_BAND.get(band, "MEDIUM"),
            title=f"{band} risk transaction {transaction.currency} {float(transaction.amount):,.2f}",
            description=str(headline),
            triggered_rules=[r.get("code") for r in triggered],
            details={
                "reason": payload.get("reason"),
                "reason_codes": payload.get("reason_codes", []),
                "top_factors": top_factors,
                "graph_signals": payload.get("graph_signals", []),
            },
        )
        metrics.increment("alerts.created")

        if payload.get("requires_case"):
            existing_case = db.execute(
                select(Case).where(Case.primary_transaction_id == transaction_id).limit(1)
            ).scalar_one_or_none()
            if existing_case is not None:
                return
            case = case_service.create_case(
                db,
                transaction=transaction,
                alert=alert,
                title=f"{band} risk on transaction {transaction.id}",
                summary=str(payload.get("reason", "")),
                risk_band=band,
                risk_score=float(payload.get("risk_score", 0.0)),
                evidence={
                    "top_factors": top_factors,
                    "triggered_rules": triggered,
                    "graph_signals": payload.get("graph_signals", []),
                },
                tags=[r.get("category") for r in triggered if r.get("category")][:4],
            )
            user_id, user_name = case_service.pick_assignee(db)
            if user_id:
                case_service.assign(
                    db, case, user_id=user_id, user_name=user_name or "", actor="auto-assignment"
                )
            metrics.increment("cases.created")


def on_alert_created(event: EventEnvelope) -> None:
    payload = event.payload
    with session_scope() as db:
        case_service.notify(
            db,
            severity=payload.get("severity", "MEDIUM"),
            category="FRAUD_ALERT",
            title=payload.get("title", "Fraud alert"),
            body=f"Transaction {payload.get('transaction_id')} scored {payload.get('risk_score')}/100.",
            entity_type="TRANSACTION",
            entity_id=payload.get("transaction_id"),
            link=f"/transactions/{payload.get('transaction_id')}",
            target_role="FRAUD_INVESTIGATOR",
            payload=payload,
        )


def on_case_created(event: EventEnvelope) -> None:
    payload = event.payload
    with session_scope() as db:
        case_service.notify(
            db,
            severity="CRITICAL" if payload.get("risk_band") == "CRITICAL" else "WARNING",
            category="CASE",
            title=f"Case {payload.get('case_number')} opened",
            body=payload.get("title", ""),
            entity_type="CASE",
            entity_id=payload.get("case_id"),
            link=f"/cases/{payload.get('case_id')}",
            target_role="FRAUD_INVESTIGATOR",
            payload=payload,
        )


def on_analyst_feedback(event: EventEnvelope) -> None:
    """Feedback is the retraining signal -- surface it and count it."""
    payload = event.payload
    metrics.increment("feedback.received")
    metrics.increment(f"feedback.{str(payload.get('verdict', 'unknown')).lower()}")
    with session_scope() as db:
        case_service.notify(
            db,
            severity="INFO",
            category="MODEL_FEEDBACK",
            title=f"Analyst verdict: {payload.get('verdict')}",
            body=(
                f"Transaction {payload.get('transaction_id')} was labelled by "
                f"{payload.get('analyst')} and added to the retraining dataset."
            ),
            entity_type="TRANSACTION",
            entity_id=payload.get("transaction_id"),
            target_role="DATA_SCIENTIST",
            payload=payload,
        )


def on_model_event(event: EventEnvelope) -> None:
    payload = event.payload
    with session_scope() as db:
        case_service.notify(
            db,
            severity=payload.get("severity", "INFO"),
            category="MODEL",
            title=payload.get("title", "Model event"),
            body=payload.get("body", ""),
            entity_type="MODEL",
            entity_id=payload.get("model_version_id"),
            link="/ml/models",
            target_role="DATA_SCIENTIST",
            payload=payload,
        )


def on_system_event(event: EventEnvelope) -> None:
    payload = event.payload
    with session_scope() as db:
        case_service.notify(
            db,
            severity=payload.get("severity", "INFO"),
            category="SYSTEM",
            title=payload.get("title", "System event"),
            body=payload.get("body", ""),
            entity_type=payload.get("entity_type"),
            entity_id=payload.get("entity_id"),
            payload=payload,
        )


def on_transaction_validated(event: EventEnvelope) -> None:
    metrics.increment("stream.transactions_validated")
    metrics.increment(f"stream.decision.{str(event.payload.get('decision', 'unknown')).lower()}")


def on_fraud_prediction(event: EventEnvelope) -> None:
    metrics.increment("stream.predictions")
    probability = float(event.payload.get("fraud_probability", 0.0) or 0.0)
    metrics.observe("model.prediction_probability", probability, unit="probability")


def register_all() -> None:
    """Wire every handler onto the bus. Safe to call once at startup."""
    event_bus.subscribe(Topic.TRANSACTIONS_VALIDATED, on_transaction_validated)
    event_bus.subscribe(Topic.FRAUD_PREDICTIONS, on_fraud_prediction)
    event_bus.subscribe(Topic.RISK_EVENTS, on_risk_decided)
    event_bus.subscribe(Topic.ALERTS_CREATED, on_alert_created)
    event_bus.subscribe(Topic.CASES_CREATED, on_case_created)
    event_bus.subscribe(Topic.ANALYST_FEEDBACK, on_analyst_feedback)
    event_bus.subscribe(Topic.MODEL_EVENTS, on_model_event)
    event_bus.subscribe(Topic.SYSTEM_EVENTS, on_system_event)
    logger.info("consumers_registered", extra={"driver": event_bus.driver})
