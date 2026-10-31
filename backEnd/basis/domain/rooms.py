"""Room guidance: IRS context per room type, typical components and typical areas.

Migrated from ``agentic/agents/room_agent.py`` (``ROOM_IRS_GUIDANCE``,
``ROOM_TYPICAL_COMPONENTS``, ``ROOM_AREA_ESTIMATES``). The guidance is context for the
residual classifier and the reviewer, never a classification by itself; the typical area
is the takeoff input when no measured area exists, and the takeoff records that it was an
estimate.

Change from legacy: the partial-match lookup (``key in room or room in key``) is gone; it
matched ``bath`` to ``bathroom`` but also ``hall`` to ``hallway`` and ``office_kitchen`` to
``office``. Room types are normalized and aliased explicitly instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

# ROOM_ALIASES and normalize_room_type live in basis.domain.rules, whose lookup needs the
# room type (importing this module from there would be a cycle); re-exported here so the
# existing imports keep working.
from basis.domain.rules import ROOM_ALIASES as ROOM_ALIASES
from basis.domain.rules import normalize_room_type as normalize_room_type


@dataclass(frozen=True, slots=True)
class RoomGuidance:
    room_type: str
    space_category: str
    typical_section: str
    guidance: str
    asset_classes: tuple[str, ...]
    key_considerations: tuple[str, ...]
    found: bool = True


ROOM_IRS_GUIDANCE: Final[dict[str, RoomGuidance]] = {
    "kitchen": RoomGuidance(
        "kitchen",
        "unit_space",
        "1245",
        "Kitchen improvements often qualify as Section 1245 personal property. "
        "Cabinets, countertops, and appliances are typically 5-7 year property.",
        ("57.0", "00.11"),
        (
            "Built-in appliances vs freestanding affects classification",
            "Custom cabinetry may be Section 1250 if structural",
            "Countertops typically Section 1245, 5-year",
        ),
    ),
    "bathroom": RoomGuidance(
        "bathroom",
        "unit_space",
        "1245",
        "Bathroom fixtures are generally Section 1245 personal property. "
        "Vanities, toilets, and specialized finishes qualify for accelerated depreciation.",
        ("57.0",),
        (
            "Plumbing fixtures typically Section 1245",
            "Tile and flooring may be Section 1250 depending on installation",
            "Mirrors and accessories are Section 1245",
        ),
    ),
    "office": RoomGuidance(
        "office",
        "unit_space",
        "mixed",
        "Office spaces contain both Section 1245 and 1250 property. "
        "Built-in millwork and carpet are typically 1245; walls and ceiling are 1250.",
        ("00.11", "00.12"),
        (
            "Carpet and flooring typically Section 1245, 5-year",
            "Lighting fixtures typically Section 1245",
            "Structural elements remain Section 1250",
        ),
    ),
    "lobby": RoomGuidance(
        "lobby",
        "common_area",
        "mixed",
        "Lobby areas in commercial buildings contain significant Section 1245 property. "
        "Decorative finishes, specialized lighting, and reception fixtures qualify.",
        ("00.11",),
        (
            "Decorative elements often Section 1245",
            "Reception desk may be 1245 if not built-in",
            "Specialized flooring and finishes may qualify",
        ),
    ),
    "hallway": RoomGuidance(
        "hallway",
        "common_area",
        "1250",
        "Hallways are primarily Section 1250 real property. "
        "However, carpet, lighting, and fire safety equipment may be Section 1245.",
        ("00.11",),
        (
            "Carpet typically Section 1245, 5-year",
            "Emergency lighting and fire equipment Section 1245",
            "Walls and ceiling Section 1250",
        ),
    ),
    "mechanical_room": RoomGuidance(
        "mechanical_room",
        "service_area",
        "1245",
        "Mechanical rooms contain primarily Section 1245 equipment. "
        "HVAC systems, electrical panels, and plumbing equipment qualify.",
        ("57.0", "00.3"),
        (
            "HVAC equipment typically Section 1245, 5-15 year depending on type",
            "Electrical distribution equipment Section 1245",
            "Room structure remains Section 1250",
        ),
    ),
    "storage": RoomGuidance(
        "storage",
        "service_area",
        "mixed",
        "Storage areas have limited Section 1245 property. "
        "Shelving and racking systems may qualify if not permanently attached.",
        ("00.11",),
        (
            "Freestanding shelving may be Section 1245",
            "Built-in storage typically Section 1250",
            "Lighting fixtures Section 1245",
        ),
    ),
    "exterior": RoomGuidance(
        "exterior",
        "exterior",
        "1250",
        "Exterior improvements are typically Section 1250 land improvements. "
        "Parking, sidewalks, and landscaping have 15-year recovery.",
        ("00.3",),
        (
            "Paving and parking are 15-year land improvements",
            "Landscaping typically 15-year",
            "Outdoor lighting may be Section 1245",
        ),
    ),
    "parking": RoomGuidance(
        "parking",
        "exterior",
        "1250",
        "Parking improvements are Section 1250 land improvements with 15-year recovery. "
        "Includes paving, striping, curbs, and lighting infrastructure.",
        ("00.3",),
        (
            "Asphalt/concrete paving is 15-year",
            "Parking structure may have different treatment",
            "Parking lot lighting may be Section 1245",
        ),
    ),
}

ROOM_TYPICAL_COMPONENTS: Final[dict[str, tuple[str, ...]]] = {
    "kitchen": (
        "cabinet",
        "countertop",
        "sink",
        "faucet",
        "garbage disposal",
        "dishwasher",
        "range",
        "oven",
        "refrigerator",
        "microwave",
        "range hood",
        "light fixture",
        "flooring",
        "backsplash",
    ),
    "bathroom": (
        "toilet",
        "vanity",
        "sink",
        "faucet",
        "mirror",
        "light fixture",
        "shower",
        "tub",
        "tile",
        "exhaust fan",
        "towel bar",
        "flooring",
    ),
    "office": (
        "light fixture",
        "electrical outlet",
        "flooring",
        "carpet",
        "window treatment",
        "hvac vent",
        "thermostat",
        "fire sprinkler",
    ),
    "lobby": (
        "reception desk",
        "light fixture",
        "flooring",
        "elevator",
        "security system",
        "fire panel",
        "signage",
        "decorative element",
    ),
    "hallway": (
        "light fixture",
        "flooring",
        "carpet",
        "fire extinguisher",
        "exit sign",
        "emergency light",
        "door",
        "hvac vent",
    ),
    "mechanical_room": (
        "hvac unit",
        "air handler",
        "boiler",
        "water heater",
        "electrical panel",
        "generator",
        "pump",
        "pipe",
        "duct",
    ),
    "storage": ("shelving", "light fixture", "door", "flooring"),
    "exterior": (
        "door",
        "window",
        "roofing",
        "siding",
        "gutter",
        "landscaping",
        "fence",
        "lighting",
        "signage",
    ),
    "parking": (
        "pavement",
        "striping",
        "curb",
        "light pole",
        "parking meter",
        "signage",
        "bollard",
    ),
}

# (min, typical, max) square feet by property type and room type.
ROOM_AREA_ESTIMATES: Final[dict[str, dict[str, tuple[int, int, int]]]] = {
    "residential": {
        "kitchen": (80, 150, 400),
        "bathroom": (35, 75, 150),
        "bedroom": (100, 150, 300),
        "office": (80, 120, 200),
        "hallway": (20, 50, 100),
        "storage": (10, 30, 100),
    },
    "commercial": {
        "office": (100, 200, 500),
        "lobby": (200, 500, 2000),
        "hallway": (50, 150, 500),
        "bathroom": (50, 100, 300),
        "mechanical_room": (100, 300, 1000),
        "storage": (50, 200, 1000),
    },
}
DEFAULT_AREA_SF: Final = (100, 150, 200)  # legacy fallback for an unknown room type

# Allocation weights for distributing the appraisal's GLA over the study's rooms
# (basis.domain.measurements.room_areas; TAKEOFF_RESEARCH.md section 3.1 and 4): the
# typical residential area of each room type of the vision vocabulary
# (basis.vision.schemas.RoomType; ``closet`` is aliased to storage), the legacy
# residential typical areas where the table above has them. A URAR appraisal describes a
# 1-4 unit residence, so the weights are residential whatever the study's property class.
# Keyed by normalized room type.
ROOM_WEIGHTS_SF: Final[dict[str, int]] = {
    "kitchen": 150,
    "bathroom": 75,
    "bedroom": 150,
    "living_room": 250,
    "dining_room": 150,
    "family_room": 250,
    "office": 120,
    "laundry": 50,
    "hallway": 50,
    "stairway": 40,
    "lobby": 60,  # a residential foyer
    "storage": 30,
    "mechanical_room": 40,
    "attic": 150,
    "basement": 150,
    "garage": 240,
    "other": 150,
}
DEFAULT_WEIGHT_SF: Final = 150


def guidance_for(room_type: str | None) -> RoomGuidance:
    """The IRS guidance for a room type; ``found=False`` generic guidance otherwise."""
    name = normalize_room_type(room_type)
    known = ROOM_IRS_GUIDANCE.get(name)
    if known is not None:
        return known
    return RoomGuidance(
        room_type=name or "unknown",
        space_category="unknown",
        typical_section="mixed",
        guidance="Evaluate components individually for MACRS classification.",
        asset_classes=(),
        key_considerations=(),
        found=False,
    )


def area_family(property_class: str) -> str:
    """``residential_rental`` -> ``residential``; anything else -> ``commercial``."""
    return "residential" if property_class == "residential_rental" else "commercial"


def room_weight_sf(room_type: str | None) -> Decimal:
    """The allocation weight of a room type (``ROOM_WEIGHTS_SF``, else 150 SF)."""
    return Decimal(ROOM_WEIGHTS_SF.get(normalize_room_type(room_type), DEFAULT_WEIGHT_SF))


def typical_area_sf(room_type: str | None, property_class: str) -> tuple[Decimal, bool]:
    """``(typical area, known)``: the legacy typical area for this room type, else 150 SF."""
    areas = ROOM_AREA_ESTIMATES[area_family(property_class)]
    name = normalize_room_type(room_type)
    known = areas.get(name)
    _, typical, _ = known if known is not None else DEFAULT_AREA_SF
    return Decimal(typical), known is not None


# --------------------------------------------------------------------------- room corrections
# A ``room`` correction's values (``name``, ``room_type``, ``floor``; ``merge_into`` is checked
# by ``apply_corrections``). The decision route refuses what ``apply_corrections`` would, so a
# bad value is a 422 instead of a rejection that starts the next revision without any of its
# corrections; a floor sent as a number is stored as its text (``rooms.floor`` is text: "0"
# is the basement).
ROOM_TEXT_LIMITS: Final = {"name": 120, "room_type": 64, "floor": 64}
FLOOR_RANGE: Final = range(-3, 201)  # the per-photo model's range (PhotoAnalysis.floor)


def room_field_problems(after: Mapping[str, Any]) -> list[str]:
    """What is wrong with a ``room`` correction's ``name``, ``room_type`` and ``floor``."""
    problems = []
    for key in ("name", "room_type"):
        if key in after:
            value = after[key]
            limit = ROOM_TEXT_LIMITS[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                problems.append(f"{key} must be text of 1 to {limit} characters")
    if "floor" in after:
        try:
            stored_floor(after["floor"])
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def stored_floor(value: Any) -> str | None:
    """A corrected floor as ``rooms.floor`` stores it: null, the text as given (trimmed), or
    a whole number as its text (``0`` -> ``"0"``). Anything else raises ``ValueError``."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value not in FLOOR_RANGE:
            low, high = FLOOR_RANGE.start, FLOOR_RANGE.stop - 1
            raise ValueError(f"floor {value} is outside {low}..{high}")
        return str(value)
    limit = ROOM_TEXT_LIMITS["floor"]
    if isinstance(value, str) and value.strip() and len(value) <= limit:
        return value.strip()
    raise ValueError(f"floor must be null, a whole number or text of 1 to {limit} characters")
