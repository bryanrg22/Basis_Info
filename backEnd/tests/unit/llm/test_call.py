"""``call_model``: reserve -> call -> record -> charge, including the failure paths."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid

import pytest

from basis.llm import Budget, BudgetExceededError, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext, call_model, estimate_request_usd, request_payload
from basis.llm.cost import cost_usd
from basis.llm.errors import ProviderBadResponse, ProviderRateLimited, redact_api_keys
from basis.llm.provider import ChatRequest, ImagePart, Message, request_params
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


# ------------------------------------------------------------------ the ledger (section 40)


async def test_the_record_names_the_calling_component_and_archives_both_payloads() -> None:
    usage = Usage(input_tokens=300, output_tokens=100)
    provider = ScriptedProvider([ScriptedTurn(text="ok", usage=usage)], model="gpt-4o-mini")
    ctx = context().about("photo", uuid.uuid4())
    await call_model(provider, request(), ctx)
    recorder = ctx.recorder
    assert isinstance(recorder, MemoryRecorder)
    [record] = recorder.records
    me = test_the_record_names_the_calling_component_and_archives_both_payloads.__name__
    assert record.component == f"{__name__}.{me}"
    assert (record.entity_type, record.entity_id) == ("photo", ctx.entity_id)
    assert record.entry.requested_model == "gpt-4o-mini" and record.entry.finish_reason == "stop"
    assert record.price_row == "gpt-4o-mini" and record.status == "ok"
    assert record.request_uri in recorder.payloads and record.response_uri in recorder.payloads
    sent = json.loads(recorder.payloads[record.request_uri])
    assert sent["system"] == "x" * 400 and sent["messages"][0]["content"] == "y" * 400
    assert sent["params"]["max_output_tokens"] == 500 and sent["prompt"]["name"] == "p"
    got = json.loads(recorder.payloads[record.response_uri])
    assert got["usage"]["input_tokens"] == 300 and got["cost_usd"] == str(record.cost_usd)
    assert got["response_id"] == "scripted-1" and got["text"] == "ok"
    assert (
        hashlib.sha256(recorder.payloads[record.request_uri]).hexdigest() == record.request_sha256
    )


async def test_a_bad_response_is_an_error_row_with_the_reason_and_the_usage() -> None:
    usage = Usage(input_tokens=1000, output_tokens=50)
    error = ProviderBadResponse(
        "output truncated at max_tokens",
        provider="azure",
        usage=usage,
        model="gpt-4o-mini",
        finish_reason="length",
        response_id="chatcmpl-9",
        raw={"usage": {"prompt_tokens": 1000, "completion_tokens": 50}},
    )
    ctx = context()
    with pytest.raises(ProviderBadResponse):
        await call_model(ScriptedProvider([ScriptedTurn(raise_error=error)]), request(), ctx)
    [record] = ctx.recorder.records  # type: ignore[attr-defined]
    assert record.status == "error"
    assert record.entry.error == "ProviderBadResponse: output truncated at max_tokens"
    assert (record.entry.finish_reason, record.entry.response_id) == ("length", "chatcmpl-9")
    assert record.usage == usage and record.cost_usd > 0
    assert ctx.budget.calls == 1


async def test_api_keys_never_reach_the_stored_payloads() -> None:
    leaked = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # pragma: allowlist secret
    usage = Usage(input_tokens=10, output_tokens=5)
    provider = ScriptedProvider(
        [ScriptedTurn(text=f"your key {leaked} is invalid", usage=usage)], model="gpt-4o-mini"
    )
    ctx = context()
    await call_model(
        provider,
        request(
            system=f"Authorization: Bearer {leaked} and sk-proj-ZYXWVUTSRQPONMLKJIHGFEDCBA9876"
        ),
        ctx,
    )
    recorder = ctx.recorder
    assert isinstance(recorder, MemoryRecorder)
    [record] = recorder.records
    for uri in (record.request_uri, record.response_uri):
        assert uri is not None
        text = recorder.payloads[uri].decode()
        assert leaked not in text and "sk-proj-" not in text and "[redacted]" in text


def test_image_bytes_are_replaced_by_their_record() -> None:
    image = ImagePart(data_base64=base64.b64encode(b"\x00" * 1000).decode(), ref="doc-1")
    req = request(messages=(Message(role="user", content="look", images=(image,)),))
    payload = request_payload(req, context(), request_params(req))
    [stored] = payload["messages"][0]["images"]
    assert stored["ref"] == "doc-1" and stored["bytes"] == 1000
    assert stored["sha256"] == hashlib.sha256(b"\x00" * 1000).hexdigest()
    assert "AAAA" not in json.dumps(payload)
    assert payload["params"]["image_count"] == 1 and payload["params"]["image_bytes"] == 1000


def test_redaction_leaves_hex_ids_alone() -> None:
    chunk = uuid.uuid4().hex
    fake_key = "sk-ant-api03-ABCDEFGHIJ0123456789"  # pragma: allowlist secret
    text = f'{{"chunk": "{chunk}", "key": "{fake_key}"}}'
    redacted = redact_api_keys(text)
    assert chunk in redacted and "sk-ant" not in redacted
