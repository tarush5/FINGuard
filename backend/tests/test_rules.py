"""Rule engine: grammar validation, evaluation semantics and scoring."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.services import rules as rule_service
from app.services.features import FeatureVector


def namespace(**values: float) -> dict:
    base = {
        "amount": 1000.0,
        "amount_ratio_to_avg": 1.0,
        "txn_count_5m": 0,
        "is_new_device": 0,
        "impossible_travel": 0,
        "device_customer_count": 1,
        "ip_customer_count": 1,
        "merchant_fraud_rate": 0.001,
        "customer_txn_count": 50,
        "is_night": 0,
        "customer_watchlisted": False,
    }
    base.update(values)
    return base


class TestValidation:
    def test_accepts_a_simple_predicate(self) -> None:
        rule_service.validate_condition({"field": "amount", "op": "gt", "value": 100})

    def test_accepts_nested_boolean_logic(self) -> None:
        rule_service.validate_condition(
            {
                "all": [
                    {"field": "amount", "op": "gt", "value": 100},
                    {
                        "any": [
                            {"field": "is_new_device", "op": "is_true"},
                            {"field": "is_night", "op": "is_true"},
                        ]
                    },
                ]
            }
        )

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Unknown rule field"):
            rule_service.validate_condition(
                {"field": "definitely_not_a_field", "op": "gt", "value": 1}
            )

    def test_rejects_unknown_operator(self) -> None:
        with pytest.raises(ValidationError, match="Unknown operator"):
            rule_service.validate_condition({"field": "amount", "op": "regex", "value": ".*"})

    def test_rejects_missing_value(self) -> None:
        with pytest.raises(ValidationError, match="requires 'value'"):
            rule_service.validate_condition({"field": "amount", "op": "gt"})

    def test_rejects_excessive_nesting(self) -> None:
        condition: dict = {"field": "amount", "op": "gt", "value": 1}
        for _ in range(8):
            condition = {"all": [condition]}
        with pytest.raises(ValidationError, match="nests too deeply"):
            rule_service.validate_condition(condition)

    def test_between_requires_two_bounds(self) -> None:
        with pytest.raises(ValidationError, match="two element list"):
            rule_service.validate_condition({"field": "amount", "op": "between", "value": [5]})


class TestEvaluation:
    def test_numeric_comparison(self) -> None:
        assert rule_service.evaluate_condition(
            {"field": "amount", "op": "gt", "value": 500}, namespace()
        )
        assert not rule_service.evaluate_condition(
            {"field": "amount", "op": "gt", "value": 5000}, namespace()
        )

    def test_boolean_operators(self) -> None:
        assert rule_service.evaluate_condition(
            {"field": "is_new_device", "op": "is_false"}, namespace()
        )
        assert rule_service.evaluate_condition(
            {"field": "is_new_device", "op": "is_true"}, namespace(is_new_device=1)
        )

    def test_all_requires_every_branch(self) -> None:
        condition = {
            "all": [
                {"field": "amount", "op": "gt", "value": 500},
                {"field": "txn_count_5m", "op": "gte", "value": 4},
            ]
        }
        assert not rule_service.evaluate_condition(condition, namespace())
        assert rule_service.evaluate_condition(condition, namespace(txn_count_5m=6))

    def test_any_requires_one_branch(self) -> None:
        condition = {
            "any": [
                {"field": "impossible_travel", "op": "is_true"},
                {"field": "device_customer_count", "op": "gte", "value": 3},
            ]
        }
        assert not rule_service.evaluate_condition(condition, namespace())
        assert rule_service.evaluate_condition(condition, namespace(device_customer_count=4))

    def test_not_inverts(self) -> None:
        condition = {"not": {"field": "amount", "op": "gt", "value": 500}}
        assert not rule_service.evaluate_condition(condition, namespace())

    def test_value_ref_with_multiplier(self) -> None:
        # "amount greater than five times the customer average"
        values = namespace(amount=6000.0)
        values["customer_avg_amount"] = 1000.0
        condition = {
            "field": "amount",
            "op": "gt",
            "value_ref": "customer_avg_amount",
            "multiplier": 5,
        }
        assert rule_service.evaluate_condition(condition, values)
        values["amount"] = 4000.0
        assert not rule_service.evaluate_condition(condition, values)

    def test_matched_values_are_recorded(self) -> None:
        matched: dict = {}
        rule_service.evaluate_condition(
            {"field": "amount", "op": "gt", "value": 500}, namespace(), matched
        )
        assert matched["amount"]["actual"] == 1000.0
        assert matched["amount"]["expected"] == 500


class FakeRule:
    """Minimal stand-in for the ORM row the engine consumes."""

    def __init__(
        self, code: str, condition: dict, points: float, action: str = "SCORE", shadow: bool = False
    ):
        self.id = f"RULE-{code}"
        self.code = code
        self.name = code
        self.description = ""
        self.category = "TEST"
        self.severity = "HIGH"
        self.version = 1
        self.condition = condition
        self.risk_points = points
        self.action = action
        self.is_shadow = shadow


class TestScoring:
    def test_points_accumulate_and_cap_at_100(self) -> None:
        rules = [
            FakeRule(f"R{i}", {"field": "amount", "op": "gt", "value": 100}, 40) for i in range(4)
        ]
        result = rule_service.evaluate(namespace(), rules)
        assert len(result.triggered) == 4
        assert result.score == 100.0  # capped

    def test_shadow_rules_are_evaluated_but_not_scored(self) -> None:
        rules = [FakeRule("SHADOW", {"field": "amount", "op": "gt", "value": 100}, 30, shadow=True)]
        result = rule_service.evaluate(namespace(), rules)
        assert len(result.triggered) == 1
        assert result.score == 0.0

    def test_decline_action_escalates(self) -> None:
        rules = [
            FakeRule("REVIEW", {"field": "amount", "op": "gt", "value": 100}, 10, action="REVIEW"),
            FakeRule("BLOCK", {"field": "amount", "op": "gt", "value": 100}, 10, action="DECLINE"),
        ]
        assert rule_service.evaluate(namespace(), rules).forced_action == "DECLINE"

    def test_a_broken_rule_does_not_break_scoring(self) -> None:
        rules = [
            FakeRule("BROKEN", {"nonsense": True}, 25),
            FakeRule("GOOD", {"field": "amount", "op": "gt", "value": 100}, 15),
        ]
        result = rule_service.evaluate(namespace(), rules)
        assert result.score == 15.0
        assert [hit.code for hit in result.triggered] == ["GOOD"]


def test_namespace_is_built_from_the_feature_vector() -> None:
    fv = FeatureVector(
        values={"amount": 250.0, "hour_of_day": 3}, context={"customer_avg_amount": 100.0}
    )
    values = rule_service.build_namespace(fv, {"channel": "WEB"})
    assert values["amount"] == 250.0
    assert values["customer_avg_amount"] == 100.0
    assert values["hour"] == 3
    assert values["channel"] == "WEB"


def test_describe_condition_is_human_readable() -> None:
    text = rule_service.describe_condition(
        {
            "all": [
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 5},
                {"field": "is_new_device", "op": "is_true"},
            ]
        }
    )
    assert "Amount vs customer average > 5" in text
    assert "AND" in text
