"""``evals.fuse_counts``: per-class source selection, leave-one-photo-out, on prediction files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basis.vision.fusion import Rule
from evals import datasets as ds
from evals import fuse_counts


def write_counts(root: Path, name: str, counts: dict[str, Any], spent: float = 1.0) -> Path:
    run = root / name
    run.mkdir()
    (run / "photo_counts.json").write_text(json.dumps(counts))
    (run / "photo_rooms.json").write_text(json.dumps({f: "kitchen" for f in counts}))
    (run / "run.json").write_text(json.dumps({"spent_usd": spent}))
    return run


def test_candidates_list_the_base_first() -> None:
    rules = fuse_counts.candidates("a", ["a", "b"], ["sam3@0.5"])
    assert rules[0] == Rule("pick", ("a",))
    assert Rule("max", ("a", "sam3@0.5")) in rules
    assert Rule("min", ("a", "b")) in rules
    assert Rule("median", ("a", "b", "sam3@0.5")) in rules
    assert len(rules) == 1 + 3 * 2 + 1


def test_sam3_counts_per_threshold(tmp_path: Path) -> None:
    run = tmp_path / "sam"
    run.mkdir()
    instances = [
        {"label": "window", "score": 0.9},
        {"label": "window", "score": 0.45},
        {"label": "interior_door", "score": 0.35},
    ]
    (run / "instances_raw.jsonl").write_text(
        json.dumps({"file": "a.jpg", "instances": instances})
        + "\n"
        + json.dumps({"file": "x"})
        + "\n"
    )
    counts = fuse_counts.sam3_counts(run, [0.3, 0.5])
    assert counts == {
        "sam3@0.3": {"a.jpg": {"window": 2, "interior_door": 1}},
        "sam3@0.5": {"a.jpg": {"window": 1}},
    }


def test_build_examples_cycles_short_sources_and_skips_photos_a_source_lacks() -> None:
    vlm = {"a": {"p": [{"window": 1}, {"window": 2}], "q": [{"window": 5}]}}
    single = {"s": {"p": {"window": 3}}}
    examples = fuse_counts.build_examples(["p", "q"], 2, vlm, single, {"p": {"window": 2}})
    assert list(examples) == ["p"]
    assert [e.by_source["a"]["window"] for e in examples["p"]] == [1, 2]
    assert [e.by_source["s"]["window"] for e in examples["p"]] == [3, 3]


@pytest.fixture
def truth(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, int]]:
    """Six photos: windows true 2, outlets true 1."""
    labels = {f"p{i}.jpg": {"window": 2, "electrical_outlet": 1} for i in range(6)}
    photo_counts = ds.PhotoCounts(
        photos=[ds.PhotoCountLabel(file=f, counts=c) for f, c in labels.items()]
    )
    monkeypatch.setattr(ds, "load", lambda name, directory=None: object())
    monkeypatch.setattr(ds, "photo_counts", lambda boxes: photo_counts)
    monkeypatch.setattr(ds, "DetectionBoxes", object)
    return labels


def test_fuse_picks_the_better_source_per_class_out_of_sample(
    tmp_path: Path, truth: dict[str, dict[str, int]]
) -> None:
    # model a: windows right, outlets missed; model b: windows over by one, outlets right
    a = write_counts(
        tmp_path, "a", {f: {"repeats": [{"window": 2, "sink": 1}] * 2} for f in truth}, 2.0
    )
    b = write_counts(
        tmp_path, "b", {f: {"repeats": [{"window": 3, "electrical_outlet": 1}] * 2} for f in truth}
    )
    out = tmp_path / "out"
    args = fuse_counts.parse_args(["--vlm", f"a={a}", "--vlm", f"b={b}", "--out", str(out)])
    summary = fuse_counts.fuse_counts(args)
    fused = json.loads((out / "photo_counts.json").read_text())
    assert fused["p0.jpg"] == {"repeats": [{"window": 2, "sink": 1, "electrical_outlet": 1}] * 2}
    assert summary["rules_fitted_on_all"]["window"] == "a"
    assert summary["rules_fitted_on_all"]["electrical_outlet"] in ("b", "max(a, b)")
    assert summary["rules_chosen_per_photo"]["window"] == {"a": 6}
    assert summary["spent_usd"] == 3.0
    assert summary["labelled_photos"] == 6
    # rooms copied from the base
    assert json.loads((out / "photo_rooms.json").read_text())["p0.jpg"] == "kitchen"
    assert json.loads((out / "run.json").read_text()) == summary
    # The run record: a manifest naming the sources, no model call, and the log.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.fuse_counts"
    assert manifest["harness"]["options"]["cv"] == "loo"
    assert manifest["calls"]["count"] == 0
    assert [(i["name"], i["path"]) for i in manifest["data"]["inputs"]] == [
        ("a", str(a)),
        ("b", str(b)),
    ]
    assert manifest["data"]["datasets"] == []  # no data directory in this test
    assert manifest["notes"] == {"cv": "loo", "thresholds": []}
    assert manifest["summary"] == summary
    assert (out / "console.log").exists()
    # One passed case per photo fused; no source manifest: unversioned photo_counts.
    results = json.loads((out / "results.json").read_text())
    counts = results["summary"]
    assert (counts["passed"], counts["failed"], counts["test_error"]) == (6, 0, 0)
    assert [c["id"] for c in results["cases"]] == sorted(truth)
    assert results["cases"][0]["detail"] == {"labelled": True, "repeats": 2}
    assert "results: 6 passed, 0 failed, 0 test errors" in (out / "report.md").read_text()
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == (
        "photo_counts",
        "unversioned",
    )


def test_leave_one_out_differs_from_in_sample_when_one_photo_drives_the_choice(
    tmp_path: Path, truth: dict[str, dict[str, int]]
) -> None:
    # a is right on p1..p5 and over-counts p0 by 7; b is right on p0 and one short on
    # p1..p5. On every photo b wins (5 errors against 7; min(a, b) ties b and comes later).
    # Without p0, a is perfect, so held out p0 gets a's 9: one photo drove the choice.
    a_counts = {f: {"window": 2} for f in truth}
    a_counts["p0.jpg"] = {"window": 9}
    b_counts = {f: {"window": 1} for f in truth}
    b_counts["p0.jpg"] = {"window": 2}
    a = write_counts(tmp_path, "a", a_counts)
    b = write_counts(tmp_path, "b", b_counts)
    common = ["--vlm", f"a={a}", "--vlm", f"b={b}", "--classes", "window"]
    loo = fuse_counts.fuse_counts(fuse_counts.parse_args([*common, "--out", str(tmp_path / "l")]))
    insample = fuse_counts.fuse_counts(
        fuse_counts.parse_args([*common, "--cv", "none", "--out", str(tmp_path / "n")])
    )
    assert insample["rules_fitted_on_all"]["window"] == "b"
    l_counts = json.loads((tmp_path / "l" / "photo_counts.json").read_text())
    n_counts = json.loads((tmp_path / "n" / "photo_counts.json").read_text())
    assert n_counts["p0.jpg"] == {"window": 2}
    assert l_counts["p0.jpg"] == {"window": 9}  # held out, a wins on the rest
    assert loo["rules_chosen_per_photo"]["window"] == {"a": 1, "b": 5}


def test_a_sam3_source_and_the_label_file_are_in_the_manifest(
    tmp_path: Path, truth: dict[str, dict[str, int]]
) -> None:
    a = write_counts(tmp_path, "a", {f: {"window": 2} for f in truth})
    sam = tmp_path / "sam"
    sam.mkdir()
    (sam / "instances_raw.jsonl").write_text(
        "".join(
            json.dumps({"file": f, "instances": [{"label": "window", "score": 0.9}]}) + "\n"
            for f in truth
        )
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / ds.DATASET_FILES["detection_boxes"]).write_text("{}")
    # The sources' manifests name what they exercised: the VLM run's prompt, the SAM 3 run's
    # phrase set (filed under detection).
    (a / "manifest.json").write_text(
        json.dumps(
            {"components": {"prompts": [{"name": "photo_countables", "version": "v2", "calls": 6}]}}
        )
    )
    (sam / "manifest.json").write_text(
        json.dumps({"tree": {"component": "detection", "version": "sam3-v3"}})
    )
    out = tmp_path / "out"
    args = fuse_counts.parse_args(
        ["--vlm", f"a={a}", "--sam3", str(sam), "--thresholds", "0.5", "--data-dir", str(data),
         "--out", str(out)]
    )  # fmt: skip
    fuse_counts.fuse_counts(args)
    manifest = json.loads((out / "manifest.json").read_text())
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["a", "sam3"]
    assert manifest["data"]["inputs"][1]["files"] == 2  # instances_raw.jsonl and its manifest
    [dataset] = manifest["data"]["datasets"]
    assert (dataset["name"], dataset["version"]) == ("detection_boxes", "v1")
    assert manifest["notes"]["thresholds"] == [0.5]
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == (
        "photo_counts",
        "photo_countables-v2+sam3-v3",
    )
    assert json.loads((out / "results.json").read_text())["summary"]["passed"] == 6


def test_a_failed_fusion_still_leaves_a_manifest(
    tmp_path: Path, truth: dict[str, dict[str, int]]
) -> None:
    out = tmp_path / "out"
    args = fuse_counts.parse_args(["--vlm", f"a={tmp_path / 'missing'}", "--out", str(out)])
    with pytest.raises(FileNotFoundError):
        fuse_counts.fuse_counts(args)
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("error", 1)
    assert "FileNotFoundError" in manifest["error"]


def test_source_names_must_be_distinct(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        fuse_counts.parse_args(
            ["--vlm", f"a={tmp_path}", "--counts", f"a={tmp_path}", "--out", "o"]
        )
    with pytest.raises(SystemExit):
        fuse_counts.parse_args(["--vlm", "nodir", "--out", "o"])
