# Basis Agentic Workflow - Implementation To-Do List

> **Generated:** 2024-01-19
> **Purpose:** Track all improvements needed for production-ready agentic workflow
> **Status:** Phase 1 Complete (Security Hardening) | Phase 2 Next

---

## Overview

This document tracks all implementation items identified during the comprehensive code review of the Basis agentic workflow system. Items are organized by category and priority.

**Total Items: 27**

---

## Category 1: Critical Security (Must Have Before Production)

| # | Item | Location | Why | Status |
|---|------|----------|-----|--------|
| 1 | **API Authentication** | `api/routes/workflow.py` | Anyone can call your API, access data, rack up LLM costs | [x] |
| 2 | **CORS Restriction** | `api/main.py:43-48` | Currently `allow_origins=["*"]` - any website can call you | [x] |
| 3 | **Error Message Sanitization** | `api/routes/workflow.py:121` | Internal errors leak to clients via `str(e)` | [x] |
| 4 | **Input Validation** | All API endpoints | Study IDs, doc IDs not validated for format/injection | [x] |
| 5 | **Rate Limiting** | API layer | No protection against abuse, can exhaust LLM quotas | [x] |

### Details

**#1 API Authentication**
- Add Firebase Auth token verification middleware
- Or implement API key authentication
- Verify user has access to requested study_id

**#2 CORS Restriction**
```python
# Current (INSECURE)
allow_origins=["*"]

# Should be
allow_origins=["https://yourdomain.com", "https://app.yourdomain.com"]
```

**#3 Error Message Sanitization**
```python
# Current (LEAKS INFO)
raise HTTPException(status_code=500, detail=str(e))

# Should be
logger.error(f"Workflow error: {e}", exc_info=True)
raise HTTPException(status_code=500, detail="Internal server error")
```

---

## Category 2: Agent Architecture Fixes

| # | Item | Location | Why | Status |
|---|------|----------|-----|--------|
| 6 | **Convert VisionAgent to real agent** | `agents/vision_agent.py` | Currently a function, not an agent - no ReAct loop, no retry, no tools | [ ] |
| 7 | **Add ClassificationVerifierAgent** | New file needed | No verification after asset classification - IRS defensibility gap | [ ] |
| 8 | **Fix room context mapping** | `nodes.py:505-509` | All objects get `rooms[0]` context instead of their actual room | [ ] |
| 9 | **Add domain-specific tools per agent** | `base_agent.py:146` | All agents have same generic search tools, no specialization | [ ] |
| 10 | **Add retry logic for LLM calls** | `base_agent.py` | No retry on transient failures (network, rate limits) | [ ] |

### Details

**#6 VisionAgent Rewrite**

Current state:
```python
async def analyze_image(...):
    model = get_vision_llm()
    response = await model.ainvoke(messages)  # Single call, no reasoning
    return parse_response(response)
```

Target state:
```python
class VisionAnalysisAgent(BaseStageAgent):
    def get_tools(self):
        return [
            analyze_full_image_tool,
            crop_and_analyze_tool,
            detect_text_regions_tool,
            retry_with_enhanced_prompt_tool,
        ]

    # ReAct loop can now:
    # - Retry if JSON parsing fails
    # - Crop and re-analyze specific regions
    # - Handle blurry/unclear images intelligently
```

**#7 ClassificationVerifierAgent**

New agent needed after AssetClassificationAgent:
- Verify section/bucket consistency (1245 shouldn't be 39-year)
- Cross-check with room context
- Validate citations actually support the classification
- Flag suspicious patterns

**#8 Room Context Fix**

Current (broken):
```python
default_room_type = rooms[0].get("room_type") if rooms else None
# ALL objects get this same room context
```

Should be:
```python
for obj in objects:
    room_id = obj.get("room_id")
    room = next((r for r in rooms if r["id"] == room_id), None)
    obj_room_context = room if room else rooms[0] if rooms else None
```

---

## Category 3: Tool-as-Agent Conversions

| # | Item | Current State | Target State | Status |
|---|------|---------------|--------------|--------|
| 11 | **Vision analysis tool** | Direct GPT-4V call, one-shot | Agent with crop/retry/re-analyze tools | [ ] |
| 12 | **Azure DI extraction tool** | One-shot extraction | Agent that re-extracts low-confidence pages | [ ] |
| 13 | **Classification tool** | Single agent call, no verification | Agent-tool that self-verifies before returning | [ ] |

### Details

**Tool-as-Agent Pattern:**
```python
@tool
async def classify_component_agent(component: str, context: dict) -> dict:
    """
    This tool spawns a specialist classification agent that:
    1. Searches IRS corpus for relevant guidance
    2. Determines section (1245 vs 1250)
    3. Assigns MACRS bucket
    4. VERIFIES the classification makes sense
    5. If uncertain, searches more and re-evaluates
    6. Returns classification with citations
    """
    agent = AssetClassificationAgent()
    result = await agent.run(...)  # Agent has its own ReAct loop
    return result.to_dict()
```

---

## Category 4: Workflow Improvements

| # | Item | Location | Why | Status |
|---|------|----------|-----|--------|
| 14 | **Replace background task with proper queue** | `nodes.py:416` | `asyncio.create_task()` is fire-and-forget, tasks lost on restart | [ ] |
| 15 | **Add checkpoint history** | `checkpointer.py:159` | Only stores latest state, can't debug what happened between pauses | [ ] |
| 16 | **Add cross-validation between stages** | Workflow | Takeoff results don't validate classification, no feedback loop | [ ] |
| 17 | **Configurable max iterations** | `base_agent.py:270` | Hardcoded to 5, should be in settings | [ ] |

### Details

**#14 Background Task Replacement**

Current (dangerous):
```python
asyncio.create_task(run_analyze_rooms_background())
# Fire and forget - lost on restart, no tracking
```

Options:
- Google Cloud Tasks
- Celery with Redis
- LangGraph's built-in async patterns

**#15 Checkpoint History**

Current: Only stores latest checkpoint per thread
Need: Store version history for debugging

```python
# Collection structure change
workflow_checkpoints/
  {thread_id}/
    current/        # Latest state
    history/        # Previous states with timestamps
      {checkpoint_id_1}/
      {checkpoint_id_2}/
```

---

## Category 5: Data Flow Fixes

| # | Item | Location | Why | Status |
|---|------|----------|-----|--------|
| 18 | **Classification validation rules** | `asset_agent.py` | Section 1245 with 39-year bucket is invalid, not caught | [ ] |
| 19 | **Bidirectional data flow** | `nodes.py` | If Takeoff says "not in RSMeans", classification doesn't know | [ ] |
| 20 | **Evidence aggregation** | Throughout | Citations spread across stages, no unified evidence pack view | [ ] |

### Details

**#18 Classification Validation Rules**

Add validation:
```python
VALID_COMBINATIONS = {
    "1245": ["5-year", "7-year", "15-year"],  # Personal property
    "1250": ["15-year", "27.5-year", "39-year"],  # Real property
}

def validate_classification(section: str, bucket: str) -> bool:
    return bucket in VALID_COMBINATIONS.get(section, [])
```

**#19 Bidirectional Data Flow**

Current: Linear pipeline, no feedback
```
Classification → Takeoff → Cost
     ↓              ↓        ↓
   (done)        (done)   (done)
```

Target: Feedback loops
```
Classification → Takeoff → Cost
     ↑              ↓        ↓
     └── "Component not in RSMeans, reconsider classification"
```

---

## Category 6: Code Quality & Robustness

| # | Item | Location | Why | Status |
|---|------|----------|-----|--------|
| 21 | **Fix vision JSON parsing** | `vision_agent.py:206-236` | Fragile regex parsing, fails silently to "unknown" | [ ] |
| 22 | **Standardize logging** | Throughout | Mix of `print()` and `logger.info()` | [ ] |
| 23 | **Remove dead code** | `edges.py:51-59` | `route_after_room_review()` defined but never used | [ ] |
| 24 | **Add integration tests** | New | No end-to-end workflow tests exist | [ ] |

### Details

**#21 Vision JSON Parsing Fix**

Current (fragile):
```python
json_match = re.search(r'\{[^{}]*"room_type".*?\}', response_text, re.DOTALL)
# If no match, returns room_type="unknown" with no retry
```

Target:
```python
# 1. Try structured output (if model supports)
# 2. Try JSON parsing with multiple patterns
# 3. If all fail, RETRY with clarified prompt
# 4. Only return "unknown" after N retries
```

**#22 Logging Standardization**

Files with `print()` that should use `logger`:
- `nodes.py:308, 334, 339, 365, 369, 404, 409, 417, 425`
- `vision_agent.py:63, 75`
- `extractor_agent.py` various

---

## Category 7: Observability & Operations

| # | Item | Location | Why | Status |
|---|------|----------|-----|--------|
| 25 | **Workflow failure alerts** | New | No notifications when workflows fail | [ ] |
| 26 | **LLM cost tracking** | New | No visibility into per-study LLM spend | [ ] |
| 27 | **Agent decision logging** | `base_agent.py` | Can't see why agent made specific choices | [ ] |

### Details

**#26 LLM Cost Tracking**

Track per call:
```python
{
    "study_id": "...",
    "agent": "AssetClassificationAgent",
    "model": "gpt-5-nano",
    "input_tokens": 1234,
    "output_tokens": 567,
    "estimated_cost_usd": 0.0023,
    "timestamp": "..."
}
```

Aggregate per study for billing/reporting.

---

## Priority Summary

| Priority | Description | Items | Count |
|----------|-------------|-------|-------|
| **P0** | Security (blocking for production) | ~~1, 2, 3, 4, 5~~ | 5 ✅ |
| **P1** | Core Architecture | 6, 7, 8, 9, 10, 11, 12, 13 | 8 |
| **P2** | Workflow Reliability | 14, 15, 16, 17 | 4 |
| **P3** | Data Correctness | 18, 19, 20 | 3 |
| **P4** | Code Quality | 21, 22, 23, 24 | 4 |
| **P5** | Operations | 25, 26, 27 | 3 |

---

## Quick Wins (< 1 hour each)

- [x] #2 CORS fix (~5 min) ✅ DONE
- [x] #3 Error sanitization (~15 min) ✅ DONE
- [ ] #8 Room context fix (~30 min)
- [ ] #17 Configurable max iterations (~15 min)
- [ ] #23 Remove dead code (~10 min)

---

## Big Efforts (Require Design)

- [ ] #6 VisionAgent rewrite (1-2 days)
- [ ] #7 ClassificationVerifierAgent - new agent (1 day)
- [ ] #11, 12, 13 Tool-as-agent conversions (2-3 days)
- [ ] #14 Task queue integration (1-2 days)
- [ ] #16 Cross-validation system (1-2 days)

---

## Implementation Order Recommendation

### Phase 1: Security Hardening ✅ COMPLETE
1. ~~#2 CORS fix~~ ✅
2. ~~#3 Error sanitization~~ ✅
3. ~~#1 API Authentication~~ ✅
4. ~~#4 Input validation~~ ✅
5. ~~#5 Rate limiting~~ ✅

### Phase 2: Quick Fixes
1. #8 Room context fix
2. #17 Configurable max iterations
3. #23 Remove dead code
4. #22 Standardize logging

### Phase 3: Core Architecture
1. #6 VisionAgent rewrite
2. #10 Add retry logic
3. #21 Fix vision JSON parsing
4. #7 ClassificationVerifierAgent

### Phase 4: Tool-as-Agent Pattern
1. #11 Vision tool-as-agent
2. #12 Azure DI tool-as-agent
3. #13 Classification tool-as-agent
4. #9 Domain-specific tools

### Phase 5: Workflow Reliability
1. #14 Task queue
2. #18 Classification validation
3. #16 Cross-validation
4. #19 Bidirectional data flow

### Phase 6: Polish
1. #15 Checkpoint history
2. #20 Evidence aggregation
3. #24 Integration tests
4. #25, 26, 27 Observability

---

## Notes

- This list was generated from a comprehensive code review
- Security items (P0) are blocking for any customer deployment
- Agent architecture items (P1) are critical for the "tool-as-agent" vision
- Delete this file once items are moved to proper issue tracker

---

## Files Referenced

| File | Issues |
|------|--------|
| `api/main.py` | #2 |
| `api/routes/workflow.py` | #1, #3, #4 |
| `agents/base_agent.py` | #9, #10, #17, #27 |
| `agents/vision_agent.py` | #6, #11, #21, #22 |
| `agents/asset_agent.py` | #13, #18 |
| `agents/appraisal/tools.py` | #12 |
| `graph/nodes.py` | #8, #14, #19, #22 |
| `graph/edges.py` | #23 |
| `firestore/checkpointer.py` | #15 |
