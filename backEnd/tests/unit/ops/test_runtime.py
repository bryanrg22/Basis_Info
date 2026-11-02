"""The worker's runtime report (basis.ops.runtime): what the worker actually runs, with no
secret, endpoint or host in it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from basis.activities.deps import Deps
from basis.config import Settings
from basis.domain.cost_table import CostTable
from basis.llm.pool import ProviderPool
from basis.ops.runtime import WorkerRuntime
from basis.retrieval.embed import ModelLoad
from basis.worker.main import pipeline_models, room_config

OPENAI_KEY = "sk-test-RUNTIME-SECRET"  # pragma: allowlist secret
AZURE_KEY = "azure-RUNTIME-SECRET"  # pragma: allowlist secret
ANTHROPIC_KEY = "sk-ant-RUNTIME-SECRET"  # pragma: allowlist secret
REPLICATE_TOKEN = "r8_RUNTIME-SECRET"  # pragma: allowlist secret
AZURE_ENDPOINT = "https://secret-resource-name.openai.azure.com"


@dataclass
class FakeLocalizer:
    provider: str


def worker_settings(settings: Settings, **overrides: Any) -> Settings:
    return settings.model_copy(
        update={
            "azure_openai_endpoint": AZURE_ENDPOINT,
            "azure_openai_api_key": SecretStr(AZURE_KEY),
            "azure_openai_deployment_name": "basis-default",
            "azure_openai_vision_deployment": "basis-vision",
            "openai_api_key": SecretStr(OPENAI_KEY),
            "anthropic_api_key": SecretStr(ANTHROPIC_KEY),
            "replicate_api_token": SecretStr(REPLICATE_TOKEN),
            "llm_primary": "azure",
            "git_sha": "0123456789abcdef",
            # The production pipeline (tests/conftest.py pins a smaller one).
            "vision_model": "gpt-6.1-sol",
            "vision_photo_mode": "decomposed",
            "vision_count_fusion": "per_class",
            "room_reconcile": True,
            "extraction_method": "grounded",
            **overrides,
        }
    )


def runtime_for(settings: Settings, **deps: Any) -> WorkerRuntime:
    built = Deps(
        sessionmaker=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        bucket="unused",
        settings=settings,
        llm=ProviderPool.from_settings(settings),
        **deps,
    )
    return WorkerRuntime(built, pipeline_models(settings, room_config(settings)), cost_table=None)


def route(runtime: WorkerRuntime, setting: str) -> dict[str, Any]:
    return next(r for r in runtime.routes if setting in r["settings"])


def test_routes_name_the_effective_provider_and_deployment(settings: Settings) -> None:
    runtime = runtime_for(worker_settings(settings))
    vision = route(runtime, "VISION_MODEL")
    assert vision["model"] == "gpt-6.1-sol"
    # Azure first (LLM_PRIMARY), sent to the vision deployment; OpenAI is the fallback.
    assert vision["route"][0] == {
        "provider": "azure",
        "service": "azure_openai",
        "target": "basis-vision",
    }
    assert vision["route"][1]["service"] == "openai"
    classify = route(runtime, "CLASSIFY_MODEL")
    assert [p["service"] for p in classify["route"]] == ["anthropic"]


def test_fusion_and_the_room_step_follow_the_effective_settings(settings: Settings) -> None:
    on = runtime_for(worker_settings(settings))
    assert any("VISION_FUSION_MODEL" in r["settings"] for r in on.routes)
    single = runtime_for(worker_settings(settings, vision_photo_mode="single"))
    assert not any("VISION_FUSION_MODEL" in r["settings"] for r in single.routes)
    assert single.report()["steps"]["count_fusion_active"] is False
    off = runtime_for(worker_settings(settings, room_reconcile=False))
    assert not any("ROOM_MODEL" in r["settings"] for r in off.routes)


def test_probes_cover_only_routed_models_and_used_adapters(settings: Settings) -> None:
    runtime = runtime_for(worker_settings(settings), localizer=FakeLocalizer("replicate"))
    targets = {(p.target, p.label) for p in runtime._probe_list}
    anthropic = {label for target, label in targets if target == "anthropic"}
    assert anthropic == {
        m
        for m in pipeline_models(runtime.settings, room_config(runtime.settings))
        if m.startswith("claude")
    }
    assert ("replicate", "account") in targets
    assert any(t == "sam3" for t, _ in targets)
    assert not any(t == "mask2former" for t, _ in targets)  # surface crops are off
    no_localizer = runtime_for(worker_settings(settings))
    assert not any(p.target in ("replicate", "sam3") for p in no_localizer._probe_list)


def test_the_report_carries_no_secret_endpoint_or_host(settings: Settings) -> None:
    runtime = runtime_for(
        worker_settings(settings, localizer_url="https://sam3-internal.a.run.app"),
        localizer=FakeLocalizer("cog"),
    )
    runtime.embedder_warmed(
        ModelLoad(
            model="sentence-transformers/all-MiniLM-L6-v2",
            revision="abc",
            source="cache",
            seconds=3.2,
        ),
        "unused",
    )
    runtime.reference_warmed(None)
    text = json.dumps(runtime.report())
    for secret in (OPENAI_KEY, AZURE_KEY, ANTHROPIC_KEY, REPLICATE_TOKEN):
        assert secret not in text
    assert "secret-resource-name" not in text and "sam3-internal" not in text
    report = runtime.report()
    assert report["localizer"] == {
        "adapter": "self-hosted",
        "version": settings.sam3_replicate_version[:12],
        "probed": False,
        "hosting": "Google Cloud Run",  # a label for the run.app host, never the URL
    }
    assert report["build"]["hosting"] in ("Local process", "Docker container", "Google Cloud Run")
    assert report["embedder"]["status"] == "ready" and report["embedder"]["source"] == "cache"
    assert report["reference_index"]["status"] == "no_corpus"
    assert report["build"]["git_sha"] == "0123456789abcdef"
    assert report["providers_configured"] == ["anthropic", "azure_openai", "openai"]


def test_the_cost_table_is_reported_by_name_and_size(settings: Settings) -> None:
    table = CostTable(name="RSMeans 2026 subset", base_year=2026, rows=dict.fromkeys("abc"))  # type: ignore[arg-type]
    built = Deps(
        sessionmaker=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        bucket="unused",
        settings=settings,
    )
    runtime = WorkerRuntime(built, {}, cost_table=table)
    assert runtime.report()["cost_table"] == {
        "name": "RSMeans 2026 subset",
        "rows": 3,
        "base_year": 2026,
    }
    assert runtime.report()["providers_configured"] == []


async def test_probe_once_stores_sanitized_results(settings: Settings) -> None:
    built = Deps(
        sessionmaker=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        bucket="unused",
        settings=worker_settings(settings),
        llm=ProviderPool.from_settings(worker_settings(settings)),
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": OPENAI_KEY}))
    runtime = WorkerRuntime(
        built,
        pipeline_models(built.settings, room_config(built.settings)),
        cost_table=None,
        transport=transport,
    )
    await runtime.probe_once()
    assert runtime.probes["anthropic"]["status"] == "down"
    assert OPENAI_KEY not in json.dumps(runtime.probes)


async def test_probe_loop_does_nothing_when_probes_are_off(settings: Settings) -> None:
    runtime = runtime_for(worker_settings(settings, ops_provider_probe_seconds=0))
    await runtime.probe_loop()  # returns at once
    assert runtime.probes == {}
