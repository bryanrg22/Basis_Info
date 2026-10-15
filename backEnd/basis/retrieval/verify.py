"""Deterministic citation checks (S3_SPEC sections 0 and 3, BUILD_PLAN 13.2 layer one).

A citation is ``(chunk_id, excerpt)`` claimed by a model. It is accepted only when

1. the chunk exists **in the corpus version the classifier searched** (an id from another
   version, another study, or nowhere is rejected), and
2. the excerpt, normalized, is a substring of the chunk text, normalized.

Normalization (:func:`normalize_text`): Unicode NFKC (ligatures such as "ﬁ" become "fi",
non-breaking spaces become spaces), soft hyphens and zero-width characters removed,
typographic quotes to ASCII quotes, the dash family to "-", case folded, whitespace runs
to one space, trimmed. It forgives what a model changes when it copies text (quotes,
dashes, line breaks, case) and nothing that changes meaning (digits, words, order).

Excerpts shorter than :data:`MIN_EXCERPT_CHARS` after normalization are rejected: "the"
is a substring of almost every chunk and proves nothing.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from basis.retrieval.types import Chunk

# Below this many normalized characters an excerpt says nothing ("the", "1245"). One
# threshold for retrieval and classification citations (basis.classify.verify imports it).
MIN_EXCERPT_CHARS: Final = 12

_QUOTES: Final = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "«": '"',
        "»": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "­": None,  # soft hyphen
        "​": None,  # zero-width space
        "‌": None,
        "‍": None,
        "﻿": None,
    }
)
_WHITESPACE: Final = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """The comparison form of ``text`` (see module docstring)."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return _WHITESPACE.sub(" ", text.casefold()).strip()


def verify_excerpt(chunk_text: str, excerpt: str, *, min_chars: int = MIN_EXCERPT_CHARS) -> bool:
    """True when the normalized ``excerpt`` is long enough and inside ``chunk_text``."""
    needle = normalize_text(excerpt)
    if len(needle) < min_chars:
        return False
    return needle in normalize_text(chunk_text)


class ChunkReader(Protocol):
    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None: ...


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def resolve_citation(
    store: ChunkReader, chunk_id: uuid.UUID | str, corpus_version_id: uuid.UUID
) -> Chunk | None:
    """The chunk when it exists in ``corpus_version_id``; ``None`` otherwise.

    ``chunk_id`` may be model output, so a malformed id resolves to ``None`` rather than
    raising.
    """
    parsed = _as_uuid(chunk_id)
    if parsed is None:
        return None
    chunk = await store.get_chunk(parsed)
    if chunk is None or chunk.corpus_version_id != corpus_version_id:
        return None
    return chunk


CitationStatus = Literal["verified", "chunk_not_found", "excerpt_not_in_chunk"]


@dataclass(frozen=True, slots=True)
class CitationCheck:
    status: CitationStatus
    chunk: Chunk | None = None

    @property
    def ok(self) -> bool:
        return self.status == "verified"


async def check_citation(
    store: ChunkReader,
    chunk_id: uuid.UUID | str,
    corpus_version_id: uuid.UUID,
    excerpt: str,
) -> CitationCheck:
    """Both checks with the reason a citation failed (for ``needs_review`` reasons)."""
    chunk = await resolve_citation(store, chunk_id, corpus_version_id)
    if chunk is None:
        return CitationCheck("chunk_not_found")
    if not verify_excerpt(chunk.text, excerpt):
        return CitationCheck("excerpt_not_in_chunk", chunk)
    return CitationCheck("verified", chunk)
