"""Evidence retrieval: PDF parsing, tables, chunking, BM25, embeddings, the chunk store.

Migrated from ``evidence_layer/`` (S3_SPEC section 3). The database is the only store:
no pickles, FAISS files or GCS index downloads. See each module's docstring for what was
kept from the legacy code and what changed and why.

:class:`Retrieval` is what a process binds once (the worker through
``basis.activities.deps.Deps``): one embedder and one BM25 registry. Importing this
package does not import torch or sentence-transformers; the model loads on first use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from basis.retrieval.bm25 import Bm25Registry, process_registry
from basis.retrieval.embed import Embedder, embedder_for

if TYPE_CHECKING:
    from basis.config import Settings


@dataclass(frozen=True, slots=True)
class Retrieval:
    embedder: Embedder
    registry: Bm25Registry


def default_retrieval(settings: Settings) -> Retrieval:
    """The process's embedder for ``settings`` and the process-wide BM25 registry.

    Both are process singletons (``embedder_for`` caches one embedder per model name),
    so calling this repeatedly never loads a model twice.
    """
    return Retrieval(embedder=embedder_for(settings), registry=process_registry())


__all__ = ["Retrieval", "default_retrieval"]
