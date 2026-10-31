# Internal system health: `GET /v1/ops/health`

For platform operators and the developer page (`web/developer/`, served at `/developer/`).
It is separate from customer study screens and from `GET /health`, which keeps its
semantics: unauthenticated, 200 while Temporal fails, 503 with `checks` when the database
fails.

## 1. Access

* A Firebase ID token (`Authorization: Bearer ...`), verified like every `/v1` route.
* **And** the token's uid listed in `OPS_OPERATOR_UIDS` (comma-separated Firebase uids).
  Empty, the default, refuses everyone. A firm role never grants access: a firm owner is
  not a platform operator.
* 401 without a usable token, 403 `not-operator` (`BASIS-API-043`) otherwise. Neither
  body carries any diagnostics.
* The route does not upsert a database user, so an operator can read it while the
  database is down.
* Rate limits: the usual read limit, then 30 requests a minute per operator (429 with
  `Retry-After`).

Why a uid allowlist and not a database role or a Firebase custom claim: a role needs a
migration and a grant path (an HTTP route to grant operator access would itself need an
operator), and a custom claim needs Admin SDK credentials that the API holds only in
`prod`. A deployment setting is explicit, reviewable in the deployment's configuration,
and fails closed.

## 2. Response

200, or 503 with the same body when a service with `required: true` is `down`.
`Cache-Control: no-store`.

```json
{
  "schema_version": 1,
  "services": [
    {
      "id": "sam3",
      "status": "healthy",
      "checked_at": "2026-10-31T14:00:00Z",
      "expires_at": "2026-10-31T14:02:00Z",
      "check_kind": "probe",
      "latency_ms": 212.4,
      "required": false,
      "detail": "Metadata probe: vufinder/sam3@1bf97763d5df: answered. The vufinder/sam3 version is visible to the token (version lookup). No prediction was run ...",
      "adapter": "replicate",
      "model": "SAM 3 (vufinder/sam3@1bf97763d5df)",
      "last_inference": {"at": "2026-10-30T18:22:10Z", "model": "vufinder/sam3", "kind": "localization"}
    }
  ]
}
```

* `services` lists all 25 ids, always in this order. Infrastructure: `api`, `db`,
  `temporal`, `worker`, `relay`, `storage`, `firebase`. Providers: `openai`,
  `azure_openai`, `anthropic`, `replicate`. Processing: `azure_di`, `appraisal`, `vision`,
  `sam3`, `mask2former`, `room_inventory`, `embeddings`, `retrieval`, `classification`,
  `takeoff`, `structure`, `rsmeans`, `cost_indices`, `exports`.
* `status`: `healthy`, `degraded`, `down`, `unknown`, `disabled`. `disabled` is an
  optional dependency that the effective configuration does not use (never something
  merely unchecked).
* `checked_at` is when the evidence was gathered: a heartbeat, a probe, or a check this
  request ran. A cached answer is served as it was, and its times are not refreshed.
  `expires_at` is when the evidence stops standing. **Evidence past `expires_at` is reported
  `unknown`**, never with its old status. Every known status has a time, `disabled`
  included: the time of the worker report that the configuration was read from.
* `check_kind`: `probe`, `heartbeat`, `model_run` or `configuration`. `configuration`
  alone is never `healthy`.
* `model` and `adapter` are what the **worker** runs (from its heartbeat report: each model
  with its provider route, primary first, and the Azure deployment a request is sent to),
  never source defaults or the API's own settings.
* `last_inference` (an addition to the original contract; clients may ignore it) is the
  latest successful paid call that the ledger (`llm_calls`) recorded for the service. It is
  evidence of inference, kept separate from the metadata probe that sets `status`.
* `detail` says what the check established. It never contains a key, connection string,
  endpoint, host, document content, prompt or provider error text.

## 3. Where the evidence comes from

The API cannot see the worker's runtime: it is a separate process with its own settings,
and in staging only the worker holds provider credentials. So:

* **Worker and relay heartbeats.** Each process upserts one `service_heartbeats` row
  (migration 0019) every `OPS_HEARTBEAT_SECONDS` (default 15) and on every state change:
  `starting`, then `warming` (the embedder loads before polling), then `polling`, then
  `draining`, then `stopped`, or `failed`. The worker's report holds its build, model
  routes, step switches (photo mode, count fusion, room step, surface crops, structure
  step, the rooms gate waiting for SAM 3), adapters, cost table, embedder warm-up,
  reference index, and the latest provider probe results. A heartbeat stands for three
  intervals, at least 45 s. A write failure is logged as `BASIS-INF-004` and never stops
  the process.
* **Temporal pollers.** `DescribeTaskQueue` (workflow and activity) for the study task
  queue. A namespace check does not verify that workers are polling; this does.
* **The outbox.** The relay's backlog: pending, due, the age of the oldest due message, and
  dead messages.
* **The ledger.** The latest successful call per provider, kind, activity and model.
* **The stores.** The latest published reference corpus, and the cost index store's latest
  historical year plus the last refresh run per source.
* **Storage.** A metadata lookup of the reserved name `_ops/health-probe`, which sits under
  no study's prefix, so no client file is read.
* **Firebase.** This request's token was just verified, which is the readiness evidence.
  No user identity is returned.
* **The database and Temporal.** The `/health` checks, reused, each bounded at 2 s.

Each read is bounded and fails on its own, so the response is partial rather than an
error. All checks run concurrently. The document is computed at most once every 10 s per
API process, and concurrent requests share one evaluation.

## 4. Provider probes (worker side, never on GET)

Every `OPS_PROVIDER_PROBE_SECONDS` (default 60; 0 turns it off) the worker makes one `GET`
per lookup through a client that refuses any other method, with a 5 s bound:

| Target | Request | Establishes | Does not establish |
|---|---|---|---|
| `openai` | `GET /v1/models/{model}` for each model routed to it | the key, and that the model is visible to it | quota, vision, structured output, inference |
| `azure_openai` | `GET {endpoint}/openai/models?api-version=...` | the endpoint and key | the deployments (the data plane does not list them), quota |
| `anthropic` | `GET /v1/models/{model}` for each Claude model routed to it | the key and the model | credit, rate limits, inference |
| `replicate` | `GET /v1/account` | the token | billing credit |
| `sam3` (Replicate) | `GET /v1/models/vufinder/sam3/versions/{id}` | the version is visible | cold start, inference |
| `mask2former` (Replicate) | `GET /v1/models/oitoito/mask2former/versions/{id}` | the version is visible | inference |
| `azure_di` | `GET {endpoint}/documentintelligence/info` | the resource and key | an analysis |
| `sam3` (self-hosted) | `GET {LOCALIZER_URL}/health-check`, only with `OPS_PROBE_LOCALIZER=true` | `READY`/`BUSY` means setup ran, so the model is loaded | inference |

The self-hosted probe is off by default because, on Cloud Run, a request starts a GPU
instance billed per second. Results are sanitized to a category (credentials rejected,
not found, rate limited, provider error, timed out, network error, malformed response)
plus the HTTP status. No inference, Replicate prediction or Azure DI analysis runs on a
GET or on any automatic refresh. A future inference smoke test would be a separate,
explicitly authorized action with a budget and an audit record.

## 5. How statuses are decided

* **Worker:**
  * `healthy`: a `polling` heartbeat is current and Temporal lists recent workflow and
    activity pollers.
  * `degraded`: any of
    * cold start (`starting`/`warming`, at most 15 min);
    * polling, but Temporal lists no poller or the poller check failed;
    * Temporal lists pollers but no heartbeat is current.
  * `down`: nothing is current, or start-up has run longer than 15 min.
* **Relay:**
  * `down`: no current heartbeat.
  * `degraded`: any dead message (`BASIS-INF-003`), a due message older than 60 s, or the
    relay reporting that it has not reached the database.
* **Providers:**
  * `disabled`: not configured in the worker.
  * Otherwise the probe's status, and `unknown` until the first probe.
  * `required` when the provider is the first route of some pipeline model; a
    fallback-only provider is optional.
* **Capabilities** aggregate only what they use under the worker's effective settings.
  Each one also needs a current worker.
  * `appraisal`: the grounded model's provider; the text model's provider (optional in
    grounded mode, where it is only the fallback); Azure DI (optional).
  * `vision`: the vision, surfaces and (when active) fusion models.
  * `room_inventory`: the room, second-look, room-call and surface-crop models, plus
    Mask2Former (optional) with surface crops.
  * `classification`: the classifier model, `retrieval` and `embeddings`.
  * `exports`: storage and the database.

  A required dependency that is down makes the capability down; one that is unknown makes
  it unknown; a degraded or optional failing one makes it degraded. The evidence is the
  oldest of its dependencies'.
* **Data is not an outage.** Each of these is `degraded`, and its detail says so:
  * no published reference corpus (`retrieval`);
  * no licensed RSMeans table in the worker, `COST_TABLE_PATH` unset (`rsmeans`);
  * a latest historical index older than last year, or a failed refresh (`cost_indices`,
    with `python -m basis.costs.refresh`).

  An empty cost index store is `down`: run `python -m basis.costs.refresh seed`.
* **Disabled:**
  * `sam3`: neither `LOCALIZER_URL` nor `REPLICATE_API_TOKEN` is set. It is required only
    with `ROOMS_GATE_WAITS_FOR_LOCALIZATION=true`.
  * `mask2former`: surface crops are off.
  * `azure_di`: not configured.
  * `room_inventory`: `ROOM_RECONCILE` is off.
  * `structure`: `STRUCTURE_STEP` is off.
  * `replicate`: nothing runs on it.

## 6. Settings

| Setting | Default | Read by |
|---|---|---|
| `OPS_OPERATOR_UIDS` | empty (nobody) | API |
| `OPS_HEARTBEAT_SECONDS` | 15 | worker, relay |
| `OPS_PROVIDER_PROBE_SECONDS` | 60 (0: off) | worker |
| `OPS_PROBE_LOCALIZER` | false | worker |

Rollout: apply migration 0019 before deploying the worker and the relay. Until it is
applied they log `BASIS-INF-004` and keep running.
