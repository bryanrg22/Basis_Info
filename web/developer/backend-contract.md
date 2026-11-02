# Claude Code handoff: internal system health

The frontend is `web/developer/`, served at `/developer/`. Keep this separate from customer study screens. It already reads `GET /health` for API / PostgreSQL / Temporal; it must not infer that GPT or SAM 3 is healthy from a successful API response.

Implement a read-only **GET `/v1/ops/health`** for authenticated platform operators. A firm owner is not automatically a platform operator. Use the existing Firebase bearer token and explicit backend operator authorization; return 401/403 without sensitive diagnostics. The page's Connect diagnostics button reuses the existing session. Set the trusted API with `BASIS_CONFIG_OVERRIDES.apiBase` before loading `live/config.js` for another deployment; a query-string `api` override alone cannot receive the bearer token. No provider keys, connection strings, document contents, prompts or raw provider exceptions may be returned. Do not weaken `/health` or existing access rules.

This is a proposed integration contract, not an endpoint that exists today. Coordinate if an ops endpoint is already being built and update the frontend adapter to the actual contract. Preserve concurrent frontend and backend work.

## Response contract

HTTP 200 (or 503 with the same body for a failed dependency):

```json
{
  "schema_version": 1,
  "services": [
    {
      "id": "sam3",
      "status": "healthy",
      "checked_at": "2026-10-31T14:00:00Z",
      "expires_at": "2026-10-31T14:01:00Z",
      "check_kind": "probe",
      "latency_ms": 42.5,
      "required": false,
      "detail": "Localizer is ready and its model is loaded. Inference was not run.",
      "adapter": "self-hosted",
      "model": "SAM 3"
    }
  ]
}
```

Statuses: `healthy`, `degraded`, `down`, `unknown`, `disabled`. `disabled` means an explicitly inactive optional dependency; omit or use `unknown` when unverified. Check kinds: `probe`, `heartbeat`, `model_run`, `configuration`. Configuration alone is never a green health result. A timestamp is mandatory for known status. Evidence older than 120 seconds (or `expires_at`, whichever is earlier) becomes stale in the UI. Do not update `checked_at` merely because a cached result was read. Report the actual model / deployment and selected adapter, not source defaults. Expose last successful inference separately from a non-inference provider probe and be explicit about what that probe establishes. A provider metadata request does not prove vision, structured-output, quota or inference readiness.

Stable IDs consumed by the frontend:

- Infrastructure: `api`, `db`, `temporal`, `worker`, `relay`, `storage`, `firebase`.
- Providers: `openai`, `azure_openai`, `anthropic`, `replicate`.
- Processing: `azure_di`, `appraisal`, `vision`, `sam3`, `mask2former`, `room_inventory`, `embeddings`, `retrieval`, `classification`, `takeoff`, `structure`, `rsmeans`, `cost_indices`, `exports`.

The list reflects `basis/config.py`, `worker/main.py`, `activities/deps.py` and Compose. Not every deployment enables every adapter. Model pool routing, optional fusion, surface crops, SAM hosting and legacy workflow gates must reflect effective runtime settings. Aggregate capability checks (`vision`, `appraisal`, etc.) must include only the dependencies used by that capability. Do not mark a capability healthy merely because its provider has credentials. Distinguish a missing licensed cost table, missing reference corpus and stale cost indices from provider outages.

## Checks and operational behavior

- Reuse bounded database / Temporal checks. A Temporal namespace check does not verify workers or the decision relay.
- Worker: current polling/heartbeat, startup model warm-up, build identity. Relay: recent heartbeat and delivery backlog. Storage: safe metadata/read probe without touching client data. Auth: configured project and token-verification readiness without returning user identities.
- Providers: cached, bounded account/deployment diagnostics with sanitized errors. Keep **all paid inference, Replicate predictions and Azure DI document analysis out of GET and automatic refresh**. If inference smoke tests are desired later, design a separate explicit authorized action with budget limits and audit records.
- SAM 3: distinguish self-hosted ready/model-loaded from Replicate account/model availability. Mask2Former is optional. Embeddings must report warm/readiness, not only a model name.
- Return partial results when one check fails; use concurrent bounded checks, rate limits and a short cache. Browser refresh is every 30 seconds, paused in hidden tabs. Never expose internal addresses or secrets unnecessarily.
- Existing `/health` can return 200 while Temporal fails, and 503 with dependency checks when the database fails. Preserve those semantics.

## Acceptance

Cover missing credentials, disabled optional adapters, expired evidence, auth denial, timeouts, malformed upstream responses, partial outages, relay backlog and worker cold start. Verify no billable calls on GET. Add API schema and tests, regenerate OpenAPI, and run current backend gates. Connect `/developer/` against the real API and verify green/red/unknown transitions and runtime metadata. Do not restart or migrate another running stack or trigger paid studies merely to verify this UI.
