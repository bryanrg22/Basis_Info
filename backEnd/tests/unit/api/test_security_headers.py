"""The response headers every answer carries (``basis.api.headers``, OWASP ASVS 14.4).

* ``X-Content-Type-Options: nosniff`` on every response: ``/health``, a ``/v1`` JSON body, a
  401 problem, a 404 for an unknown path, a 413, an opaque 500.
* ``Cache-Control: no-store`` on every ``/v1`` response that sets none itself (JSON bodies
  and problems alike); a route's own ``Cache-Control`` (the file routes' ``private,
  max-age``) is kept; ``/health`` and ``/`` are not marked.
* ``Strict-Transport-Security`` only in staging and prod.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Response

from basis.api.app import create_app
from basis.api.auth import get_token_verifier
from basis.api.bodylimit import MAX_JSON_BODY_BYTES
from basis.api.headers import HSTS, NO_STORE
from basis.auth.firebase import Principal
from basis.config import Settings

HSTS_HEADER = "strict-transport-security"


class OneUser:
    def verify(self, token: str) -> Principal:
        return Principal(uid="alice")


def build(**overrides: object) -> Settings:
    return Settings(_env_file=None, temporal_address="", **overrides)  # type: ignore[arg-type]


def with_probe_routes(app: FastAPI) -> FastAPI:
    app.dependency_overrides[get_token_verifier] = OneUser

    @app.get("/v1/probe/json")
    async def probe_json() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/v1/probe/cached")
    async def probe_cached() -> Response:
        return Response(
            b"x", media_type="text/plain", headers={"Cache-Control": "private, max-age=60"}
        )

    @app.get("/v1/probe/boom")
    async def probe_boom() -> None:
        raise RuntimeError("secret internals")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = with_probe_routes(create_app(build()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        yield c


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/health", 200),
        ("GET", "/", 200),
        ("GET", "/v1/probe/json", 200),
        ("GET", "/v1/probe/cached", 200),
        ("GET", "/v1/probe/boom", 500),
        ("GET", "/v1/studies", 401),  # no token: the auth problem
        ("GET", "/nowhere", 404),
    ],
)
async def test_every_response_is_nosniff(
    client: httpx.AsyncClient, method: str, path: str, status: int
) -> None:
    response = await client.request(method, path)
    assert response.status_code == status
    assert response.headers.get_list("x-content-type-options") == ["nosniff"]


async def test_v1_json_and_problems_are_no_store(client: httpx.AsyncClient) -> None:
    for path, status in (("/v1/probe/json", 200), ("/v1/studies", 401), ("/v1/probe/boom", 500)):
        response = await client.get(path)
        assert response.status_code == status, path
        assert response.headers.get_list("cache-control") == [NO_STORE], path


async def test_a_413_is_no_store_too(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/studies",
        content=b"x" * (MAX_JSON_BODY_BYTES + 1),
        headers={"Content-Type": "application/json", "Authorization": "Bearer t"},
    )
    assert response.status_code == 413
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_routes_own_cache_control_is_kept(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/probe/cached")
    assert response.headers.get_list("cache-control") == ["private, max-age=60"]


async def test_health_and_root_are_not_marked_no_store(client: httpx.AsyncClient) -> None:
    for path in ("/health", "/"):
        assert "cache-control" not in (await client.get(path)).headers


async def test_no_hsts_in_test(client: httpx.AsyncClient) -> None:
    assert HSTS_HEADER not in (await client.get("/health")).headers


@pytest.mark.parametrize("env", ["staging", "prod"])
async def test_hsts_when_deployed(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        BASIS_ENV=env,  # the field's alias; a plain ``env=`` kwarg is ignored
        database_url="postgresql+asyncpg://basis:x@db/basis",
        gcs_bucket="bucket",
        firebase_project_id="project",
        temporal_address="",
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        response = await c.get("/health")
    assert response.headers[HSTS_HEADER] == HSTS
