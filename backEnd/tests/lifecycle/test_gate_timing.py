"""The appraisal review opens when the extraction is done (EVAL_REPORT section 27, item 4).

Live, the appraisal gate waited for SAM 3 localization (74% of the wall time under
Replicate's throttle) although it reviews only the extraction, done at +94 s. With patch
``appraisal-gate-early-v1``:

* the appraisal review opens while the photos are still being analyzed and localized
  (timing: in history order, the gate's transition completes before any photo finishes);
* the rooms review opens only after the whole photo stage (every analysis, every
  localization, the room step);
* an appraisal rejection while photos are in flight waits for them, so the next revision
  carries every photo's outputs.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from sqlalchemy import select
from temporalio import activity

from basis.activities.analyze_photo import AnalyzePhoto
from basis.db.enums import Stage, StudyStatus
from basis.db.models import PhotoRoom
from basis.db.session import session_scope
from basis.workflow.contracts import AnalyzePhotoInput, AnalyzePhotoOutput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class HeldPhoto:
    """``analyze_photo`` whose first photo waits until the test releases it (heartbeating),
    so a photo is certainly still in flight when the appraisal gate opens."""

    def __init__(self, harness: Harness, held: str) -> None:
        self.real = AnalyzePhoto(harness.deps)
        self.held = held
        self.release = asyncio.Event()

    @activity.defn(name="analyze_photo")
    async def run(self, input: AnalyzePhotoInput) -> AnalyzePhotoOutput:
        if input.document_id == self.held:
            while not self.release.is_set():
                activity.heartbeat("held")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.release.wait(), 1.0)
        return await self.real.run(input)


async def activity_events(handle: Any) -> list[tuple[str, str]]:
    """``(what, activity id)`` for every activity scheduled or completed, in order."""
    history = await handle.fetch_history()
    ids: dict[int, str] = {}
    events: list[tuple[str, str]] = []
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            ids[event.event_id] = event.activity_task_scheduled_event_attributes.activity_id
            events.append(("scheduled", ids[event.event_id]))
        elif event.HasField("activity_task_completed_event_attributes"):
            scheduled = event.activity_task_completed_event_attributes.scheduled_event_id
            events.append(("completed", ids[scheduled]))
    return events


def first(events: list[tuple[str, str]], what: str, prefix: str) -> int:
    return next(i for i, (w, a) in enumerate(events) if w == what and a.startswith(prefix))


def last(events: list[tuple[str, str]], what: str, prefix: str) -> int:
    return max(i for i, (w, a) in enumerate(events) if w == what and a.startswith(prefix))


async def assigned(harness: Harness, study_id: uuid.UUID, revision: int) -> int:
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.scalars(
            select(PhotoRoom).where(PhotoRoom.study_id == study_id, PhotoRoom.revision == revision)
        )
        return len(rows.all())


async def test_the_appraisal_gate_opens_while_photos_run_and_the_rooms_gate_after_them(
    harness: Harness,
) -> None:
    study = await harness.seed()
    held = HeldPhoto(harness, str(study.photo_ids[0]))
    async with harness.worker(analyze_photo=held.run):
        started = await harness.start(study)
        handle, sid = started.handle, started.study_id
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        # Open before the photo stage is done: the held photo is still in its analysis.
        assert status.pending_photos >= 1
        held.release.set()
        row = await harness.study_row(sid)
        assert (row.current_stage, row.status) == (
            Stage.APPRAISAL_REVIEW,
            StudyStatus.WAITING_REVIEW,
        )
        await harness.approve(handle, "appraisal_review")
        status = await harness.wait_awaiting(handle, "rooms_review", 1)
        assert (status.pending_photos, status.completed_photos) == (0, 3)
        assert await assigned(harness, sid, 1) == 3
        await harness.approve(handle, "rooms_review")
        await harness.approve(handle, "assets_review")
        result = await harness.result(handle)
    assert result.outcome == "completed"
    events = await activity_events(handle)
    gate_1 = first(events, "completed", "transition:appraisal_review:r1")
    gate_2 = first(events, "scheduled", "transition:rooms_review:r1")
    # Timing: the appraisal gate opened right after the extraction, before the photo
    # stage finished (the slow photo's analysis completed later) ...
    assert first(events, "completed", "extract_fields:r1") < gate_1
    assert gate_1 < last(events, "completed", "analyze_photo:")
    # ... and the rooms gate only after every analysis, localization and the room step.
    assert last(events, "completed", "analyze_photo:") < gate_2
    assert last(events, "completed", "localize_photo:") < gate_2
    assert last(events, "completed", "reconcile_rooms:r1") < gate_2


async def test_an_appraisal_rejection_waits_for_the_photos_it_carries_over(
    harness: Harness,
) -> None:
    study = await harness.seed()
    held = HeldPhoto(harness, str(study.photo_ids[0]))
    async with harness.worker(analyze_photo=held.run):
        started = await harness.start(study)
        handle, sid = started.handle, started.study_id
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        assert status.pending_photos >= 1  # rejected while a photo is still in flight
        await harness.decide(handle, harness.decision("appraisal_review", 1, "reject"))
        held.release.set()
        await harness.wait_awaiting(handle, "appraisal_review", 2)
        # Revision 2 was started after the photo stage finished: it carries all three.
        assert await assigned(harness, sid, 1) == 3
        assert await assigned(harness, sid, 2) == 3
        await harness.approve_all(handle, revision=2)
        result = await harness.result(handle)
    assert (result.outcome, result.revision) == ("completed", 2)
    events = await activity_events(handle)
    assert last(events, "completed", "reconcile_rooms:r1") < first(
        events, "scheduled", "apply_corrections:appraisal_review:r1"
    )
