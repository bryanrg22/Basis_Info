"""Whole tables from ruled PDF tables (from ``evidence_layer/src/extract_tables.py``).

Tables are never chunked: each is stored whole in ``evidence_tables`` (headers, rows,
markdown) and fetched by id; search sees *surrogate* chunks built from it (see
:func:`basis.retrieval.chunk.table_surrogates`). This keeps exact cell values out of
the model's guesswork: a hit on a surrogate leads to the real table.

Kept from the legacy module: pdfplumber's line-based table finder, the first row as the
header (blank header cells become ``Column_<n>``), whitespace-normalized cells, empty rows
dropped, the "Table X: ..." caption probe just above the table, markdown rendering.

Dropped: the text-strategy fallback (``vertical_strategy="text"``). On the synthetic
corpus it reported a "table" on every page of running text (27, 23, 23 and 15 rows on
the four pages of SYN_PUB_A), which would have hidden that text from the chunker. Only
ruled tables are extracted; an unruled table stays in the text, where BM25 still sees
every cell.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from pdfplumber.page import Page
from pdfplumber.table import Table as PlumberTable

CAPTION_LOOKUP_POINTS: Final = 40.0
_CAPTION_PATTERNS: Final = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^Table\s+[\dA-Za-z\-\.]+[:\.]\s*(.+)$",
        r"^Table\s+[\dA-Za-z\-\.]+\s*[-–—]\s*(.+)$",
    )
)


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    doc_id: str
    page: int
    index: int  # 0-based position on the page
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    caption: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    @property
    def key(self) -> str:
        """Stable within a document: ``{doc_id}:{page}:t{index}``."""
        return f"{self.doc_id}:{self.page}:t{self.index}"

    def markdown(self) -> str:
        width = len(self.headers)
        lines = [
            "| " + " | ".join(_md(h) for h in self.headers) + " |",
            "| " + " | ".join("---" for _ in self.headers) + " |",
        ]
        for row in self.rows:
            padded = list(row[:width]) + [""] * (width - len(row))
            lines.append("| " + " | ".join(_md(cell) for cell in padded) + " |")
        return "\n".join(lines)


def _md(cell: str) -> str:
    return cell.replace("|", "\\|")


def clean_cell(cell: object) -> str:
    """``None`` -> ``""``; whitespace (including line breaks inside a cell) collapsed."""
    if cell is None:
        return ""
    return " ".join(str(cell).split())


def normalize_headers(row: Sequence[object]) -> tuple[str, ...]:
    return tuple(clean_cell(cell) or f"Column_{i + 1}" for i, cell in enumerate(row))


def table_from_rows(
    raw: Sequence[Sequence[object]],
    *,
    doc_id: str,
    page: int,
    index: int,
    caption: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> ExtractedTable | None:
    """A table from raw extracted rows, or ``None`` when there is no header or no data."""
    if len(raw) < 2 or not any(clean_cell(cell) for cell in raw[0]):
        return None
    rows = tuple(
        cleaned
        for cleaned in (tuple(clean_cell(cell) for cell in row) for row in raw[1:])
        if any(cleaned)
    )
    if not rows:
        return None
    return ExtractedTable(
        doc_id=doc_id,
        page=page,
        index=index,
        headers=normalize_headers(raw[0]),
        rows=rows,
        caption=caption,
        bbox=bbox,
    )


def extract_page_tables(
    page: Page, found: Sequence[PlumberTable], *, doc_id: str, page_number: int
) -> list[ExtractedTable]:
    """Every usable ruled table pdfplumber found on ``page``, in the finder's order."""
    tables: list[ExtractedTable] = []
    for found_table in found:
        bbox = _bbox(found_table.bbox)
        table = table_from_rows(
            found_table.extract(),
            doc_id=doc_id,
            page=page_number,
            index=len(tables),
            caption=detect_caption(page, bbox),
            bbox=bbox,
        )
        if table is not None:
            tables.append(table)
    return tables


def _bbox(raw: Sequence[Any]) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = (float(v) for v in raw)
    return x0, top, x1, bottom


def detect_caption(page: Page, bbox: tuple[float, float, float, float]) -> str | None:
    """A "Table X: ..." line within 40 points above the table, if there is one."""
    x0, top, x1, _ = bbox
    region = (
        max(0.0, x0 - 20),
        max(0.0, top - CAPTION_LOOKUP_POINTS),
        min(float(page.width), x1 + 20),
        min(float(page.height), top),
    )
    if region[0] >= region[2] or region[1] >= region[3]:
        return None
    text = page.within_bbox(region).extract_text() or ""
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        if any(p.match(line) for p in _CAPTION_PATTERNS):
            return line
    return None
