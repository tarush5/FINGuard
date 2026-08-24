"""Drift detection.

Compares the live feature distribution against the distribution the production
model was trained on, using PSI (the industry standard for tabular monitoring)
plus a two-sample KS statistic.  Prediction drift is tracked the same way, so a
model whose inputs look stable but whose output distribution has shifted is
still caught.

PSI convention used here (and shown in the UI):
    < 0.10  stable | 0.10 - 0.25  warning | >= 0.25  critical
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models.core import Transaction, TransactionFeature
from app.db.models.mlops import DriftMetric
from app.events.bus import event_bus
from app.events.schemas import Topic, make_event
from app.ml import registry
from app.utils import ks_statistic, psi, safe_float

logger = get_logger(__name__)

# Features worth monitoring: the ones that actually move the score.
MONITORED_FEATURES = (
    "amount",
    "amount_ratio_to_avg",
    "amount_zscore",
    "txn_count_5m",
    "txn_count_1h",
    "txn_count_24h",
    "device_customer_count",
    "is_new_device",
    "merchant_fraud_rate",
    "ip_customer_count",
    "distance_from_prev_km",
    "hour_of_day",
)


def status_for(score: float) -> str:
    if score >= settings.drift_psi_critical:
        return "CRITICAL"
    if score >= settings.drift_psi_warning:
        return "WARNING"
    return "HEALTHY"


def compute_drift(db: Session, *, window_days: int = 7, min_rows: int = 50) -> dict[str, Any]:
    """Compute feature and prediction drift for the production fraud model."""
    record = registry.production_record(db, registry.FRAUD_MODEL)
    baseline_stats = (record.baseline_stats if record else {}) or {}
    cutoff = utcnow() - timedelta(days=window_days)

    current_rows = list(
        db.execute(
            select(TransactionFeature.features)
            .join(Transaction, Transaction.id == TransactionFeature.transaction_id)
            .where(Transaction.occurred_at >= cutoff)
            .limit(20_000)
        ).scalars()
    )
    baseline_rows = list(
        db.execute(
            select(TransactionFeature.features)
            .join(Transaction, Transaction.id == TransactionFeature.transaction_id)
            .where(Transaction.occurred_at < cutoff)
            .limit(20_000)
        ).scalars()
    )

    if len(current_rows) < min_rows or len(baseline_rows) < min_rows:
        return {
            "status": "INSUFFICIENT_DATA",
            "message": (
                f"Need at least {min_rows} rows on each side of the window "
                f"(baseline={len(baseline_rows)}, current={len(current_rows)})."
            ),
            "features": [],
        }

    now = utcnow()
    results: list[dict[str, Any]] = []
    worst = "HEALTHY"

    for feature in MONITORED_FEATURES:
        baseline_values = [safe_float((row or {}).get(feature)) for row in baseline_rows]
        current_values = [safe_float((row or {}).get(feature)) for row in current_rows]
        score, detail = psi(baseline_values, current_values)
        ks = ks_statistic(baseline_values, current_values)
        status = status_for(score)
        if status == "CRITICAL" or (status == "WARNING" and worst == "HEALTHY"):
            worst = status

        baseline_mean = sum(baseline_values) / len(baseline_values)
        current_mean = sum(current_values) / len(current_values)
        db.add(
            DriftMetric(
                id=new_id("DR"),
                model_version_id=record.id if record else None,
                feature_name=feature,
                drift_type="feature",
                computed_at=now,
                psi=score,
                ks_statistic=ks,
                baseline_mean=round(baseline_mean, 6),
                current_mean=round(current_mean, 6),
                status=status,
                baseline_window=f"older than {window_days}d",
                current_window=f"last {window_days}d",
                bins=detail,
            )
        )
        results.append(
            {
                "feature": feature,
                "psi": score,
                "ks_statistic": ks,
                "status": status,
                "baseline_mean": round(baseline_mean, 4),
                "current_mean": round(current_mean, 4),
                "shift_pct": round(
                    (
                        ((current_mean - baseline_mean) / baseline_mean * 100)
                        if baseline_mean
                        else 0.0
                    ),
                    2,
                ),
                "training_mean": (baseline_stats.get(feature) or {}).get("mean"),
                "bins": detail.get("bins", []),
            }
        )

    prediction_drift = _prediction_drift(db, cutoff, record.id if record else None, now)
    if prediction_drift:
        results.append(prediction_drift)
        if prediction_drift["status"] == "CRITICAL":
            worst = "CRITICAL"
        elif prediction_drift["status"] == "WARNING" and worst == "HEALTHY":
            worst = "WARNING"

    db.flush()

    if worst != "HEALTHY":
        offenders = [r["feature"] for r in results if r["status"] != "HEALTHY"]
        event_bus.publish(
            make_event(
                Topic.MODEL_EVENTS,
                "model.drift_detected",
                {
                    "title": f"Feature drift {worst.lower()}",
                    "body": f"PSI threshold exceeded for: {', '.join(offenders)}.",
                    "severity": "WARNING" if worst == "WARNING" else "CRITICAL",
                    "model_version_id": record.id if record else None,
                    "features": offenders,
                },
            )
        )

    logger.info(
        "drift_computed",
        extra={"status": worst, "features": len(results), "window_days": window_days},
    )
    return {
        "status": worst,
        "model_version": record.tag if record else None,
        "window_days": window_days,
        "baseline_rows": len(baseline_rows),
        "current_rows": len(current_rows),
        "thresholds": {
            "warning": settings.drift_psi_warning,
            "critical": settings.drift_psi_critical,
        },
        "features": sorted(results, key=lambda item: item["psi"], reverse=True),
        "computed_at": now.isoformat(),
    }


def _prediction_drift(
    db: Session, cutoff: Any, model_version_id: str | None, now: Any
) -> dict[str, Any] | None:
    baseline = [
        safe_float(value)
        for value in db.execute(
            select(Transaction.fraud_probability)
            .where(Transaction.occurred_at < cutoff)
            .limit(20_000)
        ).scalars()
    ]
    current = [
        safe_float(value)
        for value in db.execute(
            select(Transaction.fraud_probability)
            .where(Transaction.occurred_at >= cutoff)
            .limit(20_000)
        ).scalars()
    ]
    if len(baseline) < 50 or len(current) < 50:
        return None

    score, detail = psi(baseline, current)
    status = status_for(score)
    baseline_mean = sum(baseline) / len(baseline)
    current_mean = sum(current) / len(current)

    # A model swap inside the comparison window shifts the score distribution by
    # construction. Say so, rather than reporting it as unexplained drift.
    baseline_models = {
        m
        for m in db.execute(
            select(Transaction.model_version).where(Transaction.occurred_at < cutoff).distinct()
        ).scalars()
        if m
    }
    current_models = {
        m
        for m in db.execute(
            select(Transaction.model_version).where(Transaction.occurred_at >= cutoff).distinct()
        ).scalars()
        if m
    }
    note = (
        "Model version changed across the window "
        f"({', '.join(sorted(baseline_models))} -> {', '.join(sorted(current_models))}); "
        "a shift in the score distribution is expected."
        if baseline_models and current_models and baseline_models != current_models
        else None
    )
    db.add(
        DriftMetric(
            id=new_id("DR"),
            model_version_id=model_version_id,
            feature_name="fraud_probability",
            drift_type="prediction",
            computed_at=now,
            psi=score,
            ks_statistic=ks_statistic(baseline, current),
            baseline_mean=round(baseline_mean, 6),
            current_mean=round(current_mean, 6),
            status=status,
            bins=detail,
        )
    )
    return {
        "feature": "fraud_probability",
        "psi": score,
        "ks_statistic": ks_statistic(baseline, current),
        "status": status,
        "baseline_mean": round(baseline_mean, 6),
        "current_mean": round(current_mean, 6),
        "shift_pct": round(
            ((current_mean - baseline_mean) / baseline_mean * 100) if baseline_mean else 0.0, 2
        ),
        "drift_type": "prediction",
        "note": note,
        "bins": detail.get("bins", []),
    }


def latest(db: Session, *, limit: int = 40) -> dict[str, Any]:
    """Most recent drift computation, read back from the stored metrics."""
    from sqlalchemy import func

    latest_run = db.execute(select(func.max(DriftMetric.computed_at))).scalar_one_or_none()
    if latest_run is None:
        return {"status": "UNKNOWN", "features": [], "computed_at": None}
    rows = list(
        db.execute(
            select(DriftMetric)
            .where(DriftMetric.computed_at == latest_run)
            .order_by(DriftMetric.psi.desc())
            .limit(limit)
        ).scalars()
    )
    features = [
        {
            "feature": row.feature_name,
            "drift_type": row.drift_type,
            "psi": safe_float(row.psi),
            "ks_statistic": safe_float(row.ks_statistic),
            "status": row.status,
            "baseline_mean": safe_float(row.baseline_mean),
            "current_mean": safe_float(row.current_mean),
            "shift_pct": round(
                (
                    (
                        (safe_float(row.current_mean) - safe_float(row.baseline_mean))
                        / safe_float(row.baseline_mean)
                        * 100
                    )
                    if safe_float(row.baseline_mean)
                    else 0.0
                ),
                2,
            ),
            "bins": (row.bins or {}).get("bins", []),
        }
        for row in rows
    ]
    worst = "HEALTHY"
    for feature in features:
        if feature["status"] == "CRITICAL":
            worst = "CRITICAL"
            break
        if feature["status"] == "WARNING":
            worst = "WARNING"
    return {
        "status": worst,
        "features": features,
        "computed_at": latest_run.isoformat() if latest_run else None,
        "thresholds": {
            "warning": settings.drift_psi_warning,
            "critical": settings.drift_psi_critical,
        },
    }
