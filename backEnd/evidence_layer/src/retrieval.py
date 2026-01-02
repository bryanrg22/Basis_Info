"""
Retrieval interface for evidence layer.

Provides stable tool contracts for agentic modules:
- bm25_search(): Exact token matching (IRS codes, section numbers)
- vector_search(): Semantic similarity (paraphrases, fuzzy queries)
- hybrid_search(): Combined BM25 + vector with deduplication
- get_table(): Fetch structured table by ID
- get_chunk(): Fetch chunk by ID
"""

import os
from pathlib import Path
from typing import Optional

from .build_bm25 import load_bm25_index
from .build_faiss import load_vector_index
from .chunk_text import load_chunks
from .extract_tables import load_tables
from .gcs_loader import GCSIndexLoader
from .manifest import Corpus, get_data_dir
from .schemas.chunk import Chunk
from .schemas.table import Table


class EvidenceStore:
    """
    Evidence store for a document.

    Provides unified access to chunks, tables, and indexes.
    Supports both local and GCS-based index loading.
    """

    def __init__(
        self,
        doc_id: str,
        corpus: Corpus,
        study_id: Optional[str] = None,
    ):
        self.doc_id = doc_id
        self.corpus = corpus
        self.study_id = study_id

        # Check if GCS mode is enabled
        self._use_gcs = bool(os.getenv("GCS_BUCKET_NAME"))
        self._gcs_loader: Optional[GCSIndexLoader] = None

        if self._use_gcs:
            self._gcs_loader = GCSIndexLoader(
                bucket_name=os.getenv("GCS_BUCKET_NAME", ""),
                prefix=os.getenv("GCS_INDEX_PREFIX", "indexes"),
            )

        self.data_dir = get_data_dir(corpus, study_id, use_gcs=self._use_gcs)

        # Lazy-loaded resources
        self._chunks: Optional[list[Chunk]] = None
        self._chunks_by_id: Optional[dict[str, Chunk]] = None
        self._tables: Optional[list[Table]] = None
        self._tables_by_id: Optional[dict[str, Table]] = None
        self._bm25_index = None
        self._vector_index = None
    
    @property
    def chunks(self) -> list[Chunk]:
        """Load chunks lazily."""
        if self._chunks is None:
            chunks_path = self.data_dir / "retrieval" / f"{self.doc_id}.chunks.jsonl"
            if chunks_path.exists():
                self._chunks = load_chunks(chunks_path)
            else:
                self._chunks = []
        return self._chunks
    
    @property
    def chunks_by_id(self) -> dict[str, Chunk]:
        """Get chunks indexed by chunk_id."""
        if self._chunks_by_id is None:
            self._chunks_by_id = {c.chunk_id: c for c in self.chunks}
        return self._chunks_by_id
    
    @property
    def tables(self) -> list[Table]:
        """Load tables lazily."""
        if self._tables is None:
            tables_path = self.data_dir / "structured" / f"{self.doc_id}.tables.jsonl"
            if tables_path.exists():
                self._tables = load_tables(tables_path)
            else:
                self._tables = []
        return self._tables
    
    @property
    def tables_by_id(self) -> dict[str, Table]:
        """Get tables indexed by table_id."""
        if self._tables_by_id is None:
            self._tables_by_id = {t.table_id: t for t in self.tables}
        return self._tables_by_id
    
    @property
    def bm25_index(self):
        """Load BM25 index lazily."""
        if self._bm25_index is None:
            if self._use_gcs and self._gcs_loader:
                # Load from GCS with local caching
                try:
                    index_path = self._gcs_loader.get_bm25_index_path(self.doc_id)
                    from .build_bm25 import BM25Index
                    self._bm25_index = BM25Index.load(index_path)
                except FileNotFoundError:
                    pass  # Index not available
            else:
                # Load from local filesystem
                self._bm25_index = load_bm25_index(
                    self.data_dir / "indexes",
                    self.doc_id,
                )
        return self._bm25_index

    @property
    def vector_index(self):
        """Load vector index lazily."""
        if self._vector_index is None:
            if self._use_gcs and self._gcs_loader:
                # Load from GCS with local caching
                try:
                    faiss_path, meta_path = self._gcs_loader.get_faiss_index_paths(
                        self.doc_id
                    )
                    from .build_faiss import FAISSIndex
                    self._vector_index = FAISSIndex.load(faiss_path, meta_path)
                except FileNotFoundError:
                    pass  # Index not available
            else:
                # Load from local filesystem
                self._vector_index = load_vector_index(
                    self.data_dir / "indexes",
                    self.doc_id,
                )
        return self._vector_index


def bm25_search(
    doc_id: str,
    query: str,
    top_k: int = 10,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> list[dict]:
    """
    BM25 lexical search.
    
    Best for:
    - Exact IRS codes: "1245", "168(e)(3)"
    - Asset class numbers: "57.0"
    - Specific phrases: "tangible personal property"
    
    Args:
        doc_id: Document identifier
        query: Search query
        top_k: Number of results
        corpus: "reference" or "study"
        study_id: Required for study corpus
        
    Returns:
        List of result dicts with chunk info and scores
    """
    corpus_enum = Corpus(corpus)
    store = EvidenceStore(doc_id, corpus_enum, study_id)
    
    if store.bm25_index is None:
        return []
    
    results = store.bm25_index.search(query, top_k)
    
    return _expand_results(results, store)


def vector_search(
    doc_id: str,
    query: str,
    top_k: int = 10,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> list[dict]:
    """
    Vector semantic search.
    
    Best for:
    - Paraphrases: "equipment used in business" → matches "tangible personal property"
    - Conceptual queries: "how to depreciate improvements"
    - Fuzzy matching
    
    Args:
        doc_id: Document identifier
        query: Search query
        top_k: Number of results
        corpus: "reference" or "study"
        study_id: Required for study corpus
        
    Returns:
        List of result dicts with chunk info and scores
    """
    corpus_enum = Corpus(corpus)
    store = EvidenceStore(doc_id, corpus_enum, study_id)
    
    if store.vector_index is None:
        return []
    
    results = store.vector_index.search(query, top_k)
    
    return _expand_results(results, store)


def hybrid_search(
    doc_id: str,
    query: str,
    top_k: int = 10,
    corpus: str = "reference",
    study_id: Optional[str] = None,
    bm25_weight: float = 0.5,
) -> list[dict]:
    """
    Hybrid BM25 + vector search with fusion.
    
    Combines lexical and semantic search for best coverage.
    Automatically expands table hits to full table data.
    
    Args:
        doc_id: Document identifier
        query: Search query
        top_k: Number of results
        corpus: "reference" or "study"
        study_id: Required for study corpus
        bm25_weight: Weight for BM25 scores (0-1)
        
    Returns:
        List of result dicts with expanded table data
    """
    corpus_enum = Corpus(corpus)
    store = EvidenceStore(doc_id, corpus_enum, study_id)
    
    # Get results from both
    bm25_results = {}
    vector_results = {}
    
    if store.bm25_index:
        for chunk_id, score in store.bm25_index.search(query, top_k * 2):
            bm25_results[chunk_id] = score
    
    if store.vector_index:
        for chunk_id, score in store.vector_index.search(query, top_k * 2):
            vector_results[chunk_id] = score
    
    # Normalize scores
    def normalize(scores: dict) -> dict:
        if not scores:
            return {}
        max_score = max(scores.values())
        if max_score == 0:
            return {k: 0 for k in scores}
        return {k: v / max_score for k, v in scores.items()}
    
    bm25_norm = normalize(bm25_results)
    vector_norm = normalize(vector_results)
    
    # Fuse scores
    all_chunk_ids = set(bm25_norm.keys()) | set(vector_norm.keys())
    fused = {}
    
    for chunk_id in all_chunk_ids:
        bm25_score = bm25_norm.get(chunk_id, 0)
        vector_score = vector_norm.get(chunk_id, 0)
        fused[chunk_id] = bm25_weight * bm25_score + (1 - bm25_weight) * vector_score
    
    # Sort and take top-k
    sorted_ids = sorted(fused.keys(), key=lambda x: fused[x], reverse=True)[:top_k]
    results = [(chunk_id, fused[chunk_id]) for chunk_id in sorted_ids]
    
    return _expand_results(results, store, expand_tables=True)


def get_table(
    doc_id: str,
    table_id: str,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Fetch a structured table by ID.
    
    Use this when a table surrogate chunk is hit to get
    the full table data (never chunked, never hallucinated).
    
    Args:
        doc_id: Document identifier
        table_id: Table identifier
        corpus: "reference" or "study"
        study_id: Required for study corpus
        
    Returns:
        Table dict with headers, rows, and provenance
    """
    corpus_enum = Corpus(corpus)
    store = EvidenceStore(doc_id, corpus_enum, study_id)
    
    table = store.tables_by_id.get(table_id)
    if table is None:
        return None
    
    return {
        "table_id": table.table_id,
        "doc_id": table.doc_id,
        "page": table.page,
        "caption": table.caption,
        "headers": table.headers,
        "rows": table.rows,
        "num_rows": table.num_rows,
        "markdown": table.to_markdown(),
    }


def get_chunk(
    doc_id: str,
    chunk_id: str,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Fetch a chunk by ID.
    
    Args:
        doc_id: Document identifier
        chunk_id: Chunk identifier
        corpus: "reference" or "study"
        study_id: Required for study corpus
        
    Returns:
        Chunk dict with text and provenance
    """
    corpus_enum = Corpus(corpus)
    store = EvidenceStore(doc_id, corpus_enum, study_id)
    
    chunk = store.chunks_by_id.get(chunk_id)
    if chunk is None:
        return None
    
    result = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "type": chunk.chunk_type.value,
        "text": chunk.text,
        "page_span": chunk.page_span,
        "element_ids": chunk.element_ids,
        "section_path": chunk.section_path,
    }
    
    # Expand table reference if present
    if chunk.table_id:
        result["table_id"] = chunk.table_id
        table = store.tables_by_id.get(chunk.table_id)
        if table:
            result["table"] = {
                "headers": table.headers,
                "rows": table.rows,
                "caption": table.caption,
            }
    
    return result


def _expand_results(
    results: list[tuple[str, float]],
    store: EvidenceStore,
    expand_tables: bool = False,
) -> list[dict]:
    """Expand search results with chunk/table data."""
    expanded = []
    
    for chunk_id, score in results:
        chunk = store.chunks_by_id.get(chunk_id)
        if chunk is None:
            continue
        
        result = {
            "chunk_id": chunk_id,
            "score": score,
            "type": chunk.chunk_type.value,
            "text": chunk.text,
            "page_span": chunk.page_span,
            "section_path": chunk.section_path,
            "doc_id": chunk.doc_id,
        }
        
        # Expand table if this is a table surrogate
        if expand_tables and chunk.table_id:
            table = store.tables_by_id.get(chunk.table_id)
            if table:
                result["table"] = {
                    "table_id": table.table_id,
                    "headers": table.headers,
                    "rows": table.rows,
                    "caption": table.caption,
                    "markdown": table.to_markdown(),
                }
        
        expanded.append(result)
    
    return expanded

