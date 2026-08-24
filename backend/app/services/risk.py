"""Ensemble risk engine.

The final score is a weighted blend of six independent signals.  Weights are
data (not constants buried in code) so the What-If simulator and the policy
screen can re-run the same function with different weights and get a truthful
answer instead of an approximation.

    final = w_rule * rule_score
          + 100 * (w_model * fraud_probability
                 + w_anomaly * anomaly_score
                 + w_customer * customer_risk
                 + w_merchant * merchant_risk
                 + w_graph * graph_risk)

with the weights summing to 1, so the result is directly on a 0-100 scale.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.features import FEATURE_LABELS
from app.utils import clamp, safe_float

RISK_BANDS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


@dataclass(frozen=True)
class RiskWeights:
    rule: float = 0.22
    model: float = 0.40
    anomaly: float = 0.10
    customer: float = 0.08
    merchant: float = 0.06
    graph: float = 0.14

    def normalised(self) -> RiskWeights:
        total = self.rule + self.model + self.anomaly + self.customer + self.merchant + self.graph
        if total <= 0:
            return RiskWeights()
        return RiskWeights(
            rule=self.rule / total,
            model=self.model / total,
            anomaly=self.anomaly / total,
            customer=self.customer / total,
            merchant=self.merchant / total,
            graph=self.graph / total,
        )

    def to_dict(self) -> dict[str, float]:
        return {key: round(value, 4) for key, value in asdict(self).items()}


DEFAULT_WEIGHTS = RiskWeights()


@dataclass
class RiskAssessment:
    final_score: float
    risk_band: str
    components: dict[str, Any] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    top_factors: list[dict[str, Any]] = field(default_factory=list)
    contributions: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_score": self.final_score,
            "risk_band": self.risk_band,
            "components": self.components,
            "weights": self.weights,
            "contributions": self.contributions,
            "top_factors": self.top_factors,
        }


def band_for(score: float) -> str:
    if score >= 85:
        return "CRITICAL"
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def combine(
    *,
    rule_score: float,
    fraud_probability: float,
    anomaly_score: float,
    customer_risk: float,
    merchant_risk: float,
    graph_risk: float,
    weights: RiskWeights | None = None,
    model_factors: list[dict[str, Any]] | None = None,
    triggered_rules: list[dict[str, Any]] | None = None,
    graph_signals: list[dict[str, Any]] | None = None,
) -> RiskAssessment:
    """Blend every signal into a single 0-100 score plus its explanation."""
    w = (weights or DEFAULT_WEIGHTS).normalised()

    rule_score = clamp(safe_float(rule_score), 0.0, 100.0)
    fraud_probability = clamp(safe_float(fraud_probability))
    anomaly_score = clamp(safe_float(anomaly_score))
    customer_risk = clamp(safe_float(customer_risk))
    merchant_risk = clamp(safe_float(merchant_risk))
    graph_risk = clamp(safe_float(graph_risk))

    contributions = {
        "rule": w.rule * rule_score,
        "model": w.model * fraud_probability * 100,
        "anomaly": w.anomaly * anomaly_score * 100,
        "customer": w.customer * customer_risk * 100,
        "merchant": w.merchant * merchant_risk * 100,
        "graph": w.graph * graph_risk * 100,
    }
    final = round(clamp(sum(contributions.values()), 0.0, 100.0), 2)

    components = {
        "rule_score": round(rule_score, 2),
        "fraud_probability": round(fraud_probability, 6),
        "anomaly_score": round(anomaly_score, 6),
        "customer_risk": round(customer_risk, 6),
        "merchant_risk": round(merchant_risk, 6),
        "graph_risk": round(graph_risk, 6),
    }

    return RiskAssessment(
        final_score=final,
        risk_band=band_for(final),
        components=components,
        weights=w.to_dict(),
        contributions={key: round(value, 2) for key, value in contributions.items()},
        top_factors=build_top_factors(
            final,
            contributions,
            model_factors=model_factors or [],
            triggered_rules=triggered_rules or [],
            graph_signals=graph_signals or [],
        ),
    )


def build_top_factors(
    final_score: float,
    contributions: dict[str, float],
    *,
    model_factors: list[dict[str, Any]],
    triggered_rules: list[dict[str, Any]],
    graph_signals: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Unify model, rule and graph evidence into one ranked explanation list.

    Model feature attributions are rescaled so that together they account for the
    model's share of the final score -- the percentages therefore add up against
    the same 0-100 scale the analyst sees.
    """
    factors: list[dict[str, Any]] = []
    total = final_score or 1.0

    model_share = contributions.get("model", 0.0) + contributions.get("anomaly", 0.0)
    positive = [f for f in model_factors if safe_float(f.get("contribution")) > 0]
    positive_total = sum(safe_float(f.get("contribution")) for f in positive) or 1.0
    for factor in positive[:limit]:
        points = model_share * (safe_float(factor.get("contribution")) / positive_total)
        factors.append(
            {
                "source": "MODEL",
                "key": factor.get("feature"),
                "label": factor.get(
                    "label",
                    FEATURE_LABELS.get(str(factor.get("feature")), str(factor.get("feature"))),
                ),
                "value": factor.get("value"),
                "points": round(points, 2),
                "impact_pct": round(points / total * 100, 1),
            }
        )

    rule_total = sum(safe_float(r.get("risk_points")) for r in triggered_rules) or 1.0
    rule_share = contributions.get("rule", 0.0)
    for rule in triggered_rules:
        points = rule_share * (safe_float(rule.get("risk_points")) / rule_total)
        factors.append(
            {
                "source": "RULE",
                "key": rule.get("code"),
                "label": rule.get("name"),
                "value": rule.get("matched_values"),
                "points": round(points, 2),
                "impact_pct": round(points / total * 100, 1),
            }
        )

    graph_total = sum(safe_float(s.get("weight")) for s in graph_signals) or 1.0
    graph_share = contributions.get("graph", 0.0)
    for signal in graph_signals:
        points = graph_share * (safe_float(signal.get("weight")) / graph_total)
        factors.append(
            {
                "source": "GRAPH",
                "key": signal.get("type"),
                "label": signal.get("detail"),
                "value": signal.get("entities"),
                "points": round(points, 2),
                "impact_pct": round(points / total * 100, 1),
            }
        )

    factors.sort(key=lambda item: item["points"], reverse=True)
    return factors[:limit]


def weights_from_payload(payload: dict[str, Any] | None) -> RiskWeights:
    if not payload:
        return DEFAULT_WEIGHTS
    base = asdict(DEFAULT_WEIGHTS)
    for key in base:
        if key in payload and payload[key] is not None:
            base[key] = clamp(safe_float(payload[key]), 0.0, 1.0)
    return RiskWeights(**base)


def customer_risk_band(score_0_100: float) -> str:
    return band_for(clamp(safe_float(score_0_100), 0.0, 100.0))
