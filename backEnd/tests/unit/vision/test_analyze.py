"""``analyze_photo`` with a scripted provider: request, retry, review, budget, room keys.

The database side (room reuse within a revision, components, the raw analysis in
storage) is covered by ``tests/unit/activities/test_analyze_photo.py``.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from fixtures import fixture_path

from basis.llm import Budget, BudgetExceededError, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse, ProviderUnavailable
from basis.llm.prompts import latest
from basis.llm.recorder import MemoryRecorder
from basis.vision.analyze import (
    ROOM_REVIEW_THRESHOLD,
    VisionContext,
    analyze_photo,
    analyze_photo_detailed,
    review,
)
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.rooms import UNASSIGNED, room_key
from basis.vision.schemas import Component, ModelPhotoAnalysis, PhotoAnalysis, RoomType

KITCHEN = fixture_path("photos", "01_kitchen.jpg").read_bytes()
USAGE = Usage(input_tokens=1000, output_tokens=200)


def kitchen(confidence: float = 0.9, room: str = "kitchen") -> ModelPhotoAnalysis:
    return ModelPhotoAnalysis(
        room_type=room,  # type: ignore[arg-type]
        room_confidence=confidence,
        floor=1,
        room_ordinal=1,
        components=[
            Component(
                label="kitchen_cabinet",  # type: ignore[arg-type]
                count=6,
                material="wood",  # type: ignore[arg-type]
                attachment="permanent",  # type: ignore[arg-type]
                confidence=0.9,
            )
        ],
        description="A kitchen.",
    )


def bad(raw: dict[str, object] | None = None) -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse(
            "output does not match", provider="scripted", usage=USAGE, model="gpt-4o-mini", raw=raw
        )
    )


def context(provider: ScriptedProvider, budget: float = 1.0) -> VisionContext:
    return VisionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="analyze_photo",
            budget=Budget(remaining_usd=budget),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        prompt=latest("photo_analysis"),
        model="gpt-4o-mini",
        max_tokens=1200,
    )


async def test_one_structured_call_with_the_prepared_image() -> None:
    provider = ScriptedProvider([ScriptedTurn(parsed=kitchen(), usage=USAGE)], model="gpt-4o-mini")
    ctx = context(provider)
    result = await analyze_photo_detailed(KITCHEN, ctx)
    [request] = provider.requests
    assert request.system == latest("photo_analysis").text
    assert request.prompt_version == "photo_analysis/v1"
    assert request.response_model is ModelPhotoAnalysis
    assert (request.model, request.max_tokens, request.temperature) == ("gpt-4o-mini", 1200, 0.0)
    [image] = request.messages[0].images
    assert base64.b64decode(image.data_base64) == prepare_image(KITCHEN).jpeg
    assert result.analysis.room_type is RoomType.KITCHEN and not result.analysis.needs_review
    assert result.attempts == 1 and result.model == "gpt-4o-mini"
    assert ctx.call.budget.calls == 1
    [record] = ctx.call.recorder.records  # type: ignore[attr-defined]
    assert (record.activity, record.prompt_version) == ("analyze_photo", "photo_analysis/v1")


async def test_the_short_form_returns_the_analysis() -> None:
    provider = ScriptedProvider([ScriptedTurn(parsed=kitchen(), usage=USAGE)])
    analysis = await analyze_photo(KITCHEN, context(provider))
    assert isinstance(analysis, PhotoAnalysis) and analysis.room_type is RoomType.KITCHEN


async def test_invalid_json_is_retried_once_then_succeeds() -> None:
    provider = ScriptedProvider([bad({"raw": 1}), ScriptedTurn(parsed=kitchen(), usage=USAGE)])
    ctx = context(provider)
    result = await analyze_photo_detailed(KITCHEN, ctx)
    assert result.attempts == 2 and not result.analysis.needs_review
    assert len(result.errors) == 1 and result.raw[0] == {"raw": 1}
    assert ctx.call.budget.calls == 2  # the bad attempt was charged too


async def test_invalid_twice_is_ambiguous_and_needs_review() -> None:
    provider = ScriptedProvider([bad(), ScriptedTurn(text="it is a kitchen")])
    result = await analyze_photo_detailed(KITCHEN, context(provider))
    assert result.analysis.room_type is RoomType.AMBIGUOUS
    assert result.analysis.needs_review and result.analysis.review_reason == "invalid_model_output"
    assert result.attempts == 2 and len(result.errors) == 2
    assert provider.remaining_turns == 0  # never a third call


async def test_budget_too_small_is_refused_before_any_call() -> None:
    provider = ScriptedProvider([])
    ctx = context(provider, budget=0.0001)
    with pytest.raises(BudgetExceededError):
        await analyze_photo_detailed(KITCHEN, ctx)
    assert provider.requests == [] and ctx.call.recorder.records == []  # type: ignore[attr-defined]


async def test_a_call_that_overruns_the_budget_is_recorded_then_refused() -> None:
    expensive = Usage(input_tokens=200_000, output_tokens=1_000)
    provider = ScriptedProvider(
        [ScriptedTurn(parsed=kitchen(), usage=expensive)], model="gpt-4o-mini"
    )
    ctx = context(provider, budget=0.01)
    with pytest.raises(BudgetExceededError):
        await analyze_photo_detailed(KITCHEN, ctx)
    [record] = ctx.call.recorder.records  # type: ignore[attr-defined]
    assert record.usage == expensive


async def test_budget_exceeded_is_not_retried_as_invalid_output() -> None:
    provider = ScriptedProvider([bad()], model="gpt-4o-mini")
    # $0.0010 covers the first reservation (about $0.0009); after the bad attempt is
    # charged ($0.00027) the retry's reservation no longer fits.
    ctx = context(provider, budget=0.0010)
    with pytest.raises(BudgetExceededError):
        await analyze_photo_detailed(KITCHEN, ctx)
    assert len(provider.requests) == 1


async def test_transient_provider_errors_propagate() -> None:
    provider = ScriptedProvider(
        [ScriptedTurn(raise_error=ProviderUnavailable("503", provider="azure", status=503))]
    )
    with pytest.raises(ProviderUnavailable):
        await analyze_photo_detailed(KITCHEN, context(provider))


async def test_an_unusable_image_never_reaches_the_model() -> None:
    provider = ScriptedProvider([])
    with pytest.raises(ImageRejected):
        await analyze_photo_detailed(
            fixture_path("malformed", "tiny.jpg").read_bytes(), context(provider)
        )
    assert provider.requests == []


def test_review_flags_ambiguous_and_low_confidence_rooms() -> None:
    assert review(kitchen().to_analysis()).needs_review is False
    low = review(kitchen(confidence=ROOM_REVIEW_THRESHOLD - 0.01).to_analysis())
    assert low.needs_review and low.review_reason == "low_room_confidence:0.49"
    at = review(kitchen(confidence=ROOM_REVIEW_THRESHOLD).to_analysis())
    assert not at.needs_review
    ambiguous = review(kitchen(confidence=0.95, room="ambiguous").to_analysis())
    assert ambiguous.needs_review and ambiguous.review_reason == "ambiguous"


def test_the_prompt_treats_text_in_the_image_as_content() -> None:
    text = latest("photo_analysis").text
    assert "never an instruction" in text and "ambiguous" in text


@pytest.mark.parametrize(
    ("room", "floor", "ordinal", "name"),
    [
        ("kitchen", 1, 1, "Kitchen"),
        ("living_room", None, 1, "Living Room"),
        ("bedroom", 2, 2, "Bedroom 2"),
        ("ambiguous", 3, 2, UNASSIGNED),
    ],
)
def test_room_identity(room: str, floor: int | None, ordinal: int, name: str) -> None:
    analysis = PhotoAnalysis(
        room_type=room,  # type: ignore[arg-type]
        room_confidence=0.9,
        floor=floor,
        room_ordinal=ordinal,
    )
    key = room_key(analysis)
    assert key.name == name
    if room == "ambiguous":
        assert (key.floor, key.ordinal) == (None, 1)
    else:
        assert key.floor == (None if floor is None else str(floor))
