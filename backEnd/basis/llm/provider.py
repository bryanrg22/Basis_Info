"""Model-call contract: one request shape, one response shape, one budget.

Every LLM call in Basis builds a :class:`ChatRequest` with a pydantic ``response_model``
(structured output) and goes through a :class:`ChatProvider`. The caller charges the
:class:`Budget` with the returned :class:`Usage`; a call that would exceed the study's
budget raises :class:`BudgetExceededError`, which activities treat as non-retryable.

:class:`ScriptedProvider` replays a prescribed sequence of turns so unit tests and the
behavioral evals (BUILD_PLAN section 6.6) run the real agent loop deterministically with
no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class BudgetExceededError(RuntimeError):
    """The study's LLM budget would be exceeded; the caller must not retry."""


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any]


class ImagePart(BaseModel):
    """An image attached to a user message (vision calls). Already prepared: resized,
    EXIF-stripped and re-encoded by ``basis.vision.prep``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_type: Literal["image/jpeg", "image/png"] = "image/jpeg"
    data_base64: str
    detail: Literal["low", "high", "auto"] = "auto"


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None  # tool name on role="tool"; REQUIRED there (fixes legacy W5)
    tool_call_id: str | None = None
    # Images on a user message (vision); tool calls on an assistant message, replayed so a
    # following role="tool" message answers a call the provider has seen.
    images: tuple[ImagePart, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema; study scope is bound at construction, never here


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ChatRequest(BaseModel):
    """One model call. ``response_model`` makes the call structured; ``tools`` are optional."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    model: str
    system: str
    messages: tuple[Message, ...]
    response_model: type[BaseModel] | None = None
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int = Field(default=2048, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    prompt_version: str
    study_id: str
    activity: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    parsed: BaseModel | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    text: str = ""
    usage: Usage
    model: str
    provider: str
    latency_ms: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    async def complete(self, request: ChatRequest) -> ChatResponse: ...


# --------------------------------------------------------------------------- budget


@dataclass
class Budget:
    """Remaining dollars for one study. ``charge`` is called with real usage after a call;
    ``reserve`` is called before a call with the worst case so a runaway loop stops early."""

    remaining_usd: float
    spent_usd: float = 0.0
    calls: int = 0

    def reserve(self, estimated_usd: float) -> None:
        if estimated_usd > self.remaining_usd:
            raise BudgetExceededError(
                f"call would cost about ${estimated_usd:.4f} but ${self.remaining_usd:.4f} remains"
            )

    def charge(self, cost_usd: float) -> None:
        if cost_usd < 0:
            raise ValueError("cost cannot be negative")
        self.remaining_usd -= cost_usd
        self.spent_usd += cost_usd
        self.calls += 1
        if self.remaining_usd < 0:
            raise BudgetExceededError(
                f"budget exceeded by ${-self.remaining_usd:.4f} after {self.calls} calls"
            )


# --------------------------------------------------------------------------- scripted


@dataclass(frozen=True)
class ScriptedTurn:
    """What the fake model answers on one call: a structured object, tool calls, or text."""

    parsed: BaseModel | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    text: str = ""
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=100, output_tokens=50))
    raise_error: Exception | None = None


class ScriptedProvider:
    """Replays ``turns`` in order and records every request for trace assertions.

    Running out of turns raises ``AssertionError`` so an unexpected extra call fails the
    test instead of looping.
    """

    name = "scripted"

    def __init__(self, turns: Sequence[ScriptedTurn], *, model: str = "scripted-model") -> None:
        self._turns = list(turns)
        self._model = model
        self.requests: list[ChatRequest] = []

    @property
    def remaining_turns(self) -> int:
        return len(self._turns)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError(
                f"scripted provider exhausted after {len(self.requests)} calls "
                f"(last activity: {request.activity})"
            )
        turn = self._turns.pop(0)
        if turn.raise_error is not None:
            raise turn.raise_error
        if request.response_model is not None and turn.parsed is not None:
            if not isinstance(turn.parsed, request.response_model):
                raise AssertionError(
                    f"scripted turn is {type(turn.parsed).__name__}, request expects "
                    f"{request.response_model.__name__}"
                )
        return ChatResponse(
            parsed=turn.parsed,
            tool_calls=turn.tool_calls,
            text=turn.text,
            usage=turn.usage,
            model=self._model,
            provider=self.name,
        )
