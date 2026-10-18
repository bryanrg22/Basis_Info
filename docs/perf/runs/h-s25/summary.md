# Load run `h-s25` (scale 0.5)

- studies 25: completed 25, failed 0
- makespan 783.6 s (real-equivalent 1567 s); studies/hour (real) 57.43
- study run time real p50/p95/max {50: 1535.9, 95: 1566.1, 100: 1566.5}
- to appraisal gate {50: 172.8, 95: 208.4, 100: 244.0}, rooms gate {50: 594.9, 95: 614.6, 100: 619.6}, assets gate {50: 840.5, 95: 848.4, 100: 848.5}
- uploads (wall s) p50/p95/max {50: 65.66, 95: 65.87, 100: 65.9}; 429s seen 52
- cost per study (recorded) mean $6.15; calls mean 523; failed activities 0
- Temporal backlog max: activity 125 (age 37.037867 s), workflow 0
- activity slots used max 100.0 / 100; workflow slots 4.0
- activity schedule-to-start {'count': 2963.0, 'mean_ms': 8748.1, 'share_le_ms': {'100': 0.3854, '500': 0.4013, '1000': 0.4144, '5000': 0.5012, '10000': 0.6649, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 3055.0, 'mean_ms': 1.6, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 10.4, 'export_report': 0.11, 'extract_fields': 53.13, 'ingest_appraisal': 1.06, 'list_photos': 0.38, 'localize_photo': 37.15, 'process_assets': 116.66, 'reconcile_rooms': 24.1, 'record_run_end': 0.01, 'record_run_start': 0.16, 'transition': 0.07}
- Postgres connections max 12 {'basis': 11, '': 1}; idle in transaction max 5; waiting on locks max 0; oldest transaction 1.9 s
- processes {"api": {"cpu_p50": 4.0, "cpu_p95": 10.2, "cpu_max": 95.4, "rss_max_mb": 176.5}, "worker": {"cpu_p50": 19.9, "cpu_p95": 39.2, "cpu_max": 81.1, "rss_max_mb": 531.8}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.5, "rss_max_mb": 113.6}, "postgres": {"cpu_p50": 6.32, "cpu_p95": 22.42, "cpu_max": 40.98}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 52, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 12111, "failures": 52, "rps": 14.305615066624554, "p95": 130.0, "p99": 64000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 7538 | 0 | 34.0 | 61.0 | 120.0 | 400 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 1371 | 46 | 100.0 | 150.0 | 170.0 | 193 |
| `POST /v1/studies/{id}/documents` | 1331 | 6 | 87.0 | 140.0 | 240.0 | 253 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 1300 | 0 | 6.0 | 10.0 | 12.0 | 20 |
| `GET /v1/studies/{id}` | 123 | 0 | 10.0 | 43.0 | 100.0 | 118 |
| `GET /v1/studies/{id}/evidence` | 100 | 0 | 49.0 | 130.0 | 170.0 | 171 |
| `POST /v1/studies/{id}/decisions` | 75 | 0 | 14.0 | 110.0 | 180.0 | 183 |
| `GET /v1/studies/{id}/assets` | 50 | 0 | 42.0 | 140.0 | 180.0 | 177 |
| `GET /v1/studies/{id}/fields` | 25 | 0 | 46.0 | 80.0 | 110.0 | 114 |
| `GET /v1/studies/{id}/rooms` | 25 | 0 | 21.0 | 74.0 | 75.0 | 75 |
| `POST /v1/studies` | 25 | 0 | 140.0 | 170.0 | 170.0 | 175 |
| `POST /v1/studies/{id}/start` | 25 | 0 | 74.0 | 140.0 | 160.0 | 165 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 46 |
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents` | 6 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 19150 | 2461.8 | 0.13 | 3.8 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 20250 | 1470.8 | 0.07 | 4.4 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 13075 | 950.2 | 0.07 | 8.3 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 25 | 717.6 | 28.7 | 69.9 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 2975 | 530.4 | 0.18 | 3.1 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |
| 1300 | 455.8 | 0.35 | 1.5 | `SELECT components.label FROM components WHERE components.study_id = $1::UUID AND components.document_id = $2::` |
| 100 | 377.8 | 3.78 | 9.6 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 75 | 299.7 | 4.0 | 6.8 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |

