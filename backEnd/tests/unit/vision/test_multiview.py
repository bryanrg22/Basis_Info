"""``basis.vision.multiview``: many photos in one call (EVAL_REPORT section 26)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from basis.llm.prompts import Prompt
from basis.vision import multiview as mv
from basis.vision.prep import prepare_image
from basis.vision.reconcile import ModelRoomInventory
from basis.vision.schemas import ComponentLabel


def _image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (96, 72), (120, 130, 140)).save(buffer, format="JPEG")
    return buffer.getvalue()


IMAGE = prepare_image(_image())
PROMPT = Prompt(name="house_inventory", version="v2", text="system text", sha256="0" * 64)


def photos(*rooms: str) -> list[mv.Photo]:
    return [mv.Photo(f"{i:02d}_{room}.jpg", room, IMAGE) for i, room in enumerate(rooms, 1)]


def settings(prompt: Prompt = PROMPT) -> mv.CallSettings:
    return mv.CallSettings(
        model="claude-opus-5-5", prompt=prompt, study_id="s", activity="a", max_tokens=100
    )


def obj(label: str, count: int, indices: list[int], material: str = "unknown") -> dict[str, object]:
    return {
        "label": label,
        "count": count,
        "image_indices": indices,
        "material": material,
        "attachment": "permanent",
        "confidence": 0.9,
        "note": None,
    }


def test_house_request_captions_every_image_with_its_room_and_lists_the_rooms() -> None:
    request = mv.build_request("house", photos("1_kitchen", "1_kitchen", "1_bath"), settings())
    contents = [m.content for m in request.messages]
    assert contents[:3] == [
        "Image 1 (room 1_kitchen):",
        "Image 2 (room 1_kitchen):",
        "Image 3 (room 1_bath):",
    ]
    assert all(len(m.images) == 1 for m in request.messages[:3])
    assert "1_kitchen: images 1, 2\n1_bath: image 3" in contents[3]
    assert request.response_model is mv.ModelHouseInventory
    assert request.prompt_version == "house_inventory/v2"


def test_room_request_is_uncaptioned_and_the_schedule_needs_a_template() -> None:
    request = mv.build_request("room", photos("1_bath", "1_bath"), settings())
    assert [m.content for m in request.messages[:2]] == ["Image 1:", "Image 2:"]
    assert request.response_model is ModelRoomInventory
    with pytest.raises(ValueError, match="template"):
        mv.build_request("schedule", photos("1_bath"), settings())
    schedule = mv.build_request("schedule", photos("1_bath"), settings(), ["Windows", "Doors"])
    assert "- Windows\n- Doors" in str(schedule.messages[-1].content)
    with pytest.raises(ValueError, match="at least one"):
        mv.build_request("house", [], settings())


def test_check_house_drops_unknown_rooms_and_bad_indices_and_keeps_every_listed_room() -> None:
    parsed = mv.ModelHouseInventory.model_validate(
        {
            "rooms": [
                {
                    "room_id": "1_kitchen",
                    "objects": [obj("window", 2, [1, 2]), obj("sink", 1, [9])],
                },
                {"room_id": "9_attic", "objects": [obj("window", 1, [1])]},
            ]
        }
    )
    checked = mv.check_house(parsed, photos("1_kitchen", "1_kitchen", "1_bath"))
    assert list(checked.rooms) == ["1_kitchen", "1_bath"]
    assert [(o.label, o.count) for o in checked.rooms["1_kitchen"]] == [(ComponentLabel.WINDOW, 2)]
    assert checked.rooms["1_bath"] == []
    reasons = sorted(d["reason"] for d in checked.dropped)
    assert reasons == ["image_indices", "unknown_room"]


def test_check_room_drops_objects_without_valid_images() -> None:
    parsed = ModelRoomInventory.model_validate(
        {"objects": [obj("toilet", 1, [1]), obj("sink", 1, []), obj("tile", 1, [3], "ceramic")]}
    )
    checked = mv.check_room(parsed, "1_bath", images=2)
    assert [o.label.value for o in checked.rooms["1_bath"]] == ["toilet"]
    assert len(checked.dropped) == 2


def test_check_schedule_matches_template_names_loosely_and_maps_images_to_files() -> None:
    template = ["Exterior Façade / Building Skin", "Windows", "Door & Window Guards"]
    rows = [
        {"system": "exterior facade/building skin", "bucket": "39", "quantity": 1,
         "unit": "lump_sum", "image_indices": [2, 2], "rooms": [], "note": None},
        {"system": "Windows", "bucket": "39", "quantity": 4, "unit": "each",
         "image_indices": [1], "rooms": [], "note": None},
        {"system": "Pool", "bucket": "15", "quantity": 1, "unit": "each",
         "image_indices": [1], "rooms": [], "note": None},
        {"system": "Door & Window Guards", "bucket": "5", "quantity": 1, "unit": "each",
         "image_indices": [5], "rooms": [], "note": None},
    ]  # fmt: skip
    parsed = mv.ModelSchedule.model_validate({"rows": rows})
    checked = mv.check_schedule(parsed, photos("x_front", "x_rear"), template)
    assert [r["system"] for r in checked.rows] == ["Exterior Façade / Building Skin", "Windows"]
    assert checked.rows[0]["files"] == ["02_x_rear.jpg"]
    assert sorted(d["reason"] for d in checked.dropped) == ["image_indices", "unknown_system"]


def test_normalize_system_ignores_case_spacing_and_the_cedilla() -> None:
    assert mv.normalize_system("Exterior Façade / Building Skin") == mv.normalize_system(
        "exterior facade/building  skin"
    )
    assert mv.normalize_system("Door & Window Guards") == mv.normalize_system(
        "door and window guards"
    )
    assert mv.normalize_system("Flooring - Carpet") == mv.normalize_system("flooring-carpet")


def test_house_objects_accept_property_wide_image_numbers() -> None:
    parsed = mv.ModelHouseInventory.model_validate(
        {"rooms": [{"room_id": "x", "objects": [obj("window", 1, [45, 52])]}]}
    )
    assert parsed.rooms[0].objects[0].image_indices == [45, 52]
