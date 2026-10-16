"""``analyze_photo``: one structured vision call per photo (S3_SPEC sections 2 and 4).

Migrated from ``agentic/agents/vision_agent.py`` (``analyze_image_with_provider``). The
legacy path asked for free-form JSON, parsed it with four fallback strategies, retried
twice when the room came back ``unknown``, and on any exception returned
``room_type="unknown"`` with confidence 0 as if that were an answer. Here:

* The image is prepared first (:mod:`basis.vision.prep`); a rejected image raises
  :class:`~basis.vision.prep.ImageRejected` (the activity makes it non-retryable).
* One call with the versioned prompt and the :class:`ModelPhotoAnalysis` JSON schema in
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

Every attempt's raw provider response is returned in :class:`PhotoResult` for storage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.errors import ProviderBadResponse
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.vision.prep import PreparedImage, prepare_image
from basis.vision.schemas import ModelPhotoAnalysis, PhotoAnalysis, RoomType, ambiguous_analysis

PROMPT_NAME: Final = "photo_analysis"
# The released version the pipeline runs. A newer version is released for evaluation first
# (evals.predict_photos --prompt vN) and promoted here only when it wins (EVAL_REPORT).
PROMPT_VERSION: Final = "v1"
MAX_ATTEMPTS: Final = 2
# Below this self-reported room confidence the photo goes to the engineer (eval-tuned later).
ROOM_REVIEW_THRESHOLD: Final = 0.5
USER_TEXT: Final = (
    "Analyze this property photo. Identify the room type and every visible building "
    "component, using only what is visible."
)


def released_prompt() -> Prompt:
    """The photo prompt the pipeline runs (``PROMPT_NAME``/``PROMPT_VERSION``)."""
    return prompts.load(PROMPT_NAME, PROMPT_VERSION)


def _no_heartbeat(_: str) -> None:
    return None


@dataclass
class VisionContext:
    call: CallContext
    provider: ChatProvider
    prompt: Prompt
    model: str
    max_tokens: int = 1200
    heartbeat: Callable[[str], None] = field(default=_no_heartbeat)


@dataclass
class PhotoResult:
    analysis: PhotoAnalysis
    image: PreparedImage
    attempts: int
    provider: str | None = None
    model: str | None = None
    raw: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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


async def analyze_photo_detailed(image_bytes: bytes, ctx: VisionContext) -> PhotoResult:
    image = prepare_image(image_bytes)
    request = build_request(image, ctx)
    raws: list[dict[str, Any]] = []
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ctx.heartbeat(f"vision attempt {attempt}")
        try:
            response = await call_model(ctx.provider, request, ctx.call)
        except ProviderBadResponse as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if exc.raw is not None:
                raws.append(exc.raw)
            continue
        raws.append(response.raw)
        parsed = response.parsed
        if not isinstance(parsed, ModelPhotoAnalysis):
            errors.append(f"attempt {attempt}: no structured analysis in the response")
            continue
        return PhotoResult(
            analysis=review(parsed.to_analysis()),
            image=image,
            attempts=attempt,
            provider=response.provider,
            model=response.model,
            raw=raws,
            errors=errors,
        )
    return PhotoResult(
        analysis=ambiguous_analysis("invalid_model_output"),
        image=image,
        attempts=MAX_ATTEMPTS,
        raw=raws,
        errors=errors,
    )


async def analyze_photo(image_bytes: bytes, ctx: VisionContext) -> PhotoAnalysis:
    """The binding S3 interface: the photo's analysis (see :func:`analyze_photo_detailed`)."""
    return (await analyze_photo_detailed(image_bytes, ctx)).analysis
