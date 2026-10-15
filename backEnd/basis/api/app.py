"""FastAPI application factory."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from basis import __version__
from basis.api.middleware import RequestIdMiddleware
from basis.config import Settings

SERVICE_NAME = "basis"
DOCS_URL = "/docs"

logger = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    """Build the API. ``settings`` is passed in so tests control every value."""
    app = FastAPI(
        title="Basis API",
        version=__version__,
        docs_url=DOCS_URL,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, Any]:
        """Liveness and build identity. No auth, no dependency calls."""
        logger.debug("health check")
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "version": __version__,
            "git_sha": settings.git_sha,
            "env": settings.env,
        }

    @app.get("/", tags=["ops"])
    async def root() -> dict[str, str]:
        return {"service": SERVICE_NAME, "docs": DOCS_URL}

    return app
