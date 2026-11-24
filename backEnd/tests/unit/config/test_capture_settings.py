"""The property-capture settings (EVAL_REPORT 98, docs/api/PROPERTY_CAPTURE.md 5): off by
default with the section's gentle values; the spacing and load caps must be ordered; the
Chromium flags are a comma list (empty is unset); an empty text setting is its default."""

from __future__ import annotations

import pytest

from basis.config import ConfigError, Settings


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, env="test", **values)  # type: ignore[call-arg]


def test_the_defaults_are_off_and_gentle() -> None:
    s = settings()
    assert s.property_capture is False
    assert s.capture_task_queue == "basis-capture"
    assert (s.capture_spacing_min_s, s.capture_spacing_max_s) == (8.0, 20.0)
    assert (s.capture_loads_per_hour, s.capture_loads_per_day, s.capture_moves_per_hour) == (
        6,
        30,
        120,
    )
    assert (s.capture_breaker_minutes, s.capture_breaker_max_hours) == (60.0, 24.0)
    assert (s.capture_max_wait_hours, s.capture_settle_s, s.capture_recent_hours) == (
        48.0,
        12.0,
        12.0,
    )
    assert s.capture_profile_dir is None and s.capture_chromium_args is None
    assert "google" not in s.capture_source_text.lower()


def test_the_spacing_and_the_load_caps_must_be_ordered() -> None:
    with pytest.raises(ConfigError, match="CAPTURE_SPACING_MIN_S"):
        settings(capture_spacing_min_s=30, capture_spacing_max_s=20)
    with pytest.raises(ConfigError, match="CAPTURE_LOADS_PER_HOUR"):
        settings(capture_loads_per_hour=40, capture_loads_per_day=30)


def test_the_chromium_flags_are_a_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPTURE_CHROMIUM_ARGS", " --use-angle=swiftshader , --enable-gpu ")
    assert settings().capture_chromium_args == ("--use-angle=swiftshader", "--enable-gpu")
    monkeypatch.setenv("CAPTURE_CHROMIUM_ARGS", "")
    assert settings().capture_chromium_args is None


def test_empty_text_settings_are_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """``.env.example`` ships every value empty; empty must mean the default, never "" (an
    empty source line under every view, an empty task queue)."""
    for name in ("CAPTURE_SOURCE_TEXT", "CAPTURE_TASK_QUEUE", "CAPTURE_PROFILE_DIR"):
        monkeypatch.setenv(name, " ")
    s = settings()
    assert s.capture_source_text.startswith("Aerial imagery")
    assert s.capture_task_queue == "basis-capture" and s.capture_profile_dir is None


def test_the_section_99_defaults_and_the_viewer_origin() -> None:
    s = settings()
    assert s.capture_history is True and s.capture_street_view is False
    assert (s.capture_history_window_days, s.capture_history_max_views) == (730, 6)
    assert s.capture_history_max_steps == 40
    assert s.capture_clean_views is True and s.capture_frame_margin_px == 170
    assert s.capture_tesseract is None
    assert s.capture_viewer_origin == "https://earth.google.com"
    assert settings(capture_tesseract="").capture_tesseract is None
    assert settings(capture_viewer_origin="").capture_viewer_origin == "https://earth.google.com"
    local = settings(capture_viewer_origin="http://127.0.0.1:8123")
    assert local.capture_viewer_origin == "http://127.0.0.1:8123"
    for bad in ("earth.google.com", "https://earth.google.com/web", "ftp://x"):
        with pytest.raises(ValueError):
            settings(capture_viewer_origin=bad)
