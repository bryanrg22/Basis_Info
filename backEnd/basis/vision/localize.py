"""Object localization: every instance of a building component in a photo, as a mask and
a box (VISION_DETECTION_RESEARCH.md, recommendation P1 and section 4).

The per-photo VLM call (:mod:`basis.vision.analyze`) proposes labels; this module finds
each instance of them with Meta's SAM 3 (promptable concept segmentation: one noun phrase
in, every matching instance out, each with a mask, a box and a score) and counts them.

* :class:`Rle` is the compact mask: uncompressed COCO-style run lengths over the
  row-major flattened mask, starting with a run of zeros (``[0, ...]`` when the first
  pixel is set). The model returns a dense ``1 x H x W`` 0/1 array per instance (14 MB of
  JSON for ten instances on an 800 x 600 image), converted on arrival. Row-major, unlike
  pycocotools' column-major order, so ``counts`` read in the order the rows are drawn.
* :class:`Box` is normalized to the image (0-1, ``x1 < x2``, ``y1 < y2``).
* :data:`PHRASES` maps each of the 77 component labels to one to three short noun phrases
  (SAM 3 is prompted with simple noun phrases); ``other`` has none and is never prompted.
  No phrase is shared between two labels. :data:`COUNTABLE` labels are prompted on every
  photo, whatever the VLM said, because counting them is where the VLM fails (EVAL_REPORT
  section 7: 30 windows reported as 7).
* :class:`ReplicateSam3Localizer` calls the community model ``vufinder/sam3`` by version
  (the model is not served at ``/v1/models/<owner>/<name>/predictions``): one prediction
  per image with every phrase as a prompt, polled until it finishes, each prompt's result
  JSON downloaded and converted. Near-identical instances found by two phrases of one
  label (mask IoU > :data:`PHRASE_DEDUPE_IOU`) are kept once, at the higher score.
  Rate limits, 5xx and timeouts are retried with a bounded backoff (:data:`RETRY_DELAYS_S`,
  ``Retry-After`` honoured up to :data:`MAX_RETRY_AFTER_S`); anything else raises a
  :class:`LocalizerError`. The create call carries no idempotency key, so one that timed
  out after the prediction was accepted is billed twice when retried (bounded by the
  retries; a poll or download error re-polls or re-downloads, never re-creates). The
  token is sent only to ``api.replicate.com`` (result files on
  ``replicate.delivery`` are fetched without it) and is never logged or put in an error.
* The input asks for compact output (:func:`model_input`): masks cut to their boxes with
  offsets (``offset_masks``) in one result file (``split_output`` off). Pasted back by
  :func:`parse_result` they are the same masks, pixel for pixel (EVAL_REPORT 30: 50 of
  50 instances); the result is 27x smaller (0.75 MB against 20.6 MB a photo) and the
  billed predict time 3 to 4x shorter (the model writes less JSON).
* :class:`CogSam3Localizer` calls the same Cog container served by us (``LOCALIZER_URL``,
  e.g. Cloud Run with an L4: SAM3_HOSTING.md) with the same input, one synchronous
  request per photo; the output comes back inline. :func:`build_cog_localizer` makes one.
* Cost: Replicate bills public models by predict seconds on the hardware (an L40S at
  :data:`L40S_USD_PER_SECOND`). ``metrics.predict_time`` of every finished prediction is
  recorded in :class:`LocalizeCall` and charged to the :class:`~basis.llm.provider.Budget`;
  :data:`RESERVE_SECONDS` of predict time is reserved before each prediction.
* :func:`tiles` / :func:`merge_tiled`: SAHI-style slicing for small objects (outlets,
  fixtures). The full-image pass is kept and the tiles' instances are mapped back to
  full-image coordinates (exactly: a tile is a pixel crop, optionally resized by
  ``scale``), then duplicates within a label are suppressed by greedy mask NMS (IoU >
  :data:`TILE_NMS_IOU`). Instances that touch an interior tile edge are probably cut by
  the tile, so they rank after complete ones at equal footing; ``metric="ios"``
  (intersection over the smaller mask, SAHI's default match metric) also suppresses a
  cut piece lying inside a complete instance, which plain IoU can miss when the piece is
  less than half of the object. Which metric counts better is an evaluation question
  (``evals.predict_boxes --merge-metric``).
* :func:`localize_photo` is the pipeline step: phrases for (VLM labels | COUNTABLE), one
  full-image prediction, tiles for the countable phrases only when ``tiling``, merged.
* The product step (``basis.activities.localize_photo``) prompts the analysis's object
  labels (:func:`object_labels`: everything but :data:`SURFACE_LABELS`) with the countable
  set, on the full frame only (EVAL_REPORT 10.3: tiling hurt), at
  :data:`CANDIDATE_THRESHOLD`, and counts an instance when its score clears its class's
  threshold (:func:`count_threshold`). Both come from :data:`DEFAULT_PHRASE_SET`, ``v3``
  (EVAL_REPORT section 19): per class, the phrases and threshold chosen on the verified
  boxes by leaving one photo out (out of sample against the v1 table at its 10.2
  thresholds: mAP@0.5 0.65 to 0.76, count MAE 1.39 to 0.79).

Box-to-add (:meth:`Localizer.segment_prompt`) uses SAM 3's text and box prompts together:
the label's phrases of the phrase set (:data:`DEFAULT_PHRASE_SET`, the phrases the photo
was localized with), one prompt object per phrase, each carrying the drawn box. In SAM 3's
concept mode a box or point is an *exemplar*, not a selection: the model returns every
similar instance (4.6 at score >= 0.5 per box on the 43 objects the pipeline missed on the
real photos), so the result is narrowed by :func:`pick_prompted`: the instance under each
tapped point, or the one overlapping each drawn box best, accepted only at box IoU >=
:data:`PROMPT_BOX_IOU` (the PASCAL VOC / COCO match criterion) and at the call's score
threshold (:data:`DEFAULT_THRESHOLD`). Without both gates a box drawn next to an object
would take whatever similar object the model found elsewhere (``evals.manual_add``
measures the path). The call's cost comes back with the instances (:class:`Prompted`).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx
import numpy as np
import numpy.typing as npt
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from basis.llm.provider import Budget
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel

logger = logging.getLogger("basis.vision.localize")

Mask = npt.NDArray[np.bool_]
Source = Literal["model", "manual"]
MergeMetric = Literal["iou", "ios"]

REPLICATE_API: Final = "https://api.replicate.com/v1"
L40S_USD_PER_SECOND: Final = 0.000975
# Cloud Run, us-east4, instance-based billing, no zonal redundancy: one L4 ($0.0001867/s)
# with the 4 vCPU ($0.000018 each) and 16 GiB ($0.000002 each) it requires (SAM3_HOSTING.md).
CLOUD_RUN_L4_USD_PER_SECOND: Final = 0.0001867 + 4 * 0.000018 + 16 * 0.000002
RESERVE_SECONDS: Final = 30.0
PREFER_WAIT_S: Final = 60
POLL_INTERVAL_S: Final = 1.0
PREDICTION_DEADLINE_S: Final = 300.0
COG_TIMEOUT_S: Final = 300.0
RETRY_DELAYS_S: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0, 120.0)  # Replicate 429s last minutes
# An engineer waits on an interactive call (a box drawn at the rooms review): one quick
# retry of a transient error, never minutes of backoff.
INTERACTIVE_RETRY_DELAYS_S: tuple[float, ...] = (1.0,)
MAX_RETRY_AFTER_S: Final = 60.0
PHRASE_DEDUPE_IOU: Final = 0.8
TILE_NMS_IOU: Final = 0.5
DEFAULT_THRESHOLD: Final = 0.5
# A drawn box selects an instance only when their boxes overlap at IoU >= 0.5 (PASCAL VOC /
# COCO); any smaller overlap is a box drawn next to the instance, not around it.
PROMPT_BOX_IOU: Final = 0.5
_RUNNING: Final = frozenset({"starting", "processing"})


# --------------------------------------------------------------------------- errors


class LocalizerError(RuntimeError):
    """The localizer could not produce a result; ``status`` is the HTTP status if any."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LocalizerUnavailable(LocalizerError):
    """Rate limits, 5xx, timeouts or connection failures that outlasted every retry."""


class LocalizerBadResponse(LocalizerError):
    """The prediction failed, was canceled, or its output cannot be read.

    ``call`` is the prediction's :class:`LocalizeCall` when one ran and was billed (a
    failed Replicate prediction still has predict time), so the ledger can record it.
    """

    def __init__(
        self, message: str, *, status: int | None = None, call: LocalizeCall | None = None
    ) -> None:
        super().__init__(message, status=status)
        self.call = call


# --------------------------------------------------------------------------- geometry


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Box(_Model):
    """A box normalized to the image: 0 <= x1 <= x2 <= 1, 0 <= y1 <= y2 <= 1."""

    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Box:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError("a box needs x1 <= x2 and y1 <= y2")
        return self

    @classmethod
    def from_pixels(
        cls, x1: float, y1: float, x2: float, y2: float, width: int, height: int
    ) -> Box:
        """Pixel corners -> normalized, clamped to the image (the model may overshoot)."""

        def clamp(value: float) -> float:
            return min(1.0, max(0.0, value))

        left, right = sorted((clamp(x1 / width), clamp(x2 / width)))
        top, bottom = sorted((clamp(y1 / height), clamp(y2 / height)))
        return cls(x1=left, y1=top, x2=right, y2=bottom)

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    def iou(self, other: Box) -> float:
        return box_iou(self, other)

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


def box_intersection(a: Box, b: Box) -> float:
    width = min(a.x2, b.x2) - max(a.x1, b.x1)
    height = min(a.y2, b.y2) - max(a.y1, b.y1)
    return width * height if width > 0 and height > 0 else 0.0


def box_iou(a: Box, b: Box) -> float:
    """Intersection over union of two boxes; 0 when both are empty."""
    inter = box_intersection(a, b)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


class Rle(_Model):
    """A binary mask as run lengths over its row-major flattening, zeros first."""

    height: int = Field(ge=0)
    width: int = Field(ge=0)
    counts: tuple[int, ...]

    @model_validator(mode="after")
    def _runs_cover_the_mask(self) -> Rle:
        if any(c < 0 for c in self.counts):
            raise ValueError("run lengths cannot be negative")
        if sum(self.counts) != self.height * self.width:
            raise ValueError(
                f"runs cover {sum(self.counts)} pixels, the mask has {self.height * self.width}"
            )
        return self

    @classmethod
    def encode(cls, mask: Sequence[Sequence[int | bool]] | npt.NDArray[Any]) -> Rle:
        """Encode a 2-D 0/1 mask (nested lists as the model returns them, or an array)."""
        array = np.asarray(mask)
        if array.ndim != 2:
            raise ValueError(f"a mask is 2-D, got shape {array.shape}")
        height, width = (int(n) for n in array.shape)
        flat = array.reshape(-1) != 0
        if flat.size == 0:
            return cls(height=height, width=width, counts=())
        changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
        bounds = np.concatenate(([0], changes, [flat.size]))
        runs = [int(n) for n in np.diff(bounds)]
        if flat[0]:
            runs.insert(0, 0)
        return cls(height=height, width=width, counts=tuple(runs))

    def decode(self) -> Mask:
        values = np.arange(len(self.counts)) % 2 == 1
        flat = np.repeat(values, self.counts)
        return flat.reshape(self.height, self.width)

    def area(self) -> int:
        """Set pixels."""
        return int(sum(self.counts[1::2]))

    def iou(self, other: Rle) -> float:
        return mask_iou(self.decode(), other.decode())

    def bbox(self) -> tuple[int, int, int, int] | None:
        """Pixel bounds ``(x1, y1, x2, y2)`` with exclusive ends, or None when empty."""
        mask = self.decode()
        rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
        if rows.size == 0:
            return None
        return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def mask_iou(a: Mask, b: Mask) -> float:
    if a.shape != b.shape:
        raise ValueError(f"masks differ in shape: {a.shape} vs {b.shape}")
    union = int(np.logical_or(a, b).sum())
    return int(np.logical_and(a, b).sum()) / union if union else 0.0


def mask_ios(a: Mask, b: Mask) -> float:
    """Intersection over the smaller mask (1.0 when one lies inside the other)."""
    if a.shape != b.shape:
        raise ValueError(f"masks differ in shape: {a.shape} vs {b.shape}")
    smaller = min(int(a.sum()), int(b.sum()))
    return int(np.logical_and(a, b).sum()) / smaller if smaller else 0.0


# --------------------------------------------------------------------------- results


class Phrase(_Model):
    """One prompt: the noun phrase sent to the model and the label it stands for."""

    label: ComponentLabel
    text: str = Field(min_length=1, max_length=80)


class Instance(_Model):
    label: ComponentLabel
    phrase: str
    score: float = Field(ge=0.0, le=1.0)
    box: Box
    mask: Rle | None = None
    area: float = Field(ge=0.0, le=1.0, description="Share of the image the instance covers")
    source: Source = "model"


class LocalizeCall(_Model):
    """One prediction: what was asked, the billed predict time and its cost."""

    prompts: tuple[str, ...]
    predict_seconds: float = Field(ge=0.0)
    cost_usd: float = Field(ge=0.0)
    status: str
    prediction_id: str | None = None


class Localization(_Model):
    image_sha256: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    instances: tuple[Instance, ...] = ()
    calls: tuple[LocalizeCall, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return counts(self)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def predict_seconds(self) -> float:
        return sum(c.predict_seconds for c in self.calls)


class Prompted(_Model):
    """A prompted segmentation (:meth:`Localizer.segment_prompt`): the instances kept for
    the points and boxes, how many the model returned at or above the threshold before
    that narrowing, and the call (predict seconds and cost, as in :class:`Localization`)."""

    instances: tuple[Instance, ...] = ()
    returned: int = Field(default=0, ge=0)
    calls: tuple[LocalizeCall, ...] = ()

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def predict_seconds(self) -> float:
        return sum(c.predict_seconds for c in self.calls)


def counts(localization: Localization, min_score: float = 0.0) -> dict[str, int]:
    """``{label: instances}`` at or above ``min_score``, in label order."""
    totals: dict[str, int] = {}
    for instance in localization.instances:
        if instance.score >= min_score:
            totals[instance.label.value] = totals.get(instance.label.value, 0) + 1
    return dict(sorted(totals.items()))


def unlocalized(labels: Sequence[ComponentLabel | str], localization: Localization) -> list[str]:
    """Labels the VLM named that no instance was found for: left for the engineer."""
    found = {i.label.value for i in localization.instances}
    named = {ComponentLabel(label).value for label in labels} - {ComponentLabel.OTHER.value}
    return sorted(named - found)


# --------------------------------------------------------------------------- phrases

L = ComponentLabel
PHRASES: Final[dict[ComponentLabel, tuple[str, ...]]] = {
    L.CARPET: ("carpet",),
    L.HARDWOOD_FLOOR: ("hardwood floor",),
    L.VINYL_FLOORING: ("vinyl floor",),
    L.LAMINATE_FLOORING: ("laminate floor",),
    L.TILE: ("tile floor", "wall tile"),
    L.WALL_FINISH: ("wall",),
    L.CEILING_FINISH: ("ceiling",),
    L.BASEBOARD: ("baseboard",),
    L.CROWN_MOLDING: ("crown molding",),
    L.WAINSCOTING: ("wainscoting",),
    L.CONCRETE_FLOOR: ("concrete floor",),
    L.HALF_WALL: ("half wall",),
    L.INTERIOR_DOOR: ("interior door",),
    L.EXTERIOR_DOOR: ("front door", "exterior door"),
    L.WINDOW: ("window",),
    L.BLINDS: ("window blinds",),
    L.WINDOW_TREATMENT: ("curtain", "drapes"),
    L.STAIRS: ("stairs", "staircase"),
    L.RAILING: ("railing", "handrail"),
    L.FIREPLACE: ("fireplace",),
    L.CLOSET_SHELVING: ("closet shelf",),
    L.BUILT_IN_SHELVING: ("built-in shelves", "bookshelf"),
    L.MIRROR: ("mirror",),
    L.KITCHEN_CABINET: ("kitchen cabinet",),
    L.COUNTERTOP: ("countertop",),
    L.SINK: ("sink",),
    L.APPLIANCE: ("appliance",),
    L.REFRIGERATOR: ("refrigerator",),
    L.DISHWASHER: ("dishwasher",),
    L.STOVE: ("stove", "cooktop"),
    L.OVEN: ("oven",),
    L.MICROWAVE: ("microwave",),
    L.RANGE_HOOD: ("range hood",),
    L.GARBAGE_DISPOSAL: ("garbage disposal",),
    L.WASHER: ("washing machine",),
    L.DRYER: ("clothes dryer",),
    L.BATHROOM_VANITY: ("bathroom vanity",),
    L.TOILET: ("toilet",),
    L.BATHTUB: ("bathtub",),
    L.SHOWER: ("shower",),
    L.BATHROOM_ACCESSORY: ("towel bar", "toilet paper holder"),
    L.LIGHT_FIXTURE: ("light fixture", "ceiling light"),
    L.CEILING_FAN: ("ceiling fan",),
    L.OUTDOOR_LIGHTING: ("outdoor light", "wall lantern"),
    L.ELECTRICAL_PANEL: ("electrical panel", "breaker box"),
    L.ELECTRICAL_OUTLET: ("electrical outlet", "wall outlet"),
    L.HVAC_UNIT: ("furnace", "air handler"),
    L.AC_CONDENSER: ("air conditioner condenser",),
    L.HVAC_VENT: ("air vent", "air register"),
    L.THERMOSTAT: ("thermostat",),
    L.WATER_HEATER: ("water heater",),
    L.SMOKE_DETECTOR: ("smoke detector",),
    L.SPRINKLER_SYSTEM: ("fire sprinkler",),
    L.SECURITY_SYSTEM: ("security camera", "alarm keypad"),
    L.DOORBELL: ("doorbell",),
    L.WINDOW_GUARD: ("window bars", "security grille"),
    L.FURNITURE: ("sofa", "table", "bed"),
    L.DESK: ("desk",),
    L.CHAIR: ("chair",),
    L.FILING_CABINET: ("filing cabinet",),
    L.GARAGE_DOOR: ("garage door",),
    L.ROOF: ("roof",),
    L.SIDING: ("siding",),
    L.GUTTER: ("gutter",),
    L.DECK: ("deck",),
    L.PATIO: ("patio",),
    L.PORCH: ("porch",),
    L.POOL: ("swimming pool",),
    L.FENCE: ("fence",),
    L.RETAINING_WALL: ("retaining wall",),
    L.DRIVEWAY: ("driveway",),
    L.SIDEWALK: ("sidewalk",),
    L.PARKING_LOT: ("parking lot",),
    L.LANDSCAPING: ("shrub", "lawn"),
    L.SIGNAGE: ("sign",),
    L.SHED: ("shed",),
    L.CONCRETE_PAD: ("concrete pad",),
}
COUNTABLE: Final[frozenset[ComponentLabel]] = frozenset(
    {
        L.WINDOW,
        L.LIGHT_FIXTURE,
        L.OUTDOOR_LIGHTING,
        L.ELECTRICAL_OUTLET,
        L.INTERIOR_DOOR,
        L.EXTERIOR_DOOR,
        L.KITCHEN_CABINET,
    }
)
_LABEL_ORDER: Final = {label: i for i, label in enumerate(ComponentLabel)}
# Surfaces and areas, not objects: the released photo_surfaces prompt's list. A mask of a
# wall or a driveway is not something to count, so the product step does not prompt them.
SURFACE_LABELS: Final[frozenset[ComponentLabel]] = frozenset(
    {
        L.CARPET,
        L.HARDWOOD_FLOOR,
        L.VINYL_FLOORING,
        L.LAMINATE_FLOORING,
        L.TILE,
        L.WALL_FINISH,
        L.CEILING_FINISH,
        L.BASEBOARD,
        L.CROWN_MOLDING,
        L.WAINSCOTING,
        L.CONCRETE_FLOOR,
        L.HALF_WALL,
        L.SIDING,
        L.ROOF,
        L.GUTTER,
        L.STAIRS,
        L.RAILING,
        L.PORCH,
        L.DECK,
        L.PATIO,
        L.DRIVEWAY,
        L.SIDEWALK,
        L.PARKING_LOT,
        L.LANDSCAPING,
        L.POOL,
        L.RETAINING_WALL,
    }
)
# Count thresholds per class, one table per phrase set (a threshold belongs to the phrases
# whose scores it cuts). "v1": EVAL_REPORT section 10.2, window 0.6 where its precision and
# count error were best, every other class measured at 0.5. "v3": section 19, chosen per
# class on the verified boxes as the grid value (0.30 to 0.90) with the least count error,
# by leaving one photo out (each choice stable in 50 to 52 of 52 folds; light fixture 44).
# A label without an entry is counted at COUNT_THRESHOLD_DEFAULT.
COUNT_THRESHOLD_DEFAULT: Final = 0.5
COUNT_THRESHOLD_SETS: Final[dict[str, Mapping[ComponentLabel, float]]] = {
    "v1": {L.WINDOW: 0.6},
    "v3": {
        L.WINDOW: 0.65,
        L.INTERIOR_DOOR: 0.5,
        L.EXTERIOR_DOOR: 0.85,
        L.LIGHT_FIXTURE: 0.7,
        L.OUTDOOR_LIGHTING: 0.3,
        L.ELECTRICAL_OUTLET: 0.55,
        L.KITCHEN_CABINET: 0.7,
    },
}
# Sent to the model in the product step: instances between this and the count threshold
# are stored as uncounted candidates, so a tap can select an object the model scored low.
# Below every count threshold (v3's lowest is 0.3), so every counted instance is returned.
CANDIDATE_THRESHOLD: Final = 0.25


# Alternative phrase tables for experiments (EVAL_REPORT section 10): "v1" is PHRASES; "v2"
# re-phrases the classes whose v1 phrases caught the wrong objects on the real photos
# (plug-in lamps as light fixtures, porch lanterns missed by "outdoor light", cabinet runs
# instead of units) so each change is measured against the same verified labels.
PHRASE_SETS: Final[dict[str, Mapping[ComponentLabel, tuple[str, ...]]]] = {
    "v1": PHRASES,
    "v2": {
        **PHRASES,
        L.LIGHT_FIXTURE: (
            "ceiling light",
            "recessed light",
            "pendant light",
            "chandelier",
            "wall sconce",
        ),
        L.OUTDOOR_LIGHTING: ("porch light", "wall lantern", "outdoor light fixture"),
        L.KITCHEN_CABINET: ("cabinet door",),
        L.INTERIOR_DOOR: ("interior door", "door"),
    },
    # Section 19: per class, the pool subset with the best AP@0.5 on the verified boxes,
    # chosen by leaving one photo out (each choice stable in 44 to 52 of 52 folds). Only
    # the countable classes change; every other label keeps its v1 phrases.
    "v3": {
        **PHRASES,
        L.WINDOW: ("house window",),
        L.INTERIOR_DOOR: ("room door",),
        L.EXTERIOR_DOOR: ("front door",),
        L.LIGHT_FIXTURE: ("recessed light", "ceiling lamp"),
        L.OUTDOOR_LIGHTING: ("porch light", "outdoor wall light"),
        L.ELECTRICAL_OUTLET: ("wall outlet", "power outlet"),
        L.KITCHEN_CABINET: ("kitchen cabinet", "cabinet door"),
    },
    # Not a deployable table: every candidate phrase for the countable classes (v1, v2 and
    # the ones the rejection reasons of EVAL_REPORT 10.2 suggest), prompted once each with
    # no dedupe so any per-class subset can be scored offline (EVAL_REPORT section 19).
    "pool": {
        **PHRASES,
        L.WINDOW: ("window", "house window", "glass window"),
        L.INTERIOR_DOOR: ("interior door", "door", "room door", "bedroom door", "wooden door"),
        L.EXTERIOR_DOOR: ("front door", "exterior door", "entry door", "entrance door"),
        L.LIGHT_FIXTURE: (
            "light fixture",
            "ceiling light",
            "recessed light",
            "pendant light",
            "chandelier",
            "wall sconce",
            "ceiling lamp",
            "flush mount light",
        ),
        L.OUTDOOR_LIGHTING: (
            "outdoor light",
            "wall lantern",
            "porch light",
            "outdoor light fixture",
            "exterior light",
            "porch lantern",
            "hanging lantern",
            "outdoor wall light",
        ),
        L.ELECTRICAL_OUTLET: ("electrical outlet", "wall outlet", "power outlet", "outlet"),
        L.KITCHEN_CABINET: (
            "kitchen cabinet",
            "cabinet door",
            "cabinet",
            "cabinet drawer",
            "drawer",
        ),
    },
}
# The phrase set (and its count thresholds) the pipeline and the harness use by default.
DEFAULT_PHRASE_SET: Final = "v3"
COUNT_THRESHOLDS: Final[Mapping[ComponentLabel, float]] = COUNT_THRESHOLD_SETS[DEFAULT_PHRASE_SET]


def prompt_labels(labels: Sequence[ComponentLabel | str]) -> list[ComponentLabel]:
    """``labels`` | :data:`COUNTABLE`, without ``other`` and unknown labels, in vocabulary
    order (so the prompt list, and so the request, is deterministic)."""
    chosen = set(COUNTABLE)
    for label in labels:
        try:
            chosen.add(ComponentLabel(label))
        except ValueError:
            logger.warning("not a component label, not prompted: %r", label)
    chosen.discard(ComponentLabel.OTHER)
    return sorted(chosen, key=_LABEL_ORDER.__getitem__)


def object_labels(labels: Sequence[ComponentLabel | str]) -> list[ComponentLabel]:
    """The labels the product step prompts: :func:`prompt_labels` of ``labels`` without the
    surfaces (:data:`SURFACE_LABELS`); the countable set is always included."""
    objects: list[ComponentLabel | str] = []
    for label in labels:
        try:
            if ComponentLabel(label) in SURFACE_LABELS:
                continue
        except ValueError:
            pass  # prompt_labels logs and drops it
        objects.append(label)
    return prompt_labels(objects)


def count_threshold(label: ComponentLabel | str, phrase_set: str = DEFAULT_PHRASE_SET) -> float:
    """The score at which an instance of ``label`` found with ``phrase_set``'s phrases is
    counted (EVAL_REPORT 10.2 for v1, 19 for v3; a set without a table: the default)."""
    table = COUNT_THRESHOLD_SETS.get(phrase_set, {})
    try:
        return table.get(ComponentLabel(label), COUNT_THRESHOLD_DEFAULT)
    except ValueError:
        return COUNT_THRESHOLD_DEFAULT


def class_counts(
    localization: Localization, phrase_set: str = DEFAULT_PHRASE_SET
) -> dict[str, int]:
    """``{label: instances}`` counted at each label's threshold (:func:`count_threshold`)."""
    totals: dict[str, int] = {}
    for instance in localization.instances:
        if instance.score >= count_threshold(instance.label, phrase_set):
            totals[instance.label.value] = totals.get(instance.label.value, 0) + 1
    return dict(sorted(totals.items()))


def phrases_for(
    labels: Sequence[ComponentLabel], phrase_set: str = DEFAULT_PHRASE_SET
) -> list[Phrase]:
    table = PHRASE_SETS[phrase_set]
    return [Phrase(label=label, text=text) for label in labels for text in table.get(label, ())]


# --------------------------------------------------------------------------- suppression


def suppress(
    instances: Sequence[Instance],
    threshold: float,
    *,
    metric: MergeMetric = "iou",
    rank: Callable[[int], tuple[Any, ...]] | None = None,
) -> list[Instance]:
    """Greedy NMS within each label: in ``rank`` order (a key on the instance's index;
    default: score, highest first, then input order),
    keep an instance unless it overlaps a kept one of its label by more than ``threshold``
    (mask ``metric``; box IoU when either mask is missing). Output in kept order."""
    order = sorted(
        range(len(instances)),
        key=rank if rank is not None else (lambda i: (-instances[i].score, i)),
    )
    decoded: dict[int, Mask] = {}

    def mask_of(i: int) -> Mask | None:
        rle = instances[i].mask
        if rle is None:
            return None
        if i not in decoded:
            decoded[i] = rle.decode()
        return decoded[i]

    kept: list[int] = []
    for i in order:
        candidate = instances[i]
        duplicate = False
        for j in kept:
            other = instances[j]
            if other.label != candidate.label:
                continue
            a, b = mask_of(i), mask_of(j)
            if a is not None and b is not None and a.shape == b.shape:
                overlap = mask_iou(a, b) if metric == "iou" else mask_ios(a, b)
            else:
                overlap = box_iou(candidate.box, other.box)
            if overlap > threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(i)
    return [instances[i] for i in kept]


# --------------------------------------------------------------------------- protocol


class Point(_Model):
    """A tap, normalized to the image."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


@runtime_checkable
class Localizer(Protocol):
    async def localize(
        self,
        image: PreparedImage,
        phrases: Sequence[Phrase],
        *,
        threshold: float,
        dedupe: bool = True,
    ) -> Localization: ...

    async def segment_prompt(
        self,
        image: PreparedImage,
        *,
        label: ComponentLabel,
        points: Sequence[Point] = (),
        boxes: Sequence[Box] = (),
        negative_points: Sequence[Point] = (),
        threshold: float = DEFAULT_THRESHOLD,
        phrase_set: str = DEFAULT_PHRASE_SET,
    ) -> Prompted: ...


def covers(instance: Instance, point: Point) -> bool:
    """Whether the instance's mask (its box when it has none) covers the point."""
    if instance.mask is not None:
        col = min(instance.mask.width - 1, int(point.x * instance.mask.width))
        row = min(instance.mask.height - 1, int(point.y * instance.mask.height))
        return bool(instance.mask.decode()[row, col])
    return instance.box.contains(point.x, point.y)


def under_point(candidates: Sequence[Instance], point: Point) -> int | None:
    """The index of the highest-scoring instance covering the point (the first on ties)."""
    hits = [i for i, instance in enumerate(candidates) if covers(instance, point)]
    return max(hits, key=lambda i: (candidates[i].score, -i)) if hits else None


def best_box_match(
    candidates: Sequence[Instance], box: Box, min_iou: float = PROMPT_BOX_IOU
) -> int | None:
    """The index of the instance whose box overlaps ``box`` best, at box IoU >= ``min_iou``
    (and above 0); the higher score on ties, then the first. None when none does."""
    scored = [
        (box_iou(box, instance.box), instance.score, -i) for i, instance in enumerate(candidates)
    ]
    kept = [s for s in scored if s[0] >= min_iou and s[0] > 0]
    return -max(kept)[2] if kept else None


def pick_prompted(
    candidates: Sequence[Instance], points: Sequence[Point], boxes: Sequence[Box]
) -> list[Instance]:
    """The instance under each point (highest score when several cover it) and the one
    overlapping each box best at box IoU >= :data:`PROMPT_BOX_IOU`; each instance at most
    once, as ``source="manual"``."""
    picked = [under_point(candidates, p) for p in points]
    picked += [best_box_match(candidates, b) for b in boxes]
    chosen: list[int] = []
    for index in picked:
        if index is not None and index not in chosen:
            chosen.append(index)
    return [candidates[i].model_copy(update={"source": "manual"}) for i in chosen]


# --------------------------------------------------------------------------- replicate


def _center_box(box: Box) -> list[float]:
    return [
        (box.x1 + box.x2) / 2,
        (box.y1 + box.y2) / 2,
        box.x2 - box.x1,
        box.y2 - box.y1,
    ]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return min(MAX_RETRY_AFTER_S, max(0.0, float(value))) if value is not None else None
    except ValueError:
        return None


def _paste(cutout: npt.NDArray[Any], offset: Any, width: int, height: int) -> npt.NDArray[Any]:
    """An ``offset_masks`` cut-out (``h x w``, upper-left corner ``[x, y]`` in pixels of
    the input image) placed on an empty ``height x width`` mask; what falls outside the
    image is dropped (the model's cut-outs lie inside it, measured in EVAL_REPORT 30)."""
    try:
        x, y = (int(v) for v in offset)
    except (TypeError, ValueError):
        raise LocalizerBadResponse(f"bad mask offset {offset!r}") from None
    full = np.zeros((height, width), dtype=np.uint8)
    h, w = cutout.shape
    top, left = max(0, y), max(0, x)
    bottom, right = min(height, y + h), min(width, x + w)
    if bottom > top and right > left:
        full[top:bottom, left:right] = cutout[top - y : bottom - y, left - x : right - x]
    return full


async def _send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    json_body: Any,
    delays: Sequence[float],
    sleep: Callable[[float], Awaitable[None]],
    also_retry: frozenset[int] = frozenset(),
) -> httpx.Response:
    """One request, retried after ``delays`` on 429, 5xx, timeouts, connection failures
    and the statuses in ``also_retry`` (``Retry-After`` honoured up to
    :data:`MAX_RETRY_AFTER_S`); any other 4xx raises :class:`LocalizerError` at once and
    the last transient failure :class:`LocalizerUnavailable`. Errors carry the URL
    without its query, never the headers."""
    last: str = ""
    for attempt in range(len(delays) + 1):
        delay = delays[attempt] if attempt < len(delays) else None
        try:
            response = await client.request(method, url, json=json_body, headers=dict(headers))
        except httpx.TimeoutException:
            last, status = f"{method} {_safe(url)} timed out", None
        except httpx.TransportError as exc:
            last, status = f"{method} {_safe(url)} failed: {type(exc).__name__}", None
        else:
            status = response.status_code
            if status < 400:
                return response
            if status != 429 and status < 500 and status not in also_retry:
                raise LocalizerError(
                    f"{method} {_safe(url)} returned {status}: {_detail(response)}",
                    status=status,
                )
            last = f"{method} {_safe(url)} returned {status}"
            after = _retry_after(response)
            if delay is not None and after is not None:
                delay = max(delay, after)
        if delay is None:
            raise LocalizerUnavailable(f"{last} (after {attempt + 1} attempts)", status=status)
        logger.warning("transient localizer error, retrying in %gs: %s", delay, last)
        await sleep(delay)
    raise AssertionError("unreachable")


def parse_result(
    data: Mapping[str, Any], phrase: Phrase, width: int, height: int, threshold: float
) -> list[Instance]:
    """One prompt's result JSON -> instances at or above ``threshold``.

    ``boxes`` are ``[x1, y1, x2, y2]`` pixels of the input image; ``masks`` are
    ``1 x H x W`` 0/1 arrays, one per box, in the same order as ``scores``, or with
    ``masks_offset`` (the model's ``offset_masks`` input) ``h x w`` cut-outs whose
    upper-left corner is the matching ``[x, y]``, pasted back onto the full image. A mask
    that is not that shape is dropped (the instance keeps its box); a length mismatch
    between boxes, scores, masks and offsets is a bad response.
    """
    boxes, scores = data.get("boxes"), data.get("scores")
    masks = data.get("masks") or []
    offsets = data.get("masks_offset")
    if not isinstance(boxes, list) or not isinstance(scores, list) or len(boxes) != len(scores):
        raise LocalizerBadResponse(f"result for {phrase.text!r} has no matching boxes and scores")
    if masks and len(masks) != len(boxes):
        raise LocalizerBadResponse(
            f"result for {phrase.text!r}: {len(masks)} masks, {len(boxes)} boxes"
        )
    if offsets is not None and (not isinstance(offsets, list) or len(offsets) != len(masks)):
        raise LocalizerBadResponse(f"result for {phrase.text!r}: mask offsets do not match")
    instances: list[Instance] = []
    for i, (raw_box, raw_score) in enumerate(zip(boxes, scores, strict=True)):
        score = float(raw_score)
        if score < threshold:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in raw_box)
        except (TypeError, ValueError):
            raise LocalizerBadResponse(f"result for {phrase.text!r}: bad box {raw_box!r}") from None
        box = Box.from_pixels(x1, y1, x2, y2, width, height)
        rle: Rle | None = None
        if masks:
            array = np.asarray(masks[i])
            if array.ndim == 3 and array.shape[0] == 1:
                array = array[0]
            if offsets is not None and array.ndim == 2 and array.size:
                array = _paste(array, offsets[i], width, height)
            if array.ndim == 2 and array.size:
                rle = Rle.encode(array)
            else:
                logger.warning("dropping a mask of shape %s for %r", array.shape, phrase.text)
        area = rle.area() / (rle.height * rle.width) if rle is not None else box.area
        instances.append(
            Instance(
                label=phrase.label,
                phrase=phrase.text,
                score=min(1.0, max(0.0, score)),
                box=box,
                mask=rle,
                area=min(1.0, area),
            )
        )
    return instances


@dataclass
class ReplicateSam3Localizer:
    """SAM 3 on Replicate (``vufinder/sam3``) through an ``httpx.AsyncClient``.

    The client's timeout must exceed ``prefer_wait_s`` (the create call may be held that
    long, :data:`PREFER_WAIT_S` by default); :func:`build_replicate_localizer` makes one.
    """

    client: httpx.AsyncClient
    token: str = field(repr=False)
    version: str
    concurrency: int = 4
    budget: Budget | None = None
    usd_per_second: float = L40S_USD_PER_SECOND
    api: str = REPLICATE_API
    # The ledger's provider name and model (``vufinder/sam3`` by version id).
    provider: str = "replicate"
    model: str = "vufinder/sam3"
    poll_interval_s: float = POLL_INTERVAL_S
    deadline_s: float = PREDICTION_DEADLINE_S
    prefer_wait_s: int = PREFER_WAIT_S
    retry_delays: tuple[float, ...] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    compact: bool = True
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.token:
            raise LocalizerError("no Replicate API token")
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._api_host = urlsplit(self.api).netloc

    # ------------------------------------------------------------------ http

    def _headers(self, url: str) -> dict[str, str]:
        # The token goes to the API host only, never to the file-delivery host.
        return (
            {"Authorization": f"Bearer {self.token}"}
            if urlsplit(url).netloc == self._api_host
            else {}
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        wait: bool = False,
        retry_not_found: bool = False,
    ) -> httpx.Response:
        headers = self._headers(url)
        if wait:
            headers["Prefer"] = f"wait={self.prefer_wait_s}"
        retry = frozenset({404}) if retry_not_found else frozenset()
        return await _send(
            self.client,
            method,
            url,
            headers=headers,
            json_body=json_body,
            delays=RETRY_DELAYS_S if self.retry_delays is None else self.retry_delays,
            sleep=self.sleep,
            also_retry=retry,
        )

    @staticmethod
    async def _json(response: httpx.Response) -> dict[str, Any]:
        """The body as a JSON object, parsed off the event loop (a result is megabytes)."""
        try:
            body = await asyncio.to_thread(json.loads, response.content)
        except ValueError:
            raise LocalizerBadResponse(f"{_safe(str(response.url))} did not return JSON") from None
        if not isinstance(body, dict):
            raise LocalizerBadResponse(f"{_safe(str(response.url))} did not return a JSON object")
        return body

    # ------------------------------------------------------------------ prediction

    async def _predict(
        self, input_: dict[str, Any], prompts: tuple[str, ...]
    ) -> tuple[dict[str, Any], LocalizeCall]:
        if self.budget is not None:
            self.budget.reserve(RESERVE_SECONDS * self.usd_per_second)
        response = await self._request(
            "POST",
            f"{self.api}/predictions",
            json_body={"version": self.version, "input": input_},
            wait=True,
        )
        prediction = await self._json(response)
        loop = asyncio.get_running_loop()
        started = loop.time()
        while prediction.get("status") in _RUNNING:
            get_url = (prediction.get("urls") or {}).get("get")
            if not get_url:
                raise LocalizerBadResponse("a running prediction has no urls.get to poll")
            if loop.time() - started > self.deadline_s:
                await self._cancel(prediction)
                raise LocalizerUnavailable(
                    f"prediction {prediction.get('id')} still {prediction.get('status')} "
                    f"after {self.deadline_s:g}s; canceled"
                )
            await self.sleep(self.poll_interval_s)
            prediction = await self._json(await self._request("GET", get_url))
        status = str(prediction.get("status"))
        seconds = float((prediction.get("metrics") or {}).get("predict_time") or 0.0)
        cost = round(seconds * self.usd_per_second, 6)
        call = LocalizeCall(
            prompts=prompts,
            predict_seconds=seconds,
            cost_usd=cost,
            status=status,
            prediction_id=prediction.get("id"),
        )
        if self.budget is not None and cost > 0:
            self.budget.charge(cost)
        if status != "succeeded":
            raise LocalizerBadResponse(
                f"prediction {prediction.get('id')} {status}: {str(prediction.get('error'))[:300]}",
                call=call,
            )
        return prediction, call

    async def _cancel(self, prediction: Mapping[str, Any]) -> None:
        url = (prediction.get("urls") or {}).get("cancel")
        if not url:
            return
        try:
            await self.client.post(url, headers=self._headers(url))
        except httpx.HTTPError:
            logger.warning("could not cancel prediction %s", prediction.get("id"))

    async def _results(self, prediction: Mapping[str, Any], expected: int) -> list[dict[str, Any]]:
        output = prediction.get("output") or {}
        urls = output.get("results") if isinstance(output, Mapping) else None
        if not isinstance(urls, list) or not urls:
            raise LocalizerBadResponse(f"prediction {prediction.get('id')} returned no results")
        # A result file is sometimes not served yet right after the prediction succeeds
        # (one 404 in 52 photos, EVAL_REPORT 30): retried like a transient error.
        responses = await asyncio.gather(
            *(self._request("GET", str(u), retry_not_found=True) for u in urls)
        )
        documents = await asyncio.gather(*(_json_document(r) for r in responses))
        return per_prompt(documents, expected, f"prediction {prediction.get('id')}")

    async def _run(
        self, input_: dict[str, Any], prompts: tuple[str, ...]
    ) -> tuple[list[dict[str, Any]], LocalizeCall]:
        async with self._semaphore:
            prediction, call = await self._predict(input_, prompts)
            return await self._results(prediction, len(prompts)), call

    async def localize(
        self,
        image: PreparedImage,
        phrases: Sequence[Phrase],
        *,
        threshold: float,
        dedupe: bool = True,
    ) -> Localization:
        """Every phrase in one prediction; with ``dedupe`` (the pipeline) same-label
        instances found by two phrases are kept once, without it every phrase's instances
        are kept as returned (the phrase-pool experiment, EVAL_REPORT section 19)."""
        return await _localize(self._run, image, phrases, threshold, dedupe, self.compact)

    async def segment_prompt(
        self,
        image: PreparedImage,
        *,
        label: ComponentLabel,
        points: Sequence[Point] = (),
        boxes: Sequence[Box] = (),
        negative_points: Sequence[Point] = (),
        threshold: float = DEFAULT_THRESHOLD,
        phrase_set: str = DEFAULT_PHRASE_SET,
    ) -> Prompted:
        return await _segment_prompt(
            self._run,
            image,
            label,
            points,
            boxes,
            negative_points,
            threshold,
            self.compact,
            phrase_set,
        )


# --------------------------------------------------------------------------- self-hosted


@dataclass
class CogSam3Localizer:
    """The same SAM 3 Cog container served by us at ``url`` (``LOCALIZER_URL``), e.g. on
    Cloud Run with an L4 (SAM3_HOSTING.md): ``POST {url}/predictions`` with ``{"input":
    ...}`` answers when the prediction is done, output files inline as ``data:`` URIs.

    A Cog server runs one prediction at a time and answers 409 while busy; Cloud Run
    answers 429 when every instance is busy and no new one can start. Both are retried as
    transient, with 5xx, timeouts and connection failures (:data:`RETRY_DELAYS_S`). The
    bearer token (``token_source``, called before each request: a Cloud Run ID token
    expires after an hour) goes to ``url``'s host only and is never logged. Cost: the
    request's wall time at ``usd_per_second`` (an instance is billed while it serves;
    its idle time before it scales to zero is not charged to any study).
    """

    client: httpx.AsyncClient
    url: str
    version: str
    token_source: Callable[[], Awaitable[str | None]] | None = field(default=None, repr=False)
    concurrency: int = 4
    usd_per_second: float = CLOUD_RUN_L4_USD_PER_SECOND
    # The ledger's provider name (a self-hosted Cog server) and model.
    provider: str = "cog"
    model: str = "sam3"
    compact: bool = True
    retry_delays: tuple[float, ...] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise LocalizerError("LOCALIZER_URL must be an http(s) URL")
        self.url = self.url.rstrip("/")
        self._semaphore = asyncio.Semaphore(self.concurrency)

    async def _run(
        self, input_: dict[str, Any], prompts: tuple[str, ...]
    ) -> tuple[list[dict[str, Any]], LocalizeCall]:
        endpoint = f"{self.url}/predictions"
        async with self._semaphore:
            headers: dict[str, str] = {}
            token = await self.token_source() if self.token_source is not None else None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            started = self.clock()
            response = await _send(
                self.client,
                "POST",
                endpoint,
                headers=headers,
                json_body={"input": input_},
                delays=RETRY_DELAYS_S if self.retry_delays is None else self.retry_delays,
                sleep=self.sleep,
                also_retry=frozenset({409}),
            )
            elapsed = self.clock() - started
        prediction = await _json_document(response)
        if not isinstance(prediction, dict):
            raise LocalizerBadResponse(f"{_safe(endpoint)} did not return a JSON object")
        status = str(prediction.get("status"))
        seconds = float((prediction.get("metrics") or {}).get("predict_time") or 0.0)
        call = LocalizeCall(
            prompts=prompts,
            predict_seconds=seconds,
            cost_usd=round(elapsed * self.usd_per_second, 6),
            status=status,
        )
        if status != "succeeded":
            raise LocalizerBadResponse(
                f"prediction {status}: {str(prediction.get('error'))[:300]}", call=call
            )
        output = prediction.get("output") or {}
        uris = output.get("results") if isinstance(output, Mapping) else None
        if not isinstance(uris, list) or not uris:
            raise LocalizerBadResponse("the prediction returned no results")
        documents = await asyncio.gather(*(asyncio.to_thread(_from_data_uri, u) for u in uris))
        return per_prompt(documents, len(prompts), "the prediction"), call

    async def localize(
        self,
        image: PreparedImage,
        phrases: Sequence[Phrase],
        *,
        threshold: float,
        dedupe: bool = True,
    ) -> Localization:
        return await _localize(self._run, image, phrases, threshold, dedupe, self.compact)

    async def segment_prompt(
        self,
        image: PreparedImage,
        *,
        label: ComponentLabel,
        points: Sequence[Point] = (),
        boxes: Sequence[Box] = (),
        negative_points: Sequence[Point] = (),
        threshold: float = DEFAULT_THRESHOLD,
        phrase_set: str = DEFAULT_PHRASE_SET,
    ) -> Prompted:
        return await _segment_prompt(
            self._run,
            image,
            label,
            points,
            boxes,
            negative_points,
            threshold,
            self.compact,
            phrase_set,
        )


# --------------------------------------------------------------------------- shared steps

_Run = Callable[
    [dict[str, Any], tuple[str, ...]], Awaitable[tuple[list[dict[str, Any]], LocalizeCall]]
]


def model_input(
    image: PreparedImage, prompts: Sequence[str], threshold: float, compact: bool
) -> dict[str, Any]:
    """The SAM 3 Cog model's input. ``compact`` asks for masks cut to their boxes with
    offsets (``offset_masks``, pasted back by :func:`parse_result`, lossless) in one result
    file (``split_output`` off): on the 52 photos 15x less to transfer (EVAL_REPORT 30)."""
    input_: dict[str, Any] = {
        "image": f"data:image/jpeg;base64,{image.base64}",
        "prompts": list(prompts),
        "confidence_threshold": threshold,
        "visualize": False,
    }
    if compact:
        input_ |= {"offset_masks": True, "split_output": False}
    return input_


async def _localize(
    run: _Run,
    image: PreparedImage,
    phrases: Sequence[Phrase],
    threshold: float,
    dedupe: bool,
    compact: bool,
) -> Localization:
    if not phrases:
        return Localization(image_sha256=image.sha256, width=image.width, height=image.height)
    prompts = tuple(json.dumps({"text": p.text}) for p in phrases)
    results, call = await run(
        model_input(image, prompts, threshold, compact), tuple(p.text for p in phrases)
    )
    found: list[Instance] = []
    for phrase, data in zip(phrases, results, strict=True):
        found += await asyncio.to_thread(
            parse_result, data, phrase, image.width, image.height, threshold
        )
    return Localization(
        image_sha256=image.sha256,
        width=image.width,
        height=image.height,
        instances=tuple(suppress(found, PHRASE_DEDUPE_IOU) if dedupe else found),
        calls=(call,),
    )


async def _segment_prompt(
    run: _Run,
    image: PreparedImage,
    label: ComponentLabel,
    points: Sequence[Point],
    boxes: Sequence[Box],
    negative_points: Sequence[Point],
    threshold: float,
    compact: bool,
    phrase_set: str,
) -> Prompted:
    if not points and not boxes:
        raise ValueError("a prompt needs at least one point or box")
    phrases = prompt_phrases(label, phrase_set)
    geometry: dict[str, Any] = {}
    if points:
        geometry["positive_points"] = [[p.x, p.y] for p in points]
    if negative_points:
        geometry["negative_points"] = [[p.x, p.y] for p in negative_points]
    if boxes:
        geometry["positive_boxes"] = [_center_box(b) for b in boxes]
    prompts = [json.dumps({"text": p.text, **geometry}) for p in phrases]
    results, call = await run(
        model_input(image, prompts, threshold, compact), tuple(p.text for p in phrases)
    )
    found: list[Instance] = []
    for phrase, data in zip(phrases, results, strict=True):
        found += await asyncio.to_thread(
            parse_result, data, phrase, image.width, image.height, threshold
        )
    return Prompted(
        instances=tuple(pick_prompted(found, points, boxes)), returned=len(found), calls=(call,)
    )


def prompt_phrases(label: ComponentLabel, phrase_set: str = DEFAULT_PHRASE_SET) -> list[Phrase]:
    """The phrases a prompted segmentation of ``label`` sends, one prompt object each: the
    label's phrases in ``phrase_set`` (those the photo was localized with), else its name."""
    return phrases_for([label], phrase_set) or [
        Phrase(label=label, text=label.value.replace("_", " "))
    ]


def per_prompt(documents: Sequence[Any], expected: int, what: str) -> list[dict[str, Any]]:
    """The output files as one result per prompt: a file per prompt (``split_output``)
    or one file holding the list of them."""
    if len(documents) == 1 and isinstance(documents[0], list):
        results = documents[0]
    else:
        results = list(documents)
    if len(results) != expected or not all(isinstance(r, dict) for r in results):
        raise LocalizerBadResponse(f"{what} returned {len(results)} results for {expected} prompts")
    return results


async def _json_document(response: httpx.Response) -> Any:
    """The body as JSON, parsed off the event loop (a result is megabytes)."""
    try:
        return await asyncio.to_thread(json.loads, response.content)
    except ValueError:
        raise LocalizerBadResponse(f"{_safe(str(response.url))} did not return JSON") from None


def _from_data_uri(uri: Any) -> Any:
    """A Cog output file returned inline: ``data:<type>;base64,<JSON>``."""
    if not isinstance(uri, str) or not uri.startswith("data:") or ";base64," not in uri:
        raise LocalizerBadResponse("a result is not an inline data URI")
    try:
        return json.loads(base64.b64decode(uri.split(";base64,", 1)[1], validate=True))
    except (ValueError, binascii.Error):
        raise LocalizerBadResponse("a result data URI is not base64 JSON") from None


def _safe(url: str) -> str:
    """A URL without its query string (never carries the token, but keep logs short)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, Mapping):
        return str(body.get("detail") or body.get("title") or body)[:200]
    return str(body)[:200]


def build_replicate_localizer(
    token: str,
    version: str,
    *,
    concurrency: int = 4,
    budget: Budget | None = None,
    timeout_s: float | None = None,
) -> ReplicateSam3Localizer:
    """A Replicate client. ``timeout_s`` makes an interactive one (an engineer waits on the
    answer, :data:`INTERACTIVE_RETRY_DELAYS_S`): the create call is held at most that long,
    a prediction still running after it is canceled, and transient errors are retried
    once; the caller bounds the whole call with the same timeout."""
    if timeout_s is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(PREFER_WAIT_S + 30.0, connect=10.0))
        return ReplicateSam3Localizer(
            client=client, token=token, version=version, concurrency=concurrency, budget=budget
        )
    wait = min(PREFER_WAIT_S, max(1, math.ceil(timeout_s)))
    client = httpx.AsyncClient(timeout=httpx.Timeout(wait + 5.0, connect=5.0))
    return ReplicateSam3Localizer(
        client=client,
        token=token,
        version=version,
        concurrency=concurrency,
        budget=budget,
        deadline_s=timeout_s,
        prefer_wait_s=wait,
        retry_delays=INTERACTIVE_RETRY_DELAYS_S,
    )


TokenSource = Callable[[], Awaitable[str | None]]
ID_TOKEN_LIFETIME_S: Final = 3600.0  # Google ID tokens expire after an hour
ID_TOKEN_REFRESH_S: Final = 300.0


def static_token(token: str) -> TokenSource:
    """Always the same bearer token (``LOCALIZER_TOKEN``)."""

    async def source() -> str | None:
        return token

    return source


def google_id_token(
    audience: str,
    *,
    fetch: Callable[[str], str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TokenSource:
    """A Google ID token for ``audience`` (a Cloud Run service URL), fetched with the
    process's service-account credentials (the metadata server on Google Cloud, or
    ``GOOGLE_APPLICATION_CREDENTIALS``) and refreshed :data:`ID_TOKEN_REFRESH_S` before it
    expires. User credentials cannot mint one; use ``LOCALIZER_TOKEN`` locally."""

    def default_fetch(aud: str) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        return str(id_token.fetch_id_token(Request(), aud))  # type: ignore[no-untyped-call]

    get = fetch or default_fetch
    cached: list[tuple[str, float]] = []
    lock = asyncio.Lock()

    async def source() -> str | None:
        async with lock:
            if not cached or clock() > cached[0][1]:
                token = await asyncio.to_thread(get, audience)
                cached[:] = [(token, clock() + ID_TOKEN_LIFETIME_S - ID_TOKEN_REFRESH_S)]
            return cached[0][0]

    return source


def build_cog_localizer(
    url: str,
    version: str,
    *,
    token_source: TokenSource | None = None,
    concurrency: int = 4,
    usd_per_second: float = CLOUD_RUN_L4_USD_PER_SECOND,
    timeout_s: float | None = None,
) -> CogSam3Localizer:
    """A client of a self-hosted SAM 3 Cog server; a cold Cloud Run instance takes minutes
    to answer the first request (SAM3_HOSTING.md), so the read timeout is the request
    timeout Cloud Run is deployed with. ``timeout_s`` makes an interactive one: that read
    timeout and one retry (:data:`INTERACTIVE_RETRY_DELAYS_S`)."""
    if timeout_s is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(COG_TIMEOUT_S, connect=10.0))
        delays = None
    else:
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0))
        delays = INTERACTIVE_RETRY_DELAYS_S
    return CogSam3Localizer(
        client=client,
        url=url,
        version=version,
        token_source=token_source,
        concurrency=concurrency,
        usd_per_second=usd_per_second,
        retry_delays=delays,
    )


# --------------------------------------------------------------------------- fake


@dataclass
class FakeLocalizer:
    """Scripted instances per phrase text, for tests: ``script[text]`` is a list of
    instances, or a function of the image returning one. Records every call; applies
    the threshold and the same per-label dedupe as the real localizer."""

    script: Mapping[str, Sequence[Instance] | Callable[[PreparedImage], Sequence[Instance]]] = (
        field(default_factory=dict)
    )
    seconds_per_call: float = 0.0
    provider: str = "fake"
    model: str = "fake-sam3"
    version: str = "fake"
    usd_per_second: float = L40S_USD_PER_SECOND
    calls: list[tuple[str, tuple[str, ...], float]] = field(default_factory=list)
    prompted: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _instances(self, image: PreparedImage, text: str) -> list[Instance]:
        entry = self.script.get(text, ())
        return list(entry(image) if callable(entry) else entry)

    async def localize(
        self,
        image: PreparedImage,
        phrases: Sequence[Phrase],
        *,
        threshold: float,
        dedupe: bool = True,
    ) -> Localization:
        texts = tuple(p.text for p in phrases)
        self.calls.append((image.sha256, texts, threshold))
        found = [
            i.model_copy(update={"label": p.label, "phrase": p.text})
            for p in phrases
            for i in self._instances(image, p.text)
            if i.score >= threshold
        ]
        call = LocalizeCall(
            prompts=texts,
            predict_seconds=self.seconds_per_call,
            cost_usd=round(self.seconds_per_call * L40S_USD_PER_SECOND, 6),
            status="succeeded",
        )
        return Localization(
            image_sha256=image.sha256,
            width=image.width,
            height=image.height,
            instances=tuple(suppress(found, PHRASE_DEDUPE_IOU) if dedupe else found),
            calls=(call,) if phrases else (),
        )

    async def segment_prompt(
        self,
        image: PreparedImage,
        *,
        label: ComponentLabel,
        points: Sequence[Point] = (),
        boxes: Sequence[Box] = (),
        negative_points: Sequence[Point] = (),
        threshold: float = DEFAULT_THRESHOLD,
        phrase_set: str = DEFAULT_PHRASE_SET,
    ) -> Prompted:
        phrases = prompt_phrases(label, phrase_set)
        self.prompted.append(
            (
                image.sha256,
                {
                    "label": label,
                    "points": list(points),
                    "boxes": list(boxes),
                    "phrases": [p.text for p in phrases],
                    "threshold": threshold,
                },
            )
        )
        found = [
            i.model_copy(update={"label": label, "phrase": p.text})
            for p in phrases
            for i in self._instances(image, p.text)
            if i.score >= threshold
        ]
        call = LocalizeCall(
            prompts=tuple(p.text for p in phrases),
            predict_seconds=self.seconds_per_call,
            cost_usd=round(self.seconds_per_call * L40S_USD_PER_SECOND, 6),
            status="succeeded",
        )
        return Prompted(
            instances=tuple(pick_prompted(found, points, boxes)),
            returned=len(found),
            calls=(call,),
        )


# --------------------------------------------------------------------------- tiling


@dataclass(frozen=True)
class Tile:
    """A crop of the full image: pixels ``[x0, x0 + crop_width) x [y0, y0 + crop_height)``
    sent as ``image``, resized by ``scale`` (sent size = crop size x scale)."""

    image: PreparedImage
    offset: tuple[int, int]
    crop_size: tuple[int, int]
    full_size: tuple[int, int]
    scale: float = 1.0

    @property
    def crop(self) -> bytes:
        return self.image.jpeg


def _spans(length: int, grid: int, overlap: float) -> list[tuple[int, int]]:
    if grid == 1:
        return [(0, length)]
    size = math.ceil(length / (grid - (grid - 1) * overlap))
    size = min(size, length)
    starts = [round(i * (length - size) / (grid - 1)) for i in range(grid)]
    return [(s, s + size) for s in starts]


def tiles(
    image: PreparedImage,
    grid: int = 2,
    overlap: float = 0.2,
    *,
    long_side: int | None = None,
    jpeg_quality: int = 95,
) -> list[Tile]:
    """``grid x grid`` overlapping crops covering the image exactly (the last row and
    column end on the border). Adjacent tiles overlap by ``overlap`` of a tile's side.
    ``long_side`` resizes each crop so its longer side is that many pixels (default: the
    crop as is; SAM 3 resizes its input to 1008 px anyway, so a crop is already zoomed)."""
    if grid < 1 or not 0.0 <= overlap < 1.0:
        raise ValueError("grid must be >= 1 and overlap in [0, 1)")
    full = Image.open(io.BytesIO(image.jpeg)).convert("RGB")
    out: list[Tile] = []
    for y0, y1 in _spans(image.height, grid, overlap):
        for x0, x1 in _spans(image.width, grid, overlap):
            crop = full.crop((x0, y0, x1, y1))
            width, height = crop.size
            scale = 1.0
            if long_side is not None and long_side != max(width, height):
                scale = long_side / max(width, height)
                crop = crop.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    Image.Resampling.LANCZOS,
                )
            buffer = io.BytesIO()
            crop.save(buffer, format="JPEG", quality=jpeg_quality)
            out.append(
                Tile(
                    image=PreparedImage(
                        jpeg=buffer.getvalue(),
                        width=crop.width,
                        height=crop.height,
                        original_width=width,
                        original_height=height,
                        original_format="JPEG",
                    ),
                    offset=(x0, y0),
                    crop_size=(width, height),
                    full_size=(image.width, image.height),
                    scale=scale,
                )
            )
    return out


def _to_full(instance: Instance, tile: Tile) -> tuple[Instance, bool]:
    """A tile instance in full-image coordinates, and whether it touches an interior edge
    of the tile (an edge that is not the image border: the object may be cut there)."""
    (x0, y0), (cw, ch), (fw, fh) = tile.offset, tile.crop_size, tile.full_size
    box = instance.box
    full_box = Box(
        x1=(x0 + box.x1 * cw) / fw,
        y1=(y0 + box.y1 * ch) / fh,
        x2=min(1.0, (x0 + box.x2 * cw) / fw),
        y2=min(1.0, (y0 + box.y2 * ch) / fh),
    )
    mask: Rle | None = None
    if instance.mask is not None:
        local = instance.mask.decode()
        if local.shape != (ch, cw):
            resized = Image.fromarray(local.astype(np.uint8) * 255).resize(
                (cw, ch), Image.Resampling.NEAREST
            )
            local = np.asarray(resized) > 127
        canvas = np.zeros((fh, fw), dtype=bool)
        canvas[y0 : y0 + ch, x0 : x0 + cw] = local
        mask = Rle.encode(canvas)
    area = mask.area() / (fw * fh) if mask is not None else full_box.area
    eps = 1e-9
    cut = (
        (box.x1 <= eps and x0 > 0)
        or (box.y1 <= eps and y0 > 0)
        or (box.x2 >= 1 - eps and x0 + cw < fw)
        or (box.y2 >= 1 - eps and y0 + ch < fh)
    )
    return instance.model_copy(update={"box": full_box, "mask": mask, "area": min(1.0, area)}), cut


def merge_tiled(
    full: Localization,
    tiled: Sequence[tuple[Tile, Localization]],
    *,
    iou_threshold: float = TILE_NMS_IOU,
    metric: MergeMetric = "iou",
) -> Localization:
    """The full-image localization plus every tile's instances mapped back, with
    duplicates within a label suppressed (complete instances rank before ones cut by a
    tile edge, then by score; the full-image pass wins ties)."""
    pool: list[Instance] = list(full.instances)
    cut_flags: list[bool] = [False] * len(pool)
    calls = list(full.calls)
    for tile, localization in tiled:
        if tile.full_size != (full.width, full.height):
            raise ValueError("a tile does not belong to this image")
        for instance in localization.instances:
            mapped, cut = _to_full(instance, tile)
            pool.append(mapped)
            cut_flags.append(cut)
        calls += localization.calls
    kept = suppress(
        pool, iou_threshold, metric=metric, rank=lambda i: (cut_flags[i], -pool[i].score, i)
    )
    return full.model_copy(update={"instances": tuple(kept), "calls": tuple(calls)})


# --------------------------------------------------------------------------- pipeline


async def localize_photo(
    image: PreparedImage,
    labels_from_vlm: Sequence[ComponentLabel | str],
    localizer: Localizer,
    *,
    tiling: bool,
    threshold: float = DEFAULT_THRESHOLD,
    grid: int = 2,
    overlap: float = 0.2,
    merge_metric: MergeMetric = "iou",
    phrase_set: str = DEFAULT_PHRASE_SET,
    dedupe: bool = True,
) -> Localization:
    """Prompt (VLM labels | COUNTABLE) on the full image and, with ``tiling``, the
    countable phrases on ``grid x grid`` tiles too; merged into one localization.
    ``dedupe=False`` (full frame only) keeps every phrase's instances as returned."""
    phrases = phrases_for(prompt_labels(labels_from_vlm), phrase_set)
    if not tiling:
        if not dedupe:
            return await localizer.localize(image, phrases, threshold=threshold, dedupe=False)
        return await localizer.localize(image, phrases, threshold=threshold)
    if not dedupe:
        raise ValueError("dedupe=False is for the full-frame phrase experiment, not tiling")
    countable = [p for p in phrases if p.label in COUNTABLE]
    pieces = tiles(image, grid, overlap)
    full, *per_tile = await asyncio.gather(
        localizer.localize(image, phrases, threshold=threshold),
        *(localizer.localize(t.image, countable, threshold=threshold) for t in pieces),
    )
    return merge_tiled(full, list(zip(pieces, per_tile, strict=True)), metric=merge_metric)


__all__ = [
    "CANDIDATE_THRESHOLD",
    "COUNTABLE",
    "COUNT_THRESHOLDS",
    "COUNT_THRESHOLD_SETS",
    "COUNT_THRESHOLD_DEFAULT",
    "SURFACE_LABELS",
    "PHRASES",
    "DEFAULT_PHRASE_SET",
    "PHRASE_SETS",
    "PROMPT_BOX_IOU",
    "Box",
    "FakeLocalizer",
    "Instance",
    "Localization",
    "LocalizeCall",
    "Localizer",
    "LocalizerBadResponse",
    "LocalizerError",
    "LocalizerUnavailable",
    "Phrase",
    "Point",
    "Prompted",
    "CogSam3Localizer",
    "ReplicateSam3Localizer",
    "Rle",
    "Tile",
    "best_box_match",
    "box_iou",
    "class_counts",
    "count_threshold",
    "counts",
    "localize_photo",
    "merge_tiled",
    "object_labels",
    "pick_prompted",
    "prompt_phrases",
    "tiles",
    "under_point",
]
