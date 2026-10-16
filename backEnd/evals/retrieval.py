"""Chunk-level retrieval systems for ``retrieval_qrels`` (EVAL_REPORT section 22).

``bm25_pages`` (in :mod:`evals.run`) ranks whole pages. The systems here rank the
chunks production stores and searches, then read the ranking at page level (the qrels
unit): the chunks' ``doc_id:page`` keys in rank order, de-duplicated
(:func:`basis.retrieval.metrics.page_keys`), cut at 10.

* ``bm25_chunks``: :class:`basis.retrieval.bm25.Bm25Index`, the index
  :class:`~basis.retrieval.store.PostgresChunkStore` builds, over the same rows in the
  same order (``chunk_key``), so the ranking is the store's.
* ``vector_chunks``: cosine similarity between the query embedding and every chunk
  embedding, exact (a full scan). The store uses pgvector's HNSW index with
  ``iterative_scan = strict_order``, an approximate search; the real-corpus test
  (``tests/integration/retrieval/test_real_corpus.py``) measures how often the two
  disagree.
* ``hybrid``: :func:`basis.retrieval.hybrid.hybrid_search` itself, unchanged, over this
  in-memory index, with the production fusion: :func:`hybrid_search`'s defaults unless
  ``Settings`` says otherwise (:func:`production_fusion`).

The chunks come from the production path: :func:`basis.retrieval.parse.parse_pdf`
(reference page limit) then :func:`basis.retrieval.chunk.chunk_document`, embedded by
the production embedder (``EMBEDDING_MODEL``, default MiniLM-L6-v2). Each query keeps
the top :data:`CHUNK_DEPTH` chunks; that is always enough for 10 distinct pages here
(the run records how many pages each query got).

:func:`fusion_sweep` scores weighted fusion over a grid of BM25 weights and RRF, and
estimates what choosing the weight on these queries is worth out of sample with
leave-one-out cross-validation (pick the weight on 49 queries, score the 50th).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from basis.retrieval.bm25 import Bm25Doc, Bm25Index
from basis.retrieval.chunk import CHUNK_TOKENS, OVERLAP_TOKENS
from basis.retrieval.embed import Embedder
from basis.retrieval.hybrid import FusionMethod, fuse, hybrid_search
from basis.retrieval.metrics import page_keys
from basis.retrieval.types import Chunk, Hit, Table

CHUNK_VERSION: Final = uuid.UUID(int=2)
CHUNK_DEPTH: Final = 50  # chunks per query; the page ranking is cut at PAGE_DEPTH
PAGE_DEPTH: Final = 10
CHUNK_SYSTEMS: Final = ("bm25_chunks", "vector_chunks", "hybrid")
SWEEP_WEIGHTS: Final = tuple(round(0.1 * i, 1) for i in range(11))


@dataclass(frozen=True, slots=True)
class CorpusChunk:
    doc_id: str
    page: int
    chunk_key: str
    text: str


def parser_chunks(
    pdfs: Mapping[str, Path],
    *,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[CorpusChunk]:
    """Every chunk production ingestion makes from ``pdfs`` at this chunk window,
    ordered by ``chunk_key``."""
    from basis.retrieval.chunk import chunk_document
    from basis.retrieval.parse import REFERENCE_MAX_PAGES, parse_pdf

    chunks: list[CorpusChunk] = []
    for doc_id, path in pdfs.items():
        document = parse_pdf(path.read_bytes(), doc_id, max_pages=REFERENCE_MAX_PAGES)
        drafts = chunk_document(document, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens)
        chunks += [CorpusChunk(d.doc_id, d.page, d.chunk_key, d.text) for d in drafts]
    # The store loads BM25 rows ``ORDER BY chunk_key`` (string order); ties keep it.
    return sorted(chunks, key=lambda c: c.chunk_key)


class ChunkIndex:
    """BM25 and exact cosine search over one in-memory chunk set.

    It has the :class:`~basis.retrieval.store.ChunkStore` read methods
    :func:`hybrid_search` calls, so the production fusion runs unchanged on it.
    """

    def __init__(self, chunks: Sequence[CorpusChunk], embeddings: Sequence[Sequence[float]]):
        if len(chunks) != len(embeddings):
            raise ValueError("one embedding per chunk")
        self.chunks = list(chunks)
        self._ids = [uuid.uuid5(CHUNK_VERSION, c.chunk_key) for c in self.chunks]
        self._by_id = dict(zip(self._ids, self.chunks, strict=True))
        self._bm25 = Bm25Index(
            Bm25Doc(chunk_id=cid, text=c.text)
            for cid, c in zip(self._ids, self.chunks, strict=True)
        )
        matrix = (
            np.asarray(embeddings, dtype=np.float64).reshape(len(self.chunks), -1)
            if self.chunks
            else np.zeros((0, 1))
        )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self._matrix = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)

    def _hit(self, chunk_id: uuid.UUID, score: float) -> Hit:
        chunk = self._by_id[chunk_id]
        return Hit(chunk_id, chunk.doc_id, chunk.page, chunk.page, score, chunk.text)

    async def search_bm25(self, corpus_version_id: uuid.UUID, query: str, k: int) -> list[Hit]:
        return self.bm25(query, k)

    def bm25(self, query: str, k: int) -> list[Hit]:
        return [self._hit(cid, score) for cid, score in self._bm25.search(query, k)]

    async def search_vector(
        self, corpus_version_id: uuid.UUID, query_embedding: Sequence[float], k: int
    ) -> list[Hit]:
        return self.vector(query_embedding, k)

    def vector(self, query_embedding: Sequence[float], k: int) -> list[Hit]:
        """Top ``k`` by cosine similarity; ties by ``chunk_key`` (as the store orders)."""
        if k <= 0 or not self.chunks:
            return []
        query = np.asarray(query_embedding, dtype=np.float64)
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            return []
        scores = self._matrix @ (query / norm)
        # Stable sort on -score keeps chunk_key order (the chunks are sorted) among ties.
        order = np.argsort(-scores, kind="stable")[:k]
        return [self._hit(self._ids[i], float(scores[i])) for i in order]

    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None:
        chunk = self._by_id.get(chunk_id)
        if chunk is None:
            return None
        return Chunk(chunk_id, CHUNK_VERSION, chunk.doc_id, chunk.chunk_key, chunk.page,
                     chunk.page, None, chunk.text, None)  # fmt: skip

    async def get_table(self, table_id: uuid.UUID) -> Table | None:
        return None


def pages(hits: Sequence[Hit], depth: int = PAGE_DEPTH) -> list[str]:
    """The ``doc_id:page`` ranking of chunk hits, de-duplicated, top ``depth``."""
    return page_keys(hits)[:depth]


@dataclass(frozen=True, slots=True)
class Fusion:
    method: FusionMethod
    weight: float


def production_fusion() -> Fusion:
    """The fusion production's :func:`hybrid_search` uses: the configured settings."""
    from basis.config import get_settings

    settings = get_settings()
    return Fusion(settings.retrieval_fusion, settings.retrieval_bm25_weight)


@dataclass(frozen=True, slots=True)
class ChunkRuns:
    """Per query: the chunk hits of each retriever (enough to fuse at any setting)."""

    bm25: dict[str, list[Hit]]
    vector: dict[str, list[Hit]]
    hybrid: dict[str, list[Hit]]
    fusion: Fusion

    def page_run(self, system: str) -> dict[str, list[str]]:
        hits = {"bm25_chunks": self.bm25, "vector_chunks": self.vector, "hybrid": self.hybrid}
        return {qid: pages(h) for qid, h in hits[system].items()}


def run_chunk_systems(
    index: ChunkIndex,
    embedder: Embedder,
    queries: Mapping[str, str],
    fusion: Fusion,
    depth: int = CHUNK_DEPTH,
) -> ChunkRuns:
    """Each query through BM25, vector and the production ``hybrid_search``.

    BM25 and vector keep ``depth * CANDIDATE_FACTOR`` hits, the candidates
    ``hybrid_search(k=depth)`` fuses, so :func:`fusion_sweep` can re-fuse them exactly.
    """
    from basis.retrieval.hybrid import CANDIDATE_FACTOR

    candidates = depth * CANDIDATE_FACTOR
    ids = list(queries)
    vectors = embedder.embed_query([queries[q] for q in ids])
    bm25 = {q: index.bm25(queries[q], candidates) for q in ids}
    vector = {q: index.vector(v, candidates) for q, v in zip(ids, vectors, strict=True)}

    async def hybrid_all() -> dict[str, list[Hit]]:
        return {
            q: await hybrid_search(
                index,
                embedder,
                CHUNK_VERSION,
                queries[q],
                depth,
                method=fusion.method,
                weight=fusion.weight,
            )  # fmt: skip
            for q in ids
        }

    return ChunkRuns(bm25, vector, asyncio.run(hybrid_all()), fusion)


def refuse(runs: ChunkRuns, fusion: Fusion, depth: int = CHUNK_DEPTH) -> dict[str, list[str]]:
    """The page run of ``fuse`` at another setting over the same candidates."""
    return {
        q: pages(fuse(runs.bm25[q], runs.vector[q], fusion.method, fusion.weight, limit=depth))
        for q in runs.bm25
    }


Scorer = Callable[[Mapping[str, Sequence[str]]], dict[str, float]]


@dataclass(frozen=True, slots=True)
class SweepRow:
    label: str
    fusion: Fusion
    mean: float
    per_query: dict[str, float]


def fusion_sweep(
    runs: ChunkRuns, score: Scorer, weights: Sequence[float] = SWEEP_WEIGHTS
) -> tuple[list[SweepRow], dict[str, float], list[str]]:
    """Every fusion setting's per-query metric, and leave-one-out choice of the weight.

    Returns the rows (weighted at each weight, then RRF), the leave-one-out per-query
    score (for each query, the setting with the best mean on the other queries; ties go
    to the earlier row), and the setting chosen for each query.
    """
    settings = [Fusion("weighted", w) for w in weights] + [Fusion("rrf", 0.5)]
    rows: list[SweepRow] = []
    for setting in settings:
        per_query = score(refuse(runs, setting))
        label = "rrf" if setting.method == "rrf" else f"weighted {setting.weight:.1f}"
        rows.append(SweepRow(label, setting, sum(per_query.values()) / len(per_query), per_query))
    queries = sorted(rows[0].per_query)
    loo: dict[str, float] = {}
    chosen: list[str] = []
    for held in queries:

        def rest(row: SweepRow, held: str = held) -> float:
            return sum(v for q, v in row.per_query.items() if q != held)

        best = max(rows, key=rest)  # max keeps the first of equal maxima
        loo[held] = best.per_query[held]
        chosen.append(best.label)
    return rows, loo, chosen


# --------------------------------------------------------------------------- rule citations
# How many rules-table citations the production resolver finds through each search
# (``RETRIEVAL_SEARCH``): :func:`basis.classify.router.resolve_rule_citations` searches the
# excerpt and keeps a hit on the cited document and page that contains it (EVAL_REPORT
# section 25).


@dataclass(frozen=True, slots=True)
class RuleResolution:
    search: str
    claimed: int
    resolved: int
    unresolved: list[str]  # "<rule key> <doc_id>:p<page>"


def resolve_rules(index: ChunkIndex, embedder: Embedder, fusion: Fusion) -> list[RuleResolution]:
    """Every rules-table citation through ``resolve_rule_citations`` over ``index``, once
    with the classifier's default search (the store's BM25) and once with the hybrid."""
    from basis.classify.router import ClassifyContext, resolve_rule_citations
    from basis.domain.rules import RULES, lookup
    from basis.llm.provider import Budget
    from basis.retrieval import Retrieval
    from basis.retrieval.bm25 import Bm25Registry

    retrieval = Retrieval(embedder, Bm25Registry(), fusion.method, fusion.weight, "hybrid")

    async def one(search: str) -> RuleResolution:
        ctx = ClassifyContext(
            study_id="eval-rule-citations",
            property_class="residential_rental",
            budget=Budget(remaining_usd=0.0),
            store=index,
            reference_version_id=CHUNK_VERSION,
            search=retrieval.classifier_search(index) if search == "hybrid" else None,
        )
        claimed = resolved = 0
        unresolved: list[str] = []
        for key in RULES:
            hit = lookup(key)
            if hit is None:  # pragma: no cover - every key looks itself up
                continue
            verified, dropped = await resolve_rule_citations(hit, ctx)
            claimed += len(hit.citations)
            resolved += len(verified)
            unresolved += [f"{key} {d.chunk_id}" for d in dropped]
        return RuleResolution(search, claimed, resolved, unresolved)

    async def both() -> list[RuleResolution]:
        return [await one("bm25"), await one("hybrid")]

    return asyncio.run(both())


# --------------------------------------------------------------------------- sentences
# The verbatim-sentence check of EVAL_REPORT section 20: sentences from pymupdf's page text
# (pymupdf reads Pub 527 and Pub 946 column by column, independently of our parser) must
# survive, as the production citation matcher sees them, in the parser's output.

SENTENCE_MIN_CHARS: Final = 40
SENTENCE_MAX_CHARS: Final = 200
_SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"\u201c])")


def reference_sentences(page_text: str) -> list[str]:
    """Sentences of 40 to 200 characters of ``page_text`` (whitespace collapsed).

    A sentence ends at ``.``, ``!`` or ``?`` followed by whitespace and a capital, an
    opening parenthesis or quote. Fragments outside the length window (headings, table
    cells, run-on text with no sentence end) are not sentences for this check.
    """
    flat = " ".join(page_text.split())
    return [
        s
        for s in (part.strip() for part in _SENTENCE_END.split(flat))
        if SENTENCE_MIN_CHARS <= len(s) <= SENTENCE_MAX_CHARS
    ]


def intact_sentences(sentences: Sequence[str], chunk_texts: Sequence[str]) -> list[bool]:
    """Per sentence: whether one chunk holds it whole (``excerpt_supported``, the
    normalized-substring test a citation must pass against one chunk)."""
    from basis.classify.verify import excerpt_supported

    return [any(excerpt_supported(s, text) for text in chunk_texts) for s in sentences]


__all__ = [
    "CHUNK_DEPTH",
    "CHUNK_SYSTEMS",
    "ChunkIndex",
    "ChunkRuns",
    "CorpusChunk",
    "Fusion",
    "fusion_sweep",
    "intact_sentences",
    "pages",
    "parser_chunks",
    "production_fusion",
    "reference_sentences",
    "refuse",
    "resolve_rules",
    "run_chunk_systems",
]
