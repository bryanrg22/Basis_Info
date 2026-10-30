"""``evals.manual_add`` on synthetic photos: drawn boxes, which objects count as missed, the
outcomes of a box (stored candidate, SAM 3's saved answer, the box alone, a duplicate,
no saved answer), the guard measure, the summary and the gates."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from basis.vision.localize import Box, Instance, Rle
from basis.vision.schemas import ComponentLabel
from basis.vision.selection import Stored
from evals import manual_add as M

L = ComponentLabel
SIZE = 100


def inst(label: str, score: float, x1: int, y1: int, x2: int, y2: int) -> Instance:
    grid = np.zeros((SIZE, SIZE), dtype=bool)
    grid[y1:y2, x1:x2] = True
    rle = Rle.encode(grid)
    return Instance(
        label=L(label),
        phrase=label,
        score=score,
        box=Box.from_pixels(x1, y1, x2, y2, SIZE, SIZE),
        mask=rle,
        area=rle.area() / SIZE**2,
    )


def gold(label: str, x1: float, y1: float, x2: float, y2: float) -> dict[str, object]:
    return {"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def test_drawn_boxes_are_scaled_shifted_and_clamped() -> None:
    box = Box(x1=0.2, y1=0.2, x2=0.4, y2=0.6)
    exact = M.drawn(box, "exact")
    assert (exact.x1, exact.y1, exact.x2, exact.y2) == pytest.approx((0.2, 0.2, 0.4, 0.6))
    big = M.drawn(box, "big")
    assert (big.x2 - big.x1, big.y2 - big.y1) == pytest.approx((0.23, 0.46))
    assert ((big.x1 + big.x2) / 2, (big.y1 + big.y2) / 2) == pytest.approx((0.32, 0.44))
    small = M.drawn(box, "small")
    assert ((small.x1 + small.x2) / 2, (small.y1 + small.y2) / 2) == pytest.approx((0.28, 0.36))
    edge = M.drawn(Box(x1=0.9, y1=0.9, x2=1.0, y2=1.0), "big")
    assert (edge.x2, edge.y2) == (1.0, 1.0)


def test_stored_rows_and_counted_targets_follow_the_product() -> None:
    raw = [
        inst("window", 0.9, 10, 10, 30, 30).model_dump(mode="json"),  # counted (>= 0.65)
        inst("window", 0.5, 60, 10, 80, 30).model_dump(mode="json"),  # a candidate
        inst("window", 0.2, 10, 60, 30, 80).model_dump(mode="json"),  # under 0.25: not stored
    ]
    stored = M.stored_instances(raw)
    assert [(s.instance.score, s.counted) for s in stored] == [(0.9, True), (0.5, False)]
    targets = M.counted_targets(
        0, stored, [gold("window", 0.1, 0.1, 0.3, 0.3), gold("window", 0.6, 0.1, 0.8, 0.3)]
    )
    assert [(t.label, t.counted) for t in targets] == [("window", True), ("window", False)]


def photo() -> M.Photo:
    stored = [
        Stored(inst("window", 0.9, 10, 10, 30, 30), counted=True),
        Stored(inst("window", 0.5, 60, 10, 80, 30), counted=False),
    ]
    targets = [
        M.Target(0, "window", Box(x1=0.1, y1=0.1, x2=0.3, y2=0.3), counted=True),
        M.Target(0, "window", Box(x1=0.6, y1=0.1, x2=0.8, y2=0.3), counted=False),  # candidate
        M.Target(0, "window", Box(x1=0.1, y1=0.6, x2=0.3, y2=0.8), counted=False),  # nothing
    ]
    return M.Photo(index=0, file="x.jpg", sha256="s", stored=stored, targets=targets)


def answers(found: list[Instance]) -> M.SavedPrompts:
    """SAM 3's saved answer for the third target, every variant, one phrase."""
    target = photo().targets[2]
    return {M.prompt_key(target, v): {"house window": found} for v in M.VARIANTS}


def outcomes(records: list[dict[str, object]], index: int) -> dict[str, object]:
    box = photo().targets[index].box
    rows = [r for r in records if r["box"] == [box.x1, box.y1, box.x2, box.y2]]
    return {str(r["selection"]): r["outcome"] for r in rows}


def test_a_box_resolves_to_a_candidate_sam_the_box_alone_or_a_duplicate() -> None:
    p = photo()
    assert not M.needs_prompt(p, p.targets[0], "exact")
    assert not M.needs_prompt(p, p.targets[1], "exact")
    assert M.needs_prompt(p, p.targets[2], "exact")
    unanswered = M.evaluate([p], {})
    assert outcomes(unanswered, 0)["box_exact"] == "duplicate"  # counted: nothing added
    assert outcomes(unanswered, 1)["box_exact"] == "correct"  # the stored candidate
    assert outcomes(unanswered, 2)["box_exact"] == "unprompted"
    assert outcomes(unanswered, 2)["tap"] == "nothing"
    right = inst("window", 0.8, 10, 60, 30, 80)
    elsewhere = inst("window", 0.95, 60, 60, 80, 80)
    found = M.evaluate([p], answers([elsewhere, right]))
    assert outcomes(found, 2)["box_exact"] == "correct"
    alone = M.evaluate([p], answers([elsewhere]))
    assert outcomes(alone, 2)["box_exact"] == "box_only"
    faint = M.evaluate([p], answers([right.model_copy(update={"score": 0.4})]))
    assert outcomes(faint, 2)["box_exact"] == "box_only"  # under the 0.5 threshold


def test_summary_guard_measure_and_gates() -> None:
    p = photo()
    records = M.evaluate([p], answers([inst("window", 0.8, 10, 60, 30, 80)]))
    summary = M.summarize(records)
    exact = summary["misses"]["box_exact"]
    assert (exact["correct"]["k"], exact["correct"]["n"], exact["wrong"]["k"]) == (2, 2, 0)
    assert summary["counted"]["boxes_all"]["duplicates_created"]["k"] == 0
    assert M.gates(summary) == []
    # A counted object's box never reaches the guard (it is the counted instance).
    assert all(
        r["guard_overlap"] is None
        for r in records
        if r["counted"] and r["selection"] == "box_exact"
    )
    shifted = M.evaluate([p], answers([inst("window", 0.8, 10, 57, 30, 77)]))  # IoU 0.74
    by_box = {r["selection"]: r for r in shifted if r["box"] == [0.1, 0.6, 0.3, 0.8]}
    assert by_box["box_exact"]["outcome"] == "correct"
    failing = M.summarize(M.evaluate([p], {}))
    assert any("exact box outlines 1/2" in f for f in M.gates(failing))
    assert "| box_exact |" in M.table(summary)


def test_saved_answers_round_trip(tmp_path: Path) -> None:
    found = inst("window", 0.8, 10, 60, 30, 80)
    path = tmp_path / "prompted.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "key": "k:exact",
                        "phrase": "house window",
                        "instances": [found.model_dump(mode="json")],
                    },
                    {"key": "k:exact", "phrase": "window", "instances": []},
                ]
            }
        )
    )
    loaded = M.load_prompts(path)
    assert list(loaded["k:exact"]) == ["house window", "window"]
    assert loaded["k:exact"]["house window"] == [found]
    assert M.load_prompts(tmp_path / "missing.json") == {}
    box = Box(x1=0.1, y1=0.6, x2=0.3, y2=0.8)
    assert M.prompted_instance(loaded["k:exact"], box, "first") == found.model_copy(
        update={"source": "manual"}
    )
    assert M.prompted_instance(None, box, "all") == "missing"


def test_main_replays_a_data_directory_and_leaves_the_run_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The photo of :func:`photo` as a data directory: the labels, the stored run, and the
    saved SAM 3 answer for the one miss. The gates pass, and ``--out`` holds the report
    plus the run record (manifest naming the labels and the stored runs, and the log)."""
    data = tmp_path / "data"
    stored = [
        inst("window", 0.9, 10, 10, 30, 30).model_dump(mode="json"),
        inst("window", 0.5, 60, 10, 80, 30).model_dump(mode="json"),
    ]
    (data / M.LABELS_FILE).parent.mkdir(parents=True)
    (data / M.LABELS_FILE).write_text(
        json.dumps(
            {
                "images": [
                    {
                        "file": "x.jpg",
                        "boxes": [
                            gold("window", 0.1, 0.1, 0.3, 0.3),
                            gold("window", 0.6, 0.1, 0.8, 0.3),
                            gold("window", 0.1, 0.6, 0.3, 0.8),
                        ],
                    }
                ]
            }
        )
    )
    first, second = (data / run / "instances_raw.jsonl" for run in M.SAM3_RUNS)
    first.parent.mkdir(parents=True)
    first.write_text(
        json.dumps({"file": "x.jpg", "instances": stored, "image": {"sha256": "s"}}) + "\n"
    )
    second.parent.mkdir(parents=True)
    second.write_text("")
    prompts = data / M.PROMPTS_FILE
    prompts.parent.mkdir(parents=True)
    right = inst("window", 0.8, 10, 60, 30, 80).model_dump(mode="json")
    miss = M.Target(0, "window", Box(x1=0.1, y1=0.6, x2=0.3, y2=0.8), counted=False)
    prompts.write_text(
        json.dumps(
            {
                "records": [
                    {"key": M.prompt_key(miss, v), "phrase": "house window", "instances": [right]}
                    for v in M.VARIANTS
                ]
            }
        )
    )
    out = tmp_path / "out"
    assert M.main(["--data", str(data), "--out", str(out)]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["gates"] == {"passed": True, "failures": [], "phrases": "all"}
    assert len(json.loads((out / "records.json").read_text())) > 0
    assert "gates: passed" in capsys.readouterr().out
    # The run record: no run.json (summary.json is the report), a manifest, the log.
    assert not (out / "run.json").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.manual_add"
    assert manifest["harness"]["options"]["phrases"] == "all"
    assert manifest["calls"]["count"] == 0
    assert [(i["name"], i["path"]) for i in manifest["data"]["inputs"]] == [
        (M.SAM3_RUNS[0], str(first)),
        (M.SAM3_RUNS[1], str(second)),
        ("prompts", str(prompts)),
    ]
    [labels] = manifest["data"]["datasets"]
    assert (labels["name"], labels["version"], labels["path"]) == (
        "detection_boxes",
        "v1",
        str(data / M.LABELS_FILE),
    )
    assert manifest["notes"] == {"phrases": "all", "live": False, "gates": summary["gates"]}
    assert (manifest["status"], manifest["exit_code"]) == ("ok", 0)
    assert "gates: passed" in (out / "run.log").read_text()


def test_a_failed_replay_still_leaves_a_manifest(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with pytest.raises(FileNotFoundError):
        M.main(["--data", str(tmp_path / "nope"), "--out", str(out)])
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("error", 1)
    assert "FileNotFoundError" in manifest["error"]
    assert (out / "run.log").exists()
