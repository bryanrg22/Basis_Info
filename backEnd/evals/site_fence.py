"""``python -m evals.site_fence``: can a fence line be drafted from above (EVAL_REPORT 106.6)?

No public layer of fences was found for the truth parcels (Fairfax publishes paving, decks,
patios and pools, not fences), so this is a probe, not a scored method. The method tried is
the standard one the research names (a lot-line prior confirmed by detection; cv_methods.md
1c): on the sharp county view, SAM 3 is asked for ``fence`` with no click (concept mode: every
fence it sees) and, as a second prompt, for ``fence`` with positive points every
:data:`STEP_M` along the parcel's lot line (the prior); a lot-line stretch counts as fenced
where a fence mask lies within :data:`NEAR_M` of it. The output per parcel: the masks, the lot
line's length, the length confirmed, and a montage to read by eye whether the masks are
fences. Paid: one SAM 3 prediction per parcel, in the run's ledger.

Usage (from ``backEnd/``)::

    BASIS_ENV=dev uv run python -m evals.site_fence --out DIR --env-file ../backEnd/.env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

from basis.config import load_settings
from basis.geo.geometry import lonlat_to_pixel
from basis.llm.call import CallContext
from basis.llm.provider import Budget
from basis.vision.localize import (
    Instance,
    LocalizeCall,
    Point,
    VisualPrompt,
    build_replicate_localizer,
)
from basis.vision.prep import prepare_image
from basis.vision.schemas import ComponentLabel
from evals import record
from evals import site_truth as st
from evals.site_draft_run import DEFAULT_VERSION, ledger_sam

STEP_M: Final = 4.0
NEAR_M: Final = 1.0
MAX_POINTS: Final = 24


def lot_line_points(
    ring_px: Sequence[tuple[float, float]], step_px: float
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    closed = [*ring_px, ring_px[0]]
    for (x1, y1), (x2, y2) in zip(closed, closed[1:], strict=False):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) // step_px))
        pts += [(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n) for k in range(n)]
    if len(pts) > MAX_POINTS:
        pts = pts[:: math.ceil(len(pts) / MAX_POINTS)]
    return pts


def confirmed_length_m(
    ring_px: Sequence[tuple[float, float]], fence: np.ndarray, near_px: float, pixel_m: float
) -> tuple[float, float]:
    """(the lot line's length inside the picture, the part of it within ``near_px`` of a fence
    mask), in metres, sampled every pixel."""
    from scipy import ndimage

    h, w = fence.shape
    dist = ndimage.distance_transform_edt(~fence) if fence.any() else np.full((h, w), np.inf)
    total = near = 0.0
    closed = [*ring_px, ring_px[0]]
    for (x1, y1), (x2, y2) in zip(closed, closed[1:], strict=False):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1)))
        for k in range(n):
            x, y = x1 + (x2 - x1) * (k + 0.5) / n, y1 + (y2 - y1) * (k + 0.5) / n
            if 0 <= x < w and 0 <= y < h:
                total += 1
                near += dist[int(y), int(x)] <= near_px
    return total * pixel_m, near * pixel_m


async def run(args: argparse.Namespace) -> int:
    rec = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component="site_drafts", version="v1"
    )
    os.environ.setdefault("BASIS_ENV", "dev")
    settings = load_settings(args.env_file)
    token = settings.replicate_api_token.get_secret_value() if settings.replicate_api_token else ""
    budget = Budget(remaining_usd=args.budget_usd)
    localizer = build_replicate_localizer(token, DEFAULT_VERSION, concurrency=2, budget=budget)
    ctx = CallContext(uuid.uuid4(), None, "evals.site_fence", budget, rec.recorder)
    parcels = st.load_all(args.truth)
    if args.only:
        parcels = [p for p in parcels if p.id in set(args.only.split(","))]
    rows = []
    try:
        for parcel in parcels:
            meta = json.loads((args.views_dir / parcel.id / "county.json").read_text())
            data = (args.views_dir / parcel.id / meta["file"]).read_bytes()
            prepared = await asyncio.to_thread(prepare_image, data)
            sx, sy = prepared.width / meta["width"], prepared.height / meta["height"]
            ring = [
                (x * sx, y * sy)
                for x, y in (
                    lonlat_to_pixel(meta["bbox"], meta["width"], meta["height"], lo, la)
                    for lo, la in parcel.parcel
                )
            ]
            pixel_m = meta["pixel_size_m"] / sx
            points = [
                Point(x=min(1, max(0, x / prepared.width)), y=min(1, max(0, y / prepared.height)))
                for x, y in lot_line_points(ring, STEP_M / pixel_m)
            ]
            prompts = [
                VisualPrompt(label=ComponentLabel.FENCE, text="fence"),
                VisualPrompt(label=ComponentLabel.FENCE, text="fence", points=tuple(points)),
            ]
            found, call = await localizer.run_prompts(prepared, prompts, threshold=0.4)
            await ledger_sam(ctx, call, DEFAULT_VERSION, f"{parcel.id}/county/fence")
            row: dict[str, Any] = {"parcel": parcel.id, "pixel_m": pixel_m}
            for name, group in zip(("text", "text_lot_points"), found, strict=True):
                masks = [i.mask.decode().astype(bool) for i in group if i.mask is not None]
                union = (
                    np.logical_or.reduce(masks)
                    if masks
                    else np.zeros((prepared.height, prepared.width), dtype=bool)
                )
                total, near = confirmed_length_m(ring, union, NEAR_M / pixel_m, pixel_m)
                row[name] = {
                    "instances": len(group),
                    "scores": [round(i.score, 3) for i in group],
                    "mask_m2": float(union.sum()) * pixel_m * pixel_m,
                    "lot_line_m": round(total, 1),
                    "confirmed_m": round(near, 1),
                }
            folder = args.out / parcel.id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "fence.json").write_text(
                json.dumps(
                    {
                        **row,
                        "ring_px": ring,
                        "prepared": [prepared.width, prepared.height],
                        "instances": [[i.model_dump(mode="json") for i in g] for g in found],
                        "call": call.model_dump(mode="json"),
                    }
                )
                + "\n"
            )
            (folder / "prepared.jpg").write_bytes(prepared.jpeg)
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        await localizer.client.aclose()
    (args.out / "fence_rows.json").write_text(json.dumps(rows, indent=1) + "\n")
    rec.finish({"spent_usd": round(budget.spent_usd, 6), "parcels": len(rows)})
    return 0


def montage(out: Path, ids: Sequence[str], path: Path) -> None:
    """Each parcel's view with the lot line (yellow) and the fence masks (red: no click,
    cyan: the lot-line points), to read by eye."""
    from PIL import Image, ImageDraw

    from evals.site_views import montage as sheet

    ims = []
    for pid in ids:
        doc = json.loads((out / pid / "fence.json").read_text())
        img = Image.open(out / pid / "prepared.jpg").convert("RGB")
        over = np.asarray(img).copy()
        for group, colour in zip(doc["instances"], ((255, 0, 0), (0, 255, 255)), strict=True):
            for inst in group:
                m = Instance.model_validate(inst).mask
                if m is not None:
                    over[m.decode().astype(bool)] = colour
        img = Image.blend(img, Image.fromarray(over), 0.5)
        d = ImageDraw.Draw(img)
        ring = [tuple(p) for p in doc["ring_px"]]
        d.line([*ring, ring[0]], fill=(255, 255, 0), width=2)
        d.text((4, 4), pid, fill=(255, 255, 255))
        ims.append(img)
    sheet(ims, path, cell=500, cols=3)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.site_fence", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--truth", type=Path, default=st.FIXTURES)
    parser.add_argument(
        "--views-dir", type=Path, default=Path.home() / "basis-eval-data/site_truth/views"
    )
    parser.add_argument("--only")
    parser.add_argument("--budget-usd", type=float, default=0.3)
    parser.add_argument("--env-file", default=".env")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


__all__ = ["confirmed_length_m", "lot_line_points", "montage"]

_ = LocalizeCall

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
