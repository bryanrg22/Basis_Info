"""What ``process_assets`` drafts from (EVAL_REPORT 110; :mod:`basis.domain.measure_drafts`).

* :func:`exterior_source` (``MEASURE_DRAFTS_EXTERIOR``): the building shell of the study's
  newest gather that stored one, only when the pin is confirmed and the gather was taken at the
  pin's current point (a pin moved since makes it a shell of another place, as the public
  route's ``stale``). Otherwise the reason, written on the roof, siding and gutter rows.
* :func:`room_drafts` (``MEASURE_DRAFTS_INTERIOR``): each room's draft, from the drafts the
  workflow passed (``ProcessAssetsInput.room_drafts``, keyed by the room ``measure_rooms``
  measured, followed along ``rooms.carried_from``), else from the rejected revision's rows
  (each drafted row's ``takeoffs.inputs.draft`` keeps the room's answer: a reprice, or an
  execution reopened at the assets review, carries the drafts it priced on). A draft is used
  only while the room has exactly the photos it measured and its type is the one measured.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import Asset, Component, GeoGather, PropertyPin, Room, Takeoff
from basis.db.repositories.room import RoomRepository
from basis.domain import measure_drafts as md
from basis.domain.rooms import normalize_room_type
from basis.workflow.contracts import RoomDraftOut

SAME_POINT_M: Final = 1.0  # as the property routes decide a view or gather is the pin's
MAX_LINEAGE: Final = 1000
INTERIOR_SUFFIX: Final = f"@{md.INTERIOR_PROMPT}-{md.INTERIOR_PROMPT_VERSION}"


def _near(lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
    north = math.radians(lat2 - lat1) * 6_371_008.8
    east = math.radians(lon2 - lon1) * 6_371_008.8 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(north, east) <= SAME_POINT_M


async def exterior_source(
    session: AsyncSession, study_id: uuid.UUID
) -> tuple[md.ShellSource | None, str | None]:
    """``(the shell to draft from, None)`` or ``(None, why there is none)``."""
    pin = await session.get(PropertyPin, study_id)
    if pin is None or pin.lat is None or pin.lon is None:
        return None, "the study has no pin on the building"
    if not pin.confirmed:
        return None, "the pin is not confirmed (nothing is measured before it)"
    rows = await session.scalars(
        select(GeoGather)
        .where(GeoGather.study_id == study_id, GeoGather.status == "succeeded")
        .order_by(GeoGather.finished_at.desc(), GeoGather.id)
        .limit(10)
    )
    for gather in rows:
        lidar = gather.lidar if isinstance(gather.lidar, dict) else None
        shell = None if lidar is None else lidar.get("shell")
        if not isinstance(shell, dict):
            continue
        if not _near(gather.lat, gather.lon, pin.lat, pin.lon):
            return None, "the newest shell was gathered around an earlier pin"
        if shell.get("method") is None:
            return None, f"the shell could not be read ({shell.get('code')})"
        year = lidar.get("year") if lidar is not None else None
        project = lidar.get("project") if lidar is not None else None
        return (
            md.ShellSource(
                str(gather.id),
                project if isinstance(project, str) else None,
                year if isinstance(year, int) and not isinstance(year, bool) else None,
                shell,
            ),
            None,
        )
    return None, "no public records gather stored a building shell (GEO_SHELL)"


def _from_out(draft: RoomDraftOut) -> md.RoomDraft | None:
    if draft.status != "drafted" or draft.model is None:
        return None
    return md.RoomDraft(
        room_type=draft.room_type,
        quantities=md.answer_quantities(draft.quantities, draft.room_type),
        model=draft.model,
        photos=tuple(draft.photos),
        notes=draft.notes,
        confidence=draft.confidence,
        room_fully_seen=draft.room_fully_seen,
    )


def _from_record(record: Mapping[str, Any], room_type: str) -> md.RoomDraft | None:
    """A room's draft as a drafted row recorded it (``takeoffs.inputs.draft``)."""
    drawn = record.get("drawn_by")
    photos, answer = record.get("photos"), record.get("answer")
    if (
        not isinstance(drawn, str)
        or not drawn.startswith("model:")
        or not drawn.endswith(INTERIOR_SUFFIX)
        or not isinstance(photos, list)
        or not isinstance(answer, dict)
    ):
        return None
    model = drawn[len("model:") : -len(INTERIOR_SUFFIX)]
    notes, confidence = record.get("notes"), record.get("confidence")
    seen = record.get("room_fully_seen")
    return md.RoomDraft(
        room_type=room_type,
        quantities=md.answer_quantities(answer, room_type),
        model=model,
        photos=tuple(str(p) for p in photos),
        notes=notes if isinstance(notes, str) else None,
        confidence=confidence if isinstance(confidence, str) else None,
        room_fully_seen=seen if isinstance(seen, bool) else None,
    )


async def room_drafts(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    rooms: Mapping[uuid.UUID, Room],
    given: Sequence[RoomDraftOut],
) -> dict[uuid.UUID, md.RoomDraft]:
    """Each current room's draft (module doc), by room id."""
    repo = RoomRepository(session)
    photos: dict[uuid.UUID, set[str]] = {}
    for assignment in await repo.photo_rooms(study_id, revision):
        photos.setdefault(assignment.room_id, set()).add(str(assignment.document_id))
    parents = dict(
        (await session.execute(select(Room.id, Room.carried_from).where(Room.study_id == study_id)))
        .tuples()
        .all()
    )
    by_measured = {uuid.UUID(d.room_id): d for d in given}
    previous: dict[uuid.UUID, dict[str, Any]] = {}
    if revision > 1:
        previous = await _previous_records(session, study_id, revision - 1)
    out: dict[uuid.UUID, md.RoomDraft] = {}
    for room in rooms.values():
        kind = normalize_room_type(room.room_type)
        if kind not in md.INTERIOR_ROOM_TYPES:
            continue
        draft: md.RoomDraft | None = None
        node: uuid.UUID | None = room.id
        for _ in range(MAX_LINEAGE):
            if node is None:
                break
            if node in by_measured:
                found = by_measured[node]
                draft = _from_out(found) if found.room_type == kind else None
                break
            node = parents.get(node)
        if draft is None and room.carried_from in previous:
            draft = _from_record(previous[room.carried_from], kind)
        if draft is None or set(draft.photos) != photos.get(room.id, set()):
            continue
        out[room.id] = draft
    return out


async def _previous_records(
    session: AsyncSession, study_id: uuid.UUID, revision: int
) -> dict[uuid.UUID, dict[str, Any]]:
    """The interior draft records of a revision's rows, by the room their component is in."""
    rows = await session.execute(
        select(Component.room_id, Takeoff.inputs)
        .join(Asset, Asset.component_id == Component.id)
        .join(Takeoff, Takeoff.asset_id == Asset.id)
        .where(Asset.study_id == study_id, Asset.revision == revision)
    )
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for room_id, inputs in rows.tuples():
        record = inputs.get(md.DRAFT) if isinstance(inputs, dict) else None
        drawn = record.get("drawn_by") if isinstance(record, dict) else None
        if room_id is None or not isinstance(drawn, str) or not drawn.endswith(INTERIOR_SUFFIX):
            continue
        assert isinstance(record, dict)
        out.setdefault(room_id, record)
    return out


__all__ = ["exterior_source", "room_drafts"]
