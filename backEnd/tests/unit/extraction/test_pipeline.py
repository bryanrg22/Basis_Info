"""``extract_appraisal`` end to end on fixtures: Azure DI JSON, the PDFs, a scripted model."""

from __future__ import annotations

import dataclasses
import json
import uuid
from decimal import Decimal

import pytest
from fixtures import fixture_path
from llm_fakes import FixtureAzureDi, load_azure_fixture

from basis.extraction.azure_di import AzureDiKeyValue, AzureDiResult
from basis.extraction.fallback import FallbackAnswer, FallbackValue
from basis.extraction.pdf_text import MAX_TEXT_PAGES, InvalidDocument
from basis.extraction.pipeline import ExtractionContext, extract_appraisal
from basis.llm import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse, ProviderUnavailable
from basis.llm.prompts import latest
from basis.llm.recorder import MemoryRecorder

from .golden import compare

SAMPLE = fixture_path("urar_sample.pdf").read_bytes()
SCAN = fixture_path("urar_scan_noisy.pdf").read_bytes()


class StaticAzure:
    def __init__(self, result: AzureDiResult) -> None:
        self.result = result
        self.calls = 0

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        self.calls += 1
        return self.result


def context(
    azure: object | None = None,
    provider: ScriptedProvider | None = None,
    previous: AzureDiResult | None = None,
) -> ExtractionContext:
    return ExtractionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="extract_fields",
            budget=Budget(remaining_usd=5.0),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        azure=azure,  # type: ignore[arg-type]
        prompt=latest("appraisal_field_fallback"),
        text_model="gpt-4o-mini",
        previous_azure=previous,
    )


def edit_pair(result: AzureDiResult, label: str, **changes: object) -> AzureDiResult:
    pairs: list[AzureDiKeyValue] = []
    for pair in result.key_value_pairs:
        if pair.key == label:
            if changes.get("drop"):
                continue
            pair = pair.model_copy(update=changes)
        pairs.append(pair)
    return result.model_copy(update={"key_value_pairs": tuple(pairs)})


async def test_azure_di_and_text_layer_agree_and_pass_the_gate() -> None:
    result = await extract_appraisal(SAMPLE, "SYN_URAR_0001", context(FixtureAzureDi()))
    hits, misses = compare(result.fields)
    assert misses == [] and len(hits) == 110
    assert result.method == "azure_di+regex"
    assert not result.needs_review and result.issues == []
    assert result.overall_confidence >= 0.93
    gla = result.fields["improvements.gla_sqft"]
    assert gla.source == "azure" and gla.corroborated_by == ("regex",)
    assert result.fallback["status"] == "not_needed"
    assert len(result.comparables) == 3
    json.dumps(result.raw)  # storable as JSONB
    assert result.raw["azure_di"]["model_id"] == "prebuilt-layout"


async def test_low_azure_confidence_on_a_critical_field_goes_to_review() -> None:
    ctx = context(FixtureAzureDi("urar_sample_low_confidence.json"))
    result = await extract_appraisal(SAMPLE, "d", ctx)
    assert result.needs_review
    assert result.review_reasons == ["low_confidence:improvements.gla_sqft=0.62<0.90"]
    assert result.fields["improvements.gla_sqft"].value_num == Decimal("1850")  # value kept


async def test_without_azure_the_text_layer_reads_every_field_but_is_reviewed() -> None:
    result = await extract_appraisal(SAMPLE, "d", context(None))
    hits, misses = compare(result.fields)
    assert misses == [] and len(hits) == 110
    assert result.method == "regex"
    assert result.needs_review
    assert "Azure DI not configured: text layer and tables only" in result.notes
    assert result.overall_confidence == pytest.approx(0.80)


async def test_a_scan_is_read_by_azure_di_alone() -> None:
    result = await extract_appraisal(SCAN, "d", context(FixtureAzureDi()))
    hits, misses = compare(result.fields)
    assert misses == []
    assert not result.needs_review
    assert "the PDF has no text layer (scanned)" in result.notes


async def test_a_scan_without_azure_has_nothing_and_does_not_call_the_model() -> None:
    provider = ScriptedProvider([])
    result = await extract_appraisal(SCAN, "d", context(None, provider))
    assert result.fields == {} and result.needs_review
    assert result.fallback["status"] == "no_page_text"
    assert provider.requests == []
    assert {r.split(":")[0] for r in result.review_reasons} == {"missing"}


async def test_fallback_fills_only_a_critical_field_no_reader_found() -> None:
    azure = StaticAzure(edit_pair(load_azure_fixture(), "Gross Living Area", drop=True))
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                parsed=FallbackAnswer(
                    fields=[
                        FallbackValue(
                            key="improvements.gla_sqft",
                            value="1,850 sq ft",
                            page=3,
                            confidence=0.92,
                        )
                    ]
                ),
                usage=Usage(input_tokens=2000, output_tokens=60),
            )
        ]
    )
    ctx = context(azure, provider)
    result = await extract_appraisal(SCAN, "d", ctx)
    [request] = provider.requests
    asked = request.messages[0].content.split("Page text")[0]
    assert asked.count("- ") == 1 and "improvements.gla_sqft" in asked
    gla = result.fields["improvements.gla_sqft"]
    assert (gla.source, gla.value_num, gla.confidence) == ("llm", Decimal("1850"), 0.6)
    assert result.needs_review  # 0.6 < 0.90: a suggestion, never an accepted value
    assert result.review_reasons == ["low_confidence:improvements.gla_sqft=0.60<0.90"]
    assert ctx.call.budget.calls == 1


async def test_the_text_layer_fills_an_azure_gap_before_any_model_call() -> None:
    azure = StaticAzure(edit_pair(load_azure_fixture(), "Gross Living Area", drop=True))
    provider = ScriptedProvider([])
    result = await extract_appraisal(SAMPLE, "d", context(azure, provider))
    assert provider.requests == []
    assert result.fields["improvements.gla_sqft"].source == "regex"
    assert result.review_reasons == ["low_confidence:improvements.gla_sqft=0.80<0.90"]


async def test_azure_and_text_layer_disagreeing_on_a_critical_field_is_reviewed() -> None:
    azure = StaticAzure(edit_pair(load_azure_fixture(), "Gross Living Area", value="1,580 sq ft"))
    result = await extract_appraisal(SAMPLE, "d", context(azure))
    assert result.needs_review
    assert "sources_disagree:improvements.gla_sqft" in result.review_reasons
    assert result.fields["improvements.gla_sqft"].value_num == Decimal("1580")


async def test_an_unparseable_azure_value_is_replaced_by_the_text_layer() -> None:
    azure = StaticAzure(edit_pair(load_azure_fixture(), "Year Built", value="see addendum"))
    provider = ScriptedProvider([])
    result = await extract_appraisal(SAMPLE, "d", context(azure, provider))
    assert provider.requests == []
    assert result.fields["improvements.year_built"].value_num == Decimal("1987")


async def test_a_critical_field_failing_a_range_rule_is_asked_and_revalidated() -> None:
    # Azure misread the year (1087); on a scan there is no text layer to correct it.
    azure = StaticAzure(edit_pair(load_azure_fixture(), "Year Built", value="1087"))
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                parsed=FallbackAnswer(
                    fields=[
                        FallbackValue(
                            key="improvements.year_built", value="1987", page=3, confidence=0.9
                        )
                    ]
                ),
            )
        ]
    )
    result = await extract_appraisal(SCAN, "d", context(azure, provider))
    [request] = provider.requests
    assert "improvements.year_built" in request.messages[0].content.split("Page text")[0]
    year = result.fields["improvements.year_built"]
    assert (year.source, year.value_num) == ("llm", Decimal("1987"))
    assert not [i for i in result.issues if i.rule == "range"]  # revalidated
    assert result.needs_review  # still: an LLM value is capped below the gate


class RefusingAzure:
    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        raise ProviderBadResponse("401 unauthorized", provider="azure_di", status=401)


async def test_azure_refusing_the_request_falls_back_to_the_text_layer_and_review() -> None:
    result = await extract_appraisal(SAMPLE, "d", context(RefusingAzure()))
    hits, _ = compare(result.fields)
    assert len(hits) == 110 and result.method == "regex"
    assert result.needs_review and "azure_di_rejected" in result.review_reasons
    assert "Azure DI rejected the request (401): text layer only" in result.notes


async def test_azure_unavailable_propagates_for_a_retry() -> None:
    class Down:
        async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
            raise ProviderUnavailable("503", provider="azure_di", status=503)

    with pytest.raises(ProviderUnavailable):
        await extract_appraisal(SAMPLE, "d", context(Down()))


async def test_a_stored_analysis_is_reused() -> None:
    azure = StaticAzure(load_azure_fixture())
    result = await extract_appraisal(SAMPLE, "d", context(azure, previous=load_azure_fixture()))
    assert azure.calls == 0
    assert "reused the Azure DI analysis of an earlier revision" in result.notes


async def test_a_very_long_pdf_is_read_up_to_the_limit() -> None:
    data = fixture_path("malformed", "huge_page_count.pdf").read_bytes()
    result = await extract_appraisal(data, "d", context(None))
    assert result.page_count == 200 and result.raw["pages_read"] == MAX_TEXT_PAGES
    assert f"read the first {MAX_TEXT_PAGES} of 200 pages" in result.notes
    assert result.needs_review


@pytest.mark.parametrize(
    ("name", "message"),
    [("not_a_pdf.pdf", "not a PDF"), ("empty.pdf", "no pages")],
)
async def test_malformed_pdfs_are_rejected(name: str, message: str) -> None:
    data = fixture_path("malformed", name).read_bytes()
    azure = StaticAzure(load_azure_fixture())
    with pytest.raises(InvalidDocument, match=message):
        await extract_appraisal(data, "d", context(azure))
    assert azure.calls == 0  # rejected before paying for an analysis


async def test_heartbeats_are_sent_at_each_stage() -> None:
    beats: list[str] = []
    ctx = context(FixtureAzureDi())
    ctx.heartbeat = beats.append
    await extract_appraisal(SAMPLE, "d", ctx)
    assert beats == ["read_pdf", "azure_di", "map_fields", "fallback"]


# ------------------------------------------------------- scope "missing" (EVAL_REPORT 26)


async def test_the_missing_scope_asks_once_for_every_empty_field_and_fills_only_those() -> None:
    azure = StaticAzure(edit_pair(load_azure_fixture(), "Gross Living Area", drop=True))
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                parsed=FallbackAnswer(
                    fields=[
                        # In the text: kept. A field Azure DI read: not asked, so dropped.
                        FallbackValue(
                            key="improvements.gla_sqft", value="1,850 sq ft", page=3, confidence=0.9
                        ),
                        FallbackValue(key="subject.city", value="Nowhere", page=1, confidence=0.9),
                    ]
                ),
                usage=Usage(input_tokens=9000, output_tokens=900),
            )
        ]
    )
    ctx = dataclasses.replace(context(azure, provider), fallback_scope="missing")
    result = await extract_appraisal(SCAN, "d", ctx)
    [request] = provider.requests
    asked = request.messages[0].content.split("Page text")[0]
    assert "improvements.gla_sqft" in asked and "neighborhood.price_low_thousands" in asked
    assert "- subject.city:" not in asked  # Azure DI read it
    assert "site.area_acres" not in asked  # derived, never asked
    content = request.messages[0].content
    assert content.count("<page number=") == result.page_count  # the whole document
    assert result.fields["improvements.gla_sqft"].source == "llm"
    assert result.fields["subject.city"].source == "azure"
    assert result.fallback["dropped"] == {"subject.city": "not_asked"}
    assert result.raw["fallback_scope"] == "missing"
    assert ctx.call.budget.calls == 1


async def test_the_site_area_is_kept_as_printed_alongside_square_feet() -> None:
    result = await extract_appraisal(SAMPLE, "d", context(FixtureAzureDi()))
    area, text = result.fields["site.area_sqft"], result.fields["site.area"]
    assert (area.value_num, text.value_text, text.source) == (
        Decimal("10000"),
        "10,000 sf",
        "azure",
    )
    assert "site.area_acres" not in result.fields  # printed in square feet
    acres = StaticAzure(edit_pair(load_azure_fixture(), "Site Area", value="1.39 ac"))
    result = await extract_appraisal(SAMPLE, "d", context(acres))
    assert result.fields["site.area_sqft"].value_num == Decimal("60548")
    assert result.fields["site.area"].value_text == "1.39 ac"
    assert result.fields["site.area_acres"].value_num == Decimal("1.39")
