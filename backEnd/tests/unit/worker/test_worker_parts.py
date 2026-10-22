"""Worker pieces: ``/health``, worker versioning config, the activity logging context, the
composition root's check of the per-photo vision prompts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.common import VersioningBehavior
from temporalio.testing import ActivityEnvironment
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput

from basis.config import Settings
from basis.db.engine import create_engine
from basis.llm.prompts import PromptError
from basis.obs.logging import get_context
from basis.worker.health import HealthServer
from basis.worker.interceptors import LoggingInterceptor
from basis.worker.main import (
    MAX_HEARTBEAT_THROTTLE,
    SHUTDOWN_GRACE,
    build_deps,
    deployment_config,
    parse_args,
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
