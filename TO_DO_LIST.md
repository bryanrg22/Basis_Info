# Basis Agentic Workflow - Current Issues

> **Updated:** 2026-01-20
> **Session Focus:** Infrastructure debugging & Azure DI fix

---

## Critical Issues Found

### 1. Azure DI Not Extracting Key-Value Pairs (FIXED)

**Problem:** `prebuilt-layout` model returns 0 key-value pairs by default. The `azure_di_extractor.py` relied on `key_value_pairs` which didn't exist.

**Root Cause:** Microsoft deprecated `prebuilt-document` model. Now you must use `prebuilt-layout` with `features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS]` enabled.

**Fix Applied:**
```python
# In azure_di_extractor.py - _analyze_document()
poller = self.client.begin_analyze_document(
    model_id="prebuilt-layout",
    body=f,
    content_type="application/pdf",
    features=[self.DocumentAnalysisFeature.KEY_VALUE_PAIRS]  # NEW
)
```

**Status:** Fixed - now extracts 540 key-value pairs with high confidence

---

### 2. Background Job Worker Not Running (BLOCKING)

**Problem:** `analyze_rooms` is enqueued to `JobQueue` (Firestore) but no worker process exists to execute the jobs.

**Location:** `nodes.py:569-583`

```python
# Jobs get enqueued here...
job_id = await job_queue.enqueue(
    job_type="analyze_rooms",
    study_id=state["study_id"],
    ...
)
# ...but nothing picks them up!
```

**Result:** Workflow gets stuck at "Categorizing Rooms and Objects" because `analyze_rooms` never runs.

**Fix Options:**
1. Add a worker process to Docker that polls `JobQueue.claim_next_job()`
2. Revert to `asyncio.create_task()` for simpler fire-and-forget (loses durability)

**Status:** NOT FIXED - needs implementation

---

### 3. Docker Missing poppler-utils (FIXED)

**Problem:** Vision extraction failed with "Unable to get page count. Is poppler installed?"

**Fix Applied:** Added `poppler-utils` to Dockerfile

```dockerfile
RUN apt-get update && apt-get install -y \
    gcc \
    poppler-utils \  # NEW
    && rm -rf /var/lib/apt/lists/*
```

**Status:** Fixed

---

### 4. Firestore Checkpointer Missing Async Methods (FIXED)

**Problem:** LangGraph calls `aget_tuple()` (async) but `FirestoreCheckpointer` only had `get_tuple()` (sync).

**Error:** `NotImplementedError` from `langgraph/checkpoint/base/__init__.py`

**Fix Applied:** Added async wrapper methods:
- `aget_tuple()`
- `aput()`
- `alist()`

**Status:** Fixed

---

### 5. Python 3.14 Compatibility Issues

**Problem:** LangChain's Pydantic V1 doesn't support Python 3.14.

**Warning:** `Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater`

**Fix:** Using Docker with Python 3.12 (resolved via containerization)

**Status:** Mitigated via Docker

---

## Infrastructure Setup

### Start Backend
```bash
docker-compose up --build
```

### Start Frontend
```bash
cd frontend && npm run dev
```

### Test Azure DI Locally
```bash
python test.py
```

---

## Next Steps

1. **Implement JobQueue worker** - Critical for `analyze_rooms` to run
2. **Test full workflow** - Verify Azure DI extraction flows through to Firestore
3. **Check LangSmith traces** - Confirm agent execution paths

---

## Files Modified This Session

| File | Change |
|------|--------|
| `backEnd/Dockerfile` | Added poppler-utils, fixed Python 3.12 |
| `docker-compose.yml` | Created for containerized backend |
| `backEnd/agentic/firestore/checkpointer.py` | Added async methods |
| `backEnd/evidence_layer/src/tiered_extraction/azure_di_extractor.py` | Added KEY_VALUE_PAIRS feature |
| `test.py` | Updated to test Azure DI with features flag |
