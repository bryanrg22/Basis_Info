"""The embedding loop's progress reports keep a real activity alive through a batch that
runs longer than its heartbeat timeout (EVAL_REPORT 39).

A real Temporal dev server (``workflowtest_support.local_temporal``) enforces a 1 s
heartbeat timeout on an activity whose embedder blocks 2.5 s per batch, as the model load
did inside the first batch on a fresh worker (187 s cold, 7 s warm, against 30 s). With
the loop's reports every 0.2 s the activity completes on its first attempt; with the
reports suppressed (``every`` far longer than the batch, the loop before this section) the
same activity fails with a heartbeat timeout, which is what the test is for.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, TimeoutError, TimeoutType
from temporalio.worker import Worker

# The workflow below lives in this module, which the workflow sandbox re-imports: the
# activity side (and the database helpers behind the Temporal helper) stay outside it.
with workflow.unsafe.imports_passed_through():
    from workflowtest_support import local_temporal

    from basis.retrieval.embed import HashEmbedder
    from basis.retrieval.ingest import EMBED_BATCH, _embed

pytestmark = [pytest.mark.integration, pytest.mark.slow]

BATCH_SECONDS = 2.5
HEARTBEAT = timedelta(seconds=1)


class Blocking(HashEmbedder):
    def embed(self, texts: Any) -> list[list[float]]:
        time.sleep(BATCH_SECONDS)  # on the worker thread, like a model load or encode
        return super().embed(texts)


@activity.defn(name="embed_blocking")
async def embed_blocking(every: float) -> int:
    texts = ["text"] * (EMBED_BATCH + 1)  # two batches
    vectors = await _embed(Blocking(), texts, activity.heartbeat, every=every)
    assert len(vectors) == len(texts)
    return activity.info().attempt


@workflow.defn
class EmbedWorkflow:
    @workflow.run
    async def run(self, every: float) -> int:
        return await workflow.execute_activity(
            "embed_blocking",
            every,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=HEARTBEAT,
            retry_policy=RetryPolicy(maximum_attempts=1),
            result_type=int,
        )


async def run_once(every: float) -> int:
    async with local_temporal() as env:
        queue = f"embed-heartbeat-{uuid.uuid4().hex[:8]}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[EmbedWorkflow],
            activities=[embed_blocking],
            max_heartbeat_throttle_interval=timedelta(milliseconds=200),
        ):
            result: int = await env.client.execute_workflow(
                EmbedWorkflow.run,
                every,
                id=f"wf-{queue}",
                task_queue=queue,
                execution_timeout=timedelta(seconds=60),
            )
            return result


async def test_reports_during_a_batch_keep_the_activity_alive() -> None:
    assert await run_once(every=0.2) == 1  # first attempt, no heartbeat timeout


async def test_without_reports_during_the_batch_the_heartbeat_times_out() -> None:
    with pytest.raises(WorkflowFailureError) as failure:
        await run_once(every=60.0)
    cause = failure.value.cause
    assert isinstance(cause, ActivityError), cause
    assert isinstance(cause.cause, TimeoutError), cause.cause
    assert cause.cause.type is TimeoutType.HEARTBEAT


async def test_the_blocking_embedder_blocks() -> None:
    started = time.perf_counter()
    await asyncio.to_thread(Blocking().embed, ["x"])
    assert time.perf_counter() - started >= BATCH_SECONDS
