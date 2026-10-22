"""An engineer's selection on a photo, resolved against the photo's stored instances.

The rooms review lets an engineer add an object the pipeline did not count
(``POST /v1/studies/{id}/photos/{document_id}/objects``). These pure functions decide what
a selection becomes; the route runs them on the photo's rows and ``evals.manual_add``
replays them on the real photos, so the eval measures this code.

* A drawn box (:func:`plan_box`): the stored instance of the label overlapping it best at
  box IoU >= :data:`~basis.vision.localize.PROMPT_BOX_IOU` (counted: nothing to add;
  an uncounted candidate: promote it with its mask). Otherwise SAM 3 is prompted with the
  label's phrases and the box (:meth:`~basis.vision.localize.Localizer.segment_prompt`,
  which keeps the returned instance overlapping the box best at IoU >= 0.5 and score
  >= 0.5); when that keeps nothing, the box itself is stored. Stored candidates exist only
  from :data:`~basis.vision.localize.CANDIDATE_THRESHOLD`, and on the 52 real photos 13 of
  the 43 objects the pipeline missed had none: the prompt is what outlines those.
* A tap (:func:`plan_point`): the highest-scoring stored instance of the label under the
  point. Nothing under it is :class:`Unresolved` (the engineer draws a box instead): a
  SAM 3 point prompt picked the right object for only about 0.79 of the misses and a wrong
  one for some, where a box picked the right one for 0.98 or more and never a wrong one.
* A candidate by id (the review previews the candidates under a tap and lets the
  engineer cycle through them): promoted when uncounted, otherwise nothing to add.

The duplicate guard (:func:`duplicate_of`): an instance about to be stored that covers a
counted instance of its label at intersection over the smaller (:func:`overlap`) >=
:data:`DUPLICATE_IOS` is that counted instance, not a new one. On the real photos the
guard blocked 4 of the 8 duplicates the selections would have created on objects already
counted, and 1 of 158 correct adds (a counted mask that covered two lights): so the
engineer can add anyway (``allow_duplicate``), which also skips counted instances when
resolving (a box around one of two lights the counted mask covers then reaches SAM 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from basis.vision.localize import (
    Box,
    Instance,
    Point,
    best_box_match,
    box_intersection,
    mask_ios,
    under_point,
)
from basis.vision.schemas import ComponentLabel

# Intersection over the smaller of the two (mask, or box when either has no mask), SAHI's
# default match threshold. Measured on the real photos (8 would-be duplicates, 158 correct
# adds): 0.5 and 0.8 block the same 4 duplicates and 1 add; 0.3 also blocks the other 4,
# which are four taps on one window (its candidate covers a counted mask at 0.36), with the
# same 1 add blocked. One object is too little to move the threshold on; evals.manual_add
# reports both.
DUPLICATE_IOS: Final = 0.5


@dataclass(frozen=True, slots=True)
class Stored:
    """A stored instance of the photo as the resolver sees it."""

    instance: Instance
    counted: bool


@dataclass(frozen=True, slots=True)
class Counted:
    """The selection is a counted instance already (``index`` into the stored ones)."""

    index: int


@dataclass(frozen=True, slots=True)
class Candidate:
    """The selection is an uncounted stored candidate, to be promoted with its mask."""

    index: int


@dataclass(frozen=True, slots=True)
class Prompt:
    """No stored instance matches the box: prompt SAM 3 with it (or store the box)."""


@dataclass(frozen=True, slots=True)
class Unresolved:
    """Nothing under the tap."""


Plan = Counted | Candidate | Prompt | Unresolved


@dataclass(frozen=True, slots=True)
class Duplicate:
    """The counted instance a new one would duplicate, and how much it covers."""

    index: int
    overlap: float


def _pool(stored: Sequence[Stored], label: ComponentLabel, allow_duplicate: bool) -> list[int]:
    """The stored instances of ``label`` a selection may resolve to: all of them, or only
    the uncounted candidates when the engineer adds anyway."""
    return [
        i
        for i, s in enumerate(stored)
        if s.instance.label is label and not (allow_duplicate and s.counted)
    ]


def _resolved(stored: Sequence[Stored], pool: list[int], found: int | None) -> Plan | None:
    if found is None:
        return None
    index = pool[found]
    return Counted(index) if stored[index].counted else Candidate(index)


def plan_box(
    stored: Sequence[Stored], label: ComponentLabel, box: Box, *, allow_duplicate: bool = False
) -> Plan:
    """A drawn box: the stored instance it matches (module doc), else :class:`Prompt`."""
    pool = _pool(stored, label, allow_duplicate)
    found = best_box_match([stored[i].instance for i in pool], box)
    return _resolved(stored, pool, found) or Prompt()


def plan_point(
    stored: Sequence[Stored], label: ComponentLabel, point: Point, *, allow_duplicate: bool = False
) -> Plan:
    """A tap: the stored instance under it (module doc), else :class:`Unresolved`."""
    pool = _pool(stored, label, allow_duplicate)
    found = under_point([stored[i].instance for i in pool], point)
    return _resolved(stored, pool, found) or Unresolved()


def plan_candidate(stored: Sequence[Stored], index: int) -> Plan:
    """A stored instance chosen by id: promote it when uncounted."""
    return Counted(index) if stored[index].counted else Candidate(index)


def bare_box(label: ComponentLabel, box: Box) -> Instance:
    """A drawn box as an instance with no mask (what is stored when nothing outlines it)."""
    return Instance(
        label=label, phrase=label.value, score=1.0, box=box, area=box.area, source="manual"
    )


def overlap(a: Instance, b: Instance) -> float:
    """Intersection over the smaller of the two: of the masks when both have one of the
    same size, else of the boxes (1.0 when one lies inside the other)."""
    if a.mask is not None and b.mask is not None:
        if (a.mask.height, a.mask.width) == (b.mask.height, b.mask.width):
            return mask_ios(a.mask.decode(), b.mask.decode())
    smaller = min(a.box.area, b.box.area)
    return box_intersection(a.box, b.box) / smaller if smaller > 0 else 0.0


def duplicate_of(stored: Sequence[Stored], new: Instance) -> Duplicate | None:
    """The counted instance of ``new``'s label it covers most, when that is at least
    :data:`DUPLICATE_IOS` (the first on ties); None when it duplicates nothing."""
    best: Duplicate | None = None
    for index, s in enumerate(stored):
        if not s.counted or s.instance.label is not new.label:
            continue
        value = overlap(new, s.instance)
        if value >= DUPLICATE_IOS and (best is None or value > best.overlap):
            best = Duplicate(index=index, overlap=value)
    return best


@dataclass(frozen=True, slots=True)
class SuggestedCount:
    """A room's count of a label against what its photos show (:func:`suggested_counts`)."""

    label: str
    inventory_count: int
    photo_count: int
    manual_count: int
    document_id: uuid.UUID

    @property
    def suggested(self) -> int:
        return max(self.inventory_count, self.photo_count)


def suggested_counts(
    inventory: Mapping[str, int], photos: Mapping[uuid.UUID, Mapping[str, tuple[int, int]]]
) -> list[SuggestedCount]:
    """Per label counted on any photo of a room, in label order: the room's ``inventory``
    count, the most counted instances (model and manual, ``(counted, manual)`` per photo
    and label) in one photo, and so the suggestion max(inventory, photo). A room holds at
    least what one photo of it shows; summing photos would count an object once per photo
    it is in (EVAL_REPORT section 6), so the largest photo is the defensible floor. Ties
    between photos go to the one with more manual instances, then the first by id."""
    best: dict[str, tuple[int, int, uuid.UUID]] = {}
    for document_id in sorted(photos):
        for label, (total, manual) in photos[document_id].items():
            if total <= 0:
                continue
            current = best.get(label)
            if current is None or (total, manual) > current[:2]:
                best[label] = (total, manual, document_id)
    return [
        SuggestedCount(
            label=label,
            inventory_count=inventory.get(label, 0),
            photo_count=total,
            manual_count=manual,
            document_id=document_id,
        )
        for label, (total, manual, document_id) in sorted(best.items())
    ]


__all__ = [
    "DUPLICATE_IOS",
    "Candidate",
    "Counted",
    "Duplicate",
    "Plan",
    "Prompt",
    "Stored",
    "SuggestedCount",
    "Unresolved",
    "bare_box",
    "duplicate_of",
    "overlap",
    "plan_box",
    "plan_candidate",
    "plan_point",
    "suggested_counts",
]
