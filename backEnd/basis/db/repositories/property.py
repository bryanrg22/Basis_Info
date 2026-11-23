"""The property pin and the captures, as the API reads and writes them (migration ``0036``,
docs/api/PROPERTY_CAPTURE.md). The capture worker writes through
:class:`basis.capture.pg_store.PgCaptureStore`.

The pin is written whole with ``If-Match`` on its ``version`` (the intake's pattern):

* a point the engineer sends that differs from the stored one is ``source = engineer``;
* no point clears it (a new address is searched again by the next capture);
* ``confirmed`` needs a point; confirming records who and when; moving the point withdraws an
  earlier confirmation unless the same request confirms it again;
* an unchanged document writes nothing and keeps the version.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.capture.contracts import capture_workflow_id
from basis.db.errors import ConflictError, PreconditionFailedError, constraint_name
from basis.db.models import (
    CAPTURE_ACTIVE_STATUSES,
    CaptureIncident,
    CaptureView,
    PropertyCapture,
    PropertyPin,
)
from basis.db.repositories.study_event import StudyEventRepository

MAX_CAPTURE_PAGE: Final = 50
_ONE_ACTIVE: Final = "uq_property_captures_one_active"


class CaptureActiveError(ConflictError):
    """A capture of the study is already queued, waiting or running."""

    def __init__(self, capture_id: uuid.UUID | None) -> None:
        super().__init__("a capture of this study is already in progress")
        self.capture_id = capture_id


@dataclass(frozen=True, slots=True)
class PinDocument:
    address: str | None
    lat: float | None
    lon: float | None
    confirmed: bool


@dataclass(frozen=True, slots=True)
class PinWrite:
    row: PropertyPin
    version_before: int
    changed: bool


def _pin_json(pin: PropertyPin | None) -> dict[str, Any] | None:
    if pin is None:
        return None
    return {
        "address": pin.address,
        "lat": pin.lat,
        "lon": pin.lon,
        "source": pin.source,
        "confirmed": pin.confirmed,
        "version": pin.version,
    }


class PropertyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ the pin
    async def pin(self, study_id: uuid.UUID) -> PropertyPin | None:
        return await self._session.get(PropertyPin, study_id)

    async def write_pin(
        self,
        study_id: uuid.UUID,
        document: PinDocument,
        *,
        user_id: uuid.UUID,
        precondition: Callable[[int, bool], bool] | None,
        now: datetime | None = None,
    ) -> PinWrite:
        if document.confirmed and document.lat is None:
            raise ValueError("a pin can be confirmed only with a point")
        pin = await self._session.get(PropertyPin, study_id, with_for_update=True)
        version = 0 if pin is None else pin.version
        if precondition is not None and not precondition(version, pin is not None):
            raise PreconditionFailedError("the pin changed", version=version)
        before = _pin_json(pin)
        when = now or datetime.now(UTC)
        point = None if document.lat is None else (document.lat, document.lon)
        if pin is None:
            pin = PropertyPin(
                study_id=study_id,
                address=document.address,
                lat=document.lat,
                lon=document.lon,
                source="engineer",
                confirmed=document.confirmed,
                confirmed_by=user_id if document.confirmed else None,
                confirmed_at=when if document.confirmed else None,
                updated_by=user_id,
                version=1,
            )
            self._session.add(pin)
        else:
            stored = None if pin.lat is None else (pin.lat, pin.lon)
            moved = point != stored
            same = (
                not moved
                and pin.address == document.address
                and pin.confirmed == document.confirmed
            )
            if same:
                return PinWrite(row=pin, version_before=version, changed=False)
            if moved:
                pin.lat, pin.lon = document.lat, document.lon
                pin.source = "engineer"
                if point is None:
                    pin.place_label = None
            if pin.address != document.address:
                pin.address = document.address
                if not moved and point is not None and pin.source == "search":
                    # The found point belonged to the old address: search the new one.
                    pin.lat = pin.lon = pin.place_label = None
            if document.confirmed and (moved or not pin.confirmed):
                pin.confirmed, pin.confirmed_by, pin.confirmed_at = True, user_id, when
            elif not document.confirmed:
                pin.confirmed, pin.confirmed_by, pin.confirmed_at = False, None, None
            if pin.lat is None:
                pin.confirmed, pin.confirmed_by, pin.confirmed_at = False, None, None
            pin.updated_by = user_id
            pin.version += 1
        await self._session.flush()
        await StudyEventRepository(self._session).record(
            study_id,
            "property.pin_set",
            actor="engineer",
            actor_id=user_id,
            entity_type="property_pin",
            before=before,
            after=_pin_json(pin),
            detail={"version_before": version, "version_after": pin.version},
        )
        await self._session.refresh(pin)
        return PinWrite(row=pin, version_before=version, changed=True)

    # ------------------------------------------------------------------ captures
    async def active_capture(self, study_id: uuid.UUID) -> PropertyCapture | None:
        return await self._session.scalar(
            select(PropertyCapture).where(
                PropertyCapture.study_id == study_id,
                PropertyCapture.status.in_(CAPTURE_ACTIVE_STATUSES),
            )
        )

    async def recent_success(
        self, study_id: uuid.UUID, *, since: datetime, pin_version: int
    ) -> PropertyCapture | None:
        return await self._session.scalar(
            select(PropertyCapture)
            .where(
                PropertyCapture.study_id == study_id,
                PropertyCapture.status == "succeeded",
                PropertyCapture.pin_version == pin_version,
                PropertyCapture.finished_at >= since,
            )
            .order_by(PropertyCapture.finished_at.desc())
            .limit(1)
        )

    async def create_capture(
        self, study_id: uuid.UUID, *, user_id: uuid.UUID, pin_version: int
    ) -> PropertyCapture:
        capture_id = uuid.uuid4()
        try:
            async with self._session.begin_nested():
                row = PropertyCapture(
                    id=capture_id,
                    study_id=study_id,
                    requested_by=user_id,
                    status="queued",
                    pin_version=pin_version,
                    workflow_id=capture_workflow_id(capture_id),
                )
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            if constraint_name(exc) == _ONE_ACTIVE:
                active = await self.active_capture(study_id)
                raise CaptureActiveError(None if active is None else active.id) from exc
            raise
        await StudyEventRepository(self._session).record(
            study_id,
            "capture.requested",
            actor="engineer",
            actor_id=user_id,
            entity_type="property_capture",
            entity_id=row.id,
            detail={"capture_id": str(row.id), "pin_version": pin_version},
        )
        await self._session.refresh(row)
        return row

    async def mark_unstarted(
        self, capture_id: uuid.UUID, *, code: str, reason: str, now: datetime | None = None
    ) -> None:
        row = await self._session.get(PropertyCapture, capture_id, with_for_update=True)
        if row is None or row.status != "queued":
            return
        row.status, row.code, row.reason = "failed", code, reason
        row.finished_at = now or datetime.now(UTC)
        await StudyEventRepository(self._session).record(
            row.study_id,
            "capture.failed",
            actor="system",
            entity_type="property_capture",
            entity_id=row.id,
            code=code,
            detail={"capture_id": str(row.id), "reason": reason},
        )

    async def captures(self, study_id: uuid.UUID, *, limit: int = 20) -> list[PropertyCapture]:
        if not 1 <= limit <= MAX_CAPTURE_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_CAPTURE_PAGE}")
        rows = await self._session.scalars(
            select(PropertyCapture)
            .where(PropertyCapture.study_id == study_id)
            .order_by(PropertyCapture.created_at.desc(), PropertyCapture.id)
            .limit(limit)
        )
        return list(rows.all())

    async def capture(self, study_id: uuid.UUID, capture_id: uuid.UUID) -> PropertyCapture | None:
        row = await self._session.get(PropertyCapture, capture_id)
        return row if row is not None and row.study_id == study_id else None

    async def views(self, capture_id: uuid.UUID) -> list[CaptureView]:
        rows = await self._session.scalars(
            select(CaptureView)
            .where(CaptureView.capture_id == capture_id)
            .order_by(CaptureView.captured_at, CaptureView.label)
        )
        return list(rows.all())

    async def view(self, study_id: uuid.UUID, view_id: uuid.UUID) -> CaptureView | None:
        row = await self._session.get(CaptureView, view_id)
        return row if row is not None and row.study_id == study_id else None

    # ------------------------------------------------------------------ operators
    async def incidents(self, *, limit: int = 100) -> list[CaptureIncident]:
        rows = await self._session.scalars(
            select(CaptureIncident).order_by(CaptureIncident.last_seen_at.desc()).limit(limit)
        )
        return list(rows.all())
