"""Deterministic placeholder rules for the Stage 2 ``analyze_photo`` stub.

Room names come from photo file names and components from a fixed template per room
type. Stage 3 replaces this with the vision layer (S3-B); the asset rules that used to
live here were replaced by ``basis.domain.rules`` (S3-C).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

# Cost charged against the study's LLM budget by the stubs, so budget handling is testable.
PHOTO_COST_USD: Final = 0.01

UNASSIGNED: Final = "Unassigned"

_DEFAULT_COMPONENTS: Final = ("lighting", "flooring")
ROOM_COMPONENTS: Final[dict[str, tuple[str, str]]] = {
    "kitchen": ("cabinetry", "flooring"),
    "bathroom": ("plumbing_fixtures", "tile_flooring"),
    "office": ("carpet", "lighting"),
    "lobby": ("decorative_lighting", "millwork"),
}


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
