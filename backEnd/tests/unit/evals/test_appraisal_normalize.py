"""Appraisal eval v2 normalizers (``evals/appraisal/normalize.py``, SPEC §4 and §8).

Fabricated values only. Each type's parser, the enum synonym table (adding a synonym must
change a test here), the comparison rules (acre rounding, tolerance, ``other`` write-ins),
the display mirror of ``web/live/live-review.js`` ``fieldValue`` and the canonical
display strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from evals.appraisal.normalize import (
    ENUMS,
    UI_LOW_CONFIDENCE,
    Area,
    Baths,
    Columns,
    EnumValue,
    MultiValue,
    anls,
    canonical_display,
    first_number,
    fold_text,
    levenshtein,
    marked_options,
    matches,
    normalize,
    normalize_truth,
    parse_area_sqft,
    parse_baths,
    parse_bool,
    parse_currency,
    parse_date,
    parse_enum,
    parse_enum_multi,
    parse_month,
    parse_percent,
    prediction_value,
    review_display,
)

REPO = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("=$ 572,831", Decimal(572831)),
        ("156,240 =$( 156,240)", Decimal(-156240)),  # outside the cost approach: negative
        ("$85,000", Decimal(85000)),
        ("$ 85,000.50", Decimal("85000.50")),
        ("(5,000)", Decimal(-5000)),
        ("$(5,000)", Decimal(-5000)),
        ("-$5,000", Decimal(-5000)),
        ("0", Decimal(0)),
        ("$0", Decimal(0)),  # a printed $0 is a value (SPEC principle 2)
        ("85,000 (rounded)", Decimal(85000)),
        # "No"/"None" in a currency box is 0 (UAD 3.6 "Known Sales Concessions No");
        # only n/a and an empty box are no value (SPEC principle 2, bb44501).
        ("No", Decimal(0)),
        ("no", Decimal(0)),
        ("None", Decimal(0)),
        ("$ None", Decimal(0)),
        ("n/a", None),
        ("N/A", None),
        ("na", None),
        ("", None),
        ("   ", None),
        ("-", None),
        ("see addendum", None),
        ("Not known", None),  # "no" only as a whole word
    ],
)
def test_currency(printed: str, expected: Decimal | None) -> None:
    assert parse_currency(printed) == expected


@pytest.mark.parametrize(
    ("printed", "key", "expected"),
    [
        # In the cost approach, parentheses after "=$" or "$" are the form's subtraction
        # marks: the depreciation line's amount is positive (SPEC section 4).
        ("156,240 =$( 156,240)", "cost_approach.depreciation", Decimal(156240)),
        ("=$( 156,240)", "cost_approach.depreciation_physical", Decimal(156240)),
        ("$(5,000)", "cost_approach.depreciation_external", Decimal(5000)),
        ("=$ 572,831", "cost_approach.indicated_value", Decimal(572831)),
        # Not right after a dollar sign: still negative, even in the cost approach.
        ("(5,000)", "cost_approach.depreciation_physical", Decimal(-5000)),
        ("-$5,000", "cost_approach.depreciation_physical", Decimal(-5000)),
        # Outside the cost approach "$(x)" is negative.
        ("$(5,000)", "listing_and_contract.seller_concessions", Decimal(-5000)),
        ("=$( 156,240)", None, Decimal(-156240)),
    ],
)
def test_cost_approach_parentheses_are_subtraction_marks(
    printed: str, key: str | None, expected: Decimal
) -> None:
    assert parse_currency(printed, key=key) == expected
    assert normalize("currency", printed, key=key) == expected


def test_zero_dollars_is_a_value_that_can_be_correct() -> None:
    truth = normalize_truth("currency", 0)
    for printed in ("$0", "0", "=$ 0", "$ 0.00"):
        assert matches("currency", normalize("currency", printed), truth), printed
    assert Columns("$0", Decimal(0), None).has_value
    assert prediction_value("currency", Columns("$0", Decimal(0), None)) == Decimal(0)
    assert normalize("currency", "n/a") is None


def test_currency_keeps_a_typed_number() -> None:
    assert parse_currency(Decimal("85000.00")) == Decimal(85000)
    assert parse_currency(85000) == Decimal(85000)
    assert parse_currency(True) is None


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("1,740 Sq.Ft.", Decimal(1740)),
        ("1,740", Decimal(1740)),
        ("2.5", Decimal("2.5")),
        ("-3", Decimal(-3)),
        ("0", Decimal(0)),
        ("n/a", None),
        ("", None),
        ("unknown", None),
    ],
)
def test_first_number(printed: str, expected: Decimal | None) -> None:
    assert first_number(printed) == expected


def test_int_is_exact() -> None:
    truth = normalize_truth("int", 1740)
    assert matches("int", normalize("int", "1,740 Sq.Ft."), truth)
    assert matches("int", normalize("int", Decimal("1740.0")), truth)
    assert not matches("int", normalize("int", "1,741"), truth)


def test_tolerance_is_absolute_and_opt_in() -> None:
    truth = normalize_truth("currency", 85000)
    assert not matches("currency", Decimal(85001), truth)
    assert matches("currency", Decimal(85001), truth, tolerance=1)
    assert not matches("currency", Decimal(85002), truth, tolerance=1)


def test_percent() -> None:
    assert parse_percent("80%") == Decimal(80)
    assert parse_percent("80 %") == Decimal(80)
    assert parse_percent("0%") == Decimal(0)
    assert parse_percent("") is None


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("1.39 ac", Decimal(60548)),  # 60,548.4 rounded
        ("1.39 acres", Decimal(60548)),
        ("0.25 acre", Decimal(10890)),
        ("1,740 Sq.Ft.", Decimal(1740)),
        ("7,500 sf", Decimal(7500)),
        ("7,500 sq ft", Decimal(7500)),
        ("7500", Decimal(7500)),  # a bare number is square feet
        ("n/a", None),
    ],
)
def test_area(printed: str, expected: Decimal | None) -> None:
    assert parse_area_sqft(printed) == expected


def test_area_printed_in_acres_is_correct_within_the_printed_rounding() -> None:
    truth = normalize_truth("area_sqft", {"sqft": 60548, "printed_unit": "ac"})
    assert truth == Area(Decimal(60548), "ac")
    # 1.385 and 1.395 ac are the edges of "1.39"; 0.005 ac = 217.8 sq ft.
    assert matches("area_sqft", Area(Decimal(60548 + 217)), truth)
    assert matches("area_sqft", Area(Decimal(60548 - 217)), truth)
    assert not matches("area_sqft", Area(Decimal(60548 + 218)), truth)
    plain = normalize_truth("area_sqft", 1740)
    assert matches("area_sqft", normalize("area_sqft", "1,740 Sq.Ft."), plain)
    assert not matches("area_sqft", normalize("area_sqft", "1,741"), plain)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("2.1", Baths(2, 1)),
        ("2.5", Baths(2, 1)),  # read as 2 full, 1 half
        ("2.0", Baths(2, 0)),
        ("3", Baths(3, 0)),
        ("2.2", Baths(2, 2)),
        ("2 full 1 half", Baths(2, 1)),
        ({"full": 2, "half": 1}, Baths(2, 1)),
        ("n/a", None),
        ("", None),
    ],
)
def test_baths(printed: object, expected: Baths | None) -> None:
    assert parse_baths(printed) == expected


def test_baths_from_the_stored_number() -> None:
    columns = Columns(value_text=None, value_num=Decimal("2.1"), value_date=None)
    assert prediction_value("baths", columns) == Baths(2, 1)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("01/2025", "2025-01"),
        ("1/2025", "2025-01"),
        ("1/25", "2025-01"),
        ("2025-01", "2025-01"),
        ("13/2025", None),
        ("January", None),
        ("", None),
    ],
)
def test_month(printed: str, expected: str | None) -> None:
    assert parse_month(printed, today=date(2026, 10, 26)) == expected


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("1/5/2025", "2025-01-05"),
        ("01/05/2025", "2025-01-05"),
        ("01/05/25", "2025-01-05"),
        ("12/01/27", "2027-12-01"),  # within a year ahead of today: this century
        ("12/01/28", "1928-12-01"),  # more than a year ahead: the last century
        ("2025-01-05", "2025-01-05"),
        ("January 5, 2025", "2025-01-05"),
        ("Jan. 5, 2025", "2025-01-05"),
        ("Effective 01/05/2025", "2025-01-05"),
        ("2/30/2025", None),
        ("n/a", None),
    ],
)
def test_date(printed: str, expected: str | None) -> None:
    assert parse_date(printed, today=date(2026, 10, 26)) == expected


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Yes", True),
        ("X", True),
        ("checked", True),
        (":selected:", True),
        ("No", False),
        ("unchecked", False),
        (":unselected:", False),
        ("X Yes", True),  # the box's mark is not a "yes"
        ("X No", False),
        ("No Hazard Zone Noted", False),  # by words, as clean_checkbox reads it
        ("Yes and no", None),  # a word for each side: unclear
        ("USGS Lava Flow Zone", None),
        (Decimal(1), True),
        (Decimal(0), False),
        ("maybe", None),
        (Decimal(2), None),
    ],
)
def test_bool(printed: object, expected: bool | None) -> None:
    assert parse_bool(printed) == expected


def test_no_in_a_currency_box_scores_against_a_zero_truth() -> None:
    truth = normalize_truth("currency", 0)
    # Production stores "No" as text with no number: the text is read, and it is 0.
    assert prediction_value("currency", Columns("No", None, None)) == Decimal(0)
    assert matches("currency", prediction_value("currency", Columns("No", None, None)), truth)
    assert prediction_value("currency", Columns("N/A", None, None)) is None
    # The zero words are a currency rule only: other numeric types read no number.
    assert normalize("int", "None") is None


@pytest.mark.parametrize(
    ("prediction", "truth", "expected"),
    [
        # A truth joined with "; " is a set of parts: order and join do not matter.
        ("Jane Fab; John Fab", "John Fab; Jane Fab", True),
        ("John Fab and Jane Fab", "John Fab; Jane Fab", True),
        ("John Fab & Jane Fab", "John Fab; Jane Fab", True),
        ("John Fab, Jane Fab", "John Fab; Jane Fab", True),
        ("JOHN FAB;JANE FAB.", "John Fab; Jane Fab", True),
        # The GSE builder also joins with " | ".
        ("Crawl Space; Pier", "Crawl Space | Pier", True),
        ("Pier | Crawl Space", "Crawl Space | Pier", True),
        ("123-45-678 / 123-45-679", "123-45-678; 123-45-679", True),
        # Every part must be there, and nothing else.
        ("John Fab", "John Fab; Jane Fab", False),
        ("John Fab; Jane Fab; Jim Fab", "John Fab; Jane Fab", False),
        # Names with commas: the prediction's ";" decides its split.
        ("Fab, John; Fab, Jane", "Fab, Jane; Fab, John", True),
        ("Fab, John and Fab, Jane", "Fab, John; Fab, Jane", False),  # conservative
        # Without ";" in the truth the comparison stays plain folded equality.
        ("Tile, Carpet", "Carpet, Tile", False),
        ("Carpet, Tile.", "carpet, tile", True),
        # A list read without its separators: same words, same order.
        ("Refrigerator Range/Oven Dishwasher", "Refrigerator, Range/Oven, Dishwasher", True),
        ("Range/Oven Refrigerator Dishwasher", "Refrigerator, Range/Oven, Dishwasher", False),
        ("Refrigerator Range/Oven", "Refrigerator, Range/Oven, Dishwasher", False),
    ],
)
def test_text_with_several_values(prediction: str, truth: str, expected: bool) -> None:
    assert (
        matches("text", normalize("text", prediction), normalize_truth("text", truth)) is expected
    )


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("X Suburban", ["Suburban"]),
        ("Asphalt; X Public", ["Public"]),  # description beside the box is not an option
        ("X Public; Propane gas", ["Public"]),
        ("X Detached; X Garage; X Att.", ["Detached", "Garage", "Att."]),
        ("X Refrigerator X Range/Oven", ["Refrigerator", "Range/Oven"]),
        ("[x] As is", ["As is"]),
        ("\u2612 Owner", ["Owner"]),
        ("Suburban", None),  # nothing marked: read as is
        ("2 x 4 studs", ["4 studs"]),  # an x between words reads as a mark (enums only)
    ],
)
def test_marked_options(printed: str, expected: list[str] | None) -> None:
    assert marked_options(printed) == expected


def test_checkbox_marks_select_the_options() -> None:
    assert parse_enum("X Suburban", "neighborhood.location") == EnumValue("suburban")
    assert parse_enum("X Over 75%", "neighborhood.built_up") == EnumValue("over_75")
    assert parse_enum('X "as is"', "reconciliation.appraisal_made") == EnumValue("as_is")
    assert parse_enum("Asphalt; X Public", "site.off_site_improvements_street") == EnumValue(
        "public"
    )
    assert parse_enum("X Public; Propane gas", "site.utilities_gas") == EnumValue("public")
    # Two marks in a one-choice box name no single code: unmatched.
    assert parse_enum("X Urban X Rural", "neighborhood.location") == EnumValue(None)
    storage = "improvements.car_storage"
    assert parse_enum_multi("X Detached; X Garage; X Att.", storage) == MultiValue(
        ("attached", "detached", "garage")
    )
    assert parse_enum_multi("X FWA; X Other Infloor", "improvements.heating_type") == MultiValue(
        ("fwa", "other"), "infloor"
    )


def test_uad_synonyms() -> None:
    street = "site.off_site_improvements_street"
    assert parse_enum("Public Street", street) == EnumValue("public")
    assert parse_enum("Private Street", street) == EnumValue("private")
    assert parse_enum("Subject to Repair", "reconciliation.appraisal_made") == EnumValue(
        "subject_to_repairs"
    )


def test_typographic_quotes_fold() -> None:
    assert fold_text("The subject\u2019s market area") == "the subject's market area"
    assert fold_text("\u201cAs is\u201d") == '"as is"'


def test_text_folding_and_anls() -> None:
    assert fold_text("  Lot 7,  Block\tB. ") == "lot 7, block\tb".replace("\t", " ")
    assert fold_text("ＡＢＣ") == "abc"  # NFKC
    assert matches(
        "text",
        normalize("text", "Smith Appraisal Co."),
        normalize_truth("text", "smith appraisal co"),
    )
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert anls("Jon Smith", "John Smith") == pytest.approx(0.9)
    assert anls("abc", "xyz") == 0.0  # below the 0.5 threshold
    assert anls(None, "abc") == 0.0
    assert anls("Same", "same.") == 1.0


@pytest.mark.parametrize(
    ("key", "printed", "code"),
    [
        ("neighborhood.location", "Suburban", "suburban"),
        ("neighborhood.built_up", "Over 75%", "over_75"),
        ("neighborhood.built_up", "25-75%", "25_75"),
        ("neighborhood.built_up", "Under 25%", "under_25"),
        ("neighborhood.marketing_time", "Under 3 mths", "under_3_mths"),
        ("neighborhood.marketing_time", "3-6 mths", "3_6_mths"),
        ("neighborhood.demand_supply", "In Balance", "in_balance"),
        ("neighborhood.demand_supply", "Over Supply", "over_supply"),
        ("subject.occupant", "Owner", "owner"),
        ("subject.property_rights_appraised", "Fee Simple", "fee_simple"),
        ("subject.assignment_type", "Purchase Transaction", "purchase"),
        ("site.zoning_compliance", "Legal Nonconforming", "legal_nonconforming"),
        (
            "site.zoning_compliance",
            "Legal Nonconforming (Grandfathered Use)",
            "legal_nonconforming",
        ),
        ("site.zoning_compliance", "Legal", "legal"),
        ("improvements.units", "One", "one"),
        ("improvements.type", "Det.", "det"),
        ("improvements.type", "Detached", "det"),
        ("improvements.type", "S-Det/End Unit", "s_det_end_unit"),
        ("improvements.heating_type", "FWA", "fwa"),
        ("improvements.heating_type", "Forced Warm Air", "fwa"),
        ("improvements.heating_type", "HWBB", "hwbb"),
        ("reconciliation.appraisal_made", "As is", "as_is"),
        ("reconciliation.appraisal_made", "AS IS", "as_is"),
        ("site.utilities_water", "Public", "public"),
        ("site.off_site_improvements_street", "Private", "private"),
    ],
)
def test_enum_synonyms(key: str, printed: str, code: str) -> None:
    assert parse_enum(printed, key) == EnumValue(code)
    assert matches("enum", parse_enum(printed, key), normalize_truth("enum", code, key=key))


def test_enum_unmatched_and_other() -> None:
    # No "other" code: an unknown or ambiguous print is unmatched (a wrong value).
    unmatched = parse_enum("Urban Suburban", "neighborhood.location")
    assert unmatched == EnumValue(None)
    assert not matches("enum", unmatched, normalize_truth("enum", "urban"))
    # With an "other" code: the write-in is the text, compared as text.
    truth = normalize_truth("enum", {"code": "other", "text": "Infloor"})
    assert matches("enum", parse_enum("Infloor", "improvements.heating_type"), truth)
    assert matches("enum", parse_enum("Other: Infloor", "improvements.heating_type"), truth)
    assert not matches("enum", parse_enum("Baseboard", "improvements.heating_type"), truth)
    assert not matches("enum", parse_enum("FWA", "improvements.heating_type"), truth)
    assert parse_enum("n/a", "improvements.heating_type") is None
    # A key without a vocabulary (an extra key) compares folded text with the code.
    assert matches("enum", parse_enum("Built-In", "improvements.pool_type"), EnumValue("builtin"))
    # The extra garage key shares car storage's attachment codes.
    assert parse_enum("X Att.", "improvements.garage_type") == EnumValue("attached")


def test_enum_multi() -> None:
    key = "improvements.foundation_type"
    truth = normalize_truth("enum_multi", ["crawl_space", "concrete_slab"], key=key)
    assert truth == MultiValue(("concrete_slab", "crawl_space"))
    assert matches("enum_multi", parse_enum_multi("Concrete Slab, Crawl Space", key), truth)
    assert matches("enum_multi", parse_enum_multi("Crawl Space & Concrete Slab", key), truth)
    assert not matches("enum_multi", parse_enum_multi("Concrete Slab", key), truth)
    storage = "improvements.car_storage"
    assert parse_enum_multi("Garage Att.", storage) == MultiValue(("attached", "garage"))
    assert parse_enum_multi("Driveway, Garage Det.", storage) == MultiValue(
        ("detached", "driveway", "garage")
    )
    # An uncovered word in a vocabulary without "other": unmatched, never a match.
    blue = parse_enum_multi("Garage Blue", storage)
    assert blue is not None and "?" in blue.codes
    assert not matches("enum_multi", blue, normalize_truth("enum_multi", ["garage"], key=storage))
    assert parse_enum_multi("", storage) is None
    # "None" is a car-storage code, not "no value".
    assert parse_enum_multi("None", storage) == MultiValue(("none",))


def test_enum_multi_truth_with_two_write_ins_keeps_both() -> None:
    heating = "improvements.heating_type"
    truth = normalize_truth(
        "enum_multi",
        [{"code": "other", "text": "Baseboard"}, {"code": "other", "text": "Infloor"}],
        key=heating,
    )
    assert truth == MultiValue(("other",), "baseboard infloor")
    assert matches("enum_multi", parse_enum_multi("Baseboard, Infloor", heating), truth)
    assert not matches("enum_multi", parse_enum_multi("Infloor", heating), truth)


def test_parking_garage_is_the_garage_code() -> None:
    """UAD 3.6 prints a condo or co-op unit's space as "Parking Garage" (A-1 3200.0006
    ParkingGarage); the legacy box it stands for is Garage (the 1073's Car Storage Garage)."""
    storage = "improvements.car_storage"
    garage = normalize_truth("enum_multi", ["garage"], key=storage)
    for printed in ("Parking Garage", "PARKING GARAGE", "parking-garage"):
        assert parse_enum_multi(printed, storage) == MultiValue(("garage",)), printed
        assert matches("enum_multi", parse_enum_multi(printed, storage), garage), printed
    assert parse_enum_multi("Driveway; Parking Garage", storage) == MultiValue(
        ("driveway", "garage")
    )
    # Only the two words together: "Parking" alone names no code.
    parking = parse_enum_multi("Parking", storage)
    assert parking is not None and "?" in parking.codes
    # The extra attachment key does not take it: a parking garage has no attachment code.
    assert parse_enum("Parking Garage", "improvements.garage_type") == EnumValue(None)


HEATING = "improvements.heating_type"


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("FWA", MultiValue(("fwa",))),
        ("Forced Warm Air, HWBB", MultiValue(("fwa", "hwbb"))),
        ("FWA Other: Infloor", MultiValue(("fwa", "other"), "infloor")),
        ("FWA, Infloor", MultiValue(("fwa", "other"), "infloor")),
        ("Radiant & Other Infloor", MultiValue(("other", "radiant"), "infloor")),
        ("Infloor", MultiValue(("other",), "infloor")),
        ("Other", MultiValue(("other",))),
        ("n/a", None),
    ],
)
def test_heating_is_multi_with_an_other_write_in(printed: str, expected: MultiValue | None) -> None:
    assert parse_enum_multi(printed, HEATING) == expected


def test_heating_other_text_is_compared_when_the_truth_has_it() -> None:
    with_text = normalize_truth(
        "enum_multi", ["fwa", {"code": "other", "text": "Infloor"}], key=HEATING
    )
    assert with_text == MultiValue(("fwa", "other"), "infloor")
    assert matches("enum_multi", parse_enum_multi("FWA Other: Infloor", HEATING), with_text)
    assert matches("enum_multi", parse_enum_multi("Infloor, FWA", HEATING), with_text)
    assert not matches("enum_multi", parse_enum_multi("FWA", HEATING), with_text)  # code set
    assert not matches("enum_multi", parse_enum_multi("FWA, Baseboard", HEATING), with_text)
    # A truth "other" without text: only the code set is compared.
    no_text = normalize_truth("enum_multi", ["fwa", {"code": "other"}], key=HEATING)
    assert matches("enum_multi", parse_enum_multi("FWA, Baseboard", HEATING), no_text)
    assert matches("enum_multi", parse_enum_multi("FWA, Other", HEATING), no_text)
    assert not matches("enum_multi", parse_enum_multi("HWBB, Other", HEATING), no_text)


def test_every_vocabulary_label_and_code_maps_to_itself() -> None:
    for key, codes in ENUMS.items():
        for item in codes:
            for form in (item.code, item.label, *item.synonyms):
                assert parse_enum(form, key) == EnumValue(item.code), (key, form)
                assert parse_enum_multi(form, key) == MultiValue((item.code,)), (key, form)


def test_prediction_reads_the_typed_column_first() -> None:
    # The stored number wins over the text (what extraction_fields stores and cost seg uses).
    columns = Columns(value_text="Site value =$ 85,000", value_num=Decimal(85000), value_date=None)
    assert prediction_value("currency", columns) == Decimal(85000)
    # No number stored: the text is parsed.
    assert prediction_value("currency", Columns("=$ 85,000", None, None)) == Decimal(85000)
    assert prediction_value("date", Columns("01/05/2025", None, date(2025, 1, 5))) == "2025-01-05"
    assert prediction_value("month", Columns("2025-01", None, None)) == "2025-01"
    assert prediction_value("area_sqft", Columns("1.39 ac", Decimal(60548), None)) == Area(
        Decimal(60548)
    )
    assert prediction_value("area_sqft", Columns("1.39 ac", None, None)) == Area(Decimal(60548))
    assert prediction_value("bool", Columns("Yes", Decimal(1), None)) is True
    assert prediction_value("text", Columns(None, Decimal(5), None)) == "5"


def test_has_value() -> None:
    assert not Columns(None, None, None).has_value
    assert not Columns("  ", None, None).has_value
    assert Columns("0", None, None).has_value
    assert Columns(None, Decimal(0), None).has_value


def test_review_display_mirrors_live_review_js() -> None:
    source = (REPO / "web/live/live-review.js").read_text()
    # The mirrored rule; if this line changes, review_display must change with it.
    assert (
        "const fieldValue=f=>f.value_text??(f.value_num!==null&&f.value_num!==undefined?"
        "Number(f.value_num).toLocaleString('en-US'):null)??f.value_date??'—';"
    ) in source
    assert f"f.confidence<{UI_LOW_CONFIDENCE}" in source
    assert review_display(Columns("$85,000", Decimal(85000), None)) == "$85,000"
    assert review_display(Columns("", Decimal(85000), None)) == ""  # ?? keeps ""
    assert review_display(Columns(None, Decimal("85000.00"), None)) == "85,000"
    assert review_display(Columns(None, Decimal("1234.5678"), None)) == "1,234.568"
    assert review_display(Columns(None, Decimal("0.0005"), None)) == "0.001"  # half away
    assert review_display(Columns(None, Decimal(-1500), None)) == "-1,500"
    assert review_display(Columns(None, None, date(2025, 1, 5))) == "2025-01-05"
    assert review_display(Columns(None, None, None)) == "—"


@pytest.mark.parametrize(
    ("kind", "value", "key", "printed", "shown"),
    [
        ("currency", 85000, None, None, "$85,000"),
        ("currency", 85000.5, None, None, "$85,000.50"),
        ("currency", -5000, None, None, "-$5,000"),
        ("int", 1740, None, None, "1,740"),
        ("float", 1.39, None, None, "1.39"),
        ("area_sqft", 1740, None, None, "1,740 sq ft"),
        (
            "area_sqft",
            {"sqft": 60548, "printed_unit": "ac"},
            None,
            "1.39 ac",
            "60,548 sq ft (1.39 ac)",
        ),
        ("area_sqft", {"sqft": 60548, "printed_unit": "ac"}, None, None, "60,548 sq ft (1.39 ac)"),
        ("percent", 80, None, None, "80%"),
        ("date", "2025-01-05", None, None, "01/05/2025"),
        ("month", "2025-01", None, None, "01/2025"),
        ("bool", True, None, None, "Yes"),
        ("bool", False, None, None, "No"),
        ("enum", "suburban", "neighborhood.location", None, "Suburban"),
        (
            "enum",
            {"code": "other", "text": "Infloor"},
            "improvements.heating_type",
            None,
            "Infloor",
        ),
        (
            "enum_multi",
            ["crawl_space", "concrete_slab"],
            "improvements.foundation_type",
            None,
            "Concrete Slab, Crawl Space",
        ),
        (
            "enum_multi",
            ["fwa", {"code": "other", "text": "Infloor"}],
            "improvements.heating_type",
            None,
            "FWA, Infloor",
        ),
        ("baths", {"full": 2, "half": 1}, None, None, "2.1"),
        ("text", "Lot 7 Block B", None, None, "Lot 7 Block B"),
    ],
)
def test_canonical_display(
    kind: str, value: object, key: str | None, printed: str | None, shown: str
) -> None:
    assert canonical_display(kind, value, key=key, printed=printed) == shown


def test_unknown_type_is_refused() -> None:
    with pytest.raises(ValueError):
        normalize("colour", "red")


def test_appliance_checkboxes_compare_as_a_set_of_codes() -> None:
    key = "improvements.appliances"
    truth = normalize_truth("enum_multi", ["dishwasher", "range_oven", "refrigerator"], key=key)
    assert matches(
        "enum_multi", parse_enum_multi("Refrigerator, Range/Oven, Dishwasher", key), truth
    )
    assert matches(
        "enum_multi", parse_enum_multi("X Range/Oven X Dishwasher X Refrigerator", key), truth
    )
    assert not matches("enum_multi", parse_enum_multi("Refrigerator, Range/Oven", key), truth)
    assert not matches(
        "enum_multi",
        parse_enum_multi("Refrigerator, Range/Oven, Dishwasher, Microwave", key),
        truth,
    )
