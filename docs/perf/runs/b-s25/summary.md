# Load run `b-s25` (scale 0.1)

- studies 25: completed 25, failed 0
- makespan 161.6 s (real-equivalent 1616 s); studies/hour (real) 55.71
- study run time real p50/p95/max {50: 1586.0, 95: 1607.9, 100: 1608.8}
- to appraisal gate {50: 183.8, 95: 290.4, 100: 321.5}, rooms gate {50: 645.6, 95: 664.5, 100: 666.3}, assets gate {50: 937.0, 95: 979.1, 100: 981.9}
- uploads (wall s) p50/p95/max {50: 65.61, 95: 65.86, 100: 65.86}; 429s seen 50
- cost per study (recorded) mean $6.07; calls mean 523; failed activities 0
- Temporal backlog max: activity 123 (age 7.947729 s), workflow 0
- activity slots used max 100.0 / 100; workflow slots 8.0
- activity schedule-to-start {'count': 2912.0, 'mean_ms': 1728.2, 'share_le_ms': {'100': 0.3977, '500': 0.4684, '1000': 0.5594, '5000': 0.8616, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- workflow task schedule-to-start {'count': 2892.0, 'mean_ms': 16.8, 'share_le_ms': {'100': 1.0, '500': 1.0, '1000': 1.0, '5000': 1.0, '10000': 1.0, '100000': 1.0, '1000000': 1.0, '+Inf': 1.0}}
- activity execution mean s (real = / scale) {'analyze_photo': 2.17, 'export_report': 0.1, 'extract_fields': 10.97, 'ingest_appraisal': 1.03, 'list_photos': 0.75, 'localize_photo': 7.55, 'process_assets': 26.59, 'reconcile_rooms': 6.71, 'record_run_end': 0.01, 'record_run_start': 0.29, 'transition': 0.31}
- Postgres connections max 11 {'basis': 11}; idle in transaction max 7; waiting on locks max 0; oldest transaction 1.0 s
- processes {"api": {"cpu_p50": 14.6, "cpu_p95": 49.9, "cpu_max": 94.8, "rss_max_mb": 195.9}, "worker": {"cpu_p50": 29.4, "cpu_p95": 102.4, "cpu_max": 104.3, "rss_max_mb": 519.2}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.3, "cpu_max": 0.4, "rss_max_mb": 120.0}, "postgres": {"cpu_p50": 14.97, "cpu_p95": 44.01, "cpu_max": 44.01}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 50, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 11900, "failures": 50, "rps": 52.653706102168286, "p95": 140.0, "p99": 14000.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/workflow` | 7333 | 0 | 23.0 | 90.0 | 200.0 | 498 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 1371 | 46 | 100.0 | 160.0 | 190.0 | 274 |
| `POST /v1/studies/{id}/documents` | 1329 | 4 | 87.0 | 150.0 | 220.0 | 302 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 1300 | 0 | 9.0 | 20.0 | 37.0 | 65 |
| `GET /v1/studies/{id}` | 121 | 0 | 9.0 | 25.0 | 45.0 | 54 |
| `GET /v1/studies/{id}/evidence` | 100 | 0 | 41.0 | 91.0 | 110.0 | 107 |
| `POST /v1/studies/{id}/decisions` | 75 | 0 | 18.0 | 45.0 | 130.0 | 135 |
| `GET /v1/studies/{id}/assets` | 50 | 0 | 33.0 | 93.0 | 95.0 | 95 |
| `GET /v1/studies/{id}/fields` | 25 | 0 | 60.0 | 100.0 | 150.0 | 149 |
| `GET /v1/studies/{id}/rooms` | 25 | 0 | 22.0 | 60.0 | 63.0 | 63 |
| `POST /v1/studies` | 25 | 0 | 150.0 | 170.0 | 180.0 | 184 |
| `POST /v1/studies/{id}/start` | 25 | 0 | 75.0 | 110.0 | 110.0 | 106 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 46 |
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents` | 4 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 19150 | 1458.5 | 0.08 | 6.4 | `INSERT INTO photo_objects (study_id, document_id, revision, label, phrase, score, x1, y1, x2, y2, mask, area, ` |
| 20250 | 947.9 | 0.05 | 1.9 | `INSERT INTO components (study_id, revision, document_id, room_id, label, material, attachment, confidence, bbo` |
| 25 | 706.0 | 28.24 | 59.8 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 107950 | 668.7 | 0.01 | 4.9 | `SELECT $2 FROM ONLY "public"."studies" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |
| 13075 | 510.0 | 0.04 | 5.0 | `INSERT INTO llm_calls (study_id, run_id, activity, provider, model, prompt_version, input_tokens, output_token` |
| 1300 | 381.3 | 0.29 | 113.9 | `SELECT pg_advisory_xact_lock(hashtextextended($1, $2))` |
| 1300 | 288.3 | 0.22 | 0.7 | `SELECT components.label FROM components WHERE components.study_id = $1::UUID AND components.document_id = $2::` |
| 7750 | 283.4 | 0.04 | 0.6 | `INSERT INTO assets (study_id, revision, component_id, category, bucket, recovery_period, irs_section, method, ` |

