"""M3 of AI_MEASUREMENT_PLAN.md section 5 (EVAL_REPORT 109): facade quantities from ground
photos, scaled by the wall's known length.

Every setup measures one facade (:mod:`evals.measure_facade_truth`) on one photo of it and is
scored against the facade's truth: the eave height (a cross-check of M1's eave), the gross face,
the openings' area, the face net of the openings (the siding), the openings' trim length, the
window and door counts, and each opening's width and height where it matches a true one.

**The setups** (:data:`SETUPS`):

* ``M3.engineer``: single-view metrology (:func:`basis.geo.facade.measure`, Criminisi-style
  rectification) on the corners an engineer placed on the photo (``clicks/engineer/``, placed
  without the drawings, ``work/CLICK_FACADE.md``), the camera's field of view from the photo's
  EXIF focal length (or the capture's), the wall's length from the drawing. The method's
  ceiling with a careful hand.
* ``M3.engineer.vp``: the same clicks, the focal length from the wall's own vanishing points
  (no EXIF).
* ``M3.engineer.m1``: the same clicks, the wall's length from M1's lidar walls
  (:func:`m1_wall_length`): end to end with no drawing.
* ``M3.<model>.corners``: the vision model places the corners (``measure_facade_corners/v1``),
  the metrology measures them (field of view from EXIF; length from the drawing; ``.m1``: from
  M1).
* ``M3.<model>.direct``: the vision model is given the wall's length and asked for the opening
  sizes and the eave height in feet (``measure_facade_direct/v1``).
* ``M0.15pct``: today's formula: openings a flat 15 % of the gross face
  (:data:`basis.domain.measurements.WALL_OPENINGS_SHARE`), the gross face the wall's length
  times 9 ft a storey (``M0.15pct``) or times the true eave (``M0.15pct.true-eave``: the share
  alone), no counts.
* ``M1.eave``: M1's main eave over this facade (the eave cross-check only).
* Variant (b) of the brief, a monocular depth model's shape scaled by the wall's length, was
  not run: the disk had under 1.5 GB free (EVAL_REPORT 109.6).

Paid calls go through :func:`basis.llm.call.call_model` with the run's ledger, as M5
(:mod:`evals.measure_vlm`): the production photo preparation (1,024 px JPEG, EXIF stripped), the
models at their production calls' settings, every row in ``llm_calls.jsonl``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import statistics
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from basis.domain import measurements as ms
from basis.geo import facade as fc
from basis.geo import shell
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import estimate_usd
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.llm.recorder import CallRecord
from basis.vision.prep import prepare_image
from evals import measure_facade_truth as ft
from evals import measure_truth as mt
from evals import measure_vlm

FT_M: Final = 0.3048
M_FT: Final = 1 / FT_M
QUANTITIES: Final[dict[str, str]] = {
    "eave_height_ft": "eave height above grade (ft)",
    "gross_sf": "gross wall face, length x eave (SF)",
    "openings_sf": "openings, trim outline (SF)",
    "net_sf": "net face = gross - openings (SF, the siding)",
    "trim_lf": "openings' trim perimeter (LF)",
}
# The wall's outward normal, degrees clockwise from true north, for picking M1's wall: the
# sides as the drawings name them; ga0157's drawings are turned about 45 degrees from true
# (its HABS photos call the drawing's west side the north-west side, EVAL_REPORT 109.1).
AZIMUTH_DEG: Final[dict[str, float]] = {
    "mo1857-west": 270.0,
    "mo1857-north": 0.0,
    "mo1857-south": 180.0,
    "mo1222-north": 0.0,
    "mo1224-east": 90.0,
    "md1464-east": 90.0,
    "ga0157-west": 315.0,
    "ga0157-east": 135.0,
}
# Each house's eave line, in the words the clicks and the prompts use (work/CLICK_FACADE.md).
EAVE_WORDS: Final[dict[str, str]] = {
    "mo1857": "the top edge of the box cornice, where the roof surface begins",
    "ga0157": "the underside of the boxed eave (the soffit) where it meets the wall",
    "mo1222": "the line where the wall meets the underside of the roof's overhang",
    "mo1224": "the line where the wall meets the underside of the roof's overhang",
    "md1464": "the line where the wall meets the underside of the roof's overhang",
}
MATCH_FT: Final = 1.5  # an opening's centre within this (or half its width) of a true one
# Facades whose wall is the whole side of the building as the lidar sees it from above (no wing,
# porch or block beside it on that side): only there is M1's side width this wall's length
# (EVAL_REPORT 109.4). The others need the engineer to pick the wall on the diagram.
WHOLE_SIDE: Final = frozenset({"mo1857-west", "mo1222-north"})
PROMPTS: Final[dict[str, str]] = {
    "corners": "measure_facade_corners",
    "direct": "measure_facade_direct",
}
PROMPT_VERSION: Final = "v1"


def data_root() -> Path:
    return Path(os.environ.get("BASIS_EVAL_DATA_DIR") or Path.home() / "basis-eval-data")


# ------------------------------------------------------------------ the drafts


@dataclass(frozen=True, slots=True)
class Draft:
    """Corners on a photo, in its stored pixels."""

    wall: fc.Quad | None
    openings: tuple[fc.Opening, ...]
    occluded_share: float | None
    note: str = ""


def _quad(d: Mapping[str, Sequence[float]]) -> fc.Quad:
    return fc.Quad(*(tuple(float(v) for v in d[k][:2]) for k in ("bl", "br", "tr", "tl")))  # type: ignore[arg-type]


def draft_from_clicks(data: Mapping[str, Any], scale: float = 1.0) -> Draft:
    """A clicks file (or a corners answer scaled by ``scale`` to the stored pixels)."""

    def scaled(d: Mapping[str, Any]) -> fc.Quad:
        q = _quad(d)
        return fc.Quad(*((x * scale, y * scale) for x, y in q.points()))

    visible = data.get("visible", data.get("wall_visible", True))
    wall = data.get("wall")
    openings = tuple(
        fc.Opening(o.get("kind", "window"), scaled(o))
        for o in data.get("openings") or []
        if all(k in o for k in ("bl", "br", "tr", "tl"))
    )
    occ = data.get("occluded_share")
    return Draft(
        wall=scaled(wall) if visible and wall else None,
        openings=openings,
        occluded_share=None if occ is None else float(occ),
        note=str(data.get("notes") or ""),
    )


@dataclass(frozen=True, slots=True)
class Measured:
    """One setup on one facade and photo: the quantities, the openings found, the refusal."""

    quantities: dict[str, float]
    openings: tuple[tuple[str, float, float, float, float], ...] = ()  # kind, w, h, left, sill
    windows: int | None = None
    doors: int | None = None
    refused: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def from_facade(f: fc.Facade) -> Measured:
    if f.code is not None or f.height_m is None:
        why = "; ".join(f"{code}: {why}" for _, code, why in f.refusals)
        return Measured({}, refused=why or "refused", detail={"view_deg": f.view_deg})
    q: dict[str, float] = {"eave_height_ft": f.height_m * M_FT}
    assert f.gross_m2 is not None
    q["gross_sf"] = f.gross_m2 * M_FT * M_FT
    detail = {
        "focal_from": f.focal_from,
        "focal_px": f.focal_px,
        "view_deg": f.view_deg,
        "skew_deg": f.skew_deg,
        "expected_error_pct": f.expected_error_pct,
        "left_out": list(f.left_out),
    }
    if f.openings_m2 is None:
        return Measured(q, refused=None, detail=detail | {"openings": "refused (occluded)"})
    assert f.net_m2 is not None and f.trim_m is not None
    q["openings_sf"] = f.openings_m2 * M_FT * M_FT
    q["net_sf"] = f.net_m2 * M_FT * M_FT
    q["trim_lf"] = f.trim_m * M_FT
    ops = tuple(
        (o.kind, o.width_m * M_FT, o.height_m * M_FT, o.left_m * M_FT, o.sill_m * M_FT)
        for o in f.openings
    )
    return Measured(q, ops, f.windows, f.doors, detail=detail)


def measure_draft(
    photo: ft.Photo, draft: Draft, length_ft: float, hfov: float | None | Literal["exif"]
) -> Measured:
    if draft.wall is None:
        return Measured({}, refused="the wall could not be placed on this photo")
    camera = fc.Camera(photo.width_px, photo.height_px, photo.hfov_deg if hfov == "exif" else hfov)
    return from_facade(
        fc.measure(
            camera,
            draft.wall,
            length_ft * FT_M,
            draft.openings,
            occluded_share=draft.occluded_share,
        )
    )


# ------------------------------------------------------------------ M1's wall and eave


@dataclass(frozen=True, slots=True)
class M1Wall:
    length_ft: float | None
    eave_ft: float | None
    note: str


def m1_wall_length(
    walls: Sequence[tuple[float, float]], azimuth_deg: float, *, spread_deg: float = 60.0
) -> tuple[float, int] | None:
    """The width of the side of M1's wall outline that faces ``azimuth_deg``, as an elevation
    of that side shows it: the edges whose outward normal is within ``spread_deg`` of the
    azimuth, projected on the horizontal axis of that side, from the first end to the last
    (the simplified lidar outline cuts corners into short diagonal edges, so a single edge is
    not a wall: EVAL_REPORT 109.4). Returns the width (m) and how many edges made it."""
    if len(walls) < 3:
        return None
    ring = list(walls)
    if shell.ring_area(ring) < 0:
        ring.reverse()
    az = math.radians(azimuth_deg)
    want = (math.sin(az), math.cos(az))  # east, north
    axis = (want[1], -want[0])
    ends: list[float] = []
    used = 0
    for i, (x0, y0) in enumerate(ring):
        x1, y1 = ring[(i + 1) % len(ring)]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 0.3:
            continue
        normal = (dy / length, -dx / length)  # outward for a counter-clockwise ring
        if normal[0] * want[0] + normal[1] * want[1] < math.cos(math.radians(spread_deg)):
            continue
        used += 1
        ends += [x0 * axis[0] + y0 * axis[1], x1 * axis[0] + y1 * axis[1]]
    if not ends:
        return None
    return max(ends) - min(ends), used


def m1_walls(geo: Path, facades: Sequence[ft.Facade]) -> dict[str, M1Wall]:
    """M1 (EVAL_REPORT 105's shipped setup) on each facade's house: the facing wall's length and
    the main eave."""
    from evals import measure_eval

    houses = {h.id: h for h in mt.load_all()}
    out: dict[str, M1Wall] = {}
    shells: dict[str, shell.Shell | None] = {}
    for f in facades:
        if f.house not in shells:
            got = measure_eval.house_points(geo, f.house)
            if got is None:
                shells[f.house] = None
            else:
                points, meta = got
                h = houses[f.house]
                shells[f.house] = shell.derive(
                    points,
                    h.location.lon,
                    h.location.lat,
                    measure_eval.mercator_box(meta["box_lonlat"]),
                )
        s = shells[f.house]
        if s is None:
            out[f.id] = M1Wall(None, None, "no lidar points")
            continue
        eave = s.eave_main_m * M_FT if s.eave_main_m else None
        picked = m1_wall_length(s.wall_outline_m, AZIMUTH_DEG[f.id])
        if picked is None:
            out[f.id] = M1Wall(None, eave, f"no wall facing {AZIMUTH_DEG[f.id]:.0f} deg ({s.code})")
            continue
        length, index = picked
        out[f.id] = M1Wall(
            length * M_FT,
            eave,
            f"{index} facing edges of {len(s.wall_outline_m)}, {s.classification}",
        )
    return out


# ------------------------------------------------------------------ the vision model


class CornerPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


class CornerQuad(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bl: CornerPoint
    br: CornerPoint
    tr: CornerPoint
    tl: CornerPoint


class CornerOpening(CornerQuad):
    kind: Literal["window", "door", "garage"]


class CornersAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wall_visible: bool
    wall: CornerQuad | None
    openings: list[CornerOpening] = Field(max_length=60)
    occluded_share: float = Field(ge=0, le=1)
    notes: str = Field(max_length=600)


class DirectOpening(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["window", "door", "garage"]
    width_ft: float = Field(gt=0, le=40)
    height_ft: float = Field(gt=0, le=30)
    left_ft: float
    sill_ft: float


class DirectAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wall_visible: bool
    eave_height_ft: float | None = Field(default=None, gt=0, le=80)
    openings: list[DirectOpening] = Field(max_length=60)
    occluded_share: float = Field(ge=0, le=1)
    notes: str = Field(max_length=600)


ANSWERS: Final[dict[str, type[BaseModel]]] = {"corners": CornersAnswer, "direct": DirectAnswer}


@dataclass(frozen=True, slots=True)
class VlmSetup:
    model: measure_vlm.ModelSetup
    prompt: Literal["corners", "direct"]

    @property
    def id(self) -> str:
        return f"M3.{self.model.key}.{self.prompt}"

    def settings(self) -> dict[str, Any]:
        p = prompts.load(PROMPTS[self.prompt], PROMPT_VERSION)
        return {
            "model": self.model.model,
            "reasoning_effort": self.model.effort,
            "answer_budget_tokens": self.model.max_tokens,
            "temperature": "not sent (reasoning models refuse it)",
            "prompt": p.version_id,
            "prompt_sha256": p.sha256,
            "production_call": self.model.production_call,
            "inputs": "one photo, production preparation (1024 px JPEG, EXIF stripped); which "
            "wall; the eave line's words"
            + ("; the wall's length" if self.prompt == "direct" else ""),
        }


def parse_setups(text: str, models: Mapping[str, measure_vlm.ModelSetup]) -> list[VlmSetup]:
    out: list[VlmSetup] = []
    for part in (p.strip() for p in text.split(",") if p.strip()):
        mk, _, pk = part.partition(":")
        for m in list(models) if mk in ("*", "") else [mk]:
            if m not in models:
                raise ValueError(f"unknown model {m!r}; known {list(models)}")
            for p in list(PROMPTS) if pk in ("*", "") else [pk]:
                if p not in PROMPTS:
                    raise ValueError(f"unknown M3 prompt {p!r}")
                s = VlmSetup(models[m], p)  # type: ignore[arg-type]
                if s not in out:
                    out.append(s)
    return out


@dataclass(frozen=True, slots=True)
class Prepared:
    jpeg: bytes
    width: int
    height: int
    scale: float  # stored px per prepared px


def prepare(photo: ft.Photo) -> Prepared:
    path = data_root() / "measure_truth" / "photos" / photo.path
    p = prepare_image(path.read_bytes())
    return Prepared(p.jpeg, p.width, p.height, photo.width_px / p.width)


def message(setup: VlmSetup, f: ft.Facade, photo: ft.Photo, prepared: Prepared) -> str:
    lines = [
        f"The photo is {prepared.width} x {prepared.height} pixels.",
        f"The wall to {'mark' if setup.prompt == 'corners' else 'measure'}: {photo.which}.",
        f"Its top, the eave line on this house: {EAVE_WORDS[f.house]}.",
    ]
    if setup.prompt == "direct":
        lines.append(
            f"The wall is {f.length.ft:.1f} ft long between its two outside corners "
            "(measured by another method)."
        )
    return "\n".join(lines)


def build_request(
    setup: VlmSetup,
    f: ft.Facade,
    photo: ft.Photo,
    prepared: Prepared,
    *,
    study_id: str,
    activity: str,
) -> ChatRequest:
    p = prompts.load(PROMPTS[setup.prompt], PROMPT_VERSION)
    return ChatRequest(
        model=setup.model.model,
        system=p.text,
        messages=(
            Message(
                role="user",
                content=message(setup, f, photo, prepared),
                images=(
                    ImagePart(
                        data_base64=base64.b64encode(prepared.jpeg).decode("ascii"),
                        detail="high",
                        ref=f"{f.id}:{photo.id}",
                    ),
                ),
            ),
        ),
        response_model=ANSWERS[setup.prompt],
        max_tokens=setup.model.max_tokens,
        reasoning_effort=setup.model.effort,
        prompt_version=p.version_id,
        study_id=study_id,
        activity=activity,
    )


def estimate_job_usd(setup: VlmSetup, f: ft.Facade, photo: ft.Photo, prepared: Prepared) -> float:
    r = build_request(setup, f, photo, prepared, study_id="estimate", activity="estimate")
    chars = len(r.system) + sum(len(m.content) for m in r.messages)
    return estimate_usd(r.model, r.max_tokens, chars, 1)


@dataclass
class Outcome:
    setup: str
    facade: str
    photo: str
    repeat: int
    answer: dict[str, Any] | None = None
    scale: float = 1.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0
    models: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_one(
    provider: ChatProvider,
    ctx: CallContext,
    setup: VlmSetup,
    f: ft.Facade,
    photo: ft.Photo,
    prepared: Prepared,
    repeat: int,
    records: Sequence[CallRecord],
) -> Outcome:
    from evals.predict_multiview import call_with_retries

    entity = uuid.uuid4()
    job_ctx = ctx.about("measure_facade", entity)
    request = build_request(
        setup, f, photo, prepared, study_id=str(ctx.study_id), activity=ctx.activity
    )
    started = time.monotonic()
    parsed, attempts = await call_with_retries(provider, request, job_ctx)
    out = Outcome(setup.id, f.id, photo.id, repeat, scale=prepared.scale)
    out.seconds = time.monotonic() - started
    out.errors = list(attempts.get("errors") or [])
    for key, value in measure_vlm.ledger_totals(records, entity).items():
        setattr(out, key, value)
    if parsed is not None:
        out.answer = parsed.model_dump(mode="json")
    return out


async def run_jobs(
    provider: ChatProvider,
    ctx: CallContext,
    records: Sequence[CallRecord],
    jobs: Sequence[tuple[VlmSetup, ft.Facade, ft.Photo, Prepared, int]],
    concurrency: int = 4,
) -> list[Outcome]:
    sem = asyncio.Semaphore(concurrency)

    async def one(job: tuple[VlmSetup, ft.Facade, ft.Photo, Prepared, int]) -> Outcome:
        async with sem:
            return await run_one(provider, ctx, *job, records=records)

    return list(await asyncio.gather(*(one(j) for j in jobs)))


def measured_direct(answer: Mapping[str, Any], length_ft: float) -> Measured:
    if not answer.get("wall_visible") or answer.get("eave_height_ft") is None:
        return Measured({}, refused="the model saw no wall")
    eave = float(answer["eave_height_ft"])
    ops = [
        (
            o["kind"],
            float(o["width_ft"]),
            float(o["height_ft"]),
            float(o["left_ft"]),
            float(o["sill_ft"]),
        )
        for o in answer.get("openings") or []
    ]
    area = sum(w * h for _, w, h, _, _ in ops)
    gross = length_ft * eave
    q = {
        "eave_height_ft": eave,
        "gross_sf": gross,
        "openings_sf": area,
        "net_sf": gross - area,
        "trim_lf": sum(2 * (w + h) for _, w, h, _, _ in ops),
    }
    return Measured(
        q,
        tuple(ops),
        sum(1 for o in ops if o[0] == "window"),
        sum(1 for o in ops if o[0] != "window"),
    )


# ------------------------------------------------------------------ today's formula


def formula(f: ft.Facade, eave_ft: float) -> Measured:
    gross = f.length.ft * eave_ft
    share = float(ms.WALL_OPENINGS_SHARE)
    return Measured(
        {
            "eave_height_ft": eave_ft,
            "gross_sf": gross,
            "openings_sf": gross * share,
            "net_sf": gross * (1 - share),
        }
    )


# ------------------------------------------------------------------ scoring


@dataclass(frozen=True, slots=True)
class Score:
    setup: str
    facade: str
    photo: str
    quantity: str
    truth: float
    predicted: float
    signed_pct: float

    @property
    def abs_pct(self) -> float:
        return abs(self.signed_pct)


@dataclass(frozen=True, slots=True)
class Counts:
    setup: str
    facade: str
    photo: str
    true_openings: int
    found: int
    matched: int
    missed: int
    extra: int
    windows: tuple[int, int] | None  # (predicted, true)
    doors: tuple[int, int] | None
    width_abs_pct: tuple[float, ...]
    height_abs_pct: tuple[float, ...]


def match_openings(
    predicted: Sequence[tuple[str, float, float, float, float]], truth: Sequence[ft.Opening]
) -> list[tuple[int, int]]:
    """Greedy pairs (predicted index, true index) by centre distance, each within
    ``max(MATCH_FT, half the true width)``."""
    pairs: list[tuple[float, int, int]] = []
    for i, (_, w, h, left, sill) in enumerate(predicted):
        cx, cy = left + w / 2, sill + h / 2
        for j, o in enumerate(truth):
            tx, ty = o.left_ft + o.width_ft / 2, o.sill_ft + o.height_ft / 2
            d = math.hypot(cx - tx, cy - ty)
            if d <= max(MATCH_FT, o.width_ft / 2):
                pairs.append((d, i, j))
    used_i: set[int] = set()
    used_j: set[int] = set()
    out = []
    for _, i, j in sorted(pairs):
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        out.append((i, j))
    return out


def score(setup: str, f: ft.Facade, photo: str, got: Measured) -> tuple[list[Score], Counts | None]:
    truth = ft.truths(f)
    scores = [
        Score(setup, f.id, photo, q, truth[q].value, v, (v - truth[q].value) / truth[q].value * 100)
        for q, v in got.quantities.items()
        if q in truth and truth[q].value > 0 and math.isfinite(v)
    ]
    if got.windows is None:
        return scores, None
    pairs = match_openings(got.openings, f.openings)
    widths = tuple(
        abs(got.openings[i][1] - f.openings[j].width_ft) / f.openings[j].width_ft * 100
        for i, j in pairs
    )
    heights = tuple(
        abs(got.openings[i][2] - f.openings[j].height_ft) / f.openings[j].height_ft * 100
        for i, j in pairs
    )
    t = ft.truths(f)
    counts = Counts(
        setup,
        f.id,
        photo,
        len(f.openings),
        len(got.openings),
        len(pairs),
        len(f.openings) - len(pairs),
        len(got.openings) - len(pairs),
        (got.windows, int(t["windows"].value)),
        (got.doors or 0, int(t["doors"].value)),
        widths,
        heights,
    )
    return scores, counts


def summarize(scores: Sequence[Score]) -> list[dict[str, Any]]:
    rows = []
    for setup, q in dict.fromkeys((s.setup, s.quantity) for s in scores):
        chosen = [s for s in scores if s.setup == setup and s.quantity == q]
        signed = [s.signed_pct for s in chosen]
        absolute = [s.abs_pct for s in chosen]
        rows.append(
            {
                "setup": setup,
                "quantity": q,
                "n": len(chosen),
                "mean_signed_pct": statistics.fmean(signed),
                "mean_abs_pct": statistics.fmean(absolute),
                "median_abs_pct": statistics.median(absolute),
                "worst_abs_pct": max(absolute),
                "worst": max(chosen, key=lambda s: s.abs_pct).facade
                + "/"
                + max(chosen, key=lambda s: s.abs_pct).photo,
            }
        )
    return rows


# ------------------------------------------------------------------ the run


@dataclass(frozen=True, slots=True)
class Row:
    """One setup on one facade and photo, what it measured."""

    setup: str
    facade: str
    photo: str
    measured: Measured
    cost_usd: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float = 0.0


SETUP_NAMES: Final[dict[str, str]] = {
    "M3.engineer": "Metrology on an engineer's corners, EXIF field of view, the drawing's length",
    "M3.engineer.vp": "Metrology on an engineer's corners, focal length from the wall's "
    "vanishing points, the drawing's length",
    "M3.engineer.m1": "Metrology on an engineer's corners, EXIF field of view, M1's lidar wall "
    "length",
    "M0.15pct": "Today's formula: openings 15 % of the face, 9 ft a storey",
    "M0.15pct.true-eave": "Today's 15 % share on the true gross face (the share alone)",
    "M1.eave": "M1's main eave from lidar (eave only)",
}


def setup_name(setup: str, vlm: Mapping[str, VlmSetup]) -> str:
    if setup in SETUP_NAMES:
        return SETUP_NAMES[setup]
    base, _, rep = setup.partition("#")
    m1 = base.endswith(".m1")
    v = vlm.get(base.removesuffix(".m1"))
    if v is None:
        return setup
    what = (
        "places the corners, metrology measures"
        if v.prompt == "corners"
        else "given the length, states the sizes"
    )
    name = f"Vision model ({v.model.model}, effort {v.model.effort}) {what}"
    if m1:
        name += ", M1's lidar wall length"
    elif v.prompt == "corners":
        name += ", the drawing's length"
    return name + (f", repeat {rep}" if rep else "")


def engineer_rows(
    facades: Sequence[ft.Facade], clicks: Path, m1: Mapping[str, M1Wall] | None
) -> list[Row]:
    rows = []
    for f in facades:
        for photo in f.photos:
            path = clicks / f"{f.id}__{photo.id}.json"
            if not path.is_file():
                continue
            draft = draft_from_clicks(json.loads(path.read_text(encoding="utf-8")))
            started = time.perf_counter()
            got = measure_draft(photo, draft, f.length.ft, "exif")
            seconds = time.perf_counter() - started
            rows.append(Row("M3.engineer", f.id, photo.id, got, seconds=seconds))
            vp = measure_draft(photo, draft, f.length.ft, None)
            rows.append(Row("M3.engineer.vp", f.id, photo.id, vp))
            w = (m1 or {}).get(f.id)
            if w is not None and w.length_ft is not None and f.id in WHOLE_SIDE:
                got_m1 = measure_draft(photo, draft, w.length_ft, "exif")
                rows.append(Row("M3.engineer.m1", f.id, photo.id, got_m1))
    return rows


def baseline_rows(facades: Sequence[ft.Facade], m1: Mapping[str, M1Wall] | None) -> list[Row]:
    houses = {h.id: h for h in mt.load_all()}
    rows = []
    for f in facades:
        eave = float(ms.STORY_WALL_HEIGHT_FT) * houses[f.house].stories
        rows.append(Row("M0.15pct", f.id, "-", formula(f, eave)))
        rows.append(Row("M0.15pct.true-eave", f.id, "-", formula(f, f.eave.ft)))
        w = (m1 or {}).get(f.id)
        if w is not None and w.eave_ft is not None:
            rows.append(Row("M1.eave", f.id, "-", Measured({"eave_height_ft": w.eave_ft})))
    return rows


def vlm_rows(
    facades: Sequence[ft.Facade], outcomes: Sequence[Outcome], m1: Mapping[str, M1Wall] | None
) -> list[Row]:
    by_id = {f.id: f for f in facades}
    rows = []
    for o in outcomes:
        f = by_id[o.facade]
        photo = next(p for p in f.photos if p.id == o.photo)
        tag = "" if o.repeat == 1 else f"#{o.repeat}"
        paid: dict[str, Any] = {
            "cost_usd": o.cost_usd,
            "input_tokens": o.input_tokens,
            "output_tokens": o.output_tokens,
            "seconds": o.seconds,
        }
        if o.answer is None:
            got = Measured({}, refused="no usable answer: " + " | ".join(o.errors))
            rows.append(Row(o.setup + tag, f.id, photo.id, got, **paid))
            continue
        if o.setup.endswith(".corners"):
            draft = draft_from_clicks(o.answer, o.scale)
            got = measure_draft(photo, draft, f.length.ft, "exif")
            rows.append(Row(o.setup + tag, f.id, photo.id, got, **paid))
            w = (m1 or {}).get(f.id)
            if w is not None and w.length_ft is not None and f.id in WHOLE_SIDE:
                got_m1 = measure_draft(photo, draft, w.length_ft, "exif")
                rows.append(Row(o.setup + ".m1" + tag, f.id, photo.id, got_m1))
        else:
            got = measured_direct(o.answer, f.length.ft)
            rows.append(Row(o.setup + tag, f.id, photo.id, got, **paid))
    return rows


def vlm_jobs(
    facades: Sequence[ft.Facade], setups: Sequence[VlmSetup], repeats: int
) -> list[tuple[VlmSetup, ft.Facade, ft.Photo, Prepared, int]]:
    jobs = []
    for f in facades:
        for photo in f.photos:
            prepared = prepare(photo)
            for s in setups:
                for r in range(1, repeats + 1):
                    jobs.append((s, f, photo, prepared, r))
    return jobs


def score_all(
    facades: Sequence[ft.Facade], rows: Sequence[Row]
) -> tuple[list[Score], list[Counts]]:
    by_id = {f.id: f for f in facades}
    scores: list[Score] = []
    counts: list[Counts] = []
    for r in rows:
        s, c = score(r.setup, by_id[r.facade], r.photo, r.measured)
        scores += s
        if c is not None:
            counts.append(c)
    return scores, counts


def _g(v: Any, d: int = 1) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return "" if not math.isfinite(v) else f"{v:,.{d}f}"
    return str(v)


def render(
    facades: Sequence[ft.Facade],
    rows: Sequence[Row],
    scores: Sequence[Score],
    counts: Sequence[Counts],
    m1: Mapping[str, M1Wall] | None,
    vlm: Mapping[str, VlmSetup],
) -> str:
    name = {r.setup: setup_name(r.setup, vlm) for r in rows}
    lines = ["", "# M3: facades from ground photos (EVAL_REPORT 109)", "", "## The facades", ""]
    lines += [
        "| Facade | Length ft (how) | Eave ft (how) | Openings | Openings SF | Net SF | Photos "
        "| Disagreements |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in facades:
        t = ft.truths(f)
        lines.append(
            f"| {f.id} | {f.length.ft:.2f} ({f.length.how}) | {f.eave.ft:.2f} ({f.eave.how}) | "
            f"{int(t['windows'].value)} windows, {int(t['doors'].value)} doors | "
            f"{t['openings_sf'].value:.1f} | {t['net_sf'].value:.1f} | {len(f.photos)} | "
            f"{len(f.passes.disagreements)} |"
        )
    if m1:
        lines += ["", "## M1's wall per facade (the scale input of the .m1 setups)", ""]
        lines += [
            "| Facade | True length ft | M1 length ft | Signed % | M1 main eave ft | True eave ft "
            "| Note |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in facades:
            w = m1.get(f.id)
            if w is None:
                continue
            signed = (w.length_ft / f.length.ft - 1) * 100 if w.length_ft else None
            lines.append(
                f"| {f.id} | {f.length.ft:.2f} | {_g(w.length_ft, 2)} | {_g(signed)} | "
                f"{_g(w.eave_ft, 2)} | {f.eave.ft:.2f} | {w.note} |"
            )
    lines += ["", "## Error per quantity", ""]
    lines += [
        "Columns: *n* facade-photo pairs the setup measured (refusals not counted); *mean "
        "signed* the bias, (setup - truth) / truth; *mean*, *median*, *worst* absolute.",
        "",
        "| Setup | Quantity | n | Mean signed % | Mean abs % | Median abs % "
        "| Worst abs % (where) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in summarize(scores):
        lines.append(
            f"| {name.get(r['setup'], r['setup'])} | "
            f"{QUANTITIES.get(r['quantity'], r['quantity'])} | {r['n']} | "
            f"{_g(r['mean_signed_pct'])} | {_g(r['mean_abs_pct'])} | "
            f"{_g(r['median_abs_pct'])} | {_g(r['worst_abs_pct'])} ({r['worst']}) |"
        )
    lines += ["", "## Openings found", ""]
    lines += [
        "Columns: pairs the setup measured openings on; *true* and *found* openings summed; "
        "*missed* true openings with no match (centre within 1.5 ft or half the width), *extra* "
        "found with no true one; window and door counts right: pairs whose count equals the "
        "truth's; width and height: mean absolute % over the matched openings.",
        "",
        "| Setup | Pairs | True | Found | Missed | Extra | Window count right | Door count right "
        "| Width abs % | Height abs % |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for setup in dict.fromkeys(c.setup for c in counts):
        cs = [c for c in counts if c.setup == setup]
        widths = [w for c in cs for w in c.width_abs_pct]
        heights = [h for c in cs for h in c.height_abs_pct]
        win = sum(1 for c in cs if c.windows and c.windows[0] == c.windows[1])
        door = sum(1 for c in cs if c.doors and c.doors[0] == c.doors[1])
        lines.append(
            f"| {name.get(setup, setup)} | {len(cs)} | {sum(c.true_openings for c in cs)} | "
            f"{sum(c.found for c in cs)} | {sum(c.missed for c in cs)} | "
            f"{sum(c.extra for c in cs)} | {win}/{len(cs)} | {door}/{len(cs)} | "
            f"{_g(statistics.fmean(widths) if widths else None)} | "
            f"{_g(statistics.fmean(heights) if heights else None)} |"
        )
    lines += ["", "## Refusals", "", "| Setup | Facade | Photo | Why |", "|---|---|---|---|"]
    for row in rows:
        if row.measured.refused:
            lines.append(
                f"| {name.get(row.setup, row.setup)} | {row.facade} | {row.photo} | "
                f"{row.measured.refused[:200]} |"
            )
    setups = list(dict.fromkeys(r.setup for r in rows))
    pairs = list(dict.fromkeys((r.facade, r.photo) for r in rows if r.photo != "-"))
    by = {(s.setup, s.facade, s.photo, s.quantity): s for s in scores}
    lines += ["", "## Per facade and photo (signed %: eave / openings SF / net SF)", ""]
    lines += ["| Facade / photo | " + " | ".join(setups) + " |", "|---|" + "---|" * len(setups)]
    for fid, ph in pairs:
        cells = []
        for setup in setups:
            got = []
            for q in ("eave_height_ft", "openings_sf", "net_sf"):
                s = by.get((setup, fid, ph, q)) or by.get((setup, fid, "-", q))
                got.append(_g(s.signed_pct, 0) if s else "-")
            cells.append(" / ".join(got))
        lines.append(f"| {fid} / {ph} | " + " | ".join(cells) + " |")
    lines += ["", "## Run to run (vision models)", ""]
    lines += [
        "Columns: pairs with both repeats; mean (largest) of |repeat 2 - repeat 1| in % of the "
        "truth.",
        "",
        "| Setup | Pairs | Eave | Openings SF | Net SF |",
        "|---|---|---|---|---|",
    ]
    for setup in setups:
        if "#" in setup or not setup.startswith("M3.") or setup.startswith("M3.engineer"):
            continue
        cells = []
        n = 0
        for q in ("eave_height_ft", "openings_sf", "net_sf"):
            diffs = []
            for fid, ph in pairs:
                a, b = by.get((setup, fid, ph, q)), by.get((setup + "#2", fid, ph, q))
                if a and b:
                    diffs.append(abs(a.predicted - b.predicted) / a.truth * 100)
            n = max(n, len(diffs))
            cells.append(f"{_g(statistics.fmean(diffs))} ({_g(max(diffs))})" if diffs else "")
        if n:
            lines.append(f"| {name.get(setup, setup)} | {n} | " + " | ".join(cells) + " |")
    lines += ["", "## Cost per facade-photo", ""]
    lines += [
        "| Setup | Rows | Dollars | Input tokens (mean) | Output tokens (mean) | Seconds (mean) |",
        "|---|---|---|---|---|---|",
    ]
    for setup in setups:
        mine = [r for r in rows if r.setup == setup]
        ti = [r.input_tokens for r in mine if r.input_tokens]
        to = [r.output_tokens for r in mine if r.output_tokens]
        lines.append(
            f"| {name.get(setup, setup)} | {len(mine)} | {sum(r.cost_usd for r in mine):.4f} | "
            f"{statistics.fmean(ti) if ti else 0:,.0f} | {statistics.fmean(to) if to else 0:,.0f} "
            f"| {statistics.fmean(r.seconds for r in mine):.3f} |"
        )
    return "\n".join(lines) + "\n"


def run_m3(args: Any, run: Any) -> tuple[str, dict[str, Any]] | None:
    """M3 inside ``python -m evals.measure_eval --m3`` (EVAL_REPORT 109): the free setups, and
    with ``--m3-vlm`` the paid ones (``--m3-estimate``: the estimate only, ``None`` back;
    ``--m3-replay RUN``: a recorded run's answers rescored, no call)."""
    import sys

    from basis.config import load_settings
    from basis.llm.provider import Budget
    from evals import measure_eval

    facades = ft.load_all(args.m3_truth)
    for path in sorted(args.m3_truth.glob("*.json")):
        run.dataset(path, name=f"measure_facades/{path.stem}")
    for f in facades:
        found = ft.check(f)
        run.case(
            f"facade_truth/{f.id}",
            "passed" if not found else "failed",
            detail={"findings": [asdict(x) for x in found]},
        )
    m1 = m1_walls(args.points, facades) if args.points is not None else None
    rows = engineer_rows(facades, args.m3_clicks, m1) + baseline_rows(facades, m1)
    run.input("m3_clicks", args.m3_clicks)
    vlm: dict[str, VlmSetup] = {}
    outcomes: list[Outcome] = []
    if args.m3_vlm:
        settings = load_settings()
        setups = parse_setups(args.m3_vlm, measure_vlm.models_from_settings(settings))
        vlm = {s.id: s for s in setups}
        for key in sorted({s.prompt for s in setups}):
            run.prompt(prompts.load(PROMPTS[key], PROMPT_VERSION))
        if args.m3_replay is not None:
            recorded = json.loads((args.m3_replay / "measure_eval.json").read_text())
            run.input("m3_replay", args.m3_replay / "measure_eval.json")
            outcomes = [
                Outcome(**o)
                for o in (recorded.get("m3") or {}).get("outcomes", [])
                if o["setup"] in vlm
            ]
        else:
            jobs = vlm_jobs(facades, setups, args.m3_repeats)
            estimate = sum(estimate_job_usd(s, f, p, pr) for s, f, p, pr, _ in jobs)
            print(
                f"M3: {len(jobs)} calls, worst-case estimate ${estimate:.2f} "
                f"(budget ${args.budget_usd:.2f})",
                file=sys.stderr,
            )
            run.note(m3_estimate_usd=round(estimate, 4), m3_calls_planned=len(jobs))
            if args.m3_estimate:
                return None
            budget = Budget(remaining_usd=args.budget_usd)
            ctx = measure_vlm.new_context(budget, run.recorder, activity="evals.measure_facades")
            provider = measure_eval.m5_provider(settings)
            outcomes = asyncio.run(
                run_jobs(provider, ctx, run.recorder.records, jobs, concurrency=args.m5_concurrency)
            )
            for o in outcomes:
                run.case(
                    f"m3/{o.setup}/{o.facade}/{o.photo}#{o.repeat}",
                    "passed" if o.answer is not None else "failed",
                    kind="m3_call",
                    detail={"cost_usd": o.cost_usd, "calls": o.calls, "errors": o.errors},
                    seconds=round(o.seconds, 2),
                )
            print(f"M3 spent ${budget.spent_usd:.4f} (ledger)", file=sys.stderr)
        rows += vlm_rows(facades, outcomes, m1)
    scores, counts = score_all(facades, rows)
    text = render(facades, rows, scores, counts, m1, vlm)
    result = {
        "facades": [f.id for f in facades],
        "truths": {f.id: {k: asdict(v) for k, v in ft.truths(f).items()} for f in facades},
        "m1": {k: asdict(v) for k, v in (m1 or {}).items()},
        "rows": [
            {
                "setup": r.setup,
                "facade": r.facade,
                "photo": r.photo,
                "quantities": r.measured.quantities,
                "openings": r.measured.openings,
                "windows": r.measured.windows,
                "doors": r.measured.doors,
                "refused": r.measured.refused,
                "detail": r.measured.detail,
                "cost_usd": r.cost_usd,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "seconds": r.seconds,
            }
            for r in rows
        ],
        "scores": [asdict(s) | {"abs_pct": s.abs_pct} for s in scores],
        "counts": [asdict(c) for c in counts],
        "summary": summarize(scores),
        "outcomes": [asdict(o) for o in outcomes],
        "setups": {k: v.settings() for k, v in vlm.items()},
    }
    return text, result


__all__ = [
    "AZIMUTH_DEG",
    "run_m3",
    "Row",
    "baseline_rows",
    "engineer_rows",
    "render",
    "score_all",
    "setup_name",
    "vlm_jobs",
    "vlm_rows",
    "Counts",
    "Draft",
    "Measured",
    "Outcome",
    "Score",
    "VlmSetup",
    "draft_from_clicks",
    "formula",
    "from_facade",
    "m1_wall_length",
    "match_openings",
    "measure_draft",
    "measured_direct",
    "parse_setups",
    "score",
    "summarize",
]
