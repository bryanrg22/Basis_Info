"""M4 of AI_MEASUREMENT_PLAN.md: interior quantities from ordinary room photos, scored on the
ARKitScenes rooms (EVAL_REPORT 107). ``evals.measure_eval --m4 DIR`` runs it.

The truth is :mod:`evals.interior_truth` on the rooms :mod:`evals.interior_accept` kept; the
photos and depth outputs are under ``DIR`` (``$BASIS_EVAL_DATA_DIR/measure_truth/interiors``):
``photos/<room>/photo_<k>.png`` with ``photo_<k>.ade.png`` (:mod:`evals.interior_segment`) and
``depth/<model>/<room>.npz`` (``evals/interior_depth_run.py``). The setups:

* **M0**, today's formulas (:func:`basis.domain.takeoffs.estimate_quantity`), fed the room's
  *true* floor area (the GLA share exactly right, its best case): baseboard = perimeter =
  4 sqrt(area), wall finish = 3.5 x area (an 8 ft ceiling), countertop = 4 sqrt(area) x 25/12,
  cabinet = 4 sqrt(area) LF; the implied square room (sqrt(area) a side) and 8 ft ceiling are
  scored too. **M0 typical**: the same formulas on the room type's allocation weight
  (``ROOM_WEIGHTS_SF``: what the GLA share gives in a house whose rooms are all typical).
* **M4a**: a depth model's shape scaled by references (doors 80 in, counters 36 in; the median
  over the room's references), :func:`evals.room_measure.measure_room`; and **M4a,
  camera height**: scaled by an assumed 1.5 m camera height instead.
* **M4b**: the depth model's own metres, no reference.
* **M4c**: all of a room's photos through a multi-view model at once (one shared frame), scaled
  by references (or the camera height).
* **M4d**: a vision model asked directly (:mod:`evals.measure_interior_vlm`).

Every setup is scored per quantity per room: signed and absolute percent error, then per
quantity the bias, spread (SD of signed), mean, median and worst absolute error and the misses
(rooms with a truth the setup gave no number for); per setup the cost, tokens and seconds per
room (the depth model's GPU seconds for the local ones). The **reconciliation** sums each
setup's floor areas over the rooms it measured against the truth's sum, and rescales them so the
sum is right (what reconciling to the GLA can do at best) to see what that buys per room.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image

from basis.domain import takeoffs
from basis.domain.rooms import room_weight_sf
from evals import interior_truth as it
from evals import room_measure as rm

QUANTITIES: Final[dict[str, str]] = {
    "floor_area_sf": "floor area (SF)",
    "length_ft": "length, wall to wall (ft)",
    "width_ft": "width, wall to wall (ft)",
    "ceiling_height_ft": "ceiling height (ft)",
    "perimeter_ft": "perimeter = baseboard run, openings not deducted (LF)",
    "wall_area_sf": "wall area, gross (SF)",
    "countertop_sf": "countertop (SF)",
    "cabinet_lf": "base cabinet run (LF)",
}
ROOM_TYPES: Final = {
    "kitchen": "kitchen",
    "bedroom": "bedroom",
    "bathroom": "bathroom",
    "living": "living_room",
}


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    room_type: str
    truth: dict[str, float]  # only the quantities the acceptance rule kept
    photos: Path


@dataclass(slots=True)
class Result:
    quantities: dict[str, float]
    cost_usd: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Setup:
    id: str
    name: str
    settings: dict[str, Any]
    run: Callable[[Room], Result]
    paid: bool = False


def load_rooms(base: Path) -> list[Room]:
    accepted = json.loads((base / "accepted.json").read_text())
    rooms = []
    for rid, info in sorted(accepted["rooms"].items()):
        truth = it.load(base / "truth" / f"{rid}.json")
        q = truth.quantities()
        rooms.append(
            Room(
                rid,
                info["room_type"],
                {k: q[k] for k in info["quantities"] if k in q},
                base / "photos" / rid,
            )
        )
    return rooms


# ------------------------------------------------------------------ M0


def formulas(area_sf: float, kitchen: bool) -> dict[str, float]:
    """Today's takeoff rows on a room of ``area_sf`` (the production functions)."""
    area = Decimal(str(round(area_sf, 2)))
    q = {
        "floor_area_sf": float(takeoffs.estimate_quantity("flooring", area).quantity),
        "perimeter_ft": float(takeoffs.estimate_quantity("baseboard", area).quantity),
        "wall_area_sf": float(takeoffs.estimate_quantity("wall_finish", area).quantity),
        # The square room and the 8 ft ceiling the rows imply.
        "length_ft": math.sqrt(area_sf),
        "width_ft": math.sqrt(area_sf),
        "ceiling_height_ft": 8.0,
    }
    if kitchen:
        q["countertop_sf"] = float(takeoffs.estimate_quantity("countertop", area).quantity)
        q["cabinet_lf"] = float(takeoffs.estimate_quantity("cabinet", area).quantity)
    return q


def m0_setups(true_area: Mapping[str, float]) -> list[Setup]:
    def given(room: Room) -> Result:
        return Result(formulas(true_area[room.id], room.room_type == "kitchen"))

    def typical(room: Room) -> Result:
        weight = float(room_weight_sf(ROOM_TYPES[room.room_type]))
        return Result(formulas(weight, room.room_type == "kitchen"), notes={"area_sf": weight})

    return [
        Setup(
            "M0",
            "Today's formulas, the room's true floor area",
            {"functions": "basis.domain.takeoffs.estimate_quantity", "area": "truth"},
            given,
        ),
        Setup(
            "M0.typical",
            "Today's formulas, the room type's typical area",
            {"functions": "basis.domain.takeoffs.estimate_quantity", "area": "ROOM_WEIGHTS_SF"},
            typical,
        ),
    ]


# ------------------------------------------------------------------ M4a, M4b, M4c


def labels_for(path: Path, shape: tuple[int, int]) -> np.ndarray:
    """The photo's ADE20K map at the point map's size (nearest pixel)."""
    full = np.asarray(Image.open(path))
    rows = np.clip(
        ((np.arange(shape[0]) + 0.5) * full.shape[0] / shape[0]).astype(int), 0, full.shape[0] - 1
    )
    cols = np.clip(
        ((np.arange(shape[1]) + 0.5) * full.shape[1] / shape[1]).astype(int), 0, full.shape[1] - 1
    )
    return np.asarray(full[rows][:, cols])


def load_views(room: Room, depth: Path) -> tuple[list[rm.View], float]:
    """The room's views from a depth model's file, and its GPU seconds."""
    meta = json.loads((room.photos / "photos.json").read_text())
    data = np.load(depth / f"{room.id}.npz")
    views = []
    seconds = 0.0
    for k, photo in enumerate(meta["photos"]):
        pts = data[f"points_{k}"].astype(np.float64)
        labels = labels_for(room.photos / photo["file"].replace(".png", ".ade.png"), pts.shape[:2])
        camera = (0.0, 0.0, 0.0)
        if f"c2w_{k}" in data:
            c2w = data[f"c2w_{k}"]
            camera = (float(c2w[0, 3]), float(c2w[1, 3]), float(c2w[2, 3]))
        views.append(rm.View(pts, data[f"valid_{k}"], labels, camera))
        seconds += float(data[f"seconds_{k}"])
    return views, seconds


def depth_setup(
    id_: str,
    name: str,
    depth: Path,
    model: str,
    source: rm.ScaleSource,
    *,
    registered: bool = False,
) -> Setup:
    def run(room: Room) -> Result:
        if not (depth / f"{room.id}.npz").exists():
            return Result({}, notes={"refused": "no depth output"})
        views, seconds = load_views(room, depth)
        got = rm.measure_room(
            views, source=source, registered=registered, kitchen=room.room_type == "kitchen"
        )
        return Result(
            got.quantities(),
            seconds=seconds,
            notes={
                "refused": got.refused,
                "scale": got.scale,
                "references": [
                    {"kind": r.kind, "measured": r.measured, "scale": r.scale, "view": r.view}
                    for r in got.references
                ],
                "views_used": got.views_used,
                "notes": got.notes,
            },
        )

    return Setup(
        id_,
        name,
        {
            "depth_model": model,
            "scale": source,
            "registered": registered,
            "segmenter": "facebook/mask2former-swin-large-ade-panoptic (production's)",
            "door_m": rm.DOOR_HEIGHT_M,
            "counter_m": rm.COUNTER_HEIGHT_M,
            "camera_height_m": rm.CAMERA_HEIGHT_M,
        },
        run,
    )


DEPTH_MODELS: Final = {
    "moge2-s": "Ruicheng/moge-2-vits-normal (MIT), field of view estimated",
    "moge2-s-fov": "Ruicheng/moge-2-vits-normal (MIT), the photo's field of view given",
    "da3-metric": "depth-anything/DA3METRIC-LARGE (Apache-2.0), the photo's focal length given",
    "da3-small-mv": "depth-anything/DA3-SMALL (Apache-2.0), all the room's photos at once",
}


def depth_setups(base: Path) -> list[Setup]:
    out: list[Setup] = []
    for key, model in DEPTH_MODELS.items():
        depth = base / "depth" / key
        if not depth.is_dir():
            continue
        if key == "da3-small-mv":
            out += [
                depth_setup(
                    "M4c.da3s.ref",
                    f"Multi-view shape ({key}), scaled by doors and counters",
                    depth,
                    model,
                    "references",
                    registered=True,
                ),
                depth_setup(
                    "M4c.da3s.cam",
                    f"Multi-view shape ({key}), scaled by a 1.5 m camera height",
                    depth,
                    model,
                    "camera_height",
                    registered=True,
                ),
                depth_setup(
                    "M4c.da3s.door",
                    f"Multi-view shape ({key}), scaled by doors only",
                    depth,
                    model,
                    "doors",
                    registered=True,
                ),
            ]
            continue
        out += [
            depth_setup(
                f"M4a.{key}.ref",
                f"Depth shape ({key}), scaled by doors and counters",
                depth,
                model,
                "references",
            ),
            depth_setup(
                f"M4a.{key}.cam",
                f"Depth shape ({key}), scaled by a 1.5 m camera height",
                depth,
                model,
                "camera_height",
            ),
            depth_setup(
                f"M4a.{key}.door",
                f"Depth model's metres checked by doors ({key})",
                depth,
                model,
                "doors_checked",
            ),
            depth_setup(f"M4b.{key}", f"Depth model's own metres ({key})", depth, model, "model"),
        ]
    return out


# ------------------------------------------------------------------ scoring


@dataclass(frozen=True, slots=True)
class Score:
    setup: str
    room: str
    room_type: str
    quantity: str
    predicted: float
    truth: float

    @property
    def signed_pct(self) -> float:
        return 100 * (self.predicted - self.truth) / self.truth

    @property
    def abs_pct(self) -> float:
        return abs(self.signed_pct)


def score(
    setup: Setup, rooms: Sequence[Room], results: Mapping[str, Result]
) -> tuple[list[Score], dict[str, list[str]]]:
    scores: list[Score] = []
    misses: dict[str, list[str]] = {}
    for room in rooms:
        got = results[room.id].quantities
        for q, truth in room.truth.items():
            if q in got and math.isfinite(got[q]) and got[q] > 0:
                scores.append(Score(setup.id, room.id, room.room_type, q, got[q], truth))
            else:
                misses.setdefault(q, []).append(room.id)
    return scores, misses


def summarize(
    scores: Sequence[Score], misses: Mapping[str, Mapping[str, list[str]]]
) -> list[dict[str, Any]]:
    rows = []
    for setup in sorted({s.setup for s in scores} | set(misses)):
        for q in QUANTITIES:
            mine = [s for s in scores if s.setup == setup and s.quantity == q]
            missed = len(misses.get(setup, {}).get(q, []))
            if not mine and not missed:
                continue
            signed = [s.signed_pct for s in mine]
            absolute = [s.abs_pct for s in mine]
            worst = max(mine, key=lambda s: s.abs_pct) if mine else None
            rows.append(
                {
                    "setup": setup,
                    "quantity": q,
                    "rooms": len(mine),
                    "misses": missed,
                    "mean_signed_pct": statistics.fmean(signed) if signed else None,
                    "sd_signed_pct": statistics.stdev(signed) if len(signed) > 1 else None,
                    "mean_abs_pct": statistics.fmean(absolute) if absolute else None,
                    "median_abs_pct": statistics.median(absolute) if absolute else None,
                    "worst_abs_pct": worst.abs_pct if worst else None,
                    "worst_room": worst.room if worst else None,
                    "within_10_pct": sum(a <= 10 for a in absolute),
                }
            )
    return rows


def reconcile(scores: Sequence[Score]) -> list[dict[str, Any]]:
    """Each setup's floor areas summed over the rooms it measured, against the truth's sum, and
    the per-room error after rescaling them so that the sum is right."""
    out = []
    for setup in sorted({s.setup for s in scores}):
        mine = [s for s in scores if s.setup == setup and s.quantity == "floor_area_sf"]
        if not mine:
            continue
        total_p = sum(s.predicted for s in mine)
        total_t = sum(s.truth for s in mine)
        k = total_t / total_p
        after = [abs(100 * (s.predicted * k - s.truth) / s.truth) for s in mine]
        out.append(
            {
                "setup": setup,
                "rooms": len(mine),
                "sum_predicted_sf": total_p,
                "sum_truth_sf": total_t,
                "sum_error_pct": 100 * (total_p - total_t) / total_t,
                "mean_abs_pct_before": statistics.fmean(s.abs_pct for s in mine),
                "mean_abs_pct_after": statistics.fmean(after),
                "median_abs_pct_after": statistics.median(after),
            }
        )
    return out


def paired(scores: Sequence[Score], a: str, b: str, quantity: str) -> dict[str, Any] | None:
    """Setup ``a``'s absolute error minus ``b``'s on the rooms both measured: mean, t, wins."""
    sa = {s.room: s.abs_pct for s in scores if s.setup == a and s.quantity == quantity}
    sb = {s.room: s.abs_pct for s in scores if s.setup == b and s.quantity == quantity}
    common = sorted(set(sa) & set(sb))
    if len(common) < 2:
        return None
    d = [sa[r] - sb[r] for r in common]
    mean = statistics.fmean(d)
    sd = statistics.stdev(d)
    t = mean / (sd / math.sqrt(len(d))) if sd > 0 else math.inf
    return {"rooms": len(d), "mean_diff_pct": mean, "t": t, "a_better": sum(x < 0 for x in d)}


def _g(v: Any, digits: int = 1) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return str(v)


def render(
    setups: Sequence[Setup],
    rooms: Sequence[Room],
    summary: Sequence[Mapping[str, Any]],
    costs: Mapping[str, Mapping[str, Any]],
    rec: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    refusals: Mapping[str, Mapping[str, str]],
) -> str:
    names = {s.id: s.name for s in setups}
    types: dict[str, int] = {}
    for room in rooms:
        types[room.room_type] = types.get(room.room_type, 0) + 1
    lines = [
        "## M4, interiors (EVAL_REPORT 107)",
        "",
        f"Rooms: {len(rooms)} ({', '.join(f'{v} {k}' for k, v in sorted(types.items()))}).",
        "",
        "Columns: *rooms*: rooms scored; *misses*: rooms with a truth the setup gave no number "
        "for; *mean signed*: the bias (negative: low); *SD*: the spread of the signed error; "
        "*within 10 %*: rooms within 10 % of the truth; *worst*: the largest absolute error.",
        "",
    ]
    for q, label in QUANTITIES.items():
        mine = [r for r in summary if r["quantity"] == q]
        if not mine:
            continue
        lines += [
            f"### {label}",
            "",
            "| Setup | Rooms | Misses | Mean signed % | SD % | Mean abs % | Median abs % | "
            "Within 10 % | Worst abs % (room) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in mine:
            lines.append(
                f"| {names.get(r['setup'], r['setup'])} | {r['rooms']} | {r['misses']} | "
                f"{_g(r['mean_signed_pct'])} | {_g(r['sd_signed_pct'])} | "
                f"{_g(r['mean_abs_pct'])} | "
                f"{_g(r['median_abs_pct'])} | {r['within_10_pct']} | "
                f"{_g(r['worst_abs_pct'])} ({r['worst_room'] or ''}) |"
            )
        lines.append("")
    lines += [
        "### Cost, tokens and time per room",
        "",
        "| Setup | Rooms run | $ / room | Input tokens / room | Output tokens / room | "
        "Seconds / room |",
        "|---|---|---|---|---|---|",
    ]
    for sid, c in costs.items():
        lines.append(
            f"| {names.get(sid, sid)} | {c['rooms']} | "
            f"{c['cost_usd'] / max(1, c['rooms']):.4f} | "
            f"{_g(c['input_tokens'] / c['rooms'] if c['input_tokens'] else None, 0)} | "
            f"{_g(c['output_tokens'] / c['rooms'] if c['output_tokens'] else None, 0)} | "
            f"{c['seconds'] / max(1, c['rooms']):.2f} |"
        )
    lines += [
        "",
        "### Reconciliation: the floor areas summed",
        "",
        "Columns: *sum error*: the setup's floor areas summed over the rooms it measured, "
        "against the truth's sum; *after*: each room's error once the areas are rescaled so the "
        "sum is right (reconciling to a known total, the GLA's role, at its best).",
        "",
        "| Setup | Rooms | Sum error % | Mean abs % before | Mean abs % after | "
        "Median abs % after |",
        "|---|---|---|---|---|---|",
    ]
    for r in rec:
        lines.append(
            f"| {names.get(r['setup'], r['setup'])} | {r['rooms']} | {r['sum_error_pct']:+.1f} | "
            f"{r['mean_abs_pct_before']:.1f} | {r['mean_abs_pct_after']:.1f} | "
            f"{r['median_abs_pct_after']:.1f} |"
        )
    if pairs:
        lines += [
            "",
            "### Paired against M0 (true floor area)",
            "",
            "Columns: the setup's absolute error minus M0's on the rooms both measured (negative: "
            "better); *t*: the mean over its standard error; *better*: rooms where the setup was "
            "closer.",
            "",
            "| Setup | Quantity | Rooms | Mean difference % | t | Better |",
            "|---|---|---|---|---|---|",
        ]
        for p in pairs:
            lines.append(
                f"| {names.get(p['setup'], p['setup'])} | {p['quantity']} | {p['rooms']} | "
                f"{p['mean_diff_pct']:+.1f} | {p['t']:.1f} | {p['a_better']}/{p['rooms']} |"
            )
    refused_lines = [
        f"| {names.get(sid, sid)} | {len(r)} | {', '.join(sorted(set(r.values())))} |"
        for sid, r in refusals.items()
        if r
    ]
    if refused_lines:
        lines += [
            "",
            "### Refusals",
            "",
            "| Setup | Rooms | Why |",
            "|---|---|---|",
            *refused_lines,
        ]
    return "\n".join(lines) + "\n"


def evaluate(
    setups: Sequence[Setup],
    rooms: Sequence[Room],
    extra: Mapping[str, Mapping[str, Result]] | None = None,
) -> dict[str, Any]:
    """Run every setup on every room (or take ``extra``'s recorded results) and score them."""
    all_scores: list[Score] = []
    misses: dict[str, dict[str, list[str]]] = {}
    costs: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Result]] = {}
    refusals: dict[str, dict[str, str]] = {}
    for setup in setups:
        mine = (
            dict(extra[setup.id])
            if extra and setup.id in extra
            else {r.id: setup.run(r) for r in rooms}
        )
        mine = {r.id: mine.get(r.id, Result({}, notes={"refused": "not run"})) for r in rooms}
        results[setup.id] = mine
        s, m = score(setup, rooms, mine)
        all_scores += s
        misses[setup.id] = m
        refusals[setup.id] = {
            rid: str(res.notes["refused"]) for rid, res in mine.items() if res.notes.get("refused")
        }
        ran = [res for res in mine.values() if res.quantities or res.cost_usd]
        costs[setup.id] = {
            "rooms": len(ran),
            "cost_usd": sum(res.cost_usd for res in mine.values()),
            "input_tokens": sum(res.input_tokens or 0 for res in mine.values()) or None,
            "output_tokens": sum(res.output_tokens or 0 for res in mine.values()) or None,
            "seconds": sum(res.seconds for res in mine.values()),
        }
    summary = summarize(all_scores, misses)
    pairs = []
    for setup in setups:
        if setup.id == "M0":
            continue
        for q in QUANTITIES:
            p = paired(all_scores, setup.id, "M0", q)
            if p is not None and q != "floor_area_sf":
                pairs.append({"setup": setup.id, "quantity": q, **p})
    return {
        "scores": all_scores,
        "summary": summary,
        "misses": misses,
        "costs": costs,
        "reconcile": reconcile(all_scores),
        "pairs": pairs,
        "refusals": refusals,
        "results": results,
    }


__all__ = [
    "DEPTH_MODELS",
    "QUANTITIES",
    "Result",
    "Room",
    "Score",
    "Setup",
    "depth_setup",
    "depth_setups",
    "evaluate",
    "formulas",
    "labels_for",
    "load_rooms",
    "load_views",
    "m0_setups",
    "paired",
    "reconcile",
    "render",
    "score",
    "summarize",
]
