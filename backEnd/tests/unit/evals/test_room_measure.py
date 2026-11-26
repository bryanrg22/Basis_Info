"""Room measurements from point maps (evals.room_measure, EVAL_REPORT 107).

The scenes are rendered here by ray casting a pinhole camera inside a box room of known size,
with a door on a wall and a counter along another, so every expected number is exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pytest

from evals import room_measure as rm

FT = rm.M_TO_FT
SF = rm.SQM_TO_SF


@dataclass(frozen=True)
class Scene:
    length: float = 5.0  # along x
    width: float = 3.6  # along y
    height: float = 2.5
    door: tuple[float, float] | None = (1.0, 0.9)  # on the far wall y = width: x from, width
    door_height: float = rm.DOOR_HEIGHT_M
    counter: tuple[float, float, float] | None = None  # along the wall x = length: y0, y1, depth
    counter_height: float = rm.COUNTER_HEIGHT_M


def render(
    scene: Scene,
    camera: tuple[float, float, float],
    yaw_deg: float,
    pitch_deg: float = -10.0,
    *,
    fov_deg: float = 70.0,
    size: tuple[int, int] = (240, 320),
    scale: float = 1.0,
    world: bool = False,
) -> rm.View:
    """A pinhole camera's point map (OpenCV camera frame, or the room's frame with ``world``)
    of the scene, every distance multiplied by ``scale`` (a depth model's wrong metres)."""
    h, w = size
    f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    rays_cam = np.stack([(u - w / 2) / f, (v - h / 2) / f, np.ones_like(u)], axis=-1)
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    forward = np.array(
        [math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)]
    )
    right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    down = np.cross(forward, right)
    r_cw = np.stack([right, down, forward], axis=1)  # camera to world, columns
    rays = rays_cam @ r_cw.T
    c = np.asarray(camera, dtype=np.float64)
    best = np.full((h, w), np.inf)
    label = np.full((h, w), 255, dtype=np.int64)
    planes = [  # (axis, value, ADE class)
        (2, 0.0, 3),
        (2, scene.height, 5),
        (0, 0.0, 0),
        (0, scene.length, 0),
        (1, 0.0, 0),
        (1, scene.width, 0),
    ]
    for axis, value, cls in planes:
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (value - c[axis]) / rays[..., axis]
        hit = c + rays * t[..., None]
        inside = np.all(
            (hit >= -1e-6) & (hit <= np.array([scene.length, scene.width, scene.height]) + 1e-6),
            axis=-1,
        )
        ok = (t > 0) & inside & (t < best)
        best = np.where(ok, t, best)
        cls_here = np.full((h, w), cls)
        if scene.door is not None and axis == 1 and value == scene.width:
            x0, dw = scene.door
            on_door = (
                (hit[..., 0] >= x0) & (hit[..., 0] <= x0 + dw) & (hit[..., 2] <= scene.door_height)
            )
            cls_here = np.where(on_door, 14, cls_here)
        label = np.where(ok, cls_here, label)
    if scene.counter is not None:
        y0, y1, depth = scene.counter
        # The counter's top: z = counter_height over x in [length - depth, length], y in [y0, y1].
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (scene.counter_height - c[2]) / rays[..., 2]
        hit = c + rays * t[..., None]
        on = (
            (t > 0)
            & (t < best)
            & (hit[..., 0] >= scene.length - depth)
            & (hit[..., 1] >= y0)
            & (hit[..., 1] <= y1)
        )
        best = np.where(on, t, best)
        label = np.where(on, 70, label)
    points_world = c + rays * best[..., None]
    valid = np.isfinite(best)
    if world:
        pts = points_world * scale
        cam = tuple(float(x) for x in c * scale)
    else:
        pts = ((points_world - c) @ r_cw) * scale
        cam = (0.0, 0.0, 0.0)
    pts = np.where(valid[..., None], pts, 0.0)
    return rm.View(points=pts, valid=valid, labels=label, camera=cam)


def corner_views(scene: Scene, scale: float = 1.0, world: bool = False) -> list[rm.View]:
    """Four photos from the room's corners, each looking at the opposite corner."""
    m = 0.3
    spots = [
        ((m, m, 1.5), 45.0),
        ((scene.length - m, m, 1.5), 135.0),
        ((scene.length - m, scene.width - m, 1.5), 225.0),
        ((m, scene.width - m, 1.5), 315.0),
    ]
    return [render(scene, c, yaw, scale=scale, world=world) for c, yaw in spots]


def test_a_door_scales_a_wrong_model_back_to_metres() -> None:
    scene = Scene()
    got = rm.measure_room(corner_views(scene, scale=0.7))
    assert got.refused is None
    assert got.scale == pytest.approx(1 / 0.7, rel=0.02)
    assert {r.kind for r in got.references} == {"door"}
    assert got.ceiling_height_ft == pytest.approx(2.5 * FT, rel=0.02)
    # From a corner the wall behind the camera is not seen: the room ends at the camera
    # (0.3 m in from each wall), the longer side reads 5.0 - 0.3 at worst.
    assert got.length_ft == pytest.approx(5.0 * FT, rel=0.07)
    assert got.width_ft == pytest.approx(3.6 * FT, rel=0.09)


def test_the_models_own_scale_is_left_as_it_is() -> None:
    scene = Scene()
    got = rm.measure_room(corner_views(scene, scale=0.7), source="model")
    assert got.scale == 1.0
    assert got.ceiling_height_ft == pytest.approx(2.5 * 0.7 * FT, rel=0.02)


def test_the_camera_height_prior_scales_from_the_floor_distance() -> None:
    scene = Scene()
    got = rm.measure_room(corner_views(scene, scale=0.7), source="camera_height")
    # The cameras stand 1.5 m high, the prior's own value: the scale is exact.
    assert got.scale == pytest.approx(1 / 0.7, rel=0.01)
    assert {r.kind for r in got.references} == {"camera_height"}


def test_registered_views_close_the_room_from_both_sides() -> None:
    scene = Scene()
    got = rm.measure_room(corner_views(scene, scale=0.5, world=True), registered=True)
    assert got.refused is None
    assert got.length_ft == pytest.approx(5.0 * FT, rel=0.02)
    assert got.width_ft == pytest.approx(3.6 * FT, rel=0.02)
    assert got.floor_area_sf == pytest.approx(5.0 * 3.6 * SF, rel=0.03)
    assert got.perimeter_ft == pytest.approx(2 * 8.6 * FT, rel=0.02)
    assert got.wall_area_sf == pytest.approx(2 * 8.6 * 2.5 * SF, rel=0.03)


def test_no_reference_is_a_refusal_not_a_guess() -> None:
    scene = Scene(door=None)
    got = rm.measure_room(corner_views(scene, scale=0.7))
    assert got.refused == "no reference"
    assert got.scale is None
    assert got.floor_area_sf is None


def test_a_door_whose_foot_is_hidden_is_not_a_reference() -> None:
    # The door segment stops 0.8 m above the floor (a bed in front of it, say): its top's
    # height is still 2.03 m, but nothing says the segment is the whole door; it is skipped.
    scene = Scene(door=(1.0, 0.9))
    views = corner_views(scene, scale=0.7)
    hidden = []
    for v in views:
        pts = np.asarray(v.points)
        labels = np.asarray(v.labels).copy()
        # Height above the floor in the camera frame is not available here: hide the door's
        # lower rows in the picture instead.
        door = labels == 14
        rows = np.nonzero(door.any(axis=1))[0]
        if len(rows):
            cut = rows.min() + int(0.6 * (rows.max() - rows.min()))
            labels[cut:, :][door[cut:, :]] = 0
        hidden.append(rm.View(pts, v.valid, labels, v.camera))
    got = rm.measure_room(hidden)
    assert got.refused == "no reference"


def test_a_counter_is_a_reference_and_is_measured_in_a_kitchen() -> None:
    scene = Scene(door=None, counter=(0.4, 3.2, 0.635))
    truth_sf = 2.8 * 0.635 * SF
    # The corners' photos miss the counter's ends; one more photo faces it from across the room.
    facing = render(scene, (0.3, 1.8, 1.5), 0.0, -20.0, scale=1.3, world=True)
    views = [*corner_views(scene, scale=1.3, world=True), facing]
    registered = rm.measure_room(views, kitchen=True, registered=True)
    assert registered.refused is None
    assert {r.kind for r in registered.references} == {"counter"}
    assert registered.scale == pytest.approx(1 / 1.3, rel=0.02)
    assert registered.countertop_sf == pytest.approx(truth_sf, rel=0.10)
    assert registered.cabinet_lf == pytest.approx(registered.countertop_sf / (25 / 12), rel=1e-9)
    # Unregistered, each photo sees part of the counter and the largest part is kept: never
    # more than the counter (the same counter seen twice is not counted twice).
    single = rm.measure_room(corner_views(scene, scale=1.3), kitchen=True)
    assert single.scale == pytest.approx(1 / 1.3, rel=0.02)
    assert 0 < single.countertop_sf <= registered.countertop_sf * 1.02


def test_outside_a_kitchen_a_counter_is_neither_measured_nor_a_ruler() -> None:
    # A cabinet under the camera in a bedroom is a dresser, in a bathroom a vanity: not 36 in.
    scene = Scene(counter=(0.4, 3.2, 0.635))
    got = rm.measure_room(corner_views(scene))
    assert got.countertop_sf is None and got.cabinet_lf is None
    assert {r.kind for r in got.references} == {"door"}
    kitchen = rm.measure_room(corner_views(scene), kitchen=True)
    assert {r.kind for r in kitchen.references} == {"door", "counter"}


def test_a_base_cabinet_painted_as_cabinet_is_the_counter_in_a_kitchen() -> None:
    # The segmenter often paints a counter's top with the cabinets (class 10).
    scene = Scene(door=None, counter=(0.4, 3.2, 0.635))
    views = []
    for v in corner_views(scene, scale=1.3):
        labels = np.asarray(v.labels).copy()
        labels[labels == 70] = 10
        views.append(rm.View(v.points, v.valid, labels, v.camera))
    got = rm.measure_room(views, kitchen=True)
    assert got.scale == pytest.approx(1 / 1.3, rel=0.02)


def test_the_median_of_references_resists_one_wrong_door() -> None:
    refs = [
        rm.Reference("door", 2.0, rm.DOOR_HEIGHT_M, 0),
        rm.Reference("door", 2.0, rm.DOOR_HEIGHT_M, 1),
        rm.Reference("door", 1.0, rm.DOOR_HEIGHT_M, 2),  # a half-height door: a cabinet door
    ]
    scale, used, why = rm.room_scale(refs, "references")
    assert why is None and len(used) == 3
    assert scale == pytest.approx(rm.DOOR_HEIGHT_M / 2.0)


def test_doors_checked_keeps_the_doors_that_agree_with_the_model() -> None:
    door = rm.DOOR_HEIGHT_M
    refs = [
        rm.Reference("door", door / 1.04, door, 0),  # the model 4 % short: agrees
        rm.Reference("door", door / 1.10, door, 1),  # 10 % short: agrees
        rm.Reference("door", door / 1.60, door, 2),  # a door cut short by a bed: dropped
        rm.Reference("counter", 0.5, rm.COUNTER_HEIGHT_M, 0),  # not a door: not used
    ]
    scale, used, why = rm.room_scale(refs, "doors_checked")
    assert why is None
    assert [r.view for r in used] == [0, 1]
    assert scale == pytest.approx((1.04 + 1.10) / 2)


def test_doors_checked_without_an_agreeing_door_keeps_the_models_metres() -> None:
    refs = [rm.Reference("door", 1.0, rm.DOOR_HEIGHT_M, 0)]  # 2.03 / 1.0: 103 % off
    assert rm.room_scale(refs, "doors_checked") == (1.0, [], None)
    assert rm.room_scale([], "doors_checked") == (1.0, [], None)


def test_doors_only_refuses_without_a_door() -> None:
    refs = [rm.Reference("counter", 0.9, rm.COUNTER_HEIGHT_M, 0)]
    assert rm.room_scale(refs, "doors") == (None, [], "no door")
    scale, used, _ = rm.room_scale([rm.Reference("door", 1.0, rm.DOOR_HEIGHT_M, 0), *refs], "doors")
    assert scale == pytest.approx(rm.DOOR_HEIGHT_M) and len(used) == 1


def test_camera_height_references_are_not_mixed_with_objects() -> None:
    refs = [
        rm.Reference("door", 2.0, rm.DOOR_HEIGHT_M, 0),
        rm.Reference("camera_height", 3.0, rm.CAMERA_HEIGHT_M, 0),
    ]
    assert rm.room_scale(refs, "references")[0] == pytest.approx(rm.DOOR_HEIGHT_M / 2.0)
    assert rm.room_scale(refs, "camera_height")[0] == pytest.approx(0.5)


def test_a_photo_without_floor_is_refused_and_the_others_still_count() -> None:
    scene = Scene()
    views = corner_views(scene)
    blank = rm.View(views[0].points, views[0].valid, np.zeros_like(views[0].labels), (0, 0, 0))
    got = rm.measure_room([blank, *views[1:]])
    assert got.views_used == 3
    assert any("no floor" in n for n in got.notes)
    assert got.refused is None


def test_no_photo_at_all() -> None:
    got = rm.measure_room([])
    assert got.refused == "no photos"


def test_a_tilted_camera_reads_the_same_ceiling() -> None:
    scene = Scene()
    for pitch in (-12.0, 0.0, 5.0):
        view = render(scene, (0.3, 0.3, 1.5), 45.0, pitch, scale=0.8)
        geo, _ = rm.view_geometry([view])
        assert geo.refused is None
        assert geo.ceiling_height == pytest.approx(2.5 * 0.8, rel=0.02)
        assert geo.camera_height == pytest.approx(1.5 * 0.8, rel=0.02)


def test_a_photo_that_shows_no_ceiling_gives_no_ceiling_height() -> None:
    # Tipped 30 degrees down, the top of the view is level: the ceiling is not in it.
    geo, _ = rm.view_geometry([render(Scene(), (0.3, 0.3, 1.5), 45.0, -30.0)])
    assert geo.refused is None
    assert geo.ceiling_height is None


def test_a_room_turned_in_its_frame_measures_the_same() -> None:
    scene = Scene()
    views = corner_views(scene, world=True)
    t = math.radians(20.0)
    rot = np.array([[math.cos(t), -math.sin(t), 0.0], [math.sin(t), math.cos(t), 0.0], [0, 0, 1]])
    turned = [
        rm.View(
            np.asarray(v.points) @ rot.T, v.valid, v.labels, tuple(np.asarray(v.camera) @ rot.T)
        )
        for v in views
    ]
    a = rm.measure_room(views, registered=True)
    b = rm.measure_room(turned, registered=True)
    assert b.length_ft == pytest.approx(a.length_ft, rel=0.01)
    assert b.width_ft == pytest.approx(a.width_ft, rel=0.01)


def test_manhattan_angle_reads_four_fold_directions() -> None:
    for deg in (0.0, 10.0, 44.0):
        t = math.radians(deg)
        dirs = np.array(
            [[math.cos(t + k * math.pi / 2), math.sin(t + k * math.pi / 2)] for k in range(4)] * 20
        )
        got = math.degrees(rm.manhattan_angle(dirs))
        assert min(abs(got - deg), 90 - abs(got - deg)) < 1.0


def test_fit_plane_finds_the_floor_among_clutter() -> None:
    rng = np.random.default_rng(1)
    floor = np.column_stack([rng.uniform(0, 4, 500), rng.uniform(0, 3, 500), np.zeros(500)])
    clutter = rng.uniform(0, 3, (200, 3))
    n, d, inliers = rm.fit_plane(np.vstack([floor, clutter]), 0.01, rng)
    assert abs(abs(n[2]) - 1) < 1e-6 and abs(d) < 1e-6
    assert inliers >= 500


def test_counter_area_of_a_dense_rectangle() -> None:
    xs, ys = np.meshgrid(np.arange(0, 2.0, 0.01), np.arange(0, 0.6, 0.01))
    xy = np.column_stack([xs.ravel(), ys.ravel()])
    assert rm.counter_area(xy, 1.0) == pytest.approx(1.2, rel=0.05)
    assert rm.counter_area(xy, 2.0) == pytest.approx(4.8, rel=0.05)
