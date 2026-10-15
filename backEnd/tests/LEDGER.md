# Test ledger

Every test file, the invariant it protects, and the release that introduced it
(BUILD_PLAN section 0, rule 5). Changing a behavior means changing its test in the same
change, with the reason stated in the commit message and in the Notes column here.

Run everything from `backEnd/` with `uv run pytest -q`. `-m "not legacy"` runs only the
basis suite; `-m "not slow"` skips the subprocess-based checks.

## basis

| Test file | Invariant protected | Introduced in | Notes |
|---|---|---|---|
| `tests/unit/config/test_settings.py` | `BASIS_ENV` is explicit and required (missing, blank or unknown raises; a `.env` cannot select it); only `dev` reads `.env`, and process env overrides it; `test`/`staging`/`prod` never read a file; `staging`/`prod` raise `ConfigError` naming every missing required value (`DATABASE_URL`, `GCS_BUCKET`, `FIREBASE_PROJECT_ID`); secrets are `SecretStr`; neither `repr`, `model_dump_json`, nor any error message contains a secret or the database URL; settings are immutable and cached until `reset_settings()` | v0.1.0 (S0) | 37 tests. BUILD_PLAN C1, 6.1 |
| `tests/unit/obs/test_logging.py` | One JSON object per line with `severity` (Cloud Logging names), `message`, `logger`, `timestamp` (RFC 3339 UTC); bound `request_id`/`study_id`/`run_id`/`activity` on every line and gone after `clear_context()`; concurrent tasks never share context; `trace` becomes `logging.googleapis.com/trace`; any attribute or context key matching `(?i)(key\|token\|secret\|password\|authorization)` is written as `***`, including nested; `logger.exception` writes `stack_trace`; `configure_logging` is idempotent and never removes foreign handlers; uvicorn logs propagate | v0.1.0 (S0) | 50 tests. BUILD_PLAN 6.1, section 8. Message text is not scanned for secrets; uvicorn `color_message` is dropped |
| `tests/api/test_health.py` | `/health` returns status, service, version, git sha and env with no auth; `/` returns service and docs URL; no other routes exist; `X-Request-Id` echoed when well formed, else a fresh UUID4, also on 404; log lines inside a request carry its id; the id does not outlive the request; concurrent requests log their own ids; `basis.api.main` builds the app from the environment | v0.1.0 (S0) | 26 tests. BUILD_PLAN C3, C16, 6.4. Dependency checks in `/health` arrive with the database (S1) |
| `tests/unit/legacy/test_src_isolation.py` | The root `conftest.py` switcher keeps evidence_layer's and vision_layer's top-level `src` packages apart so all legacy suites run in one process, in either order | v0.1.0 (S0) | 9 tests, one `slow` + `regression` (subprocess). Delete with the legacy packages (S3) |
| `tests/unit/legacy/test_vision_lazy_replicate.py` | vision_layer Replicate clients import without the `replicate` SDK, and calling them without it still raises `ImportError` | v0.1.0 (S0) | 2 tests, `slow` + `regression` (subprocess). Delete with vision_layer (S3) |

## regression (reproduced legacy defects, strict `xfail` until fixed)

Each test asserts the correct behavior and is marked `xfail(strict=True)`, so it fails the
run the moment the behavior is fixed; the fix removes the mark in the same change.
Run with `-m regression`. Sources: REVIEW_AND_PLAN.md A.3 (W1, W2, W3, W5) and A.8.

| Test file | Invariant protected | Introduced in | Notes |
|---|---|---|---|
| `tests/regression/test_legacy_graph_reachability.py` | Every workflow node is reachable from START | v0.1.0 (S0) | xfail: 4 of 7 nodes orphaned (W1). Fixed by S2 (Temporal workflow replaces the graph) |
| `tests/regression/test_legacy_resume_runs_expected_node.py` | Resuming at a stage runs that stage's next node, never extraction again | v0.1.0 (S0) | 2 xfails (W2). Fixed by S2 |
| `tests/regression/test_legacy_rejection_not_recorded_as_approval.py` | A rejection never writes an approval | v0.1.0 (S0) | xfail (W3). Fixed by S2 |
| `tests/regression/test_legacy_citation_extraction.py` | Search tool results become citations | v0.1.0 (S0) | 1 xfail + 1 passing test documenting the three causes (W5). Fixed by S3 |
| `tests/regression/test_legacy_critical_fields_mapping.py` | Every critical field name is produced by the mapping tables | v0.1.0 (S0) | xfail (A.8, commit 277703c). Fixed by S3 |

## legacy (pre-rebuild, auto-marked `legacy`)

These suites predate the rebuild. They are migrated into `tests/` module by module in
S3 and deleted with their packages. Counts are as of v0.1.0.

| Test file | Invariant protected | Introduced in | Notes |
|---|---|---|---|
| `agentic/tests/test_agents.py` | Agent framework models (citation, stage context, component input) and the asset classification agent | pre-rebuild | 13 tests. To migrate in S3 |
| `agentic/tests/test_azure_di_cleaner.py` | Azure DI value cleaners | pre-rebuild | 41 tests. Becomes `unit/extraction/test_cleaners.py` (S3) |
| `agentic/tests/test_config.py` | Legacy agentic settings and LLM provider selection | pre-rebuild | 6 tests. Reads `backEnd/.env` through the legacy settings when present (pre-existing) |
| `agentic/tests/test_mcp_server.py` | MCP tool input schemas | pre-rebuild | 5 tests. Removed with the MCP server (S3) |
| `agentic/tests/test_static_rules.py` | Static IRS classification rules | pre-rebuild | 33 tests. Becomes `unit/domain/test_rules_table.py` (S3) |
| `agentic/tests/integration/test_workflow_e2e.py` | Evidence aggregation, evidence entries and packs, checkpoint history, alerts, cost tracking, decision logging | pre-rebuild | 21 tests. Superseded by Temporal lifecycle tests (S2) |
| `evidence_layer/tests/test_tokenizers.py` | IRS-aware tokenizer | pre-rebuild | 14 tests. Becomes `unit/retrieval/test_tokenizer.py` (S3) |
| `vision_layer/tests/test_detection.py` | Detection schemas, bounding boxes, masks | pre-rebuild | 16 tests. To migrate in S3 |
| `vision_layer/tests/test_validation.py` | Grounding verifier | pre-rebuild | 5 tests. To migrate in S3 |
