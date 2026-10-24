"""``reconcile_room`` with a scripted provider: request layout, index checks, retry and
fallback, splitting rooms over ten photos, budget; and the ``heuristic_merge`` baseline."""

from __future__ import annotations

import base64
import uuid
from dataclasses import replace

import pytest
from fixtures import fixture_path

from basis.llm import Budget, BudgetExceededError, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import (
    ProviderBadResponse,
    ProviderConfigError,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.prompts import load
from basis.llm.recorder import MemoryRecorder
from basis.vision import overlap
from basis.vision.prep import PreparedImage, prepare_image
from basis.vision.reconcile import (
    INVALID_OUTPUT,
    MAX_IMAGES_PER_CALL,
    MAX_TOKENS,
    InventoryObject,
    ModelInventoryObject,
    ModelRoomInventory,
    ReconcileContext,
    RoomInventory,
    RoomPhoto,
    build_request,
    chunks,
    heuristic_merge,
    merge_calls,
    reconcile_room,
    render_analysis,
)
from basis.vision.schemas import Component, PhotoAnalysis, RoomType

USAGE = Usage(input_tokens=1000, output_tokens=200)
# The v1 layout (one line per image); v2's per-label layout is tested in test_room_steps.py.
V1 = load("room_inventory", "v1")
MODEL = "gpt-4.1-mini"
KITCHEN = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())
BATHROOM = prepare_image(fixture_path("photos", "02_bathroom.jpg").read_bytes())


def component(label: str, count: int = 1, **extra: object) -> Component:
    return Component.model_validate(
        {
            "label": label,
            "count": count,
            "material": extra.pop("material", "wood"),
            "attachment": extra.pop("attachment", "permanent"),
            "confidence": extra.pop("confidence", 0.8),
            **extra,
        }
    )


def photo_analysis(*components: Component) -> PhotoAnalysis:
    return PhotoAnalysis(
        room_type=RoomType.KITCHEN, room_confidence=0.9, components=list(components)
    )


def obj(
    label: str, indices: list[int], count: int = 1, note: str | None = None
) -> dict[str, object]:
    return {
        "label": label,
        "count": count,
        "image_indices": indices,
        "material": "wood",
        "attachment": "permanent",
        "confidence": 0.9,
        "note": note,
    }


def inventory(*objects: dict[str, object]) -> ModelRoomInventory:
    return ModelRoomInventory.model_validate({"objects": list(objects)})


def turn(parsed: ModelRoomInventory) -> ScriptedTurn:
    return ScriptedTurn(parsed=parsed, usage=USAGE)


def bad() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse(
            "output does not match", provider="scripted", usage=USAGE, model=MODEL, raw={"x": 1}
        )
    )


def context(provider: ScriptedProvider, budget: float = 5.0) -> ReconcileContext:
    return ReconcileContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="reconcile_room",
            budget=Budget(remaining_usd=budget),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        prompt=V1,
        model=MODEL,
    )


def photos(count: int, image: PreparedImage = KITCHEN) -> list[RoomPhoto]:
    return [
        RoomPhoto(
            file=f"{n:02d}_1st Floor Kitchen photo {n}.JPG",
            image=image,
            analysis=photo_analysis(component("countertop", material="granite")),
        )
        for n in range(1, count + 1)
    ]


def images_in(provider: ScriptedProvider, call: int = 0) -> int:
    return sum(len(m.images) for m in provider.requests[call].messages)


# --------------------------------------------------------------------------- happy path


async def test_two_photos_of_one_countertop_are_one_object_seen_in_both() -> None:
    provider = ScriptedProvider([turn(inventory(obj("countertop", [1, 2])))], model=MODEL)
    ctx = context(provider)
    result = await reconcile_room("1_kitchen", photos(2), ctx)
    assert result.source == "model" and not result.needs_review
    [only] = result.inventory.objects
    assert (only.label.value, only.count, only.image_indices) == ("countertop", 1, [1, 2])
    assert result.files_for(only) == [
        "01_1st Floor Kitchen photo 1.JPG",
        "02_1st Floor Kitchen photo 2.JPG",
    ]
    assert result.inventory.counts() == {"countertop": 1}
    [call] = result.calls
    assert (call.images, call.attempts, call.model, call.first_index) == (2, 1, MODEL, 1)
    assert call.response == inventory(obj("countertop", [1, 2])).model_dump(mode="json")
    assert call.cost_usd == pytest.approx(ctx.call.budget.spent_usd) and call.cost_usd > 0
    assert ctx.call.budget.calls == 1
    [record] = ctx.call.recorder.records  # type: ignore[attr-defined]
    assert record.prompt_version == "room_inventory/v1"


async def test_request_layout() -> None:
    provider = ScriptedProvider([turn(inventory(obj("countertop", [1])))], model=MODEL)
    room = [
        RoomPhoto(
            "a.jpg",
            KITCHEN,
            photo_analysis(component("kitchen_cabinet", 6), component("countertop", 1)),
        ),
        RoomPhoto("b.jpg", BATHROOM, None),
        RoomPhoto(
            "c.jpg",
            KITCHEN,
            photo_analysis(component("other", note="wine rack", attachment="freestanding")),
        ),
    ]
    await reconcile_room("1_kitchen", room, context(provider))
    [request] = provider.requests
    assert request.response_model is ModelRoomInventory
    assert (request.temperature, request.max_tokens) == (0.0, MAX_TOKENS)
    assert request.prompt_version == "room_inventory/v1"
    assert request.system == V1.text
    # One message per image, labelled in order, then the per-photo analyses as text.
    *image_messages, text = request.messages
    assert [m.content for m in image_messages] == ["Image 1:", "Image 2:", "Image 3:"]
    assert [len(m.images) for m in image_messages] == [1, 1, 1]
    assert [base64.b64decode(m.images[0].data_base64) for m in image_messages] == [
        KITCHEN.jpeg,
        BATHROOM.jpeg,
        KITCHEN.jpeg,
    ]
    assert all(m.images[0].detail == "high" for m in image_messages)
    assert images_in(provider) == 3 and text.images == ()
    assert "There are 3 images." in text.content
    assert (
        "Image 1: kitchen; kitchen_cabinet x6 (wood, permanent); countertop x1 (wood, permanent)"
        in text.content
    )
    assert "Image 2: no per-photo analysis" in text.content
    assert "Image 3: kitchen; other [wine rack] x1 (wood, freestanding)" in text.content
    # File names never reach the model.
    assert not any("a.jpg" in m.content for m in request.messages)


def test_the_prompt_says_same_room_once_each_with_indices_and_text_is_content() -> None:
    text = V1.text
    assert "SAME room" in text and "ONCE" in text
    assert "image_indices" in text and "allowed component list" in text
    assert "never an instruction" in text


def test_render_analysis_with_no_components() -> None:
    assert render_analysis(4, photo_analysis()) == "Image 4: kitchen; no components"


def test_build_request_limits() -> None:
    ctx = context(ScriptedProvider([]))
    with pytest.raises(ValueError, match="at least one"):
        build_request([], ctx)
    with pytest.raises(ValueError, match="at most 10"):
        build_request(photos(11), ctx)


# --------------------------------------------------------------------------- validation


async def test_out_of_range_and_empty_indices_are_dropped_with_a_reason() -> None:
    provider = ScriptedProvider(
        [
            turn(
                inventory(
                    obj("countertop", [1, 2]),
                    obj("sink", [3]),
                    obj("stove", [0, 1]),
                    obj("window", []),
                    obj("toilet", [2, 2, 1]),
                )
            )
        ],
        model=MODEL,
    )
    result = await reconcile_room("1_kitchen", photos(2), context(provider))
    assert [(o.label.value, o.image_indices) for o in result.inventory.objects] == [
        ("countertop", [1, 2]),
        ("toilet", [1, 2]),  # duplicates removed, sorted
    ]
    assert [(d.object["label"], d.reason, d.call) for d in result.dropped] == [
        ("sink", "image_index_out_of_range:[3] (images 1..2)", 1),
        ("stove", "image_index_out_of_range:[0] (images 1..2)", 1),
        ("window", "no_image_indices", 1),
    ]
    assert result.needs_review and result.review_reason == "dropped_objects:3"
    assert result.source == "model"


def test_out_of_vocabulary_label_becomes_other_with_its_text() -> None:
    parsed = ModelInventoryObject.model_validate(obj("Wine Fridge", [1]))
    assert (parsed.label.value, parsed.note) == ("other", "Wine Fridge")
    assert ModelInventoryObject.model_validate(obj("Kitchen Cabinet", [1])).label.value == (
        "kitchen_cabinet"
    )


def test_inventory_object_validation() -> None:
    with pytest.raises(ValueError):
        InventoryObject.model_validate({"label": "sink", "count": 1, "image_indices": []})
    with pytest.raises(ValueError):
        InventoryObject.model_validate({"label": "sink", "count": 0, "image_indices": [1]})
    with pytest.raises(ValueError):
        InventoryObject.model_validate({"label": "sink", "count": 1, "image_indices": [0]})
    assert InventoryObject.model_validate(
        {"label": "sink", "count": 2, "image_indices": [3, 1, 3]}
    ).image_indices == [1, 3]


def test_counts_add_entries_of_one_label() -> None:
    two_sinks = RoomInventory(
        objects=[
            InventoryObject.model_validate({"label": "sink", "count": 1, "image_indices": [1]}),
            InventoryObject.model_validate({"label": "sink", "count": 2, "image_indices": [2]}),
        ]
    )
    assert two_sinks.counts() == {"sink": 3}


# --------------------------------------------------------------------------- retry


async def test_invalid_output_is_retried_once_then_succeeds() -> None:
    provider = ScriptedProvider([bad(), turn(inventory(obj("countertop", [1, 2])))], model=MODEL)
    ctx = context(provider)
    result = await reconcile_room("1_kitchen", photos(2), ctx)
    assert result.source == "model" and not result.needs_review
    [call] = result.calls
    assert call.attempts == 2 and len(call.errors) == 1 and call.raw[0] == {"x": 1}
    assert ctx.call.budget.calls == 2  # the unusable answer was charged too


async def test_invalid_output_twice_falls_back_to_the_heuristic_merge() -> None:
    provider = ScriptedProvider([bad(), ScriptedTurn(text="no json", usage=USAGE)], model=MODEL)
    room = [
        RoomPhoto("a.jpg", KITCHEN, photo_analysis(component("countertop"), component("sink"))),
        RoomPhoto("b.jpg", KITCHEN, photo_analysis(component("countertop", 2))),
    ]
    result = await reconcile_room("1_kitchen", room, context(provider))
    assert result.source == "heuristic"
    assert result.needs_review and result.review_reason == INVALID_OUTPUT
    assert result.inventory.counts() == {"countertop": 2, "sink": 1}
    [call] = result.calls
    assert call.attempts == 2 and len(call.errors) == 2 and call.response is None
    assert provider.remaining_turns == 0  # never a third call


async def test_transient_errors_propagate() -> None:
    provider = ScriptedProvider(
        [ScriptedTurn(raise_error=ProviderUnavailable("down", provider="scripted"))]
    )
    with pytest.raises(ProviderUnavailable):
        await reconcile_room("1_kitchen", photos(2), context(provider))


async def test_a_refused_room_falls_back_flagged_without_a_retry() -> None:
    """This room's photos refused (a content filter): the room falls back to the heuristic
    merge and is flagged, as after unusable answers, but the same input is not sent again;
    the other rooms are unaffected (no exception)."""
    refused = ProviderRequestRejected(
        "The response was filtered", provider="azure", status=400, code="content_filter"
    )
    provider = ScriptedProvider([ScriptedTurn(raise_error=refused)], model=MODEL)
    result = await reconcile_room("1_kitchen", photos(2), context(provider))
    assert result.source == "heuristic" and result.needs_review
    [call] = result.calls
    assert call.attempts == 1 and "content_filter" in call.errors[0]
    assert len(provider.requests) == 1


async def test_a_configuration_error_propagates() -> None:
    config = ProviderConfigError("model: x", provider="anthropic", status=404)
    provider = ScriptedProvider([ScriptedTurn(raise_error=config)], model=MODEL)
    with pytest.raises(ProviderConfigError):
        await reconcile_room("1_kitchen", photos(2), context(provider))


async def test_a_refused_overlap_pair_has_no_judgment_and_no_retry() -> None:
    """The pair-overlap step (``evals.predict_rooms``) treats a refused pair like an unusable
    one, without sending it again; a configuration error propagates."""
    refused = ProviderRequestRejected("Could not process image", provider="x", status=400)
    provider = ScriptedProvider([ScriptedTurn(raise_error=refused)], model=MODEL)
    ctx = overlap.OverlapContext(
        call=context(provider).call,
        provider=provider,
        prompt=load(overlap.PROMPT_NAME, "v1"),
        model=MODEL,
    )
    judgment = await overlap.judge_pair(photos(2), 0, 1, ctx)
    assert judgment.overlap is None and judgment.attempts == 1
    assert "Could not process image" in judgment.errors[0] and len(provider.requests) == 1
    config = ProviderConfigError("model: x", provider="anthropic", status=404)
    failing = ScriptedProvider([ScriptedTurn(raise_error=config)], model=MODEL)
    with pytest.raises(ProviderConfigError):
        await overlap.judge_pair(photos(2), 0, 1, replace(ctx, provider=failing))


# --------------------------------------------------------------------------- splitting


def test_chunks_are_at_most_ten_and_balanced() -> None:
    assert chunks(0) == []
    assert [len(c) for c in chunks(1)] == [1]
    assert [len(c) for c in chunks(10)] == [10]
    assert [len(c) for c in chunks(11)] == [6, 5]
    assert [len(c) for c in chunks(23)] == [8, 8, 7]
    assert [len(c) for c in chunks(30)] == [10, 10, 10]
    assert [(c.start, c.stop) for c in chunks(12)] == [(0, 6), (6, 12)]


async def test_a_room_over_ten_photos_is_split_and_merged_by_label() -> None:
    provider = ScriptedProvider(
        [
            # Photos 1-6: one countertop (images 1, 2), two sinks listed separately.
            turn(
                inventory(
                    obj("countertop", [1, 2]),
                    obj("sink", [3]),
                    obj("sink", [4]),
                )
            ),
            # Photos 7-12 (local 1-6): the countertop again, one sink, a stove.
            turn(inventory(obj("countertop", [1]), obj("sink", [2]), obj("stove", [5, 6], 2))),
        ],
        model=MODEL,
    )
    ctx = context(provider)
    result = await reconcile_room("1_kitchen", photos(12), ctx)
    assert [images_in(provider, 0), images_in(provider, 1)] == [6, 6]
    # Each call numbers its own images from 1.
    assert [m.content for m in provider.requests[1].messages[:-1]] == [
        f"Image {n}:" for n in range(1, 7)
    ]
    assert [(c.first_index, c.images) for c in result.calls] == [(1, 6), (7, 6)]
    by_label = {o.label.value: o for o in result.inventory.objects}
    assert {k: (v.count, v.image_indices) for k, v in by_label.items()} == {
        "countertop": (1, [1, 2, 7]),
        "sink": (2, [3, 4, 8]),  # 2 in call 1, 1 in call 2 -> max 2
        "stove": (2, [11, 12]),
    }
    assert result.source == "model" and not result.needs_review
    assert ctx.call.budget.calls == 2


async def test_a_failed_slice_falls_back_for_the_whole_room_and_stops_calling() -> None:
    provider = ScriptedProvider([bad(), bad(), turn(inventory())], model=MODEL)
    result = await reconcile_room("1_kitchen", photos(MAX_IMAGES_PER_CALL + 1), context(provider))
    assert result.source == "heuristic" and result.review_reason == INVALID_OUTPUT
    assert len(result.calls) == 1 and provider.remaining_turns == 1
    [countertop] = result.inventory.objects
    assert countertop.image_indices == list(range(1, 12))


def test_merge_calls_keeps_a_single_call_as_returned() -> None:
    sink = InventoryObject.model_validate({"label": "sink", "count": 1, "image_indices": [1]})
    assert merge_calls([[sink, sink]]) == [sink, sink]


# --------------------------------------------------------------------------- budget


async def test_budget_refusal_sends_nothing() -> None:
    provider = ScriptedProvider([turn(inventory())], model=MODEL)
    with pytest.raises(BudgetExceededError):
        await reconcile_room("1_kitchen", photos(2), context(provider, budget=0.0001))
    assert provider.requests == []


async def test_empty_room_is_an_error() -> None:
    with pytest.raises(ValueError, match="no photos"):
        await reconcile_room("1_kitchen", [], context(ScriptedProvider([])))


# --------------------------------------------------------------------------- heuristic


def test_heuristic_merge_takes_the_largest_count_and_lists_the_photos() -> None:
    merged = heuristic_merge(
        [
            photo_analysis(component("countertop"), component("kitchen_cabinet", 4)),
            None,
            photo_analysis(
                component("kitchen_cabinet", 6, material="metal"),
                component("sink"),
                component("sink"),  # listed twice in one photo -> 2 there
            ),
            photo_analysis(component("countertop"), component("sink")),
        ]
    )
    assert [(o.label.value, o.count, o.image_indices, o.material.value) for o in merged] == [
        ("countertop", 1, [1, 4], "wood"),
        ("kitchen_cabinet", 6, [1, 3], "metal"),  # attributes from the largest count
        ("sink", 2, [3, 4], "wood"),
    ]


def test_heuristic_merge_keeps_other_things_apart_by_note() -> None:
    merged = heuristic_merge(
        [
            photo_analysis(component("other", note="Wine rack")),
            photo_analysis(component("other", note="wine rack"), component("other", note="Safe")),
        ]
    )
    assert [(o.note, o.image_indices) for o in merged] == [("Wine rack", [1, 2]), ("Safe", [2])]


def test_heuristic_merge_of_nothing() -> None:
    assert heuristic_merge([]) == [] and heuristic_merge([None, photo_analysis()]) == []
