"""Rooms, photo-to-room assignments and components (``rooms``, ``photo_rooms``, ``components``).

All three are grouped by the study's revision: a rejection starts a new revision and the
outputs that survive are copied into it (``basis.activities.apply_corrections``), so the
rows of a decided revision stay unchanged as history.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import Component, PhotoRoom, Room


class RoomRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        *,
        study_id: uuid.UUID,
        revision: int,
        name: str,
        room_type: str,
        source: str,
        floor: str | None = None,
    ) -> tuple[Room, bool]:
        """The room named ``name`` in this study revision, created if missing.

        ``rooms`` has no unique key on the name, so two photos of the same room analyzed
        concurrently would both insert; a transaction-scoped advisory lock on
        ``(study, revision, name)`` serializes them instead.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"room:{study_id}:{revision}:{name}"},
        )
        stmt = select(Room).where(
            Room.study_id == study_id, Room.revision == revision, Room.name == name
        )
        existing = (await self._session.scalars(stmt)).first()
        if existing is not None:
            return existing, False
        room = Room(
            study_id=study_id,
            revision=revision,
            name=name,
            room_type=room_type,
            floor=floor,
            source=source,
        )
        self._session.add(room)
        await self._session.flush()
        await self._session.refresh(room)
        return room, True

    async def get_or_create_by_identity(
        self,
        *,
        study_id: uuid.UUID,
        revision: int,
        room_type: str,
        floor: str | None,
        name: str,
        source: str,
        reuse_renamed: bool,
    ) -> tuple[Room, bool]:
        """The room with this identity in the revision, created if missing.

        Identity is ``(room_type, floor, name)`` where ``name`` encodes the ordinal
        ("Bedroom 2"). With ``reuse_renamed`` and no room of that name, a room of the same
        type and floor that a reviewer corrected (``source="manual"``, e.g. renamed to
        "Main Kitchen") is reused, oldest first. Serialized per
        ``(study, revision, room_type, floor)`` with a transaction-scoped advisory lock, so
        concurrent photos of one room share a row.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"room-identity:{study_id}:{revision}:{room_type}:{floor}"},
        )
        same_kind = (
            select(Room)
            .where(
                Room.study_id == study_id,
                Room.revision == revision,
                Room.room_type == room_type,
                Room.floor.is_not_distinct_from(floor),
            )
            .order_by(Room.created_at, Room.id)
        )
        rooms = list((await self._session.scalars(same_kind)).all())
        for room in rooms:
            if room.name == name:
                return room, False
        renamed = [room for room in rooms if room.source == "manual"]
        if reuse_renamed and renamed:
            return renamed[0], False
        return (
            await self.create(
                study_id=study_id,
                revision=revision,
                name=name,
                room_type=room_type,
                source=source,
                floor=floor,
            ),
            True,
        )

    async def get_or_create_by_group(
        self,
        *,
        study_id: uuid.UUID,
        revision: int,
        group_key: str,
        name: str,
        room_type: str,
        floor: str | None,
        source: str,
    ) -> tuple[Room, bool]:
        """The room of the revision that groups file-name room ``group_key``, created if
        missing. Serialized per ``(study, revision, group_key)`` with a transaction-scoped
        advisory lock, so concurrent photos of one room share a row; a room a reviewer
        renamed keeps its key and is found by it."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"room-group:{study_id}:{revision}:{group_key}"},
        )
        stmt = (
            select(Room)
            .where(
                Room.study_id == study_id,
                Room.revision == revision,
                Room.group_key == group_key,
            )
            .order_by(Room.created_at, Room.id)
        )
        existing = (await self._session.scalars(stmt)).first()
        if existing is not None:
            return existing, False
        room = await self.create(
            study_id=study_id,
            revision=revision,
            name=name,
            room_type=room_type,
            source=source,
            floor=floor,
            group_key=group_key,
        )
        return room, True

    async def create(
        self,
        *,
        study_id: uuid.UUID,
        revision: int,
        name: str,
        room_type: str,
        source: str,
        floor: str | None = None,
        group_key: str | None = None,
    ) -> Room:
        room = Room(
            study_id=study_id,
            revision=revision,
            name=name,
            room_type=room_type,
            floor=floor,
            source=source,
            group_key=group_key,
        )
        self._session.add(room)
        await self._session.flush()
        await self._session.refresh(room)
        return room

    async def get(self, study_id: uuid.UUID, room_id: uuid.UUID) -> Room | None:
        stmt = select(Room).where(Room.id == room_id, Room.study_id == study_id)
        return (await self._session.scalars(stmt)).one_or_none()

    async def list_for_revision(self, study_id: uuid.UUID, revision: int) -> list[Room]:
        stmt = (
            select(Room)
            .where(Room.study_id == study_id, Room.revision == revision)
            .order_by(Room.name, Room.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def assign_photo(
        self, *, study_id: uuid.UUID, document_id: uuid.UUID, room_id: uuid.UUID, revision: int
    ) -> PhotoRoom:
        assignment = PhotoRoom(
            study_id=study_id, document_id=document_id, room_id=room_id, revision=revision
        )
        self._session.add(assignment)
        await self._session.flush()
        return assignment

    async def photo_room(self, document_id: uuid.UUID, revision: int) -> PhotoRoom | None:
        return await self._session.get(PhotoRoom, (document_id, revision))

    async def photo_rooms(self, study_id: uuid.UUID, revision: int) -> list[PhotoRoom]:
        stmt = (
            select(PhotoRoom)
            .where(PhotoRoom.study_id == study_id, PhotoRoom.revision == revision)
            .order_by(PhotoRoom.document_id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def add_component(
        self,
        *,
        study_id: uuid.UUID,
        revision: int,
        document_id: uuid.UUID | None,
        room_id: uuid.UUID | None,
        label: str,
        material: str | None,
        attachment: str | None,
        confidence: float | None,
        bbox: Any = None,
        raw: Any = None,
    ) -> Component:
        component = Component(
            study_id=study_id,
            revision=revision,
            document_id=document_id,
            room_id=room_id,
            label=label,
            material=material,
            attachment=attachment,
            confidence=confidence,
            bbox=bbox,
            raw=raw,
        )
        self._session.add(component)
        await self._session.flush()
        await self._session.refresh(component)
        return component

    async def get_component(self, study_id: uuid.UUID, component_id: uuid.UUID) -> Component | None:
        stmt = select(Component).where(Component.id == component_id, Component.study_id == study_id)
        return (await self._session.scalars(stmt)).one_or_none()

    async def components(self, study_id: uuid.UUID, revision: int) -> list[Component]:
        stmt = (
            select(Component)
            .where(Component.study_id == study_id, Component.revision == revision)
            .order_by(Component.document_id, Component.label, Component.id)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_components(
        self, study_id: uuid.UUID, revision: int, document_id: uuid.UUID | None = None
    ) -> int:
        stmt = select(func.count()).where(
            Component.study_id == study_id, Component.revision == revision
        )
        if document_id is not None:
            stmt = stmt.where(Component.document_id == document_id)
        return int((await self._session.scalar(stmt)) or 0)
