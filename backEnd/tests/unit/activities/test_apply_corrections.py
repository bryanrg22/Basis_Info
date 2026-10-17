"""``apply_corrections``: the next revision = the rejected one's surviving outputs, corrected."""

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
from basis.activities.process_assets import ProcessAssets
from basis.db.models import (
    Asset,
    Component,
    Extraction,
    ExtractionField,
    PhotoRoom,
    Room,
    Study,
)
from basis.db.session import session_scope
from basis.vision.rooms import resolve_aliases
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ApplyCorrectionsInput,
    CorrectionInput,
    ExtractFieldsInput,
    ProcessAssetsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration


@pytest.fixture
async def revision_one(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    """Revision 1 outputs: an extraction and the three photos analyzed."""
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
    return seeded


async def rows(deps: Deps, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


def apply(
    seeded: SeededStudy, stage: str, corrections: tuple[CorrectionInput, ...] = ()
) -> ApplyCorrectionsInput:
    return ApplyCorrectionsInput(
        study_id=str(seeded.study_id),
        stage=stage,  # type: ignore[arg-type]
        from_revision=1,
        to_revision=2,
        corrections=corrections,
    )


async def test_appraisal_reject_carries_photo_outputs_but_not_the_extraction(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    output = await activity_env.run(
        ApplyCorrections(deps).run, apply(revision_one, "appraisal_review")
    )
    assert output.applied == 0
    assert await rows(deps, Extraction, sid, 2) == []  # extract_fields recomputes it
    assert len(await rows(deps, PhotoRoom, sid, 2)) == 3
    assert len(await rows(deps, Component, sid, 2)) == 6
    assert {r.name for r in await rows(deps, Room, sid, 2)} == {"Kitchen", "Bathroom"}


async def test_rooms_reject_applies_room_and_component_corrections_and_skips_flagged(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    rooms = {r.name: r for r in await rows(deps, Room, sid, 1)}
    components = await rows(deps, Component, sid, 1)
    flagged = revision_one.photo_ids[2]  # bathroom_1.jpg
    target_component = next(c for c in components if c.room_id == rooms["Kitchen"].id)
    corrections = (
        CorrectionInput(
            entity_type="room", entity_id=str(rooms["Kitchen"].id), after={"name": "Galley"}
        ),
        CorrectionInput(
            entity_type="component",
            entity_id=str(target_component.id),
            after={"label": "millwork", "material": "oak"},
        ),
        CorrectionInput(entity_type="photo", entity_id=str(flagged), after={}),
    )
    output = await activity_env.run(
        ApplyCorrections(deps).run, apply(revision_one, "rooms_review", corrections)
    )
    assert output.applied == 2

    new_rooms = {r.name: r for r in await rows(deps, Room, sid, 2)}
    assert set(new_rooms) == {"Galley"}  # the bathroom's only photo is flagged
    assert new_rooms["Galley"].source == "manual"
    assignments = await rows(deps, PhotoRoom, sid, 2)
    assert {a.document_id for a in assignments} == set(revision_one.photo_ids[:2])
    assert {a.room_id for a in assignments} == {new_rooms["Galley"].id}
    new_components = await rows(deps, Component, sid, 2)
    assert len(new_components) == 4
    corrected = [c for c in new_components if c.raw.get("corrected")]
    assert len(corrected) == 1
    assert (corrected[0].label, corrected[0].material, corrected[0].confidence) == (
        "millwork",
        "oak",
        1.0,
    )
    assert corrected[0].raw["carried_from"] == str(target_component.id)
    # The extraction is carried for a rooms rejection, fields included.
    [extraction] = await rows(deps, Extraction, sid, 2)
    assert extraction.raw["carried_from"]
    [first] = await rows(deps, Extraction, sid, 1)
    carried = await count_rows(deps.sessionmaker, ExtractionField, extraction_id=extraction.id)
    assert carried == await count_rows(deps.sessionmaker, ExtractionField, extraction_id=first.id)
    # Revision 1 untouched.
    assert {r.name for r in await rows(deps, Room, sid, 1)} == {"Kitchen", "Bathroom"}


async def test_assets_reject_carries_everything(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    await activity_env.run(ApplyCorrections(deps).run, apply(revision_one, "assets_review"))
    assert len(await rows(deps, Extraction, sid, 2)) == 1
    assert len(await rows(deps, PhotoRoom, sid, 2)) == 3
    assert len(await rows(deps, Component, sid, 2)) == 6


@pytest.mark.parametrize(
    ("entity_type", "after"),
    [("room", {"name": "X"}), ("component", {"label": "x"}), ("room", {"colour": "red"})],
)
async def test_bad_targets_are_not_retried_and_write_nothing(
    deps: Deps,
    revision_one: SeededStudy,
    activity_env: Env,
    entity_type: str,
    after: dict[str, str],
) -> None:
    sid = revision_one.study_id
    rooms = await rows(deps, Room, sid, 1)
    entity_id = str(rooms[0].id) if "colour" in after else str(uuid.uuid4())
    bad = (CorrectionInput(entity_type=entity_type, entity_id=entity_id, after=after),)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ApplyCorrections(deps).run, apply(revision_one, "rooms_review", bad))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert await rows(deps, Room, sid, 2) == []
    assert await rows(deps, Extraction, sid, 2) == []


async def test_revisions_must_be_consecutive(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    payload = apply(revision_one, "rooms_review").model_copy(update={"to_revision": 3})
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ApplyCorrections(deps).run, payload)
    assert caught.value.non_retryable


async def test_replay_does_not_copy_twice(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    activity = ApplyCorrections(deps)
    await activity_env.run(activity.run, apply(revision_one, "assets_review"))
    await activity_env.run(activity.run, apply(revision_one, "assets_review"))
    assert len(await rows(deps, Component, revision_one.study_id, 2)) == 6


async def test_every_correction_is_validated_before_anything_is_written(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    async with session_scope(deps.sessionmaker) as session:
        field_id = (await session.scalars(select(ExtractionField.id).limit(1))).one()

    def c(entity_type: str, entity_id: object, after: dict[str, Any]) -> CorrectionInput:
        return CorrectionInput(entity_type=entity_type, entity_id=str(entity_id), after=after)

    cases = [
        ("appraisal_review", c("extraction_field", field_id, {"value_num": "lots"})),
        ("appraisal_review", c("extraction_field", field_id, {"value_num": "NaN"})),
        ("appraisal_review", c("extraction_field", uuid.uuid4(), {})),
        ("assets_review", c("asset", uuid.uuid4(), {})),
        ("rooms_review", c("photo", revision_one.appraisal_id, {})),  # not a photo
        ("appraisal_review", c("room", uuid.uuid4(), {})),  # type not accepted here
    ]
    for stage, correction in cases:
        with pytest.raises(ApplicationError) as caught:
            await activity_env.run(
                ApplyCorrections(deps).run, apply(revision_one, stage, (correction,))
            )
        assert caught.value.type == "ValidationError" and caught.value.non_retryable, correction
        assert await rows(deps, Room, sid, 2) == []
        assert await rows(deps, Extraction, sid, 2) == []


async def test_asset_corrections_are_checked_for_consistency_before_anything_is_written(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """S3: a correction must be a valid classification (domain validators), not just parse.

    A valid asset correction is accepted here and applied by ``process_assets`` for the
    new revision (it recomputes the assets); ``applied`` counts only rows written here.
    """
    sid = revision_one.study_id
    await activity_env.run(
        ProcessAssets(deps).run,
        ProcessAssetsInput(
            study_id=str(sid), revision=1, idempotency_key="k-a1", budget_remaining_usd=1.0
        ),
    )
    async with session_scope(deps.sessionmaker) as session:
        target = (await session.scalars(select(Asset.id).where(Asset.revision == 1))).first()

    def asset(after: dict[str, Any]) -> tuple[CorrectionInput, ...]:
        return (CorrectionInput(entity_type="asset", entity_id=str(target), after=after),)

    for after in (
        {"bucket": "y5", "recovery_period": "27.5"},
        {"bucket": "y39", "irs_section": "1245"},
        {"irs_section": "personal"},
    ):
        with pytest.raises(ApplicationError) as caught:
            await activity_env.run(
                ApplyCorrections(deps).run, apply(revision_one, "assets_review", asset(after))
            )
        assert caught.value.type == "ValidationError", after
        assert await rows(deps, Component, sid, 2) == []
    good = asset({"bucket": "y5", "recovery_period": "5", "irs_section": "1245"})
    output = await activity_env.run(
        ApplyCorrections(deps).run, apply(revision_one, "assets_review", good)
    )
    assert output.applied == 0
    assert len(await rows(deps, Component, sid, 2)) == 6


# --------------------------------------------------------------------------- room merges
# EVAL_REPORT section 26, item 1: the engineer merges rooms at the rooms review.


def merge(source: Room, target: Room | str) -> CorrectionInput:
    into = target if isinstance(target, str) else str(target.id)
    return CorrectionInput(entity_type="room", entity_id=str(source.id), after={"merge_into": into})


async def add_inventory_row(deps: Deps, room: Room) -> None:
    """A reconciled inventory row in ``room`` (no document), as ``reconcile_rooms`` writes."""
    async with session_scope(deps.sessionmaker) as session:
        session.add(
            Component(
                study_id=room.study_id,
                revision=1,
                document_id=None,
                room_id=room.id,
                label="kitchen_cabinet",
                raw={"source": "room_inventory", "count": 6},
            )
        )


async def test_a_merge_moves_photos_and_rows_and_records_an_alias(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    rooms = {r.name: r for r in await rows(deps, Room, sid, 1)}
    kitchen, bathroom = rooms["Kitchen"], rooms["Bathroom"]
    assert (kitchen.group_key, bathroom.group_key) == ("x_kitchen", "x_bathroom")
    await add_inventory_row(deps, kitchen)
    output = await activity_env.run(
        ApplyCorrections(deps).run, apply(revision_one, "rooms_review", (merge(bathroom, kitchen),))
    )
    assert output.applied == 1
    [only] = await rows(deps, Room, sid, 2)  # the bathroom is merged into the kitchen
    assert (only.name, only.group_key, only.source) == ("Kitchen", "x_kitchen", "vision")
    assignments = await rows(deps, PhotoRoom, sid, 2)
    assert {a.document_id for a in assignments} == set(revision_one.photo_ids)
    assert {a.room_id for a in assignments} == {only.id}
    components = await rows(deps, Component, sid, 2)
    assert len(components) == 6 and {c.room_id for c in components} == {only.id}
    # Neither room's inventory is carried: reconcile_rooms rebuilds the merged room.
    assert all(c.document_id is not None for c in components)
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, sid)
        assert study is not None and study.room_aliases == {"x_bathroom": "x_kitchen"}
    # Revision 1 is history.
    assert {r.name for r in await rows(deps, Room, sid, 1)} == {"Kitchen", "Bathroom"}


async def test_a_merged_room_is_rebuilt_by_the_room_step_and_reanalysis_joins_it(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """After the merge a photo of the merged room re-analyzed in revision 2 joins the
    target (the alias), instead of recreating the merged room."""
    sid = revision_one.study_id
    rooms = {r.name: r for r in await rows(deps, Room, sid, 1)}
    flagged = revision_one.photo_ids[2]  # the bathroom photo, re-analyzed
    corrections = (
        merge(rooms["Bathroom"], rooms["Kitchen"]),
        CorrectionInput(entity_type="photo", entity_id=str(flagged), after={}),
    )
    await activity_env.run(
        ApplyCorrections(deps).run, apply(revision_one, "rooms_review", corrections)
    )
    output = await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(flagged),
            revision=2,
            idempotency_key="k-p-again",
            budget_remaining_usd=1.0,
        ),
    )
    [only] = await rows(deps, Room, sid, 2)
    assert output.room_id == str(only.id)


async def test_merge_chains_are_followed(deps: Deps, activity_env: Env, storage: Any) -> None:
    names = ("Den.jpg", "Nook.jpg", "Kitchen.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for index, photo in enumerate(study.photo_ids):
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(study.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-chain-{index}",
                budget_remaining_usd=1.0,
            ),
        )
    by_key = {r.group_key: r for r in await rows(deps, Room, study.study_id, 1)}
    corrections = (
        merge(by_key["x_den"], by_key["x_nook"]),
        merge(by_key["x_nook"], by_key["x_kitchen"]),
    )
    output = await activity_env.run(
        ApplyCorrections(deps).run, apply(study, "rooms_review", corrections)
    )
    assert output.applied == 2
    [only] = await rows(deps, Room, study.study_id, 2)
    assert only.group_key == "x_kitchen"
    assert len(await rows(deps, PhotoRoom, study.study_id, 2)) == 3
    async with session_scope(deps.sessionmaker) as session:
        row = await session.get(Study, study.study_id)
        assert row is not None
        assert resolve_aliases(row.room_aliases) == {"x_den": "x_kitchen", "x_nook": "x_kitchen"}


async def test_a_target_keyed_by_the_model_adopts_the_merged_file_name_key(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    study = await seed_study(deps.sessionmaker, photos=("IMG_0001.jpg", "Den.jpg"), storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for index, photo in enumerate(study.photo_ids):
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(study.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-adopt-{index}",
                budget_remaining_usd=1.0,
            ),
        )
    by_key = {r.group_key: r for r in await rows(deps, Room, study.study_id, 1)}
    await activity_env.run(
        ApplyCorrections(deps).run,
        apply(study, "rooms_review", (merge(by_key["x_den"], by_key[None]),)),
    )
    [only] = await rows(deps, Room, study.study_id, 2)
    assert (only.name, only.group_key) == ("Bedroom", "x_den")
    async with session_scope(deps.sessionmaker) as session:
        row = await session.get(Study, study.study_id)
        assert row is not None and row.room_aliases == {}  # nothing to alias it to


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda k, b: (merge(k, k),), "into itself"),
        (lambda k, b: (merge(k, b), merge(b, k)), "loop"),
        (lambda k, b: (merge(k, str(uuid.uuid4())),), "not in the rejected revision"),
        (
            lambda k, b: (
                CorrectionInput(
                    entity_type="room",
                    entity_id=str(k.id),
                    after={"merge_into": str(b.id), "name": "x"},
                ),
            ),
            "no other field",
        ),
        (
            lambda k, b: (
                CorrectionInput(
                    entity_type="room", entity_id=str(k.id), after={"merge_into": "nope"}
                ),
            ),
            "merge_into",
        ),
    ],
)
async def test_bad_merges_are_refused_before_anything_is_written(
    deps: Deps, revision_one: SeededStudy, activity_env: Env, make: Any, message: str
) -> None:
    sid = revision_one.study_id
    rooms = {r.name: r for r in await rows(deps, Room, sid, 1)}
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(
            ApplyCorrections(deps).run,
            apply(revision_one, "rooms_review", make(rooms["Kitchen"], rooms["Bathroom"])),
        )
    assert raised.value.type == "ValidationError" and raised.value.non_retryable
    assert message in str(raised.value)
    assert await rows(deps, Room, sid, 2) == []
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, sid)
        assert study is not None and study.room_aliases == {}
