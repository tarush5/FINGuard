"""Per-prediction explanations.

SHAP is the primary explainer.  Tree SHAP gives exact, additive attributions for
the gradient boosted model, which is what makes the "why was this flagged"
answer defensible to an auditor rather than a plausible-sounding story.

If SHAP is unavailable or the estimator is not tree based, the explainer falls
back to model feature importance weighted by how far each feature sits from the
training baseline -- and *says so* in ``method``, so no explanation is ever
presented as SHAP when it is not.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.ml.registry import ModelBundle
from app.services.features import FEATURE_LABELS

logger = get_logger(__name__)

_explainer_lock = threading.RLock()
_explainers: dict[str, Any] = {}
_shap_disabled: set[str] = set()


def _get_shap_explainer(bundle: ModelBundle) -> Any | None:
    if bundle.identifier in _shap_disabled:
        return None
    with _explainer_lock:
        cached = _explainers.get(bundle.identifier)
        if cached is not None:
            return cached
        try:
            import shap  # imported lazily: heavy and optional

            explainer = shap.TreeExplainer(bundle.estimator)
            _explainers[bundle.identifier] = explainer
            return explainer
        except Exception as exc:
            logger.warning(
                "shap_explainer_unavailable",
                extra={"model": bundle.identifier, "error": str(exc)},
            )
            _shap_disabled.add(bundle.identifier)
            return None


def _baseline_deviation(bundle: ModelBundle, name: str, value: float) -> float:
    stats = (bundle.baseline_stats or {}).get(name)
    if not stats:
        return 0.0
    mean = float(stats.get("mean", 0.0))
    std = float(stats.get("std", 0.0)) or 1.0
    return float((value - mean) / std)


def explain_prediction(
    bundle: ModelBundle,
    row: Sequence[float],
    *,
    top_k: int = 8,
) -> dict[str, Any]:
    """Return top contributing features for a single prediction."""
    feature_names = list(bundle.feature_names)
    vector = np.asarray(row, dtype=float).reshape(1, -1)

    explainer = _get_shap_explainer(bundle)
    method = "shap"
    contributions: np.ndarray | None = None
    base_value = 0.0

    if explainer is not None:
        try:
            shap_values = explainer.shap_values(vector)
            if isinstance(shap_values, list):  # multiclass
                shap_values = shap_values[-1]
            contributions = np.asarray(shap_values, dtype=float).reshape(-1)
            expected = getattr(explainer, "expected_value", 0.0)
            if isinstance(expected, (list, np.ndarray)):
                expected = float(np.asarray(expected).reshape(-1)[-1])
            base_value = float(expected)
        except Exception as exc:
            logger.warning(
                "shap_explain_failed", extra={"model": bundle.identifier, "error": str(exc)}
            )
            contributions = None

    if contributions is None:
        method = "importance_weighted_deviation"
        importances = getattr(bundle.estimator, "feature_importances_", None)
        if importances is None:
            importances = np.full(len(feature_names), 1.0 / max(len(feature_names), 1))
        importances = np.asarray(importances, dtype=float).reshape(-1)
        deviations = np.array(
            [
                _baseline_deviation(bundle, name, float(vector[0][i]))
                for i, name in enumerate(feature_names)
            ]
        )
        contributions = importances * deviations

    total = float(np.abs(contributions).sum()) or 1.0
    ranked = sorted(
        (
            {
                "feature": name,
                "label": FEATURE_LABELS.get(name, name.replace("_", " ").title()),
                "value": round(float(vector[0][index]), 4),
                "contribution": round(float(contributions[index]), 6),
                "direction": "increases" if contributions[index] > 0 else "decreases",
                "impact_pct": round(abs(float(contributions[index])) / total * 100, 2),
            }
            for index, name in enumerate(feature_names)
            if index < len(contributions)
        ),
        key=lambda item: abs(item["contribution"]),
        reverse=True,
    )

    top = ranked[:top_k]
    return {
        "method": method,
        "model": bundle.identifier,
        "base_value": round(base_value, 6),
        "top_factors": top,
        "risk_increasing": [f for f in top if f["contribution"] > 0][:5],
        "risk_decreasing": [f for f in top if f["contribution"] < 0][:5],
        "total_absolute_contribution": round(total, 6),
    }


def global_importance(bundle: ModelBundle, top_k: int = 20) -> list[dict[str, Any]]:
    """Model-level feature importance for the ML Studio screen."""
    importances = getattr(bundle.estimator, "feature_importances_", None)
    if importances is None:
        return []
    values = np.asarray(importances, dtype=float).reshape(-1)
    total = float(values.sum()) or 1.0
    ranked = sorted(
        (
            {
                "feature": name,
                "label": FEATURE_LABELS.get(name, name.replace("_", " ").title()),
                "importance": round(float(values[index]), 6),
                "importance_pct": round(float(values[index]) / total * 100, 2),
            }
            for index, name in enumerate(bundle.feature_names)
            if index < len(values)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    return ranked[:top_k]


def reset_explainers() -> None:
    with _explainer_lock:
        _explainers.clear()
        _shap_disabled.clear()
