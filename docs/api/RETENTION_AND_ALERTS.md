# Client-data retention and operator alerts

Two things a firm needs before it trusts Basis with client files, and one the operator
needs before firms use it unattended:

1. **A retention rule**: how long a finished study's uploaded files stay in storage, set
   per firm, applied by a purge job that keeps the deliverables and the audit record.
2. **Alerts**: an email when a study fails or when a worker or relay stops heartbeating,
   raised once per occurrence through the outbox.

Backups and restores (Cloud SQL, the uploads bucket) and the Cloud Monitoring policies
that watch the processes themselves are infrastructure:
[infra/staging/RUNBOOK.md](../../infra/staging/RUNBOOK.md).

## 1. Retention

### 1.1 The rule: `orgs.retention_days`

```
GET /v1/orgs/{org_id}/retention            (members)
PUT /v1/orgs/{org_id}/retention            (owners)   {"retention_days": 365 | null}
```

```json
{"org_id": "0b8f0f5e-...", "retention_days": 365}
```

`null` (the default) keeps everything. `1` to `3650` days. No entity tag: one integer,
last write wins; the write is logged with who set it. The rule deletes nothing by itself;
the purge job applies it, so a shorter rule takes effect at the job's next run and `null`
set before that run keeps everything.

### 1.2 What the purge selects

A study is purged when all of these hold:

| Condition | Why |
|---|---|
| its firm has a rule | the default is to keep |
| `status` is `completed` or `archived` | a study in progress or waiting for review is never touched, however old |
| `purged_at` is null | once |
| `updated_at + retention_days < now` | the row's last change: the completion, the archive, a later report answer. Signing a report writes its own row and does not reset the clock |

Oldest change first; `--limit` (100) per run; `--org-id` narrows to one firm.

### 1.3 What goes and what stays

Everything the study owns in storage lives under `gs://<bucket>/studies/<id>/`. The purge
lists that prefix (`Storage.list`) and deletes every object **except** `exports/`:

| Deleted | Kept |
|---|---|
| the uploaded documents and photos (`<document>/<filename>`, `documents.gcs_uri`) | `exports/rev-N.json`: the export, the study's result |
| the rendered pages (`document_pages.gcs_uri`) | `exports/rev-N.assets.csv`, `rev-N.calls.jsonl` |
| the photo analyses (`analyses/`) and room reconciliations (`rooms/`) | `exports/rev-N.report.pdf` and the schedule files (docs/api/REPORT.md) |
| the model call payloads the ledger kept (`llm/`: prompts that embed the documents) | `exports/rev-N.report.signed.<id>.pdf`: the signed report |
| | **every database row**: the study, its documents (metadata: filename, size, sha256), extractions, rooms, components, assets, decisions, corrections, the ledger, the timeline |

The listing, not the database, decides what exists, so an object written without a row is
purged too. The database then checks the other way: every document and page row's object
must be among the deleted or already gone; one already gone is reported
(`BASIS-RETENTION-002`), not an error.

Afterwards `studies.purged_at` is set (`StudyOut.purged_at`) and the timeline gets:

| `event_type` | Actor | Carries |
|---|---|---|
| `study.purged` | system | `detail`: `retention_days`, `rule_from` (the `updated_at` the rule counted from), `eligible_since`, `documents`, `pages`, `objects_deleted`, `objects_kept` (the URIs), `missing` |

The timeline is append-only, so the record of the purge outlives the files it is about,
which is the point: a firm can show *that* the documents were deleted, when, and under
which rule, after they are gone. `GET .../report` still serves the kept files;
`GET .../documents/{id}/content` of a purged document answers 404 from storage.

Undo: the uploads bucket is versioned (RUNBOOK.md section 2), so for 30 days a purged
object is a noncurrent version and can be copied back; clear `purged_at` by hand. The
`study.purged` event stays.

### 1.4 Running it

```bash
python -m basis.retention purge --dry-run          # lists; changes nothing
python -m basis.retention purge                    # purges
python -m basis.retention purge --org-id <uuid> --limit 20
```

Same environment as the worker (`DATABASE_URL`, `GCS_BUCKET`; without a bucket, the local
storage directory). One JSON line per study, then a summary; exit 0, or 1 when a study's
objects could not be deleted (`BASIS-RETENTION-003`: that study is not marked purged and
nothing is recorded; run again). Each study is its own transaction.

Order of operations: objects are deleted first, the mark and the event committed after. A
crash in between leaves an unmarked study with fewer objects, which the next run finishes;
a crash after the commit cannot leave objects behind. Deleting is idempotent.

Scheduling is documented, not enabled (RUNBOOK.md 3.1): a Cloud Run job run daily by Cloud
Scheduler after the backup window, or a cron line on a local stack.

### 1.5 Not under the firm's rule: browser error records

`client_errors` (docs/api/CLIENT_ERRORS.md section 6) is operational data about the
application, not a client file: it names a user, a firm and a study id, never a document or
a price. It is kept a fixed `CLIENT_ERRORS_RETENTION_DAYS` (180) and deleted by
`python -m basis.retention purge-client-errors`, run on the same schedule as the purge.

### 1.6 Not under the firm's rule: demo requests

`demo_requests` (docs/api/DEMO_REQUESTS.md section 6) holds the public site's demo requests:
a visitor's name, work email, firm, role and note, given to be contacted about a demo. A
visitor belongs to no firm, so the firm's rule cannot apply; they are kept a fixed
`DEMO_REQUESTS_RETENTION_DAYS` (365) and deleted by
`python -m basis.retention purge-demo-requests`, run on the same schedule as the purge.

## 2. Alerts

### 2.1 What raises one

| Alert | When | Key (unique in `alerts`) |
|---|---|---|
| `study_failed` | `record_failure` marks a stage failed (`stage.failed` on the timeline), in the same transaction | `study-failed:<the activity's key>`: one per failure the workflow recorded, stable across the activity's retries |
| `heartbeat_stale` | the relay's check (every `ALERT_CHECK_SECONDS`, 60) finds no heartbeat of a service (worker, relay) younger than `ALERT_HEARTBEAT_STALE_SECONDS` (300) | `heartbeat-stale:<service>:<last heartbeat seen>`: one per stale period; a new one only after the service wrote again and fell silent again; `never` for a service with no row |

Raising is one `alerts` row plus one `notification` outbox row of `type: alert`, inserted
together; the unique key makes the second raise of the same occurrence a no-op (the insert
conflicts inside a SAVEPOINT and nothing is enqueued), so **nothing is sent twice from the
source**. `alerts` is also the answer to "was this sent, and when": the row, its outbox
row's status, and the relay's log.

Why 300 s and not the 45 s `GET /v1/ops/health` uses to call a service `down`: the health
page is a live view and may flicker through a deploy; an email should not. A deploy's
worker restart takes under a minute.

The relay checking itself is a limit: a dead relay alerts on nothing. The Cloud Monitoring
policy on the worker pool's instance count (RUNBOOK.md 4.1) covers the whole pool being
down, relay included.

### 2.2 The payload

```json
{"type": "alert", "kind": "study_failed", "key": "study-failed:study-5f0c...:0193...:record_failure:extracting:r1:1",
 "study_id": "5f0c6a52-...", "stage": "extracting", "revision": 1,
 "reason": "IngestError: Azure DI timed out [BASIS-INGEST-003]", "code": "BASIS-INGEST-003",
 "marked": true, "at": "2026-11-01T14:30:05+00:00"}

{"type": "alert", "kind": "heartbeat_stale", "key": "heartbeat-stale:worker:2026-11-01T14:00:00+00:00",
 "service": "worker", "last_heartbeat_at": "2026-11-01T14:00:00+00:00", "stale_for_s": 1805.0,
 "threshold_s": 300.0, "at": "2026-11-01T14:30:05+00:00"}
```

`reason` is the activity's error message (cut at 2000 characters), which never carries
document content, prompts or keys. Nothing else about the client is in an alert.

### 2.3 Delivery

The relay (`basis.relay`) is the one process that sends. For a `notification` row of
`type: alert` it renders a plain-text email (`basis.notify.render`: subject
`[Basis] (<env>) Study failed at <stage>: <code>` or `[Basis] (<env>) <service> heartbeat
is stale`, the facts, and the next step) addressed to `ALERT_EMAIL_TO`, with the alert's
key as the message's idempotency key (an HTTP provider that honours it drops a redelivery;
SMTP gets it as the `Message-ID`), and hands it to the configured `EmailSender`:

| Outcome | Outbox row |
|---|---|
| sent | `delivered` |
| sender failed, may succeed later (connection, 4xx) | retried with the relay's backoff (`BASIS-ALERT-003`) |
| recipient refused for good (5xx) | `dead` at once (`BASIS-ALERT-003`); the `alerts` row stays |
| `ALERT_EMAIL_TO` unset | the rendered alert goes to the relay's log at WARNING (`BASIS-ALERT-004`); `delivered` |
| payload not an alert this build renders | `dead` (malformed) |

Other `notification` types (`review_pending`) still go to the log, as before.

### 2.4 The email sender

`basis.email` (docs/api/FIRMS.md 4, shared with the invitation emails): `EmailMessage(to,
subject, text, idempotency_key)`, `EmailSender.send(message) -> SendResult`,
`EmailSendError` (retry) and `EmailRejectedError` (final), `LoggingEmailSender` (logs, never
sends) and `SmtpEmailSender`; `EMAIL_PROVIDER` (`log`, the default, or `smtp`) with
`EMAIL_FROM` / `SMTP_*` picks the transport for both. `basis.notify` re-exports those names
and adds only the alert rendering and the heartbeat monitor. `ALERT_EMAIL_TO` names who the
SMTP sender will write to.

### 2.5 Settings

| Setting | Default | Read by |
|---|---|---|
| `ALERT_EMAIL_TO` | unset (log only) | relay |
| `ALERT_HEARTBEAT_STALE_SECONDS` | 300 | relay |
| `ALERT_CHECK_SECONDS` | 60 | relay |

## 3. Codes

`BASIS-RETENTION-001` purged (outcome), `-002` an object already gone (outcome), `-003` a
delete failed (error, exit 1); `BASIS-ALERT-001` study-failed alert enqueued (outcome),
`-002` heartbeat stale (error), `-003` the sender failed (error), `-004` no recipient
(outcome). [docs/ERROR_CODES.md](../ERROR_CODES.md).

## 4. Rollout

Migration `0026_retention_alerts` (`orgs.retention_days`, `studies.purged_at`, `alerts`)
before the relay, the worker and the API: `record_failure` (worker) writes `alerts`; the
relay reads the heartbeats and sends. Until the migration is applied a worker on this
build fails `record_failure` on the missing table (Temporal retries it; the study is still
marked failed once the migration lands), so apply it first, as with every migration.
