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
from typing import Any, Final

from basis.domain import measurements as ms
from evals import measure_pixels, record
from evals import measure_truth as mt
from evals import site_score as ss
from evals import site_truth as st

COMPONENT: Final = "measure_eval"
VERSION: Final = "v1"
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
    # Site items (EVAL_REPORT 106): one outline each, scored per item by evals.site_score.
    "driveway_sf": "driveway, one outline (SF)",
    "patio_sf": "patio, one outline (SF)",
    "deck_sf": "deck, one outline (SF)",
    "pool_sf": "pool as the county drew it (SF)",
    "walk_sf": "walk on the lot (SF)",
}
SITE_FAMILIES: Final = ("driveway", "patio", "deck", "pool", "walk")
# Douglas-Peucker tolerances on the ground (m): 0, then about 1, 2, 3, 4, 6 and 8 pixels at
# the county view's 7.8 cm.
SITE_TOLERANCES: Final = (0.0, 0.08, 0.15, 0.23, 0.3, 0.45, 0.6)


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
    setups = {m.id: m.setup for m in methods}
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


# ------------------------------------------------------------------ site items (EVAL 106)


def site_results(
    root: Sequence[Path], truth_dir: Path, fence: Path | None
) -> tuple[str, dict[str, Any]]:
    """Every site-item table of EVAL_REPORT 106 from a ``site_draft_run`` directory."""
    parcels = st.load_all(truth_dir)
    dropped = {p.id: c.note for p in parcels for c in p.checks.values() if c.status == "dropped"}
    truth = {p.id: p for p in parcels if p.id not in dropped}
    scored = ss.score_all(root, truth)
    regs = ss.registrations(root)
    loo = ss.leave_one_out_k(regs)
    corrected = ss.with_parcel_scale(scored, loo)
    summary = ss.summarize(scored, SITE_FAMILIES)
    google_corrected = [r for r in ss.summarize(corrected, SITE_FAMILIES) if r["view"] == "google"]
    sweep = []
    for tol in SITE_TOLERANCES:
        rows = ss.summarize(
            [s for s in ss.score_all(root, truth, tolerance_m=tol) if s.method == "box"],
            SITE_FAMILIES,
        )
        sweep += [{"tolerance_m": tol, **r} for r in rows if r["family"] == "all"]
    lines = ["## Site items (EVAL_REPORT 106)", ""]
    lines += [
        f"Parcels scored: {len(truth)} of {len(parcels)} "
        f"(dropped: {', '.join(f'{k} ({v})' for k, v in sorted(dropped.items())) or 'none'}).",
        "",
    ]
    edges = ss.truth_edges(root, truth)
    if edges:
        lines += [
            "### The truth's own edges (the county outline against the county picture)",
            "",
            "Columns: *offset*: where the picture's strongest edge lies against the county's "
            "outline (positive: outside it, the outline drawn small), median and the median "
            "of its size; *area effect*: that offset all round as a share of the item's area "
            "(signed median, and the 90th percentile of its size): the truth's own error.",
            "",
            "| Family | Items | Median offset m | Median abs offset m | Median area effect % | "
            "P90 abs area effect % |",
            "|---|---|---|---|---|---|",
        ]
        for family in ("all", *SITE_FAMILIES):
            mine = [e for e in edges if family == "all" or e["family"] == family]
            if not mine:
                continue
            effects = sorted(abs(e["area_effect_pct"]) for e in mine)
            lines.append(
                f"| {family} | {len(mine)} | "
                f"{statistics.median(e['offset_m'] for e in mine):.2f} | "
                f"{statistics.median(abs(e['offset_m']) for e in mine):.2f} | "
                f"{statistics.median(e['area_effect_pct'] for e in mine):.1f} | "
                f"{ss.percentile(effects, 90):.1f} |"
            )
        lines.append("")
    for view in ("county", "google", "naip"):
        mine = [r for r in summary if r["view"] == view]
        if mine:
            lines.append(ss.render_summary(mine, f"{ss.VIEWS[view]}, as recorded"))
    if google_corrected:
        lines.append(
            ss.render_summary(
                google_corrected,
                "Google straight-down capture, areas corrected by the other parcels' median k",
            )
        )
    if regs:
        lines += [
            "### Google's ground pixel against the county ortho (evals.site_register)",
            "",
            "Columns: *k*: Google's ground pixel over its recorded one (1: right; above 1 a "
            "traced length reads short); *score*: the normalized cross-correlation at the best "
            "fit; *k used*: the median of the other parcels (the correction scored above).",
            "",
            "| Parcel | Imagery date | k | Score | k used |",
            "|---|---|---|---|---|",
        ]
        for r in regs:
            lines.append(
                f"| {r['parcel']} | {r['imagery_date'] or ''} | {r['k']:.4f} | "
                f"{r['score']:.3f} | {loo[r['parcel']]:.4f} |"
            )
        ks = [r["k"] for r in regs]
        lines += [
            "",
            f"Median k {statistics.median(ks):.4f}, mean {statistics.fmean(ks):.4f}, SD "
            f"{statistics.stdev(ks) if len(ks) > 1 else 0:.4f} over {len(ks)} parcels.",
            "",
        ]
    lines += [
        "### Douglas-Peucker tolerance (box alone, every view)",
        "",
        "Columns: as above; *tolerance*: on the ground, in metres.",
        "",
        "| Tolerance m | View | Items | Drafted | Mean abs % | Median abs % | P90 abs % | "
        "Mean IoU | Corners |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sweep:
        lines.append(
            f"| {r['tolerance_m']:g} | {r['view']} | {r['items']} | {r['drafted']} | "
            f"{_g(r['mean_abs_pct'])} | {_g(r['median_abs_pct'])} | {_g(r['p90_abs_pct'])} | "
            f"{_g(r['mean_iou'], 3)} | {_g(r['mean_corners'], 0)} |"
        )
    lines += ["", ss.render_costs(ss.costs(root))]
    fence_rows = None
    if fence is not None and (fence / "fence_rows.json").is_file():
        fence_rows = json.loads((fence / "fence_rows.json").read_text())
        lines += [
            "### Fence probe (evals.site_fence, county view)",
            "",
            "Columns: *parcels with an instance*: parcels where SAM 3 returned any instance; "
            "*confirmed*: the lot line within 1 m of a returned mask, over the lot line in the "
            "picture.",
            "",
            "| Prompt | Parcels with an instance | Lot line m | Confirmed m | Confirmed % |",
            "|---|---|---|---|---|",
        ]
        for key, name in (
            ("text", "'fence', no click"),
            ("text_lot_points", "'fence' with points along the lot line"),
        ):
            total = sum(r[key]["lot_line_m"] for r in fence_rows)
            near = sum(r[key]["confirmed_m"] for r in fence_rows)
            hit = sum(1 for r in fence_rows if r[key]["instances"])
            lines.append(
                f"| {name} | {hit} of {len(fence_rows)} | {total:,.0f} | {near:,.0f} | "
                f"{near / total * 100 if total else 0:.1f} |"
            )
        lines.append("")
    result = {
        "parcels": sorted(truth),
        "dropped": dropped,
        "summary": summary,
        "google_corrected": google_corrected,
        "registrations": regs,
        "leave_one_out_k": loo,
        "tolerance_sweep": sweep,
        "truth_edges": edges,
        "costs": ss.costs(root),
        "ledger": ss.ledger_total(root),
        "fence": fence_rows,
        "scores": [{**asdict(s), "signed_pct": s.signed_pct} for s in scored],
    }
    return "\n".join(lines) + "\n", result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.measure_eval", description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--truth", type=Path, default=mt.FIXTURES, help="the truth files")
    parser.add_argument("--methods", default=",".join(METHODS), help="comma-separated ids")
    parser.add_argument("--geo", type=Path, help="per-house gathers (measure_gather)")
    parser.add_argument("--pixels", type=Path, help="the pixel check's result (measure_pixels)")
    parser.add_argument("--dry-run", action="store_true", help="the synthetic house only")
    parser.add_argument(
        "--site", type=Path, action="append", help="a site_draft_run directory (EVAL 106)"
    )
    parser.add_argument("--site-truth", type=Path, default=st.FIXTURES, help="site truth files")
    parser.add_argument("--fence", type=Path, help="a site_fence directory (EVAL 106.6)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        wanted = [m.strip() for m in args.methods.split(",") if m.strip()]
        unknown = [m for m in wanted if m not in METHODS]
        if unknown:
            raise ValueError(f"unknown methods {unknown}; known {list(METHODS)}")
        methods = [METHODS[m] for m in wanted]
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
        for m in methods:
            mine = [s for s in scores if s.method == m.id]
            run.case(
                f"method_ran/{m.id}",
                "passed" if mine else "failed",
                detail={"scores": len(mine), "houses": len({s.house for s in mine})},
            )
        text = render(houses, methods, scores, costs, lidar, pixels)
        site: dict[str, Any] | None = None
        if args.site is not None:
            for k, site_dir in enumerate(args.site):
                run.input(f"site_{k + 1}", site_dir)
            for p in sorted(args.site_truth.glob("*.json")):
                run.dataset(p, name=f"site_truth/{p.stem}")
            site_text, site = site_results(args.site, args.site_truth, args.fence)
            text += "\n" + site_text
            for method in sorted({r["method"] for r in site["summary"]}):
                rows = [r for r in site["summary"] if r["method"] == method]
                run.case(
                    f"site_method/{method}",
                    "passed" if rows else "failed",
                    detail={"views": sorted({r["view"] for r in rows})},
                )
        (args.out / "measure_eval.md").write_text(text, encoding="utf-8")
        result = {
            "houses": [h.id for h in houses],
            "truths": {h.id: {k: asdict(v) for k, v in mt.truths(h).items()} for h in houses},
            "scores": [{**asdict(s), "abs_pct": s.abs_pct} for s in scores],
            "summary": summarize(scores),
            "costs": costs,
            "lidar": lidar,
            "pixels": pixels,
            "site": site,
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
                "site_parcels": None if site is None else len(site["parcels"]),
                "tokens": None,
            }
        )
    except (ValueError, OSError, ArithmeticError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0 if all(c["outcome"] == "passed" for c in run.cases) else 1


__all__ = [
    "METHODS",
    "M0",
    "Method",
    "MethodResult",
    "Score",
    "appraisal_inputs",
    "error_pct",
    "lidar_rows",
    "main",
    "render",
    "run_all",
    "site_results",
    "run_m0",
    "score_house",
    "summarize",
    "synthetic_house",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
