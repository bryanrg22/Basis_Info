"""The single-shot LLM fallback (scripted provider): only failed criticals are asked."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from basis.extraction.derived import DERIVED_KEYS
from basis.extraction.fallback import (
    MAX_CONTEXT_CHARS,
    FallbackAnswer,
    FallbackValue,
    appears_in,
    build_context,
    context_pages,
    fallback_fields,
    fallback_keys,
)
from basis.extraction.field_mappings import CRITICAL_FIELDS, FIELDS
from basis.extraction.results import Candidate, ExtractedField, ValidationIssue
from basis.llm import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderAccountError, ProviderBadResponse, ProviderUnavailable
from basis.llm.prompts import latest
from basis.llm.recorder import MemoryRecorder

PAGES = [
    "Subject\nProperty Address: 1290 Example Way City: Springfield",
    "Improvements\nYear Built: 1987\nGross Living Area: 1,850 sq ft",
    "Reconciliation\nFinal Opinion of Market Value: $410,000\nEffective Date: 10/01/2026",
]


def ctx(budget: float = 1.0) -> CallContext:
    return CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="extract_fields",
        budget=Budget(remaining_usd=budget),
        recorder=MemoryRecorder(),
    )


def answer(*values: FallbackValue) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=FallbackAnswer(fields=list(values)), usage=Usage(input_tokens=500, output_tokens=50)
    )


async def run(keys: list[str], provider: ScriptedProvider | None, budget: float = 1.0):  # type: ignore[no-untyped-def]
    call = ctx(budget)
    outcome = await fallback_fields(
        keys,
        PAGES,
        provider=provider,
        ctx=call,
        prompt=latest("appraisal_field_fallback"),
        model="gpt-4o-mini",
        max_tokens=800,
    )
    return outcome, call


async def test_only_the_failed_critical_fields_are_asked() -> None:
    provider = ScriptedProvider(
        [
            answer(
                FallbackValue(
                    key="improvements.gla_sqft", value="1,850 sq ft", page=2, confidence=0.9
                )
            )
        ]
    )
    outcome, call = await run(["improvements.gla_sqft"], provider)
    [request] = provider.requests
    asked = request.messages[0].content.split("Page text")[0]
    assert "improvements.gla_sqft" in asked
    assert "reconciliation.final_market_value" not in asked
    assert request.response_model is FallbackAnswer
    assert request.prompt_version == "appraisal_field_fallback/v1"
    assert request.temperature == 0.0
    gla = outcome.fields["improvements.gla_sqft"]
    assert (gla.source, gla.value_num, gla.confidence, gla.page) == ("llm", Decimal("1850"), 0.6, 2)
    assert outcome.status == "called"
    [record] = call.recorder.records  # type: ignore[attr-defined]
    assert record.prompt_version == "appraisal_field_fallback/v1"
    assert call.budget.calls == 1


async def test_only_the_relevant_pages_are_sent() -> None:
    provider = ScriptedProvider([answer()])
    await run(["improvements.gla_sqft"], provider)
    content = provider.requests[0].messages[0].content
    assert "<page number=2>" in content
    assert "<page number=1>" not in content and "<page number=3>" not in content


async def test_nothing_failed_means_no_call() -> None:
    provider = ScriptedProvider([])
    outcome, _ = await run([], provider)
    assert outcome.status == "not_needed" and provider.requests == []


async def test_confidence_is_capped_at_0_6() -> None:
    provider = ScriptedProvider(
        [answer(FallbackValue(key="improvements.year_built", value="1987", page=2, confidence=1.0))]
    )
    outcome, _ = await run(["improvements.year_built"], provider)
    assert outcome.fields["improvements.year_built"].confidence == 0.6


async def test_low_self_reported_confidence_is_kept() -> None:
    provider = ScriptedProvider(
        [answer(FallbackValue(key="improvements.year_built", value="1987", page=2, confidence=0.3))]
    )
    outcome, _ = await run(["improvements.year_built"], provider)
    assert outcome.fields["improvements.year_built"].confidence == 0.3


async def test_values_not_in_the_page_text_are_dropped() -> None:
    provider = ScriptedProvider(
        [
            answer(
                FallbackValue(
                    key="reconciliation.final_market_value",
                    value="$9,999,999",
                    page=3,
                    confidence=0.99,
                )
            )
        ]
    )
    outcome, _ = await run(["reconciliation.final_market_value"], provider)
    assert outcome.fields == {}
    assert outcome.dropped == {"reconciliation.final_market_value": "not_in_text"}


async def test_unasked_duplicate_missing_and_unparseable_values_are_dropped() -> None:
    provider = ScriptedProvider(
        [
            answer(
                FallbackValue(key="subject.city", value="Springfield", page=1, confidence=0.9),
                FallbackValue(key="improvements.year_built", value="1987", page=2, confidence=0.9),
                FallbackValue(key="improvements.year_built", value="1987", page=2, confidence=0.9),
                FallbackValue(key="improvements.gla_sqft", value=None, page=None, confidence=0.0),
                FallbackValue(
                    key="reconciliation.effective_date_of_appraisal",
                    value="Effective Date",
                    page=3,
                    confidence=0.9,
                ),
            )
        ]
    )
    outcome, _ = await run(
        [
            "improvements.year_built",
            "improvements.gla_sqft",
            "reconciliation.effective_date_of_appraisal",
        ],
        provider,
    )
    assert set(outcome.fields) == {"improvements.year_built"}
    assert outcome.dropped == {
        "subject.city": "not_asked",
        "improvements.year_built": "duplicate",
        "improvements.gla_sqft": "not_found",
        "reconciliation.effective_date_of_appraisal": "unparseable:not a date",
    }


async def test_a_page_number_not_sent_is_not_kept() -> None:
    provider = ScriptedProvider(
        [answer(FallbackValue(key="improvements.year_built", value="1987", page=9, confidence=0.9))]
    )
    outcome, _ = await run(["improvements.year_built"], provider)
    assert outcome.fields["improvements.year_built"].page is None


async def test_no_provider_or_no_text_skips_the_call() -> None:
    outcome, _ = await run(["improvements.year_built"], None)
    assert outcome.status == "no_provider"
    provider = ScriptedProvider([])
    blank = await fallback_fields(
        ["improvements.year_built"],
        ["", "  "],
        provider=provider,
        ctx=ctx(),
        prompt=latest("appraisal_field_fallback"),
        model="gpt-4o-mini",
        max_tokens=800,
    )
    assert blank.status == "no_page_text" and provider.requests == []


async def test_an_exhausted_budget_skips_the_call() -> None:
    provider = ScriptedProvider([])
    outcome, call = await run(["improvements.year_built"], provider, budget=0.0)
    assert outcome.status == "budget_exceeded" and provider.requests == []
    assert call.recorder.records == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("down", provider="azure", status=503),
        ProviderBadResponse(
            "bad json",
            provider="azure",
            usage=Usage(input_tokens=10, output_tokens=5),
            model="gpt-4o-mini",
        ),
    ],
)
async def test_provider_errors_do_not_fail_the_extraction(error: Exception) -> None:
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)])
    outcome, call = await run(["improvements.year_built"], provider)
    assert outcome.status == "provider_error" and outcome.fields == {}
    # A bad response reached the model: its tokens are recorded and charged.
    expected = 1 if isinstance(error, ProviderBadResponse) else 0
    assert len(call.recorder.records) == expected  # type: ignore[attr-defined]


async def test_an_account_error_stops_the_extraction() -> None:
    # Not a failed fallback: the activity must stop with the account error.
    refused = ProviderAccountError("no credit", provider="anthropic", status=400)
    provider = ScriptedProvider([ScriptedTurn(raise_error=refused)])
    with pytest.raises(ProviderAccountError):
        await run(["improvements.year_built"], provider)


def test_context_is_bounded() -> None:
    pages = ["Improvements\n" + "Year Built: 1987 " * 2000] * 5
    context, used = build_context(["improvements.year_built"], pages)
    # A page larger than the budget is cut, not dropped; nothing follows it.
    assert len(context) <= MAX_CONTEXT_CHARS and used == [1]
    assert context.startswith("<page number=1>\nImprovements") and context.endswith("</page>")
    small = ["Improvements\nYear Built: 1987"] * 3
    assert build_context(["improvements.year_built"], small)[1] == [1, 2, 3]


def test_context_pages_follow_sections() -> None:
    assert context_pages(["reconciliation.final_market_value"], PAGES) == [3]
    assert context_pages(["subject.property_address", "improvements.gla_sqft"], PAGES) == [1, 2]


# --------------------------------------------------------------------------- scope (EVAL_REPORT 27)


def field(key: str, raw: str) -> ExtractedField:
    return ExtractedField.from_candidate(
        Candidate(key=key, raw_text=raw, page=1, confidence=0.95, source="azure")
    )


def test_the_critical_scope_asks_only_failed_criticals() -> None:
    fields = {key: field(key, "1") for key in CRITICAL_FIELDS if key != "improvements.bedrooms"}
    assert fallback_keys(fields, [], "critical") == ["improvements.bedrooms"]


def test_the_missing_scope_asks_every_empty_catalogue_field_in_order() -> None:
    present = {"subject.city": field("subject.city", "Springfield")}
    asked = fallback_keys(present, [], "missing")
    catalogue = [spec.key for spec in FIELDS]
    assert asked == [k for k in catalogue if k not in present and k not in DERIVED_KEYS]
    assert "subject.city" not in asked
    assert {"site.area", "site.area_acres"}.isdisjoint(asked)  # derived, never asked
    assert set(CRITICAL_FIELDS) <= set(asked)


def test_the_missing_scope_asks_a_field_that_failed_its_rule_again() -> None:
    fields = {spec.key: field(spec.key, "x") for spec in FIELDS}
    issue = ValidationIssue(rule="type", message="not a number", keys=("subject.tax_year",))
    cross = ValidationIssue(rule="cross_field", message="disagree", keys=("subject.city",))
    assert fallback_keys(fields, [issue, cross], "missing") == ["subject.tax_year"]
    assert fallback_keys(fields, [issue], "critical") == []  # tax year is not critical


async def test_the_whole_document_is_sent_up_to_the_limit() -> None:
    provider = ScriptedProvider([answer(), answer()])
    call = ctx()
    common = {
        "provider": provider,
        "ctx": call,
        "prompt": latest("appraisal_field_fallback"),
        "model": "gpt-4o-mini",
        "max_tokens": 800,
    }
    # One subject key: by sections only page 1 would be sent; the whole document sends all.
    outcome = await fallback_fields(["subject.city"], PAGES, whole_document=True, **common)  # type: ignore[arg-type]
    content = provider.requests[0].messages[0].content
    assert all(f"<page number={n}>" in content for n in (1, 2, 3))
    assert outcome.status == "called"
    await fallback_fields(
        ["subject.city"],
        PAGES,
        whole_document=True,
        max_context_chars=120,
        **common,  # type: ignore[arg-type]
    )
    short = provider.requests[1].messages[0].content
    assert "<page number=1>" in short and "<page number=3>" not in short


@pytest.mark.parametrize(
    ("value", "context", "kept"),
    [
        ("$460,900", "DWELLING 3,200 Sq.Ft. @ $\n125.25\n=$\n460,900\n", True),
        ("$ 682,000", "Indicated Value $682,000", True),
        ("460,900", "=$\n460,900", True),
        ("$460,901", "=$\n460,900", False),  # a different figure is still dropped
        ("$460,900", "460 900", False),
    ],
)
def test_the_guard_joins_a_currency_sign_to_its_amount(
    value: str, context: str, kept: bool
) -> None:
    """EVAL_REPORT 27: Azure DI prints "=$" and the figure on separate lines."""
    assert appears_in(value, context) is kept
