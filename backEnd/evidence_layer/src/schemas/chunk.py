"""
Chunk schema for retrieval units.

Chunks are the atomic units for BM25 and vector search:
- text: Overlapped narrative chunks from paragraphs/headings
- table_summary: Searchable surrogate pointing to a table_id

Every chunk includes full provenance (doc_id, page_span, element_ids)
so we can always trace back to the source.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ChunkType(str, Enum):
    """Type of retrieval chunk."""
    
    TEXT = "text"
    TABLE_SUMMARY = "table_summary"


class Chunk(BaseModel):
    """
    A retrieval unit for BM25/vector search.
    
    Includes full provenance so every search result
    can be traced back to its source page and elements.
    """
    
    chunk_id: str = Field(
        ...,
        description="Unique identifier: {doc_id}_chunk_{index}",
        examples=["DOC001_chunk_0", "DOC001_chunk_42"]
    )
    doc_id: str = Field(..., description="Parent document identifier")
    chunk_type: ChunkType = Field(..., description="Type of chunk")
    text: str = Field(..., description="Chunk text content (for indexing)")
    
    # Provenance
    page_span: tuple[int, int] = Field(
        ...,
        description="(start_page, end_page) - 1-indexed, inclusive"
    )
    element_ids: list[str] = Field(
        ...,
        description="IDs of source elements that contributed to this chunk"
    )
    
    # For table_summary chunks only
    table_id: Optional[str] = Field(
        None,
        description="ID of the table this chunk summarizes (for table_summary type)"
    )
    
    # Token info (for debugging/analysis)
    token_count: Optional[int] = Field(
        None,
        description="Number of tokens in this chunk"
    )
    
    # Structural context
    section_path: Optional[list[str]] = Field(
        None,
        description="Hierarchical path of headings for this chunk",
        examples=[["Chapter 4", "MACRS", "Recovery Periods"]]
    )
    
    def is_table_hit(self) -> bool:
        """Check if this chunk is a table surrogate."""
        return self.chunk_type == ChunkType.TABLE_SUMMARY and self.table_id is not None
    
    class Config:
        json_schema_extra = {
            "example": {
                "chunk_id": "IRS_PUB946_2024_chunk_15",
                "doc_id": "IRS_PUB946_2024",
                "chunk_type": "text",
                "text": "Section 1245 property. This type of property includes any property that is or has been property of a character subject to the allowance for depreciation provided in section 167 and is either personal property or other tangible property...",
                "page_span": [12, 12],
                "element_ids": ["IRS_PUB946_2024_p12_e3", "IRS_PUB946_2024_p12_e4"],
                "token_count": 387,
                "section_path": ["How To Depreciate Property", "Section 1245 Property"]
            }
        }

