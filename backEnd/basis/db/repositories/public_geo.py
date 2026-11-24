"""The public geographic sources as the API reads and writes them (migration ``0038``,
docs/api/PUBLIC_GEO.md). The geo worker writes through :class:`basis.geo.pg_store.PgGeoStore`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.errors import ConflictError, constraint_name
from basis.db.models import (
    GEO_ACTIVE_STATUSES,
    Extraction,
    ExtractionField,
    GeoGather,
    GeoIncident,
    GeoParcel,
    GeoView,
    PropertyPin,
)
from basis.db.repositories.study_event import StudyEventRepository
from basis.geo.contracts import geo_workflow_id

_ONE_ACTIVE: Final = "uq_geo_gathers_one_active"
UNSTARTED_REASON: Final = "The public records could not start; ask for them again in a minute."


class GeoActiveError(ConflictError):
    """A gather of the study is already queued or running."""

    def __init__(self, gather_id: uuid.UUID | None) -> None:
        super().__init__("a gather of this study is already in progress")
        self.gather_id = gather_id


class PublicGeoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pin(self, study_id: uuid.UUID) -> PropertyPin | None:
        return await self._session.get(PropertyPin, study_id)

    async def active(self, study_id: uuid.UUID) -> GeoGather | None:
        return await self._session.scalar(
            select(GeoGather).where(
                GeoGather.study_id == study_id, GeoGather.status.in_(GEO_ACTIVE_STATUSES)
            )
        )

    async def recent_successes(self, study_id: uuid.UUID, *, since: datetime) -> list[GeoGather]:
        """The newest gathers that succeeded since ``since``, newest first (at most five). The
        route keeps the first one taken at the pin's current point: confirming the pin or
        editing its address raises its version without moving the point."""
        rows = await self._session.scalars(
            select(GeoGather)
            .where(
                GeoGather.study_id == study_id,
                GeoGather.status == "succeeded",
                GeoGather.finished_at >= since,
            )
            .order_by(GeoGather.finished_at.desc())
            .limit(5)
        )
        return list(rows)

    async def create_gather(
        self, study_id: uuid.UUID, *, user_id: uuid.UUID | None, pin: PropertyPin
    ) -> GeoGather:
        if pin.lat is None or pin.lon is None:
            raise ValueError("a gather needs a point")
        gather_id = uuid.uuid4()
        try:
            async with self._session.begin_nested():
                row = GeoGather(
                    id=gather_id,
                    study_id=study_id,
                    requested_by=user_id,
                    status="queued",
                    pin_version=pin.version,
                    lat=pin.lat,
                    lon=pin.lon,
                    workflow_id=geo_workflow_id(gather_id),
                )
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            if constraint_name(exc) == _ONE_ACTIVE:
                active = await self.active(study_id)
                raise GeoActiveError(None if active is None else active.id) from exc
            raise
        await StudyEventRepository(self._session).record(
            study_id,
            "geo.requested",
            actor="engineer" if user_id is not None else "system",
            actor_id=user_id,
            entity_type="geo_gather",
            entity_id=row.id,
            detail={"gather_id": str(row.id), "pin_version": pin.version},
        )
        await self._session.refresh(row)
        return row

    async def mark_unstarted(self, gather_id: uuid.UUID, *, code: str) -> None:
        row = await self._session.get(GeoGather, gather_id, with_for_update=True)
        if row is None or row.status != "queued":
            return
        row.status, row.code, row.reason = "failed", code, UNSTARTED_REASON
        row.finished_at = datetime.now(UTC)
        await StudyEventRepository(self._session).record(
            row.study_id,
            "geo.failed",
            actor="system",
            entity_type="geo_gather",
            entity_id=row.id,
            code=code,
            detail={"gather_id": str(row.id), "reason": UNSTARTED_REASON},
        )

    async def newest(self, study_id: uuid.UUID) -> GeoGather | None:
        return await self._session.scalar(
            select(GeoGather)
            .where(GeoGather.study_id == study_id)
            .order_by(GeoGather.created_at.desc(), GeoGather.id)
            .limit(1)
        )

    async def gather(self, study_id: uuid.UUID, gather_id: uuid.UUID) -> GeoGather | None:
        row = await self._session.get(GeoGather, gather_id)
        return row if row is not None and row.study_id == study_id else None

    async def views(self, gather_id: uuid.UUID) -> list[GeoView]:
        rows = await self._session.scalars(
            select(GeoView)
            .where(GeoView.gather_id == gather_id)
            .order_by(GeoView.kind, GeoView.year.desc().nulls_last(), GeoView.label)
        )
        return list(rows.all())

    async def view(self, study_id: uuid.UUID, view_id: uuid.UUID) -> GeoView | None:
        row = await self._session.get(GeoView, view_id)
        return row if row is not None and row.study_id == study_id else None

    async def parcels(self, gather_id: uuid.UUID) -> list[GeoParcel]:
        rows = await self._session.scalars(
            select(GeoParcel)
            .where(GeoParcel.gather_id == gather_id)
            .order_by(GeoParcel.under_pin.desc(), GeoParcel.parcel_id)
        )
        return list(rows.all())

    async def appraisal_site_sqft(self, study_id: uuid.UUID) -> float | None:
        value = await self._session.scalar(
            select(ExtractionField.value_num)
            .join(Extraction, Extraction.id == ExtractionField.extraction_id)
            .where(
                ExtractionField.study_id == study_id,
                ExtractionField.section == "site",
                ExtractionField.field == "area_sqft",
                ExtractionField.value_num.is_not(None),
            )
            .order_by(Extraction.revision.desc(), ExtractionField.created_at.desc())
            .limit(1)
        )
        return None if value is None else float(value)

    async def incidents(self, *, limit: int = 100) -> list[GeoIncident]:
        rows = await self._session.scalars(
            select(GeoIncident).order_by(GeoIncident.last_seen_at.desc()).limit(limit)
        )
        return list(rows.all())
