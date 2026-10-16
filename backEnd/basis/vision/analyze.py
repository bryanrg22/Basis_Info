"""``analyze_photo``: the structured vision call(s) for one photo (S3_SPEC sections 2 and 4).

Migrated from ``agentic/agents/vision_agent.py`` (``analyze_image_with_provider``). The
legacy path asked for free-form JSON, parsed it with four fallback strategies, retried
twice when the room came back ``unknown``, and on any exception returned
``room_type="unknown"`` with confidence 0 as if that were an answer. Here:

* The image is prepared first (:mod:`basis.vision.prep`); a rejected image raises
  :class:`~basis.vision.prep.ImageRejected` (the activity makes it non-retryable).
* Each call uses a versioned prompt and the :class:`ModelPhotoAnalysis` JSON schema in
  strict mode, so the room type and labels come from the controlled vocabularies.
* An unusable answer (invalid JSON for the schema, refusal, truncation) is retried once;
  a second unusable answer returns an ``ambiguous`` analysis with ``needs_review`` and the
  reason, not a guess.
* Budget: reserved before and charged after every attempt through
  :func:`basis.llm.call.call_model`; :class:`BudgetExceededError` propagates (the
  activity turns it into a non-retryable error). Transient provider errors propagate too
  (the activity's Temporal retry handles them after the pool's fallback).
* The analysis is ``needs_review`` when the room is ``ambiguous`` or its confidence is
  below :data:`ROOM_REVIEW_THRESHOLD`.

Two modes, chosen by the activity from ``Settings.vision_photo_mode``
(:func:`analyze_photo_in_mode`):

* ``single`` (the default): one call with ``photo_analysis/v1`` that asks for everything
  (:func:`analyze_photo_detailed`).
* ``decomposed`` (EVAL_REPORT section 11): two calls on the same prepared image, run
  concurrently through the provider pool, each with one narrow job:
  ``photo_countables/v2`` finds and counts the countable objects and ``photo_surfaces/v1``
  names the surfaces and their materials (:func:`analyze_photo_decomposed`). The answers
  are merged by :mod:`basis.vision.merge`, the same rule the harness scored: components
  unioned by label with the largest count, the countables call's material and attachment
  for a label both list, every ``other`` kept; room type, confidence, floor and ordinal
  from the surfaces call. Each call has its own retry on unusable output. If either call
  stays unusable the photo is ``ambiguous``/``needs_review`` naming the call (never a half
  result), and a transient provider error or an exhausted budget on either call cancels
  the other and propagates, so the activity fails or retries the photo as a whole.

Every attempt's raw provider response is returned in :class:`PhotoResult` for storage;
``PhotoResult.calls`` keeps them per prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal, get_args

from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.errors import ProviderBadResponse
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.vision.merge import merge_analyses
from basis.vision.prep import PreparedImage, prepare_image
from basis.vision.schemas import ModelPhotoAnalysis, PhotoAnalysis, RoomType, ambiguous_analysis

PhotoMode = Literal["single", "decomposed"]
PHOTO_MODES: Final[tuple[str, ...]] = get_args(PhotoMode)

PROMPT_NAME: Final = "photo_analysis"
# The released version the pipeline runs. A newer version is released for evaluation first
# (evals.predict_photos --prompt vN) and promoted here only when it wins (EVAL_REPORT).
PROMPT_VERSION: Final = "v1"
# The decomposed step's two prompts, pinned the same way (EVAL_REPORT section 11: v2 of the
# countables prompt won on three repeats; a v3 released for evaluation must win first).
COUNTABLES_PROMPT_NAME: Final = "photo_countables"
COUNTABLES_PROMPT_VERSION: Final = "v2"
SURFACES_PROMPT_NAME: Final = "photo_surfaces"
SURFACES_PROMPT_VERSION: Final = "v1"
MAX_ATTEMPTS: Final = 2
# Below this self-reported room confidence the photo goes to the engineer (eval-tuned later).
ROOM_REVIEW_THRESHOLD: Final = 0.5
USER_TEXT: Final = (
    "Analyze this property photo. Identify the room type and every visible building "
    "component, using only what is visible."
)
INVALID_OUTPUT: Final = "invalid_model_output"


def released_prompt() -> Prompt:
    """The photo prompt the single-call step runs (``PROMPT_NAME``/``PROMPT_VERSION``)."""
    return prompts.load(PROMPT_NAME, PROMPT_VERSION)


def decomposed_prompts() -> tuple[Prompt, Prompt]:
    """The ``(countables, surfaces)`` prompts the decomposed step runs."""
    return (
        prompts.load(COUNTABLES_PROMPT_NAME, COUNTABLES_PROMPT_VERSION),
        prompts.load(SURFACES_PROMPT_NAME, SURFACES_PROMPT_VERSION),
    )


def prompts_for_mode(mode: str) -> tuple[Prompt, ...]:
    """Every prompt ``mode`` sends, loaded and verified against the manifest.

    The worker calls this at startup so a mode whose prompt is missing from the manifest
    fails there, not on the first photo.
    """
    if mode == "single":
        return (released_prompt(),)
    if mode == "decomposed":
        return decomposed_prompts()
    raise ValueError(f"unknown vision photo mode {mode!r}; expected one of {PHOTO_MODES}")


def _no_heartbeat(_: str) -> None:
    return None


@dataclass
class VisionContext:
    call: CallContext
    provider: ChatProvider
    # The prompt of the single-call step. The decomposed step sends its own two prompts
    # (``decomposed_prompts``) with everything else from this context.
    prompt: Prompt
    model: str
    max_tokens: int = 1200
    heartbeat: Callable[[str], None] = field(default=_no_heartbeat)
    # Reasoning models spend ``max_tokens`` on reasoning before the answer: give them a budget
    # that covers both and an effort level (``None`` = the provider's default).
    reasoning_effort: str | None = None


@dataclass
class CallOutcome:
    """One prompt's call(s) on the photo: the usable answer, or why there is none."""

    prompt_version: str
    parsed: ModelPhotoAnalysis | None
    attempts: int
    provider: str | None = None
    model: str | None = None
    raw: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PhotoResult:
    analysis: PhotoAnalysis
    image: PreparedImage
    # Across every call: attempts add up, raw responses and errors are concatenated in
    # prompt order (countables, then surfaces), provider and model are the shared value or
    # the distinct ones joined with "+".
    attempts: int
    provider: str | None = None
    model: str | None = None
    raw: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mode: PhotoMode = "single"
    prompt_versions: tuple[str, ...] = ()
    calls: tuple[CallOutcome, ...] = ()


def build_request(image: PreparedImage, ctx: VisionContext) -> ChatRequest:
    return ChatRequest(
        model=ctx.model,
        system=ctx.prompt.text,
        messages=(
            Message(
                role="user",
                content=USER_TEXT,
                images=(
                    ImagePart(media_type="image/jpeg", data_base64=image.base64, detail="high"),
                ),
            ),
        ),
        response_model=ModelPhotoAnalysis,
        max_tokens=ctx.max_tokens,
        temperature=0.0,
        reasoning_effort=ctx.reasoning_effort,
        prompt_version=ctx.prompt.version_id,
        study_id=str(ctx.call.study_id),
        activity=ctx.call.activity,
    )


def review(analysis: PhotoAnalysis) -> PhotoAnalysis:
    """Flag an analysis the engineer must look at (ambiguous or low room confidence)."""
    if analysis.needs_review:
        return analysis
    if analysis.room_type is RoomType.AMBIGUOUS:
        return analysis.model_copy(update={"needs_review": True, "review_reason": "ambiguous"})
    if analysis.room_confidence < ROOM_REVIEW_THRESHOLD:
        return analysis.model_copy(
            update={
                "needs_review": True,
                "review_reason": f"low_room_confidence:{analysis.room_confidence:.2f}",
            }
        )
    return analysis


async def call_until_usable(
    image: PreparedImage, ctx: VisionContext, label: str = ""
) -> CallOutcome:
    """``ctx.prompt`` on the prepared image: up to :data:`MAX_ATTEMPTS` calls, stopping at
    the first usable structured answer. ``label`` prefixes heartbeats and error messages
    so the decomposed step's two calls can be told apart."""
    request = build_request(image, ctx)
    raws: list[dict[str, Any]] = []
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ctx.heartbeat(f"vision {label}attempt {attempt}")
        try:
            response = await call_model(ctx.provider, request, ctx.call)
        except ProviderBadResponse as exc:
            errors.append(f"{label}attempt {attempt}: {exc}")
            if exc.raw is not None:
                raws.append(exc.raw)
            continue
        raws.append(response.raw)
        parsed = response.parsed
        if not isinstance(parsed, ModelPhotoAnalysis):
            errors.append(f"{label}attempt {attempt}: no structured analysis in the response")
            continue
        return CallOutcome(
            prompt_version=ctx.prompt.version_id,
            parsed=parsed,
            attempts=attempt,
            provider=response.provider,
            model=response.model,
            raw=raws,
            errors=errors,
        )
    return CallOutcome(
        prompt_version=ctx.prompt.version_id,
        parsed=None,
        attempts=MAX_ATTEMPTS,
        raw=raws,
        errors=errors,
    )


async def analyze_photo_detailed(image_bytes: bytes, ctx: VisionContext) -> PhotoResult:
    """The single-call step: ``ctx.prompt`` once (retried once on unusable output)."""
    image = prepare_image(image_bytes)
    call = await call_until_usable(image, ctx)
    if call.parsed is None:
        analysis = ambiguous_analysis(INVALID_OUTPUT)
    else:
        analysis = review(call.parsed.to_analysis())
    return PhotoResult(
        analysis=analysis,
        image=image,
        attempts=call.attempts,
        provider=call.provider,
        model=call.model,
        raw=call.raw,
        errors=call.errors,
        mode="single",
        prompt_versions=(call.prompt_version,),
        calls=(call,),
    )


async def _all_or_none(*coroutines: Coroutine[Any, Any, CallOutcome]) -> list[CallOutcome]:
    """Run the calls concurrently; the first exception cancels the others and propagates
    as itself (not wrapped), so the activity's error mapping sees the real error."""
    tasks = [asyncio.ensure_future(c) for c in coroutines]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _joined(values: list[str | None]) -> str | None:
    distinct = sorted({v for v in values if v is not None})
    if not distinct:
        return None
    return "+".join(distinct)


async def analyze_photo_decomposed(
    image_bytes: bytes,
    ctx: VisionContext,
    *,
    countables: Prompt | None = None,
    surfaces: Prompt | None = None,
) -> PhotoResult:
    """The two-call step (EVAL_REPORT section 11): the countables and the surfaces prompt
    on the same prepared image, concurrently, merged by :mod:`basis.vision.merge`.

    ``countables`` and ``surfaces`` default to :func:`decomposed_prompts`; ``ctx.prompt``
    is not sent.
    """
    default_countables, default_surfaces = decomposed_prompts()
    countables = countables or default_countables
    surfaces = surfaces or default_surfaces
    image = prepare_image(image_bytes)
    calls = await _all_or_none(
        call_until_usable(image, replace(ctx, prompt=countables), label=f"{countables.name} "),
        call_until_usable(image, replace(ctx, prompt=surfaces), label=f"{surfaces.name} "),
    )
    countables_call, surfaces_call = calls
    errors = [*countables_call.errors, *surfaces_call.errors]
    unusable = [c.prompt_version for c in calls if c.parsed is None]
    if unusable:
        # One narrow answer alone is not an analysis of the photo: no half result.
        analysis = ambiguous_analysis(f"{INVALID_OUTPUT}:{','.join(unusable)}")
    else:
        assert countables_call.parsed is not None and surfaces_call.parsed is not None
        merged = merge_analyses(countables_call.parsed, surfaces_call.parsed)
        if merged.dropped:
            errors.append(f"merge: {merged.dropped} component(s) beyond the schema cap dropped")
        analysis = review(merged.analysis)
    return PhotoResult(
        analysis=analysis,
        image=image,
        attempts=sum(c.attempts for c in calls),
        provider=_joined([c.provider for c in calls]),
        model=_joined([c.model for c in calls]),
        raw=[*countables_call.raw, *surfaces_call.raw],
        errors=errors,
        mode="decomposed",
        prompt_versions=tuple(c.prompt_version for c in calls),
        calls=tuple(calls),
    )


async def analyze_photo_in_mode(image_bytes: bytes, ctx: VisionContext, mode: str) -> PhotoResult:
    """The step ``Settings.vision_photo_mode`` selects."""
    if mode == "single":
        return await analyze_photo_detailed(image_bytes, ctx)
    if mode == "decomposed":
        return await analyze_photo_decomposed(image_bytes, ctx)
    raise ValueError(f"unknown vision photo mode {mode!r}; expected one of {PHOTO_MODES}")


async def analyze_photo(image_bytes: bytes, ctx: VisionContext) -> PhotoAnalysis:
    """The binding S3 interface: the photo's analysis (see :func:`analyze_photo_detailed`)."""
    return (await analyze_photo_detailed(image_bytes, ctx)).analysis
