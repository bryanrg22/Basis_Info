"""A minimal ``/health`` endpoint for the worker's startup probe (stdlib only).

Cloud Run worker pools have no default probe and no URL; a TCP/HTTP startup probe on this
port tells Cloud Run the worker connected to Temporal and is polling. ``GET /health``
answers 200 ``{"status":"ok"}`` while the worker runs and 503 ``{"status":"draining"}``
(or ``"starting"``) otherwise; any other path is 404. HTTP/1.0 semantics: one request per
connection, then close.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Final

logger = logging.getLogger(__name__)

READ_TIMEOUT_S: Final = 5.0


class HealthServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self.status = "starting"

    @property
    def port(self) -> int:
        """The bound port (useful with ``port=0`` in tests)."""
        if self._server is None or not self._server.sockets:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port)
        logger.info("health endpoint on %s:%s/health", self._host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), READ_TIMEOUT_S)
            parts = request_line.decode("latin-1").split()
            path = parts[1] if len(parts) >= 2 else ""
            if len(parts) >= 1 and parts[0] in ("GET", "HEAD") and path == "/health":
                code, reason = (200, "OK") if self.status == "ok" else (503, "Service Unavailable")
                body = json.dumps({"status": self.status}).encode()
            else:
                code, reason, body = 404, "Not Found", b'{"status":"not found"}'
            head = (
                f"HTTP/1.0 {code} {reason}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            ).encode()
            writer.write(head + (body if parts[:1] != ["HEAD"] else b""))
            await writer.drain()
        except (TimeoutError, ConnectionError, UnicodeDecodeError):
            pass
        finally:
            writer.close()
