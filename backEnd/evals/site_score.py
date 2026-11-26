"""Score the site-item drafts of a :mod:`evals.site_draft_run` run against the county truth
(EVAL_REPORT 106). Free and deterministic: every draft is rebuilt from the recorded SAM 3
instances by the production function (:func:`basis.vision.site_items.draft_from_instance`), so
the drafting rules can be changed and re-scored without a paid call.

Per view and method:

* **prompted** methods (``box``, ``point``, ``text_box``: an engineer's box or click, one per
  truth item): the draft for each truth item, its area against the truth's (signed and absolute
  percent), the overlap (IoU of the draft's outline and the truth's, rasterized on the view's
  pixels) and the refusals by code (a refused prompt is a miss: the engineer traces it);
* **finding** methods (``text``: a phrase per family and no click; ``m2v:<model>``: the model's
  boxes): every draft whose centre lies on the parcel, duplicates (IoU > 0.5) kept once at the
  higher score, matched to the truth items greedily by IoU (at least 0.5, the PASCAL VOC
  criterion): matched drafts are scored as above; a truth item left over is a **miss**, a draft
  left over a **false item** (an upper bound: the county layer leaves some items out, EVAL 106.1).

The *hand-trace bound* per item is :func:`basis.domain.traced.expected_error_pct` on the truth
outline: one pixel of the view on every edge, the error an engineer's careful trace carries at
best (AI_MEASUREMENT_PLAN section 1.4: 4.5 % on a 6 x 10 m driveway at 8.4 cm).
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

import numpy as np

from basis.domain import traced
from basis.errors.codes import E
from basis.vision import site_items as si
from basis.vision.localize import Box, Instance, Point
from basis.vision.prep import PreparedImage
from evals import site_truth as st

MATCH_IOU: Final = 0.5
DEDUPE_IOU: Final = 0.5
PROMPTED: Final = ("box", "point", "text_box")
FT_PER_M: Final = 1 / 0.3048


@dataclass(frozen=True, slots=True)
class Scored:
    parcel: str
    view: str
    method: str
    family: str
    item: str | None  # the truth item (None: a false item)
    truth_sf: float | None
    draft_sf: float | None
    iou: float | None
    code: str | None = None  # a refusal's code (prompted methods)
    hand_bound_pct: float | None = None
    corners: int | None = None
    proposed_family: str | None = None

    @property
    def signed_pct(self) -> float | None:
        if self.truth_sf is None or self.draft_sf is None:
            return None
        return (self.draft_sf - self.truth_sf) / self.truth_sf * 100.0


@dataclass(slots=True)
class ViewRun:
    folder: Path
    meta: dict[str, Any]
    m2: dict[str, Any] | None
    m2v: dict[str, dict[str, Any]] = field(default_factory=dict)


Roots = Path | Sequence[Path]


def roots_of(roots: Roots) -> list[Path]:
    """One run directory or several (a run per view kind, scored together)."""
    return [roots] if isinstance(roots, Path) else list(roots)


def load_runs(roots: Roots) -> list[ViewRun]:
    runs = []
    views = [v for root in roots_of(roots) for v in sorted(root.glob("*/*/view.json"))]
    for view_json in views:
        folder = view_json.parent
        meta = json.loads(view_json.read_text())
        m2 = (
            json.loads((folder / "m2.json").read_text()) if (folder / "m2.json").is_file() else None
        )
        m2v = {
            p.stem.removeprefix("m2v_"): json.loads(p.read_text())
            for p in folder.glob("m2v_*.json")
        }
        runs.append(ViewRun(folder, meta, m2, m2v))
    return runs


def georef(meta: Mapping[str, Any], scale: float = 1.0) -> si.Georef:
    g = meta["georef"]
    if g["type"] == "mercator":
        return si.MercatorGeoref(tuple(g["bbox"]), g["width"], g["height"])
    inner = si.CentredGeoref(
        g["lat"], g["lon"], g["width"], g["height"], g["pixel_size_m"], scale=scale
    )
    return si.Offset(inner, g["x0"], g["y0"])


def view_of(meta: Mapping[str, Any], scale: float = 1.0) -> si.TopDownView:
    return si.TopDownView(
        data=b"",
        width=meta["width"],
        height=meta["height"],
        pixel_size_m=meta["pixel_size_m"],
        georef=georef(meta, scale),
        kind="public" if meta["georef"]["type"] == "mercator" else "capture",
        source=meta["source"],
        label=meta["view"],
    )


def prepared_of(meta: Mapping[str, Any]) -> PreparedImage:
    w, h = meta["prepared"]
    return PreparedImage(b"", w, h, meta["width"], meta["height"], None)


def raster(ring: Sequence[Sequence[float]], w: int, h: int) -> np.ndarray:
    from PIL import Image, ImageDraw

    img = Image.new("L", (w, h), 0)
    if len(ring) >= 3:
        ImageDraw.Draw(img).polygon([(float(x), float(y)) for x, y in ring], fill=1)
    return np.asarray(img, dtype=bool)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int(np.logical_or(a, b).sum())
    return int(np.logical_and(a, b).sum()) / union if union else 0.0


def hand_bound(item: st.Item, pixel_m: float) -> float:
    bound = traced.expected_error_pct(
        area_sf=item.area_sf,
        length_ft=None,
        edges_ft=item.perimeter_ft,
        lines=0,
        pixel_ft=pixel_m * FT_PER_M,
        scale_px=None,
    )
    return float(bound) if bound is not None else math.nan


def _site_prompt(entry: Mapping[str, Any]) -> si.SitePrompt:
    p = entry["prompt"]
    boxes, points = p.get("boxes") or [], p.get("points") or []
    return si.SitePrompt(
        entry["family"],
        box=Box(**boxes[0]) if boxes else None,
        point=Point(**points[0]) if points and not boxes else None,
    )


def _settings(tolerance_m: float) -> si.DraftSettings:
    # Scored on every view, the coarse one included, so the refusal limit is measured rather
    # than applied; everything else as production drafts.
    return si.DraftSettings(tolerance_m=tolerance_m, max_pixel_m=10.0)


def _draft(
    view: si.TopDownView,
    prepared: PreparedImage,
    prompt: si.SitePrompt,
    instance: Instance,
    settings: si.DraftSettings,
) -> si.Draft | si.Refused:
    return si.draft_from_instance(view, prepared, prompt, instance, settings)


def _sf(draft: si.Draft) -> float:
    return float(draft.quantity)


def score_prompted(
    run: ViewRun, parcel: st.Parcel, tolerance_m: float, scale: float = 1.0
) -> list[Scored]:
    if run.m2 is None:
        return []
    meta = run.meta
    view, prepared = view_of(meta, scale), prepared_of(meta)
    settings = _settings(tolerance_m)
    items = {i.id: i for i in parcel.items}
    w, h = meta["width"], meta["height"]
    out = []
    for entry, found in zip(run.m2["prompts"], run.m2["instances"], strict=True):
        if entry["mode"] not in PROMPTED:
            continue
        item = items[entry["item"]]
        instances = [Instance.model_validate(i) for i in found]
        prompt = _site_prompt(entry)
        chosen = si.pick(instances, prompt)
        truth_mask = raster(meta["truth_px"][item.id], w, h)
        common = {
            "parcel": parcel.id,
            "view": meta["view"],
            "method": entry["mode"],
            "family": item.family,
            "item": item.id,
            "truth_sf": item.area_sf,
            "hand_bound_pct": hand_bound(item, meta["pixel_size_m"]),
        }
        if chosen is None:
            out.append(Scored(**common, draft_sf=None, iou=None, code=E.SITE_NOTHING_FOUND))
            continue
        d = _draft(view, prepared, prompt, chosen, settings)
        if isinstance(d, si.Refused):
            out.append(Scored(**common, draft_sf=None, iou=None, code=d.code))
            continue
        out.append(
            Scored(
                **common,
                draft_sf=_sf(d),
                iou=iou(raster(d.ring_px, w, h), truth_mask),
                corners=len(d.ring_px),
            )
        )
    return out


def _inside(point: tuple[float, float], ring: Sequence[Sequence[float]]) -> bool:
    return st.inside(point, [(float(x), float(y)) for x, y in ring])


def _centre(ring: Sequence[tuple[float, float]]) -> tuple[float, float]:
    return st.centroid(ring) if len(ring) >= 3 else (math.nan, math.nan)


def score_found(
    run: ViewRun,
    parcel: st.Parcel,
    method: str,
    candidates: Iterable[tuple[str, Instance]],
    tolerance_m: float,
    scale: float = 1.0,
) -> list[Scored]:
    """Drafts found without a click (a phrase, or a model's boxes): on the parcel, deduplicated,
    matched to the truth (module doc)."""
    meta = run.meta
    view, prepared = view_of(meta, scale), prepared_of(meta)
    settings = _settings(tolerance_m)
    w, h = meta["width"], meta["height"]
    drafts: list[tuple[si.Draft, np.ndarray]] = []
    for family, instance in candidates:
        m = instance.mask.decode() if instance.mask is not None else None
        if m is None or not m.any():
            continue
        ys, xs = np.nonzero(m)
        point = Point(x=float(xs.mean() + 0.5) / m.shape[1], y=float(ys.mean() + 0.5) / m.shape[0])
        d = _draft(view, prepared, si.SitePrompt(family, point=point), instance, settings)
        if isinstance(d, si.Refused):
            continue
        cx, cy = _centre(list(d.ring_px))
        if not _inside((cx, cy), meta["parcel_px"]):
            continue
        drafts.append((d, raster(d.ring_px, w, h)))
    drafts.sort(key=lambda t: -t[0].score)
    kept: list[tuple[si.Draft, np.ndarray]] = []
    for d, mask in drafts:
        if all(iou(mask, k[1]) <= DEDUPE_IOU for k in kept):
            kept.append((d, mask))
    truth = [(i, raster(meta["truth_px"][i.id], w, h)) for i in parcel.items]
    pairs = sorted(
        (
            (iou(dm, tm), di, ti)
            for di, (_, dm) in enumerate(kept)
            for ti, (_, tm) in enumerate(truth)
        ),
        reverse=True,
    )
    used_d: set[int] = set()
    used_t: set[int] = set()
    out = []
    for value, di, ti in pairs:
        if value < MATCH_IOU or di in used_d or ti in used_t:
            continue
        used_d.add(di)
        used_t.add(ti)
        item, d = truth[ti][0], kept[di][0]
        out.append(
            Scored(
                parcel.id,
                meta["view"],
                method,
                item.family,
                item.id,
                item.area_sf,
                _sf(d),
                value,
                hand_bound_pct=hand_bound(item, meta["pixel_size_m"]),
                corners=len(d.ring_px),
                proposed_family=d.family,
            )
        )
    for ti, (item, _) in enumerate(truth):
        if ti not in used_t:
            out.append(
                Scored(
                    parcel.id,
                    meta["view"],
                    method,
                    item.family,
                    item.id,
                    item.area_sf,
                    None,
                    None,
                    hand_bound_pct=hand_bound(item, meta["pixel_size_m"]),
                )
            )
    for di, (d, _) in enumerate(kept):
        if di not in used_d:
            out.append(
                Scored(
                    parcel.id,
                    meta["view"],
                    method,
                    d.family,
                    None,
                    None,
                    _sf(d),
                    None,
                    proposed_family=d.family,
                )
            )
    return out


def text_candidates(run: ViewRun) -> list[tuple[str, Instance]]:
    if run.m2 is None:
        return []
    return [
        (entry["family"], Instance.model_validate(i))
        for entry, found in zip(run.m2["prompts"], run.m2["instances"], strict=True)
        if entry["mode"] == "text"
        for i in found
    ]


def m2v_candidates(doc: Mapping[str, Any]) -> list[tuple[str, Instance]]:
    """The instance each proposed box picks (box IoU >= 0.5), with the model's family."""
    out = []
    for prompt, found in zip(doc.get("sam_prompts") or [], doc.get("instances") or [], strict=True):
        sp = si.SitePrompt(prompt["family"], box=Box(**prompt["box"]))
        chosen = si.pick([Instance.model_validate(i) for i in found], sp)
        if chosen is not None:
            out.append((prompt["family"], chosen))
    return out


def what_candidates(run: ViewRun, doc: Mapping[str, Any]) -> list[tuple[str, Instance]]:
    """The phrase-found instances of the families the model says the property has (the model
    says *what*, SAM 3's phrase finds *where*)."""
    said = {i["family"] for i in ((doc.get("answer") or {}).get("items") or [])}
    return [(f, i) for f, i in text_candidates(run) if f in said]


def score_all(
    root: Roots,
    truth: Mapping[str, st.Parcel],
    *,
    tolerance_m: float = si.TOLERANCE_M,
    google_scale: float = 1.0,
    keep: Iterable[str] | None = None,
) -> list[Scored]:
    wanted = None if keep is None else set(keep)
    out: list[Scored] = []
    for run in load_runs(root):
        parcel = truth.get(run.meta["parcel"])
        if parcel is None or (wanted is not None and parcel.id not in wanted):
            continue
        scale = google_scale if run.meta["view"] == "google" else 1.0
        out += score_prompted(run, parcel, tolerance_m, scale)
        out += score_found(run, parcel, "text", text_candidates(run), tolerance_m, scale)
        for model, doc in run.m2v.items():
            out += score_found(run, parcel, f"m2v:{model}", m2v_candidates(doc), tolerance_m, scale)
            out += score_found(
                run, parcel, f"m2v_what:{model}", what_candidates(run, doc), tolerance_m, scale
            )
    return out


def _mean(values: Iterable[float | None]) -> float:
    got = [v for v in values if v is not None and math.isfinite(v)]
    return statistics.fmean(got) if got else math.nan


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return math.nan
    return float(np.percentile(np.asarray(values, dtype=float), q))


def summarize(
    scored: Sequence[Scored], families: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Per view, method and family (``all`` too): items, drafted, misses (and refusals by
    code), false items, mean signed and absolute area error, median and 90th percentile
    absolute, mean IoU, the hand-trace bound's mean."""
    rows = []
    keys = dict.fromkeys((s.view, s.method) for s in scored)
    for view, method in keys:
        mine = [s for s in scored if s.view == view and s.method == method]
        fams = families or sorted({s.family for s in mine})
        for family in ["all", *fams]:
            chosen = [s for s in mine if family == "all" or s.family == family]
            truths = [s for s in chosen if s.item is not None]
            if not truths:
                continue
            drafted = [s for s in truths if s.draft_sf is not None]
            signed = [s.signed_pct for s in drafted if s.signed_pct is not None]
            absolute = [abs(v) for v in signed]
            codes: dict[str, int] = {}
            for s in truths:
                if s.draft_sf is None:
                    codes[s.code or "not found"] = codes.get(s.code or "not found", 0) + 1
            rows.append(
                {
                    "view": view,
                    "method": method,
                    "family": family,
                    "items": len(truths),
                    "drafted": len(drafted),
                    "misses": len(truths) - len(drafted),
                    "miss_codes": codes,
                    "false_items": sum(1 for s in chosen if s.item is None),
                    "mean_signed_pct": statistics.fmean(signed) if signed else math.nan,
                    "mean_abs_pct": statistics.fmean(absolute) if absolute else math.nan,
                    "median_abs_pct": statistics.median(absolute) if absolute else math.nan,
                    "p90_abs_pct": percentile(absolute, 90),
                    "mean_iou": statistics.fmean(s.iou for s in drafted if s.iou is not None)
                    if drafted
                    else math.nan,
                    "hand_bound_pct": _mean(s.hand_bound_pct for s in truths),
                    "mean_corners": statistics.fmean(s.corners for s in drafted if s.corners)
                    if drafted
                    else math.nan,
                    "family_right": sum(
                        1 for s in drafted if s.proposed_family in (None, s.family)
                    ),
                }
            )
    return rows


def costs(roots: Roots) -> list[dict[str, Any]]:
    """Per view and method: dollars, tokens and seconds per property, from the recorded calls."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for run in load_runs(roots):
        view = run.meta["view"]
        if run.m2 is not None:
            call = run.m2["call"]
            r = rows.setdefault((view, "sam3 (all M2 prompts, one call)"), _zero())
            r["properties"] += 1
            r["usd"] += call["cost_usd"]
            r["sam_seconds"] += call["predict_seconds"]
        for model, doc in run.m2v.items():
            r = rows.setdefault((view, f"m2v:{model}"), _zero())
            r["properties"] += 1
            r["input_tokens"] += doc.get("input_tokens") or 0
            r["output_tokens"] += doc.get("output_tokens") or 0
            r["model_seconds"] += doc.get("seconds") or 0
            if doc.get("call"):
                r["usd_sam"] += doc["call"]["cost_usd"]
                r["sam_seconds"] += doc["call"]["predict_seconds"]
    # The vision calls' dollars as the ledger priced them: each chat row belongs to the view of
    # the SAM 3 row before it (the runner writes a view's M2 row, then per model its chat row
    # and its SAM 3 row).
    for root in roots_of(roots):
        _ledger_costs(root, rows)
    return [{"view": k[0], "method": k[1], **v} for k, v in rows.items()]


def _ledger_costs(root: Path, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
    view = None
    for row in ledger_rows(root):
        what = (row.get("request_params") or {}).get("what")
        if row.get("kind") == "localization" and isinstance(what, str):
            view = what.split("/")[1]
        elif row.get("kind") == "chat" and view is not None:
            key = (view, f"m2v:{row.get('requested_model')}")
            if key in rows:
                rows[key]["usd"] += float(row.get("cost_usd") or 0)


def ledger_rows(roots: Roots) -> list[dict[str, Any]]:
    out = []
    for root in roots_of(roots):
        path = root / "llm_calls.jsonl"
        if path.is_file():
            out += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return out


def ledger_total(root: Roots) -> dict[str, Any]:
    """The run's spend by kind and model, from its ledger."""
    out: dict[str, Any] = {}
    for row in ledger_rows(root):
        key = f"{row.get('kind')}:{row.get('model')}"
        got = out.setdefault(key, {"calls": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0})
        got["calls"] += 1
        got["usd"] += float(row.get("cost_usd") or 0)
        got["input_tokens"] += int(row.get("input_tokens") or 0)
        got["output_tokens"] += int(row.get("output_tokens") or 0)
    return out


def _zero() -> dict[str, Any]:
    return {
        "properties": 0,
        "usd": 0.0,
        "usd_sam": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "model_seconds": 0.0,
        "sam_seconds": 0.0,
    }


def with_parcel_scale(scored: Sequence[Scored], ks: Mapping[str, float]) -> list[Scored]:
    """Google's drafts with each parcel's area corrected by its own ``k`` squared."""
    return [
        replace(s, draft_sf=s.draft_sf * ks.get(s.parcel, 1.0) ** 2)
        if s.view == "google" and s.draft_sf
        else s
        for s in scored
    ]


def with_scale(scored: Sequence[Scored], k: float) -> list[Scored]:
    """Google's drafts with the area corrected by ``k`` squared (a similarity scale)."""
    return [
        replace(s, draft_sf=s.draft_sf * k * k) if s.view == "google" and s.draft_sf else s
        for s in scored
    ]


__all__ = [
    "MATCH_IOU",
    "Scored",
    "ViewRun",
    "costs",
    "hand_bound",
    "iou",
    "load_runs",
    "m2v_candidates",
    "raster",
    "score_all",
    "score_found",
    "score_prompted",
    "summarize",
    "text_candidates",
    "with_parcel_scale",
    "with_scale",
    "what_candidates",
    "SETUPS",
    "VIEWS",
    "leave_one_out_k",
    "registrations",
    "render_costs",
    "render_summary",
]


# ------------------------------------------------------------------ the tables

SETUPS: Final[dict[str, str]] = {
    "box": "SAM 3, an engineer's box alone (no phrase)",
    "point": "SAM 3, an engineer's click alone (no phrase)",
    "text_box": "SAM 3, the phrase and the engineer's box (today's photo box-to-add)",
    "text": "SAM 3, a phrase per family, no click",
    "m2v:gpt-4o-mini": "Vision model gpt-4o-mini proposes boxes, then SAM 3 (box alone)",
    "m2v:gpt-5-nano": "Vision model gpt-5-nano proposes boxes, then SAM 3 (box alone)",
    "m2v_what:gpt-4o-mini": "Vision model gpt-4o-mini says which families; SAM 3 finds them "
    "by phrase",
    "m2v_what:gpt-5-nano": "Vision model gpt-5-nano says which families; SAM 3 finds them "
    "by phrase",
}
VIEWS: Final[dict[str, str]] = {
    "county": "Fairfax 2023 ortho, 3 in (7.8 cm a pixel as fetched)",
    "naip": "Federal NAIP Plus, 0.6 m",
    "google": "Google straight-down capture, 8.4 cm recorded",
}


def _f(value: Any, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def render_summary(rows: Sequence[Mapping[str, Any]], title: str) -> str:
    lines = [
        f"### {title}",
        "",
        "Columns: *items*: truth items in the views; *drafted*: drafts (a prompted method) or "
        "matched drafts (a finding method, IoU >= 0.5); *misses*: items with no draft, with the "
        "refusal codes; *false*: drafts on the parcel matching no truth item (an upper bound: "
        "the county layer leaves some items out); *signed* / *abs*: area error of the drafted "
        "items against the county's, mean, median and 90th percentile; *IoU*: mean overlap with "
        "the truth outline; *hand bound*: one pixel on every edge of the truth outline "
        "(`traced.expected_error_pct`), the best an engineer's own trace on the same view does; "
        "*corners*: mean corners a draft keeps.",
        "",
        "| View | Setup | Family | Items | Drafted | Misses (codes) | False | Mean signed % | "
        "Mean abs % | Median abs % | P90 abs % | Mean IoU | Hand bound % | Corners |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        codes = ", ".join(
            f"{k.removeprefix('BASIS-')} {v}" for k, v in sorted(r["miss_codes"].items())
        )
        lines.append(
            f"| {VIEWS.get(r['view'], r['view'])} | {SETUPS.get(r['method'], r['method'])} | "
            f"{r['family']} | {r['items']} | {r['drafted']} | {r['misses']}"
            f"{f' ({codes})' if codes else ''} | {r['false_items']} | "
            f"{_f(r['mean_signed_pct'])} | {_f(r['mean_abs_pct'])} | {_f(r['median_abs_pct'])} | "
            f"{_f(r['p90_abs_pct'])} | {_f(r['mean_iou'], 3)} | {_f(r['hand_bound_pct'])} | "
            f"{_f(r['mean_corners'], 0)} |"
        )
    return "\n".join(lines) + "\n"


def render_costs(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "### Cost, tokens and time per property",
        "",
        "Columns: dollars are the ledger's (SAM 3 billed predict seconds at $0.000975 a second "
        "on Replicate's L40S; the vision model's tokens at the price table); *SAM s*: billed "
        "predict seconds; *model s*: the vision call's wall time.",
        "",
        "| View | Setup | Properties | $ / property | Input tokens / property | "
        "Output tokens / property | SAM s / property | Model s / property |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        n = max(1, r["properties"])
        lines.append(
            f"| {VIEWS.get(r['view'], r['view'])} | {SETUPS.get(r['method'], r['method'])} | "
            f"{r['properties']} | {(r['usd'] + r['usd_sam']) / n:.5f} | "
            f"{r['input_tokens'] / n:,.0f} | {r['output_tokens'] / n:,.0f} | "
            f"{r['sam_seconds'] / n:.2f} | {r['model_seconds'] / n:.2f} |"
        )
    return "\n".join(lines) + "\n"


def truth_edges(roots: Roots, truth: Mapping[str, st.Parcel]) -> list[dict[str, Any]]:
    """Per truth item on the county view: where the picture's edge lies against the county's
    outline (:func:`evals.site_register.edge_offset_px`), in metres (positive: the visible edge
    is outside the outline), and the area that offset all round would add, in percent."""
    from PIL import Image

    from evals.site_register import edge_offset_px

    out = []
    for run in load_runs(roots):
        meta = run.meta
        parcel = truth.get(meta["parcel"])
        if meta["view"] != "county" or parcel is None:
            continue
        with Image.open(run.folder / "prepared.jpg") as im:
            grey = np.asarray(im.convert("L"), dtype=np.float64)
        h, w = grey.shape
        sx, sy = w / meta["width"], h / meta["height"]
        pixel_m = meta["pixel_size_m"] / sx
        for item in parcel.items:
            ring = [(x * sx, y * sy) for x, y in meta["truth_px"][item.id]]
            offset_m = edge_offset_px(grey, raster(ring, w, h)) * pixel_m
            perimeter_m = item.perimeter_ft * 0.3048
            area_m2 = item.area_sf / 10.763910416709722
            out.append(
                {
                    "parcel": parcel.id,
                    "item": item.id,
                    "family": item.family,
                    "offset_m": offset_m,
                    "area_effect_pct": offset_m * perimeter_m / area_m2 * 100,
                    "source": item.source,
                }
            )
    return out


def registrations(root: Roots) -> list[dict[str, Any]]:
    out = []
    for run in load_runs(root):
        reg = run.meta.get("registration")
        if run.meta["view"] != "google" or not reg:
            continue
        out.append(
            {
                "parcel": run.meta["parcel"],
                "k": reg["k"],
                "score": reg["score"],
                "imagery_date": run.meta.get("imagery_date"),
            }
        )
    return out


def leave_one_out_k(regs: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Each parcel's correction: the median ``k`` of the other parcels (never its own)."""
    out = {}
    for r in regs:
        others = [o["k"] for o in regs if o["parcel"] != r["parcel"]]
        out[r["parcel"]] = statistics.median(others) if others else 1.0
    return out
