"""Real-time feature engineering.

One function -- :func:`compute_features` -- produces the feature vector used by
*both* the online decision path and the offline training pipeline, which is what
keeps training and serving consistent (no train/serve skew).

Every feature is computed from data that was available *strictly before* the
transaction being scored, so replaying history through this function is a valid
point-in-time reconstruction rather than a leak of the future.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import cache
from app.db.models.core import (
    Customer,
    Device,
    DeviceLink,
    Merchant,
    Transaction,
    TransactionFeature,
)
from app.utils import (
    clamp,
    haversine_km,
    implied_speed_kmh,
    percentile_rank,
    safe_float,
    to_utc,
    utcnow,
    zscore,
)

FEATURE_VERSION = "v1"

# Ordered contract shared by the feature store, the trainer and the serving path.
FEATURE_NAMES: tuple[str, ...] = (
    "amount",
    "amount_log",
    "amount_zscore",
    "amount_ratio_to_avg",
    "amount_percentile",
    "txn_count_1m",
    "txn_count_5m",
    "txn_count_1h",
    "txn_count_24h",
    "amount_sum_1h",
    "amount_sum_24h",
    "seconds_since_prev",
    "distance_from_prev_km",
    "velocity_kmh",
    "impossible_travel",
    "country_change",
    "is_new_device",
    "device_customer_count",
    "device_risk",
    "merchant_fraud_rate",
    "merchant_risk",
    "is_new_merchant_for_customer",
    "category_mismatch",
    "hour_of_day",
    "day_of_week",
    "is_night",
    "hour_deviation",
    "customer_tenure_days",
    "customer_txn_count",
    "customer_prior_fraud",
    "ip_customer_count",
    "is_high_risk_channel",
    "is_cross_border",
    "amount_to_max_ratio",
    "distinct_merchants_24h",
)

# Human readable labels used by explanations and the UI.
FEATURE_LABELS: dict[str, str] = {
    "amount": "Transaction amount",
    "amount_log": "Transaction amount (log)",
    "amount_zscore": "Amount vs customer profile (z-score)",
    "amount_ratio_to_avg": "Amount vs customer average",
    "amount_percentile": "Amount percentile for customer",
    "txn_count_1m": "Transactions in last minute",
    "txn_count_5m": "Transactions in last 5 minutes",
    "txn_count_1h": "Transactions in last hour",
    "txn_count_24h": "Transactions in last 24 hours",
    "amount_sum_1h": "Amount spent in last hour",
    "amount_sum_24h": "Amount spent in last 24 hours",
    "seconds_since_prev": "Seconds since previous transaction",
    "distance_from_prev_km": "Distance from previous transaction",
    "velocity_kmh": "Implied travel speed",
    "impossible_travel": "Impossible travel detected",
    "country_change": "Country changed since last transaction",
    "is_new_device": "New device for this customer",
    "device_customer_count": "Accounts sharing this device",
    "device_risk": "Device reputation risk",
    "merchant_fraud_rate": "Merchant historical fraud rate",
    "merchant_risk": "Merchant risk score",
    "is_new_merchant_for_customer": "First transaction with merchant",
    "category_mismatch": "Unusual merchant category",
    "hour_of_day": "Hour of day",
    "day_of_week": "Day of week",
    "is_night": "Night-time transaction",
    "hour_deviation": "Deviation from customer's usual hour",
    "customer_tenure_days": "Customer tenure (days)",
    "customer_txn_count": "Customer transaction history size",
    "customer_prior_fraud": "Customer prior confirmed fraud",
    "ip_customer_count": "Accounts sharing this IP",
    "is_high_risk_channel": "High-risk channel",
    "is_cross_border": "Cross-border transaction",
    "amount_to_max_ratio": "Amount vs customer maximum",
    "distinct_merchants_24h": "Distinct merchants in 24 hours",
}

HIGH_RISK_CHANNELS = {"WEB", "API"}
IMPOSSIBLE_TRAVEL_KMH = 900.0  # faster than a commercial aircraft
NIGHT_HOURS = range(0, 6)
LOOKBACK_HOURS = 24
HISTORY_LIMIT = 400


@dataclass
class FeatureVector:
    """Computed features plus the context the explanation layer needs."""

    values: dict[str, float] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    computation_ms: float = 0.0
    version: str = FEATURE_VERSION

    def as_list(self) -> list[float]:
        return [safe_float(self.values.get(name, 0.0)) for name in FEATURE_NAMES]

    def get(self, name: str, default: float = 0.0) -> float:
        return safe_float(self.values.get(name, default), default)


@dataclass
class TransactionContext:
    """Everything the feature computation needs about one incoming transaction."""

    transaction_id: str
    customer_id: str
    merchant_id: str
    amount: float
    occurred_at: datetime
    device_id: str | None = None
    ip_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str = "IN"
    city: str = ""
    channel: str = "WEB"
    payment_method: str = "CARD"
    merchant_category: str = ""
    account_id: str | None = None
    session_id: str | None = None


def _recent_transactions(
    db: Session, customer_id: str, before: datetime, limit: int = HISTORY_LIMIT
) -> list[Transaction]:
    """Customer history strictly before ``before`` -- the point-in-time guard."""
    stmt = (
        select(Transaction)
        .where(Transaction.customer_id == customer_id, Transaction.occurred_at < before)
        .order_by(Transaction.occurred_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def _count_within(history: Sequence[Transaction], now: datetime, seconds: int) -> int:
    cutoff = now - timedelta(seconds=seconds)
    return sum(1 for txn in history if (to_utc(txn.occurred_at) or now) >= cutoff)


def _sum_within(history: Sequence[Transaction], now: datetime, seconds: int) -> float:
    cutoff = now - timedelta(seconds=seconds)
    return float(sum(txn.amount for txn in history if (to_utc(txn.occurred_at) or now) >= cutoff))


def _ip_customer_count(db: Session, ip_address: str | None, before: datetime) -> int:
    if not ip_address:
        return 1
    cache_key = f"feat:ipcust:{ip_address}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return int(cached)
    cutoff = before - timedelta(days=30)
    stmt = select(func.count(func.distinct(Transaction.customer_id))).where(
        Transaction.ip_address == ip_address,
        Transaction.occurred_at >= cutoff,
        Transaction.occurred_at < before,
    )
    count = int(db.execute(stmt).scalar_one() or 0)
    result = max(count, 1)
    cache.set_json(cache_key, result, ttl=60)
    return result


def _device_state(
    db: Session, device_id: str | None, customer_id: str, before: datetime
) -> tuple[int, int, float]:
    """Return ``(is_new_device, distinct_customers, device_risk)``."""
    if not device_id:
        return 1, 1, 0.35  # a missing device fingerprint is itself mildly risky

    link = db.get(DeviceLink, f"{device_id}::{customer_id}")
    seen_before = bool(link and link.transaction_count > 0 and to_utc(link.first_seen_at) and to_utc(link.first_seen_at) < before)  # type: ignore[operator]
    linked = int(
        db.execute(
            select(func.count()).select_from(DeviceLink).where(DeviceLink.device_id == device_id)
        ).scalar_one()
        or 0
    )
    # The link row for this customer is written after scoring, so add them in.
    distinct = linked + (0 if seen_before else 1)
    device = db.get(Device, device_id)
    risk = safe_float(device.risk_score if device else 0.3)
    if device and device.is_blacklisted:
        risk = max(risk, 0.95)
    return (0 if seen_before else 1), max(distinct, 1), clamp(risk)


def compute_features(
    db: Session | None,
    ctx: TransactionContext,
    *,
    customer: Customer | None = None,
    merchant: Merchant | None = None,
    history: Sequence[Any] | None = None,
    device_state: tuple[int, int, float] | None = None,
    ip_customer_count: int | None = None,
) -> FeatureVector:
    """Compute the full feature vector for one transaction.

    ``history``, ``device_state`` and ``ip_customer_count`` may be injected by the
    batch backfill, which keeps that state in memory.  Injection is the only
    difference between the online and offline paths -- the feature definitions
    below are shared, which is what prevents train/serve skew.
    """
    started = time.perf_counter()
    now = to_utc(ctx.occurred_at) or utcnow()

    if customer is None and db is not None:
        customer = db.get(Customer, ctx.customer_id)
    if merchant is None and db is not None:
        merchant = db.get(Merchant, ctx.merchant_id)
    if history is None:
        history = _recent_transactions(db, ctx.customer_id, now) if db is not None else []

    amounts = [float(txn.amount) for txn in history]
    profile_avg = safe_float(customer.avg_transaction_amount if customer else 0.0)
    profile_std = safe_float(customer.std_transaction_amount if customer else 0.0)
    profile_max = safe_float(customer.max_transaction_amount if customer else 0.0)
    if not profile_avg and amounts:
        profile_avg = sum(amounts) / len(amounts)
    if not profile_max and amounts:
        profile_max = max(amounts)

    previous = history[0] if history else None
    prev_time = to_utc(previous.occurred_at) if previous else None
    seconds_since_prev = (now - prev_time).total_seconds() if prev_time else 0.0
    distance_km = (
        haversine_km(previous.latitude, previous.longitude, ctx.latitude, ctx.longitude)
        if previous
        else 0.0
    )
    speed = implied_speed_kmh(distance_km, seconds_since_prev) if previous else 0.0
    impossible = 1 if (previous and distance_km > 120 and speed > IMPOSSIBLE_TRAVEL_KMH) else 0
    country_change = 1 if previous and previous.country != ctx.country else 0

    is_new_device, device_customers, device_risk = device_state or (
        _device_state(db, ctx.device_id, ctx.customer_id, now) if db is not None else (1, 1, 0.35)
    )
    merchant_fraud_rate = safe_float(merchant.fraud_rate if merchant else 0.0)
    merchant_risk = safe_float(merchant.risk_score if merchant else 0.0) / 100.0
    merchant_seen = any(txn.merchant_id == ctx.merchant_id for txn in history)
    category = ctx.merchant_category or (merchant.category if merchant else "")
    typical_category = customer.typical_merchant_category if customer else None
    category_mismatch = 1 if typical_category and category and category != typical_category else 0

    typical_hour = customer.typical_hour if customer and customer.typical_hour is not None else None
    hour_deviation = (
        min(abs(now.hour - typical_hour), 24 - abs(now.hour - typical_hour))
        if typical_hour is not None
        else 0.0
    )

    distinct_merchants_24h = len(
        {
            txn.merchant_id
            for txn in history
            if (to_utc(txn.occurred_at) or now) >= now - timedelta(hours=24)
        }
    )

    values: dict[str, float] = {
        "amount": round(float(ctx.amount), 2),
        "amount_log": round(math.log1p(max(float(ctx.amount), 0.0)), 5),
        "amount_zscore": round(zscore(ctx.amount, profile_avg, profile_std), 4),
        "amount_ratio_to_avg": round(ctx.amount / profile_avg, 4) if profile_avg > 0 else 1.0,
        "amount_percentile": percentile_rank(amounts, ctx.amount) if amounts else 0.5,
        "txn_count_1m": _count_within(history, now, 60),
        "txn_count_5m": _count_within(history, now, 300),
        "txn_count_1h": _count_within(history, now, 3600),
        "txn_count_24h": _count_within(history, now, 86400),
        "amount_sum_1h": round(_sum_within(history, now, 3600), 2),
        "amount_sum_24h": round(_sum_within(history, now, 86400), 2),
        "seconds_since_prev": round(seconds_since_prev, 2),
        "distance_from_prev_km": round(distance_km, 2),
        "velocity_kmh": round(min(speed, 1e6), 2),
        "impossible_travel": impossible,
        "country_change": country_change,
        "is_new_device": is_new_device,
        "device_customer_count": device_customers,
        "device_risk": round(device_risk, 4),
        "merchant_fraud_rate": round(merchant_fraud_rate, 5),
        "merchant_risk": round(clamp(merchant_risk), 4),
        "is_new_merchant_for_customer": 0 if merchant_seen else 1,
        "category_mismatch": category_mismatch,
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
        "is_night": 1 if now.hour in NIGHT_HOURS else 0,
        "hour_deviation": round(float(hour_deviation), 2),
        "customer_tenure_days": int(customer.tenure_days if customer else 0),
        "customer_txn_count": int(customer.transaction_count if customer else len(history)),
        "customer_prior_fraud": int(customer.confirmed_fraud_count if customer else 0),
        "ip_customer_count": (
            ip_customer_count
            if ip_customer_count is not None
            else (_ip_customer_count(db, ctx.ip_address, now) if db is not None else 1)
        ),
        "is_high_risk_channel": 1 if ctx.channel in HIGH_RISK_CHANNELS else 0,
        "is_cross_border": (
            1 if customer and customer.country and customer.country != ctx.country else 0
        ),
        "amount_to_max_ratio": round(ctx.amount / profile_max, 4) if profile_max > 0 else 1.0,
        "distinct_merchants_24h": distinct_merchants_24h,
    }

    context = {
        "customer_avg_amount": round(profile_avg, 2),
        "customer_std_amount": round(profile_std, 2),
        "customer_max_amount": round(profile_max, 2),
        "history_size": len(history),
        "previous_transaction_id": previous.id if previous else None,
        "previous_city": previous.city if previous else None,
        "previous_country": previous.country if previous else None,
        "merchant_name": merchant.name if merchant else None,
        "merchant_category": category,
        "typical_merchant_category": typical_category,
        "device_id": ctx.device_id,
        "feature_version": FEATURE_VERSION,
    }

    return FeatureVector(
        values=values,
        context=context,
        computation_ms=round((time.perf_counter() - started) * 1000, 3),
    )


def persist_features(db: Session, transaction_id: str, customer_id: str, fv: FeatureVector) -> None:
    """Write the vector to the feature store table (upsert semantics)."""
    record = db.get(TransactionFeature, transaction_id)
    if record is None:
        record = TransactionFeature(transaction_id=transaction_id, customer_id=customer_id)
        db.add(record)
    record.customer_id = customer_id
    record.computed_at = utcnow()
    record.feature_version = fv.version
    record.computation_ms = fv.computation_ms
    record.features = fv.values
    for column in (
        "amount",
        "amount_log",
        "amount_zscore",
        "amount_ratio_to_avg",
        "amount_percentile",
        "txn_count_1m",
        "txn_count_5m",
        "txn_count_1h",
        "txn_count_24h",
        "amount_sum_1h",
        "amount_sum_24h",
        "seconds_since_prev",
        "distance_from_prev_km",
        "velocity_kmh",
        "impossible_travel",
        "country_change",
        "is_new_device",
        "device_customer_count",
        "device_risk",
        "merchant_fraud_rate",
        "merchant_risk",
        "is_new_merchant_for_customer",
        "category_mismatch",
        "hour_of_day",
        "day_of_week",
        "is_night",
        "hour_deviation",
        "customer_tenure_days",
        "customer_txn_count",
        "customer_prior_fraud",
        "ip_customer_count",
    ):
        setattr(record, column, fv.values.get(column, 0))


def update_customer_profile(
    customer: Customer, amount: float, occurred_at: datetime, category: str
) -> None:
    """Incrementally maintain the behavioural profile (Welford-style update)."""
    count = customer.transaction_count or 0
    avg = safe_float(customer.avg_transaction_amount)
    std = safe_float(customer.std_transaction_amount)

    new_count = count + 1
    new_avg = avg + (amount - avg) / new_count
    # Track variance through the running sum of squared deviations.
    prev_m2 = (std**2) * max(count - 1, 0)
    new_m2 = prev_m2 + (amount - avg) * (amount - new_avg)
    new_std = (new_m2 / max(new_count - 1, 1)) ** 0.5 if new_count > 1 else 0.0

    customer.transaction_count = new_count
    customer.avg_transaction_amount = round(new_avg, 2)
    customer.std_transaction_amount = round(new_std, 2)
    customer.max_transaction_amount = round(
        max(safe_float(customer.max_transaction_amount), amount), 2
    )
    customer.lifetime_value = round(safe_float(customer.lifetime_value) + amount, 2)
    if category:
        customer.typical_merchant_category = customer.typical_merchant_category or category
    hour = (to_utc(occurred_at) or utcnow()).hour
    customer.typical_hour = hour if customer.typical_hour is None else customer.typical_hour
