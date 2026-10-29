"""``extract_appraisal`` with ``method="grounded"`` (EXTRACTION_METHOD, the production default).

Azure DI is a fabricated analysis with word and mark geometry (the sweep tests' layout), the
PDF is the synthetic sample (its text layer is not what the grounded reading reads), and the
model is a ``ScriptedProvider``:

* the reading: one request on the configured model, effort and budget with the grounded
  prompt and schema; values as ``llm`` fields at the 0.6 cap with the quote's box; the
  derived site-area fields with their source's box; the checks' drops and the quotes in
  ``raw``; the review gate; a UAD 3.6 report asked only its keys;
* the error contract (module doc of :mod:`basis.extraction.pipeline`): a provider error, an
  answer with no structured content, an exhausted budget, no geometry, no provider and no
  Azure DI each read the document by the pipeline on the same analysis (Azure DI paid once),
  with the attempt in ``raw["grounded"]``; an account error propagates;
* a UAD 3.6 XML upload is still read by the XML reader (no model call).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import FixtureAzureDi

from basis.extraction.azure_di import AzureDiResult, from_sdk
from basis.extraction.fallback import FallbackAnswer
from basis.extraction.field_mappings import LLM_CONFIDENCE_CAP
from basis.extraction.grounded import GroundedAnswer, GroundedModel, GroundedValue
from basis.extraction.pipeline import (
    METHOD_GROUNDED,
    ExtractionContext,
    extract_appraisal,
    extract_appraisal_upload,
)
from basis.extraction.uad36_pdf import NOT_CARRIED
from basis.llm import Budget, ScriptedProvider, ScriptedTurn
from basis.llm.call import CallContext
from basis.llm.errors import ProviderAccountError, ProviderUnavailable
from basis.llm.prompts import latest
from basis.llm.recorder import MemoryRecorder
from evals.appraisal.recordings import rebuild_sdk_result

from ..evals.appraisal_systems_support import raw_layout

SAMPLE = fixture_path("urar_sample.pdf").read_bytes()
SOL = GroundedModel(model="gpt-6.1-sol", reasoning_effort="low", max_tokens=32000)

PAGE_1 = [
    "Subject",
    "Property Address 1290 Example Way",
    "Occupant :selected: Owner :unselected: Tenant :unselected: Vacant",
    "Contract Price $ 412,000 Date of Contract 03/01/2026",
]
PAGE_2 = [
    "Site",
    "Area 7,405 sf Shape Rectangular",
    "Improvements",
    "Year Built 2004 Gross Living Area 1,850",
]


class StaticAzure:
    def __init__(self, result: AzureDiResult) -> None:
        self.result = result
        self.calls = 0

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        self.calls += 1
        return self.result


def analysis(*pages: list[str]) -> AzureDiResult:
    return from_sdk(rebuild_sdk_result(raw_layout(pages or (PAGE_1, PAGE_2))))


def value(key: str, text: str, page: int, quote: str) -> GroundedValue:
    return GroundedValue(key=key, status="present", value=text, page=page, quote=quote)


def answer(*values: GroundedValue) -> ScriptedTurn:
    return ScriptedTurn(parsed=GroundedAnswer(fields=list(values)))


GOOD = answer(
    value("subject.property_address", "1290 Example Way", 1, "Property Address 1290 Example Way"),
    value("subject.occupant", "Owner", 1, "Occupant :selected: Owner"),
    value("listing_and_contract.contract_price", "$ 412,000", 1, "Contract Price $ 412,000"),
    value("site.area_sqft", "7,405 sf", 2, "Area 7,405 sf"),
    value("improvements.year_built", "2004", 2, "Year Built 2004"),
    # Not printed where the model says: dropped by the checks.
    value("improvements.gla_sqft", "1,900", 2, "Gross Living Area 1,900"),
    GroundedValue(
        key="reconciliation.indicated_value_income_approach",
        status="not_developed",
        value=None,
        page=None,
        quote=None,
    ),
)


def context(
    azure: object | None,
    provider: ScriptedProvider | None,
    *,
    budget: float = 5.0,
    method: str = "grounded",
) -> ExtractionContext:
    return ExtractionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="extract_fields",
            budget=Budget(remaining_usd=budget),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        azure=azure,  # type: ignore[arg-type]
        prompt=latest("appraisal_field_fallback"),
        text_model="gpt-4o-mini",
        # The production scope: the pipeline's fallback asks every field left empty.
        fallback_scope="missing",
        method=method,  # type: ignore[arg-type]
        grounded=SOL,
    )


def fallback_turn() -> ScriptedTurn:
    return ScriptedTurn(parsed=FallbackAnswer(fields=[]))


# --------------------------------------------------------------------------- the reading


async def test_the_grounded_reading_is_one_checked_call_on_the_configured_model() -> None:
    azure = StaticAzure(analysis())
    provider = ScriptedProvider([GOOD], model="gpt-6.1-sol")
    ctx = context(azure, provider)
    result = await extract_appraisal(SAMPLE, "doc", ctx)

    [request] = provider.requests
    assert (request.model, request.reasoning_effort, request.max_tokens) == (
        "gpt-6.1-sol",
        "low",
        32000,
    )
    assert request.response_model is GroundedAnswer and request.temperature == 0.0
    assert request.prompt_version == latest("appraisal_fields_grounded").version_id
    content = request.messages[0].content
    assert "Occupant :selected:[Owner]" in content  # the marks labelled from the geometry
    assert '- subject.occupant: printed as "Occupant" (checkbox)' in content
    assert azure.calls == 1

    assert result.method == METHOD_GROUNDED == "azure_di+grounded"
    address = result.fields["subject.property_address"]
    assert (address.value_text, address.source, address.page) == ("1290 Example Way", "llm", 1)
    assert address.confidence == LLM_CONFIDENCE_CAP
    assert address.regions and address.regions[0].page == 1
    assert result.fields["subject.occupant"].value_text == "Owner"
    assert result.fields["listing_and_contract.contract_price"].value_num == 412000
    # The site area's other views follow from the reading, with its box.
    sqft = result.fields["site.area_sqft"]
    area = result.fields["site.area"]
    assert sqft.value_num == 7405 and area.value_text == "7,405 sf"
    assert area.regions == sqft.regions and sqft.regions
    assert "site.area_acres" not in result.fields  # not printed in acres
    assert "improvements.gla_sqft" not in result.fields
    assert result.fallback["dropped"]["improvements.gla_sqft"] == "quote_not_on_page"
    assert result.fallback["statuses"] == {
        "reconciliation.indicated_value_income_approach": "not_developed"
    }
    assert result.fallback["status"] == "called"
    grounded = result.raw["grounded"]
    assert grounded["model"] == "gpt-6.1-sol" and grounded["status"] == "called"
    assert grounded["quotes"]["subject.occupant"] == "Occupant :selected: Owner"
    assert result.raw["azure_di"]["pages"] and result.raw["method"] == result.method
    # A model value is a suggestion: critical fields at 0.6 send the extraction to review.
    assert result.needs_review
    assert [r.activity for r in ctx.call.recorder.records] == ["extract_fields"]  # type: ignore[attr-defined]


async def test_a_uad36_report_is_asked_only_its_keys_and_not_required_the_others() -> None:
    footer = "Fannie Mae | Freddie Mac Appraiser Reference ID A1"
    azure = StaticAzure(
        analysis(["Dwelling Exterior", "Year Built 2004", footer], ["Unit Interior", footer])
    )
    provider = ScriptedProvider(
        [answer(value("improvements.year_built", "2004", 1, "Year Built 2004"))]
    )
    result = await extract_appraisal(SAMPLE, "doc", context(azure, provider))
    [request] = provider.requests
    assert request.messages[0].content.startswith("This report is printed on the redesigned URAR")
    assert result.method == "azure_di+grounded+uad36"
    assert result.raw["form"]["form"] == "uad36_urar"
    assert result.fields["improvements.year_built"].value_num == 2004
    assert not any(set(issue.keys) & NOT_CARRIED for issue in result.issues)
    assert not set(result.fallback["asked"]) & NOT_CARRIED


# --------------------------------------------------------------------------- the error contract


def pipeline_attempt(result: Any) -> dict[str, Any]:
    assert result.method.startswith(("azure_di+regex", "regex")), result.method
    assert any("grounded reading unavailable" in note for note in result.notes)
    attempt: dict[str, Any] = result.raw["grounded"]
    assert attempt["model"] == "gpt-6.1-sol"
    return attempt


async def test_a_provider_error_reads_the_document_by_the_pipeline_on_the_same_analysis() -> None:
    azure = StaticAzure(analysis())
    failing = ScriptedTurn(raise_error=ProviderUnavailable("down", provider="scripted"))
    provider = ScriptedProvider([failing, fallback_turn()])
    result = await extract_appraisal(SAMPLE, "doc", context(azure, provider))
    attempt = pipeline_attempt(result)
    assert attempt["status"] == "provider_error" and "ProviderUnavailable" in attempt["error"]
    assert azure.calls == 1  # not analyzed again
    grounded_request, fallback_request = provider.requests
    assert grounded_request.response_model is GroundedAnswer
    # The pipeline's own fallback call, on TEXT_MODEL, best effort as before.
    assert fallback_request.response_model is FallbackAnswer
    assert fallback_request.model == "gpt-4o-mini"


async def test_an_answer_without_structure_reads_the_document_by_the_pipeline() -> None:
    provider = ScriptedProvider([ScriptedTurn(text="sorry"), fallback_turn()])
    result = await extract_appraisal(SAMPLE, "doc", context(StaticAzure(analysis()), provider))
    attempt = pipeline_attempt(result)
    assert (attempt["status"], attempt["error"]) == ("provider_error", "all: no structured answer")


async def test_an_exhausted_budget_reads_the_document_by_the_pipeline() -> None:
    provider = ScriptedProvider([])  # nothing is sent: the reservation is refused
    result = await extract_appraisal(
        SAMPLE, "doc", context(StaticAzure(analysis()), provider, budget=0.0001)
    )
    assert pipeline_attempt(result)["status"] == "budget_exceeded"
    assert provider.requests == []
    assert result.fallback["status"] == "budget_exceeded"  # the pipeline's call too


async def test_an_account_error_stops_the_extraction() -> None:
    error = ProviderAccountError("no credit", provider="scripted", status=402)
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)])
    with pytest.raises(ProviderAccountError):
        await extract_appraisal(SAMPLE, "doc", context(StaticAzure(analysis()), provider))


async def test_an_analysis_without_geometry_is_read_by_the_pipeline_without_a_grounded_call() -> (
    None
):
    provider = ScriptedProvider([fallback_turn(), fallback_turn()])
    pinned = await extract_appraisal(
        SAMPLE, "doc", context(FixtureAzureDi(), provider, method="pipeline")
    )
    result = await extract_appraisal(SAMPLE, "doc", context(FixtureAzureDi(), provider))
    assert pipeline_attempt(result)["status"] == "no_geometry"
    first, second = provider.requests  # the pipeline's own fallback, once per extraction
    assert first.response_model is second.response_model is FallbackAnswer
    assert first.messages == second.messages
    # The same result the pipeline gives (bar the note and the attempt).
    assert result.fields == pinned.fields and result.issues == pinned.issues
    assert "grounded" not in pinned.raw and pinned.method == result.method == "azure_di+regex"


async def test_no_provider_or_no_azure_di_reads_the_document_by_the_pipeline() -> None:
    result = await extract_appraisal(SAMPLE, "doc", context(StaticAzure(analysis()), None))
    assert pipeline_attempt(result)["status"] == "no_provider"
    provider = ScriptedProvider([fallback_turn()])
    result = await extract_appraisal(SAMPLE, "doc", context(None, provider))
    assert pipeline_attempt(result)["status"] == "no_azure_di"
    assert result.method == "regex"
    assert [r.response_model for r in provider.requests] == [FallbackAnswer]


async def test_a_blank_analysis_has_no_page_text_to_send() -> None:
    # Words with boxes but no page text (no content and no lines: an OCR that read nothing
    # but stray marks would look like this).
    blank = analysis()
    blank = blank.model_copy(
        update={
            "content": "",
            "pages": tuple(p.model_copy(update={"lines": ()}) for p in blank.pages),
        }
    )
    provider = ScriptedProvider([fallback_turn()])
    result = await extract_appraisal(SAMPLE, "doc", context(StaticAzure(blank), provider))
    assert pipeline_attempt(result)["status"] == "no_page_text"
    assert all(r.response_model is not GroundedAnswer for r in provider.requests)


def test_a_caller_that_sets_nothing_gets_the_pipeline() -> None:
    ctx = ExtractionContext(
        call=context(None, None).call,
        provider=None,
        azure=None,
        prompt=latest("appraisal_field_fallback"),
        text_model="gpt-4o-mini",
    )
    assert (ctx.method, ctx.grounded) == ("pipeline", None)


async def test_grounded_without_a_model_is_a_programming_error() -> None:
    ctx = context(StaticAzure(analysis()), ScriptedProvider([]))
    ctx.grounded = None
    with pytest.raises(ValueError, match="needs ExtractionContext.grounded"):
        await extract_appraisal(SAMPLE, "doc", ctx)


# --------------------------------------------------------------------------- UAD 3.6 XML


async def test_a_uad36_xml_upload_is_still_read_by_the_xml_reader() -> None:
    from uad36_fakes import azure_printing, fabricated_xml, fabricated_zip

    from basis.extraction import pipeline

    pdf = type("Pdf", (), {"page_count": 1})()
    original = pipeline.read_pdf
    pipeline.read_pdf = lambda data: pdf  # type: ignore[assignment,return-value]
    azure = StaticAzure(azure_printing([["Opinion", "of", "Market", "Value", "$300,000"]]))
    provider = ScriptedProvider([])
    try:
        data = fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": SAMPLE})
        result = await extract_appraisal_upload(data, "doc", context(azure, provider))
    finally:
        pipeline.read_pdf = original
    assert result.fields["reconciliation.final_market_value"].source == "mismo_xml"
    assert result.raw["upload_kind"] == "zip" and "grounded" not in result.raw
    assert provider.requests == [] and azure.calls == 1
