"""The worker's provider probes (basis.ops.probes): free GET lookups only, bounded, every
failure sanitized (docs/api/OPS_HEALTH.md section 4)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from basis.config import Settings
from basis.ops import probes
from basis.ops.probes import (
    GetOnlyTransport,
    Probe,
    ProbeMethodRefused,
    build_probes,
    run_probe,
    run_probes,
)

OPENAI_KEY = "sk-test-OPENAI-SECRET"  # pragma: allowlist secret
ANTHROPIC_KEY = "sk-ant-test-ANTHROPIC-SECRET"  # pragma: allowlist secret
AZURE_KEY = "azure-test-SECRET"  # pragma: allowlist secret
DI_KEY = "di-test-SECRET"  # pragma: allowlist secret
REPLICATE_TOKEN = "r8_test-REPLICATE-SECRET"  # pragma: allowlist secret
SECRETS = (OPENAI_KEY, ANTHROPIC_KEY, AZURE_KEY, DI_KEY, REPLICATE_TOKEN)
PROVIDER_ERROR_TEXT = "Incorrect API key provided: sk-test-OPENAI-SECRET at 10.0.0.7"


def configured(settings: Settings, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "openai_api_key": SecretStr(OPENAI_KEY),
        "anthropic_api_key": SecretStr(ANTHROPIC_KEY),
        "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
        "azure_openai_api_key": SecretStr(AZURE_KEY),
        "azure_openai_deployment_name": "prod-gpt",
        "azure_document_intelligence_endpoint": "https://example-di.cognitiveservices.azure.com",
        "azure_document_intelligence_key": SecretStr(DI_KEY),
        "replicate_api_token": SecretStr(REPLICATE_TOKEN),
        **overrides,
    }
    return settings.model_copy(update=values)


class Recorder:
    """A mock transport: answers by URL and records every request."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._answer(request)

        return httpx.MockTransport(handle)


def ok_for(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/account"):
        return httpx.Response(200, json={"type": "organization", "username": "basis"})
    if path.endswith("/openai/models"):
        return httpx.Response(200, json={"data": [{"id": "gpt-x"}], "object": "list"})
    if path.endswith("/documentintelligence/info"):
        return httpx.Response(200, json={"customDocumentModels": {"count": 0, "limit": 250}})
    if path.endswith("/health-check"):
        return httpx.Response(200, json={"status": "READY", "setup": {"status": "succeeded"}})
    return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})


def all_probes(settings: Settings) -> list[Probe]:
    return build_probes(
        configured(settings),
        openai_models=["gpt-6.1-sol", "gpt-6-luna"],
        anthropic_models=["claude-sonnet-5-5"],
        azure_openai=True,
        localizer_adapter="replicate",
        segmenter_adapter="replicate",
    )


def test_each_probe_is_the_documented_free_lookup(settings: Settings) -> None:
    built = {(p.target, p.label): p for p in all_probes(settings)}
    urls = {key: p.url for key, p in built.items()}
    assert urls[("openai", "gpt-6.1-sol")] == "https://api.openai.com/v1/models/gpt-6.1-sol"
    assert urls[("openai", "gpt-6-luna")] == "https://api.openai.com/v1/models/gpt-6-luna"
    assert urls[("anthropic", "claude-sonnet-5-5")] == (
        "https://api.anthropic.com/v1/models/claude-sonnet-5-5"
    )
    assert urls[("azure_openai", "resource")] == (
        "https://example-resource.openai.azure.com/openai/models?api-version=2024-10-21"
    )
    assert urls[("azure_di", "resource")] == (
        "https://example-di.cognitiveservices.azure.com/documentintelligence/info"
        "?api-version=2024-11-30"
    )
    assert urls[("replicate", "account")] == "https://api.replicate.com/v1/account"
    sam3 = next(p for (t, _), p in built.items() if t == "sam3")
    assert sam3.url.startswith("https://api.replicate.com/v1/models/vufinder/sam3/versions/")
    mask = next(p for (t, _), p in built.items() if t == "mask2former")
    assert mask.url.startswith("https://api.replicate.com/v1/models/oitoito/mask2former/versions/")
    # Each says what it does not prove.
    for probe in built.values():
        assert "not" in probe.establishes.lower() or "no " in probe.establishes.lower()


def test_no_credentials_means_no_probe(settings: Settings) -> None:
    bare = settings.model_copy(
        update={
            "openai_api_key": None,
            "anthropic_api_key": None,
            "azure_openai_api_key": None,
            "azure_document_intelligence_key": None,
            "replicate_api_token": None,
        }
    )
    assert (
        build_probes(
            bare,
            openai_models=["gpt-6.1-sol"],
            anthropic_models=["claude-sonnet-5-5"],
            azure_openai=True,
            localizer_adapter="replicate",
            segmenter_adapter="replicate",
        )
        == []
    )


def test_replicate_is_probed_only_when_something_runs_on_it(settings: Settings) -> None:
    built = build_probes(configured(settings), localizer_adapter="self-hosted")
    assert {p.target for p in built} == {"azure_di"}


def test_the_self_hosted_localizer_is_probed_only_when_asked(settings: Settings) -> None:
    hosted = configured(settings, localizer_url="https://sam3.example.run.app")
    assert "sam3" not in {p.target for p in build_probes(hosted, localizer_adapter="self-hosted")}
    asked = hosted.model_copy(update={"ops_probe_localizer": True})
    [cog] = [p for p in build_probes(asked, localizer_adapter="self-hosted") if p.target == "sam3"]
    assert cog.url == "https://sam3.example.run.app/health-check"


async def test_every_request_is_a_get_and_healthy_on_the_documented_body(
    settings: Settings,
) -> None:
    recorder = Recorder(ok_for)
    results = await run_probes(all_probes(settings), transport=recorder.transport())
    assert {r.method for r in recorder.requests} == {"GET"}
    assert len(recorder.requests) == 8
    assert {t: r["status"] for t, r in results.items()} == {
        "openai": "healthy",
        "anthropic": "healthy",
        "azure_openai": "healthy",
        "azure_di": "healthy",
        "replicate": "healthy",
        "sam3": "healthy",
        "mask2former": "healthy",
    }
    assert len(results["openai"]["parts"]) == 2
    sent = {r.url.host: dict(r.headers) for r in recorder.requests}
    assert sent["api.anthropic.com"]["anthropic-version"] == "2023-06-01"


async def test_a_post_is_refused_before_it_is_sent() -> None:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=GetOnlyTransport(httpx.MockTransport(handle))) as c:
        with pytest.raises(ProbeMethodRefused):
            await c.post("https://api.openai.com/v1/chat/completions", json={})
        await c.get("https://api.openai.com/v1/models/x")
    assert [r.method for r in sent] == ["GET"]


def single(settings: Settings) -> Probe:
    [probe] = build_probes(configured(settings), openai_models=["gpt-6.1-sol"])[:1]
    return probe


@pytest.mark.parametrize(
    ("status", "body", "expected", "detail"),
    [
        (
            401,
            {"error": {"message": PROVIDER_ERROR_TEXT}},
            "down",
            "credentials rejected (HTTP 401)",
        ),
        (
            403,
            {"error": {"message": PROVIDER_ERROR_TEXT}},
            "down",
            "credentials rejected (HTTP 403)",
        ),
        (404, {"error": {"message": PROVIDER_ERROR_TEXT}}, "down", "not found"),
        (429, {"error": {"message": PROVIDER_ERROR_TEXT}}, "degraded", "rate limited"),
        (503, {"error": {"message": PROVIDER_ERROR_TEXT}}, "down", "provider error (HTTP 503)"),
        (418, "teapot", "down", "unexpected answer (HTTP 418)"),
        (200, {"object": "model"}, "degraded", "malformed response"),
        (200, ["not", "an", "object"], "degraded", "malformed response"),
    ],
)
async def test_failures_are_sanitized(
    settings: Settings, status: int, body: Any, expected: str, detail: str
) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    async with probes.ProbeClient(transport) as client:
        result = await run_probe(client, single(settings))
    assert result.status == expected
    assert detail in result.detail
    text = json.dumps(result.to_json())
    assert "Incorrect API key" not in text and "10.0.0.7" not in text
    assert not any(secret in text for secret in SECRETS)


async def test_a_non_json_200_is_malformed(settings: Settings) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>proxy</html>"))
    async with probes.ProbeClient(transport) as client:
        result = await run_probe(client, single(settings))
    assert (result.status, result.http_status) == ("degraded", 200)
    assert "malformed" in result.detail


async def test_a_timeout_is_bounded_and_reported(settings: Settings) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"id": "x"})

    async with probes.ProbeClient(httpx.MockTransport(slow)) as client:
        started = asyncio.get_running_loop().time()
        result = await run_probe(client, single(settings), timeout_s=0.2)
    assert asyncio.get_running_loop().time() - started < 2
    assert result.status == "down" and "timed out" in result.detail


async def test_a_network_error_names_no_address(settings: Settings) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 61] Connection refused to 10.0.0.7:443")

    async with probes.ProbeClient(httpx.MockTransport(refuse)) as client:
        result = await run_probe(client, single(settings))
    assert result.status == "down"
    assert result.detail == "network error: the endpoint could not be reached"


@pytest.mark.parametrize(
    ("server", "expected"),
    [
        ("READY", "healthy"),
        ("BUSY", "healthy"),
        ("STARTING", "degraded"),
        ("SETUP_FAILED", "down"),
        ("DEFUNCT", "down"),
        ("SOMETHING_NEW", "degraded"),
    ],
)
async def test_the_cog_health_check(settings: Settings, server: str, expected: str) -> None:
    asked = settings.model_copy(
        update={"localizer_url": "https://sam3.example", "ops_probe_localizer": True}
    )
    [probe] = build_probes(asked, localizer_adapter="self-hosted")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": server}))
    async with probes.ProbeClient(transport) as client:
        result = await run_probe(client, probe)
    assert result.status == expected
    if server == "READY":
        assert "model is loaded" in result.detail


async def test_combined_targets_take_the_worst_part_and_the_oldest_time(
    settings: Settings,
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gpt-6-luna"):
            return httpx.Response(404, json={})
        return ok_for(request)

    stamps = [datetime(2026, 10, 31, 12, 0, 1, tzinfo=UTC)]

    def clock() -> datetime:
        # Each probe finishes one second after the previous: the first is the oldest.
        stamps.append(stamps[-1].replace(second=stamps[-1].second + 1))
        return stamps[-2]

    built = build_probes(configured(settings), openai_models=["gpt-6.1-sol", "gpt-6-luna"])
    results = await run_probes(built, transport=httpx.MockTransport(answer), clock=clock)
    openai = results["openai"]
    assert openai["status"] == "down"
    assert openai["checked_at"] == "2026-10-31T12:00:01+00:00"
    assert "gpt-6-luna: not found" in openai["detail"]
