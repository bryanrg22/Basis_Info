"""``basis.extraction.sketch_outline``: the sketch's outline from the PDF's line work.

The geometry (closed loops from segments in any drawing order, with strays dropped), the
belief rule (a loop must reproduce a verified table row's area *and* show the dimensions the
row's calculation prints), and the two perimeter fields, on fabricated PDFs built by
``sketch_pdf`` (EVAL_REPORT 100). Every failure writes no field and records why.
"""

from __future__ import annotations

import io
import random
from collections.abc import Sequence
from decimal import Decimal

import pytest
from PIL import Image

from basis.extraction import sketch_outline
from basis.extraction.pdf_text import read_pdf
from basis.extraction.sketch import (
    Rectangle,
    SketchArea,
    read_document_sketch,
    read_sketch,
    sketch_fields,
)
from basis.extraction.sketch_outline import (
    GROUND_PERIMETER,
    SOURCE,
    UPPER_PERIMETER,
    Loop,
    OutlineReading,
    faces,
    overlap,
    page_drawings,
    printed_grid,
    read_outline,
    shared_length,
    shows_dimensions,
)

from .sketch_pdf import (
    GARAGE,
    GARAGE_ROW,
    L_HOUSE,
    L_ROWS,
    THIN,
    SketchPage,
    build,
    l_house_page,
    rectangle,
)
from .sketch_pdf import (
    GROUND_PERIMETER as L_GROUND,
)

Segment = tuple[tuple[float, float], tuple[float, float]]


def ring(points: Sequence[tuple[float, float]]) -> list[Segment]:
    return list(zip(points, [*points[1:], points[0]], strict=True))


def read(data: bytes, azure: list[tuple[int, list[str]]] | None = None) -> OutlineReading:
    """What the extraction does with a PDF: the table from text, then the line work."""
    reading = (
        read_sketch(azure, "azure")
        if azure is not None
        else read_document_sketch(None, read_pdf(data).pages)
    )
    assert reading is not None
    fields, _ = sketch_fields(reading)
    return read_outline(reading, fields, page_drawings(data, reading.pages))


def statuses(result: OutlineReading) -> list[str]:
    return [page["status"] for page in result.trace["pages"]]


# ------------------------------------------------------------------ loops from segments


def test_a_square_is_one_loop_whatever_the_drawing_order_or_direction() -> None:
    square = ring([(0, 0), (10, 0), (10, 10), (0, 10)])
    expected = faces(square)
    assert [(loop.area, loop.perimeter, len(loop.corners)) for loop in expected] == [(100, 40, 4)]
    rng = random.Random(7)
    for _ in range(20):
        shuffled = [(b, a) if rng.random() < 0.5 else (a, b) for a, b in square]
        rng.shuffle(shuffled)
        assert [(loop.area, loop.perimeter) for loop in faces(shuffled)] == [(100, 40)]


def test_two_rooms_sharing_a_wall_drawn_twice_are_two_loops() -> None:
    house, garage = ring([(0, 0), (10, 0), (10, 10), (0, 10)]), ring(rectangle(10, 0, 6, 14))
    loops = faces([*house, *garage])  # the wall x=10 is drawn by both, at different lengths
    assert sorted(loop.area for loop in loops) == [84, 100]
    # The garage's left wall is one wall although the house's corner sits on it.
    assert sorted(len(loop.corners) for loop in loops) == [4, 4]
    assert shared_length(*loops) == pytest.approx(10)
    assert not overlap(*loops)


def test_strokes_that_close_nothing_are_dropped() -> None:
    square = ring([(0, 0), (10, 0), (10, 10), (0, 10)])
    other = ring(rectangle(30, 0, 5, 5))
    strays: list[Segment] = [
        ((50, 50), (60, 55)),  # alone
        ((10, 10), (14, 16)),  # hanging off a corner
        ((5, 10), (5, 13)),  # hanging off the middle of a wall, outside
        ((3, 0), (3, 4)),  # hanging off a wall, inside the loop
        ((6, 6), (8, 7)),  # alone, inside the loop
        ((10, 3), (30, 3)),  # joining the two loops
    ]
    loops = faces([*strays, *square, *other])
    assert [(loop.area, loop.perimeter, len(loop.corners)) for loop in loops] == [
        (100, 40, 4),
        (25, 20, 4),
    ]
    assert faces(strays) == [] and faces([]) == []


def test_a_line_across_a_loop_cuts_it_in_two() -> None:
    square = ring([(0, 0), (10, 0), (10, 10), (0, 10)])
    loops = faces([*square, ((-2, 4), (12, 4))])
    assert sorted(loop.area for loop in loops) == [40, 60]


def test_ends_that_miss_by_a_hair_still_close() -> None:
    square = [
        ((0, 0), (10, 0.00004)),
        ((10.00003, 0), (10, 10)),
        ((10, 10.00002), (0.00001, 10)),
        ((0, 10), (0.00002, 0.00003)),
    ]
    (loop,) = faces(square)
    assert loop.area == pytest.approx(100, abs=0.01)


def test_loops_that_touch_are_told_from_loops_that_cover_each_other() -> None:
    (a,) = faces(ring(rectangle(0, 0, 10, 10)))
    (beside,) = faces(ring(rectangle(10, 2, 5, 5)))
    (apart,) = faces(ring(rectangle(20, 0, 5, 5)))
    (across,) = faces(ring(rectangle(5, 5, 10, 10)))
    (within,) = faces(ring(rectangle(2, 2, 3, 3)))
    (same,) = faces(ring(rectangle(0, 0, 10, 10)))
    assert (shared_length(a, beside), overlap(a, beside)) == (pytest.approx(5), False)
    assert (shared_length(a, apart), overlap(a, apart)) == (0, False)
    assert overlap(a, across) and overlap(a, within) and overlap(within, a) and overlap(a, same)


# ------------------------------------------------------------------ belief


def row(label: str, area: str, *rects: tuple[str, ...], ok: bool = True) -> SketchArea:
    parts = tuple(
        Rectangle(tuple(Decimal(f) for f in factors[:-1]), Decimal(factors[-1]), ok)
        for factors in rects
    )
    return SketchArea(label, "living", "living", 1, None, Decimal(area), 1, parts, True)


def test_a_loop_must_show_the_dimensions_its_row_prints() -> None:
    (house,) = faces(ring([(x * 6, y * 6) for x, y in L_HOUSE]))
    printed = row("First Floor", "1008", ("40", "18", "720"), ("24", "12", "288"))
    assert shows_dimensions(house, printed, 6.0)
    # The same area as one rectangle the drawing does not contain.
    assert not shows_dimensions(house, row("First Floor", "1008", ("42", "24", "1008")), 6.0)
    # The order a rectangle's sides are printed in does not matter...
    assert shows_dimensions(house, row("First Floor", "1008", ("18", "40", "720")), 6.0)
    # ...but one side runs across and the other up: 40 and 24 are both widths of this house.
    assert not shows_dimensions(house, row("First Floor", "960", ("40", "24", "960")), 6.0)
    # At the wrong scale nothing lines up.
    assert not shows_dimensions(house, printed, 6.3)
    # A calculation the text lost a factor of proves nothing, and neither does no calculation.
    assert not shows_dimensions(house, row("First Floor", "1008", ("40", "720"), ok=False), 6.0)
    assert not shows_dimensions(house, row("First Floor", "1008"), 6.0)


def test_a_triangle_is_half_of_a_base_and_a_height() -> None:
    (gable,) = faces(ring([(0, 0), (60, 0), (60, 30), (30, 60), (0, 30)]))  # at 6 pt per ft
    printed = row("First Floor", "75", ("10", "5", "50"), ("0.5", "10", "5", "25"))
    assert shows_dimensions(gable, printed, 6.0)
    # Its two sloping walls are not on any printed precision, and need not be.
    match, trace = sketch_outline.match_page(1, [printed], [gable])
    assert match is not None and trace["matched"][0]["walls_on_printed_precision"] is False
    assert trace["matched"][0]["perimeter_ft"] == pytest.approx(10 + 5 + 5 + 2 * 50**0.5, abs=0.01)


def test_the_printed_precision_is_the_coarsest_every_dimension_lands_on() -> None:
    assert printed_grid([row("a", "720", ("40", "18", "720"))]) == Decimal(1)
    assert printed_grid([row("a", "375", ("12.5", "30", "375"))]) == Decimal("0.5")
    assert printed_grid([row("a", "619", ("30.5", "20.3", "619"))]) == Decimal("0.1")
    assert printed_grid([row("a", "619", ("30.52", "20.3", "619"))]) is None
    assert printed_grid([row("a", "619")]) is None


# ------------------------------------------------------------------ the fields, on PDFs


def test_an_l_shaped_house_with_an_attached_garage() -> None:
    result = read(build(l_house_page(), before=2))
    assert set(result.fields) == {GROUND_PERIMETER}
    ground = result.fields[GROUND_PERIMETER]
    assert (ground.value_num, ground.page, ground.source) == (Decimal(L_GROUND), 3, SOURCE)
    assert ground.value_text == "188"
    assert ground.raw_text == (
        "outline of 'First Floor', '2 Car Attached' on page 3, 18.0 ft of shared wall "
        "counted once (6.0 points per foot)"
    )
    trace = result.trace
    assert trace["status"] == "read" and trace["notes"] == []
    assert trace["ground"] == {
        "page": 3,
        "rows": ["First Floor", "2 Car Attached"],
        "perimeter_ft": 188.0,
        "shared_wall_ft": 18.0,
        "walls_on_printed_precision": True,
    }
    (page,) = trace["pages"]
    assert (page["status"], page["scale_pt_per_ft"], page["loops"]) == ("read", 6.0, 2)
    house, garage = page["matched"]
    assert (house["label"], house["area_sf"], house["walls"], house["perimeter_ft"]) == (
        "First Floor",
        1008.0,
        6,
        140.0,
    )
    assert sorted(house["wall_lengths_ft"]) == [12, 16, 18, 24, 30, 40]
    assert (garage["label"], garage["walls"], garage["perimeter_ft"]) == ("2 Car Attached", 4, 84)
    # The outline is kept in feet from its lower-left corner, for whoever draws it.
    assert sorted(map(tuple, house["outline_ft"])) == sorted(L_HOUSE)


def test_the_result_does_not_depend_on_the_order_walls_are_drawn_in() -> None:
    expected = read(build(l_house_page())).fields[GROUND_PERIMETER].value_num
    rng = random.Random(3)
    for _ in range(5):
        order = list(range(len(L_HOUSE)))
        rng.shuffle(order)
        page = SketchPage()
        page.walls(GARAGE, order=[2, 0, 3, 1]).walls(L_HOUSE, order=order)
        page.section("Living Area").row(*L_ROWS[0]).row(*GARAGE_ROW)
        assert read(build(page)).fields[GROUND_PERIMETER].value_num == expected == L_GROUND


def dashed_page() -> SketchPage:
    page = l_house_page()
    page.line((10, 0), (10, 30), dash=(4, 3))  # a dashed line across the house, wall weight
    page.walls(rectangle(4, -6, 24, 6), dash=(4, 3))  # a dashed porch against the front wall
    page.row("Covered Porch", "144 Sq ft", "24 x 6 = 144")
    return page


def test_a_dashed_porch_outline_is_not_a_wall() -> None:
    result = read(build(dashed_page()))
    assert result.fields[GROUND_PERIMETER].value_num == L_GROUND
    (page,) = result.trace["pages"]
    assert page["dashed_strokes"] == 5
    assert [m["label"] for m in page["matched"]] == ["First Floor", "2 Car Attached"]


def test_the_dash_test_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    # Were dashes taken as walls, the line across the house would cut its loop in two and
    # the first floor could not be read: the test above would fail.
    monkeypatch.setattr(sketch_outline, "_is_dashed", lambda dash: False)
    result = read(build(dashed_page()))
    assert result.fields == {} and result.trace["status"] == "not_read"


def test_a_dashed_outline_printed_as_short_bars_is_not_a_wall_either() -> None:
    page = l_house_page()
    for a, b in ring(rectangle(4, -6, 24, 6)):  # the porch, as one tool prints it
        page.chopped(a, b)
    page.row("Non-Calculated", "144 Sq ft", "24 x 6 = 144")
    result = read(build(page))
    assert result.fields[GROUND_PERIMETER].value_num == L_GROUND
    (found,) = result.trace["pages"]
    assert found["loops"] > 20  # every bar is a loop of its own, and none is believed
    assert [m["label"] for m in found["matched"]] == ["First Floor", "2 Car Attached"]


def test_stray_heavy_lines_change_nothing() -> None:
    page = l_house_page()
    page.line((-8, 34), (10, 38))  # alone
    page.line((0, 30), (-5, 36))  # off a corner
    page.line((60, 10), (66, 10))  # off the garage wall
    page.line((20, 0), (20, 7))  # a stub of wall inside the house
    page.line((45, 5), (52, 9))  # alone, inside the garage
    page.walls(rectangle(2, 20, 3, 8), width=THIN)  # stairs, in a thin line
    page.walls(rectangle(-20, 20, 10, 8), width=THIN)  # a shed the table does not list
    assert read(build(page)).fields[GROUND_PERIMETER].value_num == L_GROUND


def test_a_heavy_line_through_the_house_leaves_nothing_to_believe() -> None:
    page = l_house_page().line((-3, 9), (43, 9))
    result = read(build(page))
    assert result.fields == {}
    assert result.trace["status"] == "not_read"
    assert result.trace["notes"] == [
        "ground perimeter: no outline on page 1 reproduces 'First Floor' (1008 Sq ft); "
        "the page's line work: read"
    ]
    # The garage's loop is still believed; the ground story needs both.
    assert [m["label"] for m in result.trace["pages"][0]["matched"]] == ["2 Car Attached"]


def test_outlines_drawn_as_one_path_or_with_the_rectangle_operator() -> None:
    page = SketchPage().path(L_HOUSE, fill=True).box(40, 0, 20, 22)
    page.section("Living Area").row(*L_ROWS[0]).row(*GARAGE_ROW)
    assert read(build(page)).fields[GROUND_PERIMETER].value_num == L_GROUND


def test_a_curved_wall_is_not_read() -> None:
    page = SketchPage().walls(L_HOUSE, closed=False).arc_wall((0, 30), (-6, 15), (0, 0))
    page.section("Living Area").row(*L_ROWS[0])
    result = read(build(page))
    assert result.fields == {}
    (found,) = result.trace["pages"]
    assert (found["status"], found["curved_paths"], found["loops"]) == ("no_loop", 1, 0)


# ------------------------------------------------------------------ the floors above


def two_level(*upper: SketchPage) -> bytes:
    return build(l_house_page(), *upper)


def test_a_second_floor_on_its_own_page_at_its_own_scale() -> None:
    upper = SketchPage(scale=9.0).walls(rectangle(0, 0, 30, 25))
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    result = read(two_level(upper))
    assert result.fields[GROUND_PERIMETER].value_num == L_GROUND
    above = result.fields[UPPER_PERIMETER]
    assert (above.value_num, above.page, above.source) == (Decimal(110), 2, SOURCE)
    assert above.raw_text == "outline of 'Second Floor' on page 2"
    assert result.trace["upper"] == {
        "perimeter_ft": 110.0,
        "separate_pages": False,
        "blocks": [
            {"level": 2, "page": 2, "perimeter_ft": 110.0, "shared_wall_ft": 0.0, "masses": 1}
        ],
    }
    assert [page["scale_pt_per_ft"] for page in result.trace["pages"]] == [6.0, 9.0]


def test_two_blocks_of_one_floor_on_separate_pages_are_summed_and_said_to_be() -> None:
    main = SketchPage(scale=9.0).walls(rectangle(0, 0, 30, 25))
    main.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    bonus = SketchPage(scale=12.0).walls(rectangle(0, 0, 20, 14))
    bonus.section("Living Area").row("Second Floor", "280 Sq ft", "20 x 14 = 280")
    result = read(two_level(main, bonus))
    above = result.fields[UPPER_PERIMETER]
    assert above.value_num == Decimal(110 + 68)
    assert above.raw_text == (
        "outline of 'Second Floor', 'Second Floor' on pages 2, 3, summed as separate "
        "masses (a wall they share would be counted twice)"
    )
    assert result.trace["upper"]["separate_pages"] is True
    assert result.trace["notes"] == [
        "upper perimeter: one floor is drawn on more than one page; the outlines are summed "
        "as separate masses, and a wall they share would be counted twice"
    ]


def test_two_blocks_on_one_page_share_the_wall_the_page_shows() -> None:
    upper = SketchPage().walls(rectangle(0, 0, 30, 25)).walls(rectangle(30, 5, 20, 14))
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    upper.row("Second Floor Bonus", "280 Sq ft", "20 x 14 = 280")
    result = read(two_level(upper))
    assert result.fields[UPPER_PERIMETER].value_num == Decimal(110 + 68 - 2 * 14)
    assert result.trace["upper"]["blocks"] == [
        {"level": 2, "page": 2, "perimeter_ft": 150.0, "shared_wall_ft": 14.0, "masses": 1}
    ]
    assert result.trace["upper"]["separate_pages"] is False and result.trace["notes"] == []


def test_two_blocks_drawn_apart_on_one_page_are_two_masses() -> None:
    upper = SketchPage().walls(rectangle(0, 0, 30, 25)).walls(rectangle(36, 5, 20, 14))
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    upper.row("Second Floor Bonus", "280 Sq ft", "20 x 14 = 280")
    result = read(two_level(upper))
    assert result.fields[UPPER_PERIMETER].value_num == Decimal(110 + 68)
    assert result.trace["upper"]["blocks"][0]["masses"] == 2


def test_a_third_floor_adds_its_own_outline() -> None:
    second = SketchPage().walls(rectangle(0, 0, 30, 25))
    second.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    third = SketchPage().walls(rectangle(0, 0, 20, 15))
    third.section("Living Area").row("Third Floor", "300 Sq ft", "20 x 15 = 300")
    result = read(two_level(second, third))
    assert result.fields[UPPER_PERIMETER].value_num == Decimal(110 + 70)
    assert result.trace["upper"]["separate_pages"] is False  # one page per floor


def test_an_upper_floor_that_cannot_be_read_leaves_the_ground_perimeter_alone() -> None:
    upper = SketchPage().walls(rectangle(0, 0, 30, 24))  # drawn 30 x 24, printed 30 x 25
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    result = read(two_level(upper))
    assert set(result.fields) == {GROUND_PERIMETER}
    assert result.trace["status"] == "read" and "upper" not in result.trace
    assert result.trace["notes"] == [
        "upper perimeter: no outline on page 2 reproduces 'Second Floor' (750 Sq ft); "
        "the page's line work: no_match"
    ]


# ------------------------------------------------------------------ nothing to believe


def drawing_image() -> Image.Image:
    image = Image.new("RGB", (420, 380), "white")
    for x in range(60, 300):
        for y in (60, 240):
            image.putpixel((x, y), (0, 0, 0))
    return image


def test_a_raster_sketch_with_a_text_table_writes_nothing() -> None:
    page = SketchPage().picture(drawing_image())
    page.section("Living Area").row(*L_ROWS[0])
    result = read(build(page))
    assert result.fields == {} and statuses(result) == ["raster"]
    assert result.trace["pages"][0]["images"] == 1
    assert result.trace["notes"] == [
        "ground perimeter: no outline on page 1 reproduces 'First Floor' (1008 Sq ft); "
        "the page's line work: raster"
    ]


def test_a_page_that_is_all_image_has_no_heading_in_its_text_layer() -> None:
    # The table was read by OCR (Azure's lines); the PDF page itself is a picture.
    page = SketchPage(heading="").picture(drawing_image())
    lines = ["Area Calculations Summary", "Living Area", "First Floor", "1008 Sq ft"]
    lines += ["40 x 18 = 720", "24 x 12 = 288"]
    result = read(build(page), azure=[(1, lines)])
    assert result.fields == {} and statuses(result) == ["raster"]


def test_a_vector_page_with_no_walls_drawn_writes_nothing() -> None:
    page = SketchPage()
    page.section("Living Area").row(*L_ROWS[0])
    result = read(build(page))
    assert result.fields == {} and statuses(result) == ["no_strokes"]


@pytest.mark.parametrize(
    ("printed", "why"),
    [
        (("First Floor", "1100 Sq ft", "44 x 25 = 1100"), "another area and shape"),
        (("First Floor", "1008 Sq ft", "42 x 24 = 1008"), "the same area, another shape"),
        (("First Floor", "1008 Sq ft", "40 x 18 = 720", "32 x 9 = 288"), "one rectangle off"),
    ],
)
def test_a_loop_that_does_not_match_its_row_is_not_believed(
    printed: tuple[str, ...], why: str
) -> None:
    page = SketchPage().walls(L_HOUSE)
    page.section("Living Area").row(*printed)
    result = read(build(page))
    assert result.fields == {}, why
    assert statuses(result) == ["no_match"]
    assert result.trace["status"] == "not_read"


def test_area_alone_would_have_believed_the_wrong_loops() -> None:
    # Two thin-line boxes whose areas are in the ratio of the two rows (1008 : 440) but are
    # neither the house nor the garage. Reproducing two rows by area at one scale holds for
    # them as it does for the real walls; the printed dimensions hold only for the walls.
    page = l_house_page()
    page.walls(rectangle(-30, 0, 12.6, 8), width=THIN).walls(rectangle(-30, 12, 5.5, 8), width=THIN)
    result = read(build(page))
    (found,) = result.trace["pages"]
    assert found["scales_reproducing_two_rows_by_area"] == 2
    assert found["status"] == "read" and found["scale_pt_per_ft"] == 6.0
    assert result.fields[GROUND_PERIMETER].value_num == L_GROUND


def test_the_same_shape_at_two_sizes_is_ambiguous() -> None:
    page = SketchPage().walls(L_HOUSE)
    page.walls([(x * 0.5 - 30, y * 0.5) for x, y in L_HOUSE])  # an inset copy at half size
    page.section("Living Area").row(*L_ROWS[0])
    result = read(build(page))
    assert result.fields == {} and statuses(result) == ["ambiguous_scale"]
    assert result.trace["pages"][0]["scales_pt_per_ft"] == [3.0, 6.0]


def test_areas_rounded_to_the_square_foot_still_match() -> None:
    page = SketchPage(scale=7.3).walls(rectangle(0, 0, 30.5, 20.3))  # 619.15 SF
    page.section("Living Area").row("First Floor", "619 Sq ft", "30.5 x 20.3 = 619")
    result = read(build(page))
    assert result.fields[GROUND_PERIMETER].value_num == Decimal("101.6")
    assert result.trace["pages"][0]["printed_precision_ft"] == "0.1"
    assert result.trace["ground"]["walls_on_printed_precision"] is True


# ------------------------------------------------------------------ the garage


def garage_page(garage: Sequence[tuple[float, float]], *row: str) -> SketchPage:
    page = SketchPage().walls(L_HOUSE).walls(garage)
    page.section("Living Area").row(*L_ROWS[0])
    page.section("Non-living Area").row(*(row or GARAGE_ROW))
    return page


def test_a_garage_drawn_apart_from_the_house_is_not_assumed_attached() -> None:
    result = read(build(garage_page(rectangle(46, 0, 20, 22))))
    assert result.fields == {} and statuses(result) == ["read"]
    assert result.trace["notes"] == [
        "ground perimeter: the outlines do not share a wall ('First Floor', '2 Car "
        "Attached'), so whether they are attached is not shown"
    ]


def test_a_garage_that_only_touches_at_a_corner_is_not_attached() -> None:
    result = read(build(garage_page(rectangle(40, 18, 20, 22))))
    assert result.fields == {} and "do not share a wall" in result.trace["notes"][0]


def test_a_garage_drawn_over_the_house_is_not_read() -> None:
    result = read(build(garage_page(rectangle(30, 0, 20, 22))))
    assert result.fields == {}  # the two outlines cut each other: neither loop is whole


def test_a_garage_the_table_cannot_verify_withholds_the_ground_story() -> None:
    result = read(build(garage_page(GARAGE, "2 Car Attached", "440 Sq ft", "20 x 21 = 420")))
    assert result.fields == {}
    assert result.trace["notes"] == [
        "ground perimeter: the table's garage could not be verified, so the ground story's "
        "outline is not read"
    ]


def test_a_garage_on_another_page_withholds_the_ground_story() -> None:
    house = l_house_page(garage=False)
    garage = SketchPage().walls(GARAGE)
    garage.section("Non-living Area").row(*GARAGE_ROW)
    result = read(build(house, garage))
    assert result.fields == {}
    assert "drawn on more than one page" in result.trace["notes"][0]


def test_a_detached_garage_is_left_out_of_the_outline_as_it_is_of_the_footprint() -> None:
    page = garage_page(rectangle(50, 0, 20, 22), "Detached Garage", "440 Sq ft", "20 x 22 = 440")
    result = read(build(page))
    assert result.fields[GROUND_PERIMETER].value_num == Decimal(140)
    assert result.trace["ground"]["rows"] == ["First Floor"]


def test_a_house_with_no_garage_is_its_own_outline() -> None:
    result = read(build(l_house_page(garage=False)))
    assert result.fields[GROUND_PERIMETER].value_num == Decimal(140)
    assert result.trace["ground"]["shared_wall_ft"] == 0.0


def test_without_a_ground_floor_from_the_table_no_perimeter_is_read() -> None:
    page = SketchPage().walls(L_HOUSE)
    page.section("Living Area").row("Living Space", "1008 Sq ft", "40 x 18 = 720", "24 x 12 = 288")
    result = read(build(page))
    assert result.fields == {}
    assert result.trace["notes"] == [
        "ground perimeter: the table gives no ground floor area, so none is read"
    ]


# ------------------------------------------------------------------ the PDF itself


def test_only_strokes_above_the_table_heading_are_the_drawing() -> None:
    page = l_house_page()
    for x, y, w, d in ((-12, -30, 80, 4), (-12, -36, 80, 4)):  # ruled boxes around table rows
        page.walls(rectangle(x, y, w, d))
    data = build(page)
    (drawing,) = page_drawings(data, [1]).values()
    assert drawing.heading_top is not None and drawing.images == 0
    assert len(drawing.strokes) == 10 and all(
        max(s.a[1], s.b[1]) <= drawing.heading_top for s in drawing.strokes
    )
    assert read(data).fields[GROUND_PERIMETER].value_num == L_GROUND


def test_pages_the_document_does_not_have_and_bytes_that_are_not_a_pdf() -> None:
    data = build(l_house_page())
    assert set(page_drawings(data, [1, 1, 2, 0, -3])) == {1}
    assert page_drawings(b"%PDF-1.4 not really", [1]) == {}
    reading = read_document_sketch(None, read_pdf(data).pages)
    assert reading is not None
    fields, _ = sketch_fields(reading)
    unread = read_outline(reading, fields, {})
    assert unread.fields == {} and statuses(unread) == ["page_not_read"]


def test_a_style_with_too_many_strokes_is_set_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sketch_outline, "MAX_STROKES", 5)
    result = read(build(l_house_page()))
    assert result.fields == {}
    assert result.trace["pages"][0]["styles"][0]["skipped"] == "more than 5 strokes"


def test_the_trace_is_json() -> None:
    import json

    upper = SketchPage().walls(rectangle(0, 0, 30, 25))
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    json.dumps(read(two_level(upper)).trace)


def test_loop_properties() -> None:
    loop = Loop(((0, 0), (4, 0), (4, 3)))
    assert (loop.area, loop.perimeter, len(loop.walls)) == (6, 12, 3)
    assert io.BytesIO(build(l_house_page())).read(5) == b"%PDF-"
