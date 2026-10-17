"""``evals.replay_rooms``: the production room step on stored per-photo outputs, offline.

The runner must build the room step from the settings exactly as ``reconcile_rooms`` does,
so a setting flipped here is the setting flipped in the worker. Fixture photos named the
engineer's way, a stored per-photo run written from the fixture labels, the fixture
provider (and a fake segmenter) instead of the real ones.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import (
    PHOTO_COST_USD,
    FakeSegmenter,
    FixtureVisionProvider,
    analysis_for,
    photo_labels,
)

from basis.config import reset_settings
from basis.vision import room_step
from basis.vision.schemas import PhotoAnalysis
from evals import replay_rooms

# file -> fixture photo
PHOTOS = {
    "21_1st Floor Kitchen photo 1.jpg": "01_kitchen.jpg",
    "22_1st Floor Kitchen photo 2.jpg": "01_kitchen.jpg",
    "30_2nd Floor Bath.jpg": "02_bathroom.jpg",
    "01_Property Front.jpg": "06_exterior_front.jpg",
    "02_Property Rear.jpg": "06_exterior_front.jpg",
}


@pytest.fixture
def package(tmp_path: Path) -> dict[str, Path]:
    photos = tmp_path / "photos"
    per_photo = tmp_path / "per_photo"
    photos.mkdir()
    per_photo.mkdir()
    labels = photo_labels()
    with (per_photo / "photo_raw.jsonl").open("w") as raw:
        for name, fixture in PHOTOS.items():
            shutil.copy(fixture_path("photos", fixture), photos / name)
            model = analysis_for(labels[f"photos/{fixture}"])
            analysis = PhotoAnalysis.model_validate(model.model_dump(mode="json"))
            raw.write(
                json.dumps(
                    {"file": name, "repeat": 1, "analysis": analysis.model_dump(mode="json")}
                )
                + "\n"
            )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({"x_property_rear": "x_property_front"}))
    return {"photos": photos, "per_photo": per_photo, "aliases": aliases, "out": tmp_path / "out"}


def args(package: dict[str, Path]) -> Any:
    return replay_rooms.parse_args(
        [
            "--photos",
            str(package["photos"]),
            "--per-photo",
            str(package["per_photo"]),
            "--aliases",
            str(package["aliases"]),
            "--out",
            str(package["out"]),
        ]
    )


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ROOM_RECONCILE", "true")
    reset_settings()
    yield monkeypatch
    reset_settings()


async def test_room_call_mode_replays_the_activitys_paths(
    package: dict[str, Path], settings_env: pytest.MonkeyPatch
) -> None:
    settings_env.setenv("ROOM_STEP_MODE", "room_call")
    fixture = FixtureVisionProvider()
    assert await replay_rooms.run(args(package), provider=fixture) == 0
    run = json.loads((package["out"] / "run.json").read_text())
    assert run["room_step_mode"] == "room_call"
    assert run["room_call"] == {
        "prompt": "room_photos_inventory/v2",
        "model": "claude-opus-5-5",
        "reasoning_effort": "high",
    }
    assert run["rooms_by_path"] == {
        "1_kitchen": "room_call",
        "2_bath": "room_call",
        "x_property_front": "reconcile",
    }
    assert run["paths"] == {"room_call": 2, "reconcile": 1}
    # Two room calls; the two-photo exterior group's reconciliation call (no second look:
    # the fixture's exterior lists no sensitive surface).
    assert sorted(r.prompt_version for r in fixture.requests) == [
        "room_inventory/v3",
        "room_photos_inventory/v2",
        "room_photos_inventory/v2",
    ]
    assert run["spent_usd"] == pytest.approx(3 * PHOTO_COST_USD)
    inventory = json.loads((package["out"] / "room_inventory.json").read_text())
    assert inventory["1_kitchen"] == {"kitchen_cabinet": 6, "countertop": 1}
    lines = [json.loads(x) for x in (package["out"] / "room_raw.jsonl").read_text().splitlines()]
    kitchen = next(x for x in lines if x["room_id"] == "1_kitchen")
    assert (kitchen["path"], kitchen["step"], kitchen["room_type"]) == (
        "room_call",
        "room_call",
        "kitchen",
    )
    assert kitchen["cost_usd"] == pytest.approx(PHOTO_COST_USD)


async def test_reconcile_mode_replays_todays_room_step(
    package: dict[str, Path], settings_env: pytest.MonkeyPatch
) -> None:
    fixture = FixtureVisionProvider()
    assert await replay_rooms.run(args(package), provider=fixture) == 0
    run = json.loads((package["out"] / "run.json").read_text())
    assert run["room_step_mode"] == "reconcile" and run["room_call"] is None
    assert set(run["rooms_by_path"].values()) == {"reconcile"}
    # Two two-photo rooms get the call; the one-photo bath is its analysis.
    assert [r.prompt_version for r in fixture.requests] == ["room_inventory/v3"] * 2


async def test_surface_crops_run_after_the_inventory(
    package: dict[str, Path], settings_env: pytest.MonkeyPatch
) -> None:
    settings_env.setenv("MATERIALS_SECOND_LOOK", "surface_crops")
    fixture = FixtureVisionProvider()
    segmenter = FakeSegmenter()
    assert await replay_rooms.run(args(package), provider=fixture, segmenter=segmenter) == 0
    run = json.loads((package["out"] / "run.json").read_text())
    assert run["materials_second_look"] == "surface_crops"
    assert run["segmenter"] == "fake-segmenter" and segmenter.calls == len(PHOTOS)
    crops = [r for r in fixture.requests if r.prompt_version == "surface_crop_material/v1"]
    assert len(crops) == 3 * len(PHOTOS)
    lines = [json.loads(x) for x in (package["out"] / "room_raw.jsonl").read_text().splitlines()]
    assert all(x["surface_crops"]["flags"] == [] for x in lines)


def test_stored_rows_round_trip_like_the_activitys() -> None:
    model = analysis_for(photo_labels()["photos/01_kitchen.jpg"])
    analysis = PhotoAnalysis.model_validate(model.model_dump(mode="json"))
    rebuilt = room_step.analysis_from_rows(
        "kitchen", replay_rooms.stored_rows(analysis), analysis.room_confidence
    )
    assert [(c.label, c.count, c.material) for c in rebuilt.components] == [
        (c.label, c.count, c.material) for c in analysis.components
    ]


async def test_a_missing_per_photo_run_is_an_input_error(tmp_path: Path) -> None:
    parsed = replay_rooms.parse_args(
        ["--photos", str(tmp_path), "--per-photo", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert await replay_rooms.run(parsed, provider=FixtureVisionProvider()) == 2


async def test_the_crop_scorer_reads_a_replay_run(
    package: dict[str, Path], settings_env: pytest.MonkeyPatch
) -> None:
    """``evals.predict_surface_crops score --crops`` takes the replay's crops directly
    (EVAL_REPORT 29.5: the second package's crop score)."""
    from evals import predict_surface_crops as ps

    settings_env.setenv("MATERIALS_SECOND_LOOK", "surface_crops")
    await replay_rooms.run(
        args(package), provider=FixtureVisionProvider(), segmenter=FakeSegmenter()
    )
    [predicted] = ps.crop_predictions(package["out"])
    assert set(predicted) == set(PHOTOS)
    # The fixture crop answers: tile floor, painted wall with a wallpaper band, painted ceiling.
    assert predicted["30_2nd Floor Bath.jpg"] == {
        "floor": {"ceramic_tile"},
        "wall": {"painted_drywall"},
        "wall_lower": {"wallpaper"},
        "ceiling": {"painted_drywall"},
    }
