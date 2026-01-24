"""Tests for Azure DI value cleaner."""

import pytest

from agentic.utils.azure_di_cleaner import (
    clean_text,
    clean_currency,
    clean_integer,
    clean_checkbox,
    extract_selection,
    clean_address,
    clean_date,
    clean_comparable,
    clean_subject_section,
    clean_cost_approach_section,
    clean_improvements_section,
)


class TestCleanText:
    """Tests for clean_text function."""

    def test_removes_selected_marker(self):
        """Test removal of :selected: checkbox marker."""
        assert clean_text("Urban :selected:") == "Urban"
        assert clean_text(":selected: Yes") == "Yes"

    def test_removes_unselected_marker(self):
        """Test removal of :unselected: checkbox marker."""
        assert clean_text("Rural :unselected:") == "Rural"

    def test_fixes_word_splits(self):
        """Test fixing OCR word splits."""
        assert clean_text("P urchase") == "Purchase"
        assert clean_text("Le gal") == "Legal"

    def test_collapses_multiple_spaces(self):
        """Test collapsing multiple spaces."""
        assert clean_text("hello    world") == "hello world"

    def test_removes_trailing_single_chars(self):
        """Test removing trailing single characters."""
        assert clean_text("Purchase e") == "Purchase"

    def test_handles_none(self):
        """Test handling None input."""
        assert clean_text(None) == ""

    def test_handles_non_string(self):
        """Test handling non-string input."""
        assert clean_text(123) == "123"


class TestCleanCurrency:
    """Tests for clean_currency function."""

    def test_removes_leading_equals(self):
        """Test removing leading equals sign (form artifact)."""
        assert clean_currency("=$692,831") == 692831.0

    def test_removes_dollar_sign(self):
        """Test removing dollar sign."""
        assert clean_currency("$85,000") == 85000.0

    def test_removes_commas(self):
        """Test removing commas."""
        assert clean_currency("1,234,567") == 1234567.0

    def test_handles_spaces(self):
        """Test handling spaces in currency."""
        assert clean_currency("$ 85,000") == 85000.0

    def test_handles_int_input(self):
        """Test handling integer input."""
        assert clean_currency(85000) == 85000.0

    def test_handles_float_input(self):
        """Test handling float input."""
        assert clean_currency(85000.50) == 85000.50

    def test_handles_none(self):
        """Test handling None input."""
        assert clean_currency(None) == 0.0

    def test_handles_malformed_string(self):
        """Test handling malformed string."""
        assert clean_currency("not a number") == 0.0

    def test_extracts_number_from_mixed_text(self):
        """Test extracting number from mixed text."""
        assert clean_currency("approximately 85000 dollars") == 85000.0


class TestCleanInteger:
    """Tests for clean_integer function."""

    def test_converts_to_int(self):
        """Test conversion to integer."""
        assert clean_integer("3,200") == 3200

    def test_truncates_decimal(self):
        """Test truncation of decimal."""
        assert clean_integer("3.7") == 3


class TestCleanCheckbox:
    """Tests for clean_checkbox function."""

    def test_selected_marker(self):
        """Test :selected: marker."""
        assert clean_checkbox(":selected:") is True
        assert clean_checkbox("Yes :selected:") is True

    def test_unselected_marker(self):
        """Test :unselected: marker."""
        assert clean_checkbox(":unselected:") is False
        assert clean_checkbox("No :unselected:") is False

    def test_yes_no_text(self):
        """Test yes/no text."""
        assert clean_checkbox("yes") is True
        assert clean_checkbox("no") is False

    def test_bool_input(self):
        """Test boolean input passthrough."""
        assert clean_checkbox(True) is True
        assert clean_checkbox(False) is False

    def test_unclear_returns_none(self):
        """Test unclear input returns None."""
        assert clean_checkbox("maybe") is None
        assert clean_checkbox(None) is None


class TestExtractSelection:
    """Tests for extract_selection function."""

    def test_extracts_selected_option(self):
        """Test extracting selected option from options list."""
        options = ["Urban", "Suburban", "Rural"]
        assert extract_selection("Urban :selected: Suburban :unselected:", options) == "Urban"

    def test_falls_back_to_unmarked(self):
        """Test fallback to option without unselected marker."""
        options = ["Rapid", "Stable", "Slow"]
        assert extract_selection("Stable", options) == "Stable"

    def test_returns_empty_if_no_match(self):
        """Test returning empty if no option matches."""
        options = ["Urban", "Suburban"]
        assert extract_selection("Rural", options) == ""


class TestCleanAddress:
    """Tests for clean_address function."""

    def test_cleans_address(self):
        """Test basic address cleaning."""
        assert clean_address("123 Main St") == "123 Main St"

    def test_removes_city_state_zip(self):
        """Test removing concatenated city/state/zip."""
        result = clean_address("123 Main St Montrose, CA 91020")
        assert result == "123 Main St"


class TestCleanDate:
    """Tests for clean_date function."""

    def test_mm_dd_yyyy_format(self):
        """Test MM/DD/YYYY format passthrough."""
        assert clean_date("01/15/2024") == "01/15/2024"

    def test_iso_format_conversion(self):
        """Test ISO format conversion."""
        assert clean_date("2024-01-15") == "01/15/2024"

    def test_handles_none(self):
        """Test handling None."""
        assert clean_date(None) == ""


class TestCleanComparable:
    """Tests for clean_comparable function."""

    def test_cleans_all_fields(self):
        """Test cleaning all comparable fields."""
        raw = {
            "id": 1,
            "address": "57 Walton Ave Montrose, CA 91020",
            "sale_price": "=$419,000",
            "gla_sqft": "1,428",
        }
        result = clean_comparable(raw)

        assert result["id"] == 1
        assert result["address"] == "57 Walton Ave"
        assert result["sale_price"] == 419000.0
        assert result["gla_sqft"] == 1428

    def test_handles_empty_input(self):
        """Test handling empty input."""
        assert clean_comparable({}) == {}
        assert clean_comparable(None) == {}


class TestCleanSubjectSection:
    """Tests for clean_subject_section function."""

    def test_cleans_all_fields(self):
        """Test cleaning all subject fields."""
        raw = {
            "property_address": "1290 W. 29th :selected:",
            "city": "Montrose :unselected:",
            "state": "CA",
            "tax_year": "2024",
            "real_estate_taxes": "=$5,000",
        }
        result = clean_subject_section(raw)

        assert result["property_address"] == "1290 W. 29th"
        assert result["city"] == "Montrose"
        assert result["state"] == "CA"
        assert result["tax_year"] == 2024
        assert result["real_estate_taxes"] == 5000.0

    def test_provides_defaults(self):
        """Test providing default values."""
        result = clean_subject_section({})

        assert result["form"] == "1004"
        assert result["property_address"] == ""
        assert result["tax_year"] == 0


class TestCleanCostApproachSection:
    """Tests for clean_cost_approach_section function."""

    def test_cleans_currency_fields(self):
        """Test cleaning currency fields."""
        raw = {
            "site_value": "=$85,000",
            "total_cost_new": "$729,071",
            "depreciation": "156,240",
        }
        result = clean_cost_approach_section(raw)

        assert result["site_value"] == 85000.0
        assert result["total_cost_new"] == 729071.0
        assert result["depreciation"] == 156240.0

    def test_cleans_integer_fields(self):
        """Test cleaning integer fields."""
        raw = {
            "effective_age_years": "15",
            "remaining_economic_life_years": "45",
        }
        result = clean_cost_approach_section(raw)

        assert result["effective_age_years"] == 15
        assert result["remaining_economic_life_years"] == 45


class TestCleanImprovementsSection:
    """Tests for clean_improvements_section function."""

    def test_cleans_general_section(self):
        """Test cleaning general improvements section."""
        raw = {
            "general": {
                "year_built": "1969",
                "gla_sqft": "3,200",
                "bedrooms": "6",
                "bathrooms": "6.0",
            },
            "exterior": {},
            "interior_mechanical": {},
        }
        result = clean_improvements_section(raw)

        assert result["general"]["year_built"] == 1969
        assert result["general"]["gla_sqft"] == 3200
        assert result["general"]["bedrooms"] == 6
        assert result["general"]["bathrooms"] == 6.0

    def test_defaults_units_to_one(self):
        """Test that units defaults to 1."""
        result = clean_improvements_section({"general": {}, "exterior": {}, "interior_mechanical": {}})
        assert result["general"]["units"] == 1

    def test_handles_missing_subsections(self):
        """Test handling missing subsections."""
        result = clean_improvements_section({})

        assert "general" in result
        assert "exterior" in result
        assert "interior_mechanical" in result


class TestIntegration:
    """Integration tests for the cleaner."""

    def test_cleans_realistic_azure_di_output(self):
        """Test cleaning realistic Azure DI output with artifacts."""
        raw_subject = {
            "property_address": "1290 W. 29th :selected: Montrose CA 91020",
            "borrower": "John Doe :selected: 1290 W. 29th Montrose",  # Concatenated
            "city": "Montrose :unselected:",
            "state": "CA :selected:",
            "zip": "91020",
            "real_estate_taxes": "=$5,832",
        }

        result = clean_subject_section(raw_subject)

        # Should clean checkbox markers
        assert ":selected:" not in result["property_address"]
        assert ":unselected:" not in result["city"]

        # Should clean currency
        assert result["real_estate_taxes"] == 5832.0

        # State should be clean
        assert result["state"] == "CA"
