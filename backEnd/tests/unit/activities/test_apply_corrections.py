"""``apply_corrections``: the next revision = the rejected one's surviving outputs, corrected."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycletest_support import SeededStudy, count_rows
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.extract_fields import ExtractFields
from basis.db.models import Component, Extraction, ExtractionField, PhotoRoom, Room
from basis.db.session import session_scope
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
    assert await count_rows(deps.sessionmaker, ExtractionField, extraction_id=extraction.id) == 5
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
