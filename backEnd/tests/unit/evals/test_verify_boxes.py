"""``evals.verify_boxes``: a vision model confirms SAM 3 detections one by one; count the
confirmed ones."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from llm_fakes import PHOTO_MODEL, PHOTO_USAGE
from PIL import Image

from basis.llm import prompts
from basis.llm.pool import ProviderPool
from basis.llm.provider import ChatRequest, ChatResponse
from basis.vision.prep import prepare_image
from evals import verify_boxes


def jpeg(width: int = 800, height: int = 600) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


def box(x1: float, y1: float, x2: float, y2: float) -> dict[str, float]:
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def inst(label: str, score: float, b: dict[str, float]) -> dict[str, Any]:
    return {"label": label, "score": score, "box": b}


def test_count_class_pools_doors() -> None:
    assert verify_boxes.count_class("exterior_door") == "door"
    assert verify_boxes.count_class("interior_door") == "door"
    assert verify_boxes.count_class("window") == "window"


def test_box_iou() -> None:
    assert verify_boxes.box_iou(box(0, 0, 1, 1), box(0, 0, 1, 1)) == 1.0
    assert verify_boxes.box_iou(box(0, 0, 0.5, 1), box(0.5, 0, 1, 1)) == 0.0
    assert verify_boxes.box_iou(box(0, 0, 0.5, 0.5), box(0.25, 0, 0.75, 0.5)) == pytest.approx(
        1 / 3
    )


def test_detections_filter_rank_and_nms_within_the_count_class() -> None:
    instances = [
        inst("interior_door", 0.6, box(0.1, 0.1, 0.3, 0.6)),
        inst("exterior_door", 0.8, box(0.1, 0.1, 0.3, 0.62)),  # same door, other phrase
        inst("window", 0.9, box(0.1, 0.1, 0.3, 0.6)),  # not a verified class
        inst("kitchen_cabinet", 0.2, box(0.5, 0.5, 0.6, 0.6)),  # under min score
        inst("kitchen_cabinet", 0.5, box(0.5, 0.5, 0.6, 0.6)),
        inst("light_fixture", 0.4, box(0.1, 0.1, 0.3, 0.6)),  # overlaps a door: other class
    ]
    kept = verify_boxes.detections(
        instances, ["door", "kitchen_cabinet", "light_fixture"], 0.3, 0.5
    )
    assert [(k["label"], k["score"]) for k in kept] == [
        ("exterior_door", 0.8),
        ("kitchen_cabinet", 0.5),
        ("light_fixture", 0.4),
    ]


def test_views_outline_the_box_and_enlarge_the_crop() -> None:
    photo = prepare_image(jpeg())
    outlined, enlarged = verify_boxes.views(photo, box(0.5, 0.5, 0.55, 0.6), 0.5)
    assert (outlined.width, outlined.height) == (800, 600)
    with Image.open(io.BytesIO(outlined.jpeg)) as image:
        pixel = image.convert("RGB").getpixel((400, 330))  # on the left edge of the box
    assert isinstance(pixel, tuple)
    r, g, b = pixel[:3]
    assert r > g + 80 and r > b + 80  # red outline (JPEG softens it) on a light gray photo
    # 40 x 60 px box, padded 20 / 30 px a side -> 80 x 120, enlarged to 512 on the long side
    assert max(enlarged.width, enlarged.height) == 480  # capped at 4x
    tiny = verify_boxes.views(photo, box(0.5, 0.5, 0.501, 0.501), 0.0)[1]
    assert min(tiny.width, tiny.height) >= 64


@dataclass
class VerdictProvider:
    """Answers with the label in ``answers`` keyed by the detection's x1 (rounded), from
    the box drawn in the first image, which the test cannot see; so key by call order."""

    labels: list[str]
    name: str = "fake"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        label = self.labels[(len(self.requests) - 1) % len(self.labels)]
        verdict = verify_boxes.Verdict(label=label, confidence=0.9)  # type: ignore[arg-type]
        return ChatResponse(
            parsed=verdict,
            text=verdict.model_dump_json(),
            usage=PHOTO_USAGE,
            model=PHOTO_MODEL,
            provider=self.name,
        )


@pytest.mark.asyncio
async def test_run_counts_the_confirmed_detections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(jpeg())
    sam = tmp_path / "sam"
    sam.mkdir()
    instances = [
        inst("interior_door", 0.9, box(0.1, 0.1, 0.3, 0.6)),
        inst("exterior_door", 0.8, box(0.6, 0.1, 0.8, 0.6)),
        inst("kitchen_cabinet", 0.7, box(0.4, 0.6, 0.5, 0.7)),
    ]
    (sam / "instances_raw.jsonl").write_text(
        json.dumps({"file": "a.jpg", "instances": instances})
        + "\n"
        + json.dumps({"file": "gone.jpg", "instances": instances})
        + "\n"
    )
    # in score order: interior door -> exterior_door (door class: confirmed), exterior door
    # -> part_or_several (rejected), cabinet -> kitchen_cabinet (confirmed)
    provider = VerdictProvider(["exterior_door", "part_or_several", "kitchen_cabinet"])
    monkeypatch.setattr(ProviderPool, "from_settings", staticmethod(lambda settings: provider))
    out = tmp_path / "out"
    args = ["--sam3", str(sam), "--photos", str(photos), "--out", str(out), "--repeats", "2"]
    args += ["--concurrency", "1"]
    assert await verify_boxes.run(verify_boxes.parse_args(args)) == 0
    assert len(provider.requests) == 6
    request = provider.requests[0]
    assert request.prompt_version == "photo_verify_box/v1"
    assert len(request.messages[0].images) == 2
    counts = json.loads((out / "photo_counts.json").read_text())
    assert counts == {"a.jpg": {"repeats": [{"interior_door": 1, "kitchen_cabinet": 1}] * 2}}
    verdicts = [json.loads(line) for line in (out / "verdicts.jsonl").read_text().splitlines()]
    assert [v["confirmed"] for v in verdicts[:3]] == [True, False, True]
    run = json.loads((out / "run.json").read_text())
    assert (run["detections"], run["calls"], run["unanswered"]) == (3, 6, 0)
    # The run record (evals.record): the SAM 3 run and the photos read, one ledger
    # line per call, the prompt declared.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.verify_boxes"
    assert manifest["calls"]["count"] == 6
    assert manifest["components"]["prompts"] == [
        {
            "name": "photo_verify_box",
            "version": "v1",
            "sha256": prompts.load("photo_verify_box", "v1").sha256,
            "calls": 6,
        }
    ]
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["photos", "sam3"]
    assert len((out / "llm_calls.jsonl").read_text().splitlines()) == 6
    assert "wrote" in (out / "console.log").read_text()
    # One case per (detection, repeat): every verdict was usable, so all passed, whether or
    # not it confirmed the detection (a rejection is an answer, not a failure).
    results = json.loads((out / "results.json").read_text())
    summary = results["summary"]
    assert summary["total"] == 6
    assert (summary["passed"], summary["failed"], summary["test_error"]) == (6, 0, 0)
    assert [c["id"] for c in results["cases"][:3]] == ["a.jpg:0#1", "a.jpg:1#1", "a.jpg:2#1"]
    assert all(c["kind"] == "detection" and c["seconds"] >= 0 for c in results["cases"])
    assert [(c["detail"]["verdict"], c["detail"]["confirmed"]) for c in results["cases"][:3]] == [
        ("exterior_door", True),
        ("part_or_several", False),
        ("kitchen_cabinet", True),
    ]
    assert (
        "**results: 6 passed, 0 failed, 0 test errors (6 cases)**"
        in (out / "report.md").read_text()
    )
    assert manifest["tree"] == {"component": "photo_verify_box", "version": "v1", "link": None}
