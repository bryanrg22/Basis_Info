# Stage 2 runbook: run the StudyWorkflow locally

Everything runs on this machine: Postgres in Docker, the Temporal dev server from the
`temporal` CLI, the worker (and, once S2-B is merged, the outbox relay and the API) with
`uv`. Commands are run from `backEnd/` unless stated otherwise.

Verified on 2026-10-15 (Apple Silicon, Docker Desktop, `temporal` 1.9.1 / Server 1.32.0,
Python 3.12 via `uv`): steps 1-4, a synthetic study run end to end against the worker,
the Temporal UI search, and the SIGTERM drain. Steps 5-6 are placeholders for S2-B.

## 0. Prerequisites (once)

```bash
brew install temporal          # Temporal CLI (dev server + UI)
cd backEnd && uv sync          # project environment
```

`BASIS_ENV=dev` reads `backEnd/.env`; values exported in the shell override it. The
commands below export everything they need, so they work with or without a `.env`.

## 1. Postgres (pgvector) in Docker

Port 5544 avoids clashing with other local Postgres instances.

```bash
docker run -d --name basis-pg \
  -e POSTGRES_USER=basis -e POSTGRES_PASSWORD=basis -e POSTGRES_DB=basis \
  -p 5544:5432 pgvector/pgvector:pg16
docker exec basis-pg pg_isready -U basis
```

## 2. Temporal dev server (gRPC 7233, UI 8233)

In its own terminal; `--db-filename` keeps workflows across restarts (omit it for an
in-memory server):

```bash
temporal server start-dev --db-filename ~/.basis-temporal.db
```

## 3. Schema

```bash
export BASIS_ENV=dev
export DATABASE_URL=postgresql://basis:basis@localhost:5544/basis  # pragma: allowlist secret
uv run alembic upgrade head
uv run alembic check           # "No new upgrade operations detected."
```

## 4. Worker

In its own terminal (same exports as step 3):

```bash
export BASIS_ENV=dev
export DATABASE_URL=postgresql://basis:basis@localhost:5544/basis  # pragma: allowlist secret
export TEMPORAL_ADDRESS=localhost:7233        # default; shown for clarity
uv run python -m basis.worker                 # --health-port 8080 --metrics-port 9464
```

On start it registers the `StudyId`, `Stage` and `OrgId` search attributes on the dev
server (idempotent), then polls task queue `basis-studies`. Log lines seen in the
verification run (JSON when `BASIS_ENV` is not `dev`):

```
{"severity": "INFO", "message": "health endpoint on 0.0.0.0:8080/health", "logger": "basis.worker.health", ...}
{"severity": "INFO", "message": "registered search attributes in default: OrgId, Stage, StudyId", "logger": "basis.worker.search_attributes", ...}
{"severity": "INFO", "message": "worker started", "logger": "basis.worker", ..., "task_queue": "basis-studies", "namespace": "default", "temporal_address": "localhost:7233", "build_id": "local-check", "worker_versioning": false, "metrics_port": 9464, "health_port": 8080}
```

Checks:

```bash
curl -i localhost:8080/health                  # HTTP/1.0 200 OK {"status": "ok"}
curl -s localhost:9464/metrics | grep temporal_ | head
temporal operator search-attribute list | grep -E 'StudyId|Stage|OrgId'
```

Stop with Ctrl-C or `kill -TERM <pid>`: the worker stops polling, gives in-flight
activities up to 8 s, and exits (`shutdown requested; draining for up to 8s` ...
`worker stopped`). Run `python -m basis.worker` directly (not under `uv run`) when you
need its own exit status; `uv run` reports the signal it received instead.

Worker versioning (off by default): `TEMPORAL_WORKER_VERSIONING=true` makes the worker
register as deployment `TEMPORAL_DEPLOYMENT_NAME` (default `basis-worker`) with build id
`TEMPORAL_BUILD_ID` (default `GIT_SHA`) and auto-upgrade behavior. On the dev server the
version must then be made current before it receives tasks:

```bash
temporal worker deployment set-current-version \
  --deployment-name basis-worker --build-id "$GIT_SHA"
```

## 5. Outbox relay (S2-B, placeholder)

```bash
# Owned by S2-B; expected once merged:
uv run python -m basis.relay
```

## 6. API (S2-B routes, placeholder)

```bash
# Owned by S2-B; expected once merged (same exports as step 3, plus FIREBASE_PROJECT_ID
# for real tokens):
uv run uvicorn basis.api.main:app --reload --port 8000
# POST /v1/studies/{id}/start, POST /v1/studies/{id}/decisions, GET /v1/studies/{id}/workflow
```

## 7. A synthetic study without the API

Until the API routes land, a study can be driven directly. This is what the
verification run did (`tests/workflowtest_support.seed_study` writes a synthetic study:
one `appraisal_12_main_st.pdf`, three photos, random sha256 values, no real files):

```bash
# with BASIS_ENV and DATABASE_URL exported as in step 3:
PYTHONPATH=tests uv run python - <<'EOF'
import asyncio
from datetime import UTC, datetime
from workflowtest_support import seed_study
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
    for stage in ("appraisal_review", "rooms_review", "assets_review"):
        while (await handle.query(StudyWorkflow.status)).awaiting != stage:
            await asyncio.sleep(0.2)
        print(stage, await handle.execute_update(
            StudyWorkflow.decide,
            Decision(review_id=f"{stage}-demo", stage=stage, revision=1, decision="approve",
                     reviewer_id="runbook", submitted_at=datetime.now(UTC)),
            id=f"decision-{stage}-demo"))
    print(await handle.result())
    await engine.dispose()

asyncio.run(main())
EOF
```

Verification output:

```
started study-13427c10-55da-4b42-9550-2503c70073aa
appraisal_review accepted=True applied_revision=1 reason=None
rooms_review accepted=True applied_revision=1 reason=None
assets_review accepted=True applied_revision=1 reason=None
result outcome='completed' stage='completed' revision=1 export_uri='gs://basis-local/studies/13427c10-.../exports/rev-1.json' ...
```

Without `GCS_BUCKET` the export goes to the API's local storage
(`$TMPDIR/basis-local-storage/basis-local/studies/<id>/exports/rev-1.json`).

## 8. Watching a study in the Temporal UI

Open <http://localhost:8233>, namespace `default`. Each study is workflow
`study-<study_id>` of type `StudyWorkflow`. Useful searches (List filter box, or the CLI):

```bash
temporal workflow list --query "StudyId = '<study uuid>'"
temporal workflow list --query "Stage = 'appraisal_review'"     # studies waiting at a gate
temporal workflow list --query "OrgId = '<org uuid>'"
temporal workflow query --workflow-id study-<id> --type status  # gate, revision, photos, budget
```

In the workflow's page: the Event History shows every activity with its id
(`transition:rooms_review:r1`, `analyze_photo:<document>:r1`, ...), updates appear as
`WorkflowExecutionUpdateAccepted/Completed`, and timers are the 7-day reminders.

## Cleanup

```bash
docker rm -f basis-pg
rm -f ~/.basis-temporal.db
```
