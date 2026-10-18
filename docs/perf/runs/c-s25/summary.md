# Load run `c-s25` (scale 0.1)

- studies 25: completed 25, failed 0
- makespan 1361.3 s (real-equivalent 13613 s); studies/hour (real) 6.61
- study run time real p50/p95/max {50: 13589.7, 95: 13602.0, 100: 13605.5}
- to appraisal gate {50: 1009.5, 95: 1545.0, 100: 1583.7}, rooms gate {50: 9280.5, 95: 9299.6, 100: 9309.8}, assets gate {50: 13531.7, 95: 13592.6, 100: 13592.8}
- uploads (wall s) p50/p95/max {50: 65.9, 95: 66.03, 100: 66.04}; 429s seen 50
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 102 (age 53.615248 s), workflow 0
- activity slots used max 100.0 / 100; workflow slots 4.0
- activity schedule-to-start {'count': 2963.0, 'mean_ms': 15053.1, 'share_le_ms': {'100': 0.1802, '500': 0.1887, '1000': 0.1974, '5000': 0.2545, '10000': 0.4016, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 3056.0, 'mean_ms': 1.5, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 62.01, 'export_report': 1.1, 'extract_fields': 69.07, 'ingest_appraisal': 0.65, 'list_photos': 0.66, 'localize_photo': 0.42, 'process_assets': 423.47, 'reconcile_rooms': 81.64, 'record_run_end': 0.43, 'record_run_start': 0.56, 'transition': 0.39}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 6; waiting on locks max 0; oldest transaction 1.7 s
- processes {"api": {"cpu_p50": 15.2, "cpu_p95": 17.4, "cpu_max": 94.2, "rss_max_mb": 176.5}, "worker": {"cpu_p50": 17.8, "cpu_p95": 25.4, "cpu_max": 108.4, "rss_max_mb": 537.2}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.2, "cpu_max": 0.4, "rss_max_mb": 115.5}, "postgres": {"cpu_p50": 6.06, "cpu_p95": 16.19, "cpu_max": 46.43}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 50, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 66910, "failures": 50, "rps": 46.922975761161716, "p95": 85.0, "p99": 140.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 62341 | 0 | 42.0 | 66.0 | 100.0 | 2154 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 1373 | 48 | 110.0 | 160.0 | 240.0 | 277 |
| `POST /v1/studies/{id}/documents` | 1327 | 2 | 87.0 | 140.0 | 240.0 | 261 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 1300 | 0 | 7.0 | 20.0 | 48.0 | 97 |
| `GET /v1/studies/{id}` | 122 | 0 | 12.0 | 57.0 | 81.0 | 84 |
| `GET /v1/studies/{id}/evidence` | 100 | 0 | 58.0 | 250.0 | 290.0 | 290 |
| `POST /v1/studies/{id}/decisions` | 75 | 0 | 15.0 | 110.0 | 150.0 | 152 |
| `GET /v1/studies/{id}/assets` | 50 | 0 | 46.0 | 220.0 | 270.0 | 268 |
| `GET /v1/studies/{id}/fields` | 25 | 0 | 48.0 | 67.0 | 76.0 | 76 |
| `GET /v1/studies/{id}/rooms` | 25 | 0 | 25.0 | 82.0 | 91.0 | 91 |
| `POST /v1/studies` | 25 | 0 | 150.0 | 180.0 | 180.0 | 181 |
| `POST /v1/studies/{id}/start` | 25 | 0 | 73.0 | 130.0 | 130.0 | 127 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 48 |
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents` | 2 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 19150 | 2538.4 | 0.13 | 1.5 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 66719 | 1852.2 | 0.03 | 0.5 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 20250 | 1772.8 | 0.09 | 1.0 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 13075 | 1151.8 | 0.09 | 3.2 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 62344 | 1067.0 | 0.02 | 0.5 | `SELECT runs.id, runs.study_id, runs.workflow_id, runs.run_id, runs.workflow_version, runs.input_revision, runs` |
| 107950 | 935.2 | 0.01 | 1.0 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 66744 | 801.9 | 0.01 | 0.5 | `SELECT users.id, users.firebase_uid, users.email, users.display_name, users.created_at, users.last_seen_at FRO` |
| 25 | 672.1 | 26.89 | 51.1 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |

