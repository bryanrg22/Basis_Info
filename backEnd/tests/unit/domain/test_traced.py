"""``basis.domain.traced``: the engineer's traced measurement (EVAL_REPORT 97).

Each space and unit computed against a hand value or the closed-form ellipsoid area of the
EVAL_REPORT 97 harness, cutouts, the dropped closing point, the wall face, the expected
error formula, the formula string, the review flags with their boundaries, every refusal of
the rules one by one with its message, and the numerical edges (the smallest shape, 500
corners, either winding, a sliver, the antimeridian, the point cap's timing)."""

from __future__ import annotations

import copy
import math
import time
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from basis.activities.corrections import MAX_QUANTITY as TYPED_MAX
from basis.domain import traced
from basis.domain.traced import MeasurementError, measure, problems, review_flags
from evals.trace_geometry import box, box_area_closed_sf, meridian_arc_integrated_ft

D = Decimal
SCALE = {"points": [[0, 0], [100, 0]], "length": 20, "unit": "ft"}  # 0.2 ft per pixel


def image_area(ring: list[list[float]], **extra: Any) -> dict[str, Any]:
    return {
        "unit": "SF",
        "space": "image",
        "shapes": [{"kind": "area", "ring": ring}],
        "scale": dict(SCALE),
        "image": {"source": "site plan sheet C1", "captured_on": "2020-04-18"},
        **extra,
    }


RECT = [[0, 0], [100, 0], [100, 200], [0, 200]]  # 20 x 40 ft at 0.2 ft per pixel


def lonlat(shapes: list[dict[str, Any]], unit: str = "SF", **image: Any) -> dict[str, Any]:
    return {
        "unit": unit,
        "space": "lonlat",
        "shapes": shapes,
        "image": {"source": "State orthoimagery 2020, 6 inch", **image},
    }


def refused(value: Any) -> list[str]:
    with pytest.raises(MeasurementError) as raised:
        measure(value)
    assert raised.value.problems == problems(value)
    return raised.value.problems


# --------------------------------------------------------------------------- image space


def test_an_image_rectangle_is_its_pixels_times_the_scale() -> None:
    t = measure(image_area(RECT))
    assert (t.quantity, t.unit, t.area_sf, t.length_ft) == (D("800.00"), "SF", D("800.00"), None)
    assert (t.method, t.shape_count, t.corner_count, t.cutout_count) == ("image_scale", 1, 4, 0)
    assert t.scale_ft_per_px == pytest.approx(0.2)
    # One pixel on every edge (120 ft x 0.2 ft / 800 SF = 3 %) plus the scale twice (2 x 2 px
    # / 100 px = 4 %).
    assert t.expected_error_pct == D("7.00")
    assert traced.formula(t) == (
        "800.00 SF traced by the engineer on site plan sheet C1 (captured 2020-04-18), "
        "1 area of 4 corners, scale 0.2 ft per pixel; expected error up to 7.00 %"
    )


def test_the_record_is_the_stored_body_plus_what_was_computed() -> None:
    t = measure(image_area([*RECT, [0, 0]]))  # the closing point repeated
    record = t.record()
    assert record["shapes"] == [
        {"kind": "area", "ring": [[0.0, 0.0], [100.0, 0.0], [100.0, 200.0], [0.0, 200.0]]}
    ]
    assert record["scale"] == {"points": [[0.0, 0.0], [100.0, 0.0]], "length": 20.0, "unit": "ft"}
    assert record["image"] == {"source": "site plan sheet C1", "captured_on": "2020-04-18"}
    assert {k: record[k] for k in ("quantity", "area_sf", "method", "expected_error_pct")} == {
        "quantity": "800.00",
        "area_sf": "800.00",
        "method": "image_scale",
        "expected_error_pct": "7.00",
    }
    assert (record["shape_count"], record["corner_count"], record["cutout_count"]) == (1, 4, 0)
    assert "length_ft" not in record and record["scale_ft_per_px"] == 0.2


def test_square_yards_are_square_feet_over_nine() -> None:
    t = measure({**image_area(RECT), "unit": "SY"})
    assert (t.quantity, t.unit, t.area_sf) == (D("88.89"), "SY", D("800.00"))
    assert traced.formula(t).startswith("88.89 SY traced by the engineer")


def test_a_scale_in_metres_is_converted_to_feet() -> None:
    t = measure(
        image_area(RECT, scale={"points": [[0, 0], [100, 0]], "length": 6.096, "unit": "m"})
    )
    assert t.quantity == D("800.00")  # 6.096 m = 20 ft exactly


def test_a_line_in_pixels_is_its_length_times_the_scale() -> None:
    value = {
        **image_area(RECT),
        "unit": "LF",
        "shapes": [{"kind": "line", "path": [[0, 0], [300, 0], [300, 400]]}],
    }
    t = measure(value)
    assert (t.quantity, t.unit, t.length_ft, t.area_sf) == (D("140.00"), "LF", D("140.00"), None)
    # 2 x 0.2 ft / 140 ft = 0.29 %, plus the scale once (2 / 100 = 2 %).
    assert t.expected_error_pct == D("2.29")
    assert "1 line of 3 points" in traced.formula(t)


def test_cutouts_are_taken_out_of_their_ring() -> None:
    big = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]  # 200 x 200 ft
    holes = [[[100, 100], [200, 100], [200, 200], [100, 200]], [[500, 500], [600, 500], [600, 600]]]
    t = measure(image_area(big, shapes=[{"kind": "area", "ring": big, "cutouts": holes}]))
    # 40,000 - 20 x 20 - 20 x 20 / 2 = 39,400 SF.
    assert t.quantity == D("39400.00") and t.cutout_count == 2 and t.corner_count == 11
    assert "1 area of 11 corners (2 cut out)" in traced.formula(t)


def test_a_cutout_touching_its_ring_from_inside_is_accepted() -> None:
    big = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
    notch = [[0, 0], [100, 0], [100, 100], [0, 100]]  # shares a corner and two edges
    t = measure(image_area(big, shapes=[{"kind": "area", "ring": big, "cutouts": [notch]}]))
    assert t.quantity == D("39600.00")


def test_clockwise_and_counter_clockwise_rings_measure_the_same() -> None:
    assert measure(image_area(RECT)).quantity == measure(image_area(RECT[::-1])).quantity


def test_several_areas_add_up() -> None:
    other = [[200, 200], [300, 200], [300, 300], [200, 300]]  # 20 x 20 ft
    t = measure(
        image_area(RECT, shapes=[{"kind": "area", "ring": RECT}, {"kind": "area", "ring": other}])
    )
    assert t.quantity == D("1200.00") and t.shape_count == 2
    assert "2 areas of 8 corners" in traced.formula(t)


@pytest.mark.parametrize(("unit", "quantity"), [("SF", D("200.00")), ("SY", D("22.22"))])
def test_a_wall_face_is_its_length_times_its_height(unit: str, quantity: Decimal) -> None:
    value = {
        **image_area(RECT),
        "unit": unit,
        "shapes": [{"kind": "line", "path": [[0, 0], [250, 0]]}],  # 50 ft
        "height_ft": 4,
    }
    t = measure(value)
    assert (t.quantity, t.length_ft, t.height_ft) == (quantity, D("50.00"), 4.0)
    assert "1 line of 2 points x 4 ft high" in traced.formula(t)


# --------------------------------------------------------------------------- lon/lat space


@pytest.mark.parametrize("lat", [25, 37, 49])
def test_a_lonlat_box_matches_the_ellipsoids_closed_form(lat: int) -> None:
    ring, dlat, dlon = box(lat, 20.0, 50.0)
    t = measure(lonlat([{"kind": "area", "ring": [list(p) for p in ring]}], pixel_size_m=0.1524))
    key = box_area_closed_sf(lat, lat + dlat, dlon)
    assert float(t.area_sf or 0) == pytest.approx(key, abs=0.006)  # the quantum, not the method
    assert t.method == "local_tangent_plane_wgs84"
    # Perimeter 140 ft x 0.5 ft (0.1524 m) / 1,000 SF = 7 %.
    assert t.expected_error_pct == D("7.00")


def test_a_lonlat_meridian_line_matches_the_integrated_arc() -> None:
    lat = 43.8
    m, _ = traced.radii(lat)
    dlat = math.degrees(150 / traced.FT_PER_M / m)
    value = lonlat([{"kind": "line", "path": [[-88.9, lat], [-88.9, lat + dlat]]}], unit="LF")
    t = measure(value)
    assert float(t.quantity) == pytest.approx(
        meridian_arc_integrated_ft(lat, lat + dlat), abs=0.006
    )
    assert t.expected_error_pct is None  # no pixel size


def test_a_shape_across_the_antimeridian_is_the_small_shape_it_is() -> None:
    ring, _, _ = box(10, 100.0, 100.0)
    dlon = ring[1][0] - ring[0][0]
    across = [
        [180 - dlon / 2, 10],
        [-180 + dlon / 2, 10],
        [-180 + dlon / 2, ring[2][1]],
        [180 - dlon / 2, ring[2][1]],
    ]
    t = measure(lonlat([{"kind": "area", "ring": across}]))
    assert abs(t.quantity - D("10000")) < D("0.1")


def test_a_one_square_foot_shape_is_measured_and_a_smaller_one_rounds_to_zero() -> None:
    ring, _, _ = box(40, 1.0, 1.0)
    assert measure(lonlat([{"kind": "area", "ring": [list(p) for p in ring]}])).quantity == D(
        "1.00"
    )
    tiny, _, _ = box(40, 0.05, 0.05)  # 0.0025 SF
    [message] = refused(lonlat([{"kind": "area", "ring": [list(p) for p in tiny]}]))
    assert "must be above 0" in message


def test_five_hundred_corners_are_accepted_and_the_closing_501st_too() -> None:
    circle = [
        [500 + 400 * math.cos(2 * math.pi * k / 500), 500 + 400 * math.sin(2 * math.pi * k / 500)]
        for k in range(500)
    ]
    t = measure(image_area(circle))
    assert t.corner_count == 500
    assert float(t.quantity) == pytest.approx(math.pi * 80**2, rel=1e-4)
    assert measure(image_area([*circle, circle[0]])).quantity == t.quantity


def test_a_nearly_degenerate_but_simple_sliver_is_accepted() -> None:
    t = measure(image_area([[0, 0], [10_000, 0], [10_000, 1]]))
    assert t.quantity == D("200.00")  # 5,000 px^2 x 0.04


def test_the_largest_measurement_is_checked_quickly() -> None:
    circle = [
        [
            50_000 + 400 * math.cos(2 * math.pi * k / 500),
            50_000 + 400 * math.sin(2 * math.pi * k / 500),
        ]
        for k in range(500)
    ]
    value = image_area(circle, shapes=[{"kind": "area", "ring": circle}] * 10)
    start = time.perf_counter()
    measure(value)
    assert time.perf_counter() - start < 1.0  # about 20 ms on a laptop (EVAL_REPORT 97)


# --------------------------------------------------------------------------- expected error


def test_the_expected_error_formula() -> None:
    area = traced.expected_error_pct(
        area_sf=800, length_ft=None, edges_ft=120, lines=1, pixel_ft=0.5, scale_px=None
    )
    assert area == D("7.50")  # 120 x 0.5 / 800
    with_scale = traced.expected_error_pct(
        area_sf=800, length_ft=None, edges_ft=120, lines=1, pixel_ft=0.5, scale_px=50
    )
    assert with_scale == D("15.50")  # + 2 x 2 / 50
    line = traced.expected_error_pct(
        area_sf=None, length_ft=150, edges_ft=150, lines=2, pixel_ft=0.5, scale_px=None
    )
    assert line == D("1.33")  # 2 x 0.5 x 2 lines / 150
    line_scaled = traced.expected_error_pct(
        area_sf=None, length_ft=150, edges_ft=150, lines=1, pixel_ft=0.5, scale_px=100
    )
    assert line_scaled == D("2.67")  # 2 x 0.5 / 150 + 2 / 100
    assert (
        traced.expected_error_pct(
            area_sf=800, length_ft=None, edges_ft=120, lines=1, pixel_ft=None, scale_px=None
        )
        is None
    )


def test_a_cutouts_perimeter_counts_in_the_expected_error() -> None:
    big = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
    hole = [[100, 100], [200, 100], [200, 200], [100, 200]]
    t = measure(image_area(big, shapes=[{"kind": "area", "ring": big, "cutouts": [hole]}]))
    # (800 + 80 ft) x 0.2 / 39,600 SF = 0.44 %, + 4 % for the scale.
    assert t.expected_error_pct == D("4.44")


def test_a_wall_faces_height_is_not_counted_in_its_error() -> None:
    value = {
        **image_area(RECT),
        "shapes": [{"kind": "line", "path": [[0, 0], [250, 0]]}],
        "height_ft": 4,
    }
    assert measure(value).expected_error_pct == D("2.80")  # 2 x 0.2 / 50 + 2 / 100


# --------------------------------------------------------------------------- flags


PIS = date(2020, 6, 1)


def flags(value: dict[str, Any], **kw: Any) -> list[str]:
    options: dict[str, Any] = {
        "placed_in_service": PIS,
        "after_service_days": 365,
        "error_flag_pct": D("10"),
    }
    return review_flags(measure(value), **{**options, **kw})


def test_a_dated_sharp_image_before_service_raises_no_flag() -> None:
    assert flags(image_area(RECT)) == []


def test_an_undated_image_is_flagged() -> None:
    value = image_area(RECT)
    del value["image"]["captured_on"]
    assert flags(value) == [traced.UNDATED]


@pytest.mark.parametrize(
    ("captured", "flagged"), [("2021-06-01", False), ("2021-06-02", True), ("2019-01-01", False)]
)
def test_an_image_long_after_service_is_flagged(captured: str, flagged: bool) -> None:
    value = image_area(RECT)
    value["image"]["captured_on"] = captured
    assert flags(value) == ([traced.AFTER_SERVICE] if flagged else [])  # 365 days is the limit


def test_without_a_placed_in_service_date_the_after_service_check_is_skipped() -> None:
    value = image_area(RECT)
    value["image"]["captured_on"] = "2030-01-01"
    assert flags(value, placed_in_service=None) == []


def test_lonlat_without_a_pixel_size_is_flagged_error_unknown() -> None:
    ring, _, _ = box(43, 20.0, 50.0)
    value = lonlat([{"kind": "area", "ring": [list(p) for p in ring]}], captured_on="2020-04-18")
    assert flags(value) == [traced.ERROR_UNKNOWN]


@pytest.mark.parametrize(("threshold", "flagged"), [("7", False), ("6.99", True)])
def test_a_coarse_measurement_is_flagged_above_the_threshold(threshold: str, flagged: bool) -> None:
    assert flags(image_area(RECT), error_flag_pct=D(threshold)) == (
        [traced.COARSE] if flagged else []
    )


def test_flags_never_change_the_quantity() -> None:
    sharp = measure(image_area(RECT))
    coarse_undated = image_area(RECT)
    del coarse_undated["image"]["captured_on"]
    t = measure(coarse_undated)
    assert review_flags(
        t, placed_in_service=PIS, after_service_days=0, error_flag_pct=D("0.01")
    ) == [traced.UNDATED, traced.COARSE]
    assert t.quantity == sharp.quantity


# --------------------------------------------------------------------------- refusals


def with_(path: list[Any], value: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    body = copy.deepcopy(base or image_area(RECT))
    node: Any = body
    for key in path[:-1]:
        node = node[key]
    if value is DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return body


DELETE = object()
LINE = {"kind": "line", "path": [[0, 0], [100, 0]]}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (with_(["colour"], "red"), "measurement has unknown keys: colour"),
        (with_(["image", "provider"], "x"), "measurement.image has unknown keys: provider"),
        (with_(["scale", "dpi"], 300), "measurement.scale has unknown keys: dpi"),
        (
            with_(["shapes", 0, "path"], [[0, 0], [1, 1]]),
            "measurement.shapes[0] has unknown keys: path",
        ),
        (
            with_(["shapes"], [{**LINE, "cutouts": []}], {**image_area(RECT), "unit": "LF"}),
            "measurement.shapes[0] has unknown keys: cutouts",
        ),
        (
            with_(["unit"], "EA"),
            "measurement.unit must be SF, SY or LF (EA is counted, not traced)",
        ),
        (with_(["unit"], DELETE), "measurement.unit must be SF, SY or LF"),
        (with_(["space"], "mercator"), "measurement.space must be lonlat or image"),
        (with_(["shapes"], []), "measurement.shapes must be a list of 1 to 50 shapes, got 0"),
        (
            with_(["shapes"], [{"kind": "area", "ring": RECT}] * 51),
            "measurement.shapes must be a list of 1 to 50 shapes, got 51",
        ),
        (
            with_(["shapes"], {"kind": "area"}),
            "measurement.shapes must be a list of 1 to 50 shapes",
        ),
        (
            with_(["shapes", 0, "kind"], "circle"),
            "measurement.shapes[0] must be an object with kind area or line",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [10, 0]]),
            "measurement.shapes[0].ring has 2 points; a ring has 3 to 500",
        ),
        (
            with_(["shapes", 0, "ring"], [[k, k * k % 97] for k in range(502)]),
            "measurement.shapes[0].ring has 502 points; a ring has 3 to 500",
        ),
        (
            with_(["shapes", 0, "ring"], "a ring"),
            "measurement.shapes[0].ring must be a list of points",
        ),
        (
            with_(["shapes", 0, "cutouts"], [[[10, 10], [20, 10], [20, 20]]] * 21),
            "measurement.shapes[0].cutouts must be a list of at most 20 rings",
        ),
        (
            with_(["shapes", 0, "cutouts"], [[[10, 10], [20, 10]]]),
            "measurement.shapes[0].cutouts[0] has 2 points; a ring has 3 to 500",
        ),
        (
            with_(["shapes", 0, "ring", 1], [100, True]),
            "measurement.shapes[0].ring[1][1] must be a number, got True",
        ),
        (
            with_(["shapes", 0, "ring", 1], ["100", 0]),
            "measurement.shapes[0].ring[1][0] must be a number, got '100'",
        ),
        (
            with_(["shapes", 0, "ring", 1], [math.nan, 0]),
            "measurement.shapes[0].ring[1][0] must be a finite number",
        ),
        (
            with_(["shapes", 0, "ring", 1], [math.inf, 0]),
            "measurement.shapes[0].ring[1][0] must be a finite number",
        ),
        (
            with_(["shapes", 0, "ring", 1], [100, 0, 0]),
            "measurement.shapes[0].ring[1] must be a pair of numbers [x, y]",
        ),
        (
            with_(["shapes", 0, "ring", 1], [-1, 0]),
            "measurement.shapes[0].ring[1] is at pixel [-1, 0]; pixels are from 0 to 100000",
        ),
        (with_(["shapes", 0, "ring", 1], [100_001, 0]), "pixels are from 0 to 100000"),
        (with_(["scale"], DELETE), "measurement.scale is required for an image"),
        (
            with_(["scale", "points"], [[0, 0], [1, 1]]),
            "measurement.scale.points are 1.41 pixels apart; the known length must span at "
            "least 2 pixels",
        ),
        (
            with_(["scale", "points"], [[0, 0]]),
            "measurement.scale.points must be the two pixels the known length runs between",
        ),
        (with_(["scale", "unit"], "yd"), "measurement.scale.unit must be ft or m, got 'yd'"),
        (
            with_(["scale", "length"], 0),
            "measurement.scale.length must be above 0 and at most 100000, got 0",
        ),
        (
            with_(["scale", "length"], 100_001),
            "measurement.scale.length must be above 0 and at most 100000",
        ),
        (
            with_(["image", "pixel_size_m"], 0.15),
            "measurement.image.pixel_size_m is for lon/lat imagery only",
        ),
        (with_(["image"], DELETE), "measurement.image must be an object with at least source"),
        (
            with_(["image", "source"], "  "),
            "measurement.image.source must say what the image is and where it came from (1 to "
            "300 characters)",
        ),
        (
            with_(["image", "source"], "x" * 301),
            "measurement.image.source must say what the image is",
        ),
        (
            with_(["image", "source"], "plan\x00"),
            "measurement.image.source must say what the image is",
        ),
        (with_(["image", "source"], 7), "measurement.image.source must say what the image is"),
        (
            with_(["image", "document_id"], "not-a-uuid"),
            "measurement.image.document_id must be a document id (a UUID)",
        ),
        (
            with_(["image", "document_id"], 12),
            "measurement.image.document_id must be a document id (a UUID)",
        ),
        (
            with_(["image", "captured_on"], "2020/04/18"),
            "measurement.image.captured_on must be a date YYYY-MM-DD",
        ),
        (
            with_(["image", "captured_on"], "20200418"),
            "measurement.image.captured_on must be a date YYYY-MM-DD",
        ),
        (
            with_(["image", "captured_on"], "2020-02-30"),
            "measurement.image.captured_on must be a date YYYY-MM-DD",
        ),
        (with_(["unit"], "LF"), "measurement.shapes are areas; LF is measured with line shapes"),
        (with_(["shapes"], [LINE]), "measurement.height_ft is required to measure SF with lines"),
        (
            with_(["height_ft"], 4),
            "measurement.height_ft goes with line shapes (a wall face), not with areas",
        ),
        (
            with_(["height_ft"], 0, {**image_area(RECT), "shapes": [LINE]}),
            "measurement.height_ft must be above 0 and at most 100000",
        ),
        (
            with_(["height_ft"], 4, {**image_area(RECT), "unit": "LF", "shapes": [LINE]}),
            "measurement.height_ft goes with SF or SY (a wall face); LF is a length alone",
        ),
        (
            with_(["shapes"], [{"kind": "area", "ring": RECT}, LINE]),
            "measurement.shapes mix area and line shapes",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [100, 100], [100, 0], [0, 100]]),
            "measurement.shapes[0].ring crosses or touches itself",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [100, 0], [100, 0], [0, 100]]),
            "measurement.shapes[0].ring has a repeated corner",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [100, 0], [50, 0], [50, 100]]),
            "measurement.shapes[0].ring folds back on itself",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [100, 0], [100, 100], [50, 0], [0, 100]]),
            "measurement.shapes[0].ring crosses or touches itself",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [50, 0], [100, 0]]),
            "measurement.shapes[0].ring folds back on itself",
        ),
        (
            with_(["shapes", 0, "ring"], [[0, 0], [100_000, 0], [100_000, 0.000001]]),
            "measurement.shapes[0].ring has no area (its corners lie on one line)",
        ),
        (
            with_(["shapes", 0, "cutouts"], [[[50, 50], [150, 50], [150, 150]]]),
            "measurement.shapes[0].cutouts[0] is not inside its ring (corner 1 is outside)",
        ),
        (
            with_(["shapes", 0, "cutouts"], [[[0, 0], [100, 0], [100, 200], [0, 200]]]),
            "measurement.shapes[0] has no area left once its cutouts are taken out",
        ),
        (
            with_(["shapes", 0, "cutouts"], [[[10, 10], [50, 50], [50, 10], [10, 50]]]),
            "measurement.shapes[0].cutouts[0] crosses or touches itself",
        ),
        (with_(["scale", "length"], 99_999), "the quantity must be above 0 and at most 10,000,000"),
    ],
)
def test_each_rule_refuses_with_its_message(body: dict[str, Any], message: str) -> None:
    assert any(message in p for p in refused(body)), refused(body)


def test_the_path_limits() -> None:
    base = {**image_area(RECT), "unit": "LF"}
    assert (
        "has 1 points; a path has 2 to 500"
        in refused(with_(["shapes"], [{"kind": "line", "path": [[0, 0]]}], base))[0]
    )
    long = [[k, k % 2] for k in range(501)]
    assert (
        "has 501 points; a path has 2 to 500"
        in refused(with_(["shapes"], [{"kind": "line", "path": long}], base))[0]
    )


def test_lonlat_ranges_and_no_scale() -> None:
    ring, _, _ = box(43, 20.0, 50.0)
    good = lonlat([{"kind": "area", "ring": [list(p) for p in ring]}])
    assert problems(good) == []
    assert (
        "has longitude 181; a longitude is from -180 to 180"
        in refused(with_(["shapes", 0, "ring", 0], [181, 43], good))[0]
    )
    assert (
        "has latitude 86; a latitude is from -85 to 85"
        in refused(with_(["shapes", 0, "ring", 0], [-88.9, 86], good))[0]
    )
    assert (
        "measurement.scale is for an image in pixels" in refused(with_(["scale"], SCALE, good))[0]
    )
    assert (
        "pixel_size_m must be above 0 and at most 100, got 0"
        in refused(with_(["image", "pixel_size_m"], 0, good))[0]
    )
    assert (
        "pixel_size_m must be above 0 and at most 100"
        in refused(with_(["image", "pixel_size_m"], 101, good))[0]
    )


def test_the_total_point_cap() -> None:
    circle = [
        [500 + 400 * math.cos(2 * math.pi * k / 500), 500 + 400 * math.sin(2 * math.pi * k / 500)]
        for k in range(500)
    ]
    body = image_area(circle, shapes=[{"kind": "area", "ring": circle}] * 11)
    assert refused(body) == [
        "measurement.shapes have 5500 points in all; a measurement has at most 5000"
    ]


def test_not_an_object_and_every_problem_at_once() -> None:
    assert refused([1, 2]) == ["measurement must be an object (unit, space, shapes, image)"]
    found = refused({"unit": "EA", "space": "x", "shapes": [], "image": {}, "foo": 1})
    assert len(found) == 5 and found[0] == "measurement has unknown keys: foo"


def test_a_valid_measurement_has_no_problems_and_the_caps_agree() -> None:
    assert problems(image_area(RECT)) == []
    assert traced.MAX_QUANTITY == TYPED_MAX
    t = measure(
        image_area(
            RECT, image={"source": "plan", "document_id": "6F9619FF-8B86-D011-B42D-00C04FC964FF"}
        )
    )
    assert t.stored["image"]["document_id"] == "6f9619ff-8b86-d011-b42d-00c04fc964ff"  # normalized


def test_a_body_far_over_the_point_cap_is_refused_before_any_point_is_parsed() -> None:
    ring = [[0, 0]] * 500
    shape = {"kind": "area", "ring": ring, "cutouts": [ring] * 20}
    body = image_area(RECT, shapes=[shape] * 50)  # 525,000 points: the contract's limits allow it
    start = time.perf_counter()
    assert refused(body) == [
        "measurement.shapes have 525000 points in all; a measurement has at most 5000"
    ]
    assert time.perf_counter() - start < 0.5  # no point parsed: microseconds


def test_a_number_beyond_a_double_and_a_list_of_bad_points_are_named_once() -> None:
    huge = with_(["shapes", 0, "ring", 1], [10**400, 0])
    [message] = refused(huge)
    assert message.startswith("measurement.shapes[0].ring[1][0] must be a finite number, got 1000")
    assert message.endswith("...") and len(message) < 130  # the value shortened to 60
    strings = with_(["shapes", 0, "ring"], [["x", "y"]] * 400)
    assert len(refused(strings)) == 2  # the first bad point's two numbers, not 800 messages
