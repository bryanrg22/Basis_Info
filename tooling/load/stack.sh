#!/usr/bin/env bash
# The local load-test stack's host processes (docs/perf/LOAD_TESTING.md section 5).
#
#   tooling/load/stack.sh up RUN_DIR [fake|real]   # API, worker, relay; pids + logs in RUN_DIR
#   tooling/load/stack.sh down RUN_DIR
#
# Needs compose.load.yml up (Postgres on 5561, Temporal on 7261) and backEnd/.venv
# (uv sync --group dev --group load). Settings come from this environment, never from a
# .env file (BASIS_ENV=dev reads backEnd/.env only when it exists; a worktree has none).
# Any setting can be overridden from the caller's environment: LLM_CONCURRENCY=32
# DB_POOL_SIZE=20 LOAD_LATENCY_SCALE=0.1 tooling/load/stack.sh up RUN_DIR.
# "real" runs the production worker composition (real providers, real spend): the keys
# must be in the caller's environment; the probe in LOAD_TESTING.md section 6 caps spend
# with STUDY_BUDGET_USD.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../../backEnd" && pwd)"
PY="$BACKEND/.venv/bin/python"
cmd="${1:?up|down}"
run_dir="${2:?RUN_DIR}"
mode="${3:-fake}"
mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd)"

export BASIS_ENV="${BASIS_ENV:-dev}"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://basis:basis@127.0.0.1:5561/basis}"  # pragma: allowlist secret
export TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-127.0.0.1:7261}"
export FIREBASE_PROJECT_ID="${FIREBASE_PROJECT_ID:-basis-load}"
export FIREBASE_AUTH_EMULATOR_HOST="${FIREBASE_AUTH_EMULATOR_HOST:-127.0.0.1:9099}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
API_PORT="${API_PORT:-8161}"
METRICS_PORT="${METRICS_PORT:-9471}"
HEALTH_PORT="${HEALTH_PORT:-8171}"

start() {  # name, command...
  local name="$1"; shift
  (cd "$BACKEND" && exec "$@" >"$run_dir/$name.log" 2>&1) &
  echo "$!" >"$run_dir/$name.pid"
}

case "$cmd" in
  up)
    (cd "$BACKEND" && "$PY" tests/load/setup_db.py) | tee "$run_dir/setup.log"
    env | grep -E '^(LLM_CONCURRENCY|CLASSIFY_CONCURRENCY|PHOTO_CONCURRENCY|LOCALIZER_CONCURRENCY|DB_POOL_SIZE|LOAD_LATENCY_SCALE|STUDY_BUDGET_USD|RATE_LIMIT_[A-Z_]+|VISION_[A-Z_]+|LLM_PRIMARY)=' \
      >"$run_dir/settings.env" || true
    echo "MODE=$mode" >>"$run_dir/settings.env"
    start api "$PY" -m uvicorn basis.api.main:app --host 127.0.0.1 --port "$API_PORT"
    if [ "$mode" = "real" ]; then
      start worker "$PY" tests/load/fake_worker.py --providers real \
        --health-port "$HEALTH_PORT" --metrics-port "$METRICS_PORT"
    else
      start worker "$PY" tests/load/fake_worker.py --providers fake \
        --health-port "$HEALTH_PORT" --metrics-port "$METRICS_PORT"
    fi
    start relay "$PY" -m basis.relay
    for _ in $(seq 1 60); do
      if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 \
        && curl -fsS "http://127.0.0.1:$HEALTH_PORT/health" >/dev/null 2>&1; then
        echo "stack up: api :$API_PORT, worker health :$HEALTH_PORT metrics :$METRICS_PORT ($mode)"
        exit 0
      fi
      sleep 1
    done
    echo "stack did not come up; see $run_dir/*.log" >&2
    exit 1
    ;;
  down)
    for name in api worker relay; do
      if [ -f "$run_dir/$name.pid" ]; then
        kill -TERM "$(cat "$run_dir/$name.pid")" 2>/dev/null || true
      fi
    done
    sleep 10  # the worker drains for up to 8 s
    for name in api worker relay; do
      if [ -f "$run_dir/$name.pid" ]; then
        kill -KILL "$(cat "$run_dir/$name.pid")" 2>/dev/null || true
        rm -f "$run_dir/$name.pid"
      fi
    done
    ;;
  *)
    echo "usage: $0 up|down RUN_DIR [fake|real]" >&2
    exit 2
    ;;
esac
