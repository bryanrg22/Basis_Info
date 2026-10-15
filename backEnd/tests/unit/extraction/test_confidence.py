"""The review gate and overall confidence."""

from __future__ import annotations

import pytest
from llm_fakes import load_azure_fixture

from basis.extraction.confidence import aggregate_confidence, review_gate
from basis.extraction.field_mappings import CRITICAL_FIELDS
from basis.extraction.results import ExtractedField, ValidationIssue
from basis.extraction.sections import azure_candidates, merge


def golden() -> dict[str, ExtractedField]:
    fields, _ = merge(azure_candidates(load_azure_fixture())[0], [])
    return fields


def with_confidence(fields: dict[str, ExtractedField], key: str, value: float) -> None:
    fields[key] = fields[key].model_copy(update={"confidence": value})


def test_the_golden_extraction_passes_the_gate() -> None:
    fields = golden()
    gate = review_gate(fields, [], threshold=0.90)
    assert not gate.needs_review and gate.reasons == ()
    assert gate.overall_confidence == min(fields[k].confidence for k in CRITICAL_FIELDS)


def test_a_critical_field_below_the_threshold_needs_review() -> None:
    fields = golden()
    with_confidence(fields, "improvements.gla_sqft", 0.89)
    gate = review_gate(fields, [], threshold=0.90)
    assert gate.needs_review
    assert gate.reasons == ("low_confidence:improvements.gla_sqft=0.89<0.90",)
    assert gate.overall_confidence == pytest.approx(0.89)


def test_exactly_at_the_threshold_passes() -> None:
    fields = golden()
    with_confidence(fields, "improvements.gla_sqft", 0.90)
    assert not review_gate(fields, [], threshold=0.90).needs_review


def test_low_confidence_on_a_non_critical_field_does_not_gate() -> None:
    fields = golden()
    with_confidence(fields, "subject.county", 0.10)
    assert not review_gate(fields, [], threshold=0.90).needs_review


def test_threshold_is_a_parameter() -> None:
    fields = golden()
    with_confidence(fields, "improvements.gla_sqft", 0.80)
    assert review_gate(fields, [], threshold=0.75).needs_review is False
    assert review_gate(fields, [], threshold=0.85).needs_review is True


def test_a_missing_critical_field_zeroes_the_overall_confidence() -> None:
    fields = golden()
    del fields["subject.property_address"]
    assert aggregate_confidence(fields) == 0.0
    gate = review_gate(
        fields,
        [
            ValidationIssue(
                rule="missing_critical",
                message="missing",
                keys=("subject.property_address",),
            )
        ],
        threshold=0.90,
    )
    assert gate.reasons == ("missing:subject.property_address",)  # not reported twice


def test_any_validation_issue_needs_review() -> None:
    issue = ValidationIssue(
        rule="basement_vs_gla",
        message="m",
        keys=("improvements.basement_area_sqft",),
        severity="warning",
    )
    gate = review_gate(golden(), [issue], threshold=0.90)
    assert gate.needs_review and gate.reasons == (
        "basement_vs_gla:improvements.basement_area_sqft",
    )


def test_no_fields_at_all() -> None:
    gate = review_gate({}, [], threshold=0.90)
    assert gate.needs_review and gate.overall_confidence == 0.0
    assert len(gate.reasons) == len(CRITICAL_FIELDS)
