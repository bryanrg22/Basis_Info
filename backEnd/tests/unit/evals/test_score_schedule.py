"""``evals.score_schedule``: row set, quantities and buckets against the engineer's
schedule (EVAL_REPORT section 26). Values are hand-computed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import datasets as ds
from evals import score_schedule as ss


def row(rid: str, system: str, kind: str, share: float, **rule: object) -> ds.ReportRow:
    return ds.ReportRow.model_validate(
        {"id": rid, "system": system, "bucket": rule.pop("bucket", "y39"), "cost_share": share,
         "kind": kind, **rule}
    )  # fmt: skip


ROWS = [
    row("r1", "Windows", "A", 0.2, labels=["window"], count_required=True),
    row("r2", "Ceramic Tile Flooring", "B", 0.1, labels=["tile"],
        materials={"tile": ["ceramic"]}),
    row("r3", "Appliances", "A", 0.3, labels=["refrigerator", "stove"], count_required=True,
        bucket="y5"),
    row("r4", "Foundations", "C", 0.4),
]  # fmt: skip
TEMPLATE = ss.Template(
    names=("Windows", "Ceramic Tile Flooring", "Appliances", "Foundations", "Mirrors", "Elevators"),
    distractors=(ss.Distractor("Mirrors", ("mirror",)), ss.Distractor("Elevators", ())),
)
TYPES = {"1_kitchen": "kitchen", "1_bath": "bathroom"}


def test_inventory_lists_rows_by_their_rules_and_distractors_by_their_labels() -> None:
    inventory = {"1_kitchen": {"window": 2, "stove": 1}, "1_bath": {"tile": 1, "mirror": 1}}
    materials = {"1_bath": {"tile": ["vinyl"]}}
    score = ss.score_inventory(ROWS, TEMPLATE, inventory, TYPES, materials)
    # tile is vinyl, so the ceramic tile row is not evidenced
    assert score.listed == {"r1", "r3"}
    assert score.distractors == {"Mirrors"}
    assert score.quantities == {"r1": 2.0, "r3": 1.0}
    assert ss.inventory_quantity(ROWS[0], inventory, TYPES, materials) == 2


def test_schedule_rows_are_matched_by_name_with_quantities_only_in_each() -> None:
    written = [
        {"system": "windows", "bucket": "39", "quantity": 3, "unit": "each"},
        {"system": "Appliances", "bucket": "39", "quantity": 2, "unit": "lump_sum"},
        {"system": "Foundations", "bucket": "39", "quantity": 1, "unit": "lump_sum"},
        {"system": "Elevators", "bucket": "39", "quantity": 1, "unit": "each"},
        {"system": "Pool", "bucket": "15", "quantity": 1, "unit": "each"},
    ]
    score = ss.score_schedule_rows(ROWS, TEMPLATE, written)
    assert score.listed == {"r1", "r3"} and score.listed_c == {"r4"}
    assert score.distractors == {"Elevators"}
    assert score.quantities == {"r1": 3.0, "r3": 0.0}
    # Windows 39 right, Appliances 39 against the engineer's 5 wrong
    assert (score.buckets_right, score.buckets_listed) == (1, 2)


def test_summary_metrics_by_hand() -> None:
    score = ss.RepeatScore(
        listed={"r1", "r3"}, distractors={"Mirrors"}, quantities={"r1": 3.0, "r3": 0.0}
    )
    reference = {"r1": 2.0, "r3": 4.0}
    result = ss.summarize(ss.SystemScore("s", [score]), ROWS, reference)
    assert result["row_recall"] == pytest.approx(2 / 3)
    assert result["row_precision"] == pytest.approx(2 / 3)
    # cost recall over A/B rows: (0.2 + 0.3) / 0.6
    assert result["cost_recall"] == pytest.approx(0.5 / 0.6)
    # errors 1 and 4: MAE 2.5, WAPE 5 / 6
    assert result["quantity_mae"] == 2.5
    assert result["quantity_wape"] == pytest.approx(5 / 6)
    # relative errors 0.5 (1/2) and 1.0 (capped), weighted 0.2 and 0.3 -> 0.4 / 0.5
    assert result["quantity_cost_weighted_error"] == pytest.approx(0.8)
    assert result["missed_rows"] == ["Ceramic Tile Flooring"]


def test_relative_error_is_capped_and_a_missing_quantity_is_the_worst() -> None:
    assert ss.relative_error(None, 3.0) == 1.0
    assert ss.relative_error(10.0, 2.0) == 1.0
    assert ss.relative_error(1.0, 0.0) == 1.0  # reference 0 divides by 1
    assert ss.relative_error(3.0, 4.0) == 0.25


def test_paired_bootstrap_of_identical_systems_is_zero_and_ordered() -> None:
    a = {"r1": 0.5, "r2": 0.1, "r3": 0.0}
    assert ss.paired_bootstrap(a, a) == (0.0, 0.0, 0.0)
    mean, low, high = ss.paired_bootstrap({"r1": 1.0, "r2": 0.5, "r3": 0.2}, a, resamples=500)
    assert mean == pytest.approx((0.5 + 0.4 + 0.2) / 3)
    assert low <= mean <= high


def test_read_inventories_unpacks_repeats(tmp_path: Path) -> None:
    (tmp_path / "room_inventory.json").write_text(
        json.dumps(
            {
                "1_kitchen": {"repeats": [{"window": 2}, {"window": 3, "other": 4}]},
                "1_bath": {"toilet": 1},
            }
        )
    )
    first, second = ss.read_inventories(tmp_path, "room_inventory.json")
    assert first == {"1_kitchen": {"window": 2}, "1_bath": {"toilet": 1}}
    assert second == {"1_kitchen": {"window": 3}, "1_bath": {"toilet": 1}}


def test_template_reads_names_and_distractors(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps({"rows": [{"name": "Windows"}, {"name": "Mirrors", "distractor": True,
                                                   "labels": ["mirror"]}]})
    )  # fmt: skip
    template = ss.Template.read(path)
    assert template.names == ("Windows", "Mirrors")
    assert template.distractors == (ss.Distractor("Mirrors", ("mirror",)),)


def test_room_count_errors_cover_every_repeat_and_skip_unscored_labels() -> None:
    gold = {"1_kitchen": {"window": 2, "sink": 1}, "1_bath": {"toilet": 1}}
    inventories = [
        {"1_kitchen": {"window": 3, "sink": 1, "furniture": 5}, "1_bath": {"toilet": 1}},
        {"1_kitchen": {"window": 2, "microwave": 1}},  # the bath is missing: empty
    ]
    per_room, mae = ss.room_count_errors(gold, inventories)
    # kitchen pairs: rep1 window 1, sink 0; rep2 microwave 1, sink 1, window 0 -> 3 / 5
    assert per_room["1_kitchen"] == pytest.approx(3 / 5)
    # bath: rep1 0, rep2 1 -> 0.5
    assert per_room["1_bath"] == 0.5
    assert mae == pytest.approx(4 / 7)
