"""OpenAI and Azure OpenAI providers over the official ``openai`` SDK (Chat Completions).

What every call does (S3_SPEC sections 0 and 2):

* Structured output: a request with ``response_model`` sends
  ``response_format={"type": "json_schema", "json_schema": {..., "strict": true}}`` built
  from the pydantic model by :func:`strict_json_schema`, and the reply is validated with
  ``response_model.model_validate_json``. Invalid JSON, a refusal or truncation is a
  :class:`ProviderBadResponse` carrying the usage (the tokens were spent).
* Tools: ``ToolSpec`` -> ``{"type": "function", ...}``; tool results go back as
  ``role="tool"`` messages that must carry ``tool_call_id`` and ``name`` (legacy W5); the
  assistant turn that made the calls is replayed with its ``tool_calls``.
* Retries: the SDK's own retries are off (``max_retries=0``). The provider retries once on
  HTTP 429 and 5xx after a jittered delay (``Retry-After`` honoured up to a cap); timeouts
  and connection errors are not retried here (a second 60 s wait would eat the activity's
  budget; Temporal retries the activity instead). Every failure is mapped to
  :mod:`basis.llm.errors` so no caller imports the SDK. An account refusal (401, 403, or
  the 429 ``insufficient_quota``, which waiting does not clear) is
  :class:`ProviderAccountError` and is not retried.
* Timeouts come from ``settings.llm_request_timeout_seconds``; latency is measured around
  the HTTP call and returned in ``ChatResponse.latency_ms``.

Token limits: reasoning models (``o1``/``o3``/``o4``, ``gpt-5*`` and later families) take
``max_completion_tokens`` and only the default temperature; other models take
``max_tokens`` and ``temperature``. Azure OpenAI needs api-version 2024-08-01-preview or
later for strict JSON-schema output (Microsoft's structured-outputs guide); older versions
are refused when the provider is built so the failure is a clear startup error, not a 400
on every call.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Any, Final

import openai
from pydantic import BaseModel, ValidationError

from basis.config import ConfigError, Settings
from basis.llm.anthropic_provider import AnthropicProvider, is_anthropic_model
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
    is_retryable_status,
)
from basis.llm.families import is_reasoning_model as is_reasoning_model  # re-exported
from basis.llm.provider import ChatProvider, ChatRequest, ChatResponse, Message, ToolCall, Usage

logger = logging.getLogger("basis.llm.providers")

MIN_AZURE_API_VERSION: Final = date(2024, 8, 1)
RETRY_AFTER_CAP_S: Final = 10.0
ACCOUNT_STATUSES: Final = frozenset({401, 403})
ACCOUNT_ERROR_CODES: Final = frozenset({"insufficient_quota", "billing_hard_limit_reached"})
MALFORMED_ARGUMENTS: Final = "_malformed_arguments"
# Dropped from strict schemas (pydantic still enforces them when the reply is validated).
UNSUPPORTED_KEYWORDS: Final = ("default", "minLength", "maxLength", "uniqueItems")

Sleep = Callable[[float], Awaitable[None]]


def default_jitter() -> float:
    """Seconds to wait before the single retry when the server gave no ``Retry-After``."""
    return random.uniform(0.5, 2.0)  # noqa: S311 - jitter, not cryptography


# --------------------------------------------------------------------------- schema


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of ``model`` in the form OpenAI's strict structured outputs accept.

    Mirrors the SDK's own conversion: every object gets ``additionalProperties: false``
    and lists all its properties as ``required`` (optional fields stay nullable through
    their ``anyOf``), ``default`` values and string-length keywords are dropped (every key
    is required anyway; the length limits are enforced when the reply is validated), and a
    ``$ref`` that has sibling keys is inlined because strict mode rejects that shape.
    """
    root = model.model_json_schema()
    defs: dict[str, Any] = root.get("$defs", {})

    def resolve(ref: str) -> dict[str, Any]:
        name = ref.rsplit("/", 1)[-1]
        if not ref.startswith("#/$defs/") or name not in defs:
            raise ValueError(f"unsupported $ref {ref!r} in {model.__name__}")
        resolved: dict[str, Any] = copy.deepcopy(defs[name])
        return resolved

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        node = dict(node)
        if "$ref" in node and len(node) > 1:
            merged = resolve(node.pop("$ref"))
            merged.update(node)
            node = merged
        for keyword in UNSUPPORTED_KEYWORDS:
            node.pop(keyword, None)
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties", {})
            node["properties"] = {name: walk(value) for name, value in properties.items()}
            node["required"] = list(properties)
            node["additionalProperties"] = False
        for key, value in list(node.items()):
            if key in ("properties",):
                continue
            if isinstance(value, dict | list):
                node[key] = (
                    {name: walk(sub) for name, sub in value.items()}
                    if key == "$defs" and isinstance(value, dict)
                    else walk(value)
                )
        return node

    result: dict[str, Any] = walk(root)
    return result


# --------------------------------------------------------------------------- request


def _message_dict(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        if not message.name or not message.tool_call_id:
            raise ValueError("a tool message needs both name and tool_call_id (legacy W5)")
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ],
        }
    if message.images:
        if message.role != "user":
            raise ValueError("images are only allowed on user messages")
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.media_type};base64,{image.data_base64}",
                        "detail": image.detail,
                    },
                }
            )
        return {"role": "user", "content": parts}
    return {"role": message.role, "content": message.content}


def build_request_body(request: ChatRequest, *, model: str | None = None) -> dict[str, Any]:
    """The ``chat.completions.create`` keyword arguments for ``request``.

    ``model`` overrides the request's model name (an Azure deployment name).
    """
    target = model or request.model
    body: dict[str, Any] = {
        "model": target,
        "messages": [{"role": "system", "content": request.system}]
        + [_message_dict(message) for message in request.messages],
    }
    if is_reasoning_model(request.model) or is_reasoning_model(target):
        body["max_completion_tokens"] = request.max_tokens
        if request.reasoning_effort is not None:
            body["reasoning_effort"] = request.reasoning_effort
    else:
        body["max_tokens"] = request.max_tokens
        body["temperature"] = request.temperature
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    if request.response_model is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.response_model.__name__,
                "schema": strict_json_schema(request.response_model),
                "strict": True,
            },
        }
    return body


# --------------------------------------------------------------------------- response


def _usage(completion: Any) -> Usage:
    usage = getattr(completion, "usage", None)
    if usage is None:
        logger.warning("completion has no usage; recording zero tokens")
        return Usage(input_tokens=0, output_tokens=0)
    return Usage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _tool_calls(message: Any) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or ():
        function = getattr(call, "function", None)
        if function is None:
            continue
        raw_args = function.arguments or "{}"
        try:
            arguments = json.loads(raw_args)
            if not isinstance(arguments, dict):
                raise ValueError("arguments are not an object")
        except (ValueError, TypeError):
            # Passed through so the tool loop can answer with an error message.
            arguments = {MALFORMED_ARGUMENTS: raw_args}
        calls.append(ToolCall(id=call.id, name=function.name, arguments=arguments))
    return tuple(calls)


def parse_completion(
    completion: Any, request: ChatRequest, *, provider: str, latency_ms: int
) -> ChatResponse:
    """Turn an SDK ``ChatCompletion`` into a :class:`ChatResponse` (or a bad-response error)."""
    usage = _usage(completion)
    model = str(getattr(completion, "model", None) or request.model)
    raw: dict[str, Any] = completion.model_dump(mode="json")

    def bad(message: str) -> ProviderBadResponse:
        return ProviderBadResponse(message, provider=provider, usage=usage, model=model, raw=raw)

    if not completion.choices:
        raise bad("completion has no choices")
    choice = completion.choices[0]
    message = choice.message
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise bad(f"model refused: {refusal}")
    tool_calls = _tool_calls(message)
    text = message.content or ""
    parsed: BaseModel | None = None
    if request.response_model is not None and not tool_calls:
        if choice.finish_reason == "length":
            raise bad("output truncated at max_tokens")
        if choice.finish_reason == "content_filter":
            raise bad("output blocked by the content filter")
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


def _retry_after(exc: openai.APIStatusError) -> float | None:
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if header is None:
        return None
    try:
        return max(0.0, min(float(header), RETRY_AFTER_CAP_S))
    except ValueError:
        return None


def is_account_error(exc: openai.APIStatusError) -> bool:
    """401 (bad key), 403 (no access to the model, project or region), or the 429 whose
    ``code`` (and ``type``) is ``insufficient_quota`` ("You exceeded your current quota"):
    unlike a rate limit it does not clear by waiting, so it must not be retried as one; and
    the ``billing_hard_limit_reached`` code of an account past its billing limit."""
    return (
        exc.status_code in ACCOUNT_STATUSES
        or exc.code in ACCOUNT_ERROR_CODES
        or exc.type in ACCOUNT_ERROR_CODES
    )


def map_error(exc: Exception, provider: str) -> ProviderError:
    """An SDK exception as a :mod:`basis.llm.errors` error."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, openai.APIStatusError) and is_account_error(exc):
        return ProviderAccountError(
            f"{provider} rejected the account (HTTP {exc.status_code}; check the API key, "
            f"its permissions and the quota): {exc}",
            provider=provider,
            status=exc.status_code,
        )
    if isinstance(exc, openai.RateLimitError):
        return ProviderRateLimited(str(exc), provider=provider, status=429)
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return ProviderUnavailable(str(exc), provider=provider, status=exc.status_code)
        return ProviderBadResponse(str(exc), provider=provider, status=exc.status_code)
    if isinstance(exc, openai.APITimeoutError | asyncio.TimeoutError):
        return ProviderUnavailable(f"timed out: {exc}", provider=provider)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderUnavailable(f"connection failed: {exc}", provider=provider)
    return ProviderUnavailable(f"{type(exc).__name__}: {exc}", provider=provider)


# --------------------------------------------------------------------------- providers


class OpenAICompatibleProvider:
    """Shared body of the OpenAI and Azure OpenAI providers."""

    name = "openai"

    @staticmethod
    def accepts(model: str) -> bool:
        """Every model name but Claude's (those belong to :class:`AnthropicProvider`)."""
        return not is_anthropic_model(model)

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        timeout_s: float,
        model_map: Mapping[str, str] | None = None,
        default_model: str | None = None,
        jitter: Callable[[], float] = default_jitter,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._client = client
        self._timeout_s = timeout_s
        self._model_map = dict(model_map or {})
        self._default_model = default_model
        self._jitter = jitter
        self._sleep = sleep
        self._clock = clock

    def target_model(self, model: str) -> str:
        """The model (OpenAI) or deployment (Azure) a request for ``model`` is sent to."""
        return self._model_map.get(model) or self._default_model or model

    async def complete(self, request: ChatRequest) -> ChatResponse:
        body = build_request_body(request, model=self.target_model(request.model))
        for attempt in (1, 2):
            started = self._clock()
            try:
                completion = await self._client.chat.completions.create(
                    **body, timeout=self._timeout_s
                )
            except Exception as exc:  # mapped below; nothing is swallowed
                error = map_error(exc, self.name)
                if attempt == 1 and is_retryable_status(error):
                    delay = _retry_after(exc) if isinstance(exc, openai.APIStatusError) else None
                    wait = self._jitter() if delay is None else delay
                    logger.warning(
                        "%s returned %s; retrying once in %.2fs", self.name, error.status, wait
                    )
                    await self._sleep(wait)
                    continue
                raise error from exc
            latency_ms = max(0, int((self._clock() - started) * 1000))
            return parse_completion(completion, request, provider=self.name, latency_ms=latency_ms)
        raise AssertionError("unreachable")  # pragma: no cover


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"

    @classmethod
    def from_settings(
        cls, settings: Settings, *, http_client: Any | None = None, **kwargs: Any
    ) -> OpenAIProvider | None:
        """``http_client`` (an ``httpx2.AsyncClient``) is for tests' mock transports."""
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
            return None
        client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            max_retries=0,
            timeout=settings.llm_request_timeout_seconds,
            http_client=http_client,
        )
        return cls(client, timeout_s=settings.llm_request_timeout_seconds, **kwargs)


def check_azure_api_version(version: str) -> None:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", version.strip())
    if match is None:
        raise ConfigError(f"AZURE_OPENAI_API_VERSION {version!r} is not a dated version")
    year, month, day = (int(part) for part in match.groups())
    if date(year, month, day) < MIN_AZURE_API_VERSION:
        raise ConfigError(
            f"AZURE_OPENAI_API_VERSION {version!r} predates structured outputs; "
            "use 2024-08-01-preview or later (default 2024-10-21)"
        )


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Azure OpenAI: requests name a logical model; ``model_map`` maps it to a deployment."""

    name = "azure"

    @classmethod
    def from_settings(
        cls, settings: Settings, *, http_client: Any | None = None, **kwargs: Any
    ) -> AzureOpenAIProvider | None:
        key = settings.azure_openai_api_key
        if not settings.azure_openai_endpoint or key is None or not key.get_secret_value():
            return None
        default = settings.azure_openai_deployment_name
        vision = settings.azure_openai_vision_deployment or default
        text = settings.azure_openai_text_deployment or default
        if not (vision or text):
            return None
        check_azure_api_version(settings.azure_openai_api_version)
        model_map = {
            name: deployment
            for name, deployment in ((settings.vision_model, vision), (settings.text_model, text))
            if deployment
        }
        client = openai.AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            max_retries=0,
            timeout=settings.llm_request_timeout_seconds,
            http_client=http_client,
        )
        return cls(
            client,
            timeout_s=settings.llm_request_timeout_seconds,
            model_map=model_map,
            default_model=default,
            **kwargs,
        )


def providers_from_settings(settings: Settings) -> list[ChatProvider]:
    """The configured providers, ``settings.llm_primary`` first (empty when none).

    The pool routes each request to the providers that serve its model (``claude-*`` to
    Anthropic, everything else to Azure/OpenAI), so the primary only matters among those.
    """
    azure = AzureOpenAIProvider.from_settings(settings)
    openai_provider = OpenAIProvider.from_settings(settings)
    claude = AnthropicProvider.from_settings(settings)
    configured: list[ChatProvider] = [p for p in (azure, openai_provider, claude) if p]
    configured.sort(key=lambda p: p.name != settings.llm_primary)
    return configured


def require_providers(settings: Settings) -> list[ChatProvider]:
    providers = providers_from_settings(settings)
    if not providers:
        raise ProviderNotConfigured(
            "no model provider configured: set OPENAI_API_KEY, ANTHROPIC_API_KEY, or "
            "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and a deployment name"
        )
    return providers
