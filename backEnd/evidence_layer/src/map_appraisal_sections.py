"""
Map extracted URAR tables to frontend section structure.

URAR (Uniform Residential Appraisal Report) has a standardized layout:
- Page 1: Subject, Contract, Neighborhood sections
- Page 2: Site, Improvements sections
- Page 3: Sales Comparison grid
- Page 4: Cost Approach, Reconciliation

IMPORTANT: URAR tables are complex forms where data is embedded in long strings
within table cells (e.g., "Property Address 1290 W. 29th City Montrose State CA").
This module extracts all text from table rows and parses with regex patterns.
"""

import re
from pathlib import Path
from typing import Any, Optional

from .schemas.table import Table
from .extract_tables import load_tables


# =============================================================================
# Helper Functions
# =============================================================================

def _extract(text: str, pattern: str, group: int = 1, clean: bool = True) -> str:
    """
    Extract first regex match from text.

    Args:
        text: Text to search
        pattern: Regex pattern with capture group
        group: Which capture group to return (default 1)
        clean: Whether to clean OCR artifacts (default True)

    Returns:
        Matched string or empty string
    """
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            result = match.group(group).strip()
            if clean:
                result = _clean_text(result)
            return result
        except (IndexError, AttributeError):
            return ""
    return ""


def _extract_number(text: str, pattern: str) -> float:
    """
    Extract a number from text using regex pattern.

    Args:
        text: Text to search
        pattern: Regex pattern with capture group for the number

    Returns:
        Extracted number or 0
    """
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            # Remove commas and convert to float
            num_str = match.group(1).replace(",", "").strip()
            return float(num_str)
        except (IndexError, ValueError, AttributeError):
            return 0
    return 0


def _extract_int(text: str, pattern: str) -> int:
    """Extract an integer from text."""
    return int(_extract_number(text, pattern))


def _clean_text(text: str) -> str:
    """
    Clean up common OCR/PDF extraction artifacts.

    Fixes:
    - "P urchase" → "Purchase" (space after first letter)
    - "Le gal" → "Legal" (space in middle of word)
    - Trailing single characters like " e" at end
    - Multiple spaces
    """
    if not text:
        return text

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


def _get_all_table_text(tables: list[Table]) -> str:
    """
    Concatenate all text from table rows.

    URAR forms have data embedded in long strings within cells.
    This combines all text for regex extraction.
    """
    all_text = ""
    for table in tables:
        # Add headers
        for header in table.headers:
            if header and isinstance(header, str):
                all_text += " " + header
        # Add all row cells
        for row in table.rows:
            for cell in row:
                if cell and isinstance(cell, str):
                    all_text += " " + cell
    return all_text


# =============================================================================
# Section Parsers - Extract from Combined Text
# =============================================================================

def _parse_subject(text: str) -> dict:
    """Parse subject info from combined table text."""
    return {
        "form": _extract(text, r'Form\s+(\d+)') or "1004",
        "appraisal_company": _extract(text, r'([A-Z][a-z]+ Appraisal)'),
        "appraiser_phone": _extract(text, r'\((\d{3})\)\s*(\d{3})-(\d{4})'),
        "file_number": _extract(text, r'File\s*(?:#|Number|No\.?)?\s*(\w+)'),
        "internal_id": "",
        "property_address": _extract(text, r'Property Address\s+(.+?)\s+City'),
        "city": _extract(text, r'City\s+([A-Za-z\s]+?)\s+State'),
        "state": _extract(text, r'State\s+([A-Z]{2})\s+Zip'),
        "zip": _extract(text, r'Zip(?:\s+Code)?\s+(\d{5})'),
        "borrower": _extract(text, r'Borrower\s+(.+?)\s+Owner'),
        "owner_of_public_record": _extract(text, r'Owner of Public Record\s+(.+?)\s+County'),
        "county": _extract(text, r'County\s+([A-Za-z]+)'),
        "legal_description": _extract(text, r'Legal Description\s+(.+?)\s+Assessor'),
        "assessors_parcel_numbers": [_extract(text, r"Assessor's Parcel #\s+([\d\-\s&]+)")],
        "tax_year": _extract_int(text, r'Tax Year\s+(\d{4})'),
        "real_estate_taxes": _extract_number(text, r'R\.?E\.?\s+Taxes\s+\$?\s*([\d,]+)'),
        "neighborhood_name": _extract(text, r'Neighborhood Name\s+(.+?)\s+Map'),
        "map_reference": _extract(text, r'Map Reference\s+(\w+)'),
        "census_tract": _extract(text, r'Census Tract\s+([\d.]+)'),
        "property_rights_appraised": _extract(text, r'Property Rights Appraised\s+(.+?)\s+(?:Leasehold|Assignment)'),
        "assignment_type": _extract(text, r'Assignment Type\s+(.+?)\s+(?:Transaction|Lender)'),
        # Be specific: "Lender/Client" followed by company name, not the boilerplate "lender/client with an accurate..."
        "lender_client": _extract(text, r'Lender/Client\s+([A-Z][A-Za-z\s]+(?:LLC|Inc|Corp|Company|Bank|Mortgage)?)\s+(?:Address|Borrower)'),
    }


def _parse_listing_and_contract(text: str) -> dict:
    """Parse listing/contract info from combined table text."""
    # Extract DOM from various formats
    dom = _extract_int(text, r'DOM\s+(\d+)')
    if not dom:
        dom = _extract_int(text, r'Days on Market\s+(\d+)')

    # Extract list price
    list_price = _extract_number(text, r'listed.*?for\s+\$?([\d,]+)')
    if not list_price:
        list_price = _extract_number(text, r'list(?:ing)?\s+price\s+\$?([\d,]+)')

    return {
        "mls_number": _extract(text, r'#(\d{7})') or _extract(text, r'MLS\s*#?\s*(\w+)'),
        "days_on_market": dom,
        "listing_date": _extract(text, r'listed on\s+(\d{2}/\d{2}/\d{4})'),
        "original_list_price": list_price,
        "listing_expiration_date": _extract(text, r'expires?\s+(?:on\s+)?(\d{1,2}/\d{1,2}/\d{4})'),
        "contract_price": _extract_number(text, r'Contract Price\s+\$?\s*([\d,]+)'),
        "contract_date": _extract(text, r'Date of Contract\s+(\d{2}/\d{2}/\d{4})'),
        "sale_type": _extract(text, r'Sale Type\s+(\w+)'),
        "contract_documents_reviewed": [],
        "contract_provided_by": _extract(text, r'contract (?:was )?provided (?:by )?([\w\s]+)'),
        "financial_assistance_concessions": _extract_number(text, r'(?:assistance|concessions)[^\$]*\$?\s*([\d,]+)'),
        "subject_offered_for_sale_prior_12_months": "yes" in text.lower() and "offered for sale" in text.lower(),
    }


def _parse_neighborhood(text: str) -> dict:
    """Parse neighborhood info from combined table text."""
    # Extract location type
    location = ""
    if re.search(r'Location\s+Urban', text, re.IGNORECASE):
        location = "Urban"
    elif re.search(r'Location\s+Suburban', text, re.IGNORECASE):
        location = "Suburban"
    elif re.search(r'Location\s+Rural', text, re.IGNORECASE):
        location = "Rural"

    # Extract built-up percentage
    built_up = ""
    if re.search(r'Built-Up\s+Over 75%', text, re.IGNORECASE):
        built_up = "Over 75%"
    elif re.search(r'Built-Up\s+25-75%', text, re.IGNORECASE):
        built_up = "25-75%"
    elif re.search(r'Built-Up\s+Under 25%', text, re.IGNORECASE):
        built_up = "Under 25%"

    # Extract growth
    growth = ""
    if re.search(r'Growth\s+Rapid', text, re.IGNORECASE):
        growth = "Rapid"
    elif re.search(r'Growth\s+Stable', text, re.IGNORECASE):
        growth = "Stable"
    elif re.search(r'Growth\s+Slow', text, re.IGNORECASE):
        growth = "Slow"

    # Extract property values trend
    value_trend = ""
    if re.search(r'Property Values\s+Increasing', text, re.IGNORECASE):
        value_trend = "Increasing"
    elif re.search(r'Property Values\s+Stable', text, re.IGNORECASE):
        value_trend = "Stable"
    elif re.search(r'Property Values\s+Declining', text, re.IGNORECASE):
        value_trend = "Declining"

    # Extract demand/supply
    demand_supply = ""
    if re.search(r'Demand/Supply\s+Shortage', text, re.IGNORECASE):
        demand_supply = "Shortage"
    elif re.search(r'Demand/Supply\s+In Balance', text, re.IGNORECASE):
        demand_supply = "In Balance"
    elif re.search(r'Demand/Supply\s+Over Supply', text, re.IGNORECASE):
        demand_supply = "Over Supply"

    # Extract marketing time
    marketing_time = ""
    if re.search(r'Marketing Time\s+Under 3', text, re.IGNORECASE):
        marketing_time = "Under 3 months"
    elif re.search(r'Marketing Time\s+3-6', text, re.IGNORECASE):
        marketing_time = "3-6 months"
    elif re.search(r'Marketing Time\s+Over 6', text, re.IGNORECASE):
        marketing_time = "Over 6 months"

    # Extract price range from one-unit housing
    low_price = _extract_number(text, r'(\d{2,3})\s+Low')
    high_price = _extract_number(text, r'([\d,]+)\s+High')
    pred_price = _extract_number(text, r'([\d,]+)\s+Pred')

    # If prices are in thousands (like 130, 1250), multiply
    if low_price < 1000:
        low_price *= 1000
    if high_price < 10000 and high_price > 0:
        high_price *= 1000
    if pred_price < 1000 and pred_price > 0:
        pred_price *= 1000

    # Extract boundaries
    boundaries = {
        "north": _extract(text, r'North(?:ern)?\s+(?:is\s+)?(.+?)[,\s]+South'),
        "south": _extract(text, r'South(?:ern)?\s+(?:is\s+)?(.+?)[,\s]+East'),
        "east": _extract(text, r'East(?:ern)?\s+(?:is\s+)?(.+?)[,\s]+(?:West|and)'),
        "west": _extract(text, r'West(?:ern)?\s+(?:is\s+)?(.+?)\.'),
    }

    return {
        "location": location,
        "built_up": built_up,
        "growth": growth,
        "one_unit_value_trend": value_trend,
        "demand_supply": demand_supply,
        "typical_marketing_time": marketing_time,
        "one_unit_listings": {
            "count": _extract_int(text, r'(\d+)\s+comparable\s+(?:properties\s+)?currently\s+offered'),
            "price_range_low": _extract_number(text, r'ranging in price from\s+\$?\s*([\d,]+)'),
            "price_range_high": _extract_number(text, r'ranging in price from.*?to\s+\$?\s*([\d,]+)'),
        },
        "one_unit_sales_12_months": {
            "count": _extract_int(text, r'(\d+)\s+comparable sales'),
            "price_range_low": low_price,
            "price_range_high": high_price,
        },
        "boundaries": boundaries,
        "description": _extract(text, r'Neighborhood Description\s+(.+?)\s+(?:Market Conditions|Other land use)'),
        # Extract the market conditions narrative (after the header text)
        "market_notes": _extract(text, r'Market Conditions.*?conclusions\)\s+(.+?)\s+(?:ETIS|SITE|Dimensions)'),
    }


def _parse_site(text: str) -> dict:
    """Parse site info from combined table text."""
    # Check flood hazard
    flood_hazard = bool(re.search(r'FEMA.*?Yes', text, re.IGNORECASE))

    return {
        "dimensions": _extract(text, r'Dimensions\s+(.+?)\s+Area'),
        "area_acres": _extract_number(text, r'Area\s+([\d.]+)\s*ac'),
        "shape": _extract(text, r'Shape\s+(\w+)'),
        "view": _extract(text, r'View\s+([^;]+)'),
        "zoning_classification": _extract(text, r'(?:Specific\s+)?Zoning Classification\s+(.+?)\s+Zoning Description'),
        "zoning_description": _extract(text, r'Zoning Description\s+(.+?)\s+(?:Zoning Compliance|Is the)'),
        # Extract just "Legal" or similar, not the full checkbox text
        "zoning_compliance": _extract(text, r'Zoning Compliance\s+(Le\s*gal|Legal|Illegal|Nonconforming)'),
        # The HBU answer comes after "If No, describe" - extract the actual description
        "highest_and_best_use_as_improved": _extract(text, r'If No, describe\s+(.+?)\s+(?:Utilities|Public|FEMA)'),
        "utilities": {
            "electric": "Public" if re.search(r'Electricity\s+.*Public', text) else "",
            "gas": _extract(text, r'Gas\s+(.+?)\s+(?:Sanitary|Water)'),
            "water": "Municipal" if re.search(r'Water\s+.*Municipal', text) else _extract(text, r'Water\s+(\w+)'),
            "sanitary_sewer": "Municipal" if re.search(r'Sanitary Sewer\s+.*Municipal', text) else "",
        },
        "off_site_improvements": {
            "street": _extract(text, r'Street\s+(\w+)'),
            "alley": _extract(text, r'Alley\s+(\w+)') or None,
        },
        "flood_hazard_area": flood_hazard,
        "flood_zone": _extract(text, r'FEMA Flood Zone\s+(\w+)'),
        "fema_map_number": _extract(text, r'FEMA Map #\s+(\w+)'),
        "fema_map_date": _extract(text, r'FEMA Map Date\s+(\d{2}/\d{2}/\d{4})'),
        # Extract easements description; if just punctuation, return "None"
        "easements_encroachments": _extract(text, r'If Yes, describe\s+(.+?)\s+(?:No survey|The utilities|FEMA)') or "None",
        "site_comments": _extract(text, r'utilities were all functioning(.+?)\s+(?:IMPROVEMENTS|General Description)'),
    }


def _parse_improvements(text: str) -> dict:
    """Parse improvements info from combined table text."""
    # Extract GLA from the explicit statement
    # Text: "Finished area above grade contains: 11 Rooms 6 Bedrooms 6.0 Bath(s) 3,200 Square Feet of Gross Living Area Above Grade"
    gla = _extract_int(text, r'(\d[\d,]+)\s+Square Feet of Gross Living Area')
    if not gla:
        gla = _extract_int(text, r'Gross Living Area\s+([\d,]+)')

    # Extract rooms/bedrooms/baths from the "Finished area above grade contains:" line
    # This is more specific to avoid matching comp data
    rooms = _extract_int(text, r'(?:contains|area above grade).*?(\d+)\s+Rooms')
    if not rooms:
        rooms = _extract_int(text, r'(\d+)\s+Rooms')

    bedrooms = _extract_int(text, r'(?:contains|area above grade).*?(\d+)\s+Bedrooms')
    if not bedrooms:
        bedrooms = _extract_int(text, r'(\d+)\s+Bedrooms')

    # More specific pattern for bathrooms to match "6.0 Bath(s)" not comp data
    baths = _extract_number(text, r'(?:contains|area above grade).*?([\d.]+)\s+Bath')
    if not baths:
        baths = _extract_number(text, r'([\d.]+)\s+Bath\(s\)')

    # Extract basement info
    basement_area = _extract_int(text, r'[Bb]asement Area\s+([\d,]+)\s*sq')
    basement_finish = _extract_int(text, r'[Bb]asement Finish\s+(\d+)\s*%')

    # Extract year built
    year_built = _extract_int(text, r'Year Built\s+(\d{4})')
    effective_age = _extract_int(text, r'Effective Age.*?(\d+)')

    # Extract quality and condition
    quality = _extract(text, r'(?:Q\d|quality)[^\n]*?([QC]\d)')
    condition = _extract(text, r'condition[^\n]*?([QC]\d)') or _extract(text, r'([QC]\d);')

    # Extract design style
    design_style = _extract(text, r'Design.*?Style[)\s]+([A-Za-z/]+)')

    return {
        "general": {
            "units": 1,
            "stories": _extract_int(text, r'Stories\s+(\d+)') or _extract_int(text, r'# of Stories\s+(\d+)'),
            "type": "Detached" if re.search(r'Type\s+Det', text) else "",
            "status": "Existing" if re.search(r'Existing', text) else "",
            "design_style": design_style,
            "year_built": year_built,
            "effective_age_years": effective_age,
            "foundation_type": _extract(text, r'Foundation.*?Concrete\s+(\w+)'),
            "basement_area_sqft": basement_area,
            "basement_finish_percent": basement_finish,
            "basement_access": "Outside Entry" if re.search(r'Outside Entry', text) else "",
            "overall_quality": quality,
            "overall_condition": condition,
            "gla_sqft": gla,
            "total_rooms": rooms,
            "bedrooms": bedrooms,
            "bathrooms": baths,
        },
        "exterior": {
            "foundation_walls": _extract(text, r'Foundation Walls\s+(\w+)'),
            "exterior_walls": _extract(text, r'Exterior Walls\s+(\w+)'),
            "roof_surface": _extract(text, r'Roof Surface\s+([A-Za-z\s]+?)\s+[QC]\d'),
            "gutters": _extract(text, r'Gutters.*?(\w+/?\w*)'),
            "window_type": _extract(text, r'Window Type\s+([A-Za-z/]+)'),
            "storm_sash": _extract(text, r'Storm Sash.*?(\w+)'),
            "screens": _extract(text, r'Screens\s+(\w+)'),
        },
        "interior_mechanical": {
            "floors": _extract(text, r'Floors\s+([A-Za-z/]+)'),
            "walls": _extract(text, r'Walls\s+([A-Za-z/]+)'),
            "trim_finish": _extract(text, r'Trim/Finish\s+([A-Za-z/&]+)'),
            "bath_floor": _extract(text, r'Bath Floor\s+([A-Za-z/]+)'),
            "bath_wainscot": _extract(text, r'Bath Wainscot\s+([A-Za-z/]+)'),
            "heating": {
                "type": ["FWA"] if re.search(r'FWA', text) else (
                    ["Radiant"] if re.search(r'Radiant', text) else (
                        ["HWBB"] if re.search(r'HWBB', text) else []
                    )
                ),
                "fuel": _extract(text, r'Fuel\s+([A-Za-z\s]+)'),
            },
            "heating_fuel": _extract(text, r'Fuel\s+([A-Za-z\s]+)'),
            "cooling": "Central" if re.search(r'Central Air', text) else "",
            "fireplaces": {
                "count": _extract_int(text, r'Fireplace.*?#\s*(\d+)'),
                "type": "Electric" if re.search(r'electric fireplace', text, re.IGNORECASE) else "",
            },
            "garage_cars": _extract_int(text, r'Garage.*?#.*?Cars\s+(\d+)'),
            "carport_cars": _extract_int(text, r'Carport.*?#.*?Cars\s+(\d+)'),
            "driveway_surface": _extract(text, r'Driveway Surface\s+(\w+)'),
            "pool": _extract(text, r'Pool\s+(\w+)'),
            "patio_deck": _extract(text, r'Patio/Deck\s+(\w+)'),
            "porch": _extract(text, r'Porch\s+(\w+)'),
            "fence": _extract(text, r'Fence\s+(\w+)'),
            "appliances": {
                "refrigerator": bool(re.search(r'Refrigerator', text, re.IGNORECASE)),
                "range_oven": bool(re.search(r'Range/Oven', text, re.IGNORECASE)),
                "dishwasher": bool(re.search(r'Dishwasher', text, re.IGNORECASE)),
                "disposal": bool(re.search(r'Disposal', text, re.IGNORECASE)),
                "microwave": bool(re.search(r'Microwave', text, re.IGNORECASE)),
                "washer_dryer": bool(re.search(r'Washer/Dryer', text, re.IGNORECASE)),
            },
            # Frontend expects these fields in interior_mechanical
            "gross_living_area_above_grade_sqft": gla,
            "rooms_above_grade": {
                "total_rooms": rooms,
                "bedrooms": bedrooms,
                "bathrooms": baths,
            },
        },
    }


def _parse_sales_comparison(text: str) -> dict:
    """Parse sales comparison info from combined table text."""
    # Extract market stats from header text
    active_count = _extract_int(text, r'(\d+)\s+comparable\s+(?:properties\s+)?currently\s+offered')
    active_low = _extract_number(text, r'currently\s+offered.*?from\s+\$?\s*([\d,]+)')
    active_high = _extract_number(text, r'currently\s+offered.*?to\s+\$?\s*([\d,]+)')

    sales_count = _extract_int(text, r'(\d+)\s+comparable sales')
    sales_low = _extract_number(text, r'comparable sales.*?from\s+\$?\s*([\d,]+)')
    sales_high = _extract_number(text, r'comparable sales.*?to\s+\$?\s*([\d,]+)')

    # Extract subject info - from the sales comparison grid
    # Look for pattern: "Sale Price $ 680,000 $ 419,000 $ 680,000 $ 1,050,000"
    # We need to find all dollar amounts after "Sale Price" on that line
    sale_price_line = re.search(r'Sale Price\s+(.*?)(?:Sale Price/|Data Source)', text, re.DOTALL)
    if sale_price_line:
        sale_prices = re.findall(r'\$\s*([\d,]+)', sale_price_line.group(1))
    else:
        sale_prices = []

    subject_price = float(sale_prices[0].replace(',', '')) if sale_prices else 0

    # GLA from "3,200 sq.ft." in the grid - look for the subject GLA specifically
    # Pattern: "3,200 sq.ft. 1,428 sq.ft. +130,320" (subject then comp1)
    gla_line = re.search(r'(\d[\d,]+)\s+sq\.?ft\.\s+(\d[\d,]+)\s+sq\.?ft\.', text)
    subject_gla = int(gla_line.group(1).replace(',', '')) if gla_line else 0

    # Price per sqft: "$ 188.89sq.ft."
    subject_price_per_sqft = _extract_number(text, r'\$\s*([\d.]+)\s*sq\.?ft\.')

    # Extract comparables from the grid
    # Look for addresses like "57 Walton Ave Montrose, CA"
    comps = []

    # Comp addresses appear after "COMPARABLE SALE # N" or after the subject address
    # Pattern: "Address 1290 W. 29th Montrose, CA 57 Walton Ave Montrose, CA 234 Tanner Dr..."
    comp_addr_match = re.search(
        r'Address\s+[\d\w\s.]+?Montrose.*?'
        r'(\d+\s+\w+\s+(?:Ave|St|Dr|Ln|Rd|Way|Blvd|Ct|Pl)\s+Montrose.*?)'
        r'(\d+\s+\w+\s+(?:Ave|St|Dr|Ln|Rd|Way|Blvd|Ct|Pl)\s+Montrose.*?)?'
        r'(\d+\s+\w+\s+(?:Ave|St|Dr|Ln|Rd|Way|Blvd|Ct|Pl)\s+Montrose.*?)?',
        text, re.DOTALL
    )

    # More robust: find all addresses that look like comparables
    # Format: "57 Walton Ave Montrose, CA"
    all_addresses = re.findall(r'(\d+\s+\w+\s+(?:Ave|St|Dr|Ln|Rd|Way|Blvd|Ct|Pl))\s+Montrose', text)

    # Skip the first one (subject) if it matches the subject address
    subject_addr = _extract(text, r'Property Address\s+([\d\w\s.]+?)\s+City')
    comp_addresses = [a for a in all_addresses if subject_addr.lower() not in a.lower()][:6]

    # Extract proximity patterns: "1.03 miles SE", "0.86 miles SW"
    proximities = re.findall(r'(\d+\.\d+\s+miles?\s+[NSEW]{1,2})', text)

    # Extract sale prices (after subject): positions 1, 2, 3 are comps
    # sale_prices[0] = subject ($680,000), sale_prices[1,2,3] = comps
    comp_prices = [float(p.replace(',', '')) for p in sale_prices[1:4]] if len(sale_prices) > 1 else []

    # Extract adjusted prices from the grid footer
    # Pattern: "Net Adj. 61.3% Gross Adj. 63.0% $ 675,860"
    adjusted_match = re.findall(r'Net Adj\.\s+[\d.-]+%\s+Gross Adj\.\s+[\d.]+%\s+\$\s*([\d,]+)', text)
    adjusted_prices = adjusted_match if adjusted_match else []

    # Extract design/style and condition for each comp
    designs = re.findall(r'Design.*?DT[\d.]+;([A-Za-z/]+)', text)
    conditions = re.findall(r'Condition\s+([QC]\d)', text)

    for i, addr in enumerate(comp_addresses[:3]):  # Max 3 comps on main page
        comp = {
            "id": i + 1,
            "address": addr.strip(),
            "city": "Montrose",
            "state": "CA",
            "proximity": proximities[i] if i < len(proximities) else "",
            "sale_price": comp_prices[i] if i < len(comp_prices) else 0,
            "adjusted_sale_price": float(adjusted_prices[i].replace(',', '')) if i < len(adjusted_prices) else 0,
            "design": designs[i + 1] if i + 1 < len(designs) else "",  # Skip subject design
            "condition": conditions[i + 1] if i + 1 < len(conditions) else "",  # Skip subject condition
        }
        comps.append(comp)

    return {
        "market_stats": {
            "active_listings_count": active_count,
            "active_listings_price_range": {"low": active_low, "high": active_high},
            "sales_12_months_count": sales_count,
            "sales_12_months_price_range": {"low": sales_low, "high": sales_high},
        },
        "subject": {
            "address": subject_addr,
            "city": "Montrose",
            "state": "CA",
            "contract_price": subject_price,
            "price_per_sqft": subject_price_per_sqft,
            "gross_living_area_sqft": subject_gla,
        },
        "comparables": comps,
    }


def _parse_cost_approach(text: str) -> dict:
    """Parse cost approach info from combined table text."""
    # Try multiple patterns for site value
    # Actual text: "OPINION OF SITE VALUE =$ 85,000"
    site_value = _extract_number(text, r'OPINION OF SITE VALUE\s*=?\s*\$?\s*([\d,]+)')
    if not site_value:
        site_value = _extract_number(text, r'Site Value\s*=?\s*\$?\s*([\d,]+)')

    # Try multiple patterns for total cost new
    # Actual text: "Total Estimate of Cost-New =$ 729,071"
    total_cost_new = _extract_number(text, r'Total Estimate of Cost-New\s*=?\s*\$?\s*([\d,]+)')
    if not total_cost_new:
        total_cost_new = _extract_number(text, r'Total.*?Cost.*?New\s*=?\s*\$?\s*([\d,]+)')

    # Depreciation - actual text: "Depreciation 156,240"
    depreciation = _extract_number(text, r'(?:Less\s+)?Physical.*?Depreciation\s+([\d,]+)')
    if not depreciation:
        depreciation = _extract_number(text, r'Depreciation\s+([\d,]+)')

    return {
        "site_value": site_value,
        "improvements_cost_new": {
            "dwelling_gla": {
                "area_sqft": _extract_int(text, r'DWELLING\s+(\d[\d,]+)\s*[Ss]q'),
                "unit_cost": _extract_number(text, r'DWELLING.*?@\s*\$\s*([\d.]+)'),
                "total_cost": _extract_number(text, r'DWELLING.*?=\s*\$?\s*([\d,]+)'),
            },
            "basement": {
                "area_sqft": _extract_int(text, r'Basement\s+(\d[\d,]+)\s*[Ss]q'),
                "unit_cost": _extract_number(text, r'Basement.*?@\s*\$\s*([\d.]+)'),
                "total_cost": _extract_number(text, r'Basement.*?=\s*\$?\s*([\d,]+)'),
            },
            "mechanicals_misc": _extract_number(text, r'Mechanicals/Misc\s*=?\s*\$?\s*([\d,]+)'),
            "garage_carport": {
                "area_sqft": _extract_int(text, r'Garage/Carport\s+(\d+)\s*[Ss]q'),
                "unit_cost": _extract_number(text, r'Garage/Carport.*?@\s*\$\s*([\d.]+)'),
                "total_cost": _extract_number(text, r'Garage/Carport.*?=\s*\$?\s*([\d,]+)'),
            },
        },
        "total_cost_new": total_cost_new,
        "depreciation": depreciation,
        "depreciated_cost_of_improvements": _extract_number(text, r'Depreciated Cost of Improvements\s*=?\s*\$?\s*([\d,]+)'),
        "as_is_site_improvements_value": _extract_number(text, r'"?As-is"?\s+Value of Site Improvements\s*=?\s*\$?\s*([\d,]+)'),
        "indicated_value_by_cost_approach": _extract_number(text, r'INDICATED VALUE BY COST APPROACH\s*=?\s*\$?\s*([\d,]+)'),
        "effective_age_years": _extract_int(text, r'Effective Age\s*=?\s*(\d+)'),
        "remaining_economic_life_years": _extract_int(text, r'(?:Remaining\s+)?Economic Life\s*=?\s*(\d+)'),
        "cost_data_source": _extract(text, r'Source of cost data\s+(.+?)\s+Quality'),
        "comments": "",
    }


def _parse_reconciliation(text: str) -> dict:
    """Parse reconciliation info from combined table text."""
    # Try multiple patterns for final value
    final_value = _extract_number(text, r'(?:Final|Opinion of).*?Market Value\s+\$?\s*([\d,]+)')
    if not final_value:
        final_value = _extract_number(text, r'Indicated Value.*?\$?\s*([\d,]+)')

    return {
        "indicated_value_sales_comparison": _extract_number(text, r'(?:Sales Comparison|Indicated).*?Sales.*?\$?\s*([\d,]+)'),
        "indicated_value_cost_approach": _extract_number(text, r'(?:Cost Approach|Indicated).*?Cost.*?\$?\s*([\d,]+)'),
        "indicated_value_income_approach": None,  # Typically N/A for residential
        "final_market_value": final_value,
        "effective_date_of_appraisal": _extract(text, r'Effective Date.*?(\d{2}/\d{2}/\d{4})'),
        "value_condition": "As Is" if re.search(r'As.Is', text) else "",
        "comments": "",
    }


# =============================================================================
# Main Entry Point
# =============================================================================

def map_appraisal_tables_to_sections(
    tables_path: Path,
    fallback_fields: Optional[dict] = None,
    debug: bool = True,
) -> dict:
    """
    Load extracted tables and map to URAR section structure.

    Args:
        tables_path: Path to the .tables.jsonl file
        fallback_fields: Optional dict from regex extraction to use as fallback
        debug: If True, print debug information about extraction

    Returns:
        Dict with section keys matching AppraisalResources TypeScript interface
    """
    tables_path = Path(tables_path)

    if not tables_path.exists():
        print(f"[WARNING] Tables file not found: {tables_path}")
        return _create_empty_sections()

    # Load tables
    tables = load_tables(tables_path)

    if not tables:
        print(f"[WARNING] No tables loaded from: {tables_path}")
        return _create_empty_sections()

    # Get all text from all tables
    all_text = _get_all_table_text(tables)

    print(f"[DEBUG] Extracted {len(all_text)} chars of text from {len(tables)} tables")

    # Debug: Save full text to file for inspection
    if debug:
        debug_path = tables_path.parent / f"{tables_path.stem}_debug_text.txt"
        with open(debug_path, "w") as f:
            f.write(all_text)
        print(f"[DEBUG] Saved combined text to: {debug_path}")

    # Parse each section from the combined text
    result = {
        "subject": _parse_subject(all_text),
        "listing_and_contract": _parse_listing_and_contract(all_text),
        "neighborhood": _parse_neighborhood(all_text),
        "site": _parse_site(all_text),
        "improvements": _parse_improvements(all_text),
        "sales_comparison": _parse_sales_comparison(all_text),
        "cost_approach": _parse_cost_approach(all_text),
        "reconciliation": _parse_reconciliation(all_text),
        "photos": [],  # Not extracted from tables
        "sketch": {"areas": [], "basement_layout": []},  # Not extracted from tables
    }

    # Debug: Print key extracted values
    if debug:
        print("\n" + "="*60)
        print("[DEBUG] KEY EXTRACTED VALUES:")
        print("="*60)
        print(f"  Subject:")
        print(f"    property_address: {result['subject'].get('property_address', 'NOT FOUND')}")
        print(f"    city: {result['subject'].get('city', 'NOT FOUND')}")
        print(f"    state: {result['subject'].get('state', 'NOT FOUND')}")
        print(f"    borrower: {result['subject'].get('borrower', 'NOT FOUND')}")
        print(f"  Listing/Contract:")
        print(f"    contract_price: {result['listing_and_contract'].get('contract_price', 'NOT FOUND')}")
        print(f"    days_on_market: {result['listing_and_contract'].get('days_on_market', 'NOT FOUND')}")
        print(f"  Improvements:")
        print(f"    year_built: {result['improvements']['general'].get('year_built', 'NOT FOUND')}")
        print(f"    gla_sqft: {result['improvements']['general'].get('gla_sqft', 'NOT FOUND')}")
        print(f"    bedrooms: {result['improvements']['general'].get('bedrooms', 'NOT FOUND')}")
        print(f"    bathrooms: {result['improvements']['general'].get('bathrooms', 'NOT FOUND')}")
        print(f"  Cost Approach:")
        print(f"    site_value: {result['cost_approach'].get('site_value', 'NOT FOUND')}")
        print(f"    total_cost_new: {result['cost_approach'].get('total_cost_new', 'NOT FOUND')}")
        print(f"    depreciation: {result['cost_approach'].get('depreciation', 'NOT FOUND')}")
        print(f"    indicated_value: {result['cost_approach'].get('indicated_value_by_cost_approach', 'NOT FOUND')}")
        print(f"  Sales Comparison:")
        print(f"    comparables count: {len(result['sales_comparison'].get('comparables', []))}")
        print(f"  Reconciliation:")
        print(f"    final_market_value: {result['reconciliation'].get('final_market_value', 'NOT FOUND')}")
        print("="*60 + "\n")

    # Apply fallback from regex extraction if provided
    if fallback_fields:
        result = _apply_fallback(result, fallback_fields)

    return result


def _create_empty_sections() -> dict:
    """Create empty section structure matching frontend types."""
    return {
        "subject": _parse_subject(""),
        "listing_and_contract": _parse_listing_and_contract(""),
        "neighborhood": _parse_neighborhood(""),
        "site": _parse_site(""),
        "improvements": _parse_improvements(""),
        "sales_comparison": _parse_sales_comparison(""),
        "cost_approach": _parse_cost_approach(""),
        "reconciliation": _parse_reconciliation(""),
        "photos": [],
        "sketch": {"areas": [], "basement_layout": []},
    }


def _apply_fallback(sections: dict, fields: dict) -> dict:
    """
    Apply fallback values from regex extraction where section mapping is empty.

    Args:
        sections: Mapped sections from table extraction
        fields: Flat dict from AppraisalFields.to_dict()

    Returns:
        Sections with fallback values applied
    """
    # Subject fallbacks
    if not sections["subject"].get("property_address") and fields.get("property_address"):
        sections["subject"]["property_address"] = fields["property_address"]
    if not sections["subject"].get("city") and fields.get("city"):
        sections["subject"]["city"] = fields["city"]
    if not sections["subject"].get("state") and fields.get("state"):
        sections["subject"]["state"] = fields["state"]
    if not sections["subject"].get("zip") and fields.get("zip_code"):
        sections["subject"]["zip"] = fields["zip_code"]
    if not sections["subject"].get("county") and fields.get("county"):
        sections["subject"]["county"] = fields["county"]
    if not sections["subject"].get("borrower") and fields.get("borrower"):
        sections["subject"]["borrower"] = fields["borrower"]

    # Listing fallbacks
    if not sections["listing_and_contract"].get("contract_price") and fields.get("total_value"):
        sections["listing_and_contract"]["contract_price"] = fields["total_value"]

    # Site fallbacks
    if not sections["site"].get("area_acres") and fields.get("land_area_acres"):
        sections["site"]["area_acres"] = fields["land_area_acres"]

    # Improvements fallbacks
    general = sections["improvements"]["general"]
    if not general.get("year_built") and fields.get("year_built"):
        general["year_built"] = fields["year_built"]
    if not general.get("effective_age_years") and fields.get("effective_age_years"):
        general["effective_age_years"] = fields["effective_age_years"]
    if not general.get("overall_quality") and fields.get("quality_rating"):
        general["overall_quality"] = fields["quality_rating"]
    if not general.get("overall_condition") and fields.get("condition_rating"):
        general["overall_condition"] = fields["condition_rating"]
    if not general.get("gla_sqft") and fields.get("gross_living_area"):
        general["gla_sqft"] = fields["gross_living_area"]
    if not general.get("bedrooms") and fields.get("bedroom_count"):
        general["bedrooms"] = fields["bedroom_count"]
    if not general.get("bathrooms") and fields.get("bathroom_count"):
        general["bathrooms"] = fields["bathroom_count"]

    # Cost approach fallbacks
    if not sections["cost_approach"].get("site_value") and fields.get("land_value"):
        sections["cost_approach"]["site_value"] = fields["land_value"]
    if not sections["cost_approach"].get("indicated_value_by_cost_approach") and fields.get("building_value"):
        sections["cost_approach"]["indicated_value_by_cost_approach"] = (
            (fields.get("land_value") or 0) + (fields.get("building_value") or 0)
        )

    # Reconciliation fallbacks
    if not sections["reconciliation"].get("final_market_value") and fields.get("total_value"):
        sections["reconciliation"]["final_market_value"] = fields["total_value"]

    return sections
