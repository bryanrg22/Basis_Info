"""``extract_fields`` (stub): five typed fields; corrections applied; no partial rows."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from lifecycletest_support import SeededStudy, count_rows
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.extract_fields import ExtractFields
from basis.db.models import Extraction, ExtractionField
from basis.db.session import session_scope
from basis.workflow.contracts import CorrectionInput, ExtractFieldsInput

from .conftest import Env

pytestmark = pytest.mark.integration


def extract(
    seeded: SeededStudy,
    revision: int = 1,
    key: str | None = None,
    document_id: str | None = "appraisal",
    corrections: tuple[CorrectionInput, ...] = (),
) -> ExtractFieldsInput:
    return ExtractFieldsInput(
        study_id=str(seeded.study_id),
        document_id=str(seeded.appraisal_id) if document_id == "appraisal" else document_id,
        revision=revision,
        idempotency_key=key or f"k-extract-{revision}",
        corrections=corrections,
    )


async def fields(deps: Deps, extraction_id: str) -> dict[str, ExtractionField]:
    async with session_scope(deps.sessionmaker) as session:
        rows = await session.scalars(
            select(ExtractionField).where(ExtractionField.extraction_id == uuid.UUID(extraction_id))
        )
        return {row.field: row for row in rows}


async def test_writes_five_fields_from_the_document(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ExtractFields(deps).run, extract(seeded))
    assert output.fields == 5 and not output.needs_review
    found = await fields(deps, output.extraction_id or "")
    assert set(found) == {
        "file_name",
        "property_name",
        "document_format",
        "appraised_value",
        "effective_date",
    }
    assert found["file_name"].value_text == "appraisal_12_main_st.pdf"
    assert found["property_name"].value_text == "Appraisal 12 Main St"
    assert found["document_format"].value_text == "PDF"
    assert isinstance(found["appraised_value"].value_num, Decimal)
    assert found["effective_date"].value_date is not None
    assert {f.source for f in found.values()} == {"stub"}
    assert len(activity_env.heartbeats) == 5


async def test_replay_and_reset_do_not_duplicate(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = ExtractFields(deps)
    first = await activity_env.run(activity.run, extract(seeded))
    assert await activity_env.run(activity.run, extract(seeded)) == first
    assert await activity_env.run(activity.run, extract(seeded, key="k-reset")) == first
    assert await count_rows(deps.sessionmaker, Extraction) == 1
    assert await count_rows(deps.sessionmaker, ExtractionField) == 5


async def test_corrections_overwrite_fields_in_the_new_revision(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = ExtractFields(deps)
    rev1 = await activity_env.run(activity.run, extract(seeded))
    old = await fields(deps, rev1.extraction_id or "")
    corrections = (
        CorrectionInput(
            entity_type="extraction_field",
            entity_id=str(old["appraised_value"].id),
            after={"value_num": "1250000"},
        ),
        CorrectionInput(
            entity_type="extraction_field",
            entity_id=str(old["effective_date"].id),
            after={"value_date": "2026-01-31"},
        ),
    )
    rev2 = await activity_env.run(activity.run, extract(seeded, 2, corrections=corrections))
    new = await fields(deps, rev2.extraction_id or "")
    assert new["appraised_value"].value_num == Decimal("1250000")
    assert (new["appraised_value"].source, new["appraised_value"].confidence) == ("manual", 1.0)
    assert str(new["effective_date"].value_date) == "2026-01-31"
    assert new["file_name"].source == "stub"
    # Revision 1 is unchanged.
    assert (await fields(deps, rev1.extraction_id or ""))["appraised_value"].source == "stub"


@pytest.mark.parametrize(
    "after",
    [{"value_num": "lots"}, {"value_date": "yesterday"}, {"colour": "red"}],
)
async def test_a_bad_correction_is_not_retried_and_leaves_no_rows(
    deps: Deps, seeded: SeededStudy, activity_env: Env, after: dict[str, str]
) -> None:
    activity = ExtractFields(deps)
    rev1 = await activity_env.run(activity.run, extract(seeded))
    target = (await fields(deps, rev1.extraction_id or ""))["file_name"]
    bad = (CorrectionInput(entity_type="extraction_field", entity_id=str(target.id), after=after),)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(activity.run, extract(seeded, 2, corrections=bad))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Extraction, revision=2) == 0


async def test_unknown_correction_target_is_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    bad = (CorrectionInput(entity_type="extraction_field", entity_id=str(uuid.uuid4()), after={}),)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ExtractFields(deps).run, extract(seeded, corrections=bad))
    assert caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Extraction) == 0


async def test_no_document_means_needs_review(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ExtractFields(deps).run, extract(seeded, document_id=None))
    assert (output.extraction_id, output.fields, output.needs_review) == (None, 0, True)


async def test_a_photo_is_not_an_appraisal(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(
            ExtractFields(deps).run, extract(seeded, document_id=str(seeded.photo_ids[0]))
        )
    assert caught.value.non_retryable
