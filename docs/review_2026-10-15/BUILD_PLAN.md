# Basis — Build Plan: production system, full test program, and execution protocol

**Date:** 2026-10-15. Companion to `REVIEW_AND_PLAN.md` (verified state and design decisions). This document is the contract for the build: what gets built, in what order, how every piece is tested, what data is used, how the work is run, and what Bryan has to set up.

**Decisions already made:** application data in PostgreSQL; workflow progress in Temporal; LLM calls as plain structured-output functions; LangGraph removed from the top level (optional later inside one activity); no Redis or Kubernetes until measured; Cloud Run for API and worker; GCS for files; Firebase Auth kept; GitHub Actions CI/CD.

---

## 0. Ground rules that the plan enforces

1. **The repository `bryanrg22/Basis_Info` is public.** Client documents, their derived artifacts (chunks, extractions, labels that quote them), service-account keys, and `.env` values never enter git, CI logs, or CI artifacts. A pre-commit hook and a CI check block known client file hashes and secret patterns.
2. **Every component ships with its tests in the same change.** No PR merges without the tests listed for it in §6 passing.
3. **Every behavior that matters is a test, not a sentence.** Rejection never records approval; cross-study access is impossible; duplicate delivery does not duplicate output; a restart preserves a pending review; a citation must resolve and support its claim.
4. **Real-model and real-provider runs are budgeted and separate** from infrastructure tests. CI never calls Azure, OpenAI, or Replicate; nightly evals do, with a dollar cap.
5. **Tests are versioned with the code.** A test ledger (`tests/LEDGER.md`) lists every test file, what invariant it protects, and which release introduced it. Changing behavior means changing the test in the same PR with a stated reason.
6. **Nothing changes in the `cost-seg-basis` project until the setup checklist in §12 is done.** A separate staging environment is created first; production is touched last.

---

## 1. Target architecture

```
Browser (Next.js, Firebase Auth)
   │  HTTPS, Bearer ID token
   ▼
API  (FastAPI on Cloud Run)  ── verifies token, authorizes, validates, writes Postgres,
   │                              starts / signals Temporal workflows, returns job ids
   ├──► PostgreSQL (Cloud SQL + pgvector): studies, documents, rooms, photos, components,
   │       assets, takeoffs, costs, chunks (+embeddings), citations, reviews, corrections,
   │       runs, llm_usage, eval results
   ├──► GCS: originals (PDF, JPG), derived artifacts (page images, exports)
   └──► Temporal (Cloud or self-hosted): one StudyWorkflow per study
            │
            ▼
Worker (Cloud Run worker pool, fixed instance count, no URL; Temporal Python worker) ── runs activities:
   ingest_appraisal, extract_fields, analyze_photo (×N), enrich, classify_component,
   verify_classification, compute_takeoffs, estimate_costs, build_evidence_pack, export_report
   Each activity: idempotent, keyed by (study_id, input_revision, activity name), writes Postgres.
External: Azure Document Intelligence, Azure OpenAI / OpenAI (vision + text), LangSmith (traces)
```

**StudyWorkflow (Temporal), in words.** Start when documents are confirmed. Run `ingest_appraisal` and `extract_fields`, and fan out `analyze_photo` per photo in parallel with bounded concurrency. Wait for the engineer's `appraisal_decision` signal; on reject with corrections, re-run extraction with the corrections applied and wait again. Wait for `rooms_decision`. Run the asset activities. Wait for `assets_decision`; on reject, apply corrections, invalidate dependents, recompute, wait again. On approve, `export_report` against the approved revision and complete. Every wait has a long timeout (default 30 days) that marks the study `stalled` and alerts rather than silently hanging.

**Revisions.** Each stage's output is written as a numbered revision. A review row references the exact revision it approved. If any input to that revision changes, the approval is invalid and the workflow returns to the wait.

---

## 2. Repository layout after the build

```
backEnd/
  basis/                      # new package; replaces agentic/ when parity is reached
    api/        routes, auth, schemas, errors, middleware (request ids), rate limits
    domain/     pure logic: rules table, takeoff formulas, cost tables, validators, cleaners
    extraction/ azure_di client, regex extractors, section mapper, confidence
    vision/     photo analysis (structured-output VLM call), room/component schemas
    retrieval/  parse, chunk, tokenizer, bm25, embeddings, fusion, store (Postgres), verify
    classify/   rule → cache → residual LLM classifier (bounded tool loop), citation checks
    workflow/   Temporal workflow definition, signals, activities registry
    activities/ one module per activity, idempotent
    db/         SQLAlchemy models, repositories, alembic/
    obs/        logging, tracing, metrics, cost accounting
    config.py   pydantic-settings, explicit environment, no inference from bucket names
  tests/
    unit/ node/ lifecycle/ api/ integration/ evals/ failure/ load/ LEDGER.md
  evals/        dataset loaders (read from BASIS_EVAL_DATA_DIR, never from repo), metrics, runners
  Dockerfile  Dockerfile.worker  pyproject.toml  uv.lock
frontend/      (existing app, API-only data layer, Playwright tests)
infra/         compose.dev.yaml, compose.test.yaml, cloudrun/*.yaml, k6/, scripts/
.github/workflows/  ci.yml, deploy-staging.yml, promote-prod.yml, nightly-evals.yml
```

`agentic/`, `evidence_layer/`, and `vision_layer/` are migrated module by module into `basis/` with their tests, then deleted. Reusable today: PDF parsing, table extraction, chunking, IRS tokenizer, BM25 build, fusion, Azure DI cleaner, static classification rules, takeoff formulas, cost tables, verifier rules.

---

## 3. Data model (PostgreSQL)

Core tables, keys, and the rule each enforces:

| Table | Key columns | Enforced rule |
|---|---|---|
| `users` | id (Firebase uid), email, org_id | one row per Firebase user |
| `orgs`, `org_members` | org_id, user_id, role | study access is by org membership, not just creator |
| `studies` | id, org_id, status, current_stage, workflow_id, created_by | `status` and `current_stage` are written only by the worker/API, never the browser |
| `documents` | id, study_id, kind (appraisal/photo/other), gcs_uri, sha256, bytes, uploaded_by | sha256 unique per study (duplicate upload detected) |
| `document_pages` | document_id, page_no, gcs_uri (rendered image) | |
| `extractions` | id, study_id, document_id, revision, method, payload JSONB, confidence JSONB | unique (document_id, revision) |
| `extraction_fields` | extraction_id, field, value_text, value_num, confidence, source (azure/regex/manual) | typed columns for everything the UI filters on |
| `rooms` | id, study_id, revision, name, type, source | |
| `photo_rooms` | document_id, room_id, revision | a photo maps to one room per revision |
| `components` | id, study_id, revision, document_id, room_id, label, material, attachment, bbox JSONB, raw JSONB | FK to room in the same study (CHECK via trigger or composite FK) |
| `assets` | id, study_id, revision, component_id, bucket, recovery_period, section, method (rule/cache/llm), confidence, needs_review, review_reason | FK component in same study |
| `takeoffs` | asset_id, quantity, unit, formula, inputs JSONB | |
| `cost_estimates` | asset_id, unit_cost, total, source_row, location_factor, year | |
| `corpora`, `corpus_versions` | corpus (reference/study), version, embedding_model, created_at | immutable once published |
| `chunks` | id, corpus_version_id, doc_id, page_start, page_end, section_path, text, embedding vector(384), table_id | pgvector column; `study_id` nullable for study corpus |
| `tables_` | id, corpus_version_id, doc_id, page, headers JSONB, rows JSONB, markdown | never chunked |
| `citations` | asset_id, chunk_id, excerpt, verified_at, verification_method | **FK to chunks**: a citation cannot point nowhere |
| `reviews` | id, study_id, stage, revision, decision (approve/reject), reviewer_id, comment, created_at | unique (study_id, stage, revision); decision is immutable |
| `corrections` | id, study_id, entity_type, entity_id, revision, before JSONB, after JSONB, reason, reviewer_id | append-only |
| `runs` | id, study_id, workflow_id, run_id, workflow_version, input_revision, started_at, ended_at, outcome | |
| `activity_results` | run_id, activity, idempotency_key, status, output_ref | unique idempotency_key |
| `llm_calls` | id, study_id, run_id, activity, model, prompt_version, input_tokens, output_tokens, cost_usd, latency_ms | per-study budget is a SUM over this |
| `classification_cache` | key (component,material,attachment,property_class,rules_version), result JSONB, hits, source | seeded from engineer-approved assets |
| `eval_runs`, `eval_results` | dataset, split, metric, value, model, prompt_version, git_sha | nightly history |

JSONB is used for provider payloads and variable attributes; everything filtered, joined, or summed is a column. Migrations with Alembic; every migration has an upgrade test from empty and from the previous release, and a downgrade test.

---

## 4. Component inventory and what "done" means for each

| # | Component | Done means |
|---|---|---|
| C1 | Config and secrets | explicit `ENV` (dev/test/staging/prod); no inference from env var presence; secrets from Secret Manager in cloud, `.env` locally; config test fails on missing required values |
| C2 | Auth and authorization | Firebase ID token verified with `check_revoked` in prod; every route resolves org membership; ownership tests for every route |
| C3 | API | OpenAPI spec is the contract; idempotency keys on all mutating routes; request ids; rate limits keyed by user; schema-based fuzzing passes |
| C4 | Postgres schema and repositories | tables in §3; constraints tested; migration tests; connection pool sized and tested under load |
| C5 | GCS storage | signed upload URLs; sha256 recorded; object naming by study; lifecycle rule for derived artifacts |
| C6 | Temporal workflow | StudyWorkflow with signals, timers, versioning; replay tests; lifecycle tests with time skipping |
| C7 | Activities | each idempotent and resumable; unit tests with fakes; contract tests against recorded provider responses |
| C8 | Appraisal extraction | Azure DI + regex + cleaner + validation; golden tests on the real appraisal; confidence thresholds drive `needs_review` |
| C9 | Photo analysis | one structured VLM call per photo; schema-validated; room and component outputs; accuracy on labeled photos |
| C10 | Retrieval | parse → chunk → embed → store in Postgres; BM25 in-process with IRS tokenizer; fusion; fetch-by-id; corpus versioning; IR metrics on the query set |
| C11 | Classification | rules → cache → residual LLM with bound tools and caps; citation verification; behavioral evals |
| C12 | Takeoffs and costs | formulas and tables with unit tests against the engineer's report |
| C13 | Review and corrections | approve/reject bound to revisions; corrections invalidate dependents; audit trail |
| C14 | Export | report of the approved revision with evidence; deterministic for the same revision |
| C15 | Frontend | API-only data layer; reject and correction UI; type errors zero; Playwright journeys |
| C16 | Observability | structured logs with ids; OTel traces; metrics; alerts; version in `/health` |
| C17 | Packaging and deploy | uv lock; hardened images; Cloud Run api + worker pool; staging and prod |
| C18 | CI/CD | PR gate, staging deploy, manual promotion, rollback drill, nightly evals |
| C19 | Evaluation program | datasets, metrics, runners, reports with variance, budget |
| C20 | Load and failure program | k6 scenarios, fault injection, soak, recovery, cost per study |
| C21 | Data migration | Firestore export → Postgres import with verification; rollback window |

---

## 5. Evaluation datasets built from the client package

What exists: one complete study. Appraisal (20-page URAR, redacted), 53 photos whose filenames carry the engineer's room labels (`1st Floor Kitchen photo 1`, `2nd Floor Bed 3 Bath`, `Basement Bed 2`, `AC Condenser`, `Electrical Meter`, …), and the engineer's final report (12 pages, ~40 component categories with 5/7/15/39-year buckets and dollar amounts, two summary tables with 126 money cells). The photos are 800×600, 2 MB total.

Datasets to author (stored outside the repo under `BASIS_EVAL_DATA_DIR`, mirrored to a private GCS bucket `basis-eval-private`):

| Dataset | Contents | Size now | How labeled |
|---|---|---|---|
| `appraisal_fields_v1` | expected value per URAR field with tolerance (exact for text, ±1 for integers, ±0.5% for currency) | ~45 fields | read from the PDF by hand, double-checked by Bryan |
| `appraisal_tables_v1` | which tables must survive intact: sales comparison grid, cost approach | 3 tables | page and cell spot-checks |
| `photo_rooms_v1` | photo → room type, floor, "exterior/mechanical/other" | 53 | from filenames, verified visually |
| `photo_components_v1` | photo → visible components with material and attachment where visible, plus `ambiguous` | 53 photos, ~150 components | hand-labeled; ambiguous allowed |
| `assets_v1` | component category → bucket and recovery period, and the dollar line | ~40 rows | from the engineer's report; the 39-year question is confirmed with the engineer |
| `retrieval_qrels_v1` | query → expected chunk/page in the reference corpus | 50 queries | written from real classification questions; two labelers; disagreements resolved |
| `uncertainty_v1` | items that must be flagged `needs_review` | ~20 | blurry/cropped photos, components outside the rules table, conflicting evidence |
| `adversarial_v1` | appraisal pages with injected instructions; photos with text overlays; tool-abuse scripts | ~15 | synthetic, derived from the real documents |
| `synthetic_v1` | malformed PDFs, empty PDFs, 200-page PDF, huge image, zero photos, 300 photos | ~10 | generated; lives in the repo (no client content) |

Splits: `dev` = this package and synthetic variants (used to tune prompts and thresholds); `regression` = every bug reproduced as a case; `held_out` = every new package Bryan obtains (never used for tuning). With one package the held-out score is "not yet measurable" and is reported as such; the plan asks Bryan for 5-10 more completed studies.

---

## 6. The full test inventory

Each line is a test module that will exist. "Scripted model" means a fake provider returning a prescribed sequence; "recorded" means a saved real provider response replayed offline.

### 6.1 Unit (pure, fast, PR gate)
- `unit/domain/test_rules_table.py` — every rule row has section, bucket, life, citation; aliases resolve; unknown label → None.
- `unit/domain/test_takeoff_formulas.py` — carpet SY from SF with waste, cabinet LF, baseboard LF, each against the engineer's report quantities where derivable.
- `unit/domain/test_cost_tables.py` — unit cost × quantity × location factor; year adjustment; missing row → explicit error.
- `unit/domain/test_validators.py` — bucket/section consistency; recovery period by bucket; cross-validation warnings.
- `unit/extraction/test_cleaners.py` — existing 44 cleaner tests, migrated.
- `unit/extraction/test_critical_fields.py` — critical field names match mapped names (the uncommitted regression).
- `unit/extraction/test_section_mapper.py` — Azure DI payload → sections; fallbacks only fill empty values.
- `unit/retrieval/test_tokenizer.py` — `168(e)(3)`, `57.0`, `1245`, hyphenated codes survive.
- `unit/retrieval/test_chunker.py` — ≤400 tokens, 80 overlap, tables excluded, page spans correct.
- `unit/retrieval/test_bm25.py` — exact-code query ranks its chunk first; empty query handled.
- `unit/retrieval/test_fusion.py` — weight 1.0 equals BM25 order; weight 0 equals vector order; RRF reference implementation agrees on ties.
- `unit/retrieval/test_citation_verify.py` — excerpt must be a normalized substring of the chunk; wrong chunk id rejected.
- `unit/vision/test_schemas.py` — VLM output schema validation, unknown room type → `other`, confidence bounds.
- `unit/classify/test_router.py` — rule hit → no LLM; cache hit → no LLM; miss → LLM path; property class affects key.
- `unit/classify/test_tool_loop.py` — scripted model: tool-call cap, iteration cap, invalid JSON, unknown tool, malformed args, infinite tool loop, provider error → `needs_review` with reason.
- `unit/api/test_schemas.py` — request/response models; idempotency key format; study id validation.
- `unit/config/test_settings.py` — required values; no `.env` loading in tests; explicit env.
- `unit/obs/test_logging.py` — request id, study id, job id appear on every log line; secrets never logged.
- `unit/workflow/test_state_rules.py` — which decisions are legal in which stage; revision invalidation rules.

### 6.2 Activity and node tests (fakes, PR gate)
- `node/test_ingest_appraisal.py` — idempotent: run twice → one extraction revision; failure mid-way leaves no partial rows.
- `node/test_extract_fields.py` — recorded Azure DI response → expected fields; low confidence → `needs_review`.
- `node/test_analyze_photo.py` — recorded VLM response → room + components; image download failure → explicit error, not `unknown`.
- `node/test_classify_component.py` — scripted model with search tool results → asset with verified citation; fabricated citation rejected.
- `node/test_compute_takeoffs.py`, `node/test_estimate_costs.py` — deterministic on fixtures.
- `node/test_build_evidence_pack.py` — dedupe, per-stage grouping, every entry resolvable.
- `node/test_export_report.py` — same revision → byte-identical output.

### 6.3 Workflow lifecycle (Temporal test environment with time skipping, PR gate)
- `lifecycle/test_happy_path.py` — start → extraction + photos → wait → approve → wait → approve → assets → wait → approve → export → complete; assert activity sequence and final state.
- `lifecycle/test_reject_paths.py` — reject at each gate with corrections → recompute → wait again; approval never recorded on reject.
- `lifecycle/test_restart_recovery.py` — kill worker between activities and during a wait; new worker resumes; outputs identical; no duplicate rows.
- `lifecycle/test_duplicate_signals.py` — same decision sent twice → one review row; second returns same result.
- `lifecycle/test_concurrent_decisions.py` — approve and reject race → exactly one wins, other rejected with 409.
- `lifecycle/test_stale_approval.py` — approve revision 1 after inputs changed to revision 2 → rejected.
- `lifecycle/test_parallel_photos.py` — one photo activity fails permanently → study continues with that photo flagged; retry policy observed.
- `lifecycle/test_timeouts.py` — review wait exceeds limit → `stalled` + alert, not hang.
- `lifecycle/test_versioning.py` — replay a recorded history against new workflow code with `patched` → no nondeterminism error.
- `lifecycle/test_budget.py` — study exceeds LLM budget → remaining LLM activities skipped, items flagged, workflow completes to review.

### 6.4 API (httpx ASGI, auth faked, PR gate)
- `api/test_auth.py` — missing/expired/revoked token; wrong org; study without org.
- `api/test_studies.py` — create, list (own org only), get, delete; pagination.
- `api/test_uploads.py` — signed URL flow; sha256 duplicate; size and type limits.
- `api/test_decisions.py` — approve/reject per stage; idempotency key; out-of-order stage → 409; non-owner → 403.
- `api/test_corrections.py` — payload validation; audit row; dependent invalidation.
- `api/test_evidence.py` — citations for an asset resolve to chunk text.
- `api/test_rate_limit.py` — per-user key, not IP.
- `api/test_openapi_contract.py` — schema-based fuzzing of every route (Schemathesis) finds no 5xx.
- `api/test_health.py` — version, git sha, dependency checks.

### 6.5 Integration (real Postgres in a container, real GCS emulator or test bucket, PR gate)
- `integration/db/test_migrations.py` — upgrade from empty; upgrade from previous tag; downgrade; autogenerate reports no drift.
- `integration/db/test_constraints.py` — citation to missing chunk fails; asset to component of another study fails; duplicate idempotency key fails; review decision immutable; userId cannot change.
- `integration/db/test_transactions.py` — approve + transition + workflow signal is atomic; failure rolls back.
- `integration/db/test_concurrency.py` — two reviewers on the same revision; lost-update prevention.
- `integration/db/test_pool.py` — pool exhaustion returns 503, not hang; connections released.
- `integration/retrieval/test_store.py` — ingest a fixture corpus into Postgres; vector search returns neighbor; study isolation (`study_id` filter) proven with two studies.
- `integration/retrieval/test_corpus_versions.py` — re-ingest creates v2; v1 citations still resolve; search defaults to latest.
- `integration/retrieval/test_cold_warm.py` — first query loads BM25 once; second query does not reload (timing + call count).
- `integration/storage/test_gcs.py` — upload, sha256, signed URL expiry, delete.
- `integration/providers/test_azure_di_recorded.py`, `test_openai_recorded.py` — client code against recorded responses; error mapping for 400/429/500/timeout.
- `integration/temporal/test_worker_boot.py` — worker registers all activities; task queue names; metrics endpoint up.

### 6.6 Behavioral evals (scripted model, PR gate)
- `evals/behavior/test_prompt_injection_document.py` — injected instructions in appraisal text and in photo overlays produce no change in outputs or tool calls.
- `evals/behavior/test_tool_scope.py` — model requests another study's corpus → tool refuses; trace shows refusal; output `needs_review`.
- `evals/behavior/test_review_bypass.py` — model output cannot set status; only API decisions move stages.
- `evals/behavior/test_missing_evidence.py` — no search → `needs_review`, confidence ≤ 0.3.
- `evals/behavior/test_fabricated_citation.py` — nonexistent chunk id rejected.
- `evals/behavior/test_caps.py` — tool-call cap, iteration cap, token budget.

### 6.7 Quality evals (real models, nightly, budgeted, reported not gated)
- `evals/quality/extraction_fields.py` — per-field accuracy, exact and normalized; confidence calibration; needs_review precision.
- `evals/quality/photo_rooms.py` — accuracy, per-class F1, confusion matrix on 53 photos; repeated 5× for agreement.
- `evals/quality/photo_components.py` — precision/recall of component detection with `ambiguous` handling.
- `evals/quality/retrieval_ir.py` — recall@5/10, MRR, nDCG@10 for BM25, vector, hybrid, and RRF on 50 queries.
- `evals/quality/classification.py` — bucket accuracy vs the engineer's report; citation support rate (verified by fetch); abstention precision.
- `evals/quality/end_to_end.py` — full study on the real package: dollar totals per bucket within tolerance of the report; items needing review count.
- `evals/quality/variance.py` — spread across repeats; any field under 90% agreement listed.
- `evals/quality/cost.py` — tokens and dollars per study per stage.

### 6.8 Frontend (PR gate and post-deploy)
- `tsc --noEmit` with zero errors (26 today); ESLint zero warnings for new code; `next build` without `ignoreBuildErrors`.
- Vitest: data layer (API client, retries, token refresh), reducers, hooks.
- Playwright against local Next + API + Postgres + Temporal dev server + Firebase Auth emulator: sign in; create study; upload; wait for extraction; approve appraisal; reject rooms with correction; approve; assets review with a correction; approve; export; verify no direct Firestore writes (network assertions).
- Accessibility smoke (axe) on each review page.

### 6.9 Failure and chaos (PR gate where fast, nightly where slow)
- `failure/test_worker_kill.py` — SIGKILL during each activity; recovery; no duplicates.
- `failure/test_provider_outage.py` — Azure DI down; OpenAI 429 storm; timeouts; retry policy and eventual `needs_review`.
- `failure/test_db_outage.py` — Postgres unavailable during activity → activity fails, retried, no partial writes; API returns 503.
- `failure/test_temporal_unavailable.py` — API cannot reach Temporal → decision stored, signal retried by an outbox worker; no lost decisions.
- `failure/test_slow_network.py` — Toxiproxy latency on provider calls → timeouts honored.
- `failure/test_disk_full.py`, `test_oom_image.py` — large PDF and images handled with explicit errors.

### 6.10 Load (k6 against staging with mock providers, nightly/weekly)
- `load/smoke.js` — 1 VU, one study end to end.
- `load/average.js` — 10 concurrent studies, 50 photos each, 20 min.
- `load/stress.js` — ramp until p95 or error threshold breaks; record the knee.
- `load/spike.js` — 0 → 25 studies in 10 s.
- `load/soak.js` — 2 h at average load; memory, connections, queue age.
- `load/polling.js` — 200 synthetic users polling status; API p95 and DB load.
- `load/cold_warm.js` — retrieval latency cold vs warm.
- Thresholds: API p95 < 500 ms for reads, < 2 s for decision routes; queue age < 5 min at average load; zero 5xx; worker recovers within 2 min after kill.

### 6.11 Deployment and ops drills (scripted, run per release)
- Container smoke: build from lock, start, `/health` ok, runs as non-root, secrets absent from image.
- Staging deploy + smoke + one synthetic study; promotion; rollback to previous revision; migration expand/contract check.
- Backup restore drill to a scratch instance with row-count check (quarterly).
- Alert drill: force a failed job and a stalled review; alerts fire.
- Security: `pip-audit`/`npm audit` high severity zero; secret scan; Firestore and storage rules reviewed (until Firestore is retired).

---

## 7. CI/CD pipeline

**PR (`ci.yml`)**: path filters for backend/frontend; ruff + mypy; unit + node + lifecycle + api + behavioral evals (scripted); integration with Postgres service container and Temporal dev server; frontend tsc/lint/vitest; Docker build and container smoke; secret scan; dependency audit; coverage report with a floor that only rises. No cloud credentials.

**Merge to main (`deploy-staging.yml`)**: build image tagged with SHA → Artifact Registry; Alembic upgrade on staging; deploy api and worker to staging with `--no-traffic` then shift; smoke; Playwright journey; k6 smoke; budgeted real-provider eval on `dev` split.

**Promote (`promote-prod.yml`, manual, GitHub environment with required reviewer = Bryan)**: same image digest; migration; deploy; smoke; keep previous revision for rollback; post release notes with eval and test summary.

**Nightly (`nightly-evals.yml`)**: quality evals with a dollar cap, variance report, k6 average load on staging, results written to `eval_runs`.

Auth from GitHub to GCP via Workload Identity Federation (no stored keys). Concurrency groups prevent overlapping deploys.

---

## 8. Observability

Structured JSON logs with `request_id`, `study_id`, `run_id`, `activity`, `git_sha`; Cloud Logging trace correlation. OpenTelemetry on FastAPI and the Temporal worker (SDK interceptor) to Cloud Trace; LangSmith for model spans with the same ids as tags. Metrics: activity duration by name, workflow waits, failed activities, queue/schedule-to-start latency, API latency, DB pool usage, LLM cost per study. Alerts: failed workflow, stalled review, schedule-to-start > 5 min, 5xx rate, worker heartbeat missing, budget exceeded. Dashboards: infrastructure health and agent quality (eval scores by prompt version, needs_review rate, correction rate per component type).

---

## 9. Execution protocol (how the work is run)

- **Orchestrator:** this session (Fable) plans tasks, assigns them, verifies by running the tests itself, reviews diffs, and merges. It does not trust a subagent's "done".
- **Builders:** subagents on Opus 5.5 at high effort, one per task, each in its own worktree. One writer per set of files at a time. Every task brief contains: files to touch, the tests that must pass (from §6), the invariant protected, and the forbidden changes.
- **Loop per task:** builder implements + tests → orchestrator runs the suite, reads the diff, runs the reproduction scripts and relevant evals → fix cycle until green → commit with a message naming the tests added → ledger updated.
- **Cadence:** one PR per task; CI must be green; staging deploy after each stage; tag a version at each stage exit (`v0.1.0` … `v0.6.0`) with a changelog listing behaviors and tests.
- **Client data:** read only from `BASIS_EVAL_DATA_DIR`; builders get synthetic fixtures; only the orchestrator runs eval scripts over real files; outputs reported as metrics, never as content.
- **Stops:** the orchestrator comes back to Bryan only for: credentials or accounts it cannot create, an engineer judgment call (for example the 39-year bucket), spending above the agreed budget, or a design change that contradicts this plan.

---

## 10. Stages, order, exit criteria

| Stage | Scope | Exit criteria |
|---|---|---|
| S0 Foundation (3-4 days) | tooling (uv, ruff, mypy, Temporal CLI, k6), `basis/` skeleton, config, logging, CI skeleton, pre-commit, secret rotation, README truth pass, ledger | CI green on an empty-but-wired package; four reproduction tests present as `xfail(strict)` |
| S1 Data layer (1 week) | Postgres schema, Alembic, repositories, GCS storage, Firebase Auth verification, API skeleton with idempotency and request ids | §6.4 and §6.5 db tests green; contract fuzzing green |
| S2 Workflow (1-2 weeks) | Temporal StudyWorkflow, activities as thin shells, signals, timers, versioning, outbox for signals | §6.3 lifecycle green incl. restart, duplicates, stale approval, versioning |
| S3 Pipeline parity (2 weeks) | migrate extraction, vision, retrieval (Postgres + pgvector), classification, takeoffs, costs, evidence, export into activities; delete `agentic/` | §6.1, §6.2, §6.6 green; first quality eval on the real package produces numbers |
| S4 Frontend (1 week) | API-only data layer, reject and correction UI, type errors zero, Playwright journeys | §6.8 green; no Firestore writes from the browser |
| S5 Deploy and CI/CD (1 week) | images, staging project, Cloud Run api + worker pool, Secret Manager, WIF, pipelines, observability, alerts | staging live; promote and rollback drills done; alerts fire in drill |
| S6 Data migration (2-3 days) | Firestore export → Postgres; verification; cutover; rollback window | counts and checksums match; old frontend path removed |
| S7 Quality and load (ongoing) | datasets, nightly evals, k6 suite, failure drills, cost report, Redis decision revisited with numbers | reports published; thresholds in §6.10 met or documented |

---

## 11. Risks and how the plan handles them

- **One client package.** Dev-set tuning will overfit. Mitigation: synthetic variants, held-out reported as "not measurable" until new packages arrive, and the request for 5-10 more studies.
- **Temporal learning curve.** Determinism and versioning rules. Mitigation: replay tests in CI from day one; workflow code kept tiny; all logic in activities.
- **Public repository.** Mitigation: §0 rule 1, pre-commit hash blocklist, CI secret scan, private eval bucket.
- **Provider cost drift.** Mitigation: per-study budget enforced in code; nightly eval cap; cost per study reported.
- **Scope creep into Redis/Kubernetes/LangGraph.** Mitigation: they are listed as post-S7 with a measurement precondition.

---

## 12. Setup checklist for Bryan (only what the orchestrator cannot do alone)

Items the orchestrator will do itself: install tools with Homebrew/uv, create branches and PRs, write workflows, build images, run the Temporal dev server, create local Postgres via Docker, run all tests, create GCS buckets and Cloud SQL instances in a **staging** project once it exists.

Items that need you:

1. **GCP staging project.** Create `cost-seg-basis-staging` (or approve using the existing project with `staging-` prefixed resources) and link billing. Say which.
2. **Enable APIs** in the chosen projects: Cloud SQL Admin, Cloud Run, Artifact Registry, Secret Manager, IAM Credentials, Cloud Trace, Monitoring. The orchestrator can run the `gcloud services enable` commands if you confirm.
3. **Temporal Cloud account.** Sign up at temporal.io (the $150 / 90-day credit applies), create two namespaces in region `aws-us-east-1` (`basis-staging`, `basis-prod`), and generate one API key per namespace for the orchestrator to place in Secret Manager. GCP regions currently require an account exception, so do not pick one. If you prefer not to create the account, say so and the orchestrator self-hosts Temporal on a small VM instead (§13.1).
4. **GitHub.** Allow the orchestrator to create the `staging` and `production` environments with you as required reviewer; enable Dependabot and secret scanning on the repo.
5. **Workload Identity Federation.** Approve creation of a WIF pool and a deploy service account with least privilege; the orchestrator runs the commands and shows them first.
6. **Private eval bucket.** Approve `basis-eval-private` (uniform access, no public ACLs) and the local path for `BASIS_EVAL_DATA_DIR`.
7. **Engineer judgment.** Confirm the 39-year treatment in the report, and review the ~45 expected appraisal field values and the ~40 asset rows once the orchestrator drafts them.
8. **More studies.** Ask the design partners for 5-10 completed studies with photos, appraisal, and final report.
9. **Secrets.** Rotate the LangSmith key found in `.claude/settings.local.json`; confirm the Azure DI and OpenAI keys are allowed for staging use and have spend caps.
10. **Budget.** Agree a monthly cap for provider spend during the build (proposal: $150) and for nightly evals (proposal: $10/night).

---

## 13. Research-backed specifics

### 13.1 Temporal (Python SDK 1.34.0, pinned)

**Workflow design decisions, each tied to an official rule.**
- Workflow id = `study-{study_id}`, started with conflict policy `USE_EXISTING` and reuse policy `REJECT_DUPLICATE`. Temporal guarantees at most one running execution per id, so a double "start" is harmless and a completed study cannot be re-run by accident.
- Engineer decisions are **updates with validators**, not signals. The validator checks stage, reviewer permission, and payload shape before anything is written to history, and the API gets a synchronous accept or reject. Updates need a polling worker, so the API wraps the call in a timeout and, on failure, stores the decision in `reviews` with `delivered=false` and an outbox retries the update. This is the gap `failure/test_temporal_unavailable.py` covers.
- Every wait uses `workflow.wait_condition(..., timeout=timedelta(days=7))`; on timeout, a reminder activity runs and the wait repeats; after 30 days the study is marked `stalled`. Timers are durable and cost nothing while waiting.
- Activities: `start_to_close_timeout` always set; `schedule_to_close_timeout` caps total retries; heartbeats on anything over 30 s (photo batches, ingestion). Retry policy is explicit per activity with `maximum_attempts` (3 for provider calls, 5 for DB), never the default unlimited.
- Idempotency key for every activity = `(workflow_id, run_id, activity_id)` stored in `activity_results`; Postgres writes are upserts on that key; vision calls are keyed by `(study_id, photo_sha256, prompt_version)`.
- Workflow code imports nothing but the SDK and dataclasses. All provider and DB clients live in activities. Payloads carry ids and GCS URIs, never documents (Temporal Cloud caps payloads at 2 MB).
- Versioning: Worker Versioning with auto-upgrade for in-flight studies plus `workflow.patched()` for any change to workflow code; replay tests in CI from day one. The old build-id versioning is being removed from the server in March 2026 and is not used.
- Per-photo fan-out as parallel activities with a semaphore of 5; history stays far under the 51,200-event limit for hundreds of photos.
- Search attribute `StudyId` (Keyword) and `Stage` upserted at each transition so the Temporal UI can find a study.

**Testing.** `WorkflowEnvironment.start_time_skipping()` for lifecycle tests (sleeps and wait timeouts fast-forward; `env.sleep(days=8)` triggers the reminder path). Activities are mocked by registering functions with the same `@activity.defn(name=...)`. `ActivityEnvironment` for single-activity tests with heartbeat capture. `Replayer.replay_workflows()` over recorded histories in CI to catch nondeterminism. The time-skipping server is x64-only; this Mac runs Rosetta, so it works.

**Where Temporal runs.**
- Local and CI: `brew install temporal`, `temporal server start-dev` (gRPC 7233, UI 8233).
- Staging and production, decision: **Temporal Cloud, API-key auth, region `aws-us-east-1`.** Reason: new namespaces in all GCP regions require an account exception as of October 2026; cross-cloud gRPC latency is irrelevant at this workload. Pay-as-you-go has no base fee, $150 of credit for 90 days, and roughly $25-50 per million actions; a study is on the order of a few hundred actions. Fallback if the account cannot be created: self-host `temporalio/server` on one small Compute Engine VM backed by Cloud SQL, kept off the public internet.
- Worker hosting: a **Cloud Run worker pool** (GA since 2026-04-14: no URL, CPU always allocated, fixed instance count, SIGTERM with a 10 s grace period) running a standard long-lived Temporal worker with instance count 1 for staging and 2 for production. Worker pools do not autoscale; Temporal's "serverless workers" integration that scales pools on queue backlog is still public preview and requires worker versioning, so it is a follow-up, not a dependency. The worker heartbeats long activities so a pool restart during maintenance is survivable, and `/health` is exposed for a startup probe because worker pools have no default probe.

### 13.2 Evaluation standards applied

- **Retrieval:** metrics are nDCG@10 (the BEIR standard), recall@5/10, and MRR, computed with `pytrec_eval` on a qrels file. Qrels are labeled at document + page/section span, not chunk id, so chunking changes can be regression-tested against frozen labels. Candidates for labeling are pooled from BM25, dense, and hybrid so the set is not biased toward one retriever; the hole rate is reported. Fusion is compared against reciprocal rank fusion with k = 60. Fifty queries is the floor; paired per-query differences with standard errors decide winners, not point estimates.
- **Citations:** two layers. Deterministic: the chunk id exists in the corpus version and was in the retrieved set, and the excerpt is a normalized substring. Semantic: an entailment judge checks that the passage supports the claim, validated against a human-labeled sample before it is trusted.
- **Extraction:** per-field precision and recall with normalization (whitespace, case, currency, dates) and per-field tolerances chosen from business impact; Azure DI confidence is calibrated on the golden set to pick the review threshold, because Microsoft publishes no number.
- **Vision:** per-class precision/recall/F1, macro-F1, confusion matrix, an explicit `ambiguous` label scored as abstention, and Wilson intervals. Fifty-three photos give roughly ±8 points at 90% accuracy; the plan reports the interval, not just the mean, and asks for more studies.
- **Variance:** every real-model metric is reported with a standard error clustered by document (photos and fields from one property are not independent), and pass^k (all of k repeats succeed) is reported for consistency.
- **LLM-as-judge:** used only where a deterministic check is impossible, with pass/fail prompts, a judge model different from the generator, position and verbosity controls, and a one-time agreement check against human labels.
- **Tooling:** plain pytest with small, owned metric functions, `pytrec_eval` for IR metrics, and optional LangSmith datasets/experiments for repetition reporting with `LANGSMITH_TEST_TRACKING=false` in CI. RAGAS only for its deterministic id-based variants. promptfoo's red-team plugins (`indirect-prompt-injection`, `rag-poisoning`, `excessive-agency`) as a periodic manual pass, not in CI.
- **Hygiene:** dev/regression/held-out splits with one split per example; dataset versions tagged and pinned by CI; private documents never in CI; real-model evals nightly with a budget and reported as intervals.

### 13.3 Infrastructure testing and delivery specifics

- **Database tests** run against a real PostgreSQL started by `testcontainers` using the `pgvector/pgvector:pg16` image per test session, with `CREATE EXTENSION vector` in the fixture; no mocked SQL anywhere. Migration tests: upgrade from empty, upgrade from the previous git tag's schema, head → −1 → head round-trip, and `alembic check` as the drift gate (it exits non-zero when models and migrations disagree; it cannot see renames, so renames get explicit review).
- **Cloud SQL:** Enterprise edition, smallest tier for staging (about 0.6 GB RAM, default `max_connections` 25, so API pool 5 × up to 2 instances + worker pool 5 × 1 instance leaves headroom); automated backups (7-day default retention) and point-in-time recovery on; `CREATE EXTENSION vector` run once as the `postgres` user and `ALTER EXTENSION vector UPDATE` after maintenance; connections through the Cloud SQL Python Connector (`create_async_connector` + asyncpg + SQLAlchemy async). The restore drill clones a point in time to a new instance (`gcloud sql instances clone --point-in-time`) and compares row counts and per-table checksums, then deletes the clone.
- **API contract:** the OpenAPI document is the contract; Schemathesis fuzzes every route in CI and fails on any 5xx or schema violation. Auth in tests uses FastAPI dependency overrides for unit speed and the Firebase Auth emulator for end-to-end runs so real ID tokens are minted and verified.
- **Frontend:** Playwright runs under `firebase emulators:exec --only auth "npx playwright test"` against a local Next server, the API, Postgres in Docker, and the Temporal dev server, started through Playwright's `webServer` array; authenticated state is saved once by a setup project with `storageState({ indexedDB: true })`, which Firebase Auth needs. The API verifies emulator tokens because `FIREBASE_AUTH_EMULATOR_HOST` is set only in test. Network assertions prove no request reaches Firestore. `next build` runs with `ignoreBuildErrors` and `ignoreDuringBuilds` removed, so type and lint errors fail the build.
- **Load:** k6 scenarios use `ramping-vus` for stress, `ramping-arrival-rate` for spike and average load (open model, so slow responses do not hide load), `constant-vus` for soak; thresholds encode the targets in §6.10 so a run fails on its own; tags separate API latency from processing time. Providers are replaced by a mock upstream with configurable latency and error rate. Fault injection uses Toxiproxy between worker and the mock provider and between API and Postgres.
- **Synthetic users:** k6 virtual users sign in through the emulator (staging uses a dedicated test Firebase project or emulator-backed auth), create studies, upload fixtures, poll, and submit decisions; a separate uptime check in Cloud Monitoring hits `/health` from several regions.
- **GitHub → GCP:** Workload Identity Federation via `google-github-actions/auth@v3` with `id-token: write`, an attribute condition pinning `repository_owner`, and no stored keys; images pushed to Artifact Registry; `google-github-actions/deploy-cloudrun@v3` with `no_traffic` then `revision_traffic` after smoke; rollback is `gcloud run services update-traffic --to-revisions PREV=100` (and `--to-latest` afterwards, because a pinned split persists across later deploys); `concurrency` with `queue: max` serializes deploys; GitHub Environments `staging` (automatic) and `production` (required reviewer: Bryan). The repo is public, so environments are available on the free plan. Because a workflow skipped by path filters leaves required checks pending, CI is one workflow whose jobs decide internally what to run.
- **Runtime:** Secret Manager secrets exposed as environment variables with pinned versions (resolved at start; `latest` only for mounted volumes); startup and liveness probes on `/health` configured in Cloud Run, since Cloud Run does not honor Dockerfile `HEALTHCHECK`; JSON logs on stdout with `severity`, `message`, and `logging.googleapis.com/trace` so API and worker lines correlate in Cloud Logging; OpenTelemetry with explicit instrumentation packages (FastAPI instrumentor with `/health` excluded, logging instrumentor, Temporal interceptor) exporting OTLP to Cloud Trace, installed as pinned packages rather than `opentelemetry-bootstrap`, which breaks uv environments; alerting policies on log-based metrics with retest windows for failed workflows, stalled reviews, and 5xx rate; a Cloud Monitoring uptime check on the API from three regions.
- **Packaging:** `uv lock` committed; multi-stage Dockerfile per the uv guide (`uv sync --frozen --no-install-project` on a cached layer, then `uv sync --locked`, `UV_COMPILE_BYTECODE=1`, copy only `.venv`), non-root `USER` with explicit UID, base image pinned by digest with Dependabot keeping the digest fresh, `.dockerignore` excluding data, tests, secrets, and `.venv`; a Docker `HEALTHCHECK` for compose and local runs; CI builds and runs the image before any deploy; Dependabot for `uv`, `npm`, `docker`, and `github-actions`.

## 14. Sources (official or primary, fetched 2026-10-15)

**Temporal**
- Python SDK README and release 1.34.0: https://github.com/temporalio/sdk-python ; PyPI: https://pypi.org/project/temporalio/
- Workflow id uniqueness, reuse and conflict policies: https://docs.temporal.io/workflow-execution/workflowid-runid
- Client usage: https://docs.temporal.io/develop/python/temporal-client
- Signals, queries, updates (validators; update-with-start): https://docs.temporal.io/develop/python/message-passing
- Approval pattern (timeouts, escalation, audit trail): https://docs.temporal.io/design-patterns/approval
- Timers: https://docs.temporal.io/develop/python/timers ; child workflows: https://docs.temporal.io/develop/python/child-workflows ; continue-as-new: https://docs.temporal.io/develop/python/continue-as-new
- Retry policies: https://docs.temporal.io/encyclopedia/retry-policies ; activity timeouts and heartbeats: https://docs.temporal.io/encyclopedia/detecting-activity-failures ; cancellation: https://docs.temporal.io/develop/python/cancellation
- Sandbox and determinism: https://docs.temporal.io/develop/python/python-sdk-sandbox ; idempotent activities: https://docs.temporal.io/activity-definition
- Patching: https://docs.temporal.io/develop/python/versioning ; worker versioning: https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning
- Testing suite (time skipping, activity mocks, replay): https://docs.temporal.io/develop/python/testing-suite ; CLI dev server: https://docs.temporal.io/cli/setup-cli
- Temporal Cloud onboarding: https://docs.temporal.io/cloud/get-started ; regions and the GCP restriction: https://docs.temporal.io/cloud/regions ; pricing: https://temporal.io/pricing ; limits: https://docs.temporal.io/cloud/limits
- Self-hosting: https://docs.temporal.io/self-hosted-guide/deployment ; visibility store: https://docs.temporal.io/self-hosted-guide/visibility
- Cloud Run serverless workers (preview): https://docs.temporal.io/develop/python/workers/serverless-workers/cloud-run
- Observability, search attributes: https://docs.temporal.io/develop/python/observability ; history limits: https://docs.temporal.io/workflow-execution/limits

**Retrieval and LLM evaluation**
- IR metrics (precision@k, recall@k, MAP, nDCG): Manning, Raghavan, Schütze, *Introduction to IR*, ch. 8: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html ; nDCG origin: Järvelin & Kekäläinen 2002, DOI 10.1145/582415.582418
- BEIR benchmark (nDCG@10, pooling bias, BM25 as baseline): https://arxiv.org/abs/2104.08663 ; MS MARCO passage ranking (MRR): https://github.com/microsoft/MSMARCO-Passage-Ranking
- Topic-set size and error: Voorhees & Buckley, SIGIR 2002: https://www.nist.gov/node/707561
- Reciprocal rank fusion, k = 60: Cormack, Clarke, Büttcher, SIGIR 2009: http://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- RAGAS metrics: faithfulness https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/ ; context precision https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/ ; context recall https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/
- LLM-as-judge biases: Zheng et al. 2023 https://arxiv.org/abs/2306.05685 ; OpenAI evaluation best practices: https://developers.openai.com/api/docs/guides/evaluation-best-practices ; Anthropic test development: https://platform.claude.com/docs/en/test-and-evaluate/develop-tests ; Anthropic agent evals: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Citation precision/recall via entailment (ALCE): https://arxiv.org/html/2305.14627v2 ; Azure RAG evaluators (groundedness, document retrieval, holes): https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators
- Error bars and clustered standard errors for evals: Miller 2024 https://arxiv.org/abs/2411.00640
- LangSmith evaluation concepts, repetitions, pytest plugin: https://docs.langchain.com/langsmith/evaluation-concepts ; https://docs.langchain.com/langsmith/repetition ; https://docs.langchain.com/langsmith/pytest ; agentevals trajectory matching: https://github.com/langchain-ai/agentevals
- Document KIE evaluation (DocILE): https://arxiv.org/html/2302.05658v2 ; Azure DI confidence semantics: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/accuracy-confidence
- Calibration: Guo et al. 2017 https://arxiv.org/abs/1706.04599 ; binomial intervals (Wilson): Brown, Cai, DasGupta 2001 https://projecteuclid.org/euclid.ss/1009213286
- OWASP LLM Top 10 (2025): https://genai.owasp.org/llm-top-10/ ; LLM01: https://genai.owasp.org/llmrisk/llm01-prompt-injection/ ; promptfoo red-team plugins: https://www.promptfoo.dev/docs/red-team/plugins/ ; garak: https://github.com/NVIDIA/garak ; DeepEval telemetry: https://deepeval.com/docs/data-privacy

**Infrastructure, CI/CD, load**
- testcontainers-python Postgres: https://testcontainers-python.readthedocs.io/en/latest/modules/postgres/README.html ; pgvector module (image `pgvector/pgvector:pg16`): https://testcontainers.com/modules/pgvector/
- Alembic `alembic check` and autogenerate limits: https://alembic.sqlalchemy.org/en/latest/autogenerate.html ; cookbook (programmatic upgrade, asyncio): https://alembic.sqlalchemy.org/en/latest/cookbook.html
- Cloud SQL extensions (pgvector): https://docs.cloud.google.com/sql/docs/postgres/extensions ; flags and `max_connections` by memory: https://docs.cloud.google.com/sql/docs/postgres/flags ; quotas (100 connections per Cloud Run instance): https://docs.cloud.google.com/sql/docs/postgres/quotas ; Python Connector (async): https://github.com/GoogleCloudPlatform/cloud-sql-python-connector
- Cloud SQL backups and PITR: https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/backups ; https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/configure-pitr ; restore: https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/restoring ; PITR clone: https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/pitr ; dump/restore: https://docs.cloud.google.com/sql/docs/postgres/import-export/import-export-dmp
- Firestore export (`--snapshot-time`): https://docs.cloud.google.com/firestore/docs/manage-data/export-import ; move data guidance: https://docs.cloud.google.com/firestore/docs/manage-data/move-data ; BigQuery load of exports: https://docs.cloud.google.com/bigquery/docs/loading-data-cloud-firestore
- FastAPI testing dependencies and async tests: https://fastapi.tiangolo.com/advanced/testing-dependencies/ ; https://fastapi.tiangolo.com/advanced/async-tests/ ; Schemathesis: https://schemathesis.readthedocs.io/en/stable/tutorials/pytest/ ; https://schemathesis.readthedocs.io/en/stable/guides/cicd/
- Firebase Auth emulator (`FIREBASE_AUTH_EMULATOR_HOST`): https://firebase.google.com/docs/emulator-suite/connect_auth ; Emulator Suite in CI (`emulators:exec`): https://firebase.google.com/docs/emulator-suite/install_and_configure
- Playwright auth and `storageState({ indexedDB: true })`: https://playwright.dev/docs/auth ; https://playwright.dev/docs/api/class-browsercontext ; webServer: https://playwright.dev/docs/test-webserver ; CI: https://playwright.dev/docs/ci-intro
- Next.js Vitest guide: https://nextjs.org/docs/app/guides/testing/vitest ; TypeScript build gate: https://nextjs.org/docs/14/app/api-reference/next-config-js/typescript ; ESLint in build: https://nextjs.org/docs/14/app/building-your-application/configuring/eslint
- k6 executors: https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ ; thresholds: https://grafana.com/docs/k6/latest/using-k6/thresholds/ ; tags: https://grafana.com/docs/k6/latest/using-k6/tags-and-groups/ ; soak: https://grafana.com/docs/k6/latest/testing-guides/test-types/soak-testing/ ; GitHub action: https://github.com/grafana/run-k6-action ; browser module: https://grafana.com/docs/k6/latest/using-k6-browser/options/ ; synthetic monitoring: https://grafana.com/docs/k6/latest/testing-guides/synthetic-monitoring/
- Toxiproxy toxics: https://github.com/Shopify/toxiproxy
- Cloud Monitoring uptime checks: https://docs.cloud.google.com/monitoring/uptime-checks ; synthetic monitors: https://docs.cloud.google.com/monitoring/synthetic-monitors/create
- GitHub → GCP auth (WIF): https://github.com/google-github-actions/auth ; deploy-cloudrun: https://github.com/google-github-actions/deploy-cloudrun ; Artifact Registry auth: https://docs.cloud.google.com/artifact-registry/docs/docker/authentication
- GitHub environments: https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment ; concurrency: https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs ; workflow syntax (path filters leave required checks pending): https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax ; dependency caching: https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching ; Dependabot ecosystems: https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories ; push protection: https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection
- uv in GitHub Actions: https://docs.astral.sh/uv/guides/integration/github/ ; uv in Docker: https://docs.astral.sh/uv/guides/integration/docker/
- Cloud Run rollouts, traffic, rollback: https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration ; worker pools: https://docs.cloud.google.com/run/docs/deploy-worker-pools ; worker pool health checks: https://docs.cloud.google.com/run/docs/configuring/workerpools/healthchecks ; service health checks: https://docs.cloud.google.com/run/docs/configuring/healthchecks ; container contract: https://docs.cloud.google.com/run/docs/container-contract ; secrets: https://docs.cloud.google.com/run/docs/configuring/services/secrets
- Structured logging fields: https://docs.cloud.google.com/logging/docs/structured-logging ; OpenTelemetry for Python on Google Cloud: https://docs.cloud.google.com/trace/docs/setup/python-ot ; OTel zero-code and uv caveat: https://opentelemetry.io/docs/zero-code/python/ ; https://opentelemetry.io/docs/zero-code/python/troubleshooting/ ; FastAPI instrumentor: https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html
- Log-based metrics and alerts: https://docs.cloud.google.com/logging/docs/logs-based-metrics ; https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts ; alerting UI (retest window): https://docs.cloud.google.com/monitoring/alerts/using-alerting-ui
- Docker best practices and Dockerfile reference: https://docs.docker.com/build/building/best-practices/ ; https://docs.docker.com/reference/dockerfile/

**Not confirmable from official sources, handled by our own practice:** a Python pgvector testcontainers example; Alembic downgrade testing; a Firestore→Postgres recipe; mocking upstreams for load tests; Cloud Run-native fault injection; "bot account" terminology (the docs say synthetic monitoring and probes); Temporal worker-versioning GA status; Temporal serverless-worker status on Cloud Run.
