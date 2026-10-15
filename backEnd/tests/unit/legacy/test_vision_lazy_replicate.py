"""Regression: vision_layer's Replicate clients import without ``replicate`` installed.

``grounding_dino.py`` and ``sam2.py`` used to ``import replicate`` at module import time,
which made the whole ``src`` package (its ``__init__`` imports both) unusable without
the Replicate SDK. The import now happens inside the methods that call Replicate.

Each check runs in a subprocess with ``replicate`` blocked (``sys.modules["replicate"]
= None`` makes ``import replicate`` raise ImportError), so the result cannot depend on
which ``src`` package the current process has loaded.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

VISION_ROOT = Path(__file__).resolve().parents[3] / "vision_layer"


def run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    prelude = textwrap.dedent(
        f"""
        import sys
        sys.modules["replicate"] = None
        sys.path.insert(0, {str(VISION_ROOT)!r})
        """
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=110,
    )


@pytest.mark.regression
@pytest.mark.slow
def test_vision_clients_import_without_replicate() -> None:
    result = run_isolated(
        """
        from src.api_clients.grounding_dino import GroundingDINOClient
        from src.api_clients.sam2 import SAM2Client
        GroundingDINOClient()
        SAM2Client()
        assert sys.modules["replicate"] is None
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("ok")


@pytest.mark.regression
@pytest.mark.slow
def test_calling_replicate_without_it_still_fails_loudly() -> None:
    """Lazy import must not turn a missing dependency into a silent ``None`` result."""
    result = run_isolated(
        """
        import asyncio
        from tenacity import stop_after_attempt
        from src.api_clients.grounding_dino import GroundingDINOClient
        client = GroundingDINOClient()
        # One attempt, re-raise the original error instead of tenacity's RetryError.
        detect = GroundingDINOClient.detect.retry_with(stop=stop_after_attempt(1), reraise=True)
        try:
            asyncio.run(detect(client, "https://example.invalid/img.jpg", ["cabinet"]))
        except ImportError:
            print("import-error")
        """
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("import-error")
