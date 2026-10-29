# Study workflow: start, progress, a failed stage and its retry

A study runs as one Temporal workflow (`workflow_id = study-{study_id}`), started by
`POST /v1/studies/{id}/start` and watched through `GET /v1/studies/{id}/workflow`. A stage
that fails does not end the workflow: the study is marked `failed`, the workflow holds the
failure and waits for `POST /v1/studies/{id}/retry`, which runs the stage again. This page is
the client's view of that; the workflow's own contract is `basis/workflow/study_workflow.py`
(patch `stage-failure-retry-v1`, EVAL_REPORT section 27) and the routes are
`basis/api/routes/workflow.py`. Decisions at the review gates are a separate page
(`POST /v1/studies/{id}/decisions`; `openapi.json`).

## `GET /v1/studies/{id}/workflow` (viewer)

```json
{
  "workflow_id": "study-5f0c6a52-2f53-4c39-9a3e-2b0a8f1f7d10",
  "run_id": "0193a1b2-7c3d-7e4f-8a9b-0c1d2e3f4a5b",
  "execution_status": "RUNNING",
  "status": {
    "stage": "extracting",
    "revision": 1,
    "waiting_since": null,
    "pending_photos": 12,
    "completed_photos": 3,
    "decisions_applied": [],
    "stalled": false,
    "budget_remaining_usd": 24.1,
    "awaiting": null,
    "flagged_photos": [],
    "needs_review_stages": [],
    "failed_stage": "extracting",
    "failure": "ingest_appraisal: activity Heartbeat timeout"
  },
  "runs": [ { "workflow_id": "…", "run_id": "…", "workflow_version": "9c8f443", "input_revision": 1,
              "started_at": "2026-10-29T17:02:11Z", "ended_at": null, "outcome": null } ]
}
```

* `execution_status` is Temporal's: `RUNNING` while the study is in progress **or waiting
  for a retry**; `COMPLETED`, `FAILED`, `CANCELED`, `TERMINATED` or `TIMED_OUT` once it has
  ended.
* `status` is the workflow's own progress (null when nothing could answer the query, for
  example while no worker is running; the rest of the answer is still returned).
* **`failed_stage` and `failure`** are set while a stage has failed and the workflow waits
  for a retry, and null otherwise. `failed_stage` is the stage that failed (`extracting`,
  `assets_processing`, `exporting`, or the stage the photos were at when a provider refused
  the account); `failure` is the reason, as the activity reported it. At the same time
  `GET /v1/studies/{id}` shows `status: "failed"` and the same reason in `failure_reason`.
* A client that polls this route should treat `status.failed_stage != null` as "needs a
  retry", not as the end of the study: the execution is still running and nothing else will
  move it.

## What happens while a stage waits for a retry

The appraisal (ingestion, extraction) and the photos (analysis, localization, the room step)
run in parallel. When the appraisal side fails, the workflow records the failure and
**schedules no more photo work**: the few photo activities already running finish and keep
their rows, and the rest of the photo stage waits with the failed stage. The photo stage
therefore pauses while the study waits for a retry, and resumes with it; the photo rows
already written are reused, not redone. The appraisal review does not open while a failure
is held.

Nothing retries on its own after the activity's own retry policy is exhausted (three attempts
for an activity that calls a provider): the retry is a client's call, because the cause may
need a fix first (a provider account with no credit, a bad configuration), and retrying into
the same failure would only spend more.

## `POST /v1/studies/{id}/retry` (engineer)

Runs the failed stage again. No body; `Idempotency-Key` is not needed.

| Answer | When |
|---|---|
| `202` `{workflow_id, stage, revision, already_requested}` | the workflow accepted the retry; `stage` is the stage that runs again, `revision` the input revision it runs on. `already_requested: true` means a retry was already accepted and the stage has not restarted yet (a repeat is harmless). |
| `409` `study-not-failed` | no stage has failed (the workflow's validator refused the request). |
| `409` `workflow-closed` | the execution has ended (completed, cancelled, or failed before v0.9, whose executions ended on a failure and cannot be resumed). |
| `409` `study-archived` | the study is archived. |
| `404` | the study has no workflow (never started). |
| `503` | Temporal is not configured or unreachable; `Retry-After` is set. |

After the retry the failure is cleared (`failed_stage` and `failure` go back to null), the
study leaves `failed`, and the stage's activities run again under fresh activity ids
(`:retry<n>`), so their idempotency keys are new while any rows the first attempt committed
(a published study corpus version, photo outputs) are reused.

A client loop:

```
poll GET /workflow until status.awaiting (a review gate) or status.failed_stage
  failed_stage set  -> show failure; on the engineer's go-ahead POST /retry; keep polling
  awaiting set      -> POST /decisions
execution_status not RUNNING -> the study ended (COMPLETED, CANCELED, FAILED)
```

## The failure this page was written for

On 2026-10-29 a worker on a freshly built container failed the first stage of its first
study three times with `ingest_appraisal: activity Heartbeat timeout`: the first embedding
call downloaded and loaded the sentence-transformers model inside the activity, 187 s on a
cold cache against the activity's 30 s heartbeat timeout. The study parked at
`failed_stage: "extracting"` until an engineer called `retry`, which passed in 2 s once the
model was cached; the photo stage paused with it and 19 minutes were lost. Since EVAL_REPORT
section 38 the worker loads the model before it polls (from the Docker image, which bakes
it) and the embedding loop heartbeats while a batch runs, so a fresh worker's first study
does not reach this page. The route exists for the failures that remain a client's call.
