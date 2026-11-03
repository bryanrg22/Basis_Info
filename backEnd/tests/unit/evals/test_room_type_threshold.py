"""``evals.room_type_threshold``: the room-type review flag measured on stored answers
(synthetic answers and labels; the client's are private)."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from basis.vision.analyze import ROOM_REVIEW_THRESHOLD
from evals import room_type_threshold as rtt

# file -> label room type (the labels' vocabulary: ``stairs`` is the model's ``stairway``)
LABELS = {
    "01_Kitchen.JPG": "kitchen",
    "02_2nd Floor Bed 2.JPG": "bedroom",
    "03_Stairs to Garage.JPG": "stairs",
    "IMG_0004.JPG": "living_room",  # the name says nothing
    "05_Bath.JPG": "bathroom",
}

# (file, repeat) -> (room type answered, confidence). Repeat 1 and 2 disagree on the bedroom
# and on the camera-named photo; the bath is ``ambiguous`` both times.
ANSWERS: dict[tuple[str, int], tuple[str, float]] = {
    ("01_Kitchen.JPG", 1): ("kitchen", 0.99),
    ("02_2nd Floor Bed 2.JPG", 1): ("living_room", 0.7),
    ("03_Stairs to Garage.JPG", 1): ("stairway", 0.6),
    ("IMG_0004.JPG", 1): ("living_room", 0.45),
    ("05_Bath.JPG", 1): ("ambiguous", 0.3),
    ("01_Kitchen.JPG", 2): ("kitchen", 0.98),
    ("02_2nd Floor Bed 2.JPG", 2): ("bedroom", 0.9),
    ("03_Stairs to Garage.JPG", 2): ("stairway", 0.6),
    ("IMG_0004.JPG", 2): ("dining_room", 0.8),
    ("05_Bath.JPG", 2): ("ambiguous", 0.3),
}


def write_labels(root: Path) -> Path:
    labels = root / "labels"
    labels.mkdir()
    items = [{"file": f, "room_type": t, "floor": None} for f, t in LABELS.items()]
    (labels / "photo_rooms_v1.json").write_text(json.dumps({"items": items}))
    return labels


def write_run(root: Path, name: str, *, recorded: bool = True, model: bool = True) -> Path:
    """A stored run as ``evals.predict_photos`` leaves it; ``recorded`` keeps the
    ``needs_review`` flag the production ``review`` sets at prediction time."""
    run = root / name
    run.mkdir()
    lines = []
    for (file, repeat), (room_type, confidence) in ANSWERS.items():
        flagged = recorded and (room_type == "ambiguous" or confidence < ROOM_REVIEW_THRESHOLD)
        reason = None
        if flagged:
            reason = (
                "ambiguous" if room_type == "ambiguous" else f"low_room_confidence:{confidence:.2f}"
            )
        analysis: dict[str, Any] = {
            "room_type": room_type,
            "room_confidence": confidence,
            "floor": None,
            "room_ordinal": 1,
            "components": [],
            "description": "",
            "needs_review": flagged,
            "review_reason": reason,
        }
        lines.append(json.dumps({"file": file, "repeat": repeat, "analysis": analysis}))
    (run / "photo_raw.jsonl").write_text("\n".join(lines) + "\n")
    if model:
        (run / "run.json").write_text(
            json.dumps(
                {
                    "models_reported": ["gpt-6-luna"],
                    "reasoning_effort": "low",
                    "prompt": "photo_surfaces/v2",
                }
            )
        )
    return run


@pytest.fixture
def answers(tmp_path: Path) -> list[rtt.Answer]:
    truth = rtt.load_truth(write_labels(tmp_path))
    found, skipped = rtt.answers_of(write_run(tmp_path, "run_a"), truth)
    assert skipped == []
    return found


def by_key(found: list[rtt.Answer]) -> dict[tuple[str, int], rtt.Answer]:
    return {(a.file, a.repeat): a for a in found}


def test_answers_carry_truth_name_and_recorded_flag(answers: list[rtt.Answer]) -> None:
    got = by_key(answers)
    assert len(got) == 10
    stairs = got["03_Stairs to Garage.JPG", 1]
    assert stairs.truth == "stairway" and stairs.name_type == "stairway" and not stairs.wrong
    camera = got["IMG_0004.JPG", 2]
    assert camera.name_type is None and camera.wrong and not camera.ambiguous
    bath = got["05_Bath.JPG", 1]
    assert bath.ambiguous and bath.wrong and bath.name_type == "bathroom"
    assert bath.recorded_flag and bath.recorded_reason == "ambiguous"
    assert got["IMG_0004.JPG", 1].recorded_flag  # 0.45: the production floor fired
    assert not got["01_Kitchen.JPG", 1].recorded_flag


def test_skips_answers_without_label_or_analysis(tmp_path: Path) -> None:
    truth = rtt.load_truth(write_labels(tmp_path))
    run = write_run(tmp_path, "run_a")
    with (run / "photo_raw.jsonl").open("a") as f:
        f.write(json.dumps({"file": "99_Unlabelled.JPG", "repeat": 1, "analysis": None}) + "\n")
        f.write(
            json.dumps(
                {
                    "file": "98_No label.JPG",
                    "repeat": 1,
                    "analysis": {"room_type": "kitchen", "room_confidence": 0.9},
                }
            )
            + "\n"
        )
    found, skipped = rtt.answers_of(run, truth)
    assert len(found) == 10
    assert sorted(skipped) == ["98_No label.JPG#1", "99_Unlabelled.JPG#1"]


def test_threshold_rule_counts(answers: list[rtt.Answer]) -> None:
    swept = {r.rule: r for r in rtt.sweep(answers, (0.0, 0.5, 0.75, 0.85))}
    at0 = swept["threshold 0.00"]
    assert (at0.flagged, at0.flagged_ambiguous, at0.flagged_by_rule) == (2, 2, 0)
    assert (at0.errors, at0.caught, at0.missed) == (4, 2, 2)
    production = swept["threshold 0.50"]
    assert (production.flagged, production.flagged_by_rule, production.caught) == (3, 1, 2)
    assert production.precision == pytest.approx(2 / 3)
    assert production.flag_rate == pytest.approx(0.3)
    assert production.error_recall == pytest.approx(0.5)
    assert production.cost == pytest.approx(1.5)
    assert production.unflagged_accuracy == pytest.approx(5 / 7)
    at75 = swept["threshold 0.75"]
    assert (at75.flagged, at75.caught, at75.missed) == (6, 3, 1)
    at85 = swept["threshold 0.85"]
    assert (at85.flagged, at85.caught, at85.missed) == (7, 4, 0)
    assert at85.unflagged_accuracy == 1.0


def test_cost_is_infinite_when_nothing_is_caught() -> None:
    result = rtt.RuleResult("r", 4, 2, 2, 0, 2, 0, 2, 0.0)
    assert math.isinf(result.cost) and result.precision == 0.0
    empty = rtt.RuleResult("r", 0, 0, 0, 0, 0, 0, 0, 0.0)
    assert math.isnan(empty.precision) and math.isnan(empty.flag_rate)


def test_alternative_rules(answers: list[rtt.Answer]) -> None:
    rules = {r.rule: r for r in rtt.alternative_rules(answers, runs=1)}
    name = rules["file name disagrees"]
    assert (name.flagged, name.caught, name.missed) == (3, 3, 1)  # the camera name is silent
    silent = rules["file name silent or disagrees"]
    assert (silent.flagged, silent.caught, silent.missed) == (5, 4, 0)
    repeats = rules["repeats disagree"]
    assert (repeats.flagged, repeats.flagged_by_rule, repeats.caught) == (4, 4, 2)
    combined = rules[f"threshold {ROOM_REVIEW_THRESHOLD:.2f} or repeats disagree"]
    assert (combined.flagged, combined.caught, combined.missed) == (6, 4, 0)
    assert "models disagree (across the runs)" not in rules


def test_models_disagree_pairs_runs_by_file_and_repeat(tmp_path: Path) -> None:
    truth = rtt.load_truth(write_labels(tmp_path))
    a, _ = rtt.answers_of(write_run(tmp_path, "run_a"), truth)
    b, _ = rtt.answers_of(write_run(tmp_path, "run_b"), truth)
    # The second run answers the kitchen as a dining room in repeat 1 only.
    b = [
        replace(a_, room_type="dining_room")
        if a_.file == "01_Kitchen.JPG" and a_.repeat == 1
        else a_
        for a_ in b
    ]
    flagged = rtt.disagreement_flags([*a, *b], across="runs")
    assert flagged == {("run_a", "01_Kitchen.JPG", 1), ("run_b", "01_Kitchen.JPG", 1)}
    rules = {r.rule: r for r in rtt.alternative_rules([*a, *b], runs=2)}
    models = rules["models disagree (across the runs)"]
    assert (models.flagged, models.caught) == (2, 1)


def test_calibration_bins_ece_brier_and_auroc(answers: list[rtt.Answer]) -> None:
    calib = rtt.calibration(answers, rtt.DEFAULT_BINS)
    rows = {(b["low"], b["high"]): b for b in calib["bins"]}
    low = rows[0.0, 0.5]
    assert (low["n"], low["right"]) == (3, 1)
    assert low["mean_confidence"] == pytest.approx(0.35)
    assert rows[0.6, 0.7]["accuracy"] == 1.0
    assert rows[0.7, 0.8]["accuracy"] == 0.0
    assert rows[0.98, 1.0]["n"] == 2 and rows[0.98, 1.0]["gap"] == pytest.approx(0.015)
    assert rows[0.5, 0.6]["n"] == 0 and math.isnan(rows[0.5, 0.6]["accuracy"])
    assert calib["accuracy"] == pytest.approx(0.6)
    assert calib["ece"] == pytest.approx(0.248, abs=1e-6)
    assert calib["auroc"] == pytest.approx(0.75)
    brier = (
        sum(
            (
                c
                - (
                    1.0
                    if (f, r)
                    in {
                        ("01_Kitchen.JPG", 1),
                        ("01_Kitchen.JPG", 2),
                        ("02_2nd Floor Bed 2.JPG", 2),
                        ("03_Stairs to Garage.JPG", 1),
                        ("03_Stairs to Garage.JPG", 2),
                        ("IMG_0004.JPG", 1),
                    }
                    else 0.0
                )
            )
            ** 2
            for (f, r), (_, c) in ANSWERS.items()
        )
        / 10
    )
    assert calib["brier"] == pytest.approx(brier)


def test_auroc_ties_count_half_and_degenerate_sets_are_nan() -> None:
    def answer(room_type: str, confidence: float) -> rtt.Answer:
        return rtt.Answer("r", "f", 1, room_type, confidence, "kitchen", None, False, None)

    assert rtt.auroc([answer("kitchen", 0.9), answer("bath", 0.9)]) == 0.5
    assert math.isnan(rtt.auroc([answer("kitchen", 0.9)]))


def test_operating_points(answers: list[rtt.Answer]) -> None:
    thresholds = rtt.DEFAULT_THRESHOLDS
    points = rtt.operating_points(answers, rtt.sweep(answers, thresholds), thresholds, 0.8)
    assert points["useful_range"] == [0.45, 0.8]
    assert points["production_in_useful_range"] is True
    assert points["production_flags_beyond_ambiguous"] == 1
    assert points["smallest_threshold_catching_every_error"] == 0.85
    assert points["smallest_threshold_catching_80pct_of_errors"] == 0.85
    # 0.50 adds the camera photo (right): 2 of 3 flagged are wrong; 0.65 adds the stairs
    # (right, 2 of 5); 0.75 adds a wrong one (3 of 6); 0.85 another (4 of 7). 0.50 wins.
    assert points["best_precision_threshold"] == 0.5


def test_regression_matches_the_production_path(answers: list[rtt.Answer]) -> None:
    check = rtt.regression(answers)
    assert check["equal"] is True and check["recorded_equal"] is True
    assert check["rule_flagged"] == check["production_flagged"] == 3
    assert check["recorded_reasons"] == ["ambiguous", "low_room_confidence:0.45"]


def test_regression_fails_when_the_rule_drifts(
    answers: list[rtt.Answer], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: the sweep's floor moved while production's stays; the check must say so."""
    monkeypatch.setattr(rtt, "ROOM_REVIEW_THRESHOLD", 0.7)
    check = rtt.regression(answers)
    assert check["equal"] is False
    # 0.7 is not below 0.7: the bedroom stays; the two stairs answers (0.6) are added.
    assert check["only_rule"] == ["03_Stairs to Garage.JPG#1", "03_Stairs to Garage.JPG#2"]
    assert check["only_production"] == []


def test_main_writes_the_record_and_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    labels = write_labels(tmp_path)
    run_a = write_run(tmp_path, "run_a")
    run_b = write_run(tmp_path, "run_b", recorded=False, model=False)
    out = tmp_path / "out"
    code = rtt.main(
        ["--run", str(run_a), "--run", str(run_b), "--labels", str(labels), "--out", str(out)]
    )
    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["tree"]["component"] == "room_type_threshold"
    assert manifest["calls"]["count"] == 0 and manifest["calls"]["cost_usd"] == 0.0
    results = json.loads((out / "results.json").read_text())
    outcomes = {c["id"]: c["outcome"] for c in results["cases"]}
    assert outcomes["regression/production-path/run_a"] == "passed"
    assert outcomes["regression/recorded-flag/run_a"] == "passed"
    assert outcomes["regression/recorded-flag/run_b"] == "test_error"  # nothing recorded
    summary = json.loads((out / "run.json").read_text())
    assert summary["answers"] == 20 and summary["spent_usd"] == 0.0
    assert summary["regression"] == {"run_a": True, "run_b": True}
    per_run = json.loads((out / "per_run.json").read_text())
    assert per_run["run_a"]["models"].startswith("`gpt-6-luna`")
    assert per_run["run_b"]["models"] == "not recorded in the run"
    tables = (out / "tables.md").read_text()
    assert "How to read this table" in tables
    assert "None (stored answers, no new call)" in tables
    assert "| threshold 0.50 (production) |" in tables
    assert "models disagree (across the runs)" in tables
    assert "nan" not in (out / "all_runs.json").read_text().lower().replace('"nan"', "")
