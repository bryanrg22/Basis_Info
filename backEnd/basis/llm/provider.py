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
    # What the image is, for the ledger's request payload (never sent to the provider): the
    # photo's document id, a page number, a crop name. The payload replaces the bytes with
    # this, the media type, the byte size and the sha256.
    ref: str | None = None


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
    """Tokens of one call, as the provider reported them, in the OpenTelemetry GenAI shape
    (``gen_ai.usage.*``): ``input_tokens`` is the whole prompt, cached tokens included;
    ``output_tokens`` the whole answer, reasoning included; the other three are the parts of
    those totals billed at their own rates (``cache_read``, ``cache_creation`` and
    ``reasoning`` are each "already included in" the total they belong to). A provider that
    does not report a part leaves it 0. :func:`basis.llm.cost.cost_decimal` prices each part.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)

    @property
    def uncached_input_tokens(self) -> int:
        """The input tokens billed at the base rate (never below 0 if a provider's totals
        and parts disagree)."""
        return max(
            0, self.input_tokens - self.cache_read_input_tokens - self.cache_creation_input_tokens
        )


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
    # Reasoning models only (``none``/``minimal``/``low``/``medium``/``high``...; which values a
    # model accepts is per model). ``None`` leaves the provider's default. Ignored elsewhere.
    reasoning_effort: str | None = None
    prompt_version: str
    study_id: str
    activity: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    parsed: BaseModel | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    text: str = ""
    usage: Usage
    # The model the provider says answered (``gen_ai.response.model``: a dated snapshot on
    # OpenAI), its name, and the HTTP round trip of the answering attempt.
    model: str
    provider: str
    latency_ms: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)
    # The provider's own identifiers of the exchange (``gen_ai.response.id``; the request id
    # from the response headers when the SDK exposes it), why it stopped
    # (``gen_ai.response.finish_reasons``), where it was sent (``server.address``, the base
    # URL or Azure endpoint) and the parameters as sent on the wire (``gen_ai.request.*``:
    # the output cap, effort, temperature, format, tool names; never the content), for the
    # ledger. A provider that cannot say leaves them empty.
    response_id: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    endpoint: str | None = None
    request_params: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    async def complete(self, request: ChatRequest) -> ChatResponse: ...


def request_params(
    request: ChatRequest,
    *,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """The request's parameters as the ledger records them (``gen_ai.request.*``): what a
    provider sent on the wire, never the content. ``max_output_tokens``, ``reasoning_effort``
    and ``temperature`` are the values as sent when the provider changed or dropped them
    (Claude's thinking headroom, the provider's default effort, no temperature on reasoning
    models); unset, the request's own values stand. Images are counted and sized (the
    decoded bytes), tools and the response schema named.
    """
    images = [image for message in request.messages for image in message.images]
    return {
        "max_output_tokens": (
            request.max_tokens if max_output_tokens is None else max_output_tokens
        ),
        "answer_budget_tokens": request.max_tokens,
        "reasoning_effort": (
            request.reasoning_effort if reasoning_effort is None else reasoning_effort
        ),
        "temperature": temperature,
        "response_format": (
            None if request.response_model is None else request.response_model.__name__
        ),
        "tool_names": [tool.name for tool in request.tools],
        "image_count": len(images),
        "image_bytes": sum(_decoded_size(image.data_base64) for image in images),
        "message_count": len(request.messages),
        "prompt_chars": len(request.system) + sum(len(m.content) for m in request.messages),
    }


def _decoded_size(data_base64: str) -> int:
    """Bytes of a base64 string without decoding it (padding accounted for)."""
    stripped = data_base64.rstrip("=")
    return (len(stripped) * 3) // 4


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
            response_id=f"scripted-{len(self.requests)}",
            finish_reason="tool_use" if turn.tool_calls else "stop",
            request_params=request_params(request),
        )
