# Load run `r2-u100` (scale 1.0)

- studies 0: completed 0, failed 0
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: None, 95: None, 100: None}; 429s seen 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 0.0 / 100; workflow slots 6.0
- activity schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- workflow task schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- activity execution mean s (real = / scale) {}
- Postgres connections max 7 {'basis': 7}; idle in transaction max 3; waiting on locks max 0; oldest transaction 0.0 s
- processes {"api": {"cpu_p50": 31.3, "cpu_p95": 38.9, "cpu_max": 46.0, "rss_max_mb": 189.5}, "worker": {"cpu_p50": 82.4, "cpu_p95": 103.9, "cpu_max": 106.6, "rss_max_mb": 452.5}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.1, "cpu_max": 0.1, "rss_max_mb": 113.8}, "postgres": {"cpu_p50": 20.76, "cpu_p95": 52.13, "cpu_max": 52.13}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 7783, "failures": 2, "rps": 43.38798615029213, "p95": 370.0, "p99": 700.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/assets` | 3309 | 0 | 20.0 | 67.0 | 150.0 | 607 |
| `GET /v1/studies/{id}/workflow` | 1947 | 0 | 200.0 | 640.0 | 1600.0 | 1743 |
| `GET /v1/studies/{id}/fields` | 726 | 2 | 34.0 | 79.0 | 130.0 | 438 |
| `GET /v1/studies/{id}` | 572 | 0 | 10.0 | 62.0 | 410.0 | 604 |
| `GET /v1/studies/{id}/evidence` | 397 | 0 | 30.0 | 87.0 | 280.0 | 609 |
| `GET /v1/studies/{id}/rooms` | 374 | 0 | 23.0 | 87.0 | 410.0 | 573 |
| `GET /v1/studies` | 287 | 0 | 12.0 | 410.0 | 640.0 | 795 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 171 | 0 | 10.0 | 45.0 | 150.0 | 185 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', ConnectionResetError(54, 'Connection reset by | `GET /v1/studies/{id}/fields` | 2 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 2754 | 17109.9 | 6.21 | 194.8 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 366 | 8253.9 | 22.55 | 140.8 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 459 | 2738.6 | 5.97 | 23.1 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 336 | 2202.5 | 6.56 | 27.2 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 363 | 1610.6 | 4.44 | 24.9 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 358 | 1171.1 | 3.27 | 13.5 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 3090 | 1073.3 | 0.35 | 19.4 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 2754 | 516.5 | 0.19 | 8.6 | `SELECT takeoffs.id, takeoffs.study_id, takeoffs.asset_id, takeoffs.quantity, takeoffs.unit, takeoffs.formula, ` |

