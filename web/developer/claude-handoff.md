# Developer page integration

Continue the existing `claude/client-errors` implementation; do not start a second error store. Keep existing customer workflow changes intact. The developer UI is being worked on in `/Users/bryanramirez-gonzalez/.codex/worktrees/vision-review-workbench/Basis_Info/web/developer/`; compare and integrate its changes rather than overwrite the directory.

## Recent errors

Connect the developer page's `#recent-errors` section to your operator-only persisted error list/detail API. It currently says “Error history is not connected”; this is intentional until a real endpoint is available. Keep the section between Systems and Cost Segregation Workflow.

List recent events newest first with severity, timestamp/time zone, stable BASIS error code, service or workflow step, short message and occurrence count. Open a record to show the stored context: first/last occurrence, request/trace/correlation ID, study and revision, firm and user IDs, page, action, HTTP status, exception type, redacted message/stack, build/environment, workflow/activity/run/attempt, provider/model, retryability and exit code when applicable. Missing data must be “Not recorded”, never zero or a guessed value. Preserve failed and cancelled attempts.

Use the error catalogue for the explanation and corrective action. Enforce `OPS_OPERATOR_UIDS` server-side; a firm owner is not a platform operator. Redact tokens, cookies, signed URLs, credentials and unnecessary client content before persistence. Do not expose local file paths or private report artifacts through static web files. Bound pagination, filters, payload size and rate limits. Distinguish an empty successful result from 401, 403, endpoint unavailable, network failure and malformed responses. A failed feed must not say “No errors”.

The browser must not create an error-reporting loop when telemetry itself fails. Keep request IDs correlated between frontend, API, workflow and worker. Do not turn a current failed health probe into an allegedly persisted historical event.

## Runtime architecture

Extend the existing `/v1/ops/health` response additively. `model`, `adapter` and `last_inference` already exist. The frontend now preserves them even when `/health` provides a more recent API/database/Temporal check.

Add safe operator metadata from the effective runtime, not `.env.example` or defaults:

- `hosting`: a display label such as “Local Docker”, “Temporal Cloud”, “Google Cloud Run”, “Replicate” or “Azure OpenAI”. A Temporal adapter name alone does not establish hosting. Include region separately if useful; no credentials, internal connection strings or secret-bearing URLs.
- `fallbacks`: null if unknown, [] if explicitly no backups, otherwise ordered display labels including the model/deployment and provider. The current UI accepts an array of strings; coordinate a structured extension if you need trigger conditions/health.
- Prefer structured route metadata with primary and fallback order, model/deployment, provider, active settings and effective timestamp. The worker already builds `model_routes()` and stores routes in its heartbeat. Expose that evidence instead of reconstructing routing from names.
- Make clear which model is doing countables, surfaces, fusion and room reconciliation. A brand logo is not proof a service is in use.

## Evaluation records

Expose the existing evaluation registry/manifests through an authenticated operator list/detail/download interface. Reuse `evals/record.py`, `evals/registry.py` and the verification tree; do not duplicate the registry or bundle private artifacts in `web/`.

Return component/step, architecture ID, selected-versus-alternative status, tested git SHA, settings/prompts, dataset label version and split, repeat count, run status, measured accuracy/count metrics, input/output/cached/reasoning tokens, recorded USD cost and duration. Preserve unknowns, distinguish measured values from estimates, and only compare like-for-like datasets/settings. Return authorized links to run manifest, report, redacted ledger and console output. The UI currently includes the historical public section 15.1 photo comparison with a pinned source link; it does not claim this is the current runtime or all historical runs.

## Verification

Use synthetic records and mocked providers. Test tenant/operator isolation, redaction, duplicate delivery, malformed/oversized events, pagination, unavailable feeds, missing metadata, stale evidence and partial health outages. Verify error list/detail and architecture metadata on desktop and a 375px screen. Keep failed systems above normal category groups, with text status plus red color; unknown remains neutral. No paid model calls, deployment, migration execution on the active stack or merge without the existing authorization. Update API docs/OpenAPI, EVAL_REPORT and the test ledger; run the repository gates required before a PR.

References: W3C Use of Color (https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html); OpenTelemetry exception attributes (https://opentelemetry.io/docs/specs/semconv/registry/attributes/exception/).
