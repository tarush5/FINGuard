"""Model registry, experiments, monitoring, drift and the feedback loop."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DbSession, PaginationDep, require
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.rbac import Permission
from app.db.models.core import Transaction
from app.db.models.mlops import (
    FeedbackLabel,
    ModelMetric,
    ModelVersion,
    TrainingRun,
)
from app.ml import drift as drift_service
from app.ml import registry
from app.ml.explain import global_importance
from app.ml.train import retraining_candidates, train_all, train_fraud_model
from app.services import audit
from app.utils import safe_float, utcnow

router = APIRouter(tags=["mlops"])


class TrainRequest(BaseModel):
    model: str = Field(default="fraud", pattern="^(fraud|all)$")
    promote: bool = True


class PromoteRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


def _serialize_model(record: ModelVersion, *, include_curves: bool = False) -> dict[str, Any]:
    metrics = dict(record.metrics or {})
    curves = {
        "roc_curve": metrics.pop("roc_curve", []),
        "pr_curve": metrics.pop("pr_curve", []),
        "threshold_curve": metrics.pop("threshold_curve", []),
        "confusion_matrix": metrics.get("confusion_matrix", {}),
    }
    payload = {
        "id": record.id,
        "name": record.name,
        "version": record.version,
        "tag": record.tag,
        "algorithm": record.algorithm,
        "task": record.task,
        "stage": record.stage,
        "status": record.status,
        "threshold": safe_float(record.threshold),
        "metrics": metrics,
        "hyperparameters": record.hyperparameters or {},
        "feature_count": len(record.feature_names or []),
        "training_rows": record.training_rows,
        "validation_rows": record.validation_rows,
        "test_rows": record.test_rows,
        "positive_rate": safe_float(record.positive_rate),
        "training_seconds": safe_float(record.training_seconds),
        "trained_at": record.trained_at.isoformat() if record.trained_at else None,
        "trained_by": record.trained_by,
        "promoted_at": record.promoted_at.isoformat() if record.promoted_at else None,
        "promoted_by": record.promoted_by,
        "mlflow_run_id": record.mlflow_run_id,
        "notes": record.notes,
    }
    if include_curves:
        payload["curves"] = curves
        payload["feature_names"] = record.feature_names or []
    return payload


@router.get("/models", summary="Model registry")
def list_models(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_READ))],
    name: Annotated[str | None, Query()] = None,
    stage: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(ModelVersion)
    if name:
        stmt = stmt.where(ModelVersion.name == name)
    if stage:
        stmt = stmt.where(ModelVersion.stage == stage.upper())
    rows = list(db.execute(stmt.order_by(ModelVersion.trained_at.desc())).scalars())
    production = {
        model_name: registry.production_record(db, model_name)
        for model_name in (
            registry.FRAUD_MODEL,
            registry.ANOMALY_MODEL,
            registry.CUSTOMER_RISK_MODEL,
        )
    }
    return {
        "items": [_serialize_model(record) for record in rows],
        "production": {
            key: (_serialize_model(value) if value else None) for key, value in production.items()
        },
        "mlflow": {
            "configured": bool(settings.mlflow_tracking_uri),
            "tracking_uri": settings.mlflow_tracking_uri,
            "experiment": settings.mlflow_experiment,
        },
    }


@router.get("/models/{model_version_id}", summary="Model detail with curves")
def get_model(
    model_version_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_READ))],
) -> dict[str, Any]:
    record = db.get(ModelVersion, model_version_id)
    if record is None:
        raise NotFoundError(
            f"Model version {model_version_id} was not found.", code="MODEL_NOT_FOUND"
        )

    metric_rows = list(
        db.execute(
            select(ModelMetric)
            .where(ModelMetric.model_version_id == record.id)
            .order_by(ModelMetric.recorded_at.desc())
            .limit(60)
        ).scalars()
    )
    importance: list[dict[str, Any]] = []
    if record.stage == registry.STAGE_PRODUCTION:
        bundle = registry.get_production_bundle(db, record.name)
        if bundle is not None:
            importance = global_importance(bundle)

    return {
        **_serialize_model(record, include_curves=True),
        "metric_history": [
            {
                "metric": row.metric_name,
                "value": safe_float(row.metric_value),
                "window": row.window,
                "status": row.status,
                "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
            }
            for row in metric_rows
        ],
        "feature_importance": importance,
    }


@router.post("/models/{model_version_id}/promote", summary="Promote a version to production")
def promote(
    model_version_id: str,
    payload: PromoteRequest,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_PROMOTE))],
) -> dict[str, Any]:
    record = registry.promote(db, model_version_id, actor=user.email)
    audit.record(
        db,
        action="model.promoted",
        entity_type="MODEL_VERSION",
        entity_id=record.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        model_version=record.tag,
        reason=payload.reason,
    )
    db.commit()
    return _serialize_model(record)


@router.post("/models/{name}/rollback", summary="Roll back to the previous version")
def rollback(
    name: str,
    payload: PromoteRequest,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_PROMOTE))],
) -> dict[str, Any]:
    record = registry.rollback(db, name, actor=user.email)
    audit.record(
        db,
        action="model.rolled_back",
        entity_type="MODEL_VERSION",
        entity_id=record.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        model_version=record.tag,
        reason=payload.reason,
    )
    db.commit()
    return _serialize_model(record)


@router.get("/models/compare/{left_id}/{right_id}", summary="Compare two model versions")
def compare(
    left_id: str,
    right_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_READ))],
) -> dict[str, Any]:
    left, right = db.get(ModelVersion, left_id), db.get(ModelVersion, right_id)
    if left is None or right is None:
        raise NotFoundError("One or both model versions were not found.", code="MODEL_NOT_FOUND")

    keys = ("roc_auc", "pr_auc", "precision", "recall", "f1", "threshold", "expected_cost")
    comparison = []
    for key in keys:
        left_value = safe_float((left.metrics or {}).get(key))
        right_value = safe_float((right.metrics or {}).get(key))
        comparison.append(
            {
                "metric": key,
                "left": left_value,
                "right": right_value,
                "delta": round(right_value - left_value, 6),
                "better": (
                    "right"
                    if right_value > left_value
                    else "left" if left_value > right_value else "equal"
                ),
            }
        )
    return {
        "left": _serialize_model(left),
        "right": _serialize_model(right),
        "comparison": comparison,
    }


@router.post("/models/train", summary="Trigger a training run")
def train(
    payload: TrainRequest,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_TRAIN))],
) -> dict[str, Any]:
    """Train synchronously and return the metrics.

    Training on the demo dataset takes a few seconds; a production deployment
    would dispatch this to the Celery worker defined in ``app.worker``.
    """
    if payload.model == "all":
        result = train_all(db, triggered_by=user.email, promote=payload.promote)
    else:
        result = {"fraud": train_fraud_model(db, triggered_by=user.email, promote=payload.promote)}
    audit.record(
        db,
        action="model.trained",
        entity_type="MODEL_VERSION",
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"models": list(result.keys()), "promote": payload.promote},
    )
    db.commit()
    return result


@router.get("/experiments", summary="Training run history")
def experiments(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_READ))],
    page: PaginationDep,
) -> dict[str, Any]:
    total = int(db.execute(select(func.count()).select_from(TrainingRun)).scalar_one() or 0)
    rows = db.execute(
        select(TrainingRun)
        .order_by(TrainingRun.started_at.desc())
        .offset(page.offset)
        .limit(page.limit)
    ).scalars()
    return page.envelope(
        [
            {
                "id": run.id,
                "experiment": run.experiment,
                "run_name": run.run_name,
                "model_name": run.model_name,
                "algorithm": run.algorithm,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "duration_seconds": safe_float(run.duration_seconds),
                "metrics": run.metrics or {},
                "parameters": run.parameters or {},
                "dataset_rows": run.dataset_rows,
                "triggered_by": run.triggered_by,
                "mlflow_run_id": run.mlflow_run_id,
                "error": run.error,
            }
            for run in rows
        ],
        total,
    )


@router.get("/monitoring/models", summary="Production model health")
def model_monitoring(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MONITORING_READ))],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict[str, Any]:
    cutoff = utcnow() - timedelta(days=days)
    models = []
    for name in (registry.FRAUD_MODEL, registry.ANOMALY_MODEL, registry.CUSTOMER_RISK_MODEL):
        record = registry.production_record(db, name)
        if record is None:
            models.append(
                {"name": name, "status": "NOT_DEPLOYED", "detail": "No production version."}
            )
            continue
        volume = int(
            db.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.model_version == record.tag, Transaction.occurred_at >= cutoff)
            ).scalar_one()
            or 0
        )
        metrics_dict = record.metrics or {}
        health = "HEALTHY"
        issues = []
        if safe_float(metrics_dict.get("pr_auc")) < 0.30:
            health, _ = "CRITICAL", issues.append("PR-AUC below 0.30")
        elif safe_float(metrics_dict.get("pr_auc")) < 0.45:
            health, _ = "WARNING", issues.append("PR-AUC below 0.45")
        if safe_float(metrics_dict.get("recall")) < 0.55:
            health = "CRITICAL"
            issues.append("Recall below 0.55")
        models.append(
            {
                "name": name,
                "tag": record.tag,
                "stage": record.stage,
                "status": health,
                "issues": issues,
                "metrics": {
                    key: metrics_dict.get(key)
                    for key in ("roc_auc", "pr_auc", "precision", "recall", "f1", "threshold")
                },
                "predictions_in_window": volume,
                "trained_at": record.trained_at.isoformat() if record.trained_at else None,
                "deployed_at": record.promoted_at.isoformat() if record.promoted_at else None,
            }
        )

    latency = db.execute(
        select(func.avg(Transaction.processing_ms)).where(Transaction.occurred_at >= cutoff)
    ).scalar_one_or_none()
    return {
        "window_days": days,
        "models": models,
        "average_decision_ms": round(float(latency or 0), 3),
        "drift": drift_service.latest(db),
        "retraining": retraining_candidates(db),
    }


@router.get("/monitoring/drift", summary="Feature and prediction drift")
def drift(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MONITORING_READ))],
    recompute: Annotated[
        bool, Query(description="Recompute instead of reading the last run")
    ] = False,
    window_days: Annotated[int, Query(ge=1, le=60)] = 7,
) -> dict[str, Any]:
    if recompute:
        result = drift_service.compute_drift(db, window_days=window_days)
        db.commit()
        return result
    return drift_service.latest(db)


@router.get("/feedback", summary="Analyst feedback labels awaiting retraining")
def feedback(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MODEL_READ))],
    page: PaginationDep,
) -> dict[str, Any]:
    total = int(db.execute(select(func.count()).select_from(FeedbackLabel)).scalar_one() or 0)
    rows = db.execute(
        select(FeedbackLabel)
        .order_by(FeedbackLabel.created_at.desc())
        .offset(page.offset)
        .limit(page.limit)
    ).scalars()
    envelope = page.envelope(
        [
            {
                "id": row.id,
                "transaction_id": row.transaction_id,
                "case_id": row.case_id,
                "label": row.label,
                "verdict": row.verdict,
                "analyst_name": row.analyst_name,
                "predicted_probability": safe_float(row.predicted_probability),
                "model_version": row.model_version,
                "used_in_training": row.used_in_training,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        total,
    )
    envelope["retraining"] = retraining_candidates(db)
    return envelope
