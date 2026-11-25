"""The capture image's rendering check (``basis.capture.smoke``, run by CI on that image).

Runs a real headless Chromium where one is installed (skipped otherwise, e.g. the unit job,
which installs the Python package but no browser): WebGL2 must draw the known colour, on the
default flags and on the software renderer a GPU-less worker uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from basis.capture import smoke
from basis.config import reset_settings


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:  # no driver or no browser: nothing to test here
        return False


needs_chromium = pytest.mark.skipif(
    not _chromium_installed(), reason="no Playwright Chromium on this machine"
)


def test_the_expected_pixel_is_the_cleared_colour() -> None:
    assert smoke.EXPECTED_PIXEL == tuple(round(c * 255) for c in (0.2, 0.6, 0.4))


@needs_chromium
@pytest.mark.parametrize(
    "args",
    [None, "--use-angle=swiftshader,--enable-unsafe-swiftshader"],
    ids=["default", "swiftshader"],
)
async def test_webgl2_draws(monkeypatch: pytest.MonkeyPatch, args: str | None) -> None:
    if args is None:
        monkeypatch.delenv("CAPTURE_CHROMIUM_ARGS", raising=False)
    else:
        monkeypatch.setenv("CAPTURE_CHROMIUM_ARGS", args)
    reset_settings()
    try:
        result = await smoke.check()
    finally:
        reset_settings()
    assert result["webgl2"] is True, result
    assert result["drawn"] is True and result["ok"] is True, result
    if args is not None:
        assert "SwiftShader" in str(result["renderer"]), result
    assert os.environ.get("BASIS_ENV") == "test"
