"""Removing an erroneous room inventory component row (``component_remove``).

A correction accepted wherever ``component`` is (the components review with the components
gate; the single rooms review without it). ``entity_id`` is a component row of the decided
revision that the room's assets are built from: a reconciled room-inventory row
(``room_inventory``), a component an engineer added to the room (``room_manual``) or a
photo's own row (``per_photo``; :func:`row_kind`). ``after`` is exactly ``{}`` and the
correction's ``reason`` is required: the removal is engineer evidence. ``apply_corrections``
does not copy the row into the next revision, so it is absent from every later revision (each
copies from the one before).

What it is not: deleting a photo's detected object (``photo_objects``, the localized instances
the suggested counts are read from) is ``DELETE
/v1/studies/{id}/photos/{document_id}/objects/{object_id}``, which soft-deletes an engineer's
manual instance (a model instance stays: it is evidence of what the model found) and never
touches a component row. A removal never deletes or changes a ``photo_objects`` row, so after
a removal the room's ``suggested_counts`` still show the photo's count for the label (the
inventory count drops) and the reviewer can see the removal against what the photos show.

Pure, so the decision route (a 422 before the decision is recorded) and ``apply_corrections``
(a ``ValidationError``) refuse the same removals:

* :func:`rebuilt_problem`: a row the rejection rebuilds anyway. An inventory row of a room
  whose inventory ``reconcile_rooms`` rebuilds in the next revision (a photo of the room is
  flagged for re-analysis by a ``photo`` correction of the same decision, or the room is
  merged), or a row of a flagged photo itself: ``apply_corrections`` does not carry those rows
  and corrections of rebuilt rows are not carried, so the removal would silently do nothing.
* :func:`emptied_rooms`: a reconciled room whose every inventory row the decision removes.
  Without inventory rows the room falls back to its per-photo rows (the outputs and
  ``process_assets``), and the room step treats it as not built and rebuilds its inventory
  from the photos, so the removed rows would come back.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from basis.vision.rooms import is_room_inventory, is_room_manual

RowKind = Literal["room_inventory", "room_manual", "per_photo"]
COMPONENT_REMOVE: Final = "component_remove"


def row_kind(raw: Any, document_id: uuid.UUID | None) -> RowKind:
    """Which kind of component row: a reconciled inventory row, an engineer's added row, or a
    photo's row (any other row)."""
    if is_room_inventory(raw, document_id):
        return "room_inventory"
    if is_room_manual(raw, document_id):
        return "room_manual"
    return "per_photo"


@dataclass(frozen=True, slots=True)
class Row:
    """What the removal rules need of a component row."""

    id: uuid.UUID
    kind: RowKind
    room_id: uuid.UUID | None
    document_id: uuid.UUID | None


def as_row(component: Any) -> Row:
    """A :class:`Row` of a ``components`` row (anything with ``id``, ``raw``, ``room_id``,
    ``document_id``)."""
    return Row(
        id=component.id,
        kind=row_kind(component.raw, component.document_id),
        room_id=component.room_id,
        document_id=component.document_id,
    )


def rebuilt_rooms(
    flagged_photos: Iterable[uuid.UUID],
    room_of_photo: Mapping[uuid.UUID, uuid.UUID],
    merged_rooms: Iterable[uuid.UUID] = (),
) -> set[uuid.UUID]:
    """The rooms whose inventory the next revision rebuilds: the rooms of the flagged photos
    in the decided revision, and every room a merge names (source or target)."""
    rooms = {room_of_photo[p] for p in flagged_photos if p in room_of_photo}
    return rooms | set(merged_rooms)


def rebuilt_problem(
    row: Row, *, flagged_photos: Iterable[uuid.UUID], rebuilt: Iterable[uuid.UUID]
) -> str | None:
    """Why removing ``row`` would do nothing (it is rebuilt), or None."""
    if row.kind == "room_inventory" and row.room_id is not None and row.room_id in set(rebuilt):
        return (
            f"component {row.id} is an inventory row of room {row.room_id}, whose inventory "
            "this rejection rebuilds (a photo of the room is flagged for re-analysis, or the "
            "room is merged): the room step rebuilds it from the photos and corrections of "
            "rebuilt rows are not carried; remove the row from the next revision instead"
        )
    if row.kind == "per_photo" and row.document_id in set(flagged_photos):
        return (
            f"component {row.id} is a row of photo {row.document_id}, which this rejection "
            "re-analyzes: the photo's rows are rebuilt and corrections of rebuilt rows are "
            "not carried; remove the row from the next revision instead"
        )
    return None


def emptied_rooms(rows: Iterable[Row], removed: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The reconciled rooms whose every inventory row is in ``removed``."""
    gone = set(removed)
    inventory: dict[uuid.UUID, set[uuid.UUID]] = {}
    for row in rows:
        if row.kind == "room_inventory" and row.room_id is not None:
            inventory.setdefault(row.room_id, set()).add(row.id)
    return {room for room, ids in inventory.items() if ids <= gone}


def emptied_message(row: Row) -> str:
    return (
        f"component {row.id} is the last inventory row of room {row.room_id} this decision "
        "keeps: without inventory rows the room falls back to its per-photo rows and the room "
        "step rebuilds its inventory from the photos (the removed rows would come back); keep "
        "one row and correct it, or flag the room's photos for re-analysis"
    )
