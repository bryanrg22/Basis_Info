# Test evidence: v0.10.0

Captured 2026-10-17T14:00:18+00:00 on macOS-26.6-arm64-arm-64bit (arm64).
Commit `a8987ece8dd6274dccf0a56beae358aa38c98152` on `evidence/v0.10.0` (v0.9.0-23-ga8987ec).

## What was under test

- Python 3.12.15 (pinned 3.12), uv 0.13.1 (Homebrew 2026-10-14 aarch64-apple-darwin), temporal version 1.9.1 (Server 1.32.0, UI 2.54.1), Docker version 29.1.5, build 0e6fee6
- Postgres test image: `pgvector/pgvector:pg16`; Alembic head: `0006_study_failed`
- Prompt versions: `{"appraisal_field_fallback": ["v1"], "classify": ["v1"], "house_inventory": ["v1", "v2"], "house_schedule": ["v1", "v2"], "photo_analysis": ["v1", "v2"], "photo_countables": ["v1", "v2", "v3"], "photo_countables_joint": ["v1"], "photo_countables_tile": ["v1"], "photo_lighting_electrical": ["v1"], "photo_marks": ["v1"], "photo_overlap": ["v1"], "photo_points": ["v1"], "photo_surfaces": ["v1", "v2"], "photo_verify_box": ["v1"], "room_inventory": ["v1", "v2", "v3"], "room_photos_inventory": ["v1", "v2"], "surface_crop_material": ["v1"], "surface_material": ["v1", "v2", "v3"]}`; rules version: `2026-10-16.1`; embedding model default: `sentence-transformers/all-MiniLM-L6-v2`
- Key packages: alembic 1.20.0, asyncpg 0.32.0, azure-ai-documentintelligence 1.0.2, fastapi 0.143.1, firebase-admin 7.7.0, google-cloud-storage 3.17.0, mypy 2.4.0, openai 3.31.0, pdfplumber 0.11.10, pgvector 0.5.1, pydantic 2.14.1, pymupdf 1.28.2, pytest 9.1.1, rank-bm25 0.2.2, ruff 0.17.0, schemathesis 4.31.1, sentence-transformers 6.1.0, sqlalchemy 2.1.4, temporalio 1.34.0, testcontainers 4.15.0, tiktoken 0.14.0
- `uv.lock` sha256 `1526ef7bf56ecdd789cd90730049af3a2d28fca3f0b08742709a641faab90fa2`

## Commands

- `collect`: `uv run --no-sync pytest --collect-only -q -p no:cacheprovider`
- `ruff_check`: `uv run --no-sync ruff check basis tests`
- `ruff_format`: `uv run --no-sync ruff format --check basis tests`
- `mypy`: `uv run --no-sync mypy`
- `openapi`: `uv run --no-sync python -m basis.api.openapi --check`
- `pytest`: `uv run --no-sync pytest -q -p no:cacheprovider --timeout=600 -rfEsx --junitxml=/Users/bryanramirez-gonzalez/dev/GitHub/Basis_Info/.claude/worktrees/elegant-payne-f05924/docs/test-evidence/v0.10.0/junit.xml -m not eval_data`
- `pytest_real_corpus`: `uv run --no-sync pytest -q -p no:cacheprovider --timeout=3600 -rfEsxP -m eval_data --junitxml=/Users/bryanramirez-gonzalez/dev/GitHub/Basis_Info/.claude/worktrees/elegant-payne-f05924/docs/test-evidence/v0.10.0/junit_real_corpus.xml`

## Result

- pytest: **3571 passed**, 0 failed, 0 errors, 5 skipped, 0 expected failures (of 3576 collected)
- gate exit codes: collect=0, ruff_check=0, ruff_format=0, mypy=0, openapi=0, pytest=0, pytest_real_corpus=0

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

### Optional sets (data-gated, not in CI)

- `real_corpus` (`-m eval_data`): ran, 6 passed, 0 failed, 0 skipped, exit 0

Files: `manifest.json`, `summary.json`, `tests_collected.txt`, `gates.log`, `junit.xml`; per optional set that ran, `junit_<name>.xml` and `summary_<name>.json`.

## CI run

`ci_run.json`, `ci_run.log` and `ci_run.png` are GitHub Actions run 39011366597 on commit a8987ec
(`main`, the merge of PR 54), triggered by the push.

## What this version adds over v0.9.0

PR 53: provider account errors (no credit or quota, a bad or revoked key, no permission)
are never retried or sent to the other provider; runs stop and studies fail and can be
retried (section 26.12). PR 56: the room-stage options measured in section 26.10, wired
behind settings that keep today's behaviour by default (`ROOM_STEP_MODE`,
`MATERIALS_SECOND_LOOK`, `ROOM_MATERIALS_MODEL`; section 29). PR 55: SAM 3 throughput:
compact model output (27x smaller results, about $0.03 a study instead of $0.11), SAM 3
behind the rooms review, a self-hosted Cog option and the hosting decision (section 30 and
SAM3_HOSTING.md). PR 54: the cost sweep: the per-photo surfaces call moves to gpt-6-luna,
the room step and the classifier stay on Sonnet 5.5, and the room-step split is one setting
away (section 28).
