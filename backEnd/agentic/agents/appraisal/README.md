# Appraisal Extraction Agents

Multi-agent LangGraph system for intelligent appraisal data extraction with self-correction.

## Overview

This module implements **Agentic Tool Use** (not Agentic RAG) for appraisal PDF extraction. Three specialized agents work together in a LangGraph StateGraph to extract, verify, and correct appraisal data.

```
┌─────────────────────────────────────────────────────────────────┐
│  APPRAISAL EXTRACTION LANGGRAPH                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────┐                                   │
│  │    EXTRACTOR AGENT       │                                   │
│  │    "Extract intelligently │                                   │
│  │     using available tools"│                                   │
│  └───────────┬──────────────┘                                   │
│              │                                                  │
│              ▼                                                  │
│  ┌──────────────────────────┐                                   │
│  │    VERIFIER AGENT        │                                   │
│  │    "Be skeptical. Find   │                                   │
│  │     errors. Question     │                                   │
│  │     everything."         │                                   │
│  └───────────┬──────────────┘                                   │
│              │                                                  │
│   ┌──────────┼──────────┐                                       │
│   │          │          │                                       │
│   ▼          ▼          ▼                                       │
│ all_good  needs_corr  max_iter                                  │
│   │          │          │                                       │
│   ▼          ▼          ▼                                       │
│ [END]  ┌──────────┐  [END]                                      │
│        │CORRECTOR │                                             │
│        │  AGENT   │                                             │
│        │"Fix using│                                             │
│        │ DIFFERENT│                                             │
│        │ method"  │                                             │
│        └────┬─────┘                                             │
│             │                                                   │
│             └──────► back to verifier (max 2 iterations)        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Why Agentic Tool Use (Not Agentic RAG)?

| Agentic RAG | Agentic Tool Use (This Module) |
|-------------|--------------------------------|
| LLM searches document corpus | LLM extracts from single document |
| Retrieves relevant passages | Reads specific fields |
| Tools: `bm25_search`, `vector_search` | Tools: `extract_with_azure_di`, `extract_with_vision` |
| Used by: AssetAgent, RoomAgent, etc. | Used by: Appraisal Extraction |

Appraisal extraction doesn't need retrieval - it needs structured extraction from a single PDF with verification and self-correction.

## Agents

### 1. ExtractorAgent

**Role:** "Extract appraisal data intelligently using available tools"

**Strategy:**
1. Use FREE tools first (MISMO XML if available)
2. Use PAID tools for main extraction (Azure DI)
3. Use EXPENSIVE tools only for stubborn fields (Vision)

**Tools:**
| Tool | Cost | Confidence | When to Use |
|------|------|------------|-------------|
| `parse_mismo_xml` | FREE | 1.0 | If XML uploaded |
| `extract_with_azure_di` | $0.10-0.50 | 0.70-0.95 | Primary extraction |
| `extract_with_vision` | $0.10-0.20 | 0.60-0.90 | Handwritten/faded fields |

### 2. VerifierAgent

**Role:** "Be skeptical. Find errors. Question everything."

**Checks:**
- **Plausibility**: year_built 1800-2026, GLA 500-15000 sq ft
- **OCR errors**: 0↔O, 1↔I, digit transposition
- **Consistency**: GLA vs bedrooms, contract vs appraised value
- **Confidence**: Critical fields < 0.90 flagged

**Tools:**
| Tool | Cost | Purpose |
|------|------|---------|
| `validate_extraction` | FREE | Rule-based validation |
| `vision_recheck_field` | $0.05-0.10 | Visual verification of suspicious field |

### 3. CorrectorAgent

**Role:** "Fix flagged errors using DIFFERENT extraction method"

**Strategy:**
- If original source was `azure_di` → Use `vision`
- If original source was `vision` → Use `azure_di`
- Document what was wrong and how it was fixed

**Tools:** Same as ExtractorAgent, but MUST use different tool than original.

## Usage

### Production (via workflow)

```python
# In agentic/graph/nodes.py - automatically called
from agentic.agents.appraisal import run_appraisal_extraction

result = await run_appraisal_extraction(
    study_id="STUDY_001",
    pdf_path="/path/to/appraisal.pdf",
    context=stage_context,
    max_iterations=2,
)

# Result structure
{
    "extraction_result": {
        "subject": {"property_address": "123 Main St", ...},
        "improvements": {"year_built": 1995, "gross_living_area": 2450, ...},
        ...
    },
    "audit_trail": {
        "study_id": "STUDY_001",
        "iterations": 1,
        "agent_calls": [...],
        "field_history": [...],
    },
    "overall_confidence": 0.92,
    "needs_review": False,
}
```

### Direct Agent Usage (testing)

```python
from agentic.agents.appraisal import (
    ExtractorAgent,
    VerifierAgent,
    CorrectorAgent,
    ExtractorInput,
    VerifierInput,
)

# Run extractor
extractor = ExtractorAgent()
extractor_output = await extractor.run(ExtractorInput(
    pdf_path="/path/to/appraisal.pdf",
    mismo_xml=None,
))

# Run verifier
verifier = VerifierAgent()
verifier_output = await verifier.run(VerifierInput(
    sections=extractor_output.sections,
    field_confidences=extractor_output.field_confidences,
    field_sources=extractor_output.field_sources,
))

print(f"All plausible: {verifier_output.all_plausible}")
print(f"Suspicious fields: {verifier_output.suspicious_fields}")
```

## Schemas

### Input/Output Models

```python
# Extractor
class ExtractorInput(BaseModel):
    pdf_path: str
    mismo_xml: Optional[str]
    tables_path: Optional[str]

class ExtractorOutput(BaseModel):
    sections: Dict[str, Dict[str, Any]]
    field_confidences: Dict[str, Dict[str, float]]
    field_sources: Dict[str, Dict[str, str]]
    tools_invoked: List[str]

# Verifier
class SuspiciousField(BaseModel):
    field_key: str          # "improvements.year_built"
    current_value: Any
    issue_type: str         # "ocr_error", "implausible", "inconsistent"
    reasoning: str
    suggested_recheck_method: str

class VerifierOutput(BaseModel):
    all_plausible: bool
    suspicious_fields: List[SuspiciousField]
    recommend_correction: bool

# Corrector
class FieldCorrection(BaseModel):
    field_key: str
    old_value: Any
    new_value: Any
    correction_source: str
    correction_reasoning: str

class CorrectorOutput(BaseModel):
    corrections_made: List[FieldCorrection]
    updated_sections: Dict[str, Dict[str, Any]]
    correction_summary: str
```

## Audit Trail (IRS Defensibility)

Every extraction produces a complete audit trail:

```python
{
    "study_id": "STUDY_001",
    "started_at": "2024-01-15T10:30:00Z",
    "completed_at": "2024-01-15T10:30:45Z",
    "iterations": 1,
    "final_confidence": 0.92,
    "needs_review": False,
    "agent_calls": [
        {
            "agent_name": "ExtractorAgent",
            "timestamp": "2024-01-15T10:30:05Z",
            "tools_used": ["extract_with_azure_di"],
            "duration_ms": 12500,
        },
        {
            "agent_name": "VerifierAgent",
            "timestamp": "2024-01-15T10:30:18Z",
            "tools_used": ["validate_extraction"],
            "duration_ms": 2100,
        },
    ],
    "field_history": [
        {
            "field_key": "improvements.year_built",
            "timestamp": "2024-01-15T10:30:10Z",
            "action": "extracted",
            "value": "I995",
            "source": "azure_di",
            "confidence": 0.75,
        },
        {
            "field_key": "improvements.year_built",
            "timestamp": "2024-01-15T10:30:20Z",
            "action": "flagged",
            "value": "I995",
            "source": "verifier",
            "notes": "ocr_error: Looks like '1' misread as 'I'"
        },
        {
            "field_key": "improvements.year_built",
            "timestamp": "2024-01-15T10:30:35Z",
            "action": "corrected",
            "value": 1995,
            "source": "vision_recheck_field",
            "confidence": 0.95,
            "notes": "Was: I995. Vision confirmed year is 1995."
        },
    ]
}
```

## File Structure

```
appraisal/
├── __init__.py           # Module exports
├── README.md             # This file
├── schemas.py            # Pydantic models (Input/Output for each agent)
├── tools.py              # LangChain tool wrappers
│                         # - parse_mismo_xml
│                         # - extract_with_azure_di
│                         # - extract_with_vision
│                         # - validate_extraction
│                         # - vision_recheck_field
├── extractor_agent.py    # ExtractorAgent (ReAct loop)
├── verifier_agent.py     # VerifierAgent (skeptical checking)
├── corrector_agent.py    # CorrectorAgent (self-correction)
└── orchestrator.py       # LangGraph StateGraph coordination
```

## Configuration

All agents use `gpt-5-nano` (cheapest text model) via `llm_providers.py`:

```python
# In config/llm_providers.py
STAGE_MODELS = {
    "appraisal_extraction": "gpt-5-nano",
    "appraisal_verification": "gpt-5-nano",
    "appraisal_correction": "gpt-5-nano",
}
```

## Loop Protection

The system cannot run forever:

1. **Orchestrator level**: `max_iterations=2` (default)
2. **Route check**: `route_after_verification()` checks `iterations >= max_iterations`
3. **Agent level**: Each agent has internal `max_iterations` for ReAct loops (5, 3, 5)

```python
def route_after_verification(state):
    if state["all_plausible"]:
        return "all_good"           # → END
    if state["iterations"] >= state["max_iterations"]:
        return "max_iterations"     # → END
    return "needs_correction"       # → corrector
```

## Testing

```bash
# Run unit tests
pytest agentic/tests/test_appraisal_agents.py

# Test individual agent
python -c "
from agentic.agents.appraisal import ExtractorAgent, ExtractorInput
import asyncio

async def test():
    agent = ExtractorAgent()
    result = await agent.run(ExtractorInput(pdf_path='test.pdf'))
    print(result)

asyncio.run(test())
"
```
