"""``TAKEOFF_SKETCH`` and ``TAKEOFF_SKETCH_GLA_TOLERANCE`` (EVAL_REPORT 96).

Off by default (section 96 measured one property); on hands the takeoff the tolerance as a
Decimal fraction; a value outside off/on or a tolerance outside 0 to 1 is refused at startup.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import load_settings, reset_settings

VARS = ("TAKEOFF_SKETCH", "TAKEOFF_SKETCH_GLA_TOLERANCE")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_the_sketch_is_off_by_default_and_nothing_is_read(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.takeoff_sketch == "off"
    assert settings.takeoff_sketch_gla_tolerance == 0.01
    assert settings.sketch_tolerance is None


def test_on_hands_the_takeoff_the_tolerance(env: pytest.MonkeyPatch) -> None:
    env.setenv("TAKEOFF_SKETCH", "on")
    assert load_settings().sketch_tolerance == Decimal("0.01")
    reset_settings()
    env.setenv("TAKEOFF_SKETCH_GLA_TOLERANCE", "0.05")
    assert load_settings().sketch_tolerance == Decimal("0.05")


def test_a_tolerance_alone_does_not_turn_the_sketch_on(env: pytest.MonkeyPatch) -> None:
    env.setenv("TAKEOFF_SKETCH_GLA_TOLERANCE", "0.05")
    assert load_settings().sketch_tolerance is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAKEOFF_SKETCH", "yes"),
        ("TAKEOFF_SKETCH_GLA_TOLERANCE", "-0.01"),
        ("TAKEOFF_SKETCH_GLA_TOLERANCE", "1.5"),
        ("TAKEOFF_SKETCH_GLA_TOLERANCE", "tight"),
    ],
)
def test_a_bad_value_is_refused(env: pytest.MonkeyPatch, name: str, value: str) -> None:
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load_settings()
