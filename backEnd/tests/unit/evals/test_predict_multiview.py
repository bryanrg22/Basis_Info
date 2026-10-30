"""``evals.predict_multiview``: the room, house and schedule runs with a scripted provider
(EVAL_REPORT section 26)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from basis.llm import prompts
from basis.llm.provider import ScriptedTurn
from basis.vision import multiview as mv
from basis.vision.reconcile import ModelRoomInventory
from evals import predict_multiview as pm
from tests.llm_fakes import PromptKeyedProvider

FILES = [
    "21_1st Floor Kitchen photo 1.JPG",
    "15_1st Floor Breakfast Nook.JPG",
    "17_1st Floor Bath.JPG",
]
ALIASES = {"1_breakfast_nook": "1_kitchen"}


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    for name in FILES:
        Image.new("RGB", (96, 72), (100, 110, 120)).save(folder / name, format="JPEG")
    return folder


def obj(label: str, count: int, indices: list[int], material: str = "unknown") -> dict[str, Any]:
    return {
        "label": label,
        "count": count,
        "image_indices": indices,
        "material": material,
        "attachment": "permanent",
        "confidence": 0.9,
        "note": None,
    }


def args(tmp_path: Path, photos: Path, scope: str, **extra: Any) -> argparse.Namespace:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps(ALIASES))
    base = {
        "photos": photos,
        "out": tmp_path / "out",
        "scope": scope,
        "model": "claude-opus-5-5",
        "reasoning_effort": "medium",
        "prompt": "v2",
        "max_tokens": None,
        "repeats": 1,
        "aliases": aliases,
        "template": None,
        "concurrency": 1,
        "budget_usd": 5.0,
    }
    base.update(extra)
    return argparse.Namespace(**base)


def test_room_scope_calls_once_per_room_and_writes_inventories_with_materials(
    tmp_path: Path, photos: Path
) -> None:
    # rooms in order of first photo: 15 (nook -> kitchen) opens the kitchen, then 17 the bath
    turns = {
        "room_photos_inventory": [
            ScriptedTurn(
                parsed=ModelRoomInventory.model_validate(
                    {"objects": [obj("window", 2, [1, 2]), obj("sink", 1, [3])]}
                )
            ),
            ScriptedTurn(
                parsed=ModelRoomInventory.model_validate(
                    {"objects": [obj("toilet", 1, [1]), obj("tile", 1, [1], "ceramic")]}
                )
            ),
        ]
    }
    provider = PromptKeyedProvider(turns=turns, model="claude-opus-5-5")
    a = args(tmp_path, photos, "room", concurrency=1)
    assert asyncio.run(pm.run(a, provider=provider)) == 0
    inventory = json.loads((a.out / "room_inventory.json").read_text())
    # the kitchen's sink pointed at a third image of a two-photo room: dropped
    assert inventory == {"1_bath": {"tile": 1, "toilet": 1}, "1_kitchen": {"window": 2}}
    raw = [json.loads(line) for line in (a.out / "room_raw.jsonl").read_text().splitlines()]
    bath = next(r for r in raw if r["room_id"] == "1_bath")
    assert {o["label"]: o["material"] for o in bath["objects"]} == {
        "toilet": "unknown",
        "tile": "ceramic",
    }
    kitchen = next(r for r in raw if r["room_id"] == "1_kitchen")
    assert kitchen["objects"][0]["files"] == [FILES[1], FILES[0]]
    assert [d["reason"] for d in kitchen["dropped"]] == ["image_indices"]
    run = json.loads((a.out / "run.json").read_text())
    assert run["rooms"] == 2 and run["prompt"] == "room_photos_inventory/v2"
    assert run["models_reported"] == ["claude-opus-5-5"]
    assert len(provider.requests) == 2
    # The run record (evals.record) beside the runner's own calls.jsonl.
    manifest = json.loads((a.out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_multiview"
    assert manifest["summary"] == run
    assert manifest["calls"]["count"] == 2 and manifest["calls"]["by_kind"] == {"chat": 2}
    prompt = prompts.load("room_photos_inventory", "v2")
    assert manifest["components"]["prompts"] == [
        {"name": "room_photos_inventory", "version": "v2", "sha256": prompt.sha256, "calls": 2}
    ]
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["photos", "aliases"]
    assert len((a.out / "llm_calls.jsonl").read_text().splitlines()) == 2
    assert len((a.out / "calls.jsonl").read_text().splitlines()) == 2
    assert f"wrote {a.out}" in (a.out / "console.log").read_text()
    # One case per (room, repeat), both answers usable: passed (the kitchen's dropped
    # object is noted, not a failure).
    results = json.loads((a.out / "results.json").read_text())
    summary = results["summary"]
    assert summary["total"] == 2
    assert (summary["passed"], summary["failed"], summary["test_error"]) == (2, 0, 0)
    by_id = {c["id"]: c for c in results["cases"]}
    assert set(by_id) == {"1_kitchen#1", "1_bath#1"}
    assert by_id["1_kitchen#1"]["kind"] == "room" and by_id["1_kitchen#1"]["detail"]["dropped"] == 1
    assert by_id["1_bath#1"]["detail"] == {
        "attempts": 1,
        "errors": [],
        "model": "claude-opus-5-5",
        "dropped": 0,
    }
    assert (
        "**results: 2 passed, 0 failed, 0 test errors (2 cases)**"
        in (a.out / "report.md").read_text()
    )
    assert manifest["tree"] == {
        "component": "room_photos_inventory",
        "version": "v2",
        "link": None,
    }


def test_house_scope_is_one_call_and_an_unusable_answer_is_retried_once(
    tmp_path: Path, photos: Path
) -> None:
    house = mv.ModelHouseInventory.model_validate(
        {
            "rooms": [
                {"room_id": "1_kitchen", "objects": [obj("window", 3, [1, 3])]},
                {"room_id": "1_bath", "objects": [obj("toilet", 1, [2])]},
            ]
        }
    )
    turns = {
        "house_inventory": [ScriptedTurn(parsed=None, text="oops"), ScriptedTurn(parsed=house)]
    }
    provider = PromptKeyedProvider(turns=turns, model="claude-opus-5-5")
    a = args(tmp_path, photos, "house")
    assert asyncio.run(pm.run(a, provider=provider)) == 0
    assert json.loads((a.out / "room_inventory.json").read_text()) == {
        "1_bath": {"toilet": 1},
        "1_kitchen": {"window": 3},
    }
    call = json.loads((a.out / "calls.jsonl").read_text().splitlines()[0])
    assert call["attempts"] == 2 and "no structured answer" in call["errors"][0]
    assert json.loads((a.out / "run.json").read_text())["errors"] == []
    # The house call is the unit of work: one case per repeat, passed on the retry.
    results = json.loads((a.out / "results.json").read_text())
    assert results["summary"]["total"] == 1 and results["summary"]["passed"] == 1
    [case] = results["cases"]
    assert (case["id"], case["kind"], case["outcome"]) == ("house#1", "house", "passed")
    assert case["detail"]["attempts"] == 2 and len(case["detail"]["errors"]) == 1


def test_schedule_scope_needs_a_template_and_keeps_only_template_rows(
    tmp_path: Path, photos: Path
) -> None:
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"rows": [{"name": "Windows"}, {"name": "Appliances"}]}))
    schedule = mv.ModelSchedule.model_validate(
        {
            "rows": [
                {
                    "system": "windows",
                    "bucket": "39",
                    "quantity": 3,
                    "unit": "each",
                    "image_indices": [1, 3],
                    "rooms": ["1_kitchen"],
                    "note": None,
                },
                {
                    "system": "Elevators",
                    "bucket": "39",
                    "quantity": 1,
                    "unit": "each",
                    "image_indices": [1],
                    "rooms": [],
                    "note": None,
                },
            ]
        }  # fmt: skip
    )
    provider = PromptKeyedProvider(
        turns={"house_schedule": [ScriptedTurn(parsed=schedule)]}, model="claude-opus-5-5"
    )
    assert asyncio.run(pm.run(args(tmp_path, photos, "schedule"), provider=provider)) == 2
    a = args(tmp_path, photos, "schedule", template=template)
    assert asyncio.run(pm.run(a, provider=provider)) == 0
    written = json.loads((a.out / "schedule.json").read_text())["repeats"][0]
    assert [r["system"] for r in written] == ["Windows"]
    assert "- Windows\n- Appliances" in str(provider.requests[0].messages[-1].content)
    results = json.loads((a.out / "results.json").read_text())
    [case] = results["cases"]
    assert (case["id"], case["kind"], case["outcome"]) == ("schedule#1", "schedule", "passed")
    assert case["detail"]["dropped"] == 1  # the elevator row is not in the template


def test_read_template_rejects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text(json.dumps(["A", "A"]))
    with pytest.raises(ValueError):
        pm.read_template(path)
    path.write_text(json.dumps(["A", {"name": "B", "distractor": True}]))
    assert pm.read_template(path) == ["A", "B"]


def test_room_of_applies_the_aliases_to_the_file_name_hint() -> None:
    assert pm.room_of(FILES[1], ALIASES) == "1_kitchen"
    assert pm.room_of(FILES[2], ALIASES) == "1_bath"
