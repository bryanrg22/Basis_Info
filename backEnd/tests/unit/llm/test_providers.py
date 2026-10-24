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
from basis.llm.errors import (
    CONFIG_HINT,
    ProviderAccountError,
    ProviderBadResponse,
    ProviderConfigError,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
    classify_rejection,
)
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
    assert "reasoning_effort" not in body


async def test_reasoning_effort_is_sent_only_to_reasoning_models() -> None:
    server = Server(ok())
    await provider(server).complete(request(model="gpt-6.1-sol", reasoning_effort="low"))
    assert server.body()["reasoning_effort"] == "low"
    server = Server(ok())
    await provider(server).complete(request(model="gpt-4.1", reasoning_effort="low"))
    assert "reasoning_effort" not in server.body()


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [
        ("gpt-5", True),
        ("gpt-5-nano", True),
        ("o3-mini", True),
        ("gpt-6.1-sol", True),
        ("gpt-6-astra", True),
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


@pytest.mark.parametrize("code", [400, 404, 422])
async def test_an_unexplained_4xx_is_a_configuration_error_and_not_retried(code: int) -> None:
    """Before: a bad response, which the vision step retried once and turned into an
    ambiguous photo with no component. A 4xx refused the request, not the answer; one whose
    code and message do not name the input is the configuration's, which stops the study."""
    server = Server(status(code))
    with pytest.raises(ProviderConfigError) as caught:
        await provider(server).complete(request())
    assert server.call_count == 1 and caught.value.status == code
    assert isinstance(caught.value, ProviderAccountError)  # every account stop applies
    assert not isinstance(caught.value, ProviderBadResponse)


@pytest.mark.parametrize("code", [408, 409])
async def test_408_and_409_are_transient_as_the_sdk_treats_them(code: int) -> None:
    server = Server(status(code))
    with pytest.raises(ProviderUnavailable) as caught:
        await provider(server).complete(request())
    assert caught.value.status == code and server.call_count == 1  # Temporal retries it


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


# --------------------------------------------------------------------------- account errors
# Bodies as OpenAI documents them (developers.openai.com/api/docs/guides/error-codes, read
# 2026-10-16) and as the shared account returned them (EVAL_REPORT sections 14, 16).


def error_body(message: str, *, type: str | None = None, code: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": type, "param": None, "code": code}}


QUOTA = "You exceeded your current quota, please check your plan and billing details."
BAD_KEY = (
    "Incorrect API key provided: sk-proj-abcdEFGH1234****************wxyz. You can find "
    "your API key at https://platform.openai.com/account/api-keys."
)


@pytest.mark.parametrize(
    ("code", "body", "expected_code"),
    [
        (
            429,
            error_body(QUOTA, type="insufficient_quota", code="insufficient_quota"),
            "insufficient_quota",
        ),
        (
            429,
            error_body(
                "Your credit balance is exhausted.",
                type="insufficient_quota",
                code="credit_balance_exhausted",
            ),
            "credit_balance_exhausted",
        ),
        (
            429,
            error_body(
                "Spend limit reached.",
                type="insufficient_quota",
                code="project_spend_limit_exceeded",
            ),
            "insufficient_quota",
        ),
        (
            429,
            error_body("Spend limit reached.", code="organization_spend_limit_exceeded"),
            "organization_spend_limit_exceeded",
        ),
        (
            429,
            error_body("Usage limit reached.", code="organization_usage_limit_exceeded"),
            "organization_usage_limit_exceeded",
        ),
        (
            401,
            error_body(BAD_KEY, type="invalid_request_error", code="invalid_api_key"),
            "invalid_api_key",
        ),
        (
            401,
            error_body(
                "You must be a member of an organization to use the API.", code="no_organization"
            ),
            "no_organization",
        ),
        (
            403,
            error_body(
                "Country, region, or territory not supported",
                code="unsupported_country_region_territory",
            ),
            "unsupported_country_region_territory",
        ),
        (402, error_body("Payment required."), None),
    ],
)
async def test_account_errors_are_not_retried(
    code: int, body: dict[str, Any], expected_code: str | None
) -> None:
    server = Server(httpx2.Response(code, json=body))
    waits = Recorder()
    with pytest.raises(ProviderAccountError) as caught:
        await provider(server, waits).complete(request())
    error = caught.value
    assert server.call_count == 1 and waits.sleeps == []  # no retry, no wait
    assert (error.provider, error.status, error.code) == ("openai", code, expected_code)
    assert body["error"]["message"][:20] in str(error)
    assert not isinstance(error, ProviderBadResponse | ProviderRateLimited | ProviderUnavailable)


async def test_an_account_error_never_carries_the_key() -> None:
    server = Server(httpx2.Response(401, json=error_body(BAD_KEY, code="invalid_api_key")))
    with pytest.raises(ProviderAccountError) as caught:
        await provider(server).complete(request())
    text = f"{caught.value} {caught.value.args}"
    assert "sk-proj" not in text and "abcdEFGH" not in text and "wxyz" not in text
    assert "[redacted]" in text
    assert str(caught.value).startswith("openai account error (HTTP 401, invalid_api_key): ")


async def test_an_account_error_without_a_json_body_still_maps() -> None:
    server = Server(httpx2.Response(401, text="Unauthorized"))
    with pytest.raises(ProviderAccountError) as caught:
        await provider(server).complete(request())
    assert caught.value.status == 401 and server.call_count == 1


@pytest.mark.parametrize(
    "body",
    [
        error_body(
            "Rate limit reached for gpt-4o-mini in organization org-x on tokens per min.",
            type="tokens",
            code="rate_limit_exceeded",
        ),
        error_body("Slow down", type="rate_limit_error", code="slow_down"),
        {"error": {"message": "x"}},
    ],
)
async def test_a_429_that_is_a_rate_limit_is_still_retried(body: dict[str, Any]) -> None:
    """Regression: only the billing codes are account errors; rate limits keep their retry."""
    server = Server(httpx2.Response(429, json=body), ok())
    waits = Recorder()
    response = await provider(server, waits).complete(request())
    assert server.call_count == 2 and waits.sleeps == [0.7] and response.parsed is not None


AZURE_BAD_KEY = (
    "Access denied due to invalid subscription key or wrong API endpoint. Make sure to "
    "provide a valid key for an active subscription and use a correct regional API endpoint "
    "for your resource."
)


@pytest.mark.parametrize("code", [401, 403])
async def test_azure_key_and_permission_errors_are_account_errors(code: int) -> None:
    server = Server(
        httpx2.Response(code, json={"error": {"code": str(code), "message": AZURE_BAD_KEY}})
    )
    azure = AzureOpenAIProvider.from_settings(azure_settings(), http_client=server.client())
    assert azure is not None
    with pytest.raises(ProviderAccountError) as caught:
        await azure.complete(request())
    assert (caught.value.provider, caught.value.status) == ("azure", code)
    assert "Access denied" in str(caught.value) and server.call_count == 1


async def test_an_azure_token_rate_limit_is_still_retried() -> None:
    body = {
        "error": {
            "code": "429",
            "message": "Requests to the ChatCompletions_Create Operation have exceeded the "
            "token rate limit.",
        }
    }
    server = Server(httpx2.Response(429, json=body), ok())
    azure = AzureOpenAIProvider.from_settings(
        azure_settings(), http_client=server.client(), jitter=lambda: 0.0, sleep=Recorder().sleep
    )
    assert azure is not None
    assert (await azure.complete(request())).parsed is not None and server.call_count == 2


# --------------------------------------------------------------------------- refused requests
# A 4xx that is not an account error and not a 429 refused the request, not the answer: the
# request as configured (ProviderConfigError, which stops the study like an account error)
# or this request's content (ProviderRequestRejected, which fails the photo). Bodies as the
# providers document them: OpenAI's error-codes guide and the 400 ``unsupported_parameter``
# reports for reasoning models; Azure's content-filtering page (HTTP 400, ``code:
# "content_filter"``, ``innererror.code: "ResponsibleAIPolicyViolation"``) and Microsoft Q&A
# for ``DeploymentNotFound`` (404) and ``OperationNotSupported``; all read 2026-10-23.

UNSUPPORTED_PARAMETER = error_body(
    "Unsupported parameter: 'max_tokens' is not supported with this model. Use "
    "'max_completion_tokens' instead.",
    type="invalid_request_error",
    code="unsupported_parameter",
)
UNSUPPORTED_VALUE = error_body(
    "Unsupported value: 'temperature' does not support 0.0 with this model. Only the default "
    "(1) value is supported.",
    type="invalid_request_error",
    code="unsupported_value",
)
MODEL_NOT_FOUND = error_body(
    "The model `gpt-6.1-sol` does not exist or you do not have access to it.",
    type="invalid_request_error",
    code="model_not_found",
)
NOT_A_VISION_MODEL = error_body(
    "Invalid content type. image_url is only supported by certain models.",
    type="invalid_request_error",
)
BAD_SCHEMA = error_body(
    "Invalid schema for response_format 'Answer': In context=(), 'required' is required to "
    "be supplied and to be an array including every key in properties.",
    type="invalid_request_error",
    code="invalid_value",
)
UNSUPPORTED_IMAGE = error_body(
    "You uploaded an unsupported image. Please make sure your image is below 20 MB in size "
    "and is of one the following formats: ['png', 'jpeg', 'gif', 'webp'].",
    type="invalid_request_error",
    code="invalid_image_format",
)
INVALID_IMAGE = error_body("Invalid image.", type="invalid_request_error")
AZURE_DEPLOYMENT_NOT_FOUND = {
    "error": {
        "code": "DeploymentNotFound",
        "message": "The API deployment for this resource does not exist. If you created the "
        "deployment within the last 5 minutes, please wait a moment and try again.",
    }
}
AZURE_OPERATION_NOT_SUPPORTED = {
    "error": {
        "code": "OperationNotSupported",
        "message": "The chatCompletion operation does not work with the specified model, "
        "gpt-35-turbo-instruct. Please choose different model and try again.",
    }
}
AZURE_UNRECOGNIZED_ARGUMENT = error_body(
    "Unrecognized request argument supplied: reasoning_effort", type="invalid_request_error"
)
AZURE_CONTENT_FILTER = {
    "error": {
        "message": "The response was filtered due to the prompt triggering Azure OpenAI's "
        "content management policy. Please modify your prompt and retry.",
        "type": None,
        "param": "prompt",
        "code": "content_filter",
        "status": 400,
        "innererror": {
            "code": "ResponsibleAIPolicyViolation",
            "content_filter_result": {"violence": {"filtered": True, "severity": "high"}},
        },
    }
}


@pytest.mark.parametrize(
    ("code", "body", "expected_code"),
    [
        (400, UNSUPPORTED_PARAMETER, "unsupported_parameter"),
        (400, UNSUPPORTED_VALUE, "unsupported_value"),
        (404, MODEL_NOT_FOUND, "model_not_found"),
        (400, NOT_A_VISION_MODEL, "invalid_request_error"),
        (400, BAD_SCHEMA, "invalid_value"),
        (422, error_body("Unprocessable entity."), None),
    ],
)
async def test_openai_configuration_errors_stop_and_are_not_retried(
    code: int, body: dict[str, Any], expected_code: str | None
) -> None:
    server = Server(httpx2.Response(code, json=body))
    waits = Recorder()
    with pytest.raises(ProviderConfigError) as caught:
        await provider(server, waits).complete(request(model="gpt-6.1-sol"))
    error = caught.value
    assert server.call_count == 1 and waits.sleeps == []
    assert (error.provider, error.status, error.code) == ("openai", code, expected_code)
    assert isinstance(error, ProviderAccountError)
    assert not isinstance(error, ProviderBadResponse | ProviderRequestRejected)
    where = f"HTTP {code}, {expected_code}" if expected_code else f"HTTP {code}"
    assert str(error) == (
        f"openai rejected the request ({where}): {body['error']['message']}; {CONFIG_HINT}"
    )


@pytest.mark.parametrize(
    ("code", "body", "expected_code"),
    [
        (400, UNSUPPORTED_IMAGE, "invalid_image_format"),
        (400, INVALID_IMAGE, "invalid_request_error"),
        (413, error_body("Request too large."), None),
    ],
)
async def test_openai_input_rejections_fail_the_input_and_are_not_retried(
    code: int, body: dict[str, Any], expected_code: str | None
) -> None:
    server = Server(httpx2.Response(code, json=body))
    waits = Recorder()
    with pytest.raises(ProviderRequestRejected) as caught:
        await provider(server, waits).complete(request())
    error = caught.value
    assert server.call_count == 1 and waits.sleeps == []
    assert (error.provider, error.status, error.code) == ("openai", code, expected_code)
    assert not isinstance(error, ProviderAccountError | ProviderBadResponse)
    assert str(error).startswith(f"openai rejected the input (HTTP {code}")
    assert body["error"]["message"] in str(error)


@pytest.mark.parametrize(
    ("code", "body", "expected", "expected_code"),
    [
        (404, AZURE_DEPLOYMENT_NOT_FOUND, ProviderConfigError, "DeploymentNotFound"),
        (400, AZURE_OPERATION_NOT_SUPPORTED, ProviderConfigError, "OperationNotSupported"),
        (400, AZURE_UNRECOGNIZED_ARGUMENT, ProviderConfigError, "invalid_request_error"),
        (400, UNSUPPORTED_PARAMETER, ProviderConfigError, "unsupported_parameter"),
        (400, AZURE_CONTENT_FILTER, ProviderRequestRejected, "content_filter"),
    ],
)
async def test_azure_refusals_are_classified_by_code_and_message(
    code: int,
    body: dict[str, Any],
    expected: type[ProviderConfigError | ProviderRequestRejected],
    expected_code: str,
) -> None:
    server = Server(httpx2.Response(code, json=body))
    azure = AzureOpenAIProvider.from_settings(
        azure_settings(), http_client=server.client(), jitter=lambda: 0.0, sleep=Recorder().sleep
    )
    assert azure is not None
    with pytest.raises(expected) as caught:
        await azure.complete(request(model="gpt-6.1-sol"))
    error = caught.value
    assert type(error) is expected and server.call_count == 1
    assert (error.provider, error.status, error.code) == ("azure", code, expected_code)
    assert body["error"]["message"] in str(error)


async def test_azure_deployment_not_found_reads_as_configuration() -> None:
    server = Server(httpx2.Response(404, json=AZURE_DEPLOYMENT_NOT_FOUND))
    azure = AzureOpenAIProvider.from_settings(azure_settings(), http_client=server.client())
    assert azure is not None
    with pytest.raises(ProviderConfigError) as caught:
        await azure.complete(request())
    text = str(caught.value)
    assert text.startswith("azure rejected the request (HTTP 404, DeploymentNotFound): ")
    assert text.endswith("; check the model and deployment settings")
    assert "account" not in text


async def test_a_configuration_error_never_carries_a_key() -> None:
    """The provider's message is kept, a key in it is not (OpenAI keys and Azure's 32-hex
    keys), as for account errors."""
    azure_key = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
    body = error_body(
        f"Unsupported parameter for key sk-proj-abcdEFGH1234wxyz and {azure_key}.",
        code="unsupported_parameter",
    )
    server = Server(httpx2.Response(400, json=body))
    with pytest.raises(ProviderConfigError) as caught:
        await provider(server).complete(request())
    text = f"{caught.value} {caught.value.args}"
    assert "sk-proj" not in text and "abcdEFGH" not in text and azure_key not in text
    assert text.count("[redacted]") >= 2


def test_classify_rejection_edges() -> None:
    """Codes match whatever their case (Azure's inner ``ResponsibleAIPolicyViolation``);
    a bare mention of images is not an input refusal; 408/409 are transient."""
    inner = classify_rejection(400, [None, None, "ResponsibleAIPolicyViolation"], "x", provider="a")
    assert isinstance(inner, ProviderRequestRejected)
    assert inner.code == "ResponsibleAIPolicyViolation"
    vision = classify_rejection(
        400, [], "image_url is only supported by certain models", provider="a"
    )
    assert isinstance(vision, ProviderConfigError) and vision.code is None
    assert isinstance(classify_rejection(408, [], "timeout", provider="a"), ProviderUnavailable)
    assert isinstance(classify_rejection(409, [], "conflict", provider="a"), ProviderUnavailable)
    too_large = classify_rejection(413, ["request_too_large"], "too big", provider="a")
    assert isinstance(too_large, ProviderRequestRejected)
