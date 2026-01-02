"""
Token-based text chunking with overlap.

Chunks ONLY narrative text (paragraphs, headings, list items).
Tables are handled separately as structured objects.

Overlap ensures evidence spanning chunk boundaries is captured.
"""

from pathlib import Path
from typing import Optional

import tiktoken

from .schemas.chunk import Chunk, ChunkType
from .schemas.element import Element, ElementType


# Default chunking parameters
DEFAULT_CHUNK_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 80
HARD_MAX_TOKENS = 700

# Use cl100k_base (GPT-4 tokenizer)
TOKENIZER = tiktoken.get_encoding("cl100k_base")


def chunk_with_overlap(
    elements: list[Element],
    doc_id: str,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    start_chunk_idx: int = 0,
) -> list[Chunk]:
    """
    Chunk narrative elements with token-based overlap.
    
    Args:
        elements: Parsed elements (will filter to narrative only)
        doc_id: Document identifier
        chunk_tokens: Target tokens per chunk
        overlap_tokens: Tokens to overlap between chunks
        start_chunk_idx: Starting index for chunk IDs
        
    Returns:
        List of text Chunk objects with provenance
    """
    # Filter to narrative elements only (no tables)
    narrative_elements = [
        e for e in elements
        if e.element_type != ElementType.TABLE
    ]
    
    if not narrative_elements:
        return []
    
    # Build text stream with element tracking
    text_parts: list[tuple[str, str, int]] = []  # (text, element_id, page)
    
    for elem in narrative_elements:
        if elem.text.strip():
            text_parts.append((elem.text.strip(), elem.element_id, elem.page))
    
    if not text_parts:
        return []
    
    # Tokenize all parts
    tokenized_parts: list[tuple[list[int], str, int]] = []
    for text, elem_id, page in text_parts:
        tokens = TOKENIZER.encode(text)
        tokenized_parts.append((tokens, elem_id, page))
    
    # Build chunks with overlap
    chunks: list[Chunk] = []
    chunk_idx = start_chunk_idx
    
    current_tokens: list[int] = []
    current_element_ids: list[str] = []
    current_pages: set[int] = set()
    
    part_idx = 0
    token_offset = 0  # Offset within current part
    
    while part_idx < len(tokenized_parts):
        part_tokens, elem_id, page = tokenized_parts[part_idx]
        remaining_tokens = part_tokens[token_offset:]
        
        # Add tokens to current chunk
        tokens_to_add = remaining_tokens[:chunk_tokens - len(current_tokens)]
        current_tokens.extend(tokens_to_add)
        
        if elem_id not in current_element_ids:
            current_element_ids.append(elem_id)
        current_pages.add(page)
        
        # Check if chunk is full
        if len(current_tokens) >= chunk_tokens or part_idx == len(tokenized_parts) - 1:
            # Create chunk if we have content
            if current_tokens:
                chunk_text = TOKENIZER.decode(current_tokens)
                
                chunk = Chunk(
                    chunk_id=f"{doc_id}_chunk_{chunk_idx}",
                    doc_id=doc_id,
                    chunk_type=ChunkType.TEXT,
                    text=chunk_text,
                    page_span=(min(current_pages), max(current_pages)),
                    element_ids=current_element_ids.copy(),
                    token_count=len(current_tokens),
                )
                chunks.append(chunk)
                chunk_idx += 1
                
                # Calculate overlap start
                overlap_start = max(0, len(current_tokens) - overlap_tokens)
                overlap_tokens_kept = current_tokens[overlap_start:]
                
                # Reset for next chunk, keeping overlap
                current_tokens = overlap_tokens_kept.copy()
                
                # Keep element IDs that contributed to overlap
                # (simplified: keep last element ID)
                if current_element_ids:
                    current_element_ids = [current_element_ids[-1]]
                else:
                    current_element_ids = []
                
                # Keep pages from overlap
                # (simplified: keep last page)
                if current_pages:
                    current_pages = {max(current_pages)}
                else:
                    current_pages = set()
        
        # Move to next part if we've consumed all tokens
        if token_offset + len(tokens_to_add) >= len(part_tokens):
            part_idx += 1
            token_offset = 0
        else:
            token_offset += len(tokens_to_add)
    
    # Handle any remaining tokens
    if current_tokens and len(current_tokens) > overlap_tokens:
        chunk_text = TOKENIZER.decode(current_tokens)
        
        chunk = Chunk(
            chunk_id=f"{doc_id}_chunk_{chunk_idx}",
            doc_id=doc_id,
            chunk_type=ChunkType.TEXT,
            text=chunk_text,
            page_span=(min(current_pages), max(current_pages)) if current_pages else (1, 1),
            element_ids=current_element_ids,
            token_count=len(current_tokens),
        )
        chunks.append(chunk)
    
    return chunks


def merge_chunks_with_surrogates(
    text_chunks: list[Chunk],
    table_surrogates: list[Chunk],
) -> list[Chunk]:
    """
    Merge text chunks with table surrogate chunks.
    
    Interleaves based on page order for better context.
    """
    all_chunks = text_chunks + table_surrogates
    
    # Sort by page, then by type (text before tables on same page)
    def sort_key(chunk: Chunk):
        page = chunk.page_span[0]
        type_order = 0 if chunk.chunk_type == ChunkType.TEXT else 1
        return (page, type_order, chunk.chunk_id)
    
    return sorted(all_chunks, key=sort_key)


def build_section_paths(
    elements: list[Element],
    chunks: list[Chunk],
) -> list[Chunk]:
    """
    Add section_path to chunks based on preceding headings.
    
    Tracks hierarchical heading structure and assigns
    to each chunk based on its element IDs.
    """
    # Build heading hierarchy
    heading_stack: list[tuple[ElementType, str]] = []
    element_to_section: dict[str, list[str]] = {}
    
    for elem in elements:
        if elem.element_type in (ElementType.TITLE, ElementType.HEADING):
            # Titles reset the stack
            if elem.element_type == ElementType.TITLE:
                heading_stack = [(elem.element_type, elem.text)]
            else:
                # Pop headings at same or lower level (simplified)
                heading_stack.append((elem.element_type, elem.text))
                # Keep last 3 levels max
                if len(heading_stack) > 3:
                    heading_stack = heading_stack[-3:]
        
        # Record section path for this element
        element_to_section[elem.element_id] = [h[1] for h in heading_stack]
    
    # Apply section paths to chunks
    updated_chunks = []
    for chunk in chunks:
        section_path = None
        for elem_id in chunk.element_ids:
            if elem_id in element_to_section:
                section_path = element_to_section[elem_id]
                break
        
        updated_chunk = chunk.model_copy(update={"section_path": section_path})
        updated_chunks.append(updated_chunk)
    
    return updated_chunks


def save_chunks(chunks: list[Chunk], output_path: Path) -> None:
    """Save chunks to JSONL file."""
    import jsonlines
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with jsonlines.open(output_path, mode="w") as writer:
        for chunk in chunks:
            writer.write(chunk.model_dump(mode="json"))


def load_chunks(input_path: Path) -> list[Chunk]:
    """Load chunks from JSONL file."""
    import jsonlines
    
    chunks = []
    with jsonlines.open(input_path) as reader:
        for obj in reader:
            chunks.append(Chunk.model_validate(obj))
    return chunks

