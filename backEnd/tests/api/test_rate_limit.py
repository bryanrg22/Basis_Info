"""Rate limits on the real routes (S1_SPEC section 5, BUILD_PLAN 6.4).

The app is built with 3 reads and 2 mutations per minute. Budgets are per verified user,
not per IP (every request here comes from the same ASGI client address), reads and
mutations are counted separately, and a refused request gets a 429 problem with
``Retry-After`` before any database work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    REFUSED_DATABASE_URL,
    Api,
    FakeVerifier,
    api_settings,
    build_api_app,
)
from dbtest_support import db_settings
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.ratelimit import WINDOW
from basis.db.engine import create_engine
from basis.db.models import Study
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

READS, MUTATIONS = 3, 2


@pytest.fixture
def limited_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    settings = api_settings(
        database_url, rate_limit_reads_per_min=READS, rate_limit_mutations_per_min=MUTATIONS
    )
    return build_api_app(settings, sessionmaker, storage, verifier)


@pytest.fixture
async def limited(limited_app: FastAPI) -> AsyncIterator[Api]:
    transport = httpx.ASGITransport(app=limited_app, client=("203.0.113.7", 50000))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield Api(client)


def assert_429(response: httpx.Response) -> None:
    assert response.status_code == 429
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["type"].endswith("/rate-limited")
    retry_after = int(response.headers["retry-after"])
    assert 1 <= retry_after <= WINDOW.total_seconds()


async def test_reads_are_limited_per_user(limited: Api) -> None:
    for _ in range(READS):
        assert (await limited.get("alice", "/v1/studies")).status_code == 200
    assert_429(await limited.get("alice", "/v1/studies"))
    # Same client address, different user: a fresh budget.
    assert (await limited.get("bob", "/v1/studies")).status_code == 200


async def test_mutations_have_their_own_budget(
    limited: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(READS):
        await limited.get("alice", "/v1/studies")
    assert_429(await limited.get("alice", "/v1/studies"))

    for i in range(MUTATIONS):
        response = await limited.post("alice", "/v1/studies", {"property_name": f"S{i}"})
        assert response.status_code == 201
    assert_429(await limited.post("alice", "/v1/studies", {"property_name": "one too many"}))

    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Study)) == MUTATIONS


async def test_mutation_limit_does_not_spend_reads(limited: Api) -> None:
    for i in range(MUTATIONS):
        await limited.post("alice", "/v1/studies", {"property_name": f"S{i}"})
    assert_429(await limited.post("alice", "/v1/studies", {"property_name": "x"}))
    assert (await limited.get("alice", "/v1/studies")).status_code == 200


async def test_the_limit_is_checked_before_any_database_work(
    limited: Api, limited_app: FastAPI, database_url: str
) -> None:
    """With the budget spent, the answer is 429 even when the database is unreachable."""
    for _ in range(READS):
        await limited.get("alice", "/v1/studies")
    unreachable = create_engine(
        db_settings(database_url).model_copy(update={"database_url": REFUSED_DATABASE_URL}),
        pool_timeout=0.5,
    )
    limited_app.state.db_sessionmaker = create_sessionmaker(unreachable)
    try:
        assert_429(await limited.get("alice", "/v1/studies"))
        # Control: a request that is not limited does reach the (broken) database.
        assert (await limited.get("bob", "/v1/studies")).status_code >= 500
    finally:
        await unreachable.dispose()


async def test_unauthenticated_callers_are_limited_by_address(limited: Api) -> None:
    for _ in range(READS):
        assert (await limited.client.get("/v1/studies")).status_code == 401
    assert_429(await limited.client.get("/v1/studies"))
    # An authenticated user behind the same address is unaffected.
    assert (await limited.get("alice", "/v1/studies")).status_code == 200
