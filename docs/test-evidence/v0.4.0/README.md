# Test evidence: v0.4.0

Captured 2026-10-15T17:23:41+00:00 on macOS-26.6-arm64-arm-64bit (arm64).
Commit `04328c1c075b0a8dcc2e56c738cec48d7b8d6b14` on `rebuild/stage-3` (v0.3.0-31-g04328c1).

## What was under test

- Python 3.12.15 (pinned 3.12), uv 0.13.1 (Homebrew 2026-10-14 aarch64-apple-darwin), temporal version 1.9.1 (Server 1.32.0, UI 2.54.1), Docker version 29.1.5, build 0e6fee6
- Postgres test image: `pgvector/pgvector:pg16`; Alembic head: `0003_chunks_hnsw`
- Prompt versions: `{"appraisal_field_fallback": ["v1"], "classify": ["v1"], "photo_analysis": ["v1"]}`; rules version: `2026-10-15.1`; embedding model default: `sentence-transformers/all-MiniLM-L6-v2`
- Key packages: alembic 1.20.0, asyncpg 0.32.0, azure-ai-documentintelligence 1.0.2, fastapi 0.143.1, firebase-admin 7.7.0, google-cloud-storage 3.17.0, mypy 2.4.0, openai 3.31.0, pdfplumber 0.11.10, pgvector 0.5.1, pydantic 2.14.1, pymupdf 1.28.2, pytest 9.1.1, rank-bm25 0.2.2, ruff 0.17.0, schemathesis 4.31.1, sentence-transformers 6.1.0, sqlalchemy 2.1.4, temporalio 1.34.0, testcontainers 4.15.0, tiktoken 0.14.0
- `uv.lock` sha256 `ac5be4c935c1009fcc6368cc36bf18c7881b3c8561620b446270b7115706d693`

## Commands

- `collect`: `uv run --no-sync pytest --collect-only -q -p no:cacheprovider`
- `ruff_check`: `uv run --no-sync ruff check basis tests`
- `ruff_format`: `uv run --no-sync ruff format --check basis tests`
- `mypy`: `uv run --no-sync mypy`
- `openapi`: `uv run --no-sync python -m basis.api.openapi --check`
- `pytest`: `uv run --no-sync pytest -q -p no:cacheprovider --timeout=600 -rfEsx --junitxml=/Users/bryanramirez-gonzalez/dev/GitHub/Basis_Info/.claude/worktrees/stage3/docs/test-evidence/v0.4.0/junit.xml`

## Result

- pytest: **2281 passed**, 0 failed, 0 errors, 5 skipped, 0 expected failures (of 2286 collected)
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

## CI run on the pull request

`ci_run.json`, `ci_run.log` and `ci_run.png` are GitHub Actions run 38783894815 on commit
`6798080` of PR #15 (`rebuild/stage-3`, the code of this bundle plus two workflow-only
commits: the tiktoken cache directory resolved from `$HOME` at run time, and the Temporal
CLI installed so the worker-kill and worker-process tests run). All seven jobs green:
changes, secrets-scan, client-data-guard, backend-lint, backend-test (2265 passed, 8
skipped: the Firebase Auth emulator, the GCS bucket, the MiniLM model not cached on the
runner, the history re-recorders), frontend, docker-build (the rebuilt uv image builds and
`import basis` succeeds).

## The runbook run

`runbook/`: the S2 runbook executed end to end on this code (API, outbox relay, worker,
export) with Temporal UI screenshots; see `runbook/README.md`.
