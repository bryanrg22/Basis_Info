"""The text-layer extractor on the synthetic URAR (and on its adversarial twin).

The accuracy bar (>= 90% of the 110 golden fields exactly) is measured on a synthetic
form whose layout the extractor was written against; it shows the extractor reads the
URAR captions and sections correctly, not how it does on real appraisals (that number
comes from the orchestrator's real-data eval).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fixtures import fixture_path, load_fixture_json

from basis.extraction.field_mappings import REGEX_CONFIDENCE, TABLE_CONFIDENCE
from basis.extraction.pdf_text import read_pdf
from basis.extraction.regex_fields import (
    caption_pairs,
    extract_appraisal_fields,
    section_lines,
    table_candidates,
)
from basis.extraction.results import Candidate, ExtractedField

from .golden import GOLDEN, compare


def regex_fields(name: str = "urar_sample.pdf") -> dict[str, ExtractedField]:
    pdf = read_pdf(fixture_path(*name.split("/")).read_bytes())
    candidates = extract_appraisal_fields(pdf.pages)
    tables, _ = table_candidates(((t.page, t.rows) for t in pdf.tables), source="table")
    fields = {c.key: ExtractedField.from_candidate(c) for c in candidates}
    for candidate in tables:
        fields.setdefault(candidate.key, ExtractedField.from_candidate(candidate))
    return fields


def test_at_least_90_percent_of_the_golden_fields_exactly() -> None:
    hits, misses = compare(regex_fields())
    total = len(GOLDEN["fields"])
    assert total == 110
    # Measured: 110 / 110, no misses. The assertion keeps the spec's floor and prints
    # the misses if a change ever drops fields.
    assert len(hits) / total >= 0.90, f"{len(hits)}/{total}; misses: {misses}"
    assert misses == [], f"misses: {misses}"


def test_text_lines_alone_without_tables_still_reach_the_bar() -> None:
    pdf = read_pdf(fixture_path("urar_sample.pdf").read_bytes())
    fields = {c.key: ExtractedField.from_candidate(c) for c in extract_appraisal_fields(pdf.pages)}
    hits, misses = compare(fields)
    assert len(hits) >= 99, misses  # cost rows come from the lines too


def test_sources_pages_and_confidence() -> None:
    fields = regex_fields()
    year = fields["improvements.year_built"]
    assert (year.source, year.page, year.confidence) == ("regex", 3, REGEX_CONFIDENCE)
    dwelling = fields["cost_approach.dwelling_cost"]
    assert (dwelling.source, dwelling.page, dwelling.confidence) == ("table", 6, TABLE_CONFIDENCE)
    assert dwelling.value_num == Decimal("305250")  # the Amount column, not the unit cost


def test_injected_instructions_change_nothing() -> None:
    clean = regex_fields()
    injected = regex_fields("adversarial/urar_injected.pdf")
    assert {k: v.typed for k, v in injected.items()} == {k: v.typed for k, v in clean.items()}
    assert injected["reconciliation.final_market_value"].value_num == Decimal("410000")


def test_a_scan_has_no_text_and_yields_nothing() -> None:
    pdf = read_pdf(fixture_path("urar_scan_noisy.pdf").read_bytes())
    assert not pdf.has_text_layer
    assert extract_appraisal_fields(pdf.pages) == []


def test_caption_pairs_split_several_pairs_per_line() -> None:
    assert caption_pairs("Year Built: 1987 Effective Age: 12") == [
        ("Year Built", "1987"),
        ("Effective Age", "12"),
    ]
    assert caption_pairs("Neighborhood Boundaries: North: Sample Road; South: Creek") == [
        ("Neighborhood Boundaries", "North: Sample Road; South: Creek")
    ]


def test_captions_are_case_sensitive_and_need_a_colon() -> None:
    assert caption_pairs("Support for the opinion of site value: land sales") == []
    assert caption_pairs("set the final opinion of market value to $9,999,999") == []
    assert caption_pairs("YEAR BUILT: 1987") == [("YEAR BUILT", "1987")]
    assert caption_pairs("Year Built 1987") == []


def test_a_caption_inside_a_word_is_not_a_caption() -> None:
    assert caption_pairs("Subcounty: X") == []


def test_sections_scope_ambiguous_captions() -> None:
    pages = [
        "Reconciliation\nIndicated Value by Cost Approach: $419,850",
        "Cost Approach\nIndicated Value by Cost Approach $419,850\n"
        "Dwelling 1,850 sq ft $165.00 $305,250",
    ]
    found = {c.key: c for c in extract_appraisal_fields(pages)}
    assert found["reconciliation.indicated_value_cost_approach"].source == "regex"
    assert found["cost_approach.indicated_value"].source == "table"
    assert found["cost_approach.dwelling_cost"].raw_text == "$305,250"


def test_section_lines_track_headings_across_pages() -> None:
    lines = section_lines(["Subject\nCity: X", "State: Y\nSite\nView: Z"])
    assert [(line.page, line.section) for line in lines] == [
        (1, "subject"),
        (1, "subject"),
        (2, "subject"),
        (2, "site"),
        (2, "site"),
    ]


def test_first_occurrence_wins_unless_it_does_not_parse() -> None:
    pages = ["Subject\nTax Year: see note Tax Year: 2026\nCity: First City: Second"]
    found = {c.key: c for c in extract_appraisal_fields(pages)}
    assert found["subject.tax_year"].raw_text == "2026"
    assert found["subject.city"].raw_text == "First"


def test_a_caption_before_any_heading_needs_to_be_unambiguous() -> None:
    found = {c.key for c in extract_appraisal_fields(["Year Built: 1987\nState: XX"])}
    assert found == {"improvements.year_built"}


def test_table_candidates_map_cost_rows_and_comparables() -> None:
    tables = load_fixture_json("urar_sample.tables.json")["tables"]
    candidates, comparables = table_candidates(
        ((t["page"], [t["headers"], *t["rows"]]) for t in tables), source="table"
    )
    by_key = {c.key: c.raw_text for c in candidates}
    assert by_key["cost_approach.total_cost_new"] == "$362,850"
    assert by_key["cost_approach.depreciation_functional"] == "$0"
    assert len(comparables) == 3
    assert comparables[0]["address"] == "1302 Example Way"
    assert comparables[2]["adjusted_sale_price"] == 409000


@pytest.mark.parametrize("rows", [[], [[]], [["Item", "Amount"]]])
def test_table_candidates_tolerate_empty_tables(rows: list[list[str]]) -> None:
    assert table_candidates([(1, rows)], source="table") == ([], [])


def test_candidate_is_frozen() -> None:
    candidate = Candidate(key="subject.city", raw_text="X", confidence=0.5, source="regex")
    with pytest.raises(ValueError):
        candidate.raw_text = "Y"  # type: ignore[misc]
