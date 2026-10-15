"""``call_model``: reserve -> call -> record -> charge, including the failure paths."""

from __future__ import annotations

import uuid

import pytest

from basis.llm import Budget, BudgetExceededError, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext, call_model, estimate_request_usd
from basis.llm.cost import cost_usd
from basis.llm.errors import ProviderBadResponse, ProviderRateLimited
from basis.llm.provider import ChatRequest, ImagePart, Message
from basis.llm.recorder import MemoryRecorder


def request(**changes: object) -> ChatRequest:
    values: dict[str, object] = {
        "model": "gpt-4o-mini",
        "system": "x" * 400,
        "messages": (Message(role="user", content="y" * 400),),
        "max_tokens": 500,
        "prompt_version": "p/v1",
        "study_id": "s",
        "activity": "classify",
    }
    values.update(changes)
    return ChatRequest(**values)  # type: ignore[arg-type]


def context(budget: float = 1.0) -> CallContext:
    return CallContext(
        study_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        activity="classify",
        budget=Budget(remaining_usd=budget),
        recorder=MemoryRecorder(),
    )


def test_the_estimate_counts_text_images_and_output() -> None:
    text = estimate_request_usd(request())
    assert text == pytest.approx((200 * 0.15 + 500 * 0.60) / 1e6, abs=1e-6)
    with_image = request(
        messages=(Message(role="user", content="y" * 400, images=(ImagePart(data_base64="QQ=="),)),)
    )
    assert estimate_request_usd(with_image) > text


async def test_records_and_charges_the_real_cost() -> None:
    usage = Usage(input_tokens=300, output_tokens=100)
    provider = ScriptedProvider([ScriptedTurn(text="ok", usage=usage)], model="gpt-4o-mini")
    ctx = context()
    await call_model(provider, request(), ctx)
    [record] = ctx.recorder.records  # type: ignore[attr-defined]
    assert (record.model, record.prompt_version, record.activity) == (
        "gpt-4o-mini",
        "p/v1",
        "classify",
    )
    assert record.run_id == ctx.run_id and record.study_id == ctx.study_id
    assert ctx.budget.spent_usd == pytest.approx(cost_usd("gpt-4o-mini", usage))
    assert ctx.budget.calls == 1


async def test_not_enough_budget_means_no_call_and_no_record() -> None:
    provider = ScriptedProvider([])
    ctx = context(budget=0.0001)
    with pytest.raises(BudgetExceededError):
        await call_model(provider, request(), ctx)
    assert provider.requests == [] and ctx.recorder.records == []  # type: ignore[attr-defined]


async def test_a_bad_response_is_recorded_and_charged_then_raised() -> None:
    usage = Usage(input_tokens=1000, output_tokens=50)
    error = ProviderBadResponse("bad", provider="azure", usage=usage, model="gpt-4o-mini")
    ctx = context()
    with pytest.raises(ProviderBadResponse):
        await call_model(ScriptedProvider([ScriptedTurn(raise_error=error)]), request(), ctx)
    [record] = ctx.recorder.records  # type: ignore[attr-defined]
    assert (record.provider, record.usage, record.latency_ms) == ("azure", usage, None)
    assert ctx.budget.calls == 1


async def test_an_error_before_the_model_is_not_recorded() -> None:
    error = ProviderRateLimited("429", provider="azure", status=429)
    ctx = context()
    with pytest.raises(ProviderRateLimited):
        await call_model(ScriptedProvider([ScriptedTurn(raise_error=error)]), request(), ctx)
    assert ctx.recorder.records == []  # type: ignore[attr-defined]
    assert ctx.budget.calls == 0


async def test_an_overrun_is_recorded_and_raises_after_the_call() -> None:
    usage = Usage(input_tokens=500_000, output_tokens=0)  # $0.075, estimate was ~$0.0003
    provider = ScriptedProvider([ScriptedTurn(text="ok", usage=usage)], model="gpt-4o-mini")
    ctx = context(budget=0.01)
    with pytest.raises(BudgetExceededError):
        await call_model(provider, request(), ctx)
    assert len(ctx.recorder.records) == 1  # type: ignore[attr-defined]
    assert ctx.budget.remaining_usd < 0
