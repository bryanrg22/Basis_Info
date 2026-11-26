"""The MEASURE_DRAFTS_* settings (EVAL_REPORT 110): both drafts off by default, the interior
step at section 107's setup, the verify threshold at section 110.4's $250; each turned on or set
by the environment, a bad value refused, a non-Claude effort refused for a Claude model, and the
interior setting carried onto the workflow input the API starts."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from basis.config import ConfigError, load_settings, reset_settings

NAMES = (
    "MEASURE_DRAFTS_EXTERIOR",
    "MEASURE_DRAFTS_INTERIOR",
    "MEASURE_INTERIOR_MODEL",
    "MEASURE_INTERIOR_REASONING_EFFORT",
    "MEASURE_INTERIOR_MAX_TOKENS",
    "MEASURE_DRAFT_VERIFY_USD",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_the_defaults(env: pytest.MonkeyPatch) -> None:
    s = load_settings()
    assert (s.measure_drafts_exterior, s.measure_drafts_interior) == (False, False)
    assert s.measure_interior_model == "claude-sonnet-5-5"
    assert s.measure_interior_reasoning_effort == "low"
    assert s.measure_interior_max_tokens == 1200
    assert s.measure_draft_verify_usd == 250.0


def test_each_can_be_set(env: pytest.MonkeyPatch) -> None:
    values = ("true", "1", "claude-opus-5-5", "medium", "2000", "0")
    for name, value in zip(NAMES, values, strict=True):
        env.setenv(name, value)
    reset_settings()
    s = load_settings()
    assert s.measure_drafts_exterior and s.measure_drafts_interior
    assert s.measure_interior_model == "claude-opus-5-5"
    assert s.measure_interior_reasoning_effort == "medium"
    assert s.measure_interior_max_tokens == 2000
    assert s.measure_draft_verify_usd == 0.0  # every pending draft holds the report


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEASURE_DRAFTS_EXTERIOR", "sometimes"),
        ("MEASURE_DRAFT_VERIFY_USD", "-1"),
        ("MEASURE_INTERIOR_MAX_TOKENS", "10"),
    ],
)
def test_a_bad_value_is_refused(env: pytest.MonkeyPatch, name: str, value: str) -> None:
    env.setenv(name, value)
    reset_settings()
    with pytest.raises((ValidationError, ConfigError)):
        load_settings()


def test_a_claude_model_refuses_an_openai_only_effort(env: pytest.MonkeyPatch) -> None:
    env.setenv("MEASURE_INTERIOR_REASONING_EFFORT", "minimal")
    reset_settings()
    with pytest.raises((ValidationError, ConfigError), match="MEASURE_INTERIOR"):
        load_settings()


@pytest.mark.parametrize("on", [False, True])
def test_the_workflow_input_carries_the_interior_setting(env: pytest.MonkeyPatch, on: bool) -> None:
    from basis.api.routes.workflow import workflow_input

    env.setenv("MEASURE_DRAFTS_INTERIOR", str(on).lower())
    reset_settings()
    study = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4(), revision=1)
    got = workflow_input(study, load_settings())  # type: ignore[arg-type]
    assert got.measure_drafts_interior is on
