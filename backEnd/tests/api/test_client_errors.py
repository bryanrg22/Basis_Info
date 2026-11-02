"""``POST /v1/client-errors`` and the operator reads ``GET /v1/ops/client-errors[/{id}]`` on
the real app and database (docs/api/CLIENT_ERRORS.md, EVAL_REPORT section 65).

* Any firm role posts (a viewer included); the reporter is the token's user whatever the
  event claims; the firm and study columns are set only when verified, the claims stay in
  the stored event; an event on a visible study appends ``client.error`` to the study's
  timeline in the same request (with the request id, the code, the record as entity) and
  shows in ``GET /v1/me/activity``, not in the audit; an event without a study, or on a
  study of another firm, writes no timeline row anywhere.
* The sample batch ``web/tests/fixtures/client-error-sample.json`` (written by the node test
  of ``live/errors.js``) is accepted as it is: the client's shape passes the server's schema.
* Secrets are masked anywhere in the stored event; the same ``event_id`` is stored once
  across batches; a batch over 256 KiB is 413 ``BASIS-API-045`` before validation; a
  malformed event is 422 with ``errors``; more than 30 batches a minute is 429.
* Operators list newest first with every filter and a keyset cursor, read one record with
  the whole event and ``_server``; a firm owner is 403 ``not-operator``; an unknown id 404.
* The correlation log lines: an unhandled exception's line carries the request id, the
  caller's uid and user id, the route template and the exception type; a problem's line the
  same; the study id and firm once a study route resolved them.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, bearer, build_api_app
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.routes import client_errors as route
from basis.api.schemas.client_errors import MAX_BATCH
from basis.db.enums import OrgRole
from basis.db.models import CLIENT_ERROR_KINDS, ClientError, StudyEvent
from basis.errors.codes import E
from basis.obs.logging import configure_logging
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

OPERATOR = "ops-admin"
REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE = REPO_ROOT / "web" / "tests" / "fixtures" / "client-error-sample.json"
PATH = "/v1/client-errors"
OPS = "/v1/ops/client-errors"


def event(**overrides: Any) -> dict[str, Any]:
    """A minimal valid event; ``overrides`` replace top-level members."""
    base: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "since_load_ms": 1234.5,
        "kind": "error",
        "message": "TypeError: Cannot read properties of undefined (reading 'rooms')",
        "error_type": "TypeError",
        "stack": "TypeError: x\n    at render (http://127.0.0.1:3100/live/live-app.js:120:9)",
        "source": {"file": "http://127.0.0.1:3100/live/live-app.js", "line": 120, "column": 9},
        "page": {"hash": "#rooms/{id}", "screen": "rooms", "path": "/"},
        "client": {"user_agent": "TestBrowser/1.0", "language": "en-US"},
        "build": {"api_git_sha": "abc1234", "web_version": "live-16"},
    }
    base.update(overrides)
    return base


def batch(*events: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"events": list(events), **extra}


@pytest.fixture
def ops_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    return build_api_app(
        api_settings(database_url, ops_operator_uids=(OPERATOR,), git_sha="server-sha"),
        sessionmaker,
        storage,
        verifier,
    )


@pytest.fixture
async def ops(ops_app: FastAPI) -> AsyncIterator[Api]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ops_app), base_url=BASE_URL
    ) as client:
        yield Api(client)


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


def lines(stream: io.StringIO, message: str) -> list[dict[str, Any]]:
    out = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    return [e for e in out if e["message"] == message]


async def rows(sessionmaker: async_sessionmaker[AsyncSession]) -> list[ClientError]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(ClientError).order_by(ClientError.seq))).all())


async def timeline(api: Api, uid: str, study_id: str) -> list[dict[str, Any]]:
    response = await api.get(
        uid, f"/v1/studies/{study_id}/timeline", params={"event_type": route.CLIENT_ERROR_EVENT}
    )
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


# --------------------------------------------------------------------------- posting


async def test_the_sample_batch_the_capture_module_produces_is_accepted(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sample = json.loads(SAMPLE.read_text())
    response = await api.post("alice", PATH, sample)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body == {
        "accepted": len(sample["events"]),
        "stored": len(sample["events"]),
        "duplicates": 0,
        "ids": body["ids"],
    }
    assert len(body["ids"]) == len(sample["events"])
    stored = await rows(sessionmaker)
    assert [r.kind for r in stored] == [e["kind"] for e in sample["events"]]
    api_event = stored[0]
    assert api_event.code == "BASIS-STUDY-003"
    assert api_event.http_status == 409
    assert api_event.request_id == "r-2"
    assert api_event.route == "#rooms/{id}"
    assert api_event.screen == "rooms"
    assert api_event.build_sha is None  # /health had not answered when the sample was built
    assert api_event.web_version == "live-16"
    assert api_event.user_agent == "TestBrowser/1.0"
    assert api_event.study_id is None  # the sample's study is not alice's
    assert api_event.event["_server"]["study_visible"] is False
    # The secret-looking key of the report's context was masked by the client and again here.
    report = stored[2]
    assert report.event["context"] == {"action": "move-photo", "apiKey": "***"}


async def test_every_detail_is_stored_and_the_timeline_gets_the_event(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], api_app: FastAPI
) -> None:
    study = await api.create_study("alice")
    org_id = await seed.personal_org("alice")
    occurred = datetime.now(UTC) - timedelta(seconds=3)
    sent = datetime.now(UTC) - timedelta(milliseconds=1500)
    ev = event(
        occurred_at=occurred.astimezone().isoformat(),
        kind="api",
        level="warning",
        message="POST /v1/studies/{id}/decisions failed with 409 (BASIS-STUDY-003)",
        error_type="ApiError",
        stack=None,
        source=None,
        user={"uid": "alice", "email": "alice@example.test", "role": "owner"},
        firm={"org_id": str(org_id), "org_name": "Alice's firm"},
        study={"study_id": study["id"], "revision": 1, "stage": "rooms_review", "status": "x"},
        http={
            "method": "POST",
            "route": "/v1/studies/{id}/decisions",
            "status": 409,
            "code": "BASIS-STUDY-003",
            "request_id": "req-prev",
            "problem_type": "https://basis.invalid/problems/stale-state",
            "problem_title": "Conflict",
            "problem_detail": "revision 1 is stale",
            "latency_ms": 12.5,
            "attempt": 2,
            "retry": True,
            "body_fields": ["stage", "revision", "decision"],
        },
        breadcrumbs=[
            {
                "at": occurred.isoformat(),
                "since_load_ms": 1000,
                "kind": "click",
                "message": "approve rooms_review",
                "data": {"action": "approve"},
            }
        ],
        client={
            "user_agent": "TestBrowser/1.0",
            "platform": "MacIntel",
            "language": "en-US",
            "languages": ["en-US"],
            "viewport": {"width": 1280, "height": 720},
            "device_pixel_ratio": 2,
            "online": True,
            "timezone": "America/New_York",
            "connection": "4g",
            "visibility": "visible",
            "cookies_enabled": True,
        },
        context={"note": "kept", "password": "p"},
        repeats=3,
        dropped=2,
        capture_version="client-errors-1",
    )
    response = await api.post("alice", PATH, batch(ev, sent_at=sent.isoformat()))
    assert response.status_code == 202, response.text
    request_id = response.headers["x-request-id"]
    (row,) = await rows(sessionmaker)
    assert row.event_id == uuid.UUID(ev["event_id"])
    assert row.occurred_at == occurred
    assert row.org_id == org_id
    assert row.study_id == uuid.UUID(study["id"])
    assert (row.revision, row.stage, row.route, row.screen) == (
        1,
        "rooms_review",
        "#rooms/{id}",
        "rooms",
    )
    assert (row.kind, row.error_type) == ("api", "ApiError")
    assert (row.code, row.http_status, row.request_id) == ("BASIS-STUDY-003", 409, "req-prev")
    assert (row.build_sha, row.web_version, row.user_agent) == (
        "abc1234",
        "live-16",
        "TestBrowser/1.0",
    )
    stored = row.event
    assert stored["http"] == ev["http"]
    assert stored["breadcrumbs"][0]["data"] == {"action": "approve"}
    assert stored["client"] == ev["client"]
    assert stored["context"] == {"note": "kept", "password": "***"}
    assert (stored["repeats"], stored["dropped"]) == (3, 2)
    server = stored["_server"]
    assert server["api_request_id"] == request_id
    assert server["api_git_sha"] == api_app.state.settings.git_sha  # what /health reports
    assert server["sent_at"] == sent.isoformat()
    assert -5000 < server["clock_offset_ms"] <= 0
    assert server["reporter"]["uid"] == "alice"
    assert server["org_verified"] is True and server["study_visible"] is True
    # The study's timeline has the event with provenance.
    (item,) = await timeline(api, "alice", study["id"])
    assert item["event_type"] == "client.error"
    assert item["actor_type"] == "engineer"
    assert item["actor_id"] == server["reporter"]["user_id"]
    assert item["request_id"] == request_id
    assert item["code"] == "BASIS-STUDY-003"
    assert item["entity_type"] == "client_error"
    assert item["entity_id"] == response.json()["ids"][0]
    assert (item["stage"], item["revision"]) == ("uploading", 1)  # the row's, not the claim
    detail = item["detail"]
    assert detail["kind"] == "api"
    assert detail["http"] == {
        "method": "POST",
        "route": "/v1/studies/{id}/decisions",
        "status": 409,
        "code": "BASIS-STUDY-003",
        "request_id": "req-prev",
    }
    assert detail["client_stage"] == "rooms_review"
    assert detail["event_id"] == ev["event_id"]
    # Listed under the user's own activity; not an engineer action in the audit.
    activity = await api.get("alice", "/v1/me/activity", params={"event_type": "client.error"})
    assert [i["study_id"] for i in activity.json()["items"]] == [study["id"]]
    audit = await api.get("alice", f"/v1/studies/{study['id']}/audit")
    assert all(i["kind"] != "client.error" for i in audit.json()["items"])


async def test_a_viewer_may_post_and_gets_their_one_firm(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    org_id = await seed.personal_org("alice")
    await seed.member(org_id, "vera", OrgRole.VIEWER, own_firm=False)
    response = await api.post("vera", PATH, batch(event(study={"study_id": study["id"]})))
    assert response.status_code == 202, response.text
    (row,) = await rows(sessionmaker)
    assert row.org_id == org_id  # the one firm, though the event named none
    assert row.study_id == uuid.UUID(study["id"])
    assert len(await timeline(api, "vera", study["id"])) == 1


async def test_claims_the_reporter_cannot_make_stay_inside_the_event(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Another firm's study and firm id, and a user claim: no column, no timeline row."""
    alices = await api.create_study("alice")
    alice_org = await seed.personal_org("alice")
    await api.create_study("mallory")  # mallory has a firm of her own
    response = await api.post(
        "mallory",
        PATH,
        batch(
            event(
                user={"uid": "alice", "email": "alice@example.test", "role": "owner"},
                firm={"org_id": str(alice_org), "org_name": "not mine"},
                study={"study_id": alices["id"], "revision": 1},
            ),
            event(study={"study_id": str(uuid.uuid4())}),  # a study that does not exist
        ),
    )
    assert response.status_code == 202, response.text
    stored = await rows(sessionmaker)
    mallory_org = await seed.personal_org("mallory")
    # The first event claimed alice's firm (refused: null); the second claimed none, and
    # mallory belongs to exactly one firm, so that one is hers.
    assert [r.org_id for r in stored] == [None, mallory_org]
    assert [r.study_id for r in stored] == [None, None]
    assert stored[0].event["firm"]["org_id"] == str(alice_org)
    assert stored[0].event["study"]["study_id"] == alices["id"]
    assert stored[0].event["_server"]["org_verified"] is False
    async with sessionmaker() as session:
        mallory_id = (await session.execute(select(ClientError.user_id))).scalars().first()
    assert str(mallory_id) == stored[0].event["_server"]["reporter"]["user_id"]
    assert await timeline(api, "alice", alices["id"]) == []
    async with sessionmaker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(StudyEvent)
            .where(StudyEvent.event_type == route.CLIENT_ERROR_EVENT)
        )
    assert count == 0


async def test_an_event_without_a_study_is_stored_and_writes_no_timeline_row(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    response = await api.post("alice", PATH, batch(event(kind="console", message="oops")))
    assert response.status_code == 202, response.text
    (row,) = await rows(sessionmaker)
    assert row.study_id is None and row.kind == "console"
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(StudyEvent)) == 0


async def test_a_code_that_is_not_a_catalogue_code_is_not_indexed(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ev = event(kind="api", http={"status": 500, "code": "not-a-code"})
    assert (await api.post("alice", PATH, batch(ev))).status_code == 202
    (row,) = await rows(sessionmaker)
    assert row.code is None and row.http_status == 500
    assert row.event["http"]["code"] == "not-a-code"


async def test_the_same_event_id_is_stored_once_across_batches_and_a_replay(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ev = event()
    first = await api.post("alice", PATH, batch(ev), key="batch-1")
    assert first.json()["stored"] == 1
    again = await api.post("alice", PATH, batch(ev, event()), key="batch-2")
    assert again.status_code == 202
    assert (again.json()["stored"], again.json()["duplicates"]) == (1, 1)
    replay = await api.post("alice", PATH, batch(ev), key="batch-1")
    assert replay.headers.get("idempotent-replayed") == "true"
    assert replay.json() == first.json()
    assert len(await rows(sessionmaker)) == 2


async def test_secrets_are_masked_anywhere_in_the_stored_event(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ev = event(
        context={"authorization": "Bearer x", "nested": {"api_token": "t", "fine": 1}},
        breadcrumbs=[
            {
                "at": datetime.now(UTC).isoformat(),
                "kind": "custom",
                "data": {"secret_key": "s", "list": [{"password": "p"}]},
            }
        ],
    )
    assert (await api.post("alice", PATH, batch(ev))).status_code == 202
    (row,) = await rows(sessionmaker)
    assert row.event["context"] == {
        "authorization": "***",
        "nested": {"api_token": "***", "fine": 1},
    }
    assert row.event["breadcrumbs"][0]["data"] == {
        "secret_key": "***",
        "list": [{"password": "***"}],
    }
    assert "Bearer x" not in json.dumps(row.event)


# --------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("body", "where"),
    [
        ({"events": []}, ["body", "events"]),
        ({"events": [event() for _ in range(MAX_BATCH + 1)]}, ["body", "events"]),
        ({"events": [event(message="")]}, ["body", "events", 0, "message"]),
        ({"events": [event(message="a\x00b")]}, ["body", "events", 0, "message"]),
        ({"events": [event(kind="panic")]}, ["body", "events", 0, "kind"]),
        (
            {"events": [event(occurred_at="2026-11-01T10:00:00")]},
            ["body", "events", 0, "occurred_at"],
        ),
        ({"events": [event(extra_member=1)]}, ["body", "events", 0, "extra_member"]),
        ({"events": [event(stack="x" * 16001)]}, ["body", "events", 0, "stack"]),
        ({"events": [event(http={"status": 1000})]}, ["body", "events", 0, "http", "status"]),
        ({"events": [event(context={"big": "x" * 16001})]}, ["body", "events", 0, "context"]),
        (
            {"events": [event(breadcrumbs=[{"kind": "click"}])]},
            ["body", "events", 0, "breadcrumbs", 0, "at"],
        ),
        ({"events": [event(repeats=0)]}, ["body", "events", 0, "repeats"]),
        ({"events": [{"event_id": "nope"}]}, ["body", "events", 0, "event_id"]),
        ({"batch": []}, ["body", "events"]),
    ],
)
async def test_a_malformed_batch_is_422_naming_the_field(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession], body: dict[str, Any], where: list[Any]
) -> None:
    response = await api.post("alice", PATH, body)
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == E.API_VALIDATION
    assert where in [e["loc"] for e in problem["errors"]], problem["errors"]
    assert await rows(sessionmaker) == []


async def test_a_batch_over_256_kib_is_413_before_validation(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Twenty events each with the largest stack the schema allows: valid, and over the cap.
    big = batch(*(event(stack="s" * 16000) for _ in range(MAX_BATCH)))
    assert len(json.dumps(big)) > route.MAX_BATCH_BYTES
    response = await api.post("alice", PATH, big)
    assert response.status_code == 413, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == E.API_CLIENT_ERROR_BATCH_TOO_LARGE
    # Junk of the same size: still 413, not 422 (the size check runs first).
    junk = {"events": "x" * (route.MAX_BATCH_BYTES + 10)}
    assert (await api.post("alice", PATH, junk)).status_code == 413
    # Just under the cap with the same junk: 422, the schema's answer.
    small = {"events": "x" * 1000}
    assert (await api.post("alice", PATH, small)).status_code == 422
    assert await rows(sessionmaker) == []


async def test_more_than_30_batches_a_minute_is_429(api: Api) -> None:
    for _ in range(route.BATCHES_PER_MIN):
        assert (await api.post("alice", PATH, batch(event()))).status_code == 202
    refused = await api.post("alice", PATH, batch(event()))
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    assert refused.json()["code"] == E.API_RATE_LIMITED
    # Another user's budget is their own.
    assert (await api.post("bob", PATH, batch(event()))).status_code == 202


async def test_without_a_token_or_a_firm_the_route_still_needs_a_user(
    api: Api, invite_only_api: Api
) -> None:
    assert (await api.client.post(PATH, json=batch(event()))).status_code == 401
    # A signed-in user with no firm yet can still report (the firm column stays null).
    response = await invite_only_api.post("nofirm", PATH, batch(event()))
    assert response.status_code == 202, response.text


# --------------------------------------------------------------------------- operators


async def seed_records(ops: Api, seed: Seed) -> dict[str, Any]:
    study = await ops.create_study("alice")
    alice_org = await seed.personal_org("alice")
    await ops.create_study("bob")
    bob_org = await seed.personal_org("bob")
    at = datetime.now(UTC)
    posted = [
        ("alice", event(kind="error", occurred_at=(at - timedelta(minutes=3)).isoformat())),
        (
            "alice",
            event(
                kind="api",
                study={"study_id": study["id"]},
                http={"status": 409, "code": "BASIS-STUDY-003", "request_id": "req-9"},
            ),
        ),
        ("bob", event(kind="console")),
    ]
    ids = []
    for uid, ev in posted:
        response = await ops.post(uid, PATH, batch(ev))
        assert response.status_code == 202, response.text
        ids.extend(response.json()["ids"])
    return {"study": study, "alice_org": alice_org, "bob_org": bob_org, "ids": ids}


async def test_operators_list_newest_first_with_filters_and_a_cursor(ops: Api, seed: Seed) -> None:
    seeded = await seed_records(ops, seed)
    page = await ops.get(OPERATOR, OPS)
    assert page.status_code == 200, page.text
    items = page.json()["items"]
    assert [i["id"] for i in items] == list(reversed(seeded["ids"]))
    assert items[0]["kind"] == "console" and items[0]["org_id"] == str(seeded["bob_org"])
    assert set(items[0]) == {
        "id", "seq", "event_id", "occurred_at", "received_at", "user_id", "org_id", "study_id",
        "revision", "stage", "route", "screen", "kind", "error_type", "message", "code",
        "http_status", "request_id", "build_sha", "web_version", "user_agent",
    }  # fmt: skip
    assert "event" not in items[0]

    async def ids(**params: Any) -> list[str]:
        response = await ops.get(OPERATOR, OPS, params=params)
        assert response.status_code == 200, response.text
        return [i["id"] for i in response.json()["items"]]

    a, b, c = seeded["ids"]
    assert await ids(org_id=str(seeded["alice_org"])) == [b, a]
    assert await ids(org_id=str(seeded["bob_org"])) == [c]
    assert await ids(user_id=items[0]["user_id"]) == [c]
    assert await ids(study_id=seeded["study"]["id"]) == [b]
    assert await ids(code="BASIS-STUDY-003") == [b]
    assert await ids(kind="error") == [a]
    assert await ids(request_id="req-9") == [b]
    assert await ids(since=(datetime.now(UTC) + timedelta(minutes=1)).isoformat()) == []
    assert await ids(until=(datetime.now(UTC) + timedelta(minutes=1)).isoformat()) == [c, b, a]
    assert await ids(since=(datetime.now(UTC) - timedelta(minutes=1)).isoformat()) == [c, b, a]
    # Pages of one, newest first, no gaps or repeats.
    seen: list[str] = []
    cursor = None
    while True:
        response = await ops.get(
            OPERATOR, OPS, params={"limit": 1, **({"cursor": cursor} if cursor else {})}
        )
        body = response.json()
        seen.extend(i["id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [c, b, a]


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "abc"},
        {"limit": 0},
        {"limit": 201},
        {"code": "nope"},
        {"kind": "panic"},
        {"since": "5"},
        {"org_id": "not-a-uuid"},
        {"request_id": "a b"},
    ],
)
async def test_operators_get_422_for_bad_parameters(ops: Api, params: dict[str, Any]) -> None:
    response = await ops.get(OPERATOR, OPS, params=params)
    assert response.status_code == 422, response.text


async def test_operators_read_one_record_with_the_whole_event(ops: Api, seed: Seed) -> None:
    seeded = await seed_records(ops, seed)
    response = await ops.get(OPERATOR, f"{OPS}/{seeded['ids'][1]}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["study_id"] == seeded["study"]["id"]
    assert body["event"]["http"]["request_id"] == "req-9"
    assert body["event"]["_server"]["api_git_sha"] == "server-sha"
    assert body["event"]["_server"]["study_visible"] is True
    missing = await ops.get(OPERATOR, f"{OPS}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == E.API_NOT_FOUND
    assert (await ops.get(OPERATOR, f"{OPS}/not-a-uuid")).status_code == 422


async def test_a_firm_owner_is_not_an_operator(ops: Api, seed: Seed) -> None:
    seeded = await seed_records(ops, seed)
    for path in (OPS, f"{OPS}/{seeded['ids'][0]}"):
        response = await ops.get("alice", path)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == E.API_NOT_OPERATOR
        assert "event" not in response.text
    # With no operators configured (the default app), nobody reads.
    assert (await ops.client.get(OPS, headers=bearer(OPERATOR))).status_code == 200
    assert (await ops.client.get(OPS)).status_code == 401


# --------------------------------------------------------------------------- correlation


async def test_server_log_lines_carry_the_correlation_fields(
    api: Api, log_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    from basis.db.repositories import StudyRepository

    study = await api.create_study("alice")

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(StudyRepository, "list_for_user", boom)
    crashed = await api.client.get(
        "/v1/studies", headers={**bearer("alice"), "X-Request-Id": "req-crash"}
    )
    assert crashed.status_code == 500
    (line,) = lines(log_stream, "unhandled exception")
    assert line["request_id"] == "req-crash"
    assert line["uid"] == "alice"
    assert uuid.UUID(line["user_id"])
    assert line["route"] == "/v1/studies"
    assert line["http_method"] == "GET"
    assert line["status"] == 500
    assert line["exception_type"] == "RuntimeError"
    assert line["exception_message"] == "secret internals"
    assert line["error_code"] == E.API_INTERNAL_ERROR
    assert "Traceback" in line["stack_trace"]
    assert line["severity"] == "ERROR"
    # A refusal on a study route names the study and the firm; a 404 has the study id only.
    forbidden = await api.client.post(
        f"/v1/studies/{study['id']}/decisions",
        json={"stage": "appraisal_review", "revision": 1, "decision": "approve"},
        headers={**bearer("alice"), "X-Request-Id": "req-problem", "Idempotency-Key": "k1"},
    )
    assert forbidden.status_code == 409, forbidden.text
    (problem,) = [p for p in lines(log_stream, "problem") if p["request_id"] == "req-problem"]
    assert problem["study_id"] == study["id"]
    assert problem["org_id"] and problem["role"] == "owner"
    assert problem["route"] == "/v1/studies/{study_id}/decisions"
    assert problem["error_code"].startswith("BASIS-")
    missing = await api.client.get(
        f"/v1/studies/{uuid.uuid4()}", headers={**bearer("alice"), "X-Request-Id": "req-404"}
    )
    assert missing.status_code == 404
    (p404,) = [p for p in lines(log_stream, "problem") if p["request_id"] == "req-404"]
    assert p404["study_id"] and "org_id" not in p404
    # The reporter's log line for a stored event.
    response = await api.client.post(
        PATH,
        json=batch(
            event(
                kind="api", http={"status": 500, "code": "BASIS-API-024", "request_id": "req-crash"}
            )
        ),
        headers={**bearer("alice"), "X-Request-Id": "req-report", "Idempotency-Key": "k2"},
    )
    assert response.status_code == 202
    (recorded,) = lines(log_stream, "client error recorded")
    assert recorded["request_id"] == "req-report"
    assert recorded["client_request_id"] == "req-crash"
    assert recorded["client_code"] == "BASIS-API-024"
    assert recorded["error_code"] == E.CLIENT_ERROR_RECORDED
    assert recorded["uid"] == "alice"


def test_the_kinds_the_schema_accepts_are_the_kinds_the_table_allows() -> None:
    from typing import get_args

    from basis.api.schemas.client_errors import ClientErrorKind

    assert tuple(get_args(ClientErrorKind)) == CLIENT_ERROR_KINDS
