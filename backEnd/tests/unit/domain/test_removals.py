"""Removing a room inventory component row (``component_remove``; basis.domain.removals).

The rules the decision route and ``apply_corrections`` share: the kind of row, a row the
rejection rebuilds anyway (a flagged photo's room, a merged room, a flagged photo's own row),
and a reconciled room whose every inventory row is removed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from basis.domain.removals import (
    Row,
    as_row,
    emptied_message,
    emptied_rooms,
    rebuilt_problem,
    rebuilt_rooms,
    row_kind,
)
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, ROOM_MANUAL_SOURCE

ROOM, OTHER, PHOTO, PHOTO2 = (uuid.uuid4() for _ in range(4))


def row(kind: str, room: uuid.UUID | None = ROOM, document: uuid.UUID | None = None) -> Row:
    return Row(id=uuid.uuid4(), kind=kind, room_id=room, document_id=document)  # type: ignore[arg-type]


def test_the_kind_of_a_row() -> None:
    assert row_kind({"source": ROOM_INVENTORY_SOURCE}, None) == "room_inventory"
    assert row_kind({"source": ROOM_MANUAL_SOURCE}, None) == "room_manual"
    assert row_kind({"source": ROOM_INVENTORY_SOURCE}, PHOTO) == "per_photo"  # has a photo
    assert row_kind({"count": 2}, PHOTO) == "per_photo"
    assert row_kind(None, PHOTO) == "per_photo"
    component = SimpleNamespace(
        id=uuid.uuid4(), raw={"source": ROOM_MANUAL_SOURCE}, room_id=ROOM, document_id=None
    )
    assert as_row(component) == Row(component.id, "room_manual", ROOM, None)


def test_the_rebuilt_rooms_are_the_flagged_photos_rooms_and_every_merged_room() -> None:
    assert rebuilt_rooms([PHOTO], {PHOTO: ROOM, PHOTO2: OTHER}) == {ROOM}
    assert rebuilt_rooms([uuid.uuid4()], {PHOTO: ROOM}) == set()  # a photo with no room
    assert rebuilt_rooms([], {}, [ROOM, OTHER]) == {ROOM, OTHER}


def test_a_rebuilt_row_is_refused_and_the_others_are_not() -> None:
    inventory = row("room_inventory")
    assert "rebuilds" in (rebuilt_problem(inventory, flagged_photos=[], rebuilt=[ROOM]) or "")
    assert rebuilt_problem(inventory, flagged_photos=[], rebuilt=[OTHER]) is None
    flagged_row = row("per_photo", document=PHOTO)
    message = rebuilt_problem(flagged_row, flagged_photos=[PHOTO], rebuilt=[ROOM])
    assert message is not None and "re-analyzes" in message
    # Another photo's row of the rebuilt room is carried, so its removal holds; so is an
    # engineer's added row (the room step never rebuilds it).
    assert (
        rebuilt_problem(row("per_photo", document=PHOTO2), flagged_photos=[PHOTO], rebuilt=[ROOM])
        is None
    )
    assert rebuilt_problem(row("room_manual"), flagged_photos=[PHOTO], rebuilt=[ROOM]) is None


def test_a_room_is_emptied_only_when_every_inventory_row_goes() -> None:
    a, b = row("room_inventory"), row("room_inventory")
    other = row("room_inventory", room=OTHER)
    manual, photo_row = row("room_manual"), row("per_photo", document=PHOTO)
    rows = [a, b, other, manual, photo_row]
    assert emptied_rooms(rows, [a.id]) == set()
    assert emptied_rooms(rows, [a.id, b.id]) == {ROOM}
    assert emptied_rooms(rows, [manual.id, photo_row.id]) == set()  # no inventory row removed
    assert emptied_rooms([manual, photo_row], [manual.id, photo_row.id]) == set()  # not reconciled
    assert "last inventory row" in emptied_message(a)
