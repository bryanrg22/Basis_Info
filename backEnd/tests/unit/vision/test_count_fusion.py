"""Per-class count fusion in the decomposed photo step (EVAL_REPORT section 15).

The base model's countables and surfaces calls plus the countables call on the fusion
model, all three concurrently; each count class from the source the versioned table names;
a second call that fails never fails the photo (base counts, flagged); with fusion off the
step is the two-call step exactly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import ModelKeyedProvider

from basis.activities.analyze_photo import _raw_json
from basis.llm import Budget, BudgetExceededError, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderConfigError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.provider import ChatRequest, ChatResponse
from basis.llm.recorder import MemoryRecorder
from basis.vision import analyze, fusion
from basis.vision.analyze import (
    FusionConfig,
    VisionContext,
    analyze_photo_decomposed,
    fuse_components,
    released_prompt,
)
from basis.vision.prep import prepare_image
from basis.vision.schemas import Component, ModelPhotoAnalysis, RoomType

KITCHEN = fixture_path("photos", "01_kitchen.jpg").read_bytes()
BASE = "gpt-6.1-sol"
SECOND = "claude-opus-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)


def comp(label: str, count: int, material: str = "unknown", **extra: Any) -> Component:
    return Component.model_validate(
        {
            "label": label,
            "count": count,
            "material": material,
            "attachment": "permanent",
            "confidence": 0.8,
            **extra,
        }
    )


def answer(*components: Component, room: str = "kitchen", conf: float = 0.9) -> ModelPhotoAnalysis:
    return ModelPhotoAnalysis(
        room_type=room,  # type: ignore[arg-type]
        room_confidence=conf,
        floor=1,
        room_ordinal=1,
        components=list(components),
        description="",
    )


BASE_COUNTABLES = answer(
    comp("window", 3, "glass"),
    comp("interior_door", 1, "wood"),
    comp("electrical_outlet", 4, "plastic"),
    comp("kitchen_cabinet", 6, "wood"),
    comp("other", 1, note="stool"),
)
SECOND_COUNTABLES = answer(
    comp("window", 5, "vinyl"),
    comp("interior_door", 2, "wood"),
    comp("exterior_door", 1, "metal"),
    comp("electrical_outlet", 9, "plastic"),
    comp("light_fixture", 2, "metal"),
    comp("kitchen_cabinet", 10, "wood"),
    comp("kitchen_cabinet", 4, "wood"),  # a repeated label: deduplicated like the base
)
SURFACES = answer(comp("tile", 1, "ceramic"), comp("wall_finish", 1, "drywall"), conf=0.8)
REJECTED = ProviderRequestRejected(
    "Could not process image", provider="anthropic", status=400, code="invalid_request_error"
)


def turn(parsed: ModelPhotoAnalysis) -> ScriptedTurn:
    return ScriptedTurn(parsed=parsed, usage=USAGE)


def bad() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse("no", provider="x", usage=USAGE, model=SECOND)
    )


def provider(second: list[ScriptedTurn], **base: list[ScriptedTurn]) -> ModelKeyedProvider:
    return ModelKeyedProvider(
        turns={
            (BASE, "photo_countables"): base.get("countables", [turn(BASE_COUNTABLES)]),
            (BASE, "photo_surfaces"): base.get("surfaces", [turn(SURFACES)]),
            (SECOND, "photo_countables"): second,
        }
    )


def context(chat: Any, *, fusion_on: bool = True, budget: float = 5.0) -> VisionContext:
    return VisionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="analyze_photo",
            budget=Budget(remaining_usd=budget),
            recorder=MemoryRecorder(),
        ),
        provider=chat,
        prompt=released_prompt(),
        model=BASE,
        max_tokens=6000,
        reasoning_effort="low",
        fusion=FusionConfig(model=SECOND, max_tokens=1200, reasoning_effort="medium")
        if fusion_on
        else None,
    )


@pytest.fixture(autouse=True)
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze, "FUSION_RETRY_DELAYS_S", (0.0, 0.0))


def counts(components: list[Component]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in components:
        if c.label.value != "other":
            out[c.label.value] = out.get(c.label.value, 0) + c.count
    return out


# --------------------------------------------------------------------------- the table


def test_the_table_is_section_15_4() -> None:
    assert fusion.PER_CLASS_SOURCE == {
        "window": fusion.SECOND,
        "door": fusion.SECOND,
        "light_fixture": fusion.SECOND,
        "kitchen_cabinet": fusion.SECOND,
        "outdoor_lighting": fusion.BASE,
        "electrical_outlet": fusion.BASE,
    }
    assert fusion.TABLE_VERSION.startswith("section15.4/")
    assert fusion.class_of("interior_door") == "door" and fusion.class_of("window") == "window"


def test_fuse_components_counts_equal_the_evaluated_rule() -> None:
    """The pipeline's component replacement gives, per class, what ``fusion.fuse`` (the
    rule evals.fuse_counts scored) gives on the two answers' counts."""
    merged = [*BASE_COUNTABLES.components, *SURFACES.components]
    components, classes, dropped = fuse_components(
        merged, BASE_COUNTABLES.components, SECOND_COUNTABLES.components, fusion.PER_CLASS_SOURCE
    )
    assert dropped == 0
    second_counts = {
        "window": 5,
        "interior_door": 2,
        "exterior_door": 1,
        "electrical_outlet": 9,
        "light_fixture": 2,
        "kitchen_cabinet": 10,
    }
    by_source = {fusion.BASE: counts(BASE_COUNTABLES.components), fusion.SECOND: second_counts}
    expected = fusion.fuse(by_source, fusion.table_rules(), fusion.BASE)
    got = counts(components)
    for count_class in fusion.PER_CLASS_SOURCE:
        assert fusion.class_count(got, count_class) == fusion.class_count(expected, count_class)
        assert classes[count_class]["count"] == fusion.class_count(expected, count_class)
    assert classes["window"] == {"source": "second", "base": 3, "second": 5, "count": 5}
    assert classes["electrical_outlet"] == {"source": "base", "base": 4, "second": 9, "count": 4}
    # The second model's door split and materials replace the base's door entry.
    by_label = {c.label.value: c for c in components}
    assert (by_label["interior_door"].count, by_label["exterior_door"].material.value) == (
        2,
        "metal",
    )
    assert by_label["window"].material.value == "vinyl"
    # Surfaces and the base's "other" are untouched, "other" last.
    assert by_label["tile"].material.value == "ceramic"
    assert components[-1].label.value == "other" and components[-1].note == "stool"


def test_a_table_without_second_classes_changes_nothing() -> None:
    merged = [*BASE_COUNTABLES.components, *SURFACES.components]
    components, _, _ = fuse_components(
        merged,
        BASE_COUNTABLES.components,
        SECOND_COUNTABLES.components,
        {c: fusion.BASE for c in fusion.PER_CLASS_SOURCE},
    )
    assert counts(components) == counts(merged)


# --------------------------------------------------------------------------- the step


async def test_three_calls_and_counts_per_class() -> None:
    chat = provider([turn(SECOND_COUNTABLES)])
    result = await analyze_photo_decomposed(KITCHEN, context(chat))
    assert len(chat.requests) == 3
    second = [r for r in chat.requests if r.model == SECOND]
    assert len(second) == 1
    assert second[0].prompt_version == "photo_countables/v3"
    assert (second[0].reasoning_effort, second[0].max_tokens) == ("medium", 1200)
    base = [r for r in chat.requests if r.model == BASE]
    assert {r.reasoning_effort for r in base} == {"low"} and {r.max_tokens for r in base} == {6000}
    got = counts(result.analysis.components)
    assert got["window"] == 5 and got["kitchen_cabinet"] == 10 and got["light_fixture"] == 2
    assert (got["interior_door"], got["exterior_door"]) == (2, 1)
    assert got["electrical_outlet"] == 4  # outlets stay the base model's
    assert result.analysis.room_type is RoomType.KITCHEN and not result.analysis.needs_review
    assert result.fusion is not None and result.fusion.status == "fused"
    assert result.fusion.count_sources() == {
        "window": SECOND,
        "door": SECOND,
        "light_fixture": SECOND,
        "kitchen_cabinet": SECOND,
    }
    assert result.fusion.classes["electrical_outlet"]["model"] == BASE
    assert result.model == f"{SECOND}+{BASE}"
    assert result.prompt_versions == ("photo_countables/v3", "photo_surfaces/v2")
    assert len(result.calls) == 3 and result.attempts == 3


async def test_the_three_calls_run_concurrently() -> None:
    started: list[str] = []
    release = asyncio.Event()

    class Gate:
        name = "gate"

        async def complete(self, request: ChatRequest) -> ChatResponse:
            started.append(request.model)
            if len(started) == 3:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            family = request.prompt_version.split("/")[0]
            parsed = (
                SECOND_COUNTABLES
                if request.model == SECOND
                else BASE_COUNTABLES
                if family == "photo_countables"
                else SURFACES
            )
            return ChatResponse(
                parsed=parsed,
                text="",
                usage=USAGE,
                model=request.model,
                provider="gate",
                latency_ms=1,
                raw={},
            )

    result = await analyze_photo_decomposed(KITCHEN, context(Gate()))
    assert sorted(started) == sorted([BASE, BASE, SECOND])
    assert result.fusion is not None and result.fusion.status == "fused"


def test_the_record_keeps_both_answers_and_the_choice() -> None:
    chat = provider([turn(SECOND_COUNTABLES)])
    result = asyncio.run(analyze_photo_decomposed(KITCHEN, context(chat)))
    payload = json.loads(_raw_json(result, "gs://b/x.json"))
    assert payload["fusion"]["status"] == "fused"
    assert payload["fusion"]["table_version"] == fusion.TABLE_VERSION
    assert payload["fusion"]["classes"]["door"]["source"] == "second"
    assert payload["fusion"]["second_answer"]["components"][0]["label"] == "window"
    assert [c["model"] for c in payload["calls"]] == [BASE, BASE, SECOND]
    assert len(payload["responses"]) == 3


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        (
            [ScriptedTurn(raise_error=ProviderUnavailable("down", provider="x", status=503))] * 3,
            "provider_unavailable",
        ),
        (
            [ScriptedTurn(raise_error=ProviderRateLimited("slow", provider="x", status=429))] * 3,
            "provider_unavailable",
        ),
        ([bad(), bad()], "invalid_model_output"),
        # The second model refused this photo: the base calls analyzed it, so the base
        # counts stand (flagged); the next photo may well be accepted.
        ([ScriptedTurn(raise_error=REJECTED)], "ProviderRequestRejected"),
        ([ScriptedTurn(raise_error=BudgetExceededError("spent"))], "BudgetExceededError"),
    ],
)
async def test_a_failed_second_call_keeps_the_base_counts_and_flags(
    second: list[ScriptedTurn], reason: str
) -> None:
    chat = provider(list(second))
    result = await analyze_photo_decomposed(KITCHEN, context(chat))
    got = counts(result.analysis.components)
    assert got["window"] == 3 and got["interior_door"] == 1 and got["kitchen_cabinet"] == 6
    assert "exterior_door" not in got and "light_fixture" not in got
    assert result.analysis.needs_review
    assert result.analysis.review_reason == f"count_fusion_fallback:{reason}"
    assert result.fusion is not None and result.fusion.status == "fallback"
    assert result.fusion.reason == reason and result.fusion.count_sources() == {}
    assert all(v["second"] is None for v in result.fusion.classes.values())
    assert result.errors  # the failures are recorded


async def test_an_account_error_on_the_second_model_stops_the_photo() -> None:
    """Unlike every other failure of the second call, an account error is not a fusion
    fallback: the photo (and the run or the stage) stops, so the outage is not hidden as
    ``count_fusion_fallback`` on every photo."""
    credit = ProviderAccountError("credit balance is too low", provider="anthropic", status=400)
    chat = provider([ScriptedTurn(raise_error=credit), turn(SECOND_COUNTABLES)])
    with pytest.raises(ProviderAccountError) as caught:
        await analyze_photo_decomposed(KITCHEN, context(chat))
    assert caught.value is credit


@pytest.mark.parametrize(
    "error",
    [
        # What the pool raises when no configured provider serves the fusion model (the
        # Azure-only worker): before, every photo was flagged
        # count_fusion_fallback:ProviderNotConfigured and the study went on.
        ProviderNotConfigured(
            f"no configured provider serves model {SECOND!r} (configured: azure)"
        ),
        ProviderConfigError(
            "model: claude-opus-5-5", provider="anthropic", status=404, code="not_found_error"
        ),
    ],
)
async def test_a_configuration_error_on_the_second_model_stops_the_photo(
    error: ProviderConfigError,
) -> None:
    chat = provider([ScriptedTurn(raise_error=error), turn(SECOND_COUNTABLES)])
    with pytest.raises(ProviderConfigError) as caught:
        await analyze_photo_decomposed(KITCHEN, context(chat))
    assert caught.value is error
    second = [r for r in chat.requests if r.model == SECOND]
    assert len(second) == 1  # not retried as a transient error


async def test_a_transient_error_is_retried_then_fused() -> None:
    chat = provider(
        [
            ScriptedTurn(raise_error=ProviderUnavailable("blip", provider="x", status=503)),
            turn(SECOND_COUNTABLES),
        ]
    )
    result = await analyze_photo_decomposed(KITCHEN, context(chat))
    assert result.fusion is not None and result.fusion.status == "fused"
    second_call = result.calls[2]
    assert second_call.attempts == 2 and "ProviderUnavailable" in second_call.errors[0]
    assert not result.analysis.needs_review


async def test_a_second_call_past_the_deadline_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze, "FUSION_DEADLINE_S", 0.05)
    keyed = provider([])

    class Slow:
        name = "slow"

        async def complete(self, request: ChatRequest) -> ChatResponse:
            if request.model == SECOND:
                await asyncio.sleep(5)
            return await keyed.complete(request)

    result = await analyze_photo_decomposed(KITCHEN, context(Slow()))
    assert result.fusion is not None and result.fusion.reason == "timeout"
    assert result.analysis.review_reason == "count_fusion_fallback:timeout"


async def test_an_existing_review_reason_is_kept() -> None:
    low = answer(*SURFACES.components, conf=0.2)
    chat = provider([bad(), bad()], surfaces=[turn(low)])
    result = await analyze_photo_decomposed(KITCHEN, context(chat))
    assert result.analysis.review_reason == (
        "low_room_confidence:0.20; count_fusion_fallback:invalid_model_output"
    )


async def test_an_unusable_base_answer_is_not_fused() -> None:
    chat = provider([turn(SECOND_COUNTABLES)], surfaces=[bad(), bad()])
    result = await analyze_photo_decomposed(KITCHEN, context(chat))
    assert result.analysis.room_type is RoomType.AMBIGUOUS
    assert result.analysis.review_reason == "invalid_model_output:photo_surfaces/v2"
    assert result.fusion is not None and result.fusion.status == "not_applied"


async def test_a_base_provider_error_cancels_the_second_call_and_propagates() -> None:
    cancelled = asyncio.Event()

    class Mixed:
        name = "mixed"

        async def complete(self, request: ChatRequest) -> ChatResponse:
            if request.model == SECOND:
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            raise ProviderUnavailable("base down", provider="x", status=503)

    with pytest.raises(ProviderUnavailable):
        await analyze_photo_decomposed(KITCHEN, context(Mixed()))
    assert cancelled.is_set()


async def test_fusion_off_is_the_two_call_step() -> None:
    """The previous behaviour: VISION_COUNT_FUSION=off sends two calls, base counts."""
    chat = provider([])
    result = await analyze_photo_decomposed(KITCHEN, context(chat, fusion_on=False))
    assert [r.model for r in chat.requests] == [BASE, BASE]
    assert result.fusion is None and len(result.calls) == 2
    assert counts(result.analysis.components)["window"] == 3
    assert "fusion" not in json.loads(_raw_json(result, "gs://b/x.json"))


def test_the_image_is_prepared_once_for_all_three_calls() -> None:
    chat = provider([turn(SECOND_COUNTABLES)])
    asyncio.run(analyze_photo_decomposed(KITCHEN, context(chat)))
    images = {r.messages[0].images[0].data_base64 for r in chat.requests}
    assert images == {prepare_image(KITCHEN).base64}
