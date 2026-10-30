# Study workflow diagrams — October 27, 2026

Code snapshot: `main`, `fcf60ac`. These diagrams replace the October 17 diagram for explaining the current backend's default new-study path. They do not claim a live deployment or that the existing frontend implements all review screens. Application code was not changed and provider calls/tests were not run for this documentation task. Both PNG renders were visually inspected.

## Owning sources

- `backEnd/basis/workflow/study_workflow.py:427`: lifecycle; appraisal and photos run concurrently; appraisal approval and completed photo analysis precede rooms review; assets review precedes export.
- `backEnd/basis/workflow/study_workflow.py:495`: room-grouping approval precedes component preparation and the separate components review.
- `backEnd/basis/workflow/study_workflow.py:515`: components preparation waits for localization outcomes. Optional localization can be skipped or fail; the study can continue with that recorded outcome.
- `backEnd/basis/workflow/study_workflow.py:534`: photo processing starts in the background while appraisal extraction runs. Normal operation can present appraisal review before the photo task finishes. Provider account failures can hold the stage until retried.
- `backEnd/basis/workflow/contracts.py`: `components_gate=True` for new workflow inputs. Existing histories can follow earlier patched paths.
- `backEnd/basis/api/routes/workflow.py:129`: workflow inputs and configured concurrency.
- `backEnd/basis/extraction/pipeline.py:79`: PDF text/tables, configured Azure DI, merge/validation, missing-field LLM fallback and review flags. AI fallback requires a configured provider.
- `backEnd/basis/activities/analyze_photo.py`: structured per-photo room/component analysis, configured count fusion, persistence of the results. Five photos is an example input count, not a product limit or number of model calls (decomposed mode makes multiple calls per photo).
- `backEnd/basis/activities/reconcile_rooms.py`: room inventory from per-photo observations; default reconcile mode; one-photo reuse, multiple-photo reconciliation, material checks; fallback outputs are flagged. This reduces duplicate observations but is not a guarantee of correct counts.
- `backEnd/basis/activities/localize_photo.py`: SAM 3 boxes/masks; configuration-dependent, best-effort background localization.
- `backEnd/basis/activities/process_assets.py`: rules/cache/residual classification, takeoff estimation, costs, citations and warnings. Measured area is not always available; typical room areas and configured cost-table coverage are material limits.
- `backEnd/basis/activities/export_report.py:68`: approved revision's extraction, rooms, photos, components, assets and evidence assembled as canonical JSON and assets CSV, then saved through the storage adapter. This is not a finished PDF report.

Render sources: `generate-study-diagrams.py`. Generate with Python, then use `rsvg-convert` on the resulting SVGs.

## System views added in response to the follow-up

- Diagram 1: `system-design-2026-10-27` — application/service structure with the study workflow collapsed into one box.
- Diagram 2: `study-detail-2026-10-27` — the retained business workflow diagram.
- Diagram 3: `study-system-design-2026-10-27` — workflow implementation, activity modules and supporting dependencies.
- Additional owning sources inspected: `web/README.md`, `web/live/`, `docker-compose.yml`, `docker-compose.web.yml`, `basis/activities/deps.py`, `basis/worker/main.py`, `basis/llm/providers.py`, `basis/retrieval/__init__.py`, and `basis/activities/ingest_appraisal.py`.
- Correction to the earlier architectural inference: `web/` is the new HTML/CSS/JavaScript application with a live `/v1` integration. `frontend/` is the retained Next.js application. The current Compose file launches the new API, worker, relay, PostgreSQL and Temporal stack. Describing the current frontend/Compose as exclusively legacy would be incorrect.
- `docs/deploy/STAGING.md` documents a GCP staging topology and says it is parked. No live cloud query was made, so these diagrams show connected application code and configurable storage/provider adapters, not currently running cloud resources.
