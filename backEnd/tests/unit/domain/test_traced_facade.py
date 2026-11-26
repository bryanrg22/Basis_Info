"""basis.domain.traced, space ``facade`` (EVAL_REPORT 111): a wall's four corners and its
openings' on one photo, scaled by the wall's known length and measured by the single-view
metrology of basis.geo.facade (EVAL_REPORT 109). A synthetic wall photographed by a pinhole
camera must measure back to its true net face through the correction body; every malformed
body is refused in plain words; the lens comes from the 35 mm equivalent on the diagonal."""

from __future__ import annotations

import math
import uuid
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from basis.domain import traced
from basis.errors.codes import E
from basis.vision.camera import FRAME_35MM_DIAGONAL_MM, hfov_deg

FT = 1 / 0.3048
W_M, H_M = 12.0, 3.2  # the wall
WINDOWS = [(1.0, 0.9, 1.0, 1.5), (4.0, 0.9, 1.0, 1.5), (8.5, 0.9, 1.2, 1.5)]
DOOR = (6.2, 0.0, 0.95, 2.1)
SIZE = (1024, 768)
DOC = str(uuid.UUID(int=7))


def _pose(yaw_deg: float, pitch_deg: float, distance: float) -> tuple[Any, Any]:
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    target = np.array([W_M / 2, H_M / 2, 0.0])
    eye = target + distance * np.array(
        [math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
    )
    forward = (target - eye) / np.linalg.norm(target - eye)
    right = np.cross(forward, np.array([0.0, 1.0, 0.0]))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    return eye, np.stack([right, down, forward])


def focal35_for(hfov: float, size: tuple[int, int] = SIZE) -> float:
    """The 35 mm equivalent that gives ``hfov`` across a ``size`` photo (CIPA: the diagonal)."""
    w, h = size
    return w / math.hypot(w, h) * FRAME_35MM_DIAGONAL_MM / 2 / math.tan(math.radians(hfov) / 2)


def photographed(
    yaw: float = 25.0, pitch: float = 8.0, distance: float = 14.0, hfov: float = 70.0
) -> tuple[list[list[float]], list[dict[str, Any]]]:
    w, h = SIZE
    f = w / 2 / math.tan(math.radians(hfov) / 2)
    eye, rot = _pose(yaw, pitch, distance)

    def quad(left: float, bottom: float, width: float, height: float) -> list[list[float]]:
        out = []
        for x, y in [
            (left, bottom),
            (left + width, bottom),
            (left + width, bottom + height),
            (left, bottom + height),
        ]:
            p = rot @ (np.array([x, y, 0.0]) - eye)
            out.append([float(f * p[0] / p[2] + w / 2), float(f * p[1] / p[2] + h / 2)])
        return out

    openings = [{"kind": "window", "corners": quad(*win)} for win in WINDOWS]
    openings.append({"kind": "door", "corners": quad(*DOOR)})
    return quad(0.0, 0.0, W_M, H_M), openings


def body(**over: Any) -> dict[str, Any]:
    wall, openings = photographed()
    facade: dict[str, Any] = {
        "wall": wall,
        "openings": openings,
        "wall_length_ft": W_M * FT,
        "wall_length_from": "typed by the engineer",
        "lens": {"focal_length_35mm": focal35_for(70.0), "from": "photo"},
    }
    facade.update(over.pop("facade", {}))
    out: dict[str, Any] = {
        "unit": "SF",
        "space": "facade",
        "facade": facade,
        "image": {"source": "Photo front.jpg uploaded to the study", "document_id": DOC},
        "view": {"kind": "document", "id": DOC, "width": SIZE[0], "height": SIZE[1]},
    }
    out.update(over)
    return out


def problems_of(value: Any) -> list[str]:
    with pytest.raises(traced.MeasurementError) as caught:
        traced.measure(value)
    return caught.value.problems


TRUE_OPENINGS_M2 = sum(w * h for _, _, w, h in WINDOWS) + DOOR[2] * DOOR[3]
TRUE_NET_SF = (W_M * H_M - TRUE_OPENINGS_M2) * FT * FT


def test_lens_from_the_35mm_equivalent_on_the_diagonal() -> None:
    # EVAL_REPORT 109: a 28 mm equivalent across a 4:3 frame is 63.4 degrees.
    assert hfov_deg(28.0, 4032, 3024) == pytest.approx(63.44, abs=0.01)
    assert hfov_deg(focal35_for(70.0), *SIZE) == pytest.approx(70.0, abs=1e-9)
    with pytest.raises(ValueError):
        hfov_deg(0.0, 100, 100)


def test_a_photographed_wall_measures_back_to_its_net_face() -> None:
    got = traced.measure(body())
    assert got.space == "facade" and got.kind == "facade"
    assert got.method == traced.METHOD_FACADE
    assert float(got.quantity) == pytest.approx(TRUE_NET_SF, abs=0.006)
    assert got.unit == "SF" and got.area_sf == got.quantity and got.length_ft is None
    assert (got.shape_count, got.corner_count, got.cutout_count) == (5, 20, 4)
    m = got.facade
    assert m is not None
    assert (m["windows"], m["doors"]) == (3, 1)
    assert float(m["height_ft"]) == pytest.approx(H_M * FT, abs=0.006)
    assert float(m["openings_sf"]) == pytest.approx(TRUE_OPENINGS_M2 * FT * FT, abs=0.006)
    assert float(m["gross_sf"]) == pytest.approx(W_M * H_M * FT * FT, abs=0.006)
    trim = sum(2 * (w + h) for _, _, w, h in WINDOWS) + 2 * (DOOR[2] + DOOR[3])
    assert float(m["trim_lf"]) == pytest.approx(trim * FT, abs=0.006)
    assert m["focal_from"] == "field of view" and m["hfov_deg"] == pytest.approx(70.0)
    assert float(m["skew_deg"]) == pytest.approx(0.0, abs=0.01)
    door = next(o for o in m["openings"] if o["kind"] == "door")
    assert float(door["width_ft"]) == pytest.approx(0.95 * FT, abs=0.006)
    # The expected error is the metrology's own (3 px a corner) for the net face.
    assert got.expected_error_pct is not None and Decimal(0) < got.expected_error_pct
    assert m["expected_error_pct"]["net"] == format(got.expected_error_pct, "f")
    record = got.record()
    assert record["facade_measured"] == m and record["quantity"] == format(got.quantity, "f")
    assert record["facade"]["wall_length_from"] == "typed by the engineer"


def test_sy_is_the_net_face_over_nine_and_lf_is_refused() -> None:
    sf = traced.measure(body()).quantity
    sy = traced.measure(body(unit="SY")).quantity
    assert float(sy) == pytest.approx(float(sf) / 9, abs=0.006)
    assert "measurement.unit must be SF or SY for a facade (its net face)" in problems_of(
        body(unit="LF")
    )


def test_the_stored_body_measures_again_to_the_same_record() -> None:
    first = traced.measure(body(drawn_by="engineer", verified=True))
    again = traced.measure(first.stored)
    assert again.record() == first.record()
    assert first.stored["verified"] is True and first.stored["drawn_by"] == "engineer"


def test_an_edit_keeps_its_original_and_the_sentence_says_so() -> None:
    wall, openings = photographed()
    nudged = [list(p) for p in wall]
    nudged[2] = [nudged[2][0], nudged[2][1] - 4]
    original = body(drawn_by="model:gpt-6.1-sol@facade-v1", verified=False)
    edit = body(facade={"wall": nudged}, drawn_by="engineer", verified=True, original=original)
    got = traced.measure(edit)
    assert got.original is not None and got.original.space == "facade"
    sentence = traced.formula(got)
    assert sentence.startswith(f"{got.quantity:,} SF net siding measured by the engineer on ")
    assert "a wall 39.3701 ft long (typed by the engineer)" in sentence
    assert "4 openings (3 windows, 1 door)" in sentence
    assert "; verified by the engineer; replaces " in sentence and "drafted by Basis" in sentence
    assert "(the wall's length not counted)" in sentence


def test_without_a_lens_an_oblique_wall_uses_its_vanishing_points() -> None:
    plain = body()
    del plain["facade"]["lens"]
    got = traced.measure(plain)
    assert got.facade is not None and got.facade["focal_from"] == "vanishing points"
    assert got.facade["hfov_deg"] is None
    assert float(got.quantity) == pytest.approx(TRUE_NET_SF, rel=1e-4)
    assert "lens from vanishing points" in traced.formula(got)


def test_without_a_lens_parallel_verticals_are_refused_with_geo_024() -> None:
    wall, openings = photographed(pitch=0.0)  # verticals parallel: no vertical vanishing point
    plain = body(facade={"wall": wall, "openings": openings})
    del plain["facade"]["lens"]
    found = problems_of(plain)
    assert len(found) == 1 and found[0].startswith("measurement.facade.wall cannot be measured:")
    assert found[0].endswith(f"[{E.GEO_FACADE_NO_FOCAL}]")
    with_lens = body(facade={"wall": wall, "openings": openings})
    assert float(traced.measure(with_lens).quantity) == pytest.approx(TRUE_NET_SF, abs=0.006)


def test_a_wall_seen_too_obliquely_is_refused_with_geo_025() -> None:
    wall, openings = photographed(yaw=74.0, distance=12.0, hfov=110.0)
    lens = {"focal_length_35mm": focal35_for(110.0), "from": "photo"}
    found = problems_of(body(facade={"wall": wall, "openings": openings, "lens": lens}))
    assert found == [
        "measurement.facade.wall cannot be measured: the wall is seen 74 degrees off square, "
        f"over 70 [{E.GEO_FACADE_OBLIQUE}]"
    ]
    narrow, _ = photographed(yaw=75.0, distance=16.0, hfov=110.0)
    found = problems_of(body(facade={"wall": narrow, "openings": [], "lens": lens}))
    assert found[0].endswith(f"[{E.GEO_FACADE_UNREADABLE}]")


def test_an_opening_outside_the_wall_is_named() -> None:
    wall, openings = photographed()
    outside = {"kind": "window", "corners": [[1, 760], [40, 760], [40, 720], [1, 720]]}
    found = problems_of(body(facade={"openings": [*openings, outside]}))
    assert found == [
        "measurement.facade.openings has opening 5 (window): outside the wall: move its "
        "corners onto the wall"
    ]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            {"view": {"kind": "document", "id": DOC}},
            "measurement.view is required for a facade, with width and height",
        ),
        (
            {"view": {"kind": "capture", "id": "v", "width": 1024, "height": 768}},
            "measurement.view.kind must be document for a facade (a photo of the study)",
        ),
        (
            {"image": {"source": "a photo"}},
            "measurement.image.document_id is required for a facade",
        ),
        (
            {"image": {"source": "a photo", "document_id": DOC, "pixel_size_m": 0.1}},
            "measurement.image.pixel_size_m is for lon/lat imagery only; a facade",
        ),
        ({"shapes": [{"kind": "area", "ring": [[0, 0], [1, 0], [1, 1]]}]}, "measurement.shapes"),
        ({"scale": {"points": [[0, 0], [9, 0]], "length": 3, "unit": "ft"}}, "measurement.scale"),
        ({"height_ft": 9}, "measurement.height_ft does not go with space facade"),
        ({"facade": {"wall": [[0, 0], [1, 0], [1, 1]]}}, "measurement.facade.wall must be the 4"),
        (
            {"facade": {"wall": [[600, 300], [100, 300], [100, 100], [600, 100]]}},
            "measurement.facade.wall must run bottom-left, bottom-right, top-right, top-left",
        ),
        (
            {"facade": {"wall": [[100, 300], [600, 300], [600, 500], [100, 500]]}},
            "measurement.facade.wall must run bottom-left",
        ),
        (
            {"facade": {"wall": [[100, 800], [600, 800], [600, 100], [100, 100]]}},
            "measurement.facade.wall[0] is at pixel [100, 800], outside the 1024 x 768 photo",
        ),
        ({"facade": {"wall_length_ft": 0}}, "measurement.facade.wall_length_ft must be above 0"),
        ({"facade": {"wall_length_ft": 1001}}, "measurement.facade.wall_length_ft must be above"),
        ({"facade": {"wall_length_ft": True}}, "measurement.facade.wall_length_ft must be a"),
        ({"facade": {"wall_length_from": " "}}, "measurement.facade.wall_length_from must be text"),
        ({"facade": {"lens": {"focal_length_35mm": 28}}}, "measurement.facade.lens.from must be"),
        (
            {"facade": {"lens": {"focal_length_35mm": -1, "from": "photo"}}},
            "measurement.facade.lens.focal_length_35mm must be above 0",
        ),
        (
            {"facade": {"lens": {"focal_length_35mm": 28, "from": "photo", "gps": 1}}},
            "measurement.facade.lens has unknown keys: gps",
        ),
        ({"facade": {"extra": 1}}, "measurement.facade has unknown keys: extra"),
        (
            {"facade": {"openings": [{"kind": "skylight", "corners": []}]}},
            "measurement.facade.openings[0].kind must be window, door, garage or other",
        ),
        ({"facade": {"openings": "none"}}, "measurement.facade.openings must be a list"),
        ({"facade": {"openings": [{"kind": "door"}]}}, "measurement.facade.openings[0].corners"),
    ],
)
def test_malformed_facades_are_refused_in_plain_words(
    change: dict[str, Any], expected: str
) -> None:
    value = body(**{k: v for k, v in change.items() if k != "facade"})
    value["facade"].update(change.get("facade", {}))
    found = problems_of(value)
    assert any(p.startswith(expected) for p in found), found


def test_a_facade_is_its_own_space() -> None:
    assert "measurement.space must be lonlat, image or facade, got 'mercator'" in problems_of(
        body(space="mercator")
    )
    lonlat = {
        "unit": "SF",
        "space": "lonlat",
        "shapes": [{"kind": "area", "ring": [[-88.9, 43.8], [-88.89, 43.8], [-88.89, 43.81]]}],
        "image": {"source": "an aerial"},
        "facade": {"wall": []},
    }
    assert "measurement.facade goes with space facade (a wall on a photo)" in problems_of(lonlat)
    no_facade = body()
    del no_facade["facade"]
    assert problems_of(no_facade)[0].startswith("measurement.facade must be an object")


def test_the_review_flags_follow_the_record() -> None:
    got = traced.measure(body())
    flags = traced.review_flags(
        got, placed_in_service=None, after_service_days=365, error_flag_pct=Decimal(10)
    )
    assert flags == [traced.UNDATED]  # a photo with no captured_on; its error is known
