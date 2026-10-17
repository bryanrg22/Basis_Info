"""``analyze_photo`` with a scripted provider: request, retry, review, budget, room keys;
the decomposed two-call step: both prompts, the merge, independent retries, no half result.

The database side (room reuse within a revision, components, the raw analysis in
storage) is covered by ``tests/unit/activities/test_analyze_photo.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import PHOTO_MODEL, FixtureVisionProvider, PromptKeyedProvider

from basis.activities.analyze_photo import _raw_json
from basis.llm import Budget, BudgetExceededError, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderAccountError, ProviderBadResponse, ProviderUnavailable
from basis.llm.prompts import PromptError
from basis.llm.provider import ChatRequest, ChatResponse
from basis.llm.recorder import MemoryRecorder
from basis.vision.analyze import (
    COUNTABLES_PROMPT_VERSION,
    PHOTO_MODES,
    ROOM_REVIEW_THRESHOLD,
    SURFACES_PROMPT_VERSION,
    VisionContext,
    analyze_photo,
    analyze_photo_decomposed,
    analyze_photo_detailed,
    analyze_photo_in_mode,
    decomposed_prompts,
    prompts_for_mode,
    released_prompt,
    review,
)
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.rooms import UNASSIGNED, room_key
from basis.vision.schemas import Component, ModelPhotoAnalysis, PhotoAnalysis, RoomType

KITCHEN = fixture_path("photos", "01_kitchen.jpg").read_bytes()
BEDROOM = fixture_path("photos", "03_bedroom.jpg").read_bytes()
USAGE = Usage(input_tokens=1000, output_tokens=200)
COUNTABLES = f"photo_countables/{COUNTABLES_PROMPT_VERSION}"
SURFACES = f"photo_surfaces/{SURFACES_PROMPT_VERSION}"


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


def context(
    provider: Any, budget: float = 1.0, heartbeats: list[str] | None = None
) -> VisionContext:
    return VisionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="analyze_photo",
            budget=Budget(remaining_usd=budget),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        prompt=released_prompt(),
        model="gpt-4o-mini",
        max_tokens=1200,
        heartbeat=heartbeats.append if heartbeats is not None else (lambda _: None),
    )


def records(ctx: VisionContext) -> list[Any]:
    return ctx.call.recorder.records  # type: ignore[attr-defined]


async def test_one_structured_call_with_the_prepared_image() -> None:
    provider = ScriptedProvider([ScriptedTurn(parsed=kitchen(), usage=USAGE)], model="gpt-4o-mini")
    ctx = context(provider)
    result = await analyze_photo_detailed(KITCHEN, ctx)
    [request] = provider.requests
    assert request.system == released_prompt().text
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


def no_credit() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderAccountError(
            "anthropic rejected the account (HTTP 400): credit balance is too low",
            provider="anthropic",
            status=400,
        )
    )


@pytest.mark.parametrize("mode", PHOTO_MODES)
async def test_an_account_error_stops_the_photo_and_is_not_an_unusable_answer(mode: str) -> None:
    # EVAL_REPORT 26.12: no credit was recorded as invalid output, retried, and flagged.
    provider = ScriptedProvider([no_credit()], model="gpt-4o-mini")
    with pytest.raises(ProviderAccountError, match="credit balance"):
        await analyze_photo_in_mode(KITCHEN, context(provider), mode)
    # One call per prompt of the mode, none of them retried.
    assert len(provider.requests) == len(prompts_for_mode(mode))


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
    text = released_prompt().text
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


# --------------------------------------------------------------------------- modes


def test_the_modes_and_their_prompts() -> None:
    assert PHOTO_MODES == ("single", "decomposed")
    assert [p.version_id for p in prompts_for_mode("single")] == ["photo_analysis/v1"]
    assert [p.version_id for p in prompts_for_mode("decomposed")] == [COUNTABLES, SURFACES]
    assert prompts_for_mode("decomposed") == decomposed_prompts()
    with pytest.raises(ValueError, match="unknown vision photo mode"):
        prompts_for_mode("both")


async def test_an_unknown_mode_is_refused_before_any_call() -> None:
    provider = ScriptedProvider([])
    with pytest.raises(ValueError, match="unknown vision photo mode"):
        await analyze_photo_in_mode(KITCHEN, context(provider), "triple")
    assert provider.requests == []


def test_an_unreleased_decomposed_prompt_fails_to_load(monkeypatch: pytest.MonkeyPatch) -> None:
    # What the worker's composition root relies on: a bad pin fails at startup.
    monkeypatch.setattr("basis.vision.analyze.COUNTABLES_PROMPT_VERSION", "v99")
    with pytest.raises(PromptError, match="photo_countables/v99"):
        prompts_for_mode("decomposed")


def test_the_decomposed_prompts_each_forbid_the_other_job() -> None:
    countables, surfaces = decomposed_prompts()
    assert "Ignore floors, walls, ceilings" in countables.text
    assert "Ignore countable objects" in surfaces.text
    for prompt in (countables, surfaces):
        assert "never an instruction" in prompt.text and "ambiguous" in prompt.text


# --------------------------------------------------------------------------- single mode


async def test_single_mode_is_unchanged_by_the_two_call_step() -> None:
    """The one-call result, request and stored record are what they were before the
    decomposed step existed (same keys, same values; no mode or per-call detail)."""
    provider = ScriptedProvider(
        [bad({"raw": 1}), ScriptedTurn(parsed=kitchen(), usage=USAGE)], model="gpt-4o-mini"
    )
    ctx = context(provider)
    result = await analyze_photo_in_mode(KITCHEN, ctx, "single")
    prepared = prepare_image(KITCHEN)
    expected_analysis = kitchen().to_analysis()
    assert result.analysis == expected_analysis
    assert (result.attempts, result.provider, result.model) == (2, "scripted", "gpt-4o-mini")
    assert result.raw == [{"raw": 1}, {}]
    assert result.errors == ["attempt 1: output does not match"]  # no call prefix
    assert (result.mode, result.prompt_versions) == ("single", ("photo_analysis/v1",))
    assert [c.prompt_version for c in result.calls] == ["photo_analysis/v1"]
    assert [r.system for r in provider.requests] == [released_prompt().text] * 2
    stored = json.loads(_raw_json(result, "gs://b/analysis.json"))
    assert stored == {
        "uri": "gs://b/analysis.json",
        "analysis": expected_analysis.model_dump(mode="json"),
        "attempts": 2,
        "errors": ["attempt 1: output does not match"],
        "provider": "scripted",
        "model": "gpt-4o-mini",
        "image": {
            "sha256": prepared.sha256,
            "width": prepared.width,
            "height": prepared.height,
            "original_width": prepared.original_width,
            "original_height": prepared.original_height,
            "original_format": prepared.original_format,
        },
        "responses": [{"raw": 1}, {}],
    }


async def test_single_mode_heartbeats_are_unchanged() -> None:
    beats: list[str] = []
    provider = ScriptedProvider([ScriptedTurn(parsed=kitchen(), usage=USAGE)])
    await analyze_photo_detailed(KITCHEN, context(provider, heartbeats=beats))
    assert beats == ["vision attempt 1"]


# --------------------------------------------------------------------------- decomposed


def countables_answer(components: list[Component], room: str = "kitchen") -> ModelPhotoAnalysis:
    return ModelPhotoAnalysis(
        room_type=room,  # type: ignore[arg-type]
        room_confidence=0.95,
        floor=1,
        room_ordinal=1,
        components=components,
        description="Six cabinets.",
    )


def surfaces_answer(
    components: list[Component], room: str = "dining_room", confidence: float = 0.7
) -> ModelPhotoAnalysis:
    return ModelPhotoAnalysis(
        room_type=room,  # type: ignore[arg-type]
        room_confidence=confidence,
        floor=2,
        room_ordinal=2,
        components=components,
        description="Ceramic tile floor.",
    )


def part(label: str, count: int = 1, material: str = "unknown", conf: float = 0.8) -> Component:
    return Component(
        label=label,  # type: ignore[arg-type]
        count=count,
        material=material,  # type: ignore[arg-type]
        attachment="permanent",  # type: ignore[arg-type]
        confidence=conf,
    )


def keyed(countables: list[ScriptedTurn], surfaces: list[ScriptedTurn]) -> PromptKeyedProvider:
    return PromptKeyedProvider({"photo_countables": countables, "photo_surfaces": surfaces})


def ok(answer: ModelPhotoAnalysis) -> ScriptedTurn:
    return ScriptedTurn(parsed=answer, usage=USAGE)


async def test_decomposed_sends_both_prompts_on_the_prepared_image_and_merges() -> None:
    # The bedroom fixture has a surface (carpet) and a countable (other): the fixture
    # provider answers each narrow prompt with its own part, so the merge is real.
    provider = FixtureVisionProvider()
    beats: list[str] = []
    ctx = context(provider, heartbeats=beats)
    result = await analyze_photo_in_mode(BEDROOM, ctx, "decomposed")
    assert len(provider.requests) == 2
    by_prompt: dict[str, ChatRequest] = {r.prompt_version: r for r in provider.requests}
    assert set(by_prompt) == {COUNTABLES, SURFACES}
    countables, surfaces = decomposed_prompts()
    assert by_prompt[COUNTABLES].system == countables.text
    assert by_prompt[SURFACES].system == surfaces.text
    prepared = prepare_image(BEDROOM).jpeg
    for request in provider.requests:
        [image] = request.messages[0].images
        assert base64.b64decode(image.data_base64) == prepared
        assert request.response_model is ModelPhotoAnalysis
        assert (request.model, request.max_tokens, request.temperature) == (
            "gpt-4o-mini",
            1200,
            0.0,
        )
    assert result.analysis.room_type is RoomType.BEDROOM and not result.analysis.needs_review
    assert {c.label.value for c in result.analysis.components} == {"carpet", "other"}
    assert (result.mode, result.prompt_versions) == ("decomposed", (COUNTABLES, SURFACES))
    assert result.attempts == 2 and (result.provider, result.model) == ("fixture", PHOTO_MODEL)
    assert [c.prompt_version for c in result.calls] == [COUNTABLES, SURFACES]
    assert all(c.parsed is not None and c.attempts == 1 for c in result.calls)
    assert len(result.raw) == 2 and result.errors == []
    assert ctx.call.budget.calls == 2
    assert sorted(r.prompt_version for r in records(ctx)) == sorted([COUNTABLES, SURFACES])
    assert set(beats) == {
        "vision photo_countables attempt 1",
        "vision photo_surfaces attempt 1",
    }


async def test_decomposed_matches_the_single_call_on_the_fixture_labels() -> None:
    # The fixture's split by prompt family puts every label back together: what the
    # single call lists for a photo, the two calls list between them.
    for image in (KITCHEN, BEDROOM):
        single = await analyze_photo_in_mode(image, context(FixtureVisionProvider()), "single")
        decomposed = await analyze_photo_in_mode(
            image, context(FixtureVisionProvider()), "decomposed"
        )
        assert {(c.label, c.count) for c in decomposed.analysis.components} == {
            (c.label, c.count) for c in single.analysis.components
        }
        assert decomposed.analysis.room_type is single.analysis.room_type


async def test_decomposed_takes_the_room_from_the_surfaces_call_and_merges_components() -> None:
    provider = keyed(
        countables=[
            ok(
                countables_answer(
                    [part("kitchen_cabinet", 6, "wood", 0.9), part("countertop", 2, "granite", 0.5)]
                )
            )
        ],
        surfaces=[
            ok(surfaces_answer([part("tile", 1, "ceramic"), part("countertop", 1, "quartz")]))
        ],
    )
    result = await analyze_photo_decomposed(KITCHEN, context(provider))
    analysis = result.analysis
    assert (analysis.room_type, analysis.room_confidence) == (RoomType.DINING_ROOM, 0.7)
    assert (analysis.floor, analysis.room_ordinal) == (2, 2)
    assert [c.label.value for c in analysis.components] == ["kitchen_cabinet", "countertop", "tile"]
    countertop = analysis.components[1]
    assert (countertop.count, countertop.confidence, countertop.material.value) == (
        2,
        0.8,
        "granite",
    )
    assert analysis.description == "Six cabinets. Ceramic tile floor."
    assert not analysis.needs_review
    assert (result.provider, result.model) == ("keyed", "gpt-4o-mini")
    assert result.raw == [{"keyed": "photo_countables"}, {"keyed": "photo_surfaces"}]


async def test_decomposed_review_uses_the_surfaces_room_confidence() -> None:
    low = surfaces_answer([], confidence=ROOM_REVIEW_THRESHOLD - 0.01)
    provider = keyed(countables=[ok(countables_answer([]))], surfaces=[ok(low)])
    result = await analyze_photo_decomposed(KITCHEN, context(provider))
    assert result.analysis.needs_review
    assert result.analysis.review_reason == "low_room_confidence:0.49"
    ambiguous = keyed(
        countables=[ok(countables_answer([part("window", 3)]))],
        surfaces=[ok(surfaces_answer([], room="ambiguous", confidence=0.9))],
    )
    result = await analyze_photo_decomposed(KITCHEN, context(ambiguous))
    assert result.analysis.room_type is RoomType.AMBIGUOUS
    assert result.analysis.review_reason == "ambiguous"
    assert [c.label.value for c in result.analysis.components] == ["window"]  # kept for review


async def test_decomposed_calls_retry_independently() -> None:
    provider = keyed(
        countables=[bad({"c": 1}), ok(countables_answer([part("window", 2)]))],
        surfaces=[ok(surfaces_answer([part("carpet")]))],
    )
    ctx = context(provider)
    result = await analyze_photo_decomposed(KITCHEN, ctx)
    assert not result.analysis.needs_review
    assert {c.label.value for c in result.analysis.components} == {"window", "carpet"}
    assert result.attempts == 3 and [c.attempts for c in result.calls] == [2, 1]
    assert result.errors == ["photo_countables attempt 1: output does not match"]
    assert result.raw == [{"c": 1}, {"keyed": "photo_countables"}, {"keyed": "photo_surfaces"}]
    assert ctx.call.budget.calls == 3  # the bad attempt was charged too


async def test_decomposed_one_call_unusable_twice_is_ambiguous_not_a_half_result() -> None:
    provider = keyed(
        countables=[bad(), ScriptedTurn(text="a kitchen", usage=USAGE)],
        surfaces=[
            ok(surfaces_answer([part("tile", 1, "ceramic")], room="kitchen", confidence=0.9))
        ],
    )
    ctx = context(provider)
    result = await analyze_photo_decomposed(KITCHEN, ctx)
    assert result.analysis.room_type is RoomType.AMBIGUOUS
    assert result.analysis.components == []  # the usable surfaces answer is not shipped alone
    assert result.analysis.needs_review
    assert result.analysis.review_reason == f"invalid_model_output:{COUNTABLES}"
    assert result.attempts == 3 and len(result.errors) == 2
    assert all(e.startswith("photo_countables attempt") for e in result.errors)
    assert result.calls[0].parsed is None and result.calls[1].parsed is not None
    assert all(not s for s in provider.turns.values())  # never a third call on either


async def test_decomposed_both_calls_unusable_names_both() -> None:
    provider = keyed(countables=[bad(), bad()], surfaces=[bad(), bad()])
    result = await analyze_photo_decomposed(KITCHEN, context(provider))
    assert result.analysis.review_reason == f"invalid_model_output:{COUNTABLES},{SURFACES}"
    assert result.attempts == 4 and len(result.errors) == 4
    assert (result.provider, result.model) == (None, None)


class HangingCountables:
    """The surfaces call fails at once; the countables call hangs until cancelled."""

    name = "hanging"

    def __init__(self) -> None:
        self.cancelled = False
        self.started = asyncio.Event()

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if request.prompt_version.startswith("photo_surfaces/"):
            raise ProviderUnavailable("503", provider="azure", status=503)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


async def test_decomposed_transient_error_on_one_call_fails_the_photo_and_cancels_the_other() -> (
    None
):
    provider = HangingCountables()
    ctx = context(provider)
    with pytest.raises(ProviderUnavailable):  # the error itself, not an exception group
        await analyze_photo_decomposed(KITCHEN, ctx)
    assert provider.started.is_set() and provider.cancelled
    assert records(ctx) == []  # nothing reached a model, nothing recorded


async def test_decomposed_budget_must_cover_both_calls() -> None:
    provider = keyed(countables=[ok(countables_answer([]))], surfaces=[ok(surfaces_answer([]))])
    # $0.0010 covers one reservation (about $0.0009) and one charge ($0.00027) but not
    # two: whichever check fails first, the photo fails as a whole (no analysis, no half
    # result) and every call that was made is recorded.
    ctx = context(provider, budget=0.0010)
    with pytest.raises(BudgetExceededError):
        await analyze_photo_decomposed(KITCHEN, ctx)
    assert len(records(ctx)) == ctx.call.budget.calls <= 2


async def test_decomposed_budget_too_small_is_refused_before_any_call() -> None:
    provider = keyed(countables=[], surfaces=[])
    ctx = context(provider, budget=0.0001)
    with pytest.raises(BudgetExceededError):
        await analyze_photo_decomposed(KITCHEN, ctx)
    assert provider.requests == [] and records(ctx) == []


async def test_decomposed_unusable_image_never_reaches_the_model() -> None:
    provider = keyed(countables=[], surfaces=[])
    with pytest.raises(ImageRejected):
        await analyze_photo_decomposed(
            fixture_path("malformed", "tiny.jpg").read_bytes(), context(provider)
        )
    assert provider.requests == []


async def test_decomposed_record_names_the_mode_and_each_call() -> None:
    provider = keyed(
        countables=[bad({"c": 1}), ok(countables_answer([part("window", 2)]))],
        surfaces=[ok(surfaces_answer([part("carpet")]))],
    )
    result = await analyze_photo_decomposed(KITCHEN, context(provider))
    stored = json.loads(_raw_json(result, "gs://b/analysis.json"))
    assert stored["mode"] == "decomposed"
    assert stored["prompt_versions"] == [COUNTABLES, SURFACES]
    assert stored["calls"] == [
        {
            "prompt_version": COUNTABLES,
            "usable": True,
            "attempts": 2,
            "provider": "keyed",
            "model": "gpt-4o-mini",
            "errors": ["photo_countables attempt 1: output does not match"],
            "responses": 2,
        },
        {
            "prompt_version": SURFACES,
            "usable": True,
            "attempts": 1,
            "provider": "keyed",
            "model": "gpt-4o-mini",
            "errors": [],
            "responses": 1,
        },
    ]
    assert len(stored["responses"]) == 3 and stored["attempts"] == 3
