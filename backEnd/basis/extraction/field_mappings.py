"""The URAR field catalogue: keys, printed labels, types, and the critical fields.

Migrated from ``evidence_layer/src/tiered_extraction/field_mappings.py`` with one rule
that fixes legacy A.8: every field is named once, as ``"<section>.<field>"``, and every
consumer (Azure DI key mapping, regex extraction, cleaners, validation, the confidence
gate, the LLM fallback) looks fields up by that key. The legacy module kept
``CRITICAL_FIELDS`` as bare names (``gross_living_area``, ``effective_date``,
``appraised_value``) while its mapping tables produced ``gla_sqft``,
``effective_date_of_appraisal`` and ``final_market_value``, so two critical fields always
read as missing and every extraction went to review. Here :data:`CRITICAL_FIELDS` is
derived from the catalogue itself (``critical=True``), so it cannot name a field that
nothing produces; ``tests/unit/extraction/test_field_mappings.py`` asserts it.

Labels are the URAR 1004 captions as printed (and as Azure DI returns them as keys);
``aliases`` are other captions seen for the same field. Matching is exact after
:func:`normalize_label` (case, punctuation, whitespace), scoped by section where a caption
is ambiguous ("Effective Date" in Reconciliation vs. "Effective Date of Appraisal" in the
certification). The legacy substring "fuzzy" table (``"address"`` -> property address,
``"design"`` -> style, ``"client"`` -> lender) is not carried over: a substring match
assigned unrelated captions to critical fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

FieldType = Literal[
    "text", "int", "float", "currency", "currency_or_text", "percent", "date", "bool"
]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    label: str
    type: FieldType = "text"
    aliases: tuple[str, ...] = ()
    critical: bool = False
    # False for a box whose caption is not unique on the form ("Low", "Sq.Ft." in a table
    # row): Azure DI pairs and the regex pass never map to it, only the model fallback
    # (which is given the label and the key) fills it.
    captioned: bool = True

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def field(self) -> str:
        return self.key.split(".", 1)[1]

    @property
    def labels(self) -> tuple[str, ...]:
        return (self.label, *self.aliases)


def F(  # noqa: N802 - a short constructor keeps the table readable
    key: str,
    label: str,
    type: FieldType = "text",
    *aliases: str,
    critical: bool = False,
    captioned: bool = True,
) -> FieldSpec:
    return FieldSpec(key, label, type, tuple(aliases), critical, captioned)


def M(key: str, label: str, type: FieldType = "text") -> FieldSpec:  # noqa: N802
    """A box only the model fallback reads (``captioned=False``)."""
    return FieldSpec(key, label, type, (), False, False)


# URAR sections in form order; the heading captions that open each one.
SECTIONS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("subject", ("Subject",)),
    ("listing_and_contract", ("Contract", "Listing and Contract")),
    ("neighborhood", ("Neighborhood",)),
    ("site", ("Site",)),
    ("improvements", ("Improvements",)),
    (
        "sales_comparison",
        ("Sales Comparison Approach", "Sales Comparison Approach (continued)"),
    ),
    ("reconciliation", ("Reconciliation",)),
    ("additional_comments", ("Additional Comments",)),
    ("cost_approach", ("Cost Approach", "Cost Approach to Value")),
    ("income_approach", ("Income Approach", "Income Approach to Value")),
    (
        "appraiser",
        ("Appraiser Certification and Signature", "Appraiser's Certification", "Signatures"),
    ),
)
SECTION_NAMES: Final = tuple(name for name, _ in SECTIONS)

FIELDS: Final[tuple[FieldSpec, ...]] = (
    # Subject
    F("subject.property_address", "Property Address", "text", "Subject Address", critical=True),
    F("subject.city", "City"),
    F("subject.state", "State"),
    F("subject.zip", "Zip Code", "text", "Zip"),
    F("subject.borrower", "Borrower"),
    F("subject.owner_of_public_record", "Owner of Public Record"),
    F("subject.county", "County"),
    F("subject.legal_description", "Legal Description"),
    F("subject.assessors_parcel", "Assessor's Parcel #", "text", "Assessor's Parcel No"),
    F("subject.tax_year", "Tax Year", "int"),
    F("subject.real_estate_taxes", "R.E. Taxes", "currency", "R.E. Taxes $", "Real Estate Taxes"),
    F("subject.neighborhood_name", "Neighborhood Name"),
    F("subject.map_reference", "Map Reference"),
    F("subject.census_tract", "Census Tract"),
    F("subject.occupant", "Occupant"),
    F("subject.special_assessments", "Special Assessments", "currency", "Special Assessments $"),
    F("subject.hoa_dues", "HOA Dues", "currency", "HOA $"),
    F("subject.property_rights_appraised", "Property Rights Appraised"),
    F("subject.assignment_type", "Assignment Type"),
    F("subject.lender_client", "Lender/Client"),
    # Listing and contract
    F(
        "listing_and_contract.contract_price",
        "Contract Price",
        "currency",
        "Contract Price $",
        critical=True,
    ),
    F("listing_and_contract.contract_date", "Date of Contract", "date", "Contract Date"),
    F("listing_and_contract.offering_price", "Offering Price", "currency"),
    F("listing_and_contract.listing_date", "Listing Date", "date"),
    F("listing_and_contract.days_on_market", "DOM", "int", "Days on Market"),
    F("listing_and_contract.sale_type", "Sale Type"),
    F(
        "listing_and_contract.seller_concessions",
        "Financial Assistance (Concessions)",
        "currency",
        "Seller Concessions",
    ),
    F("listing_and_contract.data_source", "Data Source(s)"),
    # Neighborhood
    F("neighborhood.location", "Location"),
    F("neighborhood.built_up", "Built-Up"),
    F("neighborhood.growth", "Growth"),
    F("neighborhood.property_values", "Property Values"),
    F("neighborhood.demand_supply", "Demand/Supply"),
    F("neighborhood.marketing_time", "Marketing Time"),
    F("neighborhood.present_land_use_one_unit", "One-Unit", "percent"),
    F("neighborhood.present_land_use_two_to_four", "2-4 Unit", "percent"),
    F("neighborhood.present_land_use_multi_family", "Multi-Family", "percent"),
    F("neighborhood.present_land_use_commercial", "Commercial", "percent"),
    F("neighborhood.present_land_use_other", "Other Land Use", "percent"),
    F("neighborhood.neighborhood_boundaries", "Neighborhood Boundaries"),
    # One-unit housing price ($000) and age (yrs) ranges (EVAL_REPORT section 27).
    M("neighborhood.price_low_thousands", "One-Unit Housing PRICE $(000) Low", "int"),
    M("neighborhood.price_high_thousands", "One-Unit Housing PRICE $(000) High", "int"),
    M("neighborhood.price_predominant_thousands", "One-Unit Housing PRICE $(000) Pred.", "int"),
    M("neighborhood.age_low", "One-Unit Housing AGE (yrs) Low", "int"),
    M("neighborhood.age_high", "One-Unit Housing AGE (yrs) High", "int"),
    M("neighborhood.age_predominant", "One-Unit Housing AGE (yrs) Pred.", "int"),
    # Site
    F("site.dimensions", "Dimensions"),
    F("site.area_sqft", "Site Area", "int", "Area", "Lot Size"),
    # The Area box as printed ("1.39 ac") and in acres when printed so; derived from the
    # value read for site.area_sqft (basis.extraction.derived), never read on their own.
    M("site.area", "Site Area (as printed, with its unit)"),
    M("site.area_acres", "Site Area in acres", "float"),
    F("site.shape", "Shape"),
    F("site.view", "View"),
    F(
        "site.zoning_classification",
        "Zoning Classification",
        "text",
        "Specific Zoning Classification",
    ),
    F("site.zoning_description", "Zoning Description"),
    F("site.zoning_compliance", "Zoning Compliance"),
    F("site.highest_and_best_use", "Highest and Best Use"),
    F("site.utilities_electric", "Electricity"),
    F("site.utilities_gas", "Gas"),
    F("site.utilities_water", "Water"),
    F("site.utilities_sewer", "Sanitary Sewer"),
    F("site.off_site_improvements_street", "Street"),
    F("site.off_site_improvements_alley", "Alley"),
    F("site.special_flood_hazard", "FEMA Special Flood Hazard Area", "bool"),
    F("site.flood_zone", "FEMA Flood Zone"),
    F("site.fema_map_number", "FEMA Map #"),
    F("site.fema_map_date", "FEMA Map Date", "date"),
    # Improvements
    F("improvements.units", "Units"),
    F("improvements.stories", "Stories", "int", "# of Stories"),
    F("improvements.type", "Type"),
    F("improvements.design_style", "Design (Style)"),
    F("improvements.year_built", "Year Built", "int", critical=True),
    F("improvements.effective_age_years", "Effective Age", "int", "Effective Age (Yrs)"),
    F("improvements.foundation_type", "Foundation"),
    # Exterior and interior description, "materials/condition" columns (the C1-C6
    # condition rating printed after the material is not part of the value).
    F("improvements.foundation_walls", "Foundation Walls"),
    F("improvements.floors", "Floors"),
    F("improvements.walls", "Walls"),
    F("improvements.basement_area_sqft", "Basement Area", "int"),
    F("improvements.basement_finish_percent", "Basement Finish", "percent"),
    F("improvements.exterior_walls", "Exterior Walls"),
    F("improvements.roof_surface", "Roof Surface"),
    F("improvements.gutters_downspouts", "Gutters and Downspouts", "text", "Gutters & Downspouts"),
    F("improvements.window_type", "Window Type"),
    F("improvements.heating_type", "Heating"),
    F("improvements.heating_fuel", "Fuel"),
    F("improvements.cooling", "Cooling"),
    F("improvements.fireplaces", "Fireplaces", "int", "Fireplace(s) #"),
    F("improvements.patio_deck", "Patio/Deck"),
    F("improvements.car_storage", "Car Storage"),
    F("improvements.garage_cars", "Garage Cars", "int", "Garage # of Cars"),
    F("improvements.total_rooms", "Rooms", "int", critical=True),
    F("improvements.bedrooms", "Bedrooms", "int", critical=True),
    F("improvements.bathrooms", "Bath(s)", "float", critical=True),
    F(
        "improvements.gla_sqft",
        "Gross Living Area",
        "int",
        "Square Feet of Gross Living Area Above Grade",
        critical=True,
    ),
    F("improvements.quality", "Quality of Construction"),
    F("improvements.condition", "Condition"),
    F("improvements.appliances", "Appliances"),
    F("improvements.additional_features", "Additional Features"),
    # Sales comparison
    F(
        "sales_comparison.indicated_value",
        "Indicated Value by Sales Comparison Approach",
        "currency_or_text",
    ),
    # Reconciliation
    F(
        "reconciliation.indicated_value_sales_comparison",
        "Indicated Value by Sales Comparison",
        "currency_or_text",
    ),
    F(
        "reconciliation.indicated_value_cost_approach",
        "Indicated Value by Cost Approach",
        "currency_or_text",
    ),
    F(
        "reconciliation.indicated_value_income_approach",
        "Indicated Value by Income Approach",
        "currency_or_text",
    ),
    F("reconciliation.appraisal_made", "This Appraisal Is Made"),
    F(
        "reconciliation.final_market_value",
        "Final Opinion of Market Value",
        "currency",
        "Opinion of Market Value",
        critical=True,
    ),
    F(
        "reconciliation.effective_date_of_appraisal",
        "Effective Date",
        "date",
        "Effective Date of Value",
        critical=True,
    ),
    # Cost approach (the table rows are matched by caption, the value is the last amount)
    F("cost_approach.site_value", "Opinion of Site Value", "currency", "Site Value"),
    F("cost_approach.dwelling_cost", "Dwelling", "currency"),
    # The quantity rows "<item> ___ Sq.Ft. @ $___ = $___" (EVAL_REPORT section 27).
    M("cost_approach.dwelling_sqft", "Cost Approach Dwelling Sq.Ft.", "int"),
    M(
        "cost_approach.dwelling_cost_per_sqft",
        "Cost Approach Dwelling cost per Sq.Ft. (@ $)",
        "float",
    ),
    M("cost_approach.basement_sqft", "Cost Approach Basement Sq.Ft.", "int"),
    M(
        "cost_approach.basement_cost_per_sqft",
        "Cost Approach Basement cost per Sq.Ft. (@ $)",
        "float",
    ),
    M("cost_approach.basement_cost", "Cost Approach Basement cost (= $)", "currency"),
    M("cost_approach.garage_sqft", "Cost Approach Garage/Carport Sq.Ft.", "int"),
    M(
        "cost_approach.garage_cost_per_sqft",
        "Cost Approach Garage/Carport cost per Sq.Ft. (@ $)",
        "float",
    ),
    M("cost_approach.garage_cost", "Cost Approach Garage/Carport cost (= $)", "currency"),
    M(
        "cost_approach.mechanicals_misc",
        "Cost Approach Mechanicals/Misc. row cost (= $)",
        "currency",
    ),
    M("cost_approach.quality_rating_from_cost_service", "Quality rating from cost service"),
    M("cost_approach.effective_date_of_cost_data", "Effective date of cost data"),
    F("cost_approach.total_cost_new", "Total Estimate of Cost-New", "currency", "Total Cost New"),
    F("cost_approach.depreciation_physical", "Less Physical Depreciation", "currency"),
    F("cost_approach.depreciation_functional", "Less Functional Depreciation", "currency"),
    F("cost_approach.depreciation_external", "Less External Depreciation", "currency"),
    F("cost_approach.depreciation", "Total Depreciation", "currency", "Depreciation"),
    F(
        "cost_approach.depreciated_cost_improvements",
        "Depreciated Cost of Improvements",
        "currency",
    ),
    F(
        "cost_approach.site_improvements_value",
        '"As-is" Value of Site Improvements',
        "currency",
        "As-is Value of Site Improvements",
    ),
    F("cost_approach.indicated_value", "Indicated Value by Cost Approach", "currency_or_text"),
    F("cost_approach.remaining_economic_life", "Remaining Economic Life", "int"),
    F("cost_approach.cost_data_source", "Source of Cost Data"),
    # Appraiser certification
    F("appraiser.name", "Appraiser", "text", "Appraiser Name"),
    F("appraiser.company", "Company Name"),
    F("appraiser.license_number", "State Certification #", "text", "State License #"),
    F("appraiser.license_state", "License State", "text", "State"),
    F("appraiser.signature_date", "Date of Signature and Report", "date"),
    F("appraiser.effective_date", "Effective Date of Appraisal", "date"),
    F("appraiser.appraised_value", "Appraised Value of Subject Property", "currency"),
)

FIELD_BY_KEY: Final[dict[str, FieldSpec]] = {spec.key: spec for spec in FIELDS}
CRITICAL_FIELDS: Final[tuple[str, ...]] = tuple(spec.key for spec in FIELDS if spec.critical)

# Boxes the legacy 1004 prints as checkbox groups (the form, not the scorer's vocabulary:
# tests check that every scored enum key is here). One option is checked in a single group;
# several may be in a multi group.
SINGLE_CHECKBOX_KEYS: Final = frozenset(
    {
        "subject.occupant",
        "subject.property_rights_appraised",
        "subject.assignment_type",
        "neighborhood.location",
        "neighborhood.built_up",
        "neighborhood.growth",
        "neighborhood.property_values",
        "neighborhood.demand_supply",
        "neighborhood.marketing_time",
        "site.zoning_compliance",
        "site.utilities_electric",
        "site.utilities_gas",
        "site.utilities_water",
        "site.utilities_sewer",
        "site.off_site_improvements_street",
        "site.off_site_improvements_alley",
        "site.special_flood_hazard",
        "improvements.units",
        "improvements.type",
        "reconciliation.appraisal_made",
    }
)
MULTI_CHECKBOX_KEYS: Final = frozenset(
    {
        "improvements.foundation_type",
        "improvements.heating_type",
        "improvements.cooling",
        "improvements.appliances",
        "improvements.car_storage",
    }
)
CHECKBOX_KEYS: Final = SINGLE_CHECKBOX_KEYS | MULTI_CHECKBOX_KEYS

# Rows of the cost approach table: caption -> key. Values are the row's last amount.
COST_TABLE_ROWS: Final[tuple[str, ...]] = tuple(
    spec.key
    for spec in FIELDS
    if spec.section == "cost_approach"
    and spec.captioned
    and spec.key not in ("cost_approach.remaining_economic_life", "cost_approach.cost_data_source")
)

# Confidence a source can claim before calibration (legacy SOURCE_CONFIDENCE ranges).
# Azure DI reports its own per-pair confidence; a regex match on a born-digital text layer
# is capped at REGEX_CONFIDENCE (below the 0.90 review gate on purpose: without Azure DI
# a person checks the critical fields); an LLM fallback value is capped at 0.6 (S3_SPEC 4).
REGEX_CONFIDENCE: Final = 0.80
TABLE_CONFIDENCE: Final = 0.80
LLM_CONFIDENCE_CAP: Final = 0.60

_PUNCT = re.compile(r"[\s:;.,]+$")
_SPACES = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def normalize_label(text: str) -> str:
    """Case-, whitespace- and trailing-punctuation-insensitive form of a caption."""
    folded = text.translate(_QUOTES).strip().lower()
    folded = _PUNCT.sub("", folded)
    return _SPACES.sub(" ", folded)


def normalize_heading(text: str) -> str:
    return normalize_label(text)


SECTION_BY_HEADING: Final[dict[str, str]] = {
    normalize_heading(heading): name for name, headings in SECTIONS for heading in headings
}


def _label_index() -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    scoped: dict[tuple[str, str], str] = {}
    by_label: dict[str, set[str]] = {}
    for spec in FIELDS:
        if not spec.captioned:
            continue
        for label in spec.labels:
            norm = normalize_label(label)
            scoped.setdefault((spec.section, norm), spec.key)
            by_label.setdefault(norm, set()).add(spec.key)
    unique = {label: next(iter(keys)) for label, keys in by_label.items() if len(keys) == 1}
    return scoped, unique


_SCOPED, _UNIQUE = _label_index()


def all_labels() -> list[str]:
    """Every caption and alias, longest first (for building the regex alternation)."""
    labels = {label for spec in FIELDS if spec.captioned for label in spec.labels}
    return sorted(labels, key=lambda label: (-len(label), label))


def key_for_label(label: str, section: str | None) -> str | None:
    """The field a caption names in ``section`` (or anywhere, when it is unambiguous)."""
    norm = normalize_label(label)
    if section is not None:
        scoped = _SCOPED.get((section, norm))
        if scoped is not None:
            return scoped
    return _UNIQUE.get(norm)


def section_for_heading(line: str) -> str | None:
    """The section a line opens when the whole line is a section heading."""
    return SECTION_BY_HEADING.get(normalize_heading(line))
