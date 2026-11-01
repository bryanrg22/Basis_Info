# Revisions: reopening a completed study, the revision history, restoring a revision

A study's outputs are grouped by **revision**: every rejection at a review gate starts a new
one, the rows of the decided revision are never changed, and each copied row names the row it
came from (`carried_from`; docs/api/TIMELINE_AND_AUDIT.md, "Provenance"). Until now a
`completed` study was the end of the line: nothing could take it back to a gate. This page adds
three operations on that model, without changing it:

* **Reopen** (`POST /v1/studies/{id}/reopen`): a completed study goes back to a chosen review
  gate on a new revision; the gate shows the completed revision's rows, and every step after
  the gate recomputes.
* **Revision history** (`GET /v1/studies/{id}/revisions`, `.../revisions/{a}/diff/{b}`): one
  item per revision with who made it, at which gate, with which corrections, whether it was
  exported and signed; and the rows added, removed and changed between any two revisions.
* **Restore** (`POST /v1/studies/{id}/revisions/{n}/restore`): a new revision whose rows copy
  revision `n`, opened at the last gate revision `n` reached, so the engineer reviews what they
  restored before anything downstream recomputes.

The model stays append-only. Reopen and restore only ever **add** a revision: the previous
revision's rows, its export, its report PDF and its signature stay exactly as they are, and the
revision history shows them. Nothing here deletes or rewrites a revision.

The routes are in `basis/api/routes/revisions.py`, the history and the diff in
`basis/db/repositories/revision.py`, the copy in `basis/activities/carry_revision.py`, the
workflow's resumed start in `basis/workflow/study_workflow.py` (`StudyWorkflowInput.resume`).

## 1. The standard this follows

Versioned document systems do not undo: they append.

* **Git**: `git revert` records a new commit whose tree is the old state; history is never
  rewritten. **Wikipedia** reverts the same way: an undo is a new revision.
* **Google Docs** and **Notion** version history: "restore this version" makes the chosen
  version's content the current content; the versions after it stay in the history and can be
  restored in turn.
* **Temporal**: a closed execution cannot be signalled or continued. The only way to run a
  workflow id again after it completed is to start a new execution under that id with the
  `ALLOW_DUPLICATE` id-reuse policy (`ALLOW_DUPLICATE_FAILED_ONLY`, which `POST .../restart`
  uses for cancelled studies, refuses an id whose last execution completed); a running
  execution is a separate check, the id-conflict policy.

So: reopen and restore each start a **new revision** (never rewrite one) and a **new Temporal
execution** under the study's one workflow id (never continue the closed one).

## 2. Reopen: what is kept, what recomputes

The decision in one table. A reopen at gate `G` copies into the new revision everything the
gate `G` shows (what the stages before it produced) and leaves out what the stages after it
produce, so those stages run again. The copy is the one `apply_corrections` makes after a
rejection, with no corrections; the rows name their parents (`carried_from`), so provenance,
the audit listing and the diff work across the reopen.

| Reopened at | Carried into the new revision | Recomputed after the gate | Gates to pass again |
|---|---|---|---|
| `appraisal_review` | extraction + fields; rooms, photo assignments, per-photo components, engineer-added components, localized objects | the room step (`components_processing`), assets, export | appraisal, rooms, components, assets |
| `rooms_review` | the same | the room step, assets, export | rooms, components, assets |
| `components_review` | the same + the reconciled room inventories | assets, export | components, assets |
| `assets_review` | the same + the assets with their takeoffs, cost estimates and citations | export (and the conventions promotion, which skips keys already promoted) | assets |

Why the room inventories are left out below `components_review`: they are the room step's
output, which the components review shows; a reopen at the rooms review means the engineer
wants to change the grouping, and the inventories are rebuilt from the grouping they approve
(exactly what a rooms rejection does). Why the assets are copied, not recomputed, at
`assets_review`: the gate must show the assets as they were approved (recomputing them could
change prices with a newer cost index and would spend on the classifier); on approval only the
export reruns, on rejection `process_assets` reruns for the next revision as after any assets
rejection, and `asset` corrections name the copied rows.

What does **not** carry: nothing is re-analyzed, re-localized or re-voted by the reopen itself.
The photos' analyses are what the approved grouping was built on; a photo an engineer wants
re-analyzed is flagged with a `photo` correction at the components review, as always. The
rooms' file-name vote is not run again (the rooms are carried as the engineer last approved
them). The rooms gate of a reopened study therefore shows the rooms exactly as they were.

The response says what recomputes (`recomputed`, `reviews`, `carried`) and the same plan is
available without acting (`dry_run: true`, 200), so a client can show the warning and ask for
confirmation first.

**The execution.** The study's workflow id is `study-{id}` and its last execution is
`COMPLETED`. It cannot be continued: Temporal refuses signals and updates to a closed
execution, and `continue-as-new` is a decision the running workflow takes, not something a
client can ask of a completed one. A reopen therefore starts a **new execution** under the same
id (`WorkflowService.resume_study`: id-reuse `ALLOW_DUPLICATE`, id-conflict `FAIL`) whose input
carries `resume = {stage, from_revision, reason}`. The workflow then runs no intake step: it
records its run, runs one activity, `carry_revision` (the copy of section 2, one transaction,
idempotent on its key), and opens the gate on the new revision; from there the code path is the
ordinary one (rejections, retries, the conventions promotion, the export). Executions started
without `resume` take exactly the command sequence they took before (the recorded histories in
`tests/lifecycle/histories/` replay unchanged), so no workflow patch is needed: the branch is on
the input, which is in the history. The new execution has the components review (patch
`components-gate-v1`) whatever flow the completed one had, and `record_run_start` records that
on the study; the carried rows are the same tables either way.

Why not do the copy in the API transaction: the copy is the size of a revision (hundreds of
rows for a real study) and the API's rule is one short transaction per write with no Temporal
call inside it (`POST .../start`, `POST .../restart`). The route resets the study row and
returns; the workflow's first activity copies, heartbeating as `apply_corrections` does. Until
the copy is done the study reads `processing` at the gate, as it does after a decision, and
`gate.opened` marks when the rows are there.

### `POST /v1/studies/{id}/reopen` (engineer; owners too)

Body `{"stage": "<review gate>", "dry_run": false}`. `Idempotency-Key` as on every write.

| Answer | When |
|---|---|
| `202` `{workflow_id, run_id, revision, stage, from_revision, reason: "reopen", recomputed[], reviews[], carried[]}` | the study is at `stage` on `revision` (the previous one plus one, status `processing`) and a new execution runs; `gate.opened` follows once the rows are copied. |
| `200` the same without `workflow_id`/`run_id`/`reason` | `dry_run: true`: the plan, nothing written (every refusal below still applies). |
| `409` `study-not-completed` | the study is not `completed`: reject at the gate it waits at, or `retry`/`restart` a failed or cancelled one. |
| `409` `workflow-running` | an execution of the study is running (the completed one is still closing, or another reopen won the race). |
| `409` `study-archived` | unarchive it first (`POST .../unarchive`); an archived study is read-only on purpose. |
| `422` `validation` | `stage` is not a review gate (`appraisal_review`, `rooms_review`, `components_review`, `assets_review`). |
| `404` | the study is not visible, or has no workflow (never started: nothing to reopen). |
| `503` | Temporal is not configured or unreachable; `Retry-After` is set. The study row may already be reset (see concurrency); repeating the request reuses the reset. |

**Order of checks and the concurrency rule.** The route reads the study (role, archived,
workflow, `completed`), describes the execution with no transaction open (`RUNNING` is 409),
then in one short transaction takes the study's row lock and re-checks: a study still
`completed` is moved to `(stage, revision + 1, processing)` with `stage.entered` and
`study.reopened`; a study that is no longer completed is refused (`study-not-completed`),
**unless** the last of `study.reopened` / `revision.restored` / `study.restarted` /
`run.started` on its timeline is a reopen (or restore) of the same stage and source revision at
the current revision with no `run.started` after it: that reset was made by an earlier request
whose start did not happen yet (it is in flight, or it answered 503), and is reused without a
second revision bump. After the commit the execution is started; Temporal is the arbiter: of
two requests racing past the lock, the start with `FAIL` on conflict admits one and the other is
409 `workflow-running`, and the row is consistent with the one that runs. A test runs two
reopens concurrently against the real database and the fake Temporal and gets one 202 and one
409; another starts two executions concurrently on the Temporal test server and sees one admitted.

**Timeline.** `stage.entered` (engineer; `before` `{completed, N}`, `after` `{stage, N+1}`),
`study.reopened` (engineer; `stage`, `revision` N+1; `detail` `{from_revision, reason,
recomputed, reviews, carried, workflow_id}`), then from the workflow: `run.started`,
`revision.carried` (system; `detail` `{from_revision, to_revision, through, reason}` and the
row counts), `gate.opened`. The audit listing shows the reopen as an engineer action.

## 3. The revision history

### `GET /v1/studies/{id}/revisions` (viewer)

One item per revision, 1 to the current one, oldest first, no pagination (a study has a few
revisions; one per rejection, reopen or restore). Everything is derived from what exists:
`study_events`, `reviews`, `corrections`, `report_signatures`; no new bookkeeping.

```json
{
  "study_id": "5f0c6a52-…",
  "current_revision": 3,
  "items": [
    {
      "revision": 1,
      "started_at": "2026-11-01T14:02:11Z",
      "ended_at": "2026-11-01T14:40:02Z",
      "origin": {"kind": "start", "stage": null, "actor_id": "…", "at": "…", "review_id": null, "from_revision": null},
      "corrections": {},
      "decisions": [{"stage": "appraisal_review", "decision": "approve", "reviewer_id": "…", "review_id": "…", "at": "…"}, …],
      "stage_reached": "completed",
      "export": {"uri": "gs://…/exports/rev-1.json", "sha256": "…", "at": "…"},
      "signature": {"id": "…", "signed_by": "…", "signed_at": "…"},
      "current": false
    },
    {
      "revision": 2,
      "origin": {"kind": "reopen", "stage": "rooms_review", "actor_id": "…", "at": "…", "review_id": null, "from_revision": 1},
      "corrections": {},
      …
    },
    {
      "revision": 3,
      "origin": {"kind": "rejection", "stage": "rooms_review", "actor_id": "…", "at": "…", "review_id": "…", "from_revision": 2},
      "corrections": {"room": 1, "photo_room": 1},
      …
    }
  ]
}
```

* `origin`: what made the revision. `start` (revision 1), `rejection` (the decision's reviewer,
  the gate it was made at, the review id), `reopen`, `restore` (`from_revision` the revision
  copied), `restart` (`POST .../restart`). From the timeline; for a study older than the
  timeline the rejection is read from `reviews` and the actor is its reviewer.
* `corrections`: counts by kind of the corrections the decision that made this revision carried
  (`corrections` rows of the decided revision, applied here); `{}` for a reopen or a restore.
* `decisions`: the reviews made at this revision, in order.
* `stage_reached`: the furthest stage the revision got to (`completed` for one that was
  exported); it is the gate a restore of this revision opens at.
* `export`: the `export.written` event of the revision (`null` when it was not exported);
  `signature`: its `report_signatures` row (`null` when unsigned). One signature per revision,
  never rewritten: a reopened study's new revision has its own export and is signed on its own.
* `started_at`/`ended_at`: when the study entered the revision and when the next one started
  (`null` on the current).

### `GET /v1/studies/{id}/revisions/{a}/diff/{b}` (viewer)

The rooms, components and assets **added**, **removed** and **changed** from revision `a` to
revision `b`, keyed through lineage: a row of `b` is the same row as a row of `a` when the two
share a **lineage root**, the row of the earliest revision both descend from by their
`carried_from` chains (`rooms.carried_from`, `components.raw.carried_from`; an asset through
its component, since assets are rebuilt per revision and carry their lineage through the
component they price; a concealed row, which has no component, by its label). For successive
revisions the root pairs each row with its ancestor at `a`; after a restore, whose rows descend
from the restored revision and skip the ones between, it still pairs each row with its
counterpart, so the diff 3 → 4 of a revision 4 restored from 1 shows revision 3's changes
undone. A row of `b` with no counterpart is `added` (the model rewrote it: a re-analyzed
photo's components, a rebuilt inventory, a recomputed asset of a changed component), a row of
`a` with no counterpart in `b` is `removed`. `a > b` is allowed and reads as the reverse diff.

```
GET /v1/studies/{id}/revisions/1/diff/3?limit=100
{
  "study_id": "…", "from_revision": 1, "to_revision": 3,
  "counts": {"rooms": {"added": 0, "removed": 1, "changed": 1, "unchanged": 1},
             "components": {…}, "assets": {…}},
  "added":   [{"kind": "component", "id": "…", "label": "light_fixture"}],
  "removed": [{"kind": "room", "id": "…", "label": "Bath"}],
  "changed": [
    {"kind": "room", "from_id": "…", "to_id": "…", "label": "Main Kitchen",
     "changes": {"name": {"before": "Kitchen", "after": "Main Kitchen"}}},
    {"kind": "component", "from_id": "…", "to_id": "…", "label": "kitchen_cabinet",
     "changes": {"material": {"before": "wood", "after": "laminate"}, "count": {"before": 6, "after": 8},
                 "room": {"before": {"id": "…", "name": "Kitchen"}, "after": {"id": "…", "name": "Main Kitchen"}}}}
  ],
  "next_cursor": null
}
```

Compared fields: rooms `name`, `room_type`, `floor`; components `label`, `material`,
`attachment`, `count` (`raw.count`) and `room` (the room's lineage at `a`, so a moved photo's
row shows the move); assets `category`, `bucket`, `recovery_period`, `irs_section`, `method`,
`quantity`, `unit`, `unit_cost`, `total`. `changed` is keyset-paginated (`limit` 1..200,
default 100; `cursor`), ordered rooms, components, assets, then by id; `added` and `removed`
are complete (ids and labels only). 404 `revision-not-found` when `a` or `b` is not a revision
of the study. Extraction fields are not in the diff (the audit listing and each field's
`provenance` cover them); they can be added the same way.

## 4. Restore

### `POST /v1/studies/{id}/revisions/{n}/restore` (engineer)

Body optional `{"dry_run": false}`. A new revision `N + 1` whose rows copy revision `n`'s state
(section 2's copy with `from_revision = n`, through the last gate revision `n` reached), opened
at **that gate**: the engineer reviews what they restored, and nothing before the gate is rerun.
Which gate: the furthest review stage revision `n` entered (`stage.entered` on the timeline;
for a revision with no events, the furthest its rows reach: assets, components, rooms,
extraction). A revision that completed restores at `assets_review` (its assets are copied; only
the export reruns on approval); one that was rejected at the rooms review restores at
`rooms_review` (its grouping is copied; the room step, assets and export rerun).

| Answer | When |
|---|---|
| `202` `{…, reason: "restore", from_revision: n, …}` as the reopen's | the study is at the gate on revision `N + 1` and a new execution copies revision `n` into it. |
| `200` the plan | `dry_run: true`. |
| `404` `revision-not-found` | `n` is not a revision of the study. |
| `409` `revision-current` | `n` is the current revision: reopen it at a gate instead. |
| `409` `revision-not-restorable` | revision `n` never reached a review gate (cancelled during extraction): there is no reviewed state to restore. |
| `409` `study-not-completed`, `workflow-running`, `study-archived`; `404`; `503` | as for reopen. |

The same preconditions as reopen (`completed`, not running): a study waiting at a gate is
changed by rejecting there, and a restore while an execution runs would have to cancel it.
Recorded as `revision.restored` (engineer; `detail.from_revision = n`, `detail.stage`, the plan)
with its `stage.entered`; never deletes or rewrites any revision. A restore of revision 1 after
revisions 2 and 3 gives revision 4 with revision 1's rows (the diff 1 → 4 is empty for the
carried tables) and revisions 2 and 3 stay in the history with their exports and signatures.

## 5. Client sequence

```
GET  /v1/studies/{id}/revisions                       -> the history (who, when, what, signed)
GET  /v1/studies/{id}/revisions/{a}/diff/{b}           -> what changed between two of them
POST /v1/studies/{id}/reopen {stage, dry_run: true}    -> 200 the plan: show "steps X, Y will recompute"
POST /v1/studies/{id}/reopen {stage}                   -> 202; poll GET /workflow until status.awaiting
POST /v1/studies/{id}/revisions/{n}/restore            -> 202; same
```

## 6. Errors

| Code | Problem type | Status | When |
|---|---|---|---|
| `BASIS-STUDY-030` | `study-not-completed` | 409 | reopen or restore of a study that is not `completed` |
| `BASIS-STUDY-031` | `workflow-running` | 409 | an execution of the study is running |
| `BASIS-STUDY-032` | `revision-not-found` | 404 | a revision number the study does not have (restore, diff) |
| `BASIS-STUDY-033` | `revision-current` | 409 | restoring the current revision |
| `BASIS-STUDY-034` | `revision-not-restorable` | 409 | the revision never reached a gate |

## 7. Verified and not

The lifecycle tests (`tests/lifecycle/test_reopen.py`, Temporal's time-skipping test server,
real Postgres, the real activities with scripted models) complete a study, sign its revision,
then reopen it at each of the four gates and check the second execution's activity sequence
(what reran, what did not), that the first revision's export bytes and signature row are
untouched, that the new revision's export is written, and the restore round trip (revision 1
restored after a rename in revision 3; the diff 1 → 4 is empty); the API tests
(`tests/api/test_revisions.py`, the fake Temporal) cover every refusal, the dry run, the
history and the diff on the seeded two-revision study, the pagination, the restore refusals,
and two concurrent reopens. Not verified: a reopen through a deployed API and worker (the API
tests use the fake Temporal, the lifecycle tests bypass the API; the two halves are the same
code as `restart`'s, whose live path is also unverified); the frontend.
