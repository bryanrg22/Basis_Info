"""OpenAI / Azure OpenAI providers over the real SDK with a mock HTTP transport.

No request leaves the process: the SDK (``openai`` 3.x, which is built on ``httpx2``, so
``respx`` cannot intercept it) gets an ``httpx2.AsyncClient`` whose ``MockTransport``
answers from a queue. The tests therefore exercise the SDK's own request serialisation,
status handling and error classes.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel, Field, SecretStr

from basis.config import ConfigError, Settings
from basis.llm.errors import ProviderBadResponse, ProviderRateLimited, ProviderUnavailable
from basis.llm.provider import ChatRequest, ImagePart, Message, ToolCall, ToolSpec
from basis.llm.providers import (
    MALFORMED_ARGUMENTS,
    AzureOpenAIProvider,
    OpenAIProvider,
    build_request_body,
    check_azure_api_version,
    is_reasoning_model,
    providers_from_settings,
    strict_json_schema,
)

BASE = "https://api.test/v1"
URL = f"{BASE}/chat/completions"


class Answer(BaseModel):
    bucket: str
    life: float = Field(ge=0)
    note: str | None = None


class Nested(BaseModel):
    answers: list[Answer]
    label: str = Field(default="x", max_length=10)


def request(**changes: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "model": "gpt-4o-mini",
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


def completion(
    content: str | None = '{"bucket": "5", "life": 5, "note": null}',
    *,
    finish: str = "stop",
    tool_calls: list[dict[str, Any]] | None = None,
    refusal: str | None = None,
    model: str = "gpt-4o-mini-2024-07-18",
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, "refusal": refusal}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish, "logprobs": None}],
        "usage": usage or {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
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
    return httpx2.Response(200, json=payload if payload is not None else completion())


def status(code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(code, headers=headers, json={"error": {"message": "x"}})


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def provider(server: Server, waits: Recorder | None = None, **kwargs: Any) -> OpenAIProvider:
    client = openai.AsyncOpenAI(
        api_key="test-key", base_url=BASE, max_retries=0, http_client=server.client()
    )
    ticks = iter([10.0, 10.25, 20.0, 20.5, 30.0, 30.5])
    return OpenAIProvider(
        client,
        timeout_s=5.0,
        jitter=lambda: 0.7,
        sleep=(waits or Recorder()).sleep,
        clock=lambda: next(ticks),
        **kwargs,
    )


# --------------------------------------------------------------------------- request


async def test_structured_request_and_parsed_response() -> None:
    server = Server(ok())
    response = await provider(server).complete(request())
    body = server.body()
    assert str(server.requests[0].url) == URL
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"] == [
        {"role": "system", "content": "be exact"},
        {"role": "user", "content": "classify carpet"},
    ]
    assert (body["max_tokens"], body["temperature"]) == (300, 0.0)
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "Answer"
    assert fmt["json_schema"]["schema"] == strict_json_schema(Answer)
    assert server.requests[0].headers["authorization"] == "Bearer test-key"
    assert isinstance(response.parsed, Answer) and response.parsed.bucket == "5"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 30)
    assert response.model == "gpt-4o-mini-2024-07-18" and response.provider == "openai"
    assert response.latency_ms == 250
    assert response.raw["id"] == "chatcmpl-1"


async def test_reasoning_models_use_max_completion_tokens_and_no_temperature() -> None:
    server = Server(ok())
    await provider(server).complete(request(model="gpt-5-mini", temperature=0.3))
    body = server.body()
    assert body["max_completion_tokens"] == 300
    assert "max_tokens" not in body and "temperature" not in body


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [
        ("gpt-5", True),
        ("gpt-5-nano", True),
        ("o3-mini", True),
        ("gpt-4o", False),
        ("gpt-4.1", False),
    ],
)
def test_reasoning_model_detection(model: str, reasoning: bool) -> None:
    assert is_reasoning_model(model) is reasoning


async def test_tools_tool_calls_and_tool_results() -> None:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search_reference", "arguments": '{"query": "carpet"}'},
    }
    server = Server(ok(completion(None, finish="tool_calls", tool_calls=[call])))
    tool = ToolSpec(
        name="search_reference",
        description="Search the reference corpus",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    previous = ToolCall(id="call_0", name="get_chunk", arguments={"chunk_id": "c1"})
    response = await provider(server).complete(
        request(
            tools=(tool,),
            messages=(
                Message(role="user", content="classify carpet"),
                Message(role="assistant", content="", tool_calls=(previous,)),
                Message(
                    role="tool", content='{"text": "..."}', name="get_chunk", tool_call_id="call_0"
                ),
            ),
        )
    )
    body = server.body()
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_reference",
                "description": "Search the reference corpus",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]
    assert body["messages"][2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "get_chunk", "arguments": '{"chunk_id": "c1"}'},
            }
        ],
    }
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_0",
        "content": '{"text": "..."}',
    }
    assert response.parsed is None  # a tool turn is not parsed as the final answer
    assert response.tool_calls == (
        ToolCall(id="call_1", name="search_reference", arguments={"query": "carpet"}),
    )


async def test_malformed_tool_arguments_are_passed_through() -> None:
    call = {"id": "c", "type": "function", "function": {"name": "t", "arguments": "{oops"}}
    server = Server(ok(completion(None, finish="tool_calls", tool_calls=[call])))
    response = await provider(server).complete(request())
    assert response.tool_calls[0].arguments == {MALFORMED_ARGUMENTS: "{oops"}


def test_a_tool_message_needs_its_name_and_call_id() -> None:
    for message in (
        Message(role="tool", content="[]", tool_call_id="c1"),
        Message(role="tool", content="[]", name="search_reference"),
    ):
        with pytest.raises(ValueError, match="W5"):
            build_request_body(request(messages=(message,)))


def test_images_are_data_urls_on_the_user_message() -> None:
    image = ImagePart(data_base64="QUJD", detail="high")
    body = build_request_body(
        request(messages=(Message(role="user", content="what room?", images=(image,)),))
    )
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "what room?"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,QUJD", "detail": "high"},
        },
    ]
    with pytest.raises(ValueError):
        build_request_body(
            request(messages=(Message(role="assistant", content="x", images=(image,)),))
        )


def test_no_response_model_means_no_response_format() -> None:
    assert "response_format" not in build_request_body(request(response_model=None))


def test_strict_schema_rules() -> None:
    schema = strict_json_schema(Nested)
    answer = schema["$defs"]["Answer"]
    assert answer["additionalProperties"] is False
    assert answer["required"] == ["bucket", "life", "note"]  # optional fields required too
    assert "default" not in json.dumps(schema)
    assert "maxLength" not in json.dumps(schema)  # enforced on validation instead
    assert answer["properties"]["life"]["minimum"] == 0
    assert schema["required"] == ["answers", "label"]


# --------------------------------------------------------------------------- responses


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (completion('{"bucket": 5}'), "does not match Answer"),
        (completion("not json"), "does not match Answer"),
        (completion('{"bucket": "5", "life": 5, "note": null}', finish="length"), "truncated"),
        (completion(None, refusal="I can't help with that"), "refused"),
        (completion('{"bucket": "5"', finish="content_filter"), "content filter"),
    ],
)
async def test_unusable_answers_are_bad_responses_with_usage(
    payload: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProviderBadResponse, match=message) as caught:
        await provider(Server(ok(payload))).complete(request())
    assert caught.value.usage is not None and caught.value.usage.input_tokens == 120
    assert caught.value.model == "gpt-4o-mini-2024-07-18" and caught.value.raw is not None


async def test_missing_usage_is_recorded_as_zero() -> None:
    payload = completion()
    del payload["usage"]
    response = await provider(Server(ok(payload))).complete(request())
    assert (response.usage.input_tokens, response.usage.output_tokens) == (0, 0)


# --------------------------------------------------------------------------- retries / errors


async def test_one_retry_on_429_with_jitter() -> None:
    server = Server(status(429), ok())
    waits = Recorder()
    response = await provider(server, waits).complete(request())
    assert server.call_count == 2 and waits.sleeps == [0.7]
    assert response.parsed is not None


async def test_retry_after_is_honoured() -> None:
    server = Server(status(429, {"retry-after": "3"}), status(429, {"retry-after": "3"}))
    waits = Recorder()
    with pytest.raises(ProviderRateLimited):
        await provider(server, waits).complete(request())
    assert waits.sleeps == [3.0] and server.call_count == 2  # a single retry only


async def test_retry_after_is_capped() -> None:
    capped = Recorder()
    await provider(Server(status(429, {"retry-after": "120"}), ok()), capped).complete(request())
    assert capped.sleeps == [10.0]


async def test_429_twice_is_rate_limited() -> None:
    server = Server(status(429))
    with pytest.raises(ProviderRateLimited) as caught:
        await provider(server).complete(request())
    assert server.call_count == 2 and caught.value.status == 429


@pytest.mark.parametrize("code", [500, 502, 503])
async def test_5xx_is_retried_once_then_unavailable(code: int) -> None:
    server = Server(status(code))
    with pytest.raises(ProviderUnavailable) as caught:
        await provider(server).complete(request())
    assert server.call_count == 2 and caught.value.status == code


async def test_5xx_then_success() -> None:
    server = Server(status(503), ok())
    assert (await provider(server).complete(request())).parsed is not None


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
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


async def test_the_timeout_reaches_the_http_request() -> None:
    server = Server(ok())
    await provider(server).complete(request())
    timeout = server.requests[0].extensions["timeout"]
    assert timeout["read"] == 5.0


# --------------------------------------------------------------------------- Azure


AZURE = "https://example-resource.openai.azure.com"


def azure_settings(**changes: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "azure_openai_endpoint": AZURE,
        "azure_openai_api_key": "azure-key",  # pragma: allowlist secret
        "azure_openai_deployment_name": "default-dep",
        "azure_openai_vision_deployment": "vision-dep",
        "azure_openai_text_deployment": None,
        "azure_openai_api_version": "2024-10-21",
        "openai_api_key": None,
        "anthropic_api_key": None,
        "vision_model": "gpt-4o-mini",
        "text_model": "gpt-4.1-mini",
        "llm_request_timeout_seconds": 7,
    }
    values.update(changes)
    return Settings(**values)


async def test_azure_sends_to_the_deployment_with_the_api_version() -> None:
    server = Server(ok())
    azure = AzureOpenAIProvider.from_settings(azure_settings(), http_client=server.client())
    assert azure is not None and azure.name == "azure"
    response = await azure.complete(request(model="gpt-4o-mini"))
    await azure.complete(request(model="gpt-4.1-mini"))
    vision, text = server.requests
    assert vision.url.path == "/openai/deployments/vision-dep/chat/completions"
    assert text.url.path == "/openai/deployments/default-dep/chat/completions"
    assert vision.url.host == "example-resource.openai.azure.com"
    assert vision.url.params["api-version"] == "2024-10-21"
    assert vision.headers["api-key"] == "azure-key"
    assert response.provider == "azure"


@pytest.mark.parametrize("version", ["2024-02-15-preview", "2023-05-15", "latest"])
def test_old_or_undated_azure_api_versions_are_refused(version: str) -> None:
    with pytest.raises(ConfigError):
        check_azure_api_version(version)
    with pytest.raises(ConfigError):
        AzureOpenAIProvider.from_settings(azure_settings(azure_openai_api_version=version))


@pytest.mark.parametrize("version", ["2024-08-01-preview", "2024-10-21", "2025-04-01-preview"])
def test_structured_output_api_versions_are_accepted(version: str) -> None:
    check_azure_api_version(version)


def test_from_settings_returns_nothing_when_unconfigured() -> None:
    bare = azure_settings(azure_openai_endpoint=None, openai_api_key=None)
    assert AzureOpenAIProvider.from_settings(bare) is None
    assert OpenAIProvider.from_settings(bare) is None
    assert providers_from_settings(bare) == []
    no_deployment = azure_settings(
        azure_openai_deployment_name=None, azure_openai_vision_deployment=None
    )
    assert AzureOpenAIProvider.from_settings(no_deployment) is None


def test_primary_order_follows_settings() -> None:
    both = azure_settings(openai_api_key=SecretStr("sk-test"))
    assert [p.name for p in providers_from_settings(both)] == ["azure", "openai"]
    openai_first = azure_settings(openai_api_key=SecretStr("sk-test"), llm_primary="openai")
    assert [p.name for p in providers_from_settings(openai_first)] == ["openai", "azure"]


def test_openai_providers_do_not_serve_claude_models() -> None:
    assert OpenAIProvider.accepts("gpt-4.1") and AzureOpenAIProvider.accepts("vision-dep")
    assert not OpenAIProvider.accepts("claude-opus-5-5")


def test_openai_provider_from_settings_uses_the_timeout() -> None:
    key = "sk-test"  # pragma: allowlist secret
    configured = OpenAIProvider.from_settings(
        azure_settings(azure_openai_endpoint=None, openai_api_key=key)
    )
    assert configured is not None and configured._timeout_s == 7
    assert configured._client.max_retries == 0  # our single retry replaces the SDK's
