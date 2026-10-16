# Test evidence: v0.7.0

Captured 2026-10-16T13:12:30+00:00 on macOS-26.6-arm64-arm-64bit (arm64).
Commit `caba145bd5d14a6408c0c192b8cdb777fb2014ba` on `evidence/v0.7.0` (v0.6.0-3-gcaba145).

## What was under test

- Python 3.12.15 (pinned 3.12), uv 0.13.1 (Homebrew 2026-10-14 aarch64-apple-darwin), temporal version 1.9.1 (Server 1.32.0, UI 2.54.1), Docker version 29.1.5, build 0e6fee6
- Postgres test image: `pgvector/pgvector:pg16`; Alembic head: `0004_photo_objects`
- Prompt versions: `{"appraisal_field_fallback": ["v1"], "classify": ["v1"], "photo_analysis": ["v1", "v2"], "photo_countables": ["v1", "v2"], "photo_countables_joint": ["v1"], "photo_countables_tile": ["v1"], "photo_lighting_electrical": ["v1"], "photo_overlap": ["v1"], "photo_surfaces": ["v1"], "photo_verify_box": ["v1"], "room_inventory": ["v1", "v2", "v3"], "surface_material": ["v1", "v2", "v3"]}`; rules version: `2026-10-15.2`; embedding model default: `sentence-transformers/all-MiniLM-L6-v2`
- Key packages: alembic 1.20.0, asyncpg 0.32.0, azure-ai-documentintelligence 1.0.2, fastapi 0.143.1, firebase-admin 7.7.0, google-cloud-storage 3.17.0, mypy 2.4.0, openai 3.31.0, pdfplumber 0.11.10, pgvector 0.5.1, pydantic 2.14.1, pymupdf 1.28.2, pytest 9.1.1, rank-bm25 0.2.2, ruff 0.17.0, schemathesis 4.31.1, sentence-transformers 6.1.0, sqlalchemy 2.1.4, temporalio 1.34.0, testcontainers 4.15.0, tiktoken 0.14.0
- `uv.lock` sha256 `51a392dddb347af997c4839809dbfab92482b4d9168183c2cf3350ca30ff2450`

## Commands

- `collect`: `uv run --no-sync pytest --collect-only -q -p no:cacheprovider`
- `ruff_check`: `uv run --no-sync ruff check basis tests`
- `ruff_format`: `uv run --no-sync ruff format --check basis tests`
- `mypy`: `uv run --no-sync mypy`
- `openapi`: `uv run --no-sync python -m basis.api.openapi --check`
- `pytest`: `uv run --no-sync pytest -q -p no:cacheprovider --timeout=600 -rfEsx --junitxml=/Users/bryanramirez-gonzalez/dev/GitHub/Basis_Info/.claude/worktrees/localize/docs/test-evidence/v0.7.0/junit.xml`

## Result

- pytest: **2979 passed**, 0 failed, 0 errors, 5 skipped, 0 expected failures (of 2984 collected)
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

`ci_run.json`, `ci_run.log` and `ci_run.png` are GitHub Actions run 38906071207 on commit caba145
(`main`, the merge of PR 31), triggered by the push.

## What this version adds over v0.6.0

PR 31 makes the measured winners of EVAL_REPORT sections 15 to 17 the pipeline's defaults,
each behind a setting so the previous behaviour stays reachable: per-class count fusion
(gpt-6.1-sol at low effort and Claude Opus 5.5 at medium, the class's more accurate source),
the room step (room_inventory/v3 on Claude Sonnet 5.5 at low effort, one-photo rooms passed
through, the material second look), appraisal extraction on gpt-6-luna at low effort and
asset classification on Claude Sonnet 5.5 at low effort; the Material enum gains wallpaper
and painted_masonry. Nothing here was re-measured against live models; the numbers are the
sections' offline evaluations on the client study.
