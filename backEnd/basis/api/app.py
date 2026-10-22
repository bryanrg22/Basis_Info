"""FastAPI application factory.

Wiring (all on ``app.state``):

* ``settings``, ``token_verifier`` (Firebase), ``rate_limiter`` (in memory, per process).
* ``db_sessionmaker``: created at startup (lifespan) from ``DATABASE_URL`` (or, from S5,
  ``CLOUD_SQL_INSTANCE``) and disposed at shutdown; ``None`` when neither is set, in which
  case ``/v1`` routes answer 503 and ``/health`` reports no database check. Tests pass a
  ready sessionmaker to :func:`create_app` instead; the app then does not own the engine.
* ``idempotency_store``: Postgres-backed whenever a database is configured, in memory
  otherwise.
* ``storage`` and ``storage_bucket``: :class:`~basis.storage.GcsStorage` when
  ``GCS_BUCKET`` is set; otherwise :class:`~basis.storage.LocalStorage` under the system
  temp directory with the placeholder bucket :data:`LOCAL_BUCKET` (tests and local runs).
* ``temporal`` (a ``temporalio`` client) and ``workflows`` (the
  :class:`~basis.temporal.service.WorkflowService` the workflow routes use): created at
  startup when ``TEMPORAL_ADDRESS`` is non-empty. The client is lazy, so startup makes no
  network call and the API starts (and accepts decisions, which go through the outbox)
  while Temporal is down; only the workflow routes answer 503 then. Tests pass a fake
  ``workflows`` to :func:`create_app` instead; ``temporal`` then stays ``None``.

Middleware, innermost first: the 1 MiB body limit for ``/v1/``, the 500 handler, request
ids, and CORS for ``CORS_ALLOWED_ORIGINS`` (:func:`install_cors`). The OpenAPI document is
built by :func:`basis.api.openapi.build_openapi`.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis import __version__
from basis.api import health
from basis.api import local_uploads as local_uploads_route
from basis.api.bodylimit import BodySizeLimitMiddleware
from basis.api.errors import install_error_handlers
from basis.api.idempotency import (
    IDEMPOTENCY_HEADER,
    REPLAYED_HEADER,
    InMemoryIdempotencyStore,
)
from basis.api.idempotency_pg import PostgresIdempotencyStore
from basis.api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from basis.api.openapi import build_openapi
from basis.api.ratelimit import InMemoryRateLimiter
from basis.api.routes import api_routers
from basis.auth.firebase import FirebaseTokenVerifier
from basis.config import Settings
from basis.db.engine import create_engine
from basis.db.session import SESSIONMAKER_STATE_ATTR, create_sessionmaker
from basis.storage import GcsStorage, LocalStorage, Storage
from basis.storage.base import CONTENT_LENGTH_RANGE_HEADER
from basis.temporal.client import connect, temporal_configured
from basis.temporal.service import TemporalWorkflowService, WorkflowService

SERVICE_NAME: Final = health.SERVICE_NAME
DOCS_URL: Final = "/docs"
LOCAL_BUCKET: Final = "basis-local"
LOCAL_STORAGE_DIR: Final = Path(tempfile.gettempdir()) / "basis-local-storage"
# What the browser may send and read cross-origin: the methods the /v1 routes use, the
# request headers they read, and the response headers a client acts on.
CORS_ALLOW_METHODS: Final = ("GET", "POST", "DELETE")
CORS_ALLOW_HEADERS: Final = ("Authorization", "Content-Type", IDEMPOTENCY_HEADER, REQUEST_ID_HEADER)
CORS_EXPOSE_HEADERS: Final = (REQUEST_ID_HEADER, REPLAYED_HEADER, "Location", "Retry-After")
CORS_MAX_AGE_S: Final = 600

logger = logging.getLogger(__name__)


def database_configured(settings: Settings) -> bool:
    return bool(settings.database_url or settings.cloud_sql_instance)


def install_database(app: FastAPI, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Use ``sessionmaker`` for requests and keep idempotency keys in PostgreSQL."""
    setattr(app.state, SESSIONMAKER_STATE_ATTR, sessionmaker)
    app.state.idempotency_store = PostgresIdempotencyStore(sessionmaker)


def local_uploads_enabled(app: FastAPI, settings: Settings) -> bool:
    """The dev upload route stands in for GCS: local storage, dev or test only."""
    return isinstance(app.state.storage, LocalStorage) and settings.env in ("dev", "test")


def install_cors(app: FastAPI, settings: Settings, *, local_uploads: bool = False) -> None:
    """Answer browser preflights and tag responses for ``CORS_ALLOWED_ORIGINS``.

    Outermost, so error responses (401, 413, 429, 5xx problems) carry the headers too and
    the browser shows the frontend the problem instead of an opaque CORS failure. Bearer
    tokens, not cookies, so credentials stay off. With ``local_uploads`` the browser's
    upload to the dev upload route (a ``PUT`` with the signed content-length range header,
    as on GCS) is allowed too; deployed stacks never allow it.
    """
    if not settings.cors_allowed_origins:
        return
    methods, headers = list(CORS_ALLOW_METHODS), list(CORS_ALLOW_HEADERS)
    if local_uploads:
        methods.append("PUT")
        headers.append(CONTENT_LENGTH_RANGE_HEADER)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_methods=methods,
        allow_headers=headers,
        expose_headers=list(CORS_EXPOSE_HEADERS),
        allow_credentials=False,
        max_age=CORS_MAX_AGE_S,
    )


def default_storage(settings: Settings) -> tuple[Storage, str]:
    if settings.gcs_bucket:
        return GcsStorage(settings.gcs_bucket), settings.gcs_bucket
    return (
        LocalStorage(LOCAL_STORAGE_DIR, upload_url_base=settings.local_upload_base_url),
        LOCAL_BUCKET,
    )


def create_app(
    settings: Settings,
    *,
    db_sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    storage: Storage | None = None,
    workflows: WorkflowService | None = None,
) -> FastAPI:
    """Build the API. ``settings`` is passed in so tests control every value.

    ``db_sessionmaker``, ``storage`` and ``workflows`` override what settings would
    select; they exist for tests and are not used by :mod:`basis.api.main`.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = None
        if getattr(app.state, SESSIONMAKER_STATE_ATTR) is None and database_configured(settings):
            owned = create_engine(settings)
            install_database(app, create_sessionmaker(owned))
        owns_temporal = app.state.workflows is None and temporal_configured(settings)
        if owns_temporal:
            client = await connect(settings, lazy=True)
            app.state.temporal = client
            app.state.workflows = TemporalWorkflowService(
                client, task_queue=settings.temporal_task_queue
            )
        try:
            yield
        finally:
            if owns_temporal:
                # A temporalio client holds no resources that need closing.
                app.state.temporal = None
                app.state.workflows = None
            if owned is not None:
                await owned.dispose()
                setattr(app.state, SESSIONMAKER_STATE_ATTR, None)
                app.state.idempotency_store = InMemoryIdempotencyStore()

    app = FastAPI(
        title="Basis API",
        version=__version__,
        docs_url=DOCS_URL,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Raises ConfigError (refuses to start) if the auth emulator is set in staging/prod.
    app.state.token_verifier = FirebaseTokenVerifier(settings)
    app.state.rate_limiter = InMemoryRateLimiter()
    setattr(app.state, SESSIONMAKER_STATE_ATTR, None)
    app.state.idempotency_store = InMemoryIdempotencyStore()
    if db_sessionmaker is not None:
        install_database(app, db_sessionmaker)
    app.state.temporal = None
    app.state.workflows = workflows
    if storage is None:
        app.state.storage, app.state.storage_bucket = default_storage(settings)
    else:
        app.state.storage = storage
        app.state.storage_bucket = settings.gcs_bucket or LOCAL_BUCKET

    # Order matters: middleware added later wraps earlier middleware. The body limit and
    # the 500 handler must run inside the request-id context.
    app.add_middleware(BodySizeLimitMiddleware)
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    local_uploads = local_uploads_enabled(app, settings)
    install_cors(app, settings, local_uploads=local_uploads)

    app.include_router(health.router)
    if local_uploads:
        app.include_router(local_uploads_route.router)

    @app.get("/", tags=["ops"], summary="Service name and docs URL")
    async def root() -> dict[str, str]:
        return {"service": SERVICE_NAME, "docs": DOCS_URL}

    for router in api_routers():
        app.include_router(router)

    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]
    return app
