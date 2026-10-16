"""Read pages of a revision's outputs: extraction fields, rooms with inventories, assets.

The review gates show these (EVAL_REPORT section 23, finding 5: until v0.9 no route returned
them and the live client read the database). Each page is keyset-paginated on a stable
order with an opaque cursor that carries the revision it was issued for, like the evidence
page (:mod:`basis.db.repositories.evidence`), and is built with one query per table.

* fields: the revision's extraction fields, ordered by ``(section, field, id)``;
* rooms: the revision's rooms ordered by ``(name, id)``, each with its photos (file-name
  order) and its inventory: the reconciled ``room_inventory`` rows when the room has them,
  else the per-photo component rows (what ``process_assets`` builds the assets from);
* assets: ordered by id, each with its component, room, takeoff, cost estimate and
  citation counts; filterable by ``needs_review``.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.errors import InvalidCursorError
from basis.db.models import (
    Asset,
    Citation,
    Component,
    CostEstimate,
    Document,
    Extraction,
    ExtractionField,
    PhotoRoom,
    Room,
    Takeoff,
)
from basis.vision.rooms import is_room_inventory

MAX_OUTPUT_PAGE: Final = 200


def encode_cursor(revision: int, key: Sequence[str]) -> str:
    raw = json.dumps({"r": revision, "k": list(key)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, parts: int) -> tuple[int, list[str]]:
    """``(revision, key)`` of a cursor whose key has ``parts`` strings, the last a UUID."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        revision, key = data["r"], data["k"]
        if not isinstance(key, list) or len(key) != parts:
            raise ValueError("cursor key")
        if not all(isinstance(k, str) for k in key):
            raise ValueError("cursor key")
        uuid.UUID(key[-1])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise InvalidCursorError("invalid pagination cursor")
    return revision, key


@dataclass(frozen=True, slots=True)
class Page[T]:
    revision: int
    items: list[T]
    next_cursor: str | None


# --------------------------------------------------------------------------- fields


@dataclass(frozen=True, slots=True)
class FieldRow:
    field: ExtractionField
    extraction: Extraction


# --------------------------------------------------------------------------- rooms


@dataclass(frozen=True, slots=True)
class RoomPhoto:
    document_id: uuid.UUID
    filename: str


@dataclass(slots=True)
class RoomRow:
    room: Room
    photos: list[RoomPhoto] = field(default_factory=list)
    inventory: list[Component] = field(default_factory=list)
    reconciled: bool = False


# --------------------------------------------------------------------------- assets


@dataclass(frozen=True, slots=True)
class AssetRow:
    asset: Asset
    component: Component | None
    room: Room | None
    takeoff: Takeoff | None
    cost: CostEstimate | None
    citations: int
    verified_citations: int


class OutputRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fields(
        self, study_id: uuid.UUID, revision: int, *, limit: int, cursor: str | None = None
    ) -> Page[FieldRow]:
        after: list[str] | None = None
        if cursor is not None:
            revision, after = decode_cursor(cursor, 3)
        stmt = (
            select(ExtractionField, Extraction)
            .join(
                Extraction,
                and_(
                    Extraction.id == ExtractionField.extraction_id,
                    Extraction.study_id == ExtractionField.study_id,
                ),
            )
            .where(Extraction.study_id == study_id, Extraction.revision == revision)
        )
        if after is not None:
            stmt = stmt.where(
                tuple_(ExtractionField.section, ExtractionField.field, ExtractionField.id)
                > tuple_(after[0], after[1], uuid.UUID(after[2]))
            )
        stmt = stmt.order_by(ExtractionField.section, ExtractionField.field, ExtractionField.id)
        rows = (await self._session.execute(stmt.limit(limit + 1))).all()
        items = [FieldRow(field=f, extraction=e) for f, e in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1].field
            next_cursor = encode_cursor(revision, (last.section, last.field, str(last.id)))
        return Page(revision=revision, items=items, next_cursor=next_cursor)

    async def rooms(
        self, study_id: uuid.UUID, revision: int, *, limit: int, cursor: str | None = None
    ) -> Page[RoomRow]:
        after: list[str] | None = None
        if cursor is not None:
            revision, after = decode_cursor(cursor, 2)
        stmt = select(Room).where(Room.study_id == study_id, Room.revision == revision)
        if after is not None:
            stmt = stmt.where(
                or_(
                    Room.name > after[0],
                    and_(Room.name == after[0], Room.id > uuid.UUID(after[1])),
                )
            )
        found = list(
            (await self._session.scalars(stmt.order_by(Room.name, Room.id).limit(limit + 1))).all()
        )
        rooms = [RoomRow(room=room) for room in found[:limit]]
        by_id = {row.room.id: row for row in rooms}
        if by_id:
            photos = await self._session.execute(
                select(PhotoRoom.room_id, Document.id, Document.filename)
                .join(
                    Document,
                    and_(Document.id == PhotoRoom.document_id, Document.study_id == study_id),
                )
                .where(
                    PhotoRoom.study_id == study_id,
                    PhotoRoom.revision == revision,
                    PhotoRoom.room_id.in_(by_id),
                )
                .order_by(Document.filename, Document.id)
            )
            for room_id, document_id, filename in photos.all():
                by_id[room_id].photos.append(RoomPhoto(document_id, filename))
            components = await self._session.scalars(
                select(Component)
                .where(
                    Component.study_id == study_id,
                    Component.revision == revision,
                    Component.room_id.in_(by_id),
                )
                .order_by(Component.label, Component.id)
            )
            per_photo: dict[uuid.UUID, list[Component]] = {}
            for component in components.all():
                assert component.room_id is not None
                row = by_id[component.room_id]
                if is_room_inventory(component.raw, component.document_id):
                    row.inventory.append(component)
                    row.reconciled = True
                else:
                    per_photo.setdefault(component.room_id, []).append(component)
            for room_id, row in by_id.items():
                if not row.reconciled:
                    row.inventory = per_photo.get(room_id, [])
        next_cursor = None
        if len(found) > limit:
            last = rooms[-1].room
            next_cursor = encode_cursor(revision, (last.name, str(last.id)))
        return Page(revision=revision, items=rooms, next_cursor=next_cursor)

    async def assets(
        self,
        study_id: uuid.UUID,
        revision: int,
        *,
        limit: int,
        cursor: str | None = None,
        needs_review: bool | None = None,
    ) -> Page[AssetRow]:
        after: list[str] | None = None
        if cursor is not None:
            revision, after = decode_cursor(cursor, 1)
        stmt = select(Asset).where(Asset.study_id == study_id, Asset.revision == revision)
        if needs_review is not None:
            stmt = stmt.where(Asset.needs_review.is_(needs_review))
        if after is not None:
            stmt = stmt.where(Asset.id > uuid.UUID(after[0]))
        found = list((await self._session.scalars(stmt.order_by(Asset.id).limit(limit + 1))).all())
        assets = found[:limit]
        items: list[AssetRow] = []
        if assets:
            ids = [a.id for a in assets]
            component_ids = [a.component_id for a in assets if a.component_id is not None]
            components = {
                c.id: c
                for c in (
                    await self._session.scalars(
                        select(Component).where(
                            Component.study_id == study_id, Component.id.in_(component_ids)
                        )
                    )
                ).all()
            }
            room_ids = {c.room_id for c in components.values() if c.room_id is not None}
            rooms = {
                r.id: r
                for r in (
                    await self._session.scalars(
                        select(Room).where(Room.study_id == study_id, Room.id.in_(room_ids))
                    )
                ).all()
            }
            takeoffs = {
                t.asset_id: t
                for t in (
                    await self._session.scalars(
                        select(Takeoff).where(
                            Takeoff.study_id == study_id, Takeoff.asset_id.in_(ids)
                        )
                    )
                ).all()
            }
            costs = {
                c.asset_id: c
                for c in (
                    await self._session.scalars(
                        select(CostEstimate).where(
                            CostEstimate.study_id == study_id, CostEstimate.asset_id.in_(ids)
                        )
                    )
                ).all()
            }
            counted: dict[uuid.UUID, tuple[int, int]] = {}
            for asset_id, total, verified in (
                await self._session.execute(
                    select(
                        Citation.asset_id,
                        func.count(),
                        func.count(Citation.verified_at),
                    )
                    .where(Citation.study_id == study_id, Citation.asset_id.in_(ids))
                    .group_by(Citation.asset_id)
                )
            ).all():
                counted[asset_id] = (int(total), int(verified))
            for asset in assets:
                component = components.get(asset.component_id) if asset.component_id else None
                room = (
                    rooms.get(component.room_id)
                    if component is not None and component.room_id is not None
                    else None
                )
                total, verified = counted.get(asset.id, (0, 0))
                items.append(
                    AssetRow(
                        asset=asset,
                        component=component,
                        room=room,
                        takeoff=takeoffs.get(asset.id),
                        cost=costs.get(asset.id),
                        citations=total,
                        verified_citations=verified,
                    )
                )
        next_cursor = encode_cursor(revision, (str(assets[-1].id),)) if len(found) > limit else None
        return Page(revision=revision, items=items, next_cursor=next_cursor)


def component_count(component: Component) -> int:
    """The component's count as stored (``raw.count``; 1 when absent or invalid)."""
    raw: Any = component.raw if isinstance(component.raw, dict) else {}
    count = raw.get("count")
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 1


__all__ = [
    "MAX_OUTPUT_PAGE",
    "AssetRow",
    "FieldRow",
    "OutputRepository",
    "Page",
    "RoomPhoto",
    "RoomRow",
    "component_count",
    "decode_cursor",
    "encode_cursor",
]
