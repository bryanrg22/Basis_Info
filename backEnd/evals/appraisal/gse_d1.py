"""Truth set for the GSE UAD 3.6 sample appraisals (Appendix D-1), built from their MISMO XML.

Each D-1 scenario ships a MISMO 3.6 XML file and the PDF of the redesigned URAR it renders.
The XML is the answer key; the extractor only sees the PDF. This module reads the XML
through an explicit element map (:data:`XPATHS`), converts each value to the catalogue key
it corresponds to (:data:`basis.extraction.field_mappings.FIELDS`, legacy-1004 shaped) in the
SPEC section 4 formats, and finds where the PDF prints it (page, the characters printed, a
quote) so the truth also scores page citations. The cover section of every sample ("not
part of the URAR") is skipped.

Statuses (SPEC section 1): a key whose concept the UAD 3.6 URAR does not print at all is
``absent`` ("not on the UAD 3.6 URAR", :data:`NOT_ON_UAD36`); keys of an approach the XML
marks as not developed (``CostApproachIndicator`` / ``IncomeApproachIndicator`` false) are
``not_developed``; a single-unit key on a multi-unit property, or a key whose box the
redesigned form splits into several, is ``unscorable`` with the reason.

``verified`` is true only when the value comes straight from the XML and the PDF prints it
on the chosen page next to its caption, in the section the key belongs to. Derived values
(a sum of two printed boxes), mapping judgments and page picks that fell back are
``verified: false`` with a note.

``python -m evals.appraisal.gse_d1 [--data DIR]`` reads
``DIR/appraisal/gse_d1/<scenario>/appraisal.{xml,pdf}`` and writes
``DIR/appraisal/truth/gse_d1.json``. The GSE files and the truth stay outside the repository.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pdfplumber
import pymupdf

from basis.extraction.field_mappings import FIELDS
from evals.appraisal.truth import (
    DocumentTruth,
    ExpectedIssue,
    FieldTruth,
    Status,
    TruthSet,
    TruthType,
    appraisal_dir,
    dump_truth,
)

SET_NAME: Final = "gse_d1"
FORM: Final = "uad36_urar"
NOT_ON: Final = "not on the UAD 3.6 URAR"

# ---------------------------------------------------------------------------------------
# The element map. Paths are ElementTree XPath over the XML with namespaces stripped,
# relative to an anchor: ``subject`` (PROPERTY with ValuationUseType="SubjectProperty"),
# ``report`` (VALUATION_REPORT), ``analysis`` (VALUATION_ANALYSIS), ``service`` (the SERVICE
# that carries the VALUATION_ANALYSIS), ``message`` (the root). Party, unit and improvement
# selections that need a predicate ElementTree cannot express are filtered in the readers.
# ---------------------------------------------------------------------------------------
XPATHS: Final[dict[str, tuple[str, str]]] = {
    # subject
    "address_line": ("subject", "ADDRESS/AddressLineText"),
    "address_unit_designator": ("subject", "ADDRESS/AddressUnitDesignatorType"),
    "address_unit": ("subject", "ADDRESS/AddressUnitIdentifier"),
    "city": ("subject", "ADDRESS/CityName"),
    "state": ("subject", "ADDRESS/StateCode"),
    "zip": ("subject", "ADDRESS/PostalCode"),
    "county": ("subject", "ADDRESS/CountyName"),
    "legal_description": (
        "subject",
        "PARCEL_SUMMARY/PARCEL_SUMMARY_DETAIL/ParcelsLegalDescription",
    ),
    "parcel_identification": (
        "subject",
        "PARCELS/PARCEL/PARCEL_IDENTIFICATIONS/PARCEL_IDENTIFICATION",
    ),
    "neighborhood_name": ("subject", "NEIGHBORHOOD/NEIGHBORHOOD_DETAIL/NeighborhoodName"),
    "special_tax_assessments": (
        "subject",
        "PROPERTY_TAXES/PROPERTY_TAX/PROPERTY_TAX_DETAIL/SpecialTaxAssessmentsIndicator",
    ),
    "association_charge": (
        "subject",
        "HOMEOWNERS_ASSOCIATIONS/HOMEOWNERS_ASSOCIATION/ASSOCIATION_CHARGES/ASSOCIATION_CHARGE/"
        "ASSOCIATION_CHARGE_DETAIL",
    ),
    "estate_type": ("subject", "PROPERTY_DETAIL/PropertyEstateType"),
    "attachment_type": ("subject", "PROPERTY_DETAIL/AttachmentType"),
    "units_excluding_adu": ("subject", "PROPERTY_DETAIL/LivingUnitExcludingADUCount"),
    "adu_count": ("subject", "PROPERTY_DETAIL/AccessoryDwellingUnitTotalCount"),
    "overall_quality": ("subject", "PROPERTY_DETAIL/OverallQualityRatingCode"),
    "overall_condition": ("subject", "PROPERTY_DETAIL/OverallConditionRatingCode"),
    "total_finished_area": (
        "subject",
        "PROPERTY_DETAIL/TotalFinishedAreaOfAllLivingUnitsIncludingADUAreaMeasure",
    ),
    # listing and contract
    "sales_contract": ("subject", "SALES_CONTRACTS/SALES_CONTRACT/SALES_CONTRACT_DETAIL"),
    "listing": ("subject", "LISTING_INFORMATIONS/LISTING_INFORMATION/LISTING_INFORMATION_DETAIL"),
    # market
    "demand_supply": ("subject", "MARKET/MARKET_TREND/MarketSupplyTrendType"),
    "marketing_time": ("subject", "MARKET/MARKET_TREND/MarketingTimeType"),
    "market_boundaries": ("subject", "MARKET/MARKET_DETAIL/MarketBoundariesDescription"),
    # site
    "lot_size": ("subject", "SITE/SITE_SIZE_LAYOUT/LotSizeAreaMeasure"),
    "view": ("subject", "SITE/SITE_VIEWS/SITE_VIEW"),
    "zoning_code": (
        "subject",
        "SITE/SITE_ZONING/SITE_ZONING_DETAIL/SiteZoningClassificationIdentifier",
    ),
    "zoning_description": (
        "subject",
        "SITE/SITE_ZONING/SITE_ZONING_DETAIL/SiteZoningClassificationDescription",
    ),
    "zoning_compliance": (
        "subject",
        "SITE/SITE_ZONING/SITE_ZONING_DETAIL/SiteZoningComplianceType",
    ),
    "highest_best_use": ("subject", "SITE/HIGHEST_AND_BEST_USE/SiteHighestBestUseIndicator"),
    "site_utility": ("subject", "SITE/SITE_UTILITIES/SITE_UTILITY"),
    "primary_access": (
        "subject",
        "SITE/PROPERTY_ACCESS/PROPERTY_ACCESS_DETAIL/PropertyPrimaryEntryExitMethodType",
    ),
    "hazard_zone": ("subject", "SITE/HAZARD_ZONES/HAZARD_ZONE/HazardZoneType"),
    # improvements (the dwelling improvements and their units are filtered in readers)
    "improvement": ("subject", "IMPROVEMENTS/IMPROVEMENT"),
    "improvement_type": ("improvement", "IMPROVEMENT_DETAIL/ImprovementType"),
    "built_year": ("improvement", "IMPROVEMENT_DETAIL/PropertyStructureBuiltYear"),
    "built_year_estimated": (
        "improvement",
        "IMPROVEMENT_DETAIL/PropertyStructureBuiltYearEstimatedIndicator",
    ),
    "construction_method": (
        "improvement",
        "STRUCTURE/CONSTRUCTION_METHODS/CONSTRUCTION_METHOD/ConstructionMethodType",
    ),
    "design_style": (
        "improvement",
        "STRUCTURE/ARCHITECTURAL_DESIGN/ArchitecturalDesignCategoryType",
    ),
    "stories": ("improvement", "STRUCTURE/STRUCTURE_DETAIL/StoriesCount"),
    "structure_component": ("improvement", "STRUCTURE/STRUCTURE_COMPONENTS/STRUCTURE_COMPONENT"),
    "wall_material": ("component", "EXTERIOR_WALLS/EXTERIOR_WALL/WallMaterialType"),
    "roof_material": ("component", "ROOF/ROOF_MATERIALS/ROOF_MATERIAL/RoofMaterialType"),
    "foundation_material": (
        "component",
        "FOUNDATIONS/FOUNDATION/FOUNDATION_MATERIALS/FOUNDATION_MATERIAL/FoundationMaterialType",
    ),
    "foundation_style": ("component", "FOUNDATIONS/FOUNDATION/FOUNDATION_STYLES/FOUNDATION_STYLE"),
    "component_type": ("component", "STRUCTURE_COMPONENT_DETAIL/ImprovementComponentType"),
    "component_detail": (
        "component",
        "STRUCTURE_COMPONENT_DETAIL/ImprovementComponentTypeAdditionalDescription",
    ),
    "heating_system": ("improvement", "SYSTEM/HEATING_SYSTEMS/HEATING_SYSTEM"),
    "cooling_system": ("improvement", "SYSTEM/COOLING_SYSTEMS/COOLING_SYSTEM/CoolingSystemType"),
    "effective_age": (
        "improvement",
        "STRUCTURE/STRUCTURE_ANALYSES/STRUCTURE_ANALYSIS/STRUCTURE_ANALYSIS_DETAIL",
    ),
    "outbuilding_type": ("improvement", "IMPROVEMENT_DETAIL/OutbuildingType"),
    "outbuilding_other": ("improvement", "IMPROVEMENT_DETAIL/OutbuildingTypeOtherDescription"),
    "structure_area": ("improvement", "STRUCTURE/STRUCTURE_DETAIL/StructureAreaMeasure"),
    "unit": ("improvement", "PROPERTY_UNITS/PROPERTY_UNIT"),
    "unit_detail": ("unit", "PROPERTY_UNIT_DETAIL"),
    "unit_detail_adu": ("unit", "PROPERTY_UNIT_DETAIL/AccessoryDwellingUnitIndicator"),
    "unit_area": ("unit", "PROPERTY_UNIT_AREA"),
    "flooring": (
        "unit",
        "INTERIOR_COMPONENTS/INTERIOR_COMPONENT/FLOOR_COVERINGS/FLOOR_COVERING/FlooringType",
    ),
    "car_storage": ("subject", "CAR_STORAGES/CAR_STORAGE/CAR_STORAGE_DETAIL"),
    "amenity_category": ("subject", "PROPERTY_AMENITY/AMENITY_CATEGORIES/AMENITY_CATEGORY"),
    # approaches and reconciliation
    "cost_indicator": ("report", "SCOPE_OF_WORK/SCOPE_OF_WORK_DETAIL/CostApproachIndicator"),
    "income_indicator": ("report", "SCOPE_OF_WORK/SCOPE_OF_WORK_DETAIL/IncomeApproachIndicator"),
    "site_value_indicator": ("report", "SCOPE_OF_WORK/SCOPE_OF_WORK_DETAIL/SiteValueIndicator"),
    "assignment_type": ("report", "SCOPE_OF_WORK/SCOPE_OF_WORK_DETAIL/ValuationAssignmentType"),
    "sales_comparison_value": (
        "report",
        "APPROACH_TO_VALUE/SALES_COMPARISON_APPROACH/SALES_COMPARISON_APPROACH_DETAIL/"
        "ValueIndicatedBySalesComparisonAmount",
    ),
    "income_value": (
        "report",
        "APPROACH_TO_VALUE/INCOME_APPROACH/INCOME_APPROACH_DETAIL/ValueIndicatedByIncomeApproachAmount",
    ),
    "cost_approach": ("report", "APPROACH_TO_VALUE/COST_APPROACH"),
    "cost_detail": ("cost", "COST_APPROACH_DETAIL"),
    "cost_improvement": (
        "cost",
        "IMPROVEMENT_COST_AND_DEPRECIATED_VALUES/IMPROVEMENT_COST_AND_DEPRECIATED_VALUE",
    ),
    "cost_line": (
        "cost_improvement",
        "IMPROVEMENT_COST/REPLACEMENT_REPRODUCTION_COSTS/REPLACEMENT_REPRODUCTION_COST",
    ),
    "cost_installation": (
        "cost_improvement",
        "IMPROVEMENT_COST/IMPROVEMENT_COST_DETAIL/ManufacturedHomeInstallationCostAmount",
    ),
    "cost_improvement_total": (
        "cost_improvement",
        "IMPROVEMENT_DEPRECIATED_VALUE_SUMMARY/ImprovementEstimatedTotalCostAmount",
    ),
    "cost_depreciation": ("cost_improvement", "IMPROVEMENT_DEPRECIATION/DepreciationTotalAmount"),
    "site_improvement_description": (
        "cost",
        "SITE_IMPROVEMENTS_AS_IS_COSTS/SITE_IMPROVEMENTS_AS_IS_COST/SiteImprovementDescription",
    ),
    "exclusion_reason": (
        "approach",
        "VALUATION_APPROACH_EXCLUSION_REASONS/VALUATION_APPROACH_EXCLUSION_REASON/ValuationApproachExclusionReasonType",
    ),
    "valuation_condition": (
        "report",
        "VALUATION_RECONCILIATION/VALUATION_RECONCILIATION_SUMMARY/VALUATION_CONDITIONS/"
        "VALUATION_CONDITION/PropertyValuationConditionalConclusionType",
    ),
    "opinion_of_value": (
        "report",
        "VALUATION_RECONCILIATION/VALUATION_RECONCILIATION_SUMMARY/"
        "VALUATION_RECONCILIATION_SUMMARY_DETAIL/OpinionOfValueAmount",
    ),
    "effective_date": (
        "report",
        "VALUATION_RECONCILIATION/VALUATION_RECONCILIATION_SUMMARY/"
        "VALUATION_RECONCILIATION_SUMMARY_DETAIL/AppraisalReportEffectiveDate",
    ),
    "cost_service": ("analysis", "DATA_SOURCES/DATA_SOURCE"),
    # parties: every SERVICE's (A-1 reads ``SERVICES/SERVICE/PARTIES/PARTY`` without naming
    # the service, and a message may carry more than one: Condo1 puts its property data
    # collector in a SERVICE of its own, before the valuation service and its parties)
    "party": ("message", ".//SERVICES/SERVICE/PARTIES/PARTY"),
    "signatory": ("message", ".//SIGNATORIES/SIGNATORY"),
}

# Catalogue keys whose concept the redesigned URAR does not print, with the reason.
NOT_ON_UAD36: Final[dict[str, str]] = {
    "subject.tax_year": NOT_ON + " (no tax year or tax amount box)",
    "subject.real_estate_taxes": NOT_ON + " (no tax year or tax amount box)",
    "subject.map_reference": NOT_ON,
    "subject.census_tract": NOT_ON,
    "neighborhood.location": NOT_ON + " (no urban/suburban/rural box)",
    "neighborhood.built_up": NOT_ON,
    "neighborhood.growth": NOT_ON,
    "neighborhood.property_values": NOT_ON
    + " (the Market section shows a price-trend graph, no increasing/stable/declining box)",
    "neighborhood.present_land_use_one_unit": NOT_ON,
    "neighborhood.present_land_use_two_to_four": NOT_ON,
    "neighborhood.present_land_use_multi_family": NOT_ON,
    "neighborhood.present_land_use_commercial": NOT_ON,
    "neighborhood.present_land_use_other": NOT_ON,
    "neighborhood.price_low_thousands": NOT_ON
    + " (the Market search metrics are the appraiser's search results, not the neighborhood"
    " one-unit price range)",
    "neighborhood.price_high_thousands": NOT_ON
    + " (Market search metrics are not the neighborhood price range)",
    "neighborhood.price_predominant_thousands": NOT_ON
    + " (Market search metrics are not the neighborhood price range)",
    "neighborhood.age_low": NOT_ON + " (year built of sales is a graph)",
    "neighborhood.age_high": NOT_ON + " (year built of sales is a graph)",
    "neighborhood.age_predominant": NOT_ON + " (year built of sales is a graph)",
    "site.dimensions": NOT_ON + " (site size only)",
    "site.shape": NOT_ON,
    "site.off_site_improvements_alley": NOT_ON + " (street type and surface only)",
    "site.flood_zone": NOT_ON
    + " (FEMA zone details print only for a noted FEMA hazard zone; none in this report)",
    "site.fema_map_number": NOT_ON + " (FEMA map details print only for a noted FEMA hazard zone)",
    "site.fema_map_date": NOT_ON + " (FEMA map details print only for a noted FEMA hazard zone)",
    "improvements.walls": NOT_ON + " (Walls and Ceiling reports ceiling height and style)",
    "improvements.gutters_downspouts": NOT_ON,
    "improvements.appliances": NOT_ON + " (no appliance checklist)",
    "improvements.total_rooms": NOT_ON + " (rooms are listed per level; no total room count)",
    "appraiser.effective_date": NOT_ON + " (the signature block has no effective date;"
    " see reconciliation.effective_date_of_appraisal)",
    "appraiser.appraised_value": NOT_ON
    + " (the signature block has no appraised value; see reconciliation.final_market_value)",
}

COST_KEYS: Final = tuple(s.key for s in FIELDS if s.section == "cost_approach")

# MISMO enumerations whose printed wording is not the split CamelCase name.
PRINTED: Final[dict[str, str]] = {
    "OwnerOccupied": "Owner",
    "UnderThreeMonths": "Under 3 months",
    "ThreeToSixMonths": "3 to 6 months",
    "OverSixMonths": "Over 6 months",
    "BuiltIn": "Built-in",
    "LegalNonConforming": "Legal Nonconforming",
    "Midrise": "Mid-rise",
    "Highrise": "High-rise",
    "Garden": "Garden",
    "RowhouseTownhouse": "Rowhouse / Townhouse",
    "SiteBuilt": "Site Built",
    "NotNecessaryForCredibleResults": "Not Necessary for Credible Results",
    "InsufficientData": "Insufficient Data",
    "SubjectToRepair": "Subject to Repair",
    "SubjectToInspection": "Subject to Inspection",
    "SubjectToCompletion": "Subject to Completion",
    "AsIs": "As Is",
    "PostAndPier": "Post and Pier",
    "SalesComparison": "Sales Comparison",
    "ForSaleByOwner": "FSBO",
    "USGSLavaFlowZone": "USGS Lava Flow Zone",
}


def printed_enum(code: str) -> str:
    """The wording the URAR prints for a MISMO enumeration value."""
    if code in PRINTED:
        return PRINTED[code]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", code)


# ---------------------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------------------


def _strip_namespaces(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for name in list(el.attrib):
            if "}" in name:
                el.attrib[name.split("}", 1)[1]] = el.attrib.pop(name)
    return root


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


class Report:
    """The parts of one MISMO 3.6 appraisal message the map reads."""

    def __init__(self, root: ET.Element) -> None:
        self.root = _strip_namespaces(root)
        analysis = self.root.find(".//VALUATION_ANALYSIS")
        report = self.root.find(".//VALUATION_REPORT")
        # The valuation service (the first SERVICE need not be it: Condo1's is a data
        # collection service).
        services = self.root.findall(".//SERVICES/SERVICE")
        service = next(
            (s for s in services if s.find(".//VALUATION_ANALYSIS") is not None),
            services[0] if services else None,
        )
        subject = self.root.find(".//PROPERTY[@ValuationUseType='SubjectProperty']")
        if analysis is None or report is None or service is None or subject is None:
            raise ValueError("not a UAD 3.6 appraisal message (no subject property or report)")
        self.anchors: dict[str, ET.Element] = {
            "message": self.root,
            "analysis": analysis,
            "report": report,
            "service": service,
            "subject": subject,
        }

    @classmethod
    def parse(cls, path: Path) -> Report:
        # The GSE samples are local files we chose to read; no external entities are used.
        return cls(ET.parse(path).getroot())  # noqa: S314

    def all(self, name: str, anchor: ET.Element | None = None) -> list[ET.Element]:
        anchor_name, path = XPATHS[name]
        base = anchor if anchor is not None else self.anchors[anchor_name]
        return base.findall(path)

    def one(self, name: str, anchor: ET.Element | None = None) -> ET.Element | None:
        found = self.all(name, anchor)
        return found[0] if found else None

    def text(self, name: str, anchor: ET.Element | None = None) -> str | None:
        return _text(self.one(name, anchor))

    def texts(self, name: str, anchor: ET.Element | None = None) -> list[str]:
        return [t for t in (_text(e) for e in self.all(name, anchor)) if t is not None]

    def printed(self, name: str, anchor: ET.Element | None = None) -> list[str]:
        """The printed wording of an enumeration element: ``Other`` is replaced by its
        sibling ``<Element>OtherDescription``, which is what the URAR prints."""
        anchor_name, path = XPATHS[name]
        base = anchor if anchor is not None else self.anchors[anchor_name]
        parent_path, _, tag = path.rpartition("/")
        parents = base.findall(parent_path) if parent_path else [base]
        out = []
        for parent in parents:
            for el in parent.findall(tag):
                value = _text(el)
                if value == "Other":
                    value = _child(parent, tag + "OtherDescription") or value
                elif value is not None:
                    value = printed_enum(value)
                if value is not None:
                    out.append(value)
        return out

    def flag(self, name: str, anchor: ET.Element | None = None) -> bool | None:
        value = self.text(name, anchor)
        return None if value is None else value == "true"

    # parties ---------------------------------------------------------------------------
    def parties(self, role: str) -> list[ET.Element]:
        return [p for p in self.all("party") if role in {_text(r) for r in p.iter("PartyRoleType")}]

    @staticmethod
    def party_name(party: ET.Element) -> str | None:
        full = _text(party.find("LEGAL_ENTITY/LEGAL_ENTITY_DETAIL/FullName"))
        if full:
            return full
        parts = [
            _text(party.find(f"INDIVIDUAL/NAME/{tag}"))
            for tag in ("FirstName", "MiddleName", "LastName")
        ]
        name = " ".join(x for x in parts if x)
        return name or None

    # improvements ----------------------------------------------------------------------
    def dwellings(self) -> list[ET.Element]:
        return [
            i for i in self.all("improvement") if self.text("improvement_type", i) == "Dwelling"
        ]

    def outbuildings(self) -> list[ET.Element]:
        return [
            i for i in self.all("improvement") if self.text("improvement_type", i) == "Outbuilding"
        ]

    def units(self) -> list[ET.Element]:
        """Living units of the dwellings, excluding accessory dwelling units."""
        out = []
        for d in self.dwellings():
            for u in self.all("unit", d):
                if self.flag("unit_detail_adu", u):
                    continue
                out.append(u)
        return out

    def components(self, improvement: ET.Element, kind: str) -> list[ET.Element]:
        return [
            c
            for c in self.all("structure_component", improvement)
            if self.text("component_type", c) == kind
        ]

    def cost(self) -> ET.Element | None:
        return self.one("cost_approach")


def _child(el: ET.Element | None, tag: str) -> str | None:
    return None if el is None else _text(el.find(tag))


def _enum_text(el: ET.Element | None, tag: str) -> str | None:
    """The printed wording of an enumeration, using its OtherDescription for ``Other``."""
    value = _child(el, tag)
    if value == "Other":
        return _child(el, tag + "OtherDescription") or value
    return None if value is None else printed_enum(value)


def _num(value: str | None) -> float | None:
    return None if value is None else float(value)


def _int(value: str | None) -> int | None:
    return None if value is None else int(round(float(value)))


# ---------------------------------------------------------------------------------------
# PDF pages
# ---------------------------------------------------------------------------------------

SECTION_IDS: Final[tuple[tuple[str, str], ...]] = (
    ("Assignment Information", "assignment"),
    ("Subject Property Amenities", "amenities"),
    ("Subject Property", "subject"),
    ("Subject Listing Information", "listing"),
    ("Site", "site"),
    ("Energy Efficient", "energy"),
    ("Disaster Mitigation", "disaster"),
    ("Sketch", "sketch"),
    ("Dwelling Exterior", "dwelling_exterior"),
    ("Manufactured Home", "manufactured_home"),
    ("Unit Interior", "unit_interior"),
    ("Outbuilding", "outbuilding"),
    ("Functional Obsolescence", "functional"),
    ("Vehicle Storage", "vehicle_storage"),
    ("Overall Quality and Condition", "overall_qc"),
    ("Highest and Best Use", "hbu"),
    ("Market", "market"),
    ("Project Information", "project"),
    ("Sales Contract", "sales_contract"),
    ("Prior Sale and Transfer History", "prior_sales"),
    ("Sales Comparison Approach", "sales_comparison"),
    ("Rental Information", "rental"),
    ("Income Approach", "income"),
    ("Cost Approach", "cost"),
    ("Reconciliation", "reconciliation"),
    ("Certifications", "certifications"),
    ("Supplemental Information", "supplemental"),
    ("Revision History", "cover"),
)
COVER_MARK: Final = "is not part of the URAR"
CHECKED: Final = ""  # Wingdings 2 checked box
UNCHECKED: Final = ""  # Wingdings 2 empty box
TICK: Final = ""  # Wingdings 2 check mark (utilities table)


def section_id(heading: str) -> str | None:
    for prefix, sid in SECTION_IDS:
        if (
            heading == prefix
            or heading.startswith(prefix + " ")
            or heading.startswith(prefix + "(")
        ):
            return sid
    return None


@dataclass
class Span:
    text: str
    x0: float
    y0: float
    y1: float
    font: str


@dataclass
class Page:
    number: int
    text: str  # whitespace-collapsed text in pymupdf block order
    alt_text: str  # whitespace-collapsed pdfplumber text (fallback search)
    sections: list[tuple[int, str, str]]  # (offset in text, section id, heading)
    start_section: tuple[str, str]
    spans: list[Span] = field(default_factory=list)

    def section_at(self, offset: int) -> tuple[str, str]:
        current = self.start_section
        for at, sid, heading in self.sections:
            if at <= offset:
                current = (sid, heading)
        return current


def _collapse(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Pages:
    """The PDF's pages with section labels; the cover pages are excluded from search."""

    def __init__(self, pages: list[Page], cover_end: int) -> None:
        self.pages = pages
        self.cover_end = cover_end

    @property
    def count(self) -> int:
        return len(self.pages)

    def searchable(self) -> list[Page]:
        return [p for p in self.pages if p.number > self.cover_end]

    @classmethod
    def read(cls, path: Path) -> Pages:
        pages: list[Page] = []
        current = ("cover", "")
        with pdfplumber.open(path) as plumber:
            alt = [_collapse(p.extract_text() or "") for p in plumber.pages]
        with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
            page: Any
            for index, page in enumerate(doc):
                parts: list[str] = []
                spans: list[Span] = []
                sections: list[tuple[int, str, str]] = []
                start = current
                length = 0
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        line_text = ""
                        for s in line["spans"]:
                            spans.append(
                                Span(s["text"], s["bbox"][0], s["bbox"][1], s["bbox"][3], s["font"])
                            )
                            if (
                                s["color"] == 0xFFFFFF
                                and "Bold" in s["font"]
                                and s["size"] >= 8.5
                                and s["bbox"][0] < 60
                            ):
                                heading = _collapse(s["text"])
                                sid = section_id(heading)
                                if sid is not None:
                                    sections.append((length + len(line_text), sid, heading))
                                    current = (sid, heading)
                            line_text += s["text"]
                        chunk = _collapse(line_text)
                        if chunk:
                            parts.append(chunk)
                            length += len(chunk) + 1
                text = " ".join(parts)
                if index > 0 and current[0] == "cover" and not sections and pages:
                    # first URAR page after the cover: the summary page has no white heading
                    if COVER_MARK in pages[-1].text:
                        start = ("summary", "Summary")
                        current = start
                pages.append(Page(index + 1, text, alt[index], sections, start, spans))
        cover_end = 0
        for p in pages:
            if COVER_MARK in p.text:
                cover_end = p.number
                break
        return cls(pages, cover_end)


@dataclass(frozen=True)
class Hit:
    page: int
    start: int
    end: int
    captioned: bool
    section: str
    heading: str
    matched: str
    quote: str


def _value_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value).replace(r"\ ", r"\s*").replace("'", "['’‘]")
    return re.compile(r"(?<![0-9A-Za-z])" + escaped + r"(?![0-9A-Za-z])", re.IGNORECASE)


def find_hits(
    pages: Pages,
    values: Sequence[str],
    captions: Sequence[str],
    window: int = 80,
    after_window: int = 0,
) -> list[Hit]:
    """Every place a value variant is printed, marked captioned when a caption is near."""
    hits: list[Hit] = []
    caption_res = [re.compile(re.escape(c), re.IGNORECASE) for c in captions]
    for page in pages.searchable():
        seen: set[tuple[int, int]] = set()
        for source_index, text in enumerate((page.text, page.alt_text)):
            if source_index == 1 and any(h.page == page.number for h in hits):
                break
            for value in values:
                if not value:
                    continue
                for m in _value_pattern(value).finditer(text):
                    if (m.start(), m.end()) in seen:
                        continue
                    seen.add((m.start(), m.end()))
                    # a short number ("2") needs its caption close by
                    reach = min(window, 30) if len(value) <= 3 and value.isdigit() else window
                    before = text[max(0, m.start() - reach) : m.start()]
                    after = text[m.end() : m.end() + after_window]
                    cap_start = None
                    for cre in caption_res:
                        found = list(cre.finditer(before))
                        if found:
                            cs = m.start() - len(before) + found[-1].start()
                            cap_start = cs if cap_start is None else max(cap_start, cs)
                    captioned = cap_start is not None or (
                        bool(after) and any(cre.search(after) for cre in caption_res)
                    )
                    q0 = cap_start if cap_start is not None else max(0, m.start() - 40)
                    quote = text[q0 : m.end()].strip()
                    if len(quote) > 200:
                        quote = quote[-200:]
                    sid, heading = (
                        page.section_at(m.start()) if source_index == 0 else page.section_at(0)
                    )
                    hits.append(
                        Hit(
                            page.number,
                            m.start(),
                            m.end(),
                            captioned,
                            sid,
                            heading,
                            m.group(0),
                            quote,
                        )
                    )
    return hits


# ---------------------------------------------------------------------------------------
# Facts: what the XML says a key is, and how it is printed
# ---------------------------------------------------------------------------------------


@dataclass
class Fact:
    status: Status
    type: TruthType | None = None
    value: Any = None
    search: tuple[str, ...] = ()  # printed variants to look for (first = display form)
    note: str | None = None
    judged: bool = False  # the value is a mapping judgment or derived: never verified
    tolerance: float | None = None
    sections: tuple[str, ...] | None = None  # override the rule's sections
    captions: tuple[str, ...] | None = None  # override the rule's captions
    exclude_heading: str | None = None  # skip hits under headings containing this
    printed: str | None = None  # what the box shows when the search string is its caption
    value_from_print: bool = False  # search variants are equivalent values: keep the printed one


@dataclass(frozen=True)
class Rule:
    key: str
    sections: tuple[str, ...]
    captions: tuple[str, ...]
    derive: Callable[[Report], Fact]
    window: int = 80
    extra: bool = False
    after: int = 0  # captions printed after the value (signature block)


def money(amount: float) -> str:
    if amount != int(amount):
        return f"${amount:,.2f}"
    return f"${int(amount):,}"


def area(sqft: float) -> str:
    return f"{int(sqft):,} Sq. Ft."


def us_date(iso: str) -> str:
    """``YYYY-MM-DD`` as MM/DD/YYYY; a year-month ``YYYY-MM`` as MM/YYYY."""
    parts = iso[:10].split("-")
    if len(parts) == 2:
        return f"{parts[1]}/{parts[0]}"
    y, m, d = parts
    return f"{m}/{d}/{y}"


def date_fact(value: str) -> Fact:
    """A MISMO date as a ``date`` (YYYY-MM-DD) or a ``month`` (YYYY-MM) truth."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T.*)?", value):
        return present("date", value[:10], us_date(value))
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return present("month", value, us_date(value))
    return Fact("unscorable", note=f"unrecognized date {value!r}")


def absent(note: str) -> Fact:
    return Fact("absent", note=note)


def present(type_: TruthType, value: Any, *search: str, **kw: Any) -> Fact:
    if type_ in ("currency", "int") and isinstance(value, float) and value.is_integer():
        value = int(value)
    return Fact("present", type_, value, tuple(search), **kw)


RULES: dict[str, Rule] = {}


def rule(
    key: str,
    sections: Iterable[str],
    captions: Iterable[str] = (),
    window: int = 80,
    extra: bool = False,
    after: int = 0,
) -> Callable[[Callable[[Report], Fact]], Callable[[Report], Fact]]:
    def register(fn: Callable[[Report], Fact]) -> Callable[[Report], Fact]:
        RULES[key] = Rule(key, tuple(sections), tuple(captions), fn, window, extra, after)
        return fn

    return register


def simple_text(name: str, key: str, sections: Iterable[str], captions: Iterable[str]) -> None:
    def derive(r: Report) -> Fact:
        value = r.text(name)
        if value is None:
            return absent(f"not in the XML ({XPATHS[name][1]}); not printed")
        return present("text", value, value)

    RULES[key] = Rule(key, tuple(sections), tuple(captions), derive)


# --- subject -------------------------------------------------------------------------


@rule("subject.property_address", ["subject"], ["Physical Address", "Property Address"])
def _address(r: Report) -> Fact:
    line = r.text("address_line")
    if line is None:
        return absent("no subject address in the XML")
    unit = r.text("address_unit")
    if unit:
        designator = r.text("address_unit_designator") or "Unit"
        variants = (f"{line}, {designator} {unit}", f"{line} {designator} {unit}")
        return present("text", variants[0], *variants, value_from_print=True)
    return present("text", line, line)


simple_text("city", "subject.city", ["subject"], ["Physical Address"])
simple_text("state", "subject.state", ["subject"], ["Physical Address"])
simple_text("zip", "subject.zip", ["subject"], ["Physical Address"])
simple_text("county", "subject.county", ["subject"], ["County"])
simple_text("legal_description", "subject.legal_description", ["subject"], ["Legal Description"])
simple_text("neighborhood_name", "subject.neighborhood_name", ["subject"], ["Neighborhood Name"])


def _names(r: Report, role: str) -> list[str]:
    return [n for n in (r.party_name(p) for p in r.parties(role)) if n]


@rule("subject.borrower", ["assignment"], ["Borrower Name"])
def _borrower(r: Report) -> Fact:
    names = _names(r, "Borrower")
    if not names:
        return absent("no borrower in the XML")
    note = "several borrowers, one per line; joined with '; '" if len(names) > 1 else None
    return present("text", "; ".join(names), names[0], note=note)


@rule("subject.owner_of_public_record", ["assignment"], ["Current Owner of Public Record"])
def _owner(r: Report) -> Fact:
    names = _names(r, "PropertyOwner")
    if not names:
        return absent("no owner of public record in the XML")
    note = "several owners, one per line; joined with '; '" if len(names) > 1 else None
    return present("text", "; ".join(names), names[0], note=note)


@rule("subject.assessors_parcel", ["site"], ["Assessor Parcel Number (APN)", "APN"], window=200)
def _apn(r: Report) -> Fact:
    ids = [
        _child(pi, "ParcelIdentifier")
        for pi in r.all("parcel_identification")
        if _child(pi, "ParcelIdentificationType") == "AssessorUnformattedIdentifier"
    ]
    found = [i for i in ids if i]
    if not found:
        return absent("no assessor parcel number in the XML")
    note = "several parcels; joined with '; '" if len(found) > 1 else None
    return present("text", "; ".join(found), found[0], note=note)


@rule("subject.occupant", ["unit_interior"], ["Occupancy"])
def _occupant(r: Report) -> Fact:
    codes = {"OwnerOccupied": "owner", "Tenant": "tenant", "Vacant": "vacant"}
    found = [_child(r.one("unit_detail", u), "UnitOccupancyType") for u in r.units()]
    values = {v for v in found if v}
    if len(values) != 1:
        return Fact(
            "unscorable", note=f"units differ in occupancy ({sorted(values)}); single-unit key"
        )
    value = values.pop()
    if value not in codes:
        return Fact("unscorable", note=f"occupancy {value} has no legacy code")
    return present("enum", codes[value], printed_enum(value), exclude_heading="ADU")


@rule("subject.special_assessments", ["subject"], ["Special Tax Assessments"])
def _special(r: Report) -> Fact:
    """The special TAX assessment (SPEC section 4): the Subject section's Special Tax
    Assessments Yes/No (A-1 0100.0033, comment 0100.0050), which G-1 maps to the 1004's and
    the 1073's "Special Assessments $" (1-23 / 1-25, legacy ``_TAX/@_TotalSpecialTaxAmount``).
    The Project section's association special assessment (2500.0163 status, 2500.0013
    remaining balance) is a different concept with no legacy cross-reference, not read here."""
    tax = r.flag("special_tax_assessments")
    if tax:
        return absent("special tax assessment noted (Yes) with a description; no amount printed")
    if tax is False:
        return present(
            "currency",
            0,
            "No",
            judged=True,
            note="printed 'Special Tax Assessments No' (no amount box); $0 policy (SPEC 1)",
        )
    return absent("no special tax assessment information in the XML")


@rule("subject.hoa_dues", ["project"], ["Monthly Amount", "Annual Amount", "Amount"])
def _hoa(r: Report) -> Fact:
    dues = [
        c
        for c in r.all("association_charge")
        if _child(c, "AssociationChargeType") == "AssociationDues"
    ]
    if not dues:
        return absent("no mandatory association fees printed")
    amount = _num(_child(dues[0], "AssociationChargeAmount"))
    if amount is None:
        return absent("association dues without an amount")
    period = _child(dues[0], "AssociationChargePeriodType")
    return present("currency", amount, money(amount), note=f"period {period}" if period else None)


@rule("subject.property_rights_appraised", ["subject"], ["Property Rights Appraised"])
def _rights(r: Report) -> Fact:
    value = r.text("estate_type")
    if value is None:
        return absent("no estate type in the XML")
    wording = r.printed("estate_type")[0]
    code = {"FeeSimple": "fee_simple", "Leasehold": "leasehold"}.get(value)
    enum = code if code else {"code": "other", "text": wording}
    return present("enum", enum, wording)


@rule("subject.assignment_type", ["assignment"], ["Assignment Reason"])
def _assignment(r: Report) -> Fact:
    value = r.text("assignment_type")
    if value is None:
        return absent("no assignment type in the XML")
    wording = r.printed("assignment_type")[0]
    code = {"Purchase": "purchase", "Refinance": "refinance"}.get(value)
    enum = code if code else {"code": "other", "text": wording}
    return present("enum", enum, wording)


@rule("subject.lender_client", ["assignment"], ["Company Name", "Client/Lender"], window=120)
def _lender(r: Report) -> Fact:
    names = _names(r, "Lender") or _names(r, "Client")
    if not names:
        return absent("no lender/client in the XML")
    return present("text", names[0], names[0])


# --- listing and contract ---------------------------------------------------------------


def _contract(r: Report) -> ET.Element | None:
    detail = r.one("sales_contract")
    if detail is None or _child(detail, "SalesContractExistsIndicator") == "false":
        return None
    return detail


NO_CONTRACT: Final = "no sales contract (the report prints no Sales Contract section)"


@rule("listing_and_contract.contract_price", ["sales_contract"], ["Contract Price"])
def _price(r: Report) -> Fact:
    amount = _num(_child(_contract(r), "SalesContractAmount"))
    if amount is None:
        return absent(NO_CONTRACT)
    return present("currency", amount, money(amount))


@rule("listing_and_contract.contract_date", ["sales_contract"], ["Contract Date"])
def _contract_date(r: Report) -> Fact:
    value = _child(_contract(r), "SalesContractDate")
    if value is None:
        return absent(NO_CONTRACT)
    return date_fact(value)


def _listing(r: Report) -> ET.Element | None:
    """The subject's current or most recent listing: the latest ``ListingStartDate`` (the last
    of equals, a listing without a start date sorting first), the rule of the production XML
    reader (``basis.extraction.mismo36``). The legacy 1004 reports the DOM and the offering of
    the listing period that is current or ended last (UAD Appendix D: DOM is "the total number
    of continuous days" until the property was taken off the market or contracted; an earlier
    listing and the cumulative DOM are optional commentary)."""
    best = None
    for listing in r.all("listing"):
        if best is None or (_child(listing, "ListingStartDate") or "") >= (
            _child(best, "ListingStartDate") or ""
        ):
            best = listing
    return best


@rule(
    "listing_and_contract.offering_price",
    ["listing"],
    ["Final List Price", "List Price"],
    window=300,
)
def _offering(r: Report) -> Fact:
    detail = _listing(r)
    amount = _num(_child(detail, "FinalListPriceAmount"))
    if amount is None:
        return absent("no listing of the subject in the look-back period")
    return present(
        "currency",
        amount,
        money(amount),
        note="legacy offering price mapped to the current or final list price",
    )


@rule("listing_and_contract.listing_date", ["listing"], ["Start Date"], window=300)
def _listing_date(r: Report) -> Fact:
    value = _child(_listing(r), "ListingStartDate")
    if value is None:
        return absent("no listing of the subject in the look-back period")
    return date_fact(value)


@rule("listing_and_contract.days_on_market", ["listing"], ["DOM"])
def _dom(r: Report) -> Fact:
    """The current listing's DOM (A-1 0900.0007 ``DaysOnMarketCount``, which G-1 maps to the
    1004's DOM, 1-e-2), not the Total DOM (0900.0003 ``CumulativeDaysOnMarketCount``, no
    legacy cross-reference)."""
    listing = _listing(r)
    if listing is None:
        return absent("no listing of the subject in the look-back period")
    value = _int(_child(listing, "DaysOnMarketCount"))
    if value is None:
        return absent("the current listing reports no DOM")
    note = "the current listing's DOM, not the Total DOM" if len(r.all("listing")) > 1 else None
    return present("int", value, str(value), note=note)


@rule("listing_and_contract.sale_type", ["sales_contract"], ["Transfer Terms"])
def _sale_type(r: Report) -> Fact:
    value = _enum_text(_contract(r), "SaleType")
    if value is None:
        return absent(NO_CONTRACT)
    return present("text", value, value)


@rule(
    "listing_and_contract.seller_concessions",
    ["sales_contract"],
    ["Total Sales Concessions", "Known Sales Concessions"],
)
def _concessions(r: Report) -> Fact:
    detail = _contract(r)
    if detail is None:
        return absent(NO_CONTRACT)
    amount = _num(_child(detail, "TotalSalesConcessionAmount"))
    if amount is not None:
        return present("currency", amount, money(amount))
    if _child(detail, "SalesConcessionIndicator") == "false":
        return present(
            "currency",
            0,
            "No",
            judged=True,
            note="printed 'Known Sales Concessions No'; $0 policy (SPEC 1)",
        )
    return Fact("unscorable", note="concessions known but no amount in the XML")


@rule("listing_and_contract.data_source", ["listing"], ["Listing Type"], window=300)
def _data_source(r: Report) -> Fact:
    value = _enum_text(_listing(r), "ListingType")
    if value is None:
        return absent("no subject listing; the legacy listing data-source box has no counterpart")
    return present(
        "text",
        value,
        value,
        judged=True,
        note="legacy 'Data Source(s)' mapped to the subject listing's Listing Type",
    )


# --- market ("neighborhood") ------------------------------------------------------------


@rule("neighborhood.demand_supply", ["market"], ["Demand/Supply"])
def _demand(r: Report) -> Fact:
    value = r.text("demand_supply")
    codes = {"Shortage": "shortage", "InBalance": "in_balance", "OverSupply": "over_supply"}
    if value not in codes:
        return absent(f"no demand/supply trend in the XML ({value})")
    return present("enum", codes[value], printed_enum(value))


@rule("neighborhood.marketing_time", ["market"], ["Marketing Time"])
def _marketing(r: Report) -> Fact:
    value = r.text("marketing_time")
    codes = {
        "UnderThreeMonths": "under_3_mths",
        "ThreeToSixMonths": "3_6_mths",
        "OverSixMonths": "over_6_mths",
    }
    if value not in codes:
        return absent(f"no marketing time in the XML ({value})")
    return present("enum", codes[value], printed_enum(value))


@rule("neighborhood.neighborhood_boundaries", ["market"], ["Market Area Boundary"])
def _boundaries(r: Report) -> Fact:
    value = r.text("market_boundaries")
    if value is None:
        return absent("no market area boundary in the XML")
    words = value.split()
    prefix = " ".join(words[:8])
    return present("text", value, value, prefix)


# --- site -------------------------------------------------------------------------------


def _lot(r: Report) -> tuple[float, str] | None:
    el = r.one("lot_size")
    value = _num(_text(el))
    if el is None or value is None:
        return None
    return value, el.get("AreaUnitOfMeasureType", "SquareFeet")


def _acres_print(acres: float) -> str:
    return f"{acres:g} Acres"


def _site_area(r: Report) -> Fact:
    lot = _lot(r)
    if lot is None:
        return absent("no site size printed (unit in a project)")
    value, unit = lot
    if unit == "Acres":
        return present(
            "area_sqft", {"sqft": round(value * 43560), "printed_unit": "ac"}, _acres_print(value)
        )
    return present("area_sqft", int(value), area(value))


RULES["site.area_sqft"] = Rule("site.area_sqft", ("site",), ("Total Site Size",), _site_area)
RULES["site.area"] = Rule("site.area", ("site",), ("Total Site Size",), _site_area)


@rule("site.area_acres", ["site"], ["Total Site Size"])
def _acres(r: Report) -> Fact:
    lot = _lot(r)
    if lot is None:
        return absent("no site size printed (unit in a project)")
    value, unit = lot
    if unit != "Acres":
        return absent("site size printed in square feet; no acres printed")
    return present("float", value, _acres_print(value))


@rule("site.view", ["site"], ["View", "Range of View"], window=120)
def _view(r: Report) -> Fact:
    views = r.all("view")
    if not views:
        return absent("no view in the XML")
    primary = [v for v in views if _child(v, "ViewPrimaryIndicator") == "true"] or views
    text = _enum_text(primary[0], "ViewType")
    if text is None:
        return absent("no view type in the XML")
    note = "primary view; other views are listed" if len(views) > 1 else None
    return present("text", text, text, note=note)


@rule("site.zoning_classification", ["site"], ["Classification Code"])
def _zoning_code(r: Report) -> Fact:
    value = r.text("zoning_code")
    if value is None:
        return absent("no zoning classification printed")
    return present("text", value, value)


@rule("site.zoning_description", ["site"], ["Classification Code Description"])
def _zoning_desc(r: Report) -> Fact:
    value = r.text("zoning_description")
    if value is None:
        return absent("no zoning description printed")
    return present("text", value, value, " ".join(value.split()[:3]))


@rule("site.zoning_compliance", ["site"], ["Compliance", "Zoning Compliance"])
def _zoning(r: Report) -> Fact:
    value = r.text("zoning_compliance")
    codes = {
        "Legal": "legal",
        "LegalNonConforming": "legal_nonconforming",
        "NoZoning": "no_zoning",
        "Illegal": "illegal",
    }
    if value not in codes:
        return absent(f"no zoning compliance in the XML ({value})")
    return present("enum", codes[value], printed_enum(value))


@rule("site.highest_and_best_use", ["hbu"], ["Yes No"], window=30)
def _hbu(r: Report) -> Fact:
    value = r.flag("highest_best_use")
    if value is None:
        return absent("no highest-and-best-use answer in the XML")
    return present(
        "bool",
        value,
        "Is the highest and best use",
        printed="Yes" if value else "No",
        note="printed as a checkbox",
    )


UTILITIES: Final = {
    "site.utilities_electric": ("Electricity", "Electricity"),
    "site.utilities_gas": ("Gas", "Gas"),
    "site.utilities_water": ("Water", "Water"),
    "site.utilities_sewer": ("SanitarySewer", "Sanitary Sewer"),
}


def _utility(kind: str) -> Callable[[Report], Fact]:
    def derive(r: Report) -> Fact:
        for u in r.all("site_utility"):
            detail = u.find("SITE_UTILITY_DETAIL")
            if _child(detail, "UtilityType") != kind:
                continue
            ownership = _child(detail, "SiteUtilityOwnershipType")
            if ownership == "Public":
                return present(
                    "enum", "public", caption, printed="Public", note="printed as a check mark"
                )
            service = u.find("SITE_UTILITY_SERVICES/SITE_UTILITY_SERVICE")
            text = None
            if service is not None:
                for child in service:
                    if child.tag.endswith("Type") and _text(child):
                        text = printed_enum(_text(child) or "")
            return present(
                "enum",
                {"code": "other", "text": text or "Private"},
                caption,
                printed="Private",
                note="private utility (check mark in the Private column)"
                + (f"; detail '{text}'" if text else "; no detail printed"),
            )
        return absent("no row for this utility in the Utilities table")

    caption = UTILITY_CAPTIONS[kind]
    return derive


UTILITY_CAPTIONS: Final = {kind: caption for kind, caption in UTILITIES.values()}
for _key, (_kind, _caption) in UTILITIES.items():
    RULES[_key] = Rule(_key, ("site",), ("Private Utility", "Public Private"), _utility(_kind), 200)


@rule("site.off_site_improvements_street", ["site"], ["Primary Access"])
def _street(r: Report) -> Fact:
    value = r.text("primary_access")
    codes = {"PublicStreet": "public", "PrivateStreet": "private"}
    if value not in codes:
        return absent(f"no primary access in the XML ({value})")
    return present(
        "enum",
        codes[value],
        printed_enum(value),
        note="legacy street public/private mapped to Primary Access",
    )


@rule("site.special_flood_hazard", ["site"], ["Hazard Zone"])
def _flood(r: Report) -> Fact:
    zones = r.texts("hazard_zone")
    fema = any("FEMA" in z or "Flood" in z for z in zones)
    if not zones or zones == ["None"]:
        return present(
            "bool",
            False,
            "No Hazard Zone Noted",
            note="no hazard zone noted, so not in a FEMA special flood hazard area",
        )
    return present(
        "bool",
        fema,
        printed_enum(zones[0]),
        judged=True,
        note=f"hazard zones {zones}: no FEMA flood zone among them" if not fema else None,
    )


# --- improvements -----------------------------------------------------------------------

MULTI_UNIT: Final = "a {n}-unit property prints this per unit; the catalogue key is single-unit"


def _unit_count(r: Report) -> int:
    return _int(r.text("units_excluding_adu")) or 1


@rule("improvements.units", ["subject"], ["Units Excluding ADUs"])
def _units(r: Report) -> Fact:
    n = _unit_count(r)
    adus = _int(r.text("adu_count")) or 0
    if n == 1:
        code = "one_with_accessory_unit" if adus else "one"
        return present(
            "enum", code, "1", note=f"Units Excluding ADUs 1, Accessory Dwelling Units {adus}"
        )
    return present("int", n, str(n), note="2-4 unit property: unit count (no legacy enum code)")


@rule("improvements.stories", ["dwelling_exterior"], ["Floors in Building", "Stories"])
def _stories(r: Report) -> Fact:
    for d in r.dwellings():
        value = r.text("stories", d)
        if value:
            return present("int", int(value), value, note="floors in the building (project unit)")
    return absent(NOT_ON + " (prints Levels in Unit, which counts below-grade levels, not stories)")


@rule("improvements.type", ["subject"], ["Attachment Type"])
def _type(r: Report) -> Fact:
    value = r.text("attachment_type")
    codes = {"Detached": "det", "Attached": "att", "SemiDetached": "s_det_end_unit"}
    if value not in codes:
        return absent(f"no attachment type in the XML ({value})")
    return present("enum", codes[value], printed_enum(value))


def _same(values: list[str | None]) -> str | None:
    found = {v for v in values if v}
    return found.pop() if len(found) == 1 else None


@rule("improvements.design_style", ["dwelling_exterior"], ["Dwelling Style"])
def _design(r: Report) -> Fact:
    """The Dwelling Style (A-1 0300.0030 ``ArchitecturalDesignCategoryType``, G-1 to the 1004's
    Design (Style), 1-137). An attached dwelling reports only its Structure Design (0300.0032
    ``StructuralDesignType``: Mid-rise, Rowhouse / Townhouse ...), which G-1 maps to the 1004's
    S-Det./End Unit box (1-133) and the 1073's Project Description (1-133 to 1-137), not to
    Design (Style): the key is absent then."""
    dwellings = r.dwellings()
    style = _same([next(iter(r.printed("design_style", d)), None) for d in dwellings])
    if style is None:
        return absent(
            "no single Dwelling Style in the XML (an attached dwelling reports only its Structure"
            " Design, which G-1 maps to the 1004's attachment type / the 1073's Project"
            " Description)"
        )
    return present("text", style, style)


@rule("improvements.year_built", ["dwelling_exterior"], ["Year Built"])
def _year(r: Report) -> Fact:
    dwellings = r.dwellings()
    years = {r.text("built_year", d) for d in dwellings} - {None}
    if len(years) != 1:
        return Fact(
            "unscorable", note=f"buildings differ in year built ({sorted(y or '' for y in years)})"
        )
    year = int(years.pop() or 0)
    estimated = any(r.flag("built_year_estimated", d) for d in dwellings)
    variants = (f"~{year}", str(year)) if estimated else (str(year),)
    note = "estimated year, printed with '~'" if estimated else None
    if len(dwellings) > 1:
        note = f"{len(dwellings)} buildings, same year built"
    return present("int", year, *variants, note=note)


@rule("improvements.effective_age_years", ["cost"], ["Effective Age"])
def _effective_age(r: Report) -> Fact:
    for d in r.dwellings():
        detail = r.one("effective_age", d)
        if detail is None:
            continue
        years = _child(detail, "EffectiveAgeYearsCount")
        if years is not None:
            return present("int", int(years), f"{years} years")
        low, high = (
            _child(detail, "EffectiveAgeRangeLowYearsCount"),
            _child(detail, "EffectiveAgeRangeHighYearsCount"),
        )
        if low and high:
            return Fact(
                "unscorable", note=f"printed as a range ({low}-{high} years); single-number key"
            )
    return absent(NOT_ON + " outside the cost approach (effective age prints only there)")


def _dwelling_components(r: Report, kind: str) -> list[ET.Element]:
    return [c for d in r.dwellings()[:1] for c in r.components(d, kind)]


@rule("improvements.foundation_type", ["dwelling_exterior"], ["Foundation"])
def _foundation(r: Report) -> Fact:
    styles: list[str] = []
    for c in _dwelling_components(r, "Foundation"):
        for s in r.all("foundation_style", c):
            t = _child(s, "FoundationType")
            if t == "Other":
                t = _child(s, "FoundationTypeOtherDescription") or t
            elif t:
                t = printed_enum(t)
            if t:
                styles.append(t)
    if not styles:
        return absent("no foundation style in the XML")
    codes = {"Slab": "concrete_slab", "Crawl Space": "crawl_space"}
    if all(s in codes for s in styles):
        return present("enum_multi", sorted({codes[s] for s in styles}), styles[0])
    value = " | ".join(styles)
    return present(
        "text",
        value,
        styles[0],
        note="no legacy code for this foundation style (UAD 3.6 'Basement' does not say full or"
        " partial); printed wording"
        if "Basement" in styles
        else "printed wording",
    )


@rule("improvements.foundation_walls", ["dwelling_exterior"], ["Foundation"])
def _foundation_walls(r: Report) -> Fact:
    materials = [
        m
        for c in _dwelling_components(r, "Foundation")
        for m in r.printed("foundation_material", c)
    ]
    if not materials:
        return absent("no foundation material in the XML")
    return present("text", materials[0], materials[0])


def _component_text(r: Report, kind: str, name: str) -> list[str]:
    return [t for c in _dwelling_components(r, kind) for t in r.printed(name, c)]


@rule("improvements.exterior_walls", ["dwelling_exterior"], ["Exterior Walls and Trim"])
def _ext_walls(r: Report) -> Fact:
    values = _component_text(r, "ExteriorWallsAndTrim", "wall_material")
    if not values:
        return absent("no exterior wall material printed")
    return present("text", ", ".join(values), values[0])


@rule("improvements.roof_surface", ["dwelling_exterior"], ["Roof"])
def _roof(r: Report) -> Fact:
    values = _component_text(r, "Roof", "roof_material")
    if not values:
        return absent("no roof material printed")
    return present("text", ", ".join(values), values[0])


@rule("improvements.window_type", ["dwelling_exterior"], ["Windows"])
def _windows(r: Report) -> Fact:
    values = [t for c in _dwelling_components(r, "Windows") for t in r.texts("component_detail", c)]
    if not values:
        return absent("no window detail printed")
    words = values[0].split()
    return present("text", values[0], values[0], " ".join(words[:3]))


def _primary_unit(r: Report) -> ET.Element | None:
    units = r.units()
    return units[0] if len(units) == 1 else None


def _unit_area(r: Report, unit: ET.Element, name: str) -> float:
    total = 0.0
    for a in r.all("unit_area", unit):
        v = _num(_child(a, name))
        if v:
            total += v
    return total


@rule("improvements.floors", ["unit_interior"], ["Flooring"])
def _floors(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return Fact("unscorable", note=MULTI_UNIT.format(n=_unit_count(r)))
    values = r.printed("flooring", unit)
    if not values:
        return absent("no flooring listed")
    return present(
        "text",
        ", ".join(values),
        values[0],
        exclude_heading="ADU",
        note="several floor coverings, joined with ', ' in print order"
        if len(values) > 1
        else None,
    )


# No basement: the redesigned URAR prints "Finished Below Grade 0 Sq. Ft." (A-1 0700.0143 /
# 0700.0144 / 1800.0398, "Deliver even if the value is zero"), and the legacy 1004 box these keys
# stand for took a 0 too (UAD Appendix D, Basement Area / Basement Finish: "If there is no
# basement, enter the numeral zero (0) in both fields"). A printed 0 is the value (SPEC section 1,
# principle 2), not a box left undeveloped.
NO_BASEMENT_NOTE: Final = (
    "no below-grade area: printed 0 Sq. Ft., the legacy 1004's 0 (no basement)"
)


@rule(
    "improvements.basement_area_sqft",
    ["unit_interior"],
    ["Finished Below Grade", "Unfinished Below Grade"],
)
def _basement(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return Fact("unscorable", note=MULTI_UNIT.format(n=_unit_count(r)))
    finished = _unit_area(r, unit, "UnitStandardBelowGradeFinishedAreaMeasure") + _unit_area(
        r, unit, "UnitNonStandardBelowGradeFinishedAreaMeasure"
    )
    unfinished = _unit_area(r, unit, "UnitBelowGradeUnfinishedAreaMeasure")
    total = finished + unfinished
    if total == 0:
        return present(
            "area_sqft",
            0,
            area(0),
            exclude_heading="ADU",
            note=NO_BASEMENT_NOTE,
        )
    if finished and unfinished:
        return present(
            "area_sqft",
            int(total),
            area(finished),
            judged=True,
            exclude_heading="ADU",
            note=f"derived: Finished Below Grade {area(finished)} + Unfinished Below Grade"
            f" {area(unfinished)}; the total is not printed",
        )
    return present(
        "area_sqft",
        int(total),
        area(total),
        exclude_heading="ADU",
        note="all below-grade area is " + ("finished" if finished else "unfinished"),
    )


@rule("improvements.basement_finish_percent", ["unit_interior"], ["Finished Below Grade"])
def _basement_finish(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return Fact("unscorable", note=MULTI_UNIT.format(n=_unit_count(r)))
    finished = _unit_area(r, unit, "UnitStandardBelowGradeFinishedAreaMeasure") + _unit_area(
        r, unit, "UnitNonStandardBelowGradeFinishedAreaMeasure"
    )
    unfinished = _unit_area(r, unit, "UnitBelowGradeUnfinishedAreaMeasure")
    total = finished + unfinished
    if total == 0:
        return present(
            "percent",
            0,
            area(0),
            judged=True,
            exclude_heading="ADU",
            note=NO_BASEMENT_NOTE + "; no percent printed",
        )
    pct = round(100 * finished / total)
    return present(
        "percent",
        pct,
        area(finished),
        judged=True,
        tolerance=1.0,
        exclude_heading="ADU",
        note=f"derived: {area(finished)} finished of {area(total)} below grade; no percent printed",
    )


@rule("improvements.heating_type", ["dwelling_exterior"], ["Heating"])
def _heating(r: Report) -> Fact:
    systems = [h for d in r.dwellings()[:1] for h in r.all("heating_system", d)]
    types = [
        (_child(h, "HeatingSystemType"), _child(h, "HeatingSystemTypeOtherDescription"))
        for h in systems
    ]
    types = [t for t in types if t[0]]
    if not types:
        return absent("no heating system in the XML")
    if all(t == "None" for t, _ in types):
        return Fact("not_developed", search=("None",), note="no heating system (printed 'None')")
    codes = {"ForcedWarmAir": "fwa", "HotWaterBaseboard": "hwbb", "Radiant": "radiant"}
    value: list[Any] = []
    for t, other in types:
        if t == "None":
            continue
        if t in codes:
            value.append(codes[t])
        else:
            value.append({"code": "other", "text": other or printed_enum(t or "")})
    first = types[0]
    printed = printed_enum(first[0] or "") if first[0] != "Other" else (first[1] or "Other")
    return present("enum_multi", value, printed)


@rule("improvements.heating_fuel", ["dwelling_exterior"], ["Heating"])
def _fuel(r: Report) -> Fact:
    fuels = [
        _enum_text(h, "HeatingFuelType")
        for d in r.dwellings()[:1]
        for h in r.all("heating_system", d)
    ]
    found = [f for f in fuels if f]
    if not found:
        return Fact("not_developed", note="no heating fuel (no heating system)")
    return present("text", found[0], found[0])


@rule("improvements.cooling", ["dwelling_exterior"], ["Cooling"])
def _cooling(r: Report) -> Fact:
    values = [v for d in r.dwellings()[:1] for v in r.printed("cooling_system", d)]
    if not values:
        return Fact("not_developed", note="no cooling system in the XML")
    return present("text", values[0], values[0])


def _amenities(r: Report, category: str) -> list[ET.Element]:
    out = []
    for cat in r.all("amenity_category"):
        if _child(cat.find("AMENITY_CATEGORY_DETAIL"), "AmenityCategoryType") == category:
            out.extend(cat.findall("AMENITIES/AMENITY/AMENITY_DETAIL"))
    return out


def _amenity_name(detail: ET.Element) -> str:
    kind = _child(detail, "AmenityType") or ""
    if kind == "Other":
        return _child(detail, "AmenityTypeOtherDescription") or "Other"
    return printed_enum(kind)


@rule("improvements.fireplaces", ["amenities"], ["Indoor Fireplace", "Total Number"])
def _fireplaces(r: Report) -> Fact:
    count = 0
    for d in _amenities(r, "WholeHome"):
        if _child(d, "AmenityType") == "IndoorFireplace":
            count += _int(_child(d, "AmenityCount")) or 1
    if not count:
        return absent("no fireplace listed in the amenities")
    return present("int", count, f"Total Number - {count}")


@rule("improvements.patio_deck", ["amenities"], ["Outdoor Living"], window=200)
def _patio(r: Report) -> Fact:
    names: list[str] = []
    for d in _amenities(r, "OutdoorLiving"):
        name = _amenity_name(d)
        if name in ("Deck", "Patio") and name not in names:
            names.append(name)
    if not names:
        return absent("no patio or deck listed in the amenities")
    return present("text", ", ".join(names), names[0])


def _storages(r: Report) -> list[ET.Element]:
    return r.all("car_storage")


@rule("improvements.car_storage", ["vehicle_storage"], ["Vehicle Storage", "Storage"], window=200)
def _car_storage(r: Report) -> Fact:
    codes: set[str] = set()
    first = None
    for s in _storages(r):
        kind = _child(s, "CarStorageType")
        if kind is None:
            continue
        first = first or printed_enum(kind)
        base = {
            "Driveway": "driveway",
            "Garage": "garage",
            "Carport": "carport",
            "ParkingGarage": "garage",
            "None": "none",
        }.get(kind)
        if base is None:
            return Fact("unscorable", note=f"car storage type {kind} has no legacy code")
        codes.add(base)
        attach = {"Attached": "attached", "Detached": "detached", "BuiltIn": "built_in"}.get(
            _child(s, "CarStorageAttachmentType") or ""
        )
        if attach:
            codes.add(attach)
    if not codes:
        return present("enum_multi", ["none"], "None", note="no vehicle storage listed")
    note = "a parking garage space mapped to 'garage'" if "Parking Garage" == first else None
    return present("enum_multi", sorted(codes), first or "", note=note)


def _garages(r: Report) -> list[ET.Element]:
    return [s for s in _storages(r) if _child(s, "CarStorageType") in ("Garage", "ParkingGarage")]


@rule("improvements.garage_cars", ["vehicle_storage"], ["Garage", "Parking Garage"])
def _garage_cars(r: Report) -> Fact:
    garages = _garages(r)
    if not garages:
        return absent("no garage listed in Vehicle Storage")
    spaces = sum(_int(_child(g, "ParkingSpacesCount")) or 0 for g in garages)
    return present("int", spaces, str(spaces))


@rule("improvements.bedrooms", ["unit_interior"], ["Total Bedrooms", "Bedrooms"])
def _bedrooms(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return Fact("unscorable", note=MULTI_UNIT.format(n=_unit_count(r)))
    value = _int(_child(r.one("unit_detail", unit), "BedroomCount"))
    if value is None:
        return absent("no bedroom count in the XML")
    return present("int", value, str(value), exclude_heading="ADU")


@rule("improvements.bathrooms", ["unit_interior"], ["Total Bathrooms - Full"])
def _bathrooms(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return Fact("unscorable", note=MULTI_UNIT.format(n=_unit_count(r)))
    detail = r.one("unit_detail", unit)
    full, half = (
        _int(_child(detail, "FullBathroomCount")),
        _int(_child(detail, "HalfBathroomCount")),
    )
    if full is None:
        return absent("no bathroom count in the XML")
    return present(
        "baths",
        {"full": full, "half": half or 0},
        str(full),
        exclude_heading="ADU",
        note=f"printed 'Total Bathrooms - Full {full}' and '- Half {half or 0}' (all levels)",
    )


@rule(
    "improvements.gla_sqft",
    ["unit_interior"],
    ["Finished Above Grade", "Gross Building Finished Area"],
)
def _gla(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        total = _num(r.text("total_finished_area"))
        if total is None:
            return Fact("unscorable", note=MULTI_UNIT.format(n=_unit_count(r)))
        return present(
            "area_sqft",
            int(total),
            area(total),
            sections=("sales_comparison",),
            captions=("Gross Building Finished Area",),
            note="multi-unit: the sales grid's Gross Building Finished Area (all units)",
        )
    gla = _unit_area(r, unit, "UnitStandardAboveGradeFinishedAreaMeasure")
    return present("area_sqft", int(gla), area(gla), exclude_heading="ADU")


@rule("improvements.quality", ["overall_qc"], ["Overall Quality"])
def _quality(r: Report) -> Fact:
    value = r.text("overall_quality")
    if value is None:
        return absent("no overall quality rating")
    return present("text", value, value)


@rule("improvements.condition", ["overall_qc"], ["Overall Condition"])
def _condition(r: Report) -> Fact:
    value = r.text("overall_condition")
    if value is None:
        return absent("no overall condition rating")
    return present("text", value, value)


@rule("improvements.additional_features", ["amenities"], ["Miscellaneous"], window=200)
def _features(r: Report) -> Fact:
    names = [_amenity_name(d) for d in _amenities(r, "Miscellaneous")]
    if not names:
        return absent("no miscellaneous amenities listed (no free-text additional features box)")
    return present(
        "text",
        ", ".join(names),
        names[0],
        judged=True,
        note="legacy 'Additional Features' mapped to the Miscellaneous amenities, joined with ', '",
    )


# --- sales comparison and reconciliation -----------------------------------------------


def _sca(r: Report) -> float | None:
    return _num(r.text("sales_comparison_value"))


@rule("sales_comparison.indicated_value", ["sales_comparison"], ["Indicated Value"])
def _sca_value(r: Report) -> Fact:
    value = _sca(r)
    if value is None:
        return Fact("not_developed", note="sales comparison approach not developed")
    return present("currency", value, money(value))


@rule("reconciliation.indicated_value_sales_comparison", ["reconciliation"], ["Indicated Value"])
def _rec_sca(r: Report) -> Fact:
    return _sca_value(r)


def _exclusion(r: Report, approach: str) -> str:
    el = r.anchors["report"].find(f"APPROACH_TO_VALUE/{approach}")
    reason = r.text("exclusion_reason", el) if el is not None else None
    if reason == "Other" and el is not None:
        other = _text(el.find(XPATHS["exclusion_reason"][1] + "OtherDescription"))
        if other:
            return other
    return printed_enum(reason) if reason else "not developed"


def _reason_search(reason: str) -> tuple[str, ...]:
    """A long free-text reason wraps in its table cell: search its first words too."""
    words = reason.split()
    return (reason, " ".join(words[:3])) if len(words) > 3 else (reason,)


def _cost_developed(r: Report) -> bool:
    return bool(r.flag("cost_indicator"))


@rule(
    "reconciliation.indicated_value_cost_approach",
    ["reconciliation"],
    ["Indicated Value", "Reason for Exclusion"],
    window=200,
)
def _rec_cost(r: Report) -> Fact:
    if not _cost_developed(r):
        reason = _exclusion(r, "COST_APPROACH")
        return Fact(
            "not_developed",
            search=_reason_search(reason),
            note=f"cost approach not developed ({reason})",
        )
    value = _num(_child(r.one("cost_detail", r.cost()), "ValueIndicatedByCostApproachAmount"))
    if value is None:
        return Fact("unscorable", note="cost approach developed without an indicated value")
    return present("currency", value, money(value))


@rule(
    "reconciliation.indicated_value_income_approach",
    ["reconciliation"],
    ["Indicated Value", "Reason for Exclusion"],
    window=200,
)
def _rec_income(r: Report) -> Fact:
    if not r.flag("income_indicator"):
        reason = _exclusion(r, "INCOME_APPROACH")
        return Fact(
            "not_developed",
            search=_reason_search(reason),
            note=f"income approach not developed ({reason})",
        )
    value = _num(r.text("income_value"))
    if value is None:
        return Fact("unscorable", note="income approach developed without an indicated value")
    return present("currency", value, money(value))


@rule("reconciliation.appraisal_made", ["reconciliation"], ["Market Value Condition"])
def _made(r: Report) -> Fact:
    values = r.texts("valuation_condition")
    codes = {
        "AsIs": "as_is",
        "SubjectToCompletion": "subject_to_completion",
        "SubjectToRepair": "subject_to_repairs",
        "SubjectToInspection": "subject_to_inspection",
    }
    if len(values) != 1:
        return Fact("unscorable", note=f"several market value conditions {values}; single-code key")
    if values[0] not in codes:
        return absent(f"market value condition {values[0]} has no legacy code")
    return present("enum", codes[values[0]], printed_enum(values[0]))


@rule("reconciliation.final_market_value", ["reconciliation"], ["Opinion of Market Value"])
def _final(r: Report) -> Fact:
    value = _num(r.text("opinion_of_value"))
    if value is None:
        return absent("no opinion of value in the XML")
    return present("currency", value, money(value))


@rule(
    "reconciliation.effective_date_of_appraisal",
    ["reconciliation"],
    ["Effective Date of Appraisal"],
)
def _effective(r: Report) -> Fact:
    value = r.text("effective_date")
    if value is None:
        return absent("no effective date in the XML")
    return date_fact(value)


# --- cost approach ----------------------------------------------------------------------


@dataclass(frozen=True)
class CostLine:
    improvement: str
    area_type: str
    sqft: float | None
    per_sqft: float | None
    cost: float | None


@dataclass(frozen=True)
class CostImprovement:
    label: str
    lines: tuple[CostLine, ...]
    installation: float
    depreciation: float | None
    total: float | None


def cost_improvements(r: Report) -> list[CostImprovement]:
    cost = r.cost()
    if cost is None:
        return []
    out = []
    for imp in r.all("cost_improvement", cost):
        label = (imp.get("label") or "").rsplit("_", 1)[-1]
        lines = []
        for line in r.all("cost_line", imp):
            area_el = line.find("ReplacementOrReproductionCostAreaMeasure")
            area_type = _child(line, "ReplacementOrReproductionCostAreaType") or ""
            if area_type == "Other":
                area_type = (
                    _child(line, "ReplacementOrReproductionCostAreaTypeOtherDescription")
                    or area_type
                )
            lines.append(
                CostLine(
                    label,
                    area_type,
                    _num(_text(area_el)),
                    _num(_child(line, "PricePerAreaAmount")),
                    _num(_child(line, "ReplacementOrReproductionCostAmount")),
                )
            )
        out.append(
            CostImprovement(
                label,
                tuple(lines),
                _num(r.text("cost_installation", imp)) or 0.0,
                _num(r.text("cost_depreciation", imp)),
                _num(r.text("cost_improvement_total", imp)),
            )
        )
    return out


def _cost_value(r: Report, tag: str) -> float | None:
    return _num(_child(r.one("cost_detail", r.cost()) if r.cost() is not None else None, tag))


NOT_DEVELOPED_COST: Final = "cost approach not developed"


def _cost_rule(
    key: str, captions: Iterable[str], window: int = 80
) -> Callable[[Callable[[Report], Fact]], Callable[[Report], Fact]]:
    """A cost-approach key: not developed unless the XML developed the approach."""

    def register(fn: Callable[[Report], Fact]) -> Callable[[Report], Fact]:
        def derive(r: Report) -> Fact:
            if not _cost_developed(r):
                reason = _exclusion(r, "COST_APPROACH")
                return Fact(
                    "not_developed",
                    search=_reason_search(reason),
                    sections=("reconciliation",),
                    captions=("Reason for Exclusion",),
                    note=f"{NOT_DEVELOPED_COST} ({reason})",
                )
            return fn(r)

        RULES[key] = Rule(key, ("cost",), tuple(captions), derive, window)
        return fn

    return register


@rule("cost_approach.site_value", ["cost", "site"], ["Opinion of Site Value"])
def _site_value(r: Report) -> Fact:
    value = _cost_value(r, "SiteEstimatedValueAmount")
    if value is not None:
        note = None if _cost_developed(r) else "site value developed without the cost approach"
        return present("currency", value, money(value), note=note)
    reason = _exclusion(r, "COST_APPROACH")
    return Fact(
        "not_developed",
        search=_reason_search(reason),
        sections=("reconciliation",),
        captions=("Reason for Exclusion",),
        note=f"no site value; {NOT_DEVELOPED_COST} ({reason})",
    )


def _dwelling_line(r: Report, kinds: Iterable[str]) -> CostLine | None:
    wanted = set(kinds)
    for imp in cost_improvements(r):
        for line in imp.lines:
            if line.area_type in wanted:
                return line
    return None


DWELLING_LINE: Final = ("StandardFinishedAreaAboveGrade",)
BASEMENT_LINE: Final = (
    "StandardFinishedAreaBelowGrade",
    "BelowGradeFinishedArea",
    "BelowGradeArea",
)
GARAGE_LINE: Final = ("AttachedGarage", "DetachedGarage", "BuiltInGarage", "Garage")


def _line_rule(key: str, kinds: tuple[str, ...], field_: str, caption: str, label: str) -> None:
    def fn(r: Report) -> Fact:
        line = _dwelling_line(r, kinds)
        if line is None:
            return absent(f"no {label} line in the cost approach")
        if field_ == "sqft" and line.sqft is not None:
            return present("area_sqft", int(line.sqft), area(line.sqft))
        if field_ == "per_sqft" and line.per_sqft is not None:
            return present("float", line.per_sqft, f"${line.per_sqft:,.2f}")
        if field_ == "cost" and line.cost is not None:
            return present("currency", line.cost, money(line.cost))
        return absent(f"the {label} line has no {field_}")

    captions = (caption, "Attached Garage", "Detached Garage") if label == "garage" else (caption,)
    _cost_rule(key, captions, window=60)(fn)


_line_rule(
    "cost_approach.dwelling_sqft", DWELLING_LINE, "sqft", "Above Grade Finished Area", "dwelling"
)
_line_rule(
    "cost_approach.dwelling_cost_per_sqft",
    DWELLING_LINE,
    "per_sqft",
    "Above Grade Finished Area",
    "dwelling",
)
_line_rule(
    "cost_approach.dwelling_cost", DWELLING_LINE, "cost", "Above Grade Finished Area", "dwelling"
)
_line_rule("cost_approach.basement_sqft", BASEMENT_LINE, "sqft", "Below Grade", "below-grade")
_line_rule(
    "cost_approach.basement_cost_per_sqft", BASEMENT_LINE, "per_sqft", "Below Grade", "below-grade"
)
_line_rule("cost_approach.basement_cost", BASEMENT_LINE, "cost", "Below Grade", "below-grade")
_line_rule("cost_approach.garage_sqft", GARAGE_LINE, "sqft", "Garage", "garage")
_line_rule("cost_approach.garage_cost_per_sqft", GARAGE_LINE, "per_sqft", "Garage", "garage")
_line_rule("cost_approach.garage_cost", GARAGE_LINE, "cost", "Garage", "garage")


MECHANICALS_LINE: Final = re.compile(r"mechanical|misc", re.IGNORECASE)


@_cost_rule("cost_approach.mechanicals_misc", ["Mechanicals", "Misc"], window=60)
def _mechanicals(r: Report) -> Fact:
    """A cost line whose area type (or its OtherDescription) names mechanicals/misc."""
    for imp in cost_improvements(r):
        for line in imp.lines:
            if MECHANICALS_LINE.search(line.area_type) and line.cost is not None:
                return present("currency", line.cost, money(line.cost))
    return absent(
        "no mechanicals/misc. line (the manufactured-home delivery, installation and setup"
        " line is extra cost_approach.mh_delivery_installation_setup)"
    )


def _cost_service(r: Report) -> ET.Element | None:
    for ds in r.all("cost_service"):
        if _child(ds, "DataSourceType") == "CostService":
            return ds
    return None


@_cost_rule("cost_approach.quality_rating_from_cost_service", ["Quality Rating"])
def _cost_quality(r: Report) -> Fact:
    value = _child(_cost_service(r), "DataSourceQualityRatingCode")
    if value is None:
        return absent("no cost-service quality rating")
    return present("text", printed_enum(value), printed_enum(value))


@_cost_rule("cost_approach.effective_date_of_cost_data", ["Effective Date"])
def _cost_date(r: Report) -> Fact:
    value = _child(_cost_service(r), "DataSourceEffectiveDate")
    if value is None:
        return absent("no cost data effective date")
    return date_fact(value)


@_cost_rule("cost_approach.cost_data_source", ["Cost Data Source"])
def _cost_source(r: Report) -> Fact:
    value = _child(_cost_service(r), "DataSourceName")
    if value is None:
        return absent("no cost data source")
    return present("text", value, value)


@_cost_rule("cost_approach.total_cost_new", ["Total Estimate of Cost-New"])
def _total_new(r: Report) -> Fact:
    return absent(
        NOT_ON + " (cost lines per improvement and depreciated totals; no total cost new)"
    )


for _dep in ("physical", "functional", "external"):

    def _split(r: Report, kind: str = _dep) -> Fact:
        return absent(NOT_ON + f" (total depreciation per improvement; no {kind} split)")

    _cost_rule(f"cost_approach.depreciation_{_dep}", [f"{_dep.title()} Depreciation"])(_split)


@_cost_rule("cost_approach.depreciation", ["Total Depreciation"])
def _depreciation(r: Report) -> Fact:
    imps = cost_improvements(r)
    if len(imps) != 1:
        return Fact(
            "unscorable",
            note=f"{len(imps)} improvements each print 'Total Depreciation'; no aggregate box",
        )
    value = imps[0].depreciation
    if value is None:
        return absent("no depreciation in the XML")
    variants = (f"$({int(value):,})", money(value)) if value else ("$0",)
    return present(
        "currency",
        value,
        *variants,
        note="printed in parentheses as a deduction; the value is the depreciation (SPEC 4)"
        if value
        else None,
    )


@_cost_rule("cost_approach.depreciated_cost_improvements", ["Depreciated Cost of Dwellings"])
def _depreciated(r: Report) -> Fact:
    dwellings = _cost_value(r, "DepreciatedCostDwellingsTotalAmount")
    outbuildings = _cost_value(r, "DepreciatedCostOutbuildingsTotalAmount")
    if outbuildings:
        return Fact(
            "unscorable",
            note="dwellings and outbuildings are printed as separate depreciated costs; no"
            " combined box (see the extra keys)",
        )
    if dwellings is None:
        return absent("no depreciated cost in the XML")
    return present("currency", dwellings, money(dwellings), note="Depreciated Cost of Dwellings")


@_cost_rule("cost_approach.site_improvements_value", ["As Is Value of Site Improvements"])
def _site_imp(r: Report) -> Fact:
    value = _cost_value(r, "SiteOtherImprovementsAsIsAmount")
    if value is None:
        return absent("no as-is site improvements value")
    return present("currency", value, money(value))


@_cost_rule("cost_approach.indicated_value", ["Indicated Value by Cost Approach"])
def _cost_indicated(r: Report) -> Fact:
    value = _cost_value(r, "ValueIndicatedByCostApproachAmount")
    if value is None:
        return absent("no indicated value by cost approach")
    return present("currency", value, money(value))


@_cost_rule("cost_approach.remaining_economic_life", ["Remaining Economic Life"])
def _rel(r: Report) -> Fact:
    for d in r.dwellings():
        value = _child(r.one("effective_age", d), "EstimatedRemainingEconomicLifeYearsCount")
        if value:
            return present("int", int(value), f"{value} years")
    return absent("no remaining economic life printed")


# --- appraiser --------------------------------------------------------------------------


def _appraiser(r: Report) -> ET.Element | None:
    found = r.parties("Appraiser")
    return found[0] if found else None


def _appraiser_role(r: Report) -> ET.Element | None:
    party = _appraiser(r)
    if party is None:
        return None
    for role in party.findall("ROLES/ROLE"):
        if _child(role, "ROLE_DETAIL/PartyRoleType") == "Appraiser":
            return role
    return None


@rule("appraiser.name", ["certifications"], ["Signature", "Appraiser Name", "Name"], after=200)
def _appraiser_name(r: Report) -> Fact:
    party = _appraiser(r)
    name = r.party_name(party) if party is not None else None
    if not name:
        return absent("no appraiser")
    return present("text", name, name)


@rule("appraiser.company", ["assignment"], ["Company Name"])
def _company(r: Report) -> Fact:
    value = _child(_appraiser_role(r), "APPRAISER/APPRAISER_DETAIL/AppraiserCompanyName")
    if value is None:
        return absent("no appraiser company")
    return present("text", value, value)


@rule("appraiser.license_number", ["certifications"], ["ID"])
def _license(r: Report) -> Fact:
    value = _child(_appraiser_role(r), "LICENSES/LICENSE/LICENSE_DETAIL/LicenseIdentifier")
    if value is None:
        return absent("no license number")
    return present("text", value, value)


@rule("appraiser.license_state", ["certifications"], ["State"])
def _license_state(r: Report) -> Fact:
    value = _child(
        _appraiser_role(r), "LICENSES/LICENSE/LICENSE_DETAIL/LicenseIssuingAuthorityStateCode"
    )
    if value is None:
        return absent("no license state")
    return present("text", value, value)


@rule("appraiser.signature_date", ["certifications"], ["Date of Signature and Report"], after=200)
def _signed(r: Report) -> Fact:
    for s in r.all("signatory"):
        if s.get("label", "").lower().endswith("appraiser"):
            value = _child(s, "EXECUTION/EXECUTION_DETAIL/ExecutionDate")
            if value:
                return date_fact(value)
    return absent("no appraiser signature date")


# --- extra keys (cost-seg facts the catalogue lacks; SPEC section 5) -------------------


@rule("improvements.garage_type", ["vehicle_storage"], ["Garage"], extra=True)
def _garage_type(r: Report) -> Fact:
    for g in _garages(r):
        attach = _child(g, "CarStorageAttachmentType")
        if attach:
            return present("text", printed_enum(attach), printed_enum(attach))
    return absent("no garage")


@rule("improvements.garage_area_sqft", ["vehicle_storage"], ["Garage"], window=120, extra=True)
def _garage_area(r: Report) -> Fact:
    for g in _garages(r):
        value = _num(_child(g, "CarStorageAreaMeasure"))
        if value:
            return present("area_sqft", int(value), area(value))
    return absent("no garage area")


@rule(
    "improvements.construction_method", ["dwelling_exterior"], ["Construction Method"], extra=True
)
def _construction(r: Report) -> Fact:
    value = _same([r.text("construction_method", d) for d in r.dwellings()])
    if value is None:
        return absent("no construction method")
    return present("text", printed_enum(value), printed_enum(value))


@rule(
    "improvements.below_grade_finished_sqft",
    ["unit_interior"],
    ["Finished Below Grade"],
    extra=True,
)
def _bg_fin(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return absent("multi-unit")
    fin = _unit_area(r, unit, "UnitStandardBelowGradeFinishedAreaMeasure")
    unf = _unit_area(r, unit, "UnitBelowGradeUnfinishedAreaMeasure")
    if not fin and not unf:
        return absent("no below-grade area")
    return present("area_sqft", int(fin), area(fin), exclude_heading="ADU")


@rule(
    "improvements.below_grade_unfinished_sqft",
    ["unit_interior"],
    ["Unfinished Below Grade"],
    extra=True,
)
def _bg_unf(r: Report) -> Fact:
    unit = _primary_unit(r)
    if unit is None:
        return absent("multi-unit")
    fin = _unit_area(r, unit, "UnitStandardBelowGradeFinishedAreaMeasure")
    unf = _unit_area(r, unit, "UnitBelowGradeUnfinishedAreaMeasure")
    if not fin and not unf:
        return absent("no below-grade area")
    return present("area_sqft", int(unf), area(unf), exclude_heading="ADU")


@rule(
    "cost_approach.depreciated_cost_dwellings",
    ["cost"],
    ["Depreciated Cost of Dwellings"],
    extra=True,
)
def _dep_dwellings(r: Report) -> Fact:
    value = _cost_value(r, "DepreciatedCostDwellingsTotalAmount") if _cost_developed(r) else None
    if value is None:
        return absent(NOT_DEVELOPED_COST)
    return present("currency", value, money(value))


@rule(
    "cost_approach.depreciated_cost_outbuildings",
    ["cost"],
    ["Depreciated Cost of Outbuildings"],
    extra=True,
)
def _dep_outbuildings(r: Report) -> Fact:
    value = _cost_value(r, "DepreciatedCostOutbuildingsTotalAmount") if _cost_developed(r) else None
    if value is None:
        return absent("no outbuildings in the cost approach")
    return present("currency", value, money(value))


@rule(
    "cost_approach.site_value_method",
    ["cost", "site"],
    ["Primary Site Valuation Method"],
    extra=True,
)
def _site_method(r: Report) -> Fact:
    value = _cost_value(r, "SiteEstimatedValueAmount")
    detail = r.one("cost_detail", r.cost()) if r.cost() is not None else None
    method = _child(detail, "SiteValuationMethodType")
    if value is None or method is None:
        return absent("no site value")
    return present("text", printed_enum(method), printed_enum(method))


@rule(
    "cost_approach.site_improvements_description", ["cost"], ["Description"], window=120, extra=True
)
def _site_imp_desc(r: Report) -> Fact:
    cost = r.cost()
    value = (
        r.text("site_improvement_description", cost)
        if cost is not None and _cost_developed(r)
        else None
    )
    if value is None:
        return absent("no as-is site improvements listed")
    return present("text", value, value, " ".join(value.split()[:5]))


@rule(
    "cost_approach.mh_delivery_installation_setup",
    ["cost"],
    ["Manufactured Home Delivery"],
    extra=True,
)
def _mh_install(r: Report) -> Fact:
    total = sum(i.installation for i in cost_improvements(r)) if _cost_developed(r) else 0
    if not total:
        return absent("no manufactured-home delivery and installation line")
    return present("currency", total, money(total))


def outbuilding_facts(r: Report) -> list[tuple[Rule, Fact]]:
    out: list[tuple[Rule, Fact]] = []
    for i, ob in enumerate(r.outbuildings(), 1):
        kind = r.text("outbuilding_type", ob)
        name = r.text("outbuilding_other", ob) if kind == "Other" else printed_enum(kind or "")
        if not name:
            continue
        out.append(
            (
                Rule(
                    f"improvements.outbuilding_{i}_type",
                    ("outbuilding",),
                    ("Outbuilding -",),
                    lambda _r: absent(""),
                    extra=True,
                ),
                present("text", name, name),
            )
        )
        gba = _num(r.text("structure_area", ob))
        if gba:
            out.append(
                (
                    Rule(
                        f"improvements.outbuilding_{i}_gba_sqft",
                        ("outbuilding",),
                        ("Gross Building Area",),
                        lambda _r: absent(""),
                        extra=True,
                    ),
                    present("area_sqft", int(gba), area(gba)),
                )
            )
    return out


# ---------------------------------------------------------------------------------------
# Pages for one fact
# ---------------------------------------------------------------------------------------


def _checkbox_note(pages: Pages, key: str, fact: Fact, page_no: int) -> str | None:
    """Read the printed checkbox/check mark for HBU and utilities; a note when it disagrees."""
    page = pages.pages[page_no - 1]
    if key == "site.highest_and_best_use":
        for s in page.spans:
            if s.text.startswith("Is the highest and best use"):
                marks = [
                    g
                    for g in page.spans
                    if g.font.startswith("Wingdings") and g.text.strip() and abs(g.y0 - s.y0) < 4
                ]
                if marks:
                    shown = marks[0].text.strip()
                    printed_yes = shown.startswith(CHECKED)
                    return (
                        None
                        if printed_yes == fact.value
                        else f"checkbox reads {'Yes' if printed_yes else 'No'}"
                    )
        return "checkbox not found"
    caption = UTILITIES[key][1]
    headers = {s.text.strip(): s.x0 for s in page.spans if s.text.strip() in ("Public", "Private")}
    for s in page.spans:
        if s.text.strip() == caption and "Public" in headers:
            ticks = [g for g in page.spans if g.text.strip() == TICK and abs(g.y0 - s.y0) < 4]
            if not ticks:
                state = "none"
            else:
                x = ticks[0].x0
                state = (
                    "public"
                    if abs(x - headers["Public"]) < abs(x - headers.get("Private", 1e9))
                    else "private"
                )
            want = fact.value if isinstance(fact.value, str) else "private"
            want = "private" if isinstance(fact.value, dict) else want
            return None if state == want else f"check mark reads {state}"
    return "utility row not found"


def _near_text(
    pages: Pages, value: str, captions: Sequence[str], sections: Sequence[str]
) -> tuple[int, str, str] | None:
    """The text printed after a caption that nearly equals the XML value (ratio >= 0.85)."""
    best: tuple[float, int, str, str] | None = None
    for page in pages.searchable():
        for caption in captions:
            for m in re.finditer(re.escape(caption), page.text, re.IGNORECASE):
                if page.section_at(m.start())[0] not in sections:
                    continue
                tail = page.text[m.end() :].lstrip()
                for length in range(max(1, len(value) - 6), len(value) + 7):
                    candidate = tail[:length]
                    ratio = difflib.SequenceMatcher(None, candidate.lower(), value.lower()).ratio()
                    if ratio >= 0.85 and (best is None or ratio > best[0]):
                        best = (
                            ratio,
                            page.number,
                            candidate.strip(),
                            f"{caption} {candidate}".strip(),
                        )
    return None if best is None else best[1:]


def place(pages: Pages, rule_: Rule, fact: Fact) -> dict[str, Any]:
    """page, also_on, printed, quote, verified and the note for a fact."""
    out: dict[str, Any] = {"page": None, "also_on": (), "printed": None, "quote": None}
    notes = [fact.note] if fact.note else []
    sections = fact.sections or rule_.sections
    captions = fact.captions or rule_.captions
    if not fact.search:
        out["verified"] = fact.status != "present"
        if fact.status == "present":
            notes.append("not searched in the PDF")
        out["note"] = "; ".join(notes) or None
        return out
    hits = find_hits(pages, fact.search, captions, rule_.window, rule_.after)
    if fact.exclude_heading:
        hits = [h for h in hits if fact.exclude_heading not in h.heading]

    def in_section(h: Hit) -> bool:
        return h.section in sections

    ranked = (
        [h for h in hits if h.captioned and in_section(h)],
        [h for h in hits if h.captioned],
        [h for h in hits if in_section(h)],
    )
    verified = not fact.judged
    chosen: Hit | None = None
    for level, group in enumerate(ranked):
        if group:
            # the section listed first wins, then the earliest page
            group.sort(
                key=lambda h: (
                    sections.index(h.section) if h.section in sections else 99,
                    h.matched not in fact.search,
                    h.page,
                    h.start,
                )
            )
            chosen = group[0]
            if level == 1:
                verified = False
                notes.append(f"caption found outside the expected section ({chosen.section})")
            elif level == 2:
                verified = False
                notes.append("value found in the section but not next to its caption")
            break
    if chosen is None and fact.status == "present" and fact.type == "text":
        near = _near_text(pages, str(fact.value), captions, sections)
        if near is not None:
            page_no, printed_text, quote = near
            out.update(page=page_no, printed=printed_text, quote=quote, verified=False)
            notes.append(
                f"the PDF prints {printed_text!r} where the XML has {fact.value!r}; value set to"
                " the print (truth is what the box says)"
            )
            out["value"] = printed_text
            out["note"] = "; ".join(notes)
            return out
    if chosen is None:
        out["verified"] = False
        notes.append("printed value not found in the PDF text layer")
        out["printed"] = fact.search[0] if fact.status == "present" else None
        out["note"] = "; ".join(notes)
        return out
    out["page"] = chosen.page
    out["printed"] = fact.printed or chosen.matched
    if fact.value_from_print:
        out["value"] = _collapse(chosen.matched)
    out["quote"] = chosen.quote
    # other pages printing the same fact: captioned, and in the printed case for words
    others = sorted(
        {
            h.page
            for h in hits
            if h.captioned
            and h.page != chosen.page
            and (fact.type != "text" or h.matched in fact.search)
        }
    )
    out["also_on"] = tuple(others) if fact.status == "present" else ()
    if rule_.key in ("site.highest_and_best_use", *UTILITIES) and fact.status == "present":
        problem = _checkbox_note(pages, rule_.key, fact, chosen.page)
        if problem:
            verified = False
            notes.append(problem)
        else:
            notes.append("checkbox read from the page")
    out["verified"] = verified
    out["note"] = "; ".join(notes) or None
    return out


# ---------------------------------------------------------------------------------------
# Arithmetic checks (expected issues)
# ---------------------------------------------------------------------------------------


def _line_key(area_type: str) -> str:
    if area_type in BASEMENT_LINE:
        return "cost_approach.basement_cost"
    if area_type in GARAGE_LINE:
        return "cost_approach.garage_cost"
    return "cost_approach.dwelling_cost"


def cost_issues(r: Report) -> tuple[list[ExpectedIssue], list[str]]:
    """Cost-approach arithmetic: inconsistencies become issues; checks done are listed."""
    if r.cost() is None:
        return [], []
    issues: list[ExpectedIssue] = []
    checks: list[str] = []
    for imp in cost_improvements(r):
        lines_total = 0.0
        for line in imp.lines:
            # every printed line counts toward the improvement's total, with or without an
            # area (a Mechanicals/Misc. line is a lump sum)
            lines_total += line.cost or 0.0
            if line.sqft is None or line.per_sqft is None or line.cost is None:
                continue
            product = line.sqft * line.per_sqft
            ok = abs(product - line.cost) <= max(1.0, 0.005 * line.cost)
            checks.append(
                f"{imp.label} {line.area_type}: {line.sqft:g} x {line.per_sqft:g} = {product:,.2f},"
                f" printed {line.cost:,.0f} ({'rounding' if ok else 'MISMATCH'})"
            )
            if not ok:
                issues.append(
                    ExpectedIssue(
                        rule="cost_line_product",
                        keys=(_line_key(line.area_type),),
                        note=f"{imp.label}: {line.sqft:g} x {line.per_sqft:g} = {product:,.2f},"
                        f" printed {line.cost:,.0f}",
                    )
                )
        if imp.total is not None:
            expected = lines_total + imp.installation - (imp.depreciation or 0)
            ok = abs(expected - imp.total) <= 1
            checks.append(
                f"{imp.label} total: {lines_total:,.0f} + {imp.installation:,.0f} -"
                f" {imp.depreciation or 0:,.0f} = {expected:,.0f}, printed {imp.total:,.0f}"
                f" ({'ok' if ok else 'MISMATCH'})"
            )
            if not ok:
                issues.append(
                    ExpectedIssue(
                        rule="cost_improvement_total",
                        keys=("cost_approach.depreciation",),
                        note=f"{imp.label}: computed {expected:,.0f}, printed {imp.total:,.0f}",
                    )
                )
    parts = {
        "dwellings": _cost_value(r, "DepreciatedCostDwellingsTotalAmount"),
        "outbuildings": _cost_value(r, "DepreciatedCostOutbuildingsTotalAmount"),
        "site improvements": _cost_value(r, "SiteOtherImprovementsAsIsAmount"),
        "site value": _cost_value(r, "SiteEstimatedValueAmount"),
    }
    indicated = _cost_value(r, "ValueIndicatedByCostApproachAmount")
    if indicated is not None:
        total = sum(v for v in parts.values() if v)
        ok = abs(total - indicated) <= 1
        checks.append(
            " + ".join(f"{v:,.0f}" for v in parts.values() if v)
            + f" = {total:,.0f}, indicated {indicated:,.0f} ({'ok' if ok else 'MISMATCH'})"
        )
        if not ok:
            issues.append(
                ExpectedIssue(
                    rule="cost_approach_total",
                    keys=("cost_approach.indicated_value",),
                    note=f"components sum to {total:,.0f}, printed {indicated:,.0f}",
                )
            )
        dwellings = [
            i for i in cost_improvements(r) if i.lines and i.lines[0].area_type in DWELLING_LINE
        ]
        dwelling_total = sum(i.total or 0 for i in dwellings)
        if (
            parts["dwellings"] is not None
            and dwellings
            and abs(dwelling_total - parts["dwellings"]) > 1
        ):
            issues.append(
                ExpectedIssue(
                    rule="cost_dwellings_total",
                    keys=("cost_approach.depreciated_cost_improvements",),
                    note=f"dwelling totals {dwelling_total:,.0f},"
                    f" printed {parts['dwellings']:,.0f}",
                )
            )
    return issues, checks


def coop_issues(r: Report) -> tuple[list[ExpectedIssue], list[str]]:
    """Cooperative interest: market value = sales comparison - pro rata blanket financing."""
    financings = r.anchors["subject"].findall(
        "PROJECT/PROJECT_BLANKET_FINANCINGS/PROJECT_BLANKET_FINANCING"
    )
    sca, final = _sca(r), _num(r.text("opinion_of_value"))
    if not financings or sca is None or final is None:
        return [], []
    method = _text(r.anchors["report"].find(".//LineOfCreditProRataShareSelectionMethodType"))
    loc_tag = f"LineOfCredit{method or 'Maximum'}ProRataShareAttributableToUnitAmount"
    upb = sum(_num(_child(f, "UPBProRataShareAttributableToUnitAmount")) or 0.0 for f in financings)
    loc = sum(_num(_child(f, loc_tag)) or 0.0 for f in financings)
    expected = sca - upb - loc
    ok = abs(expected - final) <= 1
    check = (
        f"cooperative interest: {sca:,.0f} - {upb:,.0f} - {loc:,.0f} = {expected:,.0f},"
        f" opinion of value {final:,.0f} ({'ok' if ok else 'MISMATCH'})"
    )
    issue = ExpectedIssue(
        rule="cooperative_interest",
        keys=("reconciliation.final_market_value",),
        note=check,
    )
    return ([] if ok else [issue]), [check]


# ---------------------------------------------------------------------------------------
# Documents and the set
# ---------------------------------------------------------------------------------------


def _field(rule_: Rule, fact: Fact, pages: Pages) -> FieldTruth:
    placed: dict[str, Any] = (
        place(pages, rule_, fact)
        if fact.status != "absent"
        else {
            "page": None,
            "also_on": (),
            "printed": None,
            "quote": None,
            "verified": True,
            "note": fact.note,
        }
    )
    return FieldTruth(
        key=rule_.key,
        status=fact.status,
        type=fact.type if fact.status == "present" else None,
        value=placed.get("value", fact.value) if fact.status == "present" else None,
        printed=placed["printed"],
        page=placed["page"],
        also_on=placed["also_on"],
        quote=placed["quote"],
        verified=placed["verified"],
        tolerance=fact.tolerance,
        extra=rule_.extra,
        note=placed["note"],
    )


def build_document(name: str, xml_path: Path, pdf_path: Path, pdf_rel: str) -> DocumentTruth:
    report = Report.parse(xml_path)
    pages = Pages.read(pdf_path)
    fields: list[FieldTruth] = []
    for spec in FIELDS:
        if spec.key in NOT_ON_UAD36:
            fields.append(FieldTruth(key=spec.key, status="absent", note=NOT_ON_UAD36[spec.key]))
            continue
        rule_ = RULES[spec.key]
        fields.append(_field(rule_, rule_.derive(report), pages))
    for rule_ in RULES.values():
        if rule_.extra:
            fact = rule_.derive(report)
            if fact.status == "present":
                fields.append(_field(rule_, fact, pages))
    for rule_, fact in outbuilding_facts(report):
        fields.append(_field(rule_, fact, pages))
    issues, checks = cost_issues(report)
    coop, coop_checks = coop_issues(report)
    issues += coop
    checks += coop_checks
    note = f"cover pages 1-{pages.cover_end} skipped"
    if checks:
        note += "; arithmetic checked: " + "; ".join(checks)
    return DocumentTruth(
        document=name,
        source="gse_d1",
        form=FORM,
        split="dev",
        pdf=pdf_rel,
        sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        pages=pages.count,
        fields=tuple(fields),
        expected_issues=tuple(issues),
        note=note,
    )


def scenarios(directory: Path) -> list[str]:
    return sorted(
        p.name
        for p in directory.iterdir()
        if p.is_dir() and (p / "appraisal.xml").is_file() and (p / "appraisal.pdf").is_file()
    )


def build_set(appraisal: Path) -> TruthSet:
    """Every scenario under ``appraisal/gse_d1/``."""
    base = appraisal / SET_NAME
    docs = [
        build_document(
            s,
            base / s / "appraisal.xml",
            base / s / "appraisal.pdf",
            f"{SET_NAME}/{s}/appraisal.pdf",
        )
        for s in scenarios(base)
    ]
    return TruthSet(set=SET_NAME, documents=tuple(docs))


def coverage(truth: TruthSet) -> list[tuple[str, Counter[str], int, int]]:
    """Per document: status counts over catalogue keys, extra keys, unverified fields."""
    rows = []
    for doc in truth.documents:
        statuses: Counter[str] = Counter(str(f.status) for f in doc.fields if not f.extra)
        extras = sum(1 for f in doc.fields if f.extra)
        unverified = sum(1 for f in doc.fields if not f.verified)
        rows.append((doc.document, statuses, extras, unverified))
    return rows


def mapped_keys() -> list[str]:
    """Catalogue keys that map onto UAD 3.6 at all (everything not in NOT_ON_UAD36)."""
    return [s.key for s in FIELDS if s.key not in NOT_ON_UAD36]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--data", help="BASIS_EVAL_DATA_DIR (default: the environment variable)")
    args = parser.parse_args(argv)
    appraisal = appraisal_dir(args.data)
    if not (appraisal / SET_NAME).is_dir():
        print(f"no {appraisal / SET_NAME}", file=sys.stderr)
        return 2
    truth = build_set(appraisal)
    out = appraisal / "truth" / f"{SET_NAME}.json"
    dump_truth(truth, out)
    print(f"wrote {out} ({len(truth.documents)} documents)")
    print(f"{'document':<12} present absent not_dev unscorable extra unverified issues")
    for (name, c, extras, unverified), doc in zip(coverage(truth), truth.documents, strict=True):
        print(
            f"{name:<12} {c['present']:>7} {c['absent']:>6} {c['not_developed']:>7}"
            f" {c['unscorable']:>10} {extras:>5} {unverified:>10} {len(doc.expected_issues):>6}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
