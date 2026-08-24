"""Financial, fraud, merchant and customer analytics.

Every number the UI shows is computed here with SQL aggregation over the
warehouse tables -- no constants, no placeholder KPIs.  Expensive rollups are
cached briefly in Redis (or the in-process cache) because command-centre widgets
poll frequently.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.cache import cache
from app.core.config import settings
from app.db.models.core import Customer, Merchant, Transaction
from app.db.models.risk import Alert, Case
from app.utils import safe_float, utcnow

CACHE_TTL = 20

# Business cost assumptions used for loss analytics; identical to the values the
# decision engine optimises against, so the two views cannot disagree.
COST_FALSE_POSITIVE = settings.cost_false_positive
COST_MANUAL_REVIEW = settings.cost_manual_review


def _dialect(db: Session) -> str:
    bind = db.get_bind()
    return bind.dialect.name if bind is not None else "sqlite"


def _bucket_expr(db: Session, granularity: str) -> Any:
    """Dialect-aware time bucketing (SQLite locally, PostgreSQL when deployed)."""
    if _dialect(db) == "postgresql":
        fmt = {
            "hour": 'YYYY-MM-DD"T"HH24:00',
            "day": "YYYY-MM-DD",
            "week": 'IYYY-"W"IW',
        }.get(granularity, "YYYY-MM-DD")
        return func.to_char(Transaction.occurred_at, fmt)
    fmt = {"hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d", "week": "%Y-W%W"}.get(
        granularity, "%Y-%m-%d"
    )
    return func.strftime(fmt, Transaction.occurred_at)


def _part_expr(db: Session, part: str) -> Any:
    """Extract weekday (0-6) or hour (0-23) portably."""
    if _dialect(db) == "postgresql":
        return func.extract("dow" if part == "dow" else "hour", Transaction.occurred_at)
    return func.strftime("%w" if part == "dow" else "%H", Transaction.occurred_at)


def _window(days: int) -> tuple[datetime, datetime]:
    end = utcnow()
    return end - timedelta(days=days), end


def _fraud_sum() -> Any:
    return func.coalesce(func.sum(cast(Transaction.is_fraud, Integer)), 0)


def overview(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Command centre KPI block, including period-over-period comparison."""

    def compute() -> dict[str, Any]:
        start, end = _window(days)
        previous_start = start - timedelta(days=days)

        def period_stats(window_start: datetime, window_end: datetime) -> dict[str, Any]:
            row = db.execute(
                select(
                    func.count(Transaction.id),
                    func.coalesce(func.sum(Transaction.amount), 0.0),
                    func.coalesce(func.avg(Transaction.amount), 0.0),
                    _fraud_sum(),
                    func.coalesce(
                        func.sum(
                            case((Transaction.is_fraud.is_(True), Transaction.amount), else_=0.0)
                        ),
                        0.0,
                    ),
                    func.coalesce(func.avg(Transaction.risk_score), 0.0),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Transaction.decision.in_(["DECLINE", "MANUAL_REVIEW"]),
                                    case(
                                        (Transaction.is_fraud.is_(True), Transaction.amount),
                                        else_=0.0,
                                    ),
                                ),
                                else_=0.0,
                            )
                        ),
                        0.0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    (Transaction.decision.in_(["DECLINE", "MANUAL_REVIEW"]))
                                    & (Transaction.is_fraud.isnot(True)),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                ).where(
                    Transaction.occurred_at >= window_start, Transaction.occurred_at < window_end
                )
            ).one()
            (
                count,
                volume,
                avg_amount,
                fraud_count,
                fraud_amount,
                avg_risk,
                prevented,
                false_positives,
            ) = row
            count = int(count or 0)
            return {
                "transactions": count,
                "volume": round(float(volume or 0), 2),
                "average_amount": round(float(avg_amount or 0), 2),
                "fraud_transactions": int(fraud_count or 0),
                "fraud_amount": round(float(fraud_amount or 0), 2),
                "fraud_rate": round((int(fraud_count or 0) / count * 100) if count else 0.0, 4),
                "average_risk_score": round(float(avg_risk or 0), 2),
                "prevented_loss": round(float(prevented or 0), 2),
                "false_positives": int(false_positives or 0),
            }

        current = period_stats(start, end)
        previous = period_stats(previous_start, start)

        open_cases = int(
            db.execute(
                select(func.count())
                .select_from(Case)
                .where(Case.status.notin_(["RESOLVED", "FALSE_POSITIVE", "CONFIRMED_FRAUD"]))
            ).scalar_one()
            or 0
        )
        previous_open_cases = int(
            db.execute(
                select(func.count()).select_from(Case).where(Case.created_at < start)
            ).scalar_one()
            or 0
        )

        def delta(now_value: float, before_value: float) -> float:
            if not before_value:
                return 0.0
            return round((now_value - before_value) / before_value * 100, 2)

        return {
            "window_days": days,
            "generated_at": utcnow().isoformat(),
            "currency": settings.currency,
            "kpis": [
                {
                    "key": "transactions",
                    "label": "Transactions",
                    "value": current["transactions"],
                    "format": "number",
                    "change_pct": delta(current["transactions"], previous["transactions"]),
                    "comparison": f"vs previous {days} days",
                },
                {
                    "key": "volume",
                    "label": "Processed volume",
                    "value": current["volume"],
                    "format": "currency",
                    "change_pct": delta(current["volume"], previous["volume"]),
                    "comparison": f"vs previous {days} days",
                },
                {
                    "key": "fraud_amount",
                    "label": "Fraud detected",
                    "value": current["fraud_amount"],
                    "format": "currency",
                    "change_pct": delta(current["fraud_amount"], previous["fraud_amount"]),
                    "comparison": f"vs previous {days} days",
                    "invert_trend": True,
                },
                {
                    "key": "fraud_rate",
                    "label": "Fraud rate",
                    "value": current["fraud_rate"],
                    "format": "percent",
                    "change_pct": delta(current["fraud_rate"], previous["fraud_rate"]),
                    "comparison": f"vs previous {days} days",
                    "invert_trend": True,
                },
                {
                    "key": "prevented_loss",
                    "label": "Prevented loss",
                    "value": current["prevented_loss"],
                    "format": "currency",
                    "change_pct": delta(current["prevented_loss"], previous["prevented_loss"]),
                    "comparison": f"vs previous {days} days",
                },
                {
                    "key": "open_cases",
                    "label": "Open cases",
                    "value": open_cases,
                    "format": "number",
                    "change_pct": delta(open_cases, previous_open_cases),
                    "comparison": "vs start of window",
                    "invert_trend": True,
                },
                {
                    "key": "average_risk",
                    "label": "Average risk score",
                    "value": current["average_risk_score"],
                    "format": "score",
                    "change_pct": delta(
                        current["average_risk_score"], previous["average_risk_score"]
                    ),
                    "comparison": f"vs previous {days} days",
                    "invert_trend": True,
                },
            ],
            "current": current,
            "previous": previous,
        }

    return cache.cached(f"analytics:overview:{days}", CACHE_TTL, compute)


def timeseries(db: Session, *, days: int = 30, bucket: str = "day") -> list[dict[str, Any]]:
    """Volume / fraud / decision time series used by every trend chart."""

    def compute() -> list[dict[str, Any]]:
        start, end = _window(days)
        bucket_expr = _bucket_expr(db, bucket)
        rows = db.execute(
            select(
                bucket_expr.label("bucket"),
                func.count(Transaction.id),
                func.coalesce(func.sum(Transaction.amount), 0.0),
                _fraud_sum(),
                func.coalesce(
                    func.sum(case((Transaction.is_fraud.is_(True), Transaction.amount), else_=0.0)),
                    0.0,
                ),
                func.coalesce(func.avg(Transaction.risk_score), 0.0),
                func.coalesce(func.sum(case((Transaction.decision == "DECLINE", 1), else_=0)), 0),
                func.coalesce(
                    func.sum(case((Transaction.decision == "MANUAL_REVIEW", 1), else_=0)), 0
                ),
            )
            .where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
            .group_by("bucket")
            .order_by("bucket")
        ).all()
        return [
            {
                "bucket": bucket_value,
                "transactions": int(count or 0),
                "volume": round(float(volume or 0), 2),
                "fraud_transactions": int(fraud or 0),
                "fraud_amount": round(float(fraud_amount or 0), 2),
                "fraud_rate": round((int(fraud or 0) / int(count or 1)) * 100, 4),
                "average_risk": round(float(avg_risk or 0), 2),
                "declines": int(declines or 0),
                "reviews": int(reviews or 0),
            }
            for bucket_value, count, volume, fraud, fraud_amount, avg_risk, declines, reviews in rows
        ]

    return cache.cached(f"analytics:timeseries:{days}:{bucket}", CACHE_TTL, compute)


def breakdown(
    db: Session, dimension: str, *, days: int = 30, limit: int = 12
) -> list[dict[str, Any]]:
    """Aggregate by a whitelisted dimension (payment method, channel, country...)."""
    columns = {
        "payment_method": Transaction.payment_method,
        "channel": Transaction.channel,
        "country": Transaction.country,
        "city": Transaction.city,
        "merchant_category": Transaction.merchant_category,
        "risk_band": Transaction.risk_band,
        "decision": Transaction.decision,
        "fraud_type": Transaction.fraud_type,
    }
    column = columns.get(dimension)
    if column is None:
        raise ValueError(f"Unsupported dimension '{dimension}'. Allowed: {sorted(columns)}")

    start, end = _window(days)
    rows = db.execute(
        select(
            column,
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
            _fraud_sum(),
            func.coalesce(
                func.sum(case((Transaction.is_fraud.is_(True), Transaction.amount), else_=0.0)), 0.0
            ),
            func.coalesce(func.avg(Transaction.risk_score), 0.0),
        )
        .where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
        .group_by(column)
        .order_by(func.count(Transaction.id).desc())
        .limit(limit)
    ).all()
    return [
        {
            "key": value or "UNKNOWN",
            "transactions": int(count or 0),
            "volume": round(float(volume or 0), 2),
            "fraud_transactions": int(fraud or 0),
            "fraud_amount": round(float(fraud_amount or 0), 2),
            "fraud_rate": round((int(fraud or 0) / int(count or 1)) * 100, 4),
            "average_risk": round(float(avg_risk or 0), 2),
        }
        for value, count, volume, fraud, fraud_amount, avg_risk in rows
    ]


def loss_analytics(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Gross loss, prevented loss, false-positive and investigation cost, net loss."""
    start, end = _window(days)

    row = db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.is_(True))
                            & (Transaction.decision.in_(["APPROVE", "STEP_UP"])),
                            Transaction.amount,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.is_(True))
                            & (Transaction.decision.in_(["DECLINE", "MANUAL_REVIEW"])),
                            Transaction.amount,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.isnot(True))
                            & (Transaction.decision.in_(["DECLINE", "MANUAL_REVIEW"])),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(case((Transaction.decision == "MANUAL_REVIEW", 1), else_=0)), 0),
            func.coalesce(func.sum(cast(Transaction.is_fraud, Integer)), 0),
        ).where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
    ).one()
    gross_loss, prevented, false_positives, reviews, fraud_count = row

    gross_loss = float(gross_loss or 0)
    prevented = float(prevented or 0)
    fp_cost = int(false_positives or 0) * COST_FALSE_POSITIVE
    review_cost = int(reviews or 0) * COST_MANUAL_REVIEW
    net_loss = gross_loss + fp_cost + review_cost

    return {
        "window_days": days,
        "currency": settings.currency,
        "gross_fraud_loss": round(gross_loss, 2),
        "prevented_fraud": round(prevented, 2),
        "false_positive_count": int(false_positives or 0),
        "false_positive_cost": round(fp_cost, 2),
        "manual_review_count": int(reviews or 0),
        "investigation_cost": round(review_cost, 2),
        "net_loss": round(net_loss, 2),
        "fraud_transactions": int(fraud_count or 0),
        "detection_rate": round(
            (prevented / (prevented + gross_loss) * 100) if (prevented + gross_loss) else 0.0, 2
        ),
        "cost_assumptions": {
            "false_positive": COST_FALSE_POSITIVE,
            "manual_review": COST_MANUAL_REVIEW,
        },
        "by_geography": breakdown(db, "country", days=days, limit=10),
        "by_channel": breakdown(db, "channel", days=days, limit=8),
        "by_payment_method": breakdown(db, "payment_method", days=days, limit=8),
        "by_fraud_type": breakdown(db, "fraud_type", days=days, limit=10),
    }


def detection_performance(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Confusion matrix of the *decision* (not the model) against ground truth."""
    start, end = _window(days)
    row = db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.is_(True))
                            & (Transaction.decision.in_(["DECLINE", "MANUAL_REVIEW"])),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.isnot(True))
                            & (Transaction.decision.in_(["DECLINE", "MANUAL_REVIEW"])),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.is_(True))
                            & (Transaction.decision.in_(["APPROVE", "STEP_UP"])),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.is_fraud.isnot(True))
                            & (Transaction.decision.in_(["APPROVE", "STEP_UP"])),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
    ).one()
    tp, fp, fn, tn = (int(v or 0) for v in row)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "window_days": days,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": (
            round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0
        ),
        "false_positive_rate": round(fp / (fp + tn), 5) if (fp + tn) else 0.0,
        "review_rate": round((tp + fp) / max(tp + fp + fn + tn, 1) * 100, 3),
        "note": "Measured on the decision outcome against labelled ground truth.",
    }


def merchant_analytics(db: Session, *, limit: int = 20, days: int = 30) -> list[dict[str, Any]]:
    start, end = _window(days)
    rows = db.execute(
        select(
            Merchant.id,
            Merchant.name,
            Merchant.category,
            Merchant.risk_score,
            Merchant.risk_band,
            Merchant.high_risk_flag,
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
            _fraud_sum(),
            func.coalesce(
                func.sum(case((Transaction.is_fraud.is_(True), Transaction.amount), else_=0.0)), 0.0
            ),
        )
        .join(Transaction, Transaction.merchant_id == Merchant.id)
        .where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
        .group_by(Merchant.id)
        .order_by(Merchant.risk_score.desc())
        .limit(limit)
    ).all()
    return [
        {
            "merchant_id": mid,
            "name": name,
            "category": category,
            "risk_score": safe_float(risk),
            "risk_band": band,
            "high_risk": bool(high_risk),
            "transactions": int(count or 0),
            "volume": round(float(volume or 0), 2),
            "fraud_transactions": int(fraud or 0),
            "fraud_amount": round(float(fraud_amount or 0), 2),
            "fraud_rate": round((int(fraud or 0) / int(count or 1)) * 100, 3),
        }
        for mid, name, category, risk, band, high_risk, count, volume, fraud, fraud_amount in rows
    ]


def customer_analytics(db: Session, *, limit: int = 20) -> dict[str, Any]:
    bands = db.execute(
        select(
            Customer.risk_band,
            func.count(Customer.id),
            func.coalesce(func.avg(Customer.lifetime_value), 0.0),
        ).group_by(Customer.risk_band)
    ).all()
    riskiest = db.execute(
        select(Customer).order_by(Customer.risk_score.desc()).limit(limit)
    ).scalars()
    valuable = db.execute(
        select(Customer).order_by(Customer.lifetime_value.desc()).limit(limit)
    ).scalars()
    return {
        "risk_distribution": [
            {
                "band": band or "LOW",
                "customers": int(count or 0),
                "average_lifetime_value": round(float(ltv or 0), 2),
            }
            for band, count, ltv in bands
        ],
        "highest_risk": [
            {
                "id": c.id,
                "name": c.full_name,
                "risk_score": safe_float(c.risk_score),
                "risk_band": c.risk_band,
                "segment": c.segment,
                "transactions": c.transaction_count,
                "confirmed_fraud": c.confirmed_fraud_count,
                "watchlisted": c.watchlisted,
            }
            for c in riskiest
        ],
        "most_valuable": [
            {
                "id": c.id,
                "name": c.full_name,
                "lifetime_value": safe_float(c.lifetime_value),
                "transactions": c.transaction_count,
                "segment": c.segment,
                "risk_band": c.risk_band,
            }
            for c in valuable
        ],
    }


def fraud_heatmap(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Fraud intensity by weekday x hour, plus category concentration."""
    start, end = _window(days)
    rows = db.execute(
        select(
            _part_expr(db, "dow"),
            _part_expr(db, "hour"),
            func.count(Transaction.id),
            _fraud_sum(),
        )
        .where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
        .group_by(_part_expr(db, "dow"), _part_expr(db, "hour"))
    ).all()
    cells = [
        {
            "day_of_week": int(day),
            "hour": int(hour),
            "transactions": int(count or 0),
            "fraud_transactions": int(fraud or 0),
            "fraud_rate": round((int(fraud or 0) / int(count or 1)) * 100, 3),
        }
        for day, hour, count, fraud in rows
    ]
    return {
        "window_days": days,
        "cells": cells,
        "by_category": breakdown(db, "merchant_category", days=days, limit=15),
        "max_fraud_rate": max((c["fraud_rate"] for c in cells), default=0.0),
    }


def geography(db: Session, *, days: int = 30) -> list[dict[str, Any]]:
    """Per-city rollup for the global risk map."""
    start, end = _window(days)
    rows = db.execute(
        select(
            Transaction.city,
            Transaction.country,
            func.avg(Transaction.latitude),
            func.avg(Transaction.longitude),
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
            _fraud_sum(),
            func.coalesce(
                func.sum(case((Transaction.is_fraud.is_(True), Transaction.amount), else_=0.0)), 0.0
            ),
            func.coalesce(func.avg(Transaction.risk_score), 0.0),
        )
        .where(Transaction.occurred_at >= start, Transaction.occurred_at <= end)
        .group_by(Transaction.city, Transaction.country)
        .order_by(func.count(Transaction.id).desc())
        .limit(60)
    ).all()
    return [
        {
            "city": city or "Unknown",
            "country": country,
            "latitude": round(float(lat or 0), 4),
            "longitude": round(float(lon or 0), 4),
            "transactions": int(count or 0),
            "volume": round(float(volume or 0), 2),
            "fraud_transactions": int(fraud or 0),
            "fraud_amount": round(float(fraud_amount or 0), 2),
            "fraud_rate": round((int(fraud or 0) / int(count or 1)) * 100, 3),
            "average_risk": round(float(avg_risk or 0), 2),
        }
        for city, country, lat, lon, count, volume, fraud, fraud_amount, avg_risk in rows
    ]


def operations_snapshot(db: Session) -> dict[str, Any]:
    """Alert/case queue health for the operations widgets."""
    case_rows = db.execute(select(Case.status, func.count()).group_by(Case.status)).all()
    priority_rows = db.execute(select(Case.priority, func.count()).group_by(Case.priority)).all()
    alert_rows = db.execute(select(Alert.severity, func.count()).group_by(Alert.severity)).all()

    overdue = int(
        db.execute(
            select(func.count())
            .select_from(Case)
            .where(
                Case.sla_due_at < utcnow(),
                Case.status.notin_(["RESOLVED", "CONFIRMED_FRAUD", "FALSE_POSITIVE"]),
            )
        ).scalar_one()
        or 0
    )
    resolved = db.execute(
        select(func.count(), func.coalesce(func.sum(Case.exposure_amount), 0.0)).where(
            Case.status.in_(["CONFIRMED_FRAUD", "FALSE_POSITIVE", "RESOLVED"])
        )
    ).one()
    confirmed = int(
        db.execute(
            select(func.count()).select_from(Case).where(Case.status == "CONFIRMED_FRAUD")
        ).scalar_one()
        or 0
    )
    false_positive = int(
        db.execute(
            select(func.count()).select_from(Case).where(Case.status == "FALSE_POSITIVE")
        ).scalar_one()
        or 0
    )
    decided = confirmed + false_positive
    return {
        "cases_by_status": [{"status": s, "count": int(c)} for s, c in case_rows],
        "cases_by_priority": [{"priority": p, "count": int(c)} for p, c in priority_rows],
        "alerts_by_severity": [{"severity": s, "count": int(c)} for s, c in alert_rows],
        "sla_breached": overdue,
        "resolved_cases": int(resolved[0] or 0),
        "resolved_exposure": round(float(resolved[1] or 0), 2),
        "confirmed_fraud_cases": confirmed,
        "false_positive_cases": false_positive,
        "investigation_precision": round(confirmed / decided, 4) if decided else 0.0,
    }
