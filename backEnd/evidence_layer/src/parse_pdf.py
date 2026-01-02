"""
PDF parsing to layout-aware elements.

Uses pdfplumber for text extraction with positional info.
Classifies blocks as Title/Heading/Paragraph/ListItem/Table
based on font size, position, and structural patterns.
"""

import re
from pathlib import Path
from typing import Optional

import pdfplumber

from .schemas.element import BoundingBox, Element, ElementType


def _extract_text_with_chars(page: pdfplumber.page.Page) -> list[dict]:
    """
    Extract text grouped by lines with character-level info.
    
    Returns list of text blocks with position and font info.
    """
    chars = page.chars
    if not chars:
        return []
    
    # Group characters into words, then lines
    words = page.extract_words(
        keep_blank_chars=False,
        x_tolerance=3,
        y_tolerance=3,
        extra_attrs=["fontname", "size"]
    )
    
    if not words:
        return []
    
    # Group words into lines by y-position
    lines: list[list[dict]] = []
    current_line: list[dict] = []
    current_top: Optional[float] = None
    y_tolerance = 5  # pixels
    
    # Sort words by position (top to bottom, left to right)
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    
    for word in sorted_words:
        if current_top is None:
            current_top = word["top"]
            current_line = [word]
        elif abs(word["top"] - current_top) <= y_tolerance:
            current_line.append(word)
        else:
            if current_line:
                lines.append(current_line)
            current_line = [word]
            current_top = word["top"]
    
    if current_line:
        lines.append(current_line)
    
    # Convert lines to text blocks
    blocks = []
    for line_words in lines:
        if not line_words:
            continue
        
        text = " ".join(w["text"] for w in line_words)
        
        # Get dominant font size
        sizes = [w.get("size", 10) for w in line_words if w.get("size")]
        avg_size = sum(sizes) / len(sizes) if sizes else 10.0
        
        # Check if bold (heuristic: fontname contains "Bold")
        fontnames = [w.get("fontname", "") for w in line_words]
        is_bold = any("bold" in fn.lower() for fn in fontnames if fn)
        
        blocks.append({
            "text": text.strip(),
            "x0": min(w["x0"] for w in line_words),
            "y0": min(w["top"] for w in line_words),
            "x1": max(w["x1"] for w in line_words),
            "y1": max(w["bottom"] for w in line_words),
            "font_size": avg_size,
            "is_bold": is_bold,
        })
    
    return blocks


def _merge_blocks_to_paragraphs(blocks: list[dict], page_height: float) -> list[dict]:
    """
    Merge adjacent lines into paragraphs based on spacing.
    
    Lines that are close together vertically and have similar
    formatting are merged into single paragraph blocks.
    """
    if not blocks:
        return []
    
    merged = []
    current_para: Optional[dict] = None
    
    for block in blocks:
        if current_para is None:
            current_para = block.copy()
            continue
        
        # Check if this block should be merged with current paragraph
        vertical_gap = block["y0"] - current_para["y1"]
        same_size = abs(block["font_size"] - current_para["font_size"]) < 1.5
        similar_indent = abs(block["x0"] - current_para["x0"]) < 50
        
        # Merge if close vertically and similar formatting
        if vertical_gap < 15 and same_size and similar_indent:
            current_para["text"] += " " + block["text"]
            current_para["y1"] = block["y1"]
            current_para["x1"] = max(current_para["x1"], block["x1"])
        else:
            merged.append(current_para)
            current_para = block.copy()
    
    if current_para:
        merged.append(current_para)
    
    return merged


def _classify_element(
    block: dict,
    page_blocks: list[dict],
    page_width: float,
) -> ElementType:
    """
    Classify a text block as Title/Heading/Paragraph/ListItem.
    
    Heuristics:
    - Title: Large font (>14pt), near top of page, bold
    - Heading: Medium-large font (>12pt), bold, short
    - ListItem: Starts with bullet/number, or indented
    - Paragraph: Everything else
    """
    text = block["text"]
    font_size = block.get("font_size", 10)
    is_bold = block.get("is_bold", False)
    x0 = block.get("x0", 0)
    
    # Empty or very short
    if len(text.strip()) < 3:
        return ElementType.PARAGRAPH
    
    # List item patterns
    list_patterns = [
        r"^[\u2022\u2023\u25E6\u2043\u2219]\s",  # Bullets: •, ‣, ◦, ⁃, ∙
        r"^[-–—]\s",                              # Dashes
        r"^\d{1,3}[\.\)]\s",                      # Numbered: 1. or 1)
        r"^[a-zA-Z][\.\)]\s",                     # Lettered: a. or a)
        r"^\([a-zA-Z0-9]+\)\s",                   # Parenthetical: (a) or (1)
    ]
    for pattern in list_patterns:
        if re.match(pattern, text):
            return ElementType.LIST_ITEM
    
    # Check indentation (list items are often indented)
    avg_x0 = sum(b.get("x0", 0) for b in page_blocks) / len(page_blocks) if page_blocks else 0
    is_indented = x0 > avg_x0 + 30
    
    if is_indented and len(text) < 200:
        return ElementType.LIST_ITEM
    
    # Title: Large font, near top, bold
    if font_size >= 14 and is_bold:
        return ElementType.TITLE
    
    # Heading: Bold, medium-large font, relatively short
    if is_bold and font_size >= 11 and len(text) < 150:
        return ElementType.HEADING
    
    # Section-like patterns (even without bold detection)
    heading_patterns = [
        r"^(Chapter|Section|Part|Article)\s+\d+",
        r"^[A-Z][A-Z\s]{3,50}$",  # ALL CAPS SHORT
        r"^\d+\.\d+\.?\s+[A-Z]",   # Numbered sections: 1.2 Title
    ]
    for pattern in heading_patterns:
        if re.match(pattern, text):
            return ElementType.HEADING
    
    return ElementType.PARAGRAPH


def parse_pdf_to_elements(
    pdf_path: Path,
    doc_id: str,
) -> list[Element]:
    """
    Parse a PDF into layout-aware elements.
    
    Args:
        pdf_path: Path to PDF file
        doc_id: Document identifier for element IDs
        
    Returns:
        List of Element objects with page-level provenance
    """
    pdf_path = Path(pdf_path)
    elements: list[Element] = []
    element_idx = 0
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_width = page.width
            page_height = page.height
            
            # Check for tables first
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables] if tables else []
            
            # Extract text blocks
            raw_blocks = _extract_text_with_chars(page)
            
            # Filter out blocks that overlap with tables
            text_blocks = []
            for block in raw_blocks:
                block_bbox = (block["x0"], block["y0"], block["x1"], block["y1"])
                overlaps_table = any(
                    _bboxes_overlap(block_bbox, table_bbox)
                    for table_bbox in table_bboxes
                )
                if not overlaps_table:
                    text_blocks.append(block)
            
            # Merge into paragraphs
            paragraphs = _merge_blocks_to_paragraphs(text_blocks, page_height)
            
            # Add table placeholders
            for table_idx, table in enumerate(tables):
                bbox = table.bbox
                elements.append(Element(
                    element_id=f"{doc_id}_p{page_num}_e{element_idx}",
                    doc_id=doc_id,
                    element_type=ElementType.TABLE,
                    text=f"[TABLE {table_idx + 1}]",
                    page=page_num,
                    bbox=BoundingBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
                ))
                element_idx += 1
            
            # Classify and add text elements
            for para in paragraphs:
                if not para["text"].strip():
                    continue
                
                element_type = _classify_element(para, paragraphs, page_width)
                
                elements.append(Element(
                    element_id=f"{doc_id}_p{page_num}_e{element_idx}",
                    doc_id=doc_id,
                    element_type=element_type,
                    text=para["text"],
                    page=page_num,
                    bbox=BoundingBox(
                        x0=para["x0"],
                        y0=para["y0"],
                        x1=para["x1"],
                        y1=para["y1"],
                    ),
                    font_size=para.get("font_size"),
                    is_bold=para.get("is_bold"),
                ))
                element_idx += 1
    
    return elements


def _bboxes_overlap(bbox1: tuple, bbox2: tuple, margin: float = 5) -> bool:
    """Check if two bounding boxes overlap (with margin)."""
    x0_1, y0_1, x1_1, y1_1 = bbox1
    x0_2, y0_2, x1_2, y1_2 = bbox2
    
    return not (
        x1_1 + margin < x0_2 or
        x0_1 - margin > x1_2 or
        y1_1 + margin < y0_2 or
        y0_1 - margin > y1_2
    )


def save_elements(elements: list[Element], output_path: Path) -> None:
    """Save elements to JSONL file."""
    import jsonlines
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with jsonlines.open(output_path, mode="w") as writer:
        for element in elements:
            writer.write(element.model_dump(mode="json"))


def load_elements(input_path: Path) -> list[Element]:
    """Load elements from JSONL file."""
    import jsonlines
    
    elements = []
    with jsonlines.open(input_path) as reader:
        for obj in reader:
            elements.append(Element.model_validate(obj))
    return elements

