# Load run `b-s10` (scale 0.1)

- studies 10: completed 10, failed 0
- makespan 66.4 s (real-equivalent 664 s); studies/hour (real) 54.2
- study run time real p50/p95/max {50: 652.8, 95: 663.6, 100: 663.6}
- to appraisal gate {50: 134.3, 95: 144.7, 100: 144.7}, rooms gate {50: 275.8, 95: 301.4, 100: 301.4}, assets gate {50: 415.1, 95: 428.7, 100: 428.7}
- uploads (wall s) p50/p95/max {50: 62.37, 95: 62.45, 100: 62.45}; 429s seen 10
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 87.0 / 100; workflow slots 3.0
- activity schedule-to-start {'count': 1146.0, 'mean_ms': 0.6, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 1100.0, 'mean_ms': 13.7, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 2.15, 'extract_fields': 11.32, 'ingest_appraisal': 1.03, 'list_photos': 0.28, 'localize_photo': 3.45, 'process_assets': 10.93, 'reconcile_rooms': 2.67, 'record_run_start': 0.08, 'transition': 0.04}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 5; waiting on locks max 0; oldest transaction 1.6 s
- processes {"api": {"cpu_p50": 5.5, "cpu_p95": 37.5, "cpu_max": 41.9, "rss_max_mb": 190.2}, "worker": {"cpu_p50": 1.0, "cpu_p95": 78.1, "cpu_max": 86.3, "rss_max_mb": 508.4}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.4, "cpu_max": 0.4, "rss_max_mb": 119.1}, "postgres": {"cpu_p50": 9.15, "cpu_p95": 23.14, "cpu_max": 23.14}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 10, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 3048, "failures": 10, "rps": 23.736051536808986, "p95": 62.0, "p99": 30000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 1230 | 0 | 20.0 | 53.0 | 210.0 | 590 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 540 | 10 | 40.0 | 51.0 | 67.0 | 76 |
| `POST /v1/studies/{id}/documents` | 530 | 0 | 35.0 | 45.0 | 210.0 | 223 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 520 | 0 | 5.0 | 9.0 | 11.0 | 15 |
| `GET /v1/studies/{id}` | 49 | 0 | 7.0 | 21.0 | 51.0 | 51 |
| `GET /v1/studies/{id}/evidence` | 40 | 0 | 31.0 | 75.0 | 81.0 | 81 |
| `POST /v1/studies/{id}/decisions` | 30 | 0 | 14.0 | 30.0 | 32.0 | 32 |
| `GET /v1/studies/{id}/assets` | 20 | 0 | 25.0 | 78.0 | 78.0 | 78 |
| `GET /v1/studies/{id}/fields` | 10 | 0 | 62.0 | 67.0 | 67.0 | 67 |
| `GET /v1/studies/{id}/rooms` | 10 | 0 | 16.0 | 62.0 | 62.0 | 62 |
| `POST /v1/studies` | 10 | 0 | 100.0 | 110.0 | 110.0 | 105 |
| `POST /v1/studies/{id}/start` | 10 | 0 | 44.0 | 54.0 | 54.0 | 54 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 10 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 7660 | 613.9 | 0.08 | 3.1 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 8100 | 375.1 | 0.05 | 0.5 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 43180 | 264.1 | 0.01 | 0.2 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 10 | 225.8 | 22.58 | 39.8 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 5230 | 218.0 | 0.04 | 0.7 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 3640 | 109.0 | 0.03 | 0.3 | `INSERT INTO citations (study_id, asset_id, chunk_id, excerpt, verified_at, verification_method) VALUES ($1::UU` |
| 520 | 108.7 | 0.21 | 0.8 | `SELECT components.label FROM components WHERE components.study_id = $1::UUID AND components.document_id = $2::` |
| 3100 | 105.2 | 0.03 | 0.2 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |

