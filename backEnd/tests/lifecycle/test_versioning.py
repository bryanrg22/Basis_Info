"""Replay safety: recorded histories must replay against the current workflow code.

BUILD_PLAN 6.3 ``test_versioning`` and 13.1: ``Replayer`` over the histories in
``tests/lifecycle/histories/`` catches a nondeterministic change before it reaches a
running study. Any change to the workflow's command sequence must be guarded with
``workflow.patched`` (see the StudyWorkflow docstring) so these keep replaying.

Re-record the histories (only when the change is deliberately incompatible and no study
started on the old code is still open) with::

    BASIS_RECORD_HISTORIES=1 uv run pytest tests/lifecycle/test_versioning.py -k record

``happy_path``, ``appraisal_reject`` and ``cancelled`` (before any patch) and
``single_rooms_gate`` (recorded by the code at 2728042, every patch before
``components-gate-v1``: a rooms rejection with a flagged photo) are the single rooms review
and must not be re-recorded while such an execution may be open (``-k record`` would
re-record them with the current code); ``components_gate`` (v0.12) is the new flow,
recorded alone with ``-k "record_histories and components_gate"``.

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
from sqlalchemy import select
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer
from temporalio.workflow import NondeterminismError

from basis.db.models import Component, Room
from basis.db.session import session_scope
from basis.workflow.contracts import CorrectionInput
from basis.workflow.study_workflow import COMPONENTS_GATE_PATCH, StudyWorkflow

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


async def single_rooms_gate_path(harness: Harness) -> Started:
    """The single rooms review with every patch before components-gate-v1: a rename and a
    flagged photo, then approvals. The recorded history was made by the code at 2728042
    (its input has no ``components_gate``); afresh it runs with ``components_gate=False``."""
    started = await harness.start(components_gate=False)
    handle, sid = started.handle, started.study_id
    await harness.approve(handle, "appraisal_review")
    await harness.wait_awaiting(handle, "rooms_review", 1)
    async with session_scope(harness.sessionmaker) as session:
        rooms = (
            await session.scalars(select(Room).where(Room.study_id == sid, Room.revision == 1))
        ).all()
    kitchen = next(r for r in rooms if r.name == "Kitchen")
    corrections = [
        CorrectionInput(entity_type="room", entity_id=str(kitchen.id), after={"name": "Galley"}),
        CorrectionInput(entity_type="photo", entity_id=str(started.study.photo_ids[2]), after={}),
    ]
    await harness.decide(
        handle, harness.decision("rooms_review", 1, "reject", corrections=corrections)
    )
    await harness.approve(handle, "rooms_review", 2)
    await harness.approve(handle, "assets_review", 2)
    await harness.result(handle)
    return started


async def components_gate_path(harness: Harness) -> Started:
    """Patch components-gate-v1: a rooms rejection with a move, the room step after the
    approval, a components rejection with a count and a flagged photo, then approvals."""
    started = await harness.start()
    handle, sid = started.handle, started.study_id
    await harness.approve(handle, "appraisal_review")
    await harness.wait_awaiting(handle, "rooms_review", 1)
    async with session_scope(harness.sessionmaker) as session:
        rooms = (
            await session.scalars(select(Room).where(Room.study_id == sid, Room.revision == 1))
        ).all()
    bathroom = next(r for r in rooms if r.name == "Bathroom")
    move = CorrectionInput(
        entity_type="photo_room",
        entity_id=str(started.study.photo_ids[1]),
        after={"room_id": str(bathroom.id)},
    )
    await harness.decide(handle, harness.decision("rooms_review", 1, "reject", corrections=[move]))
    await harness.approve(handle, "rooms_review", 2)
    await harness.wait_awaiting(handle, "components_review", 2)
    async with session_scope(harness.sessionmaker) as session:
        component = (
            await session.scalars(
                select(Component).where(Component.study_id == sid, Component.revision == 2)
            )
        ).first()
    assert component is not None
    corrections = [
        CorrectionInput(entity_type="component", entity_id=str(component.id), after={"count": 2}),
        CorrectionInput(entity_type="photo", entity_id=str(started.study.photo_ids[0]), after={}),
    ]
    await harness.decide(
        handle, harness.decision("components_review", 2, "reject", corrections=corrections)
    )
    await harness.approve(handle, "components_review", 3)
    await harness.approve(handle, "assets_review", 3)
    await harness.result(handle)
    return started


SCENARIOS: dict[str, Callable[[Harness], Awaitable[Started]]] = {
    "happy_path": happy_path,
    "appraisal_reject": appraisal_reject_path,
    "cancelled": cancelled_path,
    "single_rooms_gate": single_rooms_gate_path,
    "components_gate": components_gate_path,
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


def patch_markers(history: WorkflowHistory) -> list[str]:
    """The patch ids recorded in a history (``workflow.patched`` markers)."""
    found = []
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            details = event.marker_recorded_event_attributes.details
            if "patch-data" in details:
                data = json.loads(details["patch-data"].payloads[0].data)
                found.append(data["id"])
    return found


async def test_the_recorded_histories_span_both_flows() -> None:
    """The histories recorded before patch components-gate-v1 (the single rooms review)
    have no marker of it and replay as such: three before any patch, and single_rooms_gate
    with every earlier patch (an execution open when the patch is deployed). The
    components_gate history has it."""
    for name in ("happy_path", "appraisal_reject", "cancelled"):
        assert patch_markers(load(HISTORIES / f"{name}.json")) == []
    single = patch_markers(load(HISTORIES / "single_rooms_gate.json"))
    assert COMPONENTS_GATE_PATCH not in single
    assert {
        "localize-photos-v1",
        "reconcile-rooms-v1",
        "stage-failure-retry-v1",
        "appraisal-gate-early-v1",
        "provider-account-stop-v1",
        "localize-background-v1",
    } == set(single)
    assert COMPONENTS_GATE_PATCH in patch_markers(load(HISTORIES / "components_gate.json"))


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
