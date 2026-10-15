"""W3: an engineer rejection must never be recorded as an approval.

Reproduced 2026-10-15 (REVIEW_AND_PLAN.md A.3 W3, Appendix 2): with the study at
resource_extraction, resume_workflow(study, engineer_approved=False) still writes
{"appraisalApproved": True} to the study (agentic/graph/workflow.py, the
`current_stage == "resource_extraction"` branch ignores engineer_approved).
"""

from __future__ import annotations

import asyncio

import pytest

STUDY_ID = "study_regression_reject"


@pytest.mark.regression
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "W3: resume_workflow() writes appraisalApproved=True at resource_extraction "
        "regardless of engineer_approved"
    ),
)
def test_rejection_does_not_write_appraisal_approved(
    workflow_module,
    recorded_nodes: list[str],
    fake_firestore,
    shared_memory_saver,
) -> None:
    fake_firestore.stage = "resource_extraction"

    asyncio.run(workflow_module.resume_workflow(STUDY_ID, engineer_approved=False))

    approval_writes = [
        data
        for op, data in fake_firestore.writes
        if op == "update_study" and isinstance(data, dict) and data.get("appraisalApproved") is True
    ]
    assert not approval_writes, f"rejection recorded as approval: {fake_firestore.writes}"
