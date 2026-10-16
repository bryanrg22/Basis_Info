"""Evidence retrieval: PDF parsing, tables, chunking, BM25, embeddings, the chunk store.

Migrated from ``evidence_layer/`` (S3_SPEC section 3). The database is the only store:
no pickles, FAISS files or GCS index downloads. See each module's docstring for what was
kept from the legacy code and what changed and why.

:class:`Retrieval` is what a process binds once (the worker through
``basis.activities.deps.Deps``): one embedder, one BM25 registry, and the hybrid fusion
from settings (``RETRIEVAL_FUSION``, ``RETRIEVAL_BM25_WEIGHT``; EVAL_REPORT section 22).
Importing this package does not import torch or sentence-transformers; the model loads
on first use.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from basis.retrieval.bm25 import Bm25Registry, process_registry
from basis.retrieval.embed import Embedder, embedder_for

if TYPE_CHECKING:
    from basis.config import Settings
    from basis.retrieval.store import ChunkStore
    from basis.retrieval.types import Hit


@dataclass(frozen=True, slots=True)
class Retrieval:
    embedder: Embedder
    registry: Bm25Registry
    # The Settings defaults (tests/unit/retrieval/test_retrieval_binding.py keeps them equal).
    fusion: Literal["rrf", "weighted"] = "rrf"
    bm25_weight: float = 0.5

    async def hybrid(
        self, store: ChunkStore, corpus_version_id: uuid.UUID, query: str, k: int
    ) -> list[Hit]:
        """:func:`basis.retrieval.hybrid.hybrid_search` with this process's fusion."""
        from basis.retrieval.hybrid import hybrid_search

        return await hybrid_search(
            store,
            self.embedder,
            corpus_version_id,
            query,
            k,
            method=self.fusion,
            weight=self.bm25_weight,
        )


def default_retrieval(settings: Settings) -> Retrieval:
    """The process's embedder for ``settings`` and the process-wide BM25 registry.

    Both are process singletons (``embedder_for`` caches one embedder per model name),
    so calling this repeatedly never loads a model twice.
    """
    return Retrieval(
        embedder=embedder_for(settings),
        registry=process_registry(),
        fusion=settings.retrieval_fusion,
        bm25_weight=settings.retrieval_bm25_weight,
    )


__all__ = ["Retrieval", "default_retrieval"]
