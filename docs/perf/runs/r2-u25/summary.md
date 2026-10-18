# Load run `r2-u25` (scale 1.0)

- studies 0: completed 0, failed 0
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: None, 95: None, 100: None}; 429s seen 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 0.0 / 100; workflow slots 1.0
- activity schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- workflow task schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- activity execution mean s (real = / scale) {}
- Postgres connections max 7 {'': 1, 'basis': 7}; idle in transaction max 1; waiting on locks max 0; oldest transaction 0.4 s
- processes {"api": {"cpu_p50": 9.6, "cpu_p95": 13.9, "cpu_max": 14.1, "rss_max_mb": 189.9}, "worker": {"cpu_p50": 21.1, "cpu_p95": 33.7, "cpu_max": 37.5, "rss_max_mb": 417.5}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.2, "cpu_max": 0.2, "rss_max_mb": 113.6}, "postgres": {"cpu_p50": 8.73, "cpu_p95": 42.94, "cpu_max": 42.94}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 2036, "failures": 0, "rps": 11.380089757581864, "p95": 170.0, "p99": 250.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/assets` | 901 | 0 | 20.0 | 49.0 | 77.0 | 203 |
| `GET /v1/studies/{id}/workflow` | 506 | 0 | 130.0 | 240.0 | 540.0 | 807 |
| `GET /v1/studies/{id}/fields` | 192 | 0 | 35.0 | 89.0 | 170.0 | 225 |
| `GET /v1/studies/{id}` | 134 | 0 | 14.0 | 63.0 | 140.0 | 171 |
| `GET /v1/studies/{id}/rooms` | 98 | 0 | 31.0 | 77.0 | 210.0 | 207 |
| `GET /v1/studies/{id}/evidence` | 92 | 0 | 40.0 | 97.0 | 160.0 | 160 |
| `GET /v1/studies` | 79 | 0 | 17.0 | 120.0 | 140.0 | 144 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 34 | 0 | 13.0 | 42.0 | 81.0 | 81 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 756 | 5306.7 | 7.02 | 20.5 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 96 | 3308.1 | 34.46 | 66.5 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 97 | 743.8 | 7.67 | 24.7 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 126 | 698.9 | 5.55 | 7.7 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 78 | 669.1 | 8.58 | 28.7 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 834 | 459.0 | 0.55 | 6.9 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 96 | 368.8 | 3.84 | 7.9 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 756 | 190.6 | 0.25 | 2.7 | `SELECT takeoffs.id, takeoffs.study_id, takeoffs.asset_id, takeoffs.quantity, takeoffs.unit, takeoffs.formula, ` |

