"""Synthetic legacy URAR 1004s (Freddie Mac Form 70) with exact truth (``SPEC.md`` section 6).

The blank Form 70 (``forms/form70_blank.pdf``: 6 pages, 612 x 1008 pt, a text layer, no
AcroForm) is filled the way appraisal software prints it: values drawn in a plain font
(Helvetica) at measured positions next to or under the printed captions, no
``Caption:`` colons, checkboxes marked with an ``X`` glyph inside the box square, ``$`` and
commas, dates ``MM/DD/YYYY``, the cost section's ``=$ 572,831`` and ``=$( 156,240)``, a site
area in acres or square feet, UAD baths (``2.1``). Optional pages after page 6: a
supplemental addendum and a subject photo page whose header block repeats rooms, GLA,
site, view and quality (the ``also_on`` pages of those keys).

Three parts, each testable on its own:

* **Plan** (:func:`plan_document`, no PDF needed): a seeded, internally consistent property
  and, for every catalogue key, its :class:`FieldPlan` (status, type, normalized value,
  printed text, page, ``also_on``), plus the non-catalogue text that makes the form look
  filled (comparables, narrative). Profiles (:func:`assign_profiles`) choose the edge
  cases per document and are recorded in the truth ``note``.
* **Layout** (:data:`SLOTS`, :data:`CHECKS`, :class:`FormGeometry`): where each box is.
  Each slot names its anchor caption and the anchor's measured position on the blank form;
  :class:`FormGeometry` finds that caption in the form's text layer (``pdfplumber``) and
  takes the baseline and x from it, and finds each checkbox square among the form's
  rectangles (checking the caption printed right of it). A form that moved a caption fails
  loudly instead of printing in the wrong box.
* **Render and verify** (:func:`render_document`, :func:`verify_document`): draw with
  ``pymupdf``, re-open the result with ``pdfplumber`` and check every present field's
  printed text is on its truth page (and its ``also_on`` pages), every intended ``X`` is
  inside its square and no other square of the group is marked, and no drawn value
  overlaps a form caption or another value. Truth quotes are spans of the generated text
  layer. ``scan`` documents are rasterized afterwards (no text layer); their quotes come
  from the text version that was rasterized.

Fabricated data only: names, streets and companies are invented; phone numbers are 555.

``python -m evals.appraisal.synth_1004 --count 24 --seed 7 [--scans 3] [--out DIR]``
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import random
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from evals.appraisal.truth import (
    CATALOGUE_KEYS,
    DocumentTruth,
    ExpectedIssue,
    FieldTruth,
    Status,
    TruthSet,
    TruthType,
    appraisal_dir,
    dump_truth,
)
from evals.datasets import Split

SET_NAME: Final = "synthetic_1004"
FORM_FILE: Final = "forms/form70_blank.pdf"
FORM_PAGES: Final = 6
FORM_ID: Final = "urar_1004_2005"
PAGE_W: Final = 612.0
LETTER_H: Final = 792.0  # appended pages (addendum, photos) are letter size
FONT: Final = "helv"
FONT_BOLD: Final = "hebo"
VALUE_SIZE: Final = 8.0
MIN_SIZE: Final = 5.5
WORD_GAP: Final = 3.6  # > pdfplumber's x_tolerance (3), so a value is its own word
ARIAL_DESCENT: Final = 0.211  # baseline = pdfplumber "bottom" - descent * size (measured)

# Keys whose box Form 70 does not have. Everything else in the catalogue is fillable.
ABSENT_KEYS: Final[dict[str, str]] = {
    "listing_and_contract.sale_type": "Form 70 has no sale-type box for the subject "
    "(the sales grid's sale-type row is for comparables only)",
}

# --------------------------------------------------------------------------- profiles


# Planted errors: the cost section's indicated value or its total cost-new does not add
# up, or the reconciliation copies a different cost-approach value.
Planted = Literal["cost_indicated_total", "cost_new_total", "reconciliation_mismatch"]


@dataclass(frozen=True, slots=True)
class Profile:
    assignment: Literal["purchase", "refinance"] = "purchase"
    cost_developed: bool = True
    site_value_printed: bool = True  # only meaningful when the cost approach is not developed
    income_zero: bool = False  # reconciliation income box printed "0" (else blank)
    half_bath: bool = False
    site_acres: bool = False
    multi_car_storage: bool = False
    heating_other: bool = False
    foundation_multi: bool = False
    planted: Planted | None = None
    photo_page: bool = False
    addendum: bool = False
    long_legal: bool = False
    unit_suffixes: bool = False
    scan: bool = False
    split: Split = "dev"

    def describe(self) -> str:
        parts = [
            f"assignment={self.assignment}",
            "cost=developed" if self.cost_developed else "cost=not_developed",
        ]
        if not self.cost_developed:
            parts.append("site_value=" + ("printed" if self.site_value_printed else "blank"))
        parts.append("income=" + ("printed_0" if self.income_zero else "blank"))
        parts.append("baths=" + ("half" if self.half_bath else "full_only"))
        parts.append("site_area=" + ("acres" if self.site_acres else "sqft"))
        for flag, name in (
            (self.multi_car_storage, "car_storage=multi"),
            (self.heating_other, "heating=other"),
            (self.foundation_multi, "foundation=multi"),
            (self.photo_page, "photo_page"),
            (self.addendum, "addendum"),
            (self.long_legal, "legal=long"),
            (self.unit_suffixes, "values_with_units"),
            (self.scan, "scan"),
        ):
            if flag:
                parts.append(name)
        if self.planted:
            parts.append(f"planted={self.planted}")
        return "profile: " + ", ".join(parts)


# Fraction of documents that get each edge case (at least one document each when n >= 2).
PROFILE_RATES: Final[dict[str, float]] = {
    "cost_not_developed": 0.2,
    "site_value_blank": 0.6,  # of the documents without a cost approach
    "income_zero": 0.5,
    "half_bath": 0.5,
    "site_acres": 0.3,
    "multi_car_storage": 0.25,
    "heating_other": 0.2,
    "foundation_multi": 0.2,
    "cost_arithmetic": 0.12,  # of the documents with a cost approach
    "reconciliation_mismatch": 0.12,
    "photo_page": 0.35,
    "addendum": 0.35,
    "long_legal": 0.2,
    "unit_suffixes": 0.25,
    "refinance": 0.17,
}


def assign_profiles(count: int, scans: int, seed: int) -> list[Profile]:
    """One profile per document: ``count`` text documents, then ``scans`` scan documents.

    Every edge case is given to ``max(1, round(rate * n))`` documents drawn without
    replacement, so even a two-document run covers every profile it can.
    """
    total = count + scans
    rng = random.Random(f"synth_1004:{seed}:profiles")
    everyone = list(range(total))

    def pick(rate: float, pool: Sequence[int]) -> set[int]:
        if not pool:
            return set()
        k = min(len(pool), max(1, round(rate * len(pool))))
        return set(rng.sample(list(pool), k))

    r = PROFILE_RATES
    not_dev = pick(r["cost_not_developed"], everyone) if total >= 2 else set()
    site_blank = pick(r["site_value_blank"], sorted(not_dev))
    developed = [i for i in everyone if i not in not_dev]
    arithmetic = pick(r["cost_arithmetic"], developed)
    mismatch = pick(r["reconciliation_mismatch"], [i for i in developed if i not in arithmetic])
    flags = {
        name: pick(r[name], everyone)
        for name in (
            "income_zero",
            "half_bath",
            "site_acres",
            "multi_car_storage",
            "heating_other",
            "foundation_multi",
            "photo_page",
            "addendum",
            "long_legal",
            "unit_suffixes",
            "refinance",
        )
    }
    profiles: list[Profile] = []
    for i in everyone:
        planted: Planted | None = None
        if i in arithmetic:  # the two arithmetic variants alternate
            planted = ("cost_indicated_total", "cost_new_total")[sorted(arithmetic).index(i) % 2]
        elif i in mismatch:
            planted = "reconciliation_mismatch"
        scan = i >= count
        profiles.append(
            Profile(
                assignment="refinance" if i in flags["refinance"] else "purchase",
                cost_developed=i not in not_dev,
                site_value_printed=i not in site_blank,
                income_zero=i in flags["income_zero"],
                half_bath=i in flags["half_bath"],
                site_acres=i in flags["site_acres"],
                multi_car_storage=i in flags["multi_car_storage"],
                heating_other=i in flags["heating_other"],
                foundation_multi=i in flags["foundation_multi"],
                planted=planted,
                photo_page=i in flags["photo_page"],
                addendum=i in flags["addendum"],
                long_legal=i in flags["long_legal"],
                unit_suffixes=i in flags["unit_suffixes"],
                scan=scan,
                split="regression" if not scan and (i + 1) % 8 == 0 else "dev",
            )
        )
    return profiles


def document_name(index: int, profile: Profile, count: int) -> str:
    if profile.scan:
        return f"syn1004_scan_{index - count + 1:02d}"
    return f"syn1004_{index + 1:03d}"


# --------------------------------------------------------------------------- plans


@dataclass(frozen=True, slots=True)
class FieldPlan:
    """Truth for one catalogue key, and what is drawn for it."""

    key: str
    status: Status
    type: TruthType | None = None
    value: Any = None
    printed: str | None = None
    page: int | None = None
    also_on: tuple[int, ...] = ()
    also_text: str | None = None  # the text found on the also_on pages (default: printed)
    note: str | None = None
    slot: str | None = None  # where ``printed`` is drawn; None = drawn inside a composite line
    checks: tuple[str, ...] = ()  # option codes marked in CHECKS[key]


@dataclass
class DocPlan:
    name: str
    index: int
    seed: int
    profile: Profile
    file_no: str
    fields: dict[str, FieldPlan]
    texts: dict[str, str]  # non-catalogue slot -> text
    checks: dict[str, tuple[str, ...]]  # non-catalogue check group -> marked codes
    blocks: dict[str, list[str]]  # free-text block -> lines
    grid: dict[tuple[str, str], str]  # (sales-grid row, column) -> text
    photo_block: list[tuple[str, str]]  # caption, value rows of the photo page header
    photo_header: list[tuple[str, str]]
    addendum_lines: list[str]
    expected_issues: list[ExpectedIssue]
    scan_dpi: int = 0
    scan_angle: float = 0.0

    @property
    def pages(self) -> int:
        return FORM_PAGES + int(self.profile.addendum) + int(self.profile.photo_page)

    @property
    def addendum_page(self) -> int | None:
        return FORM_PAGES + 1 if self.profile.addendum else None

    @property
    def photo_page(self) -> int | None:
        return self.pages if self.profile.photo_page else None


# Form labels of the checkbox options (printed right of each box), by catalogue key.
ENUM_LABELS: Final[dict[str, dict[str, str]]] = {
    "neighborhood.location": {"urban": "Urban", "suburban": "Suburban", "rural": "Rural"},
    "neighborhood.built_up": {"over_75": "Over 75%", "25_75": "25–75%", "under_25": "Under 25%"},
    "neighborhood.growth": {"rapid": "Rapid", "stable": "Stable", "slow": "Slow"},
    "neighborhood.property_values": {
        "increasing": "Increasing",
        "stable": "Stable",
        "declining": "Declining",
    },
    "neighborhood.demand_supply": {
        "shortage": "Shortage",
        "in_balance": "In Balance",
        "over_supply": "Over Supply",
    },
    "neighborhood.marketing_time": {
        "under_3_mths": "Under 3 mths",
        "3_6_mths": "3–6 mths",
        "over_6_mths": "Over 6 mths",
    },
    "subject.occupant": {"owner": "Owner", "tenant": "Tenant", "vacant": "Vacant"},
    "subject.property_rights_appraised": {
        "fee_simple": "Fee Simple",
        "leasehold": "Leasehold",
        "other": "Other (describe)",
    },
    "subject.assignment_type": {
        "purchase": "Purchase Transaction",
        "refinance": "Refinance Transaction",
        "other": "Other (describe)",
    },
    "site.zoning_compliance": {
        "legal": "Legal",
        "legal_nonconforming": "Legal Nonconforming (Grandfathered Use)",
        "no_zoning": "No Zoning",
        "illegal": "Illegal (describe)",
    },
    "improvements.units": {"one": "One", "one_with_accessory_unit": "One with Accessory Unit"},
    "improvements.type": {"det": "Det.", "att": "Att.", "s_det_end_unit": "S-Det./End Unit"},
    "improvements.foundation_type": {
        "concrete_slab": "Concrete Slab",
        "crawl_space": "Crawl Space",
        "full_basement": "Full Basement",
        "partial_basement": "Partial Basement",
    },
    "improvements.heating_type": {
        "fwa": "FWA",
        "hwbb": "HWBB",
        "radiant": "Radiant",
        "other": "Other",
    },
    "improvements.cooling": {
        "central": "Central Air Conditioning",
        "individual": "Individual",
        "other": "Other",
    },
    "improvements.car_storage": {
        "none": "None",
        "driveway": "Driveway",
        "garage": "Garage",
        "carport": "Carport",
        "attached": "Att.",
        "detached": "Det.",
        "built_in": "Built-in",
    },
    "improvements.appliances": {
        "refrigerator": "Refrigerator",
        "range_oven": "Range/Oven",
        "dishwasher": "Dishwasher",
        "disposal": "Disposal",
        "microwave": "Microwave",
        "washer_dryer": "Washer/Dryer",
        "other": "Other (describe)",
    },
    "reconciliation.appraisal_made": {
        "as_is": "as is",
        "subject_to_completion": "subject to completion per plans and specifications",
        "subject_to_repairs": "subject to the following repairs or alterations",
        # The caption wraps ("subject to the" / "following required inspection") around the
        # form's vertical section letters; printed is the line the text layer keeps whole.
        "subject_to_inspection": "following required inspection",
    },
    "site.highest_and_best_use": {"yes": "Yes", "no": "No"},
    "site.special_flood_hazard": {"yes": "Yes", "no": "No"},
    "site.utilities_electric": {"public": "Public", "other": "Other (describe)"},
    "site.utilities_gas": {"public": "Public", "other": "Other (describe)"},
    "site.utilities_water": {"public": "Public", "other": "Other (describe)"},
    "site.utilities_sewer": {"public": "Public", "other": "Other (describe)"},
    "site.off_site_improvements_street": {"public": "Public", "private": "Private"},
    "site.off_site_improvements_alley": {"public": "Public", "private": "Private"},
}

# Appliances use codes outside SPEC section 4's table (the normalizer must add them).
APPLIANCE_CODES: Final = tuple(ENUM_LABELS["improvements.appliances"])

FIRST: Final = (
    "Avery Brooke Calder Dana Ellis Fiona Grant Harper Imogen Jonah Keira Landon Mara "
    "Nolan Odette Pierce Quinn Rowan Sabine Tobias Uma Vance Willa Xander Yara Zane"
).split()
LAST: Final = (
    "Ashworth Bellamy Corrigan Delacroix Everhart Fairbanks Gallow Hollister Ingram "
    "Jessup Kettering Lindqvist Marchetti Northcutt Oakhurst Pemberton Quarles Redfern "
    "Stanhope Thistlewood Underhill Vossberg Whitlock Yarborough Zellner"
).split()
STREETS: Final = (
    "Maple Grove|Juniper Hollow|Quail Run|Larkspur|Copper Kettle|Thornbury|Willow Bend|"
    "Saddle Creek|Briar Patch|Foxglove|Heron Point|Amberwood|Cobble Hill|Partridge|"
    "Silver Birch|Wren Meadow|Ironstone|Tamarack"
).split("|")
SUFFIXES: Final = ("St", "Ave", "Ln", "Dr", "Ct", "Way", "Rd", "Pl", "Cir", "Trl")
PLACES: Final = (
    ("Brookhollow", "Wexmoor", "OH"),
    ("Cedar Mill", "Pellham", "IN"),
    ("Fairhaven", "Kestrel", "MI"),
    ("Larkfield", "Dunmore", "PA"),
    ("Oak Hollow", "Ravensworth", "TN"),
    ("Millbrook Springs", "Ashcombe", "NC"),
    ("Riverton Heights", "Galloway", "GA"),
    ("Stonebridge", "Halvern", "TX"),
    ("Willowmere", "Brackenridge", "CO"),
    ("Sunmesa", "Corrado", "AZ"),
)
LENDERS: Final = (
    "Bluebird Home Lending LLC",
    "Cedar Ridge Mortgage Co.",
    "Harborlight Federal Credit Union",
    "Northgate Residential Funding",
    "Prairie Lantern Bank",
    "Summit Arch Mortgage Corp.",
)
COMPANIES: Final = (
    "Keystone Valuation Group LLC",
    "Granite Line Appraisal Services",
    "Tallgrass Real Estate Appraisals",
    "Meridian Point Valuations Inc.",
    "Old Mill Appraisal Co.",
)
SUBDIVISIONS: Final = (
    "Maple Grove Estates",
    "Quail Run Village",
    "Heron Point",
    "Willow Bend Park",
    "Copper Kettle Farms",
    "Amberwood Commons",
)
BEARINGS: Final = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
CONDITION_NOTES: Final = (
    "No updates in the prior 15 years",
    "Kitchen-updated-one to five years ago",
    "Bathrooms-updated-six to ten years ago",
)
DESIGNS: Final = (
    "Ranch",
    "Colonial",
    "Cape Cod",
    "Craftsman",
    "Split Level",
    "Traditional",
    "Contemporary",
)
NARRATIVE: Final = (
    "The subject is located in an established residential area with good access to "
    "schools, shopping and employment.",
    "Marketing times are typical and supply and demand appear to be in balance.",
    "No adverse easements, encroachments or environmental conditions were noted.",
    "The subject conforms to the neighborhood in age, style, condition and use.",
    "Comparable sales were selected from the subject's market area within the past twelve months.",
    "Adjustments were derived from paired sales and market extraction.",
)


def _money(amount: int | float) -> str:
    if isinstance(amount, float) and not float(amount).is_integer():
        return f"{amount:,.2f}"
    return f"{int(amount):,}"


def _date(day: date) -> str:
    return day.strftime("%m/%d/%Y")


def _round(value: float, step: int) -> int:
    return int(round(value / step) * step)


def _signed(amount: int) -> str:
    if amount == 0:
        return "0"
    return f"{'+' if amount > 0 else '-'}{abs(amount):,}"


class _Fields:
    """Collects one FieldPlan per catalogue key."""

    def __init__(self) -> None:
        self.plans: dict[str, FieldPlan] = {}

    def present(
        self,
        key: str,
        type_: TruthType,
        value: Any,
        printed: str | None,
        page: int,
        *,
        slot: str | None = "",
        checks: tuple[str, ...] = (),
        also_on: Sequence[int] = (),
        also_text: str | None = None,
        note: str | None = None,
    ) -> None:
        self._add(
            FieldPlan(
                key=key,
                status="present",
                type=type_,
                value=value,
                printed=printed,
                page=page,
                also_on=tuple(sorted({p for p in also_on if p != page})),
                also_text=also_text,
                note=note,
                slot=key if slot == "" else slot,
                checks=checks,
            )
        )

    def not_developed(
        self, key: str, page: int, printed: str | None = None, note: str | None = None
    ) -> None:
        self._add(
            FieldPlan(
                key=key,
                status="not_developed",
                printed=printed,
                page=page,
                note=note,
                slot=key if printed is not None else None,
            )
        )

    def absent(self, key: str, note: str) -> None:
        self._add(FieldPlan(key=key, status="absent", note=note))

    def enum(
        self,
        key: str,
        code: str,
        page: int,
        also_on: Sequence[int] = (),
    ) -> None:
        self.present(
            key,
            "enum",
            code,
            ENUM_LABELS[key][code],
            page,
            slot=None,
            checks=(code,),
            also_on=also_on,
        )

    def multi(self, key: str, codes: Sequence[str], page: int) -> None:
        ordered = tuple(sorted(codes))
        self.present(
            key,
            "enum_multi",
            list(ordered),
            "; ".join(ENUM_LABELS[key][c] for c in ordered),
            page,
            slot=None,
            checks=ordered,
        )

    def boolean(self, key: str, value: bool, page: int) -> None:
        code = "yes" if value else "no"
        self.present(key, "bool", value, ENUM_LABELS[key][code], page, slot=None, checks=(code,))

    def _add(self, plan: FieldPlan) -> None:
        if plan.key not in CATALOGUE_KEYS:
            raise KeyError(f"{plan.key} is not a catalogue key")
        if plan.key in self.plans:
            raise KeyError(f"{plan.key} planned twice")
        self.plans[plan.key] = plan


def plan_document(index: int, profile: Profile, seed: int, count: int) -> DocPlan:
    """The property, every catalogue key's truth, and the decoration for one document."""
    rng = random.Random(f"synth_1004:{seed}:{index}")
    name = document_name(index, profile, count)
    f = _Fields()
    texts: dict[str, str] = {}
    xchecks: dict[str, tuple[str, ...]] = {}
    blocks: dict[str, list[str]] = {}
    grid: dict[tuple[str, str], str] = {}
    issues: list[ExpectedIssue] = []
    p_add = FORM_PAGES + 1 if profile.addendum else None
    p_photo = FORM_PAGES + int(profile.addendum) + 1 if profile.photo_page else None
    photo = [p_photo] if p_photo else []
    file_no = f"SYN{seed:02d}-{index + 1:04d}"
    purchase = profile.assignment == "purchase"

    # ---- people and place
    def person() -> str:
        return f"{rng.choice(FIRST)} {rng.choice(LAST)}"

    city, county, state = rng.choice(PLACES)
    street = f"{rng.randint(102, 9870)} {rng.choice(STREETS)} {rng.choice(SUFFIXES)}"
    zip_code = f"{rng.randint(10, 99)}{rng.randint(100, 999)}"
    city_line = f"{city}, {state} {zip_code}"
    borrower = person()
    owner = borrower if not purchase else person()
    lender = rng.choice(LENDERS)
    subdivision = rng.choice(SUBDIVISIONS)

    # ---- dates
    effective = date(2024, 3, 1) + timedelta(days=rng.randrange(0, 860))
    signed = effective + timedelta(days=rng.randint(1, 9))
    contract_day = effective - timedelta(days=rng.randint(12, 55))
    dom = rng.randint(4, 95)
    listing_day = contract_day - timedelta(days=dom)

    # ---- improvements
    year_built = rng.randint(1952, effective.year - 2)
    actual_age = effective.year - year_built
    effective_age = (
        actual_age if actual_age <= 5 else max(3, round(actual_age * rng.uniform(0.3, 0.75)))
    )
    total_life = rng.choice((60, 65, 70))
    remaining_life = total_life - effective_age
    bedrooms = rng.choice((2, 3, 3, 3, 4, 4, 5))
    full_baths = rng.randint(1, 3 if bedrooms >= 3 else 2)
    half_baths = 1 if profile.half_bath else 0
    rooms = bedrooms + rng.choice((3, 3, 4))
    rooms = max(rooms, bedrooms + full_baths + half_baths + 1)
    gla = int(rooms * rng.uniform(205, 285))
    stories = 1 if gla < 1450 or rng.random() < 0.35 else 2
    design = rng.choice(DESIGNS)
    design_printed = f"DT{stories};{design}" if rng.random() < 0.5 else design
    if profile.foundation_multi:
        foundation = rng.choice(
            (["crawl_space", "partial_basement"], ["concrete_slab", "partial_basement"])
        )
    else:
        foundation = [
            rng.choice(("concrete_slab", "concrete_slab", "crawl_space", "full_basement"))
        ]
    footprint = gla / stories
    basement = 0
    if "full_basement" in foundation:
        basement = _round(footprint * rng.uniform(0.9, 1.0), 10)
    elif "partial_basement" in foundation:
        basement = _round(footprint * rng.uniform(0.35, 0.65), 10)
    basement_finish = rng.choice((0, 25, 50, 75, 100)) if basement else 0
    quality = rng.choice(("Q3", "Q4", "Q4", "Q4", "Q5"))
    condition = "C2" if effective_age <= 5 else rng.choice(("C3", "C3", "C4"))
    rating = f"/{condition}" if rng.random() < 0.5 else ""
    heat_codes: list[str] = []
    heat_other: str | None = None
    if profile.heating_other:
        heat_other = rng.choice(("Heat Pump", "Infloor", "Mini-Split"))
        heat_codes = ["fwa"] if rng.random() < 0.5 else []
    else:
        heat_codes = [rng.choice(("fwa", "fwa", "fwa", "hwbb", "radiant"))]
    fuel = (
        "Electric"
        if heat_other in ("Heat Pump", "Mini-Split")
        else rng.choice(("Gas", "Gas", "Electric", "Oil", "Propane"))
    )
    cooling = rng.choice(("central", "central", "central", "individual"))
    fireplaces = rng.choice((0, 0, 1, 1, 2))
    patio = rng.choice(("Patio", "Deck", "CvPatio", "Patio", None))
    if profile.multi_car_storage:
        cars = rng.choice(
            (
                ["garage", "driveway", "carport", "detached"],
                ["garage", "driveway", "attached", "carport"],
                ["garage", "driveway", "attached", "detached"],
            )
        )
    else:
        cars = rng.choice(
            (
                ["garage", "driveway", "attached"],
                ["garage", "driveway", "attached"],
                ["garage", "driveway", "built_in"],
                ["carport", "driveway"],
                ["none"],
            )
        )
    garage_cars = rng.choice((1, 2, 2, 2, 3)) if "garage" in cars else 0
    driveway_cars = rng.choice((2, 2, 4)) if "driveway" in cars else 0
    carport_cars = rng.choice((1, 2)) if "carport" in cars else 0
    appliances = sorted(
        {"range_oven", "dishwasher"}
        | {
            c
            for c in ("refrigerator", "disposal", "microwave", "washer_dryer")
            if rng.random() < 0.6
        }
    )

    # ---- site
    if profile.site_acres:
        acres = round(rng.uniform(0.55, 4.8), 2)
        site_sqft = round(acres * 43560)
        site_printed = f"{acres:.2f} {rng.choice(('ac', 'ac', 'acres'))}"
        dimensions = rng.choice(("Irregular", "See Plat", "Irregular per plat"))
        shape = "Irregular"
    else:
        width = rng.choice((50, 60, 65, 70, 75, 80, 90, 100, 110))
        depth = rng.choice((100, 110, 120, 125, 130, 150, 165))
        site_sqft = width * depth
        acres = 0.0
        unit = rng.choice((" sf", " sf", " sq ft", ""))
        site_printed = f"{site_sqft:,}{unit}"
        dimensions = f"{width} x {depth}"
        shape = "Rectangular"
    view = rng.choice(("N;Res;", "N;Res;", "B;Wtr;", "N;Pstrl;", "B;Prk;"))
    location_rating = rng.choice(("N;Res;", "N;Res;", "B;BsyRd;"))

    # ---- values
    site_value = _round(
        rng.uniform(28000, 140000) if profile.site_acres else rng.uniform(22000, 95000), 1000
    )
    rate = round(rng.uniform(108, 196), rng.choice((0, 2)))
    dwelling_cost = round(gla * rate)
    basement_rate = round(rng.uniform(24, 58), 2) if basement else 0.0
    basement_cost = round(basement * basement_rate) if basement else 0
    garage_sqft = (
        (garage_cars * 240 + rng.choice((0, 24, 48)))
        if garage_cars
        else (carport_cars * 220 if carport_cars else 0)
    )
    garage_rate = round(rng.uniform(28, 55), rng.choice((0, 2))) if garage_sqft else 0.0
    garage_cost = round(garage_sqft * garage_rate) if garage_sqft else 0
    misc = _round(rng.uniform(3000, 15000), 100) if rng.random() < 0.6 else 0
    total_new = dwelling_cost + basement_cost + garage_cost + misc
    physical = round(total_new * effective_age / total_life)
    functional = _round(rng.uniform(2000, 9000), 100) if rng.random() < 0.15 else 0
    external = _round(rng.uniform(3000, 12000), 100) if rng.random() < 0.15 else 0
    depreciation = physical + functional + external
    depreciated = total_new - depreciation
    site_improvements = _round(rng.uniform(8000, 32000), 500)
    cost_indicated = depreciated + site_improvements + site_value
    if profile.cost_developed:
        sales_value = _round(cost_indicated * rng.uniform(0.95, 1.05), 1000)
    else:
        sales_value = _round(gla * rng.uniform(165, 310) + site_value, 1000)
    final_value = sales_value
    contract_price = _round(final_value * rng.uniform(0.975, 1.02), 500)
    offering_price = _round(contract_price, 1000) + rng.randint(1, 15) * 1000 - 100  # e.g. 439,900
    taxes: int | float = round(final_value * rng.uniform(0.008, 0.021))
    if rng.random() < 0.3:
        taxes = round(taxes + rng.random(), 2)
    hoa = rng.choice((0, 0, 0, 240, 360, 450))
    concessions = rng.choice((0, 0, 3000, 5000, 6000)) if purchase else 0

    p1, p2, p3, p6 = 1, 2, 3, 6

    # ---- subject
    f.present(
        "subject.property_address",
        "text",
        street,
        street,
        p1,
        also_on=[p2, p6, *photo],
    )
    f.present("subject.city", "text", city, city, p1, also_on=[p2, p6, *photo])
    f.present("subject.state", "text", state, state, p1, also_on=[p2, p6, *photo])
    f.present("subject.zip", "text", zip_code, zip_code, p1, also_on=[p2, p6, *photo])
    f.present("subject.borrower", "text", borrower, borrower, p1, also_on=photo)
    f.present("subject.owner_of_public_record", "text", owner, owner, p1)
    f.present("subject.county", "text", county, county, p1, also_on=photo)
    lot = rng.randint(1, 48)
    block = rng.randint(1, 9)
    if profile.long_legal:
        legal = (
            f"Lot {lot}, Block {block}, {subdivision} Phase {rng.choice(('II', 'III', 'IV'))}, "
            f"according to the plat thereof recorded in Plat Book {rng.randint(40, 190)}, "
            f"Page {rng.randint(1, 99)}, public records of {county} County, {state}"
        )
    else:
        legal = f"Lot {lot} Block {block} {subdivision}"
    f.present("subject.legal_description", "text", legal, legal, p1)
    parcel = "-".join(
        str(rng.randint(lo, hi)) for lo, hi in ((10, 99), (100, 999), (10, 99), (100, 999))
    )
    f.present("subject.assessors_parcel", "text", parcel, parcel, p1)
    tax_year = effective.year - (1 if effective.month < 7 else 0)
    f.present("subject.tax_year", "int", tax_year, str(tax_year), p1)
    f.present("subject.real_estate_taxes", "currency", taxes, _money(taxes), p1)
    f.present("subject.neighborhood_name", "text", subdivision, subdivision, p1)
    map_ref = rng.choice(
        (
            f"{rng.randint(10, 99)}-{rng.choice('ABCDEF')}{rng.randint(1, 9)}",
            f"{rng.randint(10000, 99999)}",
        )
    )
    f.present("subject.map_reference", "text", map_ref, map_ref, p1)
    tract = f"{rng.randint(1000, 9999)}.{rng.randint(0, 99):02d}"
    f.present("subject.census_tract", "text", tract, tract, p1)
    occupant = rng.choice(("owner", "owner", "vacant", "tenant")) if purchase else "owner"
    f.enum("subject.occupant", occupant, p1)
    f.present(
        "subject.special_assessments",
        "currency",
        0,
        "0",
        p1,
        note="$0 is a real value (SPEC principle 2)",
    )
    f.present("subject.hoa_dues", "currency", hoa, _money(hoa), p1)
    if hoa:
        xchecks["x.pud"] = ("yes",)
        xchecks["x.hoa_period"] = ("year",)
    rights = "leasehold" if rng.random() < 0.05 else "fee_simple"
    f.enum("subject.property_rights_appraised", rights, p1, also_on=[p2])
    f.enum("subject.assignment_type", "purchase" if purchase else "refinance", p1)
    f.present("subject.lender_client", "text", lender, lender, p1, also_on=[p6, *photo])
    texts["x.lender_address"] = (
        f"{rng.randint(100, 9999)} Commerce Pkwy, Suite {rng.randint(100, 900)}, "
        f"{rng.choice(PLACES)[0]}"
    )

    # ---- contract and listing
    mls = f"MLS #{rng.choice('ABCKLM')}{rng.randint(1000000, 9999999)}"
    if purchase:
        xchecks["x.offered"] = ("yes",)
        f.present(
            "listing_and_contract.offering_price",
            "currency",
            offering_price,
            f"${offering_price:,}",
            p1,
            slot=None,
        )
        f.present(
            "listing_and_contract.listing_date",
            "date",
            listing_day.isoformat(),
            _date(listing_day),
            p1,
            slot=None,
        )
        f.present("listing_and_contract.days_on_market", "int", dom, str(dom), p1, slot=None)
        texts["x.offered_report"] = (
            f"{mls}; listed {_date(listing_day)} at ${offering_price:,}; DOM {dom}; pending sale"
        )
        xchecks["x.contract_analyzed"] = ("did",)
        texts["x.contract_analysis"] = (
            "Arms-length sale; the contract was reviewed and no unusual terms were noted."
        )
        f.present(
            "listing_and_contract.contract_price",
            "currency",
            contract_price,
            _money(contract_price),
            p1,
            also_on=[p2, *photo],
        )
        f.present(
            "listing_and_contract.contract_date",
            "date",
            contract_day.isoformat(),
            _date(contract_day),
            p1,
        )
        f.present(
            "listing_and_contract.data_source", "text", "Purchase Contract", "Purchase Contract", p1
        )
        xchecks["x.seller_owner"] = ("yes",)
        xchecks["x.concessions"] = ("yes",) if concessions else ("no",)
        printed = f"${concessions:,}"
        f.present(
            "listing_and_contract.seller_concessions",
            "currency",
            concessions,
            printed,
            p1,
            slot=None,
            note=None if concessions else "$0 is a real value (SPEC principle 2)",
        )
        texts["x.concessions_line"] = (
            f"{printed} seller-paid closing costs, typical for the market"
            if concessions
            else printed
        )
    else:
        xchecks["x.offered"] = ("no",)
        texts["x.offered_report"] = (
            f"No listing of the subject found in the {mls[:3]} or public records "
            "in the past 12 months."
        )
        for key in (
            "listing_and_contract.offering_price",
            "listing_and_contract.listing_date",
            "listing_and_contract.days_on_market",
        ):
            f.not_developed(key, p1, note="not offered for sale in the prior 12 months")
        xchecks["x.contract_analyzed"] = ("did_not",)
        texts["x.contract_analysis"] = "Refinance transaction; there is no contract to analyze."
        for key in (
            "listing_and_contract.contract_price",
            "listing_and_contract.contract_date",
            "listing_and_contract.data_source",
            "listing_and_contract.seller_concessions",
        ):
            f.not_developed(key, p1, note="refinance: the contract section is left blank")
    f.absent("listing_and_contract.sale_type", ABSENT_KEYS["listing_and_contract.sale_type"])

    # ---- neighborhood
    f.enum("neighborhood.location", rng.choice(("suburban", "suburban", "urban", "rural")), p1)
    f.enum("neighborhood.built_up", rng.choice(("over_75", "over_75", "25_75", "under_25")), p1)
    f.enum("neighborhood.growth", rng.choice(("stable", "stable", "rapid", "slow")), p1)
    trend = rng.choice(("stable", "increasing", "increasing", "declining"))
    f.enum("neighborhood.property_values", trend, p1)
    f.enum(
        "neighborhood.demand_supply",
        rng.choice(("in_balance", "in_balance", "shortage", "over_supply")),
        p1,
    )
    f.enum(
        "neighborhood.marketing_time",
        rng.choice(("under_3_mths", "under_3_mths", "3_6_mths", "over_6_mths")),
        p1,
    )
    one_unit = rng.choice((65, 70, 75, 80, 85, 90))
    rest = 100 - one_unit
    two_four = rng.choice((0, 5)) if rest >= 10 else 0
    multi_family = rng.choice((0, 5)) if rest - two_four >= 10 else 0
    commercial = rng.choice((5, 10)) if rest - two_four - multi_family >= 10 else 0
    other_use = rest - two_four - multi_family - commercial
    for key, pct in (
        ("neighborhood.present_land_use_one_unit", one_unit),
        ("neighborhood.present_land_use_two_to_four", two_four),
        ("neighborhood.present_land_use_multi_family", multi_family),
        ("neighborhood.present_land_use_commercial", commercial),
        ("neighborhood.present_land_use_other", other_use),
    ):
        f.present(key, "percent", pct, str(pct), p1)
    low_k = _round(final_value * rng.uniform(0.55, 0.75) / 1000, 5)
    high_k = _round(final_value * rng.uniform(1.4, 1.9) / 1000, 5)
    pred_k = _round(final_value * rng.uniform(0.9, 1.1) / 1000, 5)
    age_low = rng.randint(1, 10)
    age_high = rng.randint(max(actual_age + 5, 40), 110)
    age_pred = rng.randint(max(age_low + 1, actual_age - 10), max(age_low + 2, actual_age + 10))
    for key, number in (
        ("neighborhood.price_low_thousands", low_k),
        ("neighborhood.price_high_thousands", high_k),
        ("neighborhood.price_predominant_thousands", pred_k),
        ("neighborhood.age_low", age_low),
        ("neighborhood.age_high", age_high),
        ("neighborhood.age_predominant", age_pred),
    ):
        f.present(key, "int", number, str(number), p1)
    boundaries = (
        f"North: {rng.choice(STREETS)} Rd; South: Hwy {rng.randint(10, 99)}; "
        f"East: {rng.choice(STREETS)} Ave; "
        f"West: {rng.choice(('river', 'rail line', 'county line'))}"
    )
    f.present("neighborhood.neighborhood_boundaries", "text", boundaries, boundaries, p1)
    blocks["x.nbhd_description"] = [rng.choice(NARRATIVE[:2]), NARRATIVE[3]]
    blocks["x.market_conditions"] = [
        f"Values have been {trend} over the past 12 months; "
        f"typical marketing time is {rng.randint(20, 90)} days.",
    ]

    # ---- site
    f.present("site.dimensions", "text", dimensions, dimensions, p1)
    area_value: Any = {"sqft": site_sqft, "printed_unit": "ac"} if profile.site_acres else site_sqft
    f.present("site.area_sqft", "area_sqft", area_value, site_printed, p1, also_on=[p2, *photo])
    f.present(
        "site.area",
        "text",
        site_printed,
        site_printed,
        p1,
        slot=None,
        also_on=[p2, *photo],
        note="same box as site.area_sqft (the value as printed)",
    )
    if profile.site_acres:
        f.present(
            "site.area_acres",
            "float",
            acres,
            site_printed,
            p1,
            slot=None,
            also_on=[p2, *photo],
            note="same box as site.area_sqft",
        )
    else:
        f.absent(
            "site.area_acres", "site area printed in square feet: no acres value on the document"
        )
    f.present("site.shape", "text", shape, shape, p1)
    f.present("site.view", "text", view, view, p1, also_on=[p2, *photo])
    zoning = rng.choice(("R-1", "RS-7", "SF-2", "R-2A", "RA"))
    f.present("site.zoning_classification", "text", zoning, zoning, p1)
    f.present(
        "site.zoning_description",
        "text",
        "Single Family Residential",
        "Single Family Residential",
        p1,
    )
    f.enum(
        "site.zoning_compliance", rng.choice(("legal", "legal", "legal", "legal_nonconforming")), p1
    )
    f.boolean("site.highest_and_best_use", True, p1)
    rural = profile.site_acres
    f.enum("site.utilities_electric", "public", p1)
    for key, other_text, chance in (
        ("site.utilities_gas", "Propane", 0.6 if rural else 0.1),
        ("site.utilities_water", "Private Well", 0.7 if rural else 0.05),
        ("site.utilities_sewer", "Septic", 0.7 if rural else 0.05),
    ):
        if key == "site.utilities_gas" and rng.random() < 0.08:
            f.present(
                key,
                "enum",
                "none",
                "None",
                p1,
                slot=key,
                note="no utility: None written, no box marked",
            )
        elif rng.random() < chance:
            f.present(
                key,
                "enum",
                {"code": "other", "text": other_text},
                other_text,
                p1,
                slot=key,
                checks=("other",),
            )
        else:
            f.enum(key, "public", p1)
    texts["x.street_type"] = rng.choice(("Asphalt", "Asphalt", "Concrete"))
    f.enum("site.off_site_improvements_street", "public", p1)
    if rng.random() < 0.7:
        texts["x.alley_type"] = "None"
        f.not_developed(
            "site.off_site_improvements_alley", p1, note="no alley: None written, no box marked"
        )
    else:
        texts["x.alley_type"] = rng.choice(("Asphalt", "Gravel"))
        f.enum("site.off_site_improvements_alley", rng.choice(("public", "private")), p1)
    flood = rng.random() < 0.12
    f.boolean("site.special_flood_hazard", flood, p1)
    zone = "AE" if flood else rng.choice(("X", "X", "X500"))
    f.present("site.flood_zone", "text", zone, zone, p1)
    fema = (
        f"{rng.randint(10, 56)}{rng.randint(100, 999)}C{rng.randint(1000, 9999)}"
        f"{rng.choice('DEFGH')}"
    )
    f.present("site.fema_map_number", "text", fema, fema, p1)
    fema_day = date(rng.randint(2008, 2021), rng.randint(1, 12), rng.randint(1, 28))
    f.present("site.fema_map_date", "date", fema_day.isoformat(), _date(fema_day), p1)
    xchecks["x.utilities_typical"] = ("yes",)
    xchecks["x.adverse"] = ("no",)
    blocks["x.site_comments"] = [NARRATIVE[2]]

    # ---- improvements (page 1)
    units = "one_with_accessory_unit" if rng.random() < 0.06 else "one"
    f.enum("improvements.units", units, p1)
    stories_printed = (
        f"{stories} Story" if profile.unit_suffixes and rng.random() < 0.5 else str(stories)
    )
    f.present("improvements.stories", "int", stories, stories_printed, p1)
    f.enum(
        "improvements.type", rng.choice(("det", "det", "det", "det", "att", "s_det_end_unit")), p1
    )
    xchecks["x.existing"] = ("existing",)
    f.present("improvements.design_style", "text", design_printed, design_printed, p1, also_on=[p2])
    f.present("improvements.year_built", "int", year_built, str(year_built), p1)
    eff_printed = f"{effective_age} Years" if profile.unit_suffixes else str(effective_age)
    f.present("improvements.effective_age_years", "int", effective_age, eff_printed, p1)
    f.multi("improvements.foundation_type", foundation, p1)
    f.present(
        "improvements.basement_area_sqft",
        "area_sqft",
        basement,
        f"{basement:,}",
        p1,
        also_on=[p2] if basement else [],
    )
    f.present(
        "improvements.basement_finish_percent", "percent", basement_finish, str(basement_finish), p1
    )
    if basement:
        xchecks["x.sump"] = ("sump",) if rng.random() < 0.5 else ()

    def material(key: str, options: Sequence[str], suffix: str) -> None:
        mat = rng.choice(options)
        printed = mat + suffix
        f.present(
            key,
            "text",
            mat,
            printed,
            p1,
            note="the UAD condition rating after the material is not part of the value"
            if suffix
            else None,
        )

    material(
        "improvements.foundation_walls", ("Concrete", "Poured Concrete", "Concrete Block"), rating
    )
    material(
        "improvements.exterior_walls",
        ("Vinyl Siding", "Brick", "Stucco", "Fiber Cement", "Brick/Vinyl"),
        rating,
    )
    material(
        "improvements.roof_surface",
        ("Comp Shingle", "Asphalt Shingle", "Metal", "Arch Shingle"),
        rating,
    )
    material("improvements.gutters_downspouts", ("Aluminum", "Galvanized", "Vinyl"), rating)
    material(
        "improvements.window_type", ("Double Hung", "Vinyl Dbl Hung", "Casement", "Sliders"), rating
    )
    material(
        "improvements.floors", ("Carpet/Tile", "Hdwd/Tile", "Carpet/LVP", "Hdwd/Carpet"), rating
    )
    material("improvements.walls", ("Drywall", "Plaster", "Drywall/Paint"), rating)
    texts["x.storm_sash"] = "Insulated"
    texts["x.screens"] = "Yes"
    texts["x.trim"] = "Wood" + rating
    texts["x.bath_floor"] = "Tile" + rating
    texts["x.bath_wainscot"] = rng.choice(("Tile", "Fiberglass", "Ceramic"))
    xchecks["x.attic"] = (rng.choice(("drop_stair", "scuttle", "none")),)
    heat_value: list[Any] = sorted(heat_codes)
    heat_checks = tuple(sorted(heat_codes))
    if heat_other is not None:
        heat_value.append({"code": "other", "text": heat_other})
        heat_checks = (*heat_checks, "other")
        texts["x.heating_other_text"] = heat_other
    heat_printed = "; ".join(
        [ENUM_LABELS["improvements.heating_type"][c] for c in sorted(heat_codes)]
        + ([heat_other] if heat_other else [])
    )
    f.present(
        "improvements.heating_type",
        "enum_multi",
        heat_value,
        heat_printed,
        p1,
        slot=None,
        checks=heat_checks,
        note="two boxes marked" if len(heat_checks) > 1 else None,
    )
    f.present("improvements.heating_fuel", "text", fuel, fuel, p1)
    cooling_label = ENUM_LABELS["improvements.cooling"][cooling]
    f.present(
        "improvements.cooling",
        "text",
        cooling_label,
        cooling_label,
        p1,
        slot=None,
        checks=(cooling,),
        note="a checkbox group without a SPEC enum: the value is the marked box's label",
    )
    f.present("improvements.fireplaces", "int", fireplaces, str(fireplaces), p1)
    if fireplaces:
        xchecks["x.fireplace_box"] = ("yes",)
    if patio:
        f.present("improvements.patio_deck", "text", patio, patio, p1, also_on=[p2])
        xchecks["x.patio_box"] = ("yes",)
    else:
        f.not_developed(
            "improvements.patio_deck", p1, note="no patio or deck: box unmarked and blank"
        )
    texts["x.fence"] = rng.choice(("Wood", "Chain Link", "Vinyl", "None"))
    if texts["x.fence"] != "None":
        xchecks["x.fence_box"] = ("yes",)
    f.multi("improvements.car_storage", cars, p1)
    if driveway_cars:
        texts["x.driveway_cars"] = str(driveway_cars)
        texts["x.driveway_surface"] = rng.choice(("Concrete", "Asphalt", "Gravel"))
    else:
        texts["x.driveway_surface"] = "None"
    if garage_cars:
        f.present("improvements.garage_cars", "int", garage_cars, str(garage_cars), p1)
    else:
        f.not_developed(
            "improvements.garage_cars", p1, note="no garage: the garage # of cars box is blank"
        )
    if carport_cars:
        texts["x.carport_cars"] = str(carport_cars)
    f.multi("improvements.appliances", appliances, p1)
    f.present("improvements.total_rooms", "int", rooms, str(rooms), p1, also_on=[p2, *photo])
    f.present("improvements.bedrooms", "int", bedrooms, str(bedrooms), p1, also_on=[p2, *photo])
    baths_printed = f"{full_baths}.{half_baths}"
    f.present(
        "improvements.bathrooms",
        "baths",
        {"full": full_baths, "half": half_baths},
        baths_printed,
        p1,
        also_on=[p2, *photo],
    )
    gla_core = f"{gla:,}"
    gla_printed = f"{gla_core} sq.ft." if profile.unit_suffixes else gla_core
    f.present(
        "improvements.gla_sqft",
        "area_sqft",
        gla,
        gla_printed,
        p1,
        also_on=[p2, *photo],
        also_text=gla_core,
    )
    features = rng.choice(
        (
            "Ceiling fans, dual-pane windows, covered patio",
            "Updated kitchen, LED lighting, programmable thermostat",
            "Insulated windows, tankless water heater, storage shed",
            "Vaulted ceilings, solar screens, irrigation system",
        )
    )
    f.present("improvements.additional_features", "text", features, features, p1)
    # Quality and condition: the subject column of the sales grid (page 2).
    f.present("improvements.quality", "text", quality, quality, p2, also_on=photo)
    f.present("improvements.condition", "text", condition, condition, p2, also_on=[p1])
    blocks["x.condition_desc"] = [
        f"{condition};{rng.choice(CONDITION_NOTES)};",
        "The subject has been adequately maintained; no functional or external obsolescence noted.",
    ]
    xchecks["x.deficiencies"] = ("no",)
    xchecks["x.conforms"] = ("yes",)

    # ---- cost approach (page 3)
    cost_keys = [k for k in CATALOGUE_KEYS if k.startswith("cost_approach.")]
    if profile.cost_developed:
        xchecks["x.cost_estimated"] = (rng.choice(("reproduction", "replacement", "replacement")),)
        f.present(
            "cost_approach.site_value",
            "currency",
            site_value,
            _money(site_value),
            p3,
            also_on=[p_add] if p_add else [],
        )
        source = rng.choice(
            ("Marshall & Swift", "Marshall & Swift Residential", "Local builders, M&S")
        )
        f.present("cost_approach.cost_data_source", "text", source, source, p3)
        q_cost = rng.choice(("Average", "Good", quality))
        f.present("cost_approach.quality_rating_from_cost_service", "text", q_cost, q_cost, p3)
        cost_month = date(effective.year, max(1, effective.month - rng.randint(0, 5)), 1)
        f.present(
            "cost_approach.effective_date_of_cost_data",
            "month",
            cost_month.strftime("%Y-%m"),
            cost_month.strftime("%m/%Y"),
            p3,
        )
        f.present("cost_approach.dwelling_sqft", "area_sqft", gla, gla_core, p3)
        f.present("cost_approach.dwelling_cost_per_sqft", "float", rate, f"{rate:.2f}", p3)
        if basement:
            texts["x.cost_basement_label"] = "Basement"
            f.present("cost_approach.basement_sqft", "area_sqft", basement, f"{basement:,}", p3)
            f.present(
                "cost_approach.basement_cost_per_sqft",
                "float",
                basement_rate,
                f"{basement_rate:.2f}",
                p3,
            )
            f.present(
                "cost_approach.basement_cost", "currency", basement_cost, _money(basement_cost), p3
            )
        else:
            for key in (
                "cost_approach.basement_sqft",
                "cost_approach.basement_cost_per_sqft",
                "cost_approach.basement_cost",
            ):
                f.not_developed(key, p3, note="no basement: the row is blank")
        if garage_sqft:
            f.present("cost_approach.garage_sqft", "area_sqft", garage_sqft, f"{garage_sqft:,}", p3)
            f.present(
                "cost_approach.garage_cost_per_sqft", "float", garage_rate, f"{garage_rate:.2f}", p3
            )
            f.present("cost_approach.garage_cost", "currency", garage_cost, _money(garage_cost), p3)
        else:
            for key in (
                "cost_approach.garage_sqft",
                "cost_approach.garage_cost_per_sqft",
                "cost_approach.garage_cost",
            ):
                f.not_developed(key, p3, note="no garage or carport: the row is blank")
        if misc:
            texts["x.cost_misc_label"] = "Porches/Patio/Misc."
            texts["x.cost_misc_eq"] = "=$"
            f.present("cost_approach.mechanicals_misc", "currency", misc, _money(misc), p3)
        else:
            f.not_developed("cost_approach.mechanicals_misc", p3, note="the misc. row is blank")
        printed_total = total_new
        printed_indicated = cost_indicated
        if profile.planted is not None and profile.planted != "reconciliation_mismatch":
            if profile.planted == "cost_indicated_total":
                printed_indicated = cost_indicated + rng.choice((10000, -10000, 9000))
                issues.append(
                    ExpectedIssue(
                        rule="cost_approach_total",
                        keys=("cost_approach.indicated_value",),
                        note=f"{depreciated:,} + {site_improvements:,} + {site_value:,} = "
                        f"{cost_indicated:,}, printed {printed_indicated:,}",
                    )
                )
            else:
                printed_total = total_new + rng.choice((10000, -10000, 1000))
                issues.append(
                    ExpectedIssue(
                        rule="cost_new_total",
                        keys=("cost_approach.total_cost_new",),
                        note=f"the cost rows sum to {total_new:,}, printed {printed_total:,}",
                    )
                )
        f.present(
            "cost_approach.dwelling_cost", "currency", dwelling_cost, _money(dwelling_cost), p3
        )
        f.present(
            "cost_approach.total_cost_new", "currency", printed_total, _money(printed_total), p3
        )
        f.present("cost_approach.depreciation_physical", "currency", physical, _money(physical), p3)
        for key, amount in (
            ("cost_approach.depreciation_functional", functional),
            ("cost_approach.depreciation_external", external),
        ):
            if amount:
                f.present(key, "currency", amount, _money(amount), p3)
            else:
                f.not_developed(
                    key,
                    p3,
                    printed="0",
                    note="a skipped cost-approach line printed 0 (SPEC principle 2)",
                )
        f.present(
            "cost_approach.depreciation",
            "currency",
            depreciation,
            _money(depreciation),
            p3,
            note="printed =$( N): the parentheses are the form's subtraction mark",
        )
        f.present(
            "cost_approach.depreciated_cost_improvements",
            "currency",
            depreciated,
            _money(depreciated),
            p3,
        )
        f.present(
            "cost_approach.site_improvements_value",
            "currency",
            site_improvements,
            _money(site_improvements),
            p3,
        )
        recon_cost = printed_indicated
        if profile.planted == "reconciliation_mismatch":
            recon_cost = printed_indicated + rng.choice((10000, -10000, 1000))
            issues.append(
                ExpectedIssue(
                    rule="cost_reconciliation_mismatch",
                    keys=(
                        "reconciliation.indicated_value_cost_approach",
                        "cost_approach.indicated_value",
                    ),
                    note=f"cost approach prints {printed_indicated:,}, "
                    f"the reconciliation {recon_cost:,}",
                )
            )
        same = recon_cost == printed_indicated
        f.present(
            "cost_approach.indicated_value",
            "currency",
            printed_indicated,
            _money(printed_indicated),
            p3,
            also_on=[p2] if same else [],
        )
        f.present(
            "reconciliation.indicated_value_cost_approach",
            "currency",
            recon_cost,
            _money(recon_cost),
            p2,
            also_on=[p3] if same else [],
        )
        rel_printed = str(remaining_life)
        f.present("cost_approach.remaining_economic_life", "int", remaining_life, rel_printed, p3)
        blocks["x.site_support"] = [
            f"Site value estimated by extraction and {rng.randint(3, 6)} land sales "
            "in the subject market area.",
        ]
        blocks["x.cost_comments"] = [
            "Cost estimate from the cost service, adjusted to the local market.",
            f"Depreciation by the age-life method: effective age {effective_age} of a "
            f"{total_life}-year total life.",
            "Gross living area from exterior measurements; see sketch.",
        ]
    else:
        if profile.site_value_printed:
            f.present(
                "cost_approach.site_value",
                "currency",
                site_value,
                _money(site_value),
                p3,
                also_on=[p_add] if p_add else [],
                note="cost approach not developed; the site value alone is given",
            )
            blocks["x.site_support"] = [
                "Site value by land sales; the cost approach was not developed."
            ]
        else:
            f.not_developed("cost_approach.site_value", p3, note="cost approach not developed")
        for key in cost_keys:
            if key not in f.plans:
                f.not_developed(key, p3, note="cost approach not developed")
        f.not_developed(
            "reconciliation.indicated_value_cost_approach", p2, note="cost approach not developed"
        )
        blocks["x.cost_comments"] = [
            "The cost approach was not developed; it is not considered meaningful for this "
            "property type and age."
        ]

    # ---- sales comparison and reconciliation (page 2)
    f.present("sales_comparison.indicated_value", "currency", sales_value, _money(sales_value), p2)
    f.present(
        "reconciliation.indicated_value_sales_comparison",
        "currency",
        sales_value,
        _money(sales_value),
        p2,
    )
    if profile.income_zero:
        f.not_developed(
            "reconciliation.indicated_value_income_approach",
            p2,
            printed="0",
            note="income approach not developed, printed 0",
        )
    else:
        f.not_developed(
            "reconciliation.indicated_value_income_approach",
            p2,
            note="income approach not developed, left blank",
        )
    made = rng.choice(
        ("as_is", "as_is", "as_is", "as_is", "subject_to_repairs", "subject_to_inspection")
    )
    f.enum("reconciliation.appraisal_made", made, p2)
    if made == "subject_to_repairs":
        blocks["x.made_conditions"] = [
            "Subject to repair of the damaged roof flashing and installation of smoke detectors."
        ]
    elif made == "subject_to_inspection":
        blocks["x.made_conditions"] = [
            "Subject to a satisfactory roof inspection by a licensed roofing contractor."
        ]
    final_also = [p6] + ([p_add] if p_add else [])
    f.present(
        "reconciliation.final_market_value",
        "currency",
        final_value,
        _money(final_value),
        p2,
        also_on=final_also,
    )
    f.present(
        "reconciliation.effective_date_of_appraisal",
        "date",
        effective.isoformat(),
        _date(effective),
        p2,
        also_on=final_also,
    )
    blocks["x.recon_narrative"] = [
        "The sales comparison approach is given most weight as it best reflects buyer and seller "
        "behavior.",
        "The cost approach supports the value conclusion."
        if profile.cost_developed
        else "The income approach was not developed; single-family homes in the area are "
        "not typically rented.",
    ]

    # ---- appraiser (page 6)
    appraiser = person()
    company = rng.choice(COMPANIES)
    f.present("appraiser.name", "text", appraiser, appraiser, p6)
    f.present("appraiser.company", "text", company, company, p6)
    license_no = f"{rng.choice(('CR', 'AR', 'RA'))}{rng.randint(100000, 999999)}"
    certified = rng.random() < 0.6
    f.present(
        "appraiser.license_number",
        "text",
        license_no,
        license_no,
        p6,
        slot="appraiser.license_number" if certified else "x.license_alt",
    )
    f.present("appraiser.license_state", "text", state, state, p6)
    f.present("appraiser.signature_date", "date", signed.isoformat(), _date(signed), p6)
    f.present(
        "appraiser.effective_date",
        "date",
        effective.isoformat(),
        _date(effective),
        p6,
        also_on=[p2],
    )
    f.present(
        "appraiser.appraised_value", "currency", final_value, _money(final_value), p6, also_on=[p2]
    )
    texts["x.company_address"] = (
        f"{rng.randint(100, 9999)} {rng.choice(STREETS)} {rng.choice(SUFFIXES)}"
    )
    texts["x.company_city"] = (
        f"{rng.choice(PLACES)[0]}, {state} {rng.randint(10, 99)}{rng.randint(100, 999)}"
    )
    texts["x.phone"] = f"(555) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}"
    texts["x.email"] = f"{appraiser.split()[0].lower()}@{company.split()[0].lower()}.example.com"
    texts["x.expiration"] = _date(date(effective.year + rng.randint(1, 2), rng.randint(1, 12), 28))
    texts["x.appraised_street"] = street
    texts["x.appraised_city"] = city_line
    texts["x.lender_contact"] = person()
    texts["x.lender_company"] = lender
    texts["x.inspection_date"] = _date(effective)
    texts["x.comps_inspection_date"] = _date(effective + timedelta(days=rng.randint(0, 2)))
    xchecks["x.inspected"] = ("interior",)
    xchecks["x.comps_inspected"] = ("exterior",)

    # ---- page 2 decoration: the sales grid
    comps = _comparables(
        rng,
        sales_value,
        gla,
        rooms,
        bedrooms,
        site_sqft,
        profile.site_acres,
        quality,
        condition,
        actual_age,
        design_printed,
    )
    lo = min(c["price"] for c in comps)
    hi = max(c["price"] for c in comps)
    texts["x.listings_count"] = str(rng.randint(4, 18))
    texts["x.listings_low"] = _money(_round(lo * 0.92, 1000))
    texts["x.listings_high"] = _money(_round(hi * 1.12, 1000))
    texts["x.sales_count"] = str(rng.randint(8, 40))
    texts["x.sales_low"] = _money(_round(lo * 0.85, 1000))
    texts["x.sales_high"] = _money(_round(hi * 1.15, 1000))
    g = grid
    g[("address", "S")] = street
    g[("address2", "S")] = city_line
    if purchase:
        g[("sale_price", "S")] = _money(contract_price)
        g[("price_gla", "S")] = f"{contract_price / gla:.2f}"
    g[("location", "S")] = location_rating
    g[("leasehold", "S")] = ENUM_LABELS["subject.property_rights_appraised"][rights]
    g[("site", "S")] = site_printed
    g[("view", "S")] = view
    g[("design", "S")] = design_printed
    g[("age", "S")] = str(actual_age)
    g[("rooms_total", "S")] = str(rooms)
    g[("rooms_beds", "S")] = str(bedrooms)
    g[("rooms_baths", "S")] = baths_printed
    g[("gla", "S")] = gla_core
    g[("basement", "S")] = (
        f"{basement:,}sf{round(basement * basement_finish / 100):,}sfwo" if basement else "0sf"
    )
    g[("functional", "S")] = "Average"
    g[("heating", "S")] = (
        ("FWA" if "fwa" in heat_codes else (heat_other or "HWBB"))
        + "/"
        + ("CAC" if cooling == "central" else "Wall")
    )
    g[("energy", "S")] = "None"
    g[("garage", "S")] = (f"{garage_cars}ga" if garage_cars else "") + (
        f"{carport_cars}cp" if carport_cars else ""
    ) + (f"{driveway_cars}dw" if driveway_cars else "") or "None"
    g[("porch", "S")] = patio or "None"
    for n, c in enumerate(comps, start=1):
        col = f"C{n}"
        adj = f"A{n}"
        g[("address", col)] = c["street"]
        g[("address2", col)] = f"{city}, {state} {zip_code}"
        g[("proximity", col)] = c["proximity"]
        g[("sale_price", adj)] = _money(c["price"])
        g[("price_gla", col)] = f"{c['price'] / c['gla']:.2f}"
        g[("data_source", col)] = c["mls"]
        g[("verification", col)] = c["doc"]
        g[("concessions", col)] = "ArmLth"
        g[("concessions2", col)] = c["financing"]
        g[("date", col)] = c["date"]
        g[("location", col)] = "N;Res;"
        g[("leasehold", col)] = "Fee Simple"
        g[("site", col)] = c["site"]
        g[("view", col)] = "N;Res;"
        g[("design", col)] = c["design"]
        g[("quality", col)] = c["quality"]
        g[("age", col)] = str(c["age"])
        g[("condition", col)] = c["condition"]
        g[("rooms_total", col)] = str(c["rooms"])
        g[("rooms_beds", col)] = str(c["beds"])
        g[("rooms_baths", col)] = c["baths"]
        g[("gla", col)] = f"{c['gla']:,}"
        g[("basement", col)] = "0sf"
        g[("functional", col)] = "Average"
        g[("heating", col)] = "FWA/CAC"
        g[("energy", col)] = "None"
        g[("garage", col)] = c["garage"]
        g[("porch", col)] = c["porch"]
        for row, amount in c["adjustments"].items():
            g[(f"{row}_adj", adj)] = _signed(amount)
        g[("net_adj", adj)] = _money(abs(c["net"]))
        g[("net_pct", col)] = f"{c['net_pct']:.1f}"
        g[("gross_pct", col)] = f"{c['gross_pct']:.1f}"
        g[("adjusted", adj)] = _money(c["adjusted"])
        xchecks[f"x.net_sign_{n}"] = ("plus",) if c["net"] >= 0 else ("minus",)
    xchecks["x.research"] = ("did",)
    xchecks["x.prior_subject"] = ("did_not",)
    xchecks["x.prior_comps"] = ("did_not",)
    texts["x.prior_subject_source"] = "County public records"
    texts["x.prior_comps_source"] = "County public records, MLS"
    blocks["x.prior_analysis"] = [
        "No prior sales of the subject or comparables within the periods researched."
    ]
    blocks["x.sales_summary"] = [
        f"Comparables bracket the subject's gross living area of {gla_core} square feet "
        "and its age.",
        "Adjustments for GLA, site, garage and condition are market-derived.",
        "All three sales were given consideration, with the most weight on the closest "
        "in location.",
    ]
    blocks["x.additional_comments"] = [
        "Scope of work: a complete visual inspection of the interior and exterior of the subject.",
        "The intended user of this report is the lender/client named above.",
        "The appraiser has no current or prospective interest in the subject property.",
    ] + (["See the supplemental addendum for additional comments."] if p_add else [])
    blocks["x.income_summary"] = [
        "Not developed; the subject is a single-family, owner-occupied property."
    ]

    # ---- photo page and addendum
    photo_header = [
        ("Borrower", borrower),
        ("Property Address", street),
        ("City", f"{city}    County {county}    State {state}    Zip Code {zip_code}"),
        ("Lender/Client", lender),
    ]
    photo_block = [
        ("Sales Price", _money(contract_price) if purchase else "N/A"),
        ("Gross Living Area", gla_core),
        ("Total Rooms", str(rooms)),
        ("Total Bedrooms", str(bedrooms)),
        ("Total Bathrooms", baths_printed),
        ("Location", location_rating),
        ("View", view),
        ("Site", site_printed),
        ("Quality", quality),
        ("Age", str(actual_age)),
    ]
    if purchase and p_photo:
        plan = f.plans["listing_and_contract.contract_price"]
        f.plans[plan.key] = FieldPlan(
            **{**_asdict(plan), "also_on": tuple(sorted({*plan.also_on, p_photo}))}
        )
    addendum_lines: list[str] = []
    if p_add:
        prior_gla = _round(gla * rng.uniform(0.88, 0.95), 10)
        addendum_lines = [
            "Neighborhood Comments",
            NARRATIVE[0],
            NARRATIVE[1],
            "Site Comments",
            f"The site is {dimensions.lower()} with {shape.lower()} shape and typical utility.",
            "Improvements Comments",
            f"A prior appraisal (2019) reported {prior_gla:,} sq ft; the current measurement is "
            f"used in this report.",
        ]
        if profile.cost_developed or profile.site_value_printed:
            addendum_lines += [
                "Cost Approach Comments",
                f"Site value of ${site_value:,} is supported by land sales and extraction.",
            ]
        addendum_lines += [
            "Reconciliation",
            f"The final opinion of market value is ${final_value:,} as of {_date(effective)}.",
            "This report was prepared in conformity with USPAP and the lender's guidelines.",
        ]
    scan_dpi = rng.choice((150, 175, 200)) if profile.scan else 0
    scan_angle = round(rng.uniform(-0.8, 0.8), 2) if profile.scan else 0.0

    unplanned = sorted(CATALOGUE_KEYS - set(f.plans))
    if unplanned:  # pragma: no cover - a catalogue change must be planned here
        raise AssertionError(f"{name}: no plan for {unplanned}")
    return DocPlan(
        name=name,
        index=index,
        seed=seed,
        profile=profile,
        file_no=file_no,
        fields=f.plans,
        texts=texts,
        checks=xchecks,
        blocks=blocks,
        grid=grid,
        photo_block=photo_block if p_photo else [],
        photo_header=photo_header if p_photo else [],
        addendum_lines=addendum_lines,
        expected_issues=issues,
        scan_dpi=scan_dpi,
        scan_angle=scan_angle,
    )


def _asdict(plan: FieldPlan) -> dict[str, Any]:
    return {name: getattr(plan, name) for name in FieldPlan.__slots__}


def _comparables(
    rng: random.Random,
    value: int,
    gla: int,
    rooms: int,
    beds: int,
    site_sqft: int,
    acres: bool,
    quality: str,
    condition: str,
    age: int,
    design: str,
) -> list[dict[str, Any]]:
    """Three sales whose adjusted prices bracket ``value`` (one below, one above)."""
    targets = sorted(
        (
            _round(value * rng.uniform(0.965, 0.99), 100),
            _round(value * rng.uniform(0.99, 1.01), 100),
            _round(value * rng.uniform(1.01, 1.035), 100),
        )
    )
    rng.shuffle(targets)
    comps: list[dict[str, Any]] = []
    for adjusted in targets:
        c_gla = _round(gla * rng.uniform(0.86, 1.14), 1)
        c_age = max(1, age + rng.randint(-8, 8))
        gla_adj = _round((gla - c_gla) * rng.uniform(30, 45), 100)
        age_adj = _round((c_age - age) * rng.uniform(300, 600), 100)
        garage_adj = rng.choice((0, 0, 5000, -5000))
        site_adj = _round(rng.uniform(-6000, 6000), 500) if rng.random() < 0.5 else 0
        partial = gla_adj + age_adj + garage_adj + site_adj
        price = _round(adjusted - partial + rng.uniform(-4000, 4000), 500)
        cond_adj = adjusted - price - partial
        adjustments = {
            "gla": gla_adj,
            "age": age_adj,
            "garage": garage_adj,
            "site": site_adj,
            "condition": cond_adj,
        }
        net = sum(adjustments.values())
        gross = sum(abs(a) for a in adjustments.values())
        c_site = _round(site_sqft * rng.uniform(0.8, 1.2), 10)
        sale_day = date(2025, 1, 1) + timedelta(days=rng.randint(-300, 200))
        comps.append(
            {
                "street": f"{rng.randint(102, 9870)} {rng.choice(STREETS)} {rng.choice(SUFFIXES)}",
                "proximity": f"{rng.uniform(0.1, 1.9):.2f} miles {rng.choice(BEARINGS)}",
                "price": price,
                "gla": c_gla,
                "mls": f"MLS#{rng.randint(100000, 999999)};DOM {rng.randint(3, 120)}",
                "doc": f"Doc#{rng.randint(10000, 99999)}",
                "financing": rng.choice(("Conv;0", "FHA;3000", "Cash;0", "VA;0")),
                "date": f"s{sale_day:%m/%y};c{sale_day - timedelta(days=30):%m/%y}",
                "site": f"{c_site / 43560:.2f} ac" if acres else f"{c_site:,} sf",
                "design": design,
                "quality": quality,
                "age": c_age,
                "condition": condition,
                "rooms": max(beds + 2, rooms + rng.randint(-1, 1)),
                "beds": beds + rng.choice((-1, 0, 0, 1)) if beds > 2 else beds,
                "baths": f"{rng.randint(1, 3)}.{rng.randint(0, 1)}",
                "garage": rng.choice(("2ga2dw", "1ga1dw", "2ga4dw", "2cp2dw")),
                "porch": rng.choice(("Patio", "Deck", "Porch", "None")),
                "adjustments": adjustments,
                "net": net,
                "net_pct": abs(net) / price * 100,
                "gross_pct": gross / price * 100,
                "adjusted": price + net,
            }
        )
    return comps


# --------------------------------------------------------------------------- layout


@dataclass(frozen=True, slots=True)
class Slot:
    """Where a value is drawn: relative to an anchor caption printed on the blank form.

    ``near`` is the anchor's measured ``(x0, bottom)`` on the blank Form 70; the geometry
    finds the caption there (within 4 pt) and takes its baseline (and, for ``after``, its
    right edge). ``after``: left-aligned ``WORD_GAP`` after the caption; ``left``: left
    edge at ``x``; ``right``: right edge at ``x``. ``limit``: for after/left the x the text
    must stop short of (the next caption or rule), for right the x it may not start before.
    Text wider than the space is set in a smaller size (down to ``MIN_SIZE``), as appraisal
    software does.
    """

    page: int
    anchor: str
    near: tuple[float, float]
    how: Literal["after", "left", "right"]
    x: float | None = None
    limit: float | None = None
    dy: float = 0.0
    size: float = VALUE_SIZE
    bold: bool = False
    min_size: float = MIN_SIZE


@dataclass(frozen=True, slots=True)
class Check:
    """A checkbox square: its measured ``(x0, top)`` and the caption printed right of it."""

    page: int
    near: tuple[float, float]
    label: str | None = None


def _s(
    page: int,
    anchor: str,
    x0: float,
    bottom: float,
    how: Literal["after", "left", "right"] = "after",
    x: float | None = None,
    limit: float | None = None,
    dy: float = 0.0,
    size: float = VALUE_SIZE,
    bold: bool = False,
) -> Slot:
    return Slot(page, anchor, (x0, bottom), how, x, limit, dy, size, bold)


# Page 6 prints its blanks as runs of "_" glyphs. A value is drawn on the caption baseline;
# the underscores under it are removed from the text layer and the rule is redrawn as a
# line at the measured stroke position (1.1-1.55 pt below the baseline), so the page looks
# the same and the text layer reads "Name Willa Ashworth ____" as software output does.
UNDERSCORE_STROKE: Final = 1.33

SLOTS: Final[dict[str, Slot]] = {
    # Page 1: subject
    "subject.property_address": _s(1, "Property Address", 27.7, 62, limit=328.0),
    "subject.city": _s(1, "City", 328.0, 62, limit=469.3),
    "subject.state": _s(1, "State", 469.3, 62, limit=516.5),
    "subject.zip": _s(1, "Zip Code", 516.5, 62, limit=592.0),
    "subject.borrower": _s(1, "Borrower", 27.7, 74, limit=246.9),
    "subject.owner_of_public_record": _s(1, "Owner of Public Record", 246.9, 74, limit=469.3),
    "subject.county": _s(1, "County", 469.3, 74, limit=592.0),
    "subject.legal_description": _s(1, "Legal Description", 27.7, 86, limit=592.0),
    "subject.assessors_parcel": _s(1, "Assessor’s Parcel #", 27.7, 99, limit=328.0),
    "subject.tax_year": _s(1, "Tax Year", 328.0, 99, limit=469.3),
    "subject.real_estate_taxes": _s(1, "R.E. Taxes $", 469.3, 99, limit=592.0),
    "subject.neighborhood_name": _s(1, "Neighborhood Name", 27.7, 111, limit=328.0),
    "subject.map_reference": _s(1, "Map Reference", 328.0, 111, limit=469.3),
    "subject.census_tract": _s(1, "Census Tract", 469.3, 111, limit=592.0),
    "subject.special_assessments": _s(1, "Special Assessments $", 247.0, 123, limit=406.0),
    "subject.hoa_dues": _s(1, "HOA $", 451.6, 123, limit=499.1),
    "subject.lender_client": _s(1, "Lender/Client", 27.7, 160, limit=252.2),
    "x.lender_address": _s(1, "Address", 252.2, 160, limit=592.0),
    "x.offered_report": _s(
        1, "Report data source(s) used, offering price(s), and date(s).", 27.7, 184, limit=592.0
    ),
    "x.contract_analysis": _s(1, "performed.", 27.7, 220, limit=592.0),
    "listing_and_contract.contract_price": _s(1, "Contract Price $", 27.7, 245, limit=123.5),
    "listing_and_contract.contract_date": _s(1, "Date of Contract", 123.5, 245, limit=223.1),
    "listing_and_contract.data_source": _s(1, "Data Source(s)", 437.5, 245, limit=592.0),
    "x.concessions_line": _s(
        1,
        "If Yes, report the total dollar amount and describe the items to be paid.",
        27.7,
        267,
        limit=592.0,
    ),
    # Page 1: neighborhood
    "neighborhood.present_land_use_one_unit": _s(
        1, "One-Unit", 509.3, 330, "right", x=583.4, limit=541.0
    ),
    "neighborhood.present_land_use_two_to_four": _s(
        1, "2-4 Unit", 509.3, 342, "right", x=583.4, limit=537.0
    ),
    "neighborhood.present_land_use_multi_family": _s(
        1, "Multi-Family", 509.3, 355, "right", x=583.4, limit=551.0
    ),
    "neighborhood.present_land_use_commercial": _s(
        1, "Commercial", 509.3, 367, "right", x=583.4, limit=551.0
    ),
    "neighborhood.present_land_use_other": _s(
        1, "Other", 509.3, 379, "right", x=583.4, limit=530.0
    ),
    "neighborhood.price_low_thousands": _s(1, "Low", 459.0, 355, "right", x=459.0, limit=427.0),
    "neighborhood.price_high_thousands": _s(1, "High", 458.2, 367, "right", x=458.2, limit=427.0),
    "neighborhood.price_predominant_thousands": _s(
        1, "Pred.", 457.0, 379, "right", x=457.0, limit=427.0
    ),
    "neighborhood.age_low": _s(1, "Low", 459.0, 355, "right", x=504.0, limit=476.0),
    "neighborhood.age_high": _s(1, "High", 458.2, 367, "right", x=504.0, limit=476.0),
    "neighborhood.age_predominant": _s(1, "Pred.", 457.0, 379, "right", x=504.0, limit=478.0),
    "neighborhood.neighborhood_boundaries": _s(
        1, "Neighborhood Boundaries", 27.7, 367, limit=425.0
    ),
    # Page 1: site
    "site.dimensions": _s(1, "Dimensions", 27.7, 466, limit=237.2),
    "site.area_sqft": _s(1, "Area", 237.2, 466, limit=355.5),
    "site.shape": _s(1, "Shape", 355.5, 466, limit=473.8),
    "site.view": _s(1, "View", 473.8, 466, limit=592.0),
    "site.zoning_classification": _s(1, "Specific Zoning Classification", 27.7, 478, limit=237.2),
    "site.zoning_description": _s(1, "Zoning Description", 237.2, 478, limit=592.0),
    "site.utilities_electric": _s(1, "Electricity", 27.7, 539, "left", x=118.6, limit=212.0),
    "site.utilities_gas": _s(1, "Gas", 27.7, 551, "left", x=118.6, limit=212.0),
    "site.utilities_water": _s(1, "Water", 212.0, 539, "left", x=322.4, limit=415.0),
    "site.utilities_sewer": _s(1, "Sanitary Sewer", 212.0, 551, "left", x=322.4, limit=415.0),
    "x.street_type": _s(1, "Street", 415.0, 539, limit=537.2),
    "x.alley_type": _s(1, "Alley", 415.0, 551, limit=537.2),
    "site.flood_zone": _s(1, "FEMA Flood Zone", 202.1, 563, limit=313.4),
    "site.fema_map_number": _s(1, "FEMA Map #", 313.4, 563, limit=452.8),
    "site.fema_map_date": _s(1, "FEMA Map Date", 452.8, 563, limit=592.0),
    # Page 1: improvements
    "improvements.stories": _s(1, "# of Stories", 27.7, 664, limit=170.0),
    "improvements.design_style": _s(1, "Design (Style)", 27.7, 701, limit=170.0),
    "improvements.year_built": _s(1, "Year Built", 27.7, 714, limit=170.0),
    "improvements.effective_age_years": _s(1, "Effective Age (Yrs)", 27.7, 726, limit=170.0),
    "improvements.basement_area_sqft": _s(
        1, "Basement Area", 170.4, 677, "right", x=289.1, limit=220.8
    ),
    "improvements.basement_finish_percent": _s(
        1, "Basement Finish", 170.4, 689, "right", x=299.7, limit=225.3
    ),
    "improvements.foundation_walls": _s(
        1, "Foundation Walls", 313.0, 652, "left", x=392.0, limit=471.0
    ),
    "improvements.exterior_walls": _s(
        1, "Exterior Walls", 313.1, 664, "left", x=392.0, limit=471.0
    ),
    "improvements.roof_surface": _s(1, "Roof Surface", 313.0, 677, "left", x=392.0, limit=471.0),
    "improvements.gutters_downspouts": _s(
        1, "Gutters & Downspouts", 313.1, 689, "left", x=392.0, limit=471.0
    ),
    "improvements.window_type": _s(1, "Window Type", 313.1, 701, "left", x=392.0, limit=471.0),
    "x.storm_sash": _s(1, "Storm Sash/Insulated", 313.1, 714, "left", x=392.0, limit=471.0),
    "x.screens": _s(1, "Screens", 313.1, 726, "left", x=392.0, limit=471.0),
    "improvements.floors": _s(1, "Floors", 472.0, 652, "left", x=525.0, limit=592.0),
    "improvements.walls": _s(1, "Walls", 472.0, 664, "left", x=525.0, limit=592.0),
    "x.trim": _s(1, "Trim/Finish", 472.0, 677, "left", x=525.0, limit=592.0),
    "x.bath_floor": _s(1, "Bath Floor", 472.0, 689, "left", x=525.0, limit=592.0),
    "x.bath_wainscot": _s(1, "Bath Wainscot", 472.0, 701, "left", x=525.0, limit=592.0),
    "x.heating_other_text": _s(1, "Other", 182.8, 752, limit=238.0),
    "improvements.heating_fuel": _s(1, "Fuel", 241.7, 752, limit=312.0),
    "improvements.fireplaces": _s(1, "Fireplace(s) #", 325.4, 752, limit=393.7),
    "improvements.patio_deck": _s(1, "Patio/Deck", 325.4, 764, limit=393.7),
    "x.fence": _s(1, "Fence", 404.9, 752, limit=471.0),
    "x.driveway_cars": _s(1, "Cars", 537.7, 726, limit=592.0),
    "x.driveway_surface": _s(1, "Driveway Surface", 472.0, 740, limit=592.0),
    "improvements.garage_cars": _s(1, "Cars", 538.2, 752, limit=592.0),
    "x.carport_cars": _s(1, "Cars", 538.6, 764, limit=592.0),
    "improvements.total_rooms": _s(1, "Rooms", 196.5, 803, "right", x=196.5, limit=148.3),
    "improvements.bedrooms": _s(1, "Bedrooms", 268.7, 803, "right", x=268.7, limit=219.8),
    "improvements.bathrooms": _s(1, "Bath(s)", 340.8, 803, "right", x=340.8, limit=302.2),
    "improvements.gla_sqft": _s(
        1, "Square Feet of Gross Living Area Above Grade", 412.9, 803, "right", x=412.9, limit=364.5
    ),
    "improvements.additional_features": _s(
        1, "Additional features (special energy efficient items, etc.)", 27.7, 815, limit=592.0
    ),
    # Page 2: sales comparison, reconciliation
    "improvements.quality": _s(
        2, "Quality of Construction", 27.7, 278, "left", x=121.0, limit=185.3
    ),
    "improvements.condition": _s(2, "Condition", 27.7, 303, "left", x=121.0, limit=185.3),
    "sales_comparison.indicated_value": _s(
        2, "Indicated Value by Sales Comparison Approach $", 27.8, 840, limit=420.0
    ),
    "reconciliation.indicated_value_sales_comparison": _s(
        2, "Sales Comparison Approach $", 100.7, 853, limit=262.4
    ),
    "reconciliation.indicated_value_cost_approach": _s(
        2, "Cost Approach (if developed) $", 262.4, 853, limit=421.8
    ),
    "reconciliation.indicated_value_income_approach": _s(
        2, "Income Approach (if developed) $", 421.8, 853, limit=592.0
    ),
    "reconciliation.final_market_value": _s(
        2, "$", 27.8, 973, "right", x=103.6, limit=31.9, size=9.0, bold=True
    ),
    "reconciliation.effective_date_of_appraisal": _s(
        2, "as of", 107.7, 973, limit=207.8, size=9.0, bold=True
    ),
    "x.listings_count": _s(2, "There are", 27.7, 49, "right", x=86.3, limit=59.6),
    "x.listings_low": _s(2, "$", 405.2, 49, limit=485.3),
    "x.listings_high": _s(2, "$", 495.5, 49, limit=588.1),
    "x.sales_count": _s(2, "There are", 27.7, 61, "right", x=86.3, limit=59.6),
    "x.sales_low": _s(2, "$", 422.2, 61, limit=500.2),
    "x.sales_high": _s(2, "$", 510.5, 61, limit=588.5),
    "x.prior_subject_source": _s(2, "Data source(s)", 27.7, 555, limit=592.0),
    "x.prior_comps_source": _s(2, "Data source(s)", 27.7, 581, limit=592.0),
    # Page 3: cost approach
    "cost_approach.site_value": _s(3, "$", 544.5, 592, "right", x=589.5, limit=548.6),
    "cost_approach.cost_data_source": _s(3, "Source of cost data", 28.1, 604, limit=312.0),
    "cost_approach.quality_rating_from_cost_service": _s(
        3, "Quality rating from cost service", 28.1, 617, limit=172.7
    ),
    "cost_approach.effective_date_of_cost_data": _s(
        3, "Effective date of cost data", 172.7, 617, limit=312.0
    ),
    "cost_approach.dwelling_sqft": _s(3, "Dwelling", 314.0, 604, "right", x=404.0, limit=341.9),
    "cost_approach.dwelling_cost_per_sqft": _s(
        3, "Dwelling", 314.0, 604, "left", x=445.0, limit=495.6
    ),
    "cost_approach.dwelling_cost": _s(3, "Dwelling", 314.0, 604, "right", x=589.5, limit=548.5),
    "x.cost_basement_label": _s(3, "Sq. Ft. @ $", 404.1, 617, "left", x=316.0, limit=360.0),
    "cost_approach.basement_sqft": _s(3, "Sq. Ft. @ $", 404.1, 617, "right", x=404.1, limit=360.0),
    "cost_approach.basement_cost_per_sqft": _s(
        3, "Sq. Ft. @ $", 404.1, 617, "left", x=445.0, limit=495.7
    ),
    "cost_approach.basement_cost": _s(3, "Sq. Ft. @ $", 404.1, 617, "right", x=589.5, limit=548.5),
    "x.cost_misc_label": _s(
        3, "Comments on Cost Approach", 28.1, 629, "left", x=316.0, limit=420.0
    ),
    "x.cost_misc_eq": _s(3, "Comments on Cost Approach", 28.1, 629, "left", x=537.0, limit=552.0),
    "cost_approach.mechanicals_misc": _s(
        3, "Comments on Cost Approach", 28.1, 629, "right", x=589.5, limit=552.0
    ),
    "cost_approach.garage_sqft": _s(3, "Garage/Carport", 314.1, 642, "right", x=404.1, limit=365.4),
    "cost_approach.garage_cost_per_sqft": _s(
        3, "Garage/Carport", 314.1, 642, "left", x=445.0, limit=495.7
    ),
    "cost_approach.garage_cost": _s(3, "Garage/Carport", 314.1, 642, "right", x=589.5, limit=548.6),
    "cost_approach.total_cost_new": _s(
        3, "Total Estimate of Cost-New", 314.1, 655, "right", x=589.5, limit=548.6
    ),
    "cost_approach.depreciation_physical": _s(
        3, "Depreciation", 314.1, 680, "right", x=402.0, limit=355.5
    ),
    "cost_approach.depreciation_functional": _s(
        3, "Depreciation", 314.1, 680, "right", x=449.0, limit=402.0
    ),
    "cost_approach.depreciation_external": _s(
        3, "Depreciation", 314.1, 680, "right", x=540.1, limit=449.0
    ),
    "cost_approach.depreciation": _s(3, "Depreciation", 314.1, 680, "right", x=588.6, limit=550.9),
    "cost_approach.depreciated_cost_improvements": _s(
        3, "Depreciated Cost of", 314.1, 692, "right", x=589.5, limit=548.5
    ),
    "cost_approach.site_improvements_value": _s(
        3, "“As-is” Value of Site", 314.1, 705, "right", x=589.5, limit=548.5
    ),
    "cost_approach.indicated_value": _s(
        3, "Indicated Value By Cost", 314.0, 730, "right", x=589.5, limit=548.6
    ),
    "cost_approach.remaining_economic_life": _s(
        3, "Years", 287.9, 730, "right", x=287.9, limit=211.4
    ),
    # Page 6: appraiser and lender (values above the underscore rules)
    "appraiser.name": _s(6, "Name", 30.9, 645, "left", x=62.0, limit=300.0),
    "appraiser.company": _s(6, "Company Name", 30.9, 658, "left", x=102.0, limit=300.0),
    "x.company_address": _s(6, "Company", 30.9, 672, "left", x=110.0, limit=300.0),
    "x.company_city": _s(6, "Company", 30.9, 672, "left", x=38.0, limit=300.0, dy=13.4),
    "x.phone": _s(6, "Telephone", 30.9, 698, "left", x=114.0, limit=300.0),
    "x.email": _s(6, "Email", 30.9, 712, "left", x=94.0, limit=300.0),
    "appraiser.signature_date": _s(
        6, "Date of Signature and", 30.9, 725, "left", x=160.0, limit=300.0
    ),
    "appraiser.effective_date": _s(
        6, "Effective Date of Appraisal", 30.9, 738, "left", x=142.0, limit=300.0
    ),
    "appraiser.license_number": _s(
        6, "State Certification", 30.9, 752, "left", x=113.0, limit=300.0
    ),
    "x.license_alt": _s(6, "or State License", 30.9, 765, "left", x=107.0, limit=300.0),
    "appraiser.license_state": _s(6, "State", 30.9, 792, "left", x=62.0, limit=300.0),
    "x.expiration": _s(
        6,
        "Expiration Date of Certification or",
        30.9,
        805,
        "left",
        x=208.0,
        limit=300.0,
    ),
    "x.appraised_street": _s(
        6,
        "ADDRESS OF PROPERTY APPRAISED",
        30.9,
        828,
        "left",
        x=38.0,
        limit=300.0,
        dy=13.3,
    ),
    "x.appraised_city": _s(
        6,
        "ADDRESS OF PROPERTY APPRAISED",
        30.9,
        828,
        "left",
        x=38.0,
        limit=300.0,
        dy=26.7,
    ),
    "appraiser.appraised_value": _s(
        6,
        "APPRAISED VALUE OF SUBJECT PROPERTY $",
        30.2,
        868,
        "left",
        x=238.0,
        limit=300.0,
    ),
    "x.lender_contact": _s(6, "Name", 30.9, 896, "left", x=62.0, limit=300.0),
    "x.lender_company": _s(6, "Company Name", 30.9, 910, "left", x=102.0, limit=300.0),
    "x.inspection_date": _s(6, "Date of Inspection", 331.9, 881, "left", x=412.0, limit=575.0),
    "x.comps_inspection_date": _s(
        6, "Date of Inspection", 331.9, 947, "left", x=412.0, limit=575.0
    ),
}

# Slots that carry a catalogue key's printed text drawn by a composite (field slot None).
COMPOSITE_SOURCES: Final[dict[str, str]] = {
    "listing_and_contract.offering_price": "x.offered_report",
    "listing_and_contract.listing_date": "x.offered_report",
    "listing_and_contract.days_on_market": "x.offered_report",
    "listing_and_contract.seller_concessions": "x.concessions_line",
    "site.area": "site.area_sqft",
    "site.area_acres": "site.area_sqft",
}

CHECKS: Final[dict[str, dict[str, Check]]] = {
    "subject.occupant": {
        "owner": Check(1, (66.5, 114.1), "Owner"),
        "tenant": Check(1, (106.8, 114.1), "Tenant"),
        "vacant": Check(1, (147.4, 114.1), "Vacant"),
    },
    "x.pud": {"yes": Check(1, (406.0, 114.1), "PUD")},
    "x.hoa_period": {
        "year": Check(1, (499.1, 114.1), "per year"),
        "month": Check(1, (544.7, 114.1), "per month"),
    },
    "subject.property_rights_appraised": {
        "fee_simple": Check(1, (120.6, 126.2), "Fee Simple"),
        "leasehold": Check(1, (176.5, 126.2), "Leasehold"),
        "other": Check(1, (229.0, 126.2), "Other (describe)"),
    },
    "subject.assignment_type": {
        "purchase": Check(1, (91.4, 138.5), "Purchase Transaction"),
        "refinance": Check(1, (181.3, 138.5), "Refinance Transaction"),
        "other": Check(1, (273.3, 138.5), "Other (describe)"),
    },
    "x.offered": {"yes": Check(1, (497.3, 163.0), "Yes"), "no": Check(1, (527.8, 163.0), "No")},
    "x.contract_analyzed": {
        "did": Check(1, (32.8, 200.6), "did"),
        "did_not": Check(1, (57.1, 200.6), "did not"),
    },
    "x.seller_owner": {
        "yes": Check(1, (388.3, 235.4), "Yes"),
        "no": Check(1, (412.7, 235.4), "No"),
    },
    "x.concessions": {"yes": Check(1, (537.4, 247.6), "Yes"), "no": Check(1, (567.9, 247.6), "No")},
    "neighborhood.location": {
        "urban": Check(1, (60.8, 321.1), "Urban"),
        "suburban": Check(1, (108.1, 321.1), "Suburban"),
        "rural": Check(1, (155.3, 321.1), "Rural"),
    },
    "neighborhood.property_values": {
        "increasing": Check(1, (263.3, 321.1), "Increasing"),
        "stable": Check(1, (319.6, 321.1), "Stable"),
        "declining": Check(1, (366.8, 321.1), "Declining"),
    },
    "neighborhood.built_up": {
        "over_75": Check(1, (60.8, 333.3), "Over 75%"),
        "25_75": Check(1, (108.1, 333.3), "25–75%"),
        "under_25": Check(1, (155.3, 333.3), "Under 25%"),
    },
    "neighborhood.demand_supply": {
        "shortage": Check(1, (263.3, 333.3), "Shortage"),
        "in_balance": Check(1, (319.6, 333.3), "In Balance"),
        "over_supply": Check(1, (366.8, 333.3), "Over Supply"),
    },
    "neighborhood.growth": {
        "rapid": Check(1, (60.8, 345.5), "Rapid"),
        "stable": Check(1, (108.1, 345.5), "Stable"),
        "slow": Check(1, (155.3, 345.5), "Slow"),
    },
    "neighborhood.marketing_time": {
        "under_3_mths": Check(1, (263.3, 345.5), "Under 3 mths"),
        "3_6_mths": Check(1, (319.6, 345.5), "3–6 mths"),
        "over_6_mths": Check(1, (366.8, 345.5), "Over 6 mths"),
    },
    "site.zoning_compliance": {
        "legal": Check(1, (98.5, 481.1), "Legal"),
        "legal_nonconforming": Check(1, (135.1, 481.1), "Legal Nonconforming"),
        "no_zoning": Check(1, (293.5, 481.1), "No Zoning"),
        "illegal": Check(1, (346.0, 481.1), "Illegal (describe)"),
    },
    "site.highest_and_best_use": {
        "yes": Check(1, (446.8, 493.3), "Yes"),
        "no": Check(1, (477.3, 493.3), "No"),
    },
    # Utility columns: the box sits under the column header, not before a caption.
    "site.utilities_electric": {
        "public": Check(1, (76.9, 530.0)),
        "other": Check(1, (106.7, 530.0)),
    },
    "site.utilities_gas": {"public": Check(1, (76.9, 542.2)), "other": Check(1, (106.7, 542.2))},
    "site.utilities_water": {"public": Check(1, (280.2, 530.0)), "other": Check(1, (310.7, 530.0))},
    "site.utilities_sewer": {"public": Check(1, (280.2, 542.2)), "other": Check(1, (310.7, 542.2))},
    "site.off_site_improvements_street": {
        "public": Check(1, (537.2, 530.0)),
        "private": Check(1, (570.8, 530.0)),
    },
    "site.off_site_improvements_alley": {
        "public": Check(1, (537.2, 542.2)),
        "private": Check(1, (570.8, 542.2)),
    },
    "site.special_flood_hazard": {
        "yes": Check(1, (144.8, 554.4), "Yes"),
        "no": Check(1, (175.3, 554.4), "No"),
    },
    "x.utilities_typical": {
        "yes": Check(1, (261.6, 566.6), "Yes"),
        "no": Check(1, (292.0, 566.6), "No"),
    },
    "x.adverse": {"yes": Check(1, (456.7, 578.9), "Yes"), "no": Check(1, (487.1, 578.9), "No")},
    "improvements.units": {
        "one": Check(1, (48.2, 643.0), "One"),
        "one_with_accessory_unit": Check(1, (79.3, 643.0), "One with Accessory Unit"),
    },
    "improvements.type": {
        "det": Check(1, (49.7, 667.4), "Det."),
        "att": Check(1, (78.3, 667.4), "Att."),
        "s_det_end_unit": Check(1, (105.9, 667.4), "S-Det./End Unit"),
    },
    "x.existing": {
        "existing": Check(1, (28.7, 679.7), "Existing"),
        "proposed": Check(1, (69.5, 679.7), "Proposed"),
        "under_const": Check(1, (114.2, 679.7), "Under Const."),
    },
    "improvements.foundation_type": {
        "concrete_slab": Check(1, (171.5, 643.0), "Concrete Slab"),
        "crawl_space": Check(1, (236.8, 643.0), "Crawl Space"),
        "full_basement": Check(1, (171.5, 655.3), "Full Basement"),
        "partial_basement": Check(1, (237.2, 655.3), "Partial Basement"),
    },
    "x.sump": {"sump": Check(1, (249.1, 691.9), "Sump Pump")},
    "x.attic": {
        "none": Check(1, (99.7, 730.6), "None"),
        "drop_stair": Check(1, (28.7, 742.8), "Drop Stair"),
        "scuttle": Check(1, (99.7, 755.0), "Scuttle"),
    },
    "improvements.heating_type": {
        "fwa": Check(1, (200.1, 730.6), "FWA"),
        "hwbb": Check(1, (232.7, 730.6), "HWBB"),
        "radiant": Check(1, (271.4, 730.6), "Radiant"),
        "other": Check(1, (171.5, 742.8), "Other"),
    },
    "improvements.cooling": {
        "central": Check(1, (202.7, 755.0), "Central Air Conditioning"),
        "individual": Check(1, (171.5, 767.3), "Individual"),
        "other": Check(1, (242.8, 767.3), "Other"),
    },
    "x.fireplace_box": {"yes": Check(1, (314.2, 742.8), "Fireplace(s) #")},
    "x.patio_box": {"yes": Check(1, (314.2, 755.0), "Patio/Deck")},
    "x.fence_box": {"yes": Check(1, (393.7, 742.8), "Fence")},
    "improvements.car_storage": {
        "none": Check(1, (524.9, 705.2), "None"),
        "driveway": Check(1, (473.1, 717.4), "Driveway"),
        "garage": Check(1, (473.1, 742.8), "Garage"),
        "carport": Check(1, (473.1, 755.0), "Carport"),
        "attached": Check(1, (473.1, 767.3), "Att."),
        "detached": Check(1, (513.5, 767.3), "Det."),
        "built_in": Check(1, (554.0, 767.3), "Built-in"),
    },
    "improvements.appliances": {
        "refrigerator": Check(1, (66.9, 780.5), "Refrigerator"),
        "range_oven": Check(1, (120.3, 780.5), "Range/Oven"),
        "dishwasher": Check(1, (176.2, 780.5), "Dishwasher"),
        "disposal": Check(1, (227.0, 780.5), "Disposal"),
        "microwave": Check(1, (269.8, 780.5), "Microwave"),
        "washer_dryer": Check(1, (317.4, 780.5), "Washer/Dryer"),
        "other": Check(1, (377.0, 780.5), "Other (describe)"),
    },
    "x.deficiencies": {
        "yes": Check(1, (459.3, 891.5), "Yes"),
        "no": Check(1, (489.7, 891.5), "No"),
    },
    "x.conforms": {"yes": Check(1, (411.8, 940.4), "Yes"), "no": Check(1, (442.3, 940.4), "No")},
    # Page 2
    "x.net_sign_1": {"plus": Check(2, (199.8, 459.5), "+"), "minus": Check(2, (224.7, 459.5), "-")},
    "x.net_sign_2": {"plus": Check(2, (335.5, 459.5), "+"), "minus": Check(2, (360.4, 459.5), "-")},
    "x.net_sign_3": {"plus": Check(2, (471.4, 459.5), "+"), "minus": Check(2, (496.3, 459.5), "-")},
    "x.research": {
        "did": Check(2, (32.8, 494.6), "did"),
        "did_not": Check(2, (57.1, 494.6), "did not"),
    },
    "x.prior_subject": {
        "did": Check(2, (71.4, 532.2), "did"),
        "did_not": Check(2, (95.6, 532.2), "did not"),
    },
    "x.prior_comps": {
        "did": Check(2, (71.4, 558.7), "did"),
        "did_not": Check(2, (95.6, 558.7), "did not"),
    },
    "reconciliation.appraisal_made": {
        "as_is": Check(2, (104.8, 893.8), "“as is”,"),
        "subject_to_completion": Check(2, (141.3, 893.8), "subject to completion"),
        "subject_to_repairs": Check(2, (67.0, 904.1), "subject to the following repairs"),
        "subject_to_inspection": Check(2, (534.1, 904.1), "subject to the"),
    },
    # Page 3
    "x.cost_estimated": {
        "reproduction": Check(3, (77.6, 582.8), "REPRODUCTION"),
        "replacement": Check(3, (171.1, 582.8), "REPLACEMENT COST NEW"),
    },
    # Page 6
    "x.inspected": {
        "none": Check(6, (320.0, 818.4), "Did not inspect subject property"),
        "exterior": Check(6, (320.0, 831.7), "Did inspect exterior"),
        "interior": Check(6, (320.0, 858.4), "Did inspect interior and exterior"),
    },
    "x.comps_inspected": {
        "none": Check(6, (320.0, 911.5), "Did not inspect exterior"),
        "exterior": Check(6, (320.0, 924.8), "Did inspect exterior"),
    },
}

# Free-text blocks: (page, first line's bottom rule y on the blank form, x, right limit, lines).
# A line's baseline is 2.6 pt above the rule under it (measured: the form's rows are ruled).
BLOCKS: Final[dict[str, tuple[int, float, float, float, int]]] = {
    "x.nbhd_description": (1, 404.4, 30.0, 588.0, 2),
    "x.market_conditions": (1, 441.1, 30.0, 588.0, 1),
    "x.site_comments": (1, 601.0, 30.0, 588.0, 1),
    "x.condition_desc": (1, 852.6, 30.0, 588.0, 2),
    "x.prior_analysis": (2, 681.7, 30.0, 588.0, 1),
    "x.sales_summary": (2, 755.0, 30.0, 588.0, 3),
    "x.recon_narrative": (2, 866.1, 30.0, 588.0, 2),
    "x.made_conditions": (2, 937.7, 30.0, 588.0, 1),
    "x.additional_comments": (3, 62.2, 30.0, 588.0, 4),
    "x.site_support": (3, 555.1, 30.0, 588.0, 1),
    "x.cost_comments": (3, 642.8, 30.0, 309.0, 3),
    "x.income_summary": (3, 784.8, 30.0, 588.0, 1),
}

# Sales grid (page 2): rows by their column-0 caption, columns measured from the "$" and
# "sq. ft." marks of the price rows. S = subject, Cn = comparable n's description column,
# An = its adjustment column.
GRID_ROWS: Final[dict[str, tuple[str, float, float, float]]] = {
    # row: (caption, x0, bottom, dy)
    "address": ("Address", 27.7, 86, -1.0),
    "address2": ("Address", 27.7, 86, 9.0),
    "proximity": ("Proximity to Subject", 27.7, 109, 0.0),
    "sale_price": ("Sale Price", 27.7, 121, 0.0),
    "price_gla": ("Sale Price/Gross Liv. Area", 27.7, 133, 0.0),
    "data_source": ("Data Source(s)", 27.7, 146, 0.0),
    "verification": ("Verification Source(s)", 27.7, 158, 0.0),
    "concessions": ("Sale or Financing", 27.7, 182, 0.0),
    "concessions2": ("Concessions", 27.7, 193, 0.0),
    "date": ("Date of Sale/Time", 27.7, 205, 0.0),
    "location": ("Location", 27.7, 217, 0.0),
    "leasehold": ("Leasehold/Fee Simple", 27.7, 229, 0.0),
    "site": ("Site", 27.7, 242, 0.0),
    "view": ("View", 27.7, 254, 0.0),
    "design": ("Design (Style)", 27.7, 266, 0.0),
    "quality": ("Quality of Construction", 27.7, 278, 0.0),
    "age": ("Actual Age", 27.7, 290, 0.0),
    "condition": ("Condition", 27.7, 303, 0.0),
    "rooms_total": ("Room Count", 27.7, 327, 0.0),
    "rooms_beds": ("Room Count", 27.7, 327, 0.0),
    "rooms_baths": ("Room Count", 27.7, 327, 0.0),
    "gla": ("Gross Living Area", 27.7, 339, 0.0),
    "basement": ("Basement & Finished", 27.7, 352, 0.0),
    "functional": ("Functional Utility", 27.7, 374, 0.0),
    "heating": ("Heating/Cooling", 27.7, 386, 0.0),
    "energy": ("Energy Efficient Items", 27.7, 399, 0.0),
    "garage": ("Garage/Carport", 27.7, 411, 0.0),
    "porch": ("Porch/Patio/Deck", 27.7, 423, 0.0),
    "net_adj": ("Net Adjustment (Total)", 27.7, 469, 0.0),
    "net_pct": ("Adjusted Sale Price", 27.7, 481, 0.0),
    "gross_pct": ("of Comparables", 27.7, 491, 0.0),
    "adjusted": ("of Comparables", 27.7, 491, 0.0),
    # Adjustment rows (adjustment columns only)
    "gla_adj": ("Gross Living Area", 27.7, 339, 0.0),
    "age_adj": ("Actual Age", 27.7, 290, 0.0),
    "garage_adj": ("Garage/Carport", 27.7, 411, 0.0),
    "site_adj": ("Site", 27.7, 242, 0.0),
    "condition_adj": ("Condition", 27.7, 303, 0.0),
}
_COL_EDGES: Final[dict[str, tuple[float, float]]] = {
    "S": (117.7, 185.3),
    "C1": (185.3, 253.0),
    "A1": (253.0, 321.0),
    "C2": (321.0, 388.8),
    "A2": (388.8, 456.6),
    "C3": (456.6, 524.8),
    "A3": (524.8, 591.5),
}
# Cells with their own geometry (inside the description column): (left, right, align).
_SPECIAL_CELLS: Final[dict[tuple[str, str], tuple[float, float, str]]] = {
    **{
        ("rooms_total", c): (a, b, "center")
        for c, (a, b) in (
            ("S", (117.7, 137.5)),
            ("C1", (185.3, 206.0)),
            ("C2", (321.0, 342.5)),
            ("C3", (456.6, 478.0)),
        )
    },
    **{
        ("rooms_beds", c): (a, b, "center")
        for c, (a, b) in (
            ("S", (137.5, 163.8)),
            ("C1", (206.0, 231.5)),
            ("C2", (342.5, 367.2)),
            ("C3", (478.0, 503.2)),
        )
    },
    **{
        ("rooms_baths", c): (a, b, "center")
        for c, (a, b) in (
            ("S", (163.8, 185.3)),
            ("C1", (231.5, 253.0)),
            ("C2", (367.2, 388.8)),
            ("C3", (503.2, 524.8)),
        )
    },
    # Values right-aligned before "sq. ft." / "%" marks, or after "$" marks.
    ("gla", "S"): (120.0, 164.5, "right"),
    ("gla", "C1"): (187.0, 232.3, "right"),
    ("gla", "C2"): (323.0, 368.1, "right"),
    ("gla", "C3"): (459.0, 504.1, "right"),
    ("price_gla", "S"): (123.4, 164.2, "right"),
    ("price_gla", "C1"): (191.0, 231.8, "right"),
    ("price_gla", "C2"): (326.7, 367.5, "right"),
    ("price_gla", "C3"): (462.3, 503.1, "right"),
    ("sale_price", "S"): (123.4, 185.3, "left"),
    ("sale_price", "A1"): (258.7, 321.0, "left"),
    ("sale_price", "A2"): (394.5, 456.6, "left"),
    ("sale_price", "A3"): (530.5, 592.0, "left"),
    ("net_adj", "A1"): (258.7, 321.0, "left"),
    ("net_adj", "A2"): (394.5, 456.6, "left"),
    ("net_adj", "A3"): (530.5, 592.0, "left"),
    ("adjusted", "A1"): (258.7, 321.0, "left"),
    ("adjusted", "A2"): (394.5, 456.6, "left"),
    ("adjusted", "A3"): (530.5, 592.0, "left"),
    ("net_pct", "C1"): (212.7, 241.5, "right"),
    ("net_pct", "C2"): (348.4, 377.1, "right"),
    ("net_pct", "C3"): (484.0, 514.8, "right"),
    ("gross_pct", "C1"): (220.9, 241.5, "right"),
    ("gross_pct", "C2"): (356.5, 377.1, "right"),
    ("gross_pct", "C3"): (492.1, 514.8, "right"),
}


def grid_slot(row: str, col: str) -> Slot:
    caption, x0, bottom, dy = GRID_ROWS[row]
    special = _SPECIAL_CELLS.get((row, col))
    if special is not None:
        left, right, align = special
    else:
        left, right = _COL_EDGES[col]
        align = "right" if col.startswith("A") else "left"
    if col.startswith("C") and row in (
        "address",
        "address2",
        "proximity",
        "data_source",
        "verification",
    ):
        right = _COL_EDGES["A" + col[1]][1]  # these rows span the adjustment column
    # The subject's address cell is a third as wide as a comparable's: software sets it
    # smaller there.
    floor = 4.8 if col == "S" and row in ("address", "address2") else MIN_SIZE
    if align == "right":
        return Slot(2, caption, (x0, bottom), "right", x=right - 2.0, limit=left + 1.0, dy=dy)
    if align == "center":  # sized against the whole cell, then centred by placements()
        return Slot(2, caption, (x0, bottom), "left", x=left + 1.5, limit=right, dy=dy)
    return Slot(2, caption, (x0, bottom), "left", x=left + 3.0, limit=right, dy=dy, min_size=floor)


# --------------------------------------------------------------------------- geometry


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    baseline: float


@dataclass(frozen=True, slots=True)
class Placed:
    """A resolved draw: text, left x, baseline, size, bold, and its page."""

    page: int
    text: str
    x: float
    y: float
    size: float
    bold: bool
    name: str

    @property
    def width(self) -> float:
        return text_width(self.text, self.size, self.bold)

    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x, self.y - 0.72 * self.size, self.x + self.width, self.y + 0.2 * self.size)


def _pymupdf() -> Any:
    """pymupdf, untyped (its partial annotations do not satisfy ``mypy --strict``)."""
    import importlib

    return importlib.import_module("pymupdf")


def text_width(text: str, size: float, bold: bool = False) -> float:
    pymupdf = _pymupdf()

    return float(pymupdf.get_text_length(text, fontname=FONT_BOLD if bold else FONT, fontsize=size))


class LayoutError(RuntimeError):
    """The blank form does not have a caption or checkbox where the layout expects it."""


class FormGeometry:
    """Captions, checkbox squares and ruled lines of the blank form, by page."""

    def __init__(self, pdf_bytes: bytes) -> None:
        import pdfplumber

        self.pdf_bytes = pdf_bytes
        self.words: dict[int, list[Word]] = {}
        self.squares: dict[int, list[tuple[float, float, float, float]]] = {}
        self.rules: dict[int, list[tuple[float, float, float]]] = {}
        self.chars: dict[int, list[tuple[str, float, float, float, float]]] = {}
        self.glyphs: dict[int, int] = {}
        self.underscores: dict[int, list[tuple[float, float, float]]] = {}
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) != FORM_PAGES:
                raise LayoutError(f"the blank form has {len(pdf.pages)} pages, not {FORM_PAGES}")
            for number, page in enumerate(pdf.pages, start=1):
                height = float(page.height)
                words: list[Word] = []
                for w in page.extract_words(return_chars=True):
                    first = w["chars"][0]
                    baseline = height - float(first["matrix"][5])
                    words.append(Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], baseline))
                self.words[number] = words
                self.squares[number] = [
                    (r["x0"], r["top"], r["x1"], r["bottom"])
                    for r in page.rects
                    if 7.5 < r["width"] < 8.6 and 7.5 < r["height"] < 8.6
                ]
                self.rules[number] = [
                    (r["x0"], r["x1"], r["top"])
                    for r in page.rects
                    if r["height"] < 1.5 and r["width"] > 150
                ]
                self.glyphs[number] = sum(
                    1 for c in page.chars if c["text"].strip() and c["text"] != "_"
                )
                self.underscores[number] = [
                    (c["x0"], c["x1"], height - float(c["matrix"][5]))
                    for c in page.chars
                    if c["text"] == "_"
                ]
                self.chars[number] = [
                    (c["text"], c["x0"], c["top"], c["x1"], c["bottom"])
                    for c in page.chars
                    if c["text"] not in "_." and c["text"].strip()
                ]

    def find(self, page: int, phrase: str, near: tuple[float, float]) -> Word:
        """The occurrence of ``phrase`` (consecutive words on one line) nearest ``near``."""
        tokens = phrase.split()
        words = self.words[page]
        best: tuple[float, Word] | None = None
        for i, first in enumerate(words):
            if not _token_matches(first.text, tokens[0], last=len(tokens) == 1):
                continue
            seq = [first]
            ok = True
            for k, token in enumerate(tokens[1:], start=1):
                j = i + k
                if j >= len(words) or abs(words[j].bottom - first.bottom) > 1.5:
                    ok = False
                    break
                if not _token_matches(words[j].text, token, last=k == len(tokens) - 1):
                    ok = False
                    break
                seq.append(words[j])
            if not ok:
                continue
            distance = math.hypot(first.x0 - near[0], first.bottom - near[1])
            if best is None or distance < best[0]:
                last = seq[-1]
                best = (
                    distance,
                    Word(phrase, first.x0, last.x1, first.top, first.bottom, first.baseline),
                )
        if best is None or best[0] > 4.0:
            raise LayoutError(f"page {page}: caption {phrase!r} not found near {near}")
        return best[1]

    def resolve(self, slot: Slot, text: str, name: str) -> Placed:
        anchor = self.find(slot.page, slot.anchor, slot.near)
        y = anchor.baseline + slot.dy
        size = slot.size
        if slot.how == "after":
            x = anchor.x1 + WORD_GAP
            right = (slot.limit if slot.limit is not None else 592.0) - WORD_GAP
        elif slot.how == "left":
            assert slot.x is not None
            x = slot.x
            right = (slot.limit if slot.limit is not None else 592.0) - (
                WORD_GAP if slot.limit is not None and slot.limit < 592.0 else 0.0
            )
        else:
            assert slot.x is not None
            # x is the next printed mark (text stops WORD_GAP short of it), or past 585 the
            # box's right border inset (text ends exactly there).
            right = slot.x if slot.x > 585.0 else slot.x - WORD_GAP
            if name == "cost_approach.depreciation":
                right = slot.x - 0.8  # joined to the form's ")" as software prints "=$( 156,240)"
            left_bound = (slot.limit if slot.limit is not None else 0.0) + WORD_GAP
            while text_width(text, size, slot.bold) > right - left_bound and size > slot.min_size:
                size = round(size - 0.25, 2)
            if text_width(text, size, slot.bold) > right - left_bound + 0.01:
                raise LayoutError(f"{name}: {text!r} does not fit its box")
            x = right - text_width(text, size, slot.bold)
            return Placed(slot.page, text, x, y, size, slot.bold, name)
        while text_width(text, size, slot.bold) > right - x and size > slot.min_size:
            size = round(size - 0.25, 2)
        if text_width(text, size, slot.bold) > right - x + 0.01:
            raise LayoutError(f"{name}: {text!r} does not fit its box")
        return Placed(slot.page, text, x, y, size, slot.bold, name)

    def square(self, check: Check, name: str) -> tuple[float, float, float, float]:
        candidates = [
            s
            for s in self.squares[check.page]
            if abs(s[0] - check.near[0]) < 2 and abs(s[1] - check.near[1]) < 2
        ]
        if len(candidates) != 1:
            raise LayoutError(
                f"{name}: {len(candidates)} checkbox squares near {check.near} on page {check.page}"
            )
        box = candidates[0]
        if check.label is not None:
            cy = (box[1] + box[3]) / 2
            right = sorted(
                (
                    w
                    for w in self.words[check.page]
                    if abs((w.top + w.bottom) / 2 - cy) < 5 and w.x0 >= box[2] - 1
                ),
                key=lambda w: w.x0,
            )
            first = check.label.split()[0]
            if not right or right[0].x0 - box[2] > 6 or not right[0].text.startswith(first):
                found = right[0].text if right else None
                raise LayoutError(
                    f"{name}: the box at {check.near} is not followed by "
                    f"{check.label!r} ({found!r})"
                )
        return box

    def block_baselines(self, page: int, first_rule: float, lines: int) -> list[float]:
        ys = sorted({round(r[2], 1) for r in self.rules[page] if r[2] >= first_rule - 0.6})
        if len(ys) < lines:
            raise LayoutError(f"page {page}: not enough ruled lines below {first_rule}")
        if abs(ys[0] - first_rule) > 0.6:
            raise LayoutError(f"page {page}: no rule at {first_rule}")
        return [y - 2.6 for y in ys[:lines]]


def _token_matches(word: str, token: str, *, last: bool) -> bool:
    if word == token:
        return True
    # A caption fused with the form's leader dots or underscores ("VALUE.....=", "Name____").
    return last and word.startswith(token) and set(word[len(token) :]) <= set("._=$#")


def x_mark(box: tuple[float, float, float, float], name: str, label_gap: float) -> Placed:
    """An ``X`` centred in the square, nudged left when the caption follows closely, so the
    text layer reads ``X Caption`` (two words) as appraisal software output does."""
    size = 7.0
    width = text_width("X", size, True)
    cx = (box[0] + box[2]) / 2
    shift = 0.0
    if label_gap < 3.0:
        shift = min(1.1, (box[2] - box[0] - width) / 2 - 0.4)
    x = cx - width / 2 - shift
    baseline = (box[1] + box[3]) / 2 + 0.36 * size
    return Placed(0, "X", x, baseline, size, True, name)


# --------------------------------------------------------------------------- render


def placements(
    plan: DocPlan, geo: FormGeometry
) -> tuple[list[Placed], dict[str, list[tuple[float, float, float, float]]]]:
    """Every draw on the form pages, and the squares marked per check group."""
    out: list[Placed] = []
    marked: dict[str, list[tuple[float, float, float, float]]] = {}

    def text_at(slot_name: str, text: str, slot: Slot | None = None) -> None:
        resolved = geo.resolve(slot or SLOTS[slot_name], text, slot_name)
        out.append(resolved)

    for fp in plan.fields.values():
        if fp.status not in ("present", "not_developed"):
            continue
        if fp.slot is not None and fp.printed is not None:
            text_at(fp.slot, fp.printed)
    for name, text in plan.texts.items():
        if name in SLOTS:
            text_at(name, text)
    for name, lines in plan.blocks.items():
        page, rule, x, right, capacity = BLOCKS[name]
        baselines = geo.block_baselines(page, rule, capacity)
        for line, y in zip(lines[:capacity], baselines, strict=False):
            size = VALUE_SIZE
            while text_width(line, size) > right - x and size > MIN_SIZE:
                size = round(size - 0.25, 2)
            if text_width(line, size) > right - x:
                raise LayoutError(f"{name}: {line!r} does not fit its line")
            out.append(Placed(page, line, x, y, size, False, name))
    for (row, col), text in plan.grid.items():
        gname = f"grid.{row}.{col}"
        slot = grid_slot(row, col)
        resolved = geo.resolve(slot, text, gname)
        special = _SPECIAL_CELLS.get((row, col))
        if special is not None and special[2] == "center":
            middle = (special[0] + special[1]) / 2
            resolved = Placed(
                2, text, middle - resolved.width / 2, resolved.y, resolved.size, False, gname
            )
        out.append(resolved)
    groups: dict[str, tuple[str, ...]] = {
        k: fp.checks for k, fp in plan.fields.items() if fp.checks
    }
    groups.update(plan.checks)
    for group, codes in groups.items():
        for code in codes:
            check = CHECKS[group][code]
            box = geo.square(check, f"{group}.{code}")
            gap = _label_gap(geo, check.page, box)
            mark = x_mark(box, f"check.{group}.{code}", gap)
            out.append(Placed(check.page, mark.text, mark.x, mark.y, mark.size, True, mark.name))
            marked.setdefault(group, []).append(box)
    return out, marked


def _label_gap(geo: FormGeometry, page: int, box: tuple[float, float, float, float]) -> float:
    cy = (box[1] + box[3]) / 2
    right = [
        w.x0 - box[2]
        for w in geo.words[page]
        if abs((w.top + w.bottom) / 2 - cy) < 5 and w.x0 >= box[2] - 1
    ]
    return min(right) if right else 99.0


def render_document(plan: DocPlan, geo: FormGeometry) -> bytes:
    """The filled text PDF (the scan variant is made from it by :func:`rasterize`)."""
    pymupdf = _pymupdf()

    draws, _ = placements(plan, geo)
    for number in range(1, FORM_PAGES + 1):
        try:
            anchor = geo.find(number, "File #", (485.6, 35))
        except LayoutError:
            continue
        draws.append(
            Placed(
                number,
                plan.file_no,
                anchor.x1 + WORD_GAP,
                anchor.baseline,
                VALUE_SIZE,
                False,
                "x.file_no",
            )
        )
    doc = pymupdf.open(stream=geo.pdf_bytes, filetype="pdf")
    # The blank form fills its boxes with space glyphs; a value drawn over them would read
    # "4 5 0" in the text layer, which appraisal software output never does. Remove the
    # glyphs under each value (text only: no fill, no line art or images touched). On page
    # 6 the glyphs under a value are underscores: they go too and the rule is redrawn.
    rules: dict[int, list[tuple[float, float, float]]] = {}
    for number in range(1, FORM_PAGES + 1):
        page = doc[number - 1]
        rects = []
        for d in draws:
            if d.page != number:
                continue
            x0, top, x1, bottom = d.bbox()
            rects.append((x0 + 0.3, top, x1 - 0.3, bottom))
            under = [
                u
                for u in geo.underscores[number]
                if abs(u[2] - d.y) < 1.5
                and u[1] > d.x - 20.0
                and u[0] < d.x + d.width + WORD_GAP + 0.5
            ]
            if under:
                ux0, ux1 = min(u[0] for u in under), max(u[1] for u in under)
                rects.append((ux0 + 0.2, d.y - 5.0, ux1 - 0.2, d.y + 1.0))
                rules.setdefault(number, []).append((ux0, ux1, under[0][2] + UNDERSCORE_STROKE))
        for rect in rects:
            page.add_redact_annot(pymupdf.Rect(*rect), fill=False, cross_out=False)
        if rects:
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        for ux0, ux1, y in rules.get(number, []):
            page.draw_line((ux0, y), (ux1, y), color=(0, 0, 0), width=0.5)
    for d in draws:
        page = doc[d.page - 1]
        page.insert_text(
            (d.x, d.y), d.text, fontname=FONT_BOLD if d.bold else FONT, fontsize=d.size
        )
    _signature(doc[5], plan)
    if plan.profile.addendum:
        _addendum_page(doc, plan)
    if plan.profile.photo_page:
        _photo_page(doc, plan)
    doc.set_metadata(
        {
            "title": "Uniform Residential Appraisal Report",
            "author": "",
            "subject": plan.file_no,
            "creator": "synth_1004",
            "producer": "synth_1004",
            "creationDate": "",
            "modDate": "",
            "keywords": "",
        }
    )
    data: bytes = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    doc.close()
    return data


def _signature(page: Any, plan: DocPlan) -> None:
    rng = random.Random(f"sig:{plan.name}")
    points = []
    for i in range(40):
        x = 100 + i * 3.6
        y = 626 + 4.5 * math.sin(i * 0.9 + rng.random()) - (i % 7) * 0.4
        points.append((x, y))
    page.draw_polyline(points, color=(0.1, 0.1, 0.35), width=0.9)


def _page_header(page: Any, plan: DocPlan, title: str) -> float:
    page.insert_text((36, 40), title, fontname=FONT_BOLD, fontsize=13)
    page.insert_text((470, 40), f"File # {plan.file_no}", fontname=FONT, fontsize=VALUE_SIZE)
    page.draw_rect((32, 48, 580, 108), color=(0, 0, 0), width=0.6)
    fp = plan.fields
    rows = [
        ("Borrower", fp["subject.borrower"].printed or ""),
        ("Property Address", fp["subject.property_address"].printed or ""),
        (
            "City",
            f"{fp['subject.city'].printed}    County {fp['subject.county'].printed}    "
            f"State {fp['subject.state'].printed}    Zip Code {fp['subject.zip'].printed}",
        ),
        ("Lender/Client", fp["subject.lender_client"].printed or ""),
    ]
    y = 62.0
    for caption, value in rows:
        page.insert_text((38, y), caption, fontname=FONT, fontsize=7.5)
        page.insert_text((120, y), value, fontname=FONT, fontsize=VALUE_SIZE)
        y += 13.0
    return 120.0


def _addendum_page(doc: Any, plan: DocPlan) -> None:
    page = doc.new_page(width=PAGE_W, height=LETTER_H)
    y = _page_header(page, plan, "Supplemental Addendum")
    headings = {
        "Neighborhood Comments",
        "Site Comments",
        "Improvements Comments",
        "Cost Approach Comments",
        "Reconciliation",
    }
    for line in plan.addendum_lines:
        if line in headings:
            y += 8
            page.insert_text((38, y), line, fontname=FONT_BOLD, fontsize=9)
        else:
            page.insert_text((38, y), line, fontname=FONT, fontsize=VALUE_SIZE)
        y += 13


def _photo_page(doc: Any, plan: DocPlan) -> None:
    page = doc.new_page(width=PAGE_W, height=LETTER_H)
    y0 = _page_header(page, plan, "Subject Photo Page")
    rng = random.Random(f"photo:{plan.name}")
    captions = ("Subject Front", "Subject Rear", "Subject Street")
    for i, caption in enumerate(captions):
        top = y0 + i * 220
        rect = (38, top, 338, top + 205)
        sky = (0.55 + rng.random() * 0.2, 0.7 + rng.random() * 0.15, 0.9)
        page.draw_rect(rect, color=None, fill=sky)
        page.draw_rect(
            (38, top + 140, 338, top + 205), color=None, fill=(0.35, 0.55 + rng.random() * 0.1, 0.3)
        )
        hx = 90 + rng.random() * 60
        wall = (0.75 + rng.random() * 0.2, 0.7, 0.6)
        page.draw_rect((hx, top + 85, hx + 150, top + 160), color=None, fill=wall)
        page.draw_polyline(
            [(hx - 10, top + 85), (hx + 75, top + 40), (hx + 160, top + 85)],
            color=None,
            fill=(0.35, 0.25, 0.2),
            closePath=True,
        )
        page.draw_rect((hx + 65, top + 120, hx + 85, top + 160), color=None, fill=(0.4, 0.25, 0.15))
        page.insert_text((350, top + 12), caption, fontname=FONT_BOLD, fontsize=9)
        if i == 0:
            page.insert_text(
                (350, top + 26),
                plan.fields["subject.property_address"].printed or "",
                fontname=FONT,
                fontsize=VALUE_SIZE,
            )
            y = top + 44
            for cap, value in plan.photo_block:
                page.insert_text((350, y), cap, fontname=FONT, fontsize=7.5)
                page.insert_text((450, y), value, fontname=FONT, fontsize=VALUE_SIZE)
                y += 12.5


def rasterize(pdf_bytes: bytes, plan: DocPlan) -> bytes:
    """An image-only copy: each page at ``plan.scan_dpi``, rotated slightly, with noise."""
    import numpy as np

    pymupdf = _pymupdf()
    from PIL import Image

    rng = np.random.default_rng(abs(hash_int(plan.name)) % (2**32))
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=plan.scan_dpi, colorspace=pymupdf.csGRAY)
        image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        angle = plan.scan_angle + float(rng.uniform(-0.15, 0.15))
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
        arr = np.asarray(image, dtype=np.float64)
        arr = arr + rng.normal(0, 7.0, arr.shape)
        speckle = rng.random(arr.shape)
        arr[speckle < 0.0008] = 40.0
        arr[speckle > 0.9994] = 255.0
        arr = np.clip(arr * 0.97 + 6.0, 0, 255).astype(np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(arr, mode="L").save(buffer, format="JPEG", quality=72, optimize=False)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buffer.getvalue())
    out.set_metadata(
        {
            "title": "scan",
            "producer": "synth_1004",
            "creator": "synth_1004",
            "creationDate": "",
            "modDate": "",
        }
    )
    data: bytes = out.tobytes(garbage=3, deflate=True, no_new_id=True)
    out.close()
    src.close()
    return data


def hash_int(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


# --------------------------------------------------------------------------- truth


_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def page_texts(pdf_bytes: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


# Captions for quotes where the slot's anchor is not the box caption.
QUOTE_CAPTIONS: Final[dict[str, str | None]] = {
    "cost_approach.site_value": "OPINION OF SITE VALUE",
    "reconciliation.final_market_value": None,
    "cost_approach.mechanicals_misc": "Porches/Patio/Misc.",
    "cost_approach.basement_cost": "Basement",
    "cost_approach.basement_sqft": "Basement",
    "cost_approach.basement_cost_per_sqft": "Basement",
}
# Checkbox columns (utilities, off-site improvements): the row caption opens the quote.
ROW_CAPTIONS: Final[dict[str, str]] = {
    "site.utilities_electric": "Electricity",
    "site.utilities_gas": "Gas",
    "site.utilities_water": "Water",
    "site.utilities_sewer": "Sanitary Sewer",
    "site.off_site_improvements_street": "Street",
    "site.off_site_improvements_alley": "Alley",
}
# Anchors that are not a caption worth quoting.
_NOT_CAPTIONS: Final = frozenset({"$", "Comments on Cost Approach", "Sq. Ft. @ $"})


@dataclass(frozen=True, slots=True)
class TextLine:
    """One text-layer line as pdfplumber's ``extract_text`` joins it, with each word's x."""

    top: float
    bottom: float
    text: str
    starts: tuple[tuple[float, int], ...]  # (word x0, index of the word in text)

    def index_at(self, x: float) -> int | None:
        """Where the word drawn at ``x`` starts in ``text``."""
        best = min(self.starts, key=lambda s: abs(s[0] - x), default=None)
        return best[1] if best is not None and abs(best[0] - x) < 0.8 else None


Lines = dict[int, list[TextLine]]


def text_lines(pdf_bytes: bytes) -> Lines:
    """Every text-layer line per page, words joined by one space (as ``extract_text``)."""
    import pdfplumber
    from pdfplumber.utils.clustering import cluster_objects

    out: Lines = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            lines: list[TextLine] = []
            for cluster in cluster_objects(page.extract_words(), lambda w: w["top"], 3):
                words = sorted(cluster, key=lambda w: w["x0"])
                text = ""
                starts: list[tuple[float, int]] = []
                for word in words:
                    if text:
                        text += " "
                    starts.append((float(word["x0"]), len(text)))
                    text += word["text"]
                top = min(float(w["top"]) for w in words)
                bottom = max(float(w["bottom"]) for w in words)
                lines.append(TextLine(top, bottom, text, tuple(starts)))
            out[number] = lines
    return out


def line_at(lines: Lines, page: int, baseline: float) -> TextLine | None:
    """The text line whose vertical extent holds a glyph drawn on ``baseline``."""
    y = baseline - 2.5
    holding = [line for line in lines.get(page, []) if line.top - 1 <= y <= line.bottom + 1]
    if not holding:
        return None
    return min(holding, key=lambda line: abs((line.top + line.bottom) / 2 - y))


def make_quote(text: str, at: int, printed: str, caption: str | None) -> str | None:
    """The span of a line from the nearest caption before the value (at index ``at``) to
    the value's end, or from the value to a caption right after it; else the value with a
    little left context."""
    if text[at : at + len(printed)] != printed:
        return None
    end = at + len(printed)
    if caption:
        before = text.rfind(caption, 0, at)
        after = text[end:].lstrip()
        if after.startswith(caption) and (before < 0 or at - before > 60):
            return text[at : len(text) - len(after) + len(caption)]
        if before >= 0:
            return text[before:end]
    begin = max(0, at - 24)
    if begin > 0:
        space = text.find(" ", begin)
        begin = space + 1 if 0 <= space < at else at
    return text[begin:end]


def _check_quote(key: str, fp: FieldPlan, drawn: dict[str, Placed], lines: Lines) -> str | None:
    for code in fp.checks:
        mark = drawn[f"check.{key}.{code}"]
        line = line_at(lines, mark.page, mark.y)
        at = line.index_at(mark.x) if line is not None else None
        if line is None or at is None or (line.text + " ")[at : at + 2] != "X ":
            continue
        row = ROW_CAPTIONS.get(key)
        if row is not None:
            if code == "other" and fp.printed is not None:
                value_at = line.text.find(fp.printed, at)
                return make_quote(line.text, value_at, fp.printed, row) if value_at >= 0 else None
            begin = line.text.rfind(row, 0, at)
            return line.text[begin : at + 1] if begin >= 0 else None
        label = ENUM_LABELS[key][code]
        if code == "other" and key == "improvements.heating_type":
            label = f"Other {(fp.printed or '').split('; ')[-1]}"
        rest = line.text[at + 2 :]
        for caption in (label, CHECKS[key][code].label or label):
            if rest.startswith(caption):  # a caption that wraps keeps its first line here
                return f"X {caption}"
    return None


def field_quotes(plan: DocPlan, geo: FormGeometry, lines: Lines) -> dict[str, str | None]:
    """A text-layer quote for every field with a printed value (keyed by catalogue key).

    The value is found by the word that starts where it was drawn, so a short value ("0",
    "3") is quoted from its own box, never from another number that contains it.
    """
    draws, _ = placements(plan, geo)
    drawn = {d.name: d for d in draws}
    quotes: dict[str, str | None] = {}
    for key, fp in plan.fields.items():
        if fp.printed is None or fp.page is None:
            continue
        if fp.checks:
            quotes[key] = _check_quote(key, fp, drawn, lines)
            continue
        slot_name = fp.slot or COMPOSITE_SOURCES.get(key)
        d = drawn.get(slot_name) if slot_name else None
        line = line_at(lines, d.page, d.y) if d is not None else None
        if d is None or line is None or slot_name is None:
            quotes[key] = None
            continue
        at = line.index_at(d.x)
        if at is not None and fp.slot is None:  # inside a composite line
            at = line.text.find(fp.printed, at)
        caption = QUOTE_CAPTIONS.get(key, SLOTS[slot_name].anchor)
        if caption in _NOT_CAPTIONS:
            caption = None
        quotes[key] = (
            make_quote(line.text, at, fp.printed, caption) if at is not None and at >= 0 else None
        )
    return quotes


def document_truth(
    plan: DocPlan,
    pdf_rel: str,
    sha256: str,
    pages: int,
    quotes: dict[str, str | None] | None = None,
) -> DocumentTruth:
    """The truth entry; ``quotes`` from :func:`field_quotes` (None when built without a PDF)."""
    fields: list[FieldTruth] = []
    for key in sorted(plan.fields):
        fp = plan.fields[key]
        quote = (quotes or {}).get(key)
        fields.append(
            FieldTruth(
                key=key,
                status=fp.status,
                type=fp.type,
                value=fp.value,
                printed=fp.printed,
                page=fp.page,
                also_on=fp.also_on,
                quote=quote,
                verified=True,
                note=fp.note,
            )
        )
    note = plan.profile.describe()
    if plan.profile.scan:
        note += (
            f"; scan {plan.scan_dpi} dpi, rotated {plan.scan_angle:+.2f} deg, noise; "
            "quotes from the text version"
        )
    return DocumentTruth(
        document=plan.name,
        source="synthetic",
        form=FORM_ID,
        split=plan.profile.split,
        pdf=pdf_rel,
        sha256=sha256,
        pages=pages,
        fields=tuple(fields),
        expected_issues=tuple(plan.expected_issues),
        note=note,
    )


# --------------------------------------------------------------------------- verify


def verify_document(plan: DocPlan, pdf_bytes: bytes, geo: FormGeometry) -> list[str]:
    """Problems with a generated text PDF (an empty list means it is right)."""
    import pdfplumber

    problems: list[str] = []
    texts = page_texts(pdf_bytes)
    if len(texts) != plan.pages:
        problems.append(f"{len(texts)} pages, planned {plan.pages}")
        return problems
    normed = [_norm(t) for t in texts]
    for key, fp in plan.fields.items():
        if fp.printed is None or fp.page is None:
            continue
        parts = fp.printed.split("; ") if fp.type == "enum_multi" else [fp.printed]
        for part in parts:
            if part not in normed[fp.page - 1]:
                problems.append(f"{key}: {part!r} not on page {fp.page}")
        for other in fp.also_on:
            needle = fp.also_text or fp.printed
            if needle not in normed[other - 1]:
                problems.append(f"{key}: {needle!r} not on also_on page {other}")
    quotes = field_quotes(plan, geo, text_lines(pdf_bytes))
    for key, fp in plan.fields.items():
        if fp.printed is None:
            continue
        quote = quotes.get(key)
        if quote is None:
            problems.append(f"{key}: no quote")
        elif quote not in normed[(fp.page or 1) - 1]:
            problems.append(f"{key}: quote {quote!r} is not in the page text")
        elif (
            key not in ROW_CAPTIONS
            and fp.type != "enum_multi"
            and fp.printed not in quote
            and not (fp.checks and quote.startswith("X "))  # a wrapped checkbox caption
        ):
            problems.append(f"{key}: quote {quote!r} lacks {fp.printed!r}")
    draws, marked = placements(plan, geo)
    # Every X inside its square; no X in an unmarked square of the same group.
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        xs: dict[int, list[tuple[float, float]]] = {}
        for number in range(1, FORM_PAGES + 1):
            xs[number] = [
                ((c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2)
                for c in pdf.pages[number - 1].chars
                if c["text"] == "X" and "Bold" in c["fontname"] and c["size"] < 7.5
            ]
    groups: dict[str, tuple[str, ...]] = {
        k: fp.checks for k, fp in plan.fields.items() if fp.checks
    }
    groups.update(plan.checks)
    for group, options in CHECKS.items():
        chosen = groups.get(group, ())
        for code, check in options.items():
            box = geo.square(check, f"{group}.{code}")
            inside = [
                p for p in xs[check.page] if box[0] < p[0] < box[2] and box[1] < p[1] < box[3]
            ]
            if code in chosen and len(inside) != 1:
                problems.append(f"{group}.{code}: {len(inside)} X marks in its square")
            if code not in chosen and inside:
                problems.append(f"{group}.{code}: marked but not chosen")
    # The space-glyph removal under values must not have removed any printed form text.
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for number in range(1, FORM_PAGES + 1):
            got = sum(
                1 for c in pdf.pages[number - 1].chars if c["text"].strip() and c["text"] != "_"
            )
            drawn = sum(len(d.text.replace(" ", "")) for d in draws if d.page == number)
            drawn += len(plan.file_no) if any(w.text == "File" for w in geo.words[number]) else 0
            if got - drawn != geo.glyphs[number]:
                problems.append(
                    f"page {number}: {geo.glyphs[number] - (got - drawn)} form characters lost"
                )
    total_marks = sum(len(v) for v in xs.values())
    planned_marks = sum(len(v) for v in groups.values())
    if total_marks != planned_marks:
        problems.append(f"{total_marks} X marks on the form, planned {planned_marks}")
    problems += overlap_problems(draws, geo)
    return problems


def overlap_problems(draws: Sequence[Placed], geo: FormGeometry) -> list[str]:
    """Drawn text that overlaps a form character, another drawn value, or leaves the page."""
    problems: list[str] = []
    by_page: dict[int, list[Placed]] = {}
    for d in draws:
        by_page.setdefault(d.page, []).append(d)
    for page, items in by_page.items():
        chars = geo.chars[page]
        for d in items:
            x0, top, x1, bottom = d.bbox()
            if x0 < 18 or x1 > 594:
                problems.append(f"{d.name}: outside the form ({x0:.1f}-{x1:.1f})")
            for text, cx0, ctop, cx1, cbottom in chars:
                if d.name.startswith("check."):
                    continue
                if (
                    cx0 < x1 - 0.4
                    and cx1 > x0 + 0.4
                    and ctop < bottom - 0.6
                    and cbottom > top + 0.6
                ):
                    problems.append(
                        f"{d.name}: {d.text!r} overlaps form text {text!r} at {cx0:.1f},{ctop:.1f}"
                    )
                    break
        for i, a in enumerate(items):
            ax0, atop, ax1, abottom = a.bbox()
            for b in items[i + 1 :]:
                bx0, btop, bx1, bbottom = b.bbox()
                if (
                    bx0 < ax1 - 0.3
                    and bx1 > ax0 + 0.3
                    and btop < abottom - 0.5
                    and bbottom > atop + 0.5
                ):
                    problems.append(f"{a.name} overlaps {b.name}")
    return problems


# --------------------------------------------------------------------------- generate


@dataclass
class Generated:
    plan: DocPlan
    truth: DocumentTruth
    problems: list[str] = field(default_factory=list)


def plan_set(count: int, scans: int, seed: int) -> list[DocPlan]:
    profiles = assign_profiles(count, scans, seed)
    return [plan_document(i, profile, seed, count) for i, profile in enumerate(profiles)]


def generate(
    form_pdf: Path, out_dir: Path, *, count: int, scans: int, seed: int
) -> tuple[TruthSet, list[Generated]]:
    """Write every PDF under ``out_dir/synthetic_1004/`` and return the truth set."""
    geo = FormGeometry(form_pdf.read_bytes())
    results: list[Generated] = []
    for plan in plan_set(count, scans, seed):
        text_pdf = render_document(plan, geo)
        problems = verify_document(plan, text_pdf, geo)
        quotes = field_quotes(plan, geo, text_lines(text_pdf))
        data = rasterize(text_pdf, plan) if plan.profile.scan else text_pdf
        rel = f"{SET_NAME}/{plan.name}/appraisal.pdf"
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        truth = document_truth(plan, rel, hashlib.sha256(data).hexdigest(), plan.pages, quotes)
        results.append(Generated(plan, truth, problems))
    truth_set = TruthSet(set=SET_NAME, documents=tuple(r.truth for r in results))
    return truth_set, results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.appraisal.synth_1004", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--count", type=int, default=24, help="text documents (default 24)")
    parser.add_argument(
        "--scans", type=int, default=3, help="scan documents in addition (default 3)"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="appraisal data root (default $BASIS_EVAL_DATA_DIR/appraisal)",
    )
    parser.add_argument(
        "--form", type=Path, default=None, help=f"blank form (default <data>/appraisal/{FORM_FILE})"
    )
    args = parser.parse_args(argv)
    out_dir = args.out if args.out is not None else appraisal_dir()
    form = args.form if args.form is not None else appraisal_dir() / FORM_FILE
    truth_set, results = generate(form, out_dir, count=args.count, scans=args.scans, seed=args.seed)
    path = out_dir / "truth" / f"{SET_NAME}.json"
    dump_truth(truth_set, path)
    bad = 0
    for r in results:
        status = "ok" if not r.problems else f"{len(r.problems)} problems"
        print(f"{r.plan.name}: {r.plan.pages} pages, {r.plan.profile.split}, {status}")
        for problem in r.problems[:20]:
            print(f"    {problem}")
        bad += bool(r.problems)
    print(f"wrote {len(results)} documents and {path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
