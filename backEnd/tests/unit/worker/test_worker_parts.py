"""Worker pieces: ``/health``, worker versioning config, the activity logging context, the
composition root's check of the per-photo vision prompts."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.common import VersioningBehavior
from temporalio.testing import ActivityEnvironment
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput

from basis.activities.deps import Deps
from basis.config import ConfigError, Settings
from basis.db.engine import create_engine
from basis.llm.pool import ProviderPool, unserved_models
from basis.llm.prompts import PromptError
from basis.obs.logging import get_context
from basis.retrieval import Retrieval
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.embed import HashEmbedder, ModelLoad
from basis.vision import room_step
from basis.worker.health import HealthServer
from basis.worker.interceptors import LoggingInterceptor
from basis.worker.main import (
    MAX_HEARTBEAT_THROTTLE,
    SHUTDOWN_GRACE,
    build_deps,
    check_models_served,
    deployment_config,
    parse_args,
    pipeline_models,
    warm_embedder,
)


async def http(port: int, request: bytes) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


async def test_health_reports_starting_ok_and_draining() -> None:
    server = HealthServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        get = b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n"
        assert await http(server.port, get) == (503, b'{"status": "starting"}')
        server.status = "ok"
        assert await http(server.port, get) == (200, b'{"status": "ok"}')
        server.status = "draining"
        assert (await http(server.port, get))[0] == 503
        assert (await http(server.port, b"GET /other HTTP/1.1\r\n\r\n"))[0] == 404
        assert (await http(server.port, b"POST /health HTTP/1.1\r\n\r\n"))[0] == 404
        server.status = "ok"
        status, body = await http(server.port, b"HEAD /health HTTP/1.1\r\n\r\n")
        assert (status, body) == (200, b"")
    finally:
        await server.stop()


def test_versioning_is_off_by_default(settings: Settings) -> None:
    assert deployment_config(settings) is None


def test_versioning_uses_the_deployment_and_build_id(settings: Settings) -> None:
    on = settings.model_copy(
        update={
            "temporal_worker_versioning": True,
            "temporal_deployment_name": "basis-worker",
            "temporal_build_id": "abc123",
        }
    )
    config = deployment_config(on)
    assert config is not None and config.use_worker_versioning
    assert (config.version.deployment_name, config.version.build_id) == ("basis-worker", "abc123")
    assert config.default_versioning_behavior is VersioningBehavior.AUTO_UPGRADE
    fallback = deployment_config(on.model_copy(update={"temporal_build_id": None}))
    assert fallback is not None and fallback.version.build_id == settings.git_sha


def test_shutdown_fits_in_cloud_runs_grace_period() -> None:
    assert SHUTDOWN_GRACE < timedelta(seconds=10)
    assert MAX_HEARTBEAT_THROTTLE <= timedelta(seconds=15)


def test_ports_default_to_8080_and_9464() -> None:
    args = parse_args([])
    assert (args.health_port, args.metrics_port) == (8080, 9464)
    args = parse_args(["--health-port", "18080", "--metrics-port", "19464"])
    assert (args.health_port, args.metrics_port) == (18080, 19464)


async def test_activity_logs_carry_run_and_activity() -> None:
    seen: dict[str, Any] = {}

    class Next(ActivityInboundInterceptor):
        def __init__(self) -> None:
            pass

        async def execute_activity(self, input: ExecuteActivityInput) -> Any:
            seen.update(get_context())
            return "done"

    interceptor = LoggingInterceptor().intercept_activity(Next())
    env = ActivityEnvironment()

    async def call() -> Any:
        return await interceptor.execute_activity(None)  # type: ignore[arg-type]

    assert await env.run(call) == "done"
    assert seen == {
        "workflow_id": "test",
        "run_id": "test-run",
        "activity": "unknown",
        "activity_id": "test",
        "attempt": 1,
    }
    assert get_context() == {}  # unbound afterwards


async def test_a_garbage_request_line_is_a_404() -> None:
    server = HealthServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        status, _ = await http(server.port, b"garbage\r\n\r\n")
        assert status == 404
    finally:
        await server.stop()


# --------------------------------------------------------------------------- build_deps


def offline_engine(settings: Settings) -> AsyncEngine:
    """An engine that never connects (SQLAlchemy connects lazily); enough for build_deps."""
    return create_engine(
        settings.model_copy(update={"database_url": "postgresql+asyncpg://u:p@127.0.0.1:1/x"})
    )


@pytest.mark.parametrize(
    ("mode", "prompts"),
    [
        ("single", "photo_analysis/v1"),
        ("decomposed", "photo_countables/v3, photo_surfaces/v2"),
    ],
)
async def test_build_deps_resolves_the_vision_prompts_for_the_mode(
    settings: Settings, caplog: pytest.LogCaptureFixture, mode: str, prompts: str
) -> None:
    chosen = settings.model_copy(update={"vision_photo_mode": mode})
    engine = offline_engine(chosen)
    try:
        with caplog.at_level("INFO", logger="basis.worker"):
            deps = build_deps(chosen, engine)
    finally:
        await engine.dispose()
    assert deps.settings.vision_photo_mode == mode  # the activity reads the mode from here
    assert deps.llm is None  # no provider configured in the test environment
    assert f"mode {mode} ({prompts})" in caplog.text


async def test_build_deps_fails_at_startup_when_a_pinned_prompt_is_not_released(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("basis.vision.analyze.SURFACES_PROMPT_VERSION", "v99")
    chosen = settings.model_copy(update={"vision_photo_mode": "decomposed"})
    engine = offline_engine(chosen)
    try:
        with pytest.raises(PromptError, match="photo_surfaces/v99"):
            build_deps(chosen, engine)
    finally:
        await engine.dispose()


async def test_build_localizer_prefers_the_self_hosted_url(settings: Settings) -> None:
    """``LOCALIZER_URL`` (SAM3_HOSTING.md) wins over Replicate; its credentials are a static
    token, else a Google ID token when asked, else none; neither set: no localizer."""
    from pydantic import SecretStr

    from basis.vision.localize import CogSam3Localizer, ReplicateSam3Localizer
    from basis.worker.main import build_localizer

    none = settings.model_copy(update={"replicate_api_token": None, "localizer_url": None})
    assert build_localizer(none) is None
    replicate = build_localizer(
        none.model_copy(
            update={"replicate_api_token": SecretStr("r8_x"), "localizer_concurrency": 3}
        )
    )
    assert isinstance(replicate, ReplicateSam3Localizer) and replicate.concurrency == 3
    url = "https://sam3.example.run.app"
    hosted = none.model_copy(
        update={
            "replicate_api_token": SecretStr("r8_x"),
            "localizer_url": url,
            "localizer_token": SecretStr("bearer-x"),
            "localizer_usd_per_second": 0.001,
        }
    )
    cog = build_localizer(hosted)
    assert isinstance(cog, CogSam3Localizer)
    assert (cog.url, cog.usd_per_second, cog.version) == (
        url,
        0.001,
        settings.sam3_replicate_version,
    )
    assert cog.token_source is not None and await cog.token_source() == "bearer-x"
    google = build_localizer(
        hosted.model_copy(update={"localizer_token": None, "localizer_google_auth": True})
    )
    assert isinstance(google, CogSam3Localizer) and google.token_source is not None
    open_ = build_localizer(hosted.model_copy(update={"localizer_token": None}))
    assert isinstance(open_, CogSam3Localizer) and open_.token_source is None


def test_the_api_builds_the_same_localizer_as_an_interactive_client(settings: Settings) -> None:
    """Manual add (``basis.vision.localizer_config``): the API prompts the worker's model
    and host, but an engineer waits, so the call is bounded and retried once; the worker's
    client is unchanged (minutes of backoff for Replicate's throttle)."""
    from pydantic import SecretStr

    from basis.vision.localize import (
        INTERACTIVE_RETRY_DELAYS_S,
        PREDICTION_DEADLINE_S,
        PREFER_WAIT_S,
        CogSam3Localizer,
        ReplicateSam3Localizer,
    )
    from basis.vision.localizer_config import build_localizer

    replicate = settings.model_copy(
        update={"replicate_api_token": SecretStr("r8_x"), "localizer_url": None}
    )
    worker, api = build_localizer(replicate), build_localizer(replicate, timeout_s=12.5)
    assert isinstance(worker, ReplicateSam3Localizer) and isinstance(api, ReplicateSam3Localizer)
    assert (worker.deadline_s, worker.prefer_wait_s, worker.retry_delays) == (
        PREDICTION_DEADLINE_S,
        PREFER_WAIT_S,
        None,
    )
    assert (api.deadline_s, api.prefer_wait_s, api.retry_delays) == (
        12.5,
        13,
        INTERACTIVE_RETRY_DELAYS_S,
    )
    assert api.version == worker.version == settings.sam3_replicate_version
    assert api.client.timeout.read == 18.0
    hosted = replicate.model_copy(update={"localizer_url": "https://sam3.example.run.app"})
    cog = build_localizer(hosted, timeout_s=12.5)
    assert isinstance(cog, CogSam3Localizer)
    assert (cog.retry_delays, cog.client.timeout.read) == (INTERACTIVE_RETRY_DELAYS_S, 12.5)
    assert build_localizer(replicate.model_copy(update={"replicate_api_token": None})) is None


@pytest.mark.parametrize(
    ("method", "logged"),
    [
        (
            "grounded",
            "appraisal extraction: grounded on gpt-6.1-sol at effort low (budget 32000), "
            "the pipeline when it cannot run",
        ),
        ("pipeline", "appraisal extraction: pipeline (Azure DI, text layer, TEXT_MODEL fallback)"),
    ],
)
async def test_build_deps_logs_the_appraisal_extraction_method(
    settings: Settings, caplog: pytest.LogCaptureFixture, method: str, logged: str
) -> None:
    chosen = settings.model_copy(
        update={
            "extraction_method": method,
            "extraction_grounded_model": "gpt-6.1-sol",
            "extraction_grounded_reasoning_effort": "low",
            "extraction_grounded_max_tokens": 32000,
        }
    )
    engine = offline_engine(chosen)
    try:
        with caplog.at_level("INFO", logger="basis.worker"):
            build_deps(chosen, engine)
    finally:
        await engine.dispose()
    assert logged in caplog.text
    assert "text steps: extraction fallback" in caplog.text


# --------------------------------------------------------------------------- room-stage options


async def test_build_deps_at_the_defaults_logs_todays_room_step_and_builds_no_segmenter(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    chosen = settings.model_copy(update={"room_reconcile": True})
    engine = offline_engine(chosen)
    try:
        with caplog.at_level("INFO", logger="basis.worker"):
            deps = build_deps(chosen, engine)
    finally:
        await engine.dispose()
    assert deps.segmenter is None
    assert "second look sensitive (surface_material/v3 on claude-sonnet-5-5 at effort low)" in (
        caplog.text
    )
    assert "room step mode: reconcile (every room)" in caplog.text
    assert "materials second look: per_photo" in caplog.text


async def test_build_deps_with_both_options_loads_their_prompts_and_the_local_segmenter(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    from basis.vision.surface_segmenter import LOCAL_CHECKPOINT, LocalMask2Former

    chosen = settings.model_copy(
        update={
            "room_reconcile": True,
            "room_step_mode": "room_call",
            "materials_second_look": "surface_crops",
        }
    )
    engine = offline_engine(chosen)
    try:
        with caplog.at_level("INFO", logger="basis.worker"):
            deps = build_deps(chosen, engine)
    finally:
        await engine.dispose()
    # Built, not loaded: the weights are read on the first photo.
    assert isinstance(deps.segmenter, LocalMask2Former)
    assert deps.segmenter.name == f"local:{LOCAL_CHECKPOINT}"
    assert (
        "room step mode room_call: interior rooms room_photos_inventory/v2 on claude-opus-5-5 "
        "at effort high; exterior/mechanical/parking rooms keep the reconcile path"
    ) in caplog.text
    assert (
        "materials second look: surface crops (surface_crop_material/v1 on claude-sonnet-5-5 "
        "at effort low, segmenter local)"
    ) in caplog.text


async def test_the_segmenter_is_not_built_when_the_room_step_is_off(settings: Settings) -> None:
    from basis.worker.main import build_surface_segmenter

    off = settings.model_copy(
        update={"room_reconcile": False, "materials_second_look": "surface_crops"}
    )
    assert build_surface_segmenter(off) is None


async def test_build_deps_fails_at_startup_when_the_room_call_prompt_is_not_released(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("basis.vision.room_step.ROOM_CALL_PROMPT_VERSION", "v99")
    chosen = settings.model_copy(update={"room_reconcile": True, "room_step_mode": "room_call"})
    engine = offline_engine(chosen)
    try:
        with pytest.raises(PromptError, match="room_photos_inventory/v99"):
            build_deps(chosen, engine)
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- served models
# The live failure: a worker started with an old .env (Azure OpenAI only) ran a study whose
# Claude calls had no provider; the fusion call fell back on every photo. Now it refuses to
# start, naming the models and what would serve them.

AZURE_ONLY: dict[str, Any] = {
    "azure_openai_endpoint": "https://example-resource.openai.azure.com",
    "azure_openai_api_key": SecretStr("azure-key"),  # pragma: allowlist secret
    "azure_openai_deployment_name": "gpt-4o-mini",
    "openai_api_key": None,
    "anthropic_api_key": None,
}
ANTHROPIC = {"anthropic_api_key": SecretStr("sk-ant-test")}  # pragma: allowlist secret
# The tests' environment pins cheaper models (tests/conftest.py); these are the defaults.
PRODUCTION_MODELS: dict[str, Any] = {
    name: Settings.model_fields[name].default
    for name in (
        "vision_model",
        "vision_surfaces_model",
        "vision_count_fusion",
        "vision_fusion_model",
        "text_model",
        "room_model",
        "room_materials_model",
        "classify_model",
    )
}


async def test_a_worker_with_azure_alone_refuses_to_start_and_names_the_claude_models(
    settings: Settings,
) -> None:
    """The production defaults (decomposed photos with fusion, the room step with the
    Sonnet second look, the Sonnet classifier) on Azure alone: every claude-* model the
    pipeline calls is listed with the settings that name it."""
    chosen = settings.model_copy(
        update={
            **AZURE_ONLY,
            **PRODUCTION_MODELS,
            "room_reconcile": True,
            "vision_photo_mode": "decomposed",
        }
    )
    engine = offline_engine(chosen)
    try:
        with pytest.raises(ConfigError) as caught:
            build_deps(chosen, engine)
    finally:
        await engine.dispose()
    message = str(caught.value)
    assert message.startswith(
        "no configured provider serves these models the pipeline calls (configured: azure): "
        "claude-opus-5-5 (VISION_FUSION_MODEL); "
        "claude-sonnet-5-5 (ROOM_MATERIALS_MODEL, CLASSIFY_MODEL). Set ANTHROPIC_API_KEY"
    )
    assert "gpt-6.1-sol" not in message  # Azure routes every other name to its deployment
    assert "azure-key" not in message


async def test_a_fully_configured_worker_starts(settings: Settings) -> None:
    chosen = settings.model_copy(update={**AZURE_ONLY, **ANTHROPIC, "room_reconcile": True})
    engine = offline_engine(chosen)
    try:
        deps = build_deps(chosen, engine)
    finally:
        await engine.dispose()
    assert deps.llm is not None and deps.llm.name == "azure+anthropic"


async def test_claude_only_names_the_openai_models_and_their_variables(
    settings: Settings,
) -> None:
    chosen = settings.model_copy(
        update={
            **AZURE_ONLY,
            **ANTHROPIC,
            **PRODUCTION_MODELS,
            "vision_photo_mode": "decomposed",
            "azure_openai_endpoint": None,
            "room_reconcile": False,
        }
    )
    with pytest.raises(ConfigError) as caught:
        check_models_served(chosen, ProviderPool.from_settings(chosen), None)
    message = str(caught.value)
    assert "gpt-6.1-sol (VISION_MODEL)" in message
    assert "gpt-6-luna (VISION_SURFACES_MODEL, TEXT_MODEL)" in message
    assert "claude" not in message.split(":", 2)[1]  # the Claude models are served
    assert "OPENAI_API_KEY (or AZURE_OPENAI_ENDPOINT" in message


def test_pipeline_models_follow_the_settings_the_activities_read(settings: Settings) -> None:
    single = settings.model_copy(
        update={**PRODUCTION_MODELS, "vision_photo_mode": "single", "room_reconcile": False}
    )
    assert pipeline_models(single, None) == {
        single.vision_model: ["VISION_MODEL"],
        single.text_model: ["TEXT_MODEL"],
        single.classify_model: ["CLASSIFY_MODEL"],
    }  # no surfaces call and no fusion in the single-call mode, no room step
    # The grounded appraisal reading (the production default) adds its model; the pipeline's
    # TEXT_MODEL stays (it reads a document the grounded reading could not).
    grounded = single.model_copy(
        update={"extraction_method": "grounded", "extraction_grounded_model": "gpt-6.1-sol"}
    )
    assert pipeline_models(grounded, None) == {
        "gpt-6.1-sol": ["VISION_MODEL", "EXTRACTION_GROUNDED_MODEL"],
        grounded.text_model: ["TEXT_MODEL"],
        grounded.classify_model: ["CLASSIFY_MODEL"],
    }
    claude = grounded.model_copy(update={"extraction_grounded_model": "claude-reader"})
    assert pipeline_models(claude, None)["claude-reader"] == ["EXTRACTION_GROUNDED_MODEL"]
    every = settings.model_copy(
        update={
            "vision_photo_mode": "decomposed",
            "vision_count_fusion": "per_class",
            "room_reconcile": True,
            "room_step_mode": "room_call",
            "materials_second_look": "surface_crops",
            "vision_fusion_model": "claude-fusion",
            "room_model": "room-model",
            "room_materials_model": "look-model",
            "room_call_model": "claude-room-call",
            "surface_crop_model": "claude-crops",
        }
    )
    rooms = room_step.load_config(
        prompt_version=every.room_prompt_version,
        model=every.room_model,
        reasoning_effort=every.room_reasoning_effort,
        single_photo=every.room_single_photo,
        materials=every.room_materials,
        mode=every.room_step_mode,
        room_call_model=every.room_call_model,
        room_call_reasoning_effort=every.room_call_reasoning_effort,
        materials_model=every.room_materials_model,
        materials_reasoning_effort=every.room_materials_reasoning_effort,
    )
    models = pipeline_models(every, rooms)
    assert models["claude-fusion"] == ["VISION_FUSION_MODEL"]
    assert models["room-model"] == ["ROOM_MODEL"]
    assert models["look-model"] == ["ROOM_MATERIALS_MODEL"]
    assert models["claude-room-call"] == ["ROOM_CALL_MODEL"]
    assert models["claude-crops"] == ["SURFACE_CROP_MODEL"]
    no_fusion = every.model_copy(update={"vision_count_fusion": "off"})
    assert "claude-fusion" not in pipeline_models(no_fusion, rooms)


def test_no_provider_at_all_starts_in_dev_and_test_only(settings: Settings) -> None:
    """The offline mode the worker-process test runs (photos fail as ProviderNotConfigured,
    flagged) stays in dev and test; a deployed worker with no provider is refused."""
    bare = settings.model_copy(
        update={"openai_api_key": None, "anthropic_api_key": None, "azure_openai_endpoint": None}
    )
    for env in ("dev", "test"):
        check_models_served(bare.model_copy(update={"env": env}), None, None)
    for env in ("staging", "prod"):
        with pytest.raises(ConfigError, match="no model provider configured"):
            check_models_served(bare.model_copy(update={"env": env}), None, None)


def test_unserved_models_is_routing_only() -> None:
    from basis.llm.anthropic_provider import AnthropicProvider
    from basis.llm.providers import AzureOpenAIProvider

    assert unserved_models([AzureOpenAIProvider], ["gpt-x", "claude-a", "claude-a"]) == [  # type: ignore[list-item]
        "claude-a"
    ]
    assert unserved_models([AnthropicProvider], ["gpt-x", "claude-a"]) == ["gpt-x"]  # type: ignore[list-item]
    assert unserved_models([], ["m"]) == ["m"]


# ------------------------------------------------------ embedder warm-up (EVAL_REPORT 39)


def deps_with(settings: Settings, embedder: Any) -> Deps:
    return Deps(
        sessionmaker=None,  # type: ignore[arg-type]  # never used by the warm-up
        storage=None,  # type: ignore[arg-type]
        bucket="unused",
        settings=settings,
        retrieval=Retrieval(embedder=embedder, registry=Bm25Registry()),
    )


class Warmable(HashEmbedder):
    """An embedder with something to load (``warm`` reports a load, or raises)."""

    def __init__(
        self,
        load: ModelLoad | None = None,
        error: Exception | None = None,
        order: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.load = load
        self.error = error
        self.order = order if order is not None else []
        self.warmed = 0
        self.thread: str | None = None

    @property
    def name(self) -> str:
        return "test/warmable"

    def warm(self) -> ModelLoad | None:
        self.warmed += 1
        self.order.append("warm_embedder")
        self.thread = threading.current_thread().name
        if self.error is not None:
            raise self.error
        return self.load


async def test_warm_embedder_logs_the_source_and_duration(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    load = ModelLoad(model="test/warmable", revision="abc", source="cache", seconds=6.84)
    embedder = Warmable(load)
    with caplog.at_level("INFO", logger="basis.worker"):
        await warm_embedder(deps_with(settings, embedder))
    assert embedder.warmed == 1 and embedder.thread != "MainThread"  # off the event loop
    [record] = [r for r in caplog.records if r.message.startswith("embedding model warm")]
    assert record.message == "embedding model warm: test/warmable from cache in 6.8 s"
    assert (record.source, record.seconds, record.embedding_model_revision) == (  # type: ignore[attr-defined]
        "cache",
        6.84,
        "abc",
    )


async def test_warm_embedder_has_nothing_to_do_for_the_hash_embedder(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="basis.worker"):
        await warm_embedder(deps_with(settings, HashEmbedder()))
    assert "embedder hash-v1-384 needs no warm-up" in caplog.text


async def test_warm_embedder_raises_when_the_model_cannot_load(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    error = OSError("couldn't find them in the cached files")
    with caplog.at_level("CRITICAL", logger="basis.worker"), pytest.raises(OSError):
        await warm_embedder(deps_with(settings, Warmable(error=error)))
    [record] = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert "will not start" in record.message and record.exc_info is not None


class StartupProbe:
    """``run_worker`` with its Temporal client, search attributes, metrics runtime and
    worker replaced: records the order of the startup steps."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, embedder: Warmable) -> None:
        import basis.worker.main as main

        self.order = embedder.order
        self.embedder = embedder
        self.stop = asyncio.Event()
        order = self.order

        async def connect(settings: Settings, *, runtime: Any = None) -> object:
            order.append("connect")
            return object()

        async def ensure(client: object) -> None:
            order.append("search_attributes")

        async def warm_index(deps: Deps) -> None:
            order.append("warm_reference_index")

        class FakeWorker:
            async def run(self) -> None:
                order.append("polling")
                await asyncio.Event().wait()

            async def shutdown(self) -> None:
                order.append("shutdown")

        def build(client: object, settings: Settings, deps: Deps, **_: Any) -> FakeWorker:
            order.append("build_worker")
            return FakeWorker()

        monkeypatch.setattr(main, "connect", connect)
        monkeypatch.setattr(main, "ensure_search_attributes", ensure)
        monkeypatch.setattr(main, "metrics_runtime", lambda port: None)
        monkeypatch.setattr(main, "warm_reference_index", warm_index)
        monkeypatch.setattr(main, "build_worker", build)
        monkeypatch.setattr(main, "create_engine", offline_engine)

    def deps_factory(self, settings: Settings, engine: AsyncEngine) -> Deps:
        self.order.append("deps")
        return deps_with(settings, self.embedder)

    async def run(self, settings: Settings) -> None:
        from basis.worker.main import run_worker

        await run_worker(settings, health_port=0, stop=self.stop, deps_factory=self.deps_factory)


async def test_the_worker_polls_only_after_the_model_is_warm(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = ModelLoad(model="test/warmable", revision=None, source="download", seconds=1.0)
    probe = StartupProbe(monkeypatch, Warmable(load))
    task = asyncio.create_task(probe.run(settings))
    while "polling" not in probe.order:
        await asyncio.sleep(0.01)
    probe.stop.set()
    await asyncio.wait_for(task, 5)
    assert probe.order == [
        "connect",
        "search_attributes",
        "deps",
        "warm_embedder",
        "warm_reference_index",
        "build_worker",
        "polling",
        "shutdown",
    ]


async def test_the_worker_refuses_to_start_without_the_model(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    probe = StartupProbe(monkeypatch, Warmable(error=OSError("no model")))
    with (
        caplog.at_level("CRITICAL", logger="basis.worker"),
        pytest.raises(OSError, match="no model"),
    ):
        await asyncio.wait_for(probe.run(settings), 5)
    assert probe.order == ["connect", "search_attributes", "deps", "warm_embedder"]
    assert "build_worker" not in probe.order and "polling" not in probe.order
    assert "will not start" in caplog.text
