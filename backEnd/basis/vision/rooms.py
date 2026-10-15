"""Map a :class:`PhotoAnalysis` to ``rooms``, ``photo_rooms`` and ``components`` rows.

Room identity within a study revision is ``(room_type, floor, ordinal)``:

* ``room_type`` is the normalized enum value; ``floor`` is the model's hint as text
  (``None`` when not visible); ``ordinal`` is 1 unless the photo itself shows otherwise.
* The room's display name encodes the ordinal ("Bedroom", "Bedroom 2"); the floor is in
  its own column, so the kitchen on floor 1 and the one on floor 2 are two rows both
  named "Kitchen".
* Every photo with the same identity in the revision shares one row (several photos of
  one kitchen are one room). A room a reviewer corrected (``source="manual"``, e.g.
  renamed "Main Kitchen") still matches its identity, so re-analyzing a flagged photo
  after a rooms rejection joins that room instead of creating a duplicate.
* An ``ambiguous`` photo goes to the revision's "Unassigned" room so it still has a
  ``photo_rooms`` row the reviewer can move.

A photo can only identify its own room; telling two bedrooms on one floor apart needs the
engineer (rooms review) unless a visible sign gives the ordinal. This is a documented
limitation, not a guess.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.repositories.room import RoomRepository
from basis.vision.schemas import Attachment, Material, PhotoAnalysis, RoomType

SOURCE: Final = "vision"
UNASSIGNED: Final = "Unassigned"


@dataclass(frozen=True, slots=True)
class RoomKey:
    room_type: str
    floor: str | None
    ordinal: int

    @property
    def name(self) -> str:
        if self.room_type == RoomType.AMBIGUOUS.value:
            return UNASSIGNED
        title = self.room_type.replace("_", " ").title()
        return title if self.ordinal == 1 else f"{title} {self.ordinal}"


def room_key(analysis: PhotoAnalysis) -> RoomKey:
    if analysis.room_type is RoomType.AMBIGUOUS:
        return RoomKey(RoomType.AMBIGUOUS.value, None, 1)
    floor = None if analysis.floor is None else str(analysis.floor)
    return RoomKey(analysis.room_type.value, floor, analysis.room_ordinal)


def _optional(value: str) -> str | None:
    return None if value in (Material.UNKNOWN.value, Attachment.UNKNOWN.value) else value


@dataclass(frozen=True, slots=True)
class PersistedPhoto:
    room_id: uuid.UUID
    room_created: bool
    components: int


async def assign_room(
    session: AsyncSession,
    *,
    study_id: uuid.UUID,
    revision: int,
    analysis: PhotoAnalysis,
) -> tuple[uuid.UUID, bool]:
    key = room_key(analysis)
    room, created = await RoomRepository(session).get_or_create_by_identity(
        study_id=study_id,
        revision=revision,
        room_type=key.room_type,
        floor=key.floor,
        name=key.name,
        source=SOURCE,
        reuse_renamed=key.ordinal == 1 and key.room_type != RoomType.AMBIGUOUS.value,
    )
    return room.id, created


async def write_photo(
    session: AsyncSession,
    *,
    study_id: uuid.UUID,
    document_id: uuid.UUID,
    revision: int,
    room_id: uuid.UUID,
    analysis: PhotoAnalysis,
    context: dict[str, Any],
) -> int:
    """The ``photo_rooms`` row and one ``components`` row per component kind."""
    rooms = RoomRepository(session)
    await rooms.assign_photo(
        study_id=study_id, document_id=document_id, room_id=room_id, revision=revision
    )
    for component in analysis.components:
        await rooms.add_component(
            study_id=study_id,
            revision=revision,
            document_id=document_id,
            room_id=room_id,
            label=component.label.value,
            material=_optional(component.material.value),
            attachment=_optional(component.attachment.value),
            confidence=component.confidence,
            raw={
                "source": SOURCE,
                "count": component.count,
                "note": component.note,
                "material": component.material.value,
                "attachment": component.attachment.value,
                **context,
            },
        )
    return len(analysis.components)
