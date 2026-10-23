"""``apply_corrections`` with the components gate (patch ``components-gate-v1``).

The rooms review takes the grouping (``room`` fields and merges, ``photo_room`` moves) and
the components review the components (``component`` with ``count``, ``room_component``,
``photo`` re-analysis). What each rejection carries is the module docstring's table; these
tests pin the two new rows and the carry-over of an engineer's work across both gates: a
moved photo's rows, manual photo objects, added room components and count corrections.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycletest_support import SeededStudy, count_rows, seed_run, seed_study
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.extract_fields import ExtractFields
from basis.activities.reconcile_rooms import plan_rooms
from basis.db.models import Component, Extraction, LlmCall, PhotoObject, PhotoRoom, Room
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.vision.rooms import is_room_inventory, is_room_manual
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ApplyCorrectionsInput,
    CorrectionInput,
    ExtractFieldsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration


@pytest.fixture
async def revision_one(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    """Revision 1: an extraction, the three photos analyzed (two kitchen photos, one
    bathroom photo), each photo localized with one model object, the second kitchen photo
    with an engineer's manual object too, and a reconciled kitchen inventory row."""
    await activity_env.run(
        ExtractFields(deps).run,
        ExtractFieldsInput(
            study_id=str(seeded.study_id),
            document_id=str(seeded.appraisal_id),
            revision=1,
            idempotency_key="k-x1",
        ),
    )
    for photo in seeded.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(seeded.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    async with session_scope(deps.sessionmaker) as session:
        repo = PhotoObjectRepository(session)
        for photo in seeded.photo_ids:
            await repo.record_localization(
                study_id=seeded.study_id, document_id=photo, revision=1, status="done"
            )
            await repo.add(study_id=seeded.study_id, document_id=photo, revision=1, new=box())
        await repo.add(
            study_id=seeded.study_id,
            document_id=seeded.photo_ids[1],
            revision=1,
            new=box(source="manual", created_by=seeded.owner_id, mask_source="none"),
        )
        kitchen = next(
            r for r in await rows_in(session, Room, seeded.study_id, 1) if r.name == "Kitchen"
        )
        session.add(
            Component(
                study_id=seeded.study_id,
                revision=1,
                document_id=None,
                room_id=kitchen.id,
                label="kitchen_cabinet",
                raw={
                    "source": "room_inventory",
                    "count": 6,
                    "photos": [str(p) for p in seeded.photo_ids[:2]],
                },
            )
        )
    return seeded


def box(**changes: Any) -> NewObject:
    values: dict[str, Any] = {
        "label": "window",
        "x1": 0.1,
        "y1": 0.1,
        "x2": 0.4,
        "y2": 0.4,
        "area": 0.09,
        "source": "model",
        "score": 0.9,
    }
    return NewObject(**(values | changes))


async def rows_in(session: Any, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    stmt = select(model).where(model.study_id == study_id, model.revision == revision)
    return list((await session.scalars(stmt)).all())


async def rows(deps: Deps, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        return await rows_in(session, model, study_id, revision)


async def objects(deps: Deps, study_id: uuid.UUID, revision: int) -> list[PhotoObject]:
    return [o for o in await rows(deps, PhotoObject, study_id, revision) if o.deleted_at is None]


def gated(
    seeded: SeededStudy,
    stage: str,
    corrections: tuple[CorrectionInput, ...] = (),
    from_revision: int = 1,
) -> ApplyCorrectionsInput:
    return ApplyCorrectionsInput(
        study_id=str(seeded.study_id),
        stage=stage,  # type: ignore[arg-type]
        from_revision=from_revision,
        to_revision=from_revision + 1,
        corrections=corrections,
        components_gate=True,
    )


def move(photo: uuid.UUID, room: Room | str, **extra: Any) -> CorrectionInput:
    target = room if isinstance(room, str) else str(room.id)
    return CorrectionInput(
        entity_type="photo_room", entity_id=str(photo), after={"room_id": target, **extra}
    )


async def by_name(deps: Deps, study_id: uuid.UUID, revision: int) -> dict[str, Room]:
    return {r.name: r for r in await rows(deps, Room, study_id, revision)}


async def assigned(deps: Deps, study_id: uuid.UUID, revision: int) -> dict[uuid.UUID, uuid.UUID]:
    return {a.document_id: a.room_id for a in await rows(deps, PhotoRoom, study_id, revision)}


# --------------------------------------------------------------------------- rooms review


async def test_a_move_takes_the_photo_and_its_rows_to_the_target_room(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    kitchen_photo, moved, bathroom_photo = revision_one.photo_ids
    rooms = await by_name(deps, sid, 1)
    output = await activity_env.run(
        ApplyCorrections(deps).run,
        gated(revision_one, "rooms_review", (move(moved, rooms["Bathroom"]),)),
    )
    assert output.applied == 1
    new = await by_name(deps, sid, 2)
    assert set(new) == {"Kitchen", "Bathroom"}
    assert await assigned(deps, sid, 2) == {
        kitchen_photo: new["Kitchen"].id,
        moved: new["Bathroom"].id,
        bathroom_photo: new["Bathroom"].id,
    }
    components = await rows(deps, Component, sid, 2)
    assert {c.room_id for c in components if c.document_id == moved} == {new["Bathroom"].id}
    assert {c.room_id for c in components if c.document_id == kitchen_photo} == {new["Kitchen"].id}
    # The kitchen lost a photo and the bathroom gained one: neither inventory is carried.
    assert not [c for c in components if is_room_inventory(c.raw, c.document_id)]
    # Objects do not depend on the room: every photo's are carried, the manual one too.
    carried = await objects(deps, sid, 2)
    assert len(carried) == 4 and sum(o.source == "manual" for o in carried) == 1
    # The extraction is carried (nothing recomputes it after a rooms rejection).
    assert len(await rows(deps, Extraction, sid, 2)) == 1
    # Revision 1 is history.
    assert (await assigned(deps, sid, 1))[moved] == rooms["Kitchen"].id


async def test_moving_a_rooms_last_photo_drops_the_room_and_the_vote_sees_the_change(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """The bathroom's only photo moves to the kitchen: the bathroom is gone, and the room
    step plans the kitchen again (its photo list changed)."""
    sid = revision_one.study_id
    bathroom_photo = revision_one.photo_ids[2]
    rooms = await by_name(deps, sid, 1)
    await activity_env.run(
        ApplyCorrections(deps).run,
        gated(revision_one, "rooms_review", (move(bathroom_photo, rooms["Kitchen"]),)),
    )
    new = await by_name(deps, sid, 2)
    assert set(new) == {"Kitchen"}
    assert set((await assigned(deps, sid, 2)).values()) == {new["Kitchen"].id}
    async with session_scope(deps.sessionmaker) as session:
        plan = await plan_rooms(session, sid, 2)
    assert [p.room.id for p in plan.todo] == [new["Kitchen"].id]
    assert len(plan.todo[0].photos) == 3


async def test_a_move_to_its_own_room_changes_nothing(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    rooms = await by_name(deps, sid, 1)
    output = await activity_env.run(
        ApplyCorrections(deps).run,
        gated(revision_one, "rooms_review", (move(revision_one.photo_ids[0], rooms["Kitchen"]),)),
    )
    assert output.applied == 0
    # Nothing moved, so the kitchen's inventory row is carried.
    inventory = [
        c for c in await rows(deps, Component, sid, 2) if is_room_inventory(c.raw, c.document_id)
    ]
    assert len(inventory) == 1


async def test_a_move_into_a_merged_room_lands_in_the_merge_target(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    kitchen_photo = revision_one.photo_ids[0]
    rooms = await by_name(deps, sid, 1)
    merge = CorrectionInput(
        entity_type="room",
        entity_id=str(rooms["Bathroom"].id),
        after={"merge_into": str(rooms["Kitchen"].id)},
    )
    rename = CorrectionInput(
        entity_type="room", entity_id=str(rooms["Kitchen"].id), after={"name": "Galley"}
    )
    await activity_env.run(
        ApplyCorrections(deps).run,
        gated(
            revision_one, "rooms_review", (merge, rename, move(kitchen_photo, rooms["Bathroom"]))
        ),
    )
    [only] = await rows(deps, Room, sid, 2)
    assert (only.name, only.source) == ("Galley", "manual")
    assert set((await assigned(deps, sid, 2)).values()) == {only.id}


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda s, r: (move(s.photo_ids[0], str(uuid.uuid4())),), "not in the rejected revision"),
        (lambda s, r: (move(s.photo_ids[0], r["Bathroom"], name="x"),), "exactly room_id"),
        (lambda s, r: (move(s.appraisal_id, r["Bathroom"]),), "is not a photo of this study"),
        (
            lambda s, r: (
                CorrectionInput(entity_type="component", entity_id=str(uuid.uuid4()), after={}),
            ),
            "does not accept component",
        ),
        (
            lambda s, r: (
                CorrectionInput(entity_type="photo", entity_id=str(s.photo_ids[0]), after={}),
            ),
            "does not accept photo",
        ),
    ],
)
async def test_bad_rooms_review_corrections_are_refused_before_anything_is_written(
    deps: Deps, revision_one: SeededStudy, activity_env: Env, make: Any, message: str
) -> None:
    sid = revision_one.study_id
    rooms = await by_name(deps, sid, 1)
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(
            ApplyCorrections(deps).run,
            gated(revision_one, "rooms_review", make(revision_one, rooms)),
        )
    assert raised.value.type == "ValidationError" and raised.value.non_retryable
    assert message in str(raised.value)
    assert await rows(deps, Room, sid, 2) == []
    assert await rows(deps, Extraction, sid, 2) == []


async def test_a_photo_with_no_room_cannot_be_moved(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    """A photo whose analysis failed has no room in the revision; it is flagged for
    re-analysis at the components review instead."""
    study = await seed_study(deps.sessionmaker, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for photo in study.photo_ids[:2]:  # the bathroom photo is never analyzed
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(study.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-n-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    [kitchen] = await rows(deps, Room, study.study_id, 1)
    with pytest.raises(ApplicationError, match="has no room in the rejected revision"):
        await activity_env.run(
            ApplyCorrections(deps).run,
            gated(study, "rooms_review", (move(study.photo_ids[2], kitchen),)),
        )


async def test_without_the_gate_a_move_is_refused(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    rooms = await by_name(deps, revision_one.study_id, 1)
    single = gated(
        revision_one, "rooms_review", (move(revision_one.photo_ids[0], rooms["Bathroom"]),)
    )
    with pytest.raises(ApplicationError, match="does not accept photo_room"):
        await activity_env.run(
            ApplyCorrections(deps).run, single.model_copy(update={"components_gate": False})
        )


# --------------------------------------------------------------------------- components review


async def test_a_flagged_photo_keeps_its_approved_room_and_is_reanalyzed_into_it(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """After a move at the rooms review (r1 -> r2) the moved photo is flagged at the
    components review (r2 -> r3): its assignment stays, its rows, objects and its room's
    inventory are left for the re-analysis, and ``keep_room`` writes the new analysis into
    the approved room instead of the one its file name names."""
    sid = revision_one.study_id
    moved = revision_one.photo_ids[1]  # a kitchen photo, moved to the bathroom
    rooms = await by_name(deps, sid, 1)
    await activity_env.run(
        ApplyCorrections(deps).run,
        gated(revision_one, "rooms_review", (move(moved, rooms["Bathroom"]),)),
    )
    activity_env.with_info(activity_id="apply-components")
    flag = CorrectionInput(entity_type="photo", entity_id=str(moved), after={})
    await activity_env.run(
        ApplyCorrections(deps).run,
        gated(revision_one, "components_review", (flag,), from_revision=2),
    )
    r3 = await by_name(deps, sid, 3)
    assert set(r3) == {"Kitchen", "Bathroom"}
    assert (await assigned(deps, sid, 3))[moved] == r3["Bathroom"].id
    assert not [c for c in await rows(deps, Component, sid, 3) if c.document_id == moved]
    assert {o.document_id for o in await objects(deps, sid, 3)} == set(revision_one.photo_ids) - {
        moved
    }

    output = await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(moved),
            revision=3,
            idempotency_key="k-p-r3",
            budget_remaining_usd=1.0,
            keep_room=True,
        ),
    )
    assert output.room_id == str(r3["Bathroom"].id) and output.components > 0
    assert set(await by_name(deps, sid, 3)) == {"Kitchen", "Bathroom"}  # no room recreated
    written = [c for c in await rows(deps, Component, sid, 3) if c.document_id == moved]
    assert {c.room_id for c in written} == {r3["Bathroom"].id}
    # A second execution of the same activity returns the stored analysis (run_once) and
    # makes no model call.
    calls = await count_rows(deps.sessionmaker, LlmCall, study_id=sid)
    assert calls > 0  # the analyses so far are recorded
    again = await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(moved),
            revision=3,
            idempotency_key="k-p-r3",
            budget_remaining_usd=1.0,
            keep_room=True,
        ),
    )
    assert again == output
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=sid) == calls
    assert len([c for c in await rows(deps, Component, sid, 3) if c.document_id == moved]) == len(
        written
    )


async def test_work_at_both_gates_is_carried_into_every_later_revision(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """r1 -> r2 rooms rejection (a rename), r2 -> r3 components rejection (a count and an
    added component), r3 -> r4 components rejection with nothing: the rename, the count,
    the added component and the manual photo object are all in r4."""
    sid = revision_one.study_id
    rooms = await by_name(deps, sid, 1)
    rename = CorrectionInput(
        entity_type="room", entity_id=str(rooms["Kitchen"].id), after={"name": "Galley"}
    )
    await activity_env.run(
        ApplyCorrections(deps).run, gated(revision_one, "rooms_review", (rename,))
    )
    r2 = await by_name(deps, sid, 2)
    [inventory] = [
        c for c in await rows(deps, Component, sid, 2) if is_room_inventory(c.raw, c.document_id)
    ]
    count = CorrectionInput(
        entity_type="component", entity_id=str(inventory.id), after={"count": 8}
    )
    added = CorrectionInput(
        entity_type="room_component",
        entity_id=str(r2["Bathroom"].id),
        after={"label": "electrical_outlet", "count": 2},
    )
    activity_env.with_info(activity_id="apply-r2")
    output = await activity_env.run(
        ApplyCorrections(deps).run,
        gated(revision_one, "components_review", (count, added), from_revision=2),
    )
    assert output.applied == 2
    activity_env.with_info(activity_id="apply-r3")
    await activity_env.run(
        ApplyCorrections(deps).run, gated(revision_one, "components_review", from_revision=3)
    )
    r4 = await by_name(deps, sid, 4)
    assert set(r4) == {"Galley", "Bathroom"} and r4["Galley"].source == "manual"
    components = await rows(deps, Component, sid, 4)
    [counted] = [c for c in components if is_room_inventory(c.raw, c.document_id)]
    assert (counted.raw["count"], counted.room_id) == (8, r4["Galley"].id)
    [manual] = [c for c in components if is_room_manual(c.raw, c.document_id)]
    assert (manual.label, manual.raw["count"], manual.room_id) == (
        "electrical_outlet",
        2,
        r4["Bathroom"].id,
    )
    carried = await objects(deps, sid, 4)
    assert len(carried) == 4 and sum(o.source == "manual" for o in carried) == 1
    # The room step plans nothing: every inventory still names its room's photos.
    async with session_scope(deps.sessionmaker) as session:
        plan = await plan_rooms(session, sid, 4)
    assert r4["Galley"].id not in {p.room.id for p in plan.todo}


@pytest.mark.parametrize(
    ("entity_type", "after"),
    [("room", {"name": "Galley"}), ("photo_room", {"room_id": None})],
)
async def test_the_components_review_refuses_grouping_corrections(
    deps: Deps, revision_one: SeededStudy, activity_env: Env, entity_type: str, after: Any
) -> None:
    sid = revision_one.study_id
    rooms = await by_name(deps, sid, 1)
    if entity_type == "room":
        target = str(rooms["Kitchen"].id)
    else:
        target, after = str(revision_one.photo_ids[0]), {"room_id": str(rooms["Bathroom"].id)}
    bad = (CorrectionInput(entity_type=entity_type, entity_id=target, after=after),)
    with pytest.raises(ApplicationError, match=f"does not accept {entity_type}"):
        await activity_env.run(
            ApplyCorrections(deps).run, gated(revision_one, "components_review", bad)
        )
    assert await rows(deps, Room, sid, 2) == []
