"""The ``decide`` update's validator and handler, exercised without a Temporal server.

The validator and the handler read and write only the workflow object's own fields, so
they are tested on an instance built with ``@workflow.init``'s constructor. The lifecycle
tests cover the same rules through a real server.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from basis.workflow.contracts import CorrectionInput, Decision, StudyWorkflowInput
from basis.workflow.study_workflow import ALREADY_APPLIED, INVALID_DECISION, StudyWorkflow


def workflow_at(stage: str | None = "appraisal_review", revision: int = 1) -> StudyWorkflow:
    wf = StudyWorkflow(
        StudyWorkflowInput(
            study_id=str(uuid.uuid4()),
            org_id=str(uuid.uuid4()),
            input_revision=1,
            workflow_version="test",
            budget_usd=10.0,
        )
    )
    wf._awaiting = stage  # type: ignore[assignment]
    wf._stage = stage or "extracting"  # type: ignore[assignment]
    wf._revision = revision
    return wf


def decision(
    stage: str = "appraisal_review",
    revision: int = 1,
    kind: str = "approve",
    corrections: tuple[CorrectionInput, ...] = (),
    review_id: str | None = None,
) -> Decision:
    return Decision(
        review_id=review_id or str(uuid.uuid4()),
        stage=stage,  # type: ignore[arg-type]
        revision=revision,
        decision=kind,  # type: ignore[arg-type]
        reviewer_id=str(uuid.uuid4()),
        corrections=corrections,
        submitted_at=datetime.now(UTC),
    )


def rejected(wf: StudyWorkflow, d: Any) -> str:
    with pytest.raises(ApplicationError) as caught:
        wf.validate_decide(d)
    assert caught.value.type == INVALID_DECISION
    assert caught.value.non_retryable
    return caught.value.message


def correction(entity_type: str, entity_id: str | None = None) -> CorrectionInput:
    return CorrectionInput(
        entity_type=entity_type,
        entity_id=str(uuid.uuid4()) if entity_id is None else entity_id,
        after={"x": 1},
    )


def test_accepts_a_decision_for_the_open_gate_and_revision() -> None:
    wf = workflow_at("rooms_review", 3)
    d = decision("rooms_review", 3)
    wf.validate_decide(d)
    ack = wf.decide(d)
    assert (ack.accepted, ack.applied_revision, ack.reason) == (True, 3, None)
    assert wf._pending == d
    assert wf.status().decisions_applied == (d.review_id,)


def test_rejects_when_no_gate_is_open() -> None:
    assert "not waiting for a decision" in rejected(workflow_at(None), decision())


@pytest.mark.parametrize("other", ["rooms_review", "assets_review"])
def test_rejects_another_stage(other: str) -> None:
    message = rejected(workflow_at("appraisal_review"), decision(other))
    assert "wrong stage" in message and "appraisal_review" in message


@pytest.mark.parametrize("revision", [1, 3])
def test_rejects_a_revision_other_than_the_current_one(revision: int) -> None:
    message = rejected(workflow_at("appraisal_review", 2), decision(revision=revision))
    assert "stale revision" in message and "revision 2" in message


def test_rejects_a_second_review_while_the_first_is_pending() -> None:
    wf = workflow_at()
    first = decision()
    wf.validate_decide(first)
    wf.decide(first)
    message = rejected(wf, decision(kind="reject"))
    assert "already decided" in message and first.review_id in message


def test_a_redelivered_review_passes_and_is_not_reapplied() -> None:
    wf = workflow_at()
    d = decision()
    wf.validate_decide(d)
    wf.decide(d)
    wf._pending = None  # the run loop consumed it and moved on
    wf._awaiting = "rooms_review"
    wf._revision = 1
    wf.validate_decide(d)  # no exception: a redelivery is answered, not refused
    ack = wf.decide(d)
    assert (ack.accepted, ack.applied_revision, ack.reason) == (True, 1, ALREADY_APPLIED)
    assert wf._pending is None


def test_rejects_after_cancellation() -> None:
    wf = workflow_at()
    wf.cancel_study("withdrawn")
    assert "cancelled" in rejected(wf, decision())


def test_cancel_reason_is_kept_from_the_first_signal() -> None:
    wf = workflow_at()
    wf.cancel_study("first")
    wf.cancel_study("second")
    assert wf._cancel_reason == "first"


def test_rejects_an_unknown_decision_kind() -> None:
    bogus = decision().model_copy(update={"decision": "maybe"})
    assert "unknown decision" in rejected(workflow_at(), bogus)


@pytest.mark.parametrize(
    ("stage", "allowed", "refused"),
    [
        ("appraisal_review", "extraction_field", "room"),
        ("rooms_review", "room", "asset"),
        ("rooms_review", "photo", "extraction_field"),
        ("rooms_review", "component", "asset"),
        ("assets_review", "asset", "component"),
    ],
)
def test_correction_types_are_checked_per_stage(stage: str, allowed: str, refused: str) -> None:
    wf = workflow_at(stage)
    wf.validate_decide(decision(stage, kind="reject", corrections=(correction(allowed),)))
    message = rejected(wf, decision(stage, kind="reject", corrections=(correction(refused),)))
    assert f"does not accept corrections of type '{refused}'" in message


@pytest.mark.parametrize("entity_id", ["", "12", "not-a-uuid", str(uuid.uuid4()) + "0"])
def test_correction_targets_must_be_uuids(entity_id: str) -> None:
    d = decision(kind="reject", corrections=(correction("extraction_field", entity_id),))
    assert "is not a UUID" in rejected(workflow_at(), d)


def test_an_approval_cannot_carry_corrections() -> None:
    d = decision(kind="approve", corrections=(correction("extraction_field"),))
    assert "approval cannot carry corrections" in rejected(workflow_at(), d)


def test_a_rejection_without_corrections_is_valid() -> None:
    workflow_at().validate_decide(decision(kind="reject"))
