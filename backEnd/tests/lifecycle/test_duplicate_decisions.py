"""The same decision delivered twice is applied once (the relay delivers at least once).

S2_SPEC section 9: same ``review_id`` twice -> one application; the second ack is
``accepted=True, reason="already applied"``. BUILD_PLAN 6.3 ``test_duplicate_signals``.

Two layers make a redelivery harmless, and both are tested:

* The relay sends every decision with update id ``decision-<review_id>``. Temporal
  deduplicates update ids within a run: a redelivery returns the first delivery's ack and
  never reaches the handler.
* The handler is idempotent on ``review_id``: a delivery under any other update id is
  answered ``already applied`` and changes nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from lifecycle_support import Harness
from temporalio.client import WorkflowHandle

from basis.db.enums import Stage
from basis.workflow.study_workflow import ALREADY_APPLIED

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def scheduled(handle: WorkflowHandle[Any, Any], activity_id: str) -> int:
    history = await handle.fetch_history()
    return sum(
        1
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
        and e.activity_task_scheduled_event_attributes.activity_id == activity_id
    )


async def test_redelivered_approval_is_acknowledged_but_not_reapplied(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        decision = harness.decision("appraisal_review", 1)

        first = await harness.decide(handle, decision)
        assert (first.accepted, first.applied_revision, first.reason) == (True, 1, None)
        # Same relay update id: deduplicated by Temporal, the first ack is returned.
        assert await harness.decide(handle, decision) == first
        # Any other update id: the handler answers "already applied".
        other = await harness.decide(handle, decision, relay_id=False)
        assert (other.accepted, other.applied_revision, other.reason) == (
            True,
            1,
            ALREADY_APPLIED,
        )

        status = await harness.wait_awaiting(handle, "rooms_review", 1)
        assert status.decisions_applied == (decision.review_id,)
        # Redeliveries after the workflow moved on change nothing either.
        assert await harness.decide(handle, decision) == first
        late = await harness.decide(handle, decision, relay_id=False)
        assert (late.accepted, late.reason) == (True, ALREADY_APPLIED)
        status = await harness.status(handle)
        assert (status.awaiting, status.decisions_applied) == (
            "rooms_review",
            (decision.review_id,),
        )
        assert await scheduled(handle, "transition:rooms_review:r1") == 1


async def test_redelivered_rejection_starts_only_one_new_revision(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        decision = harness.decision("appraisal_review", 1, "reject")
        acks = await asyncio.gather(
            harness.decide(handle, decision),
            harness.decide(handle, decision),
            harness.decide(handle, decision, relay_id=False),
            harness.decide(handle, decision, relay_id=False),
        )
        assert all(ack.accepted and ack.applied_revision == 1 for ack in acks)
        relay_a, relay_b, fresh_a, fresh_b = acks
        assert relay_a == relay_b  # one update id, one answer
        # Of the three distinct deliveries, exactly one was applied.
        applied = [ack for ack in (relay_a, fresh_a, fresh_b) if ack.reason is None]
        assert len(applied) == 1

        await harness.wait_awaiting(handle, "appraisal_review", 2)
        again = await harness.decide(handle, decision, relay_id=False)
        assert again.reason == ALREADY_APPLIED
        status = await harness.status(handle)
        assert (status.revision, status.decisions_applied) == (2, (decision.review_id,))
        study = await harness.study_row(started.study_id)
        assert (study.current_stage, study.revision) == (Stage.APPRAISAL_REVIEW, 2)
        assert await scheduled(handle, "transition:extracting:r2") == 1
        assert await scheduled(handle, "transition:extracting:r3") == 0
