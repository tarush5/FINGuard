"""Decision engine and cost-sensitive threshold analysis.

Thresholds are configuration, never literals in the decision path.  The same
:func:`decide` function powers live decisioning, the What-If simulator and the
back-test, so a simulation result is a genuine replay of production logic.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.config import settings
from app.utils import clamp, safe_float

APPROVE = "APPROVE"
STEP_UP = "STEP_UP"
REVIEW = "MANUAL_REVIEW"
DECLINE = "DECLINE"

OUTCOMES = (APPROVE, STEP_UP, REVIEW, DECLINE)


@dataclass(frozen=True)
class DecisionPolicy:
    """Score bands and business costs that define the operating point."""

    approve_below: float = settings.decision_approve_below
    step_up_below: float = settings.decision_stepup_below
    review_below: float = settings.decision_review_below
    cost_false_negative: float = settings.cost_false_negative
    cost_false_positive: float = settings.cost_false_positive
    cost_manual_review: float = settings.cost_manual_review
    version: str = "policy-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def bands(self) -> dict[str, str]:
        return {
            APPROVE: f"< {self.approve_below}",
            STEP_UP: f"{self.approve_below} - {self.step_up_below}",
            REVIEW: f"{self.step_up_below} - {self.review_below}",
            DECLINE: f">= {self.review_below}",
        }


DEFAULT_POLICY = DecisionPolicy()


@dataclass
class DecisionResult:
    outcome: str
    risk_score: float
    reason: str
    reason_codes: list[str] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)
    policy_version: str = DEFAULT_POLICY.version
    forced_by_rule: str | None = None
    requires_case: bool = False
    requires_alert: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.outcome,
            "risk_score": self.risk_score,
            "reason": self.reason,
            "reason_codes": self.reason_codes,
            "thresholds": self.thresholds,
            "policy_version": self.policy_version,
            "forced_by_rule": self.forced_by_rule,
            "requires_case": self.requires_case,
            "requires_alert": self.requires_alert,
        }


def decide(
    risk_score: float,
    *,
    policy: DecisionPolicy | None = None,
    forced_action: str | None = None,
    forced_by: str | None = None,
    triggered_rules: Sequence[dict[str, Any]] = (),
    top_factors: Sequence[dict[str, Any]] = (),
) -> DecisionResult:
    """Map a risk score (plus any rule override) onto an action."""
    policy = policy or DEFAULT_POLICY
    score = clamp(safe_float(risk_score), 0.0, 100.0)

    if score < policy.approve_below:
        outcome = APPROVE
    elif score < policy.step_up_below:
        outcome = STEP_UP
    elif score < policy.review_below:
        outcome = REVIEW
    else:
        outcome = DECLINE

    forced_rule_code = None
    if forced_action in {DECLINE, REVIEW, STEP_UP, "REVIEW"}:
        mapped = REVIEW if forced_action == "REVIEW" else forced_action
        if OUTCOMES.index(mapped) > OUTCOMES.index(outcome):
            outcome = mapped
            forced_rule_code = forced_by

    reason_codes = [f"SCORE_{outcome}"]
    reason_parts = [f"Ensemble risk score {score:.1f}/100 falls in the {outcome} band"]
    if forced_rule_code:
        reason_codes.append(f"RULE_OVERRIDE_{forced_rule_code}")
        reason_parts.append(f"escalated by rule {forced_rule_code}")
    for rule in list(triggered_rules)[:3]:
        code = str(rule.get("code", "RULE"))
        reason_codes.append(f"RULE_{code}")
    for factor in list(top_factors)[:2]:
        label = factor.get("label")
        if label:
            reason_parts.append(str(label).lower())

    return DecisionResult(
        outcome=outcome,
        risk_score=round(score, 2),
        reason="; ".join(reason_parts) + ".",
        reason_codes=reason_codes[:8],
        thresholds={
            "approve_below": policy.approve_below,
            "step_up_below": policy.step_up_below,
            "review_below": policy.review_below,
        },
        policy_version=policy.version,
        forced_by_rule=forced_rule_code,
        requires_case=outcome in {REVIEW, DECLINE},
        requires_alert=outcome in {STEP_UP, REVIEW, DECLINE},
    )


def policy_from_payload(payload: dict[str, Any] | None) -> DecisionPolicy:
    if not payload:
        return DEFAULT_POLICY
    base = asdict(DEFAULT_POLICY)
    for key, value in payload.items():
        if key in base and value is not None:
            base[key] = safe_float(value) if key != "version" else str(value)
    approve = clamp(safe_float(base["approve_below"]), 0.0, 100.0)
    step_up = clamp(safe_float(base["step_up_below"]), approve, 100.0)
    review = clamp(safe_float(base["review_below"]), step_up, 100.0)
    base.update({"approve_below": approve, "step_up_below": step_up, "review_below": review})
    base["version"] = "policy-simulated"
    return DecisionPolicy(**base)


# --------------------------------------------------------------- cost analysis


@dataclass
class CostOutcome:
    threshold: float
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    manual_reviews: int
    fraud_loss: float
    prevented_loss: float
    false_positive_cost: float
    review_cost: float
    total_cost: float
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in data.items()}


def evaluate_threshold(
    samples: Iterable[tuple[float, int, float]],
    threshold: float,
    *,
    policy: DecisionPolicy | None = None,
    review_band: float = 0.6,
) -> CostOutcome:
    """Score one operating point.

    ``samples`` are ``(probability, actual_label, amount)`` triples.  Anything at
    or above ``threshold`` is blocked; the band immediately below it is treated
    as manual review, which is where the review cost comes from.
    """
    policy = policy or DEFAULT_POLICY
    tp = fp = tn = fn = reviews = 0
    fraud_loss = prevented = 0.0

    for probability, label, amount in samples:
        blocked = probability >= threshold
        in_review_band = (not blocked) and probability >= threshold * review_band
        if in_review_band:
            reviews += 1
        if blocked and label == 1:
            tp += 1
            prevented += amount
        elif blocked and label == 0:
            fp += 1
        elif not blocked and label == 1:
            fn += 1
            fraud_loss += amount
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    fp_cost = fp * policy.cost_false_positive
    review_cost = reviews * policy.cost_manual_review
    # Fraud loss uses the observed amount; the configured FN cost is applied when
    # no amount is available (amount == 0), which keeps both views comparable.
    fn_cost = fraud_loss if fraud_loss else fn * policy.cost_false_negative
    total = fn_cost + fp_cost + review_cost

    return CostOutcome(
        threshold=round(threshold, 4),
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        manual_reviews=reviews,
        fraud_loss=round(fn_cost, 2),
        prevented_loss=round(prevented, 2),
        false_positive_cost=round(fp_cost, 2),
        review_cost=round(review_cost, 2),
        total_cost=round(total, 2),
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
    )


def optimise_threshold(
    samples: Sequence[tuple[float, int, float]],
    *,
    policy: DecisionPolicy | None = None,
    steps: int = 40,
) -> dict[str, Any]:
    """Sweep thresholds and return the cost-minimising operating point."""
    policy = policy or DEFAULT_POLICY
    if not samples:
        return {"curve": [], "optimal": None, "policy": policy.to_dict(), "sample_size": 0}

    curve = [
        evaluate_threshold(samples, (index + 1) / (steps + 1), policy=policy).to_dict()
        for index in range(steps)
    ]
    optimal = min(curve, key=lambda point: point["total_cost"])
    return {
        "curve": curve,
        "optimal": optimal,
        "policy": policy.to_dict(),
        "sample_size": len(samples),
        "baseline_cost": evaluate_threshold(samples, 0.5, policy=policy).to_dict()["total_cost"],
    }
