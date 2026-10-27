"""The page geometry and the grounded model systems (``llm_all``, ``llm_pages``,
``llm_sections``) on fabricated Azure DI analyses and a scripted model (no network).

* geometry: page text with selection marks from the content spans, word and mark boxes as
  page fractions, figure coverage, locating a text (several words, nearest a caption, within
  a box, absent);
* the mechanical checks: each drop reason (quote on the page or not, value in the quote,
  bad page, checkbox marks before/after/unselected, several checked options, an Other
  write-in, absent/not_developed statuses, keys not asked or answered twice, unparseable),
  the 0.6 cap and the region from the quote's word polygons;
* the systems: the request (every key, page text with marks, the prompt), the result; page
  images only of form pages (photo pages skipped by word count and by figures); one call per
  section group with its own pages, in parallel; provider error and budget statuses.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from basis.extraction.field_mappings import LLM_CONFIDENCE_CAP
from basis.llm import ScriptedProvider, ScriptedTurn
from basis.llm.errors import ProviderUnavailable
from evals.appraisal.systems.base import ASKED_KEYS, ModelChoice
from evals.appraisal.systems.grounded import (
    SECTION_GROUPS,
    GroundedAnswer,
    GroundedValue,
    check_answer,
    form_pages,
    is_form_page,
    llm_all,
    llm_pages,
    llm_sections,
)
from evals.appraisal.systems.layout import fold_chars, from_raw, locate
from evals.appraisal.systems.marks import labelled_texts

from .appraisal_systems_support import (
    RawClient,
    make_env,
    pdf_bytes,
    raw_layout,
    token_box,
)

PAGE_1 = [
    "Subject",
    "Property Address 1290 Example Way",
    "Occupant :selected: Owner :unselected: Tenant :unselected: Vacant",
    "Contract",
    "Contract Price $ 412,000 Date of Contract 03/01/2026",
]
PAGE_2 = [
    "Site",
    "Water :unselected: Public :selected: Other Municipal well",
    "Electricity :selected: Public :unselected: Other",
    "Improvements",
    "Foundation :selected: Concrete Slab :selected: Crawl Space :unselected: Full Basement",
    "Gross Living Area 1,740",
]
PAGE_3 = ["Subject Front"]  # a photo page
PAGES = [PAGE_1, PAGE_2, PAGE_3]
PHOTO = {"boundingRegions": [{"pageNumber": 3, "polygon": [0, 0, 8.5, 0, 8.5, 11, 0, 11]}]}
MODEL = ModelChoice("gpt-6-luna", "low", 8000)


def layout(**kwargs: Any) -> dict[str, Any]:
    return raw_layout(PAGES, figures=[PHOTO], **kwargs)


def value(
    key: str, val: str | None, page: int | None, quote: str | None, status: str = "present"
) -> GroundedValue:
    return GroundedValue(key=key, status=status, value=val, page=page, quote=quote)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- geometry


def test_page_text_keeps_marks_and_boxes_are_fractions() -> None:
    geometry = from_raw(layout())
    page = geometry.page(1)
    assert page is not None
    assert ":selected: Owner :unselected: Tenant" in page.text
    assert page.lines[2] == "Occupant Owner Tenant Vacant"  # lines carry no marks
    assert [m.selected for m in page.marks] == [True, False, False]
    owner = next(w for w in page.words if w.text == "Owner")
    assert owner.box == pytest.approx(token_box(2, PAGE_1[2].split(" "), 2))
    assert all(0 <= v <= 1 for w in page.words for v in w.box)
    assert geometry.texts()[1].startswith("Site\n")
    assert geometry.page(3) is not None and geometry.page(3).figure_fraction == pytest.approx(1.0)  # type: ignore[union-attr]
    assert geometry.page(1).figure_fraction == 0.0  # type: ignore[union-attr]


def test_locate_words_nearest_and_within() -> None:
    page = from_raw(layout()).page(1)
    tokens = PAGE_1[4].split(" ")
    box = locate(page, "Contract Price $ 412,000")
    assert box == pytest.approx(
        (
            token_box(4, tokens, 0)[0],
            token_box(4, tokens, 0)[1],
            token_box(4, tokens, 3)[2],
            token_box(4, tokens, 3)[3],
        )
    )
    assert locate(page, "$412,000") == pytest.approx(
        (
            token_box(4, tokens, 2)[0],
            token_box(4, tokens, 2)[1],
            token_box(4, tokens, 3)[2],
            token_box(4, tokens, 3)[3],
        )
    )
    # "Contract" twice: nearest the given point, else the first in reading order.
    first = locate(page, "Contract")
    near = locate(page, "Contract", near=token_box(4, tokens, 0))
    assert first is not None and near is not None and first[1] < near[1]
    assert locate(page, "Contract", within=token_box(4, tokens, 0)) == pytest.approx(
        token_box(4, tokens, 0)
    )
    assert locate(page, "Not on the page") is None
    assert locate(None, "Owner") is None
    assert fold_chars(":selected: Fee  Simple") == "feesimple"


# --------------------------------------------------------------------------- checks


def check(*items: GroundedValue, asked: tuple[str, ...] | None = None) -> Any:
    geometry = from_raw(layout())
    texts = labelled_texts(geometry)
    keys = asked or tuple(i.key for i in items)
    return check_answer(GroundedAnswer(fields=list(items)), keys, texts, [1, 2, 3], geometry)


def test_check_accepts_a_grounded_value_with_its_region() -> None:
    out = check(
        value("listing_and_contract.contract_price", "$ 412,000", 1, "Contract Price $ 412,000")
    )
    item = out.fields["listing_and_contract.contract_price"]
    assert (item.value_num, item.page, item.source) == (412000, 1, "llm")
    assert item.confidence == LLM_CONFIDENCE_CAP
    tokens = PAGE_1[4].split(" ")
    [region] = out.regions["listing_and_contract.contract_price"]
    assert region["page"] == 1
    assert region["bbox"] == pytest.approx(
        [
            token_box(4, tokens, 0)[0],
            token_box(4, tokens, 0)[1],
            token_box(4, tokens, 3)[2],
            token_box(4, tokens, 3)[3],
        ],
        abs=1e-4,
    )
    assert out.quotes["listing_and_contract.contract_price"] == "Contract Price $ 412,000"
    assert out.dropped == {}


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (
            value(
                "listing_and_contract.contract_price", "$ 412,000", 1, "Contract Price $ 421,000"
            ),
            "quote_not_on_page",
        ),
        (
            value(
                "listing_and_contract.contract_price", "$ 412,000", 2, "Contract Price $ 412,000"
            ),
            "quote_not_on_page",
        ),
        (
            value(
                "listing_and_contract.contract_price", "$ 500,000", 1, "Contract Price $ 412,000"
            ),
            "value_not_in_quote",
        ),
        (
            value(
                "listing_and_contract.contract_price", "$ 412,000", 9, "Contract Price $ 412,000"
            ),
            "bad_page",
        ),
        (
            value(
                "listing_and_contract.contract_price", "$ 412,000", None, "Contract Price $ 412,000"
            ),
            "bad_page",
        ),
        (value("listing_and_contract.contract_price", "$ 412,000", 1, None), "no_quote"),
        (value("listing_and_contract.contract_price", None, 1, "Contract Price"), "not_found"),
        (value("subject.occupant", "Tenant", 1, PAGE_1[2]), "checkbox_not_selected"),
        (value("site.utilities_water", "Municipal", 2, PAGE_2[1]), "checkbox_not_selected"),
        (
            value("improvements.year_built", "Owner", 1, "Occupant :selected: Owner"),
            "unparseable:not an integer",
        ),
    ],
)
def test_check_drops(item: GroundedValue, reason: str) -> None:
    out = check(item)
    assert out.fields == {}
    assert out.dropped[item.key].startswith(reason.split(":")[0])


def test_check_checkbox_values_and_statuses() -> None:
    out = check(
        value("subject.occupant", "Owner", 1, PAGE_1[2]),
        value("improvements.foundation_type", "Concrete Slab, Crawl Space", 2, PAGE_2[4]),
        value("site.utilities_water", "Other Municipal well", 2, PAGE_2[1]),
        value("reconciliation.indicated_value_income_approach", None, None, None, "not_developed"),
        value("appraiser.name", "Ignored", 1, "ignored", "absent"),
    )
    assert out.fields["subject.occupant"].value_text == "Owner"
    assert out.fields["improvements.foundation_type"].value_text == "Concrete Slab, Crawl Space"
    assert out.fields["site.utilities_water"].value_text == "Other Municipal well"
    assert out.statuses == {
        "reconciliation.indicated_value_income_approach": "not_developed",
        "appraiser.name": "absent",
    }
    assert "appraiser.name" not in out.fields
    # The checkbox region covers the checked label's words.
    [owner] = out.regions["subject.occupant"]
    assert owner["page"] == 1


def test_check_not_asked_duplicate_and_unanswered() -> None:
    out = check(
        value("subject.occupant", "Owner", 1, PAGE_1[2]),
        value("subject.occupant", "Tenant", 1, PAGE_1[2]),
        value("subject.city", "Springfield", 1, "Springfield"),
        asked=("subject.occupant", "subject.zip"),
    )
    assert out.fields["subject.occupant"].value_text == "Owner"
    assert out.dropped == {
        "subject.occupant": "duplicate",
        "subject.city": "not_asked",
        "subject.zip": "not_answered",
    }


# --------------------------------------------------------------------------- systems


def answer(*items: GroundedValue) -> ScriptedTurn:
    return ScriptedTurn(parsed=GroundedAnswer(fields=list(items)))


def run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def test_llm_all_one_call_every_key_with_marks() -> None:
    provider = ScriptedProvider(
        [answer(value("subject.occupant", "Owner", 1, PAGE_1[2]))], model="gpt-6-luna"
    )
    env = make_env(layout=RawClient(layout()), provider=provider)
    output = run(llm_all(MODEL, dpi=72, min_page_words=3).run(env))
    [request] = provider.requests
    assert request.prompt_version == "appraisal_fields_grounded/v2"
    assert (request.model, request.reasoning_effort, request.max_tokens) == (
        "gpt-6-luna",
        "low",
        8000,
    )
    content = request.messages[0].content
    assert ":selected:[Owner] Owner :unselected:[Tenant] Tenant" in content  # labelled marks
    assert "<page number=3>" in content  # the text of every page
    assert all(f"- {key}:" in content for key in ASKED_KEYS)
    assert "site.area_acres" not in content  # derived keys are never asked
    assert '- subject.occupant: printed as "Occupant" (checkbox)' in content
    assert request.messages[0].images == ()
    assert output.result.fields["subject.occupant"].value_text == "Owner"
    assert output.result.method == "llm_all"
    assert output.origins == {"subject.occupant": "llm"}
    assert output.result.needs_review  # critical fields missing
    assert output.result.fallback["dropped"]["subject.zip"] == "not_answered"
    assert env.call.budget.calls == 1


def test_llm_pages_sends_images_of_form_pages_only() -> None:
    geometry = from_raw(layout())
    assert is_form_page(geometry.page(1), 3)  # type: ignore[arg-type]
    assert not is_form_page(geometry.page(3), 3)  # type: ignore[arg-type]  # figures cover it
    assert not is_form_page(geometry.page(1), 100)  # type: ignore[arg-type]  # too few words
    assert form_pages(geometry, [1, 2, 3], 3) == [1, 2]
    provider = ScriptedProvider([answer()], model="claude-haiku-5-5")
    env = make_env(layout=RawClient(layout()), provider=provider, pdf=pdf_bytes(PAGES))
    output = run(
        llm_pages(ModelChoice("claude-haiku-5-5", "low"), dpi=50, min_page_words=3).run(env)
    )
    [request] = provider.requests
    images = request.messages[0].images
    assert len(images) == 2
    assert all(base64.b64decode(i.data_base64).startswith(b"\x89PNG") for i in images)
    assert "Images of pages 1, 2 are attached, in that order." in request.messages[0].content
    assert output.result.fallback["calls"][0]["images"] == [1, 2]
    # Rendering is deterministic: the same bytes again (the request hash depends on it).
    again = ScriptedProvider([answer()], model="claude-haiku-5-5")
    env2 = make_env(layout=RawClient(layout()), provider=again, pdf=pdf_bytes(PAGES))
    run(llm_pages(ModelChoice("claude-haiku-5-5", "low"), dpi=50, min_page_words=3).run(env2))
    assert again.requests[0].messages[0].images == images


def test_llm_sections_one_call_per_group_every_page() -> None:
    turns = [answer() for _ in SECTION_GROUPS]
    turns[0] = answer(value("subject.occupant", "Owner", 1, PAGE_1[2]))
    provider = ScriptedProvider(turns, model="gpt-6-luna")
    env = make_env(layout=RawClient(layout()), provider=provider, pdf=pdf_bytes(PAGES))
    system = llm_sections(MODEL, dpi=50, min_page_words=3, with_images=True)
    output = run(system.run(env))
    assert len(provider.requests) == len(SECTION_GROUPS)
    # Every call gets every page's text (heading routing lost page 1 on the synthetic 1004s)
    # and the images of the form pages (the photo page has none).
    for call in output.result.fallback["calls"]:
        assert call["pages"] == [1, 2, 3]
        assert call["images"] == [1, 2]
    asked = [k for r in provider.requests for k in ASKED_KEYS if f"- {k}:" in r.messages[0].content]
    assert sorted(asked) == sorted(ASKED_KEYS)  # each key asked once, in exactly one call
    assert output.result.fields["subject.occupant"].value_text == "Owner"


def test_answer_checked_against_its_own_call_pages() -> None:
    turns = [answer() for _ in SECTION_GROUPS]
    turns[2] = answer(value("improvements.year_built", "1,740", 4, "Gross Living Area 1,740"))
    provider = ScriptedProvider(turns, model="gpt-6-luna")
    env = make_env(layout=RawClient(layout()), provider=provider)
    output = run(llm_sections(MODEL, dpi=50, min_page_words=3, with_images=False).run(env))
    assert output.result.fallback["dropped"]["improvements.year_built"] == "bad_page"


def test_provider_error_and_budget_are_statuses() -> None:
    error = ProviderUnavailable("down", provider="scripted", status=503)
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)], model="gpt-6-luna")
    env = make_env(layout=RawClient(layout()), provider=provider)
    output = run(llm_all(MODEL, dpi=50, min_page_words=3).run(env))
    assert output.result.fallback["status"] == "provider_error"
    assert output.result.fields == {}
    broke = make_env(layout=RawClient(layout()), provider=ScriptedProvider([]), budget_usd=1e-6)
    out = run(llm_all(ModelChoice("claude-opus-5-5", "low"), dpi=50, min_page_words=3).run(broke))
    assert out.result.fallback["status"] == "budget_exceeded"


def test_needs_the_raw_layout() -> None:
    from evals.appraisal.systems.base import MissingInput

    env = make_env(layout=RawClient(layout(), keep_raw=False), provider=ScriptedProvider([]))
    with pytest.raises(MissingInput, match="raw Azure DI layout"):
        run(llm_all(MODEL, dpi=50, min_page_words=3).run(env))
    with pytest.raises(MissingInput, match="layout analysis"):
        run(llm_all(MODEL, dpi=50, min_page_words=3).run(make_env(provider=ScriptedProvider([]))))
