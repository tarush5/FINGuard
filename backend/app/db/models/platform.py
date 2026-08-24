"""Data platform tables: datasets, pipelines, quality, lineage, AI queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    layer: Mapped[str] = mapped_column(String(24), default="raw", index=True)
    source_system: Mapped[str] = mapped_column(String(80), default="core-banking")
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(120), default="Data Platform")
    steward: Mapped[str] = mapped_column(String(120), default="")
    classification: Mapped[str] = mapped_column(String(24), default="INTERNAL", index=True)
    contains_pii: Mapped[bool] = mapped_column(Boolean, default=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    column_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    refresh_cadence: Mapped[str] = mapped_column(String(48), default="streaming")
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    freshness_sla_minutes: Mapped[int] = mapped_column(Integer, default=15)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_runs_pipeline_time", "pipeline", "started_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    pipeline: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    pipeline_type: Mapped[str] = mapped_column(String(24), default="batch")
    run_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    records_in: Mapped[int] = mapped_column(Integer, default=0)
    records_out: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str] = mapped_column(String(120), default="scheduler")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    steps: Mapped[list] = mapped_column(JSON, default=list)


class QualityCheck(Base):
    __tablename__ = "quality_checks"
    __table_args__ = (Index("ix_quality_dataset_time", "dataset", "run_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    check_name: Mapped[str] = mapped_column(String(120), nullable=False)
    dimension: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    expectation: Mapped[str] = mapped_column(String(255), default="")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="PASS", index=True)
    score: Mapped[float] = mapped_column(Float, default=100.0)
    rows_scanned: Mapped[int] = mapped_column(Integer, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, default=0)
    threshold: Mapped[float] = mapped_column(Float, default=99.0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class LineageEdge(Base, TimestampMixin):
    """Directed edge in the data lineage DAG."""

    __tablename__ = "lineage_edges"
    __table_args__ = (Index("ix_lineage_source_target", "source", "target", unique=True),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="dataset")
    target: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="dataset")
    transformation: Mapped[str] = mapped_column(String(160), default="")
    processor: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AIQuery(Base):
    """Every AI interaction is logged: prompt, generated SQL, evidence, outcome."""

    __tablename__ = "ai_queries"
    __table_args__ = (Index("ix_ai_queries_user_time", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_roles: Mapped[list] = mapped_column(JSON, default=list)
    surface: Mapped[str] = mapped_column(String(40), default="investigator", index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="deterministic")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    sql_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="SUCCESS", index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    entity_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SystemMetric(Base):
    """Persisted observability samples powering the System Health screen."""

    __tablename__ = "system_metrics"
    __table_args__ = (Index("ix_system_metrics_name_time", "name", "recorded_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    component: Mapped[str] = mapped_column(String(48), default="api", index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(24), default="count")
    status: Mapped[str] = mapped_column(String(16), default="HEALTHY")
    labels: Mapped[dict] = mapped_column(JSON, default=dict)


class DemoScenarioRun(Base, TimestampMixin):
    """Record of a one-click demo scenario execution and what it produced."""

    __tablename__ = "demo_scenario_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    scenario_key: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    scenario_name: Mapped[str] = mapped_column(String(120), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default="COMPLETED")
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    transaction_ids: Mapped[list] = mapped_column(JSON, default=list)
    case_ids: Mapped[list] = mapped_column(JSON, default=list)
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
