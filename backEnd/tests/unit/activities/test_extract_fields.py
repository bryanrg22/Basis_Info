"""``extract_fields``: the real pipeline on the synthetic appraisal, offline.

Azure DI is the hand-built fixture (``FixtureAzureDi``), the PDF is
``tests/fixtures/synthetic/urar_sample.pdf`` written to storage by the ``seeded`` fixture,
and the LLM fallback is a ``ScriptedProvider``. Rows are real (Postgres container).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fixtures import load_fixture_json
from lifecycletest_support import SeededStudy, count_rows, make_deps
from llm_fakes import FixtureAzureDi, load_azure_fixture
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.extract_fields import ExtractFields
from basis.db.models import Document, Extraction, ExtractionField, LlmCall
from basis.db.session import session_scope
from basis.extraction.azure_di import AzureDiResult
from basis.extraction.fallback import FallbackAnswer, FallbackValue
from basis.llm import ScriptedProvider, ScriptedTurn, Usage
from basis.llm.errors import ProviderUnavailable
from basis.workflow.contracts import CorrectionInput, ExtractFieldsInput

from .conftest import Env

pytestmark = pytest.mark.integration

GOLDEN: dict[str, Any] = load_fixture_json("urar_sample.expected.json")
GOLDEN_KEYS = {f["key"] for f in GOLDEN["fields"]}


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
        return {f"{row.section}.{row.field}": row for row in rows}


async def extraction(deps: Deps, extraction_id: str) -> Extraction:
    async with session_scope(deps.sessionmaker) as session:
        row = await session.get(Extraction, uuid.UUID(extraction_id))
        assert row is not None
        return row


def with_deps(deps: Deps, **changes: Any) -> Deps:
    values = {
        "llm": deps.llm,
        "azure_di": deps.azure_di,
    }
    values.update(changes)
    return make_deps(deps.sessionmaker, deps.storage, deps.settings, **values)


class StaticAzure:
    def __init__(self, result: AzureDiResult | Exception) -> None:
        self.result = result
        self.calls = 0

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def without_pair(label: str) -> AzureDiResult:
    result = load_azure_fixture()
    pairs = tuple(p for p in result.key_value_pairs if p.key != label)
    return result.model_copy(update={"key_value_pairs": pairs})


async def put_bytes(deps: Deps, seeded: SeededStudy, data: bytes) -> None:
    async with session_scope(deps.sessionmaker) as session:
        document = await session.get(Document, seeded.appraisal_id)
        assert document is not None
        uri = document.gcs_uri
    await deps.storage.put(uri, data, "application/pdf")


async def test_extracts_every_golden_field_from_azure_di(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ExtractFields(deps).run, extract(seeded))
    assert not output.needs_review
    found = await fields(deps, output.extraction_id or "")
    assert GOLDEN_KEYS <= set(found)
    assert output.fields == len(found)
    assert found["subject.property_address"].value_text == "1290 Example Way"
    assert found["listing_and_contract.contract_price"].value_num == Decimal("412000")
    assert found["improvements.gla_sqft"].value_num == Decimal("1850")
    assert str(found["reconciliation.effective_date_of_appraisal"].value_date) == "2026-10-01"
    assert found["improvements.year_built"].page == 3
    assert found["subject.property_address"].source == "azure"
    row = await extraction(deps, output.extraction_id or "")
    assert row.method == "azure_di+regex"
    assert row.raw["azure_di"]["synthetic"] is True
    assert row.raw["fallback"]["status"] == "not_needed"
    assert await count_rows(deps.sessionmaker, LlmCall) == 0
    assert activity_env.heartbeats


async def test_without_azure_the_text_layer_is_used_and_reviewed(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    regex_only = with_deps(deps, azure_di=None, llm=None)
    output = await activity_env.run(ExtractFields(regex_only).run, extract(seeded))
    found = await fields(deps, output.extraction_id or "")
    assert GOLDEN_KEYS <= set(found)
    assert {f.source for k, f in found.items() if k in GOLDEN_KEYS} <= {"regex", "table"}
    row = await extraction(deps, output.extraction_id or "")
    assert row.method == "regex"
    # Regex confidence (0.80) is below the 0.90 gate: without Azure DI a person checks.
    assert output.needs_review and row.needs_review
    assert any(r.startswith("low_confidence:") for r in row.raw["review_reasons"])


async def test_low_confidence_critical_field_needs_review(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    low = with_deps(deps, azure_di=FixtureAzureDi("urar_sample_low_confidence.json"))
    output = await activity_env.run(ExtractFields(low).run, extract(seeded))
    assert output.needs_review
    row = await extraction(deps, output.extraction_id or "")
    assert "low_confidence:improvements.gla_sqft=0.62<0.90" in row.raw["review_reasons"]
    assert row.overall_confidence == pytest.approx(0.62)


async def test_replay_and_reset_do_not_duplicate_or_reanalyze(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    azure = FixtureAzureDi()
    activity = ExtractFields(with_deps(deps, azure_di=azure))
    first = await activity_env.run(activity.run, extract(seeded))
    assert await activity_env.run(activity.run, extract(seeded)) == first
    assert await activity_env.run(activity.run, extract(seeded, key="k-reset")) == first
    assert azure.calls == 1
    assert await count_rows(deps.sessionmaker, Extraction) == 1
    assert await count_rows(deps.sessionmaker, ExtractionField) == first.fields


async def test_a_new_revision_reuses_the_stored_azure_analysis(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    azure = FixtureAzureDi()
    activity = ExtractFields(with_deps(deps, azure_di=azure))
    await activity_env.run(activity.run, extract(seeded))
    rev2 = await activity_env.run(activity.run, extract(seeded, 2))
    assert azure.calls == 1
    row = await extraction(deps, rev2.extraction_id or "")
    assert "reused the Azure DI analysis of an earlier revision" in row.raw["notes"]


async def test_corrections_overwrite_fields_and_clear_the_review(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = ExtractFields(
        with_deps(deps, azure_di=FixtureAzureDi("urar_sample_low_confidence.json"))
    )
    rev1 = await activity_env.run(activity.run, extract(seeded))
    assert rev1.needs_review
    old = await fields(deps, rev1.extraction_id or "")
    corrections = (
        CorrectionInput(
            entity_type="extraction_field",
            entity_id=str(old["improvements.gla_sqft"].id),
            after={"value_num": "1850", "value_text": "1,850 sq ft"},
        ),
        CorrectionInput(
            entity_type="extraction_field",
            entity_id=str(old["listing_and_contract.contract_date"].id),
            after={"value_date": "2026-09-02"},
        ),
    )
    rev2 = await activity_env.run(activity.run, extract(seeded, 2, corrections=corrections))
    assert not rev2.needs_review  # the only failing critical field was corrected
    new = await fields(deps, rev2.extraction_id or "")
    gla = new["improvements.gla_sqft"]
    assert (gla.value_num, gla.source, gla.confidence) == (Decimal("1850"), "manual", 1.0)
    assert str(new["listing_and_contract.contract_date"].value_date) == "2026-09-02"
    assert new["subject.city"].source == "azure"
    # Revision 1 is unchanged.
    assert (await fields(deps, rev1.extraction_id or ""))["improvements.gla_sqft"].source == "azure"


@pytest.mark.parametrize(
    "after",
    [{"value_num": "lots"}, {"value_date": "yesterday"}, {"colour": "red"}],
)
async def test_a_bad_correction_is_not_retried_and_leaves_no_rows(
    deps: Deps, seeded: SeededStudy, activity_env: Env, after: dict[str, str]
) -> None:
    activity = ExtractFields(deps)
    rev1 = await activity_env.run(activity.run, extract(seeded))
    target = (await fields(deps, rev1.extraction_id or ""))["subject.city"]
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


async def test_the_text_layer_fills_what_azure_missed_without_an_llm_call(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = ScriptedProvider([])  # any call fails the test
    missing = StaticAzure(without_pair("Effective Date"))
    activity = ExtractFields(with_deps(deps, azure_di=missing, llm=provider))
    output = await activity_env.run(activity.run, extract(seeded))
    assert provider.requests == []
    found = await fields(deps, output.extraction_id or "")
    date_field = found["reconciliation.effective_date_of_appraisal"]
    assert (date_field.source, str(date_field.value_date)) == ("regex", "2026-10-01")
    # A critical field read only by the regex (0.80) is below the gate.
    assert output.needs_review
    assert await count_rows(deps.sessionmaker, LlmCall) == 0


async def test_fallback_value_is_capped_and_recorded_when_no_reader_found_it(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    # Neither Azure DI nor the text layer can read the GLA: the PDF bytes are the scan
    # (no text layer) and Azure DI lost the pair. The model's answer must appear in the
    # page text Azure DI returned, and its confidence is capped at 0.6.
    scan = await _fixture_bytes("urar_scan_noisy.pdf")
    await put_bytes(deps, seeded, scan)
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                parsed=FallbackAnswer(
                    fields=[
                        FallbackValue(
                            key="improvements.gla_sqft",
                            value="1,850 sq ft",
                            page=3,
                            confidence=0.95,
                        ),
                        FallbackValue(
                            key="reconciliation.final_market_value",
                            value="$9,999,999",
                            page=5,
                            confidence=0.99,
                        ),
                    ]
                ),
                usage=Usage(input_tokens=3000, output_tokens=100),
            )
        ],
        model="gpt-4o-mini",
    )
    missing = StaticAzure(without_pair("Gross Living Area"))
    activity = ExtractFields(with_deps(deps, azure_di=missing, llm=provider))
    output = await activity_env.run(activity.run, extract(seeded))
    assert output.needs_review
    [request] = provider.requests
    content = request.messages[0].content
    assert "improvements.gla_sqft" in content
    assert "reconciliation.final_market_value" not in content.split("<document>")[0]
    found = await fields(deps, output.extraction_id or "")
    gla = found["improvements.gla_sqft"]
    assert (gla.source, gla.value_num) == ("llm", Decimal("1850"))
    assert gla.confidence == pytest.approx(0.6)  # REAL column
    # A value for a field that was not asked for is dropped; Azure DI's value stays.
    assert found["reconciliation.final_market_value"].value_num == Decimal("410000")
    row = await extraction(deps, output.extraction_id or "")
    assert row.raw["fallback"]["status"] == "called"
    assert row.raw["fallback"]["dropped"] == {"reconciliation.final_market_value": "not_asked"}
    async with session_scope(deps.sessionmaker) as session:
        [call] = (await session.scalars(select(LlmCall))).all()
    assert (call.activity, call.prompt_version, call.model) == (
        "extract_fields",
        "appraisal_field_fallback/v1",
        "gpt-4o-mini",
    )
    assert (call.input_tokens, call.output_tokens) == (3000, 100)
    assert call.cost_usd == Decimal("0.000510")
    assert call.study_id == seeded.study_id and call.run_id is not None


async def test_a_scan_without_azure_has_no_text_and_needs_review(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await put_bytes(deps, seeded, await _fixture_bytes("urar_scan_noisy.pdf"))
    provider = ScriptedProvider([])  # no page text: the fallback must not be called
    activity = ExtractFields(with_deps(deps, azure_di=None, llm=provider))
    output = await activity_env.run(activity.run, extract(seeded))
    assert output.needs_review and output.fields == 0
    row = await extraction(deps, output.extraction_id or "")
    assert row.raw["fallback"]["status"] == "no_page_text"
    assert "the PDF has no text layer (scanned)" in row.raw["notes"]
    assert row.overall_confidence == 0.0


@pytest.mark.parametrize(
    ("data", "message"),
    [(b"", "empty"), (b"hello, not a pdf", "not a PDF"), (None, "no pages")],
)
async def test_unreadable_appraisals_are_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env, data: bytes | None, message: str
) -> None:
    payload = data if data is not None else await _fixture_bytes("malformed", "empty.pdf")
    await put_bytes(deps, seeded, payload)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ExtractFields(deps).run, extract(seeded))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert message in str(caught.value)
    assert await count_rows(deps.sessionmaker, Extraction) == 0


async def test_a_missing_file_is_an_explicit_non_retryable_error(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        document = await session.get(Document, seeded.appraisal_id)
        assert document is not None
        await deps.storage.delete(document.gcs_uri)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ExtractFields(deps).run, extract(seeded))
    assert caught.value.non_retryable and "missing from storage" in str(caught.value)


async def test_an_unavailable_azure_di_is_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    down = StaticAzure(ProviderUnavailable("503", provider="azure_di", status=503))
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ExtractFields(with_deps(deps, azure_di=down)).run, extract(seeded))
    assert caught.value.type == "ProviderUnavailable" and not caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Extraction) == 0


async def test_the_injected_appraisal_extracts_like_the_clean_one(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    clean = await activity_env.run(
        ExtractFields(with_deps(deps, azure_di=None)).run, extract(seeded)
    )
    await put_bytes(deps, seeded, await _fixture_bytes("adversarial", "urar_injected.pdf"))
    injected = await activity_env.run(
        ExtractFields(with_deps(deps, azure_di=None)).run, extract(seeded, 2)
    )

    def values(rows: dict[str, ExtractionField]) -> dict[str, Any]:
        return {k: (r.value_text, r.value_num, r.value_date) for k, r in rows.items()}

    assert values(await fields(deps, injected.extraction_id or "")) == values(
        await fields(deps, clean.extraction_id or "")
    )


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


async def _fixture_bytes(*parts: str) -> bytes:
    from fixtures import fixture_path

    return fixture_path(*parts).read_bytes()


def test_golden_file_is_the_one_these_tests_assume() -> None:
    assert len(GOLDEN["fields"]) == 110
    assert json.dumps(GOLDEN["critical_fields"]) == json.dumps(
        [
            "subject.property_address",
            "listing_and_contract.contract_price",
            "improvements.year_built",
            "improvements.gla_sqft",
            "reconciliation.final_market_value",
            "reconciliation.effective_date_of_appraisal",
        ]
    )
