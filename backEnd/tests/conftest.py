"""Shared fixtures for the basis test suite.

The environment is set at import time, before any basis module builds settings, so no
test can accidentally run as ``dev`` (the only environment that reads ``.env``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault("BASIS_ENV", "test")
os.environ["GIT_SHA"] = "test-sha"  # tests are hermetic: CI exports the real sha for the job
# The suite's photo-step baseline is the pre-12.1 configuration (one call per photo on a
# non-reasoning model), which the budget and call-count arithmetic of the lifecycle tests
# is written against; the decomposed step and the reasoning-model budget have their own
# tests, and tests/unit/config proves the production defaults with these unset.
os.environ.setdefault("VISION_PHOTO_MODE", "single")
os.environ.setdefault("VISION_MODEL", "gpt-4o-mini")

from basis.config import Settings, get_settings, reset_settings  # noqa: E402
from basis.obs.logging import clear_context  # noqa: E402

if TYPE_CHECKING:
    from testcontainers.community.postgres import PostgresContainer


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get("BASIS_ENV") != "test":
        raise pytest.UsageError(
            "The test suite must run with BASIS_ENV=test (found "
            f"{os.environ.get('BASIS_ENV')!r}); unset it or set it to 'test'."
        )


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Fresh settings built from the current environment; cache cleared on both sides."""
    reset_settings()
    yield get_settings()
    reset_settings()


@pytest.fixture(autouse=True)
def _isolated_log_context() -> Iterator[None]:
    clear_context()
    yield
    clear_context()


# --------------------------------------------------------------------------- PostgreSQL
# Shared by tests/integration/db and tests/api: one container per test session. Imports
# are local so that collecting the unit tests does not import testcontainers or Alembic.


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    from dbtest_support import PG_IMAGE
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres: PostgresContainer) -> str:
    """The container's database with ``CREATE EXTENSION vector`` and Alembic at head."""
    from alembic import command
    from dbtest_support import alembic_config, execute_admin

    url = postgres.get_connection_url()
    asyncio.run(execute_admin(url, "CREATE EXTENSION IF NOT EXISTS vector"))
    command.upgrade(alembic_config(url), "head")
    return url
