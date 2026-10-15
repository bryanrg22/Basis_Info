"""W2: resuming after an engineer approval must run the next stage, not restart.

Reproduced 2026-10-15 (REVIEW_AND_PLAN.md A.3 W2, Appendix 2): resume_workflow()
calls app.ainvoke(update, config) on a finished thread, which starts a new run
from START. Every resume, whatever the stage, runs
['load_study', 'resource_extraction'], and the real resource_extraction_node then
writes the study back to the first stage.
"""

from __future__ import annotations

import asyncio

import pytest

STUDY_ID = "study_regression_resume"


@pytest.mark.regression
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "W2: resume_workflow() re-invokes the graph from START, so every resume runs "
        "load_study -> resource_extraction instead of the next stage"
    ),
)
@pytest.mark.parametrize(
    ("stage", "expected_node"),
    [
        ("reviewing_rooms", "process_assets"),
        ("engineering_takeoff", "complete"),
    ],
)
def test_resume_runs_expected_node(
    stage: str,
    expected_node: str,
    workflow_module,
    recorded_nodes: list[str],
    fake_firestore,
    shared_memory_saver,
) -> None:
    # Start the study the way /workflow/start does; this pauses at the first END.
    asyncio.run(workflow_module.run_workflow(STUDY_ID))
    if recorded_nodes[:1] != ["load_study"]:
        pytest.fail(f"start did not run load_study first: {recorded_nodes}")

    # The study now sits at `stage` awaiting approval; the engineer approves.
    recorded_nodes.clear()
    fake_firestore.stage = stage
    asyncio.run(workflow_module.resume_workflow(STUDY_ID, engineer_approved=True))

    assert expected_node in recorded_nodes, f"nodes run on resume: {recorded_nodes}"
    assert "resource_extraction" not in recorded_nodes, (
        f"resume re-ran resource_extraction: {recorded_nodes}"
    )
