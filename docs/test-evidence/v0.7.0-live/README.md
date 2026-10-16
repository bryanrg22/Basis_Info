# v0.7.0-live: the real client study end to end through the running system

Run on 2026-10-16 on `main` at `8908ed6` (tag v0.7.0), then on the same code plus the fixes
below. Three runs: run 1 (Claude overrides, v0.7.0 as tagged) failed; run 2 (Claude overrides)
completed; run 3, after the OpenAI account was refilled, completed on the **real v0.7.0
defaults** with no model setting changed. The client's appraisal (20-page URAR) and 52 photos went through the API exactly as
the frontend sends them; nothing was called around the system. The client files, the photo
names and the study's values stay in the private data directory: the payloads here are
redacted (file names replaced by `photo-NN.jpg`, field values and dollar amounts removed),
and every score is computed by the eval harness on the private labels.

The scores, the comparison with the offline evaluation and the reading are EVAL_REPORT
section 22 (`docs/review_2026-10-15/EVAL_REPORT.md`).

## Setup

Local stack as in the S2 runbook (`docs/review_2026-10-15/S2_RUNBOOK.md`), on ports that do
not collide with other sessions: Postgres `pgvector/pgvector:pg16` on 5546, `temporal server
start-dev` (CLI 1.9.1, server 1.32.0, UI 2.54.1) on 7244 / UI 8244, `alembic upgrade head`
(0001 -> 0004), the worker, the outbox relay and the API (`uvicorn`, port 8110) as three
processes, Firebase emulator tokens. The reference corpus was ingested first with
`python -m basis.retrieval.ingest`: the four IRS documents of EVAL_REPORT section 17
(1,262 chunks, version 1).

Models. For runs 1 and 2 the OpenAI account was out of credit, so the OpenAI-routed defaults
were overridden with Claude models through the documented settings (table below). Run 3 used
the defaults of EVAL_REPORT 18 unchanged (OpenAI key set, Azure OpenAI unset so nothing could
fall back to Azure; `LLM_PRIMARY=openai`, `PROPERTY_CLASS=nonresidential`), on the code with
fixes 1 to 4.

| Setting | Default (EVAL_REPORT 18; run 3) | Runs 1 and 2 |
|---|---|---|
| `VISION_MODEL` (countables + surfaces, decomposed, effort low) | gpt-6.1-sol | `claude-opus-5-5` (output budget 1200, as section 14) |
| `VISION_COUNT_FUSION` | per_class (sol + Opus) | `off` (Opus is already the vision model) |
| `TEXT_MODEL` (extraction fallback), `EXTRACTION_REASONING_EFFORT` | gpt-6-luna, low | `claude-sonnet-5-5`, low |
| `LLM_PRIMARY` | azure | `anthropic` (Azure OpenAI and OpenAI keys unset, so nothing could fall back) |
| `PROPERTY_CLASS` | residential_rental | `nonresidential` (the engineer's 39-year building; section 17 ran it so) |
| Room step, classifier, SAM 3 | Sonnet 5.5 v3 + second look; Sonnet 5.5 low; Replicate | unchanged |
| Azure Document Intelligence | configured | configured (prebuilt-layout + key-value pairs) |

The worker logged the configuration in effect at startup: `photo analysis: claude-opus-5-5,
mode decomposed (photo_countables/v2, photo_surfaces/v1), reasoning effort low, output budget
1200`; `count fusion: off`; `room step: room_inventory/v3 on claude-sonnet-5-5 at effort low,
one-photo rooms per_photo, second look sensitive (surface_material/v3)`; `text steps:
extraction fallback claude-sonnet-5-5 at effort low (budget 800); classifier claude-sonnet-5-5
at effort low`; `azure_di=True llm_providers=anthropic localizer=ReplicateSam3Localizer`.

The client is `docs/review_2026-10-15/repro/live_client.py`: create the study, register each
document (`POST .../documents`), write the bytes where the API's `LocalStorage` reads them (a
`local-upload://` signed URL lives in the API process; on GCS this is the browser's PUT),
confirm (`POST .../complete`, which checks size and sha256), start, poll `GET .../workflow`
every 5 s, and at each gate approve with no corrections. At each gate it also reads the
database (read only) for the record, because no API route returns extraction fields, rooms or
inventories (finding 5 below).

## Timeline

### Run 1 (study `133c1e1a`, v0.7.0 as tagged): FAILED

| Time (UTC) | +s | What happened |
|---|---|---|
| 16:58:54 | 0 | study created; 53 uploads registered and confirmed in 61 s (106 mutations; 2 answered 429 and were retried with the same key) |
| 16:59:56 | 62 | `start` 202; stage `extracting` |
| 17:00:08-17:00:12 | 74-78 | `extract_fields` fails three attempts: `ProviderUnavailable: ModuleNotFoundError: No module named 'aiohttp'` (bug 1) |
| 17:02:07 | 193 | all 52 photos analyzed (one flagged: room `ambiguous`, by design) |
| 17:15:51 | 1,017 | SAM 3 localization of the 52 photos done (Replicate throttle); `reconcile_rooms` runs (18 rooms) |
| 17:16:25 | 1,051 | workflow **FAILED** (`Activity task failed`, the extraction's error); the study stays at `extracting`, status `processing` |
| 17:17 | | `POST .../start` on the failed study answered **202** with the dead run's ids (bug 2) |

Spent before failing: $2.67 of the $25 budget ($2.55 Anthropic, $0.12 Replicate), all of it
wasted: a failed study cannot be restarted and its outputs are not reachable through a gate.

### Run 2 (study `16522525`, the worker with fix 1): COMPLETED

| Time (UTC) | +s from start | Stage | Waiting / work |
|---|---|---|---|
| 17:17:40 | -61 | uploads | 53 documents registered and confirmed in 61.6 s (2 x 429, retried) |
| 17:18:41 | 0 | `start` 202, `extracting` | photos (concurrency 5), appraisal ingest and extraction in parallel |
| 17:18:53 | 12 | | `ingest_appraisal` done (85 chunks of the appraisal) |
| 17:20:15 | 94 | | `extract_fields` done (Azure DI 82 s, one fallback call) |
| 17:20:39 | 118 | | 52 photos analyzed (mean 10.9 s, max 20.6 s per photo; 1 flagged `ambiguous`) |
| 17:30:32 | 711 | | SAM 3 done on 52 photos (705 s; 91 HTTP 429 from Replicate, 4 activity retries, every photo `done`) |
| 17:31:01 | 740 | **appraisal_review** | `reconcile_rooms` done (29 s, 21 rooms, 7 reconciliation calls, 18 material looks); gate reached |
| 17:31:07 | 746 | | approved (1.0 s after the client saw the gate); relay delivered it at 17:31:08 |
| 17:31:08 | 747 | **rooms_review** | gate reached; objects endpoints exercised; approved at 17:31:45 (37 s: the client script crashed on the objects payload and was resumed; not a system delay) |
| 17:31:46 | 785 | `assets_processing` | relay delivered; `process_assets` 167 s (203 assets, 292 classifier calls) |
| 17:34:33 | 952 | **assets_review** | gate reached; evidence read (3 pages, 203 items); approved at 17:34:37, delivered 17:34:38 |
| 17:34:39 | 957 | `exporting` -> **completed** | export `rev-1.json` (sha256 `6b9a6e91...`), 13 sections |

Wall time: 15 min 57 s from `start` to completion (17 min with the uploads), of which the
engineer's three approvals took 40 s and SAM 3 under Replicate's throttle 11 min 45 s. The
appraisal gate waited 646 s after the extraction it reviews had finished (finding 4).

Relay: 3 outbox rows, all `delivered` on the first attempt, 0.9 to 1.8 s after the API
answered 202. Temporal history: 752 events; `analyze_photo` 52 completed on the first attempt;
`localize_photo` 52 completed, 4 after a retry (`LocalizerUnavailable: POST
https://api.replicate.com/v1/predictions returned 429 (after 6 attempts)`); every other
activity completed on its first attempt.

### Run 3 (study `c078e055`, the real defaults, fixes 1 to 4): COMPLETED

Worker startup: `photo analysis: gpt-6.1-sol, mode decomposed (photo_countables/v2,
photo_surfaces/v1), reasoning effort low, output budget 6000`; `count fusion: per_class
(claude-opus-5-5 at effort medium, table section15.4/2026-10-16)`; `text steps: extraction
fallback gpt-6-luna at effort low (budget 8000); classifier claude-sonnet-5-5 at effort low`;
`llm_providers=openai+anthropic`. Which model answered each call, from the recorder
(`payloads/run3_defaults/models_by_step.json`); no other model appears in the study's calls:

| Step | Provider, model, prompt | Calls | Cost |
|---|---|---|---|
| per-photo countables | openai `gpt-6.1-sol` `photo_countables/v2` | 52 | $0.469 |
| per-photo surfaces | openai `gpt-6.1-sol` `photo_surfaces/v1` | 52 | $0.315 |
| count fusion | anthropic `claude-opus-5-5` `photo_countables/v2` | 53 (one photo retried) | $1.418 |
| extraction fallback | openai `gpt-6-luna` `appraisal_field_fallback/v1` | 1 | $0.0004 |
| room inventory | anthropic `claude-sonnet-5-5` `room_inventory/v3` | 10 | $0.270 |
| material second look | anthropic `claude-sonnet-5-5` `surface_material/v3` | 15 | $0.077 |
| classification | anthropic `claude-sonnet-5-5` `classify/v1` | 277 | $3.078 |
| SAM 3 | Replicate, version `1bf97763...` | 52 | $0.110 |

| +s from start (17:56:22 UTC) | Stage | Work |
|---|---|---|
| 0 to 310 | `extracting` | 52 photos (three calls each, mean 29.2 s, no retry); ingest 11 s; extraction 87 s (Azure DI + one luna call), confidence 0.6, 40 fields |
| 16 to 649 | | SAM 3 (96 HTTP 429s, 6 activity retries, 52 of 52 `done`, 666 instances) |
| 681 | **appraisal_review** | `reconcile_rooms` 32 s: **14 rooms**; approved after 1.3 s |
| 685 | **rooms_review** | objects: tap 201, delete 200; approved after 1.4 s |
| 693 to 868 | `assets_processing` | 192 assets (77 rules, 115 classifier), 175 need review |
| 868 | **assets_review** | evidence read; approved after 1.4 s |
| 874 | **completed** | export sha256 `2a036a22...` |

14 min 34 s from `start`, $5.74 (Anthropic $4.84, OpenAI $0.78, Replicate $0.11), equal to
the workflow's budget ($25 - $19.27). Count fusion: 51 photos fused, 1 fell back to sol's
counts because Opus answered an item with count 0 on both attempts and the whole answer
failed validation (bug 5). Four photos flagged: that one and three `ambiguous` room types.

## What each gate showed (run 2; run 3 showed the same kinds of content)

| Gate | Through the API | In the database (no API route) |
|---|---|---|
| appraisal_review | `GET .../workflow`: `awaiting: appraisal_review`, `completed_photos: 52`, one flagged photo, `budget_remaining_usd` 22.34 (`payloads/workflow_status_appraisal_review.json`). `GET /v1/studies/{id}`: documents and stage. No route returns the extracted fields. | 39 fields with page and source (38 Azure DI, 1 model fallback), `method azure_di+regex`, `overall_confidence 0`, `needs_review true`: the critical effective date was dropped as "not a date" (bug 3); `site.area_sqft = 1` for a site printed `1.39 ac` (bug 4). `payloads/gate_appraisal_review_fields.json` (values redacted). |
| rooms_review | `GET .../workflow` (`awaiting: rooms_review`). `GET .../photos/{id}/objects` on all 52 photos: 648 SAM 3 instances, 257 uncounted candidates. `POST` a tap on an uncounted `exterior_door` candidate: 201, a manual instance with the candidate's mask (`derived_from` set); `DELETE`: 200, the counts return to the model's (`payloads/photo_objects_exercise.json`). | 21 rooms by the model's room identity (type, floor, ordinal): 6 reconciled by `room_inventory/v3` (7 calls), 14 one-photo rooms passed through, 1 Unassigned (the flagged photo); 201 inventory rows (`payloads/gate_rooms_review_inventories.json`). |
| assets_review | `GET .../evidence` (limit 100, 3 pages): 203 assets, each with its component, room, photos with localization status and instances, and citations with the cited passage (`payloads/evidence_endpoint.json`: summary and two items). 261 citations, all verified; 186 assets `needs_review`. | 203 assets (80 rules table, 123 classifier), buckets y39 107 / y5 75 / y15 13 / y7 8; 106 flagged `COST_ROW_MISSING`. |

Localization per photo (`payloads/localization_per_photo.json`): 52 of 52 `done`, 648
instances, 0.2 to 20.7 s of prediction each (mean 2.9 s); the rest of the 705 s was waiting
on Replicate's 6-predictions-per-minute throttle.

## Bugs found and fixed (each its own commit, with tests)

| # | Bug | Effect in the live run | Fix |
|---|---|---|---|
| 1 | `aiohttp` was not installed: the Azure DI async client builds an aiohttp transport when constructed | every extraction with Azure DI configured failed; the study failed after 17 min (run 1) | `951f294`: `azure-core[aio]` dependency; a test constructs the real async client (the unit tests faked the client, so they could not see it) |
| 2 | `POST /start` on a study whose workflow failed answered 202 with the dead run | the frontend is told the study started; it stays "processing" forever | `a03a4fa`: 409 `workflow-closed` for a failed, terminated, timed-out or cancelled execution (a completed one stays 202); verified live: run 1 -> 409, run 2 -> 202 |
| 3 | dates printed `M/D/YY` were rejected | the fallback's answer for the critical effective date (`1/29/25`) was dropped; extraction confidence 0 | `9e9e523`: two-digit years read with the century that keeps the date at most a year in the future |
| 4 | `site.area_sqft` ignored the unit and cut decimals | `1.39 ac` stored as 1 square foot, no error | `4c41d39`: acres converted at 43,560 sq ft per acre |
| 5 | a photo component the model counted 0 failed the whole answer | run 3: one photo lost its fused counts (Opus, both attempts) | `d55e22f`: a 0-count item is dropped (it means none visible); negative counts still fail |

Fixes 3 and 4 were checked on the real appraisal through the production extraction path
(Azure DI, regex, Sonnet fallback; $0.013 of model spend): the effective date is read as
2025-01-29, the site area as 60,548 sq ft, 40 fields, no validation issue, confidence 0.6
(was 0; still below the 0.90 review threshold, so the extraction still goes to review).

## Findings not fixed here (decisions or larger work)

1. **Room grouping differs from what was measured.** Production groups photos by the vision
   model's room identity (type, floor, ordinal; `basis/vision/rooms.py`); EVAL_REPORT 16
   grouped by file name plus the engineer's room aliases (`group_photos`), which only the
   eval runners use. Live: 21 rooms for 33 labelled; one "Bedroom" and one "Bathroom" hold
   several real rooms. The same live per-photo outputs grouped the section 16 way score room
   count MAE 0.40 against 0.68 as shipped (EVAL_REPORT 22.3).
2. **Extraction covers 39 of the 75 labelled fields.** Every extracted value was right
   (23 of 23 labelled ones), but rooms, bedrooms and bathrooms (critical in the labels) are
   never extracted, and the fallback is asked only for the six fields the catalogue marks
   critical. Section 17.1's 0.916 measured a different task (one call for all 75 fields).
3. **An extraction failure is noticed only after the photo stage and fails the study.** The
   workflow awaits the appraisal task after photos, localization and rooms (`study_workflow.py`
   `_lifecycle`), so run 1 spent 17 minutes and $2.67 before failing, with no way to resume.
4. **The appraisal gate waits for SAM 3.** Extraction was done at +94 s; the gate opened at
   +740 s, after localization and the room step, which the appraisal review does not need.
5. **No read route for gate contents.** The API exposes workflow status, photo objects and
   evidence, but not the extracted fields (with page citations), rooms or inventories, or a
   plain asset list; the frontend cannot show what the first two gates review.
6. **92% of assets go to review**, 106 of 203 for `COST_ROW_MISSING`: the static cost table
   has no row for windows, interior doors, finishes and many other labels.
7. `PROPERTY_CLASS` is a deployment setting, not a study attribute; the API reports version
   `0.4.0` at v0.7.0.
8. The private label files were regenerated by another session at 10:07 local time during
   this run (new labels); this run is scored on a set rebuilt from the backups of the label
   builders, which reproduces section 16.5 exactly (0.34 / 0.72 / 0.90 / 31 of 39).

## Cost

| Item | Provider | Recorded |
|---|---|---|
| Run 2: per-photo calls (105 Opus calls) | Anthropic | $2.22 |
| Run 2: room step (7 + 18 Sonnet calls) | Anthropic | $0.29 |
| Run 2: extraction fallback (1 Sonnet call) | Anthropic | $0.013 |
| Run 2: classification (292 Sonnet calls for 123 assets) | Anthropic | $3.27 |
| Run 2: SAM 3 (52 predictions) | Replicate | $0.148 |
| Run 2 total (the workflow's own budget agrees: $25 - $19.07 = $5.93) | | **$5.95** |
| Run 1 (failed) | Anthropic $2.55, Replicate $0.12 | $2.67 |
| Room-grouping isolation runs (22.3) and the extraction recheck | Anthropic | $0.53 |
| Run 3 (defaults) | Anthropic $4.84, OpenAI $0.78, Replicate $0.11 | $5.74 |
| Run 3 grouping isolation run | Anthropic | $0.32 |
| Azure DI prebuilt-layout, 3 analyses of 20 pages | Azure | not recorded by the pipeline (list price about $10 per 1,000 pages: about $0.60, not verified against the bill) |
| **All** | | **about $15.8** |

## Files

| File | What it is |
|---|---|
| `payloads/workflow_status_*.json`, `workflow_final.json` | `GET /v1/studies/{id}/workflow` at each gate and at the end (ids only) |
| `payloads/study_get_assets_review.json` | `GET /v1/studies/{id}` (file names replaced, sha256 removed) |
| `payloads/gate_appraisal_review_fields.json` | the extraction at the first gate: keys, cited pages, sources, confidences (values redacted) |
| `payloads/gate_rooms_review_inventories.json` | rooms and their inventories at the second gate |
| `payloads/photo_objects_exercise.json` | GET, POST (tap) and DELETE on one photo's objects |
| `payloads/evidence_endpoint.json` | the evidence endpoint at the third gate: totals and two items |
| `payloads/localization_per_photo.json` | SAM 3 status, instances and seconds per photo |
| `payloads/run3_defaults/` | run 3: status at each gate and at the end, the model of every step (`models_by_step.json`), count fusion per photo, localization per photo |
| `screenshots/temporal_ui_workflows.png` | Temporal UI: runs 3 and 2 COMPLETED, run 1 FAILED |
| `screenshots/temporal_ui_run3_defaults_completed.png` | run 3: input, result (export sha256, four flagged photos), history |
| `screenshots/temporal_ui_run2_completed.png` | run 2: input, result (export sha256, the flagged photo), event history (752 events, the `decide` updates) |
| `screenshots/temporal_ui_run1_failed.png` | run 1: the failure (`No module named 'aiohttp'`) and the end of its history |
| `screenshots/api_health.png`, `api_openapi_docs.png` | the running API: `/health` (db and Temporal ok) and its route list |

Reproduce: the client (`repro/live_client.py`), the exporter (`repro/live_export.py`) and
the commands in EVAL_REPORT 22.
