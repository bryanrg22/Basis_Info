"""SAM 3 localization in the StudyWorkflow (``localize_photo`` after each analyzed photo).

The harness's real activities with a ``FakeLocalizer`` bound in ``Deps`` (scripted per
phrase), so the workflow, the activity, the rows and the carry-over across a rejection
are all real; only SAM 3 is replaced.

* happy path: every analyzed photo is localized before the first gate opens, instances
  and a ``done`` status per photo, the study completes;
* a localizer that always fails: every photo recorded ``failed``, nothing flagged, the
  study completes as without localization;
* a rooms rejection that flags one photo re-analyzes and re-localizes that photo only;
  the other photos' instances (an engineer's manual one included) and statuses are carried
  into revision 2 and the rows of revision 1 are unchanged.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections import Counter
from typing import Any

import numpy as np
import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from sqlalchemy import select

from basis.db.models import PhotoLocalization, PhotoObject
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.vision.localize import (
    Box,
    FakeLocalizer,
    Instance,
    Localization,
    LocalizerBadResponse,
    Rle,
)
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel
from basis.workflow.contracts import CorrectionInput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def instance(label: str, score: float, at: int = 0) -> Instance:
    """``at`` places the instance (its own mask and box): two instances of one label with
    the same mask would be suppressed as one object by the localizer's dedupe."""
    mask = np.zeros((8, 8), dtype=bool)
    mask[1:4, 1 + 4 * at : 4 + 4 * at] = True
    x1 = 0.1 + 0.5 * at
    return Instance(
        label=ComponentLabel(label),
        phrase=label,
        score=score,
        box=Box(x1=x1, y1=0.1, x2=x1 + 0.3, y2=0.5),
        mask=Rle.encode(mask),
        area=0.14,
    )


SCRIPT = {
    "house window": [instance("window", 0.9)],  # the v3 window phrase (EVAL_REPORT 19)
    "kitchen cabinet": [instance("kitchen_cabinet", 0.8), instance("kitchen_cabinet", 0.4, at=1)],
    "toilet": [instance("toilet", 0.95)],
}


class AlwaysFailing(FakeLocalizer):
    async def localize(
        self, image: PreparedImage, phrases: Any, *, threshold: float
    ) -> Localization:
        self.calls.append((image.sha256, tuple(p.text for p in phrases), threshold))
        raise LocalizerBadResponse("prediction failed")


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
        task_queue=f"lifecycle-localize-{uuid.uuid4().hex[:12]}",
        handles=[],
    )


async def rows(harness: Harness, model: Any, study_id: uuid.UUID, **where: Any) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return list((await session.scalars(stmt)).all())


async def test_every_analyzed_photo_is_localized_before_the_first_gate(harness: Harness) -> None:
    localizer = FakeLocalizer(script=SCRIPT, seconds_per_call=1.0)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.wait_awaiting(handle, "appraisal_review", 1)
            # The gate opened only after every photo's localization finished.
            statuses = await rows(run, PhotoLocalization, sid, revision=1)
            assert sorted(s.status for s in statuses) == ["done"] * 3
            assert len(localizer.calls) == 3
            await run.approve_all(handle)
            result = await run.result(handle)
        assert result.outcome == "completed" and result.flagged_photos == ()
        sequence = await run.activity_sequence(handle)
        assert Counter(sequence)["localize_photo"] == 3
        for photo in started.study.photo_ids:  # each localization follows its analysis
            ids = [
                i
                for i in await scheduled_ids(handle)
                if i.endswith(f"{photo}:r1")
                and i.split(":")[0] in ("analyze_photo", "localize_photo")
            ]
            assert ids == [f"analyze_photo:{photo}:r1", f"localize_photo:{photo}:r1"]
    finally:
        await run.cleanup()

    objects = await rows(run, PhotoObject, sid, revision=1)
    # Every photo is prompted with the countable set (window, cabinet, ...): a window and two
    # cabinets each; the bathroom also gets its toilet.
    assert Counter(o.label for o in objects) == {"window": 3, "kitchen_cabinet": 6, "toilet": 1}
    assert Counter(o.counted for o in objects) == {True: 7, False: 3}  # the 0.4 cabinets
    assert {o.source for o in objects} == {"model"}


async def test_a_failing_localizer_never_fails_the_photo_or_the_study(harness: Harness) -> None:
    localizer = AlwaysFailing()
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            status = await run.wait_awaiting(handle, "appraisal_review", 1)
            assert status.flagged_photos == () and status.completed_photos == 3
            await run.approve_all(handle)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed" and result.flagged_photos == ()
    statuses = await rows(run, PhotoLocalization, sid, revision=1)
    assert [s.status for s in statuses] == ["failed"] * 3
    assert all("prediction failed" in (s.reason or "") for s in statuses)
    assert await rows(run, PhotoObject, sid) == []


async def test_rooms_reject_relocalizes_only_the_reanalyzed_photo(harness: Harness) -> None:
    localizer = FakeLocalizer(script=SCRIPT)
    run = with_localizer(harness, localizer)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.wait_awaiting(handle, "rooms_review", 1)
            await wait_rows(run, PhotoLocalization, sid, 3, revision=1)  # SAM 3 runs behind
            kitchen, flagged = started.study.photo_ids[0], started.study.photo_ids[2]
            # An engineer's manual box on a kitchen photo, before the rejection.
            async with session_scope(run.sessionmaker) as session:
                await PhotoObjectRepository(session).add(
                    study_id=sid,
                    document_id=kitchen,
                    revision=1,
                    new=NewObject(
                        label="electrical_outlet",
                        x1=0.7,
                        y1=0.7,
                        x2=0.75,
                        y2=0.78,
                        area=0.004,
                        source="manual",
                        created_by=started.study.owner_id,
                        mask_source="none",
                    ),
                )
            assert len(localizer.calls) == 3
            await run.decide(
                handle,
                run.decision(
                    "rooms_review",
                    1,
                    "reject",
                    corrections=[
                        CorrectionInput(entity_type="photo", entity_id=str(flagged), after={})
                    ],
                ),
            )
            await run.wait_awaiting(handle, "rooms_review", 2)
            assert len(localizer.calls) == 4  # only the re-analyzed photo
            ids = await scheduled_ids(handle)
            assert [i for i in ids if i.startswith("localize_photo:") and i.endswith(":r2")] == [
                f"localize_photo:{flagged}:r2"
            ]
            await run.approve(handle, "rooms_review", 2)
            await run.approve(handle, "assets_review", 2)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"

    r1 = await rows(run, PhotoObject, sid, revision=1)
    r2 = await rows(run, PhotoObject, sid, revision=2)
    assert len(r1) == 11  # 10 model rows + the manual one, unchanged
    assert len(r2) == len(r1)
    carried = [o for o in r2 if o.document_id != flagged]
    assert all(o.carried_from is not None for o in carried)
    assert [o.created_by for o in carried if o.source == "manual"] == [started.study.owner_id]
    fresh = [o for o in r2 if o.document_id == flagged]
    assert fresh and all(o.carried_from is None for o in fresh)
    statuses = {s.document_id: s for s in await rows(run, PhotoLocalization, sid, revision=2)}
    assert statuses[flagged].carried_from_revision is None
    assert {statuses[d].carried_from_revision for d in statuses if d != flagged} == {1}


async def wait_rows(
    harness: Harness, model: Any, study_id: uuid.UUID, count: int, **where: Any
) -> list[Any]:
    """The rows once there are ``count`` (localizations finish behind the rooms review)."""
    for _ in range(150):
        found = await rows(harness, model, study_id, **where)
        if len(found) >= count:
            return found
        await asyncio.sleep(0.2)
    raise AssertionError(f"fewer than {count} {model.__name__} rows")


async def scheduled_ids(handle: Any) -> list[str]:
    history = await handle.fetch_history()
    return [
        e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]
