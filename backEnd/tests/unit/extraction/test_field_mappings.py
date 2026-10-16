"""The field catalogue. Replaces ``tests/regression/test_legacy_critical_fields_mapping.py``.

Legacy A.8: ``CRITICAL_FIELDS`` named fields (``gross_living_area``, ``effective_date``,
``appraised_value``) that no mapping produced, so two critical fields always read as
missing and every extraction went to review. The invariant, on the new module: every
critical field is a catalogue key that the Azure DI mapping and the regex extractor both
produce, and every consumer looks fields up by that same key.
"""

from __future__ import annotations

from typing import Any

import pytest
from fixtures import load_fixture_json
from llm_fakes import load_azure_fixture

from basis.extraction.field_mappings import (
    COST_TABLE_ROWS,
    CRITICAL_FIELDS,
    FIELD_BY_KEY,
    FIELDS,
    SECTION_NAMES,
    all_labels,
    key_for_label,
    normalize_label,
    section_for_heading,
)
from basis.extraction.pdf_text import read_pdf
from basis.extraction.regex_fields import extract_appraisal_fields
from basis.extraction.sections import azure_candidates
from basis.extraction.validation import validate_fields

GOLDEN: dict[str, Any] = load_fixture_json("urar_sample.expected.json")


def test_every_critical_field_is_a_catalogue_key() -> None:
    assert CRITICAL_FIELDS, "no critical fields"
    assert set(CRITICAL_FIELDS) <= set(FIELD_BY_KEY)


# EVAL_REPORT section 25: the room, bedroom and bathroom counts are critical too (the
# labelled critical fields the live run missed); the golden file names the S3 six.
ROOM_COUNTS = ["improvements.total_rooms", "improvements.bedrooms", "improvements.bathrooms"]


def test_critical_fields_are_the_golden_six_and_the_room_counts() -> None:
    assert sorted(CRITICAL_FIELDS) == sorted([*GOLDEN["critical_fields"], *ROOM_COUNTS])


def test_every_critical_field_is_produced_by_azure_di_and_by_the_text_layer() -> None:
    from fixtures import fixture_path

    azure_keys = {c.key for c in azure_candidates(load_azure_fixture())[0]}
    pdf = read_pdf(fixture_path("urar_sample.pdf").read_bytes())
    regex_keys = {c.key for c in extract_appraisal_fields(pdf.pages)}
    missing_azure = sorted(set(CRITICAL_FIELDS) - azure_keys)
    missing_regex = sorted(set(CRITICAL_FIELDS) - regex_keys)
    assert not missing_azure, f"Azure DI mapping never produces {missing_azure}"
    assert not missing_regex, f"regex extraction never produces {missing_regex}"


def test_the_legacy_orphaned_names_are_gone() -> None:
    for legacy in ("gross_living_area", "effective_date", "appraised_value"):
        assert legacy not in CRITICAL_FIELDS
        assert f"improvements.{legacy}" not in CRITICAL_FIELDS


def test_validation_reads_critical_fields_by_the_same_keys() -> None:
    issues = validate_fields({})
    missing = {issue.keys[0] for issue in issues if issue.rule == "missing_critical"}
    assert missing == set(CRITICAL_FIELDS)


def test_keys_are_section_dot_field_and_unique() -> None:
    keys = [spec.key for spec in FIELDS]
    assert len(keys) == len(set(keys))
    for spec in FIELDS:
        section, field = spec.key.split(".")
        assert section in SECTION_NAMES and field.isidentifier()


def test_every_golden_field_is_in_the_catalogue_with_its_type() -> None:
    type_map = {"int": "int", "float": "float", "currency": "currency", "percent": "percent"}
    for golden in GOLDEN["fields"]:
        spec = FIELD_BY_KEY.get(golden["key"])
        assert spec is not None, golden["key"]
        assert golden["label"] in spec.labels
        if golden["type"] in type_map and spec.type != "currency_or_text":
            assert spec.type == type_map[golden["type"]], golden["key"]


def test_ambiguous_captions_resolve_by_section() -> None:
    assert key_for_label("Indicated Value by Cost Approach", "reconciliation") == (
        "reconciliation.indicated_value_cost_approach"
    )
    assert key_for_label("Indicated Value by Cost Approach", "cost_approach") == (
        "cost_approach.indicated_value"
    )
    assert key_for_label("State", "subject") == "subject.state"
    assert key_for_label("State", "appraiser") == "appraiser.license_state"
    assert key_for_label("State", None) is None  # ambiguous without a section


def test_matching_is_exact_after_normalization_not_substring() -> None:
    assert key_for_label("YEAR BUILT:", None) == "improvements.year_built"
    assert key_for_label("  Gross   Living Area ", None) == "improvements.gla_sqft"
    assert key_for_label("Assessor’s Parcel #", "subject") == "subject.assessors_parcel"
    # The legacy fuzzy table mapped these by substring; they are not fields.
    assert key_for_label("Mailing Address", None) is None
    assert key_for_label("Design", None) is None
    assert key_for_label("Client", None) is None


def test_headings() -> None:
    assert section_for_heading("Sales Comparison Approach (continued)") == "sales_comparison"
    assert section_for_heading("Contract") == "listing_and_contract"
    assert section_for_heading("Site Area: 10,000 sf") is None
    assert normalize_label("Site:") == "site"


def test_labels_longest_first() -> None:
    labels = all_labels()
    assert labels.index("Indicated Value by Sales Comparison Approach") < labels.index(
        "Indicated Value by Sales Comparison"
    )


@pytest.mark.parametrize("key", CRITICAL_FIELDS)
def test_critical_fields_have_typed_values(key: str) -> None:
    assert FIELD_BY_KEY[key].type in ("text", "int", "float", "currency", "date")


def test_uncaptioned_boxes_are_read_only_by_the_model() -> None:
    """EVAL_REPORT 25: boxes whose caption is not unique ("Low", "Sq.Ft." in a cost row) are
    never matched from Azure DI or the regex pass, and never a cost-table row."""
    uncaptioned = [spec for spec in FIELDS if not spec.captioned]
    assert {spec.key for spec in uncaptioned} >= {
        "neighborhood.price_low_thousands",
        "cost_approach.dwelling_sqft",
        "site.area",
        "site.area_acres",
    }
    labels = set(all_labels())
    for spec in uncaptioned:
        assert spec.label not in labels
        assert key_for_label(spec.label, spec.section) is None
        assert spec.key not in COST_TABLE_ROWS
    # The captioned additions are matched in their section.
    assert key_for_label("Foundation Walls", "improvements") == "improvements.foundation_walls"
    assert key_for_label("Floors", "improvements") == "improvements.floors"
