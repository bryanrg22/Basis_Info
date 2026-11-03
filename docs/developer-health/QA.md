# Developer health verification

Verified locally on October 31, 2026, in the existing frontend worktree. This change is frontend work; it does not implement provider probes or deploy a backend.

## Design and source inspection

The page deliberately keeps Basis's restrained green/white styling, uses a system font and labeled statuses, and separates a service list from a horizontal workflow diagram. The diagram preserves the appraisal/photo split and join, all four engineer checkpoints, and correction/revision behavior. It is service health, not progress for a particular study.

Read actual `backEnd/basis/api/health.py`, `worker/health.py`, `worker/main.py`, `activities/deps.py`, `config.py`, provider-pool code and both Compose files. The current `/health` checks API identity, PostgreSQL and Temporal. Temporal failure can coexist with HTTP 200; database failure returns a 503 problem response containing checks. Workers, relay, providers and processing capabilities need an operator diagnostics feed.

References used before implementation: [Kubernetes probe semantics](https://kubernetes.io/docs/concepts/workloads/pods/probes/) distinguish health/readiness evidence; [W3C use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) calls for a non-color indication; [W3C status messages](https://www.w3.org/WAI/WCAG21/Understanding/status-messages) informs the polite refresh announcement. These references inform interaction choices, not a claim of complete accessibility conformance.

## Automated verification

`node --test web/tests/*.test.cjs`: **262 passed, 0 failed** (18 new health tests plus 244 existing tests).

JavaScript syntax and `git diff --check` passed. Tests cover partial health in HTTP 200 and 503 responses, unreachable API, missing/malformed schemas, no inference from configuration, stale/future/missing timestamps, disabled optional versus required dependencies, unknown and zero latency, HTML escaping, workflow dependencies, GET-only paths, private access without a session, error response parsing, bounded network/auth waits, and refusal to send a token to an untrusted query-selected API origin.

## Observed browser checks

- Live `/developer/` successfully read API build `9c430aa`, version `0.4.0`, and reported **3 healthy / 22 not checked**. This is a point-in-time observation, not a guarantee of current health.
- The live extended-diagnostics attempt could not complete through the local sign-in/diagnostics connection. Provider state remained unknown. No inference or prediction was submitted.
- Synthetic fixture: **23 healthy / 2 disabled** when all enabled checks report success; **19 healthy / 4 failed / 2 disabled** with Temporal, Replicate, SAM 3 and photo-analysis failures; **3 healthy / 22 stale** with expired extended evidence.
- A 403 after a previously healthy response cleared the provider greens and displayed the operator-access explanation. Missing endpoint displayed the backend connection explanation. API outage displayed **1 failed / 24 unknown**, and recovery restored the next reported snapshot.
- Workflow arrows show split, join and sequential review connections. Horizontal scroll controls work. A SAM 3 workflow chip opened the matching details, including the failure explanation, model and adapter.
- Search plus the attention filter narrowed the list to the failing SAM 3 row. Native details and labeled controls remained usable.
- Screenshots inspected at full desktop width and a 390px iframe; the live narrow in-app browser was also inspected. Page scroll stays vertical; the workflow has horizontal overflow.

## Delivery boundary

A copyable backend prompt and stable IDs/schema are in [`web/developer/backend-contract.md`](../../web/developer/backend-contract.md). The endpoint is proposed and must be coordinated with Claude's backend work. Provider and worker healthy/failure scenarios have been verified with synthetic data, not live provider probes. No backend files, migrations, provider credentials, running studies or deployment state were changed by this task. The local frontend server was started on port 3100.

## Mobile refinement

Phone layouts now put status below each service name, keep logos and descriptions aligned, use 44px controls and 16px search input text, and place workflow navigation below its description. Workflow arrows advance one column; the diagram supports native horizontal scrolling and column snap while the page scrolls vertically. Tablet table columns collapse before they overflow. Coarse-pointer devices receive larger controls at all widths.

The browser fixture now exposes selectable viewport widths and a read-only layout measurement. Observed no page-wide horizontal overflow or missing logos at 320, 375, 390, 430, 768, 1024 and 1600 CSS pixels; phone controls measured at least 44px. At 390px, synthetic failure results, search, expanded SAM 3 details, and one-column workflow movement were checked interactively (304px movement for a 280px column and 24px gap). These are browser viewport checks, not physical iPhone/Android certification.

Guidance reviewed: [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) and [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).

## November 1, 2026: systems and workflow details

Changes in this pass:

- Concise page/section titles; removed repeated instructional copy.
- Failed rows lead every normal category, followed by degraded/stale/required-disabled checks. Each service appears once. Status uses text and color.
- Models/adapters remain runtime evidence. Hosting is shown only from explicit metadata or a provider-identifying adapter; `Temporal` alone never implies Temporal Cloud. Unknown backups differ from an explicit empty fallback list. Last successful inference does not turn readiness green.
- Public probes retain the richer operator metadata while keeping their own fresh status/expiry. Short expiry times survive subsequent redraws.
- All 11 workflow boxes open a native dialog with three short steps and arrows, runtime details and source links. Failure red is preserved on hover/focus. Keyboard users can open the title button, dismiss with Escape and return to the button.
- Photo analysis includes the 21 historical architectures from EVAL_REPORT 15.1 at source commit `15cd291`, with the measured selection first. The page labels these as historical results, preserves unmeasured times, and does not substitute them for the current runtime. Source links were verified against the checkout; no private evaluation artifacts are copied to static web files.
- Recent errors explicitly says the history is not connected. The current API has no persisted error-history endpoint. The separate `claude/client-errors` worktree owns that implementation. `web/developer/claude-handoff.md` describes the connection and runtime/evaluation metadata still needed.

Verification:

- `node --test web/tests/*.test.cjs`: **269 passed, 0 failed**; 25 developer-health tests, including 7 additional ordering, recovery, expiry, metadata, escaping and workflow-detail cases.
- Browser: all 11 boxes opened with the correct title, and Escape dismissed every dialog.
- Browser synthetic partial outage: Temporal, Replicate, photo analysis and SAM 3 appeared above Infrastructure with Failed status; searching continued to work. Reported Temporal hosting appeared in the row.
- Browser layout measurements at 320, 375, 768 and 1600 CSS pixels: no page overflow and no broken logos. Phone controls measured at least 44px. The 375px modal's three-step explanation was visually inspected; wide tables scroll inside the dialog.
- Live public API build `15cd291`, version `0.4.0`: API, database and Temporal checks reported healthy. Worker/provider checks were not authenticated in this session, so 22 checks remained unknown. This does not establish end-to-end service readiness.
- Claude's walkthrough owns port 3100. This preview uses port 3101 and a temporary GET-only bridge for `/health` and `/v1/ops/health` to the existing local API on port 8000. No backend service, study or authorization settings were changed. This is local, not deployed.

Standards reviewed: [W3C color cues](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) and [OpenTelemetry exception attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/exception/). Error capture and protected run downloads remain backend work; the UI does not invent missing records or metadata.

## November 2, 2026: step dialogs filled from the code, the health feed and the evaluation report

Changes in this pass (EVAL_REPORT 73, branch `claude/developer-workflow`):

- Every step dialog's "How it works" is written from the step's activity (`web/developer/workflow-content.json`, each step naming its source file); "Build inventory" speaks of components, the room step's one call per multi-photo room and the material second look.
- "Current runtime" lists every system the step uses and, under a capability, one row per model role from the health feed's new `models` field: provider logo, exact model id (and the Azure deployment when it differs), the provider's hosting and adapter, the model's backups. Without a feed every row says "Not reported"; a model id is never typed in the page.
- "Tests & results" shows the architectures compared and the measured results per step (comparisons with the selected row first, metrics, cost, tokens and latency where the report states them, the regression status) with links to the report sections; `backEnd/tests/unit/test_developer_workflow_content.py` fails when a figure, heading or path no longer matches the repository.
- Service health still never says whether a study is signed or completed; every dialog says so under its runtime table.

Verification:

- `node --test web/tests/*.test.cjs`: **331 passed, 0 failed** (10 new developer-workflow tests; the three architecture tests ported).
- Browser, live with diagnostics connected to an isolated API (this checkout, port 8500, real `service_heartbeats` rows, provider probes answered by a mock transport): hosting labels on the system rows; the Build inventory dialog with `ROOM_MODEL`, `ROOM_MATERIALS_MODEL`, Mask2Former (not enabled) and SAM 3 on Replicate; all twelve dialogs open with the three sections.
- Browser, not connected: every runtime cell "Not reported", the measured results unchanged.
- Keyboard: Escape closes the dialog and returns focus to the step button. Console on a fresh load: empty. 390 CSS px: no page or dialog overflow; wide tables scroll inside the dialog.
- No paid model call, no deployment, no change to the `basis-web` stack or any client file.

Changes in this pass (2026-11-02, branch `claude/plain-how-it-works`; supersedes the runtime-table
lines above):

- The step dialogs no longer have a "Current runtime" table; live health stays on the Systems list.
- The Systems list shows the 18 systems that have a check of their own (Basis services, infrastructure, AI providers, models and reference data), each with its logo (the Basis mark on Basis's own) and one line on what it does; "Hosting" shows only when the feed reports it. The seven step roll-ups (appraisal extraction, photo analysis, room inventory, asset classification, quantity takeoff, concealed systems, result exports) have no check of their own and are not listed: their status is the workflow card's. The health feed (`GET /v1/ops/health`) is unchanged.
- On screens 981 px and wider the page is drawn at 67 % (`zoom`), the size Bryan preferred; dialogs keep the window's full height. Phones and tablets are unchanged.
- "How it works" is written in plain words, and each part lists the systems it runs today: the
  provider's logo, what the system does, the exact system (for a model: id, provider, effort and
  temperature). A model there is the production default of `basis/config.py`;
  `test_developer_workflow_content.py` fails if a typed model or effort differs from it.
- Section titles ("How it works", "Tests & results") are larger.
- Scrolling past the end of a dialog no longer scrolls the page behind it (`overscroll-behavior:
  contain` on the dialog, and the page does not scroll while a dialog is open).

Changes in this pass (2026-11-03, branch `claude/basis-logo`):

- Workflow cards say who does the step ("Engineer uploads the files", "Basis reads the appraisal") and show the systems the step runs, with their logos, instead of the health services.
- Each part of "How it works" has a one-line summary and the exact numbered steps the code runs today (read from the code with file and line, not from the docs), naming each technology; parts Basis built carry the Basis mark.
- "Tests & results" adds "What was tested, and what was not": every component of the step, whether it was compared against alternatives, what is missing and the test that would close it, with its cost (from a coverage audit of EVAL_REPORT 1-80: 19 compared, 23 partly, 7 not). "Regression" now reads "Still true on today's build?" and says the last live check was on 15cd291 (Nov 1).
