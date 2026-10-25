"""A provider that refuses every request stops the study visibly; a refused photo is flagged.

The live failure (2026-10-23): a worker started with an old ``.env`` (Azure OpenAI only, a
``gpt-4o-mini`` deployment) sent every photo's calls to a deployment that answered HTTP 400.
The 400 was a bad response, so each photo was retried once and completed as an ``ambiguous``
analysis with no component, cost $0 and ``needs_review``; the UI said every photo was
analyzed and the study went on to the rooms review with no vision at all. Now:

* the 400 is a :class:`~basis.llm.errors.ProviderConfigError` (an account error by type),
  so the first ``analyze_photo`` fails with the non-retryable ``ProviderAccountError`` and
  the workflow's ``provider-account-stop-v1`` path holds the study at ``extracting`` with a
  reason that quotes the provider and says to check the model and deployment settings; the
  ``retry`` update, once the deployment is fixed, re-runs the stopped photos and the study
  completes. No workflow code changed, so the recorded histories replay as before.
* an input the provider refuses (Azure's ``content_filter`` 400) fails that photo only: it
  is flagged, written nowhere, and the study goes on.

Both go through the real ``AzureOpenAIProvider`` (the ``openai`` SDK over a mock HTTP
transport), the real ``analyze_photo`` activity and the workflow, so the error mapping, the
activity's error type and the workflow's reaction are tested together.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import openai
import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from llm_fakes import FixtureVisionProvider
from test_account_stop import ExtractAfter, replays
from test_stage_retry import photo_rows

from basis.activities.analyze_photo import AnalyzePhoto
from basis.db.enums import StudyStatus
from basis.llm.provider import ChatRequest, ChatResponse
from basis.llm.providers import AzureOpenAIProvider
from basis.vision.schemas import ModelPhotoAnalysis
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

UNSUPPORTED = (
    "Unsupported parameter: 'max_tokens' is not supported with this model. Use "
    "'max_completion_tokens' instead."
)
FILTERED = (
    "The response was filtered due to the prompt triggering Azure OpenAI's content "
    "management policy. Please modify your prompt and retry."
)


def azure(*responses: httpx2.Response) -> AzureOpenAIProvider:
    """The real Azure provider over a mock transport answering ``responses`` in turn (the
    last one repeats)."""
    queue = list(responses)

    def answer(request: httpx2.Request) -> httpx2.Response:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = openai.AsyncAzureOpenAI(
        azure_endpoint="https://example-resource.openai.azure.com",
        api_key="azure-key",  # pragma: allowlist secret
        api_version="2024-10-21",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
    )
    return AzureOpenAIProvider(client, timeout_s=5.0, default_model="gpt-4o-mini")


class Deployment:
    """Photo calls go to ``broken`` (the Azure provider answering an error) while set, then
    to the fixture provider; every other call (the extraction fallback) to the fixture."""

    name = "azure"

    def __init__(self, broken: AzureOpenAIProvider, *, refuse: int | None = None) -> None:
        self.inner = FixtureVisionProvider()
        self.broken: AzureOpenAIProvider | None = broken
        self.refuse = refuse  # refuse only this many photo calls, then answer
        self.refused = 0
        self.refusing = asyncio.Event()

    def fix(self) -> None:
        self.broken = None

    async def complete(self, request: ChatRequest) -> ChatResponse:
        photo = request.response_model is ModelPhotoAnalysis
        if (
            photo
            and self.broken is not None
            and (self.refuse is None or self.refused < self.refuse)
        ):
            self.refused += 1
            self.refusing.set()
            return await self.broken.complete(request)  # raises the mapped error
        return await self.inner.complete(request)


def analysis(harness: Harness, provider: Deployment) -> Any:
    deps = make_deps(
        harness.deps.sessionmaker,
        harness.deps.storage,
        harness.deps.settings,
        llm=provider,
        chunk_store=harness.deps.chunk_store,
    )
    return AnalyzePhoto(deps).run


def error(status: int, body: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(status, json={"error": body})


async def test_a_deployment_that_rejects_every_request_stops_the_study_and_retry_resumes(
    harness: Harness,
) -> None:
    deployment = Deployment(
        azure(
            error(
                400,
                {
                    "message": UNSUPPORTED,
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                    "code": "unsupported_parameter",
                },
            )
        )
    )
    extract = ExtractAfter(harness, deployment.refusing)
    async with harness.worker(
        analyze_photo=analysis(harness, deployment), extract_fields=extract.run
    ):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, "the configuration stop"
        )
        assert status.failed_stage == "extracting"
        assert status.failure is not None
        assert status.failure.startswith("analyze_photo: ProviderAccountError: photo ")
        assert (
            f"azure rejected the request (HTTP 400, unsupported_parameter): {UNSUPPORTED}; "
            "check the model and deployment settings, then retry the study"
        ) in status.failure
        assert "account error" not in status.failure
        assert status.flagged_photos == ()  # not three bad photos
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (StudyStatus.FAILED, "extracting")
        assert row.failure_reason == status.failure  # what the progress page shows

        await harness.wait_for(handle, lambda s: s.pending_photos == 0, "photos to drain")
        status = await harness.status(handle)
        assert status.completed_photos == 0 and status.awaiting is None  # nothing "analyzed"
        assert await photo_rows(harness, sid) == 0  # no photo with no component was written
        refused = deployment.refused
        assert 1 <= refused <= 3  # one per photo already running at most; none retried

        deployment.fix()
        ack = await handle.execute_update(StudyWorkflow.retry)
        assert (ack.stage, ack.already_requested) == ("extracting", False)
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        assert status.failed_stage is None and status.failure is None
        status = await harness.wait_photos(handle, 3)
        assert status.flagged_photos == ()
        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed" and result.flagged_photos == ()
    assert deployment.refused == refused
    assert await photo_rows(harness, sid) == 3
    row = await harness.study_row(sid)
    assert (row.status, row.failure_reason) == (StudyStatus.COMPLETED, None)
    await replays(handle)


async def test_a_refused_photo_is_flagged_and_the_study_goes_on(harness: Harness) -> None:
    filtered = {
        "message": FILTERED,
        "type": None,
        "param": "prompt",
        "code": "content_filter",
        "status": 400,
        "innererror": {"code": "ResponsibleAIPolicyViolation"},
    }
    deployment = Deployment(azure(error(400, filtered)), refuse=1)
    async with harness.worker(analyze_photo=analysis(harness, deployment)):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        status = await harness.wait_photos(handle, 3)
        assert len(status.flagged_photos) == 1  # this photo only
        assert status.failed_stage is None and status.failure is None
        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed" and len(result.flagged_photos) == 1
    assert deployment.refused == 1
    assert await photo_rows(harness, sid) == 2  # the refused photo has no room, no rows
    [failure] = await activity_failures(handle)  # one failed attempt: not retried
    assert failure.application_failure_info.type == "ValidationError"
    assert failure.application_failure_info.non_retryable
    assert f"azure rejected the input (HTTP 400, content_filter): {FILTERED}" in failure.message
    row = await harness.study_row(sid)
    assert row.status is StudyStatus.COMPLETED
    await replays(handle)


async def activity_failures(handle: Any) -> list[Any]:
    """The failure of every failed ``analyze_photo`` attempt in the history."""
    history = await handle.fetch_history()
    return [
        event.activity_task_failed_event_attributes.failure
        for event in history.events
        if event.HasField("activity_task_failed_event_attributes")
    ]
