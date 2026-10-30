"""The structure model: a top-down replacement cost of the building, split into systems,
carved against the itemized assets, depreciated and reconciled to the purchase price less
land (docs/review_2026-10-15/STRUCTURE_MODEL.md; ATG ch. 3 A(6), C.2, D(3)-(6)).

Pure: ``Decimal`` end to end, no I/O. The pieces, in the order the result uses them:

1. :func:`cost_approach_from_fields`: the appraisal's cost approach, quality and room counts
   read from the stored extraction fields (the same ``FieldRow`` protocol as
   :mod:`basis.domain.measurements`).
2. :func:`select_model`: the RSMeans Residential 2020 square-foot model for the house's class
   of construction (from the UAD quality rating, else the cost service's words) and
   building type (from the stories).
3. :func:`building_rcn`: the building's replacement cost new: the RSMeans square-foot model
   sized by the facing page's matrix (the sweep's pick, section 7 of the design doc), else
   the appraisal's cost new (printed total, cross-checked against its parts, brought from
   the cost data's year to the cost year; the runner-up, selectable), else ``None``.
4. :func:`split_components`: the RCN over the model's nine components by the model page's
   dollars per square foot, the Overhead component spread pro rata (ATG ch. 4 C.11).
5. :data:`LABEL_SYSTEMS`: every component label and every structure label mapped to one of
   the twelve building systems and to the model component it belongs to (or none).
6. :func:`carve_out`: each component's top-down figure less the itemized assets mapped to
   it; the residual is the concealed remainder (clamped at zero, the clamp reported).
7. :func:`concealed_rows`: one row per residual (and the small systems with code-derived
   quantities), each with quantity, unit, formula, inputs, assumptions and source.
8. :func:`depreciation`: the appraiser's physical depreciation rate (else age-life), applied
   to every row by :func:`depreciate`.
9. :func:`reconcile`: RCNLD against the purchase price less land, with the gap, the
   by-component picture and the guide's list of causes; within tolerance the allocation is
   pro rata on RCNLD, outside it is withheld.

Every transcribed number names its page (STRUCTURE_MODEL.md section 3.3 and 4); the model
table is pinned by ``tests/unit/domain/test_structure.py`` (the ten components sum to the
page's bottom line).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from typing import Final, Literal

from basis.domain.cost_indices import YearFactor, YearIndex, year_ratio
from basis.domain.costs import year_factor_index
from basis.domain.measurements import (
    BuildingMeasurements,
    FieldRow,
    Reading,
    TextReading,
)
from basis.domain.rules import canonical_key, normalize_label

CENT: Final = Decimal("0.01")
ZERO: Final = Decimal(0)
ONE: Final = Decimal(1)
HUNDRED: Final = Decimal(100)
PRECISION: Final = 34
RSMEANS: Final = "RSMeans Residential Costs 2020"
RSMEANS_BASE_YEAR: Final = 2020

# ----------------------------------------------------------------- assumptions (section 5)

# RSMeans Residential 2020, square-foot section instructions, printed p.15: "At this level of
# estimating, a variation of +/- 10% should be expected."
RECONCILIATION_TOLERANCE: Final = Decimal("0.10")
# Assemblies 6-04 "Drywall & Thincoat Wall Systems", printed p.214: per SF of wall, gypsum
# wallboard 0.84 + taped and finished joints 0.54 + corners 0.09 = 1.47 against painting,
# primer and two coats, 0.75.
DRYWALL_PER_SF: Final = Decimal("1.47")
PAINT_PER_SF: Final = Decimal("0.75")
PAINTING_SHARE_OF_INTERIORS_RESIDUAL: Final = PAINT_PER_SF / (DRYWALL_PER_SF + PAINT_PER_SF)
# Assemblies 8-12 "Three Fixture Bathroom Systems", printed p.250: bathroom installed with
# vanity 5,041.55 per bathroom; 8-60 "Gas Heating/Cooling Systems", printed p.268: heating
# only, gas fired hot air, one zone, 1,200 SF building, 5,929.90.
BATHROOM_SYSTEM: Final = Decimal("5041.55")
HEATING_SYSTEM: Final = Decimal("5929.90")
PLUMBING_SHARE_OF_MECHANICAL_RESIDUAL: Final = BATHROOM_SYSTEM / (BATHROOM_SYSTEM + HEATING_SYSTEM)
# A utility lateral from the property line (no site plan): an assumption, sent to review.
LATERAL_LENGTH_FT: Final = Decimal(50)
# Front and rear doors when no photograph shows an exterior door.
EXTERIOR_DOORS_DEFAULT: Final = 2
# The printed total of the cost approach against the sum of its parts (a warning).
COST_APPROACH_PARTS_TOLERANCE: Final = Decimal("0.01")

Class = Literal["economy", "average", "custom", "luxury"]
BuildingType = Literal["1 story", "1-1/2 story", "2 story", "2-1/2 story", "3 story"]
CLASSES: Final[tuple[Class, ...]] = ("economy", "average", "custom", "luxury")
TYPES: Final[tuple[BuildingType, ...]] = (
    "1 story",
    "1-1/2 story",
    "2 story",
    "2-1/2 story",
    "3 story",
)
DEFAULT_CLASS: Final[Class] = "average"
DEFAULT_TYPE: Final[BuildingType] = "1 story"

Component = Literal[
    "site_work",
    "foundation",
    "framing",
    "exterior_walls",
    "roofing",
    "interiors",
    "specialties",
    "mechanical",
    "electrical",
]
COMPONENTS: Final[tuple[Component, ...]] = (
    "site_work",
    "foundation",
    "framing",
    "exterior_walls",
    "roofing",
    "interiors",
    "specialties",
    "mechanical",
    "electrical",
)
COMPONENT_NAMES: Final[dict[str, str]] = {
    "site_work": "1 Site Work",
    "foundation": "2 Foundation",
    "framing": "3 Framing",
    "exterior_walls": "4 Exterior Walls",
    "roofing": "5 Roofing",
    "interiors": "6 Interiors",
    "specialties": "7 Specialties",
    "mechanical": "8 Mechanical",
    "electrical": "9 Electrical",
}


# The sweep's variants (STRUCTURE_MODEL.md section 7, EVAL_REPORT 44.3): where the building's
# RCN comes from, how it is split, whether the itemized rows are carved out, and how it is
# depreciated. The defaults are the production choice.
RcnSource = Literal["appraisal_total", "appraisal_parts", "square_foot"]
RCN_SOURCES: Final[tuple[RcnSource, ...]] = ("appraisal_total", "appraisal_parts", "square_foot")
SplitMethod = Literal["rsmeans", "nahb", "marshall_swift"]
SPLIT_METHODS: Final[tuple[SplitMethod, ...]] = ("rsmeans", "nahb", "marshall_swift")
DepreciationMethod = Literal["appraisal_physical", "age_life", "none"]
DEPRECIATION_METHODS: Final[tuple[DepreciationMethod, ...]] = (
    "appraisal_physical",
    "age_life",
    "none",
)
# The sweep's pick (EVAL_REPORT 44.3): the square-foot model scored the engineer's bucket
# shares closer on both live exports than the appraisal's figure, which reconciled tighter;
# STRUCTURE_RCN_SOURCE switches to the appraisal's printed total or its parts.
DEFAULT_RCN_SOURCE: Final[RcnSource] = "square_foot"
DEFAULT_SPLIT: Final[SplitMethod] = "rsmeans"
DEFAULT_DEPRECIATION: Final[DepreciationMethod] = "appraisal_physical"

# NAHB "Cost of Constructing a Home in 2024" (Eye on Housing, January 2025): the eight
# construction stages as shares of the construction cost. Site work (permits, impact and
# utility fees, architecture and engineering) and "other" are indirect, spread pro rata like
# the RSMeans Overhead; final steps (landscaping, driveway, clean-up) are outside the
# building. Within a stage the RSMeans model's proportions apportion the components.
NAHB_2024_STAGES: Final[dict[str, Decimal]] = {
    "site_work": Decimal("7.6"),
    "foundations": Decimal("10.5"),
    "framing": Decimal("16.6"),
    "exterior_finishes": Decimal("13.4"),
    "major_systems": Decimal("19.2"),
    "interior_finishes": Decimal("24.1"),
    "final_steps": Decimal("6.5"),
    "other": Decimal("2.1"),
}
NAHB_STAGE_COMPONENTS: Final[dict[str, tuple[str, ...]]] = {
    "foundations": ("site_work", "foundation"),
    "framing": ("framing",),
    "exterior_finishes": ("exterior_walls", "roofing"),
    "major_systems": ("mechanical", "electrical"),
    "interior_finishes": ("interiors", "specialties"),
}
NAHB_SOURCE: Final = (
    "NAHB Cost of Constructing a Home in 2024 (Eye on Housing, 2025-01): stage shares of the "
    "construction cost; site work and other spread pro rata, final steps outside the building; "
    "the RSMeans model's proportions within a stage"
)
MARSHALL_SWIFT_UNAVAILABLE: Final = (
    "Marshall & Swift Residential Cost Handbook segregated-cost percentages: the handbook is "
    "licensed and not in the corpus; no percentage was transcribed, so this split is not "
    "available (reported, never defaulted)"
)


class StructureError(ValueError):
    """An input the model refuses; the message says why."""


# ----------------------------------------------------------------- the square-foot models


@dataclass(frozen=True, slots=True)
class Model:
    """One RSMeans residential square-foot model page: dollars per square foot of living area
    per component (material plus installation) and the page's bottom line; the facing page's
    basement additions at the model's living area and its attached-garage modification."""

    cls: Class
    type: BuildingType
    pdf_page: int
    printed_page: int
    living_area_sf: int
    per_sf: dict[str, Decimal]  # the nine components
    overhead: Decimal
    total: Decimal
    finished_basement_add: Decimal  # per SF of living area
    unfinished_basement_add: Decimal
    garage_modification: Decimal  # the facing page's attached garage, dollars
    garage_cars: int  # cars in that modification

    @property
    def name(self) -> str:
        return f"{self.cls.capitalize()} {self.type.replace(' story', ' Story')}"

    @property
    def base(self) -> Decimal:
        """The nine components' sum (the total less overhead)."""
        return self.total - self.overhead

    def cite(self) -> str:
        return (
            f"{RSMEANS}, {self.name} model, printed p.{self.printed_page} (PDF p.{self.pdf_page})"
        )


def _m(
    cls: Class,
    type_: BuildingType,
    pdf: int,
    printed: int,
    living: int,
    per_sf: tuple[str, ...],
    total: str,
    basement: tuple[str, str],
    garage: tuple[str, int],
) -> Model:
    values = [Decimal(v) for v in per_sf]
    return Model(
        cls=cls,
        type=type_,
        pdf_page=pdf,
        printed_page=printed,
        living_area_sf=living,
        per_sf=dict(zip(COMPONENTS, values[:9], strict=True)),
        overhead=values[9],
        total=Decimal(total),
        finished_basement_add=Decimal(basement[0]),
        unfinished_basement_add=Decimal(basement[1]),
        garage_modification=Decimal(garage[0]),
        garage_cars=garage[1],
    )


_ECON_GARAGE: Final = ("14543", 1)  # One Car Attached Garage
_AVG_GARAGE: Final = ("15712", 1)
_CUSTOM_GARAGE: Final = ("30463", 2)  # Two Car Attached Garage
_LUX_GARAGE: Final = ("35170", 2)

# Site work, foundation, framing, exterior walls, roofing, interiors, specialties,
# mechanical, electrical, overhead; then the bottom line (STRUCTURE_MODEL.md section 3.3).
MODELS: Final[dict[tuple[Class, BuildingType], Model]] = {
    (m.cls, m.type): m
    for m in (
        _m("economy", "1 story", 39, 25, 1200,
           ("2.31", "15.31", "14.02", "16.39", "5.02", "26.02", "4.89", "8.68", "3.24", "14.37"),
           "110.25", ("29.90", "11.35"), _ECON_GARAGE),
        _m("economy", "1-1/2 story", 41, 27, 1600,
           ("1.73", "10.34", "17.67", "22.76", "3.64", "28.67", "3.66", "7.39", "3.01", "14.83"),
           "113.70", ("19.50", "7.75"), _ECON_GARAGE),
        _m("economy", "2 story", 43, 29, 2000,
           ("1.39", "8.28", "15.86", "22.48", "2.52", "28.27", "2.94", "6.60", "2.87", "13.69"),
           "104.90", ("15.60", "6.20"), _ECON_GARAGE),
        _m("average", "1 story", 53, 39, 1600,
           ("1.78", "14.05", "17.64", "19.62", "5.16", "27.60", "6.57", "7.93", "4.26", "17.79"),
           "122.40", ("34.85", "12.30"), _AVG_GARAGE),
        _m("average", "1-1/2 story", 55, 41, 1800,
           ("1.58", "10.20", "19.24", "21.60", "3.75", "31.93", "5.83", "7.40", "4.12", "17.95"),
           "123.60", ("25.40", "8.95"), _AVG_GARAGE),
        _m("average", "2 story", 57, 43, 2000,
           ("1.43", "8.51", "18.19", "22.53", "2.59", "31.94", "5.27", "7.03", "4.02", "17.24"),
           "118.75", ("21.65", "7.50"), _AVG_GARAGE),
        _m("average", "2-1/2 story", 59, 45, 3200,
           ("0.89", "6.06", "18.01", "19.32", "2.29", "31.19", "3.27", "5.71", "3.64", "15.37"),
           "105.75", ("16.95", "5.45"), _AVG_GARAGE),
        _m("average", "3 story", 61, 47, 3000,
           ("0.96", "5.66", "17.81", "21.49", "1.72", "32.39", "3.51", "5.86", "3.69", "15.81"),
           "108.90", ("15.90", "5.10"), _AVG_GARAGE),
        _m("custom", "1 story", 75, 61, 2400,
           ("1.33", "15.31", "16.88", "16.37", "9.47", "30.11", "10.51", "11.27", "5.33", "23.32"),
           "139.90", ("52.20", "20.55"), _CUSTOM_GARAGE),
        _m("custom", "1-1/2 story", 77, 63, 2800,
           ("1.14", "10.85", "18.07", "16.07", "6.86", "33.61", "8.97", "9.96", "5.20", "22.17"),
           "132.90", ("34.55", "13.85"), _CUSTOM_GARAGE),
        _m("custom", "2 story", 79, 65, 2800,
           ("1.14", "9.24", "17.11", "17.42", "4.75", "34.51", "8.97", "10.19", "5.20", "21.72"),
           "130.25", ("28.60", "11.55"), _CUSTOM_GARAGE),
        _m("custom", "2-1/2 story", 81, 67, 3200,
           ("0.99", "7.66", "18.32", "18.05", "4.22", "36.62", "7.85", "9.40", "5.12", "21.67"),
           "129.90", ("22.80", "9.25"), _CUSTOM_GARAGE),
        _m("custom", "3 story", 83, 69, 3000,
           ("1.07", "7.20", "18.11", "20.10", "3.16", "37.95", "8.40", "9.78", "5.16", "22.22"),
           "133.15", ("20.70", "8.55"), _CUSTOM_GARAGE),
        _m("luxury", "1 story", 93, 79, 2800,
           ("1.23", "16.96", "17.69", "17.08", "10.24", "29.87", "11.88", "11.34", "6.73", "29.53"),
           "152.55", ("55.40", "20.40"), _LUX_GARAGE),
        _m("luxury", "1-1/2 story", 95, 81, 2800,
           ("1.23", "12.55", "19.79", "18.30", "7.42", "34.94", "11.88", "11.34", "6.73", "29.82"),
           "154.00", ("38.00", "14.45"), _LUX_GARAGE),
        _m("luxury", "2 story", 97, 83, 3200,
           ("1.07", "10.20", "18.50", "18.55", "5.14", "34.82", "10.37", "10.44", "6.65", "27.76"),
           "143.50", ("30.70", "11.70"), _LUX_GARAGE),
        _m("luxury", "2-1/2 story", 99, 85, 3000,
           ("1.15", "9.07", "20.43", "21.56", "4.57", "39.34", "11.07", "10.88", "6.69", "29.94"),
           "154.70", ("25.80", "10.15"), _LUX_GARAGE),
        _m("luxury", "3 story", 101, 87, 3000,
           ("1.15", "8.22", "20.00", "22.99", "3.41", "39.60", "11.07", "10.88", "6.69", "29.74"),
           "153.75", ("20.70", "9.10"), _LUX_GARAGE),
    )
}  # fmt: skip

# "Base cost per square foot of living area", the Wood Siding - Wood Frame row of each
# model's facing page (STRUCTURE_MODEL.md section 3.3; printed pages 24-86, even), by living
# area. The cell at the model's representative area equals the model page's bottom line for
# every wood-siding model (the Economy 1 Story model is the stucco row: 110.25 at 1,200 SF
# against 117.85 for wood siding), pinned by a test.
_MATRIX_CELLS: Final[dict[tuple[Class, BuildingType], tuple[tuple[int, str], ...]]] = {
    ("economy", "1 story"): (
        (600, "153.40"),
        (800, "138.35"),
        (1000, "126.95"),
        (1200, "117.85"),
        (1400, "109.90"),
        (1600, "104.90"),
        (1800, "102.25"),
        (2000, "98.85"),
        (2400, "92.10"),
        (2800, "87.20"),
        (3200, "83.80"),
    ),
    ("economy", "1-1/2 story"): (
        (600, "180.80"),
        (800, "149.80"),
        (1000, "134.55"),
        (1200, "127.10"),
        (1400, "121.85"),
        (1600, "113.70"),
        (1800, "109.70"),
        (2000, "105.70"),
        (2400, "97.15"),
        (2800, "93.90"),
        (3200, "90.30"),
    ),
    ("economy", "2 story"): (
        (1000, "137.55"),
        (1200, "124.50"),
        (1400, "118.30"),
        (1600, "114.00"),
        (1800, "109.65"),
        (2000, "104.90"),
        (2200, "101.65"),
        (2600, "95.60"),
        (3000, "89.70"),
        (3400, "87.05"),
        (3800, "84.60"),
    ),
    ("average", "1 story"): (
        (600, "180.60"),
        (800, "161.95"),
        (1000, "148.30"),
        (1200, "137.35"),
        (1400, "128.30"),
        (1600, "122.40"),
        (1800, "119.00"),
        (2000, "115.10"),
        (2400, "107.35"),
        (2800, "101.70"),
        (3200, "97.75"),
    ),
    ("average", "1-1/2 story"): (
        (600, "206.15"),
        (800, "170.75"),
        (1000, "152.85"),
        (1200, "143.80"),
        (1400, "137.60"),
        (1600, "128.30"),
        (1800, "123.60"),
        (2000, "118.90"),
        (2400, "109.30"),
        (2800, "105.55"),
        (3200, "101.35"),
    ),
    ("average", "2 story"): (
        (1000, "156.90"),
        (1200, "141.70"),
        (1400, "134.40"),
        (1600, "129.30"),
        (1800, "124.15"),
        (2000, "118.75"),
        (2200, "115.10"),
        (2600, "108.15"),
        (3000, "101.60"),
        (3400, "98.40"),
        (3800, "95.65"),
    ),
    ("average", "2-1/2 story"): (
        (1200, "156.50"),
        (1400, "146.50"),
        (1600, "133.55"),
        (1800, "130.95"),
        (2000, "126.05"),
        (2400, "118.15"),
        (2800, "112.00"),
        (3200, "105.75"),
        (3600, "102.60"),
        (4000, "97.00"),
        (4400, "95.15"),
    ),
    ("average", "3 story"): (
        (1500, "142.15"),
        (1800, "128.65"),
        (2100, "122.60"),
        (2500, "117.75"),
        (3000, "108.90"),
        (3500, "104.90"),
        (4000, "99.45"),
        (4500, "93.75"),
        (5000, "91.85"),
        (5500, "89.65"),
        (6000, "87.35"),
    ),
    ("custom", "1 story"): (
        (800, "224.60"),
        (1000, "202.70"),
        (1200, "185.50"),
        (1400, "171.85"),
        (1600, "162.55"),
        (1800, "157.15"),
        (2000, "151.05"),
        (2400, "139.90"),
        (2800, "131.75"),
        (3200, "126.00"),
        (3600, "120.15"),
    ),
    ("custom", "1-1/2 story"): (
        (1000, "202.70"),
        (1200, "188.15"),
        (1400, "177.80"),
        (1600, "165.65"),
        (1800, "158.55"),
        (2000, "151.65"),
        (2400, "138.85"),
        (2800, "132.90"),
        (3200, "127.35"),
        (3600, "123.20"),
        (4000, "117.50"),
    ),
    ("custom", "2 story"): (
        (1200, "184.90"),
        (1400, "173.50"),
        (1600, "165.30"),
        (1800, "158.05"),
        (2000, "150.45"),
        (2400, "139.55"),
        (2800, "130.25"),
        (3200, "124.15"),
        (3600, "120.55"),
        (4000, "116.65"),
        (4400, "113.55"),
    ),
    ("custom", "2-1/2 story"): (
        (1500, "181.90"),
        (1800, "163.75"),
        (2100, "152.90"),
        (2400, "146.35"),
        (2800, "137.85"),
        (3200, "129.90"),
        (3600, "125.50"),
        (4000, "118.60"),
        (4500, "114.90"),
        (5000, "111.60"),
        (5500, "108.25"),
    ),
    ("custom", "3 story"): (
        (1500, "178.55"),
        (1800, "160.65"),
        (2100, "151.85"),
        (2500, "144.35"),
        (3000, "133.15"),
        (3500, "127.40"),
        (4000, "120.55"),
        (4500, "113.50"),
        (5000, "110.80"),
        (5500, "108.10"),
        (6000, "105.25"),
    ),
    ("luxury", "1 story"): (
        (1000, "237.95"),
        (1200, "217.25"),
        (1400, "200.75"),
        (1600, "189.50"),
        (1800, "182.95"),
        (2000, "175.50"),
        (2400, "162.15"),
        (2800, "152.55"),
        (3200, "145.60"),
        (3600, "138.75"),
        (4000, "133.10"),
    ),
    ("luxury", "1-1/2 story"): (
        (1000, "238.80"),
        (1200, "220.75"),
        (1400, "207.95"),
        (1600, "193.55"),
        (1800, "184.85"),
        (2000, "176.55"),
        (2400, "161.20"),
        (2800, "154.00"),
        (3200, "147.30"),
        (3600, "142.35"),
        (4000, "135.60"),
    ),
    ("luxury", "2 story"): (
        (1200, "216.50"),
        (1400, "202.60"),
        (1600, "192.60"),
        (1800, "183.90"),
        (2000, "174.80"),
        (2400, "161.70"),
        (2800, "150.75"),
        (3200, "143.50"),
        (3600, "139.15"),
        (4000, "134.55"),
        (4400, "130.75"),
    ),
    ("luxury", "2-1/2 story"): (
        (1500, "211.75"),
        (1800, "190.35"),
        (2100, "177.25"),
        (2500, "167.60"),
        (3000, "154.70"),
        (3500, "145.40"),
        (4000, "136.65"),
        (4500, "132.15"),
        (5000, "128.20"),
        (5500, "124.40"),
        (6000, "120.30"),
    ),
    ("luxury", "3 story"): (
        (1500, "207.95"),
        (1800, "186.80"),
        (2100, "176.15"),
        (2500, "167.00"),
        (3000, "153.75"),
        (3500, "146.90"),
        (4000, "138.85"),
        (4500, "130.65"),
        (5000, "127.45"),
        (5500, "124.25"),
        (6000, "120.85"),
    ),
}
BASE_COST_BY_AREA: Final[dict[tuple[Class, BuildingType], tuple[tuple[int, Decimal], ...]]] = {
    key: tuple((area, Decimal(cost)) for area, cost in cells)
    for key, cells in _MATRIX_CELLS.items()
}


def base_cost_per_sf(model: Model, living_area: Decimal) -> tuple[Decimal, str, tuple[str, ...]]:
    """The wood-siding base cost per square foot of living area at ``living_area``, read from
    the facing page's matrix with linear interpolation between its columns and clamped at
    the ends (an assumption when clamped). Returns ``(cost, formula, assumptions)``."""
    cells = BASE_COST_BY_AREA[(model.cls, model.type)]
    low_area, low_cost = cells[0]
    high_area, high_cost = cells[-1]
    if living_area <= low_area:
        return (
            low_cost,
            f"{_fmt(low_cost)}/SF at {low_area} SF (the matrix's smallest column)",
            (
                f"living area {_fmt(living_area)} SF is below the matrix's {low_area} SF "
                "column: its cost used",
            ),
        )
    if living_area >= high_area:
        return (
            high_cost,
            f"{_fmt(high_cost)}/SF at {high_area} SF (the matrix's largest column)",
            (
                f"living area {_fmt(living_area)} SF is above the matrix's {high_area} SF "
                "column: its cost used",
            ),
        )
    for (a0, c0), (a1, c1) in zip(cells, cells[1:], strict=False):
        if a0 <= living_area <= a1:
            if living_area == a0:
                return c0, f"{_fmt(c0)}/SF at {a0} SF", ()
            if living_area == a1:
                return c1, f"{_fmt(c1)}/SF at {a1} SF", ()
            with localcontext() as ctx:
                ctx.prec = PRECISION
                cost = (c0 + (c1 - c0) * (living_area - a0) / (a1 - a0)).quantize(
                    CENT, rounding=ROUND_HALF_UP
                )
            return (
                cost,
                f"{_fmt(cost)}/SF interpolated between {a0} SF ({_fmt(c0)}) and {a1} SF "
                f"({_fmt(c1)})",
                (),
            )
    raise StructureError("unreachable: the matrix is sorted by area")


# UAD quality ratings (Fannie Mae, Q1 best) against the RSMeans class descriptions
# (STRUCTURE_MODEL.md section 3.2).
UAD_QUALITY_CLASS: Final[dict[str, Class]] = {
    "Q1": "luxury",
    "Q2": "luxury",
    "Q3": "custom",
    "Q4": "average",
    "Q5": "economy",
    "Q6": "economy",
}
# Marshall & Swift quality words as a cost service prints them.
COST_SERVICE_CLASS: Final[dict[str, Class]] = {
    "excellent": "luxury",
    "very good": "custom",
    "good": "custom",
    "average": "average",
    "fair": "economy",
    "low": "economy",
}


# ----------------------------------------------------------------- the appraisal's cost approach

COST_APPROACH_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "total_cost_new": ("cost_approach.total_cost_new",),
    "dwelling_cost": ("cost_approach.dwelling_cost",),
    "dwelling_sqft": ("cost_approach.dwelling_sqft", "improvements.gla_sqft"),
    "dwelling_cost_per_sqft": ("cost_approach.dwelling_cost_per_sqft",),
    "basement_cost": ("cost_approach.basement_cost",),
    "basement_sqft": ("cost_approach.basement_sqft", "improvements.basement_area_sqft"),
    "basement_cost_per_sqft": ("cost_approach.basement_cost_per_sqft",),
    "garage_cost": ("cost_approach.garage_cost",),
    "garage_sqft": ("cost_approach.garage_sqft",),
    "garage_cost_per_sqft": ("cost_approach.garage_cost_per_sqft",),
    "mechanicals_misc": ("cost_approach.mechanicals_misc",),
    "depreciation_physical": ("cost_approach.depreciation_physical",),
    "depreciation_total": ("cost_approach.depreciation",),
    "depreciated_cost": ("cost_approach.depreciated_cost_improvements",),
    "effective_age": ("improvements.effective_age_years",),
    "remaining_economic_life": ("cost_approach.remaining_economic_life",),
    "site_improvements_value": ("cost_approach.site_improvements_value",),
    "site_value": ("cost_approach.site_value",),
    "bedrooms": ("improvements.bedrooms",),
    "total_rooms": ("improvements.total_rooms",),
}
COST_APPROACH_TEXT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "quality_uad": ("improvements.quality",),
    "quality_service": ("cost_approach.quality_rating_from_cost_service",),
    "cost_data_date": ("cost_approach.effective_date_of_cost_data",),
}
UTILITY_KEYS: Final[dict[str, str]] = {
    "water": "site.utilities_water",
    "sewer": "site.utilities_sewer",
    "gas": "site.utilities_gas",
}
# A lateral exists for a service the appraisal marks public (ATG p.319: water, sanitary sewer
# and gas services; the electric service drop is the utility's).
_PUBLIC: Final = re.compile(r"\bpublic\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CostApproach:
    """The appraisal's cost approach and the fields the model reads beside it; every member
    ``None`` when the field was not read."""

    total_cost_new: Reading | None = None
    dwelling_cost: Reading | None = None
    dwelling_sqft: Reading | None = None
    dwelling_cost_per_sqft: Reading | None = None
    basement_cost: Reading | None = None
    basement_sqft: Reading | None = None
    basement_cost_per_sqft: Reading | None = None
    garage_cost: Reading | None = None
    garage_sqft: Reading | None = None
    garage_cost_per_sqft: Reading | None = None
    mechanicals_misc: Reading | None = None
    depreciation_physical: Reading | None = None
    depreciation_total: Reading | None = None
    depreciated_cost: Reading | None = None
    effective_age: Reading | None = None
    remaining_economic_life: Reading | None = None
    site_improvements_value: Reading | None = None
    site_value: Reading | None = None
    bedrooms: Reading | None = None
    total_rooms: Reading | None = None
    quality_uad: TextReading | None = None
    quality_service: TextReading | None = None
    cost_data_date: TextReading | None = None
    # Public utility services the appraisal lists (water, sewer, gas), in that order.
    public_utilities: tuple[str, ...] = ()

    @property
    def has_cost_new(self) -> bool:
        return (
            self.total_cost_new is not None
            or self.dwelling_cost is not None
            or (self.dwelling_sqft is not None and self.dwelling_cost_per_sqft is not None)
        )


def cost_approach_from_fields(rows: Iterable[FieldRow]) -> CostApproach:
    """The cost approach in the stored extraction fields: a numeric field when finite and
    non-negative (counts at least 1), the first key of each entry that has one winning."""
    by_key: dict[str, FieldRow] = {}
    for row in rows:
        by_key.setdefault(f"{row.section}.{row.field}", row)

    def number(name: str) -> Reading | None:
        for key in COST_APPROACH_KEYS[name]:
            row = by_key.get(key)
            if row is None or row.value_num is None or isinstance(row.value_num, bool):
                continue
            value = Decimal(str(row.value_num))
            if not value.is_finite() or value < 0:
                continue
            if name in ("bedrooms", "total_rooms") and (
                value < 1 or value != value.to_integral_value()
            ):
                continue
            return Reading(value, key, row.source)
        return None

    def text(name: str) -> TextReading | None:
        for key in COST_APPROACH_TEXT_KEYS[name]:
            row = by_key.get(key)
            if row is not None and row.value_text and row.value_text.strip():
                return TextReading(row.value_text.strip(), key, row.source)
        return None

    public = tuple(
        service
        for service, key in UTILITY_KEYS.items()
        if (found := by_key.get(key)) is not None
        and found.value_text is not None
        and _PUBLIC.search(found.value_text)
    )
    numbers = {name: number(name) for name in COST_APPROACH_KEYS}
    texts = {name: text(name) for name in COST_APPROACH_TEXT_KEYS}
    return CostApproach(**numbers, **texts, public_utilities=public)  # type: ignore[arg-type]


# ----------------------------------------------------------------- model selection


@dataclass(frozen=True, slots=True)
class ModelChoice:
    model: Model
    class_source: str
    type_source: str
    assumptions: tuple[str, ...]
    needs_review: bool


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral_value() else f"{value:.2f}"


def class_of(cost_approach: CostApproach | None) -> tuple[Class, str, bool]:
    """``(class, source, defaulted)`` from the UAD quality rating, else the cost service's
    words, else the default class."""
    if cost_approach is not None and cost_approach.quality_uad is not None:
        code = cost_approach.quality_uad.value.upper().split()[0]
        if code in UAD_QUALITY_CLASS:
            return (
                UAD_QUALITY_CLASS[code],
                f"UAD quality {code} ({cost_approach.quality_uad.describe()})",
                False,
            )
    if cost_approach is not None and cost_approach.quality_service is not None:
        words = cost_approach.quality_service.value.lower()
        for name, cls in sorted(COST_SERVICE_CLASS.items(), key=lambda kv: -len(kv[0])):
            if name in words:
                return (
                    cls,
                    f"cost service quality {cost_approach.quality_service.value!r} "
                    f"({cost_approach.quality_service.describe()})",
                    False,
                )
    return DEFAULT_CLASS, f"assumed {DEFAULT_CLASS}: no quality rating read", True


def type_of(measurements: BuildingMeasurements | None) -> tuple[BuildingType, str, bool]:
    """``(type, source, defaulted)`` from the stories."""
    if measurements is None or measurements.stories is None:
        return DEFAULT_TYPE, f"assumed {DEFAULT_TYPE}: stories not printed", True
    stories = measurements.stories.value
    if stories < Decimal("1.5"):
        chosen: BuildingType = "1 story"
    elif stories < 2:
        chosen = "1-1/2 story"
    elif stories < Decimal("2.5"):
        chosen = "2 story"
    elif stories < 3:
        chosen = "2-1/2 story"
    else:
        chosen = "3 story"
    return chosen, f"{_fmt(stories)} stories ({measurements.stories.describe()})", False


def select_model(
    measurements: BuildingMeasurements | None, cost_approach: CostApproach | None
) -> ModelChoice:
    cls, class_source, class_defaulted = class_of(cost_approach)
    type_, type_source, type_defaulted = type_of(measurements)
    assumptions: list[str] = []
    if class_defaulted:
        assumptions.append(class_source)
    if type_defaulted:
        assumptions.append(type_source)
    model = MODELS[(cls, type_)]
    return ModelChoice(
        model, class_source, type_source, tuple(assumptions), class_defaulted or type_defaulted
    )


# ----------------------------------------------------------------- the building RCN


@dataclass(frozen=True, slots=True)
class Check:
    code: str
    severity: Literal["ok", "warning", "error", "not_checked"]
    message: str


@dataclass(frozen=True, slots=True)
class BuildingRcn:
    """The building's replacement cost new at the cost year, and how it was arrived at."""

    amount: Decimal
    source: Literal["appraisal_cost_approach", "square_foot_model"]
    formula: str
    inputs: dict[str, str]
    assumptions: tuple[str, ...]
    checks: tuple[Check, ...]
    needs_review: bool
    model: Model
    model_choice: ModelChoice
    # The appraisal's own figures when printed, for the reconciliation's independent check.
    appraisal_depreciated_cost: Decimal | None = None
    appraisal_site_improvements: Decimal | None = None
    appraisal_site_value: Decimal | None = None


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _year_of(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(19|20)\d{2}", text)
    return int(match.group(0)) if match else None


def _appraisal_rcn(
    cost_approach: CostApproach,
    cost_year: int,
    year_source: str | None,
    prefer_parts: bool = False,
    supplement: Mapping[int, YearIndex] | None = None,
) -> tuple[Decimal, str, dict[str, str], list[str], list[Check]]:
    inputs: dict[str, str] = {}
    assumptions: list[str] = []
    checks: list[Check] = []
    parts: list[tuple[str, Decimal]] = []
    dwelling = cost_approach.dwelling_cost
    if dwelling is not None:
        parts.append(("dwelling", dwelling.value))
        inputs["dwelling_cost"] = _fmt(dwelling.value)
        inputs["dwelling_cost_field"] = dwelling.describe()
    elif (
        cost_approach.dwelling_sqft is not None and cost_approach.dwelling_cost_per_sqft is not None
    ):
        area, rate = cost_approach.dwelling_sqft.value, cost_approach.dwelling_cost_per_sqft.value
        parts.append(("dwelling", money(area * rate)))
        inputs["dwelling_sqft"] = _fmt(area)
        inputs["dwelling_cost_per_sqft"] = _fmt(rate)
        inputs["dwelling_cost_field"] = (
            f"{cost_approach.dwelling_sqft.describe()} x "
            f"{cost_approach.dwelling_cost_per_sqft.describe()}"
        )
    part_cost: Reading | None
    part_area: Reading | None
    part_rate: Reading | None
    for name, part_cost, part_area, part_rate in (
        (
            "basement",
            cost_approach.basement_cost,
            cost_approach.basement_sqft,
            cost_approach.basement_cost_per_sqft,
        ),
        (
            "garage",
            cost_approach.garage_cost,
            cost_approach.garage_sqft,
            cost_approach.garage_cost_per_sqft,
        ),
    ):
        if part_cost is not None:
            parts.append((name, part_cost.value))
            inputs[f"{name}_cost"] = _fmt(part_cost.value)
            inputs[f"{name}_cost_field"] = part_cost.describe()
        elif part_area is not None and part_rate is not None and dwelling is not None:
            parts.append((name, money(part_area.value * part_rate.value)))
            inputs[f"{name}_cost"] = _fmt(money(part_area.value * part_rate.value))
            inputs[f"{name}_cost_field"] = f"{part_area.describe()} x {part_rate.describe()}"
    if cost_approach.mechanicals_misc is not None:
        parts.append(("mechanicals_misc", cost_approach.mechanicals_misc.value))
        inputs["mechanicals_misc"] = _fmt(cost_approach.mechanicals_misc.value)
        inputs["mechanicals_misc_field"] = cost_approach.mechanicals_misc.describe()
    parts_sum = sum((v for _, v in parts), ZERO)
    printed = cost_approach.total_cost_new
    if printed is not None and prefer_parts and parts:
        # The parts over the printed total (the sweep's appraisal_parts variant).
        amount = parts_sum
        formula = "the cost approach's parts: " + " + ".join(f"{n} {_fmt(v)}" for n, v in parts)
        inputs["total_cost_new"] = _fmt(printed.value)
        inputs["total_cost_new_field"] = printed.describe()
        with localcontext() as ctx:
            ctx.prec = PRECISION
            diff = (parts_sum - printed.value) / printed.value if printed.value else ZERO
        checks.append(
            Check(
                "cost_approach_parts",
                "ok" if abs(diff) <= COST_APPROACH_PARTS_TOLERANCE else "warning",
                f"the parts {_fmt(parts_sum)} are used; the printed total {_fmt(printed.value)} "
                f"differs by {_pct(-diff, signed=True)}",
            )
        )
    elif printed is not None:
        amount = printed.value
        formula = f"total estimate of cost new {_fmt(amount)}"
        inputs["total_cost_new"] = _fmt(amount)
        inputs["total_cost_new_field"] = printed.describe()
        if parts:
            with localcontext() as ctx:
                ctx.prec = PRECISION
                diff = (parts_sum - amount) / amount if amount else ZERO
            names = " + ".join(f"{n} {_fmt(v)}" for n, v in parts)
            if abs(diff) <= COST_APPROACH_PARTS_TOLERANCE:
                checks.append(
                    Check(
                        "cost_approach_parts",
                        "ok",
                        f"the printed total {_fmt(amount)} agrees with its parts ({names}) "
                        f"within {_pct(COST_APPROACH_PARTS_TOLERANCE)}",
                    )
                )
            else:
                checks.append(
                    Check(
                        "cost_approach_parts",
                        "warning",
                        f"the appraisal's printed total cost new {_fmt(amount)} differs from "
                        f"the sum of its parts {_fmt(parts_sum)} ({names}) by "
                        f"{_pct(diff, signed=True)}; the printed total is used",
                    )
                )
        else:
            checks.append(
                Check(
                    "cost_approach_parts",
                    "not_checked",
                    "the cost approach prints no parts to check the total against",
                )
            )
    else:
        amount = parts_sum
        formula = " + ".join(f"{n} {_fmt(v)}" for n, v in parts)
        checks.append(
            Check(
                "cost_approach_parts",
                "not_checked",
                "the cost approach prints no total; the parts are summed",
            )
        )
    # From the cost data's year to the cost year, by the Historical Cost Index ratio (the
    # book's time adjustment, p.778), the same index every RSMeans row uses.
    data_year = _year_of(
        None if cost_approach.cost_data_date is None else cost_approach.cost_data_date.value
    )
    ratio: tuple[Decimal, str] | None = None
    if data_year is not None:
        try:
            ratio = year_ratio(cost_year, data_year, supplement)
        except (KeyError, ValueError):
            ratio = None
    if ratio is not None:
        factor, ratio_formula = ratio
        adjusted = money(amount * factor)
        inputs["cost_data_year"] = str(data_year)
        inputs["cost_year"] = str(cost_year)
        inputs["year_factor"] = f"{factor:.4f}"
        inputs["year_factor_formula"] = ratio_formula
        when = f" ({year_source})" if year_source else ""
        formula += f" x {factor:.4f} from the cost data's year {data_year} to {cost_year}{when}"
        amount = adjusted
    else:
        why = (
            "the cost approach prints no cost data date"
            if data_year is None
            else f"no published cost index for {data_year} or {cost_year}"
        )
        assumptions.append(
            f"the appraisal's cost new is taken as of the cost year {cost_year}: {why}"
        )
        inputs["cost_year"] = str(cost_year)
    assumptions.append(
        "the appraiser's cost new is a local cost-service figure: no location factor applied"
    )
    return amount, formula, inputs, assumptions, checks


def _square_foot_rcn(
    choice: ModelChoice,
    measurements: BuildingMeasurements,
    cost_year: int,
    year_source: str | None,
    location_factor: Decimal,
    location_source: str | None,
    year: YearFactor,
) -> tuple[Decimal, str, dict[str, str], list[str], list[Check]]:
    model = choice.model
    if measurements.gla_sf is None:
        raise StructureError("the square-foot model needs a living area (GLA)")
    gla = measurements.gla_sf.value
    per_sf, per_sf_formula, size_assumptions = base_cost_per_sf(model, gla)
    inputs: dict[str, str] = {
        "model": model.name,
        "model_source": model.cite(),
        "model_living_area_sf": str(model.living_area_sf),
        "base_cost_per_sf": _fmt(per_sf),
        "base_cost_formula": per_sf_formula,
        "base_cost_source": (
            f"{RSMEANS}, {model.name} facing page, printed p.{model.printed_page - 1}: base "
            "cost per square foot of living area, wood siding on wood frame"
        ),
        "gla_sf": _fmt(gla),
        "gla_field": measurements.gla_sf.describe(),
    }
    assumptions = [
        *choice.assumptions,
        *size_assumptions,
        "the exterior wall is wood siding on wood frame (the matrix's first row); the "
        "basement additions and the garage modification are the facing page's at the model's "
        "representative living area",
    ]
    with localcontext() as ctx:
        ctx.prec = PRECISION
        base = per_sf * gla
        terms = [f"{per_sf_formula} x {_fmt(gla)} SF living area"]
        if measurements.basement_sf is not None:
            stories = measurements.stories.value if measurements.stories is not None else ONE
            ground = gla / stories
            share = min(measurements.basement_sf.value / ground, ONE) if ground > 0 else ONE
            finished_pct = (
                measurements.basement_finish_pct.value / HUNDRED
                if measurements.basement_finish_pct is not None
                else ONE
            )
            add_per_sf = (
                model.finished_basement_add * finished_pct
                + model.unfinished_basement_add * (ONE - finished_pct)
            )
            basement = add_per_sf * gla * share
            base += basement
            terms.append(
                f"basement ({_fmt(model.finished_basement_add)} finished / "
                f"{_fmt(model.unfinished_basement_add)} unfinished per SF of living area, "
                f"{_pct(finished_pct)} finished, {_pct(share)} of the ground floor) "
                f"{_fmt(money(basement))}"
            )
            inputs["basement_sf"] = _fmt(measurements.basement_sf.value)
            inputs["basement_share_of_ground_floor"] = f"{share:.4f}"
            inputs["basement_finished_pct"] = _pct(finished_pct)
            if measurements.basement_finish_pct is None:
                assumptions.append("basement finish not printed: taken as fully finished")
        if measurements.garage_cars is not None:
            cars = measurements.garage_cars.value
            garage = model.garage_modification * cars / model.garage_cars
            base += garage
            terms.append(
                f"attached garage {_fmt(model.garage_modification)} per {model.garage_cars} "
                f"cars x {_fmt(cars)} cars = {_fmt(money(garage))}"
            )
            inputs["garage_cars"] = _fmt(cars)
        factor = year.factor
        amount = money(base * factor * location_factor)
    inputs["year_factor"] = str(factor)
    inputs["year_factor_formula"] = year.citation()
    inputs["location_factor"] = str(location_factor)
    when = f" from {year_source}" if year_source else ""
    where = f" from {location_source}" if location_source else ""
    formula = (
        f"({' + '.join(terms)}) x {factor} for {cost_year} ({year.citation()}){when} "
        f"x {location_factor} location{where}"
    )
    checks = [
        Check(
            "building_rcn_source",
            "warning",
            f"the building RCN is the RSMeans square-foot model {model.name} sized by the "
            "facing page's matrix; the engineer should confirm the class, type and exterior wall",
        )
    ]
    if year.assumption is not None:
        assumptions.append(year.assumption)
        checks.append(
            Check(
                "cost_year_index",
                "warning",
                f"no published cost index for {cost_year} is loaded; the square-foot model is "
                f"priced at the latest, {year.index.describe()}",
            )
        )
    return amount, formula, inputs, assumptions, checks


def building_rcn(
    measurements: BuildingMeasurements | None,
    cost_approach: CostApproach | None,
    *,
    cost_year: int,
    year_source: str | None = None,
    location_factor: Decimal = ONE,
    location_source: str | None = None,
    rcn_source: RcnSource = DEFAULT_RCN_SOURCE,
    year: YearFactor | None = None,
    supplement: Mapping[int, YearIndex] | None = None,
) -> BuildingRcn | None:
    """The building's RCN from the appraisal's cost approach (``appraisal_total``: the
    printed total, ``appraisal_parts``: the sum of its parts), else the square-foot model
    (``square_foot`` asks for it first), else ``None`` (no living area to size a model).
    ``year`` is the study's resolved time factor (``CostIndex.year``); without it the cost
    year's factor comes from the released book and ``supplement``."""
    choice = select_model(measurements, cost_approach)
    if year is None or year.cost_year != cost_year:
        year = year_factor_index(cost_year, supplement)
    use_appraisal = (
        cost_approach is not None
        and cost_approach.has_cost_new
        and (rcn_source != "square_foot" or measurements is None or measurements.gla_sf is None)
    )
    if use_appraisal:
        assert cost_approach is not None
        amount, formula, inputs, assumptions, checks = _appraisal_rcn(
            cost_approach,
            cost_year,
            year_source,
            prefer_parts=rcn_source == "appraisal_parts",
            supplement=supplement,
        )
        source: Literal["appraisal_cost_approach", "square_foot_model"] = "appraisal_cost_approach"
        needs_review = any(c.severity == "warning" for c in checks)
        assumptions = [*choice.assumptions, *assumptions]
        needs_review = needs_review or choice.needs_review
    elif measurements is not None and measurements.gla_sf is not None:
        amount, formula, inputs, assumptions, checks = _square_foot_rcn(
            choice, measurements, cost_year, year_source, location_factor, location_source, year
        )
        source = "square_foot_model"
        needs_review = True
    else:
        return None
    if amount <= 0:
        return None
    ca = cost_approach
    return BuildingRcn(
        amount=amount,
        source=source,
        formula=formula,
        inputs=inputs,
        assumptions=tuple(assumptions),
        checks=tuple(checks),
        needs_review=needs_review,
        model=choice.model,
        model_choice=choice,
        appraisal_depreciated_cost=None
        if ca is None or ca.depreciated_cost is None
        else ca.depreciated_cost.value,
        appraisal_site_improvements=(
            None
            if ca is None or ca.site_improvements_value is None
            else ca.site_improvements_value.value
        ),
        appraisal_site_value=None if ca is None or ca.site_value is None else ca.site_value.value,
    )


# ----------------------------------------------------------------- the split


def _largest_remainder(exact: Sequence[Decimal], total: Decimal) -> list[Decimal]:
    """Round ``exact`` to cents so they sum to ``total`` (half up, residual cents to the
    largest remainders, ties by order)."""
    floors = [x.quantize(CENT, rounding=ROUND_DOWN) for x in exact]
    residual = int(((total - sum(floors, ZERO)) / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    if residual < 0 or residual > len(exact):
        raise StructureError(
            f"cannot foot {len(exact)} parts to {total}: {residual} residual cents"
        )
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
    values = list(floors)
    for i in order[:residual]:
        values[i] = floors[i] + CENT
    return values


def component_shares(model: Model) -> dict[str, Decimal]:
    """Each component's share of the building RCN after Overhead is spread pro rata over the
    nine (ATG ch. 4 C.11: project-wide indirect costs pro rata), to 6 places."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return {
            c: (model.per_sf[c] / model.base).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            for c in COMPONENTS
        }


def _nahb_fractions(model: Model) -> dict[str, Decimal]:
    """The NAHB shares at full precision (``nahb_shares`` rounds them for display)."""
    building = sum((NAHB_2024_STAGES[s] for s in NAHB_STAGE_COMPONENTS), ZERO)
    shares: dict[str, Decimal] = {}
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for stage, components in NAHB_STAGE_COMPONENTS.items():
            stage_share = NAHB_2024_STAGES[stage] / building
            within = sum((model.per_sf[c] for c in components), ZERO)
            for c in components:
                shares[c] = stage_share * model.per_sf[c] / within
    return {c: shares[c] for c in COMPONENTS}


def nahb_shares(model: Model) -> dict[str, Decimal]:
    """Each component's share under the NAHB 2024 stage shares (:data:`NAHB_2024_STAGES`):
    the five building stages renormalized (site work and other spread pro rata, final steps
    outside the building), each stage apportioned to its components by the model's
    proportions, to 6 places."""
    return {
        c: v.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        for c, v in _nahb_fractions(model).items()
    }


def split_shares(model: Model, method: SplitMethod = DEFAULT_SPLIT) -> dict[str, Decimal]:
    """The components' shares under ``method``; ``StructureError`` for a split that has no
    source (Marshall & Swift)."""
    if method == "rsmeans":
        return component_shares(model)
    if method == "nahb":
        return nahb_shares(model)
    raise StructureError(MARSHALL_SWIFT_UNAVAILABLE)


def split_source(model: Model, method: SplitMethod = DEFAULT_SPLIT) -> str:
    if method == "rsmeans":
        return f"{model.cite()}: the ten components' dollars per SF, Overhead spread pro rata"
    if method == "nahb":
        return f"{NAHB_SOURCE} ({model.name} proportions within a stage)"
    return MARSHALL_SWIFT_UNAVAILABLE


def split_components(
    rcn: Decimal, model: Model, method: SplitMethod = DEFAULT_SPLIT
) -> dict[str, Decimal]:
    """The RCN over the nine components by the split's shares, footing to ``rcn`` to the
    cent (the RSMeans split uses the page's dollars at full precision)."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        if method == "rsmeans":
            exact = [rcn * model.per_sf[c] / model.base for c in COMPONENTS]
        elif method == "nahb":
            fractions = _nahb_fractions(model)
            exact = [rcn * fractions[c] for c in COMPONENTS]
        else:
            raise StructureError(MARSHALL_SWIFT_UNAVAILABLE)
    return dict(zip(COMPONENTS, _largest_remainder(exact, money(rcn)), strict=True))


# ----------------------------------------------------------------- systems and labels

# The design partner's twelve building systems (``basis.domain.basis.REPORT_ROW_SYSTEMS``).
MISCELLANEOUS: Final = "Miscellaneous Building Components"
SYSTEMS: Final = (
    "Ceiling Systems",
    "Doors and Frames",
    "Electrical",
    "Fire Protection & Alarm",
    "HVAC",
    "Interior Framing / Partitions",
    MISCELLANEOUS,
    "Painting",
    "Plumbing",
    "Roofing Systems",
    "Security",
    "Windows",
)

Structure = Literal[
    "foundation",
    "structural_framing",
    "exterior_wall_assembly",
    "roofing_assembly",
    "interior_partition",
    "painting",
    "plumbing_rough_in",
    "hvac_distribution",
    "electrical_wiring",
    "fire_alarm_system",
    "data_cabling",
    "access_control",
    "site_utilities",
]
STRUCTURE_LABELS: Final[tuple[str, ...]] = (
    "foundation",
    "structural_framing",
    "exterior_wall_assembly",
    "roofing_assembly",
    "interior_partition",
    "painting",
    "plumbing_rough_in",
    "hvac_distribution",
    "electrical_wiring",
    "fire_alarm_system",
    "data_cabling",
    "access_control",
    "site_utilities",
)


@dataclass(frozen=True, slots=True)
class LabelMapping:
    system: str
    component: str | None  # None: outside the square-foot model (additive, never carved)


def _lm(system: str, component: str | None) -> LabelMapping:
    return LabelMapping(system, component)


# Every vision label (basis.vision.schemas.ComponentLabel) and every structure label
# (STRUCTURE_MODEL.md section 3.4). ``tests/unit/domain/test_structure.py`` pins coverage.
LABEL_SYSTEMS: Final[dict[str, LabelMapping]] = {
    # interiors: floors, walls, ceilings, trim, interior doors, stairs
    "carpet": _lm(MISCELLANEOUS, "interiors"),
    "hardwood_floor": _lm(MISCELLANEOUS, "interiors"),
    "vinyl_flooring": _lm(MISCELLANEOUS, "interiors"),
    "laminate_flooring": _lm(MISCELLANEOUS, "interiors"),
    "tile": _lm(MISCELLANEOUS, "interiors"),
    "concrete_floor": _lm(MISCELLANEOUS, "foundation"),
    "wall_finish": _lm("Painting", "interiors"),
    "wainscoting": _lm(MISCELLANEOUS, "interiors"),
    "half_wall": _lm("Interior Framing / Partitions", "interiors"),
    "ceiling_finish": _lm("Ceiling Systems", "interiors"),
    "baseboard": _lm(MISCELLANEOUS, "interiors"),
    "crown_molding": _lm(MISCELLANEOUS, "interiors"),
    "interior_door": _lm("Doors and Frames", "interiors"),
    "stairs": _lm(MISCELLANEOUS, "interiors"),
    "railing": _lm(MISCELLANEOUS, "interiors"),
    "closet_shelving": _lm(MISCELLANEOUS, "interiors"),
    "built_in_shelving": _lm(MISCELLANEOUS, "interiors"),
    # exterior walls: windows, exterior and garage doors, siding
    "window": _lm("Windows", "exterior_walls"),
    "exterior_door": _lm("Doors and Frames", "exterior_walls"),
    "garage_door": _lm("Doors and Frames", "exterior_walls"),
    "siding": _lm(MISCELLANEOUS, "exterior_walls"),
    "window_guard": _lm(MISCELLANEOUS, None),
    # roofing
    "roof": _lm("Roofing Systems", "roofing"),
    "gutter": _lm(MISCELLANEOUS, "roofing"),
    # specialties: kitchen cabinets, counters, kitchen sink, water heater
    "kitchen_cabinet": _lm(MISCELLANEOUS, "specialties"),
    "countertop": _lm(MISCELLANEOUS, "specialties"),
    "sink": _lm("Plumbing", "specialties"),
    "water_heater": _lm("Plumbing", "specialties"),
    "garbage_disposal": _lm("Plumbing", None),
    # mechanical: the bathroom and the heating system
    "bathroom_vanity": _lm(MISCELLANEOUS, "mechanical"),
    "toilet": _lm("Plumbing", "mechanical"),
    "bathtub": _lm("Plumbing", "mechanical"),
    "shower": _lm("Plumbing", "mechanical"),
    "hvac_unit": _lm("HVAC", "mechanical"),
    "ac_condenser": _lm("HVAC", "mechanical"),
    "hvac_vent": _lm("HVAC", "mechanical"),
    "thermostat": _lm("HVAC", "mechanical"),
    "sprinkler_system": _lm("Fire Protection & Alarm", "mechanical"),
    "bathroom_accessory": _lm(MISCELLANEOUS, None),
    # electrical: service, wiring, devices, lighting, communications
    "light_fixture": _lm("Electrical", "electrical"),
    "ceiling_fan": _lm("Electrical", "electrical"),
    "outdoor_lighting": _lm("Electrical", "electrical"),
    "electrical_panel": _lm("Electrical", "electrical"),
    "electrical_outlet": _lm("Electrical", "electrical"),
    "doorbell": _lm("Electrical", "electrical"),
    "smoke_detector": _lm("Fire Protection & Alarm", "electrical"),
    "security_system": _lm("Security", "electrical"),
    # outside the model: appliances, fireplaces, furnishings, treatments, decor, site
    "fireplace": _lm(MISCELLANEOUS, None),
    "mirror": _lm(MISCELLANEOUS, None),
    "appliance": _lm(MISCELLANEOUS, None),
    "refrigerator": _lm(MISCELLANEOUS, None),
    "dishwasher": _lm(MISCELLANEOUS, None),
    "stove": _lm(MISCELLANEOUS, None),
    "oven": _lm(MISCELLANEOUS, None),
    "microwave": _lm(MISCELLANEOUS, None),
    "range_hood": _lm(MISCELLANEOUS, None),
    "washer": _lm(MISCELLANEOUS, None),
    "dryer": _lm(MISCELLANEOUS, None),
    "blinds": _lm(MISCELLANEOUS, None),
    "window_treatment": _lm(MISCELLANEOUS, None),
    "furniture": _lm(MISCELLANEOUS, None),
    "desk": _lm(MISCELLANEOUS, None),
    "chair": _lm(MISCELLANEOUS, None),
    "filing_cabinet": _lm(MISCELLANEOUS, None),
    "deck": _lm(MISCELLANEOUS, None),
    "patio": _lm(MISCELLANEOUS, None),
    "porch": _lm(MISCELLANEOUS, None),
    "pool": _lm(MISCELLANEOUS, None),
    "fence": _lm(MISCELLANEOUS, None),
    "retaining_wall": _lm(MISCELLANEOUS, None),
    "driveway": _lm(MISCELLANEOUS, None),
    "sidewalk": _lm(MISCELLANEOUS, None),
    "parking_lot": _lm(MISCELLANEOUS, None),
    "landscaping": _lm(MISCELLANEOUS, None),
    "signage": _lm(MISCELLANEOUS, None),
    "shed": _lm(MISCELLANEOUS, None),
    "concrete_pad": _lm(MISCELLANEOUS, None),
    "other": _lm(MISCELLANEOUS, None),
    # the concealed rows (section 3.5 and 3.6)
    "foundation": _lm(MISCELLANEOUS, "foundation"),
    "structural_framing": _lm(MISCELLANEOUS, "framing"),
    "exterior_wall_assembly": _lm(MISCELLANEOUS, "exterior_walls"),
    "roofing_assembly": _lm("Roofing Systems", "roofing"),
    "interior_partition": _lm("Interior Framing / Partitions", "interiors"),
    "painting": _lm("Painting", "interiors"),
    "plumbing_rough_in": _lm("Plumbing", "mechanical"),
    "hvac_distribution": _lm("HVAC", "mechanical"),
    "electrical_wiring": _lm("Electrical", "electrical"),
    "fire_alarm_system": _lm("Fire Protection & Alarm", "electrical"),
    "data_cabling": _lm("Electrical", "electrical"),
    "access_control": _lm("Security", "exterior_walls"),
    "site_utilities": _lm(MISCELLANEOUS, None),
}


def mapping_for(label: str | None) -> LabelMapping:
    """The system and component of a label (an alias resolves through the rules table's
    canonical key, so ``cabinetry`` maps as ``kitchen_cabinet``); an unknown or missing
    label is Miscellaneous, outside the model."""
    if label is None:
        return LABEL_SYSTEMS["other"]
    found = LABEL_SYSTEMS.get(label)
    if found is None:
        resolved = canonical_key(normalize_label(label))
        if resolved is not None:
            found = LABEL_SYSTEMS.get(resolved[0])
    return found or LABEL_SYSTEMS["other"]


# ----------------------------------------------------------------- the carve-out


@dataclass(frozen=True, slots=True)
class ItemizedRow:
    """One bottom-up asset as the carve-out sees it: its label and its RCN (None: unpriced)."""

    label: str | None
    rcn: Decimal | None
    count: int = 1


@dataclass(frozen=True, slots=True)
class ComponentCarve:
    component: str
    top_down: Decimal
    itemized: Decimal
    itemized_assets: int
    unpriced_assets: int
    residual: Decimal  # top_down - itemized, clamped at zero
    clamped: Decimal  # the excess when itemized > top_down, else zero


@dataclass(frozen=True, slots=True)
class CarveOut:
    components: tuple[ComponentCarve, ...]
    outside_model: Decimal  # priced assets the model does not include
    outside_model_assets: int
    carved: bool = True  # False: top-down only, the itemized rows were not subtracted

    def get(self, component: str) -> ComponentCarve:
        return next(c for c in self.components if c.component == component)


def carve_out(
    top_down: Mapping[str, Decimal], itemized: Iterable[ItemizedRow], *, carve: bool = True
) -> CarveOut:
    """Each component's top-down figure less the itemized assets mapped to it; with
    ``carve=False`` (the sweep's top-down-only variant) the itemized dollars are counted but
    not subtracted, so the residual is the whole component."""
    priced: dict[str, Decimal] = dict.fromkeys(COMPONENTS, ZERO)
    counts: dict[str, int] = dict.fromkeys(COMPONENTS, 0)
    unpriced: dict[str, int] = dict.fromkeys(COMPONENTS, 0)
    outside = ZERO
    outside_n = 0
    for row in itemized:
        component = mapping_for(row.label).component
        if component is None:
            if row.rcn is not None:
                outside += row.rcn
                outside_n += 1
            continue
        if row.rcn is None:
            unpriced[component] += 1
            continue
        priced[component] += row.rcn
        counts[component] += 1
    carves = []
    for component in COMPONENTS:
        top = money(top_down[component])
        residual = top - priced[component] if carve else top
        carves.append(
            ComponentCarve(
                component=component,
                top_down=top,
                itemized=money(priced[component]),
                itemized_assets=counts[component],
                unpriced_assets=unpriced[component],
                residual=max(residual, ZERO),
                clamped=max(-residual, ZERO),
            )
        )
    return CarveOut(tuple(carves), money(outside), outside_n, carve)


# ----------------------------------------------------------------- the concealed rows


@dataclass(frozen=True, slots=True)
class ConcealedRow:
    """One concealed asset to write: the label the rules table classifies, the engineer's
    row name, the quantity and unit, the RCN when the model gives it (None: priced from the
    cost table's row for the label, or left unpriced), and the record of how."""

    label: str
    category: str
    system: str
    component: str | None
    quantity: Decimal
    unit: str
    method: Literal["count", "area", "perimeter"]
    formula: str
    inputs: dict[str, str | int | bool | None]
    assumptions: tuple[str, ...]
    source: str
    rcn: Decimal | None
    needs_review: bool
    review_reasons: tuple[str, ...]


CATEGORIES: Final[dict[str, str]] = {
    "foundation": "Foundations (footings, foundation walls, slab, excavation and backfill)",
    "structural_framing": "Structural Framing (exterior wall, roof and floor framing)",
    "exterior_wall_assembly": "Exterior Façade / Building Skin (sheathing, housewrap, insulation)",
    "roofing_assembly": "Roofing Systems (underlayment, flashings, drip edge)",
    "interior_partition": "Interior Framing / Partitions (gypsum board walls and ceilings)",
    "painting": "Painting (primer and two coats)",
    "plumbing_rough_in": "Plumbing (supply, drain, waste and vent piping)",
    "hvac_distribution": "HVAC (heating plant, ductwork or piping)",
    "electrical_wiring": "Electrical (service and branch wiring)",
    "fire_alarm_system": "Fire Protection & Alarm (hard-wired smoke alarms)",
    "data_cabling": "Communication / Data (telephone and data outlets)",
    "access_control": "Access Control (keyed locksets and deadbolts)",
    "site_utilities": "Site Utilities (water, sewer and gas laterals)",
}
# The engineer's row each concealed label answers (the e2e scorer's kind C rows and the
# result's ``not_produced`` list).
ENGINEER_ROWS: Final[dict[str, str]] = {
    label: category.split(" (")[0] for label, category in CATEGORIES.items()
}


@dataclass(frozen=True, slots=True)
class StructureInputs:
    """What the result needs to run the model: the appraisal's readings and the study's
    cost index (``basis.domain.measurements.resolve_cost_index``)."""

    measurements: BuildingMeasurements | None
    cost_approach: CostApproach | None
    cost_year: int
    year_source: str | None = None
    location_factor: Decimal = ONE
    location_source: str | None = None
    # The resolved time factor (``CostIndex.year``): None prices from the released book.
    year: YearFactor | None = None
    # The variant (the sweep's axes); the defaults are the production choice.
    rcn_source: RcnSource = DEFAULT_RCN_SOURCE
    split: SplitMethod = DEFAULT_SPLIT
    carve: bool = True
    depreciation_method: DepreciationMethod = DEFAULT_DEPRECIATION

    @property
    def living_area(self) -> Decimal | None:
        if self.measurements is not None and self.measurements.gla_sf is not None:
            return self.measurements.gla_sf.value
        if self.cost_approach is not None and self.cost_approach.dwelling_sqft is not None:
            return self.cost_approach.dwelling_sqft.value
        return None


# The residual of each component and the label(s) it is written as, with the share of each.
RESIDUAL_ROWS: Final[dict[str, tuple[tuple[str, Decimal], ...]]] = {
    "site_work": (("foundation", ONE),),
    "foundation": (("foundation", ONE),),
    "framing": (("structural_framing", ONE),),
    "exterior_walls": (("exterior_wall_assembly", ONE),),
    "roofing": (("roofing_assembly", ONE),),
    "interiors": (
        ("interior_partition", ONE - PAINTING_SHARE_OF_INTERIORS_RESIDUAL),
        ("painting", PAINTING_SHARE_OF_INTERIORS_RESIDUAL),
    ),
    "mechanical": (
        ("plumbing_rough_in", PLUMBING_SHARE_OF_MECHANICAL_RESIDUAL),
        ("hvac_distribution", ONE - PLUMBING_SHARE_OF_MECHANICAL_RESIDUAL),
    ),
    "electrical": (("electrical_wiring", ONE),),
    # specialties: no concealed content; its residual is reported, never written
}
SPLIT_SOURCES: Final[dict[str, str]] = {
    "interiors": (
        f"{RSMEANS} Assemblies 6-04 Drywall & Thincoat Wall Systems, printed p.214: drywall "
        f"{DRYWALL_PER_SF}/SF of wall against painting {PAINT_PER_SF}/SF"
    ),
    "mechanical": (
        f"{RSMEANS} Assemblies 8-12 (three-fixture bathroom with vanity {BATHROOM_SYSTEM}, "
        f"printed p.250) and 8-60 (gas-fired hot-air heating, one zone, 1,200 SF building "
        f"{HEATING_SYSTEM}, printed p.268)"
    ),
}


def _pct(share: Decimal, *, signed: bool = False) -> str:
    value = (share * HUNDRED).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value:+} %" if signed else f"{value} %"


def _residual_rows(
    rcn: BuildingRcn,
    carve: CarveOut,
    living_area: Decimal | None,
    split: SplitMethod = DEFAULT_SPLIT,
) -> list[ConcealedRow]:
    """The building rows: each component's residual, by label."""
    rows: list[ConcealedRow] = []
    shares = split_shares(rcn.model, split)
    amounts: dict[str, Decimal] = {}
    formulas: dict[str, list[str]] = {}
    inputs: dict[str, dict[str, str | int | bool | None]] = {}
    for component in COMPONENTS:
        c = carve.get(component)
        targets = RESIDUAL_ROWS.get(component)
        if targets is None:
            continue
        with localcontext() as ctx:
            ctx.prec = PRECISION
            parts = _largest_remainder([c.residual * w for _, w in targets], c.residual)
        for (label, weight), part in zip(targets, parts, strict=True):
            amounts[label] = amounts.get(label, ZERO) + part
            text = (
                f"{COMPONENT_NAMES[component]} {_pct(shares[component])} of the building RCN "
                f"{_fmt(rcn.amount)} = {_fmt(c.top_down)}, less {c.itemized_assets} itemized "
                f"assets {_fmt(c.itemized)} = {_fmt(c.residual)}"
            )
            if weight != ONE:
                text += f", x {_pct(weight)} ({SPLIT_SOURCES[component]})"
            formulas.setdefault(label, []).append(text)
            entry = inputs.setdefault(label, {})
            entry[f"{component}_top_down"] = _fmt(c.top_down)
            entry[f"{component}_itemized"] = _fmt(c.itemized)
            entry[f"{component}_itemized_assets"] = c.itemized_assets
            entry[f"{component}_residual"] = _fmt(c.residual)
            if c.clamped > 0:
                entry[f"{component}_clamped"] = _fmt(c.clamped)
    quantity = living_area if living_area is not None and living_area > 0 else ONE
    unit = "SF" if living_area is not None and living_area > 0 else "EA"
    for label, amount in amounts.items():
        clamped = [k for k in inputs[label] if k.endswith("_clamped")]
        reasons: list[str] = []
        if rcn.needs_review:
            reasons.append("structure:building_rcn_review")
        if clamped:
            reasons.append("structure:component_clamped")
        if amount == 0:
            reasons.append("structure:residual_zero")
        rows.append(
            ConcealedRow(
                label=label,
                category=CATEGORIES[label],
                system=LABEL_SYSTEMS[label].system,
                component=LABEL_SYSTEMS[label].component,
                quantity=quantity,
                unit=unit,
                method="area" if unit == "SF" else "count",
                formula="; ".join(formulas[label]),
                inputs={
                    **inputs[label],
                    "building_rcn": _fmt(rcn.amount),
                    "building_rcn_source": rcn.source,
                    "model": rcn.model.name,
                    "model_source": rcn.model.cite(),
                    "split": split,
                    "split_source": split_source(rcn.model, split),
                    "carved": carve.carved,
                    "overhead_spread": "pro rata over the nine components (ATG ch. 4 C.11)",
                },
                assumptions=rcn.assumptions
                + (
                    ()
                    if carve.carved
                    else (
                        "the itemized rows are not carved out (top-down only): the systems "
                        "count them twice",
                    )
                ),
                source=f"{split_source(rcn.model, split)}; {rcn.formula}",
                rcn=amount,
                needs_review=bool(reasons),
                review_reasons=tuple(reasons),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class SmallSystemInputs:
    """What the quantity rules of the small systems need (STRUCTURE_MODEL.md section 3.6)."""

    bedrooms: Decimal | None
    total_rooms: Decimal | None
    stories: Decimal | None
    has_basement: bool
    public_utilities: tuple[str, ...]
    smoke_detectors_found: int
    exterior_doors_found: int


def small_system_inputs(
    measurements: BuildingMeasurements | None,
    cost_approach: CostApproach | None,
    itemized: Iterable[ItemizedRow],
) -> SmallSystemInputs:
    counts: dict[str, int] = {"smoke_detector": 0, "exterior_door": 0}
    for row in itemized:
        if row.label in counts:
            counts[row.label] += max(row.count, 1)
    return SmallSystemInputs(
        bedrooms=None
        if cost_approach is None or cost_approach.bedrooms is None
        else cost_approach.bedrooms.value,
        total_rooms=None
        if cost_approach is None or cost_approach.total_rooms is None
        else cost_approach.total_rooms.value,
        stories=None
        if measurements is None or measurements.stories is None
        else measurements.stories.value,
        has_basement=measurements is not None and measurements.basement_sf is not None,
        public_utilities=() if cost_approach is None else cost_approach.public_utilities,
        smoke_detectors_found=counts["smoke_detector"],
        exterior_doors_found=counts["exterior_door"],
    )


def small_system_rows(inputs: SmallSystemInputs) -> list[ConcealedRow]:
    """The four small systems with a code- or standard-derived quantity; priced by the caller
    from the cost table's row for the label (``rcn`` is None here)."""
    rows: list[ConcealedRow] = []
    # Fire alarm: IRC R314.3, one per sleeping room, one outside each sleeping area (one per
    # story with bedrooms, counted as one per story), one per additional story and basement.
    if inputs.bedrooms is not None:
        stories = (
            int(inputs.stories.to_integral_value(rounding=ROUND_HALF_UP)) if inputs.stories else 1
        )
        stories = max(stories, 1)
        required = int(inputs.bedrooms) + stories + (1 if inputs.has_basement else 0)
        quantity = max(required - inputs.smoke_detectors_found, 0)
        rows.append(
            ConcealedRow(
                label="fire_alarm_system",
                category=CATEGORIES["fire_alarm_system"],
                system=LABEL_SYSTEMS["fire_alarm_system"].system,
                component=LABEL_SYSTEMS["fire_alarm_system"].component,
                quantity=Decimal(quantity),
                unit="EA",
                method="count",
                formula=(
                    f"{int(inputs.bedrooms)} bedrooms + {stories} stories"
                    f"{' + 1 basement' if inputs.has_basement else ''} = {required} alarms "
                    f"(IRC R314.3), less {inputs.smoke_detectors_found} detectors in the "
                    "photographs"
                ),
                inputs={
                    "bedrooms": int(inputs.bedrooms),
                    "stories": stories,
                    "basement": inputs.has_basement,
                    "required_alarms": required,
                    "smoke_detectors_found": inputs.smoke_detectors_found,
                    "rule": "IRC R314.3 (2021)",
                },
                assumptions=(
                    "the alarm outside each sleeping area is counted once per story",
                    "stories counted from the appraisal" if inputs.stories else "1 story assumed",
                ),
                source="IRC R314.3 (2021): smoke alarms in each sleeping room, outside each "
                "separate sleeping area, and on each additional story including basements",
                rcn=None,
                needs_review=False,
                review_reasons=(),
            )
        )
    # Data cabling: TIA-570, one telecommunications outlet per room (the URAR room count).
    if inputs.total_rooms is not None:
        rows.append(
            ConcealedRow(
                label="data_cabling",
                category=CATEGORIES["data_cabling"],
                system=LABEL_SYSTEMS["data_cabling"].system,
                component=LABEL_SYSTEMS["data_cabling"].component,
                quantity=Decimal(int(inputs.total_rooms)),
                unit="EA",
                method="count",
                formula=f"{int(inputs.total_rooms)} rooms x 1 outlet per room (TIA-570)",
                inputs={
                    "total_rooms": int(inputs.total_rooms),
                    "rule": "TIA-570 residential, one outlet per room",
                },
                assumptions=(
                    "one telecommunications outlet per room (TIA-570 minimum grade); the "
                    "standard is not in the corpus",
                ),
                source=(
                    "TIA-570 residential telecommunications cabling: one outlet per room at "
                    "the minimum grade"
                ),
                rcn=None,
                needs_review=False,
                review_reasons=(),
            )
        )
    # Access control: one keyed lockset with deadbolt per exterior door.
    doors = inputs.exterior_doors_found or EXTERIOR_DOORS_DEFAULT
    assumed = inputs.exterior_doors_found == 0
    rows.append(
        ConcealedRow(
            label="access_control",
            category=CATEGORIES["access_control"],
            system=LABEL_SYSTEMS["access_control"].system,
            component=LABEL_SYSTEMS["access_control"].component,
            quantity=Decimal(doors),
            unit="EA",
            method="count",
            formula=(
                f"{doors} exterior doors x 1 keyed lockset with deadbolt"
                + (" (front and rear assumed)" if assumed else " (from the photographs)")
            ),
            inputs={
                "exterior_doors": doors,
                "exterior_doors_source": "assumed" if assumed else "photographs",
            },
            assumptions=(
                f"{EXTERIOR_DOORS_DEFAULT} exterior doors assumed: none in the photographs",
            )
            if assumed
            else (),
            source="one keyed lockset and deadbolt per exterior door",
            rcn=None,
            needs_review=assumed,
            review_reasons=("structure:assumed_quantity",) if assumed else (),
        )
    )
    # Site utilities: a lateral per public service, an assumed length each.
    if inputs.public_utilities:
        n = len(inputs.public_utilities)
        rows.append(
            ConcealedRow(
                label="site_utilities",
                category=CATEGORIES["site_utilities"],
                system=LABEL_SYSTEMS["site_utilities"].system,
                component=LABEL_SYSTEMS["site_utilities"].component,
                quantity=LATERAL_LENGTH_FT * n,
                unit="LF",
                method="perimeter",
                formula=(
                    f"{n} public services ({', '.join(inputs.public_utilities)}) x "
                    f"{_fmt(LATERAL_LENGTH_FT)} LF each"
                ),
                inputs={
                    "public_utilities": ", ".join(inputs.public_utilities),
                    "lateral_length_ft": _fmt(LATERAL_LENGTH_FT),
                    "lateral_length_source": "assumed",
                },
                assumptions=(f"{_fmt(LATERAL_LENGTH_FT)} LF per lateral assumed: no site plan",),
                source=(
                    "ATG Exhibit A p.319 (site utilities: water, sanitary sewer, gas and "
                    "electrical services from the property line)"
                ),
                rcn=None,
                needs_review=True,
                review_reasons=("structure:assumed_quantity",),
            )
        )
    return rows


def concealed_rows(
    rcn: BuildingRcn | None,
    carve: CarveOut | None,
    living_area: Decimal | None,
    small: SmallSystemInputs,
    split: SplitMethod = DEFAULT_SPLIT,
) -> list[ConcealedRow]:
    """Every concealed row of a study: the building residuals when a building RCN exists,
    then the small systems."""
    rows: list[ConcealedRow] = []
    if rcn is not None and carve is not None:
        rows += _residual_rows(rcn, carve, living_area, split)
    rows += small_system_rows(small)
    return rows


# ----------------------------------------------------------------- depreciation


@dataclass(frozen=True, slots=True)
class Depreciation:
    rate: Decimal  # 0..1, to 6 places
    method: Literal["appraisal_physical_depreciation", "age_life"]
    source: str
    checks: tuple[Check, ...]


def depreciation(
    cost_approach: CostApproach | None, method: DepreciationMethod = DEFAULT_DEPRECIATION
) -> Depreciation | None:
    """The appraiser's physical depreciation over the cost new, else the age-life ratio of
    the effective age and remaining economic life; None without either. ``age_life`` asks
    for the ratio first; ``none`` returns None (RCN compared undepreciated: a baseline)."""
    if cost_approach is None or method == "none":
        return None
    checks: list[Check] = []
    age_life: Decimal | None = None
    age_text = ""
    age_source = ""
    age, remaining = cost_approach.effective_age, cost_approach.remaining_economic_life
    if age is not None and remaining is not None and age.value + remaining.value > 0:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            age_life = (age.value / (age.value + remaining.value)).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
        age_text = f"{_fmt(age.value)} / {_fmt(age.value + remaining.value)} years"
        age_source = f"{age.describe()} / ({age.describe()} + {remaining.describe()})"
    physical = cost_approach.depreciation_physical or cost_approach.depreciation_total
    total = cost_approach.total_cost_new
    if method == "age_life" and age_life is not None and age_life < ONE:
        checks.append(
            Check(
                "depreciation_age_life",
                "ok",
                f"age-life: effective age over total economic life, {age_text}",
            )
        )
        checks.append(_uniform_check())
        return Depreciation(age_life, "age_life", age_source, tuple(checks))
    if physical is not None and total is not None and total.value > 0:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            rate = (physical.value / total.value).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
        if rate >= ONE:
            return None
        if age_life is None:
            checks.append(
                Check(
                    "depreciation_age_life",
                    "not_checked",
                    "no effective age and remaining economic life printed",
                )
            )
        else:
            diff = abs(rate - age_life)
            checks.append(
                Check(
                    "depreciation_age_life",
                    "ok" if diff <= Decimal("0.01") else "warning",
                    f"the appraiser's physical depreciation {_pct(rate)} against the age-life "
                    f"ratio {_pct(age_life)} ({age_text})",
                )
            )
        checks.append(_uniform_check())
        return Depreciation(
            rate,
            "appraisal_physical_depreciation",
            f"{physical.describe()} / {total.describe()}",
            tuple(checks),
        )
    if age_life is not None and age_life < ONE:
        checks.append(
            Check(
                "depreciation_age_life",
                "ok",
                f"age-life: effective age over total economic life, {age_text}",
            )
        )
        checks.append(_uniform_check())
        return Depreciation(age_life, "age_life", age_source, tuple(checks))
    return None


def _uniform_check() -> Check:
    return Check(
        "depreciation_uniform",
        "warning",
        "one physical depreciation rate (the appraiser's, for the improvements as a whole) is "
        "applied to every row; the guide expects the adjustment to differ by class (ATG ch. 3 "
        "A(6)); a per-class age-life needs the ages of finishes and equipment",
    )


def depreciate(rcns: Sequence[Decimal], rate: Decimal) -> list[Decimal]:
    """``rcn x (1 - rate)`` per row, footing to the depreciated total to the cent."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        exact = [r * (ONE - rate) for r in rcns]
        total = money(sum(rcns, ZERO) * (ONE - rate))
    if not rcns:
        return []
    return _largest_remainder(exact, total)


# ----------------------------------------------------------------- reconciliation

Status = Literal["reconciled", "unreconciled", "not_checked"]

ATG_CAUSES: Final[tuple[tuple[str, str], ...]] = (
    ("land_fmv", "Incorrect FMV of the land"),
    (
        "assets_missing",
        "Failure to properly identify all the parcels of land, assets, building components, "
        "quantities, etc.",
    ),
    ("cost_factors", "Utilizing incorrect cost estimates and factors"),
    ("depreciation_factors", "Utilizing incorrect depreciation/obsolescence factors"),
    ("indirect_costs", "Failure to properly account for indirect costs"),
)


@dataclass(frozen=True, slots=True)
class SystemLine:
    component: str
    top_down: Decimal
    itemized: Decimal
    concealed: Decimal
    clamped: Decimal


@dataclass(frozen=True, slots=True)
class Reconciliation:
    status: Status
    building_basis: Decimal
    rcn_total: Decimal
    rcnld_total: Decimal | None
    gap: Decimal | None  # B - RCNLD
    gap_share: Decimal | None  # of B, to 6 places
    tolerance: Decimal
    implied_uniform_factor: Decimal | None  # T / ΣRCN: the old method's number
    by_component: tuple[SystemLine, ...]
    unassigned_remainders: tuple[tuple[str, Decimal], ...]
    outside_model: Decimal
    unpriced_assets: int
    appraisal_check: Check
    causes: tuple[tuple[str, str, str], ...]  # (code, the guide's words, what the data says)
    checks: tuple[Check, ...]


def reconcile(
    *,
    building_basis: Decimal,
    total_basis: Decimal,
    rcn_total: Decimal,
    rcnld_total: Decimal | None,
    building: BuildingRcn | None,
    carve: CarveOut | None,
    concealed_by_component: Mapping[str, Decimal],
    unpriced_assets: int,
    land_checks: Sequence[Check],
    depreciation_: Depreciation | None,
    purchase_price: Decimal | None = None,
    tolerance: Decimal = RECONCILIATION_TOLERANCE,
) -> Reconciliation:
    """RCNLD against the purchase price less land, with the diagnosis."""
    b = building_basis
    checks: list[Check] = []
    lines: tuple[SystemLine, ...] = ()
    remainders: list[tuple[str, Decimal]] = []
    outside = ZERO
    if carve is not None:
        lines = tuple(
            SystemLine(
                c.component,
                c.top_down,
                c.itemized,
                money(concealed_by_component.get(c.component, ZERO)),
                c.clamped,
            )
            for c in carve.components
        )
        outside = carve.outside_model
        for c in carve.components:
            if c.component not in RESIDUAL_ROWS and c.residual > 0:
                remainders.append((c.component, c.residual))
    with localcontext() as ctx:
        ctx.prec = PRECISION
        implied = (
            (total_basis / rcn_total).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
            if rcn_total > 0
            else None
        )
        gap = gap_share = None
        if rcnld_total is not None and b > 0:
            gap = money(b - rcnld_total)
            gap_share = (gap / b).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if building is None:
        status: Status = "not_checked"
        checks.append(
            Check(
                "reconciliation",
                "error",
                "no building RCN: the appraisal's cost approach is not developed and no living "
                "area sizes a square-foot model; the schedule holds only what the photographs show",
            )
        )
    elif depreciation_ is None or rcnld_total is None or gap_share is None:
        status = "not_checked"
        checks.append(
            Check(
                "reconciliation",
                "error",
                "no depreciation rate: the appraisal prints neither physical depreciation nor an "
                "effective age and remaining economic life; RCN cannot be compared with the basis",
            )
        )
    elif abs(gap_share) <= tolerance:
        status = "reconciled"
        checks.append(
            Check(
                "reconciliation",
                "ok",
                f"RCNLD {_fmt(rcnld_total)} against the purchase price less land {_fmt(b)}: "
                f"gap {_fmt(gap or ZERO)} ({_pct(gap_share, signed=True)}), within the "
                f"square-foot method's {_pct(tolerance)} (RSMeans Residential 2020 p.15); the "
                "basis is allocated pro rata on RCNLD",
            )
        )
    else:
        status = "unreconciled"
        checks.append(
            Check(
                "reconciliation",
                "error",
                f"RCNLD {_fmt(rcnld_total)} against the purchase price less land {_fmt(b)}: "
                f"gap {_fmt(gap or ZERO)} ({_pct(gap_share, signed=True)}) is outside "
                f"{_pct(tolerance)}; the allocation is withheld until the source of the "
                "difference is found (ATG ch. 3 D(5)-(6)); the causes below say what the data "
                "shows",
            )
        )
    # The appraisal's own reconciliation: depreciated improvements + site improvements against
    # the price less the appraisal's site value (an independent check of the appraiser's figures).
    if (
        building is not None
        and building.appraisal_depreciated_cost is not None
        and building.appraisal_site_value is not None
        and purchase_price is not None
        and purchase_price > building.appraisal_site_value
    ):
        improvements = building.appraisal_depreciated_cost + (
            building.appraisal_site_improvements or ZERO
        )
        price_less_site = purchase_price - building.appraisal_site_value
        with localcontext() as ctx:
            ctx.prec = PRECISION
            diff = (improvements - price_less_site) / price_less_site
        appraisal_check = Check(
            "appraisal_reconciles",
            "ok" if abs(diff) <= tolerance else "warning",
            f"the appraisal's depreciated cost of improvements plus site improvements "
            f"{_fmt(improvements)} against the purchase price less the appraisal's site value "
            f"{_fmt(price_less_site)}: {_pct(diff, signed=True)}",
        )
    else:
        appraisal_check = Check(
            "appraisal_reconciles",
            "not_checked",
            "the appraisal prints no depreciated cost or site value",
        )
    causes = _causes(
        land_checks=land_checks,
        building=building,
        carve=carve,
        remainders=remainders,
        unpriced=unpriced_assets,
        depreciation_=depreciation_,
        outside=outside,
    )
    return Reconciliation(
        status=status,
        building_basis=b,
        rcn_total=money(rcn_total),
        rcnld_total=None if rcnld_total is None else money(rcnld_total),
        gap=gap,
        gap_share=gap_share,
        tolerance=tolerance,
        implied_uniform_factor=implied,
        by_component=lines,
        unassigned_remainders=tuple(remainders),
        outside_model=outside,
        unpriced_assets=unpriced_assets,
        appraisal_check=appraisal_check,
        causes=causes,
        checks=tuple(checks),
    )


def _causes(
    *,
    land_checks: Sequence[Check],
    building: BuildingRcn | None,
    carve: CarveOut | None,
    remainders: Sequence[tuple[str, Decimal]],
    unpriced: int,
    depreciation_: Depreciation | None,
    outside: Decimal,
) -> tuple[tuple[str, str, str], ...]:
    """The guide's five causes (D(6)), each with what the data says."""
    out: list[tuple[str, str, str]] = []
    for code, words in ATG_CAUSES:
        if code == "land_fmv":
            flagged = [c for c in land_checks if c.severity in ("warning", "error")]
            said = (
                "; ".join(f"{c.code}: {c.message}" for c in flagged)
                if flagged
                else "every land check of the worksheet is ok"
            )
        elif code == "assets_missing":
            parts = [f"{unpriced} assets are unpriced"]
            if carve is not None:
                clamped = [c for c in carve.components if c.clamped > 0]
                if clamped:
                    parts.append(
                        "itemized assets exceed the model in "
                        + ", ".join(
                            f"{COMPONENT_NAMES[c.component]} by {_fmt(c.clamped)}" for c in clamped
                        )
                    )
                if remainders:
                    parts.append(
                        "unassigned remainders in "
                        + ", ".join(f"{COMPONENT_NAMES[c]} {_fmt(v)}" for c, v in remainders)
                    )
                parts.append(
                    f"{_fmt(outside)} of itemized assets are outside the model (appliances, "
                    "site, furnishings)"
                )
            else:
                parts.append("no building model: the concealed systems are not produced")
            said = "; ".join(parts)
        elif code == "cost_factors":
            said = (
                "no building RCN"
                if building is None
                else f"building RCN from {building.source.replace('_', ' ')}: {building.formula}"
            )
        elif code == "depreciation_factors":
            said = (
                "no depreciation rate"
                if depreciation_ is None
                else (
                    f"{depreciation_.method.replace('_', ' ')} {_pct(depreciation_.rate)} "
                    f"from {depreciation_.source}, one rate for every row"
                )
            )
        else:
            said = (
                "the model's Overhead component (contractor's overhead, profit and plans) is "
                "spread pro rata over the nine components; the appraiser's cost new includes "
                "indirect costs"
            )
        out.append((code, words, said))
    return tuple(out)


__all__ = [
    "ATG_CAUSES",
    "BASE_COST_BY_AREA",
    "CATEGORIES",
    "DEFAULT_DEPRECIATION",
    "DEFAULT_RCN_SOURCE",
    "DEFAULT_SPLIT",
    "DEPRECIATION_METHODS",
    "ENGINEER_ROWS",
    "MARSHALL_SWIFT_UNAVAILABLE",
    "NAHB_2024_STAGES",
    "NAHB_SOURCE",
    "RCN_SOURCES",
    "SPLIT_METHODS",
    "COMPONENTS",
    "COMPONENT_NAMES",
    "COST_APPROACH_PARTS_TOLERANCE",
    "EXTERIOR_DOORS_DEFAULT",
    "LABEL_SYSTEMS",
    "LATERAL_LENGTH_FT",
    "MISCELLANEOUS",
    "MODELS",
    "PAINTING_SHARE_OF_INTERIORS_RESIDUAL",
    "PLUMBING_SHARE_OF_MECHANICAL_RESIDUAL",
    "RECONCILIATION_TOLERANCE",
    "RESIDUAL_ROWS",
    "STRUCTURE_LABELS",
    "SYSTEMS",
    "BuildingRcn",
    "CarveOut",
    "Check",
    "ComponentCarve",
    "ConcealedRow",
    "CostApproach",
    "Depreciation",
    "ItemizedRow",
    "LabelMapping",
    "Model",
    "ModelChoice",
    "Reconciliation",
    "SmallSystemInputs",
    "StructureError",
    "StructureInputs",
    "SystemLine",
    "base_cost_per_sf",
    "building_rcn",
    "carve_out",
    "class_of",
    "component_shares",
    "concealed_rows",
    "cost_approach_from_fields",
    "depreciate",
    "depreciation",
    "mapping_for",
    "money",
    "nahb_shares",
    "reconcile",
    "select_model",
    "small_system_inputs",
    "small_system_rows",
    "split_components",
    "split_shares",
    "split_source",
    "type_of",
]
