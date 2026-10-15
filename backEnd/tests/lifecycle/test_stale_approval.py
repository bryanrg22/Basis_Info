"""A decision for anything but the current gate and revision is rejected by the validator.

BUILD_PLAN 6.3 ``test_stale_approval``: approving revision 1 after a rejection produced
revision 2 is refused, as are a decision for another stage, one sent while no gate is
open, and corrections the gate does not accept. Nothing rejected reaches history.
"""

from __future__ import annotations

import uuid

import pytest
from lifecycle_support import Harness
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from basis.workflow.contracts import CorrectionInput, SimulateFault
from basis.workflow.study_workflow import INVALID_DECISION

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def message_of(error: WorkflowUpdateFailedError) -> str:
    cause = error.cause
    assert isinstance(cause, ApplicationError) and cause.type == INVALID_DECISION
    return cause.message


async def test_approving_a_superseded_revision_is_rejected(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await harness.decide(handle, harness.decision("appraisal_review", 1, "reject"))
        await harness.wait_awaiting(handle, "appraisal_review", 2)

        with pytest.raises(WorkflowUpdateFailedError) as caught:
            await harness.decide(handle, harness.decision("appraisal_review", 1, "approve"))
        assert "stale revision" in message_of(caught.value)
        assert "revision 2" in message_of(caught.value)

        status = await harness.status(handle)
        assert (status.awaiting, status.revision) == ("appraisal_review", 2)
        assert len(status.decisions_applied) == 1

        # The current revision is still decidable.
        ack = await harness.decide(handle, harness.decision("appraisal_review", 2, "approve"))
        assert ack.accepted and ack.applied_revision == 2
        await harness.wait_awaiting(handle, "rooms_review", 2)


async def test_decision_for_another_stage_is_rejected(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        with pytest.raises(WorkflowUpdateFailedError) as caught:
            await harness.decide(started.handle, harness.decision("assets_review", 1))
        assert "wrong stage" in message_of(caught.value)


async def test_decision_while_no_gate_is_open_is_rejected(harness: Harness) -> None:
    async with harness.worker():
        study = await harness.seed(photos=["kitchen_1.jpg"])
        slow = {str(study.photo_ids[0]): SimulateFault(sleep_seconds=3)}
        started = await harness.start(study, simulate_photos=slow)
        await harness.wait_for(started.handle, lambda s: s.stage == "extracting", "extracting")
        with pytest.raises(WorkflowUpdateFailedError) as caught:
            await harness.decide(started.handle, harness.decision("appraisal_review", 1))
        assert "not waiting for a decision" in message_of(caught.value)
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)


@pytest.mark.parametrize(
    ("stage", "entity_type", "entity_id", "expected"),
    [
        ("appraisal_review", "room", str(uuid.uuid4()), "does not accept corrections"),
        ("appraisal_review", "extraction_field", "not-a-uuid", "is not a UUID"),
    ],
)
async def test_corrections_the_gate_does_not_accept_are_rejected(
    harness: Harness, stage: str, entity_type: str, entity_id: str, expected: str
) -> None:
    async with harness.worker():
        started = await harness.start()
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        correction = CorrectionInput(entity_type=entity_type, entity_id=entity_id, after={})
        decision = harness.decision(stage, 1, "reject", corrections=[correction])  # type: ignore[arg-type]
        with pytest.raises(WorkflowUpdateFailedError) as caught:
            await harness.decide(started.handle, decision)
        assert expected in message_of(caught.value)
        status = await harness.status(started.handle)
        assert (status.awaiting, status.decisions_applied) == ("appraisal_review", ())
