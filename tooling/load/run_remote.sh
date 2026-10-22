#!/usr/bin/env bash
# A load step against a deployed stack (staging): Locust only, no local processes.
#
#   API_URL=https://<staging api> TOKEN_FILE=tokens.json \
#     tooling/load/run_remote.sh RUN_DIR STUDIES [SCALE]
#
# TOKEN_FILE: JSON {"load-eng-0": "<Firebase ID token>", ...}, one test user per concurrent
# study (the API refuses emulator tokens outside dev and test). Tokens live an hour: mint
# them right before the step.
# Firms are invite-only: once per deployed database, an operator with database access
# (Cloud SQL proxy) gives the same uids a firm, or every study request is 403 no-firm:
#   cd backEnd && BASIS_ENV=staging DATABASE_URL=postgresql+asyncpg://... \
#     .venv/bin/python ../tooling/load/provision.py --users 25
# This script has no database access and does not do it. SCALE must equal the staging worker's LOAD_LATENCY_SCALE
# when it runs the fake-provider worker (1.0 for the production worker).
# Optional sampling (Temporal backlog, Postgres) when reachable from here:
#   TEMPORAL_ADDRESS (+ TEMPORAL_API_KEY via the sampler's env), PG_DSN (Cloud SQL proxy).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../../backEnd" && pwd)"
PY="$BACKEND/.venv/bin/python"
run_dir="${1:?RUN_DIR}"
studies="${2:?STUDIES}"
scale="${3:-1.0}"
: "${API_URL:?set API_URL}"
: "${TOKEN_FILE:?set TOKEN_FILE}"
mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd)"

sampler=""
if [ -n "${PG_DSN:-}" ] && [ -n "${TEMPORAL_ADDRESS:-}" ]; then
  "$PY" "$HERE/sampler.py" "$run_dir" --pg "$PG_DSN" --temporal "$TEMPORAL_ADDRESS" \
    --metrics-url "${WORKER_METRICS_URL:-http://127.0.0.1:1/metrics}" --postgres-container "" \
    >"$run_dir/sampler.log" 2>&1 &
  sampler=$!
fi

# shellcheck disable=SC2086
(cd "$BACKEND" && "$PY" -m locust -f "$HERE/locustfile.py" --headless StudyEngineer \
  --host "$API_URL" -u "$studies" -r "$studies" --target-studies "$studies" \
  --time-scale "$scale" --token-file "$TOKEN_FILE" \
  --run-dir "$run_dir" --csv "$run_dir/locust" --only-summary -t "${RUN_LIMIT:-4h}" \
  ${LOCUST_ARGS:-}) >"$run_dir/locust.log" 2>&1 || true

if [ -n "$sampler" ]; then kill -TERM "$sampler" 2>/dev/null || true; wait "$sampler" || true; fi
"$PY" "$HERE/report.py" "$run_dir" --scale "$scale" --pg "${PG_DSN:-}" | tee "$run_dir/summary.md"
