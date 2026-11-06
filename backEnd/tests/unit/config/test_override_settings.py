"""The engineer override settings (docs/api/OVERRIDES.md, EVAL_REPORT 90).

``OBJECT_EXCLUSION`` and ``UNIT_COST_OVERRIDE``, both default true (section 90 turned them
on); either can be turned off, and a value that is not a boolean is refused at startup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import load_settings, reset_settings

VARS = ("OBJECT_EXCLUSION", "UNIT_COST_OVERRIDE")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_both_overrides_are_on_by_default(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.object_exclusion is True and settings.unit_cost_override is True


@pytest.mark.parametrize("name", VARS)
@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("0", False), ("true", True)])
def test_each_can_be_turned_off(
    env: pytest.MonkeyPatch, name: str, raw: str, expected: bool
) -> None:
    env.setenv(name, raw)
    assert getattr(load_settings(), name.lower()) is expected


@pytest.mark.parametrize("name", VARS)
def test_a_non_boolean_is_refused(env: pytest.MonkeyPatch, name: str) -> None:
    env.setenv(name, "sometimes")
    with pytest.raises(ValidationError):
        load_settings()
