"""Caps hold against a model that never stops: tool calls, iterations, tokens, budget.

BUILD_PLAN 6.6 ``test_caps``.
"""

from __future__ import annotations

from classifytest_support import answer_turn, call, component, make_ctx, tools_turn

from basis.classify.residual import LoopLimits
from basis.classify.router import classify
from basis.llm.provider import Budget, ScriptedProvider, ScriptedTurn, Usage

SEARCH = {"query": "garage door"}


async def test_tool_call_cap_is_three_per_component() -> None:
    greedy = [
        tools_turn(*(call("search_reference", SEARCH, f"c{t}{i}") for i in range(3)))
        for t in range(5)
    ]
    provider = ScriptedProvider(greedy)
    result = await classify(component(), make_ctx(provider))
    executed = [t for t in result.tool_calls if not t.refused]
    assert len(executed) == 3
    assert all(r.tools == () for r in provider.requests[1:])
    assert result.review_reasons == ("tool_cap_exceeded",)


async def test_iteration_cap_is_four_model_calls() -> None:
    chatty = [ScriptedTurn(text="thinking...") for _ in range(10)]
    provider = ScriptedProvider(chatty)
    result = await classify(component(), make_ctx(provider))
    assert len(provider.requests) == 2  # invalid output, one retry, then stop
    assert result.review_reasons == ("invalid_output",)
    looping = [tools_turn(call("search_reference", SEARCH, f"c{i}")) for i in range(10)]
    provider = ScriptedProvider(looping)
    ctx = make_ctx(provider, limits=LoopLimits(max_tool_calls=99))
    result = await classify(component(), ctx)
    assert len(provider.requests) == 4
    assert result.review_reasons == ("iteration_cap",)


async def test_max_tokens_is_set_on_every_request() -> None:
    provider = ScriptedProvider([tools_turn(call("search_reference", SEARCH)), answer_turn()])
    await classify(component(), make_ctx(provider, limits=LoopLimits(max_tokens=256)))
    assert [r.max_tokens for r in provider.requests] == [256, 256]
    assert all(r.temperature == 0.0 for r in provider.requests)


async def test_budget_stops_a_runaway_loop() -> None:
    # Each call costs (100000 x 0.15 + 0) / 1e6 = $0.015; the budget covers one call.
    expensive = Usage(input_tokens=100_000, output_tokens=0)
    turns = [
        ScriptedTurn(tool_calls=(call("search_reference", SEARCH, f"c{i}"),), usage=expensive)
        for i in range(4)
    ]
    provider = ScriptedProvider(turns)
    budget = Budget(remaining_usd=0.02)
    result = await classify(component(), make_ctx(provider, budget=budget))
    assert result.review_reasons == ("budget_exceeded",)
    assert len(provider.requests) == 2  # the second charge overran; no third call
    assert budget.remaining_usd < 0.005


async def test_timeout_cap_is_sixty_seconds_by_default() -> None:
    assert LoopLimits().timeout_s == 60.0
    assert (LoopLimits().max_tool_calls, LoopLimits().max_iterations) == (3, 4)
    assert LoopLimits().max_json_retries == 1
