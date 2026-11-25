"""The lidar roof outline as a polygon (EVAL_REPORT 104.2, :mod:`basis.geo.lidar`).

A rectangle of cells traces to exactly its 4 corners and an L to its 6, counter-clockwise with
no closing point and the cells' area; a stair-stepped wall is simplified within the tolerance;
two cells that meet at a corner do not loop; the lon/lat corners come back to the cell edges
within 1 cm; the summary's heights, footprint, counts and flags are those main computed before
the outline existed (values printed from main's ``measure`` on the same synthetic points); an
API body stored before the outline is still read."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from basis.api.routes.property_public import _lidar_out
from basis.domain.traced import lonlat_path_length_ft, ring_problem, shoelace
from basis.geo import lidar
from basis.geo.geometry import to_mercator

from .test_parcels_and_lidar import LAT, LON, box_m, synthetic_points


def cells(shape: tuple[int, int], *blocks: tuple[int, int, int, int]) -> np.ndarray:
    m = np.zeros(shape, dtype=bool)
    for i0, i1, j0, j1 in blocks:
        m[i0:i1, j0:j1] = True
    return m


def test_a_rectangle_is_exactly_its_four_corners_counter_clockwise_with_the_cells_area() -> None:
    mask = cells((20, 20), (3, 13, 5, 9))  # 10 cells east by 4 north
    ring = lidar.trace_outline(mask)
    assert ring == [(3, 5), (13, 5), (13, 9), (3, 9)]
    assert shoelace(ring) == mask.sum() > 0  # counter-clockwise, area in cells
    assert ring[0] != ring[-1]


def test_an_l_has_six_corners_and_a_t_eight() -> None:
    l_shape = cells((20, 20), (2, 12, 2, 6), (2, 6, 6, 12))
    ring = lidar.trace_outline(l_shape)
    assert len(ring) == 6 and shoelace(ring) == l_shape.sum()
    t_shape = cells((30, 30), (2, 26, 16, 24), (10, 18, 2, 16))
    assert len(lidar.trace_outline(t_shape)) == 8
    assert lidar.simplify_ring([(float(i), float(j)) for i, j in ring], 0.71) == [
        (float(i), float(j)) for i, j in ring
    ]


def test_a_stair_stepped_wall_is_simplified_within_the_tolerance() -> None:
    from evals.lidar_outline_tolerance import rasterize, turned

    mask = rasterize(turned([(0, 0), (12, 0), (12, 8), (0, 8)], 30))
    traced = [(i * lidar.CELL_M, j * lidar.CELL_M) for i, j in lidar.trace_outline(mask)]
    simple = lidar.simplify_ring(traced, lidar.OUTLINE_TOLERANCE_M)
    assert len(traced) > 40 and len(simple) == 4
    assert ring_problem(simple) is None and shoelace(simple) > 0

    def to_ring(p: tuple[float, float]) -> float:
        n = len(simple)
        best = math.inf
        for k in range(n):
            (ax, ay), (bx, by) = simple[k], simple[(k + 1) % n]
            t = max(
                0.0,
                min(
                    1.0,
                    ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay))
                    / ((bx - ax) ** 2 + (by - ay) ** 2),
                ),
            )
            best = min(best, math.dist(p, (ax + t * (bx - ax), ay + t * (by - ay))))
        return best

    assert max(to_ring(p) for p in traced) <= lidar.OUTLINE_TOLERANCE_M + 1e-9
    assert abs(shoelace(simple)) == pytest.approx(mask.sum() * lidar.CELL_M**2, rel=0.011)


def test_two_cells_meeting_at_a_corner_and_an_empty_mask() -> None:
    pinch = cells((6, 6), (1, 2, 1, 2), (2, 3, 2, 3))
    ring = lidar.trace_outline(pinch)  # no endless walk; the left turn keeps one cell's square
    assert len(ring) == 4 and shoelace(ring) == 1
    assert lidar.trace_outline(np.zeros((5, 5), dtype=bool)) == []
    assert lidar.simplify_ring([], 1.0) == []


def test_the_lon_lat_corners_come_back_to_the_cell_edges_within_a_centimetre() -> None:
    mask = cells((80, 80), (30, 50, 20, 60))
    k = math.cos(math.radians(LAT))
    cx, cy = to_mercator(LON, LAT)
    outline = lidar.outline_of(mask, west_m=-20.0, south_m=-20.0, cx=cx, cy=cy, k=k)
    expected = [(-20 + i * 0.5, -20 + j * 0.5) for i, j in lidar.trace_outline(mask)]
    assert len(outline) == 4
    for (lon, lat), (e, n) in zip(outline, expected, strict=True):
        x, y = to_mercator(lon, lat)
        assert math.dist(((x - cx) * k, (y - cy) * k), (e, n)) < 0.01


def test_the_synthetic_buildings_outline_is_its_footprint() -> None:
    _, _, b = lidar.measure(synthetic_points(), LON, LAT, box_m(40))
    assert b is not None and len(b.outline) == 4
    closed = [*b.outline, b.outline[0]]
    perimeter_m = lonlat_path_length_ft(closed) * 0.3048
    assert perimeter_m == pytest.approx(60, rel=0.05)  # a 10 x 20 m building
    from basis.domain.traced import measure

    area = measure(
        {
            "unit": "SF",
            "space": "lonlat",
            "shapes": [{"kind": "area", "ring": [list(p) for p in b.outline]}],
            "image": {"source": "lidar"},
        }
    ).area_sf
    assert float(area) * 0.3048**2 == pytest.approx(b.footprint_m2, rel=0.005)


# Printed from origin/main's ``measure`` (before the outline) on the same points (2026-11-25).
BEFORE = {
    "on": {
        "footprint_m2": 199.25,
        "peak_m": 7.976131566497841,
        "max_m": 7.997784820023213,
        "eave_m": 5.137077692436364,
        "ground_elevation_m": 100.00030820954296,
        "points": 748,
        "distance_m": 0.0,
        "outline_cells": 118,
        "reaches_box_edge": False,
    },
    "cut": {
        "footprint_m2": 154.25,
        "peak_m": 7.978309593787657,
        "max_m": 7.997390116130617,
        "eave_m": 5.131147588277614,
        "ground_elevation_m": 100.00070291343556,
        "points": 568,
        "distance_m": 0.0,
        "outline_cells": 100,
        "reaches_box_edge": True,
    },
    "shifted": {
        "footprint_m2": 195.75,
        "peak_m": 7.9663405931341345,
        "max_m": 7.990450030420135,
        "eave_m": 5.132795811913752,
        "ground_elevation_m": 99.99916365949647,
        "points": 784,
        "distance_m": 10.2530483269541,
        "outline_cells": 122,
        "reaches_box_edge": False,
    },
}


@pytest.mark.regression
def test_heights_footprint_and_flags_are_those_main_computed() -> None:
    pts = synthetic_points()
    off = synthetic_points(pin_on_building=False)
    shifted = lidar.Points(off.x - 15 / math.cos(math.radians(LAT)), off.y, off.z, off.cls)
    for name, points, half in (("on", pts, 40), ("cut", pts, 8), ("shifted", shifted, 40)):
        _, _, b = lidar.measure(points, LON, LAT, box_m(half))
        assert b is not None
        got = dataclasses.asdict(b)
        assert len(got.pop("outline")) >= 4
        assert got == BEFORE[name], name


def test_an_api_body_stored_before_the_outline_is_read_and_a_new_one_carries_it() -> None:
    old = {
        "project": "P_2019",
        "year": 2019,
        "points": 10,
        "density_per_m2": 1.0,
        "code": None,
        "credit": "U.S. Geological Survey",
        "terms_class": "public-domain",
        "terms_url": "https://example.test/terms",
        "building": dict(BEFORE["on"]),
    }
    out = _lidar_out(old)
    assert out is not None and out.building is not None and out.building.outline == []
    new = {
        **old,
        "building": {**BEFORE["on"], "outline": [[-89.6, 39.7], [-89.5, 39.7], [-89.5, 39.8]]},
    }
    built = _lidar_out(new)
    assert built is not None and built.building is not None
    assert built.building.outline == [[-89.6, 39.7], [-89.5, 39.7], [-89.5, 39.8]]
