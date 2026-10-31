"""A removed inventory row and the room's suggested counts (``/rooms``; basis.domain.removals).

A ``component_remove`` leaves the row out of the next revision but never touches the photo's
localized objects, so ``room_out`` still reports what the photos counted for the label: the
inventory count drops to what is left and ``photo_count`` stays, so the reviewer sees the
removal against the photos (the suggestion is max(inventory, photo)).
"""

from __future__ import annotations

import uuid

from basis.api.routes.outputs import room_out
from basis.db.models import Component, Room
from basis.db.repositories.outputs import RoomPhoto, RoomRow
from basis.vision.rooms import ROOM_INVENTORY_SOURCE

STUDY, PHOTO = uuid.uuid4(), uuid.uuid4()


def kitchen() -> Room:
    return Room(
        id=uuid.uuid4(),
        study_id=STUDY,
        revision=2,
        name="Kitchen",
        room_type="kitchen",
        source="vision",
    )


def inventory_row(room: Room, label: str, count: int) -> Component:
    return Component(
        id=uuid.uuid4(),
        study_id=STUDY,
        revision=2,
        document_id=None,
        room_id=room.id,
        label=label,
        raw={"source": ROOM_INVENTORY_SOURCE, "count": count, "photos": [str(PHOTO)]},
    )


def row(room: Room, inventory: list[Component]) -> RoomRow:
    return RoomRow(
        room=room,
        photos=[RoomPhoto(PHOTO, "kitchen_1.jpg")],
        inventory=inventory,
        reconciled=True,
        # The photo's localized objects (never touched by a removal): two windows counted.
        objects={PHOTO: {"window": (2, 0), "kitchen_cabinet": (6, 0)}},
    )


def test_the_photo_count_survives_a_removed_inventory_row() -> None:
    room = kitchen()
    cabinets, window = inventory_row(room, "kitchen_cabinet", 6), inventory_row(room, "window", 2)
    before = {s.label: s for s in room_out(row(room, [cabinets, window])).suggested_counts}
    after_out = room_out(row(room, [cabinets]))  # the window row removed
    after = {s.label: s for s in after_out.suggested_counts}
    assert (before["window"].inventory_count, before["window"].photo_count) == (2, 2)
    assert (after["window"].inventory_count, after["window"].photo_count) == (0, 2)
    assert after["window"].suggested == 2 and after["window"].document_id == PHOTO
    assert [item.label for item in after_out.inventory] == ["kitchen_cabinet"]
    assert after["kitchen_cabinet"] == before["kitchen_cabinet"]
