"""Worker restarts: a new worker resumes the study; no activity's work is done twice.

BUILD_PLAN 6.3 ``test_restart_recovery`` and the W2 regression ("resuming at a stage runs
that stage's next step, never extraction again"): the worker is stopped between
activities, during a review wait, and in the middle of an activity; a new worker (fresh
cache, so it replays the history) finishes the run. ``activity_results`` holds each
idempotency key once and no output row is duplicated. (A SIGKILLed worker process is
covered by tests/failure/test_worker_kill.py.)
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import sample_appraisal_chunks
from sqlalchemy import func, select
from temporalio import activity
from temporalio.client import WorkflowHandle

from basis.activities.ingest_appraisal import IngestAppraisal
from basis.db.models import Chunk, Component, Extraction, PhotoRoom
from basis.db.repositories.activity_result import COMPLETED
from basis.db.session import session_scope
from basis.workflow.contracts import IngestAppraisalInput, IngestAppraisalOutput, SimulateFault
from basis.workflow.study_workflow import workflow_id_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# The worker that is stopped runs without a workflow cache, so the server never routes the
# next task or query to its (now dead) sticky queue. A real server falls back from a dead
# sticky queue after a timeout; the time-skipping test server does not for queries.
NO_STICKY: dict[str, Any] = {"max_cached_workflows": 0}


async def started_counts(handle: WorkflowHandle[Any, Any]) -> Counter[str]:
    """How many times each activity id was started, per the history (final attempts)."""
    history = await handle.fetch_history()
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    }
    return Counter(
        scheduled[e.activity_task_started_event_attributes.scheduled_event_id]
        for e in history.events
        if e.HasField("activity_task_started_event_attributes")
    )


async def assert_no_duplicates(harness: Harness, handle: WorkflowHandle[Any, Any]) -> None:
    workflow_id = handle.id
    results = await harness.activity_results(workflow_id)
    keys = [r.idempotency_key for r in results]
    assert len(keys) == len(set(keys))
    assert {r.status for r in results} == {COMPLETED}
    scheduled = await started_counts(handle)
    assert sorted(k.split(":", 2)[2] for k in keys) == sorted(scheduled)
    assert set(scheduled.values()) == {1}


async def output_counts(harness: Harness, study_id: Any) -> dict[str, int]:
    async with session_scope(harness.sessionmaker) as session:

        async def count(model: Any) -> int:
            stmt = select(func.count()).select_from(model).where(model.study_id == study_id)
            return int(await session.scalar(stmt) or 0)

        chunks = await session.scalar(select(func.count()).select_from(Chunk))
        return {
            "extractions": await count(Extraction),
            "photo_rooms": await count(PhotoRoom),
            "components": await count(Component),
            "chunks": int(chunks or 0),
        }


async def test_new_worker_resumes_a_study_waiting_for_review(harness: Harness) -> None:
    async with harness.worker(NO_STICKY):
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await harness.decide(handle, harness.decision("appraisal_review", 1))
        await harness.wait_awaiting(handle, "rooms_review", 1)
    before = await output_counts(harness, started.study_id)

    # No worker: the study waits durably. A new worker replays and continues.
    async with harness.worker():
        status = await harness.status(handle)
        assert (status.awaiting, status.revision) == ("rooms_review", 1)
        await harness.approve(handle, "rooms_review")
        await harness.approve(handle, "assets_review")
        result = await harness.result(handle)

    assert result.outcome == "completed"
    after = await output_counts(harness, started.study_id)
    assert {k: after[k] for k in before} == before  # nothing recomputed
    await assert_no_duplicates(harness, handle)
    assert len(await harness.runs(started.study_id)) == 1


async def test_worker_stopped_between_activities(harness: Harness) -> None:
    ingested = asyncio.Event()

    def ingest_then_signal(real: IngestAppraisal) -> Any:
        @activity.defn(name="ingest_appraisal")
        async def wrapped(input: IngestAppraisalInput) -> IngestAppraisalOutput:
            output = await real.run(input)
            ingested.set()
            return output

        return wrapped

    study = await harness.seed(photos=["kitchen_1.jpg"])
    async with harness.worker(
        NO_STICKY, ingest_appraisal=ingest_then_signal(IngestAppraisal(harness.deps))
    ):
        started = await harness.start(study)
        await asyncio.wait_for(ingested.wait(), 30)

    async with harness.worker():
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)
    assert result.outcome == "completed"
    counts = await started_counts(started.handle)
    assert counts["ingest_appraisal:r1"] == 1
    assert counts["extract_fields:r1"] == 1  # resumed at the next step, not re-ingested
    await assert_no_duplicates(harness, started.handle)
    outputs = await output_counts(harness, study.study_id)
    assert (outputs["extractions"], outputs["photo_rooms"], outputs["chunks"]) == (
        1,
        1,
        sample_appraisal_chunks(),  # S3: one real ingestion of the sample (S2 stub: 3)
    )


async def test_worker_stopped_in_the_middle_of_an_activity(harness: Harness) -> None:
    study = await harness.seed(photos=["kitchen_1.jpg", "bathroom_1.jpg"])
    slow = str(study.photo_ids[0])
    # Shut down without a grace period: the running activity is cancelled mid-transaction.
    async with harness.worker({**NO_STICKY, "graceful_shutdown_timeout": timedelta(0)}):
        started = await harness.start(study, simulate_photos={slow: SimulateFault(sleep_seconds=4)})
        await harness.wait_for(
            started.handle,
            lambda s: s.pending_photos == 1 and s.completed_photos == 1,
            "slow photo in flight",
        )
        await asyncio.sleep(1)

    async with harness.worker():
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        assert status.flagged_photos == ()
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)
    assert result.outcome == "completed"
    history = await started.handle.fetch_history()
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    }
    attempts = {
        scheduled[e.activity_task_started_event_attributes.scheduled_event_id]: (
            e.activity_task_started_event_attributes.attempt
        )
        for e in history.events
        if e.HasField("activity_task_started_event_attributes")
    }
    assert attempts[f"analyze_photo:{slow}:r1"] == 2  # interrupted once, then retried
    outputs = await output_counts(harness, study.study_id)
    assert (outputs["photo_rooms"], outputs["components"]) == (2, 4)
    results = await harness.activity_results(workflow_id_for(str(study.study_id)))
    keys = [r.idempotency_key for r in results]
    assert len(keys) == len(set(keys))
    assert {r.status for r in results} == {COMPLETED}
