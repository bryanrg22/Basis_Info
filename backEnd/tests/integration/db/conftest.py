"""Fixtures for the database integration tests (S1_SPEC §7).

* ``postgres`` and ``database_url`` (in ``tests/conftest.py``, shared with ``tests/api``):
  one ``pgvector/pgvector:pg16`` container per test session (testcontainers; Docker must
  be running), with ``CREATE EXTENSION vector`` and Alembic upgraded to head.
* ``engine``: a fresh ``basis.db.engine.create_engine`` engine per test (asyncpg
  connections belong to one event loop, and each test has its own loop).
* ``session``: isolated per test. It runs inside an outer transaction that is rolled
  back at teardown; ``session.commit()`` only releases a SAVEPOINT
  (``join_transaction_mode="create_savepoint"``), so tests never see each other's rows.
* ``committed``: a sessionmaker whose transactions really commit, for tests that need
  several connections (concurrency, atomicity across transactions). Every table is
  truncated after the test. ``TRUNCATE`` does not fire the row-level append-only
  triggers on ``reviews``/``corrections``.
* ``make``: small factory for users, orgs and studies on the ``session`` fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from dbtest_support import Factory, db_settings, truncate_all
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(db_settings(database_url))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        outer = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
            autoflush=False,
        )
        try:
            yield session
        finally:
            await session.close()
            if outer.is_active:
                await outer.rollback()


@pytest.fixture
async def committed(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await truncate_all(engine)
    try:
        yield create_sessionmaker(engine)
    finally:
        await truncate_all(engine)


@pytest.fixture
def make(session: AsyncSession) -> Factory:
    return Factory(session)
