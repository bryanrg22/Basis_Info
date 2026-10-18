# Load run `c-s10` (scale 0.1)

- studies 10: completed 10, failed 0
- makespan 545.5 s (real-equivalent 5455 s); studies/hour (real) 6.6
- study run time real p50/p95/max {50: 5450.6, 95: 5454.6, 100: 5454.6}
- to appraisal gate {50: 461.8, 95: 488.6, 100: 488.6}, rooms gate {50: 3718.4, 95: 3728.5, 100: 3728.5}, assets gate {50: 5424.6, 95: 5435.0, 100: 5435.0}
- uploads (wall s) p50/p95/max {50: 62.54, 95: 62.6, 100: 62.6}; 429s seen 10
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 62.0 / 100; workflow slots 9.0
- activity schedule-to-start {'count': 1190.0, 'mean_ms': 0.5, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 1223.0, 'mean_ms': 1.7, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 31.52, 'export_report': 0.34, 'extract_fields': 43.79, 'ingest_appraisal': 1.01, 'list_photos': 0.22, 'localize_photo': 0.43, 'process_assets': 168.78, 'reconcile_rooms': 30.6, 'record_run_end': 0.01, 'record_run_start': 0.11, 'transition': 0.03}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 5; waiting on locks max 0; oldest transaction 0.4 s
- processes {"api": {"cpu_p50": 7.0, "cpu_p95": 10.9, "cpu_max": 42.0, "rss_max_mb": 176.1}, "worker": {"cpu_p50": 15.0, "cpu_p95": 23.8, "cpu_max": 67.4, "rss_max_mb": 430.1}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.5, "rss_max_mb": 113.4}, "postgres": {"cpu_p50": 6.01, "cpu_p95": 22.67, "cpu_max": 24.37}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 10, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 9987 | 0 | 39.0 | 49.0 | 84.0 | 380 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 540 | 10 | 41.0 | 57.0 | 110.0 | 124 |
| `POST /v1/studies/{id}/documents` | 530 | 0 | 36.0 | 54.0 | 210.0 | 209 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 520 | 0 | 6.0 | 8.0 | 10.0 | 33 |
| `GET /v1/studies/{id}` | 42 | 0 | 9.0 | 17.0 | 49.0 | 49 |
| `GET /v1/studies/{id}/evidence` | 40 | 0 | 46.0 | 87.0 | 89.0 | 89 |
| `POST /v1/studies/{id}/decisions` | 30 | 0 | 14.0 | 35.0 | 80.0 | 80 |
| `GET /v1/studies/{id}/assets` | 20 | 0 | 29.0 | 85.0 | 85.0 | 85 |
| `GET /v1/studies/{id}/fields` | 10 | 0 | 74.0 | 120.0 | 120.0 | 117 |
| `GET /v1/studies/{id}/rooms` | 10 | 0 | 22.0 | 59.0 | 59.0 | 59 |
| `POST /v1/studies` | 10 | 0 | 99.0 | 110.0 | 110.0 | 109 |
| `POST /v1/studies/{id}/start` | 10 | 0 | 45.0 | 60.0 | 60.0 | 60 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 10 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 7660 | 1100.4 | 0.14 | 1.7 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 8100 | 792.5 | 0.1 | 2.3 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 5230 | 503.1 | 0.1 | 1.2 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 520 | 337.8 | 0.65 | 298.0 | `SELECT pg_advisory_xact_lock(hashtextextended($1, $2))` |
| 11746 | 334.3 | 0.03 | 0.7 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 10 | 305.0 | 30.5 | 64.7 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 43180 | 277.9 | 0.01 | 1.5 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 1190 | 231.1 | 0.19 | 2.1 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |

