"""The wall's length a facade may take from the lidar shell (EVAL_REPORT 109.4 and 111): each
compass side's width (``basis.geo.facade.side_width``, the rule 109.4 scored, now the
production one the eval reuses) and the shell as ``GET .../property/public`` shows it."""

from __future__ import annotations

import math

import pytest

from basis.api.routes.property_public import _lidar_out, _shell_out
from basis.domain import traced
from basis.errors.codes import E
from basis.geo import facade as fc
from evals.measure_facades import m1_wall_length


def rectangle(w: float, h: float, turn_deg: float = 0.0) -> list[tuple[float, float]]:
    c, s = math.cos(math.radians(turn_deg)), math.sin(math.radians(turn_deg))
    return [(x * c - y * s, x * s + y * c) for x, y in ((0, 0), (w, 0), (w, h), (0, h))]


def test_a_rectangles_sides_are_its_widths_either_way_round() -> None:
    ring = rectangle(12.0, 8.0)
    for walls in (ring, list(reversed(ring))):
        found = {side: (round(width, 9), edges) for side, _, width, edges in fc.sides(walls)}
        assert found == {"north": (12.0, 1), "south": (12.0, 1), "east": (8.0, 1), "west": (8.0, 1)}


def test_cut_corners_and_a_turned_house_take_the_whole_side() -> None:
    # A 12 x 8 m house with its corners cut 0.5 m (what the simplified lidar outline does).
    cut = [(0.5, 0), (11.5, 0), (12, 0.5), (12, 7.5), (11.5, 8), (0.5, 8), (0, 7.5), (0, 0.5)]
    width, edges = fc.side_width(cut, 0.0) or (0.0, 0)
    assert width == pytest.approx(12.0) and edges == 3  # the side and both diagonals
    turned = rectangle(12.0, 8.0, 20.0)  # counter-clockwise by 20 degrees: walls at 340/70
    # Seen from due north the turned side is foreshortened; along its own normal it is whole.
    seen = fc.side_width(turned, 0.0)
    assert seen is not None and seen[0] == pytest.approx(12.0 * math.cos(math.radians(20)))
    assert fc.main_axis_deg(turned) == pytest.approx(70.0)
    found = {name: (az, width) for name, az, width, _ in fc.sides(turned)}
    assert found == {
        "north": pytest.approx((340.0, 12.0)),
        "east": pytest.approx((70.0, 8.0)),
        "south": pytest.approx((160.0, 12.0)),
        "west": pytest.approx((250.0, 8.0)),
    }
    assert {n: w for n, _, w, _ in fc.sides(list(reversed(turned)))} == pytest.approx(
        {n: w for n, (_, w) in found.items()}
    )


def test_the_eval_uses_the_production_rule() -> None:
    ring = rectangle(16.25, 9.1, 7.0)
    for az in (0.0, 90.0, 180.0, 270.0, 33.0):
        assert m1_wall_length(ring, az) == fc.side_width(ring, az)


def test_too_little_outline_has_no_sides() -> None:
    assert fc.side_width([(0, 0), (1, 0)], 0.0) is None
    assert fc.sides([]) == []


def lonlat_ring(w: float, h: float, lat: float = 38.87, lon: float = -77.43) -> list[list[float]]:
    m, n = traced.radii(lat)
    dlon = math.degrees(w / (n * math.cos(math.radians(lat))))
    dlat = math.degrees(h / m)
    return [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat]]


def test_the_shell_as_the_api_shows_it() -> None:
    record = {
        "method": "basis.geo.shell (EVAL_REPORT 105)",
        "code": None,
        "wall_outline": lonlat_ring(12.0, 8.0),
        "overhang_m": 0.4572,
        "perimeter_m": 40.0,
        "eave_main_m": 5.6,
        "pitch_deg": 26.0,
    }
    shown = _shell_out(record)
    assert shown is not None
    widths = {s["side"]: s["width_m"] for s in shown["sides"]}  # type: ignore[union-attr]
    assert widths == pytest.approx(
        {"north": 12.0, "south": 12.0, "east": 8.0, "west": 8.0}, rel=1e-6
    )
    assert (shown["eave_main_m"], shown["overhang_m"], shown["code"]) == (5.6, 0.4572, None)
    assert _shell_out(None) is None and _shell_out("x") is None
    refused = _shell_out({"code": E.GEO_SHELL_WALLS_UNREAD, "wall_outline": []})
    assert refused is not None and refused["sides"] == []


def test_a_gather_without_a_shell_reads_as_before() -> None:
    summary = {
        "project": "XX_2018",
        "year": 2018,
        "points": 1,
        "density_per_m2": 1.0,
        "building": None,
        "code": None,
        "credit": "USGS 3DEP",
        "terms_class": "public-domain",
        "terms_url": "https://www.usgs.gov/",
    }
    out = _lidar_out(summary)
    assert out is not None and out.shell is None
    with_shell = _lidar_out({**summary, "shell": {"code": None, "wall_outline": lonlat_ring(9, 7)}})
    assert with_shell is not None and with_shell.shell is not None
    assert len(with_shell.shell.sides) == 4
