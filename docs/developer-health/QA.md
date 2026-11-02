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
