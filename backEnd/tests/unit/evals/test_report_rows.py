"""``report_rows``: the engineer's report checklist, its loader and scorer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals import datasets as ds
from evals.run import (
    evaluate,
    gold_materials,
    load_room_materials,
    main,
    materials_for_repeats,
    render_report,
    row_outcome,
    score_report_rows,
)


def rows(*items: dict[str, object]) -> ds.ReportRows:
    """A dataset with equal cost shares unless an item sets its own."""
    given = sum(float(i.get("cost_share", 0.0)) for i in items)
    free = [i for i in items if "cost_share" not in i]
    share = (1.0 - given) / len(free) if free else 0.0
    return ds.ReportRows.model_validate(
        {
            "rows": [
                {"document": "package", "cost_share": share} | i if "cost_share" not in i else i
                for i in items
            ]
        }
    )


def inventory() -> ds.RoomInventorySet:
    return ds.RoomInventorySet.model_validate(
        {
            "rooms": [
                {
                    "room_id": "1_kitchen",
                    "room_type": "kitchen",
                    "photos": ["21_1st Floor Kitchen photo 1.JPG"],
                    "objects": [{"label": "kitchen_cabinet"}, {"label": "sink"}, {"label": "tile"}],
                },
                {
                    "room_id": "2_bath",
                    "room_type": "bathroom",
                    "photos": ["30_2nd Floor Bath.JPG"],
                    "objects": [
                        {"label": "kitchen_cabinet", "note": "a vanity, labelled loosely"},
                        {"label": "sink"},
                        {"label": "toilet"},
                        {"label": "other", "count": 2, "note": "towel bars"},
                    ],
                },
                {
                    "room_id": "x_property_front",
                    "room_type": "exterior",
                    "photos": ["01_Front.JPG"],
                    "objects": [{"label": "roof"}, {"label": "window", "count": 12}],
                    "document": "other_package",
                },
            ]
        }
    )


FULL = [
    # kind A, any room, count required
    {
        "id": "windows",
        "system": "Windows",
        "bucket": "y39",
        "kind": "A",
        "labels": ["window"],
        "count_required": True,
    },
    # kind A with a room filter: kitchen millwork excludes bathrooms
    {
        "id": "millwork",
        "system": "Cabinets / Millwork",
        "bucket": "y5",
        "kind": "A",
        "labels": ["kitchen_cabinet"],
        "exclude_room_types": ["bathroom"],
        "count_required": True,
    },
    # kind A restricted to bathrooms
    {
        "id": "restroom",
        "system": "Cabinets / Millwork - Restrooms",
        "bucket": "y39",
        "kind": "A",
        "labels": ["bathroom_vanity", "kitchen_cabinet"],
        "room_types": ["bathroom"],
        "count_required": True,
    },
    # kind B with a material constraint and a material requirement
    {
        "id": "tile",
        "system": "Ceramic Tile Flooring",
        "bucket": "y39",
        "kind": "B",
        "labels": ["tile"],
        "materials": {"tile": ["ceramic", "porcelain"]},
        "material_required": True,
    },
    # kind A, material required but unconstrained
    {
        "id": "roof",
        "system": "Roofing Systems",
        "bucket": "y39",
        "kind": "A",
        "labels": ["roof"],
        "material_required": True,
    },
    # a miss: nothing in the inventory
    {
        "id": "fence",
        "system": "Exterior Fencing",
        "bucket": "y15",
        "kind": "A",
        "labels": ["fence"],
    },
    # a vocabulary gap and a kind C row
    {"id": "shed", "system": "Movable Shed", "bucket": "y7", "kind": "A", "missing_label": "shed"},
    {"id": "foundation", "system": "Foundations", "bucket": "y39", "kind": "C"},
]
PREDICTED = {
    "1_kitchen": {"kitchen_cabinet": 4, "sink": 1, "tile": 1, "other": 3},
    "2_bath": {"Kitchen Cabinet": 1, "toilet": 1},
    "x_property_front": {"roof": 1, "window": 10},
}
MATERIALS = {
    "1_kitchen": {"tile": ["ceramic"], "kitchen_cabinet": ["wood"]},
    "x_property_front": {"roof": ["unknown"]},
}


# --------------------------------------------------------------------------- loader


def test_loader_reads_and_derives_kinds(tmp_path: Path) -> None:
    payload = {"schema_version": 1, "rows": [dict(r, cost_share=1 / len(FULL)) for r in FULL]}
    (tmp_path / ds.DATASET_FILES["report_rows"]).write_text(json.dumps(payload))
    data = ds.load("report_rows", tmp_path)
    assert isinstance(data, ds.ReportRows) and len(data.rows) == 8
    by_id = {r.id: r for r in data.rows}
    assert by_id["windows"].photo_derivable and not by_id["windows"].vocabulary_gap
    assert by_id["shed"].photo_derivable and by_id["shed"].vocabulary_gap
    assert not by_id["foundation"].photo_derivable and not by_id["foundation"].vocabulary_gap
    assert by_id["windows"].split == "dev" and by_id["windows"].document == "package"
    assert "report_rows" in ds.load_available(tmp_path)


def test_room_filters() -> None:
    [row] = rows({"id": "r", "system": "s", "bucket": "y5", "kind": "A", "labels": ["sink"]}).rows
    assert row.room_allowed("kitchen") and row.room_allowed(None)
    [row] = rows(
        {
            "id": "r",
            "system": "s",
            "bucket": "y5",
            "kind": "A",
            "labels": ["sink"],
            "room_types": ["kitchen", "laundry"],
        }
    ).rows
    assert row.room_allowed("laundry") and not row.room_allowed("bathroom")
    assert not row.room_allowed(None)  # a room of unknown type cannot pass a filter
    [row] = rows(
        {
            "id": "r",
            "system": "s",
            "bucket": "y5",
            "kind": "A",
            "labels": ["sink"],
            "exclude_room_types": ["bathroom"],
        }
    ).rows
    assert row.room_allowed("kitchen") and not row.room_allowed("bathroom")
    assert not row.room_allowed(None)


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"kind": "A", "labels": ["lamp"]}, "not a component label"),
        ({"kind": "A", "labels": ["other"]}, "not a component label"),
        ({"kind": "A", "labels": ["sink", "sink"]}, "labels repeat"),
        ({"kind": "A"}, "needs labels or a missing_label"),
        (
            {"kind": "A", "labels": ["sink"], "missing_label": "x"},
            "needs labels or a missing_label",
        ),
        ({"kind": "C", "labels": ["sink"]}, "kind C row has no evidence rule"),
        ({"kind": "C", "count_required": True}, "kind C row has no evidence rule"),
        ({"kind": "B", "labels": ["tile"], "materials": {"roof": ["metal"]}}, "not in the rule"),
        (
            {"kind": "B", "labels": ["tile"], "materials": {"tile": ["Ceramic Tile"]}},
            "bad material",
        ),
        ({"kind": "B", "labels": ["tile"], "materials": {"tile": []}}, "bad material"),
        ({"kind": "A", "labels": ["sink"], "room_types": []}, "null or non-empty"),
        ({"kind": "A", "labels": ["sink"], "room_types": ["ballroom"]}, "not a room type"),
        (
            {
                "kind": "A",
                "labels": ["sink"],
                "room_types": ["kitchen"],
                "exclude_room_types": ["kitchen"],
            },
            "both required and excluded",
        ),
        ({"kind": "D", "labels": ["sink"]}, "kind"),
        ({"kind": "A", "labels": ["sink"], "bucket": "y10"}, "bucket"),
        ({"kind": "A", "labels": ["sink"], "cost_share": 1.5}, "cost_share"),
    ],
)
def test_malformed_rows_are_rejected(item: dict[str, object], message: str) -> None:
    base = {"id": "r", "system": "s", "bucket": "y5", "cost_share": 1.0}
    with pytest.raises(ValidationError, match=message):
        ds.ReportRows.model_validate({"rows": [base | item]})


def test_set_level_validation() -> None:
    row = {"id": "r", "system": "s", "bucket": "y5", "kind": "C", "cost_share": 0.5}
    with pytest.raises(ValidationError, match="appears twice"):
        ds.ReportRows.model_validate({"rows": [row, row]})
    with pytest.raises(ValidationError, match="sum to 0.5000"):
        ds.ReportRows.model_validate({"rows": [row]})
    assert ds.ReportRows.model_validate({"rows": []}).rows == []  # empty is allowed
    ds.ReportRows.model_validate({"rows": [row, row | {"id": "q", "cost_share": 0.495}]})  # 0.995


def test_synthetic_report_rows_cover_the_synthetic_assets() -> None:
    data = ds.synthetic_smoke()["report_rows"]
    assert isinstance(data, ds.ReportRows)
    assets = ds.synthetic_smoke()["assets"]
    assert isinstance(assets, ds.Assets)
    assert [r.labels for r in data.rows[:6]] == [[a.component] for a in assets.assets]
    assert [r.bucket for r in data.rows[:6]] == ["y5", "y5", "y27_5", "y27_5", "y27_5", "y27_5"]
    assert [(r.kind, r.missing_label) for r in data.rows[6:]] == [("A", "shed"), ("C", None)]
    assert sum(r.cost_share for r in data.rows) == pytest.approx(1.0)


# --------------------------------------------------------------------------- outcomes


def test_every_outcome_class_and_the_evidence_behind_it() -> None:
    data = rows(*FULL)
    result = score_report_rows(data, "dev", 1, PREDICTED, inventory(), MATERIALS)
    assert result.system == "predictions" and not result.skipped
    detail = {r["id"]: r for r in result.rows[0].details["rows"]}
    assert {k: v["outcome"] for k, v in detail.items()} == {
        "windows": "evidenced",
        "millwork": "evidenced",
        "restroom": "evidenced",
        "tile": "evidenced",
        "roof": "evidenced",
        "fence": "missed",
        "shed": "vocabulary_gap",
        "foundation": "not_photo_derivable",
    }
    # the kitchen cabinets evidence the kitchen row, the bathroom's (normalized label)
    # evidence the restroom row, and neither crosses over
    assert [(e["room"], e["count"]) for e in detail["millwork"]["evidence"]] == [("1_kitchen", 4)]
    assert [(e["room"], e["count"]) for e in detail["restroom"]["evidence"]] == [("2_bath", 1)]
    assert detail["windows"]["evidence"] == [
        {"room": "x_property_front", "label": "window", "count": 10, "materials": []}
    ]
    assert detail["tile"]["evidence"][0]["materials"] == ["ceramic"]
    assert detail["tile"]["material_present"] is True
    assert detail["roof"]["material_present"] is False  # "unknown" is not a material
    assert detail["windows"]["material_present"] is None  # not required
    assert detail["windows"]["count_present"] is True
    assert detail["shed"]["missing_label"] == "shed"
    metrics = {r.metric: r for r in result.rows}
    # 7 photo-derivable rows, 5 evidenced (the gap counts against the product number)
    assert metrics["row_coverage"].value == pytest.approx(5 / 7)
    assert metrics["row_coverage"].n == 7 and metrics["row_coverage"].interval is not None
    assert metrics["row_coverage_in_vocabulary"].value == pytest.approx(5 / 6)
    assert metrics["count_present_rate"].value == pytest.approx(1.0)  # windows, millwork, restroom
    assert metrics["count_present_rate"].n == 3
    assert metrics["material_present_rate"].value == pytest.approx(1 / 2)  # tile yes, roof no
    assert metrics["vocabulary_gap_rate"].value == pytest.approx(1 / 7)
    assert metrics["not_photo_derivable_rate"].value == pytest.approx(1 / 8)
    assert "pass^1" not in metrics
    assert any("missed rows: Exterior Fencing" in n for n in result.notes)
    assert any("Movable Shed (shed)" in n for n in result.notes)
    assert any("not photo-derivable: Foundations" in n for n in result.notes)
    [(title, headers, body)] = result.tables
    assert title == "Rows" and len(body) == 8 and headers[0] == "row"
    assert body[6][3] == "vocabulary_gap (needs `shed`)"
    assert body[0][6] == "" and detail["windows"]["repeats_evidenced"] == 1  # one repeat


def test_a_predicted_room_without_a_label_still_evidences_rows_without_a_room_filter() -> None:
    """x_ghost is in the predictions but not in the labels: a row with no room filter is
    evidenced by it (we score what the pipeline produced), a filtered row is not (the
    room's type is unknown)."""
    data = rows(
        {
            "id": "fence",
            "system": "Exterior Fencing",
            "bucket": "y15",
            "kind": "A",
            "labels": ["fence"],
        },
        {
            "id": "fence_x",
            "system": "Exterior Fencing (exterior rooms)",
            "bucket": "y15",
            "kind": "A",
            "labels": ["fence"],
            "room_types": ["exterior"],
        },
    )
    outcome = {
        r["id"]: r["outcome"]
        for r in score_report_rows(data, "dev", 1, {"x_ghost": {"fence": 1}}, inventory(), None)
        .rows[0]
        .details["rows"]
    }
    assert outcome == {"fence": "evidenced", "fence_x": "missed"}


def test_material_constraints() -> None:
    row = rows(
        {
            "id": "tile",
            "system": "Ceramic Tile Flooring",
            "bucket": "y39",
            "kind": "B",
            "labels": ["tile"],
            "materials": {"tile": ["ceramic", "porcelain"]},
            "material_required": True,
        }
    ).rows[0]
    predicted = {"1_kitchen": {"tile": 1}}
    # no material source at all: a constrained label cannot evidence the row
    assert row_outcome(row, predicted, None, None).outcome == "missed"
    # the wrong material
    assert row_outcome(row, predicted, None, {"1_kitchen": {"tile": ["vinyl"]}}).outcome == "missed"
    # one right material among several reported
    out = row_outcome(row, predicted, None, {"1_kitchen": {"tile": ["vinyl", "porcelain"]}})
    assert out.outcome == "evidenced" and out.material_present
    # an unconstrained label with material required: evidenced, material absent
    roof = rows(
        {
            "id": "roof",
            "system": "Roofing Systems",
            "bucket": "y39",
            "kind": "A",
            "labels": ["roof"],
            "material_required": True,
        }
    ).rows[0]
    out = row_outcome(roof, {"x": {"roof": 1}}, None, None)
    assert out.outcome == "evidenced" and not out.material_present


def test_count_zero_or_negative_is_no_evidence() -> None:
    row = rows(
        {
            "id": "w",
            "system": "Windows",
            "bucket": "y39",
            "kind": "A",
            "labels": ["window"],
            "count_required": True,
        }
    ).rows[0]
    assert row_outcome(row, {"r": {"window": 0}}, None, None).outcome == "missed"
    assert row_outcome(row, {"r": {"window": 2}}, None, None).count_present


def test_without_room_types_filtered_rows_cannot_be_evidenced() -> None:
    data = rows(*FULL)
    result = score_report_rows(data, "dev", 1, PREDICTED, None, MATERIALS)
    outcome = {r["id"]: r["outcome"] for r in result.rows[0].details["rows"]}
    assert outcome["millwork"] == "missed" and outcome["restroom"] == "missed"
    assert outcome["windows"] == "evidenced"
    assert any("2 rows with a room-type filter cannot be evidenced" in n for n in result.notes)


# --------------------------------------------------------------------------- weighting


def test_cost_weighting_and_per_bucket_coverage() -> None:
    data = rows(
        {
            "id": "a",
            "system": "A",
            "bucket": "y5",
            "kind": "A",
            "labels": ["sink"],
            "cost_share": 0.10,
        },
        {
            "id": "b",
            "system": "B",
            "bucket": "y5",
            "kind": "A",
            "labels": ["fence"],
            "cost_share": 0.30,
        },
        {
            "id": "c",
            "system": "C",
            "bucket": "y39",
            "kind": "A",
            "labels": ["roof"],
            "cost_share": 0.20,
        },
        {
            "id": "d",
            "system": "D",
            "bucket": "y7",
            "kind": "A",
            "missing_label": "shed",
            "cost_share": 0.05,
        },
        {"id": "e", "system": "E", "bucket": "y39", "kind": "C", "cost_share": 0.35},
    )
    predicted = {"k": {"sink": 1}, "x": {"roof": 1}}
    metrics = {r.metric: r for r in score_report_rows(data, "dev", 1, predicted, None, None).rows}
    # photo-derivable: a, b, c, d (share 0.65); evidenced: a, c (share 0.30)
    assert metrics["row_coverage"].value == pytest.approx(2 / 4)
    assert metrics["cost_coverage"].value == pytest.approx(0.30 / 0.65)
    assert metrics["cost_coverage_of_study"].value == pytest.approx(0.30)
    assert metrics["cost_coverage"].details == pytest.approx(
        {
            "evidenced_share": 0.30,
            "missed_share": 0.30,
            "vocabulary_gap_share": 0.05,
            "not_photo_derivable_share": 0.35,
            "photo_derivable_share": 0.65,
        }
    )
    assert metrics["coverage:y5"].value == pytest.approx(1 / 2) and metrics["coverage:y5"].n == 2
    assert metrics["cost_coverage:y5"].value == pytest.approx(0.10 / 0.40)
    assert metrics["coverage:y7"].value == 0.0  # the gap is in the denominator
    assert metrics["coverage:y39"].value == 1.0 and metrics["coverage:y39"].n == 1  # e excluded
    assert metrics["coverage:y39"].details["rows_not_photo_derivable"] == 1
    assert "coverage:y15" not in metrics and "coverage:y27_5" not in metrics
    # n/a rates when nothing requires a count or a material
    assert metrics["count_present_rate"].n == 0


# --------------------------------------------------------------------------- repeats


def test_repeats_give_stable_coverage() -> None:
    data = rows(
        {"id": "w", "system": "Windows", "bucket": "y39", "kind": "A", "labels": ["window"]},
        {"id": "f", "system": "Fence", "bucket": "y15", "kind": "A", "labels": ["fence"]},
        {"id": "r", "system": "Roof", "bucket": "y39", "kind": "A", "labels": ["roof"]},
    )
    saved = {
        "x": {"repeats": [{"window": 3, "roof": 1}, {"window": 2}, {"window": 4, "roof": 1}]},
        "y": {"fence": 1},  # a plain value counts for every repeat
    }
    result = score_report_rows(data, "dev", 3, saved, None, None)
    metrics = {r.metric: r.value for r in result.rows}
    assert metrics["row_coverage"] == 1.0  # repeat 1 evidences all three
    assert [r["repeats_evidenced"] for r in result.rows[0].details["rows"]] == [3, 3, 2]
    assert any("some repeats only: Roof (2/3)" in n for n in result.notes)
    assert [r[6] for r in result.tables[0][2]] == ["3/3", "3/3", "2/3"]
    # stable: windows and fence pass all 3 repeats, roof passes 2 of 3 -> C(2,3)/C(3,3) = 0
    assert metrics["pass^3"] == pytest.approx(2 / 3)
    assert metrics["pass^1"] == pytest.approx((1 + 1 + 2 / 3) / 3)


# --------------------------------------------------------------------------- materials


def test_room_materials_come_from_the_raw_records_or_the_heuristic_objects(tmp_path: Path) -> None:
    assert load_room_materials(None, "room_inventory.json") is None
    assert load_room_materials(tmp_path, "room_inventory.json") is None  # no files yet
    raw = [
        {
            "room_id": "1_kitchen",
            "repeat": 1,
            "objects": [
                {"label": "tile", "material": "ceramic"},
                {"label": "Tile", "material": None},
                {"label": "other", "material": "wood"},
            ],
        },
        {"room_id": "1_kitchen", "repeat": 2, "objects": [{"label": "tile", "material": "vinyl"}]},
        {"room_id": "2_bath", "repeat": 1, "objects": []},
    ]
    (tmp_path / "room_raw.jsonl").write_text("\n".join(json.dumps(r) for r in raw) + "\n\n")
    (tmp_path / "rooms.json").write_text(
        json.dumps(
            [
                {
                    "room_id": "1_kitchen",
                    "heuristic_objects": [{"label": "roof", "material": "metal"}],
                }
            ]
        )
    )
    # one map per repeat, in repeat order; labels normalized; `other` dropped; a null
    # material is "unknown"
    assert load_room_materials(tmp_path, "room_inventory.json") == [
        {"1_kitchen": {"tile": ["ceramic", "unknown"]}},
        {"1_kitchen": {"tile": ["vinyl"]}},
    ]
    assert load_room_materials(tmp_path, "room_inventory.heuristic.json") == {
        "1_kitchen": {"roof": ["metal"]}
    }
    assert load_room_materials(tmp_path, "something_else.json") is None


def test_materials_follow_the_repeats() -> None:
    """A material-constrained row is evidenced per repeat from that repeat's materials, so
    pass^k measures the model, not the material source."""
    data = rows(
        {
            "id": "tile",
            "system": "Ceramic Tile Flooring",
            "bucket": "y39",
            "kind": "B",
            "labels": ["tile"],
            "materials": {"tile": ["ceramic"]},
            "material_required": True,
        }
    )
    saved = {"k": {"repeats": [{"tile": 1}, {"tile": 1}, {"tile": 1}]}}
    per_repeat = [
        {"k": {"tile": ["ceramic"]}},
        {"k": {"tile": ["vinyl"]}},
        {"k": {"tile": ["ceramic"]}},
    ]
    result = score_report_rows(data, "dev", 3, saved, None, per_repeat)
    assert result.rows[0].details["rows"][0]["repeats_evidenced"] == 2
    assert {r.metric: r.value for r in result.rows}["pass^3"] == 0.0
    shared = score_report_rows(data, "dev", 3, saved, None, {"k": {"tile": ["ceramic"]}})
    assert shared.rows[0].details["rows"][0]["repeats_evidenced"] == 3
    # a shorter list cycles like a saved prediction's repeats
    cycled = score_report_rows(data, "dev", 3, saved, None, per_repeat[:2])
    assert cycled.rows[0].details["rows"][0]["repeats_evidenced"] == 2  # ceramic, vinyl, ceramic
    assert materials_for_repeats([], 2) == [None, None]


def test_gold_materials_follow_the_room_photos() -> None:
    components = ds.PhotoComponentsSet.model_validate(
        {
            "photos": [
                {
                    "file": "21_1st Floor Kitchen photo 1.JPG",
                    "components": [
                        {"label": "tile", "material": "ceramic"},
                        {"label": "sink"},
                        {"label": "other", "material": "wood"},
                    ],
                },
                {
                    "file": "photos/01_Front.JPG",
                    "components": [{"label": "roof", "material": "asphalt"}],
                },
            ]
        }
    )
    assert gold_materials(inventory(), None) is None
    assert gold_materials(inventory(), components) == {
        "1_kitchen": {"tile": ["ceramic"], "sink": ["unknown"]},
        "x_property_front": {"roof": ["asphalt"]},  # matched by base name
    }


# --------------------------------------------------------------------------- baseline / runner


def test_gold_inventory_baseline_and_skips() -> None:
    data = rows(*FULL)
    result = score_report_rows(data, "dev", 1, None, inventory(), None)
    assert result.system == "gold_inventory"
    outcome = {r["id"]: r["outcome"] for r in result.rows[0].details["rows"]}
    # the gold kitchen has tile, but no material source -> the constrained row is missed
    assert outcome["tile"] == "missed" and outcome["roof"] == "evidenced"
    assert outcome["restroom"] == "evidenced" and outcome["millwork"] == "evidenced"
    assert score_report_rows(data, "held_out", 1, None, inventory(), None).skipped
    skipped = score_report_rows(data, "dev", 1, None, None, None)
    assert skipped.skipped and "no saved room inventory" in skipped.notes[0]
    with pytest.raises(ValueError, match="label: count"):
        score_report_rows(data, "dev", 1, {"r": ["window"]}, None, None)


def test_evaluate_scores_report_rows_from_the_inventory_file(tmp_path: Path) -> None:
    data = ds.synthetic_smoke()
    (tmp_path / "room_inventory.json").write_text(json.dumps({"x_kitchen": {"kitchen_cabinet": 6}}))
    (tmp_path / "room_inventory.heuristic.json").write_text(
        json.dumps({"x_bathroom": {"toilet": 1, "bathtub": 1}})
    )
    (tmp_path / "rooms.json").write_text(
        json.dumps(
            [
                {
                    "room_id": "x_bathroom",
                    "heuristic_objects": [{"label": "toilet", "material": "ceramic"}],
                }
            ]
        )
    )
    by_name = {
        r.dataset: r
        for r in evaluate(data, split="dev", repeats=1, predictions=tmp_path, corpus_dir=None)
    }
    assert by_name["report_rows"].system == "predictions"
    assert by_name["room_inventory"].system == "predictions"
    assert by_name["report_rows"].rows[0].value == pytest.approx(1 / 7)  # cabinets only
    heuristic = {
        r.dataset: r
        for r in evaluate(
            data,
            split="dev",
            repeats=1,
            predictions=tmp_path,
            corpus_dir=None,
            inventory_file="room_inventory.heuristic.json",
        )
    }
    assert heuristic["report_rows"].rows[0].value == pytest.approx(2 / 7)  # bathtub, toilet
    detail = {r["id"]: r for r in heuristic["report_rows"].rows[0].details["rows"]}
    assert detail["B-1:4"]["evidence"][0]["materials"] == ["ceramic"]  # from rooms.json
    # the room_inventory dataset was scored from the same heuristic file
    per_room = heuristic["room_inventory"].rows[3].details["per_room"]
    assert per_room["x_bathroom"]["predicted"] == {"toilet": 1, "bathtub": 1}
    assert per_room["x_kitchen"]["predicted"] == {}


def test_dry_run_scores_the_synthetic_checklist_against_the_gold_inventory(tmp_path: Path) -> None:
    report = tmp_path / "eval.md"
    assert main(["--dry-run", "--no-db", "--repeats", "2", "--report", str(report)]) == 0
    text = report.read_text()
    assert "## report_rows (system: `gold_inventory`)" in text
    assert "| row_coverage | 0.8571 | 7 |" in text  # 6 of 7: the shed has no label
    assert "| row_coverage_in_vocabulary | 1.0000 | 6 |" in text
    assert "| pass^2 | 0.8571 | 7 |" in text  # the vocabulary gap never passes
    assert "vocabulary_gap (needs `shed`)" in text and "not_photo_derivable" in text
    assert "### Rows" in text


def test_inventory_file_option_must_be_a_json_file_name() -> None:
    with pytest.raises(SystemExit):
        main(["--dry-run", "--no-db", "--inventory-file", "sub/room_inventory.json"])
    with pytest.raises(SystemExit):
        main(["--dry-run", "--no-db", "--inventory-file", "room_inventory"])


def test_other_datasets_are_unchanged_by_the_checklist() -> None:
    """Regression: adding report_rows changes nothing about the other datasets' scores."""
    data = ds.synthetic_smoke()
    without = dict(data)
    del without["report_rows"]
    scored = evaluate(data, split="dev", repeats=2, predictions=None, corpus_dir=None)
    reference = evaluate(without, split="dev", repeats=2, predictions=None, corpus_dir=None)
    assert [r.dataset for r in scored] == [r.dataset for r in reference]
    for a, b in zip(scored, reference, strict=True):
        if a.dataset == "report_rows":
            assert not a.skipped and b.skipped and b.notes == ["dataset file not found"]
            continue
        assert (a.system, a.skipped, a.notes) == (b.system, b.skipped, b.notes)
        assert [(r.metric, r.value, r.n) for r in a.rows] == [
            (r.metric, r.value, r.n) for r in b.rows
        ]
    text = render_report(scored, split="dev", repeats=2, source="x", git_sha="abc")
    assert text.count("\n## ") == len(ds.DATASETS)
