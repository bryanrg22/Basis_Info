# Timeline, audit trail and provenance

Three questions about any study, answered from the database without reading a worker log:

1. **What happened, when, and how long did each step take?** `GET /v1/studies/{id}/timeline`
   (every recorded event, in order) and `GET /v1/studies/{id}/timeline/steps` (per activity:
   attempts and duration; per stage visit: duration and review wait). The same document is in
   the export as `summary.timeline`.
2. **What did the engineer do?** `GET /v1/studies/{id}/audit`: every decision, correction,
   manual object edit and intake change, in order, with before and after.
3. **Who produced this row?** Every row the output routes return (`/fields`, `/rooms` with
   its photos and inventory, `/assets`) and every row of the export carries `provenance`:
   `model`, `engineer_added` or `engineer_modified`, with the correction, the step, the time,
   the reviewer, and the values before and after.

All three are viewer routes. Failures carry their error code (`BASIS-<area>-<nnn>`,
[docs/ERROR_CODES.md](../ERROR_CODES.md)).

## The timeline

One append-only table, `study_events` (migration `0013_study_events`): a row per occurrence,
written in the same transaction as the change it records where there is one (a decision, a
stage move, a correction applied to a row), so a rollback removes both. `seq` is the
insertion order. Rows can never be updated or deleted (the `forbid_mutation()` trigger that
protects `reviews` and `corrections`).

```
GET /v1/studies/{id}/timeline?limit=200&cursor=<next_cursor>&event_type=activity.failed&event_type=stage.failed
```

```json
{
  "study_id": "5f0c6a52-...",
  "items": [
    {"seq": 41, "at": "2026-10-29T18:02:11.482Z", "event_type": "stage.entered", "actor_type": "system",
     "actor_id": null, "stage": "extracting", "revision": 1, "before": {"stage": "uploading", "revision": 1},
     "after": {"stage": "extracting", "revision": 1}, "workflow_id": null, "run_id": null,
     "activity": null, "activity_id": null, "attempt": null, "started_at": null, "duration_ms": null,
     "entity_type": null, "entity_id": null, "code": null, "request_id": "0193a1b2-...", "detail": null},
    {"seq": 57, "at": "2026-10-29T18:05:40.010Z", "event_type": "activity.failed", "actor_type": "system",
     "workflow_id": "study-5f0c6a52-...", "run_id": "0193...", "activity": "ingest_appraisal",
     "activity_id": "ingest_appraisal:r1", "attempt": 1, "started_at": "2026-10-29T18:02:12.001Z",
     "duration_ms": 207998, "code": "BASIS-WF-001",
     "detail": {"error": "TimeoutError: activity Heartbeat timeout [BASIS-WF-001]"}, "...": null}
  ],
  "next_cursor": "57"
}
```

| Member | Meaning |
|---|---|
| `event_type` | A dotted name from the vocabulary below. |
| `actor_type`, `actor_id` | Who caused it: `engineer` (`actor_id` is the user), `model` (a model call's output), `system` (the workflow, a rule, bookkeeping). |
| `stage`, `revision` | The study's at the time. |
| `workflow_id`, `run_id`, `activity`, `activity_id`, `attempt` | The Temporal execution and the activity attempt, on activity events. |
| `started_at`, `duration_ms` | An attempt's start (the worker's clock) and its duration (measured on the worker, so it never depends on two clocks). |
| `entity_type`, `entity_id`, `before`, `after` | The row an event is about and what changed. |
| `code` | The error code of a failure or refusal. |
| `request_id` | The `X-Request-Id` of the API request that caused it. |
| `detail` | The rest: counts, reasons, the correction ids an applied correction came from, an export's URI. |

### Event vocabulary

| `event_type` | Actor | When | Carries |
|---|---|---|---|
| `study.created`, `study.intake_set`, `study.archived` | engineer | the study routes | `intake_set`: `before`/`after` the intake documents |
| `document.uploaded` | engineer | an upload confirmed | `entity_id` the document; `detail` kind, filename, bytes, sha256 |
| `study.started` | engineer | `POST .../start` moved the study to `extracting` | `detail.workflow_id` |
| `run.started`, `run.ended` | system | the Temporal execution | `detail.outcome` on the end |
| `stage.entered` | engineer or system | the study row moved (a decision moves it first; the workflow's `transition` converges) | `before`/`after` stage and revision |
| `gate.opened` | system | the workflow opened a review gate (the study is `waiting_review` there) | |
| `activity.completed`, `activity.failed`, `activity.cancelled` | system | one attempt of an activity ended | `attempt`, `started_at`, `duration_ms`; `code` and `detail.error` on a failure |
| `review.decided` | engineer | a decision was recorded | `entity_id` the review; `detail` decision, comment, corrections, the stage it moved to |
| `correction.recorded` | engineer | each correction the decision carried | `entity_type`/`entity_id` the row of the decided revision; `before`/`after`; `detail.correction_id` |
| `entity.corrected`, `entity.added`, `entity.moved`, `entity.merged` | engineer | `apply_corrections`, `extract_fields` or `process_assets` applied a correction | `entity_id` the **new** row (the merged room's old id for `merged`); `before`/`after`; `detail.carried_from`, `detail.correction_ids`, `detail.reasons` |
| `entity.added`, `entity.deleted` (`photo_object`) | engineer | a manual instance added or soft-deleted on a photo | `after`/`before` the instance |
| `revision.carried` | system | `apply_corrections` finished copying a revision | `detail.applied`, `detail.from_revision` |
| `corrections.refused` | system | `apply_corrections` refused a rejection's corrections; the revision started without them | `code`, `detail.reason` |
| `inventory.rebuilt` | model | `reconcile_rooms` replaced a room's inventory rows | `entity_id` the room; `detail.deleted` (row ids), `written`, `step`, `model` |
| `stage.failed`, `stage.failure_cleared` | system | `record_failure` | `code`, `detail.reason`, `detail.marked` |
| `study.stalled`, `review.reminded` | system | the review deadline and reminders | `detail.waiting_days` |
| `study.retry_requested`, `study.cancel_requested` | engineer | `POST .../retry`, `POST .../cancel` | |
| `export.written` | system | the export | `detail.uri`, `sha256`, `assets_csv` |
| `convention.promoted` | engineer | `promote_conventions` promoted an approved asset's ruling to the firm's conventions | `actor_id` the approving reviewer; `entity_id` the asset; `detail` key, bucket, `agrees_with_rules`, `rule_key`, outcome (`created`, `confirmed`, `replaced`), approvals, method, cited |
| `convention.replaced` | engineer | the approved answer differed from the firm's convention for the key, which it replaced | `code` `BASIS-ASSETS-013`; `before` the replaced convention, `after` the new one; `detail.key` |

Not recorded per row, on purpose: the model's `photo_objects` (SAM 3 candidates, tens per
photo) and the rows a model call wrote (components, fields): the activity event carries the
step, and the rows carry `created_at` and their lineage. Studies that ran before the table
existed have no events; their outputs still get `provenance` and an audit listing, which read
the tables.

### Steps and stages

```
GET /v1/studies/{id}/timeline/steps
```

```json
{
  "study_id": "5f0c6a52-...",
  "steps": [
    {"activity": "ingest_appraisal", "activity_id": "ingest_appraisal:r1",
     "started_at": "...", "ended_at": "...", "duration_ms": 215400,
     "attempts": [{"attempt": 1, "started_at": "...", "ended_at": "...", "duration_ms": 207998,
                   "outcome": "failed", "code": "BASIS-WF-001", "error": "..."},
                  {"attempt": 2, "started_at": "...", "ended_at": "...", "duration_ms": 7402,
                   "outcome": "completed", "code": null, "error": null}],
     "outcome": "completed", "code": null}
  ],
  "stages": [
    {"stage": "extracting", "revision": 1, "entered_at": "...", "entered_by": "engineer",
     "left_at": "...", "duration_ms": 221000, "gate_opened_at": null, "decided_at": null,
     "decision": null, "review_wait_ms": null},
    {"stage": "appraisal_review", "revision": 1, "entered_at": "...", "entered_by": "system",
     "left_at": "...", "duration_ms": 3600000, "gate_opened_at": "...", "decided_at": "...",
     "decision": "approve", "review_wait_ms": 3599000}
  ]
}
```

A **step** is one activity id: every attempt that ended, the first start, the last end, the
attempts' durations summed, and the last attempt's outcome and code (an OpenTelemetry span:
a start, an end, a status set only on failure, an `error.type`-like code). A **stage** is one
visit of the study to a stage at a revision: entered when the row moved, left when the next
move happened (open while the study is there), and for a review stage when the workflow
opened the gate and when the engineer decided, so the review wait is separable from the
processing time. Temporal's history cannot give per-attempt timing (`ActivityTaskStarted` is
written only when the attempts end), which is why the worker records it.

The export's `summary.timeline` is `{"events": [...], "steps": [...], "stages": [...]}` with
the same members, as of the export, without the export step's own events (`export_report`
attempts, `export.written`): exporting the same revision twice yields byte-identical files,
which a retried export's record of its earlier attempt would break. The API's timeline has
them.

## The audit listing

```
GET /v1/studies/{id}/audit
```

```json
{
  "study_id": "5f0c6a52-...",
  "items": [
    {"kind": "decision", "at": "...", "by": "<user>", "stage": "rooms_review", "revision": 1,
     "action": "rejected", "entity_type": "review", "entity_id": "<review>", "reason": "Kitchen is two rooms",
     "review_id": "<review>", "correction_id": null, "before": null, "after": null, "changed": [], "produced": []},
    {"kind": "correction", "at": "...", "by": "<user>", "stage": "rooms_review", "revision": 1,
     "action": "modified", "entity_type": "room", "entity_id": "<room r1>", "before": {"name": "Kitchen"},
     "after": {"name": "Main Kitchen"}, "changed": ["name"], "reason": null,
     "review_id": "<review>", "correction_id": "<correction>", "produced": ["<room r2>"]},
    {"kind": "object_added", "at": "...", "by": "<user>", "stage": null, "revision": 1, "action": "added",
     "entity_type": "photo_object", "entity_id": "<object>", "after": {"label": "light_fixture", "box": [0.1, 0.1, 0.2, 0.3], "mask_source": "none", "derived_from": null, "document_id": "<photo>"}, "...": null}
  ]
}
```

| `kind` | `action` | Source |
|---|---|---|
| `decision` | `approved`, `rejected` | `reviews` (the comment as `reason`) |
| `correction` | `modified` (extraction_field, room, component, asset), `added` (room_component), `moved` (photo_room), `merged` (room with `merge_into`), `reanalyzed` (photo) | `corrections`, with the rejection it belonged to and, when recorded, `produced`: the rows of the next revision it produced |
| `object_added`, `object_deleted` | `added`, `deleted` | manual `photo_objects` (`created_by`/`created_at`, `deleted_by`/`deleted_at`) |
| `intake` | `added`, `modified` | the `study.intake_set` events |

Deletion exists in two forms, both listed: a manual photo object soft-deleted (the row keeps
who and when), and a room merged into another (`merged`: the room is gone from the next
revision, its photos and rows are in the target). Components and assets are never deleted;
they are corrected, or a photo is re-analyzed.

## Provenance

```json
"provenance": {
  "origin": "engineer_modified",
  "correction_id": "<correction>", "review_id": "<review>", "stage": "components_review", "revision": 1,
  "at": "2026-10-29T18:40:02.117Z", "by": "<user>",
  "before": {"material": "wood", "count": 6}, "after": {"material": "laminate", "count": 8},
  "changed": ["count", "material"], "carried_from": "<component r1>"
}
```

| `origin` | Meaning |
|---|---|
| `model` | The pipeline wrote the row and no engineer changed it on the way (the rules table and the cache count as the model's: deterministic code, not the engineer). |
| `engineer_added` | The row exists because an engineer added it: a `room_component` correction (the component, and the asset built from it). |
| `engineer_modified` | An engineer's correction was applied when the row was copied into its revision. `before` is read from the copied row (`carried_from`), `after` is the correction's values, `changed` the fields it set. A row carried unchanged into later revisions keeps this: the value is still the engineer's. |

How it is resolved (`basis.db.repositories.provenance`): the append-only `corrections` table
is the source of truth, joined to the row's lineage. `apply_corrections` copies a revision's
rows into the next and names each copy's parent (`components.raw.carried_from`,
`rooms.carried_from`, an extraction's `raw.carried_from`; a photo assignment is keyed by its
document; an asset reaches its parent through its component). A correction at revision `r`
targets a row of revision `r` and is applied to that row's copy at `r + 1`, so walking a
row's ancestors and looking each up in `corrections` finds the correction that produced it;
the nearest ancestor with one wins. Where it is on the rows: `/fields` items, `/rooms` items
and their `photos[]` and `inventory[]`, `/assets` items (the takeoff and cost share the
asset's: an engineer cannot edit them directly), and the same members in the export's
`extraction[].fields[]`, `rooms[]`, `photos[]`, `components[]` and `assets[]`.

Rooms copied before `rooms.carried_from` existed have no lineage: a renamed one reports
`engineer_modified` with no correction named. Everything else resolves for old studies too.

## Firm conventions

An engineer's approved ruling classifies the same component in the firm's later studies
(docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md section 5). When the assets review is
approved, `promote_conventions` writes one convention per context key (`label|material|
attachment|room|property_class|rules version`) into `classification_cache`, scoped by the
study's firm: from every `manual` asset (the correction's reason is kept as the rationale)
and every `llm` asset with a verified reference citation; never from `rule` assets. A later
approval with the same answer adds to `approvals`, including an approved `cache` asset (the
engineer let the convention stand); a different answer replaces the convention and is
recorded as `convention.replaced`. A failure of the step is
logged with `BASIS-ASSETS-012` and does not stop the export.

The router checks the firm's conventions first, then the rules table, then the model. An
asset classified from a convention has `method` `cache`, is not sent to review for
disagreeing with the rules table, and carries where the position came from, as of its
classification, in `/assets` items and the export's `assets[]`:

```json
"convention": {
  "key": "toilet|porcelain|permanent|bathroom|residential_rental|2026-10-29.1",
  "study_id": "<study whose approval seeded it>", "reviewer_id": "<user>",
  "approved_at": "2026-10-29T18:40:02.117Z", "approvals": 3,
  "rule_key": "toilet", "agrees_with_rules": false,
  "citation": null
}
```

`agrees_with_rules` is null when the rules table has no row for the key; `citation` is
`firm convention, uncited` for a ruling without citations (a cited one has its citations
re-verified and listed like any other). `convention` is null for every other method. The
row's `provenance` stays `model` (deterministic code applied an earlier decision); the
convention member says whose decision.

## Where the error codes show

| Surface | Member |
|---|---|
| a problem response | `code` next to `type` |
| `GET .../workflow` → `status` | `failure_code` next to `failure`; `GET /v1/studies/{id}` → `failure_code` next to `failure_reason` |
| a timeline event (`activity.failed`, `stage.failed`, `corrections.refused`, `convention.replaced`) | `code` |
| `/assets` and `/extraction` | `review_codes`, the codes behind `review_reasons` |
| `activity_results.error`, the worker log, Temporal | `[BASIS-...]` at the end of the message |
