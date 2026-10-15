"""Worker pieces: ``/health``, worker versioning config, the activity logging context."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio.common import VersioningBehavior
from temporalio.testing import ActivityEnvironment
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput

from basis.config import Settings
from basis.obs.logging import get_context
from basis.worker.health import HealthServer
from basis.worker.interceptors import LoggingInterceptor
from basis.worker.main import (
    MAX_HEARTBEAT_THROTTLE,
    SHUTDOWN_GRACE,
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
