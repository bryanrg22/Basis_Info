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
the same five datasets from `tests/fixtures/synthetic/`.

## Systems

Each dataset is scored by saved predictions (`--predictions DIR/<dataset>.json`) when
present, otherwise by a deterministic baseline: `printed_text` (fields as printed),
`filename` (photo rooms and components from file-name words), `rules_table`
(`basis.domain.rules.lookup`, a miss abstains) and `bm25_pages` (BM25 over one chunk per
page of `<data dir>/corpus/*.pdf`, doc id = file stem). Model-backed systems are run by
the orchestrator and scored through `--predictions`.

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

For any predictions file, a value may instead be `{"repeats": [v1, v2, ...]}` with one
value per repeat; pass^k is computed over them.

## Metrics

`evals/metrics.py`, unit-tested against hand-computed values in
`tests/unit/evals/test_metrics.py`: field accuracy (exact and normalized), per-class
precision/recall/F1 with macro-F1, accuracy with a 95% Wilson interval, coverage and
selective accuracy under abstention, nDCG@10 (linear gains, log2 discount, as
`pytrec_eval`'s `ndcg_cut_10`), recall@5/10, MRR, the judged-hole rate, citation support
rate, the standard error of a mean clustered by document (CR1), and pass^k
(`C(c, k) / C(n, k)` per item).
