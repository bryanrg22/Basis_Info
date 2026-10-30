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

Corpus text (`--corpus-text`, `retrieval_qrels` and `rule_citations` only): `pymupdf`
(default; pymupdf's page text, independent of the ingestion parser, the historical
baseline) or `parser` (the production path: `basis.retrieval.parse.parse_pdf`, then
`basis.retrieval.chunk.chunk_document`, a page's chunks joined; what retrieval indexes and
citations are checked against). With `parser` the systems are named `bm25_pages_parser`
and `page_text_parser`. Use `parser` to measure a parser change (EVAL_REPORT 20).

Retrieval systems (`--retrieval-systems`, `retrieval_qrels` only; default `bm25_pages`):
a comma-separated list of `bm25_pages`, `bm25_chunks`, `vector_chunks`, `hybrid`, or `all`.
The chunk systems (`evals/retrieval.py`) parse and chunk the corpus with the production
path, embed every chunk with the production embedder (`EMBEDDING_MODEL`, MiniLM by default;
about 3 minutes on the six PDFs), rank the top 50 chunks per query and read them as
de-duplicated `doc_id:page` keys cut at 10. `bm25_chunks` is the store's BM25 index,
`vector_chunks` exact cosine, `hybrid` `basis.retrieval.hybrid.hybrid_search` with the
configured fusion (`RETRIEVAL_FUSION`, `RETRIEVAL_BM25_WEIGHT`). Two or more systems add a
`paired_bootstrap` result: per pair and metric, the mean per-query difference, a 95%
percentile-bootstrap interval, a sign-flip randomization p and wins/losses/ties.
`--fusion-sweep` (with `hybrid`) adds weighted fusion at BM25 weights 0.0-1.0 and RRF, and
the leave-one-out score of choosing the weight on the other queries (EVAL_REPORT 22).
`hole_rate_at_10` is the share of top-10 pages with no judgment for that query.
The chunk window is `RETRIEVAL_CHUNK_TOKENS` / `RETRIEVAL_CHUNK_OVERLAP` (default 400 / 80)
and the embedder `EMBEDDING_MODEL` (any of `basis.retrieval.embed.MODEL_SPECS`, which adds
the model's query and passage prefixes). With `hybrid`, two `rule_citations` results follow:
`resolve_bm25` and `resolve_hybrid`, every rules-table citation through
`basis.classify.router.resolve_rule_citations` over the same chunks with the classifier's
two searches (`RETRIEVAL_SEARCH`; EVAL_REPORT 25).

`python -m evals.predict_assets --store parser [--search hybrid]` runs the classifier
against the production chunks (parse, chunk at the configured window, embed) with the
store's BM25, or with `Retrieval.hybrid` as production does under `RETRIEVAL_SEARCH=hybrid`.
The default `--store pymupdf` is the historical store (pymupdf page text, a simple BM25).

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
An account error (no credit or quota, a bad key) never falls back: every runner that calls
a model stops at the first one with exit code 3 and a `run.json` that says
`"complete": false` and why (`evals/account_stop.py`, EVAL_REPORT 26.12); the files already
written are partial.
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
an object's `photos` must be among the room's `photos`; room ids are unique; `other` and
loose furniture (`furniture`, `chair`, `desk`, `filing_cabinet`: listed for the FF&E report
row, EVAL_REPORT section 19) are excluded from scoring (`photo_components` excludes them
too; `report_rows` reads them). Predictions: `{"<room_id>": {"<label>": count}}` (labels are
normalized; a room missing from the predictions has an empty inventory).

Metrics, per (room, label) over the union of gold and predicted labels of each room
(missing = 0): count MAE, exact-count rate and signed bias = mean(predicted - gold)
(positive = over-counting, the duplicate failure), SE clustered by room; per room: exact
inventory (Wilson interval), label-set precision/recall/F1 (counts ignored; nothing
predicted -> precision 0; two empty inventories agree), SE clustered by document; pass^k
over repeats for the exact inventory. Predictions come from `python -m evals.predict_rooms`
(`room_inventory.json`, the reconciliation call; `room_inventory.heuristic.json` is the
per-photo-plus-merge baseline on the model's own per-photo output, scored with
`--inventory-file room_inventory.heuristic.json`). The runner's per-room options (EVAL_REPORT
section 16): `--room-prompt` (released `room_inventory` version, default `v1`), `--model`,
`--reasoning-effort`, `--max-tokens`, `--single-photo-rooms per-photo` (no call for a one-photo
room), `--overlap hint|merge` with `--overlap-model` (pairwise `photo_overlap` judgments),
`--materials disagree|sensitive` with `--materials-model` (the `surface_material` second
look); the recommended room step is `--room-prompt v3 --model claude-sonnet-5-5
--reasoning-effort low --single-photo-rooms per-photo --materials sensitive`.
`docs/review_2026-10-15/repro/room_run_table.py` prints the section 16 table rows from the
run directories.

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

Phrases and count thresholds come from a phrase set (`--phrase-set`, default the
localizer's `DEFAULT_PHRASE_SET`, `v3`); `photo_counts.json` counts each class at that
set's threshold (`--count-threshold` sets one for all). Choosing phrases and thresholds per
class (EVAL_REPORT section 19): run the candidate pool once with every phrase's instances
kept, then choose offline by leaving one photo out, and score a run at the shipped
thresholds:

```bash
BASIS_ENV=dev uv run python -m evals.predict_boxes --dataset ~/basis-eval-data/detection \
    --out out/sam3_pool --phrase-set pool --phrase-dedupe off
uv run python -m evals.sam3_select select --pool out/sam3_pool \
    --dataset ~/basis-eval-data/detection --out out/sam3_select
uv run python -m evals.sam3_select score --predictions out/sam3_v3 \
    --dataset ~/basis-eval-data/detection
```

`select` writes `selection.json` (the chosen phrases and threshold per class, every fold's
choice, the out-of-sample and in-sample metrics and the v1/v2 baselines) and the
out-of-sample `detection_boxes.json` / `photo_counts.json`.

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
### `labels/e2e_rows_v1.json` and `labels/engineer_report_v1.json` (end to end)

Read by `python -m evals.e2e_report` (EVAL_REPORT section 34), not by `evals.run`; both
default to `$BASIS_EVAL_DATA_DIR/labels/`. The third input is a study export the pipeline
wrote at completion (`rev-<n>.json` of `basis.activities.export_report`, format
`basis-export/2`, or its `rev-<n>.assets.csv`).

```json
{"schema_version": 1, "rows": [
  {"id": "report-14", "system": "Specialty Plumbing - Kitchen & Utility Sink / Laundry",
   "kind": "A", "assign": [{"label": "sink", "room_types": ["kitchen", "laundry"]}]},
  {"id": "report-30", "system": "Plumbing", "kind": "A",
   "assign": [{"label": "toilet"}, {"label": "sink", "exclude_room_types": ["kitchen", "laundry"]}]},
  {"id": "report-45", "system": "Ceramic Tile Flooring", "kind": "B",
   "assign": [{"label": "tile", "materials": ["ceramic", "porcelain", "stone"]}]},
  {"id": "report-12", "system": "Specialty Electrical", "kind": "A", "assign": [],
   "note": "dedicated circuits are not an object the pipeline produces"},
  {"id": "report-37", "system": "Painting", "kind": "C",
   "assign": [{"label": "wall_finish", "exclude_materials": ["wood", "wallpaper"]}]}],
 "unassigned": [{"label": "other", "reason": "no label"}, {"label": "mirror", "reason": "decor"}]}
```

The row assignment: for each engineer row (same ids as `assets_v1` / `report_rows_v1`), the
pipeline labels charged to it, each under optional conditions. Unlike `report_rows`'
evidence rules (any-of, many-to-many), every asset is charged to **at most one** row:
`room_types` / `materials` are allow-lists that reject an unknown room or material,
`exclude_room_types` / `exclude_materials` deny-lists that accept it, so a label split
between two rows by complementary lists is charged exactly once. The loader requires every
vocabulary label to be assigned somewhere or listed under `unassigned` with a reason; the
scorer refuses an export in which one asset matches two rows, and reports an asset whose
label is assigned but meets no condition as "no rule matched". `kind` as in `report_rows`;
a kind C row may carry assignments (hints charged to it for the totals) and is never
scored; a kind A or B row with no assignment is a vocabulary gap. Built by the private
`work/build_e2e_rows.py`; the decisions are in the private `labels/E2E_ROWS_NOTES.md`.

```json
{"schema_version": 1, "property_class": "nonresidential",
 "building_basis": "249500.00", "closing_costs": "500.00", "replacement_cost_total": "312500.00",
 "rows": [{"id": "report-01", "system": "Cabinets / Millwork", "group": "Building Components",
           "bucket": "y5", "cost": "1234.56"}],
 "systems": [{"name": "Painting", "rows": {"report-37": "1", "report-08": "0.2"},
              "depreciable_cost": "20000.00", "replacement_cost": "25000.00"}],
 "bucket_percent": {"y5": "12.3"}, "allocation_building": {"y5": "30688.50"},
 "allocation_closing": {"y5": "61.50"}}
```

The engineer's printed report (the example values are made up; the file is private): the
rows with bucket and cost, the building basis B and closing costs CC (the
rows must sum to B + CC to the cent), and, when printed, the replacement-cost total, the
building-systems page (each system the rows it sums, by share; every row charged to
systems with shares summing to 1), the bucket percentages and the pro-rata allocation
block. The printed extras are checked by the reproduction (`evals.report_math`: implied
RCNs = cost x printed RCN total / T, scaled back through the factor, the buckets, the
percentages, the footed allocation columns and the systems page, to the cent), not used to
score.

Output (`--out`, `--json`): per row the outcome (evidenced / missed / vocabulary gap / not
produced for kind C), the pipeline's bucket by asset count and by dollars, bucket right,
assets charged (priced / unpriced with reasons), pipeline against engineer dollars (ratio,
error) and each side's row as a share of its own total; per bucket the same with share
errors; the pipeline's rows through the deterministic tail with the engineer's B and CC;
the summary block (`classification`, `shares` data-free; `dollars` not). `--data-free`
leaves the engineer's dollars, shares and ratios out of the Markdown. The dollar
comparison is replacement cost (pipeline) against allocated basis (engineer) until a basis
step exists; the share comparison is like-for-like.

### `structure_replay` (the structure model over a stored export)

`python -m evals.structure_replay export/rev-5.json --out export/rev-5.structure.json
--cost-table costs/unit_costs_rsmeans_2020.json [--intake intake.json] [--changes run.json]`
(EVAL_REPORT section 44). From the export's extraction fields it reads the appraisal's cost
approach and measurements, from its assets the itemized rows, and does what `process_assets`
does after the component loop and what the result does after it (`basis.domain.structure`,
docs/review_2026-10-15/STRUCTURE_MODEL.md): the building RCN, the split, the carve-out, one
concealed asset per system (classified by the rules table, priced from the residual or from
the cost table's row for its label), depreciation, reconciliation and the allocation on RCNLD.
The output is the export with the concealed rows appended and `summary.basis` replaced, so
`evals.e2e_report` scores it like any export and the kind C rows become `produced` rows. The
intake is rebuilt from the export's worksheet unless `--intake` gives the study's own. The
cost year, location factor and property class are the export's `summary.takeoff`. The eval
cost table carries rows for the four small systems (`fire_alarm_system`, `data_cabling`,
`access_control`, `site_utilities`), each cited to its RSMeans page.

### `structure_sweep` (every variant of the structure model, scored)

`python -m evals.structure_sweep export_a.json export_b.json --cost-table ... --out sweep.md
--json sweep.json` (EVAL_REPORT section 44.3, STRUCTURE_MODEL.md section 7): the 54 variants
(RCN source x split x carve on/off x depreciation) each replayed and scored on every export;
per cell the reconciliation's status and gap, the implied uniform factor, the kind C rows
produced and bucket right, the bucket shares and their errors against the engineer's, the
row-share distance, the photo-visible rows' scores (the regression check) and the runtime;
the ranking of the sourced, reconciling variants (mean bucket error, then TVD, then gap), the
pick and the runner-up; unsourced variants (Marshall & Swift) reported as such.

### `reprice_export` (a stored export priced again with the cost indices)

`python -m evals.reprice_export export/rev-5.json --out out/rev-5.repriced.json --cost-table
<table> [--state CA] [--zip 90001] [--supplement later_indices.json] [--changes out/changes.json]`
prices every itemized asset of a stored export again through `estimate_cost` with the RSMeans
cost indices (`basis.domain.cost_indices`, EVAL_REPORT 45, COST_INDICES.md): the export's
cost year and the study's location (the city factor of `--zip`, else the mean of `--state`'s
cities; the state defaults to the export's `location_source`). The concealed rows are dropped
and `summary` recomputed, so `structure_replay` then rebuilds them on the same factors and
`e2e_report` scores the result. The stored dollars are checked against the row (`base_mismatch`
counts an asset whose old unit cost over the old factors is not the row's base). Deterministic,
no model call.

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

Tools that produce or combine count predictions (EVAL_REPORT section 15); each writes a
predictions directory `evals.run --predictions` scores:

- `evals.predict_tiles --mode separate|joint`: the countables prompt on enlarged 2 x 2
  parts of each photo, summed with a center-ownership rule at the cuts (`separate`,
  `photo_countables_tile/v1`), or the whole photo and its parts in one call (`joint`,
  `photo_countables_joint/v1`).
- `evals.aggregate_runs --rule median|vote|max [--by-repeat]`: self-consistency over the
  repeats of one or more runs (pooled, or repeat i of every run together).
- `evals.fuse_counts --vlm NAME=DIR ... [--sam3 DIR] [--counts NAME=DIR]`: per count class,
  the source or rule (`max`, `min`, `median`) with the lowest error
  (`basis.vision.fusion`), scored leave-one-photo-out (`--cv loo`, the default).
- `evals.verify_boxes`: a vision model names what each SAM 3 detection contains
  (`photo_verify_box/v1`); the confirmed detections are the counts.

Detector and model orderings (EVAL_REPORT section 21; shared rules in `basis.vision.marks`):

- `evals.predict_marks`: SAM 3 first. Saved detections become numbered marks (boxes or mask
  outlines, with or without the unmarked photo); the model keeps, relabels or rejects each
  and adds misses with a point (`photo_marks/v1`). `--loop` gives the added points SAM 3
  masks (saved candidates, or `--prompt-sam` point prompts) and removes duplicates by mask
  overlap (`loop_masked/`, `loop_all/`); `--from-raw` redoes that step without model calls.
- `evals.predict_points`: the model first. One point per object (`photo_points/v1`, any chat
  model, or Molmo-7B on Replicate with `--pointer molmo`), then SAM 3 masks per point; counts
  from the points, the stated counts (`stated/`), the masks (`masked/`, `masked_all/`).
- `evals.compare_counts --baseline DIR --system NAME=DIR ...`: the count metrics of each
  system and the paired bootstrap over photos of its per-photo count error against the
  baseline's.

Adding what the pipeline missed (the rooms review's Add object; `basis.vision.selection`):

- `evals.manual_add --data DATA --out OUT [--live] [--phrases all|first]`: every verified
  object of the count classes selected as an engineer would (the verified box exact and two
  sloppy ones, a tap at its centre) against the instances the product stores (the v3 SAM 3
  run from `CANDIDATE_THRESHOLD`), with the product's own resolution, SAM 3 box prompt gates
  and duplicate guard. SAM 3's answers to the boxes that reach it are prompted once with
  `--live` (saved to `DATA/predictions/manual_add_v1/prompted.json`, one record per phrase)
  and replayed after. Gates: the exact box outlines >= 0.95 of the missed objects, a wrong
  object never, and boxes on counted objects create <= 2% duplicates
  (`tests/evals/test_manual_add_real_photos.py`, `eval_data`). Measured 2026-10-22: 42/43 at
  once and 43/43 after "add anyway" for every box variant, 0 wrong, 1/444 duplicates; taps
  27/43 (13 over no stored instance); first v3 phrase = every v3 phrase.

Vision architecture alternatives (EVAL_REPORT section 26; `docs/review_2026-10-15/VISION_ARCHITECTURE_SURVEY.md`):

- `evals.predict_detector --detector mask2former|oneformer|owlv2|gdino|llmdet|mmgdino`: a
  trained ADE20K segmenter or an open-vocabulary detector, run locally through `transformers`,
  written in SAM 3's pool format (`instances_raw.jsonl`; segmenters also `surfaces_raw.jsonl`
  with the wall, floor and ceiling masks); `evals.sam3_select select --candidates observed`
  chooses a segmenter's class names per class, `--grid-min 0.05` lets a detector's lower
  scores be chosen.
- `evals.predict_multiview --scope room|house|schedule`: one call per room with all its photos,
  one call for the whole property, or the schedule rows directly (`--template FILE`);
  `room_inventory.json` / `room_raw.jsonl` or `schedule.json`.
- `evals.predict_surface_crops predict|score|apply`: segmentation-guided surface crops with a
  closed-choice material question; material accuracy per surface against the per-photo
  labels (`--combine NAME=CROPS,PER_PHOTO`: crop answers, the per-photo surfaces elsewhere);
  crop materials put into a room run.
- `evals.combine_rooms`: one room run's rooms (`--rooms`, `--exterior`) taken from another.
- `evals.replay_rooms --per-photo DIR`: the production room step (`reconcile_rooms`'s
  configuration, built from the settings: `ROOM_*`, `ROOM_STEP_MODE`, `ROOM_CALL_*`,
  `MATERIALS_SECOND_LOOK`, `SURFACE_*`) on a stored per-photo run, grouped by file name and
  `--aliases`; `room_inventory.json` / `room_raw.jsonl` (each room's path, step, fallback,
  crops and cost) / `run.json`. EVAL_REPORT 29:
  `ROOM_STEP_MODE=room_call python -m evals.replay_rooms --photos ... --per-photo ... --aliases ... --out ...`.
- `evals.score_schedule`: row recall and precision against the engineer's schedule with
  distractor rows (`--template`), row quantities against the labelled room inventory (WAPE,
  cost-weighted relative error), room count error over every repeat, paired bootstraps over
  rows and rooms against `--baseline`. The engineer's report gives costs per row, no
  quantities, so the labelled inventory is the quantity reference.

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

## Appraisal extraction (v2)

The contract is [`appraisal/SPEC.md`](appraisal/SPEC.md): truth files per set
(`appraisal/truth.py`), absence as a value, cost-seg tiers, the outcomes and metrics. It
replaces `appraisal_fields_v1.json` for appraisal work. Everything it reads and writes is
under `BASIS_EVAL_DATA_DIR/appraisal/`; nothing from it enters the repository.

```bash
cd backEnd
# live: the production extract_appraisal as configured (EXTRACTION_METHOD, default grounded on
# EXTRACTION_GROUNDED_MODEL; --system pipeline for Azure DI + TEXT_MODEL), recorded
BASIS_ENV=dev uv run python -m evals.appraisal.run --set all --mode live [--repeats 3]
# replay: no network, from appraisal/recordings/<pdf sha256>/
BASIS_ENV=dev uv run python -m evals.appraisal.run --set client --mode replay
# ablations; one document; accept the pipeline replay as the regression baseline
... --no-azure | --azure-only    ... --documents client_a
... --system pipeline --accept-baseline
BASIS_ENV=test uv run pytest -m eval_data tests/evals/test_appraisal_regression.py
```

* `normalize.py`: per-type normalizers, the enum synonym table, the review page's display
  rule (`fieldValue` in `web/live/live-review.js`) and the canonical display.
* `score.py`: outcomes and every §8 metric; `score.json` and `report.md` (keys, outcomes and
  counts only).
* `recordings.py`: the raw Azure DI result (`azure_di.json`) and the model answers
  (`llm/<request hash>.json`); replay rebuilds the SDK result and runs the production
  `from_sdk`. A request without a recording fails its document with `missing_recording`
  (the prompt or the context changed: run live again).
* `run.py`: writes `runs/<id>/{predictions.json,score.json,report.md,run.json}`; live prints
  the model cost (Azure DI is billed separately and reported as pages). The budget is $1 per
  document (`--budget-usd`). Replay uses the extraction settings the recording was made with
  (`context.json`; a key it predates, such as the method or the grounded model, takes the
  current setting) and the current review threshold.
* `baseline.py`: `appraisal/baseline.json` (keys and outcomes) is the `pipeline` system's:
  accept it from `--system pipeline` (no `--no-azure`/`--azure-only`); the regression test
  replays that system. `--baseline NAME.json` compares with (and accepts into) another file
  under `appraisal/`, e.g. `baseline.grounded.json` for the `production` default.

### Extraction systems for the method/model sweep (`--system`)

`evals/appraisal/systems/` (designs in each module's docstring). Every system returns the
production `ExtractionResult` (the scorer is unchanged) plus per-field **regions**
(`[{page, bbox}]`, page fractions), an **origin** (which reader produced the value) and, for
the grounded systems, the verbatim **quote**; all three are in `predictions.json`.

| system | what it is | model flags |
|---|---|---|
| `production` (default) | the `extract_fields` activity's `extract_appraisal` as configured: `EXTRACTION_METHOD=grounded` (the default since EVAL_REPORT 33.5) is `llm_all` through production code (`basis/extraction/grounded.py`, the same request byte for byte, so `llm_all` recordings replay) plus the derived site-area fields, boxed by the pipeline's highlight rules (else the quote), falling back to the pipeline when it cannot run | `EXTRACTION_GROUNDED_*`; `--model`/`--effort`/`--max-tokens` override them |
| `pipeline` | the pipeline method (before section 33): Azure DI pairs, text layer, the `TEXT_MODEL` fallback; the regression baseline's system | `TEXT_MODEL` |
| `azure_1004` | `prebuilt-mortgage.us.1004` mapped to ~50 catalogue keys (no GLA, site value, cost lines); Azure's confidence; field polygons, a checkbox narrowed to its checked label; recorded as `azure_1004.json` | none |
| `production_plus_1004` | the pipeline merged with the 1004: agreement keeps it at the higher confidence; on a checkbox key the 1004 wins a disagreement, elsewhere production does; every disagreement 0.5 and a `sources_disagree` issue | `TEXT_MODEL` |
| `checkbox_context` | the pipeline with the fallback given Azure's page `content` *with* `:selected:` marks (prompt `appraisal_field_fallback_marks`) and every checkbox key always asked | `--model` overrides `TEXT_MODEL` |
| `llm_all` | (`basis/extraction/grounded.py`, production's grounded reading) one call, every key, page text with marks; per key `{status, value, page, quote}`; the quote must be on that page, the value in the quote, a checkbox option marked `:selected:`; 0.6 cap; region = the quote's words | `--model` (required) |
| `llm_pages` | `llm_all` plus PNG images (`--dpi`, default 150) of the form pages only (fewer than `--min-page-words` Azure words, or mostly figures = photo/sketch page, no image); vision models | `--model` |
| `llm_sections` | one grounded call per section group (subject+contract, neighborhood+site, improvements, sales+reconciliation, cost approach, appraiser) with its pages, in parallel; `--with-images` adds images | `--model` |
| `validate_reask` | `--base NAME`, then validators (cost rows, cost-new sum, depreciation, cost total, reconciliation vs cost, final value, effective date, GLA vs other prints, one checkbox per single group, and the baselines' reading defects: several figures in one money box, an amount over 5x the headline value, an effective date after or a year before signing) as issues; only the implicated keys re-asked once with their page images (prompt `appraisal_fields_reask`); a surviving issue stays | `--model` (re-ask), `--base-model` |
| `vote` | `--voters system@model[@effort],system@model[@effort]`: agreement 0.95, disagreement the first voter's value at 0.5 with `voters_disagree:<key>` | per voter |

The new prompts are released in `basis/prompts/` (hashed, immutable, recorded as
`prompt_version`) rather than kept eval-only, because a winning system ships with its prompt
and the replay hash covers the prompt text. `score.json`/`report.md` add **highlight correct**
(the first region is on the truth page or `also_on` and the pdfplumber words in it contain
the printed value, or a checked option's label; n/a for `production`), a breakdown **by
origin**, and **usage** (model dollars and tokens from the recorder, Azure DI pages per model
id at $10 per 1,000, wall time and the models' own latency p50/p95; replay prices the
recorded calls).

Example sweep (record live once per cell, then replay for free; set
`LLM_REQUEST_TIMEOUT=300`, a ~6,000-token grounded answer outlasts the 60 s default on the
large models):

```bash
cd backEnd
M="gpt-6-luna gpt-6.1-sol claude-haiku-5-5 claude-sonnet-5-5 claude-opus-5-5"
run() { BASIS_ENV=dev LLM_REQUEST_TIMEOUT=300 uv run python -m evals.appraisal.run --set client --mode live "$@"; }
run --system production
run --system azure_1004
run --system production_plus_1004
for m in $M; do
  run --system checkbox_context --model $m --effort low
  run --system llm_all --model $m --effort low
  run --system llm_pages --model $m --effort low
  run --system llm_sections --model $m --effort low
  run --system llm_sections --with-images --model $m --effort low
  run --system validate_reask --base llm_pages --model $m --effort low
done
run --system vote --voters llm_pages@gpt-6-luna@low,llm_pages@claude-haiku-5-5@low
# any cell again, no network:
BASIS_ENV=dev uv run python -m evals.appraisal.run --set client --mode replay --system llm_all --model gpt-6-luna --effort low
```

## Run records, the verification tree and the registry

Every runner leaves, in its output directory, the record a study leaves in production
(EVAL_REPORT section 43; `evals/record.py`), and files the run in one tree a person can
browse with Finder, so a result can be reused instead of re-run and a question like "has
`photo_countables/v3` on gpt-6.1-sol been tested against `photo_components_v1`" is answered
from files:

```
$BASIS_EVAL_DATA_DIR/verification/<component>/<version>/<run id>/
    manifest.json      what ran, on which code, with which components, on which data, cost,
                       how it ended, where it is filed
    results.json       every case: passed | failed | test_error (+ expected/observed error codes)
    report.md          the same as a page: the summary line with all three counts, one row per case
    llm_calls.jsonl    one line per paid call, every column of the llm_calls table
    llm/               <call id>.request.json / .response.json (prompts as sent, raw answers)
    console.log        everything printed and logged (one tee: terminal and file never disagree)
    run.json           the runner's own summary (unchanged)
    scores.jsonl       appended by evals.run --predictions: label file, version, sha256, every metric
    ...                the runner's prediction files
```

Components are the system's own names: a released prompt (`photo_countables`,
`photo_surfaces`, `room_inventory`, `surface_material`, `house_inventory`, `classify`,
`appraisal_fields_grounded`, ...), `detection` (SAM 3 and the detectors), `photo_analysis`
(merged per-photo runs), `photo_counts` (count fusion), `appraisal_fields` (the appraisal
harness as configured), `provider_errors` (the expected-error runs), `scoring`. The version
is the prompt, rule or table version the run exercised (`v3`), a detector's name, SAM 3's
phrase set (`sam3-v3`), the sources' prompt versions joined for a derived run
(`photo_countables-v2+photo_surfaces-v1`), the release for `provider_errors`, the pointer
model for a Molmo points run; a run without one files under `unversioned`. A runner declares its component and version when the default (the prompt with
the most calls) would be wrong (`predict_rooms` files under `room_inventory`, not the
`surface_material` second look). The run directory itself is wherever `--out` says
(`predictions/<run id>` by convention); the tree holds a relative symbolic link to it, made
when the run finishes, and `verification/README.md` (written by the helper) explains the
layout. Runs made before 2026-10-29 are linked in by the backfill.

| file | what it holds |
|---|---|
| `llm_calls.jsonl` | one line per paid call with every column of the `llm_calls` table (`basis.llm.recorder.ledger_columns`, the dict the production repository writes) plus `created_at`: component, prompt name/version/sha256, build sha, requested and answering model, request parameters, tokens of every kind, cost with price row and pricing version, latency, finish reason, status |
| `llm/<call id>.request.json`, `.response.json` | the request as sent (system prompt, messages, images as ref + size + sha256, tools, schema) and the raw response, built and redacted by the production archive's functions |
| `console.log` | everything printed and logged during the run through one tee, API-key-shaped strings redacted; the summary line of the results is printed the same way (the log ends when the runner finishes its record) |
| `results.json` | every case the run recorded with an outcome of exactly one of `passed` (the system under test did what the case asked), `failed` (it did not) or `test_error` (the harness or an API failed, so the case says nothing about the system); the summary carries all three counts, never folded into each other, the counts per case kind, and the expected-error tally (`matched`, `mismatched`: another code occurred, `not_observed`: no catalogued error occurred). Each runner records one case per unit of work (photo x repeat, room x repeat, document, asset row, crop, SAM 3 image); `evals.run --predictions` adds one `score` case per labelled item (passed = the metric's per-item criterion, failed otherwise, test_error when the run itself recorded a test error for that item) |
| `report.md` | the results as a page: the run's identity, `**results: N passed, M failed, K test errors (T cases)**`, one row per case (with expected error, observed error and matched yes/no when any case expected one). The appraisal runner appends its own scoring tables (SPEC §8) to the same page after a `---` |
| `manifest.json` | the run manifest: harness module and its file's sha256, command line and options; git sha, dirty flag, branch and nearest release tag; every prompt name + version + sha256 (declared or called), the price table version, the rules table version, the embedder and its pinned revision, the cost table if configured, the models requested and answered, the request parameters in effect, the model settings; the inputs and dataset files read (sha256, label version), split and repeats; calls, tokens and dollars; status, exit code and error; the results summary; `tree` (component, version, the link made); the `run.json` summary |
| `run.json` | the runner's own summary, unchanged |
| `scores.jsonl` | appended by `evals.run --predictions DIR`: the label file, version and sha256 of every dataset scored, the scorer's git state and options, every metric and the per-dataset case counts. The latest scoring per dataset is what the registry reports |

Expected errors (`python -m evals.provoke_errors --out DIR [--dry-run] [--only id ...]`): the
provider failures the product maps to error codes (`docs/ERROR_CODES.md`), provoked on
purpose and at no cost: a key the provider did not issue (`BASIS-PROVIDER-001`), a model that
does not exist and a parameter the model rejects (`BASIS-PROVIDER-002`), a request that cannot
finish in time (`BASIS-PROVIDER-007`), a budget that cannot cover one call (`BASIS-BUDGET-001`),
on OpenAI and Anthropic. Each scenario is a case of kind `expected_error` recording the
expected code, the observed code and `matched` yes/no: `passed` when they match, `failed`
when another code (or no error) occurred, `test_error` when the provider is not configured
or the harness itself raised. `--dry-run` runs the same scenarios against scripted providers
(plus one deliberate mismatch and one unconfigured provider) to exercise the record without a
network; an account without credit cannot be provoked on demand and is covered by the unit
tests of `evals.account_stop` only. The run files under `provider_errors/<release>/`.

The derive-a-run tools (`merge_photo_runs`, `aggregate_runs`, `fuse_counts`,
`combine_rooms`, `sam3_select`, `manual_add`) write a manifest, results and log too, naming
their source runs (and the sources' manifest ids) under `notes.sources` / `data.inputs` and
taking their version from the sources' manifests. A run stopped by an account error or an
exception still gets its manifest (`status: stopped` / `error`), results and log:
`evals.account_stop.stops_on_account_error` finishes the record on every exit. When
`DATABASE_URL` is set, `evals.run` writes the same manifest fields on its `eval_runs` rows
(`git_dirty`, `release`, `run_dir`, `label_version`, `label_sha256`, `component`,
`component_version`, `manifest`; migration `0015`), so the database and the files agree.

Runs made before this record (the directories under `$BASIS_EVAL_DATA_DIR/predictions/`
and `appraisal/runs/` up to 2026-10-29) carry a backfilled manifest written by
`python -m evals.backfill_manifests --write` (`backfilled: true`, each field with its source,
`unknown` where the files do not say; no `results.json`, since per-case outcomes were not
recorded) and are linked into the tree under the first prompt their `run.json` names (the
note on what could and could not be recovered is `docs/review_2026-10-15/EVAL_RUN_BACKFILL.md`).
The tool is a dry run without `--write`, never touches a manifest a runner wrote, and
rewrites its own only with `--force`.

```bash
cd backEnd
# every run, newest first (Markdown to stdout)
uv run python -m evals.registry
# the question above: component at a version, on a model, against a label version
uv run python -m evals.registry --component photo_countables/v3 --model gpt-6.1-sol --labels photo_components_v1
# "fully tested?": prompt version x model, with the label versions seen in each cell
uv run python -m evals.registry --component room_inventory --matrix
# every filter, both outputs
uv run python -m evals.registry --release v0.9.0 --harness evals.predict_rooms --since 2026-10-16 \
    --status ok --json runs.json --markdown runs.md
```

`--component` takes a prompt name or `name/vN` (also the tree component, `detector:gdino`,
`embedder:<model>`), `--model` a requested or answered model (`claude-*` for a prefix),
`--labels` a label file stem, `--dataset` a scored dataset. The table shows each run's tree
component and version, start, release, git sha (`*` when the tree was dirty), harness,
prompts, model, effort, labels, `passed / failed / test_error`, the headline metric per
dataset (`registry.HEADLINE_METRICS`; every metric is in the JSON), cost, calls and where the
record came from (`manifest`, `backfilled`, or `run.json` alone) with the EVAL_REPORT
sections that cite it.

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
