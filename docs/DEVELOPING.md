# Developing Basis

How this project is built, for a developer joining it: the machine setup, how to run the
system, the method every change follows, where every number is recorded, the recipes for
the common kinds of change, and what "done" means. `README.md` explains what the system is;
`AGENTS.md` is the two-page summary of this document that coding agents read. The
repository is private to the team; the rules about client data and secrets hold anyway.

Contents

1. The standard we hold ourselves to
2. Set up a machine
3. Run the system locally
4. The method: how a change is developed
5. Where every number goes
6. Testing
7. Recipes for the common changes
8. Live runs, money and approvals
9. Branches, commits, pull requests, CI and merging
10. Security, client data and what is never done
11. Definition of done
12. Reading list

## 1. The standard we hold ourselves to

Basis produces a document an engineer signs and a client's accountant files with the IRS.
Every number in it must be defensible, and every design choice must be defensible in the
same way: with evidence. The project's rule, from the beginning, has been that **two
well-tested functions are worth more than ten unverified ones** and that taking an hour to
do a small thing right beats shipping it fast and finding out later.

Concretely, every change, however small, goes through the same six steps (section 4):
research the standard first; verify empirically, never by inference; implement to match the
standard; test every path including the regression path; defend every choice as if a
reviewer were drilling you; and say plainly what is verified and what is only suspected.

The bar was set by the vision work in October 2026 (EVAL_REPORT sections 6 to 21): a labelled
harness, a baseline, then prompts, task decompositions, detectors and models compared side
by side on the same photos, with accuracy, cost, tokens and latency for each, and the
cheapest configuration that matched the best one chosen as the default. That campaign found
the winning combination and its cost only because everything was measured. The same is
expected for every stage: appraisal extraction, classification, costing, allocation, the
report, and the operational work around them (races, backups, onboarding).

## 2. Set up a machine

Toolchain, as used on the maintainer's machine (check the pins in the repository; the
versions here are the ones known to work):

| tool | version | why |
|---|---|---|
| Python | 3.12 (`backEnd/.python-version`) | the backend |
| uv | 0.13 or later | environment and lockfile (`backEnd/uv.lock`) |
| Docker Desktop | current | Postgres with pgvector, Temporal, the stack; testcontainers for the integration suite |
| Node | 20 or later (23 is in use) | `web/tests/*.test.cjs` with `node --test` |
| Temporal CLI | optional | the lifecycle tests download the test server themselves when it is absent |
| Firebase CLI and Java | 14.x, Java 11 or later | the Auth emulator for the web app in live mode |
| pre-commit | 4.x | the commit hooks |

Steps:

```bash
git clone https://github.com/bryanrg22/Basis_Info.git && cd Basis_Info
pre-commit install                                   # ruff, detect-secrets, private-key check, client-data guard
cd backEnd
uv sync --locked --group dev                         # the exact environment CI uses
cp .env.example .env                                 # dev only: BASIS_ENV=dev reads it; never commit it
```

`.env` holds your own provider keys if you have any (OpenAI, Anthropic, Azure OpenAI, Azure
Document Intelligence). You do not need any to develop: every test and most evaluations run
with scripted models. Keys are never committed, never pasted into a chat, never printed; the
pre-commit hooks and CI's gitleaks scan both refuse them.

The private evaluation data (a client's appraisal, photos and engineer's report) lives
outside the repository under `BASIS_EVAL_DATA_DIR` (the maintainer uses `~/basis-eval-data`).
Only the maintainer has it; tests that need it carry the `eval_data` marker and skip without
it. Everything in this guide works without it.

## 3. Run the system locally

**The whole stack** (Postgres, Temporal, API, worker, relay), from the repository root:

```bash
docker compose up --build
docker compose exec api alembic upgrade head
```

The API is on `localhost:8000` (`/health` reports the git sha it was built from, `/docs` the
OpenAPI UI), the Temporal UI on `localhost:8233`. Rebuild the worker before the API when a
change touches both: the API records decisions the worker's workflow must understand.

**The API alone**, for route work, from `backEnd/` with `BASIS_ENV=dev` and a `DATABASE_URL`:

```bash
uv run --offline uvicorn basis.api.main:app --reload --port 8000
```

**The web app.** Mock mode needs nothing: `cd web && python3 -m http.server 3100 --bind
127.0.0.1`, open `http://127.0.0.1:3100/`. Live mode talks to the stack through the Firebase
Auth emulator (`firebase emulators:start --only auth --project demo-basis`, then
`http://127.0.0.1:3100/?mode=live`); `web/README.md` "Live mode, locally" has the exact steps.
The API accepts emulator tokens only with `BASIS_ENV=dev`.

**A full study by hand** is walked through in `docs/review_2026-10-15/S2_RUNBOOK.md`.

## 4. The method: how a change is developed

### 4.1 Research the standard first

Before designing, find out how the established work does it and write it down: the IRS
guidance (the Cost Segregation Audit Techniques Guide, Pub 946, Rev. Proc. 87-56), RSMeans'
method, the appraisal form's specification (UAD 3.6), the provider's documentation, the
paper the technique comes from, what the industry tool does. Respect proven solutions; do
not reinvent. Improve on the standard only after you can state what it is. The research
notes go in a design document under `docs/review_2026-10-15/` (`*_RESEARCH.md`,
`*_SPEC.md`, `CLASSIFICATION_CONVENTIONS.md` and `STRUCTURE_MODEL.md` are the models) or in
the EVAL_REPORT section when the change is small.

### 4.2 Design against the system we have

Read the code that runs, not the document that describes it. The workflow is a Temporal
state machine (`basis/workflow/study_workflow.py`) with one activity module per step; the API
records decisions in Postgres and the outbox relay delivers them; every output carries
provenance and every paid call is ledgered. A design fits those seams: a new behaviour is a
new activity or a patch to the workflow, a new setting with a measured default, a new
prompt version, a new migration, a new route with its schema and contract test. Section 7
has the recipe for each. If the right design needs a seam that does not exist, that is a
design discussion first (an issue or a note to the maintainer), not a workaround.

### 4.3 Build the harness and the baseline

Anything that produces a judgment (a model call, a rule, a formula) gets a labelled dataset
and a scorer before it gets a second implementation. `backEnd/evals/` holds the runners
(`predict_*.py`), the datasets (`datasets.py`), the metrics (`metrics.py`) and the scorers;
`evals.run --dry-run` exercises the path on synthetic fixtures. Score the simplest thing
first (the printed text, the file name, the rules table alone): that is the baseline every
candidate must beat.

### 4.4 Compare alternatives side by side

Then the candidates: methods (one call or two, with or without a detector, reconcile per
room or not), prompts (versioned, `v1`, `v2`, ...), models (the frontier model and the cheap
ones), reasoning effort and output budget. Every candidate runs on the same data, repeated
when the output is stochastic, and the table records for each: the accuracy metrics the
dataset defines, the cost per study, input and output tokens, latency, and the failure modes
seen. The decision rule is written down with the project: the cheapest candidate within the
noise of the best is the default, except where accuracy truly matters (per-photo component
identification and counting), and a more expensive default needs the numbers that justify
it. The pipeline is then pinned to the winner in `config.py`, with a comment naming the
section.

### 4.5 Test every path

Unit tests for the logic; API tests for the route (auth, each role, each error code, the
OpenAPI contract); integration tests when Postgres semantics matter (locks, uniqueness,
migrations); lifecycle tests when the workflow changes (the recorded histories must still
replay); node tests for the web helpers. Edge cases are enumerated, not guessed at: empty
input, the largest input, the repeated request, two requests at once, the request after
completion, after signing, after archive. Races are made deterministic (the helpers in
`tests/integration/db/dbtest_support.py` hold a lock and queue the second request behind it)
and never left to `sleep`. A changed behaviour changes its test in the same commit, with the
reason in the commit message and the LEDGER row. When a test is cheap to mutate, mutate it:
break the code on purpose and confirm the test fails.

### 4.6 Run it for real, then record

A change to a stage is not done until it has run end to end on a real study (section 8) or,
when a live run is not justified, on a replay of a recorded export (`evals.e2e_report`,
`evals.conventions_replay`). The result is written up the same day (section 5), including
what went wrong and what is still open. A README or docs claim is written after the code
does what it says, never before.

### 4.7 Defend it

Before opening the pull request, read the change as a hostile reviewer: why this design and
not the obvious alternative; what the numbers would look like if the hypothesis were wrong;
which test would catch a regression; what a firm's engineer would see if it failed at 2 a.m.
Fix what does not hold up. Then write the PR body (section 9) so the reviewer does not have
to ask.

## 5. Where every number goes

| what | where | form |
|---|---|---|
| a measured result (accuracy, cost, timing, a finding) | `docs/review_2026-10-15/EVAL_REPORT.md` | the next free section (check open branches for `^## N\.` first): **Question**, **Setup**, **Method**, results table, **Verdict**, **Open**; counts and percentages only, never a client's figures |
| a new test file | `backEnd/tests/LEDGER.md` | one row: file, the invariant it protects, the release or PR, notes |
| an evaluation run | `$BASIS_EVAL_DATA_DIR/verification/<component>/<version>/<run id>/` | written by the runner (`evals/record.py`): manifest (code sha, prompts and their sha256, model, options), `results.json`, `report.md`, `llm_calls.jsonl` with every paid call, the raw requests and responses, the console log; `uv run python -m evals.registry` lists them |
| every paid call in production | the `llm_calls` table | component, stage, activity and attempt, prompt version and sha, requested and answered model, input, output and cache tokens, price row, cost, latency, status; the export carries `rev-<n>.calls.jsonl` |
| a release's test evidence | `docs/test-evidence/<label>/` | `tooling/evidence/capture.py --label vX.Y.Z`: manifest, collected tests, gate logs, JUnit summary |
| a live run's dollars and client-identifying detail | `$BASIS_EVAL_DATA_DIR/reports/` | the private report; the EVAL_REPORT section cites it by file name |
| a decision about a default | the setting's comment in `config.py` | the section number that measured it |
| a process or convention | `AGENTS.md` (short) and this file (long) | fixed in the same change that changes the process |

The point of all of it: a question like "has `photo_countables/v3` on gpt-6.1-sol been tested
against `photo_components_v1`, and what did it cost" is answered from files, and a reviewer
can trace any default in the pipeline to the table that chose it.

## 6. Testing

From `backEnd/`, with the provider keys unset (`env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY
-u AZURE_OPENAI_API_KEY ...`; one worker test asserts no provider is configured):

```bash
uv run --offline pytest -m "not integration and not slow"              # unit, api, node, behavior: about 80 s
uv run --offline pytest -m "(integration or slow) and not eval_data"   # Postgres + Temporal: about 15 min
uv run --offline pytest tests/lifecycle -q                             # the workflow on the time-skipping server
node --test web/tests/*.test.cjs                                       # from the repository root
```

Markers (`pyproject.toml`): `integration` needs Docker (testcontainers starts
`pgvector/pgvector:pg16`); `slow` takes more than a few seconds; `eval_data` needs the private
data and is deselected in CI; `regression` guards a fixed bug and is never deleted without a
stated reason. The suites in `tests/` and what each covers are tabulated in `README.md`
"Testing". CI's `backend-test` job runs the whole suite with `--timeout=600` per test and a
45-minute job limit; the suite takes about 26 minutes there, so do not add tests that sleep.

Habits that keep the suite trustworthy: assert on the thing itself, not on a log line;
order-free assertions where the database may pick the winner; one real Postgres for anything
about locks or constraints; recorded Temporal histories for every workflow patch; a mutation
check on a new test when it is cheap.

## 7. Recipes for the common changes

**A new setting.** Add the field to `Settings` in `basis/config.py` with a default and a
comment naming the EVAL_REPORT section that chose it; add the line to the header comment
mapping environment variable to field; add it to `.env.example`; test it in
`tests/unit/config/`. A new behaviour ships behind a setting so the old path stays
reachable until the new one is measured live.

**A new prompt version.** Prompts are immutable once released. Add
`basis/prompts/<name>/v<n+1>.md` and its sha256 to `basis/prompts/manifest.json` (the unit
tests check every file is in the manifest); measure it against the current version with the
runner for that step; pin the pipeline to it in `config.py` only with the section that shows
it is better or equal at lower cost.

**A new route.** Schema in `basis/api/schemas/`, route in `basis/api/routes/`, the operation
in `tests/api/test_health.py`'s known-routes list, the Markdown contract in `docs/api/<AREA>.md`,
then `uv run python -m basis.api.openapi --write` and the Schemathesis contract test. Writes
take `If-Match` and answer 412 with the current `ETag`; errors are problem details with a
code; every role is tested (viewer, engineer, owner, non-member 404).

**A new error code.** `BASIS-<AREA>-<nnn>` in `basis/errors/codes.py`, the description in
`basis/errors/catalogue.py`, then `uv run python -m basis.errors.catalogue --write` regenerates
`docs/ERROR_CODES.md`. Never write a code as a string literal elsewhere (a test forbids it).
Customer-facing text keyed on a code never names a provider.

**A new migration.** The next number in `basis/db/alembic/versions/` (check open pull
requests for a collision; renumber yours when another lands first), hand-written, with the
model change; `uv run alembic check` proves they agree; data migrations are plain SQL so the
chain renders offline (`tests/integration/db/test_migrations.py` renders it and pins the head).

**A workflow change.** Behind a Temporal patch, `workflow.patched("<name>-v<n>")`, so the
histories in `tests/lifecycle/histories/` replay; a new activity gets its options (timeouts,
attempts, heartbeat) in `basis/workflow/activity_options.py`, its module in `activities/`, its
contracts in `workflow/contracts.py`, and lifecycle tests for the happy path and every
failure path (retry, budget, account error, cancel).

**A new evaluation.** A dataset in `evals/datasets.py` (and its private label file under
`BASIS_EVAL_DATA_DIR`, built by a private script that stays the source of truth), a runner
`evals/predict_<thing>.py` that declares its component and version and leaves the run
record, metrics in `evals/metrics.py`, and a `--dry-run` path on synthetic fixtures so the
harness itself is tested in CI.

**A web change.** The design is the maintainer's (with Codex); backend work records what the
API offers and the app does not use yet in `web/FRONTEND_BACKLOG.md`. A plain bug in
`web/live/*.js` is fixed with a node test in `web/tests/`; the mock mode must keep working.
Nothing the app shows names a provider.

**A dependency.** `uv add` or `uv add --group dev`, commit `uv.lock`, run both suites;
Dependabot opens the routine bumps and they merge when green.

## 8. Live runs, money and approvals

A live study runs the real models on the client property: about $2 to $4 with the firm's
conventions in the database, $7 to $8 without. It is run when a change touches a stage's
output and a replay cannot prove it, and only with the maintainer's approval of the spend,
recorded in the pull request. The procedure the maintainer uses (private drivers under
`$BASIS_EVAL_DATA_DIR/work/live_e2e/`): a database copy, a compose override that builds the
stack from the commit under test, the worker rebuilt before the API, the driver in `plain`
mode (every gate approved as the model left it) and in `corrected` mode (the engineer's
corrections from the labels at every gate), `evals.e2e_report` on the export, the write-up
the same day with cost by model, failed activities and their causes, and the comparison to
the previous run. Keep the machine awake (`caffeinate -w <driver pid>`): run 6 lost seven
photos to a laptop sleeping for twenty seconds (EVAL_REPORT 63).

Nothing else costs money without being said so: an evaluation runner calls a provider only
when a key is configured and its command asks for it (`--dry-run` uses scripted providers
and is what CI runs), every runner prints its cost, and the tests never call a provider.

## 9. Branches, commits, pull requests, CI and merging

- **Branch** from `main`: `<your-handle>/<topic>`. One change per branch; stack a second
  branch on the first when it depends on it and say so in the PR.
- **Commits** are plain sentences that say what changed and why ("Intake lock tests: a
  changed price the intake validation accepts, so the 409 is the lock's"). No
  `Co-Authored-By` lines; no mention of an AI assistant in commits or pull request bodies.
- **Before you push**, every gate is green locally (`AGENTS.md` lists them) with provider
  keys unset. `pre-commit` runs the fast ones on each commit.
- **CI** (`.github/workflows/ci.yml`) runs on every pull request and push to `main`:
  `backend-lint`, `backend-test` (the full suite, about 26 minutes), `frontend`,
  the gitleaks scan, `client-data-guard`, `docker-build`; `deploy-staging` only when the
  repository variable enables it (it does not). `main` requires lint, test, the Docker
  build, the gitleaks scan and the client-data guard; a red check is fixed in the branch,
  never bypassed.
- **The pull request body** follows `.github/PULL_REQUEST_TEMPLATE.md`: what changed, what was
  measured (section numbers and headline numbers), the tests and the invariants they
  protect, what is not verified and open, the gates with their results. The maintainer
  reviews and merges; when two pull requests claim the same EVAL_REPORT section number or
  migration number, the second to land renumbers.
- **After a merge** that changes behaviour a running stack depends on, rebuild the worker
  first, then the API.

## 10. Security, client data and what is never done

- **Client files stay out of the repository**, private or not (it was public until November
  2026, and a private repository is still cloned to laptops and CI runners). Appraisals,
  photos, engineer's reports and the cost table derived from RSMeans never enter it or CI.
  `tooling/check_no_client_data.py`
  blocks known files by sha256, data extensions, large files and credential-like paths, as a
  pre-commit hook and in CI. A report that quotes a client's figures is written under
  `$BASIS_EVAL_DATA_DIR/reports/` and cited by name.
- **Secrets** live in `.env` (gitignored) or Secret Manager, never in code, docs, tests,
  commit messages or chat. gitleaks and detect-secrets run on every change.
- **Provider names** (OpenAI, Anthropic, Azure, Replicate, SAM 3) never appear in anything an
  engineer or client sees: outages read as Basis being temporarily unavailable; the
  operator ops page is the one place with provider detail.
- **Nothing is deployed.** `infra/staging/` is Terraform for a parked staging project; no
  `terraform apply`, no Cloud Run, Cloud SQL or bucket change, no DNS, without the
  maintainer's explicit approval in the session doing it. Docker Compose is the development
  and verification environment.
- **Firm conventions** are a firm's rulings. Changing how they match (keys, tiers, the
  decision-over-model rule) is replayed with `evals.conventions_replay` against the recorded
  exports before it ships.
- **A final study is never changed silently**: signing records input versions, the intake
  locks at start, outputs that feed a signed report refuse writes (EVAL_REPORT 62). Keep it
  that way in every new route.

## 11. Definition of done

A change is done when all of these are true, and the pull request says so:

1. The standard was researched and the design cites it.
2. The behaviour runs in the real system (the stack, or a replay when a live run is not
   justified), and what was seen is written down.
3. Alternatives that mattered were measured side by side, and the default is the cheapest
   within the noise of the best (or the exception is justified).
4. Every path has a test: the happy path, each error, each role, the repeated request, the
   concurrent request, the request after completion; the regression path is unchanged.
5. Every number is recorded: EVAL_REPORT section, LEDGER rows, the run record, the setting's
   comment.
6. The documents that describe the behaviour are true: `docs/api/`, `openapi.json`,
   `ERROR_CODES.md`, `README.md`, this file.
7. Every gate is green locally and in CI; nothing paid or deployed happened without recorded
   approval; no client data or secret moved.
8. The pull request body states what is verified, what is suspected and what is open.

## 12. Reading list

In this order, for a first week:

1. `README.md`: the system, the stages, the data handling.
2. `docs/review_2026-10-15/REVIEW_AND_PLAN.md` and `BUILD_PLAN.md`: why the system is shaped
   this way, and the staged plan it was built to.
3. `docs/review_2026-10-15/EVAL_REPORT.md` sections 6 to 21 (the vision campaign), 42 to 46
   and 54 (classification and the firm's conventions), 44 to 49 (the structure model, cost
   indices, reconciliation), 53 and 63 (live runs), 57 and 62 (security, races).
4. `docs/api/WORKFLOW.md`, `STUDY_RESULT.md`, `TIMELINE_AND_AUDIT.md`, `REPORT.md`,
   `REVISIONS.md`: the contract the web app and any integration use.
5. `backEnd/basis/workflow/study_workflow.py` top docstring: every patch the workflow carries
   and why.
6. `backEnd/evals/README.md`: the harness, the run records, the registry.
7. `web/README.md` and `web/FRONTEND_BACKLOG.md`: the app, and what it does not use yet.
