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

## 10. Localizing and counting objects with SAM 3 (2026-10-16)

Design: `VISION_DETECTION_RESEARCH.md`. Code: `basis/vision/localize.py` (SAM 3 through
Replicate, `vufinder/sam3`; masks kept as run lengths; per-label phrases; 2 x 2 tiling with
mask NMS; tap-to-add prompts), `evals/openimages.py` (public evaluation set),
`evals/predict_boxes.py` (runner), `evals.run` dataset `detection_boxes`. Replicate billed
by predict time; the account was throttled to 6 prediction creates per minute (under $5 of
credit), which is why runs below took an hour and lost some images to retries.

### 10.1 Public yardstick: Open Images V7 validation, 23 of our classes

360 images selected (25 per class where available, 879 boxes, built by
`evals.openimages`; Open Images "Door" scored against the union of our interior and
exterior door predictions; group boxes and verified-label rules as in the Open Images
protocol). SAM 3 prompted with every label of the dataset, no tiling, score threshold 0.1
for the curve; 310 images returned (50 lost to the throttle), $2.18.

| Metric | Value |
|---|---|
| mAP@0.5 over 23 classes | 0.596 |
| AP@0.5, strongest: washer / toilet / ceiling_fan / light_fixture / bathtub / window / pool / refrigerator / stairs | 0.91 / 0.85 / 0.82 / 0.81 / 0.77 / 0.77 / 0.75 / 0.73 / 0.71 |
| AP@0.5, weakest: kitchen_cabinet / dishwasher / porch / countertop / door | 0.11 / 0.16 / 0.32 / 0.35 / 0.38 |
| at threshold 0.5: precision / recall | 0.45 / 0.66 |
| at threshold 0.6: precision / recall; count MAE; signed bias (per image-class) | 0.54 / 0.60; 0.99; +0.19 |
| at threshold 0.3: precision / recall; count MAE; signed bias | 0.31 / 0.75; 2.45; +1.98 |

The two worst classes fail on semantics, not vision: SAM 3 returns every cabinet door
where Open Images boxes a run of cabinetry (count bias +4.3 per image), and "door" also
returns appliance and cabinet doors (bias +2.7). Singular fixtures are found reliably.
For comparison, the SAM 3 paper reports 53.6 box AP on LVIS zero-shot; 0.60 on these
everyday classes is in line with that, so the public set says the model is as advertised
on our vocabulary and that the remaining work is phrasing, granularity and thresholds.

### 10.2 Our photos: SAM 3 against verified boxes

Labels: SAM 3's own boxes for six countable classes on all 52 photos were verified by hand
(every instance accepted or rejected with a reason, missed objects added; 191 boxes;
`labels/countable_boxes_v1_part1/2.json`, harness copy `detection_boxes_v1.json`). This
replaced the per-photo component labels as the yardstick for countable classes, because
those labels undercounted badly (one window on a facade photo where SAM 3 found six; one
light fixture in a kitchen with nine recessed lights and a chandelier).

Single full-frame pass, plain thresholds, from the verification data (precision and recall
over instances; count error per photo):

| Class | True objects | Threshold | Precision | Recall | Count MAE per photo |
|---|---|---|---|---|---|
| electrical outlet | 21 | 0.5 | 0.71 | 0.95 | 0.17 |
| window | 68 | 0.6 | 0.66 | 0.84 | 0.71 |
| door | 31 | 0.5 | 0.51 | 0.81 | 0.54 |
| light fixture | 24 | 0.5 | 0.32 | 0.92 | 0.85 |
| exterior light | 23 | 0.5 | 0.80 | 0.17 | 0.38 |
| kitchen cabinet | 24 | 0.5 | 0.45 | 0.42 | 0.15 |

Why precision is low, from the rejection reasons: windows of neighbouring buildings and
garage-door glass (window), appliance and cabinet doors and casings (door), plug-in lamps
and outdoor lanterns (light fixture); "exterior light" as a phrase misses porch lanterns
that "light fixture" finds (pooling the two on outdoor photos raises exterior-light recall
from 0.17 to 0.52); cabinets are a granularity mismatch (the label counts units, the
model boxes both doors and runs). Containment suppression (a same-class box mostly inside
another is dropped) raises window precision 0.55 to 0.62 at 0.5 but removes cabinet units
inside run boxes, so it must be per class.

Adjustments to measure next, one at a time against these labels: per-class thresholds;
phrases per class ("ceiling light", "recessed light", "chandelier" for fixtures; "porch
light", "wall lantern" for exterior lights; "cabinet door" for cabinets); a vision-model
check of each box's crop for the door and neighbour-building confusions; tiling for the
exterior windows. (Harness runs with tiling on and off follow in 10.3.)

## 11. Task decomposition: one call per task instead of one call for everything (2026-10-16)

Question (Bryan's): does the per-photo model do better when the same photo is sent several
times, each call with one narrow job, than when one call is asked for everything? The
literature calls this decomposed (or compositional) prompting; in detection it is
class-conditioned, multi-query inference. Branch `exp/focused-prompts`.

Design. Two released prompts, same output schema as `photo_analysis/v1` so the harness scores
them unchanged: `photo_countables/v1` (find and count the countable objects; floors, walls,
ceilings, trim and site surfaces forbidden) and `photo_surfaces/v1` (surfaces and their
materials, one entry each; countable objects forbidden). `evals.predict_photos --prompt-name`
runs a family; `evals.merge_photo_runs` merges N runs into one run's prediction files (room
type from the first run; label sets unioned per photo; the largest count per label; costs and
seconds summed). `gpt-4.1` through OpenAI, three repeats, the 52 real photos. Scored three
ways: per-photo label sets (`photo_components`, section 6), per-photo counts against the
verified boxes of section 10.2 (six classes, 191 objects; scratch scorer, not yet in the
harness), and the per-room inventory after reconciliation (section 7).

### 11.1 Per photo, prompts v1

| Run | Room accuracy | Component P / R / F1 | Label set identical across 3 repeats | Count MAE, 6 verified classes | Signed bias | Count recall | Cost per 52 photos | Seconds per photo |
|---|---|---|---|---|---|---|---|---|
| one pass, `photo_analysis/v1` (section 8) | 0.85 | 0.63 / 0.72 / 0.67 | 26 of 52 | 0.36 | -0.16 | 0.57 | $0.30 | 2.2 |
| countables pass alone | 0.87 | 0.59 / 0.45 / 0.51 | 35 of 52 | 0.30 | -0.07 | 0.70 | $0.30 | 1.8 |
| surfaces pass alone | 0.90 | 0.62 / 0.33 / 0.43 | 42 of 52 | (none listed) | | | $0.26 | 1.5 |
| decomposed: union of the two | 0.87 | 0.59 / 0.73 / 0.65 | 33 of 52 | 0.30 | -0.07 | 0.70 | $0.56 | 3.3 summed; 1.8 when the two calls run in parallel |

Counts per class against the verified boxes (count MAE per photo; count recall = found
objects over true objects):

| Class | one pass MAE / count recall | decomposed MAE / count recall |
|---|---|---|
| window | 0.63 / 0.79 | 0.58 / 0.82 |
| door | 0.42 / 0.38 | 0.46 / 0.38 |
| light fixture | 0.26 / 0.86 | 0.19 / 0.85 |
| exterior light | 0.31 / 0.30 | 0.25 / 0.43 |
| electrical outlet | 0.34 / 0.16 | 0.14 / 0.79 |
| kitchen cabinet | 0.22 / 0.51 | 0.15 / 0.79 |

The counts, which are what the engineer prices, improve on five of six classes, and the
under-count bias halves; outlets go from mostly missed to mostly found. Each narrow call is
also more repeatable than the wide one (35 and 42 photos with identical label sets across
repeats, against 26). Label-set F1 is a wash (0.67 to 0.65), and the per-label breakdown of
where its precision went (156 records each) is instructive:

- `furniture` false positives 22 to 83: the countables prompt said plug-in lamps are
  furniture, while the labels file loose furniture under `other`. A prompt convention error,
  not a vision error.
- Exterior surfaces listed on every exterior photo (roof 9 to 17 false positives, driveway 9
  to 18, landscaping 4 to 21, siding 6 to 12) and baseboard 11 to 29: the label guide lists
  site-wide items and baseboard once per room, so the per-photo labels carry them on some
  photos only. The surfaces pass does what it was told; the per-photo yardstick cannot tell
  "listed on the wrong photo" from "wrong". The room level settles it (11.3).
- Two vocabulary confusions: bathroom vanities came back as `kitchen_cabinet` plus `sink`
  (0 of 6 vanity records found, against 6 of 6 for the one-pass prompt), and curtains and
  shades came back as `blinds` (`window_treatment` 0 of 30 found; `blinds` false positives 3
  to 14).
- Gains on the surfaces side: carpet misses 15 to 3, wainscoting 6 to 0, vinyl flooring 12
  to 6; `wall_finish` false positives 55 to 23, `light_fixture` 34 to 21, stairs 8 to 0.

### 11.2 Countables prompt v2: definitions only

`photo_countables/v2` (released) changes nothing about counting; it adds label definitions
for the three confusions above (vanity is never `kitchen_cabinet`; `blinds` are slatted, every
curtain and shade is `window_treatment`, a shower curtain is `other`; loose furniture, lamps,
rugs, art and bath accessories are one `other` entry) and drops `furniture`, `chair`, `desk`,
`filing_cabinet` from its list. Its run stopped after 50 photos of the first repeat: the
OpenAI account ran out of credit mid-run, and the provider pool's per-call fallback sent the
rest to the Azure small model (those records are excluded here; `run.json` `models_reported`
is the purity check for every `--model` run from now on). So this is a paired comparison on
50 photos and one repeat, to be confirmed on three repeats after a top-up:

| Run (50 photos, repeat 1) | Component P / R / F1 | Count MAE, 6 classes | kitchen cabinet MAE / count recall | light fixture MAE |
|---|---|---|---|---|
| one pass v1 | 0.67 / 0.72 / 0.69 | 0.33 | 0.20 / 0.38 | 0.24 |
| decomposed, countables v1 | 0.58 / 0.73 / 0.65 | 0.30 | 0.12 / 0.69 | 0.20 |
| decomposed, countables v2 | 0.66 / 0.73 / 0.70 | 0.27 | 0.08 / 0.81 | 0.16 |

The definitions recover the precision (false positives 126 to 90 on these photos, `furniture`
27 to 0, `blinds` 5 to 0, `window_treatment` found 0 to 3 of 10) without touching recall, and
the counts improve further. The eight "lost" true positives of v2 against v1 are surface
labels the v1 countables call listed outside its brief (siding, roof, porch, driveway, gutter)
and chairs and a desk now filed as `other`; the surfaces pass supplies the former.

### 11.3 Per room, after reconciliation

Section 7's pipeline on the decomposed per-photo output (repeat 1 of the v1 prompts),
reconciled with the small model three times, against the one-pass equivalent of section 8.
The first decomposed run was reconciled by OpenAI `gpt-4o-mini` (the `LLM_PRIMARY=openai`
setting of the per-photo runs changed the reconciler too); the rerun on the Azure
`gpt-4.1-mini` deployment, the baseline's reconciler, is the like-for-like row.

| Per-photo input | Reconciler | count MAE | exact-count rate | signed bias | label F1 | inventory identical across 3 repeats | MAE without furniture | MAE without furniture and kitchen cabinets |
|---|---|---|---|---|---|---|---|---|
| one pass v1 (section 8) | Azure gpt-4.1-mini | 0.81 | 0.43 | +0.03 | 0.69 | 22 of 33 | 0.74 | 0.72 |
| decomposed v1 | OpenAI gpt-4o-mini | 0.93 | 0.45 | +0.32 | 0.70 | 30 of 33 | 0.75 | 0.68 |
| decomposed v1 | Azure gpt-4.1-mini | (pending: two attempts died, one on an Azure timeout, one on an Azure 500 whose per-call fallback hit the empty OpenAI account) | | | | | | |

The headline room MAE gets worse because of two things that are not decomposition: the v1
prompt's `furniture` rule (20 room-label pairs, bias +3.0; gone in v2) and the kitchen's
cabinets, which the reconciliation summed across the kitchen's two photos (10 + 8 = 18) where
it had merged 6 + 6 into 6 before, against a room label of 1. That last number exposes a
convention gap: the verified boxes count cabinet units, the room inventory counts the run.
Outside those two labels, the decomposed input is as good or better (MAE 0.72 to 0.68, bias
-0.08 to -0.01), the under-count is gone, and the inventories are stable for 30 rooms
instead of 22. Exact inventories per room stay rare (1 of 33) for the reason section 8 gave.

### 11.4 Reading and decision

Decomposition works for the thing it was meant to fix: the model counts repeated small
objects better when counting is its only job, and it answers more consistently. It costs
twice as much per photo ($0.56 against $0.30 per 52 photos, so about a dollar per property)
and no more wall time when the two calls run in parallel. Its label-set cost was a prompt
vocabulary error, fixed in v2 on the partial evidence above. Decision: adopt the two-call
per-photo step once v2 is confirmed on three repeats; wire it into `basis.vision.analyze`
(two calls, `merge_photo_runs.merge_components` as the merge) only then, the pipeline stays
on one call until that. Open from this section: settle the cabinet convention (units or run)
between the box labels and the room labels; make the reconciliation call carry the verified
per-photo counts rather than re-estimate them; a third narrow call (electrical only, or
exterior only) is the next thing to test once the two-call result is confirmed; add the
per-photo count metrics against the verified boxes to the harness. Blocked on Bryan: OpenAI
credit (v2, three repeats) and Replicate credit (the section 10.3 tiling and phrase runs have
returned 429 for every call for two hours).
