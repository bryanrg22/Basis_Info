# Load run `u60-s5` (scale 1.0)

- studies 5: completed 5, failed 0
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: 61.49, 95: 61.5, 100: 61.5}; 429s seen 5
- cost per study (recorded) mean $0.00; calls mean 0; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 0.0 / 100; workflow slots 0.0
- activity schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- workflow task schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- activity execution mean s (real = / scale) {}
- Postgres connections max 7 {'basis': 7}; idle in transaction max 0; waiting on locks max 0; oldest transaction 0 s
- processes {"api": {"cpu_p50": 0.2, "cpu_p95": 20.0, "cpu_max": 20.0, "rss_max_mb": 164.5}, "worker": {"cpu_p50": 0.0, "cpu_p95": 0.9, "cpu_max": 0.9, "rss_max_mb": 377.6}, "relay": {"cpu_p50": 0.2, "cpu_p95": 0.3, "cpu_max": 0.3, "rss_max_mb": 113.5}, "postgres": {"cpu_p50": 0.22, "cpu_p95": 23.66, "cpu_max": 23.66}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 5, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 480, "failures": 5, "rps": 7.8357614044406825, "p95": 31.0, "p99": 81.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `POST /v1/studies/{id}/documents/{id}/complete` | 240 | 5 | 21.0 | 24.0 | 34.0 | 38 |
| `POST /v1/studies/{id}/documents` | 235 | 0 | 19.0 | 22.0 | 210.0 | 210 |
| `POST /v1/studies` | 5 | 0 | 73.0 | 81.0 | 81.0 | 81 |

| Failure | Route | Count |
|---|---|---|
| CatchResponseError('429 rate limited') | `POST /v1/studies/{id}/documents/{id}/complete` | 5 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 535 | 30.9 | 0.06 | 0.5 | `INSERT INTO idempotency_keys (key, user_id, route, request_hash, expires_at) VALUES ($1::VARCHAR, $2::UUID, $3` |
| 265 | 25.1 | 0.09 | 0.7 | `INSERT INTO documents (id, study_id, kind, filename, content_type, bytes, sha256, gcs_uri, uploaded_by, upload` |
| 535 | 21.2 | 0.04 | 0.1 | `UPDATE idempotency_keys SET status_code=$1::INTEGER, response=$2::JSONB, expires_at=(now() + $3::INTERVAL) WHE` |
| 265 | 9.5 | 0.04 | 0.1 | `UPDATE documents SET uploaded_at=now() WHERE documents.id = $1::UUID AND documents.study_id = $2::UUID AND doc` |
| 530 | 6.2 | 0.01 | 0.1 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 1070 | 5.7 | 0.01 | 0.0 | `SELECT users.id FROM users WHERE users.firebase_uid = $1::VARCHAR` |
| 535 | 3.6 | 0.01 | 0.1 | `SELECT users.id, users.firebase_uid, users.email, users.display_name, users.created_at, users.last_seen_at FRO` |
| 805 | 3.1 | 0.0 | 0.0 | `SELECT $2 FROM ONLY "public"."users" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |

