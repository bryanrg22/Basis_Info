"""Chunking (BUILD_PLAN 6.1): <= 400 tokens, 80 overlap, tables excluded, page spans."""

from __future__ import annotations

import pytest
from fixtures import fixture_path, load_fixture_json

from basis.retrieval.chunk import (
    CHUNK_TOKENS,
    OVERLAP_TOKENS,
    chunk_document,
    count_tokens,
    encoding,
    page_texts,
    split_text,
    table_surrogates,
    token_windows,
)
from basis.retrieval.parse import Element, ElementType, ParsedDocument, ParsedPage, parse_pdf
from basis.retrieval.tables import ExtractedTable


def element(
    page: int,
    ordinal: int,
    text: str,
    kind: ElementType = ElementType.PARAGRAPH,
    size: float = 10.0,
    top: float = 0.0,
) -> Element:
    return Element(
        page,
        ordinal,
        kind,
        text,
        50.0,
        top,
        500.0,
        top + size,
        size,
        kind is not ElementType.PARAGRAPH,
    )


def document(
    *pages: list[Element], tables: dict[int, list[ExtractedTable]] | None = None
) -> ParsedDocument:
    tables = tables or {}
    return ParsedDocument(
        doc_id="DOC",
        pages=tuple(
            ParsedPage(n, tuple(els), tuple(tables.get(n, [])), "pdfplumber", False)
            for n, els in enumerate(pages, start=1)
        ),
    )


VOCAB = ("cat", "dog", "tree", "house", "word", "apple")  # one cl100k token each


def words(n: int, word: str = "cat") -> str:
    """Exactly ``n`` tokens: the same one-token word repeated."""
    text = " ".join([word] * n)
    assert count_tokens(text) == n
    return text


# --------------------------------------------------------------------------- windows


def test_token_windows() -> None:
    assert token_windows(0, 400, 80) == []
    assert token_windows(400, 400, 80) == [(0, 400)]
    assert token_windows(401, 400, 80) == [(0, 400), (320, 401)]
    assert token_windows(1000, 400, 80) == [(0, 400), (320, 720), (640, 1000)]
    for bad in [(10, 0, 0), (10, 5, 5), (10, 5, -1)]:
        with pytest.raises(ValueError):
            token_windows(*bad)


def test_split_text_bounds_and_overlap() -> None:
    text = words(1000)
    pieces = split_text(text)
    assert [n for _, _, n in pieces] == [400, 400, 360]
    enc = encoding()
    for (start_a, a, _), (start_b, b, _) in zip(pieces, pieces[1:], strict=False):
        assert text[start_a:].startswith(a) and text[start_b:].startswith(b)
        tail = enc.decode(enc.encode(a)[-OVERLAP_TOKENS:]).strip()
        assert b.startswith(tail)  # the next window starts with the last 80 tokens


def test_no_tail_made_only_of_overlap() -> None:
    assert len(split_text(words(400))) == 1
    pieces = split_text(words(401))
    assert [n for _, _, n in pieces] == [400, 81]


def test_multibyte_text_is_sliced_not_decoded() -> None:
    text = "漢字のテキスト 😀 naïve café – “quoted” " * 40
    pieces = split_text(text, size=7, overlap=2)
    assert len(pieces) > 10
    for start, piece, _ in pieces:
        assert "�" not in piece
        assert text[start : start + len(piece)] == piece


def test_empty_text_has_no_chunks() -> None:
    assert split_text("") == [] and split_text("   \n ") == []


# --------------------------------------------------------------------------- documents


def test_chunks_never_cross_pages_and_respect_the_budget() -> None:
    doc = document(
        [element(1, 0, words(700, "dog"))],
        [element(2, 0, words(150, "tree")), element(2, 1, words(150, "house"))],
    )
    drafts = chunk_document(doc)
    assert [(d.page, d.ordinal) for d in drafts] == [(1, 0), (1, 1), (2, 0)]
    assert all(d.page_start == d.page_end == d.page for d in drafts)
    assert all(d.token_count <= CHUNK_TOKENS for d in drafts)
    texts = page_texts(doc)
    assert all(d.text in texts[d.page] for d in drafts)
    assert [d.chunk_key for d in drafts] == ["DOC:1:0", "DOC:1:1", "DOC:2:0"]


def test_section_paths_follow_a_size_hierarchy() -> None:
    doc = document(
        [
            element(1, 0, "Publication title", ElementType.TITLE, size=14),
            element(1, 1, "Sec. 1. First", ElementType.HEADING),
            element(1, 2, words(50)),
        ],
        [element(2, 0, "Sec. 2. Second", ElementType.HEADING), element(2, 1, words(50))],
        [element(3, 0, words(50))],
    )
    paths = [d.section_path for d in chunk_document(doc)]
    assert paths == [
        "Publication title > Sec. 1. First",
        "Publication title > Sec. 2. Second",  # sibling replaces, does not append
        "Publication title > Sec. 2. Second",  # carried onto a page without headings
    ]


def test_corpus_document_tables_become_surrogates() -> None:
    manifest = load_fixture_json("corpus", "manifest.json")["documents"][0]
    parsed = parse_pdf(fixture_path("corpus", "SYN_PUB_A.pdf").read_bytes(), "SYN_PUB_A")
    drafts = chunk_document(parsed)
    surrogates = [d for d in drafts if d.table_key is not None]
    text_chunks = [d for d in drafts if d.table_key is None]
    assert [d.chunk_key for d in surrogates] == ["SYN_PUB_A:3:1"]
    [surrogate] = surrogates
    assert surrogate.table_key == "SYN_PUB_A:3:t0"
    for row in manifest["tables"][0]["rows"]:
        assert " | ".join(row) in surrogate.text  # every row, including 57.0 in row four
    # Table cells are not in the text chunks ("Information systems" is only in the table).
    assert all("Information systems" not in d.text for d in text_chunks)
    assert len({d.chunk_key for d in drafts}) == len(drafts)
    assert {d.page for d in drafts} == {1, 2, 3, 4}


def test_scanned_document_has_no_chunks() -> None:
    parsed = parse_pdf(fixture_path("urar_scan_noisy.pdf").read_bytes(), "scan")
    assert chunk_document(parsed) == []


# --------------------------------------------------------------------------- surrogates


def table(rows: int, caption: str | None = "Table X-1. Things") -> ExtractedTable:
    return ExtractedTable(
        doc_id="DOC",
        page=5,
        index=0,
        headers=("Code", "Description"),
        rows=tuple((f"{i:02d}.1", f"description number {i}") for i in range(rows)),
        caption=caption,
    )


def test_small_table_is_one_surrogate() -> None:
    [text] = table_surrogates(table(3))
    assert text.splitlines() == [
        "Table: Table X-1. Things",
        "Columns: Code, Description",
        "00.1 | description number 0",
        "01.1 | description number 1",
        "02.1 | description number 2",
    ]


def test_large_table_is_split_by_budget_with_every_row_once() -> None:
    big = table(200)
    pieces = table_surrogates(big, max_tokens=120)
    assert len(pieces) > 5
    rows_seen = []
    for piece in pieces:
        assert count_tokens(piece) <= 120
        lines = piece.splitlines()
        assert lines[:2] == ["Table: Table X-1. Things", "Columns: Code, Description"]
        rows_seen += lines[2:]
    assert rows_seen == [" | ".join(r) for r in big.rows]


def test_surrogate_title_falls_back_to_the_section() -> None:
    [text] = table_surrogates(table(1, caption=None), "Report > Cost Approach")
    assert text.startswith("Table: Cost Approach\nColumns: Code, Description")
    [bare] = table_surrogates(table(1, caption=None))
    assert bare.startswith("Columns: Code, Description")
