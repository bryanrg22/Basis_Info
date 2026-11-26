"""Register Google's straight-down view on the county orthophoto, and read Google's ground pixel
size from it (EVAL_REPORT 106.2).

The county view is georeferenced exactly (a Web Mercator box), Google's capture only by its
centre and recorded pixel size. Both pictures show the same ground, so the scale ``k`` and the
shift that make Google's picture lie on the county's answer two questions: where the truth
outlines fall on Google's picture (to place an engineer's box or click there, and to score the
overlap), and whether Google's recorded ``pixel_size_m`` is right on the ground (EVAL_REPORT 103
suspected it 2.7 % too fine).

Method (the standard area-based registration: normalized cross-correlation, J. P. Lewis, "Fast
Normalized Cross-Correlation", 1995): both pictures to grey, to gradient magnitude (colour and
exposure differ between the two flights; edges do not), Google's resampled to the county's pixel
by ``recorded / county * k``; for each ``k`` in a grid, the county picture's normalized
cross-correlation over every shift inside Google's; the best ``k`` and shift win, refined by a
parabola through the scores around it. Raised surfaces stand nearer Google's camera than the
ground and show larger (EVAL 103.6), so the county's building outlines (dilated by 1.5 m) are
left out of the template: what is matched is the ground (yards, paving, streets) and the trees,
which also stand up and are the remaining bias (stated, not removed).

``k`` > 1: Google's ground pixel is larger than recorded (a length traced on it reads short by
``1 - 1/k``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

SCALES: Final = tuple(round(0.94 + 0.005 * i, 3) for i in range(25))  # 0.94 to 1.06

Array = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Registration:
    k: float  # Google's ground pixel over its recorded one
    score: float  # the normalized cross-correlation at the best shift (1: identical)
    # County pixel (cx, cy) lies on Google pixel ((cx - x0) / f, (cy - y0) / f), with f the
    # county pixels per Google pixel after the scale: f = recorded * k / county.
    f: float
    x0: float
    y0: float
    scores: tuple[tuple[float, float], ...]  # (k, best score) over the grid

    def county_to_google(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.x0) / self.f, (y - self.y0) / self.f)


def gradient(grey: Array) -> Array:
    gy, gx = np.gradient(grey)
    return np.asarray(np.hypot(gx, gy), dtype=np.float64)


def resample(image: Array, factor: float) -> Array:
    """``image`` scaled by ``factor`` (bilinear)."""
    from scipy import ndimage

    return np.asarray(ndimage.zoom(image, factor, order=1), dtype=np.float64)


def ncc(template: Array, weight: Array, image: Array) -> Array:
    """Weighted normalized cross-correlation of ``template`` (weights 0 or 1) at every shift
    that keeps it inside ``image``: score[y, x] for the template's corner at (x, y)."""
    from scipy.signal import fftconvolve

    w = weight
    n = w.sum()
    t = (template - (template * w).sum() / n) * w
    tnorm = np.sqrt((t * t).sum())
    flip_t = t[::-1, ::-1]
    flip_w = w[::-1, ::-1]
    num = fftconvolve(image, flip_t, mode="valid")
    s1 = fftconvolve(image, flip_w, mode="valid")
    s2 = fftconvolve(image * image, flip_w, mode="valid")
    var = np.maximum(s2 - s1 * s1 / n, 1e-9)
    return np.asarray(num / (tnorm * np.sqrt(var)), dtype=np.float64)


def register(
    county: Array,
    google: Array,
    *,
    county_px: float,
    google_px: float,
    mask: Array | None = None,
    scales: Sequence[float] = SCALES,
) -> Registration:
    """Find ``k`` and the shift (module doc). ``county`` and ``google`` are grey pictures;
    ``mask`` is 1 where the county picture is used (0 on buildings)."""
    template = gradient(county)
    weight = np.ones_like(template) if mask is None else mask.astype(np.float64)
    results: list[tuple[float, float, int, int]] = []
    for k in scales:
        f = google_px * k / county_px
        g = gradient(resample(google, f))
        if g.shape[0] < template.shape[0] or g.shape[1] < template.shape[1]:
            continue
        grid = ncc(template, weight, g)
        at = np.unravel_index(int(np.argmax(grid)), grid.shape)
        results.append((k, float(grid[at]), int(at[1]), int(at[0])))
    if not results:
        raise ValueError("Google's picture is smaller than the county's at every scale")
    best = max(range(len(results)), key=lambda i: results[i][1])
    k, score, sx, sy = results[best]
    if 0 < best < len(results) - 1:
        (k0, s0, _, _), (k2, s2, _, _) = results[best - 1], results[best + 1]
        denom = s0 - 2 * score + s2
        if denom < 0:
            k = k + 0.5 * (s0 - s2) / denom * (k2 - k0) / 2
    f_best = google_px * results[best][0] / county_px
    # The template's corner at (sx, sy) in the resampled Google picture: county (0, 0) is
    # Google (sx / f, sy / f), so county (x, y) is Google ((x + sx) / f, (y + sy) / f).
    return Registration(
        k=k,
        score=score,
        f=f_best,
        x0=-sx,
        y0=-sy,
        scores=tuple((r[0], r[1]) for r in results),
    )


def edge_offset_px(grey: Array, mask: npt.NDArray[np.bool_], reach_px: int = 12) -> float:
    """Where the picture's edge lies against a drawn outline: the signed distance (pixels,
    positive outward) of the band around the outline whose mean gradient is highest. 0: the
    outline sits on the edge; +2: the visible edge is two pixels outside it (the outline is
    drawn small). The truth's own edge accuracy (EVAL_REPORT 106.1)."""
    from scipy import ndimage

    g = gradient(grey)
    # Rounded distance in whole pixels: -1 is the outline's inner pixel ring, +1 its outer one.
    outside = np.rint(ndimage.distance_transform_edt(~mask))
    inside = np.rint(ndimage.distance_transform_edt(mask))
    signed = np.where(mask, -inside, outside)
    best, at = -1.0, 0.0
    for d in range(-reach_px, reach_px + 1):
        if d == 0:
            continue
        band = signed == d
        if band.sum() < 20:
            continue
        value = float(g[band].mean())
        if value > best:
            # A ring's centre lies half a pixel from the outline.
            best, at = value, d - 0.5 if d > 0 else d + 0.5
    return at


__all__ = ["SCALES", "Registration", "edge_offset_px", "gradient", "ncc", "register", "resample"]
