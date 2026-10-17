"""Room grouping from file names plus the per-photo model (``basis.vision.rooms``).

The file-name rule is the one the labeller applies by hand to build the
``room_inventory`` dataset, so the examples here are the rule's specification.
"""

from __future__ import annotations

import pytest
from llm_fakes import photo_labels

from basis.vision.rooms import (
    AliasCycleError,
    NameHint,
    PhotoInput,
    display_name,
    group_photos,
    is_room_hint,
    merge_alias,
    name_group,
    name_hint,
    resolve_aliases,
    vote_identity,
)
from basis.vision.schemas import PhotoAnalysis, RoomType


@pytest.mark.parametrize(
    ("filename", "floor", "token", "slug", "room_id"),
    [
        ("21_1st Floor Kitchen photo 1.JPG", 1, "1", "kitchen", "1_kitchen"),
        ("22_1st Floor Kitchen photo 2.JPG", 1, "1", "kitchen", "1_kitchen"),
        ("29_2nd Floor Bed 2 .JPG", 2, "2", "bed_2", "2_bed_2"),
        (
            "30_2nd Floor Bed 2 Seating Area.JPG",
            2,
            "2",
            "bed_2_seating_area",
            "2_bed_2_seating_area",
        ),
        ("33_3rd Floor Office.jpg", 3, "3", "office", "3_office"),
        ("40_11th Floor Lobby.jpg", 11, "11", "lobby", "11_lobby"),
        ("45_Basement Bath.JPG", 0, "b", "bath", "b_bath"),
        ("04_Property Front.JPG", None, "x", "property_front", "x_property_front"),
        ("49_Mechanicals.JPG", None, "x", "mechanicals", "x_mechanicals"),
        ("50_Mechanicals.JPG", None, "x", "mechanicals", "x_mechanicals"),
        ("kitchen_1.jpg", None, "x", "kitchen_1", "x_kitchen_1"),
        ("photos/01_kitchen.jpg", None, "x", "kitchen", "x_kitchen"),
        ("12_Living Room PHOTO 3.jpeg", None, "x", "living_room", "x_living_room"),
        ("46_Basement.JPG", 0, "b", "unnamed", "b_unnamed"),
        ("07_photo 2.jpg", None, "x", "unnamed", "x_unnamed"),
        ("1st Floor Hall & Stairs (west).png", 1, "1", "hall_stairs_west", "1_hall_stairs_west"),
    ],
)
def test_name_hint_rule(
    filename: str, floor: int | None, token: str, slug: str, room_id: str
) -> None:
    assert name_hint(filename) == NameHint(floor, token, slug, room_id)


def test_name_hint_edges() -> None:
    # Only a leading index is dropped, and only once; "photo" must end the name.
    assert name_hint("01_02_Kitchen.jpg").room_id == "x_02_kitchen"
    assert name_hint("Photographs Wall.jpg").room_id == "x_photographs_wall"
    # "Floor" needs the ordinal form; "Basement" must be a whole word.
    assert name_hint("2 Floor Bath.jpg").room_id == "x_2_floor_bath"
    assert name_hint("Basements Storage.jpg").room_id == "x_basements_storage"
    # No extension; the floor words in lower case (matched case-insensitively).
    assert name_hint("Garage").room_id == "x_garage"
    assert name_hint("1st floor kitchen.jpg").room_id == "1_kitchen"


def test_display_name_is_the_readable_remainder() -> None:
    assert display_name("21_1st Floor Kitchen photo 1.JPG") == "1st Floor Kitchen"
    assert display_name("29_2nd Floor Bed 2 .JPG") == "2nd Floor Bed 2"
    assert display_name("kitchen_1.jpg") == "kitchen 1"
    assert display_name("07_photo 2.jpg") == "Unnamed"


def analysis(room: RoomType, floor: int | None = None) -> PhotoAnalysis:
    return PhotoAnalysis(room_type=room, room_confidence=0.9, floor=floor)


PACKAGE = [
    PhotoInput("21_1st Floor Kitchen photo 1.JPG", analysis(RoomType.KITCHEN)),
    PhotoInput("22_1st Floor Kitchen photo 2.JPG", analysis(RoomType.DINING_ROOM)),
    PhotoInput("29_2nd Floor Bed 2 .JPG", analysis(RoomType.BEDROOM)),
    PhotoInput("30_2nd Floor Bed 2 Seating Area.JPG", analysis(RoomType.LIVING_ROOM)),
    PhotoInput("45_Basement Bath.JPG", analysis(RoomType.BATHROOM)),
    PhotoInput("04_Property Front.JPG", analysis(RoomType.EXTERIOR)),
    PhotoInput("49_Mechanicals.JPG", analysis(RoomType.MECHANICAL, floor=0)),
    PhotoInput("50_Mechanicals.JPG", analysis(RoomType.MECHANICAL)),
]


def test_groups_by_room_id_in_floor_then_slug_order() -> None:
    groups = group_photos(PACKAGE)
    assert [g.room_id for g in groups] == [
        "b_bath",
        "1_kitchen",
        "2_bed_2",
        "2_bed_2_seating_area",
        "x_mechanicals",
        "x_property_front",
    ]
    kitchen = groups[1]
    assert kitchen.name == "1st Floor Kitchen" and kitchen.floor == 1
    assert [p.file for p in kitchen.photos] == [
        "21_1st Floor Kitchen photo 1.JPG",
        "22_1st Floor Kitchen photo 2.JPG",
    ]
    # Tie (kitchen 1, dining 1) -> the first photo's answer.
    assert kitchen.room_type is RoomType.KITCHEN
    assert groups[0].floor == 0
    # No floor in the name -> the model's floor.
    mechanicals = groups[4]
    assert (mechanicals.floor, mechanicals.room_type, len(mechanicals.photos)) == (
        0,
        RoomType.MECHANICAL,
        2,
    )
    assert groups[5].floor is None


def test_ordering_is_numeric_and_independent_of_input_order() -> None:
    files = ["10th Floor A.jpg", "2nd Floor A.jpg", "Basement A.jpg", "Zed.jpg", "1st Floor B.jpg"]
    expected = ["b_a", "1_b", "2_a", "10_a", "x_zed"]
    assert [g.room_id for g in group_photos([PhotoInput(f) for f in files])] == expected
    assert [g.room_id for g in group_photos([PhotoInput(f) for f in reversed(files)])] == expected


def test_aliases_merge_a_view_into_its_room() -> None:
    groups = group_photos(PACKAGE, {"2_bed_2_seating_area": "2_bed_2"})
    bed = next(g for g in groups if g.room_id == "2_bed_2")
    assert "2_bed_2_seating_area" not in {g.room_id for g in groups}
    assert [p.file for p in bed.photos] == [
        "29_2nd Floor Bed 2 .JPG",
        "30_2nd Floor Bed 2 Seating Area.JPG",
    ]
    # The canonical room keeps its own name; tie bedroom/living -> first photo.
    assert bed.name == "2nd Floor Bed 2" and bed.room_type is RoomType.BEDROOM


def test_aliases_are_transitive_and_may_name_a_room_with_no_photo_of_its_own() -> None:
    aliases = {"2_bed_2_seating_area": "2_bed_2", "2_bed_2": "2_primary_suite"}
    assert resolve_aliases(aliases) == {
        "2_bed_2_seating_area": "2_primary_suite",
        "2_bed_2": "2_primary_suite",
    }
    groups = group_photos(PACKAGE, aliases)
    suite = next(g for g in groups if g.room_id == "2_primary_suite")
    assert len(suite.photos) == 2 and suite.name == "2nd Floor Bed 2"
    assert suite.floor == 2


@pytest.mark.parametrize(
    "aliases",
    [
        {"a": "a"},
        {"a": "b", "b": "a"},
        {"a": "b", "b": "c", "c": "a"},
        {"x": "a", "a": "b", "b": "a"},
    ],
)
def test_alias_cycles_are_refused(aliases: dict[str, str]) -> None:
    with pytest.raises(AliasCycleError, match="loop"):
        resolve_aliases(aliases)
    with pytest.raises(AliasCycleError):
        group_photos(PACKAGE, aliases)


def test_room_type_is_the_majority_and_ambiguous_does_not_vote() -> None:
    def room(*types: RoomType | None) -> RoomType:
        photos = [
            PhotoInput(f"1st Floor Den photo {i}.jpg", None if t is None else analysis(t))
            for i, t in enumerate(types, start=1)
        ]
        [group] = group_photos(photos)
        return group.room_type

    assert room(RoomType.OTHER, RoomType.FAMILY_ROOM, RoomType.FAMILY_ROOM) is RoomType.FAMILY_ROOM
    assert room(RoomType.AMBIGUOUS, RoomType.AMBIGUOUS, RoomType.OFFICE) is RoomType.OFFICE
    assert room(RoomType.AMBIGUOUS, RoomType.AMBIGUOUS) is RoomType.AMBIGUOUS
    assert room(None, RoomType.OFFICE, RoomType.BEDROOM) is RoomType.OFFICE  # tie, first answer
    assert room(None, None) is RoomType.OTHER  # no analyses


def test_model_floor_is_the_most_common_answer() -> None:
    photos = [
        PhotoInput("Garage photo 1.jpg", analysis(RoomType.GARAGE, 1)),
        PhotoInput("Garage photo 2.jpg", analysis(RoomType.GARAGE, 0)),
        PhotoInput("Garage photo 3.jpg", analysis(RoomType.GARAGE, 0)),
    ]
    assert group_photos(photos)[0].floor == 0


def test_synthetic_fixture_photos_are_each_their_own_room() -> None:
    files = sorted(photo_labels())
    groups = group_photos([PhotoInput(f) for f in files])
    assert len(groups) == len(files) == 8
    assert {g.room_id for g in groups} == {
        "x_kitchen",
        "x_bathroom",
        "x_bedroom",
        "x_living_room",
        "x_garage",
        "x_exterior_front",
        "x_ac_condenser",
        "x_ambiguous",
    }
    assert all(g.floor is None and g.room_type is RoomType.OTHER for g in groups)


def test_empty_input_gives_no_rooms() -> None:
    assert group_photos([]) == []


# --------------------------------------------------------------------------- production grouping
# (EVAL_REPORT section 27, item 1: analyze_photo groups by these functions.)

# A package named like the client's (synthetic names, same conventions).
NAMED_PACKAGE = (
    "01_Ingress-Egress.JPG",
    "04_Property Front.JPG",
    "05_Property Front.JPG",
    "14_1st Floor Front Entrance.JPG",
    "15_1st Floor Breakfast Nook.JPG",
    "21_1st Floor Kitchen photo 1.JPG",
    "22_1st Floor Kitchen photo 2.JPG",
    "29_2nd Floor Bed 2 .JPG",
    "30_2nd Floor Bed 2 Seating Area.JPG",
    "31_2nd Floor Bed 3.JPG",
    "32_2nd Floor Bed 3 Shower.JPG",
    "33_2nd Floor Bed 3 Bath.JPG",
    "45_Basement Bath.JPG",
    "46_Basement.JPG",
    "49_Mechanicals.JPG",
)
PACKAGE_ALIASES = {
    "1_breakfast_nook": "1_kitchen",
    "2_bed_2_seating_area": "2_bed_2",
    "2_bed_3_shower": "2_bed_3_bath",
}


@pytest.mark.parametrize(
    "filename",
    [
        "21_1st Floor Kitchen photo 1.JPG",
        "49_Mechanicals.JPG",
        "46_Basement.JPG",
        "04_Property Front.JPG",
        "kitchen_1.jpg",
        "Kitchen photo 2.jpg",
        "Bed 2.heic",
        "photos/2nd floor hall.png",
        "IMG_1234 kitchen.JPG",
    ],
)
def test_names_that_name_a_room_are_hints(filename: str) -> None:
    assert is_room_hint(filename)


@pytest.mark.parametrize(
    "filename",
    [
        "IMG_1234.JPG",
        "IMG_20250129_100211.jpg",
        "DSC01234.JPG",
        "DSCN0042.jpg",
        "PXL_20250101_120000123.jpg",
        "MVIMG_20250101_120000.jpg",
        "WhatsApp Image 2025-01-29 at 10.02.11.jpeg",
        "Screenshot 2025-01-29 at 10.02.11 AM.png",
        "3f2a9c1e-7b4d-4c1a-9e2f-0a1b2c3d4e5f.jpg",
        "07_photo 2.jpg",
        "image (3).png",
        "IMG_1234 copy.JPG",
        "1234.jpg",
        "",
    ],
)
def test_camera_and_app_names_are_not_hints(filename: str) -> None:
    assert not is_room_hint(filename)


def test_name_group_is_group_photos_for_every_photo() -> None:
    """The production assignment (one photo at a time, from the file names alone) puts
    every photo in the room group_photos puts it in, with the same id, name and floor."""
    expected = {
        p.file: g
        for g in group_photos([PhotoInput(f) for f in sorted(NAMED_PACKAGE)], PACKAGE_ALIASES)
        for p in g.photos
    }
    for filename in NAMED_PACKAGE:
        group = name_group(filename, NAMED_PACKAGE, PACKAGE_ALIASES)
        want = expected[filename]
        assert (group.room_id, group.name, group.floor) == (want.room_id, want.name, want.floor)
    assert name_group("15_1st Floor Breakfast Nook.JPG", NAMED_PACKAGE, PACKAGE_ALIASES).name == (
        "1st Floor Kitchen"
    )
    assert (
        name_group("32_2nd Floor Bed 3 Shower.JPG", NAMED_PACKAGE, PACKAGE_ALIASES).room_id
        == "2_bed_3_bath"
    )
    # Without aliases each view is its own room.
    assert (
        name_group("15_1st Floor Breakfast Nook.JPG", NAMED_PACKAGE).room_id == "1_breakfast_nook"
    )


def test_name_group_order_does_not_depend_on_the_input_order() -> None:
    forward = name_group("22_1st Floor Kitchen photo 2.JPG", NAMED_PACKAGE, PACKAGE_ALIASES)
    backward = name_group("22_1st Floor Kitchen photo 2.JPG", NAMED_PACKAGE[::-1], PACKAGE_ALIASES)
    assert forward == backward
    # A file not in the list is grouped as if it were.
    alone = name_group("23_1st Floor Kitchen photo 3.JPG", NAMED_PACKAGE, PACKAGE_ALIASES)
    assert (alone.room_id, alone.name) == ("1_kitchen", "1st Floor Kitchen")


def test_vote_identity_follows_group_photos() -> None:
    assert vote_identity(["kitchen", "kitchen", "bathroom"], [1, None, 2]) == (
        RoomType.KITCHEN,
        1,
    )
    # ambiguous abstains; ties go to the earliest; unknown strings vote other.
    assert vote_identity(["ambiguous", "bedroom", "office"], [None, 2, 2]) == (
        RoomType.BEDROOM,
        2,
    )
    assert vote_identity(["ambiguous"], [None]) == (RoomType.AMBIGUOUS, None)
    assert vote_identity(["not-a-type"], [3]) == (RoomType.OTHER, 3)
    assert vote_identity([], []) == (RoomType.OTHER, None)


def test_merge_alias() -> None:
    merged = merge_alias({"a": "b"}, "b", "c")
    assert resolve_aliases(merged) == {"a": "c", "b": "c"}
    # The target becomes canonical: its own alias is dropped, so nothing loops.
    assert merge_alias({"c": "b"}, "b", "c") == {"b": "c"}
    assert merge_alias({}, "x_a", "x_b") == {"x_a": "x_b"}
    with pytest.raises(AliasCycleError):
        merge_alias({}, "a", "a")
