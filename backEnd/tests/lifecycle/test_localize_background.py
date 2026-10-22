"""SAM 3 behind the rooms review (EVAL_REPORT section 30, patch ``localize-background-v1``).

Live, the rooms review waited for every SAM 3 localization although nothing it shows but
the masks comes from them (the room step, the assets and the export read the per-photo
analysis). With the patch:

* the rooms review opens once the photos are analyzed and the rooms voted, while a
  localization is still running (a ``FakeLocalizer`` that holds one photo until the test
  releases it); its row appears when it finishes. With the components gate (patch
  ``components-gate-v1``) the room step runs as soon as the grouping is approved and the
  components review waits for every localization; with the single rooms review the study
  does not export before every localization is recorded;
* ``rooms_gate_waits_for_localization`` restores the wait: the gate opens only after the
  held localization;
* a rooms rejection waits for the localizations of the revision it carries over, so the
  next revision has every photo's objects;
* at most ``localize_concurrency`` localizations run at once.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from sqlalchemy import select

from basis.db.models import PhotoLocalization, PhotoObject, PhotoRoom, Room
from basis.db.session import session_scope
from basis.vision.localize import FakeLocalizer, Localization, Phrase
from basis.vision.prep import PreparedImage
from basis.workflow.contracts import CorrectionInput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class HeldLocalizer(FakeLocalizer):
    """Localizes every photo at once except the ``held``-th one it is asked for (an image
    hash is not known in advance), which waits for ``release``; tracks concurrency."""

    def __init__(self, held: int | None = 0, pause_s: float = 0.0) -> None:
        super().__init__()
        self.held = held
        self.pause_s = pause_s
        self.release = asyncio.Event()
        self.seen = 0
        self.running = 0
        self.peak = 0
        self.finished: list[str] = []

    async def localize(
        self,
        image: PreparedImage,
        phrases: Sequence[Phrase],
        *,
        threshold: float,
        dedupe: bool = True,
    ) -> Localization:
        index, self.seen = self.seen, self.seen + 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if index == self.held:
                await self.release.wait()
            await asyncio.sleep(self.pause_s)
            return await super().localize(image, phrases, threshold=threshold, dedupe=dedupe)
        finally:
            self.running -= 1
            self.finished.append(image.sha256)


def with_localizer(harness: Harness, localizer: FakeLocalizer) -> Harness:
    return dataclasses.replace(
        harness,
        deps=make_deps(
            harness.sessionmaker,
            harness.deps.storage,
            harness.settings,
            chunk_store=harness.deps.chunk_store,
            localizer=localizer,
        ),
        task_queue=f"lifecycle-localize-bg-{uuid.uuid4().hex[:12]}",
        handles=[],
    )


async def localized(harness: Harness, study_id: uuid.UUID, revision: int) -> dict[uuid.UUID, Any]:
    async with session_scope(harness.sessionmaker) as session:
        found = await session.scalars(
            select(PhotoLocalization).where(
                PhotoLocalization.study_id == study_id, PhotoLocalization.revision == revision
            )
        )
        return {row.document_id: row for row in found.all()}


async def wait_localized(
    harness: Harness, study_id: uuid.UUID, revision: int, count: int, timeout: float = 30.0
) -> dict[uuid.UUID, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        rows = await localized(harness, study_id, revision)
        if len(rows) >= count or loop.time() > deadline:
            return rows
        await asyncio.sleep(0.2)


async def activity_events(handle: Any) -> list[tuple[str, str]]:
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


def at(events: list[tuple[str, str]], what: str, prefix: str, pick: Any = min) -> int:
    return int(pick(i for i, (w, a) in enumerate(events) if w == what and a.startswith(prefix)))


async def test_the_rooms_gate_opens_while_sam3_still_runs(harness: Harness) -> None:
    """Patch components-gate-v1: the rooms review opens and is approved while a
    localization is still held; the room step runs at once, and the components review opens
    only when the held localization is recorded (it shows the outlines)."""
    localizer = HeldLocalizer(held=0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            status = await run.wait_awaiting(handle, "rooms_review", 1)
            # Open with every photo analyzed and one localization still held.
            assert (status.pending_photos, status.completed_photos) == (0, 3)
            assert len(await wait_localized(run, sid, 1, 2)) == 2
            assert localizer.running == 1
            await run.approve(handle, "rooms_review")
            await asyncio.sleep(1.0)
            status = await run.status(handle)
            assert (status.stage, status.awaiting) == ("components_processing", None)
            row = await run.study_row(sid)
            assert (row.current_stage.value, row.status.value) == (
                "components_processing",
                "processing",
            )
            localizer.release.set()
            await run.wait_awaiting(handle, "components_review", 1)
            rows = await localized(run, sid, 1)
            assert sorted(r.status for r in rows.values()) == ["done"] * 3
            await run.approve(handle, "components_review")
            await run.approve(handle, "assets_review")
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed" and result.flagged_photos == ()
    events = await activity_events(handle)
    gate = at(events, "scheduled", "transition:rooms_review:r1")
    assert at(events, "completed", "analyze_photo:", max) < gate
    assert at(events, "completed", "reconcile_rooms:r1:vote") < gate
    assert gate < at(events, "completed", "localize_photo:", max)  # the held one, later
    # The room step did not wait for the held localization; the components gate did.
    step = events.index(("completed", "reconcile_rooms:r1"))
    components = at(events, "scheduled", "transition:components_review:r1")
    assert step < at(events, "completed", "localize_photo:", max) < components


async def test_the_study_does_not_export_before_every_localization_single_rooms_gate(
    harness: Harness,
) -> None:
    """The single rooms review (an execution without the components gate) opens while a
    localization is held and is approved straight through: the workflow waits for it
    before exporting, so the completed study has every photo's row."""
    localizer = HeldLocalizer(held=0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start(components_gate=False)
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.approve(handle, "rooms_review")
            await run.approve(handle, "assets_review")
            await asyncio.sleep(1.0)
            assert (await run.status(handle)).stage == "assets_review"  # held before export
            localizer.release.set()
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    assert len(await localized(run, sid, 1)) == 3
    events = await activity_events(handle)
    assert "transition:components_processing:r1" not in {a for _, a in events}


async def test_the_study_does_not_export_before_every_localization(harness: Harness) -> None:
    """With the components gate a held localization holds the components review (above);
    every later stage sees every localization, and the completed study has every row."""
    localizer = HeldLocalizer(held=0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.approve(handle, "rooms_review")
            localizer.release.set()
            await run.approve_rest(handle)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    assert len(await localized(run, sid, 1)) == 3
    events = await activity_events(handle)
    assert at(events, "completed", "localize_photo:", max) < at(
        events, "scheduled", "transition:components_review:r1"
    )


async def test_the_setting_restores_the_wait_for_sam3(harness: Harness) -> None:
    localizer = HeldLocalizer(held=0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start(rooms_gate_waits_for_localization=True)
            handle = started.handle
            await run.approve(handle, "appraisal_review")
            await run.wait_photos(handle, 3)
            await asyncio.sleep(1.5)
            assert (await run.status(handle)).awaiting is None  # still waiting for SAM 3
            localizer.release.set()
            await run.approve(handle, "rooms_review")
            await run.approve_rest(handle)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    events = await activity_events(handle)
    assert at(events, "completed", "localize_photo:", max) < at(
        events, "scheduled", "transition:rooms_review:r1"
    )


async def test_a_rooms_rejection_waits_for_the_localizations_it_carries(harness: Harness) -> None:
    """Patch components-gate-v1: a move at the rooms review waits for the held
    localization, then revision 2 carries every photo's objects (none is re-analyzed, so
    none is re-localized: objects do not depend on the room)."""
    localizer = HeldLocalizer(held=0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.wait_awaiting(handle, "rooms_review", 1)
            missing = set(started.study.photo_ids) - set(await localized(run, sid, 1))
            assert len(missing) == 1  # the held photo
            [held] = missing
            moved = next(p for p in started.study.photo_ids if p != held)
            async with session_scope(run.sessionmaker) as session:
                rooms = (
                    await session.scalars(
                        select(Room).where(Room.study_id == sid, Room.revision == 1)
                    )
                ).all()
                current = await session.get(PhotoRoom, (moved, 1))
                assert current is not None
            target = next(r for r in rooms if r.id != current.room_id)
            correction = CorrectionInput(
                entity_type="photo_room", entity_id=str(moved), after={"room_id": str(target.id)}
            )
            await run.decide(
                handle, run.decision("rooms_review", 1, "reject", corrections=[correction])
            )
            await asyncio.sleep(1.0)
            assert (await run.status(handle)).revision == 1  # waiting for the held photo
            localizer.release.set()
            await run.wait_awaiting(handle, "rooms_review", 2)
            rows = await localized(run, sid, 2)
            await run.approve(handle, "rooms_review", 2)
            await run.approve_rest(handle, 2)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert (result.outcome, result.revision) == ("completed", 2)
    assert set(rows) == set(started.study.photo_ids)
    assert all(r.carried_from_revision == 1 for r in rows.values())
    assert localizer.seen == 3  # nothing localized again
    events = await activity_events(handle)
    assert at(events, "completed", f"localize_photo:{held}:r1") < at(
        events, "scheduled", "apply_corrections:rooms_review:r1"
    )


async def test_a_rooms_rejection_waits_for_the_localizations_it_carries_single_rooms_gate(
    harness: Harness,
) -> None:
    """The single rooms review: a flagged photo is re-analyzed and re-localized."""
    localizer = HeldLocalizer(held=0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start(components_gate=False)
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.wait_awaiting(handle, "rooms_review", 1)
            missing = set(started.study.photo_ids) - set(await localized(run, sid, 1))
            assert len(missing) == 1  # the held photo
            [held] = missing
            flagged = next(p for p in started.study.photo_ids if p != held)
            correction = CorrectionInput(entity_type="photo", entity_id=str(flagged), after={})
            await run.decide(
                handle, run.decision("rooms_review", 1, "reject", corrections=[correction])
            )
            await asyncio.sleep(1.0)
            assert (await run.status(handle)).revision == 1  # waiting for the held photo
            localizer.release.set()
            await run.wait_awaiting(handle, "rooms_review", 2)
            rows = await wait_localized(run, sid, 2, 3)
            await run.approve(handle, "rooms_review", 2)
            await run.approve(handle, "assets_review", 2)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert (result.outcome, result.revision) == ("completed", 2)
    # Revision 2 carried the held photo's localization (it finished before the carry) and
    # re-localized the flagged photo.
    assert rows[held].carried_from_revision == 1 and rows[held].status == "done"
    assert rows[flagged].carried_from_revision is None
    events = await activity_events(handle)
    assert at(events, "completed", f"localize_photo:{held}:r1") < at(
        events, "scheduled", "apply_corrections:rooms_review:r1"
    )
    async with session_scope(run.sessionmaker) as session:
        r2 = (
            await session.scalars(
                select(PhotoObject).where(
                    PhotoObject.study_id == sid,
                    PhotoObject.revision == 2,
                    PhotoObject.document_id == held,
                )
            )
        ).all()
    assert all(o.carried_from is not None for o in r2)


@pytest.mark.parametrize("slots", [1, 2])
async def test_localizations_run_at_most_localize_concurrency_at_once(
    harness: Harness, slots: int
) -> None:
    localizer = HeldLocalizer(held=None, pause_s=0.3)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start(localize_concurrency=slots, photo_concurrency=5)
            handle = started.handle
            await run.approve_all(handle)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    assert localizer.seen == 3 and localizer.peak == slots
