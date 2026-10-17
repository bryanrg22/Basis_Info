# Load run `cal-1x` (scale 1.0)

- studies 1: completed 1, failed 0
- makespan 563.7 s (real-equivalent 564 s); studies/hour (real) 6.39
- study run time real p50/p95/max {50: 563.7, 95: 563.7, 100: 563.7}
- to appraisal gate {50: 120.7, 95: 120.7, 100: 120.7}, rooms gate {50: 382.0, 95: 382.0, 100: 382.0}, assets gate {50: 558.4, 95: 558.4, 100: 558.4}
- uploads (wall s) p50/p95/max {50: 62.1, 95: 62.1, 100: 62.1}; 429s seen 2
- cost per study (recorded) mean $6.25; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 8.0 / 100; workflow slots 0.0
- activity schedule-to-start {'count': 119.0, 'mean_ms': 0.1, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 122.0, 'mean_ms': 1.1, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 32.35, 'export_report': 0.08, 'extract_fields': 117.47, 'ingest_appraisal': 0.41, 'list_photos': 0.01, 'localize_photo': 3.85, 'process_assets': 173.28, 'reconcile_rooms': 28.52, 'record_run_end': 0.01, 'record_run_start': 0.02, 'transition': 0.01}
- Postgres connections max 8 {'basis': 8}; idle in transaction max 1; waiting on locks max 0; oldest transaction 172.0 s
- processes {"api": {"cpu_p50": 0.2, "cpu_p95": 0.5, "cpu_max": 7.4, "rss_max_mb": 130.2}, "worker": {"cpu_p50": 0.7, "cpu_p95": 3.5, "cpu_max": 10.8, "rss_max_mb": 337.4}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.2, "cpu_max": 0.5, "rss_max_mb": 65.3}, "postgres": {"cpu_p50": 1.86, "cpu_p95": 6.71, "cpu_max": 9.92}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 2, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 112 | 1 | 18.0 | 48.0 | 64.0 | 66 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 55 | 2 | 15.0 | 31.0 | 49.0 | 49 |
| `POST /v1/studies/{id}/documents` | 53 | 0 | 14.0 | 30.0 | 43.0 | 43 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 52 | 0 | 6.0 | 7.0 | 10.0 | 10 |
| `GET /v1/studies/{id}` | 4 | 0 | 14.0 | 20.0 | 20.0 | 20 |
| `GET /v1/studies/{id}/evidence` | 4 | 0 | 23.0 | 30.0 | 30.0 | 30 |
| `POST /v1/studies/{id}/decisions` | 3 | 0 | 25.0 | 31.0 | 31.0 | 31 |
| `GET /v1/studies/{id}/assets` | 2 | 0 | 33.0 | 33.0 | 33.0 | 33 |
| `GET /v1/studies/{id}/fields` | 1 | 0 | 46.0 | 46.0 | 46.0 | 46 |
| `GET /v1/studies/{id}/rooms` | 1 | 0 | 100.0 | 100.0 | 100.0 | 102 |
| `POST /v1/studies` | 1 | 0 | 88.0 | 88.0 | 88.0 | 88 |
| `POST /v1/studies/{id}/start` | 1 | 0 | 42.0 | 42.0 | 42.0 | 42 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 2 |
| CatchResponseError('0 ') | `GET /v1/studies/{id}/workflow` | 1 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 766 | 86.6 | 0.11 | 5.3 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 810 | 75.6 | 0.09 | 1.1 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 523 | 59.3 | 0.11 | 2.6 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 1 | 33.0 | 33.0 | 33.0 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 119 | 24.4 | 0.2 | 1.3 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |
| 32 | 20.5 | 0.64 | 1.9 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 4 | 17.9 | 4.47 | 6.6 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 116 | 14.4 | 0.12 | 0.9 | `select coalesce(application_name, $1) as app, coalesce(state, $2) as state, count(*) as n, count(*) filter (wh` |

