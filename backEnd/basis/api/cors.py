"""CORS by path: the app's origins everywhere, a public page's origins on the one route it posts to.

Starlette's ``CORSMiddleware`` is application-wide, and it answers every preflight itself:
an origin it does not list gets 400 before any inner middleware or route sees the request.
Two of them stacked therefore cannot give one path a wider list. This middleware holds one
``CORSMiddleware`` per configuration around the same inner app and picks by the exact path,
so each request goes through exactly one of them (no doubled headers) and each path's
preflight is decided by its own list.

Used by :func:`basis.api.app.install_cors`: ``CORS_ALLOWED_ORIGINS`` (the web app) for every
path, and for ``POST /v1/demo-requests`` the public site's ``DEMO_REQUEST_ALLOWED_ORIGINS``
together with the app's (docs/api/DEMO_REQUESTS.md). Without a configuration for the default
a path not listed passes through with no CORS headers, as the API did before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class CorsPolicy:
    """The ``CORSMiddleware`` arguments for one set of paths (credentials always off: the API
    uses bearer tokens, never cookies)."""

    origins: tuple[str, ...]
    methods: tuple[str, ...]
    headers: tuple[str, ...]
    expose: tuple[str, ...] = field(default=())
    max_age: int = 600

    def wrap(self, app: ASGIApp) -> ASGIApp:
        return CORSMiddleware(
            app,
            allow_origins=list(self.origins),
            allow_methods=list(self.methods),
            allow_headers=list(self.headers),
            expose_headers=list(self.expose),
            allow_credentials=False,
            max_age=self.max_age,
        )


class PathScopedCORSMiddleware:
    """``paths`` maps an exact request path to its policy; every other path gets ``default``
    (``None``: no CORS handling)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default: CorsPolicy | None,
        paths: Mapping[str, CorsPolicy],
    ) -> None:
        self.app = app
        self._default: ASGIApp = default.wrap(app) if default is not None else app
        self._paths: dict[str, ASGIApp] = {path: policy.wrap(app) for path, policy in paths.items()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scoped = self._paths.get(scope["path"])
            if scoped is not None:
                await scoped(scope, receive, send)
                return
        await self._default(scope, receive, send)
