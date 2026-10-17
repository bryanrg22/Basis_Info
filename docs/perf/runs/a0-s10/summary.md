# Load run `a0-s10` (scale 0.1)

- studies 10: completed 0, failed 10 (RuntimeError: create: 503)
- makespan None s (real-equivalent None s); studies/hour (real) None
- study run time real p50/p95/max {50: None, 95: None, 100: None}
- to appraisal gate {50: None, 95: None, 100: None}, rooms gate {50: None, 95: None, 100: None}, assets gate {50: None, 95: None, 100: None}
- uploads (wall s) p50/p95/max {50: None, 95: None, 100: None}; 429s seen 0
- Temporal backlog max: activity 0 (age 0.0 s), workflow 0
- activity slots used max 0.0 / 100; workflow slots 0.0
- activity schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- workflow task schedule-to-start {'count': 0, 'mean_ms': None, 'share_le_ms': {}}
- activity execution mean s (real = / scale) {}
- Postgres connections max 7 {'basis': 7}; idle in transaction max 5; waiting on locks max 0; oldest transaction 5.8 s
- processes {"api": {"cpu_p50": 0.2, "cpu_p95": 1.3, "cpu_max": 1.3, "rss_max_mb": 165.0}, "worker": {"cpu_p50": 0.0, "cpu_p95": 1.0, "cpu_max": 1.0, "rss_max_mb": 376.7}, "relay": {"cpu_p50": 0.0, "cpu_p95": 0.3, "cpu_max": 0.3, "rss_max_mb": 113.5}, "postgres": {"cpu_p50": 7.12, "cpu_p95": 7.12, "cpu_max": 7.12}}
- logs {"worker": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "api": {"pool_timeout": 15, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}, "relay": {"pool_timeout": 0, "traceback": 0, "error_lines": 0, "api_429": 0, "provider_429": 0, "provider_5xx": 0, "pool_fallback": 0, "activity_failed": 0}}

| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|

Top statements by total time:

| calls | total ms | mean ms | max ms | query |
|---|---|---|---|---|
| 10 | 1.0 | 0.1 | 0.2 | `INSERT INTO idempotency_keys (key, user_id, route, request_hash, expires_at) VALUES ($1::VARCHAR, $2::UUID, $3` |
| 5 | 0.9 | 0.17 | 0.2 | `INSERT INTO org_members (org_id, user_id, role) VALUES ($1::UUID, $2::UUID, $3::VARCHAR) RETURNING org_members` |
| 5 | 0.6 | 0.12 | 0.2 | `INSERT INTO studies (org_id, created_by, property_name, workflow_id, room_aliases, failure_reason) VALUES ($1:` |
| 5 | 0.3 | 0.07 | 0.1 | `INSERT INTO users (firebase_uid, email, display_name, last_seen_at) VALUES ($1::VARCHAR, $2::VARCHAR, $3::VARC` |
| 5 | 0.3 | 0.05 | 0.1 | `DELETE FROM idempotency_keys WHERE idempotency_keys.key = $1::VARCHAR AND idempotency_keys.status_code IS NULL` |
| 8 | 0.3 | 0.03 | 0.1 | `SELECT outbox.id, outbox.kind, outbox.payload, outbox.status, outbox.attempts, outbox.next_attempt_at, outbox.` |
| 2 | 0.3 | 0.13 | 0.1 | `select coalesce(application_name, $1) as app, coalesce(state, $2) as state, count(*) as n, count(*) filter (wh` |
| 15 | 0.2 | 0.01 | 0.1 | `SELECT users.id FROM users WHERE users.firebase_uid = $1::VARCHAR` |

