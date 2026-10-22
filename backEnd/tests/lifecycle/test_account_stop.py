"""A provider account error fails the study instead of flagging photos (EVAL_REPORT 26.12).

Before patch ``provider-account-stop-v1`` a provider that refused the account (no credit or
quota, a bad key) failed every later ``analyze_photo``, and the workflow flagged each photo
as a failed analysis and went on to review with them (156 photos in one run). With it:

* the first ``ProviderAccountError`` from ``analyze_photo`` stops the photo stage (no more
  photos scheduled, nothing flagged), the study is ``failed`` at its current stage with the
  provider's reason, and the appraisal review does not open while the failure is held;
* the ``retry`` update re-runs the photos the error stopped (``:retry1`` activity ids) and
  the study completes;
* a failure that arrives while the study waits at the appraisal review is recorded at that
  stage, and the review stays open once it is retried;
* ``reconcile_rooms`` is held and retried the same way instead of flagging the rooms review.

The photo tests go through the real ``analyze_photo`` activity with a provider that refuses
the account, so the provider error, the activity's mapping (non-retryable
``ProviderAccountError``) and the workflow are tested together.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from llm_fakes import FixtureVisionProvider
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer
from test_stage_retry import history_events, photo_rows

from basis.activities._model_calls import provider_failure
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.extract_fields import ExtractFields
from basis.activities.reconcile_rooms import ReconcileRooms
from basis.db.enums import StudyStatus
from basis.llm.errors import ProviderAccountError
from basis.llm.provider import ChatRequest, ChatResponse
from basis.workflow.contracts import (
    ExtractFieldsInput,
    ExtractFieldsOutput,
    ReconcileRoomsInput,
    ReconcileRoomsOutput,
)
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

QUOTA = "You exceeded your current quota, please check your plan and billing details."


class Account:
    """The vision provider behind an account that has run dry until ``top_up``."""

    name = "openai"

    def __init__(self, *, refuse_after: asyncio.Event | None = None) -> None:
        self.inner = FixtureVisionProvider()
        self.broken = True
        self.refused = 0
        self.refusing = asyncio.Event()
        self.refuse_after = refuse_after

    def top_up(self) -> None:
        self.broken = False

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if self.refuse_after is not None:
            await self.refuse_after.wait()
        if self.broken:
            self.refused += 1
            self.refusing.set()
            raise ProviderAccountError(
                QUOTA, provider="openai", status=429, code="insufficient_quota"
            )
        return await self.inner.complete(request)


def analysis(harness: Harness, account: Account) -> Any:
    deps = make_deps(
        harness.deps.sessionmaker,
        harness.deps.storage,
        harness.deps.settings,
        llm=account,
        chunk_store=harness.deps.chunk_store,
    )
    return AnalyzePhoto(deps).run


class ExtractAfter:
    """``extract_fields`` that waits for ``event`` first (orders the test's race)."""

    def __init__(self, harness: Harness, event: asyncio.Event) -> None:
        self.real = ExtractFields(harness.deps)
        self.event = event

    @activity.defn(name="extract_fields")
    async def run(self, input: ExtractFieldsInput) -> ExtractFieldsOutput:
        await self.event.wait()
        await asyncio.sleep(0.5)  # the photo failure reaches the workflow first
        return await self.real.run(input)


async def replays(handle: Any) -> None:
    history = await handle.fetch_history()
    replayed = await Replayer(
        workflows=[StudyWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)
    assert replayed.replay_failure is None


async def test_an_account_error_fails_the_study_and_a_retry_resumes_the_photos(
    harness: Harness,
) -> None:
    account = Account()
    extract = ExtractAfter(harness, account.refusing)
    async with harness.worker(analyze_photo=analysis(harness, account), extract_fields=extract.run):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, "the account error"
        )
        assert status.failed_stage == "extracting"
        assert status.failure is not None
        assert status.failure.startswith("analyze_photo: ProviderAccountError: photo ")
        assert "openai account error (HTTP 429, insufficient_quota)" in status.failure
        assert status.flagged_photos == ()  # not a bad photo
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (StudyStatus.FAILED, "extracting")
        assert row.failure_reason is not None and QUOTA in row.failure_reason

        # The extraction finishes, but the appraisal review does not open while the
        # failure is held: moving the study to the gate would hide the failed status.
        await harness.wait_for(handle, lambda s: s.pending_photos == 0, "photos to drain")
        for _ in range(60):
            if ("completed", "extract_fields") in await history_events(handle):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("the extraction did not complete")
        await asyncio.sleep(0.5)
        status = await harness.status(handle)
        assert status.awaiting is None and status.stage == "extracting"
        assert status.failed_stage == "extracting" and status.completed_photos == 0
        refused = account.refused
        assert 1 <= refused <= 3  # one per photo already running at most; no retries
        events = await history_events(handle)
        assert ("scheduled", "reconcile_rooms") not in events
        assert ("scheduled", "localize_photo") not in events

        account.top_up()
        ack = await handle.execute_update(StudyWorkflow.retry)
        assert (ack.stage, ack.already_requested) == ("extracting", False)
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        assert status.failed_stage is None and status.failure is None
        status = await harness.wait_photos(handle, 3)
        assert status.flagged_photos == ()
        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed" and result.flagged_photos == ()
    assert account.refused == refused  # nothing was refused after the top-up
    assert await photo_rows(harness, sid) == 3
    row = await harness.study_row(sid)
    assert (row.status, row.failure_reason) == (StudyStatus.COMPLETED, None)
    history = await handle.fetch_history()
    retried = [
        e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
        and e.activity_task_scheduled_event_attributes.activity_type.name == "analyze_photo"
        and e.activity_task_scheduled_event_attributes.activity_id.endswith(":retry1")
    ]
    assert len(retried) == refused  # exactly the stopped photos ran again
    sequence = await harness.activity_sequence(handle)
    assert sequence.count("record_failure") == 2  # recorded, then cleared
    await replays(handle)


async def test_an_account_error_at_the_appraisal_review_is_recorded_there(
    harness: Harness,
) -> None:
    gate_open = asyncio.Event()
    account = Account(refuse_after=gate_open)
    async with harness.worker(analyze_photo=analysis(harness, account)):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        gate_open.set()  # the photos are refused only now, while the review is open
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, "the account error"
        )
        assert status.failed_stage == "appraisal_review"
        assert status.awaiting == "appraisal_review" and status.flagged_photos == ()
        await harness.wait_for(handle, lambda s: s.pending_photos == 0, "photos to drain")
        assert (await harness.status(handle)).completed_photos == 0  # stopped, not done
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (StudyStatus.FAILED, "appraisal_review")

        account.top_up()
        ack = await handle.execute_update(StudyWorkflow.retry)
        assert ack.stage == "appraisal_review"
        status = await harness.wait_for(
            handle,
            lambda s: s.failed_stage is None and s.completed_photos == 3 and s.pending_photos == 0,
            "the photos to run again",
        )
        assert status.awaiting == "appraisal_review"
        row = await harness.study_row(sid)
        assert row.status is StudyStatus.WAITING_REVIEW and row.failure_reason is None
        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed" and result.flagged_photos == ()
    assert await photo_rows(harness, sid) == 3
    await replays(handle)


class RefusedOnce:
    """``reconcile_rooms`` refused once for the account, then the real activity. The rooms'
    vote (patch components-gate-v1) makes no model call, so it is never refused."""

    def __init__(self, harness: Harness) -> None:
        self.real = ReconcileRooms(harness.deps)
        self.ids: list[str] = []

    @activity.defn(name="reconcile_rooms")
    async def run(self, input: ReconcileRoomsInput) -> ReconcileRoomsOutput:
        if input.vote_only:
            return await self.real.run(input)
        self.ids.append(activity.info().activity_id)
        if len(self.ids) == 1:
            error = ProviderAccountError(
                "Your credit balance is too low to access the Anthropic API.",
                provider="anthropic",
                status=400,
                code="invalid_request_error",
            )
            raise provider_failure(error, f"room step of study {input.study_id}")
        return await self.real.run(input)


async def test_an_account_error_in_the_room_step_is_held_and_retried(
    harness: Harness,
) -> None:
    """Patch components-gate-v1: the room step runs after the grouping is approved; an
    account error there fails the study at ``components_processing`` (the stage the API
    moved it to), and the retry runs the room step again before the components review."""
    rooms = RefusedOnce(harness)
    async with harness.worker(reconcile_rooms=rooms.run):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.approve(handle, "appraisal_review")
        await harness.approve(handle, "rooms_review")
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, "the room step to fail"
        )
        assert status.failed_stage == "components_processing"
        assert status.failure is not None
        assert status.failure.startswith("reconcile_rooms: ProviderAccountError: ")
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (
            StudyStatus.FAILED,
            "components_processing",
        )
        assert status.awaiting is None  # the components review is not open
        await handle.execute_update(StudyWorkflow.retry)
        await harness.approve_rest(handle)
        result = await harness.result(handle)
        final = await harness.status(handle)
    assert result.outcome == "completed"
    assert rooms.ids == ["reconcile_rooms:r1", "reconcile_rooms:r1:retry1"]
    assert "components_review" not in final.needs_review_stages  # held, not flagged
    await replays(handle)


async def test_an_account_error_in_the_room_step_is_held_and_retried_single_rooms_gate(
    harness: Harness,
) -> None:
    rooms = RefusedOnce(harness)
    async with harness.worker(reconcile_rooms=rooms.run):
        started = await harness.start(components_gate=False)
        handle, sid = started.handle, started.study_id
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, "the room step to fail"
        )
        assert status.failure is not None
        assert status.failure.startswith("reconcile_rooms: ProviderAccountError: ")
        assert "anthropic account error (HTTP 400" in status.failure
        row = await harness.study_row(sid)
        assert row.status is StudyStatus.FAILED
        assert status.flagged_photos == ()
        await handle.execute_update(StudyWorkflow.retry)
        await harness.approve_all(handle)  # the rooms review opens after the room step
        result = await harness.result(handle)
        final = await harness.status(handle)
    assert result.outcome == "completed"
    assert [i.rsplit(":", 1)[-1] for i in rooms.ids] == ["r1", "retry1"]
    assert "rooms_review" not in final.needs_review_stages  # held, not flagged
    await replays(handle)
