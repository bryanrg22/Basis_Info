"""
Fetch tools for direct evidence retrieval.

Wraps evidence_layer.src.retrieval fetch functions as LangChain tools.
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
    get_table as _get_table,
    get_chunk as _get_chunk,
)


@tool
def get_table_tool(
    doc_id: str,
    table_id: str,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Fetch a structured table by ID.

    Tables are NEVER chunked in Basis - they are stored as complete JSON objects.
    Use this when you need exact table data (headers, rows, specific values).

    Args:
        doc_id: Document ID containing the table
        table_id: Table ID to fetch (e.g., "IRS_PUB946_2024_p45_t0")
        corpus: "reference" or "study"
        study_id: Required when corpus="study"

    Returns:
        Table dict with:
        - table_id: Unique identifier
        - headers: Column names
        - rows: Data rows as lists
        - caption: Table title if detected
        - markdown: Rendered markdown representation
        - page: Page number (1-indexed)
        - num_rows: Number of data rows

        Returns None if table not found.
    """
    return _get_table(
        doc_id=doc_id,
        table_id=table_id,
        corpus=corpus,
        study_id=study_id,
    )


@tool
def get_chunk_tool(
    doc_id: str,
    chunk_id: str,
    corpus: str = "reference",
    study_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Fetch a specific chunk by ID.

    Use this to retrieve the full context of a search hit.
    If the chunk is a table surrogate, returns expanded table data.

    Args:
        doc_id: Document ID containing the chunk
        chunk_id: Chunk ID to fetch (e.g., "IRS_PUB946_2024_chunk_15")
        corpus: "reference" or "study"
        study_id: Required when corpus="study"

    Returns:
        Chunk dict with:
        - chunk_id: Unique identifier
        - text: Chunk content
        - type: "text" or "table_summary"
        - page_span: (start_page, end_page) tuple
        - element_ids: Source element IDs for provenance
        - section_path: Hierarchical heading path
        - table: Expanded table data (if table_summary type)

        Returns None if chunk not found.
    """
    return _get_chunk(
        doc_id=doc_id,
        chunk_id=chunk_id,
        corpus=corpus,
        study_id=study_id,
    )
