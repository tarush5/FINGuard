"""Historical backfill.

Replays generated history through the *same* feature, rule, model, graph and
decision code the live API uses -- the only difference is that neighbourhood
state (customer history, device/IP fan-out) is held in memory and injected,
instead of being re-queried per row.  That keeps the seeded history consistent
with what production would have produced, and keeps the feature store free of
train/serve skew.

This is the batch counterpart of ``app.services.pipeline`` and is what a Spark
job would do in a larger deployment.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.datagen.generator import RawTransaction
from app.db.base import new_id, utcnow
from app.db.models.core import Customer, Device, DeviceLink, Merchant, Transaction
from app.db.models.risk import Decision, FraudPrediction, RiskScore, Rule
from app.services import graph as graph_service
from app.services import rules as rule_service
from app.services.decision import DecisionPolicy, decide
from app.services.features import (
    FEATURE_NAMES,
    TransactionContext,
    compute_features,
    update_customer_profile,
)
from app.services.ml import model_service
from app.services.risk import combine
from app.utils import safe_float

logger = get_logger(__name__)

HISTORY_CAP = 200
FLUSH_EVERY = 2000


@dataclass
class HistoryRow:
    """Minimal stand-in for a persisted transaction in the in-memory window."""

    occurred_at: datetime
    amount: float
    latitude: float | None
    longitude: float | None
    country: str
    city: str
    merchant_id: str
    merchant_category: str
    id: str


@dataclass
class BackfillStats:
    processed: int = 0
    approved: int = 0
    step_up: int = 0
    review: int = 0
    declined: int = 0
    fraud: int = 0
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "approved": self.approved,
            "step_up": self.step_up,
            "manual_review": self.review,
            "declined": self.declined,
            "fraud_labelled": self.fraud,
            "duration_seconds": round(self.duration_seconds, 2),
            "throughput_per_second": round(
                self.processed / self.duration_seconds if self.duration_seconds else 0.0, 1
            ),
        }


def backfill(
    db: Session,
    raw_transactions: Iterable[RawTransaction],
    *,
    policy: DecisionPolicy | None = None,
    progress_every: int = 5000,
) -> BackfillStats:
    """Score and persist a chronological stream of generated transactions."""
    started = time.perf_counter()
    stats = BackfillStats()
    policy = policy or DecisionPolicy()

    customers: dict[str, Customer] = {c.id: c for c in db.execute(select(Customer)).scalars()}
    merchants: dict[str, Merchant] = {m.id: m for m in db.execute(select(Merchant)).scalars()}
    devices: dict[str, Device] = {d.id: d for d in db.execute(select(Device)).scalars()}
    rules: list[Rule] = rule_service.active_rules(db)

    history: dict[str, list[HistoryRow]] = defaultdict(list)
    device_customers: dict[str, set[str]] = defaultdict(set)
    ip_customers: dict[str, set[str]] = defaultdict(set)
    device_links: dict[str, DeviceLink] = {}
    contaminated_customers: set[str] = set()

    txn_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    rule_exec_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []

    def flush() -> None:
        # Persist pending ORM objects (new devices, device links) first so the
        # bulk transaction insert never violates a foreign key.
        db.flush()
        if txn_rows:
            db.bulk_insert_mappings(Transaction, txn_rows)
            txn_rows.clear()
        if feature_rows:
            from app.db.models.core import TransactionFeature

            db.bulk_insert_mappings(TransactionFeature, feature_rows)
            feature_rows.clear()
        if prediction_rows:
            db.bulk_insert_mappings(FraudPrediction, prediction_rows)
            prediction_rows.clear()
        if risk_rows:
            db.bulk_insert_mappings(RiskScore, risk_rows)
            risk_rows.clear()
        if decision_rows:
            db.bulk_insert_mappings(Decision, decision_rows)
            decision_rows.clear()
        if rule_exec_rows:
            from app.db.models.risk import RuleExecution

            db.bulk_insert_mappings(RuleExecution, rule_exec_rows)
            rule_exec_rows.clear()
        if event_rows:
            from app.db.models.core import IngestedEvent

            db.bulk_insert_mappings(IngestedEvent, event_rows)
            event_rows.clear()
        db.flush()

    for raw in raw_transactions:
        customer = customers.get(raw.customer_id)
        merchant = merchants.get(raw.merchant_id)
        if customer is None or merchant is None:
            continue

        occurred = raw.occurred_at
        ctx = TransactionContext(
            transaction_id=raw.transaction_id,
            customer_id=raw.customer_id,
            merchant_id=raw.merchant_id,
            amount=raw.amount,
            occurred_at=occurred,
            device_id=raw.device_id,
            ip_address=raw.ip_address,
            latitude=raw.latitude,
            longitude=raw.longitude,
            country=raw.country,
            city=raw.city,
            channel=raw.channel,
            payment_method=raw.payment_method,
            merchant_category=raw.merchant_category,
            account_id=raw.account_id,
            session_id=raw.session_id,
        )

        device = devices.get(raw.device_id)
        if device is None:
            device = Device(
                id=raw.device_id,
                device_type="MOBILE",
                first_seen_at=occurred,
                last_seen_at=occurred,
                transaction_count=0,
                distinct_customers=0,
                risk_score=0.30,
            )
            db.add(device)
            devices[raw.device_id] = device

        # Point-in-time device/IP state: computed *before* this transaction is
        # folded into the maps.
        seen_customers = device_customers[raw.device_id]
        is_new_device = 0 if raw.customer_id in seen_customers else 1
        # Fan-out counts include the customer being scored: "how many accounts
        # does this device/IP touch, counting this one".
        device_state = (
            is_new_device,
            len(seen_customers | {raw.customer_id}),
            safe_float(device.risk_score),
        )
        ip_count = len(ip_customers[raw.ip_address] | {raw.customer_id})

        fv = compute_features(
            None,
            ctx,
            customer=customer,
            merchant=merchant,
            history=history[raw.customer_id],
            device_state=device_state,
            ip_customer_count=ip_count,
        )

        namespace = rule_service.build_namespace(
            fv,
            {
                "channel": raw.channel,
                "country": raw.country,
                "city": raw.city,
                "payment_method": raw.payment_method,
                "merchant_id": raw.merchant_id,
                "customer_id": raw.customer_id,
                "device_id": raw.device_id,
                "transaction_type": raw.transaction_type,
                "currency": raw.currency,
                "customer_risk_score": safe_float(customer.risk_score),
                "customer_watchlisted": bool(customer.watchlisted),
                "merchant_high_risk": bool(merchant.high_risk_flag),
            },
        )
        rule_evaluation = rule_service.evaluate(namespace, rules)

        prediction = model_service.predict_fraud(db, fv)
        anomaly = model_service.anomaly_score(db, fv)
        customer_risk = safe_float(customer.risk_score) / 100.0

        graph_result = graph_service.graph_risk(
            None,
            customer_id=raw.customer_id,
            device_id=raw.device_id,
            ip_address=raw.ip_address,
            merchant_id=raw.merchant_id,
            now=occurred,
            device_customers=sorted(seen_customers | {raw.customer_id}),
            ip_account_count=len(ip_customers[raw.ip_address] | {raw.customer_id}),
            contaminated=sorted(contaminated_customers & seen_customers),
            merchant=merchant,
        )

        triggered = [hit.to_dict() for hit in rule_evaluation.triggered]
        assessment = combine(
            rule_score=rule_evaluation.score,
            fraud_probability=prediction.probability,
            anomaly_score=anomaly.score,
            customer_risk=customer_risk,
            merchant_risk=safe_float(merchant.risk_score) / 100.0,
            graph_risk=graph_result.score,
            model_factors=prediction.explanation.get("top_factors", []),
            triggered_rules=triggered,
            graph_signals=graph_result.signals,
        )
        forced_by = next(
            (
                h.code
                for h in rule_evaluation.triggered
                if h.action in {"DECLINE", "REVIEW", "STEP_UP"}
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

        now_ts = utcnow()
        txn_rows.append(
            {
                "id": raw.transaction_id,
                "event_id": raw.event_id,
                "correlation_id": None,
                "customer_id": raw.customer_id,
                "account_id": raw.account_id,
                "merchant_id": raw.merchant_id,
                "device_id": raw.device_id,
                "amount": raw.amount,
                "currency": raw.currency,
                "occurred_at": occurred,
                "ingested_at": occurred,
                "payment_method": raw.payment_method,
                "merchant_category": raw.merchant_category,
                "channel": raw.channel,
                "transaction_type": raw.transaction_type,
                "status": "SETTLED" if decision_result.outcome == "APPROVE" else "HELD",
                "ip_address": raw.ip_address,
                "latitude": raw.latitude,
                "longitude": raw.longitude,
                "country": raw.country,
                "city": raw.city,
                "session_id": raw.session_id,
                "risk_score": assessment.final_score,
                "risk_band": assessment.risk_band,
                "decision": decision_result.outcome,
                "fraud_probability": prediction.probability,
                "anomaly_score": anomaly.score,
                "graph_risk": graph_result.score,
                "rule_score": rule_evaluation.score,
                "processing_ms": round(fv.computation_ms + rule_evaluation.evaluation_ms, 3),
                "model_version": prediction.model_version,
                "is_fraud": raw.is_fraud,
                "fraud_type": raw.fraud_type,
                "label_source": "synthetic",
                "labelled_at": occurred,
                "is_demo": True,
                "metadata_json": raw.metadata,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        feature_rows.append(
            {
                "transaction_id": raw.transaction_id,
                "customer_id": raw.customer_id,
                "computed_at": occurred,
                "feature_version": fv.version,
                "computation_ms": fv.computation_ms,
                "features": fv.values,
                **{name: fv.values.get(name, 0) for name in _FEATURE_COLUMNS},
            }
        )
        prediction_rows.append(
            {
                "id": new_id("FP"),
                "transaction_id": raw.transaction_id,
                "model_name": prediction.model_name,
                "model_version": prediction.model_version,
                "predicted_at": occurred,
                "probability": prediction.probability,
                "predicted_label": prediction.label,
                "threshold": prediction.threshold,
                "inference_ms": prediction.inference_ms,
                "explanation": prediction.explanation,
                "feature_snapshot": {},
                "outcome": None,
            }
        )
        risk_rows.append(
            {
                "id": new_id("RS"),
                "transaction_id": raw.transaction_id,
                "customer_id": raw.customer_id,
                "scored_at": occurred,
                "rule_score": rule_evaluation.score,
                "fraud_probability": prediction.probability,
                "anomaly_score": anomaly.score,
                "customer_risk": customer_risk,
                "merchant_risk": safe_float(merchant.risk_score) / 100.0,
                "graph_risk": graph_result.score,
                "final_score": assessment.final_score,
                "risk_band": assessment.risk_band,
                "weights": assessment.weights,
                "components": assessment.components,
                "triggered_rules": triggered,
                "top_factors": assessment.top_factors,
                "model_version": prediction.model_version,
                "ruleset_version": rule_evaluation.ruleset_version,
                "latency_breakdown": {
                    "feature_ms": fv.computation_ms,
                    "rule_ms": rule_evaluation.evaluation_ms,
                    "model_ms": prediction.inference_ms,
                    "graph_ms": round(graph_result.computation_ms, 3),
                },
            }
        )
        decision_rows.append(
            {
                "id": new_id("DEC"),
                "transaction_id": raw.transaction_id,
                "decided_at": occurred,
                "outcome": decision_result.outcome,
                "risk_score": assessment.final_score,
                "reason": decision_result.reason,
                "reason_codes": decision_result.reason_codes,
                "policy_version": decision_result.policy_version,
                "thresholds": decision_result.thresholds,
                "model_version": prediction.model_version,
                "triggered_rules": [h["code"] for h in triggered],
                "processing_ms": round(fv.computation_ms, 3),
            }
        )
        for hit in rule_evaluation.triggered:
            rule_exec_rows.append(
                {
                    "id": new_id("RX"),
                    "rule_id": hit.rule_id,
                    "rule_code": hit.code,
                    "rule_version": hit.version,
                    "transaction_id": raw.transaction_id,
                    "evaluated_at": occurred,
                    "triggered": True,
                    "risk_points": hit.risk_points,
                    "evaluation_ms": hit.evaluation_ms,
                    "matched_values": hit.matched_values,
                }
            )
        event_rows.append(
            {
                "event_id": raw.event_id,
                "topic": "transactions.raw",
                "entity_id": raw.transaction_id,
                "processed_at": occurred,
                "result": "PROCESSED",
                "payload_hash": "",
            }
        )

        # ---- advance in-memory state -------------------------------------
        window = history[raw.customer_id]
        window.insert(
            0,
            HistoryRow(
                occurred_at=occurred,
                amount=raw.amount,
                latitude=raw.latitude,
                longitude=raw.longitude,
                country=raw.country,
                city=raw.city,
                merchant_id=raw.merchant_id,
                merchant_category=raw.merchant_category,
                id=raw.transaction_id,
            ),
        )
        del window[HISTORY_CAP:]

        if raw.customer_id not in seen_customers:
            seen_customers.add(raw.customer_id)
            device.distinct_customers = len(seen_customers)
            link = DeviceLink(
                id=f"{raw.device_id}::{raw.customer_id}",
                device_id=raw.device_id,
                customer_id=raw.customer_id,
                first_seen_at=occurred,
                last_seen_at=occurred,
                transaction_count=0,
                total_amount=0.0,
                fraud_count=0,
            )
            db.add(link)
            device_links[link.id] = link
        link = device_links[f"{raw.device_id}::{raw.customer_id}"]
        link.last_seen_at = occurred
        link.transaction_count += 1
        link.total_amount = round(safe_float(link.total_amount) + raw.amount, 2)

        ip_customers[raw.ip_address].add(raw.customer_id)
        device.last_seen_at = occurred
        device.transaction_count = (device.transaction_count or 0) + 1
        device.risk_score = round(
            min(max(safe_float(device.risk_score), 0.15 * max(len(seen_customers) - 1, 0)), 1.0), 4
        )

        update_customer_profile(customer, raw.amount, occurred, raw.merchant_category)
        merchant.transaction_count = (merchant.transaction_count or 0) + 1
        merchant.transaction_volume = round(safe_float(merchant.transaction_volume) + raw.amount, 2)

        if raw.is_fraud:
            stats.fraud += 1
            merchant.fraud_count = (merchant.fraud_count or 0) + 1
            customer.confirmed_fraud_count = (customer.confirmed_fraud_count or 0) + 1
            device.fraud_count = (device.fraud_count or 0) + 1
            link.fraud_count += 1
            contaminated_customers.add(raw.customer_id)

        stats.processed += 1
        outcome = decision_result.outcome
        if outcome == "APPROVE":
            stats.approved += 1
        elif outcome == "STEP_UP":
            stats.step_up += 1
        elif outcome == "MANUAL_REVIEW":
            stats.review += 1
        else:
            stats.declined += 1

        if stats.processed % FLUSH_EVERY == 0:
            flush()
        if progress_every and stats.processed % progress_every == 0:
            logger.info(
                "backfill_progress",
                extra={
                    "processed": stats.processed,
                    "elapsed_s": round(time.perf_counter() - started, 1),
                },
            )

    flush()
    stats.duration_seconds = time.perf_counter() - started
    return stats


_FEATURE_COLUMNS = [
    name
    for name in FEATURE_NAMES
    if name
    not in {
        "is_high_risk_channel",
        "is_cross_border",
        "amount_to_max_ratio",
        "distinct_merchants_24h",
    }
]


def rescore_recent(
    db: Session,
    *,
    days: int = 7,
    limit: int = 6000,
    policy: DecisionPolicy | None = None,
) -> dict[str, Any]:
    """Replay recent transactions through the newly promoted model.

    Uses the *stored* feature vectors, so this is a pure model swap: identical
    inputs, new estimator.  It is how a newly promoted model becomes visible on
    recent traffic and how the drift baseline gets populated.
    """
    from app.db.models.core import TransactionFeature

    policy = policy or DecisionPolicy()
    cutoff = utcnow() - timedelta(days=days)
    rows = list(
        db.execute(
            select(Transaction, TransactionFeature)
            .join(TransactionFeature, TransactionFeature.transaction_id == Transaction.id)
            .where(Transaction.occurred_at >= cutoff)
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
        )
    )
    if not rows:
        return {"rescored": 0, "model_version": None}

    from app.services.features import FeatureVector

    changed = 0
    model_version = None
    prediction_rows: list[dict[str, Any]] = []

    for transaction, feature_record in rows:
        fv = FeatureVector(values=feature_record.features or {}, context={})
        prediction = model_service.predict_fraud(db, fv)
        model_version = prediction.model_version
        merchant = db.get(Merchant, transaction.merchant_id)
        customer = db.get(Customer, transaction.customer_id)
        assessment = combine(
            rule_score=safe_float(transaction.rule_score),
            fraud_probability=prediction.probability,
            anomaly_score=safe_float(transaction.anomaly_score),
            customer_risk=safe_float(customer.risk_score) / 100.0 if customer else 0.0,
            merchant_risk=safe_float(merchant.risk_score) / 100.0 if merchant else 0.0,
            graph_risk=safe_float(transaction.graph_risk),
            model_factors=prediction.explanation.get("top_factors", []),
        )
        decision_result = decide(assessment.final_score, policy=policy)

        transaction.fraud_probability = prediction.probability
        transaction.risk_score = assessment.final_score
        transaction.risk_band = assessment.risk_band
        transaction.decision = decision_result.outcome
        transaction.model_version = prediction.model_version
        prediction_rows.append(
            {
                "id": new_id("FP"),
                "transaction_id": transaction.id,
                "model_name": prediction.model_name,
                "model_version": prediction.model_version,
                "predicted_at": utcnow(),
                "probability": prediction.probability,
                "predicted_label": prediction.label,
                "threshold": prediction.threshold,
                "inference_ms": prediction.inference_ms,
                "explanation": prediction.explanation,
                "feature_snapshot": {},
                "outcome": None,
            }
        )
        changed += 1

    db.bulk_insert_mappings(FraudPrediction, prediction_rows)
    db.flush()
    logger.info("rescore_completed", extra={"rescored": changed, "model_version": model_version})
    return {"rescored": changed, "model_version": model_version, "window_days": days}
