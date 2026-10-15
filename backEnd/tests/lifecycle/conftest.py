"""Fixtures for the StudyWorkflow lifecycle tests (S2_SPEC section 9, BUILD_PLAN 6.3).

* ``temporal_env``: one time-skipping Temporal test server per session
  (``WorkflowEnvironment.start_time_skipping``; the x64 binary runs under Rosetta on
  Apple Silicon), with the pydantic data converter and the custom search attributes
  registered. Session-scoped, so every test module here runs on the session event loop
  (``pytestmark = pytest.mark.asyncio(loop_scope="session")``).
* ``lifecycle_engine``: one engine on the session's Postgres container (shared with the
  other suites through ``database_url``), pool large enough for parallel photo activities.
* ``harness``: per test; truncates every table first, builds the real activities bound to
  a :class:`~basis.activities.deps.Deps` with a temporary ``LocalStorage``, gives the test
  its own task queue, and terminates its workflows afterwards.
* ``isolated_harness``: the same on a time-skipping server of its own, for tests that move
  the clock with ``env.sleep``. Observed: after a test stops its worker while a workflow
  is mid-activity, the shared test server stops skipping time and ``env.sleep`` times out
  (even once that workflow is terminated), so clock-driven tests do not share a server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest_asyncio
from dbtest_support import db_settings, truncate_all
from lifecycle_support import Harness
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from lifecycletest_support import make_deps

from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage
from basis.worker.search_attributes import ensure_search_attributes


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    env = await start_env()
    try:
        yield env
    finally:
        await env.shutdown()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def lifecycle_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(db_settings(database_url, db_pool_size=20))
    try:
        yield engine
    finally:
        await engine.dispose()


async def start_env() -> WorkflowEnvironment:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    await ensure_search_attributes(env.client)
    return env


@asynccontextmanager
async def make_harness(
    env: WorkflowEnvironment, engine: AsyncEngine, database_url: str, tmp_path: Path
) -> AsyncIterator[Harness]:
    await truncate_all(engine)
    sessionmaker = create_sessionmaker(engine)
    settings = db_settings(database_url)
    deps = make_deps(sessionmaker, LocalStorage(tmp_path / "storage"), settings)
    harness = Harness(env=env, sessionmaker=sessionmaker, deps=deps, settings=settings)
    try:
        yield harness
    finally:
        await harness.cleanup()


@pytest_asyncio.fixture(loop_scope="session")
async def harness(
    temporal_env: WorkflowEnvironment,
    lifecycle_engine: AsyncEngine,
    database_url: str,
    tmp_path: Path,
) -> AsyncIterator[Harness]:
    async with make_harness(temporal_env, lifecycle_engine, database_url, tmp_path) as harness:
        yield harness


@pytest_asyncio.fixture(loop_scope="session")
async def isolated_harness(
    lifecycle_engine: AsyncEngine, database_url: str, tmp_path: Path
) -> AsyncIterator[Harness]:
    env = await start_env()
    try:
        async with make_harness(env, lifecycle_engine, database_url, tmp_path) as harness:
            yield harness
    finally:
        await env.shutdown()
