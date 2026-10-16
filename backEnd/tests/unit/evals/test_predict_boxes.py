"""``evals.predict_boxes``: the localizer over a dataset or photo directory -> the harness's
detection predictions, counts, raw instances with masks, and the run summary.

Offline: ``make_localizer`` is replaced by :class:`~basis.vision.localize.FakeLocalizer`
scripted with full-image instances (it answers tiles with nothing, so tiling adds calls
but no instances)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from basis.vision.localize import (
    COUNTABLE,
    PHRASES,
    Box,
    FakeLocalizer,
    Instance,
    LocalizerUnavailable,
    Rle,
)
from basis.vision.prep import PreparedImage, prepare_image
from basis.vision.schemas import ComponentLabel
from evals import predict_boxes
from evals.datasets import DetectionBoxes
from evals.run import score_detection_boxes

L = ComponentLabel
W, H = 160, 120


def jpeg(colour: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (W, H), colour).save(out, format="JPEG")
    return out.getvalue()


def inst(label: ComponentLabel, score: float, rect: tuple[int, int, int, int]) -> Instance:
    x1, y1, x2, y2 = rect
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[y1:y2, x1:x2] = 1
    return Instance(
        label=label,
        phrase=PHRASES[label][0],
        score=score,
        box=Box.from_pixels(x1, y1, x2, y2, W, H),
        mask=Rle.encode(mask),
        area=(x2 - x1) * (y2 - y1) / (W * H),
    )


def full_only(*instances: Instance) -> Any:
    def answer(image: PreparedImage) -> list[Instance]:
        return list(instances) if (image.width, image.height) == (W, H) else []

    return answer


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    root = tmp_path / "oi"
    (root / "images").mkdir(parents=True)
    (root / "images" / "a1.jpg").write_bytes(jpeg("white"))
    (root / "images" / "b2.jpg").write_bytes(jpeg("gray"))
    data = {
        "images": [
            {
                "file": "a1.jpg",
                "width": W,
                "height": H,
                "document": "a1",
                "boxes": [{"label": "window", "x1": 0.0, "y1": 0.0, "x2": 0.25, "y2": 0.25}],
            },
            {
                "file": "b2.jpg",
                "width": W,
                "height": H,
                "document": "b2",
                "boxes": [{"label": "door", "x1": 0.5, "y1": 0.0, "x2": 0.75, "y2": 1.0}],
            },
            {"file": "c3.jpg", "width": W, "height": H, "split": "held_out", "boxes": []},
        ]
    }
    (root / "detection_boxes_v1.json").write_text(json.dumps(data))
    return root


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLocalizer:
    localizer = FakeLocalizer(
        {
            "window": full_only(
                inst(L.WINDOW, 0.9, (0, 0, 40, 30)), inst(L.WINDOW, 0.3, (100, 90, 120, 110))
            ),
            "interior door": full_only(inst(L.INTERIOR_DOOR, 0.8, (80, 0, 120, 120))),
            "toilet": full_only(inst(L.TOILET, 0.95, (10, 60, 50, 110))),
        },
        seconds_per_call=1.0,
    )
    monkeypatch.setattr(predict_boxes, "make_localizer", lambda settings, budget: localizer)
    return localizer


async def run(args: list[str]) -> int:
    return await predict_boxes.run(predict_boxes.parse_args(args))


async def test_dataset_run_writes_predictions_the_harness_scores(
    dataset_dir: Path, tmp_path: Path, fake: FakeLocalizer
) -> None:
    out = tmp_path / "out"
    # the fake is scripted with v1 phrase texts ("window", "interior door")
    assert await run(["--dataset", str(dataset_dir), "--out", str(out), "--phrase-set", "v1"]) == 0
    predictions = json.loads((out / "detection_boxes.json").read_text())
    assert set(predictions) == {"a1.jpg", "b2.jpg"}  # the held_out image is not run
    assert predictions["a1.jpg"][0] == {
        "label": "interior_door",
        "score": 0.8,
        "x1": 0.5,
        "y1": 0.0,
        "x2": 0.75,
        "y2": 1.0,
    }
    # Phrases: the dataset's labels (door -> interior and exterior door) and the countable set.
    expected = {t for label in COUNTABLE | {L.WINDOW} for t in PHRASES[label]}
    assert all(set(texts) == expected for _, texts, _ in fake.calls)
    assert {threshold for _, _, threshold in fake.calls} == {0.1}
    counts = json.loads((out / "photo_counts.json").read_text())
    # per-class v1 thresholds (window 0.6, door 0.5): the 0.3 window is not counted
    assert counts["a1.jpg"] == {"interior_door": 1, "window": 1}
    raw = [json.loads(line) for line in (out / "instances_raw.jsonl").read_text().splitlines()]
    assert len(raw) == 2
    first = raw[0]
    assert first["image"]["width"] == W and len(first["instances"]) == 3
    mask = Rle.model_validate(first["instances"][0]["mask"])
    assert (mask.height, mask.width) == (H, W)
    assert first["predict_seconds"] == 1.0 and first["calls"][0]["status"] == "succeeded"
    summary = json.loads((out / "run.json").read_text())
    assert (summary["calls"], summary["phrases_from"], summary["tiling"]) == (2, "dataset", False)
    assert summary["model"] == "vufinder/sam3" and summary["errors"] == []
    assert (summary["phrase_set"], summary["count_threshold"]) == ("v1", "per_class")

    data = DetectionBoxes.model_validate_json((dataset_dir / "detection_boxes_v1.json").read_text())
    rows = {r.metric: r for r in score_detection_boxes(data, "dev", 1, predictions).rows}
    assert rows["ap_50:window"].value == 1.0
    # The fake finds the same interior door on both photos: a false door on a1.jpg (door
    # is exhaustively labelled there) ties with the true one on b2.jpg at 0.8 and ranks
    # first in input order -> precision 1/2 at recall 1.
    assert rows["ap_50:door"].value == 0.5


async def test_tiling_and_repeats(dataset_dir: Path, tmp_path: Path, fake: FakeLocalizer) -> None:
    out = tmp_path / "out"
    args = ["--dataset", str(dataset_dir), "--out", str(out), "--tiling", "on", "--repeats", "2"]
    assert await run([*args, "--merge-metric", "ios"]) == 0
    assert len(fake.calls) == 2 * 2 * 5  # photos x repeats x (full image + four tiles)
    full = {prepare_image(jpeg(c)).sha256 for c in ("white", "gray")}
    assert sum(1 for sha, _, _ in fake.calls if sha not in full) == 2 * 2 * 4  # tile calls
    predictions = json.loads((out / "detection_boxes.json").read_text())
    assert len(predictions["a1.jpg"]["repeats"]) == 2
    summary = json.loads((out / "run.json").read_text())
    assert summary["merge_metric"] == "ios" and summary["calls"] == 20


async def test_photo_directory_with_vlm_labels(tmp_path: Path, fake: FakeLocalizer) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "01_bath.jpg").write_bytes(jpeg("white"))
    (photos / "02_hall.jpg").write_bytes(jpeg("gray"))
    (photos / "notes.txt").write_text("x")
    raw = tmp_path / "photo_raw.jsonl"
    analysis = {"components": [{"label": "toilet"}, {"label": "sink"}, {"label": "other"}]}
    raw.write_text(
        "\n".join(
            [
                json.dumps({"file": "01_bath.jpg", "repeat": 1, "error": "timeout"}),
                json.dumps({"file": "01_bath.jpg", "repeat": 2, "analysis": analysis}),
            ]
        )
    )
    out = tmp_path / "out"
    args = ["--photos", str(photos), "--out", str(out), "--phrases-from", "vlm"]
    assert await run([*args, "--photo-raw", str(raw), "--phrase-set", "v1"]) == 0
    by_image = {sha: set(texts) for sha, texts, _ in fake.calls}
    assert len(by_image) == 2
    countable = {t for label in COUNTABLE for t in PHRASES[label]}
    assert sorted(len(t - countable) for t in by_image.values()) == [0, 2]  # toilet, sink
    records = {
        r["file"]: r
        for r in map(json.loads, (out / "instances_raw.jsonl").read_text().splitlines())
    }
    assert records["01_bath.jpg"]["unlocalized"] == ["sink"]  # named by the VLM, not found
    assert "toilet" in records["01_bath.jpg"]["labels"]
    assert json.loads((out / "run.json").read_text())["notes"] == [
        "1 photos have no VLM labels: countable set only"
    ]


async def test_failures_are_recorded_not_fatal(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(FakeLocalizer):
        async def localize(self, image: PreparedImage, phrases: Any, *, threshold: float) -> Any:
            raise LocalizerUnavailable("HTTP 503 after 4 attempts")

    monkeypatch.setattr(predict_boxes, "make_localizer", lambda settings, budget: Failing())
    (dataset_dir / "images" / "b2.jpg").write_bytes(b"not an image")
    out = tmp_path / "out"
    assert await run(["--dataset", str(dataset_dir), "--out", str(out)]) == 0
    errors = json.loads((out / "run.json").read_text())["errors"]
    assert [e["file"] for e in errors] == ["a1.jpg", "b2.jpg"]
    assert (
        "LocalizerUnavailable" in errors[0]["error"]
        and "not a readable image" in errors[1]["error"]
    )
    assert json.loads((out / "detection_boxes.json").read_text()) == {}


async def test_no_token_no_photos_and_bad_options(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    assert await run(["--dataset", str(dataset_dir), "--out", str(tmp_path / "o")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert await run(["--photos", str(empty), "--out", str(tmp_path / "o")]) == 2
    with pytest.raises(SystemExit):
        predict_boxes.parse_args(["--photos", "p", "--out", "o", "--phrases-from", "vlm"])
    with pytest.raises(SystemExit):
        predict_boxes.parse_args(["--photos", "p", "--out", "o", "--phrases-from", "dataset"])
    with pytest.raises(SystemExit):
        predict_boxes.parse_args(["--photos", "p", "--out", "o", "--threshold", "1.5"])
    with pytest.raises(SystemExit):
        predict_boxes.parse_args(["--photos", "p", "--dataset", "d", "--out", "o"])
    args = predict_boxes.parse_args(["--photos", "p", "--out", "o"])
    assert (args.phrases_from, args.threshold, args.tiling) == ("countable", 0.1, "off")


def test_make_localizer_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from basis.config import load_settings
    from basis.llm.provider import Budget

    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    assert predict_boxes.make_localizer(load_settings(), Budget(remaining_usd=1)) is None
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_dummy-test-value")  # pragma: allowlist secret
    localizer = predict_boxes.make_localizer(load_settings(), Budget(remaining_usd=1))
    assert localizer is not None and getattr(localizer, "version", None)


async def test_phrase_pool_without_dedupe_keeps_every_phrase_instance(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The phrase-pool experiment (EVAL_REPORT section 19): every pool phrase is prompted and
    two phrases of one label finding the same window both keep their instance, each under
    its own phrase, so any per-class subset can be scored offline."""
    same = (0, 0, 40, 30)
    localizer = FakeLocalizer(
        {
            "window": full_only(inst(L.WINDOW, 0.9, same)),
            "house window": full_only(inst(L.WINDOW, 0.7, same)),
        }
    )
    monkeypatch.setattr(predict_boxes, "make_localizer", lambda settings, budget: localizer)
    out = tmp_path / "pool"
    args = ["--dataset", str(dataset_dir), "--out", str(out), "--phrase-set", "pool"]
    assert await run([*args, "--phrase-dedupe", "off"]) == 0
    records = [json.loads(line) for line in (out / "instances_raw.jsonl").read_text().splitlines()]
    a1 = next(r for r in records if r["file"] == "a1.jpg")
    assert sorted((i["phrase"], i["score"]) for i in a1["instances"]) == [
        ("house window", 0.7),
        ("window", 0.9),
    ]
    prompted = set(localizer.calls[0][1])
    assert {"house window", "glass window", "entry door", "cabinet door", "porch light"} <= prompted
    run_json = json.loads((out / "run.json").read_text())
    assert (run_json["phrase_set"], run_json["phrase_dedupe"]) == ("pool", "off")
    # with the pipeline's dedupe the duplicate is kept once, at the higher score
    assert await run([*args, "--out", str(tmp_path / "dedupe")]) == 0
    raw = (tmp_path / "dedupe" / "instances_raw.jsonl").read_text().splitlines()
    kept = next(json.loads(r) for r in raw if json.loads(r)["file"] == "a1.jpg")["instances"]
    assert [(i["phrase"], i["score"]) for i in kept] == [("window", 0.9)]


def test_phrase_dedupe_off_is_full_frame_only() -> None:
    with pytest.raises(SystemExit):
        predict_boxes.parse_args(
            ["--photos", "p", "--out", "o", "--tiling", "on", "--phrase-dedupe", "off"]
        )
    assert predict_boxes.parse_args(["--photos", "p", "--out", "o"]).phrase_dedupe == "on"


async def test_the_default_phrase_set_is_v3_and_counts_use_its_thresholds(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    localizer = FakeLocalizer(
        {
            # v3 window threshold 0.65 (EVAL_REPORT 19): 0.7 counts, 0.62 does not
            "house window": full_only(
                inst(L.WINDOW, 0.7, (0, 0, 40, 30)), inst(L.WINDOW, 0.62, (100, 90, 120, 110))
            ),
            "window": full_only(inst(L.WINDOW, 0.99, (50, 50, 60, 60))),  # v1 text: not prompted
        }
    )
    monkeypatch.setattr(predict_boxes, "make_localizer", lambda settings, budget: localizer)
    out = tmp_path / "out"
    assert await run(["--dataset", str(dataset_dir), "--out", str(out)]) == 0
    prompted = set(localizer.calls[0][1])
    assert "house window" in prompted and "window" not in prompted
    counts = json.loads((out / "photo_counts.json").read_text())
    assert counts["a1.jpg"] == {"window": 1}
    assert (
        await run(["--dataset", str(dataset_dir), "--out", str(out), "--count-threshold", "0.6"])
        == 0
    )
    assert json.loads((out / "photo_counts.json").read_text())["a1.jpg"] == {"window": 2}
    assert json.loads((out / "run.json").read_text())["count_threshold"] == 0.6
