"""Invariants for RFC 9457 problem responses (S1_SPEC section 4, BUILD_PLAN 6.4).

* Every error the API returns is ``application/problem+json`` with ``type``, ``title``,
  ``status``, ``instance`` and the request's ``request_id`` (equal to ``X-Request-Id``).
* ``ApiError`` subclasses keep their status, title, type URI and headers
  (``WWW-Authenticate`` on 401, ``Retry-After`` on 429).
* Validation failures are 422 with per-field ``errors`` and never echo the input.
* Starlette/FastAPI ``HTTPException`` (unknown route, 405) uses ``about:blank`` and keeps
  its headers.
* Unexpected exceptions become an opaque 500 that hides the message, carries the request
  id, and is logged with ``stack_trace`` and the request id.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from basis.api.app import create_app
from basis.api.errors import (
    ABOUT_BLANK,
    INTERNAL_ERROR_DETAIL,
    PROBLEM_JSON,
    PROBLEM_TYPE_BASE,
    ApiError,
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    TooManyRequests,
    Unauthorized,
    Unprocessable,
    field_errors,
    problem_response,
)
from basis.api.middleware import REQUEST_ID_HEADER
from basis.config import Settings
from basis.errors.catalogue import code_for_problem
from basis.errors.codes import E
from basis.obs.logging import bound, configure_logging

SECRET_MESSAGE = "db password is hunter2"  # pragma: allowlist secret


class Payload(BaseModel):
    name: str
    count: int


ERRORS: dict[str, ApiError] = {
    "bad-request": BadRequest("bad input"),
    "unauthorized": Unauthorized("token rejected"),
    "unauthorized-missing": Unauthorized("no credentials", error=None),
    "forbidden": Forbidden("not your study"),
    "not-found": NotFound("no such study"),
    "conflict": Conflict("stale revision"),
    "unprocessable": Unprocessable("key reused"),
    "too-many": TooManyRequests("slow down", retry_after=7),
    "teapot": ApiError(418, detail="short and stout", type_suffix="teapot"),
}


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/raise/{name}")
    async def raise_named(name: str) -> None:
        raise ERRORS[name]

    @app.post("/validate")
    async def validate(payload: Payload) -> dict[str, Any]:
        return payload.model_dump()

    @app.get("/http/{status}")
    async def http_error(status: int) -> None:
        raise HTTPException(status, detail="custom detail", headers={"X-Extra": "1"})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_MESSAGE)

    @app.get("/stream-boom")
    async def stream_boom() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError(SECRET_MESSAGE)

        return StreamingResponse(chunks())

    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        yield c


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    previous = root.level
    stream = io.StringIO()
    handler = configure_logging("DEBUG", stream=stream)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous)


def log_entries(stream: io.StringIO, message: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    return [e for e in lines if e["message"] == message]


def assert_problem(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    body: dict[str, Any] = response.json()
    assert body["status"] == status
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert isinstance(body["title"], str) and body["title"]
    assert isinstance(body["type"], str) and body["type"]
    return body


# --------------------------------------------------------------------------- ApiError


@pytest.mark.parametrize(
    ("name", "status", "title", "suffix", "detail"),
    [
        ("bad-request", 400, "Bad Request", "bad-request", "bad input"),
        ("unauthorized", 401, "Unauthorized", "unauthorized", "token rejected"),
        ("forbidden", 403, "Forbidden", "forbidden", "not your study"),
        ("not-found", 404, "Not Found", "not-found", "no such study"),
        ("conflict", 409, "Conflict", "conflict", "stale revision"),
        ("unprocessable", 422, "Unprocessable Content", "unprocessable", "key reused"),
        ("too-many", 429, "Too Many Requests", "rate-limited", "slow down"),
        ("teapot", 418, "I'm a Teapot", "teapot", "short and stout"),
    ],
)
async def test_api_error_renders_problem(
    client: httpx.AsyncClient, name: str, status: int, title: str, suffix: str, detail: str
) -> None:
    response = await client.get(f"/raise/{name}", headers={REQUEST_ID_HEADER: f"req-{name}"})
    body = assert_problem(response, status)
    assert body == {
        "type": PROBLEM_TYPE_BASE + suffix,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": f"/raise/{name}",
        "request_id": f"req-{name}",
        # The catalogue code (docs/ERROR_CODES.md): the suffix's generic, or the plain HTTP
        # error's for a suffix the catalogue does not know (the teapot).
        "code": code_for_problem(suffix) or E.API_HTTP_ERROR,
    }


async def test_unauthorized_sends_bearer_challenge(client: httpx.AsyncClient) -> None:
    rejected = await client.get("/raise/unauthorized")
    assert rejected.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    missing = await client.get("/raise/unauthorized-missing")
    assert missing.headers["www-authenticate"] == "Bearer"


async def test_too_many_requests_sends_retry_after(client: httpx.AsyncClient) -> None:
    response = await client.get("/raise/too-many")
    assert response.headers["retry-after"] == "7"


@pytest.mark.parametrize(("given", "sent"), [(0, 1), (-5, 1), (1, 1), (120, 120)])
def test_retry_after_is_at_least_one_second(given: int, sent: int) -> None:
    assert TooManyRequests("x", retry_after=given).headers["Retry-After"] == str(sent)


def test_api_error_without_suffix_is_about_blank() -> None:
    error = ApiError(503, detail="later")
    assert error.type_uri == ABOUT_BLANK
    assert error.title == "Service Unavailable"
    assert error.status == 503


def test_api_error_explicit_title_wins() -> None:
    assert ApiError(409, title="Stale Revision").title == "Stale Revision"


async def test_generated_request_id_is_in_body(client: httpx.AsyncClient) -> None:
    response = await client.get("/raise/not-found")
    body = assert_problem(response, 404)
    assert len(body["request_id"]) == 36


async def test_problem_omits_unset_members(client: httpx.AsyncClient) -> None:
    body = (await client.get("/raise/conflict")).json()
    assert "errors" not in body


# --------------------------------------------------------------------------- validation


async def test_validation_error_lists_fields(client: httpx.AsyncClient) -> None:
    response = await client.post("/validate", json={"count": "not-a-number"})
    body = assert_problem(response, 422)
    assert body["type"] == PROBLEM_TYPE_BASE + "validation"
    assert body["title"] == "Unprocessable Content"
    assert body["instance"] == "/validate"
    by_loc = {tuple(e["loc"]): e for e in body["errors"]}
    assert set(by_loc) == {("body", "name"), ("body", "count")}
    assert by_loc[("body", "name")]["type"] == "missing"
    assert by_loc[("body", "count")]["type"] == "int_parsing"
    assert all(set(e) == {"loc", "detail", "type"} for e in body["errors"])


async def test_validation_error_never_echoes_input(client: httpx.AsyncClient) -> None:
    response = await client.post("/validate", json={"name": "ok", "count": SECRET_MESSAGE})
    assert response.status_code == 422
    assert SECRET_MESSAGE not in response.text


async def test_malformed_json_is_a_validation_problem(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/validate", content=b"{not json", headers={"content-type": "application/json"}
    )
    body = assert_problem(response, 422)
    assert body["errors"][0]["type"] == "json_invalid"


def test_field_errors_keep_only_location_message_type() -> None:
    raw = [
        {
            "loc": ("body", "items", 0),
            "msg": "bad",
            "type": "value_error",
            "input": "secret",
            "ctx": {"error": ValueError("x")},
        }
    ]
    (error,) = field_errors(raw)
    assert error.model_dump() == {
        "loc": ["body", "items", 0],
        "detail": "bad",
        "type": "value_error",
    }


# --------------------------------------------------------------------------- HTTPException


async def test_unknown_route_is_about_blank_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/no-such-route", headers={REQUEST_ID_HEADER: "nf-2"})
    body = assert_problem(response, 404)
    assert body == {
        "type": ABOUT_BLANK,
        "title": "Not Found",
        "status": 404,
        "instance": "/no-such-route",
        "request_id": "nf-2",
        "code": E.API_HTTP_ERROR,
    }


async def test_method_not_allowed_keeps_allow_header(client: httpx.AsyncClient) -> None:
    response = await client.delete("/validate")
    assert_problem(response, 405)
    assert response.headers["allow"] == "POST"


async def test_http_exception_keeps_detail_and_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/http/409")
    body = assert_problem(response, 409)
    assert body["detail"] == "custom detail"
    assert body["type"] == ABOUT_BLANK
    assert body["title"] == "Conflict"
    assert response.headers["x-extra"] == "1"


# --------------------------------------------------------------------------- 500


async def test_unexpected_exception_is_opaque_500(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom", headers={REQUEST_ID_HEADER: "req-500"})
    body = assert_problem(response, 500)
    assert body == {
        "type": ABOUT_BLANK,
        "title": "Internal Server Error",
        "status": 500,
        "detail": INTERNAL_ERROR_DETAIL,
        "instance": "/boom",
        "request_id": "req-500",
        "code": E.API_INTERNAL_ERROR,
    }
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text


async def test_unexpected_exception_is_logged_with_stack_and_request_id(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/boom", headers={REQUEST_ID_HEADER: "req-500-log"})
    (entry,) = log_entries(log_stream, "unhandled exception")
    assert entry["severity"] == "ERROR"
    assert entry["request_id"] == "req-500-log"
    assert entry["path"] == "/boom"
    assert entry["http_method"] == "GET"
    assert entry["exception_type"] == "RuntimeError"
    assert "RuntimeError" in entry["stack_trace"]
    assert "Traceback" in entry["stack_trace"]


async def test_exception_after_response_started_is_logged_and_reraised(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    with pytest.raises(RuntimeError):
        await client.get("/stream-boom", headers={REQUEST_ID_HEADER: "req-stream"})
    (entry,) = log_entries(log_stream, "unhandled exception")
    assert entry["request_id"] == "req-stream"


async def test_health_unaffected(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"


# --------------------------------------------------------------------------- rendering


def test_problem_response_reads_request_id_from_context() -> None:
    with bound(request_id="ctx-1"):
        response = problem_response(status=409, detail="d")
    assert json.loads(response.body)["request_id"] == "ctx-1"
    assert response.media_type == PROBLEM_JSON


def test_problem_response_without_context_has_no_request_id() -> None:
    body = json.loads(problem_response(status=400).body)
    assert body == {
        "type": ABOUT_BLANK,
        "title": "Bad Request",
        "status": 400,
        "code": E.API_HTTP_ERROR,
    }


def test_unknown_status_gets_generic_title() -> None:
    assert json.loads(problem_response(status=599).body)["title"] == "Error"
