"""Shared fixtures for the basis test suite.

The environment is set at import time, before any basis module builds settings, so no
test can accidentally run as ``dev`` (the only environment that reads ``.env``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("BASIS_ENV", "test")
os.environ.setdefault("GIT_SHA", "test-sha")

from basis.config import Settings, get_settings, reset_settings  # noqa: E402
from basis.obs.logging import clear_context  # noqa: E402


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
