"""Audit trail.

Every state-changing action records who did what, when, from where and why,
along with the request id, model version and rule version in force at the time.
The table is append-only: nothing in the API updates or deletes an audit row.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_request_id
from app.db.base import new_id, utcnow
from app.db.models.identity import AuditLog


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    actor_roles: list[str] | None = None,
    reason: str | None = None,
    request: Request | None = None,
    model_version: str | None = None,
    rule_version: str | None = None,
    status: str = "SUCCESS",
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        id=new_id("AUD"),
        created_at=utcnow(),
        actor_id=actor_id,
        actor_email=actor_email,
        actor_roles=actor_roles or [],
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        request_id=get_request_id(),
        ip_address=_client_ip(request),
        user_agent=(request.headers.get("user-agent") if request else None),
        model_version=model_version,
        rule_version=rule_version,
        status=status,
        details=details or {},
    )
    db.add(entry)
    return entry


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def serialize(entry: AuditLog) -> dict[str, Any]:
    return {
        "id": entry.id,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "actor_id": entry.actor_id,
        "actor_email": entry.actor_email,
        "actor_roles": entry.actor_roles or [],
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "reason": entry.reason,
        "request_id": entry.request_id,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
        "model_version": entry.model_version,
        "rule_version": entry.rule_version,
        "status": entry.status,
        "details": entry.details or {},
    }


def query(
    db: Session,
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    from sqlalchemy import func

    stmt = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)
    if actor:
        filters.append(AuditLog.actor_email == actor)
    for condition in filters:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = list(
        db.execute(stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)).scalars()
    )
    return rows, total
