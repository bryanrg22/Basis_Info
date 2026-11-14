"""localize_photos.py: run a landing-page photo through Basis's localizer and write its masks.

    cd backEnd && ../../backEnd/.venv/bin/python ../web/site/landing/tools/localize_photos.py \
        --photo ../web/assets/kitchen.webp --name kitchen \
        --labels kitchen_cabinet,countertop,light_fixture,chair,stove,sink,built_in_shelving

(any Python with the backend's dependencies and ``backEnd/`` on the path works; the token is read
from ``REPLICATE_API_TOKEN`` or ``backEnd/.env``.)

This is the product step, not a copy of it: the photo is prepared by ``basis.vision.prep``
(1024 px, re-encoded), the labels' phrases come from the default phrase set, the call is
``basis.vision.localize.localize_photo`` on the full frame at the product's candidate threshold,
and an instance is counted when its score clears its class's count threshold
(``count_threshold``), exactly as ``basis.activities.localize_photo`` stores it.

It is a paid call (SAM 3 on Replicate, billed by predict seconds on an L40S). ``--max-usd`` is
the budget handed to the localizer; it reserves 30 s of predict time (about $0.03) before the
prediction and refuses to start past the cap. ``--dry-run`` prints the phrases and the estimate
and calls nothing.

Rerunnable: run it again after the photo is replaced. It writes
  web/site/landing/src/data/<name>.json   what the page draws: every instance with its label,
                                            score, counted flag, box and mask outline as SVG
                                            path data in image fractions (0-1 on both axes,
                                            the page scales it with preserveAspectRatio=none)
  .ui-climb/r3/localize/<name>.raw.json     the full Localization (masks as run lengths), for
                                            the crop check (tools/crop_check.py)
  ``--draw`` adds the engineer's boxes the way the app's box-to-add does (one call each).

  .ui-climb/r3/localize/costs.jsonl         one line per call: when, photo sha, predict seconds,
                                            cost, prediction id
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backEnd"))

from basis.llm.provider import Budget  # noqa: E402
from basis.vision.localize import (  # noqa: E402
    CANDIDATE_THRESHOLD,
    DEFAULT_PHRASE_SET,
    DEFAULT_THRESHOLD,
    L40S_USD_PER_SECOND,
    RESERVE_SECONDS,
    Box,
    build_replicate_localizer,
    count_threshold,
    localize_photo,
    phrases_for,
    prompt_labels,
)
from basis.vision.prep import prepare_image  # noqa: E402
from basis.vision.schemas import ComponentLabel  # noqa: E402

SAM3_VERSION_DEFAULT = None  # read from Settings below


# ----------------------------------------------------------------------------- outlines


def _segments(mask: np.ndarray) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Marching squares over a boolean mask, at pixel corners: the boundary as unit segments
    between edge midpoints (the standard 16-case table; saddles split so the set stays inside)."""
    h, w = mask.shape
    m = np.zeros((h + 2, w + 2), dtype=np.uint8)
    m[1:-1, 1:-1] = mask
    tl, tr, br, bl = m[:-1, :-1], m[:-1, 1:], m[1:, 1:], m[1:, :-1]
    code = tl * 8 + tr * 4 + br * 2 + bl
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    ys, xs = np.nonzero((code > 0) & (code < 15))
    for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
        c = int(code[y, x])
        # cell corners in pixel-centre coordinates of the unpadded mask: (x-1, y-1) .. (x, y)
        top, right, bottom, left = (x - 0.5, y - 1.0), (x, y - 0.5), (x - 0.5, y), (x - 1.0, y - 0.5)
        table = {
            1: [(left, bottom)], 2: [(bottom, right)], 3: [(left, right)], 4: [(right, top)],
            5: [(left, top), (right, bottom)], 6: [(bottom, top)], 7: [(left, top)],
            8: [(top, left)], 9: [(top, bottom)], 10: [(top, right), (bottom, left)],
            11: [(top, right)], 12: [(right, left)], 13: [(right, bottom)], 14: [(bottom, left)],
        }
        segs.extend(table[c])
    return segs


def _loops(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """The mask's boundary as closed loops of points (pixel coordinates)."""
    nxt: dict[tuple[float, float], tuple[float, float]] = {}
    for a, b in _segments(mask):
        nxt[a] = b
    loops = []
    while nxt:
        start, cur = next(iter(nxt.items()))
        loop = [start]
        del nxt[start]
        while cur != start and cur in nxt:
            loop.append(cur)
            cur = nxt.pop(cur)
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def _simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on a closed loop (split at the point farthest from the first)."""

    def dp(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(pts) < 3:
            return pts
        a, b = np.array(pts[0]), np.array(pts[-1])
        ab = b - a
        n = float(np.hypot(*ab)) or 1.0
        arr = np.array(pts[1:-1])
        d = np.abs(ab[0] * (arr[:, 1] - a[1]) - ab[1] * (arr[:, 0] - a[0])) / n
        i = int(np.argmax(d))
        if d[i] <= tol:
            return [pts[0], pts[-1]]
        left = dp(pts[: i + 2])
        return left[:-1] + dp(pts[i + 1 :])

    far = int(np.argmax([(x - points[0][0]) ** 2 + (y - points[0][1]) ** 2 for x, y in points]))
    ring = points + [points[0]]
    out = dp(ring[: far + 1])[:-1] + dp(ring[far:])[:-1]
    return out


def outline(mask: np.ndarray, *, min_area_px: int = 24, tol: float = 0.9) -> str:
    """SVG path data in image fractions; holes and specks under ``min_area_px`` dropped."""
    h, w = mask.shape
    parts = []
    for loop in _loops(mask):
        xs, ys = np.array([p[0] for p in loop]), np.array([p[1] for p in loop])
        area = 0.5 * abs(float(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1))))
        if area < min_area_px:
            continue
        pts = _simplify(loop, tol)
        if len(pts) < 3:
            continue
        coords = " ".join(f"{(x + 0.5) / w:.4f} {(y + 0.5) / h:.4f}" for x, y in pts)
        parts.append(f"M{coords}Z")
    return "".join(parts)


# ----------------------------------------------------------------------------- run


def _token() -> str:
    token = os.environ.get("REPLICATE_API_TOKEN", "").strip()
    if token:
        return token
    for env in (ROOT / "backEnd" / ".env", ROOT.parents[2] / "backEnd" / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("REPLICATE_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("REPLICATE_API_TOKEN is not set (environment or backEnd/.env)")


def _version() -> str:
    from basis.config import Settings

    return Settings.model_fields["sam3_replicate_version"].default


async def run(args: argparse.Namespace) -> None:
    photo = Path(args.photo).resolve()
    data = photo.read_bytes()
    image = prepare_image(data)
    labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    phrases = phrases_for(prompt_labels(labels), DEFAULT_PHRASE_SET)
    estimate = RESERVE_SECONDS * L40S_USD_PER_SECOND
    print(f"photo {photo.name}: {image.width}x{image.height} prepared, sha256 {image.sha256[:12]}")
    print(f"phrases ({len(phrases)}): " + ", ".join(f"{p.text} [{p.label}]" for p in phrases))
    print(f"estimate: one prediction, typically 1-3 s of predict time (${3 * L40S_USD_PER_SECOND:.4f});"
          f" the localizer reserves ${estimate:.4f} first; cap ${args.max_usd:.2f}")
    if args.dry_run:
        return
    budget = Budget(remaining_usd=args.max_usd)
    localizer = build_replicate_localizer(_token(), _version(), budget=budget, concurrency=1)
    loc = await localize_photo(image, labels, localizer, tiling=False, threshold=CANDIDATE_THRESHOLD)
    # The engineer's drawn boxes: the product's box-to-add (basis.api.routes.objects.prompt_box),
    # one call per box, the label's phrases with the box, the instance overlapping it best kept.
    drawn = []
    for spec in filter(None, (args.draw or "").split(";")):
        label, coords = spec.split(":")
        x1, y1, x2, y2 = (float(v) for v in coords.split(","))
        prompted = await localizer.segment_prompt(
            image, label=ComponentLabel(label), boxes=[Box(x1=x1, y1=y1, x2=x2, y2=y2)], threshold=DEFAULT_THRESHOLD
        )
        kept = prompted.instances[0] if prompted.instances else None
        print(f"drawn {label} [{coords}]: returned {prompted.returned}, kept "
              f"{'none' if kept is None else f'{kept.score:.2f}'}; ${prompted.cost_usd:.4f}")
        drawn.append((spec, kept, prompted))

    out_dir = ROOT / ".ui-climb" / "r3" / "localize"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.name}.raw.json").write_text(loc.model_dump_json())
    all_calls = list(loc.calls) + [c for _, _, pr in drawn for c in pr.calls]
    total_cost = sum(c.cost_usd for c in all_calls)
    total_seconds = sum(c.predict_seconds for c in all_calls)
    with (out_dir / "costs.jsonl").open("a") as f:
        for call in all_calls:
            f.write(json.dumps({
                "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "photo": args.name,
                "photo_sha256": hashlib.sha256(data).hexdigest(), "prepared_sha256": image.sha256,
                "prediction_id": call.prediction_id, "predict_seconds": call.predict_seconds,
                "cost_usd": call.cost_usd, "status": call.status, "prompts": list(call.prompts),
            }) + "\n")

    instances = []
    order = sorted(loc.instances, key=lambda i: (i.label, -i.score))
    for n, inst in enumerate(order, 1):
        mask = inst.mask.decode() if inst.mask is not None else None
        thr = count_threshold(inst.label, DEFAULT_PHRASE_SET)
        instances.append({
            "id": f"{args.name[:1]}{n}",
            "label": str(inst.label),
            "phrase": inst.phrase,
            "score": round(inst.score, 3),
            "threshold": thr,
            "counted": inst.score >= thr,
            "box": [round(v, 4) for v in (inst.box.x1, inst.box.y1, inst.box.x2, inst.box.y2)],
            "area": round(inst.area, 5),
            "path": outline(mask) if mask is not None else None,
            "source": "model",
        })
    for n, (spec, kept, _) in enumerate(drawn, 1):
        if kept is None:
            continue
        mask = kept.mask.decode() if kept.mask is not None else None
        instances.append({
            "id": f"{args.name[:1]}d{n}", "label": str(kept.label), "phrase": kept.phrase,
            "score": round(kept.score, 3), "threshold": DEFAULT_THRESHOLD, "counted": True,
            "box": [round(v, 4) for v in (kept.box.x1, kept.box.y1, kept.box.x2, kept.box.y2)],
            "area": round(kept.area, 5), "path": outline(mask) if mask is not None else None,
            "source": "manual", "drawn_box": [float(v) for v in spec.split(":")[1].split(",")],
        })
    page = {
        "photo": {"name": args.name, "src": os.path.relpath(photo, ROOT / "web" / "site" / "landing"),
                  "width": image.width, "height": image.height,
                  "sha256": hashlib.sha256(data).hexdigest()},
        "engine": {"step": "basis.vision.localize.localize_photo", "model": "SAM 3 (Replicate)",
                   "phrase_set": DEFAULT_PHRASE_SET, "candidate_threshold": CANDIDATE_THRESHOLD,
                   "labels": labels},
        "cost_usd": round(total_cost, 6), "predict_seconds": round(total_seconds, 3),
        "calls": len(all_calls),
        "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "instances": instances,
    }
    data_dir = ROOT / "web" / "site" / "landing" / "src" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{args.name}.json").write_text(json.dumps(page, indent=1))
    print(f"{len(instances)} instances ({sum(i['counted'] for i in instances)} counted); "
          f"{len(all_calls)} calls, predict {total_seconds:.2f} s, cost ${total_cost:.4f}")
    for i in instances:
        print(f"  {i['id']:>4} {i['label']:<18} {i['phrase']:<18} {i['score']:.2f} "
              f"{'counted' if i['counted'] else 'below ' + str(i['threshold'])} box {i['box']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--photo", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--labels", required=True, help="comma-separated ComponentLabel values")
    p.add_argument("--draw", default="", help="engineer's boxes, 'label:x1,y1,x2,y2;...' in image fractions")
    p.add_argument("--max-usd", type=float, default=0.5)
    p.add_argument("--dry-run", action="store_true")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
