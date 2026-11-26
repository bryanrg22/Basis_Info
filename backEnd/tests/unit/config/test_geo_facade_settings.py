"""``GEO_FACADE`` (EVAL_REPORT 109): off by default, turned on by the environment, refused when
it is not a boolean."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import load_settings, reset_settings


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("GEO_FACADE", raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_off_by_default(env: pytest.MonkeyPatch) -> None:
    assert load_settings().geo_facade is False


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("1", True), ("false", False)])
def test_it_can_be_turned_on(env: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    env.setenv("GEO_FACADE", raw)
    reset_settings()
    assert load_settings().geo_facade is expected


def test_a_value_that_is_not_a_boolean_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("GEO_FACADE", "sometimes")
    reset_settings()
    with pytest.raises(ValidationError):
        load_settings()
