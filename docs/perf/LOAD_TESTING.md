# Load testing Basis

How Basis is load-tested before launch: the tool, what is measured and against which
objectives, how the system is loaded without spending model money, and the commands. The
first capacity report (numbers, the bottleneck, the provider ceilings) is EVAL_REPORT
section 31 (`docs/review_2026-10-15/EVAL_REPORT.md`).

Code: `tooling/load/` (the Locust harness, the sampler, the report, the local stack) and
`backEnd/tests/load/` (the fake-provider worker and its latency profile; it lives next to
`tests/failure/kill_worker.py`, the precedent for a worker with swapped providers, so no
fake ever ships in the production image).

## 1. What is being tested

A study is long-running and mostly waits on models: about 520 model calls, 52 SAM 3
predictions and one Azure DI analysis, roughly 10 minutes and $6.40 on the real providers
(EVAL_REPORT 27.8). The engineer's frontend creates the study, uploads the appraisal and
the photos to signed URLs, starts it, polls the workflow status every 5 s and decides at
three review gates. So there are two different loads, and the test drives both:

| Scenario | Locust user | What it does | What it stresses |
|---|---|---|---|
| Whole studies | `StudyEngineer` | the frontend's calls for one study, end to end: create, 53 x (register + upload + confirm), start, poll, read each gate's screen (fields; rooms and every photo's objects; assets and evidence), approve; then the next study | the worker, Temporal, the provider pool, Postgres writes, the API's mutation path and rate limits |
| Reviewers reading | `ReviewReader` | an engineer with studies open: poll a workflow, open a study, page assets / rooms / fields / evidence, open photos' objects (wait 2-5 s between) | the API's read path and Postgres reads |

Each Locust user is its own engineer (uid `load-eng-<n>`), so the per-caller rate limits
apply as they do in production.

## 2. Tool: Locust

Researched first (k6, Locust, JMeter; Temporal's own `omes`):

- **Locust** (MIT, Python). A user is a Python class, so a stateful multi-step flow (a
  study that waits on polls and branches at gates) is ordinary code, and the harness can
  reuse the backend's own helpers (the upload path naming, `basis.storage`). It reports
  per-request-name percentiles, writes CSVs for CI, and custom events (we report each
  study's milestones as `STUDY` requests so Locust's percentiles cover turnaround too).
- **k6** (AGPL, JavaScript on a Go runtime). More virtual users per load generator
  (tens of thousands) and built-in pass/fail thresholds. Neither matters here: the
  heaviest step is 25 concurrent studies or 200 readers, under 100 requests a
  second, far below one Locust process's capacity; and our team, client logic and
  helpers are Python.
- **JMeter**: GUI-first XML plans; no advantage for API flows written by a Python team.
- **Temporal `omes`** load-tests the Temporal service itself with generic workflows. Our
  question is how our workflow, activities and dependencies behave, so the test drives
  the real API and the real worker instead, and reads Temporal's own metrics (section 3).

Decision: Locust, headless (`--headless --csv`), one process (no distributed mode is
needed at these rates; `run_step.sh` and the report assume one process).

## 3. Loading without spending money: the fake-provider worker

`backEnd/tests/load/fake_worker.py` is the production worker entrypoint with one change,
the composition root: the model providers, Azure DI and SAM 3 are replaced by fakes that
answer like the test fixtures and **take as long as the real ones**. Everything else is
production code: the workflow, every activity, the provider pool and its
`LLM_CONCURRENCY` semaphore (the fake sits behind the real `ProviderPool`), the Postgres
writes and the chunk store, the Temporal client and worker with their default slots, the
health server, the Prometheus metrics, the drain.

- **Latency profile** (`backEnd/tests/load/latency_profile_live_27_8.json`, built by
  `build_profile.py` from the live study's database rows, timings and token counts only):
  every model call of EVAL_REPORT 27.8 (550 calls: 52 + 52 + 52 per-photo, 9 room, 25
  second looks, 359 classifier, 1 extraction fallback) with its latency and tokens; the 52
  SAM 3 `predict_seconds`; Azure DI 72-82 s per appraisal (from the live timelines, Azure
  DI calls are not recorded). A fake call sleeps a latency drawn with replacement from the
  live calls of the same prompt family and model and reports their mean tokens, so the
  recorded cost per study is the real price. `photo_surfaces` now runs on gpt-6-luna
  (cost sweep, section 28) and was measured live only on gpt-6.1-sol: the fake uses the
  sol latencies, an upper bound. SAM 3: live `predict_seconds` + 1.3 s of Replicate
  create-and-poll overhead (section 30: 52 predictions at concurrency 3 in 55-63 s).
- **Answers**: the per-photo calls return a per-room-type component mix
  (`ROOM_TEMPLATES`) chosen so a study makes about as many assets as the live one with a
  similar share sent to the classifier (labels without a rules-table row); the room step
  merges its photos; the residual classifier runs as a tool loop (one search against the
  Postgres chunk store, then an answer citing the first hit, which the activity verifies).
- **Synthetic data only**: the study is `tooling/load/plan.py`: 52 photos in 33 rooms like
  the client package (10 multi-photo rooms + the one unnamed photo), named by the
  package's convention (`17_1st Floor Kitchen photo 4.jpg`) so file-name grouping makes
  the same rooms; every photo is one of the 8 synthetic fixture photos with a unique
  trailer after the JPEG end marker (the API refuses a second document with the same
  sha256 in a study; the decoded image is unchanged); the appraisal is the synthetic URAR.
  The reference corpus is the lifecycle tests' synthetic chunks (`setup_db.py`).
- **Calibration**: one fake study against the live one is EVAL_REPORT 31.2 (call counts,
  assets, cost, stage timings).
- **Provider rate limits** (optional, `LOAD_PROVIDER_LIMITS=<provider_limits.json>`):
  `backEnd/tests/load/rate_limits.py` turns our accounts' limits (read from the providers'
  response headers by `tooling/load/provider_limits.py`) into one token bucket per model
  and limit (RPM / ITPM / OTPM for Claude, RPM / TPM for OpenAI). A spent bucket answers
  as the real provider does: one retry after the Retry-After (capped at 10 s), then a 429
  `ProviderRateLimited`, which the callers handle as in production. Off by default, so a
  step measures our own limits first; on, it shows what the providers' ceilings do to it.
- **The switch**: `fake_worker.py --providers real` (`stack.sh up DIR real`) runs the
  production composition root unchanged (real providers, real spend).

## 4. Time compression

At real speed a 25-study step takes hours, because a study is ten minutes of mostly
waiting. `LOAD_LATENCY_SCALE=s` multiplies every fake sleep by `s`, and the clients'
`--time-scale s` multiplies their poll interval and gate think time by the same `s`, so
the whole timeline shrinks by `s`. Real-equivalent times are measured times divided by
`s`. This is exact for the waiting (the providers) and conservative for the rest: the
system's own work (HTTP, Postgres, Temporal) is not compressed, so it takes a larger
share and the API sees `1/s` times the real request rate. A step that passes compressed
passes at real speed; one that fails compressed is re-run at a larger scale before it is
called a defect. Section 31.2 checks the compression on one study (scale 1.0 vs 0.1).

## 5. What we measure, and the objectives

| Signal | Source | Why |
|---|---|---|
| API latency p50 / p95 / p99 and errors, per route | Locust (`locust_stats.csv`, route templates) | what the engineer feels; 429s counted separately (they are throttling, not faults) |
| Study turnaround: start -> each gate, start -> completed | Locust `STUDY` events, `studies.jsonl` | the product's latency |
| Studies completed per hour | `studies.jsonl` (completed / makespan) | capacity |
| Temporal: activity and workflow task backlog, backlog age | server `DescribeTaskQueue` (report_stats), sampler | work waiting for a worker |
| Temporal: schedule-to-start latency | worker Prometheus `temporal_*_schedule_to_start_latency` | the standard worker-capacity signal (p95 > 1 s means too few workers or slots) |
| Worker slots used / available | `temporal_worker_task_slots_*` | the activity slots (100 by default) |
| Activity execution latency | `temporal_activity_execution_latency` | time spent inside activities, including waiting on the provider pool |
| Worker / API / relay CPU and memory; Postgres container CPU | psutil, `docker stats` | the next hardware limit |
| Postgres connections by state, lock waits, oldest open transaction; slow statements | `pg_stat_activity`, `pg_stat_statements`, `log_min_duration_statement=250` | pool sizing, held transactions, slow queries |
| Cost per study | `llm_calls` + `photo_localizations` rows | spend per study under load (retries cost money) |
| Provider throttling | 429s in the worker log; the providers' rate-limit headers (`provider_limits.py`) | the ceiling we do not control |

Caveat that matters for Basis: the provider pool's semaphore queues model calls **inside**
the worker process. That wait is invisible to Temporal: the activity has a slot and has
started, so schedule-to-start stays near zero and the backlog empty while studies crawl.
Saturation of the pool shows as activity execution latency far above the work's own
latency with slots still free. There is no metric for the pool wait yet (follow-up in
EVAL_REPORT 31.12).

Proposed SLOs (for production; the load steps are judged against them):

| SLO | Objective | Window |
|---|---|---|
| API availability | 99.9% of requests answer non-5xx | 30 days |
| Status poll latency (`GET .../workflow`) | p95 < 200 ms, p99 < 500 ms | at the planned peak |
| Other reads | p95 < 300 ms, p99 < 1 s | at the planned peak |
| Mutations (register, confirm, start, decide) | p95 < 500 ms | at the planned peak |
| Upload of one study by one engineer | completes with no 429 | every study |
| Appraisal gate open after start | p95 < 3 min | at the planned peak |
| Assets gate open after start (the machine's part of a study) | p95 < 20 min | at the planned peak |
| Activity schedule-to-start | p95 < 1 s (Temporal's guidance) | at the planned peak |
| Cost per study | < $8 (the budget guard stops at $25) | per study |

"Planned peak" for launch: 25 studies in flight at once (the largest step), each engineer
also polling. The objectives come from the product (an engineer waits at a gate; a status
poll must feel instant) and from Temporal's guidance for schedule-to-start; they are a
proposal to agree on, not a contract yet.

## 6. Running it

Setup once: `cd backEnd && uv sync --group dev --group load`.

Local (fake providers):

```bash
docker compose -p basis-load -f tooling/load/compose.load.yml up -d
# one step: N concurrent studies at time scale S (both the worker and the clients)
tooling/load/run_step.sh RUN_DIR 10 0.1
# settings under test come from the environment, e.g. the tuned worker
LLM_CONCURRENCY=64 CLASSIFY_CONCURRENCY=16 tooling/load/run_step.sh RUN_DIR 25 0.1
```

`run_step.sh` restarts the host processes with the step's settings (`stack.sh`), starts
the sampler, runs Locust until the target number of studies finished, stops everything
and prints `report.py`'s summary (also `summary.json`, the Locust CSVs, `studies.jsonl`,
`samples.jsonl`, `pg_statements.json`, the process logs, in RUN_DIR).

The read-heavy scenario, after a study step has created studies for `load-eng-0..N`
(start the stack with `RATE_LIMIT_READS_PER_MIN` raised when more readers than engineers
share those identities, as in EVAL_REPORT 31.7; `ReviewReader` polls only started studies):

```bash
cd backEnd && .venv/bin/python -m locust -f ../tooling/load/locustfile.py --headless ReviewReader \
  --host http://127.0.0.1:8161 -u 200 -r 20 -t 5m --reader-pool 25 \
  --firebase-project basis-load --csv RUN_DIR/locust
```

Real-provider probe (the only step that spends; cap it): the provider keys in the
environment, `STUDY_BUDGET_USD` as the hard per-study cap, few photos:

```bash
# OPENAI_API_KEY, ANTHROPIC_API_KEY, REPLICATE_API_TOKEN and AZURE_DOCUMENT_INTELLIGENCE_*
# exported; Azure OpenAI left unset so no call can fall back to another deployment.
LLM_PRIMARY=openai STUDY_BUDGET_USD=4 LLM_CONCURRENCY=64 CLASSIFY_CONCURRENCY=16 \
  LOCUST_ARGS="--photos 20" tooling/load/run_step.sh RUN_DIR 3 1.0 real
```

Before a real-provider step, terminate every open workflow of earlier steps (`temporal
workflow terminate --address 127.0.0.1:7261 --query "ExecutionStatus='Running'"`): the
real worker would otherwise pick up a leftover study on the same task queue and spend on it.

Against staging (no local processes; the stack is Cloud Run + Cloud SQL + Temporal):
`tooling/load/run_remote.sh` (EVAL_REPORT 31.11 has the exact commands). Staging needs real
Firebase ID tokens (`TOKEN_FILE`, a JSON `{uid: token}` minted by
`tooling/load/mint_tokens.py`, which creates the `load-eng-<n>` test users in the
project) because the API refuses the auth emulator outside dev and test, and uploads go
to the GCS signed URLs. The staging worker runs the real providers, so a study step there
costs about $6.40 a study: run the API steps with `--upload-only` (create, upload,
confirm; never start) at any concurrency, and the study steps small and capped.

Provider limits for our accounts (one tiny request per model, headers only):

```bash
backEnd/.venv/bin/python tooling/load/provider_limits.py --out RUN_DIR/provider_limits.json
```
