"""Configurable rule engine.

Rules are *data*, not code.  A rule's ``condition`` is a small JSON expression
tree evaluated by :func:`evaluate_condition`, which means analysts can author,
edit, test and retire rules from the UI with no deployment, while the engine
keeps a strict allow-list of operators and fields (no ``eval``, no injection
surface).

Grammar::

    condition := {"all": [condition, ...]}
               | {"any": [condition, ...]}
               | {"not": condition}
               | predicate

    predicate := {"field": <name>,
                  "op": gt|gte|lt|lte|eq|ne|in|not_in|between|contains|
                        starts_with|is_true|is_false,
                  "value": <literal>          # or
                  "value_ref": <name>,        # compare against another field
                  "multiplier": <number>}     # applied to value/value_ref
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.db.base import new_id, utcnow
from app.db.models.risk import Rule, RuleExecution
from app.services.features import FEATURE_LABELS, FEATURE_NAMES, FeatureVector
from app.utils import safe_float

# Fields a rule may reference: every feature plus a small set of transaction and
# profile attributes exposed under stable names.
CONTEXT_FIELDS: tuple[str, ...] = (
    "amount",
    "currency",
    "channel",
    "country",
    "city",
    "payment_method",
    "merchant_category",
    "merchant_id",
    "customer_id",
    "device_id",
    "transaction_type",
    "customer_avg_amount",
    "customer_std_amount",
    "customer_max_amount",
    "customer_risk_score",
    "customer_watchlisted",
    "merchant_high_risk",
    "history_size",
    "hour",
)

ALLOWED_FIELDS: frozenset[str] = frozenset(FEATURE_NAMES) | frozenset(CONTEXT_FIELDS)

NUMERIC_OPS = {"gt", "gte", "lt", "lte"}
ALLOWED_OPS = NUMERIC_OPS | {
    "eq",
    "ne",
    "in",
    "not_in",
    "between",
    "contains",
    "starts_with",
    "is_true",
    "is_false",
}
ALLOWED_ACTIONS = {"SCORE", "REVIEW", "DECLINE", "STEP_UP"}
MAX_CONDITION_DEPTH = 6


@dataclass
class RuleHit:
    rule_id: str
    code: str
    name: str
    category: str
    severity: str
    version: int
    risk_points: float
    action: str
    triggered: bool
    matched_values: dict[str, Any] = field(default_factory=dict)
    evaluation_ms: float = 0.0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "severity": self.severity,
            "version": self.version,
            "risk_points": round(self.risk_points, 2),
            "action": self.action,
            "matched_values": self.matched_values,
            "evaluation_ms": round(self.evaluation_ms, 3),
            "description": self.description,
        }


@dataclass
class RuleEvaluation:
    hits: list[RuleHit] = field(default_factory=list)
    evaluated: int = 0
    score: float = 0.0
    forced_action: str | None = None
    evaluation_ms: float = 0.0
    ruleset_version: str = "1"

    @property
    def triggered(self) -> list[RuleHit]:
        return [hit for hit in self.hits if hit.triggered]


def build_namespace(fv: FeatureVector, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flatten features + transaction context into the rule namespace."""
    namespace: dict[str, Any] = dict(fv.values)
    namespace.update(
        {
            "customer_avg_amount": fv.context.get("customer_avg_amount", 0.0),
            "customer_std_amount": fv.context.get("customer_std_amount", 0.0),
            "customer_max_amount": fv.context.get("customer_max_amount", 0.0),
            "merchant_category": fv.context.get("merchant_category", ""),
            "history_size": fv.context.get("history_size", 0),
            "hour": fv.values.get("hour_of_day", 0),
        }
    )
    if extra:
        namespace.update(extra)
    return namespace


def validate_condition(condition: Any, depth: int = 0) -> None:
    """Reject malformed or unsafe conditions before they are ever stored."""
    if depth > MAX_CONDITION_DEPTH:
        raise ValidationError("Rule condition nests too deeply (max 6 levels).")
    if not isinstance(condition, dict):
        raise ValidationError("Rule condition must be an object.")

    for junction in ("all", "any"):
        if junction in condition:
            branches = condition[junction]
            if not isinstance(branches, list) or not branches:
                raise ValidationError(f"'{junction}' must be a non-empty list of conditions.")
            for branch in branches:
                validate_condition(branch, depth + 1)
            return
    if "not" in condition:
        validate_condition(condition["not"], depth + 1)
        return

    field_name = condition.get("field")
    op = condition.get("op")
    if field_name not in ALLOWED_FIELDS:
        raise ValidationError(
            f"Unknown rule field '{field_name}'. Allowed: {', '.join(sorted(ALLOWED_FIELDS))}"
        )
    if op not in ALLOWED_OPS:
        raise ValidationError(f"Unknown operator '{op}'. Allowed: {', '.join(sorted(ALLOWED_OPS))}")
    if op in {"is_true", "is_false"}:
        return
    if "value" not in condition and "value_ref" not in condition:
        raise ValidationError(f"Operator '{op}' requires 'value' or 'value_ref'.")
    if "value_ref" in condition and condition["value_ref"] not in ALLOWED_FIELDS:
        raise ValidationError(f"Unknown value_ref '{condition['value_ref']}'.")
    if op == "between":
        bounds = condition.get("value")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValidationError("'between' expects value to be a two element list.")
    if op in {"in", "not_in"} and not isinstance(condition.get("value"), (list, tuple)):
        raise ValidationError(f"'{op}' expects value to be a list.")


def _resolve(condition: dict[str, Any], namespace: dict[str, Any]) -> Any:
    if "value_ref" in condition:
        base = namespace.get(condition["value_ref"], 0)
    else:
        base = condition.get("value")
    multiplier = condition.get("multiplier")
    if multiplier is not None and isinstance(base, (int, float)):
        base = safe_float(base) * safe_float(multiplier, 1.0)
    return base


def evaluate_condition(
    condition: dict[str, Any], namespace: dict[str, Any], matched: dict[str, Any] | None = None
) -> bool:
    """Evaluate one condition tree; records the values that matched."""
    matched = matched if matched is not None else {}

    if "all" in condition:
        return all(evaluate_condition(c, namespace, matched) for c in condition["all"])
    if "any" in condition:
        # Evaluate every branch so matched_values captures the full picture.
        results = [evaluate_condition(c, namespace, matched) for c in condition["any"]]
        return any(results)
    if "not" in condition:
        return not evaluate_condition(condition["not"], namespace, matched)

    field_name = condition["field"]
    op = condition["op"]
    actual = namespace.get(field_name)
    expected = _resolve(condition, namespace)

    if op == "is_true":
        result = bool(actual)
    elif op == "is_false":
        result = not bool(actual)
    elif op in NUMERIC_OPS:
        left, right = safe_float(actual), safe_float(expected)
        result = {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]
    elif op == "eq":
        result = actual == expected
    elif op == "ne":
        result = actual != expected
    elif op == "in":
        result = actual in (expected or [])
    elif op == "not_in":
        result = actual not in (expected or [])
    elif op == "between":
        low, high = expected  # validated at write time
        result = safe_float(low) <= safe_float(actual) <= safe_float(high)
    elif op == "contains":
        result = str(expected).lower() in str(actual or "").lower()
    elif op == "starts_with":
        result = str(actual or "").lower().startswith(str(expected).lower())
    else:  # pragma: no cover - guarded by validate_condition
        result = False

    if result:
        matched[field_name] = {
            "actual": actual,
            "operator": op,
            "expected": expected,
            "label": FEATURE_LABELS.get(field_name, field_name.replace("_", " ").title()),
        }
    return result


def active_rules(db: Session, include_shadow: bool = True) -> list[Rule]:
    stmt = (
        select(Rule)
        .where(Rule.is_active.is_(True), Rule.is_deleted.is_(False))
        .order_by(Rule.priority.asc(), Rule.code.asc())
    )
    rules = list(db.execute(stmt).scalars())
    return rules if include_shadow else [r for r in rules if not r.is_shadow]


def evaluate(
    namespace: dict[str, Any], rules: Iterable[Rule], *, cap: float = 100.0
) -> RuleEvaluation:
    """Evaluate every rule against one namespace, accumulating risk points."""
    started = time.perf_counter()
    evaluation = RuleEvaluation()
    versions: list[str] = []

    for rule in rules:
        evaluation.evaluated += 1
        versions.append(f"{rule.code}.v{rule.version}")
        rule_started = time.perf_counter()
        matched: dict[str, Any] = {}
        try:
            triggered = evaluate_condition(rule.condition, namespace, matched)
        except Exception:
            triggered = False
            matched = {"error": "rule evaluation failed"}
        elapsed = (time.perf_counter() - rule_started) * 1000

        hit = RuleHit(
            rule_id=rule.id,
            code=rule.code,
            name=rule.name,
            category=rule.category,
            severity=rule.severity,
            version=rule.version,
            risk_points=float(rule.risk_points) if triggered else 0.0,
            action=rule.action,
            triggered=triggered,
            matched_values=matched,
            evaluation_ms=elapsed,
            description=rule.description,
        )
        evaluation.hits.append(hit)

        if triggered and not rule.is_shadow:
            evaluation.score += float(rule.risk_points)
            if rule.action == "DECLINE":
                evaluation.forced_action = "DECLINE"
            elif rule.action in {"REVIEW", "STEP_UP"} and evaluation.forced_action != "DECLINE":
                evaluation.forced_action = rule.action

    evaluation.score = min(evaluation.score, cap)
    evaluation.evaluation_ms = round((time.perf_counter() - started) * 1000, 3)
    evaluation.ruleset_version = str(hash(tuple(sorted(versions))) % 100_000) if versions else "0"
    return evaluation


def persist_executions(
    db: Session, transaction_id: str, evaluation: RuleEvaluation, *, only_triggered: bool = True
) -> None:
    """Store rule executions (triggered only by default, to bound write volume)."""
    now = utcnow()
    for hit in evaluation.hits:
        if only_triggered and not hit.triggered:
            continue
        db.add(
            RuleExecution(
                id=new_id("RX"),
                rule_id=hit.rule_id,
                rule_code=hit.code,
                rule_version=hit.version,
                transaction_id=transaction_id,
                evaluated_at=now,
                triggered=hit.triggered,
                risk_points=hit.risk_points,
                evaluation_ms=hit.evaluation_ms,
                matched_values=hit.matched_values,
            )
        )


def bump_rule_counters(db: Session, evaluation: RuleEvaluation) -> None:
    now = utcnow()
    for hit in evaluation.triggered:
        rule = db.get(Rule, hit.rule_id)
        if rule:
            rule.hit_count += 1
            rule.last_triggered_at = now


def describe_condition(condition: dict[str, Any]) -> str:
    """Render a condition tree as readable pseudo-code for the UI and audit log."""
    if "all" in condition:
        return " AND ".join(f"({describe_condition(c)})" for c in condition["all"])
    if "any" in condition:
        return " OR ".join(f"({describe_condition(c)})" for c in condition["any"])
    if "not" in condition:
        return f"NOT ({describe_condition(condition['not'])})"

    field_name = condition.get("field", "?")
    label = FEATURE_LABELS.get(field_name, field_name)
    op = condition.get("op", "?")
    symbols = {
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "eq": "=",
        "ne": "!=",
        "in": "in",
        "not_in": "not in",
        "between": "between",
        "contains": "contains",
        "starts_with": "starts with",
        "is_true": "is true",
        "is_false": "is false",
    }
    if op in {"is_true", "is_false"}:
        return f"{label} {symbols[op]}"
    if "value_ref" in condition:
        ref = FEATURE_LABELS.get(condition["value_ref"], condition["value_ref"])
        multiplier = condition.get("multiplier")
        suffix = f" x {multiplier}" if multiplier else ""
        return f"{label} {symbols.get(op, op)} {ref}{suffix}"
    return f"{label} {symbols.get(op, op)} {condition.get('value')}"
