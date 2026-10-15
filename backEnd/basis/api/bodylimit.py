"""Request body size limit for the JSON API (S1_SPEC section 5: 1 MiB).

Uploads never pass through the API (the browser PUTs to a signed URL), so every body the
API accepts is a small JSON document. Anything larger is refused with a 413 problem
before a route, dependency or the idempotency fingerprint reads it.

* A ``Content-Length`` above the limit is refused without reading the body.
* Otherwise (no ``Content-Length``, e.g. chunked) the body is read up to the limit; one
  byte more and the request is refused. The buffered body is then replayed to the app, so
  at most ``max_bytes`` are ever held per request.

Applies to ``POST``/``PUT``/``PATCH``/``DELETE`` under ``path_prefix`` (``/v1/``); other
paths, such as ``/health``, are untouched. Pure ASGI, so nothing is buffered twice and
the request-id context of the outer middleware is preserved.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from basis.api.errors import PayloadTooLarge, problem_response

MAX_JSON_BODY_BYTES: Final = 1024 * 1024
API_PREFIX: Final = "/v1/"
_METHODS_WITH_BODY: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class BodySizeLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, max_bytes: int = MAX_JSON_BODY_BYTES, path_prefix: str = API_PREFIX
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") not in _METHODS_WITH_BODY
            or not str(scope.get("path", "")).startswith(self.path_prefix)
        ):
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.strip().isdigit():
            if int(declared) > self.max_bytes:
                await self._reject(scope, receive, send)
                return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Client went away before sending the body; let the app see it.
                replay = _Replay([message], receive)
                await self.app(scope, replay, send)
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body_message: Message = {
            "type": "http.request",
            "body": b"".join(chunks),
            "more_body": False,
        }
        await self.app(scope, _Replay([body_message], receive), send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        error = PayloadTooLarge(
            f"The request body exceeds {self.max_bytes} bytes. Files are uploaded to the "
            "signed URL, not through the API."
        )
        response = problem_response(
            status=error.status,
            title=error.title,
            detail=error.detail,
            type_uri=error.type_uri,
            instance=scope.get("path"),
        )
        await response(scope, receive, send)


class _Replay:
    """``receive`` that returns the buffered messages first, then the real ``receive``."""

    def __init__(self, messages: list[Message], receive: Receive) -> None:
        self._messages = messages
        self._receive = receive

    async def __call__(self) -> Message:
        if self._messages:
            return self._messages.pop(0)
        return await self._receive()
