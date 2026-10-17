# Load run `b-s5` (scale 0.1)

- studies 5: completed 5, failed 0
- makespan 34.4 s (real-equivalent 344 s); studies/hour (real) 52.3
- study run time real p50/p95/max {50: 333.4, 95: 344.0, 100: 344.0}
- to appraisal gate {50: 117.4, 95: 122.6, 100: 122.6}, rooms gate {50: 191.1, 95: 206.6, 100: 206.6}, assets gate {50: 271.3, 95: 282.4, 100: 282.4}
- uploads (wall s) p50/p95/max {50: 61.43, 95: 61.43, 100: 61.43}; 429s seen 5
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 45.0 / 100; workflow slots 2.0
- activity schedule-to-start {'count': 565.0, 'mean_ms': 0.2, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 539.0, 'mean_ms': 9.4, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 1.47, 'extract_fields': 10.32, 'ingest_appraisal': 0.75, 'list_photos': 0.06, 'localize_photo': 1.72, 'process_assets': 6.15, 'reconcile_rooms': 2.64, 'record_run_start': 0.03, 'transition': 0.01}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 4; waiting on locks max 0; oldest transaction 0.1 s
- processes {"api": {"cpu_p50": 0.2, "cpu_p95": 21.4, "cpu_max": 21.4, "rss_max_mb": 188.5}, "worker": {"cpu_p50": 0.0, "cpu_p95": 58.4, "cpu_max": 58.4, "rss_max_mb": 472.6}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.3, "rss_max_mb": 120.1}, "postgres": {"cpu_p50": 3.15, "cpu_p95": 21.24, "cpu_max": 21.24}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 5, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 1224, "failures": 5, "rps": 12.858611439317105, "p95": 52.0, "p99": 27000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 316 | 0 | 16.0 | 56.0 | 180.0 | 227 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 270 | 5 | 20.0 | 31.0 | 51.0 | 54 |
| `POST /v1/studies/{id}/documents` | 265 | 0 | 19.0 | 24.0 | 210.0 | 215 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 260 | 0 | 5.0 | 7.0 | 9.0 | 12 |
| `GET /v1/studies/{id}` | 24 | 0 | 8.0 | 11.0 | 11.0 | 11 |
| `GET /v1/studies/{id}/evidence` | 20 | 0 | 26.0 | 64.0 | 64.0 | 64 |
| `POST /v1/studies/{id}/decisions` | 15 | 0 | 13.0 | 32.0 | 32.0 | 32 |
| `GET /v1/studies/{id}/assets` | 10 | 0 | 25.0 | 65.0 | 65.0 | 65 |
| `GET /v1/studies/{id}/fields` | 5 | 0 | 66.0 | 67.0 | 67.0 | 67 |
| `GET /v1/studies/{id}/rooms` | 5 | 0 | 18.0 | 48.0 | 48.0 | 48 |
| `POST /v1/studies` | 5 | 0 | 73.0 | 81.0 | 81.0 | 81 |
| `POST /v1/studies/{id}/start` | 5 | 0 | 26.0 | 38.0 | 38.0 | 38 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 5 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 3830 | 277.4 | 0.07 | 0.7 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 4050 | 187.2 | 0.05 | 0.4 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 5 | 117.2 | 23.45 | 42.2 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 21590 | 114.0 | 0.01 | 0.5 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 2615 | 105.1 | 0.04 | 1.1 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 20 | 60.8 | 3.04 | 5.7 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 1820 | 52.0 | 0.03 | 0.3 | `INSERT INTO citations (study_id, asset_id, chunk_id, excerpt, verified_at, verification_method) VALUES ($1::UU` |
| 1550 | 51.5 | 0.03 | 0.6 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |

