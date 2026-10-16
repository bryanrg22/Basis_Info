"""Fusion of BM25 and vector results (S3_SPEC section 3).

Two methods, both deterministic:

* ``weighted`` (the legacy ``hybrid_search`` behaviour): each list's scores are divided
  by that list's maximum, then ``weight * bm25 + (1 - weight) * vector``; a chunk missing
  from a list contributes 0 for it. When a list's maximum is not positive its normalized
  scores are all 0 (the legacy rule).
* ``rrf``: reciprocal rank fusion (Cormack, Clarke and Buettcher, SIGIR 2009),
  ``sum 1 / (k + rank)`` over the lists that contain the chunk, rank 1-based, k = 60.
  ``weight`` does not apply to RRF.

Ties (equal fused score) are broken by the rank in the list that ``weight`` favours
(BM25 when ``weight >= 0.5``), then the rank in the other list, then the chunk id. So
``weight=1.0`` reproduces the BM25 order exactly and ``weight=0.0`` the vector order,
including ties inside each list. A chunk appearing twice in one list keeps its first
(best) position.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Final, Literal

from basis.retrieval.embed import Embedder
from basis.retrieval.store import ChunkStore
from basis.retrieval.types import Hit

FusionMethod = Literal["weighted", "rrf"]
RRF_K: Final = 60
CANDIDATE_FACTOR: Final = 2  # each retriever fetches k * 2 candidates (legacy)


def _first_positions(hits: Sequence[Hit]) -> dict[uuid.UUID, int]:
    positions: dict[uuid.UUID, int] = {}
    for position, hit in enumerate(hits):
        positions.setdefault(hit.chunk_id, position)
    return positions


def _max_normalized(hits: Sequence[Hit], positions: dict[uuid.UUID, int]) -> dict[uuid.UUID, float]:
    if not positions:
        return {}
    top = max(hits[p].score for p in positions.values())
    if top <= 0:
        return dict.fromkeys(positions, 0.0)
    return {chunk_id: hits[p].score / top for chunk_id, p in positions.items()}


def fuse(
    bm25: Sequence[Hit],
    vec: Sequence[Hit],
    method: FusionMethod,
    weight: float = 0.5,
    k: int = RRF_K,
    *,
    limit: int | None = None,
) -> list[Hit]:
    """The union of both lists ordered by fused score; ``score`` is the fused score."""
    if not 0.0 <= weight <= 1.0 or math.isnan(weight):
        raise ValueError("weight must be within [0, 1]")
    if k <= 0:
        raise ValueError("k must be positive")
    bm25_pos = _first_positions(bm25)
    vec_pos = _first_positions(vec)
    ids = list(bm25_pos) + [chunk_id for chunk_id in vec_pos if chunk_id not in bm25_pos]

    if method == "weighted":
        b_norm = _max_normalized(bm25, bm25_pos)
        v_norm = _max_normalized(vec, vec_pos)
        fused = {
            cid: weight * b_norm.get(cid, 0.0) + (1.0 - weight) * v_norm.get(cid, 0.0)
            for cid in ids
        }
    elif method == "rrf":
        fused = {
            cid: (1.0 / (k + bm25_pos[cid] + 1) if cid in bm25_pos else 0.0)
            + (1.0 / (k + vec_pos[cid] + 1) if cid in vec_pos else 0.0)
            for cid in ids
        }
    else:
        raise ValueError(f"unknown fusion method {method!r}")

    absent = len(bm25) + len(vec) + 1
    bm25_first = weight >= 0.5

    def order(cid: uuid.UUID) -> tuple[float, int, int, str]:
        b_rank = bm25_pos.get(cid, absent)
        v_rank = vec_pos.get(cid, absent)
        first, second = (b_rank, v_rank) if bm25_first else (v_rank, b_rank)
        return (-fused[cid], first, second, str(cid))

    ranked = sorted(ids, key=order)
    if limit is not None:
        ranked = ranked[:limit]
    source = {cid: bm25[p] for cid, p in bm25_pos.items()}
    for cid, p in vec_pos.items():
        source.setdefault(cid, vec[p])
    return [replace(source[cid], score=fused[cid]) for cid in ranked]


async def hybrid_search(
    store: ChunkStore,
    embedder: Embedder,
    corpus_version_id: uuid.UUID,
    query: str,
    k: int,
    *,
    method: FusionMethod = "rrf",
    weight: float = 0.5,
) -> list[Hit]:
    """BM25 and vector search over one corpus version, fused, top ``k``.

    The production fusion is the settings (``RETRIEVAL_FUSION``,
    ``RETRIEVAL_BM25_WEIGHT``) bound on :class:`basis.retrieval.Retrieval`; call
    ``Retrieval.hybrid`` to get it. These defaults equal the settings' defaults (RRF,
    EVAL_REPORT section 22), which a unit test keeps true.
    """
    candidates = k * CANDIDATE_FACTOR
    bm25 = await store.search_bm25(corpus_version_id, query, candidates)
    [embedding] = await asyncio.to_thread(embedder.embed_query, [query])
    vec = await store.search_vector(corpus_version_id, embedding, candidates)
    return fuse(bm25, vec, method, weight, limit=k)
