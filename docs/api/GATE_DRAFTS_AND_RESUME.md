# Leaving and coming back: gate drafts, resumable uploads, restarting a cancelled study

An engineer who leaves a study at any stage must be able to come back and continue, with
nothing lost. Before this page three things lived only in the browser or had no way back:

| Before | Now |
|---|---|
| Corrections added at a review gate (`L.corrections` in `web/live/live-review.js`) existed only in the tab until `POST .../decisions` sent them; a reload or a switch of study lost them. | `GET`/`PUT`/`DELETE /v1/studies/{id}/drafts/{stage}`: one draft per study, stage and engineer, on the server, validated as a decision is, versioned with `ETag`/`If-Match`, deleted by the decision that submits it. |
| The upload run (`L.upload` in `live-app.js`) was in memory: after a reload a draft study showed "Ready to start" with no way to add its files, and the upload screen always created a new study. | The registration already survives (`GET /v1/studies/{id}` lists it with `uploaded_at: null`); `POST .../documents/{id}/upload-url` re-issues the signed URL. The sequence is below. |
| A cancelled study was a dead end: `start` answered 409 `workflow-closed` for ever. | `POST /v1/studies/{id}/restart` (owner) runs it again from its uploads on a new revision. |

Code: `basis/api/routes/drafts.py`, `basis/db/repositories/review_draft.py` (migration
`0024_review_drafts`), `basis/api/routes/documents.py`, `basis/api/routes/workflow.py`,
`basis/temporal/service.py`. Tests: `tests/api/test_drafts.py`, the resume section of
`tests/api/test_uploads.py`, the restart section of `tests/api/test_workflow_routes.py`.

## 1. Gate drafts

### The model

A **draft** is what an engineer has lined up for a review stage before deciding it: the
`comment` and the `corrections` of the decision to come. It is stored whole, one row per
`(study, stage, user)` (`review_drafts`), with:

* `revision`: the study's revision the corrections name rows of (every `entity_id` is a row of
  that revision, exactly as on `POST .../decisions`);
* `version`: the row's strong entity tag, from 1, incremented by every save;
* `document`: `{comment, corrections}` as validated.

Two engineers draft apart (each has their own row). A draft is overwritten by every save and
deleted when its decision is submitted or the engineer discards it: it is work in progress,
not history.

### `GET /v1/studies/{id}/drafts/{stage}` (engineer)

```json
{
  "study_id": "5f0c…", "stage": "appraisal_review", "current_revision": 1,
  "draft": {
    "study_id": "5f0c…", "stage": "appraisal_review", "revision": 1, "user_id": "9a…",
    "comment": "Checking the deed.",
    "corrections": [
      {"entity_type": "extraction_field", "entity_id": "9d1f…",
       "before": {"year_built": 1989}, "after": {"year_built": 1998}, "reason": "Deed shows 1998."}
    ],
    "version": 3, "stale": false, "stale_reason": null,
    "created_at": "2026-11-01T14:02:11Z", "updated_at": "2026-11-01T14:09:40Z"
  },
  "others": [
    {"user_id": "7c…", "display_name": "Bob", "email": "bob@firm.test", "revision": 1,
     "corrections": 2, "has_comment": false, "stale": false, "updated_at": "2026-11-01T13:58:03Z"}
  ]
}
```

* `draft` is the caller's own draft, `null` when none. `others` lists the other engineers'
  drafts on the stage: who, since when, how many corrections, whether a comment; never their
  content (a colleague's half-finished work is theirs to send).
* `ETag: "<version>"` is **the caller's draft's version** (`"0"` while none), the tag to send
  back as `If-Match`. It does not cover `others`, and the response is `Cache-Control:
  no-store`, so no cache ever answers this route from the tag.
* `stale` / `stale_reason`: see below.
* `stage` is one of the four review stages (`appraisal_review`, `rooms_review`,
  `components_review`, `assets_review`); anything else is 422.

### `PUT /v1/studies/{id}/drafts/{stage}` (engineer; no `Idempotency-Key`)

The whole draft each time:

```json
{"revision": 1, "comment": "Checking the deed.", "corrections": [ … CorrectionIn … ]}
```

| Answer | When |
|---|---|
| `200` the same body as `GET`, `ETag` the new version | saved (created at version 1, or replaced at version + 1) |
| `422` `validation`, one `errors` entry per offending correction | **exactly the checks of `POST .../decisions`**, with the same `type` values and locations `["body", "corrections", <i>, …]`: `correction_type` (the stage in the study's flow does not take that type; `reprice`/`structure_model` go through `POST .../assets/reprice`), `correction_target` (the row is not in `revision`; a `photo_room` target room too), `correction_value` (counts, room fields, added-field shape, a page past the PDF), `correction_reason`, `correction_duplicate`, `correction_rebuilt`, `correction_last_inventory`, `correction_field_exists`. A draft that would be refused at submit time is refused at save time, so the engineer learns it while the context is in front of them. The one decision-only rule, "an approval cannot carry corrections", does not apply: the decision is chosen at submit time. |
| `409` `draft-stale` (`BASIS-STUDY-026`) | `revision` is not the study's current one, or the stage is already decided at it (`reviews` has a row): the corrections could never be submitted. `detail` names the current revision. |
| `412` `precondition-failed` (`BASIS-STUDY-025`), `ETag` the current version | `If-Match` did not hold; nothing written |
| `409` `study-archived` | the study is archived (drafts stay readable and discardable) |
| `403` / `404` | viewer role / not a member or no such study |

A draft may be saved while the study is still `processing` towards the stage (the gate
governs the decision, not the draft) and with no corrections at all (a comment alone).

**Optimistic concurrency, as the intake's** (`docs/api/STUDY_RESULT.md`, "Versions and
concurrent edits"; RFC 9110 section 13.1.1): send the `ETag` of the last read as `If-Match`.
`"0"` creates only while the caller has no draft; `*` writes only over an existing one; a
weak tag (`W/"3"`) or an unquoted number never matches. Without `If-Match` the save is
accepted (last save wins, within one engineer's own drafts). Of two saves carrying the same
tag exactly one succeeds (the row lock, and the one-per-user constraint for two first saves).
A client that autosaves should chain saves on the returned `ETag`; on 412 it re-reads,
merges and saves again.

### `DELETE /v1/studies/{id}/drafts/{stage}` (engineer; `Idempotency-Key` as on every DELETE)

Deletes the caller's draft on the stage and answers the stage's remaining drafts (the same
body as `GET`, `draft` null, `ETag: "0"`). `If-Match` as on `PUT` (412 otherwise, nothing
deleted); 404 `not-found` (`BASIS-STUDY-027`) when the caller has none. Another engineer's
draft is never touched.

### Stale drafts

A draft is saved against a revision. When the study leaves it (a rejection started revision
2) or the stage is decided at it (by this engineer, whose draft the decision deletes, or by a
colleague), the draft's corrections name rows that are no longer the ones under review.
`GET` still returns such a draft, with `stale: true` and `stale_reason` `revision` or
`decided`, **never as current**: the client shows it as "drafted against revision 1" and lets
the engineer carry what still applies into a new draft (`PUT` with the current `revision`,
which replaces the stale one) or discard it (`DELETE`). `PUT` with the stale revision is 409
`draft-stale`. A stale draft in `others` is flagged the same way.

### Submission

`POST .../decisions` deletes the submitting engineer's draft on the decided stage in the
decision's own transaction, whatever revision it was drafted against (it was for this
decision, or stale). A refused decision (422, 409) rolls that back with everything else, so
the draft is still there to fix. The `review.decided` event's `detail.draft_discarded` says
whether a draft went with the decision. The client sends the draft's `comment` and
`corrections` as the decision's and need not `DELETE` afterwards.

### Why no `draft.saved` / `draft.discarded` timeline events

The study timeline (`docs/api/TIMELINE_AND_AUDIT.md`) is the record of what happened to the
study and the audit trail of the engineer input that changed it: decisions, the corrections
they carried, the rows those produced. A draft changes nothing about the study. Its
corrections enter the trail as `correction.recorded` when the decision is submitted; until
then they are the engineer's working notes, saved on every edit (an append-only row per
autosave would bury the trail in noise and keep every half-typed value for ever). The decision
records `draft_discarded`; who is drafting since when is on the `review_drafts` row itself
(`user_id`, `updated_at`), which `GET` shows as `others`. Nothing of this is in `GET
.../audit` either, for the same reason.

### What the frontend does

1. Opening a gate: `GET .../drafts/{stage}`; render `draft.corrections` and `draft.comment`
   as the pending corrections, keep the `ETag`; show `others` ("Bob has 2 corrections
   drafted"); if `draft.stale`, show it apart with its revision and offer "carry over" (re-add
   those that still have a target in the current revision; a `PUT` tells which do not, by
   index) and "discard".
2. Every change to the pending corrections or the comment: `PUT` the whole draft with the
   current `revision` and `If-Match` the last `ETag`; keep the new `ETag`. On 412, `GET`,
   merge, `PUT` again. On 422, show the `errors` on the correction they name (the same code
   the decision modal shows today).
3. Approve or request corrections: `POST .../decisions` as today; then `GET .../drafts/{stage}`
   (or trust the 202) — the draft is gone.
4. Switching study or reloading: nothing to do; step 1 brings it back.

## 2. Resuming an upload

What is already durable: `POST .../documents` registers the document before any byte moves
(`uploaded_at: null`); `GET /v1/studies/{id}` lists every document of the study with its
`sha256`, `bytes`, `content_type`, `kind` and `uploaded_at`; `POST .../documents/{id}/complete`
confirms once the bytes are there and hash to the registered sha256, and is idempotent (a
second confirm returns the document). What was missing was a way to get the signed URL back:
it was in the first response only, and it expires after 15 minutes.

### `POST /v1/studies/{id}/documents/{document_id}/upload-url` (engineer, `Idempotency-Key`)

Re-issues the signed `PUT` of an unconfirmed document: the same `document`, a new
`upload.url` (15 minutes) for exactly the declared `bytes` and `content_type`. The bytes must
still hash to the registered sha256, which `complete` checks; a different file is a different
document.

| Answer | When |
|---|---|
| `201` `UploadTicket` (as `POST .../documents`) | the document is registered and unconfirmed |
| `409` `upload-complete` (`BASIS-UPLOAD-011`) | the upload is confirmed; its bytes are immutable (register a new document for a different file) |
| `409` `study-archived` | the study is archived |
| `404` | no such document in this study |
| `403` | viewer role |

### `POST .../documents` on a repeat

A second registration of the same bytes in the same study is **409 `duplicate-document`**
naming the existing document in `detail`; nothing is registered twice (the unique constraint on
`(study_id, sha256)`). It is not replayed as a 201: the client finds the registration in the
study's document list by `sha256` instead, which is also what tells it whether the bytes are
already confirmed.

### The sequence

A client that has nothing but the study id and, when the engineer re-selects them, the files:

```
GET /v1/studies/{id}                       -> status draft, documents[]
for each document with uploaded_at == null:
    POST .../documents/{doc}/complete      -> 200: the bytes were already there; done
                                           -> 409 upload-missing: the bytes never arrived
                                           -> 409 upload-mismatch: wrong bytes, deleted; upload again
    (the engineer re-selects the file; hash it; match it to the document by sha256)
    POST .../documents/{doc}/upload-url    -> 201 ticket
    PUT ticket.upload.url with ticket.upload.headers and the bytes
    POST .../documents/{doc}/complete      -> 200
for each selected file whose sha256 is in no document: POST .../documents as today
    (409 duplicate-document: it is registered after all; resume it as above)
when the intake is complete and an appraisal is confirmed: POST .../start
```

The upload screen therefore takes an existing study id: "continue this study" opens it with
its registered documents listed, confirmed or pending, instead of creating a new study. A
pending document whose file the engineer no longer has stays listed as pending; `start`
needs only one confirmed appraisal, so it does not block the study. (Removing a registration
is not provided: a pending row costs nothing and keeps the duplicate check honest.)

## 3. Restarting a cancelled study

### Why `start` cannot

A study runs as one Temporal workflow, `workflow_id = study-{study_id}`, started with the
reuse policy `REJECT_DUPLICATE`: once an execution under that id has **closed**, with any
outcome, Temporal refuses a new one, and `POST .../start` describes the closed execution and
answers 409 `workflow-closed` (a completed one is answered as the existing run). The owner's
`POST .../cancel` signals the workflow, which stops its activities, records `runs.outcome =
cancelled` and ends; the study row stays at the stage and revision it had (`processing` or
`waiting_review`). `retry` is for a stage that failed inside a running execution and is 409
`workflow-closed` too. So a cancelled study could only be archived and re-created, re-uploading
every file and re-typing the intake.

### `POST /v1/studies/{id}/restart` (owner, `Idempotency-Key`)

Runs the study again from its uploads, under the same workflow id, on a new revision:

1. The execution is described first (no transaction open). `RUNNING` is 409
   `workflow-running` (`BASIS-STUDY-028`: cancel it first, or `retry` a failed stage);
   `COMPLETED` is 409 `study-completed` (`BASIS-STUDY-029`: the outputs exist; reject a
   review stage to rework a revision); `CANCELED`, `TERMINATED`, `TIMED_OUT` and `FAILED`
   (an execution from before v0.9, which ended on a failure) proceed; no record at all
   (namespace retention) proceeds too, because the owner is asking explicitly.
2. One short transaction resets the study row: `current_stage = extracting`, `status =
   processing`, `revision + 1`, `failure_reason`/`failure_code` cleared; events
   `stage.entered` (from the stage it was at) and `study.restarted` (`before` the stage,
   revision and status it left, `after` the new ones, `detail.workflow_id`).
3. After it commits, a new execution is started under `study-{id}` with the reuse policy
   `ALLOW_DUPLICATE_FAILED_ONLY` (a second execution only over a closed one that did not
   complete) and the conflict policy `FAIL`. The input is the study as it is now: its
   intake (edited since, if it was) and its confirmed documents.

```json
{"workflow_id": "study-5f0c…", "run_id": "0193…", "revision": 3,
 "from_stage": "assets_review", "from_revision": 2}
```

* **Why a new revision.** Outputs are grouped by revision per study; the per-revision tables
  (extractions, rooms, components, assets, reviews, photo rows) are unique per revision. The
  cancelled execution's rows stay as history on their revision, the new run writes its own,
  and every gate draft for the old revision becomes stale (section 1).
* **Why the same workflow id.** `studies.workflow_id`, the outbox relay's routing of
  decisions, `GET .../workflow`, `cancel` and `retry` all address `study-{id}`; a new run id
  under it keeps them working and gives the new execution its own `runs` row.
* **Two restarts at once, or a 503 after the reset.** The row lock serializes the resets; the
  second finds the first's `study.restarted` event with no `run.started` after it and reuses
  the reset instead of bumping the revision again. Then one start wins and the other is 409
  `workflow-running`, and the row is at the revision the running execution was given. If
  Temporal is unreachable after the reset the answer is 503 with the row reset; the repeat
  reuses it. (`tests/api/test_workflow_routes.py`, the restart section.)
* `GET .../workflow` follows the new execution (`run_id`, `RUNNING`, the new revision in
  `status`); the old run stays in `runs` with `outcome: cancelled`.
* 404 when the study was never started (no workflow); 409 `study-archived`; 403 below owner.

A **completed** study is not restarted: its outputs and signed report stand, and it has no
open gate to reject. Reworking a completed property is a new study. `restart` is for the
study that was stopped before it finished.
