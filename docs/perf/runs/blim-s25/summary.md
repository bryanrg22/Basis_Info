# Load run `blim-s25` (scale 0.1)

- studies 25: completed 25, failed 0
- makespan 161.7 s (real-equivalent 1617 s); studies/hour (real) 55.67
- study run time real p50/p95/max {50: 1586.3, 95: 1605.5, 100: 1609.5}
- to appraisal gate {50: 178.8, 95: 282.0, 100: 283.1}, rooms gate {50: 648.0, 95: 693.2, 100: 708.7}, assets gate {50: 944.4, 95: 964.7, 100: 970.9}
- uploads (wall s) p50/p95/max {50: 65.89, 95: 66.1, 100: 66.11}; 429s seen 50
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 129 (age 7.810017 s), workflow 0
- activity slots used max 100.0 / 100; workflow slots 7.0
- activity schedule-to-start {'count': 2910.0, 'mean_ms': 1769.3, 'share_le_ms': {'100': 0.3928, '500': 0.4591, '1000': 0.5306, '5000': 0.8605, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 2903.0, 'mean_ms': 17.0, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 2.18, 'export_report': 0.1, 'extract_fields': 10.69, 'ingest_appraisal': 0.95, 'list_photos': 0.55, 'localize_photo': 7.53, 'process_assets': 24.48, 'reconcile_rooms': 6.41, 'record_run_end': 0.01, 'record_run_start': 0.48, 'transition': 0.33}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 6; waiting on locks max 0; oldest transaction 1.7 s
- processes {"api": {"cpu_p50": 14.7, "cpu_p95": 43.7, "cpu_max": 93.5, "rss_max_mb": 196.5}, "worker": {"cpu_p50": 27.8, "cpu_p95": 99.5, "cpu_max": 102.8, "rss_max_mb": 514.7}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.3, "cpu_max": 0.4, "rss_max_mb": 119.8}, "postgres": {"cpu_p50": 10.22, "cpu_p95": 25.15, "cpu_max": 25.15}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 50, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 11935, "failures": 50, "rps": 52.6664354467313, "p95": 140.0, "p99": 15000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 7362 | 0 | 24.0 | 82.0 | 200.0 | 559 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 1375 | 50 | 100.0 | 170.0 | 250.0 | 304 |
| `POST /v1/studies/{id}/documents` | 1325 | 0 | 88.0 | 140.0 | 240.0 | 274 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 1300 | 0 | 7.0 | 15.0 | 21.0 | 62 |
| `GET /v1/studies/{id}` | 124 | 0 | 9.0 | 17.0 | 64.0 | 138 |
| `GET /v1/studies/{id}/evidence` | 100 | 0 | 29.0 | 70.0 | 96.0 | 96 |
| `POST /v1/studies/{id}/decisions` | 75 | 0 | 16.0 | 38.0 | 61.0 | 61 |
| `GET /v1/studies/{id}/assets` | 50 | 0 | 28.0 | 69.0 | 93.0 | 93 |
| `GET /v1/studies/{id}/fields` | 25 | 0 | 38.0 | 65.0 | 96.0 | 96 |
| `GET /v1/studies/{id}/rooms` | 25 | 0 | 20.0 | 55.0 | 65.0 | 65 |
| `POST /v1/studies` | 25 | 0 | 150.0 | 180.0 | 180.0 | 177 |
| `POST /v1/studies/{id}/start` | 25 | 0 | 69.0 | 100.0 | 120.0 | 121 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 50 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 19150 | 1550.6 | 0.08 | 1.7 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 20250 | 996.1 | 0.05 | 1.1 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 107950 | 777.8 | 0.01 | 0.4 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 25 | 602.3 | 24.09 | 53.0 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 13075 | 552.0 | 0.04 | 0.8 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 7750 | 316.2 | 0.04 | 0.7 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |
| 9100 | 316.1 | 0.03 | 0.6 | `INSERT INTO citations (study_id, asset_id, chunk_id, excerpt, verified_at, verification_method) VALUES ($1::UU` |
| 1300 | 298.3 | 0.23 | 1.5 | `SELECT components.label FROM components WHERE components.study_id = $1::UUID AND components.document_id = $2::` |

