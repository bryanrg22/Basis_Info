"""``basis.vision.marks``: Set-of-Mark proposals, drawing, keep/reject/add answers, points."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from basis.vision import marks as mk
from basis.vision.localize import Box, Instance, Point, Rle
from basis.vision.schemas import ComponentLabel

W, H = 40, 30


def mask_for(x1: int, y1: int, x2: int, y2: int) -> Rle:
    array = np.zeros((H, W), dtype=np.uint8)
    array[y1:y2, x1:x2] = 1
    return Rle.encode(array)


def inst(label: str, score: float, px: tuple[int, int, int, int], *, mask: bool = True) -> Instance:
    x1, y1, x2, y2 = px
    return Instance(
        label=ComponentLabel(label),
        phrase=label,
        score=score,
        box=Box(x1=x1 / W, y1=y1 / H, x2=x2 / W, y2=y2 / H),
        mask=mask_for(*px) if mask else None,
        area=(x2 - x1) * (y2 - y1) / (W * H),
    )


def jpeg(width: int = 80, height: int = 60) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- select_marks


def test_select_marks_filters_merges_across_labels_and_numbers_in_reading_order() -> None:
    instances = [
        inst("window", 0.9, (20, 20, 30, 28)),  # bottom right
        inst("interior_door", 0.8, (2, 2, 10, 20)),  # top left
        inst("exterior_door", 0.6, (2, 2, 10, 21)),  # the same door, another label: merged
        inst("light_fixture", 0.35, (20, 2, 24, 4)),  # top right
        inst("electrical_outlet", 0.2, (30, 25, 32, 27)),  # under the score threshold
        inst("sink", 0.95, (10, 10, 20, 20)),  # not a mark label
        inst("kitchen_cabinet", 0.5, (21, 21, 25, 25)),  # inside the window box: low IoU, kept
    ]
    marks = mk.select_marks(instances, min_score=0.3, nms=0.6)
    # rows of a tenth of the image (by box centre), then left to right
    assert [(m.number, m.label) for m in marks] == [
        (1, "light_fixture"),
        (2, "interior_door"),
        (3, "kitchen_cabinet"),
        (4, "window"),
    ]


def test_select_marks_caps_at_the_highest_scores_and_uses_boxes_without_masks() -> None:
    instances = [
        inst("window", 0.9, (0, 0, 10, 10), mask=False),
        inst("window", 0.8, (0, 0, 10, 11), mask=False),  # box IoU > 0.6 with the first
        inst("window", 0.7, (20, 0, 30, 10)),
        inst("window", 0.4, (0, 20, 10, 30)),
    ]
    marks = mk.select_marks(instances, min_score=0.3, max_marks=2)
    assert [m.instance.score for m in marks] == [0.9, 0.7]
    assert mk.mark_list_text(marks) == "1: window\n2: window"


# --------------------------------------------------------------------------- drawing


@pytest.mark.parametrize("style", ["box", "outline"])
def test_render_marks_enlarges_and_draws_in_colour(style: str) -> None:
    marks = mk.select_marks([inst("window", 0.9, (10, 10, 30, 25))])
    out = mk.render_marks(jpeg(), marks, style=style, long_side=160)  # type: ignore[arg-type]
    with Image.open(io.BytesIO(out)) as image:
        rgb = np.asarray(image.convert("RGB")).astype(int)
    assert rgb.shape == (120, 160, 3)
    # the first palette colour (red) is on the mark's edge and on its tag; the photo is gray
    red = (rgb[..., 0] > 180) & (rgb[..., 1] < 90) & (rgb[..., 2] < 120)
    assert red.sum() > 50
    # the object's middle is not painted (a tag sits outside the box when there is room)
    assert not red[50:80, 60:100].any()


def test_render_marks_with_no_marks_returns_the_enlarged_photo() -> None:
    out = mk.render_marks(jpeg(), [], long_side=100)
    with Image.open(io.BytesIO(out)) as image:
        assert image.size == (100, 75)


# --------------------------------------------------------------------------- answers


def test_coordinates_in_percent_are_read_as_fractions_and_others_clamped() -> None:
    assert mk.AddedObject(label="window", x=50, y=0.25).x == 0.5
    clamped = mk.PointedObject(label="window", x=-0.2, y=140)
    assert (clamped.x, clamped.y) == (0.0, 1.0)
    with pytest.raises(ValidationError):
        mk.AddedObject(label="sink", x=0.1, y=0.1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        mk.MarkVerdict(mark=1, verdict="keep")  # type: ignore[arg-type]


def test_tally_counts_kept_by_the_model_label_plus_added() -> None:
    marks = mk.select_marks(
        [
            inst("window", 0.9, (0, 0, 8, 8)),
            inst("light_fixture", 0.8, (20, 0, 24, 4)),
            inst("interior_door", 0.7, (30, 0, 38, 20)),
            inst("window", 0.6, (10, 20, 18, 28)),
        ]
    )
    review = mk.MarksReview(
        marks=[
            mk.MarkVerdict(mark=1, verdict="window"),
            mk.MarkVerdict(mark=2, verdict="outdoor_lighting"),  # relabelled
            mk.MarkVerdict(mark=2, verdict="not_countable"),  # repeated: first verdict wins
            mk.MarkVerdict(mark=3, verdict="part_or_group"),
            mk.MarkVerdict(mark=9, verdict="window"),  # not drawn
        ],
        added=[
            mk.AddedObject(label="window", x=0.5, y=0.5),
            mk.AddedObject(label="electrical_outlet", x=0.1, y=0.9),
        ],
    )
    tally = mk.tally_review(review, marks)
    assert tally.counts == {"electrical_outlet": 1, "outdoor_lighting": 1, "window": 2}
    assert [(m.number, label) for m, label in tally.kept] == [
        (1, "window"),
        (2, "outdoor_lighting"),
    ]
    assert tally.rejected == {"part_or_group": 1}
    assert (tally.unanswered, tally.unknown, tally.repeated) == ([4], [9], [2])


def test_point_answer_counts() -> None:
    answer = mk.PointAnswer(
        objects=[
            mk.PointedObject(label="window", x=0.1, y=0.1),
            mk.PointedObject(label="window", x=0.3, y=0.1),
            mk.PointedObject(label="interior_door", x=0.5, y=0.5),
        ],
        counts=mk.StatedCounts(
            window=3,
            interior_door=1,
            exterior_door=0,
            light_fixture=0,
            outdoor_lighting=0,
            electrical_outlet=0,
            kitchen_cabinet=0,
        ),
    )
    assert mk.point_counts(answer.objects) == {"interior_door": 1, "window": 2}
    assert mk.stated_counts(answer) == {"interior_door": 1, "window": 3}
    assert mk.add_counts({"a": 1}, {"a": 2, "b": 1}) == {"a": 3, "b": 1}


def test_parse_molmo_points() -> None:
    text = (
        'Sure. <points x1="10.5" y1="20" x2="30" y2="40.5" alt="windows">windows</points> and '
        '<point x="50" y="60" alt="door">door</point> <point x="120" y="5">bad</point> '
        '<point x="abc" y="5">bad</point> <POINT X="1" Y="2">x</POINT>'
    )
    assert mk.parse_molmo_points(text) == [
        (0.105, 0.2),
        (0.3, 0.405),
        (0.5, 0.6),
    ]
    assert mk.parse_molmo_points("There are none.") == []


# --------------------------------------------------------------------------- resolve_points


def p(x: int, y: int) -> Point:
    return Point(x=(x + 0.5) / W, y=(y + 0.5) / H)


def test_resolve_points_kept_inside_pool_prompted_unmasked_and_duplicates() -> None:
    kept_lamp = inst("light_fixture", 0.9, (0, 0, 6, 6))
    pool = [
        inst("outdoor_lighting", 0.4, (0, 0, 6, 6)),  # under the kept fixture
        inst("window", 0.3, (10, 10, 20, 20)),
        inst("window", 0.6, (12, 12, 18, 18)),  # inside the larger window: higher score wins
        inst("window", 0.2, (25, 0, 35, 10)),
        inst("interior_door", 0.5, (30, 15, 38, 29)),
    ]
    prompted = {5: inst("window", 0.7, (0, 20, 8, 29))}
    points = [
        ("outdoor_lighting", p(2, 2)),  # 0: inside a counted light (same family)
        ("window", p(15, 15)),  # 1: pool, the 0.6 window
        ("window", p(16, 16)),  # 2: the same mask: duplicate of 1
        ("window", p(26, 1)),  # 3: pool, the 0.2 window
        ("window", p(34, 20)),  # 4: only a door is there: other family, so no mask
        ("window", p(3, 25)),  # 5: no candidate: the prompted instance
        ("exterior_door", p(33, 20)),  # 6: pool door (family door)
    ]
    resolved = mk.resolve_points(points, pool, counted=[kept_lamp], prompted=prompted)
    assert [r.status for r in resolved] == [
        "kept_inside",
        "pool",
        "duplicate",
        "pool",
        "unmasked",
        "prompted",
        "pool",
    ]
    assert resolved[1].instance is not None and resolved[1].instance.score == 0.6
    assert resolved[2].duplicate_of == 1
    assert mk.resolved_counts(resolved, include_unmasked=False) == {
        "exterior_door": 1,
        "window": 3,
    }
    assert mk.resolved_counts(resolved) == {"exterior_door": 1, "window": 4}


def test_resolve_points_overlapping_masks_are_one_object() -> None:
    pool = [inst("window", 0.9, (0, 0, 10, 10)), inst("window", 0.5, (0, 0, 10, 11))]
    # each point lies only in its own mask's extra row/area? both cover (5,5); the second
    # point is only in the second mask (row 10), whose IoU with the first is 100/110
    resolved = mk.resolve_points([("window", p(5, 5)), ("window", p(5, 10))], pool)
    assert [r.status for r in resolved] == ["pool", "duplicate"]
    looser = mk.resolve_points([("window", p(5, 5)), ("window", p(5, 10))], pool, overlap=0.95)
    assert [r.status for r in looser] == ["pool", "pool"]


def test_family() -> None:
    assert mk.family("exterior_door") == mk.family("interior_door") == "door"
    assert mk.family("outdoor_lighting") == mk.family("light_fixture") == "light"
    assert mk.family("window") == "window"
