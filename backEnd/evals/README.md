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
the same six datasets from `tests/fixtures/synthetic/` (for `room_inventory`, every fixture
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
`page_text` (rules-table citations checked against corpus page text). Model-backed systems
are run by the orchestrator and scored through `--predictions`.

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

### `assets_v1.json`

```json
{"schema_version": 1, "assets": [
  {"id": "report-12", "component": "carpet", "material": null, "attachment": null,
   "property_class": "nonresidential", "section": "1245", "bucket": "y5",
   "amount_usd": 4210.0, "document": "package"}]}
```

`section` and `bucket` may each be null (not labeled). Predictions:
`{"<id>": {"section": "1245", "bucket": "y5"}}`.

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

## Metrics

`evals/metrics.py`, unit-tested against hand-computed values in
`tests/unit/evals/test_metrics.py`: field accuracy (exact and normalized), per-class
precision/recall/F1 with macro-F1, accuracy with a 95% Wilson interval, coverage and
selective accuracy under abstention, room-inventory count MAE / exact-count rate /
signed bias and per-room exact inventory and label-set P/R/F1, nDCG@10 (linear gains, log2 discount, as
`pytrec_eval`'s `ndcg_cut_10`), recall@5/10, MRR, the judged-hole rate, citation support
rate, the status of a page citation (`page_citation_status`), the standard error of a
mean clustered by document (CR1), and pass^k
(`C(c, k) / C(n, k)` per item).
