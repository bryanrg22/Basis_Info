# S2 runbook: outbox relay and workflow API

Section file for the Stage 2 runbook (`S2_RUNBOOK.md`, written with the worker). It
covers the outbox relay (`python -m basis.relay`) and the API's workflow routes. All
commands run from `backEnd/`. Verified on 2026-10-15 against a fresh
`pgvector/pgvector:pg16` container and `temporal server start-dev` (CLI 1.9.1, server
1.32.0).

## 1. Local dependencies

```bash
temporal server start-dev                       # gRPC :7233, UI http://localhost:8233
docker run -d --name basis-pg -e POSTGRES_PASSWORD=pw -p 127.0.0.1:5432:5432 \
  pgvector/pgvector:pg16
export DATABASE_URL='postgresql://postgres:pw@127.0.0.1:5432/postgres'  # pragma: allowlist secret
BASIS_ENV=dev uv run alembic upgrade head       # 0001 then 0002_outbox_status
BASIS_ENV=dev uv run alembic check              # "No new upgrade operations detected."
```

## 2. Settings the relay and the workflow routes read

| Variable | Default | Used by |
|---|---|---|
| `DATABASE_URL` | none (required) | API, relay |
| `TEMPORAL_ADDRESS` | `localhost:7233` | API, relay. Set it to the empty string to run the API without Temporal: the workflow routes then answer 503 and `/health` has no `temporal` check |
| `TEMPORAL_NAMESPACE` | `default` | API, relay |
| `TEMPORAL_API_KEY` | unset | API, relay. Temporal Cloud: API key and TLS |
| `TEMPORAL_TASK_QUEUE` | `basis-studies` | API (`start`) |
| `RELAY_POLL_SECONDS` | `2.0` | relay |
| `STUDY_BUDGET_USD`, `PHOTO_CONCURRENCY`, `REVIEW_REMINDER_DAYS`, `REVIEW_DEADLINE_DAYS` | `25.0`, `5`, `7`, `30` | API (`start` puts them in `StudyWorkflowInput`) |
| `GIT_SHA` | `unknown` | API (`StudyWorkflowInput.workflow_version`) |

Both processes create their Temporal client lazily: they start, and keep serving, while
Temporal is down.

## 3. Run the relay

```bash
BASIS_ENV=dev uv run python -m basis.relay                    # loop; /health on :8081
BASIS_ENV=dev uv run python -m basis.relay --health-port 9081  # another health port
BASIS_ENV=dev uv run python -m basis.relay --once              # one batch, then exit
curl -s localhost:8081/health                                  # {"status": "ok"}
```

* Every `RELAY_POLL_SECONDS` it claims up to 20 due `pending` rows
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
* SIGTERM/SIGINT: polling stops, a batch in flight gets 8 s to finish (Cloud Run grants
  10 s) and is otherwise rolled back (its rows are simply due again), the process exits 0.
* Logs are JSON on stdout with `outbox_id`, `study_id`, `kind`, `attempts`, `error`.

## 4. Inspect and repair the outbox

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

## 5. Run the API and drive a study

```bash
BASIS_ENV=dev uv run uvicorn basis.api.main:app --port 8000
curl -s localhost:8000/health
# {"status":"ok",...,"checks":{"db":{"status":"ok",...},"temporal":{"status":"ok",...}}}
```

A Temporal failure shows as `checks.temporal.status = "fail"` with status 200 (verified by
stopping the dev server): decisions keep working through the outbox, so a Temporal outage
must not take the API out of the load balancer. A database failure is still 503.

Every `/v1` call needs `Authorization: Bearer <Firebase ID token>`; every POST needs an
`Idempotency-Key` (a UUID). With `T` a token and `S` a study id:

```bash
H=(-H "Authorization: Bearer $T" -H "Content-Type: application/json")
# needs one appraisal document whose upload is confirmed (POST .../documents, then .../complete)
curl -s -X POST "${H[@]}" -H "Idempotency-Key: $(uuidgen)" localhost:8000/v1/studies/$S/start
# 202 {"workflow_id":"study-<S>","run_id":"..."}; the same 202 on every repeat
curl -s "${H[@]}" localhost:8000/v1/studies/$S/workflow
# {"workflow_id","run_id","execution_status":"RUNNING","status":{stage, revision, ...},"runs":[...]}
curl -s -X POST "${H[@]}" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"stage":"appraisal_review","revision":1,"decision":"approve"}' \
  localhost:8000/v1/studies/$S/decisions        # 202; the relay delivers it
curl -s -X POST "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{"reason":"withdrawn"}' \
  localhost:8000/v1/studies/$S/cancel           # owner only; 202
```

| Route | Role | Answers |
|---|---|---|
| `POST /v1/studies/{id}/start` | engineer | 202; 409 `appraisal-required` (no confirmed appraisal), `study-archived`, `stale-state` (past `uploading` without a workflow), `workflow-missing` (past `extracting` but Temporal has no record: investigate, it is never restarted automatically); 503 when Temporal is unreachable, with the study already `extracting`: repeat the request (any key) and it converges |
| `GET /v1/studies/{id}/workflow` | viewer | 200 (`status` is null when no worker answers the query); 404 before `start`; 503 |
| `POST /v1/studies/{id}/cancel` | owner | 202; 404 before `start`; 409 `workflow-closed`; 503 |

## 6. Tests

```bash
uv run pytest -q tests/integration/relay tests/integration/api tests/failure \
  tests/api/test_workflow_routes.py tests/unit/relay tests/unit/temporal
```

The Temporal-backed tests start their own dev server with
`WorkflowEnvironment.start_local()` (the `temporal` CLI on `PATH` when installed,
otherwise the SDK downloads it) and run the test workflow
`tests/fake_study_workflow.py`.
