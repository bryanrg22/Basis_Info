# Basis

<img width="997" height="336" alt="Basis Logo" src="https://github.com/user-attachments/assets/aaab8d9c-7238-46d0-a8ea-29ea04a666e5" />

---

> **Cost segregation shouldn't take weeks. Basis gets engineers 80% of the way there: fast, guided, and defensible.**

---

## Contents

* [Overview](#overview)
* [Status (October 2026)](#status-october-2026)
* [Architecture](#architecture)
* [Backend packages](#backend-packages)
* [Workflow lifecycle](#workflow-lifecycle)
* [Retrieval and citations](#retrieval-and-citations)
* [Extraction and vision](#extraction-and-vision)
* [Classification, takeoffs and costs](#classification-takeoffs-and-costs)
* [Evaluation and test evidence](#evaluation-and-test-evidence)
* [Running locally](#running-locally)
* [Testing](#testing)
* [CI](#ci)
* [Security and data handling](#security-and-data-handling)
* [Frontend (legacy, unchanged)](#frontend-legacy-unchanged)
* [Deployment status](#deployment-status)
* [Roadmap](#roadmap)
* [History: the legacy design](#history-the-legacy-design)
* [Other document domains](#other-document-domains)
* [Traction](#traction)
* [Planning documents](#planning-documents)

---

## Overview

Basis is a platform for residential-focused cost segregation firms. It speeds up the slowest part of a study: going through hundreds of photos, sketches and appraisal pages to produce an IRS-ready asset schedule.

**Why cost segregation.** A $1M rental property normally depreciates over 27.5 years. A cost segregation study separates out components that qualify for 5-, 7- or 15-year recovery, so the owner deducts more, earlier. Thousands of these studies are done each year in the U.S.

**The problem.** A study has three steps: document the property, analyze the documentation, write the report. Step 2 is the bottleneck. In our interviews it took engineers 2 to 3 weeks and more than $1,200 of labor per study, and missed or inconsistently documented components could leave more than $1,000 of savings on the table.

**What Basis does.** Engineers upload the files they already use. Basis extracts the appraisal fields, identifies rooms and components in the photos, classifies each component for depreciation with citations to the IRS text, and estimates quantities and costs. An engineer reviews and can correct the output at three gates (appraisal, rooms, assets) before anything is exported. Basis is not a one-click study generator.

**Demo video** (recorded on the legacy system):

[![Basis Demo Video](https://img.youtube.com/vi/ZpUEYUvN5II/hqdefault.jpg)](https://youtu.be/ZpUEYUvN5II)

---

## Status (October 2026)

The backend has been rebuilt against a reviewed plan. [REVIEW_AND_PLAN.md](docs/review_2026-10-15/REVIEW_AND_PLAN.md) records what was verified in the old code and the cloud project; [BUILD_PLAN.md](docs/review_2026-10-15/BUILD_PLAN.md) is the build contract. Stages 0 to 3 are built. The package version is `0.4.0` ([`backEnd/pyproject.toml`](backEnd/pyproject.toml)).

**Built (Stages 0 to 3), all under [`backEnd/basis/`](backEnd/basis/)**
- A FastAPI `/v1` API with Firebase ID-token auth, idempotency keys, rate limits, RFC 9457 errors and signed uploads.
- PostgreSQL as the application database (SQLAlchemy async, Alembic migrations `0001` to `0004`, pgvector for chunk embeddings).
- A Temporal `StudyWorkflow` with three engineer review gates, reject-with-corrections, idempotent activities and a per-study LLM budget. Decisions travel from the API to the workflow through a transactional outbox and a relay process.
- The pipeline that the old LangGraph code only partly ran: appraisal ingestion and field extraction, per-photo vision, retrieval over PostgreSQL, classification with verified citations, takeoffs, costs, an evidence pack and a deterministic export.
- An evaluation harness ([`backEnd/evals/`](backEnd/evals/)) and about 2273 tests collected by pytest.

**Not built yet**
- **Frontend migration (Stage 4).** The Next.js app in [`frontend/`](frontend/) is still the legacy one. It writes to Firestore directly and calls the legacy `/workflow/...` endpoints, which no longer exist. It does not use the new API yet.
- **Read endpoints for pipeline output.** The API has no routes yet that return extraction fields or rooms on their own. Assets are readable through `GET /v1/studies/{id}/evidence` (each asset with its room, photos, localized instances and citations, [`basis/api/routes/evidence.py`](backEnd/basis/api/routes/evidence.py)) and a photo's localized objects through `GET /v1/studies/{id}/photos/{document_id}/objects`; everything is also in PostgreSQL and in the export file.
- **Deployment (Stage 5).** Nothing in this branch is deployed. See [Deployment status](#deployment-status).
- **A real-provider evaluation report.** The harness runs on synthetic fixtures (`--dry-run`) and scores saved predictions. No report from real models on the private client package has been published.
- **Classification cache writes.** `basis.classify.cache.promote_to_cache` exists and is tested, but nothing calls it yet (Stage 4's review flow will). The cache is read but stays empty.
- **Hybrid retrieval in classification.** `basis.retrieval.hybrid.hybrid_search` (BM25 + vector, fused) is built and tested, but the classifier's search tools use BM25 only today ([`basis/classify/tools.py`](backEnd/basis/classify/tools.py)).

**Legacy defects that caused the rebuild, now fixed.** The old backend had four defects, each reproduced as a strict `xfail` test in Stage 0 ([REVIEW_AND_PLAN.md A.3](docs/review_2026-10-15/REVIEW_AND_PLAN.md#a3-workflow-and-agent-correctness-topic-3)):

| Legacy defect | Fixed by | Tests that now pass |
|---|---|---|
| Only 2 of 7 LangGraph nodes were reachable; rooms, assets and completion never ran | Temporal `StudyWorkflow` runs every stage in order | [`tests/lifecycle/test_happy_path.py`](backEnd/tests/lifecycle/test_happy_path.py) |
| Every resume restarted from the first stage | Temporal replays history; activities are idempotent | [`tests/lifecycle/test_restart_recovery.py`](backEnd/tests/lifecycle/test_restart_recovery.py) |
| A rejection at the appraisal checkpoint was recorded as an approval | Decisions are `approve` or `reject`, checked by the API and by the workflow's update validator | [`tests/lifecycle/test_reject_paths.py`](backEnd/tests/lifecycle/test_reject_paths.py), [`test_stale_approval.py`](backEnd/tests/lifecycle/test_stale_approval.py), [`test_concurrent_decisions.py`](backEnd/tests/lifecycle/test_concurrent_decisions.py) |
| LLM classifications never had citations (tool results were dropped) | Tool results are named JSON tool messages; every citation is verified against the stored chunk | [`tests/unit/classify/test_tool_loop.py`](backEnd/tests/unit/classify/test_tool_loop.py), [`test_citation_verify.py`](backEnd/tests/unit/classify/test_citation_verify.py), [`tests/node/test_process_assets.py`](backEnd/tests/node/test_process_assets.py), [`tests/evals/behavior/test_fabricated_citation.py`](backEnd/tests/evals/behavior/test_fabricated_citation.py) |

The `xfail` tests were deleted when their replacements passed. The "regression" section of [`backEnd/tests/LEDGER.md`](backEnd/tests/LEDGER.md) records each replacement.

---

## Architecture

```
  Browser (Next.js 14, Firebase Auth)        [Stage 4: today the frontend still uses Firestore]
     │ Authorization: Bearer <Firebase ID token>
     │                                        signed PUT URL
     ▼                                      ───────────────────►  Google Cloud Storage
  API  (FastAPI, /v1)                                              documents, photos, exports
     │  writes ───────────────────────────────►  PostgreSQL + pgvector
     │  start / status / cancel                  studies, documents, reviews, corrections,
     │                                           outbox, runs, activity_results, extractions,
     │                                           rooms, components, assets, takeoffs, costs,
     │                                           corpus_versions, chunks, citations, llm_calls
     │                     outbox rows                ▲
     │                         │                      │
     │                         ▼                      │
     │                  Outbox relay  (python -m basis.relay)
     │                         │ "decide" updates     │
     ▼                         ▼                      │
  Temporal  (StudyWorkflow, task queue basis-studies) │
     │                                                │
     ▼                                                │
  Worker  (python -m basis.worker): workflow + activities
     ├──► PostgreSQL (rows, idempotency keys, chunk store) ─┘
     ├──► Azure Document Intelligence (prebuilt-layout)
     ├──► OpenAI / Azure OpenAI (primary/secondary provider pool)
     └──► Google Cloud Storage (read uploads, write raw analyses and exports)
```

- **The database is the source of truth for data.** Temporal holds workflow progress only. Payloads carry ids and storage URIs, never document contents ([`basis/workflow/contracts.py`](backEnd/basis/workflow/contracts.py)).
- **The API never sends decisions to Temporal.** It writes the review, the corrections, the stage transition and an outbox row in one transaction and answers 202. The relay delivers the decision later. Decisions keep working while Temporal is down ([`basis/temporal/service.py`](backEnd/basis/temporal/service.py)).
- **Uploads never pass through the API.** The browser PUTs to a signed URL and the API checks size, content type and sha256 afterwards ([`basis/api/routes/documents.py`](backEnd/basis/api/routes/documents.py)).

The target cloud topology is in [BUILD_PLAN §1](docs/review_2026-10-15/BUILD_PLAN.md#1-target-architecture).

---

## Backend packages

Everything lives in one Python 3.12 package, `basis`, managed with `uv` ([`backEnd/pyproject.toml`](backEnd/pyproject.toml), [`backEnd/uv.lock`](backEnd/uv.lock)). Module docstrings explain each design choice in detail; this is a map.

| Package | What it does | Key modules |
|---|---|---|
| [`api/`](backEnd/basis/api/) | FastAPI app under `/v1`. Firebase ID-token auth; studies belong to orgs (`viewer` < `engineer` < `owner`; a study you cannot see is 404). `Idempotency-Key` required on `POST`/`DELETE`, replayed for 24 h, stored in Postgres. Per-user sliding-window rate limits with separate read and mutation budgets. 1 MiB body limit. Every error is RFC 9457 `application/problem+json` with a `request_id`. Started with `uv run uvicorn basis.api.main:app --port 8000`. | `app.py`, `auth.py`, `users.py`, `idempotency.py`, `idempotency_pg.py`, `ratelimit.py`, `errors.py`, `health.py`, `routes/` |
| [`auth/`](backEnd/basis/auth/) | Firebase ID-token verification. Revocation is checked only in `prod`. The auth emulator is refused in `staging` and `prod`. | `firebase.py` |
| [`db/`](backEnd/basis/db/) | SQLAlchemy 2.0 async models over asyncpg, a bounded pool, a 30 s `statement_timeout`, one transaction per request. Alembic migrations: `0001` schema, enums and triggers (reviews and corrections are append-only); `0002` outbox delivery status; `0003` HNSW index on chunk embeddings and immutable published corpus versions. Repositories never commit. | `models.py`, `engine.py`, `session.py`, `repositories/`, `alembic/versions/` |
| [`workflow/`](backEnd/basis/workflow/) | The deterministic Temporal `StudyWorkflow`, its wire contracts, and the timeout and retry policy of every activity. | `study_workflow.py`, `contracts.py`, `activity_options.py` |
| [`activities/`](backEnd/basis/activities/) | One module per activity: `record_run_start`, `transition`, `list_photos`, `ingest_appraisal`, `extract_fields`, `analyze_photo`, `localize_photo`, `reconcile_rooms`, `apply_corrections`, `process_assets`, `export_report`, `notify_review_pending`, `mark_stalled`, `record_run_end`. Dependencies are bound once through `Deps`. | `_support.py` (idempotency runner), `deps.py`, one file per activity |
| [`worker/`](backEnd/basis/worker/) | The worker process (`python -m basis.worker`): registers the workflow and activities, Prometheus metrics on `:9464`, `/health` on `:8080`, optional worker versioning, an 8 s drain on SIGTERM. | `main.py`, `health.py`, `interceptors.py`, `search_attributes.py` |
| [`relay/`](backEnd/basis/relay/) | The outbox relay (`python -m basis.relay`): claims due rows with `FOR UPDATE SKIP LOCKED`, delivers each decision as a Temporal update, retries with backoff, parks rows as `dead` after too many attempts. `/health` on `:8081`. | `delivery.py`, `main.py` |
| [`temporal/`](backEnd/basis/temporal/) | Temporal client construction (API key + TLS for Temporal Cloud, none for the dev server), the names the API and relay use, and the API's narrow `WorkflowService`. | `client.py`, `names.py`, `service.py` |
| [`retrieval/`](backEnd/basis/retrieval/) | PDF parsing, whole tables, chunking, IRS-aware BM25, MiniLM embeddings in pgvector, fusion, citation checks, ingest CLI. See [Retrieval and citations](#retrieval-and-citations). | `parse.py`, `tables.py`, `chunk.py`, `tokenizer.py`, `bm25.py`, `embed.py`, `store.py`, `hybrid.py`, `verify.py`, `ingest.py`, `metrics.py` |
| [`extraction/`](backEnd/basis/extraction/) | Appraisal field extraction: Azure Document Intelligence plus the PDF text layer, cleaners, validation, a single-shot LLM fallback, a review gate. | `pipeline.py`, `azure_di.py`, `regex_fields.py`, `sections.py`, `cleaners.py`, `validation.py`, `fallback.py`, `confidence.py`, `field_mappings.py` |
| [`vision/`](backEnd/basis/vision/) | The per-photo vision step: one structured call (`photo_analysis`) or, with `VISION_PHOTO_MODE=decomposed`, two narrow calls (`photo_countables`, `photo_surfaces`) merged into one analysis; room grouping and the per-room reconciliation call; SAM 3 localization. | `prep.py`, `analyze.py`, `merge.py`, `rooms.py`, `reconcile.py`, `localize.py`, `schemas.py` |
| [`llm/`](backEnd/basis/llm/) | The single model-call path. One `ChatProvider` contract; OpenAI and Azure OpenAI providers in a primary/secondary pool; a price table; a per-study budget; one `llm_calls` row per call; versioned prompts. | `provider.py`, `providers.py`, `pool.py`, `call.py`, `cost.py`, `recorder.py`, `prompts.py` |
| [`prompts/`](backEnd/basis/prompts/) | Prompt files `<name>/<version>.md` (`photo_analysis`, `photo_countables`, `photo_surfaces`, `room_inventory`, `appraisal_field_fallback`, `classify`), each pinned by sha256 in `manifest.json`. An edited prompt fails to load. | `manifest.json` |
| [`domain/`](backEnd/basis/domain/) | Pure functions over tables: the IRS rules table with `(doc_id, page, excerpt)` citations, room guidance, takeoff formulas, RSMeans-style unit costs, classification validators, cross-validation. No I/O, no model calls. | `rules.py`, `rooms.py`, `takeoffs.py`, `costs.py`, `validators.py`, `cross_validation.py` |
| [`classify/`](backEnd/basis/classify/) | Component classification: rules, then the engineer-approved cache, then a bounded residual model loop with study-scoped tools. Citations are verified on every path. | `router.py`, `residual.py`, `tools.py`, `verify.py`, `cache.py`, `schemas.py` |
| [`evidence/`](backEnd/basis/evidence/) | The evidence pack of one revision (every asset with its numbers and cited passages) and its deterministic JSON and CSV export. | `pack.py`, `export.py` |
| [`storage/`](backEnd/basis/storage/) | GCS with V4 signed `PUT` URLs; a filesystem backend with the same `gs://` addressing for tests and local runs. | `gcs.py`, `local.py`, `base.py` |
| [`obs/`](backEnd/basis/obs/) | JSON logs for Cloud Logging with request, study, run and activity context and key-name redaction. | `logging.py` |
| [`config.py`](backEnd/basis/config.py) | Settings from the environment. `BASIS_ENV` (`dev`, `test`, `staging`, `prod`) is required; `.env` is read only in `dev`. | |

The committed OpenAPI document is [`docs/api/openapi.json`](docs/api/openapi.json). A test fails when it is stale; regenerate it with `uv run python -m basis.api.openapi --write`.

---

## Workflow lifecycle

One `StudyWorkflow` per study, with workflow id `study-{study_id}` ([`basis/workflow/study_workflow.py`](backEnd/basis/workflow/study_workflow.py)):

```
record_run_start
transition -> extracting
in parallel:  ingest_appraisal -> extract_fields   |   analyze_photo -> localize_photo x N
                                                       (at most photo_concurrency at once) ; reconcile_rooms
gate appraisal_review    reject: apply_corrections, extract_fields again, revision + 1
gate rooms_review        reject: apply_corrections, re-analyze only the flagged photos, reconcile_rooms
transition -> assets_processing ; process_assets
gate assets_review       reject: apply_corrections, process_assets again
transition -> exporting ; export_report ; transition -> completed ; record_run_end
```

**Decisions.** `POST /v1/studies/{id}/decisions` takes `approve` or `reject` for the stage and revision the study is at. A stale stage or revision is 409. A correction whose type the stage does not accept, or whose target is not in that revision, is 422. One transaction records the review (append-only), the corrections (append-only), the stage move and an outbox row. The relay delivers the decision as the `decide` update (update id `decision-{review_id}`). The workflow's update validator refuses a decision for a gate it is not waiting at, a stale revision, a gate that already has a decision, or a correction type the gate does not accept. Refused updates are not written to history. A redelivered decision is answered "already applied" and changes nothing.

| Gate | Approve moves to | Reject moves to (revision + 1) | Correction types |
|---|---|---|---|
| `appraisal_review` | `rooms_review` | `extracting` | `extraction_field` |
| `rooms_review` | `assets_processing` | `extracting` | `room`, `component`, `photo` |
| `assets_review` | `exporting` | `assets_processing` | `asset` |

**Rejection with corrections.** `apply_corrections` copies the outputs the rejected stage does not recompute into the new revision and applies the corrections to the copies. The rejected revision's rows are never changed, so it stays as history ([`basis/activities/apply_corrections.py`](backEnd/basis/activities/apply_corrections.py)).

**Idempotency.** Every activity claims the key `{workflow_id}:{run_id}:{activity_id}` in `activity_results` and writes its rows and its completion in one transaction. A retried or replayed activity returns the stored output ([`basis/activities/_support.py`](backEnd/basis/activities/_support.py)). Activities that touch only the database retry up to 5 times; activities that call providers retry up to 3 times. `ValidationError` and `BudgetExceededError` never retry ([`basis/workflow/activity_options.py`](backEnd/basis/workflow/activity_options.py)).

**Budget.** Each study has an LLM budget (`STUDY_BUDGET_USD`, default 25.0). Every model call reserves its worst-case cost before it is sent, records an `llm_calls` row, then charges the actual cost ([`basis/llm/call.py`](backEnd/basis/llm/call.py)). A photo the budget cannot cover is flagged and the study continues. `process_assets` checks the worst case for all residual components up front and writes nothing if it does not fit.

**Timers and cancel.** A gate sends a reminder every `review_reminder_days` (default 7) and marks the study `stalled` after `review_deadline_days` (default 30); the workflow keeps waiting. `POST /v1/studies/{id}/cancel` (owner) sends the `cancel_study` signal.

**Starting.** `POST /v1/studies/{id}/start` (engineer) needs a confirmed appraisal upload. It moves the study to `extracting`, commits, then starts the workflow with conflict policy `USE_EXISTING`, so repeats return the same execution.

---

## Retrieval and citations

Retrieval runs entirely on PostgreSQL. There are no pickles, FAISS files or index downloads ([`basis/retrieval/`](backEnd/basis/retrieval/)).

- **Parsing** (`parse.py`): pdfplumber words to lines, paragraphs, headings and list items. Ruled tables are found first and kept out of the text. Repeating headers and footers are dropped. A page without a text layer is retried with PyMuPDF; a page that is only images is reported as scanned (no OCR here).
- **Tables** (`tables.py`): stored whole in `evidence_tables` and fetched by id. Search sees surrogate chunks that cover every row and point at the table.
- **Chunks** (`chunk.py`): tiktoken `cl100k_base`, 400-token windows with 80 tokens of overlap. Chunks never cross a page, so every citation names one page. Each chunk is an exact substring of the parsed page text.
- **BM25** (`bm25.py`, `tokenizer.py`): `rank_bm25` scoring (k1 = 1.5, b = 0.75) over an IRS-aware tokenizer that keeps codes such as `§1245`, `168(e)(3)(B)`, `57.0` and `87-56` whole. It uses the Lucene IDF, which stays positive in a small study corpus. The index is built in process from the chunk rows and cached per corpus version.
- **Embeddings** (`embed.py`, `store.py`): `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, normalized, stored in `chunks.embedding` with an HNSW cosine index. Tests use a deterministic hash embedder, which the settings refuse outside `dev` and `test`.
- **Fusion** (`hybrid.py`): weighted score fusion or reciprocal rank fusion (k = 60), deterministic tie-breaking. Not yet used by the classifier (see [Status](#status-october-2026)).
- **Corpus versions** (`ingest.py`): every ingest creates a new `corpus_versions` row. Once published, a version's chunks and tables cannot change (database triggers from migration `0003`), so a stored chunk id always means the same text. The reference corpus is ingested with the CLI; each study's appraisal becomes a study corpus in the `ingest_appraisal` activity.
- **Citation verification** (`verify.py`, `basis/classify/verify.py`): a citation is a `(chunk_id, excerpt)` pair. It stands only if the chunk belongs to a corpus version the classification may cite, the normalized excerpt (NFKC, case folded, quotes and dashes unified, whitespace collapsed) is a substring of the normalized chunk text, and, on the model path, the chunk was actually returned by a tool during that classification. Excerpts shorter than 12 characters are rejected.

```bash
cd backEnd
uv run python -m basis.retrieval.ingest --corpus reference --pdf PATH --doc-id ID [--pdf PATH2 --doc-id ID2 ...]
uv run python -m basis.retrieval.ingest --list-versions
```

---

## Extraction and vision

**Appraisal extraction** ([`basis/extraction/pipeline.py`](backEnd/basis/extraction/pipeline.py)), one run per appraisal revision:

1. Read the PDF text layer and ruled tables with pdfplumber. Malformed input fails as a non-retryable error.
2. Run Azure Document Intelligence `prebuilt-layout` with key-value pairs when it is configured. An earlier revision's analysis of the same document is reused, not paid for again. Without Azure DI the text layer alone is used and the result says so.
3. Merge: Azure DI first; the caption-based text reader and tables only fill gaps. Disagreement on a critical field is an issue. Every field has one key (`section.field`) in a single catalogue (`field_mappings.py`).
4. Clean each value by type (`cleaners.py`: unparseable numbers are `None`, never `0`; money is `Decimal`) and validate (`validation.py`: types, ranges, cross-field rules).
5. One structured LLM call for critical fields that are missing or failed a rule (`fallback.py`), on `TEXT_MODEL=gpt-6-luna` at `EXTRACTION_REASONING_EFFORT=low` with an 8000-token budget (EVAL_REPORT section 17.1: 0.924 of 75 fields, 12 of 12 critical; `EXTRACTION_FALLBACK_MAX_TOKENS` overrides it, unset it is 8000 on an OpenAI reasoning model and 800 otherwise). A returned value must appear in the page text that was sent, and its confidence is capped at 0.6, so it is always a suggestion for the reviewer.
6. Review gate (`confidence.py`): `needs_review` when a critical field is missing, below the threshold (default 0.90), or has any validation issue.

**Photo analysis** ([`basis/vision/`](backEnd/basis/vision/)): each photo is resized to at most 1024 px on its longest side, its EXIF (including GPS) is stripped, and it is sent in strict JSON-schema calls that return a room type and components from fixed vocabularies. `VISION_PHOTO_MODE` picks the step (`basis/vision/analyze.py`):

- `decomposed` (the default since EVAL_REPORT section 12.1): two calls on the same prepared image, run concurrently through the provider pool, each with one job: `photo_countables/v2` finds and counts the countable objects, `photo_surfaces/v1` names the surfaces and their materials. The answers are merged by `basis/vision/merge.py` (components unioned by label with the largest count, the countables call's material and attachment when both list a label, every `other` kept; room type, confidence, floor and ordinal from the surfaces call), the same rule the evaluation harness scored (EVAL_REPORT section 11: count error against verified boxes 0.36 to 0.29 at the same label-set quality, twice the per-photo cost). Each call has its own `llm_calls` row and the activity's cost is their sum; a component row's `raw.prompt_version` names both prompts. If either call stays unusable the photo is `ambiguous` with `needs_review` naming the call; a provider error or an exhausted budget on either call fails the photo as a whole, never a half result.

- `single`: one call with `photo_analysis/v1` that asks for everything (the pre-12.1 step; the test suite pins it as its baseline in `tests/conftest.py`).

The production configuration is section 12.1's decision: `VISION_MODEL=gpt-6.1-sol` (the default), `VISION_PHOTO_MODE=decomposed`, `VISION_REASONING_EFFORT=low` and an output budget of 6000 tokens per call (`VISION_MAX_TOKENS`; unset, it is 6000 on an OpenAI reasoning model and 1200 otherwise). Claude Opus 5.5 decomposed is the fallback (`VISION_MODEL=claude-opus-5-5`, with `ANTHROPIC_API_KEY`). Explicit older values (`VISION_MODEL=gpt-4o-mini`, `VISION_PHOTO_MODE=single`, `VISION_MAX_TOKENS=1200`) still work as before.

**Per-class count fusion** (EVAL_REPORT section 15, `VISION_COUNT_FUSION=per_class`, the default): the decomposed step sends `photo_countables/v2` a second time, concurrently, to `VISION_FUSION_MODEL` (default `claude-opus-5-5`, at `VISION_FUSION_REASONING_EFFORT=medium`; needs `ANTHROPIC_API_KEY`). Each count class takes its components from the source the versioned table in [`basis/vision/fusion.py`](backEnd/basis/vision/fusion.py) names (`PER_CLASS_SOURCE`, `TABLE_VERSION`): Opus for windows, doors, light fixtures and kitchen cabinets, gpt-6.1-sol for exterior lights and outlets; labels, materials and the room type stay gpt-6.1-sol's. Measured: count MAE 0.124 against 0.180 (leave-one-photo-out), $2.20 per 52 photos against $0.79. The stored analysis keeps both raw answers and, per class, the source, model and both counts; each component row's `raw.fusion` names the table and the classes Opus counted. If the second model stays unavailable after retries (or is unusable, unconfigured, over budget, or slower than 80 s), the photo keeps gpt-6.1-sol's counts and is flagged `count_fusion_fallback:<why>`; it never fails the photo. `VISION_COUNT_FUSION=off` is the two-call step; in `VISION_PHOTO_MODE=single` fusion is inactive (there is no countables call).

In both modes an unusable answer is retried once and then becomes an `ambiguous` analysis with `needs_review`, never a guess. Photos with the same `(room_type, floor, ordinal)` in a revision share one room row.

**Object localization** ([`basis/activities/localize_photo.py`](backEnd/basis/activities/localize_photo.py), [`basis/vision/localize.py`](backEnd/basis/vision/localize.py)): after a photo's analysis succeeds, the workflow runs `localize_photo` for it. SAM 3 (Replicate `vufinder/sam3`, needs `REPLICATE_API_TOKEN`; without it every photo is recorded `skipped`) is prompted once on the full frame with the photo's object labels (not its surfaces) plus the countable set, and every instance is stored in `photo_objects` with its normalized box, run-length mask and score. An instance is `counted` when its score clears its class's threshold (EVAL_REPORT section 10.2: window 0.6, others 0.5); lower-scoring ones down to 0.3 are kept as candidates for the engineer. `photo_localizations` records `done`, `failed` or `skipped` per photo and revision. Rate limits are retried inside the localizer and then by Temporal; a failed localization never fails the photo or the study. The rooms gate waits for every localization. At `rooms_review` an engineer can add an instance with a tap or a box (`POST /v1/studies/{id}/photos/{document_id}/objects`, resolved against the stored candidates) and delete a manual one; a rooms rejection re-localizes only the re-analyzed photos and carries the others' rows (manual ones included) into the new revision.

**Room step** ([`basis/activities/reconcile_rooms.py`](backEnd/basis/activities/reconcile_rooms.py), [`basis/vision/room_step.py`](backEnd/basis/vision/room_step.py); EVAL_REPORT section 16.5): after a revision's photos are analyzed and localized, `reconcile_rooms` builds one inventory per room, so an object seen in two photos is counted once. A one-photo room takes its photo's analysis (`ROOM_SINGLE_PHOTO=per_photo`, no call); a multi-photo room gets one `room_inventory` call (`ROOM_PROMPT_VERSION=v3`) on `ROOM_MODEL=claude-sonnet-5-5` at `ROOM_REASONING_EFFORT=low`; then every wainscoting, wall finish and tile gets a `surface_material/v3` second look on its best photo (`ROOM_MATERIALS=sensitive`) on `ROOM_MATERIALS_MODEL=claude-sonnet-5-5` at `ROOM_MATERIALS_REASONING_EFFORT=low`, a setting of its own so the reconciler can move to a cheaper model while the second look stays on Sonnet (EVAL_REPORT 28.2). The input is each photo's stored component rows, so a reviewer's carried corrections are what is reconciled. The inventory is written as `components` rows without a document (`raw.source="room_inventory"`, with the room's photos, the photos each object was seen in, the step, prompt and model, and the second look); the full record of every call is stored next to the analyses. `process_assets` builds a room's assets from its inventory rows instead of its per-photo rows. Measured: room count MAE 0.72 to 0.43 on gpt-6.1-sol per-photo input, material present on 9 of 9 rows that need one, $0.33 per property. An unusable answer falls back to the heuristic merge (flagged); a room is rebuilt only when its photos change (a rooms rejection rebuilds the rooms of flagged photos and carries the others). If the activity fails, the rooms review is flagged and the assets come from the per-photo rows, as with `ROOM_RECONCILE=false`. The component materials include `wallpaper` and `painted_masonry`, which the second look answers.

**Room-stage options, measured but off** (EVAL_REPORT sections 26.10 and 29; to be confirmed on a second client package, then switched on by setting them): `ROOM_STEP_MODE=room_call` gives every interior room (by its voted type: not exterior, parking or mechanical, [`room_step.room_path`](backEnd/basis/vision/room_step.py)) one `room_photos_inventory/v2` call with all its photos on `ROOM_CALL_MODEL=claude-opus-5-5` at `ROOM_CALL_REASONING_EFFORT=high`, counting from the pixels instead of reconciling per-photo lists; exterior, parking and mechanical rooms keep the path above, and an unusable answer falls back to it. Rows and the stored record say which path each room took (`path`, `room_call_fallback`) and the record its cost. `MATERIALS_SECOND_LOOK=surface_crops` segments each photo's wall, floor and ceiling with Mask2Former ([`basis/vision/surface_segmenter.py`](backEnd/basis/vision/surface_segmenter.py); `SURFACE_SEGMENTER=local` runs the measured Swin-L ADE20K checkpoint in the worker on the CPU, `replicate` a smaller, unmeasured deployment), asks `SURFACE_CROP_MODEL=claude-sonnet-5-5` about each enlarged crop (`surface_crop_material/v1`), and gives the room's wall finish, wainscoting, tile and ceiling finish the crops' majority material ([`surface_crops.apply_crops`](backEnd/basis/vision/surface_crops.py)); where no crop answered, the per-photo material stands, and a segmenter or call failure keeps it with a flag (`surface_crops_flags`) instead of failing the room. Both run under the activity's budget, and a provider account error still stops the study. Measured on the one study: room count MAE 0.27 against 0.36 (replayed through the wired code, section 29), material hit 0.79 against 0.64 (26.3). `python -m evals.replay_rooms` runs the room step exactly as the activity configures it on stored per-photo outputs, so the second-package check is the same command with the settings flipped.

**Model providers** ([`basis/llm/`](backEnd/basis/llm/)): every call goes through `call_model` (reserve, call, record, charge). OpenAI and Azure OpenAI sit behind one `ChatProvider` contract. Each call tries the primary first and moves to the secondary only after a 429 or 5xx. Prices come from one table in `cost.py`; an unknown model is priced at the most expensive row. Tests and behavioral evals use `ScriptedProvider` and make no network calls.

---

## Classification, takeoffs and costs

`process_assets` handles every component of a revision ([`basis/activities/process_assets.py`](backEnd/basis/activities/process_assets.py)):

1. **Classify** ([`basis/classify/router.py`](backEnd/basis/classify/router.py)):
   - **Rules table** (`basis/domain/rules.py`): no model call. The rule's `(doc_id, page, excerpt)` citations are resolved against the reference corpus. Rows whose treatment conflicts with Treas. Reg. §1.48-1(e)(2) carry a `contested` note and go to review.
   - **Approved cache** (`classification_cache`): engineer-approved answers keyed by label, material, attachment, property class and rules version; citations re-verified. Empty until Stage 4 writes to it.
   - **Residual model** (`residual.py`): `CLASSIFY_MODEL=claude-sonnet-5-5` at `CLASSIFY_REASONING_EFFORT=low` (EVAL_REPORT section 17.2: 0.688 bucket accuracy, 96% verified citations), routed to Anthropic by its name. At most 3 tool calls and 4 model calls (one retry on invalid JSON), 60 s for the whole loop. An OpenAI reasoning model can run this tool loop on Chat Completions only at `CLASSIFY_REASONING_EFFORT=none`, and gpt-6.1-sol not at all; the settings refuse those combinations at startup. An `asset_class` longer than 16 characters is clipped, not a loop failure. The tools (`search_reference`, `search_study`, `get_chunk`, `get_table`) are bound to one study's corpus versions; the model can only pass a query or an id, never a study or corpus. The answer schema has no field that can move a study.
   - **Review policy**: an unverified citation, no verified citation on the model path, an answer without a search, a contested rule, a loop failure or a validator warning sets `needs_review`. A model answer without a search or without a verified citation has its confidence capped at 0.3. When the model gives no usable answer, the asset gets the building's recovery period (no acceleration claimed) and confidence 0.
2. **Measure** ([`basis/domain/takeoffs.py`](backEnd/basis/domain/takeoffs.py)): formulas that store their text and inputs so a reviewer can recompute the number. Room area is the room type's typical area until rooms carry a measured area.
3. **Price** ([`basis/domain/costs.py`](backEnd/basis/domain/costs.py)): unit cost x quantity x year factor x location factor, in `Decimal`. The unit costs are placeholder "typical RSMeans" values from the legacy code until the engineer's cost source is loaded. A missing cost row is recorded with no amounts, not as zero.
4. **Cross-validate** ([`basis/domain/cross_validation.py`](backEnd/basis/domain/cross_validation.py)) classification, takeoff and cost per asset.
5. **Write** assets, takeoffs, cost estimates, verified citations and `llm_calls` rows in one transaction.

`export_report` writes the approved revision to `studies/<study_id>/exports/rev-<n>.json` and `rev-<n>.assets.csv`. Both are canonical (sorted keys, fixed row order, no timestamps), so exporting the same revision twice gives byte-identical files ([`basis/evidence/export.py`](backEnd/basis/evidence/export.py)).

---

## Evaluation and test evidence

**Evaluation harness** ([`backEnd/evals/`](backEnd/evals/), [README](backEnd/evals/README.md)). Five datasets: appraisal fields, photo rooms, photo components, assets and retrieval qrels. Metrics are small owned functions with hand-checked tests: field accuracy (exact and normalized, with tolerances), per-class precision/recall/F1 and macro-F1, accuracy with Wilson intervals, abstention scored as abstention, nDCG@10, recall@k and MRR (trec_eval conventions), citation support rate, standard errors clustered by document, and pass^k over repeats.

```bash
cd backEnd
uv run python -m evals.run --dry-run --report /tmp/eval.md                       # synthetic fixtures
BASIS_EVAL_DATA_DIR=/path/to/package uv run python -m evals.run --split dev --repeats 5
uv run python -m evals.run --split dev --predictions out/                        # score saved outputs
```

The real datasets come from a **private** client package under `BASIS_EVAL_DATA_DIR`. It is never in the repository or in CI. Without saved predictions the harness scores deterministic baselines (printed text, file names, the rules table, BM25 over pages). Model-backed systems are scored through `--predictions`.

**Test evidence.** Each captured version gets a folder under [`docs/test-evidence/`](docs/test-evidence/), written by [`tooling/evidence/capture.py`](tooling/evidence/capture.py): `manifest.json` and `README.md` (git sha, toolchain, package versions, prompt and rules versions, the exact commands), `tests_collected.txt`, `gates.log` (ruff, mypy, OpenAPI check, pytest output), `junit.xml`, `summary.json`, and, where captured, CI run metadata and a screenshot. Folders in this tree: [`v0.3.0`](docs/test-evidence/v0.3.0/) (the Stage 2 release: 1582 tests passed, with the CI run's metadata, job logs and a screenshot of all seven jobs green) and the two Stage 2 preliminaries, [`stage2-a474e4d`](docs/test-evidence/stage2-a474e4d/) and [`stage2-prelim`](docs/test-evidence/stage2-prelim/). Each release adds its own folder; `v0.4.0` is the Stage 3 release.

---

## Running locally

Two options. Both need settings in the environment; `BASIS_ENV=dev` also reads `backEnd/.env`. Values you may set: `DATABASE_URL`, `TEMPORAL_ADDRESS`, `GCS_BUCKET` (otherwise files go to a local temp directory), `FIREBASE_PROJECT_ID`, `OPENAI_API_KEY` and/or `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` (+ deployment names) and/or `ANTHROPIC_API_KEY` (Claude models, `claude-*`; `ANTHROPIC_EFFORT` sets their thinking depth, default `low`), `VISION_MODEL`, `VISION_PHOTO_MODE`, `VISION_REASONING_EFFORT`, `VISION_MAX_TOKENS` (the photo step; defaults above), `REPLICATE_API_TOKEN` (SAM 3 localization), `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` + `AZURE_DOCUMENT_INTELLIGENCE_KEY`, `EMBEDDING_MODEL`, `STUDY_BUDGET_USD`. The full list is in [`basis/config.py`](backEnd/basis/config.py); [`backEnd/.env.example`](backEnd/.env.example) lists the names (no values). Without model providers the worker still runs: photo analysis fails as flagged, extraction uses the text layer only, and residual components are written `classifier_unavailable`.

**Option 1: Docker Compose** ([`docker-compose.yml`](docker-compose.yml): Postgres with pgvector, the Temporal dev server, the API and one worker; development only).

```bash
docker compose up --build
docker compose exec api alembic upgrade head
```

The API is on `localhost:8000` and the Temporal UI on `localhost:8233`. The compose file does **not** run the outbox relay; without it, decisions are recorded but never reach the workflow. Run it as in option 2.

**Option 2: uv, the Temporal CLI and Postgres**, one terminal per process ([S2_RUNBOOK.md](docs/review_2026-10-15/S2_RUNBOOK.md) walks through a full study):

```bash
docker run -d --name basis-pg -e POSTGRES_USER=basis -e POSTGRES_PASSWORD=basis \
  -e POSTGRES_DB=basis -p 5544:5432 pgvector/pgvector:pg16
temporal server start-dev --db-filename ~/.basis-temporal.db

cd backEnd
uv sync
export BASIS_ENV=dev
export DATABASE_URL=postgresql://basis:basis@localhost:5544/basis  # pragma: allowlist secret
uv run alembic upgrade head
uv run python -m basis.worker
uv run python -m basis.relay
uv run uvicorn basis.api.main:app --port 8000
```

Load the reference corpus with the ingest CLI ([Retrieval and citations](#retrieval-and-citations)). The image is built from [`backEnd/Dockerfile`](backEnd/Dockerfile) with uv from the lockfile and the embedding model baked into its Hugging Face cache at the pinned revision (the worker loads it before it polls, with no download; EVAL_REPORT section 39); the API is its default command and the worker runs from the same image with `python -m basis.worker`.

---

## Testing

From `backEnd/`. About 2273 tests are collected (`BASIS_ENV=test uv run --no-sync pytest --collect-only -q`).

```bash
uv run pytest -q                          # everything (needs Docker and the Temporal test server)
uv run pytest -q -m "not integration"     # no containers
uv run pytest -q -m integration           # Postgres container (testcontainers), Temporal
uv run pytest -q tests/lifecycle          # workflow lifecycle on the time-skipping server
```

| Suite | Covers |
|---|---|
| `tests/unit/` | Pure logic: config, logging, errors, idempotency, rate limits, storage, workflow validator and contracts, retrieval, extraction, vision, LLM providers, domain tables, classification, evidence, metrics |
| `tests/api/` | The API through httpx with auth faked: auth, studies, uploads, decisions, workflow routes, idempotency, rate limits, the OpenAPI contract |
| `tests/integration/` | Real Postgres (pgvector) in a container: migrations, constraints, concurrency, repositories, outbox, relay, retrieval store and ingest CLI, extraction persistence, worker process |
| `tests/lifecycle/` | The real workflow and activities on Temporal's time-skipping server: happy path, reject paths, stale and duplicate and concurrent decisions, restart recovery, budget, timeouts, cancel, parallel photos, history size, versioning (replay of recorded histories) |
| `tests/node/` | `process_assets` and `export_report` with a scripted model |
| `tests/failure/` | Database outage, Temporal unavailable, worker killed mid-activity |
| `tests/evals/behavior/` | Behavioral evals with a scripted model: missing evidence, fabricated citation, tool scope, review bypass, prompt injection in a document, loop caps |
| `tests/regression/` | Home of the reproduced legacy defects; all have been fixed and replaced (see the ledger) |

Every test file, the invariant it protects and the release that introduced it are listed in [`backEnd/tests/LEDGER.md`](backEnd/tests/LEDGER.md). Unit and node tests use synthetic fixtures under `backEnd/tests/fixtures/synthetic/`, generated by [`tooling/fixtures/`](tooling/fixtures/); no client file is used.

---

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and on pushes to `main`. Every job always runs and skips its own steps when its area did not change, so required checks never stay pending. Actions are pinned to commit SHAs.

- **backend-lint**: `ruff check`, `ruff format --check`, `mypy`.
- **backend-test**: the full pytest suite including integration tests (testcontainers starts the pgvector image; the Temporal SDK starts its test servers), with a JUnit report uploaded. The OpenAPI document is checked by a test in this suite.
- **frontend**: `npm ci`, `npm run lint`, and `tsc --noEmit` (reported, not blocking until Stage 4).
- **secrets-scan**: gitleaks.
- **client-data-guard**: tests and runs [`tooling/check_no_client_data.py`](tooling/check_no_client_data.py) over every tracked file.
- **docker-build**: builds the backend image and imports `basis` in it (non-blocking until Stage 5).

[`.pre-commit-config.yaml`](.pre-commit-config.yaml) runs ruff, detect-secrets, private-key detection and the client-data guard on developer machines. It is not a CI job.

---

## Security and data handling

- **The repository is public.** Client files never enter it or CI. [`tooling/check_no_client_data.py`](tooling/check_no_client_data.py) blocks files by sha256 ([`tooling/blocked_file_hashes.txt`](tooling/blocked_file_hashes.txt), known client evaluation files), by location, by data extension, by size (over 5 MiB) and by credential-like paths. It runs as a pre-commit hook and in CI. gitleaks and detect-secrets scan for secrets.
- **Settings come from the environment.** `BASIS_ENV` is required and never inferred. `.env` is read only in `dev`. `staging` and `prod` refuse to start without required values. Secrets are `SecretStr`, and the database URL is excluded from `repr` ([`basis/config.py`](backEnd/basis/config.py)).
- **Logs redact** any field whose name looks like a key, token, secret, password or authorization header ([`basis/obs/logging.py`](backEnd/basis/obs/logging.py)).
- **Tenancy is enforced in the database.** Every study-scoped row carries `study_id` with a composite foreign key to its parent, so a row cannot reference another study's data. The API answers 404 for studies the user cannot see.
- **The model cannot reach another study.** Classifier tools are bound to one study's corpus versions and refuse ids from any other version. Document text is passed to models as data, and extraction fallback values must appear in that text.
- **Photos lose their metadata** (EXIF, GPS, ICC) before any provider call.
- **Providers.** Model calls go to OpenAI or Azure OpenAI and document analysis to Azure Document Intelligence.

---

## Frontend (legacy, unchanged)

[`frontend/`](frontend/) is a Next.js 14 (App Router) TypeScript app with TailwindCSS and Firebase (Auth with Google sign-in, Firestore, Storage). See [`frontend/README.md`](frontend/README.md). It has not changed in this rebuild:

- **It does not use the new API.** It calls the legacy `/workflow/start`, `/workflow/resume` and `/workflow/{id}/status` endpoints ([`src/api/backend/workflow.api.ts`](frontend/src/api/backend/workflow.api.ts)), which were deleted with the legacy backend.
- **It writes to Firestore directly**, including `workflowStatus`, rooms and assets. Firestore security rules are owner-only on `studies`, so the browser can write any field of its own study ([REVIEW_AND_PLAN A.4](docs/review_2026-10-15/REVIEW_AND_PLAN.md#a4-persistence-queue-and-data-ownership)).
- **The asset stages** on the engineering-takeoff page run on client-side demo models.

**Firestore data model as the frontend uses it.** One `studies` collection; each document is owned by `userId` and holds `propertyName`, `status`, `workflowStatus`, `currentStep`, `visitedSteps`, `uploadedFiles`, `appraisalResources`, `rooms`, `takeoffs`, `assets`, `photoAnnotations`, `resourceChecklist` and `engineeringTakeoffState` ([`src/types/study.types.ts`](frontend/src/types/study.types.ts)). Files are in Firebase Storage. `workflowStatus` moves through:

```
uploading_documents → analyzing_rooms → resource_extraction → reviewing_rooms → engineering_takeoff → completed
```

Stage 4 moves the frontend to the API (no Firestore writes from the browser, a reject and correction UI, zero type errors, Next.js 15). Stage 6 migrates existing Firestore data into PostgreSQL.

---

## Deployment status

Facts verified on 2026-10-15 ([REVIEW_AND_PLAN.md A.7](docs/review_2026-10-15/REVIEW_AND_PLAN.md#a7-build-and-deploy-configuration-verified), [INFRA_NOTES.md](docs/review_2026-10-15/INFRA_NOTES.md)):

- **Backend: not deployed.** No Cloud Run service or job exists in the `cost-seg-basis` project.
- **Frontend:** the Firebase App Hosting backend `basisbackend` is connected to a different, older repository, so it does not serve this code.
- **`docker-compose.yml` is for development only.**
- **No deploy pipeline yet.** CI builds and tests; there is no deploy workflow or infrastructure-as-code. Stage 5 adds them.

---

## Roadmap

From [BUILD_PLAN §10](docs/review_2026-10-15/BUILD_PLAN.md#10-stages-order-exit-criteria):

| Stage | Scope |
|---|---|
| S0 to S3 (done) | Tooling and CI; PostgreSQL, auth and API; Temporal workflow and outbox; pipeline parity and deletion of the legacy packages |
| S4 Frontend | API-only data layer, reject and correction UI, zero type errors, Playwright journeys, Next.js 15 |
| S5 Deploy | Cloud Run API and worker, Cloud SQL, Temporal Cloud or self-hosted, Secret Manager, Workload Identity Federation, staging, alerts, promote and rollback drills |
| S6 Data migration | Firestore export into PostgreSQL, verification, cutover, rollback window |
| S7 Quality and load | Real-data evals, nightly runs, k6 load tests, failure drills, cost report |

**Deliberately deferred until measured:** Redis, Kubernetes, and LangGraph at the top level. BUILD_PLAN lists them after S7 with a measurement precondition. LangGraph may come back later inside a single activity if a step needs it.

---

## History: the legacy design

Until Stage 3 the backend was three packages, all deleted on this branch:

- `backEnd/agentic/`: a LangGraph workflow with nine "agents", a Firestore checkpointer, a Firestore job queue and worker, and an MCP server that nothing launched.
- `backEnd/evidence_layer/`: PDF parsing, pickled BM25 and FAISS indexes, regex and Azure DI extraction.
- `backEnd/vision_layer/`: a Grounding DINO + SAM2 detection pipeline that was never wired in.

The review found the four defects in the [Status](#status-october-2026) table: an unreachable graph, resumes that restarted, rejections recorded as approvals, and citations that were always empty. Those, plus state split between Firestore fields and an unused checkpointer, are why the workflow moved to Temporal and the data to PostgreSQL ([REVIEW_AND_PLAN B.1](docs/review_2026-10-15/REVIEW_AND_PLAN.md#b1-does-langgraph-belong-here-topic-2)). The useful parts were migrated module by module into `basis/`, and each module docstring says what was kept and what changed. The legacy design diagrams remain for reference: [architecture](docs/architecture.svg), [LangGraph workflow](docs/technique_stategraph.svg), [retrieval agent](docs/technique_rag.svg), [appraisal agent loop](docs/agent_loop.svg). They do not describe the current code.

---

## Other document domains

The retrieval design targets documents with "standardized headers, messy context": exact codes and tables inside narrative text. Cost segregation uses IRS publications. The same pattern fits, for example, ISO 26262 functional-safety documents (HARA baselines, safety goals, TSRs, FMEA tables):

| Basis today | Safety-document equivalent |
|---|---|
| Tokenizer keeps `§1245`, `168(e)(3)`, `57.0`, `87-56` whole | Add patterns for `HAZ-001`, `TSR-042`, `ASIL-D`, `ISO 26262-6:2018 §7.4.3` |
| Tables stored whole, surrogate chunks cover every row | FMEA tables, DFA and traceability matrices stay intact |
| Chunks never cross a page; citations name document and page | Every claim traces to a clause on a page |
| Citations verified by chunk id + excerpt substring | "No evidence, no claim" for safety arguments |
| Engineer review gates | Engineer sign-off before a safety decision is final |

What would change: tokenizer patterns, the reference corpus, the prompts and the rules table. Parsing, chunking, storage, BM25, embeddings and citation verification carry over.

---

## Traction

Basis has been used in paid pilots on the legacy system:

- **CSSI** (top-5 cost segregation firm), paying user
- **CBIZ**, paying user
- Design partners at multiple top-5 firms reported **50%+ time savings** on analysis.

**LavaLab Fall 2025: Best Traction**

![Basis Team Holding Check](https://github.com/user-attachments/assets/a48693f1-f7cb-4832-a8ca-f7ed817b2f7f)

---

## Planning documents

- [REVIEW_AND_PLAN.md](docs/review_2026-10-15/REVIEW_AND_PLAN.md): verified review of the legacy system and the decision record.
- [BUILD_PLAN.md](docs/review_2026-10-15/BUILD_PLAN.md): build contract, test inventory, stages.
- Stage specs: [S1_SPEC.md](docs/review_2026-10-15/S1_SPEC.md), [S2_SPEC.md](docs/review_2026-10-15/S2_SPEC.md), [S3_SPEC.md](docs/review_2026-10-15/S3_SPEC.md).
- [S2_RUNBOOK.md](docs/review_2026-10-15/S2_RUNBOOK.md): run a study locally end to end.
- [INFRA_NOTES.md](docs/review_2026-10-15/INFRA_NOTES.md): what exists in the cloud project.
- [docs/api/openapi.json](docs/api/openapi.json): the API contract.
- [docs/api/WORKFLOW.md](docs/api/WORKFLOW.md): the workflow route, a failed stage (`failed_stage`, `failure`) and the retry.
- [backEnd/tests/LEDGER.md](backEnd/tests/LEDGER.md): every test file and the invariant it protects.
