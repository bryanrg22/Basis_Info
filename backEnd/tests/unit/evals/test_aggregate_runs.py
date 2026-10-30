"""``evals.aggregate_runs``: self-consistency (median, vote, max) over pooled repeats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals import aggregate_runs


def write_run(root: Path, name: str, files: dict[str, Any], spent: float = 1.0) -> Path:
    run = root / name
    run.mkdir()
    for key, data in files.items():
        (run / f"{key}.json").write_text(json.dumps(data))
    (run / "run.json").write_text(json.dumps({"spent_usd": spent}))
    return run


def read(out: Path, name: str) -> Any:
    return json.loads((out / f"{name}.json").read_text())


@pytest.mark.parametrize(
    ("values", "rule", "expected"),
    [
        ([1, 3, 2], "median", 2),
        ([1, 2], "median", 1),  # lower median: a value a sample gave, never rounded up
        ([4, 1, 1, 2], "median", 1),
        ([2, 2, 5], "vote", 2),
        ([1, 3], "vote", 1),  # tie: lower median of the tied counts
        ([0, 3, 3, 1, 1], "vote", 1),
        ([0, 4, 1], "max", 4),
        ([7], "median", 7),
    ],
)
def test_aggregate_rules(values: list[int], rule: str, expected: int) -> None:
    assert aggregate_runs.aggregate(values, rule) == expected


def test_aggregate_rejects_unknown_rule_and_empty_input() -> None:
    with pytest.raises(ValueError, match="unknown rule"):
        aggregate_runs.aggregate([1], "mean")
    with pytest.raises(ValueError, match="nothing"):
        aggregate_runs.aggregate([], "median")


def three_repeat_run(root: Path) -> Path:
    return write_run(
        root,
        "sol",
        {
            "photo_counts": {
                "a.jpg": {
                    "repeats": [
                        {"window": 3, "door": 1},
                        {"window": 5},
                        {"window": 4, "door": 1},
                    ]
                },
                "b.jpg": {"window": 1},
            },
            "photo_components": {
                "a.jpg": {"repeats": [["door", "window"], ["window"], ["door", "window", "x"]]},
                "b.jpg": ["window"],
            },
            "photo_rooms": {"a.jpg": {"repeats": ["kitchen", "dining", "dining"]}, "b.jpg": "bath"},
        },
        spent=2.5,
    )


def test_median_over_three_repeats(tmp_path: Path) -> None:
    run = three_repeat_run(tmp_path)
    summary = aggregate_runs.aggregate_runs([run], tmp_path / "out", "median")
    out = tmp_path / "out"
    # missing label counts 0 in that sample: door [1, 0, 1] -> 1; window [3, 5, 4] -> 4
    assert read(out, "photo_counts") == {"a.jpg": {"door": 1, "window": 4}, "b.jpg": {"window": 1}}
    # presence by majority: x appears in 1 of 3 samples
    assert read(out, "photo_components") == {"a.jpg": ["door", "window"], "b.jpg": ["window"]}
    assert read(out, "photo_rooms") == {"a.jpg": "dining", "b.jpg": "bath"}
    assert summary["samples_per_photo"] == [1, 3]
    assert summary["spent_usd"] == 2.5
    assert summary["rule"] == "median"
    assert json.loads((out / "run.json").read_text()) == summary
    # The run record: a manifest naming the source run, no model call, and the log.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.aggregate_runs"
    assert manifest["calls"]["count"] == 0
    assert [(i["name"], i["path"]) for i in manifest["data"]["inputs"]] == [("run1", str(run))]
    assert manifest["notes"]["rule"] == "median" and manifest["notes"]["by_repeat"] is False
    assert manifest["notes"]["sources"] == [{"run": str(run), "manifest_id": None}]
    assert manifest["summary"] == summary
    assert (out / "run.log").exists()


def test_max_is_the_union_and_the_largest_count(tmp_path: Path) -> None:
    run = three_repeat_run(tmp_path)
    aggregate_runs.aggregate_runs([run], tmp_path / "out", "max")
    out = tmp_path / "out"
    assert read(out, "photo_counts")["a.jpg"] == {"door": 1, "window": 5}
    assert read(out, "photo_components")["a.jpg"] == ["door", "window", "x"]


def test_zero_aggregate_drops_the_label_from_counts(tmp_path: Path) -> None:
    run = write_run(
        tmp_path,
        "r",
        {"photo_counts": {"a.jpg": {"repeats": [{"outlet": 2}, {}, {}]}}},
    )
    aggregate_runs.aggregate_runs([run], tmp_path / "out", "vote")
    assert read(tmp_path / "out", "photo_counts") == {"a.jpg": {}}
    # no photo_components or photo_rooms in the source: written empty, not invented
    assert read(tmp_path / "out", "photo_components") == {}
    assert read(tmp_path / "out", "photo_rooms") == {}


def test_runs_are_pooled_and_room_ties_go_to_the_first_run(tmp_path: Path) -> None:
    first = write_run(
        tmp_path,
        "sol",
        {
            "photo_counts": {"a.jpg": {"repeats": [{"window": 2}, {"window": 2}]}},
            "photo_rooms": {"a.jpg": {"repeats": ["kitchen", "kitchen"]}},
        },
        spent=1.0,
    )
    second = write_run(
        tmp_path,
        "opus",
        {
            "photo_counts": {"a.jpg": {"repeats": [{"window": 3}, {"window": 3}]}},
            "photo_rooms": {"a.jpg": {"repeats": ["dining", "dining"]}},
        },
        spent=3.0,
    )
    summary = aggregate_runs.aggregate_runs([first, second], tmp_path / "out", "median")
    assert read(tmp_path / "out", "photo_counts") == {"a.jpg": {"window": 2}}
    assert read(tmp_path / "out", "photo_rooms") == {"a.jpg": "kitchen"}
    assert summary["samples_per_photo"] == [4]
    assert summary["spent_usd"] == 4.0
    aggregate_runs.aggregate_runs([second, first], tmp_path / "out2", "median")
    assert read(tmp_path / "out2", "photo_rooms") == {"a.jpg": "dining"}


def test_main_writes_the_output(tmp_path: Path) -> None:
    run = three_repeat_run(tmp_path)
    assert (
        aggregate_runs.main(["--runs", str(run), "--out", str(tmp_path / "o"), "--rule", "vote"])
        == 0
    )
    assert json.loads((tmp_path / "o" / "run.json").read_text())["rule"] == "vote"


def test_by_repeat_aggregates_repeat_i_of_every_run(tmp_path: Path) -> None:
    runs = [
        write_run(
            tmp_path,
            name,
            {
                "photo_counts": {"a.jpg": {"repeats": [{"window": w1}, {"window": w2}]}},
                "photo_components": {"a.jpg": {"repeats": [["window"], ["window", name]]}},
                "photo_rooms": {"a.jpg": {"repeats": ["kitchen", room2]}},
            },
        )
        for name, w1, w2, room2 in (
            ("x", 1, 4, "dining"),
            ("y", 2, 6, "kitchen"),
            ("z", 3, 5, "dining"),
        )
    ]
    summary = aggregate_runs.aggregate_runs(runs, tmp_path / "out", "median", by_repeat=True)
    out = tmp_path / "out"
    assert read(out, "photo_counts") == {"a.jpg": {"repeats": [{"window": 2}, {"window": 5}]}}
    assert read(out, "photo_rooms") == {"a.jpg": {"repeats": ["kitchen", "dining"]}}
    # x, y, z each in one of three samples of repeat 2: not a majority
    assert read(out, "photo_components") == {"a.jpg": {"repeats": [["window"], ["window"]]}}
    assert summary["samples_per_photo"] == [3]
    assert summary["by_repeat"] is True
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["notes"]["by_repeat"] is True
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["run1", "run2", "run3"]
