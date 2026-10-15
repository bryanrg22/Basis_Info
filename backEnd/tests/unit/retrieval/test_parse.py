"""PDF parsing: elements, headings, furniture, scanned pages, malformed input.

Every malformed fixture ends in an explicit error or flag, never an empty success
(``tests/fixtures/synthetic/malformed/README.md``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fixtures import fixture_path

from basis.retrieval import parse as parse_module
from basis.retrieval.parse import (
    STUDY_MAX_PAGES,
    ElementType,
    EmptyPdfError,
    Line,
    NotAPdfError,
    TooManyPagesError,
    UnreadablePdfError,
    classify,
    group_lines,
    merge_paragraphs,
    parse_pdf,
    sniff_pdf,
)


def read(*parts: str) -> bytes:
    return fixture_path(*parts).read_bytes()


def page_text(document: Any, number: int) -> str:
    return "\n".join(e.text for e in document.pages[number - 1].elements)


# --------------------------------------------------------------------------- URAR sample


@pytest.fixture(scope="module")
def urar() -> Any:
    return parse_pdf(read("urar_sample.pdf"), "SYN_URAR_0001")


def test_urar_pages_and_text_source(urar: Any) -> None:
    assert urar.page_count == 7
    assert [p.text_source for p in urar.pages] == ["pdfplumber"] * 7
    assert not urar.scanned and urar.scanned_pages == []
    assert urar.body_size == 9.0


def test_label_and_value_keep_reading_order(urar: Any) -> None:
    # The value is set one point higher than its bold label; the legacy sort put it first.
    text = page_text(urar, 1)
    assert "Property Address: 1290 Example Way City: Springfield" in text
    assert "Contract Price: $412,000" in text


def test_section_headings_are_headings(urar: Any) -> None:
    headings = {(e.page, e.text) for e in urar.elements if e.type is ElementType.HEADING}
    for page, title in [
        (1, "Subject"),
        (1, "Contract"),
        (2, "Neighborhood"),
        (3, "Improvements"),
        (4, "Sales Comparison Approach"),
        (6, "Cost Approach"),
    ]:
        assert (page, title) in headings


def test_running_header_and_footer_are_dropped(urar: Any) -> None:
    assert set(urar.dropped_furniture) == {
        "uniform residential appraisal report file no. syn-#",
        "synthetic test fixture - fictional data - not an appraisal page # of #",
    }
    everything = "\n".join(e.text for e in urar.elements)
    assert "Page 4 of 7" not in everything
    assert "UNIFORM RESIDENTIAL APPRAISAL REPORT" not in everything


def test_table_words_are_left_out_of_the_text(urar: Any) -> None:
    page4 = page_text(urar, 4)
    assert "Sales Comparison Approach" in page4
    assert "1302 Example Way" not in page4  # a cell of the comparison grid
    assert [t.key for t in urar.tables] == ["SYN_URAR_0001:4:t0", "SYN_URAR_0001:6:t0"]


def test_elements_are_numbered_per_page(urar: Any) -> None:
    for page in urar.pages:
        assert [e.ordinal for e in page.elements] == list(range(len(page.elements)))
        assert all(e.page == page.number for e in page.elements)


# --------------------------------------------------------------------------- corpus


def test_corpus_headings_titles_and_paths() -> None:
    document = parse_pdf(read("corpus", "SYN_PUB_A.pdf"), "SYN_PUB_A")
    first = document.pages[0].elements
    assert first[0].type is ElementType.TITLE
    assert first[0].text == "Synthetic Publication A: Recovery Periods for Business Property"
    # A same-size bold heading is not merged into the paragraph under it.
    assert (first[1].type, first[1].text) == (ElementType.HEADING, "Sec. 1. Purpose")
    assert first[2].type is ElementType.PARAGRAPH
    # "Section 168(e)(3) groups ..." starts like a heading pattern but is a paragraph.
    starts = [e for e in first if e.text.startswith("Section 168(e)(3)")]
    assert starts and starts[0].type is ElementType.PARAGRAPH
    assert [p.number for p in document.pages] == [1, 2, 3, 4]


# --------------------------------------------------------------------------- scanned


def test_image_only_pdf_is_flagged_scanned_with_no_text() -> None:
    document = parse_pdf(read("urar_scan_noisy.pdf"), "scan")
    assert document.scanned
    assert document.scanned_pages == [1, 2, 3, 4, 5, 6, 7]
    assert document.elements == [] and document.tables == []
    assert not document.has_text
    assert all(p.text_source == "none" and p.has_images for p in document.pages)


def test_pages_without_words_fall_back_to_pymupdf(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate pdfminer reading no words: the pymupdf fallback must supply the text.
    monkeypatch.setattr(parse_module, "group_lines", lambda words: [])
    document = parse_pdf(read("corpus", "SYN_PUB_A.pdf"), "SYN_PUB_A")
    assert [p.text_source for p in document.pages] == ["pymupdf"] * 4
    assert not document.scanned
    text = "\n".join(e.text for e in document.elements)
    assert "Section 168(e)(3) groups property into recovery classes" in text
    headings = [e.text for e in document.elements if e.type is ElementType.HEADING]
    assert "Sec. 4. Asset class 57.0, distributive trades and services" in headings


# --------------------------------------------------------------------------- malformed


def test_empty_pdf_is_an_error() -> None:
    with pytest.raises(EmptyPdfError):
        parse_pdf(read("malformed", "empty.pdf"), "empty")


def test_not_a_pdf_is_rejected_by_content() -> None:
    with pytest.raises(NotAPdfError):
        parse_pdf(read("malformed", "not_a_pdf.pdf"), "nap")
    with pytest.raises(NotAPdfError):
        sniff_pdf(b"")


def test_page_limit_is_enforced() -> None:
    data = read("malformed", "huge_page_count.pdf")
    with pytest.raises(TooManyPagesError) as caught:
        parse_pdf(data, "huge")
    assert (caught.value.pages, caught.value.limit) == (200, STUDY_MAX_PAGES)
    # Under a higher limit it parses, and its only line (a repeated footer) is dropped:
    # the caller sees has_text False, not a silent success (ingest raises NoTextError).
    document = parse_pdf(data, "huge", max_pages=200)
    assert document.page_count == 200 and not document.has_text and not document.scanned


@pytest.mark.parametrize(
    "mangle",
    [lambda d: d[: len(d) // 2], lambda d: b"%PDF-1.4\n" + b"x" * 500],
    ids=["truncated", "header-then-junk"],
)
def test_unreadable_pdf_is_an_error(mangle: Any) -> None:
    with pytest.raises(UnreadablePdfError):
        parse_pdf(mangle(read("urar_sample.pdf")), "bad")


def test_magic_may_follow_leading_junk() -> None:
    sniff_pdf(b"\x00" * 100 + b"%PDF-1.7")  # PDF 32000 annex H allows it
    with pytest.raises(NotAPdfError):
        sniff_pdf(b"\x00" * 2000 + b"%PDF-1.7")


# --------------------------------------------------------------------------- pure helpers


def word(
    text: str, x0: float, top: float, size: float = 10.0, bold: bool = False
) -> dict[str, Any]:
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + 5 * len(text),
        "top": top,
        "bottom": top + size,
        "size": size,
        "fontname": "Helvetica-Bold" if bold else "Helvetica",
    }


def test_group_lines_orders_by_x_within_a_line() -> None:
    words = [
        word("Example", 150, 111),
        word("Address:", 60, 112, bold=True),
        word("1290", 120, 111),
    ]
    [line] = group_lines(words)
    assert line.text == "Address: 1290 Example"
    assert not line.bold  # 8 of 19 characters are bold


def test_group_lines_splits_on_vertical_gap() -> None:
    lines = group_lines([word("one", 10, 100), word("two", 10, 120)])
    assert [ln.text for ln in lines] == ["one", "two"]


def line(text: str, top: float, size: float = 10.0, bold: bool = False, x0: float = 50) -> Line:
    return Line(text, x0, top, x0 + 300, top + size, size, bold)


def test_merge_paragraphs_rules() -> None:
    merged = merge_paragraphs(
        [
            line("Heading", 100, bold=True),
            line("body one", 114),
            line("body two", 128),
            line("far away", 200),
            line("small print", 214, size=7),
        ]
    )
    assert [m.text for m in merged] == [
        "Heading",
        "body one body two",
        "far away",
        "small print",
    ]


@pytest.mark.parametrize(
    ("para", "expected"),
    [
        (line("1. First item in a list", 0), ElementType.LIST_ITEM),
        (line("(a) lettered item", 0), ElementType.LIST_ITEM),
        (line("Big Bold Title", 0, size=14, bold=True), ElementType.TITLE),
        (line("Sec. 4. Bold heading", 0, bold=True), ElementType.HEADING),
        (line("SHORT ALL CAPS", 0), ElementType.HEADING),
        (
            line("Section 168(e)(3) groups property into classes by class life.", 0),
            ElementType.HEADING,
        ),
        (
            line("Section 168(e)(3) groups property into recovery classes by class life " * 2, 0),
            ElementType.PARAGRAPH,
        ),
        (line("Small bold note", 0, size=8, bold=True), ElementType.PARAGRAPH),
        (line("ab", 0, bold=True), ElementType.PARAGRAPH),
    ],
)
def test_classify(para: Line, expected: ElementType) -> None:
    assert classify(para, [para], body_size=10.0) is expected
