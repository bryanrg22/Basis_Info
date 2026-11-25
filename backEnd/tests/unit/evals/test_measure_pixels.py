"""``evals.measure_pixels`` (EVAL_REPORT 103): the implied pixel size, the ratio to the recorded
one, the perspective correction of a raised edge, and the per-house and per-view summaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import measure_pixels as mp


def view(kind: str, segments: list[dict[str, object]], **kw: object) -> dict[str, object]:
    return {"house": "h1", "kind": kind, "pixel_size_m": 0.1, "segments": segments, **kw}


def seg(px: float, truth_ft: float, **kw: object) -> dict[str, object]:
    return {"what": "edge", "truth_ft": truth_ft, "p1": [0, 0], "p2": [px, 0], **kw}


def test_a_length_at_the_recorded_scale_has_ratio_one() -> None:
    row = mp.score_segment(view("public_ortho", []), seg(100, 10 / 0.3048))  # 10 m over 100 px
    assert row["px"] == pytest.approx(100)
    assert row["implied_m_per_px"] == pytest.approx(0.1)
    assert row["ratio"] == pytest.approx(1)
    assert row["traced_error_pct"] == pytest.approx(0, abs=1e-9)
    assert "corrected_ratio" not in row


def test_a_length_shown_larger_reads_long() -> None:
    row = mp.score_segment(view("public_ortho", []), seg(105, 10 / 0.3048))
    assert row["ratio"] == pytest.approx(100 / 105)
    assert row["traced_error_pct"] == pytest.approx(5)


def test_the_diagonal_counts() -> None:
    s = {"what": "d", "truth_ft": 1.0, "p1": [1, 1], "p2": [4, 5]}
    assert mp.segment_px(s) == pytest.approx(5)


def test_a_raised_edge_is_corrected_for_perspective() -> None:
    # 160 m range, an edge 4 m up appears 160 / 156 larger: a ground-true pixel size gives a raw
    # ratio of 156 / 160 and a corrected ratio of 1.
    px = 100 * 160 / 156
    row = mp.score_segment(
        view("google_close", [], range_m=160.0), seg(px, 10 / 0.3048, edge_height_ft=4 / 0.3048)
    )
    assert mp.perspective_factor(160, 4) == pytest.approx(156 / 160)
    assert row["ratio"] == pytest.approx(156 / 160)
    assert row["corrected_ratio"] == pytest.approx(1)


@pytest.mark.parametrize(("rng", "h"), [(0, 1), (10, 10), (10, 11)])
def test_an_edge_at_or_above_the_camera_is_refused(rng: float, h: float) -> None:
    with pytest.raises(ValueError):
        mp.perspective_factor(rng, h)


def test_a_zero_length_segment_is_refused() -> None:
    with pytest.raises(ValueError):
        mp.score_segment(view("public_ortho", []), seg(0, 10))


def test_the_summary_averages_houses_then_views(tmp_path: Path) -> None:
    doc = {
        "method": "test",
        "views": [
            view("public_ortho", [seg(100, 10 / 0.3048), seg(110, 10 / 0.3048)]),
            {**view("public_ortho", [seg(90, 10 / 0.3048)]), "house": "h2"},
        ],
    }
    result = mp.run(doc)
    k = result["summary"]["public_ortho"]
    assert k["houses"] == 2 and k["segments"] == 3
    h1 = k["per_house"]["h1"]["ratio"]["mean"]
    assert h1 == pytest.approx((1 + 100 / 110) / 2)
    assert k["ratio_over_house_means"]["mean"] == pytest.approx((h1 + 100 / 90) / 2)
    points = tmp_path / "p.json"
    points.write_text(json.dumps(doc))
    assert mp.main([str(points), "--out", str(tmp_path / "r.json")]) == 0
    assert json.loads((tmp_path / "r.json").read_text())["summary"]["public_ortho"]["houses"] == 2
