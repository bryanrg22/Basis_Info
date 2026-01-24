"""
Azure Document Intelligence value cleaner.

Cleans and normalizes raw values extracted by Azure DI from URAR forms.
Handles form artifacts, malformed numbers, checkbox selections, and
concatenated field values.

This is document-agnostic - it doesn't assume specific field positions,
just cleans common artifacts that appear in form extraction.
"""

import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Value Cleaning Functions
# =============================================================================


def clean_text(value: Any) -> str:
    """
    Clean text value from Azure DI extraction.

    Handles:
    - :selected:/:unselected: checkbox artifacts
    - Multiple spaces
    - Word splits from OCR (e.g., "P urchase" → "Purchase")
    - Trailing single characters
    - None/non-string values

    Args:
        value: Raw value from Azure DI

    Returns:
        Cleaned string
    """
    if value is None:
        return ""

    if not isinstance(value, str):
        return str(value)

    text = value

    # Remove checkbox artifacts
    text = re.sub(r':selected:', '', text, flags=re.IGNORECASE)
    text = re.sub(r':unselected:', '', text, flags=re.IGNORECASE)

    # Fix common word splits from PDF extraction
    # Pattern: single uppercase letter followed by space and lowercase continuation
    text = re.sub(r'\b([A-Z])\s+([a-z])', r'\1\2', text)

    # Fix "Le gal" type splits (two letter + space + rest)
    text = re.sub(r'\b([A-Z][a-z])\s+([a-z]{2,})', r'\1\2', text)

    # Remove trailing single characters (stray OCR artifacts)
    text = re.sub(r'\s+[a-z]\s*$', '', text)

    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def clean_currency(value: Any) -> float:
    """
    Clean currency value from Azure DI extraction.

    Handles:
    - Leading equals sign (e.g., "=$692,831")
    - Dollar signs
    - Commas in numbers
    - Spaces around numbers
    - Malformed values

    Args:
        value: Raw currency value

    Returns:
        Float value, or 0 if cannot parse
    """
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    if not isinstance(value, str):
        return 0.0

    text = value.strip()

    # Remove leading equals sign (form artifact)
    text = text.lstrip('=')

    # Remove dollar sign and spaces
    text = text.replace('$', '').replace(' ', '')

    # Remove commas
    text = text.replace(',', '')

    # Try to extract number
    try:
        return float(text)
    except ValueError:
        # Try regex extraction as fallback
        match = re.search(r'([\d.]+)', text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return 0.0


def clean_integer(value: Any) -> int:
    """
    Clean integer value from Azure DI extraction.

    Args:
        value: Raw integer value

    Returns:
        Integer value, or 0 if cannot parse
    """
    return int(clean_currency(value))


def clean_checkbox(value: Any) -> Optional[bool]:
    """
    Parse checkbox value from Azure DI extraction.

    Args:
        value: Raw checkbox value (may contain :selected:/:unselected:)

    Returns:
        True if selected, False if unselected, None if unclear
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if not isinstance(value, str):
        return None

    text = value.lower()

    if ':selected:' in text or 'yes' in text or 'true' in text or text == 'x':
        return True
    elif ':unselected:' in text or 'no' in text or 'false' in text:
        return False

    return None


def extract_selection(value: Any, options: list[str]) -> str:
    """
    Extract selected option from Azure DI value.

    Handles form fields where multiple options appear but only one is selected.

    Args:
        value: Raw value that may contain :selected:/:unselected: markers
        options: List of valid option values to look for

    Returns:
        The selected option, or empty string if none found
    """
    if value is None or not isinstance(value, str):
        return ""

    text = value

    # Check if any option is marked as selected
    for option in options:
        # Look for option followed by :selected:
        pattern = rf'{re.escape(option)}\s*:selected:'
        if re.search(pattern, text, re.IGNORECASE):
            return option

    # Fallback: look for option that appears without :unselected:
    for option in options:
        if option.lower() in text.lower():
            # Make sure it's not marked as unselected
            pattern = rf'{re.escape(option)}\s*:unselected:'
            if not re.search(pattern, text, re.IGNORECASE):
                return option

    return ""


def split_concatenated_field(value: Any, field_patterns: dict[str, str]) -> dict[str, str]:
    """
    Split a concatenated field back into separate values.

    Azure DI sometimes concatenates multiple fields together.
    This attempts to split them based on known patterns.

    Args:
        value: Concatenated value
        field_patterns: Dict mapping field_name → regex pattern to extract it

    Returns:
        Dict with extracted field values
    """
    if value is None or not isinstance(value, str):
        return {}

    results = {}
    text = value

    for field_name, pattern in field_patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                results[field_name] = match.group(1).strip()
            except (IndexError, AttributeError):
                pass

    return results


def clean_address(value: Any) -> str:
    """
    Clean address value from Azure DI.

    Handles common address concatenation issues.

    Args:
        value: Raw address value

    Returns:
        Cleaned address string
    """
    text = clean_text(value)

    # Remove city/state/zip if accidentally concatenated
    # (we extract those separately)
    # Pattern: address followed by city, state zip
    match = re.match(r'^(.+?)\s+(?:[A-Z][a-z]+),?\s+[A-Z]{2}\s+\d{5}', text)
    if match:
        return match.group(1).strip()

    return text


def clean_date(value: Any) -> str:
    """
    Clean and normalize date value.

    Args:
        value: Raw date value

    Returns:
        Date string in MM/DD/YYYY format, or empty if invalid
    """
    if value is None:
        return ""

    if not isinstance(value, str):
        value = str(value)

    text = value.strip()

    # Try to find date pattern
    # MM/DD/YYYY
    match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if match:
        return f"{match.group(1)}/{match.group(2)}/{match.group(3)}"

    # YYYY-MM-DD (ISO format)
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if match:
        return f"{match.group(2)}/{match.group(3)}/{match.group(1)}"

    return text


# =============================================================================
# Comparable Property Cleaner
# =============================================================================


def clean_comparable(comp_data: dict) -> dict:
    """
    Clean comparable property data from Azure DI.

    Args:
        comp_data: Raw comparable data dict

    Returns:
        Cleaned comparable dict
    """
    if not comp_data:
        return {}

    return {
        "id": comp_data.get("id", 0),
        "address": clean_address(comp_data.get("address", "")),
        "city": clean_text(comp_data.get("city", "")),
        "state": clean_text(comp_data.get("state", "")),
        "proximity": clean_text(comp_data.get("proximity", "")),
        "sale_price": clean_currency(comp_data.get("sale_price", 0)),
        "adjusted_sale_price": clean_currency(comp_data.get("adjusted_sale_price", 0)),
        "price_per_sqft": clean_currency(comp_data.get("price_per_sqft", 0)),
        "gla_sqft": clean_integer(comp_data.get("gla_sqft", 0)),
        "lot_size": clean_text(comp_data.get("lot_size", "")),
        "design": clean_text(comp_data.get("design", "")),
        "condition": clean_text(comp_data.get("condition", "")),
        "sale_date": clean_date(comp_data.get("sale_date", "")),
    }


# =============================================================================
# Full Section Cleaners
# =============================================================================


def clean_subject_section(raw: dict) -> dict:
    """Clean subject section data."""
    return {
        "form": clean_text(raw.get("form", "")) or "1004",
        "appraisal_company": clean_text(raw.get("appraisal_company", "")),
        "appraiser_phone": clean_text(raw.get("appraiser_phone", "")),
        "file_number": clean_text(raw.get("file_number", "")),
        "internal_id": clean_text(raw.get("internal_id", "")),
        "property_address": clean_address(raw.get("property_address", "")),
        "city": clean_text(raw.get("city", "")),
        "state": clean_text(raw.get("state", "")),
        "zip": clean_text(raw.get("zip", "")),
        "borrower": clean_text(raw.get("borrower", "")),
        "owner_of_public_record": clean_text(raw.get("owner_of_public_record", "")),
        "county": clean_text(raw.get("county", "")),
        "legal_description": clean_text(raw.get("legal_description", "")),
        "assessors_parcel_numbers": raw.get("assessors_parcel_numbers", []),
        "tax_year": clean_integer(raw.get("tax_year", 0)),
        "real_estate_taxes": clean_currency(raw.get("real_estate_taxes", 0)),
        "neighborhood_name": clean_text(raw.get("neighborhood_name", "")),
        "map_reference": clean_text(raw.get("map_reference", "")),
        "census_tract": clean_text(raw.get("census_tract", "")),
        "property_rights_appraised": clean_text(raw.get("property_rights_appraised", "")),
        "assignment_type": clean_text(raw.get("assignment_type", "")),
        "lender_client": clean_text(raw.get("lender_client", "")),
    }


def clean_listing_contract_section(raw: dict) -> dict:
    """Clean listing and contract section data."""
    return {
        "mls_number": clean_text(raw.get("mls_number", "")),
        "days_on_market": clean_integer(raw.get("days_on_market", 0)),
        "listing_date": clean_date(raw.get("listing_date", "")),
        "original_list_price": clean_currency(raw.get("original_list_price", 0)),
        "listing_expiration_date": clean_date(raw.get("listing_expiration_date", "")),
        "contract_price": clean_currency(raw.get("contract_price", 0)),
        "contract_date": clean_date(raw.get("contract_date", "")),
        "sale_type": clean_text(raw.get("sale_type", "")),
        "contract_documents_reviewed": raw.get("contract_documents_reviewed", []),
        "contract_provided_by": clean_text(raw.get("contract_provided_by", "")),
        "financial_assistance_concessions": clean_currency(raw.get("financial_assistance_concessions", 0)),
        "subject_offered_for_sale_prior_12_months": clean_checkbox(raw.get("subject_offered_for_sale_prior_12_months")),
    }


def clean_neighborhood_section(raw: dict) -> dict:
    """Clean neighborhood section data."""
    # Handle selection fields
    location_options = ["Urban", "Suburban", "Rural"]
    built_up_options = ["Over 75%", "25-75%", "Under 25%"]
    growth_options = ["Rapid", "Stable", "Slow"]
    value_trend_options = ["Increasing", "Stable", "Declining"]
    demand_supply_options = ["Shortage", "In Balance", "Over Supply"]
    marketing_time_options = ["Under 3 months", "3-6 months", "Over 6 months"]

    return {
        "location": extract_selection(raw.get("location", ""), location_options) or clean_text(raw.get("location", "")),
        "built_up": extract_selection(raw.get("built_up", ""), built_up_options) or clean_text(raw.get("built_up", "")),
        "growth": extract_selection(raw.get("growth", ""), growth_options) or clean_text(raw.get("growth", "")),
        "one_unit_value_trend": extract_selection(raw.get("one_unit_value_trend", ""), value_trend_options) or clean_text(raw.get("one_unit_value_trend", "")),
        "demand_supply": extract_selection(raw.get("demand_supply", ""), demand_supply_options) or clean_text(raw.get("demand_supply", "")),
        "typical_marketing_time": extract_selection(raw.get("typical_marketing_time", ""), marketing_time_options) or clean_text(raw.get("typical_marketing_time", "")),
        "one_unit_listings": {
            "count": clean_integer(raw.get("one_unit_listings", {}).get("count", 0) if isinstance(raw.get("one_unit_listings"), dict) else 0),
            "price_range_low": clean_currency(raw.get("one_unit_listings", {}).get("price_range_low", 0) if isinstance(raw.get("one_unit_listings"), dict) else 0),
            "price_range_high": clean_currency(raw.get("one_unit_listings", {}).get("price_range_high", 0) if isinstance(raw.get("one_unit_listings"), dict) else 0),
        },
        "one_unit_sales_12_months": {
            "count": clean_integer(raw.get("one_unit_sales_12_months", {}).get("count", 0) if isinstance(raw.get("one_unit_sales_12_months"), dict) else 0),
            "price_range_low": clean_currency(raw.get("one_unit_sales_12_months", {}).get("price_range_low", 0) if isinstance(raw.get("one_unit_sales_12_months"), dict) else 0),
            "price_range_high": clean_currency(raw.get("one_unit_sales_12_months", {}).get("price_range_high", 0) if isinstance(raw.get("one_unit_sales_12_months"), dict) else 0),
        },
        "boundaries": raw.get("boundaries", {}),
        "description": clean_text(raw.get("description", "")),
        "market_notes": clean_text(raw.get("market_notes", "")),
    }


def clean_site_section(raw: dict) -> dict:
    """Clean site section data."""
    return {
        "dimensions": clean_text(raw.get("dimensions", "")),
        "area_acres": clean_currency(raw.get("area_acres", 0)),
        "shape": clean_text(raw.get("shape", "")),
        "view": clean_text(raw.get("view", "")),
        "zoning_classification": clean_text(raw.get("zoning_classification", "")),
        "zoning_description": clean_text(raw.get("zoning_description", "")),
        "zoning_compliance": clean_text(raw.get("zoning_compliance", "")),
        "highest_and_best_use_as_improved": clean_text(raw.get("highest_and_best_use_as_improved", "")),
        "utilities": raw.get("utilities", {}),
        "off_site_improvements": raw.get("off_site_improvements", {}),
        "flood_hazard_area": clean_checkbox(raw.get("flood_hazard_area")),
        "flood_zone": clean_text(raw.get("flood_zone", "")),
        "fema_map_number": clean_text(raw.get("fema_map_number", "")),
        "fema_map_date": clean_date(raw.get("fema_map_date", "")),
        "easements_encroachments": clean_text(raw.get("easements_encroachments", "")),
        "site_comments": clean_text(raw.get("site_comments", "")),
    }


def clean_improvements_section(raw: dict) -> dict:
    """Clean improvements section data."""
    general = raw.get("general", {}) if isinstance(raw.get("general"), dict) else {}
    exterior = raw.get("exterior", {}) if isinstance(raw.get("exterior"), dict) else {}
    interior = raw.get("interior_mechanical", {}) if isinstance(raw.get("interior_mechanical"), dict) else {}

    return {
        "general": {
            "units": clean_integer(general.get("units", 1)) or 1,
            "stories": clean_integer(general.get("stories", 0)),
            "type": clean_text(general.get("type", "")),
            "status": clean_text(general.get("status", "")),
            "design_style": clean_text(general.get("design_style", "")),
            "year_built": clean_integer(general.get("year_built", 0)),
            "effective_age_years": clean_integer(general.get("effective_age_years", 0)),
            "foundation_type": clean_text(general.get("foundation_type", "")),
            "basement_area_sqft": clean_integer(general.get("basement_area_sqft", 0)),
            "basement_finish_percent": clean_integer(general.get("basement_finish_percent", 0)),
            "basement_access": clean_text(general.get("basement_access", "")),
            "overall_quality": clean_text(general.get("overall_quality", "")),
            "overall_condition": clean_text(general.get("overall_condition", "")),
            "gla_sqft": clean_integer(general.get("gla_sqft", 0)),
            "total_rooms": clean_integer(general.get("total_rooms", 0)),
            "bedrooms": clean_integer(general.get("bedrooms", 0)),
            "bathrooms": clean_currency(general.get("bathrooms", 0)),  # Can be decimal (e.g., 2.5)
        },
        "exterior": {
            "foundation_walls": clean_text(exterior.get("foundation_walls", "")),
            "exterior_walls": clean_text(exterior.get("exterior_walls", "")),
            "roof_surface": clean_text(exterior.get("roof_surface", "")),
            "gutters": clean_text(exterior.get("gutters", "")),
            "window_type": clean_text(exterior.get("window_type", "")),
            "storm_sash": clean_text(exterior.get("storm_sash", "")),
            "screens": clean_text(exterior.get("screens", "")),
        },
        "interior_mechanical": {
            "floors": clean_text(interior.get("floors", "")),
            "walls": clean_text(interior.get("walls", "")),
            "trim_finish": clean_text(interior.get("trim_finish", "")),
            "bath_floor": clean_text(interior.get("bath_floor", "")),
            "bath_wainscot": clean_text(interior.get("bath_wainscot", "")),
            "heating": interior.get("heating", {}),
            "heating_fuel": clean_text(interior.get("heating_fuel", "")),
            "cooling": clean_text(interior.get("cooling", "")),
            "fireplaces": interior.get("fireplaces", {}),
            "garage_cars": clean_integer(interior.get("garage_cars", 0)),
            "carport_cars": clean_integer(interior.get("carport_cars", 0)),
            "driveway_surface": clean_text(interior.get("driveway_surface", "")),
            "pool": clean_text(interior.get("pool", "")),
            "patio_deck": clean_text(interior.get("patio_deck", "")),
            "porch": clean_text(interior.get("porch", "")),
            "fence": clean_text(interior.get("fence", "")),
            "appliances": interior.get("appliances", {}),
            "gross_living_area_above_grade_sqft": clean_integer(interior.get("gross_living_area_above_grade_sqft", 0)),
            "rooms_above_grade": interior.get("rooms_above_grade", {}),
        },
    }


def clean_cost_approach_section(raw: dict) -> dict:
    """Clean cost approach section data."""
    return {
        "site_value": clean_currency(raw.get("site_value", 0)),
        "improvements_cost_new": raw.get("improvements_cost_new", {}),
        "total_cost_new": clean_currency(raw.get("total_cost_new", 0)),
        "depreciation": clean_currency(raw.get("depreciation", 0)),
        "depreciated_cost_of_improvements": clean_currency(raw.get("depreciated_cost_of_improvements", 0)),
        "as_is_site_improvements_value": clean_currency(raw.get("as_is_site_improvements_value", 0)),
        "indicated_value_by_cost_approach": clean_currency(raw.get("indicated_value_by_cost_approach", 0)),
        "effective_age_years": clean_integer(raw.get("effective_age_years", 0)),
        "remaining_economic_life_years": clean_integer(raw.get("remaining_economic_life_years", 0)),
        "cost_data_source": clean_text(raw.get("cost_data_source", "")),
        "comments": clean_text(raw.get("comments", "")),
    }


def clean_reconciliation_section(raw: dict) -> dict:
    """Clean reconciliation section data."""
    return {
        "indicated_value_sales_comparison": clean_currency(raw.get("indicated_value_sales_comparison", 0)),
        "indicated_value_cost_approach": clean_currency(raw.get("indicated_value_cost_approach", 0)),
        "indicated_value_income_approach": clean_currency(raw.get("indicated_value_income_approach", 0)) or None,
        "final_market_value": clean_currency(raw.get("final_market_value", 0)),
        "effective_date_of_appraisal": clean_date(raw.get("effective_date_of_appraisal", "")),
        "value_condition": clean_text(raw.get("value_condition", "")),
        "comments": clean_text(raw.get("comments", "")),
    }


def clean_sales_comparison_section(raw: dict) -> dict:
    """Clean sales comparison section data."""
    # Clean comparables
    comparables = []
    raw_comps = raw.get("comparables", [])
    if isinstance(raw_comps, list):
        for i, comp in enumerate(raw_comps):
            if isinstance(comp, dict):
                cleaned = clean_comparable(comp)
                cleaned["id"] = cleaned.get("id") or (i + 1)
                comparables.append(cleaned)

    # Clean market stats
    market_stats = raw.get("market_stats", {})
    if isinstance(market_stats, dict):
        cleaned_stats = {
            "active_listings_count": clean_integer(market_stats.get("active_listings_count", 0)),
            "active_listings_price_range": {
                "low": clean_currency(market_stats.get("active_listings_price_range", {}).get("low", 0) if isinstance(market_stats.get("active_listings_price_range"), dict) else 0),
                "high": clean_currency(market_stats.get("active_listings_price_range", {}).get("high", 0) if isinstance(market_stats.get("active_listings_price_range"), dict) else 0),
            },
            "sales_12_months_count": clean_integer(market_stats.get("sales_12_months_count", 0)),
            "sales_12_months_price_range": {
                "low": clean_currency(market_stats.get("sales_12_months_price_range", {}).get("low", 0) if isinstance(market_stats.get("sales_12_months_price_range"), dict) else 0),
                "high": clean_currency(market_stats.get("sales_12_months_price_range", {}).get("high", 0) if isinstance(market_stats.get("sales_12_months_price_range"), dict) else 0),
            },
        }
    else:
        cleaned_stats = {}

    # Clean subject
    subject = raw.get("subject", {})
    if isinstance(subject, dict):
        cleaned_subject = {
            "address": clean_address(subject.get("address", "")),
            "city": clean_text(subject.get("city", "")),
            "state": clean_text(subject.get("state", "")),
            "contract_price": clean_currency(subject.get("contract_price", 0)),
            "price_per_sqft": clean_currency(subject.get("price_per_sqft", 0)),
            "gross_living_area_sqft": clean_integer(subject.get("gross_living_area_sqft", 0)),
        }
    else:
        cleaned_subject = {}

    return {
        "market_stats": cleaned_stats,
        "subject": cleaned_subject,
        "comparables": comparables,
    }
