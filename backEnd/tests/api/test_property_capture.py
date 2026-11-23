"""Property pictures over HTTP (docs/api/PROPERTY_CAPTURE.md, EVAL_REPORT 98), on the real app
and database with a fake workflow starter:

* the pin: ``GET`` answers ``pin: null`` with ``ETag "0"``; ``PUT`` writes it whole with
  ``If-Match`` (412 BASIS-CAPTURE-016 with the current tag when stale), refuses half a point
  or a confirmation without one (422), records ``property.pin_set`` on the timeline, and a
  viewer may read but not write;
* ``POST .../captures``: 409 capture-off while ``PROPERTY_CAPTURE`` is off; 422
  capture-no-address without a pin; 202 queued with the workflow started once; 409
  capture-in-progress while one is active; 200 with the recent capture of the same pin; a
  start that fails is 503 BASIS-CAPTURE-017 and leaves the capture failed, not queued;
* a view's image is served with its sha256 as ``ETag`` and 304 on ``If-None-Match``;
  ``stale`` marks views of an earlier pin;
* ``GET /v1/ops/capture-incidents`` is operators only; ``GET /v1/ops/health`` carries the
  ``property_capture`` member; ``GET /v1/capabilities`` says ``property_capture``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, bearer, build_api_app
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.capture.starter import CaptureStartError
from basis.db.models import CaptureIncident, CaptureView, PropertyCapture, StudyEvent
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

OPERATOR = "ops-admin"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


class FakeStarter:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.fail = False

    async def start(self, capture_id: str) -> str:
        if self.fail:
            raise CaptureStartError("down")
        self.started.append(capture_id)
        return f"property-capture-{capture_id}"


@pytest.fixture
def starter() -> FakeStarter:
    return FakeStarter()


def app_with(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    starter: FakeStarter,
    **settings: Any,
) -> FastAPI:
    app = build_api_app(
        api_settings(database_url, ops_operator_uids=(OPERATOR,), **settings),
        sessionmaker,
        storage,
        verifier,
    )
    app.state.capture_starter = starter
    return app


@pytest.fixture
async def on(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    starter: FakeStarter,
) -> AsyncIterator[Api]:
    app = app_with(database_url, sessionmaker, storage, verifier, starter, property_capture=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield Api(client)


async def put_pin(api: Api, uid: str, sid: str, body: dict[str, Any], **headers: str) -> Any:
    return await api.client.put(
        f"/v1/studies/{sid}/property/pin",
        json=body,
        headers={**bearer(uid), "Idempotency-Key": str(uuid.uuid4()), **headers},
    )


async def test_the_pin_is_versioned_and_whole(on: Api, seed: Seed) -> None:
    sid = (await on.create_study("alice"))["id"]
    empty = await on.get("alice", f"/v1/studies/{sid}/property")
    assert empty.status_code == 200 and empty.headers["ETag"] == '"0"'
    assert empty.json() == {"pin": None, "capture": None, "enabled": True}
    first = await put_pin(
        on, "alice", sid, {"address": "1 Main St, Green Lake, WI"}, **{"If-Match": '"0"'}
    )
    assert first.status_code == 200, first.text
    assert first.headers["ETag"] == '"1"' and first.json()["source"] == "engineer"
    stale = await put_pin(on, "alice", sid, {"address": "2 Main St"}, **{"If-Match": '"0"'})
    assert stale.status_code == 412 and stale.json()["code"] == E.CAPTURE_PIN_PRECONDITION_FAILED
    assert stale.headers["ETag"] == '"1"'
    half = await put_pin(on, "alice", sid, {"address": "x", "lat": 43.8})
    assert half.status_code == 422
    unconfirmable = await put_pin(on, "alice", sid, {"address": "x", "confirmed": True})
    assert unconfirmable.status_code == 422
    confirmed = await put_pin(
        on,
        "alice",
        sid,
        {"address": "1 Main St", "lat": 43.85, "lon": -88.96, "confirmed": True},
        **{"If-Match": '"1"'},
    )
    body = confirmed.json()
    assert (body["confirmed"], body["version"], body["source"]) == (True, 2, "engineer")
    same = await put_pin(
        on, "alice", sid, {"address": "1 Main St", "lat": 43.85, "lon": -88.96, "confirmed": True}
    )
    assert same.json()["version"] == 2  # nothing changed, nothing written


async def test_starting_a_capture(
    on: Api, starter: FakeStarter, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = (await on.create_study("alice"))["id"]
    path = f"/v1/studies/{sid}/property/captures"
    no_pin = await on.post("alice", path, {})
    assert no_pin.status_code == 422 and no_pin.json()["code"] == E.CAPTURE_NO_ADDRESS
    await put_pin(on, "alice", sid, {"address": "1 Main St"})
    queued = await on.post("alice", path, {})
    assert queued.status_code == 202, queued.text
    capture = queued.json()
    assert capture["status"] == "queued" and capture["message"] == "Property pictures are queued."
    assert starter.started == [capture["id"]]
    busy = await on.post("alice", path, {})
    assert busy.status_code == 409 and busy.json()["code"] == E.CAPTURE_IN_PROGRESS
    async with session_scope(sessionmaker) as session:
        row = await session.get(PropertyCapture, uuid.UUID(capture["id"]))
        assert row is not None
        row.status, row.finished_at = "succeeded", datetime.now(UTC)
    recent = await on.post("alice", path, {})
    assert recent.status_code == 200 and recent.json()["id"] == capture["id"]
    forced = await on.post("alice", path, {"force": True})
    assert forced.status_code == 202 and len(starter.started) == 2
    async with session_scope(sessionmaker) as session:
        kinds = (
            await session.scalars(
                select(StudyEvent.event_type).where(StudyEvent.study_id == uuid.UUID(sid))
            )
        ).all()
        assert kinds.count("capture.requested") == 2 and "property.pin_set" in kinds


async def test_a_start_that_fails_leaves_the_capture_failed(
    on: Api, starter: FakeStarter, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", sid, {"address": "1 Main St"})
    starter.fail = True
    refused = await on.post("alice", f"/v1/studies/{sid}/property/captures", {})
    assert refused.status_code == 503 and refused.json()["code"] == E.CAPTURE_UNAVAILABLE
    async with session_scope(sessionmaker) as session:
        (row,) = (await session.scalars(select(PropertyCapture))).all()
        assert (row.status, row.code) == ("failed", E.CAPTURE_UNAVAILABLE)
    starter.fail = False
    assert (await on.post("alice", f"/v1/studies/{sid}/property/captures", {})).status_code == 202


async def test_captures_are_refused_while_off(api: Api, seed: Seed) -> None:
    sid = (await api.create_study("alice"))["id"]
    off = await api.post("alice", f"/v1/studies/{sid}/property/captures", {})
    assert off.status_code == 409 and off.json()["code"] == E.CAPTURE_OFF
    assert (await api.get("alice", "/v1/capabilities")).json()["property_capture"] is False


async def test_a_views_image_and_staleness(
    on: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", sid, {"address": "1 Main St", "lat": 43.85, "lon": -88.96})
    capture_id = (await on.post("alice", f"/v1/studies/{sid}/property/captures", {})).json()["id"]
    uri = f"gs://test-bucket/studies/{sid}/captures/{capture_id}/aerial-close.png"
    await storage.put(uri, PNG, "image/png")
    async with session_scope(sessionmaker) as session:
        view = CaptureView(
            study_id=uuid.UUID(sid),
            capture_id=uuid.UUID(capture_id),
            pin_version=1,
            kind="aerial",
            label="aerial-close",
            lat=43.85,
            lon=-88.96,
            ground_alt_m=250,
            range_m=120,
            fov_deg=35,
            heading_deg=0,
            tilt_deg=0,
            pixel_size_m=0.084,
            imagery_date="8/4/2026",
            width=1440,
            height=900,
            uri=uri,
            sha256="a" * 64,
            bytes=len(PNG),
            source="Aerial imagery",
            captured_at=datetime.now(UTC),
        )
        session.add(view)
        await session.flush()
        view_id = view.id
    image = await on.get("alice", f"/v1/studies/{sid}/property/views/{view_id}/image")
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["ETag"] == f'"{"a" * 64}"'
    cached = await on.get(
        "alice",
        f"/v1/studies/{sid}/property/views/{view_id}/image",
        headers={**bearer("alice"), "If-None-Match": image.headers["ETag"]},
    )
    assert cached.status_code == 304
    await put_pin(on, "alice", sid, {"address": "1 Main St", "lat": 43.86, "lon": -88.96})
    views = (await on.get("alice", f"/v1/studies/{sid}/property")).json()["capture"]["views"]
    assert [v["stale"] for v in views] == [True]
    assert (
        await on.get("bob", f"/v1/studies/{sid}/property/views/{view_id}/image")
    ).status_code == 404


async def test_operators_read_the_incidents_and_the_health_member(
    on: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(sessionmaker) as session:
        now = datetime.now(UTC)
        session.add(
            CaptureIncident(
                code=E.CAPTURE_RECAPTCHA,
                occurrences=3,
                first_seen_at=now,
                last_seen_at=now,
                last_url="https://earth.google.com/web/",
                last_http_status=200,
            )
        )
    assert (await on.get("alice", "/v1/ops/capture-incidents")).status_code == 403
    listed = (await on.get(OPERATOR, "/v1/ops/capture-incidents")).json()["items"]
    assert [(i["code"], i["occurrences"]) for i in listed] == [(E.CAPTURE_RECAPTCHA, 3)]
    assert "reCAPTCHA" in listed[0]["what"]
    health = (await on.get(OPERATOR, "/v1/ops/health")).json()["property_capture"]
    assert health["enabled"] is True and health["breaker"]["state"] == "closed"
    assert health["used"]["loads_hour_cap"] == 6
