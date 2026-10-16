"""``evals.predict_rooms``: grouping, heuristic and reconciliation on a photo directory.

Offline: per-photo requests go to the fixture vision provider (the real per-photo
pipeline on the synthetic fixture photos) and room requests to a fixture room provider
(order-independent: it recognises each image and answers with that photo's labels merged
by label) or to a :class:`ScriptedProvider` when a test needs exact answers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import PHOTO_MODEL, PHOTO_USAGE, FixtureVisionProvider, photo_labels

from basis.llm import ScriptedProvider, ScriptedTurn, Usage
from basis.llm.errors import ProviderBadResponse
from basis.llm.provider import ChatProvider, ChatRequest, ChatResponse
from basis.vision.prep import prepare_image
from basis.vision.reconcile import ModelRoomInventory
from basis.vision.schemas import ModelPhotoAnalysis
from evals import predict_photos, predict_rooms

KITCHEN = "photos/01_kitchen.jpg"
BATHROOM = "photos/02_bathroom.jpg"
BEDROOM = "photos/03_bedroom.jpg"
# Package-style names -> fixture photo.
PACKAGE = {
    "21_1st Floor Kitchen photo 1.JPG": KITCHEN,
    "22_1st Floor Kitchen photo 2.JPG": KITCHEN,
    "45_Basement Bath.JPG": BATHROOM,
}


def labels_by_image() -> dict[str, dict[str, Any]]:
    by_hash = {}
    for file, entry in photo_labels().items():
        by_hash[prepare_image(fixture_path(*file.split("/")).read_bytes()).sha256] = entry
    return by_hash


@dataclass
class FixtureRoomProvider:
    """Answers a room request from the fixture labels of each image in it."""

    name: str = "fixture-rooms"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        known = labels_by_image()
        merged: dict[str, dict[str, Any]] = {}
        number = 0
        for message in request.messages:
            for image in message.images:
                number += 1
                digest = hashlib.sha256(base64.b64decode(image.data_base64)).hexdigest()
                for c in known[digest]["components"]:
                    entry = merged.setdefault(c["label"], {"count": 0, "indices": []})
                    entry["count"] = max(entry["count"], c["count"])
                    entry["indices"].append(number)
        parsed = ModelRoomInventory.model_validate(
            {
                "objects": [
                    {
                        "label": label,
                        "count": e["count"],
                        "image_indices": e["indices"],
                        "material": "unknown",
                        "attachment": "permanent",
                        "confidence": 0.8,
                        "note": "fixture" if label == "other" else None,
                    }
                    for label, e in merged.items()
                ]
            }
        )
        return ChatResponse(
            parsed=parsed, usage=PHOTO_USAGE, model=PHOTO_MODEL, provider=self.name, raw={"n": 1}
        )


@dataclass
class Router:
    """Per-photo requests to ``photos``, room requests to ``rooms``."""

    photos: ChatProvider
    rooms: ChatProvider
    name: str = "router"

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if request.response_model is ModelPhotoAnalysis:
            return await self.photos.complete(request)
        assert request.response_model is ModelRoomInventory
        return await self.rooms.complete(request)


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "photos"
    directory.mkdir()
    for name, fixture in PACKAGE.items():
        shutil.copy(fixture_path(*fixture.split("/")), directory / name)
    (directory / "notes.txt").write_text("not a photo")
    return directory


def use(monkeypatch: pytest.MonkeyPatch, provider: ChatProvider | None) -> None:
    for module in (predict_rooms, predict_photos):
        monkeypatch.setattr(
            module.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
        )


async def run(*argv: str) -> int:
    return await predict_rooms.run(predict_rooms.parse_args(list(argv)))


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_run_writes_the_heuristic_the_reconciled_inventory_and_raw_records(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, rooms = FixtureVisionProvider(), FixtureRoomProvider()
    use(monkeypatch, Router(photos, rooms))
    out = tmp_path / "out"
    assert await run("--photos", str(photo_dir), "--out", str(out)) == 0
    assert len(photos.requests) == 3 and len(rooms.requests) == 2  # 3 photos, 2 rooms

    grouping = json.loads((out / "rooms.json").read_text())
    assert [(r["room_id"], r["name"], r["floor"], r["room_type"]) for r in grouping] == [
        ("b_bath", "Basement Bath", 0, "bathroom"),
        ("1_kitchen", "1st Floor Kitchen", 1, "kitchen"),
    ]
    kitchen = grouping[1]
    assert kitchen["photos"] == [
        "21_1st Floor Kitchen photo 1.JPG",
        "22_1st Floor Kitchen photo 2.JPG",
    ]
    cabinets = next(o for o in kitchen["heuristic_objects"] if o["label"] == "kitchen_cabinet")
    assert (cabinets["count"], cabinets["image_indices"], cabinets["files"]) == (
        6,
        [1, 2],
        kitchen["photos"],
    )

    heuristic = json.loads((out / "room_inventory.heuristic.json").read_text())
    assert heuristic == {
        "1_kitchen": {"kitchen_cabinet": 6, "countertop": 1},
        "b_bath": {"bathtub": 1, "toilet": 1},
    }
    assert json.loads((out / "room_inventory.json").read_text()) == heuristic

    raw = jsonl(out / "room_raw.jsonl")
    assert [(r["room_id"], r["repeat"], r["images"], r["source"]) for r in raw] == [
        ("b_bath", 1, 1, "model"),
        ("1_kitchen", 1, 2, "model"),
    ]
    record = raw[1]
    countertop = next(o for o in record["objects"] if o["label"] == "countertop")
    assert countertop["image_indices"] == [1, 2] and countertop["files"] == record["files"]
    [call] = record["calls"]
    assert (call["images"], call["attempts"], call["model"], call["errors"]) == (
        2,
        1,
        PHOTO_MODEL,
        [],
    )
    assert call["response"]["objects"] and call["cost_usd"] == pytest.approx(0.01)
    assert record["cost_usd"] == pytest.approx(0.01) and not record["needs_review"]

    # The per-photo records are kept so a later run can reuse them.
    assert len(jsonl(out / "photo_raw.jsonl")) == 3
    summary = json.loads((out / "run.json").read_text())
    assert (summary["photos"], summary["rooms"], summary["repeats"]) == (3, 2, 1)
    assert summary["prompts"] == {
        "photo_analysis": "photo_analysis/v1",
        "room_inventory": "room_inventory/v1",
    }
    assert summary["spent_usd"] == pytest.approx(5 * 0.01)  # 3 photos + 2 rooms
    assert summary["per_photo_source"] == "computed" and summary["rejected"] == {}


async def test_repeats_aliases_and_rejected_images(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.copy(fixture_path("photos", "03_bedroom.jpg"), photo_dir / "29_2nd Floor Bed 2 .JPG")
    shutil.copy(
        fixture_path("photos", "03_bedroom.jpg"),
        photo_dir / "30_2nd Floor Bed 2 Seating Area.JPG",
    )
    shutil.copy(fixture_path("malformed", "tiny.jpg"), photo_dir / "60_Attic.JPG")
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({"2_bed_2_seating_area": "2_bed_2"}))
    rooms = FixtureRoomProvider()
    use(monkeypatch, Router(FixtureVisionProvider(), rooms))
    out = tmp_path / "out"
    code = await run(
        "--photos", str(photo_dir), "--out", str(out), "--repeats", "2", "--aliases", str(aliases)
    )
    assert code == 0
    inventory = json.loads((out / "room_inventory.json").read_text())
    assert set(inventory) == {"b_bath", "1_kitchen", "2_bed_2"}
    assert inventory["2_bed_2"] == {"repeats": [{"carpet": 1, "other": 1}] * 2}
    raw = jsonl(out / "room_raw.jsonl")
    bed = [r for r in raw if r["room_id"] == "2_bed_2"]
    assert [(r["repeat"], r["images"]) for r in bed] == [(1, 2), (2, 2)]
    assert len(rooms.requests) == 6  # 3 rooms x 2 repeats
    summary = json.loads((out / "run.json").read_text())
    assert summary["aliases"] == {"2_bed_2_seating_area": "2_bed_2"}
    assert list(summary["rejected"]) == ["60_Attic.JPG"]
    assert summary["rejected"]["60_Attic.JPG"].startswith("too_small")
    assert summary["photos"] == 6 and summary["rooms"] == 3


async def test_per_photo_output_is_reused_without_per_photo_calls(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FixtureVisionProvider())
    vision_out = tmp_path / "vision"
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(vision_out), "--repeats", "2"]
        )
    )
    assert code == 0
    photos, rooms = FixtureVisionProvider(), FixtureRoomProvider()
    use(monkeypatch, Router(photos, rooms))
    out = tmp_path / "out"
    code = await run(
        "--photos",
        str(photo_dir),
        "--out",
        str(out),
        "--per-photo",
        str(vision_out),
        "--per-photo-repeat",
        "2",
    )
    assert code == 0
    assert photos.requests == [] and len(rooms.requests) == 2
    assert not (out / "photo_raw.jsonl").exists()
    summary = json.loads((out / "run.json").read_text())
    assert summary["per_photo_source"].endswith("photo_raw.jsonl repeat 2")
    assert summary["prompts"]["photo_analysis"] is None
    assert summary["spent_usd"] == pytest.approx(2 * 0.01)
    # The per-photo analyses still reach the room request as text.
    text = rooms.requests[1].messages[-1].content
    assert "Image 1: kitchen; kitchen_cabinet x6" in text


async def test_scripted_answers_fallback_and_files_per_object(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = Usage(input_tokens=1000, output_tokens=100)
    bad = ScriptedTurn(
        raise_error=ProviderBadResponse("bad json", provider="scripted", usage=usage)
    )
    scripted = ScriptedProvider(
        [
            bad,  # b_bath: twice unusable -> heuristic, needs_review
            bad,
            ScriptedTurn(  # 1_kitchen: one countertop in both photos, a sink out of range
                parsed=ModelRoomInventory.model_validate(
                    {
                        "objects": [
                            {
                                "label": "countertop",
                                "count": 1,
                                "image_indices": [2, 1],
                                "material": "granite",
                                "attachment": "permanent",
                                "confidence": 0.9,
                                "note": None,
                            },
                            {
                                "label": "sink",
                                "count": 1,
                                "image_indices": [3],
                                "material": "metal",
                                "attachment": "permanent",
                                "confidence": 0.9,
                                "note": None,
                            },
                        ]
                    }
                ),
                usage=usage,
            ),
        ],
        model="gpt-4.1-mini",
    )
    use(monkeypatch, Router(FixtureVisionProvider(), scripted))
    out = tmp_path / "out"
    assert await run("--photos", str(photo_dir), "--out", str(out), "--concurrency", "1") == 0
    bath, kitchen = jsonl(out / "room_raw.jsonl")
    assert (bath["source"], bath["needs_review"], bath["review_reason"]) == (
        "heuristic",
        True,
        "invalid_model_output",
    )
    assert bath["calls"][0]["attempts"] == 2 and len(bath["calls"][0]["errors"]) == 2
    assert {o["label"] for o in bath["objects"]} == {"bathtub", "toilet"}
    [countertop] = kitchen["objects"]
    assert countertop["image_indices"] == [1, 2]
    assert countertop["files"] == [
        "21_1st Floor Kitchen photo 1.JPG",
        "22_1st Floor Kitchen photo 2.JPG",
    ]
    assert kitchen["dropped"][0]["object"]["label"] == "sink"
    assert kitchen["review_reason"] == "dropped_objects:1"
    inventory = json.loads((out / "room_inventory.json").read_text())
    assert inventory == {"b_bath": {"bathtub": 1, "toilet": 1}, "1_kitchen": {"countertop": 1}}
    assert json.loads((out / "run.json").read_text())["room_results_needing_review"] == 2


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("cycle", "loop"),
        ("not_a_map", "JSON object"),
        ("bad_json", "cannot read aliases"),
        ("no_per_photo", "does not exist"),
        ("wrong_repeat", "no records for repeat 3"),
    ],
)
async def test_unusable_inputs_exit_2_before_any_call(
    photo_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setup: str,
    message: str,
) -> None:
    provider = ScriptedProvider([])
    use(monkeypatch, provider)
    aliases = tmp_path / "aliases.json"
    per_photo = tmp_path / "vision"
    per_photo.mkdir()
    argv = ["--photos", str(photo_dir), "--out", str(tmp_path / "out")]
    if setup == "cycle":
        aliases.write_text(json.dumps({"a": "b", "b": "a"}))
        argv += ["--aliases", str(aliases)]
    elif setup == "not_a_map":
        aliases.write_text(json.dumps(["a", "b"]))
        argv += ["--aliases", str(aliases)]
    elif setup == "bad_json":
        aliases.write_text("{")
        argv += ["--aliases", str(aliases)]
    elif setup == "wrong_repeat":
        (per_photo / "photo_raw.jsonl").write_text(
            json.dumps({"file": "x.jpg", "repeat": 1, "analysis": None}) + "\n"
        )
        argv += ["--per-photo", str(per_photo), "--per-photo-repeat", "3"]
    else:
        argv += ["--per-photo", str(per_photo)]
    assert await run(*argv) == 2
    assert message in capsys.readouterr().err
    assert provider.requests == []


async def test_no_provider_is_an_error(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, None)
    assert await run("--photos", str(photo_dir), "--out", str(tmp_path / "o")) == 2


def test_argument_limits() -> None:
    with pytest.raises(SystemExit):
        predict_rooms.parse_args(["--photos", "p", "--out", "o", "--repeats", "0"])
    with pytest.raises(SystemExit):
        predict_rooms.parse_args(["--photos", "p", "--out", "o", "--concurrency", "0"])
