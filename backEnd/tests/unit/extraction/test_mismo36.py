"""The UAD 3.6 XML reader (:mod:`basis.extraction.mismo36`) on fabricated XML."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from uad36_fakes import azure_printing, fabricated_xml, unit

from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.extraction.geometry import layout_of
from basis.extraction.mismo36 import (
    MAX_XML_BYTES,
    MISMATCH_RULE,
    InvalidMismo,
    Mismo36Document,
    extract_mismo,
    parse_mismo,
    read_document,
    units_value,
    variants,
)
from basis.extraction.uad36_map import MAPPING


def read(xml: bytes) -> dict[str, str]:
    doc = Mismo36Document(parse_mismo(xml))
    return {key: item.reading.raw for key, item in read_document(doc).items()}


# --------------------------------------------------------------------------- the mapping


def test_every_catalogue_key_has_a_mapping_entry_with_a_status() -> None:
    assert set(MAPPING) == set(FIELD_BY_KEY)
    for key, entry in MAPPING.items():
        assert entry["status"] in ("mapped", "no_equivalent", "unresolved"), key
        if entry["status"] != "mapped":
            assert entry["reason"], key
            continue
        assert entry["sources"], key
        for source in entry["sources"]:
            assert re.fullmatch(r"\d{4}\.\d{4}", source["uid"]), key
            assert source["path"].startswith("MESSAGE/DOCUMENT_SETS/"), key
        assert isinstance(entry["captions"], tuple), key


def test_mapped_rules_exist_and_enumerations_carry_display_text() -> None:
    from basis.extraction.mismo36 import RULES

    for key, entry in MAPPING.items():
        if entry["status"] == "mapped":
            assert entry["rule"] in RULES, key
    estate = MAPPING["subject.property_rights_appraised"]["sources"][0]["enums"]
    assert estate["FeeSimple"] == "Fee Simple"
    assert all(
        "\n" not in text
        for e in MAPPING.values()
        for s in e.get("sources", ())
        for text in s["enums"].values()
    )


# --------------------------------------------------------------------------- subject vs comparable


def test_the_subject_is_read_never_the_comparable() -> None:
    values = read(fabricated_xml())
    assert values["subject.property_address"] == "100 Test Ln"
    assert values["improvements.gla_sqft"] == "2,000"
    assert values["subject.property_rights_appraised"] == "Fee Simple"
    assert "999 Comp Rd" not in values.values()
    assert values["listing_and_contract.contract_price"] == "$250,000"


def test_a_subject_without_its_use_type_is_found_when_it_is_the_only_one() -> None:
    xml = fabricated_xml(with_subject_marker=False)
    assert read(xml)["subject.property_address"] == "100 Test Ln"


def test_no_subject_at_all_is_refused() -> None:
    xml = fabricated_xml().replace(
        b'ValuationUseType="SubjectProperty"', b'ValuationUseType="SalesComparable"'
    )
    with pytest.raises(InvalidMismo, match="no subject"):
        Mismo36Document(parse_mismo(xml))


# --------------------------------------------------------------------------- values


def test_enumerations_read_as_the_report_prints_them_and_other_as_its_description() -> None:
    assert read(fabricated_xml())["improvements.heating_type"] == "Forced Warm Air"
    assert read(fabricated_xml(heating="Other"))["improvements.heating_type"] == "Geothermal loop"


def test_dates_money_names_and_parties_by_role() -> None:
    values = read(fabricated_xml())
    assert values["reconciliation.effective_date_of_appraisal"] == "01/15/2026"
    assert values["reconciliation.final_market_value"] == "$300,000"
    assert values["appraiser.name"] == "Alex Example"
    assert values["subject.borrower"] == "Pat Placeholder"
    assert values["improvements.bathrooms"] == "2.1"
    assert values["improvements.basement_area_sqft"] == "800"
    assert values["improvements.basement_finish_percent"] == "75"


def _below_grade(xml: bytes, *, level: bool, standard: int | None, other: int = 0) -> bytes:
    """The fabricated unit with its below-grade level kept or turned above grade, and the
    Unit Interior's below-grade areas (finished, nonstandard finished, unfinished)."""
    text = xml.decode()
    if not level:
        text = text.replace(
            "<GradeLevelType>PartiallyBelowGrade</GradeLevelType>",
            "<GradeLevelType>AboveGrade</GradeLevelType>",
        )
    if standard is not None:
        areas = (
            f"<UnitBelowGradeUnfinishedAreaMeasure>{other}</UnitBelowGradeUnfinishedAreaMeasure>"
            f"<UnitNonStandardBelowGradeFinishedAreaMeasure>{other}"
            "</UnitNonStandardBelowGradeFinishedAreaMeasure>"
            f"<UnitStandardBelowGradeFinishedAreaMeasure>{standard}"
            "</UnitStandardBelowGradeFinishedAreaMeasure>"
        )
        text = text.replace("<PROPERTY_UNIT_AREA>", "<PROPERTY_UNIT_AREA>" + areas)
    return text.encode()


def test_no_below_grade_level_reads_the_unit_interiors_zero() -> None:
    """No basement: the unit has no below-grade level, and the Unit Interior prints
    "Finished Below Grade 0 Sq. Ft." (A-1 deliver even if zero): the Basement Area is 0, the
    legacy 1004's 0 for no basement, not empty."""
    key = "improvements.basement_area_sqft"
    xml = _below_grade(fabricated_xml(), level=False, standard=0)
    assert read(xml)[key] == "0"
    result = extract_mismo(xml, "doc", layout=None)
    assert result.fields[key].value_num == Decimal(0)
    # the percent of nothing stays empty (no box prints it)
    assert "improvements.basement_finish_percent" not in read(xml)
    # no level and no Unit Interior areas at all: nothing to read
    assert key not in read(_below_grade(fabricated_xml(), level=False, standard=None))


def test_below_grade_levels_win_over_the_unit_interior() -> None:
    key = "improvements.basement_area_sqft"
    # G-1's source, the below-grade levels (600 + 200), when the unit has one
    assert read(_below_grade(fabricated_xml(), level=True, standard=0))[key] == "800"
    # without one, the Unit Interior's finished, nonstandard and unfinished areas add up
    xml = _below_grade(fabricated_xml(), level=False, standard=500, other=50)
    assert read(xml)[key] == "600"


def test_an_approach_not_developed_leaves_its_keys_empty() -> None:
    values = read(fabricated_xml(cost_developed=False))
    assert "cost_approach.site_value" not in values
    assert "cost_approach.indicated_value" not in values
    developed = read(fabricated_xml(cost_developed=True))
    assert developed["cost_approach.site_value"] == "$55,000"
    assert developed["reconciliation.indicated_value_cost_approach"] == "$310,000"


def test_no_concessions_reads_as_zero_dollars() -> None:
    result = extract_mismo(fabricated_xml(), "doc", layout=None)
    concessions = result.fields["listing_and_contract.seller_concessions"]
    assert concessions.value_text == "No" and concessions.value_num == Decimal(0)
    assert concessions.source == "mismo_xml" and concessions.confidence == 1.0


def test_multi_unit_property_per_unit_keys_and_building_area() -> None:
    xml = fabricated_xml(
        units=[unit(900, 2, "Tenant", label="A"), unit(1100, 3, "OwnerOccupied", label="B")]
    )
    values = read(xml)
    assert values["improvements.units"] == "2"
    assert values["improvements.gla_sqft"] == "2,000"  # the sum of the living units
    assert "improvements.bedrooms" not in values and "subject.occupant" not in values


def test_an_accessory_unit_is_not_a_living_unit() -> None:
    xml = fabricated_xml(
        units=[unit(1500, 3, "OwnerOccupied"), unit(400, 1, "Tenant", adu=True, label="ADU")]
    )
    values = read(xml)
    assert values["improvements.units"] == "One with Accessory Unit"
    assert values["improvements.gla_sqft"] == "1,500"
    assert values["improvements.bedrooms"] == "3"
    assert values["subject.occupant"] == "Owner"


def test_units_come_from_the_property_counts_when_the_delivery_has_them() -> None:
    # the counts (A-1 0100.0022 / 0100.0019) win over the PROPERTY_UNIT containers
    assert read(fabricated_xml(unit_counts=(2, 0)))["improvements.units"] == "2"
    assert read(fabricated_xml(unit_counts=(1, 1)))["improvements.units"] == (
        "One with Accessory Unit"
    )
    adu = [unit(1500, 3, "OwnerOccupied"), unit(400, 1, "Tenant", adu=True, label="ADU")]
    assert read(fabricated_xml(units=adu, unit_counts=(1, 0)))["improvements.units"] == "One"
    # no ADU count: the accessory PROPERTY_UNITs say
    assert read(fabricated_xml(units=adu, unit_counts=(1, None)))["improvements.units"] == (
        "One with Accessory Unit"
    )
    assert read(fabricated_xml(unit_counts=(1, None)))["improvements.units"] == "One"
    # a count that is no count: nothing (not the structure's guess)
    assert "improvements.units" not in read(fabricated_xml(unit_counts=(0, 0)))


@pytest.mark.parametrize(
    ("count", "adus", "text"),
    [
        (1, 0, "One"),
        (1, 2, "One with Accessory Unit"),
        (2, 0, "2"),
        (4, 1, "4"),
        (1, None, None),
        (0, 0, None),
        (3, None, "3"),
    ],
)
def test_units_value(count: int, adus: int | None, text: str | None) -> None:
    assert units_value(count, adus) == text


# --------------------------------------------------------------------------- hostile XML


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b'<MESSAGE xmlns="http://www.mismo.org/residential/2009/schemas">&b;</MESSAGE>',
        b'<?xml version="1.0"?><!DOCTYPE m SYSTEM "http://example.invalid/x.dtd">'
        b'<MESSAGE xmlns="http://www.mismo.org/residential/2009/schemas"/>',
        b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<MESSAGE xmlns="http://www.mismo.org/residential/2009/schemas">&x;</MESSAGE>',
    ],
)
def test_doctype_and_entities_are_refused(xml: bytes) -> None:
    with pytest.raises(InvalidMismo, match="DOCTYPE|entit"):
        parse_mismo(xml)


def test_malformed_wrong_root_and_oversized_xml_are_refused() -> None:
    with pytest.raises(InvalidMismo, match="well-formed"):
        parse_mismo(b"<MESSAGE><unclosed></MESSAGE>")
    with pytest.raises(InvalidMismo, match="not a MISMO MESSAGE"):
        parse_mismo(b"<html/>")
    with pytest.raises(InvalidMismo, match="larger"):
        parse_mismo(b" " * (MAX_XML_BYTES + 1))


# --------------------------------------------------------------------------- the PDF


def test_values_are_located_next_to_their_caption_and_a_missing_one_is_flagged() -> None:
    azure = azure_printing(
        [
            ["Physical", "Address", "100", "Test", "Ln"],
            ["Opinion", "of", "Market", "Value", "$300,000"],
            ["Effective", "Date", "of", "Appraisal", "01/15/2026"],
            ["Indicated", "Value", "$300,000"],
            ["Contract", "Price", "$999,999"],  # the PDF disagrees with the XML
        ]
    )
    result = extract_mismo(fabricated_xml(), "doc", layout=layout_of(azure), page_count=1)
    value = result.fields["reconciliation.final_market_value"]
    assert value.page == 1 and len(value.regions) == 1
    x0, y0, _, _ = value.regions[0].bbox
    assert y0 == pytest.approx(0.09) and x0 == pytest.approx(0.53)  # line 2, not line 4
    flagged = {i.keys[0] for i in result.issues if i.rule == MISMATCH_RULE}
    assert "listing_and_contract.contract_price" in flagged
    assert "reconciliation.final_market_value" not in flagged
    assert all(i.severity == "warning" for i in result.issues if i.rule == MISMATCH_RULE)
    assert result.method == "mismo_xml+azure_di"
    assert result.fields["listing_and_contract.contract_price"].regions == ()


def test_without_a_layout_there_are_no_regions_and_no_mismatch() -> None:
    result = extract_mismo(fabricated_xml(), "doc", layout=None)
    assert all(not f.regions for f in result.fields.values())
    assert not [i for i in result.issues if i.rule == MISMATCH_RULE]
    assert result.method == "mismo_xml"


def test_printed_variants_of_a_value() -> None:
    assert "489,000" in variants("$489,000") and "489000" in variants("$489,000")
    assert "9/5/2019" in variants("09/05/2019")
