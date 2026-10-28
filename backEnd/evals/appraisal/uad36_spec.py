"""Generate :mod:`basis.extraction.uad36_map` from the GSE UAD 3.6 specifications.

``python -m evals.appraisal.uad36_spec [--check]`` reads, from
``BASIS_EVAL_DATA_DIR/appraisal/forms/`` (public Freddie Mac downloads, kept out of the
repository like the other GSE files):

* **A-1** ``appendix-a-1-urar-delivery-specification.xlsx`` (URAR Delivery Specification v1.4):
  sheet ``UAD Delivery Spec 1.4`` (one row per MISMO data point instance: Unique ID, Report
  Location, xPath, data point, attribute, format, the Subject Property / comparable columns)
  and ``UAD Enumerations`` (each enumeration's *Output Report Display Text*);
* **G-1** ``appendix-g-1-redesign-to-legacy-uad-cross-reference-guide.xlsx`` (v1.4): sheet
  ``Cross Ref Guide (Clean) 1.4``, which ties each MISMO 3.6 data point to the legacy UAD 2.6
  Form 1004/70 field (identifier such as ``3-14`` and label such as "Opinion of Site Value").

and writes the committed, reviewed module ``basis/extraction/uad36_map.py``: per catalogue key
(:data:`basis.extraction.field_mappings.FIELDS`) the MISMO data points the reader takes the value
from (A-1 Unique ID, full xPath, format, enumerations with their display text), the report
captions the review page highlights near (from the A-1 Report Location), the legacy 1004 fields
G-1 names, and for a key with no UAD 3.6 equivalent the reason.

**The join.** :data:`SELECTORS` is the reviewed join between the catalogue and the specs, one
line per catalogue key: either a G-1 legacy 1004 field identifier (the catalogue's labels are
1004 captions, so this is "catalogue label = G-1 Field Label") plus the MISMO data point(s), or,
for a key G-1 does not cross-reference, an A-1 Report Location field id (``19.009``) plus the
data point. The generator resolves every selector against the sheets and refuses to write when
one is ambiguous or names a data point, enumeration or field the sheets do not have. It also
reports, per key, whether the catalogue label equals the G-1 Field Label it joined on, and for
the keys marked as having no equivalent, the A-1 Report Locations whose label mentions the
catalogue label (the evidence that nothing was missed).

Only rows about the subject (Subject Property column marked) or about the report itself (no
Valuation Use Type column marked: reconciliation, cost approach, parties) are used; comparable
rows never are. Stdlib only (``zipfile`` + ``xml.etree``): no spreadsheet dependency.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree

from basis.extraction.field_mappings import FIELD_BY_KEY, FIELDS, normalize_label
from evals.datasets import data_dir

A1_FILE: Final = "appendix-a-1-urar-delivery-specification.xlsx"
G1_FILE: Final = "appendix-g-1-redesign-to-legacy-uad-cross-reference-guide.xlsx"
A1_SHEET: Final = "UAD Delivery Spec 1.4"
A1_ENUM_SHEET: Final = "UAD Enumerations"
G1_SHEET: Final = "Cross Ref Guide (Clean) 1.4"
OUTPUT: Final = Path(__file__).resolve().parents[2] / "basis" / "extraction" / "uad36_map.py"

_MAIN: Final = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL: Final = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_NOT_DISPLAYED: Final = "<<Does Not Display>>"
# A-1 Report Location sections that describe comparables, rentals or the GRM grid: a
# caption there is a grid column header, not where the subject's value is printed.
_GRID_SECTIONS: Final = frozenset({"22", "23", "24"})

# --------------------------------------------------------------------------- xlsx (stdlib)


def _column(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    assert letters is not None
    index = 0
    for char in letters.group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def read_sheet(path: Path, name: str) -> list[list[str]]:
    """Every row of sheet ``name`` as strings ("" for an empty cell)."""
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{_MAIN}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{_MAIN}t")))
        workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") or "" for rel in rels}
        sheets = workbook.find(f"{_MAIN}sheets")
        assert sheets is not None
        rel_id = next((s.get(_REL) for s in sheets if s.get("name") == name), None)
        if rel_id is None:
            raise ValueError(f"{path.name}: no sheet {name!r}")
        target = targets[rel_id].lstrip("/")
        target = target if target.startswith("xl/") else f"xl/{target}"
        sheet = ElementTree.fromstring(book.read(target))
    data = sheet.find(f"{_MAIN}sheetData")
    assert data is not None
    rows: list[list[str]] = []
    for row in data:
        cells: dict[int, str] = {}
        for cell in row:
            value = cell.find(f"{_MAIN}v")
            kind = cell.get("t")
            if kind == "s" and value is not None and value.text is not None:
                text = shared[int(value.text)]
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{_MAIN}t"))
            else:
                text = value.text if value is not None and value.text is not None else ""
            cells[_column(cell.get("r") or "A")] = text
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


# --------------------------------------------------------------------------- spec rows


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


@dataclass(frozen=True)
class SpecRow:
    """One A-1 (or G-1) data point row."""

    uid: str
    xpath: str  # the container path, ending in "/"
    point: str  # the MISMO data point ("" for an attribute-only row)
    attribute: str
    locations: tuple[str, ...]  # A-1 Report Location lines
    fmt: str = ""
    subject: bool = False
    comparable: bool = False
    legacy_ids: tuple[str, ...] = ()  # G-1 1004 field identifiers
    legacy_label: str = ""  # G-1 1004 field label (all lines joined by " / ")

    @property
    def report_level(self) -> bool:
        return not self.subject and not self.comparable


def _header_index(header: Sequence[str]) -> dict[str, int]:
    return {cell.split("\n")[0].strip(): i for i, cell in enumerate(header)}


def _cell(row: Sequence[str], index: dict[str, int], name: str) -> str:
    i = index[name]
    return row[i].strip() if i < len(row) else ""


_USE_TYPES: Final = (
    "Land Comparable",
    "Property Analyzed Not Used",
    "Rental Comparable",
    "Gross Rent Multiplier Comparable",
    "Sales Comparable",
)


def _marked(row: Sequence[str], index: dict[str, int], name: str) -> bool:
    return _cell(row, index, name).lower() == "x"


def load_a1(path: Path) -> list[SpecRow]:
    rows = read_sheet(path, A1_SHEET)
    index = _header_index(rows[0])
    out: list[SpecRow] = []
    for row in rows[1:]:
        uid = _cell(row, index, "Unique ID")
        if not uid:
            continue
        out.append(
            SpecRow(
                uid=uid,
                xpath=_cell(row, index, "MISMO xPath"),
                point=_cell(row, index, "MISMO Data Point Name"),
                attribute=_cell(row, index, "MISMO Attribute Name"),
                locations=tuple(_lines(_cell(row, index, "Report Location"))),
                fmt=_cell(row, index, "UAD Data Point Format Type"),
                subject=_marked(row, index, "Subject Property"),
                comparable=any(_marked(row, index, name) for name in _USE_TYPES),
            )
        )
    return out


def load_g1(path: Path) -> list[SpecRow]:
    rows = read_sheet(path, G1_SHEET)
    index = _header_index(rows[0])
    out: list[SpecRow] = []
    for row in rows[1:]:
        uid = _cell(row, index, "Unique ID")
        if not uid:
            continue
        sort_id = _cell(row, index, "xRef 2.6 Form 1004/70 Sort ID")
        ids = _lines(_cell(row, index, "xRef 2.6 Form 1004/70 Field Identifier"))
        out.append(
            SpecRow(
                uid=uid,
                xpath=_cell(row, index, "MISMO xPath"),
                point=_cell(row, index, "MISMO Data Point Name"),
                attribute=_cell(row, index, "MISMO Attribute Name"),
                locations=tuple(_lines(_cell(row, index, "Report Location"))),
                subject=_marked(row, index, "Subject Property"),
                comparable=any(_marked(row, index, name) for name in _USE_TYPES),
                legacy_ids=tuple(ids) if sort_id and sort_id != "No Cross Reference" else (),
                legacy_label=" / ".join(
                    _lines(_cell(row, index, "xRef 2.6 Form 1004/70 Field Label"))
                ),
            )
        )
    return out


def load_enums(path: Path) -> dict[str, list[tuple[str, str, str]]]:
    """``{unique id: [(data point, enumeration, display text)]}`` from A-1."""
    rows = read_sheet(path, A1_ENUM_SHEET)
    index = _header_index(rows[0])
    display = next(name for name in index if name.startswith("Output Report Display Text"))
    out: dict[str, list[tuple[str, str, str]]] = {}
    for row in rows[1:]:
        uid = _cell(row, index, "Unique ID")
        if not uid:
            continue
        out.setdefault(uid, []).append(
            (
                _cell(row, index, "MISMO Data Point / Attribute Name"),
                _cell(row, index, "Supported Enumerations"),
                _cell(row, index, display),
            )
        )
    return out


# --------------------------------------------------------------------------- selectors

Where = Mapping[str, Mapping[str, str | tuple[str, ...]]]


@dataclass(frozen=True)
class Selector:
    """How one catalogue key joins the specs (module doc). ``via``: ``g1`` (``ref`` is a legacy
    1004 field identifier, one per data point or one for all), ``a1`` (``ref`` is an A-1 Report
    Location field id), ``none`` (no UAD 3.6 equivalent; ``note`` says why), ``unresolved``
    (an equivalent exists in part but no data point carries the 1004 box; ``note``)."""

    via: str
    ref: tuple[str, ...] = ()
    points: tuple[str, ...] = ()
    rule: str = "text"
    where: Where = field(default_factory=dict)
    scope: str | None = None
    gate: tuple[str, ...] = ()
    location: str | None = None  # a Report Location substring that picks among several rows
    note: str = ""
    # A-1 data points read besides ``points`` (A-1 Report Location field id, data point): the
    # parts of a value G-1 does not cross-reference (the unit of an address).
    extra: tuple[tuple[str, str], ...] = ()


def G(  # noqa: N802 - a short constructor keeps the table readable
    ref: str | tuple[str, ...], *points: str, rule: str = "text", **kw: Any
) -> Selector:
    return Selector("g1", (ref,) if isinstance(ref, str) else ref, points, rule, **kw)


def A(  # noqa: N802
    ref: str | tuple[str, ...], *points: str, rule: str = "text", **kw: Any
) -> Selector:
    return Selector("a1", (ref,) if isinstance(ref, str) else ref, points, rule, **kw)


def NONE(note: str) -> Selector:  # noqa: N802
    return Selector("none", note=note)


def UNRESOLVED(note: str) -> Selector:  # noqa: N802
    return Selector("unresolved", note=note)


_NO_XREF: Final = "G-1 cross-references no UAD 3.6 data point to this 1004 field and no A-1 "
_NO_XREF_END: Final = "Report Location carries it: the redesigned URAR does not report it"
NO_XREF: Final = _NO_XREF + _NO_XREF_END
_NAME: Final = ("FirstName", "MiddleName", "LastName", "SuffixName", "FullName")
_COST: Final = ("CostApproachIndicator",)
_BELOW_GRADE: Final = ("FullyBelowGrade", "PartiallyBelowGrade")
_BASEMENT_COST: Final = (
    "StandardFinishedAreaBelowGrade",
    "NonStandardFinishedAreaBelowGrade",
    "UnfinishedAreaBelowGrade",
)
_ABOVE_GRADE_ROW: Final = {"REPLACEMENT_REPRODUCTION_COST": {
    "ReplacementOrReproductionCostAreaType": "StandardFinishedAreaAboveGrade"
}}  # fmt: skip
_BASEMENT_ROW: Final = {
    "REPLACEMENT_REPRODUCTION_COST": {"ReplacementOrReproductionCostAreaType": _BASEMENT_COST}
}


def _role(role: str | tuple[str, ...]) -> Where:
    return {"PARTY": {"PartyRoleType": role}}


_DWELLING: Final = {"IMPROVEMENT": {"ImprovementType": "Dwelling"}}

# The reviewed join (module doc). Rules are what the reader does with the values
# (basis.extraction.mismo36.RULES); scopes narrow a repeating container the reader resolves
# from the document's relationships.
SELECTORS: Final[dict[str, Selector]] = {
    # Subject
    "subject.property_address": G(
        "1-6",
        "AddressLineText",
        rule="address",
        extra=(("3.000", "AddressUnitDesignatorType"), ("3.000", "AddressUnitIdentifier")),
        note="the A-1 Physical Address (3.000) includes the unit (Unit 202) G-1 does not map",
    ),
    "subject.city": G("1-7", "CityName"),
    "subject.state": G("1-8", "StateCode"),
    "subject.zip": G("1-9", "PostalCode"),
    "subject.borrower": G("1-10", *_NAME, rule="name", where=_role("Borrower")),
    "subject.owner_of_public_record": G("1-11", *_NAME, rule="name", where=_role("PropertyOwner")),
    "subject.county": G("1-12", "CountyName"),
    "subject.legal_description": G("1-13", "ParcelsLegalDescription"),
    "subject.assessors_parcel": G(
        "1-14",
        "ParcelIdentifier",
        rule="join",
        where={
            "PARCEL_IDENTIFICATION": {"ParcelIdentificationType": "AssessorUnformattedIdentifier"}
        },
    ),
    "subject.tax_year": NONE(NO_XREF),
    "subject.real_estate_taxes": NONE(NO_XREF),
    "subject.neighborhood_name": G("1-17", "NeighborhoodName"),
    "subject.map_reference": NONE(NO_XREF),
    "subject.census_tract": NONE(NO_XREF),
    "subject.occupant": G("1-20", "UnitOccupancyType", rule="per_unit_agree"),
    "subject.special_assessments": G(
        "1-23",
        "SpecialTaxAssessmentsIndicator",
        rule="indicator_no",
        note="UAD 3.6 reports whether there are special tax assessments (Yes/No) and a "
        "description, not an amount: No reads as $0 (SPEC principle 2); Yes leaves the "
        "amount empty",
    ),
    "subject.hoa_dues": G(
        "1-25",
        "AssociationChargeAmount",
        rule="amount",
        where={"ASSOCIATION_CHARGE": {"AssociationChargeType": "AssociationDues"}},
    ),
    "subject.property_rights_appraised": G("1-28", "PropertyEstateType", rule="enum"),
    "subject.assignment_type": G("1-32", "ValuationAssignmentType", rule="enum"),
    "subject.lender_client": G(
        "1-36",
        "FullName",
        rule="client",
        where=_role("@client"),
        location="2.012",
        note="the client party: a Lender first, then the other A-1 client roles, an "
        "appraisal management company last (it is named apart on the report)",
    ),
    # Listing and contract
    "listing_and_contract.contract_price": G("1-44", "SalesContractAmount", rule="amount"),
    "listing_and_contract.contract_date": G("1-45", "SalesContractDate", rule="date"),
    "listing_and_contract.offering_price": A(
        "19.009",
        "FinalListPriceAmount",
        rule="amount",
        scope="current_listing",
        note="G-1 maps the 1004 offering price only into the listing commentary (1-40); the "
        "A-1 'Current or Final List Price' is the listing's offering price",
    ),
    "listing_and_contract.listing_date": A(
        "19.005", "ListingStartDate", rule="date", scope="current_listing"
    ),
    "listing_and_contract.days_on_market": G(
        "1-e-2", "DaysOnMarketCount", rule="number", scope="current_listing"
    ),
    "listing_and_contract.sale_type": G("1-e-3", "SaleType", rule="enum", location="20.006"),
    "listing_and_contract.seller_concessions": G(
        ("1-51a", "1-49"),
        "TotalSalesConcessionAmount",
        "SalesConcessionIndicator",
        rule="amount_or_no",
        note="Known Sales Concessions No reads as $0 (SPEC principle 2)",
    ),
    "listing_and_contract.data_source": A(
        "19.003",
        "ListingType",
        rule="enum",
        note="the 1004 'Data Source(s)' of the listing; G-1 maps it only into commentary "
        "(1-40); the A-1 Listing Type names the listing's source (MLS, ...)",
    ),
    # Neighborhood (UAD 3.6: Market)
    "neighborhood.location": NONE(NO_XREF),
    "neighborhood.built_up": NONE(NO_XREF),
    "neighborhood.growth": NONE(NO_XREF),
    "neighborhood.property_values": NONE(NO_XREF),
    "neighborhood.demand_supply": G("1-64", "MarketSupplyTrendType", rule="enum"),
    "neighborhood.marketing_time": G("1-67", "MarketingTimeType", rule="enum"),
    "neighborhood.present_land_use_one_unit": NONE(NO_XREF),
    "neighborhood.present_land_use_two_to_four": NONE(NO_XREF),
    "neighborhood.present_land_use_multi_family": NONE(NO_XREF),
    "neighborhood.present_land_use_commercial": NONE(NO_XREF),
    "neighborhood.present_land_use_other": NONE(NO_XREF),
    "neighborhood.neighborhood_boundaries": G("1-82", "MarketBoundariesDescription"),
    "neighborhood.price_low_thousands": NONE(
        "G-1 cross-references 1004 1-70..1-72 (One-Unit Housing Price $(000)) to the Market "
        "search result metrics, but A-1 defines those (17.013-17.015) as the results of the "
        "appraiser's comparable search under its own criteria, printed in dollars under "
        "'Search Result Metrics': not the neighborhood's one-unit price range in thousands"
    ),
    "neighborhood.price_high_thousands": NONE(
        "G-1 cross-references 1004 1-70..1-72 (One-Unit Housing Price $(000)) to the Market "
        "search result metrics, but A-1 defines those (17.013-17.015) as the results of the "
        "appraiser's comparable search under its own criteria, printed in dollars under "
        "'Search Result Metrics': not the neighborhood's one-unit price range in thousands"
    ),
    "neighborhood.price_predominant_thousands": NONE(
        "G-1 cross-references 1004 1-70..1-72 (One-Unit Housing Price $(000)) to the Market "
        "search result metrics, but A-1 defines those (17.013-17.015) as the results of the "
        "appraiser's comparable search under its own criteria, printed in dollars under "
        "'Search Result Metrics': not the neighborhood's one-unit price range in thousands"
    ),
    "neighborhood.age_low": NONE(NO_XREF),
    "neighborhood.age_high": NONE(NO_XREF),
    "neighborhood.age_predominant": NONE(NO_XREF),
    # Site
    "site.dimensions": G("1-85", "SiteDimensionsDescription"),
    "site.area_sqft": G("1-86", "LotSizeAreaMeasure", rule="area"),
    "site.area": NONE("derived from site.area_sqft (basis.extraction.derived), never read"),
    "site.area_acres": NONE("derived from site.area_sqft (basis.extraction.derived), never read"),
    "site.shape": G(
        "1-87",
        "SiteFeatureDescription",
        where={"SITE_FEATURE": {"SiteFeatureType": "Shape"}},
    ),
    "site.view": G(
        "1-e-6",
        "ViewType",
        rule="join",
        where={"SITE_VIEW": {"ViewPrimaryIndicator": "true"}},
        note="the primary view(s) (A-1 4.039 '(Primary)'): the 1004 View box is the view",
    ),
    "site.zoning_classification": G("1-89", "SiteZoningClassificationIdentifier"),
    "site.zoning_description": G("1-90", "SiteZoningClassificationDescription"),
    "site.zoning_compliance": G("1-91", "SiteZoningComplianceType", rule="enum"),
    "site.highest_and_best_use": G("1-96", "SiteHighestBestUseIndicator", rule="bool"),
    "site.utilities_electric": G(
        "1-100",
        "SiteUtilityOwnershipType",
        rule="utility",
        where={"SITE_UTILITY": {"UtilityType": "Electricity"}},
    ),
    "site.utilities_gas": G(
        "1-103",
        "SiteUtilityOwnershipType",
        rule="utility",
        where={"SITE_UTILITY": {"UtilityType": "Gas"}},
    ),
    "site.utilities_water": G(
        "1-106",
        "SiteUtilityOwnershipType",
        rule="utility",
        where={"SITE_UTILITY": {"UtilityType": "Water"}},
        extra=(("4.090", "WaterSourceType"),),
    ),
    "site.utilities_sewer": G(
        "1-109",
        "SiteUtilityOwnershipType",
        rule="utility",
        where={"SITE_UTILITY": {"UtilityType": "SanitarySewer"}},
        extra=(("4.084", "SewerSystemType"),),
    ),
    "site.off_site_improvements_street": G(
        "1-112", "PropertyPrimaryEntryExitMethodType", rule="enum"
    ),
    "site.off_site_improvements_alley": NONE(NO_XREF),
    "site.special_flood_hazard": G("1-117", "HazardZoneType", rule="flood"),
    "site.flood_zone": NONE(NO_XREF),
    "site.fema_map_number": NONE(NO_XREF),
    "site.fema_map_date": NONE(NO_XREF),
    # Improvements
    # G-1 cross-references 1004 1-128/1-129 (Units One / One with Accessory Unit) only to the
    # per-unit AccessoryDwellingUnitIndicator (10.011 "ADU", printed in each Unit Interior): a
    # Yes/No that cannot say how many units the property has. A-1 carries the box as the
    # subject property's own counts, printed in Subject Property (and the Summary).
    "improvements.units": A(
        ("3.005", "3.006"),
        "LivingUnitExcludingADUCount",
        "AccessoryDwellingUnitTotalCount",
        rule="units",
        note="the 1004 Units box from A-1's property counts (3.005 Units Excluding ADUs, "
        "3.006 Accessory Dwelling Units): 1 unit and no ADU is One, 1 unit and an ADU is One "
        "with Accessory Unit, 2-4 units (no legacy 1004 code) read as the count; G-1's "
        "1-128 cross-reference (the per-unit ADU indicator) cannot produce a count",
    ),
    "improvements.stories": G("1-130", "StoriesCount", rule="number", where=_DWELLING),
    "improvements.type": G("1-131", "AttachmentType", rule="enum"),
    "improvements.design_style": G(
        "1-137", "ArchitecturalDesignCategoryType", rule="enum", where=_DWELLING
    ),
    "improvements.year_built": G(
        "1-138", "PropertyStructureBuiltYear", rule="number", where=_DWELLING
    ),
    "improvements.effective_age_years": G(
        "1-139",
        "EffectiveAgeYearsCount",
        "EffectiveAgeRangeLowYearsCount",
        "EffectiveAgeRangeHighYearsCount",
        rule="effective_age",
        where=_DWELLING,
    ),
    "improvements.foundation_type": G("1-140", "FoundationType", rule="join", where=_DWELLING),
    "improvements.foundation_walls": G(
        "1-152", "FoundationMaterialType", rule="join", where=_DWELLING
    ),
    "improvements.floors": G(
        "1-159", "FlooringType", rule="join", where=_DWELLING, scope="living_units"
    ),
    "improvements.walls": UNRESOLVED(
        "G-1 maps 1004 1-160 (interior walls) only to the WallsAndCeiling component's type and "
        "condition rows: UAD 3.6 has no interior wall material data point"
    ),
    "improvements.basement_area_sqft": G(
        "1-144",
        "LevelFinishedAreaMeasure",
        "LevelUnfinishedAreaMeasure",
        rule="sum",
        where={**_DWELLING, "LEVEL": {"GradeLevelType": _BELOW_GRADE}},
        scope="living_units",
        note="finished plus unfinished area of the below-grade levels (G-1 maps 1-144 to both)",
    ),
    "improvements.basement_finish_percent": G(
        ("1-145", "1-144"),
        "LevelFinishedAreaMeasure",
        "LevelUnfinishedAreaMeasure",
        rule="finished_percent",
        where={**_DWELLING, "LEVEL": {"GradeLevelType": _BELOW_GRADE}},
        scope="living_units",
        note="below-grade finished / (finished + unfinished), rounded; derived, not printed",
    ),
    "improvements.exterior_walls": G("1-153", "WallMaterialType", rule="join", where=_DWELLING),
    "improvements.roof_surface": G("1-154", "RoofMaterialType", rule="join", where=_DWELLING),
    "improvements.gutters_downspouts": UNRESOLVED(
        "G-1 maps 1004 1-155 only to the generic Other structure component's description: "
        "the A-1 component types (Exterior Walls and Trim, Foundation, Roof, Windows) have no "
        "gutters entry"
    ),
    "improvements.window_type": G(
        "1-156",
        "ImprovementComponentTypeAdditionalDescription",
        where={**_DWELLING, "STRUCTURE_COMPONENT": {"ImprovementComponentType": "Windows"}},
    ),
    "improvements.heating_type": G("1-171", "HeatingSystemType", rule="join", where=_DWELLING),
    "improvements.heating_fuel": G("1-176", "HeatingFuelType", rule="join", where=_DWELLING),
    "improvements.cooling": G("1-177", "CoolingSystemType", rule="join", where=_DWELLING),
    "improvements.fireplaces": G(
        "1-182",
        "AmenityCount",
        rule="sum",
        where={"AMENITY": {"AmenityType": "IndoorFireplace"}},
        location="0200.0004",
    ),
    "improvements.patio_deck": G(
        "1-183",
        "AmenityType",
        rule="join",
        where={"AMENITY": {"AmenityType": ("Patio", "Deck")}},
        note="G-1 maps the same AmenityType to the 1004 Patio/Deck (1-183) and Porch (1-191) "
        "boxes: only Patio and Deck are this box",
    ),
    "improvements.car_storage": G(
        ("1-195", "1-203"), "CarStorageType", "CarStorageAttachmentType", rule="car_storage"
    ),
    "improvements.garage_cars": G(
        "1-200",
        "ParkingSpacesCount",
        rule="sum",
        where={"CAR_STORAGE": {"CarStorageType": ("Garage", "ParkingGarage")}},
    ),
    "improvements.total_rooms": NONE(
        "G-1 cross-references 1004 1-214 to nothing for the subject (TotalRoomCount is a "
        "comparable-grid row): the redesigned URAR lists rooms per level, no total room count"
    ),
    "improvements.bedrooms": G("1-215", "BedroomCount", rule="per_unit"),
    "improvements.bathrooms": G(
        "1-216", "FullBathroomCount", "HalfBathroomCount", rule="baths", location="10.02"
    ),
    "improvements.gla_sqft": G(
        "1-217",
        "UnitStandardAboveGradeFinishedAreaMeasure",
        rule="gla",
        extra=(("22.05.04", "TotalFinishedAreaOfAllLivingUnitsIncludingADUAreaMeasure"),),
        note="per unit in UAD 3.6: one living unit reads as its own area; a 2-4 unit property "
        "as the A-1 Gross Building Finished Area (22.05.04), else the sum over its units",
    ),
    "improvements.quality": G("2-25", "OverallQualityRatingCode"),
    "improvements.condition": G("2-27", "OverallConditionRatingCode"),
    "improvements.appliances": NONE(NO_XREF),
    "improvements.additional_features": UNRESOLVED(
        "G-1 maps 1004 1-218 to the energy-efficiency indicators, certifications and ratings "
        "(several data points, none of them the free-text box)"
    ),
    # Sales comparison and reconciliation
    "sales_comparison.indicated_value": G(
        "2-124",
        "ValueIndicatedBySalesComparisonAmount",
        rule="amount",
        gate=("SalesComparisonApproachIndicator",),
    ),
    "reconciliation.indicated_value_sales_comparison": G(
        "2-125",
        "ValueIndicatedBySalesComparisonAmount",
        rule="amount",
        gate=("SalesComparisonApproachIndicator",),
    ),
    "reconciliation.indicated_value_cost_approach": G(
        "2-126", "ValueIndicatedByCostApproachAmount", rule="amount", gate=_COST
    ),
    "reconciliation.indicated_value_income_approach": G(
        "2-127",
        "ValueIndicatedByIncomeApproachAmount",
        rule="amount",
        gate=("IncomeApproachIndicator",),
    ),
    "reconciliation.appraisal_made": G(
        "2-129", "PropertyValuationConditionalConclusionType", rule="enum", location="26.009"
    ),
    "reconciliation.final_market_value": G("2-134", "OpinionOfValueAmount", rule="amount"),
    "reconciliation.effective_date_of_appraisal": G(
        "2-135", "AppraisalReportEffectiveDate", rule="date"
    ),
    # Cost approach
    "cost_approach.site_value": G(
        "3-14",
        "SiteEstimatedValueAmount",
        rule="amount",
        gate=("SiteValueIndicator", "CostApproachIndicator"),
    ),
    "cost_approach.dwelling_cost": A(
        "25.009",
        "ReplacementOrReproductionCostAmount",
        rule="amount",
        where=_ABOVE_GRADE_ROW,
        scope="dwelling_cost",
        gate=_COST,
        note="the dwelling's standard above-grade area row (sq ft x price): G-1 maps 1004 3-17 "
        "to the dwelling's total, which also holds its other area rows",
    ),
    "cost_approach.dwelling_sqft": G(
        "3-15",
        "ReplacementOrReproductionCostAreaMeasure",
        rule="number",
        where=_ABOVE_GRADE_ROW,
        scope="dwelling_cost",
        gate=_COST,
    ),
    "cost_approach.dwelling_cost_per_sqft": G(
        "3-16",
        "PricePerAreaAmount",
        rule="number",
        where=_ABOVE_GRADE_ROW,
        scope="dwelling_cost",
        gate=_COST,
    ),
    "cost_approach.basement_sqft": G(
        "3-19",
        "ReplacementOrReproductionCostAreaMeasure",
        rule="sum",
        where=_BASEMENT_ROW,
        scope="dwelling_cost",
        gate=_COST,
        note="the 1004's second cost row, read as the dwelling's below-grade area rows",
    ),
    "cost_approach.basement_cost_per_sqft": G(
        "3-20",
        "PricePerAreaAmount",
        rule="single",
        where=_BASEMENT_ROW,
        scope="dwelling_cost",
        gate=_COST,
    ),
    "cost_approach.basement_cost": A(
        "25.009",
        "ReplacementOrReproductionCostAmount",
        rule="sum",
        where=_BASEMENT_ROW,
        scope="dwelling_cost",
        gate=_COST,
    ),
    "cost_approach.garage_sqft": A(
        "25.007",
        "ReplacementOrReproductionCostAreaMeasure",
        rule="sum",
        scope="garage_cost",
        gate=_COST,
        note="G-1 maps 1004 3-24 to the subject's car-storage area (13.003), not to a cost "
        "row; read here as the cost rows of a garage: an area type Attached Garage, Built-in "
        "Garage or Carport, or Other described as a garage, on any costed improvement",
    ),
    "cost_approach.garage_cost_per_sqft": A(
        "25.008",
        "PricePerAreaAmount",
        rule="single",
        scope="garage_cost",
        gate=_COST,
    ),
    "cost_approach.garage_cost": A(
        "25.009",
        "ReplacementOrReproductionCostAmount",
        rule="sum",
        scope="garage_cost",
        gate=_COST,
    ),
    "cost_approach.mechanicals_misc": UNRESOLVED(
        "G-1 maps the 1004 extras row (3-22/3-23) to the cost commentary and the per-"
        "improvement totals: UAD 3.6 has no mechanicals/misc cost line"
    ),
    "cost_approach.quality_rating_from_cost_service": G(
        "3-10", "DataSourceQualityRatingCode", scope="cost_data_source", gate=_COST
    ),
    "cost_approach.effective_date_of_cost_data": G(
        "3-11", "DataSourceEffectiveDate", rule="month", scope="cost_data_source", gate=_COST
    ),
    "cost_approach.cost_data_source": A(
        "25.052", "DataSourceName", scope="cost_data_source", gate=_COST
    ),
    "cost_approach.total_cost_new": NONE(
        "UAD 3.6 prints a cost-new total per improvement (A-1 25.018 / 25.036) and no "
        "'Total Estimate of Cost-New' line; G-1 maps 1004 3-35 to the per-improvement totals "
        "('many to one'), whose sum the report does not print"
    ),
    "cost_approach.depreciation_physical": A(
        "25.011", "DepreciationPhysicalAmount", rule="sum", scope="all_costs", gate=_COST
    ),
    "cost_approach.depreciation_functional": A(
        "25.013", "DepreciationFunctionalAmount", rule="sum", scope="all_costs", gate=_COST
    ),
    "cost_approach.depreciation_external": A(
        "25.015", "DepreciationExternalAmount", rule="sum", scope="all_costs", gate=_COST
    ),
    "cost_approach.depreciation": A(
        "25.016", "DepreciationTotalAmount", rule="sum", scope="all_costs", gate=_COST
    ),
    "cost_approach.depreciated_cost_improvements": A(
        ("25.001", "25.002"),
        "DepreciatedCostDwellingsTotalAmount",
        "DepreciatedCostOutbuildingsTotalAmount",
        rule="sum",
        gate=_COST,
        note="dwellings plus outbuildings (UAD 3.6 splits the 1004 line in two)",
    ),
    "cost_approach.site_improvements_value": G(
        "3-36", "SiteOtherImprovementsAsIsAmount", rule="amount", gate=_COST
    ),
    "cost_approach.indicated_value": G(
        "3-37", "ValueIndicatedByCostApproachAmount", rule="amount", gate=_COST
    ),
    "cost_approach.remaining_economic_life": G(
        "3-13", "EstimatedRemainingEconomicLifeYearsCount", rule="number", where=_DWELLING
    ),
    # Appraiser certification
    "appraiser.name": G(
        "6-5", *_NAME[:4], rule="name", where=_role("Appraiser"), location="Appraiser Name"
    ),
    "appraiser.company": G(
        "6-6", "AppraiserCompanyName", where=_role("Appraiser"), location="2.019"
    ),
    "appraiser.license_number": G(
        "6-14", "LicenseIdentifier", where=_role("Appraiser"), location="2.025"
    ),
    "appraiser.license_state": G(
        "6-17", "LicenseIssuingAuthorityStateCode", where=_role("Appraiser"), location="2.026"
    ),
    "appraiser.signature_date": G(
        "6-11", "ExecutionDate", rule="date", scope="appraiser_signatory", location="2200.0002"
    ),
    "appraiser.effective_date": NONE(
        "G-1 maps 1004 6-12 and 2-135 to one data point (AppraisalReportEffectiveDate, "
        "reconciliation.effective_date_of_appraisal); A-1 prints it only in the Summary and "
        "Reconciliation (1.003, 26.011): the redesigned URAR has no certification-page box"
    ),
    "appraiser.appraised_value": NONE(
        "G-1 maps 1004 6-21 and 2-134 to one data point (OpinionOfValueAmount, "
        "reconciliation.final_market_value); A-1 prints it only in the Summary and "
        "Reconciliation (1.001, 26.007): the redesigned URAR has no certification-page box"
    ),
}

# Roles a client party plays (A-1 2.011 Client/[Party Role Type]).
CLIENT_ROLE_UID: Final = "2400.0017"


# --------------------------------------------------------------------------- resolve


@dataclass
class Specs:
    a1: list[SpecRow]
    g1: list[SpecRow]
    enums: dict[str, list[tuple[str, str, str]]]

    def __post_init__(self) -> None:
        self.a1_by_uid = {row.uid: row for row in self.a1}

    def enum_codes(self, point: str, uid: str | None = None) -> dict[str, str]:
        """``{enumeration: display text}`` of ``point`` (``uid``'s own list when A-1 has one,
        else every list A-1 gives that data point)."""
        lists = [self.enums.get(uid, [])] if uid else []
        if not any(p == point for p, _, _ in (lists[0] if lists else [])):
            lists = list(self.enums.values())
        out: dict[str, str] = {}
        for items in lists:
            for name, code, display in items:
                if name == point and code:
                    # the cell may carry a check mark glyph on a second line
                    out.setdefault(code, display.split("\n")[0].strip())
        return out


class SpecError(ValueError):
    pass


def _subject_or_report(row: SpecRow) -> bool:
    return row.subject or row.report_level


def _g1_rows(specs: Specs, ref: str, point: str, location: str | None) -> list[SpecRow]:
    rows = [
        row
        for row in specs.g1
        if ref in row.legacy_ids and point in (row.point, row.attribute) and _subject_or_report(row)
    ]
    if location is not None and len(rows) > 1:
        rows = [r for r in rows if r.uid == location or any(location in x for x in r.locations)]
    return rows


def _a1_rows(specs: Specs, ref: str, point: str, location: str | None) -> list[SpecRow]:
    rows = [
        row
        for row in specs.a1
        if point in (row.point, row.attribute)
        and _subject_or_report(row)
        and any(line.startswith(f"{ref}:") for line in row.locations)
    ]
    if location is not None and len(rows) > 1:
        rows = [r for r in rows if r.uid == location or any(location in x for x in r.locations)]
    return rows


def captions_of(locations: Iterable[str], *, grid: bool = False) -> list[str]:
    """The report captions of A-1 Report Location lines ("4.105: Site - Site Valuation
    Methodology - Opinion of Site Value" -> "Opinion of Site Value"): the subject sections
    first, the summary page (section 1) after, the comparable grids only with ``grid`` (a
    building total printed only in the grid's subject column)."""
    main: list[str] = []
    summary: list[str] = []
    for line in locations:
        fid, _, rest = line.partition(":")
        section = fid.strip().split(".")[0]
        parts = [p.strip() for p in rest.split(" - ")]
        if not rest or (section in _GRID_SECTIONS and not grid) or len(parts) < 2:
            continue
        label = _clean_label(parts[-1])
        if not label and len(parts) >= 3:  # "{No Label}": the subsection names the box
            label = _clean_label(parts[-2])
        if not label:
            continue
        if section != "1" and label not in main:
            main.append(label)
        elif section == "1" and label not in summary:
            summary.append(label)
    return main + [label for label in summary if label not in main]


def _clean_label(text: str) -> str:
    label = re.sub(r"\[[^\]]*\]|\{[^}]*\}", "", text).strip(" -_")
    return re.sub(r"\s+", " ", label)


def locations_of(locations: Iterable[str]) -> list[tuple[str, str, str, str]]:
    """``(field id, section, subsection, label)`` of each A-1 Report Location line, the label
    cleaned as :func:`captions_of` does (an unlabelled box takes its subsection's last part),
    the subsection's parts joined by " - " ("" for ``{No Subsection}``). Every section,
    grids included: the PDF caption mapping scopes a label by the section it is printed in."""
    out: list[tuple[str, str, str, str]] = []
    for line in locations:
        fid, _, rest = line.partition(":")
        parts = [p.strip() for p in rest.split(" - ")]
        if not rest.strip() or len(parts) < 2 or not fid.strip()[:1].isdigit():
            continue
        section = _clean_label(parts[0])
        middle = [_clean_label(p) for p in parts[1:-1]]
        label = _clean_label(parts[-1]) or (middle[-1] if middle else "")
        if not label or not section:
            continue
        item = (fid.strip(), section, " - ".join(m for m in middle if m), label)
        if item not in out:
            out.append(item)
    return out


def report_sections(specs: Specs) -> tuple[tuple[int, str], ...]:
    """The URAR's numbered sections in report order (A-1 Report Location field ids number the
    sections as the report prints them: 1 Summary ... 29 Certifications)."""
    found: dict[int, str] = {}
    for row in specs.a1:
        for line in row.locations:
            fid, _, rest = line.partition(":")
            number = fid.strip().split(".")[0]
            name = _clean_label(rest.split(" - ")[0]) if rest.strip() else ""
            if number.isdigit() and name:
                found.setdefault(int(number), name)
    return tuple(sorted(found.items()))


def report_places(specs: Specs) -> tuple[tuple[int, str, str, str], ...]:
    """``(section number, section, subsection, label)`` of every box A-1 prints for the subject
    or the report: the boxes no catalogue key reads too, so the PDF caption mapping can tell
    that a caption sits in another subsection (the AMC's "ID" is not the appraiser's)."""
    places: set[tuple[int, str, str, str]] = set()
    for row in specs.a1:
        if not _subject_or_report(row):
            continue
        for fid, section, subsection, label in locations_of(row.locations):
            number = fid.split(".")[0]
            if number.isdigit():
                places.add((int(number), section, subsection, label))
    return tuple(sorted(places))


def report_labels(specs: Specs) -> tuple[str, ...]:
    """Every caption A-1 prints for the subject or the report (header and footer included),
    longest first: the delimiters of the PDF caption pass, where one caption's value ends at
    the next caption printed on the line."""
    labels: set[str] = set()
    for row in specs.a1:
        if not _subject_or_report(row):
            continue
        for line in row.locations:
            fid, _, rest = line.partition(":")
            parts = [p.strip() for p in rest.split(" - ")]
            if len(parts) < 2:
                continue
            label = _clean_label(parts[-1])
            if label and not label.startswith("<<"):
                labels.add(label)
    return tuple(sorted(labels, key=lambda label: (-len(label), label)))


# Qualifier values that are not printed next to the value (the party role, the improvement
# type): not used as captions.
_UNPRINTED_QUALIFIERS: Final = frozenset({"PartyRoleType", "ImprovementType"})


def _qualifier_captions(
    specs: Specs, where: Mapping[str, Mapping[str, tuple[str, ...]]]
) -> list[str]:
    """The display text of each ``where`` value ("Electricity", "Windows"): the row caption the
    URAR prints next to a value that A-1 labels only generically ("Public/Private", "Detail")."""
    out: list[str] = []
    for tests in where.values():
        for point, values in tests.items():
            if point in _UNPRINTED_QUALIFIERS:
                continue
            codes = specs.enum_codes(point)
            for value in values:
                text = codes.get(value, "")
                if text and text != _NOT_DISPLAYED and text not in out:
                    out.append(text)
    return out


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_label(text)).strip()


def _label_match(key: str, legacy_label: str) -> bool:
    spec = FIELD_BY_KEY[key]
    folded = _fold(legacy_label)
    return any(_fold(label) and _fold(label) in folded for label in spec.labels)


def resolve(specs: Specs, key: str, selector: Selector) -> dict[str, Any]:
    """One catalogue key's mapping entry (module doc)."""
    spec = FIELD_BY_KEY[key]
    entry: dict[str, Any] = {"label": spec.label, "type": spec.type}
    if selector.via in ("none", "unresolved"):
        hits = sorted(
            {
                line
                for row in specs.a1
                if _subject_or_report(row)
                for line in row.locations
                if any(_fold(label) in _fold(line) for label in spec.labels if len(label) > 3)
            }
        )
        entry.update(
            status="no_equivalent" if selector.via == "none" else "unresolved",
            reason=selector.note,
            a1_label_hits=tuple(hits[:6]),
        )
        return entry
    sources: list[dict[str, Any]] = []
    legacy: list[tuple[str, str]] = []
    locations: list[str] = []
    label_match = False
    for i, point in enumerate(selector.points):
        ref = selector.ref[min(i, len(selector.ref) - 1)]
        refs = selector.ref if selector.via == "g1" and len(selector.ref) > 1 else (ref,)
        rows: list[SpecRow] = []
        for candidate in refs:
            find = _g1_rows if selector.via == "g1" else _a1_rows
            rows = find(specs, candidate, point, selector.location)
            if rows:
                ref = candidate
                break
        if len(rows) != 1:
            uids = [r.uid for r in rows]
            raise SpecError(f"{key}: {selector.via} {ref} {point}: {len(rows)} rows {uids}")
        row = rows[0]
        a1 = specs.a1_by_uid.get(row.uid)
        if a1 is None:
            raise SpecError(f"{key}: {row.uid} is in G-1 but not in A-1")
        if a1.xpath != row.xpath:
            raise SpecError(f"{key}: {row.uid} xPath differs between A-1 and G-1")
        if selector.via == "g1":
            if (ref, row.legacy_label) not in legacy:
                legacy.append((ref, row.legacy_label))
            label_match = label_match or _label_match(key, row.legacy_label)
        locations.extend(x for x in a1.locations if x not in locations)
        element = a1.point or a1.attribute
        sources.append(
            {
                "uid": a1.uid,
                "path": a1.xpath + element,
                "attribute": a1.attribute.lstrip("@") if a1.point and a1.attribute else None,
                "format": a1.fmt,
                "enums": specs.enum_codes(element, a1.uid),
                "captions": tuple(captions_of(a1.locations)),
            }
        )
    extra_captions: list[str] = []
    extra_locations: list[str] = []
    for fid, point in selector.extra:
        rows = _a1_rows(specs, fid, point, None)
        if len(rows) != 1:
            raise SpecError(f"{key}: a1 {fid} {point}: {len(rows)} rows")
        row = rows[0]
        extra_captions.extend(captions_of(row.locations, grid=True))
        extra_locations.extend(row.locations)
        sources.append(
            {
                "uid": row.uid,
                "path": row.xpath + row.point,
                "attribute": row.attribute.lstrip("@") or None,
                "format": row.fmt,
                "enums": specs.enum_codes(row.point, row.uid),
                "captions": tuple(captions_of(row.locations, grid=True)),
            }
        )
    where = _resolve_where(specs, key, selector.where)
    for gate in selector.gate:
        if not any(r.point == gate for r in specs.a1):
            raise SpecError(f"{key}: gate {gate} is not an A-1 data point")
    entry.update(
        status="mapped",
        rule=selector.rule,
        sources=tuple(sources),
        where=where,
        scope=selector.scope,
        gate=selector.gate,
        captions=tuple(
            dict.fromkeys(
                [*_qualifier_captions(specs, where), *captions_of(locations), *extra_captions]
            )
        ),
        locations=tuple(locations_of([*locations, *extra_locations])),
        legacy=tuple(legacy),
        label_match=label_match if selector.via == "g1" else None,
        via=f"{selector.via}:{','.join(selector.ref)}",
        note=selector.note or None,
    )
    return entry


def _resolve_where(specs: Specs, key: str, where: Where) -> dict[str, dict[str, tuple[str, ...]]]:
    """``where`` with each value checked against the data point's A-1 enumerations
    (``@client``: every role A-1 lists for a client party)."""
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for container, tests in where.items():
        if not any(f"/{container}/" in row.xpath for row in specs.a1):
            raise SpecError(f"{key}: no A-1 container {container}")
        resolved: dict[str, tuple[str, ...]] = {}
        for point, wanted in tests.items():
            codes = specs.enum_codes(point)
            if any(r.point == point and r.fmt.startswith("Boolean") for r in specs.a1):
                codes = {"true": "true", "false": "false"}
            values = (wanted,) if isinstance(wanted, str) else tuple(wanted)
            if values == ("@client",):
                values = tuple(specs.enum_codes(point, CLIENT_ROLE_UID))
                values = tuple(v for v in values if v not in ("Borrower", "PropertyOwner"))
            missing = [v for v in values if v not in codes]
            if missing:
                raise SpecError(f"{key}: {point} has no A-1 enumeration {missing}")
            resolved[point] = values
        out[container] = resolved
    return out


def build(specs: Specs) -> dict[str, dict[str, Any]]:
    missing = [spec.key for spec in FIELDS if spec.key not in SELECTORS]
    extra = sorted(set(SELECTORS) - {spec.key for spec in FIELDS})
    if missing or extra:
        raise SpecError(f"selectors missing {missing}, unknown {extra}")
    return {spec.key: resolve(specs, spec.key, SELECTORS[spec.key]) for spec in FIELDS}


# --------------------------------------------------------------------------- render

_HEADER: Final = '''"""UAD 3.6 (MISMO 3.6) -> catalogue mapping. GENERATED, do not edit by hand.

Written by ``python -m evals.appraisal.uad36_spec`` from the GSE URAR Delivery Specification
(Appendix A-1 v1.4) and the Redesign-to-Legacy Cross Reference Guide (Appendix G-1 v1.4); the
reviewed join between the catalogue and the specs is ``SELECTORS`` in that module, and each
entry here is traceable to its A-1 Unique IDs (``sources[].uid``) and G-1 legacy 1004 fields
(``legacy``).

Per catalogue key (:data:`basis.extraction.field_mappings.FIELDS`):

* ``status``: ``mapped``; ``no_equivalent`` (the redesigned URAR does not carry the 1004 box,
  ``reason``); ``unresolved`` (a related data point exists but none carries the box, ``reason``).
* ``sources``: the MISMO data points read, each with its A-1 Unique ID, full xPath from
  ``MESSAGE``, the attribute holding its unit (``AreaUnitOfMeasureType``), A-1 format type, its
  enumerations with the A-1 Output Report Display Text (``<<Does Not Display>>`` for an
  ``Other`` whose text is in the companion ``...OtherDescription``) and the captions it is
  printed under (a reader that combines several printed values finds each by its own).
* ``rule``: how :mod:`basis.extraction.mismo36` turns the values into the field;
  ``where``: per repeating container, the enumeration values a sibling data point must hold;
  ``scope``: a container the reader resolves through the document's relationships;
  ``gate``: Scope of Work indicators, one of which must not be ``false`` (an approach not
  developed leaves the key empty).
* ``captions``: the URAR captions (A-1 Report Location labels) the value is printed next to,
  used to pick which occurrence on the PDF to highlight.
* ``locations``: each A-1 Report Location of the key's data points as ``(field id, section,
  subsection, label)``: where the redesigned URAR prints the value, which the PDF extraction
  of a UAD 3.6 report maps captions by (:mod:`basis.extraction.uad36_pdf`).

:data:`SECTIONS` are the report's numbered sections in print order, :data:`LABELS` every
caption A-1 prints for the subject or the report (longest first) and :data:`PLACES` every such
box as ``(section number, section, subsection, label)``.
"""

from __future__ import annotations

from typing import Any, Final

'''


def render(
    mapping: Mapping[str, Mapping[str, Any]],
    versions: Mapping[str, str],
    sections: Sequence[tuple[int, str]] = (),
    labels: Sequence[str] = (),
    places: Sequence[tuple[int, str, str, str]] = (),
) -> str:
    lines = [_HEADER, f"SPEC_FILES: Final = {dict(versions)!r}\n\n"]
    lines.append(f"SECTIONS: Final[tuple[tuple[int, str], ...]] = {tuple(sections)!r}\n\n")
    lines.append(f"LABELS: Final[tuple[str, ...]] = {tuple(labels)!r}\n\n")
    lines.append(f"PLACES: Final[tuple[tuple[int, str, str, str], ...]] = {tuple(places)!r}\n\n")
    lines.append("MAPPING: Final[dict[str, dict[str, Any]]] = {\n")
    for key, entry in mapping.items():
        lines.append(f"    {key!r}: {dict(entry)!r},\n")
    lines.append("}\n")
    return "".join(lines)


def _format(source: str) -> str:
    """The module as ``ruff format`` writes it (the committed file is formatted)."""
    done = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", str(OUTPUT), "-"],
        input=source,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def load_specs(directory: Path | None = None) -> tuple[Specs, dict[str, str]]:
    forms = data_dir(directory) / "appraisal" / "forms"
    a1_path, g1_path = forms / A1_FILE, forms / G1_FILE
    specs = Specs(load_a1(a1_path), load_g1(g1_path), load_enums(a1_path))
    return specs, {"a1": f"{A1_FILE} [{A1_SHEET}]", "g1": f"{G1_FILE} [{G1_SHEET}]"}


def summary(mapping: Mapping[str, Mapping[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for entry in mapping.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    unmatched = [
        k for k, e in mapping.items() if e["status"] == "mapped" and e.get("label_match") is False
    ]
    return (
        f"{len(mapping)} keys: "
        + ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        + f"; G-1 joins whose 1004 label differs from the catalogue label: {unmatched}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail when the module is stale")
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    specs, versions = load_specs(args.data_dir)
    mapping = build(specs)
    rendered = _format(
        render(
            mapping,
            versions,
            report_sections(specs),
            report_labels(specs),
            report_places(specs),
        )
    )
    print(summary(mapping))
    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != rendered:
            print(f"{OUTPUT} is stale; run: python -m evals.appraisal.uad36_spec")
            return 1
        return 0
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
