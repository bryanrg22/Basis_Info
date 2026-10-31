"""``evals.room_metadata``: the scorer of the rooms the Rooms review opens on (synthetic
labels; the client's are private)."""

from __future__ import annotations

import json
from pathlib import Path

from basis.vision.rooms import InitialRoom
from basis.vision.schemas import PhotoAnalysis, RoomType
from evals.room_metadata import engineer_name, load_answers, run, score

FILES = ("01_Shed.JPG", "29_2nd Floor Bed 2 .JPG", "30_2nd Floor Bed 2 Seating Area.JPG")


def labels(tmp_path: Path) -> Path:
    rooms = {
        "rooms": [
            {"room_id": "x_shed", "name": "Shed", "floor": None, "room_type": "exterior"},
            {"room_id": "2_bed_2", "name": "2nd Floor Bed 2", "floor": 2, "room_type": "bedroom"},
        ]
    }
    photos = {
        "items": [
            {"file": FILES[0], "engineer_label": "Shed", "room_type": "exterior", "floor": None},
            {
                "file": FILES[1],
                "engineer_label": "2nd Floor Bed 2",
                "room_type": "bedroom",
                "floor": 2,
            },
            {
                "file": FILES[2],
                "engineer_label": "2nd Floor Bed 2 Seating Area",
                "room_type": "bedroom",
                "floor": 2,
            },
        ]
    }
    for name, data in (
        ("room_inventory_v1.json", rooms),
        ("photo_rooms_v1.json", photos),
        ("room_aliases_v1.json", {"2_bed_2_seating_area": "2_bed_2"}),
    ):
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_engineer_name_drops_the_photo_suffix_and_a_leading_floor() -> None:
    assert engineer_name("1st Floor Kitchen photo 1") == "Kitchen"
    assert engineer_name("Basement Bed 2") == "Bed 2"
    assert engineer_name("Basement") == "Basement"  # nothing would be left
    assert engineer_name("Mechanicals") == "Mechanicals"


def test_score_counts_each_field_and_the_unassigned(tmp_path: Path) -> None:
    from evals.room_metadata import load_labels

    by_room, by_photo, _ = load_labels(labels(tmp_path))
    rooms = [
        InitialRoom("x_shed", "Shed", "exterior", "1", (FILES[0],)),  # a guessed floor
        InitialRoom("2_bed_2", "2nd Floor Bed 2", "bedroom", "2", (FILES[1],)),  # old name
    ]
    result = score(rooms, list(FILES), by_room, by_photo)
    assert (result["rooms"], result["name"], result["room_type"], result["floor"]) == (2, 1, 2, 1)
    assert result["unassigned"] == [FILES[2]] and result["photos_unassigned"] == 1
    assert {m["key"]: m["wrong"] for m in result["misses"]} == {
        "x_shed": ["floor"],
        "2_bed_2": ["name"],
    }


def test_run_scores_the_production_grouping_in_every_scenario(tmp_path: Path) -> None:
    answer = PhotoAnalysis(room_type=RoomType.AMBIGUOUS, room_confidence=0.5, floor=1)
    lines = [
        json.dumps({"file": f, "repeat": 1, "analysis": answer.model_dump(mode="json")})
        for f in FILES
    ]
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "photo_raw.jsonl").write_text("\n".join(lines), encoding="utf-8")
    _, summary = run(load_answers(tmp_path / "run"), labels(tmp_path))
    [named] = summary["filenames"]
    assert (named["rooms"], named["all_three"], named["photos_unassigned"]) == (3, 3, 0)
    [merged] = summary["aliases"]
    assert (merged["rooms"], merged["all_three"]) == (2, 2)
    [camera] = summary["camera"]
    # IMG names: the model's identity; every answer ambiguous, so every photo is unplaced.
    assert (camera["rooms"], camera["photos_unassigned"]) == (0, 3)
