"""The residual loop under a scripted model: caps, bad output, bad tools, failures.

BUILD_PLAN 6.1 ``unit/classify/test_tool_loop.py``: tool-call cap, iteration cap, invalid
JSON, unknown tool, malformed arguments, infinite tool loop, provider error, each ending
in ``needs_review`` with a reason (never an exception).
"""

from __future__ import annotations

import asyncio
import json

from classifytest_support import (
    STRUCTURAL_CHUNK,
    answer_turn,
    call,
    classification,
    component,
    happy_turns,
    make_ctx,
    make_store,
    tools_turn,
)

from basis.classify.residual import LoopLimits, run_residual
from basis.classify.router import RESIDUAL_ALLOWLIST, classify
from basis.classify.tools import build_tools
from basis.llm.provider import (
    Budget,
    ChatRequest,
    ChatResponse,
    ScriptedProvider,
    ScriptedTurn,
    Usage,
)

SEARCH = {"query": "garage door structural component", "k": 3}


async def test_happy_path_search_then_cited_answer() -> None:
    provider = ScriptedProvider(happy_turns())
    result = await classify(component(), make_ctx(provider))
    assert result.needs_review is False and result.review_reasons == ()
    assert [c.chunk_id for c in result.citations] == [STRUCTURAL_CHUNK]
    [trace] = result.tool_calls
    assert (trace.name, trace.refused, trace.iteration) == ("search_reference", False, 1)
    assert str(STRUCTURAL_CHUNK) in trace.chunk_ids
    first, second = provider.requests
    assert {t.name for t in first.tools} == set(RESIDUAL_ALLOWLIST)
    assert first.response_model is not None and first.prompt_version == "classify/v1"


async def test_tool_results_are_named_json_tool_messages() -> None:
    """W5 by construction: the tool message has the tool's name and JSON content."""
    provider = ScriptedProvider(happy_turns())
    await classify(component(), make_ctx(provider))
    messages = provider.requests[1].messages
    assistant, tool = messages[-2], messages[-1]
    assert assistant.role == "assistant"
    [replayed] = assistant.tool_calls  # native calls, not JSON text the APIs reject
    assert (replayed.id, replayed.name) == ("call-1", "search_reference")
    assert (tool.role, tool.name, tool.tool_call_id) == ("tool", "search_reference", "call-1")
    payload = json.loads(tool.content)
    assert payload["corpus"] == "reference"
    assert payload["hits"][0]["chunk_id"] == str(STRUCTURAL_CHUNK)


async def test_tool_call_cap_refuses_the_fourth_call_in_one_turn() -> None:
    calls = [call("search_reference", SEARCH, f"c{i}") for i in range(5)]
    provider = ScriptedProvider([tools_turn(*calls), answer_turn()])
    result = await classify(component(), make_ctx(provider))
    assert [t.refused for t in result.tool_calls] == [False, False, False, True, True]
    assert "cap" in (result.tool_calls[3].error or "")
    assert provider.requests[1].tools == ()  # no tools offered once the cap is spent
    assert not result.needs_review  # the answer itself is fine


async def test_infinite_tool_loop_stops_at_the_cap() -> None:
    turns = [tools_turn(call("search_reference", SEARCH, f"c{i}")) for i in range(10)]
    provider = ScriptedProvider(turns)
    result = await classify(component(), make_ctx(provider))
    assert len(provider.requests) == 4  # three tool turns, then a turn with no tools offered
    assert len([t for t in result.tool_calls if not t.refused]) == 3
    assert result.needs_review and result.review_reasons == ("tool_cap_exceeded",)
    assert result.confidence == 0.0 and result.citations == ()


async def test_iteration_cap() -> None:
    turns = [tools_turn(call("search_reference", SEARCH, f"c{i}")) for i in range(10)]
    provider = ScriptedProvider(turns)
    ctx = make_ctx(provider, limits=LoopLimits(max_tool_calls=10, max_iterations=4))
    result = await classify(component(), ctx)
    assert len(provider.requests) == 4
    assert result.review_reasons == ("iteration_cap",)


async def test_invalid_json_is_retried_once_then_succeeds() -> None:
    valid = classification().model_dump_json()
    provider = ScriptedProvider(
        [
            tools_turn(call("search_reference", SEARCH)),
            ScriptedTurn(text="Sure! The bucket is 27.5 years."),
            ScriptedTurn(text=valid),
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert len(provider.requests) == 3
    assert not result.needs_review
    retry = provider.requests[2].messages[-1]
    assert retry.role == "user" and "not a valid JSON" in retry.content


async def test_invalid_json_twice_is_needs_review() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(call("search_reference", SEARCH)),
            ScriptedTurn(text="{not json"),
            ScriptedTurn(text='{"bucket": "y100"}'),
            answer_turn(),  # never requested
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert len(provider.requests) == 3 and provider.remaining_turns == 1
    assert result.review_reasons == ("invalid_output",)


async def test_unknown_tool_is_refused_and_counts_toward_the_cap() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(call("approve_study", {"study_id": "x"}, "c1")),
            tools_turn(call("search_study", {"query": "garage"}, "c2")),  # not offered
            tools_turn(call("search_reference", SEARCH, "c3")),
            answer_turn(),
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert [(t.name, t.refused) for t in result.tool_calls] == [
        ("approve_study", True),
        ("search_study", True),
        ("search_reference", False),
    ]
    assert "unknown tool" in (result.tool_calls[0].error or "")
    assert provider.requests[3].tools == ()  # three calls used, even though two refused
    assert not result.needs_review


async def test_malformed_arguments_are_refused_not_raised() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(
                call("search_reference", {"q": "garage"}, "c1"),
                call("search_reference", {"query": "garage", "k": 99}, "c2"),
                call("get_chunk", {"chunk_id": "not-a-uuid"}, "c3"),
            ),
            answer_turn(),
        ]
    )
    result = await classify(component(), make_ctx(provider))
    errors = [t.error or "" for t in result.tool_calls]
    assert all(t.refused for t in result.tool_calls)
    assert "invalid arguments" in errors[0] and "k:" in errors[1]
    assert "not a valid id" in errors[2]
    # Nothing was retrieved, so the cited chunk is "not_retrieved" and dropped.
    assert result.citations == () and result.dropped[0].reason == "not_retrieved"
    assert result.review_reasons[0] == "missing_evidence"


async def test_provider_error_is_needs_review() -> None:
    provider = ScriptedProvider([ScriptedTurn(raise_error=RuntimeError("503 from upstream"))])
    result = await classify(component(), make_ctx(provider))
    assert result.review_reasons == ("provider_error:RuntimeError",)
    assert result.method == "llm" and result.confidence == 0.0


class SlowProvider:
    name = "slow"

    async def complete(self, request: ChatRequest) -> ChatResponse:
        await asyncio.sleep(5)
        raise AssertionError("not reached")


async def test_timeout_is_needs_review() -> None:
    ctx = make_ctx(SlowProvider(), limits=LoopLimits(timeout_s=0.05))  # type: ignore[arg-type]
    result = await classify(component(), ctx)
    assert result.review_reasons == ("timeout",)


async def test_budget_reservation_stops_before_any_call() -> None:
    provider = ScriptedProvider(happy_turns())
    result = await classify(component(), make_ctx(provider, budget_usd=0.001))
    assert provider.requests == []
    assert result.review_reasons == ("budget_exceeded",)


async def test_budget_is_charged_per_call() -> None:
    budget = Budget(remaining_usd=1.0)
    provider = ScriptedProvider(happy_turns())
    result = await classify(component(), make_ctx(provider, budget=budget))
    # 2 calls x (100 in x 0.15 + 50 out x 0.60) / 1e6
    assert budget.calls == 2
    assert budget.spent_usd == result.cost_usd == 2 * (100 * 0.15 + 50 * 0.60) / 1_000_000


async def test_answer_without_search_is_missing_evidence() -> None:
    provider = ScriptedProvider([answer_turn(classification(confidence=0.9))])
    result = await classify(component(), make_ctx(provider))
    assert result.review_reasons[:2] == ("missing_evidence", "citation_unverified")
    assert result.confidence <= 0.3


async def test_model_requested_review_is_kept() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(call("search_reference", SEARCH)),
            answer_turn(classification(needs_review=True, reason="photo is blurry")),
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert result.review_reasons == ("model:photo is blurry",)


async def test_run_residual_records_usage_and_trace() -> None:
    store = make_store()
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                tool_calls=(call("search_reference", SEARCH),),
                usage=Usage(input_tokens=1000, output_tokens=10),
            ),
            answer_turn(),
        ]
    )
    outcome = await run_residual(
        component(),
        provider=provider,
        tools=build_tools("study-1", None, None, store),
        allowlist=RESIDUAL_ALLOWLIST,
        budget=Budget(remaining_usd=1.0),
        model="m",
        system_prompt="s",
        prompt_version="classify/v1",
        study_id="study-1",
    )
    # No reference corpus: the search is refused, the answer still arrives.
    assert outcome.classification is not None and not outcome.searched
    assert outcome.tool_calls[0].refused
    assert [c.input_tokens for c in outcome.calls] == [1000, 100]
    assert outcome.iterations == 2


async def test_reasoning_effort_reaches_every_model_call() -> None:
    provider = ScriptedProvider(happy_turns())
    await classify(component(), make_ctx(provider, reasoning_effort="none"))
    assert [r.reasoning_effort for r in provider.requests] == ["none", "none"]
    provider = ScriptedProvider(happy_turns())
    await classify(component(), make_ctx(provider))
    assert [r.reasoning_effort for r in provider.requests] == [None, None]


async def test_replayed_history_is_valid_on_the_openai_and_anthropic_wires() -> None:
    """Regression (2026-10-16): the tool-call turn went back as JSON text, and OpenAI
    answered 400 "messages with role 'tool' must be a response to a preceeding message
    with 'tool_calls'". Each tool result must follow an assistant turn that made the call."""
    from basis.llm.anthropic_provider import _message_dicts
    from basis.llm.providers import _message_dict

    provider = ScriptedProvider(happy_turns())
    await classify(component(), make_ctx(provider))
    history = provider.requests[1].messages

    openai = [_message_dict(m) for m in history]
    assert [m["role"] for m in openai] == ["user", "assistant", "tool"]
    assert openai[1]["tool_calls"][0]["id"] == openai[2]["tool_call_id"] == "call-1"
    assert openai[1]["tool_calls"][0]["function"]["name"] == "search_reference"

    anthropic = _message_dicts(history)
    assert [m["role"] for m in anthropic] == ["user", "assistant", "user"]
    [use] = [b for b in anthropic[1]["content"] if b["type"] == "tool_use"]
    [result] = anthropic[2]["content"]
    assert use["id"] == result["tool_use_id"] == "call-1"
