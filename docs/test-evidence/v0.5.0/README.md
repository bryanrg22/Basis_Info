# Test evidence: v0.5.0

Captured 2026-10-16T08:29:06+00:00 on macOS-26.6-arm64-arm-64bit (arm64).
Commit `6538b14dd7050174465d7ffc6a07edf6f49d06e3` on `evidence/v0.5.0` (v0.4.0-57-g6538b14).

## What was under test

- Python 3.12.15 (pinned 3.12), uv 0.13.1 (Homebrew 2026-10-14 aarch64-apple-darwin), temporal version 1.9.1 (Server 1.32.0, UI 2.54.1), Docker version 29.1.5, build 0e6fee6
- Postgres test image: `pgvector/pgvector:pg16`; Alembic head: `0003_chunks_hnsw`
- Prompt versions: `{"appraisal_field_fallback": ["v1"], "classify": ["v1"], "photo_analysis": ["v1", "v2"], "photo_countables": ["v1", "v2"], "photo_surfaces": ["v1"], "room_inventory": ["v1"]}`; rules version: `2026-10-15.2`; embedding model default: `sentence-transformers/all-MiniLM-L6-v2`
- Key packages: alembic 1.20.0, asyncpg 0.32.0, azure-ai-documentintelligence 1.0.2, fastapi 0.143.1, firebase-admin 7.7.0, google-cloud-storage 3.17.0, mypy 2.4.0, openai 3.31.0, pdfplumber 0.11.10, pgvector 0.5.1, pydantic 2.14.1, pymupdf 1.28.2, pytest 9.1.1, rank-bm25 0.2.2, ruff 0.17.0, schemathesis 4.31.1, sentence-transformers 6.1.0, sqlalchemy 2.1.4, temporalio 1.34.0, testcontainers 4.15.0, tiktoken 0.14.0
- `uv.lock` sha256 `51a392dddb347af997c4839809dbfab92482b4d9168183c2cf3350ca30ff2450`

## Commands

- `collect`: `uv run --no-sync pytest --collect-only -q -p no:cacheprovider`
- `ruff_check`: `uv run --no-sync ruff check basis tests`
- `ruff_format`: `uv run --no-sync ruff format --check basis tests`
- `mypy`: `uv run --no-sync mypy`
- `openapi`: `uv run --no-sync python -m basis.api.openapi --check`
- `pytest`: `uv run --no-sync pytest -q -p no:cacheprovider --timeout=600 -rfEsx --junitxml=/Users/bryanramirez-gonzalez/dev/GitHub/Basis_Info/.claude/worktrees/localize/docs/test-evidence/v0.5.0/junit.xml`

## Result

- pytest: **2631 passed**, 0 failed, 0 errors, 5 skipped, 0 expected failures (of 2636 collected)
- gate exit codes: collect=0, ruff_check=0, ruff_format=0, mypy=0, openapi=0, pytest=0

### Skipped (with reason)

- `tests.api.test_auth::test_real_verifier_against_the_auth_emulator`: needs the Firebase Auth emulator (FIREBASE_AUTH_EMULATOR_HOST)
- `tests.integration.storage.test_gcs::test_signed_upload_stat_read_delete`: BASIS_GCS_TEST_BUCKET is not set
- `tests.lifecycle.test_versioning::test_record_histories[appraisal_reject]`: set BASIS_RECORD_HISTORIES=1 to re-record
- `tests.lifecycle.test_versioning::test_record_histories[cancelled]`: set BASIS_RECORD_HISTORIES=1 to re-record
- `tests.lifecycle.test_versioning::test_record_histories[happy_path]`: set BASIS_RECORD_HISTORIES=1 to re-record

### Expected failures (with reason)

- none

### Failed

- none

Files: `manifest.json`, `summary.json`, `tests_collected.txt`, `gates.log`, `junit.xml`.

## CI run

`ci_run.json`, `ci_run.log` and `ci_run.png` are GitHub Actions run 38872798750 on commit
6538b14 (`main`, the merge of PR 24, the last of PRs 21 to 24), triggered by the push:
all seven jobs green (changes, secrets-scan, client-data-guard, backend-lint,
backend-test, frontend, docker-build), 10 min 5 s. The run page lists ten `frontend`
annotations: TypeScript errors in the legacy Next.js app (`StudyBackButton` props,
`AppraisalResources.ingested`, `Statistics` fields, `lib/types.ts`, `sqliteDb`), reported
as annotations only, as they were on the previous versions; the job's own gate passes.
They belong to the frontend rewiring (Stage 4), not to this version.

## What this version adds over v0.4.0

PRs 21 to 24: task-decomposed per-photo prompts (`photo_countables/v2`, `photo_surfaces/v1`)
and the two-call step behind `VISION_PHOTO_MODE` (default `single`); gpt-6 family request
parameters, prices and `reasoning_effort`; the native Anthropic provider with model-family
routing; the harness tasks `photo_counts` (per-photo counts against the verified boxes)
and `report_rows` (coverage of the engineer's report rows); `predict_rooms` retries;
`merge_photo_runs --room-from`; the vision schema cuts over-long notes instead of failing
a photo; a harness fix (a predicted `other` label no longer counts as a false positive).
EVAL_REPORT sections 10.3 and 11 to 14 hold the measurements. No runbook rerun for this
version: the workflow, API and relay did not change.
