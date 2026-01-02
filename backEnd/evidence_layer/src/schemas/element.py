"""
Element schema for layout-aware PDF parsing.

Elements are the atomic units extracted from PDF pages:
- Title: Document/section titles (largest font, often bold)
- Heading: Section headings
- Paragraph: Body text blocks
- ListItem: Bulleted/numbered list items
- Table: Placeholder for table locations (actual table data in Table schema)
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ElementType(str, Enum):
    """Type of layout element extracted from PDF."""
    
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"


class BoundingBox(BaseModel):
    """Bounding box coordinates (PDF units, origin at bottom-left)."""
    
    x0: float = Field(..., description="Left edge")
    y0: float = Field(..., description="Bottom edge")
    x1: float = Field(..., description="Right edge")
    y1: float = Field(..., description="Top edge")
    
    @property
    def width(self) -> float:
        return self.x1 - self.x0
    
    @property
    def height(self) -> float:
        return self.y1 - self.y0


class Element(BaseModel):
    """
    A layout element extracted from a PDF page.
    
    Provides page-level provenance for all downstream artifacts.
    """
    
    element_id: str = Field(
        ...,
        description="Unique identifier: {doc_id}_p{page}_e{index}",
        examples=["DOC001_p1_e0", "DOC001_p3_e12"]
    )
    doc_id: str = Field(..., description="Parent document identifier")
    element_type: ElementType = Field(..., description="Classification of this element")
    text: str = Field(..., description="Extracted text content")
    page: int = Field(..., ge=1, description="1-indexed page number")
    
    # Optional provenance
    bbox: Optional[BoundingBox] = Field(
        None,
        description="Bounding box on page (if available)"
    )
    confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Classification confidence (0-1)"
    )
    
    # Structural context
    section_path: Optional[list[str]] = Field(
        None,
        description="Hierarchical path of headings leading to this element",
        examples=[["Chapter 3", "Section 1245 Property", "Definition"]]
    )
    
    # Font info for classification
    font_size: Optional[float] = Field(None, description="Dominant font size in points")
    is_bold: Optional[bool] = Field(None, description="Whether text is predominantly bold")
    
    class Config:
        json_schema_extra = {
            "example": {
                "element_id": "IRS_PUB946_2024_p12_e3",
                "doc_id": "IRS_PUB946_2024",
                "element_type": "paragraph",
                "text": "Section 1245 property includes any property that is or has been property of a character subject to the allowance for depreciation...",
                "page": 12,
                "bbox": {"x0": 72, "y0": 500, "x1": 540, "y1": 550},
                "confidence": 0.95,
                "section_path": ["How To Depreciate Property", "Section 1245 Property"],
                "font_size": 10.0,
                "is_bold": False
            }
        }

