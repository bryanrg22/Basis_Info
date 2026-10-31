"""A room's name, type and floor, each from its own evidence (``basis.vision.rooms``,
EVAL_REPORT section 48): the name without the floor it starts with, the type the name says
(else the per-photo model's vote), the floor a file name gives (else the model's, except for
the grounds), and the initial grouping the Rooms review opens on.
"""

from __future__ import annotations

import pytest

from basis.domain.rooms import room_field_problems, stored_floor
from basis.vision.rooms import (
    PhotoInput,
    RoomAnswer,
    group_photos,
    initial_rooms,
    name_room_type,
    resolve_room,
    room_name,
)
from basis.vision.schemas import (
    Attachment,
    Component,
    ComponentLabel,
    Material,
    PhotoAnalysis,
    RoomType,
)


def analysis(
    room: RoomType, floor: int | None = None, *, components: int = 1, ordinal: int = 1
) -> PhotoAnalysis:
    outlet = Component(
        label=ComponentLabel.ELECTRICAL_OUTLET,
        count=1,
        material=Material.UNKNOWN,
        attachment=Attachment.UNKNOWN,
        confidence=0.9,
    )
    return PhotoAnalysis(
        room_type=room,
        room_confidence=0.9,
        floor=floor,
        room_ordinal=ordinal,
        components=[outlet] * components,
    )


# --------------------------------------------------------------------------- the name


@pytest.mark.parametrize(
    ("filename", "name"),
    [
        ("2nd Floor Bedroom 2.jpg", "Bedroom 2"),  # the brief's example
        ("29_2nd Floor Bed 2 .JPG", "Bed 2"),
        ("21_1st Floor Kitchen photo 1.JPG", "Kitchen"),
        ("40_11th Floor Lobby.jpg", "Lobby"),
        ("45_Basement Bath.JPG", "Bath"),
        ("51_Basement Stairs to Garage.JPG", "Stairs to Garage"),
        ("52_Basement Access from 1st Floor.JPG", "Access from 1st Floor"),  # only a prefix
        ("1st Floor Hall & Stairs (west).png", "Hall & Stairs (west)"),
        ("2nd Floor - Bedroom 3.jpg", "Bedroom 3"),
        # The floor is the whole name: it stays the name.
        ("46_Basement.JPG", "Basement"),
        ("2nd Floor.jpg", "2nd Floor"),
        # No floor identified: the name as written (the rule reads only "<n>th Floor" and
        # "Basement"; "Second Floor" and underscores between the words are not read).
        ("49_Mechanicals.JPG", "Mechanicals"),
        ("Second Floor Bedroom.jpg", "Second Floor Bedroom"),
        ("1st_Floor_Kitchen.jpg", "1st Floor Kitchen"),
        ("kitchen_1.jpg", "kitchen 1"),
        ("07_photo 2.jpg", "Unnamed"),
    ],
)
def test_the_name_drops_only_a_floor_the_rule_identified(filename: str, name: str) -> None:
    assert room_name(filename) == name


# --------------------------------------------------------------------------- the type


# Room names in the measured package's conventions (EVAL_REPORT 48; the same names as
# test_room_grouping's synthetic package). The vocabulary was written knowing them, so they
# are the regression, not the evidence.
PACKAGE_NAMES = {
    "Ingress-Egress": RoomType.EXTERIOR,
    "Signage": RoomType.EXTERIOR,
    "Address": RoomType.EXTERIOR,
    "Property Front": RoomType.EXTERIOR,
    "Property Left Side": RoomType.EXTERIOR,
    "Parking Lot": RoomType.EXTERIOR,
    "AC Condenser": RoomType.EXTERIOR,
    "Electrical Meter": RoomType.EXTERIOR,
    "Gas Meter": RoomType.EXTERIOR,
    "Shed": RoomType.EXTERIOR,
    "Front Entrance": RoomType.HALLWAY,
    "Breakfast Nook": RoomType.DINING_ROOM,
    "Dining": RoomType.DINING_ROOM,
    "Bath": RoomType.BATHROOM,
    "Bed": RoomType.BEDROOM,
    "Garage": RoomType.GARAGE,
    "Hall": RoomType.HALLWAY,
    "Kitchen": RoomType.KITCHEN,
    "Laundry": RoomType.LAUNDRY,
    "Living": RoomType.LIVING_ROOM,
    "Office": RoomType.OFFICE,
    "Bed 1 Bath": RoomType.BATHROOM,  # the head comes last
    "Bed 2 Seating Area": RoomType.BEDROOM,  # "seating area" is no room type
    "Bed 2 Tub area": RoomType.BATHROOM,
    "Bed 3 Shower": RoomType.BATHROOM,
    "Extra Area Mechanicals": RoomType.MECHANICAL,
    "Extra Area Access Stairs": RoomType.STAIRWAY,
    "Mechanicals": RoomType.MECHANICAL,
    "Stairs to Garage": RoomType.STAIRWAY,  # a locator ends the room words
}
# Common listing and inspection names outside that package (written with the vocabulary,
# not drawn from a second client package: EVAL_REPORT 48.4 says what that leaves open).
OTHER_NAMES = {
    "Master Bedroom": RoomType.BEDROOM,
    "Primary Bath 2": RoomType.BATHROOM,
    "Half Bath": RoomType.BATHROOM,
    "Powder Room": RoomType.BATHROOM,
    "Hall Bath": RoomType.BATHROOM,
    "Great Room": RoomType.LIVING_ROOM,
    "Family Room Fireplace": RoomType.FAMILY_ROOM,
    "Den": RoomType.FAMILY_ROOM,
    "Foyer": RoomType.HALLWAY,
    "Mud Room": RoomType.HALLWAY,
    "Walk-in Closet": RoomType.CLOSET,
    "Closet off Bedroom 2": RoomType.CLOSET,
    "Unfinished Basement": RoomType.BASEMENT,
    "Attic": RoomType.ATTIC,
    "Furnace Room": RoomType.MECHANICAL,
    "Utility Room": RoomType.MECHANICAL,
    "Water Heater": RoomType.MECHANICAL,
    "Back Yard": RoomType.EXTERIOR,
    "Front Porch": RoomType.EXTERIOR,
    "Rear Elevation": RoomType.EXTERIOR,
    "Parking Garage Level 2": RoomType.PARKING,  # the phrase before its words
    "Lobby Restroom": RoomType.BATHROOM,
    "Reception": RoomType.LOBBY,
    "Unit 2 Kitchen": RoomType.KITCHEN,
    "Kitchen to Dining": RoomType.KITCHEN,
    "Garage Door": RoomType.GARAGE,
    "Laundry Room": RoomType.LAUNDRY,
    "Study": RoomType.OFFICE,
    "Storage Room": RoomType.STORAGE,
    "Stairwell B": RoomType.STAIRWAY,
}
SAYS_NOTHING = (
    "Extra Area Seating Area",
    "Access from 1st Floor",
    "Unnamed",
    "IMG 1234",
    "Suite",
    "Electrical Panel",
    "2nd Floor",
    "",
)


@pytest.mark.parametrize(("name", "room_type"), [*PACKAGE_NAMES.items(), *OTHER_NAMES.items()])
def test_the_name_says_the_type(name: str, room_type: RoomType) -> None:
    assert name_room_type(name) is room_type


@pytest.mark.parametrize("name", SAYS_NOTHING)
def test_a_name_without_a_room_word_says_no_type(name: str) -> None:
    assert name_room_type(name) is None


def test_case_numbers_and_punctuation_do_not_change_the_type() -> None:
    for name in ("BED 2", "bed-2", "Bed #2", "  bed   2 "):
        assert name_room_type(name) is RoomType.BEDROOM


# --------------------------------------------------------------------------- the three fields


def test_each_field_has_its_own_source() -> None:
    meta = resolve_room(
        "Bed 2", ["29_2nd Floor Bed 2 .JPG"], [RoomAnswer(RoomType.STORAGE, floor=1)]
    )
    assert (meta.name, meta.room_type, meta.floor) == ("Bed 2", RoomType.BEDROOM, 2)
    assert meta.sources == {"name": "file_name", "room_type": "file_name", "floor": "file_name"}
    meta = resolve_room(
        "Extra Area Seating Area",
        ["42_2nd Floor Extra Area Seating Area.JPG"],
        [RoomAnswer(RoomType.LIVING_ROOM)],
    )
    assert (meta.room_type, meta.sources["room_type"]) == (RoomType.LIVING_ROOM, "model")


def test_basement_zero_is_kept_and_is_not_unknown() -> None:
    meta = resolve_room("Bath", ["45_Basement Bath.JPG"], [RoomAnswer(RoomType.BATHROOM, 1)])
    assert meta.floor == 0 and meta.sources["floor"] == "file_name"  # the file beats the model
    # A model's 0 counts as an answer (not falsy), and a basement by name is floor 0.
    assert (
        resolve_room("Mechanicals", ["Mechanicals.jpg"], [RoomAnswer(RoomType.OTHER, 0)]).floor == 0
    )
    meta = resolve_room("Unfinished Basement", ["Unfinished Basement.jpg"], [])
    assert (meta.room_type, meta.floor) == (RoomType.BASEMENT, 0)


def test_an_unknown_floor_stays_unknown() -> None:
    meta = resolve_room("Mechanicals", ["49_Mechanicals.JPG"], [RoomAnswer(RoomType.OTHER)])
    assert (meta.floor, meta.sources["floor"]) == (None, "none")
    meta = resolve_room("Suite", ["Suite.jpg"], [])
    assert (meta.room_type, meta.floor, meta.sources) == (
        RoomType.OTHER,
        None,
        {"name": "file_name", "room_type": "none", "floor": "none"},
    )


def test_the_grounds_take_no_floor_from_the_model_but_keep_a_file_floor() -> None:
    answers = [RoomAnswer(RoomType.EXTERIOR, 1)]
    assert resolve_room("Property Front", ["04_Property Front.JPG"], answers).floor is None
    assert resolve_room("Parking Lot", ["08_Parking Lot.JPG"], answers).floor is None
    # The model's exterior answer for a name that says nothing: no floor either.
    assert resolve_room("Grounds 2", ["Grounds 2.jpg"], answers).floor is None
    # An engineer's floor in the file name stands ("1st Floor Patio").
    assert resolve_room("Patio", ["1st Floor Patio.jpg"], answers).floor == 1


def test_the_model_votes_when_the_name_says_nothing() -> None:
    def vote(*answers: RoomAnswer) -> tuple[RoomType, int | None]:
        meta = resolve_room("Suite", ["Suite photo 1.jpg"], list(answers))
        return meta.room_type, meta.floor

    assert vote(RoomAnswer(RoomType.AMBIGUOUS), RoomAnswer(RoomType.OFFICE, 2)) == (
        RoomType.OFFICE,
        2,
    )
    assert vote(RoomAnswer(RoomType.AMBIGUOUS)) == (RoomType.AMBIGUOUS, None)
    assert vote(RoomAnswer(RoomType.BEDROOM, 2), RoomAnswer(RoomType.OFFICE, 3)) == (
        RoomType.BEDROOM,
        2,
    )  # ties: the first answer


# --------------------------------------------------------------------------- the grouping

PACKAGE = (
    "13_Shed.JPG",
    "17_1st Floor Bath.JPG",
    "18_1st Floor Bed.JPG",
    "21_1st Floor Kitchen photo 1.JPG",
    "22_1st Floor Kitchen photo 2.JPG",
    "26_2nd Floor Bed 1 Bath.JPG",
    "27_2nd Floor Bed 1.JPG",
    "29_2nd Floor Bed 2 .JPG",
    "35_2nd Floor Bed 3 .JPG",
    "45_Basement Bath.JPG",
    "46_Basement Bed 1.JPG",
    "49_Mechanicals.JPG",
    "50_Mechanicals.JPG",
)


def package(answer: dict[str, PhotoAnalysis | None] | None = None) -> list[PhotoInput]:
    given = answer or {}
    return [PhotoInput(f, given.get(f, analysis(RoomType.AMBIGUOUS))) for f in PACKAGE]


def test_multiple_floors_and_duplicate_types_stay_apart() -> None:
    rooms = {r.key: r for r in initial_rooms(package())}
    # Every file-name room stays its own room: same type and same name are no reason to merge.
    assert len(rooms) == 11
    seen = {(r.name, r.room_type, r.floor) for r in rooms.values()}
    assert {
        ("Bath", "bathroom", "1"),
        ("Bath", "bathroom", "0"),
        ("Bed", "bedroom", "1"),
        ("Bed 1", "bedroom", "2"),
        ("Bed 2", "bedroom", "2"),
        ("Bed 3", "bedroom", "2"),
        ("Bed 1", "bedroom", "0"),
        ("Bed 1 Bath", "bathroom", "2"),
        ("Kitchen", "kitchen", "1"),
        ("Shed", "exterior", None),
        ("Mechanicals", "mechanical", None),
    } == seen
    assert rooms["1_kitchen"].photos == (
        "21_1st Floor Kitchen photo 1.JPG",
        "22_1st Floor Kitchen photo 2.JPG",
    )
    assert not any(r.unassigned for r in rooms.values())


def test_aliases_merge_only_what_they_name() -> None:
    rooms = initial_rooms(package(), {"2_bed_1_bath": "2_bed_1"})
    bed = next(r for r in rooms if r.key == "2_bed_1")
    assert (bed.name, bed.room_type, bed.floor, len(bed.photos)) == ("Bed 1", "bedroom", "2", 2)
    assert len(rooms) == 10


def test_every_analyzed_photo_votes_even_with_no_component() -> None:
    files = ("Suite photo 1.jpg", "Suite photo 2.jpg", "Suite photo 3.jpg")
    answers = (
        analysis(RoomType.AMBIGUOUS, components=3),
        analysis(RoomType.OFFICE, 3, components=0),
        analysis(RoomType.OFFICE, 3, components=0),
    )
    [room] = initial_rooms([PhotoInput(f, a) for f, a in zip(files, answers, strict=True)])
    assert (room.room_type, room.floor) == ("office", "3")


def test_a_failed_analysis_leaves_the_photo_without_a_room() -> None:
    rooms = initial_rooms(package({"18_1st Floor Bed.JPG": None}))
    placed = {f for r in rooms for f in r.photos}
    assert "18_1st Floor Bed.JPG" not in placed and len(placed) == len(PACKAGE) - 1
    assert "1_bed" not in {r.key for r in rooms}


def test_camera_names_group_by_the_model_and_an_ambiguous_photo_is_unassigned() -> None:
    photos = [
        PhotoInput("IMG_0001.JPG", analysis(RoomType.BEDROOM, 2)),
        PhotoInput("IMG_0002.JPG", analysis(RoomType.BEDROOM, 2, ordinal=2)),
        PhotoInput("IMG_0003.JPG", analysis(RoomType.BEDROOM, 2)),
        PhotoInput("IMG_0004.JPG", analysis(RoomType.AMBIGUOUS)),
        PhotoInput("IMG_0005.JPG", analysis(RoomType.BATHROOM, 0)),
    ]
    rooms = initial_rooms(photos)
    by_name = {(r.name, r.floor): r for r in rooms}
    assert by_name[("Bedroom", "2")].photos == ("IMG_0001.JPG", "IMG_0003.JPG")
    assert by_name[("Bedroom 2", "2")].photos == ("IMG_0002.JPG",)  # the ordinal is kept
    assert by_name[("Bathroom", "0")].room_type == "bathroom"
    [tray] = [r for r in rooms if r.unassigned]
    assert (tray.name, tray.room_type, tray.photos) == (
        "Unassigned",
        "ambiguous",
        ("IMG_0004.JPG",),
    )
    # The same photos with ROOM_GROUPING=model: names are ignored.
    named = [PhotoInput("1st Floor Kitchen.jpg", analysis(RoomType.DINING_ROOM, 1))]
    [room] = initial_rooms(named, by_name=False)
    assert (room.key, room.name, room.room_type) == (None, "Dining Room", "dining_room")


def test_initial_rooms_agree_with_group_photos_on_named_packages() -> None:
    """The replay functions (group_photos, used by evals) and the production grouping give the
    same name, type and floor for every file-name room."""
    photos = package({"21_1st Floor Kitchen photo 1.JPG": analysis(RoomType.DINING_ROOM, 1)})
    groups = {g.room_id: g for g in group_photos(photos)}
    for room in initial_rooms(photos):
        group = groups[room.key or ""]
        floor = None if group.floor is None else str(group.floor)
        assert (room.name, room.room_type, room.floor) == (
            group.name,
            group.room_type.value,
            floor,
        )


# --------------------------------------------------------------------------- corrections


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (None, None),
        (0, "0"),
        (-1, "-1"),
        (2, "2"),
        ("2", "2"),
        (" 0 ", "0"),
        ("Mezzanine", "Mezzanine"),
    ],
)
def test_a_corrected_floor_is_stored_as_text(value: object, stored: str | None) -> None:
    assert stored_floor(value) == stored


@pytest.mark.parametrize("value", [True, False, 2.0, "", "  ", 201, -4, ["1"], {"n": 1}])
def test_a_floor_that_cannot_be_stored_is_refused(value: object) -> None:
    with pytest.raises(ValueError, match="floor"):
        stored_floor(value)
    assert room_field_problems({"floor": value})


def test_room_field_problems_name_each_bad_field() -> None:
    assert room_field_problems({"name": "Guest Bedroom", "room_type": "bedroom", "floor": 0}) == []
    assert room_field_problems({"merge_into": "x"}) == []  # checked by apply_corrections
    assert len(room_field_problems({"name": "", "room_type": "x" * 65, "floor": True})) == 3
