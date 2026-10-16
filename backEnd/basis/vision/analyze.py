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

Per-class count fusion (EVAL_REPORT section 15, ``VisionContext.fusion``): the decomposed
step also sends the countables prompt to a second model (Claude Opus 5.5 at medium effort by
default), concurrently with the other two calls, and each count class in
:data:`basis.vision.fusion.PER_CLASS_SOURCE` takes its components from the source the table
names (:func:`fuse_components`); labels, materials and room type stay the base model's.
The second call never fails the photo: a transient provider error is retried
(:data:`FUSION_RETRY_DELAYS_S`), and if it stays unavailable or unusable (or no provider
serves the model, or the budget cannot cover it) the photo keeps the base model's counts
and is flagged ``needs_review`` with reason ``count_fusion_fallback:<why>``. Both raw
answers and the chosen source per class are in :class:`FusionOutcome`.

Every attempt's raw provider response is returned in :class:`PhotoResult` for storage;
``PhotoResult.calls`` keeps them per prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal, get_args

from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.errors import TRANSIENT, ProviderBadResponse, ProviderError
from basis.llm.prompts import Prompt
from basis.llm.provider import BudgetExceededError, ChatProvider, ChatRequest, ImagePart, Message
from basis.vision import fusion
from basis.vision.merge import merge_analyses, merge_components
from basis.vision.prep import PreparedImage, prepare_image
from basis.vision.schemas import (
    MAX_COMPONENTS,
    Component,
    ComponentLabel,
    ModelPhotoAnalysis,
    PhotoAnalysis,
    RoomType,
    ambiguous_analysis,
)

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
FUSION_FALLBACK: Final = "count_fusion_fallback"
# Waits before the second and third try of the fusion call after a transient provider error
# (the pool has already retried once and tried the fallback provider by then).
FUSION_RETRY_DELAYS_S: tuple[float, ...] = (2.0, 8.0)
# The fusion call's whole time, retries included: past it the photo keeps the base counts
# (flagged) instead of running into the analyze_photo activity's 2-minute timeout, which
# would retry or fail the whole photo.
FUSION_DEADLINE_S: float = 80.0


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


@dataclass(frozen=True)
class FusionConfig:
    """The second countables call of per-class count fusion (``VISION_COUNT_FUSION``)."""

    model: str
    max_tokens: int
    reasoning_effort: str | None = None
    table: Mapping[str, str] = field(default_factory=lambda: dict(fusion.PER_CLASS_SOURCE))
    table_version: str = fusion.TABLE_VERSION


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
    # Per-class count fusion in the decomposed step; ``None`` = off.
    fusion: FusionConfig | None = None


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
class FusionOutcome:
    """What the fusion call did for one photo.

    ``status``: ``fused`` (counts taken per class), ``fallback`` (the second call failed:
    the base counts stand and the photo is flagged; ``reason`` says why) or ``not_applied``
    (the base calls gave no usable analysis, so there was nothing to fuse into).
    ``classes``: per count class of the table, the source and model chosen and each
    source's count (``second`` is ``None`` without a usable second answer)."""

    table_version: str
    model: str
    status: Literal["fused", "fallback", "not_applied"]
    call: CallOutcome
    reason: str | None = None
    classes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return {
            "table_version": self.table_version,
            "model": self.model,
            "status": self.status,
            "reason": self.reason,
            "classes": self.classes,
            "second_answer": (
                None if self.call.parsed is None else self.call.parsed.model_dump(mode="json")
            ),
        }

    def count_sources(self) -> dict[str, str]:
        """``{count class: model}`` of the classes the second model counted."""
        return {c: v["model"] for c, v in self.classes.items() if v["source"] == fusion.SECOND}


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
    fusion: FusionOutcome | None = None


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
    second_task = (
        asyncio.ensure_future(_fusion_call(image, ctx, ctx.fusion, countables))
        if ctx.fusion is not None
        else None
    )
    try:
        calls = await _all_or_none(
            call_until_usable(image, replace(ctx, prompt=countables), label=f"{countables.name} "),
            call_until_usable(image, replace(ctx, prompt=surfaces), label=f"{surfaces.name} "),
        )
    except BaseException:
        if second_task is not None:
            second_task.cancel()
            await asyncio.gather(second_task, return_exceptions=True)
        raise
    second = await second_task if second_task is not None else None
    countables_call, surfaces_call = calls
    errors = [*countables_call.errors, *surfaces_call.errors]
    unusable = [c.prompt_version for c in calls if c.parsed is None]
    outcome: FusionOutcome | None = None
    if unusable:
        # One narrow answer alone is not an analysis of the photo: no half result.
        analysis = ambiguous_analysis(f"{INVALID_OUTPUT}:{','.join(unusable)}")
        if second is not None:
            assert ctx.fusion is not None
            outcome = FusionOutcome(
                ctx.fusion.table_version, ctx.fusion.model, "not_applied", second[0], second[1]
            )
    else:
        assert countables_call.parsed is not None and surfaces_call.parsed is not None
        merged = merge_analyses(countables_call.parsed, surfaces_call.parsed)
        if merged.dropped:
            errors.append(f"merge: {merged.dropped} component(s) beyond the schema cap dropped")
        analysis = review(merged.analysis)
        if second is not None:
            assert ctx.fusion is not None
            analysis, outcome, dropped = _apply_fusion(
                analysis, countables_call.parsed, second, ctx.fusion, ctx.model
            )
            if dropped:
                errors.append(f"fusion: {dropped} component(s) beyond the schema cap dropped")
    all_calls = [*calls, second[0]] if second is not None else list(calls)
    if second is not None:
        errors += second[0].errors
    return PhotoResult(
        analysis=analysis,
        image=image,
        attempts=sum(c.attempts for c in all_calls),
        provider=_joined([c.provider for c in all_calls]),
        model=_joined([c.model for c in all_calls]),
        raw=[r for c in all_calls for r in c.raw],
        errors=errors,
        mode="decomposed",
        prompt_versions=tuple(c.prompt_version for c in calls),
        calls=tuple(all_calls),
        fusion=outcome,
    )


async def _fusion_call(
    image: PreparedImage, ctx: VisionContext, config: FusionConfig, prompt: Prompt
) -> tuple[CallOutcome, str | None]:
    """The countables prompt on the fusion model: the outcome and, when it gave no usable
    answer, why (``None`` when usable). Never raises a provider or budget error: a
    transient one is retried after each of :data:`FUSION_RETRY_DELAYS_S`, anything else
    ends the call, and the whole call stops at :data:`FUSION_DEADLINE_S`; the caller then
    falls back to the base counts."""
    errors: list[str] = []
    try:
        return await asyncio.wait_for(
            _fusion_attempts(image, ctx, config, prompt, errors), FUSION_DEADLINE_S
        )
    except TimeoutError:
        errors.append(f"{prompt.name}@{config.model} no answer within {FUSION_DEADLINE_S:g}s")
        return CallOutcome(prompt.version_id, None, 1, errors=errors), "timeout"


async def _fusion_attempts(
    image: PreparedImage,
    ctx: VisionContext,
    config: FusionConfig,
    prompt: Prompt,
    errors: list[str],
) -> tuple[CallOutcome, str | None]:
    second_ctx = replace(
        ctx,
        prompt=prompt,
        model=config.model,
        max_tokens=config.max_tokens,
        reasoning_effort=config.reasoning_effort,
        fusion=None,
    )
    label = f"{prompt.name}@{config.model} "
    attempts = 0
    for delay in (0.0, *FUSION_RETRY_DELAYS_S):
        if delay:
            ctx.heartbeat(f"vision {label}waiting {delay:g}s")
            await asyncio.sleep(delay)
        try:
            outcome = await call_until_usable(image, second_ctx, label=label)
        except TRANSIENT as exc:
            attempts += 1
            errors.append(f"{label}{type(exc).__name__}: {exc}")
            continue
        except (ProviderError, BudgetExceededError) as exc:
            errors.append(f"{label}{type(exc).__name__}: {exc}")
            failed = CallOutcome(prompt.version_id, None, attempts + 1, errors=errors)
            return failed, type(exc).__name__
        outcome.attempts += attempts
        outcome.errors[:0] = list(errors)
        return outcome, (None if outcome.parsed is not None else INVALID_OUTPUT)
    return CallOutcome(prompt.version_id, None, attempts, errors=errors), "provider_unavailable"


def _label_counts(components: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in components:
        if c["label"] != ComponentLabel.OTHER.value:
            counts[c["label"]] = counts.get(c["label"], 0) + int(c["count"])
    return counts


def fuse_components(
    merged: Sequence[Component],
    base: Sequence[Component],
    second: Sequence[Component],
    table: Mapping[str, str],
) -> tuple[list[Component], dict[str, dict[str, Any]], int]:
    """The merged components with every count class the table gives to
    :data:`~basis.vision.fusion.SECOND` replaced by the second answer's components of that
    class (its labels, counts, materials and attachments; the second answer is deduplicated
    by label with :func:`~basis.vision.merge.merge_components`, as the base answer was).

    Returns the components (non-``other`` first, ``other`` last, cut to the schema's cap),
    per class ``{"source", "base", "second", "count"}`` and how many were cut. The class
    counts equal :func:`basis.vision.fusion.fuse` on the two answers' counts (tested)."""
    second_dicts = merge_components([[c.model_dump(mode="json") for c in second]])
    base_counts = _label_counts(merge_components([[c.model_dump(mode="json") for c in base]]))
    second_counts = _label_counts(second_dicts)
    taken = {
        label
        for count_class, source in table.items()
        if source == fusion.SECOND
        for label in fusion.CLASS_LABELS.get(count_class, (count_class,))
    }
    kept = [c for c in merged if c.label.value not in taken]
    added = [Component.model_validate(c) for c in second_dicts if c["label"] in taken]
    ordered = [
        *[c for c in kept if c.label is not ComponentLabel.OTHER],
        *added,
        *[c for c in kept if c.label is ComponentLabel.OTHER],
    ]
    components = ordered[:MAX_COMPONENTS]
    final_counts = _label_counts([c.model_dump(mode="json") for c in components])
    classes = {
        count_class: {
            "source": source,
            "base": fusion.class_count(base_counts, count_class),
            "second": fusion.class_count(second_counts, count_class),
            "count": fusion.class_count(final_counts, count_class),
        }
        for count_class, source in table.items()
    }
    return components, classes, len(ordered) - len(components)


def _apply_fusion(
    analysis: PhotoAnalysis,
    base: ModelPhotoAnalysis,
    second: tuple[CallOutcome, str | None],
    config: FusionConfig,
    base_model: str,
) -> tuple[PhotoAnalysis, FusionOutcome, int]:
    call, failure = second
    if call.parsed is None:
        reason = f"{FUSION_FALLBACK}:{failure or INVALID_OUTPUT}"
        existing = analysis.review_reason
        flagged = analysis.model_copy(
            update={
                "needs_review": True,
                "review_reason": f"{existing}; {reason}" if existing else reason,
            }
        )
        base_counts = _label_counts([c.model_dump(mode="json") for c in analysis.components])
        classes = {
            count_class: {
                "source": fusion.BASE,
                "model": base_model,
                "base": fusion.class_count(base_counts, count_class),
                "second": None,
                "count": fusion.class_count(base_counts, count_class),
            }
            for count_class in config.table
        }
        outcome = FusionOutcome(
            config.table_version, config.model, "fallback", call, failure, classes
        )
        return flagged, outcome, 0
    components, classes, dropped = fuse_components(
        analysis.components, base.components, call.parsed.components, config.table
    )
    for entry in classes.values():
        entry["model"] = config.model if entry["source"] == fusion.SECOND else base_model
    fused = analysis.model_copy(update={"components": components})
    outcome = FusionOutcome(config.table_version, config.model, "fused", call, None, classes)
    return fused, outcome, dropped


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
