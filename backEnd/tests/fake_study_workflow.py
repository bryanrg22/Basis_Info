"""A minimal stand-in for ``StudyWorkflow``, for relay and API tests (test code only).

Registered under the real names (workflow ``StudyWorkflow``, update ``decide``, query
``status``, signal ``cancel_study``) with the same update-validator semantics as
S2_SPEC section 3, so the relay and the API are tested against a real Temporal server
without depending on the real workflow (built in parallel by another stage-2 builder):

* the validator refuses a decision whose stage is not the workflow's stage or whose
  revision is not its revision, and a decision after cancellation; a review id that was
  already applied passes the validator and is acknowledged ``already applied``;
* approve moves to the next review stage (``assets_review`` -> ``completed``); reject
  stays at the stage with ``revision + 1``;
* ``cancel_study(reason)`` ends the run with ``"cancelled: <reason>"``.

Test-only extras: the ``fake_set_stage`` signal puts the workflow at any stage and
revision (to make it "behind" or "ahead" of a decision), and the ``fake_decisions``
query returns every decision applied, in order, with all fields.

Imported by module name inside the workflow sandbox: keep imports to the SDK, the
stdlib, and ``basis.workflow.contracts`` (passed through).
"""

from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from basis.workflow.contracts import (
        Decision,
        DecisionAck,
        StageName,
        StudyStatusView,
        StudyWorkflowInput,
    )

NEXT_STAGE: dict[str, StageName] = {
    "appraisal_review": "rooms_review",
    "rooms_review": "assets_review",
    "assets_review": "completed",
}


@workflow.defn(name="StudyWorkflow")
class FakeStudyWorkflow:
    def __init__(self) -> None:
        self.stage: StageName = "extracting"
        self.revision = 1
        self.placed_by_test = False
        self.applied: list[str] = []
        self.decisions: list[Decision] = []
        self.cancel_reason: str | None = None

    @workflow.run
    async def run(self, workflow_input: StudyWorkflowInput) -> str:
        if not self.placed_by_test:
            self.revision = workflow_input.input_revision
            self.stage = "appraisal_review"
        await workflow.wait_condition(
            lambda: self.cancel_reason is not None or self.stage == "completed"
        )
        return f"cancelled: {self.cancel_reason}" if self.cancel_reason else "completed"

    @workflow.update(name="decide")
    async def decide(self, decision: Decision) -> DecisionAck:
        if decision.review_id in self.applied:
            return DecisionAck(
                accepted=True, applied_revision=self.revision, reason="already applied"
            )
        self.applied.append(decision.review_id)
        self.decisions.append(decision)
        if decision.decision == "approve":
            self.stage = NEXT_STAGE[decision.stage]
        else:
            self.revision += 1
        return DecisionAck(accepted=True, applied_revision=self.revision)

    @decide.validator
    def validate_decide(self, decision: Decision) -> None:
        if decision.review_id in self.applied:
            return
        if self.cancel_reason is not None:
            raise ValueError("the study was cancelled")
        if decision.stage != self.stage:
            raise ValueError(
                f"wrong stage: the workflow is at {self.stage}, the decision is for "
                f"{decision.stage}"
            )
        if decision.revision != self.revision:
            raise ValueError(
                f"stale revision: the workflow is at revision {self.revision}, the decision "
                f"is for revision {decision.revision}"
            )

    @workflow.query(name="status")
    def status(self) -> StudyStatusView:
        return StudyStatusView(
            stage=self.stage, revision=self.revision, decisions_applied=tuple(self.applied)
        )

    @workflow.query(name="fake_decisions")
    def fake_decisions(self) -> list[Decision]:
        return list(self.decisions)

    @workflow.signal(name="cancel_study")
    def cancel_study(self, reason: str) -> None:
        self.cancel_reason = reason

    @workflow.signal(name="fake_set_stage")
    def fake_set_stage(self, stage: StageName, revision: int) -> None:
        self.placed_by_test = True
        self.stage = stage
        self.revision = revision
