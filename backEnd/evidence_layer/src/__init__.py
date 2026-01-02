"""
Evidence Layer source modules.

Pipeline:
    ingest.py       - Main orchestrator
    manifest.py     - Document registration
    parse_pdf.py    - PDF → elements
    extract_tables.py - Elements → tables + surrogates
    chunk_text.py   - Elements → overlapped chunks
    tokenizers.py   - IRS-aware tokenization
    build_bm25.py   - Chunks → BM25 index
    build_faiss.py  - Chunks → FAISS index
    retrieval.py    - Search functions
"""

