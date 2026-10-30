"""``evals.predict_detector`` (trained segmenters and open-vocabulary detectors in the SAM 3
pool format) and ``evals.sam3_select --candidates observed`` (EVAL_REPORT section 26).

The models themselves are not loaded here (gigabytes of weights); the runner is exercised
with injected fakes and the pure helpers with hand-built masks and boxes."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from basis.vision.localize import Box, Instance, Rle
from basis.vision.schemas import ComponentLabel
from evals import predict_detector as pd
from evals import sam3_select as sel
from evals.metrics import DetectionImage, GoldBox

L = ComponentLabel


def mask(h: int, w: int, rect: tuple[int, int, int, int]) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    x1, y1, x2, y2 = rect
    m[y1:y2, x1:x2] = True
    return m


def test_mask_box_is_normalized_with_exclusive_ends() -> None:
    box = pd.mask_box(mask(10, 20, (5, 2, 10, 4)))
    assert box == Box(x1=0.25, y1=0.2, x2=0.5, y2=0.4)
    assert pd.mask_box(np.zeros((3, 3), dtype=bool)) is None


def test_segments_map_to_labels_by_scene_and_unmapped_or_weak_ones_are_dropped() -> None:
    segments = [
        pd.Segment("light", 0.9, mask(10, 10, (0, 0, 2, 2))),
        pd.Segment("windowpane", 0.6, mask(10, 10, (2, 2, 6, 6))),
        pd.Segment("sofa", 0.99, mask(10, 10, (0, 5, 10, 10))),
        pd.Segment("door", 0.01, mask(10, 10, (6, 0, 9, 9))),
    ]
    inside = pd.segment_instances(segments, exterior=False)
    assert [(i.label, i.phrase, i.score) for i in inside] == [
        (L.LIGHT_FIXTURE, "light", 0.9),
        (L.WINDOW, "windowpane", 0.6),
    ]
    assert inside[0].mask is not None and inside[0].mask.area() == 4
    assert inside[1].area == 0.16
    outside = pd.segment_instances(segments, exterior=True)
    assert outside[0].label is L.OUTDOOR_LIGHTING


def test_surface_masks_union_each_surface_class() -> None:
    segments = [
        pd.Segment("wall", 1.0, mask(4, 4, (0, 0, 2, 4))),
        pd.Segment("wall", 0.8, mask(4, 4, (2, 0, 3, 4))),
        pd.Segment("floor", 1.0, mask(4, 4, (3, 0, 4, 4))),
        pd.Segment("cabinet", 1.0, mask(4, 4, (3, 0, 4, 1))),
    ]
    found = pd.surface_masks(segments)
    assert sorted(found) == ["floor", "wall"]
    assert found["wall"].area() == 12 and found["floor"].area() == 4
    areas = pd.class_areas(segments)
    assert areas["wall"] == 0.75 and areas["cabinet"] == 0.0625


def test_nms_keeps_the_higher_score_of_two_overlapping_boxes() -> None:
    a = Box(x1=0.0, y1=0.0, x2=0.5, y2=0.5)
    b = Box(x1=0.05, y1=0.0, x2=0.5, y2=0.5)  # IoU 0.9 with a
    c = Box(x1=0.6, y1=0.6, x2=0.9, y2=0.9)
    assert pd.nms([(0.4, a), (0.8, b), (0.3, c)]) == [(0.8, b), (0.3, c)]
    found = pd.box_instances("house window", L.WINDOW, [(0.4, a), (0.8, b), (0.01, c)])
    assert [(i.phrase, i.score) for i in found] == [("house window", 0.8)]


def test_pool_phrases_cover_the_seven_counted_classes() -> None:
    labels = {label for _, label in pd.pool_phrases()}
    assert labels == set(pd.COUNTABLE_POOL)
    assert ("house window", L.WINDOW) in pd.pool_phrases()


def _photos(tmp_path: Path, names: Sequence[str]) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    for name in names:
        Image.new("RGB", (20, 10), (200, 200, 200)).save(folder / name)
    return folder


def test_runner_writes_pool_records_surfaces_and_label_sets_for_a_segmenter(
    tmp_path: Path,
) -> None:
    photos = _photos(tmp_path, ["01_a.jpg", "02_b.jpg"])

    def fake(image: Image.Image) -> list[pd.Segment]:
        return [
            pd.Segment("sky", 1.0, mask(10, 20, (0, 0, 20, 3))),
            pd.Segment("door", 0.7, mask(10, 20, (5, 3, 9, 10))),
            pd.Segment("wall", 1.0, mask(10, 20, (10, 3, 20, 10))),
        ]

    out = tmp_path / "out"
    args = argparse.Namespace(
        photos=photos, out=out, detector="mask2former", checkpoint=None, device="auto", limit=None
    )
    assert pd.run(args, segmenter=fake) == 0
    records = [json.loads(line) for line in (out / "instances_raw.jsonl").read_text().splitlines()]
    assert [r["file"] for r in records] == ["01_a.jpg", "02_b.jpg"]
    assert records[0]["exterior"] is True  # 30% sky
    assert [i["label"] for i in records[0]["instances"]] == ["exterior_door"]
    surfaces = json.loads((out / "surfaces_raw.jsonl").read_text().splitlines()[0])
    assert list(surfaces["surfaces"]) == ["wall"]
    assert Rle.model_validate(surfaces["surfaces"]["wall"]).area() == 70
    assert json.loads((out / "photo_components.json").read_text())["01_a.jpg"] == ["exterior_door"]
    run = json.loads((out / "run.json").read_text())
    assert run["detector"] == "mask2former" and run["cost_usd"] == 0.0
    # The run record (evals.record): a manifest and a log, no paid call and so no ledger.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_detector"
    assert manifest["summary"] == run and manifest["status"] == "ok"
    assert manifest["calls"]["count"] == 0 and manifest["components"]["prompts"] == []
    assert manifest["notes"] == {
        "detector": "mask2former",
        "checkpoint": pd.SEGMENTERS["mask2former"],
        "device": "fake",
    }
    assert [(i["name"], i["files"]) for i in manifest["data"]["inputs"]] == [("photos", 2)]
    assert manifest["files"]["ledger"] is None and not (out / "llm_calls.jsonl").exists()
    assert f"wrote {out}" in (out / "console.log").read_text()
    # One passed case per photo; the run files under detection / <detector>.
    results = json.loads((out / "results.json").read_text())
    summary = results["summary"]
    assert (summary["passed"], summary["failed"], summary["test_error"]) == (2, 0, 0)
    assert [(c["id"], c["detail"]) for c in results["cases"]] == [
        ("01_a.jpg#1", {"instances": 1}),
        ("02_b.jpg#1", {"instances": 1}),
    ]
    assert "results: 2 passed, 0 failed, 0 test errors" in (out / "report.md").read_text()
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == (
        "detection",
        "mask2former",
    )
    # the pool loader of sam3_select reads the output as it reads a SAM 3 pool run
    pool = sel.load_pool(out / "instances_raw.jsonl")
    assert pool["02_b.jpg"][0].phrase == "door"


def test_runner_writes_no_surfaces_for_an_open_vocabulary_detector(tmp_path: Path) -> None:
    photos = _photos(tmp_path, ["01_a.jpg"])
    seen: list[list[str]] = []

    def fake(image: Image.Image, phrases: Sequence[tuple[str, ComponentLabel]]) -> list[Instance]:
        seen.append([p for p, _ in phrases])
        return pd.box_instances("window", L.WINDOW, [(0.5, Box(x1=0, y1=0, x2=0.5, y2=0.5))])

    out = tmp_path / "out"
    args = argparse.Namespace(
        photos=photos, out=out, detector="owlv2", checkpoint=None, device="auto", limit=None
    )
    assert pd.run(args, detector=fake) == 0
    assert not (out / "surfaces_raw.jsonl").exists()
    assert seen[0] == [p for p, _ in pd.pool_phrases()]
    assert json.loads((out / "run.json").read_text())["phrases"] == seen[0]
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == ("detection", "owlv2")
    assert json.loads((out / "results.json").read_text())["summary"]["passed"] == 1


def test_a_detector_that_raises_leaves_its_case_and_files_under_the_checkpoint(
    tmp_path: Path,
) -> None:
    """The second photo makes the detector raise: the run stops as before (the exception
    propagates, the manifest says error), with the first photo passed and the second a test
    error on record; an overridden checkpoint is part of the version."""
    photos = _photos(tmp_path, ["01_a.jpg", "02_b.jpg"])
    calls: list[str] = []

    def fake(image: Image.Image, phrases: Sequence[tuple[str, ComponentLabel]]) -> list[Instance]:
        calls.append("x")
        if len(calls) > 1:
            raise RuntimeError("MPS backend out of memory")
        return []

    out = tmp_path / "out"
    args = argparse.Namespace(
        photos=photos, out=out, detector="gdino", checkpoint="org/ckpt", device="auto", limit=None
    )
    with pytest.raises(RuntimeError, match="out of memory"):
        pd.run(args, detector=fake)
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("error", 1)
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == (
        "detection",
        "gdino@org_ckpt",
    )
    results = json.loads((out / "results.json").read_text())
    summary = results["summary"]
    assert (summary["passed"], summary["failed"], summary["test_error"]) == (1, 0, 1)
    by_id = {c["id"]: c for c in results["cases"]}
    assert by_id["01_a.jpg#1"]["outcome"] == "passed"
    assert by_id["02_b.jpg#1"]["outcome"] == "test_error"
    assert "out of memory" in by_id["02_b.jpg#1"]["observed_message"]
    assert "results: 1 passed, 0 failed, 1 test errors" in (out / "report.md").read_text()
    assert pd.detector_version("owlv2", None) == "owlv2"
    assert pd.detector_version("owlv2", "google/owlv2-base") == "owlv2@google_owlv2-base"


def _inst(label: ComponentLabel, phrase: str, score: float, x: float) -> Instance:
    return Instance(
        label=label,
        phrase=phrase,
        score=score,
        box=Box(x1=x, y1=0.0, x2=x + 0.1, y2=0.1),
        area=0.01,
    )


def test_observed_candidates_are_the_class_phrases_the_pool_used() -> None:
    pool = {
        "a.jpg": [
            _inst(L.LIGHT_FIXTURE, "light", 0.9, 0.0),
            _inst(L.LIGHT_FIXTURE, "lamp", 0.5, 0.3),
        ],
        "b.jpg": [_inst(L.OUTDOOR_LIGHTING, "light", 0.8, 0.0)],
    }
    assert sel.observed_candidates(pool, L.LIGHT_FIXTURE, 2) == [
        ("lamp",),
        ("light",),
        ("lamp", "light"),
    ]
    assert sel.observed_candidates(pool, L.OUTDOOR_LIGHTING, 2) == [("light",)]
    assert sel.observed_candidates(pool, L.WINDOW, 2) == [()]


def test_observed_build_reads_only_instances_of_the_class_label() -> None:
    # "light" stands for an interior fixture in a.jpg and an outdoor light in b.jpg
    pool = {
        "a.jpg": [_inst(L.LIGHT_FIXTURE, "light", 0.9, 0.0)],
        "b.jpg": [_inst(L.OUTDOOR_LIGHTING, "light", 0.8, 0.0)],
    }
    images = [
        DetectionImage(
            file=f,
            boxes=(GoldBox("light_fixture", 0.0, 0.0, 0.1, 0.1),) if f == "a.jpg" else (),
            verified=frozenset({"light_fixture", "outdoor_lighting"}),
        )
        for f in ("a.jpg", "b.jpg")
    ]
    classes = sel.build(
        images,
        pool,
        [L.LIGHT_FIXTURE],
        2,
        candidates=lambda label: sel.observed_candidates(pool, label, 2),
    )
    table = classes[0].table[("light",)]
    assert table["a.jpg"].scored == ((0.9, True),)
    assert table["b.jpg"].scored == ()  # b's "light" is an outdoor light, not counted here
