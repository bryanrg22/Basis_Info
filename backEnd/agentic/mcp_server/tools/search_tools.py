"""
Search tools for evidence retrieval.

Wraps evidence_layer.src.retrieval search functions as LangChain tools.
"""

import sys
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

# Add evidence_layer to path for imports
EVIDENCE_LAYER_PATH = Path(__file__).parent.parent.parent.parent / "evidence_layer"
if str(EVIDENCE_LAYER_PATH) not in sys.path:
    sys.path.insert(0, str(EVIDENCE_LAYER_PATH))

from src.retrieval import (
    bm25_search as _bm25_search,
    vector_search as _vector_search,
    hybrid_search as _hybrid_search,
)


@tool
def bm25_search_tool(
    doc_id: str,
    query: str,
    top_k: int = 10,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> list[dict]:
    """
    BM25 lexical search for exact token matching.

    Best for:
    - IRS codes: "1245", "168(e)(3)", "1250"
    - Asset class numbers: "57.0", "00.11"
    - Exact phrases: "tangible personal property"
    - Section references: "Section 1245 property"

    Args:
        doc_id: Document ID to search (e.g., "IRS_PUB946_2024")
        query: Search query text
        top_k: Number of results to return (default 10)
        corpus: "reference" for IRS/RSMeans, "study" for property documents
        study_id: Required when corpus="study"

    Returns:
        List of evidence results with chunk_id, text, page_span, and score
    """
    return _bm25_search(
        doc_id=doc_id,
        query=query,
        top_k=top_k,
        corpus=corpus,
        study_id=study_id,
    )


@tool
def vector_search_tool(
    doc_id: str,
    query: str,
    top_k: int = 10,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> list[dict]:
    """
    Semantic vector search for conceptual similarity.

    Best for:
    - Paraphrases: "equipment used in business" matches "tangible personal property"
    - Conceptual queries: "how to depreciate building improvements"
    - Fuzzy matching when exact terms are unknown

    Args:
        doc_id: Document ID to search (e.g., "IRS_PUB946_2024")
        query: Search query text
        top_k: Number of results to return (default 10)
        corpus: "reference" for IRS/RSMeans, "study" for property documents
        study_id: Required when corpus="study"

    Returns:
        List of evidence results with chunk_id, text, page_span, and score
    """
    return _vector_search(
        doc_id=doc_id,
        query=query,
        top_k=top_k,
        corpus=corpus,
        study_id=study_id,
    )


@tool
def hybrid_search_tool(
    doc_id: str,
    query: str,
    top_k: int = 10,
    corpus: str = "reference",
    study_id: Optional[str] = None,
    bm25_weight: float = 0.5,
) -> list[dict]:
    """
    Combined BM25 + vector search with score fusion.

    Recommended for general queries where both lexical and semantic matching are useful.
    Automatically expands table surrogate hits to include full table data.

    Args:
        doc_id: Document ID to search (e.g., "IRS_PUB946_2024")
        query: Search query text
        top_k: Number of results to return (default 10)
        corpus: "reference" for IRS/RSMeans, "study" for property documents
        study_id: Required when corpus="study"
        bm25_weight: Weight for BM25 scores (0.0-1.0). Default 0.5 for balanced.

    Returns:
        List of evidence results with chunk_id, text, page_span, score,
        and expanded table data when applicable
    """
    return _hybrid_search(
        doc_id=doc_id,
        query=query,
        top_k=top_k,
        corpus=corpus,
        study_id=study_id,
        bm25_weight=bm25_weight,
    )
