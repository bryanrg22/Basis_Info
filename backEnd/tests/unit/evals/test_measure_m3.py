"""M3, facades from ground photos (EVAL_REPORT 109): the facade truths, the harness's
arithmetic, M1's side width, the opening matching, today's 15 % share, the vision-model answers
and their cost through the scripted provider. No paid call, no photo needed."""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import math
from pathlib import Path
from typing import Any

import anthropic
import pytest
from PIL import Image

from basis.config import Settings
from basis.geo import facade as fc
from basis.llm import prompts
from basis.llm.cost import cost_decimal
from basis.llm.provider import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.providers import strict_json_schema
from basis.llm.recorder import MemoryRecorder
from evals import measure_facade_truth as ft
from evals import measure_facades as mf
from evals import measure_vlm as mv

FT = 0.3048


def facade(**over: Any) -> ft.Facade:
    doc: dict[str, Any] = {
        "schema": "measure_facade/v1",
        "id": "zz0001-west",
        "house": "zz0001",
        "side": "west",
        "description": "a synthetic wall",
        "sheet": "1",
        "scale": '1/4" = 1\'-0"',
        "px_per_ft": 100,
        "length": {"ft": 40.0, "how": "printed", "text": "40'-0\"", "sheet": "1"},
        "eave": {"ft": 10.0, "how": "printed", "sheet": "1"},
        "openings": [
            {
                "id": "W1",
                "kind": "window",
                "storey": "first",
                "width_ft": 3,
                "height_ft": 5,
                "left_ft": 5,
                "sill_ft": 3,
            },
            {
                "id": "W2",
                "kind": "window",
                "storey": "first",
                "width_ft": 3,
                "height_ft": 5,
                "left_ft": 30,
                "sill_ft": 3,
            },
            {
                "id": "D1",
                "kind": "door",
                "storey": "first",
                "width_ft": 3.5,
                "height_ft": 7,
                "left_ft": 18,
                "sill_ft": 0.5,
            },
        ],
        "passes": {"a": "a", "b": "b"},
        "photos": [
            {
                "id": "p1",
                "path": "x/p1.jpg",
                "source": "synthetic",
                "licence": "none",
                "hfov_deg": 65.0,
                "which": "the wall",
                "width_px": 1024,
                "height_px": 768,
            },
        ],
    }
    doc.update(over)
    return ft.Facade.model_validate(doc)


# ------------------------------------------------------------------ the truth


def test_truths_follow_the_definitions() -> None:
    t = ft.truths(facade())
    assert t["gross_sf"].value == 400.0
    assert t["openings_sf"].value == pytest.approx(15 + 15 + 24.5)
    assert t["net_sf"].value == pytest.approx(400 - 54.5)
    assert t["trim_lf"].value == pytest.approx(16 + 16 + 21)
    assert (t["windows"].value, t["doors"].value) == (2, 1)
    assert t["gross_sf"].tier == "printed" and t["openings_sf"].tier == "scaled"


def test_check_finds_openings_outside_the_wall_and_duplicates() -> None:
    bad = facade(
        openings=[
            {
                "id": "W1",
                "kind": "window",
                "storey": "first",
                "width_ft": 3,
                "height_ft": 5,
                "left_ft": 39,
                "sill_ft": 3,
            },
            {
                "id": "W1",
                "kind": "window",
                "storey": "first",
                "width_ft": 3,
                "height_ft": 9,
                "left_ft": 5,
                "sill_ft": 3,
            },
        ]
    )
    codes = sorted(f.code for f in ft.check(bad))
    assert codes == ["duplicate_id", "outside", "outside"]
    assert ft.check(facade()) == []


def test_every_committed_facade_loads_and_passes_its_checks() -> None:
    facades = ft.load_all()
    assert len(facades) == 8
    for f in facades:
        assert ft.check(f) == [], f.id
        assert f.photos, f.id
        assert f.id in mf.AZIMUTH_DEG and f.house in mf.EAVE_WORDS
        assert f.passes.a != f.passes.b  # two readings


# ------------------------------------------------------------------ M1's side width


def test_m1_side_width_spans_the_facing_edges_and_their_cut_corner() -> None:
    # A 10 x 6 m box (east, north) whose south-west corner the outline cut at 45 degrees.
    ring = [(1.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0), (0.0, 1.0)]
    west = mf.m1_wall_length(ring, 270.0)
    assert west is not None
    assert west[0] == pytest.approx(6.0)  # the west edge (5 m) and the cut corner (1 m)
    north = mf.m1_wall_length(list(reversed(ring)), 0.0)  # orientation does not matter
    assert north is not None and north[0] == pytest.approx(10.0)
    assert mf.m1_wall_length(ring[:2], 0.0) is None


# ------------------------------------------------------------------ matching and scoring


def test_openings_match_by_centre_and_count_misses_and_extras() -> None:
    f = facade()
    got = mf.Measured(
        {"openings_sf": 40.0},
        openings=(
            ("window", 3.3, 5.0, 5.2, 3.0),  # W1, 10 % wide
            ("door", 3.5, 7.0, 18.0, 0.5),  # D1
            ("window", 2.0, 2.0, 12.0, 6.0),  # nothing there
        ),
        windows=2,
        doors=1,
    )
    scores, counts = mf.score("s", f, "p1", got)
    assert counts is not None
    assert (counts.matched, counts.missed, counts.extra) == (2, 1, 1)
    assert counts.windows == (2, 2) and counts.doors == (1, 1)
    assert sorted(counts.width_abs_pct) == pytest.approx([0.0, 10.0])
    assert scores[0].signed_pct == pytest.approx((40 / 54.5 - 1) * 100)


def test_todays_share_is_fifteen_percent_of_the_face() -> None:
    got = mf.formula(facade(), 9.0)
    assert got.quantities["gross_sf"] == 360.0
    assert got.quantities["openings_sf"] == pytest.approx(54.0)
    assert got.quantities["net_sf"] == pytest.approx(306.0)
    assert got.windows is None  # no counts


def test_direct_answer_sizes_and_a_blind_answer() -> None:
    answer = {
        "wall_visible": True,
        "eave_height_ft": 10.0,
        "openings": [
            {"kind": "window", "width_ft": 3, "height_ft": 5, "left_ft": 5, "sill_ft": 3},
            {"kind": "door", "width_ft": 3.5, "height_ft": 7, "left_ft": 18, "sill_ft": 0.5},
        ],
        "occluded_share": 0.1,
        "notes": "",
    }
    got = mf.measured_direct(answer, 40.0)
    assert got.quantities["openings_sf"] == pytest.approx(39.5)
    assert (got.windows, got.doors) == (1, 1)
    assert mf.measured_direct(answer | {"wall_visible": False}, 40.0).refused


# ------------------------------------------------------------------ the metrology on a draft


def photographed() -> tuple[ft.Facade, dict[str, Any]]:
    """The synthetic facade photographed by a pinhole camera 30 degrees off square."""
    f = facade()
    hfov, width, height = 65.0, 1024, 768
    focal = width / 2 / math.tan(math.radians(hfov) / 2)
    yaw, dist = math.radians(30), 16.0
    cx_w, cy_w = 20 * FT, 5 * FT
    eye = (cx_w + dist * math.sin(yaw), cy_w, dist * math.cos(yaw))
    fwd = (cx_w - eye[0], cy_w - eye[1], -eye[2])
    n = math.sqrt(sum(c * c for c in fwd))
    fwd = (fwd[0] / n, fwd[1] / n, fwd[2] / n)
    right = (fwd[2], 0.0, -fwd[0])
    rn = math.sqrt(right[0] ** 2 + right[2] ** 2)
    right = (right[0] / rn, 0.0, right[2] / rn)
    down = (0.0, -1.0, 0.0)

    def px(x_ft: float, y_ft: float) -> list[float]:
        p = (x_ft * FT - eye[0], y_ft * FT - eye[1], -eye[2])
        z = sum(a * b for a, b in zip(p, fwd, strict=True))
        u = sum(a * b for a, b in zip(p, right, strict=True))
        v = sum(a * b for a, b in zip(p, down, strict=True))
        return [focal * u / z + width / 2, focal * v / z + height / 2]

    def quad(left: float, sill: float, w: float, h: float) -> dict[str, list[float]]:
        return {
            "bl": px(left, sill),
            "br": px(left + w, sill),
            "tr": px(left + w, sill + h),
            "tl": px(left, sill + h),
        }

    clicks = {
        "visible": True,
        "wall": quad(0, 0, 40, 10),
        "openings": [
            {"kind": o.kind, **quad(o.left_ft, o.sill_ft, o.width_ft, o.height_ft)}
            for o in f.openings
        ],
        "occluded_share": 0.0,
    }
    return f, clicks


def test_metrology_on_a_photographed_facade_gives_the_truth_back() -> None:
    f, clicks = photographed()
    got = mf.measure_draft(f.photos[0], mf.draft_from_clicks(clicks), f.length.ft, "exif")
    t = ft.truths(f)
    for q in ("eave_height_ft", "gross_sf", "openings_sf", "net_sf", "trim_lf"):
        assert got.quantities[q] == pytest.approx(t[q].value, rel=1e-6), q
    scores, counts = mf.score("M3.engineer", f, "p1", got)
    assert counts is not None and (counts.missed, counts.extra) == (0, 0)
    assert all(abs(s.signed_pct) < 1e-4 for s in scores)
    # The same corners as a model's answer, in a picture half the size: scaled back.
    half = {
        "wall_visible": True,
        "wall": {k: {"x": x / 2, "y": y / 2} for k, (x, y) in clicks["wall"].items()},
        "openings": [
            {
                "kind": o["kind"],
                **{k: {"x": o[k][0] / 2, "y": o[k][1] / 2} for k in ("bl", "br", "tr", "tl")},
            }
            for o in clicks["openings"]
        ],
        "occluded_share": 0.0,
    }
    again = mf.measure_draft(f.photos[0], mf.draft_from_clicks(half, 2.0), f.length.ft, "exif")
    assert again.quantities["openings_sf"] == pytest.approx(t["openings_sf"].value, rel=1e-6)


def test_a_wall_not_placed_is_a_refusal_not_a_number() -> None:
    f, _ = photographed()
    got = mf.measure_draft(f.photos[0], mf.draft_from_clicks({"visible": False}), 40.0, "exif")
    assert got.refused and got.quantities == {}
    assert mf.score("s", f, "p1", got) == ([], None)


# ------------------------------------------------------------------ the vision model


@pytest.mark.parametrize("model", [mf.CornersAnswer, mf.DirectAnswer])
def test_answer_schemas_are_strict(model: type) -> None:
    strict = strict_json_schema(model)
    assert set(strict["required"]) == set(model.model_fields)
    assert anthropic.transform_schema(model)["type"] == "object"


def test_prompts_are_released() -> None:
    for name in mf.PROMPTS.values():
        assert prompts.load(name, mf.PROMPT_VERSION).sha256


def write_photo(root: Path) -> None:
    image = Image.new("RGB", (2048, 1536), (200, 200, 200))
    out = root / "measure_truth" / "photos" / "x"
    out.mkdir(parents=True)
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    (out / "p1.jpg").write_bytes(buf.getvalue())


def test_paid_answers_cost_their_own_ledger_rows_and_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(mf.EAVE_WORDS, "zz0001", "the eave line")
    write_photo(tmp_path)
    f, clicks = photographed()
    photo = f.photos[0].model_copy(update={"width_px": 2048, "height_px": 1536})
    f = f.model_copy(update={"photos": (photo,)})
    setup = mf.VlmSetup(mv.models_from_settings(Settings())["sol"], "corners")
    # The model answers in the prepared (1,024 px) picture's pixels: the clicks were made on
    # a 1,024 px frame, so they scale to the 2,048 px photo by 2.
    answer = mf.CornersAnswer.model_validate(
        {
            "wall_visible": True,
            "wall": {k: {"x": x, "y": y} for k, (x, y) in clicks["wall"].items()},
            "openings": [
                {
                    "kind": o["kind"],
                    **{k: {"x": o[k][0], "y": o[k][1]} for k in ("bl", "br", "tr", "tl")},
                }
                for o in clicks["openings"]
            ],
            "occluded_share": 0.0,
            "notes": "",
        }
    )
    usage = Usage(input_tokens=1800, output_tokens=400, reasoning_output_tokens=100)
    provider = ScriptedProvider([ScriptedTurn(parsed=answer, usage=usage)], model="gpt-6.1-sol")
    recorder = MemoryRecorder()
    budget = Budget(remaining_usd=1.0)
    ctx = mv.new_context(budget, recorder, activity="evals.measure_facades")
    jobs = mf.vlm_jobs([f], [setup], 1)
    assert jobs[0][3].scale == pytest.approx(2.0)
    outcomes = asyncio.run(mf.run_jobs(provider, ctx, recorder.records, jobs, concurrency=1))
    (o,) = outcomes
    assert o.cost_usd == pytest.approx(float(cost_decimal("gpt-6.1-sol", usage)))
    assert recorder.records[0].entry.prompt_version == "measure_facade_corners/v1"
    rows = mf.vlm_rows([f], outcomes, None)
    assert rows[0].cost_usd == o.cost_usd
    # The same view at twice the pixels and the same field of view: the truth comes back.
    t = ft.truths(f)
    got = rows[0].measured.quantities
    assert got["eave_height_ft"] == pytest.approx(t["eave_height_ft"].value, rel=1e-6)
    assert got["openings_sf"] == pytest.approx(t["openings_sf"].value, rel=1e-6)
    json.dumps([dataclasses.asdict(x) for x in outcomes])  # the outcomes file can be written
