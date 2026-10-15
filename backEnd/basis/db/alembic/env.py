"""Alembic environment (async, asyncpg).

URL precedence: ``config.attributes["database_url"]`` (tests) >
``ALEMBIC_DATABASE_URL`` > ``Settings.database_url``. See ``backEnd/alembic.ini``.

Autogenerate and ``alembic check`` compare types and server defaults, so a model change
that is not reflected in a migration is reported as drift.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from basis.config import load_settings
from basis.db.engine import async_database_url
from basis.db.models import Base

URL_ENV_VAR = "ALEMBIC_DATABASE_URL"

config = context.config
# Tests may pass a deliberately different MetaData to prove that drift is detected.
target_metadata = config.attributes.get("target_metadata", Base.metadata)

# Programmatic callers (tests) set configure_logger=False so Alembic does not replace the
# process's logging configuration.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    explicit = config.attributes.get("database_url") or os.environ.get(URL_ENV_VAR)
    if explicit:
        return str(explicit)
    url = load_settings().database_url
    if not url:
        raise RuntimeError(f"Set DATABASE_URL (with BASIS_ENV) or {URL_ENV_VAR} to run Alembic")
    return url


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout (``alembic upgrade head --sql``) without a database."""
    _configure(
        url=async_database_url(_database_url()).render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(async_database_url(_database_url()), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
