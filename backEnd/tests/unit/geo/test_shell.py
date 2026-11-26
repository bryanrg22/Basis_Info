"""basis.geo.shell: the building shell from lidar (EVAL_REPORT 105).

Synthetic point clouds whose shell is known in closed form: a gable and a hip house with
walls, overhang, eave and pitch chosen; the same house with its roof unclassified (the own
classification) and a tree beside it; a neighbour across a gap the closing bridges; and every
refusal. The roof rectangles sit on the 0.5 m grid, so the cells' outline is the roof's."""

from __future__ import annotations

import io
import math
from dataclasses import replace

import numpy as np
import pytest

from basis.errors.codes import E
from basis.geo import lidar, shell
from basis.geo.geometry import to_mercator

from .test_parcels_and_lidar import LAT, LON, box_m

K = math.cos(math.radians(LAT))
CX, CY = to_mercator(LON, LAT)


def cloud(
    *,
    walls: tuple[float, float] = (16.0, 10.0),  # east-west, north-south
    overhang: float = 0.5,
    eave: float = 3.0,
    pitch_deg: float = 30.0,
    roof: str = "gable",
    density: float = 8.0,
    roof_class: int = lidar.BUILDING,
    centre: tuple[float, float] = (0.0, 0.0),
    ground: bool = True,
    seed: int = 1,
) -> list[np.ndarray]:
    """Columns e, n, z, class, return number, number of returns of one house's roof: a
    rectangle of ``walls`` plus ``overhang`` all round; a gable's ridge runs east-west."""
    rng = np.random.default_rng(seed)
    hx, hy = walls[0] / 2 + overhang, walls[1] / 2 + overhang
    count = int(4 * hx * hy * density)
    e = rng.uniform(-hx, hx, count)
    n = rng.uniform(-hy, hy, count)
    slope = math.tan(math.radians(pitch_deg))
    inward = hy - np.abs(n) if roof == "gable" else np.minimum(hx - np.abs(e), hy - np.abs(n))
    z = 100.0 + eave + slope * inward + rng.normal(0, 0.02, count)
    if roof == "flat":
        z = 100.0 + eave + rng.normal(0, 0.02, count)
    if roof == "rough":  # class-6 points with no plane in them
        z = 100.0 + eave + rng.uniform(0, 8.0, count)
    ones = np.ones(count, dtype=np.int64)
    cols = [e + centre[0], n + centre[1], z, ones * roof_class, ones, ones]
    if ground:
        cols = stack(cols, ground_cols(rng, exclude=(centre, hx, hy)))
    return cols


def ground_cols(
    rng: np.random.Generator, *, exclude: tuple[tuple[float, float], float, float]
) -> list[np.ndarray]:
    count = int(80 * 80 * 4)
    e = rng.uniform(-40, 40, count)
    n = rng.uniform(-40, 40, count)
    (cx, cy), hx, hy = exclude
    keep = ~((np.abs(e - cx) <= hx) & (np.abs(n - cy) <= hy))
    e, n = e[keep], n[keep]
    ones = np.ones(e.size, dtype=np.int64)
    return [e, n, 100.0 + rng.normal(0, 0.02, e.size), ones * lidar.GROUND, ones, ones]


def tree(rng: np.random.Generator, at: tuple[float, float], radius: float) -> list[np.ndarray]:
    """A crown: rugged points 4 to 9 m up, from pulses that came back two to four times."""
    count = int(math.pi * radius * radius * 20)
    r = radius * np.sqrt(rng.uniform(0, 1, count))
    a = rng.uniform(0, 2 * math.pi, count)
    e, n = at[0] + r * np.cos(a), at[1] + r * np.sin(a)
    z = 100.0 + rng.uniform(4.0, 9.0, count)
    nr = rng.integers(2, 5, count)
    rn = np.minimum(rng.integers(1, 5, count), nr)
    rn[: count // 2] = 1  # half of them first returns
    return [e, n, z, np.ones(count, dtype=np.int64), rn, nr]


def stack(*parts: list[np.ndarray]) -> list[np.ndarray]:
    return [np.concatenate([p[i] for p in parts]) for i in range(6)]


def points(cols: list[np.ndarray]) -> shell.ShellPoints:
    e, n, z, cls, rn, nr = cols
    return shell.ShellPoints(
        CX + e / K, CY + n / K, z, cls.astype(np.int64), rn.astype(np.int64), nr.astype(np.int64)
    )


def derive(cols: list[np.ndarray], **kw: object) -> shell.Shell:
    return shell.derive(points(cols), LON, LAT, box_m(40), **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the gable house


def test_a_gable_house_from_class_6_reads_its_walls_heights_roof_and_gutters() -> None:
    s = derive(cloud(), overhang_m=0.5)
    assert s.code is None and s.classification == "lidar" and s.refusals == ()
    assert s.roof_plan_m2 == pytest.approx(17 * 11, rel=0.01)
    assert s.perimeter_m == pytest.approx(2 * (16 + 10), rel=0.01)
    assert s.footprint_m2 == pytest.approx(16 * 10, rel=0.02)
    assert s.long_side_m == pytest.approx(16, rel=0.01)
    assert s.offset_method == "mitre"
    # Heights above the ground (100 m): the eave at the roof's edge, the ridge 5.5 tan 30 up.
    ridge = 3 + 5.5 * math.tan(math.radians(30))
    assert s.eave_main_m == pytest.approx(3.0, abs=0.1)
    assert s.ridge_m == pytest.approx(ridge, abs=0.1)
    assert s.stories_estimate == 1
    # Two planes at 30 degrees cover the roof (the points along the ridge, whose neighbours
    # straddle it, can form a thin flat strip: here at most 2 % of the roof); the surface is
    # the plan over cos 30.
    big = sorted(s.planes, key=lambda p: -p.plan_m2)
    assert [round(p.slope_deg) for p in big[:2]] == [30, 30]
    assert sum(p.plan_m2 for p in big[2:]) <= 0.02 * 17 * 11
    assert s.pitch_deg == pytest.approx(30, abs=1)
    assert s.roof_area_m2 == pytest.approx(17 * 11 / math.cos(math.radians(30)), rel=0.02)
    # The long sides are eaves (north and south), the gable ends rakes: gutters 2 x 17 m.
    kinds = {(e.facing, e.kind) for e in s.edges}
    assert kinds == {("N", "eave"), ("S", "eave"), ("E", "rake"), ("W", "rake")}
    assert s.gutter_m == pytest.approx(34, rel=0.01)
    assert s.wall_face_m2 == pytest.approx(52 * s.eave_main_m, rel=1e-9)
    assert len(s.wall_outline_lonlat) == len(s.wall_outline_m) == 4


def test_a_hip_house_has_four_planes_and_gutters_all_round() -> None:
    s = derive(cloud(roof="hip", pitch_deg=25), overhang_m=0.5)
    assert s.code is None
    big = sorted(s.planes, key=lambda p: -p.plan_m2)
    assert [round(p.slope_deg) for p in big[:4]] == [25, 25, 25, 25]
    assert sum(p.plan_m2 for p in big[4:]) <= 0.03 * 17 * 11
    assert all(e.kind == "eave" for e in s.edges)
    assert s.gutter_m == pytest.approx(2 * (17 + 11), rel=0.01)
    assert s.roof_area_m2 == pytest.approx(17 * 11 / math.cos(math.radians(25)), rel=0.02)
    assert s.eave_main_m == pytest.approx(3.0, abs=0.1)


def test_a_flat_roof_is_one_level_plane_and_every_edge_an_eave() -> None:
    s = derive(cloud(roof="flat"), overhang_m=0.0)
    assert len(s.planes) == 1 and s.planes[0].slope_deg < 1
    assert s.roof_area_m2 == pytest.approx(s.roof_plan_m2, rel=0.01)
    assert all(e.kind == "eave" for e in s.edges)
    # No overhang taken off: the walls are the roof outline.
    assert s.perimeter_m == pytest.approx(s.roof_perimeter_m, rel=1e-9)


def test_a_two_storey_house_reads_two_storeys() -> None:
    s = derive(cloud(eave=6.5), overhang_m=0.5)
    assert s.eave_main_m == pytest.approx(6.5, abs=0.1) and s.stories_estimate == 2


def test_the_overhang_is_taken_off_every_side() -> None:
    raw = derive(cloud(), overhang_m=0.0)
    less = derive(cloud(), overhang_m=0.5)
    assert raw.perimeter_m is not None and less.perimeter_m is not None
    assert raw.perimeter_m - less.perimeter_m == pytest.approx(8 * 0.5, abs=1e-6)
    with pytest.raises(ValueError):
        derive(cloud(), overhang_m=-0.1)


def test_derive_is_deterministic() -> None:
    a, b = derive(cloud(roof="hip")), derive(cloud(roof="hip"))
    assert a == b


# ------------------------------------------------------------------ the own classification


def test_without_a_building_class_the_roof_is_classified_from_heights_and_planarity() -> None:
    rng = np.random.default_rng(3)
    cols = stack(cloud(roof_class=1), tree(rng, (14.0, 0.0), 3.0))
    s = derive(cols, overhang_m=0.5)
    assert s.code is None and s.classification == "own"
    assert s.perimeter_m == pytest.approx(52, rel=0.02)
    assert s.eave_main_m == pytest.approx(3.0, abs=0.15)
    # The tree 14 m east (its crown 11 to 17 m) is not part of the roof (which ends at 8.5 m).
    assert max(e for e, _ in s.roof_outline_m) < 9.0


def test_own_matches_class_6_on_the_same_roof() -> None:
    by_class = derive(cloud(), overhang_m=0.5)
    own = derive(cloud(roof_class=1), overhang_m=0.5)
    assert own.classification == "own" and by_class.classification == "lidar"
    assert own.perimeter_m == pytest.approx(by_class.perimeter_m, rel=0.02)
    assert own.roof_area_m2 == pytest.approx(by_class.roof_area_m2, rel=0.03)
    forced = derive(cloud(), classification="own", overhang_m=0.5)
    assert forced.classification == "own"
    only_lidar = derive(cloud(roof_class=1), classification="lidar")
    assert only_lidar.code == E.GEO_SHELL_NO_BUILDING


def test_the_returns_rule_keeps_tree_points_out_and_an_edge_is_grown_back() -> None:
    rng = np.random.default_rng(5)
    house = cloud(roof_class=1)
    # The pulses at the roof's outer 0.5 m came back twice (eave and ground): the returns rule
    # drops them, and the plane test grows them back.
    e, n = house[0], house[1]
    edge = (np.abs(e) > 8.0) | (np.abs(n) > 5.0)
    roof = house[2] > 101.0
    house[5] = np.where(edge & roof, 2, house[5])
    cols = stack(house, tree(rng, (0.0, 9.0), 2.5))
    grown = derive(cols, overhang_m=0.5)
    not_grown = derive(cols, overhang_m=0.5, grow_edges=False)
    assert grown.grown_points > 0 and not_grown.grown_points == 0
    assert grown.perimeter_m == pytest.approx(52, rel=0.02)
    assert not_grown.perimeter_m is not None and not_grown.perimeter_m < 52 * 0.97
    # Every return allowed: the crown 9 m north (its edge 2.5 m from the roof) is not planar,
    # so the roof still ends where it does.
    anyr = derive(cols, overhang_m=0.5, returns="any", grow_edges=False)
    assert max(nn for _, nn in anyr.roof_outline_m) < 6.5


def test_returns_rules_on_a_tile_without_return_numbers() -> None:
    cols = cloud(roof_class=1)
    cols[4] = np.zeros_like(cols[4])
    cols[5] = np.zeros_like(cols[5])
    s = derive(cols, overhang_m=0.5)
    assert s.classification == "own" and s.canopy_share is None
    assert s.perimeter_m == pytest.approx(52, rel=0.02)


# ------------------------------------------------------------------ neighbours and necks


def test_a_neighbour_joined_at_a_corner_is_cut_off_at_the_neck() -> None:
    # A second house 0.5 m east of the first's roof and 7 m north: the closing bridges the
    # gap where their roofs overlap (1 m), a neck the split cuts (EVAL_REPORT 105.2, va1914).
    neighbour = cloud(centre=(17.5, 8.0), walls=(16.0, 6.0), ground=False, seed=2)
    cols = stack(cloud(), neighbour)
    merged = derive(cols, overhang_m=0.5, neck_m=0.0)
    split = derive(cols, overhang_m=0.5)
    assert merged.roof_plan_m2 is not None and merged.roof_plan_m2 > 17 * 11 * 1.3
    assert split.roof_plan_m2 == pytest.approx(17 * 11, rel=0.03)
    assert split.perimeter_m == pytest.approx(52, rel=0.03)


def test_a_neighbour_joined_along_a_whole_side_is_not_told_apart() -> None:
    # The limit of the split: two roofs 0.5 m apart along a whole side close into one wide
    # block with no neck (row houses, attached garages); EVAL_REPORT 105 names it.
    cols = stack(cloud(), cloud(centre=(17.5, 0.0), walls=(16.0, 10.0), ground=False, seed=2))
    s = derive(cols, overhang_m=0.5)
    assert s.roof_plan_m2 is not None and s.roof_plan_m2 > 17 * 11 * 1.8


def test_a_porch_roof_along_a_wall_stays_with_its_house() -> None:
    mask = np.zeros((60, 60), dtype=bool)
    mask[10:40, 10:30] = True  # the house
    mask[40:43, 12:28] = True  # a 1.5 m deep porch roof along its east wall
    assert (shell.split_necks(mask, (20, 20), 2) == mask).all()


def test_split_necks_keeps_the_pins_part() -> None:
    mask = np.zeros((60, 30), dtype=bool)
    mask[5:25, 5:25] = True
    mask[25:27, 14:16] = True  # a 1 m neck
    mask[27:50, 5:25] = True
    left = shell.split_necks(mask, (10, 10), 2)
    right = shell.split_necks(mask, (40, 10), 2)
    assert left[10, 10] and not left[40, 10]
    assert right[40, 10] and not right[10, 10]
    assert (left | right).sum() == mask.sum() and not (left & right).any()
    assert (shell.split_necks(mask, (10, 10), 0) == mask).all()


# ------------------------------------------------------------------ eave levels


def test_the_main_eave_is_the_highest_level_holding_a_quarter_of_the_eaves() -> None:
    def edge(h: float, length: float, kind: str = "eave") -> shell.Edge:
        return shell.Edge((0, 0), (length, 0), length, kind, "S", h)  # type: ignore[arg-type]

    two_storey_and_wing = [edge(6.5, 10), edge(6.6, 10), edge(3.1, 25), edge(3.0, 5)]
    assert shell.main_eave(two_storey_and_wing) == pytest.approx(6.5)
    tiny_tower = [edge(9.0, 2), edge(3.0, 30), edge(3.1, 20)]
    assert shell.main_eave(tiny_tower) == pytest.approx(3.0)
    assert shell.main_eave([edge(3.0, 5, "rake")]) is None
    assert shell.main_eave([]) is None


def test_a_house_with_a_one_storey_wing_takes_the_main_blocks_eave() -> None:
    main = cloud(eave=6.0, walls=(10.0, 10.0), seed=4)
    wing = cloud(eave=3.0, walls=(8.0, 8.0), centre=(9.5, 0.0), ground=False, seed=5)
    keep = wing[0] > 5.5  # the wing's roof east of the main block's
    wing = [c[keep] for c in wing]
    s = derive(stack(main, wing), overhang_m=0.5)
    assert s.eave_m is not None and s.eave_m < 3.5  # the 5th percentile is the wing's
    assert s.eave_main_m == pytest.approx(6.0, abs=0.2)


# ------------------------------------------------------------------ refusals


def test_no_point_in_the_area_read() -> None:
    empty = [np.zeros(0)] * 3 + [np.zeros(0, dtype=np.int64)] * 3
    s = derive(empty)
    assert s.code == E.GEO_SHELL_NO_BUILDING and s.perimeter_m is None


def test_no_building_near_the_pin() -> None:
    s = derive(cloud(centre=(30.0, 0.0), walls=(10.0, 6.0)), overhang_m=0.5)
    assert s.code == E.GEO_SHELL_NO_BUILDING and s.refusals[0][0] == "building"
    near = derive(cloud(centre=(12.0, 0.0), walls=(10.0, 6.0)), overhang_m=0.5)
    assert near.code is None and near.distance_m > 0


def test_a_shed_under_twenty_square_metres_is_not_a_building() -> None:
    s = derive(cloud(walls=(3.0, 3.0), overhang=0.5))
    assert s.code == E.GEO_SHELL_NO_BUILDING and "under 20" in s.refusals[0][2]


def test_no_ground_class_means_no_heights() -> None:
    no_ground = cloud(roof_class=1, ground=False)
    assert derive(no_ground).code == E.GEO_SHELL_NO_GROUND
    # Class 6 but no ground ring around it.
    assert derive(cloud(ground=False)).code == E.GEO_SHELL_NO_GROUND


def test_walls_that_do_not_survive_the_overhang() -> None:
    s = derive(cloud(walls=(30.0, 1.0), overhang=0.5), overhang_m=1.2)
    assert s.code == E.GEO_SHELL_WALLS_UNREAD
    assert s.perimeter_m is None and s.wall_face_m2 is None
    assert s.eave_m is not None and s.roof_area_m2 is not None  # the rest is still read


def test_no_roof_plane() -> None:
    s = derive(cloud(roof="rough", density=2.0), overhang_m=0.5)
    assert s.code == E.GEO_SHELL_NO_PLANES
    assert s.roof_area_m2 is None and s.gutter_m is None and s.pitch_deg is None
    assert s.perimeter_m is not None


def test_a_roof_under_trees_is_refused() -> None:
    rng = np.random.default_rng(9)
    crown = tree(rng, (0.0, 0.0), 6.0)
    crown[2] = crown[2] + 3.0  # 7 to 12 m: over the roof
    s = derive(stack(cloud(), crown), overhang_m=0.5)
    assert s.code == E.GEO_SHELL_UNDER_TREES
    assert s.canopy_share is not None and s.canopy_share > shell.MAX_CANOPY_SHARE
    assert s.perimeter_m is None and s.roof_area_m2 is None


def test_a_building_at_the_edge_of_the_area_read_gets_no_walls() -> None:
    s = shell.derive(points(cloud()), LON, LAT, box_m(8.0), overhang_m=0.5)
    assert s.code == E.GEO_LIDAR_FOOTPRINT_UNCERTAIN and s.reaches_box_edge
    assert s.perimeter_m is None and s.wall_face_m2 is None and s.eave_main_m is not None


# ------------------------------------------------------------------ geometry


def test_mitred_offset_of_a_rectangle_and_an_l() -> None:
    rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
    out = shell.offset_inward_mitre(rect, 1.0)
    assert out is not None
    assert [c for p in out for c in p] == pytest.approx([1, 1, 9, 1, 9, 5, 1, 5])
    ell = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 10.0), (0.0, 10.0)]
    o = shell.offset_inward_mitre(ell, 0.5)
    assert o is not None
    assert shell.ring_perimeter(o) == pytest.approx(shell.ring_perimeter(ell) - 8 * 0.5)
    assert shell.offset_inward_mitre(rect, 3.5) is None  # the 6 m side turns round
    assert shell.offset_inward_mitre(rect, 0.0) == rect


def test_raster_offset_agrees_with_the_mitre_and_survives_where_it_folds() -> None:
    rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
    raster = shell.offset_inward_raster(rect, 1.0)
    assert raster is not None
    assert shell.ring_area(raster) == pytest.approx(8 * 4, rel=0.03)
    # A 0.6 m jog folds the mitre at 0.5 m; the raster keeps the body.
    jog = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (5.3, 6.0), (5.3, 6.6), (4.7, 6.6), (4.7, 6.0)]
    jog += [(0.0, 6.0)]
    assert shell.offset_inward_mitre(jog, 0.5) is None
    body = shell.offset_inward_raster(jog, 0.5)
    assert body is not None and shell.ring_area(body) == pytest.approx(9 * 5, rel=0.03)
    assert shell.offset_inward_raster(rect, 4.0) is None


def test_long_side_of_a_turned_rectangle_and_points_in_a_ring() -> None:
    a = math.radians(30)
    corners = [(0, 0), (12, 0), (12, 5), (0, 5)]
    turned = [
        (x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)) for x, y in corners
    ]
    assert shell.long_side(turned) == pytest.approx(12)
    ring = np.array([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)])
    q = np.array([(1.0, 1.0), (5.0, 1.0), (2.0, -0.1), (3.9, 3.9)])
    assert shell.points_in_ring(q, ring).tolist() == [True, False, False, True]
    assert shell.ring_area(corners) == pytest.approx(60) and shell.ring_area(corners[::-1]) < 0


def test_fit_planes_finds_two_planes_and_nothing_in_noise() -> None:
    rng = np.random.default_rng(0)
    e = rng.uniform(-5, 5, 800)
    n = rng.uniform(-3, 3, 800)
    z = 3 + 0.5 * (3 - np.abs(n))
    pts = np.column_stack([e, n, z])
    normals, std, _ = shell.local_normals(e, n, z)
    planes, label = shell.fit_planes(pts, normals)
    sizes = np.bincount(label[label >= 0])
    top = np.argsort(sizes)[::-1][:2]
    assert sizes[top].sum() > 0.85 * len(pts)  # two planes hold the roof; a ridge strip may not
    slopes = sorted(round(math.degrees(math.acos(planes[int(i)][0][2]))) for i in top)
    assert slopes == [27, 27]  # atan(0.5)
    assert float(np.median(std)) < 0.05
    noise = np.column_stack([e, n, rng.uniform(0, 5, 800)])
    nn, _, _ = shell.local_normals(noise[:, 0], noise[:, 1], noise[:, 2])
    assert shell.fit_planes(noise, nn)[0] == []


# ------------------------------------------------------------- the class-6 path is the summary's


def test_the_class_6_roof_outline_is_the_lidar_summarys() -> None:
    """With no neck in the building, the shell's roof is exactly the cells the lidar summary
    measures (same grid, closing, hole filling and component): the summary is unchanged and
    the shell starts from it."""
    cols = cloud()
    s = derive(cols, overhang_m=0.5)
    p = points(cols)
    _, _, b = lidar.measure(lidar.Points(p.x, p.y, p.z, p.cls), LON, LAT, box_m(40))
    assert b is not None
    assert s.roof_plan_m2 == b.footprint_m2
    assert s.eave_m == pytest.approx(b.eave_m) and s.ridge_m == pytest.approx(b.peak_m)
    assert s.ground_elevation_m == pytest.approx(b.ground_elevation_m)


# ------------------------------------------------------------------ reading


def test_decode_laz_keeps_the_return_numbers() -> None:
    import laspy

    cols = cloud(density=1.0)
    p = points(cols)
    p = replace(p, return_number=np.where(p.cls == 6, 1, 2), number_of_returns=np.full(p.x.size, 2))
    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets = [p.x.min(), p.y.min(), p.z.min()]
    header.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(header)
    las.x, las.y, las.z = p.x, p.y, p.z
    las.classification = p.cls.astype(np.uint8)
    las.return_number = p.return_number.astype(np.uint8)
    las.number_of_returns = p.number_of_returns.astype(np.uint8)
    out = io.BytesIO()
    las.write(out, laz_backend=laspy.LazBackend.Lazrs)
    got = shell.decode_laz_returns(out.getvalue())
    assert got.x.size == p.x.size
    assert (got.cls == p.cls).all()
    assert (got.return_number == p.return_number).all()
    assert (got.number_of_returns == 2).all()
    with pytest.raises(Exception) as err:
        shell.decode_laz_returns(b"not a laz file")
    assert E.GEO_LIDAR_UNREADABLE in str(err.value)
    part = got.within(box_m(5))
    assert 0 < part.x.size < got.x.size
    assert shell.ShellPoints.concat([]).x.size == 0
