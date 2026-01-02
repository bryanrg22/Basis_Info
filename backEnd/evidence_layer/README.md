# Basis Evidence Layer

PDF ingestion pipeline that transforms documents into structured, retrievable artifacts for agentic workflows.

## Overview

The Evidence Layer is the foundation for Basis's agentic modules. It processes PDFs and produces:

- **Layout Elements**: Paragraphs, headings, tables with page-level provenance
- **Structured Tables**: First-class JSON objects (never chunked)
- **Text Chunks**: Overlapped narrative chunks for retrieval
- **BM25 Index**: Lexical search with IRS-aware tokenization
- **FAISS Index**: Semantic vector search

## Quick Start

### Installation

```bash
cd backEnd/evidence_layer
pip install -e .
# or
pip install -r requirements.txt
```

### Ingest a Document

```bash
# Reference corpus (IRS, RSMeans)
python -m evidence_layer.cli ingest path/to/pub946.pdf \
    --corpus reference \
    --doc-type irs \
    --version "IRS Pub 946 (2024)"

# Study corpus (per-property documents)
python -m evidence_layer.cli ingest path/to/appraisal.pdf \
    --corpus study \
    --doc-type appraisal \
    --study-id STUDY_001
```

### Search

```bash
# BM25 (exact codes, section numbers)
python -m evidence_layer.cli search "1245" --doc-id IRS_PUB946_2024 --method bm25

# Vector (semantic, paraphrases)
python -m evidence_layer.cli search "equipment depreciation" --doc-id IRS_PUB946_2024 --method vector

# Hybrid (best of both)
python -m evidence_layer.cli search "tangible personal property" --doc-id IRS_PUB946_2024
```

## Architecture

```
PDF → Parse → Elements → Tables → Chunks → Indexes
                 ↓          ↓         ↓
           layout/    structured/  retrieval/
                                       ↓
                              BM25 + FAISS indexes
```

### Two Corpora

1. **Reference Corpus** (`data/reference/`)
   - IRS publications (Pub 946, ATG, etc.)
   - RSMeans cost data
   - Shared across all studies, versioned

2. **Study Corpus** (`data/studies/{study_id}/`)
   - Appraisals, invoices, sketches
   - Private to each study
   - Prevents cross-customer data leakage

## Data Structures

### Elements (`layout/{doc_id}.elements.jsonl`)
```json
{
  "element_id": "IRS_PUB946_2024_p12_e3",
  "element_type": "paragraph",
  "text": "Section 1245 property includes...",
  "page": 12,
  "bbox": {"x0": 72, "y0": 500, "x1": 540, "y1": 550}
}
```

### Tables (`structured/{doc_id}.tables.jsonl`)
```json
{
  "table_id": "IRS_PUB946_2024_p45_t0",
  "headers": ["Asset Class", "Description", "Recovery Period"],
  "rows": [
    ["57.0", "Distributive Trades", "5"],
    ["00.11", "Office Furniture", "7"]
  ],
  "page": 45
}
```

### Chunks (`retrieval/{doc_id}.chunks.jsonl`)
```json
{
  "chunk_id": "IRS_PUB946_2024_chunk_15",
  "type": "text",
  "text": "Section 1245 property. This type of property...",
  "page_span": [12, 12],
  "element_ids": ["IRS_PUB946_2024_p12_e3"],
  "section_path": ["How To Depreciate Property", "Section 1245"]
}
```

## Retrieval API

For agentic modules, use the Python API:

```python
from evidence_layer.src.retrieval import (
    bm25_search,
    vector_search,
    hybrid_search,
    get_table,
)

# Exact code search
results = bm25_search("IRS_PUB946_2024", "1245", top_k=5)

# Semantic search
results = vector_search("IRS_PUB946_2024", "equipment depreciation", top_k=5)

# Hybrid (recommended)
results = hybrid_search("IRS_PUB946_2024", "tangible personal property", top_k=5)

# Fetch structured table (never hallucinated)
table = get_table("IRS_PUB946_2024", "IRS_PUB946_2024_p45_t0")
```

## IRS-Aware Tokenization

The BM25 index uses custom tokenization that preserves IRS codes:

| Input | Tokens |
|-------|--------|
| `§1245` | `["§1245", "1245"]` |
| `168(e)(3)` | `["168(e)(3)", "168"]` |
| `57.0` | `["57.0", "57"]` |

This ensures queries like "1245" match documents containing "§1245".

## Project Structure

```
evidence_layer/
├── src/
│   ├── schemas/          # Pydantic models (Element, Table, Chunk)
│   ├── manifest.py       # Document registration
│   ├── parse_pdf.py      # PDF → elements
│   ├── extract_tables.py # Elements → tables
│   ├── chunk_text.py     # Elements → chunks
│   ├── tokenizers.py     # IRS-aware tokenization
│   ├── build_bm25.py     # BM25 index
│   ├── build_faiss.py    # FAISS vector index
│   ├── ingest.py         # Pipeline orchestrator
│   └── retrieval.py      # Search API
├── cli/
│   └── __main__.py       # CLI commands
├── data/
│   ├── reference/        # Reference corpus artifacts
│   └── studies/          # Study corpus artifacts
└── tests/
```

## Key Design Decisions

1. **Tables are never chunked** - Stored as structured JSON, fetched by `table_id`
2. **Overlap prevents boundary loss** - 80 token overlap between chunks
3. **Provenance everywhere** - Every artifact links back to page + element
4. **Two corpora** - Prevents accidental cross-customer citations

## Next Steps

- [ ] Phase 5: Appraisal field extraction → `fields.json`
- [ ] Phase 6: Transition to production DBs (Postgres, OpenSearch, pgvector)

