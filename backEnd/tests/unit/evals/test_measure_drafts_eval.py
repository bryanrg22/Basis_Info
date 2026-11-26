"""The EVAL_REPORT 110 harnesses on synthetic data: ``evals.measure_drafts_replay`` (the
cabinet-run rule on an export priced before it, the exterior and interior drafts applied as
``process_assets`` applies them, the exposure sweep, which drafts can move class dollars) and
``evals.measure_drafts_rooms`` (the rooms measured once by name, type and photos, only the
measured types). No network, no paid call, no client file."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from basis.domain import measure_drafts as md
from basis.geo import shell
from evals import measure_drafts_replay as mr
from evals import measure_drafts_rooms as rooms_run
from tests.unit.geo.test_shell import cloud, derive

D = Decimal


def asset(id_: str, label: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_,
        "label": label,
        "bucket": "y5",
        "quantity": "100.0000",
        "unit": "SF",
        "unit_cost": "2.00",
        "total": "200.00",
        "review_reason": None,
        "room_id": "r-kitchen",
        "formula": "today",
        "takeoff_inputs": {"component": label, "method": "area", "room_area_source": "gla_share"},
    }
    return {**base, **kw}


def test_the_cabinet_rule_holds_a_perimeter_countertop_only() -> None:
    doc = {
        "assets": [
            asset(
                "a1",
                "countertop",
                takeoff_inputs={"component": "countertop", "method": "perimeter"},
            ),
            asset(
                "a2",
                "countertop",
                takeoff_inputs={"component": "countertop", "measured_with_asset": "a1"},
            ),
            asset(
                "a3",
                "countertop",
                takeoff_inputs={"component": "countertop", "quantity_source": "engineer"},
            ),
            asset("a4", "flooring"),
        ]
    }
    assert mr.cabinet_rule(doc) == 1
    held = doc["assets"][0]
    assert held["total"] is None and held["quantity"] == "0.0000"
    assert held["takeoff_inputs"]["missing_input"] == "cabinet_run"
    assert held["review_reason"] == "input:cabinet_run"
    assert [a["total"] for a in doc["assets"][1:]] == ["200.00", "200.00", "200.00"]


def test_the_exterior_drafts_replace_the_shell_rows_once() -> None:
    record = shell.as_record(derive(cloud(), overhang_m=0.5))
    expected, _ = md.exterior_drafts(md.ShellSource("g", None, None, record), {}, {})
    roof = {"component": "roof", "quantity_source": "appraisal_derived", "method": "area"}
    doc = {
        "assets": [
            asset("r1", "roof", bucket="y39", takeoff_inputs=roof),
            asset("r2", "roof", bucket="y39", takeoff_inputs={**roof, "measured_with_asset": "r1"}),
            asset("s1", "siding", bucket="y39", takeoff_inputs={**roof, "component": "siding"}),
        ]
    }
    rows = mr.apply_exterior(doc, md.ShellSource("g", None, None, record))
    assert {r.label for r in rows} == {"roof", "siding"}
    first, second, siding = doc["assets"]
    assert D(first["quantity"]) == expected["roof"].quantity
    assert first["total"] == str((D("2.00") * expected["roof"].quantity).quantize(D("0.01")))
    assert first["takeoff_inputs"][md.DRAFT]["drawn_by"] == md.SHELL_DRAWN_BY
    assert md.DRAFT_UNVERIFIED in first["review_reason"]
    assert second["quantity"] == "100.0000"  # measured once: left as stored
    assert D(siding["quantity"]) == expected["siding"].quantity
    # A building row inside the square-foot model moves no class dollars (EVAL_REPORT 87.1).
    assert not any(r.moves_classes() for r in rows)


def test_the_interior_drafts_size_the_measured_rooms_rows(tmp_path: Path) -> None:
    rooms_api = [
        {
            "name": "Kitchen",
            "room_type": "kitchen",
            "photos": [
                {"document_id": "d1", "filename": "k1.jpg"},
                {"document_id": "d2", "filename": "k2.jpg"},
            ],
        }
    ]
    doc = {
        "rooms": [{"id": "r-kitchen", "name": "Kitchen", "room_type": "kitchen"}],
        "photos": [
            {"document_id": "d1", "room_id": "r-kitchen"},
            {"document_id": "d2", "room_id": "r-kitchen"},
        ],
        "assets": [
            asset("f1", "flooring"),
            asset(
                "c1",
                "countertop",
                quantity="0.0000",
                total=None,
                unit_cost=None,
                review_reason="input:cabinet_run",
                takeoff_inputs={
                    "component": "countertop",
                    "method": "perimeter",
                    "missing_input": "cabinet_run",
                },
            ),
            asset(
                "l1",
                "light_fixture",
                unit="EA",
                takeoff_inputs={"component": "light_fixture", "method": "count"},
            ),
        ],
    }
    key = rooms_run.room_key("Kitchen", "kitchen", ["k1.jpg", "k2.jpg"])
    assert mr.room_keys(doc, rooms_api) == {"r-kitchen": key}
    answers = {
        key: {
            "status": "drafted",
            "model": "claude-sonnet-5-5",
            "photos": ["p1", "p2"],
            "quantities": {"floor_area_sf": 180.0, "countertop_sf": 28.0},
        }
    }
    rows, rooms = mr.apply_interior(doc, answers, rooms_api)
    assert rooms == 1 and {r.label for r in rows} == {"flooring", "countertop"}
    floor, counter, light = doc["assets"]
    assert floor["quantity"] == "180.0000" and floor["total"] == "360.00"
    assert counter["quantity"] == "28.0000" and counter["total"] == "0"  # priced by the reprice
    assert "input:cabinet_run" not in counter["review_reason"]
    assert "missing_input" not in counter["takeoff_inputs"]
    assert light["quantity"] == "100.0000"
    by = {r.label: r for r in rows}
    assert by["countertop"].replaced_missing == "cabinet_run"
    assert by["countertop"].error_pct == D("20.8") and by["flooring"].error_pct == D("22.5")


def test_the_sweep_and_the_class_moving_view() -> None:
    rows = [
        mr.DraftRow(
            "1", "roof", None, "y39", "exterior", "1", "2", "SF", D("10.4"), None, D("10000")
        ),
        mr.DraftRow(
            "2", "flooring", "K", "y5", "interior", "1", "2", "SF", D("22.5"), None, D("1000")
        ),
        mr.DraftRow(
            "3", "baseboard", "K", "y5", "interior", "1", "2", "LF", D("10.3"), None, D("100")
        ),
    ]
    swept = mr.sweep_rows(rows, [0, 200, 1000])
    assert [s["held"] for s in swept] == [3, 2, 1]  # exposures 1040, 225, 10.30
    assert swept[1]["exposure_held_share_pct"] == round(1265 / 1275.3 * 100, 1)
    moving = mr.sweep_rows(rows, [0, 250])
    assert moving[0]["drafts"] == 3
    only = mr.sweep_rows(rows, [0], moving_only=True)
    assert only[0]["drafts"] == 2  # the roof is a building row inside the model


def test_the_rooms_run_measures_each_room_once_and_only_measured_types(tmp_path: Path) -> None:
    one = [
        {"name": "Kitchen", "room_type": "kitchen", "photos": [{"filename": "k1.jpg"}]},
        {"name": "Hall", "room_type": "hallway", "photos": [{"filename": "h.jpg"}]},
        {"name": "Den", "room_type": "family_room", "photos": [{"filename": "d.jpg"}]},
        {"name": "Empty", "room_type": "bedroom", "photos": []},
    ]
    two = [{"name": "Kitchen", "room_type": "kitchen", "photos": [{"filename": "k1.jpg"}]}]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(one))
    b.write_text(json.dumps(two))
    got = rooms_run.rooms_of([a, b])
    assert [k.split("|")[0] for k, _ in got] == ["Den", "Kitchen"]
    assert got[1][1].room_type == "kitchen" and got[1][1].uris == ("k1.jpg",)
