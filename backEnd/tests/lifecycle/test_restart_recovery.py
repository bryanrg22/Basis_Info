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
from typing import Any, Final

import pytest
from lifecycle_support import Harness, completed_activities, open_tasks
from lifecycletest_support import sample_appraisal_chunks
from sqlalchemy import func, select
from temporalio import activity
from temporalio.client import WorkflowHandle

from basis.activities.ingest_appraisal import IngestAppraisal
from basis.db.models import Chunk, Component, CorpusVersion, Extraction, PhotoRoom
from basis.db.repositories.activity_result import COMPLETED
from basis.db.session import session_scope
from basis.workflow.contracts import IngestAppraisalInput, IngestAppraisalOutput, SimulateFault
from basis.workflow.study_workflow import workflow_id_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# The worker that is stopped runs without a workflow cache, so the server never routes the
# next task or query to its (now dead) sticky queue. A real server falls back from a dead
# sticky queue after a timeout; the time-skipping test server does not for queries. A query
# to such a worker is a task on the normal queue, which the test server hands out even while
# a workflow task of the same run is in flight; one of the two is then lost (observed: a
# workflow task timed out after 10 s, or the query RPC timed out). So the tests wait on that
# worker in the history (Harness.wait_history, wait_settled), never with the status query.
NO_STICKY: dict[str, Any] = {"max_cached_workflows": 0}
# A workflow task the test server hands to a poll the stopped worker sent just before it
# shut down is lost until this timeout (the server's default is 10 s). The slowest workflow
# task measured under heavy load (three suites in parallel on saturated cores) took 0.8 s.
STOPPED_WORKER_TASK_TIMEOUT: Final = timedelta(seconds=3)


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


async def started_by(handle: WorkflowHandle[Any, Any]) -> dict[str, str]:
    """The identity of the worker that ran each activity id (its final attempt)."""
    history = await handle.fetch_history()
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    }
    return {
        scheduled[e.activity_task_started_event_attributes.scheduled_event_id]: (
            e.activity_task_started_event_attributes.identity
        )
        for e in history.events
        if e.HasField("activity_task_started_event_attributes")
    }


async def assert_nothing_lost(handle: WorkflowHandle[Any, Any], stopped_at: int) -> None:
    """No activity was lost (a lost one is retried after its timeout, with no timed-out
    event), and a workflow task timed out only if it was scheduled after the worker stopped
    (``stopped_at``: the last event id then) and the stopped worker's poll took it."""
    history = await handle.fetch_history()
    assert {
        e.activity_task_started_event_attributes.attempt
        for e in history.events
        if e.HasField("activity_task_started_event_attributes")
    } == {1}
    workflow_task_started_by = {
        e.workflow_task_started_event_attributes.scheduled_event_id: (
            e.workflow_task_started_event_attributes.identity
        )
        for e in history.events
        if e.HasField("workflow_task_started_event_attributes")
    }
    timed_out = [
        e.workflow_task_timed_out_event_attributes.scheduled_event_id
        for e in history.events
        if e.HasField("workflow_task_timed_out_event_attributes")
    ]
    assert all(scheduled > stopped_at for scheduled in timed_out)
    assert {workflow_task_started_by[scheduled] for scheduled in timed_out} <= {"stopped"}


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

        # The study's own chunks only: the harness also seeds the reference corpus.
        chunks = await session.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(CorpusVersion, Chunk.corpus_version_id == CorpusVersion.id)
            .where(CorpusVersion.study_id == study_id)
        )
        return {
            "extractions": await count(Extraction),
            "photo_rooms": await count(PhotoRoom),
            "components": await count(Component),
            "chunks": int(chunks or 0),
        }


async def test_new_worker_resumes_a_study_waiting_for_review(harness: Harness) -> None:
    """The stopped worker runs the study to the rooms review; the new worker finishes it.

    The stopped worker has no cache, so the test waits for its gates in the history, not
    with the status query (see ``NO_STICKY``; observed before: 1 run in ~150 under load
    lost a workflow task for 10 s while the test polled the query). At the rooms review
    nothing is in flight, so the worker stops with no task to lose (see
    :meth:`Harness.wait_settled`).
    """
    async with harness.worker({**NO_STICKY, "identity": "stopped"}):
        started = await harness.start(task_timeout=STOPPED_WORKER_TASK_TIMEOUT)
        handle = started.handle
        await harness.wait_settled(handle, stage="appraisal_review")
        await harness.decide(handle, harness.decision("appraisal_review", 1))
        stopped_at = await harness.wait_settled(handle, stage="rooms_review")
    before = await output_counts(harness, started.study_id)

    # No worker: the study waits durably. A new worker replays and continues.
    async with harness.worker({"identity": "resumed"}):
        status = await harness.status(handle)
        assert (status.awaiting, status.revision) == ("rooms_review", 1)
        await harness.approve(handle, "rooms_review")
        await harness.approve(handle, "components_review")
        await harness.approve(handle, "assets_review")
        result = await harness.result(handle)

    assert result.outcome == "completed"
    after = await output_counts(harness, started.study_id)
    assert {k: after[k] for k in before} == before  # nothing recomputed
    workers = await started_by(handle)
    assert (workers["transition:rooms_review:r1"], workers["reconcile_rooms:r1"]) == (
        "stopped",
        "resumed",
    )
    await assert_nothing_lost(handle, stopped_at)
    await assert_no_duplicates(harness, handle)
    assert len(await harness.runs(started.study_id)) == 1


async def test_worker_stopped_between_activities(harness: Harness) -> None:
    """The stopped worker ingests the appraisal (and runs the photo stage); the new worker
    extracts.

    A task the server hands to the stopping worker's polls is lost until its timeout. So
    the worker stops only once the workflow is settled (nothing open but the ingestion,
    which the test holds), and the ingestion's result is delivered (async completion)
    after it has shut down: no activity can then be scheduled while it stops. (Before,
    observed: the photo stage's ``localize_photo`` and ``reconcile_rooms``, scheduled as it
    stopped, waited out their 30 s heartbeat timeout, longer than the harness waits; the
    shape of the CI failure in run 40157838312.) The one task
    still exposed is the workflow task after the ingestion: the test server can hand it
    to a poll the stopped worker sent just before shutting down (4 of 570 runs under
    heavy load), so the workflow runs with a short workflow task timeout.
    """
    held: list[tuple[bytes, IngestAppraisalOutput]] = []

    def ingest_then_hold(real: IngestAppraisal) -> Any:
        @activity.defn(name="ingest_appraisal")
        async def wrapped(input: IngestAppraisalInput) -> IngestAppraisalOutput:
            held.append((activity.info().task_token, await real.run(input)))
            activity.raise_complete_async()

        return wrapped

    study = await harness.seed(photos=["kitchen_1.jpg"])
    async with harness.worker(
        {**NO_STICKY, "identity": "stopped"},
        ingest_appraisal=ingest_then_hold(IngestAppraisal(harness.deps)),
    ):
        started = await harness.start(study, task_timeout=STOPPED_WORKER_TASK_TIMEOUT)
        # The photo stage done too, so the worker stops with no task in flight. (The held
        # ingestion sends no heartbeats; this takes ~1 s of its 30 s heartbeat timeout.)
        stopped_at = await harness.wait_settled(started.handle, holding="ingest_appraisal:r1")

    [(task_token, output)] = held
    await harness.client.get_async_activity_handle(task_token=task_token).complete(output)
    async with harness.worker({"identity": "resumed"}):
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)
    assert result.outcome == "completed"
    counts = await started_counts(started.handle)
    assert counts["ingest_appraisal:r1"] == 1
    assert counts["extract_fields:r1"] == 1  # resumed at the next step, not re-ingested
    workers = await started_by(started.handle)
    assert (workers["ingest_appraisal:r1"], workers["extract_fields:r1"]) == ("stopped", "resumed")
    await assert_nothing_lost(started.handle, stopped_at)
    await assert_no_duplicates(harness, started.handle)
    outputs = await output_counts(harness, study.study_id)
    assert (outputs["extractions"], outputs["photo_rooms"], outputs["chunks"]) == (
        1,
        1,
        sample_appraisal_chunks(),  # S3: one real ingestion of the sample (S2 stub: 3)
    )


async def test_worker_stopped_in_the_middle_of_an_activity(harness: Harness) -> None:
    """The worker is stopped while the slow photo's analysis runs; it is retried once.

    The stopped worker has no cache, so the test reads the history rather than querying
    it (see ``NO_STICKY``). It stops the worker once the workflow has reached the appraisal
    gate and only the slow analysis and the gate's ``transition`` are in flight (the
    transition waits for the analysis's transaction): nothing else can be scheduled until
    they finish, so no task reaches the worker as it stops. (Observed before: stopping a
    fixed second after the other photo finished could coincide with the transition being
    scheduled; it was handed to the stopping worker and lost for its 30 s timeout, longer
    than the harness waits; 1 run in ~140 under load.)
    """
    study = await harness.seed(photos=["kitchen_1.jpg", "bathroom_1.jpg"])
    slow = str(study.photo_ids[0])
    analysis, gate = f"analyze_photo:{slow}:r1", "transition:appraisal_review:r1"
    # Shut down without a grace period: the running activity is cancelled mid-transaction.
    async with harness.worker({**NO_STICKY, "graceful_shutdown_timeout": timedelta(0)}):
        # Long enough to outlast extraction under load (the stop comes after it): with 4 s,
        # the estimated time left between the stop and its end fell to ~0 s under heavy load.
        started = await harness.start(study, simulate_photos={slow: SimulateFault(sleep_seconds=6)})
        await harness.wait_history(
            started.handle,
            lambda events: (
                open_tasks(events) == {analysis, gate}
                or (open_tasks(events) == {analysis} and gate in completed_activities(events))
            ),
            "the slow photo and the appraisal gate in flight",
        )
        # Lets the transition, scheduled moments ago, reach the worker: the stop then
        # cancels it like the analysis instead of racing its dispatch.
        await asyncio.sleep(0.5)

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
