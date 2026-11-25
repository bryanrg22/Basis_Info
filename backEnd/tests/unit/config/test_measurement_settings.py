"""The traced measurement's settings (docs/api/OVERRIDES.md section 4, EVAL_REPORT 97).

``TRACED_MEASUREMENT`` defaults true and can be turned off; ``MEASURE_IMAGE_AFTER_SERVICE_DAYS``
defaults 365 and ``MEASURE_ERROR_FLAG_PCT`` 10; a value out of range or of the wrong type is
refused at startup."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import load_settings, reset_settings

VARS = ("TRACED_MEASUREMENT", "MEASURE_IMAGE_AFTER_SERVICE_DAYS", "MEASURE_ERROR_FLAG_PCT")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_the_defaults(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.traced_measurement is True
    assert settings.measure_image_after_service_days == 365
    assert settings.measure_error_flag_pct == 10.0


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("0", False), ("true", True)])
def test_traced_measurement_can_be_turned_off(
    env: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    env.setenv("TRACED_MEASUREMENT", raw)
    assert load_settings().traced_measurement is expected


def test_the_thresholds_can_be_set(env: pytest.MonkeyPatch) -> None:
    env.setenv("MEASURE_IMAGE_AFTER_SERVICE_DAYS", "0")
    env.setenv("MEASURE_ERROR_FLAG_PCT", "4.5")
    settings = load_settings()
    assert (settings.measure_image_after_service_days, settings.measure_error_flag_pct) == (0, 4.5)


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("TRACED_MEASUREMENT", "sometimes"),
        ("MEASURE_IMAGE_AFTER_SERVICE_DAYS", "-1"),
        ("MEASURE_IMAGE_AFTER_SERVICE_DAYS", "36501"),
        ("MEASURE_IMAGE_AFTER_SERVICE_DAYS", "a year"),
        ("MEASURE_ERROR_FLAG_PCT", "0"),
        ("MEASURE_ERROR_FLAG_PCT", "1001"),
        ("MEASURE_ERROR_FLAG_PCT", "ten"),
        ("MEASURE_ERROR_FLAG_PCT", "nan"),
    ],
)
def test_a_bad_value_is_refused(env: pytest.MonkeyPatch, name: str, raw: str) -> None:
    env.setenv(name, raw)
    with pytest.raises(ValidationError):
        load_settings()
