"""Rules management, policy simulation and cost-optimal thresholds."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DbSession, PaginationDep, require
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.rbac import Permission
from app.db.base import new_id, utcnow
from app.db.models.core import Transaction, TransactionFeature
from app.db.models.risk import Rule, RuleExecution
from app.services import audit
from app.services import rules as rule_service
from app.services.decision import (
    DEFAULT_POLICY,
    DecisionPolicy,
    decide,
    optimise_threshold,
    policy_from_payload,
)
from app.services.features import FEATURE_LABELS
from app.services.risk import DEFAULT_WEIGHTS, combine, weights_from_payload
from app.services.serializers import serialize_rule
from app.utils import safe_float

router = APIRouter(tags=["risk"])


class RuleCondition(BaseModel):
    model_config = {"extra": "allow"}


class RuleCreate(BaseModel):
    code: str = Field(min_length=3, max_length=48, pattern=r"^[A-Z0-9\-_.]+$")
    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="BEHAVIOUR", max_length=48)
    severity: str = Field(default="MEDIUM", pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    condition: dict[str, Any]
    risk_points: float = Field(default=10.0, ge=0, le=100)
    action: str = Field(default="SCORE", pattern="^(SCORE|REVIEW|DECLINE|STEP_UP)$")
    priority: int = Field(default=100, ge=1, le=1000)
    is_active: bool = True
    is_shadow: bool = False


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=48)
    severity: str | None = Field(default=None, pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    condition: dict[str, Any] | None = None
    risk_points: float | None = Field(default=None, ge=0, le=100)
    action: str | None = Field(default=None, pattern="^(SCORE|REVIEW|DECLINE|STEP_UP)$")
    priority: int | None = Field(default=None, ge=1, le=1000)
    is_active: bool | None = None
    is_shadow: bool | None = None


class RuleTestRequest(BaseModel):
    """Back-test a candidate condition against real stored feature vectors."""

    condition: dict[str, Any]
    sample_size: int = Field(default=2000, ge=100, le=20000)
    days: int = Field(default=60, ge=1, le=365)


class SimulationRequest(BaseModel):
    approve_below: float | None = Field(default=None, ge=0, le=100)
    step_up_below: float | None = Field(default=None, ge=0, le=100)
    review_below: float | None = Field(default=None, ge=0, le=100)
    weights: dict[str, float] | None = None
    sample_size: int = Field(default=4000, ge=100, le=30000)
    days: int = Field(default=30, ge=1, le=365)


@router.get("/rules", summary="List detection rules")
def list_rules(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RULE_READ))],
    page: PaginationDep,
    category: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Rule).where(Rule.is_deleted.is_(False))
    count_stmt = select(func.count()).select_from(Rule).where(Rule.is_deleted.is_(False))
    if category:
        stmt = stmt.where(Rule.category == category.upper())
        count_stmt = count_stmt.where(Rule.category == category.upper())
    if is_active is not None:
        stmt = stmt.where(Rule.is_active.is_(is_active))
        count_stmt = count_stmt.where(Rule.is_active.is_(is_active))

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(
        stmt.order_by(Rule.priority.asc(), Rule.code.asc()).offset(page.offset).limit(page.limit)
    ).scalars()
    envelope = page.envelope([serialize_rule(rule) for rule in rows], total)
    envelope["available_fields"] = [
        {"field": name, "label": FEATURE_LABELS.get(name, name)}
        for name in sorted(rule_service.ALLOWED_FIELDS)
    ]
    envelope["operators"] = sorted(rule_service.ALLOWED_OPS)
    return envelope


@router.post("/rules", status_code=status.HTTP_201_CREATED, summary="Create a rule")
def create_rule(
    payload: RuleCreate,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RULE_WRITE))],
) -> dict[str, Any]:
    rule_service.validate_condition(payload.condition)
    if db.execute(select(Rule).where(Rule.code == payload.code)).scalar_one_or_none():
        raise ConflictError(f"A rule with code {payload.code} already exists.")

    rule = Rule(
        id=new_id("RULE"),
        code=payload.code,
        name=payload.name,
        description=payload.description,
        category=payload.category.upper(),
        severity=payload.severity,
        condition=payload.condition,
        risk_points=payload.risk_points,
        action=payload.action,
        priority=payload.priority,
        is_active=payload.is_active,
        is_shadow=payload.is_shadow,
        created_by=user.email,
        updated_by=user.email,
    )
    db.add(rule)
    db.flush()
    audit.record(
        db,
        action="rule.created",
        entity_type="RULE",
        entity_id=rule.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        rule_version=f"{rule.code}.v{rule.version}",
        details={"condition": payload.condition, "risk_points": payload.risk_points},
    )
    db.commit()
    return serialize_rule(rule)


@router.get("/rules/{rule_id}", summary="Rule detail with execution history")
def get_rule(
    rule_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RULE_READ))],
) -> dict[str, Any]:
    rule = _get_rule(db, rule_id)
    executions = list(
        db.execute(
            select(RuleExecution)
            .where(RuleExecution.rule_id == rule.id, RuleExecution.triggered.is_(True))
            .order_by(RuleExecution.evaluated_at.desc())
            .limit(25)
        ).scalars()
    )
    daily = db.execute(
        select(func.date(RuleExecution.evaluated_at), func.count())
        .where(RuleExecution.rule_id == rule.id, RuleExecution.triggered.is_(True))
        .group_by(func.date(RuleExecution.evaluated_at))
        .order_by(func.date(RuleExecution.evaluated_at).desc())
        .limit(30)
    ).all()
    return {
        "rule": serialize_rule(rule),
        "recent_executions": [
            {
                "id": ex.id,
                "transaction_id": ex.transaction_id,
                "evaluated_at": ex.evaluated_at.isoformat() if ex.evaluated_at else None,
                "risk_points": safe_float(ex.risk_points),
                "evaluation_ms": safe_float(ex.evaluation_ms),
                "matched_values": ex.matched_values,
            }
            for ex in executions
        ],
        "daily_hits": [{"date": str(day), "hits": int(count)} for day, count in reversed(daily)],
    }


@router.patch("/rules/{rule_id}", summary="Update a rule (bumps its version)")
def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RULE_WRITE))],
) -> dict[str, Any]:
    rule = _get_rule(db, rule_id)
    changes: dict[str, Any] = {}
    for field_name, value in payload.model_dump(exclude_none=True).items():
        if field_name == "condition":
            rule_service.validate_condition(value)
        before = getattr(rule, field_name)
        if before != value:
            changes[field_name] = {"from": before, "to": value}
            setattr(rule, field_name, value)

    if changes:
        rule.version += 1
        rule.updated_by = user.email
        audit.record(
            db,
            action="rule.updated",
            entity_type="RULE",
            entity_id=rule.id,
            actor_id=user.id,
            actor_email=user.email,
            actor_roles=user.roles,
            request=request,
            rule_version=f"{rule.code}.v{rule.version}",
            details={"changes": changes},
        )
    db.commit()
    return serialize_rule(rule)


@router.delete("/rules/{rule_id}", summary="Retire a rule (soft delete)")
def delete_rule(
    rule_id: str,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RULE_WRITE))],
) -> dict[str, Any]:
    rule = _get_rule(db, rule_id)
    rule.soft_delete(user.email)
    rule.is_active = False
    audit.record(
        db,
        action="rule.retired",
        entity_type="RULE",
        entity_id=rule.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        rule_version=f"{rule.code}.v{rule.version}",
    )
    db.commit()
    return {"id": rule.id, "code": rule.code, "retired": True}


@router.post("/rules/test", summary="Back-test a rule condition against real history")
def test_rule(
    payload: RuleTestRequest,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RULE_READ))],
) -> dict[str, Any]:
    """Evaluate a candidate condition over stored feature vectors.

    Reports how often it would have fired and how well it separates the
    transactions later labelled as fraud -- the numbers an analyst needs before
    activating a rule.
    """
    rule_service.validate_condition(payload.condition)
    cutoff = utcnow() - timedelta(days=payload.days)
    rows = list(
        db.execute(
            select(
                TransactionFeature.features,
                Transaction.is_fraud,
                Transaction.amount,
                Transaction.id,
            )
            .join(Transaction, Transaction.id == TransactionFeature.transaction_id)
            .where(Transaction.occurred_at >= cutoff)
            .order_by(Transaction.occurred_at.desc())
            .limit(payload.sample_size)
        )
    )
    if not rows:
        raise ValidationError("No transactions are available in the selected window.")

    hits = 0
    true_positives = 0
    false_positives = 0
    total_fraud = 0
    samples: list[dict[str, Any]] = []

    for features, is_fraud, amount, txn_id in rows:
        if is_fraud:
            total_fraud += 1
        namespace = dict(features or {})
        namespace.setdefault("hour", namespace.get("hour_of_day", 0))
        matched: dict[str, Any] = {}
        if rule_service.evaluate_condition(payload.condition, namespace, matched):
            hits += 1
            if is_fraud:
                true_positives += 1
            else:
                false_positives += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "transaction_id": txn_id,
                        "amount": safe_float(amount),
                        "is_fraud": bool(is_fraud),
                        "matched_values": matched,
                    }
                )

    return {
        "sample_size": len(rows),
        "window_days": payload.days,
        "hits": hits,
        "hit_rate_pct": round(hits / len(rows) * 100, 3),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "fraud_in_sample": total_fraud,
        "precision": round(true_positives / hits, 4) if hits else 0.0,
        "recall": round(true_positives / total_fraud, 4) if total_fraud else 0.0,
        "condition_text": rule_service.describe_condition(payload.condition),
        "sample_matches": samples,
    }


@router.get("/risk/policy", summary="Current decision policy and ensemble weights")
def policy(user: Annotated[Any, Depends(require(Permission.RISK_READ))]) -> dict[str, Any]:
    return {
        "thresholds": {
            "approve_below": DEFAULT_POLICY.approve_below,
            "step_up_below": DEFAULT_POLICY.step_up_below,
            "review_below": DEFAULT_POLICY.review_below,
        },
        "bands": DEFAULT_POLICY.bands(),
        "costs": {
            "false_negative": DEFAULT_POLICY.cost_false_negative,
            "false_positive": DEFAULT_POLICY.cost_false_positive,
            "manual_review": DEFAULT_POLICY.cost_manual_review,
        },
        "weights": DEFAULT_WEIGHTS.to_dict(),
        "policy_version": DEFAULT_POLICY.version,
    }


@router.post("/risk/simulate", summary="What-if simulation of thresholds and weights")
def simulate(
    payload: SimulationRequest,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RISK_SIMULATE))],
) -> dict[str, Any]:
    """Re-decide real history under a candidate policy.

    Every transaction in the window is re-scored with the *same* ensemble and
    decision functions used in production, so the deltas are a genuine replay
    rather than an estimate.
    """
    cutoff = utcnow() - timedelta(days=payload.days)
    rows = list(
        db.execute(
            select(
                Transaction.id,
                Transaction.rule_score,
                Transaction.fraud_probability,
                Transaction.anomaly_score,
                Transaction.graph_risk,
                Transaction.amount,
                Transaction.is_fraud,
                Transaction.decision,
                Transaction.risk_score,
            )
            .where(Transaction.occurred_at >= cutoff)
            .order_by(Transaction.occurred_at.desc())
            .limit(payload.sample_size)
        )
    )
    if not rows:
        raise ValidationError("No transactions are available in the selected window.")

    candidate_policy = policy_from_payload(
        {
            "approve_below": payload.approve_below,
            "step_up_below": payload.step_up_below,
            "review_below": payload.review_below,
        }
    )
    candidate_weights = weights_from_payload(payload.weights)

    baseline = {"APPROVE": 0, "STEP_UP": 0, "MANUAL_REVIEW": 0, "DECLINE": 0}
    candidate = dict(baseline)
    baseline_loss = candidate_loss = 0.0
    baseline_prevented = candidate_prevented = 0.0
    baseline_fp = candidate_fp = 0
    baseline_reviews = candidate_reviews = 0

    for (
        _txn_id,
        rule_score,
        fraud_probability,
        anomaly_score,
        graph_risk,
        amount,
        is_fraud,
        current_decision,
        _current_score,
    ) in rows:
        amount = safe_float(amount)
        baseline.setdefault(current_decision, 0)
        baseline[current_decision] += 1

        assessment = combine(
            rule_score=safe_float(rule_score),
            fraud_probability=safe_float(fraud_probability),
            anomaly_score=safe_float(anomaly_score),
            customer_risk=0.0,
            merchant_risk=0.0,
            graph_risk=safe_float(graph_risk),
            weights=candidate_weights,
        )
        new_decision = decide(assessment.final_score, policy=candidate_policy).outcome
        candidate[new_decision] = candidate.get(new_decision, 0) + 1

        blocked_now = current_decision in {"DECLINE", "MANUAL_REVIEW"}
        blocked_new = new_decision in {"DECLINE", "MANUAL_REVIEW"}
        if is_fraud:
            if blocked_now:
                baseline_prevented += amount
            else:
                baseline_loss += amount
            if blocked_new:
                candidate_prevented += amount
            else:
                candidate_loss += amount
        else:
            baseline_fp += 1 if blocked_now else 0
            candidate_fp += 1 if blocked_new else 0
        baseline_reviews += 1 if current_decision == "MANUAL_REVIEW" else 0
        candidate_reviews += 1 if new_decision == "MANUAL_REVIEW" else 0

    def pct_change(new: float, old: float) -> float:
        if not old:
            return 0.0 if not new else 100.0
        return round((new - old) / old * 100, 2)

    friction_baseline = (
        baseline.get("STEP_UP", 0) + baseline.get("MANUAL_REVIEW", 0) + baseline.get("DECLINE", 0)
    )
    friction_candidate = (
        candidate.get("STEP_UP", 0)
        + candidate.get("MANUAL_REVIEW", 0)
        + candidate.get("DECLINE", 0)
    )

    return {
        "sample_size": len(rows),
        "window_days": payload.days,
        "policy": candidate_policy.to_dict(),
        "weights": candidate_weights.to_dict(),
        "baseline": {
            "decisions": baseline,
            "fraud_loss": round(baseline_loss, 2),
            "prevented_loss": round(baseline_prevented, 2),
            "false_positives": baseline_fp,
            "manual_reviews": baseline_reviews,
        },
        "candidate": {
            "decisions": candidate,
            "fraud_loss": round(candidate_loss, 2),
            "prevented_loss": round(candidate_prevented, 2),
            "false_positives": candidate_fp,
            "manual_reviews": candidate_reviews,
        },
        "impact": {
            "expected_fraud_loss_pct": pct_change(candidate_loss, baseline_loss),
            "false_positives_pct": pct_change(candidate_fp, baseline_fp),
            "manual_reviews_pct": pct_change(candidate_reviews, baseline_reviews),
            "customer_friction_pct": pct_change(friction_candidate, friction_baseline),
            "prevented_loss_pct": pct_change(candidate_prevented, baseline_prevented),
        },
        "note": (
            "Replayed with the stored model probabilities and rule scores; "
            "customer and merchant components are excluded because they are "
            "recomputed continuously and would not be comparable."
        ),
    }


@router.get("/risk/threshold-optimisation", summary="Cost-optimal model threshold")
def threshold_optimisation(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.RISK_SIMULATE))],
    days: Annotated[int, Query(ge=1, le=365)] = 60,
    sample_size: Annotated[int, Query(ge=100, le=40000)] = 8000,
    cost_false_negative: Annotated[float | None, Query(ge=0)] = None,
    cost_false_positive: Annotated[float | None, Query(ge=0)] = None,
    cost_manual_review: Annotated[float | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    """Sweep the model threshold and report expected business cost at each point."""
    cutoff = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(Transaction.fraud_probability, Transaction.is_fraud, Transaction.amount)
        .where(Transaction.occurred_at >= cutoff, Transaction.is_fraud.isnot(None))
        .order_by(Transaction.occurred_at.desc())
        .limit(sample_size)
    ).all()
    samples = [
        (safe_float(probability), 1 if is_fraud else 0, safe_float(amount))
        for probability, is_fraud, amount in rows
    ]
    policy = DecisionPolicy(
        cost_false_negative=(
            cost_false_negative
            if cost_false_negative is not None
            else DEFAULT_POLICY.cost_false_negative
        ),
        cost_false_positive=(
            cost_false_positive
            if cost_false_positive is not None
            else DEFAULT_POLICY.cost_false_positive
        ),
        cost_manual_review=(
            cost_manual_review
            if cost_manual_review is not None
            else DEFAULT_POLICY.cost_manual_review
        ),
    )
    result = optimise_threshold(samples, policy=policy)
    result["window_days"] = days
    return result


def _get_rule(db: DbSession, rule_id: str) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None:
        rule = db.execute(select(Rule).where(Rule.code == rule_id)).scalar_one_or_none()
    if rule is None or rule.is_deleted:
        raise NotFoundError(f"Rule {rule_id} was not found.", code="RULE_NOT_FOUND")
    return rule
