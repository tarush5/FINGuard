"""Uniform error envelope.

Every failure leaving the API has the shape::

    {"success": false,
     "error": {"code": "...", "message": "...", "request_id": "...", "details": {...}}}

Internal exceptions are logged with a stack trace but never leak one to the
client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_request_id

logger = get_logger(__name__)


class FinGuardError(Exception):
    """Base class for every expected, user-facing application error."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(FinGuardError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ValidationError(FinGuardError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "VALIDATION_ERROR"


class ConflictError(FinGuardError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class AuthenticationError(FinGuardError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "NOT_AUTHENTICATED"


class PermissionDeniedError(FinGuardError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PERMISSION_DENIED"


class RateLimitError(FinGuardError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"


class UnsafeQueryError(FinGuardError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "UNSAFE_QUERY"


class ServiceUnavailableError(FinGuardError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": get_request_id(),
    }
    if details:
        error["details"] = details
    return {"success": False, "error": error}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FinGuardError)
    async def _finguard(_: Request, exc: FinGuardError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())[1:]) or "body",
                "message": err.get("msg", "invalid value"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload(
                "VALIDATION_ERROR", "Request payload failed validation.", {"fields": fields}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            401: "NOT_AUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            429: "RATE_LIMITED",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception", extra={"path": request.url.path, "kind": type(exc).__name__}
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload(
                "INTERNAL_ERROR", "An unexpected error occurred. The incident was logged."
            ),
        )
