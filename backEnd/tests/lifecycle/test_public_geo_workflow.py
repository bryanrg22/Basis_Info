"""``PublicGeoWorkflow`` on the time-skipping server (basis.geo.workflow, EVAL_REPORT 101): the
gather activity's answer is the workflow's; when the activity fails after its retries the fail
activity marks the gather failed with ``BASIS-GEO-015``, once. Stub activities with the real
names and contracts; the study workflow is not involved."""

from __future__ import annotations

import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from basis.errors.codes import E
from basis.geo.contracts import (
    FAIL_ACTIVITY,
    GATHER_ACTIVITY,
    GEO_WORKFLOW,
    FailInput,
    GatherInput,
    GatherResult,
    geo_workflow_id,
)
from basis.geo.workflow import PublicGeoWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class Stubs:
    def __init__(self, *, broken: bool) -> None:
        self.broken = broken
        self.gathers = 0
        self.failed: list[FailInput] = []

    @activity.defn(name=GATHER_ACTIVITY)
    async def gather(self, request: GatherInput) -> GatherResult:
        self.gathers += 1
        if self.broken:
            raise ApplicationError("the step broke", non_retryable=True)
        return GatherResult(status="succeeded")

    @activity.defn(name=FAIL_ACTIVITY)
    async def fail(self, request: FailInput) -> None:
        self.failed.append(request)


@pytest.mark.parametrize("broken", [False, True])
async def test_the_gather_and_its_failure(temporal_env: WorkflowEnvironment, broken: bool) -> None:
    stubs = Stubs(broken=broken)
    queue = f"geo-test-{uuid.uuid4()}"
    gather_id = str(uuid.uuid4())
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[PublicGeoWorkflow],
        activities=[stubs.gather, stubs.fail],
    ):
        result = await temporal_env.client.execute_workflow(
            GEO_WORKFLOW,
            GatherInput(gather_id=gather_id),
            id=geo_workflow_id(gather_id),
            task_queue=queue,
            result_type=GatherResult,
        )
    assert stubs.gathers == 1
    if broken:
        assert result.status == "failed"
        (failed,) = stubs.failed
        assert failed.gather_id == gather_id and failed.code == E.GEO_GATHER_FAILED
        assert "the step broke" in failed.detail
    else:
        assert result.status == "succeeded" and stubs.failed == []
