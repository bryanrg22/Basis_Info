# Test evidence: v0.3.0

Captured 2026-10-15T15:51:33+00:00 on macOS-26.6-arm64-arm-64bit (arm64).
Commit `53757b0dfb41f83f1405081c2eff53925512211b` on `rebuild/stage-2` (v0.2.0-31-g53757b0).

## What was under test

- Python 3.12.15 (pinned 3.12), uv 0.13.1 (Homebrew 2026-10-14 aarch64-apple-darwin), temporal version 1.9.1 (Server 1.32.0, UI 2.54.1), Docker version 29.1.5, build 0e6fee6
- Postgres test image: `pgvector/pgvector:pg16`; Alembic head: `0002_outbox_status`
- Prompt versions: `{}`; rules version: `None`; embedding model default: `None`
- Key packages: alembic 1.20.0, asyncpg 0.32.0, azure-ai-documentintelligence 1.0.2, fastapi 0.143.1, firebase-admin 7.7.0, google-cloud-storage 3.17.0, mypy 2.4.0, openai 3.31.0, pdfplumber 0.11.10, pgvector 0.5.1, pydantic 2.14.1, pymupdf 1.28.2, pytest 9.1.1, rank-bm25 0.2.2, ruff 0.17.0, schemathesis 4.31.1, sentence-transformers 6.1.0, sqlalchemy 2.1.4, temporalio 1.34.0, testcontainers 4.15.0, tiktoken 0.14.0
- `uv.lock` sha256 `83f3018b2e0f26db4102892a1ed5f587acc0920639847f6f3522a9732a018657`

## Commands

- `collect`: `uv run --no-sync pytest --collect-only -q -p no:cacheprovider`
- `ruff_check`: `uv run --no-sync ruff check basis tests`
- `ruff_format`: `uv run --no-sync ruff format --check basis tests`
- `mypy`: `uv run --no-sync mypy`
- `openapi`: `uv run --no-sync python -m basis.api.openapi --check`
- `pytest`: `uv run --no-sync pytest -q -p no:cacheprovider --timeout=600 -rfEsx --junitxml=/Users/bryanramirez-gonzalez/dev/GitHub/Basis_Info/docs/test-evidence/v0.3.0/junit.xml`

## Result

- pytest: **1582 passed**, 0 failed, 0 errors, 5 skipped, 2 expected failures (of 1589 collected)
- gate exit codes: collect=0, ruff_check=0, ruff_format=0, mypy=0, openapi=0, pytest=0

### Skipped (with reason)

- `tests.api.test_auth::test_real_verifier_against_the_auth_emulator`: needs the Firebase Auth emulator (FIREBASE_AUTH_EMULATOR_HOST)
- `tests.integration.storage.test_gcs::test_signed_upload_stat_read_delete`: BASIS_GCS_TEST_BUCKET is not set
- `tests.lifecycle.test_versioning::test_record_histories[appraisal_reject]`: set BASIS_RECORD_HISTORIES=1 to re-record
- `tests.lifecycle.test_versioning::test_record_histories[cancelled]`: set BASIS_RECORD_HISTORIES=1 to re-record
- `tests.lifecycle.test_versioning::test_record_histories[happy_path]`: set BASIS_RECORD_HISTORIES=1 to re-record

### Expected failures (with reason)

- `tests.regression.test_legacy_citation_extraction::test_tool_message_as_built_by_run_yields_citation`: W5: ToolMessage from run() has no name and str(list) content; the filter also expects names without the _tool suffix -> 0 citations
- `tests.regression.test_legacy_critical_fields_mapping::test_every_critical_field_is_a_mapping_target`: A.8: 277703c renamed mapping targets (gla_sqft, effective_date_of_appraisal, final_market_value) without updating CRITICAL_FIELDS

### Failed

- none

Files: `manifest.json`, `summary.json`, `tests_collected.txt`, `gates.log`, `junit.xml`.
