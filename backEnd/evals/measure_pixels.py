"""``python -m evals.measure_pixels``: is a view's recorded pixel size right? Known lengths on
real houses, measured in pixels on the Google straight-down view and on the public ortho
(EVAL_REPORT 103, step 6).

The input is a JSON file of hand measurements (kept in the private data directory: the pixel
coordinates were read off Google imagery):

    {"method": "...how the points were placed...",
     "views": [{"house": "mo1222", "kind": "google_close" | "public_ortho", "source": "...",
                "pixel_size_m": 0.0841, "range_m": 165.3,          # range: Google views only
                "segments": [{"what": "roof outline, long side", "truth_ft": 61.02,
                              "truth": "printed roof plan 61'-0 1/4\\"",
                              "edge_height_ft": 13.6,               # the edge above the ground
                              "p1": [671, 375], "p2": [671, 603]}]}]}

For each segment: its length in pixels; the metres per pixel the true length implies; the
**ratio** of that to the view's recorded ``pixel_size_m`` (1.0: the recorded size is right; below
1: the view shows the length larger than its recorded scale says, so a traced length reads
long). A Google view is a perspective camera over a 3D surface: where the place has 3D buildings,
a roof edge ``h`` above the ground is ``range - h`` from the camera and appears ``range /
(range - h)`` larger than a length on the ground. ``corrected`` divides that out
(:func:`perspective_factor`), so the corrected ratio is the pixel size's own error at the
ground; the raw ratio is what a trace of a roof edge would carry. A public ortho is resampled to
the ground (a tilted building leans, but its scale changes by under 1 %), so no correction.

Summaries per house and per kind of view: mean, SD and range of the ratios, the number of
segments and houses. Deterministic, no network, $0.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

FT_PER_M: Final = 1 / 0.3048


def perspective_factor(range_m: float, height_m: float) -> float:
    """How much smaller a length ``height_m`` above the ground point would measure if it lay on
    the ground: ``(range - h) / range`` for a straight-down perspective camera ``range`` above
    the ground."""
    if range_m <= 0 or height_m >= range_m:
        raise ValueError("the edge must be below the camera")
    return (range_m - height_m) / range_m


def segment_px(seg: Mapping[str, Any]) -> float:
    (x1, y1), (x2, y2) = seg["p1"], seg["p2"]
    return math.hypot(float(x2) - float(x1), float(y2) - float(y1))


def score_segment(view: Mapping[str, Any], seg: Mapping[str, Any]) -> dict[str, Any]:
    px = segment_px(seg)
    if px <= 0:
        raise ValueError(f"{view['house']}: {seg['what']} has no length in pixels")
    truth_m = float(seg["truth_ft"]) / FT_PER_M
    implied = truth_m / px
    recorded = float(view["pixel_size_m"])
    ratio = implied / recorded
    out: dict[str, Any] = {
        "house": view["house"],
        "kind": view["kind"],
        "what": seg["what"],
        "truth_ft": float(seg["truth_ft"]),
        "px": px,
        "implied_m_per_px": implied,
        "recorded_m_per_px": recorded,
        "ratio": ratio,
        "traced_error_pct": (1 / ratio - 1) * 100,
    }
    if view.get("range_m") and seg.get("edge_height_ft") is not None:
        factor = perspective_factor(float(view["range_m"]), float(seg["edge_height_ft"]) / FT_PER_M)
        out["perspective_factor"] = factor
        out["corrected_ratio"] = ratio / factor
    return out


def _stats(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "sd": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, Any] = {}
    for kind in dict.fromkeys(r["kind"] for r in rows):
        mine = [r for r in rows if r["kind"] == kind]
        houses = {}
        for house in dict.fromkeys(r["house"] for r in mine):
            hr = [r for r in mine if r["house"] == house]
            houses[house] = {
                "ratio": _stats([r["ratio"] for r in hr]),
                "corrected_ratio": _stats(
                    [r["corrected_ratio"] for r in hr if "corrected_ratio" in r]
                ),
            }
        means = [h["ratio"]["mean"] for h in houses.values() if h["ratio"]["mean"] is not None]
        corrected = [
            h["corrected_ratio"]["mean"]
            for h in houses.values()
            if h["corrected_ratio"]["mean"] is not None
        ]
        by_kind[kind] = {
            "segments": len(mine),
            "houses": len(houses),
            "ratio_over_segments": _stats([r["ratio"] for r in mine]),
            "ratio_over_house_means": _stats(means),
            "corrected_over_house_means": _stats(corrected),
            "per_house": houses,
        }
    return by_kind


def run(doc: Mapping[str, Any]) -> dict[str, Any]:
    rows = [score_segment(v, s) for v in doc["views"] for s in v["segments"]]
    return {"method": doc.get("method"), "segments": rows, "summary": summarize(rows)}


def render(result: Mapping[str, Any]) -> str:
    lines = [
        "| House | View | Length measured | True ft | Pixels | Implied m/px | Recorded m/px | "
        "Ratio | Height-corrected ratio |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in result["segments"]:
        corr = f"{r['corrected_ratio']:.4f}" if "corrected_ratio" in r else ""
        lines.append(
            f"| {r['house']} | {r['kind']} | {r['what']} | {r['truth_ft']:.2f} | {r['px']:.1f} | "
            f"{r['implied_m_per_px']:.5f} | {r['recorded_m_per_px']:.5f} | {r['ratio']:.4f} | "
            f"{corr} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.measure_pixels", description=__doc__)
    parser.add_argument("points", type=Path, help="the hand measurements (JSON)")
    parser.add_argument("--out", type=Path, required=True, help="the result (JSON)")
    args = parser.parse_args(argv)
    result = run(json.loads(args.points.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(render(result))
    print(json.dumps(result["summary"], indent=1))
    return 0


__all__ = ["perspective_factor", "render", "run", "score_segment", "segment_px", "summarize"]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
