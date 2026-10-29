"""Invariants for the API skeleton (BUILD_PLAN C3, C16, 6.4).

* ``/health`` reports status, service, version, git sha and env, without auth.
* Every response carries ``X-Request-Id``: the caller's when well formed, else a UUID4.
* Log lines written while handling a request carry that request id, and the id does not
  leak into the context after the request.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from basis import __version__
from basis.api.app import create_app
from basis.api.middleware import REQUEST_ID_HEADER, resolve_request_id
from basis.config import Settings
from basis.obs.logging import configure_logging, get_context


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


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


def entries(stream: io.StringIO, message: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    return [e for e in lines if e["message"] == message]


def is_uuid4(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


# --------------------------------------------------------------------------- /health


async def test_health_body(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "basis",
        "version": __version__,
        "git_sha": settings.git_sha,
        "env": "test",
    }


async def test_health_reports_injected_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "0123abc")
    from basis.config import load_settings

    app = create_app(load_settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        body = (await c.get("/health")).json()
    assert body["git_sha"] == "0123abc"


async def test_health_needs_no_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={})
    assert response.status_code == 200


async def test_root_lists_service_and_docs(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"service": "basis", "docs": "/docs"}


async def test_docs_url_resolves(client: httpx.AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200


def test_only_known_routes_exist(app: FastAPI) -> None:
    """Exactly the S1 and S2 routes; a new route must arrive with its own tests (and this list)."""
    operations = {
        f"{method.upper()} {path}"
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    }
    assert operations == {
        "GET /health",
        "GET /",
        "GET /v1/studies",
        "POST /v1/studies",
        "GET /v1/studies/{study_id}",
        "DELETE /v1/studies/{study_id}",
        "POST /v1/studies/{study_id}/documents",
        "POST /v1/studies/{study_id}/documents/{document_id}/complete",
        # the browser frontend's photos and appraisal (tests/api/test_document_content.py)
        "GET /v1/studies/{study_id}/documents/{document_id}/content",
        # UAD 3.6 intake (tests/api/test_uad36_api.py)
        "GET /v1/studies/{study_id}/documents/{document_id}/pdf",
        "GET /v1/studies/{study_id}/extraction",
        "POST /v1/studies/{study_id}/decisions",
        "GET /v1/studies/{study_id}/evidence",
        # v0.6 (tests/api/test_objects.py)
        "GET /v1/studies/{study_id}/photos/{document_id}/objects",
        "POST /v1/studies/{study_id}/photos/{document_id}/objects",
        "DELETE /v1/studies/{study_id}/photos/{document_id}/objects/{object_id}",
        # S2 (tests/api/test_workflow_routes.py)
        "POST /v1/studies/{study_id}/start",
        "GET /v1/studies/{study_id}/workflow",
        "POST /v1/studies/{study_id}/cancel",
        "POST /v1/studies/{study_id}/retry",
        "GET /v1/studies/{study_id}/fields",
        "GET /v1/studies/{study_id}/rooms",
        "GET /v1/studies/{study_id}/assets",
        # basis and land allocation (tests/api/test_studies.py, test_result.py)
        "PUT /v1/studies/{study_id}/intake",
        "GET /v1/studies/{study_id}/result",
        # the paid-call ledger (tests/api/test_spend.py)
        "GET /v1/studies/{study_id}/spend",
        "GET /v1/studies/{study_id}/calls",
        # invite-only firms (tests/api/test_firms.py)
        "GET /v1/me",
        "GET /v1/orgs/{org_id}/members",
        "POST /v1/orgs/{org_id}/members/{user_id}/role",
        "DELETE /v1/orgs/{org_id}/members/{user_id}",
        "GET /v1/orgs/{org_id}/invitations",
        "POST /v1/orgs/{org_id}/invitations",
        "DELETE /v1/orgs/{org_id}/invitations/{invitation_id}",
        "POST /v1/invitations/{invitation_id}/accept",
        "POST /v1/invitations/{invitation_id}/decline",
    }


def test_version_matches_package_metadata() -> None:
    from importlib.metadata import version

    assert __version__ == version("basis")


# --------------------------------------------------------------------------- request ids


async def test_supplied_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={REQUEST_ID_HEADER: "abc-123"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_request_id_header_is_case_insensitive(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"x-request-id": "lower-case-1"})
    assert response.headers[REQUEST_ID_HEADER] == "lower-case-1"


async def test_request_id_generated_when_absent(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert is_uuid4(response.headers[REQUEST_ID_HEADER])


async def test_generated_request_ids_are_unique(client: httpx.AsyncClient) -> None:
    ids = {(await client.get("/health")).headers[REQUEST_ID_HEADER] for _ in range(20)}
    assert len(ids) == 20


@pytest.mark.parametrize("bad", ["", "a" * 129, "has space", "semi;colon", 'quote"d', "ü"])
async def test_malformed_request_id_is_replaced(client: httpx.AsyncClient, bad: str) -> None:
    response = await client.get(
        "/health", headers=[(REQUEST_ID_HEADER.encode(), bad.encode("utf-8"))]
    )
    assert is_uuid4(response.headers[REQUEST_ID_HEADER])


def test_resolve_request_id_accepts_uuid_and_trace_like_ids() -> None:
    value = str(uuid.uuid4())
    assert resolve_request_id(value) == value
    assert resolve_request_id("a" * 128) == "a" * 128
    assert resolve_request_id("svc:req.1_2-3") == "svc:req.1_2-3"


async def test_request_id_on_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist", headers={REQUEST_ID_HEADER: "nf-1"})
    assert response.status_code == 404
    assert response.headers[REQUEST_ID_HEADER] == "nf-1"


async def test_single_request_id_header_on_response(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={REQUEST_ID_HEADER: "once"})
    assert response.headers.get_list(REQUEST_ID_HEADER) == ["once"]


# --------------------------------------------------------------------------- logs


async def test_log_line_during_request_carries_request_id(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/health", headers={REQUEST_ID_HEADER: "req-log-1"})
    lines = entries(log_stream, "health check")
    assert len(lines) == 1
    assert lines[0]["request_id"] == "req-log-1"


async def test_generated_request_id_is_the_one_logged(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    response = await client.get("/health")
    (line,) = entries(log_stream, "health check")
    assert line["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_request_id_cleared_after_request(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/health", headers={REQUEST_ID_HEADER: "req-log-2"})
    assert "request_id" not in get_context()
    logging.getLogger("basis.tests.api").info("after request")
    (line,) = entries(log_stream, "after request")
    assert "request_id" not in line


async def test_concurrent_requests_log_their_own_ids(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    ids = [f"req-{i}" for i in range(15)]
    responses = await asyncio.gather(
        *(client.get("/health", headers={REQUEST_ID_HEADER: rid}) for rid in ids)
    )
    assert [r.headers[REQUEST_ID_HEADER] for r in responses] == ids
    logged = sorted(e["request_id"] for e in entries(log_stream, "health check"))
    assert logged == sorted(ids)


async def test_request_id_captured_by_caplog(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The context is live while the route runs, so any handler can read it."""
    seen: list[str | None] = []

    class Probe(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(get_context().get("request_id"))

    probe = Probe()
    logger = logging.getLogger("basis.api.health")
    logger.addHandler(probe)
    try:
        with caplog.at_level(logging.DEBUG, logger="basis.api.health"):
            await client.get("/health", headers={REQUEST_ID_HEADER: "req-caplog"})
    finally:
        logger.removeHandler(probe)
    assert seen == ["req-caplog"]
    assert any(r.getMessage() == "health check" for r in caplog.records)


# --------------------------------------------------------------------------- entry point


def test_main_module_builds_app_from_environment() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    previous_level = root.level
    try:
        main = importlib.import_module("basis.api.main")
        main = importlib.reload(main)
        assert isinstance(main.app, FastAPI)
        assert main.settings.env == "test"
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
        root.setLevel(previous_level)
