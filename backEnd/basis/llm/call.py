"""One budgeted, recorded model call: reserve -> call -> record -> charge.

Every LLM call in ``basis`` goes through :func:`call_model` (S3_SPEC section 0):

1. ``budget.reserve(estimate)`` before calling, with :func:`basis.llm.cost.estimate_usd`
   (prompt length, images, and the full ``max_tokens`` of output). Not enough budget
   raises :class:`BudgetExceededError` and nothing is sent.
2. The provider call.
3. The record (``llm_calls`` row, :mod:`basis.llm.recorder`) as soon as the call has reached
   the model, including when the answer was unusable (:class:`ProviderBadResponse` carries
   the usage; the row is ``status = error`` with the reason).
4. ``budget.charge(actual)``; if the real cost pushed the study over its budget this
   raises :class:`BudgetExceededError` after the fact. The caller discards that result:
   the hard cap holds and the overspend is bounded by one call.

What the record knows about the call comes from three places: the :class:`CallContext`
(who pays, and the calling component: stage, activity and attempt, revision, the entity the
call is about), the request (the prompt and its parameters) and the response or error (the
model that answered, the usage, the identifiers, the finish reason). The Python module and
function that called :func:`call_model` are read from the stack (:func:`caller_component`),
like OpenTelemetry's ``code.function.name``, so no caller has to name itself.
"""

from __future__ import annotations

import base64
import sys
import time
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from types import FrameType
from typing import Any

from basis.llm import prompts
from basis.llm.cost import estimate_usd
from basis.llm.errors import ProviderError
from basis.llm.provider import (
    Budget,
    ChatProvider,
    ChatRequest,
    ChatResponse,
    ImagePart,
    Message,
    Usage,
    request_params,
)
from basis.llm.recorder import (
    STATUS_ERROR,
    STATUS_OK,
    CallRecord,
    LedgerEntry,
    Recorder,
    sha256_hex,
)

LLM_PACKAGE = "basis.llm"
MAX_ERROR = 1000


@dataclass(frozen=True, slots=True)
class CallContext:
    """Who is paying for a call, where it is recorded, and which component is calling.

    ``stage``, ``revision`` and ``activity_attempt`` describe the activity execution;
    ``entity_type``/``entity_id`` the thing the call is about (``photo`` with the document
    id, ``room``, ``component``, ``appraisal``): :meth:`about` derives a context for one
    entity from the activity's.
    """

    study_id: uuid.UUID
    run_id: uuid.UUID | None
    activity: str
    budget: Budget
    recorder: Recorder
    stage: str | None = None
    revision: int | None = None
    activity_attempt: int | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None

    def about(self, entity_type: str, entity_id: uuid.UUID | str | None) -> CallContext:
        """This context for calls about one entity."""
        parsed = entity_id if isinstance(entity_id, uuid.UUID | type(None)) else _uuid(entity_id)
        return replace(self, entity_type=entity_type, entity_id=parsed)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def estimate_request_usd(request: ChatRequest) -> float:
    chars = len(request.system) + sum(len(m.content) for m in request.messages)
    images = sum(len(m.images) for m in request.messages)
    return estimate_usd(request.model, request.max_tokens, chars, images)


def caller_component(skip_frames: int = 1) -> str | None:
    """``module.qualname`` of the nearest caller outside :mod:`basis.llm` (the component
    that made the call, as the ledger records it); ``None`` when there is none."""
    frame: FrameType | None = sys._getframe(skip_frames)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module != LLM_PACKAGE and not module.startswith(LLM_PACKAGE + "."):
            return f"{module}.{frame.f_code.co_qualname}"
        frame = frame.f_back
    return None


# --------------------------------------------------------------------------- payloads


def _image_record(image: ImagePart) -> dict[str, Any]:
    """An image as the stored request keeps it: never the bytes."""
    data = image.data_base64.encode("ascii")
    try:
        raw = base64.b64decode(data, validate=False)
    except ValueError:
        raw = b""
    return {
        "ref": image.ref,
        "media_type": image.media_type,
        "detail": image.detail,
        "bytes": len(raw),
        "sha256": sha256_hex(raw),
    }


def _message_record(message: Message) -> dict[str, Any]:
    record: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name is not None:
        record["name"] = message.name
    if message.tool_call_id is not None:
        record["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        record["tool_calls"] = [c.model_dump(mode="json") for c in message.tool_calls]
    if message.images:
        record["images"] = [_image_record(i) for i in message.images]
    return record


def request_payload(
    request: ChatRequest, ctx: CallContext, params: dict[str, Any]
) -> dict[str, Any]:
    """The request as sent, for the archive (module doc): images replaced by their record."""
    prompt = _prompt_identity(request.prompt_version)
    return {
        "study_id": str(ctx.study_id),
        "activity": ctx.activity,
        "entity": {"type": ctx.entity_type, "id": _str(ctx.entity_id)},
        "requested_model": request.model,
        "prompt": prompt,
        "params": params,
        "system": request.system,
        "messages": [_message_record(m) for m in request.messages],
        "tools": [t.model_dump(mode="json") for t in request.tools],
        "response_schema": (
            None if request.response_model is None else request.response_model.model_json_schema()
        ),
    }


def _str(value: object) -> str | None:
    return None if value is None else str(value)


def _prompt_identity(prompt_version: str | None) -> dict[str, str | None]:
    """``{name, version, sha256}`` of ``"<name>/<version>"``; the sha256 only for a released
    prompt (``basis.llm.prompts``), ``None`` for an ad-hoc version id."""
    if not prompt_version:
        return {"name": None, "version": None, "sha256": None}
    name, sep, version = prompt_version.partition("/")
    if not sep:
        return {"name": prompt_version, "version": None, "sha256": None}
    try:
        sha = prompts.load(name, version).sha256
    except prompts.PromptError:
        sha = None
    return {"name": name, "version": version, "sha256": sha}


# --------------------------------------------------------------------------- entries


def chat_entry(
    ctx: CallContext,
    request: ChatRequest,
    *,
    provider: str,
    model: str,
    usage: Usage,
    status: str = STATUS_OK,
    error: str | None = None,
    latency_ms: int | None = None,
    elapsed_ms: int | None = None,
    response_id: str | None = None,
    request_id: str | None = None,
    finish_reason: str | None = None,
    endpoint: str | None = None,
    params: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    text: str = "",
    component: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> LedgerEntry:
    """The ledger entry of one model call (answered or failed after reaching the model).
    ``tools`` are the tool calls this answer asked for, as the caller executed them (name,
    arguments, outcome, duration), stored in the response payload."""
    sent = params if params else request_params(request)
    prompt = _prompt_identity(request.prompt_version)
    response: dict[str, Any] = {
        "status": status,
        "error": error,
        "provider": provider,
        "model": model,
        "response_id": response_id,
        "request_id": request_id,
        "finish_reason": finish_reason,
        "latency_ms": latency_ms,
        "elapsed_ms": elapsed_ms,
        "usage": usage.model_dump(mode="json"),
        "usage_raw": (raw or {}).get("usage"),
        "text": text,
        "tool_calls": tools or [],
        "raw": raw,
    }
    return LedgerEntry(
        study_id=ctx.study_id,
        run_id=ctx.run_id,
        activity=ctx.activity,
        provider=provider,
        model=model,
        usage=usage,
        latency_ms=latency_ms,
        prompt_version=request.prompt_version,
        status=status,
        error=None if error is None else error[:MAX_ERROR],
        stage=ctx.stage,
        revision=ctx.revision,
        activity_attempt=ctx.activity_attempt,
        component=component,
        prompt_name=prompt["name"],
        prompt_sha256=prompt["sha256"],
        entity_type=ctx.entity_type,
        entity_id=ctx.entity_id,
        requested_model=request.model,
        endpoint=endpoint,
        response_id=response_id,
        request_id=request_id,
        finish_reason=finish_reason,
        request_params=sent,
        elapsed_ms=elapsed_ms,
        request_payload=request_payload(request, ctx, sent),
        response_payload=response,
    )


async def record_response(
    ctx: CallContext,
    request: ChatRequest,
    response: ChatResponse,
    *,
    elapsed_ms: int | None = None,
    component: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> CallRecord:
    """Record an answered call (for the loops that call the provider themselves, such as
    the classifier's, which also passes the tool calls it executed for this answer);
    :func:`call_model` does this for everything else."""
    entry = chat_entry(
        ctx,
        request,
        provider=response.provider,
        model=response.model,
        usage=response.usage,
        latency_ms=response.latency_ms,
        elapsed_ms=elapsed_ms,
        response_id=response.response_id,
        request_id=response.request_id,
        finish_reason=response.finish_reason,
        endpoint=response.endpoint,
        params=response.request_params,
        raw=response.raw,
        text=response.text,
        component=component or caller_component(2),
        tools=tools,
    )
    return await ctx.recorder.record(entry)


async def record_error(
    ctx: CallContext,
    request: ChatRequest,
    error: ProviderError,
    *,
    provider: str,
    elapsed_ms: int | None = None,
    component: str | None = None,
) -> CallRecord | None:
    """Record a call that reached the model and failed (the error carries usage); ``None``
    when nothing reached it (no row: nothing was spent)."""
    if error.usage is None:
        return None
    entry = chat_entry(
        ctx,
        request,
        provider=error.provider or provider,
        model=error.model or request.model,
        usage=error.usage,
        status=STATUS_ERROR,
        error=f"{type(error).__name__}: {error}",
        latency_ms=error.latency_ms,
        elapsed_ms=elapsed_ms,
        response_id=error.response_id,
        request_id=error.request_id,
        finish_reason=error.finish_reason,
        endpoint=error.endpoint,
        params=error.request_params,
        raw=error.raw,
        component=component or caller_component(2),
    )
    return await ctx.recorder.record(entry)


async def call_model(
    provider: ChatProvider, request: ChatRequest, ctx: CallContext
) -> ChatResponse:
    component = caller_component(2)
    ctx.budget.reserve(estimate_request_usd(request))
    started = time.perf_counter()
    try:
        response = await provider.complete(request)
    except ProviderError as error:
        elapsed = _elapsed_ms(started)
        recorded = await record_error(
            ctx,
            request,
            error,
            provider=getattr(provider, "name", "unknown"),
            elapsed_ms=elapsed,
            component=component,
        )
        if recorded is not None:
            ctx.budget.charge(float(recorded.cost_usd))
        raise
    recorded = await record_response(
        ctx, request, response, elapsed_ms=_elapsed_ms(started), component=component
    )
    ctx.budget.charge(float(recorded.cost_usd))
    return response


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def step_entry(
    ctx: CallContext,
    *,
    kind: str,
    provider: str,
    model: str,
    quantity: Decimal | float,
    unit: str,
    unit_price_usd: Decimal | float,
    cost_usd: Decimal | float,
    status: str = STATUS_OK,
    error: str | None = None,
    latency_ms: int | None = None,
    response_id: str | None = None,
    endpoint: str | None = None,
    params: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    component: str | None = None,
) -> LedgerEntry:
    """The ledger entry of a non-token paid step (an Azure DI analysis, a SAM 3 prediction):
    ``quantity`` of ``unit`` at ``unit_price_usd``, costing ``cost_usd`` as metered."""
    return LedgerEntry(
        study_id=ctx.study_id,
        run_id=ctx.run_id,
        activity=ctx.activity,
        provider=provider,
        model=model,
        latency_ms=latency_ms,
        kind=kind,
        status=status,
        error=None if error is None else error[:MAX_ERROR],
        stage=ctx.stage,
        revision=ctx.revision,
        activity_attempt=ctx.activity_attempt,
        component=component or caller_component(2),
        entity_type=ctx.entity_type,
        entity_id=ctx.entity_id,
        requested_model=model,
        endpoint=endpoint,
        response_id=response_id,
        request_params=dict(params or {}),
        quantity=Decimal(str(quantity)),
        unit=unit,
        unit_price_usd=Decimal(str(unit_price_usd)),
        cost_usd=Decimal(str(cost_usd)),
        elapsed_ms=latency_ms,
        request_payload=None
        if params is None
        else {
            "study_id": str(ctx.study_id),
            "activity": ctx.activity,
            "entity": {"type": ctx.entity_type, "id": _str(ctx.entity_id)},
            "kind": kind,
            "provider": provider,
            "model": model,
            "params": params,
        },
        response_payload=response,
    )
