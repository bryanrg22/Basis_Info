# Over-Engineering Analysis & Simplification Plan

This document analyzes the Basis cost segregation platform architecture, identifies over-engineered components, and provides a detailed plan to simplify them.

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture Overview](#current-architecture-overview)
3. [Complete Workflow Visualization](#complete-workflow-visualization)
4. [Over-Engineered Components](#over-engineered-components)
   - [1. Appraisal Extraction Multi-Agent](#1-appraisal-extraction-multi-agent-fixed)
   - [2. Room Enrichment](#2-room-enrichment)
   - [3. Object Enrichment](#3-object-enrichment)
   - [4. Takeoff Agent](#4-takeoff-agent)
   - [5. Cost Agent](#5-cost-agent)
   - [6. Classification Verification](#6-classification-verification)
   - [7. Correction Cascade System](#7-correction-cascade-system)
   - [8. Evidence Pack System](#8-evidence-pack-system)
5. [Components That ARE Appropriately Designed](#components-that-are-appropriately-designed)
6. [LLM Call Analysis](#llm-call-analysis)
7. [Fuzzy Matching with Embeddings](#fuzzy-matching-with-embeddings)
8. [Implementation Priority](#implementation-priority)
9. [Detailed Fix Instructions](#detailed-fix-instructions)

---

## Executive Summary

### The Core Problem

The system uses **LLMs for tasks that don't require reasoning**. LLMs are powerful but:
- Hallucinate (make up information)
- Are non-deterministic (same input → different outputs)
- Are slow (seconds per call)
- Are expensive ($0.01-0.10 per call)

### The Principle

| Task Type | Best Tool | Example |
|-----------|-----------|---------|
| **Pattern Matching** | Specialized ML or Rules | "Extract address from form" |
| **Lookup/Mapping** | Database or Dict | "What components are in a kitchen?" |
| **Reasoning** | LLM | "Is this carpet 5-year or 27.5-year property?" |

### Current vs. Ideal

```
CURRENT FLOW (Over-Engineered):
PDF → Azure DI → LLM Verify → LLM Correct → Structured Data
Room → LLM Enrich → RAG Search → LLM Synthesize → Enriched Room
Object → LLM Enrich → RAG Search → LLM Classify → LLM Verify → Result

IDEAL FLOW (Simplified):
PDF → Azure DI → Structured Data (no LLM needed)
Room → Static Mapping → Components (no LLM needed)
Object → Static Mapping → Attributes → LLM Classify (only 1 LLM call)
```

### Expected Improvements

| Metric | Current | After Simplification |
|--------|---------|---------------------|
| Processing Time | ~3-5 minutes | ~30-60 seconds |
| LLM API Calls | ~50-100 per study | ~10-20 per study |
| API Cost | ~$1-2 per study | ~$0.10-0.20 per study |
| Failure Points | ~47 | ~10 |
| Reliability | ~70% success rate | ~95% success rate |

---

## Current Architecture Overview

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER UPLOADS FILES                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         /workflow/start                                      │
│                         (FastAPI Endpoint)                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│      BACKEND (Synchronous)      │   │      WORKER (Background Job)    │
│                                 │   │                                 │
│  resource_extraction_node()     │   │  analyze_rooms job:             │
│  ├── Download PDF               │   │  ├── Vision Analysis            │
│  ├── Ingest (BM25 + FAISS)      │   │  │   └── GPT-4o per image       │
│  ├── Azure DI Extraction        │   │  └── Room Enrichment            │
│  └── Save to Firestore          │   │      └── LLM + RAG per room     │
│                                 │   │                                 │
│  ⏱️ ~45 seconds                 │   │  ⏱️ ~170 seconds                │
└─────────────────────────────────┘   └─────────────────────────────────┘
                    │                               │
                    ▼                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PAUSE #1: resource_extraction                             │
│                    (User reviews appraisal data)                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PAUSE #2: reviewing_rooms                                 │
│                    (User reviews room categorization)                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    /workflow/resume                                          │
│                    (Triggers process_assets_node)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    process_assets_node()                                     │
│                                                                             │
│  Step 1: Object Enrichment      → LLM + RAG per object (~30s)              │
│  Step 2: Takeoff Calculation    → LLM per object (~20s)                    │
│  Step 3: Asset Classification   → LLM + RAG per object (~40s)              │
│  Step 4: Classification Verify  → LLM per object (~20s)                    │
│  Step 5: Cost Estimation        → RSMeans lookup (~5s)                     │
│  Step 6: Cross-Validation       → Rule-based (~1s)                         │
│                                                                             │
│  Total: ~120 seconds for 10 objects                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PAUSE #3: engineering_takeoff                             │
│                    (User reviews classifications & costs)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `backEnd/agentic/graph/workflow.py` | LangGraph workflow orchestration |
| `backEnd/agentic/graph/nodes.py` | Stage execution functions |
| `backEnd/agentic/agents/room_agent.py` | Room enrichment (over-engineered) |
| `backEnd/agentic/agents/object_agent.py` | Object enrichment (over-engineered) |
| `backEnd/agentic/agents/asset_agent.py` | Asset classification (appropriate) |
| `backEnd/agentic/agents/classification_verifier.py` | Verification (over-engineered) |
| `backEnd/agentic/firestore/writeback.py` | Firestore persistence |

---

## Complete Workflow Visualization

This section shows exactly what runs where, what runs in parallel, and how components depend on each other.

### Full Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           load_study_node                                    │
│                     (loads study from Firestore)                             │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │                                       │
            ▼                                       ▼
┌───────────────────────────────┐     ┌─────────────────────────────────────┐
│    resource_extraction_node    │     │  analyze_rooms_node (BACKGROUND)     │
│                               │     │                                     │
│  • Download appraisal PDF     │     │  1. VISION: GPT-4o analyzes images  │
│  • Azure DI extracts fields   │     │     (2 workers, ~2-3 min)           │
│  • ~30 seconds                │     │     → Outputs: rooms[], objects[]   │
│                               │     │                                     │
│  PAUSE #1 ◄───────────────────┼─────│  2. ROOM ENRICHMENT (LLM):          │
│  (Engineer reviews appraisal) │     │     For each room, calls            │
│                               │     │     RoomContextAgent (dict lookups) │
│  Sets: appraisalApproved=true │     │                                     │
│  Waits for: roomsReady=true   │     │  Sets: roomsReady=true              │
└───────────────────────────────┘     └─────────────────────────────────────┘
            │                                       │
            └───────────────┬───────────────────────┘
                            │
                            ▼ (when BOTH approved AND roomsReady)
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PAUSE #2: reviewing_rooms                           │
│               (Engineer reviews rooms + objects on same page)                │
│                                                                              │
│   Frontend shows:                                                            │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  Room: Kitchen                                                       │   │
│   │  ├── Light Fixture (detected by vision)                              │   │
│   │  ├── Cabinet (detected by vision)                                    │   │
│   │  ├── Countertop (detected by vision)                                 │   │
│   │  └── HVAC Vent (detected by vision)                                  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   Objects are ONLY detected here - NO classification yet!                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                │ (Engineer approves)
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          process_assets_node                                 │
│                                                                              │
│  STEP 1: Object Enrichment (SEQUENTIAL)                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  For each object:                                                    │    │
│  │    ObjectContextAgent (LLM) calls dict lookup tools                  │    │
│  │    "light_fixture" → {"section": "1245", "recovery": "5-year"}       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                │                                             │
│                                ▼                                             │
│  STEP 2 & 3: Takeoff + Classification (PARALLEL via asyncio.gather)         │
│  ┌────────────────────────────┐    ┌────────────────────────────────────┐   │
│  │  calculate_takeoffs_batch  │    │  classify_components_batch         │   │
│  │                            │    │                                    │   │
│  │  TakeoffAgent (LLM) does:  │    │  AssetClassificationAgent (LLM):   │   │
│  │  • Dict lookup for unit    │    │  • Searches IRS docs               │   │
│  │  • Basic math              │    │  • Determines Section 1245/1250    │   │
│  │  quantity=5, unit="EA"     │    │  • Determines MACRS bucket         │   │
│  └────────────────────────────┘    └────────────────────────────────────┘   │
│                                │                                             │
│                                ▼                                             │
│  STEP 3.5: Classification Verification (Python rules, no LLM)               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  verify_classifications_batch (use_agent=False)                      │    │
│  │  Pure Python: "1245 + 5-year = valid? ✓"                             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                │                                             │
│                                ▼                                             │
│  STEP 4: Cost Estimation (SEQUENTIAL after classification)                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  estimate_costs_batch                                                │    │
│  │                                                                      │    │
│  │  CostEstimationAgent (LLM) does:                                     │    │
│  │  • Dict lookup for unit costs                                        │    │
│  │  • Basic arithmetic                                                  │    │
│  │  carpet: 200 SF × $6.80/SF × 1.15 (CA factor) = $1,564              │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                │                                             │
│                                ▼                                             │
│  STEP 5: Cross-Validation (Python rules, no LLM)                            │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  CrossValidator.validate_all()                                       │    │
│  │  "Section 1245 with 39-year? ❌ Invalid"                             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PAUSE #3: engineering_takeoff                             │
│         (Engineer reviews classifications, takeoffs, costs)                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                           completed
```

### What Runs in Parallel vs Sequential

| Stage | Runs In | Depends On |
|-------|---------|------------|
| `resource_extraction` | Backend (sync) | Nothing |
| `analyze_rooms` | Background worker | Nothing |
| Room Enrichment | Inside analyze_rooms | Vision complete |
| **Object Enrichment** | process_assets (sequential) | Rooms approved |
| **Takeoff** | process_assets (parallel) | Object Enrichment |
| **Classification** | process_assets (parallel) | Object Enrichment |
| Classification Verify | process_assets (sequential) | Classification |
| **Cost Estimation** | process_assets (sequential) | Takeoff complete |
| Cross-Validation | process_assets (sequential) | All above |

### Key Insight: Where LLMs Are Called

```
analyze_rooms_node:
├── Vision (GPT-4o)        → 52 images × 1 call = 52 LLM calls ✓ NECESSARY
└── Room Enrichment (LLM)  → N rooms × 1 call = N LLM calls    ❌ UNNECESSARY

process_assets_node:
├── Object Enrichment      → N objects × 1 call = N LLM calls  ❌ UNNECESSARY
├── Takeoff                → N objects × 1 call = N LLM calls  ❌ UNNECESSARY
├── Classification         → N objects × 1 call = N LLM calls  ⚠️ PARTIAL (only edge cases)
├── Classification Verify  → 0 LLM calls (Python rules)        ✓ ALREADY GOOD
└── Cost Estimation        → N objects × 1 call = N LLM calls  ❌ UNNECESSARY
```

---

## Over-Engineered Components

### 1. Appraisal Extraction Multi-Agent [FIXED]

**Status:** ✅ Already simplified

**What it was:**
```
PDF → Azure DI → Extractor Agent (LLM) → Verifier Agent (LLM) → Corrector Agent (LLM) → Result
                      │                        │                       │
                      ▼                        ▼                       ▼
                 "Extract fields"      "Verify accuracy"      "Fix errors"
```

**Why it was wrong:**
- Azure DI is 95-99% accurate on structured forms
- Adding LLMs to "verify" introduced hallucinations
- 3 LLM calls added ~30 seconds and ~$0.05 per document
- The "corrections" often made things worse

**What we changed:**
```python
# backEnd/agentic/graph/nodes.py (lines 372-428)
SKIP_AGENTIC_EXTRACTION = True  # Skip multi-agent LLM loop
USE_AZURE_DI = True  # Use Azure Document Intelligence directly

if USE_AZURE_DI:
    azure_extractor = AzureDocumentExtractor()
    if azure_extractor.is_available():
        azure_result = await azure_extractor.extract(str(pdf_path))
        sections = _azure_di_to_sections(azure_result, fields_dict)
```

**Result:**
- Extraction time: 30s → 5s
- Accuracy: Same or better (no hallucinations)
- Cost: $0.05 → $0.01

---

### 2. Room Enrichment

**Status:** ❌ Still over-engineered

**Location:** `backEnd/agentic/agents/room_agent.py`

**Current flow:**
```
Detected Room ("Kitchen")
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1: LLM Call #1 - Generate Search Queries                  │
│  "What IRS guidance applies to kitchens in residential?"        │
│  Time: ~3 seconds                                               │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 2: RAG Search (BM25 + FAISS)                              │
│  Search IRS publications for kitchen-related guidance           │
│  Time: ~1 second                                                │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 3: LLM Call #2 - Synthesize and Enrich                    │
│  "Based on these documents, what components are in a kitchen?"  │
│  Time: ~5 seconds                                               │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
Enriched Room (with components, IRS context, etc.)
Total: ~10 seconds per room, 2 LLM calls
```

**Why it's wrong:**

A kitchen is a kitchen. The components in a residential kitchen are well-known and don't change:
- Cabinets
- Countertops
- Flooring
- Lighting fixtures
- Appliances (if included)
- Plumbing fixtures (sink, disposal)

**You don't need an LLM to tell you this.** It's a static mapping.

**The fix:**

```python
# backEnd/agentic/agents/room_agent.py - REPLACE with static mapping

ROOM_COMPONENT_MAPPING = {
    "kitchen": {
        "components": [
            {"name": "cabinets", "category": "fixtures", "typical_macrs": "5-year"},
            {"name": "countertops", "category": "fixtures", "typical_macrs": "5-year"},
            {"name": "flooring", "category": "flooring", "typical_macrs": "5-year"},
            {"name": "lighting", "category": "electrical", "typical_macrs": "5-year"},
            {"name": "sink", "category": "plumbing", "typical_macrs": "5-year"},
            {"name": "appliances", "category": "equipment", "typical_macrs": "5-year"},
        ],
        "irs_context": "Kitchen components are generally 5-year property under MACRS as they are not structural components (Rev. Proc. 87-56).",
    },
    "bathroom": {
        "components": [
            {"name": "toilet", "category": "plumbing", "typical_macrs": "5-year"},
            {"name": "sink/vanity", "category": "plumbing", "typical_macrs": "5-year"},
            {"name": "tub/shower", "category": "plumbing", "typical_macrs": "5-year"},
            {"name": "flooring", "category": "flooring", "typical_macrs": "5-year"},
            {"name": "lighting", "category": "electrical", "typical_macrs": "5-year"},
            {"name": "mirror", "category": "fixtures", "typical_macrs": "5-year"},
        ],
        "irs_context": "Bathroom fixtures are 5-year property as they are not structural (Rev. Proc. 87-56).",
    },
    "bedroom": {
        "components": [
            {"name": "flooring", "category": "flooring", "typical_macrs": "5-year"},
            {"name": "lighting", "category": "electrical", "typical_macrs": "5-year"},
            {"name": "closet_doors", "category": "fixtures", "typical_macrs": "5-year"},
        ],
        "irs_context": "Bedroom components like carpet are 5-year property (Rev. Proc. 87-56).",
    },
    "living_room": {
        "components": [
            {"name": "flooring", "category": "flooring", "typical_macrs": "5-year"},
            {"name": "lighting", "category": "electrical", "typical_macrs": "5-year"},
            {"name": "fireplace", "category": "fixtures", "typical_macrs": "5-year"},
        ],
        "irs_context": "Living room decorative elements are generally 5-year property.",
    },
    "garage": {
        "components": [
            {"name": "garage_door", "category": "fixtures", "typical_macrs": "5-year"},
            {"name": "flooring", "category": "flooring", "typical_macrs": "15-year"},
            {"name": "lighting", "category": "electrical", "typical_macrs": "5-year"},
        ],
        "irs_context": "Garage doors are 5-year property; concrete flooring may be 15-year land improvement.",
    },
    # Add more room types...
}

def enrich_room(room_type: str, detected_objects: list[str]) -> dict:
    """
    Enrich a room with components based on static mapping.

    No LLM needed - this is a lookup operation.

    Args:
        room_type: Detected room type (e.g., "kitchen")
        detected_objects: Objects detected in photos

    Returns:
        Enriched room data with components and IRS context
    """
    # Normalize room type
    room_key = room_type.lower().replace(" ", "_")

    # Get mapping (default to generic room if not found)
    mapping = ROOM_COMPONENT_MAPPING.get(room_key, {
        "components": [
            {"name": "flooring", "category": "flooring", "typical_macrs": "5-year"},
            {"name": "lighting", "category": "electrical", "typical_macrs": "5-year"},
        ],
        "irs_context": "Components should be evaluated individually for MACRS classification.",
    })

    # Merge detected objects with standard components
    components = mapping["components"].copy()
    standard_names = {c["name"] for c in components}

    for obj in detected_objects:
        obj_lower = obj.lower()
        if obj_lower not in standard_names:
            components.append({
                "name": obj_lower,
                "category": "detected",
                "typical_macrs": "needs_classification",
            })

    return {
        "room_type": room_type,
        "components": components,
        "irs_context": mapping["irs_context"],
        "enrichment_method": "static_mapping",  # For audit trail
    }
```

**Expected improvement:**
- Time: 10 seconds/room → 0.01 seconds/room
- LLM calls: 2 per room → 0 per room
- For 4 rooms: 40 seconds → 0.04 seconds

---

### 3. Object Enrichment

**Status:** ❌ Still over-engineered

**Location:** `backEnd/agentic/agents/object_agent.py`

**Current flow:**
```
Detected Object ("toilet")
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  LLM Call: "What is a toilet? What IRS category?"               │
│  Time: ~5 seconds                                               │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
Enriched Object (with description, category, etc.)
```

**Why it's wrong:**

A toilet is a toilet. You don't need GPT-4 to tell you:
- It's a plumbing fixture
- It's personal property (not structural)
- It's typically 5-year MACRS

**The fix:**

```python
# backEnd/agentic/agents/object_agent.py - REPLACE with static mapping

OBJECT_ATTRIBUTES = {
    # Plumbing
    "toilet": {
        "category": "plumbing_fixture",
        "description": "Sanitary plumbing fixture",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "sink": {
        "category": "plumbing_fixture",
        "description": "Plumbing fixture for water delivery",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "bathtub": {
        "category": "plumbing_fixture",
        "description": "Bathing fixture",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "shower": {
        "category": "plumbing_fixture",
        "description": "Bathing fixture",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },

    # Flooring
    "carpet": {
        "category": "flooring",
        "description": "Floor covering - carpet",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "hardwood_floor": {
        "category": "flooring",
        "description": "Floor covering - hardwood",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "tile_floor": {
        "category": "flooring",
        "description": "Floor covering - tile",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },

    # Kitchen
    "cabinets": {
        "category": "cabinetry",
        "description": "Storage cabinetry",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "countertop": {
        "category": "fixtures",
        "description": "Counter surface",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "appliance": {
        "category": "equipment",
        "description": "Kitchen/laundry appliance",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },

    # Electrical
    "light_fixture": {
        "category": "electrical",
        "description": "Lighting fixture",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },
    "ceiling_fan": {
        "category": "electrical",
        "description": "Ceiling-mounted fan with lighting",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },

    # HVAC
    "hvac_unit": {
        "category": "hvac",
        "description": "Heating/cooling equipment",
        "typical_macrs": "5-year",
        "irs_section": "1245",
        "is_structural": False,
    },

    # Structural (27.5-year)
    "window": {
        "category": "structural",
        "description": "Window unit",
        "typical_macrs": "27.5-year",
        "irs_section": "1250",
        "is_structural": True,
    },
    "door": {
        "category": "structural",
        "description": "Door unit",
        "typical_macrs": "27.5-year",
        "irs_section": "1250",
        "is_structural": True,
    },
    "roof": {
        "category": "structural",
        "description": "Roofing system",
        "typical_macrs": "27.5-year",
        "irs_section": "1250",
        "is_structural": True,
    },

    # Land Improvements (15-year)
    "fence": {
        "category": "land_improvement",
        "description": "Fencing",
        "typical_macrs": "15-year",
        "irs_section": "1250",
        "is_structural": False,
    },
    "driveway": {
        "category": "land_improvement",
        "description": "Paved driveway",
        "typical_macrs": "15-year",
        "irs_section": "1250",
        "is_structural": False,
    },
    "sidewalk": {
        "category": "land_improvement",
        "description": "Paved walkway",
        "typical_macrs": "15-year",
        "irs_section": "1250",
        "is_structural": False,
    },
}

def enrich_object(object_label: str) -> dict:
    """
    Enrich an object with attributes based on static mapping.

    No LLM needed - this is a lookup operation.
    Falls back to 'unknown' category if not in mapping.

    Args:
        object_label: Detected object label (e.g., "toilet")

    Returns:
        Object attributes dict
    """
    # Normalize label
    label_key = object_label.lower().replace(" ", "_")

    # Try exact match first
    if label_key in OBJECT_ATTRIBUTES:
        return {**OBJECT_ATTRIBUTES[label_key], "label": object_label}

    # Try fuzzy match (contains)
    for key, attrs in OBJECT_ATTRIBUTES.items():
        if key in label_key or label_key in key:
            return {**attrs, "label": object_label, "matched_key": key}

    # Unknown object - flag for LLM classification
    return {
        "label": object_label,
        "category": "unknown",
        "description": f"Unrecognized object: {object_label}",
        "typical_macrs": "needs_classification",
        "irs_section": "unknown",
        "is_structural": None,
        "needs_llm_classification": True,  # Flag for downstream
    }
```

**Key insight:** Only objects marked `needs_llm_classification: True` should go through the LLM classification agent. Most common objects can be handled with a lookup.

**Expected improvement:**
- Time: 5 seconds/object → 0.001 seconds/object (for known objects)
- Only ~10-20% of objects will need LLM classification

---

### 4. Takeoff Agent

**Status:** ❌ Still over-engineered

**Location:** `backEnd/agentic/agents/takeoff_agent.py`

**Current flow:**
```
Component: "light_fixture", count: 5
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  TakeoffAgent (LLM) calls tools:                                 │
│  ├── estimate_quantity_from_area() → Dict lookup + math          │
│  ├── lookup_unit_conversion()      → Dict lookup                 │
│  └── get_industry_installation_rates() → Dict lookup             │
│                                                                  │
│  Time: ~5 seconds per object                                     │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
Output: quantity=5, unit="EA", rsmeans_line_item="26 51 13.10"
```

**Why it's wrong:**

Quantity calculation is **pure math**:
- Light fixture: count = 5 → quantity = 5, unit = "EA"
- Carpet: room_area = 200 SF, waste_factor = 1.10 → quantity = 220, unit = "SF"

The LLM is doing basic arithmetic that a simple function can do.

**The tools are already dictionaries:**

```python
# In takeoff_agent.py - the "tools" are just dict lookups
QUANTITY_ESTIMATES = {
    "light_fixture": {"unit": "EA", "per_sf": 0.01, "min_per_room": 1},
    "carpet": {"unit": "SF", "per_sf": 1.0, "waste_factor": 1.10},
    "electrical_outlet": {"unit": "EA", "per_sf": 0.02, "min_per_room": 2},
}
```

**The fix:**

```python
# backEnd/agentic/agents/takeoff_agent.py - REPLACE with direct calculation

TAKEOFF_SPECS = {
    "light_fixture": {"unit": "EA", "method": "count"},
    "electrical_outlet": {"unit": "EA", "method": "count"},
    "hvac_unit": {"unit": "EA", "method": "count"},
    "carpet": {"unit": "SF", "method": "area", "waste_factor": 1.10},
    "tile": {"unit": "SF", "method": "area", "waste_factor": 1.15},
    "hardwood": {"unit": "SF", "method": "area", "waste_factor": 1.08},
    "baseboard": {"unit": "LF", "method": "perimeter"},
    "crown_molding": {"unit": "LF", "method": "perimeter"},
    "cabinet": {"unit": "LF", "method": "linear"},
}

def calculate_takeoff(
    component: str,
    detection_count: int,
    room_area_sf: float = None,
) -> dict:
    """
    Calculate takeoff quantity using direct math.

    No LLM needed - this is arithmetic.
    """
    normalized = component.lower().replace(" ", "_")
    spec = TAKEOFF_SPECS.get(normalized, {"unit": "EA", "method": "count"})

    method = spec["method"]
    unit = spec["unit"]

    if method == "count":
        quantity = detection_count
    elif method == "area" and room_area_sf:
        waste_factor = spec.get("waste_factor", 1.0)
        quantity = room_area_sf * waste_factor
    elif method == "perimeter" and room_area_sf:
        # Estimate perimeter from area (assumes square-ish room)
        side = room_area_sf ** 0.5
        quantity = 4 * side
    elif method == "linear":
        # For cabinets, estimate from room area
        quantity = (room_area_sf ** 0.5) * 0.6 if room_area_sf else detection_count * 3
    else:
        quantity = detection_count

    return {
        "component_name": component,
        "quantity": round(quantity, 2),
        "unit": unit,
        "measurement_method": method,
        "calculation_note": f"Calculated via {method} method",
    }
```

**Expected improvement:**
- Time: 5 seconds/object → 0.001 seconds/object
- LLM calls: 1 per object → 0 per object

---

### 5. Cost Agent

**Status:** ❌ Still over-engineered

**Location:** `backEnd/agentic/agents/cost_agent.py`

**Current flow:**
```
Component: "carpet", quantity: 200, unit: "SF"
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  CostEstimationAgent (LLM) calls tools:                          │
│  ├── search_rsmeans_database("carpet", "standard")               │
│  │   → Returns from TYPICAL_UNIT_COSTS dict                      │
│  ├── get_regional_cost_factor("CA", 2024)                        │
│  │   → Returns from REGIONAL_COST_FACTORS dict                   │
│  └── calculate_material_labor_split("carpet")                    │
│      → Returns from MATERIAL_LABOR_SPLIT dict                    │
│                                                                  │
│  Then LLM does: 4.50 + 2.00 + 0.30 = $6.80/SF                   │
│  Time: ~5 seconds per object                                     │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
Output: final_cost = 200 × $6.80 × 1.15 = $1,564
```

**Why it's wrong:**

**All the cost data is already in dictionaries** (cost_agent.py lines 72-109):

```python
TYPICAL_UNIT_COSTS = {
    "carpet": {
        "standard": {"material": 4.50, "labor": 2.00, "equipment": 0.30, "unit": "SF"},
    },
}

REGIONAL_COST_FACTORS = {
    "CA": 1.15, "TX": 0.88, "NY": 1.20, ...
}
```

The LLM is doing addition: `4.50 + 2.00 + 0.30 = $6.80`. That's arithmetic.

**The fix:**

```python
# backEnd/agentic/agents/cost_agent.py - REPLACE with direct calculation

def estimate_cost(
    component: str,
    quantity: float,
    unit: str,
    quality: str = "standard",
    state: str = "CA",
    year: int = 2024
) -> dict:
    """
    Calculate cost using direct lookup and math.

    No LLM needed - this is arithmetic.
    """
    normalized = component.lower().replace(" ", "_")

    # Get unit costs from dict
    component_costs = TYPICAL_UNIT_COSTS.get(normalized, {})
    tier_costs = component_costs.get(quality, component_costs.get("standard"))

    if not tier_costs:
        return {
            "component_name": component,
            "needs_review": True,
            "reason": "Component not in cost database",
        }

    # Calculate unit cost
    material = tier_costs.get("material", 0)
    labor = tier_costs.get("labor", 0)
    equipment = tier_costs.get("equipment", 0)
    unit_cost = material + labor + equipment

    # Apply adjustments
    location_factor = REGIONAL_COST_FACTORS.get(state.upper(), 1.0)
    year_factor = YEAR_ADJUSTMENT_FACTORS.get(year, 1.15)

    # Calculate final cost
    base_cost = quantity * unit_cost
    adjusted_cost = base_cost * location_factor * year_factor

    return {
        "component_name": component,
        "quantity": quantity,
        "unit": unit,
        "material_cost_per_unit": material,
        "labor_cost_per_unit": labor,
        "equipment_cost_per_unit": equipment,
        "total_cost_per_unit": unit_cost,
        "base_extended_cost": base_cost,
        "location_factor": location_factor,
        "year_factor": year_factor,
        "final_cost": round(adjusted_cost, 2),
        "rsmeans_note": f"RSMeans 2020 base, {quality} tier",
    }
```

**Expected improvement:**
- Time: 5 seconds/object → 0.001 seconds/object
- LLM calls: 1 per object → 0 per object

---

### 6. Classification Verification

**Status:** ❌ Still over-engineered

**Location:** `backEnd/agentic/agents/classification_verifier.py`

**Current flow:**
```
Classification Result (e.g., "carpet → 5-year")
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  LLM Call: "Is this classification correct? Is it defensible?" │
│  Time: ~5 seconds per object                                    │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
Verification Result (is_valid, confidence, suggestions)
```

**Why it's wrong:**

1. **LLM verifying LLM doesn't add reliability** - You're using the same type of model to check itself
2. **Most classifications are clear-cut** - Carpet is 5-year. Always. No need to verify.
3. **Edge cases need human review, not another LLM** - If it's ambiguous, flag for engineer

**The fix:**

```python
# backEnd/agentic/agents/classification_verifier.py - REPLACE with rule-based

CLASSIFICATION_RULES = {
    # Hard rules - these are always true
    "carpet": {"valid_classes": ["5-year"], "reason": "Carpet is always 5-year property per Rev. Proc. 87-56"},
    "cabinets": {"valid_classes": ["5-year"], "reason": "Cabinetry is 5-year property"},
    "appliances": {"valid_classes": ["5-year"], "reason": "Appliances are 5-year property"},
    "toilet": {"valid_classes": ["5-year"], "reason": "Plumbing fixtures are 5-year property"},
    "sink": {"valid_classes": ["5-year"], "reason": "Plumbing fixtures are 5-year property"},
    "light_fixture": {"valid_classes": ["5-year"], "reason": "Lighting fixtures are 5-year property"},
    "hvac": {"valid_classes": ["5-year"], "reason": "HVAC equipment is 5-year property"},

    # Structural - always 27.5-year
    "roof": {"valid_classes": ["27.5-year"], "reason": "Roofing is structural, 27.5-year property"},
    "foundation": {"valid_classes": ["27.5-year"], "reason": "Foundation is structural"},
    "exterior_walls": {"valid_classes": ["27.5-year"], "reason": "Exterior walls are structural"},

    # Land improvements - always 15-year
    "fence": {"valid_classes": ["15-year"], "reason": "Fencing is 15-year land improvement"},
    "driveway": {"valid_classes": ["15-year"], "reason": "Paving is 15-year land improvement"},
    "landscaping": {"valid_classes": ["15-year"], "reason": "Landscaping is 15-year land improvement"},
}

# Ambiguous items that need human review
AMBIGUOUS_ITEMS = {
    "flooring": "Flooring classification depends on type (carpet=5yr, concrete=15yr/27.5yr)",
    "doors": "Interior doors may be 5-year, exterior doors are typically 27.5-year",
    "windows": "Windows may be 5-year (decorative) or 27.5-year (structural) depending on context",
}

def verify_classification(component: str, assigned_class: str) -> dict:
    """
    Verify a classification using rule-based logic.

    No LLM needed - this is rule checking.

    Args:
        component: Component name
        assigned_class: Assigned MACRS class (e.g., "5-year")

    Returns:
        Verification result
    """
    component_lower = component.lower()

    # Check hard rules
    for key, rule in CLASSIFICATION_RULES.items():
        if key in component_lower:
            is_valid = assigned_class in rule["valid_classes"]
            return {
                "is_valid": is_valid,
                "confidence": 1.0 if is_valid else 0.0,
                "rule_applied": rule["reason"],
                "needs_review": not is_valid,
                "review_reason": None if is_valid else f"Expected {rule['valid_classes']}, got {assigned_class}",
            }

    # Check ambiguous items
    for key, reason in AMBIGUOUS_ITEMS.items():
        if key in component_lower:
            return {
                "is_valid": True,  # Accept but flag
                "confidence": 0.7,
                "rule_applied": None,
                "needs_review": True,
                "review_reason": reason,
            }

    # Unknown item - accept but flag for review
    return {
        "is_valid": True,
        "confidence": 0.5,
        "rule_applied": None,
        "needs_review": True,
        "review_reason": f"No rule found for '{component}' - manual review recommended",
    }
```

**Expected improvement:**
- Time: 5 seconds/object → 0.001 seconds/object
- LLM calls: 1 per object → 0 per object
- More consistent (rules don't hallucinate)

---

### 7. Correction Cascade System

**Status:** ❌ Over-engineered for MVP

**Location:** `backEnd/agentic/validation/correction_cascade.py`

**Current design:**
```
Engineer makes correction
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  CorrectionCascade System:                                      │
│  1. Parse correction type                                       │
│  2. Build dependency graph                                      │
│  3. Identify affected downstream stages                         │
│  4. Mark affected data as "stale"                              │
│  5. Enqueue recalculation jobs                                 │
│  6. Track cascade propagation                                  │
│  7. Update audit trail                                         │
└─────────────────────────────────────────────────────────────────┘
```

**Why it's over-engineered:**

For an MVP, if the engineer changes something, just re-run the affected stages. You don't need a complex dependency graph.

**The fix:**

```python
# Simple correction handling - replace complex cascade

async def handle_correction(study_id: str, correction_type: str, correction_data: dict):
    """
    Handle engineer correction by re-running affected stages.

    Simple approach: re-run from the corrected stage forward.
    """
    if correction_type == "room_type":
        # Re-enrich the room, re-classify its objects
        room_id = correction_data["room_id"]
        await re_enrich_room(study_id, room_id)
        await re_classify_room_objects(study_id, room_id)

    elif correction_type == "classification":
        # Just update the classification, re-calculate costs
        object_id = correction_data["object_id"]
        new_class = correction_data["new_class"]
        await update_classification(study_id, object_id, new_class)
        await recalculate_costs(study_id, [object_id])

    elif correction_type == "takeoff":
        # Update takeoff, re-calculate costs
        object_id = correction_data["object_id"]
        new_quantity = correction_data["new_quantity"]
        await update_takeoff(study_id, object_id, new_quantity)
        await recalculate_costs(study_id, [object_id])
```

---

### 8. Evidence Pack System

**Status:** ❌ Over-engineered for MVP

**Location:** `backEnd/agentic/agents/evidence_aggregator.py`

**Current design:**
```
Each agent decision
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  Evidence Aggregator:                                           │
│  1. Capture citation with bounding box                         │
│  2. Calculate confidence score                                 │
│  3. Track source document lineage                              │
│  4. Aggregate across stages                                    │
│  5. Generate evidence pack with summary                        │
│  6. Calculate overall defensibility score                      │
└─────────────────────────────────────────────────────────────────┘
```

**Why it's over-engineered:**

For IRS defensibility, you just need to know: "This classification came from [document], page [X]"

**The fix:**

```python
# Simple citation tracking

def add_citation(component_id: str, classification: str, source: dict):
    """
    Add a simple citation for a classification decision.
    """
    return {
        "component_id": component_id,
        "classification": classification,
        "source_doc": source.get("doc_id"),
        "source_page": source.get("page"),
        "source_text": source.get("text", "")[:500],  # Truncate long text
        "timestamp": datetime.utcnow().isoformat(),
    }
```

---

## Components That ARE Appropriately Designed

### 1. Vision Analysis (GPT-4o)

**Why it's appropriate:**
- Requires visual understanding (what objects are in this photo?)
- No static mapping can identify objects in arbitrary photos
- LLM vision models are the right tool for this

**Keep as-is:** `backEnd/agentic/agents/vision_agent.py`

### 2. Asset Classification (for edge cases)

**Why it's appropriate:**
- Some components genuinely require IRS code interpretation
- Edge cases need reasoning over tax law
- RAG over IRS publications adds real value

**Modification:** Only use for objects marked `needs_llm_classification: True`

### 3. Ingestion Pipeline (BM25 + FAISS)

**Why it's appropriate:**
- Indexing is deterministic and necessary for RAG
- No LLM involved in indexing
- Enables fast retrieval for classification

**Keep as-is:** `backEnd/evidence_layer/src/ingest.py`

### 4. Cross-Validation (Rule-based)

**Why it's appropriate:**
- Already rule-based, not LLM-based
- Checks consistency between stages
- Fast and reliable

**Keep as-is:** `backEnd/agentic/validation/cross_validator.py`

---

## LLM Call Analysis

### Current LLM Calls Per Study

For a typical study with **52 images** and **100 detected objects**:

| Stage | LLM Calls | Cost per Call | Time per Call | Total Time |
|-------|-----------|---------------|---------------|------------|
| Vision (GPT-4o) | 52 | ~$0.02 | ~3s | ~156s |
| Room Enrichment | ~10 | ~$0.01 | ~3s | ~30s |
| Object Enrichment | 100 | ~$0.01 | ~2s | ~200s |
| Takeoff | 100 | ~$0.01 | ~2s | ~200s |
| Asset Classification | 100 | ~$0.01 | ~3s | ~300s |
| Cost Estimation | 100 | ~$0.01 | ~2s | ~200s |
| **TOTAL** | **~462** | **~$5.62** | - | **~1086s (~18 min)** |

### After Optimization

| Stage | LLM Calls | Notes |
|-------|-----------|-------|
| Vision (GPT-4o) | 52 | **Keep** - necessary for object detection |
| Room Enrichment | 0 | Static mapping |
| Object Enrichment | 0 | Static mapping + fuzzy match |
| Takeoff | 0 | Direct calculation |
| Asset Classification | ~20 | Only for unknown/edge-case components |
| Cost Estimation | 0 | Direct calculation |
| **TOTAL** | **~72** | **84% reduction** |

### Cost/Time Savings

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| LLM Calls | 462 | 72 | **390 fewer calls** |
| API Cost | ~$5.62 | ~$1.24 | **$4.38 saved (78%)** |
| Processing Time | ~18 min | ~3 min | **15 min saved (83%)** |

### OpenAI Rate Limit Considerations

Currently, vision analysis uses **2 concurrent workers** because 3+ triggers rate limits:

```python
# In nodes.py
analyzed_rooms, analyzed_objects = await analyze_study_images(
    uploaded_files=image_files,
    property_name=property_name,
    max_concurrent=2,  # 3 hits rate limits
)
```

After optimization, you could potentially increase concurrency for vision since other LLM calls are eliminated, but 2 workers is likely still optimal for avoiding 429 errors.

---

## Fuzzy Matching with Embeddings

### The Problem

You want fuzzy matching like "sofa" → "couch" without maintaining a huge synonym dictionary.

### Option 1: Synonym Dictionary (Simple, Recommended)

For cost segregation, the universe of components is **limited** (~50-100 building components). A synonym dict is manageable:

```python
COMPONENT_SYNONYMS = {
    # Furniture
    "sofa": "couch", "couch": "couch", "loveseat": "couch", "settee": "couch",

    # Appliances
    "fridge": "refrigerator", "refrigerator": "refrigerator", "icebox": "refrigerator",

    # HVAC
    "ac": "hvac_unit", "air conditioner": "hvac_unit", "a/c": "hvac_unit",
    "furnace": "hvac_unit", "heater": "hvac_unit",

    # Lighting
    "light": "light_fixture", "lamp": "light_fixture", "chandelier": "light_fixture",

    # ... ~100 total mappings covers 99% of cases
}

def normalize_component(label: str) -> str:
    return COMPONENT_SYNONYMS.get(label.lower(), label.lower())
```

### Option 2: Fuzzy String Matching (For Typos)

Use `rapidfuzz` for handling typos:

```python
from rapidfuzz import process, fuzz

KNOWN_COMPONENTS = list(COMPONENT_STANDARDS.keys())

def find_component(label: str, threshold: int = 80) -> str | None:
    match, score, _ = process.extractOne(
        label.lower(),
        KNOWN_COMPONENTS,
        scorer=fuzz.ratio
    )
    return match if score >= threshold else None

# Examples:
# "refridgerator" (typo) → "refrigerator" (score: 92)
# "sopha" → "sofa" (score: 80)
```

### Option 3: Embeddings (Most Flexible)

Pre-compute embeddings once, then do vector similarity at runtime:

```python
from sentence_transformers import SentenceTransformer
import numpy as np

# ONE-TIME SETUP (run once, save to file)
model = SentenceTransformer('all-MiniLM-L6-v2')  # Small, fast, ~80MB
KNOWN_COMPONENTS = ["hvac unit", "air conditioner", "carpet", "light fixture", ...]
EMBEDDINGS = model.encode(KNOWN_COMPONENTS)
np.save("component_embeddings.npy", EMBEDDINGS)

# AT RUNTIME (no LLM, no API call, runs locally)
EMBEDDINGS = np.load("component_embeddings.npy")

def find_similar_component(label: str, threshold: float = 0.7) -> str | None:
    query_embedding = model.encode([label])  # Local, ~10ms
    similarities = np.dot(EMBEDDINGS, query_embedding.T).flatten()
    best_idx = np.argmax(similarities)

    if similarities[best_idx] > threshold:
        return KNOWN_COMPONENTS[best_idx]
    return None

# Examples:
# "couch" → "sofa" (similarity: 0.89)
# "a/c unit" → "air conditioner" (similarity: 0.92)
# "random gibberish" → None (similarity: 0.23)
```

**Benefits:**
- Cost: $0 (runs locally)
- Time: ~10ms (no network)
- Maintenance: Zero (handles synonyms automatically)
- Deterministic: Yes (same input = same output)

**Recommendation:** Start with Option 1 (synonym dict) + Option 2 (fuzzy for typos). Only add embeddings if you find many unmatched components.

---

## Implementation Priority

### Phase 1: Highest Impact (2-4 hours)

These changes eliminate the most LLM calls with minimal code changes:

| Priority | Change | LLM Calls Eliminated | Time Saved | Effort |
|----------|--------|---------------------|------------|--------|
| 1 | **Object Enrichment → Static** | 100 calls | ~200s | Low |
| 2 | **Takeoff → Direct Math** | 100 calls | ~200s | Low |
| 3 | **Cost → Direct Calculation** | 100 calls | ~200s | Low |
| 4 | **Room Enrichment → Static** | 10 calls | ~30s | Low |

**Total Phase 1 Impact:**
- LLM calls eliminated: **310**
- Time saved per study: **~630 seconds (10.5 minutes)**
- API cost saved: **~$3.10 per study**

### Phase 2: Classification Optimization (4-8 hours)

| Change | LLM Calls Eliminated | Effort |
|--------|---------------------|--------|
| **Classification → Static table + LLM fallback** | ~80 calls | Medium |
| Add synonym dictionary | Reduces "unknown" rate | Low |
| Add fuzzy matching (rapidfuzz) | Handles typos | Low |

**Result:** Only ~20 LLM calls for truly unknown components.

### Phase 3: Code Simplification (Optional)

| Change | Benefit | Effort |
|--------|---------|--------|
| Simplify Correction Cascade | Less code, easier debugging | Medium |
| Simplify Evidence Pack | Less code, same functionality | Medium |
| Remove unused agent classes | Smaller codebase | Low |

### Phase 4: Future Optimization (Optional)

| Change | Benefit | Effort |
|--------|---------|--------|
| Use embeddings for component matching | Zero-maintenance synonyms | Medium |
| Batch remaining LLM calls | Reduce API round-trips | Medium |
| Cache common classifications | Faster repeat processing | Low |

---

## Detailed Fix Instructions

### Step 1: Simplify Object Enrichment (Highest Impact)

**File:** `backEnd/agentic/agents/object_agent.py`

1. Keep the existing `COMPONENT_STANDARDS` dict (it's already there)
2. Add a simple lookup function that bypasses the LLM agent:

```python
def enrich_object_static(label: str, room_type: str = None) -> dict:
    """
    Enrich object using static lookup. No LLM needed.
    """
    normalized = label.lower().replace(" ", "_")

    # Try exact match
    if normalized in COMPONENT_STANDARDS:
        return {**COMPONENT_STANDARDS[normalized], "label": label}

    # Try fuzzy match (contains)
    for key, attrs in COMPONENT_STANDARDS.items():
        if key in normalized or normalized in key:
            return {**attrs, "label": label, "matched_key": key}

    # Unknown - flag for LLM classification (only ~10-20% of objects)
    return {
        "label": label,
        "typical_section": "unknown",
        "typical_recovery": "needs_classification",
        "needs_llm_classification": True,
    }
```

3. Update `enrich_objects_batch` to use the static function:

```python
async def enrich_objects_batch(detections: list[dict], context, room_type: str = None) -> list[dict]:
    """Enrich objects using static lookup, only use LLM for unknowns."""
    results = []
    for detection in detections:
        label = detection.get("label", detection.get("original_label", "unknown"))
        enriched = enrich_object_static(label, room_type)
        results.append({**detection, "context": enriched})
    return results
```

### Step 2: Simplify Takeoff Calculation

**File:** `backEnd/agentic/agents/takeoff_agent.py`

1. Add direct calculation function:

```python
def calculate_takeoff_static(
    component: str,
    detection_count: int,
    room_area_sf: float = None,
) -> dict:
    """Calculate takeoff using direct math. No LLM needed."""
    normalized = component.lower().replace(" ", "_")
    spec = QUANTITY_ESTIMATES.get(normalized, {"unit": "EA", "per_sf": None})

    unit = spec["unit"]
    if unit == "EA":
        quantity = detection_count
    elif unit == "SF" and room_area_sf:
        quantity = room_area_sf * spec.get("waste_factor", 1.0)
    elif unit == "LF" and room_area_sf:
        quantity = 4 * (room_area_sf ** 0.5)  # Perimeter estimate
    else:
        quantity = detection_count

    return {
        "component_name": component,
        "quantity": round(quantity, 2),
        "unit": unit,
    }
```

2. Update `calculate_takeoffs_batch` to use the static function.

### Step 3: Simplify Cost Estimation

**File:** `backEnd/agentic/agents/cost_agent.py`

1. The dictionaries `TYPICAL_UNIT_COSTS`, `REGIONAL_COST_FACTORS`, etc. are already there
2. Add direct calculation function:

```python
def estimate_cost_static(
    component: str,
    quantity: float,
    unit: str,
    quality: str = "standard",
    state: str = "CA",
    year: int = 2024
) -> dict:
    """Calculate cost using direct math. No LLM needed."""
    normalized = component.lower().replace(" ", "_")
    costs = TYPICAL_UNIT_COSTS.get(normalized, {}).get(quality)

    if not costs:
        return {"component_name": component, "needs_review": True}

    unit_cost = costs["material"] + costs["labor"] + costs["equipment"]
    location_factor = REGIONAL_COST_FACTORS.get(state.upper(), 1.0)
    year_factor = YEAR_ADJUSTMENT_FACTORS.get(year, 1.15)

    final_cost = quantity * unit_cost * location_factor * year_factor

    return {
        "component_name": component,
        "quantity": quantity,
        "unit": unit,
        "total_cost_per_unit": unit_cost,
        "final_cost": round(final_cost, 2),
    }
```

3. Update `estimate_costs_batch` to use the static function.

### Step 4: Simplify Room Enrichment

**File:** `backEnd/agentic/agents/room_agent.py`

1. The `ROOM_IRS_GUIDANCE` and `ROOM_TYPICAL_COMPONENTS` dicts are already there
2. Add static lookup function:

```python
def enrich_room_static(room_type: str) -> dict:
    """Enrich room using static lookup. No LLM needed."""
    normalized = room_type.lower().replace(" ", "_")

    guidance = ROOM_IRS_GUIDANCE.get(normalized, ROOM_IRS_GUIDANCE.get("unknown", {}))
    components = ROOM_TYPICAL_COMPONENTS.get(normalized, [])

    return {
        "room_type": room_type,
        "irs_guidance": guidance,
        "typical_components": components,
    }
```

### Step 5: Update nodes.py to Use Static Functions

**File:** `backEnd/agentic/graph/nodes.py`

In `analyze_rooms_node`, replace the LLM batch call:

```python
# BEFORE (LLM-based)
enriched_rooms = await enrich_rooms_batch(rooms=rooms, context=context, max_concurrent=2)

# AFTER (static)
enriched_rooms = [
    {**room, "context": enrich_room_static(room.get("room_type", "unknown"))}
    for room in rooms
]
```

In `process_assets_node`, update similarly for objects, takeoffs, and costs.

### Step 6: Only Use LLM for Unknown Components

In `process_assets_node`, filter for LLM classification:

```python
# Separate known vs unknown components
known_objects = [obj for obj in enriched_objects if not obj.get("context", {}).get("needs_llm_classification")]
unknown_objects = [obj for obj in enriched_objects if obj.get("context", {}).get("needs_llm_classification")]

# Static classification for known (instant)
known_classifications = [classify_known_component(obj) for obj in known_objects]

# LLM classification only for unknown (~10-20% of objects)
if unknown_objects:
    unknown_classifications = await classify_components_batch(
        components=unknown_objects,
        context=context,
        max_concurrent=2,
    )
else:
    unknown_classifications = []

# Merge results
asset_classifications = known_classifications + unknown_classifications
```

---

## Summary

The system was designed with "LLMs everywhere" thinking, but the reality is:

```
┌─────────────────────────────────────────────────────────────────┐
│                    THE 80/15/5 RULE                              │
├─────────────────────────────────────────────────────────────────┤
│  80% Pattern Matching  →  Static mappings, lookup tables        │
│  15% Retrieval         →  BM25/FAISS (already working)          │
│   5% Reasoning         →  LLMs (only use here!)                 │
└─────────────────────────────────────────────────────────────────┘
```

**Current state:** LLMs doing dict lookups and arithmetic.

**After optimization:**
- LLM calls: 462 → 72 (**84% reduction**)
- Processing time: 18 min → 3 min (**83% reduction**)
- API cost: $5.62 → $1.24 (**78% reduction**)
- Reliability: More deterministic, fewer hallucinations

**The key insight: LLMs are for reasoning, not pattern matching.**

---

## Quick Reference: What Uses LLM vs What Shouldn't

| Component | Currently Uses LLM | Should Use LLM | Fix |
|-----------|-------------------|----------------|-----|
| Vision Analysis | Yes | **Yes** | Keep |
| Room Enrichment | Yes | **No** | Static mapping |
| Object Enrichment | Yes | **No** | Static mapping |
| Takeoff Calculation | Yes | **No** | Direct math |
| Asset Classification | Yes | **Partial** | Static + LLM fallback |
| Classification Verify | No (Python rules) | **No** | Keep |
| Cost Estimation | Yes | **No** | Direct math |
| Cross-Validation | No (Python rules) | **No** | Keep |
