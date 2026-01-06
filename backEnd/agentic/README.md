# Basis Agentic Workflow Layer

Stage-gated agentic workflow for cost segregation studies, powered by **LangChain**, **LangGraph**, and **MCP**.

## Overview

This package provides the agentic orchestration layer that sits on top of the evidence layer. It features two key AI patterns:

1. **Agentic RAG** - LLM-driven retrieval where agents decide when, what, and how to search IRS/RSMeans documents
2. **Agentic Tool Use** - Multi-agent appraisal extraction with self-correction loops

Agents use evidence retrieval tools via MCP to make evidence-backed decisions with full provenance tracking.

## Architecture

```
Frontend (Next.js) ←→ Firestore (real-time)
                           ↑
                    Agentic API (FastAPI)
                           ↑
              LangGraph Workflow Engine
                           ↑
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
    ▼                      ▼                      ▼
┌────────────┐     ┌─────────────┐      ┌─────────────┐
│ Appraisal  │     │ Room/Asset/ │      │ Cost Agent  │
│ Extraction │     │ Object/Take │      │             │
│ (Agentic   │     │ off Agents  │      │ (Agentic    │
│ Tool Use)  │     │ (Agentic    │      │ RAG)        │
│            │     │ RAG)        │      │             │
└─────┬──────┘     └──────┬──────┘      └──────┬──────┘
      │                   │                    │
      │    Azure DI       │    MCP Tool        │
      │    Vision         │    Registry        │
      │    MISMO          │    (search)        │
      │                   │                    │
      └───────────────────┴────────────────────┘
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

# Azure Document Intelligence (for tiered appraisal extraction)
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=...

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
│   ├── settings.py       # Environment configuration
│   └── llm_providers.py  # Azure OpenAI / OpenAI abstraction
├── mcp_server/           # MCP server exposing evidence tools
│   ├── server.py         # MCP server definition
│   └── tools/            # LangChain tool wrappers (Agentic RAG)
│       ├── search_tools.py   # bm25_search, vector_search, hybrid_search
│       └── fetch_tools.py    # get_table, get_chunk
├── agents/               # Stage-specific agents
│   ├── base_agent.py     # Abstract base with Agentic RAG
│   ├── asset_agent.py    # IRS asset classification (Agentic RAG)
│   ├── room_agent.py     # Room enrichment (Agentic RAG)
│   ├── object_agent.py   # Object enrichment (Agentic RAG)
│   ├── takeoff_agent.py  # Quantity takeoff (Agentic RAG)
│   ├── cost_agent.py     # Cost estimation (Agentic RAG)
│   ├── vision_agent.py   # Image analysis (GPT-4o Vision)
│   └── appraisal/        # Multi-agent appraisal extraction
│       ├── __init__.py       # Module exports
│       ├── schemas.py        # Pydantic I/O models
│       ├── tools.py          # Extraction tools (Azure DI, Vision, MISMO)
│       ├── extractor_agent.py    # Intelligent extraction
│       ├── verifier_agent.py     # Skeptical verification
│       ├── corrector_agent.py    # Error correction
│       └── orchestrator.py       # LangGraph StateGraph coordination
├── graph/                # LangGraph workflow
│   ├── state.py          # Workflow state definition
│   ├── nodes.py          # Stage node functions
│   ├── edges.py          # Conditional routing
│   └── workflow.py       # Compiled workflow
├── firestore/            # Firestore integration
│   ├── client.py         # Firestore client
│   ├── checkpointer.py   # LangGraph state persistence
│   └── writeback.py      # Evidence-backed writes
├── observability/        # LangSmith tracing
└── api/                  # FastAPI endpoints
```

## Key Concepts

### Agentic RAG (Retrieval-Augmented Generation)

Unlike traditional RAG where retrieval happens before generation, **Agentic RAG** lets the LLM control the retrieval process:

```
┌─────────────────────────────────────────────────────────────────┐
│  TRADITIONAL RAG                                                 │
│                                                                 │
│  Query ──► Retrieve ──► Generate                                │
│  (fixed)   (always)     (once)                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  AGENTIC RAG (What Basis Uses)                                   │
│                                                                 │
│  Task ──► Think ──► Search? ──► Think ──► Search? ──► Generate  │
│           │         (LLM       │         (LLM        │          │
│           │          decides)  │          decides)   │          │
│           └──── ReAct Loop ────┴──── Multi-hop ──────┘          │
└─────────────────────────────────────────────────────────────────┘
```

**Benefits of Agentic RAG:**

| Capability | Traditional RAG | Agentic RAG |
|------------|-----------------|-------------|
| Query formulation | Fixed template | LLM crafts optimal query |
| Multi-hop reasoning | Single pass | Search → analyze → search again |
| Tool selection | Same retriever | LLM picks BM25 vs vector vs hybrid |
| Self-correction | No | Re-search if results unhelpful |

**Which agents use Agentic RAG?**

| Agent | Uses Agentic RAG | Search Tools |
|-------|------------------|--------------|
| AssetAgent | ✅ Yes | `bm25_search`, `hybrid_search`, `get_table` |
| RoomAgent | ✅ Yes | `hybrid_search` |
| ObjectAgent | ✅ Yes | `hybrid_search` |
| TakeoffAgent | ✅ Yes | `hybrid_search` |
| CostAgent | ✅ Yes | `hybrid_search` |
| Appraisal Agents | ❌ No | Uses extraction tools instead |

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

### Appraisal Processing (Multi-Agent Agentic Extraction)

The `resource_extraction_node` handles appraisal PDF ingestion with a **multi-agent LangGraph system** that reasons, verifies, and self-corrects:

```
┌─────────────────────────────────────────────────────────────────┐
│  APPRAISAL EXTRACTION LANGGRAPH                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│                    ┌──────────────────────────┐                 │
│                    │    EXTRACTOR AGENT       │                 │
│                    │                          │                 │
│                    │  "Extract intelligently" │                 │
│                    │  Tools: MISMO, Azure DI, │                 │
│                    │         Vision           │                 │
│                    └───────────┬──────────────┘                 │
│                                │                                │
│                                ▼                                │
│                    ┌──────────────────────────┐                 │
│                    │    VERIFIER AGENT        │                 │
│                    │                          │                 │
│                    │  "Be skeptical. Find     │                 │
│                    │   errors. Question       │                 │
│                    │   everything."           │                 │
│                    └───────────┬──────────────┘                 │
│                                │                                │
│              ┌─────────────────┼─────────────────┐              │
│              │                 │                 │              │
│         all_good        needs_correction    max_iterations      │
│              │                 │                 │              │
│              ▼                 ▼                 ▼              │
│           ┌─────┐    ┌────────────────────┐   ┌─────┐          │
│           │ END │    │  CORRECTOR AGENT   │   │ END │          │
│           └─────┘    │                    │   └─────┘          │
│                      │  "Fix using        │                    │
│                      │   DIFFERENT method"│                    │
│                      └─────────┬──────────┘                    │
│                                │                               │
│                                └───► loops back to verifier    │
│                                      (max 2 iterations)        │
└─────────────────────────────────────────────────────────────────┘
```

**Module:** `agentic/agents/appraisal/`

**The Three Agents:**

| Agent | Role | Tools | LLM |
|-------|------|-------|-----|
| **ExtractorAgent** | Intelligent extraction | `parse_mismo_xml`, `extract_with_azure_di`, `extract_with_vision` | gpt-5-nano |
| **VerifierAgent** | Skeptical plausibility checking | `validate_extraction`, `vision_recheck_field` | gpt-5-nano |
| **CorrectorAgent** | Fix errors using different method | `extract_with_azure_di`, `extract_with_vision`, `vision_recheck_field` | gpt-5-nano |

**Tool Cost Strategy:**
- `parse_mismo_xml` - FREE, 100% confidence
- `extract_with_azure_di` - $0.10-0.50/doc, 70-95% confidence
- `extract_with_vision` - $0.10-0.20/call, 60-90% confidence
- `validate_extraction` - FREE (rule-based)

```python
# In nodes.py - resource_extraction_node
from agentic.agents.appraisal import run_appraisal_extraction

extraction_output = await run_appraisal_extraction(
    study_id=state["study_id"],
    pdf_path=str(pdf_path),
    context=extraction_context,
    max_iterations=2,  # Max correction loops
)

sections = extraction_output["extraction_result"]
audit_trail = extraction_output["audit_trail"]

# Stored in Firestore:
appraisal_resources = {
    "doc_id": doc_id,
    "ingested": True,
    "fields": fields_dict,              # Flat extraction (backward compat)
    "_extraction_audit": audit_trail,   # Full audit trail for IRS
    **sections,                         # Rich sections for UI
}
```

**Audit Trail (IRS Defensibility):**
```python
audit_trail = {
    "study_id": "STUDY_001",
    "started_at": "2024-01-15T10:30:00Z",
    "completed_at": "2024-01-15T10:30:45Z",
    "iterations": 1,
    "final_confidence": 0.92,
    "needs_review": False,
    "agent_calls": [
        {"agent_name": "ExtractorAgent", "tools_used": ["extract_with_azure_di"], ...},
        {"agent_name": "VerifierAgent", "tools_used": ["validate_extraction"], ...},
    ],
    "field_history": [
        {"field_key": "improvements.year_built", "action": "extracted", "value": 1995, ...},
        {"field_key": "improvements.year_built", "action": "flagged", "issue_type": "ocr_error", ...},
        {"field_key": "improvements.year_built", "action": "corrected", "value": 1995, ...},
    ]
}
```

**Critical Fields (require >= 0.90 confidence):**
- `property_address`, `year_built`, `gross_living_area`
- `appraised_value`, `contract_price`, `effective_date`

**Graceful Degradation:**
- Agentic extraction fails? → Falls back to regex via `map_appraisal_tables_to_sections()`
- No Azure DI? → ExtractorAgent uses Vision fallback
- All fail? → Returns regex results with `needs_review: true`

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
