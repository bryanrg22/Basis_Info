"""PDF text layer reading and the malformed-input rules (``malformed/README.md``)."""

from __future__ import annotations

import pytest
from fixtures import fixture_path

from basis.extraction import pdf_text
from basis.extraction.pdf_text import InvalidDocument, check_pdf_bytes, read_pdf


def test_reads_pages_and_both_ruled_tables() -> None:
    pdf = read_pdf(fixture_path("urar_sample.pdf").read_bytes())
    assert pdf.page_count == 7 and len(pdf.pages) == 7 and pdf.has_text_layer
    assert not pdf.truncated
    assert [table.page for table in pdf.tables] == [4, 6]
    assert pdf.tables[1].rows[0] == ["Item", "Area", "Cost per Sq Ft", "Amount"]


def test_a_scan_has_pages_but_no_text() -> None:
    pdf = read_pdf(fixture_path("urar_scan_noisy.pdf").read_bytes())
    assert pdf.page_count == 7 and not pdf.has_text_layer


def test_long_documents_are_truncated_with_the_count_kept() -> None:
    pdf = read_pdf(fixture_path("malformed", "huge_page_count.pdf").read_bytes(), max_pages=5)
    assert pdf.page_count == 200 and len(pdf.pages) == 5 and pdf.truncated


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"plain text that claims to be a pdf", "not a PDF"),
        (b"%PDF-1.7\n garbage with no objects", "cannot be read"),
    ],
)
def test_unreadable_bytes_are_invalid(data: bytes, message: str) -> None:
    with pytest.raises(InvalidDocument, match=message):
        read_pdf(data)


def test_the_malformed_fixtures() -> None:
    with pytest.raises(InvalidDocument, match="not a PDF"):
        read_pdf(fixture_path("malformed", "not_a_pdf.pdf").read_bytes())
    with pytest.raises(InvalidDocument, match="no pages"):
        read_pdf(fixture_path("malformed", "empty.pdf").read_bytes())


def test_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_text, "MAX_PDF_BYTES", 10)
    with pytest.raises(InvalidDocument, match="limit"):
        check_pdf_bytes(b"%PDF-1.7 more than ten bytes")


def test_header_may_follow_leading_bytes() -> None:
    check_pdf_bytes(b"\n\n%PDF-1.7")  # within the first 1024 bytes, as readers allow
