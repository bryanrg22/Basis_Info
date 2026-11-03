"""``python -m evals.takeoff_constants``: every fixed constant of the takeoff varied one at a
time on stored exports, scored the same way (EVAL_REPORT section 35's constants table).

The takeoff sizes each room as a share of the appraisal's living area by a typical area per
room type (``basis.domain.rooms.ROOM_WEIGHTS_SF`` and the program constants of
``basis.domain.measurements``) and measures the roof, siding and gutters from the living
area and fixed geometry (``FOOTPRINT_ASPECT_RATIO``, ``ROOF_PITCH_FACTOR``,
``EAVE_OVERHANG_FT``, ``STORY_WALL_HEIGHT_FT``, ``WALL_OPENINGS_SHARE``, ``GUTTER_EAVES``,
``GARAGE_SF_PER_CAR``); the room-level formulas (``basis.domain.takeoffs``) carry their own
constants (wall area per square foot of floor, waste factors, the wainscot height, the
countertop depth). None was measured on a property; nothing says which of them matters.

This harness answers that without a paid run. For each setup it re-runs the takeoff on a
stored export (the same domain functions ``process_assets`` calls, with the constants
overridden for the duration: :func:`override`), keeps every count-based row as stored,
recomputes the room-measured rows (area and perimeter methods on a ``gla_share``,
``basement_share`` or ``garage_area`` room) and the property-level rows (``roof``,
``siding``, ``gutter``, ``landscaping``), keeps a row that was measured once per room or
property at zero as stored, then prices it as section 49 did (``evals.reprice_export``,
installed cost with the general contractor's O&P, the stored index values), replays the
structure model (``evals.structure_replay``, the production variant) and scores it against
the engineer's report (``evals.e2e_report``). The cell with today's constants is the
regression line: every recomputed quantity must equal the stored one. Each constant is then
set to each value of its stated range (a linear constant at 50, 75, 125 and 150 % of today;
a physical one at its named alternatives) and to the published value the research found
(:data:`CONSTANTS`); last, every published value at once. The sensitivity table ranks the
constants by how far the class-share error moves per unit of relative change (a central
difference over the 75 and 125 % cells), so the ones worth measuring from plans or aerial
data are named.

Each cell also scores the itemized schedule before the structure model (its class shares
against the engineer's), because the structure model carves the 39-year rows out of the
square-foot model's components: a constant that sizes only 39-year rows inside the model
(the roof, the siding, the gutters, tile) moves dollars between rows of the building and
none between classes, which the itemized score shows and the report's score cannot.

``--dump`` replays section 35.2's asset dump (the private ``assets_<study>_rev<n>.json``
with its fields file): first under today's constants against the stored numbers (the
quantities, the priced count, the priced total and the class shares), then under every
setup, each re-measured row at its stored unit cost (the dump carries no cost year to
reprice from) and no scorer (it has no structure model and no report rows).

Deterministic: no model call, cost $0. Usage (from ``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.takeoff_constants export_a.json export_b.json \\
        --cost-table costs/unit_costs_rsmeans_2020.json --supplement hci.json \\
        --out work/e2e_artifacts/takeoff_constants_<date> \\
        [--dump assets.json --fields fields.json --revision 3]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

from basis.domain import measurements as geometry
from basis.domain import rooms as room_guidance
from basis.domain import takeoffs
from basis.domain.cost_indices import YearIndex, supplement_for
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.measurements import (
    measurements_from_fields,
    property_quantities,
    room_areas,
)
from basis.domain.takeoffs import estimate_quantity, spec_for, takeoff_key
from evals import record
from evals.e2e_report import (
    E2ERows,
    EngineerReport,
    default_labels_path,
    load_report,
    load_rows,
)
from evals.reprice_export import reprice
from evals.structure_replay import PRODUCTION, ReplayError, field_rows
from evals.structure_sweep import BUCKETS, Cell, baseline_scores, run_cell

COMPONENT: Final = "takeoff_constants"
VERSION: Final = "v2"  # v2: sourced values, the perimeter and the dump sweep
CENT: Final = Decimal("0.01")
QUANTITY_PLACES: Final = Decimal("0.0001")
MEASURED_SOURCES: Final = frozenset({"gla_share", "basement_share", "garage_area"})
LINEAR_FACTORS: Final = (Decimal("0.5"), Decimal("0.75"), Decimal("1.25"), Decimal("1.5"))
BUILDING_BUCKETS: Final = frozenset({"y27_5", "y39"})


# --------------------------------------------------------------------------- the constants


@dataclass(frozen=True)
class Target:
    """Where a constant lives: a module attribute, a ``ROOM_WEIGHTS_SF`` entry, a field of
    a ``QUANTITY_ESTIMATES`` spec, or the room perimeter's coefficient (``perimeter``: the
    ``4`` of ``4 x sqrt(area)`` in ``estimate_quantity``, applied as a scale on the
    ``per_sf`` of every perimeter spec, since the 4 is a literal there)."""

    kind: str  # geometry | weight | spec | perimeter
    key: str
    attr: str | None = None


@dataclass(frozen=True)
class Constant:
    name: str
    group: str  # geometry | typical_area | formula
    unit: str
    today: Decimal
    targets: tuple[Target, ...]
    what: str
    published: Decimal | None = None
    published_source: str = ""
    # Named alternatives for a constant whose range is physical rather than proportional
    # (a roof pitch, the eaves a gutter runs on); a linear constant has none.
    alternatives: tuple[tuple[str, Decimal], ...] = ()
    # The pair of alternatives the sensitivity slope is taken over when not linear.
    slope_pair: tuple[str, str] | None = None
    # A second sourced value where the sources disagree (ResStock's 2 ft eaves against the
    # cost book's 1 ft), a cell of its own after the range; never in the published set.
    extra: tuple[tuple[str, Decimal], ...] = ()

    @property
    def linear(self) -> bool:
        return not self.alternatives

    def values(self) -> list[tuple[str, Decimal]]:
        """Every value this constant is set to, with the cell's name: the stated range,
        the extra sourced values, then the published value when it differs from today's
        and from every cell before it."""
        out: list[tuple[str, Decimal]] = []
        if self.linear:
            out = [(f"{self.name} x{f}", self.today * f) for f in LINEAR_FACTORS]
        else:
            out = [(f"{self.name} {label}", value) for label, value in self.alternatives]
        for label, value in self.extra:
            if value != self.today and all(v != value for _, v in out):
                out.append((f"{self.name} {label}", value))
        if self.published is not None and self.published != self.today:
            if all(value != self.published for _, value in out):
                out.append((f"{self.name} published", self.published))
        return out


def _typical(
    name: str, key: str | None, program_attr: str | None, today: int, **kw: Any
) -> Constant:
    """A typical room area: the ``ROOM_WEIGHTS_SF`` entry ``key`` (the study's rooms) and
    the program constant ``program_attr`` (the appraisal's room count) that duplicates it;
    the two are one assumption and move together."""
    targets: list[Target] = []
    if key is not None:
        targets.append(Target("weight", key))
    if program_attr is not None:
        targets.append(Target("geometry", program_attr))
    room = (key or program_attr or name).lower().replace("_sf", "").replace("_", " ")
    return Constant(
        name,
        "typical_area",
        "SF",
        Decimal(today),
        tuple(targets),
        f"typical area of a {room} (its share of the living area)",
        **kw,
    )


# Published values (the research recorded with this harness's EVAL_REPORT section). Each is
# from a public source or the cost book Basis prices from; a derived value says how.
NAHB_2019: Final = (
    "NAHB Special Study 'Spaces in New Homes' (Emrath, 2019-03-14; 153 builders, typical "
    "home 2,913 SF), Table 2 (average size of the room when present, all homes)"
)
RESSTOCK: Final = "NREL ResStock options_lookup.tsv and characteristics (single-family detached)"
RSMEANS_RES: Final = "RSMeans Residential 2020"

PERIMETER_SPECS: Final = ("baseboard", "cabinet", "countertop", "crown_molding", "wainscoting")
SQUARE_PERIMETER: Final = Decimal(4)

CONSTANTS: Final[tuple[Constant, ...]] = (
    # --- geometry (basis.domain.measurements)
    Constant(
        "footprint_aspect_ratio",
        "geometry",
        "ratio",
        Decimal("1.5"),
        (Target("geometry", "FOOTPRINT_ASPECT_RATIO"),),
        "long side over short side of the rectangular footprint (perimeter, eaves)",
        published=Decimal("1.8"),
        published_source=(
            f"{RESSTOCK}: geometry_unit_aspect_ratio=1.8 for single-family detached; "
            f"{RSMEANS_RES} square-foot models' perimeter over sqrt(footprint) 4.05-4.27 "
            "(a 1.4:1 to 2.1:1 rectangle)"
        ),
    ),
    Constant(
        "roof_pitch_factor",
        "geometry",
        "factor",
        Decimal("1.118"),
        (Target("geometry", "ROOF_PITCH_FACTOR"),),
        "roof slope factor sqrt(1 + (rise/12)^2) on the plan area",
        published=Decimal("1.118"),
        published_source=(
            f"{RESSTOCK}: geometry_roof_pitch=6:12 on every gable attic option; the factor "
            "is the slope's secant (pitch-multiplier tables)"
        ),
        alternatives=(
            ("4/12", Decimal("1.054")),
            ("8/12", Decimal("1.202")),
            ("12/12", Decimal("1.414")),
        ),
        slope_pair=("4/12", "8/12"),
    ),
    Constant(
        "eave_overhang_ft",
        "geometry",
        "ft",
        Decimal(1),
        (Target("geometry", "EAVE_OVERHANG_FT"),),
        "eave overhang added to the roof plan and the gutter runs",
        published=Decimal(1),
        published_source=(
            f"{RSMEANS_RES} roofing and truss systems (pp. 194-196): '1' overhang' included "
            f"in every quantity; {RESSTOCK}: geometry_eaves_depth=2 ft (100 % of the stock)"
        ),
        extra=(("ResStock 2 ft", Decimal(2)),),
    ),
    Constant(
        "story_wall_height_ft",
        "geometry",
        "ft",
        Decimal(9),
        (Target("geometry", "STORY_WALL_HEIGHT_FT"),),
        "exterior wall height per story (siding)",
        published=Decimal(9),
        published_source=(
            f"{RESSTOCK}: geometry_ceiling_height=8.0 ft for single-family detached, plus "
            "about 1 ft of floor framing per story (derived)"
        ),
    ),
    Constant(
        "wall_openings_share",
        "geometry",
        "share",
        Decimal("0.15"),
        (Target("geometry", "WALL_OPENINGS_SHARE"),),
        "windows and doors deducted from the gross wall area (siding)",
        published=Decimal("0.14"),
        published_source=(
            f"{RESSTOCK} Window Areas: window-to-wall ratio 6/9/12/15/18/30 % on 7.8/25/25/"
            "16/17/8.9 % of the stock, mean 0.14, median 0.12 (windows only); "
            f"{RSMEANS_RES} vinyl siding systems: 'Subtract openings from wall area'"
        ),
    ),
    Constant(
        "gutter_eaves",
        "geometry",
        "eaves",
        Decimal(2),
        (Target("geometry", "GUTTER_EAVES"),),
        "eaves the gutters run on (2: a gable roof's long sides)",
        published=Decimal(2),
        published_source=(
            f"{RSMEANS_RES} Gable End Roofing Systems p.194: gutter 0.083 LF per SF of plan "
            "(the two eaves of a 24 ft deep house), Hip Roof p.196: 0.120; gutter guides: "
            "gable roofs gutter the long eaves, hips every side"
        ),
        alternatives=(("hip (4 sides)", Decimal(4)),),
        slope_pair=("today", "hip (4 sides)"),
    ),
    Constant(
        "garage_sf_per_car",
        "geometry",
        "SF",
        Decimal(240),
        (Target("geometry", "GARAGE_SF_PER_CAR"),),
        "garage area per car when the appraisal prints no garage area",
        published=Decimal(240),
        published_source="a 12 x 20 ft bay; used only without a printed garage area",
    ),
    # --- typical areas (ROOM_WEIGHTS_SF, with the program constant that duplicates it).
    # Only the ratios between them matter: a room's area is GLA x weight / W, and W is a sum
    # of the same weights, so scaling every weight together changes nothing.
    _typical(
        "kitchen_sf",
        "kitchen",
        "KITCHEN_SF",
        150,
        published=Decimal(288),
        published_source=(
            f"{NAHB_2019}: kitchen 288 SF as a separate room (Table 1: 326 SF of kitchen area, "
            "open plans included)"
        ),
    ),
    _typical(
        "bedroom_sf",
        "bedroom",
        "BEDROOM_SF",
        150,
        published=Decimal(233),
        published_source=(
            f"{NAHB_2019}: master 312 SF + other bedrooms 503 SF in all = 815 SF; "
            "/ 3.5 bedrooms = 233 SF (derived; NAHB: 47 % of 2024 starts had 3 bedrooms, "
            "32 % 4)"
        ),
    ),
    _typical(
        "bathroom_sf",
        "bathroom",
        "FULL_BATH_SF",
        75,
        published=Decimal(132),
        published_source=(
            f"{NAHB_2019}: master bath 165 SF, other baths 173 SF in all (Table 1: 164 + 166 "
            "= 330 SF); / 2.5 baths = 132 SF (derived)"
        ),
    ),
    _typical(
        "half_bath_sf",
        None,
        "HALF_BATH_SF",
        40,
        published=None,
        published_source="a 5 x 8 ft powder room; no survey figure",
    ),
    _typical(
        "living_room_sf",
        "living_room",
        None,
        250,
        published=Decimal(343),
        published_source=f"{NAHB_2019}: living room 343 SF",
    ),
    _typical(
        "family_room_sf",
        "family_room",
        None,
        250,
        published=Decimal(398),
        published_source=f"{NAHB_2019}: family room 398 SF",
    ),
    _typical(
        "dining_room_sf",
        "dining_room",
        None,
        150,
        published=Decimal(209),
        published_source=f"{NAHB_2019}: dining room 209 SF",
    ),
    _typical(
        "office_sf",
        "office",
        None,
        120,
        published=Decimal(185),
        published_source=f"{NAHB_2019}: study/den/library 185 SF",
    ),
    _typical(
        "laundry_sf",
        "laundry",
        None,
        50,
        published=Decimal(104),
        published_source=f"{NAHB_2019}: laundry room 104 SF",
    ),
    _typical(
        "hallway_sf",
        "hallway",
        None,
        50,
        published=None,
        published_source=(
            f"{NAHB_2019}: 'other' finished space 332 SF, mostly hallways (not per room)"
        ),
    ),
    _typical(
        "stairway_sf", "stairway", None, 40, published=None, published_source="no survey figure"
    ),
    _typical(
        "foyer_sf",
        "lobby",
        None,
        60,
        published=Decimal(90),
        published_source=f"{NAHB_2019}: entry foyer 90 SF (46 small homes, 141 large)",
    ),
    _typical(
        "storage_sf",
        "storage",
        None,
        30,
        published=None,
        published_source=f"{NAHB_2019}: 79 SF of closet space per home (not per room)",
    ),
    _typical(
        "mechanical_room_sf",
        "mechanical_room",
        None,
        40,
        published=None,
        published_source="no survey figure",
    ),
    _typical(
        "basement_room_sf",
        "basement",
        None,
        150,
        published=None,
        published_source="no survey figure (capped by the finished basement area anyway)",
    ),
    _typical(
        "other_weight_sf",
        "other",
        None,
        150,
        published=None,
        published_source="no survey figure",
    ),
    _typical(
        "other_room_sf",
        None,
        "OTHER_ROOM_SF",
        200,
        published=Decimal(317),
        published_source=(
            f"{NAHB_2019}: mean of the living (343), family (398) and dining (209) rooms; the "
            "appraisal's rooms that are not the kitchen or a bedroom"
        ),
    ),
    # --- the room-level formulas (basis.domain.takeoffs)
    Constant(
        "wall_area_per_floor_sf",
        "formula",
        "SF/SF",
        Decimal("3.5"),
        (Target("spec", "paint", "per_sf"), Target("spec", "wall_finish", "per_sf")),
        "wall area per square foot of floor (paint, wall finish)",
        published=Decimal("2.6"),
        published_source=(
            f"perimeter x ceiling height on a 150 SF square room: 4 x sqrt(150) x 8 ft "
            f"({RESSTOCK} ceiling) / 150 = 2.6 (derived; 3.5 is an 84 SF room's ratio)"
        ),
    ),
    Constant(
        "carpet_waste",
        "formula",
        "factor",
        Decimal("1.10"),
        (Target("spec", "carpet", "waste_factor"),),
        "carpet seams and waste",
        published=Decimal("1.10"),
        published_source="flooring estimating guides: 10 % (10-15 % with pattern match)",
    ),
    Constant(
        "tile_waste",
        "formula",
        "factor",
        Decimal("1.15"),
        (Target("spec", "tile", "waste_factor"),),
        "tile cuts and waste",
        published=Decimal("1.15"),
        published_source="tile estimating guides: 10-15 %",
    ),
    Constant(
        "wainscot_height_ft",
        "formula",
        "ft",
        Decimal(2),
        (Target("spec", "wainscoting", "per_sf"),),
        "wainscot height on the room perimeter",
        published=Decimal(3),
        published_source=(
            "32-36 in wainscot (trim guides); the cost row Basis prices it on "
            f"({RSMEANS_RES} 06 22 13.45 5600) is a 2 ft high wainscot, today's value since "
            "EVAL_REPORT 89 (3 ft before)"
        ),
        extra=(("3 ft before EVAL 89", Decimal(3)),),
    ),
    Constant(
        "countertop_depth_ft",
        "formula",
        "ft",
        Decimal(25) / Decimal(12),
        (Target("spec", "countertop", "per_sf"),),
        "countertop depth on the cabinet run",
        published=Decimal(25) / Decimal(12),
        published_source="25 to 25.5 in standard depth",
    ),
    Constant(
        "room_perimeter_coefficient",
        "formula",
        "x sqrt(SF)",
        SQUARE_PERIMETER,
        tuple(Target("perimeter", key) for key in PERIMETER_SPECS),
        "room perimeter = coefficient x sqrt(area) (baseboard, crown, wainscot, countertop)",
        published=Decimal("4.08"),
        published_source=(
            "2 x (1 + r) / sqrt(r): 4 a square room, 4.08 a 1.5:1 room, 4.17 at 1.8:1; no "
            "survey of room shapes (derived)"
        ),
    ),
)
BY_NAME: Final[dict[str, Constant]] = {c.name: c for c in CONSTANTS}


def published_set(group: str | None = None) -> dict[str, Decimal]:
    """Every constant that has a published value (of ``group``, or every group), at it."""
    return {
        c.name: c.published
        for c in CONSTANTS
        if c.published is not None and (group is None or c.group == group)
    }


GROUPS: Final = ("geometry", "typical_area", "formula")


@contextmanager
def override(values: Mapping[str, Decimal]) -> Iterator[None]:
    """Set the named constants for the block and restore them after it. The domain
    functions read their module's names at call time, so the override reaches
    ``process_assets``' own path; nothing is copied."""
    saved: list[tuple[Target, Any]] = []
    # In the registry's order, so the perimeter coefficient (last) scales the perimeter
    # specs after a spec constant (the wainscot height, the countertop depth) has set them.
    order = {c.name: i for i, c in enumerate(CONSTANTS)}
    try:
        for name, value in sorted(values.items(), key=lambda item: order[item[0]]):
            for target in BY_NAME[name].targets:
                if target.kind == "geometry":
                    saved.append((target, getattr(geometry, target.key)))
                    setattr(geometry, target.key, value)
                elif target.kind == "weight":
                    weights = room_guidance.ROOM_WEIGHTS_SF
                    saved.append((target, weights.get(target.key)))
                    weights[target.key] = value  # type: ignore[assignment]
                elif target.kind == "perimeter":
                    spec = takeoffs.QUANTITY_ESTIMATES[target.key]
                    saved.append((target, spec))
                    takeoffs.QUANTITY_ESTIMATES[target.key] = replace(
                        spec, per_sf=spec.per_sf * value / SQUARE_PERIMETER
                    )
                else:
                    spec = takeoffs.QUANTITY_ESTIMATES[target.key]
                    saved.append((target, spec))
                    assert target.attr is not None
                    changed: dict[str, Any] = {target.attr: value}
                    takeoffs.QUANTITY_ESTIMATES[target.key] = replace(spec, **changed)
        yield
    finally:
        for target, original in reversed(saved):
            if target.kind == "geometry":
                setattr(geometry, target.key, original)
            elif target.kind == "weight":
                if original is None:
                    room_guidance.ROOM_WEIGHTS_SF.pop(target.key, None)
                else:
                    room_guidance.ROOM_WEIGHTS_SF[target.key] = original
            else:  # spec, perimeter
                takeoffs.QUANTITY_ESTIMATES[target.key] = original


# --------------------------------------------------------------------------- the takeoff


@dataclass(frozen=True)
class RoomStub:
    id: object
    room_type: str
    floor: str | None


def _concealed(asset: Mapping[str, Any]) -> bool:
    inputs = asset.get("takeoff_inputs")
    return isinstance(inputs, dict) and bool(inputs.get("concealed"))


def _qty(value: Decimal) -> str:
    return str(value.quantize(QUANTITY_PLACES, rounding=ROUND_HALF_UP))


def retakeoff(
    document: Mapping[str, Any], *, property_class: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(the export with its measured quantities recomputed under the constants in
    effect, a summary)``. Count-based rows, rows on a typical area, rows measured once
    elsewhere (``measured_with_asset``) and the concealed rows are left as stored. A priced
    row's total is its stored unit cost times the new quantity (the pricing is linear in
    the quantity; ``evals.reprice_export`` prices it again from the cost table anyway). A
    room that today's rules size as typical with a missing input (an ambiguous room type)
    is written unpriced with the input named, as ``process_assets`` writes it."""
    rooms = [
        RoomStub(r["id"], str(r.get("room_type") or ""), r.get("floor")) for r in document["rooms"]
    ]
    read = measurements_from_fields(field_rows(document))
    if read.is_empty:
        raise ReplayError("the export has no extraction fields: nothing to measure from")
    takeoff = (document.get("summary") or {}).get("takeoff") or {}
    property_class = property_class or str(takeoff.get("property_class") or "residential_rental")
    areas = room_areas(read, rooms, property_class)
    quantities = property_quantities(read)

    out = json.loads(json.dumps(document))
    counts: dict[str, int] = {
        "room_measured": 0,
        "property_measured": 0,
        "measured_elsewhere": 0,
        "kept": 0,
        "now_unpriced": 0,
    }
    changed: list[dict[str, Any]] = []
    for asset in out["assets"]:
        if _concealed(asset):
            counts["kept"] += 1
            continue
        inputs = dict(asset.get("takeoff_inputs") or {})
        if inputs.get("measured_with_asset"):
            counts["measured_elsewhere"] += 1
            continue
        key = takeoff_key(str(inputs.get("component") or asset.get("label") or ""))
        old = Decimal(str(asset.get("quantity") or "0"))
        new: Decimal | None = None
        if inputs.get("quantity_source") == "appraisal_derived":
            derived = quantities.get(key)
            if derived is None:
                counts["kept"] += 1
                continue
            new = derived.quantity
            asset["formula"] = derived.formula
            inputs.update(derived.inputs)
            inputs["assumptions"] = list(derived.assumptions)
            counts["property_measured"] += 1
        elif (
            inputs.get("method") in ("area", "perimeter")
            and spec_for(key).method in ("area", "perimeter")
            and key not in geometry.NEEDS_MEASUREMENT
            and inputs.get("room_area_source") in MEASURED_SOURCES
            and asset.get("room_id") in areas
        ):
            # A site item the appraisal cannot measure (a driveway, a fence) stays as stored:
            # the takeoff writes it unpriced with the measurement named, never a room's area.
            planned = areas[asset["room_id"]]
            count = inputs.get("count")
            estimate = estimate_quantity(key, planned.area_sf, int(count) if count else 1)
            new = estimate.quantity
            asset["formula"] = estimate.formula
            inputs["room_area_sf"] = str(planned.area_sf)
            inputs["room_area_formula"] = planned.formula
            inputs["room_area_source"] = planned.source
            inputs.update(planned.inputs)
            if planned.missing is not None:
                # Today's rules do not price a measured item in a room with no sized area.
                inputs["missing_input"] = planned.missing
                asset["total"] = None
                asset["unit_cost"] = None
                reasons = [r for r in str(asset.get("review_reason") or "").split("; ") if r]
                reasons.append(f"input:{planned.missing}")
                asset["review_reason"] = "; ".join(dict.fromkeys(reasons))
                asset["needs_review"] = True
                counts["now_unpriced"] += 1
            counts["room_measured"] += 1
        else:
            counts["kept"] += 1
            continue
        asset["quantity"] = _qty(new)
        asset["takeoff_inputs"] = inputs
        if asset.get("total") is not None and asset.get("unit_cost") is not None:
            asset["total"] = str((Decimal(str(asset["unit_cost"])) * new).quantize(CENT))
        if new.quantize(QUANTITY_PLACES) != old.quantize(QUANTITY_PLACES):
            changed.append(
                {
                    "id": asset.get("id"),
                    "label": asset.get("label"),
                    "room_type": asset.get("room_type"),
                    "old": str(old),
                    "new": _qty(new),
                }
            )
    return out, {"counts": counts, "changed": changed}


def priced_summary(assets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The itemized rows' priced count, total and class shares (27.5-year rows counted
    with the 39-year building class, as the scorer compares them)."""
    by_bucket: dict[str, Decimal] = {}
    priced = 0
    for asset in assets:
        if _concealed(asset) or asset.get("total") is None or asset.get("bucket") == "land":
            continue
        priced += 1
        bucket = "y39" if asset.get("bucket") in BUILDING_BUCKETS else str(asset.get("bucket"))
        by_bucket[bucket] = by_bucket.get(bucket, Decimal(0)) + Decimal(str(asset["total"]))
    total = sum(by_bucket.values(), Decimal(0))
    shares = {
        b: float((by_bucket.get(b, Decimal(0)) / total * 100).quantize(CENT)) if total else 0.0
        for b in BUCKETS
    }
    return {"priced": priced, "total": str(total.quantize(CENT)), "shares": shares}


# --------------------------------------------------------------------------- section 35.2's dump


def document_from_dump(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[Mapping[str, Any]],
    *,
    revision: int,
    property_class: str,
) -> dict[str, Any]:
    """Section 35.2's private asset dump (one object per asset with its room, takeoff
    inputs and cost) as an export :func:`retakeoff` reads: rooms keyed by their name."""
    rooms: dict[str, dict[str, Any]] = {}
    assets: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("room_name")
        if name and name not in rooms:
            rooms[name] = {"id": name, "room_type": row.get("room_type"), "floor": row.get("floor")}
        inputs = dict(row.get("inputs") or {})
        assets.append(
            {
                "id": row.get("asset_id"),
                "label": row.get("component_label"),
                "room_id": name,
                "room_type": row.get("room_type"),
                "bucket": row.get("bucket"),
                "quantity": row.get("quantity"),
                "unit": row.get("unit"),
                "formula": row.get("formula"),
                "unit_cost": row.get("unit_cost"),
                "total": row.get("total"),
                "review_reason": row.get("review_reason"),
                "needs_review": row.get("needs_review"),
                "takeoff_inputs": inputs,
            }
        )
    extraction = [
        {
            "section": f.get("section"),
            "field": f.get("field"),
            "value_num": f.get("value_num"),
            "value_text": f.get("value_text"),
            "value_date": None if not f.get("value_date") else str(f["value_date"])[:10],
            "source": f.get("source"),
        }
        for f in fields
        if f.get("revision") == revision
    ]
    return {
        "format": "basis-export/2",
        "study_id": "dump",
        "revision": revision,
        "rooms": list(rooms.values()),
        "extraction": [{"fields": extraction}],
        "assets": assets,
        "summary": {"takeoff": {"property_class": property_class}},
    }


def _reasons(asset: Mapping[str, Any]) -> list[str]:
    return [r for r in str(asset.get("review_reason") or "").split("; ") if r]


def dump_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """Section 35.2's counts on a dump document: assets priced, missing a measurement
    (``COST_NEEDS_QUANTITY``), with no cost-book line (``COST_ROW_MISSING``), the priced
    total and the class shares."""
    assets = document["assets"]
    summary = priced_summary(assets)
    codes = [c for a in assets for c in _reasons(a)]
    summary["missing_measurement"] = sum(1 for c in codes if c == "cross:COST_NEEDS_QUANTITY")
    summary["no_cost_row"] = sum(1 for c in codes if c == "cross:COST_ROW_MISSING")
    summary["assets"] = len(assets)
    return summary


# --------------------------------------------------------------------------- the sweep


@dataclass(frozen=True)
class Setup:
    name: str
    constant: str | None  # None: today, or the published set
    value: Decimal | None
    values: dict[str, Decimal] = field(default_factory=dict)

    @property
    def factor(self) -> float | None:
        if self.constant is None or self.value is None:
            return None
        today = BY_NAME[self.constant].today
        return float(self.value / today) if today else None


def setups() -> list[Setup]:
    out = [Setup("today", None, None, {})]
    for constant in CONSTANTS:
        for name, value in constant.values():
            out.append(Setup(name, constant.name, value, {constant.name: value}))
    # Each group's published values together (the room weights only mean anything as a
    # set: a room's area is its weight over the sum), then every published value at once.
    for group in GROUPS:
        out.append(Setup(f"published {group}", None, None, published_set(group)))
    out.append(Setup("all published", None, None, published_set()))
    return out


@dataclass(frozen=True)
class Scored:
    """One setup on one export (``cell`` the structure replay and the scorer), or on section
    35.2's dump (``cell`` None: the dump has no structure model and no report rows, so only
    the itemized schedule is scored)."""

    export: str
    setup: str
    constant: str | None
    value: str | None
    factor: float | None
    runtime_ms: float
    recomputed: dict[str, int]
    changed_quantities: int
    quantities_equal_stored: bool
    priced: int
    priced_total: str
    priced_total_ratio: float  # against the today cell on the same export
    # The itemized rows' class shares before the structure model (27.5 with 39), and their
    # mean and worst absolute error against the engineer's shares, in points.
    itemized_shares: dict[str, float]
    itemized_error: float
    itemized_worst: float
    cell: Cell | None


def share_error(shares: Mapping[str, float], engineer: Mapping[str, float]) -> tuple[float, float]:
    """``(mean, worst)`` absolute difference in points over the four classes."""
    errors = [abs(shares.get(b, 0.0) - engineer.get(b, 0.0)) for b in BUCKETS]
    return sum(errors) / len(errors), max(errors)


def engineer_shares(report: EngineerReport) -> dict[str, float]:
    totals = {b: float(v) for b, v in report.bucket_totals.items()}
    total = sum(totals.values()) or 1.0
    merged: dict[str, float] = {}
    for b, v in totals.items():
        key = "y39" if b in BUILDING_BUCKETS else b
        merged[key] = merged.get(key, 0.0) + v / total * 100
    return merged


def _ratio(numerator: Decimal, denominator: Decimal) -> float:
    return float((numerator / denominator).quantize(Decimal("0.0001"))) if denominator else 0.0


def _scored(
    name: str,
    setup: Setup,
    *,
    started: float,
    changes: Mapping[str, Any],
    assets: Sequence[Mapping[str, Any]],
    engineer: Mapping[str, float],
    today_total: Decimal | None,
    cell: Cell | None,
) -> Scored:
    priced = priced_summary(assets)
    total = Decimal(priced["total"])
    mean, worst = share_error(priced["shares"], engineer)
    return Scored(
        export=name,
        setup=setup.name,
        constant=setup.constant,
        value=None if setup.value is None else str(setup.value),
        factor=setup.factor,
        runtime_ms=(time.perf_counter() - started) * 1000,
        recomputed=dict(changes["counts"]),
        changed_quantities=len(changes["changed"]),
        quantities_equal_stored=not changes["changed"],
        priced=priced["priced"],
        priced_total=priced["total"],
        priced_total_ratio=_ratio(total, today_total if today_total is not None else total),
        itemized_shares=priced["shares"],
        itemized_error=round(mean, 4),
        itemized_worst=round(worst, 4),
        cell=cell,
    )


def run_setup(
    name: str,
    document: dict[str, Any],
    setup: Setup,
    *,
    cost_table: CostTable | None,
    rows: E2ERows,
    report: EngineerReport,
    baseline: tuple[int, int],
    supplement: Mapping[int, YearIndex] | None,
    today_total: Decimal | None,
    engineer: Mapping[str, float] | None = None,
) -> Scored:
    """One setup on one export: the takeoff under the setup's constants, the repricing
    (installed cost with the general contractor's O&P, section 49's production pricing),
    the structure replay (production) and the scorer."""
    started = time.perf_counter()
    with override(setup.values):
        taken, changes = retakeoff(document)
    repriced, _ = reprice(
        taken,
        cost_table=cost_table,
        state=None,
        zip_code=None,
        supplement=supplement,
        cost_basis="installed",
        overhead="pro_rata",
    )
    cell = run_cell(
        name,
        repriced,
        PRODUCTION,
        cost_table=cost_table,
        rows=rows,
        report=report,
        baseline=baseline,
        supplement=supplement,
        label=setup.name,
    )
    return _scored(
        name,
        setup,
        started=started,
        changes=changes,
        assets=repriced["assets"],
        engineer=engineer if engineer is not None else engineer_shares(report),
        today_total=today_total,
        cell=cell,
    )


def run_dump_setup(
    document: dict[str, Any],
    setup: Setup,
    *,
    engineer: Mapping[str, float],
    today_total: Decimal | None,
    name: str = "35.2 dump",
) -> Scored:
    """One setup on section 35.2's dump: the takeoff under the setup's constants, each
    re-measured row priced at its stored unit cost (35.2's pricing; the dump carries no
    cost year to reprice from), the itemized shares scored against the engineer's."""
    started = time.perf_counter()
    with override(setup.values):
        taken, changes = retakeoff(document)
    return _scored(
        name,
        setup,
        started=started,
        changes=changes,
        assets=taken["assets"],
        engineer=engineer,
        today_total=today_total,
        cell=None,
    )


@dataclass(frozen=True)
class Sensitivity:
    constant: str
    group: str
    today: str
    range_tested: str
    # Points of the scored schedule's mean class-share error per +100 % of the constant (a
    # central difference over the 75 and 125 % cells, or the named slope pair), averaged
    # over the exports; None when the slope pair is not in the cells.
    slope_points_per_100pct: float | None
    slope_by_export: dict[str, float]
    # The same slope on the itemized schedule before the structure model (the exports) and
    # on section 35.2's dump.
    itemized_slope_per_100pct: float | None
    dump_slope_per_100pct: float | None
    # The largest move of the scored mean class-share error over the range, in points.
    max_abs_error_move: float
    # The largest move of the itemized priced total over the range, in percent (exports).
    max_abs_total_move_pct: float
    exercised: bool  # False when no cell moved a quantity on any export or the dump


def _index(cells: Sequence[Scored]) -> dict[tuple[str, str], Scored]:
    return {(c.export, c.setup): c for c in cells}


def _final_error(scored: Scored) -> float | None:
    return None if scored.cell is None else scored.cell.mean_bucket_error_pct


def _itemized_error(scored: Scored) -> float | None:
    return scored.itemized_error


def _slope(
    index: Mapping[tuple[str, str], Scored],
    export: str,
    constant: Constant,
    metric: Callable[[Scored], float | None],
) -> float | None:
    """Points of ``metric`` per +100 % of the constant on one export."""
    if constant.linear:
        low = index.get((export, f"{constant.name} x0.75"))
        high = index.get((export, f"{constant.name} x1.25"))
        span = 0.5
    else:
        assert constant.slope_pair is not None
        first, second = constant.slope_pair
        low = (
            index.get((export, "today"))
            if first == "today"
            else index.get((export, f"{constant.name} {first}"))
        )
        high = index.get((export, f"{constant.name} {second}"))
        values = dict(constant.alternatives)
        v_low = constant.today if first == "today" else values[first]
        span = float((values[second] - v_low) / constant.today)
    if low is None or high is None or not span:
        return None
    m_low, m_high = metric(low), metric(high)
    if m_low is None or m_high is None:
        return None
    return (m_high - m_low) / span


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 4)


def sensitivity(cells: Sequence[Scored], dump_cells: Sequence[Scored] = ()) -> list[Sensitivity]:
    """One row per constant, ranked by the absolute slope of the scored error, then the
    itemized slope, then the largest move."""
    index = _index(cells)
    dump_index = _index(dump_cells)
    exports = list(dict.fromkeys(c.export for c in cells))
    dumps = list(dict.fromkeys(c.export for c in dump_cells))
    out: list[Sensitivity] = []
    for constant in CONSTANTS:
        names = [n for n, _ in constant.values()]
        err_moves: list[float] = []
        total_moves: list[float] = []
        slopes: dict[str, float] = {}
        itemized: list[float] = []
        exercised = any(
            (d, n) in dump_index and dump_index[(d, n)].changed_quantities > 0
            for d in dumps
            for n in names
        )
        for export in exports:
            today = index.get((export, "today"))
            base = None if today is None else _final_error(today)
            for n in names:
                scored = index.get((export, n))
                if scored is None:
                    continue
                exercised = exercised or scored.changed_quantities > 0
                total_moves.append(abs(scored.priced_total_ratio - 1.0) * 100)
                error = _final_error(scored)
                if base is not None and error is not None:
                    err_moves.append(abs(error - base))
            slope = _slope(index, export, constant, _final_error)
            if slope is not None:
                slopes[export] = round(slope, 4)
            slope = _slope(index, export, constant, _itemized_error)
            if slope is not None:
                itemized.append(slope)
        dump_slopes = [
            s for d in dumps if (s := _slope(dump_index, d, constant, _itemized_error)) is not None
        ]
        if constant.linear:
            tested = "50, 75, 125, 150 % of today"
        else:
            tested = ", ".join(label for label, _ in constant.alternatives)
        for label, value in constant.extra:
            tested += f"; {label} ({value})"
        if constant.published is not None and constant.published != constant.today:
            tested += f"; published {constant.published}"
        out.append(
            Sensitivity(
                constant=constant.name,
                group=constant.group,
                today=_plain(constant.today),
                range_tested=tested,
                slope_points_per_100pct=_mean(list(slopes.values())),
                slope_by_export=slopes,
                itemized_slope_per_100pct=_mean(itemized),
                dump_slope_per_100pct=_mean(dump_slopes),
                max_abs_error_move=round(max(err_moves), 4) if err_moves else 0.0,
                max_abs_total_move_pct=round(max(total_moves), 3) if total_moves else 0.0,
                exercised=exercised,
            )
        )
    out.sort(
        key=lambda s: (
            -(abs(s.slope_points_per_100pct) if s.slope_points_per_100pct is not None else 0.0),
            -(abs(s.itemized_slope_per_100pct) if s.itemized_slope_per_100pct is not None else 0.0),
            -s.max_abs_error_move,
            s.constant,
        )
    )
    return out


# --------------------------------------------------------------------------- rendering


def _plain(value: Decimal) -> str:
    """A constant as a reader writes it: 2.0833 for 25/12, 1.5 for 1.5."""
    return format(value.quantize(QUANTITY_PLACES).normalize(), "f")


def _num(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def _shares(shares: Mapping[str, float] | None) -> str:
    shares = shares or {}
    return " / ".join(_num(shares.get(b), 1) for b in BUCKETS)


def render_markdown(
    cells: Sequence[Scored],
    ranking: Sequence[Sensitivity],
    engineer: Mapping[str, float],
    dump: Mapping[str, Any] | None,
    dump_cells: Sequence[Scored] = (),
) -> str:
    lines = [
        "# Takeoff constants sweep",
        "",
        "Engineer's class shares (%): "
        + ", ".join(f"{b} {engineer.get(b, 0.0):.1f}" for b in BUCKETS)
        + ". Cost per cell: $0 (deterministic, no model call); runtime is the takeoff, the "
        "repricing, the structure replay and the scorer. 'Scored' is the schedule after the "
        "structure model (what the report shows); 'itemized' is the priced rows before it.",
        "",
    ]
    for export in dict.fromkeys(c.export for c in cells):
        lines += [
            f"## {export}",
            "",
            "| setup | constant | value | x today | quantities moved | priced | priced total "
            "(today = 1.00) | itemized shares y5 / y7 / y15 / y39 | itemized class error | "
            "scored shares y5 / y7 / y15 / y39 | scored mean / worst class error | TVD | "
            "gap % | status | ms |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in cells:
            if c.export != export or c.cell is None:
                continue
            errors = c.cell.bucket_error_pct or {}
            worst = max(errors.values()) if errors else None
            lines.append(
                f"| {c.setup} | {c.constant or '-'} | {c.value or '-'} | "
                f"{_num(c.factor)} | {c.changed_quantities} | {c.priced} | "
                f"{c.priced_total_ratio:.4f} | {_shares(c.itemized_shares)} | "
                f"{c.itemized_error:.2f} | {_shares(c.cell.bucket_share_pct)} | "
                f"{_num(c.cell.mean_bucket_error_pct)} / {_num(worst, 1)} | "
                f"{_num(c.cell.tvd_pct, 1)} | {_num(c.cell.gap_share_pct, 1)} | "
                f"{c.cell.status} | {c.runtime_ms:.0f} |"
            )
        lines.append("")
    if dump_cells:
        lines += [
            "## Section 35.2's dump (itemized only, stored unit costs)",
            "",
            "| setup | constant | value | x today | quantities moved | priced | priced total "
            "(today = 1.00) | shares y5 / y7 / y15 / y39 | mean / worst class error | ms |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in dump_cells:
            lines.append(
                f"| {c.setup} | {c.constant or '-'} | {c.value or '-'} | {_num(c.factor)} | "
                f"{c.changed_quantities} | {c.priced} | {c.priced_total_ratio:.4f} | "
                f"{_shares(c.itemized_shares)} | {c.itemized_error:.2f} / "
                f"{c.itemized_worst:.1f} | {c.runtime_ms:.0f} |"
            )
        lines.append("")
    exports = list(dict.fromkeys(c.export for c in cells))
    lines += [
        "## Sensitivity (ranked by points of scored mean class-share error per +100 % of the "
        "constant)",
        "",
        "| rank | constant | group | today | range tested | scored slope (mean; "
        + " / ".join(exports)
        + ") | itemized slope | dump slope | largest scored error move (points) | "
        "largest priced-total move (%) | exercised |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(ranking, 1):
        per = " / ".join(_num(s.slope_by_export.get(e), 3) for e in exports)
        lines.append(
            f"| {i} | {s.constant} | {s.group} | {s.today} | {s.range_tested} | "
            f"{_num(s.slope_points_per_100pct, 3)} ({per}) | "
            f"{_num(s.itemized_slope_per_100pct, 3)} | {_num(s.dump_slope_per_100pct, 3)} | "
            f"{s.max_abs_error_move:.3f} | {s.max_abs_total_move_pct:.2f} | "
            f"{'yes' if s.exercised else 'no'} |"
        )
    moved = [c for c in cells if c.setup == "today" and not c.quantities_equal_stored]
    lines += [
        "",
        "Regression (today's constants reproduce the stored quantities): "
        + (
            "every export unchanged."
            if not moved
            else "MOVED on " + ", ".join(f"{c.export} ({c.changed_quantities})" for c in moved)
        ),
    ]
    if dump is not None:
        lines += ["", "## Section 35.2's dump replay", "", "```", json.dumps(dump, indent=1), "```"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.takeoff_constants", description=__doc__)
    parser.add_argument("exports", type=Path, nargs="*", help="rev-<n>.json files")
    parser.add_argument("--cost-table", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, default=None, help="stored index values")
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--dump", type=Path, default=None, help="section 35.2's asset dump")
    parser.add_argument("--fields", type=Path, default=None, help="the dump's fields file")
    parser.add_argument("--revision", type=int, default=None, help="the dump's revision")
    parser.add_argument("--dump-property-class", default="residential_rental")
    return parser.parse_args(argv)


def load_dump(args: argparse.Namespace, run: record.EvalRun) -> dict[str, Any]:
    assert args.dump is not None and args.fields is not None and args.revision is not None
    rows = json.loads(args.dump.read_text(encoding="utf-8"))
    fields = json.loads(args.fields.read_text(encoding="utf-8"))
    run.input("dump", args.dump)
    run.input("fields", args.fields)
    return document_from_dump(
        rows, fields, revision=args.revision, property_class=args.dump_property_class
    )


def replay_dump(document: Mapping[str, Any], run: record.EvalRun) -> dict[str, Any]:
    """Section 35.2's dump under today's constants: the stored numbers, the recomputed
    ones and the quantities that differ."""
    stored = dump_summary(document)
    taken, changes = retakeoff(document)
    recomputed = dump_summary(taken)
    for change in changes["changed"]:
        run.case(
            f"dump:{change['id']}",
            "failed",
            kind="dump_quantity",
            detail={"label": change["label"], "old": change["old"], "new": change["new"]},
        )
    equal = (
        changes["counts"]["room_measured"]
        + changes["counts"]["property_measured"]
        - len(changes["changed"])
    )
    run.case(
        "dump:quantities",
        "passed" if not changes["changed"] else "failed",
        kind="dump",
        detail={"equal": equal, "differ": len(changes["changed"])},
    )
    return {
        "stored": stored,
        "recomputed": recomputed,
        "recomputed_counts": changes["counts"],
        "quantities_equal": equal,
        "quantities_differ": changes["changed"],
    }


def _case(run: record.EvalRun, scored: Scored, *, regression: bool) -> None:
    ok = (scored.cell is None or scored.cell.sourced) and (
        not regression or scored.quantities_equal_stored
    )
    run.case(
        f"{scored.export}:{scored.setup}",
        "passed" if ok else "failed",
        kind="setup",
        detail={
            "constant": scored.constant,
            "value": scored.value,
            "quantities_moved": scored.changed_quantities,
            "itemized_error": scored.itemized_error,
            "mean_bucket_error_pct": None
            if scored.cell is None
            else scored.cell.mean_bucket_error_pct,
            "status": None if scored.cell is None else scored.cell.status,
        },
        seconds=scored.runtime_ms / 1000,
    )


def _row(scored: Scored) -> dict[str, Any]:
    out = {k: v for k, v in scored.__dict__.items() if k != "cell"}
    out["cell"] = None if scored.cell is None else scored.cell.__dict__
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        rows_path = default_labels_path("e2e_rows_v1.json", args.labels_dir)
        report_path = default_labels_path("engineer_report_v1.json", args.labels_dir)
        rows = load_rows(rows_path)
        report = load_report(report_path)
        run.dataset(rows_path)
        run.dataset(report_path)
        engineer = engineer_shares(report)
        table = load_cost_table(str(args.cost_table))
        run.input("cost_table", args.cost_table)
        supplement = supplement_for(None if args.supplement is None else str(args.supplement))
        if args.supplement is not None:
            run.input("supplement", args.supplement)
        cells: list[Scored] = []
        for path in args.exports:
            document = json.loads(path.read_text(encoding="utf-8"))
            name = path.parent.parent.name if path.parent.name == "export" else path.stem
            run.input(name, path)
            baseline = baseline_scores(document, name, rows, report)
            today_total: Decimal | None = None
            for setup in setups():
                scored = run_setup(
                    name,
                    document,
                    setup,
                    cost_table=table,
                    rows=rows,
                    report=report,
                    baseline=baseline,
                    supplement=supplement,
                    today_total=today_total,
                    engineer=engineer,
                )
                if setup.name == "today":
                    today_total = Decimal(scored.priced_total)
                cells.append(scored)
                _case(run, scored, regression=setup.name == "today")
        dump: dict[str, Any] | None = None
        dump_cells: list[Scored] = []
        if args.dump is not None:
            dump_document = load_dump(args, run)
            dump = replay_dump(dump_document, run)
            dump_total: Decimal | None = None
            for setup in setups():
                scored = run_dump_setup(
                    dump_document, setup, engineer=engineer, today_total=dump_total
                )
                if setup.name == "today":
                    dump_total = Decimal(scored.priced_total)
                dump_cells.append(scored)
                _case(run, scored, regression=setup.name == "today")
        ranking = sensitivity(cells, dump_cells)
        text = render_markdown(cells, ranking, engineer, dump, dump_cells)
        (args.out / "sweep.md").write_text(text, encoding="utf-8")
        payload = {
            "constants": [
                {
                    "name": c.name,
                    "group": c.group,
                    "unit": c.unit,
                    "today": _plain(c.today),
                    "published": None if c.published is None else _plain(c.published),
                    "published_source": c.published_source,
                    "extra": [[label, _plain(v)] for label, v in c.extra],
                    "what": c.what,
                }
                for c in CONSTANTS
            ],
            "cells": [_row(c) for c in cells],
            "dump_cells": [_row(c) for c in dump_cells],
            "sensitivity": [s.__dict__ for s in ranking],
            "dump": dump,
            "engineer_shares_pct": engineer,
        }
        (args.out / "sweep.json").write_text(
            json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8"
        )
        print(text)
        summary = {
            "exports": [c.export for c in cells if c.setup == "today"],
            "setups": len(setups()),
            "cells": len(cells),
            "dump_cells": len(dump_cells),
            "regression_ok": all(
                c.quantities_equal_stored for c in [*cells, *dump_cells] if c.setup == "today"
            ),
            "ranking": [s.constant for s in ranking],
            "dump": None if dump is None else {k: dump[k] for k in ("stored", "recomputed")},
            "cost_usd": 0,
        }
        run.finish(summary)
    except (ReplayError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0


__all__ = [
    "BY_NAME",
    "CONSTANTS",
    "Constant",
    "ReplayError",
    "Scored",
    "Sensitivity",
    "Setup",
    "document_from_dump",
    "dump_summary",
    "engineer_shares",
    "override",
    "priced_summary",
    "published_set",
    "render_markdown",
    "retakeoff",
    "run_dump_setup",
    "run_setup",
    "sensitivity",
    "setups",
    "share_error",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
