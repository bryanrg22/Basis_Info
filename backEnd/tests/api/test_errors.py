"""Error mapping on the real application (S1_SPEC sections 4 and 5).

* Repository errors become problems: ``NotVisibleError`` 404, ``ConflictError`` and its
  subclasses 409 (with a specific ``type``), ``InvalidCursorError`` 422, a pool checkout
  timeout 503 with ``Retry-After``; anything else stays an opaque 500.
* ``/v1`` bodies over 1 MiB are refused with 413 before any route, dependency or
  idempotency work, whether or not ``Content-Length`` is sent.
* Every error is ``application/problem+json`` whose ``request_id`` is the response's
  ``X-Request-Id``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, bearer, build_api_app
from fastapi import FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.bodylimit import MAX_JSON_BODY_BYTES, BodySizeLimitMiddleware
from basis.api.errors import (
    PROBLEM_JSON,
    PROBLEM_TYPE_BASE,
    ApiError,
    as_api_error,
    problem_response,
)
from basis.api.idempotency import IDEMPOTENCY_HEADER
from basis.db.engine import create_engine
from basis.db.enums import Stage
from basis.db.errors import (
    ConflictError,
    DuplicateDocumentError,
    DuplicateKeyError,
    InvalidCursorError,
    NotVisibleError,
    StaleStateError,
)
from basis.db.models import IdempotencyKey, Study
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration


def assert_problem(response: httpx.Response, status: int, suffix: str | None = None) -> None:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["status"] == status
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["instance"] == response.request.url.path
    if suffix is not None:
        assert body["type"] == PROBLEM_TYPE_BASE + suffix


# --------------------------------------------------------------------------- mapping


@pytest.mark.parametrize(
    ("error", "status", "suffix"),
    [
        (NotVisibleError("study x not found"), 404, "not-found"),
        (StaleStateError("stale"), 409, "stale-state"),
        (DuplicateDocumentError("dup"), 409, "duplicate-document"),
        (DuplicateKeyError("dup key"), 409, "duplicate-key"),
        (ConflictError("other conflict"), 409, "conflict"),
        (InvalidCursorError("bad cursor"), 422, "invalid-cursor"),
        (PoolTimeoutError("QueuePool limit"), 503, "service-unavailable"),
    ],
)
def test_repository_errors_map_to_problems(error: Exception, status: int, suffix: str) -> None:
    mapped = as_api_error(error)
    assert mapped is not None
    assert (mapped.status, mapped.type_uri) == (status, PROBLEM_TYPE_BASE + suffix)
    if status == 503:
        assert int(mapped.headers["Retry-After"]) >= 1
        assert "QueuePool" not in (mapped.detail or "")  # internals are not echoed
    else:
        assert mapped.detail == str(error)


@pytest.mark.parametrize("error", [RuntimeError("x"), ValueError("y"), KeyError("z")])
def test_unexpected_errors_are_not_mapped(error: Exception) -> None:
    assert as_api_error(error) is None


def test_api_errors_map_to_themselves() -> None:
    error = ApiError(418, detail="teapot")
    assert as_api_error(error) is error


async def test_mapped_errors_through_the_app(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    await seed.stage(study["id"], Stage.APPRAISAL_REVIEW)

    assert_problem(await api.get("alice", f"/v1/studies/{uuid.uuid4()}"), 404, "not-found")
    assert_problem(
        await api.get("alice", "/v1/studies", params={"cursor": "%%%"}), 422, "invalid-cursor"
    )
    stale = await api.post(
        "alice",
        f"/v1/studies/{study['id']}/decisions",
        {"stage": "rooms_review", "revision": 1, "decision": "approve"},
    )
    assert_problem(stale, 409, "stale-state")


async def test_pool_timeout_is_503_with_retry_after(
    database_url: str, storage: LocalStorage, verifier: FakeVerifier
) -> None:
    """Every pooled connection busy: the request waits pool_timeout, then 503, not a hang."""
    engine = create_engine(api_settings(database_url, db_pool_size=1), pool_timeout=0.3)
    app = build_api_app(api_settings(database_url), create_sessionmaker(engine), storage, verifier)
    try:
        async with engine.connect() as held:  # the only connection
            await held.exec_driver_sql("SELECT 1")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE_URL
            ) as client:
                response = await asyncio.wait_for(
                    client.get("/v1/studies", headers=bearer("alice")), timeout=5
                )
        assert_problem(response, 503, "service-unavailable")
        assert int(response.headers["retry-after"]) >= 1
    finally:
        await engine.dispose()


async def test_unexpected_exception_is_an_opaque_500(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from basis.db.repositories import StudyRepository

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(StudyRepository, "list_for_user", boom)
    response = await api.get("alice", "/v1/studies")
    assert_problem(response, 500)
    assert "secret" not in response.text


# --------------------------------------------------------------------------- 413


async def test_oversized_body_with_content_length_is_413(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = json.dumps({"property_name": "x" * (MAX_JSON_BODY_BYTES + 1)}).encode()
    response = await api.client.post(
        "/v1/studies",
        content=body,
        headers={
            **bearer("alice"),
            IDEMPOTENCY_HEADER: str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )

    assert_problem(response, 413, "payload-too-large")
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Study)) == 0
        # Refused before the idempotency dependency: no key was reserved.
        assert await session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0


async def test_oversized_chunked_body_is_413(api: Api) -> None:
    chunk = b"x" * 65536

    async def stream() -> AsyncIterator[bytes]:
        for _ in range(MAX_JSON_BODY_BYTES // len(chunk) + 2):
            yield chunk

    response = await api.client.post(
        "/v1/studies",
        content=stream(),
        headers={
            **bearer("alice"),
            IDEMPOTENCY_HEADER: str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    assert "content-length" not in response.request.headers
    assert_problem(response, 413, "payload-too-large")


async def test_body_at_the_limit_is_not_413(api: Api) -> None:
    padding = MAX_JSON_BODY_BYTES - len(json.dumps({"property_name": ""}))
    body = json.dumps({"property_name": "x" * padding}).encode()
    assert len(body) == MAX_JSON_BODY_BYTES
    response = await api.client.post(
        "/v1/studies",
        content=body,
        headers={
            **bearer("alice"),
            IDEMPOTENCY_HEADER: str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 422  # too long a name, but the body was read


async def test_small_chunked_body_is_passed_through(api: Api) -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b'{"property_name":'
        yield b' "Chunked"}'

    response = await api.client.post(
        "/v1/studies",
        content=stream(),
        headers={
            **bearer("alice"),
            IDEMPOTENCY_HEADER: str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 201
    assert response.json()["property_name"] == "Chunked"


async def test_limit_applies_only_under_v1(settings_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=settings_app), base_url=BASE_URL
    ) as client:
        response = await client.post("/echo", content=b"x" * (MAX_JSON_BODY_BYTES + 10))
    assert response.status_code == 200
    assert response.json() == {"size": MAX_JSON_BODY_BYTES + 10}


@pytest.fixture
def settings_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    app = build_api_app(api_settings(database_url), sessionmaker, storage, verifier)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return app


def test_middleware_rejects_a_non_positive_limit() -> None:
    async def app(scope: object, receive: object, send: object) -> None:
        return None

    with pytest.raises(ValueError):
        BodySizeLimitMiddleware(app, max_bytes=0)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- 405


@pytest.mark.parametrize(
    ("method", "path", "allow"),
    [
        ("PUT", "/v1/studies", "GET, POST"),
        ("PATCH", f"/v1/studies/{uuid.UUID(int=1)}", "DELETE, GET"),
        ("GET", f"/v1/studies/{uuid.UUID(int=1)}/decisions", "POST"),
        ("OPTIONS", "/health", "GET"),
    ],
)
async def test_405_allow_lists_every_method_of_the_resource(
    api: Api, method: str, path: str, allow: str
) -> None:
    """Regression (found by the Schemathesis contract test): Starlette answered from the
    first route matching the path, so ``Allow`` on /v1/studies listed only GET."""
    response = await api.client.request(method, path, headers=bearer("alice"))
    assert_problem(response, 405)
    assert response.headers["allow"] == allow


# --------------------------------------------------------------------------- shape


def test_problem_extensions_cannot_replace_standard_members() -> None:
    response = problem_response(status=503, extensions={"checks": {"db": "fail"}})
    assert json.loads(bytes(response.body))["checks"] == {"db": "fail"}
    with pytest.raises(ValueError):
        problem_response(status=503, extensions={"status": 200})


async def test_validation_problem_lists_fields(api: Api) -> None:
    response = await api.post("alice", "/v1/studies", {"property_name": 5, "extra": 1})
    assert_problem(response, 422, "validation")
    locations = sorted(tuple(error["loc"]) for error in response.json()["errors"])
    assert locations == [("body", "extra"), ("body", "property_name")]
