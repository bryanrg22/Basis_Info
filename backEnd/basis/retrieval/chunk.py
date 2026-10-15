"""Token-window chunking with overlap (from ``evidence_layer/src/chunk_text.py``).

Kept from the legacy chunker: tiktoken ``cl100k_base`` token counts, 400-token windows
with 80 tokens of overlap, tables excluded from the text stream (they get surrogate
chunks instead), a heading path per chunk.

Changed, with the reason:

* **Chunks never cross a page.** Windows run over one page's text at a time. Citations
  and the retrieval qrels are both at document + page (BUILD_PLAN 13.2), and a chunk
  spanning pages 3-4 cannot say which page supports a claim. ``page_start`` and
  ``page_end`` are therefore equal for every chunk; the columns stay for other sources.
* **Chunk text is a slice of the page text, not a token decode.** Window boundaries are
  mapped to character offsets with ``decode_with_offsets``, so a window edge inside a
  multi-byte character cannot produce U+FFFD and every chunk is an exact substring of
  the parsed page (the citation check depends on that).
* **No tail duplicates.** A window is emitted only while new tokens remain; the legacy
  loop could emit a last chunk made only of overlap.
* **Heading paths are a hierarchy by font size** (a heading pops headings of the same or
  smaller size) instead of an append-only list where siblings piled up
  ("Sec. 1 > Sec. 2 > Sec. 3").
* **Table surrogates cover every row.** The legacy surrogate listed only the first three
  rows, so an exact-code query for a code in row four could never reach the table
  (``57.0`` is row four of the synthetic Table A-1). Rows are packed into as many
  surrogates as the token budget needs, each repeating the caption and column names,
  all pointing at the same table.

``chunk_key = f"{doc_id}:{page}:{ordinal}"`` (ordinal within the page, text chunks first,
then table surrogates), so re-chunking a document produces a diffable key set.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

import tiktoken

from basis.retrieval.parse import Element, ParsedDocument, ParsedPage
from basis.retrieval.tables import ExtractedTable

ENCODING_NAME: Final = "cl100k_base"
CHUNK_TOKENS: Final = 400
OVERLAP_TOKENS: Final = 80
SECTION_SEPARATOR: Final = " > "


@lru_cache(maxsize=1)
def encoding() -> tiktoken.Encoding:
    """The cl100k_base encoding (tiktoken caches the BPE file under TIKTOKEN_CACHE_DIR)."""
    return tiktoken.get_encoding(ENCODING_NAME)


def count_tokens(text: str) -> int:
    return len(encoding().encode(text))


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk before it has a database id (and before it has an embedding)."""

    doc_id: str
    page: int
    ordinal: int
    text: str
    token_count: int
    section_path: str | None = None
    table_key: str | None = None  # set on table surrogates: ExtractedTable.key

    @property
    def chunk_key(self) -> str:
        return f"{self.doc_id}:{self.page}:{self.ordinal}"

    @property
    def page_start(self) -> int:
        return self.page

    @property
    def page_end(self) -> int:
        return self.page


def token_windows(n_tokens: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """``[start, end)`` token windows of ``size`` advancing by ``size - overlap``."""
    if size <= 0:
        raise ValueError("window size must be positive")
    if not 0 <= overlap < size:
        raise ValueError("overlap must be >= 0 and smaller than the window size")
    windows: list[tuple[int, int]] = []
    start = 0
    while start < n_tokens:
        end = min(start + size, n_tokens)
        windows.append((start, end))
        if end == n_tokens:
            break
        start = end - overlap
    return windows


def split_text(
    text: str, size: int = CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS
) -> list[tuple[int, str, int]]:
    """``(char_start, chunk_text, n_tokens)`` windows of ``text``; slices, never decodes."""
    enc = encoding()
    tokens = enc.encode(text)
    if not tokens:
        return []
    decoded, offsets = enc.decode_with_offsets(tokens)
    if decoded != text:  # cannot happen for str input; guards the offset arithmetic
        raise ValueError("tokenizer round trip changed the text")
    pieces: list[tuple[int, str, int]] = []
    for start, end in token_windows(len(tokens), size, overlap):
        char_start = offsets[start]
        char_end = offsets[end] if end < len(tokens) else len(text)
        raw = text[char_start:char_end]
        stripped = raw.strip()
        if stripped:
            pieces.append((char_start + (len(raw) - len(raw.lstrip())), stripped, end - start))
    return pieces


class _SectionTracker:
    """Heading path by font size: a heading replaces headings of the same or smaller size."""

    def __init__(self) -> None:
        self._stack: list[tuple[float, str]] = []

    def see(self, element: Element) -> None:
        if not element.is_heading:
            return
        while self._stack and self._stack[-1][0] <= element.size + 0.01:
            self._stack.pop()
        self._stack.append((element.size, element.text))

    @property
    def path(self) -> str | None:
        return SECTION_SEPARATOR.join(text for _, text in self._stack) or None


def chunk_document(
    document: ParsedDocument,
    *,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[ChunkDraft]:
    """Text chunks and table surrogates for every page, ordered by page and ordinal."""
    tracker = _SectionTracker()
    drafts: list[ChunkDraft] = []
    for page in document.pages:
        drafts.extend(_chunk_page(document.doc_id, page, tracker, chunk_tokens, overlap_tokens))
    return drafts


def _chunk_page(
    doc_id: str,
    page: ParsedPage,
    tracker: _SectionTracker,
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[ChunkDraft]:
    entry_path = tracker.path
    # (char offset where the element starts in the page text, its top, whether it is a
    # heading, the path in effect from it on)
    marks: list[tuple[int, float, bool, str | None]] = []
    parts: list[str] = []
    offset = 0
    for element in page.elements:
        tracker.see(element)
        marks.append((offset, element.top, element.is_heading, tracker.path))
        parts.append(element.text)
        offset += len(element.text) + 1  # "\n" separator
    page_text = "\n".join(parts)

    def path_for(char_start: int, char_end: int) -> str | None:
        """The path of the chunk's first body text: a chunk that opens with headings
        belongs to the section they open, not to the one before them."""
        current = entry_path
        index = -1
        for i, (start, _, _, path) in enumerate(marks):
            if start > char_start:
                break
            current, index = path, i
        while 0 <= index < len(marks) - 1 and marks[index][2] and marks[index + 1][0] < char_end:
            index += 1
            current = marks[index][3]
        return current

    def path_above(top: float) -> str | None:
        current = entry_path
        for _, element_top, _, path in marks:
            if element_top >= top:
                break
            current = path
        return current

    drafts: list[ChunkDraft] = []
    for char_start, text, n_tokens in split_text(page_text, chunk_tokens, overlap_tokens):
        drafts.append(
            ChunkDraft(
                doc_id=doc_id,
                page=page.number,
                ordinal=len(drafts),
                text=text,
                token_count=n_tokens,
                section_path=path_for(char_start, char_start + len(text)),
            )
        )
    for table in page.tables:
        path = path_above(table.bbox[1]) if table.bbox else entry_path
        for text in table_surrogates(table, path, chunk_tokens):
            drafts.append(
                ChunkDraft(
                    doc_id=doc_id,
                    page=page.number,
                    ordinal=len(drafts),
                    text=text,
                    token_count=count_tokens(text),
                    section_path=path,
                    table_key=table.key,
                )
            )
    return drafts


def table_surrogates(
    table: ExtractedTable, section_path: str | None = None, max_tokens: int = CHUNK_TOKENS
) -> list[str]:
    """Searchable stand-ins for a table: title, columns, then as many rows as fit.

    Every row appears in exactly one surrogate. A single row longer than the budget gets
    a surrogate of its own (rows are never split; a cell is evidence only whole).
    """
    title = table.caption or (section_path.split(SECTION_SEPARATOR)[-1] if section_path else None)
    head = ([f"Table: {title}"] if title else []) + [f"Columns: {', '.join(table.headers)}"]
    head_text = "\n".join(head)
    budget = max_tokens - count_tokens(head_text) - 1
    rows = [" | ".join(cell for cell in row if cell) for row in table.rows]
    rows = [row for row in rows if row]
    pieces: list[list[str]] = []
    used = 0
    for row in rows:
        cost = count_tokens(row) + 1
        if pieces and used + cost <= budget:
            pieces[-1].append(row)
            used += cost
        else:
            pieces.append([row])
            used = cost
    if not pieces:
        return [head_text]
    return [head_text + "\n" + "\n".join(group) for group in pieces]


def page_texts(document: ParsedDocument) -> dict[int, str]:
    """The text the chunker windows over, per page (for tests and debugging)."""
    return {page.number: "\n".join(e.text for e in page.elements) for page in document.pages}


def drafts_by_key(drafts: Sequence[ChunkDraft]) -> dict[str, ChunkDraft]:
    return {draft.chunk_key: draft for draft in drafts}
