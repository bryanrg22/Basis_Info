"""``/health`` with the database check, and the startup wiring that provides it.

* Database reachable: 200 with ``checks.db = {"status": "ok", "latency_ms": ...}``.
* Unreachable (connection refused) or hanging (no answer within the check timeout):
  503 problem with ``checks.db.status == "fail"`` and ``Retry-After``, quickly.
* No database configured: no ``checks`` member (``tests/api/test_health.py``).
* Lifespan: with ``DATABASE_URL`` set, startup creates the engine, the sessionmaker and
  the Postgres idempotency store; shutdown disposes the engine.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    REFUSED_DATABASE_URL,
    FakeVerifier,
    api_settings,
    bearer,
    build_api_app,
)
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis import __version__
from basis.api import health
from basis.api.app import create_app
from basis.api.auth import get_token_verifier
from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import InMemoryIdempotencyStore
from basis.api.idempotency_pg import PostgresIdempotencyStore
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration


async def get_health(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get("/health")


@pytest.fixture
async def silent_server() -> AsyncIterator[int]:
    """A TCP port that accepts connections and never answers (a hung database)."""
    writers: list[asyncio.StreamWriter] = []

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()


def broken_app(
    database_url: str, url: str, storage: LocalStorage, verifier: FakeVerifier
) -> tuple[FastAPI, Any]:
    engine = create_engine(api_settings(database_url).model_copy(update={"database_url": url}))
    app = build_api_app(api_settings(database_url), create_sessionmaker(engine), storage, verifier)
    return app, engine


async def test_health_reports_the_database(api_app: FastAPI) -> None:
    response = await get_health(api_app)

    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ("status", "service", "version", "env")} == {
        "status": "ok",
        "service": "basis",
        "version": __version__,
        "env": "test",
    }
    db = body["checks"]["db"]
    assert db["status"] == "ok"
    assert isinstance(db["latency_ms"], int | float) and 0 <= db["latency_ms"] < 2000


async def test_unreachable_database_is_503(
    database_url: str, storage: LocalStorage, verifier: FakeVerifier
) -> None:
    app, engine = broken_app(database_url, REFUSED_DATABASE_URL, storage, verifier)
    try:
        started = time.perf_counter()
        response = await get_health(app)
        elapsed = time.perf_counter() - started
    finally:
        await engine.dispose()

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert int(response.headers["retry-after"]) >= 1
    body = response.json()
    assert body["type"].endswith("/service-unavailable")
    assert body["checks"]["db"]["status"] == "fail"
    assert body["request_id"] == response.headers["x-request-id"]
    assert "127.0.0.1" not in response.text and "basis:x" not in response.text
    assert elapsed < health.DB_CHECK_TIMEOUT_S + 1


async def test_hanging_database_times_out_as_503(
    database_url: str,
    storage: LocalStorage,
    verifier: FakeVerifier,
    silent_server: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "DB_CHECK_TIMEOUT_S", 0.3)
    url = f"postgresql://basis:x@127.0.0.1:{silent_server}/none"  # pragma: allowlist secret
    app, engine = broken_app(database_url, url, storage, verifier)
    try:
        started = time.perf_counter()
        response = await get_health(app)
        elapsed = time.perf_counter() - started
    finally:
        await engine.dispose()
    assert response.status_code == 503
    assert response.json()["checks"]["db"]["status"] == "fail"
    assert elapsed < 2


async def test_check_database_reports_latency(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    result = await health.check_database(sessionmaker)
    assert result.status == "ok" and result.latency_ms >= 0


# --------------------------------------------------------------------------- lifespan


async def test_lifespan_wires_and_disposes_the_database(
    database_url: str, tmp_path: Any, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # (``sessionmaker`` is requested only so the tables are truncated around this test.)
    app = create_app(api_settings(database_url), storage=LocalStorage(tmp_path))
    assert app.state.db_sessionmaker is None
    assert isinstance(app.state.idempotency_store, InMemoryIdempotencyStore)

    async with app.router.lifespan_context(app):
        assert app.state.db_sessionmaker is not None
        assert isinstance(app.state.idempotency_store, PostgresIdempotencyStore)
        engine = app.state.db_sessionmaker.kw["bind"]
        response = await get_health(app)
        assert response.json()["checks"]["db"]["status"] == "ok"
        pool = engine.pool
        assert pool.checkedin() >= 1

    assert app.state.db_sessionmaker is None
    assert isinstance(app.state.idempotency_store, InMemoryIdempotencyStore)
    assert engine.pool is not pool and pool.checkedin() == 0  # disposed: pool closed, replaced


async def test_lifespan_keeps_an_injected_sessionmaker(
    api_app: FastAPI, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with api_app.router.lifespan_context(api_app):
        assert api_app.state.db_sessionmaker is sessionmaker
    # The app did not own it, so it neither disposed nor dropped it.
    assert api_app.state.db_sessionmaker is sessionmaker
    assert (await get_health(api_app)).status_code == 200


async def test_app_built_from_settings_serves_v1_after_startup(
    database_url: str, tmp_path: Any, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    app = create_app(api_settings(database_url), storage=LocalStorage(tmp_path))
    app.dependency_overrides[get_token_verifier] = FakeVerifier
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        # /v1/me, not /v1/studies: a new user is in no firm, so the study list is 403 no-firm.
        response = await client.get("/v1/me", headers=bearer("alice"))
    assert response.status_code == 200
