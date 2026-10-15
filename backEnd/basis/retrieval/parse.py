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
from collections import Counter
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
        raw.lines = [line for line in raw.lines if not _is_furniture(line, raw, furniture)]
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
        pages.append(ParsedPage(raw.number, elements, tuple(raw.tables), source, raw.has_images))
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


def _pages(pdf: Any) -> Iterable[tuple[int, Page]]:
    return enumerate(pdf.pages, start=1)


def _read_page(page: Page, number: int, doc_id: str) -> _RawPage:
    found = page.find_tables()
    tables = extract_page_tables(page, found, doc_id=doc_id, page_number=number)
    bboxes = [tuple(float(v) for v in table.bbox) for table in found]
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
    )


def _center_inside(word: dict[str, Any], bbox: tuple[float, ...]) -> bool:
    cx = (float(word["x0"]) + float(word["x1"])) / 2
    cy = (float(word["top"]) + float(word["bottom"])) / 2
    x0, top, x1, bottom = bbox
    return x0 <= cx <= x1 and top <= cy <= bottom


def group_lines(words: Sequence[dict[str, Any]]) -> list[Line]:
    """Group pdfplumber words into lines: by ``top`` within a tolerance, then by ``x0``."""
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
    lines: list[Line] = []
    for group in groups:
        group.sort(key=lambda w: float(w["x0"]))
        text = " ".join(str(w["text"]) for w in group).strip()
        if not text:
            continue
        chars = [len(str(w["text"])) for w in group]
        sizes = [float(w.get("size") or 0.0) for w in group]
        total = sum(chars) or 1
        bold_chars = sum(n for n, w in zip(chars, group, strict=True) if _is_bold(w))
        lines.append(
            Line(
                text=text,
                x0=min(float(w["x0"]) for w in group),
                top=min(float(w["top"]) for w in group),
                x1=max(float(w["x1"]) for w in group),
                bottom=max(float(w["bottom"]) for w in group),
                size=sum(s * n for s, n in zip(sizes, chars, strict=True)) / total,
                bold=bold_chars / total >= BOLD_FRACTION,
            )
        )
    return lines


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
