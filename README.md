# Basis – Document Intelligence with Agentic RAG

<img width="997" height="336" alt="Basis Logo" src="https://github.com/user-attachments/assets/aaab8d9c-7238-46d0-a8ea-29ea04a666e5" />

---

> **Cost segregation shouldn't take weeks. Basis gets engineers 80% of the way there—fast, guided, and defensible.**

---

## Table of Contents

* [What is Basis?](#what-is-basis)
* [Why Cost Seg?](#why-cost-seg)
* [The Problem](#the-problem)
* [The Solution](#the-solution)
* [Demo Video](#demo-video)
* [Current Project Overview](#current-project-overview)
* [The Problem: Document Intelligence at Scale](#the-problem-document-intelligence-at-scale)
* [The Solution: Hybrid RAG + Agentic Workflow](#the-solution-hybrid-rag--agentic-workflow)
* [Architecture Deep Dive](#architecture-deep-dive)
  * [Offline Pipeline — PDF Ingestion](#1-offline-pipeline--pdf-ingestion)
  * [Evidence Layer — Hybrid RAG](#2-evidence-layer-hybrid-rag)
  * [Agentic Workflow — LangGraph Orchestration](#3-agentic-workflow--langgraph-orchestration)
  * [Vision Layer — Detection-First Image Processing](#4-vision-layer--detection-first-image-processing)
  * [Tool Registry](#5-tool-registry)
* [Tech Stack](#tech-stack)
* [Engineer-in-the-Loop Workflow](#engineer-in-the-loop-workflow)
* [User Workflow (High Level)](#user-workflow-high-level)
* [Current Application: Cost Segregation](#current-application-cost-segregation)
* [Traction & Validation](#traction--validation)
* [NVIDIA Applicability: Automotive Functional Safety Project](#nvidia-applicability-automotive-functional-safety-project)
* [Accuracy, Safety & Defensibility](#accuracy-safety--defensibility)
* [Data Handling](#data-handling)
* [Why Not Just Use ChatGPT?](#why-not-just-use-chatgpt)
* [Getting Started (Dev)](#getting-started-dev)
* [About](#about)

---

## What is Basis?

**Basis** is an AI-assisted platform for **residential-focused cost segregation firms** that accelerates the most time-consuming part of the study:

> **analyzing hundreds of photos, sketches, and appraisal documents to produce an IRS-ready report.**

Basis is not a "one-click study generator." It's a **human-in-the-loop, agentic workflow** powered by three core systems:

1. **Vision Layer** — Detection-first image processing that reduces VLM hallucinations through grounded detection
2. **Evidence Layer** — PDF ingestion pipeline with hybrid BM25 + vector retrieval for IRS-grounded reasoning
3. **Agentic Workflow** — LangGraph-orchestrated multi-agent system with stage-gated engineer review checkpoints

This architecture **walks the engineer through every decision before anything becomes client-facing.**

---

## Why Cost Seg?

**$1M** That's what you might spend to buy a house. That upfront spend can create **tax savings** as the property depreciates over **27.5 years**.

But 27.5 years is a long time to wait.

**Cost segregation** helps owners **accelerate depreciation** and unlock meaningful savings earlier. In the U.S., there are **5,000+** businesses conducting thousands of studies per year—which makes the workflow opportunity massive.

---

## The Problem

A cost segregation study typically follows three steps:

1. **Document the property**
2. **Analyze the documentation**
3. **Generate the report**

The bottleneck is step 2.

Our interviews revealed that this analysis phase:

* Requires engineers to comb through **hundreds of photos, drawings, and appraisals**
* Can take **2–3 weeks** to complete
* Can cost **>$1,200** in labor per study
* Can leave **>$1,000** in savings on the table due to missed or inconsistently documented components

---

## The Solution

**Enter Basis.**

Engineers upload the property artifacts they already use today. Basis:

* **Organizes documents and imagery**
* **Classifies rooms, materials, and objects**
* **Guides engineers through review checkpoints**
* **Surfaces the exact references** needed for takeoffs and tax classification
  (so engineers aren't hunting across hundreds of pages)

**Result:** faster studies, fewer errors, lower cost to serve.

---

## Demo Video

A short walkthrough showing how Basis guides engineers through appraisal constraints, room/object classification, takeoffs, and IRS-grounded asset decisions.

[![Basis Demo Video](https://img.youtube.com/vi/ZpUEYUvN5II/hqdefault.jpg)](https://youtu.be/ZpUEYUvN5II)

---

## Current Project Overview

* **Objective:**
  Reduce cost seg analysis time by automating repetitive classification and retrieval tasks while preserving engineer-led accuracy and auditability.

* **Core Features:**

  * **Study creation + structured upload**
  * **Appraisal-to-constraints extraction**
  * **Room classification with scene + object context**
  * **Object/component detection with metadata enrichment**
  * **Engineer review checkpoints at every stage**
  * **Engineering takeoffs assistance**
  * **Asset classification with IRS-grounded RAG**
  * **Cost classification hooks for integrated cost databases**
  * **Export-ready outputs for existing firm templates**

---

## The Problem: Document Intelligence at Scale

Many industries require AI-assisted workflows for querying large document sets—regulatory publications, technical standards, safety baselines—that share a common challenge:

> **Standardized headers, messy context.**

These documents contain critical structured data (IDs, codes, classifications, tables) embedded in unstructured narrative text. Traditional approaches fail because:

- **Pure keyword search** misses semantic relationships
- **Pure vector search** hallucinates on exact codes and IDs
- **Context windows** can't hold hundreds of pages
- **LLM-only approaches** lack auditability and traceability

---

## The Solution: Hybrid RAG + Agentic Workflow

Basis implements a **three-layer architecture** designed for document intelligence problems:

```
┌─────────────────────────────────────────────────────────────────┐
│                     AGENTIC LAYER (LangGraph)                   │
│  • Multi-agent orchestration with stage-gated checkpoints       │
│  • Tool routing based on query intent                           │
│  • "No evidence, no claim" enforcement                          │
│  • Human-in-the-loop verification at every stage                │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                     EVIDENCE LAYER (Hybrid RAG)                 │
│  • BM25 for exact-term matches (codes, IDs, classifications)    │
│  • FAISS vector search for semantic similarity                  │
│  • Score fusion + deduplication                                 │
│  • Tables stored intact (never chunked)                         │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                     OFFLINE PIPELINE                            │
│  • Layout-aware PDF parsing (pdfplumber)                        │
│  • Table extraction → structured JSON                           │
│  • Semantic chunking with 80-token overlap                      │
│  • Dual indexing (BM25 + FAISS)                                 │
└─────────────────────────────────────────────────────────────────┘
```

This architecture is **domain-agnostic**. The current implementation targets cost segregation (IRS tax documents), but the same pipeline handles any document corpus with structured codes and unstructured context.

---

## Architecture Deep Dive

### 1) Offline Pipeline — PDF Ingestion

**Location:** `backEnd/evidence_layer/`

Transforms raw PDFs into retrieval-ready indexes through a 5-stage pipeline:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PDF INGESTION PIPELINE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────┐ │
│  │  STAGE 1 │───►│  STAGE 2 │───►│  STAGE 3 │───►│  STAGE 4 │───►│STAGE 5 │ │
│  │  Parse   │    │  Extract │    │  Chunk   │    │  Build   │    │ Build  │ │
│  │  Layout  │    │  Tables  │    │  Text    │    │  BM25    │    │ FAISS  │ │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └────────┘ │
│       │               │               │               │              │      │
│       ▼               ▼               ▼               ▼              ▼      │
│   layout/        structured/      retrieval/      indexes/       indexes/   │
│   elements.json  tables.json      chunks.json     bm25.pkl       faiss.idx  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

#### Stage 1: Layout-Aware PDF Parsing

**File:** [`parse_pdf.py`](backEnd/evidence_layer/src/parse_pdf.py)

Extracts text with positional metadata using pdfplumber + PyMuPDF.

```
Raw PDF
   │
   ▼
┌─────────────────────────────────────┐
│  For each page:                     │
│  • Extract text with bbox coords    │
│  • Detect font size + boldness      │
│  • Classify element type            │
│  • Preserve reading order           │
└─────────────────────────────────────┘
   │
   ▼
Layout Elements (with position + type)
```

**Element Classification:**

| Type | Detection Method | Example |
|------|------------------|---------|
| `title` | Large font + bold | "Chapter 4: MACRS" |
| `heading` | Medium font + bold | "Section 1245 Property" |
| `paragraph` | Regular text blocks | Narrative content |
| `list_item` | Numbered/bulleted | "1. Tangible property..." |
| `table` | Grid structure detected | *Routed to Stage 2* |

**Output:** `layout/elements.json` — every text block with page, bbox, font, type.

---

#### Stage 2: Table Extraction (Tables Stay Intact)

**File:** [`extract_tables.py`](backEnd/evidence_layer/src/extract_tables.py)

**Critical design decision:** Tables are NEVER chunked. They're stored as structured JSON and fetched whole.

```
Layout Elements
   │
   ├── Table detected? ──YES──► Extract as structured JSON
   │                            Store in structured/tables.json
   │                            Create surrogate chunk for search
   │
   └── Not a table ──────────► Pass to Stage 3
```

**Why tables stay intact:**
- Chunking tables destroys row/column relationships
- LLMs hallucinate when given partial table data
- Agents fetch full table by `table_id` when surrogate matches

**Table Storage Format:**

```json
{
  "table_id": "DOC_2024_table_3",
  "page": 15,
  "caption": "Table B-1. Asset Classes",
  "headers": ["Asset Class", "Description", "Recovery Period"],
  "rows": [
    ["57.0", "Distributive Trades", "5 years"],
    ["00.11", "Office Furniture", "7 years"]
  ],
  "markdown": "| Asset Class | Description | Recovery Period |\n|---|---|---|\n| 57.0 | ..."
}
```

**Surrogate Chunk (for search):**
```json
{
  "chunk_id": "DOC_2024_table_3_surrogate",
  "type": "table_surrogate",
  "text": "Table B-1. Asset Classes: 57.0 Distributive Trades 5 years, 00.11 Office Furniture 7 years...",
  "table_id": "DOC_2024_table_3"
}
```

When search hits the surrogate → agent calls `get_table(table_id)` → returns full structured table.

**URAR Appraisal Mapping:**

For appraisal documents, extracted tables are additionally mapped to URAR (Uniform Residential Appraisal Report) sections:

```
┌─────────────────────────────────────────────────────────────────┐
│              APPRAISAL TABLE → SECTION MAPPING                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Extracted Tables (.tables.jsonl)                                │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────┐                         │
│  │  map_appraisal_sections.py          │                         │
│  │                                     │                         │
│  │  1. Identify section by keywords    │                         │
│  │     + page position (URAR layout)   │                         │
│  │                                     │                         │
│  │  2. Map table rows → section fields │                         │
│  │     (subject, neighborhood, etc.)   │                         │
│  │                                     │                         │
│  │  3. Fallback to regex extraction    │                         │
│  │     for missing values              │                         │
│  └─────────────────────────────────────┘                         │
│         │                                                        │
│         ▼                                                        │
│  Frontend-ready sections:                                        │
│  • subject (address, borrower, lender)                           │
│  • listing_and_contract (price, DOM, sale type)                  │
│  • neighborhood (location, growth, values)                       │
│  • site (dimensions, zoning, utilities)                          │
│  • improvements (foundation, rooms, year built)                  │
│  • sales_comparison (comps grid)                                 │
│  • cost_approach (site value, depreciation)                      │
│  • reconciliation (final value opinion)                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

This mapping uses the same high-quality table extraction from ingestion—no additional parsing or GPT calls required.

---

#### Stage 3: Semantic Chunking with Overlap

**File:** [`chunk_text.py`](backEnd/evidence_layer/src/chunk_text.py)

Splits narrative text into retrieval units with semantic overlap.

```
Non-Table Elements
   │
   ▼
┌─────────────────────────────────────┐
│  Chunking Parameters:               │
│  • Target: 400 tokens               │
│  • Overlap: 80 tokens               │
│  • Hard max: 700 tokens             │
│  • Tokenizer: cl100k_base (GPT-4)   │
└─────────────────────────────────────┘
   │
   ▼
Chunks with provenance metadata
```

**Why 80-token overlap?**

```
Without overlap:
┌──────────────────┐ ┌──────────────────┐
│ Chunk 1          │ │ Chunk 2          │
│ "...property     │ │ includes assets  │
│ under Section"   │ │ classified as... │
└──────────────────┘ └──────────────────┘
        ▲                    ▲
        └── Boundary loss ───┘
            "Section 1245 property includes..."
            is split and context is lost

With 80-token overlap:
┌──────────────────────────┐
│ Chunk 1                  │
│ "...property under       │
│ Section 1245 includes    │◄── Overlap
│ assets classified..."    │
└──────────────────────────┘
              ┌──────────────────────────┐
              │ Chunk 2                  │
     Overlap ►│ "Section 1245 includes   │
              │ assets classified as     │
              │ tangible personal..."    │
              └──────────────────────────┘

Both chunks contain the full context.
```

**Chunk Output:**

```json
{
  "chunk_id": "DOC_2024_chunk_15",
  "type": "text",
  "text": "Section 1245 property includes tangible personal property...",
  "page_span": [12, 12],
  "element_ids": ["DOC_2024_p12_e3", "DOC_2024_p12_e4"],
  "section_path": ["How To Depreciate Property", "Section 1245"],
  "token_count": 387
}
```

---

#### Stage 4: BM25 Index (Lexical Search)

**File:** [`build_bm25.py`](backEnd/evidence_layer/src/build_bm25.py)

Builds lexical index with **custom tokenization** for exact code matching.

```
Chunks
   │
   ▼
┌─────────────────────────────────────┐
│  Custom Tokenizer (not whitespace!) │
│                                     │
│  "§1245 property"                   │
│       ▼                             │
│  ["§1245", "1245", "property"]      │
└─────────────────────────────────────┘
   │
   ▼
BM25Okapi Index (bm25.pkl)
```

**Why custom tokenization matters:**

Standard tokenizers break regulatory codes:

| Standard Tokenizer | Custom Tokenizer |
|--------------------|------------------|
| `["§", "1245"]` ❌ | `["§1245", "1245"]` ✓ |
| `["168", "(", "e", ")"]` ❌ | `["168(e)(3)", "168"]` ✓ |
| `["57", ".", "0"]` ❌ | `["57.0", "57"]` ✓ |

**Tokenizer patterns** ([`tokenizers.py`](backEnd/evidence_layer/src/tokenizers.py)):

| Pattern | Example | Tokens Generated |
|---------|---------|------------------|
| Section symbols | `§1245` | `["§1245", "1245"]` |
| Parenthetical refs | `168(e)(3)(B)` | `["168(e)(3)(b)", "168"]` |
| Decimal codes | `57.0`, `00.11` | `["57.0", "57"]` |
| Mixed references | `Section 179(d)` | `["section", "179(d)", "179"]` |

```python
>>> irs_tokenize("§1245 property depreciation")
['§1245', '1245', 'property', 'depreciation']

>>> irs_tokenize("Asset class 57.0 under Section 168(e)(3)")
['asset', 'class', '57.0', '57', 'section', '168(e)(3)', '168']
```

This ensures queries for `"1245"` match documents containing `"§1245"` or `"Section 1245"`. The same pattern applies to any domain with structured identifiers (hazard IDs, ASIL levels, requirement codes).

---

#### Stage 5: FAISS Index (Semantic Search)

**File:** [`build_faiss.py`](backEnd/evidence_layer/src/build_faiss.py)

Builds vector index for semantic similarity.

```
Chunks
   │
   ▼
┌─────────────────────────────────────┐
│  Sentence Transformer               │
│  Model: all-MiniLM-L6-v2            │
│  Dimensions: 384                    │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│  FAISS Index                        │
│  • L2 distance metric               │
│  • Metadata mapping (chunk_id)      │
└─────────────────────────────────────┘
   │
   ▼
faiss.idx + metadata.json
```

**When to use semantic search:**
- Conceptual queries: "What qualifies for accelerated depreciation?"
- Paraphrased questions: "equipment that wears out quickly"
- Related concepts: "tangible personal property" → finds "Section 1245"

---

#### Pipeline Output Summary

After ingestion, each document produces:

```
data/{corpus}/{doc_id}/
├── layout/
│   └── elements.json      # Raw parsed elements with position
├── structured/
│   └── tables.json        # Complete tables (never chunked)
├── retrieval/
│   └── chunks.json        # Text chunks with overlap + provenance
└── indexes/
    ├── bm25/
    │   └── index.pkl      # Lexical search index
    └── vector/
        ├── faiss.idx      # Semantic search index
        └── metadata.json  # Chunk ID mapping
```

---

### 2) Evidence Layer (Hybrid RAG)

**Location:** `backEnd/evidence_layer/src/retrieval.py`

Combines lexical and semantic search with score normalization.

**Retrieval Flow:**

```
Query
  │
  ├──► BM25 Search ──► Normalized Scores ──┐
  │    (exact codes)                       │
  │                                        ├──► Score Fusion ──► Deduplicate ──► Results
  │                                        │
  └──► Vector Search ─► Normalized Scores ─┘
       (semantic)
```

**API:**

```python
# BM25 for exact codes/IDs
results = bm25_search("IRS_PUB946_2024", "1245", top_k=5)

# Vector for semantic queries
results = vector_search("IRS_PUB946_2024", "equipment depreciation", top_k=5)

# Hybrid (recommended) - configurable BM25 weight
results = hybrid_search("IRS_PUB946_2024", "tangible personal property", top_k=5, bm25_weight=0.5)
```

**Key Features:**
- Automatic score normalization before fusion
- Deduplication of overlapping results
- Table expansion: when surrogate chunks match, full table returned
- Supports both "reference" corpus (shared docs) and "study" corpus (per-case docs)

---

### 3) Agentic Workflow — LangGraph Orchestration

**Location:** `backEnd/agentic/`

The agentic layer solves a critical problem: **context window saturation**.

When documents are large or interrelated, naive RAG retrieves too much context, saturating the LLM's context window and degrading response quality. The solution is **agent-based selective retrieval**—the agent plans what evidence is needed, retrieves selectively, and verifies sufficiency before generating.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      AGENTIC RAG vs NAIVE RAG                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  NAIVE RAG:                          AGENTIC RAG:                           │
│  ┌─────────┐                         ┌─────────┐                            │
│  │  Query  │                         │  Query  │                            │
│  └────┬────┘                         └────┬────┘                            │
│       │                                   │                                 │
│       ▼                                   ▼                                 │
│  ┌─────────┐                         ┌─────────────┐                        │
│  │ Retrieve│                         │ Agent Plans │◄── "What evidence      │
│  │ top-k   │                         │ what to get │    do I need?"         │
│  └────┬────┘                         └──────┬──────┘                        │
│       │                                     │                               │
│       │ (may retrieve                       ▼                               │
│       │  too much or                 ┌─────────────┐                        │
│       │  wrong docs)                 │ Tool Router │◄── BM25 vs Vector      │
│       │                              │             │    vs Structured       │
│       ▼                              └──────┬──────┘                        │
│  ┌─────────┐                                │                               │
│  │ Generate│                                ▼                               │
│  │ (hope   │                         ┌─────────────┐                        │
│  │ it fits)│                         │ Selective   │◄── Only what's needed  │
│  └─────────┘                         │ Retrieval   │                        │
│                                      └──────┬──────┘                        │
│       ❌ Context                            │                               │
│          saturation                         ▼                               │
│                                      ┌─────────────┐                        │
│                                      │ Verify      │◄── "Is this enough?"   │
│                                      │ Sufficiency │    If not, retrieve    │
│                                      └──────┬──────┘    more                │
│                                             │                               │
│                                             ▼                               │
│                                      ┌─────────────┐                        │
│                                      │ Generate    │                        │
│                                      │ with        │                        │
│                                      │ citations   │                        │
│                                      └─────────────┘                        │
│                                                                             │
│                                        ✓ Selective retrieval                │
│                                        ✓ Fits context window                │
│                                        ✓ Grounded in evidence               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

#### Why Agentic? (The Context Saturation Problem)

**Mosi's observation:** Safety documents have standardized headers but messy context sections. When you retrieve naively, you pull in entire documents or too many chunks, saturating the context window.

**The agentic solution:**

1. **Agent plans first** — Before retrieving, the agent analyzes the query and decides what evidence is needed
2. **Tool routing** — Agent chooses the right retrieval method (BM25 for exact IDs, vector for concepts, structured for tables)
3. **Selective retrieval** — Only pulls what's necessary, not top-k everything
4. **Verification loop** — Checks if evidence is sufficient; if not, retrieves more targeted chunks
5. **Grounded generation** — Only claims what the evidence supports

---

#### Workflow State Machine (Simplified 3-Pause Architecture)

The workflow has been optimized to have exactly **3 engineer checkpoints** matching the frontend UI:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SIMPLIFIED WORKFLOW (3 PAUSE POINTS)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  load_study                                                                 │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      analyze_rooms_node                             │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │ 1. Vision Analysis (PARALLEL - 10 concurrent)               │    │    │
│  │  │    All images analyzed simultaneously with GPT-4o Vision    │    │    │
│  │  └─────────────────────────────────────────────────────────────┘    │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │ 2. Room Enrichment (PARALLEL - 10 concurrent)               │    │    │
│  │  │    All rooms enriched with IRS context simultaneously       │    │    │
│  │  └─────────────────────────────────────────────────────────────┘    │    │
│  └────────────────────────────────┬────────────────────────────────────┘    │
│                                   │                                         │
│                                   ▼                                         │
│  ╔═════════════════════════════════════════════════════════════════════╗    │
│  ║  ⏸️ PAUSE #1: resource_extraction                                   ║    │
│  ║  Engineer reviews: Appraisal data + detected rooms                  ║    │
│  ╚═════════════════════════════════════════════════════════════════════╝    │
│                                   │ (engineer approves)                     │
│                                   ▼                                         │
│  ╔═════════════════════════════════════════════════════════════════════╗    │
│  ║  ⏸️ PAUSE #2: reviewing_rooms                                       ║    │
│  ║  Engineer reviews: Room classifications + IRS context               ║    │
│  ╚═════════════════════════════════════════════════════════════════════╝    │
│                                   │ (engineer approves)                     │
│                                   ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      process_assets_node                            │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │ 1. Object Enrichment (PARALLEL - 20 concurrent)             │    │    │
│  │  │    All objects enriched with IRS context simultaneously     │    │    │
│  │  └─────────────────────────────────────────────────────────────┘    │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │ 2. Takeoffs + Classification (CROSS-PHASE PARALLEL!)        │    │    │
│  │  │    ┌────────────────────┐  ┌────────────────────┐           │    │    │
│  │  │    │ Takeoff Calc (×10) │  │ IRS Classify (×20) │           │    │    │
│  │  │    │ RSMeans lookup     │  │ Asset classes      │           │    │    │
│  │  │    └────────────────────┘  └────────────────────┘           │    │    │
│  │  │    Both run simultaneously via asyncio.gather()             │    │    │
│  │  └─────────────────────────────────────────────────────────────┘    │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │ 3. Cost Estimation (PARALLEL - 10 concurrent)               │    │    │
│  │  │    All costs estimated simultaneously with RSMeans          │    │    │
│  │  └─────────────────────────────────────────────────────────────┘    │    │
│  └────────────────────────────────┬────────────────────────────────────┘    │
│                                   │                                         │
│                                   ▼                                         │
│  ╔═════════════════════════════════════════════════════════════════════╗    │
│  ║  ⏸️ PAUSE #3: engineering_takeoff                                   ║    │
│  ║  Engineer reviews: Objects, takeoffs, classifications, costs        ║    │
│  ║  (Tabbed UI showing all asset data with citations)                  ║    │
│  ╚═════════════════════════════════════════════════════════════════════╝    │
│                                   │ (engineer approves)                     │
│                                   ▼                                         │
│                          ┌─────────────────┐                                │
│                          │   completed     │                                │
│                          └─────────────────┘                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Frontend WorkflowStatus values:**
```
uploading_documents → analyzing_rooms → resource_extraction → reviewing_rooms → engineering_takeoff → completed
```

**Key Design:** Only 3 engineer checkpoints (not 5-6), matching the frontend UI. The `process_assets_node` combines objects, takeoffs, classification, and costs into a single processing phase with no pauses between—engineers review all asset data together on one page.

---

#### Agent Architecture

Each agent follows a **plan → retrieve → verify → generate** pattern:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AGENT EXECUTION FLOW                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Input: Component to classify (e.g., "hardwood flooring in living room")    │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ STEP 1: PLAN                                                         │   │
│  │                                                                      │   │
│  │ Agent thinks: "I need to find:                                       │   │
│  │   1. IRS classification for flooring                                 │   │
│  │   2. Whether hardwood is personal or real property                   │   │
│  │   3. Applicable recovery period"                                     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ STEP 2: TOOL ROUTING                                                 │   │
│  │                                                                      │   │
│  │ Agent decides:                                                       │   │
│  │   • "flooring" → vector_search (semantic concept)                    │   │
│  │   • "1245 vs 1250" → bm25_search (exact IRS sections)                │   │
│  │   • "recovery period table" → get_table (structured data)            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ STEP 3: SELECTIVE RETRIEVAL                                          │   │
│  │                                                                      │   │
│  │ Agent calls tools:                                                   │   │
│  │   → hybrid_search("flooring depreciation residential")               │   │
│  │   → bm25_search("1245")                                              │   │
│  │   → get_table("MACRS_recovery_periods")                              │   │
│  │                                                                      │   │
│  │ Returns: 3 relevant chunks + 1 table (not 50 chunks)                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ STEP 4: VERIFY SUFFICIENCY                                           │   │
│  │                                                                      │   │
│  │ Agent checks: "Do I have enough evidence to classify?"               │   │
│  │   • If YES → proceed to generation                                   │   │
│  │   • If NO → retrieve more specific chunks                            │   │
│  │   • If AMBIGUOUS → flag needs_review=true                            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ STEP 5: GROUNDED GENERATION                                          │   │
│  │                                                                      │   │
│  │ Agent generates classification WITH citations:                       │   │
│  │   "Hardwood flooring is Section 1245 property (5-year recovery)      │   │
│  │    per IRS Pub 946, page 42, because..."                             │   │
│  │                                                                      │   │
│  │ "No evidence, no claim" — won't classify without source              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

#### Specialized Agents

| Agent | Purpose | Tools Used | Evidence Source |
|-------|---------|------------|-----------------|
| **Room Agent** | Enriches vision outputs with space context | `hybrid_search` | Domain guidelines |
| **Asset Agent** | MACRS classification with IRS citations | `bm25_search`, `vector_search`, `get_table` | IRS Pub 946, ATG |
| **Takeoff Agent** | Measurement extraction with confidence | `hybrid_search`, `get_chunk` | Property appraisals |
| **Cost Agent** | RSMeans cost code mapping | `hybrid_search`, `get_table` | RSMeans databases |

---

#### "No Evidence, No Claim" Enforcement

The Asset Agent system prompt explicitly requires evidence before classification:

```
CRITICAL INSTRUCTION:
You MUST search for evidence before making any classification.
- Call hybrid_search() or bm25_search() BEFORE outputting a classification
- Every classification MUST include citation_refs with chunk_ids
- If you cannot find supporting documentation, output:
    needs_review: true
    reason: "insufficient_evidence"
- NEVER guess or rely on training data—only cite retrieved documents
```

---

#### Agent Output Schema

Every agent produces structured output with provenance:

```json
{
  "asset_classification": {
    "bucket": "5-year",
    "life_years": 5,
    "section": "1245",
    "asset_class": "57.0",
    "macrs_system": "GDS",
    "irs_note": "Carpeting in residential rental property is Section 1245 property..."
  },
  "citations": [
    {"chunk_id": "IRS_PUB946_2024_chunk_42", "page": 15, "text": "Section 1245 property includes..."},
    {"chunk_id": "IRS_ATG_2024_chunk_88", "page": 34, "text": "Floor coverings are typically..."}
  ],
  "confidence": 0.92,
  "needs_review": false,
  "reasoning": "Found explicit IRS guidance classifying floor coverings as 1245 property..."
}
```

---

#### Checkpointing & Observability

**Persistent State:**
- Production: `FirestoreCheckpointer` — workflow state survives server restarts
- Development: `MemorySaver` — in-memory for fast iteration
- Thread-based resumption for long-running workflows

**LangSmith Integration:**

Every agent execution is traced in LangSmith:
- Tool calls with inputs/outputs
- LLM prompts and completions
- Latency and token usage
- Error tracking

```
┌─────────────────────────────────────────────────────────────────┐
│                    LANGSMITH TRACE                              │
├─────────────────────────────────────────────────────────────────┤
│ Asset Agent Run                                                 │
│ ├── hybrid_search("flooring depreciation") → 3 chunks          │
│ ├── bm25_search("1245") → 2 chunks                             │
│ ├── get_table("MACRS_periods") → 1 table                       │
│ ├── LLM: classify with evidence                                │
│ └── Output: { bucket: "5-year", citations: [...] }             │
│                                                                 │
│ Total tokens: 2,847  |  Latency: 3.2s  |  Status: Success      │
└─────────────────────────────────────────────────────────────────┘
```

**LangSmith Dashboard:**

![LangSmith Trace View](https://github.com/user-attachments/assets/75340053-603c-4ad2-9c40-c28c62ad703e)

---

### 4) Vision Layer — Detection-First Image Processing

**Location:** `backEnd/vision_layer/`

The vision layer processes property images using a **detection-first** approach that reduces VLM hallucinations.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                      VISION PIPELINE — DETECTION FIRST                       │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐ │
│  │  STAGE 1 │───►│  STAGE 2 │───►│  STAGE 3 │───►│  STAGE 4 │───►│STAGE 5  │ │
│  │  Detect  │    │  Segment │    │   Crop   │    │ Classify │    │ Verify  │ │
│  │  Objects │    │  Regions │    │  Regions │    │   VLM    │    │Grounding│ │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └─────────┘ │
│       │               │               │               │              │       │
│       ▼               ▼               ▼               ▼              ▼       │
│  Grounding       SAM 2           Cropped         Material      Validated     │
│  DINO 1.5        Masks           Images          Attrs         Artifacts     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

#### Why Detection-First? (Reducing VLM Hallucinations)

**The Problem:** VLMs (Vision Language Models) hallucinate when given full images. They "see" objects that aren't there or misclassify materials.

**The Solution:** Detect objects first, then classify only the cropped regions.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                VLM-ONLY vs DETECTION-FIRST                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  VLM-ONLY (hallucination-prone):     DETECTION-FIRST (grounded):            │
│                                                                             │
│  ┌─────────────────┐                 ┌─────────────────┐                    │
│  │  Full Image     │                 │  Full Image     │                    │
│  │  ┌───┐ ┌───┐    │                 │  ┌───┐ ┌───┐    │                    │
│  │  │   │ │   │    │                 │  │ A │ │ B │    │◄── Detect objects  │
│  │  └───┘ └───┘    │                 │  └───┘ └───┘    │    with bboxes     │
│  └────────┬────────┘                 └────────┬────────┘                    │
│           │                                   │                             │
│           ▼                                   ▼                             │
│  ┌─────────────────┐                 ┌─────────────────┐                    │
│  │ "I see a marble │                 │  Crop region A  │                    │
│  │  countertop,    │                 │  ┌───────────┐  │                    │
│  │  granite floor, │◄── May be       │  │  [A only] │  │◄── Send crop       │
│  │  stainless steel│    wrong!       │  └───────────┘  │    to VLM          │
│  │  appliances..." │                 └────────┬────────┘                    │
│  └─────────────────┘                          │                             │
│                                               ▼                             │
│                                      ┌─────────────────┐                    │
│                                      │ VLM classifies  │                    │
│                                      │ ONLY the crop:  │                    │
│                                      │ "wood_veneer,   │◄── Focused         │
│                                      │  built_in,      │    classification  │
│                                      │  good_condition"│                    │
│                                      └────────┬────────┘                    │
│                                               │                             │
│                                               ▼                             │
│                                      ┌─────────────────┐                    │
│                                      │ Verify: Does    │                    │
│                                      │ VLM output match│◄── Grounding       │
│                                      │ detection label?│    verification    │
│                                      └─────────────────┘                    │
│                                                                             │
│       ❌ Hallucinates objects              ✓ Grounded in detections         │
│       ❌ No provenance                     ✓ Full audit trail               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

#### Stage 1: Object Detection (Grounding DINO 1.5 Pro)

**File:** [`api_clients/grounding_dino.py`](backEnd/vision_layer/src/api_clients/grounding_dino.py)

Open-vocabulary object detection via Replicate API.

```
Property Image
      │
      ▼
┌─────────────────────────────────────┐
│  Grounding DINO 1.5 Pro             │
│                                     │
│  Prompt: "cabinet, countertop,      │
│           flooring, appliance,      │
│           lighting fixture..."      │
│                                     │
│  Confidence threshold: 0.3          │
└─────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  Detections:                        │
│  [                                  │
│    { label: "cabinet",              │
│      bbox: [100, 200, 400, 500],    │
│      confidence: 0.92 },            │
│    { label: "countertop",           │
│      bbox: [150, 50, 600, 200],     │
│      confidence: 0.87 },            │
│    ...                              │
│  ]                                  │
└─────────────────────────────────────┘
```

**Key Features:**
- Open-vocabulary: detects any object described in prompt
- Returns bounding boxes with confidence scores
- Retry logic with exponential backoff

---

#### Stage 2: Segmentation (SAM 2)

**File:** [`api_clients/sam2.py`](backEnd/vision_layer/src/api_clients/sam2.py)

Precise segmentation masks for detected regions.

```
Detection bboxes
      │
      ▼
┌─────────────────────────────────────┐
│  SAM 2 (Segment Anything Model 2)   │
│                                     │
│  Input: bbox coordinates            │
│  Output: Precise polygon mask       │
└─────────────────────────────────────┘
      │
      ▼
Refined masks with exact boundaries
```

**Purpose:** Refines rough bounding boxes into precise object boundaries. Optional stage—can be skipped for speed.

---

#### Stage 3: Region Cropping

**File:** [`pipeline/cropper.py`](backEnd/vision_layer/src/pipeline/cropper.py)

Extracts and pads regions for VLM classification.

```
Detection + Mask
      │
      ▼
┌─────────────────────────────────────┐
│  Region Cropper                     │
│                                     │
│  • Crop around bbox                 │
│  • Add 20% padding for context      │
│  • Save crop for audit trail        │
└─────────────────────────────────────┘
      │
      ▼
Cropped image (just the object + context)
```

**Why crop?**
- VLM focuses on single object, not entire scene
- Reduces hallucination from other objects in image
- Smaller image = faster inference

---

#### Stage 4: VLM Classification (GPT-4o Vision)

**File:** [`api_clients/vlm.py`](backEnd/vision_layer/src/api_clients/vlm.py)

Material and attribute classification on cropped regions.

```
Cropped Image
      │
      ▼
┌─────────────────────────────────────┐
│  GPT-4o Vision                      │
│                                     │
│  Prompt: "Classify this object:     │
│    - material (wood, metal, etc.)   │
│    - condition (good/fair/poor)     │
│    - attachment (built-in/portable) │
│    - dimensions if visible"         │
│                                     │
│  Output: Structured JSON            │
└─────────────────────────────────────┘
      │
      ▼
{
  "material": "wood_veneer",
  "condition": "good",
  "attachment_type": "built_in",
  "color": "natural_oak",
  "estimated_dimensions": "36in x 24in"
}
```

**LLM Provider:**
- **Azure OpenAI** (primary - enterprise deployment)
  - **GPT-4.1**: Best results for complex reasoning and classification
  - **GPT-4.1 nano**: Most efficient for high-volume tasks
  - This combo provides optimal cost/performance ratio

---

#### Stage 5: Grounding Verification

Cross-reference VLM claims against detection labels.

```
VLM Output + Detection Label
      │
      ▼
┌─────────────────────────────────────┐
│  Grounding Verifier                 │
│                                     │
│  Detection label: "cabinet"         │
│  VLM classification: "wood_veneer   │
│                       cabinet"      │
│                                     │
│  Match? ✓ YES                       │
│  → verified: true                   │
│                                     │
│  If mismatch:                       │
│  → needs_review: true               │
│  → reason: "grounding_mismatch"     │
└─────────────────────────────────────┘
```

**Purpose:** Catches VLM hallucinations where it classifies an object as something the detector didn't see.

---

#### Vision Artifact Output

Every processed object produces a complete artifact with provenance:

```json
{
  "artifact_id": "va_abc123",
  "image_id": "photo_456",
  "detection": {
    "label": "cabinet",
    "confidence": 0.92,
    "bbox": {"x1": 100, "y1": 200, "x2": 400, "y2": 500},
    "model": "grounding_dino_1.5_pro"
  },
  "segmentation": {
    "mask_path": "masks/va_abc123.png",
    "model": "sam2"
  },
  "crop": {
    "crop_path": "crops/va_abc123.jpg",
    "padding": 0.2
  },
  "classification": {
    "material": "wood_veneer",
    "condition": "good",
    "attachment_type": "built_in",
    "cost_seg_relevant": true,
    "model": "gpt-4.1"
  },
  "provenance": {
    "detection_model": "grounding_dino_1.5_pro",
    "segmentation_model": "sam2",
    "vlm_model": "gpt-4.1",
    "verified": true,
    "grounding_match": true
  },
  "confidence": 0.89,
  "needs_review": false
}
```

---

#### Batch Processing

**File:** [`pipeline/ingest.py`](backEnd/vision_layer/src/pipeline/ingest.py)

Concurrent processing with configurable parallelism:

```python
class VisionPipeline:
    async def process_batch(
        self,
        images: List[str],
        max_concurrent: int = 5
    ) -> List[VisionArtifact]:
        """
        Process multiple images concurrently.
        Uses semaphore to limit parallel API calls.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = [self._process_single(img, semaphore) for img in images]
        return await asyncio.gather(*tasks)
```

---

### 5) Tool Registry

Agents access evidence through standardized MCP tools:

**Retrieval Tools:**

| Tool | Purpose |
|------|---------|
| `bm25_search` | Exact token matching (codes, IDs, standard references) |
| `vector_search` | Semantic similarity for conceptual queries |
| `hybrid_search` | Combined search with score fusion |
| `get_chunk` | Fetch chunk by ID with full provenance |
| `get_table` | Fetch structured table (never chunked) |

**Tool Implementation (example):**

```python
@tool
def hybrid_search(
    doc_id: str,
    query: str,
    top_k: int = 5,
    bm25_weight: float = 0.5
) -> List[SearchResult]:
    """
    Combined BM25 + vector search with score normalization.
    Returns chunks with provenance (page_span, section_path, element_ids).
    """
    bm25_results = bm25_search(doc_id, query, top_k * 2)
    vector_results = vector_search(doc_id, query, top_k * 2)

    # Normalize and fuse scores
    fused = fuse_scores(bm25_results, vector_results, bm25_weight)

    # Deduplicate and expand tables
    return dedupe_and_expand(fused, top_k)
```

---

## Tech Stack

### Backend

| Component | Technology |
|-----------|------------|
| **Framework** | FastAPI |
| **Workflow Orchestration** | LangGraph 0.2+ |
| **LLM** | OpenAI GPT-4o (Azure OpenAI supported) |
| **PDF Parsing** | pdfplumber, PyMuPDF |
| **Vector Store** | FAISS |
| **Lexical Search** | rank-bm25 |
| **Embeddings** | sentence-transformers (`all-MiniLM-L6-v2`) |
| **Observability** | LangSmith (tracing) |
| **Database** | Firebase Firestore |
| **Storage** | Firebase Storage, GCS |

### Frontend

| Component | Technology |
|-----------|------------|
| **Framework** | Next.js 14 (App Router) |
| **Language** | TypeScript |
| **Styling** | TailwindCSS |
| **Auth/DB** | Firebase |

### Infrastructure

- **Hosting:** Firebase App Hosting, Google Cloud Run
- **Containers:** Docker
- **State Persistence:** Firestore checkpointer for workflow state

---

## Engineer-in-the-Loop Workflow

Every module follows the same contract:

1. **Frontend triggers module** with `{ studyId }`
2. **Backend fetches** the required data from Firestore/Storage
3. **Backend runs AI/ML**
4. **Backend writes results** back to Firestore
5. **Frontend renders results**
6. **Engineer reviews + corrects**
7. **Engineer manually advances** to the next stage

This is the core design principle that keeps deliverables defensible.

---

## User Workflow (High Level)

1. 📝 **Create New Study**

   * Engineer enters property name
   * Selects files to upload (photos, PDFs, appraisals)
   * Clicks **Start Analysis**

2. ⬆️ **Upload Documents**

   * Files upload to Firebase Storage
   * Progress tracked in UI

3. 📄 **Appraisal Processing**

   * Ingest PDF using same pipeline as IRS docs (parse → chunk → index)
   * Extract tables with structure preserved (headers, rows, page)
   * Map URAR tables to frontend sections (subject, neighborhood, site, improvements, etc.)
   * Create property constraints (GLA, bedrooms, room counts, etc.)
   * ⏸️ **Engineer reviews + corrects**

4. 🏠 **Room Classification**

   * Scene + material + object context
   * Groups photos into predicted rooms
   * ⏸️ **Engineer reviews + corrects**

5. 🔍 **Object Classification**

   * Detects components from photos
   * Enriches with room context + metadata
   * ⏸️ **Engineer reviews + corrects**

6. 📐 **Engineering Takeoffs**

   * Calculates measurements
   * ⏸️ **Engineer reviews + corrects**

7. 💰 **Asset Classification**

   * IRS-grounded classification
   * ⏸️ **Engineer reviews + corrects**

8. 🧾 **Cost Classification**

   * Maps components to integrated cost databases
   * ⏸️ **Engineer reviews + corrects**

9. ✅ **Complete Study**

   * Export package generated for firm templates

---

## Current Application: Cost Segregation

The architecture is currently deployed for **cost segregation**—accelerating tax depreciation analysis for commercial real estate.

**Domain-Specific Implementation:**

- **Reference Corpus:** IRS Pub 946, Pub 527, Cost Seg ATG, Rev Proc 87-56, RSMeans databases
- **Exact-Match Queries:** Asset class codes (e.g., `"57.0"`), IRS sections (e.g., `"§1245"`)
- **Semantic Queries:** "What property qualifies for 5-year depreciation?"
- **Traceability:** Every classification cites specific IRS publication pages
- **Vision Processing:** Detection-first pipeline for property photos (see [Vision Layer](#4-vision-layer--detection-first-image-processing))

---

## Traction & Validation

This isn't a proof-of-concept—it's a deployed product with paying customers.

**Customers:**
- **CSSI** (top-5 cost segregation firm) — paying user
- **CBIZ** — paying user
- Design partners at multiple top-5 firms have validated **50%+ time savings** on analysis workflows

**Awards:**

### LavaLab Fall 2025 — Best Traction

![Basis Team Holding Check](https://github.com/user-attachments/assets/a48693f1-f7cb-4832-a8ca-f7ed817b2f7f)

---

## NVIDIA Applicability: Automotive Functional Safety Project

The Basis architecture directly addresses the document intelligence challenges in ISO 26262 workflows.

**The Problem:**

Functional safety teams work with large document sets—HARA baselines, safety goals, TSRs, verification evidence—that share a common structure:
- **Standardized headers** (hazard IDs, ASIL classifications, requirement codes)
- **Messy context sections** (rationale, assumptions, linked evidence)
- **Strict traceability requirements** (every claim must cite source documents)

Querying these documents with traditional RAG fails: vector search hallucinates on exact IDs, keyword search misses semantic relationships, and LLMs can't process hundreds of pages in context.

**Architecture Mapping:**

| Basis Component | Functional Safety Application |
|-----------------|-------------------------------|
| **Custom BM25 Tokenization** | Preserve `HAZ-001`, `TSR-042`, `ASIL-D`, `ISO 26262-6:2018 §7.4.3` as atomic tokens |
| **Tables Never Chunked** | FMEA tables, DFA matrices, traceability matrices stay intact |
| **80-Token Overlap** | Safety goal rationale spanning paragraphs isn't split |
| **Hybrid Search** | Exact ID lookup + semantic "what evidence supports this safety goal?" |
| **Surrogate → Full Table** | Search hits "FMEA row for HAZ-001" → returns complete FMEA with all columns |
| **Citation Enforcement** | "No evidence, no claim" — every classification cites specific document + page |
| **Human-in-the-Loop** | Engineer reviews before any safety decision is finalized |

**Example Queries This Architecture Handles:**

```
Exact ID lookup (BM25):
  "TSR-042" → finds all chunks referencing TSR-042

Semantic search (FAISS):
  "verification evidence for braking system hazards" → finds related test reports

Hybrid (recommended):
  "ASIL-D requirements for sensor fusion" → exact ASIL match + semantic relevance

Table fetch:
  Search hits FMEA surrogate → get_table() returns full FMEA with hazard, severity, exposure, controllability
```

**Tokenizer Adaptation:**

The custom tokenizer pattern extends directly to safety document codes:

| IRS Pattern | Safety Pattern | Tokenizer Handles |
|-------------|----------------|-------------------|
| `§1245` | `HAZ-001` | Prefix + number preserved |
| `168(e)(3)` | `ISO 26262-6:2018 §7.4.3` | Nested references preserved |
| `57.0` | `ASIL-D` | Alphanumeric codes preserved |
| `Rev Proc 87-56` | `TSR-042-REV-A` | Multi-part identifiers preserved |

**What Would Change for Safety Documents:**

1. **Tokenizer regex** — add patterns for `HAZ-\d+`, `TSR-\d+`, `ASIL-[A-D]`, ISO clause refs
2. **Reference corpus** — ingest ISO 26262 parts, internal HARA baselines, verification templates
3. **Agent prompts** — swap IRS classification logic for safety goal verification logic
4. **Structured store** — FMEA tables, DFA matrices instead of depreciation tables

The pipeline, retrieval, and agentic architecture remain identical.

---

## Accuracy, Safety & Defensibility

Basis is designed for **engineering-grade output**, not generic AI chat.

We ensure accuracy through:

* **Detection-first vision processing** — Grounding DINO detects objects before VLM classifies, reducing hallucinations
* **Evidence-backed reasoning** — Every agent output cites documents with chunk IDs and page numbers
* **Grounding verification** — VLM claims are cross-referenced against detections using IoU thresholds
* **Human-in-the-loop checkpoints** — Engineers review and approve at every workflow stage
* **Confidence scoring + needs_review flags** — Uncertain outputs are flagged for engineer attention
* **Full provenance tracking** — Every artifact traces back to source image, detection, and model
* **"No evidence, no claim" enforcement** — Agents cannot classify without citing retrieved evidence

---

## Data Handling

* Customer artifacts are stored encrypted in **Firebase Storage**.
* Study data is stored in **Firestore** with role-based access.
* Vision pipelines can be isolated for sensitive drawings and photos.
* Use Enterprise API's for LLMs to prevent data being stored for training.

---

## Why Not Just Use ChatGPT?

Cost segregation is not a single "upload a PDF" problem.

Engineers often work with **hundreds of photos and mixed documents** per study, with strict IRS expectations for classification and auditability.

Basis is a **three-layer agentic system** that:

* **Detects before classifying** — Grounding DINO + SAM 2 detect objects before GPT-4o classifies, eliminating VLM hallucinations
* **Cites every classification** — Asset classifications include IRS document citations with page numbers, not just model training data
* **Preserves full provenance** — Every artifact traces back to source image, detection, crop, and model response
* **Stage-gates everything** — Engineers review and approve before any workflow advances
* **Uses actual IRS documents** — Hybrid BM25 + vector retrieval over ingested IRS publications, not model knowledge cutoff
* **Solves context saturation** — Agentic retrieval selects only relevant evidence instead of dumping everything into context


---

## System Architecture (Full)

```
┌──────────────────────────────────────────────────────────────────┐
│                         ENGINEER UI                              │
│  • Review checkpoints at every workflow stage                    │
│  • Citation verification                                         │
│  • Correction interface                                          │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────┐
│                      NEXT.JS FRONTEND                            │
│  • Typed UI state + workflow gating                              │
│  • Firebase Auth + role-aware access                             │
│  • Real-time Firestore listeners                                 │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────┐
│                     FASTAPI BACKEND                              │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │  VISION LAYER   │  │ EVIDENCE LAYER  │  │  AGENTIC LAYER  │   │
│  ├─────────────────┤  ├─────────────────┤  ├─────────────────┤   │
│  │ Grounding DINO  │  │ PDF Parsing     │  │ LangGraph       │   │
│  │ SAM 2           │  │ Table Extract   │  │ Workflow Engine │   │
│  │ GPT-4o Vision   │  │ Text Chunking   │  │                 │   │
│  │ Region Cropper  │  │ BM25 Index      │  │ Room Agent      │   │
│  │ Grounding       │  │ FAISS Index     │  │ Asset Agent     │   │
│  │ Verifier        │  │ Hybrid Search   │  │ Takeoff Agent   │   │
│  └────────┬────────┘  └────────┬────────┘  │ Cost Agent      │   │
│           │                    │           └────────┬────────┘   │
│           │                    └────────────────────┤            │
│           └─────────────────────────────────────────┤            │
│                                                     │            │
│                    ┌────────────────────────────────▼──────┐     │
│                    │        MCP TOOL REGISTRY              │     │
│                    │  • bm25_search    • vector_search     │     │
│                    │  • hybrid_search  • get_table         │     │
│                    │  • get_chunk      • vision_detect     │     │
│                    └───────────────────────────────────────┘     │
│                                                                  │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────┐
│                     FIREBASE DATA LAYER                          │
│  • Storage: documents, images, exports                           │
│  • Firestore: studies, classifications, audit trails             │
│  • Auth: role-based access                                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## About

Basis demonstrates that **document intelligence problems share common architectural requirements**:

1. **Hybrid retrieval** for documents with both exact codes and narrative context
2. **Custom tokenization** that preserves domain-specific identifiers (not naive whitespace splitting)
3. **Agentic orchestration** for multi-step reasoning with tool routing
4. **Human-in-the-loop checkpoints** for auditability and defensibility
5. **Citation-first outputs** linking every claim to source evidence

The same pipeline that queries IRS depreciation tables can query HARA baselines, safety goals, TSRs, or verification evidence—because the architectural pattern is the same:

| IRS Domain | Safety Domain |
|------------|---------------|
| `§1245`, `168(e)(3)` | `HAZ-001`, `TSR-042` |
| Asset class `57.0` | ASIL-B, ASIL-D |
| IRS Pub 946 citations | ISO 26262 clause refs |
| Depreciation tables | FMEA tables, DFA matrices |

**Standardized headers, messy context, need for traceability.**

---