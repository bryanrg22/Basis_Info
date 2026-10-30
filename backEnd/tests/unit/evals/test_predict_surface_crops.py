"""``evals.predict_surface_crops``: crop requests, material accuracy and applying crop
materials to a room run (EVAL_REPORT section 26)."""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from llm_fakes import PHOTO_MODEL, PHOTO_USAGE
from PIL import Image

from basis.llm import prompts
from basis.llm.provider import ChatRequest, ChatResponse
from basis.vision import surface_crops as sc
from basis.vision.localize import Rle
from evals import predict_surface_crops as ps


def answer(material: str, lower: str = "none") -> dict[str, object]:
    return {"material": material, "lower_finish": lower, "confidence": 0.9, "evidence": "e"}


def test_material_accuracy_hit_exact_and_answered_by_hand() -> None:
    truth = {
        "a.jpg": {"floor": {"carpet"}, "wall": set(), "wall_lower": set(), "ceiling": set()},
        "b.jpg": {
            "floor": {"hardwood", "ceramic_tile"},
            "wall": set(),
            "wall_lower": {"wallpaper"},
            "ceiling": set(),
        },
    }
    predicted = [
        {
            "a.jpg": {"floor": {"carpet"}},
            "b.jpg": {"floor": {"hardwood"}, "wall_lower": {"wood"}},
        }
    ]
    acc = ps.material_accuracy(truth, predicted)  # type: ignore[arg-type]
    # floor: a hit+exact, b hit not exact -> hit 1.0, exact 0.5
    assert acc["floor"] == {"hit": 1.0, "exact": 0.5, "answered": 1.0, "n": 2.0}
    assert acc["wall_lower"] == {"hit": 0.0, "exact": 0.0, "answered": 1.0, "n": 1.0}
    # all: 3 pairs, hits 2, exact 1, answered 3
    assert acc["all"]["hit"] == pytest.approx(2 / 3) and acc["all"]["exact"] == pytest.approx(1 / 3)


def test_material_accuracy_averages_repeats_and_counts_a_missing_answer_as_wrong() -> None:
    truth = {"a.jpg": {"floor": {"carpet"}, "wall": set(), "wall_lower": set(), "ceiling": set()}}
    repeats = [{"a.jpg": {"floor": {"carpet"}}}, {}]
    acc = ps.material_accuracy(truth, repeats)  # type: ignore[arg-type]
    assert acc["floor"]["hit"] == 0.5 and acc["floor"]["answered"] == 0.5


def test_crop_predictions_read_repeats_per_surface(tmp_path: Path) -> None:
    (tmp_path / "surface_crops.json").write_text(
        json.dumps(
            {
                "a.jpg": {
                    "floor": {"repeats": [answer("carpet"), answer("vinyl")]},
                    "wall": {"repeats": [answer("painted_drywall", "wallpaper"), None]},
                }
            }
        )
    )
    first, second = ps.crop_predictions(tmp_path)
    assert first["a.jpg"]["floor"] == {"carpet"}
    assert first["a.jpg"]["wall_lower"] == {"wallpaper"}
    assert second["a.jpg"]["floor"] == {"vinyl"} and second["a.jpg"]["wall"] == set()


def test_per_photo_predictions_read_the_surfaces_of_photo_raw(tmp_path: Path) -> None:
    lines = [
        {"file": "a.jpg", "repeat": 1, "analysis": {"components": [
            {"label": "tile", "material": "ceramic"},
            {"label": "wall_finish", "material": "drywall"}]}},
        {"file": "a.jpg", "repeat": 2, "analysis": None},
    ]  # fmt: skip
    (tmp_path / "photo_raw.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    (only,) = ps.per_photo_predictions(tmp_path)
    assert only["a.jpg"]["floor"] == {"ceramic_tile"}
    assert only["a.jpg"]["wall"] == {"painted_drywall"}


def test_surfaces_to_ask_skips_small_and_missing_surfaces() -> None:
    big = np.zeros((10, 10), dtype=bool)
    big[:5] = True
    small = np.zeros((10, 10), dtype=bool)
    small[0, 0] = True
    record = {
        "surfaces": {
            "wall": Rle.encode(big).model_dump(mode="json"),
            "floor": Rle.encode(small).model_dump(mode="json"),
        }
    }
    found = ps.surfaces_to_ask(record)
    assert [s for s, _ in found] == ["wall"]


def test_build_request_sends_the_photo_then_the_crop_and_asks_about_the_lower_band() -> None:
    from basis.llm.call import CallContext
    from basis.llm.prompts import Prompt
    from basis.llm.provider import Budget
    from basis.llm.recorder import MemoryRecorder

    buffer = io.BytesIO()
    Image.new("RGB", (96, 72)).save(buffer, format="JPEG")
    photo = buffer.getvalue()
    ctx = CallContext(
        study_id=__import__("uuid").uuid4(),
        run_id=None,
        activity="t",
        budget=Budget(remaining_usd=1.0),
        recorder=MemoryRecorder(),
    )
    prompt = Prompt("surface_crop_material", "v1", "sys", "0" * 64)
    wall = ps.build_request("wall", photo, photo, "m", prompt, ctx, "low")
    assert [m.content for m in wall.messages[:2]] == [
        "Image 1 (the whole photo):",
        "Image 2 (the wall, cropped and enlarged, the rest greyed out):",
    ]
    assert "lower part" in str(wall.messages[2].content)
    assert wall.response_model is sc.CropAnswer and wall.reasoning_effort == "low"
    floor = ps.build_request("floor", photo, photo, "m", prompt, ctx, None)
    assert "lower part" not in str(floor.messages[2].content)


@dataclass
class CropProvider:
    """Answers every crop with painted drywall."""

    name: str = "fake"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        parsed = sc.CropAnswer(
            material="painted_drywall", lower_finish="none", confidence=0.9, evidence="e"
        )
        return ChatResponse(
            parsed=parsed,
            text=parsed.model_dump_json(),
            usage=PHOTO_USAGE,
            model=PHOTO_MODEL,
            provider=self.name,
        )


async def test_predict_writes_the_crop_answers_and_the_run_record(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    buffer = io.BytesIO()
    Image.new("RGB", (96, 72), (200, 200, 200)).save(buffer, format="JPEG")
    (photos / "a.jpg").write_bytes(buffer.getvalue())
    surfaces = tmp_path / "surfaces"
    surfaces.mkdir()
    wall = np.zeros((72, 96), dtype=bool)
    wall[:36] = True  # the upper half of the photo
    (surfaces / "surfaces_raw.jsonl").write_text(
        json.dumps(
            {"file": "a.jpg", "surfaces": {"wall": Rle.encode(wall).model_dump(mode="json")}}
        )
        + "\n"
    )
    provider = CropProvider()
    out = tmp_path / "out"
    args = ps.parse_args(
        [
            "predict",
            "--surfaces",
            str(surfaces),
            "--photos",
            str(photos),
            "--out",
            str(out),
            "--model",
            "m",
            "--repeats",
            "2",
        ]
    )
    assert await ps.predict(args, provider) == 0
    assert len(provider.requests) == 2  # one wall crop, two repeats
    saved = json.loads((out / "surface_crops.json").read_text())
    assert [a["material"] for a in saved["a.jpg"]["wall"]["repeats"]] == ["painted_drywall"] * 2
    run = json.loads((out / "run.json").read_text())
    assert (run["photos"], run["crops"], run["repeats"], run["unusable"]) == (1, 1, 2, 0)
    assert run["prompt"] == "surface_crop_material/v1"
    # The run record (evals.record): the segmenter run and the photos read, one ledger
    # line per call, the prompt declared.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_surface_crops"
    assert manifest["calls"]["count"] == 2
    assert manifest["components"]["prompts"] == [
        {
            "name": "surface_crop_material",
            "version": "v1",
            "sha256": prompts.load("surface_crop_material", "v1").sha256,
            "calls": 2,
        }
    ]
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["surfaces", "photos"]
    assert len((out / "llm_calls.jsonl").read_text().splitlines()) == 2
    assert "wrote" in (out / "run.log").read_text()


def test_majority_breaks_ties_by_first_seen_and_respects_allowed() -> None:
    assert ps.majority(["wood", "wallpaper", "wallpaper", "wood"]) == "wood"
    assert ps.majority(["hardwood", "carpet", "ceramic_tile"], {"ceramic_tile", "stone"}) == (
        "ceramic_tile"
    )
    assert ps.majority([], None) is None


def test_apply_replaces_surface_materials_by_the_crops_majority_over_the_rooms_photos(
    tmp_path: Path,
) -> None:
    rooms = tmp_path / "rooms"
    rooms.mkdir()
    (rooms / "room_inventory.json").write_text(json.dumps({"1_hall": {"wainscoting": 1}}))
    record = {
        "room_id": "1_hall",
        "repeat": 1,
        "files": ["a.jpg", "b.jpg"],
        "objects": [
            {"label": "wainscoting", "count": 1, "material": "wood"},
            {"label": "tile", "count": 1, "material": "vinyl"},
            {"label": "window", "count": 2, "material": "glass"},
        ],
    }
    (rooms / "room_raw.jsonl").write_text(json.dumps(record) + "\n")
    crops = tmp_path / "crops"
    crops.mkdir()
    (crops / "surface_crops.json").write_text(
        json.dumps(
            {
                "a.jpg": {
                    "wall": {"repeats": [answer("painted_drywall", "wallpaper")]},
                    "floor": {"repeats": [answer("hardwood")]},
                },
                "b.jpg": {"wall": {"repeats": [answer("painted_drywall", "wallpaper")]}},
            }
        )
    )
    out = tmp_path / "out"
    assert ps.apply(argparse.Namespace(rooms=rooms, crops=crops, out=out)) == 0
    written = json.loads((out / "room_raw.jsonl").read_text().splitlines()[0])
    materials = {o["label"]: o["material"] for o in written["objects"]}
    # wainscot from the lower band; the floor crop said hardwood, which is not a tile
    # material, so the tile keeps its own; the window is not a surface
    assert materials == {"wainscoting": "wallpaper", "tile": "vinyl", "window": "glass"}
    assert written["objects"][0]["material_before_crops"] == "wood"
    assert json.loads((out / "room_inventory.json").read_text()) == {"1_hall": {"wainscoting": 1}}
    assert json.loads((out / "run.json").read_text())["materials_changed"] == 1
    # apply makes no call but leaves a manifest naming the two runs it combined.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_surface_crops"
    assert manifest["calls"]["count"] == 0 and manifest["components"]["prompts"] == []
    assert manifest["notes"] == {"rooms": str(rooms), "crops": str(crops)}
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["rooms", "crops"]
    assert not (out / "llm_calls.jsonl").exists()
    assert "wrote" in (out / "run.log").read_text()


def test_with_fallback_fills_only_the_kinds_the_crops_left_empty() -> None:
    crops = [{"a.jpg": {"floor": set(), "wall": {"wallpaper"}}}]
    photos = [
        {"a.jpg": {"floor": {"carpet"}, "wall": {"painted_drywall"}}},
        {"b.jpg": {"floor": {"tile"}}},
    ]
    first, second = ps.with_fallback(crops, photos)  # type: ignore[arg-type]
    assert first["a.jpg"]["floor"] == {"carpet"} and first["a.jpg"]["wall"] == {"wallpaper"}
    assert second["b.jpg"]["floor"] == {"tile"} and second["a.jpg"]["wall"] == {"wallpaper"}
