"""``evals.e2e_report``: a study export against the engineer's report, on fabricated data."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from basis.vision.schemas import COMPONENT_LABELS
from evals import e2e_report as e2e
from evals.e2e_report import (
    E2EError,
    E2ERows,
    EngineerReport,
    ExportAsset,
    StudyExport,
    assign_assets,
    load_export,
    main,
    majority_bucket,
    render_markdown,
    score,
    to_json,
)

D = Decimal


def asset(
    label: str | None,
    bucket: str,
    total: str | None = None,
    *,
    room_type: str | None = "kitchen",
    material: str | None = None,
    review_reason: str | None = None,
    id: str | None = None,
) -> ExportAsset:
    return ExportAsset(
        id=id or f"{label}-{bucket}-{total}-{room_type}-{material}",
        label=label,
        material=material,
        room_name=room_type,
        room_type=room_type,
        bucket=bucket,
        total=None if total is None else D(total),
        review_reason=review_reason,
    )


def rows(*items: dict[str, Any], unassigned: dict[str, str] | None = None) -> E2ERows:
    """A label file with the given rows; every other vocabulary label is unassigned."""
    used = {a["label"] for r in items for a in r.get("assign", [])}
    extra = dict(unassigned or {})
    listed = [{"label": k, "reason": v} for k, v in extra.items()]
    listed += [
        {"label": label, "reason": "not in this test"}
        for label in COMPONENT_LABELS
        if label not in used and label not in extra
    ]
    return E2ERows.model_validate({"rows": list(items), "unassigned": listed})


def report(**overrides: Any) -> EngineerReport:
    """Six rows: 4 photo-visible (two y5, one y15, one y39), one vocabulary gap, one kind C.
    Costs sum to 10000 = 9900 + 100."""
    data: dict[str, Any] = {
        "building_basis": "9900.00",
        "closing_costs": "100.00",
        "rows": [
            {"id": "r1", "system": "Carpet", "bucket": "y5", "cost": "1000.00"},
            {"id": "r2", "system": "Cabinets", "bucket": "y5", "cost": "500.00"},
            {"id": "r3", "system": "Driveway", "bucket": "y15", "cost": "1500.00"},
            {"id": "r4", "system": "Windows", "bucket": "y39", "cost": "3000.00"},
            {"id": "r5", "system": "Circuits", "bucket": "y5", "cost": "200.00"},
            {"id": "r6", "system": "Foundations", "bucket": "y39", "cost": "3800.00"},
        ],
    }
    data.update(overrides)
    return EngineerReport.model_validate(data)


def labels() -> E2ERows:
    return rows(
        {"id": "r1", "system": "Carpet", "kind": "B", "assign": [{"label": "carpet"}]},
        {
            "id": "r2",
            "system": "Cabinets",
            "kind": "A",
            "assign": [
                {"label": "kitchen_cabinet", "exclude_room_types": ["bathroom"]},
                {"label": "countertop"},
            ],
        },
        {"id": "r3", "system": "Driveway", "kind": "A", "assign": [{"label": "driveway"}]},
        {"id": "r4", "system": "Windows", "kind": "A", "assign": [{"label": "window"}]},
        {"id": "r5", "system": "Circuits", "kind": "A"},
        {
            "id": "r6",
            "system": "Foundations",
            "kind": "C",
            "assign": [{"label": "smoke_detector"}],
        },
        unassigned={"mirror": "decor"},
    )


def export(*assets: ExportAsset) -> StudyExport:
    return StudyExport(study_id="s", revision=1, assets=list(assets))


# ------------------------------------------------------------------ loaders


def test_load_export_json_and_csv(tmp_path: Path) -> None:
    document = {
        "format": "basis-export/2",
        "study_id": "abc",
        "revision": 3,
        "property_name": "P",
        "assets": [
            {
                "id": "a1",
                "label": "window",
                "material": "wood",
                "room_name": "Kitchen",
                "room_type": "kitchen",
                "bucket": "y27_5",
                "method": "rule",
                "needs_review": True,
                "review_reason": "citation_unverified; model:x",
                "quantity": "2.0000",
                "unit": "EA",
                "total": "100.50",
                "takeoff_inputs": {"count": 2},
                "citations": [{"id": "c1"}, {"id": "c2"}],
                "extra_field_ignored": 1,
            },
            {
                "id": "a2",
                "label": "other",
                "bucket": "y7",
                "method": "llm",
                "needs_review": True,
                "total": None,
                "citations": [],
            },
        ],
        "summary": {},
    }
    path = tmp_path / "rev-3.json"
    path.write_text(json.dumps(document))
    loaded = load_export(path)
    assert (loaded.study_id, loaded.revision, loaded.property_name) == ("abc", 3, "P")
    assert loaded.takeoff is None  # an export before EVAL_REPORT 37 has no summary.takeoff
    a1, a2 = loaded.assets
    assert a1.citation_count == 2 and a1.total == D("100.50") and a1.takeoff_inputs == {"count": 2}
    assert (a1.source, a2.source) == ("none", "none")
    assert a1.unpriced_reason == "citation_unverified"
    assert a2.total is None and a2.priced is False and a2.unpriced_reason == "unpriced"
    assert loaded.total_priced == D("100.50")

    csv_path = tmp_path / "abc" / "rev-3.assets.csv"
    csv_path.parent.mkdir()
    csv_path.write_text(
        "asset_id,component_id,room,room_type,label,material,attachment,category,bucket,"
        "recovery_period,irs_section,method,confidence,needs_review,review_reason,quantity,"
        "unit,formula,unit_cost,total,cost_year,location_factor,cost_source,citation_count,"
        "citations\n"
        "a1,,Kitchen,kitchen,window,wood,permanent,Windows,y27_5,27.5,1250,rule,0.9,true,"
        '"citation_unverified; model:x",2.0000,EA,,50.25,100.50,2026,1.000,src,2,"x | y"\n'
        "a2,,,,other,,,Other,y7,7,,llm,0.1,true,,,,,,,,,,0,\n"
    )
    from_csv = load_export(csv_path)
    assert (from_csv.study_id, from_csv.revision) == ("abc", 3)
    assert [a.model_dump(exclude={"takeoff_inputs"}) for a in from_csv.assets] == [
        a.model_dump(exclude={"takeoff_inputs"}) for a in loaded.assets
    ]

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "something/9", "study_id": "x", "revision": 1}))
    with pytest.raises(E2EError, match="not a study export"):
        load_export(bad)


# ------------------------------------------------------------------ the label file


def test_label_file_must_cover_the_vocabulary_once() -> None:
    with pytest.raises(ValidationError, match="neither assigned nor unassigned"):
        E2ERows.model_validate(
            {"rows": [{"id": "r", "system": "S", "kind": "A", "assign": [{"label": "carpet"}]}]}
        )
    with pytest.raises(ValidationError, match="both assigned and unassigned"):
        rows(
            {"id": "r", "system": "S", "kind": "A", "assign": [{"label": "carpet"}]},
            unassigned={"carpet": "x"},
        )
    with pytest.raises(ValidationError, match="appears twice"):
        rows(
            {"id": "r", "system": "S", "kind": "A", "assign": [{"label": "carpet"}]},
            {"id": "r", "system": "T", "kind": "A", "assign": [{"label": "tile"}]},
        )
    with pytest.raises(ValidationError, match="not a component label"):
        rows({"id": "r", "system": "S", "kind": "A", "assign": [{"label": "other"}]})
    with pytest.raises(ValidationError, match="not a room type"):
        rows(
            {
                "id": "r",
                "system": "S",
                "kind": "A",
                "assign": [{"label": "carpet", "room_types": ["lounge"]}],
            }
        )
    with pytest.raises(ValidationError, match="both required and excluded"):
        rows(
            {
                "id": "r",
                "system": "S",
                "kind": "A",
                "assign": [
                    {
                        "label": "carpet",
                        "room_types": ["kitchen"],
                        "exclude_room_types": ["kitchen"],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="both required and excluded"):
        rows(
            {
                "id": "r",
                "system": "S",
                "kind": "A",
                "assign": [
                    {"label": "tile", "materials": ["ceramic"], "exclude_materials": ["ceramic"]}
                ],
            }
        )
    good = labels()
    assert good.row("r2").labels == ["countertop", "kitchen_cabinet"]
    assert good.row("r5").vocabulary_gap and not good.row("r6").photo_derivable
    assert good.unassigned_reasons["mirror"] == "decor"


def test_assignment_conditions_are_complementary() -> None:
    """A label split by an allow-list and the matching deny-list is charged exactly once,
    an unknown room or material going to the deny-list side."""
    split = rows(
        {
            "id": "wet",
            "system": "Utility sinks",
            "kind": "A",
            "assign": [{"label": "sink", "room_types": ["kitchen", "laundry"]}],
        },
        {
            "id": "dry",
            "system": "Plumbing",
            "kind": "A",
            "assign": [{"label": "sink", "exclude_room_types": ["kitchen", "laundry"]}],
        },
        {
            "id": "paneling",
            "system": "Paneling",
            "kind": "B",
            "assign": [{"label": "wall_finish", "materials": ["wood"]}],
        },
        {
            "id": "paint",
            "system": "Painting",
            "kind": "C",
            "assign": [{"label": "wall_finish", "exclude_materials": ["wood"]}],
        },
    )
    kitchen = asset("sink", "y5", "1", room_type="kitchen")
    bath = asset("sink", "y5", "1", room_type="bathroom")
    nowhere = asset("sink", "y5", "1", room_type=None)
    wood = asset("wall_finish", "y39", "1", material="painted wood")
    drywall = asset("wall_finish", "y39", "1", material="drywall")
    blank = asset("wall_finish", "y39", "1", material=None)
    mirror = asset("mirror", "y5", "1")
    fiberglass = asset("tile", "y5", "1", material="fiberglass")
    result = assign_assets(
        split, [kitchen, bath, nowhere, wood, drywall, blank, mirror, fiberglass]
    )
    assert result.by_row == {
        "wet": [kitchen],
        "dry": [bath, nowhere],
        "paneling": [wood],
        "paint": [drywall, blank],
    }
    assert result.unassigned_listed == {"mirror": [mirror], "tile": [fiberglass]}
    assert result.unassigned_no_rule == {}
    assert len(result.assigned) == 6 and len(result.unassigned) == 2


def test_assignment_no_rule_and_conflicts() -> None:
    only_ceramic = rows(
        {
            "id": "tile",
            "system": "Ceramic tile",
            "kind": "B",
            "assign": [{"label": "tile", "materials": ["ceramic", "porcelain"]}],
        }
    )
    ceramic = asset("tile", "y39", "1", material="ceramic")
    fiberglass = asset("tile", "y39", "1", material="fiberglass")
    unlabeled = asset(None, "y7", None)
    result = assign_assets(only_ceramic, [ceramic, fiberglass, unlabeled])
    assert result.by_row == {"tile": [ceramic]}
    assert result.unassigned_no_rule == {"tile": [fiberglass]}
    assert result.unassigned_listed == {"other": [unlabeled]}

    overlapping = rows(
        {"id": "a", "system": "A", "kind": "A", "assign": [{"label": "sink"}]},
        {
            "id": "b",
            "system": "B",
            "kind": "A",
            "assign": [{"label": "sink", "room_types": ["kitchen"]}],
        },
    )
    with pytest.raises(E2EError, match=r"more than one row:\n.*sink.*: a, b"):
        assign_assets(overlapping, [asset("sink", "y5", "1", room_type="kitchen")])
    # A bathroom sink matches only row a: no conflict.
    assert assign_assets(overlapping, [asset("sink", "y5", "1", room_type="bathroom")]).by_row["a"]


# ------------------------------------------------------------------ the engineer's report


def test_engineer_report_must_reconcile() -> None:
    base = report()
    assert base.total == D("10000.00")
    assert base.building_bucket == "y39"
    assert base.bucket_totals == {"y5": D("1700.00"), "y15": D("1500.00"), "y39": D("6800.00")}
    with pytest.raises(ValidationError, match="not basis \\+ closing costs"):
        report(closing_costs="101.00")
    with pytest.raises(ValidationError, match="cannot have \\['y27_5'\\] rows"):
        report(
            rows=[
                {"id": "r1", "system": "S", "bucket": "y27_5", "cost": "10000.00"},
            ]
        )
    with pytest.raises(ValidationError, match="appears twice"):
        report(
            rows=[
                {"id": "r1", "system": "S", "bucket": "y5", "cost": "5000.00"},
                {"id": "r1", "system": "S", "bucket": "y5", "cost": "5000.00"},
            ]
        )
    with pytest.raises(ValidationError, match="shares summing to 1"):
        report(systems=[{"name": "All", "rows": {"r1": "1"}, "depreciable_cost": "10000.00"}])
    with pytest.raises(ValidationError, match="unknown row"):
        report(systems=[{"name": "All", "rows": {"zz": "1"}, "depreciable_cost": "10000.00"}])
    with pytest.raises(ValidationError, match="does not sum to the basis"):
        report(allocation_building={"y5": "1.00"})
    with pytest.raises(ValidationError, match="buckets with no rows"):
        report(bucket_percent={"y7": "1.0"})


# ------------------------------------------------------------------ scoring


def test_majority_bucket_by_count_and_by_dollars() -> None:
    assets = [
        asset("window", "y27_5", "10"),
        asset("window", "y27_5", "10", room_type="bedroom"),
        asset("window", "y5", "100"),
    ]
    by_count, by_dollars, counts, dollars = majority_bucket(assets, "y39")
    assert (by_count, by_dollars) == ("y39", "y5")
    assert counts == {"y39": 2, "y5": 1}
    assert dollars == {"y39": D("20"), "y5": D("100")}
    # Count tie: the bucket with more dollars; dollar tie: more assets; both tied: shorter period.
    assert majority_bucket([asset("w", "y39", "5"), asset("w", "y5", "6")], "y39")[0] == "y5"
    assert majority_bucket([asset("w", "y39", "5"), asset("w", "y5", "5")], "y39")[0] == "y5"
    tie = [asset("w", "y39", "5"), asset("w", "y39", None), asset("w", "y5", "5")]
    assert majority_bucket(tie, "y39")[:2] == ("y39", "y39")
    # No priced asset: a bucket by count, none by dollars.
    assert majority_bucket([asset("w", "y15", None)], "y39")[:2] == ("y15", None)
    assert majority_bucket([], "y39") == (None, None, {}, {})
    # The residential building bucket is compared as the engineer's building bucket.
    assert majority_bucket([asset("w", "y27_5", "1")], "y27_5")[0] == "y27_5"
    assert majority_bucket([asset("w", "y39", "1")], "y27_5")[0] == "y27_5"


def fabricated_export() -> StudyExport:
    return export(
        asset("carpet", "y5", "400.00", room_type="bedroom"),  # r1, right
        asset("carpet", "y5", "100.00", room_type="living_room"),  # r1
        asset("kitchen_cabinet", "y27_5", "300.00"),  # r2: two y39 by count...
        asset("kitchen_cabinet", "y27_5", None, review_reason="COST_NEEDS_QUANTITY"),  # r2
        asset("countertop", "y5", "350.00"),  # r2: ...but y5 by dollars
        asset("kitchen_cabinet", "y5", "50.00", room_type="bathroom"),  # excluded: no rule
        asset("driveway", "y15", None, review_reason="COST_NEEDS_QUANTITY", room_type="exterior"),
        asset("window", "y27_5", "250.00"),  # r4, right (y27_5 -> y39)
        asset("smoke_detector", "y5", "25.00"),  # r6 (kind C): charged, not scored
        asset("mirror", "y5", "75.00"),  # unassigned, listed
        asset(None, "y7", None),  # other
    )


def test_score_outcomes_buckets_dollars_and_shares() -> None:
    result = score(fabricated_export(), labels(), report())
    by_id = {r.row.id: r for r in result.rows}
    assert {k: v.outcome for k, v in by_id.items()} == {
        "r1": "evidenced",
        "r2": "evidenced",
        "r3": "evidenced",
        "r4": "evidenced",
        "r5": "vocabulary_gap",
        "r6": "not_produced",
    }
    r1, r2, r3, r4, r5, r6 = (by_id[k] for k in ("r1", "r2", "r3", "r4", "r5", "r6"))
    assert (r1.bucket_by_count, r1.bucket_by_dollars, r1.bucket_right_by_count) == (
        "y5",
        "y5",
        True,
    )
    assert (r2.bucket_by_count, r2.bucket_by_dollars) == ("y39", "y5")
    assert (r2.bucket_right_by_count, r2.bucket_right_by_dollars) == (False, True)
    assert r2.tally_count == {"y39": 2, "y5": 1} and r2.tally_dollars == {
        "y39": D("300.00"),
        "y5": D("350.00"),
    }
    assert (r2.priced, r2.unpriced, r2.unpriced_reasons) == (2, 1, {"COST_NEEDS_QUANTITY": 1})
    assert (r3.bucket_by_count, r3.bucket_by_dollars, r3.bucket_right_by_count) == (
        "y15",
        None,
        True,
    )
    assert r3.bucket_right_by_dollars is None and r3.pipeline_cost == D(0)
    assert (r4.bucket_by_count, r4.bucket_right_by_count) == ("y39", True)
    assert r5.bucket_right_by_count is None and r5.assets == []
    # Kind C: charged but never scored.
    assert r6.assets and r6.bucket_by_count == "y5" and r6.bucket_right_by_count is None
    assert r6.pipeline_cost == D("25.00") and r6.cost_ratio is None

    # Dollars: pipeline total = every priced asset, 400+100+300+350+50+250+25+75 = 1550.
    assert result.export.total_priced == D("1550.00")
    assert r1.pipeline_cost == D("500.00") and r1.cost_ratio == D("0.5")
    assert r1.cost_error == D("500.00")
    assert r1.pipeline_share == D("500.00") / D("1550.00")
    assert r1.engineer_share == D("0.1")
    assert r1.share_error == abs(D("500.00") / D("1550.00") - D("0.1"))
    assert r2.pipeline_cost == D("650.00") and r2.cost_ratio == D("1.3")

    # Buckets: pipeline dollars per bucket over all priced assets, y27_5 as y39.
    buckets = {b.bucket: b for b in result.buckets}
    assert list(buckets) == ["y5", "y7", "y15", "y39"]  # y7: the pipeline's `other` asset
    assert (buckets["y7"].engineer_rows, buckets["y7"].pipeline_assets) == (0, 1)
    assert (buckets["y7"].pipeline_cost, buckets["y7"].pipeline_unpriced) == (D(0), 1)
    assert buckets["y5"].pipeline_cost == D("1000.00")  # 400+100+350+50+25+75
    assert buckets["y39"].pipeline_cost == D("550.00")  # 300+250
    assert buckets["y15"].pipeline_cost == D(0)
    assert (buckets["y5"].pipeline_assets, buckets["y5"].pipeline_unpriced) == (6, 0)
    assert (buckets["y39"].pipeline_assets, buckets["y39"].pipeline_unpriced) == (3, 1)
    assert buckets["y5"].engineer_share == D("0.17")
    assert (buckets["y5"].engineer_rows, buckets["y5"].rows_scored, buckets["y5"].rows_right) == (
        3,
        2,
        1,
    )
    assert (
        buckets["y39"].engineer_rows,
        buckets["y39"].rows_scored,
        buckets["y39"].rows_right,
    ) == (2, 1, 1)
    assert buckets["y39"].share_error == abs(D("550.00") / D("1550.00") - D("0.68"))

    # Assignment and the summary block.
    assert result.assignment.unassigned_no_rule == {
        "kitchen_cabinet": [a for a in result.export.assets if a.room_type == "bathroom"]
    }
    assert sorted(result.assignment.unassigned_listed) == ["mirror", "other"]
    c = result.summary["classification"]
    assert (c["rows"], c["photo_visible_rows"], c["kind_c_rows"]) == (6, 5, 1)
    assert c["kind_c_engineer_share"] == 0.38
    assert (c["evidenced"], c["missed"], c["vocabulary_gap"]) == (4, 0, 1)
    assert c["vocabulary_gap_rows"] == ["Circuits"]
    assert c["evidenced_of_photo_visible"] == 0.8
    assert (c["bucket_right_by_count"], c["bucket_right_of_evidenced"]) == (3, 0.75)
    assert c["bucket_right_of_photo_visible"] == 0.6 and c["bucket_right_of_all_rows"] == 0.5
    assert (c["bucket_right_by_dollars"], c["bucket_right_by_dollars_of_scored"]) == (3, 1.0)
    assert c["wrong_bucket_rows"] == ["Cabinets (y5 vs y39)"]
    assert c["engineer_share_on_rows_bucket_right"] == 0.55  # 1000 + 1500 + 3000 of 10000
    assert c["engineer_share_on_wrong_bucket_rows"] == 0.05
    assert (c["rows_with_unpriced_assets"], c["evidenced_rows_fully_unpriced"]) == (2, 1)
    asg = result.summary["assignment"]
    assert (asg["assigned"], asg["assigned_to_kind_c_rows"], asg["unassigned"]) == (8, 1, 3)
    assert asg["unassigned_priced_dollars"] == "125.00"
    sh = result.summary["shares"]
    assert sh["bucket_share_error_pct"]["y15"] == 15.0
    assert sh["max_bucket_share_error_pct"] == pytest.approx(47.52)  # y5: 1000/1550 - 0.17
    assert result.summary["dollars"]["pipeline_over_engineer_total"] == 0.155
    assert result.summary["reproduction"] is None  # no printed RCN total


def test_score_missed_row_and_pipeline_schedule() -> None:
    """A photo-visible row with a label but no asset is missed; the pipeline's rows go
    through the tail with the engineer's basis and closing costs."""
    result = score(
        export(asset("carpet", "y5", "300.00"), asset("window", "y39", "100.00")),
        labels(),
        report(),
    )
    by_id = {r.row.id: r for r in result.rows}
    assert by_id["r2"].outcome == "missed" and by_id["r3"].outcome == "missed"
    c = result.summary["classification"]
    assert c["missed_rows"] == ["Cabinets", "Driveway"]
    assert c["evidenced_interval"][0] < c["evidenced_of_photo_visible"] == 0.4
    ps = result.pipeline_schedule
    assert ps is not None
    assert ps.rcn_total == D("400.00") and ps.factor == D("25")
    assert ps.rows == {
        "r1": D("7500.00"),
        "r2": D(0),
        "r3": D(0),
        "r4": D("2500.00"),
        "r5": D(0),
        "r6": D(0),
    }
    assert ps.bucket_totals == {"y5": D("7500.00"), "y15": D(0), "y39": D("2500.00")}
    assert ps.bucket_percent == {"y5": D("75.0"), "y15": D("0.0"), "y39": D("25.0")}
    assert ps.allocation_building == {"y5": D("7425.00"), "y15": D(0), "y39": D("2475.00")}
    assert ps.allocation_closing == {"y5": D("75.00"), "y15": D(0), "y39": D("25.00")}


def test_score_without_priced_assets_has_no_schedule() -> None:
    result = score(export(asset("carpet", "y5", None)), labels(), report())
    assert result.pipeline_schedule is None
    assert result.rows[0].pipeline_share == D(0)
    assert (
        result.summary["dollars"]["median_row_cost_ratio"]
        != result.summary["dollars"]["median_row_cost_ratio"]
    )  # nan


def test_score_reproduces_a_printed_report_to_the_cent() -> None:
    """With the printed replacement-cost total (and systems page), the engineer's own
    numbers come back exactly from the implied RCNs."""
    printed = report(
        replacement_cost_total="12500.00",  # factor 0.8
        systems=[
            {
                "name": "Finishes",
                "rows": {"r1": "1", "r2": "0.5"},
                "depreciable_cost": "1250.00",
                "replacement_cost": "1562.50",
            },
            {
                "name": "Rest",
                "rows": {"r2": "0.5", "r3": "1", "r4": "1", "r5": "1", "r6": "1"},
                "depreciable_cost": "8750.00",
                "replacement_cost": "10937.50",
            },
        ],
        bucket_percent={"y5": "17.0", "y15": "15.0", "y39": "68.0"},
        allocation_building={"y5": "1683.00", "y15": "1485.00", "y39": "6732.00"},
        allocation_closing={"y5": "17.00", "y15": "15.00", "y39": "68.00"},
    )
    result = score(fabricated_export(), labels(), printed)
    assert result.engineer_schedule is not None and result.engineer_schedule.factor == D("0.8")
    rep = result.reproduction
    assert rep is not None and rep.exact
    assert rep.compared == 6 + 1 + 3 + 3 + 3 + 3 + 4
    assert [
        (ln.name, ln.depreciable_cost, ln.replacement_cost) for ln in result.engineer_systems
    ] == [
        ("Finishes", D("1250.00"), D("1562.50")),
        ("Rest", D("8750.00"), D("10937.50")),
    ]
    assert result.summary["reproduction"] == {
        "compared": 23,
        "exact": True,
        "deviations": 0,
        "max_abs_deviation": "0",
        "items_off": [],
    }


def test_score_refuses_mismatched_row_ids() -> None:
    other = report(rows=[{"id": "zz", "system": "S", "bucket": "y5", "cost": "10000.00"}])
    with pytest.raises(E2EError, match="row ids .* differ"):
        score(fabricated_export(), labels(), other)


# ------------------------------------------------------------------ output and CLI


def test_markdown_json_and_data_free_output() -> None:
    result = score(fabricated_export(), labels(), report())
    full = render_markdown(result)
    free = render_markdown(result, data_free=True)
    for text in (full, free):
        assert "## Summary" in text and "## Buckets" in text and "## Rows" in text
        assert "## Unassigned assets" in text and "| mirror | decor |" in text
        assert "| kitchen_cabinet | no rule matched" in text
        assert "Cabinets (y5 vs y39)" in text
    assert "| engineer bucket shares |" in full and "3,000.00" in full
    assert "| engineer bucket shares |" not in free and "3,000.00" not in free
    assert "engineer $" not in free and "ratio" not in free.split("## Rows")[1].split("\n")[2]
    payload = to_json(result)
    assert json.dumps(payload)  # serializable
    assert payload["summary"] is result.summary
    rows_json = {r["id"]: r for r in payload["rows"]}
    assert rows_json["r2"]["tally_dollars"] == {"y39": "300.00", "y5": "350.00"}
    assert rows_json["r1"]["pipeline_scaled"] is not None
    assert payload["buckets"][0]["bucket"] == "y5"


def test_cli_writes_report_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export_path = tmp_path / "rev-1.json"
    document = {
        "format": "basis-export/2",
        "study_id": "s",
        "revision": 1,
        "assets": [
            a.model_dump(mode="json") | {"citations": []} for a in fabricated_export().assets
        ],
    }
    export_path.write_text(json.dumps(document, default=str))
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(labels().model_dump_json())
    report_path = tmp_path / "report.json"
    report_path.write_text(report().model_dump_json())
    out = tmp_path / "out" / "score.md"
    out_json = tmp_path / "out" / "score.json"
    code = main(
        [
            "--export",
            str(export_path),
            "--rows",
            str(rows_path),
            "--report",
            str(report_path),
            "--out",
            str(out),
            "--json",
            str(out_json),
            "--data-free",
        ]
    )
    assert code == 0
    assert "evidenced 4/5, bucket right 3/4" in capsys.readouterr().err
    assert out.read_text().startswith("# End-to-end: study s rev 1")
    assert json.loads(out_json.read_text())["summary"]["classification"]["evidenced"] == 4

    bad_rows = tmp_path / "bad.json"
    bad_rows.write_text("{}")
    assert (
        main(["--export", str(export_path), "--rows", str(bad_rows), "--report", str(report_path)])
        == 2
    )
    assert "error:" in capsys.readouterr().err


def test_default_label_paths_come_from_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(e2e.ENV_VAR, str(tmp_path))
    assert e2e.default_labels_path(e2e.ROWS_FILE) == tmp_path / "labels" / "e2e_rows_v1.json"
    assert (
        e2e.default_labels_path(e2e.REPORT_FILE) == tmp_path / "labels" / "engineer_report_v1.json"
    )


# ------------------------------------------------------------------ the sources (EVAL_REPORT 37)

TAKEOFF_BLOCK: dict[str, Any] = {
    "cost_year": 2024,
    "location_factor": "1.20",
    "cost_year_source": "placed in service 2024-03-01 (intake)",
    "location_source": "state NY (intake)",
    "property_class": "nonresidential",
    "property_class_source": "intake property_use",
    "area_sources": {"appraisal_derived": 1, "gla_share": 2},
    "needs_measurement": 1,
}


def sourced(label: str, bucket: str, total: str, **inputs: Any) -> ExportAsset:
    return asset(label, bucket, total).model_copy(update={"takeoff_inputs": inputs})


def test_asset_source_is_the_quantity_source_then_the_room_area_source() -> None:
    assert sourced("roof", "y39", "1", quantity_source="appraisal_derived").source == (
        "appraisal_derived"
    )
    assert sourced("carpet", "y5", "1", room_area_source="gla_share").source == "gla_share"
    both = sourced(
        "roof", "y39", "1", room_area_source="typical", quantity_source="appraisal_derived"
    )
    assert both.source == "appraisal_derived"
    assert sourced("carpet", "y5", "1", count=2).source == "none"
    assert asset("carpet", "y5", "1").source == "none"


def test_rows_tally_their_assets_sources_without_changing_the_scores() -> None:
    plain = export(asset("carpet", "y5", "100.00"), asset("carpet", "y5", "50.00"))
    traced = export(
        sourced("carpet", "y5", "100.00", room_area_source="gla_share"),
        sourced("carpet", "y5", "50.00", room_area_source="typical_for_room_type"),
    )
    before, after = score(plain, labels(), report()), score(traced, labels(), report())
    assert before.summary == after.summary  # no summary.takeoff on either: identical
    r1_before, r1_after = before.rows[0], after.rows[0]
    assert r1_before.row.id == "r1"
    assert (r1_before.pipeline_cost, r1_before.bucket_by_count) == (
        r1_after.pipeline_cost,
        r1_after.bucket_by_count,
    )
    assert r1_before.sources == {"none": 2}
    assert r1_after.sources == {"gla_share": 1, "typical_for_room_type": 1}
    rows_json = {r["id"]: r for r in to_json(after)["rows"]}
    assert rows_json["r1"]["sources"] == {"gla_share": 1, "typical_for_room_type": 1}
    text = render_markdown(after)
    row_table = text.split("## Rows")[1]
    assert "| sources |" in row_table.split("\n")[2]
    assert "{'gla_share': 1, 'typical_for_room_type': 1}" in row_table


def test_the_takeoff_block_is_printed_when_the_export_has_one() -> None:
    without = score(export(asset("carpet", "y5", "100.00")), labels(), report())
    assert "takeoff" not in without.summary["export"]
    text = render_markdown(without)
    assert "## Takeoff inputs" in text
    assert "not recorded in this export (written before EVAL_REPORT section 37)" in text
    assert "| quantity sources (assets) | {'none': 1} |" in text

    traced = StudyExport(
        study_id="s",
        revision=1,
        assets=[sourced("carpet", "y5", "100.00", room_area_source="gla_share")],
        takeoff=TAKEOFF_BLOCK,
    )
    with_block = score(traced, labels(), report())
    assert with_block.summary["export"]["takeoff"] == TAKEOFF_BLOCK
    text = render_markdown(with_block, data_free=True)
    assert "| cost year | 2024 (placed in service 2024-03-01 (intake)) |" in text
    assert "| location factor | 1.20 (state NY (intake)) |" in text
    assert "| property class | nonresidential (intake property_use) |" in text
    assert "| site items awaiting a measurement | 1 |" in text
    # The scores are the same with or without the block.
    for key in ("classification", "shares", "dollars", "assignment"):
        assert with_block.summary[key] == without.summary[key]


def test_load_export_reads_the_takeoff_block(tmp_path: Path) -> None:
    document = {
        "format": "basis-export/2",
        "study_id": "abc",
        "revision": 1,
        "assets": [],
        "summary": {"takeoff": TAKEOFF_BLOCK, "basis": {"intake": True}},
    }
    path = tmp_path / "rev-1.json"
    path.write_text(json.dumps(document))
    assert load_export(path).takeoff == TAKEOFF_BLOCK
    path.write_text(json.dumps({**document, "summary": {"takeoff": "not a block"}}))
    assert load_export(path).takeoff is None
