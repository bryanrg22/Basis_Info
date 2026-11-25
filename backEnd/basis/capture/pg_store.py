"""The Postgres :class:`~basis.capture.store.CaptureStore` (migration ``0036``): the same rules
as :class:`~basis.capture.store.MemoryCaptureStore`, each method one transaction.

The shared ``capture_state`` row is read ``FOR UPDATE`` wherever a decision depends on it (a
trip, a success), so two workers that see a signal at once serialize: the first opens the
breaker and raises the alert, the second finds it open and records only its incident and
pause. The alert goes through :meth:`AlertRepository.raise_once`, whose unique key is the
second guard. The lease is one conditional ``UPDATE``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.capture import governor as gov
from basis.capture.governor import GovernorConfig, GovernorState, NavigationKind, Trip, Usage
from basis.capture.store import (
    NAVIGATION_KEEP,
    CaptureRow,
    Incident,
    NewView,
    Note,
    PinRow,
    Snapshot,
    alert_payload,
    blocked_alert_key,
    event_detail,
)
from basis.db.models import (
    CaptureIncident,
    CaptureNavigation,
    CaptureState,
    CaptureView,
    PropertyCapture,
    PropertyPin,
    Study,
)
from basis.db.repositories.alert import CAPTURE_BLOCKED, AlertRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.errors.codes import E

STATE_ID = 1


def _row(capture: PropertyCapture) -> CaptureRow:
    return CaptureRow(
        id=capture.id,
        study_id=capture.study_id,
        status=capture.status,
        pin_version=capture.pin_version,
        code=capture.code,
        reason=capture.reason,
        resume_at=capture.resume_at,
        started_at=capture.started_at,
        finished_at=capture.finished_at,
        views=capture.views,
        notes=tuple(capture.notes or ()),
    )


def _state(row: CaptureState) -> GovernorState:
    return GovernorState(
        breaker_open_until=row.breaker_open_until,
        breaker_opened_at=row.breaker_opened_at,
        breaker_code=row.breaker_code,
        breaker_reason=row.breaker_reason,
        trips=row.trips,
        last_navigation_at=row.last_navigation_at,
        last_success_at=row.last_success_at,
    )


def _write_state(row: CaptureState, state: GovernorState) -> None:
    row.breaker_open_until = state.breaker_open_until
    row.breaker_opened_at = state.breaker_opened_at
    row.breaker_code = state.breaker_code
    row.breaker_reason = state.breaker_reason
    row.trips = state.trips
    row.last_success_at = state.last_success_at


async def _locked_state(session: AsyncSession) -> CaptureState:
    """The shared row, locked. The migration seeds it; when it is missing (a truncated
    database), it is created with ``ON CONFLICT DO NOTHING`` before the lock, so two workers
    arriving at once both end up locking the one row instead of both inserting it."""
    row = await session.get(CaptureState, STATE_ID, with_for_update=True)
    if row is None:
        await session.execute(
            pg_insert(CaptureState).values(id=STATE_ID, trips=0).on_conflict_do_nothing()
        )
        row = await session.get(
            CaptureState, STATE_ID, with_for_update=True, populate_existing=True
        )
        assert row is not None
    return row


async def upsert_incident(session: AsyncSession, incident: Incident) -> None:
    stmt = pg_insert(CaptureIncident).values(
        code=incident.code,
        occurrences=1,
        first_seen_at=incident.at,
        last_seen_at=incident.at,
        last_study_id=incident.study_id,
        last_capture_id=incident.capture_id,
        last_url=incident.url,
        last_http_status=incident.http_status,
        last_detail=incident.detail,
        last_evidence=incident.evidence,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[CaptureIncident.code],
            set_={
                "occurrences": CaptureIncident.occurrences + 1,
                "first_seen_at": func.least(
                    CaptureIncident.first_seen_at, stmt.excluded.first_seen_at
                ),
                "last_seen_at": func.greatest(
                    CaptureIncident.last_seen_at, stmt.excluded.last_seen_at
                ),
                "last_study_id": stmt.excluded.last_study_id,
                "last_capture_id": stmt.excluded.last_capture_id,
                "last_url": stmt.excluded.last_url,
                "last_http_status": stmt.excluded.last_http_status,
                "last_detail": stmt.excluded.last_detail,
                "last_evidence": stmt.excluded.last_evidence,
            },
        )
    )


class PgCaptureStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def capture(self, capture_id: uuid.UUID) -> CaptureRow | None:
        async with self._sm() as session:
            row = await session.get(PropertyCapture, capture_id)
            return None if row is None else _row(row)

    async def pin(self, study_id: uuid.UUID) -> PinRow | None:
        async with self._sm() as session:
            row = await session.get(PropertyPin, study_id)
            if row is None:
                return None
            return PinRow(
                study_id=row.study_id,
                address=row.address,
                lat=row.lat,
                lon=row.lon,
                source=row.source,
                version=row.version,
                place_label=row.place_label,
                confirmed=row.confirmed,
            )

    async def reference_date(self, study_id: uuid.UUID) -> tuple[date, str] | None:
        """The intake's placed-in-service date, else the effective date the appraisal's
        extraction fields of the current revision give (as the cost index resolves it,
        ``basis.domain.measurements.resolve_cost_index``)."""
        from basis.db.repositories.extraction import ExtractionRepository
        from basis.domain.measurements import measurements_from_fields

        async with self._sm() as session:
            study = await session.get(Study, study_id)
            if study is None:
                return None
            if study.intake is not None and study.intake.placed_in_service_date is not None:
                return study.intake.placed_in_service_date, "placed in service (intake)"
            repo = ExtractionRepository(session)
            rows = []
            for extraction in await repo.list_for_revision(study_id, study.revision):
                rows.extend(await repo.fields(extraction.id))
            if not rows:
                return None
            effective = measurements_from_fields(rows).effective_date
            if effective is None:
                return None
            return effective.value, "appraisal effective date"

    async def acquire_lease(self, holder: str, ttl: timedelta, now: datetime) -> bool:
        async with self._sm.begin() as session:
            await _locked_state(session)
            result = await session.execute(
                update(CaptureState)
                .where(CaptureState.id == STATE_ID)
                .where(
                    (CaptureState.lease_holder.is_(None))
                    | (CaptureState.lease_holder == holder)
                    | (CaptureState.lease_expires_at < now)
                )
                .values(lease_holder=holder, lease_expires_at=now + ttl)
                .returning(CaptureState.id)
            )
            return result.first() is not None

    async def renew_lease(self, holder: str, ttl: timedelta, now: datetime) -> bool:
        async with self._sm.begin() as session:
            result = await session.execute(
                update(CaptureState)
                .where(CaptureState.id == STATE_ID, CaptureState.lease_holder == holder)
                .values(lease_expires_at=now + ttl)
                .returning(CaptureState.id)
            )
            return result.first() is not None

    async def release_lease(self, holder: str) -> None:
        async with self._sm.begin() as session:
            await session.execute(
                update(CaptureState)
                .where(CaptureState.id == STATE_ID, CaptureState.lease_holder == holder)
                .values(lease_holder=None, lease_expires_at=None)
            )

    async def governor(self, now: datetime) -> tuple[GovernorState, Usage]:
        async with self._sm() as session:
            state = await session.get(CaptureState, STATE_ID)
            loads = (
                await session.scalars(
                    select(CaptureNavigation.created_at).where(
                        CaptureNavigation.kind == "load",
                        CaptureNavigation.created_at > now - gov.DAY,
                    )
                )
            ).all()
            moves = (
                await session.scalars(
                    select(CaptureNavigation.created_at).where(
                        CaptureNavigation.kind == "move",
                        CaptureNavigation.created_at > now - gov.HOUR,
                    )
                )
            ).all()
        return (GovernorState() if state is None else _state(state)), Usage(
            loads=list(loads), moves=list(moves)
        )

    async def navigated(
        self, kind: NavigationKind, capture_id: uuid.UUID | None, now: datetime
    ) -> None:
        async with self._sm.begin() as session:
            session.add(CaptureNavigation(kind=kind, capture_id=capture_id, created_at=now))
            # An upsert, not an update: if the shared row were missing, an update would drop the
            # time silently and the spacing between navigations would not hold.
            await session.execute(
                pg_insert(CaptureState)
                .values(id=STATE_ID, trips=0, last_navigation_at=now)
                .on_conflict_do_update(
                    index_elements=[CaptureState.id], set_={"last_navigation_at": now}
                )
            )
            await session.execute(
                delete(CaptureNavigation).where(
                    CaptureNavigation.created_at < now - NAVIGATION_KEEP
                )
            )

    async def start(self, capture_id: uuid.UUID, now: datetime) -> None:
        async with self._sm.begin() as session:
            row = await session.get(PropertyCapture, capture_id, with_for_update=True)
            assert row is not None
            first = row.started_at is None
            row.status, row.code, row.reason, row.resume_at = "running", None, None, None
            row.started_at = row.started_at or now
            await StudyEventRepository(session).record(
                row.study_id,
                "capture.started",
                actor="system",
                entity_type="property_capture",
                entity_id=row.id,
                detail={"capture_id": str(row.id), "first": first},
            )

    async def place_pin(
        self, capture_id: uuid.UUID, lat: float, lon: float, label: str | None, now: datetime
    ) -> int:
        async with self._sm.begin() as session:
            capture = await session.get(PropertyCapture, capture_id, with_for_update=True)
            assert capture is not None
            pin = await session.get(PropertyPin, capture.study_id, with_for_update=True)
            assert pin is not None
            if pin.lat is not None:
                return pin.version
            before = {"lat": pin.lat, "lon": pin.lon, "version": pin.version}
            pin.lat, pin.lon, pin.source, pin.place_label = lat, lon, "search", label
            pin.confirmed, pin.confirmed_by, pin.confirmed_at = False, None, None
            pin.version += 1
            capture.pin_version = pin.version
            await StudyEventRepository(session).record(
                capture.study_id,
                "property.pin_placed",
                actor="system",
                entity_type="property_pin",
                before=before,
                after={"lat": lat, "lon": lon, "version": pin.version},
                detail={"capture_id": str(capture_id), "label": label},
            )
            return pin.version

    async def labels(self, capture_id: uuid.UUID) -> set[str]:
        async with self._sm() as session:
            rows = await session.scalars(
                select(CaptureView.label).where(CaptureView.capture_id == capture_id)
            )
            return set(rows.all())

    async def add_view(self, view: NewView) -> None:
        async with self._sm.begin() as session:
            session.add(_view(view))
            await session.execute(
                update(PropertyCapture)
                .where(PropertyCapture.id == view.capture_id)
                .values(views=PropertyCapture.views + 1)
            )

    async def wait_turn(self, capture_id: uuid.UUID, reason: str, resume_at: datetime) -> None:
        async with self._sm.begin() as session:
            await session.execute(
                update(PropertyCapture)
                .where(PropertyCapture.id == capture_id)
                .values(status="waiting", code=None, reason=reason, resume_at=resume_at)
            )

    async def pause(
        self,
        capture_id: uuid.UUID,
        *,
        reason: str,
        resume_at: datetime,
        incident: Incident,
        config: GovernorConfig,
        block: bool,
    ) -> Trip | None:
        async with self._sm.begin() as session:
            tripped: Trip | None = None
            if block:
                state_row = await _locked_state(session)
                tripped = gov.trip(
                    _state(state_row), incident.code, incident.detail or "", config, incident.at
                )
                _write_state(state_row, tripped.state)
                assert tripped.state.breaker_open_until is not None
                resume_at = max(resume_at, tripped.state.breaker_open_until)
                if tripped.new:
                    assert tripped.state.breaker_opened_at is not None
                    await AlertRepository(session).raise_once(
                        key=blocked_alert_key(tripped.number, tripped.state.breaker_opened_at),
                        kind=CAPTURE_BLOCKED,
                        study_id=incident.study_id,
                        service="capture",
                        payload=alert_payload(tripped, incident),
                    )
            row = await session.get(PropertyCapture, capture_id, with_for_update=True)
            assert row is not None
            row.status, row.code, row.reason, row.resume_at = (
                "waiting",
                incident.code,
                reason,
                resume_at,
            )
            await upsert_incident(session, incident)
            await StudyEventRepository(session).record(
                row.study_id,
                "capture.paused",
                actor="system",
                entity_type="property_capture",
                entity_id=row.id,
                code=incident.code,
                detail=event_detail(incident, reason, resume_at),
            )
            return tripped

    async def fail(self, capture_id: uuid.UUID, *, reason: str, incident: Incident) -> None:
        async with self._sm.begin() as session:
            row = await session.get(PropertyCapture, capture_id, with_for_update=True)
            assert row is not None
            row.status, row.code, row.reason, row.resume_at = "failed", incident.code, reason, None
            row.finished_at = incident.at
            await upsert_incident(session, incident)
            await StudyEventRepository(session).record(
                row.study_id,
                "capture.failed",
                actor="system",
                entity_type="property_capture",
                entity_id=row.id,
                code=incident.code,
                detail=event_detail(incident, reason, None),
            )

    async def part_failed(self, capture_id: uuid.UUID, note: Note, incident: Incident) -> None:
        async with self._sm.begin() as session:
            row = await session.get(PropertyCapture, capture_id, with_for_update=True)
            assert row is not None
            notes = list(row.notes or [])
            if all(n.get("code") != note.code for n in notes):
                row.notes = [*notes, note.as_json()]
            await upsert_incident(session, incident)
            await StudyEventRepository(session).record(
                row.study_id,
                "capture.part_failed",
                actor="system",
                entity_type="property_capture",
                entity_id=row.id,
                code=incident.code,
                detail={**event_detail(incident, note.message, None), "part": note.part},
            )

    async def complete(self, capture_id: uuid.UUID, *, views: int, now: datetime) -> None:
        async with self._sm.begin() as session:
            state_row = await _locked_state(session)
            _write_state(state_row, gov.succeeded(_state(state_row), now))
            row = await session.get(PropertyCapture, capture_id, with_for_update=True)
            assert row is not None
            row.status, row.code, row.reason, row.resume_at = "succeeded", None, None, None
            row.finished_at = now
            await StudyEventRepository(session).record(
                row.study_id,
                "capture.completed",
                actor="system",
                entity_type="property_capture",
                entity_id=row.id,
                code=E.CAPTURE_COMPLETED,
                detail={"capture_id": str(row.id), "views": views},
            )

    async def worker_seen(self, now: datetime) -> None:
        async with self._sm.begin() as session:
            await _locked_state(session)
            await session.execute(
                update(CaptureState).where(CaptureState.id == STATE_ID).values(worker_seen_at=now)
            )

    async def snapshot(self, now: datetime) -> Snapshot:
        state, usage = await self.governor(now)
        async with self._sm() as session:
            row = await session.get(CaptureState, STATE_ID)
        return Snapshot(
            state=state,
            usage=usage,
            worker_seen_at=None if row is None else row.worker_seen_at,
            lease_holder=None if row is None else row.lease_holder,
            lease_expires_at=None if row is None else row.lease_expires_at,
        )


def _view(view: NewView) -> CaptureView:
    fields: dict[str, Any] = {name: getattr(view, name) for name in view.__dataclass_fields__}
    return CaptureView(**fields)


__all__ = ["PgCaptureStore", "upsert_incident"]
