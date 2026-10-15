"""Replay safety: recorded histories must replay against the current workflow code.

BUILD_PLAN 6.3 ``test_versioning`` and 13.1: ``Replayer`` over the histories in
``tests/lifecycle/histories/`` catches a nondeterministic change before it reaches a
running study. Any change to the workflow's command sequence must be guarded with
``workflow.patched`` (see the StudyWorkflow docstring) so these keep replaying.

Re-record the histories (only when the change is deliberately incompatible and no study
started on the old code is still open) with::

    BASIS_RECORD_HISTORIES=1 uv run pytest tests/lifecycle/test_versioning.py -k record

Histories hold synthetic data only (random ids, ``kitchen_1.jpg``-style file names).
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from lifecycle_support import Harness, Started
from reordered_workflow import ReorderedStudyWorkflow
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer
from temporalio.workflow import NondeterminismError

from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

HISTORIES = Path(__file__).parent / "histories"
RECORD_ENV = "BASIS_RECORD_HISTORIES"


def replayer(*workflows: type[Any]) -> Replayer:
    return Replayer(
        workflows=list(workflows or [StudyWorkflow]), data_converter=pydantic_data_converter
    )


def load(path: Path) -> WorkflowHistory:
    data = json.loads(path.read_text())
    return WorkflowHistory.from_json(data["workflow_id"], data["history"])


# ---------------------------------------------------------------------------- scenarios


async def happy_path(harness: Harness) -> Started:
    started = await harness.start()
    await harness.approve_all(started.handle)
    await harness.result(started.handle)
    return started


async def appraisal_reject_path(harness: Harness) -> Started:
    started = await harness.start()
    await harness.wait_awaiting(started.handle, "appraisal_review", 1)
    await harness.decide(started.handle, harness.decision("appraisal_review", 1, "reject"))
    await harness.approve_all(started.handle, revision=2)
    await harness.result(started.handle)
    return started


async def cancelled_path(harness: Harness) -> Started:
    started = await harness.start()
    await harness.wait_awaiting(started.handle, "appraisal_review", 1)
    await started.handle.signal(StudyWorkflow.cancel_study, "recorded cancel")
    await harness.result(started.handle)
    return started


SCENARIOS: dict[str, Callable[[Harness], Awaitable[Started]]] = {
    "happy_path": happy_path,
    "appraisal_reject": appraisal_reject_path,
    "cancelled": cancelled_path,
}


# ---------------------------------------------------------------------------- tests


async def test_recorded_histories_exist() -> None:
    names = sorted(p.stem for p in HISTORIES.glob("*.json"))
    assert names == sorted(SCENARIOS), names


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_recorded_history_replays(name: str) -> None:
    history = load(HISTORIES / f"{name}.json")
    result = await replayer().replay_workflow(history)
    assert result.replay_failure is None


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_fresh_history_records_and_replays(harness: Harness, name: str) -> None:
    async with harness.worker():
        started = await SCENARIOS[name](harness)
    history = await started.handle.fetch_history()
    restored = WorkflowHistory.from_json(history.workflow_id, history.to_json())
    await replayer().replay_workflow(restored)


async def test_replay_detects_an_unguarded_change() -> None:
    history = load(HISTORIES / "happy_path.json")
    with pytest.raises(NondeterminismError):
        await replayer(ReorderedStudyWorkflow).replay_workflow(history)


@pytest.mark.skipif(not os.environ.get(RECORD_ENV), reason=f"set {RECORD_ENV}=1 to re-record")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_record_histories(harness: Harness, name: str) -> None:
    async with harness.worker():
        started = await SCENARIOS[name](harness)
    history = await started.handle.fetch_history()
    HISTORIES.mkdir(exist_ok=True)
    events = json.loads(history.to_json())
    _scrub_identity(events)
    document = {"workflow_id": history.workflow_id, "history": events}
    (HISTORIES / f"{name}.json").write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


def _scrub_identity(value: Any) -> None:
    """Replace worker/client identities (``<pid>@<hostname>``) with a fixed placeholder."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "identity" and isinstance(item, str):
                value[key] = "recorder"
            else:
                _scrub_identity(item)
    elif isinstance(value, list):
        for item in value:
            _scrub_identity(item)
