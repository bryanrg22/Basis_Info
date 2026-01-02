"""
Field extraction from appraisal PDFs.

Extracts key fields that constrain cost segregation analysis:
- Total building value
- Land value
- Building dimensions
- Property type and use
- Effective age and remaining life
- Construction quality
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber


@dataclass
class AppraisalFields:
    """Extracted fields from an appraisal document."""

    # Identification
    doc_id: str
    property_address: Optional[str] = None
    appraisal_date: Optional[str] = None
    effective_date: Optional[str] = None

    # Value breakdown
    total_value: Optional[float] = None
    land_value: Optional[float] = None
    building_value: Optional[float] = None
    site_improvements_value: Optional[float] = None
    personal_property_value: Optional[float] = None

    # Building characteristics
    building_type: Optional[str] = None
    property_use: Optional[str] = None
    construction_class: Optional[str] = None
    quality_rating: Optional[str] = None
    condition_rating: Optional[str] = None

    # Dimensions
    gross_building_area_sf: Optional[float] = None
    net_leasable_area_sf: Optional[float] = None
    land_area_sf: Optional[float] = None
    land_area_acres: Optional[float] = None
    num_floors: Optional[int] = None
    num_units: Optional[int] = None

    # Age and life
    year_built: Optional[int] = None
    effective_age_years: Optional[int] = None
    remaining_economic_life_years: Optional[int] = None
    total_economic_life_years: Optional[int] = None

    # Location
    city: Optional[str] = None
    state: Optional[str] = None
    county: Optional[str] = None
    zip_code: Optional[str] = None
    neighborhood: Optional[str] = None

    # Marshall & Swift / RSMeans factors
    location_factor: Optional[float] = None
    current_cost_multiplier: Optional[float] = None

    # Confidence tracking
    fields_extracted: int = 0
    fields_total: int = 25
    extraction_notes: list[str] = None

    def __post_init__(self):
        if self.extraction_notes is None:
            self.extraction_notes = []
        self._count_extracted()

    def _count_extracted(self):
        """Count non-None fields."""
        count = 0
        for field in [
            self.property_address,
            self.appraisal_date,
            self.total_value,
            self.land_value,
            self.building_value,
            self.building_type,
            self.property_use,
            self.gross_building_area_sf,
            self.land_area_sf,
            self.year_built,
            self.effective_age_years,
            self.remaining_economic_life_years,
            self.city,
            self.state,
        ]:
            if field is not None:
                count += 1
        self.fields_extracted = count

    @property
    def extraction_confidence(self) -> float:
        """Confidence based on fields extracted."""
        return self.fields_extracted / self.fields_total

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "doc_id": self.doc_id,
            "property_address": self.property_address,
            "appraisal_date": self.appraisal_date,
            "effective_date": self.effective_date,
            "total_value": self.total_value,
            "land_value": self.land_value,
            "building_value": self.building_value,
            "site_improvements_value": self.site_improvements_value,
            "personal_property_value": self.personal_property_value,
            "building_type": self.building_type,
            "property_use": self.property_use,
            "construction_class": self.construction_class,
            "quality_rating": self.quality_rating,
            "condition_rating": self.condition_rating,
            "gross_building_area_sf": self.gross_building_area_sf,
            "net_leasable_area_sf": self.net_leasable_area_sf,
            "land_area_sf": self.land_area_sf,
            "land_area_acres": self.land_area_acres,
            "num_floors": self.num_floors,
            "num_units": self.num_units,
            "year_built": self.year_built,
            "effective_age_years": self.effective_age_years,
            "remaining_economic_life_years": self.remaining_economic_life_years,
            "total_economic_life_years": self.total_economic_life_years,
            "city": self.city,
            "state": self.state,
            "county": self.county,
            "zip_code": self.zip_code,
            "neighborhood": self.neighborhood,
            "location_factor": self.location_factor,
            "current_cost_multiplier": self.current_cost_multiplier,
            "fields_extracted": self.fields_extracted,
            "extraction_confidence": self.extraction_confidence,
            "extraction_notes": self.extraction_notes,
        }


def extract_appraisal_fields(
    pdf_path: Path,
    doc_id: str,
    max_pages: int = 30,
) -> AppraisalFields:
    """
    Extract key fields from an appraisal PDF.

    Uses regex patterns to find common appraisal data points.
    Searches first N pages where summary info typically appears.

    Args:
        pdf_path: Path to appraisal PDF
        doc_id: Document identifier
        max_pages: Maximum pages to search (default 30)

    Returns:
        AppraisalFields with extracted values
    """
    pdf_path = Path(pdf_path)
    fields = AppraisalFields(doc_id=doc_id)

    with pdfplumber.open(pdf_path) as pdf:
        # Combine text from relevant pages
        text = ""
        for i, page in enumerate(pdf.pages[:max_pages]):
            page_text = page.extract_text() or ""
            text += page_text + "\n"

        # Extract values using patterns
        fields.property_address = _extract_address(text)
        fields.appraisal_date = _extract_date(text, "appraisal")
        fields.effective_date = _extract_date(text, "effective")

        # Value extraction
        fields.total_value = _extract_value(text, ["total value", "appraised value", "market value"])
        fields.land_value = _extract_value(text, ["land value", "site value"])
        fields.building_value = _extract_value(text, ["building value", "improvement value"])
        fields.site_improvements_value = _extract_value(text, ["site improvement", "land improvement"])

        # Building characteristics
        fields.building_type = _extract_building_type(text)
        fields.property_use = _extract_property_use(text)
        fields.construction_class = _extract_construction_class(text)
        fields.quality_rating = _extract_quality(text)
        fields.condition_rating = _extract_condition(text)

        # Dimensions
        fields.gross_building_area_sf = _extract_area(text, ["gross building area", "gba", "gross area"])
        fields.net_leasable_area_sf = _extract_area(text, ["net leasable", "nla", "rentable area"])
        fields.land_area_sf = _extract_area(text, ["land area", "site area", "lot size"])
        fields.land_area_acres = _extract_acres(text)
        fields.num_floors = _extract_floors(text)
        fields.num_units = _extract_units(text)

        # Age
        fields.year_built = _extract_year_built(text)
        fields.effective_age_years = _extract_age(text, "effective age")
        fields.remaining_economic_life_years = _extract_age(text, "remaining economic life")
        fields.total_economic_life_years = _extract_age(text, "total economic life")

        # Location
        location = _extract_location(text)
        fields.city = location.get("city")
        fields.state = location.get("state")
        fields.county = location.get("county")
        fields.zip_code = location.get("zip")

        # Factors
        fields.location_factor = _extract_factor(text, ["location factor", "local multiplier"])
        fields.current_cost_multiplier = _extract_factor(text, ["current cost", "time multiplier"])

    # Recount extracted fields
    fields._count_extracted()

    return fields


def _extract_address(text: str) -> Optional[str]:
    """Extract property address."""
    patterns = [
        r"(?:property address|subject property|location)[:\s]+([^\n]{10,100})",
        r"(\d+\s+[\w\s]+(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd)[^\n]{0,50})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_date(text: str, date_type: str) -> Optional[str]:
    """Extract a date."""
    patterns = [
        rf"{date_type}\s+date[:\s]+(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})",
        rf"{date_type}\s+date[:\s]+(\w+\s+\d{{1,2}},?\s+\d{{4}})",
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_value(text: str, keywords: list[str]) -> Optional[float]:
    """Extract a dollar value."""
    for keyword in keywords:
        pattern = rf"{keyword}[:\s]+\$?([\d,]+(?:\.\d{{2}})?)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _extract_area(text: str, keywords: list[str]) -> Optional[float]:
    """Extract an area in square feet."""
    for keyword in keywords:
        pattern = rf"{keyword}[:\s]+([\d,]+)\s*(?:sf|sq\.?\s*ft|square feet)?"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _extract_acres(text: str) -> Optional[float]:
    """Extract land area in acres."""
    patterns = [
        r"([\d.]+)\s*(?:acres?|ac)",
        r"land.*?([\d.]+)\s*(?:acres?|ac)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _extract_floors(text: str) -> Optional[int]:
    """Extract number of floors/stories."""
    patterns = [
        r"(\d+)\s*(?:floor|stor(?:y|ies))",
        r"(?:floors?|stories?)[:\s]+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _extract_units(text: str) -> Optional[int]:
    """Extract number of units."""
    patterns = [
        r"(\d+)\s*(?:unit|apartment|suite)s?",
        r"(?:units?|apartments?)[:\s]+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _extract_year_built(text: str) -> Optional[int]:
    """Extract year built."""
    patterns = [
        r"year\s+built[:\s]+(\d{4})",
        r"built\s+in\s+(\d{4})",
        r"constructed\s+(?:in\s+)?(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                year = int(match.group(1))
                if 1800 <= year <= 2030:
                    return year
            except ValueError:
                continue
    return None


def _extract_age(text: str, keyword: str) -> Optional[int]:
    """Extract an age in years."""
    pattern = rf"{keyword}[:\s]+(\d+)\s*(?:years?)?"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _extract_building_type(text: str) -> Optional[str]:
    """Extract building type."""
    types = [
        "apartment",
        "office",
        "retail",
        "industrial",
        "warehouse",
        "residential",
        "multifamily",
        "single family",
        "commercial",
        "mixed use",
        "hotel",
        "motel",
        "restaurant",
        "medical",
        "hospital",
        "school",
        "church",
        "warehouse",
    ]
    text_lower = text.lower()
    for btype in types:
        if btype in text_lower:
            return btype.title()
    return None


def _extract_property_use(text: str) -> Optional[str]:
    """Extract property use classification."""
    uses = [
        "residential rental",
        "commercial",
        "industrial",
        "retail",
        "office",
        "multifamily",
        "single family",
        "mixed use",
    ]
    text_lower = text.lower()
    for use in uses:
        if use in text_lower:
            return use.title()
    return None


def _extract_construction_class(text: str) -> Optional[str]:
    """Extract construction class (A, B, C, D, or S)."""
    patterns = [
        r"(?:construction\s+)?class[:\s]+([A-DS])\b",
        r"class\s+([A-DS])\s+construction",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def _extract_quality(text: str) -> Optional[str]:
    """Extract quality rating."""
    qualities = [
        "excellent",
        "very good",
        "good",
        "average",
        "fair",
        "poor",
    ]
    patterns = [
        r"quality[:\s]+(excellent|very good|good|average|fair|poor)",
        r"(excellent|very good|good|average|fair|poor)\s+quality",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).title()
    return None


def _extract_condition(text: str) -> Optional[str]:
    """Extract condition rating."""
    patterns = [
        r"condition[:\s]+(excellent|very good|good|average|fair|poor)",
        r"(excellent|very good|good|average|fair|poor)\s+condition",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).title()
    return None


def _extract_location(text: str) -> dict:
    """Extract location components."""
    result = {}

    # State abbreviation
    state_match = re.search(r"\b([A-Z]{2})\s+\d{5}", text)
    if state_match:
        result["state"] = state_match.group(1)

    # ZIP code
    zip_match = re.search(r"\b(\d{5}(?:-\d{4})?)\b", text)
    if zip_match:
        result["zip"] = zip_match.group(1)

    # County
    county_match = re.search(r"(\w+)\s+county", text, re.IGNORECASE)
    if county_match:
        result["county"] = county_match.group(1).title()

    # City - harder to extract reliably
    city_patterns = [
        r"(?:city|location)[:\s]+(\w+(?:\s+\w+)?)",
        r"(\w+(?:\s+\w+)?),\s*[A-Z]{2}\s+\d{5}",
    ]
    for pattern in city_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["city"] = match.group(1).title()
            break

    return result


def _extract_factor(text: str, keywords: list[str]) -> Optional[float]:
    """Extract a multiplier factor."""
    for keyword in keywords:
        pattern = rf"{keyword}[:\s]+([\d.]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                factor = float(match.group(1))
                if 0.5 <= factor <= 2.0:
                    return factor
            except ValueError:
                continue
    return None


def save_fields(fields: AppraisalFields, output_path: Path) -> None:
    """Save extracted fields to JSON."""
    import json

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(fields.to_dict(), f, indent=2)


def load_fields(input_path: Path) -> AppraisalFields:
    """Load fields from JSON."""
    import json

    with open(input_path) as f:
        data = json.load(f)

    return AppraisalFields(**data)
