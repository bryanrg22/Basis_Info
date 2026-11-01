# Staging runbook: backups, restores, retention, alerts

Operating procedures for the staging project `basis-staging-512000` (architecture and
the park/unpark procedure: [docs/deploy/STAGING.md](../../docs/deploy/STAGING.md)). Every
command below is `gcloud`; set the project once:

```bash
gcloud config set project basis-staging-512000
```

Status 2026-11-01: sections 1, 2 and 4 describe Terraform that has been validated
(`terraform fmt -check`, `terraform validate`) and planned read-only against the parked
staging state, **not applied**. Staging stays parked; nothing here has been created in the
cloud. Section 3 (retention) is application code, tested on PostgreSQL with local storage.

## 1. Database backups and point-in-time recovery

What `sql.tf` declares for `staging-basis-pg` (PostgreSQL 16, Enterprise edition):

| Setting | Value | Why |
|---|---|---|
| Automated backups | daily, 08:00 UTC (01:00 Pacific) | Google's standard; outside working hours |
| Backups kept | 30 (`sql_backup_retained`, `retention_unit = COUNT`) | one a day, so 30 days of restore points |
| Point-in-time recovery | on, 7 days of write-ahead logs (`sql_pitr_days`) | the edition's maximum (Enterprise 1 to 7; Enterprise Plus up to 35) |
| Deletion protection | Terraform `deletion_protection` and the API's `deletion_protection_enabled` | `terraform destroy`, the console and `gcloud sql instances delete` all refuse |

Cost: backups are billed as backup storage ($0.08/GB-month in us-central1); the instance's
data is a few hundred MB, so 30 backups stay in cents. Write-ahead logs for an instance
that enabled PITR after 2023-01-09 are stored in Cloud Storage at no extra charge and use
no disk. The instance was created 2026-10-17, so enabling PITR puts its logs there.

Caveat while parked: automated backups run only while the instance is running (activation
policy `ALWAYS`); a parked instance (`NEVER`) keeps its existing backups and takes none.
Enabling PITR is an instance settings update; whether Cloud SQL accepts it on a stopped
instance was not tested (the plan is read-only). If the apply is refused, unpark first
(`-var parked=false`), apply, park again.

### 1.1 Restore the database to a point in time

PITR always creates a **new** instance; the current one is untouched, so this is safe to
try. Timestamps are UTC, RFC 3339.

```bash
# The window you can restore within
gcloud sql instances get-latest-recovery-time staging-basis-pg
gcloud sql backups list --instance=staging-basis-pg

# Clone the instance as it was at that moment (minutes; a new instance is billed)
gcloud sql instances clone staging-basis-pg staging-basis-pg-pitr \
  --point-in-time '2026-11-01T14:30:00.000Z'
```

Then either inspect the clone and copy what is needed, or switch the application to it:

```bash
# The clone keeps the users and passwords; only the connection name changes.
CONN=$(gcloud sql instances describe staging-basis-pg-pitr --format='value(connectionName)')
# Write the new DATABASE_URL (same user, password and database, new host) as a new
# secret version, never on a command line:
infra/staging/scripts/set-db-password.sh   # prompts; or edit the URL's host= by hand
printf %s "postgresql+asyncpg://basis:<password>@/basis?host=/cloudsql/$CONN" \
  | gcloud secrets versions add staging-database-url --data-file=-
# Point the Cloud Run resources at the clone (the connector volume names the instance):
cd infra/staging && terraform apply   # after changing google_sql_database_instance to the clone
```

Switching the application is a Terraform change (the API's, the jobs' and the proxy
sidecar's instance name come from `google_sql_database_instance.staging`), so in practice:
restore into the clone, verify, then import or rename so Terraform manages the clone, and
delete the damaged instance only after `deletion_protection` is lowered on purpose.

### 1.2 Restore an automated backup

A backup restore **overwrites the target instance** (its data and its PITR logs) and
needs the target's read replicas deleted first (none exist in staging). Restore into the
same instance only when the point-in-time clone above is not enough:

```bash
gcloud sql backups list --instance=staging-basis-pg
gcloud sql backups restore <BACKUP_ID> --restore-instance=staging-basis-pg

# Or into another instance (created first with the same version and tier):
gcloud sql backups restore <BACKUP_ID> \
  --restore-instance=staging-basis-pg-restore --backup-instance=staging-basis-pg
```

Take an on-demand backup of the target before overwriting it:

```bash
gcloud sql backups create --instance=staging-basis-pg --description="before restore"
```

### 1.3 After any database restore

1. Run the migrations job to confirm the schema is at head (`gcloud run jobs execute
   staging-basis-migrate --region us-central1 --wait`); a restore to a point before a
   deploy's migration leaves the schema behind the running code.
2. Temporal's state (the VM's own Postgres) is separate: a study whose workflow ran on
   after the restore point has a database behind its workflow. Cancel and retry those
   studies, or restore the Temporal data disk snapshot from the same time.
3. The outbox: rows restored as `pending` are delivered again; consumers are idempotent.

## 2. Uploaded files: versioning and soft delete

`storage.tf` turns on object versioning for `staging-basis-staging-512000-uploads`:

* A delete or an overwrite makes the previous version **noncurrent**; it stays in the
  bucket for `uploads_noncurrent_days` (30) and is then deleted by the lifecycle rule.
* An object rewritten more than 10 times keeps the 10 newest versions.
* GCS soft delete (7 days, the default, stated explicitly) holds every deleted version,
  the lifecycle rule's included, 7 more days.
* Staging only: live objects older than 90 days are deleted (made noncurrent). In
  production the firm's retention rule governs instead (section 3); drop that rule there.

So a client file deleted by mistake is recoverable for 30 days as a noncurrent version
and for 7 days after that from soft delete.

### 2.1 Find the versions of an object

```bash
# zsh: quote paths with '#' so extended globbing does not expand them
gcloud storage ls --all-versions --full 'gs://staging-basis-staging-512000-uploads/studies/<study id>/'
```

Every line is `OBJECT#GENERATION`; `--full` shows which are noncurrent and since when.

### 2.2 Restore a deleted or overwritten object

Copying a noncurrent version makes it the live version again (the copy is a new
generation; the noncurrent one stays until its lifecycle rule):

```bash
gcloud storage cp \
  'gs://staging-basis-staging-512000-uploads/studies/<study>/<document>/appraisal.pdf#1761900000000000' \
  'gs://staging-basis-staging-512000-uploads/studies/<study>/<document>/appraisal.pdf'
```

The application finds the object by its name (`documents.gcs_uri`), so no database change
is needed once the live version is back.

### 2.3 Restore a version soft delete still holds

After the 30 noncurrent days, a version is soft-deleted for 7 days:

```bash
gcloud storage ls --soft-deleted 'gs://staging-basis-staging-512000-uploads/studies/<study>/**'
gcloud storage restore 'gs://staging-basis-staging-512000-uploads/studies/<study>/<document>/appraisal.pdf#1761900000000000'
# Everything deleted in a window (asynchronous):
gcloud storage restore 'gs://staging-basis-staging-512000-uploads/**' --async \
  --deleted-after-time=2026-11-01T00:00:00Z --deleted-before-time=2026-11-02T00:00:00Z
```

### 2.4 A study purged by the retention rule

The purge (section 3) deletes the uploads, pages, analyses and model payloads of a study
and keeps `studies/<id>/exports/`. The deleted objects follow the same path: noncurrent for
30 days, soft-deleted for 7 more. To undo a purge inside that window, copy each noncurrent
version back (2.2) and clear `studies.purged_at`; the `study.purged` timeline event stays
(the timeline is append-only), which is the audit record of both.

## 3. Client-data retention (the purge job)

The rule is per firm (`orgs.retention_days`, null by default: keep everything), set by an
owner with `PUT /v1/orgs/{org_id}/retention` (docs/api/RETENTION_AND_ALERTS.md). The job
purges every study of such a firm that is `completed` or `archived`, not yet purged, and
whose row last changed more than `retention_days` ago:

```bash
# From the backend image, with DATABASE_URL and GCS_BUCKET (the worker's environment):
python -m basis.retention purge --dry-run      # lists what would be purged, changes nothing
python -m basis.retention purge                # deletes the objects, marks the studies purged
python -m basis.retention purge --org-id <uuid> --limit 20
```

Output is one JSON line per study (what was deleted and what was kept) and a summary line.
What it keeps: `studies/<id>/exports/` (the export JSON, the assets CSV, the calls transcript,
the report PDF and schedule files, the signed report), every database row, and the timeline,
to which it appends `study.purged`. A second run over the same studies is a no-op.

### 3.1 Scheduling it (documented, not enabled)

Not scheduled anywhere yet, by decision: staging is parked and no firm has a rule. When a
firm sets one, run it daily as a Cloud Run job triggered by Cloud Scheduler (the pattern of
`staging-basis-migrate`), both created with gcloud once (or added to `run.tf`):

```bash
gcloud run jobs create staging-basis-retention --region us-central1 \
  --image "$(gcloud run jobs describe staging-basis-migrate --region us-central1 --format='value(template.template.containers[0].image)')" \
  --service-account staging-worker@basis-staging-512000.iam.gserviceaccount.com \
  --set-cloudsql-instances basis-staging-512000:us-central1:staging-basis-pg \
  --set-secrets DATABASE_URL=staging-database-url:latest \
  --set-env-vars BASIS_ENV=staging,GCS_BUCKET=staging-basis-staging-512000-uploads \
  --command python --args=-m,basis.retention,purge --max-retries 0 --task-timeout 3600

gcloud services enable cloudscheduler.googleapis.com   # 3 jobs free per billing account
gcloud scheduler jobs create http staging-basis-retention-daily --location us-central1 \
  --schedule "30 9 * * *" --time-zone UTC \
  --uri "https://run.googleapis.com/v2/projects/basis-staging-512000/locations/us-central1/jobs/staging-basis-retention:run" \
  --http-method POST \
  --oauth-service-account-email staging-deployer@basis-staging-512000.iam.gserviceaccount.com
```

(The deployer has `run.developer`, which includes `run.jobs.run`.) 09:30 UTC is after the
08:00 backup, so the day's backup still holds the purged objects' metadata and the backup
and the noncurrent versions cover the same window. A local stack runs the same command from
cron or `docker compose run --rm worker python -m basis.retention purge`.

## 4. Alerts

### 4.1 From Cloud Monitoring (`alerts.tf`, plan only)

Created with `-var alerts_enabled=true` (together with `-var parked=false`: a parked
staging fails all of them). `alert_email` creates an email channel;
`alert_notification_channels` adds existing ones (Slack, PagerDuty, created in the
console). Each policy's notification carries its runbook text.

| Policy | Fires when | First look |
|---|---|---|
| API /health failing | the uptime check (3 regions, every 5 min) fails more than once in 20 min | the service's revisions and logs; Cloud SQL |
| API 5xx rate above 5 % | 5xx / all requests > 0.05 for 5 min (inactive with no traffic) | API logs: BASIS-API-019/020/022/024 |
| Worker pool has no instance | `container/instance_count` of `staging-basis-worker` < 1 (or absent) for 5 min | the pool's instances; the three containers' logs; `GET /v1/ops/health` |
| Cloud SQL not up | `database/up` < 1 (or absent) for 5 min | the instance's operations; if parked on purpose, turn alerts off |
| Workflow failure code logged | a `BASIS-WF-` code (except WF-010/011) in the worker's logs; at most one notification per 5 min | `GET /v1/studies/{id}/timeline?event_type=stage.failed`, then `POST .../retry` |

### 4.2 From the application (`basis.notify`)

The relay emails `ALERT_EMAIL_TO` (unset: the alert is logged at WARNING and counted
delivered) through the configured email sender:

* **A study failed**: `record_failure` writes `stage.failed` and, in the same transaction,
  one `alert` outbox row keyed by the failure (`study-failed:<activity key>`), so a retried
  activity never produces a second email.
* **A heartbeat went stale**: the relay checks `service_heartbeats` every
  `ALERT_CHECK_SECONDS` (60); a service (worker, relay) whose newest heartbeat is older
  than `ALERT_HEARTBEAT_STALE_SECONDS` (300) gets one alert per stale period (keyed by that
  last heartbeat), and a new one only after it came back and went stale again.

Both kinds are rows of the `alerts` table (the key is unique) with their outbox row, so
"was this sent, and when" is a query. docs/api/RETENTION_AND_ALERTS.md has the payloads.

## 5. Verifying this document

Checked on 2026-11-01: `terraform fmt -check` and `terraform validate` on `infra/staging`
(provider hashicorp/google 8.7.0); `terraform plan -lock=false` against the real state
(read-only; the output is summarised in the pull request and in EVAL_REPORT section 55);
the metric types and resource types of section 4.1 read from the project's metric
descriptors. Not done: no apply, no restore drill (the commands are Google's documented
ones, not exercised against this instance), no scheduler created, no alert fired.
