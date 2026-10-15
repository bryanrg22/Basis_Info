"""Fixtures for the legacy regression suite (Stage 0).

These tests encode bugs reproduced in docs/review_2026-10-15/REVIEW_AND_PLAN.md
(Part A.3, Appendix 2) against the legacy ``agentic`` / ``evidence_layer``
packages. They are adapted from the scripts in docs/review_2026-10-15/repro/.

Everything here is offline: node bodies in ``agentic.graph.workflow`` are
replaced by recorders, ``agentic.firestore.client.FirestoreClient`` is replaced
by an in-memory fake, and checkpointing uses an in-process MemorySaver.
Nothing here imports from ``basis``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Resolve `agentic` and `evidence_layer` from this checkout's backEnd/, ahead of
# any editable install that points at another clone.
_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# The seven node names registered by agentic.graph.workflow.create_workflow(),
# mapped to the module-level function each one is built from.
LEGACY_NODES: dict[str, str] = {
    "load_study": "load_study_node",
    "resource_extraction": "resource_extraction_node",
    "analyze_rooms": "analyze_rooms_node",
    "reviewing_rooms": "reviewing_rooms_node",
    "process_assets": "process_assets_node",
    "complete": "complete_workflow_node",
    "error_handler": "error_handler_node",
}

# State each recorder merges in. Mirrors the *state* effect of the real node
# (not its Firestore side effects), as in repro_resume.py.
_NODE_STATE_PATCH: dict[str, Callable[[dict], dict]] = {
    "load_study": lambda s: {"user_id": "u1", "rooms": s.get("rooms", [])},
    "resource_extraction": lambda s: {
        "current_stage": "resource_extraction",
        "needs_review": True,
        "appraisal_resources": {"ingested": True},
    },
    "analyze_rooms": lambda s: {"rooms_ready": True},
    "reviewing_rooms": lambda s: {
        "current_stage": "reviewing_rooms",
        "needs_review": True,
    },
    "process_assets": lambda s: {
        "current_stage": "engineering_takeoff",
        "needs_review": True,
    },
    "complete": lambda s: {"current_stage": "completed", "needs_review": False},
    "error_handler": lambda s: {},
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "regression: encodes a reproduced legacy bug as xfail(strict=True); "
        "remove the xfail when the behavior is fixed",
    )


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the legacy code on its dev branches and away from the network."""
    # Unset -> create_workflow() picks MemorySaver instead of FirestoreCheckpointer.
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def workflow_module():
    import agentic.graph.workflow as wf

    return wf


@pytest.fixture
def recorded_nodes(monkeypatch: pytest.MonkeyPatch, workflow_module) -> list[str]:
    """Replace every graph node with a recorder; return the list of node names run.

    create_workflow() looks the node functions up as module globals at call time,
    so patching the module attributes changes the compiled graph's node bodies
    without changing its topology.
    """
    calls: list[str] = []

    def make(name: str):
        patch = _NODE_STATE_PATCH[name]

        async def node(state: dict) -> dict:
            calls.append(name)
            return {**state, **patch(state)}

        node.__name__ = f"recorded_{name}"
        return node

    for node_name, attr in LEGACY_NODES.items():
        monkeypatch.setattr(workflow_module, attr, make(node_name))
    return calls


@pytest.fixture
def shared_memory_saver(monkeypatch: pytest.MonkeyPatch, workflow_module):
    """Make every dev-mode create_workflow() call share one MemorySaver.

    In production mode checkpoints persist across the start and resume requests
    (FirestoreCheckpointer); in dev mode each call creates a fresh MemorySaver.
    Sharing one saver gives the legacy resume path its most favorable conditions,
    so a failure is not an artifact of lost checkpoints.
    """
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    monkeypatch.setattr(workflow_module, "MemorySaver", lambda: saver)
    return saver


@dataclass
class FakeFirestore:
    """In-memory stand-in for agentic.firestore.client.FirestoreClient."""

    stage: str = "uploading_documents"
    rooms_ready: bool = True
    writes: list[tuple[str, Any]] = field(default_factory=list)

    def client_class(self) -> type:
        fake = self

        class _Client:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def get_study(self, study_id: str) -> dict:
                return {
                    "workflowStatus": fake.stage,
                    "roomsReady": fake.rooms_ready,
                    "userId": "u1",
                }

            def update_study(self, study_id: str, data: dict) -> None:
                fake.writes.append(("update_study", data))

            def update_workflow_status(self, study_id: str, status: str) -> None:
                fake.writes.append(("update_workflow_status", status))

        return _Client


@pytest.fixture
def fake_firestore(monkeypatch: pytest.MonkeyPatch) -> FakeFirestore:
    """Patch the FirestoreClient class that resume_workflow() imports at call time."""
    import agentic.firestore.client as fsclient

    fake = FakeFirestore()
    monkeypatch.setattr(fsclient, "FirestoreClient", fake.client_class())
    return fake
