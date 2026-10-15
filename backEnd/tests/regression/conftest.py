"""Fixtures for the legacy regression suite (Stage 0).

These tests encode bugs reproduced in docs/review_2026-10-15/REVIEW_AND_PLAN.md
(Part A.3, Appendix 2) against the legacy ``agentic`` / ``evidence_layer``
packages. They are adapted from the scripts in docs/review_2026-10-15/repro/.

Everything here is offline. Nothing here imports from ``basis``.

Stage 2 deleted the LangGraph regressions W1, W2 and W3 (and the node recorders,
MemorySaver sharing and Firestore fake they used): the Temporal StudyWorkflow replaces
that graph, and tests/lifecycle covers the same invariants (see tests/LEDGER.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Resolve `agentic` and `evidence_layer` from this checkout's backEnd/, ahead of
# any editable install that points at another clone.
_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "regression: encodes a reproduced legacy bug as xfail(strict=True); "
        "remove the xfail when the behavior is fixed",
    )


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the legacy code on its dev branches and away from the network."""
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
