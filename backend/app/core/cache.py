"""Cache / key-value layer.

FINGuard uses Redis in every deployed environment for hot risk features,
idempotency keys, rate limit counters and expensive analytics responses.  For
local development (and for the test suite) an in-process implementation with the
same interface and the same TTL semantics is used, so no code path is
Redis-only and nothing silently no-ops.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CacheBackend(Protocol):
    name: str

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    def setnx(self, key: str, value: str, ttl: int | None = None) -> bool: ...
    def delete(self, *keys: str) -> int: ...
    def incr(self, key: str, ttl: int | None = None) -> int: ...
    def ttl(self, key: str) -> int: ...
    def keys(self, pattern: str) -> list[str]: ...
    def flush_prefix(self, prefix: str) -> int: ...
    def healthy(self) -> bool: ...


class InMemoryCache:
    """Thread safe dict cache with expiry -- semantics mirror the Redis backend."""

    name = "in-memory"

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.RLock()

    def _expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and expires_at <= time.time()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expires_at = item
            if self._expired(expires_at):
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        with self._lock:
            self._store[key] = (value, time.time() + ttl if ttl else None)

    def setnx(self, key: str, value: str, ttl: int | None = None) -> bool:
        with self._lock:
            if self.get(key) is not None:
                return False
            self.set(key, value, ttl)
            return True

    def delete(self, *keys: str) -> int:
        with self._lock:
            return sum(1 for key in keys if self._store.pop(key, None) is not None)

    def incr(self, key: str, ttl: int | None = None) -> int:
        with self._lock:
            current = self.get(key)
            value = int(current or 0) + 1
            if current is None:
                self.set(key, str(value), ttl)
            else:
                _, expires_at = self._store[key]
                self._store[key] = (str(value), expires_at)
            return value

    def ttl(self, key: str) -> int:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return -2
            _, expires_at = item
            if expires_at is None:
                return -1
            return max(int(expires_at - time.time()), 0)

    def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        with self._lock:
            return [
                key
                for key, (_, expires_at) in list(self._store.items())
                if key.startswith(prefix) and not self._expired(expires_at)
            ]

    def flush_prefix(self, prefix: str) -> int:
        with self._lock:
            doomed = [key for key in self._store if key.startswith(prefix)]
            for key in doomed:
                self._store.pop(key, None)
            return len(doomed)

    def healthy(self) -> bool:
        return True


class RedisCache:
    name = "redis"

    def __init__(self, url: str) -> None:
        import redis  # imported lazily so redis stays an optional runtime dep

        self._client = redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )
        self._client.ping()

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._client.set(key, value, ex=ttl)

    def setnx(self, key: str, value: str, ttl: int | None = None) -> bool:
        return bool(self._client.set(key, value, ex=ttl, nx=True))

    def delete(self, *keys: str) -> int:
        return int(self._client.delete(*keys)) if keys else 0

    def incr(self, key: str, ttl: int | None = None) -> int:
        pipe = self._client.pipeline()
        pipe.incr(key)
        if ttl:
            pipe.expire(key, ttl, nx=True)
        return int(pipe.execute()[0])

    def ttl(self, key: str) -> int:
        return int(self._client.ttl(key))

    def keys(self, pattern: str) -> list[str]:
        return [str(k) for k in self._client.scan_iter(match=pattern, count=500)]

    def flush_prefix(self, prefix: str) -> int:
        found = self.keys(f"{prefix}*")
        return self.delete(*found) if found else 0

    def healthy(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


def _build_backend() -> CacheBackend:
    if settings.redis_url:
        try:
            backend = RedisCache(settings.redis_url)
            logger.info("cache_backend_selected", extra={"backend": "redis"})
            return backend
        except Exception as exc:
            logger.warning(
                "redis_unavailable_falling_back",
                extra={"error": str(exc), "url": settings.redis_url},
            )
    logger.info("cache_backend_selected", extra={"backend": "in-memory"})
    return InMemoryCache()


class Cache:
    """Namespaced JSON helper on top of a :class:`CacheBackend`."""

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self.backend = backend or _build_backend()

    @property
    def name(self) -> str:
        return self.backend.name

    def get_json(self, key: str) -> Any | None:
        raw = self.backend.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self.backend.delete(key)
            return None

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.backend.set(key, json.dumps(value, default=str), ttl or settings.cache_default_ttl)

    def cached(self, key: str, ttl: int, producer: Callable[[], Any]) -> Any:
        hit = self.get_json(key)
        if hit is not None:
            return hit
        value = producer()
        self.set_json(key, value, ttl)
        return value

    def claim(self, key: str, ttl: int) -> bool:
        """Atomically claim a key -- the primitive behind idempotent ingestion."""
        return self.backend.setnx(key, "1", ttl)

    def invalidate(self, prefix: str) -> int:
        return self.backend.flush_prefix(prefix)

    def healthy(self) -> bool:
        return self.backend.healthy()


cache = Cache()
