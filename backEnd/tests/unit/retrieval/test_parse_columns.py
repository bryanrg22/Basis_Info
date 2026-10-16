"""Two-column pages are read column by column; every other page parses exactly as before.

The PDFs are built here with reportlab (synthetic text, never client or IRS files): an
IRS-style page has two justified 9 pt columns of 255 pt with an 18 pt gutter on the same
baselines, which the line grouper used to join into one line per baseline.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Sequence
from typing import Any

import pdfplumber
import pytest
from fixtures import fixture_path
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from basis.retrieval.parse import (
    ColumnLayout,
    ParsedDocument,
    column_lines,
    detect_columns,
    group_lines,
    merge_paragraphs,
    parse_pdf,
)

PAGE_W, PAGE_H = LETTER  # 612 x 792
MARGIN = 42.0
GUTTER = 18.0
COLUMN_W = (PAGE_W - 2 * MARGIN - GUTTER) / 2  # 255 pt, as in IRS Pub 527 and Pub 946
RIGHT_X = MARGIN + COLUMN_W + GUTTER
FONT, SIZE, LEADING = "Helvetica", 9.0, 11.0

LEFT_TEXT = (
    "Alpha depreciation starts when the alder property is placed in service for use in a "
    "trade or business. Alpha recovery uses the amber convention for the first year and "
    "the alpine method for later years. Alpha basis is the cost of the asset plus the "
    "amounts paid for installation and freight before use."
)
RIGHT_TEXT = (
    "Bravo rental property is depreciated over the birch period that applies to the "
    "residential class. Bravo expenses such as repairs are deducted in the year paid when "
    "they keep the property in good working condition. Bravo improvements add to the "
    "basis and are recovered over the boxwood period."
)
LOWER_LEFT = (
    "Charlie listed property used for business less than half the time must use the cedar "
    "system. Charlie records show the business use of each item for each year it is used."
)
LOWER_RIGHT = (
    "Delta section limits apply to the deduction for a dogwood vehicle in each year. "
    "Delta amounts are adjusted each year for inflation and listed in the tables."
)


# --------------------------------------------------------------------------- builders


def column(
    canvas: Canvas, text: str, x: float, top: float, width: float = COLUMN_W, size: float = SIZE
) -> float:
    """Draw ``text`` wrapped to ``width`` from ``top`` (points from the page top); return
    the top of the next free line."""
    canvas.setFont(FONT, size)
    for line in simpleSplit(text, FONT, size, width):
        canvas.drawString(x, PAGE_H - top - size, line)
        top += LEADING
    return top


def title(canvas: Canvas, text: str, top: float, size: float = 14.0) -> float:
    canvas.setFont("Helvetica-Bold", size)
    canvas.drawString(MARGIN, PAGE_H - top - size, text)
    return top + size + 10


def build(*pages: Callable[[Canvas], None]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=LETTER)
    for draw in pages:
        draw(canvas)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def two_columns(canvas: Canvas) -> None:
    column(canvas, LEFT_TEXT, MARGIN, 72)
    column(canvas, RIGHT_TEXT, RIGHT_X, 72)


def texts(document: ParsedDocument, page: int = 1) -> list[str]:
    return [e.text for e in document.pages[page - 1].elements]


def flat(document: ParsedDocument, page: int = 1) -> str:
    return " ".join(texts(document, page))


def sentences(text: str) -> list[str]:
    return [s.strip() + "." for s in text.split(".") if s.strip()]


# --------------------------------------------------------------------------- two columns


def test_two_column_page_is_read_left_column_then_right() -> None:
    document = parse_pdf(build(two_columns), "TWO")
    page = document.pages[0]
    assert page.columns == 2
    text = flat(document)
    for sentence in sentences(LEFT_TEXT) + sentences(RIGHT_TEXT):
        assert sentence in text  # every sentence survives verbatim
    assert text.index(sentences(RIGHT_TEXT)[0]) > text.index(sentences(LEFT_TEXT)[-1])
    # No element mixes the columns (the defect: "Alpha ... Bravo ..." on one line).
    assert not any("Alpha" in t and "Bravo" in t for t in texts(document))
    left, right = page.elements
    assert left.x1 < RIGHT_X and right.x0 >= RIGHT_X - 1


def test_the_old_line_grouping_interleaved_these_columns() -> None:
    # The defect this fixes, on the same page: one line per baseline across both columns.
    with pdfplumber.open(io.BytesIO(build(two_columns))) as pdf:
        words = pdf.pages[0].extract_words(x_tolerance=3, y_tolerance=3)
    first = group_lines(words)[0].text
    assert first.startswith("Alpha") and "Bravo" in first


def test_full_width_title_and_spanning_heading_cut_the_columns() -> None:
    def draw(canvas: Canvas) -> None:
        top = title(canvas, "Chapter 9. A Full Width Synthetic Title That Crosses The Gutter", 40)
        bottom = max(
            column(canvas, LEFT_TEXT, MARGIN, top), column(canvas, RIGHT_TEXT, RIGHT_X, top)
        )
        top = title(
            canvas, "Part B. Spanning Heading Between Two Column Sections Here", bottom + 8, 11
        )
        column(canvas, LOWER_LEFT, MARGIN, top)
        column(canvas, LOWER_RIGHT, RIGHT_X, top)

    document = parse_pdf(build(draw), "SPAN")
    assert document.pages[0].columns == 2
    text = flat(document)
    order = [
        "Chapter 9.",
        sentences(LEFT_TEXT)[0],
        sentences(RIGHT_TEXT)[-1],
        "Part B. Spanning Heading",
        sentences(LOWER_LEFT)[0],
        sentences(LOWER_RIGHT)[-1],
    ]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    # The upper right column is read before the lower left one, never after it.
    assert text.index("Bravo improvements") < text.index("Charlie listed")
    headings = [e.text for e in document.pages[0].elements if e.is_heading]
    assert any(h.startswith("Chapter 9.") for h in headings)


def test_spanning_ruled_table_cuts_the_columns_and_stays_a_table() -> None:
    rows = [("Class", "Period"), ("Alder", "5 years"), ("Birch", "7 years"), ("Cedar", "15 years")]

    def draw(canvas: Canvas) -> None:
        bottom = max(column(canvas, LEFT_TEXT, MARGIN, 60), column(canvas, RIGHT_TEXT, RIGHT_X, 60))
        table_top, row_h = bottom + 12, 16.0
        x_edges = (MARGIN, PAGE_W / 2, PAGE_W - MARGIN)
        for i in range(len(rows) + 1):
            y = PAGE_H - (table_top + i * row_h)
            canvas.line(x_edges[0], y, x_edges[-1], y)
        for x in x_edges:
            canvas.line(x, PAGE_H - table_top, x, PAGE_H - (table_top + len(rows) * row_h))
        canvas.setFont(FONT, SIZE)
        for i, row in enumerate(rows):
            for x, cell in zip(x_edges, row, strict=False):
                canvas.drawString(x + 4, PAGE_H - (table_top + i * row_h) - 12, cell)
        lower = table_top + len(rows) * row_h + 14
        column(canvas, LOWER_LEFT, MARGIN, lower)
        column(canvas, LOWER_RIGHT, RIGHT_X, lower)

    document = parse_pdf(build(draw), "TABLE")
    page = document.pages[0]
    assert page.columns == 2
    assert len(page.tables) == 1 and page.tables[0].rows[0] == ("Alder", "5 years")
    text = flat(document)
    assert "Alder" not in text  # table words stay out of the text stream
    # Both columns above the table come before both columns below it.
    assert text.index("Bravo improvements") < text.index("Charlie listed")
    assert text.index(sentences(LOWER_LEFT)[-1]) < text.index(sentences(LOWER_RIGHT)[0])


def test_repeated_header_split_by_the_gutter_is_still_furniture() -> None:
    def page(number: int) -> Callable[[Canvas], None]:
        def draw(canvas: Canvas) -> None:
            canvas.setFont(FONT, 8)
            canvas.drawString(MARGIN, PAGE_H - 30, "Synthetic Publication 9 (2024)")
            canvas.drawString(RIGHT_X, PAGE_H - 30, f"Page {number}")
            two_columns(canvas)

        return draw

    document = parse_pdf(build(page(1), page(2), page(3)), "FURN")
    assert [p.columns for p in document.pages] == [2, 2, 2]
    assert "synthetic publication # (#) page #" in document.dropped_furniture
    assert all("Synthetic Publication 9" not in flat(document, n) for n in (1, 2, 3))
    assert all(texts(document, n) == texts(document, 1) for n in (2, 3))


# --------------------------------------------------------------------------- guards


def single_column(canvas: Canvas) -> None:
    top = title(canvas, "Sec. 1. A Single Column Synthetic Page", 60)
    column(canvas, LEFT_TEXT + " " + RIGHT_TEXT, MARGIN, top, width=PAGE_W - 2 * MARGIN)


def label_value_form(canvas: Canvas) -> None:
    canvas.setFont(FONT, SIZE)
    for i, (label, value) in enumerate(
        [
            ("Property Address:", "1290 Example Way"),
            ("City:", "Springfield"),
            ("Owner of Public Record:", "Jane Example"),
            ("Census Tract:", "0042.00"),
            ("Contract Price:", "$412,000"),
            ("Date of Contract:", "03/14/2024"),
            ("Zoning Classification:", "R-1 Single Family"),
        ]
    ):
        y = PAGE_H - 100 - i * 14
        canvas.drawString(MARGIN, y, label)
        canvas.drawString(RIGHT_X + 60, y, value)


def numbers_table(canvas: Canvas) -> None:
    # Rev. Proc. 87-56 style: a description column and unruled number columns.
    canvas.setFont(FONT, SIZE)
    for i in range(8):
        y = PAGE_H - 100 - i * 14
        canvas.drawString(
            MARGIN, y, f"00.{i}1 Synthetic asset class number {i} with a long description"
        )
        canvas.drawString(RIGHT_X + 20, y, f"{5 + i}     {3 + i}     {7 + i}     {9 + i}.5")


def unbalanced(canvas: Canvas) -> None:
    # A wide prose column and a narrow side column (a margin note), not two columns.
    column(canvas, LEFT_TEXT + " " + RIGHT_TEXT, MARGIN, 72, width=380)
    column(canvas, LOWER_LEFT, MARGIN + 380 + GUTTER, 72, width=PAGE_W - 2 * MARGIN - 380 - GUTTER)


def short_columns(canvas: Canvas) -> None:
    column(canvas, "Alpha only two short lines of text in this left column here.", MARGIN, 72)
    column(canvas, "Bravo only two short lines of text in this right column.", RIGHT_X, 72)


@pytest.mark.parametrize(
    "draw",
    [single_column, label_value_form, numbers_table, unbalanced, short_columns],
    ids=["single-column", "label-value-form", "numbers-table", "unbalanced", "too-few-rows"],
)
def test_pages_that_are_not_two_columns_keep_the_line_reading(
    draw: Callable[[Canvas], None],
) -> None:
    data = build(draw)
    document = parse_pdf(data, "ONE")
    assert document.pages[0].columns == 1
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        words = pdf.pages[0].extract_words(
            x_tolerance=3, y_tolerance=3, extra_attrs=["fontname", "size"]
        )
    # Exactly the pre-column pipeline: group by baseline, merge paragraphs.
    assert texts(document) == [line.text for line in merge_paragraphs(group_lines(words))]


def test_label_and_value_stay_on_one_line() -> None:
    assert "Property Address: 1290 Example Way" in flat(parse_pdf(build(label_value_form), "F"))


# --------------------------------------------------------------------------- regression


def digest(document: ParsedDocument) -> str:
    payload = {
        "body_size": document.body_size,
        "furniture": list(document.dropped_furniture),
        "pages": [
            [
                p.number,
                p.text_source,
                p.has_images,
                [
                    [str(e.type), e.text, e.x0, e.top, e.x1, e.bottom, e.size, e.bold, e.ordinal]
                    for e in p.elements
                ],
                [[t.key, t.markdown()] for t in p.tables],
            ]
            for p in document.pages
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# Digests of the parser output before column detection existed (main at 8908ed6): every
# element (type, text, box, size, weight, ordinal), table and furniture key, unchanged.
PRE_COLUMN_DIGESTS = {
    ("urar_sample.pdf",): "a1c99d67b6abe6076c5a458c6827a0347acde0d50691f3233e93b14f5f0d2932",
    ("corpus", "SYN_PUB_A.pdf"): "76a005e5e96561af243cc3acfd9992e03eb9ae2332d854a50a59594f93012157",
    ("corpus", "SYN_PUB_B.pdf"): "5974246115f23ae957e98718533a6ca712ac31b118edcdf3163122b112eaf731",
    ("corpus", "SYN_PUB_C.pdf"): "0fb7df3c21138b40cf55467c8c896ce1298f725317ba3ceb8ae0316316f036a8",
    ("adversarial", "urar_injected.pdf"): (
        "c5087d1eb9e11f06eca1ce8d08862b30beb6ac40f60bcd286e350d19f296560d"
    ),
}


@pytest.mark.parametrize("parts", list(PRE_COLUMN_DIGESTS), ids=lambda p: "/".join(p))
def test_single_column_fixtures_parse_byte_identically(parts: tuple[str, ...]) -> None:
    document = parse_pdf(fixture_path(*parts).read_bytes(), parts[-1].removesuffix(".pdf"))
    assert all(p.columns == 1 for p in document.pages)
    assert digest(document) == PRE_COLUMN_DIGESTS[parts]


# --------------------------------------------------------------------------- pure helpers


def word(text: str, x0: float, top: float, x1: float | None = None) -> dict[str, Any]:
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + 5 * len(text) if x1 is None else x1,
        "top": top,
        "bottom": top + 9,
        "size": 9.0,
        "fontname": "Helvetica",
    }


def prose_rows(tops: Sequence[float], extra: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Rows of two full columns: left 42-297.6, right 315-570."""
    words: list[dict[str, Any]] = []
    for top in tops:
        words += [word("alpha", 42, top, 120), word("words", 125, top, 297.6)]
        words += [word("bravo", 315, top, 400), word("words", 405, top, 570)]
    return words + list(extra)


def test_detect_columns_finds_the_gutter_between_word_edges() -> None:
    layout = detect_columns(prose_rows([100 + 12 * i for i in range(10)]), PAGE_W, PAGE_H)
    assert layout is not None
    # A word ending at 297.6 touches integer point 297; the gutter starts after it.
    assert layout.gutter_x0 == 298 and layout.gutter_x1 == 314
    assert not layout.crosses(42, 297.6) and not layout.crosses(315, 570)
    assert layout.crosses(200, 400)


def test_detect_columns_ignores_hidden_text_outside_the_page() -> None:
    hidden = [word("x" * 40, 150, -90 + 10 * i, 460) for i in range(6)]  # above the page box
    rows = prose_rows([100 + 12 * i for i in range(10)], hidden)
    assert detect_columns(rows, PAGE_W, PAGE_H) is not None
    lines = column_lines(rows, ColumnLayout(298, 314))
    assert lines[0].text.startswith("xxxx")  # hidden text is a band at the very top


def test_too_many_spanning_rows_means_no_gutter() -> None:
    spanning = [word("span", 42, 400 + 12 * i, 570) for i in range(4)]
    assert (
        detect_columns(prose_rows([100 + 12 * i for i in range(10)], spanning), PAGE_W, PAGE_H)
        is None
    )


def test_detect_columns_needs_enough_rows() -> None:
    assert detect_columns([], PAGE_W, PAGE_H) is None
    assert detect_columns(prose_rows([100, 112]), PAGE_W, PAGE_H) is None


def test_column_lines_bands_and_sections() -> None:
    layout = ColumnLayout(298, 314)
    words = prose_rows([100, 112]) + [word("SPANNING", 42, 140, 570)] + prose_rows([170, 182])
    words.append(word("tail", 315, 141, 340))  # on the spanning row: belongs to the band
    lines = column_lines(words, layout, table_boxes=[(42.0, 220.0, 570.0, 260.0)])
    lines += column_lines(prose_rows([280]), layout)
    tops = [(line.text, line.top) for line in lines]
    assert [t for t, _ in tops[:4]] == ["alpha words", "alpha words", "bravo words", "bravo words"]
    assert tops[4] == ("SPANNING tail", 140)
    assert [top for _, top in tops[5:9]] == [170, 182, 170, 182]
