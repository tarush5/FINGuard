"""Transaction processing pipeline -- the decision path.

    validate -> deduplicate -> enrich -> features -> rules -> model -> graph
             -> ensemble -> decide -> persist -> publish

Scoring is synchronous because the caller needs an answer; everything that
happens *after* a decision (alerting, case creation, notifications, monitoring
samples) is fanned out over the event bus.  Processing is idempotent: replaying
the same ``event_id`` returns the original decision instead of double-charging
counters or creating a second case.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import cache
from app.core.errors import ValidationError
from app.core.logging import get_logger, get_request_id
from app.db.base import new_id, utcnow
from app.db.models.core import (
    Account,
    Customer,
    Device,
    DeviceLink,
    IngestedEvent,
    Merchant,
    Transaction,
)
from app.db.models.risk import Decision, FraudPrediction, RiskScore
from app.events.bus import event_bus
from app.events.schemas import Topic, make_event
from app.services import graph as graph_service
from app.services import rules as rule_service
from app.services.decision import DecisionPolicy, decide
from app.services.features import (
    TransactionContext,
    compute_features,
    persist_features,
    update_customer_profile,
)
from app.services.ml import model_service
from app.services.monitoring import Timer, metrics
from app.services.risk import RiskWeights, combine
from app.utils import safe_float, to_utc

logger = get_logger(__name__)

DEDUP_TTL_SECONDS = 24 * 3600


@dataclass
class TransactionInput:
    """Normalised input accepted by the pipeline (API and generators share it)."""

    event_id: str
    transaction_id: str
    customer_id: str
    merchant_id: str
    amount: float
    currency: str = "INR"
    occurred_at: datetime | None = None
    account_id: str | None = None
    device_id: str | None = None
    ip_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str = "IN"
    city: str = ""
    channel: str = "WEB"
    payment_method: str = "CARD"
    merchant_category: str = ""
    transaction_type: str = "PURCHASE"
    session_id: str | None = None
    correlation_id: str | None = None
    is_demo: bool = False
    # Ground truth, only ever supplied by the synthetic generator.
    is_fraud: bool | None = None
    fraud_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessResult:
    transaction_id: str
    decision: str
    risk_score: float
    risk_band: str
    duplicate: bool = False
    trace: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, float] = field(default_factory=dict)
    # Events held back when the caller owns the transaction boundary; they must
    # be published only after that caller commits, or consumers would race the
    # write and fail to find the row.
    pending_events: list[Any] = field(default_factory=list)


def _duplicate_result(db: Session, transaction_id: str) -> ProcessResult | None:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        return None
    return ProcessResult(
        transaction_id=txn.id,
        decision=txn.decision,
        risk_score=safe_float(txn.risk_score),
        risk_band=txn.risk_band,
        duplicate=True,
        trace={"note": "Event already processed; original decision returned."},
    )


def _ensure_device(db: Session, device_id: str | None, now: datetime) -> Device | None:
    if not device_id:
        return None
    device = db.get(Device, device_id)
    if device is None:
        device = Device(
            id=device_id,
            first_seen_at=now,
            last_seen_at=now,
            transaction_count=0,
            distinct_customers=0,
            risk_score=0.30,  # unknown devices start mildly risky, then decay
        )
        db.add(device)
        db.flush()
    return device


def _link_device(
    db: Session, device: Device | None, customer_id: str, amount: float, now: datetime
) -> None:
    if device is None:
        return
    link_id = f"{device.id}::{customer_id}"
    link = db.get(DeviceLink, link_id)
    if link is None:
        link = DeviceLink(
            id=link_id,
            device_id=device.id,
            customer_id=customer_id,
            first_seen_at=now,
            last_seen_at=now,
            transaction_count=0,
            total_amount=0.0,
        )
        db.add(link)
        device.distinct_customers = (device.distinct_customers or 0) + 1
    link.last_seen_at = now
    link.transaction_count += 1
    link.total_amount = round(safe_float(link.total_amount) + amount, 2)
    device.last_seen_at = now
    device.transaction_count = (device.transaction_count or 0) + 1
    # Device risk grows with account fan-out; a device seen by many customers is
    # the single strongest structural fraud signal in this domain.
    fanout_risk = min(0.15 * max(device.distinct_customers - 1, 0), 0.75)
    device.risk_score = round(min(max(safe_float(device.risk_score), fanout_risk), 1.0), 4)


def validate_input(payload: TransactionInput) -> None:
    if payload.amount is None or payload.amount <= 0:
        raise ValidationError("Transaction amount must be greater than zero.")
    if payload.amount > 1e12:
        raise ValidationError("Transaction amount exceeds the accepted maximum.")
    if len(payload.currency or "") != 3:
        raise ValidationError("Currency must be a three letter ISO-4217 code.")
    if payload.latitude is not None and not -90 <= payload.latitude <= 90:
        raise ValidationError("Latitude must be between -90 and 90.")
    if payload.longitude is not None and not -180 <= payload.longitude <= 180:
        raise ValidationError("Longitude must be between -180 and 180.")
    occurred = to_utc(payload.occurred_at) if payload.occurred_at else None
    if (
        occurred
        and occurred > utcnow().replace(microsecond=0)
        and (occurred - utcnow()).total_seconds() > 300
    ):
        raise ValidationError("Transaction timestamp is more than 5 minutes in the future.")


def process_transaction(
    db: Session,
    payload: TransactionInput,
    *,
    policy: DecisionPolicy | None = None,
    weights: RiskWeights | None = None,
    publish: bool = True,
    commit: bool = True,
) -> ProcessResult:
    """Run one transaction through the full decision path."""
    total_timer = time.perf_counter()
    latency: dict[str, float] = {}
    correlation_id = payload.correlation_id or get_request_id()

    # 1 -- validation ------------------------------------------------------
    with Timer("decision.validate") as t_validate:
        validate_input(payload)
    latency["validation_ms"] = round(t_validate.elapsed_ms, 3)

    # 2 -- deduplication ---------------------------------------------------
    started = time.perf_counter()
    existing_event = db.get(IngestedEvent, payload.event_id)
    if existing_event is not None:
        metrics.increment("pipeline.duplicates")
        duplicate = _duplicate_result(db, existing_event.entity_id or payload.transaction_id)
        if duplicate:
            return duplicate
    if not cache.claim(f"idem:txn:{payload.event_id}", DEDUP_TTL_SECONDS):
        duplicate = _duplicate_result(db, payload.transaction_id)
        if duplicate:
            metrics.increment("pipeline.duplicates")
            return duplicate
    latency["dedup_ms"] = round((time.perf_counter() - started) * 1000, 3)

    # 3 -- enrichment ------------------------------------------------------
    with Timer("decision.enrich") as t_enrich:
        now = to_utc(payload.occurred_at) or utcnow()
        customer = db.get(Customer, payload.customer_id)
        if customer is None:
            raise ValidationError(
                f"Unknown customer {payload.customer_id}.", code="CUSTOMER_NOT_FOUND"
            )
        merchant = db.get(Merchant, payload.merchant_id)
        if merchant is None:
            raise ValidationError(
                f"Unknown merchant {payload.merchant_id}.", code="MERCHANT_NOT_FOUND"
            )
        account = db.get(Account, payload.account_id) if payload.account_id else None
        if account is None:
            account = db.execute(
                select(Account).where(Account.customer_id == customer.id).limit(1)
            ).scalar_one_or_none()
        device = _ensure_device(db, payload.device_id, now)
        category = payload.merchant_category or merchant.category
    latency["enrichment_ms"] = round(t_enrich.elapsed_ms, 3)

    ctx = TransactionContext(
        transaction_id=payload.transaction_id,
        customer_id=payload.customer_id,
        merchant_id=payload.merchant_id,
        amount=float(payload.amount),
        occurred_at=now,
        device_id=payload.device_id,
        ip_address=payload.ip_address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        country=payload.country,
        city=payload.city,
        channel=payload.channel,
        payment_method=payload.payment_method,
        merchant_category=category,
        account_id=account.id if account else None,
        session_id=payload.session_id,
    )

    # 4 -- features --------------------------------------------------------
    with Timer("decision.features") as t_features:
        fv = compute_features(db, ctx, customer=customer, merchant=merchant)
    latency["feature_ms"] = round(t_features.elapsed_ms, 3)

    # 5 -- rules -----------------------------------------------------------
    with Timer("decision.rules") as t_rules:
        namespace = rule_service.build_namespace(
            fv,
            {
                "channel": ctx.channel,
                "country": ctx.country,
                "city": ctx.city,
                "payment_method": ctx.payment_method,
                "merchant_id": ctx.merchant_id,
                "customer_id": ctx.customer_id,
                "device_id": ctx.device_id,
                "transaction_type": payload.transaction_type,
                "currency": payload.currency,
                "customer_risk_score": safe_float(customer.risk_score),
                "customer_watchlisted": bool(customer.watchlisted),
                "merchant_high_risk": bool(merchant.high_risk_flag),
            },
        )
        rules = rule_service.active_rules(db)
        rule_evaluation = rule_service.evaluate(namespace, rules)
    latency["rule_ms"] = round(t_rules.elapsed_ms, 3)

    # 6 -- models ----------------------------------------------------------
    with Timer("decision.model") as t_model:
        prediction = model_service.predict_fraud(db, fv)
        anomaly = model_service.anomaly_score(db, fv)
        customer_risk = model_service.customer_risk(db, customer)
    latency["model_ms"] = round(t_model.elapsed_ms, 3)

    # 7 -- graph -----------------------------------------------------------
    with Timer("decision.graph") as t_graph:
        graph_result = graph_service.graph_risk(
            db,
            customer_id=customer.id,
            device_id=payload.device_id,
            ip_address=payload.ip_address,
            merchant_id=merchant.id,
            now=now,
        )
    latency["graph_ms"] = round(t_graph.elapsed_ms, 3)

    # 8 -- ensemble --------------------------------------------------------
    triggered = [hit.to_dict() for hit in rule_evaluation.triggered]
    assessment = combine(
        rule_score=rule_evaluation.score,
        fraud_probability=prediction.probability,
        anomaly_score=anomaly.score,
        customer_risk=customer_risk,
        merchant_risk=safe_float(merchant.risk_score) / 100.0,
        graph_risk=graph_result.score,
        weights=weights,
        model_factors=prediction.explanation.get("top_factors", []),
        triggered_rules=triggered,
        graph_signals=graph_result.signals,
    )

    # 9 -- decision --------------------------------------------------------
    forced_by = next(
        (
            hit.code
            for hit in rule_evaluation.triggered
            if hit.action in {"DECLINE", "REVIEW", "STEP_UP"}
        ),
        None,
    )
    decision_result = decide(
        assessment.final_score,
        policy=policy,
        forced_action=rule_evaluation.forced_action,
        forced_by=forced_by,
        triggered_rules=triggered,
        top_factors=assessment.top_factors,
    )

    # 10 -- persistence ----------------------------------------------------
    with Timer("decision.persist") as t_persist:
        processing_ms = round((time.perf_counter() - total_timer) * 1000, 3)
        txn = Transaction(
            id=payload.transaction_id,
            event_id=payload.event_id,
            correlation_id=correlation_id,
            customer_id=customer.id,
            account_id=account.id if account else None,
            merchant_id=merchant.id,
            device_id=device.id if device else None,
            amount=float(payload.amount),
            currency=payload.currency,
            occurred_at=now,
            ingested_at=utcnow(),
            payment_method=payload.payment_method,
            merchant_category=category,
            channel=payload.channel,
            transaction_type=payload.transaction_type,
            status="SETTLED" if decision_result.outcome == "APPROVE" else "HELD",
            ip_address=payload.ip_address,
            latitude=payload.latitude,
            longitude=payload.longitude,
            country=payload.country,
            city=payload.city,
            session_id=payload.session_id,
            risk_score=assessment.final_score,
            risk_band=assessment.risk_band,
            decision=decision_result.outcome,
            fraud_probability=prediction.probability,
            anomaly_score=anomaly.score,
            graph_risk=graph_result.score,
            rule_score=rule_evaluation.score,
            processing_ms=processing_ms,
            model_version=prediction.model_version,
            is_fraud=payload.is_fraud,
            fraud_type=payload.fraud_type,
            label_source="synthetic" if payload.is_fraud is not None else None,
            labelled_at=utcnow() if payload.is_fraud is not None else None,
            is_demo=payload.is_demo,
            metadata_json=payload.metadata,
        )
        db.add(txn)
        db.flush()

        persist_features(db, txn.id, customer.id, fv)
        rule_service.persist_executions(db, txn.id, rule_evaluation)
        rule_service.bump_rule_counters(db, rule_evaluation)

        db.add(
            FraudPrediction(
                id=new_id("FP"),
                transaction_id=txn.id,
                model_name=prediction.model_name,
                model_version=prediction.model_version,
                predicted_at=utcnow(),
                probability=prediction.probability,
                predicted_label=prediction.label,
                threshold=prediction.threshold,
                inference_ms=prediction.inference_ms,
                explanation=prediction.explanation,
                feature_snapshot=fv.values,
            )
        )
        risk_record = RiskScore(
            id=new_id("RS"),
            transaction_id=txn.id,
            customer_id=customer.id,
            scored_at=utcnow(),
            rule_score=rule_evaluation.score,
            fraud_probability=prediction.probability,
            anomaly_score=anomaly.score,
            customer_risk=customer_risk,
            merchant_risk=safe_float(merchant.risk_score) / 100.0,
            graph_risk=graph_result.score,
            final_score=assessment.final_score,
            risk_band=assessment.risk_band,
            weights=assessment.weights,
            components=assessment.components,
            triggered_rules=triggered,
            top_factors=assessment.top_factors,
            model_version=prediction.model_version,
            ruleset_version=rule_evaluation.ruleset_version,
            latency_breakdown=latency,
        )
        db.add(risk_record)
        db.add(
            Decision(
                id=new_id("DEC"),
                transaction_id=txn.id,
                decided_at=utcnow(),
                outcome=decision_result.outcome,
                risk_score=assessment.final_score,
                reason=decision_result.reason,
                reason_codes=decision_result.reason_codes,
                policy_version=decision_result.policy_version,
                thresholds=decision_result.thresholds,
                model_version=prediction.model_version,
                triggered_rules=[hit["code"] for hit in triggered],
                processing_ms=processing_ms,
            )
        )
        db.add(
            IngestedEvent(
                event_id=payload.event_id,
                topic=str(Topic.TRANSACTIONS_RAW),
                entity_id=txn.id,
                processed_at=utcnow(),
                result="PROCESSED",
            )
        )

        _link_device(db, device, customer.id, float(payload.amount), now)
        update_customer_profile(customer, float(payload.amount), now, category)
        customer.distinct_device_count = int(
            db.execute(
                select(func.count())
                .select_from(DeviceLink)
                .where(DeviceLink.customer_id == customer.id)
            ).scalar_one()
            or 0
        )
        merchant.transaction_count = (merchant.transaction_count or 0) + 1
        merchant.transaction_volume = round(
            safe_float(merchant.transaction_volume) + float(payload.amount), 2
        )
        merchant.avg_ticket = round(
            safe_float(merchant.transaction_volume) / max(merchant.transaction_count, 1), 2
        )
        if payload.is_fraud:
            merchant.fraud_count = (merchant.fraud_count or 0) + 1
            customer.confirmed_fraud_count = (customer.confirmed_fraud_count or 0) + 1
            if device:
                device.fraud_count = (device.fraud_count or 0) + 1
        merchant.fraud_rate = round(
            (merchant.fraud_count or 0) / max(merchant.transaction_count, 1), 5
        )

        if commit:
            db.commit()
    latency["persist_ms"] = round(t_persist.elapsed_ms, 3)

    total_ms = round((time.perf_counter() - total_timer) * 1000, 3)
    latency["total_ms"] = total_ms
    metrics.observe("decision.total", total_ms)
    metrics.increment("pipeline.processed")
    metrics.increment(f"decision.{decision_result.outcome.lower()}")

    trace = build_trace(
        txn=txn,
        fv=fv,
        rule_evaluation=rule_evaluation,
        prediction=prediction,
        anomaly=anomaly,
        customer_risk=customer_risk,
        graph_result=graph_result,
        assessment=assessment,
        decision_result=decision_result,
        latency=latency,
    )

    # 11 -- publish downstream --------------------------------------------
    # Publishing happens strictly after the commit: consumers run concurrently
    # and must never observe an event for a row that is not yet visible.
    events = _build_events(txn, trace, decision_result, correlation_id) if publish else []
    pending: list[Any] = []
    if events:
        if commit:
            for event in events:
                event_bus.publish(event)
        else:
            pending = events

    logger.info(
        "transaction_decided",
        extra={
            "transaction_id": txn.id,
            "decision": decision_result.outcome,
            "risk_score": assessment.final_score,
            "model_version": prediction.model_version,
            "total_ms": total_ms,
        },
    )

    return ProcessResult(
        transaction_id=txn.id,
        decision=decision_result.outcome,
        risk_score=assessment.final_score,
        risk_band=assessment.risk_band,
        trace=trace,
        latency=latency,
        pending_events=pending,
    )


def build_trace(
    *,
    txn: Transaction,
    fv: Any,
    rule_evaluation: Any,
    prediction: Any,
    anomaly: Any,
    customer_risk: float,
    graph_result: Any,
    assessment: Any,
    decision_result: Any,
    latency: dict[str, float],
) -> dict[str, Any]:
    """The audit-friendly decision trace rendered by the UI."""
    return {
        "transaction": {
            "id": txn.id,
            "amount": float(txn.amount),
            "currency": txn.currency,
            "customer_id": txn.customer_id,
            "merchant_id": txn.merchant_id,
            "device_id": txn.device_id,
            "channel": txn.channel,
            "occurred_at": txn.occurred_at.isoformat() if txn.occurred_at else None,
        },
        "stages": [
            {
                "stage": "FEATURES",
                "duration_ms": latency.get("feature_ms", 0.0),
                "summary": f"{len(fv.values)} features computed",
                "detail": {
                    "notable": {
                        key: fv.values.get(key)
                        for key in (
                            "amount_ratio_to_avg",
                            "txn_count_5m",
                            "is_new_device",
                            "impossible_travel",
                            "device_customer_count",
                            "merchant_fraud_rate",
                        )
                    },
                    "feature_version": fv.version,
                },
            },
            {
                "stage": "RULES",
                "duration_ms": latency.get("rule_ms", 0.0),
                "summary": f"{len(rule_evaluation.triggered)} of {rule_evaluation.evaluated} rules triggered",
                "detail": {
                    "score": rule_evaluation.score,
                    "triggered": [hit.to_dict() for hit in rule_evaluation.triggered],
                    "ruleset_version": rule_evaluation.ruleset_version,
                },
            },
            {
                "stage": "MODEL",
                "duration_ms": latency.get("model_ms", 0.0),
                "summary": f"Fraud probability {prediction.probability:.4f}",
                "detail": {
                    "model_version": prediction.model_version,
                    "is_trained_model": prediction.is_trained_model,
                    "threshold": prediction.threshold,
                    "anomaly_score": anomaly.score,
                    "anomaly_model": anomaly.model_version,
                    "customer_risk": customer_risk,
                    "explanation": prediction.explanation,
                },
            },
            {
                "stage": "GRAPH",
                "duration_ms": latency.get("graph_ms", 0.0),
                "summary": f"Graph risk {graph_result.score:.2f} from {len(graph_result.signals)} signal(s)",
                "detail": graph_result.to_dict(),
            },
            {
                "stage": "RISK",
                "duration_ms": 0.0,
                "summary": f"Final score {assessment.final_score}/100 ({assessment.risk_band})",
                "detail": assessment.to_dict(),
            },
            {
                "stage": "DECISION",
                "duration_ms": latency.get("persist_ms", 0.0),
                "summary": decision_result.outcome,
                "detail": decision_result.to_dict(),
            },
        ],
        "risk": assessment.to_dict(),
        "decision": decision_result.to_dict(),
        "latency": latency,
        "model_version": prediction.model_version,
        "explanation": prediction.explanation,
        "graph": graph_result.to_dict(),
    }


def publish_pending(results: list[ProcessResult]) -> int:
    """Publish events held back by ``commit=False`` callers, after their commit."""
    published = 0
    for result in results:
        for event in result.pending_events:
            event_bus.publish(event)
            published += 1
        result.pending_events.clear()
    return published


def _build_events(
    txn: Transaction, trace: dict[str, Any], decision_result: Any, correlation_id: str | None
) -> list[Any]:
    base = {
        "transaction_id": txn.id,
        "customer_id": txn.customer_id,
        "merchant_id": txn.merchant_id,
        "amount": float(txn.amount),
        "currency": txn.currency,
        "risk_score": float(txn.risk_score),
        "risk_band": txn.risk_band,
        "decision": decision_result.outcome,
        "occurred_at": txn.occurred_at.isoformat() if txn.occurred_at else None,
    }
    return [
        make_event(
            Topic.TRANSACTIONS_VALIDATED,
            "transaction.validated",
            base,
            correlation_id=correlation_id,
            partition_key=txn.customer_id,
        ),
        make_event(
            Topic.FRAUD_PREDICTIONS,
            "fraud.predicted",
            {
                **base,
                "fraud_probability": float(txn.fraud_probability),
                "model_version": txn.model_version,
            },
            correlation_id=correlation_id,
            partition_key=txn.customer_id,
        ),
        make_event(
            Topic.RISK_EVENTS,
            "risk.decided",
            {
                **base,
                "requires_alert": decision_result.requires_alert,
                "requires_case": decision_result.requires_case,
                "reason": decision_result.reason,
                "reason_codes": decision_result.reason_codes,
                "triggered_rules": trace["stages"][1]["detail"]["triggered"],
                "top_factors": trace["risk"]["top_factors"],
                "graph_signals": trace["graph"]["signals"],
            },
            correlation_id=correlation_id,
            partition_key=txn.customer_id,
        ),
    ]
