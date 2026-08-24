"""Model serving.

Wraps the registry so the decision path never touches artifacts directly, and
provides a *transparent* cold-start scorer for the window before the first model
has been trained.  The cold-start scorer is a documented logistic scorecard, and
every response it produces is tagged ``heuristic-scorecard-v1`` -- it is never
presented as a trained model.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.core import Customer
from app.ml import registry
from app.ml.explain import explain_prediction
from app.services.features import FEATURE_LABELS, FEATURE_NAMES, FeatureVector
from app.utils import clamp, safe_float

logger = get_logger(__name__)

HEURISTIC_TAG = "heuristic-scorecard-v1"
DEFAULT_THRESHOLD = 0.5

# Cold-start scorecard. Weights are log-odds contributions applied to bounded,
# normalised signals; the intercept sets a ~1% base rate to match the portfolio.
SCORECARD_INTERCEPT = -4.6
SCORECARD_WEIGHTS: dict[str, float] = {
    "amount_ratio_to_avg": 0.55,
    "amount_zscore": 0.35,
    "txn_count_5m": 0.40,
    "txn_count_1h": 0.18,
    "impossible_travel": 2.10,
    "country_change": 0.75,
    "is_new_device": 1.05,
    "device_customer_count": 0.45,
    "device_risk": 1.60,
    "merchant_fraud_rate": 2.40,
    "merchant_risk": 1.10,
    "is_new_merchant_for_customer": 0.30,
    "category_mismatch": 0.35,
    "is_night": 0.30,
    "ip_customer_count": 0.35,
    "customer_prior_fraud": 0.60,
    "amount_to_max_ratio": 0.45,
}

# How each raw feature is squashed before the weight is applied.
_NORMALISERS: dict[str, Any] = {
    "amount_ratio_to_avg": lambda v: clamp((v - 1.0) / 6.0, 0.0, 2.0),
    "amount_zscore": lambda v: clamp(v / 4.0, -1.0, 2.0),
    "txn_count_5m": lambda v: clamp(v / 8.0, 0.0, 2.0),
    "txn_count_1h": lambda v: clamp(v / 20.0, 0.0, 2.0),
    "device_customer_count": lambda v: clamp((v - 1) / 4.0, 0.0, 2.0),
    "ip_customer_count": lambda v: clamp((v - 1) / 4.0, 0.0, 2.0),
    "merchant_fraud_rate": lambda v: clamp(v / 0.05, 0.0, 2.0),
    "customer_prior_fraud": lambda v: clamp(v / 2.0, 0.0, 2.0),
    "amount_to_max_ratio": lambda v: clamp((v - 1.0) / 2.0, 0.0, 2.0),
}


@dataclass
class PredictionResult:
    probability: float
    label: int
    threshold: float
    model_name: str
    model_version: str
    method: str
    inference_ms: float
    explanation: dict[str, Any] = field(default_factory=dict)
    is_trained_model: bool = True


@dataclass
class AnomalyResult:
    score: float
    model_version: str
    method: str
    inference_ms: float
    is_trained_model: bool = True


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-clamp(x, -30.0, 30.0)))


def _normalise(name: str, value: float) -> float:
    fn = _NORMALISERS.get(name)
    return float(fn(value)) if fn else clamp(value, 0.0, 1.0)


def heuristic_fraud_probability(fv: FeatureVector) -> tuple[float, list[dict[str, Any]]]:
    """Transparent cold-start scorecard: returns probability and contributions."""
    logit = SCORECARD_INTERCEPT
    contributions: list[dict[str, Any]] = []
    for name, weight in SCORECARD_WEIGHTS.items():
        raw = fv.get(name)
        normalised = _normalise(name, raw)
        contribution = weight * normalised
        logit += contribution
        if abs(contribution) > 1e-9:
            contributions.append(
                {
                    "feature": name,
                    "label": FEATURE_LABELS.get(name, name),
                    "value": round(raw, 4),
                    "contribution": round(contribution, 5),
                    "direction": "increases" if contribution > 0 else "decreases",
                }
            )
    probability = _sigmoid(logit)
    total = sum(abs(c["contribution"]) for c in contributions) or 1.0
    for item in contributions:
        item["impact_pct"] = round(abs(item["contribution"]) / total * 100, 2)
    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return round(probability, 6), contributions[:8]


class ModelService:
    """Stateless facade over the registry; bundles are cached inside it."""

    def predict_fraud(self, db: Session, fv: FeatureVector) -> PredictionResult:
        started = time.perf_counter()
        bundle = registry.get_production_bundle(db, registry.FRAUD_MODEL)

        if bundle is None:
            probability, contributions = heuristic_fraud_probability(fv)
            elapsed = (time.perf_counter() - started) * 1000
            return PredictionResult(
                probability=probability,
                label=int(probability >= DEFAULT_THRESHOLD),
                threshold=DEFAULT_THRESHOLD,
                model_name=registry.FRAUD_MODEL,
                model_version=HEURISTIC_TAG,
                method="logistic_scorecard",
                inference_ms=round(elapsed, 3),
                explanation={
                    "method": "scorecard_contributions",
                    "model": HEURISTIC_TAG,
                    "top_factors": contributions,
                    "risk_increasing": [c for c in contributions if c["contribution"] > 0][:5],
                    "note": "No trained model is in production yet; a documented "
                    "logistic scorecard is being used.",
                },
                is_trained_model=False,
            )

        row = [fv.get(name) for name in bundle.feature_names]
        matrix = np.asarray(row, dtype=float).reshape(1, -1)
        try:
            probability = float(bundle.estimator.predict_proba(matrix)[0][1])
        except Exception as exc:
            logger.error("inference_failed", extra={"model": bundle.identifier, "error": str(exc)})
            probability, contributions = heuristic_fraud_probability(fv)
            elapsed = (time.perf_counter() - started) * 1000
            return PredictionResult(
                probability=probability,
                label=int(probability >= DEFAULT_THRESHOLD),
                threshold=DEFAULT_THRESHOLD,
                model_name=registry.FRAUD_MODEL,
                model_version=f"{HEURISTIC_TAG} (fallback)",
                method="logistic_scorecard_fallback",
                inference_ms=round(elapsed, 3),
                explanation={"method": "scorecard_contributions", "top_factors": contributions},
                is_trained_model=False,
            )

        explanation = explain_prediction(bundle, row)
        elapsed = (time.perf_counter() - started) * 1000
        return PredictionResult(
            probability=round(probability, 6),
            label=int(probability >= bundle.threshold),
            threshold=bundle.threshold,
            model_name=bundle.name,
            model_version=bundle.identifier,
            method=str(bundle.metadata.get("algorithm", "gradient_boosting")),
            inference_ms=round(elapsed, 3),
            explanation=explanation,
        )

    def anomaly_score(self, db: Session, fv: FeatureVector) -> AnomalyResult:
        started = time.perf_counter()
        bundle = registry.get_production_bundle(db, registry.ANOMALY_MODEL)
        if bundle is None:
            score = self._heuristic_anomaly(fv)
            return AnomalyResult(
                score=score,
                model_version=HEURISTIC_TAG,
                method="deviation_norm",
                inference_ms=round((time.perf_counter() - started) * 1000, 3),
                is_trained_model=False,
            )
        row = [[fv.get(name) for name in bundle.feature_names]]
        matrix = np.asarray(row, dtype=float)
        if bundle.scaler is not None:
            matrix = bundle.scaler.transform(matrix)
        try:
            # IsolationForest: lower decision_function == more anomalous.
            raw = float(bundle.estimator.decision_function(matrix)[0])
            score = clamp(0.5 - raw, 0.0, 1.0)
        except Exception as exc:
            logger.error("anomaly_inference_failed", extra={"error": str(exc)})
            score = self._heuristic_anomaly(fv)
        return AnomalyResult(
            score=round(score, 6),
            model_version=bundle.identifier,
            method="isolation_forest",
            inference_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    @staticmethod
    def _heuristic_anomaly(fv: FeatureVector) -> float:
        """Normalised distance of the strongest deviation signals."""
        signals = [
            clamp(abs(fv.get("amount_zscore")) / 5.0),
            clamp(fv.get("txn_count_5m") / 10.0),
            clamp(fv.get("velocity_kmh") / 1200.0),
            float(fv.get("impossible_travel")),
            clamp((fv.get("device_customer_count") - 1) / 5.0),
            clamp(fv.get("hour_deviation") / 12.0),
        ]
        return round(clamp(math.sqrt(sum(s * s for s in signals) / len(signals))), 6)

    def customer_risk(self, db: Session, customer: Customer | None) -> float:
        """Customer-level risk in [0, 1] from the trained model or the profile."""
        if customer is None:
            return 0.35
        bundle = registry.get_production_bundle(db, registry.CUSTOMER_RISK_MODEL)
        if bundle is not None:
            row = [[safe_float(getattr(customer, name, 0.0)) for name in bundle.feature_names]]
            try:
                return round(float(bundle.estimator.predict_proba(np.asarray(row, float))[0][1]), 6)
            except Exception as exc:
                logger.warning("customer_risk_inference_failed", extra={"error": str(exc)})
        return round(clamp(safe_float(customer.risk_score) / 100.0), 6)

    def production_versions(self, db: Session) -> dict[str, str]:
        versions: dict[str, str] = {}
        for name in (registry.FRAUD_MODEL, registry.ANOMALY_MODEL, registry.CUSTOMER_RISK_MODEL):
            record = registry.production_record(db, name)
            versions[name] = record.tag if record else HEURISTIC_TAG
        return versions


model_service = ModelService()


def feature_row(fv: FeatureVector, feature_names: list[str] | None = None) -> list[float]:
    names = feature_names or list(FEATURE_NAMES)
    return [fv.get(name) for name in names]
