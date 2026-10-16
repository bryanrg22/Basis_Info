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
        ("decomposed", "photo_countables/v2, photo_surfaces/v1"),
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
