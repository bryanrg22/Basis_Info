"""
Table schema for structured table extraction.

Tables are NEVER chunked. They are stored as first-class JSON objects
and fetched by table_id when needed. This prevents the model from
hallucinating table values.

For retrieval, we create "table surrogate chunks" that are searchable
and point to the real table_id.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from .element import BoundingBox


class Table(BaseModel):
    """
    A structured table extracted from a PDF page.
    
    Tables are stored separately from text chunks to preserve
    their structure and enable exact value lookups.
    """
    
    table_id: str = Field(
        ...,
        description="Unique identifier: {doc_id}_p{page}_t{index}",
        examples=["DOC001_p5_t0", "RSMEANS_2024_p120_t2"]
    )
    doc_id: str = Field(..., description="Parent document identifier")
    page: int = Field(..., ge=1, description="1-indexed page number")
    
    # Table structure
    headers: list[str] = Field(
        ...,
        description="Column headers (first row, normalized)",
        examples=[["Asset Class", "Description", "Recovery Period", "Method"]]
    )
    rows: list[list[Any]] = Field(
        ...,
        description="Data rows (each row is a list of cell values)"
    )
    
    # Optional metadata
    caption: Optional[str] = Field(
        None,
        description="Table caption/title if present"
    )
    bbox: Optional[BoundingBox] = Field(
        None,
        description="Bounding box on page"
    )
    
    # Provenance
    element_id: Optional[str] = Field(
        None,
        description="ID of the Table element this was extracted from"
    )
    
    @property
    def num_rows(self) -> int:
        """Number of data rows (excluding header)."""
        return len(self.rows)
    
    @property
    def num_cols(self) -> int:
        """Number of columns."""
        return len(self.headers)
    
    def to_markdown(self) -> str:
        """Convert table to markdown format for display."""
        lines = []
        
        # Header
        lines.append("| " + " | ".join(str(h) for h in self.headers) + " |")
        lines.append("| " + " | ".join("---" for _ in self.headers) + " |")
        
        # Rows
        for row in self.rows:
            # Pad row if needed
            padded = list(row) + [""] * (len(self.headers) - len(row))
            lines.append("| " + " | ".join(str(cell) for cell in padded[:len(self.headers)]) + " |")
        
        return "\n".join(lines)
    
    def to_surrogate_text(self) -> str:
        """
        Generate searchable summary text for this table.
        
        This is what gets indexed in BM25/vectors.
        The actual table is fetched by table_id.
        """
        parts = []
        
        if self.caption:
            parts.append(f"Table: {self.caption}")
        
        parts.append(f"Columns: {', '.join(self.headers)}")
        
        # Include first few rows to aid search
        sample_rows = self.rows[:3]
        for row in sample_rows:
            row_text = " | ".join(str(cell) for cell in row if cell)
            if row_text:
                parts.append(row_text)
        
        if len(self.rows) > 3:
            parts.append(f"... ({len(self.rows)} total rows)")
        
        return "\n".join(parts)
    
    class Config:
        json_schema_extra = {
            "example": {
                "table_id": "IRS_PUB946_2024_p45_t0",
                "doc_id": "IRS_PUB946_2024",
                "page": 45,
                "headers": ["Asset Class", "Description", "Recovery Period", "Method"],
                "rows": [
                    ["57.0", "Distributive Trades and Services", "5", "200% DB"],
                    ["00.11", "Office Furniture, Fixtures, and Equipment", "7", "200% DB"],
                    ["00.12", "Information Systems (computers)", "5", "200% DB"]
                ],
                "caption": "Table A-1. MACRS Asset Classes",
                "element_id": "IRS_PUB946_2024_p45_e8"
            }
        }

