"""What the capture runner reads and writes, as a protocol (:class:`CaptureStore`), and an
in-memory version with the same rules for the unit tests (:class:`MemoryCaptureStore`). The
Postgres version is :class:`basis.capture.pg_store.PgCaptureStore`; the integration tests
hold both to the same behaviour.

Each method is one transaction. The ones that record a failure write everything about it at
once: the capture's status and engineer-facing reason, the study's timeline event
(``capture.paused`` / ``capture.failed``, with the code and the evidence), the incident row for
the code (count, first and last seen, the newest occurrence), and, for a block signal, the
breaker's trip with its one alert (``capture_blocked``, keyed by the trip's number and the
time it opened, through ``AlertRepository.raise_once`` in Postgres).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from basis.capture import governor as gov
from basis.capture.governor import GovernorConfig, GovernorState, NavigationKind, Trip, Usage
from basis.errors.codes import E

ACTIVE: Final = ("queued", "waiting", "running")
CAPTURE_BLOCKED: Final = "capture_blocked"
NAVIGATION_KEEP: Final = timedelta(days=2)


def blocked_alert_key(number: int, opened_at: datetime) -> str:
    """One alert per trip: its number and the time the breaker opened."""
    return f"capture-blocked:{number}:{opened_at.isoformat()}"


@dataclass(frozen=True, slots=True)
class PinRow:
    study_id: uuid.UUID
    address: str | None
    lat: float | None
    lon: float | None
    source: str
    version: int
    place_label: str | None = None
    confirmed: bool = False

    @property
    def has_point(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass(frozen=True, slots=True)
class CaptureRow:
    id: uuid.UUID
    study_id: uuid.UUID
    status: str
    pin_version: int
    code: str | None = None
    reason: str | None = None
    resume_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    views: int = 0
    notes: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class NewView:
    study_id: uuid.UUID
    capture_id: uuid.UUID
    pin_version: int
    kind: str
    label: str
    lat: float
    lon: float
    ground_alt_m: float
    range_m: float
    fov_deg: float
    heading_deg: float
    tilt_deg: float
    pixel_size_m: float | None
    imagery_date: str | None
    width: int
    height: int
    uri: str
    sha256: str
    bytes: int
    source: str
    captured_at: datetime
    # EVAL_REPORT 99: the date as a date and how it was read (or why not), leaf-off or not,
    # the green share of a top-down view, and for a street-level view where the camera
    # stood and the panorama's id.
    imagery_on: date | None = None
    imagery_date_note: str | None = None
    leaf: str | None = None
    green_share: float | None = None
    camera_lat: float | None = None
    camera_lon: float | None = None
    pano_id: str | None = None


@dataclass(frozen=True, slots=True)
class Note:
    """A part of a capture that did not work while the capture itself went on (history,
    street level, a date): its code and the engineer's sentence (never the provider)."""

    part: str
    code: str
    message: str

    def as_json(self) -> dict[str, Any]:
        return {"part": self.part, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class Incident:
    """One occurrence of a failure code, for the grouped record and the timeline."""

    code: str
    at: datetime
    study_id: uuid.UUID | None
    capture_id: uuid.UUID | None
    url: str | None = None
    http_status: int | None = None
    detail: str | None = None
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class IncidentRow:
    code: str
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_study_id: uuid.UUID | None
    last_capture_id: uuid.UUID | None
    last_url: str | None
    last_http_status: int | None
    last_detail: str | None
    last_evidence: str | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What ``GET /v1/ops/health`` reports about capture."""

    state: GovernorState
    usage: Usage
    worker_seen_at: datetime | None
    lease_holder: str | None
    lease_expires_at: datetime | None


def event_detail(incident: Incident, reason: str, resume_at: datetime | None) -> dict[str, Any]:
    detail: dict[str, Any] = {"reason": reason, "capture_id": str(incident.capture_id)}
    if resume_at is not None:
        detail["resume_at"] = resume_at.isoformat()
    if incident.detail:
        detail["signal"] = incident.detail
    if incident.http_status is not None:
        detail["http_status"] = incident.http_status
    if incident.evidence:
        detail["evidence"] = incident.evidence
    return detail


def alert_payload(trip: Trip, incident: Incident) -> dict[str, Any]:
    state = trip.state
    assert state.breaker_opened_at is not None and state.breaker_open_until is not None
    return {
        "trip": trip.number,
        "code": state.breaker_code,
        "reason": state.breaker_reason,
        "opened_at": state.breaker_opened_at.isoformat(),
        "open_until": state.breaker_open_until.isoformat(),
        "study_id": None if incident.study_id is None else str(incident.study_id),
        "capture_id": None if incident.capture_id is None else str(incident.capture_id),
        "url": incident.url,
        "http_status": incident.http_status,
        "evidence": incident.evidence,
        "at": incident.at.isoformat(),
    }


class CaptureStore(Protocol):
    async def capture(self, capture_id: uuid.UUID) -> CaptureRow | None: ...

    async def pin(self, study_id: uuid.UUID) -> PinRow | None: ...

    async def reference_date(self, study_id: uuid.UUID) -> tuple[date, str] | None:
        """The date the history is planned around and where it came from: the intake's
        placed-in-service date, else the appraisal's effective date; None when neither."""
        ...

    async def acquire_lease(self, holder: str, ttl: timedelta, now: datetime) -> bool: ...

    async def renew_lease(self, holder: str, ttl: timedelta, now: datetime) -> bool: ...

    async def release_lease(self, holder: str) -> None: ...

    async def governor(self, now: datetime) -> tuple[GovernorState, Usage]: ...

    async def navigated(
        self, kind: NavigationKind, capture_id: uuid.UUID | None, now: datetime
    ) -> None: ...

    async def start(self, capture_id: uuid.UUID, now: datetime) -> None: ...

    async def place_pin(
        self, capture_id: uuid.UUID, lat: float, lon: float, label: str | None, now: datetime
    ) -> int:
        """The search found the place: put the point on a pin that had none (``source =
        search``, unconfirmed) and move the capture to the new pin version; the version."""
        ...

    async def labels(self, capture_id: uuid.UUID) -> set[str]: ...

    async def add_view(self, view: NewView) -> None: ...

    async def wait_turn(self, capture_id: uuid.UUID, reason: str, resume_at: datetime) -> None:
        """Another capture holds the lease: wait without an incident (nothing failed)."""
        ...

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
        """Record a pause; with ``block``, trip the breaker first and pause until it closes.
        Returns the trip for a block signal (``new`` when it raised the alert)."""
        ...

    async def fail(self, capture_id: uuid.UUID, *, reason: str, incident: Incident) -> None: ...

    async def part_failed(self, capture_id: uuid.UUID, note: Note, incident: Incident) -> None:
        """A part did not work and the capture goes on: the note on the capture (once per
        code), the incident, and the ``capture.part_failed`` timeline event."""
        ...

    async def complete(self, capture_id: uuid.UUID, *, views: int, now: datetime) -> None: ...

    async def worker_seen(self, now: datetime) -> None: ...

    async def snapshot(self, now: datetime) -> Snapshot: ...


@dataclass
class MemoryCaptureStore:
    """The same rules in memory, for the unit tests: one lock-free event loop stands in for
    the database's transactions, and ``alerts`` keeps the unique key ``raise_once`` relies
    on."""

    pins: dict[uuid.UUID, PinRow] = field(default_factory=dict)
    captures: dict[uuid.UUID, CaptureRow] = field(default_factory=dict)
    views: list[NewView] = field(default_factory=list)
    state: GovernorState = field(default_factory=GovernorState)
    navigations: list[tuple[NavigationKind, uuid.UUID | None, datetime]] = field(
        default_factory=list
    )
    incidents: dict[str, IncidentRow] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    alerts: dict[str, dict[str, Any]] = field(default_factory=dict)
    lease_holder: str | None = None
    lease_expires_at: datetime | None = None
    worker_seen_at: datetime | None = None
    references: dict[uuid.UUID, tuple[date, str]] = field(default_factory=dict)

    # -------------------------------------------------------------- helpers for the tests
    def add_capture(
        self, study_id: uuid.UUID, pin: PinRow, *, status: str = "queued"
    ) -> CaptureRow:
        self.pins[study_id] = pin
        row = CaptureRow(id=uuid.uuid4(), study_id=study_id, status=status, pin_version=pin.version)
        self.captures[row.id] = row
        return row

    def _event(
        self, study_id: uuid.UUID, event_type: str, code: str | None, detail: dict[str, Any]
    ) -> None:
        self.events.append(
            {"study_id": study_id, "event_type": event_type, "code": code, "detail": detail}
        )

    def _incident(self, incident: Incident) -> None:
        current = self.incidents.get(incident.code)
        self.incidents[incident.code] = IncidentRow(
            code=incident.code,
            occurrences=1 if current is None else current.occurrences + 1,
            first_seen_at=incident.at
            if current is None
            else min(current.first_seen_at, incident.at),
            last_seen_at=incident.at if current is None else max(current.last_seen_at, incident.at),
            last_study_id=incident.study_id,
            last_capture_id=incident.capture_id,
            last_url=incident.url,
            last_http_status=incident.http_status,
            last_detail=incident.detail,
            last_evidence=incident.evidence,
        )

    def _set(self, capture_id: uuid.UUID, **changes: Any) -> CaptureRow:
        row = replace(self.captures[capture_id], **changes)
        self.captures[capture_id] = row
        return row

    # -------------------------------------------------------------- the protocol
    async def capture(self, capture_id: uuid.UUID) -> CaptureRow | None:
        return self.captures.get(capture_id)

    async def pin(self, study_id: uuid.UUID) -> PinRow | None:
        return self.pins.get(study_id)

    async def reference_date(self, study_id: uuid.UUID) -> tuple[date, str] | None:
        return self.references.get(study_id)

    async def acquire_lease(self, holder: str, ttl: timedelta, now: datetime) -> bool:
        free = (
            self.lease_holder is None
            or self.lease_holder == holder
            or (self.lease_expires_at is not None and self.lease_expires_at < now)
        )
        if free:
            self.lease_holder, self.lease_expires_at = holder, now + ttl
        return free

    async def renew_lease(self, holder: str, ttl: timedelta, now: datetime) -> bool:
        if self.lease_holder != holder:
            return False
        self.lease_expires_at = now + ttl
        return True

    async def release_lease(self, holder: str) -> None:
        if self.lease_holder == holder:
            self.lease_holder = self.lease_expires_at = None

    async def governor(self, now: datetime) -> tuple[GovernorState, Usage]:
        loads = [t for k, _, t in self.navigations if k == "load" and t > now - gov.DAY]
        moves = [t for k, _, t in self.navigations if k == "move" and t > now - gov.HOUR]
        return self.state, Usage(loads=loads, moves=moves)

    async def navigated(
        self, kind: NavigationKind, capture_id: uuid.UUID | None, now: datetime
    ) -> None:
        self.navigations.append((kind, capture_id, now))
        self.state = replace(self.state, last_navigation_at=now)

    async def start(self, capture_id: uuid.UUID, now: datetime) -> None:
        row = self.captures[capture_id]
        first = row.started_at is None
        row = self._set(
            capture_id,
            status="running",
            code=None,
            reason=None,
            resume_at=None,
            started_at=row.started_at or now,
        )
        self._event(
            row.study_id, "capture.started", None, {"capture_id": str(capture_id), "first": first}
        )

    async def place_pin(
        self, capture_id: uuid.UUID, lat: float, lon: float, label: str | None, now: datetime
    ) -> int:
        row = self.captures[capture_id]
        pin = self.pins[row.study_id]
        if pin.has_point:
            return pin.version
        pin = replace(
            pin,
            lat=lat,
            lon=lon,
            source="search",
            place_label=label,
            confirmed=False,
            version=pin.version + 1,
        )
        self.pins[row.study_id] = pin
        self._set(capture_id, pin_version=pin.version)
        self._event(
            row.study_id,
            "property.pin_placed",
            None,
            {"capture_id": str(capture_id), "lat": lat, "lon": lon, "label": label},
        )
        return pin.version

    async def labels(self, capture_id: uuid.UUID) -> set[str]:
        return {v.label for v in self.views if v.capture_id == capture_id}

    async def add_view(self, view: NewView) -> None:
        if view.label in await self.labels(view.capture_id):
            raise ValueError(f"view {view.label} already stored")
        self.views.append(view)
        row = self.captures[view.capture_id]
        self._set(view.capture_id, views=row.views + 1)

    async def wait_turn(self, capture_id: uuid.UUID, reason: str, resume_at: datetime) -> None:
        self._set(capture_id, status="waiting", code=None, reason=reason, resume_at=resume_at)

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
        tripped: Trip | None = None
        if block:
            tripped = gov.trip(
                self.state, incident.code, incident.detail or "", config, incident.at
            )
            self.state = tripped.state
            assert tripped.state.breaker_open_until is not None
            resume_at = max(resume_at, tripped.state.breaker_open_until)
            if tripped.new:
                assert tripped.state.breaker_opened_at is not None
                key = blocked_alert_key(tripped.number, tripped.state.breaker_opened_at)
                self.alerts.setdefault(
                    key,
                    {"kind": CAPTURE_BLOCKED, "key": key, **alert_payload(tripped, incident)},
                )
        row = self._set(
            capture_id, status="waiting", code=incident.code, reason=reason, resume_at=resume_at
        )
        self._incident(incident)
        self._event(
            row.study_id, "capture.paused", incident.code, event_detail(incident, reason, resume_at)
        )
        return tripped

    async def fail(self, capture_id: uuid.UUID, *, reason: str, incident: Incident) -> None:
        row = self._set(
            capture_id,
            status="failed",
            code=incident.code,
            reason=reason,
            resume_at=None,
            finished_at=incident.at,
        )
        self._incident(incident)
        self._event(
            row.study_id, "capture.failed", incident.code, event_detail(incident, reason, None)
        )

    async def part_failed(self, capture_id: uuid.UUID, note: Note, incident: Incident) -> None:
        row = self.captures[capture_id]
        if all(n.get("code") != note.code for n in row.notes):
            row = self._set(capture_id, notes=(*row.notes, note.as_json()))
        self._incident(incident)
        self._event(
            row.study_id,
            "capture.part_failed",
            incident.code,
            {**event_detail(incident, note.message, None), "part": note.part},
        )

    async def complete(self, capture_id: uuid.UUID, *, views: int, now: datetime) -> None:
        row = self._set(
            capture_id,
            status="succeeded",
            code=None,
            reason=None,
            resume_at=None,
            finished_at=now,
        )
        self.state = gov.succeeded(self.state, now)
        self._event(
            row.study_id,
            "capture.completed",
            E.CAPTURE_COMPLETED,
            {"capture_id": str(capture_id), "views": views},
        )

    async def worker_seen(self, now: datetime) -> None:
        self.worker_seen_at = now

    async def snapshot(self, now: datetime) -> Snapshot:
        state, usage = await self.governor(now)
        return Snapshot(
            state=state,
            usage=usage,
            worker_seen_at=self.worker_seen_at,
            lease_holder=self.lease_holder,
            lease_expires_at=self.lease_expires_at,
        )
