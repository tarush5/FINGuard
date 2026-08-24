"""Alembic environment.

The URL comes from application settings so migrations always target the same
database the app does, and `target_metadata` is the live ORM metadata so
autogenerate stays accurate.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# When Alembic is driven programmatically (scripts/bootstrap.py) the caller
# already configured logging; re-running fileConfig here would reset the root
# level to WARN and silence the application for the rest of the process.
if config.attributes.get("configure_logger", True) and config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=settings.sqlite,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Batch mode lets SQLite handle ALTER statements it cannot do natively.
            render_as_batch=settings.sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
