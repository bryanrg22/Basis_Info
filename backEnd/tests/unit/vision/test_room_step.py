"""The pipeline's room step (:mod:`basis.vision.room_step`, EVAL_REPORT section 16.5).

Offline: scripted providers keyed by model and prompt family. The database side is in
``tests/unit/activities/test_reconcile_rooms.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import ModelKeyedProvider

from basis.activities.process_assets import asset_components
from basis.db.models import Component as ComponentRow
from basis.llm import Budget, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse
from basis.llm.prompts import PromptError
from basis.llm.recorder import MemoryRecorder
from basis.vision import room_step
from basis.vision.prep import prepare_image
from basis.vision.reconcile import ModelInventoryObject, ModelRoomInventory, RoomPhoto
from basis.vision.rooms import ROOM_INVENTORY_SOURCE
from basis.vision.schemas import (
    Attachment,
    Component,
    ComponentLabel,
    Material,
    PhotoAnalysis,
    RoomType,
)
from basis.vision.surface_material import ModelSurfaceMaterial, SurfaceMaterial

MODEL = "claude-sonnet-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)
IMAGE = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())


def comp(label: str, count: int = 1, material: str = "unknown") -> Component:
    return Component.model_validate(
        {
            "label": label,
            "count": count,
            "material": material,
            "attachment": "permanent",
            "confidence": 0.8,
        }
    )


def photo(name: str, *components: Component) -> RoomPhoto:
    return RoomPhoto(
        file=name,
        image=IMAGE,
        analysis=PhotoAnalysis(
            room_type=RoomType.KITCHEN, room_confidence=0.9, components=list(components)
        ),
    )


def config(**changes: Any) -> room_step.RoomStepConfig:
    values: dict[str, Any] = {
        "prompt_version": "v3",
        "model": MODEL,
        "reasoning_effort": "low",
        "single_photo": "per_photo",
        "materials": "sensitive",
    }
    values.update(changes)
    return room_step.load_config(**values)


def call() -> CallContext:
    return CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="reconcile_rooms",
        budget=Budget(remaining_usd=5.0),
        recorder=MemoryRecorder(),
    )


def inventory(*objects: tuple[str, int, list[int], str]) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=ModelRoomInventory(
            objects=[
                ModelInventoryObject(
                    label=ComponentLabel(label),
                    count=count,
                    image_indices=indices,
                    material=Material(material),
                    attachment=Attachment.PERMANENT,
                    confidence=0.8,
                    note=None,
                )
                for label, count, indices, material in objects
            ]
        ),
        usage=USAGE,
    )


def look(material: SurfaceMaterial, confidence: float = 0.9) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=ModelSurfaceMaterial(material=material, confidence=confidence, evidence="x"),
        usage=USAGE,
    )


def bad() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse("no", provider="x", usage=USAGE, model=MODEL)
    )


def test_load_config_pins_the_released_prompts() -> None:
    loaded = config()
    assert loaded.room_prompt.version_id == "room_inventory/v3"
    assert loaded.materials_prompt is not None
    assert loaded.materials_prompt.version_id == "surface_material/v3"
    assert config(materials="none").materials_prompt is None
    with pytest.raises(PromptError):
        config(prompt_version="v99")


async def test_a_one_photo_room_is_its_photo_analysis_without_a_call() -> None:
    chat = ModelKeyedProvider(turns={})
    single = photo("a", comp("kitchen_cabinet", 6), comp("kitchen_cabinet", 2), comp("window", 1))
    out = await room_step.inventory_room(
        "r", [single], config(materials="none"), call=call(), provider=chat
    )
    assert out.step == "per_photo" and not chat.requests and not out.needs_review
    # Merged by label within the photo, as the heuristic merge does.
    assert out.result.inventory.counts() == {"kitchen_cabinet": 8, "window": 1}


async def test_a_one_photo_room_is_reconciled_when_asked() -> None:
    chat = ModelKeyedProvider(
        turns={(MODEL, "room_inventory"): [inventory(("kitchen_cabinet", 5, [1], "wood"))]}
    )
    out = await room_step.inventory_room(
        "r",
        [photo("a", comp("kitchen_cabinet", 6))],
        config(single_photo="reconcile", materials="none"),
        call=call(),
        provider=chat,
    )
    assert out.step == "model" and out.result.inventory.counts() == {"kitchen_cabinet": 5}


async def test_a_multi_photo_room_gets_one_call_with_the_configured_model_and_effort() -> None:
    chat = ModelKeyedProvider(
        turns={(MODEL, "room_inventory"): [inventory(("kitchen_cabinet", 8, [1, 2], "wood"))]}
    )
    photos = [photo("a", comp("kitchen_cabinet", 6)), photo("b", comp("kitchen_cabinet", 8))]
    out = await room_step.inventory_room(
        "r", photos, config(materials="none"), call=call(), provider=chat
    )
    [request] = chat.requests
    assert request.prompt_version == "room_inventory/v3"
    assert (request.model, request.reasoning_effort) == (MODEL, "low")
    assert out.step == "model" and out.result.inventory.counts() == {"kitchen_cabinet": 8}
    assert out.result.files_for(out.result.inventory.objects[0]) == ["a", "b"]


async def test_an_unusable_answer_falls_back_to_the_heuristic_and_flags() -> None:
    chat = ModelKeyedProvider(turns={(MODEL, "room_inventory"): [bad(), bad()]})
    photos = [photo("a", comp("kitchen_cabinet", 6)), photo("b", comp("kitchen_cabinet", 8))]
    out = await room_step.inventory_room(
        "r", photos, config(materials="none"), call=call(), provider=chat
    )
    assert out.step == "heuristic" and out.needs_review
    assert out.result.inventory.counts() == {"kitchen_cabinet": 8}


async def test_the_sensitive_second_look_runs_on_every_wall_and_tile_surface() -> None:
    chat = ModelKeyedProvider(
        turns={
            (MODEL, "room_inventory"): [
                inventory(
                    ("wainscoting", 1, [1], "wood"),
                    ("tile", 1, [2], "ceramic"),
                    ("kitchen_cabinet", 4, [1], "wood"),
                )
            ],
            (MODEL, "surface_material"): [
                look(SurfaceMaterial.WALLPAPER),
                look(SurfaceMaterial.PORCELAIN),
            ],
        }
    )
    photos = [
        photo("a", comp("wainscoting", 1, "wood"), comp("kitchen_cabinet", 4)),
        photo("b", comp("tile", 1, "ceramic")),
    ]
    out = await room_step.inventory_room("r", photos, config(), call=call(), provider=chat)
    materials = {o.label.value: o.material for o in out.result.inventory.objects}
    # Agreed-on materials are still checked ("sensitive"); wallpaper is its own material now.
    assert materials == {
        "wainscoting": Material.WALLPAPER,
        "tile": Material.PORCELAIN,
        "kitchen_cabinet": Material.WOOD,
    }
    looked = [r for r in chat.requests if r.prompt_version.startswith("surface_material")]
    assert [r.prompt_version for r in looked] == ["surface_material/v3"] * 2
    assert {r.model for r in looked} == {MODEL}
    assert [x.label for x in out.looks] == ["wainscoting", "tile"]
    assert all(x.applied for x in out.looks)


async def test_the_second_look_reaches_a_one_photo_room_too() -> None:
    chat = ModelKeyedProvider(
        turns={(MODEL, "surface_material"): [look(SurfaceMaterial.PAINTED_MASONRY)]}
    )
    out = await room_step.inventory_room(
        "r", [photo("a", comp("wall_finish", 1, "concrete"))], config(), call=call(), provider=chat
    )
    assert out.step == "per_photo"
    assert out.result.inventory.objects[0].material is Material.PAINTED_MASONRY


async def test_the_disagree_trigger_skips_surfaces_every_photo_agrees_on() -> None:
    chat = ModelKeyedProvider(turns={})
    out = await room_step.inventory_room(
        "r",
        [photo("a", comp("tile", 1, "ceramic"))],
        config(materials="disagree"),
        call=call(),
        provider=chat,
    )
    assert not chat.requests and out.looks == []


def test_analysis_from_rows_rebuilds_counts_and_keeps_corrections() -> None:
    rows = [
        {
            "label": "kitchen_cabinet",
            "material": "wood",
            "attachment": "permanent",
            "confidence": 0.9,
            "raw": {"count": 6},
        },
        # A reviewer's free-text material and an unknown label are not fatal.
        {
            "label": "Pantry Shelving",
            "material": "reclaimed oak",
            "attachment": None,
            "confidence": None,
            "raw": {},
        },
        {
            "label": "window",
            "material": None,
            "attachment": "removable",
            "confidence": 1.0,
            "raw": {"count": True, "note": "x" * 999},
        },
    ]
    analysis = room_step.analysis_from_rows("kitchen", rows, 0.7)
    assert analysis.room_type is RoomType.KITCHEN and analysis.room_confidence == 0.7
    cabinet, unknown, window = analysis.components
    assert (cabinet.count, cabinet.material) == (6, Material.WOOD)
    assert (unknown.label, unknown.note) == (ComponentLabel.OTHER, "Pantry Shelving")
    assert (unknown.material, unknown.attachment, unknown.count) == (
        Material.OTHER,
        Attachment.UNKNOWN,
        1,
    )
    assert (window.count, window.material, window.attachment) == (
        1,
        Material.UNKNOWN,
        Attachment.REMOVABLE,
    )
    assert window.note is not None and len(window.note) <= 200


def test_a_corrected_room_type_outside_the_vocabulary_is_other() -> None:
    assert room_step.analysis_from_rows("Main Kitchen", []).room_type is RoomType.OTHER


# --------------------------------------------------------------------------- process_assets


def row(room: uuid.UUID | None, doc: uuid.UUID | None, label: str, inventory: bool = False) -> Any:
    raw = {"source": ROOM_INVENTORY_SOURCE if inventory else "vision", "count": 1}
    return ComponentRow(
        id=uuid.uuid4(), room_id=room, document_id=doc, label=label, raw=raw, revision=1
    )


def test_assets_come_from_the_inventory_of_a_reconciled_room_only() -> None:
    kitchen, bath = uuid.uuid4(), uuid.uuid4()
    photo_a, photo_b, photo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    per_photo = [
        row(kitchen, photo_a, "kitchen_cabinet"),
        row(kitchen, photo_b, "kitchen_cabinet"),
        row(bath, photo_c, "toilet"),
        row(None, photo_c, "other"),
    ]
    inventory_rows = [row(kitchen, None, "kitchen_cabinet", inventory=True)]
    orphan = [row(None, None, "window", inventory=True)]
    chosen = asset_components([*per_photo, *inventory_rows, *orphan])
    assert chosen == [per_photo[2], per_photo[3], inventory_rows[0]]


def test_without_any_inventory_every_per_photo_row_is_an_asset() -> None:
    """Regression: ROOM_RECONCILE off (or the step failed) builds assets as before."""
    room = uuid.uuid4()
    rows = [row(room, uuid.uuid4(), "kitchen_cabinet"), row(room, uuid.uuid4(), "countertop")]
    assert asset_components(rows) == rows


# --------------------------------------------------------------------------- second-look model


async def test_the_second_look_runs_on_its_own_model_when_set() -> None:
    """EVAL_REPORT 28.2's split: the reconciliation call on a cheaper model, the material
    second look kept on Sonnet at its own effort."""
    reconciler = "gpt-6-luna"
    chat = ModelKeyedProvider(
        turns={
            (reconciler, "room_inventory"): [inventory(("wainscoting", 1, [1, 2], "wood"))],
            (MODEL, "surface_material"): [look(SurfaceMaterial.WALLPAPER)],
        }
    )
    photos = [
        photo("a", comp("wainscoting", 1, "wood")),
        photo("b", comp("wainscoting", 1, "wood")),
    ]
    split = config(
        model=reconciler,
        reasoning_effort="minimal",
        materials_model=MODEL,
        materials_reasoning_effort="medium",
    )
    assert (split.look_model, split.look_reasoning_effort) == (MODEL, "medium")
    out = await room_step.inventory_room("r", photos, split, call=call(), provider=chat)
    [reconciled, looked] = chat.requests
    assert (reconciled.model, reconciled.reasoning_effort) == (reconciler, "minimal")
    assert (looked.model, looked.reasoning_effort) == (MODEL, "medium")
    assert looked.prompt_version == "surface_material/v3"
    assert out.result.inventory.objects[0].material is Material.WALLPAPER
    assert [x.model for x in out.looks] == [MODEL]


def test_without_a_second_look_model_the_look_follows_the_reconciler() -> None:
    plain = config(model="gpt-6-luna", reasoning_effort="minimal")
    assert (plain.look_model, plain.look_reasoning_effort) == ("gpt-6-luna", "minimal")
