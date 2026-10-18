# Load run `r2-u200` (scale 1.0)

- studies 0: completed 0, failed 0
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: None, 95: None, 100: None}; 429s seen 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 0.0 / 100; workflow slots 40.0
- activity schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- workflow task schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- activity execution mean s (real = / scale) {}
- Postgres connections max 7 {'basis': 7}; idle in transaction max 4; waiting on locks max 0; oldest transaction 0.1 s
- processes {"api": {"cpu_p50": 58.2, "cpu_p95": 70.7, "cpu_max": 71.1, "rss_max_mb": 217.4}, "worker": {"cpu_p50": 124.9, "cpu_p95": 129.0, "cpu_max": 129.0, "rss_max_mb": 504.7}, "relay": {"cpu_p50": 0.1, "cpu_p95": 0.1, "cpu_max": 0.1, "rss_max_mb": 113.4}, "postgres": {"cpu_p50": 36.11, "cpu_p95": 58.14, "cpu_max": 58.14}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 13459, "failures": 9, "rps": 75.27423101341859, "p95": 2600.0, "p99": 4600.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `GET /v1/studies/{id}/assets` | 5934 | 3 | 25.0 | 110.0 | 310.0 | 1136 |
| `GET /v1/studies/{id}/workflow` | 3186 | 3 | 1700.0 | 4400.0 | 5100.0 | 5155 |
| `GET /v1/studies/{id}/fields` | 1336 | 0 | 38.0 | 160.0 | 810.0 | 1695 |
| `GET /v1/studies/{id}` | 932 | 2 | 13.0 | 110.0 | 790.0 | 1128 |
| `GET /v1/studies/{id}/rooms` | 667 | 0 | 35.0 | 120.0 | 580.0 | 1138 |
| `GET /v1/studies/{id}/evidence` | 665 | 1 | 41.0 | 150.0 | 780.0 | 1233 |
| `GET /v1/studies` | 518 | 0 | 43.0 | 930.0 | 1400.0 | 1691 |
| `GET /v1/studies/{id}/photos/{id}/objects` | 221 | 0 | 15.0 | 86.0 | 140.0 | 315 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', ConnectionResetError(54, 'Connection reset by | `GET /v1/studies/{id}/workflow` | 3 |
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', ConnectionResetError(54, 'Connection reset by | `GET /v1/studies/{id}/evidence` | 1 |
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', ConnectionResetError(54, 'Connection reset by | `GET /v1/studies/{id}` | 2 |
| CatchResponseError("0 ConnectionError(ProtocolError('Connection aborted.', ConnectionResetError(54, 'Connection reset by | `GET /v1/studies/{id}/assets` | 3 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 4974 | 30606.6 | 6.15 | 80.5 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 678 | 14721.0 | 21.71 | 165.7 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 829 | 5063.4 | 6.11 | 28.4 | `SELECT citations.asset_id, count(*) AS count_1, count(citations.verified_at) AS count_2 FROM citations WHERE c` |
| 555 | 3761.0 | 6.78 | 87.6 | `SELECT citations.id, citations.study_id, citations.asset_id, citations.chunk_id, citations.excerpt, citations.` |
| 656 | 2321.9 | 3.54 | 22.2 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 664 | 2004.2 | 3.02 | 15.0 | `SELECT extraction_fields.id, extraction_fields.extraction_id, extraction_fields.study_id, extraction_fields.se` |
| 5529 | 1574.8 | 0.28 | 6.0 | `SELECT components.id, components.study_id, components.revision, components.document_id, components.room_id, co` |
| 4974 | 849.8 | 0.17 | 1.8 | `SELECT takeoffs.id, takeoffs.study_id, takeoffs.asset_id, takeoffs.quantity, takeoffs.unit, takeoffs.formula, ` |

