"""Model registry, metrics, drift and the analyst feedback loop."""

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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Score, TimestampMixin


class ModelVersion(Base, TimestampMixin):
    """Registry entry. This table -- not MLflow -- is the serving source of truth.

    When an MLflow tracking server is configured the run id / model uri are
    recorded here so both systems stay reconcilable.
    """

    __tablename__ = "model_versions"
    __table_args__ = (
        Index("ix_model_versions_name_stage", "name", "stage"),
        Index("ix_model_versions_name_version", "name", "version", unique=True),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    tag: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. Fraud-XGB-v8
    algorithm: Mapped[str] = mapped_column(String(64), default="xgboost")
    task: Mapped[str] = mapped_column(String(48), default="fraud_classification")
    stage: Mapped[str] = mapped_column(String(24), default="STAGING", index=True)
    status: Mapped[str] = mapped_column(String(24), default="READY")

    artifact_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    feature_names: Mapped[list] = mapped_column(JSON, default=list)
    hyperparameters: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold: Mapped[float] = mapped_column(Score, default=0.5)
    training_rows: Mapped[int] = mapped_column(Integer, default=0)
    validation_rows: Mapped[int] = mapped_column(Integer, default=0)
    test_rows: Mapped[int] = mapped_column(Integer, default=0)
    positive_rate: Mapped[float] = mapped_column(Float, default=0.0)
    training_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trained_by: Mapped[str] = mapped_column(String(120), default="training-pipeline")
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    training_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    training_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mlflow_model_uri: Mapped[str | None] = mapped_column(String(400), nullable=True)
    baseline_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class ModelMetric(Base):
    """Time series of evaluation / production metrics per model version."""

    __tablename__ = "model_metrics"
    __table_args__ = (Index("ix_model_metrics_model_time", "model_version_id", "recorded_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    model_version_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window: Mapped[str] = mapped_column(String(24), default="offline")
    metric_name: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    metric_value: Mapped[float] = mapped_column(Float, default=0.0)
    threshold_warning: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_critical: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="HEALTHY", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class DriftMetric(Base):
    """Population Stability Index per feature, plus prediction drift."""

    __tablename__ = "drift_metrics"
    __table_args__ = (Index("ix_drift_feature_time", "feature_name", "computed_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    model_version_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    feature_name: Mapped[str] = mapped_column(String(64), nullable=False)
    drift_type: Mapped[str] = mapped_column(String(24), default="feature", index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    psi: Mapped[float] = mapped_column(Float, default=0.0)
    ks_statistic: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_mean: Mapped[float] = mapped_column(Float, default=0.0)
    current_mean: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="HEALTHY", index=True)
    baseline_window: Mapped[str] = mapped_column(String(48), default="")
    current_window: Mapped[str] = mapped_column(String(48), default="")
    bins: Mapped[dict] = mapped_column(JSON, default=dict)


class FeedbackLabel(Base, TimestampMixin):
    """Analyst verdicts feeding the retraining dataset."""

    __tablename__ = "feedback_labels"
    __table_args__ = (Index("ix_feedback_used_time", "used_in_training", "created_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    label: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 fraud, 0 legitimate
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    analyst_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analyst_name: Mapped[str] = mapped_column(String(160), default="")
    predicted_probability: Mapped[float] = mapped_column(Score, default=0.0)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    used_in_training: Mapped[bool] = mapped_column(Boolean, default=False)


class TrainingRun(Base, TimestampMixin):
    """Experiment tracking row -- mirrors an MLflow run when one is configured."""

    __tablename__ = "training_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    experiment: Mapped[str] = mapped_column(String(80), default="finguard-fraud", index=True)
    run_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(String(64), default="xgboost")
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_rows: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str] = mapped_column(String(120), default="manual")
    model_version_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
