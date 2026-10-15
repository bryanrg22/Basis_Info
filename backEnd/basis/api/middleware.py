"""ASGI middleware: request ids.

Pure ASGI (not ``BaseHTTPMiddleware``) so the context variable bound here is the one the
route handlers run in, and streaming responses are not buffered.
"""

from __future__ import annotations

import re
import uuid
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from basis.obs.logging import bound

REQUEST_ID_HEADER: Final = "X-Request-Id"
# Accept caller-supplied ids only when they are short and log-safe; otherwise mint one.
_VALID_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def new_request_id() -> str:
    return str(uuid.uuid4())


def resolve_request_id(supplied: str | None) -> str:
    """Use the caller's id when it is well formed, otherwise generate a UUID4."""
    if supplied is not None and _VALID_REQUEST_ID.fullmatch(supplied):
        return supplied
    return new_request_id()


class RequestIdMiddleware:
    """Bind ``request_id`` into the logging context and echo it on the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with bound(request_id=request_id):
            await self.app(scope, receive, send_with_request_id)
