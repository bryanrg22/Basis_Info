"""One budgeted, recorded model call: reserve -> call -> record -> charge.

Every LLM call in ``basis`` goes through :func:`call_model` (S3_SPEC section 0):

1. ``budget.reserve(estimate)`` before calling, with :func:`basis.llm.cost.estimate_usd`
   (prompt length, images, and the full ``max_tokens`` of output). Not enough budget
   raises :class:`BudgetExceededError` and nothing is sent.
2. The provider call.
3. The record (``llm_calls`` row) as soon as the call has reached the model, including
   when the answer was unusable (:class:`ProviderBadResponse` carries the usage).
4. ``budget.charge(actual)``; if the real cost pushed the study over its budget this
   raises :class:`BudgetExceededError` after the fact. The caller discards that result:
   the hard cap holds and the overspend is bounded by one call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from basis.llm.cost import estimate_usd
from basis.llm.errors import ProviderError
from basis.llm.provider import Budget, ChatProvider, ChatRequest, ChatResponse
from basis.llm.recorder import Recorder


@dataclass(frozen=True, slots=True)
class CallContext:
    """Who is paying for a call and where it is recorded."""

    study_id: uuid.UUID
    run_id: uuid.UUID | None
    activity: str
    budget: Budget
    recorder: Recorder


def estimate_request_usd(request: ChatRequest) -> float:
    chars = len(request.system) + sum(len(m.content) for m in request.messages)
    images = sum(len(m.images) for m in request.messages)
    return estimate_usd(request.model, request.max_tokens, chars, images)


async def call_model(
    provider: ChatProvider, request: ChatRequest, ctx: CallContext
) -> ChatResponse:
    ctx.budget.reserve(estimate_request_usd(request))
    try:
        response = await provider.complete(request)
    except ProviderError as error:
        if error.usage is not None:
            cost = await ctx.recorder.record(
                study_id=ctx.study_id,
                run_id=ctx.run_id,
                activity=ctx.activity,
                prompt_version=request.prompt_version,
                provider=error.provider or getattr(provider, "name", "unknown"),
                model=error.model or request.model,
                usage=error.usage,
                latency_ms=None,
            )
            ctx.budget.charge(float(cost))
        raise
    cost = await ctx.recorder.record(
        study_id=ctx.study_id,
        run_id=ctx.run_id,
        activity=ctx.activity,
        prompt_version=request.prompt_version,
        provider=response.provider,
        model=response.model,
        usage=response.usage,
        latency_ms=response.latency_ms,
    )
    ctx.budget.charge(float(cost))
    return response
