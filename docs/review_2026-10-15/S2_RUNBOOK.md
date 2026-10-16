# Stage 2 runbook: run a study locally, end to end

Everything runs on this machine: Postgres in Docker, the Temporal dev server from the
`temporal` CLI, and the worker, the outbox relay and the API with `uv`. Commands run from
`backEnd/` unless stated otherwise. Each long-running process gets its own terminal.

Verified on 2026-10-15 (Apple Silicon, Docker Desktop, `temporal` 1.9.1 / Server 1.32.0,
Python 3.12 via `uv`), from a clean container: every step below, including the scripted
study of section 7 through the API (create, three uploads, start, three decisions,
export), the Temporal searches of section 8, and SIGTERM on all three processes. The
outputs quoted are from that run (ids differ on every run; sha256 values are shortened).

Re-verified on the Stage 3 code (v0.4.0, 2026-10-15) with the real activities and no model
provider configured: the study completes with the appraisal extracted from its text layer
and both photos flagged; logs, database state and Temporal UI screenshots are in
`docs/test-evidence/v0.4.0/runbook/`. Note that the default mutation rate limit is now 60
per minute, so the client script no longer sees 429s.

| Process | Command | Ports |
|---|---|---|
| Postgres (pgvector) | `docker run ... pgvector/pgvector:pg16` | 5544 |
| Temporal dev server | `temporal server start-dev` | gRPC 7233, UI 8233 |
| Worker | `uv run python -m basis.worker` | health 8080, metrics 9464 |
| Outbox relay | `uv run python -m basis.relay` | health 8081 |
| API | `uv run uvicorn basis.api.main:app --port 8000` | 8000 |

## 0. Prerequisites (once)

```bash
brew install temporal          # Temporal CLI (dev server + UI)
cd backEnd && uv sync          # project environment
```

`BASIS_ENV=dev` reads `backEnd/.env` if there is one; values exported in the shell
override it. The commands below export everything they need, so they work with or
without a `.env`. In every terminal:

```bash
export BASIS_ENV=dev
export DATABASE_URL=postgresql://basis:basis@localhost:5544/basis  # pragma: allowlist secret
export TEMPORAL_ADDRESS=localhost:7233   # the default; shown for clarity
```

## 1. Postgres (pgvector) in Docker

Port 5544 avoids clashing with other local Postgres instances.

```bash
docker run -d --name basis-pg \
  -e POSTGRES_USER=basis -e POSTGRES_PASSWORD=basis -e POSTGRES_DB=basis \
  -p 5544:5432 pgvector/pgvector:pg16
docker exec basis-pg pg_isready -U basis    # "/var/run/postgresql:5432 - accepting connections"
```

## 2. Temporal dev server (gRPC 7233, UI 8233)

`--db-filename` keeps workflows across restarts (omit it for an in-memory server):

```bash
temporal server start-dev --db-filename ~/.basis-temporal.db
temporal operator namespace describe --namespace default | head -2   # NamespaceInfo.Name default
```

## 3. Schema

```bash
uv run alembic upgrade head    # 0001 (initial schema), then 0002 (outbox status)
uv run alembic check           # "No new upgrade operations detected."
```

## 4. Worker

```bash
GIT_SHA=runbook uv run python -m basis.worker   # --health-port 8080 --metrics-port 9464
```

On start it registers the `StudyId`, `Stage` and `OrgId` search attributes on the dev
server (idempotent), then polls task queue `basis-studies`. In `dev` the logs are plain
text; in every other environment one JSON object per line with the same fields:

```
INFO     basis.worker.health: health endpoint on 0.0.0.0:8080/health
INFO     basis.worker.search_attributes: registered search attributes in default: OrgId, Stage, StudyId
INFO     basis.worker: worker started [build_id=runbook health_port=8080 metrics_port=9464 namespace=default task_queue=basis-studies temporal_address=localhost:7233 worker_versioning=False]
```

Checks:

```bash
curl -i localhost:8080/health                  # HTTP/1.0 200 OK {"status": "ok"}
curl -s localhost:9464/metrics | grep temporal_ | head
temporal operator search-attribute list | grep -E 'StudyId|Stage|OrgId'
```

Stop with Ctrl-C or `kill -TERM <pid>`: the worker stops polling, gives in-flight
activities up to 8 s (Cloud Run sends SIGKILL 10 s after SIGTERM), and exits:

```
INFO     basis.worker: shutdown requested; draining for up to 8s
INFO     temporalio.worker._worker: Beginning worker shutdown, will wait 0:00:08 before cancelling activities
INFO     basis.worker: worker stopped
```

An activity still running after 8 s is cancelled, its transaction rolls back
(`activity cancelled; its transaction was rolled back`) and the next worker runs it again
under the same idempotency key. Run `python -m basis.worker` directly (not under
`uv run`) when you need its own exit status; `uv run` reports the signal it received
instead (143 for SIGTERM).

Worker versioning (off by default): `TEMPORAL_WORKER_VERSIONING=true` makes the worker
register as deployment `TEMPORAL_DEPLOYMENT_NAME` (default `basis-worker`) with build id
`TEMPORAL_BUILD_ID` (default `GIT_SHA`) and auto-upgrade behavior. On the dev server the
version must then be made current before it receives tasks:

```bash
temporal worker deployment set-current-version \
  --deployment-name basis-worker --build-id "$GIT_SHA"
```

## 5. Outbox relay

The API never calls Temporal for a decision: it writes the review and an `outbox` row in
one transaction, and the relay delivers it.

```bash
uv run python -m basis.relay                     # loop; /health on :8081
uv run python -m basis.relay --health-port 9081  # another health port
uv run python -m basis.relay --once              # one batch, then exit
curl -s localhost:8081/health                    # {"status": "ok"}
```

```
INFO     basis.relay.delivery: outbox relay started [poll_seconds=2.0]
```

* Every `RELAY_POLL_SECONDS` (2 s) it claims up to 20 due `pending` rows
  (`FOR UPDATE SKIP LOCKED`, so several relays can run) and delivers them:
  `workflow_signal` as the `decide` update on `study-{study_id}` (update id
  `decision-{review_id}`, 30 s timeout), `notification` to the log.
* A refusal by the workflow's validator is final: the row is `delivered` with
  `last_error` and a WARNING `outbox message refused by its consumer; not retried`.
* Anything else that fails (Temporal down, RPC error, timeout, workflow not yet at the
  decision's stage) is retried after `min(2**attempts, 300)` s; the 50th failure makes
  the row `dead` (ERROR `outbox message is dead`). That is about 3.6 hours of retries.
* `/health` is 503 when the relay has not reached the database for 60 s (or ten poll
  intervals, if longer).
* SIGTERM/SIGINT: polling stops, a batch in flight gets 8 s to finish and is otherwise
  rolled back (its rows are simply due again), the process exits 0
  (`outbox relay stopped`, `outbox relay stopping [drain_seconds=8.0]`).
* Outside `dev`, logs are JSON with `outbox_id`, `study_id`, `kind`, `attempts`, `error`.

Inspect and repair the outbox (`docker exec -it basis-pg psql -U basis -d basis`):

```sql
-- what is waiting, failing, dead
SELECT status, count(*), max(attempts) FROM outbox GROUP BY status;
SELECT id, kind, attempts, next_attempt_at, last_error
  FROM outbox WHERE status = 'pending' AND attempts > 0 ORDER BY next_attempt_at;
SELECT id, kind, payload->>'study_id' AS study, attempts, last_error
  FROM outbox WHERE status = 'dead' ORDER BY created_at;
-- decisions the workflow refused (final; the engineer may need to decide again)
SELECT id, payload->>'study_id', payload->>'review_id', last_error
  FROM outbox WHERE status = 'delivered' AND last_error IS NOT NULL;

-- requeue a dead message after fixing the cause (delivery is idempotent by review id)
UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = now()
 WHERE id = '<outbox id>' AND status = 'dead';
```

## 6. API

For a local run, Firebase is replaced by the Auth emulator's token format: with
`FIREBASE_AUTH_EMULATOR_HOST` set (allowed only in `dev` and `test`), the Firebase SDK
accepts unsigned ID tokens whose audience is `FIREBASE_PROJECT_ID`, and makes no network
call (revocation is checked only in `prod`), so nothing has to listen on that address.
Run the real emulator there instead if you want to sign in through it.

```bash
GIT_SHA=runbook FIREBASE_PROJECT_ID=basis-local FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099 \
  uv run uvicorn basis.api.main:app --port 8000
curl -s localhost:8000/health
# {"status":"ok","service":"basis","version":"0.1.0","git_sha":"runbook","env":"dev",
#  "checks":{"db":{"status":"ok","latency_ms":20.23},"temporal":{"status":"ok","latency_ms":5.98}}}
```

A Temporal failure shows as `checks.temporal.status = "fail"` with status 200: decisions
keep working through the outbox, so a Temporal outage must not take the API out of the
load balancer. A database failure is 503, on `/health` and on every route that needs the
database (`service-unavailable` problem with `Retry-After: 5`; S2-C), never a 500.

Settings the relay and the workflow routes read:

| Variable | Default | Used by |
|---|---|---|
| `DATABASE_URL` | none (required) | API, worker, relay |
| `TEMPORAL_ADDRESS` | `localhost:7233` | API, worker, relay. The empty string runs the API without Temporal: the workflow routes then answer 503 and `/health` has no `temporal` check |
| `TEMPORAL_NAMESPACE` | `default` | API, worker, relay |
| `TEMPORAL_API_KEY` | unset | API, worker, relay. Temporal Cloud: API key and TLS |
| `TEMPORAL_TASK_QUEUE` | `basis-studies` | API (`start`), worker |
| `RELAY_POLL_SECONDS` | `2.0` | relay |
| `STUDY_BUDGET_USD`, `PHOTO_CONCURRENCY`, `REVIEW_REMINDER_DAYS`, `REVIEW_DEADLINE_DAYS` | `25.0`, `5`, `7`, `30` | API (`start` puts them in `StudyWorkflowInput`) |
| `GIT_SHA` | `unknown` | API (`StudyWorkflowInput.workflow_version`), worker (build id) |
| `RATE_LIMIT_MUTATIONS_PER_MIN`, `RATE_LIMIT_READS_PER_MIN` | `10`, `60` | API, per user |
| `FIREBASE_PROJECT_ID`, `FIREBASE_AUTH_EMULATOR_HOST` | unset | API (token audience; emulator tokens) |

The API and the relay create their Temporal client lazily: they start, and keep serving,
while Temporal is down.

Every `/v1` call needs `Authorization: Bearer <Firebase ID token>`; every POST needs an
`Idempotency-Key` (a UUID; reuse it only to retry the same request). The workflow routes:

| Route | Role | Answers |
|---|---|---|
| `POST /v1/studies/{id}/start` | engineer | 202; 409 `appraisal-required` (no confirmed appraisal), `study-archived`, `stale-state` (past `uploading` without a workflow), `workflow-missing` (past `extracting` but Temporal has no record: investigate, it is never restarted automatically); `workflow-closed` (the study's workflow ended failed, terminated, timed out or cancelled: it is not started again); 503 when Temporal is unreachable, with the study already `extracting`: repeat the request (any key) and it converges |
| `GET /v1/studies/{id}/workflow` | viewer | 200 (`status` is null when no worker answers the query); 404 before `start`; 503 |
| `POST /v1/studies/{id}/decisions` | engineer | 202 (queued for the relay); 409 stale stage or revision; 422 invalid body, or a correction whose `entity_type` the stage does not accept or whose `entity_id` is not in the study at that revision (`errors[].loc` names the correction's index) |
| `POST /v1/studies/{id}/cancel` | owner | 202; 404 before `start`; 409 `workflow-closed`; 503 |

## 7. A synthetic study end to end through the API

With the worker, the relay and the API running (sections 4-6), this script plays the
client: it mints an emulator token, creates a study, uploads the synthetic URAR fixture
and two synthetic photos from `tests/fixtures/synthetic/`, confirms each upload, starts
the workflow, approves the three review gates as each is reached (the relay delivers each
decision), waits for completion and reads the export. Synthetic data only.

Uploads with `LocalStorage` (no `GCS_BUCKET`): the API answers a `local-upload://` URL
whose signature lives in the API process, so the script writes the object where the
API's `LocalStorage` reads it (`$TMPDIR/basis-local-storage`, the same directory for both
processes when they run as the same user), then calls `.../complete`, which verifies size
and sha256 exactly as for GCS. The default limit of 10 mutations per minute per user is
below what one study needs (1 + 2 per document + 1 + 3), so the script waits out the
`Retry-After` of a 429 and retries with the same `Idempotency-Key`.

```bash
FIREBASE_PROJECT_ID=basis-local uv run python - <<'EOF'
import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import httpx

from basis.api.app import LOCAL_BUCKET, LOCAL_STORAGE_DIR
from basis.storage import LocalStorage
from basis.storage.base import object_uri

API = "http://127.0.0.1:8000"
PROJECT = os.environ["FIREBASE_PROJECT_ID"]
FIXTURES = Path("tests/fixtures/synthetic")
UPLOADS = [
    ("appraisal", FIXTURES / "urar_sample.pdf", "application/pdf"),
    ("photo", FIXTURES / "photos/01_kitchen.jpg", "image/jpeg"),
    ("photo", FIXTURES / "photos/02_bathroom.jpg", "image/jpeg"),
]


def segment(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def emulator_token(uid: str) -> str:
    """An unsigned ID token, accepted only because FIREBASE_AUTH_EMULATOR_HOST is set."""
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT, "sub": uid,
        "iat": now, "exp": now + 3600, "auth_time": now,
        "email": f"{uid}@example.test", "name": "Runbook Engineer",
    }
    return f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims)}."


http = httpx.Client(
    base_url=API, timeout=30, headers={"Authorization": f"Bearer {emulator_token('runbook')}"}
)


def post(path: str, body: dict | None = None) -> dict:
    key = str(uuid.uuid4())  # one key per logical request, reused when retrying it
    while True:
        response = http.post(path, json=body, headers={"Idempotency-Key": key})
        print(response.status_code, "POST", path.replace(sid, "{id}") if sid else path)
        if response.status_code != 429:  # 10 mutations/min per user by default
            break
        time.sleep(int(response.headers["Retry-After"]))
    response.raise_for_status()
    return response.json()


def wait(predicate, what: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = http.get(f"/v1/studies/{sid}/workflow").json()
        if predicate(view):
            return view
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {what}: {view}")


sid = ""
sid = post("/v1/studies", {"property_name": "12 Main St (runbook)"})["id"]
print("study", sid)
storage = LocalStorage(LOCAL_STORAGE_DIR)
for kind, path, content_type in UPLOADS:
    data = path.read_bytes()
    ticket = post(f"/v1/studies/{sid}/documents", {
        "kind": kind, "filename": path.name, "content_type": content_type,
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    })
    document = ticket["document"]
    # A browser PUTs to ticket["upload"]["url"]. LocalStorage's signed URLs live in the
    # API process, so write the object where the API's LocalStorage reads it instead.
    uri = object_uri(LOCAL_BUCKET, uuid.UUID(sid), uuid.UUID(document["id"]), document["filename"])
    storage.write(uri, data)
    post(f"/v1/studies/{sid}/documents/{document['id']}/complete")

started = post(f"/v1/studies/{sid}/start")
print("workflow", started["workflow_id"], "run", started["run_id"])
for stage in ("appraisal_review", "rooms_review", "assets_review"):
    wait(lambda v: (v["status"] or {}).get("awaiting") == stage, stage)
    accepted = post(f"/v1/studies/{sid}/decisions",
                    {"stage": stage, "revision": 1, "decision": "approve"})
    print("   ", stage, "approved; study now at", accepted["current_stage"])

done = wait(lambda v: v["execution_status"] == "COMPLETED", "completion")
print("execution", done["execution_status"], "stage", done["status"]["stage"],
      "decisions applied", len(done["status"]["decisions_applied"]))
print("runs", [(r["outcome"], r["input_revision"]) for r in done["runs"]])
export = storage.path_for(f"gs://{LOCAL_BUCKET}/studies/{sid}/exports/rev-1.json")
document = json.loads(export.read_text())
print("export", export)
print("  sha256", hashlib.sha256(export.read_bytes()).hexdigest())
print("  sections", sorted(document))
EOF
```

Output of the verification run (the 429s are the rate limit at work; the script waited
and retried them):

```
429 POST /v1/studies
201 POST /v1/studies
study 970dfb38-83ba-44aa-aa9d-cab641c1294b
201 POST /v1/studies/{id}/documents
200 POST /v1/studies/{id}/documents/397ae418-7bde-44df-b842-ecb58557d6f5/complete
201 POST /v1/studies/{id}/documents
200 POST /v1/studies/{id}/documents/0e53b339-c585-4c78-a9a5-0463adec0216/complete
201 POST /v1/studies/{id}/documents
200 POST /v1/studies/{id}/documents/ef48308c-f509-4fc8-9c19-545a915b776d/complete
202 POST /v1/studies/{id}/start
workflow study-970dfb38-83ba-44aa-aa9d-cab641c1294b run 01a1401f-26df-7caa-9ab3-44c94e3ef3a0
202 POST /v1/studies/{id}/decisions
    appraisal_review approved; study now at rooms_review
202 POST /v1/studies/{id}/decisions
    rooms_review approved; study now at assets_processing
429 POST /v1/studies/{id}/decisions
202 POST /v1/studies/{id}/decisions
    assets_review approved; study now at exporting
execution COMPLETED stage completed decisions applied 3
runs [('completed', 1)]
export /private/var/folders/.../T/basis-local-storage/basis-local/studies/970dfb38-83ba-44aa-aa9d-cab641c1294b/exports/rev-1.json
  sha256 2755ef336e9c...
  sections ['assets', 'checksums', 'components', 'extraction', 'format', 'photos', 'property_name', 'revision', 'rooms', 'study_id']
```

What each process logged for that study (timestamps trimmed):

```
# API
basis.api.routes.workflow: workflow started [request_id=b6eef8b7-... run_id=01a1401f-26df-7caa-9ab3-44c94e3ef3a0 study_id=970dfb38-...]
uvicorn.access: 127.0.0.1:49198 - "POST /v1/studies/970dfb38-.../start HTTP/1.1" 202 [request_id=b6eef8b7-...]
uvicorn.access: 127.0.0.1:49198 - "POST /v1/studies/970dfb38-.../decisions HTTP/1.1" 202 [request_id=7d412611-...]

# relay (one line per decision, about one poll interval after the API answered)
basis.relay.delivery: outbox message delivered [attempts=1 kind=workflow_signal outbox_id=6f663695-... study_id=970dfb38-...]
basis.relay.delivery: outbox message delivered [attempts=1 kind=workflow_signal outbox_id=dcef3dbf-... study_id=970dfb38-...]
basis.relay.delivery: outbox message delivered [attempts=1 kind=workflow_signal outbox_id=a1d38f66-... study_id=970dfb38-...]

# worker (the API moves the study row when it records a decision, so the workflow's
# transition finds some stages "already there")
basis.activities.transition: study 970dfb38-... at extracting revision 1 (already there) [activity=transition activity_id=transition:extracting:r1 attempt=1 run_id=01a1401f-...]
basis.activities.transition: study 970dfb38-... at appraisal_review revision 1 (moved) [...]
basis.activities.transition: study 970dfb38-... at rooms_review revision 1 (already there) [...]
basis.activities.transition: study 970dfb38-... at assets_processing revision 1 (already there) [...]
basis.activities.transition: study 970dfb38-... at assets_review revision 1 (moved) [...]
basis.activities.transition: study 970dfb38-... at exporting revision 1 (already there) [...]
basis.activities.transition: study 970dfb38-... at completed revision 1 (moved) [...]
```

And in the database afterwards:

```bash
docker exec basis-pg psql -U basis -d basis \
  -c "SELECT status, count(*), max(attempts) FROM outbox GROUP BY status;" \
  -c "SELECT r.outcome, count(*) AS activity_results,
             count(*) FILTER (WHERE a.status = 'completed') AS completed
        FROM runs r JOIN activity_results a ON a.run_id = r.id
       WHERE r.study_id = '<study id>' GROUP BY r.outcome;"
```

```
  status   | count | max
-----------+-------+-----
 delivered |     5 |   0      (this study's 3 plus 2 from an earlier, unfinished run)

  outcome  | activity_results | completed
-----------+------------------+-----------
 completed |               16 |        16
```

## 8. Watching a study in the Temporal UI

Open <http://localhost:8233>, namespace `default`. Each study is workflow
`study-<study_id>` of type `StudyWorkflow`. The List filter box takes the same queries as
the CLI:

```bash
temporal workflow list --query "StudyId = '<study uuid>'"
temporal workflow list --query "Stage = 'appraisal_review'"     # studies waiting at a gate
temporal workflow list --query "OrgId = '<org uuid>'"
temporal workflow query --workflow-id study-<id> --type status  # gate, revision, photos, budget
temporal workflow describe --workflow-id study-<id>             # search attributes, result
```

From the verification run:

```
$ temporal workflow list --query "StudyId = '970dfb38-83ba-44aa-aa9d-cab641c1294b'"
   Status                    WorkflowId                      Type        StartTime
  Completed  study-970dfb38-83ba-44aa-aa9d-cab641c1294b  StudyWorkflow  1 minute ago
$ temporal workflow list --query "Stage = 'assets_review'"
   Status                   WorkflowId                      Type         StartTime
  Running  study-28096da7-c8de-4914-8b67-b11bf21789e5  StudyWorkflow  2 minutes ago
$ temporal workflow query --workflow-id study-970dfb38-83ba-44aa-aa9d-cab641c1294b --type status
  QueryResult  {"awaiting":null,"budget_remaining_usd":24.971999999999998,"completed_photos":2,"decisions_applied":["9101f10a-...","68a04105-...","a653eb31-..."],"flagged_photos":null,"needs_review_stages":null,"pending_photos":0,"revision":1,"stage":"completed","stalled":false,"waiting_since":null}
$ temporal workflow describe --workflow-id study-970dfb38-83ba-44aa-aa9d-cab641c1294b
  Status          COMPLETED
  Result          {"cancel_reason":null,"export_sha256":"2755ef336e9c...","export_uri":"gs://basis-local/studies/970dfb38-83ba-44aa-aa9d-cab641c1294b/exports/rev-1.json","flagged_photos":null,"outcome":"completed","revision":1,"stage":"completed"}
```

In the workflow's page: the Event History shows every activity with its id
(`transition:rooms_review:r1`, `analyze_photo:<document>:r1`, ...); each decision appears
as `WorkflowExecutionUpdateAccepted` / `WorkflowExecutionUpdateCompleted` (three pairs in
the run above); timers are the 7-day reminders.

## 9. A study without the API (workflow only)

To exercise the workflow and the worker alone (no API, no relay), a study can be seeded
in the database and driven with the Temporal client directly.
`tests/lifecycletest_support.seed_study` writes a synthetic study with one uploaded
`appraisal_12_main_st.pdf`, three uploaded photos, random sha256 values and no files (the
Stage 2 activities read only the rows). The earlier version of this section imported
`seed_study` from `workflowtest_support`, which since S2-B seeds a study already at a
review stage without documents; the workflow cannot run that one.

```bash
PYTHONPATH=tests uv run python - <<'EOF'
import asyncio
from datetime import UTC, datetime
from lifecycletest_support import seed_study
from basis.config import load_settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.temporal.client import connect
from basis.workflow.contracts import Decision, StudyWorkflowInput
from basis.workflow.study_workflow import StudyWorkflow, workflow_id_for

async def main():
    settings = load_settings()
    engine = create_engine(settings)
    study = await seed_study(create_sessionmaker(engine))
    client = await connect(settings)
    handle = await client.start_workflow(
        StudyWorkflow.run,
        StudyWorkflowInput(study_id=str(study.study_id), org_id=str(study.org_id),
                           input_revision=1, workflow_version="runbook",
                           budget_usd=settings.study_budget_usd),
        id=workflow_id_for(str(study.study_id)), task_queue=settings.temporal_task_queue)
    print("started", handle.id)
    for stage in ("appraisal_review", "rooms_review", "assets_review"):
        while (await handle.query(StudyWorkflow.status)).awaiting != stage:
            await asyncio.sleep(0.2)
        print(stage, await handle.execute_update(
            StudyWorkflow.decide,
            Decision(review_id=f"{stage}-demo", stage=stage, revision=1, decision="approve",
                     reviewer_id="runbook", submitted_at=datetime.now(UTC)),
            id=f"decision-{stage}-demo"))
    print("result", await handle.result())
    await engine.dispose()

asyncio.run(main())
EOF
```

Output of the verification run:

```
started study-579d45ac-8d7e-45e7-84c4-a83e281d034f
appraisal_review accepted=True applied_revision=1 reason=None
rooms_review accepted=True applied_revision=1 reason=None
assets_review accepted=True applied_revision=1 reason=None
result outcome='completed' stage='completed' revision=1 export_uri='gs://basis-local/studies/579d45ac-8d7e-45e7-84c4-a83e281d034f/exports/rev-1.json' export_sha256='7e0f5a2e3791...' flagged_photos=() cancel_reason=None
```

## 10. Tests

```bash
uv run pytest -q                       # everything (Docker and the temporal CLI needed)
uv run pytest -q tests/lifecycle        # workflow lifecycle on the time-skipping server
uv run pytest -q tests/integration/relay tests/integration/api tests/failure \
  tests/api/test_workflow_routes.py tests/unit/relay tests/unit/temporal
```

The Temporal-backed tests start their own servers (`WorkflowEnvironment.start_local()`
or `start_time_skipping()`; the `temporal` CLI on `PATH` when installed, otherwise the SDK
downloads it). `tests/failure/` covers Temporal down (`test_temporal_unavailable.py`),
the worker killed or terminated mid-activity (`test_worker_kill.py`, real worker
processes, about 75 s) and the database going away (`test_db_outage.py`, a TCP proxy in
front of the test container).

## Cleanup

```bash
# Ctrl-C (or kill -TERM) the API, the relay, the worker and the dev server, then:
docker rm -f basis-pg
rm -f ~/.basis-temporal.db
rm -rf "${TMPDIR:-/tmp}/basis-local-storage"     # uploaded objects and exports
```
