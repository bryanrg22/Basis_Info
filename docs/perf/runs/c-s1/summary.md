# Load run `c-s1` (scale 0.1)

- studies 1: completed 1, failed 0
- makespan 60.9 s (real-equivalent 609 s); studies/hour (real) 5.91
- study run time real p50/p95/max {50: 609.3, 95: 609.3, 100: 609.3}
- to appraisal gate {50: 130.3, 95: 130.3, 100: 130.3}, rooms gate {50: 362.4, 95: 362.4, 100: 362.4}, assets gate {50: 584.1, 95: 584.1, 100: 584.1}
- uploads (wall s) p50/p95/max {50: 61.89, 95: 61.89, 100: 61.89}; 429s seen 1
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 7.0 / 100; workflow slots 0.0
- activity schedule-to-start {'count': 117.0, 'mean_ms': 0.1, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 121.0, 'mean_ms': 1.9, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 3.04, 'extract_fields': 12.24, 'ingest_appraisal': 0.26, 'list_photos': 0.01, 'localize_photo': 0.43, 'process_assets': 30.27, 'reconcile_rooms': 3.09, 'record_run_start': 0.01, 'transition': 0.01}
- Postgres connections max 7 {'basis': 7}; idle in transaction max 1; waiting on locks max 0; oldest transaction 0.0 s
- processes {"api": {"cpu_p50": 1.6, "cpu_p95": 5.5, "cpu_max": 10.0, "rss_max_mb": 161.2}, "worker": {"cpu_p50": 12.5, "cpu_p95": 19.7, "cpu_max": 28.9, "rss_max_mb": 415.4}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.4, "rss_max_mb": 113.7}, "postgres": {"cpu_p50": 4.81, "cpu_p95": 8.03, "cpu_max": 8.03}}
- logs {"worker": {"pool_timeout": 2, "traceback": 0, "error_lines": 3, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 1, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 111 | 0 | 31.0 | 49.0 | 95.0 | 105 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 54 | 1 | 12.0 | 26.0 | 76.0 | 76 |
| `POST /v1/studies/{id}/documents` | 53 | 0 | 13.0 | 24.0 | 28.0 | 28 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 52 | 0 | 8.0 | 10.0 | 11.0 | 11 |
| `GET /v1/studies/{id}` | 4 | 0 | 19.0 | 28.0 | 28.0 | 28 |
| `GET /v1/studies/{id}/evidence` | 4 | 0 | 31.0 | 47.0 | 47.0 | 47 |
| `POST /v1/studies/{id}/decisions` | 3 | 0 | 24.0 | 26.0 | 26.0 | 26 |
| `GET /v1/studies/{id}/assets` | 2 | 0 | 65.0 | 65.0 | 65.0 | 65 |
| `GET /v1/studies/{id}/fields` | 1 | 0 | 54.0 | 54.0 | 54.0 | 54 |
| `GET /v1/studies/{id}/rooms` | 1 | 0 | 62.0 | 62.0 | 62.0 | 62 |
| `POST /v1/studies` | 1 | 0 | 61.0 | 61.0 | 61.0 | 61 |
| `POST /v1/studies/{id}/start` | 1 | 0 | 44.0 | 44.0 | 44.0 | 44 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 1 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 766 | 149.2 | 0.19 | 1.5 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 810 | 105.9 | 0.13 | 0.6 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 1191 | 73.8 | 0.06 | 1.4 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 8804 | 53.0 | 0.01 | 0.2 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 930 | 44.7 | 0.05 | 0.7 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |
| 1092 | 39.6 | 0.04 | 0.5 | `INSERT INTO citations (study_id, asset_id, chunk_id, excerpt, verified_at, verification_method) VALUES ($1::UU` |
| 930 | 36.6 | 0.04 | 0.6 | `INSERT INTO takeoffs (study_id, asset_id, quantity, unit, formula, inputs) VALUES ($1::UUID, $2::UUID, $3::NUM` |
| 1092 | 35.3 | 0.03 | 0.3 | `UPDATE citations SET verified_at=$1::TIMESTAMP WITH TIME ZONE WHERE citations.id = $2::UUID` |

