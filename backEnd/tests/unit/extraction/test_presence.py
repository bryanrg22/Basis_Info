"""Presence rules: approach / cost-line values not developed, certification restatements."""

from __future__ import annotations

import pytest

from basis.extraction.field_mappings import FIELDS
from basis.extraction.presence import (
    APPROACH_VALUE_KEYS,
    CERTIFICATION_KEYS,
    COST_LINE_KEYS,
    apply_presence_rules,
    signature_pages,
)
from basis.extraction.results import Candidate, ExtractedField

SIGNATURE_PAGE = "\n".join(
    [
        "APPRAISER SUPERVISORY APPRAISER (ONLY IF REQUIRED)",
        "Date of Signature and Report 09/08/2024",
        "Effective Date of Appraisal 08/30/2024",
        "APPRAISED VALUE OF SUBJECT PROPERTY $ 570,000",
    ]
)
RECONCILIATION_PAGE = "\n".join(
    [
        "Indicated Value by: Sales Comparison Approach $ 570,000 Cost Approach (if developed) $",
        "this report is subject to the appraiser's certification",
        "$ 570,000 , as of 08/30/2024 , which is the date of inspection and the effective date",
    ]
)


def field(key: str, text: str, page: int | None = 2, source: str = "llm") -> ExtractedField:
    return ExtractedField.from_candidate(
        Candidate(key=key, raw_text=text, page=page, confidence=0.6, source=source)  # type: ignore[arg-type]
    )


def run(*items: ExtractedField, pages: list[str] | None = None, not_on_form=frozenset()):  # type: ignore[no-untyped-def]
    return apply_presence_rules(
        {item.key: item for item in items},
        [pages if pages is not None else [RECONCILIATION_PAGE]],
        not_on_form=not_on_form,
    )


# ------------------------------------------------------------- rule a: approach not developed


@pytest.mark.parametrize(
    ("key", "text", "reason"),
    [
        ("reconciliation.indicated_value_income_approach", "$ 0", "zero"),
        ("reconciliation.indicated_value_income_approach", "0", "zero"),
        ("reconciliation.indicated_value_income_approach", "-0-", "zero"),
        ("reconciliation.indicated_value_income_approach", "$", "no_number"),
        (
            "reconciliation.indicated_value_cost_approach",
            "Not Necessary for Credible Results",
            "no_number",
        ),
        ("reconciliation.indicated_value_sales_comparison", "Not Developed", "no_number"),
        ("sales_comparison.indicated_value", "None", "no_number"),
        ("cost_approach.indicated_value", "=$", "no_number"),
        ("cost_approach.site_value", "= $", "no_number"),
        ("cost_approach.total_cost_new", "Not Necessary for Credible Results", "no_number"),
        ("cost_approach.depreciation_external", "0", "zero"),
        ("cost_approach.depreciation_functional", "$0.00", "zero"),
        ("cost_approach.basement_sqft", "0", "zero"),
        ("cost_approach.garage_cost_per_sqft", "0.00", "zero"),
    ],
)
def test_an_approach_or_cost_line_with_no_amount_or_zero_is_not_developed(
    key: str, text: str, reason: str
) -> None:
    out = run(field(key, text))
    assert out.fields == {}
    assert out.dropped == {key: f"not_developed:{reason}"}


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("reconciliation.indicated_value_income_approach", "$ 3,250"),
        ("reconciliation.indicated_value_cost_approach", "$ 682,831"),
        ("cost_approach.depreciation_physical", "=$( 156,240)"),
        ("cost_approach.site_value", "Site value $85,000 (extraction)"),
        ("sales_comparison.indicated_value", "$570,000"),
    ],
)
def test_a_developed_amount_is_kept(key: str, text: str) -> None:
    item = field(key, text)
    out = run(item)
    assert out.fields == {key: item} and out.dropped == {}


@pytest.mark.parametrize(
    ("key", "text"),
    [
        # SPEC principle 2: outside the approaches a printed 0 / No is a real value
        ("subject.hoa_dues", "$0"),
        ("subject.special_assessments", "0"),
        ("listing_and_contract.seller_concessions", "No"),
        ("improvements.basement_area_sqft", "0 Sq. Ft."),
        ("improvements.basement_finish_percent", "0"),
        ("improvements.fireplaces", "0"),
        ("neighborhood.present_land_use_multi_family", "0"),
        ("listing_and_contract.days_on_market", "0"),
        # the cost approach's non-line boxes
        ("cost_approach.remaining_economic_life", "0"),
        ("cost_approach.cost_data_source", "Not Necessary for Credible Results"),
        ("cost_approach.quality_rating_from_cost_service", "N/A - not developed"),
        # a "no number" value of a key outside the scope
        ("listing_and_contract.contract_price", "$"),
        ("subject.hoa_dues", "None"),
    ],
)
def test_keys_outside_the_approaches_are_never_touched(key: str, text: str) -> None:
    item = field(key, text)
    out = run(item)
    assert out.fields == {key: item} and out.dropped == {}


def test_the_scope_is_the_approach_values_and_the_cost_lines() -> None:
    cost = {s.key for s in FIELDS if s.section == "cost_approach"}
    assert COST_LINE_KEYS <= cost
    assert cost - COST_LINE_KEYS == {
        "cost_approach.remaining_economic_life",
        "cost_approach.cost_data_source",
        "cost_approach.quality_rating_from_cost_service",
        "cost_approach.effective_date_of_cost_data",
    }
    assert {k.split(".")[0] for k in APPROACH_VALUE_KEYS} == {
        "sales_comparison",
        "reconciliation",
        "cost_approach",
    }
    assert "reconciliation.final_market_value" not in APPROACH_VALUE_KEYS


def test_an_empty_field_is_left_alone() -> None:
    item = field("reconciliation.indicated_value_income_approach", "N/A")
    assert item.is_empty  # the cleaner reads N/A as no value
    out = run(item)
    assert out.fields == {item.key: item} and out.dropped == {}


# ------------------------------------------------------- rule b: certification restatements


def test_signature_pages_are_found_by_the_signature_caption_or_a_section_heading() -> None:
    pages = [RECONCILIATION_PAGE, "Comments", SIGNATURE_PAGE, "Signatures\nAppraiser: A. B."]
    assert signature_pages(pages) == {3, 4}
    # any reader's text counts (a scan has only Azure DI's)
    assert signature_pages(["", ""], ["", SIGNATURE_PAGE]) == {2}
    # the reconciliation mentions the certification and the effective date: not the block
    assert signature_pages([RECONCILIATION_PAGE]) == frozenset()


@pytest.mark.parametrize("key", sorted(CERTIFICATION_KEYS))
def test_a_certification_box_cited_off_the_signature_page_is_dropped(key: str) -> None:
    text = "$ 570,000" if key.endswith("value") else "08/30/2024"
    pages = [RECONCILIATION_PAGE, SIGNATURE_PAGE]
    kept = field(key, text, page=2)
    assert run(kept, pages=pages).fields == {key: kept}
    out = run(field(key, text, page=1), pages=pages)
    assert out.fields == {} and out.dropped == {key: "absent:not_on_signature_page"}
    out = run(field(key, text, page=None), pages=pages)
    assert out.dropped == {key: "absent:no_page"}
    # a document without its certification pages (the client 1004)
    out = run(field(key, text, page=1), pages=[RECONCILIATION_PAGE])
    assert out.dropped == {key: "absent:not_on_signature_page"}


def test_a_certification_box_the_form_does_not_carry_is_dropped() -> None:
    item = field("appraiser.appraised_value", "$ 570,000", page=1)
    out = run(item, pages=[SIGNATURE_PAGE], not_on_form=CERTIFICATION_KEYS)
    assert out.dropped == {"appraiser.appraised_value": "absent:not_on_form"}


def test_other_appraiser_keys_are_not_held_to_the_signature_page() -> None:
    items = [
        field("appraiser.name", "Willa Oakhurst", page=1),
        field("appraiser.company", "Currie Appraisal", page=1),
        field("appraiser.signature_date", "09/08/2024", page=1),
        field("reconciliation.final_market_value", "$ 570,000", page=1),
        field("reconciliation.effective_date_of_appraisal", "08/30/2024", page=1),
    ]
    out = run(*items)
    assert out.dropped == {} and set(out.fields) == {i.key for i in items}


def test_every_source_is_held_to_the_rules() -> None:
    azure = field("cost_approach.site_value", "=$", page=3, source="azure")
    out = run(azure)
    assert out.dropped == {"cost_approach.site_value": "not_developed:no_number"}
