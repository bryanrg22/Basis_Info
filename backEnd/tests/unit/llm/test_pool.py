"""``ProviderPool``: per-call fallback on 429/5xx only, concurrency cap, no global state."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import openai as openai_sdk
import pytest

from basis.config import Settings
from basis.llm import ChatProvider, ScriptedProvider, ScriptedTurn
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
)
from basis.llm.pool import ProviderPool
from basis.llm.provider import ChatRequest, ChatResponse, Message, Usage
from basis.llm.providers import OpenAIProvider


def request() -> ChatRequest:
    return ChatRequest(
        model="gpt-4o-mini",
        system="s",
        messages=(Message(role="user", content="hi"),),
        prompt_version="p/v1",
        study_id="s-1",
        activity="analyze_photo",
    )


def named(name: str, turns: list[ScriptedTurn]) -> ScriptedProvider:
    provider = ScriptedProvider(turns)
    provider.name = name
    return provider


def text(value: str) -> ScriptedTurn:
    return ScriptedTurn(text=value)


async def test_primary_answers_when_healthy() -> None:
    primary, secondary = named("azure", [text("a")]), named("openai", [])
    pool = ProviderPool([primary, secondary])
    assert isinstance(pool, ChatProvider) and pool.name == "azure+openai"
    assert (await pool.complete(request())).text == "a"
    assert secondary.requests == [] and pool.fallbacks == 0


@pytest.mark.parametrize(
    "error",
    [
        ProviderRateLimited("429", provider="azure", status=429),
        ProviderUnavailable("503", provider="azure", status=503),
    ],
)
async def test_429_and_5xx_fall_back_for_that_call_only(error: Exception) -> None:
    primary = named("azure", [ScriptedTurn(raise_error=error), text("primary again")])
    secondary = named("openai", [text("secondary")])
    pool = ProviderPool([primary, secondary])
    assert (await pool.complete(request())).text == "secondary"
    # No sticky switch (the legacy module-global flag): the next call tries the primary.
    assert (await pool.complete(request())).text == "primary again"
    assert pool.fallbacks == 1 and len(secondary.requests) == 1


@pytest.mark.parametrize(
    "error",
    [
        ProviderBadResponse("schema", provider="azure", status=None),
        ProviderBadResponse("400", provider="azure", status=400),
        ProviderUnavailable("timed out", provider="azure", status=None),
        RuntimeError("bug"),
    ],
)
async def test_other_errors_do_not_fall_back(error: Exception) -> None:
    secondary = named("openai", [])
    pool = ProviderPool([named("azure", [ScriptedTurn(raise_error=error)]), secondary])
    with pytest.raises(type(error)):
        await pool.complete(request())
    assert secondary.requests == []


async def test_without_a_secondary_the_error_is_raised() -> None:
    error = ProviderRateLimited("429", provider="openai", status=429)
    pool = ProviderPool([named("openai", [ScriptedTurn(raise_error=error)])])
    with pytest.raises(ProviderRateLimited):
        await pool.complete(request())


async def test_secondary_failure_propagates() -> None:
    first = ProviderRateLimited("429", provider="azure", status=429)
    second = ProviderUnavailable("503", provider="openai", status=503)
    pool = ProviderPool(
        [
            named("azure", [ScriptedTurn(raise_error=first)]),
            named("openai", [ScriptedTurn(raise_error=second)]),
        ]
    )
    with pytest.raises(ProviderUnavailable) as caught:
        await pool.complete(request())
    assert caught.value.provider == "openai"


class Slow:
    """Counts how many calls are in flight at once."""

    name = "slow"

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def complete(self, req: ChatRequest) -> ChatResponse:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return ChatResponse(
            usage=Usage(input_tokens=1, output_tokens=1), model="m", provider=self.name
        )


async def test_concurrency_is_capped() -> None:
    slow = Slow()
    pool = ProviderPool([slow], concurrency=3)
    await asyncio.gather(*(pool.complete(request()) for _ in range(10)))
    assert slow.peak == 3


def test_construction_rules() -> None:
    with pytest.raises(ProviderNotConfigured):
        ProviderPool([])
    with pytest.raises(ValueError):
        ProviderPool([Slow()], concurrency=0)
    # Three providers are allowed (Azure, OpenAI and Anthropic); a call still has one
    # primary and one fallback among the providers that serve its model.
    assert ProviderPool([Slow(), Slow(), Slow()]).name == "slow+slow+slow"


# --------------------------------------------------------------------------- routing by model


class Family(ScriptedProvider):
    """A scripted provider that serves only one model family (like the real ones)."""

    def __init__(self, name: str, turns: list[ScriptedTurn], *, claude: bool) -> None:
        super().__init__(turns)
        self.name = name
        self.claude = claude

    def accepts(self, model: str) -> bool:
        return model.startswith("claude") is self.claude


def claude_request() -> ChatRequest:
    return request().model_copy(update={"model": "claude-opus-5-5"})


async def test_a_claude_request_goes_to_anthropic_even_when_openai_is_primary() -> None:
    openai = Family("openai", [text("gpt")], claude=False)
    claude = Family("anthropic", [text("claude")], claude=True)
    pool = ProviderPool([openai, claude])
    assert pool.candidates("claude-fable-5-1") == [claude]
    assert pool.candidates("gpt-4.1") == [openai]
    assert (await pool.complete(claude_request())).text == "claude"
    assert (await pool.complete(request())).text == "gpt"
    assert [r.model for r in claude.requests] == ["claude-opus-5-5"]
    assert [r.model for r in openai.requests] == [request().model]


async def test_fallback_never_crosses_model_families() -> None:
    overloaded = ProviderUnavailable("overloaded", provider="anthropic", status=529)
    claude = Family("anthropic", [ScriptedTurn(raise_error=overloaded)], claude=True)
    openai = Family("openai", [text("gpt")], claude=False)
    pool = ProviderPool([claude, openai])
    with pytest.raises(ProviderUnavailable) as caught:
        await pool.complete(claude_request())
    assert caught.value.provider == "anthropic"
    assert openai.requests == [] and pool.fallbacks == 0
    # And a gpt request that fails on OpenAI is not sent to Anthropic either.
    openai_down = Family(
        "openai",
        [ScriptedTurn(raise_error=ProviderUnavailable("x", provider="openai", status=503))],
        claude=False,
    )
    pool = ProviderPool([openai_down, Family("anthropic", [text("no")], claude=True)])
    with pytest.raises(ProviderUnavailable):
        await pool.complete(request())
    assert pool.fallbacks == 0


async def test_fallback_within_a_family_still_works_with_anthropic_configured() -> None:
    azure = Family(
        "azure",
        [ScriptedTurn(raise_error=ProviderUnavailable("x", provider="azure", status=503))],
        claude=False,
    )
    openai = Family("openai", [text("secondary")], claude=False)
    claude = Family("anthropic", [], claude=True)
    pool = ProviderPool([azure, openai, claude])
    assert (await pool.complete(request())).text == "secondary"
    assert pool.fallbacks == 1 and claude.requests == []


async def test_a_model_nobody_serves_is_not_configured() -> None:
    pool = ProviderPool([Family("anthropic", [text("x")], claude=True)])
    with pytest.raises(ProviderNotConfigured, match="serves model"):
        await pool.complete(request())


async def test_a_provider_without_accepts_serves_every_model() -> None:
    pool = ProviderPool([named("any", [text("a"), text("b")])])
    assert (await pool.complete(request())).text == "a"
    assert (await pool.complete(claude_request())).text == "b"


def settings(**values: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "openai_api_key": None,
        "azure_openai_endpoint": None,
        "azure_openai_api_key": None,
        "anthropic_api_key": None,
    }
    base.update(values)
    return Settings(**base)


def test_from_settings() -> None:
    assert ProviderPool.from_settings(settings()) is None
    pool = ProviderPool.from_settings(settings(openai_api_key="sk-test", llm_concurrency=2))
    assert pool is not None and pool.name == "openai" and pool.concurrency == 2
    claude = ProviderPool.from_settings(settings(anthropic_api_key="sk-ant-test"))
    assert claude is not None and claude.name == "anthropic"
    assert claude.candidates("gpt-4.1") == [] and len(claude.candidates("claude-opus-5-5")) == 1
    everything = ProviderPool.from_settings(
        settings(
            openai_api_key="sk-test",  # pragma: allowlist secret
            anthropic_api_key="sk-ant-test",  # pragma: allowlist secret
            azure_openai_endpoint="https://x.openai.azure.com",
            azure_openai_api_key="k",
            azure_openai_deployment_name="dep",
            llm_primary="anthropic",
        )
    )
    assert everything is not None
    assert [p.name for p in everything.providers] == ["anthropic", "azure", "openai"]
    assert [p.name for p in everything.candidates("gpt-4.1")] == ["azure", "openai"]
    assert [p.name for p in everything.candidates("claude-fable-5-1")] == ["anthropic"]
    both = ProviderPool.from_settings(
        settings(
            openai_api_key="sk-test",  # pragma: allowlist secret
            azure_openai_endpoint="https://x.openai.azure.com",
            azure_openai_api_key="k",
            azure_openai_deployment_name="dep",
        )
    )
    assert both is not None and [p.name for p in both.providers] == ["azure", "openai"]


# --------------------------------------------------------------------------- account errors


@pytest.mark.parametrize(
    "error",
    [
        ProviderAccountError("quota", provider="azure", status=429, code="insufficient_quota"),
        ProviderAccountError("bad key", provider="azure", status=401, code="invalid_api_key"),
        ProviderAccountError("no access", provider="azure", status=403),
    ],
)
async def test_an_account_error_never_falls_back(error: ProviderAccountError) -> None:
    """Not even a 429: the secondary would answer with another account (and, on Azure,
    possibly another deployment than the model asked for), hiding the outage."""
    primary = named("azure", [ScriptedTurn(raise_error=error), text("after the fix")])
    secondary = named("openai", [text("must not answer")])
    pool = ProviderPool([primary, secondary])
    with pytest.raises(ProviderAccountError) as caught:
        await pool.complete(request())
    assert caught.value is error
    assert secondary.requests == [] and pool.fallbacks == 0
    # Nothing sticky: once the account is fixed the primary serves again.
    assert (await pool.complete(request())).text == "after the fix"


async def test_an_account_error_on_a_claude_request_stays_on_anthropic() -> None:
    credit = ProviderAccountError("credit balance is too low", provider="anthropic", status=400)
    claude = Family("anthropic", [ScriptedTurn(raise_error=credit)], claude=True)
    openai = Family("openai", [text("gpt")], claude=False)
    pool = ProviderPool([claude, openai])
    with pytest.raises(ProviderAccountError):
        await pool.complete(claude_request())
    assert openai.requests == [] and pool.fallbacks == 0


def sdk_provider(name: str, responses: list[httpx2.Response]) -> tuple[OpenAIProvider, list[int]]:
    """A real :class:`OpenAIProvider` over a mock transport; returns it and its call log."""
    calls: list[int] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return responses.pop(0) if len(responses) > 1 else responses[0]

    client = openai_sdk.AsyncOpenAI(
        api_key="test-key",  # pragma: allowlist secret
        base_url="https://api.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    async def no_wait(_: float) -> None:
        return None

    provider = OpenAIProvider(client, timeout_s=5.0, jitter=lambda: 0.0, sleep=no_wait)
    provider.name = name
    return provider, calls


def completion_json(text_value: str) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text_value},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


async def test_out_of_quota_is_one_call_no_retry_no_fallback_end_to_end() -> None:
    """Through the real SDK and mapping: OpenAI's out-of-quota 429 used to be retried once
    and then sent to Azure; now it is one call and an account error."""
    quota = httpx2.Response(
        429,
        json={
            "error": {
                "message": "You exceeded your current quota.",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        },
    )
    primary, primary_calls = sdk_provider("openai", [quota])
    secondary, secondary_calls = sdk_provider(
        "azure", [httpx2.Response(200, json=completion_json("x"))]
    )
    pool = ProviderPool([primary, secondary])
    with pytest.raises(ProviderAccountError) as caught:
        await pool.complete(request())
    assert (caught.value.provider, caught.value.code) == ("openai", "insufficient_quota")
    assert len(primary_calls) == 1 and secondary_calls == [] and pool.fallbacks == 0


async def test_rate_limits_and_5xx_still_retry_then_fall_back_end_to_end() -> None:
    """Regression: a plain 429 or a 503 is retried once by the provider, then the call
    falls back to the secondary, exactly as before."""
    for status in (429, 503):
        failing = httpx2.Response(
            status, json={"error": {"message": "busy", "code": "rate_limit_exceeded"}}
        )
        primary, primary_calls = sdk_provider("openai", [failing])
        secondary, secondary_calls = sdk_provider(
            "azure", [httpx2.Response(200, json=completion_json("from azure"))]
        )
        pool = ProviderPool([primary, secondary])
        assert (await pool.complete(request())).text == "from azure"
        assert len(primary_calls) == 2 and len(secondary_calls) == 1 and pool.fallbacks == 1
