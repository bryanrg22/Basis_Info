#!/usr/bin/env bash
# One load step (docs/perf/LOAD_TESTING.md section 5): N concurrent studies on a fresh set of
# host processes, sampled, then reported.
#
#   tooling/load/run_step.sh RUN_DIR STUDIES [SCALE] [fake|real]
#
# SCALE (default 1.0) is LOAD_LATENCY_SCALE for the fake worker and --time-scale for the
# clients. Worker and API settings come from the caller's environment (LLM_CONCURRENCY=32
# DB_POOL_SIZE=20 ... tooling/load/run_step.sh ...). Extra Locust arguments: LOCUST_ARGS.
# Against another stack (staging) use run_remote.sh instead: this script starts processes.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../../backEnd" && pwd)"
PY="$BACKEND/.venv/bin/python"
run_dir="${1:?RUN_DIR}"
studies="${2:?STUDIES}"
scale="${3:-1.0}"
mode="${4:-fake}"
mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd)"
export LOAD_LATENCY_SCALE="$scale"
API_PORT="${API_PORT:-8161}"

"$HERE/stack.sh" up "$run_dir" "$mode"
"$PY" "$HERE/sampler.py" "$run_dir" --interval "${SAMPLE_SECONDS:-5}" >"$run_dir/sampler.log" 2>&1 &
sampler=$!
trap 'kill -TERM $sampler 2>/dev/null || true; "$HERE/stack.sh" down "$run_dir"' EXIT

# shellcheck disable=SC2086
(cd "$BACKEND" && "$PY" -m locust -f "$HERE/locustfile.py" --headless StudyEngineer \
  --host "http://127.0.0.1:$API_PORT" -u "$studies" -r "$studies" \
  --target-studies "$studies" --time-scale "$scale" \
  --firebase-project "${FIREBASE_PROJECT_ID:-basis-load}" \
  --run-dir "$run_dir" --csv "$run_dir/locust" --only-summary \
  -t "${RUN_LIMIT:-4h}" ${LOCUST_ARGS:-}) >"$run_dir/locust.log" 2>&1 || true

kill -TERM "$sampler" 2>/dev/null || true
wait "$sampler" 2>/dev/null || true
trap - EXIT
"$HERE/stack.sh" down "$run_dir"
"$PY" "$HERE/report.py" "$run_dir" --scale "$scale" | tee "$run_dir/summary.md"
