"""The production configuration of each model step (EVAL_REPORT sections 15, 16 and 17).

Every setting the three wirings added: its default is the measured winner, an override is
read, an invalid value is refused (pydantic ``ValidationError`` for a value outside the
field's type, :class:`ConfigError` for a combination that would fail on the first call),
and the previous behaviour is one setting away.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import ConfigError, load_settings, reset_settings

STEP_VARS = (
    "TEXT_MODEL",
    "OPENAI_MODEL",
    "VISION_MODEL",
    "VISION_PHOTO_MODE",
    "VISION_MAX_TOKENS",
    "VISION_COUNT_FUSION",
    "VISION_FUSION_MODEL",
    "VISION_FUSION_REASONING_EFFORT",
    "ROOM_RECONCILE",
    "ROOM_PROMPT_VERSION",
    "ROOM_MODEL",
    "ROOM_REASONING_EFFORT",
    "ROOM_SINGLE_PHOTO",
    "ROOM_MATERIALS",
    "ROOM_GROUPING",
    "EXTRACTION_REASONING_EFFORT",
    "EXTRACTION_FALLBACK_MAX_TOKENS",
    "EXTRACTION_FALLBACK_SCOPE",
    "EXTRACTION_FALLBACK_CONTEXT_CHARS",
    "COST_TABLE_PATH",
    "CLASSIFY_MODEL",
    "CLASSIFY_REASONING_EFFORT",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    """No step variable set (the suite's baseline in tests/conftest.py removed too)."""
    for name in STEP_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


# --------------------------------------------------------------------------- defaults


def test_count_fusion_defaults_are_section_15(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.vision_photo_mode == "decomposed"
    assert settings.vision_count_fusion == "per_class"
    assert settings.vision_fusion_model == "claude-opus-5-5"
    assert settings.vision_fusion_reasoning_effort == "medium"
    # Claude's answer budget; the Anthropic provider adds its thinking allowance.
    assert settings.vision_fusion_output_budget == 1200


def test_room_step_defaults_are_section_16(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.room_reconcile is True
    assert settings.room_prompt_version == "v3"
    assert settings.room_model == "claude-sonnet-5-5"
    assert settings.room_reasoning_effort == "low"
    assert settings.room_single_photo == "per_photo"
    assert settings.room_materials == "sensitive"
    # EVAL_REPORT section 27: photos are grouped by file name and the study's aliases.
    assert settings.room_grouping == "filename"


def test_extraction_fallback_defaults_are_section_25(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.extraction_fallback_scope == "missing"
    assert settings.extraction_fallback_context_chars == 60_000
    # The licensed cost table is never in the repository: unset by default.
    assert settings.cost_table_path is None
    env.setenv("COST_TABLE_PATH", "/secrets/unit_costs.json")
    assert load_settings().cost_table_path == "/secrets/unit_costs.json"


def test_text_step_defaults_are_section_17(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert (settings.text_model, settings.extraction_reasoning_effort) == ("gpt-6-luna", "low")
    # A reasoning model spends output on thinking: section 17.1 ran it at 8000.
    assert settings.extraction_fallback_max_tokens is None
    assert settings.extraction_output_budget == 8000
    assert (settings.classify_model, settings.classify_reasoning_effort) == (
        "claude-sonnet-5-5",
        "low",
    )


# --------------------------------------------------------------------------- overrides


def test_count_fusion_overrides_are_read(env: pytest.MonkeyPatch) -> None:
    env.setenv("VISION_FUSION_MODEL", "claude-sonnet-5-5")
    env.setenv("VISION_FUSION_REASONING_EFFORT", "high")
    env.setenv("VISION_MAX_TOKENS", "2500")
    settings = load_settings()
    assert settings.vision_fusion_model == "claude-sonnet-5-5"
    assert settings.vision_fusion_reasoning_effort == "high"
    assert settings.vision_fusion_output_budget == 2500  # VISION_MAX_TOKENS applies to both


def test_an_openai_reasoning_fusion_model_gets_the_reasoning_budget(
    env: pytest.MonkeyPatch,
) -> None:
    env.setenv("VISION_MODEL", "claude-opus-5-5")
    env.setenv("VISION_FUSION_MODEL", "gpt-6.1-sol")
    env.setenv("VISION_FUSION_REASONING_EFFORT", "low")
    assert load_settings().vision_fusion_output_budget == 6000


def test_room_step_overrides_are_read(env: pytest.MonkeyPatch) -> None:
    env.setenv("ROOM_PROMPT_VERSION", "v2")
    env.setenv("ROOM_MODEL", "gpt-6.1-sol")
    env.setenv("ROOM_REASONING_EFFORT", "minimal")
    env.setenv("ROOM_SINGLE_PHOTO", "reconcile")
    env.setenv("ROOM_MATERIALS", "disagree")
    settings = load_settings()
    assert (settings.room_prompt_version, settings.room_model) == ("v2", "gpt-6.1-sol")
    assert settings.room_reasoning_effort == "minimal"
    assert (settings.room_single_photo, settings.room_materials) == ("reconcile", "disagree")


def test_text_step_overrides_are_read(env: pytest.MonkeyPatch) -> None:
    env.setenv("TEXT_MODEL", "claude-opus-5-5")
    env.setenv("EXTRACTION_REASONING_EFFORT", "medium")
    env.setenv("EXTRACTION_FALLBACK_MAX_TOKENS", "1600")
    env.setenv("CLASSIFY_MODEL", "claude-opus-5-5")
    env.setenv("CLASSIFY_REASONING_EFFORT", "high")
    settings = load_settings()
    assert (settings.text_model, settings.extraction_reasoning_effort) == (
        "claude-opus-5-5",
        "medium",
    )
    assert settings.extraction_output_budget == 1600
    assert (settings.classify_model, settings.classify_reasoning_effort) == (
        "claude-opus-5-5",
        "high",
    )


@pytest.mark.parametrize(
    ("model", "budget"),
    [("gpt-6-luna", 8000), ("gpt-5.2", 8000), ("gpt-4o-mini", 800), ("claude-opus-5-5", 800)],
)
def test_the_extraction_budget_default_follows_the_model_family(
    env: pytest.MonkeyPatch, model: str, budget: int
) -> None:
    """For the few critical fields (EXTRACTION_FALLBACK_SCOPE=critical)."""
    env.setenv("TEXT_MODEL", model)
    env.setenv("EXTRACTION_FALLBACK_SCOPE", "critical")
    assert load_settings().extraction_output_budget == budget


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-4o-mini", "claude-opus-5-5"])
def test_asking_for_every_missing_field_gets_section_17s_budget(
    env: pytest.MonkeyPatch, model: str
) -> None:
    env.setenv("TEXT_MODEL", model)
    assert load_settings().extraction_output_budget == 8000
    env.setenv("EXTRACTION_FALLBACK_MAX_TOKENS", "2000")
    assert load_settings().extraction_output_budget == 2000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EXTRACTION_FALLBACK_SCOPE", "all"),
        ("EXTRACTION_FALLBACK_CONTEXT_CHARS", "100"),
        ("EXTRACTION_FALLBACK_CONTEXT_CHARS", "1000000"),
        ("ROOM_GROUPING", "llm"),
    ],
)
def test_section_25_settings_refuse_bad_values(
    env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load_settings()


# --------------------------------------------------------------------------- the previous behaviour


def test_every_step_can_be_set_back_to_its_previous_configuration(
    env: pytest.MonkeyPatch,
) -> None:
    """The configuration before sections 15-17: one model's counts, no room step, the
    extraction fallback on gpt-4o-mini with its 800 tokens, the classifier on gpt-4o-mini."""
    env.setenv("VISION_COUNT_FUSION", "off")
    env.setenv("ROOM_RECONCILE", "false")
    env.setenv("ROOM_GROUPING", "model")
    env.setenv("TEXT_MODEL", "gpt-4o-mini")
    env.setenv("EXTRACTION_FALLBACK_SCOPE", "critical")
    env.setenv("CLASSIFY_MODEL", "gpt-4o-mini")
    settings = load_settings()
    assert settings.vision_count_fusion == "off" and settings.room_reconcile is False
    assert settings.room_grouping == "model" and settings.extraction_fallback_scope == "critical"
    assert settings.text_model == "gpt-4o-mini" and settings.extraction_output_budget == 800
    assert settings.classify_model == "gpt-4o-mini"


def test_fusion_is_active_only_in_the_decomposed_mode(env: pytest.MonkeyPatch) -> None:
    """The single-call step has no countables call to fuse: a deployment pinned to it keeps
    its configuration exactly (test_explicit_legacy_vision_values_still_work)."""
    assert load_settings().vision_count_fusion_active is True
    env.setenv("VISION_PHOTO_MODE", "single")
    settings = load_settings()
    assert settings.vision_count_fusion == "per_class"
    assert settings.vision_count_fusion_active is False
    env.setenv("VISION_PHOTO_MODE", "decomposed")
    env.setenv("VISION_COUNT_FUSION", "off")
    assert load_settings().vision_count_fusion_active is False


# --------------------------------------------------------------------------- invalid values


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VISION_COUNT_FUSION", "median"),
        ("VISION_FUSION_REASONING_EFFORT", "extreme"),
        ("ROOM_RECONCILE", "maybe"),
        ("ROOM_PROMPT_VERSION", "3"),
        ("ROOM_PROMPT_VERSION", "v0"),
        ("ROOM_PROMPT_VERSION", "latest"),
        ("ROOM_REASONING_EFFORT", "turbo"),
        ("ROOM_SINGLE_PHOTO", "skip"),
        ("ROOM_MATERIALS", "all"),
        ("EXTRACTION_REASONING_EFFORT", "lots"),
        ("EXTRACTION_FALLBACK_MAX_TOKENS", "10"),
        ("EXTRACTION_FALLBACK_MAX_TOKENS", "999999"),
        ("CLASSIFY_REASONING_EFFORT", "auto"),
    ],
)
def test_a_value_outside_the_type_is_refused(
    env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize(
    ("model_var", "model", "effort_var", "effort"),
    [
        ("VISION_FUSION_MODEL", "claude-opus-5-5", "VISION_FUSION_REASONING_EFFORT", "minimal"),
        ("ROOM_MODEL", "claude-sonnet-5-5", "ROOM_REASONING_EFFORT", "none"),
        ("CLASSIFY_MODEL", "claude-sonnet-5-5", "CLASSIFY_REASONING_EFFORT", "minimal"),
        ("TEXT_MODEL", "claude-opus-5-5", "EXTRACTION_REASONING_EFFORT", "none"),
    ],
)
def test_an_openai_only_effort_on_a_claude_model_is_refused(
    env: pytest.MonkeyPatch, model_var: str, model: str, effort_var: str, effort: str
) -> None:
    env.setenv(model_var, model)
    env.setenv(effort_var, effort)
    with pytest.raises(ConfigError, match="not a Claude effort level"):
        load_settings()


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_a_reasoning_openai_classifier_with_an_effort_is_refused(
    env: pytest.MonkeyPatch, effort: str
) -> None:
    """Chat Completions refuses function tools with a reasoning effort (section 17.2)."""
    env.setenv("CLASSIFY_MODEL", "gpt-6-luna")
    env.setenv("CLASSIFY_REASONING_EFFORT", effort)
    with pytest.raises(ConfigError, match="CLASSIFY_REASONING_EFFORT=none"):
        load_settings()


def test_a_reasoning_openai_classifier_runs_at_effort_none(env: pytest.MonkeyPatch) -> None:
    """How section 17.2 ran gpt-6-luna, gpt-5.4-mini and gpt-5.2 through the loop."""
    env.setenv("CLASSIFY_MODEL", "gpt-6-luna")
    env.setenv("CLASSIFY_REASONING_EFFORT", "none")
    assert load_settings().classify_model == "gpt-6-luna"


@pytest.mark.parametrize("effort", ["none", "low"])
def test_gpt_6_1_sol_cannot_run_the_classifier_at_any_effort(
    env: pytest.MonkeyPatch, effort: str
) -> None:
    env.setenv("CLASSIFY_MODEL", "gpt-6.1-sol")
    env.setenv("CLASSIFY_REASONING_EFFORT", effort)
    with pytest.raises(ConfigError, match="cannot run the tool-calling classifier"):
        load_settings()


def test_a_non_reasoning_openai_classifier_ignores_the_effort(env: pytest.MonkeyPatch) -> None:
    """gpt-4o-mini takes no reasoning effort; the OpenAI provider does not send one."""
    env.setenv("CLASSIFY_MODEL", "gpt-4o-mini")
    assert load_settings().classify_reasoning_effort == "low"
