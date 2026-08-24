"""Shared API dependencies: authentication, authorisation, paging, filters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.logging import set_actor
from app.core.rbac import Permission, can_view_pii, permissions_for
from app.core.security import decode_token
from app.db.models.identity import User
from app.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[Session, Depends(get_db)]


@dataclass
class CurrentUser:
    """The authenticated principal, resolved once per request."""

    id: str
    email: str
    full_name: str
    roles: list[str]
    permissions: frozenset[Permission]
    can_view_pii: bool
    record: User

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if not self.has(permission):
            raise PermissionDeniedError(
                f"Your roles ({', '.join(self.roles)}) do not include '{permission.value}'.",
                details={"required_permission": permission.value},
            )

    @property
    def mask_pii(self) -> bool:
        return not self.can_view_pii


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Provide a bearer access token.")

    claims = decode_token(credentials.credentials, expected_type="access")
    user = db.get(User, claims.get("sub", ""))
    if user is None or not user.is_active or user.is_deleted:
        raise AuthenticationError("The account for this token is inactive or unknown.")

    roles = user.role_names
    principal = CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        roles=roles,
        permissions=permissions_for(roles),
        can_view_pii=can_view_pii(roles),
        record=user,
    )
    set_actor(user.email)
    request.state.user = principal
    return principal


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require(permission: Permission) -> Callable[..., CurrentUser]:
    """Route dependency factory: ``user = Depends(require(Permission.CASE_WRITE))``."""

    def dependency(user: CurrentUserDep) -> CurrentUser:
        user.require(permission)
        return user

    return dependency


@dataclass
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    def envelope(self, items: list[Any], total: int) -> dict[str, Any]:
        pages = (total + self.page_size - 1) // self.page_size if self.page_size else 0
        return {
            "items": items,
            "pagination": {
                "page": self.page,
                "page_size": self.page_size,
                "total": total,
                "pages": pages,
                "has_next": self.page < pages,
                "has_previous": self.page > 1,
            },
        }


def pagination(
    page: Annotated[int, Query(ge=1, le=10_000, description="1-based page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="Items per page")] = 25,
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


PaginationDep = Annotated[Pagination, Depends(pagination)]


@dataclass
class SortSpec:
    field: str | None
    direction: str

    def apply(self, stmt: Any, allowed: dict[str, Any], default: Any) -> Any:
        column = allowed.get(self.field or "", default)
        return stmt.order_by(column.asc() if self.direction == "asc" else column.desc())


def sorting(
    sort_by: Annotated[str | None, Query(description="Field to sort by")] = None,
    sort_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> SortSpec:
    return SortSpec(field=sort_by, direction=sort_dir)


SortingDep = Annotated[SortSpec, Depends(sorting)]
