"""Resolving an engineer's selection (:mod:`basis.vision.selection`): what a box, a tap or a
candidate becomes, the duplicate guard (with the two-lights case that needs "add anyway"),
and the suggested room counts."""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from basis.vision.localize import Box, Instance, Point, Rle
from basis.vision.schemas import ComponentLabel
from basis.vision.selection import (
    DUPLICATE_IOS,
    Candidate,
    Counted,
    Duplicate,
    Prompt,
    Stored,
    SuggestedCount,
    Unresolved,
    bare_box,
    duplicate_of,
    overlap,
    plan_box,
    plan_candidate,
    plan_point,
    suggested_counts,
)

L = ComponentLabel
SIZE = 100


def rect(label: ComponentLabel, score: float, *rects: tuple[int, int, int, int]) -> Instance:
    """An instance whose mask is the union of pixel rectangles on a 100 x 100 image and
    whose box bounds them."""
    grid = np.zeros((SIZE, SIZE), dtype=bool)
    for x1, y1, x2, y2 in rects:
        grid[y1:y2, x1:x2] = True
    rle = Rle.encode(grid)
    left, top = min(r[0] for r in rects), min(r[1] for r in rects)
    right, bottom = max(r[2] for r in rects), max(r[3] for r in rects)
    return Instance(
        label=label,
        phrase=label.value,
        score=score,
        box=Box.from_pixels(left, top, right, bottom, SIZE, SIZE),
        mask=rle,
        area=rle.area() / SIZE**2,
    )


def box(x1: float, y1: float, x2: float, y2: float) -> Box:
    return Box(x1=x1, y1=y1, x2=x2, y2=y2)


COUNTED_WINDOW = Stored(rect(L.WINDOW, 0.9, (10, 10, 40, 40)), counted=True)
CANDIDATE_WINDOW = Stored(rect(L.WINDOW, 0.4, (60, 10, 90, 40)), counted=False)
COUNTED_OUTLET = Stored(rect(L.ELECTRICAL_OUTLET, 0.8, (60, 10, 90, 40)), counted=True)
STORED = [COUNTED_WINDOW, CANDIDATE_WINDOW, COUNTED_OUTLET]


# --------------------------------------------------------------------------- plans


def test_a_box_resolves_to_the_stored_instance_of_its_label_at_iou_half() -> None:
    assert plan_box(STORED, L.WINDOW, box(0.1, 0.1, 0.4, 0.4)) == Counted(0)
    assert plan_box(STORED, L.WINDOW, box(0.6, 0.1, 0.9, 0.42)) == Candidate(1)
    # The outlet has the candidate's exact box, but the selection is for a window.
    assert plan_box(STORED, L.ELECTRICAL_OUTLET, box(0.6, 0.1, 0.9, 0.4)) == Counted(2)
    # Next to the counted window (IoU 0.33): not it, so SAM 3 is asked.
    assert plan_box(STORED, L.WINDOW, box(0.25, 0.1, 0.55, 0.4)) == Prompt()
    assert plan_box([], L.WINDOW, box(0.1, 0.1, 0.4, 0.4)) == Prompt()


def test_adding_anyway_skips_the_counted_instances() -> None:
    drawn = box(0.1, 0.1, 0.4, 0.4)
    assert plan_box(STORED, L.WINDOW, drawn, allow_duplicate=True) == Prompt()
    assert plan_point(STORED, L.WINDOW, Point(x=0.2, y=0.2), allow_duplicate=True) == Unresolved()
    assert plan_point(STORED, L.WINDOW, Point(x=0.7, y=0.2), allow_duplicate=True) == Candidate(1)


def test_a_tap_resolves_to_the_instance_under_it_or_nothing() -> None:
    assert plan_point(STORED, L.WINDOW, Point(x=0.2, y=0.2)) == Counted(0)
    assert plan_point(STORED, L.WINDOW, Point(x=0.7, y=0.2)) == Candidate(1)
    assert plan_point(STORED, L.WINDOW, Point(x=0.5, y=0.9)) == Unresolved()
    # Two of the label under the tap: the higher score (the kitchen-cabinet run over a unit
    # is why the review cycles candidates instead of trusting the tap).
    run = Stored(rect(L.KITCHEN_CABINET, 0.8, (0, 50, 100, 100)), counted=False)
    unit = Stored(rect(L.KITCHEN_CABINET, 0.6, (0, 50, 30, 100)), counted=False)
    assert plan_point([unit, run], L.KITCHEN_CABINET, Point(x=0.1, y=0.7)) == Candidate(1)


def test_a_candidate_by_id_is_promoted_unless_counted() -> None:
    assert plan_candidate(STORED, 1) == Candidate(1)
    assert plan_candidate(STORED, 0) == Counted(0)


# --------------------------------------------------------------------------- duplicates


def test_two_lights_under_one_counted_mask_are_a_duplicate_until_added_anyway() -> None:
    """The measured false block: SAM 3 counted one mask over two adjacent lights; the
    engineer boxes one of them. The guard flags it (the single light lies inside the
    counted mask), which is why the request can say add anyway."""
    two = Stored(rect(L.LIGHT_FIXTURE, 0.8, (10, 10, 30, 30), (40, 10, 60, 30)), counted=True)
    one = rect(L.LIGHT_FIXTURE, 0.9, (40, 10, 60, 30))
    assert overlap(one, two.instance) == 1.0
    assert duplicate_of([two], one) == Duplicate(index=0, overlap=1.0)
    # Resolving the box around the second light: IoU with the two-light box is 0.4, so it
    # goes to SAM 3 whether or not the engineer adds anyway.
    drawn = box(0.4, 0.1, 0.6, 0.3)
    assert plan_box([two], L.LIGHT_FIXTURE, drawn) == Prompt()


def test_the_guard_uses_masks_then_boxes_and_only_counted_instances_of_the_label() -> None:
    counted = Stored(rect(L.WINDOW, 0.9, (0, 0, 40, 40)), counted=True)
    half = rect(L.WINDOW, 0.7, (20, 0, 60, 40))  # 800 of 1600 pixels over the counted mask
    assert overlap(half, counted.instance) == pytest.approx(0.5)
    assert duplicate_of([counted], half) == Duplicate(index=0, overlap=pytest.approx(0.5))
    less = rect(L.WINDOW, 0.7, (21, 0, 61, 40))
    assert overlap(less, counted.instance) < DUPLICATE_IOS
    assert duplicate_of([counted], less) is None
    # An uncounted candidate or another label never blocks.
    assert duplicate_of([Stored(counted.instance, counted=False)], half) is None
    other = Stored(rect(L.ELECTRICAL_OUTLET, 0.9, (0, 0, 40, 40)), counted=True)
    assert duplicate_of([other], half) is None
    # A bare box (no mask) is compared by boxes: inside the counted box = 1.0.
    drawn = bare_box(L.WINDOW, box(0.05, 0.05, 0.2, 0.2))
    assert drawn.mask is None and drawn.source == "manual"
    assert duplicate_of([counted], drawn) == Duplicate(index=0, overlap=1.0)
    # Masks of another size (never stored together) fall back to boxes too.
    small = Rle.encode(np.ones((10, 10), dtype=bool))
    resized = half.model_copy(update={"mask": small})
    assert overlap(resized, counted.instance) == pytest.approx(0.5)


def test_the_guard_names_the_counted_instance_covered_most() -> None:
    a = Stored(rect(L.WINDOW, 0.9, (0, 0, 40, 40)), counted=True)
    b = Stored(rect(L.WINDOW, 0.9, (30, 0, 50, 40)), counted=True)
    new = rect(L.WINDOW, 0.7, (30, 0, 50, 40))  # all of b, half of itself over a
    assert duplicate_of([a, b], new) == Duplicate(index=1, overlap=1.0)


# --------------------------------------------------------------------------- counts


def test_suggested_counts_take_the_largest_photo_and_never_go_below_the_inventory() -> None:
    p1, p2 = uuid.UUID(int=1), uuid.UUID(int=2)
    photos = {
        p1: {"window": (3, 0), "electrical_outlet": (2, 1)},
        p2: {"window": (5, 2), "light_fixture": (1, 1), "sink": (0, 0)},
    }
    found = suggested_counts({"window": 4, "electrical_outlet": 6, "toilet": 1}, photos)
    assert found == [
        SuggestedCount("electrical_outlet", 6, 2, 1, p1),
        SuggestedCount("light_fixture", 0, 1, 1, p2),
        SuggestedCount("window", 4, 5, 2, p2),
    ]
    assert [s.suggested for s in found] == [6, 1, 5]
    assert suggested_counts({"window": 2}, {}) == []


def test_suggested_count_ties_go_to_more_manual_then_the_first_photo() -> None:
    p1, p2, p3 = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)
    photos = {p3: {"window": (2, 1)}, p2: {"window": (2, 0)}, p1: {"window": (2, 1)}}
    [only] = suggested_counts({}, photos)
    assert (only.photo_count, only.manual_count, only.document_id) == (2, 1, p1)
