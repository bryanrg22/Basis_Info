"""The rooms review, then the components review (patch ``components-gate-v1``).

At the production defaults (the room step on; ``test_production_defaults``) with the
offline fixture providers; rows, storage, the Temporal server and the activities are real.

* the rooms review opens on the grouping with no inventory built (only the rooms' vote ran);
  approving it moves the study to ``components_processing``, the room step runs on the
  approved grouping, and the components review opens on its inventories; approving that
  builds the assets;
* a rooms rejection (a move and a rename) starts revision 2 with the grouping corrected and
  re-voted, re-analyzes and re-localizes nothing, runs no room step and opens the rooms
  review again; the room step after the approval builds the rooms as the engineer grouped
  them;
* a components rejection (a count and a flagged photo) re-analyzes only the flagged photo,
  in the room the rooms review approved, rebuilds only that room's inventory, keeps the
  corrected count, and opens the components review again without passing the rooms review;
* the workflow's validator refuses, at each gate, the corrections of the other gate.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from llm_fakes import FixtureVisionProvider
from sqlalchemy import select
from temporalio.client import WorkflowHandle, WorkflowUpdateFailedError
from test_production_defaults import production

from basis.db.enums import Stage, StudyStatus
from basis.db.models import Asset, Component, Extraction, PhotoRoom, Room
from basis.db.session import session_scope
from basis.vision.rooms import is_room_inventory
from basis.workflow.contracts import CorrectionInput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def rows(harness: Harness, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


async def rooms(harness: Harness, study_id: uuid.UUID, revision: int) -> dict[str, Room]:
    return {r.name: r for r in await rows(harness, Room, study_id, revision)}


async def inventory(harness: Harness, study_id: uuid.UUID, revision: int) -> list[Component]:
    return [
        c
        for c in await rows(harness, Component, study_id, revision)
        if is_room_inventory(c.raw, c.document_id)
    ]


async def scheduled(handle: WorkflowHandle[Any, Any]) -> list[str]:
    history = await handle.fetch_history()
    return [
        e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]


def room_calls(provider: FixtureVisionProvider) -> int:
    return sum(r.prompt_version.startswith("room_") for r in provider.requests)


async def test_the_room_step_runs_between_the_two_reviews(harness: Harness) -> None:
    provider = FixtureVisionProvider()
    run = production(harness, provider)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.wait_awaiting(handle, "rooms_review", 1)
            # The grouping is reviewed before any inventory exists.
            assert await inventory(run, sid, 1) == []
            assert room_calls(provider) == 0
            row = await run.study_row(sid)
            assert (row.current_stage, row.status, row.components_gate) == (
                Stage.ROOMS_REVIEW,
                StudyStatus.WAITING_REVIEW,
                True,
            )
            await run.approve(handle, "rooms_review")
            await run.wait_awaiting(handle, "components_review", 1)
            assert room_calls(provider) == 1  # the two-photo Kitchen
            assert sorted((c.label, c.raw["count"]) for c in await inventory(run, sid, 1)) == [
                ("bathtub", 1),
                ("countertop", 1),
                ("kitchen_cabinet", 6),
                ("toilet", 1),
            ]
            assert await rows(run, Asset, sid, 1) == []  # the assets wait for the approval
            await run.approve(handle, "components_review")
            await run.wait_awaiting(handle, "assets_review", 1)
            assert len(await rows(run, Asset, sid, 1)) == 4
            await run.approve(handle, "assets_review")
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert (result.outcome, result.revision) == ("completed", 1)
    ids = await scheduled(handle)
    order = [
        "reconcile_rooms:r1:vote",
        "transition:rooms_review:r1",
        "transition:components_processing:r1",
        "reconcile_rooms:r1",
        "transition:components_review:r1",
        "transition:assets_processing:r1",
        "process_assets:r1",
    ]
    assert [ids.index(i) for i in order] == sorted(ids.index(i) for i in order)


async def test_a_rooms_rejection_regroups_and_the_room_step_uses_the_approved_grouping(
    harness: Harness,
) -> None:
    provider = FixtureVisionProvider()
    run = production(harness, provider)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            kitchen_1, kitchen_2, bathroom_1 = started.study.photo_ids
            await run.approve(handle, "appraisal_review")
            await run.wait_awaiting(handle, "rooms_review", 1)
            r1 = await rooms(run, sid, 1)
            corrections = [
                CorrectionInput(
                    entity_type="photo_room",
                    entity_id=str(kitchen_2),
                    after={"room_id": str(r1["Bathroom"].id)},
                ),
                CorrectionInput(
                    entity_type="room",
                    entity_id=str(r1["Bathroom"].id),
                    after={"name": "Primary Bath"},
                ),
            ]
            await run.decide(
                handle, run.decision("rooms_review", 1, "reject", corrections=corrections)
            )
            await run.wait_awaiting(handle, "rooms_review", 2)
            ids = await scheduled(handle)
            assert not [i for i in ids if i.endswith(":r2") and i.startswith("analyze_photo:")]
            assert not [i for i in ids if i.endswith(":r2") and i.startswith("localize_photo:")]
            assert "reconcile_rooms:r2:vote" in ids and "reconcile_rooms:r2" not in ids
            assert room_calls(provider) == 0
            r2 = await rooms(run, sid, 2)
            assert set(r2) == {"Kitchen", "Primary Bath"}
            placed = {p.document_id: p.room_id for p in await rows(run, PhotoRoom, sid, 2)}
            assert placed == {
                kitchen_1: r2["Kitchen"].id,
                kitchen_2: r2["Primary Bath"].id,
                bathroom_1: r2["Primary Bath"].id,
            }
            assert len(await rows(run, Extraction, sid, 2)) == 1  # carried, not re-extracted
            row = await run.study_row(sid)
            assert (row.current_stage, row.revision) == (Stage.ROOMS_REVIEW, 2)

            await run.approve(handle, "rooms_review", 2)
            await run.wait_awaiting(handle, "components_review", 2)
            built = await inventory(run, sid, 2)
            photos = {c.room_id: set(c.raw["photos"]) for c in built}
            assert photos == {
                r2["Kitchen"].id: {str(kitchen_1)},
                r2["Primary Bath"].id: {str(kitchen_2), str(bathroom_1)},
            }
            assert room_calls(provider) == 1  # the two-photo Primary Bath only
            await run.approve(handle, "components_review", 2)
            await run.approve(handle, "assets_review", 2)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert (result.outcome, result.revision) == ("completed", 2)
    assert {a.revision for a in await rows(run, Asset, sid, 2)} == {2}


async def test_a_components_rejection_reanalyzes_in_the_approved_rooms(harness: Harness) -> None:
    provider = FixtureVisionProvider()
    run = production(harness, provider)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            bathroom_1 = started.study.photo_ids[2]
            await run.approve(handle, "appraisal_review")
            await run.approve(handle, "rooms_review")
            await run.wait_awaiting(handle, "components_review", 1)
            [cabinets] = [c for c in await inventory(run, sid, 1) if c.label == "kitchen_cabinet"]
            corrections = [
                CorrectionInput(
                    entity_type="component", entity_id=str(cabinets.id), after={"count": 8}
                ),
                CorrectionInput(entity_type="photo", entity_id=str(bathroom_1), after={}),
            ]
            await run.decide(
                handle, run.decision("components_review", 1, "reject", corrections=corrections)
            )
            await run.wait_awaiting(handle, "components_review", 2)
            ids = await scheduled(handle)
            assert [i for i in ids if i.startswith("analyze_photo:") and i.endswith(":r2")] == [
                f"analyze_photo:{bathroom_1}:r2"
            ]
            assert "transition:components_processing:r2" in ids
            assert "transition:rooms_review:r2" not in ids  # the grouping stays approved
            r2 = await rooms(run, sid, 2)
            assert set(r2) == {"Kitchen", "Bathroom"}
            assert (await run.study_row(sid)).current_stage is Stage.COMPONENTS_REVIEW
            by_label = {c.label: c for c in await inventory(run, sid, 2)}
            # The kitchen's corrected inventory is carried; the bathroom's is rebuilt.
            assert by_label["kitchen_cabinet"].raw["carried_from"] == str(cabinets.id)
            assert by_label["kitchen_cabinet"].raw["count"] == 8
            assert "carried_from" not in by_label["toilet"].raw
            assert by_label["toilet"].room_id == r2["Bathroom"].id
            await run.approve(handle, "components_review", 2)
            await run.wait_awaiting(handle, "assets_review", 2)
            assert len(await rows(run, Asset, sid, 2)) == 4
            await run.approve(handle, "assets_review", 2)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert (result.outcome, result.revision) == ("completed", 2)


async def test_each_rooms_gate_refuses_the_other_gates_corrections(harness: Harness) -> None:
    """Nothing refused is written to history: the gate keeps waiting and takes a valid
    decision afterwards."""
    async with harness.worker():
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        photo = started.study.photo_ids[0]
        await harness.approve(handle, "appraisal_review")
        await harness.wait_awaiting(handle, "rooms_review", 1)
        [component, *_] = await rows(harness, Component, sid, 1)
        refused_at_rooms = [
            CorrectionInput(entity_type="photo", entity_id=str(photo), after={}),
            CorrectionInput(entity_type="component", entity_id=str(component.id), after={}),
        ]
        for correction in refused_at_rooms:
            with pytest.raises(WorkflowUpdateFailedError) as caught:
                await harness.decide(
                    handle, harness.decision("rooms_review", 1, "reject", corrections=[correction])
                )
            assert "does not accept corrections" in str(caught.value.cause)
        await harness.approve(handle, "rooms_review")
        await harness.wait_awaiting(handle, "components_review", 1)
        room = (await rows(harness, Room, sid, 1))[0]
        move = CorrectionInput(
            entity_type="photo_room", entity_id=str(photo), after={"room_id": str(room.id)}
        )
        with pytest.raises(WorkflowUpdateFailedError):
            await harness.decide(
                handle, harness.decision("components_review", 1, "reject", corrections=[move])
            )
        with pytest.raises(WorkflowUpdateFailedError):  # the rooms review is decided
            await harness.decide(handle, harness.decision("rooms_review", 1))
        status = await harness.status(handle)
        assert (status.awaiting, len(status.decisions_applied)) == ("components_review", 2)
        await harness.approve_rest(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed"
