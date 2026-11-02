# Evaluation run records: `GET /v1/ops/eval-runs`

For platform operators and the developer page (`web/developer/`, the "Run records" block of
each workflow step's dialog). The records are the evaluation registry's
(`python -m evals.registry`, EVAL_REPORT section 43): every run directory under the host's
`BASIS_EVAL_DATA_DIR` (`predictions/`, `appraisal/runs/`, and the runs written inside
`verification/`), read from its own files (`manifest.json`, `run.json`, `scores.jsonl`,
`eval.md`, `score.json`, `results.json`). The API adds nothing the files do not say
(EVAL_REPORT section 74).

```
GET /v1/ops/eval-runs?component=photo_countables&component=detection&model=gpt-6*&labels=photo_components_v1
                     &dataset=photo_counts&split=dev&status=ok&release=v0.10.0&limit=50&cursor=<next_cursor>
GET /v1/ops/eval-runs/{run_id}
GET /v1/ops/eval-runs/{run_id}/files/{manifest|report|ledger|console}
```

## 1. Access and availability

* A Firebase ID token whose uid is in `OPS_OPERATOR_UIDS` (docs/api/OPS_HEALTH.md
  section 1). A firm role, owner included, never grants it: 401 without a usable token, 403
  `not-operator` (`BASIS-API-043`) otherwise, neither with any diagnostics. No database user
  is upserted; the routes need no database at all.
* Rate limits: the usual read limit, then 60 requests a minute per operator (429 with
  `Retry-After`), counted across the three routes.
* **A host without evaluation data answers 404 `eval-data-unavailable`
  (`BASIS-API-046`)** on every route, with the reason in `detail`: `BASIS_EVAL_DATA_DIR` is
  not set (the default on every deployed host), it names a directory that does not exist,
  or the evaluation tooling (`evals`) is not installed in the image (the production image
  ships `basis/` alone). The answer is a problem, never an empty list, so "no runs" is never
  confused with "no data here". Client data never enters a deployment, so in practice the
  routes answer on the machine that holds `~/basis-eval-data`.
* The directories are scanned at most once per 30 s per API process (one scan at a time, off
  the event loop); the cached catalogue serves the pages and the details in between. A
  directory the registry cannot read (a manifest that is valid JSON but not a run manifest)
  is listed with `record.error` and every other field null; a manifest that is not JSON is
  listed from the run's other files with `record.error` saying so. One bad directory never
  hides the rest.
* `Cache-Control: no-store` on every answer.

## 2. The list

Newest first (by `started_at`, then name), keyset-paginated: `next_cursor` is null on the
last page, an undecodable cursor is 422 `invalid-cursor` (`BASIS-API-030`). `limit` 1 to
200 (default 50). `total` counts the runs matching the filters across every page.

Filters combine with AND; `component` repeats as OR (a workflow step lists several). Each
matches as the registry's CLI does (`backEnd/evals/registry.py`):

| parameter | matches |
|---|---|
| `component` | a prompt name or `name/vN`, `detector:x`, `embedder:x`, or the tree component (`room_inventory`, `room_inventory/v3`) |
| `model` | a requested or answered model; `M*` for a prefix |
| `labels` | a label file stem the run was scored against (`photo_components_v1`) |
| `dataset` | a scored dataset or a labelled one (`photo_counts`, `appraisal`) |
| `split` | the split recorded by the run or its latest scoring (`dev`, `regression`, `held_out`) |
| `status` | `ok`, `stopped`, `error`, or `unknown` (no status recorded) |
| `release` | the nearest release tag at run time (`v0.10.0`) |

```json
{
  "items": [
    {
      "run_id": "cHJlZGljdGlvbnMvYXJjaDI2X2NvdW50YWJsZXNfdjNfc29s",
      "name": "arch26_countables_v3_sol",
      "path": "predictions/arch26_countables_v3_sol",
      "kind": "predictions",
      "component": "photo_countables", "version": "v3",
      "harness": "evals.predict_photos",
      "started_at": "2026-10-20T10:00:00+00:00", "finished_at": "2026-10-20T10:12:00+00:00",
      "status": "ok",
      "results": {"passed": 50, "failed": 2, "test_error": 0},
      "git": {"sha": "bbbbbbbb…", "dirty": false, "release": "v0.9.0"},
      "prompts": [{"name": "photo_countables", "version": "v3", "sha256": "858a…", "calls": 52}],
      "components": ["photo_countables", "photo_countables/v3", "embedder:sentence-transformers/all-MiniLM-L6-v2"],
      "models_requested": ["gpt-6.1-sol"], "models_answered": ["gpt-6.1-sol"], "reasoning_effort": ["low"],
      "labels": {"photo_components": {"file": "photo_components_v1.json", "version": "v1", "sha256": "…", "scored_at": "2026-10-21T11:00:00+00:00"}},
      "split": "dev", "repeats": 3,
      "scores": {"photo_components": {"f1": 0.7406, "precision": 0.6616, "recall": 0.8411}},
      "headline": [{"dataset": "photo_components", "metric": "f1", "value": 0.7406}],
      "spend": {"cost_usd": 1.5, "cost_source": "ledger", "calls": 52,
                "tokens": {"input": 120000, "output": 4000, "cache_read": 0, "cache_creation": 0, "reasoning": 900},
                "duration_seconds": 720.0, "duration_source": "recorded"},
      "record": {"manifest": true, "backfilled": false, "format": 1, "error": null},
      "selection": {"status": "current", "reasons": ["photo_countables v3 on gpt-6.1-sol is the pipeline's (VISION_MODEL)"]},
      "report_sections": [15],
      "files": {"manifest": "/v1/ops/eval-runs/cHJl…/files/manifest", "report": "/v1/ops/eval-runs/cHJl…/files/report",
                "ledger": "/v1/ops/eval-runs/cHJl…/files/ledger", "console": "/v1/ops/eval-runs/cHJl…/files/console"}
    }
  ],
  "next_cursor": "WyIyMDI2LTEwLTIwVDEwOjAwOjAwKzAwOjAwIiwgImFyY2gyNi4uLiJd",
  "total": 187
}
```

* `run_id` is opaque and reversible (the relative path, base64url): the API never takes a
  file system path, and an id it did not issue (a path outside the directory, a directory
  that is not a run, the tree's links) is 404.
* **Every unknown is null.** A backfilled manifest writes `"unknown"` where the files did not
  say; the API reads that as null (the tested commit of an old run, the dirty flag, a prompt's
  sha256, the split). Nothing is guessed from a name.
* **A figure says where it came from.** `spend.cost_source` is `ledger` (summed from the run's
  recorded, priced calls: the price table of the day applied to the metered tokens) or
  `summary` (the runner's own `run.json` figure, what a backfilled manifest copies);
  `spend.duration_source` is `recorded` (the runner's clock at finish) or `file_times` (the
  backfill's estimate from the directory's file birth and modification times, which can span
  days). `spend.tokens` comes only from recorded calls and is null otherwise.
  `record.backfilled` says the manifest was reconstructed after the run
  (`evals.backfill_manifests`).
* `scores` are the metrics of the latest scoring of the run's own predictions, per dataset
  (`evals.run --predictions`; the older `eval.md`; an appraisal `score.json`). Non-finite
  values are null. **Runs scored against different label files or splits are not
  comparable**: a client groups by `labels` and `split` before putting runs in one table, as
  the developer page does. `headline` is the one metric per dataset the registry's table
  shows (`evals.registry.HEADLINE_METRICS`).
* `selection` is the run's standing against the pipeline **this API process is configured
  for** (the deployment's settings, read the way the activities read them:
  `basis.ops.eval_runs.pipeline_prompts`): `current` when every prompt the run names is the
  version the pipeline runs on a model a pipeline setting names for it; `alternative` when a
  version, a model or a prompt differs; `not_in_pipeline` when none of its prompts is run;
  `unknown` when the run names no prompt (a detector, an export, a backfilled run whose files
  did not say). `reasons` says which. The worker's effective routes are on
  `GET /v1/ops/health`; a run's standing is relative to the configuration, not a verdict on
  its quality.

## 3. The detail

`GET /v1/ops/eval-runs/{run_id}` returns the item plus `settings` (the evaluated system's
settings as the manifest recorded them: model, effort, prompt, threshold and version fields
only; never a credential or an address), `environment` (Basis version, price table, rules
table, embedder, cost table; the host name is withheld), `options` (the harness's command
line options), `sources` (a backfilled manifest's provenance per field), `notes` and `error`
(the run's error text when it failed, redacted). 404 for an id this host did not issue.

## 4. The record files

`GET /v1/ops/eval-runs/{run_id}/files/{name}` serves one of the four record files
(`backEnd/evals/record.py`) as an attachment, through the API with the operator check,
never as a static file:

| `name` | file | media type |
|---|---|---|
| `manifest` | `manifest.json` | `application/json` |
| `report` | `report.md` (the cases), else the older scoring's `eval.md` | `text/markdown` |
| `ledger` | `llm_calls.jsonl` (one `llm_calls` row per paid call) | `application/x-ndjson` |
| `console` | `console.log` (stdout, stderr and logging, keys redacted at write time) | `text/plain` |

Before it is sent, every file is redacted in memory: credential-named fields (`key`, `token`,
`secret`, `password`, `authorization`) and the host name are masked in the JSON files,
API-key-shaped strings and credential headers are masked everywhere, the data directory is
replaced by `<data dir>` and any `/Users/<name>` or `/home/<name>` by `~` in every path. The
payload files (`llm/*.request.json`, `llm/*.response.json`: prompts, images as references,
model answers) are never served. 404 when the run has no such file, 413
`BASIS-API-047` over 8 MiB (read it from the run directory on the host). The file name is in
`Content-Disposition` (`<run>.manifest.json`, `<run>.report.md`, `<run>.llm_calls.jsonl`,
`<run>.console.log`), exposed to browsers.

## 5. The developer page

Each step dialog's "Tests & results" section ends with a "Run records" block
(`web/developer/architecture.js`). `web/developer/workflow-content.json` maps every step to
its registry components (`components`; a review step has none, which the block says). While
diagnostics are not connected the block says so; connected, it reads
`GET /v1/ops/eval-runs?component=…` once per opening and shows one table per label set and
split (never one table across them), each figure with its source, "Not recorded" for every
null, the standing badge, and the four file buttons, which fetch through the authenticated
client and save the attachment (an `<a href>` cannot carry the bearer token). The feed's
states have their own sentences: untrusted origin, sign-in failure, timeout, network, 401,
403, 404 with `BASIS-API-046` (the no-data reason), 404 otherwise, 429, another status, a
malformed body, and a page with no run for the step's components ("runs of other components
exist on this host"). `web/tests/developer-run-records.test.cjs` covers them.

## 6. Settings

`BASIS_EVAL_DATA_DIR` (also what `evals.*` reads), `OPS_OPERATOR_UIDS`
(`backEnd/.env.example`). Nothing else; the routes call no model and write nothing.
