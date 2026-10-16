"""How an engineer's tap or box resolves against the stored instances (no database).

``basis.api.routes.objects.resolve``: same label only; a tap picks the instance whose mask
(or, without a mask, box) covers the point, highest score first; a box picks the instance it
overlaps best, but only at IoU >= 0.5; the stored row is returned, not a copy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest

from basis.api.routes.objects import BOX_MATCH_IOU, drawn_box, object_out, resolve
from basis.api.schemas.objects import BoxIn, BoxSelection, PointIn, PointSelection
from basis.db.models import PhotoObject
from basis.vision.localize import Rle
from basis.vision.schemas import ComponentLabel

DOC = uuid.uuid4()


def row(
    label: str,
    box: tuple[float, float, float, float],
    *,
    score: float | None = 0.9,
    mask_rect: tuple[int, int, int, int] | None = None,
    source: str = "model",
    counted: bool = True,
) -> PhotoObject:
    mask = None
    if mask_rect is not None:
        grid = np.zeros((10, 10), dtype=bool)
        r0, r1, c0, c1 = mask_rect
        grid[r0:r1, c0:c1] = True
        mask = Rle.encode(grid).model_dump(mode="json")
    x1, y1, x2, y2 = box
    return PhotoObject(
        id=uuid.uuid4(),
        study_id=uuid.uuid4(),
        document_id=DOC,
        revision=1,
        label=label,
        phrase=label,
        score=score,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        mask=mask,
        area=(x2 - x1) * (y2 - y1),
        source=source,
        counted=counted,
        created_at=datetime.now(UTC),
    )


def tap(label: str, x: float, y: float) -> PointSelection:
    return PointSelection(kind="point", label=ComponentLabel(label), point=PointIn(x=x, y=y))


def drawn(label: str, x1: float, y1: float, x2: float, y2: float) -> BoxSelection:
    return BoxSelection(
        kind="box", label=ComponentLabel(label), box=BoxIn(x1=x1, y1=y1, x2=x2, y2=y2)
    )


def test_a_tap_inside_a_mask_resolves_to_that_row() -> None:
    window = row("window", (0.1, 0.1, 0.5, 0.5), mask_rect=(1, 5, 1, 5))
    other = row("window", (0.6, 0.6, 0.9, 0.9), mask_rect=(6, 9, 6, 9))
    assert resolve([window, other], tap("window", 0.3, 0.3)) is window
    assert resolve([window, other], tap("window", 0.75, 0.75)) is other


def test_a_tap_inside_the_box_but_outside_the_mask_resolves_to_nothing() -> None:
    # The mask covers pixel rows and columns 1..4 of 10; (0.02, 0.02) is pixel (0, 0):
    # inside the box, outside the mask. The mask, not the box, decides.
    window = row("window", (0.0, 0.0, 0.5, 0.5), mask_rect=(1, 5, 1, 5))
    assert resolve([window], tap("window", 0.02, 0.02)) is None


def test_without_a_mask_the_box_decides_and_the_highest_score_wins() -> None:
    low = row("light_fixture", (0.1, 0.1, 0.6, 0.6), score=0.4)
    high = row("light_fixture", (0.2, 0.2, 0.7, 0.7), score=0.8)
    assert resolve([low, high], tap("light_fixture", 0.4, 0.4)) is high


def test_only_the_selected_label_is_considered() -> None:
    door = row("interior_door", (0.1, 0.1, 0.5, 0.9))
    assert resolve([door], tap("exterior_door", 0.3, 0.5)) is None


def test_a_box_matches_only_at_iou_0_5() -> None:
    window = row("window", (0.1, 0.1, 0.5, 0.5))
    assert resolve([window], drawn("window", 0.12, 0.1, 0.5, 0.52)) is window
    # Overlapping, but well below the match criterion: a different object next to it.
    assert resolve([window], drawn("window", 0.4, 0.1, 0.8, 0.5)) is None
    assert BOX_MATCH_IOU == 0.5


def test_box_corners_are_sorted() -> None:
    box = drawn_box(drawn("window", 0.8, 0.9, 0.2, 0.1))
    assert (box.x1, box.y1, box.x2, box.y2) == (0.2, 0.1, 0.8, 0.9)


def test_a_manual_box_without_score_is_a_candidate_too() -> None:
    manual = row("electrical_outlet", (0.7, 0.7, 0.8, 0.8), score=None, source="manual")
    assert resolve([manual], tap("electrical_outlet", 0.75, 0.75)) is manual


def test_nothing_stored_resolves_to_nothing() -> None:
    assert resolve([], tap("window", 0.5, 0.5)) is None
    assert resolve([], drawn("window", 0.1, 0.1, 0.2, 0.2)) is None


def test_object_out_carries_the_geometry_and_the_mask() -> None:
    window = row("window", (0.1, 0.2, 0.5, 0.6), mask_rect=(2, 6, 1, 5), counted=False)
    out = object_out(window)
    assert (out.box.x1, out.box.y1, out.box.x2, out.box.y2) == pytest.approx((0.1, 0.2, 0.5, 0.6))
    assert out.mask is not None and (out.mask.height, out.mask.width) == (10, 10)
    assert sum(out.mask.counts) == 100
    assert out.counted is False and out.source == "model"
