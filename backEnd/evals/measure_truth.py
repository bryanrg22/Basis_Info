"""The measuring ground truth: real houses with known exterior dimensions (EVAL_REPORT 103).

Each house is one JSON file in ``evals/fixtures/measure_truth/<id>.json`` transcribed from the
Historic American Buildings Survey's measured drawings (Library of Congress, public domain:
"No known restrictions on images made by the U.S. Government"). Only public-domain facts are
kept: the survey's id and link, the drawing date, the public address and the building's centre,
and the dimensions as printed (or scaled off the drawing, said so, with the scale).

The schema (``measure_truth/v1``):

* ``id``, ``name``, ``loc_url``, ``survey_number``, ``drawing_date``, ``address``;
* ``location``: the building's centre (``lat``, ``lon``), ``how`` it was placed and on what;
* ``standing``: what the current public aerial shows against the drawing (``status``
  ``unchanged`` or ``changed``, ``source``, ``year``, ``note``);
* ``sheets``: sheet number to what is on it;
* ``stories``, ``roof_shape``;
* ``plan``: the ground floor's exterior wall outline as an ordered **chain** of edges, each a
  direction on the sheet (``N`` up, ``E`` right, ``S`` down, ``W`` left, whatever the
  drawing's north arrow says; or ``bearing_deg`` clockwise from sheet-up) and a
  :class:`Length`; ``overall`` the printed overall dimensions (``ew`` across the sheet and
  ``ns`` up the sheet); ``excluded`` what the chain leaves out on purpose (open porches,
  stoops, steps, bays drawn but not enclosed); ``north`` where the drawing's arrow points;
* ``floors``: each above-grade floor counted in the gross living area, its chain
  (``same_as_ground`` or its own) and whether it is finished;
* ``heights``: the eave (top of gutter, or the eave line) and the ridge above grade, each a
  :class:`Height` with the printed text, the datum and the elevation it was read on;
  ``readings`` keeps every height read, used or not;
* ``roof``: ``overhang`` (a :class:`Length`) where printed or scaled, ``eaves`` (``all`` for a
  hip roof, else the plan sides that carry an eave), ``outline`` when the roof plan is
  dimensioned;
* ``porches``: open porches as rectangles (not in the footprint);
* ``site``: site items (walk, drive, fence, patio) scaled off the site plan, always ``scaled``;
* ``passes``: the two independent transcriptions and every disagreement between them;
* ``flags``: every check the file does not pass, declared (``code`` and ``note``); a check that
  fails and is not declared is a test failure, so nothing is forced to reconcile.

A :class:`Length` is ``text`` (the printed string, ``32'-4"``) and ``ft``; for ``printed`` the
two must agree (:func:`parse_ft`), ``scaled`` gives the scale, ``derived`` says how. Everything
derived here (perimeter, area, the bounding box) is plain arithmetic on the transcribed values.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA: Final = "measure_truth/v1"
FIXTURES: Final = Path(__file__).parent / "fixtures" / "measure_truth"
# A printed length must equal its text to this (feet): an inch fraction of 1/64 is 0.0013 ft.
TEXT_TOLERANCE_FT: Final = 0.002
# The chain must close to this (feet): a quarter inch per edge on a ten-edge chain is 0.2 ft,
# and HABS chains are printed to the 1/8 inch at best.
CLOSE_TOLERANCE_FT: Final = 0.10
# The chain's extent must match the printed overall dimensions to this (feet).
OVERALL_TOLERANCE_FT: Final = 0.10
DIRECTIONS: Final[dict[str, float]] = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}

How = Literal["printed", "scaled", "derived"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


_FT_IN: Final = re.compile(
    r"""^\s*(?:(?P<ft>-?\d+)\s*'\s*-?\s*)?(?:(?P<in>\d+)?\s*(?:(?P<num>\d+)\s*/\s*(?P<den>\d+))?\s*"?)?\s*$"""
)


def parse_ft(text: str) -> float:
    """Feet from an architect's dimension: ``32'-4"``, ``22'-5 3/8"``, ``0'-0"``, ``8"``,
    ``28'-0 5/8"``, ``96'``. Raises ``ValueError`` on anything else."""
    cleaned = text.replace("′", "'").replace("″", '"').replace("''", '"').strip()
    found = _FT_IN.match(cleaned)
    if "'" not in cleaned and '"' not in cleaned:
        raise ValueError(f"a dimension names its unit (' or \"): {text!r}")
    if not cleaned or found is None or not any(found.group(g) for g in ("ft", "in", "num")):
        raise ValueError(f"not a feet-and-inches dimension: {text!r}")
    feet = int(found["ft"]) if found["ft"] else 0
    inches = float(found["in"]) if found["in"] else 0.0
    if found["num"]:
        den = int(found["den"])
        if den == 0:
            raise ValueError(f"zero denominator in {text!r}")
        inches += int(found["num"]) / den
    sign = -1.0 if feet < 0 or cleaned.startswith("-") else 1.0
    return sign * (abs(feet) + inches / 12.0)


class Length(_Model):
    """A length (or a height) as transcribed."""

    ft: float
    text: str | None = None
    how: How = "printed"
    sheet: str | None = None
    scale: str | None = None  # for scaled: the drawing's scale, e.g. 1/4" = 1'-0"
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Length:
        if self.how == "printed":
            if self.text is None:
                raise ValueError("a printed length needs its printed text")
            if abs(parse_ft(self.text) - self.ft) > TEXT_TOLERANCE_FT:
                raise ValueError(f"{self.text!r} is {parse_ft(self.text):.4f} ft, not {self.ft}")
        if self.how == "scaled" and not self.scale:
            raise ValueError("a scaled length needs the scale it was scaled at")
        if self.how == "derived" and not self.note:
            raise ValueError("a derived length needs the note saying how")
        return self


class Edge(_Model):
    """One exterior wall run, walked with the building on the right (clockwise on the plan)
    or on the left, as long as the whole chain goes one way."""

    dir: Literal["N", "E", "S", "W"] | None = None
    bearing_deg: float | None = None
    length: Length
    label: str | None = None  # what the run is ("east wall of the rear wing", "bay, 45 deg")

    @model_validator(mode="after")
    def _one_direction(self) -> Edge:
        if (self.dir is None) == (self.bearing_deg is None):
            raise ValueError("an edge needs exactly one of dir and bearing_deg")
        return self

    @property
    def bearing(self) -> float:
        return DIRECTIONS[self.dir] if self.dir is not None else float(self.bearing_deg or 0.0)


class Overall(_Model):
    ew: Length  # the extent across the sheet
    ns: Length  # the extent up the sheet


class Plan(_Model):
    sheet: str
    scale: str
    chain: list[Edge] = Field(min_length=3)
    overall: Overall | None = None
    excluded: list[str] = Field(default_factory=list)
    north: str | None = None  # where the drawing's north arrow points on the sheet
    note: str | None = None


class Floor(_Model):
    level: str  # first, second, attic
    finished: bool = True
    same_as_ground: bool = False
    chain: list[Edge] | None = None
    sheet: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Floor:
        if self.same_as_ground == (self.chain is not None):
            raise ValueError("a floor is either same_as_ground or has its own chain")
        return self


class Height(_Model):
    """A height above grade, from a printed level and its datum."""

    above_grade_ft: float
    text: str | None = None  # the printed level, e.g. 22'-9"
    datum_text: str | None = None  # the printed grade level on the same datum, e.g. 0'-0"
    how: How = "printed"
    sheet: str | None = None
    elevation: str | None = None  # which elevation it was read on
    scale: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Height:
        if self.how == "printed":
            if self.text is None or self.datum_text is None:
                raise ValueError("a printed height needs the level and the grade on its datum")
            got = parse_ft(self.text) - parse_ft(self.datum_text)
            if abs(got - self.above_grade_ft) > TEXT_TOLERANCE_FT:
                raise ValueError(
                    f"{self.text!r} over {self.datum_text!r} is {got:.4f} ft, "
                    f"not {self.above_grade_ft}"
                )
        if self.how == "scaled" and not self.scale:
            raise ValueError("a scaled height needs the scale")
        if self.how == "derived" and not self.note:
            raise ValueError("a derived height needs the note saying how")
        return self


class HeightReading(_Model):
    what: str  # eave, top of gutter, ridge, roof top, first floor, ...
    text: str
    datum_text: str | None = None
    sheet: str | None = None
    elevation: str | None = None
    note: str | None = None


class Heights(_Model):
    eave: Height | None = None
    ridge: Height | None = None
    readings: list[HeightReading] = Field(default_factory=list)


class Roof(_Model):
    overhang: Length | None = None
    eaves: Literal["all"] | list[Literal["N", "E", "S", "W"]] | None = None
    outline: list[Edge] | None = None
    # True only where the derived roof truths hold: one roof of one pitch over the outline,
    # the overhang the same all round (a plain hip or a plain gable). A roof that also covers
    # a porch, mixes pitches or heights, or has uneven eaves stays False, and the house has no
    # roof-area or gutter truth (unless ``outline``, the printed roof plan, gives the plan).
    derive: bool = False
    derive_note: str | None = None
    note: str | None = None


class Rect(_Model):
    name: str
    a: Length
    b: Length
    note: str | None = None


class SiteItem(_Model):
    item: Literal["walk", "drive", "fence", "patio", "parking", "wall", "other"]
    name: str
    length: Length
    width: Length | None = None  # none for a fence or a line
    note: str | None = None

    @model_validator(mode="after")
    def _scaled(self) -> SiteItem:
        for part in (self.length, self.width):
            if part is not None and part.how == "printed":
                continue
            if part is not None and part.how != "scaled":
                raise ValueError("a site item's sizes are printed or scaled off the drawing")
        return self


class Location(_Model):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    how: str


class Standing(_Model):
    status: Literal["unchanged", "changed", "unknown"]
    source: str
    year: int | None = None
    note: str | None = None


class Disagreement(_Model):
    what: str
    pass_a: str
    pass_b: str
    resolved: str  # the value kept and why


class Passes(_Model):
    a: str  # who or what read it, and when
    b: str
    disagreements: list[Disagreement] = Field(default_factory=list)


class Flag(_Model):
    code: str
    note: str


class House(_Model):
    schema_: Literal["measure_truth/v1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z]{2}\d{4}$")
    name: str
    loc_url: str
    survey_number: str
    drawing_date: str
    address: str
    location: Location
    standing: Standing
    sheets: dict[str, str]
    stories: float = Field(gt=0, le=4)
    roof_shape: str
    plan: Plan
    floors: list[Floor] = Field(min_length=1)
    heights: Heights
    roof: Roof = Roof()
    porches: list[Rect] = Field(default_factory=list)
    site: list[SiteItem] = Field(default_factory=list)
    passes: Passes
    flags: list[Flag] = Field(default_factory=list)
    note: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# ------------------------------------------------------------------ geometry


def vertices(chain: Sequence[Edge]) -> list[tuple[float, float]]:
    """The chain's corners in plan feet (x east, y north), starting at the origin; the last
    corner is where the chain ends (the origin again when it closes)."""
    x = y = 0.0
    out = [(x, y)]
    for edge in chain:
        b = math.radians(edge.bearing)
        x += edge.length.ft * math.sin(b)
        y += edge.length.ft * math.cos(b)
        out.append((x, y))
    return out


def closure_ft(chain: Sequence[Edge]) -> float:
    """How far the chain's end is from its start (0 for a closed outline)."""
    end = vertices(chain)[-1]
    return math.hypot(end[0], end[1])


def perimeter_ft(chain: Sequence[Edge]) -> float:
    return sum(e.length.ft for e in chain)


def area_sf(chain: Sequence[Edge]) -> float:
    """The shoelace area of the outline (absolute; the chain may run either way)."""
    pts = vertices(chain)[:-1]
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1], strict=True):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def extent_ft(chain: Sequence[Edge]) -> tuple[float, float]:
    """The outline's east-west and north-south extent."""
    pts = vertices(chain)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def convex_minus_concave(chain: Sequence[Edge]) -> int:
    """For a closed rectilinear outline: convex corners less concave ones (always 4). Kept as
    a check that an outline is a simple rectilinear ring."""
    turns = 0
    n = len(chain)
    for i in range(n):
        a, b = chain[i].bearing, chain[(i + 1) % n].bearing
        d = (b - a + 540.0) % 360.0 - 180.0
        if abs(abs(d) - 90.0) > 1e-6:
            raise ValueError("not a rectilinear outline")
        turns += 1 if d > 0 else -1
    return abs(turns)


def is_rectilinear(chain: Sequence[Edge]) -> bool:
    return all(e.dir is not None for e in chain)


def floor_chain(house: House, floor: Floor) -> list[Edge]:
    return house.plan.chain if floor.same_as_ground else list(floor.chain or [])


def gla_sf(house: House) -> float:
    """The gross living area the plan gives: the finished above-grade floors' exterior outline
    areas summed (ANSI Z765's measure, from the outside of the exterior walls)."""
    return sum(area_sf(floor_chain(house, f)) for f in house.floors if f.finished)


# ------------------------------------------------------------------ checks


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    detail: str


CHECK_CODES: Final = (
    "chain_open",  # the ground chain does not close within CLOSE_TOLERANCE_FT
    "overall_mismatch",  # the chain's extent differs from the printed overall
    "floor_chain_open",  # an upper floor's own chain does not close
    "no_overall",  # no printed overall dimensions to check the chain against
    "no_eave",  # no eave height
    "no_ridge",  # no ridge height
)


def check(house: House) -> list[Finding]:
    """Every self-check the house does not pass (an empty list for a clean house)."""
    out: list[Finding] = []
    gap = closure_ft(house.plan.chain)
    if gap > CLOSE_TOLERANCE_FT:
        out.append(Finding("chain_open", f"the ground chain ends {gap:.3f} ft from its start"))
    if house.plan.overall is None:
        out.append(Finding("no_overall", "no printed overall dimensions"))
    else:
        ew, ns = extent_ft(house.plan.chain)
        dx = ew - house.plan.overall.ew.ft
        dy = ns - house.plan.overall.ns.ft
        if abs(dx) > OVERALL_TOLERANCE_FT or abs(dy) > OVERALL_TOLERANCE_FT:
            out.append(
                Finding(
                    "overall_mismatch",
                    f"chain extent {ew:.3f} x {ns:.3f} ft against overall "
                    f"{house.plan.overall.ew.ft:.3f} x {house.plan.overall.ns.ft:.3f} "
                    f"(differences {dx:+.3f}, {dy:+.3f})",
                )
            )
    for floor in house.floors:
        if floor.chain is not None and closure_ft(floor.chain) > CLOSE_TOLERANCE_FT:
            out.append(
                Finding(
                    "floor_chain_open",
                    f"{floor.level} floor chain ends {closure_ft(floor.chain):.3f} ft from start",
                )
            )
    if house.heights.eave is None:
        out.append(Finding("no_eave", "no eave height"))
    if house.heights.ridge is None:
        out.append(Finding("no_ridge", "no ridge height"))
    return out


def undeclared(house: House) -> list[Finding]:
    """The findings the file does not declare in ``flags`` (a test holds this empty)."""
    declared = {f.code for f in house.flags}
    return [f for f in check(house) if f.code not in declared]


def load(path: Path) -> House:
    return House.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_all(directory: Path = FIXTURES) -> list[House]:
    return [load(p) for p in sorted(directory.glob("*.json"))]


# ------------------------------------------------------------------ the quantities scored


@dataclass(frozen=True, slots=True)
class Truth:
    """One quantity's true value for a house, and how firm it is."""

    quantity: str
    value: float
    unit: str
    tier: Literal["printed", "derived", "scaled"]
    basis: str


def _overhang(house: House) -> float | None:
    return None if house.roof.overhang is None else house.roof.overhang.ft


def truths(house: House) -> dict[str, Truth]:
    """The quantities a method is scored on, from the transcription. ``printed``: arithmetic on
    printed dimensions only; ``derived``: printed values plus a stated geometric assumption;
    ``scaled``: a value scaled off the drawing."""
    chain = house.plan.chain
    # Printed: every edge printed or arithmetic on printed strings (a sum, a difference, a
    # closure); scaled: any edge scaled off the drawing.
    tier_plan: Literal["printed", "derived", "scaled"] = (
        "scaled" if any(e.length.how == "scaled" for e in chain) else "printed"
    )
    out: dict[str, Truth] = {
        "footprint_sf": Truth(
            "footprint_sf",
            area_sf(chain),
            "SF",
            tier_plan,
            "the ground floor's exterior wall outline, shoelace area of the chain",
        ),
        "perimeter_ft": Truth(
            "perimeter_ft", perimeter_ft(chain), "LF", tier_plan, "sum of the chain's edges"
        ),
        "long_side_ft": Truth(
            "long_side_ft",
            max(extent_ft(chain)),
            "LF",
            tier_plan,
            "the outline's longer extent",
        ),
        "gla_sf": Truth(
            "gla_sf",
            gla_sf(house),
            "SF",
            "printed"
            if tier_plan == "printed"
            and all(
                f.same_as_ground or all(e.length.how != "scaled" for e in (f.chain or []))
                for f in house.floors
            )
            else "scaled",
            "finished above-grade floors' exterior outlines summed",
        ),
    }
    eave = house.heights.eave
    ridge = house.heights.ridge
    if eave is not None:
        out["eave_height_ft"] = Truth(
            "eave_height_ft",
            eave.above_grade_ft,
            "LF",
            "printed" if eave.how == "printed" else "scaled",
            f"eave above grade, {eave.elevation or 'elevation'} sheet {eave.sheet}",
        )
        out["wall_area_gross_sf"] = Truth(
            "wall_area_gross_sf",
            perimeter_ft(chain) * eave.above_grade_ft,
            "SF",
            "derived",
            "ground perimeter x eave height above grade (one eave height for the whole "
            "building; gable triangles and openings not counted)",
        )
    if ridge is not None:
        out["ridge_height_ft"] = Truth(
            "ridge_height_ft",
            ridge.above_grade_ft,
            "LF",
            "printed" if ridge.how == "printed" else "scaled",
            f"ridge above grade, {ridge.elevation or 'elevation'} sheet {ridge.sheet}",
        )
    d = _overhang(house)
    roof = house.roof
    plan_area: float | None = None
    gutter: float | None = None
    if roof.outline is not None and closure_ft(roof.outline) <= CLOSE_TOLERANCE_FT:
        plan_area = area_sf(roof.outline)
        out["roof_plan_sf"] = Truth(
            "roof_plan_sf",
            plan_area,
            "SF",
            "printed" if all(e.length.how != "scaled" for e in roof.outline) else "derived",
            "the dimensioned roof plan's outline, shoelace area",
        )
        if roof.eaves == "all":
            gutter = perimeter_ft(roof.outline)
    elif (
        roof.derive
        and d is not None
        and is_rectilinear(chain)
        and closure_ft(chain) <= CLOSE_TOLERANCE_FT
    ):
        n = convex_minus_concave(chain)
        plan_area = area_sf(chain) + perimeter_ft(chain) * d + n * d * d
        out["roof_plan_sf"] = Truth(
            "roof_plan_sf",
            plan_area,
            "SF",
            "derived",
            "the wall outline offset outward by the overhang (A + P d + 4 d^2)",
        )
        if roof.eaves == "all":
            gutter = perimeter_ft(chain) + 8.0 * d
        elif isinstance(roof.eaves, list) and roof.eaves and len(chain) == 4:
            ew, ns = extent_ft(chain)
            sides = {"N": ew, "S": ew, "E": ns, "W": ns}
            gutter = sum(sides[side] + 2.0 * d for side in roof.eaves)
    if roof.derive and plan_area is not None and gutter is not None:
        out["gutter_ft"] = Truth(
            "gutter_ft",
            gutter,
            "LF",
            "derived",
            "the eave line: the printed roof outline's perimeter (hip), or the wall outline "
            "offset by the overhang (P + 8 d for a hip; the named sides plus the overhangs for a "
            "rectangular gable)",
        )
    if (
        roof.derive
        and plan_area is not None
        and eave is not None
        and ridge is not None
        and ridge.above_grade_ft > eave.above_grade_ft
    ):
        source = roof.outline if roof.outline is not None else chain
        ew, ns = extent_ft(source)
        run = min(ew, ns) / 2.0 + (0.0 if roof.outline is not None else (d or 0.0))
        rise = ridge.above_grade_ft - eave.above_grade_ft
        factor = math.hypot(run, rise) / run
        out["roof_area_sf"] = Truth(
            "roof_area_sf",
            plan_area * factor,
            "SF",
            "derived",
            f"roof plan x slope factor {factor:.4f}: one pitch over the whole roof, rise "
            f"{rise:.2f} ft (ridge - eave) over a run of half the roof's short side "
            f"({run:.2f} ft)",
        )
    return out


def summary_line(house: House) -> str:
    t = truths(house)
    parts = [f"{k} {v.value:,.1f}" for k, v in t.items()]
    return f"{house.id} {house.name}: " + "; ".join(parts)


def ids(houses: Iterable[House]) -> list[str]:
    return [h.id for h in houses]


__all__ = [
    "CHECK_CODES",
    "FIXTURES",
    "SCHEMA",
    "Edge",
    "Finding",
    "House",
    "Length",
    "Truth",
    "area_sf",
    "check",
    "closure_ft",
    "extent_ft",
    "gla_sf",
    "load",
    "load_all",
    "parse_ft",
    "perimeter_ft",
    "truths",
    "undeclared",
    "vertices",
]
