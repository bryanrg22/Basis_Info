"""Coordinates, areas on the ellipsoid and the site-area check (basis.geo.geometry,
basis.geo.site_check; EVAL_REPORT 101)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest
from geographiclib.geodesic import Geodesic

from basis.errors.codes import E
from basis.geo import geometry as g
from basis.geo.site_check import check

LAT, LON = 41.60, -93.60


def test_mercator_round_trip() -> None:
    for lon, lat in [(0, 0), (LON, LAT), (-122.4, 37.8), (179.9, -60)]:
        x, y = g.to_mercator(lon, lat)
        back = g.from_mercator(x, y)
        assert back == pytest.approx((lon, lat), abs=1e-9)
    with pytest.raises(ValueError):
        g.to_mercator(0, 89)


def test_view_box_is_square_on_the_ground_and_centred_on_the_pin() -> None:
    box = g.view_box(LAT, LON, extent_m=160, pixel_m=0.1524, max_px=2048)
    assert (box.width, box.height) == (1050, 1050)
    assert box.pixel_size_m == pytest.approx(160 / 1050)
    # The pixel at the centre is the pin; the west and east edges are 160 m apart on the
    # ellipsoid (within the 0.3 % of Mercator's spherical scale against WGS84 at 43 degrees).
    assert g.pixel_to_lonlat(box.bbox, box.width, box.height, 525, 525) == pytest.approx(
        (LON, LAT), abs=1e-9
    )
    w = g.pixel_to_lonlat(box.bbox, box.width, box.height, 0, 525)
    e = g.pixel_to_lonlat(box.bbox, box.width, box.height, 1050, 525)
    s = g.pixel_to_lonlat(box.bbox, box.width, box.height, 525, 1050)
    n = g.pixel_to_lonlat(box.bbox, box.width, box.height, 525, 0)
    across = Geodesic.WGS84.Inverse(w[1], w[0], e[1], e[0])["s12"]
    down = Geodesic.WGS84.Inverse(s[1], s[0], n[1], n[0])["s12"]
    assert across == pytest.approx(160, rel=0.004)
    assert down == pytest.approx(160, rel=0.004)
    assert across == pytest.approx(down, rel=0.004)  # square on the ground
    px = g.lonlat_to_pixel(box.bbox, box.width, box.height, *e)
    assert px == pytest.approx((1050, 525), abs=1e-6)


def test_view_box_is_coarsened_at_the_pixel_cap() -> None:
    box = g.view_box(LAT, LON, extent_m=160, pixel_m=0.0762, max_px=2048)
    assert box.width == 2048 and box.pixel_size_m == pytest.approx(160 / 2048)
    tiny = g.view_box(LAT, LON, extent_m=5, pixel_m=1.0, max_px=2048)
    assert tiny.width == 16  # never fewer than 16 pixels
    with pytest.raises(ValueError):
        g.view_box(LAT, LON, extent_m=0, pixel_m=1, max_px=2048)


def square(lon: float, lat: float, side_m: float) -> list[tuple[float, float]]:
    dlat = side_m / 111_132.954
    dlon = side_m / (111_412.84 * math.cos(math.radians(lat)))
    return [(lon, lat), (lon + dlon, lat), (lon + dlon, lat + dlat), (lon, lat + dlat), (lon, lat)]


def test_ellipsoid_area_matches_known_values_and_not_mercator() -> None:
    # A one-degree cell at the equator (GeographicLib's own value, 12,308,778,361 m^2).
    cell = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    assert g.ellipsoid_area_m2([cell]) == pytest.approx(12_308_778_361, rel=1e-6)
    # A 100 m square is 10,000 m^2 on the ellipsoid; its Mercator area is 1 / cos^2(lat) times.
    ring = square(LON, LAT, 100)
    area = g.ellipsoid_area_m2([ring])
    assert area == pytest.approx(10_000, rel=2e-3)
    xs = [g.to_mercator(*p) for p in ring]
    merc = abs(sum(xs[i][0] * xs[i + 1][1] - xs[i + 1][0] * xs[i][1] for i in range(4)) / 2)
    assert merc / area == pytest.approx(1 / math.cos(math.radians(LAT)) ** 2, rel=0.01)
    # Orientation does not matter; a hole is subtracted; a MultiPolygon sums.
    assert g.ellipsoid_area_m2([ring[::-1]]) == pytest.approx(area)
    hole = square(LON + 0.0002, LAT + 0.0002, 20)
    assert g.ellipsoid_area_m2([ring, hole]) == pytest.approx(area - 400, rel=2e-3)
    poly = {"type": "Polygon", "coordinates": [ring]}
    multi = {"type": "MultiPolygon", "coordinates": [[ring], [square(LON + 1, LAT, 100)]]}
    assert g.geojson_area_m2(poly) == pytest.approx(area)
    assert g.geojson_area_m2(multi) == pytest.approx(2 * area, rel=1e-3)
    with pytest.raises(ValueError):
        g.geojson_area_m2({"type": "Point", "coordinates": [0, 0]})
    assert g.ellipsoid_area_m2([[(0, 0), (1, 1)]]) == 0.0  # a degenerate ring


def test_point_in_polygon_respects_holes() -> None:
    ring = square(LON, LAT, 100)
    hole = square(LON + 0.0003, LAT + 0.0003, 20)
    geom = {"type": "Polygon", "coordinates": [ring, hole]}
    inside = (LON + 0.0001, LAT + 0.0001)
    in_hole = (LON + 0.0003 + 0.0001, LAT + 0.0003 + 0.00005)
    assert g.point_in_geojson(*inside, geom)
    assert not g.point_in_geojson(*in_hole, geom)
    assert not g.point_in_geojson(LON - 0.01, LAT, geom)
    assert not g.point_in_geojson(LON, LAT, {"type": "Point"})
    assert g.geojson_bounds(geom)[0] == pytest.approx(LON)


@dataclass
class P:
    parcel_id: str | None
    under_pin: bool
    area_m2: float
    layer_acres: float | None


ACRE_SQFT = 43_560.0
ACRE_M2 = g.M2_PER_ACRE


def test_site_check_agrees_at_the_tolerance_and_flags_past_it() -> None:
    appraisal = 1.0 * ACRE_SQFT
    at = check([P("a", True, 1.0499999 * ACRE_M2, None)], appraisal, tolerance_pct=5)
    assert at.status == "agree" and not at.flag and at.code is None
    assert at.difference_pct == pytest.approx(5.0, abs=1e-3)
    past = check([P("a", True, 1.0505 * ACRE_M2, None)], appraisal, tolerance_pct=5)
    assert past.status == "disagree" and past.flag and past.code == E.GEO_SITE_AREA_MISMATCH
    # Exactly at the tolerance agrees; a hair inside it does not.
    parcel = [P("a", True, 1.07 * ACRE_M2, None)]
    exact = abs(check(parcel, appraisal, tolerance_pct=50).difference_pct or 0)
    assert check(parcel, appraisal, tolerance_pct=exact).status == "agree"
    assert check(parcel, appraisal, tolerance_pct=exact * (1 - 1e-12)).status == "disagree"
    below = check([P("a", True, 0.9 * ACRE_M2, None)], appraisal, tolerance_pct=5)
    assert below.status == "disagree" and below.difference_pct == pytest.approx(-10, abs=1e-6)


def test_site_check_takes_either_the_layer_acres_or_the_polygon() -> None:
    # The polygon is 20 % off, the layer's acres are exact: the layer agrees.
    by_layer = check([P("a", True, 1.2 * ACRE_M2, 1.0)], ACRE_SQFT, tolerance_pct=5)
    assert by_layer.status == "agree" and by_layer.parcel_sqft == pytest.approx(ACRE_SQFT)
    # The layer's acres are stale, the polygon is right: the polygon agrees.
    by_polygon = check([P("a", True, 1.0 * ACRE_M2, 1.5)], ACRE_SQFT, tolerance_pct=5)
    assert by_polygon.status == "agree"
    assert by_polygon.layer_sqft == pytest.approx(1.5 * ACRE_SQFT)


def test_site_check_notes_adjacent_parcels_that_sum_to_the_site() -> None:
    # Two parcels of one owner whose acres sum to the appraisal's site area.
    parcels = [
        P("pin", True, 0.4006 * ACRE_M2, 0.40),
        P("next", False, 0.7514 * ACRE_M2, 0.75),
        P("far", False, 3.0 * ACRE_M2, 3.0),
    ]
    result = check(parcels, 1.15 * ACRE_SQFT, tolerance_pct=5)
    assert result.status == "adjacent_match" and not result.flag
    assert result.code == E.GEO_SITE_AREA_ADJACENT
    assert result.parcels == ("pin", "next")
    assert result.combined_sqft == pytest.approx(1.15 * ACRE_SQFT)
    assert result.difference_pct == pytest.approx(0, abs=1e-9)
    # Mixed: one neighbour without layer acres makes the set use polygons.
    mixed = check(
        [P("pin", True, 0.40 * ACRE_M2, 0.40), P("next", False, 0.77 * ACRE_M2, None)],
        1.15 * ACRE_SQFT,
        tolerance_pct=5,
    )
    assert mixed.status == "adjacent_match"
    assert mixed.combined_sqft == pytest.approx(1.17 * ACRE_SQFT)
    # Neighbours that do not reach the figure leave the flag up.
    none = check(
        [P("pin", True, 0.40 * ACRE_M2, 0.40), P("x", False, 0.2 * ACRE_M2, 0.2)],
        1.15 * ACRE_SQFT,
        tolerance_pct=5,
    )
    assert none.status == "disagree" and none.flag


def test_site_check_without_a_parcel_or_an_appraisal() -> None:
    assert check([], ACRE_SQFT, tolerance_pct=5).status == "no_parcel"
    only_adjacent = check([P("x", False, ACRE_M2, 1.0)], ACRE_SQFT, tolerance_pct=5)
    assert only_adjacent.status == "no_parcel"
    for missing in (None, 0.0):
        r = check([P("a", True, ACRE_M2, 1.0)], missing, tolerance_pct=5)
        assert r.status == "no_appraisal" and not r.flag and r.difference_pct is None
