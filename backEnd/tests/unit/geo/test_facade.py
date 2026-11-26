"""basis.geo.facade: a facade measured from one photo and the wall's known length (EVAL_REPORT
109). Synthetic facades of known geometry are photographed by a pinhole camera at known poses;
the measurement must give them back, and refuse what it cannot read."""

from __future__ import annotations

import math

import numpy as np
import pytest

from basis.errors.codes import E
from basis.geo import facade as fc

W, H = 12.0, 3.2  # the wall, metres
WINDOWS = [(1.0, 0.9, 1.0, 1.5), (4.0, 0.9, 1.0, 1.5), (8.5, 0.9, 1.2, 1.5)]  # left, sill, w, h
DOOR = (6.2, 0.0, 0.95, 2.1)


def camera_pose(yaw_deg: float, pitch_deg: float, distance: float, lateral: float = 0.0):
    """A camera looking at the wall's centre from ``distance`` m in front of it, turned
    ``yaw_deg`` around the vertical (positive: from the right) and tilted ``pitch_deg`` up."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    target = np.array([W / 2, H / 2, 0.0])
    # The wall lies in z = 0, x along it, y up; the camera stands at z > 0.
    eye = target + distance * np.array(
        [math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
    )
    eye[0] += lateral
    forward = (target - eye) / np.linalg.norm(target - eye)
    right = np.cross(forward, np.array([0.0, 1.0, 0.0]))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    return eye, np.stack([right, down, forward])  # rows: camera x (right), y (down), z (forward)


def project(points, eye, rot, f, cx, cy):
    out = []
    for x, y in points:
        p = rot @ (np.array([x, y, 0.0]) - eye)
        assert p[2] > 0, "behind the camera"
        out.append((f * p[0] / p[2] + cx, f * p[1] / p[2] + cy))
    return out


def rect(left, bottom, w, h):
    return [(left, bottom), (left + w, bottom), (left + w, bottom + h), (left, bottom + h)]


def photograph(yaw=25.0, pitch=8.0, distance=14.0, hfov=70.0, size=(1024, 768), lateral=0.0):
    width, height = size
    f = width / 2 / math.tan(math.radians(hfov) / 2)
    eye, rot = camera_pose(yaw, pitch, distance, lateral)
    cx, cy = width / 2, height / 2

    def quad(points):
        return fc.Quad(*project(points, eye, rot, f, cx, cy))

    wall = quad(rect(0.0, 0.0, W, H))
    openings = [fc.Opening("window", quad(rect(*w))) for w in WINDOWS]
    openings.append(fc.Opening("door", quad(rect(*DOOR))))
    return fc.Camera(width, height, hfov), wall, openings


def true_openings_m2():
    return sum(w * h for _, _, w, h in WINDOWS) + DOOR[2] * DOOR[3]


@pytest.mark.parametrize("yaw", [0.0, 15.0, 35.0, -40.0, 55.0])
@pytest.mark.parametrize("pitch", [0.0, 10.0])
def test_rectifies_a_wall_seen_from_any_side(yaw, pitch):
    camera, wall, openings = photograph(yaw=yaw, pitch=pitch)
    got = fc.measure(camera, wall, W, openings)
    assert got.code is None, got.refusals
    assert got.height_m == pytest.approx(H, rel=1e-6)
    assert got.gross_m2 == pytest.approx(W * H, rel=1e-6)
    assert got.openings_m2 == pytest.approx(true_openings_m2(), rel=1e-6)
    assert got.net_m2 == pytest.approx(W * H - true_openings_m2(), rel=1e-6)
    assert (got.windows, got.doors) == (3, 1)
    door = next(o for o in got.openings if o.kind == "door")
    assert (door.width_m, door.height_m, door.sill_m) == pytest.approx((0.95, 2.1, 0.0), abs=1e-6)
    trim = sum(2 * (w + h) for _, _, w, h in WINDOWS) + 2 * (DOOR[2] + DOOR[3])
    assert got.trim_m == pytest.approx(trim, rel=1e-6)
    assert got.skew_deg == pytest.approx(0.0, abs=1e-6)
    assert got.focal_from == "field of view"


def test_scale_is_the_known_length():
    camera, wall, openings = photograph()
    got = fc.measure(camera, wall, 2 * W, openings)
    assert got.height_m == pytest.approx(2 * H, rel=1e-6)
    assert got.openings_m2 == pytest.approx(4 * true_openings_m2(), rel=1e-6)


def test_focal_length_from_vanishing_points_when_no_fov():
    camera, wall, openings = photograph(yaw=30.0, pitch=12.0, hfov=60.0)
    blind = fc.Camera(camera.width_px, camera.height_px, None)
    got = fc.measure(blind, wall, W, openings)
    assert got.focal_from == "vanishing points"
    true_f = camera.width_px / 2 / math.tan(math.radians(60.0) / 2)
    assert got.focal_px == pytest.approx(true_f, rel=1e-5)
    assert got.height_m == pytest.approx(H, rel=1e-5)


def test_square_on_needs_no_focal_length():
    camera, wall, _ = photograph(yaw=0.0, pitch=0.0)
    blind = fc.Camera(camera.width_px, camera.height_px, None)
    got = fc.measure(blind, wall, W)
    assert got.focal_from == "square-on"
    assert got.height_m == pytest.approx(H, rel=1e-6)


def test_one_vanishing_point_at_infinity_needs_the_field_of_view():
    # Turned but not tilted: verticals stay parallel, horizontals converge.
    camera, wall, _ = photograph(yaw=30.0, pitch=0.0)
    blind = fc.Camera(camera.width_px, camera.height_px, None)
    got = fc.measure(blind, wall, W)
    assert got.code == E.GEO_FACADE_NO_FOCAL
    assert got.height_m is None
    assert fc.measure(camera, wall, W).height_m == pytest.approx(H, rel=1e-6)


def test_wrong_field_of_view_shows_as_skew_and_is_refused():
    camera, wall, _ = photograph(yaw=45.0, pitch=20.0, hfov=60.0)
    wrong = fc.Camera(camera.width_px, camera.height_px, 20.0)
    got = fc.measure(wrong, wall, W)
    assert got.skew_deg is not None and got.skew_deg > fc.MAX_SKEW_DEG
    assert got.code == E.GEO_FACADE_OBLIQUE


def test_oblique_wall_is_refused():
    camera, wall, _ = photograph(yaw=75.0, pitch=0.0, distance=14.0, hfov=90.0)
    got = fc.measure(camera, wall, W)
    assert got.view_deg is not None and got.view_deg > fc.MAX_VIEW_DEG
    assert got.code == E.GEO_FACADE_OBLIQUE
    assert got.height_m is None


def test_small_wall_is_refused():
    camera, wall, _ = photograph(distance=120.0)
    got = fc.measure(camera, wall, W)
    assert got.code == E.GEO_FACADE_UNREADABLE
    assert got.wall_px is not None and got.wall_px < fc.MIN_WALL_PX


def test_crossed_corners_are_refused():
    camera, wall, _ = photograph()
    crossed = fc.Quad(wall.bl, wall.tr, wall.br, wall.tl)
    got = fc.measure(camera, crossed, W)
    assert got.code == E.GEO_FACADE_UNREADABLE


def test_occluded_wall_keeps_its_height_but_not_its_openings():
    camera, wall, openings = photograph()
    got = fc.measure(camera, wall, W, openings, occluded_share=0.6)
    assert got.height_m == pytest.approx(H, rel=1e-6)
    assert got.openings == () and got.net_m2 is None and got.windows is None
    assert [(part, code) for part, code, _ in got.refusals] == [("openings", E.GEO_FACADE_OCCLUDED)]
    kept = fc.measure(camera, wall, W, openings, occluded_share=0.3)
    assert kept.windows == 3


def test_opening_outside_the_wall_is_left_out():
    camera, wall, openings = photograph()
    eye, rot = camera_pose(25.0, 8.0, 14.0)
    f = camera.width_px / 2 / math.tan(math.radians(70.0) / 2)
    outside = fc.Quad(*project(rect(W + 1.0, 1.0, 1.0, 1.0), eye, rot, f, 512, 384))
    got = fc.measure(camera, wall, W, [*openings, fc.Opening("window", outside)])
    assert got.windows == 3
    assert got.left_out == ("opening 5 (window): outside the wall",)


def test_expected_error_grows_with_distance():
    near = fc.measure(*photograph(distance=12.0)[:2], W, photograph(distance=12.0)[2])
    far = fc.measure(*photograph(distance=30.0)[:2], W, photograph(distance=30.0)[2])
    assert 0 < near.expected_error_pct["height"] < far.expected_error_pct["height"]
    assert near.expected_error_pct["openings"] < far.expected_error_pct["openings"]


def test_expected_error_matches_a_perturbation():
    """The first-order error is what moving the corners actually does (within a factor)."""
    camera, wall, openings = photograph(yaw=30.0, distance=16.0)
    got = fc.measure(camera, wall, W, openings, corner_sigma_px=2.0)
    rng = np.random.default_rng(109)
    heights = []
    for _ in range(400):
        pts = [(x + rng.normal(0, 2.0), y + rng.normal(0, 2.0)) for x, y in wall.points()]
        m = fc.measure(camera, fc.Quad(*pts), W)
        if m.height_m is not None:
            heights.append(m.height_m)
    spread = 100 * float(np.std(heights)) / H
    assert got.expected_error_pct["height"] == pytest.approx(spread, rel=0.25)


def test_bad_length_raises():
    camera, wall, _ = photograph()
    with pytest.raises(ValueError):
        fc.measure(camera, wall, 0.0)
