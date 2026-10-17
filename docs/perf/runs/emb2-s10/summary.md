# Load run `emb2-s10` (scale 0.1)

- studies 10: completed 10, failed 0
- makespan 49.8 s (real-equivalent 498 s); studies/hour (real) 72.28
- study run time real p50/p95/max {50: 476.8, 95: 497.3, 100: 497.3}
- to appraisal gate {50: 274.9, 95: 290.5, 100: 290.5}, rooms gate {50: 307.9, 95: 327.8, 100: 327.8}, assets gate {50: 448.9, 95: 476.5, 100: 476.5}
- uploads (wall s) p50/p95/max {50: 62.86, 95: 62.89, 100: 62.89}; 429s seen 10
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 95.0 / 100; workflow slots 10.0
- activity schedule-to-start {'count': 1190.0, 'mean_ms': 2.1, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 1114.0, 'mean_ms': 17.1, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 2.39, 'export_report': 0.42, 'extract_fields': 10.71, 'ingest_appraisal': 15.87, 'list_photos': 0.39, 'localize_photo': 0.92, 'process_assets': 12.86, 'reconcile_rooms': 3.19, 'record_run_end': 0.01, 'record_run_start': 0.07, 'transition': 0.04}
- Postgres connections max 12 {'basis': 11, '': 1}; idle in transaction max 7; waiting on locks max 0; oldest transaction 0.7 s
- processes {"api": {"cpu_p50": 0.2, "cpu_p95": 41.1, "cpu_max": 45.7, "rss_max_mb": 192.9}, "worker": {"cpu_p50": 0.1, "cpu_p95": 112.6, "cpu_max": 145.5, "rss_max_mb": 951.5}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.3, "cpu_max": 0.4, "rss_max_mb": 113.8}, "postgres": {"cpu_p50": 4.83, "cpu_p95": 39.51, "cpu_max": 39.51}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 10, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 2711, "failures": 10, "rps": 24.0522776787848, "p95": 120.0, "p99": 45000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 891 | 0 | 20.0 | 140.0 | 280.0 | 304 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 540 | 10 | 44.0 | 57.0 | 74.0 | 83 |
| `POST /v1/studies/{id}/documents` | 530 | 0 | 39.0 | 57.0 | 250.0 | 262 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 520 | 0 | 7.0 | 10.0 | 18.0 | 74 |
| `GET /v1/studies/{id}` | 50 | 0 | 11.0 | 24.0 | 62.0 | 62 |
| `GET /v1/studies/{id}/evidence` | 40 | 0 | 43.0 | 100.0 | 100.0 | 102 |
| `POST /v1/studies/{id}/decisions` | 30 | 0 | 18.0 | 76.0 | 78.0 | 78 |
| `GET /v1/studies/{id}/assets` | 20 | 0 | 42.0 | 94.0 | 94.0 | 94 |
| `GET /v1/studies/{id}/fields` | 10 | 0 | 74.0 | 130.0 | 130.0 | 127 |
| `GET /v1/studies/{id}/rooms` | 10 | 0 | 36.0 | 65.0 | 65.0 | 65 |
| `POST /v1/studies` | 10 | 0 | 120.0 | 120.0 | 120.0 | 123 |
| `POST /v1/studies/{id}/start` | 10 | 0 | 79.0 | 93.0 | 93.0 | 93 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 10 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 7660 | 510.1 | 0.07 | 1.1 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 8100 | 412.3 | 0.05 | 2.8 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 10 | 303.2 | 30.32 | 84.4 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 5230 | 203.1 | 0.04 | 6.9 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 30 | 159.7 | 5.32 | 8.1 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 520 | 140.7 | 0.27 | 99.3 | `SELECT pg_advisory_xact_lock(hashtextextended($1, $2))` |
| 3640 | 108.7 | 0.03 | 0.4 | `INSERT INTO citations (study_id, asset_id, chunk_id, excerpt, verified_at, verification_method) VALUES ($1::UU` |
| 3100 | 106.6 | 0.03 | 0.4 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |

