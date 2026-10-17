"""ADE20K classes as component labels: a trained indoor segmenter read in our vocabulary.

A panoptic model trained on ADE20K (Mask2Former, Zhou et al. / Cheng et al. CVPR 2022;
OneFormer, Jain et al. CVPR 2023) segments 150 classes, of which a few dozen are building
components or surfaces (EVAL_REPORT section 26, VISION_ARCHITECTURE_SURVEY.md). This module
is the pure part of using one: which ADE20K class stands for which label, whether a photo is
an exterior view, and which segments are the room's surfaces. The model itself runs in
``evals.predict_detector``; nothing here imports torch.

Choices, each one a convention a reviewer can change:

* Names are normalized (lower case, stripped, ``_`` and ``-`` as spaces) because the
  checkpoints spell some classes differently (Mask2Former ``windowpane``, OneFormer
  ``window``; ``ashcan`` / ``trash can``).
* A class that can be inside or outside (``door``, ``light``, ``sconce``) maps to an
  interior and an exterior label; :func:`is_exterior` decides per photo from what the
  segmenter sees: some sky, or a large share of outdoor classes (trees, ground, road,
  buildings: a close exterior shot of a sign or a meter can show no sky). A window view is
  segmented as the window, so interiors rarely trip either rule. ``streetlight`` is always
  outdoor lighting.
* Every class a label could plausibly come from is a *candidate* (``lamp`` for light
  fixtures, ``kitchen island`` for cabinets); which ones count is chosen from the labels
  out of sample by ``evals.sam3_select --candidates observed``, exactly as SAM 3's phrases
  were (section 19), so the mapping does not need to be tuned by hand.
* Loose furniture, people, vehicles and decor have no entry: they are not building
  components (LABEL_GUIDE section 4).
* Surfaces (``wall``, ``floor``, ``ceiling``) are not objects; their masks are kept apart
  for the surface-material crops (``evals.predict_surface_crops``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from basis.vision.schemas import ComponentLabel

L = ComponentLabel


def normalize(name: str) -> str:
    """``"Window "`` -> ``"window"``, ``"trash_can"`` -> ``"trash can"``."""
    return " ".join(name.replace("_", " ").replace("-", " ").lower().split())


@dataclass(frozen=True)
class Target:
    """The label an ADE20K class stands for, indoors and outdoors."""

    interior: ComponentLabel
    exterior: ComponentLabel

    def label(self, exterior: bool) -> ComponentLabel:
        return self.exterior if exterior else self.interior


def _same(label: ComponentLabel) -> Target:
    return Target(label, label)


# ADE20K class name (normalized) -> target label. Names from the 150-class SceneParsing
# list as the Mask2Former and OneFormer checkpoints spell them.
CLASS_MAP: Final[Mapping[str, Target]] = {
    "windowpane": _same(L.WINDOW),
    "window": _same(L.WINDOW),
    "door": Target(L.INTERIOR_DOOR, L.EXTERIOR_DOOR),
    "screen door": Target(L.INTERIOR_DOOR, L.EXTERIOR_DOOR),
    "light": Target(L.LIGHT_FIXTURE, L.OUTDOOR_LIGHTING),
    "sconce": Target(L.LIGHT_FIXTURE, L.OUTDOOR_LIGHTING),
    "chandelier": _same(L.LIGHT_FIXTURE),
    "lamp": _same(L.LIGHT_FIXTURE),
    "streetlight": _same(L.OUTDOOR_LIGHTING),
    "cabinet": _same(L.KITCHEN_CABINET),
    "kitchen island": _same(L.KITCHEN_CABINET),
    "refrigerator": _same(L.REFRIGERATOR),
    "dishwasher": _same(L.DISHWASHER),
    "stove": _same(L.STOVE),
    "oven": _same(L.OVEN),
    "microwave": _same(L.MICROWAVE),
    "washer": _same(L.WASHER),
    "hood": _same(L.RANGE_HOOD),
    "sink": _same(L.SINK),
    "toilet": _same(L.TOILET),
    "bathtub": _same(L.BATHTUB),
    "shower": _same(L.SHOWER),
    "mirror": _same(L.MIRROR),
    "fireplace": _same(L.FIREPLACE),
    "stairs": _same(L.STAIRS),
    "stairway": _same(L.STAIRS),
    "step": _same(L.STAIRS),
    "railing": _same(L.RAILING),
    "bannister": _same(L.RAILING),
    "blind": _same(L.BLINDS),
    "curtain": _same(L.WINDOW_TREATMENT),
    "countertop": _same(L.COUNTERTOP),
    "counter": _same(L.COUNTERTOP),
    "fan": _same(L.CEILING_FAN),
    "fence": _same(L.FENCE),
    "sidewalk": _same(L.SIDEWALK),
    "signboard": _same(L.SIGNAGE),
    "trade name": _same(L.SIGNAGE),
    "swimming pool": _same(L.POOL),
}

SURFACES: Final[frozenset[str]] = frozenset({"wall", "floor", "ceiling"})
SKY: Final = "sky"
OUTDOOR: Final[frozenset[str]] = frozenset(
    {
        "sky", "tree", "grass", "earth", "road", "building", "house", "field", "sidewalk",
        "car", "mountain", "sand", "dirt track", "land", "path", "palm", "hill", "rock",
    }
)  # fmt: skip
# A photo is an exterior view when at least this share of its pixels is sky, or at least
# EXTERIOR_OUTDOOR_SHARE is outdoor classes.
EXTERIOR_SKY_SHARE: Final = 0.02
EXTERIOR_OUTDOOR_SHARE: Final = 0.25


def target(name: str) -> Target | None:
    return CLASS_MAP.get(normalize(name))


def is_exterior(class_areas: Mapping[str, float]) -> bool:
    """``class_areas`` = ADE20K class name -> share of the image; sky at or above
    :data:`EXTERIOR_SKY_SHARE`, or outdoor classes at or above
    :data:`EXTERIOR_OUTDOOR_SHARE`, make the photo an exterior view."""
    sky = sum(share for name, share in class_areas.items() if normalize(name) == SKY)
    outdoor = sum(share for name, share in class_areas.items() if normalize(name) in OUTDOOR)
    return sky >= EXTERIOR_SKY_SHARE or outdoor >= EXTERIOR_OUTDOOR_SHARE


def surface(name: str) -> str | None:
    """``wall`` / ``floor`` / ``ceiling`` for a surface class, else None."""
    key = normalize(name)
    return key if key in SURFACES else None
