"""A byte-stable snapshot of everything the takeoff derives from a set of extraction fields.

``tests/fixtures/sketch/measurements_before_sketch.json`` holds this snapshot as
the code produced it before the appraisal sketch was read (main at e885ca1, EVAL_REPORT 96);
``test_measurements_sketch.py`` compares today's output to it string for string. The cases
are fabricated (no client data). Regenerate only for a change that is meant to move the
takeoff of a study with no sketch::

    uv run python -m tests.unit.domain.measurement_snapshot --write
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from basis.domain.measurements import (
    BuildingMeasurements,
    Derived,
    garage_area,
    measurements_from_fields,
    property_missing,
    property_quantities,
    room_areas,
)

GOLDEN = (
    Path(__file__).resolve().parents[2] / "fixtures" / "sketch" / "measurements_before_sketch.json"
)


@dataclass
class Row:
    section: str
    field: str
    value_num: Decimal | None = None
    value_text: str | None = None
    value_date: date | None = None
    source: str = "azure"
    page: int | None = None


@dataclass
class RoomStub:
    id: str
    room_type: str
    floor: str | None = None


def num(key: str, value: str, source: str = "azure", page: int | None = None) -> Row:
    section, field = key.split(".", 1)
    return Row(section, field, Decimal(value), source=source, page=page)


TWO_STORY = [
    num("improvements.gla_sqft", "2400", "llm"),
    num("improvements.stories", "2", "llm"),
    num("cost_approach.garage_sqft", "484"),
    num("improvements.garage_cars", "2"),
    num("improvements.basement_area_sqft", "1200"),
    num("improvements.basement_finish_percent", "50"),
    num("site.area_sqft", "9000"),
    num("improvements.total_rooms", "8"),
    num("improvements.bedrooms", "4"),
    num("improvements.bathrooms", "2.1"),
]
CASES: dict[str, list[Row]] = {
    "two_story_with_garage": TWO_STORY,
    "one_story_no_garage": [
        num("improvements.gla_sqft", "1500"),
        num("improvements.stories", "1"),
        num("site.area_acres", "0.25"),
        num("improvements.total_rooms", "6"),
        num("improvements.bedrooms", "3"),
        num("improvements.bathrooms", "2"),
    ],
    "half_story_garage_by_cars": [
        num("improvements.gla_sqft", "1800"),
        num("improvements.stories", "1.5"),
        num("improvements.garage_cars", "1"),
        num("site.area_sqft", "7000"),
    ],
    "no_stories": [num("improvements.gla_sqft", "2000"), num("site.area_sqft", "8000")],
    "no_gla": [num("improvements.stories", "2"), num("cost_approach.garage_sqft", "400")],
}
ROOMS = [
    RoomStub("kitchen", "kitchen", "1"),
    RoomStub("bed-1", "bedroom", "2"),
    RoomStub("bath-1", "bathroom", "2"),
    RoomStub("garage", "garage", "1"),
    RoomStub("basement", "basement", "0"),
    RoomStub("yard", "exterior", None),
    RoomStub("patio", "exterior", None),
]


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _derived(value: Derived | None) -> Any:
    return None if value is None else _plain(asdict(value))


def snapshot_of(m: BuildingMeasurements) -> dict[str, Any]:
    """Every derived value of ``m`` with its formula, inputs (in order) and assumptions."""
    return {
        "garage": _derived(garage_area(m)),
        "property": {k: _derived(v) for k, v in property_quantities(m).items()},
        "property_missing": property_missing(m),
        "rooms": {
            str(room_id): _plain(asdict(area))
            for room_id, area in room_areas(m, ROOMS, "residential_rental").items()
        },
    }


def render(snapshots: dict[str, Any]) -> str:
    return json.dumps(snapshots, indent=1) + "\n"


def today() -> str:
    return render(
        {name: snapshot_of(measurements_from_fields(rows)) for name, rows in CASES.items()}
    )


if __name__ == "__main__":  # pragma: no cover
    if "--write" in sys.argv:
        GOLDEN.write_text(today(), encoding="utf-8")
        print(f"wrote {GOLDEN}")
    else:
        print("equal" if GOLDEN.read_text(encoding="utf-8") == today() else "DIFFERENT")
