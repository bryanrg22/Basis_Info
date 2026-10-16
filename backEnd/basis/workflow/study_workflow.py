"""The StudyWorkflow: one durable execution per study (S2_SPEC section 3).

Shape of a run::

    record_run_start
    transition -> extracting
    in parallel: ingest_appraisal -> extract_fields ;
                 analyze_photo -> localize_photo, x N (bounded)
    gate appraisal_review   (reject: apply_corrections, extract_fields again, revision + 1)
    gate rooms_review       (reject: apply_corrections, re-analyze and re-localize flagged
                             photos only)
    transition -> assets_processing ; process_assets
    gate assets_review      (reject: apply_corrections, process_assets again)
    transition -> exporting ; export_report ; transition -> completed ; record_run_end

Engineer decisions arrive through the ``decide`` update. Its validator rejects a decision
for a gate the workflow is not waiting at, for a stale revision, for a gate that already
has a decision, or with a correction type the gate does not accept; nothing rejected is
written to history. The handler is idempotent on ``review_id``: the outbox relay delivers
at least once, and a second delivery returns ``accepted=True, reason="already applied"``
without applying anything.

Determinism rules followed here: no I/O, no ``datetime.now``/``uuid4``/``random`` (only
``workflow.now()``), no iteration over unordered sets, activities referenced by name, and
the only imports are the standard library, the SDK, and pure modules of this package
passed through the sandbox.

Database stage vs. workflow stage: the API's decision endpoint already moves the study row
(``ReviewRepository.record_decision``) before the relay delivers the update, so the
``transition`` activity converges: a study already at the target stage and revision is a
no-op, a study at the source stage is moved, anything else is a non-retryable error.

Versioning: any change to this file's command sequence after the first release must be
guarded so histories recorded by the old code still replay (tests/lifecycle/histories).
Example of where a patch goes, for a hypothetical change that re-analyzes every photo
after a rooms rejection instead of only the flagged ones::

    if workflow.patched("rooms-reject-reanalyze-all-v2"):
        to_analyze = all_photo_ids            # new behavior, new executions
    else:
        to_analyze = flagged_photo_ids        # old behavior, replaying histories

    # Once no execution that started before the change is open, replace the branch with
    # workflow.deprecate_patch("rooms-reject-reanalyze-all-v2") and the new code only.

Patches in this file:

* ``localize-photos-v1`` (v0.6): after a photo's ``analyze_photo`` returns, ``localize_photo``
  runs for it (outside the analysis slot, at most ``photo_concurrency`` at a time) and the
  stage waits for every localization before the next gate. A localization that fails is
  logged and ignored; it never flags the photo. Executions recorded before the patch
  replay without it (tests/lifecycle/histories).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime, timedelta
from typing import Any, Final, Literal, TypeVar, cast, overload

from temporalio import workflow
from temporalio.common import SearchAttributeKey
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel

    from basis.workflow.activity_options import (
        ACTIVITY_CANCELLATION,
        BUDGET_EXCEEDED_ERROR,
        VALIDATION_ERROR,
        options_for,
    )
    from basis.workflow.contracts import (
        CORRECTION_TYPES,
        AnalyzePhotoInput,
        AnalyzePhotoOutput,
        ApplyCorrectionsInput,
        ApplyCorrectionsOutput,
        CorrectionInput,
        Decision,
        DecisionAck,
        ExportReportInput,
        ExportReportOutput,
        ExtractFieldsInput,
        ExtractFieldsOutput,
        IngestAppraisalInput,
        IngestAppraisalOutput,
        ListPhotosInput,
        ListPhotosOutput,
        LocalizePhotoInput,
        LocalizePhotoOutput,
        MarkStalledInput,
        NotifyInput,
        ProcessAssetsInput,
        ProcessAssetsOutput,
        ReviewStage,
        RunEndInput,
        RunStartInput,
        StageName,
        StudyStatusView,
        StudyWorkflowInput,
        StudyWorkflowResult,
        TransitionInput,
    )

WORKFLOW_NAME: Final = "StudyWorkflow"
DECIDE_UPDATE: Final = "decide"
STATUS_QUERY: Final = "status"
CANCEL_SIGNAL: Final = "cancel_study"
INVALID_DECISION: Final = "InvalidDecision"

STUDY_ID_ATTR: Final = SearchAttributeKey.for_keyword("StudyId")
STAGE_ATTR: Final = SearchAttributeKey.for_keyword("Stage")
ORG_ID_ATTR: Final = SearchAttributeKey.for_keyword("OrgId")
SEARCH_ATTRIBUTE_KEYS: Final = (STUDY_ID_ATTR, STAGE_ATTR, ORG_ID_ATTR)

ALREADY_APPLIED: Final = "already applied"
LOCALIZE_PATCH: Final = "localize-photos-v1"

_Out = TypeVar("_Out", bound=BaseModel)
_T = TypeVar("_T")


def workflow_id_for(study_id: str) -> str:
    """The one workflow id a study ever has (S2_SPEC section 1)."""
    return f"study-{study_id}"


def _is_validation_error(error: ActivityError) -> bool:
    cause = error.cause
    return isinstance(cause, ApplicationError) and cause.type == VALIDATION_ERROR


def is_budget_exceeded(error: BaseException) -> bool:
    """True when an activity failed because the study's LLM budget is exhausted."""
    cause = error.cause if isinstance(error, ActivityError) else error
    return isinstance(cause, ApplicationError) and cause.type == BUDGET_EXCEEDED_ERROR


@workflow.defn(name=WORKFLOW_NAME)
class StudyWorkflow:
    @workflow.init
    def __init__(self, input: StudyWorkflowInput) -> None:
        self._input = input
        self._stage: StageName = "uploading"
        self._revision = input.input_revision
        self._awaiting: ReviewStage | None = None
        self._pending: Decision | None = None
        self._applied: dict[str, int] = {}
        self._waiting_since: datetime | None = None
        self._stalled = False
        self._pending_photos = 0
        self._completed_photos = 0
        self._flagged: list[str] = []
        self._needs_review_stages: list[str] = []
        self._budget = input.budget_usd
        self._cancel_reason: str | None = None
        # Tasks spawned by the lifecycle; cancelled with it so no activity keeps running.
        self._children: list[asyncio.Task[Any]] = []
        # Set when the lifecycle is being stopped (cancel). An activity that finishes
        # normally despite the cancel request must not let the lifecycle schedule more.
        self._stopping = False

    # ------------------------------------------------------------------ handlers

    @workflow.query(name=STATUS_QUERY)
    def status(self) -> StudyStatusView:
        return StudyStatusView(
            stage=self._stage,
            revision=self._revision,
            waiting_since=self._waiting_since,
            pending_photos=self._pending_photos,
            completed_photos=self._completed_photos,
            decisions_applied=tuple(self._applied),
            stalled=self._stalled,
            budget_remaining_usd=self._budget,
            awaiting=self._awaiting,
            flagged_photos=tuple(self._flagged),
            needs_review_stages=tuple(self._needs_review_stages),
        )

    @workflow.update(name=DECIDE_UPDATE)
    def decide(self, decision: Decision) -> DecisionAck:
        previous = self._applied.get(decision.review_id)
        if previous is not None:
            return DecisionAck(accepted=True, applied_revision=previous, reason=ALREADY_APPLIED)
        self._applied[decision.review_id] = decision.revision
        self._pending = decision
        return DecisionAck(accepted=True, applied_revision=decision.revision)

    @decide.validator
    def validate_decide(self, decision: Decision) -> None:
        """Reject before anything is written to history; see the module docstring."""
        if decision.review_id in self._applied:
            return  # a redelivery: the handler answers "already applied"
        if self._cancel_reason is not None:
            raise _invalid("the study was cancelled")
        if self._awaiting is None:
            raise _invalid(f"not waiting for a decision (stage {self._stage})")
        if decision.stage != self._awaiting:
            raise _invalid(
                f"wrong stage: waiting for {self._awaiting}, decision is for {decision.stage}"
            )
        if decision.revision != self._revision:
            raise _invalid(
                f"stale revision: {decision.stage} is at revision {self._revision}, "
                f"decision is for revision {decision.revision}"
            )
        if self._pending is not None:
            raise _invalid(
                f"{decision.stage} revision {decision.revision} is already decided by review "
                f"{self._pending.review_id}"
            )
        if decision.decision not in ("approve", "reject"):
            raise _invalid(f"unknown decision {decision.decision!r}")
        allowed = CORRECTION_TYPES[decision.stage]
        for correction in decision.corrections:
            if correction.entity_type not in allowed:
                raise _invalid(
                    f"{decision.stage} does not accept corrections of type "
                    f"{correction.entity_type!r} (allowed: {', '.join(sorted(allowed))})"
                )
            if not _is_uuid(correction.entity_id):
                raise _invalid(f"correction entity_id {correction.entity_id!r} is not a UUID")
        if decision.decision == "approve" and decision.corrections:
            raise _invalid("an approval cannot carry corrections; reject with corrections")

    @workflow.signal(name=CANCEL_SIGNAL)
    def cancel_study(self, reason: str) -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason or "cancelled"

    # ------------------------------------------------------------------ run

    @workflow.run
    async def run(self, input: StudyWorkflowInput) -> StudyWorkflowResult:
        info = workflow.info()
        workflow.upsert_search_attributes(
            [
                STUDY_ID_ATTR.value_set(input.study_id),
                ORG_ID_ATTR.value_set(input.org_id),
                STAGE_ATTR.value_set(self._stage),
            ]
        )
        await self._activity(
            "record_run_start",
            RunStartInput(
                study_id=input.study_id,
                workflow_id=info.workflow_id,
                run_id=info.run_id,
                workflow_version=input.workflow_version,
                input_revision=input.input_revision,
            ),
            None,
            "record_run_start",
        )
        body = asyncio.create_task(self._lifecycle())
        try:
            await workflow.wait_condition(lambda: body.done() or self._cancel_reason is not None)
        except asyncio.CancelledError:
            # Cancelled through Temporal (handle.cancel()): stop the work, record it, and
            # end the execution as cancelled.
            await self._stop(body)
            await self._record_end("cancelled")
            raise
        if not body.done():
            await self._stop(body)
            await self._record_end("cancelled")
            workflow.logger.info("study cancelled: %s", self._cancel_reason)
            return self._result("cancelled")
        try:
            result = body.result()
        except BaseException:
            await self._record_end("failed")
            raise
        await self._record_end("completed")
        return result

    async def _lifecycle(self) -> StudyWorkflowResult:
        study_id = self._input.study_id
        await self._transition("extracting")

        photos = await self._activity(
            "list_photos", ListPhotosInput(study_id=study_id), ListPhotosOutput, "list_photos"
        )
        appraisal_task = self._spawn(self._ingest_and_extract())
        await self._analyze_photos(list(photos.document_ids), self._revision)
        document_id = await appraisal_task

        # Gate 1: appraisal review.
        while True:
            decision = await self._await_decision("appraisal_review")
            if decision.decision == "approve":
                break
            new_revision, corrections = await self._reject(decision, "extracting")
            await self._extract(document_id, new_revision, corrections)

        # Gate 2: rooms review.
        while True:
            decision = await self._await_decision("rooms_review")
            if decision.decision == "approve":
                break
            new_revision, corrections = await self._reject(decision, "extracting")
            flagged = [c.entity_id for c in corrections if c.entity_type == "photo"]
            await self._analyze_photos(flagged, new_revision)

        # Assets, then gate 3: assets review.
        await self._transition("assets_processing")
        await self._process_assets(())
        while True:
            decision = await self._await_decision("assets_review")
            if decision.decision == "approve":
                break
            _, corrections = await self._reject(decision, "assets_processing")
            await self._process_assets(corrections)

        await self._transition("exporting")
        export = await self._activity(
            "export_report",
            ExportReportInput(
                study_id=study_id,
                revision=self._revision,
                idempotency_key=self._key(f"export_report:r{self._revision}"),
            ),
            ExportReportOutput,
            f"export_report:r{self._revision}",
        )
        await self._transition("completed")
        return self._result("completed", export)

    # ------------------------------------------------------------------ stages

    async def _ingest_and_extract(self) -> str | None:
        revision = self._revision
        ingested = await self._activity(
            "ingest_appraisal",
            IngestAppraisalInput(
                study_id=self._input.study_id,
                revision=revision,
                idempotency_key=self._key(f"ingest_appraisal:r{revision}"),
            ),
            IngestAppraisalOutput,
            f"ingest_appraisal:r{revision}",
        )
        await self._extract(ingested.document_id, revision, ())
        return ingested.document_id

    async def _extract(
        self,
        document_id: str | None,
        revision: int,
        corrections: tuple[CorrectionInput, ...],
    ) -> ExtractFieldsOutput:
        output = await self._activity(
            "extract_fields",
            ExtractFieldsInput(
                study_id=self._input.study_id,
                document_id=document_id,
                revision=revision,
                idempotency_key=self._key(f"extract_fields:r{revision}"),
                corrections=corrections,
            ),
            ExtractFieldsOutput,
            f"extract_fields:r{revision}",
        )
        if output.needs_review:
            self._mark_needs_review("appraisal_review")
        return output

    async def _analyze_photos(self, document_ids: list[str], revision: int) -> None:
        """Analyze photos in parallel, at most ``photo_concurrency`` at a time.

        A photo that fails permanently (retries exhausted or a non-retryable error) or
        that the budget cannot cover is flagged and the study continues. Budget accounting
        is optimistic: each activity is given the remaining budget when it starts, so the
        overspend is bounded by ``photo_concurrency`` times the cost of one photo.
        """
        if not document_ids:
            return
        semaphore = asyncio.Semaphore(self._input.photo_concurrency)
        localizing = asyncio.Semaphore(self._input.photo_concurrency)
        self._pending_photos += len(document_ids)
        tasks = [
            self._spawn(self._analyze_and_localize(semaphore, localizing, doc, revision))
            for doc in document_ids
        ]
        for completed in workflow.as_completed(tasks):
            await completed

    async def _analyze_and_localize(
        self,
        analyzing: asyncio.Semaphore,
        localizing: asyncio.Semaphore,
        document_id: str,
        rev: int,
    ) -> None:
        analyzed = await self._analyze_one(analyzing, document_id, rev)
        if analyzed and workflow.patched(LOCALIZE_PATCH):
            await self._localize_one(localizing, document_id, rev)

    async def _localize_one(self, semaphore: asyncio.Semaphore, document_id: str, rev: int) -> None:
        """Localize one analyzed photo; a failure is logged, never flagged or raised."""
        async with semaphore:
            try:
                output = await self._activity(
                    "localize_photo",
                    LocalizePhotoInput(
                        study_id=self._input.study_id,
                        document_id=document_id,
                        revision=rev,
                        idempotency_key=self._key(f"localize_photo:{document_id}:r{rev}"),
                        budget_remaining_usd=self._budget,
                    ),
                    LocalizePhotoOutput,
                    f"localize_photo:{document_id}:r{rev}",
                )
            except ActivityError as error:
                if isinstance(error.cause, CancelledError):
                    raise  # the study is being cancelled
                workflow.logger.warning(
                    "photo %s not localized: %s", document_id, error.cause or error
                )
                return
            self._budget -= output.cost_usd

    async def _analyze_one(self, semaphore: asyncio.Semaphore, document_id: str, rev: int) -> bool:
        """Analyze one photo; True when ``analyze_photo`` returned (even ``needs_review``)."""
        async with semaphore:
            try:
                if self._budget <= 0:
                    self._flag(document_id, budget=True)
                    return False
                output = await self._activity(
                    "analyze_photo",
                    AnalyzePhotoInput(
                        study_id=self._input.study_id,
                        document_id=document_id,
                        revision=rev,
                        idempotency_key=self._key(f"analyze_photo:{document_id}:r{rev}"),
                        budget_remaining_usd=self._budget,
                        simulate=self._input.simulate_photos.get(document_id),
                    ),
                    AnalyzePhotoOutput,
                    f"analyze_photo:{document_id}:r{rev}",
                )
            except ActivityError as error:
                if isinstance(error.cause, CancelledError):
                    raise  # the study is being cancelled; not a photo failure
                workflow.logger.warning("photo %s flagged: %s", document_id, error.cause or error)
                self._flag(document_id, budget=is_budget_exceeded(error))
                return False
            finally:
                self._pending_photos -= 1
                self._completed_photos += 1
            self._budget -= output.cost_usd
            if output.needs_review:
                self._flag(document_id, budget=False)
            return True

    async def _process_assets(self, corrections: tuple[CorrectionInput, ...]) -> None:
        revision = self._revision
        try:
            output = await self._activity(
                "process_assets",
                ProcessAssetsInput(
                    study_id=self._input.study_id,
                    revision=revision,
                    idempotency_key=self._key(f"process_assets:r{revision}"),
                    budget_remaining_usd=max(self._budget, 0.0),
                    corrections=corrections,
                ),
                ProcessAssetsOutput,
                f"process_assets:r{revision}",
            )
        except ActivityError as error:
            if not is_budget_exceeded(error):
                raise
            workflow.logger.warning("assets for revision %s exceed the budget", revision)
            self._mark_needs_review("assets_review")
            return
        self._budget -= output.cost_usd
        if output.needs_review:
            self._mark_needs_review("assets_review")

    async def _reject(
        self, decision: Decision, back_to: StageName
    ) -> tuple[int, tuple[CorrectionInput, ...]]:
        """Start the next revision: move the study back and carry corrected outputs over.

        Returns the new revision and the corrections to apply when recomputing. If
        ``apply_corrections`` refuses the corrections (a target that is not in the rejected
        revision, a value that does not parse: reviewer input the validator cannot check
        without the database), the revision starts without them, the stage is flagged in
        ``needs_review_stages``, and the gate asks again; the study does not fail.
        """
        old_revision = self._revision
        new_revision = old_revision + 1
        await self._transition(back_to, new_revision=new_revision)
        corrections = decision.corrections
        try:
            await self._apply_corrections(decision.stage, old_revision, corrections, "")
        except ActivityError as error:
            if not corrections or not _is_validation_error(error):
                raise
            workflow.logger.warning(
                "corrections of review %s refused: %s", decision.review_id, error.cause
            )
            self._mark_needs_review(decision.stage)
            corrections = ()
            await self._apply_corrections(decision.stage, old_revision, (), ":uncorrected")
        return new_revision, corrections

    async def _apply_corrections(
        self,
        stage: ReviewStage,
        old_revision: int,
        corrections: tuple[CorrectionInput, ...],
        suffix: str,
    ) -> None:
        await self._activity(
            "apply_corrections",
            ApplyCorrectionsInput(
                study_id=self._input.study_id,
                stage=stage,
                from_revision=old_revision,
                to_revision=old_revision + 1,
                corrections=corrections,
            ),
            ApplyCorrectionsOutput,
            f"apply_corrections:{stage}:r{old_revision}{suffix}",
        )

    # ------------------------------------------------------------------ review gates

    async def _await_decision(self, stage: ReviewStage) -> Decision:
        """Wait for the engineer, reminding every ``review_reminder_days``.

        After ``review_deadline_days`` without a decision the study is marked stalled once
        and the wait continues: a late decision still completes the study.
        """
        self._pending = None
        # Set before the DB transition: a decision delivered while the transition activity
        # runs is validated against this gate and picked up by the wait below.
        self._awaiting = stage
        await self._transition(stage)
        started = workflow.now()
        self._waiting_since = started
        reminder_every = timedelta(days=self._input.review_reminder_days)
        deadline = started + timedelta(days=self._input.review_deadline_days)
        next_reminder = started + reminder_every
        while self._pending is None:
            wake = next_reminder if self._stalled else min(next_reminder, deadline)
            timeout = max(wake - workflow.now(), timedelta(seconds=1))
            try:
                await workflow.wait_condition(lambda: self._pending is not None, timeout=timeout)
            except TimeoutError:
                now = workflow.now()
                if not self._stalled and now >= deadline:
                    await self._activity(
                        "mark_stalled",
                        MarkStalledInput(
                            study_id=self._input.study_id, stage=stage, revision=self._revision
                        ),
                        None,
                        f"mark_stalled:{stage}:r{self._revision}",
                    )
                    self._stalled = True
                if now >= next_reminder:
                    waited = now - started
                    await self._activity(
                        "notify_review_pending",
                        NotifyInput(
                            study_id=self._input.study_id,
                            stage=stage,
                            revision=self._revision,
                            waiting_days=waited.days,
                        ),
                        None,
                        f"notify_review_pending:{stage}:r{self._revision}:d{waited.days}",
                    )
                    # From now, not from the missed slot: a worker outage collapses the
                    # missed reminders into this one instead of a burst of catch-ups.
                    next_reminder = now + reminder_every
        decision = self._pending
        self._awaiting = None
        self._waiting_since = None
        self._stalled = False
        return decision

    # ------------------------------------------------------------------ helpers

    async def _transition(self, to_stage: StageName, *, new_revision: int | None = None) -> None:
        revision = new_revision if new_revision is not None else self._revision
        await self._activity(
            "transition",
            TransitionInput(
                study_id=self._input.study_id,
                org_id=self._input.org_id,
                from_stage=self._stage,
                to_stage=to_stage,
                revision=revision,
                from_revision=self._revision,
            ),
            None,
            f"transition:{to_stage}:r{revision}",
        )
        self._stage = to_stage
        self._revision = revision
        workflow.upsert_search_attributes([STAGE_ATTR.value_set(to_stage)])

    @overload
    async def _activity(
        self, name: str, arg: BaseModel, result_type: type[_Out], activity_id: str
    ) -> _Out: ...

    @overload
    async def _activity(
        self, name: str, arg: BaseModel, result_type: None, activity_id: str
    ) -> None: ...

    async def _activity(
        self,
        name: str,
        arg: BaseModel,
        result_type: type[_Out] | None,
        activity_id: str,
    ) -> _Out | None:
        """Run activity ``name`` with its options from ``activity_options``."""
        stoppable = name != "record_run_end"
        if stoppable and self._stopping:
            raise asyncio.CancelledError(f"study stopping; {name} not scheduled")
        options = options_for(name)
        result: Any = await workflow.execute_activity(
            name,
            arg,
            result_type=result_type,
            activity_id=activity_id,
            start_to_close_timeout=options.start_to_close,
            schedule_to_close_timeout=options.schedule_to_close,
            heartbeat_timeout=options.heartbeat,
            retry_policy=options.retry_policy,
            cancellation_type=ACTIVITY_CANCELLATION,
        )
        if stoppable and self._stopping:
            # The activity completed although cancellation was requested; stop here.
            raise asyncio.CancelledError(f"study stopping after {name}")
        return cast("_Out | None", result)

    def _key(self, activity_id: str) -> str:
        """Idempotency key for an activity: ``{workflow_id}:{run_id}:{activity_id}``."""
        info = workflow.info()
        return f"{info.workflow_id}:{info.run_id}:{activity_id}"

    def _spawn(self, coro: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        task = asyncio.create_task(coro)
        self._children.append(task)
        return task

    async def _stop(self, body: asyncio.Task[StudyWorkflowResult]) -> None:
        """Cancel the lifecycle and every task it spawned (their activities are cancelled)."""
        self._stopping = True
        tasks = [body, *self._children]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, ActivityError):
                pass

    async def _record_end(self, outcome: Literal["completed", "cancelled", "failed"]) -> None:
        info = workflow.info()
        await self._activity(
            "record_run_end",
            RunEndInput(
                study_id=self._input.study_id,
                workflow_id=info.workflow_id,
                run_id=info.run_id,
                outcome=outcome,
            ),
            None,
            "record_run_end",
        )

    def _flag(self, document_id: str, *, budget: bool) -> None:
        if document_id not in self._flagged:
            self._flagged.append(document_id)
        if budget:
            self._mark_needs_review("rooms_review")

    def _mark_needs_review(self, stage: str) -> None:
        if stage not in self._needs_review_stages:
            self._needs_review_stages.append(stage)

    def _result(
        self,
        outcome: Literal["completed", "cancelled"],
        export: ExportReportOutput | None = None,
    ) -> StudyWorkflowResult:
        return StudyWorkflowResult(
            outcome=outcome,
            stage=self._stage,
            revision=self._revision,
            export_uri=export.export_uri if export else None,
            export_sha256=export.sha256 if export else None,
            flagged_photos=tuple(self._flagged),
            cancel_reason=self._cancel_reason,
        )


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_DECISION, non_retryable=True)


def _is_uuid(value: str) -> bool:
    """Canonical 8-4-4-4-12 hex form, checked without importing ``uuid`` semantics."""
    parts = value.split("-")
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in "".join(parts))
