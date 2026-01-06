# Basis Evidence Layer

PDF ingestion pipeline that transforms documents into structured, retrievable artifacts for **Agentic RAG** and **Agentic Tool Use** workflows.

## Overview

The Evidence Layer is the foundation for Basis's agentic modules. It provides two key capabilities:

### 1. Agentic RAG Infrastructure

Retrieval functions that power **Agentic RAG** - where LLMs decide when, what, and how to search:

- **BM25 Index**: Lexical search with IRS-aware tokenization
- **FAISS Index**: Semantic vector search
- **Hybrid Search**: Combined BM25 + vector with score fusion
- **Table Fetching**: Structured tables (never chunked, never hallucinated)

### 2. Appraisal Extraction Tools

Extraction modules used by the **Agentic Appraisal Extraction** multi-agent system:

- **MISMO Parser**: 100% accurate XML parsing
- **Azure DI Extractor**: Form recognition with confidence scores
- **Vision Fallback**: GPT-4o for handwritten/faded fields
- **Validation**: Rule-based plausibility checks

## Artifacts Produced

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
   - Pre-ingested at deployment

2. **Study Corpus** (`data/studies/{study_id}/`)
   - Appraisals, invoices, sketches
   - Private to each study
   - Prevents cross-customer data leakage
   - **Ingested live** when user uploads documents

### Live Appraisal Ingestion

When a user uploads an appraisal PDF, the workflow automatically:

```
                         load_study
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
            resource_extraction       analyze_rooms
            (ingest appraisal PDF)    (vision analysis)
            ~30 seconds               ~2-3 minutes
```

1. Downloads PDF from Firebase Storage
2. Runs full ingestion pipeline (parse → chunk → index)
3. Extracts structured fields (GLA, bedrooms, etc.)
4. Makes document searchable via MCP tools

**Study-scoped ingestion:**
```python
ingest_document(
    pdf_path=appraisal_path,
    corpus=Corpus.STUDY,           # Study-scoped
    doc_type=DocType.APPRAISAL,
    study_id="STUDY_001",          # Required for study corpus
)
```

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

## Retrieval API (Powers Agentic RAG)

The retrieval functions are wrapped as LangChain tools in `agentic/mcp_server/tools/search_tools.py` and used by agents via **Agentic RAG**:

```
┌─────────────────────────────────────────────────────────────────┐
│  AGENTIC RAG FLOW                                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  AssetAgent receives: "Classify carpet in bedroom"              │
│         │                                                       │
│         ▼                                                       │
│  LLM thinks: "I need IRS guidance on carpet depreciation"       │
│         │                                                       │
│         ▼                                                       │
│  LLM calls: hybrid_search(                                      │
│      doc_id="IRS_IRS_COST_SEG_ATG__2024",                       │
│      query="carpet depreciation classification"                 │
│  )                                                              │
│         │                                                       │
│         ▼                                                       │
│  evidence_layer.src.retrieval.hybrid_search() executes          │
│         │                                                       │
│         ▼                                                       │
│  Results: [chunk about 1245 property, chunk about 57.0...]      │
│         │                                                       │
│         ▼                                                       │
│  LLM thinks: "I see reference to asset class 57.0, verify..."   │
│         │                                                       │
│         ▼                                                       │
│  LLM calls: bm25_search(doc_id="IRS_REV_PROC_87_56", ...)       │
│         │                                                       │
│         ▼                                                       │
│  Output: {"bucket": "5-year", "section": "1245", ...}           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Python API:**

```python
from evidence_layer.src.retrieval import (
    bm25_search,
    vector_search,
    hybrid_search,
    get_table,
)

# Exact code search (best for IRS codes like "1245", "57.0")
results = bm25_search("IRS_PUB946_2024", "1245", top_k=5)

# Semantic search (best for paraphrases, conceptual queries)
results = vector_search("IRS_PUB946_2024", "equipment depreciation", top_k=5)

# Hybrid (recommended - combines both)
results = hybrid_search("IRS_PUB946_2024", "tangible personal property", top_k=5)

# Fetch structured table (never hallucinated)
table = get_table("IRS_PUB946_2024", "IRS_PUB946_2024_p45_t0")
```

**Why Agentic RAG vs Traditional RAG?**

| Feature | Traditional RAG | Agentic RAG (Basis) |
|---------|-----------------|---------------------|
| Query formulation | Fixed template | LLM crafts optimal query |
| When to retrieve | Always, before generation | LLM decides (may skip or multi-hop) |
| Which retriever | Single method | LLM picks BM25 vs vector vs hybrid |
| Self-correction | No | LLM re-searches if results unhelpful |

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
│   ├── extract_fields.py # Regex-based field extraction
│   ├── map_appraisal_sections.py  # URAR tables → frontend sections
│   ├── chunk_text.py     # Elements → chunks
│   ├── tokenizers.py     # IRS-aware tokenization
│   ├── build_bm25.py     # BM25 index
│   ├── build_faiss.py    # FAISS vector index
│   ├── ingest.py         # Pipeline orchestrator
│   ├── retrieval.py      # Search API
│   └── tiered_extraction/        # Production extraction system
│       ├── __init__.py           # Module exports
│       ├── field_mappings.py     # URAR field definitions
│       ├── confidence.py         # FieldResult, ExtractionResult
│       ├── mismo_parser.py       # Tier 1: MISMO XML
│       ├── azure_di_extractor.py # Tier 2: Azure Document Intelligence
│       ├── vision_fallback.py    # Tier 3: GPT-4o Vision
│       ├── validation.py         # Tier 5: Cross-field validation
│       └── extractor.py          # TieredExtractor orchestrator
├── cli/
│   └── __main__.py       # CLI commands
├── data/
│   ├── reference/        # Reference corpus artifacts
│   └── studies/          # Study corpus artifacts
└── tests/
```

## URAR Section Mapping

For appraisal documents, extracted tables are parsed to produce URAR (Uniform Residential Appraisal Report) sections for frontend display.

**File:** [`map_appraisal_sections.py`](src/map_appraisal_sections.py)

```python
from evidence_layer.src.map_appraisal_sections import map_appraisal_tables_to_sections

# Parse extracted tables into frontend-ready sections
sections = map_appraisal_tables_to_sections(
    tables_path=Path("data/studies/STUDY_001/structured/appraisal.tables.jsonl"),
    fallback_fields=regex_fields,  # Optional fallback from extract_fields.py
)

# Returns:
{
    "subject": {"property_address": "123 Main St", "borrower": "John Doe", ...},
    "listing_and_contract": {"contract_price": 450000, "dom": 45, ...},
    "neighborhood": {"location": "Suburban", "growth": "Stable", ...},
    "site": {"dimensions": "75x120", "zoning": "R-1", ...},
    "improvements": {"year_built": 1985, "gla": 2450, ...},
    "sales_comparison": {"comps": [...], "adjustments": [...]},
    "cost_approach": {"site_value": 85000, "depreciation": "15%", ...},
    "reconciliation": {"indicated_value": 465000, ...},
}
```

**How it works:**

URAR tables are extracted by the ingestion pipeline, but the data is embedded in long strings within cells (not clean columns):
```
"Property Address 1290 W. 29th City Montrose State CA Zip Code 70009"
"Borrower Sam Smith Owner of Public Record Stan Utley County Montrose"
```

The mapping process:
1. **Text concatenation** - All text from table headers and rows is combined into a single string
2. **Regex extraction** - Patterns extract specific field values (e.g., `r'City\s+([A-Za-z\s]+?)\s+State'`)
3. **Section mapping** - Extracted values are organized into typed frontend sections
4. **Fallback** - If regex extraction is empty, `fallback_fields` dict provides defaults

**Example regex patterns:**
| Field | Pattern |
|-------|---------|
| contract_price | `Contract Price\s+\$?\s*([\d,]+)` |
| city | `City\s+([A-Za-z\s]+?)\s+State` |
| year_built | `Year Built\s+(\d{4})` |
| gla | `Gross Living Area.*?(\d[\d,]+)\s*(?:Square Feet\|sq)` |

**Why this approach?**
- Reuses table extraction from ingestion (no extra parsing step)
- Deterministic (no LLM calls, no API costs)
- Handles URAR's embedded-string format reliably
- Fast (~10ms to process all tables)

## Tiered Extraction System (Powers Agentic Appraisal Extraction)

The tiered extraction modules are wrapped as LangChain tools and used by the **multi-agent appraisal extraction system** in `agentic/agents/appraisal/`:

```
┌─────────────────────────────────────────────────────────────────┐
│  AGENTIC APPRAISAL EXTRACTION                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐                                            │
│  │ ExtractorAgent  │ ──► tools.py wraps:                        │
│  │ (LLM decides    │     • mismo_parser.py  (parse_mismo_xml)   │
│  │  which tool)    │     • azure_di_extractor.py (extract_azure)│
│  └────────┬────────┘     • vision_fallback.py (extract_vision)  │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────┐                                            │
│  │ VerifierAgent   │ ──► tools.py wraps:                        │
│  │ (LLM checks     │     • validation.py (validate_extraction)  │
│  │  plausibility)  │     • vision_fallback.py (vision_recheck)  │
│  └────────┬────────┘                                            │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────┐                                            │
│  │ CorrectorAgent  │ ──► Uses DIFFERENT tool than original      │
│  │ (LLM fixes      │     to re-extract flagged fields           │
│  │  errors)        │                                            │
│  └─────────────────┘                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Module:** [`tiered_extraction/`](src/tiered_extraction/)

**Direct Python API** (for testing/debugging):

```python
from evidence_layer.src.tiered_extraction import TieredExtractor

extractor = TieredExtractor()
result = await extractor.extract(
    pdf_path="path/to/appraisal.pdf",
    mismo_xml=None,  # Optional MISMO XML for 100% accuracy
    tables_path="path/to/appraisal.tables.jsonl",
    fallback_fields=regex_fields,
)

# Result includes confidence metadata
print(result.overall_confidence)  # 0.0-1.0
print(result.needs_review)        # True if critical fields < 0.90
print(result.sources_used)        # ["azure_di", "regex", ...]
```

**Agentic API** (production - recommended):

```python
from agentic.agents.appraisal import run_appraisal_extraction

result = await run_appraisal_extraction(
    study_id="STUDY_001",
    pdf_path="path/to/appraisal.pdf",
    context=stage_context,
    max_iterations=2,  # Max correction loops
)

# Result includes full audit trail for IRS defensibility
print(result["overall_confidence"])
print(result["audit_trail"]["field_history"])  # Every extraction/correction logged
```

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    TieredExtractor                              │
├─────────────────────────────────────────────────────────────────┤
│  Tier 1: MISMO XML Parser (confidence: 1.0)                    │
│     ↓ (if unavailable)                                          │
│  Tier 2: Azure Document Intelligence (confidence: 0.7-0.95)     │
│     ↓ (for fields with confidence < 0.85)                       │
│  Tier 3: GPT-4o Vision Fallback (confidence: 0.6-0.9)          │
│     ↓ (for any remaining empty fields)                          │
│  Tier 4: Regex Fallback (existing code) (confidence: 0.5-0.8)  │
│     ↓                                                           │
│  Tier 5: Validation & Confidence Aggregation                    │
└─────────────────────────────────────────────────────────────────┘
```

### Tiers Explained

| Tier | Source | Confidence | When Used |
|------|--------|------------|-----------|
| 1 | MISMO XML | 1.0 | If XML file uploaded (authoritative) |
| 2 | Azure Document Intelligence | 0.70-0.95 | Primary extraction for PDFs |
| 3 | GPT-4o Vision | 0.60-0.90 | Low-confidence or handwritten fields |
| 4 | Regex (map_appraisal_sections.py) | 0.50-0.80 | Fallback for remaining fields |
| 5 | Validation | N/A | Cross-field checks, flags for review |

### Critical Fields

These fields require confidence >= 0.90 or the result is flagged for review:

- `property_address`
- `year_built`
- `gross_living_area`
- `appraised_value`
- `contract_price`
- `effective_date`

### Environment Variables

```bash
# Azure Document Intelligence (Tier 2)
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<key>

# Azure OpenAI (Tier 3) - uses existing config
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
```

### Graceful Degradation

The system degrades gracefully if services are unavailable:
- No Azure DI credentials? → Skips to Tier 3/4
- No Azure OpenAI? → Skips to Tier 4
- All fail? → Returns regex results with `needs_review: true`

### Output Format

```python
# ExtractionResult.to_dict() returns:
{
    "subject": {"property_address": "123 Main St", ...},
    "improvements": {"year_built": 1985, "gross_living_area": 2450, ...},
    # ... other sections ...
    "_metadata": {
        "overall_confidence": 0.87,
        "needs_review": true,
        "sources_used": ["azure_di", "regex"],
        "extraction_time_ms": 2340,
        "field_confidences": {
            "subject": {
                "property_address": {"confidence": 0.95, "source": "azure_di"},
                ...
            }
        }
    }
}
```

## Key Design Decisions

1. **Tables are never chunked** - Stored as structured JSON, fetched by `table_id`
2. **Overlap prevents boundary loss** - 80 token overlap between chunks
3. **Provenance everywhere** - Every artifact links back to page + element
4. **Two corpora** - Prevents accidental cross-customer citations
5. **Tiered extraction with fallbacks** - Azure DI → GPT-4o Vision → Regex, graceful degradation
6. **Field-level confidence scoring** - Every extracted field has confidence + source tracking
7. **Critical field validation** - Automatic review flagging when key fields are uncertain

## Next Steps

- [x] Phase 5: Appraisal field extraction → `fields.json` + `map_appraisal_sections.py`
- [x] Phase 6: Tiered extraction system → Azure DI + GPT-4o Vision + confidence scoring
- [x] Phase 7: Agentic appraisal extraction → Multi-agent LangGraph with self-correction
- [x] Phase 8: Agentic RAG infrastructure → Search tools for LLM-driven retrieval
- [ ] Phase 9: Transition to production DBs (Postgres, OpenSearch, pgvector)
- [ ] Phase 10: MISMO XML upload support for 100% accurate extraction

