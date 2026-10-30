"""``evals.sam3_select``: per-class SAM 3 phrases and count thresholds chosen offline from a
phrase-pool run, scored by leaving one photo out (EVAL_REPORT section 19)."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest

from basis.vision.localize import PHRASE_SETS, Box, Instance, Rle
from basis.vision.schemas import ComponentLabel
from evals import sam3_select as sel
from evals.metrics import DetectionImage, GoldBox

L = ComponentLabel
W, H = 100, 100


def inst(phrase: str, score: float, rect: tuple[int, int, int, int]) -> Instance:
    x1, y1, x2, y2 = rect
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[y1:y2, x1:x2] = 1
    return Instance(
        label=L.WINDOW,
        phrase=phrase,
        score=score,
        box=Box.from_pixels(x1, y1, x2, y2, W, H),
        mask=Rle.encode(mask),
        area=(x2 - x1) * (y2 - y1) / (W * H),
    )


def gold(file: str, *rects: tuple[int, int, int, int]) -> DetectionImage:
    return DetectionImage(
        file=file,
        boxes=tuple(GoldBox("window", x1 / W, y1 / H, x2 / W, y2 / H) for x1, y1, x2, y2 in rects),
        verified=frozenset({"window"}),
    )


def test_candidates_are_small_subsets_of_the_pool_plus_the_released_tables() -> None:
    found = sel.candidates_for(L.LIGHT_FIXTURE, 2)
    pool = PHRASE_SETS["pool"][L.LIGHT_FIXTURE]
    assert found[: len(pool)] == [(p,) for p in pool]
    assert all(len(c) <= 2 for c in found[:-1])
    # v1 ("light fixture", "ceiling light") is a pair; v2 has five phrases and is appended
    assert ("light fixture", "ceiling light") in found
    assert set(found[-1]) == set(PHRASE_SETS["v2"][L.LIGHT_FIXTURE]) and len(found[-1]) == 5
    assert len(found) == len(set(found))


def test_detections_dedupe_two_phrases_finding_one_object_like_the_localizer() -> None:
    pool = [
        inst("window", 0.9, (0, 0, 20, 20)),
        inst("house window", 0.7, (0, 0, 20, 20)),  # the same object, lower score
        inst("house window", 0.6, (50, 50, 70, 70)),
        inst("glass window", 0.95, (80, 80, 90, 90)),  # not in the subset
    ]
    boxes = sel.detections(pool, L.WINDOW, ("window", "house window"))
    assert sorted(b.score for b in boxes) == [0.6, 0.9]
    assert {b.label for b in boxes} == {"window"}
    assert len(sel.detections(pool, L.WINDOW, ("house window",))) == 2


def test_photo_class_counts_and_hits_at_a_threshold_are_a_prefix() -> None:
    image = gold("a", (0, 0, 20, 20), (50, 50, 70, 70))
    found = sel.detections(
        [
            inst("window", 0.9, (0, 0, 20, 20)),
            inst("window", 0.8, (30, 0, 40, 10)),  # false positive
            inst("window", 0.4, (50, 50, 70, 70)),
        ],
        L.WINDOW,
        ("window",),
    )
    row = sel.photo_class(image, "window", found)
    assert (row.positives, row.gold_count) == (2, 2)
    assert row.at(0.85) == (1, 1)
    assert row.at(0.5) == (2, 1)
    assert row.at(0.3) == (3, 2)


def row(*scored: tuple[float, bool], gold: int) -> sel.PhotoClass:
    return sel.PhotoClass(tuple(sorted(scored, key=lambda s: -s[0])), gold, gold)


def test_threshold_minimizes_count_error_then_f1_then_nearness_to_one_half() -> None:
    rows = [row((0.9, True), (0.55, False), gold=1), row((0.7, True), (0.35, True), gold=2)]
    # 0.3: counts 2+2 vs 1+2 -> error 1; 0.4-0.55: 2+1 -> error 2; 0.6-0.7: 1+1 -> error 1
    # error 1 at 0.30-0.35 (F1 6/7) and at 0.60-0.70 (F1 4/5): F1 decides, 0.35 is nearer 0.5
    assert sel.choose_threshold(rows, sel.DEFAULT_GRID) == 0.35
    flat = [row((0.9, True), gold=1)]
    # every grid value has error 0 and F1 1: the one nearest 0.5 wins
    assert sel.choose_threshold(flat, sel.DEFAULT_GRID) == 0.5


def test_phrases_maximize_ap_and_ties_go_to_fewer_phrases() -> None:
    data = sel.ClassData("window", [("a",), ("b",), ("a", "b")])
    data.table = {
        ("a",): {"p": row((0.9, False), (0.5, True), gold=1)},
        ("b",): {"p": row((0.9, True), gold=1)},
        ("a", "b"): {"p": row((0.9, True), gold=1)},
    }
    assert sel.choose_phrases(data, ["p"]) == ("b",)


def test_leave_one_out_never_chooses_with_the_held_out_photo() -> None:
    """Phrase "a" is perfect on photos p and q and useless on r; "b" is the reverse. The
    choice for each held-out photo is made on the other two, so r is scored with "a" (the
    better phrase on p and q) and gets AP 0 there, which an in-sample choice would hide."""
    data = sel.ClassData("window", [("a",), ("b",)])
    good, bad = row((0.9, True), gold=1), row((0.9, False), gold=1)
    data.table = {
        ("a",): {"p": good, "q": good, "r": bad},
        ("b",): {"p": bad, "q": bad, "r": good},
    }
    result, folds = sel.leave_one_out([data], ["p", "q", "r"], sel.DEFAULT_GRID)
    assert folds["r"]["window"].phrases == ("a",)
    assert folds["p"]["window"].phrases == ("a",)  # p held out: a and b tie on q, r -> fewer, first
    window = result["classes"]["window"]
    assert window["positives"] == 3
    assert window["recall"] == pytest.approx(2 / 3)
    in_sample = sel.evaluate_fixed([data], {"window": sel.Choice(("a",), 0.5)}, ["p", "q", "r"])
    assert in_sample["classes"]["window"]["recall"] == pytest.approx(2 / 3)


def write_pool(root: Path) -> tuple[Path, Path]:
    dataset = root / "detection"
    dataset.mkdir()
    images = [
        {"file": "a.jpg", "width": W, "height": H, "verified": ["window"],
         "boxes": [{"label": "window", "x1": 0.0, "y1": 0.0, "x2": 0.2, "y2": 0.2}]},
        {"file": "b.jpg", "width": W, "height": H, "verified": ["window"],
         "boxes": [{"label": "window", "x1": 0.5, "y1": 0.5, "x2": 0.7, "y2": 0.7}]},
        {"file": "c.jpg", "width": W, "height": H, "verified": ["window"], "boxes": []},
    ]  # fmt: skip
    (dataset / "detection_boxes_v1.json").write_text(json.dumps({"images": images}))
    pool = root / "pool"
    pool.mkdir()
    instances = {
        "a.jpg": [inst("window", 0.9, (0, 0, 20, 20)), inst("house window", 0.8, (60, 0, 80, 20))],
        "b.jpg": [inst("window", 0.7, (50, 50, 70, 70))],
        "c.jpg": [inst("house window", 0.6, (10, 10, 30, 30))],
    }
    lines = [
        json.dumps({"file": f, "repeat": 1, "instances": [i.model_dump(mode="json") for i in v]})
        for f, v in instances.items()
    ]
    (pool / "instances_raw.jsonl").write_text("\n".join(lines) + "\n")
    return dataset, pool


def test_select_writes_the_choice_and_out_of_sample_predictions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset, pool = write_pool(tmp_path)
    out = tmp_path / "out"
    args = ["select", "--pool", str(pool), "--dataset", str(dataset), "--out", str(out)]
    assert sel.main(args) == 0
    report = json.loads((out / "selection.json").read_text())
    assert report["final"]["window"]["phrases"] == ["window"]
    assert report["photos"] == 3
    assert set(report["baselines"]) == {"v1_product", "v1_at_0.5", "v2_at_0.5"}
    loo = report["leave_one_photo_out"]
    assert loo["classes"]["window"]["positives"] == 2
    assert not math.isnan(loo["map_50"])
    boxes = json.loads((out / "detection_boxes.json").read_text())
    assert set(boxes) == {"a.jpg", "b.jpg", "c.jpg"}
    assert all(b["label"] == "window" for v in boxes.values() for b in v)
    assert "leave one photo out" in capsys.readouterr().out
    # The run record: a manifest naming the pool and the labels, no model call, the log.
    assert not (out / "run.json").exists()  # select writes no summary file
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.sam3_select"
    assert manifest["harness"]["options"]["mode"] == "select"
    assert manifest["calls"]["count"] == 0
    assert [(i["name"], i["path"]) for i in manifest["data"]["inputs"]] == [("pool", str(pool))]
    [labels] = manifest["data"]["datasets"]
    assert (labels["name"], labels["version"]) == ("detection_boxes", "v1")
    assert labels["sha256"] is not None
    assert manifest["notes"] == {"candidates": "pool", "max_phrases": 3, "grid_min": 0.3}
    assert manifest["summary"] is None
    assert (manifest["status"], manifest["exit_code"]) == ("ok", 0)
    assert "leave one photo out" in (out / "console.log").read_text()
    # One passed case per class chosen, with the choice; a pool without a manifest files the
    # selection under detection / select-<candidates>.
    results = json.loads((out / "results.json").read_text())
    counts = results["summary"]
    assert (counts["passed"], counts["failed"], counts["test_error"]) == (1, 0, 0)
    [case] = results["cases"]
    assert (case["id"], case["kind"]) == ("window", "class")
    assert case["detail"] == report["final"]["window"]
    assert "results: 1 passed, 0 failed, 0 test errors" in (out / "report.md").read_text()
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == (
        "detection",
        "select-pool",
    )
    # A pool run with a manifest names the version selected from.
    (pool / "manifest.json").write_text(
        json.dumps({"tree": {"component": "detection", "version": "sam3-pool"}})
    )
    assert sel.main([*args[:-1], str(tmp_path / "again")]) == 0
    again = json.loads((tmp_path / "again" / "manifest.json").read_text())
    assert (again["tree"]["component"], again["tree"]["version"]) == (
        "detection",
        "select-sam3-pool",
    )


def test_select_refuses_a_pool_missing_photos(tmp_path: Path) -> None:
    dataset, pool = write_pool(tmp_path)
    lines = (pool / "instances_raw.jsonl").read_text().splitlines()
    (pool / "instances_raw.jsonl").write_text("\n".join(lines[:2]) + "\n")
    args = ["select", "--pool", str(pool), "--dataset", str(dataset), "--out", str(tmp_path / "o")]
    assert sel.main(args) == 2
    manifest = json.loads((tmp_path / "o" / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("ok", 2)
    assert "no pool instances" in (tmp_path / "o" / "console.log").read_text()
    # The photo the pool lacks is a test error: nothing was selected.
    results = json.loads((tmp_path / "o" / "results.json").read_text())
    counts = results["summary"]
    assert (counts["passed"], counts["failed"], counts["test_error"]) == (0, 0, 1)
    assert (results["cases"][0]["id"], results["cases"][0]["kind"]) == ("c.jpg", "photo")
    report = (tmp_path / "o" / "report.md").read_text()
    assert "results: 0 passed, 0 failed, 1 test errors" in report


def test_a_select_that_fails_still_leaves_a_manifest(tmp_path: Path) -> None:
    dataset, _ = write_pool(tmp_path)
    out = tmp_path / "o"
    pool = str(tmp_path / "nope")
    with pytest.raises(FileNotFoundError):
        sel.main(["select", "--pool", pool, "--dataset", str(dataset), "--out", str(out)])
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("error", 1)
    assert manifest["data"]["inputs"][0]["error"] == "not found"


def test_score_mode_counts_at_the_localizers_per_class_thresholds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset, _ = write_pool(tmp_path)
    predictions = tmp_path / "pred"
    predictions.mkdir()
    box = {"label": "window", "x1": 0.0, "y1": 0.0, "x2": 0.2, "y2": 0.2}
    (predictions / "detection_boxes.json").write_text(
        json.dumps({"a.jpg": [{**box, "score": 0.9}], "c.jpg": [{**box, "score": 0.45}]})
    )
    assert sel.main(["score", "--predictions", str(predictions), "--dataset", str(dataset)]) == 0
    assert not (predictions / "manifest.json").exists()  # score writes a report only
    images = sel.gold_images(sel.load_dataset(dataset))
    boxes = sel.read_boxes(predictions / "detection_boxes.json")
    at_class = sel.score_predictions(images, boxes, sel.count_threshold)
    at_low = sel.score_predictions(images, boxes, lambda _: 0.4)
    # c.jpg's 0.45 box is a false positive only when the threshold lets it through
    assert at_low["classes"]["window"]["precision"] == pytest.approx(0.5)
    assert at_class["classes"]["window"]["precision"] == (
        1.0 if sel.count_threshold("window") > 0.45 else 0.5
    )
    assert "mAP@0.5" in capsys.readouterr().out


def test_bad_options_exit() -> None:
    with pytest.raises(SystemExit):
        sel.parse_args(
            ["select", "--pool", "p", "--dataset", "d", "--out", "o", "--max-phrases", "0"]
        )
    with pytest.raises(SystemExit):
        sel.parse_args(["score"])


def test_the_cached_deduper_equals_the_localizers_suppress() -> None:
    """The selector's fast dedupe must give exactly the pipeline's output for every subset:
    random overlapping rectangles from three phrases, including exact duplicates, nested
    masks, ties in score, an empty mask and a missing mask."""
    rng = np.random.default_rng(7)
    phrases = ("window", "house window", "glass window")
    for trial in range(40):
        pool: list[Instance] = []
        for k in range(12):
            x1, y1 = (int(v) for v in rng.integers(0, 70, 2))
            w, h = (int(v) for v in rng.integers(1, 30, 2))
            score = float(rng.choice([0.5, 0.6, round(float(rng.uniform(0.1, 1.0)), 3)]))
            pool.append(inst(phrases[k % 3], score, (x1, y1, x1 + w, y1 + h)))
        pool.append(pool[0].model_copy(update={"phrase": "house window", "score": 0.42}))
        empty = Rle.encode(np.zeros((H, W), dtype=np.uint8))
        pool.append(pool[1].model_copy(update={"mask": empty, "phrase": "glass window"}))
        pool.append(pool[2].model_copy(update={"mask": None, "phrase": "window"}))
        deduper = sel.Deduper(pool, L.WINDOW)
        for size in (1, 2, 3):
            for subset in itertools.combinations(phrases, size):
                assert deduper.detections(subset) == sel.detections(pool, L.WINDOW, subset), (
                    trial,
                    subset,
                )
