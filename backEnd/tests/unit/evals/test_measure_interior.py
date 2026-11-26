"""The interior eval (EVAL_REPORT 107): the truth measured on a synthetic room mesh of known
size (``evals.interior_truth``), the acceptance rule (``evals.interior_accept``), M0 and the
scoring (``evals.measure_interior``), the M4d answer and its ledger
(``evals.measure_interior_vlm``, scripted model), the photo helpers (``evals.interior_data``)
and the laser registration's similarity fit (``evals.interior_laser``). No network, no paid
call, no ARKitScenes file."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import anthropic
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from basis.config import Settings
from basis.llm import prompts
from basis.llm.cost import cost_decimal
from basis.llm.provider import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.providers import strict_json_schema
from basis.llm.recorder import MemoryRecorder
from evals import interior_accept as ia
from evals import interior_data as idata
from evals import interior_laser as il
from evals import interior_truth as it
from evals import measure_eval as me
from evals import measure_interior as mi
from evals import measure_interior_vlm as miv
from evals import measure_vlm as mv

FT = it.M_TO_FT
SF = it.SQM_TO_SF

# ------------------------------------------------------------------ a synthetic room mesh


def quad(points: list[list[float]], a: Any, b: Any, c: Any, d: Any, faces: list[list[int]]) -> None:
    """Two triangles a-b-c, a-c-d (counter-clockwise seen from the normal's side)."""
    k = len(points)
    points += [list(a), list(b), list(c), list(d)]
    faces += [[k, k + 1, k + 2], [k, k + 2, k + 3]]


def grid_quads(
    points: list[list[float]],
    faces: list[list[int]],
    origin: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    nu: int,
    nv: int,
    keep: Any = None,
) -> None:
    for i in range(nu):
        for j in range(nv):
            p0 = origin + u * i / nu + v * j / nv
            centre = origin + u * (i + 0.5) / nu + v * (j + 0.5) / nv
            if keep is not None and not keep(centre):
                continue
            quad(points, p0, p0 + u / nu, p0 + u / nu + v / nv, p0 + v / nv, faces)


def room_mesh(
    length: float = 5.0,
    width: float = 3.6,
    height: float = 2.5,
    *,
    yaw_deg: float = 0.0,
    missing_wall: bool = False,
    sloped: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """A box room (floor up, ceiling down, four walls with a doorway 0.9 x 2.03 m), z up."""
    pts: list[list[float]] = []
    faces: list[list[int]] = []
    o = np.zeros(3)
    ex, ey, ez = np.eye(3)
    n = 120  # 4 cm faces on a 5 m wall, as dense as an ARKit mesh
    grid_quads(pts, faces, o, ex * length, ey * width, n, n)  # floor, normal +z
    if sloped:
        # A ceiling rising 0.8 m from one side to the other: no level holds most of it.
        top = o + ez * height
        grid_quads(pts, faces, top + ey * width, ex * length, ey * -width + ez * 0.8, n, n)
    else:
        grid_quads(pts, faces, o + ez * height, ey * width, ex * length, n, n)  # normal -z
    door = lambda c: not (1.0 <= c[0] <= 1.9 and c[2] <= 2.03)  # noqa: E731
    wall = ez * (height + (0.8 if sloped else 0.0))  # walls up to the slope's top
    grid_quads(pts, faces, o, ex * length, wall, n, n, keep=door)  # y = 0
    grid_quads(pts, faces, o + ey * width, wall, ex * length, n, n)  # y = width
    grid_quads(pts, faces, o, wall, ey * width, n, n)  # x = 0
    if not missing_wall:
        grid_quads(pts, faces, o + ex * length, ey * width, wall, n, n)  # x = length
    points = np.asarray(pts, dtype=np.float64)
    t = math.radians(yaw_deg)
    rot = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
    return points @ rot.T + np.array([10.0, -4.0, -1.2]), np.asarray(faces, dtype=np.int64)


def cameras(yaw_deg: float = 0.0) -> np.ndarray:
    t = math.radians(yaw_deg)
    rot = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
    path = np.array([[1.0 + 0.3 * k, 1.5 + 0.1 * (k % 3), 1.4] for k in range(10)])
    return path @ rot.T + np.array([10.0, -4.0, -1.2])


def test_the_truth_of_a_box_room() -> None:
    points, faces = room_mesh()
    truth, rasters = it.measure_room("box", points, faces, cameras(), [])
    assert truth.status == "ok", truth.reason
    assert truth.ceiling_height_ft == pytest.approx(2.5 * FT, rel=0.005)
    assert truth.floor_area_sf == pytest.approx(5.0 * 3.6 * SF, rel=0.02)
    assert truth.length_ft == pytest.approx(5.0 * FT, rel=0.01)
    assert truth.width_ft == pytest.approx(3.6 * FT, rel=0.015)
    assert truth.perimeter_ft == pytest.approx(2 * 8.6 * FT, rel=0.02)
    assert truth.wall_area_sf == pytest.approx(2 * 8.6 * 2.5 * SF, rel=0.025)
    assert truth.cameras_inside == 1.0
    assert truth.countertop_sf is None  # not a kitchen
    assert "region" in rasters


def test_the_truth_does_not_depend_on_the_rooms_direction() -> None:
    a, _ = it.measure_room("a", *room_mesh(), cameras(), [])
    b, _ = it.measure_room("b", *room_mesh(yaw_deg=33.0), cameras(33.0), [])
    for q in ("floor_area_sf", "length_ft", "width_ft", "perimeter_ft"):
        assert getattr(b, q) == pytest.approx(getattr(a, q), rel=0.03)


def test_the_corners_a_closing_rounds_off_are_grown_back() -> None:
    walls = np.zeros((60, 60), dtype=bool)
    walls[5, 5:55] = walls[54, 5:55] = walls[5:55, 5] = walls[5:55, 54] = True
    inside = np.zeros_like(walls)
    inside[6:54, 6:54] = True
    r = 12
    from scipy import ndimage

    closed = ndimage.binary_closing(np.pad(walls, r), structure=it.disk(r))[r:-r, r:-r]
    rounded = inside & ~closed
    assert rounded.sum() < inside.sum()  # the closing took the corners
    grown = it.regrow(rounded, walls, r)
    # Back to the square, less the cells next to a wall (kept free of the wall's neighbours).
    assert (grown & ~inside).sum() == 0
    assert grown.sum() >= inside[1:-1, 1:-1].sum() - 4 * 48
    assert grown[7, 7] and grown[52, 52] and not rounded[7, 7]


def test_an_open_room_is_refused() -> None:
    truth, _ = it.measure_room("open", *room_mesh(missing_wall=True), cameras(), [])
    assert truth.status == "refused" and truth.reason is not None
    assert truth.reason.startswith("open")


def test_a_sloped_ceiling_gives_no_height_and_no_wall_area() -> None:
    truth, _ = it.measure_room("slope", *room_mesh(sloped=True), cameras(), [])
    assert truth.status == "ok"
    assert truth.ceiling_height_ft is None and truth.wall_area_sf is None
    assert truth.method["ceiling_flat_share"] < it.FLAT_CEILING_SHARE
    assert truth.floor_area_sf == pytest.approx(5.0 * 3.6 * SF, rel=0.03)


def box(
    label: str, centre: tuple[float, float, float], lengths: tuple[float, float, float]
) -> it.Box:
    return it.Box(label, centre, lengths, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


def test_counters_come_from_the_base_units_boxes_in_a_kitchen() -> None:
    points, faces = room_mesh()
    floor = -1.2
    off = np.array([10.0, -4.0, 0.0])
    boxes = [
        box("cabinet", tuple(off + [2.0, 3.3, floor + 0.45]), (2.0, 0.6, 0.9)),  # base run
        box("dishwasher", tuple(off + [3.3, 3.3, floor + 0.45]), (0.6, 0.6, 0.9)),
        box("cabinet", tuple(off + [2.0, 3.43, floor + 1.9]), (2.0, 0.35, 0.7)),  # a wall cabinet
        box("sink", tuple(off + [1.5, 3.3, floor + 0.8]), (0.5, 0.4, 0.2)),
        box("stove", tuple(off + [4.0, 3.3, floor + 0.45]), (0.6, 0.6, 0.9)),
    ]
    truth, _ = it.measure_room("kitchen", points, faces, cameras(), boxes)
    assert truth.room_type == "kitchen"
    assert truth.cabinet_lf == pytest.approx(2.6 * FT, rel=1e-6)  # the base run and the dishwasher
    assert truth.countertop_sf == pytest.approx(2.6 * 0.6 * SF, rel=0.06)
    assert [c["label"] for c in truth.counters] == ["cabinet", "dishwasher"]


def test_room_types_from_the_boxes() -> None:
    assert it.room_type({"sink": 1, "oven": 1}) == "kitchen"
    assert it.room_type({"sink": 1, "toilet": 1}) == "bathroom"
    assert it.room_type({"bed": 1, "sofa": 1}) == "bedroom"
    assert it.room_type({"sofa": 2}) == "living"
    assert it.room_type({"table": 1}) == "other"


def test_the_box_layout_is_the_one_the_mesh_fills() -> None:
    rng = np.random.default_rng(3)
    inside = rng.uniform([-0.9, -0.2, -0.4], [0.9, 0.2, 0.4], (2000, 3))  # a long box along x
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    turned = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    pts = inside @ turned.T  # long along the turned x
    data = {
        "data": [
            {
                "label": "cabinet",
                "segments": {
                    "obbAligned": {
                        "centroid": [0, 0, 0],
                        "axesLengths": [1.8, 0.4, 0.8],
                        "normalizedAxes": turned.ravel().tolist(),  # its columns are the axes
                    }
                },
            }
        ]
    }
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "a.json"
        path.write_text(json.dumps(data))
        assert it.box_axes_layout(pts, path) is True
        b = it.read_boxes(path, transpose=True)[0]
        assert it.points_in_box(pts, b) == len(pts)


def test_ply_round_trip(tmp_path: Path) -> None:
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="<f4")
    header = (
        b"ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float x\n"
        b"property float y\nproperty float z\nproperty uchar red\nproperty uchar green\n"
        b"property uchar blue\nproperty uchar alpha\nelement face 1\n"
        b"property list uchar int vertex_indices\nend_header\n"
    )
    body = b"".join(p.tobytes() + bytes([1, 2, 3, 4]) for p in pts)
    body += bytes([3]) + np.array([0, 1, 2], dtype="<i4").tobytes()
    (tmp_path / "m.ply").write_bytes(header + body)
    v, f = it.read_ply(tmp_path / "m.ply")
    assert v.tolist() == pts.tolist() and f.tolist() == [[0, 1, 2]]


def test_traj_gives_camera_centres_from_world_to_camera_poses(tmp_path: Path) -> None:
    # A camera at (1, 2, 3) turned 90 degrees about z: world to camera R, t = -R c.
    r = it.axis_angle_matrix([0.0, 0.0, math.pi / 2])
    c = np.array([1.0, 2.0, 3.0])
    t = -r @ c
    (tmp_path / "t.traj").write_text(f"5.0 0 0 {math.pi / 2} {t[0]} {t[1]} {t[2]}\n")
    ts, rot, centre = it.read_traj(tmp_path / "t.traj")
    assert ts.tolist() == [5.0]
    assert centre[0] == pytest.approx(c)
    assert rot[0] == pytest.approx(r.T)


# ------------------------------------------------------------------ the acceptance rule


def write_truth(folder: Path, rid: str, **q: float) -> None:
    t = it.RoomTruth(rid, "ok", room_type="kitchen", **q)  # type: ignore[arg-type]
    it.save(t, folder / f"{rid}.json")


def test_acceptance_keeps_rooms_whose_scans_agree(tmp_path: Path) -> None:
    write_truth(tmp_path / "truth", "a", floor_area_sf=100.0, cabinet_lf=10.0)
    write_truth(tmp_path / "truth_repeat", "a", floor_area_sf=103.0, cabinet_lf=14.0)
    write_truth(tmp_path / "truth", "b", floor_area_sf=100.0)
    write_truth(tmp_path / "truth_repeat", "b", floor_area_sf=110.0)
    write_truth(tmp_path / "truth", "c", floor_area_sf=100.0)
    it.save(it.RoomTruth("c", "refused", reason="open"), tmp_path / "truth_repeat" / "c.json")
    write_truth(tmp_path / "truth", "d", floor_area_sf=100.0)
    got = ia.accept(tmp_path)
    assert list(got["rooms"]) == ["a"]
    assert got["rooms"]["a"]["quantities"] == ["floor_area_sf"]  # the cabinet run moved 40 %
    assert got["refused"]["b"].startswith("scans disagree")
    assert got["refused"]["c"] == "second scan: open"
    assert got["refused"]["d"] == "no second scan"


# ------------------------------------------------------------------ M0 and the scoring


def test_m0_is_the_production_takeoff_rows() -> None:
    q = mi.formulas(100.0, kitchen=True)
    assert q["perimeter_ft"] == pytest.approx(40.0)
    assert q["wall_area_sf"] == pytest.approx(350.0)
    assert q["countertop_sf"] == pytest.approx(40.0 * 25 / 12, abs=0.01)
    assert q["cabinet_lf"] == pytest.approx(40.0)
    assert q["ceiling_height_ft"] == 8.0 and q["length_ft"] == 10.0
    assert "countertop_sf" not in mi.formulas(100.0, kitchen=False)


def rooms() -> list[mi.Room]:
    return [
        mi.Room(
            "r1", "kitchen", {"floor_area_sf": 100.0, "cabinet_lf": 10.0}, Path("/nonexistent")
        ),
        mi.Room("r2", "bedroom", {"floor_area_sf": 200.0}, Path("/nonexistent")),
    ]


def test_scoring_misses_and_reconciliation() -> None:
    fixed = {"r1": mi.Result({"floor_area_sf": 90.0}), "r2": mi.Result({"floor_area_sf": 220.0})}
    setup = mi.Setup("X", "fixed", {}, lambda room: fixed[room.id])
    got = mi.evaluate([setup], rooms())
    s = {(x.room, x.quantity): x.signed_pct for x in got["scores"]}
    assert s == {
        ("r1", "floor_area_sf"): pytest.approx(-10.0),
        ("r2", "floor_area_sf"): pytest.approx(10.0),
    }
    assert got["misses"]["X"] == {"cabinet_lf": ["r1"]}
    rec = got["reconcile"][0]
    assert rec["sum_error_pct"] == pytest.approx(100 * (310 - 300) / 300)
    k = 300 / 310
    assert rec["mean_abs_pct_after"] == pytest.approx(
        (abs(90 * k - 100) / 100 + abs(220 * k - 200) / 200) * 50
    )
    row = next(r for r in got["summary"] if r["quantity"] == "cabinet_lf")
    assert row["rooms"] == 0 and row["misses"] == 1


def test_a_refusal_is_a_miss_with_its_reason() -> None:
    setup = mi.Setup("Y", "refuses", {}, lambda room: mi.Result({}, notes={"refused": "no door"}))
    got = mi.evaluate([setup], rooms())
    assert got["refusals"]["Y"] == {"r1": "no door", "r2": "no door"}
    assert got["scores"] == []


def test_paired_difference_and_t() -> None:
    scores = [
        mi.Score("A", "r1", "k", "q", 105.0, 100.0),
        mi.Score("A", "r2", "k", "q", 110.0, 100.0),
        mi.Score("B", "r1", "k", "q", 120.0, 100.0),
        mi.Score("B", "r2", "k", "q", 130.0, 100.0),
    ]
    p = mi.paired(scores, "A", "B", "q")
    assert p is not None
    assert p["mean_diff_pct"] == pytest.approx(-17.5) and p["a_better"] == 2


def test_labels_are_read_at_the_point_maps_size(tmp_path: Path) -> None:
    labels = np.zeros((8, 6), dtype=np.uint8)
    labels[:4] = 3
    Image.fromarray(labels).save(tmp_path / "x.ade.png")
    half = mi.labels_for(tmp_path / "x.ade.png", (4, 3))
    assert half.shape == (4, 3) and (half[:2] == 3).all() and (half[2:] == 0).all()


# ------------------------------------------------------------------ M4d


def interior_answer(**overrides: Any) -> miv.InteriorAnswer:
    fields: dict[str, Any] = {
        "length_ft": 14.0,
        "width_ft": 10.0,
        "floor_area_sf": 140.0,
        "ceiling_height_ft": 8.0,
        "perimeter_ft": 48.0,
        "wall_area_sf": 384.0,
        "countertop_sf": 30.0,
        "cabinet_lf": 15.0,
        "room_fully_seen": False,
        "outline_shape": "rectangle",
        "confidence": "medium",
        "notes": "counted the doors",
    }
    fields.update(overrides)
    return miv.InteriorAnswer(**fields)


def test_the_answer_schema_refuses_negatives_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        interior_answer(length_ft=-1)
    with pytest.raises(ValidationError):
        miv.InteriorAnswer(**{**interior_answer().model_dump(), "volume": 3})
    assert len(interior_answer(notes="x" * 2000).notes) == miv.NOTE_MAX
    strict = strict_json_schema(miv.InteriorAnswer)
    assert set(strict["required"]) == set(miv.InteriorAnswer.model_fields)
    assert anthropic.transform_schema(miv.InteriorAnswer)["type"] == "object"


def test_counters_count_only_in_kitchens_and_nulls_are_left_out() -> None:
    a = interior_answer(ceiling_height_ft=None)
    assert "countertop_sf" in miv.answer_quantities(a, kitchen=True)
    q = miv.answer_quantities(a, kitchen=False)
    assert "countertop_sf" not in q and "cabinet_lf" not in q and "ceiling_height_ft" not in q


def test_the_prompt_is_released() -> None:
    p = prompts.load(miv.PROMPT, miv.PROMPT_VERSION)
    assert "80 in" in p.text and "36 in" in p.text


def room_photos(tmp_path: Path) -> miv.RoomPhotos:
    folder = tmp_path / "r1"
    folder.mkdir()
    for k in (1, 2):
        Image.new("RGB", (128, 96), (200, 100, 50)).save(folder / f"photo_{k}.png")
    (folder / "photos.json").write_text(
        json.dumps({"photos": [{"file": "photo_1.png"}, {"file": "photo_2.png"}]})
    )
    return miv.load_photos(folder, "kitchen")


def test_each_room_call_is_paid_from_its_own_ledger_rows(tmp_path: Path) -> None:
    photos = room_photos(tmp_path)
    assert len(photos.jpegs) == 2
    setup = miv.Setup(mv.models_from_settings(Settings())["sol"])
    u1 = Usage(input_tokens=2000, output_tokens=500, reasoning_output_tokens=300)
    provider = ScriptedProvider(
        [
            ScriptedTurn(parsed=interior_answer(), usage=u1),
            ScriptedTurn(parsed=interior_answer(), usage=u1),
        ],
        model="gpt-6.1-sol",
    )
    recorder = MemoryRecorder()
    budget = Budget(remaining_usd=1.0)
    ctx = mv.new_context(budget, recorder, "evals.measure_interior_vlm")
    outcomes = asyncio.run(
        miv.run_jobs(
            provider, ctx, recorder.records, [(setup, photos, 1), (setup, photos, 2)], concurrency=1
        )
    )
    assert [o.repeat for o in outcomes] == [1, 2]
    assert outcomes[0].cost_usd == pytest.approx(float(cost_decimal("gpt-6.1-sol", u1)))
    assert outcomes[0].quantities["cabinet_lf"] == 15.0
    assert recorder.records[0].entry.prompt_version == "measure_interior_reference/v1"
    assert recorder.records[0].entry.entity_type == "measure_room"
    assert budget.spent_usd == pytest.approx(2 * outcomes[0].cost_usd)
    request = miv.build_request(setup, photos, study_id="s", activity="a")
    assert len(request.messages[0].images) == 2 and "kitchen" in request.messages[0].content
    assert miv.estimate_job_usd(setup, photos) > 0


def test_m4d_repeats_are_scored_apart_and_their_noise_measured() -> None:
    setup = miv.Setup(mv.models_from_settings(Settings())["luna"])
    outcomes = [
        miv.Outcome(setup.id, "r1", 1, answer={}, quantities={"floor_area_sf": 100.0}),
        miv.Outcome(setup.id, "r1", 2, answer={}, quantities={"floor_area_sf": 110.0}),
        miv.Outcome(setup.id, "r2", 1, answer=None),
    ]
    scored, results = me.m4d_results(outcomes, [setup])
    assert [s.id for s in scored] == [f"{setup.id}#1", f"{setup.id}#2"]
    assert results[f"{setup.id}#1"]["r2"].notes == {"refused": "no answer"}
    noise = me.m4d_noise(outcomes)
    assert noise == [
        {
            "setup": setup.id,
            "quantity": "floor_area_sf",
            "rooms": 1,
            "mean_pct": 10.0,
            "max_pct": 10.0,
        }
    ]


# ------------------------------------------------------------------ the photo helpers


def test_upright_rotation_and_view_angles() -> None:
    assert idata.upright_rotation("Left") == 270 and idata.upright_rotation("Up") == 0
    # A camera looking along world +x, level: pitch 0, yaw 0 (camera z is its axis).
    r_cw = np.array([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])
    pitch, yaw = idata.view_angles(idata.Pose(0.0, r_cw, np.zeros(3)), "Up")
    assert pitch == pytest.approx(0.0, abs=1e-9) and yaw == pytest.approx(0.0, abs=1e-9)


def test_nearest_member_and_sharpness() -> None:
    names = ["vga/12_1.000.png", "vga/12_1.500.png", "vga/99_1.100.png"]
    assert idata.nearest_member(names, "12", 1.2) == "vga/12_1.000.png"
    assert idata.nearest_members(names, "12", 1.45, 2) == ["vga/12_1.500.png", "vga/12_1.000.png"]
    flat = Image.new("RGB", (20, 20), (128, 128, 128))
    checks = Image.fromarray(((np.indices((20, 20)).sum(0) % 2) * 255).astype(np.uint8)).convert(
        "RGB"
    )
    assert idata.sharpness(checks) > idata.sharpness(flat) == 0.0


def test_visible_depths_see_a_wall_ahead() -> None:
    r_cw = np.array([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])  # looking along +x
    pose = idata.Pose(0.0, r_cw, np.zeros(3))
    ys, zs = np.meshgrid(np.linspace(-3, 3, 200), np.linspace(-2, 2, 150))
    wall = np.column_stack([np.full(ys.size, 3.0), ys.ravel(), zs.ravel()])
    behind = wall * np.array([-1, 1, 1])
    depth = idata.visible_depths(pose, np.vstack([wall, behind]))
    filled = np.isfinite(depth)
    assert filled.mean() > 0.95
    assert np.nanmedian(depth) == pytest.approx(3.0)


# ------------------------------------------------------------------ the laser fit


def test_umeyama_recovers_a_similarity() -> None:
    rng = np.random.default_rng(5)
    src = rng.normal(size=(200, 3))
    r = il.yaw(0.7)
    dst = 1.03 * src @ r.T + np.array([1.0, -2.0, 0.5])
    s, rot, t = il.umeyama(src, dst)
    assert (
        s == pytest.approx(1.03)
        and rot == pytest.approx(r)
        and t == pytest.approx([1.0, -2.0, 0.5])
    )


def test_floor_level_is_the_lower_peak() -> None:
    z = np.concatenate([np.full(500, 0.01), np.full(400, 2.5), np.linspace(0, 2.5, 100)])
    assert il.floor_level(z) == pytest.approx(0.01, abs=0.02)
