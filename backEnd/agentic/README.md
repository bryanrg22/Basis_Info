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
# LLM Provider (choose one)
OPENAI_API_KEY=sk-...

# Or Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o

# LangSmith (optional but recommended)
LANGCHAIN_API_KEY=ls-...
LANGCHAIN_PROJECT=basis-agentic

# Firebase
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
```

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

### Stage-Gated Workflow

The workflow progresses through stages, pausing at review checkpoints:
1. `uploading_documents` → `analyzing_rooms` → `reviewing_rooms`
2. → `analyzing_takeoffs` → `reviewing_takeoffs`
3. → `viewing_report` → `reviewing_assets` → `verifying_assets`
4. → `completed`

### MCP Tools

Available evidence tools:
- `bm25_search_tool`: Exact token matching (IRS codes, section numbers)
- `vector_search_tool`: Semantic similarity (paraphrases)
- `hybrid_search_tool`: Combined BM25 + vector
- `get_table_tool`: Fetch structured table by ID
- `get_chunk_tool`: Fetch chunk with provenance

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
