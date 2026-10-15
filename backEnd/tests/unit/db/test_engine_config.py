"""Engine and session wiring without a database (S1_SPEC §3).

``create_async_engine`` does not connect until first use, so pool configuration, URL
handling and configuration errors can be checked offline. Integration tests in
``tests/integration/db/test_pool.py`` prove the same settings against PostgreSQL.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import QueuePool

from basis.config import ConfigError, Settings
from basis.db.engine import STATEMENT_TIMEOUT_MS, async_database_url, create_engine
from basis.db.session import SessionDep, create_sessionmaker

PASSWORD = "s3cr3t-pw"  # pragma: allowlist secret
URL = f"postgresql://basis:{PASSWORD}@db.internal:5432/basis"  # pragma: allowlist secret


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u@h/db",
        "postgres://u@h/db",
        "postgresql+asyncpg://u@h/db",
    ],
)
def test_url_is_normalized_to_asyncpg(raw: str) -> None:
    assert async_database_url(raw).drivername == "postgresql+asyncpg"


@pytest.mark.parametrize(
    "raw", ["mysql://u@h/db", "postgresql+psycopg2://u@h/db", "sqlite:///x.db"]
)
def test_non_asyncpg_drivers_are_rejected(raw: str) -> None:
    with pytest.raises(ConfigError, match="asyncpg"):
        async_database_url(raw)


def test_invalid_url_error_does_not_leak_the_url() -> None:
    with pytest.raises(ConfigError) as info:
        async_database_url(f"::not a url {PASSWORD}")
    assert PASSWORD not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


async def test_engine_pool_configuration() -> None:
    engine = create_engine(settings(database_url=URL, db_pool_size=7), pool_timeout=2.5)
    try:
        pool = engine.sync_engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.size() == 7
        assert pool._max_overflow == 0
        assert pool._pre_ping is True
        assert pool.timeout() == 2.5
        assert engine.url.drivername == "postgresql+asyncpg"
        assert PASSWORD not in repr(engine)
    finally:
        await engine.dispose()


class _Captured(Exception):
    """Raised from the do_connect hook so no network connection is attempted."""


async def test_engine_sets_statement_timeout_at_connect() -> None:
    engine = create_engine(settings(database_url=URL))
    captured: dict[str, Any] = {}

    def capture(dialect: Any, conn_rec: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        captured.update(cparams)
        raise _Captured

    event.listen(engine.sync_engine, "do_connect", capture)
    try:
        with pytest.raises(_Captured):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()
    assert captured["server_settings"] == {
        "statement_timeout": str(STATEMENT_TIMEOUT_MS),
        "application_name": "basis",
    }
    assert STATEMENT_TIMEOUT_MS == 30_000
    assert captured["host"] == "db.internal"


def test_missing_database_url_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        create_engine(settings())


def test_cloud_sql_instance_is_not_silently_ignored() -> None:
    with pytest.raises(NotImplementedError, match="S5"):
        create_engine(settings(database_url=URL, cloud_sql_instance="p:r:i"))


@pytest.mark.parametrize(
    "kwargs", [{"pool_timeout": 0}, {"pool_timeout": -1}, {"statement_timeout_ms": 0}]
)
def test_invalid_timeouts_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        create_engine(settings(database_url=URL), **kwargs)


async def test_sessionmaker_keeps_attributes_after_commit() -> None:
    engine = create_engine(settings(database_url=URL))
    try:
        maker = create_sessionmaker(engine)
        assert maker.kw["expire_on_commit"] is False
        assert maker.class_ is AsyncSession
    finally:
        await engine.dispose()


async def test_get_session_without_wiring_fails_clearly() -> None:
    app = FastAPI()

    @app.get("/x")
    async def endpoint(session: SessionDep) -> dict[str, str]:
        return {}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="app.state.db_sessionmaker is not set"):
            await client.get("/x")
