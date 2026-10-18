# Load run `emb-s10` (scale 0.1)

- studies 10: completed 10, failed 0
- makespan 55.5 s (real-equivalent 555 s); studies/hour (real) 64.83
- study run time real p50/p95/max {50: 542.5, 95: 555.2, 100: 555.2}
- to appraisal gate {50: 236.5, 95: 266.9, 100: 266.9}, rooms gate {50: 376.5, 95: 396.6, 100: 396.6}, assets gate {50: 512.1, 95: 542.0, 100: 542.0}
- uploads (wall s) p50/p95/max {50: 62.66, 95: 62.73, 100: 62.73}; 429s seen 10
- cost per study (recorded) mean $6.08; calls mean 524; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 88.0 / 100; workflow slots 2.0
- activity schedule-to-start {'count': 1211.0, 'mean_ms': 1.4, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 1102.0, 'mean_ms': 20.5, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 2.96, 'export_report': 0.4, 'extract_fields': 11.74, 'ingest_appraisal': 8.09, 'list_photos': 0.22, 'localize_photo': 0.59, 'process_assets': 12.44, 'reconcile_rooms': 3.27, 'record_run_end': 0.02, 'record_run_start': 0.08, 'transition': 0.07}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 6; waiting on locks max 0; oldest transaction 8.0 s
- processes {"api": {"cpu_p50": 4.7, "cpu_p95": 41.4, "cpu_max": 43.5, "rss_max_mb": 165.5}, "worker": {"cpu_p50": 2.1, "cpu_p95": 95.1, "cpu_max": 99.3, "rss_max_mb": 864.2}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.4, "cpu_max": 0.5, "rss_max_mb": 114.3}, "postgres": {"cpu_p50": 4.0, "cpu_p95": 66.66, "cpu_max": 66.66}}
- logs {"worker": {"pool_timeout": 21, "traceback": 21, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 21}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 10, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 2823, "failures": 10, "rps": 23.874166201888595, "p95": 110.0, "p99": 51000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 1003 | 0 | 21.0 | 120.0 | 280.0 | 441 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 540 | 10 | 43.0 | 52.0 | 75.0 | 83 |
| `POST /v1/studies/{id}/documents` | 530 | 0 | 38.0 | 47.0 | 240.0 | 244 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 520 | 0 | 8.0 | 13.0 | 15.0 | 37 |
| `GET /v1/studies/{id}` | 50 | 0 | 10.0 | 29.0 | 96.0 | 96 |
| `GET /v1/studies/{id}/evidence` | 40 | 0 | 78.0 | 110.0 | 120.0 | 117 |
| `POST /v1/studies/{id}/decisions` | 30 | 0 | 19.0 | 110.0 | 170.0 | 167 |
| `GET /v1/studies/{id}/assets` | 20 | 0 | 57.0 | 180.0 | 180.0 | 183 |
| `GET /v1/studies/{id}/fields` | 10 | 0 | 46.0 | 91.0 | 91.0 | 91 |
| `GET /v1/studies/{id}/rooms` | 10 | 0 | 28.0 | 63.0 | 63.0 | 63 |
| `POST /v1/studies` | 10 | 0 | 120.0 | 120.0 | 120.0 | 122 |
| `POST /v1/studies/{id}/start` | 10 | 0 | 48.0 | 66.0 | 66.0 | 66 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 10 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 7660 | 609.7 | 0.08 | 1.2 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 8100 | 518.0 | 0.06 | 2.9 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 10 | 312.4 | 31.24 | 56.3 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 520 | 279.9 | 0.54 | 172.8 | `SELECT pg_advisory_xact_lock(hashtextextended($1, $2))` |
| 5240 | 253.2 | 0.05 | 5.3 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 30 | 145.6 | 4.85 | 7.1 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 3640 | 129.3 | 0.04 | 5.1 | `INSERT INTO citations (study_id, asset_id, chunk_id, excerpt, verified_at, verification_method) VALUES ($1::UU` |
| 3100 | 127.8 | 0.04 | 4.7 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |

