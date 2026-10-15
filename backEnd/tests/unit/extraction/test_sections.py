"""Azure DI payload -> fields, and the merge rule (fallbacks only fill empty values)."""

from __future__ import annotations

from decimal import Decimal

from llm_fakes import load_azure_fixture

from basis.extraction.azure_di import AzureDiKeyValue, AzureDiPage, AzureDiResult
from basis.extraction.results import Candidate, ExtractionResult
from basis.extraction.sections import azure_candidates, merge

from .golden import compare


def c(key: str, text: str, source: str = "regex", confidence: float = 0.8) -> Candidate:
    return Candidate(key=key, raw_text=text, confidence=confidence, source=source)  # type: ignore[arg-type]


def test_the_fixture_maps_to_every_golden_field_exactly() -> None:
    candidates, comparables = azure_candidates(load_azure_fixture())
    fields, issues = merge(candidates, [])
    hits, misses = compare(fields)
    assert misses == [] and len(hits) == 110
    assert issues == []
    assert len(comparables) == 3
    assert {f.source for f in fields.values()} == {"azure"}


def test_key_value_confidence_and_page_are_kept() -> None:
    result = load_azure_fixture()
    pair = next(p for p in result.key_value_pairs if p.key == "Year Built")
    candidates, _ = azure_candidates(result)
    year = next(x for x in candidates if x.key == "improvements.year_built")
    assert (year.confidence, year.page, year.raw_text) == (pair.confidence, 3, "1987")


def test_a_caption_is_placed_in_the_section_it_is_printed_in() -> None:
    result = AzureDiResult(
        pages=(
            AzureDiPage(page_number=1, lines=("Subject", "State:", "XX")),
            AzureDiPage(
                page_number=2,
                lines=("Appraiser Certification and Signature", "State:", "YY"),
            ),
        ),
        key_value_pairs=(
            AzureDiKeyValue(key="State", value="XX", confidence=0.9, page=1),
            AzureDiKeyValue(key="State", value="YY", confidence=0.9, page=2),
        ),
    )
    found = {x.key: x.raw_text for x in azure_candidates(result)[0]}
    assert found == {"subject.state": "XX", "appraiser.license_state": "YY"}


def test_unknown_keys_and_empty_values_are_ignored() -> None:
    result = AzureDiResult(
        pages=(AzureDiPage(page_number=1, lines=("Subject",)),),
        key_value_pairs=(
            AzureDiKeyValue(key="Mailing Address", value="PO Box 1", confidence=0.99, page=1),
            AzureDiKeyValue(key="Year Built", value="  ", confidence=0.99, page=1),
            AzureDiKeyValue(key="Year Built", value=None, confidence=0.99, page=1),
        ),
    )
    assert azure_candidates(result)[0] == []


def test_merge_keeps_the_primary_and_fills_only_gaps() -> None:
    fields, issues = merge(
        [c("subject.city", "Springfield", "azure", 0.97)],
        [c("subject.city", "Elsewhere"), c("subject.county", "Example County")],
    )
    assert fields["subject.city"].value_text == "Springfield"
    assert fields["subject.county"].source == "regex"
    assert issues == []  # disagreement on a non-critical field is not an issue


def test_agreement_is_recorded() -> None:
    fields, _ = merge(
        [c("improvements.gla_sqft", "1,850", "azure", 0.97)],
        [c("improvements.gla_sqft", "1850 sq ft")],
    )
    assert fields["improvements.gla_sqft"].corroborated_by == ("regex",)


def test_disagreement_on_a_critical_field_is_an_issue() -> None:
    fields, issues = merge(
        [c("improvements.gla_sqft", "1,580", "azure", 0.97)], [c("improvements.gla_sqft", "1,850")]
    )
    assert fields["improvements.gla_sqft"].value_num == Decimal("1580")
    [issue] = issues
    assert issue.rule == "sources_disagree" and issue.keys == ("improvements.gla_sqft",)


def test_an_unparseable_primary_is_replaced_by_a_parseable_fallback() -> None:
    fields, _ = merge(
        [c("improvements.year_built", "see addendum", "azure", 0.97)],
        [c("improvements.year_built", "1987")],
    )
    assert fields["improvements.year_built"].value_num == Decimal("1987")
    assert fields["improvements.year_built"].source == "regex"


def test_empty_values_never_overwrite() -> None:
    fields, _ = merge([c("subject.city", "Springfield", "azure")], [c("subject.city", "N/A")])
    assert fields["subject.city"].value_text == "Springfield"
    fields, _ = merge([c("subject.city", "-", "azure")], [])
    assert "subject.city" not in fields


def test_sections_shape_groups_typed_values() -> None:
    fields, _ = merge(azure_candidates(load_azure_fixture())[0], [])
    result = ExtractionResult(doc_id="d", method="azure_di+regex", page_count=7, fields=fields)
    shaped = result.sections()
    assert shaped["subject"]["property_address"] == "1290 Example Way"
    assert shaped["improvements"]["gla_sqft"] == 1850
    assert shaped["improvements"]["bathrooms"] == 2.1
    assert shaped["reconciliation"]["effective_date_of_appraisal"] == "2026-10-01"
    assert set(shaped) >= {"subject", "site", "improvements", "cost_approach", "appraiser"}
