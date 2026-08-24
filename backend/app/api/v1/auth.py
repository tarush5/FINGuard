"""Authentication: login, refresh-token rotation, logout, profile."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import CurrentUserDep, DbSession
from app.core.config import settings
from app.core.errors import AuthenticationError
from app.core.rbac import ROLE_DESCRIPTIONS, Role, permissions_for
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    token_fingerprint,
    verify_password,
)
from app.db.base import new_id, utcnow
from app.db.models.identity import RefreshToken, User
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


def _issue_tokens(db: DbSession, user: User, request: Request) -> TokenResponse:
    roles = user.role_names
    access, _, expires = create_token(user.id, token_type="access", roles=roles)
    refresh, jti, refresh_expires = create_token(user.id, token_type="refresh", roles=roles)
    db.add(
        RefreshToken(
            id=new_id("RT"),
            user_id=user.id,
            token_hash=token_fingerprint(refresh),
            jti=jti,
            expires_at=refresh_expires,
            user_agent=request.headers.get("user-agent", "")[:255],
            ip_address=(request.client.host if request.client else None),
        )
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=int((expires - utcnow()).total_seconds()),
        user=_profile(user),
    )


def _profile(user: User) -> dict:
    roles = user.role_names
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "department": user.department,
        "roles": roles,
        "permissions": sorted(p.value for p in permissions_for(roles)),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for tokens")
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    user = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()

    # Uniform failure response: never reveal whether the address exists.
    if user is None or user.is_deleted or not user.is_active:
        audit.record(
            db,
            action="auth.login_failed",
            entity_type="USER",
            actor_email=payload.email,
            status="FAILURE",
            request=request,
            reason="unknown_or_inactive_account",
        )
        db.commit()
        raise AuthenticationError("Email or password is incorrect.")

    if user.locked_until and user.locked_until.replace(tzinfo=utcnow().tzinfo) > utcnow():
        raise AuthenticationError(
            "Account temporarily locked after repeated failed sign-ins. Try again shortly."
        )

    if not verify_password(payload.password, user.hashed_password):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
        audit.record(
            db,
            action="auth.login_failed",
            entity_type="USER",
            entity_id=user.id,
            actor_id=user.id,
            actor_email=user.email,
            status="FAILURE",
            request=request,
            reason="bad_password",
        )
        db.commit()
        raise AuthenticationError("Email or password is incorrect.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    tokens = _issue_tokens(db, user, request)
    audit.record(
        db,
        action="auth.login",
        entity_type="USER",
        entity_id=user.id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.role_names,
        request=request,
    )
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse, summary="Rotate a refresh token")
def refresh(payload: RefreshRequest, request: Request, db: DbSession) -> TokenResponse:
    claims = decode_token(payload.refresh_token, expected_type="refresh")
    fingerprint = token_fingerprint(payload.refresh_token)
    record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == fingerprint)
    ).scalar_one_or_none()

    if record is None or record.revoked_at is not None:
        # Reuse of a revoked token is treated as compromise: kill the family.
        if record is not None:
            db.execute(select(RefreshToken).where(RefreshToken.user_id == record.user_id)).scalars()
            for sibling in db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == record.user_id, RefreshToken.revoked_at.is_(None)
                )
            ).scalars():
                sibling.revoked_at = utcnow()
            db.commit()
        raise AuthenticationError("Refresh token is not valid. Please sign in again.")

    if record.expires_at.replace(tzinfo=utcnow().tzinfo) <= utcnow():
        raise AuthenticationError("Refresh token has expired. Please sign in again.")

    user = db.get(User, claims["sub"])
    if user is None or not user.is_active or user.is_deleted:
        raise AuthenticationError("The account for this token is inactive.")

    record.revoked_at = utcnow()
    tokens = _issue_tokens(db, user, request)
    record.replaced_by = token_fingerprint(tokens.refresh_token)[:64]
    audit.record(
        db,
        action="auth.token_refreshed",
        entity_type="USER",
        entity_id=user.id,
        actor_id=user.id,
        actor_email=user.email,
        request=request,
    )
    db.commit()
    return tokens


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Revoke refresh tokens",
)
def logout(payload: RefreshRequest, request: Request, db: DbSession, user: CurrentUserDep) -> None:
    fingerprint = token_fingerprint(payload.refresh_token)
    record = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == fingerprint, RefreshToken.user_id == user.id
        )
    ).scalar_one_or_none()
    if record is not None:
        record.revoked_at = utcnow()
    audit.record(
        db,
        action="auth.logout",
        entity_type="USER",
        entity_id=user.id,
        actor_id=user.id,
        actor_email=user.email,
        request=request,
    )
    db.commit()


@router.get("/me", summary="Current user profile and effective permissions")
def me(user: CurrentUserDep) -> dict:
    return {
        **_profile(user.record),
        "can_view_pii": user.can_view_pii,
        "platform_mode": settings.platform_mode,
    }


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Change your own password",
)
def change_password(
    payload: PasswordChangeRequest, request: Request, db: DbSession, user: CurrentUserDep
) -> None:
    if not verify_password(payload.current_password, user.record.hashed_password):
        raise AuthenticationError("Current password is incorrect.")
    user.record.hashed_password = hash_password(payload.new_password)
    for token in db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars():
        token.revoked_at = utcnow()
    audit.record(
        db,
        action="auth.password_changed",
        entity_type="USER",
        entity_id=user.id,
        actor_id=user.id,
        actor_email=user.email,
        request=request,
    )
    db.commit()


@router.get("/roles", summary="Role catalogue and the permissions each role grants")
def roles() -> dict:
    return {
        "roles": [
            {
                "name": role.value,
                "description": ROLE_DESCRIPTIONS.get(role, ""),
                "permissions": sorted(p.value for p in permissions_for([role.value])),
            }
            for role in Role
        ]
    }
