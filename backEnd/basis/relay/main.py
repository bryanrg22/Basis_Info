"""``python -m basis.relay``: the outbox relay process (S2_SPEC section 6).

Builds settings, the database engine and a lazy Temporal client, then runs
:class:`~basis.relay.delivery.Relay` every ``RELAY_POLL_SECONDS`` until SIGTERM or
SIGINT. The Temporal client is lazy so the relay starts, and keeps retrying deliveries
with backoff, while Temporal is down.

Shutdown: on SIGTERM the loop stops polling; a batch in flight gets
:data:`DRAIN_SECONDS` to finish (Cloud Run gives 10 s) and is otherwise cancelled, which
rolls its transaction back so its messages are simply due again (no attempt is
counted). The engine is disposed before exit.

``GET /health`` on ``--health-port`` (default 8081): 200 ``{"status": "ok"}`` while the
relay has reached the database within :data:`STALE_AFTER_S` (or is still starting),
503 otherwise. Any other path is 404. It is a dependency-free asyncio server: the probe
needs nothing more.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import time
from collections.abc import Sequence
from typing import Final

from basis.config import Settings, get_settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.obs.logging import configure_logging
from basis.relay.delivery import Relay, TemporalWorkflowGateway
from basis.temporal.client import connect

DEFAULT_HEALTH_PORT: Final = 8081
DRAIN_SECONDS: Final = 8.0
STALE_AFTER_S: Final = 60.0

logger = logging.getLogger(__name__)


def healthy(relay: Relay, started: float, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    stale_after = max(STALE_AFTER_S, 10 * relay.poll_seconds)
    last = relay.last_poll if relay.last_poll is not None else started
    return now - last <= stale_after


async def serve_health(relay: Relay, port: int, host: str = "0.0.0.0") -> asyncio.Server:
    started = time.monotonic()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = request_line.decode("latin-1").split()
            path = parts[1] if len(parts) >= 2 else ""
            if path == "/health":
                ok = healthy(relay, started)
                status, body = (
                    ("200 OK", {"status": "ok"})
                    if ok
                    else (
                        "503 Service Unavailable",
                        {"status": "fail"},
                    )
                )
            else:
                status, body = "404 Not Found", {"status": "not found"}
            payload = json.dumps(body).encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    return await asyncio.start_server(handle, host, port)


async def run(settings: Settings, *, health_port: int, once: bool = False) -> int:
    engine = create_engine(settings)
    try:
        client = await connect(settings, lazy=True)
        relay = Relay(
            create_sessionmaker(engine),
            TemporalWorkflowGateway(client),
            poll_seconds=settings.relay_poll_seconds,
        )
        if once:
            try:
                report = await relay.run_once()
            except Exception:
                logger.exception("outbox relay batch failed")
                return 1
            logger.info("outbox relay ran one batch", extra={"report": str(report)})
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        server = await serve_health(relay, health_port)
        try:
            task = asyncio.create_task(relay.run(stop))
            stopping = asyncio.create_task(stop.wait())
            await asyncio.wait({task, stopping}, return_when=asyncio.FIRST_COMPLETED)
            stopping.cancel()
            logger.info("outbox relay stopping", extra={"drain_seconds": DRAIN_SECONDS})
            try:
                # Lets the batch in flight finish; cancelling it rolls its transaction back.
                await asyncio.wait_for(task, timeout=DRAIN_SECONDS)
            except TimeoutError:
                logger.warning("outbox relay batch cancelled at shutdown")
        finally:
            server.close()
            await server.wait_closed()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
        return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m basis.relay", description=__doc__)
    parser.add_argument("--health-port", type=int, default=DEFAULT_HEALTH_PORT)
    parser.add_argument("--once", action="store_true", help="deliver one batch and exit")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env != "dev")
    return asyncio.run(run(settings, health_port=args.health_port, once=args.once))
