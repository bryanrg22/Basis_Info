# Stage 2 specification: Temporal StudyWorkflow, activities, worker, outbox relay

Companion to BUILD_PLAN §1, §6.3, §6.9, §13.1 and S1_SPEC. Stage 2 makes the business process real and durable. Activity bodies are deliberate stubs that write real rows (so the lifecycle, idempotency, and recovery are testable end to end); Stage 3 replaces the bodies with the extraction, vision, retrieval, and classification pipeline without changing the workflow.

## 1. Fixed decisions (from research, BUILD_PLAN §13.1)

- `temporalio==1.34.0`; workflow files import only the SDK, stdlib, and `basis.workflow.contracts`.
- Workflow id `study-{study_id}`; start with `id_conflict_policy=USE_EXISTING`, `id_reuse_policy=REJECT_DUPLICATE`; task queue `basis-studies`.
- Engineer decisions arrive as **updates with validators** (`@workflow.update` + `@decision.validator`). The API never calls Temporal directly for decisions: it writes the review and an `outbox` row in one transaction (S1), and the **outbox relay** delivers the update. A relay delivery is idempotent because the update payload carries the review id and the workflow ignores a review it has already applied.
- Every wait: `workflow.wait_condition(..., timeout=timedelta(days=7))`; on timeout run `notify_review_pending` and wait again; after `review_deadline_days` (default 30) mark the study `stalled` via `mark_stalled` and keep waiting (a late decision still works).
- Activities: `start_to_close_timeout` always set; retry policy explicit (`maximum_attempts=3` for provider-calling activities, `5` for DB-only; `non_retryable_error_types=["ValidationError","BudgetExceededError"]`); heartbeat every ≤15 s inside any activity that loops over photos or pages.
- Idempotency key for an activity = `f"{workflow_id}:{run_id}:{activity_id}"` written to `activity_results` by the activity itself (claim → run → complete), so a retried activity skips completed work.
- Parallel photo analysis: `workflow.as_completed` over `execute_activity(analyze_photo, …)` with a workflow-side semaphore of `photo_concurrency` (default 5).
- Versioning: `workflow.patched("<id>")` for every behavior change after the first release; worker started with `deployment_config` (worker versioning, auto-upgrade) when `settings.temporal_worker_versioning` is true; replay tests over recorded histories in CI.
- Search attributes: `StudyId` (Keyword), `Stage` (Keyword), `OrgId` (Keyword); registered by a startup helper that is safe to re-run on the dev server; upserted on every stage change.
- Payloads carry ids and GCS URIs only; never document bytes. Data converter: default JSON with pydantic conversion (`temporalio.contrib.pydantic.pydantic_data_converter`).

## 2. Contracts (`basis/workflow/contracts.py`, already created on the branch; do not rename fields)

Pydantic models: `StudyWorkflowInput(study_id, org_id, input_revision, workflow_version)`, `Decision(review_id, stage, revision, decision: "approve"|"reject", reviewer_id, corrections: list[CorrectionInput], submitted_at)`, `DecisionAck(accepted: bool, applied_revision: int, reason: str | None)`, `StudyStatusView(stage, revision, waiting_since, pending_photos, completed_photos, decisions_applied: list[str])`, activity inputs/outputs: `IngestAppraisalInput/Output`, `ExtractFieldsInput/Output`, `AnalyzePhotoInput/Output`, `ProcessAssetsInput/Output`, `ExportReportInput/Output`, `NotifyInput`, `MarkStalledInput`, `TransitionInput`. Stage names reuse `basis.db.enums.Stage` values as strings.

## 3. Workflow (`basis/workflow/study_workflow.py`)

```
run(input):
  record_run_start (activity, DB-only)                        # runs row
  transition(stage=extracting)                                # DB + search attrs
  parallel: ingest_appraisal → extract_fields ; analyze_photo × N (bounded)
  loop:
    transition(stage=appraisal_review); decision = await wait_for_decision("appraisal_review")
    if reject: apply_corrections (activity) ; re-run extract_fields ; revision += 1 ; continue
    break
  loop (rooms_review) same pattern; reject → re-run the photo analyses flagged by corrections only
  transition(stage=assets_processing); process_assets (activity; S3 fans this out)
  loop (assets_review) same pattern; reject → process_assets again with corrections
  transition(stage=exporting); export_report; transition(stage=completed); record_run_end
```

- `@workflow.query status() -> StudyStatusView`.
- `@workflow.update decide(d: Decision) -> DecisionAck` with validator rejecting: wrong stage, `d.revision != self.revision`, unknown decision; handler is idempotent on `review_id` and sets the awaited decision.
- `@workflow.signal cancel_study(reason)` → cancels in-flight activities, records outcome `cancelled`.
- Budget: the workflow reads `budget_usd` from input (default from settings) and passes a remaining budget to each LLM-calling activity; an activity raising `BudgetExceededError` is non-retryable and marks the stage output `needs_review` instead of failing the workflow.
- `workflow.patched` is not needed in the first version; include a documented example of where a patch goes.
- Continue-as-new is not needed at this history size; assert in a test that a 300-photo run stays under 10,000 events.

## 4. Activities (`basis/activities/*.py`, one module each; Stage 2 bodies)

| Activity | Stage 2 body (real rows, fake content) | Timeout / retries |
|---|---|---|
| `record_run_start` / `record_run_end` | insert/update `runs` | 30 s / 5 |
| `transition` | `StudyRepository.transition` + upsert search attributes (via a `SearchAttributeUpdater` the worker injects) | 30 s / 5 |
| `ingest_appraisal` | finds the study's appraisal document; writes `corpus_versions`(study) + 3 placeholder `chunks` rows | 10 min / 3 |
| `extract_fields` | writes an `extractions` row (`method="stub"`, revision) with 5 `extraction_fields` from the document filename | 5 min / 3 |
| `analyze_photo` | per document: writes a `rooms` row (if first photo of its inferred name) + `photo_rooms` + 2 `components`; heartbeats; supports `simulate` hooks for tests (fail once, sleep) read from input | 2 min / 3 |
| `apply_corrections` | writes `corrections` rows already stored by the API? No: the API stores them; this activity applies `after` payloads to the target rows (rooms/extraction_fields) for the new revision | 1 min / 5 |
| `process_assets` | for every component in the revision: `assets` (rule `method="rule"`, bucket from a tiny label map), `takeoffs`, `cost_estimates`; one `citations` row to a placeholder chunk | 10 min / 3 |
| `export_report` | writes a JSON export to storage at `studies/{id}/exports/rev-{n}.json` with checksums; deterministic for the same revision | 5 min / 3 |
| `notify_review_pending` | logs + inserts an `outbox` row of kind `notification` | 30 s / 5 |
| `mark_stalled` | sets `studies.status='stalled'` | 30 s / 5 |

Each activity: `@activity.defn(name="…")`, pydantic input/output, claims its idempotency key in `activity_results` (status `running` → `completed`, storing `output_ref`), returns stored output on re-execution, heartbeats where looping, honors cancellation, and raises `ApplicationError(non_retryable=True)` for validation errors.

## 5. Worker (`basis/worker/main.py`)

`python -m basis.worker` builds settings, the engine/sessionmaker, a Temporal client (`temporal_address`, `temporal_namespace`, API key + TLS when `temporal_api_key` is set), registers the workflow and all activities on task queue `basis-studies`, Prometheus metrics on `:9464`, graceful shutdown on SIGTERM (drain in-flight activities up to 10 s then exit, matching Cloud Run's grace), `/health` HTTP on `:8080` for the worker pool startup probe, structured JSON logs with `run_id`/`activity` bound via an interceptor.

## 6. Outbox relay (`basis/relay/main.py`, also runnable in-process in the worker for dev)

Polls `outbox` every 2 s (`SELECT … FOR UPDATE SKIP LOCKED` on `delivered_at IS NULL AND next_attempt_at <= now()` limit 20), delivers by kind: `workflow_signal` → `client.get_workflow_handle("study-{id}").execute_update("decide", Decision(...))` with a 30 s timeout; `notification` → log (S5 wires email/Slack). Success sets `delivered_at`; failure sets `attempts += 1`, `last_error`, `next_attempt_at = now + min(2^attempts, 300) s`; after 50 attempts the row is marked `dead` (a `kind` suffix or a `status` column added by migration `0002_outbox_status`). A validator rejection (`WorkflowUpdateFailedError`) is final: mark delivered with `last_error` set and log at WARNING, because the API already answered the client.

## 7. API changes (`basis/api/routes/studies.py` + new `workflow.py`)

- `POST /v1/studies/{id}/start` (engineer; idempotent): requires ≥1 completed appraisal document; sets `current_stage=extracting` via transition; starts the workflow with `signal_with_start`-free plain start using the id policies above; returns 202 with `workflow_id`. Starting twice returns the same 202.
- `GET /v1/studies/{id}/workflow`: `status` query result plus `runs` rows; 404 when no workflow.
- `POST /v1/studies/{id}/cancel` (owner): signals `cancel_study`.
- The Temporal client lives in `app.state.temporal` created in lifespan when `settings.temporal_address` is set; a `WorkflowService` Protocol with a fake lets API tests run without Temporal.
- Health: `temporal: ok/fail` check (describe namespace) alongside `db`.

## 8. Settings additions

`temporal_task_queue="basis-studies"`, `temporal_worker_versioning=False`, `temporal_deployment_name="basis-worker"`, `temporal_build_id` (defaults to `git_sha`), `photo_concurrency=5`, `review_reminder_days=7`, `review_deadline_days=30`, `study_budget_usd=25.0`, `relay_poll_seconds=2.0`.

## 9. Tests (binding names)

Lifecycle, `tests/lifecycle/` (Temporal time-skipping environment, activities mocked where the test says so, real Postgres container for the rest):
- `test_happy_path.py`, `test_reject_paths.py`, `test_restart_recovery.py` (stop the worker between activities and during a wait; start a new worker; the run completes; `activity_results` shows each key once), `test_duplicate_decisions.py` (same `review_id` twice → one application; second ack `accepted=True, reason="already applied"`), `test_concurrent_decisions.py` (two different reviews for the same revision → validator rejects the second with the stage/revision reason), `test_stale_approval.py`, `test_parallel_photos.py` (one photo activity fails permanently → study continues, that photo flagged; one fails once → retried), `test_timeouts.py` (7-day reminder fires, 30-day stall marks `stalled`, a decision after stall still completes), `test_versioning.py` (replay recorded histories from `tests/lifecycle/histories/*.json` with `Replayer`; a test that records a fresh history and replays it), `test_budget.py`, `test_history_size.py`, `test_cancel.py`.
- `tests/unit/workflow/test_validator.py`, `test_contracts.py`; `tests/unit/activities/test_*.py` per activity with `ActivityEnvironment` (idempotency replay, heartbeat, cancellation, non-retryable errors).
- Relay: `tests/integration/relay/test_relay.py` (delivers, retries with backoff, dead-letters, validator rejection is final, SKIP LOCKED under two relays).
- API: `tests/api/test_workflow_routes.py` (start idempotent, requires document, status, cancel, 404s) with the fake `WorkflowService`; `tests/integration/api/test_start_to_decision.py` with the real dev server (`WorkflowEnvironment.start_local`): start → wait → decision via API → relay delivers → workflow advances.
- Failure: `tests/failure/test_temporal_unavailable.py` (API decision succeeds while Temporal is down; relay delivers when it returns), `test_worker_kill.py` (SIGKILL the worker subprocess mid-activity; recovery), `test_db_outage.py` (activity fails and retries without partial rows).
- Ledger rows for every file.

## 10. Exit criteria

`uv run pytest -q` green including lifecycle, relay, failure; recorded histories replay clean; `alembic check` clean after migration `0002`; the regression xfails for W1, W2, W3 are deleted and replaced by the passing lifecycle tests (the ledger notes the replacement); `python -m basis.worker` and `python -m basis.relay` start against `temporal server start-dev` and a local Postgres and run one synthetic study end to end via the API (documented in `docs/review_2026-10-15/S2_RUNBOOK.md` with the exact commands).
