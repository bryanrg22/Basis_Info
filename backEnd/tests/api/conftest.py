"""Fixtures for the database-backed API tests (BUILD_PLAN 6.4, S1_SPEC section 7).

* The PostgreSQL container and migrated ``database_url`` come from ``tests/conftest.py``
  (one container per session, shared with ``tests/integration/db``).
* ``sessionmaker``: a real, committing sessionmaker on a fresh engine per test (asyncpg
  connections belong to one event loop); every table is truncated before and after.
* ``storage``: :class:`~basis.storage.LocalStorage` under the test's ``tmp_path``.
* ``api_app``: the real application from :func:`basis.api.app.create_app`, with the
  sessionmaker and storage injected, the Postgres idempotency store, and Firebase replaced
  by :class:`apitest_support.FakeVerifier`. Rate limits are raised so only
  ``test_rate_limit.py`` (which builds its own app) is limited.
* ``api``: :class:`apitest_support.Api` over an httpx ASGI client; ``seed``: direct
  writes for preconditions.

Tests that use these fixtures are marked ``integration`` in their own modules.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from dbtest_support import db_settings, truncate_all
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage


@pytest.fixture
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(db_settings(database_url))
    await truncate_all(engine)
    try:
        yield engine
    finally:
        await truncate_all(engine)
        await engine.dispose()


@pytest.fixture
def sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(db_engine)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "objects")


@pytest.fixture
def verifier() -> FakeVerifier:
    return FakeVerifier()


@pytest.fixture
def api_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    return build_api_app(api_settings(database_url), sessionmaker, storage, verifier)


@pytest.fixture
async def http(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def api(http: httpx.AsyncClient) -> Api:
    return Api(http)


@pytest.fixture
def seed(sessionmaker: async_sessionmaker[AsyncSession]) -> Seed:
    return Seed(sessionmaker)
