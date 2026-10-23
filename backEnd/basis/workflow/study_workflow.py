"""The StudyWorkflow: one durable execution per study (S2_SPEC section 3).

Shape of a run::

    record_run_start
    transition -> extracting
    in parallel: ingest_appraisal -> extract_fields ;
                 analyze_photo -> localize_photo, x N (bounded) ; reconcile_rooms
    gate appraisal_review   (reject: apply_corrections, extract_fields again, revision + 1)
    gate rooms_review       (the grouping; reject: apply_corrections, the rooms re-voted)
    transition -> components_processing ; reconcile_rooms (and every localize_photo done)
    gate components_review  (reject: apply_corrections, re-analyze and re-localize flagged
                             photos only, reconcile_rooms)
    transition -> assets_processing ; process_assets
    gate assets_review      (reject: apply_corrections, process_assets again)
    (every localize_photo done) ; transition -> exporting ; export_report ;
    transition -> completed ; record_run_end

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
  stage waits for every localization before the next gate (until
  ``localize-background-v1``). A localization that fails is
  logged and ignored; it never flags the photo. Executions recorded before the patch
  replay without it (tests/lifecycle/histories).
* ``reconcile-rooms-v1`` (EVAL_REPORT section 16.5): after a revision's photos are analyzed
  and localized (the first photo stage, and the re-analysis after a rooms rejection),
  ``reconcile_rooms`` builds the room inventories the assets are built from. A failure of
  it is logged and flags the rooms review; the study continues on the per-photo rows.
  The activity itself is a no-op when ``ROOM_RECONCILE`` is off, so the command sequence
  does not depend on settings.
* ``stage-failure-retry-v1`` (EVAL_REPORT section 27, item 3): the photo stage runs in the
  background while the appraisal is ingested and extracted, and an extraction that fails
  is noticed at once: ``record_failure`` sets the study ``failed`` with the reason, the
  photo stage schedules nothing more (the few photo activities already running finish
  and keep their rows, which the retry reuses; cancelling them instead races with their
  completion), and the workflow waits for the ``retry`` update
  (``POST /v1/studies/{id}/retry``); then it clears the failure and runs the stage again
  (activity ids get a ``:retry<n>`` suffix, so their idempotency keys are fresh).
  ``process_assets`` (other than an exhausted budget) and ``export_report`` failures are
  held the same way instead of failing the execution.
  Before the patch an extraction failure surfaced only after the photo stage and failed
  the whole execution, which could not be restarted.
* ``appraisal-gate-early-v1`` (EVAL_REPORT section 27, item 4; only with the patch above):
  the appraisal review opens as soon as the extraction is done, while the photo stage
  (analysis, SAM 3 localization, the room step) continues in the background; the rooms
  review opens only after it. A rejection of the appraisal waits for the photo stage
  before it starts the next revision (the revision carries the photo outputs over, so
  they must be complete). Before the patch the appraisal review waited for the photo
  stage, which was 74% of the live run's wall time under Replicate's throttle.
* ``provider-account-stop-v1`` (EVAL_REPORT section 26.12; only with ``stage-failure-retry-v1``):
  a ``ProviderAccountError`` from ``analyze_photo`` or ``reconcile_rooms`` (the provider
  refused the account: no credit or quota, a bad key) fails the study instead of flagging
  the photo or the rooms review. The photo stage schedules no more photos, the ones already
  running finish, and the failure is recorded at the study's current stage with the reason;
  the ``retry`` update re-runs the photos the error stopped (activity ids with a
  ``:retry<n>`` suffix) or the room step, and the stage continues. The appraisal review does
  not open while such a failure is held (the stage change would hide the failed status),
  and a failure is never recorded while a stage change is in flight. Before the patch every
  photo after the account ran dry was flagged as a failed analysis and the study went on
  to review with them (156 photos in one run).
* ``localize-background-v1`` (EVAL_REPORT section 30): at most ``localize_concurrency``
  localizations per study (before: ``photo_concurrency``), and, unless
  ``rooms_gate_waits_for_localization``, they run behind the rooms review: the photo stage
  is done when every photo is analyzed and the room step ran, and the rooms review opens
  then (no step after it reads SAM 3's output; the masks appear in the review as each
  photo's localization is recorded). A rejection at any gate first waits for the
  localizations still running (the next revision carries the photos' objects over), so
  does the drain after a failed extraction, and the study exports only once every
  localization is recorded. Executions recorded before the patch replay without it.
* ``components-gate-v1`` (v0.12; only with every patch above, which an execution that records
  it always has): the single rooms review is split in two. It is decided first, before
  ``record_run_start``, which records it on the study (``studies.components_gate``) so the
  API knows which gates the study has. The photo stage analyzes and localizes the photos and
  re-votes the file-name rooms (``reconcile_rooms`` with ``vote_only``: no model call)
  instead of running the room step, and the rooms review opens on the grouping: room fields,
  merges and photo moves (``photo_room``). A rejection starts the next revision with the
  grouping corrected and re-voted, re-analyzing no photo (``analyze_photo`` does not depend
  on the room), and the rooms review opens again. On approval the study moves to
  ``components_processing`` and the room step runs on the approved grouping while the
  localizations still running finish; the components review opens when both are done. It
  takes ``component`` (label, material, attachment, count), ``room_component`` and ``photo``
  (re-analysis) corrections; a rejection re-analyzes and re-localizes the flagged photos in
  the rooms the engineer approved (``keep_room``), runs the room step again (only rooms whose
  photos changed are rebuilt) and opens the components review again. The input's
  ``components_gate=False`` keeps the single rooms review (tests run that flow afresh);
  executions recorded before the patch replay with it, and the API keeps their manual edits
  at ``rooms_review``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
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
        PROVIDER_ACCOUNT_ERROR,
        VALIDATION_ERROR,
        options_for,
    )
    from basis.workflow.contracts import (
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
        ReconcileRoomsInput,
        ReconcileRoomsOutput,
        RecordFailureInput,
        RetryAck,
        ReviewStage,
        RunEndInput,
        RunStartInput,
        StageName,
        StudyStatusView,
        StudyWorkflowInput,
        StudyWorkflowResult,
        TransitionInput,
        correction_types,
    )

WORKFLOW_NAME: Final = "StudyWorkflow"
DECIDE_UPDATE: Final = "decide"
RETRY_UPDATE: Final = "retry"
INVALID_RETRY: Final = "InvalidRetry"
STATUS_QUERY: Final = "status"
CANCEL_SIGNAL: Final = "cancel_study"
INVALID_DECISION: Final = "InvalidDecision"

STUDY_ID_ATTR: Final = SearchAttributeKey.for_keyword("StudyId")
STAGE_ATTR: Final = SearchAttributeKey.for_keyword("Stage")
ORG_ID_ATTR: Final = SearchAttributeKey.for_keyword("OrgId")
SEARCH_ATTRIBUTE_KEYS: Final = (STUDY_ID_ATTR, STAGE_ATTR, ORG_ID_ATTR)

ALREADY_APPLIED: Final = "already applied"
LOCALIZE_PATCH: Final = "localize-photos-v1"
RECONCILE_ROOMS_PATCH: Final = "reconcile-rooms-v1"
STAGE_RETRY_PATCH: Final = "stage-failure-retry-v1"
EARLY_GATE_PATCH: Final = "appraisal-gate-early-v1"
ACCOUNT_STOP_PATCH: Final = "provider-account-stop-v1"
LOCALIZE_BACKGROUND_PATCH: Final = "localize-background-v1"
COMPONENTS_GATE_PATCH: Final = "components-gate-v1"
MAX_FAILURE_CHARS: Final = 1000

_Out = TypeVar("_Out", bound=BaseModel)
_T = TypeVar("_T")


def workflow_id_for(study_id: str) -> str:
    """The one workflow id a study ever has (S2_SPEC section 1)."""
    return f"study-{study_id}"


def _is_validation_error(error: ActivityError) -> bool:
    cause = error.cause
    return isinstance(cause, ApplicationError) and cause.type == VALIDATION_ERROR


def is_account_error(error: BaseException) -> bool:
    """True when an activity failed because a provider refused the account (no credit or
    quota, a bad key): retrying is pointless until someone fixes the account."""
    cause = error.cause if isinstance(error, ActivityError) else error
    return isinstance(cause, ApplicationError) and cause.type == PROVIDER_ACCOUNT_ERROR


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
        # Patch stage-failure-retry-v1: the failed stage, why, and the retry request.
        self._stage_retry = False
        self._failed_stage: StageName | None = None
        self._failure: str | None = None
        self._retry_requested = False
        self._failures = 0
        # Set while the photo stage drains after a failed extraction: no photo activity
        # is scheduled any more; the ones already running finish and keep their rows.
        self._photos_stopping = False
        # Patch provider-account-stop-v1: the first account error of the photo stage, the
        # photos it stopped (run again after the retry), how many photo-stage failures are
        # held, and whether a stage change is in flight (no failure is recorded meanwhile).
        self._account_stop = False
        self._account_error: ActivityError | None = None
        self._account_retry: list[str] = []
        self._photo_holds = 0
        self._transitioning = False
        # Patch localize-background-v1: SAM 3 slots for the study, whether localizations
        # run behind the rooms review, and the ones started and not yet awaited.
        self._localize_slots: asyncio.Semaphore | None = None
        self._localize_background = False
        self._localizations: list[asyncio.Task[None]] = []
        # Patch components-gate-v1: whether the components review follows the rooms review,
        # and whether the grouping is approved (a re-analysis then keeps the photo's room).
        self._components_gate = False
        self._grouping_approved = False

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
            failed_stage=self._failed_stage,
            failure=self._failure,
        )

    @workflow.update(name=RETRY_UPDATE)
    def retry(self) -> RetryAck:
        """Run the failed stage again (``POST /v1/studies/{id}/retry``). Idempotent: a
        second request before the stage restarts answers ``already_requested``."""
        assert self._failed_stage is not None  # the validator
        already = self._retry_requested
        self._retry_requested = True
        return RetryAck(
            stage=self._failed_stage, revision=self._revision, already_requested=already
        )

    @retry.validator
    def validate_retry(self) -> None:
        if self._cancel_reason is not None:
            raise ApplicationError(
                "the study was cancelled", type=INVALID_RETRY, non_retryable=True
            )
        if self._failed_stage is None:
            raise ApplicationError(
                f"no stage has failed (stage {self._stage})", type=INVALID_RETRY, non_retryable=True
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
        allowed = correction_types(decision.stage, components_gate=self._components_gate)
        for correction in decision.corrections:
            if correction.entity_type not in allowed:
                raise _invalid(
                    f"{decision.stage} does not accept corrections of type "
                    f"{correction.entity_type!r} (allowed: {', '.join(sorted(allowed))})"
                )
            if not _is_uuid(correction.entity_id):
                raise _invalid(f"correction entity_id {correction.entity_id!r} is not a UUID")
            if correction.entity_type == "photo_room" and (
                set(correction.after) != {"room_id"}
                or not _is_uuid(str(correction.after["room_id"]))
            ):
                raise _invalid("a photo_room correction takes exactly a room_id (a UUID)")
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
        # Decided before anything is recorded, so record_run_start stores it on the study.
        self._components_gate = input.components_gate and workflow.patched(COMPONENTS_GATE_PATCH)
        await self._activity(
            "record_run_start",
            RunStartInput(
                study_id=input.study_id,
                workflow_id=info.workflow_id,
                run_id=info.run_id,
                workflow_version=input.workflow_version,
                input_revision=input.input_revision,
                components_gate=self._components_gate,
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
        self._stage_retry = workflow.patched(STAGE_RETRY_PATCH)
        self._account_stop = self._stage_retry and workflow.patched(ACCOUNT_STOP_PATCH)
        if workflow.patched(LOCALIZE_BACKGROUND_PATCH):
            self._localize_slots = asyncio.Semaphore(self._input.localize_concurrency)
            self._localize_background = not self._input.rooms_gate_waits_for_localization
        photo_task: asyncio.Task[None] | None = None
        if self._stage_retry:
            document_id, photo_task = await self._extract_with_photos(list(photos.document_ids))
            if not workflow.patched(EARLY_GATE_PATCH):
                await photo_task
        else:
            appraisal_task = self._spawn(self._ingest_and_extract())
            photo_revision = self._revision
            await self._analyze_photos(list(photos.document_ids), photo_revision)
            await self._reconcile_rooms(photo_revision)
            document_id = await appraisal_task

        # Gate 1: appraisal review; not while the photo stage holds an account failure
        # (moving the study to the gate would clear its failed status).
        if self._account_stop:
            await workflow.wait_condition(lambda: self._photo_holds == 0)
        while True:
            decision = await self._await_decision("appraisal_review")
            if decision.decision == "approve":
                break
            if photo_task is not None:
                await photo_task  # the next revision carries the photo outputs over
            new_revision, corrections = await self._reject(decision, "extracting")
            await self._held("extracting", self._extractor(document_id, new_revision, corrections))

        # Gate 2: rooms review, once the photo stage is done.
        if photo_task is not None:
            await photo_task
        if self._components_gate:
            await self._grouping_then_components()
        while not self._components_gate:
            decision = await self._await_decision("rooms_review")
            if decision.decision == "approve":
                break
            new_revision, corrections = await self._reject(decision, "extracting")
            flagged = [c.entity_id for c in corrections if c.entity_type == "photo"]
            await self._photo_pass(flagged, new_revision)

        # Assets, then gate 3: assets review.
        await self._transition("assets_processing")
        await self._process_assets(())
        while True:
            decision = await self._await_decision("assets_review")
            if decision.decision == "approve":
                break
            _, corrections = await self._reject(decision, "assets_processing")
            await self._process_assets(corrections)

        await self._localized()  # the study ends with every localization recorded
        await self._transition("exporting")
        export = await self._held("exporting", self._export)
        await self._transition("completed")
        return self._result("completed", export)

    # ------------------------------------------------------------------ stages

    async def _grouping_then_components(self) -> None:
        """Patch ``components-gate-v1``: the rooms review (the grouping), the room step on the
        approved grouping, the components review (module doc)."""
        while True:
            decision = await self._await_decision("rooms_review")
            if decision.decision == "approve":
                break
            new_revision, _ = await self._reject(decision, "extracting")
            await self._reconcile_rooms(new_revision, vote_only=True)
        self._grouping_approved = True
        await self._transition("components_processing")
        await self._prepare_components([], self._revision)
        while True:
            decision = await self._await_decision("components_review")
            if decision.decision == "approve":
                break
            new_revision, corrections = await self._reject(decision, "components_processing")
            flagged = [c.entity_id for c in corrections if c.entity_type == "photo"]
            await self._prepare_components(flagged, new_revision)

    async def _prepare_components(self, photo_ids: list[str], revision: int) -> None:
        """Re-analyze ``photo_ids`` in their approved rooms, run the room step, and wait for
        every localization (the components review shows the photos' outlines)."""
        await self._photo_pass(photo_ids, revision)
        await self._localized()

    async def _export(self, suffix: str = "") -> ExportReportOutput:
        activity_id = f"export_report:r{self._revision}{suffix}"
        return await self._activity(
            "export_report",
            ExportReportInput(
                study_id=self._input.study_id,
                revision=self._revision,
                idempotency_key=self._key(activity_id),
            ),
            ExportReportOutput,
            activity_id,
        )

    async def _extract_with_photos(
        self, photo_ids: list[str]
    ) -> tuple[str | None, asyncio.Task[None]]:
        """Patch ``stage-failure-retry-v1``: the photo stage in the background, the
        appraisal in front; a failed extraction drains the photo stage, is held for a
        retry (:meth:`_hold`), and the photo stage runs again after the retry. Returns
        when the extraction is done, with the photo stage's task (still running or done)."""
        revision = self._revision
        photo_task = self._spawn(self._photo_stage(photo_ids, revision))
        attempt = 0
        while True:
            suffix = f":retry{attempt}" if attempt else ""
            try:
                document_id = await self._ingest_and_extract(suffix)
                break
            except ActivityError as error:
                if isinstance(error.cause, CancelledError) or self._stopping:
                    raise
                self._photos_stopping = True
                await self._hold("extracting", error, self._spawn(self._drained(photo_task)))
                attempt += 1
                self._photos_stopping = False
                self._pending_photos = 0
                self._completed_photos = 0
                self._account_error, self._account_retry = None, []
                photo_task = self._spawn(self._photo_stage(photo_ids, revision))
        return document_id, photo_task

    async def _drained(self, photo_task: asyncio.Task[None]) -> None:
        """The photo stage and every localization it started have finished."""
        await photo_task
        await self._localized()

    async def _localized(self) -> None:
        """Wait for the localizations running behind the review (patch
        ``localize-background-v1``); none are pending without it."""
        while self._localizations:
            await self._localizations.pop(0)

    async def _photo_stage(self, photo_ids: list[str], revision: int) -> None:
        # Patch components-gate-v1: the room step waits for the approved grouping.
        await self._photo_pass(photo_ids, revision, vote_only=self._components_gate)

    async def _photo_pass(
        self, photo_ids: list[str], revision: int, *, vote_only: bool = False
    ) -> None:
        """Analyze ``photo_ids`` and run the room step (``vote_only``: the rooms' vote alone).
        With patch ``provider-account-stop-v1`` an account error stops the photos, is held
        for a retry, and the photos it stopped run again (fresh activity ids) before the room
        step; without it (or with no account error) this is analyze, then the room step."""
        todo, attempt = photo_ids, 0
        while True:
            await self._analyze_photos(todo, revision, f":retry{attempt}" if attempt else "")
            if self._photos_stopping:
                return  # drained after a failed extraction; the retry runs the stage again
            error = self._account_error
            if error is None:
                break
            todo, self._account_error, self._account_retry = self._account_retry, None, []
            await self._hold_photos(error)
            if self._photos_stopping:
                return  # the extraction failed meanwhile; its retry reruns the stage
            attempt += 1
        await self._reconcile_rooms(revision, vote_only=vote_only)

    async def _hold_photos(self, error: ActivityError) -> None:
        """Hold a photo-stage failure at the study's current stage, once no stage change
        is in flight; the appraisal gate waits while one is held."""
        self._photo_holds += 1
        try:
            await workflow.wait_condition(lambda: not self._transitioning)
            await self._hold(self._stage, error)
        finally:
            self._photo_holds -= 1

    async def _held(self, stage: StageName, run: Callable[[str], Awaitable[_T]]) -> _T:
        """Run a stage; with patch ``stage-failure-retry-v1`` a failure (not a
        cancellation) is held for a retry and the stage runs again, else it propagates."""
        attempt = 0
        while True:
            try:
                return await run(f":retry{attempt}" if attempt else "")
            except ActivityError as error:
                if not self._stage_retry or isinstance(error.cause, CancelledError):
                    raise
                if self._stopping:
                    raise
                await self._hold(stage, error)
                attempt += 1

    async def _hold(
        self, stage: StageName, error: ActivityError, drain: asyncio.Task[Any] | None = None
    ) -> None:
        """Record the failure, wait for the ``retry`` update (and for ``drain``, the photo
        stage finishing the activities it had started), clear the failure."""
        self._failures += 1
        number = self._failures
        reason = _failure_reason(error)
        workflow.logger.warning("stage %s failed, waiting for a retry: %s", stage, reason)
        await self._activity(
            "record_failure",
            RecordFailureInput(
                study_id=self._input.study_id, stage=stage, revision=self._revision, reason=reason
            ),
            None,
            f"record_failure:{stage}:r{self._revision}:{number}",
        )
        # Exposed (status query, retry validator) once the study row says failed.
        self._failed_stage, self._failure, self._retry_requested = stage, reason, False
        await workflow.wait_condition(lambda: self._retry_requested)
        if drain is not None:
            await drain
        await self._activity(
            "record_failure",
            RecordFailureInput(study_id=self._input.study_id, stage=stage, revision=self._revision),
            None,
            f"record_failure:{stage}:r{self._revision}:{number}:cleared",
        )
        self._failed_stage, self._failure = None, None

    def _extractor(
        self, document_id: str | None, revision: int, corrections: tuple[CorrectionInput, ...]
    ) -> Callable[[str], Awaitable[ExtractFieldsOutput]]:
        def run(suffix: str) -> Awaitable[ExtractFieldsOutput]:
            return self._extract(document_id, revision, corrections, suffix)

        return run

    async def _ingest_and_extract(self, suffix: str = "") -> str | None:
        revision = self._revision
        ingested = await self._activity(
            "ingest_appraisal",
            IngestAppraisalInput(
                study_id=self._input.study_id,
                revision=revision,
                idempotency_key=self._key(f"ingest_appraisal:r{revision}{suffix}"),
            ),
            IngestAppraisalOutput,
            f"ingest_appraisal:r{revision}{suffix}",
        )
        await self._extract(ingested.document_id, revision, (), suffix)
        return ingested.document_id

    async def _extract(
        self,
        document_id: str | None,
        revision: int,
        corrections: tuple[CorrectionInput, ...],
        suffix: str = "",
    ) -> ExtractFieldsOutput:
        output = await self._activity(
            "extract_fields",
            ExtractFieldsInput(
                study_id=self._input.study_id,
                document_id=document_id,
                revision=revision,
                idempotency_key=self._key(f"extract_fields:r{revision}{suffix}"),
                corrections=corrections,
            ),
            ExtractFieldsOutput,
            f"extract_fields:r{revision}{suffix}",
        )
        if output.needs_review:
            self._mark_needs_review("appraisal_review")
        return output

    async def _analyze_photos(
        self, document_ids: list[str], revision: int, suffix: str = ""
    ) -> None:
        """Analyze photos in parallel, at most ``photo_concurrency`` at a time.

        A photo that fails permanently (retries exhausted or a non-retryable error) or
        that the budget cannot cover is flagged and the study continues (a provider account
        error, with patch ``provider-account-stop-v1``, is not a photo failure: see
        :meth:`_photo_pass`). ``suffix`` is appended to the analysis activity ids (a
        retry after an account error). Budget accounting
        is optimistic: each activity is given the remaining budget when it starts, so the
        overspend is bounded by ``photo_concurrency`` times the cost of one photo.
        """
        if not document_ids:
            return
        semaphore = asyncio.Semaphore(self._input.photo_concurrency)
        localizing = self._localize_slots or asyncio.Semaphore(self._input.photo_concurrency)
        self._pending_photos += len(document_ids)
        tasks = [
            self._spawn(self._analyze_and_localize(semaphore, localizing, doc, revision, suffix))
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
        suffix: str = "",
    ) -> None:
        analyzed = await self._analyze_one(analyzing, document_id, rev, suffix)
        if analyzed and not self._photos_stopping and workflow.patched(LOCALIZE_PATCH):
            if self._localize_background:
                localize = self._localize_one(localizing, document_id, rev)
                self._localizations.append(self._spawn(localize))
            else:
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

    async def _analyze_one(
        self, semaphore: asyncio.Semaphore, document_id: str, rev: int, suffix: str = ""
    ) -> bool:
        """Analyze one photo; True when ``analyze_photo`` returned (even ``needs_review``)."""
        activity_id = f"analyze_photo:{document_id}:r{rev}{suffix}"
        async with semaphore:
            try:
                if self._photos_stopping:
                    return False  # draining after a failed extraction: not scheduled
                if self._account_error is not None:
                    self._account_retry.append(document_id)  # runs after the retry
                    self._completed_photos -= 1  # not done: the finally counts it
                    return False
                if self._budget <= 0:
                    self._flag(document_id, budget=True)
                    return False
                output = await self._activity(
                    "analyze_photo",
                    AnalyzePhotoInput(
                        study_id=self._input.study_id,
                        document_id=document_id,
                        revision=rev,
                        idempotency_key=self._key(activity_id),
                        budget_remaining_usd=self._budget,
                        simulate=self._input.simulate_photos.get(document_id),
                        keep_room=self._grouping_approved,
                    ),
                    AnalyzePhotoOutput,
                    activity_id,
                )
            except ActivityError as error:
                if isinstance(error.cause, CancelledError):
                    raise  # the study is being cancelled; not a photo failure
                if self._account_stop and is_account_error(error):
                    workflow.logger.warning("photo %s stopped: %s", document_id, error.cause)
                    if self._account_error is None:
                        self._account_error = error
                    self._account_retry.append(document_id)
                    self._completed_photos -= 1  # not done: the finally counts it
                    return False
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

    async def _reconcile_rooms(self, revision: int, *, vote_only: bool = False) -> None:
        """The room step for ``revision`` (patch ``reconcile-rooms-v1``; ``vote_only``: the
        rooms' vote alone, patch ``components-gate-v1``); a failure flags the review that
        shows its output and is otherwise ignored (the per-photo rows stand)."""
        if not workflow.patched(RECONCILE_ROOMS_PATCH):
            return
        review = "rooms_review" if vote_only else self._photos_review()
        step = f"reconcile_rooms:r{revision}" + (":vote" if vote_only else "")
        attempt = 0
        while True:
            activity_id = step + (f":retry{attempt}" if attempt else "")
            try:
                output = await self._activity(
                    "reconcile_rooms",
                    ReconcileRoomsInput(
                        study_id=self._input.study_id,
                        revision=revision,
                        idempotency_key=self._key(activity_id),
                        budget_remaining_usd=max(self._budget, 0.0),
                        vote_only=vote_only,
                    ),
                    ReconcileRoomsOutput,
                    activity_id,
                )
                break
            except ActivityError as error:
                if isinstance(error.cause, CancelledError):
                    raise  # the study is being cancelled
                if self._account_stop and is_account_error(error) and not self._photos_stopping:
                    await self._hold_photos(error)  # patch provider-account-stop-v1
                    if self._photos_stopping:
                        return  # the extraction failed meanwhile; its retry reruns the stage
                    attempt += 1
                    continue
                workflow.logger.warning(
                    "rooms of revision %s not reconciled: %s", revision, error.cause or error
                )
                self._mark_needs_review(review)
                return
        self._budget -= output.cost_usd
        if output.needs_review:
            self._mark_needs_review(review)

    async def _process_assets(self, corrections: tuple[CorrectionInput, ...]) -> None:
        revision = self._revision
        output = await self._held(
            "assets_processing",
            lambda suffix: self._assets_once(revision, corrections, suffix),
        )
        if output is None:
            workflow.logger.warning("assets for revision %s exceed the budget", revision)
            self._mark_needs_review("assets_review")
            return
        self._budget -= output.cost_usd
        if output.needs_review:
            self._mark_needs_review("assets_review")

    async def _assets_once(
        self, revision: int, corrections: tuple[CorrectionInput, ...], suffix: str
    ) -> ProcessAssetsOutput | None:
        """One ``process_assets``; ``None`` when the budget cannot cover it."""
        activity_id = f"process_assets:r{revision}{suffix}"
        try:
            return await self._activity(
                "process_assets",
                ProcessAssetsInput(
                    study_id=self._input.study_id,
                    revision=revision,
                    idempotency_key=self._key(activity_id),
                    budget_remaining_usd=max(self._budget, 0.0),
                    corrections=corrections,
                ),
                ProcessAssetsOutput,
                activity_id,
            )
        except ActivityError as error:
            if not is_budget_exceeded(error):
                raise
            return None

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
        # The new revision copies the localized objects of the photos it does not
        # re-analyze, so every localization of this one must be recorded first.
        await self._localized()
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
                components_gate=self._components_gate,
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
        self._transitioning = True
        try:
            await self._transition_activity(to_stage, revision)
        finally:
            self._transitioning = False
        self._stage = to_stage
        self._revision = revision
        workflow.upsert_search_attributes([STAGE_ATTR.value_set(to_stage)])

    async def _transition_activity(self, to_stage: StageName, revision: int) -> None:
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
            self._mark_needs_review(self._photos_review())

    def _photos_review(self) -> ReviewStage:
        """The review that shows the photos' components (patch ``components-gate-v1``)."""
        return "components_review" if self._components_gate else "rooms_review"

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


def _failure_reason(error: ActivityError) -> str:
    """``<activity>: <error type>: <message>`` of a failed activity, for the reviewer."""
    cause = error.cause
    detail = (
        f"{cause.type}: {cause.message}"
        if isinstance(cause, ApplicationError)
        else str(cause or error)
    )
    return f"{error.activity_type}: {detail}"[:MAX_FAILURE_CHARS]


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_DECISION, non_retryable=True)


def _is_uuid(value: str) -> bool:
    """Canonical 8-4-4-4-12 hex form, checked without importing ``uuid`` semantics."""
    parts = value.split("-")
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in "".join(parts))
