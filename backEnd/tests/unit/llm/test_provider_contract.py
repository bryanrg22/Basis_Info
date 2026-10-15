"""The model-call contract: budget arithmetic and the scripted provider's guarantees."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from basis.llm import (
    Budget,
    BudgetExceededError,
    ChatProvider,
    ChatRequest,
    Message,
    ScriptedProvider,
    ScriptedTurn,
    ToolCall,
    Usage,
)


class Answer(BaseModel):
    bucket: str


class Other(BaseModel):
    x: int


def request(**overrides: object) -> ChatRequest:
    base: dict[str, object] = {
        "model": "m",
        "system": "s",
        "messages": (Message(role="user", content="hi"),),
        "prompt_version": "v1",
        "study_id": "study-1",
        "activity": "classify",
    }
    base.update(overrides)
    return ChatRequest(**base)  # type: ignore[arg-type]


def test_budget_reserve_and_charge() -> None:
    b = Budget(remaining_usd=1.0)
    b.reserve(0.5)
    b.charge(0.4)
    assert (round(b.remaining_usd, 6), round(b.spent_usd, 6), b.calls) == (0.6, 0.4, 1)
    with pytest.raises(BudgetExceededError):
        b.reserve(0.7)
    with pytest.raises(BudgetExceededError):
        b.charge(0.7)
    with pytest.raises(ValueError):
        b.charge(-1)


async def test_scripted_provider_replays_in_order_and_records_requests() -> None:
    p = ScriptedProvider(
        [
            ScriptedTurn(
                tool_calls=(ToolCall(id="c1", name="search_reference", arguments={"q": "carpet"}),)
            ),
            ScriptedTurn(parsed=Answer(bucket="y5"), usage=Usage(input_tokens=10, output_tokens=5)),
        ]
    )
    assert isinstance(p, ChatProvider)
    first = await p.complete(request())
    assert first.tool_calls[0].name == "search_reference" and first.parsed is None
    second = await p.complete(request(response_model=Answer))
    assert isinstance(second.parsed, Answer) and second.usage.output_tokens == 5
    assert [r.activity for r in p.requests] == ["classify", "classify"]
    assert p.remaining_turns == 0


async def test_scripted_provider_fails_loudly_when_exhausted() -> None:
    p = ScriptedProvider([])
    with pytest.raises(AssertionError, match="exhausted"):
        await p.complete(request())


async def test_scripted_provider_checks_response_model() -> None:
    p = ScriptedProvider([ScriptedTurn(parsed=Other(x=1))])
    with pytest.raises(AssertionError, match="expects Answer"):
        await p.complete(request(response_model=Answer))


async def test_scripted_provider_raises_configured_error() -> None:
    p = ScriptedProvider([ScriptedTurn(raise_error=TimeoutError("slow"))])
    with pytest.raises(TimeoutError):
        await p.complete(request())


def test_tool_message_requires_name_by_convention() -> None:
    """Tool results carry the tool name (the legacy citation bug W5 came from omitting it)."""
    m = Message(role="tool", content="[]", name="search_reference", tool_call_id="c1")
    assert m.name == "search_reference"
