# Backfilled run manifests

Written by `python -m evals.backfill_manifests` over `/Users/bryanramirez-gonzalez/basis-eval-data` on 2026-10-30: one `manifest.json` with `backfilled: true` in every run directory that had none, each field with its source, `unknown` where the files do not say (EVAL_REPORT section 42). Nothing was moved or deleted.

## Counts

| | runs |
|---|---|
| run directories found | 351 |
| already had a manifest (left alone) | 0 |
| backfilled | 351 (187 under `predictions/`, 164 under `appraisal/runs/`) |
| with a `run.json` summary | 326 |
| harness identified | 350 |
| prompts named (sha256 exact from the prompt manifest) | 263 |
| models named | 269 |
| git sha recorded (appraisal runner only) | 164 |
| release known from the recorded sha | 164 |
| label files known (from `eval.md`; sha256 at the time unknown) | 40 |
| terminal log kept (sibling `.log`) | 125 |
| cited by name or prefix in EVAL_REPORT | 112 |
| stopped by an account error | 0 |
| empty directories | 1 |

## Harness per run (from `run.json` keys or the files present)

| harness | runs |
|---|---|
| `docs/review_2026-10-15/repro/extraction_replay.py` | 2 |
| `evals.aggregate_runs` | 1 |
| `evals.appraisal.run` | 164 |
| `evals.combine_rooms` | 2 |
| `evals.fuse_counts` | 4 |
| `evals.manual_add` | 1 |
| `evals.merge_photo_runs` | 19 |
| `evals.predict_assets` | 12 |
| `evals.predict_boxes` | 13 |
| `evals.predict_boxes (probe)` | 1 |
| `evals.predict_detector` | 6 |
| `evals.predict_fields` | 7 |
| `evals.predict_marks` | 8 |
| `evals.predict_multiview` | 8 |
| `evals.predict_photos` | 42 |
| `evals.predict_points` | 3 |
| `evals.predict_rooms` | 36 |
| `evals.predict_surface_crops` | 4 |
| `evals.predict_tiles` | 2 |
| `evals.replay_rooms` | 4 |
| `evals.sam3_select` | 7 |
| `evals.verify_boxes` | 1 |
| `live study export (repro/live_export.py)` | 3 |
| `unknown` | 1 |

## Not recoverable

* **The exact commit of every non-appraisal run.** The runners did not record a git sha and the runs were made from feature branches; the manifest records `git.main_at_finish` (the `main` commit at the directory's last write, a lower bound) and `release_before` / `release_after` (the tags around that time). `dirty` is unknown for every backfilled run, the appraisal runs included.
* **The label files' content at scoring time.** Only the file names are known (from `eval.md`), so the version is, but `room_inventory_v1.json` and `LABEL_GUIDE.md` were edited on 2026-10-16 (`.bak-2026-10-16` copies exist); a score from before that edit cannot be tied to either content. Runs without an `eval.md` have no label record.
* **Per-call records and payloads.** The `MemoryRecorder` was discarded at exit; only the runners' own raw files (`photo_raw.jsonl`, `room_raw.jsonl`, `fields_raw.jsonl`, `calls.jsonl`) hold answers, and no request, token split, latency or response id.
* **The price table and rules table versions**, the settings in effect, the reasoning effort where `run.json` has none, and the exit status (a `run.json` present is read as a completed run because every runner writes it last).
* **Terminal output** for 226 runs: no log was kept.

## Runs whose harness could not be identified

`arch26_room_v1_gpt61sol_low_killed`

## Runs without a `run.json`

`arch26_det_gdino_select`, `arch26_det_llmdet_base_select`, `arch26_det_llmdet_large_invalid_boxes_select`, `arch26_det_mask2former_select`, `arch26_det_oneformer_select`, `arch26_det_owlv2_select`, `arch26_room_v1_gpt61sol_low_killed`, `live_e2e_v070`, `live_e2e_v070_defaults`, `live_e2e_v070_defaults_perphoto`, `live_e2e_v070_perphoto`, `manual_add_v1`, `rooms_decomposed_recmini2`, `rooms_decomposed_recmini3`, `rooms_v1_heuristic`, `rooms_v1_pp41_heuristic`, `s25_extraction_critical`, `s25_extraction_missing`, `s27_live`, `sam3_photos_tile_iou_v2`, `sam3_probe_v1`, `sam3_select_v1`, `vision_countables_v2_gpt41`, `vision_countables_v2_gpt6astra`, `vision_countables_v2b_gpt41`
