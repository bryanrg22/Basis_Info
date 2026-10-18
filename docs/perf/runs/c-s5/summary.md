# Load run `c-s5` (scale 0.1)

- studies 5: completed 5, failed 0
- makespan 274.6 s (real-equivalent 2746 s); studies/hour (real) 6.55
- study run time real p50/p95/max {50: 2744.7, 95: 2746.2, 100: 2746.2}
- to appraisal gate {50: 272.1, 95: 288.5, 100: 288.5}, rooms gate {50: 1867.2, 95: 1877.7, 100: 1877.7}, assets gate {50: 2721.4, 95: 2737.2, 100: 2737.2}
- uploads (wall s) p50/p95/max {50: 61.4, 95: 61.4, 100: 61.4}; 429s seen 5
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 32.0 / 100; workflow slots 0.0
- activity schedule-to-start {'count': 589.0, 'mean_ms': 0.3, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 602.0, 'mean_ms': 1.4, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 15.99, 'export_report': 0.09, 'extract_fields': 26.07, 'ingest_appraisal': 0.81, 'list_photos': 0.06, 'localize_photo': 0.43, 'process_assets': 83.75, 'reconcile_rooms': 14.0, 'record_run_end': 0.01, 'record_run_start': 0.03, 'transition': 0.01}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 3; waiting on locks max 0; oldest transaction 0.1 s
- processes {"api": {"cpu_p50": 4.4, "cpu_p95": 13.6, "cpu_max": 21.6, "rss_max_mb": 194.3}, "worker": {"cpu_p50": 14.3, "cpu_p95": 20.6, "cpu_max": 50.7, "rss_max_mb": 463.3}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.3, "rss_max_mb": 119.7}, "postgres": {"cpu_p50": 5.17, "cpu_p95": 11.94, "cpu_max": 23.57}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 5, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 2533 | 0 | 34.0 | 41.0 | 91.0 | 305 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 270 | 5 | 20.0 | 30.0 | 52.0 | 57 |
| `POST /v1/studies/{id}/documents` | 265 | 0 | 18.0 | 21.0 | 190.0 | 195 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 260 | 0 | 6.0 | 8.0 | 10.0 | 13 |
| `GET /v1/studies/{id}` | 22 | 0 | 10.0 | 19.0 | 19.0 | 19 |
| `GET /v1/studies/{id}/evidence` | 13 | 0 | 23.0 | 64.0 | 64.0 | 64 |
| `POST /v1/studies/{id}/decisions` | 13 | 0 | 15.0 | 56.0 | 56.0 | 56 |
| `GET /v1/studies/{id}/assets` | 10 | 0 | 30.0 | 75.0 | 75.0 | 75 |
| `GET /v1/studies/{id}/fields` | 5 | 0 | 65.0 | 100.0 | 100.0 | 103 |
| `GET /v1/studies/{id}/rooms` | 5 | 0 | 30.0 | 31.0 | 31.0 | 31 |
| `POST /v1/studies` | 5 | 0 | 75.0 | 77.0 | 77.0 | 77 |
| `POST /v1/studies/{id}/start` | 5 | 0 | 23.0 | 35.0 | 35.0 | 35 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 5 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 3830 | 617.5 | 0.16 | 2.6 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 4050 | 426.7 | 0.11 | 1.0 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 2615 | 273.5 | 0.1 | 0.9 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 5 | 180.5 | 36.09 | 76.8 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 21590 | 143.8 | 0.01 | 0.8 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 3412 | 127.1 | 0.04 | 0.4 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 595 | 123.9 | 0.21 | 0.8 | `INSERT INTO activity_results (run_id, activity, idempotency_key, status) VALUES ($1::UUID, $2::VARCHAR, $3::VA` |
| 160 | 72.1 | 0.45 | 1.3 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |

