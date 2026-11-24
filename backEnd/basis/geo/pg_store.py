"""The gatherer's store in Postgres (migration ``0038``). Every write is its own transaction, so a
view or a parcel stored before a crash stays stored and a retried activity skips it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import (
    GeoGather,
    GeoIncident,
    GeoParcel,
    GeoView,
)
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.geo.parcels import Parcel
from basis.geo.store import GatherRow, Incident, ViewRecord


def _row(g: GeoGather) -> GatherRow:
    return GatherRow(
        id=g.id,
        study_id=g.study_id,
        status=g.status,
        pin_version=g.pin_version,
        lat=g.lat,
        lon=g.lon,
        code=g.code,
        reason=g.reason,
    )


async def upsert_incident(session: AsyncSession, incident: Incident) -> None:
    stmt = pg_insert(GeoIncident).values(
        code=incident.code,
        source_id=incident.source_id,
        occurrences=1,
        first_seen_at=incident.at,
        last_seen_at=incident.at,
        last_study_id=incident.study_id,
        last_gather_id=incident.gather_id,
        last_url=incident.url,
        last_http_status=incident.http_status,
        last_detail=incident.detail,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[GeoIncident.code, GeoIncident.source_id],
            set_={
                "occurrences": GeoIncident.occurrences + 1,
                "first_seen_at": func.least(GeoIncident.first_seen_at, stmt.excluded.first_seen_at),
                "last_seen_at": func.greatest(GeoIncident.last_seen_at, stmt.excluded.last_seen_at),
                "last_study_id": stmt.excluded.last_study_id,
                "last_gather_id": stmt.excluded.last_gather_id,
                "last_url": stmt.excluded.last_url,
                "last_http_status": stmt.excluded.last_http_status,
                "last_detail": stmt.excluded.last_detail,
            },
        )
    )


class PgGeoStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessionmaker

    async def gather(self, gather_id: uuid.UUID) -> GatherRow | None:
        async with session_scope(self._sessions) as session:
            g = await session.get(GeoGather, gather_id)
            return None if g is None else _row(g)

    async def start(self, gather_id: uuid.UUID, *, now: datetime) -> GatherRow | None:
        async with session_scope(self._sessions) as session:
            g = await session.get(GeoGather, gather_id, with_for_update=True)
            if g is None or g.status not in ("queued", "running"):
                return None
            first = g.status == "queued"
            g.status = "running"
            if g.started_at is None:
                g.started_at = now
            if first:
                await StudyEventRepository(session).record(
                    g.study_id,
                    "geo.started",
                    actor="system",
                    entity_type="geo_gather",
                    entity_id=g.id,
                    detail={"gather_id": str(g.id), "pin_version": g.pin_version},
                )
            return _row(g)

    async def labels(self, gather_id: uuid.UUID) -> set[str]:
        async with session_scope(self._sessions) as session:
            rows = await session.scalars(
                select(GeoView.label).where(GeoView.gather_id == gather_id)
            )
            return set(rows.all())

    async def add_view(self, view: ViewRecord) -> None:
        async with session_scope(self._sessions) as session:
            session.add(
                GeoView(
                    study_id=view.study_id,
                    gather_id=view.gather_id,
                    source_id=view.source_id,
                    kind=view.kind,
                    label=view.label,
                    year=view.year,
                    year_label=view.year_label,
                    leaf_off=view.leaf_off,
                    lat=view.lat,
                    lon=view.lon,
                    pixel_size_m=view.pixel_size_m,
                    native_pixel_m=view.native_pixel_m,
                    bbox=list(view.bbox),
                    width=view.width,
                    height=view.height,
                    uri=view.uri,
                    content_type=view.content_type,
                    sha256=view.sha256,
                    bytes=view.bytes,
                    credit=view.credit,
                    terms_class=view.terms_class,
                    terms_url=view.terms_url,
                    captured_at=view.captured_at,
                )
            )

    async def has_parcels(self, gather_id: uuid.UUID) -> bool:
        async with session_scope(self._sessions) as session:
            found = await session.scalar(
                select(GeoParcel.id).where(GeoParcel.gather_id == gather_id).limit(1)
            )
            return found is not None

    async def add_parcels(
        self, row: GatherRow, parcels: list[Parcel], *, credit: str, terms_class: str
    ) -> None:
        async with session_scope(self._sessions) as session:
            for p in parcels:
                session.add(
                    GeoParcel(
                        study_id=row.study_id,
                        gather_id=row.id,
                        source_id=p.source_id,
                        parcel_id=p.parcel_id,
                        under_pin=p.under_pin,
                        owner=p.owner,
                        address=p.address,
                        area_m2=p.area_m2,
                        layer_acres=p.layer_acres,
                        land_value=p.land_value,
                        improvement_value=p.improvement_value,
                        total_value=p.total_value,
                        year_built=p.year_built,
                        building_area_sqft=p.building_area_sqft,
                        stories=p.stories,
                        geometry=p.geometry,
                        attributes=p.attributes,
                        credit=credit,
                        terms_class=terms_class,
                    )
                )

    async def source_failed(
        self, row: GatherRow, outcome: dict[str, Any], incident: Incident
    ) -> None:
        async with session_scope(self._sessions) as session:
            await upsert_incident(session, incident)
            await StudyEventRepository(session).record(
                row.study_id,
                "geo.source_failed",
                actor="system",
                entity_type="geo_gather",
                entity_id=row.id,
                code=incident.code,
                detail={"gather_id": str(row.id), **outcome},
            )

    async def appraisal_site_sqft(self, study_id: uuid.UUID) -> float | None:
        from basis.db.repositories.public_geo import PublicGeoRepository

        async with session_scope(self._sessions) as session:
            return await PublicGeoRepository(session).appraisal_site_sqft(study_id)

    async def site_checked(self, row: GatherRow, detail: dict[str, Any], code: str | None) -> None:
        async with session_scope(self._sessions) as session:
            await StudyEventRepository(session).record(
                row.study_id,
                "geo.site_area_checked",
                actor="system",
                entity_type="geo_gather",
                entity_id=row.id,
                code=code,
                detail={"gather_id": str(row.id), **detail},
            )

    async def finish(
        self,
        gather_id: uuid.UUID,
        *,
        sources: list[dict[str, Any]],
        lidar: dict[str, Any] | None,
        code: str,
        now: datetime,
    ) -> None:
        async with session_scope(self._sessions) as session:
            g = await session.get(GeoGather, gather_id, with_for_update=True)
            if g is None or g.status != "running":
                return
            g.status, g.code, g.reason = "succeeded", code, None
            g.sources, g.lidar, g.finished_at = sources, lidar, now
            views = await session.scalar(
                select(func.count()).select_from(GeoView).where(GeoView.gather_id == gather_id)
            )
            await StudyEventRepository(session).record(
                g.study_id,
                "geo.completed",
                actor="system",
                entity_type="geo_gather",
                entity_id=g.id,
                code=code,
                detail={
                    "gather_id": str(g.id),
                    "views": views,
                    "failed": sum(1 for s in sources if s.get("status") == "failed"),
                },
            )

    async def fail(
        self, gather_id: uuid.UUID, *, code: str, reason: str, incident: Incident, now: datetime
    ) -> None:
        async with session_scope(self._sessions) as session:
            g = await session.get(GeoGather, gather_id, with_for_update=True)
            if g is None or g.status in ("succeeded", "failed"):
                return
            g.status, g.code, g.reason, g.finished_at = "failed", code, reason, now
            await upsert_incident(session, incident)
            await StudyEventRepository(session).record(
                g.study_id,
                "geo.failed",
                actor="system",
                entity_type="geo_gather",
                entity_id=g.id,
                code=code,
                detail={"gather_id": str(g.id), "reason": reason},
            )
