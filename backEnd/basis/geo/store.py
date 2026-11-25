"""What a gather reads and writes, as a protocol (:class:`GeoStore`), with an in-memory version
for the unit tests; :mod:`basis.geo.pg_store` is the Postgres one."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from basis.geo.parcels import Parcel


@dataclass(frozen=True, slots=True)
class GatherRow:
    id: uuid.UUID
    study_id: uuid.UUID
    status: str
    pin_version: int
    lat: float
    lon: float
    code: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ViewRecord:
    gather_id: uuid.UUID
    study_id: uuid.UUID
    source_id: str
    kind: str  # current | history
    label: str
    year: int | None
    year_label: str | None
    leaf_off: bool | None
    lat: float
    lon: float
    pixel_size_m: float
    native_pixel_m: float | None
    bbox: tuple[float, float, float, float]
    width: int
    height: int
    uri: str
    content_type: str
    sha256: str
    bytes: int
    credit: str
    terms_class: str
    terms_url: str
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Incident:
    code: str
    source_id: str
    at: datetime
    study_id: uuid.UUID | None
    gather_id: uuid.UUID | None
    url: str | None = None
    http_status: int | None = None
    detail: str | None = None


class GeoStore(Protocol):
    async def gather(self, gather_id: uuid.UUID) -> GatherRow | None: ...
    async def start(self, gather_id: uuid.UUID, *, now: datetime) -> GatherRow | None:
        """Mark it running (from queued, or again from running on a retry); ``None`` when it has
        ended already."""
        ...

    async def labels(self, gather_id: uuid.UUID) -> set[str]: ...
    async def add_view(self, view: ViewRecord) -> None: ...
    async def has_parcels(self, gather_id: uuid.UUID) -> bool: ...
    async def add_parcels(
        self, row: GatherRow, parcels: list[Parcel], *, credit: str, terms_class: str
    ) -> None: ...
    async def source_failed(
        self, row: GatherRow, outcome: dict[str, Any], incident: Incident
    ) -> None: ...
    async def appraisal_site_sqft(self, study_id: uuid.UUID) -> float | None: ...
    async def site_checked(
        self, row: GatherRow, detail: dict[str, Any], code: str | None
    ) -> None: ...
    async def finish(
        self,
        gather_id: uuid.UUID,
        *,
        sources: list[dict[str, Any]],
        lidar: dict[str, Any] | None,
        code: str,
        now: datetime,
    ) -> None: ...
    async def fail(
        self, gather_id: uuid.UUID, *, code: str, reason: str, incident: Incident, now: datetime
    ) -> None: ...


@dataclass
class MemoryGeoStore:
    """The in-memory store the unit tests drive the gatherer with."""

    gathers: dict[uuid.UUID, GatherRow] = field(default_factory=dict)
    views: list[ViewRecord] = field(default_factory=list)
    parcels: dict[uuid.UUID, list[Parcel]] = field(default_factory=dict)
    incidents: list[Incident] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finished: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    appraisal: dict[uuid.UUID, float] = field(default_factory=dict)

    async def gather(self, gather_id: uuid.UUID) -> GatherRow | None:
        return self.gathers.get(gather_id)

    async def start(self, gather_id: uuid.UUID, *, now: datetime) -> GatherRow | None:
        row = self.gathers.get(gather_id)
        if row is None or row.status not in ("queued", "running"):
            return None
        row = replace(row, status="running")
        self.gathers[gather_id] = row
        self.events.append(("geo.started", {"gather_id": str(gather_id)}))
        return row

    async def labels(self, gather_id: uuid.UUID) -> set[str]:
        return {v.label for v in self.views if v.gather_id == gather_id}

    async def add_view(self, view: ViewRecord) -> None:
        if view.label in await self.labels(view.gather_id):
            raise ValueError(f"duplicate label {view.label}")
        self.views.append(view)

    async def has_parcels(self, gather_id: uuid.UUID) -> bool:
        return gather_id in self.parcels

    async def add_parcels(
        self, row: GatherRow, parcels: list[Parcel], *, credit: str, terms_class: str
    ) -> None:
        self.parcels.setdefault(row.id, []).extend(parcels)

    async def source_failed(
        self, row: GatherRow, outcome: dict[str, Any], incident: Incident
    ) -> None:
        self.incidents.append(incident)
        self.events.append(("geo.source_failed", outcome))

    async def appraisal_site_sqft(self, study_id: uuid.UUID) -> float | None:
        return self.appraisal.get(study_id)

    async def site_checked(self, row: GatherRow, detail: dict[str, Any], code: str | None) -> None:
        self.events.append(("geo.site_area_checked", {**detail, "code": code}))

    async def finish(
        self,
        gather_id: uuid.UUID,
        *,
        sources: list[dict[str, Any]],
        lidar: dict[str, Any] | None,
        code: str,
        now: datetime,
    ) -> None:
        self.gathers[gather_id] = replace(self.gathers[gather_id], status="succeeded", code=code)
        self.finished[gather_id] = {"sources": sources, "lidar": lidar}
        self.events.append(("geo.completed", {"code": code}))

    async def fail(
        self, gather_id: uuid.UUID, *, code: str, reason: str, incident: Incident, now: datetime
    ) -> None:
        row = self.gathers[gather_id]
        if row.status in ("succeeded", "failed"):
            return
        self.gathers[gather_id] = replace(row, status="failed", code=code, reason=reason)
        self.incidents.append(incident)
        self.events.append(("geo.failed", {"code": code}))
