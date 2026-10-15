"""Validation rules: each rule fires on a field that breaks it and not on the golden data."""

from __future__ import annotations

from datetime import date

import pytest
from llm_fakes import load_azure_fixture

from basis.extraction.results import Candidate, ExtractedField
from basis.extraction.sections import azure_candidates, merge
from basis.extraction.validation import failed_critical_keys, validate_fields

TODAY = date(2026, 10, 15)


def golden() -> dict[str, ExtractedField]:
    fields, _ = merge(azure_candidates(load_azure_fixture())[0], [])
    return fields


def with_values(**values: str) -> dict[str, ExtractedField]:
    fields = golden()
    for dotted, text in values.items():
        key = dotted.replace("__", ".")
        fields[key] = ExtractedField.from_candidate(
            Candidate(key=key, raw_text=text, confidence=0.97, source="azure")
        )
    return fields


def rules(fields: dict[str, ExtractedField]) -> set[str]:
    return {issue.rule for issue in validate_fields(fields, today=TODAY)}


def test_the_golden_extraction_validates() -> None:
    assert validate_fields(golden(), today=TODAY) == []


def test_missing_critical_fields() -> None:
    fields = golden()
    del fields["improvements.gla_sqft"]
    [issue] = validate_fields(fields, today=TODAY)
    assert (issue.rule, issue.keys, issue.severity) == (
        "missing_critical",
        ("improvements.gla_sqft",),
        "error",
    )


def test_type_failure_is_an_error_on_critical_and_a_warning_elsewhere() -> None:
    issues = validate_fields(
        with_values(improvements__year_built="nineteen", subject__tax_year="soon"), today=TODAY
    )
    by_key = {i.keys[0]: i for i in issues}
    assert by_key["improvements.year_built"].severity == "error"
    assert by_key["subject.tax_year"].severity == "warning"
    assert {i.rule for i in issues} == {"type"}


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("improvements__year_built", "1650"),
        ("improvements__year_built", "2031"),
        ("improvements__gla_sqft", "40 sq ft"),
        ("listing_and_contract__contract_price", "$0"),
        ("neighborhood__present_land_use_one_unit", "180%"),
        ("reconciliation__effective_date_of_appraisal", "01/01/1850"),
        ("reconciliation__effective_date_of_appraisal", "01/01/2099"),
        ("improvements__bedrooms", "-1"),
    ],
)
def test_range_failures(field: str, text: str) -> None:
    assert "range" in rules(with_values(**{field: text}))


def test_rooms_must_cover_bedrooms_and_bathrooms() -> None:
    assert "rooms_vs_bed_bath" in rules(with_values(improvements__total_rooms="4"))
    assert "rooms_vs_bed_bath" not in rules(with_values(improvements__total_rooms="6"))


def test_value_within_50_percent_of_cost_approach() -> None:
    fields = with_values(reconciliation__final_market_value="$900,000")
    issues = validate_fields(fields, today=TODAY)
    assert "value_vs_cost" in {i.rule for i in issues}
    # 629,775 is exactly 50% above 419,850: still allowed.
    assert "value_vs_cost" not in rules(with_values(reconciliation__final_market_value="$629,775"))


def test_value_vs_cost_uses_the_cost_table_when_reconciliation_lacks_it() -> None:
    fields = with_values(reconciliation__final_market_value="$900,000")
    del fields["reconciliation.indicated_value_cost_approach"]
    assert "value_vs_cost" in rules(fields)
    del fields["cost_approach.indicated_value"]
    assert "value_vs_cost" not in rules(fields)


def test_legacy_warnings_contract_and_basement() -> None:
    issues = validate_fields(
        with_values(
            listing_and_contract__contract_price="$300,000",
            improvements__basement_area_sqft="3,000 sq ft",
        ),
        today=TODAY,
    )
    warnings = {i.rule: i.severity for i in issues}
    assert warnings == {"value_vs_contract": "warning", "basement_vs_gla": "warning"}


def test_failed_critical_keys_are_missing_or_per_field_failures_only() -> None:
    fields = with_values(
        improvements__year_built="nineteen", reconciliation__final_market_value="$900,000"
    )
    del fields["improvements.gla_sqft"]
    issues = validate_fields(fields, today=TODAY)
    # value_vs_cost is cross-field: a person resolves it, the fallback does not.
    assert failed_critical_keys(fields, issues) == [
        "improvements.year_built",
        "improvements.gla_sqft",
    ]


def test_not_developed_income_approach_is_valid() -> None:
    fields = golden()
    assert fields["reconciliation.indicated_value_income_approach"].value_text == "Not Developed"
    assert fields["reconciliation.indicated_value_income_approach"].parse_error is None
