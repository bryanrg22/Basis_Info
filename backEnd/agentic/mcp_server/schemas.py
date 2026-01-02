"""
Pydantic schemas for MCP tool inputs and outputs.

These schemas provide validation and JSON Schema generation
for MCP tool definitions.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Input Schemas
# =============================================================================


class SearchInput(BaseModel):
    """Input schema for BM25 and vector search tools."""

    doc_id: str = Field(
        ...,
        description="Document ID to search within (e.g., 'IRS_PUB946_2024')",
    )
    query: str = Field(
        ...,
        description="Search query text",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of results to return",
    )
    corpus: Literal["reference", "study"] = Field(
        default="reference",
        description="Corpus to search: 'reference' for IRS/RSMeans, 'study' for property docs",
    )
    study_id: Optional[str] = Field(
        default=None,
        description="Study ID (required when corpus='study')",
    )


class HybridSearchInput(SearchInput):
    """Extended input for hybrid search with BM25 weight."""

    bm25_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Weight for BM25 scores (0=vector only, 1=BM25 only, 0.5=balanced)",
    )


class GetTableInput(BaseModel):
    """Input for fetching a specific table by ID."""

    doc_id: str = Field(
        ...,
        description="Document ID containing the table",
    )
    table_id: str = Field(
        ...,
        description="Table ID to fetch (e.g., 'IRS_PUB946_2024_p45_t0')",
    )
    corpus: Literal["reference", "study"] = Field(
        default="reference",
    )
    study_id: Optional[str] = Field(default=None)


class GetChunkInput(BaseModel):
    """Input for fetching a specific chunk by ID."""

    doc_id: str = Field(
        ...,
        description="Document ID containing the chunk",
    )
    chunk_id: str = Field(
        ...,
        description="Chunk ID to fetch (e.g., 'IRS_PUB946_2024_chunk_15')",
    )
    corpus: Literal["reference", "study"] = Field(
        default="reference",
    )
    study_id: Optional[str] = Field(default=None)


# =============================================================================
# Output Schemas
# =============================================================================


class EvidenceResult(BaseModel):
    """Search result with provenance for citations."""

    chunk_id: str = Field(..., description="Unique chunk identifier")
    doc_id: str = Field(..., description="Source document ID")
    score: float = Field(..., description="Relevance score (0-1 normalized)")
    type: Literal["text", "table_summary"] = Field(
        ..., description="Chunk type"
    )
    text: str = Field(..., description="Chunk content")
    page_span: tuple[int, int] = Field(
        ..., description="(start_page, end_page) inclusive"
    )
    section_path: Optional[list[str]] = Field(
        default=None, description="Hierarchical heading path"
    )
    table: Optional["TableResult"] = Field(
        default=None, description="Expanded table data (for table_summary chunks)"
    )


class TableResult(BaseModel):
    """Structured table result."""

    table_id: str = Field(..., description="Unique table identifier")
    doc_id: str = Field(..., description="Source document ID")
    page: int = Field(..., description="Page number (1-indexed)")
    caption: Optional[str] = Field(default=None, description="Table caption/title")
    headers: list[str] = Field(..., description="Column headers")
    rows: list[list[Any]] = Field(..., description="Table data rows")
    num_rows: int = Field(..., description="Number of data rows")
    markdown: str = Field(..., description="Markdown representation")


class ChunkResult(BaseModel):
    """Full chunk result with optional table expansion."""

    chunk_id: str
    doc_id: str
    type: Literal["text", "table_summary"]
    text: str
    page_span: tuple[int, int]
    element_ids: list[str] = Field(
        default_factory=list, description="Source element IDs"
    )
    section_path: Optional[list[str]] = None
    table_id: Optional[str] = Field(
        default=None, description="Table ID (for table_summary)"
    )
    table: Optional[TableResult] = Field(
        default=None, description="Expanded table (if table_summary)"
    )


# Update forward references
EvidenceResult.model_rebuild()
