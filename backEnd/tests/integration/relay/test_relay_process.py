"""``python -m basis.relay`` as a real process: settings from the environment, the health
endpoint, delivery, and a clean exit on SIGTERM (S2_SPEC section 6)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import free_port, outbox_row

from basis.db.enums import OutboxStatus
from basis.db.repositories import OutboxRepository
from basis.db.session import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def relay_env(database_url: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("TEMPORAL_")}
    env.update(
        BASIS_ENV="test",
        DATABASE_URL=database_url,
        TEMPORAL_ADDRESS=f"127.0.0.1:{free_port()}",  # nothing listens: lazy client
        RELAY_POLL_SECONDS="0.1",
        LOG_LEVEL="INFO",
    )
    return env


async def enqueue_notification(sm: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with session_scope(sm) as session:
        message = await OutboxRepository(session).enqueue(
            "notification", {"study_id": str(uuid.uuid4()), "event": "review_pending"}
        )
        return message.id


async def test_once_delivers_a_batch_and_exits(
    database_url: str, sm: async_sessionmaker[AsyncSession]
) -> None:
    message_id = await enqueue_notification(sm)
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "basis.relay", "--once"],
        env=relay_env(database_url),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (await outbox_row(sm, message_id)).status is OutboxStatus.DELIVERED
    # JSON logs on stdout; outbox_id and study_id are on the line
    (line,) = [line for line in completed.stdout.splitlines() if "outbox message delivered" in line]
    logged = json.loads(line)
    assert logged["outbox_id"] == str(message_id) and logged["kind"] == "notification"
    assert logged["severity"] == "INFO" and "study_id" in logged


async def test_serves_health_delivers_and_stops_on_sigterm(
    database_url: str, sm: async_sessionmaker[AsyncSession]
) -> None:
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "basis.relay", "--health-port", str(port)],
        env=relay_env(database_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            deadline = time.monotonic() + 30
            while True:
                try:
                    response = await client.get("/health")
                    break
                except httpx.TransportError:
                    assert time.monotonic() < deadline, "relay health endpoint never came up"
                    await asyncio.sleep(0.1)
            assert (response.status_code, response.json()) == (200, {"status": "ok"})

        message_id = await enqueue_notification(sm)
        async with asyncio.timeout(10):
            while (await outbox_row(sm, message_id)).status is not OutboxStatus.DELIVERED:
                await asyncio.sleep(0.1)

        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        returncode = await asyncio.to_thread(process.wait, 15)
        assert returncode == 0
        assert time.monotonic() - started < 10  # within Cloud Run's grace period
        stdout = process.stdout.read() if process.stdout else ""
        messages = [json.loads(line)["message"] for line in stdout.splitlines() if line]
        assert messages[0] == "outbox relay started"
        assert set(messages[-2:]) == {"outbox relay stopping", "outbox relay stopped"}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
