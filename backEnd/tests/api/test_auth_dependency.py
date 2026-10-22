"""Invariants for the authentication dependencies (S1_SPEC section 4, BUILD_PLAN 6.4).

* No ``Authorization`` header: 401 problem, ``WWW-Authenticate: Bearer``.
* A header that is not ``Bearer <token>``: 401 problem with a distinct detail.
* A token the verifier rejects: 401 problem with ``error="invalid_token"``.
* The scheme is case-insensitive; the token is passed to the verifier unchanged.
* Verification runs off the event loop and once per request, even when several
  dependencies need the principal.
* Tests swap the verifier with ``dependency_overrides[get_token_verifier]``, or the
  whole principal with ``dependency_overrides[get_principal]``.
* OpenAPI advertises the bearer scheme.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from basis.api.app import create_app
from basis.api.auth import (
    DETAIL_MALFORMED,
    DETAIL_MISSING,
    CurrentPrincipal,
    OptionalPrincipal,
    get_principal,
    get_token_verifier,
    parse_bearer,
)
from basis.api.errors import PROBLEM_JSON, Unauthorized
from basis.api.ratelimit import rate_limited
from basis.auth.firebase import DETAIL_INVALID, FirebaseTokenVerifier, Principal
from basis.config import Settings

ALICE = Principal(uid="alice", email="alice@example.com", display_name="Alice")
GOOD_TOKEN = "good-token"  # pragma: allowlist secret


class FakeVerifier:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.threads: list[threading.Thread] = []

    def verify(self, token: str) -> Principal:
        self.tokens.append(token)
        self.threads.append(threading.current_thread())
        if token == GOOD_TOKEN:
            return ALICE
        raise Unauthorized(DETAIL_INVALID)


@pytest.fixture
def verifier() -> FakeVerifier:
    return FakeVerifier()


@pytest.fixture
def app(settings: Settings, verifier: FakeVerifier) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_token_verifier] = lambda: verifier

    @app.get("/v1/whoami")
    async def me(principal: CurrentPrincipal) -> dict[str, Any]:
        return {"uid": principal.uid, "email": principal.email, "name": principal.display_name}

    @app.get("/v1/maybe")
    async def maybe(principal: OptionalPrincipal) -> dict[str, str | None]:
        return {"uid": principal.uid if principal else None}

    @app.get("/v1/limited", dependencies=[Depends(rate_limited("read"))])
    async def limited(principal: CurrentPrincipal) -> dict[str, str]:
        return {"uid": principal.uid}

    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_401(response: httpx.Response, detail: str, challenge: str) -> None:
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["www-authenticate"] == challenge
    body = response.json()
    assert body["detail"] == detail
    assert body["status"] == 401
    assert body["request_id"] == response.headers["x-request-id"]


async def test_valid_token_yields_principal(
    client: httpx.AsyncClient, verifier: FakeVerifier
) -> None:
    response = await client.get("/v1/whoami", headers=bearer(GOOD_TOKEN))
    assert response.status_code == 200
    assert response.json() == {"uid": "alice", "email": "alice@example.com", "name": "Alice"}
    assert verifier.tokens == [GOOD_TOKEN]


async def test_missing_header_is_401(client: httpx.AsyncClient, verifier: FakeVerifier) -> None:
    response = await client.get("/v1/whoami")
    assert_401(response, DETAIL_MISSING, "Bearer")
    assert verifier.tokens == []


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "good-token", "Bearer a b", "Token good-token"],
)
async def test_malformed_header_is_401(
    client: httpx.AsyncClient, verifier: FakeVerifier, header: str
) -> None:
    response = await client.get("/v1/whoami", headers={"Authorization": header})
    assert_401(response, DETAIL_MALFORMED, "Bearer")
    assert verifier.tokens == []


async def test_rejected_token_is_401_invalid_token(
    client: httpx.AsyncClient, verifier: FakeVerifier
) -> None:
    response = await client.get("/v1/whoami", headers=bearer("forged"))
    assert_401(response, DETAIL_INVALID, 'Bearer error="invalid_token"')
    assert verifier.tokens == ["forged"]


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
async def test_scheme_is_case_insensitive(client: httpx.AsyncClient, scheme: str) -> None:
    response = await client.get("/v1/whoami", headers={"Authorization": f"{scheme} {GOOD_TOKEN}"})
    assert response.status_code == 200


def test_parse_bearer_tolerates_surrounding_whitespace() -> None:
    assert parse_bearer(f"  Bearer   {GOOD_TOKEN} ") == GOOD_TOKEN


async def test_verification_runs_in_a_worker_thread(
    client: httpx.AsyncClient, verifier: FakeVerifier
) -> None:
    await client.get("/v1/whoami", headers=bearer(GOOD_TOKEN))
    assert verifier.threads and verifier.threads[0] is not threading.main_thread()


async def test_token_verified_once_per_request(
    client: httpx.AsyncClient, verifier: FakeVerifier
) -> None:
    """The rate limiter and the route both need the principal; one verification."""
    response = await client.get("/v1/limited", headers=bearer(GOOD_TOKEN))
    assert response.status_code == 200
    assert verifier.tokens == [GOOD_TOKEN]


async def test_optional_principal_is_none_without_header(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/maybe")).json() == {"uid": None}


async def test_optional_principal_still_rejects_bad_tokens(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/maybe", headers=bearer("forged"))
    assert response.status_code == 401


async def test_principal_override_replaces_authentication(
    app: FastAPI, client: httpx.AsyncClient, verifier: FakeVerifier
) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(uid="test-user")
    response = await client.get("/v1/whoami")
    assert response.status_code == 200
    assert response.json()["uid"] == "test-user"
    assert verifier.tokens == []


async def test_health_needs_no_token(client: httpx.AsyncClient, verifier: FakeVerifier) -> None:
    assert (await client.get("/health")).status_code == 200
    assert verifier.tokens == []


def test_app_uses_firebase_verifier_by_default(settings: Settings) -> None:
    assert isinstance(create_app(settings).state.token_verifier, FirebaseTokenVerifier)


def test_openapi_declares_bearer_scheme(app: FastAPI) -> None:
    spec = app.openapi()
    assert spec["components"]["securitySchemes"]["FirebaseIdToken"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Firebase ID token",
    }
    assert {"FirebaseIdToken": []} in spec["paths"]["/v1/whoami"]["get"]["security"]
