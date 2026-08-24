"""Password hashing and JWT issuance / verification."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.errors import AuthenticationError, ValidationError

TokenType = Literal["access", "refresh"]
_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return bcrypt.hashpw(
        password.encode("utf-8")[:72], bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < settings.password_min_length:
        raise ValidationError(
            f"Password must be at least {settings.password_min_length} characters."
        )
    classes = (
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    )
    if sum(classes) < 3:
        raise ValidationError(
            "Password must combine at least three of: lowercase, uppercase, digit, symbol."
        )


def create_token(
    subject: str,
    *,
    token_type: TokenType,
    roles: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str, datetime]:
    """Return a ``(token, jti, expires_at)`` triple."""
    now = datetime.now(UTC)
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    expires = now + ttl
    jti = uuid.uuid4().hex
    claims: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "roles": list(roles or []),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": jti,
        "iss": settings.app_name,
    }
    if extra:
        claims.update(extra)
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti, expires


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
        )
    except JWTError as exc:
        raise AuthenticationError("Token is invalid or has expired.") from exc
    if expected_type and claims.get("type") != expected_type:
        raise AuthenticationError(f"Expected a {expected_type} token.")
    return claims


def token_fingerprint(token: str) -> str:
    """Non-reversible handle used to store/revoke a refresh token server side."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return value
    local, _, domain = value.partition("@")
    keep = local[:1]
    return f"{keep}{'*' * max(len(local) - 1, 3)}@{domain}"


def mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 4:
        return "*" * len(value)
    return "*" * (len(digits) - 4) + "".join(digits[-4:])


def mask_pan(value: str | None) -> str | None:
    """Mask a card-like identifier keeping only the last four characters."""
    if not value:
        return value
    return "*" * max(len(value) - 4, 0) + value[-4:]


def mask_ip(value: str | None) -> str | None:
    if not value:
        return value
    if ":" in value:  # IPv6
        head = value.split(":")[:2]
        return ":".join(head) + ":****"
    parts = value.split(".")
    if len(parts) != 4:
        return value
    return f"{parts[0]}.{parts[1]}.*.*"
