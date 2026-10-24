"""Anthropic provider over the official ``anthropic`` SDK (Messages API).

Honours the same :class:`~basis.llm.provider.ChatRequest` -> :class:`ChatResponse` contract
as the OpenAI providers (``basis.llm.providers``), with the same error mapping, so the
vision pipeline, the classifier and the evaluation runners run on Claude unchanged.

Sources (Anthropic docs, read 2026-10-15):

* Vision, https://platform.claude.com/docs/en/build-with-claude/vision: an image is a
  content block ``{"type": "image", "source": {"type": "base64", "media_type": ..., "data":
  ...}}`` on a user message; JPEG, PNG, GIF and WebP; at most 10 MB per image (base64) and
  32 MB per request on the Claude API, 8000 x 8000 px; "Claude works best when images come
  before text", so the image blocks precede the text block here (the OpenAI path sends text
  first; that order is a Chat Completions convention, not a model preference). Cost is
  ``ceil(width / 28) * ceil(height / 28)`` visual tokens: a prepared 1024 x 683 photo is
  37 x 25 = 925 tokens and is not downscaled on any current model.
* Structured outputs, https://platform.claude.com/docs/en/build-with-claude/structured-outputs:
  ``output_config.format = {"type": "json_schema", "schema": ...}`` is GA on every Claude 5
  model with no beta header and is what the docs recommend over forced tool use (which the
  Claude 5.5 and Fable 5.1 models reject with a 400). The reply is valid JSON in the text
  block. ``minimum``/``maximum``, ``minLength``/``maxLength`` and ``maxItems`` are
  unsupported and would be a 400, so the pydantic schema goes through the SDK's
  :func:`anthropic.transform_schema`, which moves those constraints into descriptions;
  pydantic still enforces them when the reply is validated. A ``stop_reason`` of
  ``max_tokens`` or ``refusal`` means the JSON may not match the schema.
* Thinking, https://platform.claude.com/docs/en/build-with-claude/thinking: on Claude 5
  models thinking is always on (adaptive) and cannot be disabled; its depth is steered by
  ``output_config.effort``: ``ChatRequest.reasoning_effort`` when the caller set one (the
  same field the OpenAI reasoning models take), else ``settings.anthropic_effort`` (default
  ``low``: these are perception and extraction calls, and the OpenAI baseline does not
  reason; the API's own defaults are ``high`` on Fable 5.1 and ``medium`` on Opus 5.5).
  Thinking tokens are billed as output tokens and count toward ``max_tokens``, so the wire
  ``max_tokens`` is :func:`basis.llm.cost.output_ceiling` (the request's answer budget plus
  a thinking allowance); the budget estimate uses the same number. ``temperature``/``top_p``/
  ``top_k`` are rejected by these models and are not sent. No ``thinking`` parameter is
  sent, so older models (4.x) run without thinking, as they do by default.
* Errors, https://platform.claude.com/docs/en/api/errors: 429 ``rate_limit_error``, 500
  ``api_error``, 504 ``timeout_error``, 529 ``overloaded_error``; 413 ``request_too_large``.
  Mapped like the OpenAI providers: 429 -> :class:`ProviderRateLimited`, 5xx (529 included),
  408/409, timeouts and connection failures -> :class:`ProviderUnavailable`, unusable output
  -> :class:`ProviderBadResponse`; any other 4xx refused the request (:func:`rejection`):
  413 and an image the API cannot read -> :class:`ProviderRequestRejected` (this input), 404
  ``not_found_error`` (an unknown model) and every other ``invalid_request_error`` ->
  :class:`ProviderConfigError` (the request as configured; it stops the study like an
  account error). An effort level Claude does not know and a model this provider does not
  serve are configuration errors too, raised before anything is sent. The SDK's own
  retries are off; the provider retries once on 429/5xx (``retry-after`` honoured up to a
  cap), the pool and Temporal do the rest.
  Account errors come first and are :class:`ProviderAccountError` (never retried, never
  sent elsewhere): 401 ``authentication_error``, 402 ``billing_error``, 403
  ``permission_error``; a 400 whose message says the credit balance is too low (what the
  API returned when the account ran dry, EVAL_REPORT section 26.12) or that a spend limit
  set in the Console was reached ("You have reached your specified ... API usage limits",
  https://platform.claude.com/docs/en/api/rate-limits); and the tier spend-cap 429, which
  carries ``error.details.error_code`` ``enforced_spend_limit_reached`` and no
  ``retry-after`` and "keeps failing until access resumes".
* Prices, https://platform.claude.com/docs/en/about-claude/pricing: rows in
  :mod:`basis.llm.cost`.

Not sent on purpose: the server-side ``fallbacks`` parameter (a refusal would be answered by
a different model, and every run here must stay on the model it names; a refusal is a
:class:`ProviderBadResponse` the caller handles as any unusable answer), prompt caching
(every photo call has a different image), and ``thinking.display`` (the summary is not
needed; omitted is the default). ``ImagePart.detail`` has no equivalent and is ignored.

Tool round-trips are mapped (``tool_use``/``tool_result`` blocks, consecutive tool results
in one user message, as the API requires) but the contract has no place for the thinking
blocks a Claude 5 model emits before a tool call, which the API wants replayed; the tool
loops (``basis.classify``) are not exercised on Claude yet and would need that first.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

import anthropic
from pydantic import BaseModel, ValidationError

from basis.config import Settings
from basis.llm.cost import is_anthropic_model, output_ceiling
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderConfigError,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
    classify_rejection,
    is_retryable_status,
)
from basis.llm.provider import ChatRequest, ChatResponse, Message, ToolCall, Usage

logger = logging.getLogger("basis.llm.anthropic")

PROVIDER_NAME: Final = "anthropic"
RETRY_AFTER_CAP_S: Final = 10.0
MALFORMED_ARGUMENTS: Final = "_malformed_arguments"
EFFORT_LEVELS: Final = ("low", "medium", "high", "xhigh", "max")
ACCOUNT_STATUSES: Final = frozenset({401, 402, 403})
ACCOUNT_TYPES: Final = frozenset({"authentication_error", "billing_error", "permission_error"})
SPEND_CAP_CODE: Final = "enforced_spend_limit_reached"
# 400 invalid_request_error messages that are about the account, not the request.
_ACCOUNT_400 = re.compile(
    r"credit balance is too low|reached your specified (?:workspace )?API usage limits",
    re.IGNORECASE,
)

Sleep = Callable[[float], Awaitable[None]]

__all__ = [
    "AnthropicProvider",
    "EFFORT_LEVELS",
    "build_request_body",
    "is_anthropic_model",
    "account_error",
    "map_error",
    "output_schema",
    "parse_message",
    "rejection",
]


def default_jitter() -> float:
    """Seconds to wait before the single retry when the server gave no ``retry-after``."""
    return random.uniform(0.5, 2.0)  # noqa: S311 - jitter, not cryptography


# --------------------------------------------------------------------------- schema


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of ``model`` as the structured-outputs grammar accepts it.

    :func:`anthropic.transform_schema` sets ``additionalProperties: false`` on every object,
    keeps ``required`` as pydantic declared it (optional fields stay optional), and moves the
    unsupported constraints (``minimum``/``maximum``, ``minLength``/``maxLength``,
    ``maxItems``, ``default``) into the field descriptions. ``$defs``/``$ref`` stay (they are
    supported).
    """
    schema: dict[str, Any] = anthropic.transform_schema(model)
    return schema


# --------------------------------------------------------------------------- request


def _image_block(image: Any) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.media_type,
            "data": image.data_base64,
        },
    }


def _message_dicts(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            raise ValueError("system text goes in ChatRequest.system, not in a message")
        if message.role == "tool":
            if not message.name or not message.tool_call_id:
                raise ValueError("a tool message needs both name and tool_call_id (legacy W5)")
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            # Every tool_result that answers one assistant turn must share one user message.
            if out and out[-1]["role"] == "user" and out[-1].get("_tool_results"):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block], "_tool_results": True})
            continue
        if message.role == "assistant" and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            content += [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            ]
            out.append({"role": "assistant", "content": content})
            continue
        if message.images:
            if message.role != "user":
                raise ValueError("images are only allowed on user messages")
            parts = [_image_block(image) for image in message.images]
            parts.append({"type": "text", "text": message.content})
            out.append({"role": "user", "content": parts})
            continue
        out.append({"role": message.role, "content": message.content})
    for item in out:
        item.pop("_tool_results", None)
    return out


def build_request_body(request: ChatRequest, *, effort: str = "low") -> dict[str, Any]:
    """The ``messages.create`` keyword arguments for ``request``.

    ``effort`` is the provider's default (``ANTHROPIC_EFFORT``); a ``request.reasoning_effort``
    overrides it. An effort Claude does not know is a :class:`ProviderConfigError` (the API
    would answer 400 to every request; nothing is sent, so there is no status).
    """
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {EFFORT_LEVELS}, not {effort!r}")
    if request.reasoning_effort is not None:
        if request.reasoning_effort not in EFFORT_LEVELS:
            raise ProviderConfigError(
                f"reasoning_effort {request.reasoning_effort!r} is not a Claude effort level "
                f"{EFFORT_LEVELS}",
                provider=PROVIDER_NAME,
            )
        effort = request.reasoning_effort
    body: dict[str, Any] = {
        "model": request.model,
        "max_tokens": output_ceiling(request.model, request.max_tokens),
        "system": request.system,
        "messages": _message_dicts(request.messages),
        "output_config": {"effort": effort},
    }
    if request.tools:
        body["tools"] = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
            for tool in request.tools
        ]
    if request.response_model is not None:
        body["output_config"]["format"] = {
            "type": "json_schema",
            "schema": output_schema(request.response_model),
        }
    return body


# --------------------------------------------------------------------------- response


def _usage(message: Any) -> Usage:
    usage = getattr(message, "usage", None)
    if usage is None:
        logger.warning("message has no usage; recording zero tokens")
        return Usage(input_tokens=0, output_tokens=0)
    # Cache tokens are billed at other rates, but nothing here asks for caching; counting
    # them as plain input keeps the number conservative if the API ever reports some.
    input_tokens = (
        int(getattr(usage, "input_tokens", 0) or 0)
        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    )
    # output_tokens includes the thinking tokens (billed as output).
    return Usage(input_tokens=input_tokens, output_tokens=int(usage.output_tokens or 0))


def _tool_calls(content: list[Any]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        arguments = block.input
        if not isinstance(arguments, dict):
            # Passed through so the tool loop can answer with an error message.
            arguments = {MALFORMED_ARGUMENTS: arguments}
        calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))
    return tuple(calls)


def parse_message(
    message: Any, request: ChatRequest, *, provider: str, latency_ms: int
) -> ChatResponse:
    """Turn an SDK ``Message`` into a :class:`ChatResponse` (or a bad-response error)."""
    usage = _usage(message)
    model = str(getattr(message, "model", None) or request.model)
    raw: dict[str, Any] = message.model_dump(mode="json")

    def bad(text: str) -> ProviderBadResponse:
        return ProviderBadResponse(text, provider=provider, usage=usage, model=model, raw=raw)

    content = list(message.content or ())
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) or "unspecified"
        explanation = getattr(details, "explanation", None) or ""
        raise bad(f"model refused ({category}): {explanation}".rstrip(": "))
    tool_calls = _tool_calls(content)
    text = "".join(block.text for block in content if getattr(block, "type", None) == "text")
    parsed: BaseModel | None = None
    if request.response_model is not None and not tool_calls:
        if stop_reason == "max_tokens":
            raise bad("output truncated at max_tokens")
        if stop_reason == "model_context_window_exceeded":
            raise bad("output truncated at the context window")
        try:
            parsed = request.response_model.model_validate_json(text)
        except ValidationError as exc:
            raise bad(f"output does not match {request.response_model.__name__}: {exc}") from exc
    return ChatResponse(
        parsed=parsed,
        tool_calls=tool_calls,
        text=text,
        usage=usage,
        model=model,
        provider=provider,
        latency_ms=latency_ms,
        raw=raw,
    )


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if header is None:
        return None
    try:
        return max(0.0, min(float(header), RETRY_AFTER_CAP_S))
    except ValueError:
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def account_error(exc: Exception, provider: str = PROVIDER_NAME) -> ProviderAccountError | None:
    """The :class:`ProviderAccountError` an Anthropic status error means, if any.

    ``exc.body`` is the whole response (``{"type": "error", "error": {"type", "message",
    "details"?}}``) and ``exc.type`` the SDK's parse of ``error.type``.
    """
    if not isinstance(exc, anthropic.APIStatusError):
        return None
    body = exc.body if isinstance(exc.body, dict) else {}
    error = _dict(body.get("error"))
    message = str(error.get("message") or exc.message or f"HTTP {exc.status_code}")
    details = _dict(error.get("details"))
    kind = exc.type or error.get("type")
    status = exc.status_code
    if status in ACCOUNT_STATUSES or kind in ACCOUNT_TYPES:
        code = kind
    elif status == 400 and _ACCOUNT_400.search(message):
        code = kind or "invalid_request_error"
    elif status == 429 and details.get("error_code") == SPEND_CAP_CODE:
        code = SPEND_CAP_CODE
    else:
        return None
    return ProviderAccountError(message, provider=provider, status=status, code=code)


def rejection(exc: anthropic.APIStatusError, provider: str = PROVIDER_NAME) -> ProviderError:
    """Any other 4xx, by :func:`basis.llm.errors.classify_rejection` on ``error.type`` and
    the message: 413 ``request_too_large`` and an image the API cannot read ("Could not
    process image", an image over the size or dimension limit, a media type that does not
    match) are the input's; 404 ``not_found_error`` (an unknown model) and every other
    ``invalid_request_error`` are the configuration's."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = _dict(body.get("error"))
    message = str(error.get("message") or exc.message or f"HTTP {exc.status_code}")
    codes = [exc.type or error.get("type")]
    return classify_rejection(exc.status_code, codes, message, provider=provider)


def map_error(exc: Exception, provider: str = PROVIDER_NAME) -> ProviderError:
    """An SDK exception as a :mod:`basis.llm.errors` error (account errors first: a
    credit-balance 400 is not a bad request, a spend-cap 429 is not a rate limit; then any
    other 4xx is the configuration's or the input's, :func:`rejection`)."""
    if isinstance(exc, ProviderError):
        return exc
    account = account_error(exc, provider)
    if account is not None:
        return account
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderRateLimited(str(exc), provider=provider, status=429)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:  # 500 api_error, 504 timeout_error, 529 overloaded_error
            return ProviderUnavailable(str(exc), provider=provider, status=exc.status_code)
        return rejection(exc, provider)
    if isinstance(exc, anthropic.APITimeoutError | asyncio.TimeoutError):
        return ProviderUnavailable(f"timed out: {exc}", provider=provider)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderUnavailable(f"connection failed: {exc}", provider=provider)
    return ProviderUnavailable(f"{type(exc).__name__}: {exc}", provider=provider)


# --------------------------------------------------------------------------- provider


class AnthropicProvider:
    """Claude models through ``anthropic.AsyncAnthropic``; serves ``claude-*`` requests only."""

    name = PROVIDER_NAME

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        timeout_s: float,
        effort: str = "low",
        jitter: Callable[[], float] = default_jitter,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {EFFORT_LEVELS}, not {effort!r}")
        self._client = client
        self._timeout_s = timeout_s
        self.effort = effort
        self._jitter = jitter
        self._sleep = sleep
        self._clock = clock

    @staticmethod
    def accepts(model: str) -> bool:
        return is_anthropic_model(model)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if not self.accepts(request.model):
            raise ProviderNotConfigured(
                f"{self.name} does not serve model {request.model!r}", provider=self.name
            )
        body = build_request_body(request, effort=self.effort)
        for attempt in (1, 2):
            started = self._clock()
            try:
                message = await self._client.messages.create(**body, timeout=self._timeout_s)
            except Exception as exc:  # mapped below; nothing is swallowed
                error = map_error(exc, self.name)
                if attempt == 1 and is_retryable_status(error):
                    delay = _retry_after(exc) if isinstance(exc, anthropic.APIStatusError) else None
                    wait = self._jitter() if delay is None else delay
                    logger.warning(
                        "%s returned %s; retrying once in %.2fs", self.name, error.status, wait
                    )
                    await self._sleep(wait)
                    continue
                raise error from exc
            latency_ms = max(0, int((self._clock() - started) * 1000))
            return parse_message(message, request, provider=self.name, latency_ms=latency_ms)
        raise AssertionError("unreachable")  # pragma: no cover

    @classmethod
    def from_settings(
        cls, settings: Settings, *, http_client: Any | None = None, **kwargs: Any
    ) -> AnthropicProvider | None:
        """``http_client`` (an ``httpx2.AsyncClient``) is for tests' mock transports."""
        key = settings.anthropic_api_key
        if key is None or not key.get_secret_value().strip():
            return None
        client = anthropic.AsyncAnthropic(
            api_key=key.get_secret_value(),
            max_retries=0,
            timeout=settings.llm_request_timeout_seconds,
            http_client=http_client,
        )
        return cls(
            client,
            timeout_s=settings.llm_request_timeout_seconds,
            effort=settings.anthropic_effort,
            **kwargs,
        )
