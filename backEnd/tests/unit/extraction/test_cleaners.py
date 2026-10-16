"""Value cleaners, migrated from ``agentic/tests/test_azure_di_cleaner.py``.

Each legacy test is kept with its original name and intent. Where the migrated cleaner
deliberately changed behavior (see ``basis/extraction/cleaners.py``), the test states the
new expectation and the reason; the legacy section cleaners' tests are restated against
the per-field ``clean_value``/``clean_fields`` that replaced them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from basis.extraction.cleaners import (
    MATERIAL_CONDITION_FIELDS,
    CleanValue,
    clean_address,
    clean_checkbox,
    clean_comparable,
    clean_currency,
    clean_date,
    clean_field,
    clean_fields,
    clean_integer,
    clean_material,
    clean_month,
    clean_percent,
    clean_text,
    clean_value,
    extract_selection,
    parse_date,
    printed_in_acres,
    split_concatenated_field,
)


class TestCleanText:
    def test_removes_selected_marker(self) -> None:
        assert clean_text("Urban :selected:") == "Urban"
        assert clean_text(":selected: Yes") == "Yes"

    def test_removes_unselected_marker(self) -> None:
        assert clean_text("Rural :unselected:") == "Rural"

    def test_fixes_word_splits(self) -> None:
        assert clean_text("P urchase") == "Purchase"
        assert clean_text("Le gal") == "Legal"

    def test_collapses_multiple_spaces(self) -> None:
        assert clean_text("hello    world") == "hello world"

    def test_removes_trailing_single_chars(self) -> None:
        assert clean_text("Purchase e") == "Purchase"

    def test_handles_none(self) -> None:
        assert clean_text(None) == ""

    def test_handles_non_string(self) -> None:
        assert clean_text(123) == "123"

    # New: the legacy split repair glued real words together.
    @pytest.mark.parametrize(
        "text", ["Up to date", "As Is", "In Balance", "A frame house", "I beam", "No adverse"]
    )
    def test_never_joins_real_words(self, text: str) -> None:
        assert clean_text(text) == text


class TestCleanCurrency:
    def test_removes_leading_equals(self) -> None:
        assert clean_currency("=$692,831") == Decimal("692831")

    def test_removes_dollar_sign(self) -> None:
        assert clean_currency("$85,000") == Decimal("85000")

    def test_removes_commas(self) -> None:
        assert clean_currency("1,234,567") == Decimal("1234567")

    def test_handles_spaces(self) -> None:
        assert clean_currency("$ 85,000") == Decimal("85000")

    def test_handles_int_input(self) -> None:
        assert clean_currency(85000) == Decimal("85000")

    def test_handles_float_input(self) -> None:
        assert clean_currency(85000.50) == Decimal("85000.5")

    def test_handles_none(self) -> None:
        # Changed: None (missing) instead of 0.0, so "missing" never reads as "$0".
        assert clean_currency(None) is None

    def test_handles_malformed_string(self) -> None:
        # Changed: None instead of 0.0 for the same reason.
        assert clean_currency("not a number") is None

    def test_extracts_number_from_mixed_text(self) -> None:
        assert clean_currency("approximately 85000 dollars") == Decimal("85000")

    # New
    def test_negative_amounts(self) -> None:
        assert clean_currency("-$7,500") == Decimal("-7500")
        assert clean_currency("($5,000)") == Decimal("-5000")

    def test_placeholders_are_missing_not_zero(self) -> None:
        for text in ("-", "N/A", "", "  "):
            assert clean_currency(text) is None

    def test_zero_stays_zero(self) -> None:
        assert clean_currency("$0") == Decimal("0")

    def test_returns_decimal(self) -> None:
        assert isinstance(clean_currency("$1.10"), Decimal)


class TestCleanInteger:
    def test_converts_to_int(self) -> None:
        assert clean_integer("3,200") == 3200

    def test_truncates_decimal(self) -> None:
        assert clean_integer("3.7") == 3

    # New
    def test_ignores_units(self) -> None:
        assert clean_integer("1,850 sq ft") == 1850
        assert clean_integer("48 years") == 48

    def test_missing_is_none(self) -> None:
        assert clean_integer(None) is None


class TestCleanCheckbox:
    def test_selected_marker(self) -> None:
        assert clean_checkbox(":selected:") is True
        assert clean_checkbox("Yes :selected:") is True

    def test_unselected_marker(self) -> None:
        assert clean_checkbox(":unselected:") is False
        assert clean_checkbox("No :unselected:") is False

    def test_yes_no_text(self) -> None:
        assert clean_checkbox("yes") is True
        assert clean_checkbox("no") is False

    def test_bool_input(self) -> None:
        assert clean_checkbox(True) is True
        assert clean_checkbox(False) is False

    def test_unclear_returns_none(self) -> None:
        assert clean_checkbox("maybe") is None
        assert clean_checkbox(None) is None

    # New: the legacy substring test read these as yes/no.
    @pytest.mark.parametrize("text", ["Unknown", "None", "eyes", "Nothing noted"])
    def test_words_containing_yes_or_no_are_unclear(self, text: str) -> None:
        assert clean_checkbox(text) is None


class TestExtractSelection:
    def test_extracts_selected_option(self) -> None:
        options = ["Urban", "Suburban", "Rural"]
        assert extract_selection("Urban :selected: Suburban :unselected:", options) == "Urban"

    def test_falls_back_to_unmarked(self) -> None:
        options = ["Rapid", "Stable", "Slow"]
        assert extract_selection("Stable", options) == "Stable"

    def test_returns_empty_if_no_match(self) -> None:
        options = ["Urban", "Suburban"]
        assert extract_selection("Rural", options) == ""


class TestCleanAddress:
    def test_cleans_address(self) -> None:
        assert clean_address("123 Main St") == "123 Main St"

    def test_removes_city_state_zip(self) -> None:
        assert clean_address("123 Main St Montrose, CA 91020") == "123 Main St"


class TestCleanDate:
    def test_mm_dd_yyyy_format(self) -> None:
        assert clean_date("01/15/2024") == "01/15/2024"

    def test_iso_format_conversion(self) -> None:
        assert clean_date("2024-01-15") == "01/15/2024"

    def test_handles_none(self) -> None:
        assert clean_date(None) == ""

    # New: real dates for the typed column
    def test_parse_date(self) -> None:
        assert parse_date("10/01/2026") == date(2026, 10, 1)
        assert parse_date("2026-10-01") == date(2026, 10, 1)
        assert parse_date("Effective 10/01/2026") == date(2026, 10, 1)

    def test_parse_date_two_digit_years(self) -> None:
        """URAR forms print M/D/YY ("Effective Date of Appraisal 1/29/25"); the live run's
        fallback answer was dropped as "not a date" (EVAL_REPORT section 23). The century
        puts the date at most a year after ``today``: an appraisal's dates (effective,
        contract, comparable and prior sales) are never far in the future."""
        today = date(2026, 10, 16)
        assert parse_date("1/29/25", today=today) == date(2025, 1, 29)
        assert parse_date("Effective 01/29/25.", today=today) == date(2025, 1, 29)
        assert parse_date("12/31/27", today=today) == date(2027, 12, 31)  # within a year
        assert parse_date("1/1/28", today=today) == date(1928, 1, 1)  # beyond: last century
        assert parse_date("6/15/98", today=today) == date(1998, 6, 15)
        assert parse_date("1/29/2025", today=today) == date(2025, 1, 29)  # four digits first
        assert parse_date("2/30/25", today=today) is None
        assert parse_date("1/29/255", today=today) is None
        assert clean_date("1/29/25") == "1/29/2025"

    def test_parse_date_rejects_impossible_dates(self) -> None:
        assert parse_date("02/30/2026") is None
        assert parse_date("13/01/2026") is None
        assert parse_date("soon") is None


class TestCleanComparable:
    def test_cleans_all_fields(self) -> None:
        raw = {
            "id": 1,
            "address": "57 Walton Ave Montrose, CA 91020",
            "sale_price": "=$419,000",
            "gla_sqft": "1,428",
        }
        result = clean_comparable(raw)
        assert result["id"] == 1
        assert result["address"] == "57 Walton Ave"
        assert result["sale_price"] == Decimal("419000")
        assert result["gla_sqft"] == 1428

    def test_handles_empty_input(self) -> None:
        assert clean_comparable({}) == {}
        assert clean_comparable(None) == {}


class TestCleanSubjectFields:
    """Was ``TestCleanSubjectSection`` (the legacy nested-dict section cleaner)."""

    def test_cleans_all_fields(self) -> None:
        result = clean_fields(
            {
                "subject.property_address": "1290 W. 29th :selected:",
                "subject.city": "Montrose :unselected:",
                "subject.state": "CA",
                "subject.tax_year": "2024",
                "subject.real_estate_taxes": "=$5,000",
            }
        )
        assert result["subject.property_address"].text == "1290 W. 29th"
        assert result["subject.city"].text == "Montrose"
        assert result["subject.state"].text == "CA"
        assert result["subject.tax_year"].num == Decimal("2024")
        assert result["subject.real_estate_taxes"].num == Decimal("5000")

    def test_missing_values_are_empty_not_defaults(self) -> None:
        # Changed: the legacy cleaner invented "1004", "" and 0 for absent fields.
        result = clean_fields({"subject.property_address": None, "subject.tax_year": ""})
        assert result["subject.property_address"].is_empty
        assert result["subject.tax_year"] == CleanValue(text=None)


class TestCleanCostApproachFields:
    def test_cleans_currency_fields(self) -> None:
        result = clean_fields(
            {
                "cost_approach.site_value": "=$85,000",
                "cost_approach.total_cost_new": "$729,071",
                "cost_approach.depreciation": "156,240",
            }
        )
        assert result["cost_approach.site_value"].num == Decimal("85000")
        assert result["cost_approach.total_cost_new"].num == Decimal("729071")
        assert result["cost_approach.depreciation"].num == Decimal("156240")

    def test_cleans_integer_fields(self) -> None:
        result = clean_fields(
            {
                "improvements.effective_age_years": "15",
                "cost_approach.remaining_economic_life": "45 years",
            }
        )
        assert result["improvements.effective_age_years"].num == Decimal("15")
        assert result["cost_approach.remaining_economic_life"].num == Decimal("45")


class TestCleanImprovementsFields:
    def test_cleans_general_fields(self) -> None:
        result = clean_fields(
            {
                "improvements.year_built": "1969",
                "improvements.gla_sqft": "3,200",
                "improvements.bedrooms": "6",
                "improvements.bathrooms": "6.0",
            }
        )
        assert result["improvements.year_built"].num == Decimal("1969")
        assert result["improvements.gla_sqft"].num == Decimal("3200")
        assert result["improvements.bedrooms"].num == Decimal("6")
        assert result["improvements.bathrooms"].num == Decimal("6.0")

    def test_units_is_text(self) -> None:
        # Changed: "units" defaulted to 1; it is the printed text ("One") or missing.
        assert clean_field("improvements.units", "One").text == "One"
        assert clean_field("improvements.units", None).is_empty

    def test_bathrooms_keep_urar_notation(self) -> None:
        assert clean_field("improvements.bathrooms", "2.1").num == Decimal("2.1")


class TestIntegration:
    def test_cleans_realistic_azure_di_output(self) -> None:
        result = clean_fields(
            {
                "subject.property_address": "1290 W. 29th :selected: Montrose CA 91020",
                "subject.borrower": "John Doe :selected: 1290 W. 29th Montrose",
                "subject.city": "Montrose :unselected:",
                "subject.state": "CA :selected:",
                "subject.zip": "91020",
                "subject.real_estate_taxes": "=$5,832",
            }
        )
        assert ":selected:" not in (result["subject.property_address"].text or "")
        assert ":unselected:" not in (result["subject.city"].text or "")
        assert result["subject.real_estate_taxes"].num == Decimal("5832")
        assert result["subject.state"].text == "CA"


class TestCleanValueTypes:
    """New: one value per catalogue type, the shape ``extraction_fields`` stores."""

    def test_date(self) -> None:
        assert clean_value("date", "10/01/2026").day == date(2026, 10, 1)
        bad = clean_value("date", "sometime")
        assert bad.day is None and bad.error == "not a date" and bad.text == "sometime"

    def test_bool(self) -> None:
        assert clean_value("bool", "No") == CleanValue(text="No", num=Decimal(0))
        assert clean_value("bool", "maybe").error == "not yes/no"

    def test_percent(self) -> None:
        assert clean_percent("80%") == Decimal("80")
        assert clean_value("percent", "5%").num == Decimal("5")

    def test_currency_or_text(self) -> None:
        assert clean_value("currency_or_text", "$410,000").num == Decimal("410000")
        not_developed = clean_value("currency_or_text", "Not Developed")
        assert (not_developed.text, not_developed.num, not_developed.error) == (
            "Not Developed",
            None,
            None,
        )

    def test_typed_number_that_does_not_parse_keeps_text_and_error(self) -> None:
        value = clean_value("currency", "see addendum")
        assert (value.text, value.num, value.error) == ("see addendum", None, "not a number")

    def test_text_none_is_a_value(self) -> None:
        # "None" printed in a text field (no alley) is the answer, not a missing value.
        assert clean_field("site.off_site_improvements_alley", "None").text == "None"

    def test_typed_property(self) -> None:
        assert clean_value("int", "7").typed == Decimal(7)
        assert clean_value("text", "Q4").typed == "Q4"
        assert clean_value("text", "").typed is None

    def test_unknown_key_is_text(self) -> None:
        assert clean_field("subject.not_a_field", " x  yz ").text == "x yz"

    @pytest.mark.parametrize(
        ("printed", "square_feet"),
        [
            ("1.39 ac", 60548),  # 1.39 x 43,560 = 60,548.4
            ("1.39 Acres", 60548),
            ("0.25 acre", 10890),
            ("12,500 sf", 12500),
            ("12500 sq ft", 12500),
            ("9,600", 9600),
        ],
    )
    def test_site_area_in_acres_is_converted(self, printed: str, square_feet: int) -> None:
        """The URAR site area box is printed in acres or square feet. The live run stored
        "1.39 ac" as site.area_sqft = 1 (unit ignored, decimals cut; EVAL_REPORT 23)."""
        value = clean_field("site.area_sqft", printed)
        assert value.num == Decimal(square_feet) and value.text == printed and value.error is None


def test_split_concatenated_field() -> None:
    patterns = {"city": r"City\s+(\w+)", "zip": r"Zip\s+(\d{5})"}
    assert split_concatenated_field("City Montrose Zip 91020", patterns) == {
        "city": "Montrose",
        "zip": "91020",
    }
    assert split_concatenated_field(None, patterns) == {}


# --------------------------------------------------------------------------- EVAL_REPORT 27


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Concrete C3", "Concrete"),
        ("Hwd/Vin/Tile/Crp C3", "Hwd/Vin/Tile/Crp"),
        ("Drywall/Wood/C4", "Drywall/Wood"),
        ("Asphalt Shingle  C1", "Asphalt Shingle"),
        ("Vinyl; C6", "Vinyl"),
        ("C3", "C3"),  # only a rating: kept for the reviewer
        ("Concrete C7", "Concrete C7"),  # not a UAD rating
        ("Case/Sliding", "Case/Sliding"),
        ("Brick C3 veneer", "Brick C3 veneer"),  # the rating must end the value
    ],
)
def test_materials_condition_boxes_drop_the_rating(raw: str, expected: str) -> None:
    assert clean_field("improvements.exterior_walls", raw).text == expected
    assert clean_material(raw).text == expected


def test_only_the_materials_condition_boxes_drop_the_rating() -> None:
    assert clean_field("improvements.condition", "C3").text == "C3"
    assert clean_field("site.view", "Residential C3").text == "Residential C3"
    assert MATERIAL_CONDITION_FIELDS >= {"improvements.floors", "improvements.walls"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01/2025", "2025-01"),
        ("1/2025", "2025-01"),
        ("12/2024", "2024-12"),
        ("13/2025", "13/2025"),
        ("0/2025", "0/2025"),
        ("January 2025", "January 2025"),
        ("", None),
    ],
)
def test_the_cost_data_month_is_iso(raw: str, expected: str | None) -> None:
    assert clean_field("cost_approach.effective_date_of_cost_data", raw).text == expected
    assert clean_month(raw).text == expected


def test_printed_in_acres() -> None:
    assert printed_in_acres("1.39 ac") and printed_in_acres("0.25 Acres.")
    assert not printed_in_acres("60,548 sf") and not printed_in_acres("Lacrosse field")
