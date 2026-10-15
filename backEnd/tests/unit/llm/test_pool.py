"""``ProviderPool``: per-call fallback on 429/5xx only, concurrency cap, no global state."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from basis.config import Settings
from basis.llm import ChatProvider, ScriptedProvider, ScriptedTurn
from basis.llm.errors import (
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
)
from basis.llm.pool import ProviderPool
from basis.llm.provider import ChatRequest, ChatResponse, Message, Usage


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
        ProviderPool([Slow(), Slow(), Slow()])
    with pytest.raises(ValueError):
        ProviderPool([Slow()], concurrency=0)


def settings(**values: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "openai_api_key": None,
        "azure_openai_endpoint": None,
        "azure_openai_api_key": None,
    }
    base.update(values)
    return Settings(**base)


def test_from_settings() -> None:
    assert ProviderPool.from_settings(settings()) is None
    pool = ProviderPool.from_settings(settings(openai_api_key="sk-test", llm_concurrency=2))
    assert pool is not None and pool.name == "openai" and pool.concurrency == 2
    both = ProviderPool.from_settings(
        settings(
            openai_api_key="sk-test",  # pragma: allowlist secret
            azure_openai_endpoint="https://x.openai.azure.com",
            azure_openai_api_key="k",
            azure_openai_deployment_name="dep",
        )
    )
    assert both is not None and [p.name for p in both.providers] == ["azure", "openai"]
