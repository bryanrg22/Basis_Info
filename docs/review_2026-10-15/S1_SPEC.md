# Stage 1 specification: data layer, auth, API skeleton

Companion to BUILD_PLAN §3, §4 (C1-C5), §6.4, §6.5, §10 (S1). This is the contract the Stage 1 builders implement and the orchestrator verifies. PostgreSQL 16 with the `vector` extension; SQLAlchemy 2 (async, asyncpg); Alembic; FastAPI.

## 1. Conventions

- Primary keys: `id UUID` default `gen_random_uuid()`; externally visible ids are UUIDs, never sequential.
- Every table has `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`; mutable tables also `updated_at` maintained by a trigger.
- Tenancy: every study-scoped table carries `study_id` and a composite foreign key `(study_id, <parent>_id)` to its parent so a row cannot reference a parent in another study.
- Revisions: stage outputs are grouped by `revision INT` per study; a review references `(study_id, stage, revision)`.
- Money as `NUMERIC(14,2)`; quantities as `NUMERIC(14,4)`; confidences as `REAL` with `CHECK (x BETWEEN 0 AND 1)`.
- Enums are PostgreSQL enums created in migrations: `study_status`, `stage`, `document_kind`, `decision`, `classification_method`, `bucket`.
- JSONB only for provider payloads (`raw`), extraction audit, bounding boxes, and free-form inputs; never for anything filtered, joined, or summed.
- Naming: snake_case, plural table names, `ix_<table>_<cols>` indexes, `uq_` unique constraints, `fk_` foreign keys, `ck_` checks. Alembic migrations are hand-reviewed; `alembic check` must report no drift.

## 2. Tables (Stage 1 creates all of them; later stages fill them)

### Identity and tenancy
- `orgs(id, name TEXT NOT NULL, created_at)`
- `users(id, firebase_uid TEXT UNIQUE NOT NULL, email TEXT, display_name TEXT, created_at, last_seen_at)`
- `org_members(org_id FK orgs, user_id FK users, role TEXT CHECK (role IN ('owner','engineer','viewer')), PRIMARY KEY (org_id, user_id))`

### Studies and documents
- `studies(id, org_id FK orgs NOT NULL, created_by FK users NOT NULL, property_name TEXT NOT NULL, status study_status NOT NULL DEFAULT 'draft', current_stage stage NOT NULL DEFAULT 'uploading', workflow_id TEXT UNIQUE, created_at, updated_at)`; index `(org_id, created_at DESC)`.
  - `study_status`: `draft, processing, waiting_review, stalled, completed, archived`.
  - `stage`: `uploading, extracting, appraisal_review, rooms_review, assets_processing, assets_review, exporting, completed`.
- `documents(id, study_id FK, kind document_kind NOT NULL, filename TEXT NOT NULL, content_type TEXT NOT NULL, bytes BIGINT NOT NULL CHECK (bytes > 0), sha256 CHAR(64) NOT NULL, gcs_uri TEXT NOT NULL UNIQUE, uploaded_by FK users, uploaded_at TIMESTAMPTZ, UNIQUE (study_id, sha256))`; `document_kind`: `appraisal, photo, other`.
- `document_pages(document_id FK, page_no INT, gcs_uri TEXT, PRIMARY KEY (document_id, page_no))`

### Extraction (filled in S3)
- `extractions(id, study_id FK, document_id FK, revision INT NOT NULL, method TEXT NOT NULL, overall_confidence REAL, needs_review BOOL NOT NULL DEFAULT false, raw JSONB NOT NULL, audit JSONB, created_at, UNIQUE (document_id, revision))`
- `extraction_fields(id, extraction_id FK, study_id, section TEXT NOT NULL, field TEXT NOT NULL, value_text TEXT, value_num NUMERIC, value_date DATE, confidence REAL, source TEXT NOT NULL, page INT, UNIQUE (extraction_id, section, field))`

### Rooms, photos, components, assets (filled in S3)
- `rooms(id, study_id FK, revision INT NOT NULL, name TEXT NOT NULL, room_type TEXT NOT NULL, floor TEXT, source TEXT NOT NULL, created_at)`
- `photo_rooms(study_id, document_id, room_id, revision INT, PRIMARY KEY (document_id, revision), FOREIGN KEY (study_id, room_id) REFERENCES rooms (study_id, id))` — requires `UNIQUE (study_id, id)` on `rooms`.
- `components(id, study_id FK, revision INT NOT NULL, document_id FK, room_id, label TEXT NOT NULL, material TEXT, attachment TEXT, confidence REAL, bbox JSONB, raw JSONB, FOREIGN KEY (study_id, room_id) REFERENCES rooms (study_id, id))`
- `assets(id, study_id FK, revision INT NOT NULL, component_id, category TEXT NOT NULL, bucket bucket NOT NULL, recovery_period NUMERIC(4,1) NOT NULL, irs_section TEXT, method classification_method NOT NULL, confidence REAL, needs_review BOOL NOT NULL DEFAULT false, review_reason TEXT, verified BOOL NOT NULL DEFAULT false, FOREIGN KEY (study_id, component_id) REFERENCES components (study_id, id))`
  - `bucket`: `y5, y7, y15, y27_5, y39, land`; `classification_method`: `rule, cache, llm, manual`.
- `takeoffs(id, study_id, asset_id, quantity NUMERIC(14,4) NOT NULL, unit TEXT NOT NULL, formula TEXT, inputs JSONB, FOREIGN KEY (study_id, asset_id) REFERENCES assets (study_id, id), UNIQUE (asset_id))`
- `cost_estimates(id, study_id, asset_id, unit_cost NUMERIC(14,2), total NUMERIC(14,2), source_row TEXT, location_factor NUMERIC(6,3), cost_year INT, FOREIGN KEY (study_id, asset_id) REFERENCES assets (study_id, id), UNIQUE (asset_id))`

### Evidence (filled in S3)
- `corpus_versions(id, corpus TEXT NOT NULL CHECK (corpus IN ('reference','study')), study_id FK NULL, version INT NOT NULL, embedding_model TEXT NOT NULL, published_at TIMESTAMPTZ, UNIQUE (corpus, study_id, version))`
- `chunks(id, corpus_version_id FK NOT NULL, doc_id TEXT NOT NULL, chunk_key TEXT NOT NULL, page_start INT, page_end INT, section_path TEXT, text TEXT NOT NULL, embedding vector(384), table_id UUID NULL, UNIQUE (corpus_version_id, chunk_key))`; index on `(corpus_version_id, doc_id)`; HNSW index on `embedding` deferred to S3.
- `evidence_tables(id, corpus_version_id FK, doc_id TEXT, page INT, headers JSONB, rows JSONB, markdown TEXT, UNIQUE (corpus_version_id, doc_id, page, id))`
- `citations(id, study_id, asset_id, chunk_id FK chunks NOT NULL, excerpt TEXT NOT NULL, verified_at TIMESTAMPTZ, verification_method TEXT, FOREIGN KEY (study_id, asset_id) REFERENCES assets (study_id, id))`

### Review, corrections, runs, accounting
- `reviews(id, study_id FK, stage stage NOT NULL, revision INT NOT NULL, decision decision NOT NULL, reviewer_id FK users NOT NULL, comment TEXT, idempotency_key TEXT NOT NULL, created_at, UNIQUE (study_id, stage, revision), UNIQUE (idempotency_key))`; `decision`: `approve, reject`. Rows are immutable: a trigger raises on UPDATE/DELETE.
- `corrections(id, study_id FK, entity_type TEXT NOT NULL, entity_id UUID NOT NULL, revision INT NOT NULL, before JSONB, after JSONB NOT NULL, reason TEXT, reviewer_id FK users NOT NULL, created_at)`; append-only (same trigger).
- `runs(id, study_id FK, workflow_id TEXT NOT NULL, run_id TEXT NOT NULL, workflow_version TEXT NOT NULL, input_revision INT NOT NULL, started_at, ended_at, outcome TEXT, UNIQUE (workflow_id, run_id))`
- `activity_results(id, run_id FK runs, activity TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, output_ref TEXT, error TEXT, created_at, updated_at)`
- `llm_calls(id, study_id FK, run_id FK NULL, activity TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT, input_tokens INT NOT NULL, output_tokens INT NOT NULL, cost_usd NUMERIC(10,6) NOT NULL, latency_ms INT, created_at)`; index `(study_id)`.
- `classification_cache(key TEXT PRIMARY KEY, result JSONB NOT NULL, hits INT NOT NULL DEFAULT 0, source TEXT NOT NULL, rules_version TEXT NOT NULL, created_at, updated_at)`
- `idempotency_keys(key TEXT PRIMARY KEY, user_id FK users, route TEXT NOT NULL, request_hash CHAR(64) NOT NULL, status_code INT, response JSONB, created_at, expires_at TIMESTAMPTZ NOT NULL)`
- `outbox(id, kind TEXT NOT NULL, payload JSONB NOT NULL, attempts INT NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(), delivered_at TIMESTAMPTZ, last_error TEXT, created_at)`; index `(delivered_at, next_attempt_at)`.
- `eval_runs(id, dataset TEXT, split TEXT, git_sha TEXT, model TEXT, prompt_version TEXT, created_at)`, `eval_results(id, eval_run_id FK, metric TEXT, value DOUBLE PRECISION, n INT, stderr DOUBLE PRECISION, details JSONB)`

## 3. Repository layer (`basis/db/`)

- `engine.py`: `create_engine(settings)` (asyncpg; Cloud SQL connector when `settings.cloud_sql_instance` is set, else `database_url`), pool size from settings (default 5, max overflow 0), `pool_pre_ping`, statement timeout 30 s.
- `session.py`: `get_session()` FastAPI dependency; one transaction per request; commit on success, rollback on exception.
- `models.py`: SQLAlchemy 2 declarative models mirroring §2, with `Mapped[...]` annotations; pgvector `Vector(384)`.
- `repositories/`: `StudyRepository`, `DocumentRepository`, `ReviewRepository`, `UserRepository`, `IdempotencyRepository`, `OutboxRepository` with explicit methods (no generic CRUD): e.g. `StudyRepository.create(org_id, created_by, property_name)`, `.get_for_user(study_id, user_id)` (joins org_members), `.list_for_user(user_id, limit, cursor)`, `.transition(study_id, from_stage, to_stage, expected_revision)` using `UPDATE ... WHERE current_stage = :from` and raising `StaleStateError` when 0 rows updated.
- `alembic/`: async env; `versions/0001_initial.py` creating every table, enum, trigger, and the `vector` extension.

## 4. Auth and authorization (`basis/api/auth.py`)

- `get_current_user()` dependency: `Authorization: Bearer <Firebase ID token>`; verified with `firebase_admin.auth.verify_id_token(token, check_revoked=settings.env == "prod")`; when `FIREBASE_AUTH_EMULATOR_HOST` is set the Admin SDK accepts emulator tokens (tests use this path, with the variable set only in `test`). Upserts `users` by `firebase_uid` on first sight and updates `last_seen_at`.
- Authorization helper `require_study_access(study_id, user, role>=viewer|engineer)`: membership in the study's org; 404 (not 403) when the study is not visible to the user, 403 when visible but role insufficient.
- A user with no org gets a personal org created on first login (`owner`).
- Error responses are RFC 9457 problem details (`application/problem+json`) with `type`, `title`, `status`, `detail`, `instance`, `request_id`; the same shape for validation errors.

## 5. API routes (Stage 1 scope; Temporal integration arrives in S2)

| Method, path | Purpose | Notes |
|---|---|---|
| `POST /v1/studies` | create | body `{property_name}`; 201 with study |
| `GET /v1/studies` | list for user | cursor pagination `limit<=100` |
| `GET /v1/studies/{id}` | get | includes documents and latest review per stage |
| `DELETE /v1/studies/{id}` | archive (soft) | owner only |
| `POST /v1/studies/{id}/documents` | request upload | body `{filename, content_type, bytes, sha256, kind}`; returns a signed GCS PUT URL and a document id with `uploaded_at = NULL` |
| `POST /v1/studies/{id}/documents/{doc_id}/complete` | confirm upload | verifies the object exists and its size and sha256; sets `uploaded_at`; duplicate sha256 → 409 |
| `POST /v1/studies/{id}/decisions` | approve/reject | body `{stage, revision, decision, comment, corrections[]}`; header `Idempotency-Key` required; records `reviews`, applies `transition`, writes an `outbox` row of kind `workflow_signal`; 202 with the review; wrong stage or stale revision → 409; duplicate key with same body → same 202; duplicate key with different body → 422 |
| `GET /v1/studies/{id}/evidence?asset_id=` | citations | returns `[]` in S1 |
| `GET /health` | liveness + dependency checks | `db: ok/fail` with latency; 503 when the DB check fails |

- Idempotency: middleware for `POST`/`DELETE` requiring `Idempotency-Key` (UUID or ≤128 chars); stores request hash and response in `idempotency_keys` with 24 h expiry; replays the stored response for a matching hash; 422 on hash mismatch; keys are scoped per user.
- Rate limits: per user id (fallback IP for unauthenticated), 60/min reads, 10/min mutations, in-memory for now with a `RateLimiter` interface so Postgres-backed can replace it.
- Request size limit 1 MiB for JSON; uploads never pass through the API.

## 6. Storage (`basis/storage/gcs.py`)

- `Storage` protocol with `GcsStorage` and `LocalStorage` (tmp dir, used in tests): `signed_put_url(uri, content_type, bytes, expires)`, `stat(uri) -> (bytes, md5/crc32c)`, `read(uri)`, `delete(uri)`.
- Object naming: `studies/{study_id}/{document_id}/{filename}`; bucket from `settings.gcs_bucket`.
- Tests use `LocalStorage`; one integration test against a real bucket is marked `integration` and skipped unless `BASIS_GCS_TEST_BUCKET` is set.

## 7. Tests to deliver (file names are binding)

- `tests/integration/db/conftest.py`: session-scoped `postgres` fixture via `testcontainers` image `pgvector/pgvector:pg16`, `CREATE EXTENSION vector`, Alembic upgrade to head; function-scoped `session` fixture with rollback.
- `tests/integration/db/test_migrations.py`: upgrade from empty; `alembic check` reports no drift; downgrade one and upgrade again; upgrade applies cleanly twice (idempotent at head).
- `tests/integration/db/test_constraints.py`: every cross-study FK is rejected (component→room, asset→component, takeoff→asset, citation→asset); citation to missing chunk rejected; duplicate `(document_id, revision)` and `(study_id, sha256)` rejected; review UPDATE/DELETE raise; `idempotency_keys` duplicate rejected; enum values rejected when invalid.
- `tests/integration/db/test_transactions.py`: decision recording is atomic: review + transition + outbox in one transaction; a forced failure after the review insert leaves no rows.
- `tests/integration/db/test_concurrency.py`: two concurrent `transition` calls from the same stage → exactly one succeeds; two reviewers approving the same revision → one review row.
- `tests/integration/db/test_pool.py`: with pool size 2, a third concurrent long query waits and times out with a clear error, not a hang; connections are returned after exceptions.
- `tests/api/test_auth.py`, `test_studies.py`, `test_uploads.py`, `test_decisions.py`, `test_idempotency.py`, `test_rate_limit.py`, `test_errors.py`, `test_health_db.py`: per BUILD_PLAN §6.4, against the real Postgres container and `LocalStorage`, with Firebase verification replaced by a dependency override that returns a test principal (plus one test that exercises the real emulator path when `FIREBASE_AUTH_EMULATOR_HOST` is set, skipped otherwise).
- `tests/api/test_openapi_contract.py`: Schemathesis over the generated OpenAPI spec with an auth header, all routes, no 5xx and no schema violations.

## 8. Exit criteria (orchestrator verifies)

`uv run pytest -q` green including `-m integration` (Docker available); `alembic check` clean; ruff/mypy clean; LEDGER rows for every new test file; OpenAPI spec committed at `docs/api/openapi.json` and regenerated by a test that fails if stale.
