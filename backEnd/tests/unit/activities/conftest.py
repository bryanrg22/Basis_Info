"""Fixtures for the activity tests: ``ActivityEnvironment`` plus a real Postgres.

The activities write real rows, so these tests need the session's Postgres container
(every module sets ``pytestmark = pytest.mark.integration``). ``ActivityEnvironment``'s
default info names workflow ``test`` and run ``test-run``; the ``seeded`` fixture creates
that ``runs`` row so the idempotency claim (which references it) works.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from dbtest_support import db_settings, truncate_all
from lifecycletest_support import SeededStudy, make_deps, seed_run, seed_study
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.testing import ActivityEnvironment

from basis.activities import Deps
from basis.config import Settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_settings(database_url, db_pool_size=10))
    await truncate_all(engine)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def db_test_settings(database_url: str) -> Settings:
    return db_settings(database_url)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def deps(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
) -> Deps:
    return make_deps(sessionmaker, storage, db_test_settings)


@pytest.fixture
async def seeded(
    sessionmaker: async_sessionmaker[AsyncSession], storage: LocalStorage
) -> SeededStudy:
    study = await seed_study(sessionmaker, storage=storage)
    await seed_run(sessionmaker, study.study_id)
    return study


@dataclass
class Env:
    """An ``ActivityEnvironment`` that records heartbeats."""

    env: ActivityEnvironment = field(default_factory=ActivityEnvironment)
    heartbeats: list[tuple[Any, ...]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.env.on_heartbeat = lambda *details: self.heartbeats.append(details)

    def with_info(self, **changes: Any) -> Env:
        self.env.info = dataclasses.replace(self.env.info, **changes)
        return self

    async def run(self, fn: Any, *args: Any) -> Any:
        result = self.env.run(fn, *args)
        return await result if inspect.isawaitable(result) else result


@pytest.fixture
def activity_env() -> Env:
    return Env()
