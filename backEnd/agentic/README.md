# Basis Agentic Workflow Layer

Stage-gated agentic workflow for cost segregation studies, powered by **LangChain**, **LangGraph**, and **MCP**.

## Overview

This package provides the agentic orchestration layer that sits on top of the evidence layer. It features two key AI patterns:

1. **Agentic RAG** - LLM-driven retrieval where agents decide when, what, and how to search IRS/RSMeans documents
2. **Agentic Tool Use** - Multi-agent appraisal extraction with self-correction loops

Agents use evidence retrieval tools via MCP to make evidence-backed decisions with full provenance tracking.

## Tech Stack

| Category | Technology | Purpose |
|----------|------------|---------|
| **Framework** | LangGraph | Stage-gated workflow orchestration |
| **Agents** | LangChain | Tool-calling agents with ReAct pattern |
| **LLMs** | GPT-5-nano (text), GPT-4o-mini (vision) | Cost-optimized model selection |
| **Document AI** | Azure Document Intelligence | PDF field extraction with KEY_VALUE_PAIRS |
| **Database** | Firestore | Real-time state, checkpoints, job queue |
| **API** | FastAPI | Async REST endpoints |
| **Background Jobs** | Custom Firestore-backed worker | Durable task execution |
| **Observability** | LangSmith | Trace visualization, debugging |
| **Alerting** | Slack webhooks | Real-time failure notifications |
| **Evidence Search** | MCP (Model Context Protocol) | BM25, vector, hybrid search tools |
| **Containerization** | Docker Compose | Backend + Worker deployment |

## Architecture

```
Frontend (Next.js) <--> Firestore (real-time)
                              |
                    +--------------------+
                    |                    |
              Agentic API           Job Worker
              (FastAPI)             (Background)
                    |                    |
                    +--------------------+
                              |
                  LangGraph Workflow Engine
                              |
        +---------------------+---------------------+
        |                     |                     |
        v                     v                     v
+---------------+     +---------------+     +---------------+
|  Appraisal    |     | Room/Asset/   |     |  Cost Agent   |
|  Extraction   |     | Object/Take   |     |               |
|  (Agentic     |     | off Agents    |     |  (Agentic     |
|  Tool Use)    |     | (Agentic RAG) |     |  RAG)         |
+-------+-------+     +-------+-------+     +-------+-------+
        |                     |                     |
        |    Azure DI         |    MCP Tool         |
        |    Vision           |    Registry         |
        |    MISMO            |    (search)         |
        |                     |                     |
        +---------------------+---------------------+
                              |
                  Evidence Layer (retrieval.py)
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
│   ├── classification_verifier.py  # IRS defensibility verification
│   ├── document_extraction_agent.py # PDF field extraction
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
│   ├── workflow.py       # Compiled workflow
│   ├── corrections.py    # Correction cascade logic
│   └── feedback.py       # Engineer feedback handling
├── firestore/            # Firestore integration
│   ├── client.py         # Firestore client
│   ├── checkpointer.py   # LangGraph state persistence
│   ├── writeback.py      # Evidence-backed writes
│   ├── job_queue.py      # Durable background job queue
│   └── checkpoint_history.py  # Checkpoint history tracking
├── workers/              # Background job processing
│   └── job_worker.py     # Durable job worker
├── validation/           # Cross-stage validation
│   └── cross_validator.py # Classification/Takeoff/Cost consistency
├── evidence/             # Evidence aggregation
│   ├── models.py         # Evidence models
│   └── aggregator.py     # Citation deduplication
├── observability/        # Monitoring and debugging
│   ├── tracing.py        # LangSmith tracing
│   ├── alerts.py         # Slack/webhook alerting
│   ├── trace_analyzer.py # Trace analysis and debugging
│   ├── cost_tracker.py   # Token/cost tracking
│   └── decision_log.py   # Agent decision logging
└── api/                  # FastAPI endpoints
    ├── main.py           # App initialization
    ├── routes/           # API routes
    ├── auth/             # Authentication
    ├── rate_limit.py     # Rate limiting
    └── exceptions.py     # Exception handlers
```

## Key Concepts

### Agentic RAG (Retrieval-Augmented Generation)

Unlike traditional RAG where retrieval happens before generation, **Agentic RAG** lets the LLM control the retrieval process:

```
+------------------------------------------------------------------+
|  TRADITIONAL RAG                                                  |
|                                                                   |
|  Query --> Retrieve --> Generate                                  |
|  (fixed)   (always)     (once)                                    |
+------------------------------------------------------------------+

+------------------------------------------------------------------+
|  AGENTIC RAG (What Basis Uses)                                    |
|                                                                   |
|  Task --> Think --> Search? --> Think --> Search? --> Generate    |
|           |         (LLM       |         (LLM        |            |
|           |          decides)  |          decides)   |            |
|           +---- ReAct Loop ----+---- Multi-hop ------+            |
+------------------------------------------------------------------+
```

**Benefits of Agentic RAG:**

| Capability | Traditional RAG | Agentic RAG |
|------------|-----------------|-------------|
| Query formulation | Fixed template | LLM crafts optimal query |
| Multi-hop reasoning | Single pass | Search -> analyze -> search again |
| Tool selection | Same retriever | LLM picks BM25 vs vector vs hybrid |
| Self-correction | No | Re-search if results unhelpful |

**Which agents use Agentic RAG?**

| Agent | Uses Agentic RAG | Search Tools |
|-------|------------------|--------------|
| AssetAgent | Yes | `bm25_search`, `hybrid_search`, `get_table` |
| RoomAgent | Yes | `hybrid_search` |
| ObjectAgent | Yes | `hybrid_search` |
| TakeoffAgent | Yes | `hybrid_search` |
| CostAgent | Yes | `hybrid_search` |
| Appraisal Agents | No | Uses extraction tools instead |

### Evidence-Backed Outputs

Every agent output includes:
- **result**: Structured classification data
- **citations**: List of chunk_ids/table_ids with page numbers
- **confidence**: Score based on evidence quality (0.0-1.0)
- **needs_review**: Flag if no evidence found

---

## API Server + Worker Architecture

Basis uses a **separate worker process** for long-running tasks. This is an industry-standard pattern used by production systems at scale.

### Why Separate API and Worker?

Your backend API server handles HTTP requests from users. When a user starts a workflow:

1. **API must respond quickly** - Users expect responses within seconds
2. **Vision analysis is slow** - Takes 2-3+ minutes

If the API runs vision analysis directly:
- HTTP request hangs for minutes (bad UX, timeouts)
- Server resources tied up (can't handle other requests)
- If server restarts, work is lost

### The Solution

```
+-------------------+         +-------------------+
|   API Server      |         |     Worker        |
|   (backend)       |         |                   |
|                   |         |                   |
| - Fast responses  |         | - Long tasks      |
| - User requests   |         | - Vision AI       |
| - Enqueue jobs    |         | - Retries         |
+--------+----------+         +---------+---------+
         |                              |
         |      +---------------+       |
         +----->|  Job Queue    |<------+
                | (Firestore)   |
                +---------------+
```

**API Server (backend)**:
- Receives user request: "Start workflow"
- Writes job to queue: `{type: "analyze_rooms", study_id: "abc"}`
- Returns immediately: "Workflow started!" (~100ms)

**Worker**:
- Polls the job queue continuously
- Picks up jobs and runs vision analysis (2-3 min)
- Updates Firestore with results
- Handles retries if something fails

### Industry Examples

This pattern is used by virtually every production system:

| Company | Pattern |
|---------|---------|
| **Stripe** | API server + async workers for payment processing |
| **GitHub** | Web server + Sidekiq workers for CI/CD jobs |
| **Slack** | API + workers for message processing, search indexing |
| **Netflix** | Microservices + worker pools for encoding, recommendations |

**Common tools for this pattern:**
- **Celery** (Python) - most popular
- **Sidekiq** (Ruby)
- **Bull** (Node.js)
- **AWS SQS + Lambda**
- **Google Cloud Tasks**

Basis uses Firestore as the job queue, which provides durability and works well at our scale.

---

## Parallel Stage-Gated Workflow

The workflow uses **true parallel execution** for optimal engineer productivity:

```
                         load_study
                               |
                  +------------+------------+
                  |                         |
                  v                         v
            Enqueue Job              resource_extraction
            (analyze_rooms)          (ingest appraisal PDF)
                  |                   ~30 seconds
                  v                         |
            Worker picks up                 v
            job immediately           PAUSE #1 <-- Engineer reviews appraisal
                  |                         |   (vision runs in background)
                  v                         |
            Vision analysis           +-----+
            ~2-3 minutes              |
            (background)              v
                  |              roomsReady?
                  |                   |
                  +-------------------+
                               |
                               v
                          PAUSE #2
                          (engineer reviews rooms)
                               |
                               v
                        process_assets
                        (objects + takeoffs + classification + costs)
                               |
                               v
                    engineering_takeoff <-- PAUSE #3
                               |
                               v
                          completed
```

**Key Optimizations:**
- **True parallel execution**: Vision analysis starts immediately when workflow begins
- **2 concurrent workers**: Vision analysis ~50% faster with parallel Azure OpenAI calls
- **Staggered pauses**: Engineer reviews appraisal (~30s wait) while vision continues
- **Background processing**: `analyze_rooms` runs via durable job queue

**UX Impact**: Engineer sees first review screen at ~30s instead of ~3+ minutes.

**Workflow Status Values** (matches frontend):
```
uploading_documents -> analyzing_rooms -> resource_extraction -> reviewing_rooms -> engineering_takeoff -> completed
```

---

## Durable Job Queue (Phase 5)

Background tasks are processed via a **Firestore-backed durable job queue** that survives server restarts.

### Job Types

| Job Type | Description | Timeout |
|----------|-------------|---------|
| `analyze_rooms` | Vision analysis + room enrichment | 10 min |
| `process_assets` | Objects, takeoffs, classification, costs | 10 min |
| `reclassify` | Re-run classification for specific components | 5 min |
| `recalculate_costs` | Re-run cost estimation | 5 min |
| `cascade_correction` | Propagate engineer corrections | 5 min |

### Job Lifecycle

```
pending --> claimed --> running --> completed
                           |
                           +--> failed (retries exhausted)
                           |
                           +--> retry (will be picked up again)
```

### Features

- **Persistence**: Jobs survive server/worker restarts
- **Priority ordering**: Higher priority jobs processed first (1-10 scale)
- **Retry logic**: Automatic retry with configurable max retries
- **Timeout protection**: Jobs reset if worker crashes
- **Progress tracking**: Real-time progress updates
- **Stale job cleanup**: Automatically recovers from worker crashes

### Usage

```python
from agentic.firestore.job_queue import JobQueue

job_queue = JobQueue()

# Enqueue a job
job_id = await job_queue.enqueue(
    job_type="analyze_rooms",
    study_id="study_123",
    input_data={"images": [...]},
    timeout_seconds=600,
    max_retries=2,
    priority=3,  # Higher priority (1-10, lower = higher)
)

# Check job status
job = await job_queue.get_job(job_id)
print(job.status)  # "pending", "running", "completed", etc.
```

---

## Cross-Stage Validation (Phase 5)

The `CrossValidator` ensures data consistency across workflow stages:

### Validation Rules

**Classification <-> Takeoff:**
- Section 1245 with large SF quantity -> warning (likely structural)
- 39-year bucket with EA unit -> info (typically SF/LF)
- Unit type mismatch with depreciation bucket

**Takeoff <-> Cost:**
- Unit cost within industry range
- RSMeans line item found
- Total cost proportional to quantity

### Industry Reference Data

```python
# Cost ranges by component type ($/unit)
COST_RANGES = {
    "light_fixture": {"min": 20, "max": 1000, "unit": "EA"},
    "electrical_outlet": {"min": 15, "max": 150, "unit": "EA"},
    "hvac_unit": {"min": 1000, "max": 15000, "unit": "EA"},
    "carpet": {"min": 2, "max": 30, "unit": "SF"},
    # ...
}
```

### Usage

```python
from agentic.validation.cross_validator import CrossValidator

validator = CrossValidator()
results = validator.validate_all(
    classifications=asset_classifications,
    takeoffs=takeoffs,
    costs=cost_estimates,
)

for result in results:
    if result.has_warnings:
        for issue in result.issues:
            print(f"{issue.severity}: {issue.message}")
```

---

## Slack Alerting System (Phase 6)

Production alerting via **Slack** for workflow failures, with throttling to prevent alert fatigue. Alerts are sent to the team's Slack channel in real-time when workflows fail or need review.

### Alert Channels

| Channel | Configuration | Use Case |
|---------|---------------|----------|
| **Slack** | `ALERT_SLACK_WEBHOOK` | Team notifications |
| **Webhook** | `ALERT_WEBHOOK_URL` | Custom integrations |
| **Log** | Always enabled | Development/debugging |

### Alert Severity

- **warning**: Needs review, may be correct (yellow)
- **error**: Likely incorrect, requires attention (red)
- **critical**: System failure, immediate action needed (dark red)

### Automatic Alerting

Use the `@alert_on_failure` decorator for automatic alerting:

```python
from agentic.observability.alerts import alert_on_failure

@alert_on_failure("room_agent")
async def analyze_rooms(study_id: str, images: list[str]) -> dict:
    # If this function throws, a Slack alert is sent automatically
    ...
```

### Manual Alerting

```python
from agentic.observability.alerts import send_alert

await send_alert(
    study_id="study_123",
    stage="classification",
    error_type="LLM_TIMEOUT",
    error_message="LLM call timed out after 60s",
    severity="error",
    context={"component_id": "light_fixture_1"},
)
```

### Alert Payload (Slack)

Alerts include:
- Study ID and workflow stage
- Error type and message
- Timestamp and severity
- LangSmith trace link (if available)
- Flagged fields list
- Workflow stats (cost, duration, tokens)

### Throttling

Similar alerts are throttled (default: 5 minutes between duplicates) to prevent alert fatigue during incident storms.

---

## Evidence Aggregator (Phase 6)

Unified evidence collection and deduplication across all workflow stages.

### Features

- **Automatic deduplication**: Same citation from multiple stages stored once
- **Component tracking**: Know which components lack evidence
- **Stage organization**: Evidence indexed by stage, component, and document
- **Summary statistics**: Coverage metrics for audit

### Usage

```python
from agentic.evidence.aggregator import EvidenceAggregator

aggregator = EvidenceAggregator(study_id="study_123")

# Add citations from different stages
aggregator.add_citations(
    citations=room_agent_citations,
    stage="room",
    component_id="room_1",
    component_name="Kitchen",
)

aggregator.add_citations(
    citations=classification_citations,
    stage="classification",
    component_id="hvac_1",
    component_name="HVAC Unit",
)

# Get organized pack
pack = aggregator.get_organized_pack()

print(f"Total citations: {pack.total_citations}")
print(f"Stages covered: {pack.summary.stages_covered}")
print(f"Components without evidence: {pack.summary.components_without_evidence}")
```

### Evidence Pack Structure

```python
{
    "study_id": "study_123",
    "total_citations": 45,
    "entries": [...],
    "by_stage": {"room": [...], "classification": [...]},
    "by_component": {"hvac_1": [...], "light_1": [...]},
    "by_document": {"IRS_PUB946": [...], "RSMEANS_2024": [...]},
    "summary": {
        "unique_documents": 5,
        "stages_covered": ["room", "object", "classification", "cost"],
        "avg_citations_per_component": 2.5,
        "components_without_evidence": ["misc_item_1"],
    }
}
```

---

## Appraisal Processing (Multi-Agent Agentic Extraction)

The `resource_extraction_node` handles appraisal PDF ingestion with a **multi-agent LangGraph system** that reasons, verifies, and self-corrects:

```
+------------------------------------------------------------------+
|  APPRAISAL EXTRACTION LANGGRAPH                                    |
+------------------------------------------------------------------+
|                                                                    |
|                    +----------------------------+                  |
|                    |    EXTRACTOR AGENT         |                  |
|                    |                            |                  |
|                    |  "Extract intelligently"   |                  |
|                    |  Tools: MISMO, Azure DI,   |                  |
|                    |         Vision             |                  |
|                    +-------------+--------------+                  |
|                                  |                                 |
|                                  v                                 |
|                    +----------------------------+                  |
|                    |    VERIFIER AGENT          |                  |
|                    |                            |                  |
|                    |  "Be skeptical. Find       |                  |
|                    |   errors. Question         |                  |
|                    |   everything."             |                  |
|                    +-------------+--------------+                  |
|                                  |                                 |
|              +-------------------+-------------------+             |
|              |                   |                   |             |
|         all_good          needs_correction     max_iterations      |
|              |                   |                   |             |
|              v                   v                   v             |
|           +-----+    +----------------------+     +-----+          |
|           | END |    |  CORRECTOR AGENT     |     | END |          |
|           +-----+    |                      |     +-----+          |
|                      |  "Fix using          |                      |
|                      |   DIFFERENT method"  |                      |
|                      +-----------+----------+                      |
|                                  |                                 |
|                                  +--> loops back to verifier       |
|                                       (max 2 iterations)           |
+------------------------------------------------------------------+
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
- Agentic extraction fails? -> Falls back to regex via `map_appraisal_tables_to_sections()`
- No Azure DI? -> ExtractorAgent uses Vision fallback
- All fail? -> Returns regex results with `needs_review: true`

---

## Vision Pipeline (analyze_rooms_node)

The vision pipeline processes appraisal photos to detect and classify building components for cost segregation.

**Current Implementation:**
- GPT-4o-mini vision for room/object detection (GPT-5.2 when Azure approved)
- 2 concurrent workers for parallel image processing
- Results stored in Firestore as `rooms` and `objects`

**Architecture:**
```
+------------------------------------------------------------------+
|                    VISION PIPELINE                                 |
+------------------------------------------------------------------+
|                                                                    |
|  Appraisal Photos                                                  |
|         |                                                          |
|         v                                                          |
|  +-------------------------------------------+                     |
|  |  GPT-4o-mini Vision (Azure OpenAI)        |                     |
|  |                                           |                     |
|  |  - Analyzes full image                    |                     |
|  |  - Detects room type                      |                     |
|  |  - Identifies building components         |                     |
|  |  - Returns structured JSON                |                     |
|  +-------------------------------------------+                     |
|         |                                                          |
|         v                                                          |
|  +-------------------------------------------+                     |
|  |  Self-Verification Tools                  |                     |
|  |                                           |                     |
|  |  - verify_room_classification             |                     |
|  |  - estimate_detection_confidence          |                     |
|  |  - crop_and_analyze_region (PIL)          |                     |
|  |  - request_human_verification             |                     |
|  +-------------------------------------------+                     |
|         |                                                          |
|         v                                                          |
|  Firestore: rooms[], objects[]                                     |
|                                                                    |
+------------------------------------------------------------------+
```

**Vision Agent Features:**
- **Self-verification**: Cross-checks room type against detected objects
- **Focused analysis**: Crops and re-analyzes specific regions for unclear areas
- **Human flagging**: Requests review for low-confidence results
- **Retry logic**: Re-prompts for JSON when parsing fails

**Future Goal: Grounded Vision Pipeline**

A planned enhancement using Grounding DINO + SAM2 for improved object detection:

```
Image → Grounding DINO → [Bounding boxes] → SAM2 → [Pixel masks] → GPT → Classification
```

- **Grounding DINO**: Open-vocabulary object detection with text prompts
- **SAM2**: Pixel-perfect segmentation masks for precise boundaries
- **Benefits**: Reduced hallucination, exact object localization, measurement capability

This would require GPU infrastructure and is planned for a future phase.

---

## MCP Tools

Available evidence tools:
- `bm25_search_tool`: Exact token matching (IRS codes, section numbers)
- `vector_search_tool`: Semantic similarity (paraphrases)
- `hybrid_search_tool`: Combined BM25 + vector
- `get_table_tool`: Fetch structured table by ID
- `get_chunk_tool`: Fetch chunk with provenance

---

## Observability (LangSmith)

All workflow executions are traced via LangSmith for debugging, monitoring, and optimization.

**What's captured:**
- Full workflow execution tree (nodes, edges, timing)
- LLM calls with prompts and responses
- Tool invocations and results
- Token usage and latency metrics
- Error traces with full context

### Trace Analyzer (Phase 6)

Programmatic access to trace data for alerting and debugging:

```python
from agentic.observability.trace_analyzer import get_trace_analyzer

analyzer = get_trace_analyzer()
summary = analyzer.get_latest_trace(study_id="study_123")

print(f"Duration: {summary.duration_seconds}s")
print(f"Cost: ${summary.total_cost_usd}")
print(f"Flagged fields: {summary.flagged_fields}")
```

---

## License

Proprietary - Basis Team
