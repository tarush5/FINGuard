"""Ensemble risk scoring, decision policy and cost-sensitive thresholds."""

from __future__ import annotations

import pytest

from app.services.decision import (
    APPROVE,
    DECLINE,
    REVIEW,
    STEP_UP,
    DecisionPolicy,
    decide,
    evaluate_threshold,
    optimise_threshold,
    policy_from_payload,
)
from app.services.risk import RiskWeights, band_for, combine, weights_from_payload


class TestEnsemble:
    def test_all_zero_signals_score_zero(self) -> None:
        assessment = combine(
            rule_score=0,
            fraud_probability=0,
            anomaly_score=0,
            customer_risk=0,
            merchant_risk=0,
            graph_risk=0,
        )
        assert assessment.final_score == 0.0
        assert assessment.risk_band == "LOW"

    def test_all_maximum_signals_score_one_hundred(self) -> None:
        assessment = combine(
            rule_score=100,
            fraud_probability=1,
            anomaly_score=1,
            customer_risk=1,
            merchant_risk=1,
            graph_risk=1,
        )
        assert assessment.final_score == 100.0
        assert assessment.risk_band == "CRITICAL"

    def test_contributions_sum_to_the_final_score(self) -> None:
        assessment = combine(
            rule_score=40,
            fraud_probability=0.8,
            anomaly_score=0.5,
            customer_risk=0.3,
            merchant_risk=0.2,
            graph_risk=0.6,
        )
        assert sum(assessment.contributions.values()) == pytest.approx(
            assessment.final_score, abs=0.05
        )

    def test_model_weight_dominates_by_default(self) -> None:
        model_only = combine(
            rule_score=0,
            fraud_probability=1,
            anomaly_score=0,
            customer_risk=0,
            merchant_risk=0,
            graph_risk=0,
        )
        rules_only = combine(
            rule_score=100,
            fraud_probability=0,
            anomaly_score=0,
            customer_risk=0,
            merchant_risk=0,
            graph_risk=0,
        )
        assert model_only.final_score > rules_only.final_score

    def test_custom_weights_change_the_blend(self) -> None:
        weights = RiskWeights(
            rule=0.8, model=0.05, anomaly=0.05, customer=0.04, merchant=0.03, graph=0.03
        )
        assessment = combine(
            rule_score=100,
            fraud_probability=0,
            anomaly_score=0,
            customer_risk=0,
            merchant_risk=0,
            graph_risk=0,
            weights=weights,
        )
        assert assessment.final_score == pytest.approx(80.0, abs=0.5)

    def test_weights_are_normalised(self) -> None:
        weights = RiskWeights(
            rule=2, model=2, anomaly=2, customer=2, merchant=2, graph=2
        ).normalised()
        total = sum(
            [
                weights.rule,
                weights.model,
                weights.anomaly,
                weights.customer,
                weights.merchant,
                weights.graph,
            ]
        )
        assert total == pytest.approx(1.0)

    def test_inputs_are_clamped(self) -> None:
        assessment = combine(
            rule_score=500,
            fraud_probability=9,
            anomaly_score=-2,
            customer_risk=0,
            merchant_risk=0,
            graph_risk=0,
        )
        assert 0 <= assessment.final_score <= 100

    def test_weights_from_payload_falls_back_to_defaults(self) -> None:
        assert weights_from_payload(None).model == RiskWeights().model
        assert weights_from_payload({"model": 0.9}).model == 0.9


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "LOW"),
        (39.9, "LOW"),
        (40, "MEDIUM"),
        (69.9, "MEDIUM"),
        (70, "HIGH"),
        (84.9, "HIGH"),
        (85, "CRITICAL"),
        (100, "CRITICAL"),
    ],
)
def test_risk_bands(score: float, expected: str) -> None:
    assert band_for(score) == expected


class TestDecisionEngine:
    @pytest.mark.parametrize(
        ("score", "outcome"),
        [
            (0, APPROVE),
            (29.9, APPROVE),
            (30, STEP_UP),
            (69.9, STEP_UP),
            (70, REVIEW),
            (84.9, REVIEW),
            (85, DECLINE),
            (100, DECLINE),
        ],
    )
    def test_default_thresholds(self, score: float, outcome: str) -> None:
        assert decide(score).outcome == outcome

    def test_rule_override_escalates_but_never_de_escalates(self) -> None:
        escalated = decide(10, forced_action=DECLINE, forced_by="R-ATO-001")
        assert escalated.outcome == DECLINE
        assert escalated.forced_by_rule == "R-ATO-001"

        # A weaker forced action must not soften a decision the score earned.
        unchanged = decide(95, forced_action=STEP_UP, forced_by="R-X")
        assert unchanged.outcome == DECLINE

    def test_case_and_alert_requirements(self) -> None:
        assert decide(10).requires_case is False
        assert decide(10).requires_alert is False
        assert decide(50).requires_alert is True
        assert decide(75).requires_case is True
        assert decide(95).requires_case is True

    def test_custom_policy(self) -> None:
        policy = DecisionPolicy(approve_below=10, step_up_below=20, review_below=30)
        assert decide(15, policy=policy).outcome == STEP_UP
        assert decide(35, policy=policy).outcome == DECLINE

    def test_policy_from_payload_orders_thresholds(self) -> None:
        # An analyst submitting inverted thresholds must not produce nonsense.
        policy = policy_from_payload({"approve_below": 80, "step_up_below": 20, "review_below": 10})
        assert policy.approve_below <= policy.step_up_below <= policy.review_below

    def test_reason_codes_are_populated(self) -> None:
        result = decide(
            90, triggered_rules=[{"code": "R-VEL-001"}], top_factors=[{"label": "High velocity"}]
        )
        assert any(code.startswith("SCORE_") for code in result.reason_codes)
        assert "RULE_R-VEL-001" in result.reason_codes
        assert "high velocity" in result.reason


class TestCostAnalysis:
    def samples(self) -> list[tuple[float, int, float]]:
        # 10 fraud cases worth 100k each, 90 legitimate transactions.
        fraud = [(0.9, 1, 100_000.0) for _ in range(10)]
        legit = [(0.05, 0, 5_000.0) for _ in range(90)]
        return fraud + legit

    def test_low_threshold_catches_all_fraud(self) -> None:
        outcome = evaluate_threshold(self.samples(), 0.5)
        assert outcome.true_positives == 10
        assert outcome.false_negatives == 0
        assert outcome.recall == 1.0

    def test_high_threshold_misses_fraud_and_costs_more(self) -> None:
        cheap = evaluate_threshold(self.samples(), 0.5)
        expensive = evaluate_threshold(self.samples(), 0.95)
        assert expensive.false_negatives == 10
        assert expensive.fraud_loss > cheap.fraud_loss
        assert expensive.total_cost > cheap.total_cost

    def test_optimiser_finds_the_cheapest_operating_point(self) -> None:
        result = optimise_threshold(self.samples())
        assert result["optimal"] is not None
        costs = [point["total_cost"] for point in result["curve"]]
        assert result["optimal"]["total_cost"] == min(costs)

    def test_optimiser_handles_no_samples(self) -> None:
        result = optimise_threshold([])
        assert result["optimal"] is None
        assert result["sample_size"] == 0
