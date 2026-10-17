"""``ROOM_STEP_MODE=room_call`` in the room step (:mod:`basis.vision.room_step`, EVAL_REPORT
26.10): which rooms take the room call, what the call sends, how its answer is checked and
stored, the fallback to the reconcile path, and the errors that must still stop the step.

Offline: scripted providers keyed by model and prompt family.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import ModelKeyedProvider

from basis.llm import Budget, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderUnavailable,
)
from basis.llm.provider import BudgetExceededError
from basis.llm.recorder import MemoryRecorder
from basis.vision import multiview, room_step
from basis.vision.prep import prepare_image
from basis.vision.reconcile import ModelInventoryObject, ModelRoomInventory, RoomPhoto
from basis.vision.schemas import (
    Attachment,
    Component,
    ComponentLabel,
    Material,
    PhotoAnalysis,
    RoomType,
)
from basis.vision.surface_material import ModelSurfaceMaterial, SurfaceMaterial

ROOM_MODEL = "claude-sonnet-5-5"
CALL_MODEL = "claude-opus-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)
KITCHEN = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())
BATH = prepare_image(fixture_path("photos", "02_bathroom.jpg").read_bytes())


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


def photo(name: str, *components: Component, image: Any = KITCHEN) -> RoomPhoto:
    return RoomPhoto(
        file=name,
        image=image,
        analysis=PhotoAnalysis(
            room_type=RoomType.KITCHEN, room_confidence=0.9, components=list(components)
        ),
    )


def config(**changes: Any) -> room_step.RoomStepConfig:
    values: dict[str, Any] = {
        "prompt_version": "v3",
        "model": ROOM_MODEL,
        "reasoning_effort": "low",
        "single_photo": "per_photo",
        "materials": "sensitive",
        "mode": "room_call",
    }
    values.update(changes)
    return room_step.load_config(**values)


def call(budget: float = 5.0) -> CallContext:
    return CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="reconcile_rooms",
        budget=Budget(remaining_usd=budget),
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


def bad() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse("no", provider="x", usage=USAGE, model=CALL_MODEL)
    )


def look(material: SurfaceMaterial) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=ModelSurfaceMaterial(material=material, confidence=0.9, evidence="x"),
        usage=USAGE,
    )


def sha(part_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(part_b64)).hexdigest()


# --------------------------------------------------------------------------- configuration


def test_room_call_mode_pins_the_measured_prompt_model_and_effort() -> None:
    loaded = config()
    assert loaded.mode == "room_call" and loaded.room_call is not None
    assert loaded.room_call.prompt.version_id == "room_photos_inventory/v2"
    assert (loaded.room_call.model, loaded.room_call.reasoning_effort) == (CALL_MODEL, "high")
    assert loaded.room_call.max_tokens == multiview.MAX_TOKENS["room"] == 6000
    # The reconcile path's own configuration is untouched.
    assert loaded.room_prompt.version_id == "room_inventory/v3" and loaded.model == ROOM_MODEL


def test_reconcile_mode_has_no_room_call() -> None:
    loaded = config(mode="reconcile")
    assert loaded.mode == "reconcile" and loaded.room_call is None
    assert room_step.room_path(loaded, RoomType.KITCHEN.value) == "reconcile"


@pytest.mark.parametrize(
    ("room_type", "path"),
    [
        (RoomType.KITCHEN, "room_call"),
        (RoomType.BATHROOM, "room_call"),
        (RoomType.GARAGE, "room_call"),
        (RoomType.BASEMENT, "room_call"),
        (RoomType.STAIRWAY, "room_call"),
        (RoomType.AMBIGUOUS, "room_call"),  # a file-name room whose photos all abstained
        (RoomType.OTHER, "room_call"),
        (RoomType.EXTERIOR, "reconcile"),
        (RoomType.PARKING, "reconcile"),
        (RoomType.MECHANICAL, "reconcile"),
    ],
)
def test_interior_rooms_take_the_room_call_exterior_and_site_rooms_do_not(
    room_type: RoomType, path: str
) -> None:
    """The rule fixed before the second package (26.7: the study's x_ groups were
    exterior, exterior and mechanical by vote)."""
    assert room_step.room_path(config(), room_type.value) == path


# --------------------------------------------------------------------------- the call


async def test_an_interior_room_is_one_call_with_all_its_photos_and_no_per_photo_lists() -> None:
    chat = ModelKeyedProvider(
        turns={
            (CALL_MODEL, "room_photos_inventory"): [
                inventory(("kitchen_cabinet", 18, [1, 2], "wood"), ("window", 3, [2, 3], "glass"))
            ]
        }
    )
    photos = [
        photo("a", comp("kitchen_cabinet", 6)),
        photo("b", comp("kitchen_cabinet", 9), image=BATH),
        photo("c", comp("window", 2)),
    ]
    out = await room_step.inventory_room(
        "r", photos, config(), call=call(), provider=chat, room_type="kitchen"
    )
    [request] = chat.requests  # no material second look on the room call's answer
    assert request.prompt_version == "room_photos_inventory/v2"
    assert (request.model, request.reasoning_effort, request.max_tokens) == (
        CALL_MODEL,
        "high",
        6000,
    )
    assert request.activity == "reconcile_rooms"
    images = [m for m in request.messages if m.images]
    assert [m.content for m in images] == ["Image 1:", "Image 2:", "Image 3:"]
    assert [sha(m.images[0].data_base64) for m in images] == [
        KITCHEN.sha256,
        BATH.sha256,
        KITCHEN.sha256,
    ]
    text = request.messages[-1].content
    assert text.startswith("The 3 images above all show the same room.")
    assert "kitchen_cabinet" not in text  # the per-photo lists are not sent
    assert (out.step, out.path, out.room_call_fallback) == ("room_call", "room_call", None)
    assert out.result.inventory.counts() == {"kitchen_cabinet": 18, "window": 3}
    assert out.result.files_for(out.result.inventory.objects[1]) == ["b", "c"]
    assert out.looks == [] and not out.needs_review
    [record] = out.result.calls
    assert (record.attempts, record.model, record.files) == (1, CALL_MODEL, ["a", "b", "c"])
    assert out.cost_usd == pytest.approx(record.cost_usd) and record.cost_usd > 0


async def test_a_one_photo_interior_room_gets_the_room_call_too() -> None:
    """Section 26.4 room-called every room, one photo or thirteen."""
    chat = ModelKeyedProvider(
        turns={(CALL_MODEL, "room_photos_inventory"): [inventory(("toilet", 1, [1], "ceramic"))]}
    )
    out = await room_step.inventory_room(
        "r",
        [photo("a", comp("toilet"))],
        config(),
        call=call(),
        provider=chat,
        room_type="bathroom",
    )
    assert out.step == "room_call" and len(chat.requests) == 1


async def test_an_exterior_room_keeps_the_reconcile_path_with_its_second_look() -> None:
    chat = ModelKeyedProvider(
        turns={
            (ROOM_MODEL, "room_inventory"): [inventory(("wall_finish", 1, [1, 2], "wood"))],
            (ROOM_MODEL, "surface_material"): [look(SurfaceMaterial.PAINTED_MASONRY)],
        }
    )
    photos = [photo("a", comp("wall_finish")), photo("b", comp("wall_finish"))]
    out = await room_step.inventory_room(
        "x", photos, config(), call=call(), provider=chat, room_type="exterior"
    )
    assert [r.prompt_version for r in chat.requests] == [
        "room_inventory/v3",
        "surface_material/v3",
    ]
    assert (out.step, out.path, out.room_call_fallback) == ("model", "reconcile", None)
    assert out.result.inventory.objects[0].material is Material.PAINTED_MASONRY


async def test_without_a_room_type_the_room_takes_the_reconcile_path() -> None:
    chat = ModelKeyedProvider(
        turns={(ROOM_MODEL, "room_inventory"): [inventory(("window", 2, [1, 2], "glass"))]}
    )
    photos = [photo("a", comp("window")), photo("b", comp("window"))]
    out = await room_step.inventory_room(
        "r", photos, config(materials="none"), call=call(), provider=chat
    )
    assert out.path == "reconcile" and chat.requests[0].prompt_version == "room_inventory/v3"


async def test_an_object_pointing_at_no_photo_is_dropped_and_the_room_flagged() -> None:
    chat = ModelKeyedProvider(
        turns={
            (CALL_MODEL, "room_photos_inventory"): [
                inventory(("window", 2, [1, 2], "glass"), ("interior_door", 1, [3], "wood"))
            ]
        }
    )
    photos = [photo("a", comp("window")), photo("b", comp("window"))]
    out = await room_step.inventory_room(
        "r", photos, config(), call=call(), provider=chat, room_type="hallway"
    )
    assert out.result.inventory.counts() == {"window": 2}
    assert out.needs_review and out.result.review_reason == "dropped_objects:1"
    [dropped] = out.result.dropped
    assert dropped.reason.startswith("image_index_out_of_range:[3]")


# --------------------------------------------------------------------------- fallback


async def test_an_unusable_room_call_falls_back_to_the_reconcile_path_keeping_its_cost() -> None:
    chat = ModelKeyedProvider(
        turns={
            (CALL_MODEL, "room_photos_inventory"): [bad(), bad()],
            (ROOM_MODEL, "room_inventory"): [inventory(("kitchen_cabinet", 8, [1, 2], "wood"))],
        }
    )
    photos = [photo("a", comp("kitchen_cabinet", 6)), photo("b", comp("kitchen_cabinet", 8))]
    ctx = call()
    out = await room_step.inventory_room(
        "r", photos, config(materials="none"), call=ctx, provider=chat, room_type="kitchen"
    )
    assert [r.prompt_version.split("/")[0] for r in chat.requests] == [
        "room_photos_inventory",
        "room_photos_inventory",
        "room_inventory",
    ]
    assert (out.step, out.path, out.room_call_fallback) == (
        "model",
        "room_call",
        "invalid_model_output",
    )
    assert out.result.inventory.counts() == {"kitchen_cabinet": 8}
    failed, reconciled = out.result.calls
    assert (failed.attempts, len(failed.errors), failed.response) == (2, 2, None)
    assert failed.cost_usd > 0 and reconciled.model == ROOM_MODEL
    # Every attempt was charged, the failed ones included.
    assert out.cost_usd == pytest.approx(ctx.budget.spent_usd)


async def test_a_room_beyond_the_request_image_limit_falls_back_without_a_call() -> None:
    count = multiview.MAX_IMAGES + 1
    slices = len(range(0, count, 10))  # the reconciliation call's slices of ten
    chat = ModelKeyedProvider(
        turns={
            (ROOM_MODEL, "room_inventory"): [
                inventory(("window", 1, [1], "glass")) for _ in range(slices)
            ]
        }
    )
    photos = [photo(f"p{i}", comp("window")) for i in range(count)]
    out = await room_step.inventory_room(
        "r", photos, config(materials="none"), call=call(), provider=chat, room_type="kitchen"
    )
    assert {r.prompt_version for r in chat.requests} == {"room_inventory/v3"}
    assert out.room_call_fallback == "too_many_photos" and out.result.calls[0].attempts == 0


# --------------------------------------------------------------------------- errors that stop


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("down", provider="anthropic"),
        ProviderAccountError("credit balance is too low", provider="anthropic", status=400),
    ],
)
async def test_transient_and_account_errors_propagate(error: Exception) -> None:
    """The activity retries a transient error and stops on an account error (PR 53); the
    room call must not swallow either into a fallback."""
    chat = ModelKeyedProvider(
        turns={(CALL_MODEL, "room_photos_inventory"): [ScriptedTurn(raise_error=error)]}
    )
    with pytest.raises(type(error)):
        await room_step.inventory_room(
            "r", [photo("a")], config(), call=call(), provider=chat, room_type="kitchen"
        )


async def test_an_exhausted_budget_propagates_before_the_call() -> None:
    chat = ModelKeyedProvider(turns={})
    with pytest.raises(BudgetExceededError):
        await room_step.inventory_room(
            "r", [photo("a")], config(), call=call(budget=0.0), provider=chat, room_type="kitchen"
        )
    assert chat.requests == []
