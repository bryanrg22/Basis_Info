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
* Connection establishment failures become :class:`~basis.db.errors.DatabaseUnavailableError`
  (``do_connect`` hook, :data:`CONNECT_ERRORS`): the network errors asyncpg raises while
  connecting (refused, unreachable, DNS, connect timeout: all ``OSError``) and the
  server's "not now" answers (SQLSTATE class 08, ``57P03`` starting up or shutting down,
  ``53300`` too many connections). Authentication and unknown-database errors are
  configuration faults and pass through unchanged, as does every error on an established
  connection. The API maps the translated error to 503; anywhere else it is an ordinary
  retryable exception.
"""

from __future__ import annotations

from typing import Any, Final

import asyncpg
from sqlalchemy import event
from sqlalchemy.engine import URL, Dialect, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from basis.config import ConfigError, Settings
from basis.db.errors import DatabaseUnavailableError

ASYNC_DRIVER: Final = "postgresql+asyncpg"
STATEMENT_TIMEOUT_MS: Final = 30_000
POOL_TIMEOUT_S: Final = 10.0
APPLICATION_NAME: Final = "basis"

_SYNC_POSTGRES_DRIVERS: Final = frozenset({"postgres", "postgresql"})

# Raised by asyncpg.connect when the server cannot be reached or will not take a
# connection right now. OSError covers ConnectionRefusedError, socket.gaierror and
# TimeoutError (asyncio's connect timeout is the builtin TimeoutError since 3.11); it
# reaches the hook as raised. The server's refusals are asyncpg ``PostgresError``s, which
# SQLAlchemy's adapter re-raises at connect time as its own DBAPI ``Error`` with the
# asyncpg error as ``__cause__``, so both the error and its cause are checked.
CONNECT_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    asyncpg.PostgresConnectionError,  # SQLSTATE class 08
    asyncpg.CannotConnectNowError,  # 57P03: starting up, shutting down, recovering
    asyncpg.TooManyConnectionsError,  # 53300: max_connections or the role's limit
)


def unavailable_cause(exc: BaseException) -> BaseException | None:
    """The :data:`CONNECT_ERRORS` instance behind ``exc`` (itself or its cause), if any."""
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, CONNECT_ERRORS):
            return candidate
    return None


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

    engine = create_async_engine(
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
    event.listen(engine.sync_engine, "do_connect", _connect_or_unavailable)
    return engine


def _connect_or_unavailable(
    dialect: Dialect, conn_rec: ConnectionPoolEntry, cargs: Any, cparams: Any
) -> Any:
    """``do_connect`` hook: the dialect's own connect, with :data:`CONNECT_ERRORS` translated.

    Returning the connection replaces SQLAlchemy's default call, which is exactly
    ``dialect.connect(*cargs, **cparams)``. The driver's message can name the host and
    port, so only the exception type goes into the message; the original is the cause.
    """
    try:
        return dialect.connect(*cargs, **cparams)
    except Exception as exc:
        cause = unavailable_cause(exc)
        if cause is None:
            raise
        raise DatabaseUnavailableError(
            f"could not connect to the database ({type(cause).__name__})"
        ) from exc
