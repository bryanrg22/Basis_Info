"""``evals.predict_marks`` / ``evals.predict_points`` / ``evals.compare_counts``: SAM 3 and the
vision model together, end to end on fakes."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from llm_fakes import PHOTO_MODEL, PHOTO_USAGE
from PIL import Image
from pydantic import BaseModel

from basis.llm.pool import ProviderPool
from basis.llm.provider import ChatRequest, ChatResponse
from basis.vision import marks as mk
from basis.vision.localize import Rle
from evals import compare_counts, predict_marks, predict_points
from evals import datasets as ds

W, H = 80, 60


def jpeg() -> bytes:
    buffer = io.BytesIO()
    # twice the masks' size: SAM 3's masks need not match the photo's pixels
    Image.new("RGB", (2 * W, 2 * H), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


def raw_instance(label: str, score: float, px: tuple[int, int, int, int]) -> dict[str, Any]:
    x1, y1, x2, y2 = px
    array = np.zeros((H, W), dtype=np.uint8)
    array[y1:y2, x1:x2] = 1
    return {
        "label": label,
        "phrase": label,
        "score": score,
        "box": {"x1": x1 / W, "y1": y1 / H, "x2": x2 / W, "y2": y2 / H},
        "mask": Rle.encode(array).model_dump(),
        "area": (x2 - x1) * (y2 - y1) / (W * H),
    }


@pytest.fixture
def setup(tmp_path: Path) -> dict[str, Path]:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(jpeg())
    sam = tmp_path / "sam"
    sam.mkdir()
    instances = [
        raw_instance("window", 0.9, (5, 5, 20, 20)),  # mark 1
        raw_instance("interior_door", 0.8, (40, 5, 55, 50)),  # mark 2
        raw_instance("light_fixture", 0.35, (60, 30, 64, 34)),  # mark 3
        raw_instance("window", 0.15, (25, 5, 35, 20)),  # under the mark threshold: pool only
    ]
    (sam / "instances_raw.jsonl").write_text(
        json.dumps({"file": "a.jpg", "instances": instances, "calls": [{"predict_seconds": 2.5}]})
        + "\n"
    )
    base = tmp_path / "base"
    base.mkdir()
    (base / "photo_counts.json").write_text(
        json.dumps({"a.jpg": {"repeats": [{"door": 1, "window": 4, "sink": 1}] * 2}})
    )
    (base / "photo_components.json").write_text(
        json.dumps({"a.jpg": {"repeats": [["door", "sink", "window"]] * 2}})
    )
    (base / "photo_rooms.json").write_text(json.dumps({"a.jpg": "kitchen"}))
    return {"photos": photos, "sam": sam, "base": base, "out": tmp_path / "out"}


@dataclass
class ScriptedProvider:
    answers: list[BaseModel]
    name: str = "fake"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        answer = self.answers[(len(self.requests) - 1) % len(self.answers)]
        return ChatResponse(
            parsed=answer,
            text=answer.model_dump_json(),
            usage=PHOTO_USAGE,
            model=PHOTO_MODEL,
            provider=self.name,
        )


REVIEW = mk.MarksReview(
    marks=[
        mk.MarkVerdict(mark=1, verdict="window"),
        mk.MarkVerdict(mark=2, verdict="exterior_door"),
        mk.MarkVerdict(mark=3, verdict="not_countable"),
    ],
    added=[
        mk.AddedObject(label="window", x=30 / W, y=12 / H),  # on the low-score pool window
        mk.AddedObject(label="window", x=10 / W, y=10 / H),  # inside kept mark 1: duplicate
        mk.AddedObject(label="electrical_outlet", x=0.05, y=0.95),  # nothing there: unmasked
    ],
)


@pytest.mark.asyncio
async def test_marks_run_counts_kept_plus_added_and_loops(
    setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([REVIEW])
    monkeypatch.setattr(ProviderPool, "from_settings", staticmethod(lambda settings: provider))
    args = ["--sam3", str(setup["sam"]), "--photos", str(setup["photos"])]
    args += ["--out", str(setup["out"]), "--base", str(setup["base"]), "--repeats", "2"]
    args += ["--loop", "--style", "outline"]
    assert await predict_marks.run(predict_marks.parse_args(args)) == 0
    assert len(provider.requests) == 2
    request = provider.requests[0]
    assert request.prompt_version == "photo_marks/v1"
    assert len(request.messages[0].images) == 2  # original + marked
    assert "1: window\n2: interior door\n3: light fixture" in request.messages[0].content
    out = setup["out"]
    counts = json.loads((out / "photo_counts.json").read_text())
    # base "door" and "window" replaced; "sink" kept; kept 2 + added 3
    expected = {"electrical_outlet": 1, "exterior_door": 1, "sink": 1, "window": 3}
    assert counts == {"a.jpg": {"repeats": [expected] * 2}}
    components = json.loads((out / "photo_components.json").read_text())
    assert components["a.jpg"]["repeats"][0] == [
        "electrical_outlet",
        "exterior_door",
        "sink",
        "window",
    ]
    assert json.loads((out / "photo_rooms.json").read_text()) == {"a.jpg": "kitchen"}
    masked = json.loads((out / "loop_masked" / "photo_counts.json").read_text())
    assert masked["a.jpg"]["repeats"][0] == {"exterior_door": 1, "sink": 1, "window": 2}
    everything = json.loads((out / "loop_all" / "photo_counts.json").read_text())
    assert everything["a.jpg"]["repeats"][0] == {
        "electrical_outlet": 1,
        "exterior_door": 1,
        "sink": 1,
        "window": 2,
    }
    run = json.loads((out / "run.json").read_text())
    assert run["models_reported"] == [PHOTO_MODEL]
    assert (run["kept"], run["added"], run["marks_per_photo"]) == (4, 6, 3)
    assert run["loop"]["added_point_status"] == {"kept_inside": 2, "pool": 2, "unmasked": 2}
    assert run["detector_seconds_per_photo"] == 2.5
    assert json.loads((out / "loop_all" / "run.json").read_text()) == run
    raw = [json.loads(line) for line in (out / "marks_raw.jsonl").read_text().splitlines()]
    assert raw[0]["tally"]["rejected"] == {"not_countable": 1}

    # --from-raw redoes the loop without calls, here with a stricter mask threshold
    again = ["--sam3", str(setup["sam"]), "--photos", str(setup["photos"])]
    again += ["--out", str(out), "--base", str(setup["base"]), "--loop"]
    again += ["--from-raw", str(out / "marks_raw.jsonl"), "--pool-min-score", "0.5"]
    assert await predict_marks.run(predict_marks.parse_args(again)) == 0
    assert len(provider.requests) == 2
    masked = json.loads((out / "loop_masked" / "photo_counts.json").read_text())
    assert masked["a.jpg"]["repeats"][0] == {"exterior_door": 1, "sink": 1, "window": 1}


@pytest.mark.asyncio
async def test_marks_run_without_original_sends_one_image(
    setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([mk.MarksReview(marks=[], added=[])])
    monkeypatch.setattr(ProviderPool, "from_settings", staticmethod(lambda settings: provider))
    args = ["--sam3", str(setup["sam"]), "--photos", str(setup["photos"])]
    args += ["--out", str(setup["out"]), "--no-original"]
    assert await predict_marks.run(predict_marks.parse_args(args)) == 0
    assert len(provider.requests[0].messages[0].images) == 1
    # every mark unanswered: nothing counted; no base: the counts alone
    assert json.loads((setup["out"] / "photo_counts.json").read_text()) == {"a.jpg": {}}
    assert not (setup["out"] / "photo_components.json").exists()


def test_from_raw_needs_loop() -> None:
    with pytest.raises(SystemExit):
        predict_marks.parse_args(["--sam3", "s", "--photos", "p", "--out", "o", "--from-raw", "r"])


def test_merge_with_base_keeps_missing_repeats_out() -> None:
    counts, components, rooms = predict_marks.merge_with_base({"a": [{"window": 1}, None]}, None)
    assert counts == {"a": {"repeats": [{"window": 1}, None]}}
    assert components is None and rooms is None


ANSWER = mk.PointAnswer(
    objects=[
        mk.PointedObject(label="window", x=10 / W, y=10 / H),
        mk.PointedObject(label="window", x=12 / W, y=12 / H),  # same mask: one object
        mk.PointedObject(label="interior_door", x=45 / W, y=30 / H),
        mk.PointedObject(label="electrical_outlet", x=0.05, y=0.95),  # no mask
    ],
    counts=mk.StatedCounts(
        window=3,
        interior_door=1,
        exterior_door=0,
        light_fixture=0,
        outdoor_lighting=0,
        electrical_outlet=1,
        kitchen_cabinet=0,
    ),
)


@pytest.mark.asyncio
async def test_points_run_writes_pointed_stated_and_masked_counts(
    setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([ANSWER])
    monkeypatch.setattr(ProviderPool, "from_settings", staticmethod(lambda settings: provider))
    args = ["--photos", str(setup["photos"]), "--out", str(setup["out"])]
    args += ["--pool", str(setup["sam"]), "--base", str(setup["base"]), "--repeats", "2"]
    assert await predict_points.run(predict_points.parse_args(args)) == 0
    assert provider.requests[0].prompt_version == "photo_points/v1"
    out = setup["out"]

    def first(sub: str) -> dict[str, int]:
        value: dict[str, int] = json.loads((out / sub / "photo_counts.json").read_text())["a.jpg"][
            "repeats"
        ][0]
        return {k: v for k, v in value.items() if k != "sink"}

    assert first(".") == {"electrical_outlet": 1, "interior_door": 1, "window": 2}
    assert first("stated") == {"electrical_outlet": 1, "interior_door": 1, "window": 3}
    assert first("masked") == {"interior_door": 1, "window": 1}
    assert first("masked_all") == {"electrical_outlet": 1, "interior_door": 1, "window": 1}
    run = json.loads((out / "masked" / "run.json").read_text())
    assert run["point_status"] == {"duplicate": 2, "pool": 4, "unmasked": 2}
    assert run["models_reported"] == [PHOTO_MODEL]


@dataclass
class FakeReplicate:
    texts: dict[str, str]
    seen: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        import asyncio

        self._semaphore = asyncio.Semaphore(1)

        class Client:
            async def aclose(self) -> None:
                return None

        self.client = Client()

    async def _predict(self, input_: dict[str, Any], prompts: tuple[str, ...]) -> Any:
        from basis.vision.localize import LocalizeCall

        self.seen.append(input_)
        call = LocalizeCall(prompts=prompts, predict_seconds=1.0, cost_usd=0.001, status="ok")
        return {"output": self.texts.get(input_["text"], "none")}, call


@pytest.mark.asyncio
async def test_points_run_with_molmo_labels_lights_by_room(
    setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeReplicate(
        {
            "Point to every window.": f'<point x="{100 * 10 / W}" y="{100 * 10 / H}">w</point>',
            "Point to every light fixture.": '<points x1="77.5" y1="53" x2="5" y2="95">l</points>',
        }
    )
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-token")
    monkeypatch.setattr(predict_points, "build_replicate_localizer", lambda *a, **k: fake)
    (setup["base"] / "photo_rooms.json").write_text(json.dumps({"a.jpg": "exterior"}))
    args = ["--photos", str(setup["photos"]), "--out", str(setup["out"]), "--pointer", "molmo"]
    args += ["--pool", str(setup["sam"]), "--base", str(setup["base"])]
    assert await predict_points.run(predict_points.parse_args(args)) == 0
    assert len(fake.seen) == len(predict_points.MOLMO_QUERIES)
    assert fake.seen[0]["temperature"] == 0.1
    counts = json.loads((setup["out"] / "photo_counts.json").read_text())["a.jpg"]
    assert counts == {"outdoor_lighting": 2, "sink": 1, "window": 1}
    assert not (setup["out"] / "stated").exists()
    assert predict_points.molmo_label("light", "kitchen") == "light_fixture"


# --------------------------------------------------------------------------- compare_counts


def truth() -> ds.PhotoCounts:
    return ds.PhotoCounts(
        photos=[
            ds.PhotoCountLabel(file=f, document="d", split="dev", counts=c)
            for f, c in (("a", {"window": 2, "door": 1}), ("b", {"window": 0, "door": 2}))
        ]
    )


def test_compare_counts_scores_and_pairs_over_photos() -> None:
    baseline = {"a": {"window": 2, "door": 1}, "b": {"interior_door": 1}}
    system = {
        "a": {"repeats": [{"window": 3, "exterior_door": 1}, {"window": 2, "door": 1}]},
        "b": {"interior_door": 1, "exterior_door": 1},
    }
    result = compare_counts.compare(truth(), baseline, system, 2, resamples=200)
    overall = result["overall"]
    # pairs: a r1 window 3/2 door 1/1; a r2 2/2, 1/1; b window 0/0 door 2/2 (x2)
    assert overall.n == 8
    assert overall.mae == pytest.approx(1 / 8)
    assert (overall.precision, overall.recall) == (pytest.approx(10 / 11), 1.0)
    # per photo: a 0.25 vs 0, b 0 vs 0.5 -> diffs +0.25, -0.5
    mean, low, high = result["diff"]
    assert mean == pytest.approx(-0.125)
    assert low <= mean <= high
    assert result["per_class"]["window"].mae == pytest.approx(0.25)
    text = compare_counts.render({"x": result}, 2)
    assert "| x | 0.125 |" in text


def test_compare_counts_skips_photos_missing_a_repeat() -> None:
    pairs = compare_counts.pairs_by_photo(truth(), {"a": {"repeats": [{"window": 1}]}}, 1)
    assert set(pairs) == {"a"}
    with pytest.raises(ValueError):
        compare_counts.paired_bootstrap([])
