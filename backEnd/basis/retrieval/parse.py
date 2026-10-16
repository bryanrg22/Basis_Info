"""PDF -> layout elements with page numbers and headings (from ``evidence_layer/src/parse_pdf.py``).

Pipeline per page (pdfplumber): words with font name and size -> lines -> paragraphs ->
element types (title, heading, list item, paragraph). Ruled tables are found first; the
words inside a table are left out of the text and the table is extracted whole by
:mod:`basis.retrieval.tables` (tables are never chunked).

Kept from the legacy parser: line grouping by vertical position, paragraph merging by
gap, size and indent, the list-item patterns, the title/heading idea.

Changed, each because the legacy code produced wrong output on real or synthetic pages:

* Words in a line are ordered by ``x0`` after grouping. The legacy code sorted by
  ``(top, x0)`` before grouping, so a value set one point higher than its bold label
  ("1290 Example Way" at y=111, "Property Address:" at y=112) came out first.
* A paragraph never merges a bold line with a regular one, so a same-size heading
  ("Sec. 4. Asset class 57.0", 10 pt bold over 10 pt body) is not swallowed by the
  paragraph under it.
* Heading sizes are relative to the document's body size (the most common size by
  character count) instead of the fixed 11/14 pt thresholds, and "bold" means at least
  90% of the characters are bold (a URAR line "**Address:** 1290 Example Way" is not a
  heading).
* Pattern headings ("Section 12", "1.2 Title", ALL CAPS) must be short; the legacy
  pattern made any paragraph that starts "Section 168(e)(3) groups ..." a heading.
* Running headers and footers (a line in the top or bottom 10% of the page that repeats,
  digits ignored, on at least half the pages of a document of two or more pages) are
  dropped: they are not evidence and they add the same tokens to every chunk.
* Words are assigned to a table by their center point, not by a 5 pt bbox overlap
  margin that also removed a caption line sitting just above the table.
* Two-column pages are read column by column. Grouping words into lines by height alone
  joined the left and right columns of IRS Pub 527 and Pub 946 into one line ("you
  usually use ... for the section 179 deduction"), which broke sentences in every chunk.
  :func:`detect_columns` looks for a vertical whitespace gutter (an x range almost no
  word covers, the projection-profile test of XY-cut layout analysis, Nagy and Seth
  1984) with a column of prose on each side; words and ruled tables that cross the
  gutter (a full-width title, a spanning table, a footer) cut the page into bands, and
  each band between two such cuts is read left column then right column. A page where
  no gutter passes every check keeps the single-column lines exactly as before. Only two
  columns are recognized; a narrow heading that spans both columns without crossing the
  gutter is read as part of the left column.

Failure modes are explicit, never an empty success: bytes that are not a PDF, a PDF that
cannot be opened, a PDF with no pages, and a PDF over the page limit raise a
:class:`PdfError` subclass. A page without a text layer is retried with pymupdf (it
reads some fonts pdfminer cannot); a page with images and no text in either is reported
as scanned (``ParsedPage.scanned``) and contributes no text. OCR is not done here.
"""

from __future__ import annotations

import io
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

import pdfplumber
import pymupdf
from pdfplumber.page import Page
from pdfplumber.utils.exceptions import PdfminerException

from basis.retrieval.tables import ExtractedTable, extract_page_tables

logger = logging.getLogger("basis.retrieval.parse")

PDF_MAGIC: Final = b"%PDF-"
# The PDF header may follow up to 1024 bytes of junk (PDF 32000-1:2008 annex H.3).
MAGIC_WINDOW: Final = 1024
# A study appraisal with addenda runs 30-80 pages; IRS Pub 946 is about 120. The study
# limit bounds memory and time per activity; the reference CLI raises it explicitly.
STUDY_MAX_PAGES: Final = 150
REFERENCE_MAX_PAGES: Final = 2000
# Bump when a change alters what this module outputs for an existing PDF; the real-corpus
# ingest test (tests/integration/retrieval/test_real_corpus.py) pins its chunk counts to
# this version and asks for a re-measured baseline when it changes.
PARSER_VERSION: Final = "2026-10-16.two-column"

LINE_Y_TOLERANCE: Final = 5.0  # points; words within this of a line's top join it
PARAGRAPH_MAX_GAP: Final = 15.0
PARAGRAPH_MAX_SIZE_DIFF: Final = 1.5
PARAGRAPH_MAX_INDENT_DIFF: Final = 50.0
BOLD_FRACTION: Final = 0.9
TITLE_SIZE_DELTA: Final = 2.5
HEADING_MAX_CHARS: Final = 150
PATTERN_HEADING_MAX_CHARS: Final = 80
MARGIN_FRACTION: Final = 0.10
FURNITURE_MIN_PAGES: Final = 2
FURNITURE_PAGE_SHARE: Final = 0.5
# Column detection (detect_columns). A gutter is at least COLUMN_MIN_GUTTER points wide
# and covered by at most COLUMN_MAX_SPANNING_SHARE of the page's rows (spanning titles,
# tables and footers may cross it). Each column is at least COLUMN_MIN_WIDTH_SHARE of the
# text width and COLUMN_MIN_BALANCE of the other column's width, holds COLUMN_MIN_ROWS
# rows, its median row fills COLUMN_MIN_FILL of the column (prose, not a label/value
# form), and COLUMN_MIN_WORD_SHARE of its tokens are words rather than numbers (not the
# number columns of a table). Measured on IRS Pub 527 and Pub 946: gutter 298-314 pt,
# columns 255 pt each, rows fill about 95%; Rev. Proc. 87-56's description | class life
# table has a 284 pt and a 142 pt side (balance 0.50) and must stay row by row. The
# lowest word share of a column on Pub 527/946 is 0.73; RSMeans unit-price pages (OCR
# text, description | unit costs) score 0.60-0.71, so some of them are still split.
COLUMN_MIN_GUTTER: Final = 6.0
COLUMN_MAX_SPANNING_SHARE: Final = 0.2
COLUMN_MIN_WIDTH_SHARE: Final = 0.3
COLUMN_MIN_BALANCE: Final = 0.6
COLUMN_MIN_ROWS: Final = 3
COLUMN_MIN_FILL: Final = 0.5
COLUMN_MIN_WORD_SHARE: Final = 0.7

_LIST_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"^[•‣◦⁃∙]\s",  # bullets
        r"^[-–—]\s",  # dashes
        r"^\d{1,3}[\.\)]\s",  # 1. or 1)
        r"^[a-zA-Z][\.\)]\s",  # a. or a)
        r"^\([a-zA-Z0-9]+\)\s",  # (a) or (1)
    )
)
_HEADING_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"^(Chapter|Section|Sec\.|Part|Article)\s+\d+",
        r"^[A-Z][A-Z\s]{3,50}$",  # SHORT ALL CAPS
        r"^\d+\.\d+\.?\s+[A-Z]",  # 1.2 Title
    )
)
_DIGITS: Final = re.compile(r"\d+")
_LETTER: Final = re.compile(r"[^\W\d_]")


class PdfError(ValueError):
    """The input cannot be parsed as a usable PDF (never retried: same bytes, same error)."""


class NotAPdfError(PdfError):
    """The bytes do not carry a ``%PDF-`` header (content sniffing, not the file name)."""


class UnreadablePdfError(PdfError):
    """The bytes look like a PDF but neither pdfplumber nor pymupdf can open them."""


class EmptyPdfError(PdfError):
    """The PDF has no pages."""


class TooManyPagesError(PdfError):
    """The PDF has more pages than the caller's limit."""

    def __init__(self, pages: int, limit: int) -> None:
        super().__init__(f"PDF has {pages} pages; the limit is {limit}")
        self.pages = pages
        self.limit = limit


class ElementType(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool


@dataclass(frozen=True, slots=True)
class Element:
    page: int
    ordinal: int
    type: ElementType
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool

    @property
    def is_heading(self) -> bool:
        return self.type in (ElementType.TITLE, ElementType.HEADING)


TextSource = Literal["pdfplumber", "pymupdf", "none"]


@dataclass(frozen=True, slots=True)
class ParsedPage:
    number: int  # 1-based
    elements: tuple[Element, ...]
    tables: tuple[ExtractedTable, ...]
    text_source: TextSource
    has_images: bool
    columns: int = 1  # 2 when the page was read column by column (detect_columns)

    @property
    def scanned(self) -> bool:
        """Image-only page: no text layer in pdfplumber or pymupdf, but images present."""
        return self.text_source == "none" and self.has_images


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    doc_id: str
    pages: tuple[ParsedPage, ...]
    body_size: float = 0.0
    dropped_furniture: tuple[str, ...] = field(default=())

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def scanned_pages(self) -> list[int]:
        return [page.number for page in self.pages if page.scanned]

    @property
    def scanned(self) -> bool:
        """Every page is image-only: the document needs OCR before it has any text."""
        return bool(self.pages) and all(page.scanned for page in self.pages)

    @property
    def elements(self) -> list[Element]:
        return [element for page in self.pages for element in page.elements]

    @property
    def tables(self) -> list[ExtractedTable]:
        return [table for page in self.pages for table in page.tables]

    @property
    def has_text(self) -> bool:
        return any(page.elements for page in self.pages) or any(p.tables for p in self.pages)


# --------------------------------------------------------------------------- entry point


def sniff_pdf(data: bytes) -> None:
    """Raise :class:`NotAPdfError` unless ``data`` carries a PDF header."""
    if not data:
        raise NotAPdfError("empty input is not a PDF")
    if PDF_MAGIC not in data[:MAGIC_WINDOW]:
        raise NotAPdfError("input has no %PDF- header; it is not a PDF")


def parse_pdf(data: bytes, doc_id: str, *, max_pages: int = STUDY_MAX_PAGES) -> ParsedDocument:
    """Parse ``data`` into per-page elements and whole tables.

    Raises :class:`PdfError` subclasses for input that can never parse (see module
    docstring). Synchronous and CPU-bound: call it through ``asyncio.to_thread`` from
    async code.
    """
    sniff_pdf(data)
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except PdfminerException as exc:
        raise UnreadablePdfError(f"PDF cannot be opened: {exc}") from exc
    with pdf:
        try:
            page_count = len(pdf.pages)
        except PdfminerException as exc:
            raise UnreadablePdfError(f"PDF page tree cannot be read: {exc}") from exc
        if page_count == 0:
            raise EmptyPdfError("PDF has no pages")
        if page_count > max_pages:
            raise TooManyPagesError(page_count, max_pages)
        raw_pages = [_read_page(page, number, doc_id) for number, page in _pages(pdf)]

    fallback_numbers = [raw.number for raw in raw_pages if not raw.lines]
    if fallback_numbers:
        fallback_lines = _pymupdf_lines(data, fallback_numbers)
        for raw in raw_pages:
            lines = fallback_lines.get(raw.number)
            if lines:
                raw.lines = lines
                raw.source = "pymupdf"

    furniture = _furniture_keys(raw_pages)
    for raw in raw_pages:
        raw.lines = _body_lines(raw, furniture)
    body_size = _body_size(line for raw in raw_pages for line in raw.lines)

    pages: list[ParsedPage] = []
    for raw in raw_pages:
        paragraphs = merge_paragraphs(raw.lines)
        elements = tuple(
            Element(
                page=raw.number,
                ordinal=ordinal,
                type=classify(para, paragraphs, body_size),
                text=para.text,
                x0=para.x0,
                top=para.top,
                x1=para.x1,
                bottom=para.bottom,
                size=para.size,
                bold=para.bold,
            )
            for ordinal, para in enumerate(paragraphs)
        )
        source: TextSource = raw.source if (raw.lines or raw.tables) else "none"
        pages.append(
            ParsedPage(raw.number, elements, tuple(raw.tables), source, raw.has_images, raw.columns)
        )
    document = ParsedDocument(
        doc_id=doc_id,
        pages=tuple(pages),
        body_size=body_size,
        dropped_furniture=tuple(sorted(furniture)),
    )
    if document.scanned_pages:
        logger.info(
            "image-only pages have no text layer",
            extra={"doc_id": doc_id, "scanned_pages": document.scanned_pages},
        )
    return document


# --------------------------------------------------------------------------- per page


@dataclass
class _RawPage:
    number: int
    height: float
    lines: list[Line]
    tables: list[ExtractedTable]
    has_images: bool
    source: TextSource = "pdfplumber"
    width: float = 0.0
    # The words the lines were built from and the ruled-table boxes, kept for the
    # column pass (pdfplumber pages only; the pymupdf fallback has lines, not words).
    words: list[dict[str, Any]] = field(default_factory=list)
    table_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    columns: int = 1


def _pages(pdf: Any) -> Iterable[tuple[int, Page]]:
    return enumerate(pdf.pages, start=1)


def _read_page(page: Page, number: int, doc_id: str) -> _RawPage:
    found = page.find_tables()
    tables = extract_page_tables(page, found, doc_id=doc_id, page_number=number)
    bboxes = [_box(table.bbox) for table in found]
    words = page.extract_words(
        keep_blank_chars=False, x_tolerance=3, y_tolerance=3, extra_attrs=["fontname", "size"]
    )
    kept = [w for w in words if not any(_center_inside(w, bbox) for bbox in bboxes)]
    return _RawPage(
        number=number,
        height=float(page.height),
        lines=group_lines(kept),
        tables=tables,
        has_images=bool(page.images),
        width=float(page.width),
        words=kept,
        table_boxes=bboxes,
    )


def _box(raw: Sequence[Any]) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = (float(v) for v in raw)
    return x0, top, x1, bottom


def _center_inside(word: dict[str, Any], bbox: tuple[float, ...]) -> bool:
    cx = (float(word["x0"]) + float(word["x1"])) / 2
    cy = (float(word["top"]) + float(word["bottom"])) / 2
    x0, top, x1, bottom = bbox
    return x0 <= cx <= x1 and top <= cy <= bottom


def group_lines(words: Sequence[dict[str, Any]]) -> list[Line]:
    """Group pdfplumber words into lines: by ``top`` within a tolerance, then by ``x0``."""
    lines: list[Line] = []
    for group in _word_rows(words):
        line = _row_line(group)
        if line is not None:
            lines.append(line)
    return lines


def _word_rows(words: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Words grouped by ``top`` within :data:`LINE_Y_TOLERANCE`, each row sorted by ``x0``."""
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    groups: list[list[dict[str, Any]]] = []
    line_top: float | None = None
    for word in ordered:
        top = float(word["top"])
        if line_top is None or abs(top - line_top) > LINE_Y_TOLERANCE:
            groups.append([word])
            line_top = top
        else:
            groups[-1].append(word)
    for group in groups:
        group.sort(key=lambda w: float(w["x0"]))
    return groups


def _row_line(group: Sequence[dict[str, Any]]) -> Line | None:
    text = " ".join(str(w["text"]) for w in group).strip()
    if not text:
        return None
    chars = [len(str(w["text"])) for w in group]
    sizes = [float(w.get("size") or 0.0) for w in group]
    total = sum(chars) or 1
    bold_chars = sum(n for n, w in zip(chars, group, strict=True) if _is_bold(w))
    return Line(
        text=text,
        x0=min(float(w["x0"]) for w in group),
        top=min(float(w["top"]) for w in group),
        x1=max(float(w["x1"]) for w in group),
        bottom=max(float(w["bottom"]) for w in group),
        size=sum(s * n for s, n in zip(sizes, chars, strict=True)) / total,
        bold=bold_chars / total >= BOLD_FRACTION,
    )


# --------------------------------------------------------------------------- columns


@dataclass(frozen=True, slots=True)
class ColumnLayout:
    """A two-column page: the whitespace gutter between the columns, in points."""

    gutter_x0: float
    gutter_x1: float

    @property
    def split(self) -> float:
        return (self.gutter_x0 + self.gutter_x1) / 2

    def crosses(self, x0: float, x1: float) -> bool:
        return x0 < self.gutter_x1 and x1 > self.gutter_x0


def detect_columns(
    words: Sequence[dict[str, Any]],
    width: float,
    height: float,
    table_boxes: Sequence[tuple[float, float, float, float]] = (),
) -> ColumnLayout | None:
    """The gutter of a two-column page, or ``None`` for any other page.

    Only words inside the page box count (PDFs carry hidden text above the page). The
    x projection is the number of words (and ruled tables) covering each point; the
    gutter is the widest run, at the lowest coverage that has one, of at least
    ``COLUMN_MIN_GUTTER`` points, covered by at most ``COLUMN_MAX_SPANNING_SHARE`` of the
    rows, leaving ``COLUMN_MIN_WIDTH_SHARE`` of the text width on each side. It is accepted
    only when the narrower column is ``COLUMN_MIN_BALANCE`` of the wider, both sides
    hold at least ``COLUMN_MIN_ROWS`` rows, their median row fills ``COLUMN_MIN_FILL``
    of the column and ``COLUMN_MIN_WORD_SHARE`` of their tokens are words rather than
    numbers, so a label/value form, a narrow side note or the number columns of an
    unruled table (Rev. Proc. 87-56's class lives) is not split.
    """
    on_page = [w for w in words if _on_page(w, width, height)]
    if not on_page:
        return None
    rows = _word_rows(on_page)  # rows across the page (both columns share baselines)
    if len(rows) < COLUMN_MIN_ROWS:
        return None
    left = min(float(w["x0"]) for w in on_page)
    right = max(float(w["x1"]) for w in on_page)
    span = right - left
    if span <= 0:
        return None
    origin = int(left)
    coverage = [0] * (int(right) - origin + 2)
    boxes = [(float(w["x0"]), float(w["x1"])) for w in on_page]
    boxes += [(box[0], box[2]) for box in table_boxes]
    for x0, x1 in boxes:
        start = max(0, int(x0) - origin)
        stop = min(len(coverage), int(x1) - origin + 1)
        for x in range(start, stop):
            coverage[x] += 1
    allowance = int(COLUMN_MAX_SPANNING_SHARE * len(rows))
    lo = left + COLUMN_MIN_WIDTH_SHARE * span
    hi = right - COLUMN_MIN_WIDTH_SHARE * span
    # The lowest coverage that leaves a wide enough run is the gutter's: spanning items
    # cover all of it, while a ragged column edge next to it is covered by more.
    best = None
    for level in range(allowance + 1):
        best = _widest_run(coverage, origin, lo, hi, level)
        if best is not None:
            break
    if best is None:
        return None
    layout = ColumnLayout(*best)
    widths = (layout.gutter_x0 - left, right - layout.gutter_x1)
    if min(widths) < COLUMN_MIN_BALANCE * max(widths):
        return None
    sides: tuple[list[dict[str, Any]], list[dict[str, Any]]] = ([], [])
    for w in on_page:
        if not layout.crosses(float(w["x0"]), float(w["x1"])):
            sides[_center_x(w) > layout.split].append(w)
    for side, column_width in zip(sides, widths, strict=True):
        side_rows = _word_rows(side)
        if len(side_rows) < COLUMN_MIN_ROWS:
            return None
        fills = sorted(
            (max(float(w["x1"]) for w in row) - min(float(w["x0"]) for w in row)) / column_width
            for row in side_rows
        )
        if fills[len(fills) // 2] < COLUMN_MIN_FILL:
            return None
        if _word_share(side) < COLUMN_MIN_WORD_SHARE:
            return None
    return layout


def _widest_run(
    coverage: Sequence[int], origin: int, lo: float, hi: float, level: int
) -> tuple[float, float] | None:
    """The widest x range in ``[lo, hi]`` with coverage at most ``level``, if it is at
    least :data:`COLUMN_MIN_GUTTER` wide.

    A word covers every integer point from ``int(x0)`` to ``int(x1)``, so the free points
    lie strictly outside every word: a word crosses the gutter only if it reaches into
    it, never by touching its edge.
    """
    best: tuple[float, float] | None = None
    run_start: int | None = None
    for index, count in enumerate([*coverage, level + 1]):  # the sentinel closes a run
        if count <= level:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            g0 = max(float(origin + run_start), lo)
            g1 = min(float(origin + index - 1), hi)
            if g1 - g0 >= COLUMN_MIN_GUTTER and (best is None or g1 - g0 > best[1] - best[0]):
                best = (g0, g1)
            run_start = None
    return best


def column_lines(
    words: Sequence[dict[str, Any]],
    layout: ColumnLayout,
    table_boxes: Sequence[tuple[float, float, float, float]] = (),
) -> list[Line]:
    """Lines of a two-column page in reading order.

    Words and tables that cross the gutter make full-width bands; between two bands the
    left column is read top to bottom, then the right one. A word whose vertical center
    falls inside a band belongs to it (a spanning title and anything on its row).
    """
    spans = [
        (float(w["top"]), float(w["bottom"]))
        for w in words
        if layout.crosses(float(w["x0"]), float(w["x1"]))
    ]
    spans += [(box[1], box[3]) for box in table_boxes if layout.crosses(box[0], box[2])]
    bands: list[list[float]] = []
    for top, bottom in sorted(spans):
        if bands and top <= bands[-1][1] + LINE_Y_TOLERANCE:
            bands[-1][1] = max(bands[-1][1], bottom)
        else:
            bands.append([top, bottom])
    # Part 2k is section k (k bands above it), split into columns; part 2k+1 is band k.
    parts: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = defaultdict(
        lambda: ([], [])
    )
    for w in words:
        middle = (float(w["top"]) + float(w["bottom"])) / 2
        index = 2 * sum(1 for _, bottom in bands if bottom < middle)
        inside = index // 2 < len(bands) and bands[index // 2][0] <= middle
        if inside:
            parts[index + 1][0].append(w)
        else:
            parts[index][_center_x(w) > layout.split].append(w)
    lines: list[Line] = []
    for index in sorted(parts):
        for side in parts[index]:
            lines.extend(group_lines(side))
    return lines


def _word_share(words: Sequence[dict[str, Any]]) -> float:
    """Tokens with a letter over tokens with a letter or a digit ("$20,000" and "27.5"
    are numbers; dot leaders and other punctuation count for neither)."""
    texts = [str(w["text"]) for w in words]
    lettered = sum(1 for t in texts if _LETTER.search(t))
    numeric = sum(1 for t in texts if not _LETTER.search(t) and _DIGITS.search(t))
    return lettered / (lettered + numeric) if lettered + numeric else 0.0


def _center_x(word: dict[str, Any]) -> float:
    return (float(word["x0"]) + float(word["x1"])) / 2


def _on_page(word: dict[str, Any], width: float, height: float) -> bool:
    return (
        float(word["x0"]) >= 0
        and float(word["x1"]) <= width
        and float(word["top"]) >= 0
        and float(word["bottom"]) <= height
    )


def _body_lines(page: _RawPage, furniture: set[str]) -> list[Line]:
    """The page's lines without running headers and footers, column by column if it has
    two columns. A page without a detected gutter returns its lines unchanged except for
    the furniture filter (the single-column output is exactly the earlier output)."""
    lines = [line for line in page.lines if not _is_furniture(line, page, furniture)]
    if page.source != "pdfplumber" or not page.words or page.width <= 0:
        return lines
    body = [
        w
        for row in _word_rows(page.words)
        if (line := _row_line(row)) is None or not _is_furniture(line, page, furniture)
        for w in row
    ]
    layout = detect_columns(body, page.width, page.height, page.table_boxes)
    if layout is None:
        return lines
    page.columns = 2
    return column_lines(body, layout, page.table_boxes)


def _is_bold(word: dict[str, Any]) -> bool:
    return "bold" in str(word.get("fontname") or "").lower()


def merge_paragraphs(lines: Sequence[Line]) -> list[Line]:
    """Merge consecutive lines that are close, the same size, indent and weight."""
    merged: list[Line] = []
    for line in lines:
        if merged:
            last = merged[-1]
            if (
                line.top - last.bottom < PARAGRAPH_MAX_GAP
                and abs(line.size - last.size) < PARAGRAPH_MAX_SIZE_DIFF
                and abs(line.x0 - last.x0) < PARAGRAPH_MAX_INDENT_DIFF
                and line.bold == last.bold
            ):
                merged[-1] = Line(
                    text=f"{last.text} {line.text}",
                    x0=min(last.x0, line.x0),
                    top=last.top,
                    x1=max(last.x1, line.x1),
                    bottom=line.bottom,
                    size=last.size,
                    bold=last.bold,
                )
                continue
        merged.append(line)
    return merged


def classify(para: Line, page_paragraphs: Sequence[Line], body_size: float) -> ElementType:
    """Title, heading, list item or paragraph, from patterns, weight and relative size."""
    text = para.text
    if len(text.strip()) < 3:
        return ElementType.PARAGRAPH
    if any(p.match(text) for p in _LIST_PATTERNS):
        return ElementType.LIST_ITEM
    if para.bold and body_size and para.size >= body_size + TITLE_SIZE_DELTA:
        return ElementType.TITLE
    if para.bold and para.size >= body_size - 0.5 and len(text) < HEADING_MAX_CHARS:
        return ElementType.HEADING
    if len(text) < PATTERN_HEADING_MAX_CHARS and any(p.match(text) for p in _HEADING_PATTERNS):
        return ElementType.HEADING
    avg_x0 = sum(p.x0 for p in page_paragraphs) / len(page_paragraphs) if page_paragraphs else 0
    if para.x0 > avg_x0 + 30 and len(text) < 200 and not para.bold:
        return ElementType.LIST_ITEM
    return ElementType.PARAGRAPH


def _body_size(lines: Iterable[Line]) -> float:
    """The most common font size, weighted by characters (0.0 when there is no text)."""
    weights: Counter[float] = Counter()
    for line in lines:
        weights[round(line.size * 2) / 2] += len(line.text)
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda item: (item[1], -item[0]))[0]


# --------------------------------------------------------------------------- furniture


def _furniture_key(line: Line) -> str:
    return _DIGITS.sub("#", " ".join(line.text.split())).lower()


def _in_margin(line: Line, height: float) -> bool:
    return line.bottom <= height * MARGIN_FRACTION or line.top >= height * (1 - MARGIN_FRACTION)


def _furniture_keys(pages: Sequence[_RawPage]) -> set[str]:
    if len(pages) < FURNITURE_MIN_PAGES:
        return set()
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({_furniture_key(ln) for ln in page.lines if _in_margin(ln, page.height)})
    threshold = max(FURNITURE_MIN_PAGES, len(pages) * FURNITURE_PAGE_SHARE)
    return {key for key, count in counts.items() if count >= threshold}


def _is_furniture(line: Line, page: _RawPage, keys: set[str]) -> bool:
    return bool(keys) and _in_margin(line, page.height) and _furniture_key(line) in keys


# --------------------------------------------------------------------------- pymupdf


def _pymupdf_lines(data: bytes, numbers: Sequence[int]) -> dict[int, list[Line]]:
    """Lines from pymupdf for pages pdfplumber found no words on (1-based numbers)."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception:  # pymupdf raises its own FileDataError/RuntimeError family
        logger.warning("pymupdf cannot open the PDF for the text fallback", exc_info=True)
        return {}
    out: dict[int, list[Line]] = {}
    with document:
        for number in numbers:
            page_dict = document[number - 1].get_text("dict")  # type: ignore[no-untyped-call]
            lines = [line for block in page_dict.get("blocks", []) for line in _mu_lines(block)]
            if lines:
                out[number] = lines
    return out


def _mu_lines(block: dict[str, Any]) -> list[Line]:
    lines: list[Line] = []
    for raw in block.get("lines", []):
        spans = [s for s in raw.get("spans", []) if str(s.get("text", "")).strip()]
        if not spans:
            continue
        text = " ".join(" ".join(str(s["text"]).split()) for s in spans).strip()
        chars = [len(str(s["text"]).strip()) for s in spans]
        total = sum(chars) or 1
        x0, top, x1, bottom = (float(v) for v in raw["bbox"])
        bold_chars = sum(n for n, s in zip(chars, spans, strict=True) if int(s["flags"]) & 16)
        size = sum(float(s["size"]) * n for s, n in zip(spans, chars, strict=True)) / total
        lines.append(Line(text, x0, top, x1, bottom, size, bold_chars / total >= BOLD_FRACTION))
    return lines
