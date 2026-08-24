"""Model registry: artifact persistence, staging and promotion.

The ``model_versions`` table is the serving source of truth.  When an MLflow
tracking server is configured every training run is *also* logged there and the
run id / model uri are stored on the registry row, so the two systems remain
reconcilable without MLflow becoming a hard runtime dependency.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import cache
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models.mlops import ModelMetric, ModelVersion

logger = get_logger(__name__)

STAGE_PRODUCTION = "PRODUCTION"
STAGE_STAGING = "STAGING"
STAGE_ARCHIVED = "ARCHIVED"

FRAUD_MODEL = "fraud_classifier"
ANOMALY_MODEL = "anomaly_detector"
CUSTOMER_RISK_MODEL = "customer_risk"

_lock = threading.RLock()
_bundle_cache: dict[str, ModelBundle] = {}


@dataclass
class ModelBundle:
    """A loaded model plus everything needed to score and explain with it."""

    name: str
    tag: str
    version: int
    estimator: Any
    feature_names: list[str]
    threshold: float
    metadata: dict[str, Any]
    scaler: Any | None = None
    baseline_stats: dict[str, Any] | None = None
    explainer: Any | None = None

    @property
    def identifier(self) -> str:
        return self.tag


def artifact_dir() -> Path:
    path = Path(settings.model_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_path(name: str, version: int) -> Path:
    return artifact_dir() / f"{name}_v{version}.joblib"


def save_artifact(
    name: str,
    version: int,
    *,
    estimator: Any,
    feature_names: list[str],
    threshold: float,
    metadata: dict[str, Any],
    scaler: Any | None = None,
    baseline_stats: dict[str, Any] | None = None,
) -> Path:
    path = artifact_path(name, version)
    joblib.dump(
        {
            "estimator": estimator,
            "feature_names": feature_names,
            "threshold": threshold,
            "metadata": metadata,
            "scaler": scaler,
            "baseline_stats": baseline_stats or {},
        },
        path,
        compress=3,
    )
    logger.info(
        "model_artifact_saved",
        extra={"model": name, "version": version, "path": str(path), "bytes": path.stat().st_size},
    )
    return path


def load_bundle(record: ModelVersion) -> ModelBundle:
    if not record.artifact_path or not Path(record.artifact_path).exists():
        raise NotFoundError(
            f"Artifact for {record.tag} is missing at {record.artifact_path}.",
            code="MODEL_ARTIFACT_MISSING",
        )
    payload = joblib.load(record.artifact_path)
    return ModelBundle(
        name=record.name,
        tag=record.tag,
        version=record.version,
        estimator=payload["estimator"],
        feature_names=list(payload.get("feature_names") or record.feature_names),
        threshold=float(payload.get("threshold", record.threshold)),
        metadata=payload.get("metadata", {}),
        scaler=payload.get("scaler"),
        baseline_stats=payload.get("baseline_stats") or record.baseline_stats,
    )


# The production pointer is read on every inference; caching the *id* (not the
# object) for a few seconds keeps the hot path off the database without letting a
# promotion take minutes to take effect.
_POINTER_TTL = 20


def production_record(db: Session, name: str) -> ModelVersion | None:
    pointer_key = f"model:production:{name}"
    pointer = cache.get_json(pointer_key)
    if pointer is not None:
        if pointer == "":
            return None
        record = db.get(ModelVersion, pointer)
        if record is not None and record.stage == STAGE_PRODUCTION:
            return record

    stmt = (
        select(ModelVersion)
        .where(ModelVersion.name == name, ModelVersion.stage == STAGE_PRODUCTION)
        .order_by(ModelVersion.version.desc())
        .limit(1)
    )
    record = db.execute(stmt).scalar_one_or_none()
    cache.set_json(pointer_key, record.id if record else "", ttl=_POINTER_TTL)
    return record


def latest_record(db: Session, name: str) -> ModelVersion | None:
    stmt = (
        select(ModelVersion)
        .where(ModelVersion.name == name)
        .order_by(ModelVersion.version.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def next_version(db: Session, name: str) -> int:
    record = latest_record(db, name)
    return (record.version + 1) if record else 1


def get_production_bundle(db: Session, name: str) -> ModelBundle | None:
    """Load (and memoise) the production bundle for ``name``."""
    record = production_record(db, name)
    if record is None:
        return None
    with _lock:
        cached = _bundle_cache.get(record.id)
        if cached is not None:
            return cached
        try:
            bundle = load_bundle(record)
        except Exception as exc:
            logger.error(
                "model_load_failed", extra={"model": name, "tag": record.tag, "error": str(exc)}
            )
            return None
        _bundle_cache[record.id] = bundle
        logger.info("model_loaded", extra={"model": name, "tag": record.tag})
        return bundle


def invalidate_cache(model_version_id: str | None = None) -> None:
    cache.invalidate("model:production:")
    with _lock:
        if model_version_id:
            _bundle_cache.pop(model_version_id, None)
        else:
            _bundle_cache.clear()


def register(
    db: Session,
    *,
    name: str,
    tag: str,
    version: int,
    algorithm: str,
    task: str,
    artifact_path: str,
    feature_names: list[str],
    hyperparameters: dict[str, Any],
    metrics: dict[str, Any],
    threshold: float,
    training_rows: int,
    validation_rows: int,
    test_rows: int,
    positive_rate: float,
    training_seconds: float,
    trained_by: str,
    training_window: tuple[datetime | None, datetime | None] = (None, None),
    baseline_stats: dict[str, Any] | None = None,
    mlflow_run_id: str | None = None,
    mlflow_model_uri: str | None = None,
    stage: str = STAGE_STAGING,
    notes: str = "",
) -> ModelVersion:
    record = ModelVersion(
        id=new_id("MV"),
        name=name,
        version=version,
        tag=tag,
        algorithm=algorithm,
        task=task,
        stage=stage,
        status="READY",
        artifact_path=artifact_path,
        feature_names=feature_names,
        hyperparameters=hyperparameters,
        metrics=metrics,
        threshold=threshold,
        training_rows=training_rows,
        validation_rows=validation_rows,
        test_rows=test_rows,
        positive_rate=positive_rate,
        training_seconds=training_seconds,
        trained_at=utcnow(),
        trained_by=trained_by,
        training_window_start=training_window[0],
        training_window_end=training_window[1],
        baseline_stats=baseline_stats or {},
        mlflow_run_id=mlflow_run_id,
        mlflow_model_uri=mlflow_model_uri,
        notes=notes,
    )
    db.add(record)
    db.flush()
    record_metrics(db, record, metrics, window="offline")
    return record


def record_metrics(
    db: Session, record: ModelVersion, metrics: dict[str, Any], *, window: str = "offline"
) -> None:
    thresholds = {
        "roc_auc": (0.80, 0.70),
        "pr_auc": (0.45, 0.30),
        "recall": (0.70, 0.55),
        "precision": (0.40, 0.25),
    }
    now = utcnow()
    for key, value in metrics.items():
        if not isinstance(value, (int, float)):
            continue
        warn, crit = thresholds.get(key, (None, None))
        status = "HEALTHY"
        if warn is not None and crit is not None:
            if value < crit:
                status = "CRITICAL"
            elif value < warn:
                status = "WARNING"
        db.add(
            ModelMetric(
                id=new_id("MM"),
                model_version_id=record.id,
                recorded_at=now,
                window=window,
                metric_name=key,
                metric_value=float(value),
                threshold_warning=warn,
                threshold_critical=crit,
                status=status,
            )
        )


def promote(db: Session, model_version_id: str, *, actor: str) -> ModelVersion:
    """Promote a version to production, archiving the incumbent."""
    record = db.get(ModelVersion, model_version_id)
    if record is None:
        raise NotFoundError(f"Model version {model_version_id} was not found.")
    incumbent = production_record(db, record.name)
    if incumbent and incumbent.id != record.id:
        incumbent.stage = STAGE_ARCHIVED
        invalidate_cache(incumbent.id)
    record.stage = STAGE_PRODUCTION
    record.promoted_at = utcnow()
    record.promoted_by = actor
    invalidate_cache(record.id)
    db.flush()
    logger.info(
        "model_promoted",
        extra={
            "model": record.name,
            "tag": record.tag,
            "actor": actor,
            "previous": incumbent.tag if incumbent else None,
        },
    )
    return record


def rollback(db: Session, name: str, *, actor: str) -> ModelVersion:
    """Return the most recently archived version to production."""
    stmt = (
        select(ModelVersion)
        .where(ModelVersion.name == name, ModelVersion.stage == STAGE_ARCHIVED)
        .order_by(ModelVersion.version.desc())
        .limit(1)
    )
    candidate = db.execute(stmt).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError(f"No archived version of {name} is available to roll back to.")
    return promote(db, candidate.id, actor=actor)
