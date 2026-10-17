# Load run `u300-s5` (scale 1.0)

- studies 5: completed 5, failed 0
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: 2.87, 95: 2.89, 100: 2.89}; 429s seen 0
- cost per study (recorded) mean $0.00; calls mean 0; failed activities 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 0.0 / 100; workflow slots 0.0
- activity schedule-to-start None
- workflow task schedule-to-start None
- activity execution mean s (real = / scale) None
- Postgres connections max 3 {'basis': 3}; idle in transaction max 0; waiting on locks max 0; oldest transaction 0 s
- processes {"api": {"cpu_p50": 0.0, "cpu_p95": 0.0, "cpu_max": 0.0, "rss_max_mb": 159.3}, "worker": {"cpu_p50": 1.4, "cpu_p95": 1.4, "cpu_max": 1.4, "rss_max_mb": 383.2}, "relay": {"cpu_p50": 0.0, "cpu_p95": 0.0, "cpu_max": 0.0, "rss_max_mb": 113.9}, "postgres": {"cpu_p50": 21.36, "cpu_p95": 21.36, "cpu_max": 21.36}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}
- all requests {"count": 341, "failures": 0, "rps": 170.74853224500555, "p95": 61.0, "p99": 220.0}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| `POST /v1/studies/{id}/documents` | 170 | 0 | 22.0 | 61.0 | 220.0 | 216 |
| `POST /v1/studies/{id}/documents/{id}/complete` | 166 | 0 | 23.0 | 34.0 | 96.0 | 143 |
| `POST /v1/studies` | 5 | 0 | 82.0 | 92.0 | 92.0 | 92 |

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 535 | 31.1 | 0.06 | 0.4 | `INSERT INTO idempotency_keys (key, user_id, route, request_hash, expires_at) VALUES ($1::VARCHAR, $2::UUID, $3` |
| 265 | 27.2 | 0.1 | 0.4 | `INSERT INTO documents (id, study_id, kind, filename, content_type, bytes, sha256, gcs_uri, uploaded_by, upload` |
| 535 | 27.0 | 0.05 | 0.5 | `UPDATE idempotency_keys SET status_code=$1::INTEGER, response=$2::JSONB, expires_at=(now() + $3::INTERVAL) WHE` |
| 265 | 11.8 | 0.04 | 0.2 | `UPDATE documents SET uploaded_at=now() WHERE documents.id = $1::UUID AND documents.study_id = $2::UUID AND doc` |
| 530 | 7.7 | 0.01 | 0.1 | `SELECT studies.id, studies.org_id, studies.created_by, studies.property_name, studies.status, studies.current_` |
| 1070 | 7.7 | 0.01 | 0.1 | `SELECT users.id FROM users WHERE users.firebase_uid = $1::VARCHAR` |
| 535 | 4.2 | 0.01 | 0.1 | `SELECT users.id, users.firebase_uid, users.email, users.display_name, users.created_at, users.last_seen_at FRO` |
| 805 | 3.6 | 0.0 | 0.0 | `SELECT $2 FROM ONLY "public"."users" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x` |

