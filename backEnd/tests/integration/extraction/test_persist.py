"""Persisting an extraction: typed ``extraction_fields`` columns and the raw payload."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from dbtest_support import db_settings, truncate_all
from fixtures import fixture_path
from lifecycletest_support import seed_study
from llm_fakes import FixtureAzureDi
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.engine import create_engine
from basis.db.models import Extraction, ExtractionField
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.session import create_sessionmaker, session_scope
from basis.extraction.persist import persist_extraction, stored_azure_result
from basis.extraction.pipeline import ExtractionContext, extract_appraisal
from basis.extraction.results import ExtractionResult, Region
from basis.llm import Budget
from basis.llm.call import CallContext
from basis.llm.prompts import latest
from basis.llm.recorder import MemoryRecorder

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_settings(database_url))
    await truncate_all(engine)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def extracted(azure: bool = True) -> ExtractionResult:
    ctx = ExtractionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="extract_fields",
            budget=Budget(remaining_usd=1.0),
            recorder=MemoryRecorder(),
        ),
        provider=None,
        azure=FixtureAzureDi() if azure else None,
        prompt=latest("appraisal_field_fallback"),
        text_model="gpt-4o-mini",
    )
    return await extract_appraisal(fixture_path("urar_sample.pdf").read_bytes(), "d", ctx)


async def test_fields_land_in_typed_columns(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sessionmaker, photos=())
    assert study.appraisal_id is not None
    result = await extracted()
    async with session_scope(sessionmaker) as session:
        extraction = await persist_extraction(
            session,
            study_id=study.study_id,
            document_id=study.appraisal_id,
            revision=1,
            result=result,
        )
        extraction_id = extraction.id
    async with session_scope(sessionmaker) as session:
        row = await session.get(Extraction, extraction_id)
        assert row is not None
        fields = {
            f"{f.section}.{f.field}": f
            for f in await session.scalars(
                select(ExtractionField).where(ExtractionField.extraction_id == extraction_id)
            )
        }
    assert (row.method, row.needs_review, row.revision) == ("azure_di+regex", False, 1)
    assert row.overall_confidence == pytest.approx(result.overall_confidence, abs=1e-6)
    assert len(fields) == len(result.fields)

    price = fields["listing_and_contract.contract_price"]
    assert (price.value_text, price.value_num, price.value_date) == (
        "$412,000",
        Decimal("412000"),
        None,
    )
    effective = fields["reconciliation.effective_date_of_appraisal"]
    assert effective.value_date == date(2026, 10, 1) and effective.value_num is None
    address = fields["subject.property_address"]
    assert (address.value_text, address.value_num, address.source, address.page) == (
        "1290 Example Way",
        None,
        "azure",
        1,
    )
    baths = fields["improvements.bathrooms"]
    assert baths.value_num == Decimal("2.1")
    flood = fields["site.special_flood_hazard"]
    assert (flood.value_text, flood.value_num) == ("No", Decimal(0))
    # "Not Developed" is not a value (basis.extraction.presence): no row, the reason in raw
    assert "reconciliation.indicated_value_income_approach" not in fields
    assert row.raw["presence"]["reconciliation.indicated_value_income_approach"] == (
        "not_developed:no_number"
    )
    assert {f.source for f in fields.values()} == {"azure"}

    # The stored Azure DI analysis round-trips, so a later revision can reuse it.
    reused = stored_azure_result(row.raw)
    assert reused is not None and len(reused.key_value_pairs) == 102
    assert row.raw["review_threshold"] == 0.9


async def test_one_extraction_per_document_revision(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sessionmaker, photos=())
    assert study.appraisal_id is not None
    result = await extracted(azure=False)
    async with session_scope(sessionmaker) as session:
        await persist_extraction(
            session,
            study_id=study.study_id,
            document_id=study.appraisal_id,
            revision=1,
            result=result,
        )
    with pytest.raises(IntegrityError):
        async with session_scope(sessionmaker) as session:
            await persist_extraction(
                session,
                study_id=study.study_id,
                document_id=study.appraisal_id,
                revision=1,
                result=result,
            )
    async with session_scope(sessionmaker) as session:
        repo = ExtractionRepository(session)
        existing = await repo.get_for_revision(study.appraisal_id, 1)
        assert existing is not None and existing.method == "regex"
        assert await repo.latest_before(study.appraisal_id, 2) == existing
        assert await repo.latest_before(study.appraisal_id, 1) is None
        assert stored_azure_result(existing.raw) is None  # nothing to reuse without Azure DI


def test_stored_azure_result_ignores_other_payloads() -> None:
    assert stored_azure_result(None) is None
    assert stored_azure_result({"azure_di": "nope"}) is None
    assert stored_azure_result({"azure_di": {"pages": "bad"}}) is None


def test_a_stored_analysis_without_geometry_is_analyzed_again() -> None:
    """A real analysis stored before word boxes were kept would give the new revision's
    fields no highlight regions: it is not reused (a fixture, synthetic, still is)."""
    page = {"page_number": 1, "lines": ["Property Address 1290 Example Way"]}
    old = {"azure_di": {"pages": [page], "synthetic": False}}
    assert stored_azure_result(old) is None
    fixture = {"azure_di": {"pages": [page], "synthetic": True}}
    assert stored_azure_result(fixture) is not None
    word = {"text": "1290", "bbox": [0.1, 0.1, 0.15, 0.12]}
    current = {"azure_di": {"pages": [{**page, "words": [word]}], "synthetic": False}}
    reused = stored_azure_result(current)
    assert reused is not None and reused.pages[0].words[0].text == "1290"


async def test_regions_are_stored_and_a_correction_keeps_them(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sessionmaker, photos=())
    assert study.appraisal_id is not None
    result = await extracted()
    key = next(iter(sorted(result.fields)))
    item = result.fields[key]
    where = Region(page=1, bbox=(0.123456, 0.2, 0.3, 0.25))
    result = result.model_copy(
        update={"fields": {**result.fields, key: item.model_copy(update={"regions": (where,)})}}
    )
    async with session_scope(sessionmaker) as session:
        extraction = await persist_extraction(
            session,
            study_id=study.study_id,
            document_id=study.appraisal_id,
            revision=1,
            result=result,
        )
        extraction_id = extraction.id
    expected = [{"page": 1, "bbox": [0.1235, 0.2, 0.3, 0.25]}]  # 4 decimals
    async with session_scope(sessionmaker) as session:
        rows = {
            f"{f.section}.{f.field}": f
            for f in await ExtractionRepository(session).fields(extraction_id)
        }
        assert rows[key].regions == expected
        assert all(r.regions == [] for k, r in rows.items() if k != key)
        # A reviewer's correction (regions=None) changes the value, not where it is printed.
        extraction_row = await session.get(Extraction, extraction_id)
        assert extraction_row is not None
        await ExtractionRepository(session).set_field(
            extraction=extraction_row,
            section=item.section,
            field=item.field,
            value_text="corrected",
            confidence=1.0,
            source="manual",
        )
    async with session_scope(sessionmaker) as session:
        rows = {
            f"{f.section}.{f.field}": f
            for f in await ExtractionRepository(session).fields(extraction_id)
        }
        assert (rows[key].value_text, rows[key].regions) == ("corrected", expected)
