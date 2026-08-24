"""Governance: users, roles, policies and the audit trail."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, PaginationDep, require
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.rbac import ROLE_DESCRIPTIONS, Permission, Role, permissions_for
from app.core.security import hash_password
from app.db.base import new_id, utcnow
from app.db.models.identity import Policy, RoleRecord, User
from app.db.models.platform import AIQuery
from app.services import audit

router = APIRouter(tags=["governance"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=10, max_length=200)
    roles: list[str] = Field(min_length=1)
    department: str | None = Field(default=None, max_length=80)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    roles: list[str] | None = None
    is_active: bool | None = None
    department: str | None = Field(default=None, max_length=80)


class PolicyUpdate(BaseModel):
    enforced: bool | None = None
    config: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=2000)


def _serialize_user(user: User) -> dict[str, Any]:
    roles = user.role_names
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "department": user.department,
        "roles": roles,
        "permissions": sorted(p.value for p in permissions_for(roles)),
        "is_active": user.is_active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users", summary="List platform users")
def list_users(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.GOVERNANCE_READ))],
    page: PaginationDep,
    role: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(User).where(User.is_deleted.is_(False))
    rows = list(db.execute(stmt.order_by(User.full_name)).scalars())
    if role:
        rows = [u for u in rows if role.upper() in u.role_names]
    total = len(rows)
    window = rows[page.offset : page.offset + page.limit]
    return page.envelope([_serialize_user(u) for u in window], total)


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Create a user")
def create_user(
    payload: UserCreate,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.USER_MANAGE))],
) -> dict[str, Any]:
    email = payload.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise ConflictError(f"A user with email {email} already exists.")

    valid_roles = {r.value for r in Role}
    unknown = [r for r in payload.roles if r not in valid_roles]
    if unknown:
        raise ValidationError(f"Unknown role(s): {', '.join(unknown)}.")

    record = User(
        id=new_id("USR"),
        email=email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        department=payload.department,
    )
    record.roles = list(
        db.execute(select(RoleRecord).where(RoleRecord.name.in_(payload.roles))).scalars()
    )
    db.add(record)
    db.flush()
    audit.record(
        db,
        action="user.created",
        entity_type="USER",
        entity_id=record.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"email": email, "roles": payload.roles},
    )
    db.commit()
    return _serialize_user(record)


@router.patch("/users/{user_id}", summary="Update a user's roles or status")
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.USER_MANAGE))],
) -> dict[str, Any]:
    record = db.get(User, user_id)
    if record is None or record.is_deleted:
        raise NotFoundError(f"User {user_id} was not found.", code="USER_NOT_FOUND")

    changes: dict[str, Any] = {}
    if payload.full_name is not None and payload.full_name != record.full_name:
        changes["full_name"] = {"from": record.full_name, "to": payload.full_name}
        record.full_name = payload.full_name
    if payload.department is not None:
        record.department = payload.department
    if payload.is_active is not None and payload.is_active != record.is_active:
        changes["is_active"] = {"from": record.is_active, "to": payload.is_active}
        record.is_active = payload.is_active
    if payload.roles is not None:
        valid_roles = {r.value for r in Role}
        unknown = [r for r in payload.roles if r not in valid_roles]
        if unknown:
            raise ValidationError(f"Unknown role(s): {', '.join(unknown)}.")
        if (
            record.id == user.id
            and Role.ADMIN.value in record.role_names
            and Role.ADMIN.value not in payload.roles
        ):
            raise ValidationError("You cannot remove your own ADMIN role.")
        changes["roles"] = {"from": record.role_names, "to": payload.roles}
        record.roles = list(
            db.execute(select(RoleRecord).where(RoleRecord.name.in_(payload.roles))).scalars()
        )

    if changes:
        audit.record(
            db,
            action="user.updated",
            entity_type="USER",
            entity_id=record.id,
            actor_id=user.id,
            actor_email=user.email,
            actor_roles=user.roles,
            request=request,
            details={"changes": changes},
        )
    db.commit()
    return _serialize_user(record)


@router.delete("/users/{user_id}", summary="Deactivate a user")
def deactivate_user(
    user_id: str,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.USER_MANAGE))],
) -> dict[str, Any]:
    record = db.get(User, user_id)
    if record is None or record.is_deleted:
        raise NotFoundError(f"User {user_id} was not found.", code="USER_NOT_FOUND")
    if record.id == user.id:
        raise ValidationError("You cannot deactivate your own account.")
    record.is_active = False
    record.soft_delete(user.email)
    audit.record(
        db,
        action="user.deactivated",
        entity_type="USER",
        entity_id=record.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
    )
    db.commit()
    return {"id": record.id, "deactivated": True}


@router.get("/governance/roles", summary="Role and permission matrix")
def roles(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.GOVERNANCE_READ))],
) -> dict[str, Any]:
    counts = {
        row[0]: int(row[1])
        for row in db.execute(
            select(RoleRecord.name, func.count(User.id))
            .join(RoleRecord.users, isouter=True)
            .group_by(RoleRecord.name)
        ).all()
    }
    return {
        "roles": [
            {
                "name": role.value,
                "description": ROLE_DESCRIPTIONS.get(role, ""),
                "permissions": sorted(p.value for p in permissions_for([role.value])),
                "user_count": counts.get(role.value, 0),
            }
            for role in Role
        ],
        "permissions": sorted(p.value for p in Permission),
    }


@router.get("/governance/policies", summary="Governance policies in force")
def policies(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.GOVERNANCE_READ))],
) -> dict[str, Any]:
    rows = list(db.execute(select(Policy).order_by(Policy.category, Policy.name)).scalars())
    return {
        "items": [
            {
                "id": p.id,
                "key": p.key,
                "name": p.name,
                "category": p.category,
                "description": p.description,
                "enforced": p.enforced,
                "owner": p.owner,
                "config": p.config or {},
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in rows
        ]
    }


@router.patch("/governance/policies/{key}", summary="Update a governance policy")
def update_policy(
    key: str,
    payload: PolicyUpdate,
    request: Request,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.GOVERNANCE_WRITE))],
) -> dict[str, Any]:
    policy = db.execute(select(Policy).where(Policy.key == key)).scalar_one_or_none()
    if policy is None:
        raise NotFoundError(f"Policy {key} was not found.", code="POLICY_NOT_FOUND")
    changes: dict[str, Any] = {}
    if payload.enforced is not None and payload.enforced != policy.enforced:
        changes["enforced"] = {"from": policy.enforced, "to": payload.enforced}
        policy.enforced = payload.enforced
    if payload.config is not None:
        changes["config"] = {"from": policy.config, "to": payload.config}
        policy.config = payload.config
    if payload.description is not None:
        policy.description = payload.description
    if changes:
        audit.record(
            db,
            action="policy.updated",
            entity_type="POLICY",
            entity_id=policy.key,
            actor_id=user.id,
            actor_email=user.email,
            actor_roles=user.roles,
            request=request,
            details={"changes": changes},
        )
    db.commit()
    return {"key": policy.key, "enforced": policy.enforced, "config": policy.config}


@router.get("/audit", summary="Audit trail")
def audit_log(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.AUDIT_READ))],
    page: PaginationDep,
    action: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    rows, total = audit.query(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        limit=page.limit,
        offset=page.offset,
    )
    envelope = page.envelope([audit.serialize(row) for row in rows], total)
    envelope["actions"] = [
        row[0]
        for row in db.execute(
            select(audit.AuditLog.action).distinct().order_by(audit.AuditLog.action)
        ).all()
    ]
    return envelope


@router.get("/governance/ai-usage", summary="AI usage statistics for governance review")
def ai_usage(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require(Permission.GOVERNANCE_READ))],
) -> dict[str, Any]:
    by_surface = db.execute(
        select(
            AIQuery.surface, func.count(), func.coalesce(func.avg(AIQuery.latency_ms), 0.0)
        ).group_by(AIQuery.surface)
    ).all()
    blocked = int(
        db.execute(
            select(func.count()).select_from(AIQuery).where(AIQuery.status == "BLOCKED")
        ).scalar_one()
        or 0
    )
    total = int(db.execute(select(func.count()).select_from(AIQuery)).scalar_one() or 0)
    top_users = db.execute(
        select(AIQuery.user_email, func.count())
        .group_by(AIQuery.user_email)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    return {
        "total_queries": total,
        "blocked_queries": blocked,
        "block_rate_pct": round(blocked / total * 100, 2) if total else 0.0,
        "by_surface": [
            {
                "surface": surface,
                "queries": int(count),
                "avg_latency_ms": round(float(latency or 0), 2),
            }
            for surface, count, latency in by_surface
        ],
        "top_users": [{"user": email, "queries": int(count)} for email, count in top_users],
        "generated_at": utcnow().isoformat(),
    }
