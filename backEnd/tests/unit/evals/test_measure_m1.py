"""M1 in ``evals.measure_eval`` and the point cache of ``evals.measure_points``
(EVAL_REPORT 105): a synthetic house of known shell cached as the runner caches the real
ones, scored end to end; the paired comparison's arithmetic; the USA Structures polygon under
the point; every way a setup drafts nothing."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from basis.geo import lidar, shell
from basis.geo.geometry import from_mercator, to_mercator
from evals import measure_eval as me
from evals import measure_points as mp
from evals import measure_truth as mt

FT = 0.3048


def cache_house(geo: Path, house: mt.House, *, overhang_ft: float = 1.5) -> None:
    """The synthetic house (40 x 30 ft walls, hip, eave 20 ft, ridge 28 ft) as lidar points
    over an 80 m box, written as measure_points writes a real one."""
    lat, lon = house.location.lat, house.location.lon
    k = math.cos(math.radians(lat))
    cx, cy = to_mercator(lon, lat)
    rng = np.random.default_rng(2)
    hx, hy = (40 / 2 + overhang_ft) * FT, (30 / 2 + overhang_ft) * FT
    count = int(4 * hx * hy * 9)
    e = rng.uniform(-hx, hx, count)
    n = rng.uniform(-hy, hy, count)
    rise = (28 - 20) * FT / hy
    z = 200 + 20 * FT + rise * np.minimum(hx - np.abs(e), hy - np.abs(n))
    ge = rng.uniform(-40, 40, 25600)
    gn = rng.uniform(-40, 40, 25600)
    keep = ~((np.abs(ge) <= hx) & (np.abs(gn) <= hy))
    ee = np.concatenate([e, ge[keep]])
    nn = np.concatenate([n, gn[keep]])
    zz = np.concatenate([z, np.full(int(keep.sum()), 200.0)])
    cls = np.concatenate([np.full(count, 1), np.full(int(keep.sum()), 2)]).astype(np.int64)
    ones = np.ones(ee.size, dtype=np.int64)
    pts = shell.ShellPoints(cx + ee / k, cy + nn / k, zz, cls, ones, ones, "SYN_2020")
    folder = geo / house.id
    folder.mkdir(parents=True)
    mp.save_points(folder, shell.PointsRead(pts, "SYN_2020", "u", 3, 4567, ()))
    h = 40 / k
    w, s = from_mercator(cx - h, cy - h)
    east, north = from_mercator(cx + h, cy + h)
    meta = {"project": "SYN_2020", "box_lonlat": [w, s, east, north], "bytes": 4567}
    (folder / "points.json").write_text(json.dumps(meta))


def test_the_cache_round_trips(tmp_path: Path) -> None:
    house = me.synthetic_house()
    cache_house(tmp_path, house)
    got = mp.load_points(tmp_path / house.id)
    assert got.project == "SYN_2020" and got.x.dtype == np.float64
    assert set(np.unique(got.cls).tolist()) == {1, 2}
    assert me.house_points(tmp_path, "nothing-here") is None


def test_m1_scores_the_synthetic_house(tmp_path: Path) -> None:
    house = me.synthetic_house()
    cache_house(tmp_path, house)
    result = me.run_m1(house, tmp_path, me.M1Settings())
    q = result.quantities
    truth = mt.truths(house)
    assert result.notes["classification"] == "own" and result.notes["code"] == "None"
    assert q["perimeter_ft"] == pytest.approx(truth["perimeter_ft"].value, rel=0.03)
    assert q["footprint_sf"] == pytest.approx(truth["footprint_sf"].value, rel=0.05)
    assert q["eave_height_ft"] == pytest.approx(20, rel=0.03)
    assert q["ridge_height_ft"] == pytest.approx(28, rel=0.03)
    assert q["wall_area_gross_sf"] == pytest.approx(q["perimeter_ft"] * q["eave_height_ft"])
    assert q["roof_area_sf"] == pytest.approx(truth["roof_area_sf"].value, rel=0.04)
    assert q["gutter_ft"] == pytest.approx(truth["gutter_ft"].value, rel=0.03)
    assert "gla_sf" not in q and result.cost_usd == 0 and result.seconds > 0
    # The 1.118 factor instead of the planes; the raw outline as the walls.
    factor = me.run_m1(house, tmp_path, me.M1Settings(roof="factor")).quantities
    assert factor["roof_area_sf"] == pytest.approx(q["roof_plan_sf"] * 1.118)
    raw = me.run_m1(house, tmp_path, me.M1Settings(overhang=0.0)).quantities
    # About 8 x the overhang (exactly 8 d on a rectangle; the cells add a few slanted corners).
    assert raw["perimeter_ft"] - q["perimeter_ft"] == pytest.approx(8 * 1.5, rel=0.1)
    drawn = me.run_m1(house, tmp_path, me.M1Settings(overhang="drawn")).quantities
    assert drawn["perimeter_ft"] == pytest.approx(q["perimeter_ft"], rel=0.01)  # 1.5 ft both


def test_m1_drafts_nothing_without_points_or_a_drawn_overhang(tmp_path: Path) -> None:
    house = me.synthetic_house()
    none = me.run_m1(house, tmp_path, me.M1Settings())
    assert none.quantities == {} and none.notes == {"refused": "no lidar points"}
    cache_house(tmp_path, house)
    bare = mt.House.model_validate(
        {**house.model_dump(mode="json", exclude_none=True), "roof": {"derive": False}}
    )
    assert me.run_m1(bare, tmp_path, me.M1Settings(overhang="drawn")).notes == {
        "refused": "no drawn overhang"
    }


def test_structures_footprint_under_the_point(tmp_path: Path) -> None:
    house = me.synthetic_house()
    cache_house(tmp_path, house)
    lon, lat = house.location.lon, house.location.lat
    k = math.cos(math.radians(lat))
    cx, cy = to_mercator(lon, lat)

    def poly(w: float, h: float, dx: float = 0.0) -> dict[str, Any]:
        ring = [
            (dx - w / 2, -h / 2),
            (dx + w / 2, -h / 2),
            (dx + w / 2, h / 2),
            (dx - w / 2, h / 2),
        ]
        coords = [list(from_mercator(cx + e / k, cy + n / k)) for e, n in ring]
        coords = coords[::-1] + [coords[-1]]  # clockwise, closed, as the layer may send it
        return {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {"SOURCE": "ORNL", "VAL_METHOD": "Manual"},
        }

    data = {"features": [poly(6, 6, dx=30.0), poly(13.106, 10.058)]}
    (tmp_path / house.id / "structures.json").write_text(json.dumps(data))
    ring, props = me.structures_ring(tmp_path, house) or ([], {})
    assert shell.ring_area(ring) == pytest.approx(13.106 * 10.058, rel=1e-4)
    assert props["SOURCE"] == "ORNL"
    q = me.run_m1(house, tmp_path, me.M1Settings(footprint="structures")).quantities
    assert q["perimeter_ft"] == pytest.approx(140, rel=0.01)  # 1.5 ft taken off each side
    (tmp_path / house.id / "structures.json").write_text(json.dumps({"features": [poly(6, 6, 30)]}))
    assert me.structures_ring(tmp_path, house) is None
    assert (
        "perimeter_ft"
        not in me.run_m1(house, tmp_path, me.M1Settings(footprint="structures")).quantities
    )


def test_paired_compares_on_the_same_houses_only() -> None:
    def score(method: str, house: str, signed: float) -> me.Score:
        return me.Score(method, house, "perimeter_ft", 100.0, "printed", 100 + signed, signed)

    houses = [me.synthetic_house()]
    a = houses[0]
    others = [
        mt.House.model_validate({**a.model_dump(mode="json", exclude_none=True), "id": i})
        for i in ("zz0002", "zz0003")
    ]
    scores = [
        score("M0", "zz0001", -20.0),
        score("M0", "zz0002", -10.0),
        score("M0", "zz0003", 5.0),
        score("M1", "zz0001", 4.0),
        score("M1", "zz0002", -2.0),
        # M1 drafted nothing on zz0003: that house is left out of the pair.
    ]
    (row,) = me.paired(scores, [a, *others], standing=None)
    assert row["n"] == 2 and row["houses"] == ["zz0001", "zz0002"]
    assert row["baseline_mean_abs_pct"] == pytest.approx(15.0)
    assert row["method_mean_abs_pct"] == pytest.approx(3.0)
    assert row["diff_mean_pct"] == pytest.approx(((4 - 20) + (2 - 10)) / 2)
    assert row["diff_sd_pct"] == pytest.approx(math.sqrt(32))
    assert row["diff_ci95_pct"] == pytest.approx(12.706 * math.sqrt(32) / math.sqrt(2))
    assert row["closer"] == 2
    # Standing: the synthetic houses are "unknown", so none is unchanged.
    assert me.paired(scores, [a, *others], standing="unchanged") == []


def test_the_m1_setups_and_the_dry_run_with_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    ids = [i for i, _, _ in me.M1_SETUPS]
    assert ids[0] == "M1" and len(set(ids)) == len(ids)
    assert me.M1_SETUPS[0][2] == me.M1Settings()  # the shipped defaults
    assert me.M1Settings().overhang == shell.OVERHANG_PRIOR_M
    out = tmp_path / "run"
    geo = tmp_path / "geo"
    cache_house(geo, me.synthetic_house())
    code = me.main(["--out", str(out), "--dry-run", "--points", str(geo)])
    assert code == 0
    result = json.loads((out / "measure_eval.json").read_text())
    methods = {s["method"] for s in result["scores"]}
    assert {"M0", "M1", "M1-outline-raw"} <= methods
    assert result["paired_all"] and not result["paired_unchanged"]
    text = (out / "measure_eval.md").read_text()
    assert "Paired against today's formulas" in text and "The lidar shell per house" in text


def test_the_lidar_summary_code_is_untouched_by_the_shell() -> None:
    # basis.geo.lidar's outputs are the summary's; the shell only reads its functions.
    assert lidar.EAVE_PERCENTILE == 5.0 and lidar.PEAK_PERCENTILE == 99.0
    assert shell.CELL_M == lidar.CELL_M
