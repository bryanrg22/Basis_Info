# Load tests

The method, the metrics, the proposed SLOs and every command are in
[docs/perf/LOAD_TESTING.md](../../docs/perf/LOAD_TESTING.md); the first capacity report is
EVAL_REPORT section 31.

| File | What it is |
|---|---|
| `locustfile.py` | the Locust users: `StudyEngineer` (whole studies), `ReviewReader` (reads) |
| `plan.py` | the synthetic study (52 photos, 33 rooms) and shared helpers (stdlib only) |
| `compose.load.yml`, `stack.sh` | the local stack: Postgres + Temporal in Docker, API / worker / relay on the host |
| `run_step.sh` | one step: N concurrent studies, sampled and reported |
| `run_remote.sh` | one step against a deployed stack (staging) |
| `sampler.py` | Temporal backlog, worker slots and schedule-to-start, Postgres, CPU/RSS every 5 s |
| `report.py` | the summary of a run directory |
| `mint_tokens.py` | Firebase ID tokens for the `load-eng-<n>` users on a deployed stack (creates them) |
| `provider_limits.py`, `ceilings.py` | our accounts' rate limits and the studies/hour each provider allows |

The fake-provider worker and its latency profile are in `backEnd/tests/load/`.
Run directories are not committed (`runs/` is ignored); summaries that back a report go
under `docs/perf/runs/`.
