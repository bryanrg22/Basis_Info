"""Two different decisions for the same gate and revision: exactly one wins.

S2_SPEC section 9: the validator rejects the second with the stage/revision reason;
BUILD_PLAN 6.3: approve and reject race -> one wins, the other is rejected (the API maps
it to 409). A rejected update is not written to history.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from lifecycle_support import Harness
from temporalio.client import WorkflowHandle, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from basis.workflow.contracts import DecisionAck
from basis.workflow.study_workflow import INVALID_DECISION

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def rejection(error: BaseException) -> ApplicationError:
    assert isinstance(error, WorkflowUpdateFailedError), error
    cause = error.cause
    assert isinstance(cause, ApplicationError) and cause.type == INVALID_DECISION, cause
    return cause


async def accepted_updates(handle: WorkflowHandle[Any, Any]) -> int:
    history = await handle.fetch_history()
    return sum(
        1
        for e in history.events
        if e.HasField("workflow_execution_update_accepted_event_attributes")
    )


async def test_second_review_of_a_decided_revision_is_rejected(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await harness.decide(handle, harness.decision("appraisal_review", 1, "approve"))

        with pytest.raises(WorkflowUpdateFailedError) as caught:
            await harness.decide(handle, harness.decision("appraisal_review", 1, "reject"))
        message = rejection(caught.value).message
        # Depending on how far the workflow got: still holding the first decision, moving
        # on (not waiting), or already at the next gate (wrong stage). All name the stage.
        assert "appraisal_review" in message, message

        status = await harness.wait_awaiting(handle, "rooms_review", 1)
        assert len(status.decisions_applied) == 1
        assert await accepted_updates(handle) == 1


async def test_racing_approve_and_reject_exactly_one_wins(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        approve = harness.decision("appraisal_review", 1, "approve")
        reject = harness.decision("appraisal_review", 1, "reject")

        results = await asyncio.gather(
            harness.decide(handle, approve), harness.decide(handle, reject), return_exceptions=True
        )
        accepted = [r for r in results if isinstance(r, DecisionAck)]
        failed = [r for r in results if isinstance(r, BaseException)]
        assert len(accepted) == 1 and len(failed) == 1, results
        assert "appraisal_review" in rejection(failed[0]).message

        winner = approve if isinstance(results[0], DecisionAck) else reject
        status = await harness.status(handle)
        assert status.decisions_applied == (winner.review_id,)
        if winner is approve:
            await harness.wait_awaiting(handle, "rooms_review", 1)
        else:
            await harness.wait_awaiting(handle, "appraisal_review", 2)
        assert await accepted_updates(handle) == 1
