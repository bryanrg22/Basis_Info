# Load run `a-s10` (scale 0.1)

- studies 10: completed 4, failed 6 (RuntimeError: study timed out)
- makespan 536.8 s (real-equivalent 5368 s); studies/hour (real) 2.68
- study run time real p50/p95/max {50: 5367.3, 95: 5367.8, 100: 5367.8}
- to appraisal gate {50: 450.1, 95: 493.1, 100: 493.1}, rooms gate {50: 3732.4, 95: 3736.9, 100: 3736.9}, assets gate {50: 5337.6, 95: 5347.5, 100: 5347.5}
- uploads (wall s) p50/p95/max {50: 62.4, 95: 62.45, 100: 62.45}; 429s seen 10
- cost per study (recorded) mean $3.84; calls mean 322; failed activities 6
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 61.0 / 100; workflow slots 6.0
- activity schedule-to-start {'count': 1190.0, 'mean_ms': 0.7, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 1198.0, 'mean_ms': 1.5, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 31.68, 'export_report': 0.23, 'extract_fields': 44.0, 'ingest_appraisal': 1.06, 'list_photos': 0.22, 'localize_photo': 0.42, 'process_assets': 35.38, 'reconcile_rooms': 30.08, 'record_failure': 7.94, 'record_run_end': 0.05, 'record_run_start': 0.07, 'transition': 0.02}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 6; waiting on locks max 1; oldest transaction 112.5 s
- processes {"api": {"cpu_p50": 5.7, "cpu_p95": 9.1, "cpu_max": 41.7, "rss_max_mb": 187.2}, "worker": {"cpu_p50": 2.4, "cpu_p95": 18.0, "cpu_max": 61.4, "rss_max_mb": 514.2}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.6, "rss_max_mb": 114.5}, "postgres": {"cpu_p50": 4.02, "cpu_p95": 7.8, "cpu_max": 24.64}}
- logs {"worker": {"pool_timeout": 40, "traceback": 31, "error_lines": 7, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 30}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 10, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 15939 | 0 | 36.0 | 48.0 | 90.0 | 387 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 540 | 10 | 41.0 | 50.0 | 67.0 | 71 |
| `POST /v1/studies/{id}/documents` | 530 | 0 | 36.0 | 46.0 | 210.0 | 213 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 520 | 0 | 6.0 | 8.0 | 10.0 | 12 |
| `GET /v1/studies/{id}` | 38 | 0 | 8.0 | 14.0 | 42.0 | 42 |
| `POST /v1/studies/{id}/decisions` | 24 | 0 | 15.0 | 22.0 | 27.0 | 27 |
| `GET /v1/studies/{id}/evidence` | 16 | 0 | 30.0 | 64.0 | 64.0 | 64 |
| `GET /v1/studies/{id}/fields` | 10 | 0 | 88.0 | 110.0 | 110.0 | 106 |
| `GET /v1/studies/{id}/rooms` | 10 | 0 | 28.0 | 54.0 | 54.0 | 54 |
| `POST /v1/studies` | 10 | 0 | 98.0 | 100.0 | 100.0 | 104 |
| `POST /v1/studies/{id}/start` | 10 | 0 | 45.0 | 56.0 | 56.0 | 56 |
| `GET /v1/studies/{id}/assets` | 8 | 0 | 21.0 | 29.0 | 29.0 | 29 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 10 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 7660 | 1020.8 | 0.13 | 3.3 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 8100 | 773.3 | 0.1 | 2.1 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 17635 | 544.7 | 0.03 | 1.0 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 3224 | 440.5 | 0.14 | 1.7 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 10 | 429.0 | 42.9 | 91.8 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 17645 | 386.7 | 0.02 | 1.1 | `SELECT users.id, users.firebase_uid, users.email, users.display_name, users.created_at, users.last_seen_at FRO` |
| 15939 | 268.0 | 0.02 | 0.5 | `SELECT runs.id, runs.study_id, runs.workflow_id, runs.run_id, runs.workflow_version, runs.input_revision, runs` |
| 1184 | 218.8 | 0.18 | 0.8 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |

