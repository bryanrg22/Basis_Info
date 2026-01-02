"""
BM25 index construction.

Uses IRS-aware tokenization for optimal retrieval of
section codes, asset classes, and legal references.
"""

import pickle
from pathlib import Path
from typing import Optional

from rank_bm25 import BM25Okapi

from .schemas.chunk import Chunk
from .tokenizers import get_tokenizer, irs_tokenize


class BM25Index:
    """
    BM25 index with IRS-aware tokenization.
    
    Wraps rank_bm25 with custom tokenization and
    metadata for result mapping.
    """
    
    def __init__(
        self,
        bm25: BM25Okapi,
        chunk_ids: list[str],
        doc_id: str,
        tokenizer_name: str = "irs",
    ):
        self.bm25 = bm25
        self.chunk_ids = chunk_ids
        self.doc_id = doc_id
        self.tokenizer_name = tokenizer_name
        self._tokenizer = get_tokenizer(tokenizer_name)
    
    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        Search the index.
        
        Args:
            query: Search query
            top_k: Number of results
            
        Returns:
            List of (chunk_id, score) tuples
        """
        query_tokens = self._tokenizer(query)
        
        if not query_tokens:
            return []
        
        scores = self.bm25.get_scores(query_tokens)
        
        # Get top-k indices
        top_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True
        )[:top_k]
        
        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                results.append((self.chunk_ids[idx], scores[idx]))
        
        return results
    
    def save(self, path: Path) -> None:
        """Save index to pickle file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "wb") as f:
            pickle.dump({
                "bm25": self.bm25,
                "chunk_ids": self.chunk_ids,
                "doc_id": self.doc_id,
                "tokenizer_name": self.tokenizer_name,
            }, f)
    
    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        """Load index from pickle file."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        
        return cls(
            bm25=data["bm25"],
            chunk_ids=data["chunk_ids"],
            doc_id=data["doc_id"],
            tokenizer_name=data.get("tokenizer_name", "irs"),
        )


def build_bm25_index(
    chunks: list[Chunk],
    doc_id: str,
    doc_type: str = "irs",
) -> BM25Index:
    """
    Build a BM25 index from chunks.
    
    Args:
        chunks: List of Chunk objects
        doc_id: Document identifier
        doc_type: Document type (for tokenizer selection)
        
    Returns:
        BM25Index ready for search
    """
    tokenizer = get_tokenizer(doc_type)
    
    # Tokenize all chunks
    tokenized_corpus: list[list[str]] = []
    chunk_ids: list[str] = []
    
    for chunk in chunks:
        tokens = tokenizer(chunk.text)
        if tokens:  # Skip empty chunks
            tokenized_corpus.append(tokens)
            chunk_ids.append(chunk.chunk_id)
    
    if not tokenized_corpus:
        raise ValueError("No valid chunks to index")
    
    # Build BM25 index
    bm25 = BM25Okapi(tokenized_corpus)
    
    return BM25Index(
        bm25=bm25,
        chunk_ids=chunk_ids,
        doc_id=doc_id,
        tokenizer_name=doc_type,
    )


def save_bm25_index(index: BM25Index, output_dir: Path, doc_id: str) -> Path:
    """
    Save BM25 index to standard location.
    
    Args:
        index: BM25Index to save
        output_dir: Base output directory (e.g., data/reference/indexes)
        doc_id: Document identifier
        
    Returns:
        Path to saved index file
    """
    output_path = Path(output_dir) / "bm25" / f"{doc_id}.bm25.pkl"
    index.save(output_path)
    return output_path


def load_bm25_index(index_dir: Path, doc_id: str) -> Optional[BM25Index]:
    """
    Load BM25 index from standard location.
    
    Args:
        index_dir: Base index directory
        doc_id: Document identifier
        
    Returns:
        BM25Index or None if not found
    """
    index_path = Path(index_dir) / "bm25" / f"{doc_id}.bm25.pkl"
    
    if not index_path.exists():
        return None
    
    return BM25Index.load(index_path)

