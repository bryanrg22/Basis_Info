# Basis Agentic Workflow Layer

Stage-gated agentic workflow for cost segregation studies, powered by LangChain, LangGraph, and MCP.

## Overview

This package provides the agentic orchestration layer that sits on top of the evidence layer. Agents use evidence retrieval tools via MCP to make evidence-backed decisions with full provenance tracking.

## Architecture

```
Frontend (Next.js) ←→ Firestore (real-time)
                           ↑
                    Agentic API (FastAPI)
                           ↑
              LangGraph Workflow Engine
                           ↑
         ┌─────────────────┼─────────────────┐
         ↓                 ↓                 ↓
    Room Agent      Asset Agent       Cost Agent
         │                 │                 │
         └────────── MCP Tool Registry ──────┘
                           ↑
              Evidence Layer (retrieval.py)
```

## Quick Start

### 1. Install dependencies

```bash
cd backEnd/agentic
pip install -e .

# Also install evidence layer
pip install -e ../evidence_layer
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required environment variables:
```env
# Azure OpenAI (primary provider)
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=...

# Model deployments (GPT-4.1 combo for best cost/performance)
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4.1           # Best results - complex reasoning
AZURE_OPENAI_DEPLOYMENT_NAME_FAST=gpt-4.1-nano # Most efficient - high-volume tasks

# LangSmith (optional but recommended)
LANGCHAIN_API_KEY=ls-...
LANGCHAIN_PROJECT=basis-agentic

# Firebase
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
```

**Model Strategy:**
- **GPT-4.1**: Primary model for complex reasoning, classification, and cost segregation decisions
- **GPT-4.1 nano**: Fast/cheap model for high-volume tasks (object detection, simple extraction)
- This combo provides the best cost/performance ratio for production workloads

### 3. Run the API server

```bash
uvicorn agentic.api.main:app --reload --port 8000
```

### 4. Start a workflow

```bash
curl -X POST http://localhost:8000/workflow/start \
  -H "Content-Type: application/json" \
  -d '{
    "study_id": "STUDY_001",
    "reference_doc_ids": ["IRS_PUB946_2024", "IRS_COSTSEG_ATG"],
    "study_doc_ids": []
  }'
```

## Package Structure

```
agentic/
├── config/               # Settings and LLM provider abstraction
├── mcp_server/           # MCP server exposing evidence tools
│   ├── server.py         # MCP server definition
│   └── tools/            # LangChain tool wrappers
├── agents/               # Stage-specific agents
│   ├── base_agent.py     # Abstract base with evidence backing
│   └── asset_agent.py    # IRS asset classification
├── graph/                # LangGraph workflow
│   ├── state.py          # Workflow state definition
│   ├── nodes.py          # Stage node functions
│   └── workflow.py       # Compiled workflow
├── firestore/            # Firestore integration
│   ├── client.py         # Firestore client
│   └── writeback.py      # Evidence-backed writes
├── observability/        # LangSmith tracing
└── api/                  # FastAPI endpoints
```

## Key Concepts

### Evidence-Backed Outputs

Every agent output includes:
- **result**: Structured classification data
- **citations**: List of chunk_ids/table_ids with page numbers
- **confidence**: Score based on evidence quality (0.0-1.0)
- **needs_review**: Flag if no evidence found

### Parallel Stage-Gated Workflow

The workflow uses **parallel execution with staggered pauses** for optimal engineer productivity:

```
                         load_study
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
            resource_extraction       analyze_rooms
            (ingest appraisal PDF)    (vision, 2 workers)
            ~30 seconds               ~2-3 minutes (BACKGROUND)
                  │                         │
                  ▼                         │
            PAUSE #1 ◄──────────────────────┤ (vision continues in background)
            (engineer reviews               │
             appraisal data)                │
                  │                         │
                  └────────────┬────────────┘
                               ▼
                          PAUSE #2
                          (engineer reviews rooms)
                               │
                               ▼
                        process_assets
                        (objects + takeoffs + classification + costs)
                               │
                               ▼
                    engineering_takeoff ←── PAUSE #3
                               │
                               ▼
                          completed
```

**Key Optimizations:**
- **Parallel ingestion**: Appraisal PDF ingested while vision runs in background
- **2 concurrent workers**: Vision analysis ~50% faster with parallel Azure OpenAI calls
- **Staggered pauses**: Engineer can review appraisal (~30s wait) while vision continues
- **Background processing**: `analyze_rooms` runs as `asyncio.create_task()`

**Workflow Status Values** (matches frontend):
```
uploading_documents → analyzing_rooms → resource_extraction → reviewing_rooms → engineering_takeoff → completed
```

### Appraisal Processing (resource_extraction_node)

The `resource_extraction_node` handles appraisal PDF ingestion with URAR section mapping:

1. **Ingest PDF** - Full pipeline (parse → chunk → index) via evidence_layer
2. **Extract fields** - Regex-based extraction for flat fields (GLA, bedrooms, etc.)
3. **Map tables to sections** - URAR tables → frontend-ready sections

```python
# In nodes.py - resource_extraction_node
from evidence_layer.src.map_appraisal_sections import map_appraisal_tables_to_sections

# After ingestion extracts tables to {doc_id}.tables.jsonl
sections = map_appraisal_tables_to_sections(
    tables_path=tables_path,
    fallback_fields=fields_dict,  # Regex extraction as fallback
)

# Stored in Firestore:
appraisal_resources = {
    "doc_id": doc_id,
    "ingested": True,
    "fields": fields_dict,        # Flat extraction (backward compat)
    **sections,                   # Rich sections for UI
}
# sections includes: subject, listing_and_contract, neighborhood,
# site, improvements, sales_comparison, cost_approach, reconciliation
```

**Why table mapping instead of GPT?**
- Uses same table extraction from ingestion (no extra parsing)
- Deterministic, no API costs
- Faster than LLM calls
- Falls back to regex for missing values

### Vision Pipeline (analyze_rooms_node)

The vision pipeline processes appraisal photos to detect and classify building components for cost segregation.

**Current Implementation:**
- Azure OpenAI GPT-4.1 vision for room/object detection
- 2 concurrent workers for parallel image processing
- Results stored in Firestore as `rooms` and `objects`

**Architecture (with Grounding):**
```
┌─────────────────────────────────────────────────────────────────┐
│                    VISION PIPELINE                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Appraisal Photos                                                │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────┐                     │
│  │  Grounding DINO                         │                     │
│  │  (Open-set object detection)            │                     │
│  │                                         │                     │
│  │  - Detects objects without predefined   │                     │
│  │    classes                              │                     │
│  │  - Returns bounding boxes + labels      │                     │
│  │  - Text-prompted: "HVAC, lighting,      │                     │
│  │    electrical panel, flooring"          │                     │
│  └─────────────────────────────────────────┘                     │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────┐                     │
│  │  SAM2 (Segment Anything Model 2)        │                     │
│  │  (Precise segmentation)                 │                     │
│  │                                         │                     │
│  │  - Takes bounding boxes from DINO       │                     │
│  │  - Generates pixel-perfect masks        │                     │
│  │  - Enables accurate measurements        │                     │
│  └─────────────────────────────────────────┘                     │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────┐                     │
│  │  GPT-4.1 (Azure OpenAI)                 │                     │
│  │  (Classification + Cost Segregation)    │                     │
│  │                                         │                     │
│  │  - Classifies detected objects          │                     │
│  │  - Determines IRS asset class           │                     │
│  │  - Assigns recovery periods (5/7/15/39) │                     │
│  │  - Generates evidence citations         │                     │
│  └─────────────────────────────────────────┘                     │
│         │                                                        │
│         ▼                                                        │
│  Firestore: rooms[], objects[]                                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Why DINO + SAM2?**
- **Grounding DINO**: Open-vocabulary detection - no need to retrain for new object types
- **SAM2**: State-of-the-art segmentation for precise object boundaries
- **GPT-4.1**: Reasoning layer for IRS classification and cost segregation rules
- **Combination**: Grounds LLM outputs in actual detected objects (reduces hallucination)

### MCP Tools

Available evidence tools:
- `bm25_search_tool`: Exact token matching (IRS codes, section numbers)
- `vector_search_tool`: Semantic similarity (paraphrases)
- `hybrid_search_tool`: Combined BM25 + vector
- `get_table_tool`: Fetch structured table by ID
- `get_chunk_tool`: Fetch chunk with provenance

### Observability (LangSmith)

All workflow executions are traced via LangSmith for debugging, monitoring, and optimization.

![LangSmith Trace View](https://github.com/user-attachments/assets/75340053-603c-4ad2-9c40-c28c62ad703e)

**What's captured:**
- Full workflow execution tree (nodes, edges, timing)
- LLM calls with prompts and responses
- Tool invocations and results
- Token usage and latency metrics

**Setup:** Add `LANGCHAIN_API_KEY` and `LANGCHAIN_PROJECT` to your `.env` file.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/workflow/start` | POST | Start workflow for a study |
| `/workflow/resume` | POST | Resume after engineer review |
| `/workflow/stage/{stage}` | POST | Trigger specific stage |
| `/workflow/{study_id}/status` | GET | Get workflow status |
| `/workflow/{study_id}/evidence` | GET | Get all citations |
| `/health` | GET | Health check |
| `/health/ready` | GET | Readiness check |

## Development

### Run tests

```bash
pytest tests/
```

### Add a new agent

1. Create new file in `agents/` extending `BaseStageAgent`
2. Implement `get_system_prompt()`, `get_output_schema()`, `parse_output()`
3. Add node function in `graph/nodes.py`
4. Wire into workflow in `graph/workflow.py`

## License

Proprietary - Basis Team
