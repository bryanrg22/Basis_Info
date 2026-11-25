"""The gatherer on Postgres (basis.geo.pg_store, migration 0038, EVAL_REPORT 101): a whole
gather with mocked servers writes its views, parcels, outcomes, incidents and timeline events;
the incident rows group by code and source; a retry stores nothing twice; ``fail`` ends a
running gather once; one active gather per study; views and parcels are never updated."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from dbtest_support import Factory
from geotest_support import Router, jpeg, source
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import GeoGather, GeoIncident, GeoParcel, GeoView, PropertyPin, StudyEvent
from basis.db.repositories.public_geo import GeoActiveError, PublicGeoRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.geo.gather import GatherConfig, Gatherer
from basis.geo.pg_store import PgGeoStore
from basis.geo.store import Incident
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

LAT, LON = 41.60, -93.60


async def seeded(sm: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope(sm) as session:
        study = await Factory(session).study()
        pin = PropertyPin(study_id=study.id, lat=LAT, lon=LON, source="engineer", version=1)
        session.add(pin)
        await session.flush()
        row = await PublicGeoRepository(session).create_gather(study.id, user_id=None, pin=pin)
        return study.id, row.id


def servers() -> Router:
    router = Router()
    router.on("good.test", jpeg(64, 64))
    router.on("broken.test", httpx.Response(503))
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [LON, LAT],
                    [LON + 0.0005, LAT],
                    [LON + 0.0005, LAT + 0.0004],
                    [LON, LAT + 0.0004],
                    [LON, LAT],
                ]
            ],
        },
        "properties": {"PID": "7", "OWNER": None, "ACRES": 0.5},
    }
    router.on("parcels.test", {"features": [feature]})
    return router


SOURCES = [
    source(id="good", url="https://good.test/ImageServer", year=2020),
    source(id="broken", url="https://broken.test/ImageServer", year=2018),
    source(
        id="parcels",
        kind="parcels",
        protocol="arcgis-feature",
        url="https://parcels.test/FeatureServer/0",
        fields={"id": "PID", "owner": "OWNER", "acres": ["ACRES"]},
    ),
]


async def test_a_whole_gather_on_postgres(
    committed: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    sid, gid = await seeded(committed)
    store = PgGeoStore(committed)
    gatherer = Gatherer(
        store,
        LocalStorage(tmp_path),
        "test-bucket",
        servers().client(retries=0),
        config=GatherConfig(extent_m=9.6, max_px=64, lidar=False),
        sources=SOURCES,
    )
    assert await gatherer.run(gid) == "succeeded"
    assert await gatherer.run(gid) == "succeeded"  # ended: left alone
    async with session_scope(committed) as session:
        gather = await session.get(GeoGather, gid)
        assert (
            gather is not None and gather.status == "succeeded" and gather.code == E.GEO_COMPLETED
        )
        assert gather.started_at is not None and gather.finished_at is not None
        statuses = {(o["source"], o["status"]) for o in gather.sources}
        assert ("broken", "failed") in statuses and ("good", "ok") in statuses
        views = (await session.scalars(select(GeoView).where(GeoView.gather_id == gid))).all()
        assert [v.label for v in views] == ["current-good-2020"]
        assert views[0].bbox and views[0].crs == "EPSG:3857" and len(views[0].sha256) == 64
        (parcel,) = (await session.scalars(select(GeoParcel))).all()
        assert parcel.parcel_id == "7" and parcel.under_pin and parcel.layer_acres == 0.5
        (incident,) = (await session.scalars(select(GeoIncident))).all()
        assert (incident.code, incident.source_id, incident.last_http_status) == (
            E.GEO_SOURCE_HTTP,
            "broken",
            503,
        )
        kinds = (
            await session.scalars(select(StudyEvent.event_type).where(StudyEvent.study_id == sid))
        ).all()
        assert {"geo.requested", "geo.started", "geo.source_failed", "geo.completed"} <= set(kinds)
        # Views and parcels are never updated.
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text("UPDATE geo_views SET label = 'x'"))
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text("UPDATE geo_parcels SET owner = 'x'"))


async def test_incidents_group_by_code_and_source_and_fail_ends_once(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    sid, gid = await seeded(committed)
    store = PgGeoStore(committed)
    t0 = datetime(2026, 11, 23, 12, tzinfo=UTC)
    row = await store.start(gid, now=t0)
    assert row is not None
    for i, src in enumerate(("a", "a", "b")):
        await store.source_failed(
            row,
            {"source": src},
            Incident(E.GEO_SOURCE_TIMEOUT, src, t0 + timedelta(minutes=i), sid, gid),
        )
    async with session_scope(committed) as session:
        rows = {
            (r.source_id, r.occurrences) for r in (await session.scalars(select(GeoIncident))).all()
        }
        assert rows == {("a", 2), ("b", 1)}
    incident = Incident(E.GEO_GATHER_FAILED, "gather", t0, sid, gid, detail="x")
    await store.fail(gid, code=E.GEO_GATHER_FAILED, reason="r", incident=incident, now=t0)
    await store.fail(gid, code=E.GEO_GATHER_FAILED, reason="r", incident=incident, now=t0)
    assert await store.start(gid, now=t0) is None
    async with session_scope(committed) as session:
        gather = await session.get(GeoGather, gid)
        assert gather is not None and (gather.status, gather.code) == (
            "failed",
            E.GEO_GATHER_FAILED,
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(StudyEvent)
                .where(StudyEvent.event_type == "geo.failed")
            )
            == 1
        )
    assert await store.appraisal_site_sqft(sid) is None


async def test_one_active_gather_per_study(committed: async_sessionmaker[AsyncSession]) -> None:
    sid, gid = await seeded(committed)
    async with session_scope(committed) as session:
        pin = await session.get(PropertyPin, sid)
        assert pin is not None
        with pytest.raises(GeoActiveError) as active:
            await PublicGeoRepository(session).create_gather(sid, user_id=None, pin=pin)
        assert active.value.gather_id == gid
    async with session_scope(committed) as session:
        with pytest.raises(IntegrityError, match="point"):
            async with session.begin_nested():
                session.add(GeoGather(study_id=sid, status="failed", pin_version=1, lat=95, lon=0))
                await session.flush()
