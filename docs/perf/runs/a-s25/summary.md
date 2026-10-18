# Load run `a-s25` (scale 0.1)

- studies 25: completed 0, failed 25 (RuntimeError: study timed out)
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: 65.71, 95: 65.9, 100: 65.93}; 429s seen 36
- cost per study (recorded) mean $2.68; calls mean 217; failed activities 23
- Temporal backlog max: activity 99 (age 62.885944 s), workflow 0
- activity slots used max 100.0 / 100; workflow slots 5.0
- activity schedule-to-start {'count': 2956.0, 'mean_ms': 14716.9, 'share_le_ms': {'100': 0.1691, '500': 0.1763, '1000': 0.1854, '5000': 0.2642, '10000': 0.4198, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 2935.0, 'mean_ms': 1.6, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 62.19, 'extract_fields': 69.37, 'ingest_appraisal': 0.8, 'list_photos': 0.26, 'localize_photo': 0.42, 'process_assets': 20.8, 'reconcile_rooms': 55.84, 'record_failure': 2.44, 'record_run_start': 0.56, 'transition': 3.12}
- Postgres connections max 11 {'basis': 11, '': 1}; idle in transaction max 10; waiting on locks max 0; oldest transaction 64.6 s
- processes {"api": {"cpu_p50": 15.1, "cpu_p95": 17.8, "cpu_max": 94.8, "rss_max_mb": 175.6}, "worker": {"cpu_p50": 18.2, "cpu_p95": 24.3, "cpu_max": 82.2, "rss_max_mb": 519.0}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.2, "cpu_max": 0.4, "rss_max_mb": 112.8}, "postgres": {"cpu_p50": 5.95, "cpu_p95": 17.9, "cpu_max": 29.54}}
- logs {"worker": {"pool_timeout": 142, "traceback": 112, "error_lines": 24, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 108}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 36, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 49565 | 0 | 41.0 | 68.0 | 110.0 | 341 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 1354 | 29 | 110.0 | 150.0 | 180.0 | 228 |
| `POST /v1/studies/{id}/documents` | 1332 | 7 | 88.0 | 140.0 | 230.0 | 264 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 1300 | 0 | 9.0 | 27.0 | 44.0 | 85 |
| `GET /v1/studies/{id}` | 75 | 0 | 15.0 | 44.0 | 89.0 | 89 |
| `POST /v1/studies/{id}/decisions` | 50 | 0 | 15.0 | 45.0 | 70.0 | 70 |
| `GET /v1/studies/{id}/fields` | 25 | 0 | 39.0 | 67.0 | 88.0 | 88 |
| `GET /v1/studies/{id}/rooms` | 25 | 0 | 39.0 | 99.0 | 100.0 | 101 |
| `POST /v1/studies` | 25 | 0 | 150.0 | 170.0 | 180.0 | 176 |
| `POST /v1/studies/{id}/start` | 25 | 0 | 65.0 | 120.0 | 130.0 | 125 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 29 |
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents` | 7 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 19150 | 2346.9 | 0.12 | 1.4 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 20240 | 1675.2 | 0.08 | 1.3 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 53737 | 1202.6 | 0.02 | 2.5 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 4745 | 831.2 | 0.18 | 1.2 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 49587 | 762.8 | 0.02 | 0.3 | `SELECT runs.id, runs.study_id, runs.workflow_id, runs.run_id, runs.workflow_version, runs.input_revision, runs` |
| 53762 | 636.4 | 0.01 | 1.4 | `SELECT users.id, users.firebase_uid, users.email, users.display_name, users.created_at, users.last_seen_at FRO` |
| 25 | 628.1 | 25.12 | 47.3 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 2924 | 478.0 | 0.16 | 0.8 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |

