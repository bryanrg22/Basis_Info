"""The Postgres-backed rate limiter (``basis.api.ratelimit_pg``) on the real database.

* One budget per key across processes: two limiter instances (two API processes) over the
  same database share the sliding-window log; the hit the first admits counts for the second.
* Exact: never more than ``limit`` admissions in a window, also under concurrency (20
  requests for a budget of 5 admit exactly 5), because the key's requests are serialized by
  an advisory lock.
* ``Retry-After`` is the moment the oldest counted hit leaves the window; a hit that left
  the window frees its slot (rows are backdated here, the clock is the database's).
* Keys are independent; a key's expired rows are deleted on its next request and a sweep
  removes idle keys' rows older than an hour.
* Degraded mode: when the database cannot be reached the per-process fallback answers and
  a warning carries ``BASIS-API-044``, once per minute; the shared log resumes when the
  database is back.
* Wiring: ``RATE_LIMIT_STORE=postgres`` installs it with the injected sessionmaker and with
  the one the lifespan creates; shutdown puts the in-memory limiter back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from apitest_support import REFUSED_DATABASE_URL, FakeVerifier, api_settings, build_api_app
from dbtest_support import db_settings
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.app import create_app
from basis.api.ratelimit import WINDOW, InMemoryRateLimiter
from basis.api.ratelimit_pg import RETENTION, SWEEP_INTERVAL_S, PostgresRateLimiter
from basis.db.engine import create_engine
from basis.db.models import RateLimitHit
from basis.db.session import create_sessionmaker
from basis.errors.codes import E
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

LIMIT = 5


@pytest.fixture
def limiter(sessionmaker: async_sessionmaker[AsyncSession]) -> PostgresRateLimiter:
    return PostgresRateLimiter(sessionmaker)


async def hits(sm: async_sessionmaker[AsyncSession], key: str | None = None) -> int:
    async with sm() as session:
        stmt = select(func.count()).select_from(RateLimitHit)
        if key is not None:
            stmt = stmt.where(RateLimitHit.key == key)
        return int(await session.scalar(stmt) or 0)


async def backdate(sm: async_sessionmaker[AsyncSession], key: str, by: timedelta) -> None:
    async with sm() as session, session.begin():
        await session.execute(
            update(RateLimitHit)
            .where(RateLimitHit.key == key)
            .values(created_at=RateLimitHit.created_at - by)
        )


async def test_the_budget_is_exact_and_refused_with_retry_after(
    limiter: PostgresRateLimiter, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    for remaining in range(LIMIT - 1, -1, -1):
        decision = await limiter.hit("read:uid:alice", LIMIT, WINDOW)
        assert (decision.allowed, decision.limit, decision.remaining) == (True, LIMIT, remaining)
        assert decision.retry_after == 0.0
    refused = await limiter.hit("read:uid:alice", LIMIT, WINDOW)
    assert (refused.allowed, refused.remaining) == (False, 0)
    # The oldest hit was a moment ago, so the whole window remains, within the test's run time.
    assert WINDOW.total_seconds() - 5 < refused.retry_after <= WINDOW.total_seconds()
    assert await hits(sessionmaker, "read:uid:alice") == LIMIT  # refusals are not logged


async def test_two_processes_share_one_budget(
    sessionmaker: async_sessionmaker[AsyncSession], db_engine: object
) -> None:
    first, second = PostgresRateLimiter(sessionmaker), PostgresRateLimiter(sessionmaker)
    for _ in range(LIMIT - 1):
        assert (await first.hit("k", LIMIT, WINDOW)).allowed
    last = await second.hit("k", LIMIT, WINDOW)
    assert (last.allowed, last.remaining) == (True, 0)
    assert not (await first.hit("k", LIMIT, WINDOW)).allowed
    assert not (await second.hit("k", LIMIT, WINDOW)).allowed


async def test_concurrent_requests_admit_exactly_the_limit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Twenty requests at once, from four 'processes': the advisory lock serializes the
    key, so exactly five are admitted and fifteen refused."""
    limiters = [PostgresRateLimiter(sessionmaker) for _ in range(4)]
    decisions = await asyncio.gather(
        *(limiters[i % 4].hit("burst", LIMIT, WINDOW) for i in range(20))
    )
    assert sum(d.allowed for d in decisions) == LIMIT
    assert await hits(sessionmaker, "burst") == LIMIT


async def test_a_hit_that_left_the_window_frees_its_slot(
    limiter: PostgresRateLimiter, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(LIMIT):
        assert (await limiter.hit("k", LIMIT, WINDOW)).allowed
    assert not (await limiter.hit("k", LIMIT, WINDOW)).allowed
    # Move every hit back by less than the window: still refused, Retry-After shorter.
    await backdate(sessionmaker, "k", WINDOW - timedelta(seconds=20))
    refused = await limiter.hit("k", LIMIT, WINDOW)
    assert not refused.allowed
    assert 15 < refused.retry_after <= 20
    # And past the window: admitted again, the expired rows gone.
    await backdate(sessionmaker, "k", timedelta(seconds=25))
    admitted = await limiter.hit("k", LIMIT, WINDOW)
    assert (admitted.allowed, admitted.remaining) == (True, LIMIT - 1)
    assert await hits(sessionmaker, "k") == 1


async def test_retry_after_is_when_the_oldest_counted_hit_expires(
    limiter: PostgresRateLimiter, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """With a budget of 2: one hit 50 s ago and one now, the next request waits ~10 s (the
    older hit's expiry), not the whole window."""
    assert (await limiter.hit("k", 2, WINDOW)).allowed
    await backdate(sessionmaker, "k", timedelta(seconds=50))
    assert (await limiter.hit("k", 2, WINDOW)).allowed
    refused = await limiter.hit("k", 2, WINDOW)
    assert not refused.allowed
    assert 5 < refused.retry_after <= 10


async def test_keys_are_independent(limiter: PostgresRateLimiter) -> None:
    for _ in range(LIMIT):
        assert (await limiter.hit("read:uid:alice", LIMIT, WINDOW)).allowed
    assert not (await limiter.hit("read:uid:alice", LIMIT, WINDOW)).allowed
    assert (await limiter.hit("mutation:uid:alice", LIMIT, WINDOW)).allowed
    assert (await limiter.hit("read:uid:bob", LIMIT, WINDOW)).allowed


async def test_idle_keys_are_swept_after_the_retention(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = [1000.0]
    limiter = PostgresRateLimiter(sessionmaker, clock=lambda: now[0])
    assert (await limiter.hit("idle", LIMIT, WINDOW)).allowed
    await backdate(sessionmaker, "idle", RETENTION + timedelta(minutes=1))
    assert (await limiter.hit("active", LIMIT, WINDOW)).allowed  # no sweep yet
    assert await hits(sessionmaker, "idle") == 1
    now[0] += SWEEP_INTERVAL_S + 1
    assert (await limiter.hit("active", LIMIT, WINDOW)).allowed  # this one sweeps
    assert await hits(sessionmaker, "idle") == 0
    assert await hits(sessionmaker, "active") == 2


@pytest.mark.parametrize(
    ("limit", "window"),
    [(0, WINDOW), (-1, WINDOW), (5, timedelta(0)), (5, RETENTION + timedelta(seconds=1))],
)
async def test_refuses_impossible_parameters(
    limiter: PostgresRateLimiter, limit: int, window: timedelta
) -> None:
    with pytest.raises(ValueError):
        await limiter.hit("k", limit, window)


async def test_unreachable_database_falls_back_to_per_process_limiting(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    unreachable = create_engine(
        db_settings(database_url).model_copy(update={"database_url": REFUSED_DATABASE_URL}),
        pool_timeout=0.5,
    )
    now = [0.0]
    limiter = PostgresRateLimiter(create_sessionmaker(unreachable), clock=lambda: now[0])
    try:
        with caplog.at_level(logging.WARNING, logger="basis.api.ratelimit_pg"):
            decisions = [await limiter.hit("k", 2, WINDOW) for _ in range(3)]
    finally:
        await unreachable.dispose()
    assert [d.allowed for d in decisions] == [True, True, False]  # the fallback still limits
    assert limiter.fallbacks == 3
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # one per minute, not one per request
    assert warnings[0].error_code == E.API_RATE_LIMIT_STORE_UNAVAILABLE  # type: ignore[attr-defined]
    assert "127.0.0.1" not in warnings[0].getMessage()


async def test_the_fallback_is_the_in_memory_limiter_by_default(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    assert isinstance(PostgresRateLimiter(sessionmaker)._fallback, InMemoryRateLimiter)


# --------------------------------------------------------------------------- wiring


async def test_postgres_store_is_installed_with_an_injected_sessionmaker(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> None:
    app = build_api_app(
        api_settings(database_url, rate_limit_store="postgres"), sessionmaker, storage, verifier
    )
    assert isinstance(app.state.rate_limiter, PostgresRateLimiter)
    memory = build_api_app(api_settings(database_url), sessionmaker, storage, verifier)
    assert isinstance(memory.state.rate_limiter, InMemoryRateLimiter)  # the test default


@pytest.fixture
async def owned_app(database_url: str, storage: LocalStorage) -> AsyncIterator[object]:
    settings = api_settings(database_url, rate_limit_store="postgres", temporal_address="")
    app = create_app(settings, storage=storage)
    async with app.router.lifespan_context(app):
        yield app


async def test_lifespan_installs_the_postgres_store_and_shutdown_removes_it(
    database_url: str, storage: LocalStorage
) -> None:
    settings = api_settings(database_url, rate_limit_store="postgres", temporal_address="")
    app = create_app(settings, storage=storage)
    assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)  # until startup
    async with app.router.lifespan_context(app):
        limiter = app.state.rate_limiter
        assert isinstance(limiter, PostgresRateLimiter)
        assert (await limiter.hit("k", 1, WINDOW)).allowed
        assert not (await limiter.hit("k", 1, WINDOW)).allowed
    assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)
