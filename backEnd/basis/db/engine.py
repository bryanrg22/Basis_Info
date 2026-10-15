"""Async SQLAlchemy engine (S1_SPEC §3).

* Driver: asyncpg. ``database_url`` may be written as ``postgresql://`` or
  ``postgres://``; it is normalized to ``postgresql+asyncpg://``. Any other driver is a
  configuration error.
* Pool: ``pool_size = settings.db_pool_size`` (default 5), ``max_overflow = 0`` so the
  number of server connections per process is bounded and known (Cloud SQL connection
  limits are per instance, Cloud Run scales processes), ``pool_pre_ping`` so a
  connection dropped by the server or a proxy is replaced instead of failing a request.
  When every connection is checked out, a caller waits at most ``pool_timeout`` seconds
  and then gets ``sqlalchemy.exc.TimeoutError`` (the API maps it to 503) instead of
  hanging.
* ``statement_timeout`` is a server setting applied to every connection at connect time
  (30 s), so a runaway query is cancelled by PostgreSQL even if the client is gone.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from basis.config import ConfigError, Settings

ASYNC_DRIVER: Final = "postgresql+asyncpg"
STATEMENT_TIMEOUT_MS: Final = 30_000
POOL_TIMEOUT_S: Final = 10.0
APPLICATION_NAME: Final = "basis"

_SYNC_POSTGRES_DRIVERS: Final = frozenset({"postgres", "postgresql"})


def async_database_url(raw: str) -> URL:
    """Parse ``raw`` and force the asyncpg driver; reject non-PostgreSQL URLs.

    Errors never include the URL itself, which carries the password.
    """
    try:
        url = make_url(raw)
    except Exception:  # ArgumentError echoes the URL; do not chain it
        raise ConfigError("DATABASE_URL is not a valid database URL") from None
    if url.drivername in _SYNC_POSTGRES_DRIVERS:
        url = url.set(drivername=ASYNC_DRIVER)
    if url.drivername != ASYNC_DRIVER:
        raise ConfigError(
            f"DATABASE_URL must use PostgreSQL with asyncpg ({ASYNC_DRIVER}://); "
            f"got driver {url.drivername!r}"
        )
    return url


def create_engine(
    settings: Settings,
    *,
    pool_timeout: float = POOL_TIMEOUT_S,
    statement_timeout_ms: int = STATEMENT_TIMEOUT_MS,
) -> AsyncEngine:
    """Build the process-wide async engine from ``settings``.

    ``pool_timeout`` and ``statement_timeout_ms`` are keyword overrides for tests; the
    defaults are the production values.
    """
    if settings.cloud_sql_instance:
        # TODO(S5): connect through the Cloud SQL Python Connector
        # (google-cloud-sql-connector, async_creator with IAM auth) when deploying to
        # Cloud Run. Until then a configured instance must not silently fall back to
        # database_url.
        raise NotImplementedError(
            "CLOUD_SQL_INSTANCE is set but the Cloud SQL connector path arrives in S5; "
            "unset it and use DATABASE_URL"
        )
    if not settings.database_url:
        raise ConfigError("DATABASE_URL is required to create the database engine")
    if statement_timeout_ms <= 0:
        raise ValueError("statement_timeout_ms must be positive")
    if pool_timeout <= 0:
        raise ValueError("pool_timeout must be positive")

    return create_async_engine(
        async_database_url(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "statement_timeout": str(statement_timeout_ms),
                "application_name": APPLICATION_NAME,
            }
        },
    )
