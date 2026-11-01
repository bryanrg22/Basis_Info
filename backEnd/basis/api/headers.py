"""ASGI middleware: the response headers every answer carries (OWASP ASVS 14.4).

* ``X-Content-Type-Options: nosniff`` on every response, so a browser never guesses a type
  for a JSON or problem body (the file routes set it themselves; this covers the rest).
* ``Cache-Control: no-store`` on every ``/v1`` response that sets no ``Cache-Control`` of
  its own. Every ``/v1`` answer is private to the bearer of the token: a study, a firm's
  members, an error naming the caller's ids. RFC 9111 lets a private cache store a response
  to a request with ``Authorization`` unless told not to, and a shared cache store it when
  the response says ``public`` or carries certain directives, so the API says so. The file
  routes (``.../content``, ``.../pdf``, ``.../report``, the preview) keep their own
  ``private, max-age`` / ``no-cache`` policy, which this never overrides.
* ``Strict-Transport-Security`` in the deployed environments (``staging``, ``prod``), where
  the API is reached over TLS only; not in dev and test, where plain http on localhost is
  the norm.

Pure ASGI, like the request-id middleware, so streaming responses are not buffered.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

NOSNIFF_HEADER: Final = "X-Content-Type-Options"
NOSNIFF: Final = "nosniff"
CACHE_CONTROL_HEADER: Final = "Cache-Control"
NO_STORE: Final = "no-store"
HSTS_HEADER: Final = "Strict-Transport-Security"
# One year, the usual minimum for preload lists; subdomains too (the API is on its own host).
HSTS: Final = "max-age=31536000; includeSubDomains"
API_PREFIX: Final = "/v1/"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool, no_store_prefix: str = API_PREFIX) -> None:
        self.app = app
        self.hsts = hsts
        self.no_store_prefix = no_store_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        private = str(scope.get("path", "")).startswith(self.no_store_prefix)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault(NOSNIFF_HEADER, NOSNIFF)
                if private:
                    headers.setdefault(CACHE_CONTROL_HEADER, NO_STORE)
                if self.hsts:
                    headers.setdefault(HSTS_HEADER, HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
