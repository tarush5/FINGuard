"""Cloud start-up bootstrap.

Managed platforms without a shell (Render free tier, Fly, Railway) need the
container to bring itself up: apply migrations, and optionally seed an empty
database so the demo has something to show.

    python -m scripts.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port $PORT

Both steps are safe to repeat. Migrations are idempotent by construction, and
seeding is skipped entirely unless SEED_ON_STARTUP is true *and* the database
holds no transactions -- a restart never rebuilds or duplicates data.
"""

from __future__ import annotations

import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url)
    # Keep our own logging configuration; see alembic/env.py.
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    logger.info("migrations_applied")


def database_is_empty() -> bool:
    from app.db.models.core import Transaction
    from app.db.session import session_scope

    with session_scope() as db:
        count = int(db.execute(select(func.count()).select_from(Transaction)).scalar_one() or 0)
    return count == 0


def maybe_seed() -> None:
    if not settings.seed_on_startup:
        logger.info("seed_skipped", extra={"reason": "SEED_ON_STARTUP is false"})
        return
    if not database_is_empty():
        logger.info("seed_skipped", extra={"reason": "database already holds transactions"})
        return

    from app.datagen.seed import seed

    logger.info(
        "seeding_started",
        extra={
            "customers": settings.seed_customers,
            "merchants": settings.seed_merchants,
            "transactions": settings.seed_transactions,
        },
    )
    summary = seed(reset=False, train=True, quiet=True)
    logger.info("seeding_completed", extra={"duration_seconds": summary.get("duration_seconds")})


def main() -> int:
    configure_logging(json_output=not settings.debug)
    logger.info(
        "bootstrap_started",
        extra={"environment": settings.environment, "dialect": "sqlite" if settings.sqlite else "postgresql"},
    )
    try:
        run_migrations()
        maybe_seed()
    except Exception as exc:  # noqa: BLE001 - surface the cause in platform logs
        logger.exception("bootstrap_failed", extra={"error": str(exc)})
        return 1
    logger.info("bootstrap_complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
