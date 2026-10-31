"""``apply_corrections`` with the two row-level corrections of the engineer workspace.

``component_remove`` (basis.domain.removals): the named component row of the rejected
revision is not copied into the next one, so every later revision lacks it; the photo's
localized objects are never touched; a removed added row keeps no room alive; the removal is
an ``entity.removed`` event; what the rejection would undo is refused before anything is
written. ``extraction_field_add`` (basis.extraction.added_fields) is validated here and
applied by ``extract_fields`` (tests/unit/activities/test_extract_fields.py).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycletest_support import SeededStudy
from sqlalchemy import select, update
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.extract_fields import ExtractFields
from basis.db.models import (
    Component,
    Correction,
    Extraction,
    ExtractionField,
    PhotoObject,
    Room,
    StudyEvent,
)
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.extraction.field_mappings import FIELD_BY_KEY, FIELDS
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, ROOM_MANUAL_SOURCE, is_room_inventory
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ApplyCorrectionsInput,
    CorrectionInput,
    ExtractFieldsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration

REASON = "Not in the property."


@pytest.fixture
async def revision_one(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    """Revision 1: an extraction, the three photos analyzed (two kitchen photos, one
    bathroom photo), each photo localized with one model object, and two reconciled kitchen
    inventory rows (cabinets and a window)."""
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
            await repo.add(
                study_id=seeded.study_id,
                document_id=photo,
                revision=1,
                new=NewObject(
                    label="window",
                    x1=0.1,
                    y1=0.1,
                    x2=0.4,
                    y2=0.4,
                    area=0.09,
                    source="model",
                    score=0.9,
                ),
            )
        kitchen = next(
            r
            for r in (await session.scalars(select(Room).where(Room.revision == 1))).all()
            if r.name == "Kitchen"
        )
        photos = [str(p) for p in seeded.photo_ids[:2]]
        session.add_all(
            [
                Component(
                    study_id=seeded.study_id,
                    revision=1,
                    document_id=None,
                    room_id=kitchen.id,
                    label=label,
                    raw={"source": ROOM_INVENTORY_SOURCE, "count": count, "photos": photos},
                )
                for label, count in (("kitchen_cabinet", 6), ("window", 2))
            ]
        )
    return seeded


async def rows(deps: Deps, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


async def events(deps: Deps, study_id: uuid.UUID, event_type: str) -> list[StudyEvent]:
    async with session_scope(deps.sessionmaker) as session:
        found = await session.scalars(
            select(StudyEvent)
            .where(StudyEvent.study_id == study_id, StudyEvent.event_type == event_type)
            .order_by(StudyEvent.seq)
        )
        return list(found.all())


def gated(
    seeded: SeededStudy,
    corrections: tuple[CorrectionInput, ...] = (),
    *,
    stage: str = "components_review",
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


def removal(component_id: uuid.UUID, reason: str | None = REASON, **after: Any) -> CorrectionInput:
    return CorrectionInput(
        entity_type="component_remove", entity_id=str(component_id), after=after, reason=reason
    )


async def inventory(deps: Deps, study_id: uuid.UUID, revision: int) -> dict[str, Component]:
    return {
        c.label: c
        for c in await rows(deps, Component, study_id, revision)
        if is_room_inventory(c.raw, c.document_id)
    }


async def record_correction(
    deps: Deps, seeded: SeededStudy, correction: CorrectionInput
) -> uuid.UUID:
    """The ``corrections`` row the decision route would have stored at revision 1."""
    async with session_scope(deps.sessionmaker) as session:
        row = Correction(
            study_id=seeded.study_id,
            entity_type=correction.entity_type,
            entity_id=uuid.UUID(correction.entity_id),
            revision=1,
            after=correction.after,
            reason=correction.reason,
            reviewer_id=seeded.owner_id,
        )
        session.add(row)
        await session.flush()
        return row.id


async def test_a_removed_inventory_row_is_left_out_and_the_objects_stay(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    old = await inventory(deps, sid, 1)
    window = old["window"]
    correction = removal(window.id)
    correction_id = await record_correction(deps, revision_one, correction)
    output = await activity_env.run(ApplyCorrections(deps).run, gated(revision_one, (correction,)))
    assert output.applied == 1
    new = await inventory(deps, sid, 2)
    assert set(new) == {"kitchen_cabinet"}  # the window row is gone, the cabinets carried
    assert new["kitchen_cabinet"].raw["carried_from"] == str(old["kitchen_cabinet"].id)
    # Every per-photo row is carried, and so is every localized object (never touched).
    per_photo_1 = [c for c in await rows(deps, Component, sid, 1) if c.document_id is not None]
    per_photo_2 = [c for c in await rows(deps, Component, sid, 2) if c.document_id is not None]
    assert len(per_photo_2) == len(per_photo_1)
    objects_1 = await rows(deps, PhotoObject, sid, 1)
    objects_2 = await rows(deps, PhotoObject, sid, 2)
    assert [
        (o.document_id, o.label, o.deleted_at)
        for o in sorted(objects_2, key=lambda o: str(o.document_id))
    ] == [
        (o.document_id, o.label, None) for o in sorted(objects_1, key=lambda o: str(o.document_id))
    ]
    # Revision 1 is history: the removed row is still there.
    assert set(await inventory(deps, sid, 1)) == {"kitchen_cabinet", "window"}
    [event] = await events(deps, sid, "entity.removed")
    assert (event.actor_type, event.actor_id, event.revision) == (
        "engineer",
        revision_one.owner_id,
        2,
    )
    assert (event.entity_type, event.entity_id, event.after) == ("component", window.id, None)
    assert event.before == {
        "label": "window",
        "material": None,
        "attachment": None,
        "count": 2,
        "room_id": str(window.room_id),
        "document_id": None,
    }
    assert event.detail == {
        "carried_from": None,
        "correction_ids": [str(correction_id)],
        "reasons": [REASON],
        "from_revision": 1,
        "kind": "room_inventory",
    }
    [carried] = await events(deps, sid, "revision.carried")
    assert carried.detail == {"from_revision": 1, "applied": 1}


async def test_a_removed_row_stays_absent_across_later_rejections(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """Each revision copies from the one before, so a removal carries forward; a second
    rejection can remove another row of its own revision."""
    sid = revision_one.study_id
    window = (await inventory(deps, sid, 1))["window"]
    await activity_env.run(ApplyCorrections(deps).run, gated(revision_one, (removal(window.id),)))
    photo_row = next(c for c in await rows(deps, Component, sid, 2) if c.document_id is not None)
    activity_env.with_info(activity_id="apply-2")
    second = await activity_env.run(
        ApplyCorrections(deps).run, gated(revision_one, (removal(photo_row.id),), from_revision=2)
    )
    assert second.applied == 1
    assert set(await inventory(deps, sid, 3)) == {"kitchen_cabinet"}
    rows_3 = await rows(deps, Component, sid, 3)
    assert str(photo_row.id) not in {c.raw.get("carried_from") for c in rows_3}
    activity_env.with_info(activity_id="apply-3")
    await activity_env.run(
        ApplyCorrections(deps).run, gated(revision_one, (), stage="assets_review", from_revision=3)
    )
    assert set(await inventory(deps, sid, 4)) == {"kitchen_cabinet"}
    assert len(await rows(deps, Component, sid, 4)) == len(rows_3)
    kinds = [e.detail["kind"] for e in await events(deps, sid, "entity.removed")]
    assert kinds == ["room_inventory", "per_photo"]


async def test_a_removed_added_row_keeps_no_room_alive(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """A room with no photos that exists only for an engineer's added component (its photos
    moved out earlier): removing that row drops the room from the next revision."""
    sid = revision_one.study_id
    async with session_scope(deps.sessionmaker) as session:
        closet = Room(study_id=sid, revision=1, name="Closet", room_type="closet", source="vision")
        session.add(closet)
        await session.flush()
        added = Component(
            study_id=sid,
            revision=1,
            document_id=None,
            room_id=closet.id,
            label="shelving",
            raw={"source": ROOM_MANUAL_SOURCE, "count": 1, "room_from": str(closet.id)},
        )
        session.add(added)
        await session.flush()
        added_id = added.id
    await activity_env.run(ApplyCorrections(deps).run, gated(revision_one, (removal(added_id),)))
    assert {r.name for r in await rows(deps, Room, sid, 2)} == {"Kitchen", "Bathroom"}
    [event] = await events(deps, sid, "entity.removed")
    assert (event.entity_id, event.detail["kind"]) == (added_id, "room_manual")


async def test_the_single_rooms_review_removes_rows_too(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    window = (await inventory(deps, sid, 1))["window"]
    output = await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="rooms_review",
            from_revision=1,
            to_revision=2,
            corrections=(removal(window.id),),
        ),
    )
    assert output.applied == 1
    assert set(await inventory(deps, sid, 2)) == {"kitchen_cabinet"}


@pytest.mark.parametrize(
    "case",
    [
        "no_reason",
        "blank_reason",
        "fields",
        "twice",
        "and_corrected",
        "unknown",
        "flagged_room",
        "flagged_photo_row",
        "last_inventory_row",
    ],
)
async def test_a_removal_the_rejection_would_undo_is_refused_before_anything_is_written(
    deps: Deps, revision_one: SeededStudy, activity_env: Env, case: str
) -> None:
    sid = revision_one.study_id
    rows_1 = await inventory(deps, sid, 1)
    window, cabinets = rows_1["window"], rows_1["kitchen_cabinet"]
    kitchen_photo = revision_one.photo_ids[0]
    photo_row = next(
        c for c in await rows(deps, Component, sid, 1) if c.document_id == kitchen_photo
    )
    flag = CorrectionInput(entity_type="photo", entity_id=str(kitchen_photo), after={})
    count = CorrectionInput(entity_type="component", entity_id=str(window.id), after={"count": 3})
    corrections, code, fragment = {
        "no_reason": ((removal(window.id, None),), E.CORRECT_REASON_REQUIRED, "needs a reason"),
        "blank_reason": ((removal(window.id, "  "),), E.CORRECT_REASON_REQUIRED, "reason"),
        "fields": ((removal(window.id, count=1),), E.CORRECT_REMOVE_SHAPE, "takes no fields"),
        "twice": ((removal(window.id), removal(window.id)), E.CORRECT_REMOVE_CONFLICT, "twice"),
        "and_corrected": ((count, removal(window.id)), E.CORRECT_REMOVE_CONFLICT, "corrected"),
        "unknown": ((removal(uuid.uuid4()),), E.CORRECT_TARGET_NOT_IN_REVISION, "not in the"),
        "flagged_room": ((flag, removal(window.id)), E.CORRECT_REMOVE_REBUILT, "rebuilds"),
        "flagged_photo_row": (
            (flag, removal(photo_row.id)),
            E.CORRECT_REMOVE_REBUILT,
            "re-analyzes",
        ),
        "last_inventory_row": (
            (removal(window.id), removal(cabinets.id)),
            E.CORRECT_REMOVE_LAST_INVENTORY,
            "last inventory row",
        ),
    }[case]
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ApplyCorrections(deps).run, gated(revision_one, corrections))
    assert raised.value.type == "ValidationError" and raised.value.non_retryable
    assert f"[{code}]" in str(raised.value) and fragment in str(raised.value)
    assert await rows(deps, Room, sid, 2) == []
    assert await rows(deps, Component, sid, 2) == []
    assert await rows(deps, Extraction, sid, 2) == []
    assert await events(deps, sid, "entity.removed") == []


# --------------------------------------------------------------------------- added fields


async def extraction_of(deps: Deps, study_id: uuid.UUID, revision: int) -> Extraction:
    [found] = await rows(deps, Extraction, study_id, revision)
    return found


def field_add(
    extraction_id: uuid.UUID, reason: str | None = REASON, **after: Any
) -> CorrectionInput:
    values = {"key": "improvements.year_built", "value_num": 1987, "page": 1, "evidence": "1987"}
    return CorrectionInput(
        entity_type="extraction_field_add",
        entity_id=str(extraction_id),
        after={**values, **after},
        reason=reason,
    )


async def free_key(deps: Deps, extraction: Extraction) -> str:
    """A text key of the catalogue the extraction lacks."""
    async with session_scope(deps.sessionmaker) as session:
        keys = {
            f"{f.section}.{f.field}"
            for f in (
                await session.scalars(
                    select(ExtractionField).where(ExtractionField.extraction_id == extraction.id)
                )
            ).all()
        }
    return next(s.key for s in FIELDS if s.type == "text" and s.key not in keys)


async def test_a_valid_added_field_is_validated_and_left_to_extract_fields(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    extraction = await extraction_of(deps, sid, 1)
    key = await free_key(deps, extraction)
    output = await activity_env.run(
        ApplyCorrections(deps).run,
        gated(
            revision_one,
            (field_add(extraction.id, key=key, value_num=None, value_text="Yes"),),
            stage="appraisal_review",
        ),
    )
    assert output.applied == 0  # applied by extract_fields
    assert await rows(deps, Extraction, sid, 2) == []


@pytest.mark.parametrize(
    "case",
    ["no_reason", "unknown_key", "exists", "page", "twice", "other_revision", "wrong_type"],
)
async def test_a_bad_added_field_is_refused_before_anything_is_written(
    deps: Deps, revision_one: SeededStudy, activity_env: Env, case: str
) -> None:
    sid = revision_one.study_id
    extraction = await extraction_of(deps, sid, 1)
    key = await free_key(deps, extraction)
    good: dict[str, Any] = {"key": key, "value_num": None, "value_text": "Yes"}
    existing = next(
        key
        for f in await rows_of_fields(deps, extraction.id)
        if (key := f"{f.section}.{f.field}") in FIELD_BY_KEY and FIELD_BY_KEY[key].type == "text"
    )
    if case == "page":
        async with session_scope(deps.sessionmaker) as session:
            await session.execute(
                update(Extraction)
                .where(Extraction.id == extraction.id)
                .values(raw={**extraction.raw, "page_count": 2})
            )
    corrections, code = {
        "no_reason": ((field_add(extraction.id, None, **good),), E.CORRECT_REASON_REQUIRED),
        "unknown_key": (
            (field_add(extraction.id, **{**good, "key": "subject.nope"}),),
            E.CORRECT_FIELD_ADD_UNKNOWN_KEY,
        ),
        "exists": (
            (field_add(extraction.id, **{**good, "key": existing}),),
            E.CORRECT_FIELD_ADD_EXISTS,
        ),
        "page": ((field_add(extraction.id, **good, page=3),), E.CORRECT_FIELD_ADD_PAGE),
        "twice": (
            (field_add(extraction.id, **good), field_add(extraction.id, **good)),
            E.CORRECT_FIELD_ADD_DUPLICATE,
        ),
        "other_revision": (
            (field_add(uuid.uuid4(), **good),),
            E.CORRECT_EXTRACTION_NOT_IN_REVISION,
        ),
        "wrong_type": (
            (field_add(extraction.id, value_num=None, value_text="1987"),),
            E.CORRECT_FIELD_ADD_TYPE,
        ),
    }[case]
    assert existing in FIELD_BY_KEY  # so "exists" is refused as such, not as an unknown key
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(
            ApplyCorrections(deps).run, gated(revision_one, corrections, stage="appraisal_review")
        )
    assert raised.value.type == "ValidationError" and raised.value.non_retryable
    assert f"[{code}]" in str(raised.value)
    assert await rows(deps, Room, sid, 2) == []


async def rows_of_fields(deps: Deps, extraction_id: uuid.UUID) -> list[ExtractionField]:
    async with session_scope(deps.sessionmaker) as session:
        found = await session.scalars(
            select(ExtractionField).where(ExtractionField.extraction_id == extraction_id)
        )
        return list(found.all())
