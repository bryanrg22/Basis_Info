"""``evals.measure_eval`` (EVAL_REPORT 103): the scoring arithmetic, M0 as the production
functions compute it, the lidar first look's conversions, and the ``--dry-run`` run record (no
network, no model call, $0)."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

from basis.domain import measurements as ms
from evals import measure_eval as me
from evals import measure_truth as mt


def test_error_pct_is_signed_against_the_truth() -> None:
    assert me.error_pct(110, 100) == pytest.approx(10)
    assert me.error_pct(90, 100) == pytest.approx(-10)
    with pytest.raises(ValueError):
        me.error_pct(1, 0)


def test_m0_is_the_production_formulas_fed_gla_and_stories() -> None:
    house = me.synthetic_house()
    result = me.run_m0(house)
    q = result.quantities
    m = me.appraisal_inputs(house)
    assert m.gla_sf is not None and m.gla_sf.value == Decimal("2400.0")
    assert m.stories is not None and m.stories.value == Decimal("2")
    assert q["footprint_sf"] == pytest.approx(1200)
    short = math.sqrt(1200 / 1.5)
    assert q["perimeter_ft"] == pytest.approx(2 * short * 2.5)
    assert q["long_side_ft"] == pytest.approx(short * 1.5)
    roof = ms.roof_area(m)
    assert roof is not None and q["roof_area_sf"] == pytest.approx(float(roof.quantity))
    assert q["roof_plan_sf"] == pytest.approx(float(roof.quantity) / 1.118, rel=1e-4)
    siding = ms.siding_area(m)
    assert siding is not None
    assert q["wall_area_gross_sf"] == pytest.approx(float(siding.quantity) / 0.85)
    assert q["wall_area_gross_sf"] == pytest.approx(2 * short * 2.5 * 9 * 2, rel=1e-4)
    assert q["eave_height_ft"] == pytest.approx(18)
    gutter = ms.gutter_length(m)
    assert gutter is not None and q["gutter_ft"] == pytest.approx(float(gutter.quantity))
    assert result.cost_usd == 0 and result.input_tokens is None


def test_scores_cover_only_quantities_with_a_truth() -> None:
    house = me.synthetic_house()
    scores = me.score_house(me.M0, house, me.run_m0(house))
    got = {s.quantity for s in scores}
    assert "ridge_height_ft" not in got  # M0 has no ridge
    assert got <= set(mt.truths(house))
    foot = next(s for s in scores if s.quantity == "footprint_sf")
    assert foot.signed_pct == pytest.approx(0, abs=1e-9)


def test_summarize_gives_bias_spread_and_worst() -> None:
    scores = [
        me.Score("M0", "a", "perimeter_ft", 100, "printed", 90, -10.0),
        me.Score("M0", "b", "perimeter_ft", 100, "printed", 120, 20.0),
        me.Score("M0", "c", "perimeter_ft", 100, "printed", 100, 0.0),
    ]
    [row] = me.summarize(scores)
    assert row["n"] == 3
    assert row["mean_signed_pct"] == pytest.approx(10 / 3)
    assert row["sd_signed_pct"] == pytest.approx(15.275252, rel=1e-6)
    assert row["mean_abs_pct"] == pytest.approx(10)
    assert row["median_abs_pct"] == pytest.approx(10)
    assert row["worst_abs_pct"] == 20 and row["worst_house"] == "b"


def test_the_lidar_first_look_converts_and_compares(tmp_path: Path) -> None:
    house = me.synthetic_house()
    (tmp_path / house.id).mkdir()
    building = {
        "footprint_m2": 1419 * 0.3048**2,  # exactly the roof plan
        "eave_m": 20 * 0.3048,
        "peak_m": 28 * 0.3048,
        "reaches_box_edge": False,
        "distance_m": 0.0,
    }
    (tmp_path / house.id / "gather.json").write_text(
        json.dumps({"lidar": {"project": "P", "year": 2020, "building": building}})
    )
    [row] = me.lidar_rows([house], tmp_path)
    assert row["footprint_sf"] == pytest.approx(1419)
    assert row["vs_roof_plan_pct"] == pytest.approx(0, abs=1e-9)
    assert row["vs_walls_pct"] == pytest.approx((1419 - 1200) / 12)
    assert row["eave_vs_truth_pct"] == pytest.approx(0, abs=1e-9)
    assert row["peak_vs_ridge_pct"] == pytest.approx(0, abs=1e-9)
    [missing] = me.lidar_rows([house], tmp_path / "none")
    assert missing["status"] == "no gather"


def test_an_unknown_method_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    assert me.main(["--dry-run", "--out", str(tmp_path / "run"), "--methods", "M9"]) == 2


def test_the_dry_run_leaves_its_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    out = tmp_path / "run"
    assert me.main(["--dry-run", "--out", str(out)]) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["tree"]["component"] == "measure_eval"
    assert manifest["tree"]["version"] == "v1"
    results = json.loads((out / "results.json").read_text())
    assert results["summary"]["passed"] == 2 and results["summary"]["failed"] == 0
    body = json.loads((out / "measure_eval.json").read_text())
    assert body["houses"] == ["zz0001"]
    assert sum(c["cost_usd"] for c in body["costs"]) == 0
    assert "Today's formulas" in (out / "measure_eval.md").read_text()


def test_the_committed_houses_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    out = tmp_path / "run"
    assert me.main(["--out", str(out)]) == 0
    body = json.loads((out / "measure_eval.json").read_text())
    assert len(body["houses"]) == 10
    assert {s["quantity"] for s in body["scores"]} >= {"footprint_sf", "perimeter_ft"}
