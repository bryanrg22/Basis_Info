"""
Main ingestion orchestrator.

Chains all pipeline steps:
1. Register document (manifest)
2. Parse PDF → elements
3. Extract tables → structured JSON
4. Chunk text → retrieval units
5. Build BM25 index
6. Build FAISS index
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .build_bm25 import build_bm25_index, save_bm25_index
from .build_faiss import build_vector_index, save_vector_index
from .chunk_text import (
    build_section_paths,
    chunk_with_overlap,
    merge_chunks_with_surrogates,
    save_chunks,
)
from .extract_tables import extract_tables, make_table_surrogates, save_tables
from .manifest import (
    Corpus,
    DocType,
    DocumentManifest,
    get_data_dir,
    load_manifest,
    register_document,
)
from .parse_pdf import parse_pdf_to_elements, save_elements


class IngestResult:
    """Result of ingestion pipeline."""
    
    def __init__(
        self,
        doc_id: str,
        manifest: DocumentManifest,
        num_elements: int,
        num_tables: int,
        num_chunks: int,
        data_dir: Path,
    ):
        self.doc_id = doc_id
        self.manifest = manifest
        self.num_elements = num_elements
        self.num_tables = num_tables
        self.num_chunks = num_chunks
        self.data_dir = data_dir
    
    def __repr__(self) -> str:
        return (
            f"IngestResult(doc_id={self.doc_id!r}, "
            f"elements={self.num_elements}, "
            f"tables={self.num_tables}, "
            f"chunks={self.num_chunks})"
        )


def ingest_document(
    pdf_path: Path,
    corpus: Corpus,
    doc_type: DocType,
    study_id: Optional[str] = None,
    version_label: Optional[str] = None,
    chunk_tokens: int = 400,
    overlap_tokens: int = 80,
    skip_vectors: bool = False,
    verbose: bool = True,
) -> IngestResult:
    """
    Run the full ingestion pipeline on a PDF.
    
    Args:
        pdf_path: Path to PDF file
        corpus: reference or study
        doc_type: Document type (irs, rsmeans, appraisal, etc.)
        study_id: Required for study corpus
        version_label: Optional version for reference corpus
        chunk_tokens: Target tokens per chunk
        overlap_tokens: Overlap tokens between chunks
        skip_vectors: Skip FAISS index (faster for testing)
        verbose: Print progress
        
    Returns:
        IngestResult with statistics
    """
    pdf_path = Path(pdf_path)
    
    def log(msg: str):
        if verbose:
            print(f"  → {msg}")
    
    # Step 1: Register document
    if verbose:
        print(f"\n📄 Ingesting: {pdf_path.name}")
    
    log("Registering document...")
    manifest = register_document(
        pdf_path=pdf_path,
        corpus=corpus,
        doc_type=doc_type,
        study_id=study_id,
        version_label=version_label,
    )
    doc_id = manifest.doc_id
    data_dir = get_data_dir(corpus, study_id)
    
    log(f"Registered as: {doc_id}")
    
    # Step 2: Parse PDF → elements
    log("Parsing PDF...")
    elements = parse_pdf_to_elements(pdf_path, doc_id)
    
    elements_path = data_dir / "layout" / f"{doc_id}.elements.jsonl"
    save_elements(elements, elements_path)
    log(f"Extracted {len(elements)} elements")
    
    # Step 3: Extract tables
    log("Extracting tables...")
    tables = extract_tables(pdf_path, doc_id, elements)
    
    tables_path = data_dir / "structured" / f"{doc_id}.tables.jsonl"
    save_tables(tables, tables_path)
    log(f"Extracted {len(tables)} tables")
    
    # Step 4: Chunk text
    log("Chunking text...")
    text_chunks = chunk_with_overlap(
        elements=elements,
        doc_id=doc_id,
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
    )
    
    # Create table surrogates
    table_surrogates = make_table_surrogates(
        tables=tables,
        doc_id=doc_id,
        start_chunk_idx=len(text_chunks),
    )
    
    # Merge and add section paths
    all_chunks = merge_chunks_with_surrogates(text_chunks, table_surrogates)
    all_chunks = build_section_paths(elements, all_chunks)
    
    chunks_path = data_dir / "retrieval" / f"{doc_id}.chunks.jsonl"
    save_chunks(all_chunks, chunks_path)
    log(f"Created {len(all_chunks)} chunks ({len(text_chunks)} text, {len(table_surrogates)} table surrogates)")
    
    # Step 5: Build BM25 index
    log("Building BM25 index...")
    bm25_index = build_bm25_index(
        chunks=all_chunks,
        doc_id=doc_id,
        doc_type=doc_type.value,
    )
    
    indexes_dir = data_dir / "indexes"
    bm25_path = save_bm25_index(bm25_index, indexes_dir, doc_id)
    log(f"BM25 index saved: {bm25_path.name}")
    
    # Step 6: Build FAISS index (optional)
    if not skip_vectors:
        log("Building vector index...")
        vector_index = build_vector_index(
            chunks=all_chunks,
            doc_id=doc_id,
            show_progress=verbose,
        )
        
        vector_paths = save_vector_index(vector_index, indexes_dir, doc_id)
        log(f"Vector index saved: {vector_paths[0].name}")
    else:
        log("Skipping vector index (--skip-vectors)")
    
    # Update manifest
    manifest.is_processed = True
    manifest.processed_at = datetime.now(timezone.utc)
    
    # Re-save manifest with updated status
    import json
    manifest_path = data_dir / "manifest.json"
    with open(manifest_path) as f:
        manifests = json.load(f)
    
    manifests = [m for m in manifests if m["doc_id"] != doc_id]
    manifests.append(manifest.model_dump(mode="json"))
    
    with open(manifest_path, "w") as f:
        json.dump(manifests, f, indent=2, default=str)
    
    if verbose:
        print(f"✅ Ingestion complete: {doc_id}")
    
    return IngestResult(
        doc_id=doc_id,
        manifest=manifest,
        num_elements=len(elements),
        num_tables=len(tables),
        num_chunks=len(all_chunks),
        data_dir=data_dir,
    )


def ingest_directory(
    input_dir: Path,
    corpus: Corpus,
    doc_type: DocType,
    study_id: Optional[str] = None,
    version_label: Optional[str] = None,
    **kwargs,
) -> list[IngestResult]:
    """
    Ingest all PDFs in a directory.
    
    Args:
        input_dir: Directory containing PDFs
        corpus: reference or study
        doc_type: Document type
        study_id: Study ID (for study corpus)
        version_label: Version label (for reference corpus)
        **kwargs: Additional args passed to ingest_document
        
    Returns:
        List of IngestResult objects
    """
    input_dir = Path(input_dir)
    pdf_files = list(input_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        return []
    
    print(f"Found {len(pdf_files)} PDF files")
    
    results = []
    for pdf_path in pdf_files:
        try:
            result = ingest_document(
                pdf_path=pdf_path,
                corpus=corpus,
                doc_type=doc_type,
                study_id=study_id,
                version_label=version_label,
                **kwargs,
            )
            results.append(result)
        except Exception as e:
            print(f"❌ Error ingesting {pdf_path.name}: {e}")
    
    print(f"\n📊 Ingested {len(results)}/{len(pdf_files)} documents")
    return results

