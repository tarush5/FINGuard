"""AI investigator, natural-language analytics and case summarisation.

Every AI call is logged to ``ai_queries`` with the question, the generated SQL,
the outcome and the caller -- the audit trail an AI-governance review asks for.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, PaginationDep, require
from app.core.errors import NotFoundError, UnsafeQueryError
from app.core.rbac import Permission
from app.db.base import new_id, utcnow
from app.db.models.core import Transaction
from app.db.models.platform import AIQuery
from app.services import audit
from app.services import cases as case_service
from app.services.ai import investigator, text_to_sql
from app.services.ai.llm import llm_client

router = APIRouter(prefix="/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600)
    transaction_id: str | None = Field(default=None, max_length=48)
    case_id: str | None = Field(default=None, max_length=40)


class SqlRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600)
    sql: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional analyst-authored SQL; still passes the same guard.",
    )


def _log(
    db: DbSession,
    user: CurrentUser,
    *,
    surface: str,
    question: str,
    status: str,
    latency_ms: float,
    generated_sql: str | None = None,
    sql_valid: bool | None = None,
    blocked_reason: str | None = None,
    row_count: int = 0,
    answer: str | None = None,
    evidence: dict[str, Any] | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    provider: str = "deterministic",
    model: str | None = None,
    intent: str | None = None,
) -> None:
    db.add(
        AIQuery(
            id=new_id("AIQ"),
            created_at=utcnow(),
            user_id=user.id,
            user_email=user.email,
            user_roles=user.roles,
            surface=surface,
            question=question,
            intent=intent,
            provider=provider,
            model=model,
            generated_sql=generated_sql,
            sql_valid=sql_valid,
            blocked_reason=blocked_reason,
            row_count=row_count,
            latency_ms=latency_ms,
            status=status,
            answer=(answer or "")[:8000] or None,
            evidence=evidence or {},
            entity_type=entity_type,
            entity_id=entity_id,
        )
    )


@router.get("/status", summary="AI provider status and guardrails")
def status(user: Annotated[Any, Depends(require(Permission.AI_QUERY))]) -> dict[str, Any]:
    return {
        **llm_client.describe(),
        "guardrails": [
            "Evidence is retrieved from the database before any generation.",
            "Generated SQL must be a single read-only SELECT.",
            "Table access is filtered by the caller's RBAC permissions.",
            "PII columns are blocked for roles without customer:pii_read.",
            "Analyst input is sanitised against prompt injection.",
            "Every AI interaction is logged to the ai_queries table.",
        ],
        "supported_topics": [intent["description"] for intent in text_to_sql.INTENTS],
    }


@router.post("/ask", summary="Ask about a transaction or case, grounded in evidence")
def ask(
    payload: AskRequest,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.AI_QUERY))],
) -> dict[str, Any]:
    started = time.perf_counter()

    transaction: Transaction | None = None
    case = None
    if payload.transaction_id:
        transaction = db.get(Transaction, payload.transaction_id)
        if transaction is None:
            raise NotFoundError(
                f"Transaction {payload.transaction_id} was not found.", code="TRANSACTION_NOT_FOUND"
            )
    elif payload.case_id:
        case = case_service.get_case_or_404(db, payload.case_id)
        if case.primary_transaction_id:
            transaction = db.get(Transaction, case.primary_transaction_id)

    if transaction is None:
        raise NotFoundError(
            "Provide a transaction_id, or a case_id that has a primary transaction.",
            code="AI_CONTEXT_REQUIRED",
        )

    result = investigator.answer_question(db, transaction, payload.question, mask_pii=user.mask_pii)
    _log(
        db,
        user,
        surface="investigator",
        question=payload.question,
        status="SUCCESS",
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        answer=result["answer"],
        evidence={"items": result["evidence"][:20]},
        entity_type="TRANSACTION",
        entity_id=transaction.id,
        provider=result["generated_by"],
        model=(
            str(result["provider"].get("model")) if isinstance(result["provider"], dict) else None
        ),
    )
    db.commit()
    if case is not None:
        result["case_id"] = case.id
    return result


@router.post("/sql", summary="Natural-language analytics (text-to-SQL)")
def sql(
    payload: SqlRequest,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.AI_SQL))],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = text_to_sql.run_query(
            db,
            payload.question,
            permissions=user.permissions,
            allow_pii=user.can_view_pii,
            explicit_sql=payload.sql,
        )
    except UnsafeQueryError as exc:
        _log(
            db,
            user,
            surface="text_to_sql",
            question=payload.question,
            status="BLOCKED",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            generated_sql=str(exc.details.get("sql")) if exc.details else None,
            sql_valid=False,
            blocked_reason=exc.message[:255],
        )
        audit.record(
            db,
            action="ai.sql_blocked",
            entity_type="AI_QUERY",
            actor_id=user.id,
            actor_email=user.email,
            actor_roles=user.roles,
            request=request,
            status="BLOCKED",
            reason=exc.message,
        )
        db.commit()
        raise

    _log(
        db,
        user,
        surface="text_to_sql",
        question=payload.question,
        status=result["status"],
        latency_ms=result.get("latency_ms", 0.0),
        generated_sql=result.get("sql"),
        sql_valid=result["status"] == "OK",
        row_count=result.get("row_count", 0),
        provider=result.get("provider", "deterministic"),
        intent=result.get("source"),
    )
    db.commit()
    return result


@router.post("/cases/{case_id}/summary", summary="Generate (and store) an AI case summary")
def case_summary(
    case_id: str,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.AI_QUERY))],
) -> dict[str, Any]:
    case = case_service.get_case_or_404(db, case_id)
    result = investigator.summarise_case(db, case, mask_pii=user.mask_pii)

    case.ai_summary = result["summary"]
    case.ai_summary_generated_at = utcnow()
    case_service.add_event(
        db,
        case,
        event_type="AI_SUMMARY",
        description="AI summary generated.",
        actor=user.full_name,
        actor_id=user.id,
        payload={"generated_by": result["generated_by"]},
    )
    _log(
        db,
        user,
        surface="case_summary",
        question=f"Summarise case {case.case_number}",
        status="SUCCESS",
        latency_ms=result["latency_ms"],
        answer=result["summary"],
        evidence={"indicators": result["indicators"]},
        entity_type="CASE",
        entity_id=case.id,
        provider=result["generated_by"],
    )
    audit.record(
        db,
        action="ai.case_summarised",
        entity_type="CASE",
        entity_id=case.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"generated_by": result["generated_by"]},
    )
    db.commit()
    return result


@router.post("/cases/{case_id}/report", summary="Generate an investigation report")
def case_report(
    case_id: str,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.AI_QUERY))],
) -> dict[str, Any]:
    """Assemble a complete, evidence-backed report for one case."""
    case = case_service.get_case_or_404(db, case_id)
    detail = case_service.case_detail(db, case, mask_pii=user.mask_pii)
    summary = investigator.summarise_case(db, case, mask_pii=user.mask_pii)

    report = {
        "case": detail["case"],
        "generated_at": utcnow().isoformat(),
        "generated_by": user.full_name,
        "summary": summary["summary"],
        "summary_generated_by": summary["generated_by"],
        "risk": summary["scores"],
        "indicators": summary["indicators"],
        "evidence": summary["evidence"],
        "model_factors": summary["model_factors"],
        "recommended_action": summary["recommended_action"],
        "transaction": detail["transaction"],
        "customer": detail["customer"],
        "merchant": detail["merchant"],
        "timeline": detail["timeline"],
        "notes": detail["notes"],
        "disclaimer": (
            "Evidence is retrieved from the FINGuard database. Narrative sections "
            "are generated from that evidence and are advisory; the investigating "
            "analyst remains the decision maker."
        ),
    }
    audit.record(
        db,
        action="ai.report_generated",
        entity_type="CASE",
        entity_id=case.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
    )
    db.commit()
    return report


@router.get("/queries", summary="AI query log")
def query_log(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.AI_QUERY))],
    page: PaginationDep,
) -> dict[str, Any]:
    """Analysts see their own history; auditors and admins see everything."""
    stmt = select(AIQuery)
    count_stmt = select(func.count()).select_from(AIQuery)
    if not (user.has(Permission.AUDIT_READ) or user.has(Permission.SYSTEM_ADMIN)):
        stmt = stmt.where(AIQuery.user_id == user.id)
        count_stmt = count_stmt.where(AIQuery.user_id == user.id)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(
        stmt.order_by(AIQuery.created_at.desc()).offset(page.offset).limit(page.limit)
    ).scalars()
    return page.envelope(
        [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "user_email": row.user_email,
                "surface": row.surface,
                "question": row.question,
                "intent": row.intent,
                "provider": row.provider,
                "generated_sql": row.generated_sql,
                "sql_valid": row.sql_valid,
                "blocked_reason": row.blocked_reason,
                "row_count": row.row_count,
                "latency_ms": row.latency_ms,
                "status": row.status,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
            }
            for row in rows
        ],
        total,
    )
