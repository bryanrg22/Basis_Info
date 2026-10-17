# Load run `l12-s25` (scale 0.5)

- studies 25: completed 25, failed 0
- makespan 432.6 s (real-equivalent 865 s); studies/hour (real) 104.03
- study run time real p50/p95/max {50: 854.1, 95: 864.1, 100: 864.3}
- to appraisal gate {50: 157.2, 95: 218.3, 100: 228.5}, rooms gate {50: 583.8, 95: 609.0, 100: 629.5}, assets gate {50: 843.9, 95: 858.0, 100: 858.3}
- uploads (wall s) p50/p95/max {50: 65.7, 95: 65.93, 100: 65.93}; 429s seen 59
- cost per study (recorded) mean $6.15; calls mean 523; failed activities 0
- Temporal backlog max: activity 126 (age 23.33304 s), workflow 0
- activity slots used max 100.0 / 100; workflow slots 3.0
- activity schedule-to-start {'count': 2975.0, 'mean_ms': 6684.6, 'share_le_ms': {'100': 0.1741, '500': 0.2118, '1000': 0.2511, '5000': 0.478, '10000': 0.7385, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 3069.0, 'mean_ms': 1.2, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 17.45, 'export_report': 0.27, 'extract_fields': 57.68, 'ingest_appraisal': 1.03, 'list_photos': 0.63, 'localize_photo': 1.91, 'process_assets': 124.83, 'reconcile_rooms': 23.32, 'record_run_end': 0.05, 'record_run_start': 0.14, 'transition': 0.1}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 5; waiting on locks max 0; oldest transaction 0.2 s
- processes {"api": {"cpu_p50": 3.9, "cpu_p95": 22.5, "cpu_max": 93.6, "rss_max_mb": 182.8}, "worker": {"cpu_p50": 29.0, "cpu_p95": 42.3, "cpu_max": 88.3, "rss_max_mb": 535.1}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.3, "cpu_max": 0.5, "rss_max_mb": 113.3}, "postgres": {"cpu_p50": 7.55, "cpu_p95": 33.72, "cpu_max": 41.78}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 59, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 8791, "failures": 59, "rps": 17.65656391444618, "p95": 140.0, "p99": 79000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 4207 | 0 | 30.0 | 52.0 | 130.0 | 671 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 1373 | 48 | 100.0 | 160.0 | 250.0 | 299 |
| `POST /v1/studies/{id}/documents` | 1336 | 11 | 86.0 | 140.0 | 230.0 | 265 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 1300 | 0 | 8.0 | 11.0 | 13.0 | 116 |
| `GET /v1/studies/{id}` | 125 | 0 | 10.0 | 22.0 | 34.0 | 35 |
| `GET /v1/studies/{id}/evidence` | 100 | 0 | 39.0 | 89.0 | 130.0 | 130 |
| `POST /v1/studies/{id}/decisions` | 75 | 0 | 15.0 | 45.0 | 64.0 | 64 |
| `GET /v1/studies/{id}/assets` | 50 | 0 | 29.0 | 78.0 | 83.0 | 83 |
| `GET /v1/studies/{id}/fields` | 25 | 0 | 50.0 | 95.0 | 100.0 | 104 |
| `GET /v1/studies/{id}/rooms` | 25 | 0 | 58.0 | 80.0 | 85.0 | 85 |
| `POST /v1/studies` | 25 | 0 | 160.0 | 180.0 | 180.0 | 185 |
| `POST /v1/studies/{id}/start` | 25 | 0 | 61.0 | 130.0 | 160.0 | 157 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 48 |
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents` | 11 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 19150 | 1855.5 | 0.1 | 1.8 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 20250 | 1278.9 | 0.06 | 2.5 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 13075 | 849.0 | 0.06 | 1.3 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 25 | 732.6 | 29.31 | 70.7 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 2975 | 385.9 | 0.13 | 0.9 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |
| 75 | 335.3 | 4.47 | 6.8 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 1475 | 275.0 | 0.19 | 0.9 | `SELECT documents.id, documents.study_id, documents.kind, documents.filename, documents.content_type, documents` |
| 7750 | 254.5 | 0.03 | 1.5 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |

