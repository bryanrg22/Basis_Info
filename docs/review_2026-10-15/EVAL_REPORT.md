# Evaluation report: deterministic baselines on the real client package (Stage 3)

Date: 2026-10-15. Code: `rebuild/stage-3` at the Stage 3 closure (see the git sha in the
run log below). Harness: `backEnd/evals/run.py` (BUILD_PLAN 5, 6.7, 13.2; S3_SPEC 5).

The client package never enters the repository. It lives in `BASIS_EVAL_DATA_DIR`
(mirrored to the private bucket `basis-eval-private`). This report quotes metrics only:
no field values, file names, addresses or amounts from the package.

## 1. What was measured

One completed study (one appraisal, one engineer's report, 52 photos) and the six
reference documents the retrieval corpus is built from (IRS Cost Segregation ATG, Pub
527, Pub 946, Rev. Proc. 87-56, two RSMeans 2020 volumes; 2,305 PDF pages).

| Dataset | Items | Labels | Notes |
|---|---|---|---|
| `appraisal_fields` | 75 fields (1 document) | hand-labelled from the appraisal; 13 critical | 9 further fields marked *verify* are excluded until the engineer confirms them |
| `photo_rooms` | 52 photos | room type + floor, visually verified | 13 room types |
| `photo_components` | 52 photos, 382 component mentions | 180 distinct labels with material and attachment | `other` labels are excluded by the harness |
| `assets` | 46 rows | engineer's report rows: category, bucket, amount | the bucket sums reconcile exactly with the report totals; `property_class` set to `nonresidential` because the report uses a 39-year bucket (to confirm) |
| `retrieval_qrels` | 50 queries, 173 (doc, page) judgments | graded 0-2, pooled from the legacy BM25 + vector indexes | 17 judgments lie outside the pool |

The datasets were converted from the labelling files into the harness format by a
private script (`work/convert_labels.py` in the data directory); the labelling files stay
the source of truth.

Command (from `backEnd/`):

```bash
BASIS_ENV=test BASIS_EVAL_DATA_DIR=~/basis-eval-data \
  uv run python -m evals.run --split dev --no-db --report <path>/eval_baselines_dev.md
```

## 2. Results: deterministic baselines

These are the floors the model-backed systems must beat, not the system's quality. Each
baseline is a few lines of deterministic code (`evals/run.py`), so the numbers are
reproducible to the digit.

| Dataset | Baseline | Metric | Value | n | 95% interval |
|---|---|---|---|---|---|
| `appraisal_fields` | `printed_text` (value as printed) | exact accuracy | 0.000 | 75 | [0.000, 0.049] |
| | | normalized accuracy | 0.000 | 75 | [0.000, 0.049] |
| `photo_rooms` | `filename` (words of the file name) | accuracy | 0.135 | 52 | [0.067, 0.253] |
| | | macro-F1 | 0.333 | 52 | |
| | | coverage | 0.154 | 52 | |
| `photo_components` | `filename` | precision | 0.429 | 21 predicted | |
| | | recall | 0.024 | 382 | |
| | | F1 | 0.045 | | |
| | | exact label set per photo | 0.000 | 52 | [0.000, 0.069] |
| `assets` | `rules_table` (`basis.domain.rules.lookup`, a miss abstains) | bucket accuracy | 0.087 | 46 | [0.034, 0.203] |
| | | bucket macro-F1 | 0.175 | 46 | |
| | | coverage | 0.087 | 46 | |
| `retrieval_qrels` | `bm25_pages` (BM25 over one chunk per page) | nDCG@10 | 0.423 | 50 | SE 0.040 |
| | | recall@5 | 0.382 | 50 | SE 0.045 |
| | | recall@10 | 0.518 | 50 | SE 0.045 |
| | | MRR | 0.557 | 50 | SE 0.056 |
| | | hole rate @10 (unjudged pages in the top 10) | 0.664 | 500 | |

Reading the table:

- `printed_text` scores 0 because the converted labels carry no `printed` column yet (the
  baseline predicts nothing). It is a harness smoke, not a finding about extraction.
- The photo baselines are floors by construction: the engineer's file names name the room
  for a few photos and almost never a component.
- `rules_table` abstains on 42 of 46 report rows: the engineer's report names cost
  categories ("Cabinets / Millwork"), not component labels, so the rules table rarely
  matches. The real classifier runs on components from photos, so this dataset will be
  scored by the model-backed system through `--predictions` once the pipeline has run on
  the package.
- Retrieval: page-level BM25 over the whole corpus reaches the judged pages for about
  half the queries in the top 10. Two thirds of the retrieved pages are unjudged (the
  judgments were pooled from the legacy indexes), so recall is a lower bound and nDCG is
  conservative; judging the holes is the first thing to do before tuning fusion.

## 3. Finding: the rules table's citations do not resolve against the real documents

Every rules-table row carries a `(doc_id, page, excerpt)` citation copied from the legacy
table. Checked against the real PDFs (page text normalized the same way the citation
verifier normalizes it):

| Citations | Verbatim on the cited page | Verbatim on another page | Not found verbatim |
|---|---|---|---|
| 48 | 0 | 3 | 45 |

The cited ATG pages (412-420) do not exist: the ATG PDF in the corpus has 348 pages. The
excerpts are paraphrases ("Plumbing fixtures - toilets"), not text of the documents.

Consequence in production: `resolve_rule_citations` would drop every rule citation, every
rule-classified asset would be flagged `citation_unverified` and go to review, and the
evidence pack would cite nothing for them. The tests did not catch this because the test
corpus is synthetic; the lifecycle tests now seed one chunk per citation page from the
rules table itself (so the verification path is exercised), and this report records that
the claims behind those chunks are not yet true of the real corpus.

Required follow-up (engineer-reviewed content, not a code change): re-cite every rule
against the ingested corpus with verbatim excerpts of at least 12 characters, keep the
`(doc_id, page)` of the real page, and add the check above as an eval that runs whenever
the corpus or the table changes (`evals/run.py`, dataset `rule_citations`).

## 4. Not yet measured

- The real retrieval pipeline (`basis.retrieval`: tiktoken chunks, IRS tokenizer, BM25 +
  MiniLM embeddings in pgvector, weighted or RRF fusion) on the qrels. Needs the corpus
  ingested into a Postgres with pgvector (`python -m basis.retrieval.ingest`) and a small
  runner that writes `retrieval_qrels.json` predictions; then `--predictions` scores it
  and the `retrieval_fusion` default is chosen from the numbers, not assumed.
- Azure Document Intelligence extraction on the appraisal (`appraisal_fields`), the
  structured vision call on the 52 photos (`photo_rooms`, `photo_components`) and the
  residual classifier on the extracted components (`assets`): model-backed, run by the
  orchestrator with real credentials, scored through `--predictions` with `--repeats 5`
  for pass^k.
- Judging the retrieval holes (unjudged pages in the top 10) so recall stops being a lower
  bound.

## 5. Decisions needed from the engineer

1. Confirm the 39-year treatment of the report's structural rows (`nonresidential`, as
   assumed in the `assets` labels) before it becomes a hard expectation.
2. Confirm the 9 `verify` appraisal fields.
3. Review the re-cited rules table (section 3) once prepared.

## 6. Photo model baseline on the real photos (added 2026-10-15, later the same day)

The current per-photo pipeline (`basis.vision.analyze`: image preparation, the released
`photo_analysis/v1` prompt, structured output, one retry) was run on the package's 52
photos three times with the configured provider (the Azure deployment answered as
`gpt-4.1-mini-2025-04-14`; the `VISION_MODEL` setting still says `gpt-4o-mini`, so the
deployment name decides). Runner: `python -m evals.predict_photos` (unit-tested with the
fixture provider); scoring: `python -m evals.run --predictions ... --repeats 3`. Total
cost of the three passes: $0.18. No image was rejected; every call succeeded on the first
attempt; one photo was flagged for review by the model.

Labels: the photo labels were mapped onto the model's own vocabulary (21 room types, 72
component labels) by the private converter; descriptive labels with no building-component
equivalent (lamps, art, rugs, televisions, piping, meters) became `other`, which the
harness excludes. That mapping is itself a labelling decision and is listed in the
converter.

| Metric | Value | n | 95% interval | Notes |
|---|---|---|---|---|
| Room accuracy (repeat 1) | 0.808 | 52 | [0.681, 0.892] | repeats 2 and 3: 0.827, 0.788 |
| Room macro-F1 | 0.748 | 52 | | 13 room types in the labels |
| Room pass^3 (all three repeats right) | 0.788 | 52 | | the room answer was identical across repeats for 50 of 52 photos |
| Component set precision | 0.602 | 271 predicted | | loose furniture is `other` on both sides (see the label guide) |
| Component set recall | 0.632 | 258 labelled | | |
| Component set F1 | 0.616 | | | |
| Photos with the exact label set | 0.058 | 52 | [0.020, 0.156] | |
| Component set identical across the three repeats | 16 of 52 | | | temperature 0 does not make the list stable |

What the errors are (all three repeats pooled):

- Rooms: bedrooms called `basement` (7 of 156 answers) and `other` (4); exterior
  utility photos called `mechanical` (4); hallway, bathroom and living room sometimes
  `other`. The property's sloped upper-floor ceilings and finished-basement look explain
  the bedroom/basement confusion; the file names ("2nd Floor Bed 2") would settle it.
- Components most often missed (before loose furniture was moved to `other`): furniture
  (54; the prompt asks for building components, so the label guide now files loose
  furniture as `other` and it is excluded), ceiling finish (29), electrical outlets (22),
  built-in shelving (16), baseboards (14), gutters (13), hardwood and vinyl floors (12 each).
- Components most often predicted but not labelled: `wall_finish` (50), `interior_door`
  (37), `light_fixture` (34), `window` (23), `exterior_door` (17). Several of these are
  convention disagreements rather than model errors: the labelling did not list plain
  painted walls or every door, the model does. The label guide has to decide whether
  walls, ceilings, doors and windows are always listed, and the labels re-checked
  against that decision before these numbers are read as model quality.
- Strongest labels: windows, interior doors, carpet, siding, light fixtures, baseboards,
  window treatments, sinks, hardwood floors, toilets.

What this baseline settles: the per-photo model is usable as a draft for the engineer
(four of five rooms right, three of five components right) and is not usable as a count.
It also does not try to count across photos: the two kitchen photos produce two
countertops. The next step is the design in `VISION_RESEARCH.md`: group photos into rooms
from file names plus the model's room type, then one reconciliation call per room over
the per-photo results that returns each distinct object with the photos it appears in,
scored per room against a human inventory (count error, exact-match rate, signed bias).
That human per-room inventory was labelled the same day (section 7).

## 7. Counting once per room: heuristic merge versus the reconciliation call (2026-10-15)

Labels: a per-room inventory of the package was built the same day from the photos
(`labels/room_inventory_v1.json` in the private data directory, with `LABEL_GUIDE.md`
fixing the conventions and `room_aliases_v1.json` naming which file names are views of the
same room, each alias backed by an object visible in both photos). 52 photos, 33 rooms,
16 aliases; 312 per-photo object mentions became 228 per-room objects (199 scored, 29
`other`). Only 8 rooms have more than one photo (27 photos), so on this package the
duplicate problem is confined to those rooms; the exterior is one room of 11 photos.

Systems (room grouping by file name plus the model's room type, `basis.vision.rooms`):

- `per_photo_max`: the hand per-photo labels merged by max count. Not a model; it shows
  how far "perfect per-photo detection, no reconciliation" gets.
- heuristic merge: the model's per-photo results (repeat 1 of section 6) merged by max
  count per label (`basis.vision.reconcile.heuristic_merge`).
- reconciliation call: the same per-photo results plus all of the room's photos in one
  structured call that returns each object once with the images it appears in
  (`basis.vision.reconcile.reconcile_room`, prompt `room_inventory/v1`), three repeats,
  $0.15 in total, no call failed, no room was flagged.

| Metric (per room-label pair unless noted) | per_photo_max | heuristic merge | reconciliation call |
|---|---|---|---|
| count MAE | 0.56 | 0.95 | 0.89 |
| exact-count rate | 0.63 | 0.36 | 0.38 |
| signed bias (over-counting is positive) | -0.23 | +0.09 | +0.03 |
| rooms with the exact inventory (of 33) | 0.18 | 0.00 | 0.00 |
| label precision / recall / F1 (per room) | 0.86 / 0.85 / 0.85 | 0.65 / 0.65 / 0.64 | 0.65 / 0.65 / 0.64 |
| inventory identical across 3 repeats | n/a | deterministic | 19 of 33 rooms |

On the 8 multi-photo rooms only (where reconciliation can act):

| | heuristic merge | reconciliation call (repeat 1) |
|---|---|---|
| pairs | 93 | 94 |
| count MAE | 1.28 | 1.17 |
| exact-count rate | 0.32 | 0.35 |
| signed bias | -0.03 | -0.13 |
| over / under counted pairs | 35 / 28 | 33 / 28 |

What the numbers say:

- The reconciliation call does what it was built for and nothing more. Where the
  per-photo results had duplicates it removes them: the kitchen's cabinets go from 7
  (three photos' counts added) to 1, the exterior railing from 6 to 1, and the bias moves
  from over-counting toward zero. It cannot add objects the per-photo step never saw, so
  label precision and recall do not move.
- The dominant error is per-photo recognition and counting, not duplication. The hand
  labels merged with the crudest rule still reach 0.85 label F1 and 63% exact counts; the
  model reaches 0.64 and 36 to 38%. The misses are repeated small objects: the exterior
  has about 30 windows and the model reports 7, eight outdoor lights and the model reports
  2; a kitchen with six light fixtures gets 4. Large, singular objects (sink, stove,
  refrigerator, garage door, signage) are right.
- Grouping by file name worked for every photo of this package (no photo unmatched, every
  alias resolved), and the one weak alias (a mechanical close-up placed by elimination) is
  recorded as such in the labels.
- The reconciliation call is not deterministic: 14 of 33 rooms change between identical
  runs. The engineer sees the per-object image indices, so a merge can be checked, but a
  count that changes run to run cannot be presented as a measurement.

What to do next, in order, each measured with this dataset:

1. Keep the reconciliation call as the merge step and ship the grouping; both are cheap
   and auditable. Expose the grouping at the rooms review gate so the engineer confirms
   it (the aliases are exactly the corrections they would make).
2. Attack per-photo recognition, which is where the error is: run the same per-photo
   evaluation (section 6) with a stronger vision model and with the label guide's
   conventions written into the prompt, and keep whichever wins on label F1.
3. For countable repeated objects (windows, outlets, lights, cabinets) an open-vocabulary
   detector with boxes (VISION_RESEARCH.md section 2) is the tool, because a language
   model's count of "many identical things" is the weakest part of what was measured.
   Boxes also give the engineer's interface what it needs to show and correct a count.
4. Re-label the two rooms whose floors were too dark to judge once better photos exist,
   and decide the open tub-versus-shower calls with the engineer.

## 8. Per-photo model and prompt: a two-by-two on the real photos (2026-10-15)

Question: does a stronger vision model, or writing the label guide's conventions into the
prompt, move the per-photo numbers of section 6? Four runs of `evals.predict_photos`,
three repeats each, scored with the same labels (loose furniture `other` on both sides).
`gpt-4.1-mini` is the Azure deployment the pipeline uses today; `gpt-4.1` was called
through OpenAI directly (`LLM_PRIMARY=openai`, `--model gpt-4.1`). Prompt `v2` is
`photo_analysis/v2`: windows and doors always counted, finishes once and only when
distinct, composite objects, loose furniture as `other`, careful counts of repeated
objects. The pipeline stays pinned to `v1` (`basis.vision.analyze.PROMPT_VERSION`).

| Run | Room accuracy | Room pass^3 | Component precision | Component recall | Component F1 | Label set identical across 3 repeats | Count MAE on shared labels | Exact count on shared labels | Cost per 52 photos | Seconds per photo |
|---|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1-mini, v1 (today) | 0.81 | 0.79 | 0.60 | 0.63 | 0.62 | 16 of 52 | 0.50 | 0.74 | $0.06 | 4.3 |
| gpt-4.1-mini, v2 | 0.85 | 0.79 | 0.58 | 0.66 | 0.62 | 9 of 52 | 0.46 | 0.77 | $0.08 | 5.5 |
| gpt-4.1, v1 | 0.85 | 0.85 | 0.63 | 0.72 | 0.67 | 26 of 52 | 0.44 | 0.78 | $0.30 | 2.2 |
| gpt-4.1, v2 | 0.81 | 0.79 | 0.63 | 0.68 | 0.65 | 27 of 52 | 0.39 | 0.82 | $0.38 | 2.3 |

"Shared labels" are the (photo, label) pairs both the model and the labels list; the count
metrics there ask whether the model counts right what it did find. Per-photo count
metrics are not yet in the harness (computed from the raw records; add them).

Reading the table:

- The model is the lever. `gpt-4.1` raises component recall from 0.63 to 0.72 and F1 from
  0.62 to 0.67, gets 46 of 52 room labels right on all three repeats, returns the same
  label set across repeats for half the photos instead of a third, and answers in half
  the time. It costs five times more per photo, which is still under a dollar per
  property.
- The prompt is not. Writing the conventions in (`v2`) improves the counts of what is
  found (exact count 0.74 to 0.77 on the small model, 0.78 to 0.82 on the large one) but
  lowers precision or recall on label sets, and on the small model makes the output less
  stable. Its instructions about what not to list cost more than its counting
  instructions gain. `v2` stays released for later experiments; the pipeline keeps `v1`.
- Repeated small objects are not solved by either change: the exterior's windows and
  lights are still undercounted by every run. That is the detector question (section 7,
  next steps, and the detection research).

Decision: switch the pipeline's vision model to `gpt-4.1` (a settings change:
`VISION_MODEL=gpt-4.1` with an OpenAI key, or an Azure `gpt-4.1` vision deployment);
keep prompt `v1`; re-run section 7 with the new per-photo results (below).

Section 7 re-run on the `gpt-4.1` per-photo results (repeat 1), three repeats of the
reconciliation call:

| System | count MAE | exact-count rate | signed bias | rooms exact (of 33) | label F1 | inventory identical across 3 repeats | cost |
|---|---|---|---|---|---|---|---|
| heuristic merge (section 7, `gpt-4.1-mini` per photo) | 0.95 | 0.36 | +0.09 | 0 | 0.64 | deterministic | |
| reconciliation call, `gpt-4.1-mini` throughout (section 7) | 0.89 | 0.38 | +0.03 | 0 | 0.64 | 19 | $0.15 |
| heuristic merge, `gpt-4.1` per photo | 0.82 | 0.43 | +0.04 | 0 | 0.69 | deterministic | |
| reconciliation call, `gpt-4.1` per photo, `gpt-4.1-mini` reconciling | 0.81 | 0.43 | +0.03 | 1 | 0.69 | 22 | $0.17 |
| reconciliation call, `gpt-4.1` per photo, `gpt-4.1` reconciling | 0.84 | 0.41 | +0.09 | 0 | 0.69 | 22 | $0.75 |

The per-photo model upgrade moved the room-level numbers more than the reconciliation
step does (count MAE 0.95 to 0.82 from the model alone; 0.82 to 0.81 from reconciling).
Reconciling with the larger model is not better and costs five times more, so the
reconciliation call can stay on the small model. Both reconciliation variants are stable
for 22 of 33 rooms. Per-room exact inventories remain rare (one room), which is the
detection ceiling again: the objects the per-photo step never finds cannot be counted.

## 9. Rules table re-cited; `rule_citations` eval (added 2026-10-15, later the same day)

Follows section 3. Every rules-table citation was re-cited against the reference PDFs
(`basis/domain/rules.py`, `RULES_VERSION` `2026-10-15.2`; judgments row by row in
`RULES_RECITATION.md`), and the check of section 3 is now an eval dataset,
`rule_citations` (`evals/run.py`, system `page_text`): each `(doc_id, page, excerpt)`
claim is resolved against the pymupdf text of `<data dir>/corpus/<doc_id>.pdf` the way
`resolve_rule_citations` checks a chunk (document present, page in range,
`excerpt_supported`). It is derived from the code, so it runs on every `evals.run`.

Command (from `backEnd/`, at `9c0be45`):

```bash
BASIS_ENV=test BASIS_EVAL_DATA_DIR=<data dir> uv run --no-sync python -m evals.run \
  --split dev --no-db --report <scratch>/eval_rules.md
```

| Table | Citations | Resolved on the cited page | 95% interval | Rules with every citation resolved | Unresolved by status |
|---|---|---|---|---|---|
| before (`2026-10-15.1`, legacy claims) | 48 | 0 (0.000) | [0.000, 0.074] | 0 of 47 | page out of range 25 (ATG pp.412-420; the PDF has 348 pages), excerpt not on page 23 |
| after (`2026-10-15.2`) | 65 | 65 (1.000) | [0.944, 1.000] | 47 of 47 ([0.924, 1.000]) | none |

The "before" row is the same metric code run on the legacy table (statuses from
`page_citation_status`); it matches section 3 (0 on the cited page; the 3 excerpts that
were verbatim somewhere were on other pages). The other five datasets score exactly as
on `806872e` (the report bodies are identical apart from the header).

What the 65 of 65 does and does not establish:

- Every claim is verbatim on its page. The same 65 excerpts were also checked against
  the chunks production stores (`parse_pdf` + `chunk_document` on the four IRS PDFs):
  each is inside a chunk of its page, and a simulated `resolve_rule_citations` search
  (in-memory BM25, k = 10, the IRS documents only, not Postgres, without the RSMeans
  volumes) puts that chunk in the top 10 for all 65. Not yet measured on the ingested
  Postgres corpus.
- A resolved citation is not an approved tax position. 22 of the 47 rows are
  `contested`, 15 of them added by the re-citation: for 12, no reference document
  supports the legacy section 1245, 5-year treatment and the ATG's residential rental
  matrix says section 1250, 27.5 years (kitchen cabinets, countertops, ceiling fans,
  garbage disposal, bathroom vanity, mirror, smoke detector, thermostat, closet
  shelving, water heater), or no document names the item (doorbell, laminate);
  `furniture`, `chair` and `deck` are supported only for office furniture and
  freestanding decks. The other 7 were contested already (plumbing fixtures, generic
  lights, tile, hardwood). A contested hit keeps its legacy bucket and goes to review
  (`rule_contested:<key>`), so no tax position changed silently.
- On `--dry-run` the dataset resolves 0 of 65 by design: the synthetic corpus holds none
  of the cited documents (every status `document_missing`). The dry run exercises the
  code path; only the real corpus measures the table.

Found on the way (not fixed here): the production PDF parser interleaves the two columns
of Pub 527 and Pub 946 line by line, so a sentence that wraps inside one column is not
contiguous in the stored chunk and cannot be cited verbatim (five first-choice excerpts
were replaced for this reason; `RULES_RECITATION.md`). This also degrades retrieval on
those publications.

Decision needed from the engineer (adds to section 5): for each contested row, change
the bucket to what the documents say or record the basis for the legacy treatment; and
the alias follow-ups listed in `RULES_RECITATION.md` (`range_hood`, `exterior_lighting`,
`hot_tub`/`spa`, `lvp`).
