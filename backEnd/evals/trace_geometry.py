"""``python -m evals.trace_geometry``: which formula turns a traced lon/lat shape into square
feet and feet (EVAL_REPORT 97), and how the scale of an image carries into its quantity.

The engineer's traced measurement (:mod:`basis.domain.traced`) needs area and length on the
earth from WGS84 corners. Three candidates, each run on the same shapes:

* ``web_mercator_pixels``: the shape in Web Mercator metres (EPSG:3857, the projection of every
  web map) with no latitude correction: what a tool that counts screen pixels does, the
  classic bug (a cautionary baseline);
* ``sphere_mean_radius``: the ring area of Chamberlain and Duquette, "Some Algorithms for
  Polygons on a Sphere" (JPL Publication 07-03, 2007) and the haversine length, on a sphere
  of the WGS84 mean radius 6,371,008.8 m: what Turf.js (``@turf/area``, ``@turf/length``,
  ``earthRadius``) and OpenLayers (``ol/sphere`` ``getArea``, ``getLength``,
  ``DEFAULT_RADIUS``) do;
* ``tangent_plane_wgs84``: a local tangent plane with the WGS84 meridional and prime-vertical
  radii of curvature at the shape's mean latitude (:func:`basis.domain.traced.plane`).

An ellipsoidal geodesic polygon area (``geographiclib`` or ``pyproj``) would be a fourth; neither
is in ``uv.lock`` and none is added for it.

**The answer key** has exact values:

* a quadrilateral bounded by two meridians and two parallels (a "box") has, on the ellipsoid,
  ``A = (a^2 dlambda / 2) (q(phi2) - q(phi1))`` with Snyder's authalic function ``q(phi) =
  (1 - e^2) [sin phi / (1 - e^2 sin^2 phi) - (1 / 2e) ln((1 - e sin phi) / (1 + e sin phi))]``
  (J. P. Snyder, *Map Projections: A Working Manual*, USGS Professional Paper 1395, 1987,
  ch. 3, the authalic latitude): the integral of the area element ``M N cos phi dphi
  dlambda`` with ``s = sin phi``. The closed form is cross-checked against a 20-point
  Gauss-Legendre integration of the same element (:func:`box_area_integrated_sf`), so the
  key is not only trusted;
* a meridian arc: the 20-point Gauss-Legendre integral of ``M``, cross-checked against
  Helmert's series in the third flattening ``n`` (:func:`meridian_arc_series_ft`);
* a parallel arc: ``N cos phi dlambda``, exact;
* a diagonal and a rotated shape: corners placed by Vincenty's direct geodesic (T. Vincenty,
  *Survey Review* 23(176), 1975), so the length is the distance asked for and a rotated
  rectangle keeps the area it was drawn with.

A box's edges along parallels are exactly what a Web Mercator map draws as straight lines
(a parallel is a horizontal line there), so the box is the shape as traced; a method whose
polygon edges are geodesics would see a slightly different shape, and :func:`closing_edge_lens`
measures how different (on the sphere) so a reviewer sees the key's own limit.

**The image-space table** is closed form, no data: a 34 ft reference clicked 1, 2 and 5 pixels
wrong at 0.25, 0.5 and 1 ft per pixel, carried into a 20 x 40 ft driveway and a 150 ft fence,
beside the expected error :func:`basis.domain.traced.expected_error_pct` states.

Deterministic, no model call, cost $0, tokens none. ``--dry-run`` runs two latitudes and two
sizes (the unit test and CI). The run files under ``verification/traced_measurement/v1/``.

Usage (from ``backEnd/``)::

    uv run python -m evals.trace_geometry \\
        --out "$BASIS_EVAL_DATA_DIR/predictions/trace_geometry_<date>"
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from basis.domain import traced
from evals import record

COMPONENT: Final = "traced_measurement"
VERSION: Final = "v1"
A: Final = traced.WGS84_A
E2: Final = traced.WGS84_E2
E: Final = math.sqrt(E2)
B2: Final = A * A * (1 - E2)
R_MERCATOR: Final = 6_378_137.0  # EPSG:3857's sphere
R_MEAN: Final = 6_371_008.8  # Turf.js earthRadius, OpenLayers DEFAULT_RADIUS
FT: Final = traced.FT_PER_M
SF: Final = FT * FT

LATITUDES: Final = tuple(range(25, 50, 2))  # 25 to 49 N
# (name, east-west ft, north-south ft): about 100 SF, a driveway, a lot, an acre, ten acres.
SIZES: Final = (
    ("100 SF", 10.0, 10.0),
    ("1,000 SF (driveway)", 20.0, 50.0),
    ("10,000 SF", 100.0, 100.0),
    ("1 acre", 208.71, 208.71),
    ("10 acres", 660.0, 660.0),
)
LENGTHS_FT: Final = (10.0, 50.0, 150.0, 500.0, 5280.0)
LONG_FT: Final = (32_808.4, 328_084.0)  # 10 km and 100 km: where the plane stops being exact
ROTATIONS: Final = tuple(range(0, 180, 15))
LON0: Final = -88.9

Ring = list[tuple[float, float]]


# --------------------------------------------------------------------------- the answer key


def authalic_q(lat: float) -> float:
    s = math.sin(math.radians(lat))
    return (1 - E2) * (s / (1 - E2 * s * s) - (1 / (2 * E)) * math.log((1 - E * s) / (1 + E * s)))


def box_area_closed_sf(lat1: float, lat2: float, dlon: float) -> float:
    return A * A * math.radians(dlon) / 2 * (authalic_q(lat2) - authalic_q(lat1)) * SF


_NODES, _WEIGHTS = np.polynomial.legendre.leggauss(20)


def _gauss(f: Callable[[float], float], lo: float, hi: float) -> float:
    half, mid = (hi - lo) / 2, (hi + lo) / 2
    return half * sum(w * f(mid + half * x) for x, w in zip(_NODES, _WEIGHTS, strict=True))


def box_area_integrated_sf(lat1: float, lat2: float, dlon: float) -> float:
    def element(phi: float) -> float:
        s = math.sin(phi)
        return A * A * (1 - E2) * math.cos(phi) / (1 - E2 * s * s) ** 2

    return _gauss(element, math.radians(lat1), math.radians(lat2)) * math.radians(dlon) * SF


def meridian_arc_integrated_ft(lat1: float, lat2: float) -> float:
    return (
        _gauss(
            lambda phi: traced.radii(math.degrees(phi))[0], math.radians(lat1), math.radians(lat2)
        )
        * FT
    )


def meridian_arc_series_ft(lat1: float, lat2: float) -> float:
    """Helmert's series for the meridian distance (in the third flattening n)."""
    n = traced.WGS84_F / (2 - traced.WGS84_F)

    def m(lat: float) -> float:
        p = math.radians(lat)
        return (
            A
            / (1 + n)
            * (
                (1 + n**2 / 4 + n**4 / 64) * p
                - 1.5 * (n - n**3 / 8) * math.sin(2 * p)
                + 15 / 16 * (n**2 - n**4 / 4) * math.sin(4 * p)
                - 35 / 48 * n**3 * math.sin(6 * p)
                + 315 / 512 * n**4 * math.sin(8 * p)
            )
        )

    return (m(lat2) - m(lat1)) * FT


def parallel_arc_ft(lat: float, dlon: float) -> float:
    return traced.radii(lat)[1] * math.cos(math.radians(lat)) * math.radians(dlon) * FT


def vincenty_direct(lat: float, lon: float, azimuth: float, metres: float) -> tuple[float, float]:
    """Vincenty's direct geodesic on WGS84: the point ``metres`` from (lat, lon) at
    ``azimuth`` degrees from north."""
    f = traced.WGS84_F
    b = A * (1 - f)
    alpha1 = math.radians(azimuth)
    tan_u1 = (1 - f) * math.tan(math.radians(lat))
    cos_u1 = 1 / math.sqrt(1 + tan_u1 * tan_u1)
    sin_u1 = tan_u1 * cos_u1
    sigma1 = math.atan2(tan_u1, math.cos(alpha1))
    sin_alpha = cos_u1 * math.sin(alpha1)
    cos2_alpha = 1 - sin_alpha * sin_alpha
    u2 = cos2_alpha * (A * A - b * b) / (b * b)
    big_a = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    big_b = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    sigma = metres / (b * big_a)
    for _ in range(200):
        cos_2sm = math.cos(2 * sigma1 + sigma)
        sin_s, cos_s = math.sin(sigma), math.cos(sigma)
        delta = (
            big_b
            * sin_s
            * (
                cos_2sm
                + big_b
                / 4
                * (
                    cos_s * (-1 + 2 * cos_2sm**2)
                    - big_b / 6 * cos_2sm * (-3 + 4 * sin_s**2) * (-3 + 4 * cos_2sm**2)
                )
            )
        )
        previous, sigma = sigma, metres / (b * big_a) + delta
        if abs(sigma - previous) < 1e-14:
            break
    sin_s, cos_s = math.sin(sigma), math.cos(sigma)
    cos_2sm = math.cos(2 * sigma1 + sigma)
    tmp = sin_u1 * sin_s - cos_u1 * cos_s * math.cos(alpha1)
    lat2 = math.atan2(
        sin_u1 * cos_s + cos_u1 * sin_s * math.cos(alpha1),
        (1 - f) * math.sqrt(sin_alpha**2 + tmp * tmp),
    )
    lam = math.atan2(sin_s * math.sin(alpha1), cos_u1 * cos_s - sin_u1 * sin_s * math.cos(alpha1))
    c = f / 16 * cos2_alpha * (4 + f * (4 - 3 * cos2_alpha))
    big_l = lam - (1 - c) * f * sin_alpha * (
        sigma + c * sin_s * (cos_2sm + c * cos_s * (-1 + 2 * cos_2sm**2))
    )
    return math.degrees(lat2), lon + math.degrees(big_l)


def closing_edge_lens(lat: float, dlon: float) -> float:
    """On the mean sphere, the area (SF) between the parallel at ``lat`` and the great circle
    through its two ends ``dlon`` apart: how much a geodesic-edged polygon differs from the
    parallel-edged box along one edge."""
    phi, half = math.radians(lat), math.radians(dlon) / 2

    def gap(x: float) -> float:
        lat_gc = math.atan(math.tan(phi) * math.cos(x) / math.cos(half))
        return math.sin(lat_gc) - math.sin(phi)

    return R_MEAN * R_MEAN * _gauss(gap, -half, half) * SF


# --------------------------------------------------------------------------- candidates


def _shoelace(points: Sequence[tuple[float, float]]) -> float:
    return abs(traced.shoelace(points))


def mercator_area_sf(ring: Ring) -> float:
    x0 = R_MERCATOR * math.radians(ring[0][0])
    y0 = R_MERCATOR * math.log(math.tan(math.pi / 4 + math.radians(ring[0][1]) / 2))
    pts = [
        (
            R_MERCATOR * math.radians(lon) - x0,
            R_MERCATOR * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) - y0,
        )
        for lon, lat in ring
    ]
    return _shoelace(pts) * SF


def mercator_length_ft(path: Ring) -> float:
    pts = [
        (
            R_MERCATOR * math.radians(lon),
            R_MERCATOR * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
        )
        for lon, lat in path
    ]
    return sum(math.dist(p, q) for p, q in zip(pts, pts[1:], strict=False)) * FT


def sphere_area_sf(ring: Ring) -> float:
    """``@turf/area`` ``ringArea``: sum of (lon[i+1] - lon[i-1]) sin(lat[i]) x R^2 / 2."""
    closed = [*ring, ring[0]]
    n = len(closed) - 1
    total = 0.0
    for i in range(n):
        lower, middle, upper = closed[i], closed[(i + 1) % n], closed[(i + 2) % n]
        total += (math.radians(upper[0]) - math.radians(lower[0])) * math.sin(
            math.radians(middle[1])
        )
    return abs(total * R_MEAN * R_MEAN / 2) * SF


def haversine_length_ft(path: Ring) -> float:
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(path, path[1:], strict=False):
        p1, p2 = math.radians(lat1), math.radians(lat2)
        h = (
            math.sin((p2 - p1) / 2) ** 2
            + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
        )
        total += 2 * R_MEAN * math.asin(math.sqrt(h))
    return total * FT


def plane_area_sf(ring: Ring) -> float:
    lats = [lat for _, lat in ring]
    return _shoelace(traced.plane(ring, ring[0], (min(lats) + max(lats)) / 2))


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    setup: str
    area: Callable[[Ring], float]
    length: Callable[[Ring], float]


CANDIDATES: Final = (
    Candidate(
        "web_mercator_pixels",
        "Screen pixels of a web map (Web Mercator, no latitude correction)",
        mercator_area_sf,
        mercator_length_ft,
    ),
    Candidate(
        "sphere_mean_radius",
        "Sphere of mean radius (Turf.js, OpenLayers): Chamberlain-Duquette area, haversine length",
        sphere_area_sf,
        haversine_length_ft,
    ),
    Candidate(
        "tangent_plane_wgs84",
        "Local tangent plane on the WGS84 ellipsoid (shipped)",
        plane_area_sf,
        traced.lonlat_path_length_ft,
    ),
)
SHIPPED: Final = "tangent_plane_wgs84"


# --------------------------------------------------------------------------- the cases


def box(lat: float, east_ft: float, north_ft: float) -> tuple[Ring, float, float]:
    """A box with its south-west corner at (LON0, lat): ``(ring, dlat, dlon)``."""
    m, n = traced.radii(lat)
    dlat = math.degrees(north_ft / FT / m)
    dlon = math.degrees(east_ft / FT / (n * math.cos(math.radians(lat))))
    ring = [(LON0, lat), (LON0 + dlon, lat), (LON0 + dlon, lat + dlat), (LON0, lat + dlat)]
    return ring, dlat, dlon


def pct(value: float, key: float) -> float:
    return abs(value - key) / key * 100


def _time_us(fn: Callable[[Ring], float], shape: Ring, repeats: int) -> float:
    start = time.perf_counter()
    for _ in range(repeats):
        fn(shape)
    return (time.perf_counter() - start) / repeats * 1e6


def area_cases(
    latitudes: Sequence[int], sizes: Sequence[tuple[str, float, float]]
) -> list[dict[str, Any]]:
    rows = []
    for lat in latitudes:
        for name, east, north in sizes:
            ring, dlat, dlon = box(lat, east, north)
            key = box_area_closed_sf(lat, lat + dlat, dlon)
            check = box_area_integrated_sf(lat, lat + dlat, dlon)
            rows.append(
                {
                    "lat": lat,
                    "size": name,
                    "key_sf": key,
                    "key_vs_integration_pct": pct(check, key),
                    "lens_pct": abs(
                        closing_edge_lens(lat + dlat, dlon) - closing_edge_lens(lat, dlon)
                    )
                    / key
                    * 100,
                    "one_lens_pct": closing_edge_lens(lat, dlon) / key * 100,
                    **{c.name: pct(c.area(ring), key) for c in CANDIDATES},
                }
            )
    return rows


def length_cases(latitudes: Sequence[int], lengths: Sequence[float]) -> list[dict[str, Any]]:
    rows = []
    for lat in latitudes:
        for feet in lengths:
            m, n = traced.radii(lat)
            dlat = math.degrees(feet / FT / m)
            dlon = math.degrees(feet / FT / (n * math.cos(math.radians(lat))))
            meridian = [(LON0, lat), (LON0, lat + dlat)]
            key_m = meridian_arc_integrated_ft(lat, lat + dlat)
            parallel = [(LON0, lat), (LON0 + dlon, lat)]
            key_p = parallel_arc_ft(lat, dlon)
            end_lat, end_lon = vincenty_direct(lat, LON0, 45.0, feet / FT)
            diagonal = [(LON0, lat), (end_lon, end_lat)]
            for kind, path, key in (
                ("meridian", meridian, key_m),
                ("parallel", parallel, key_p),
                ("diagonal", diagonal, feet),
            ):
                rows.append(
                    {
                        "lat": lat,
                        "length_ft": feet,
                        "kind": kind,
                        "key_ft": key,
                        "key_vs_series_pct": pct(meridian_arc_series_ft(lat, lat + dlat), key_m)
                        if kind == "meridian"
                        else None,
                        **{c.name: pct(c.length(path), key) for c in CANDIDATES},
                    }
                )
    return rows


def rotated_rectangle(lat: float, east_ft: float, north_ft: float, theta: float) -> Ring:
    """A rectangle centred at (LON0, lat), turned ``theta`` degrees, its corners placed by
    Vincenty's direct geodesic from the centre."""
    corners = [
        (-east_ft / 2, -north_ft / 2),
        (east_ft / 2, -north_ft / 2),
        (east_ft / 2, north_ft / 2),
        (-east_ft / 2, north_ft / 2),
    ]
    ring = []
    for x, y in corners:
        azimuth = math.degrees(math.atan2(x, y)) + theta
        lat2, lon2 = vincenty_direct(lat, LON0, azimuth, math.hypot(x, y) / FT)
        ring.append((lon2, lat2))
    return ring


def rotation_cases(latitudes: Sequence[int]) -> list[dict[str, Any]]:
    rows = []
    for lat in latitudes:
        for name, east, north in (("1,000 SF (driveway)", 20.0, 50.0), ("10 acres", 660.0, 660.0)):
            target = east * north
            for c in CANDIDATES:
                areas = [c.area(rotated_rectangle(lat, east, north, t)) for t in ROTATIONS]
                rows.append(
                    {
                        "lat": lat,
                        "size": name,
                        "candidate": c.name,
                        "spread_pct": (max(areas) - min(areas)) / target * 100,
                        "worst_vs_drawn_pct": max(pct(a, target) for a in areas),
                    }
                )
    return rows


def timing(repeats: int) -> dict[str, Any]:
    ring, _, _ = box(43, 20.0, 50.0)
    path = [(LON0, 43.0), (LON0 + 0.0005, 43.0003), (LON0 + 0.001, 43.0)]
    per: dict[str, Any] = {
        c.name: {
            "area_us": round(_time_us(c.area, ring, repeats), 2),
            "length_us": round(_time_us(c.length, path, repeats), 2),
        }
        for c in CANDIDATES
    }
    driveway = {
        "unit": "SF",
        "space": "lonlat",
        "shapes": [{"kind": "area", "ring": [list(p) for p in ring]}],
        "image": {"source": "timing", "pixel_size_m": 0.15},
    }
    start = time.perf_counter()
    for _ in range(max(1, repeats // 10)):
        traced.measure(driveway)
    per["measure_driveway_ms"] = round(
        (time.perf_counter() - start) / max(1, repeats // 10) * 1e3, 3
    )
    circle = [
        [
            50_000 + 400 * math.cos(2 * math.pi * k / 500),
            50_000 + 400 * math.sin(2 * math.pi * k / 500),
        ]
        for k in range(500)
    ]
    largest = {
        "unit": "SF",
        "space": "image",
        "shapes": [{"kind": "area", "ring": circle}] * 10,
        "scale": {"points": [[0, 0], [100, 0]], "length": 10, "unit": "ft"},
        "image": {"source": "timing"},
    }
    start = time.perf_counter()
    traced.measure(largest)
    per["measure_5000_points_ms"] = round((time.perf_counter() - start) * 1e3, 1)
    return per


def image_table() -> list[dict[str, Any]]:
    """A 34 ft reference clicked k pixels wrong (the distance off by k), at r ft per pixel:
    the scale is off by k / d (d = 34 / r pixels), an area by (1 + k/d)^2 - 1, a length by
    k / d. Beside it the expected error Basis states for the same trace (one pixel on every
    edge and one on each scale point)."""
    rows = []
    for res in (0.25, 0.5, 1.0):
        d = 34.0 / res
        for k in (1, 2, 5):
            s = k / d
            area_err = (1 + s) ** 2 - 1
            stated_area = traced.expected_error_pct(
                area_sf=800.0, length_ft=None, edges_ft=120.0, lines=1, pixel_ft=res, scale_px=d
            )
            stated_fence = traced.expected_error_pct(
                area_sf=None, length_ft=150.0, edges_ft=150.0, lines=1, pixel_ft=res, scale_px=d
            )
            rows.append(
                {
                    "ft_per_px": res,
                    "reference_px": round(d, 1),
                    "clicked_off_px": k,
                    "scale_error_pct": round(s * 100, 2),
                    "driveway_error_pct": round(area_err * 100, 2),
                    "driveway_error_sf": round(800 * area_err, 1),
                    "fence_error_pct": round(s * 100, 2),
                    "fence_error_ft": round(150 * s, 2),
                    "stated_driveway_pct": str(stated_area),
                    "stated_fence_pct": str(stated_fence),
                }
            )
    return rows


# --------------------------------------------------------------------------- the report


def summarize(rows: Sequence[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    out = []
    for value in dict.fromkeys(r[group] for r in rows):
        chosen = [r for r in rows if r[group] == value]
        for c in CANDIDATES:
            errors = [r[c.name] for r in chosen]
            out.append(
                {
                    group: value,
                    "candidate": c.name,
                    "mean_pct": statistics.fmean(errors),
                    "worst_pct": max(errors),
                    "cases": len(errors),
                }
            )
    return out


def _g(value: float) -> str:
    if value == 0:
        return "0"
    if value >= 0.01:
        return f"{value:.3f}"
    return f"{value:.1e}"


def render(result: dict[str, Any]) -> str:
    setups = {c.name: c.setup for c in CANDIDATES}
    lines = ["# Traced geometry: lon/lat candidates against exact answers", ""]
    lines += ["## Area, by size (absolute error, percent; lower is better)", ""]
    lines += ["| Size | Setup | Mean error % | Worst error % | Shapes |", "|---|---|---|---|---|"]
    for row in result["area_by_size"]:
        lines.append(
            f"| {row['size']} | {setups[row['candidate']]} | {_g(row['mean_pct'])} | "
            f"{_g(row['worst_pct'])} | {row['cases']} |"
        )
    lines += ["", "## Length, by kind (absolute error, percent; lower is better)", ""]
    lines += ["| Kind | Setup | Mean error % | Worst error % | Lines |", "|---|---|---|---|---|"]
    for row in result["length_by_kind"]:
        lines.append(
            f"| {row['kind']} | {setups[row['candidate']]} | {_g(row['mean_pct'])} | "
            f"{_g(row['worst_pct'])} | {row['cases']} |"
        )
    lines += ["", "## The answer key's own checks", ""]
    for key, value in result["key_checks"].items():
        lines.append(f"* {key}: {_g(value) if isinstance(value, float) else value}")
    lines += ["", "## Rotation (spread of the area over 12 turns, percent)", ""]
    lines += ["| Size | Setup | Worst spread % | Worst against drawn % |", "|---|---|---|---|"]
    for row in result["rotation_summary"]:
        lines.append(
            f"| {row['size']} | {setups[row['candidate']]} | {_g(row['spread_pct'])} | "
            f"{_g(row['worst_vs_drawn_pct'])} |"
        )
    lines += ["", "## Time", "", "```", json.dumps(result["timing"], indent=1), "```", ""]
    lines += ["## Image scale (closed form)", ""]
    lines += [
        "| ft per px | Reference px | Clicked off px | Scale error % | Driveway error % (SF) "
        "| Fence error % (ft) | Stated driveway % | Stated fence % |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in result["image_table"]:
        lines.append(
            f"| {r['ft_per_px']} | {r['reference_px']} | {r['clicked_off_px']} | "
            f"{r['scale_error_pct']} | {r['driveway_error_pct']} ({r['driveway_error_sf']}) | "
            f"{r['fence_error_pct']} ({r['fence_error_ft']}) | {r['stated_driveway_pct']} | "
            f"{r['stated_fence_pct']} |"
        )
    return "\n".join(lines) + "\n"


def run_all(*, dry_run: bool, repeats: int) -> dict[str, Any]:
    latitudes = (25, 49) if dry_run else LATITUDES
    sizes = SIZES[1:2] + SIZES[-1:] if dry_run else SIZES
    lengths = (150.0,) if dry_run else (*LENGTHS_FT, *LONG_FT)
    areas = area_cases(latitudes, sizes)
    lengths_rows = length_cases(latitudes, lengths)
    short = [r for r in lengths_rows if r["length_ft"] <= 5280]
    rotations = rotation_cases(latitudes)
    rotation_summary = []
    for size in dict.fromkeys(r["size"] for r in rotations):
        for c in CANDIDATES:
            chosen = [r for r in rotations if r["size"] == size and r["candidate"] == c.name]
            rotation_summary.append(
                {
                    "size": size,
                    "candidate": c.name,
                    "spread_pct": max(r["spread_pct"] for r in chosen),
                    "worst_vs_drawn_pct": max(r["worst_vs_drawn_pct"] for r in chosen),
                }
            )
    meridians = [r for r in lengths_rows if r["kind"] == "meridian"]
    key_checks = {
        "closed form vs integration, worst %": max(r["key_vs_integration_pct"] for r in areas),
        "meridian integral vs Helmert series, worst %": max(
            r["key_vs_series_pct"] for r in meridians
        ),
        "geodesic vs parallel edges (net of both edges), worst % of the box": max(
            r["lens_pct"] for r in areas
        ),
        "one edge's lens, worst % of the box": max(r["one_lens_pct"] for r in areas),
    }
    return {
        "dry_run": dry_run,
        "latitudes": list(latitudes),
        "sizes": [s[0] for s in sizes],
        "area_cases": areas,
        "length_cases": lengths_rows,
        "area_by_size": summarize(areas, "size"),
        "length_by_kind": summarize(short, "kind"),
        "length_by_size": summarize(lengths_rows, "length_ft"),
        "rotation_cases": rotations,
        "rotation_summary": rotation_summary,
        "key_checks": key_checks,
        "timing": timing(repeats),
        "image_table": image_table(),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.trace_geometry", description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--dry-run", action="store_true", help="two latitudes, two sizes")
    parser.add_argument("--repeats", type=int, default=2000, help="timing repetitions")
    return parser.parse_args(argv)


# The shipped method must be exact to this (percent) on every box and short line: far below
# the 0.01 a quantity is rounded to on a 100 SF shape (0.01 %).
SHIPPED_TOLERANCE_PCT: Final = 1e-4


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        result = run_all(dry_run=args.dry_run, repeats=args.repeats)
        worst_area = max(r[SHIPPED] for r in result["area_cases"])
        short = [r for r in result["length_cases"] if r["length_ft"] <= 5280]
        worst_length = max(r[SHIPPED] for r in short)
        run.case(
            "shipped_area_exact",
            "passed" if worst_area <= SHIPPED_TOLERANCE_PCT else "failed",
            detail={"worst_pct": worst_area},
        )
        run.case(
            "shipped_length_exact",
            "passed" if worst_length <= SHIPPED_TOLERANCE_PCT else "failed",
            detail={"worst_pct": worst_length},
        )
        key = result["key_checks"]["closed form vs integration, worst %"]
        run.case(
            "answer_key_cross_check",
            "passed" if key <= 1e-6 else "failed",
            detail={"worst_pct": key},
        )
        spread = max(
            r["spread_pct"] for r in result["rotation_summary"] if r["candidate"] == SHIPPED
        )
        run.case(
            "shipped_rotation_consistent",
            "passed" if spread <= SHIPPED_TOLERANCE_PCT else "failed",
            detail={"spread_pct": spread},
        )
        text = render(result)
        (args.out / "trace_geometry.md").write_text(text, encoding="utf-8")
        (args.out / "trace_geometry.json").write_text(
            json.dumps(result, indent=1) + "\n", encoding="utf-8"
        )
        print(text)
        summary = {
            "dry_run": args.dry_run,
            "shipped": SHIPPED,
            "worst_area_pct": {
                c.name: max(r[c.name] for r in result["area_cases"]) for c in CANDIDATES
            },
            "worst_length_pct": {c.name: max(r[c.name] for r in short) for c in CANDIDATES},
            "key_checks": result["key_checks"],
            "timing": result["timing"],
            "cost_usd": 0,
            "tokens": None,
        }
        run.finish(summary)
    except (ValueError, OSError, ArithmeticError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0 if all(c["outcome"] == "passed" for c in run.cases) else 1


__all__ = [
    "CANDIDATES",
    "SHIPPED",
    "box",
    "box_area_closed_sf",
    "box_area_integrated_sf",
    "closing_edge_lens",
    "image_table",
    "main",
    "meridian_arc_integrated_ft",
    "meridian_arc_series_ft",
    "parallel_arc_ft",
    "run_all",
    "vincenty_direct",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
