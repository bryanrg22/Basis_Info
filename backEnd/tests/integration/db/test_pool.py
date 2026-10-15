"""Connection pool and server-side timeouts (S1_SPEC §3, §7).

* An exhausted pool makes the next caller wait ``pool_timeout`` and then fail with a
  clear ``sqlalchemy.exc.TimeoutError`` (the API maps it to 503), never hang.
* Connections go back to the pool after exceptions and after cancellation.
* ``statement_timeout`` is applied by the server to every pooled connection.
* ``pool_pre_ping`` replaces a connection the server has killed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from dbtest_support import db_settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from basis.db.engine import STATEMENT_TIMEOUT_MS, create_engine
from basis.db.session import create_sessionmaker, session_scope

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

POOL_TIMEOUT_S = 1.0


@pytest.fixture
async def small_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(db_settings(database_url, db_pool_size=2), pool_timeout=POOL_TIMEOUT_S)
    try:
        yield engine
    finally:
        await engine.dispose()


def checked_out(engine: AsyncEngine) -> int:
    pool = engine.sync_engine.pool
    assert isinstance(pool, QueuePool)
    return pool.checkedout()


async def sleep_query(engine: AsyncEngine, seconds: float) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT pg_sleep(:s)"), {"s": seconds})


async def test_pool_is_configured_from_settings(small_engine: AsyncEngine) -> None:
    pool = small_engine.sync_engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == 2
    assert pool._max_overflow == 0
    assert pool._pre_ping is True
    assert pool.timeout() == POOL_TIMEOUT_S


async def test_third_query_on_exhausted_pool_times_out_instead_of_hanging(
    small_engine: AsyncEngine,
) -> None:
    holders = [asyncio.create_task(sleep_query(small_engine, 3)) for _ in range(2)]
    while checked_out(small_engine) < 2:  # both connections are busy
        await asyncio.sleep(0.01)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="QueuePool limit of size 2 overflow 0 reached"):
        async with small_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    waited = time.monotonic() - started
    assert POOL_TIMEOUT_S * 0.9 <= waited < POOL_TIMEOUT_S + 1.5

    await asyncio.gather(*holders)
    assert checked_out(small_engine) == 0
    # The pool is healthy again once the long queries finish.
    async with small_engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_connections_are_returned_after_exceptions(small_engine: AsyncEngine) -> None:
    sessionmaker = create_sessionmaker(small_engine)
    for _ in range(5):  # more failures than the pool has connections
        with pytest.raises(DBAPIError, match="division by zero"):
            async with session_scope(sessionmaker) as session:
                await session.execute(text("SELECT 1 / 0"))
        assert checked_out(small_engine) == 0
    for _ in range(3):
        with pytest.raises(RuntimeError):
            async with session_scope(sessionmaker) as session:
                await session.execute(text("SELECT 1"))
                raise RuntimeError("handler bug")
        assert checked_out(small_engine) == 0
    # Both connections are usable at the same time afterwards.
    await asyncio.gather(sleep_query(small_engine, 0.1), sleep_query(small_engine, 0.1))


async def test_connection_is_returned_after_cancellation(small_engine: AsyncEngine) -> None:
    for _ in range(3):  # more cancellations than the pool has connections
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sleep_query(small_engine, 5), timeout=0.3)
    deadline = time.monotonic() + 5
    while checked_out(small_engine) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert checked_out(small_engine) == 0
    await asyncio.gather(sleep_query(small_engine, 0.1), sleep_query(small_engine, 0.1))


async def test_statement_timeout_is_set_on_every_connection(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.scalar(text("SHOW statement_timeout")) == "30s"
        assert await connection.scalar(text("SHOW application_name")) == "basis"
    assert STATEMENT_TIMEOUT_MS == 30_000


async def test_statement_timeout_cancels_long_query(database_url: str) -> None:
    engine = create_engine(db_settings(database_url), statement_timeout_ms=200)
    try:
        started = time.monotonic()
        with pytest.raises(DBAPIError, match="statement timeout"):
            await sleep_query(engine, 5)
        assert time.monotonic() - started < 3
        async with engine.connect() as connection:  # the connection is still usable
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


async def test_pre_ping_replaces_connection_killed_by_server(database_url: str) -> None:
    engine = create_engine(db_settings(database_url, db_pool_size=1))
    killer = create_engine(db_settings(database_url, db_pool_size=1))
    try:
        async with engine.connect() as connection:
            pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        async with killer.connect() as connection:
            assert await connection.scalar(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        async with engine.connect() as connection:
            new_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        assert new_pid != pid
    finally:
        await engine.dispose()
        await killer.dispose()
