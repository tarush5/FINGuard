"""Transaction ingestion, querying, decision trace and the live feed."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, PaginationDep, SortingDep, require
from app.core.errors import NotFoundError
from app.core.rbac import Permission
from app.db.models.core import Transaction, TransactionFeature
from app.db.models.risk import Decision, FraudPrediction, RiskScore, RuleExecution
from app.db.session import session_scope
from app.services import audit
from app.services.pipeline import TransactionInput, process_transaction
from app.services.serializers import serialize_transaction
from app.utils import safe_float, utcnow

router = APIRouter(prefix="/transactions", tags=["transactions"])


class TransactionIngest(BaseModel):
    """Incoming transaction event. ``event_id`` is the idempotency key."""

    event_id: str | None = Field(default=None, max_length=64)
    transaction_id: str | None = Field(default=None, max_length=48)
    customer_id: str = Field(max_length=40)
    merchant_id: str = Field(max_length=40)
    amount: float = Field(gt=0, le=1e12)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    occurred_at: datetime | None = None
    account_id: str | None = Field(default=None, max_length=40)
    device_id: str | None = Field(default=None, max_length=64)
    ip_address: str | None = Field(default=None, max_length=64)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    country: str = Field(default="IN", min_length=2, max_length=2)
    city: str = Field(default="", max_length=80)
    channel: str = Field(default="WEB", max_length=24)
    payment_method: str = Field(default="CARD", max_length=32)
    merchant_category: str = Field(default="", max_length=48)
    transaction_type: str = Field(default="PURCHASE", max_length=24)
    session_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency", "country")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    def to_pipeline_input(self, correlation_id: str | None) -> TransactionInput:
        return TransactionInput(
            event_id=self.event_id or f"evt_{uuid.uuid4().hex}",
            transaction_id=self.transaction_id or f"TXN-{uuid.uuid4().hex[:16].upper()}",
            customer_id=self.customer_id,
            merchant_id=self.merchant_id,
            amount=self.amount,
            currency=self.currency,
            occurred_at=self.occurred_at or utcnow(),
            account_id=self.account_id,
            device_id=self.device_id,
            ip_address=self.ip_address,
            latitude=self.latitude,
            longitude=self.longitude,
            country=self.country,
            city=self.city,
            channel=self.channel,
            payment_method=self.payment_method,
            merchant_category=self.merchant_category,
            transaction_type=self.transaction_type,
            session_id=self.session_id,
            correlation_id=correlation_id,
            metadata=self.metadata,
        )


@router.post("", summary="Score a transaction and return the decision")
def ingest(
    payload: TransactionIngest,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.TRANSACTION_INGEST))],
) -> dict[str, Any]:
    """Run one transaction through the full decision path.

    Idempotent: replaying an ``event_id`` returns the original decision.
    """
    correlation_id = request.headers.get("x-correlation-id") or request.headers.get("x-request-id")
    result = process_transaction(db, payload.to_pipeline_input(correlation_id))
    audit.record(
        db,
        action="transaction.ingested",
        entity_type="TRANSACTION",
        entity_id=result.transaction_id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        model_version=result.trace.get("model_version"),
        details={"decision": result.decision, "risk_score": result.risk_score},
    )
    db.commit()
    return {
        "transaction_id": result.transaction_id,
        "decision": result.decision,
        "risk_score": result.risk_score,
        "risk_band": result.risk_band,
        "duplicate": result.duplicate,
        "latency": result.latency,
        "trace": result.trace,
    }


@router.get("", summary="Search transactions")
def list_transactions(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.TRANSACTION_READ))],
    page: PaginationDep,
    sort: SortingDep,
    search: Annotated[str | None, Query(max_length=64)] = None,
    decision: Annotated[str | None, Query()] = None,
    risk_band: Annotated[str | None, Query()] = None,
    channel: Annotated[str | None, Query()] = None,
    payment_method: Annotated[str | None, Query()] = None,
    country: Annotated[str | None, Query(max_length=2)] = None,
    merchant_id: Annotated[str | None, Query()] = None,
    customer_id: Annotated[str | None, Query()] = None,
    device_id: Annotated[str | None, Query()] = None,
    min_amount: Annotated[float | None, Query(ge=0)] = None,
    max_amount: Annotated[float | None, Query(ge=0)] = None,
    min_risk: Annotated[float | None, Query(ge=0, le=100)] = None,
    is_fraud: Annotated[bool | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> dict[str, Any]:
    stmt = select(Transaction).where(Transaction.occurred_at >= utcnow() - timedelta(days=days))
    count_stmt = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.occurred_at >= utcnow() - timedelta(days=days))
    )

    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                Transaction.id.ilike(like),
                Transaction.customer_id.ilike(like),
                Transaction.merchant_id.ilike(like),
                Transaction.device_id.ilike(like),
                Transaction.session_id.ilike(like),
            )
        )
    if decision:
        conditions.append(Transaction.decision == decision.upper())
    if risk_band:
        conditions.append(Transaction.risk_band == risk_band.upper())
    if channel:
        conditions.append(Transaction.channel == channel.upper())
    if payment_method:
        conditions.append(Transaction.payment_method == payment_method.upper())
    if country:
        conditions.append(Transaction.country == country.upper())
    if merchant_id:
        conditions.append(Transaction.merchant_id == merchant_id)
    if customer_id:
        conditions.append(Transaction.customer_id == customer_id)
    if device_id:
        conditions.append(Transaction.device_id == device_id)
    if min_amount is not None:
        conditions.append(Transaction.amount >= min_amount)
    if max_amount is not None:
        conditions.append(Transaction.amount <= max_amount)
    if min_risk is not None:
        conditions.append(Transaction.risk_score >= min_risk)
    if is_fraud is not None:
        conditions.append(Transaction.is_fraud.is_(is_fraud))

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    sortable = {
        "occurred_at": Transaction.occurred_at,
        "amount": Transaction.amount,
        "risk_score": Transaction.risk_score,
        "fraud_probability": Transaction.fraud_probability,
        "processing_ms": Transaction.processing_ms,
    }
    stmt = sort.apply(stmt, sortable, Transaction.occurred_at)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(stmt.offset(page.offset).limit(page.limit)).scalars()
    return page.envelope(
        [serialize_transaction(txn, mask_pii=user.mask_pii) for txn in rows], total
    )


@router.get("/live", summary="Server-sent stream of the most recent transactions")
async def live_feed(
    user: Annotated[Any, Depends(require(Permission.TRANSACTION_READ))],
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    interval: Annotated[float, Query(ge=0.5, le=10)] = 2.0,
) -> StreamingResponse:
    """Push new transactions as they are scored (SSE).

    Each tick emits only rows newer than the previous tick's watermark, so the
    client can append instead of re-rendering the whole feed.
    """
    mask = user.mask_pii

    async def event_stream() -> Any:
        watermark = utcnow() - timedelta(minutes=5)
        # Prime the stream with the latest rows so the panel is never empty.
        first = True
        try:
            while True:
                with session_scope() as db:
                    stmt = select(Transaction).order_by(Transaction.created_at.desc())
                    if not first:
                        stmt = stmt.where(Transaction.created_at > watermark)
                    rows = list(db.execute(stmt.limit(limit)).scalars())
                    if rows:
                        watermark = max(
                            (row.created_at for row in rows if row.created_at), default=watermark
                        )
                    payload = [serialize_transaction(row, mask_pii=mask) for row in reversed(rows)]
                first = False
                if payload:
                    yield f"data: {json.dumps({'transactions': payload}, default=str)}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(interval)
        except asyncio.CancelledError:  # client disconnected
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{transaction_id}", summary="Transaction detail with features")
def get_transaction(
    transaction_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.TRANSACTION_READ))],
) -> dict[str, Any]:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(
            f"Transaction {transaction_id} was not found.", code="TRANSACTION_NOT_FOUND"
        )
    features = db.get(TransactionFeature, transaction_id)
    return {
        "transaction": serialize_transaction(txn, mask_pii=user.mask_pii, features=features),
        "feature_vector": features.features if features else {},
    }


@router.get("/{transaction_id}/trace", summary="Full decision trace for one transaction")
def decision_trace(
    transaction_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RISK_READ))],
) -> dict[str, Any]:
    """Rebuild the audit-friendly trace: rules -> features -> model -> graph -> decision."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise NotFoundError(
            f"Transaction {transaction_id} was not found.", code="TRANSACTION_NOT_FOUND"
        )

    features = db.get(TransactionFeature, transaction_id)
    risk = db.execute(
        select(RiskScore)
        .where(RiskScore.transaction_id == transaction_id)
        .order_by(RiskScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    prediction = db.execute(
        select(FraudPrediction)
        .where(FraudPrediction.transaction_id == transaction_id)
        .order_by(FraudPrediction.predicted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    decision = db.execute(
        select(Decision)
        .where(Decision.transaction_id == transaction_id)
        .order_by(Decision.decided_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    rule_hits = list(
        db.execute(
            select(RuleExecution).where(
                RuleExecution.transaction_id == transaction_id,
                RuleExecution.triggered.is_(True),
            )
        ).scalars()
    )

    notable_keys = (
        "amount_ratio_to_avg",
        "txn_count_5m",
        "is_new_device",
        "impossible_travel",
        "device_customer_count",
        "ip_customer_count",
        "merchant_fraud_rate",
    )
    feature_values = features.features if features else {}
    latency = (risk.latency_breakdown if risk else {}) or {}

    return {
        "transaction": serialize_transaction(txn, mask_pii=user.mask_pii),
        "stages": [
            {
                "stage": "FEATURES",
                "duration_ms": latency.get(
                    "feature_ms", safe_float(features.computation_ms) if features else 0.0
                ),
                "summary": f"{len(feature_values)} features computed",
                "detail": {
                    "notable": {key: feature_values.get(key) for key in notable_keys},
                    "feature_version": features.feature_version if features else None,
                },
            },
            {
                "stage": "RULES",
                "duration_ms": latency.get("rule_ms", 0.0),
                "summary": f"{len(rule_hits)} rule(s) triggered",
                "detail": {
                    "score": safe_float(txn.rule_score),
                    "triggered": [
                        {
                            "code": hit.rule_code,
                            "version": hit.rule_version,
                            "risk_points": safe_float(hit.risk_points),
                            "matched_values": hit.matched_values,
                            "evaluation_ms": safe_float(hit.evaluation_ms),
                        }
                        for hit in rule_hits
                    ],
                    "ruleset_version": risk.ruleset_version if risk else None,
                },
            },
            {
                "stage": "MODEL",
                "duration_ms": latency.get(
                    "model_ms", safe_float(prediction.inference_ms) if prediction else 0.0
                ),
                "summary": f"Fraud probability {safe_float(txn.fraud_probability):.4f}",
                "detail": {
                    "model_version": txn.model_version,
                    "threshold": safe_float(prediction.threshold) if prediction else None,
                    "anomaly_score": safe_float(txn.anomaly_score),
                    "explanation": prediction.explanation if prediction else {},
                },
            },
            {
                "stage": "GRAPH",
                "duration_ms": latency.get("graph_ms", 0.0),
                "summary": f"Graph risk {safe_float(txn.graph_risk):.2f}",
                "detail": {"graph_risk": safe_float(txn.graph_risk)},
            },
            {
                "stage": "RISK",
                "duration_ms": 0.0,
                "summary": f"Final score {safe_float(txn.risk_score):.1f}/100 ({txn.risk_band})",
                "detail": {
                    "components": risk.components if risk else {},
                    "weights": risk.weights if risk else {},
                    "top_factors": risk.top_factors if risk else [],
                },
            },
            {
                "stage": "DECISION",
                "duration_ms": latency.get("persist_ms", 0.0),
                "summary": txn.decision,
                "detail": {
                    "reason": decision.reason if decision else None,
                    "reason_codes": decision.reason_codes if decision else [],
                    "thresholds": decision.thresholds if decision else {},
                    "policy_version": decision.policy_version if decision else None,
                },
            },
        ],
        "risk": {
            "final_score": safe_float(txn.risk_score),
            "risk_band": txn.risk_band,
            "components": risk.components if risk else {},
            "weights": risk.weights if risk else {},
            "top_factors": risk.top_factors if risk else [],
        },
        "latency": latency,
        "model_version": txn.model_version,
    }


@router.get("/{transaction_id}/explain", summary="Model explanation (SHAP attributions)")
def explain(
    transaction_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RISK_READ))],
) -> dict[str, Any]:
    prediction = db.execute(
        select(FraudPrediction)
        .where(FraudPrediction.transaction_id == transaction_id)
        .order_by(FraudPrediction.predicted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prediction is None:
        raise NotFoundError(
            f"No prediction is recorded for transaction {transaction_id}.",
            code="PREDICTION_NOT_FOUND",
        )
    risk = db.execute(
        select(RiskScore)
        .where(RiskScore.transaction_id == transaction_id)
        .order_by(RiskScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "transaction_id": transaction_id,
        "model_name": prediction.model_name,
        "model_version": prediction.model_version,
        "probability": safe_float(prediction.probability),
        "threshold": safe_float(prediction.threshold),
        "predicted_label": prediction.predicted_label,
        "inference_ms": safe_float(prediction.inference_ms),
        "explanation": prediction.explanation or {},
        "ensemble_factors": risk.top_factors if risk else [],
        "components": risk.components if risk else {},
    }
