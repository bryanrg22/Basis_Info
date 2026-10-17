"""The two measured-but-off room-stage settings (EVAL_REPORT 26.10).

``ROOM_STEP_MODE`` (``reconcile`` | ``room_call``, with ``ROOM_CALL_MODEL`` and
``ROOM_CALL_REASONING_EFFORT``) and ``MATERIALS_SECOND_LOOK`` (``per_photo`` |
``surface_crops``, with ``SURFACE_CROP_MODEL``, ``SURFACE_CROP_REASONING_EFFORT``,
``SURFACE_SEGMENTER`` and its Replicate version): the defaults keep today's behaviour and
carry the section 26 configuration for the day they are switched on, an override is read,
and a bad value or combination is refused at startup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import ConfigError, load_settings, reset_settings

VARS = (
    "ROOM_STEP_MODE",
    "ROOM_CALL_MODEL",
    "ROOM_CALL_REASONING_EFFORT",
    "MATERIALS_SECOND_LOOK",
    "SURFACE_CROP_MODEL",
    "SURFACE_CROP_REASONING_EFFORT",
    "SURFACE_SEGMENTER",
    "SURFACE_SEGMENTER_REPLICATE_VERSION",
    "REPLICATE_API_TOKEN",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_the_defaults_keep_todays_room_step(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.room_step_mode == "reconcile"
    assert settings.materials_second_look == "per_photo"


def test_the_defaults_carry_section_26s_configuration(env: pytest.MonkeyPatch) -> None:
    """What runs when the options are switched on: the room call on Opus 5.5 at high
    effort (26.6, 26.10), the crops on Sonnet 5.5 at low effort (26.3) with the measured
    Mask2Former Swin-L in the worker."""
    settings = load_settings()
    assert (settings.room_call_model, settings.room_call_reasoning_effort) == (
        "claude-opus-5-5",
        "high",
    )
    assert (settings.surface_crop_model, settings.surface_crop_reasoning_effort) == (
        "claude-sonnet-5-5",
        "low",
    )
    assert settings.surface_segmenter == "local"
    assert settings.surface_segmenter_replicate_version.startswith("95798aa39f0e")


def test_overrides_are_read(env: pytest.MonkeyPatch) -> None:
    env.setenv("ROOM_STEP_MODE", "room_call")
    env.setenv("ROOM_CALL_MODEL", "gpt-6.1-sol")
    env.setenv("ROOM_CALL_REASONING_EFFORT", "low")
    env.setenv("MATERIALS_SECOND_LOOK", "surface_crops")
    env.setenv("SURFACE_CROP_MODEL", "claude-opus-5-5")
    env.setenv("SURFACE_CROP_REASONING_EFFORT", "medium")
    env.setenv("SURFACE_SEGMENTER", "replicate")
    env.setenv("SURFACE_SEGMENTER_REPLICATE_VERSION", "abc123")
    env.setenv("REPLICATE_API_TOKEN", "r8_test_token")
    settings = load_settings()
    assert settings.room_step_mode == "room_call"
    assert (settings.room_call_model, settings.room_call_reasoning_effort) == (
        "gpt-6.1-sol",
        "low",
    )
    assert settings.materials_second_look == "surface_crops"
    assert (settings.surface_crop_model, settings.surface_crop_reasoning_effort) == (
        "claude-opus-5-5",
        "medium",
    )
    assert settings.surface_segmenter == "replicate"
    assert settings.surface_segmenter_replicate_version == "abc123"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ROOM_STEP_MODE", "room"),
        ("ROOM_STEP_MODE", "multiview"),
        ("ROOM_CALL_REASONING_EFFORT", "extreme"),
        ("MATERIALS_SECOND_LOOK", "crops"),
        ("SURFACE_SEGMENTER", "oneformer"),
        ("SURFACE_CROP_REASONING_EFFORT", "huge"),
    ],
)
def test_bad_values_are_refused(env: pytest.MonkeyPatch, name: str, value: str) -> None:
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize(
    ("prefix", "model"), [("ROOM_CALL", "claude-opus-5-5"), ("SURFACE_CROP", "claude-sonnet-5-5")]
)
def test_an_openai_only_effort_on_a_claude_model_is_refused(
    env: pytest.MonkeyPatch, prefix: str, model: str
) -> None:
    env.setenv(f"{prefix}_MODEL", model)
    env.setenv(f"{prefix}_REASONING_EFFORT", "minimal")
    with pytest.raises(ConfigError, match=prefix):
        load_settings()


def test_replicate_crops_without_a_token_are_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("MATERIALS_SECOND_LOOK", "surface_crops")
    env.setenv("SURFACE_SEGMENTER", "replicate")
    with pytest.raises(ConfigError, match="REPLICATE_API_TOKEN"):
        load_settings()
    env.setenv("REPLICATE_API_TOKEN", "   ")
    with pytest.raises(ConfigError, match="REPLICATE_API_TOKEN"):
        load_settings()


def test_the_replicate_segmenter_alone_needs_no_token(env: pytest.MonkeyPatch) -> None:
    """The token matters only when the crops are on; the local segmenter never needs it."""
    env.setenv("SURFACE_SEGMENTER", "replicate")
    assert load_settings().surface_segmenter == "replicate"
    env.setenv("MATERIALS_SECOND_LOOK", "surface_crops")
    env.setenv("SURFACE_SEGMENTER", "local")
    assert load_settings().materials_second_look == "surface_crops"
