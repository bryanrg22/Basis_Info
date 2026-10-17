# Load run `a-s5` (scale 0.1)

- studies 5: completed 4, failed 1 (RuntimeError: study timed out)
- makespan 347.9 s (real-equivalent 3479 s); studies/hour (real) 4.14
- study run time real p50/p95/max {50: 3478.6, 95: 3478.9, 100: 3478.9}
- to appraisal gate {50: 274.5, 95: 290.1, 100: 290.1}, rooms gate {50: 1877.0, 95: 1887.8, 100: 1887.8}, assets gate {50: 3447.3, 95: 3458.1, 100: 3458.1}
- uploads (wall s) p50/p95/max {50: 61.73, 95: 61.74, 100: 61.74}; 429s seen 5
- cost per study (recorded) mean $5.33; calls mean 456; failed activities 1
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 32.0 / 100; workflow slots 4.0
- activity schedule-to-start {'count': 599.0, 'mean_ms': 0.4, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 609.0, 'mean_ms': 0.9, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 16.03, 'export_report': 0.16, 'extract_fields': 26.42, 'ingest_appraisal': 0.8, 'list_photos': 0.09, 'localize_photo': 0.41, 'process_assets': 53.58, 'reconcile_rooms': 14.3, 'record_failure': 0.01, 'record_run_end': 0.01, 'record_run_start': 0.04, 'transition': 0.01}
- Postgres connections max 12 {'basis': 11, 'pg_isready': 1}; idle in transaction max 5; waiting on locks max 0; oldest transaction 110.5 s
- processes {"api": {"cpu_p50": 2.5, "cpu_p95": 5.0, "cpu_max": 23.1, "rss_max_mb": 168.3}, "worker": {"cpu_p50": 0.9, "cpu_p95": 15.5, "cpu_max": 36.2, "rss_max_mb": 374.0}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.4, "rss_max_mb": 96.3}, "postgres": {"cpu_p50": 2.95, "cpu_p95": 6.32, "cpu_max": 25.23}}
- logs {"worker": {"pool_timeout": 11, "traceback": 9, "error_lines": 1, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 9}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 5, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 4569 | 0 | 32.0 | 52.0 | 100.0 | 249 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 270 | 5 | 23.0 | 28.0 | 51.0 | 54 |
| `POST /v1/studies/{id}/documents` | 265 | 0 | 21.0 | 27.0 | 220.0 | 224 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 260 | 0 | 6.0 | 9.0 | 10.0 | 11 |
| `GET /v1/studies/{id}` | 23 | 0 | 10.0 | 17.0 | 18.0 | 18 |
| `GET /v1/studies/{id}/evidence` | 16 | 0 | 33.0 | 75.0 | 75.0 | 75 |
| `POST /v1/studies/{id}/decisions` | 14 | 0 | 15.0 | 38.0 | 38.0 | 38 |
| `GET /v1/studies/{id}/assets` | 8 | 0 | 38.0 | 57.0 | 57.0 | 57 |
| `GET /v1/studies/{id}/fields` | 5 | 0 | 85.0 | 100.0 | 100.0 | 100 |
| `GET /v1/studies/{id}/rooms` | 5 | 0 | 29.0 | 80.0 | 80.0 | 80 |
| `POST /v1/studies` | 5 | 0 | 78.0 | 81.0 | 81.0 | 81 |
| `POST /v1/studies/{id}/start` | 5 | 0 | 31.0 | 41.0 | 41.0 | 41 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 5 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 3830 | 422.2 | 0.11 | 2.1 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 4050 | 324.1 | 0.08 | 1.2 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 5436 | 210.3 | 0.04 | 1.2 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 2279 | 206.7 | 0.09 | 4.0 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 5441 | 186.1 | 0.03 | 0.5 | `SELECT users.id, users.firebase_uid, users.email, users.display_name, users.created_at, users.last_seen_at FRO` |
| 5 | 170.9 | 34.17 | 44.7 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 26 | 112.1 | 4.31 | 12.6 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 4570 | 109.0 | 0.02 | 0.6 | `SELECT runs.id, runs.study_id, runs.workflow_id, runs.run_id, runs.workflow_version, runs.input_revision, runs` |

