"""CORS on the API (``basis.api.app.install_cors``).

* Without ``CORS_ALLOWED_ORIGINS`` no response carries CORS headers and a preflight is not
  answered as one (the browser then blocks cross-origin calls).
* A listed origin's preflight for a ``/v1`` POST with the headers the frontend sends
  (Authorization, Content-Type, Idempotency-Key, X-Request-Id) is answered 200 with those
  methods and headers allowed, and without credentials.
* Actual responses to a listed origin carry ``Access-Control-Allow-Origin`` and expose
  ``X-Request-Id``, including error responses (401 problem), which CORS must wrap.
* The intake's conditional write works from the browser: a ``PUT`` preflight with
  ``If-Match`` is allowed and ``ETag`` is exposed.
* An unlisted origin gets no ``Access-Control-Allow-Origin``; its preflight is refused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from basis.api.app import CORS_ALLOW_HEADERS, create_app
from basis.config import Settings

FRONTEND = "https://cost-seg-basis.web.app"
LOCAL = "http://localhost:3000"


def build(**overrides: object) -> Settings:
    return Settings(_env_file=None, env="test", **overrides)  # type: ignore[arg-type]


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(build(cors_allowed_origins=(FRONTEND, LOCAL), temporal_address=""))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        yield c


def preflight_headers(origin: str, headers: str = "authorization,content-type") -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": headers,
    }


async def test_preflight_from_a_listed_origin_is_allowed(client: httpx.AsyncClient) -> None:
    requested = ",".join(h.lower() for h in CORS_ALLOW_HEADERS)
    response = await client.options("/v1/studies", headers=preflight_headers(FRONTEND, requested))
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
    allowed_methods = response.headers["access-control-allow-methods"]
    assert {"GET", "POST", "DELETE"} <= {m.strip() for m in allowed_methods.split(",")}
    allowed = {
        h.strip().lower() for h in response.headers["access-control-allow-headers"].split(",")
    }
    assert {"authorization", "content-type", "idempotency-key", "x-request-id"} <= allowed
    assert response.headers["access-control-max-age"] == "600"
    assert "access-control-allow-credentials" not in response.headers


async def test_the_intake_put_with_if_match_is_allowed_and_its_etag_exposed(
    client: httpx.AsyncClient,
) -> None:
    response = await client.options(
        "/v1/studies/00000000-0000-0000-0000-000000000001/intake",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type,if-match",
        },
    )
    assert response.status_code == 200
    assert "PUT" in {m.strip() for m in response.headers["access-control-allow-methods"].split(",")}
    allowed = {
        h.strip().lower() for h in response.headers["access-control-allow-headers"].split(",")
    }
    assert "if-match" in allowed
    actual = await client.get("/v1/studies", headers={"Origin": FRONTEND})
    exposed = {
        h.strip().lower() for h in actual.headers["access-control-expose-headers"].split(",")
    }
    assert "etag" in exposed


async def test_preflight_from_localhost_is_allowed(client: httpx.AsyncClient) -> None:
    response = await client.options("/v1/studies", headers=preflight_headers(LOCAL))
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == LOCAL


async def test_preflight_from_an_unlisted_origin_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/v1/studies", headers=preflight_headers("https://evil.example")
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_preflight_with_an_unlisted_header_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/v1/studies", headers=preflight_headers(FRONTEND, "authorization,x-custom")
    )
    assert response.status_code == 400


async def test_simple_response_carries_the_origin_and_exposes_request_id(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health", headers={"Origin": FRONTEND})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
    exposed = {
        h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")
    }
    assert {"x-request-id", "location", "retry-after", "idempotent-replayed"} <= exposed
    assert response.headers["x-request-id"]


async def test_error_responses_carry_cors_headers(client: httpx.AsyncClient) -> None:
    """A 401 problem must reach the frontend's code, not fail as a CORS error."""
    response = await client.get("/v1/studies", headers={"Origin": FRONTEND})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["access-control-allow-origin"] == FRONTEND


async def test_unlisted_origin_gets_no_allow_origin(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_no_origins_configured_means_no_cors() -> None:
    app = create_app(build(temporal_address=""))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        simple = await c.get("/health", headers={"Origin": FRONTEND})
        preflight = await c.options("/v1/studies", headers=preflight_headers(FRONTEND))
    assert "access-control-allow-origin" not in simple.headers
    assert "access-control-allow-origin" not in preflight.headers
    # No CORS middleware: OPTIONS reaches the router, which has no OPTIONS route.
    assert preflight.status_code == 405
    assert "access-control-allow-methods" not in preflight.headers
