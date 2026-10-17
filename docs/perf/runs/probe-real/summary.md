# Load run `probe-real` (scale 1.0)

- studies 3: completed 3, failed 0
- makespan 159.3 s (real-equivalent 159 s); studies/hour (real) 67.79
- study run time real p50/p95/max {50: 154.3, 95: 159.3, 100: 159.3}
- to appraisal gate {50: 48.2, 95: 48.2, 100: 48.2}, rooms gate {50: 53.4, 95: 58.4, 100: 58.4}, assets gate {50: 63.6, 95: 73.6, 100: 73.6}
- uploads (wall s) p50/p95/max {50: 0.89, 95: 0.89, 100: 0.89}; 429s seen 0
- cost per study (recorded) mean $1.23; calls mean 132; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 27.0 / 100; workflow slots 3.0
- activity schedule-to-start {'count': 165.0, 'mean_ms': 0.3, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 176.0, 'mean_ms': 1.4, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 8.61, 'export_report': 0.05, 'extract_fields': 14.64, 'ingest_appraisal': 28.41, 'list_photos': 0.01, 'localize_photo': 20.55, 'process_assets': 9.75, 'reconcile_rooms': 5.55, 'record_run_end': 0.01, 'record_run_start': 0.08, 'transition': 0.01}
- Postgres connections max 9 {'basis': 9}; idle in transaction max 5; waiting on locks max 0; oldest transaction 25.9 s
- processes {"api": {"cpu_p50": 0.3, "cpu_p95": 2.9, "cpu_max": 9.8, "rss_max_mb": 166.3}, "worker": {"cpu_p50": 5.4, "cpu_p95": 43.7, "cpu_max": 56.8, "rss_max_mb": 833.8}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.2, "cpu_max": 0.3, "rss_max_mb": 115.3}, "postgres": {"cpu_p50": 2.93, "cpu_p95": 19.83, "cpu_max": 19.83}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 10, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 336, "failures": 6, "rps": 2.0968473892323933, "p95": 890.0, "p99": 74000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 93 | 6 | 26.0 | 190.0 | 2500.0 | 2474 |
| `POST /v1/studies/{id}/documents` | 63 | 0 | 13.0 | 25.0 | 200.0 | 203 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 63 | 0 | 15.0 | 29.0 | 30.0 | 30 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 60 | 0 | 6.0 | 8.0 | 16.0 | 16 |
| `GET /v1/studies/{id}` | 15 | 0 | 8.0 | 17.0 | 17.0 | 17 |
| `POST /v1/studies/{id}/decisions` | 9 | 0 | 18.0 | 57.0 | 57.0 | 57 |
| `GET /v1/studies/{id}/assets` | 3 | 0 | 40.0 | 46.0 | 46.0 | 46 |
| `GET /v1/studies/{id}/evidence` | 3 | 0 | 31.0 | 36.0 | 36.0 | 36 |
| `GET /v1/studies/{id}/fields` | 3 | 0 | 83.0 | 91.0 | 91.0 | 91 |
| `GET /v1/studies/{id}/rooms` | 3 | 0 | 14.0 | 16.0 | 16.0 | 16 |
| `POST /v1/studies` | 3 | 0 | 70.0 | 77.0 | 77.0 | 77 |
| `POST /v1/studies/{id}/start` | 3 | 0 | 20.0 | 41.0 | 41.0 | 41 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', ConnectionResetError(54, 'Connection reset by | `GET /v1/studies/{id}/workflow` | 3 |
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', RemoteDisconnected('Remote end closed connect | `GET /v1/studies/{id}/workflow` | 3 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 60 | 162.3 | 2.7 | 126.7 | `SELECT pg_advisory_xact_lock(hashtextextended($1, $2))` |
| 453 | 99.3 | 0.22 | 1.8 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 3 | 81.9 | 27.31 | 28.9 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 433 | 68.8 | 0.16 | 2.5 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 395 | 57.3 | 0.15 | 1.7 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 3 | 43.2 | 14.39 | 22.2 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 3 | 40.7 | 13.58 | 18.6 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 165 | 40.4 | 0.25 | 1.6 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |

