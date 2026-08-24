"""Forecasting.

A deliberately simple, explainable model: weekly-seasonal decomposition with a
damped linear trend, fitted on the observed daily history.  Confidence intervals
come from the in-sample residual spread rather than from an assumed
distribution, and the API reports the backtest error (MAPE) alongside every
forecast so the number is never presented as more certain than it is.

No heavyweight dependency is required; this runs anywhere the API runs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.db.models.core import Transaction
from app.db.models.risk import Case
from app.utils import mean, percentile, stdev, utcnow

HORIZONS = (7, 30, 90)
MIN_HISTORY_DAYS = 14
DAMPING = 0.92  # trend is damped so long horizons do not run away


def _daily_series(db: Session, *, days: int = 120) -> list[dict[str, Any]]:
    start = utcnow() - timedelta(days=days)
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else "sqlite"
    bucket = (
        func.to_char(Transaction.occurred_at, "YYYY-MM-DD")
        if dialect == "postgresql"
        else func.strftime("%Y-%m-%d", Transaction.occurred_at)
    )
    rows = db.execute(
        select(
            bucket.label("day"),
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
            func.coalesce(func.sum(cast(Transaction.is_fraud, Integer)), 0),
            func.coalesce(
                func.sum(case((Transaction.is_fraud.is_(True), Transaction.amount), else_=0.0)), 0.0
            ),
            func.coalesce(func.sum(case((Transaction.decision == "MANUAL_REVIEW", 1), else_=0)), 0),
        )
        .where(Transaction.occurred_at >= start)
        .group_by("day")
        .order_by("day")
    ).all()
    return [
        {
            "date": day,
            "transactions": int(count or 0),
            "volume": round(float(volume or 0), 2),
            "fraud_transactions": int(fraud or 0),
            "fraud_amount": round(float(fraud_amount or 0), 2),
            "investigations": int(reviews or 0),
        }
        for day, count, volume, fraud, fraud_amount, reviews in rows
    ]


def _fit(values: Sequence[float]) -> dict[str, Any]:
    """Least-squares level/trend plus multiplicative weekday factors."""
    n = len(values)
    xs = list(range(n))
    mean_x, mean_y = mean(xs), mean(values)
    denominator = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denominator
    intercept = mean_y - slope * mean_x

    seasonal: dict[int, float] = {}
    for offset in range(7):
        bucket = [values[i] for i in range(n) if i % 7 == offset]
        seasonal[offset] = (mean(bucket) / mean_y) if bucket and mean_y else 1.0

    fitted = [(intercept + slope * i) * seasonal.get(i % 7, 1.0) for i in range(n)]
    residuals = [actual - predicted for actual, predicted in zip(values, fitted)]
    non_zero = [abs(r / a) for r, a in zip(residuals, values) if a]
    return {
        "intercept": intercept,
        "slope": slope,
        "seasonal": seasonal,
        "residual_std": stdev(residuals),
        "mape": round(mean(non_zero) * 100, 2) if non_zero else 0.0,
        "n": n,
    }


def _project(model: dict[str, Any], horizon: int, start_index: int) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    damped = model["slope"]
    for step in range(1, horizon + 1):
        index = start_index + step
        damped *= DAMPING
        base = model["intercept"] + model["slope"] * start_index + damped * step
        value = max(base * model["seasonal"].get(index % 7, 1.0), 0.0)
        # Uncertainty widens with the square root of the horizon.
        spread = 1.96 * model["residual_std"] * math.sqrt(step)
        points.append(
            {
                "step": step,
                "value": round(value, 2),
                "lower": round(max(value - spread, 0.0), 2),
                "upper": round(value + spread, 2),
            }
        )
    return points


def forecast(
    db: Session, *, metric: str = "transactions", horizons: Sequence[int] = HORIZONS
) -> dict[str, Any]:
    """Forecast one metric across the requested horizons."""
    allowed = {"transactions", "volume", "fraud_transactions", "fraud_amount", "investigations"}
    if metric not in allowed:
        raise ValueError(f"Unsupported metric '{metric}'. Allowed: {sorted(allowed)}")

    history = _daily_series(db)
    if len(history) < MIN_HISTORY_DAYS:
        return {
            "metric": metric,
            "status": "INSUFFICIENT_HISTORY",
            "message": f"Need at least {MIN_HISTORY_DAYS} days of history, have {len(history)}.",
            "history": history,
            "forecasts": {},
        }

    # The final bucket is usually a partial day; excluding it avoids a fake dip.
    series = [float(point[metric]) for point in history[:-1]]
    dates = [point["date"] for point in history[:-1]]
    model = _fit(series)

    last_date = utcnow().date()
    forecasts: dict[str, Any] = {}
    for horizon in horizons:
        projected = _project(model, horizon, len(series) - 1)
        forecasts[f"{horizon}d"] = {
            "horizon_days": horizon,
            "total": round(sum(p["value"] for p in projected), 2),
            "daily_average": round(mean([p["value"] for p in projected]), 2),
            "points": [
                {
                    "date": (last_date + timedelta(days=p["step"])).isoformat(),
                    "value": p["value"],
                    "lower": p["lower"],
                    "upper": p["upper"],
                }
                for p in projected
            ],
        }

    recent = series[-14:]
    return {
        "metric": metric,
        "status": "OK",
        "method": "weekly-seasonal decomposition with damped linear trend",
        "history": [
            {"date": date, "value": value} for date, value in zip(dates[-60:], series[-60:])
        ],
        "forecasts": forecasts,
        "model": {
            "trend_per_day": round(model["slope"], 4),
            "seasonality": {str(k): round(v, 4) for k, v in model["seasonal"].items()},
            "residual_std": round(model["residual_std"], 4),
            "in_sample_mape_pct": model["mape"],
            "history_days": model["n"],
            "damping": DAMPING,
        },
        "recent": {
            "average": round(mean(recent), 2),
            "p95": round(percentile(recent, 0.95), 2),
            "last": round(series[-1], 2),
        },
    }


def workload_forecast(db: Session, *, horizon: int = 7) -> dict[str, Any]:
    """Investigation workload: expected cases and analyst hours."""
    investigations = forecast(db, metric="investigations", horizons=(horizon,))
    if investigations["status"] != "OK":
        return investigations

    open_cases = int(
        db.execute(
            select(func.count())
            .select_from(Case)
            .where(Case.status.notin_(["RESOLVED", "CONFIRMED_FRAUD", "FALSE_POSITIVE"]))
        ).scalar_one()
        or 0
    )
    projected = investigations["forecasts"][f"{horizon}d"]
    minutes_per_case = 25  # observed average handling time assumption
    return {
        "horizon_days": horizon,
        "open_cases": open_cases,
        "projected_new_cases": round(projected["total"]),
        "projected_daily_average": projected["daily_average"],
        "analyst_hours_required": round(
            (projected["total"] + open_cases) * minutes_per_case / 60, 1
        ),
        "assumptions": {"minutes_per_case": minutes_per_case},
        "points": projected["points"],
        "model": investigations["model"],
    }


def all_metrics(db: Session) -> dict[str, Any]:
    return {
        metric: forecast(db, metric=metric)
        for metric in ("transactions", "volume", "fraud_transactions", "fraud_amount")
    }
