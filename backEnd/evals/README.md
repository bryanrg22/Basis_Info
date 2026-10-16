# Evaluation harness

Scores Basis against labeled datasets and writes a Markdown report (and, when
`DATABASE_URL` is set, `eval_runs` / `eval_results` rows). BUILD_PLAN sections 5, 6.7
and 13.2; S3_SPEC section 5.

```bash
cd backEnd
uv run python -m evals.run --dry-run --report /tmp/eval.md          # synthetic smoke
BASIS_EVAL_DATA_DIR=/path/to/package uv run python -m evals.run --split dev --repeats 5
uv run python -m evals.run --split dev --predictions out/            # score saved outputs
```

Client data never enters the repository. Real datasets live under `BASIS_EVAL_DATA_DIR`
(mirrored to the private bucket); only the orchestrator runs them. `--dry-run` builds
the first six datasets from `tests/fixtures/synthetic/` (for `room_inventory`, every fixture
photo is its own room, so the dry-run baseline scores perfectly: it exercises the path,
not the method). A seventh, `rule_citations`, has no file (see below).

## Systems

Each dataset is scored by saved predictions (`--predictions DIR/<dataset>.json`) when
present, otherwise by a deterministic baseline: `printed_text` (fields as printed),
`filename` (photo rooms and components from file-name words), `rules_table`
(`basis.domain.rules.lookup`, a miss abstains), `bm25_pages` (BM25 over one chunk per
page of `<data dir>/corpus/*.pdf`, doc id = file stem), `per_photo_max` (room
inventories: the labelled per-photo components of `photo_components_v1.json` merged per
room by label, count = the largest per-photo count; the "no reconciliation" floor) and
`page_text` (rules-table citations checked against corpus page text) and `gold_inventory`
(`report_rows`: the human room inventory scored as if the pipeline had produced it, with
materials from the per-photo labels; the ceiling the vocabulary and the labelling
conventions allow). Model-backed systems are run by the orchestrator and scored through
`--predictions`.

## Dataset files

Every item has `split` (`dev` | `regression` | `held_out`, default `dev`) and, where it
applies, `document`: the cluster for clustered standard errors (default `package`).

### `appraisal_fields_v1.json`

```json
{"schema_version": 1, "documents": [{
  "document": "SUBJECT_URAR", "split": "dev",
  "fields": [
    {"field": "reconciliation.final_market_value", "expected": 425000, "type": "currency",
     "relative": 0.005, "critical": true, "printed": "$425,000"},
    {"field": "improvements.year_built", "expected": 1998, "type": "int"},
    {"field": "subject.property_address", "expected": "1290 Example Way", "type": "text"}
  ]}]}
```

`type` is `text | int | float | currency | percent | date | bool`. Default tolerances:
text exact after case/whitespace folding, `int` +/-1, `float` +/-0.01, `currency` +/-0.5%,
others exact; override with `absolute` / `relative`. Predictions:
`{"<document>": {"<field>": value}}`.

Metrics: exact and normalized accuracy over fields (repeat 1), `critical_accuracy` over the
fields labelled `critical` (the misses named in the details), and with repeats `pass^k` /
`pass^1` (the mean over repeats) for all fields and `critical_pass^k` / `critical_pass^1`.
Predictions come from `python -m evals.predict_fields --out DIR --model M
[--reasoning-effort low] [--repeats 3]`: every labelled field in one call with the released
`appraisal_field_fallback` prompt and the production request format, the whole document
(`read_pdf` page text) as context, the production verbatim guard applied
(`appraisal_fields.unguarded.json` keeps the answers before it).

### `photo_rooms_v1.json`

```json
{"schema_version": 1, "photos": [
  {"file": "1st Floor Kitchen photo 1.jpg", "document": "package", "room_type": "kitchen",
   "floor": 1}]}
```

`room_type` includes `exterior`, `mechanical` and `ambiguous` (scored as abstention).
Predictions: `{"<file>": "<room_type>"}`.

### `photo_components_v1.json`

```json
{"schema_version": 1, "photos": [
  {"file": "...jpg", "components": [
    {"label": "kitchen_cabinet", "material": "wood", "attachment": "built_in", "count": 6}]}]}
```

`other` labels are excluded from scoring. Predictions: `{"<file>": ["label", ...]}`.

Predictions come from `python -m evals.predict_photos` (one call per photo with
`photo_analysis`, or one narrow prompt family with `--prompt-name photo_countables` /
`photo_surfaces`, `--prompt vN` for a version other than the latest). Decomposed runs are
combined with `python -m evals.merge_photo_runs --runs <countables> <surfaces> --out <dir>`
(label sets unioned per photo, the largest count per label, the room type of the run named
by `--room-from N`, default the first) and
the merged directory scores like any single run (EVAL_REPORT section 11). The component
merge is `basis.vision.merge.merge_components`, the same function the pipeline's
`VISION_PHOTO_MODE=decomposed` step runs, so a merged run scores the step as shipped.
After a
`--model` run, check `run.json` `models_reported`: the provider pool falls back to the
secondary provider per call, so a rate-limited primary silently mixes models into a run
(only within a model family: a `claude-*` model is served by the Anthropic provider alone,
with `ANTHROPIC_API_KEY` set, and never falls back to OpenAI; EVAL_REPORT section 12).
`--reasoning-effort` sets the OpenAI reasoning models' `reasoning_effort` and Claude's
`output_config.effort` (`low`..`max`; without it Claude runs at `ANTHROPIC_EFFORT`, default
`low`); `run.json` `reasoning_effort` records what ran.

### `assets_v1.json`

```json
{"schema_version": 1, "assets": [
  {"id": "report-12", "component": "carpet", "material": null, "attachment": null,
   "property_class": "nonresidential", "section": "1245", "bucket": "y5",
   "amount_usd": 4210.0, "document": "package"}]}
```

`section` and `bucket` may each be null (not labeled). Predictions:
`{"<id>": {"section": "1245", "bucket": "y5"}}`, optionally with
`"citations": {"claimed": n, "verified": m}`.

Metrics: section and bucket accuracy, macro-F1 and coverage (repeat 1; a missing answer
abstains), with repeats `bucket_pass^k` / `bucket_pass^1`, and when the predictions carry
citation counts `citation_validity` (verified over claimed citations, Wilson interval) and
`rows_with_verified_citation`. Predictions come from `python -m evals.predict_assets --out
DIR --model M [--reasoning-effort none] [--max-tokens 6000] [--repeats 3]`: every row through
the production model path (`basis.classify.router.classify_with_model`: the residual tool
loop with the released `classify` prompt, citation verification, the review policy; the
rules table and the cache are skipped), against a BM25 store of the IRS PDFs in
`<data dir>/corpus` (`--corpus-glob`, default `IRS_*.pdf`) cut into the ingest's 400-token
windows. A failed loop is scored as what production writes (1250, the building bucket);
`run.json` keeps the provider errors. OpenAI reasoning models accept function tools on
Chat Completions only at `reasoning_effort` `none` (gpt-6.1-sol not at all), EVAL_REPORT
section 17.

### `retrieval_qrels_v1.json`

```json
{"schema_version": 1, "queries": [
  {"id": "q01", "query": "asset class 57.0", "codes": ["57.0"],
   "relevant": [{"doc_id": "IRS_REV_PROC_87_56", "page": 12, "grade": 2}]}]}
```

Labeled at document + page (not chunk id), so re-chunking does not invalidate labels.
Grades: 2 exact match, 1 paraphrase, 0 judged not relevant. Predictions:
`{"<query id>": ["DOC_ID:page", ...]}` in rank order.

### `room_inventory_v1.json`

```json
{"schema_version": 1, "rooms": [{
  "room_id": "1_kitchen", "name": "1st Floor Kitchen", "floor": 1, "room_type": "kitchen",
  "photos": ["21_1st Floor Kitchen photo 1.JPG", "22_1st Floor Kitchen photo 2.JPG"],
  "document": "package", "split": "dev",
  "objects": [
    {"label": "kitchen_cabinet", "count": 1,
     "photos": ["21_1st Floor Kitchen photo 1.JPG", "22_1st Floor Kitchen photo 2.JPG"]},
    {"label": "sink", "count": 1, "photos": ["21_1st Floor Kitchen photo 1.JPG"]}]}]}
```

The human inventory of each room: every distinct physical object once, with the photos it
is visible in. `room_id` follows `basis.vision.rooms.name_hint` (drop the extension, a
leading `NN_` index and a trailing `photo N`; `<n>st/nd/rd/th Floor` gives token `n`,
`Basement` token `b`, anything else `x`; then the rest as a lowercase `_` slug), with views
merged into their room by hand where needed (the same mapping as `predict_rooms
--aliases`). Several objects with one label are distinct objects and their counts add up;
an object's `photos` must be among the room's `photos`; room ids are unique; `other` is
excluded from scoring. Predictions: `{"<room_id>": {"<label>": count}}` (labels are
normalized; a room missing from the predictions has an empty inventory).

Metrics, per (room, label) over the union of gold and predicted labels of each room
(missing = 0): count MAE, exact-count rate and signed bias = mean(predicted - gold)
(positive = over-counting, the duplicate failure), SE clustered by room; per room: exact
inventory (Wilson interval), label-set precision/recall/F1 (counts ignored; nothing
predicted -> precision 0; two empty inventories agree), SE clustered by document; pass^k
over repeats for the exact inventory. Predictions come from `python -m evals.predict_rooms`
(`room_inventory.json`, the reconciliation call; `room_inventory.heuristic.json` is the
per-photo-plus-merge baseline on the model's own per-photo output, scored by copying it to
`room_inventory.json` in another predictions directory).

### `detection_boxes_v1.json`

```json
{"schema_version": 1, "images": [{
  "file": "IMAGE_ID.jpg", "width": 1024, "height": 683,
  "document": "IMAGE_ID", "split": "dev",
  "verified": ["door", "electrical_outlet", "window"],
  "boxes": [
    {"label": "window", "x1": 0.71, "y1": 0.01, "x2": 0.80, "y2": 0.18, "group_of": false},
    {"label": "door", "x1": 0.60, "y1": 0.34, "x2": 0.81, "y2": 0.89, "group_of": false}]}]}
```

One box per component instance, normalized to the image. `label` is a component label
or `door` (Open Images does not split interior and exterior doors; `door` is scored
against the union of `interior_door` and `exterior_door` predictions). `group_of` marks a
box around several instances (Open Images `IsGroupOf`): the highest-scoring prediction
lying mostly inside it (intersection over the prediction >= 0.5) is one true positive,
the others are ignored, and an unhit group box is not a miss. `verified` lists the labels
exhaustively annotated in the image (boxed, or verified absent); `null` means every label
is. Predictions of an unverified label are ignored, as in the Open Images protocol.
There is no deterministic baseline and no synthetic copy (the dry run reports it
missing). Predictions: `{"<file>": [{"label": "window", "score": 0.91, "x1": 0.70,
"y1": 0.01, "x2": 0.80, "y2": 0.18}]}`.

Metrics: per-class AP@0.5 (VOC all-point interpolation; in score order a prediction
takes the gold box of highest IoU if it is still free, a duplicate is a false positive)
and mAP over classes with a positive; at `--detection-threshold` (default 0.5): precision
and recall, and per (image, class) over the union of gold and predicted classes (classes
with a group box in the image skipped, their count is unknown) count MAE and signed bias
(predicted - gold) with SE clustered by document, overall and per class; with repeats,
pass^k of the exact count.

Building it and producing predictions (from `backEnd/`):

```bash
# Open Images V7 validation subset: CSVs cached, 25 images per label, images downloaded
uv run python -m evals.openimages --out ~/basis-eval-data/openimages --per-label 25
# SAM 3 on Replicate (REPLICATE_API_TOKEN), low threshold so AP sees the whole curve
BASIS_ENV=dev uv run python -m evals.predict_boxes --dataset ~/basis-eval-data/openimages \
    --out out/sam3_oi --threshold 0.1 --tiling off
uv run python -m evals.run --data-dir ~/basis-eval-data/openimages \
    --predictions out/sam3_oi --detection-threshold 0.5 --no-db --report out/sam3_oi/eval.md
uv run python -m evals.render_masks --raw out/sam3_oi/instances_raw.jsonl \
    --photos ~/basis-eval-data/openimages/images --out out/sam3_oi/render
```

`evals.openimages` keeps every Open Images class whose display name is mapped
(`OI_LABELS`), skips rotated images, selects deterministically (ascending image id per
label until `--per-label` images contain it) and writes `MANIFEST.json` (sources, sha256
of every CSV and image, per-image author and licence for the CC BY 2.0 attribution).
`evals.predict_boxes` also runs on any photo directory (`--photos`), with the labels the
per-photo model named (`--phrases-from vlm --photo-raw <predict_photos>/photo_raw.jsonl`)
plus the always-prompted countable set; `--tiling on` adds 2 x 2 tiles for the countable
phrases, merged by mask NMS (`--merge-metric iou` or `ios`).

### `report_rows_v1.json`

```json
{"schema_version": 1, "rows": [
  {"id": "report-45", "system": "Ceramic Tile Flooring", "bucket": "y39", "cost_share": 0.012,
   "kind": "B", "labels": ["tile"], "materials": {"tile": ["ceramic", "porcelain", "stone"]},
   "material_required": true, "document": "package", "split": "dev"},
  {"id": "report-46", "system": "Cabinets / Millwork - Restrooms", "bucket": "y39",
   "cost_share": 0.003, "kind": "A",
   "labels": ["bathroom_vanity", "kitchen_cabinet", "built_in_shelving"],
   "room_types": ["bathroom"], "count_required": true},
  {"id": "report-19", "system": "Movable Shed", "bucket": "y7", "cost_share": 0.005,
   "kind": "A", "missing_label": "shed"},
  {"id": "report-27", "system": "Foundations", "bucket": "y39", "cost_share": 0.080, "kind": "C"}]}
```

The product-level checklist (COMPONENT_TAXONOMY.md section 1): one row per line of the
engineer's asset schedule, with the `assets` row's id and bucket, its share of the study's
cost (`cost_share`; the shares sum to 1) and the evidence rule the photo pipeline must
satisfy. `kind` A (countable objects) and B (surfaces: the material is the question) rows
are photo-derivable; `kind` C rows (foundations, framing, utilities, fire and data systems)
are not and are never a miss. A photo-derivable row is evidenced by any of `labels` in a
room of a type in `room_types` (null: any) and not in `exclude_room_types`, with, for a
label in `materials`, a reported material among the allowed ones (a constrained label with
no reported material does not evidence the row). `count_required` / `material_required`
say what the engineer needs beyond presence. A photo-derivable row with no label in the
vocabulary yet names the label it needs in `missing_label` and scores as a *vocabulary
gap*, never as a miss. Room types come from the `room_inventory` labels (the known room
identity), not from the model. Built from `assets_v1.json` and the taxonomy by the private
`work/build_report_rows.py`.

Predictions are a room inventory: `--predictions DIR` scores `DIR/room_inventory.json`
(the file `room_inventory` scores; `--inventory-file room_inventory.heuristic.json` scores
the heuristic merge instead, for both datasets), with materials per (room, label) read
from `DIR/room_raw.jsonl` (repeat 1 of the reconciliation call) or, for the heuristic file,
`DIR/rooms.json`; without either, no row gets a material.

Metrics: `row_coverage`, the share of photo-derivable rows evidenced (Wilson interval;
vocabulary gaps count as not evidenced: the product number) and
`row_coverage_in_vocabulary` (gaps left out: the model number); `cost_coverage`, the same
weighted by cost share over the photo-derivable rows, and `cost_coverage_of_study` over
all rows (the details carry the evidenced, missed, vocabulary-gap and not-photo-derivable
shares); `coverage:<bucket>` and `cost_coverage:<bucket>` per recovery bucket;
`count_present_rate` and `material_present_rate` over the rows that require one; the
vocabulary-gap and not-photo-derivable rates; and, with repeats, pass^k over the
photo-derivable rows (coverage stable across repeats). The notes list the missed rows,
the vocabulary gaps with the label each needs, and the rows photos cannot establish; the
report adds a per-row table with the rooms and labels that evidence each row. Checklist
scoring as in ExpertLongBench / CheckEval (one binary judgment per required item,
aggregated as recall, not-applicable items kept out of the denominator); the cost
weighting mirrors the IRS Cost Segregation ATG's requirement that a study's asset list
tie out to its total cost.
### `photo_counts` (derived from `detection_boxes`, no file)

Per photo, the true count of every verified class: the non-group boxes of that label,
zero when the class is verified absent. The door labels pool into one `door` class
(`door`, `interior_door`, `exterior_door` boxes; predictions of the three are summed),
because the count, not the interior/exterior split, is what the yardstick measures. On our
own photos every image carries the six verified classes, so every (photo, class) pair
counts, zeros included; Open Images images without a `verified` list contribute one class
per boxed label and no zeros. Predictions: `photo_counts.json` from `evals.predict_photos`
or `evals.merge_photo_runs` (`{"<file>": {"label": count} | {"repeats": [...]}}`); a
predictions directory without it is read from its `photo_raw.jsonl`.

Metrics, per (photo, class, repeat): count MAE (SE clustered by document), exact-count
rate, signed bias (> 0 over-counting), count precision and recall (min / over / under
counts pooled over pairs), and the same per class (`count_mae:window`, ...). This is the
number the engineer prices; the label-set metrics of `photo_components` say whether a
thing was listed at all.

### `rule_citations` (derived, no file)

Every `(doc_id, page, excerpt)` citation of the rules table (`basis.domain.rules.RULES`),
built from the code by `evals.datasets.rule_citations()`, so it is always present and is
scored whole on every split (it is a property of the table, not a sample). System
`page_text`: each citation is checked against the pymupdf text of page `page` of
`<data dir>/corpus/<doc_id>.pdf`, the checks `resolve_rule_citations` applies to the
chunk it finds: the document is in the corpus, the page exists, and
`basis.classify.verify.excerpt_supported` accepts the excerpt on that page (normalized
substring of at least 12 normalized characters). Reported: `resolved_fraction` over
citations (Wilson interval; SE clustered by rule), `rules_resolved` (rules whose every
citation resolves; any other rule is flagged `citation_unverified` in production), the
count per status (`resolved`, `excerpt_too_short`, `document_missing`,
`page_out_of_range`, `excerpt_not_on_page`), the unresolved rules, and the contested
rules (their hits go to review regardless). Skipped when the corpus directory holds no
PDFs. On `--dry-run` the synthetic corpus (`SYN_PUB_A..C`) holds none of the cited IRS
documents, so every citation is `document_missing` and the fraction is 0 by design: the
dry run exercises the path, the real corpus measures the table. Page-level checking is
a necessary condition for production, not a sufficient one: production also needs the
excerpt inside one stored chunk and that chunk in the top 10 of the BM25 search for the
excerpt (`docs/review_2026-10-15/RULES_RECITATION.md` records that check for the
current table).

For any predictions file, a value may instead be `{"repeats": [v1, v2, ...]}` with one
value per repeat; pass^k is computed over them.

```bash
# the engineer's report rows against a saved room inventory and its heuristic merge
uv run python -m evals.run --predictions ~/basis-eval-data/predictions/rooms_x --repeats 3 --no-db
uv run python -m evals.run --predictions ~/basis-eval-data/predictions/rooms_x \
    --inventory-file room_inventory.heuristic.json --no-db
```

## Metrics

`evals/metrics.py`, unit-tested against hand-computed values in
`tests/unit/evals/test_metrics.py`: field accuracy (exact and normalized), per-class
precision/recall/F1 with macro-F1, accuracy with a 95% Wilson interval, coverage and
selective accuracy under abstention, room-inventory count MAE / exact-count rate /
signed bias and per-room exact inventory and label-set P/R/F1, nDCG@10 (linear gains, log2 discount, as
`pytrec_eval`'s `ndcg_cut_10`), recall@5/10, MRR, the judged-hole rate, citation support
rate, the status of a page citation (`page_citation_status`), the standard error of a
mean clustered by document (CR1), pass^k (`C(c, k) / C(n, k)` per item), and detection
AP@0.5 / mAP with the Open Images group-of and verified-label rules, counts at a threshold
and pass^k of counts (`tests/unit/evals/test_detection_metrics.py`).
