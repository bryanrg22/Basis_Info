"""Citation verification: a citation stands only if it resolves and its excerpt is in the chunk.

S3_SPEC section 0 and BUILD_PLAN 13.2 (deterministic layer): the ``chunk_id`` must be a
chunk of a corpus version this classification may cite, the excerpt must be a normalized
substring of that chunk's text, and on the model path the chunk must have been returned by
a tool during this classification (``retrieved``). A citation failing any check is
dropped with a reason; the caller flags the asset ``citation_unverified``.

Text normalization and the minimum excerpt length are those of
:mod:`basis.retrieval.verify` (one implementation for retrieval and classification): NFKC
folding, case folding, typographic quotes and the dash family to ASCII, soft hyphens and
zero-width characters removed, whitespace runs collapsed, so an excerpt copied from a PDF
with typographic punctuation still matches the stored text.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from basis.classify.schemas import CitationClaim
from basis.classify.store_protocol import ChunkStore
from basis.retrieval.verify import MIN_EXCERPT_CHARS, normalize_text

MAX_EXCERPT_CHARS: Final = 1000

DropReason = Literal[
    "malformed_id",
    "not_found",
    "out_of_scope",
    "not_retrieved",
    "excerpt_too_short",
    "excerpt_mismatch",
    "duplicate",
]


def excerpt_supported(excerpt: str, chunk_text: str) -> bool:
    """True when the normalized excerpt is long enough and inside the normalized text."""
    needle = normalize_text(excerpt)
    return len(needle) >= MIN_EXCERPT_CHARS and needle in normalize_text(chunk_text)


@dataclass(frozen=True, slots=True)
class VerifiedCitation:
    chunk_id: uuid.UUID
    excerpt: str
    doc_id: str
    page_start: int | None
    page_end: int | None
    method: str  # how it was verified (written to citations.verification_method)


@dataclass(frozen=True, slots=True)
class DroppedCitation:
    chunk_id: str
    excerpt: str
    reason: DropReason


def parse_chunk_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def verify_citations(
    claims: Iterable[CitationClaim],
    store: ChunkStore | None,
    *,
    allowed_versions: frozenset[uuid.UUID],
    retrieved: frozenset[uuid.UUID] | None = None,
    method: str = "resolved+normalized_substring",
) -> tuple[list[VerifiedCitation], list[DroppedCitation]]:
    """Split ``claims`` into verified and dropped citations (order preserved, deduped)."""
    verified: list[VerifiedCitation] = []
    dropped: list[DroppedCitation] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    for claim in claims:
        chunk_id = parse_chunk_id(claim.chunk_id)
        if chunk_id is None:
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "malformed_id"))
            continue
        normalized = normalize_text(claim.excerpt)
        if (chunk_id, normalized) in seen:
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "duplicate"))
            continue
        if len(normalized) < MIN_EXCERPT_CHARS:
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "excerpt_too_short"))
            continue
        chunk = None if store is None else await store.get_chunk(chunk_id)
        if chunk is None:
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "not_found"))
            continue
        if chunk.corpus_version_id not in allowed_versions:
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "out_of_scope"))
            continue
        if retrieved is not None and chunk_id not in retrieved:
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "not_retrieved"))
            continue
        if normalized not in normalize_text(chunk.text):
            dropped.append(DroppedCitation(claim.chunk_id, claim.excerpt, "excerpt_mismatch"))
            continue
        seen.add((chunk_id, normalized))
        verified.append(
            VerifiedCitation(
                chunk_id=chunk_id,
                excerpt=claim.excerpt.strip()[:MAX_EXCERPT_CHARS],
                doc_id=chunk.doc_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                method=method,
            )
        )
    return verified, dropped
