"""Model training pipeline.

    dataset -> validation -> time-aware split -> train -> tune threshold
            -> evaluate -> SHAP baseline -> MLflow -> registry -> promote

Two properties matter more than the algorithm choice:

* **No leakage.** Features come from the feature store, where every value was
  computed from data strictly older than its own transaction, and the split is
  chronological -- never random -- so the test set is genuinely "the future".
* **Cost-aware thresholds.** The operating point is chosen by minimising
  expected business cost on the validation window, not by defaulting to 0.5.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models.core import Customer, Transaction, TransactionFeature
from app.db.models.mlops import FeedbackLabel, TrainingRun
from app.events.bus import event_bus
from app.events.schemas import Topic, make_event
from app.ml import registry
from app.ml.explain import reset_explainers
from app.services.decision import DecisionPolicy, optimise_threshold
from app.services.features import FEATURE_NAMES
from app.utils import safe_float

logger = get_logger(__name__)

MIN_ROWS = 400
MIN_POSITIVES = 25
TRAIN_FRACTION = 0.70
VALIDATION_FRACTION = 0.15


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    amounts: np.ndarray
    timestamps: list[datetime]
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))

    def __len__(self) -> int:
        return int(self.X.shape[0])

    @property
    def positive_rate(self) -> float:
        return float(self.y.mean()) if len(self) else 0.0


def build_dataset(db: Session, *, limit: int = 200_000) -> Dataset:
    """Assemble the labelled training set from the feature store."""
    rows = list(
        db.execute(
            select(
                TransactionFeature.features,
                Transaction.is_fraud,
                Transaction.amount,
                Transaction.occurred_at,
            )
            .join(Transaction, Transaction.id == TransactionFeature.transaction_id)
            .where(Transaction.is_fraud.isnot(None))
            .order_by(Transaction.occurred_at.asc())
            .limit(limit)
        )
    )
    if not rows:
        raise ValidationError(
            "No labelled transactions are available to train on.", code="NO_TRAINING_DATA"
        )

    feature_names = list(FEATURE_NAMES)
    X = np.array(
        [
            [safe_float((features or {}).get(name, 0.0)) for name in feature_names]
            for features, *_ in rows
        ],
        dtype=float,
    )
    y = np.array([1 if is_fraud else 0 for _, is_fraud, _, _ in rows], dtype=int)
    amounts = np.array([safe_float(amount) for *_, amount, _ in rows], dtype=float)
    timestamps = [occurred_at for *_, occurred_at in rows]
    return Dataset(X=X, y=y, amounts=amounts, timestamps=timestamps, feature_names=feature_names)


def time_split(dataset: Dataset) -> tuple[slice, slice, slice]:
    """Chronological 70/15/15 split -- the rows are already time ordered."""
    total = len(dataset)
    train_end = int(total * TRAIN_FRACTION)
    val_end = int(total * (TRAIN_FRACTION + VALIDATION_FRACTION))
    return slice(0, train_end), slice(train_end, val_end), slice(val_end, total)


def _metrics(y_true: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, Any]:
    from sklearn.metrics import (
        average_precision_score,
        confusion_matrix,
        precision_recall_curve,
        roc_auc_score,
        roc_curve,
    )

    predictions = (probabilities >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, predictions, labels=[0, 1]).ravel()
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / max(len(y_true), 1)

    result: dict[str, Any] = {
        "threshold": round(float(threshold), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "accuracy": round(float(accuracy), 4),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "true_negatives": int(tn),
        "false_negatives": int(fn),
        "positives": int(y_true.sum()),
        "rows": len(y_true),
    }

    # AUCs are undefined with a single class present.
    if len(set(y_true.tolist())) > 1:
        result["roc_auc"] = round(float(roc_auc_score(y_true, probabilities)), 4)
        result["pr_auc"] = round(float(average_precision_score(y_true, probabilities)), 4)
        fpr, tpr, _ = roc_curve(y_true, probabilities)
        step = max(len(fpr) // 60, 1)
        result["roc_curve"] = [
            {"fpr": round(float(a), 4), "tpr": round(float(b), 4)}
            for a, b in zip(fpr[::step], tpr[::step])
        ]
        prec, rec, _ = precision_recall_curve(y_true, probabilities)
        step = max(len(prec) // 60, 1)
        result["pr_curve"] = [
            {"precision": round(float(a), 4), "recall": round(float(b), 4)}
            for a, b in zip(prec[::step], rec[::step])
        ]
    else:
        result["roc_auc"] = 0.0
        result["pr_auc"] = 0.0
    return result


def _baseline_stats(X: np.ndarray, feature_names: Sequence[str]) -> dict[str, Any]:
    """Per-feature distribution snapshot: the reference for drift and fallback explanations."""
    stats: dict[str, Any] = {}
    for index, name in enumerate(feature_names):
        column = X[:, index]
        stats[name] = {
            "mean": round(float(np.mean(column)), 6),
            "std": round(float(np.std(column)) or 1.0, 6),
            "p05": round(float(np.percentile(column, 5)), 6),
            "p50": round(float(np.percentile(column, 50)), 6),
            "p95": round(float(np.percentile(column, 95)), 6),
            "min": round(float(np.min(column)), 6),
            "max": round(float(np.max(column)), 6),
        }
    return stats


def _mlflow_run(
    name: str, params: dict[str, Any], metrics: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Log to MLflow when a tracking server is configured; never fail training."""
    if not settings.mlflow_tracking_uri:
        return None, None
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment)
        with mlflow.start_run(run_name=name) as run:
            mlflow.log_params(
                {k: v for k, v in params.items() if isinstance(v, (int, float, str, bool))}
            )
            mlflow.log_metrics(
                {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}
            )
            return run.info.run_id, f"runs:/{run.info.run_id}/model"
    except Exception as exc:
        logger.warning("mlflow_logging_failed", extra={"error": str(exc)})
        return None, None


def _start_run(db: Session, *, model_name: str, algorithm: str, triggered_by: str) -> TrainingRun:
    run = TrainingRun(
        id=new_id("TR"),
        experiment=settings.mlflow_experiment,
        run_name=f"{model_name}-{utcnow().strftime('%Y%m%d-%H%M%S')}",
        model_name=model_name,
        algorithm=algorithm,
        status="RUNNING",
        started_at=utcnow(),
        triggered_by=triggered_by,
    )
    db.add(run)
    db.flush()
    return run


def _finish_run(
    run: TrainingRun,
    *,
    status: str,
    metrics: dict[str, Any],
    parameters: dict[str, Any],
    rows: int,
    model_version_id: str | None = None,
    mlflow_run_id: str | None = None,
    error: str | None = None,
) -> None:
    run.status = status
    run.finished_at = utcnow()
    run.duration_seconds = round(
        (run.finished_at - run.started_at.replace(tzinfo=run.finished_at.tzinfo)).total_seconds(), 3
    )
    run.metrics = {k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}
    run.parameters = parameters
    run.dataset_rows = rows
    run.model_version_id = model_version_id
    run.mlflow_run_id = mlflow_run_id
    run.error = error


# --------------------------------------------------------------- fraud model


def train_fraud_model(
    db: Session,
    *,
    triggered_by: str = "manual",
    promote: bool = True,
    policy: DecisionPolicy | None = None,
) -> dict[str, Any]:
    """Train, evaluate, register and (optionally) promote the fraud classifier."""
    started = time.perf_counter()
    dataset = build_dataset(db)
    if len(dataset) < MIN_ROWS or int(dataset.y.sum()) < MIN_POSITIVES:
        raise ValidationError(
            f"Not enough labelled data to train: {len(dataset)} rows / "
            f"{int(dataset.y.sum())} fraud cases (need {MIN_ROWS}/{MIN_POSITIVES}).",
            code="INSUFFICIENT_TRAINING_DATA",
        )

    train_idx, val_idx, test_idx = time_split(dataset)
    X_train, y_train = dataset.X[train_idx], dataset.y[train_idx]
    X_val, y_val = dataset.X[val_idx], dataset.y[val_idx]
    X_test, y_test = dataset.X[test_idx], dataset.y[test_idx]

    run = _start_run(
        db, model_name=registry.FRAUD_MODEL, algorithm="xgboost", triggered_by=triggered_by
    )

    positives = max(int(y_train.sum()), 1)
    negatives = max(len(y_train) - positives, 1)
    params: dict[str, Any] = {
        "n_estimators": 320,
        "max_depth": 6,
        "learning_rate": 0.08,
        "subsample": 0.9,
        "colsample_bytree": 0.85,
        "min_child_weight": 2,
        "gamma": 0.1,
        "reg_lambda": 1.4,
        "scale_pos_weight": round(negatives / positives, 3),  # class imbalance
        "eval_metric": "aucpr",
        "tree_method": "hist",
        "random_state": settings.seed_random_state,
        "n_jobs": 4,
    }

    try:
        from xgboost import XGBClassifier

        estimator = XGBClassifier(**params)
        algorithm = "xgboost"
    except ImportError:  # pragma: no cover - xgboost is a declared dependency
        from sklearn.ensemble import GradientBoostingClassifier

        params = {"n_estimators": 200, "max_depth": 3, "learning_rate": 0.1}
        estimator = GradientBoostingClassifier(**params)
        algorithm = "sklearn_gbdt"

    estimator.fit(X_train, y_train)

    val_probabilities = estimator.predict_proba(X_val)[:, 1]
    test_probabilities = estimator.predict_proba(X_test)[:, 1]

    # Choose the operating point on validation by expected business cost.
    policy = policy or DecisionPolicy()
    samples = list(
        zip(
            val_probabilities.tolist(),
            y_val.tolist(),
            dataset.amounts[val_idx].tolist(),
        )
    )
    optimisation = optimise_threshold(samples, policy=policy, steps=40)
    threshold = float(optimisation["optimal"]["threshold"]) if optimisation["optimal"] else 0.5

    validation_metrics = _metrics(y_val, val_probabilities, threshold)
    test_metrics = _metrics(y_test, test_probabilities, threshold)
    training_seconds = time.perf_counter() - started

    metrics = {
        "roc_auc": test_metrics.get("roc_auc", 0.0),
        "pr_auc": test_metrics.get("pr_auc", 0.0),
        "precision": test_metrics["precision"],
        "recall": test_metrics["recall"],
        "f1": test_metrics["f1"],
        "accuracy": test_metrics["accuracy"],
        "threshold": threshold,
        "validation_roc_auc": validation_metrics.get("roc_auc", 0.0),
        "validation_pr_auc": validation_metrics.get("pr_auc", 0.0),
        "expected_cost": optimisation["optimal"]["total_cost"] if optimisation["optimal"] else 0.0,
        "baseline_cost_at_0.5": optimisation.get("baseline_cost", 0.0),
    }

    version = registry.next_version(db, registry.FRAUD_MODEL)
    tag = f"Fraud-XGB-v{version}"
    baseline = _baseline_stats(dataset.X[train_idx], dataset.feature_names)
    artifact = registry.save_artifact(
        registry.FRAUD_MODEL,
        version,
        estimator=estimator,
        feature_names=dataset.feature_names,
        threshold=threshold,
        metadata={
            "algorithm": algorithm,
            "trained_by": triggered_by,
            "confusion_matrix": {
                "true_positives": test_metrics["true_positives"],
                "false_positives": test_metrics["false_positives"],
                "true_negatives": test_metrics["true_negatives"],
                "false_negatives": test_metrics["false_negatives"],
            },
            "roc_curve": test_metrics.get("roc_curve", []),
            "pr_curve": test_metrics.get("pr_curve", []),
            "threshold_curve": optimisation["curve"],
        },
        baseline_stats=baseline,
    )
    mlflow_run_id, model_uri = _mlflow_run(tag, params, metrics)

    record = registry.register(
        db,
        name=registry.FRAUD_MODEL,
        tag=tag,
        version=version,
        algorithm=algorithm,
        task="fraud_classification",
        artifact_path=str(artifact),
        feature_names=dataset.feature_names,
        hyperparameters=params,
        metrics={
            **metrics,
            "confusion_matrix": {
                "true_positives": test_metrics["true_positives"],
                "false_positives": test_metrics["false_positives"],
                "true_negatives": test_metrics["true_negatives"],
                "false_negatives": test_metrics["false_negatives"],
            },
            "roc_curve": test_metrics.get("roc_curve", []),
            "pr_curve": test_metrics.get("pr_curve", []),
            "threshold_curve": optimisation["curve"],
        },
        threshold=threshold,
        training_rows=int(X_train.shape[0]),
        validation_rows=int(X_val.shape[0]),
        test_rows=int(X_test.shape[0]),
        positive_rate=round(dataset.positive_rate, 6),
        training_seconds=round(training_seconds, 3),
        trained_by=triggered_by,
        training_window=(dataset.timestamps[0], dataset.timestamps[-1]),
        baseline_stats=baseline,
        mlflow_run_id=mlflow_run_id,
        mlflow_model_uri=model_uri,
        notes=(
            "Chronological split; threshold chosen by expected-cost minimisation on "
            "the validation window."
        ),
    )
    _finish_run(
        run,
        status="SUCCESS",
        metrics=metrics,
        parameters=params,
        rows=len(dataset),
        model_version_id=record.id,
        mlflow_run_id=mlflow_run_id,
    )

    if promote:
        registry.promote(db, record.id, actor=triggered_by)
    reset_explainers()

    event_bus.publish(
        make_event(
            Topic.MODEL_EVENTS,
            "model.trained",
            {
                "title": f"{tag} trained",
                "body": (
                    f"PR-AUC {metrics['pr_auc']}, recall {metrics['recall']}, "
                    f"precision {metrics['precision']} on the held-out window."
                ),
                "severity": "INFO",
                "model_version_id": record.id,
                "metrics": metrics,
            },
        )
    )
    logger.info("fraud_model_trained", extra={"tag": tag, **metrics})
    return {
        "model": registry.FRAUD_MODEL,
        "tag": tag,
        "version": version,
        "metrics": metrics,
        "rows": len(dataset),
        "positive_rate": round(dataset.positive_rate, 6),
        "promoted": promote,
        "threshold_optimisation": {
            "optimal": optimisation["optimal"],
            "sample_size": optimisation["sample_size"],
        },
    }


# ------------------------------------------------------------- anomaly model


def train_anomaly_model(
    db: Session, *, triggered_by: str = "manual", promote: bool = True
) -> dict[str, Any]:
    """Unsupervised detector for behaviour no labelled example covers."""
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    started = time.perf_counter()
    dataset = build_dataset(db)
    if len(dataset) < MIN_ROWS:
        raise ValidationError(
            "Not enough data to fit the anomaly detector.", code="INSUFFICIENT_TRAINING_DATA"
        )

    run = _start_run(
        db,
        model_name=registry.ANOMALY_MODEL,
        algorithm="isolation_forest",
        triggered_by=triggered_by,
    )

    train_idx, _, test_idx = time_split(dataset)
    # Fit on legitimate traffic only: the model learns "normal", not "fraud".
    legit_mask = dataset.y[train_idx] == 0
    X_legit = dataset.X[train_idx][legit_mask]

    scaler = StandardScaler().fit(X_legit)
    contamination = float(min(max(dataset.positive_rate, 0.005), 0.08))
    params = {
        "n_estimators": 220,
        "contamination": contamination,
        "max_samples": "auto",
        "random_state": settings.seed_random_state,
        "n_jobs": 2,
    }
    estimator = IsolationForest(**params)
    estimator.fit(scaler.transform(X_legit))

    # Report how well the unsupervised score separates the labelled fraud we do
    # have -- an honest sanity check, not a training objective.
    X_test, y_test = dataset.X[test_idx], dataset.y[test_idx]
    scores = -estimator.decision_function(scaler.transform(X_test))
    metrics: dict[str, Any] = {"contamination": round(contamination, 5)}
    if len(set(y_test.tolist())) > 1:
        from sklearn.metrics import average_precision_score, roc_auc_score

        metrics["roc_auc"] = round(float(roc_auc_score(y_test, scores)), 4)
        metrics["pr_auc"] = round(float(average_precision_score(y_test, scores)), 4)

    version = registry.next_version(db, registry.ANOMALY_MODEL)
    tag = f"Anomaly-IF-v{version}"
    artifact = registry.save_artifact(
        registry.ANOMALY_MODEL,
        version,
        estimator=estimator,
        feature_names=dataset.feature_names,
        threshold=0.5,
        metadata={"algorithm": "isolation_forest", "trained_on": "legitimate_only"},
        scaler=scaler,
        baseline_stats=_baseline_stats(X_legit, dataset.feature_names),
    )
    mlflow_run_id, model_uri = _mlflow_run(tag, params, metrics)
    record = registry.register(
        db,
        name=registry.ANOMALY_MODEL,
        tag=tag,
        version=version,
        algorithm="isolation_forest",
        task="anomaly_detection",
        artifact_path=str(artifact),
        feature_names=dataset.feature_names,
        hyperparameters={k: str(v) for k, v in params.items()},
        metrics=metrics,
        threshold=0.5,
        training_rows=int(X_legit.shape[0]),
        validation_rows=0,
        test_rows=int(X_test.shape[0]),
        positive_rate=round(dataset.positive_rate, 6),
        training_seconds=round(time.perf_counter() - started, 3),
        trained_by=triggered_by,
        training_window=(dataset.timestamps[0], dataset.timestamps[-1]),
        mlflow_run_id=mlflow_run_id,
        mlflow_model_uri=model_uri,
        notes="Fitted on legitimate transactions only.",
    )
    _finish_run(
        run,
        status="SUCCESS",
        metrics=metrics,
        parameters={k: str(v) for k, v in params.items()},
        rows=int(X_legit.shape[0]),
        model_version_id=record.id,
        mlflow_run_id=mlflow_run_id,
    )
    if promote:
        registry.promote(db, record.id, actor=triggered_by)
    logger.info("anomaly_model_trained", extra={"tag": tag, **metrics})
    return {"model": registry.ANOMALY_MODEL, "tag": tag, "version": version, "metrics": metrics}


# ------------------------------------------------------- customer risk model

CUSTOMER_RISK_FEATURES = [
    "transaction_count",
    "avg_transaction_amount",
    "std_transaction_amount",
    "max_transaction_amount",
    "lifetime_value",
    "distinct_device_count",
    "tenure_days",
    "chargeback_count",
]


def train_customer_risk_model(
    db: Session, *, triggered_by: str = "manual", promote: bool = True
) -> dict[str, Any]:
    """Customer-level propensity model over the behavioural profile."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    started = time.perf_counter()
    customers = list(db.execute(select(Customer)).scalars())
    rows = [
        [safe_float(getattr(customer, name, 0.0)) for name in CUSTOMER_RISK_FEATURES]
        for customer in customers
    ]
    labels = [1 if (customer.confirmed_fraud_count or 0) > 0 else 0 for customer in customers]

    if len(rows) < 100 or sum(labels) < 10:
        raise ValidationError(
            "Not enough customer-level labels to fit the customer risk model.",
            code="INSUFFICIENT_TRAINING_DATA",
        )

    run = _start_run(
        db,
        model_name=registry.CUSTOMER_RISK_MODEL,
        algorithm="logistic_regression",
        triggered_by=triggered_by,
    )

    X = np.asarray(rows, dtype=float)
    y = np.asarray(labels, dtype=int)
    split = int(len(X) * 0.8)
    X_train, y_train, X_test, y_test = X[:split], y[:split], X[split:], y[split:]

    params = {"C": 0.8, "class_weight": "balanced", "max_iter": 400}
    estimator = Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(**params))])
    estimator.fit(X_train, y_train)

    metrics: dict[str, Any] = {}
    if len(set(y_test.tolist())) > 1:
        from sklearn.metrics import average_precision_score, roc_auc_score

        probabilities = estimator.predict_proba(X_test)[:, 1]
        metrics["roc_auc"] = round(float(roc_auc_score(y_test, probabilities)), 4)
        metrics["pr_auc"] = round(float(average_precision_score(y_test, probabilities)), 4)
    metrics["positive_rate"] = round(float(y.mean()), 5)

    version = registry.next_version(db, registry.CUSTOMER_RISK_MODEL)
    tag = f"CustomerRisk-LR-v{version}"
    artifact = registry.save_artifact(
        registry.CUSTOMER_RISK_MODEL,
        version,
        estimator=estimator,
        feature_names=CUSTOMER_RISK_FEATURES,
        threshold=0.5,
        metadata={"algorithm": "logistic_regression"},
        baseline_stats=_baseline_stats(X_train, CUSTOMER_RISK_FEATURES),
    )
    mlflow_run_id, model_uri = _mlflow_run(tag, params, metrics)
    record = registry.register(
        db,
        name=registry.CUSTOMER_RISK_MODEL,
        tag=tag,
        version=version,
        algorithm="logistic_regression",
        task="customer_risk",
        artifact_path=str(artifact),
        feature_names=CUSTOMER_RISK_FEATURES,
        hyperparameters={k: str(v) for k, v in params.items()},
        metrics=metrics,
        threshold=0.5,
        training_rows=int(X_train.shape[0]),
        validation_rows=0,
        test_rows=int(X_test.shape[0]),
        positive_rate=float(y.mean()),
        training_seconds=round(time.perf_counter() - started, 3),
        trained_by=triggered_by,
        mlflow_run_id=mlflow_run_id,
        mlflow_model_uri=model_uri,
    )
    _finish_run(
        run,
        status="SUCCESS",
        metrics=metrics,
        parameters={k: str(v) for k, v in params.items()},
        rows=len(X),
        model_version_id=record.id,
        mlflow_run_id=mlflow_run_id,
    )
    if promote:
        registry.promote(db, record.id, actor=triggered_by)
    logger.info("customer_risk_model_trained", extra={"tag": tag, **metrics})
    return {
        "model": registry.CUSTOMER_RISK_MODEL,
        "tag": tag,
        "version": version,
        "metrics": metrics,
    }


def train_all(db: Session, *, triggered_by: str = "manual", promote: bool = True) -> dict[str, Any]:
    """Train every model, tolerating individual failures."""
    results: dict[str, Any] = {}
    for name, trainer in (
        ("fraud", train_fraud_model),
        ("anomaly", train_anomaly_model),
        ("customer_risk", train_customer_risk_model),
    ):
        try:
            results[name] = trainer(db, triggered_by=triggered_by, promote=promote)
        except Exception as exc:
            logger.error("training_failed", extra={"model": name, "error": str(exc)})
            results[name] = {"error": str(exc)}
    return results


def retraining_candidates(db: Session) -> dict[str, Any]:
    """How much new analyst-labelled data is waiting for the next training run."""
    unused = int(
        db.execute(
            select(func.count())
            .select_from(FeedbackLabel)
            .where(FeedbackLabel.used_in_training.is_(False))
        ).scalar_one()
        or 0
    )
    confirmed = int(
        db.execute(
            select(func.count())
            .select_from(FeedbackLabel)
            .where(FeedbackLabel.used_in_training.is_(False), FeedbackLabel.label == 1)
        ).scalar_one()
        or 0
    )
    production = registry.production_record(db, registry.FRAUD_MODEL)
    return {
        "pending_labels": unused,
        "pending_confirmed_fraud": confirmed,
        "pending_false_positives": unused - confirmed,
        "threshold_to_retrain": 50,
        "ready": unused >= 50,
        "production_model": production.tag if production else None,
        "production_trained_at": production.trained_at.isoformat() if production else None,
    }
