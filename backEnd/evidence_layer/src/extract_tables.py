"""
Table extraction from PDFs.

Extracts tables as first-class structured objects.
Tables are NEVER chunked - they are stored as JSON and
fetched by table_id when needed.

Also generates "table surrogate chunks" for indexing.
"""

from pathlib import Path
from typing import Optional

import pdfplumber

from .schemas.chunk import Chunk, ChunkType
from .schemas.element import BoundingBox, Element, ElementType
from .schemas.table import Table


def extract_tables(
    pdf_path: Path,
    doc_id: str,
    elements: Optional[list[Element]] = None,
) -> list[Table]:
    """
    Extract tables from a PDF as structured objects.
    
    Args:
        pdf_path: Path to PDF file
        doc_id: Document identifier
        elements: Optional pre-parsed elements (to link table_id to element_id)
        
    Returns:
        List of Table objects with headers and rows
    """
    pdf_path = Path(pdf_path)
    tables: list[Table] = []
    
    # Build element lookup if provided
    table_elements: dict[tuple[int, int], Element] = {}
    if elements:
        for elem in elements:
            if elem.element_type == ElementType.TABLE:
                # Key by (page, approximate y position)
                if elem.bbox:
                    key = (elem.page, int(elem.bbox.y0 // 50))
                    table_elements[key] = elem
    
    with pdfplumber.open(pdf_path) as pdf:
        table_idx = 0
        
        for page_num, page in enumerate(pdf.pages, start=1):
            page_tables = page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                }
            )
            
            if not page_tables:
                # Try text-based extraction as fallback
                page_tables = page.extract_tables(
                    table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                    }
                )
            
            # Get table bboxes for matching
            found_tables = page.find_tables()
            
            for t_idx, raw_table in enumerate(page_tables):
                if not raw_table or len(raw_table) < 2:
                    continue
                
                # First row is headers
                headers = _normalize_headers(raw_table[0])
                if not any(headers):  # Skip if no valid headers
                    continue
                
                # Remaining rows are data
                rows = []
                for row in raw_table[1:]:
                    cleaned_row = [_clean_cell(cell) for cell in row]
                    if any(cleaned_row):  # Skip empty rows
                        rows.append(cleaned_row)
                
                if not rows:
                    continue
                
                # Get bbox if available
                bbox = None
                if t_idx < len(found_tables):
                    tb = found_tables[t_idx].bbox
                    bbox = BoundingBox(x0=tb[0], y0=tb[1], x1=tb[2], y1=tb[3])
                
                # Try to match with element
                element_id = None
                if bbox:
                    key = (page_num, int(bbox.y0 // 50))
                    if key in table_elements:
                        element_id = table_elements[key].element_id
                
                # Detect caption (text just above table)
                caption = _detect_caption(page, bbox) if bbox else None
                
                table = Table(
                    table_id=f"{doc_id}_p{page_num}_t{table_idx}",
                    doc_id=doc_id,
                    page=page_num,
                    headers=headers,
                    rows=rows,
                    caption=caption,
                    bbox=bbox,
                    element_id=element_id,
                )
                tables.append(table)
                table_idx += 1
    
    return tables


def _normalize_headers(header_row: list) -> list[str]:
    """Normalize table headers."""
    headers = []
    for i, cell in enumerate(header_row):
        if cell is None or str(cell).strip() == "":
            headers.append(f"Column_{i + 1}")
        else:
            # Clean and normalize
            header = str(cell).strip()
            header = " ".join(header.split())  # Normalize whitespace
            headers.append(header)
    return headers


def _clean_cell(cell) -> str:
    """Clean a table cell value."""
    if cell is None:
        return ""
    text = str(cell).strip()
    text = " ".join(text.split())  # Normalize whitespace
    return text


def _detect_caption(page: pdfplumber.page.Page, table_bbox: BoundingBox) -> Optional[str]:
    """
    Try to detect a table caption just above the table.

    Common patterns:
    - "Table X: Description"
    - "Table X. Description"
    - "TABLE X - Description"
    """
    import re

    # Look for text in the region just above the table
    # Clamp bbox to page boundaries to avoid out-of-bounds errors
    caption_region = (
        max(0, table_bbox.x0 - 20),
        max(0, table_bbox.y0 - 40),  # 40 points above
        min(page.width, table_bbox.x1 + 20),
        min(page.height, table_bbox.y0),
    )

    # Validate region is valid (x0 < x1 and y0 < y1)
    if caption_region[0] >= caption_region[2] or caption_region[1] >= caption_region[3]:
        return None

    text = page.within_bbox(caption_region).extract_text()
    if not text:
        return None
    
    text = text.strip()
    
    # Check for table caption patterns
    caption_patterns = [
        r"^Table\s+[\dA-Za-z\-\.]+[:\.]\s*(.+)$",
        r"^TABLE\s+[\dA-Za-z\-\.]+[:\.]\s*(.+)$",
        r"^Table\s+[\dA-Za-z\-\.]+\s*[-–—]\s*(.+)$",
    ]
    
    for pattern in caption_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return text
    
    return None


def make_table_surrogates(
    tables: list[Table],
    doc_id: str,
    start_chunk_idx: int = 0,
) -> list[Chunk]:
    """
    Create searchable surrogate chunks for tables.
    
    These chunks are what BM25/vector indexes see.
    When a surrogate is hit, we fetch the real table by table_id.
    
    Args:
        tables: List of Table objects
        doc_id: Document identifier
        start_chunk_idx: Starting index for chunk IDs
        
    Returns:
        List of Chunk objects (type=table_summary)
    """
    chunks: list[Chunk] = []
    
    for i, table in enumerate(tables):
        chunk_id = f"{doc_id}_chunk_{start_chunk_idx + i}"
        
        # Generate searchable text
        surrogate_text = table.to_surrogate_text()
        
        chunk = Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            chunk_type=ChunkType.TABLE_SUMMARY,
            text=surrogate_text,
            page_span=(table.page, table.page),
            element_ids=[table.element_id] if table.element_id else [],
            table_id=table.table_id,
            section_path=None,  # Could be extracted from nearby headings
        )
        chunks.append(chunk)
    
    return chunks


def save_tables(tables: list[Table], output_path: Path) -> None:
    """Save tables to JSONL file."""
    import jsonlines
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with jsonlines.open(output_path, mode="w") as writer:
        for table in tables:
            writer.write(table.model_dump(mode="json"))


def load_tables(input_path: Path) -> list[Table]:
    """Load tables from JSONL file."""
    import jsonlines
    
    tables = []
    with jsonlines.open(input_path) as reader:
        for obj in reader:
            tables.append(Table.model_validate(obj))
    return tables

