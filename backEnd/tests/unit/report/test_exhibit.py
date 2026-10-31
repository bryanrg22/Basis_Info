"""``basis.report.exhibit``: Exhibit A photographs with every counted object outlined and
numbered, captioned with the report lines it supports (STUDY_REPORT_SPEC 11.3)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from basis.report.exhibit import (
    LONG_SIDE,
    PALETTE,
    PhotoObjectView,
    caption,
    compose,
    exhibit_photo,
    line_index,
)
from basis.report.model import build_report
from basis.vision.localize import Rle

from .support import export


def jpeg(
    width: int = 400, height: int = 300, color: tuple[int, int, int] = (230, 230, 230)
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def pixels(data: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)


def near(rgb: np.ndarray, color: tuple[int, int, int], tolerance: int = 40) -> np.ndarray:
    return (np.abs(rgb - np.array(color)) <= tolerance).all(axis=-1)


def test_a_mask_is_outlined_at_its_edge_not_filled() -> None:
    mask = np.zeros((60, 80), dtype=np.uint8)
    mask[15:45, 20:60] = 1  # a rectangle in the middle of the 80 x 60 mask
    obj = PhotoObjectView("window", (0.25, 0.25, 0.75, 0.75), Rle.encode(mask).model_dump())
    out = pixels(compose(jpeg(), [obj]))
    color = near(out, PALETTE[0])
    # The mask scales to the 400 x 300 photograph: x 100-300, y 75-225.
    assert color[72:79, 130:280].any(axis=0).mean() > 0.9  # the top edge
    assert color[221:228, 130:280].any(axis=0).mean() > 0.9  # the bottom edge
    assert color[110:200, 97:104].any(axis=1).mean() > 0.9  # the left edge
    assert color[110:200, 296:303].any(axis=1).mean() > 0.9  # the right edge
    assert not color[100:200, 130:270].any()  # the inside stays the photograph


def test_without_a_mask_the_box_is_outlined() -> None:
    obj = PhotoObjectView("door", (0.1, 0.2, 0.5, 0.9), None)
    out = pixels(compose(jpeg(), [obj]))
    color = near(out, PALETTE[0])
    left_edge = color[100:250, 38:43]
    assert left_edge.any() and not color[150, 120:180].any()


def test_a_malformed_mask_falls_back_to_the_box() -> None:
    obj = PhotoObjectView("door", (0.1, 0.2, 0.5, 0.9), {"height": 2, "width": 2, "counts": [9]})
    assert near(pixels(compose(jpeg(), [obj])), PALETTE[0]).any()


def test_large_photographs_are_scaled_and_the_bytes_are_stable() -> None:
    big = jpeg(3000, 2000)
    objects = [PhotoObjectView("window", (0.1, 0.1, 0.3, 0.3), None)]
    out = compose(big, objects)
    assert max(Image.open(io.BytesIO(out)).size) == LONG_SIDE
    assert out == compose(big, objects)


def test_captions_name_each_number_and_its_report_lines() -> None:
    report = build_report(export())
    room_of = {"a1": "room-bed", "a3": "room-front", "a4": "room-rear", "a5": "room-side"}
    lines = line_index(report, room_of)
    objects = [
        PhotoObjectView("carpet", (0, 0, 1, 1), None),
        PhotoObjectView("landscaping", (0, 0, 1, 1), None),
        PhotoObjectView("chandelier", (0, 0, 1, 1), None),
    ]
    text = caption("IMG_1.jpg (Bedroom)", "room-bed", objects, lines)
    assert text == "IMG_1.jpg (Bedroom): 1 carpet (line 1); 2 landscaping; 3 chandelier"
    rear = caption("IMG_2.jpg (Rear)", "room-rear", objects[1:2], lines)
    assert rear == "IMG_2.jpg (Rear): 1 landscaping (line 3)"  # the folded site line
    assert caption("x", None, [], lines) == "x: no counted components"
    repeated = [PhotoObjectView("carpet", (0, 0, 1, 1), None)] * 3 + objects[1:2] + objects[:1]
    assert caption("y", "room-bed", repeated, lines) == (
        "y: 1-3 carpet (line 1); 4 landscaping; 5 carpet (line 1)"
    )
    photo = exhibit_photo(7, "IMG_1.jpg", jpeg(), "room-bed", objects[:1], lines)
    assert photo.name == "photo-007.jpg" and photo.caption.endswith("1 carpet (line 1)")
