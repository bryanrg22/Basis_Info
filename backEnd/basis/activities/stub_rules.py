"""Deterministic placeholder rules for the Stage 2 stub activities.

Room names come from photo file names, components from a fixed template per room type,
and each component label maps to one MACRS bucket. Stage 3 replaces all of this with the
vision layer and the classification pipeline; nothing outside the stub activities uses it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Final

from basis.db.enums import Bucket

# Cost charged against the study's LLM budget by the stubs, so budget handling is testable.
PHOTO_COST_USD: Final = 0.01
COMPONENT_COST_USD: Final = 0.002

UNASSIGNED: Final = "Unassigned"

_DEFAULT_COMPONENTS: Final = ("lighting", "flooring")
ROOM_COMPONENTS: Final[dict[str, tuple[str, str]]] = {
    "kitchen": ("cabinetry", "flooring"),
    "bathroom": ("plumbing_fixtures", "tile_flooring"),
    "office": ("carpet", "lighting"),
    "lobby": ("decorative_lighting", "millwork"),
}


@dataclass(frozen=True, slots=True)
class AssetRule:
    category: str
    bucket: Bucket
    recovery_period: Decimal
    irs_section: str
    unit: str
    unit_cost: Decimal


_FIVE_YEAR = (Bucket.Y5, Decimal("5.0"), "1245")
_BUILDING = (Bucket.Y39, Decimal("39.0"), "1250")

LABEL_RULES: Final[dict[str, AssetRule]] = {
    "cabinetry": AssetRule("Cabinetry", *_FIVE_YEAR, "lf", Decimal("250.00")),
    "carpet": AssetRule("Carpet", *_FIVE_YEAR, "sf", Decimal("4.50")),
    "decorative_lighting": AssetRule("Decorative lighting", *_FIVE_YEAR, "ea", Decimal("400.00")),
    "millwork": AssetRule("Decorative millwork", *_FIVE_YEAR, "lf", Decimal("60.00")),
    "flooring": AssetRule("Vinyl flooring", *_FIVE_YEAR, "sf", Decimal("6.00")),
    "lighting": AssetRule("General lighting", *_BUILDING, "ea", Decimal("150.00")),
    "plumbing_fixtures": AssetRule("Plumbing fixtures", *_BUILDING, "ea", Decimal("600.00")),
    "tile_flooring": AssetRule("Ceramic tile flooring", *_BUILDING, "sf", Decimal("12.00")),
}
FALLBACK_RULE: Final = AssetRule("Building component", *_BUILDING, "ea", Decimal("100.00"))


def room_for_filename(filename: str) -> tuple[str, str]:
    """``(name, room_type)`` from a photo file name: ``kitchen_02.jpg`` -> Kitchen."""
    stem = PurePosixPath(filename).stem.lower()
    words = [w for w in re.split(r"[^a-z]+", stem) if w and w not in ("img", "photo", "dsc")]
    if not words:
        return UNASSIGNED, "unknown"
    room_type = "_".join(words)
    return " ".join(words).title(), room_type


def components_for(room_type: str) -> tuple[str, str]:
    return ROOM_COMPONENTS.get(room_type, _DEFAULT_COMPONENTS)


def rule_for(label: str) -> AssetRule:
    return LABEL_RULES.get(label, FALLBACK_RULE)
