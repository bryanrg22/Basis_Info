"""Anthropic provider over the real ``anthropic`` SDK with a mock HTTP transport.

No request leaves the process: the SDK (``anthropic`` 1.x, built on ``httpx2``) gets an
``httpx2.AsyncClient`` whose ``MockTransport`` answers from a queue, so the tests exercise
the SDK's own request serialisation, status handling and error classes, exactly as
``test_providers.py`` does for OpenAI.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field, SecretStr

from basis.config import Settings
from basis.llm.anthropic_provider import (
    EFFORT_LEVELS,
    MALFORMED_ARGUMENTS,
    AnthropicProvider,
    build_request_body,
    is_anthropic_model,
    map_error,
    output_schema,
    parse_message,
)
from basis.llm.cost import (
    CLAUDE_IMAGE_TOKENS,
    IMAGE_TOKENS,
    PRICES,
    THINKING_HEADROOM_TOKENS,
    cost_usd,
    estimate_usd,
    output_ceiling,
    price_for,
)
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from basis.llm.provider import ChatRequest, ImagePart, Message, ToolCall, ToolSpec, Usage
from basis.llm.providers import providers_from_settings
from basis.vision.schemas import ModelPhotoAnalysis

BASE = "https://api.test"
URL = f"{BASE}/v1/messages"
MODEL = "claude-opus-5-5"


class Answer(BaseModel):
    bucket: str
    life: float = Field(ge=0)
    note: str | None = None


class Nested(BaseModel):
    answers: list[Answer] = Field(max_length=10)
    label: str = Field(default="x", max_length=10)


def request(**changes: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "model": MODEL,
        "system": "be exact",
        "messages": (Message(role="user", content="classify carpet"),),
        "response_model": Answer,
        "max_tokens": 300,
        "prompt_version": "classify/v1",
        "study_id": "s-1",
        "activity": "classify",
    }
    base.update(changes)
    return ChatRequest(**base)


def message(
    text: str | None = '{"bucket": "5", "life": 5, "note": null}',
    *,
    stop: str = "end_turn",
    tool_uses: list[dict[str, Any]] | None = None,
    stop_details: dict[str, Any] | None = None,
    model: str = MODEL,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    content += tool_uses or []
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage
        or {
            "input_tokens": 1500,
            "output_tokens": 400,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


class Server:
    """A queue of responses (or exceptions); the last one repeats. Records requests."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def body(self, index: int = -1) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[index].content)
        return data


def ok(payload: dict[str, Any] | None = None) -> httpx2.Response:
    return httpx2.Response(200, json=payload if payload is not None else message())


def status(code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(
        code,
        headers=headers,
        json={"type": "error", "error": {"type": "api_error", "message": "x"}},
    )


class Waits:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def provider(server: Server, waits: Waits | None = None, **kwargs: Any) -> AnthropicProvider:
    client = anthropic.AsyncAnthropic(
        api_key="test-key", base_url=BASE, max_retries=0, http_client=server.client()
    )
    ticks = iter([10.0, 10.25, 20.0, 20.5, 30.0, 30.5])
    return AnthropicProvider(
        client,
        timeout_s=5.0,
        jitter=lambda: 0.7,
        sleep=(waits or Waits()).sleep,
        clock=lambda: next(ticks),
        **kwargs,
    )


# --------------------------------------------------------------------------- request


async def test_structured_request_and_parsed_response() -> None:
    server = Server(ok())
    response = await provider(server).complete(request())
    body = server.body()
    assert str(server.requests[0].url) == URL
    assert server.requests[0].headers["x-api-key"] == "test-key"
    assert body["model"] == MODEL
    assert body["system"] == "be exact"
    assert body["messages"] == [{"role": "user", "content": "classify carpet"}]
    # The answer budget plus the thinking allowance; no sampling or thinking parameters.
    assert body["max_tokens"] == 300 + THINKING_HEADROOM_TOKENS
    for absent in ("temperature", "top_p", "top_k", "thinking", "tools", "fallbacks"):
        assert absent not in body
    assert body["output_config"]["effort"] == "low"
    fmt = body["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": output_schema(Answer)}
    assert isinstance(response.parsed, Answer) and response.parsed.bucket == "5"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (1500, 400)
    assert response.model == MODEL and response.provider == "anthropic"
    assert response.latency_ms == 250
    assert response.raw["id"] == "msg_1" and response.text.startswith("{")


async def test_effort_setting_reaches_the_request() -> None:
    server = Server(ok())
    await provider(server, effort="high").complete(request())
    assert server.body()["output_config"]["effort"] == "high"
    # The request's own reasoning_effort (the runner's --reasoning-effort) wins.
    await provider(server, effort="high").complete(request(reasoning_effort="xhigh"))
    assert server.body()["output_config"]["effort"] == "xhigh"
    with pytest.raises(ProviderBadResponse, match="not a Claude effort level") as caught:
        await provider(server).complete(request(reasoning_effort="minimal"))
    assert caught.value.status == 400 and server.call_count == 2  # nothing was sent
    with pytest.raises(ValueError):
        provider(server, effort="extreme")
    with pytest.raises(ValueError):
        build_request_body(request(), effort="none")
    assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")


async def test_text_request_has_no_format() -> None:
    server = Server(ok(message("just text")))
    response = await provider(server).complete(request(response_model=None))
    assert "format" not in server.body()["output_config"]
    assert response.parsed is None and response.text == "just text"


def test_images_go_before_the_text_as_base64_blocks() -> None:
    image = ImagePart(media_type="image/jpeg", data_base64="AAAA", detail="high")
    body = build_request_body(
        request(messages=(Message(role="user", content="look", images=(image,)),))
    )
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"},
                },
                {"type": "text", "text": "look"},
            ],
        }
    ]
    with pytest.raises(ValueError, match="user messages"):
        build_request_body(
            request(messages=(Message(role="assistant", content="x", images=(image,)),))
        )


def test_system_role_messages_are_refused() -> None:
    with pytest.raises(ValueError, match="ChatRequest.system"):
        build_request_body(request(messages=(Message(role="system", content="x"),)))


# --------------------------------------------------------------------------- schema


def test_output_schema_moves_unsupported_constraints_into_descriptions() -> None:
    schema = output_schema(Nested)
    text = json.dumps(schema)
    for keyword in ("minimum", "maximum", "minLength", "maxLength", "maxItems", "default"):
        assert f'"{keyword}"' not in text, keyword
    assert schema["additionalProperties"] is False
    answer = schema["$defs"]["Answer"]
    assert answer["additionalProperties"] is False
    assert answer["required"] == ["bucket", "life"]  # note stays optional
    assert "0" in answer["properties"]["life"]["description"]  # the ge=0 moved here
    assert "label" not in schema["required"]  # a default makes the field optional


def test_the_photo_schema_is_accepted_as_is() -> None:
    text = json.dumps(output_schema(ModelPhotoAnalysis))
    for keyword in ("minimum", "maximum", "maxLength", "maxItems"):
        assert f'"{keyword}"' not in text, keyword
    assert '"enum"' in text  # the controlled vocabularies survive


# --------------------------------------------------------------------------- tools


def test_tools_tool_calls_and_tool_results() -> None:
    tool = ToolSpec(
        name="search_reference",
        description="Search the reference corpus",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    call = ToolCall(id="toolu_1", name="search_reference", arguments={"query": "carpet"})
    call2 = ToolCall(id="toolu_2", name="search_reference", arguments={"query": "tile"})
    body = build_request_body(
        request(
            response_model=None,
            tools=(tool,),
            messages=(
                Message(role="user", content="classify carpet"),
                Message(role="assistant", content="searching", tool_calls=(call, call2)),
                Message(role="tool", name=tool.name, tool_call_id="toolu_1", content="r1"),
                Message(role="tool", name=tool.name, tool_call_id="toolu_2", content="r2"),
                Message(role="user", content="go on"),
            ),
        )
    )
    assert body["tools"] == [
        {
            "name": "search_reference",
            "description": "Search the reference corpus",
            "input_schema": tool.parameters,
        }
    ]
    assert body["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "searching"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "search_reference",
                "input": call.arguments,
            },
            {
                "type": "tool_use",
                "id": "toolu_2",
                "name": "search_reference",
                "input": call2.arguments,
            },
        ],
    }
    # Both results answer one assistant turn, so they share one user message.
    assert body["messages"][2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "r1"},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "r2"},
        ],
    }
    assert body["messages"][3] == {"role": "user", "content": "go on"}
    with pytest.raises(ValueError, match="tool_call_id"):
        build_request_body(
            request(messages=(Message(role="tool", name="search_reference", content="r"),))
        )


async def test_tool_use_blocks_become_tool_calls() -> None:
    use = {"type": "tool_use", "id": "toolu_9", "name": "search", "input": {"query": "q"}}
    server = Server(ok(message(None, stop="tool_use", tool_uses=[use])))
    response = await provider(server).complete(request())
    assert response.tool_calls == (ToolCall(id="toolu_9", name="search", arguments={"query": "q"}),)
    assert response.parsed is None and response.text == ""


def test_malformed_tool_input_is_passed_through() -> None:
    block = SimpleNamespace(type="tool_use", id="t", name="search", input="not an object")
    fake = SimpleNamespace(
        content=[block],
        stop_reason="tool_use",
        model=MODEL,
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model_dump=lambda mode: {},
    )
    response = parse_message(fake, request(response_model=None), provider="anthropic", latency_ms=0)
    assert response.tool_calls[0].arguments == {MALFORMED_ARGUMENTS: "not an object"}


# --------------------------------------------------------------------------- bad answers


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        (message('{"bucket": 5}'), "does not match Answer"),
        (message("not json"), "does not match Answer"),
        (message('{"bucket": "5", "life": 5', stop="max_tokens"), "truncated at max_tokens"),
        (message('{"bucket"', stop="model_context_window_exceeded"), "context window"),
        (
            message(
                "I can't help",
                stop="refusal",
                stop_details={"type": "refusal", "category": "cyber", "explanation": "no"},
            ),
            r"refused \(cyber\): no",
        ),
        (message(None, stop="refusal"), "refused"),
    ],
)
async def test_unusable_answers_are_bad_responses_with_usage(
    payload: dict[str, Any], text: str
) -> None:
    with pytest.raises(ProviderBadResponse, match=text) as caught:
        await provider(Server(ok(payload))).complete(request())
    assert caught.value.usage is not None and caught.value.usage.input_tokens == 1500
    assert caught.value.model == MODEL and caught.value.raw is not None
    assert caught.value.provider == "anthropic"


async def test_a_refusal_is_bad_even_without_a_response_model() -> None:
    payload = message("declined", stop="refusal")
    with pytest.raises(ProviderBadResponse, match="refused"):
        await provider(Server(ok(payload))).complete(request(response_model=None))


# --------------------------------------------------------------------------- usage and cost


async def test_cache_tokens_count_as_input_and_thinking_as_output() -> None:
    usage = {
        "input_tokens": 1000,
        "output_tokens": 900,  # includes the thinking tokens
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 30,
    }
    response = await provider(Server(ok(message(usage=usage)))).complete(request())
    assert (response.usage.input_tokens, response.usage.output_tokens) == (1050, 900)


async def test_missing_usage_is_recorded_as_zero() -> None:
    payload = message()
    del payload["usage"]
    response = await provider(Server(ok(payload))).complete(request())
    assert (response.usage.input_tokens, response.usage.output_tokens) == (0, 0)


def test_published_prices_per_million() -> None:
    # platform.claude.com/docs/en/about-claude/pricing, base rates, checked 2026-10-15
    assert PRICES["claude-fable-5-1"].input_per_million == Decimal("10.00")
    assert PRICES["claude-fable-5-1"].output_per_million == Decimal("50.00")
    assert PRICES["claude-opus-5-5"].input_per_million == Decimal("4.00")
    assert PRICES["claude-opus-5-5"].output_per_million == Decimal("20.00")
    assert PRICES["claude-sonnet-5-5"].input_per_million == Decimal("2.00")
    assert PRICES["claude-sonnet-5-5"].output_per_million == Decimal("10.00")
    million = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("claude-fable-5-1", million) == 60.0
    assert cost_usd("claude-opus-5-5", million) == 24.0
    assert cost_usd("claude-sonnet-5-5", million) == 12.0
    # A photo call: 1500 input, 400 output tokens on Opus 5.5 = $0.006 + $0.008
    assert cost_usd("claude-opus-5-5", Usage(input_tokens=1500, output_tokens=400)) == 0.014
    assert price_for("claude-opus-5-5") is PRICES["claude-opus-5-5"]
    # Opus 5 ($5/$25) is not Opus 5.5: no prefix match, priced as unknown (the dearest row).
    assert price_for("claude-opus-5") is not PRICES["claude-opus-5-5"]


def test_thinking_headroom_and_image_tokens_enter_the_estimate() -> None:
    assert output_ceiling("claude-fable-5-1", 1200) == 1200 + THINKING_HEADROOM_TOKENS
    assert output_ceiling("gpt-4.1", 1200) == 1200  # OpenAI path unchanged
    with pytest.raises(ValueError):
        output_ceiling(MODEL, -1)
    claude = estimate_usd(MODEL, max_tokens=1200, prompt_chars=4000, images=1)
    expected = (1000 + CLAUDE_IMAGE_TOKENS) * 4.00 / 1e6 + (
        1200 + THINKING_HEADROOM_TOKENS
    ) * 20 / 1e6
    assert claude == pytest.approx(expected, abs=1e-6)
    openai = estimate_usd("gpt-4o", max_tokens=1000, prompt_chars=4000, images=1)
    expected_openai = (1000 + IMAGE_TOKENS) * 2.50 / 1e6 + 1000 * 10.00 / 1e6
    assert openai == pytest.approx(expected_openai, abs=1e-6)


def test_the_estimate_bounds_a_photo_call_that_thinks() -> None:
    estimate = estimate_usd(MODEL, max_tokens=1200, prompt_chars=8000, images=1)
    # 1024 x 683 photo (925 visual tokens) + 2000 prompt tokens, 1200 answer + 3000 thinking
    actual = cost_usd(MODEL, Usage(input_tokens=2925, output_tokens=4200))
    assert actual <= estimate


# --------------------------------------------------------------------------- retries / errors


async def test_one_retry_on_429_with_jitter() -> None:
    server = Server(status(429), ok())
    waits = Waits()
    response = await provider(server, waits).complete(request())
    assert server.call_count == 2 and waits.sleeps == [0.7]
    assert response.parsed is not None


async def test_retry_after_is_honoured_and_capped() -> None:
    server = Server(status(429, {"retry-after": "3"}), status(429, {"retry-after": "3"}))
    waits = Waits()
    with pytest.raises(ProviderRateLimited) as caught:
        await provider(server, waits).complete(request())
    assert waits.sleeps == [3.0] and server.call_count == 2  # a single retry only
    assert caught.value.status == 429 and caught.value.provider == "anthropic"
    capped = Waits()
    await provider(Server(status(429, {"retry-after": "120"}), ok()), capped).complete(request())
    assert capped.sleeps == [10.0]


@pytest.mark.parametrize("code", [500, 502, 504, 529])
async def test_5xx_and_overloaded_are_retried_once_then_unavailable(code: int) -> None:
    server = Server(status(code))
    with pytest.raises(ProviderUnavailable) as caught:
        await provider(server).complete(request())
    assert server.call_count == 2 and caught.value.status == code


async def test_overloaded_then_success() -> None:
    server = Server(status(529), ok())
    assert (await provider(server).complete(request())).parsed is not None


@pytest.mark.parametrize("code", [400, 404, 413, 422])
async def test_4xx_is_a_bad_response_and_not_retried(code: int) -> None:
    server = Server(status(code))
    with pytest.raises(ProviderBadResponse) as caught:
        await provider(server).complete(request())
    assert server.call_count == 1 and caught.value.status == code


async def test_timeout_is_unavailable_and_not_retried_here() -> None:
    server = Server(httpx2.ReadTimeout("slow"))
    with pytest.raises(ProviderUnavailable, match="timed out") as caught:
        await provider(server).complete(request())
    assert server.call_count == 1 and caught.value.status is None


async def test_connection_error_is_unavailable() -> None:
    with pytest.raises(ProviderUnavailable, match="connection failed"):
        await provider(Server(httpx2.ConnectError("refused"))).complete(request())


def test_unknown_exceptions_map_to_unavailable_and_ours_pass_through() -> None:
    assert isinstance(map_error(RuntimeError("boom")), ProviderUnavailable)
    ours = ProviderBadResponse("x", provider="anthropic")
    assert map_error(ours) is ours


async def test_the_timeout_reaches_the_http_request() -> None:
    server = Server(ok())
    await provider(server).complete(request())
    assert server.requests[0].extensions["timeout"]["read"] == 5.0


# --------------------------------------------------------------------------- routing / settings


@pytest.mark.parametrize(
    ("model", "claude"),
    [
        ("claude-fable-5-1", True),
        ("claude-opus-5-5", True),
        ("Claude-Sonnet-5-5", True),
        ("gpt-4.1", False),
        ("gpt-5-mini", False),
        ("o3-mini", False),
        ("my-azure-deployment", False),
    ],
)
def test_model_family_detection(model: str, claude: bool) -> None:
    assert is_anthropic_model(model) is claude
    assert AnthropicProvider.accepts(model) is claude


async def test_a_non_claude_request_is_refused_without_a_call() -> None:
    server = Server(ok())
    with pytest.raises(ProviderBadResponse, match="does not serve"):
        await provider(server).complete(request(model="gpt-4.1"))
    assert server.call_count == 0


def settings(**changes: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "azure_openai_endpoint": None,
        "azure_openai_api_key": None,
        "openai_api_key": None,
        "anthropic_api_key": SecretStr("sk-ant-test"),  # pragma: allowlist secret
        "llm_request_timeout_seconds": 7,
    }
    values.update(changes)
    return Settings(**values)


def test_from_settings() -> None:
    assert AnthropicProvider.from_settings(settings(anthropic_api_key=None)) is None
    assert AnthropicProvider.from_settings(settings(anthropic_api_key=SecretStr(" "))) is None
    configured = AnthropicProvider.from_settings(settings(anthropic_effort="medium"))
    assert configured is not None
    assert configured._timeout_s == 7 and configured.effort == "medium"
    assert configured._client.max_retries == 0  # our single retry replaces the SDK's
    assert configured.name == "anthropic"


def test_providers_from_settings_includes_anthropic_and_orders_by_primary() -> None:
    only = providers_from_settings(settings())
    assert [p.name for p in only] == ["anthropic"]
    both = settings(openai_api_key=SecretStr("sk-test"))  # pragma: allowlist secret
    assert [p.name for p in providers_from_settings(both)] == ["openai", "anthropic"]
    claude_first = settings(openai_api_key=SecretStr("sk-test"), llm_primary="anthropic")
    assert [p.name for p in providers_from_settings(claude_first)] == ["anthropic", "openai"]


# --------------------------------------------------------------------------- account errors
# Shapes from https://platform.claude.com/docs/en/api/errors and .../api/rate-limits (read
# 2026-10-16); the 400 credit message is what the API returned in EVAL_REPORT 26.12.

CREDIT_LOW = (
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & "
    "Billing to upgrade or purchase credits."
)
SPEND_CAP = (
    "You have reached your API usage limits: your organization has crossed its monthly API "
    "usage threshold, set based on your organization's API tier. You will regain access on "
    "2026-11-01 at 00:00 UTC."
)


def error_response(
    code: int, kind: str, text: str, details: dict[str, Any] | None = None
) -> httpx2.Response:
    error: dict[str, Any] = {"type": kind, "message": text}
    if details is not None:
        error["details"] = details
    return httpx2.Response(
        code, json={"type": "error", "error": error, "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy"}
    )


@pytest.mark.parametrize(
    ("response", "code", "expected_code"),
    [
        (error_response(400, "invalid_request_error", CREDIT_LOW), 400, "invalid_request_error"),
        (
            error_response(
                400,
                "invalid_request_error",
                "You have reached your specified API usage limits. You will regain access on "
                "2026-11-01 at 00:00 UTC.",
            ),
            400,
            "invalid_request_error",
        ),
        (
            error_response(
                400,
                "invalid_request_error",
                "You have reached your specified workspace API usage limits.",
            ),
            400,
            "invalid_request_error",
        ),
        (
            error_response(401, "authentication_error", "invalid x-api-key"),
            401,
            "authentication_error",
        ),
        (
            error_response(402, "billing_error", "There is an issue with your billing."),
            402,
            "billing_error",
        ),
        (
            error_response(403, "permission_error", "Your API key does not have permission."),
            403,
            "permission_error",
        ),
        (
            error_response(
                429, "rate_limit_error", SPEND_CAP, {"error_code": "enforced_spend_limit_reached"}
            ),
            429,
            "enforced_spend_limit_reached",
        ),
        (httpx2.Response(401, text="Unauthorized"), 401, None),
    ],
)
async def test_account_errors_are_not_retried(
    response: httpx2.Response, code: int, expected_code: str | None
) -> None:
    server = Server(response)
    waits = Waits()
    with pytest.raises(ProviderAccountError) as caught:
        await provider(server, waits).complete(request())
    error = caught.value
    assert server.call_count == 1 and waits.sleeps == []  # no retry, no wait
    assert (error.provider, error.status, error.code) == ("anthropic", code, expected_code)
    assert str(error).startswith(f"anthropic account error (HTTP {code}")
    assert error.usage is None  # nothing reached the model: nothing to charge


def test_the_credit_message_is_kept_and_a_key_is_not() -> None:
    error = map_error(
        anthropic.AuthenticationError(
            "Error code: 401",
            response=httpx2.Response(401, request=httpx2.Request("POST", BASE)),
            body={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key sk-ant-api03-SECRETSECRET-abc",
                },
            },
        )
    )
    assert isinstance(error, ProviderAccountError)
    assert "SECRETSECRET" not in str(error) and "[redacted]" in str(error)
    credit = map_error(
        anthropic.BadRequestError(
            "Error code: 400",
            response=httpx2.Response(400, request=httpx2.Request("POST", BASE)),
            body={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": CREDIT_LOW},
            },
        )
    )
    assert isinstance(credit, ProviderAccountError) and CREDIT_LOW in str(credit)


async def test_a_plain_400_stays_a_bad_response() -> None:
    server = Server(error_response(400, "invalid_request_error", "messages: field required"))
    with pytest.raises(ProviderBadResponse):
        await provider(server).complete(request())


async def test_a_rate_limit_429_is_still_retried() -> None:
    """Regression: only the spend-cap 429 is an account error; a rate limit keeps its retry
    (with or without details that are not the spend-cap code)."""
    for details in (None, {"error_code": "something_else"}):
        server = Server(
            error_response(
                429,
                "rate_limit_error",
                "Number of request tokens has exceeded your per-minute rate limit",
                details,
            ),
            ok(),
        )
        waits = Waits()
        assert (await provider(server, waits).complete(request())).parsed is not None
        assert server.call_count == 2 and waits.sleeps == [0.7]
