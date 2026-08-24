"""Customer, account, merchant, device and transaction tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Money, Score, SoftDeleteMixin, TimestampMixin


class Customer(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_risk", "risk_band", "risk_score"),
        Index("ix_customers_country_segment", "country", "segment"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    segment: Mapped[str] = mapped_column(String(32), default="RETAIL", index=True)
    kyc_status: Mapped[str] = mapped_column(String(24), default="VERIFIED")
    country: Mapped[str] = mapped_column(String(2), default="IN", index=True)
    city: Mapped[str] = mapped_column(String(80), default="")
    home_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    onboarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenure_days: Mapped[int] = mapped_column(Integer, default=0)

    # Behavioural profile maintained incrementally by the feature service.
    avg_transaction_amount: Mapped[float] = mapped_column(Money, default=0.0)
    std_transaction_amount: Mapped[float] = mapped_column(Money, default=0.0)
    max_transaction_amount: Mapped[float] = mapped_column(Money, default=0.0)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_value: Mapped[float] = mapped_column(Money, default=0.0)
    typical_merchant_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    typical_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distinct_device_count: Mapped[int] = mapped_column(Integer, default=0)
    chargeback_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_fraud_count: Mapped[int] = mapped_column(Integer, default=0)

    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    risk_band: Mapped[str] = mapped_column(String(16), default="LOW", index=True)
    risk_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watchlisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    accounts: Mapped[list[Account]] = relationship(back_populates="customer")


class Account(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_type: Mapped[str] = mapped_column(String(24), default="CHECKING")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    masked_number: Mapped[str] = mapped_column(String(32), default="")
    balance: Mapped[float] = mapped_column(Money, default=0.0)
    credit_limit: Mapped[float] = mapped_column(Money, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="accounts")


class Merchant(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "merchants"
    __table_args__ = (Index("ix_merchants_category_risk", "category", "risk_score"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    mcc: Mapped[str] = mapped_column(String(8), default="0000")
    country: Mapped[str] = mapped_column(String(2), default="IN", index=True)
    city: Mapped[str] = mapped_column(String(80), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    onboarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    transaction_volume: Mapped[float] = mapped_column(Money, default=0.0)
    fraud_count: Mapped[int] = mapped_column(Integer, default=0)
    fraud_rate: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    chargeback_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_ticket: Mapped[float] = mapped_column(Money, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    risk_band: Mapped[str] = mapped_column(String(16), default="LOW", index=True)
    risk_category: Mapped[str] = mapped_column(String(24), default="STANDARD")
    high_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class Device(Base, TimestampMixin):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_type: Mapped[str] = mapped_column(String(24), default="MOBILE")
    os: Mapped[str] = mapped_column(String(32), default="")
    browser: Mapped[str] = mapped_column(String(48), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_customers: Mapped[int] = mapped_column(Integer, default=1, index=True)
    distinct_ips: Mapped[int] = mapped_column(Integer, default=1)
    fraud_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    is_emulator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class DeviceLink(Base):
    """Materialised customer <-> device edge; the backbone of ring detection."""

    __tablename__ = "device_links"
    __table_args__ = (
        Index("ix_device_links_device", "device_id"),
        Index("ix_device_links_customer", "customer_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # device_id::customer_id
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[float] = mapped_column(Money, default=0.0)
    fraud_count: Mapped[int] = mapped_column(Integer, default=0)


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_customer_time", "customer_id", "occurred_at"),
        Index("ix_transactions_merchant_time", "merchant_id", "occurred_at"),
        Index("ix_transactions_device_time", "device_id", "occurred_at"),
        Index("ix_transactions_decision_time", "decision", "occurred_at"),
        Index("ix_transactions_risk", "risk_score"),
        Index("ix_transactions_fraud", "is_fraud", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    # Producer supplied event identity -- the deduplication key.
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    amount: Mapped[float] = mapped_column(Money, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payment_method: Mapped[str] = mapped_column(String(32), default="CARD", index=True)
    merchant_category: Mapped[str] = mapped_column(String(48), default="", index=True)
    channel: Mapped[str] = mapped_column(String(24), default="WEB", index=True)
    transaction_type: Mapped[str] = mapped_column(String(24), default="PURCHASE")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    country: Mapped[str] = mapped_column(String(2), default="IN", index=True)
    city: Mapped[str] = mapped_column(String(80), default="")
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Decision outcome, denormalised onto the transaction for fast querying.
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_band: Mapped[str] = mapped_column(String(16), default="LOW", index=True)
    decision: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    fraud_probability: Mapped[float] = mapped_column(Score, default=0.0)
    anomaly_score: Mapped[float] = mapped_column(Score, default=0.0)
    graph_risk: Mapped[float] = mapped_column(Score, default=0.0)
    rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    processing_ms: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Ground truth. NULL means "not yet reviewed"; set by analysts or by the
    # synthetic generator in demo mode.
    is_fraud: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fraud_type: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    label_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    labelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class TransactionFeature(Base):
    """Materialised feature vector -- the offline/online feature store record."""

    __tablename__ = "transaction_features"

    transaction_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    customer_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    feature_version: Mapped[str] = mapped_column(String(24), default="v1")
    computation_ms: Mapped[float] = mapped_column(Float, default=0.0)

    # Columns promoted out of the JSON blob because drift monitoring, the model
    # trainer and analyst filters all read them directly.
    amount: Mapped[float] = mapped_column(Money, default=0.0)
    amount_log: Mapped[float] = mapped_column(Float, default=0.0)
    amount_zscore: Mapped[float] = mapped_column(Float, default=0.0)
    amount_ratio_to_avg: Mapped[float] = mapped_column(Float, default=0.0)
    amount_percentile: Mapped[float] = mapped_column(Float, default=0.0)
    txn_count_1m: Mapped[int] = mapped_column(Integer, default=0)
    txn_count_5m: Mapped[int] = mapped_column(Integer, default=0)
    txn_count_1h: Mapped[int] = mapped_column(Integer, default=0)
    txn_count_24h: Mapped[int] = mapped_column(Integer, default=0)
    amount_sum_1h: Mapped[float] = mapped_column(Money, default=0.0)
    amount_sum_24h: Mapped[float] = mapped_column(Money, default=0.0)
    seconds_since_prev: Mapped[float] = mapped_column(Float, default=0.0)
    distance_from_prev_km: Mapped[float] = mapped_column(Float, default=0.0)
    velocity_kmh: Mapped[float] = mapped_column(Float, default=0.0)
    impossible_travel: Mapped[int] = mapped_column(Integer, default=0)
    country_change: Mapped[int] = mapped_column(Integer, default=0)
    is_new_device: Mapped[int] = mapped_column(Integer, default=0)
    device_customer_count: Mapped[int] = mapped_column(Integer, default=1)
    device_risk: Mapped[float] = mapped_column(Float, default=0.0)
    merchant_fraud_rate: Mapped[float] = mapped_column(Float, default=0.0)
    merchant_risk: Mapped[float] = mapped_column(Float, default=0.0)
    is_new_merchant_for_customer: Mapped[int] = mapped_column(Integer, default=0)
    category_mismatch: Mapped[int] = mapped_column(Integer, default=0)
    hour_of_day: Mapped[int] = mapped_column(Integer, default=0)
    day_of_week: Mapped[int] = mapped_column(Integer, default=0)
    is_night: Mapped[int] = mapped_column(Integer, default=0)
    hour_deviation: Mapped[float] = mapped_column(Float, default=0.0)
    customer_tenure_days: Mapped[int] = mapped_column(Integer, default=0)
    customer_txn_count: Mapped[int] = mapped_column(Integer, default=0)
    customer_prior_fraud: Mapped[int] = mapped_column(Integer, default=0)
    ip_customer_count: Mapped[int] = mapped_column(Integer, default=1)

    features: Mapped[dict] = mapped_column(JSON, default=dict)


class IngestedEvent(Base):
    """Deduplication ledger: durable proof an event id was already processed."""

    __tablename__ = "ingested_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    result: Mapped[str] = mapped_column(String(24), default="PROCESSED")
    payload_hash: Mapped[str] = mapped_column(String(64), default="")


class DeadLetterEvent(Base, TimestampMixin):
    """Events that exhausted their retries, retained for replay from the UI."""

    __tablename__ = "dead_letter_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="FAILED", index=True)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
