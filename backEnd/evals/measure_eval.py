"""``python -m evals.measure_eval``: score measuring methods against real houses of known size
(EVAL_REPORT 103, AI_MEASUREMENT_PLAN.md section 7 step 1).

The truth is :mod:`evals.measure_truth` (HABS measured drawings, one JSON per house in
``evals/fixtures/measure_truth/``). Every method that produces a quantity for a house is run on
it and scored per quantity per house: the signed error ``(method - truth) / truth`` in percent
and its absolute value, then per quantity across houses (mean signed: the bias; mean and median
absolute; worst; the per-house spread of the signed error, which sizes how many houses tell two
methods apart, AI_MEASUREMENT_PLAN section 4). Each method reports its cost in dollars, tokens
and seconds per house (zero dollars and no tokens for the free ones).

**Methods.** A method is a :class:`Method`: an id, the plain setup name the tables print, the
exact settings it ran with, and ``run(house) -> MethodResult``. :data:`METHODS` lists the ones
that exist; AI_MEASUREMENT_PLAN section 5's M1 to M6 plug in by adding one there (a method that
needs captures or a gather reads them from ``--inputs``, a directory of per-house files). Only
M0 exists today:

* ``M0``, today's formulas: the production functions of :mod:`basis.domain.measurements`
  (``footprint``, ``ground_footprint``, ``perimeter_ft``, ``long_side_ft``, ``roof_area``,
  ``siding_area``, ``gutter_length``), fed what an appraisal would give for the house: the GLA
  as the plan's finished floor areas summed (:func:`evals.measure_truth.gla_sf`) and the story
  count, no garage, no sketch. The GLA is exact here, so M0's footprint is exact on houses with
  equal floors: this is M0 with a perfect appraisal, its best case.

**The lidar first look** (``--geo DIR``): the 3DEP summary each house's public geo gather
stored (:mod:`evals.measure_gather`), its footprint, eave (5th percentile) and peak (99th)
against the truth. Not a method (M1 will be): the raw numbers, as read.

**The pixel-size check** (``--pixels FILE``): known lengths measured in pixels on the Google
straight-down view and the public ortho (:mod:`evals.measure_pixels`), the implied metres per
pixel against the view's recorded ``pixel_size_m``.

Deterministic for M0, no model call, $0. ``--dry-run`` scores a synthetic house only (CI and the
unit test). The run files under ``verification/measure_eval/v1/``.

Usage (from ``backEnd/``)::

    uv run python -m evals.measure_eval \\
        --out "$BASIS_EVAL_DATA_DIR/predictions/measure_eval_<date>" \\
        [--geo "$BASIS_EVAL_DATA_DIR/measure_truth/geo"] [--pixels .../pixel_check.json]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal

from basis.domain import measurements as ms
from basis.geo import shell
from basis.geo.geometry import point_in_geojson, to_mercator
from evals import measure_pixels, measure_points, record
from evals import measure_truth as mt

COMPONENT: Final = "measure_eval"
VERSION: Final = "v2"  # v2: the M1 setups and the paired tables (EVAL_REPORT 105)
M_TO_FT: Final = 1 / 0.3048
SQM_TO_SF: Final = M_TO_FT * M_TO_FT

# What each quantity is, for the tables (the column meanings are printed with them).
QUANTITIES: Final[dict[str, str]] = {
    "footprint_sf": "ground floor outline area (SF)",
    "perimeter_ft": "ground floor exterior wall perimeter (LF)",
    "long_side_ft": "the outline's longer side (LF)",
    "gla_sf": "gross living area (SF)",
    "eave_height_ft": "eave height above grade (ft)",
    "ridge_height_ft": "ridge height above grade (ft)",
    "wall_area_gross_sf": "exterior wall face, no openings deducted (SF)",
    "roof_plan_sf": "roof outline in plan, overhang included (SF)",
    "roof_area_sf": "roof surface (SF)",
    "gutter_ft": "eave (gutter) length (LF)",
}


@dataclass(frozen=True, slots=True)
class MethodResult:
    quantities: dict[str, float]
    cost_usd: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float = 0.0
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Method:
    id: str
    setup: str  # the plain name the tables print
    settings: dict[str, Any]
    run: Callable[[mt.House], MethodResult]
    paid: bool = False


# ------------------------------------------------------------------ M0: today's formulas


def appraisal_inputs(house: mt.House) -> ms.BuildingMeasurements:
    """What an appraisal would carry for the house: the GLA (the plan's finished floors
    summed) and the story count, read as extraction fields."""
    gla = Decimal(str(round(mt.gla_sf(house), 2)))
    stories = Decimal(str(house.stories))
    return ms.BuildingMeasurements(
        gla_sf=ms.Reading(gla, "improvements.gla_sqft", "habs_plan"),
        stories=ms.Reading(stories, "improvements.stories", "habs_drawing"),
    )


def run_m0(house: mt.House) -> MethodResult:
    started = time.perf_counter()
    m = appraisal_inputs(house)
    foot = ms.footprint(m)
    ground = ms.ground_footprint(m)
    roof = ms.roof_area(m)
    siding = ms.siding_area(m)
    gutter = ms.gutter_length(m)
    if foot is None or ground is None or roof is None or siding is None or gutter is None:
        raise ValueError(f"{house.id}: M0 produced no quantity from GLA and stories")
    roof_plan = Decimal(roof.inputs["ground_footprint_sf"]) + Decimal(
        roof.inputs["perimeter_ft"]
    ) * Decimal(roof.inputs["overhang_ft"])
    quantities = {
        "footprint_sf": float(foot.quantity),
        "perimeter_ft": float(ms.perimeter_ft(ground.quantity)),
        "long_side_ft": float(ms.long_side_ft(ground.quantity)),
        "gla_sf": float(m.gla_sf.value) if m.gla_sf is not None else math.nan,
        # The wall height M0's siding rests on: 9 ft a story.
        "eave_height_ft": float(ms.STORY_WALL_HEIGHT_FT * Decimal(str(house.stories))),
        # M0's siding before its 15 % openings, the same footing as the truth's gross face.
        "wall_area_gross_sf": float(siding.quantity / (1 - ms.WALL_OPENINGS_SHARE)),
        "roof_plan_sf": float(roof_plan),
        "roof_area_sf": float(roof.quantity),
        "gutter_ft": float(gutter.quantity),
    }
    return MethodResult(
        quantities,
        seconds=time.perf_counter() - started,
        notes={"siding_net_sf": str(siding.quantity), "roof_formula": roof.formula},
    )


M0: Final = Method(
    id="M0",
    setup="Today's formulas (GLA / stories as a 1.5:1 rectangle)",
    settings={
        "module": "basis.domain.measurements",
        "aspect_ratio": str(ms.FOOTPRINT_ASPECT_RATIO),
        "roof_pitch_factor": str(ms.ROOF_PITCH_FACTOR),
        "eave_overhang_ft": str(ms.EAVE_OVERHANG_FT),
        "story_wall_height_ft": str(ms.STORY_WALL_HEIGHT_FT),
        "wall_openings_share": str(ms.WALL_OPENINGS_SHARE),
        "gutter_eaves": ms.GUTTER_EAVES,
        "inputs": "GLA = the plan's finished floors summed; stories as drawn; no garage; no sketch",
    },
    run=run_m0,
)

# Every method the harness runs, in table order. M1 to M6 (AI_MEASUREMENT_PLAN section 5) are
# added here as they are built.
METHODS: Final[dict[str, Method]] = {"M0": M0}

# ------------------------------------------------------------------ M1: the shell from lidar


@dataclass(frozen=True, slots=True)
class M1Settings:
    """One M1 setup (EVAL_REPORT 105): the arguments :func:`basis.geo.shell.derive` runs with,
    which eave height is the wall height, how the roof surface is read, and where the
    footprint comes from."""

    classification: shell.Classification = "auto"
    overhang: float | Literal["drawn"] = shell.OVERHANG_PRIOR_M
    eave: Literal["main", "p5"] = "main"
    returns: shell.Returns = shell.RETURNS
    neck_m: float = shell.NECK_M
    grow_edges: bool = True
    roof: Literal["planes", "factor"] = "planes"
    footprint: Literal["lidar", "structures"] = "lidar"

    def as_dict(self) -> dict[str, Any]:
        return {
            "module": "basis.geo.shell.derive",
            "classification": self.classification,
            "overhang_m": self.overhang,
            "wall_height": "main eave" if self.eave == "main" else "5th percentile of the roof",
            "returns": self.returns,
            "neck_m": self.neck_m,
            "grow_edges": self.grow_edges,
            "roof_surface": (
                "planes / cos(slope)"
                if self.roof == "planes"
                else f"roof plan x {ms.ROOF_PITCH_FACTOR} (M0's 6/12)"
            ),
            "footprint": (
                "lidar roof outline less the overhang"
                if self.footprint == "lidar"
                else "USA Structures polygon under the point less the overhang"
            ),
            "planar_std_m": shell.PLANAR_STD_M,
            "planar_k": shell.PLANAR_K,
            "min_height_m": shell.MIN_HEIGHT_M,
            "plane_distance_m": shell.PLANE_DISTANCE_M,
            "plane_normal_deg": shell.PLANE_NORMAL_DEG,
            "ransac_seed": shell.RANSAC_SEED,
        }


def house_points(geo: Path, house_id: str) -> tuple[shell.ShellPoints, dict[str, Any]] | None:
    """The cached points (:mod:`evals.measure_points`) and their read record, or ``None``."""
    folder = geo / house_id
    if not (folder / "points.npz").is_file():
        return None
    meta = json.loads((folder / "points.json").read_text(encoding="utf-8"))
    return measure_points.load_points(folder), meta


def mercator_box(box_lonlat: Sequence[float]) -> tuple[float, float, float, float]:
    x0, y0 = to_mercator(float(box_lonlat[0]), float(box_lonlat[1]))
    x1, y1 = to_mercator(float(box_lonlat[2]), float(box_lonlat[3]))
    return (x0, y0, x1, y1)


def structures_ring(
    geo: Path, house: mt.House
) -> tuple[list[tuple[float, float]], dict[str, Any]] | None:
    """The USA Structures polygon that holds the house's point, in ground metres east and north
    of it (``None`` when no polygon holds it: the nearest one may be a neighbour's)."""
    path = geo / house.id / "structures.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    lon, lat = house.location.lon, house.location.lat
    k = math.cos(math.radians(lat))
    cx, cy = to_mercator(lon, lat)
    for feature in data.get("features") or []:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Polygon" or not point_in_geojson(lon, lat, geometry):
            continue
        ring = geometry["coordinates"][0]
        if ring[0] == ring[-1]:
            ring = ring[:-1]
        metres = []
        for x, y in ring:
            mx, my = to_mercator(float(x), float(y))
            metres.append(((mx - cx) * k, (my - cy) * k))
        if shell.ring_area(metres) < 0:
            metres.reverse()
        return metres, dict(feature.get("properties") or {})
    return None


def run_m1(house: mt.House, geo: Path, settings: M1Settings) -> MethodResult:
    """One M1 setup on one house; an empty result (with the reason in ``notes``) where it
    drafts nothing."""
    got = house_points(geo, house.id)
    if got is None:
        return MethodResult({}, notes={"refused": "no lidar points"})
    points, meta = got
    if settings.overhang == "drawn":
        if house.roof.overhang is None:
            return MethodResult({}, notes={"refused": "no drawn overhang"})
        overhang = house.roof.overhang.ft * 0.3048
    else:
        overhang = float(settings.overhang)
    started = time.perf_counter()
    s = shell.derive(
        points,
        house.location.lon,
        house.location.lat,
        mercator_box(meta["box_lonlat"]),
        classification=settings.classification,
        overhang_m=overhang,
        returns=settings.returns,
        neck_m=settings.neck_m,
        grow_edges=settings.grow_edges,
    )
    seconds = time.perf_counter() - started
    notes = {
        "classification": str(s.classification),
        "code": str(s.code),
        "refusals": "; ".join(f"{part} {code}: {why}" for part, code, why in s.refusals),
        "canopy_share": "" if s.canopy_share is None else f"{s.canopy_share:.3f}",
        "grown_points": str(s.grown_points),
        "building_points": str(s.building_points),
        "pitch_deg": "" if s.pitch_deg is None else f"{s.pitch_deg:.1f}",
        "planes": str(len(s.planes)),
        "eave_p5_ft": "" if s.eave_m is None else f"{s.eave_m * M_TO_FT:.2f}",
        "eave_main_ft": "" if s.eave_main_m is None else f"{s.eave_main_m * M_TO_FT:.2f}",
        "eave_edges": str(sum(1 for e in s.edges if e.kind == "eave")),
        "rake_edges": str(sum(1 for e in s.edges if e.kind == "rake")),
        "offset": str(s.offset_method),
        "project": str(meta.get("project")),
        "fetch_bytes": str(meta.get("bytes")),
        "fetch_seconds": str(meta.get("seconds")),
        "fetch_requests": str(meta.get("requests")),
    }
    q: dict[str, float] = {}
    if s.ridge_m is not None and s.eave_m is not None:
        q["ridge_height_ft"] = s.ridge_m * M_TO_FT
        wall_m = s.eave_main_m if settings.eave == "main" and s.eave_main_m else s.eave_m
        q["eave_height_ft"] = wall_m * M_TO_FT
        if s.roof_plan_m2 is not None:
            q["roof_plan_sf"] = s.roof_plan_m2 * SQM_TO_SF
            if settings.roof == "planes" and s.roof_area_m2 is not None:
                q["roof_area_sf"] = s.roof_area_m2 * SQM_TO_SF
            elif settings.roof == "factor":
                q["roof_area_sf"] = s.roof_plan_m2 * SQM_TO_SF * float(ms.ROOF_PITCH_FACTOR)
        if s.gutter_m is not None:
            q["gutter_ft"] = s.gutter_m * M_TO_FT
        walls: list[tuple[float, float]] | None = (
            list(s.wall_outline_m) if s.wall_outline_m else None
        )
        if settings.footprint == "structures":
            found = structures_ring(geo, house)
            walls = None
            if found is not None:
                ring, props = found
                walls = shell.offset_inward_mitre(ring, overhang) or shell.offset_inward_raster(
                    ring, overhang
                )
                notes["structures"] = (
                    f"{props.get('SOURCE')} {props.get('VAL_METHOD')} image "
                    f"{props.get('IMAGE_NAME') or props.get('IMAGE_DATE')}"
                )
            else:
                notes["structures"] = "no polygon holds the point"
        walls_refused = any(part == "walls" for part, _, _ in s.refusals)
        if walls and not (walls_refused and settings.footprint == "lidar"):
            perimeter = shell.ring_perimeter(walls)
            q["footprint_sf"] = shell.ring_area(walls) * SQM_TO_SF
            q["perimeter_ft"] = perimeter * M_TO_FT
            q["long_side_ft"] = shell.long_side(walls) * M_TO_FT
            q["wall_area_gross_sf"] = perimeter * wall_m * SQM_TO_SF
    return MethodResult(q, seconds=seconds, notes=notes)


def m1_method(id_: str, setup: str, geo: Path, settings: M1Settings) -> Method:
    return Method(
        id=id_,
        setup=setup,
        settings=settings.as_dict(),
        run=lambda house: run_m1(house, geo, settings),
    )


# The M1 setups EVAL_REPORT 105 compares, in table order: (id, plain setup name, settings).
# ``M1`` is the one shipped as basis.geo.shell's defaults; ``M1-drawn-overhang`` uses the
# drawing's own overhang and is a diagnostic (it reads the truth), never a method.
M1_SETUPS: Final[tuple[tuple[str, str, M1Settings], ...]] = (
    (
        "M1",
        "Lidar shell: outline less an 18 in overhang, main eave, roof planes",
        M1Settings(),
    ),
    (
        "M1-outline-raw",
        "Lidar shell, roof outline taken as the walls (no overhang removed)",
        M1Settings(overhang=0.0),
    ),
    (
        "M1-overhang-12in",
        "Lidar shell, outline less a 12 in overhang (M0's)",
        M1Settings(overhang=0.3048),
    ),
    (
        "M1-overhang-24in",
        "Lidar shell, outline less a 24 in overhang",
        M1Settings(overhang=0.6096),
    ),
    (
        "M1-drawn-overhang",
        "Diagnostic: lidar shell less the drawing's own overhang (reads the truth)",
        M1Settings(overhang="drawn"),
    ),
    (
        "M1-own-classification",
        "Lidar shell, roof points classified here on every house (class 6 ignored)",
        M1Settings(classification="own"),
    ),
    (
        "M1-eave-p5",
        "Lidar shell, wall height = the roof's 5th percentile (the lidar summary's eave)",
        M1Settings(eave="p5"),
    ),
    (
        "M1-no-neck-split",
        "Lidar shell without cutting necks (a neighbour 1 m away stays merged)",
        M1Settings(neck_m=0.0),
    ),
    (
        "M1-no-edge-growth",
        "Lidar shell, own classification's roof edge not grown back",
        M1Settings(grow_edges=False),
    ),
    (
        "M1-all-returns",
        "Lidar shell, own classification takes every return (no returns rule)",
        M1Settings(returns="any", grow_edges=False),
    ),
    (
        "M1-roof-factor",
        "Lidar roof plan x 1.118 (M0's 6/12 factor) instead of the planes",
        M1Settings(roof="factor"),
    ),
    (
        "M1-structures",
        "USA Structures footprint less an 18 in overhang, lidar heights",
        M1Settings(footprint="structures"),
    ),
)


def m1_methods(geo: Path) -> dict[str, Method]:
    return {i: m1_method(i, setup, geo, st) for i, setup, st in M1_SETUPS}


# ------------------------------------------------------------------ paired against M0


def paired(
    scores: Sequence[Score],
    houses: Sequence[mt.House],
    *,
    baseline: str = "M0",
    standing: str | None = "unchanged",
) -> list[dict[str, Any]]:
    """Per method other than the baseline and per quantity: the houses where both scored (and
    stand as ``standing``, when given), each method's mean signed and mean absolute error on
    exactly those houses, and the paired difference of absolute errors (method - baseline):
    its mean, SD and a 95 % interval (Student's t), and on how many houses the method was
    closer. A negative difference: the method is better."""
    keep = {h.id for h in houses if standing is None or h.standing.status == standing}
    by = {(s.method, s.house, s.quantity): s for s in scores if s.house in keep}
    rows = []
    for method, quantity in dict.fromkeys(
        (s.method, s.quantity) for s in scores if s.method != baseline
    ):
        pairs = [
            (by[(baseline, h, quantity)], by[(method, h, quantity)])
            for h in sorted(keep)
            if (baseline, h, quantity) in by and (method, h, quantity) in by
        ]
        if not pairs:
            continue
        diff = [m.abs_pct - b.abs_pct for b, m in pairs]
        n = len(pairs)
        sd = statistics.stdev(diff) if n > 1 else None
        half = T95[n - 1] * sd / math.sqrt(n) if sd is not None and (n - 1) in T95 else None
        rows.append(
            {
                "method": method,
                "quantity": quantity,
                "n": n,
                "houses": [b.house for b, _ in pairs],
                "baseline_mean_signed_pct": statistics.fmean(b.signed_pct for b, _ in pairs),
                "method_mean_signed_pct": statistics.fmean(m.signed_pct for _, m in pairs),
                "baseline_mean_abs_pct": statistics.fmean(b.abs_pct for b, _ in pairs),
                "method_mean_abs_pct": statistics.fmean(m.abs_pct for _, m in pairs),
                "method_worst_abs_pct": max(m.abs_pct for _, m in pairs),
                "method_sd_signed_pct": (
                    statistics.stdev(m.signed_pct for _, m in pairs) if n > 1 else None
                ),
                "diff_mean_pct": statistics.fmean(diff),
                "diff_sd_pct": sd,
                "diff_ci95_pct": half,
                "closer": sum(1 for d in diff if d < 0),
            }
        )
    return rows


# Student's t at 97.5 % for 1 to 12 degrees of freedom.
T95: Final[dict[int, float]] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
}


# ------------------------------------------------------------------ scoring


def error_pct(predicted: float, truth: float) -> float:
    """The signed error in percent of the truth."""
    if truth == 0:
        raise ValueError("a true value of zero has no percent error")
    return (predicted - truth) / truth * 100.0


@dataclass(frozen=True, slots=True)
class Score:
    method: str
    house: str
    quantity: str
    truth: float
    tier: str
    predicted: float
    signed_pct: float

    @property
    def abs_pct(self) -> float:
        return abs(self.signed_pct)


def score_house(method: Method, house: mt.House, result: MethodResult) -> list[Score]:
    truth = mt.truths(house)
    out = []
    for quantity, value in result.quantities.items():
        t = truth.get(quantity)
        if t is None or not math.isfinite(value):
            continue
        out.append(
            Score(method.id, house.id, quantity, t.value, t.tier, value, error_pct(value, t.value))
        )
    return out


def summarize(scores: Sequence[Score]) -> list[dict[str, Any]]:
    """Per method and quantity: n, mean signed, SD of signed (houses), mean / median / worst
    absolute."""
    rows = []
    keys = dict.fromkeys((s.method, s.quantity) for s in scores)
    for method, quantity in keys:
        chosen = [s for s in scores if s.method == method and s.quantity == quantity]
        signed = [s.signed_pct for s in chosen]
        absolute = [s.abs_pct for s in chosen]
        rows.append(
            {
                "method": method,
                "quantity": quantity,
                "n": len(chosen),
                "tiers": sorted({s.tier for s in chosen}),
                "mean_signed_pct": statistics.fmean(signed),
                "sd_signed_pct": statistics.stdev(signed) if len(signed) > 1 else None,
                "mean_abs_pct": statistics.fmean(absolute),
                "median_abs_pct": statistics.median(absolute),
                "worst_abs_pct": max(absolute),
                "worst_house": max(chosen, key=lambda s: s.abs_pct).house,
            }
        )
    return rows


# ------------------------------------------------------------------ the lidar first look


def lidar_rows(houses: Sequence[mt.House], geo: Path) -> list[dict[str, Any]]:
    """The gather's lidar summary per house against the truth (raw, not a method)."""
    rows = []
    for house in houses:
        path = geo / house.id / "gather.json"
        if not path.is_file():
            rows.append({"house": house.id, "status": "no gather"})
            continue
        got = json.loads(path.read_text(encoding="utf-8"))
        lidar = got.get("lidar") or {}
        building = lidar.get("building")
        truth = mt.truths(house)
        row: dict[str, Any] = {
            "house": house.id,
            "project": lidar.get("project"),
            "year": lidar.get("year"),
            "density_per_m2": lidar.get("density_per_m2"),
            "code": lidar.get("code"),
            "status": "ok" if building else (lidar.get("code") or "no lidar"),
        }
        if building:
            foot = float(building["footprint_m2"]) * SQM_TO_SF
            row.update(
                {
                    "footprint_sf": foot,
                    "eave_ft": float(building["eave_m"]) * M_TO_FT,
                    "peak_ft": float(building["peak_m"]) * M_TO_FT,
                    "reaches_box_edge": building.get("reaches_box_edge"),
                    "distance_m": building.get("distance_m"),
                    "vs_walls_pct": error_pct(foot, truth["footprint_sf"].value),
                }
            )
            if "roof_plan_sf" in truth:
                row["vs_roof_plan_pct"] = error_pct(foot, truth["roof_plan_sf"].value)
            if "eave_height_ft" in truth:
                row["eave_vs_truth_pct"] = error_pct(row["eave_ft"], truth["eave_height_ft"].value)
            if "ridge_height_ft" in truth:
                row["peak_vs_ridge_pct"] = error_pct(row["peak_ft"], truth["ridge_height_ft"].value)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ the dry-run house


def synthetic_house() -> mt.House:
    """A 30 x 40 ft two-storey box with a hip roof, 1.5 ft overhang, eave 20 ft, ridge 28 ft:
    every truth has a closed form, so the dry run checks the harness end to end."""

    def edge(d: str, ft: int) -> dict[str, Any]:
        return {"dir": d, "length": {"ft": ft, "text": f"{ft}'-0\"", "sheet": "1"}}

    doc = {
        "schema": mt.SCHEMA,
        "id": "zz0001",
        "name": "Synthetic box",
        "loc_url": "https://example.invalid/zz0001",
        "survey_number": "none",
        "drawing_date": "2026",
        "address": "nowhere",
        "location": {"lat": 40.0, "lon": -90.0, "how": "synthetic"},
        "standing": {"status": "unknown", "source": "synthetic"},
        "sheets": {"1": "plan"},
        "stories": 2,
        "roof_shape": "hip",
        "plan": {
            "sheet": "1",
            "scale": '1/4" = 1\'-0"',
            "chain": [edge("E", 40), edge("S", 30), edge("W", 40), edge("N", 30)],
            "overall": {
                "ew": {"ft": 40, "text": "40'-0\""},
                "ns": {"ft": 30, "text": "30'-0\""},
            },
        },
        "floors": [
            {"level": "first", "same_as_ground": True},
            {"level": "second", "same_as_ground": True},
        ],
        "heights": {
            "eave": {"above_grade_ft": 20, "text": "20'-0\"", "datum_text": "0'-0\""},
            "ridge": {"above_grade_ft": 28, "text": "28'-0\"", "datum_text": "0'-0\""},
        },
        "roof": {"overhang": {"ft": 1.5, "text": "1'-6\""}, "eaves": "all", "derive": True},
        "passes": {"a": "synthetic", "b": "synthetic"},
    }
    return mt.House.model_validate(doc)


# ------------------------------------------------------------------ the run


def run_all(
    houses: Sequence[mt.House], methods: Sequence[Method]
) -> tuple[list[Score], list[dict[str, Any]]]:
    scores: list[Score] = []
    costs: list[dict[str, Any]] = []
    for method in methods:
        for house in houses:
            result = method.run(house)
            scores.extend(score_house(method, house, result))
            costs.append(
                {
                    "method": method.id,
                    "house": house.id,
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "seconds": result.seconds,
                    "quantities": result.quantities,
                    "notes": result.notes,
                }
            )
    return scores, costs


def _g(value: Any, digits: int = 1) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:,.{digits}f}"
    return str(value)


def render(
    houses: Sequence[mt.House],
    methods: Sequence[Method],
    scores: Sequence[Score],
    costs: Sequence[Mapping[str, Any]],
    lidar: Sequence[Mapping[str, Any]] | None,
    pixels: Mapping[str, Any] | None,
) -> str:
    setups = {m.id: m.setup for m in methods}
    lines = ["# Measuring against real houses (EVAL_REPORT 103)", ""]
    lines += ["## The houses", ""]
    lines += [
        "| House | Name | Drawn | Stories | Roof | Standing | Truth quantities |",
        "|---|---|---|---|---|---|---|",
    ]
    for h in houses:
        t = mt.truths(h)
        lines.append(
            f"| {h.id} | {h.name} | {h.drawing_date} | {h.stories:g} | {h.roof_shape} | "
            f"{h.standing.status} | {', '.join(f'{k} ({v.tier})' for k, v in t.items())} |"
        )
    lines += ["", "## The setups", ""]
    lines += ["| Id | Setup | Exact settings | Paid |", "|---|---|---|---|"]
    for m in methods:
        lines.append(f"| {m.id} | {m.setup} | `{json.dumps(m.settings)}` | {m.paid} |")
    lines += ["", "## Error per quantity (all houses)", ""]
    lines += [
        "Columns: *n* houses with a truth for the quantity; *tiers*: printed (arithmetic on "
        "printed dimensions), derived (printed values plus a stated geometric assumption), scaled "
        "(scaled off the drawing); *mean signed*: the bias, (method - truth) / truth; *SD*: the "
        "spread of the signed error across houses; *mean*, *median* and *worst* absolute error.",
        "",
        "| Setup | Quantity | n | Tiers | Mean signed % | SD % | Mean abs % | Median abs % | "
        "Worst abs % (house) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summarize(scores):
        lines.append(
            f"| {setups[r['method']]} | {QUANTITIES.get(r['quantity'], r['quantity'])} | "
            f"{r['n']} | "
            f"{', '.join(r['tiers'])} | {_g(r['mean_signed_pct'])} | {_g(r['sd_signed_pct'])} | "
            f"{_g(r['mean_abs_pct'])} | {_g(r['median_abs_pct'])} | {_g(r['worst_abs_pct'])} "
            f"({r['worst_house']}) |"
        )
    lines += ["", "## Per house (signed error %, method against truth)", ""]
    quantities = list(dict.fromkeys(s.quantity for s in scores))
    for m in methods:
        lines += [f"### {m.setup}", ""]
        lines += ["| House | " + " | ".join(quantities) + " |"]
        lines += ["|---|" + "---|" * len(quantities)]
        for h in houses:
            cells = []
            for q in quantities:
                hit = [
                    s for s in scores if s.method == m.id and s.house == h.id and s.quantity == q
                ]
                cells.append(
                    f"{_g(hit[0].signed_pct)} ({_g(hit[0].predicted, 0)} vs {_g(hit[0].truth, 0)})"
                    if hit
                    else ""
                )
            lines.append(f"| {h.id} | " + " | ".join(cells) + " |")
        lines.append("")
    if len(methods) > 1 and any(m.id == "M0" for m in methods):
        for standing, title in (
            ("unchanged", "houses that stand unchanged"),
            (None, "every house the method drafted"),
        ):
            rows = paired(scores, houses, standing=standing)
            lines += [f"## Paired against today's formulas ({title})", ""]
            lines += [
                "Columns: *n* houses where both the setup and M0 have the quantity; *M0* and "
                "*setup*: each one's mean signed and mean absolute error on exactly those "
                "houses; *diff*: the setup's absolute error minus M0's, per house, its mean, SD "
                "and the 95 % interval of the mean (Student's t; negative = the setup is "
                "closer); *closer*: on how many houses the setup was closer; *worst*: the "
                "setup's largest absolute error.",
                "",
                "| Setup | Quantity | n | M0 signed % | Setup signed % | M0 abs % | Setup abs % "
                "| Setup SD % | Setup worst % | Diff mean % | Diff SD % | 95 % +- | Closer |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for r in rows:
                lines.append(
                    f"| {setups[r['method']]} | {QUANTITIES.get(r['quantity'], r['quantity'])} "
                    f"| {r['n']} | {_g(r['baseline_mean_signed_pct'])} | "
                    f"{_g(r['method_mean_signed_pct'])} | {_g(r['baseline_mean_abs_pct'])} | "
                    f"{_g(r['method_mean_abs_pct'])} | {_g(r['method_sd_signed_pct'])} | "
                    f"{_g(r['method_worst_abs_pct'])} | {_g(r['diff_mean_pct'])} | "
                    f"{_g(r['diff_sd_pct'])} | {_g(r['diff_ci95_pct'])} | "
                    f"{r['closer']} of {r['n']} |"
                )
            lines.append("")
        m1 = [c for c in costs if str(c["method"]).startswith("M1")]
        if m1:
            lines += ["## The lidar shell per house (what each setup read)", ""]
            lines += [
                "| Setup | House | Classified by | Code | Planes | Pitch deg | Eave p5 / main ft "
                "| Eave / rake edges | Grown pts | Canopy | Fetch bytes | Fetch s | Derive s |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for c in m1:
                n = c["notes"]
                lines.append(
                    f"| {c['method']} | {c['house']} | {n.get('classification', '')} | "
                    f"{n.get('code') or n.get('refused', '')} | {n.get('planes', '')} | "
                    f"{n.get('pitch_deg', '')} | {n.get('eave_p5_ft', '')} / "
                    f"{n.get('eave_main_ft', '')} | {n.get('eave_edges', '')} / "
                    f"{n.get('rake_edges', '')} | {n.get('grown_points', '')} | "
                    f"{n.get('canopy_share', '')} | {n.get('fetch_bytes', '')} | "
                    f"{n.get('fetch_seconds', '')} | {c['seconds']:.3f} |"
                )
            lines.append("")
    lines += ["## Cost per house", ""]
    lines += [
        "| Setup | Houses | Dollars | Input tokens | Output tokens | Seconds (mean) |",
        "|---|---|---|---|---|---|",
    ]
    for m in methods:
        mine = [c for c in costs if c["method"] == m.id]
        tokens_in = [c["input_tokens"] for c in mine if c["input_tokens"] is not None]
        tokens_out = [c["output_tokens"] for c in mine if c["output_tokens"] is not None]
        lines.append(
            f"| {m.setup} | {len(mine)} | {sum(c['cost_usd'] for c in mine):.2f} | "
            f"{sum(tokens_in) if tokens_in else 'none'} | "
            f"{sum(tokens_out) if tokens_out else 'none'} | "
            f"{statistics.fmean(c['seconds'] for c in mine):.6f} |"
        )
    if lidar is not None:
        lines += ["", "## Lidar first look (raw 3DEP summary, not a method)", ""]
        lines += [
            "Columns: *footprint*: the lidar building's cells (0.5 m) as flown, eaves included; "
            "*vs walls*: against the wall outline; *vs roof plan*: against the wall outline "
            "offset by the overhang; *eave*: the 5th percentile of the building's points above "
            "the ground ring; *peak*: the 99th.",
            "",
            "| House | Project (year) | Pts/m2 | Footprint SF | vs walls % | vs roof plan % | "
            "Eave ft | vs truth % | Peak ft | vs ridge % | Status |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for lr in lidar:
            lines.append(
                f"| {lr['house']} | {lr.get('project') or ''} ({lr.get('year') or ''}) | "
                f"{_g(lr.get('density_per_m2'))} | {_g(lr.get('footprint_sf'), 0)} | "
                f"{_g(lr.get('vs_walls_pct'))} | {_g(lr.get('vs_roof_plan_pct'))} | "
                f"{_g(lr.get('eave_ft'))} | {_g(lr.get('eave_vs_truth_pct'))} | "
                f"{_g(lr.get('peak_ft'))} | {_g(lr.get('peak_vs_ridge_pct'))} | {lr['status']} |"
            )
    if pixels is not None:
        lines += ["", "## Pixel size against known lengths (evals.measure_pixels)", ""]
        lines += [
            "Columns: *ratio*: the metres per pixel the true length implies over the view's "
            "recorded pixel size (1: right; below 1: a traced length reads long); "
            "*height-corrected*: the ratio with the perspective of a 3D roof edge divided out "
            "(Google views only).",
            "",
            measure_pixels.render(pixels),
            "| View | Houses | Segments | Ratio, mean of house means | SD | Min | Max | "
            "Height-corrected mean | SD |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for kind, k in pixels["summary"].items():
            r, c = k["ratio_over_house_means"], k["corrected_over_house_means"]
            lines.append(
                f"| {kind} | {k['houses']} | {k['segments']} | {_g(r['mean'], 4)} | "
                f"{_g(r['sd'], 4)} | {_g(r['min'], 4)} | {_g(r['max'], 4)} | "
                f"{_g(c['mean'], 4)} | {_g(c['sd'], 4)} |"
            )
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.measure_eval", description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--truth", type=Path, default=mt.FIXTURES, help="the truth files")
    parser.add_argument("--methods", default=",".join(METHODS), help="comma-separated ids")
    parser.add_argument("--geo", type=Path, help="per-house gathers (measure_gather)")
    parser.add_argument("--pixels", type=Path, help="the pixel check's result (measure_pixels)")
    parser.add_argument(
        "--points",
        type=Path,
        help="per-house cached lidar points and footprints (measure_points): adds the M1 setups",
    )
    parser.add_argument("--dry-run", action="store_true", help="the synthetic house only")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        known = dict(METHODS)
        if args.points is not None:
            known.update(m1_methods(args.points))
        wanted = [m.strip() for m in args.methods.split(",") if m.strip()]
        if args.points is not None and args.methods == ",".join(METHODS):
            wanted = list(known)
        unknown = [m for m in wanted if m not in known]
        if unknown:
            raise ValueError(f"unknown methods {unknown}; known {list(known)}")
        methods = [known[m] for m in wanted]
        if args.dry_run:
            houses = [synthetic_house()]
        else:
            paths = sorted(args.truth.glob("*.json"))
            if not paths:
                raise ValueError(f"no truth files in {args.truth}")
            for p in paths:
                run.dataset(p, name=f"measure_truth/{p.stem}")
            houses = [mt.load(p) for p in paths]
        for house in houses:
            missing = mt.undeclared(house)
            run.case(
                f"truth_checks/{house.id}",
                "passed" if not missing else "failed",
                detail={
                    "findings": [asdict(f) for f in mt.check(house)],
                    "undeclared": [asdict(f) for f in missing],
                },
            )
        scores, costs = run_all(houses, methods)
        lidar = lidar_rows(houses, args.geo) if args.geo is not None else None
        pixels = (
            json.loads(args.pixels.read_text(encoding="utf-8")) if args.pixels is not None else None
        )
        if args.geo is not None:
            run.input("geo", args.geo)
        if args.pixels is not None:
            run.input("pixels", args.pixels)
        if args.points is not None:
            run.input("points", args.points)
        for m in methods:
            mine = [s for s in scores if s.method == m.id]
            run.case(
                f"method_ran/{m.id}",
                "passed" if mine else "failed",
                detail={"scores": len(mine), "houses": len({s.house for s in mine})},
            )
        text = render(houses, methods, scores, costs, lidar, pixels)
        (args.out / "measure_eval.md").write_text(text, encoding="utf-8")
        result = {
            "houses": [h.id for h in houses],
            "truths": {h.id: {k: asdict(v) for k, v in mt.truths(h).items()} for h in houses},
            "scores": [{**asdict(s), "abs_pct": s.abs_pct} for s in scores],
            "summary": summarize(scores),
            "costs": costs,
            "lidar": lidar,
            "pixels": pixels,
            "paired_unchanged": paired(scores, houses, standing="unchanged"),
            "paired_all": paired(scores, houses, standing=None),
        }
        (args.out / "measure_eval.json").write_text(
            json.dumps(result, indent=1) + "\n", encoding="utf-8"
        )
        print(text)
        run.finish(
            {
                "dry_run": args.dry_run,
                "houses": len(houses),
                "methods": wanted,
                "summary": summarize(scores),
                "cost_usd": sum(c["cost_usd"] for c in costs),
                "tokens": None,
            }
        )
    except (ValueError, OSError, ArithmeticError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0 if all(c["outcome"] == "passed" for c in run.cases) else 1


__all__ = [
    "M1_SETUPS",
    "METHODS",
    "M0",
    "M1Settings",
    "Method",
    "MethodResult",
    "Score",
    "appraisal_inputs",
    "error_pct",
    "lidar_rows",
    "m1_method",
    "m1_methods",
    "main",
    "paired",
    "render",
    "run_all",
    "run_m0",
    "run_m1",
    "score_house",
    "summarize",
    "synthetic_house",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
