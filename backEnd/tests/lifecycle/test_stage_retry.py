"""A failed stage fails fast and is retried in place (EVAL_REPORT section 25, item 3).

Before v0.9 an extraction failure surfaced only after the photo stage (17 minutes and
$2.67 on the live run) and failed the whole execution, which could not be restarted. With
patch ``stage-failure-retry-v1``:

* an extraction that fails permanently is noticed at once: the photo work in flight is
  cancelled, the study is ``failed`` with the reason, and the workflow waits;
* the ``retry`` update (``POST /v1/studies/{id}/retry``) clears the failure and runs the
  stage again; the photos already analyzed are reused and the study completes;
* ``process_assets`` and ``export_report`` failures are held and retried the same way;
* ``retry`` is refused while no stage has failed; a held study can still be cancelled.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer

from basis.activities.export_report import ExportReport
from basis.activities.extract_fields import ExtractFields
from basis.activities.process_assets import ProcessAssets
from basis.db.enums import StudyStatus
from basis.db.models import Asset, PhotoRoom
from basis.db.session import session_scope
from basis.workflow.contracts import (
    ExportReportInput,
    ExportReportOutput,
    ExtractFieldsInput,
    ExtractFieldsOutput,
    ProcessAssetsInput,
    ProcessAssetsOutput,
    SimulateFault,
)
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BROKEN = "No module named 'aiohttp'"


def broken(name: str) -> ApplicationError:
    return ApplicationError(f"{name}: {BROKEN}", type="ProviderUnavailable", non_retryable=True)


class FlakyExtract:
    """``extract_fields`` that fails permanently ``failures`` times, then is the real one."""

    def __init__(self, harness: Harness, failures: int) -> None:
        self.real = ExtractFields(harness.deps)
        self.failures = failures
        self.calls = 0

    @activity.defn(name="extract_fields")
    async def run(self, input: ExtractFieldsInput) -> ExtractFieldsOutput:
        self.calls += 1
        if self.calls <= self.failures:
            raise broken("extract_fields")
        return await self.real.run(input)


class FlakyAssets:
    def __init__(self, harness: Harness) -> None:
        self.real = ProcessAssets(harness.deps)
        self.calls = 0

    @activity.defn(name="process_assets")
    async def run(self, input: ProcessAssetsInput) -> ProcessAssetsOutput:
        self.calls += 1
        if self.calls == 1:
            raise broken("process_assets")
        return await self.real.run(input)


class FlakyExport:
    def __init__(self, harness: Harness) -> None:
        self.real = ExportReport(harness.deps)
        self.calls = 0

    @activity.defn(name="export_report")
    async def run(self, input: ExportReportInput) -> ExportReportOutput:
        self.calls += 1
        if self.calls == 1:
            raise broken("export_report")
        return await self.real.run(input)


async def history_events(handle: Any) -> list[tuple[str, str]]:
    """``(what, activity type)`` for each activity scheduled, completed, failed or asked to
    cancel, in history order."""
    history = await handle.fetch_history()
    types: dict[int, str] = {}
    events: list[tuple[str, str]] = []
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            name = event.activity_task_scheduled_event_attributes.activity_type.name
            types[event.event_id] = name
            events.append(("scheduled", name))
        elif event.HasField("activity_task_completed_event_attributes"):
            scheduled = event.activity_task_completed_event_attributes.scheduled_event_id
            events.append(("completed", types[scheduled]))
        elif event.HasField("activity_task_failed_event_attributes"):
            scheduled = event.activity_task_failed_event_attributes.scheduled_event_id
            events.append(("failed", types[scheduled]))
        elif event.HasField("activity_task_cancel_requested_event_attributes"):
            scheduled = event.activity_task_cancel_requested_event_attributes.scheduled_event_id
            events.append(("cancel_requested", types[scheduled]))
    return events


async def photo_rows(harness: Harness, study_id: uuid.UUID) -> int:
    async with session_scope(harness.sessionmaker) as session:
        found = await session.scalars(select(PhotoRoom).where(PhotoRoom.study_id == study_id))
        return len(found.all())


async def test_an_extraction_failure_stops_the_photos_and_a_retry_completes_the_study(
    harness: Harness,
) -> None:
    flaky = FlakyExtract(harness, failures=1)
    study = await harness.seed()
    slow = {str(p): SimulateFault(sleep_seconds=3) for p in study.photo_ids}
    async with harness.worker(extract_fields=flaky.run):
        started = await harness.start(study, simulate_photos=slow)
        handle, sid = started.handle, started.study_id
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, "the extraction to fail"
        )
        assert status.failed_stage == "extracting" and status.awaiting is None
        assert status.failure is not None and BROKEN in status.failure
        assert status.failure.startswith("extract_fields: ProviderUnavailable")
        await harness.wait_for(handle, lambda s: s.pending_photos == 0, "photos to stop")
        # Failed fast: as soon as the extraction failed, every photo in flight was asked to
        # cancel, before any of them finished (each sleeps 3 s); a photo that still completes
        # (it reaches its commit before its next heartbeat sees the cancel) keeps its rows
        # for the retry but schedules nothing more (no localization, no room step).
        events = await history_events(handle)
        failed = events.index(("failed", "extract_fields"))
        first_cancel = events.index(("cancel_requested", "analyze_photo"))
        recorded = events.index(("scheduled", "record_failure"))
        assert failed < first_cancel < recorded
        assert ("completed", "analyze_photo") not in events[:first_cancel]
        assert events[:recorded].count(("cancel_requested", "analyze_photo")) == 3
        after = events[first_cancel:recorded]
        assert ("scheduled", "localize_photo") not in after
        assert ("scheduled", "reconcile_rooms") not in after
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (StudyStatus.FAILED, "extracting")
        assert row.failure_reason is not None and BROKEN in row.failure_reason

        ack = await handle.execute_update(StudyWorkflow.retry)
        assert (ack.stage, ack.revision, ack.already_requested) == ("extracting", 1, False)
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        assert status.failed_stage is None and status.failure is None
        assert status.completed_photos == 3 and status.flagged_photos == ()
        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed"
    assert flaky.calls == 2
    assert await photo_rows(harness, sid) == 3
    row = await harness.study_row(sid)
    assert (row.status, row.failure_reason) == (StudyStatus.COMPLETED, None)
    [run] = await harness.runs(sid)
    assert run.outcome == "completed"
    sequence = await harness.activity_sequence(handle)
    assert sequence.count("record_failure") == 2  # recorded, then cleared
    # The history of a failed-and-retried study replays deterministically.
    history = await handle.fetch_history()
    replayed = await Replayer(
        workflows=[StudyWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)
    assert replayed.replay_failure is None


async def test_retry_is_refused_while_nothing_has_failed(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        with pytest.raises(WorkflowUpdateFailedError) as raised:
            await started.handle.execute_update(StudyWorkflow.retry)
        cause = raised.value.cause
        assert isinstance(cause, ApplicationError) and cause.type == "InvalidRetry"
        await harness.approve_all(started.handle)
        assert (await harness.result(started.handle)).outcome == "completed"


@pytest.mark.parametrize(
    ("name", "stage", "make"),
    [
        ("process_assets", "assets_processing", FlakyAssets),
        ("export_report", "exporting", FlakyExport),
    ],
)
async def test_a_later_stage_failure_is_held_and_retried(
    harness: Harness, name: str, stage: str, make: Any
) -> None:
    flaky = make(harness)
    async with harness.worker(**{name: flaky.run}):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.approve(handle, "appraisal_review")
        await harness.approve(handle, "rooms_review")
        if stage == "exporting":
            await harness.approve(handle, "assets_review")
        status = await harness.wait_for(
            handle, lambda s: s.failed_stage is not None, f"{name} to fail"
        )
        assert status.failed_stage == stage
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (StudyStatus.FAILED, stage)
        first = await handle.execute_update(StudyWorkflow.retry)
        assert first.stage == stage
        if stage == "assets_processing":
            await harness.approve(handle, "assets_review")
        result = await harness.result(handle)
    assert result.outcome == "completed" and result.export_uri is not None
    assert flaky.calls == 2
    if stage == "assets_processing":
        async with session_scope(harness.sessionmaker) as session:
            assets = (await session.scalars(select(Asset).where(Asset.study_id == sid))).all()
        assert len(assets) == 6  # written once, by the retry
    row = await harness.study_row(sid)
    assert (row.status, row.failure_reason) == (StudyStatus.COMPLETED, None)


async def test_a_held_study_can_be_cancelled(harness: Harness) -> None:
    flaky = FlakyExtract(harness, failures=5)
    async with harness.worker(extract_fields=flaky.run):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_for(handle, lambda s: s.failed_stage is not None, "the failure")
        await handle.signal(StudyWorkflow.cancel_study, "client withdrew")
        result = await harness.result(handle)
    assert result.outcome == "cancelled" and result.cancel_reason == "client withdrew"
    [run] = await harness.runs(sid)
    assert run.outcome == "cancelled"
