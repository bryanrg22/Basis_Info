# Working in this repository

Instructions for every coding agent (Claude Code reads this through `CLAUDE.md`; Codex reads
it directly) and the short version of the rules for people. `README.md` is the full picture
of the system; `docs/DEVELOPING.md` is the full version of how we build it. When this file and
the code disagree, the code is right and this file gets fixed in the same change.

## What Basis is

An AI cost segregation platform for engineering firms. An engineer uploads an appraisal and
property photos; Basis reads the appraisal, groups the photos into rooms, counts the
components, builds the asset schedule (quantities, RSMeans costs, IRS recovery classes with
citations), reconciles it to the purchase price, and writes the client report and the
preparer's depreciation schedule. The engineer reviews at four gates (appraisal, rooms,
components, assets), corrects what is wrong, and signs the report in Basis.

The system is `backEnd/basis/` (FastAPI, Temporal, Postgres) and `web/` (static HTML/JS,
mock mode by default, live mode against `/v1`). Documents that describe Firestore, LangGraph
or a Next.js `frontend/` describe the legacy design, which no longer exists in the tree.

## Layout

```
backEnd/basis/        the backend package
  api/                FastAPI app, routes (/v1), schemas, auth, rate limits, problem details
  workflow/           the Temporal study workflow, its contracts, activity options (retries)
  activities/         one module per workflow step (extract, analyze photos, process assets, export...)
  db/                 SQLAlchemy models, Alembic migrations, repositories (no generic CRUD)
  domain/             rules table, intake, cost trail, takeoff formulas
  classify/           the classifier router, firm conventions (classification_cache)
  vision/             photo analysis (rooms, components, SAM 3 localization)
  extraction/         appraisal extraction (Azure Document Intelligence + model)
  evidence/, report/  the evidence pack and export; the client report (Typst), schedule, signing
  costs/, llm/        cost index store; provider pool (OpenAI, Azure OpenAI, Anthropic), paid-call ledger
  prompts/            versioned prompt files <name>/v<n>.md with a sha256 manifest (immutable once released)
  errors/             the BASIS-<AREA>-<nnn> code catalogue
  relay/, worker/     the outbox relay and the Temporal worker processes
backEnd/evals/        offline evaluations, replays, runners; every run leaves a record (no paid calls unless stated)
backEnd/tests/        unit, api, integration (Postgres), lifecycle (Temporal), node, failure, evals/behavior
web/                  the frontend; web/live/ is live mode; web/tests/*.test.cjs are node tests
infra/staging/        Terraform for GCP staging: parked, never apply without Bryan's approval in the session
docs/api/             the API contract: openapi.json plus one Markdown file per area
docs/review_2026-10-15/  design documents and EVAL_REPORT.md, the ledger of every measured result
docs/DEVELOPING.md    how we develop: setup, the method, recipes, definition of done
tooling/              the client-data guard, evidence capture, load tools
```

## Commands (from `backEnd/`, with `uv`; `--offline` once the environment is synced)

```bash
uv sync --locked --group dev                                      # once, and after a lockfile change
uv run uvicorn basis.api.main:app --reload --port 8000            # the API alone
docker compose up --build                                         # Postgres, Temporal, API, worker, relay (repo root)
uv run alembic upgrade head                                       # migrations (BASIS_ENV and DATABASE_URL set)
uv run pytest -m "not integration and not slow"                   # unit suite, about 80 s
uv run pytest -m "(integration or slow) and not eval_data"        # Postgres + Temporal suites, about 15 min
node --test web/tests/*.test.cjs                                  # web unit tests (repo root)
```

Run the test suites with the provider keys unset
(`env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u AZURE_OPENAI_API_KEY ...`): one worker test
asserts that no provider is configured.

## Gates: all green before a push, and CI runs them again

From `backEnd/`: `ruff check .`, `ruff format --check .`, `mypy basis`,
`python -m basis.errors.catalogue --check`, `python -m basis.api.openapi --check`,
`python -m evals.freeze_rule_fingerprints --check`, both pytest suites. From the repo root:
`python3 tooling/check_no_client_data.py --ci`. Regenerate with `--write` when codes or
routes change. `pre-commit install` once; the hooks run ruff, detect-secrets and the
client-data guard on every commit.

## How we build anything (the method; `docs/DEVELOPING.md` section 3 has the long form)

1. **Research the standard first.** Find how the established work does it (IRS guidance,
   RSMeans, the SDK's documentation, the published paper, the industry tool) before designing.
   Write what you found into the design document or the EVAL_REPORT section.
2. **Make no assumptions; verify empirically.** Read the code that runs, run it, look at the
   output. A surprising result is re-run before it is recorded.
3. **Measure alternatives side by side.** A labelled harness, a baseline, then the methods and
   models compared on the same data, with accuracy, cost, tokens and latency per candidate.
   The cheapest candidate within the noise of the best is the default, except where accuracy
   truly matters (component identification and counting).
4. **Test every path you can think of**, including the regression check that the existing path
   is unchanged. Races are made real (locks, barriers), never left to timing.
5. **Record every number.** Results go in `docs/review_2026-10-15/EVAL_REPORT.md` (next free
   section number; check open branches for `^## N\.` first), one row per test file in
   `backEnd/tests/LEDGER.md`, the run's files in the verification tree
   (`$BASIS_EVAL_DATA_DIR/verification/`, `evals.registry` lists them), and every paid call in
   `llm_calls` (model, prompt version, tokens, cost, latency).
6. **Be honest.** Verified is verified; suspected is suspected; "done" means tested and
   recorded. The PR body says what was not verified.

## Conventions that are easy to miss

- **Error codes** are `BASIS-<AREA>-<nnn>` in `basis/errors/codes.py`, described in
  `catalogue.py`; never write a code as a string literal elsewhere (a test forbids it).
- **New routes** go in `tests/api/test_health.py`'s known-routes list and in the OpenAPI
  document; the Schemathesis contract test must pass; one Markdown file per area in `docs/api/`.
- **Migrations** are hand-written Alembic files numbered in sequence; `alembic check` proves
  they match the models; data migrations are plain SQL (the chain renders offline);
  `tests/integration/db/test_migrations.py` pins the head. Check open PRs for the next number.
- **Workflow changes** are Temporal patches (`workflow.patched("<name>-v<n>")`) so recorded
  histories replay; `tests/lifecycle/histories/` holds them.
- **No CPU work on the event loop.** Async activities share the worker's loop with Temporal's
  workflow activations, which the SDK times at 2 s (`TMPRL1101`); decode, resize, crop, render
  or parse on a thread (`asyncio.to_thread`, `prepare_image_in_thread`).
  `tests/unit/test_event_loop_image_work.py` refuses the known image functions in `async def`.
- **Prompts** are immutable once released: add `v<n+1>.md` and its manifest entry; pin the
  pipeline to a version in `config.py` with the EVAL_REPORT section that chose it.
- **Settings** are documented in `config.py`'s header comment and `.env.example`, each with
  the section that measured its default; a new behaviour ships behind a setting.
- **Firm conventions** (`classification_cache`) are a firm's rulings; any change to how they
  match is replayed with `evals.conventions_replay` before it ships.
- **Client data never enters the repository.** Private files live in `~/basis-eval-data`
  (`BASIS_EVAL_DATA_DIR`); reports that quote client figures are written there, not in `docs/`.
  The guard blocks known files by hash, data extensions and large files.
- **No provider names in anything a customer sees.** Outages read as Basis being
  unavailable; provider names appear only on the operator ops page.
- **Nothing paid without approval.** A live study costs about $2 to $4 with conventions,
  $7 to $8 without; evals and tests use scripted models unless a section says otherwise.
  Keep the machine awake during a live run (`caffeinate -w <pid>`).
- **Nothing deployed.** Staging Terraform is parked (about $15 a month parked, $116 running);
  no `terraform apply`, no cloud change, without Bryan's words in the session.
- **Git:** one branch per change (`<owner>/<topic>`), commits in plain sentences, no
  `Co-Authored-By` lines and no mention of AI or an assistant in commits or PR bodies.
  Bryan merges; open the PR with the body `.github/PULL_REQUEST_TEMPLATE.md` asks for.
  `main` is protected: lint, test, docker build, secrets scan and client-data guard must pass.
- **Frontend design is Bryan's** (with Codex). Backend work records what the API offers and
  the app does not use yet in `web/FRONTEND_BACKLOG.md` rather than building screens.

## Where to read first

- `README.md`: architecture, stages, security and data handling.
- `docs/DEVELOPING.md`: setup, the method, the recipes, the definition of done.
- `docs/api/WORKFLOW.md`, `STUDY_RESULT.md`, `TIMELINE_AND_AUDIT.md`, `REPORT.md`: the API.
- `docs/review_2026-10-15/EVAL_REPORT.md`: what was measured, section by section; read the
  section a default cites before changing it.
