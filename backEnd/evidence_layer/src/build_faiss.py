"""
FAISS vector index construction.

Uses sentence-transformers for embedding and FAISS for
efficient similarity search.
"""

import json
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from .schemas.chunk import Chunk


# Default embedding model
DEFAULT_MODEL = "all-MiniLM-L6-v2"


class FAISSIndex:
    """
    FAISS vector index with metadata.
    
    Stores embeddings in FAISS and maintains a separate
    metadata mapping for chunk IDs and provenance.
    """
    
    def __init__(
        self,
        index: faiss.Index,
        chunk_ids: list[str],
        doc_id: str,
        model_name: str,
        dimension: int,
    ):
        self.index = index
        self.chunk_ids = chunk_ids
        self.doc_id = doc_id
        self.model_name = model_name
        self.dimension = dimension
        self._model: Optional[SentenceTransformer] = None
    
    def _get_model(self) -> SentenceTransformer:
        """Lazy load the embedding model."""
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model
    
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
        model = self._get_model()
        
        # Embed query
        query_embedding = model.encode([query], normalize_embeddings=True)
        query_vector = np.array(query_embedding, dtype=np.float32)
        
        # Search FAISS
        scores, indices = self.index.search(query_vector, min(top_k, len(self.chunk_ids)))
        
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0 and idx < len(self.chunk_ids):
                results.append((self.chunk_ids[idx], float(score)))
        
        return results
    
    def save(self, index_path: Path, meta_path: Path) -> None:
        """Save index and metadata to files."""
        index_path = Path(index_path)
        meta_path = Path(meta_path)
        
        index_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save FAISS index
        faiss.write_index(self.index, str(index_path))
        
        # Save metadata
        meta = {
            "chunk_ids": self.chunk_ids,
            "doc_id": self.doc_id,
            "model_name": self.model_name,
            "dimension": self.dimension,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
    
    @classmethod
    def load(cls, index_path: Path, meta_path: Path) -> "FAISSIndex":
        """Load index and metadata from files."""
        # Load FAISS index
        index = faiss.read_index(str(index_path))
        
        # Load metadata
        with open(meta_path) as f:
            meta = json.load(f)
        
        return cls(
            index=index,
            chunk_ids=meta["chunk_ids"],
            doc_id=meta["doc_id"],
            model_name=meta["model_name"],
            dimension=meta["dimension"],
        )


def build_vector_index(
    chunks: list[Chunk],
    doc_id: str,
    model_name: str = DEFAULT_MODEL,
    show_progress: bool = True,
) -> FAISSIndex:
    """
    Build a FAISS vector index from chunks.
    
    Args:
        chunks: List of Chunk objects
        doc_id: Document identifier
        model_name: Sentence-transformers model name
        show_progress: Show embedding progress
        
    Returns:
        FAISSIndex ready for search
    """
    if not chunks:
        raise ValueError("No chunks to index")
    
    # Load model
    model = SentenceTransformer(model_name)
    dimension = model.get_sentence_embedding_dimension()
    
    # Extract texts and chunk IDs
    texts = [chunk.text for chunk in chunks]
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    
    # Embed all chunks
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,  # For cosine similarity via inner product
        show_progress_bar=show_progress,
    )
    
    embeddings_array = np.array(embeddings, dtype=np.float32)
    
    # Build FAISS index
    # Using IndexFlatIP for inner product (cosine similarity with normalized vectors)
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings_array)
    
    return FAISSIndex(
        index=index,
        chunk_ids=chunk_ids,
        doc_id=doc_id,
        model_name=model_name,
        dimension=dimension,
    )


def save_vector_index(index: FAISSIndex, output_dir: Path, doc_id: str) -> tuple[Path, Path]:
    """
    Save vector index to standard location.
    
    Args:
        index: FAISSIndex to save
        output_dir: Base output directory (e.g., data/reference/indexes)
        doc_id: Document identifier
        
    Returns:
        Tuple of (index_path, meta_path)
    """
    output_dir = Path(output_dir)
    index_path = output_dir / "vector" / f"{doc_id}.faiss"
    meta_path = output_dir / "vector" / f"{doc_id}.meta.json"
    
    index.save(index_path, meta_path)
    return index_path, meta_path


def load_vector_index(index_dir: Path, doc_id: str) -> Optional[FAISSIndex]:
    """
    Load vector index from standard location.
    
    Args:
        index_dir: Base index directory
        doc_id: Document identifier
        
    Returns:
        FAISSIndex or None if not found
    """
    index_path = Path(index_dir) / "vector" / f"{doc_id}.faiss"
    meta_path = Path(index_dir) / "vector" / f"{doc_id}.meta.json"
    
    if not index_path.exists() or not meta_path.exists():
        return None
    
    return FAISSIndex.load(index_path, meta_path)

