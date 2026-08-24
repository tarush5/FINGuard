"""Engine and session management.

The same code path serves SQLite (default local developer experience) and
PostgreSQL (every deployed environment).  SQLite gets WAL mode and foreign key
enforcement so its behaviour matches PostgreSQL closely enough for the test
suite to be meaningful.
"""

from __future__ import annotations

import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Rolling window of the slowest statements, surfaced by /monitoring/database.
SLOW_QUERY_MS = 250.0
_query_stats: dict[str, Any] = {"count": 0, "total_ms": 0.0, "slow": []}


def _engine_kwargs() -> dict[str, Any]:
    if settings.sqlite:
        return {
            "connect_args": {"check_same_thread": False, "timeout": 30},
            "pool_pre_ping": True,
        }
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }


engine: Engine = create_engine(
    settings.database_url, echo=settings.db_echo, future=True, **_engine_kwargs()
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    if not settings.sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


@event.listens_for(engine, "before_cursor_execute")
def _before_cursor(conn, _cursor, _stmt, _params, context, _executemany) -> None:  # type: ignore[no-untyped-def]
    context._finguard_started = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor(conn, _cursor, stmt, _params, context, _executemany) -> None:  # type: ignore[no-untyped-def]
    started = getattr(context, "_finguard_started", None)
    if started is None:
        return
    elapsed_ms = (time.perf_counter() - started) * 1000
    _query_stats["count"] += 1
    _query_stats["total_ms"] += elapsed_ms
    if elapsed_ms >= SLOW_QUERY_MS:
        slow: list[dict[str, Any]] = _query_stats["slow"]
        slow.append({"sql": " ".join(stmt.split())[:220], "ms": round(elapsed_ms, 2)})
        del slow[:-25]


def database_stats() -> dict[str, Any]:
    count = _query_stats["count"] or 1
    pool = engine.pool
    return {
        "dialect": engine.dialect.name,
        "queries": _query_stats["count"],
        "avg_query_ms": round(_query_stats["total_ms"] / count, 3),
        "slow_queries": list(_query_stats["slow"])[-10:],
        "pool_size": getattr(pool, "size", lambda: None)(),
        "checked_out": getattr(pool, "checkedout", lambda: None)(),
    }


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers, seeders and event consumers."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("database_ping_failed", extra={"error": str(exc)})
        return False


def create_all() -> None:
    """Create the schema from the ORM metadata.

    Deployed environments run Alembic migrations instead; this exists for local
    bootstrap and for the test suite.
    """
    from app.db import models

    metadata = models.Base.metadata
    metadata.create_all(bind=engine)
    logger.info("schema_created", extra={"tables": len(metadata.tables)})
