"""Regression: ``ROOM_STEP_MODE=reconcile`` (the default) is the room step before the room
call was wired, byte for byte.

``room_step_reconcile_golden.json`` was written by :func:`dump` running on ``main`` at
35db766 (before ``ROOM_STEP_MODE`` existed): five rooms through
:func:`basis.vision.room_step.inventory_room` with scripted answers (a one-photo room, a
two-photo room with the material second look, an answer unusable twice, eleven photos in
two slices, a one-photo room sent to the call), recording every request the provider saw
(model, prompt, effort, budget, every message's text and image hash, the system prompt's
hash) and every result (inventory, files, source, review flag, drops, calls with attempts,
costs, raw answers, second looks). The test runs the same scenarios on the current code,
with the configuration built the way the activity builds it at the defaults, and compares
the JSON text. Regenerate only for an intended change of the reconcile path:
``python -m tests.unit.vision.test_room_step_unchanged`` from ``backEnd/`` writes it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from fixtures import fixture_path
from llm_fakes import ModelKeyedProvider

from basis.llm import Budget, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse
from basis.llm.provider import ChatRequest
from basis.llm.recorder import MemoryRecorder
from basis.vision import room_step
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

GOLDEN = Path(__file__).with_name("room_step_reconcile_golden.json")
MODEL = "claude-sonnet-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)
STUDY = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _comp(label: str, count: int, material: str) -> Component:
    return Component.model_validate(
        {
            "label": label,
            "count": count,
            "material": material,
            "attachment": "permanent",
            "confidence": 0.8,
            "note": f"{label} note",
        }
    )


def _photo(name: str, image: str, *components: Component) -> RoomPhoto:
    return RoomPhoto(
        file=name,
        image=prepare_image(fixture_path("photos", image).read_bytes()),
        analysis=PhotoAnalysis(
            room_type=RoomType.KITCHEN, room_confidence=0.9, components=list(components)
        ),
    )


def _inventory(*objects: tuple[str, int, list[int], str]) -> ScriptedTurn:
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
                    note="seen",
                )
                for label, count, indices, material in objects
            ]
        ),
        usage=USAGE,
    )


def _bad() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse(
            "unusable", provider="x", usage=USAGE, model=MODEL, raw={"bad": True}
        )
    )


def _look(material: SurfaceMaterial) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=ModelSurfaceMaterial(material=material, confidence=0.9, evidence="looked"),
        usage=USAGE,
    )


def _request(request: ChatRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "prompt_version": request.prompt_version,
        "system_sha256": hashlib.sha256(request.system.encode()).hexdigest(),
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "reasoning_effort": request.reasoning_effort,
        "activity": request.activity,
        "study_id": request.study_id,
        "response_model": None
        if request.response_model is None
        else request.response_model.__name__,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "images": [
                    hashlib.sha256(base64.b64decode(i.data_base64)).hexdigest() for i in m.images
                ],
            }
            for m in request.messages
        ],
    }


def _result(out: room_step.RoomStepResult) -> dict[str, Any]:
    r = out.result
    return {
        "step": out.step,
        "needs_review": out.needs_review,
        "room_id": r.room_id,
        "files": r.files,
        "source": r.source,
        "review_reason": r.review_reason,
        "objects": [o.model_dump(mode="json") for o in r.inventory.objects],
        "seen_in": [r.files_for(o) for o in r.inventory.objects],
        "dropped": [{"reason": d.reason, "object": d.object, "call": d.call} for d in r.dropped],
        "calls": [
            {
                "files": c.files,
                "first_index": c.first_index,
                "attempts": c.attempts,
                "provider": c.provider,
                "model": c.model,
                "cost_usd": round(c.cost_usd, 9),
                "errors": c.errors,
                "response": c.response,
                "raw": c.raw,
            }
            for c in r.calls
        ],
        "looks": [look.record() for look in out.looks],
    }


async def dump() -> str:
    """Run the five rooms (module doc) and return their record as JSON text."""
    config = room_step.load_config(
        prompt_version="v3",
        model=MODEL,
        reasoning_effort="low",
        single_photo="per_photo",
        materials="sensitive",
    )
    one = room_step.load_config(
        prompt_version="v3",
        model=MODEL,
        reasoning_effort="low",
        single_photo="reconcile",
        materials="none",
    )
    k1, k2 = "01_kitchen.jpg", "02_bathroom.jpg"
    rooms: list[tuple[str, list[RoomPhoto], room_step.RoomStepConfig, dict[Any, Any]]] = [
        (
            "single",
            [_photo("a", k1, _comp("kitchen_cabinet", 6, "wood"), _comp("window", 1, "glass"))],
            config,
            {},
        ),
        (
            "hall",
            [
                _photo("a", k1, _comp("wainscoting", 1, "wood"), _comp("tile", 1, "unknown")),
                _photo("b", k2, _comp("wainscoting", 1, "wood"), _comp("window", 2, "glass")),
            ],
            config,
            {
                (MODEL, "room_inventory"): [
                    _inventory(
                        ("wainscoting", 1, [1, 2], "wood"),
                        ("tile", 1, [1], "unknown"),
                        ("window", 2, [2], "glass"),
                        ("light_fixture", 1, [3], "metal"),
                    )
                ],
                (MODEL, "surface_material"): [
                    _look(SurfaceMaterial.WALLPAPER),
                    _look(SurfaceMaterial.CERAMIC),
                ],
            },
        ),
        (
            "unusable",
            [
                _photo("a", k1, _comp("kitchen_cabinet", 6, "wood")),
                _photo("b", k2, _comp("kitchen_cabinet", 8, "wood")),
            ],
            config,
            {(MODEL, "room_inventory"): [_bad(), _bad()]},
        ),
        (
            "eleven",
            [
                _photo(f"p{i:02d}", k1 if i % 2 else k2, _comp("window", 2, "glass"))
                for i in range(11)
            ],
            config,
            {
                (MODEL, "room_inventory"): [
                    _inventory(("window", 6, [1, 2, 3], "glass")),
                    _inventory(("window", 5, [1, 5], "glass")),
                ]
            },
        ),
        (
            "single_called",
            [_photo("a", k1, _comp("kitchen_cabinet", 6, "wood"))],
            one,
            {(MODEL, "room_inventory"): [_inventory(("kitchen_cabinet", 5, [1], "wood"))]},
        ),
    ]
    record: dict[str, Any] = {}
    for room_id, photos, room_config, turns in rooms:
        provider = ModelKeyedProvider(turns=turns)
        ctx = CallContext(
            study_id=STUDY,
            run_id=None,
            activity="reconcile_rooms",
            budget=Budget(remaining_usd=5.0),
            recorder=MemoryRecorder(),
        )
        out = await room_step.inventory_room(
            room_id, photos, room_config, call=ctx, provider=provider
        )
        record[room_id] = {
            "requests": [_request(r) for r in provider.requests],
            "result": _result(out),
            "spent_usd": round(ctx.budget.spent_usd, 9),
        }
    return json.dumps(record, indent=1, sort_keys=True) + "\n"


async def test_reconcile_mode_is_the_room_step_before_the_room_call() -> None:
    assert await dump() == GOLDEN.read_text(encoding="utf-8")


async def test_the_activitys_default_configuration_is_reconcile_mode() -> None:
    """What ``reconcile_rooms`` builds at the default settings has no room call, so every
    room goes down the path the golden records, whatever its type."""
    from basis.config import load_settings

    settings = load_settings()
    config = room_step.load_config(
        prompt_version=settings.room_prompt_version,
        model=settings.room_model,
        reasoning_effort=settings.room_reasoning_effort,
        single_photo=settings.room_single_photo,
        materials=settings.room_materials,
        mode=settings.room_step_mode,
        room_call_model=settings.room_call_model,
        room_call_reasoning_effort=settings.room_call_reasoning_effort,
    )
    assert config.room_call is None
    assert {room_step.room_path(config, t.value) for t in RoomType} == {"reconcile"}


if __name__ == "__main__":
    GOLDEN.write_text(asyncio.run(dump()), encoding="utf-8")
    print(f"wrote {GOLDEN}")
