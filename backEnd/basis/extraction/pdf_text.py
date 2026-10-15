"""The PDF's own text layer and ruled tables (``pdfplumber``), with input checks.

Used by the regex path and as page-text context for the LLM fallback. A scanned PDF has
no text layer: its pages come back empty, which the pipeline reports (only Azure DI's OCR
can read it). Malformed input fails with :class:`InvalidDocument` (the activity turns it
into a non-retryable error), following ``tests/fixtures/synthetic/malformed/README.md``:
not a PDF -> rejected by content sniffing; zero pages -> rejected; more pages than
:data:`MAX_TEXT_PAGES` -> only the first pages are read and the truncation is reported.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Final

PDF_MAGIC: Final = b"%PDF-"
# A URAR is 6-10 pages plus addenda; the fields the pipeline reads are on the form pages.
MAX_TEXT_PAGES: Final = 60
MAX_PDF_BYTES: Final = 100 * 1024 * 1024


class InvalidDocument(ValueError):
    """The bytes are not a readable PDF (never retried)."""


@dataclass(frozen=True)
class PdfTable:
    page: int
    rows: list[list[str]]


@dataclass(frozen=True)
class PdfText:
    page_count: int
    pages: list[str]  # text of the pages read (at most MAX_TEXT_PAGES)
    tables: list[PdfTable] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.page_count > len(self.pages)

    @property
    def has_text_layer(self) -> bool:
        return any(page.strip() for page in self.pages)


def check_pdf_bytes(data: bytes) -> None:
    if not data:
        raise InvalidDocument("the appraisal file is empty")
    if len(data) > MAX_PDF_BYTES:
        raise InvalidDocument(f"the appraisal is {len(data)} bytes; the limit is {MAX_PDF_BYTES}")
    if PDF_MAGIC not in data[:1024]:
        raise InvalidDocument("the appraisal is not a PDF (no %PDF- header)")


def read_pdf(data: bytes, *, max_pages: int = MAX_TEXT_PAGES) -> PdfText:
    """Text and ruled tables of the first ``max_pages`` pages (synchronous; CPU-bound)."""
    import pdfplumber
    from pdfminer.pdfparser import PDFSyntaxError
    from pdfplumber.utils.exceptions import PdfminerException

    check_pdf_bytes(data)
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                raise InvalidDocument("the PDF has no pages")
            pages: list[str] = []
            tables: list[PdfTable] = []
            for number, page in enumerate(pdf.pages[:max_pages], start=1):
                pages.append(page.extract_text() or "")
                for table in page.extract_tables():
                    rows = [[(cell or "").strip() for cell in row] for row in table]
                    tables.append(PdfTable(page=number, rows=rows))
                page.flush_cache()
    except (PdfminerException, PDFSyntaxError) as exc:
        raise InvalidDocument(f"the PDF cannot be read: {exc}") from exc
    return PdfText(page_count=page_count, pages=pages, tables=tables)
