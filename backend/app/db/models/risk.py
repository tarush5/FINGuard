"""Rules, scoring, decisions, alerts, cases and fraud rings."""

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


class Rule(Base, TimestampMixin, SoftDeleteMixin):
    """A configurable detection rule.

    Conditions are stored as data (a small JSON expression tree evaluated by
    ``app.services.rules``) rather than as code, so analysts can author and edit
    rules from the UI without a deployment.
    """

    __tablename__ = "rules"
    __table_args__ = (Index("ix_rules_active_priority", "is_active", "priority"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(48), default="VELOCITY", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    version: Mapped[int] = mapped_column(Integer, default=1)

    condition: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_points: Mapped[float] = mapped_column(Float, default=10.0)
    action: Mapped[str] = mapped_column(String(24), default="SCORE")  # SCORE | REVIEW | DECLINE
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_shadow: Mapped[bool] = mapped_column(Boolean, default=False)

    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    true_positive_count: Mapped[int] = mapped_column(Integer, default=0)
    false_positive_count: Mapped[int] = mapped_column(Integer, default=0)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def precision(self) -> float:
        decided = self.true_positive_count + self.false_positive_count
        return round(self.true_positive_count / decided, 4) if decided else 0.0


class RuleExecution(Base):
    __tablename__ = "rule_executions"
    __table_args__ = (
        Index("ix_rule_executions_rule_time", "rule_id", "evaluated_at"),
        Index("ix_rule_executions_txn", "transaction_id"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, default=1)
    transaction_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    risk_points: Mapped[float] = mapped_column(Float, default=0.0)
    evaluation_ms: Mapped[float] = mapped_column(Float, default=0.0)
    matched_values: Mapped[dict] = mapped_column(JSON, default=dict)


class RiskScore(Base):
    """Full ensemble breakdown for one transaction -- the decision trace record."""

    __tablename__ = "risk_scores"
    __table_args__ = (Index("ix_risk_scores_time_band", "scored_at", "risk_band"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    fraud_probability: Mapped[float] = mapped_column(Score, default=0.0)
    anomaly_score: Mapped[float] = mapped_column(Score, default=0.0)
    customer_risk: Mapped[float] = mapped_column(Score, default=0.0)
    merchant_risk: Mapped[float] = mapped_column(Score, default=0.0)
    graph_risk: Mapped[float] = mapped_column(Score, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    risk_band: Mapped[str] = mapped_column(String(16), default="LOW")

    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    triggered_rules: Mapped[list] = mapped_column(JSON, default=list)
    top_factors: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ruleset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)


class FraudPrediction(Base):
    """One model inference, retained for monitoring, drift and back-testing."""

    __tablename__ = "fraud_predictions"
    __table_args__ = (Index("ix_fraud_predictions_model_time", "model_version", "predicted_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    probability: Mapped[float] = mapped_column(Score, default=0.0, index=True)
    predicted_label: Mapped[int] = mapped_column(Integer, default=0)
    threshold: Mapped[float] = mapped_column(Score, default=0.5)
    inference_ms: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)


class Decision(Base):
    """Immutable decision record: the auditable answer to 'what did we do?'."""

    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_outcome_time", "outcome", "decided_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(48), default="v1")
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_rules: Mapped[list] = mapped_column(JSON, default=list)
    processing_ms: Mapped[float] = mapped_column(Float, default=0.0)
    overridden_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_status_severity", "status", "severity"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    merchant_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(48), default="TRANSACTION_RISK", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    amount: Mapped[float] = mapped_column(Money, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    triggered_rules: Mapped[list] = mapped_column(JSON, default=list)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Case(Base, TimestampMixin):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_status_priority", "status", "priority"),
        Index("ix_cases_assignee_status", "assigned_to", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_number: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(28), default="NEW", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM", index=True)
    risk_band: Mapped[str] = mapped_column(String(16), default="MEDIUM", index=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    merchant_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    primary_transaction_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    fraud_ring_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    exposure_amount: Mapped[float] = mapped_column(Money, default=0.0)
    recovered_amount: Mapped[float] = mapped_column(Money, default=0.0)
    transaction_count: Mapped[int] = mapped_column(Integer, default=1)

    assigned_to: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    assigned_to_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    opened_by: Mapped[str] = mapped_column(String(64), default="system")
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tags: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    events: Mapped[list[CaseEvent]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    notes: Mapped[list[CaseNote]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseEvent(Base):
    __tablename__ = "case_events"
    __table_args__ = (Index("ix_case_events_case_time", "case_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(160), default="system")
    actor_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="INFO")
    entity_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    case: Mapped[Case] = relationship(back_populates="events")


class CaseNote(Base, TimestampMixin):
    __tablename__ = "case_notes"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    author_name: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list] = mapped_column(JSON, default=list)

    case: Mapped[Case] = relationship(back_populates="notes")


class FraudRing(Base, TimestampMixin):
    __tablename__ = "fraud_rings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    detection_method: Mapped[str] = mapped_column(String(48), default="SHARED_DEVICE", index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[float] = mapped_column(Money, default=0.0)
    fraud_probability: Mapped[float] = mapped_column(Score, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    shared_devices: Mapped[list] = mapped_column(JSON, default=list)
    shared_ips: Mapped[list] = mapped_column(JSON, default=list)
    shared_merchants: Mapped[list] = mapped_column(JSON, default=list)
    density: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    members: Mapped[list[FraudRingMember]] = relationship(
        back_populates="ring", cascade="all, delete-orphan"
    )


class FraudRingMember(Base):
    __tablename__ = "fraud_ring_members"
    __table_args__ = (Index("ix_ring_members_entity", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ring_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("fraud_rings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_in_ring: Mapped[str] = mapped_column(String(32), default="MEMBER")
    centrality: Mapped[float] = mapped_column(Float, default=0.0)
    risk_contribution: Mapped[float] = mapped_column(Float, default=0.0)

    ring: Mapped[FraudRing] = relationship(back_populates="members")
