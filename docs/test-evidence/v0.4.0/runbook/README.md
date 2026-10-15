# v0.4.0: the S2 runbook run end to end on the Stage 3 code

Run on 2026-10-15 from `docs/review_2026-10-15/S2_RUNBOOK.md` sections 1-8 on commit
`04328c1` (the code under test in this evidence bundle): Postgres (`pgvector/pgvector:pg16`)
in Docker on port 5544, `temporal server start-dev` (CLI 1.9.1, server 1.32.0, UI 2.54.1),
`alembic upgrade head` (0001 -> 0003), then the worker (`python -m basis.worker`), the outbox
relay (`python -m basis.relay`) and the API (`uvicorn basis.api.main:app`) as three
processes, and the runbook's client script against the API.

No `.env` was present, so no model provider and no Azure Document Intelligence were
configured: the appraisal was extracted from its text layer (112 fields written) and both
photos were flagged `ProviderNotConfigured` by the worker, as designed. The study still
completed: three decisions through the API, delivered by the relay as Temporal updates,
export written and its sha256 reported in the workflow result.

| File | What it shows |
|---|---|
| `client.log` | The client's requests and status codes: study created, three uploads confirmed, `start` 202, three decisions 202, `COMPLETED`, export path, sha256 and sections |
| `temporal_cli.log` | `temporal workflow list --query "StudyId = ..."`, the `status` query (completed, 2 photos analyzed, 2 flagged, 3 decisions applied, budget untouched) and `describe` (result with the export sha256) |
| `db_state.log` | `outbox`: 3 rows delivered, 0 retries; `activity_results`: 14 keys, all `completed`; the study row `completed`; 1 extraction with 112 fields; 10 study chunks ingested; 0 rooms/components/assets (no vision provider) |
| `process_logs_excerpt.log` | The API, relay and worker log lines for the study: transitions, the two flagged photos, the three deliveries |
| `shutdown.log` | SIGTERM to the three processes: the relay drains and stops, uvicorn shuts down, the worker drains (8 s budget) and stops; all exited within 2 s |
| `temporal_ui_workflows.png` | The Temporal UI workflow list: the study `COMPLETED` |
| `temporal_ui_study.png` | The study's page: input, result, and the timeline (130 events, 59 state transitions): `record_run_start`, `transition`, `list_photos`, `ingest_appraisal`, two failed `analyze_photo` (red), `extract_fields`, the three `decide` updates with their 7-day reminder timers, `process_assets`, `export_report`, `record_run_end` |

The same run with a configured provider is the real-data evaluation's job
(`docs/review_2026-10-15/EVAL_REPORT.md`), not this bundle's.
