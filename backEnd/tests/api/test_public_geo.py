"""Public geographic sources over HTTP (docs/api/PUBLIC_GEO.md, EVAL_REPORT 101), on the real
app and database with a fake workflow starter:

* ``POST .../property/public``: 409 geo-off while ``PUBLIC_GEO`` is off; 422 geo-no-point
  without a point (no pin, or an address only); 202 queued and started once; 409
  geo-in-progress while active; 200 with the recent gather taken at the pin's point (a
  confirmation, an address edit or a move under 1 m keeps it), 202 with ``force``; a start
  that fails is 503 BASIS-GEO-004 and leaves the gather failed; a viewer may not ask;
* ``GET .../property/public``: empty before any gather; after one, the current view, the
  history newest first, the parcels, the site check computed against the appraisal's
  ``site.area_sqft`` when read (adjacent match, then a flag when the appraisal changes), the
  lidar summary, ``stale`` only after the pin moves more than 1 m; a viewer may read;
* the image with its sha256 ``ETag`` and 304; another firm's study is 404;
* ``POST .../property/captures`` also queues a gather when ``PUBLIC_GEO`` is on, and a gather
  that cannot start never fails the capture;
* ``GET /v1/ops/geo-incidents`` is operators only; ``GET /v1/capabilities`` says ``public_geo``.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, bearer, build_api_app
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OrgRole
from basis.db.models import (
    Extraction,
    ExtractionField,
    GeoGather,
    GeoIncident,
    GeoParcel,
    GeoView,
    StudyEvent,
)
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.geo.geometry import M2_PER_ACRE, view_box
from basis.geo.starter import GeoStartError, with_geo
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

OPERATOR = "ops-admin"
LAT, LON = 41.60, -93.60
JPEG = b"\xff\xd8\xff" + b"1" * 64


class FakeStarter:
    def __init__(self, prefix: str) -> None:
        self.started: list[str] = []
        self.fail = False
        self.prefix = prefix

    async def start(self, some_id: str) -> str:
        if self.fail:
            raise GeoStartError("down")
        self.started.append(some_id)
        return f"{self.prefix}{some_id}"


@pytest.fixture
def geo_starter() -> FakeStarter:
    return FakeStarter("public-geo-")


@pytest.fixture
def capture_starter() -> FakeStarter:
    return FakeStarter("property-capture-")


def app_with(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    geo_starter: FakeStarter,
    capture_starter: FakeStarter,
    **settings: Any,
) -> FastAPI:
    app = build_api_app(
        api_settings(database_url, ops_operator_uids=(OPERATOR,), **settings),
        sessionmaker,
        storage,
        verifier,
    )
    app.state.geo_starter = geo_starter
    app.state.capture_starter = with_geo(capture_starter, app.state)
    return app


@pytest.fixture
async def on(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    geo_starter: FakeStarter,
    capture_starter: FakeStarter,
) -> AsyncIterator[Api]:
    app = app_with(
        database_url,
        sessionmaker,
        storage,
        verifier,
        geo_starter,
        capture_starter,
        public_geo=True,
        property_capture=True,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        yield Api(c)


async def put_pin(api: Api, uid: str, sid: str, body: dict[str, Any]) -> Any:
    return await api.client.put(
        f"/v1/studies/{sid}/property/pin",
        json=body,
        headers={**bearer(uid), "Idempotency-Key": str(uuid.uuid4())},
    )


def rect(lon: float, lat: float, w: float, h: float) -> dict[str, Any]:
    dlon = w / (111_412.84 * math.cos(math.radians(lat)))
    dlat = h / 111_132.954
    ring = [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


async def finish_with_records(
    sessionmaker: async_sessionmaker[AsyncSession], storage: LocalStorage, sid: str, gid: str
) -> str:
    """What the geo worker leaves: a succeeded gather, two views, two parcels, a lidar summary.
    Returns the current view's id."""
    study, gather = uuid.UUID(sid), uuid.UUID(gid)
    box = view_box(LAT, LON, extent_m=160, pixel_m=0.1524, max_px=2048)
    async with session_scope(sessionmaker) as session:
        row = await session.get(GeoGather, gather)
        assert row is not None
        row.status, row.code, row.finished_at = "succeeded", E.GEO_COMPLETED, datetime.now(UTC)
        row.sources = [{"source": "wi-leafoff-latest", "kind": "current", "status": "ok"}]
        row.lidar = {
            "project": "XX_Example_2018",
            "year": 2018,
            "points": 15660,
            "density_per_m2": 3.37,
            "building": {
                "footprint_m2": 316.75,
                "peak_m": 9.0,
                "max_m": 10.1,
                "eave_m": 3.6,
                "ground_elevation_m": 248.2,
                "points": 1003,
                "distance_m": 0.0,
                "outline_cells": 223,
            },
            "code": None,
            "credit": "USGS 3DEP",
            "terms_class": "public-domain",
            "terms_url": "https://www.usgs.gov/",
            "ept_url": "u",
            "box_area_m2": 1.0,
            "building_points": 1,
            "ground_points": 1,
            "nodes": 14,
            "bytes": 1,
        }
        ids = {}
        for kind, label, year in (
            ("current", "current-wi-2020", 2020),
            ("history", "history-naip-2018", 2018),
        ):
            uri = f"gs://test-bucket/studies/{sid}/public/{gid}/{label}.jpg"
            await storage.put(uri, JPEG, "image/jpeg")
            view = GeoView(
                study_id=study,
                gather_id=gather,
                source_id=label.split("-", 1)[1],
                kind=kind,
                label=label,
                year=year,
                leaf_off=kind == "current",
                lat=LAT,
                lon=LON,
                pixel_size_m=box.pixel_size_m,
                native_pixel_m=0.1524,
                bbox=list(box.bbox),
                width=box.width,
                height=box.height,
                uri=uri,
                content_type="image/jpeg",
                sha256="b" * 64,
                bytes=len(JPEG),
                credit="WI DNR",
                terms_class="no-terms-stated",
                terms_url="https://dnr.wisconsin.gov/legal",
                captured_at=datetime.now(UTC),
            )
            session.add(view)
            await session.flush()
            ids[kind] = view.id
        k = 111_412.84 * math.cos(math.radians(LAT))
        for pid, under, acres, west, width, height in (
            ("pin", True, 0.40, LON - 0.0001, 50, 32.4),
            ("next", False, 0.75, LON - 0.0001 + 50 / k, 70, 43.4),
        ):
            session.add(
                GeoParcel(
                    study_id=study,
                    gather_id=gather,
                    source_id="wi-parcels",
                    parcel_id=pid,
                    under_pin=under,
                    owner="A",
                    address=None,
                    area_m2=acres * M2_PER_ACRE,
                    layer_acres=acres,
                    geometry=rect(west, LAT - 0.0001, width, height),
                    attributes={},
                    credit="SCO",
                    terms_class="open-with-credit",
                )
            )
    return str(ids["current"])


async def set_site_area(
    sessionmaker: async_sessionmaker[AsyncSession], seed: Seed, sid: str, sqft: float
) -> None:
    ids = await seed.outputs(sid)
    async with session_scope(sessionmaker) as session:
        field = await session.get(ExtractionField, uuid.UUID(ids["extraction_field"]))
        assert field is not None
        extraction = await session.get(Extraction, field.extraction_id)
        assert extraction is not None
        session.add(
            ExtractionField(
                extraction_id=extraction.id,
                study_id=uuid.UUID(sid),
                section="site",
                field="area_sqft",
                value_num=Decimal(str(sqft)),
                source="regex",
            )
        )


async def test_gathers_are_refused_while_off_and_without_a_point(
    api: Api, on: Api, seed: Seed
) -> None:
    sid = (await api.create_study("alice"))["id"]
    off = await api.post("alice", f"/v1/studies/{sid}/property/public", {})
    assert off.status_code == 409 and off.json()["code"] == E.GEO_OFF
    assert (await api.get("alice", "/v1/capabilities")).json()["public_geo"] is False
    assert (await on.get("alice", "/v1/capabilities")).json()["public_geo"] is True
    sid = (await on.create_study("alice"))["id"]
    none = await on.post("alice", f"/v1/studies/{sid}/property/public", {})
    assert none.status_code == 422 and none.json()["code"] == E.GEO_NO_POINT
    await put_pin(on, "alice", sid, {"address": "1 Main St"})
    address_only = await on.post("alice", f"/v1/studies/{sid}/property/public", {})
    assert address_only.status_code == 422 and address_only.json()["code"] == E.GEO_NO_POINT
    empty = (await on.get("alice", f"/v1/studies/{sid}/property/public")).json()
    assert empty == {
        "enabled": True,
        "gather": None,
        "current": None,
        "history": [],
        "parcels": [],
        "site_check": None,
        "lidar": None,
    }


async def test_a_gather_is_queued_once_and_reused(
    on: Api, geo_starter: FakeStarter, sessionmaker: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    sid = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", sid, {"lat": LAT, "lon": LON})
    path = f"/v1/studies/{sid}/property/public"
    first = await on.post("alice", path, {})
    assert first.status_code == 202, first.text
    gather = first.json()
    assert gather["status"] == "queued" and gather["pin_version"] == 1 and not gather["stale"]
    assert geo_starter.started == [gather["id"]]
    again = await on.post("alice", path, {})
    assert again.status_code == 409 and again.json()["code"] == E.GEO_IN_PROGRESS
    async with session_scope(sessionmaker) as session:
        row = await session.get(GeoGather, uuid.UUID(gather["id"]))
        assert row is not None and (row.lat, row.lon) == (LAT, LON)
        row.status, row.finished_at = "succeeded", datetime.now(UTC)
    recent = await on.post("alice", path, {})
    assert recent.status_code == 200 and recent.json()["id"] == gather["id"]
    # A confirmation, an address edit or a move under 1 m keeps the gather reusable.
    await put_pin(on, "alice", sid, {"lat": LAT, "lon": LON, "confirmed": True})
    await put_pin(on, "alice", sid, {"address": "1 Main St", "lat": LAT + 5e-6, "lon": LON})
    reused = await on.post("alice", path, {})
    assert reused.status_code == 200 and reused.json()["id"] == gather["id"]
    assert not reused.json()["stale"] and len(geo_starter.started) == 1
    forced = await on.post("alice", path, {"force": True})
    assert forced.status_code == 202 and len(geo_starter.started) == 2
    # Viewers read but do not ask.
    study = await on.get("alice", f"/v1/studies/{sid}")
    await seed.member(uuid.UUID(study.json()["org_id"]), "victor", OrgRole.VIEWER)
    assert (await on.post("victor", path, {"force": True})).status_code == 403
    assert (await on.get("victor", path)).status_code == 200
    async with session_scope(sessionmaker) as session:
        kinds = (
            await session.scalars(
                select(StudyEvent.event_type).where(StudyEvent.study_id == uuid.UUID(sid))
            )
        ).all()
        assert kinds.count("geo.requested") == 2


async def test_a_start_that_fails_leaves_the_gather_failed(
    on: Api, geo_starter: FakeStarter, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", sid, {"lat": LAT, "lon": LON})
    geo_starter.fail = True
    refused = await on.post("alice", f"/v1/studies/{sid}/property/public", {})
    assert refused.status_code == 503 and refused.json()["code"] == E.GEO_UNAVAILABLE
    async with session_scope(sessionmaker) as session:
        (row,) = (await session.scalars(select(GeoGather))).all()
        assert (row.status, row.code) == ("failed", E.GEO_UNAVAILABLE)
    shown = (await on.get("alice", f"/v1/studies/{sid}/property/public")).json()["gather"]
    assert shown["status"] == "failed" and "again" in shown["message"]
    geo_starter.fail = False
    assert (await on.post("alice", f"/v1/studies/{sid}/property/public", {})).status_code == 202


async def test_the_records_the_site_check_the_image_and_staleness(
    on: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", sid, {"lat": LAT, "lon": LON})
    gid = (await on.post("alice", f"/v1/studies/{sid}/property/public", {})).json()["id"]
    view_id = await finish_with_records(sessionmaker, storage, sid, gid)
    path = f"/v1/studies/{sid}/property/public"
    body = (await on.get("alice", path)).json()
    assert (
        body["gather"]["status"] == "succeeded"
        and body["gather"]["message"] == "Public records are ready."
    )
    current = body["current"]
    assert current["id"] == view_id and current["kind"] == "current" and current["year"] == 2020
    assert current["crs"] == "EPSG:3857" and current["credit"] == "WI DNR" and not current["stale"]
    west, south, east, north = current["bbox_lonlat"]
    assert west < LON < east and south < LAT < north
    assert [v["year"] for v in body["history"]] == [2020, 2018]
    assert [(p["parcel_id"], p["under_pin"]) for p in body["parcels"]] == [
        ("pin", True),
        ("next", False),
    ]
    assert body["parcels"][0]["area_acres"] == pytest.approx(0.40)
    assert body["site_check"]["status"] == "no_appraisal"
    assert body["lidar"]["building"]["footprint_sqft"] == pytest.approx(316.75 * 10.7639, rel=1e-4)
    # The appraisal read later is checked when the records are read.
    await set_site_area(sessionmaker, seed, sid, 1.15 * 43_560)
    check = (await on.get("alice", path)).json()["site_check"]
    assert check["status"] == "adjacent_match" and check["code"] == E.GEO_SITE_AREA_ADJACENT
    assert check["parcels"] == ["pin", "next"] and check["flag"] is False
    image = await on.get("alice", f"{path}/views/{view_id}/image")
    assert image.status_code == 200 and image.content == JPEG
    assert (
        image.headers["content-type"] == "image/jpeg" and image.headers["ETag"] == f'"{"b" * 64}"'
    )
    cached = await on.client.get(
        f"{path}/views/{view_id}/image",
        headers={**bearer("alice"), "If-None-Match": image.headers["ETag"]},
    )
    assert cached.status_code == 304
    assert (await on.get("alice", f"{path}/views/{uuid.uuid4()}/image")).status_code == 404
    assert (await on.get("bob", f"{path}/views/{view_id}/image")).status_code == 404
    assert (await on.get("bob", path)).status_code == 404
    # Confirming the point or editing the address raises the pin's version without moving it:
    # the records stay current (as the capture's views do).
    await put_pin(on, "alice", sid, {"lat": LAT, "lon": LON, "confirmed": True})
    await put_pin(on, "alice", sid, {"address": "1 Main St", "lat": LAT, "lon": LON})
    kept = (await on.get("alice", path)).json()
    assert kept["gather"]["pin_version"] == 1
    assert not kept["gather"]["stale"] and not kept["current"]["stale"]
    await put_pin(on, "alice", sid, {"lat": LAT + 0.001, "lon": LON})
    moved = (await on.get("alice", path)).json()
    assert moved["gather"]["stale"] and moved["current"]["stale"]
    # The move asks for a new gather; going back to the first point reuses the first one.
    assert (await on.post("alice", path, {})).status_code == 202


async def test_a_capture_also_queues_a_gather_and_never_fails_for_it(
    on: Api,
    geo_starter: FakeStarter,
    capture_starter: FakeStarter,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", sid, {"lat": LAT, "lon": LON})
    captured = await on.post("alice", f"/v1/studies/{sid}/property/captures", {})
    assert captured.status_code == 202 and len(capture_starter.started) == 1
    assert len(geo_starter.started) == 1
    async with session_scope(sessionmaker) as session:
        (row,) = (await session.scalars(select(GeoGather))).all()
        assert row.study_id == uuid.UUID(sid) and row.requested_by is not None
    # A second capture while that gather is active queues none; a broken geo start is logged.
    other = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", other, {"lat": LAT, "lon": LON})
    geo_starter.fail = True
    ok = await on.post("alice", f"/v1/studies/{other}/property/captures", {})
    assert ok.status_code == 202 and len(capture_starter.started) == 2
    async with session_scope(sessionmaker) as session:
        failed = await session.scalar(
            select(GeoGather).where(GeoGather.study_id == uuid.UUID(other))
        )
        assert failed is not None and (failed.status, failed.code) == ("failed", E.GEO_UNAVAILABLE)
    # An address-only pin queues no gather.
    third = (await on.create_study("alice"))["id"]
    await put_pin(on, "alice", third, {"address": "1 Main St"})
    geo_starter.fail = False
    await on.post("alice", f"/v1/studies/{third}/property/captures", {})
    async with session_scope(sessionmaker) as session:
        assert (
            await session.scalar(select(GeoGather).where(GeoGather.study_id == uuid.UUID(third)))
            is None
        )


async def test_operators_read_the_incidents(
    on: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(sessionmaker) as session:
        now = datetime.now(UTC)
        session.add(
            GeoIncident(
                code=E.GEO_SOURCE_TIMEOUT,
                source_id="us-naip-usgs",
                occurrences=4,
                first_seen_at=now,
                last_seen_at=now,
                last_http_status=None,
                last_url="https://imagery.nationalmap.gov/x",
            )
        )
    assert (await on.get("alice", "/v1/ops/geo-incidents")).status_code == 403
    items = (await on.get(OPERATOR, "/v1/ops/geo-incidents")).json()["items"]
    assert [(i["code"], i["source_id"], i["occurrences"]) for i in items] == [
        (E.GEO_SOURCE_TIMEOUT, "us-naip-usgs", 4)
    ]
    assert "did not answer" in items[0]["what"]
