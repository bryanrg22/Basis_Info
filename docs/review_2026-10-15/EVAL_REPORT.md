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
| Component set precision | 0.627 | 260 predicted | | loose furniture is `other` on both sides (see the label guide); corrected 2026-10-16, see section 11 |
| Component set recall | 0.632 | 258 labelled | | |
| Component set F1 | 0.629 | | | |
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
| gpt-4.1-mini, v1 (today) | 0.81 | 0.79 | 0.63 | 0.63 | 0.63 | 16 of 52 | 0.50 | 0.74 | $0.06 | 4.3 |
| gpt-4.1-mini, v2 | 0.85 | 0.79 | 0.65 | 0.66 | 0.66 | 9 of 52 | 0.46 | 0.77 | $0.08 | 5.5 |
| gpt-4.1, v1 | 0.85 | 0.85 | 0.68 | 0.72 | 0.70 | 26 of 52 | 0.44 | 0.78 | $0.30 | 2.2 |
| gpt-4.1, v2 | 0.81 | 0.79 | 0.74 | 0.68 | 0.71 | 27 of 52 | 0.39 | 0.82 | $0.38 | 2.3 |

"Shared labels" are the (photo, label) pairs both the model and the labels list; the count
metrics there ask whether the model counts right what it did find. Per-photo count
metrics are not yet in the harness (computed from the raw records; add them).

Reading the table (component columns corrected 2026-10-16: the harness had counted a
predicted `other` as a false positive, section 11; the readings below were re-checked):

- The model is the lever. `gpt-4.1` raises component recall from 0.63 to 0.72 and F1 from
  0.63 to 0.70, gets 46 of 52 room labels right on all three repeats, returns the same
  label set across repeats for half the photos instead of a third, and answers in half
  the time. It costs five times more per photo, which is still under a dollar per
  property.
- The prompt moves less. Writing the conventions in (`v2`) improves the counts of what is
  found (exact count 0.74 to 0.77 on the small model, 0.78 to 0.82 on the large one) and
  trades recall for precision on label sets (on `gpt-4.1`: precision 0.68 to 0.74, recall
  0.72 to 0.68, F1 +0.01); on the small model it makes the output less stable (9 instead of
  16 photos identical across repeats). Recall is what the engineer needs (a missed system
  costs more than an extra candidate), so `v2` stays released for later experiments and the
  pipeline keeps `v1`.
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

### 10.3 Tiling: 2 x 2 tiles plus the full frame, masks merged by IoU

Harness runs (`evals.predict_boxes`, `detection_boxes` dataset, the verified boxes of 10.2),
same phrases and model, one pass each. The tiled run makes five SAM 3 calls per photo (260
calls, $0.50, three and a half hours under the Replicate throttle with 711 rate-limit
retries, no lost photos) against one call per photo for the single pass (52 calls, $0.16).
Tile masks are pasted back into frame coordinates and merged with the full-frame masks by
mask NMS (IoU 0.5).

| Run | threshold | mAP@0.5 | window | interior door | exterior door | light fixture | outdoor lighting | outlet | cabinet | precision / recall at threshold | count MAE / signed bias per photo-class |
|---|---|---|---|---|---|---|---|---|---|---|---|
| single pass | 0.5 | 0.653 | 0.84 | 0.85 | 0.18 | 0.61 | 0.67 | 0.88 | 0.55 | 0.46 / 0.80 | 1.50 / +1.09 |
| single pass | 0.6 | 0.653 | | | | | | | | 0.53 / 0.71 | 1.23 / +0.54 |
| 2 x 2 tiles + full, IoU merge | 0.5 | 0.577 | 0.71 | 0.61 | 0.15 | 0.54 | 0.59 | 0.88 | 0.56 | 0.32 / 0.83 | 2.45 / +2.22 |
| 2 x 2 tiles + full, IoU merge | 0.6 | 0.577 | | | | | | | | 0.39 / 0.74 | 1.82 / +1.39 |

Tiling hurts on these photos. Recall rises three points and everything else falls: the
objects our photos miss are not small (the facade windows the single pass finds are already
at 0.84), while tile edges cut doors, windows and fixtures into fragments whose IoU with the
full-frame mask is too low for NMS to remove, so the fragments survive as extra detections
(interior door 0.85 to 0.61, over-count doubled). Outlets and cabinets, the small-object
classes tiling was meant to help, do not move. The research note's premise (tiling for small
objects in drone imagery) does not transfer to interior photos taken at room scale.

Containment merging (the same tiles, a tile mask dropped when mostly inside a full-frame mask,
IoS 0.5; 225 calls, $0.40, 7 interior photos lost to the throttle) is worse again:
mAP@0.5 0.453, precision / recall 0.42 / 0.59 at 0.5. It removes the fragments (count bias
+2.22 to +0.59) but also removes real objects nested inside a larger mask of the same class:
recessed lights inside a ceiling fixture's mask (light fixture AP 0.61 to 0.23), outlets
and cabinet units inside runs (0.88 to 0.57, 0.55 to 0.30). The seven lost photos depress
the interior classes somewhat, but not by that much. Tiling is dropped.

Phrase set v2 (single pass, 52 calls, $0.20, no lost photos; "ceiling light", "recessed
light", "chandelier" for fixtures, "porch light", "wall lantern" for exterior lights,
"cabinet door" for cabinets): mAP@0.5 unchanged at 0.653 but the classes move in both
directions. Cabinets 0.55 to 0.74 and light fixtures 0.61 to 0.76 improve; interior doors
0.85 to 0.70 and outdoor lighting 0.67 to 0.48 get worse (the lantern phrases pick up
interior sconces and the door phrase set loses cased openings). Taking the better phrase set
per class would give mAP about 0.70, so the phrase table becomes per class; that and the
per-class thresholds are the next single-pass adjustments.

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
| one pass, `photo_analysis/v1` (section 8) | 0.85 | 0.68 / 0.72 / 0.70 | 26 of 52 | 0.36 | -0.16 | 0.57 | $0.30 | 2.2 |
| countables pass alone | 0.87 | 0.59 / 0.45 / 0.51 | 35 of 52 | 0.30 | -0.07 | 0.70 | $0.30 | 1.8 |
| surfaces pass alone | 0.90 | 0.62 / 0.33 / 0.43 | 42 of 52 | (none listed) | | | $0.26 | 1.5 |
| decomposed: union of the two (room type from the countables pass) | 0.87 | 0.59 / 0.73 / 0.65 | 33 of 52 | 0.30 | -0.07 | 0.70 | $0.56 | 3.3 summed; 1.8 when the two calls run in parallel |

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
repeats, against 26). Label-set F1 drops (0.70 to 0.65), and the per-label breakdown of
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
`filing_cabinet` from its list. Run on OpenAI only (the Azure fallback disabled, so the run is
pure `gpt-4.1`; `models_reported` confirms it), three repeats, $0.98. A first attempt had died
when the OpenAI account ran out of credit mid-run, and its 50 completed photos had already
pointed the same way.

Scoring this exposed a harness defect: `score_photo_components` dropped `other` from the
labels but not from the predictions, so every photo where the model listed `other` was
charged a false positive. With v2 that was 42 photos, with the one-pass prompt 20. Fixed in
this branch with a test; every per-photo number in sections 6, 8 and 11 was rescored and
corrected (precision rose 0.01 to 0.05 across runs; no reading of section 8 reversed, see
its note). The merge tool also gained `--room-from N`, since the surfaces pass is the best
room-type judge (0.90 against 0.85 for the one-pass prompt).

| Run (52 photos, 3 repeats) | Room accuracy | Component P / R / F1 | Label set identical across repeats | Count MAE, 6 classes | Signed bias | Count recall | window MAE | light fixture MAE | outlet MAE | cabinet MAE / count recall |
|---|---|---|---|---|---|---|---|---|---|---|
| one pass v1 | 0.85 | 0.68 / 0.72 / 0.70 | 26 of 52 | 0.36 | -0.16 | 0.57 | 0.63 | 0.26 | 0.34 | 0.22 / 0.51 |
| decomposed, countables v1, room from surfaces | 0.90 | 0.59 / 0.73 / 0.65 | 33 of 52 | 0.30 | -0.07 | 0.70 | 0.58 | 0.19 | 0.14 | 0.15 / 0.79 |
| decomposed, countables v2, room from surfaces | 0.90 | 0.66 / 0.72 / 0.69 | 31 of 52 | 0.29 | -0.12 | 0.66 | 0.51 | 0.19 | 0.15 | 0.17 / 0.74 |

The definitions recover the precision (false positives 390 to 279 over the 156 records;
`furniture` 83 to 0, `blinds` 14 to 0, `window_treatment` found again) at the one-pass
recall, so the label-set score is now level with the one-pass prompt (F1 0.69 against 0.70)
while the counts keep their gain (MAE 0.29 against 0.36; outlets 0.15 against 0.34; windows
0.51 against 0.63). The v2 prompt gives back a little of v1-countables' cabinet and outlet
gain and its room accuracy (0.83 against 0.87 on its own answer; the merge takes the
surfaces pass's answer instead). Door counts do not move under any prompt.

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
| decomposed v1 | Azure gpt-4.1-mini (like for like) | 0.84 | 0.47 | +0.20 | 0.70 | 28 of 33 | 0.66 | 0.63 |

The headline room MAE moves little (0.81 to 0.84 like for like) and the bias turns positive,
for two reasons that are not decomposition: the v1 prompt's `furniture` rule (20 room-label
pairs, bias +3.0; gone in v2) and the kitchen's cabinets, where the per-photo counts rose
from 6 and 6 to 10 and 8 and the reconciliation kept 10 (the OpenAI reconciler summed them
to 18) against a room label of 1. That last number exposes a convention gap: the verified
boxes count cabinet units, the room inventory counts the run. Outside those two labels the
decomposed input is clearly better (MAE 0.74 to 0.66 without furniture, 0.72 to 0.63 without
furniture and cabinets; bias -0.07 to -0.04), the exact-count rate rises (0.43 to 0.47), and
the inventories are stable for 28 rooms instead of 22. Exact inventories per room stay rare
(1 of 33) for the reason section 8 gave. The second attempt at this row died on an Azure
timeout, the third on an Azure 500 whose per-call fallback hit the empty OpenAI account;
`evals.predict_rooms` now retries transient provider errors per room, as the photo runner
always did.

### 11.4 Reading and decision

Decomposition works for the thing it was meant to fix: the model counts repeated small
objects better when counting is its only job, and it answers more consistently. It costs
twice as much per photo ($0.56 against $0.30 per 52 photos, so about a dollar per property)
and no more wall time when the two calls run in parallel. Its label-set cost was a prompt
vocabulary error, fixed in v2 and confirmed on three repeats. Decision: adopt the two-call
per-photo step (`photo_countables/v2` + `photo_surfaces/v1`, room type from the surfaces
call, `merge_photo_runs.merge_components` as the merge) and wire it into
`basis.vision.analyze` as the next code change; the pipeline stays on one call until that
lands.

*Wired (2026-10-16, follow-up PR to this branch).* `basis.vision.analyze.analyze_photo_decomposed`
runs `photo_countables/v2` and `photo_surfaces/v1` concurrently on the prepared image and
merges them with `basis.vision.merge` (the merge moved out of `evals.merge_photo_runs`, which
now imports it, so the harness and the pipeline share one implementation): components unioned
by label with the largest count, the countables call's material and attachment for a label
both list, every `other` kept; room type, confidence, floor and ordinal from the surfaces
call. The step is selected by `VISION_PHOTO_MODE` (`single` | `decomposed`), read by the
`analyze_photo` activity from the worker's settings and checked at worker startup
(`build_deps` loads the mode's prompts against the manifest). Each call is its own
`llm_calls` row and the activity's cost is their sum; a component row's `raw.prompt_version`
is `photo_countables/v2+photo_surfaces/v1`; the stored analysis JSON records the mode and
each call's attempts. If either call stays unusable after its retry the photo is
`ambiguous`/`needs_review` naming the call; a provider error or an exhausted budget on either
call fails the photo as a whole. **The default is still `single`**: the single-call record,
request and heartbeats are pinned unchanged by a regression test, and switching the deployed
worker to `decomposed` is Bryan's call (about a dollar more per property). Open from this
section: settle the cabinet convention (units or run)
between the box labels and the room labels; make the reconciliation call carry the verified
per-photo counts rather than re-estimate them; a third narrow call (electrical only, or
exterior only) is the next thing to test once the two-call result is confirmed; add the
per-photo count metrics against the verified boxes to the harness. Blocked on Bryan:
Replicate credit (the section 10.3 containment-merge and phrase runs are still throttled).

## 12. Frontier OpenAI models on the per-photo step: gpt-6.1-sol and gpt-6-astra (2026-10-16)

Question (Bryan's): the account exposes gpt-5.4, gpt-5.5, gpt-6-astra, gpt-6-luna, gpt-6-sol
and gpt-6.1-sol; sections 6 to 11 used gpt-4.1 because the deployed pipeline was on
gpt-4.1-mini and the comparison was kept in one family. Section 8 showed the model, not the
prompt, moves the numbers, so the newer models are the obvious lever. Prices (standard,
developers.openai.com/api/docs/pricing, 2026-10-16, per million tokens in / out): gpt-6.1-sol
$2 / $10, gpt-6-sol $2 / $10, gpt-6-astra $10 / $50, gpt-6-luna $0.10 / $0.50, gpt-5.5 $5 / $30,
gpt-5.4 $2.50 / $15; gpt-4.1 is $2 / $8.

What had to change to run them: the gpt-6 family takes the reasoning-model request shape
(`max_completion_tokens`, no temperature; the provider's rule now covers `gpt-[5-9]`), it
spends the output budget on reasoning before answering (at the pipeline's 1200-token budget
two photos in three came back truncated, 45 seconds each), and it accepts a
`reasoning_effort`. `evals.predict_photos --reasoning-effort low --max-tokens 6000` is
threaded through `VisionContext` and `ChatRequest`; every run below used it. The count
yardstick is now a harness task (`photo_counts`, derived from the verified boxes, section
10.2; the scratch scorer of section 11 reproduces exactly). Three repeats on the 52 photos,
`photo_analysis/v1`, one call per photo.

| Model, one pass, low effort | Room accuracy | Room pass^3 | Component P / R / F1 | Label set identical across 3 repeats | Count MAE, 6 classes | Exact count | Signed bias | Count recall | Cost per 52 photos | Seconds per photo |
|---|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1 (section 8) | 0.85 | 0.85 | 0.68 / 0.72 / 0.70 | 26 of 52 | 0.36 | 0.77 | -0.16 | 0.57 | $0.30 | 2.2 |
| gpt-6.1-sol | 0.83 | 0.83 | 0.55 / 0.90 / 0.68 | 25 of 52 | 0.30 | 0.83 | -0.03 | 0.73 | $0.48 | 10.5 |
| gpt-6-astra | 0.81 | 0.81 | 0.55 / 0.88 / 0.68 | 27 of 52 | 0.29 | 0.83 | -0.01 | 0.76 | $2.44 | 11.0 |

Counts per class against the verified boxes (count MAE per photo / count recall):

| Class | gpt-4.1 | gpt-6.1-sol | gpt-6-astra |
|---|---|---|---|
| window | 0.63 / 0.79 | 0.51 / 0.88 | 0.49 / 0.90 |
| door | 0.42 / 0.38 | 0.22 / 0.82 | 0.24 / 0.81 |
| light fixture | 0.26 / 0.86 | 0.33 / 0.90 | 0.34 / 0.94 |
| exterior light | 0.31 / 0.30 | 0.26 / 0.48 | 0.24 / 0.55 |
| electrical outlet | 0.34 / 0.16 | 0.10 / 0.83 | 0.08 / 0.86 |
| kitchen cabinet | 0.22 / 0.51 | 0.37 / 0.19 | 0.37 / 0.21 |

Reading:

- The frontier models see what gpt-4.1 misses. Component recall 0.72 to 0.90 (misses 228 to
  85 over the 156 records: carpets, gutters, refrigerators and window treatments that gpt-4.1
  left out are found), door count recall 0.38 to 0.82, outlet count recall 0.16 to 0.83,
  windows 0.79 to 0.88, and the systematic under-count of section 6 is gone (bias -0.16 to
  -0.03). Exact counts rise from 77% to 83% of (photo, class) pairs with one prompt and no
  decomposition.
- Their precision drop is convention, not hallucination. The extra false positives are
  `furniture` and `chair` (loose furniture, filed as `other` by the labels), plain painted
  walls listed as `wall_finish` (78 against 55), baseboards in every photo that shows one,
  and site surfaces on every exterior photo (roof, siding, landscaping, stairs, sidewalk,
  exterior doors seen from outside). The per-photo labels list those once per room; the
  model lists everything visible. The decomposed prompts of section 11 encode exactly these
  conventions, which is why the decomposed run on these models is the one that matters.
- Cabinets go the other way (count recall 0.51 to 0.19): both gpt-6 models count a run of
  cabinetry as one unit where the verified boxes count doors and drawer stacks. The
  countables prompt defines the unit; the one-pass prompt does not.
- Room type is slightly worse (0.85 to 0.83 and 0.81) and the errors differ in kind: gpt-4.1
  answered `other` for rooms it did not recognize; the gpt-6 models answer `ambiguous`
  (abstain) for the basement bedrooms and `mechanical` for the condenser and gas-meter
  close-ups (the label guide files them under the exterior). Abstention is what the review
  gate wants; the exterior-equipment convention is a prompt fix.
- gpt-6.1-sol equals gpt-6-astra on every number that matters at a fifth of the price, so
  astra is not worth its cost for this step. Sol costs 1.6 times gpt-4.1 per pass ($0.48
  against $0.30 per 52 photos; about $1 per property for the two-call step) and takes five
  times longer per photo (10 seconds at low effort, which only matters for the review
  gate's wall clock, since the calls run in parallel).

### 12.1 Decomposed on gpt-6.1-sol, and every model on one yardstick

The decomposed run (`photo_countables/v2` + `photo_surfaces/v1`, room type from the surfaces
call, three repeats each, low effort) on gpt-6.1-sol. Its first attempt was invalid: the v2
prompt asks for a note naming the loose items, sol wrote notes over 200 characters, the strict
JSON schema cannot carry that limit, and the validator then rejected the whole photo, so 44 of
156 records fell back to empty (the same defect had cost Opus 3 records, Fable 2, gpt-4.1 1,
left as they are). `basis.vision.schemas` now cuts an over-long note or description instead
of failing the photo; the rerun below is clean (156 records, 2 empty answers from the model
itself, no errors). The Claude rows come from section 14 (same prompts, three repeats, the
Anthropic provider) and are rescored here with `photo_counts`.

| Model and prompt | Room accuracy | Component P / R / F1 | Label set identical across 3 repeats | Count MAE | Exact count | Signed bias | Count recall | window / door / light / ext. light / outlet / cabinet count recall | Cost per 52 photos | Seconds per photo |
|---|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1, one pass | 0.85 | 0.68 / 0.72 / 0.70 | 26 | 0.36 | 0.77 | -0.16 | 0.57 | .79 / .38 / .86 / .30 / .16 / .51 | $0.30 | 2.2 |
| gpt-4.1, decomposed | 0.90 | 0.66 / 0.72 / 0.69 | 31 | 0.29 | 0.80 | -0.12 | 0.66 | .78 / .38 / .72 / .43 / .78 / .74 | $0.59 | 2.2 in parallel |
| gpt-6.1-sol, one pass | 0.83 | 0.55 / 0.90 / 0.68 | 25 | 0.30 | 0.83 | -0.03 | 0.73 | .88 / .82 / .90 / .48 / .83 / .19 | $0.48 | 10.5 |
| gpt-6.1-sol, decomposed | 0.90 | 0.66 / 0.84 / 0.74 | 23 | 0.18 | 0.87 | +0.03 | 0.87 | .88 / .89 / .81 / .90 / .86 / .89 | $0.79 | 11 in parallel |
| gpt-6-astra, one pass | 0.81 | 0.55 / 0.88 / 0.68 | 27 | 0.29 | 0.83 | -0.01 | 0.76 | .90 / .81 / .94 / .55 / .86 / .21 | $2.44 | 11.0 |
| Claude Opus 5.5, one pass | 0.88 | 0.59 / 0.91 / 0.72 | 19 | 0.22 | 0.84 | -0.08 | 0.75 | .94 / .76 / .75 / .42 / .79 / .51 | $1.25 | 6.2 |
| Claude Opus 5.5, decomposed | 0.88 | 0.69 / 0.86 / 0.76 | 20 | 0.17 | 0.88 | -0.05 | 0.83 | .91 / .81 / .85 / .57 / .78 / .89 | $2.23 | 5.3 in parallel |
| Claude Fable 5.1, one pass | 0.85 | 0.57 / 0.94 / 0.71 | 17 | 0.26 | 0.83 | -0.00 | 0.79 | .90 / .76 / .90 / .39 / .87 / .68 | $3.24 | 7.4 |
| Claude Fable 5.1, decomposed | 0.87 | 0.72 / 0.89 / 0.79 | 25 | 0.22 | 0.86 | -0.08 | 0.76 | .79 / .80 / .81 / .42 / .81 / .83 | $5.76 | 5.8 in parallel |

Reading:

- Decomposition is worth more on the frontier models than it was on gpt-4.1. On sol it halves
  the count error (0.30 to 0.18), lifts count recall 0.73 to 0.87 with the bias at zero, fixes
  the cabinet unit (0.19 to 0.89) because the countables prompt defines it, and, most
  notably, fixes exterior lights (0.48 to 0.90), the class every one-pass run failed. On Opus
  it takes count error 0.22 to 0.17 and cabinets 0.51 to 0.89.
- gpt-6.1-sol decomposed and Opus 5.5 decomposed are the two best configurations and are
  within noise of each other on counts (0.18 against 0.17 MAE; sol has the higher count recall
  0.87 against 0.83, Opus the better label sets 0.76 against 0.74 and room type 0.88 against
  0.90 the other way). Sol costs a third of Opus. Fable has the best label sets of all (0.79)
  at the highest price and no count advantage.
- Count recall per class on the best configuration is now 0.81 to 0.90 for every class, from
  0.16 to 0.86 at the start of this report. What is left is precision (the one-pass frontier
  models list everything visible, including loose furniture and plain walls, which the
  decomposed prompts suppress) and the room-type abstentions on the basement bedrooms.
- Every number here is one property, 52 photos, so the ranking between sol and Opus is not
  settled; the gap to gpt-4.1 is.

Decision: the per-photo step runs gpt-6.1-sol with the two-call prompts at low effort
(`VISION_MODEL=gpt-6.1-sol`, `VISION_PHOTO_MODE=decomposed`, reasoning effort low, output
budget 6000), about $1 per property for the two calls and eleven seconds per photo in
parallel. Opus 5.5 decomposed is the fallback provider at three times the price with the
same counts. Next: the same table on a second client package before anything is tuned
further; a vision-model second look at the SAM 3 boxes of the weak classes; the cabinet
convention with the engineer.

## 13. Coverage of the engineer's report rows (2026-10-16)

Question (Bryan's): for each of the 46 rows of the engineer's asset schedule, did the
pipeline produce the evidence that row needs, and what share of the study's cost, by
bucket, is covered? This is the product metric COMPONENT_TAXONOMY.md section 2 asked for;
the per-photo and per-room numbers of sections 6 to 11 are the engineering diagnostics
behind it. Branch `claude/report-rows-eval`.

Design. Dataset `report_rows` (private file next to `assets_v1.json`; format in
`backEnd/evals/README.md`; built from the assets file and the taxonomy's section 1 by a
private script): one item per report row with its bucket, its share of the study's cost,
its kind (A countable objects, B surfaces, C not photo-derivable) and the evidence rule:
any-of vocabulary labels, a room-type filter where the bucket depends on the room (kitchen
millwork against restroom millwork; kitchen and utility sinks against bathroom plumbing),
material tokens where the bucket depends on the material (tile must be ceramic, porcelain
or stone; painting is a painted drywall wall; a wall covering is wallpaper or a soft
covering), and whether the engineer needs a count or a material. A photo-derivable row
with no label in the vocabulary names the label it needs and scores as a *vocabulary gap*,
never as a miss. Scorer `score_report_rows`: per row evidenced / missed / vocabulary gap /
not photo-derivable with the rooms and labels behind it; coverage over the photo-derivable
rows (Wilson interval; gaps count against it: the product number) and over the rows the
vocabulary can express (the model number); the same weighted by cost share, per bucket;
count-present and material-present rates; with repeats, pass^k over the rows (coverage
stable across repeats). Standard followed: checklist evaluation as in ExpertLongBench and
CheckEval (one binary judgment per required item, aggregated as recall, not-applicable
items kept out of the denominator); weighting by cost mirrors the IRS Cost Segregation ATG's
requirement that a study's asset list tie out to its total cost. The ceiling is the
`gold_inventory` baseline: the hand-labelled room inventory (section 7) with the materials
of the per-photo labels, scored as if the pipeline had produced it, which is what the
vocabulary and the labelling conventions allow at best.

The 46 rows split into 33 the vocabulary can express, 6 vocabulary gaps and 7 kind C rows.
By cost, the kind C rows (foundations, structural framing, interior partitions, site
utilities, fire protection, data, access control) are about 34% of the study, the six
vocabulary gaps about 2%, and the 33 scoreable rows about 64%. Materials for a saved
inventory come from the reconciliation call's raw records per repeat (the heuristic merge
carries the per-photo materials); a material-constrained row without a reported material
is a miss. Scored: the gold ceiling, the one-pass run of section 8, the decomposed run of
section 11.3 (both three reconciliation repeats, Azure `gpt-4.1-mini`), and the
deterministic heuristic merge of each.

| Inventory | Rows evidenced, of 39 photo-derivable | Row coverage [95%] | Of the 33 in vocabulary | Cost coverage, photo-derivable rows | Cost coverage, whole study | 5-year (16 rows) | 7-year (1) | 15-year (6) | 39-year (16) | Count present (16 rows need one) | Material present (9 need one) | Stable across 3 repeats |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gold inventory + per-photo label materials (ceiling) | 32 | 0.82 [0.67, 0.91] | 32 | 0.968 | 0.634 | 13 (cost 0.95) | 0 | 5 (1.00) | 14 (0.98) | 15 | 9 | one run |
| one pass gpt-4.1 per photo, reconciliation call | 32 | 0.82 [0.67, 0.91] | 32 | 0.974 | 0.638 | 13 (0.98) | 0 | 5 (1.00) | 14 (0.98) | 16 | 8 | 32 of 32 (pass^3 0.82) |
| one pass, heuristic merge | 32 | 0.82 | 32 | 0.974 | 0.638 | 13 | 0 | 5 | 14 | 16 | 8 | one run |
| decomposed per photo (v1 prompts), reconciliation call | 32 | 0.82 [0.67, 0.91] | 32 | 0.974 | 0.638 | 13 (0.98) | 0 | 5 (1.00) | 14 (0.98) | 16 | 8 | 32 of 32 (pass^3 0.82) |
| decomposed, heuristic merge | 32 | 0.82 | 32 | 0.974 | 0.638 | 13 | 0 | 5 | 14 | 16 | 8 | one run |

The four pipeline variants give the same outcome on every row. Missed rows, by system
name:

- **Pipeline (all four variants): Wall Coverings.** A vision miss on the material. This
  study's wall covering is the wallpaper wainscot of one hall (the label guide files a
  wainscot wallpaper with its chair rail as one `wainscoting`); both per-photo prompts and
  both reconciliations list a `wainscoting` in that hall and call its material `wood`, so
  the rule (wainscoting or wall finish with a wallpaper, fabric, vinyl or plastic
  material) is not met. The gold inventory evidences it through the label material
  `embossed_wallpaper`. The material-present rate (8 of 9) is this row.
- **Gold ceiling: FF&E.** A convention gap, not a vision miss: the room labels file loose
  furniture under `other` (LABEL_GUIDE section 4, the one open convention decision), while
  the per-photo labels and the prompts use `furniture`; the pipeline evidences the row with
  `furniture`, `chair` and `desk` in most rooms. The gold ceiling's count-present rate
  (15 of 16) is this row too.
- **Vocabulary gaps (6 rows, about 2% of cost), never a miss:** Movable Shed (`shed`),
  Bathroom Accessories (`bathroom_accessory`), Exterior Equipment Concrete Pads
  (`concrete_pad`), named in the taxonomy; and Interior Half Walls (`half_wall`), Door &
  Window Guards (`window_guard`) and Flooring - Sealed Concrete (`concrete_floor`), found
  while writing the rules (COMPONENT_TAXONOMY.md section 2 updated; it also had Bathroom
  Accessories in the wrong bucket: the report puts it in 39-year).
- **Not photo-derivable (7 rows, about 34% of cost):** Communication / Data, Access
  Control, Foundations, Fire Protection & Alarm, Interior Framing / Partitions, Structural
  Framing, Site Utilities. Scored apart; the product should say these come from the
  appraisal, the plans or the cost model rather than pretend to find them.

Reading.

1. **Row presence is not where the pipeline fails on this study.** It evidences every
   row the vocabulary can express except one, the same 32 rows the human inventory
   evidences, with either per-photo prompt, with or without the reconciliation call, and
   in all three repeats. Over-detection, which section 7 penalizes, does not hurt here, and
   the rule is deliberately lenient: one object of an allowed label in one allowed room
   evidences a row. So this metric does not separate the pipelines today; sections 7 and
   11 (counts, materials, duplicates) do, and the engineer prices from those.
2. **Two of the "evidenced" rows lean on the room filter and on a convention the labels
   reject.** The restroom millwork row is evidenced by `kitchen_cabinet` objects in
   bathrooms (the vanity confusion of section 11.1; the room filter, which uses the room
   identity the pipeline already has from the file names, rescues the row). Painting is
   evidenced by `wall_finish` with material `drywall`, which the model lists in most rooms
   and the label guide says is not an object. Both are convention gaps to settle with the
   engineer, not model errors.
3. **The ceiling is the vocabulary.** With a perfect model the row coverage of this study
   is 33 of 39 (0.85) until six labels exist; the gaps are cheap in cost share (about 2%)
   but each is a row the engineer wrote. The missed row is a material misread on a
   surface, the kind-B failure the taxonomy predicted: the per-room step must carry the
   material, and the material must be read from the photo, not inferred from the label.
4. **Limits.** One study, 39 rows: the Wilson interval is wide (0.67 to 0.91), and the
   rules were written from this study's report, so a second study will add rows and
   rules. The eval says whether evidence exists, not whether it is right: that is the
   `room_inventory` count metrics and, for the bucket, `assets`. Cost coverage counts a
   row's whole cost as covered when any evidence exists; the engineer's quantities would
   let it score the share of each row's quantity found, which this report does not list.

Decision. Keep `report_rows` as the product-level gate: a run is not acceptable while an
in-vocabulary row is missed or a row flips across repeats. Next: add the six labels to the
vocabulary and the prompts (`shed`, `bathroom_accessory`, `concrete_pad`, `half_wall`,
`window_guard`, `concrete_floor`); make the reconciliation call carry the per-photo
materials rather than re-state them (the wainscot material was right in no call); settle
the furniture and painted-drywall conventions with the engineer; re-run on the next study
and extend the rules file.

Commands (from `backEnd/`):

```bash
BASIS_ENV=test BASIS_EVAL_DATA_DIR=~/basis-eval-data uv run python -m evals.run --split dev --no-db
BASIS_ENV=test BASIS_EVAL_DATA_DIR=~/basis-eval-data uv run python -m evals.run --split dev --no-db \
    --repeats 3 --predictions ~/basis-eval-data/predictions/rooms_v1_pp41_recmini
BASIS_ENV=test BASIS_EVAL_DATA_DIR=~/basis-eval-data uv run python -m evals.run --split dev --no-db \
    --predictions ~/basis-eval-data/predictions/rooms_v1_pp41_recmini --inventory-file room_inventory.heuristic.json
```
## 14. Claude models on the per-photo step (2026-10-16)

Question: do Anthropic's current models move the per-photo numbers of sections 8 and 11,
and at what cost? Same photos, prompts, harness and scoring as those sections; only the
provider changes. The Anthropic provider (`basis.llm.anthropic_provider`, this branch) sends
the pydantic schema as `output_config.format` (the structured-outputs grammar; the unsupported
numeric and length keywords are moved into descriptions by the SDK's `transform_schema` and
still enforced by pydantic on the reply), the image as a base64 block before the text, and
`output_config.effort` = `low` (`--reasoning-effort low`): thinking cannot be turned off on
these models, so `low` is the nearest thing to the non-reasoning `gpt-4.1` baseline and keeps
a pass under the $5 line set for this experiment (the API defaults are `high` on Fable 5.1 and
`medium` on Opus 5.5; higher effort is untested here). `temperature` is not accepted by these
models. The wire `max_tokens` is the 1200-token answer budget plus a 4096-token thinking
allowance (thinking counts toward `max_tokens`). No server-side fallback was enabled, so a run
can only contain the model it names; `run.json` `models_reported` is a single model in every
run below. A one-pass probe (`vision_v1_fable51_probe`, one repeat) came in at $3.23 per 52
photos, under the line, before the full runs were started.

Runs: `BASIS_ENV=dev LLM_PRIMARY=anthropic`, `--repeats 3 --concurrency 2 --budget-usd 25`,
no other provider configured. For each model: one pass with `photo_analysis/v1`;
`photo_countables/v2`; `photo_surfaces/v1`; the last two merged with `merge_photo_runs
--room-from 2` (room type from the surfaces pass, as in 11.2). Scored with `evals.run --split
dev --no-db --repeats 3 --predictions <dir> --report <dir>/eval.md`; the rows below are
`docs/review_2026-10-15/repro/photo_run_table.py` over the run directories (it reproduces the
gpt-4.1 rows of sections 8 and 11 from their directories, so the two sets of numbers are
computed the same way). Component P/R/F1 and room accuracy are repeat 1 with `other` excluded;
"identical" counts photos whose sorted label list is the same on all three repeats.

| Run (52 photos, 3 repeats) | Room accuracy | Room pass^3 | Component P / R / F1 | Label set identical across repeats | Cost per 52 photos | Seconds per photo |
|---|---|---|---|---|---|---|
| gpt-4.1, one pass v1 (section 8) | 0.85 | 0.85 | 0.68 / 0.72 / 0.70 | 26 of 52 | $0.30 | 2.2 |
| gpt-4.1, countables v2 alone (11.2) | 0.83 | 0.81 | 0.71 / 0.40 / 0.51 | 35 of 52 | $0.33 | 2.2 |
| gpt-4.1, surfaces v1 alone (11.1) | 0.90 | 0.90 | 0.62 / 0.33 / 0.43 | 42 of 52 | $0.26 | 1.5 |
| gpt-4.1, decomposed v2, room from surfaces (11.2) | 0.90 | 0.90 | 0.66 / 0.72 / 0.69 | 31 of 52 | $0.59 | 3.7 summed; 2.2 in parallel |
| Fable 5.1, one pass v1 | 0.85 | 0.83 | 0.57 / 0.94 / 0.71 | 17 of 52 | $3.24 | 7.4 |
| Fable 5.1, countables v2 alone | 0.85 | 0.85 | 0.79 / 0.50 / 0.61 | 38 of 52 | $3.21 | 5.3 |
| Fable 5.1, surfaces v1 alone | 0.87 | 0.87 | 0.65 / 0.40 / 0.49 | 33 of 52 | $2.55 | 4.5 |
| Fable 5.1, decomposed v2, room from surfaces | 0.87 | 0.87 | 0.72 / 0.89 / 0.79 | 25 of 52 | $5.76 | 9.8 summed; 5.8 in parallel |
| Opus 5.5, one pass v1 | 0.88 | 0.88 | 0.59 / 0.91 / 0.72 | 19 of 52 | $1.25 | 6.2 |
| Opus 5.5, countables v2 alone | 0.90 | 0.85 | 0.74 / 0.50 / 0.59 | 29 of 52 | $1.23 | 4.7 |
| Opus 5.5, surfaces v1 alone | 0.88 | 0.88 | 0.63 / 0.37 / 0.47 | 36 of 52 | $1.00 | 4.2 |
| Opus 5.5, decomposed v2, room from surfaces | 0.88 | 0.88 | 0.69 / 0.86 / 0.76 | 20 of 52 | $2.23 | 8.9 summed; 5.3 in parallel |

Every Claude call succeeded on its first attempt: 936 structured calls (52 photos x 3
repeats x 3 prompts x 2 models) plus the 52-photo probe, with no rate limit, timeout, refusal
or schema failure, so the attempt counts and error lists in `photo_raw.jsonl` are all 1 and
empty. The seconds are wall time per call at concurrency 2; the Fable one-pass run had one
42 s call, its median is 7.0 s.

Reading:

- Recall is the gain. On the 258 labelled (photo, label) pairs, repeat 1, the one-pass
  prompt misses 72 with `gpt-4.1`, 16 with Fable 5.1 and 23 with Opus 5.5; decomposed, 72
  against 29 and 36. The Claude models list what is there (9.5 and 9.3 labels per photo
  against 6.0), which is what the engineer asked for (section 8: a missed system costs more
  than an extra candidate).
- The one-pass precision drop (0.68 to 0.57 / 0.59) is mostly convention, not vision. Of
  Fable's 184 one-pass false positives on repeat 1, 30 are `furniture` and `chair` (the
  labels file loose furniture as `other`, prompt v1 does not say so) and 49 are `wall_finish`,
  `ceiling_finish` and `baseboard` listed on photos where the label guide carries them once
  per room; `gpt-4.1` has 8 and 26 of those. The countables v2 definitions (11.2) remove the
  first group entirely (0 `furniture`/`chair` false positives in the decomposed runs) and the
  decomposed false-positive counts land where `gpt-4.1`'s are (91 and 102 against 96) while
  the misses stay at 29 and 36 against 72. Hence the decomposed F1: 0.79 (Fable) and 0.76
  (Opus) against 0.69.
- Room type does not move: 0.85 to 0.90 across all twelve rows, inside the n = 52 interval
  ([0.72, 0.92] for 0.85). Opus 5.5 is the most consistent (0.88 and pass^3 0.88 on three of
  its four rows). The Claude models answer `ambiguous` a little more often (6 and 3 of 156
  one-pass records against 1), which the pipeline routes to review.
- Repeatability is worse one-pass and level decomposed. The Claude one-pass runs return the
  same label set on 17 and 19 photos (against 26), a consequence of listing more labels: more
  items, more items to vary. Narrow prompts recover it (Fable countables 38 of 52, the best
  of any countables run; surfaces 33 and 36 against 42); the decomposed union is 25 and 20
  against 31. Temperature cannot be pinned on these models, so this is the floor at `low`
  effort.
- Cost and time. Per 52 photos the decomposed step is $5.76 on Fable 5.1 and $2.23 on
  Opus 5.5 against $0.59 on `gpt-4.1`: about $6 and $2 per property against 60 cents. Per
  photo the Claude calls take 4 to 7 s against 1.5 to 2.2 s, 5.8 and 5.3 s for the two calls
  in parallel. Opus 5.5 gives most of Fable's gain (F1 0.76 against 0.79, misses 36 against
  29) at 39% of its price. The effort lever is unexplored: `low` was chosen for the
  comparison, and the prompts were written for `gpt-4.1`, not tuned for Claude.
- What this section does not do: score the counts against the verified boxes. The harness
  now writes a `photo_counts` block into every `eval.md` (the `photo_counts` task merged
  from `exp/focused-prompts`), and the
  orchestrating session reads those from the prediction directories; the per-class count
  reading is theirs.

Spend for this section: $40.68 (Fable 5.1 runs $27.01, Opus 5.5 runs $10.45, the probe
$3.23). Prediction directories: `vision_v1_{fable51,opus55}`, `vision_countables_v2_*`,
`vision_surfaces_*`, `vision_decomposed_v2_*` and `vision_v1_fable51_probe` under
`$BASIS_EVAL_DATA_DIR/predictions`, each with its `eval.md`.

Decision for Bryan: the two-call step on Opus 5.5 at `low` effort is the cheapest configuration
measured here that beats the `gpt-4.1` baseline on misses (36 against 72) at level precision;
it costs $2.23 per 52 photos against $0.59 and 5 s per photo against 2. Whether the +0.07 F1
is worth four times the per-property spend, and whether to spend a run on `medium` effort or
a Claude-tuned prompt before deciding, is the open question; the provider and the routing are
in place either way (`LLM_PRIMARY=anthropic`, `VISION_MODEL=claude-opus-5-5`).

## 15. Per-photo accuracy campaign: counts against the verified boxes (2026-10-16)

Question: how close can the per-photo counts get to the client's verified counts? Starting
point (section 12.1): `gpt-6.1-sol`, decomposed (`photo_countables/v2` + `photo_surfaces/v1`),
low effort: count MAE 0.180, exact 0.865, count recall 0.874, label F1 0.741, $0.79 per 52
photos. Every variant below ran on the same 52 photos of the one study, three repeats, scored
by `evals.run` (`photo_counts`, `photo_components`, `photo_rooms`); every run reported only the
model it asked for (`models_reported`), and none lost a photo.

What had to be known before choosing experiments:

- The photos are 800 x 600 (51) and 800 x 408 (1). The pipeline sends them as they are, so
  a crop adds no pixels; it only enlarges what is there.
- In a decomposed run every count comes from the countables call (the surfaces call names
  no countable object), so a counting experiment replaces only that call; label F1 and room
  type stay the baseline's unless the experiment changes the surfaces call.
- Of the baseline's (photo, class) errors, most are the same in all three repeats (for
  example the office: 4 windows every time against 2 boxed). Repeat-to-repeat noise, the
  only thing self-consistency can remove, is the smaller part.
- The comparison statistic: the per-photo mean absolute count error (over the six classes
  and three repeats) minus the baseline's, averaged over photos, with a 95% interval from a
  paired bootstrap over photos (5,000 resamples). One document, so the harness's clustered SE
  is not available; photos are the unit.
- Anything that chooses a rule from the labels (which source per class, which SAM 3 score
  threshold) is scored leave-one-photo-out: each photo is predicted by the rule fitted on the
  other 51. The in-sample number is shown next to it where it matters, because the gap is
  large.

Sources behind each experiment, read before running it:

- Reasoning effort: the provider docs already cited in sections 12 and 14.
- Image splitting: Zhang et al., "MLLMs Know Where to Look", ICLR 2025 (small details are
  missed, an enlarged crop of the region restores them, the global view is kept alongside);
  LVLM-Count, Qharabagh et al., TMLR 2026 (summing counts over sub-images double-counts
  objects the cuts go through; they cut around objects); ABACUS, arXiv 2606.23835 (boundary
  ownership); YOLO, Redmon et al., CVPR 2016 (the grid cell holding an object's center owns
  it), which is the rule our part prompt states.
- Self-consistency: Wang et al., arXiv 2203.11171 (plurality vote over samples); the median
  is the common choice for numbers; we take the lower median so the answer is a count some
  sample gave.
- Fusion: ensemble selection by validation, Caruana et al., ICML 2004, one choice per class.
- Crop verification: two-stage detection (R-CNN; ViLD, Gu et al. 2021, classifying detector
  proposals with CLIP); ViLD and MarvelOVD (Wang et al. 2024) on VLMs misreading tight crops,
  hence the whole photo with the box outlined plus the enlarged crop; POPE (Li et al., EMNLP
  2023) on yes-bias, hence a choice among labels rather than "is this a X?".

New code (all under `backEnd/evals/`, plus one module and four prompt versions, every one with
unit tests): `evals.aggregate_runs` (median / vote / max over repeats, pooled or repeat by
repeat), `basis/vision/fusion.py` and `evals.fuse_counts` (per-class rules over VLM, SAM 3 and
other count sources, leave-one-photo-out), `evals.predict_tiles` (`separate`: 2 x 2 parts,
enlarged 2x, one `photo_countables_tile/v1` call each, summed; `joint`: the photo and its
four enlarged parts in one `photo_countables_joint/v1` call), `evals.verify_boxes` (one
`photo_verify_box/v1` call per SAM 3 detection), and the narrow `photo_lighting_electrical/v1`.
`photo_countables/v2` and the pipeline are unchanged.

### 15.1 Every variant against the current best

Count MAE, exact rate, bias, precision and recall over (photo, class, repeat), 936 pairs;
label F1 is the harness's (repeat 1). "Diff" is the paired-bootstrap difference in count
error against the baseline. Cost is one prediction of the 52 photos (both calls of a
decomposed run); seconds are per photo for the counting call(s), in parallel where the
pipeline would run them in parallel.

| Variant | Count MAE | Exact | Bias | Count P / R | Diff [95% CI] | Label F1 | Room | $ / 52 | s / photo |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline**: sol, decomposed, low | 0.180 | 0.865 | +0.03 | 0.84 / 0.87 | 0 | 0.741 | 0.90 | $0.79 | 11.1 |
| 1a. sol, countables at medium | 0.176 | 0.865 | +0.02 | 0.85 / 0.87 | -0.003 [-0.026, +0.020] | 0.741 | 0.90 | $0.90 | 21.2 |
| 1b. Opus 5.5 decomposed, low (sec. 14) | 0.166 | 0.876 | -0.05 | 0.90 / 0.83 | -0.014 [-0.049, +0.020] | 0.763 | 0.88 | $2.23 | 4.7 |
| 1c. Opus 5.5, countables at medium | 0.150 | 0.889 | -0.06 | 0.92 / 0.83 | -0.030 [-0.072, +0.011] | 0.761 | 0.88 | $2.40 | 7.4 |
| 1d. Opus 5.5, countables at high | 0.144 | 0.893 | -0.03 | 0.90 / 0.86 | -0.035 [-0.068, -0.003] | 0.764 | 0.88 | $2.54 | 9.0 |
| 2a. 2 x 2 parts, separate calls, summed | 0.265 | 0.807 | -0.03 | 0.80 / 0.76 | +0.085 [+0.026, +0.150] | (n/a) | (n/a) | $1.82 | 13.2 |
| 2b. photo + 4 parts in one call | 0.191 | 0.866 | +0.08 | 0.81 / 0.91 | +0.012 [-0.020, +0.052] | (n/a) | (n/a) | $1.04 | 11.1 |
| 3a. sol, median of its 3 repeats | 0.170 | 0.872 | +0.02 | 0.85 / 0.88 | -0.010 [-0.026, +0.004] | 0.734 | 0.90 | $1.73 | 11.1 |
| 3b. sol + Opus low, repeat by repeat (median of 2 = min) | 0.166 | 0.878 | -0.09 | 0.93 / 0.79 | -0.014 [-0.048, +0.017] | 0.766 | 0.90 | $3.02 | 11.1 |
| 3c. sol + Opus low, median of all 6 samples | 0.147 | 0.888 | -0.05 | 0.91 / 0.84 | -0.032 [-0.061, -0.006] | 0.772 | 0.90 | $5.42 | 11.1 |
| 4a. sol + SAM 3 per class, fitted in-sample | 0.129 | 0.903 | -0.07 | 0.94 / 0.84 | -0.050 [-0.092, -0.017] | 0.741 | 0.90 | $0.99 | 11.1 |
| 4b. the same, leave-one-photo-out | 0.167 | 0.880 | -0.03 | 0.89 / 0.84 | -0.013 [-0.048, +0.025] | 0.741 | 0.90 | $0.99 | 11.1 |
| **4c. sol low + Opus medium per class, leave-one-photo-out** | **0.124** | **0.899** | **-0.04** | **0.92 / 0.87** | **-0.056 [-0.093, -0.022]** | 0.741 | 0.90 | $2.20 | 11.1 |
| 4d. the same with Opus high | 0.125 | 0.900 | -0.01 | 0.90 / 0.89 | -0.054 [-0.091, -0.022] | 0.741 | 0.90 | $2.34 | 11.1 |
| 4e. 4c plus SAM 3 as a further source | 0.155 | 0.879 | -0.02 | 0.89 / 0.86 | -0.025 [-0.058, +0.005] | 0.741 | 0.90 | $2.40 | 11.1 |
| 5a. SAM 3 detections confirmed by sol, alone | 0.373 | 0.798 | -0.30 | 0.89 / 0.45 | +0.193 (verified classes only) | | | $1.77 | not measured |
| 5b. sol + confirmed detections per class | 0.184 | 0.863 | -0.01 | 0.86 / 0.84 | +0.004 [-0.027, +0.035] | 0.741 | 0.90 | $1.77 | |
| 5c. 4c plus confirmed detections | 0.126 | 0.895 | -0.03 | 0.92 / 0.87 | -0.053 [-0.094, -0.015] | 0.741 | 0.90 | $3.18 | |
| 6. 4c plus the lighting prompt on both models | 0.134 | 0.892 | -0.03 | 0.91 / 0.87 | -0.046 [-0.082, -0.013] | 0.741 | 0.90 | $3.61 | 11.1 |
| 3+4. 4c on each model's median of 3 repeats | 0.103 | 0.917 | -0.04 | 0.94 / 0.89 | -0.077 [-0.116, -0.042] | 0.734 | 0.90 | $5.93 | 11.1 |
| SAM 3 alone, v2 phrases, score 0.5 (sec. 10) | 0.587 | 0.734 | +0.45 | 0.51 / 0.89 | +0.407 | | | $0.20 | |

Notes on the table. Self-consistency rows (3a, 3c, 3+4) are one aggregated answer per photo
built from three or six calls, so they are scored as one prediction, not three independent
repeats; their cost counts every countables call they use plus one surfaces call. The tile rows (2a, 2b) replace only the countables
call and carry no surfaces call, so label F1 and room are not comparable; their seconds come
from an 8-photo timing probe at concurrency 1 (the full runs' timer included queue time; fixed
in the runner). Verification latency was not measured: the probe hit the OpenAI account's
credit limit (below). 5a counts only the four classes it verified, so its overall row is not
comparable; per class it is in the reading.

### 15.2 The winner's per-class counts

4c: per class, the counting call that is more accurate on that class, chosen leave-one-photo-
out. The choice was the same in all 52 folds for windows, doors and exterior lights, and in 51
of 52 for light fixtures, cabinets and outlets:

| Class | Source chosen | Baseline MAE / exact / P / R | Winner MAE / exact / P / R |
|---|---|---|---|
| window | Opus medium | 0.40 / 0.73 / 0.82 / 0.88 | 0.24 / 0.83 / 0.91 / 0.91 |
| door | Opus medium | 0.22 / 0.81 / 0.78 / 0.89 | 0.10 / 0.90 / 0.96 / 0.86 |
| light fixture | Opus medium | 0.14 / 0.89 / 0.88 / 0.81 | 0.12 / 0.90 / 0.94 / 0.81 |
| exterior light | sol low | 0.08 / 0.92 / 0.91 / 0.90 | 0.08 / 0.92 / 0.91 / 0.90 |
| electrical outlet | sol low (51 of 52 folds) | 0.08 / 0.92 / 0.93 / 0.86 | 0.12 / 0.90 / 0.92 / 0.78 |
| kitchen cabinet | Opus medium | 0.15 / 0.92 / 0.81 / 0.89 | 0.09 / 0.94 / 0.93 / 0.88 |
| all six | | 0.180 / 0.865 / 0.84 / 0.87 | 0.124 / 0.899 / 0.92 / 0.87 |

The outlet row is worse only because one held-out photo (the kitchen with four outlets) got
the rule fitted without it (`min(sol, Opus)`); with the rule fitted on all 52 photos, the
one a deployment would use, outlets equal the baseline and the overall MAE is 0.117. The 0.124
is the number to quote.

### 15.3 Reading: why each thing helped or not

- Effort (1a to 1d). Medium effort does nothing for sol (0.176 against 0.180) and doubles its
  time (21 s per photo). For Opus it moves windows (0.30 to 0.24 MAE) and doors (0.14 to 0.10),
  the classes that need counting one by one, for 14% more per photo than low; high adds nothing
  over medium once the models are combined (4d against 4c). Opus's weak class at every effort
  is exterior lights (count recall 0.51 to 0.57): it misses small porch and wall lanterns sol
  finds (sol 0.90). This is why neither model alone wins and the per-class choice does.
- Image splitting (2a, 2b) does not help these photos. Separate parts are significantly worse
  (door recall 0.89 to 0.57, windows 0.88 to 0.76): a door or window cut by a part's edge loses
  the context that makes it recognizable, and there are no new pixels to gain. The ownership
  rule in the prompt avoids double counting (bias stays near zero) but not the misses. The
  joint call keeps the context and does raise recall (0.91, the highest of any single call),
  but over-counts in the same proportion (precision 0.81, bias +0.08): the model counts some
  objects in both the photo and a part despite the instruction. This matches section 10.3,
  where tiling also hurt SAM 3. Splitting is a remedy for high-resolution photos with tiny
  objects; ours are 800 x 600 with one to thirteen objects a class.
- Self-consistency (3a to 3c) removes only the noise between repeats, which is the small part
  of the error here: the median of sol's three repeats gains 0.010 (not significant). Pooling
  two models gains more (3c, 0.147) because it mixes their different errors, but at seven
  times the cost of the baseline, and the lower median of six samples biases low (exterior
  lights 0.90 to 0.65 recall). On top of the per-class choice it gives the lowest error
  measured (0.103) at 2.7 times the winner's price.
- SAM 3 fusion (4a, 4b, 4e) is the clearest case of selection optimism in this report. Fitted
  on all photos, "the smaller of sol and SAM 3 at a tuned score threshold" looks like a 28%
  improvement (0.129); predicted out of sample it is 0.167 and not significant, because the
  gain comes from a few photos whose threshold choice flips when they are held out. SAM 3 is
  chosen only as a ceiling (`min`), never as a floor (`max`): with precision 0.51 it over-counts
  every class, so "SAM 3 as a floor for outlets and windows" adds false positives. Added to the
  two-model choice it makes things worse (4e).
- Crop verification (5a to 5c) does what the literature says it should to SAM 3's output: it
  removes most false positives (door MAE 1.31 to 0.18, exterior lights 0.42 to 0.05, light
  fixtures 0.42 to 0.12; cabinets 0.17 to 0.18), and confirmed exterior lights have the highest
  count recall of any source (0.96). In repeat 1 the model rejected 127 of 277 detections as `none` (74) or
  `part_or_several` (53) and moved 19 between interior and exterior lighting. But as counts it does
  not beat the VLMs it would replace (doors 0.18 against Opus 0.10; cabinets 0.18 against 0.09),
  and as a fusion source it adds nothing out of sample (5c 0.126 against 0.124). Its use is
  localization: it gives the engineer a box per counted object, which the VLM counts do not.
- The lighting-and-outlets prompt (6) raises light-fixture count recall (0.81 to 0.88 on sol,
  0.83 to 0.89 on Opus) but over-counts outlets (MAE 0.14 against 0.08), and as an extra
  source it makes the out-of-sample result worse (0.134). Not adopted.

What is left in the winner's errors is mostly the same answer in every repeat and from both
models, which no aggregation fixes: the windows of the right-side exterior (13 boxed, 10
counted), the kitchen's lights and outlets (8 lights boxed, 6 counted; 4 outlets, 2), and
windows the models count that the boxes do not. Some of those are a labeling question, not a
model error, for Bryan to settle in `LABEL_GUIDE.md`: in `09_AC Condenser` the boxes hold only
the basement egress window while two windows cut off by the top of the frame are visible and
every model counts them (3 against 1, every repeat); `25_1st Floor Office` (4 against 2) and
`15_1st Floor Breakfast Nook` (bay or mulled units: 3 against 1) look like the same question
of what one window is.

### 15.4 Recommended configuration

Counts: run the countables prompt (`photo_countables/v2`) twice per photo, in parallel, on
`gpt-6.1-sol` at low effort and on `claude-opus-5-5` at medium effort, and take windows,
doors, light fixtures and kitchen cabinets from Opus and exterior lights and outlets from sol
(the rule fitted on all 52 photos, identical to the one chosen in 51 or more of 52 folds).
Label set, materials and room type stay as today (sol's countables merged with sol's
`photo_surfaces/v1`). Measured: count MAE 0.124 against 0.180 (difference -0.056, 95% CI
[-0.093, -0.022]), exact counts 0.899 against 0.865, count precision 0.92 against 0.84 at the
same recall (0.87); $2.20 per 52 photos against $0.79 (about $2.75 per property against $1),
eleven seconds per photo with the calls in parallel. If the extra $1.40 per 52 photos is
better spent on accuracy, three repeats of both calls with a per-class median reach 0.103 for
$5.93.

Caveats that bound this decision: one property, 52 photos, so the per-class choice is fitted
on 312 (photo, class) pairs; the rule should be refitted (by `evals.fuse_counts`) on the second
client package before it is promoted, and the window convention above should be settled first,
since windows carry most of the remaining error. Wiring a third call and the per-class rule
into `basis/vision/analyze.py` is for the session that owns the pipeline; `basis.vision.fusion`
holds the rule.

Spend for this section: $24.07 (OpenAI $11.87: sol medium $1.71, tiles $6.60, verification
$2.35, lighting prompt $0.87, timing probe $0.33; Anthropic $12.20: Opus medium $4.21, high
$4.64, lighting prompt $3.35). The timing probe's last call returned
`credit_balance_exhausted`: the shared OpenAI account is out of credit and needs a top-up before
any further gpt run (this session's or another's). Prediction directories under
`$BASIS_EVAL_DATA_DIR/predictions`: `vision_countables_v2_gpt61sol_med`,
`vision_decomposed_v2_gpt61sol_med`, `vision_countables_v2_opus55_{med,high}`,
`vision_decomposed_v2_opus55_{med,high}`, `vision_tiles2x2_{separate,joint}_gpt61sol`,
`verify_sam3v2_gpt61sol`, `vision_lighting_v1_{gpt61sol,opus55_med}`,
`counts_sc3_median_gpt61sol`, and the winners `counts_fused_sol_low_opus55_med_loo` (4c, with
its `eval.md` and the fold-by-fold rules in `run.json`) and
`counts_fused_sc3_sol_low_opus55_med_loo` (3+4).
## 16. Per-room accuracy: duplicates, conventions, materials (2026-10-16)

Question: how close can the per-room step (grouping, then one inventory per room) get to the
hand-labelled room inventory and to the engineer's report rows, starting from the best
per-photo outputs of sections 12 and 14? Branch `claude/room-accuracy`. (Section 15 is
left to a parallel session.)

Setup. Per-photo input: repeat 1 of the decomposed runs `vision_decomposed_v2b_gpt61sol`
(`gpt-6.1-sol`, section 12.1) and `vision_decomposed_v2_opus55` (Opus 5.5, section 14), shared
by every room run. Grouping by file name and the 16 aliases, as in section 7 (33 rooms, 8 with
more than one photo, 27 photos in those 8). Every room run is `evals.predict_rooms ... --repeats
3`; `run.json` `models_reported` was checked after every run and every row below names one model
per step. Scored with the harness (`room_inventory`, `report_rows`) against the room labels
after the cabinet change of 16.1; the table is `docs/review_2026-10-15/repro/room_run_table.py`
over the run directories (`rooms16_*` under `$BASIS_EVAL_DATA_DIR/predictions`). Count MAE,
exact-count rate, bias, rooms exact and label F1 are the mean over the three repeats (the
harness itself prints repeat 1); "identical" is rooms whose inventory is the same on all
repeats; report rows are repeat 1 with pass^3 (row evidenced on every repeat); cost is the run's
`spent_usd` for all three repeats (one property, 52 photos, three times). Clustered standard
errors of count MAE are 0.07 to 0.12 on this set (33 rooms), so single differences under about
0.1 are not findings; paired comparisons are bootstrapped over rooms below. Identical
configurations rerun differ by 0.01 to 0.04 MAE (the v1 baseline's counting step ran five times,
as the baseline and inside the four materials rows: 0.68 to 0.72).

### 16.1 Cabinet convention: units, and what it changes

The verified boxes and `photo_countables/v2` count kitchen cabinets in units (one door or one
drawer stack); the room labels counted a run of cabinets as one object. The engineer prices
cabinetry by unit, so the room labels now count units too (LABEL_GUIDE section 3, dated note;
`work/convert_labels.py` rebuilt the harness file, which changed in exactly three counts):

| Room | Before | After | Basis |
|---|---|---|---|
| 1_kitchen | 1 (whole run) | 18 | 8 units in photo 21 and 10 in photo 22; the two photos face different walls (white refrigerator in 21, stainless in 22), so no unit is in both; the peninsula base faces away from both and is not counted |
| 1_laundry | 2 (two cabinets) | 3 | one door left of the window, two stacked doors right |
| b_bath | 1 (over-toilet cabinet) | 2 | two doors |

Bathroom vanities stay one per vanity. Rescored, before -> after: the v1 baseline 0.73 -> 0.72
MAE (bias +0.34 -> +0.27), the heuristic merge 0.54 -> 0.52 (+0.11 -> +0.03), the Opus-input
heuristic 0.44 -> 0.42; exact-count rates and rooms exact move by at most 0.01. The change matters
most to the configurations that count units across walls: the recommended room step below goes
from 0.50 to 0.43 on the `gpt-6.1-sol` input (its kitchen count of about 21 was 20 off the old
gold of 1) and from 0.41 to 0.34 on the Opus input. The old labels made a correct unit count look
like the worst error in the study.

### 16.2 Every variant against the baseline

`gpt-6.1-sol` per-photo input unless the row says Opus input. "multi only" = no call for the 25
one-photo rooms (`--single-photo-rooms per-photo`: their per-photo analysis is the inventory).
The gold ceiling on report rows is 32 of 39 (the human inventory misses FF&E because the labels
file loose furniture as `other`, LABEL_GUIDE section 4).

| Variant | Count MAE | Exact-count rate | Signed bias | Rooms exact (of 33) | Label F1 | Identical across 3 repeats | Report rows (of 39), pass^3 | Material present (of 9) | Cost, 3 repeats | Rows missed |
|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline**: reconciliation `room_inventory/v1`, Azure `gpt-4.1-mini` | 0.72 | 0.55 | +0.27 | 0.3 | 0.80 | 19 | 32, 0.82 | 8 | $0.18 | Wall Coverings |
| Heuristic merge only (largest per-photo count, no call) | 0.52 | 0.60 | +0.03 | 2.0 | 0.84 | 33 | 31 | 8 | $0 | Wall Coverings, FF&E |
| v1, multi only (the baseline run, one-photo rooms replaced by the heuristic) | 0.62 | 0.59 | +0.15 | 2.0 | 0.83 | 26 | 32, 0.82 | 8 | $0.07 | Wall Coverings |
| (a) `room_inventory/v2` (per-label accounting), `gpt-4.1-mini` | 0.66 | 0.57 | +0.35 | 2.0 | 0.84 | 30 | 31, 0.79 | 8 | $0.20 | Wall Coverings, FF&E |
| (b) overlap merge, `photo_overlap/v1` judgments by `gpt-4.1-mini` (OpenAI) | 0.51 | 0.61 | +0.05 | 2.0 | 0.84 | 32 | 31, 0.79 | 8 | $0.27 | Wall Coverings, FF&E |
| (b) overlap merge, judgments by `gpt-6.1-sol` low | 0.52 | 0.60 | +0.28 | 2.0 | 0.84 | 31 | 31, 0.79 | 8 | $1.32 | Wall Coverings, FF&E |
| Materials `disagree` trigger, `surface_material/v1`, `gpt-4.1-mini` (on the baseline) | 0.68 | 0.55 | +0.22 | 0.7 | 0.80 | 21 | 32, 0.82 | 8 | $0.19 | Wall Coverings |
| Materials `sensitive`, v1, `gpt-4.1-mini` | 0.68 | 0.55 | +0.23 | 0.0 | 0.80 | 25 | 33, 0.85 (spurious, see 16.4) | 9 | $0.22 | none |
| Materials `sensitive`, v2, Sonnet 5.5 | 0.69 | 0.55 | +0.22 | 0.0 | 0.80 | 22 | 33, 0.85 | 9 | $0.56 | none |
| Materials `sensitive`, v3, Sonnet 5.5 | 0.72 | 0.54 | +0.25 | 0.0 | 0.80 | 19 | 33, 0.85 | 9 | $0.57 | none |
| Reconciler v2 on Opus 5.5 low, all rooms | 0.62 | 0.58 | +0.40 | 0.3 | 0.78 | 7 | 33, 0.85 | 9 | $3.30 | none |
| Reconciler v2 on `gpt-6.1-sol` low, all rooms (2 repeats; see below) | 0.76 | 0.49 | +0.51 | 0.0 | 0.74 | 16 | 32, 0.82 | 8 | $0.88 | Wall Coverings |
| Reconciler `room_inventory/v3` on Opus 5.5 low, multi only | 0.44 | 0.63 | +0.15 | 2.3 | 0.84 | 27 | 31, 0.79 | 8 | $1.16 | Wall Coverings, FF&E |
| Reconciler v3 on Sonnet 5.5 low, multi only | 0.42 | 0.64 | +0.11 | 2.7 | 0.85 | 26 | 31, 0.79 | 7 | $0.57 | Wall Coverings, FF&E |
| v3 Sonnet multi only + overlap hints (Sonnet judgments) | 0.46 | 0.64 | +0.06 | 2.0 | 0.84 | 27 | 31, 0.79 | 8 | $2.35 | Wall Coverings, FF&E |
| **Recommended**: v3 Sonnet multi only + materials `sensitive` v3 Sonnet | **0.43** | **0.63** | +0.10 | 2.7 | **0.85** | 28 | **32, 0.82** | **9** | $0.99 | FF&E |
| Opus input: baseline (v1, `gpt-4.1-mini`) | 0.51 | 0.69 | +0.09 | 6.0 | 0.88 | 25 | 31, 0.79 | 8 | $0.17 | Retaining Walls, Painting |
| Opus input: heuristic merge only | 0.42 | 0.71 | -0.01 | 7.0 | 0.89 | 33 | 30 | 8 | $0 | FF&E, Retaining Walls, Painting |
| **Opus input: recommended** | **0.34** | **0.72** | +0.06 | 6.7 | **0.90** | 29 | 31, 0.79 | **9** | $0.94 | FF&E, Retaining Walls |

On the 8 multi-photo rooms, where de-duplication can act (identical is of 8; the 25 one-photo
rooms are deterministic in every "multi only" row, so the all-room identical counts above are at
least 25 there):

| Variant (multi-photo rooms only) | Count MAE | Exact-count rate | Signed bias | Label F1 | Identical (of 8) |
|---|---|---|---|---|---|
| Baseline v1 `gpt-4.1-mini` | 1.06 | 0.49 | +0.17 | 0.79 | 1 |
| Heuristic merge | 0.79 | 0.53 | -0.18 | 0.83 | 8 |
| (a) v2 `gpt-4.1-mini` | 1.08 | 0.43 | +0.61 | 0.83 | 6 |
| (b) overlap merge, `gpt-4.1-mini` judgments | 0.77 | 0.54 | -0.13 | 0.83 | 7 |
| (b) overlap merge, `gpt-6.1-sol` judgments | 0.80 | 0.50 | +0.54 | 0.83 | 6 |
| v2 Opus 5.5 | 0.84 | 0.55 | +0.45 | 0.81 | 0 |
| v2 `gpt-6.1-sol` (2 repeats) | 1.07 | 0.44 | +0.64 | 0.76 | 5 |
| v3 Opus 5.5 | 0.57 | 0.59 | +0.17 | 0.85 | 2 |
| v3 Sonnet 5.5 | 0.51 | 0.62 | +0.03 | 0.89 | 1 |
| v3 Sonnet 5.5 + overlap hints | 0.64 | 0.62 | -0.09 | 0.84 | 2 |
| Recommended (v3 Sonnet + materials) | 0.55 | 0.62 | +0.01 | 0.87 | 3 |
| Opus input: heuristic merge | 0.76 | 0.60 | -0.27 | 0.87 | 8 |
| Opus input: recommended | 0.55 | 0.62 | -0.07 | 0.89 | 4 |

Paired bootstrap over rooms (5000 resamples, MAE averaged over repeats), recommended minus
comparison: against the baseline -0.29 [95% -0.39, -0.16] on the `gpt-6.1-sol` input and -0.17
[-0.30, -0.05] on the Opus input; against the heuristic merge -0.08 [-0.18, +0.00] and -0.07
[-0.18, +0.02]. The gain over the baseline is real; the gain over the free heuristic comes only
from the 8 multi-photo rooms (0.79 -> 0.55 and 0.76 -> 0.55 there) and is not resolved at
33 rooms.

### 16.3 What was tried, and how

- **(a) Reconciliation prompt v2** (`room_inventory/v2`): the request regroups the per-photo
  counts by label (`kitchen_cabinet: Image 1 x8 (wood, permanent); Image 2 x10 (...)`,
  `reconcile.render_by_label`) and the prompt makes the model account for every count: group
  the images that show the same objects (count = the largest single-image count, more only when
  an image shows objects the others do not), add the groups that show different objects, one
  entry per group; plus the counting rules of the label guide (window = framed opening, door =
  leaf, cabinet = unit, surfaces and property-wide items once). `v3` adds one rule, copied from
  `photo_countables/v2`: loose furniture, rugs, lamps, art and decor are one `other` entry,
  never `furniture`/`chair`/`desk`.
- **(b) Overlap step** (`basis.vision.overlap`, prompt `photo_overlap/v1`): one call per pair of
  a room's photos (66 pairs for the 12-photo exterior, 75 per repeat) answering, per label, how
  many of the listed objects are the same physical objects in both images. As a replacement for
  the call (`--overlap merge`): per label, the sum of the per-photo counts minus the objects each
  pair shares (inclusion-exclusion over pairs, capped at the smaller count), floored at the
  largest single-photo count of each connected group; a failed pair counts as sharing everything
  (the max rule). As a hint (`--overlap hint`): the judgments appended to the reconciliation
  request, renumbered per call. Local feature matching was measured first, outside the code:
  SIFT (4000 features, 1024 px), Lowe ratio 0.75 and a MAGSAC fundamental-matrix fit on every pair
  of the 8 multi-photo rooms. It does not separate overlap here: kitchen 21-22, which show
  different walls, get 37 inliers, more than the truly overlapping bedroom and bath pairs (10 and
  14), because identical raised-panel cabinet doors and clapboard siding produce consistent false
  correspondences, and the exterior pairs sit at 7 to 15 inliers whatever they share (one
  close-up pair 272). Photo-level overlap is also the wrong granularity: the kitchen's photos
  share a refrigerator wall edge and a beam but no cabinet unit. This is the "doppelganger" failure of
  structure-from-motion: local matchers link distinct but similar surfaces, and geometric
  verification does not reject the false pairs (Cai et al., *Doppelgangers*, ICCV 2023,
  arXiv 2309.02420; Xiangli et al., *Doppelgangers++*, CVPR 2025, which add a learned same/different
  pair classifier on top). LightGlue/SuperPoint was not tried (model weights and torch on the
  worker, and the same ambiguity), which is why the pairwise judgment is object-level and made by a
  VLM, the analogue of those pair classifiers.
- **Materials second look** (`basis.vision.surface_material`, prompt `surface_material`): one
  call per (room, surface) on the photo where the per-photo model was most confident of the
  surface, for the labels whose report row depends on the material (`wainscoting`, `wall_finish`,
  `tile`), with the materials the rules distinguish and the cues that separate the common
  confusions (wallpaper vs wood vs painted wood; painted drywall vs plaster; tile vs vinyl). The
  component schema's `Material` has no `wallpaper`; the answer is stored as `other` with the note
  `wallpaper`, which the Wall Coverings rule accepts. Two triggers: `disagree` (the photos or the
  room inventory disagree, or the material is unknown/other) and `sensitive` (every such surface).
  An answer replaces the material at confidence >= 0.5.
- **Reconciler model**: Azure `gpt-4.1-mini` (baseline), `gpt-6.1-sol` low effort, Opus 5.5 low,
  Sonnet 5.5 low.

### 16.4 Reading

1. **On these inputs the v1 reconciliation call makes the inventory worse than not calling.**
   With the v2 per-photo inputs (definitions in the countables prompt, loose furniture filed as
   `other`), the plain heuristic merge beats the baseline call on both inputs (0.52 against
   0.72; 0.42 against 0.51), on the 25 one-photo rooms (0.37 against 0.53 and 0.24 against 0.36:
   there is nothing to merge and the call can only re-describe the photo) and on the 8
   multi-photo rooms (0.79 against 1.06). The v1 call
   re-adds loose furniture as `furniture` (`2_bed_2`: 10), lists finishes the labels do not, and
   changes from run to run (19 of 33 identical). Section 7's reading ("the call removes
   duplicates and nothing more") held for the v1 per-photo output; it does not hold for the
   better per-photo output.
2. **De-duplication needs a strong model and is worth it only where there are several photos.**
   On the 8 multi-photo rooms the max rule under-counts (bias -0.18 and -0.27: the kitchen's two
   walls of cabinets become 12, the exterior's 30 windows 10) and adding counts over-counts. The
   accounting prompt on `gpt-4.1-mini` swings to over-counting (bias +0.61: one entry per image
   group even for the roof and siding); on Opus 5.5 and Sonnet 5.5 it gets the structure right
   (kitchen cabinets 19 to 23 against 18, exterior windows 20 to 24 against 30, roof 1) and v3's
   furniture rule removes the remaining convention error (v2 Opus: 0.62 MAE overall, 0.42 with
   the furniture labels left out; v3 Opus 0.44 as run). Sonnet 5.5 is as good as Opus 5.5 at half
   the price (multi-room MAE 0.51 against 0.57; $0.57 against $1.16 for three repeats). On the
   one-photo rooms a strong reconciler adds nothing (MAE 0.37 for the heuristic, v2 mini and v2
   Opus alike, furniture aside) and costs label F1, hence "multi only".
3. **The deterministic overlap step does not beat the max rule, with either judge.**
   `gpt-4.1-mini` over-claims sharing (it says photos 21 and 22 share 10 cabinets; nearly every
   exterior pair "shares" windows), so its merge stays near the max (0.77 against 0.79 on the
   multi-photo rooms). `gpt-6.1-sol` judges the kitchen right (no shared cabinet), but then the
   merge sums per-photo counts that are themselves high (11 + 12 = 23 against 18 units) and sums
   property-wide items photographed from several sides (exterior doors 5 against 1, lights 16
   against 8): bias +0.54, MAE 0.80. Given to the Sonnet reconciler as hints, the judgments make
   it worse (multi-room MAE 0.64 against 0.51) at four times the cost. Since neither (a) on the
   small model nor (b) helped, they were not combined; (a) on a strong model is the route kept.
   Multi-view counting methods remove duplicates through explicit cross-view correspondence
   (calibration-free multi-view crowd counting, Zhang and Chan, ECCV 2022, learns a matching map
   per view pair) or through 3-D geometry (*Counting Stacked Objects*, ICCV 2025, arXiv
   2411.19149); a few uncalibrated wide-baseline photos of a room give neither reliably, and the
   error that is left is per-photo counting (cabinet units 11 and 12 against 8 and 10 in the boxes), which no
   merge rule fixes.
4. **The wainscot is a material problem with a narrow fix, and the trigger matters.** The
   `disagree` trigger never reaches it: every per-photo and per-room call agreed on `wood`, so
   there is no disagreement to detect. The `sensitive` trigger looks at it every time. The model
   matters: `gpt-4.1-mini` with the surfaces-only prompt says `painted_wood` on 7 of 8 calls, while
   Sonnet 5.5 and Opus 5.5 say `wallpaper` on every call (probe of four known surfaces, two calls
   each: Sonnet 8 of 8). Two prompt faults were found by checking every answer against the
   hand-labelled materials, and fixed by new releases rather than edits: v1 had no fiberglass
   option, so moulded shower surrounds became `plastic` and evidenced Wall Coverings spuriously
   (the v1 row's 33 rows are not a real gain); v2 had no painted-masonry option, so painted
   basement block drifted to wallpaper or other. v3, on the 13 surfaces whose material the labels
   record: 13 right, against 6 for the inventory without the second look (11 if "brick" is
   accepted for painted block). With it Wall Coverings is evidenced in all three repeats by the
   hall's wainscot itself, the material-present rate is 9 of 9, and on the Opus input it also
   recovers Painting (painted drywall). Remaining doubtful answers: the hall's upper wall is also
   called wallpaper (it is painted, with a stencil border), the exterior `wall_finish` vinyl, and
   the basement bath's wall panel (tile or faux brick, open in the label guide) is unstable.
5. **Report rows reach the gold ceiling; rooms exact stays per-photo-bound.** The recommended
   step evidences 32 of 39 rows on every repeat on the `gpt-6.1-sol` input: the gold ceiling,
   missing only FF&E, which the labels' furniture convention excludes (the v1 reconciler
   "evidences" FF&E only by breaking that convention). On the Opus input it is 31: Retaining Walls
   is missed because the Opus per-photo output never lists the retaining wall, which no room step
   can add. Exact room inventories move from 0.3 to 2.7 of 33 (`gpt-6.1-sol`) and are 6.7 on the
   Opus input against 7.0 for its heuristic: the remaining misses are per-photo recognition (a
   light fixture, an outlet, a baseboard listed or not), as in section 7.
6. **Model per dollar.** For the reconciliation call on the multi-photo rooms, MAE over all rooms
   per three-repeat run: `gpt-4.1-mini` 0.62 ($0.07, v1) or 0.66 (v2, all rooms, $0.20);
   `gpt-6.1-sol` 0.76 (v2, all rooms, $0.88 for two repeats); Opus 5.5 0.44 ($1.16, v3); Sonnet
   5.5 0.42 ($0.57, v3). Sonnet 5.5 wins outright. The `gpt-6.1-sol` row is incomplete: the run
   used `room_inventory/v2` (it lists furniture, which v3 fixes), finished two repeats and aborted
   in the third when the OpenAI account ran out of credit (`credit_balance_exhausted`);
   `room_inventory.json` was rebuilt from its 66 room records. A v3 `gpt-6.1-sol` run needs
   credit on that account.
7. **Limits.** One study, dev split, 33 rooms of which 8 have several photos: the multi-photo
   conclusions rest on 8 rooms and about 90 (room, label) pairs. The v2/v3 room prompts and the
   material prompt were written after looking at this study's failures (the conventions come from
   the label guide, the material cues are generic identification cues, and the probe included
   negatives), so they are tuned to the dev set; the held-out study must confirm them before the
   numbers are quoted as product numbers. Repeats measure the per-room calls only (the per-photo
   input is one fixed repeat).

### 16.5 Recommended room step

`evals.predict_rooms ... --room-prompt v3 --model claude-sonnet-5-5 --reasoning-effort low
--single-photo-rooms per-photo --materials sensitive` (the material prompt defaults to
`surface_material/v3` and the second look to the room model):

1. one-photo rooms: the per-photo analysis is the inventory (no call);
2. multi-photo rooms: one `reconcile_room` call per room (`room_inventory/v3`) on Sonnet 5.5 at
   low effort;
3. every `wainscoting`, `wall_finish` and `tile` in the result: one `surface_material/v3` call on
   its best photo, Sonnet 5.5;
4. no overlap step (kept behind `--overlap` for the next study, where rooms with more photos may
   change the answer).

Cost on this property: $0.33 per run (about $0.19 for the 8 reconciliation calls, $0.14 for the
about 27 material looks), against $0.06 for the baseline. With the Opus 5.5 per-photo input it is the
best configuration measured: count MAE 0.34 (baseline 0.51 on that input), exact-count rate 0.72,
label F1 0.90, report rows 31 of 39 with material present on all 9 that need one. The
`reconcile_room` path is evaluation code today (no activity calls it); wiring it into the
pipeline is the follow-up, with `room_inventory/v3` pinned as `analyze.py` pins its prompts.

Code (this branch): `basis/vision/reconcile.py` (v2+ per-label layout, hints per call slice,
reasoning effort, free-text answers clipped to the schema's 200 characters instead of failing:
before that fix a quarter of the first v2 answers and half of the first overlap judgments were
refused for a long note, and those two runs were discarded and rerun); `basis/vision/overlap.py`;
`basis/vision/surface_material.py`; prompts `room_inventory/v2`, `v3`, `photo_overlap/v1`,
`surface_material/v1`, `v2`, `v3` (manifest entries; `evals.predict_rooms` still defaults to
`room_inventory/v1`, so the release changes nothing until asked for); runner flags; 42 new unit
tests (37 in `tests/unit/vision/test_room_steps.py`, 5 in `tests/unit/evals/test_predict_rooms.py`).
Label change: three cabinet counts (16.1), private data only.

Open: the furniture convention (FF&E needs a furniture label; the room labels and the v2/v3
prompts file it as `other`) and the painted-drywall convention (the Painting row needs a
`wall_finish` the labels never list) are Bryan's and the engineer's calls; add `wallpaper` and
`painted_masonry` to the component `Material` enum so the second look need not use `other` +
note; a v3 run on `gpt-6.1-sol` once the OpenAI account has credit; confirm on the held-out study.

Spend for this section: about $15.5. Recorded in the runs' `run.json`: $14.54, of which
Anthropic $10.10 (Opus 5.5 reconciler $4.47, Sonnet 5.5 reconciler and second looks $5.63),
OpenAI $2.89 (`gpt-6.1-sol` overlap judgments $1.32 and the partial reconciler run $0.88,
`gpt-4.1-mini` overlap $0.27 and its discarded first run $0.42) and Azure `gpt-4.1-mini` $1.55
(including the discarded first v2 run, $0.24). Not recorded, about $1: two first baselines
contaminated by Azure 429 fallbacks to OpenAI `gpt-4o-mini` (discarded and rerun with the OpenAI
key unset), one overlap run stopped while rate-limited, the part of the aborted `gpt-6.1-sol`
repeat 3 that ran, and the material probes ($0.15).

Commands (from `backEnd/`, `BASIS_ENV=dev`, keys as in section 14; Azure-only runs with
`OPENAI_API_KEY` unset so a 429 cannot fall back to another model):

```bash
python -m evals.predict_rooms --photos ~/basis-eval-data/inputs/photos \
    --out ~/basis-eval-data/predictions/rooms16_final_v2_opus55 \
    --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    --per-photo ~/basis-eval-data/predictions/vision_decomposed_v2_opus55 --per-photo-repeat 1 \
    --repeats 3 --concurrency 2 --room-prompt v3 --model claude-sonnet-5-5 \
    --reasoning-effort low --single-photo-rooms per-photo --materials sensitive
BASIS_ENV=test python ../docs/review_2026-10-15/repro/room_run_table.py \
    ~/basis-eval-data/predictions/rooms16_*
```
## 17. Text steps on current models (2026-10-16)

Question: the vision step was benchmarked across models (sections 12 and 14); the two text
steps never were. Appraisal field extraction (`TEXT_MODEL`, the `appraisal_field_fallback`
call) and IRS asset classification (`CLASSIFY_MODEL`, the residual tool loop) run on the code
default `gpt-4o-mini`. Which model should each step run on, judged by accuracy per dollar?

Setup. Seven models, three repeats each, on the client study's labels: the 75 fields of
`appraisal_fields` (12 marked critical) and the 46 rows of `assets` (the engineer's schedule:
21 y39, 18 y5, 6 y15, 1 y7; no section labels). Two new runners produce the predictions and
`evals.run` scores them (`critical_accuracy`, `critical_pass^k`, `bucket_pass^k`,
`citation_validity` and `rows_with_verified_citation` were added to the harness for this
section; `evals/README.md`):

- `evals.predict_fields`: every labelled field in one call with the released
  `appraisal_field_fallback/v1` prompt, the production request format and structured answer,
  the whole document (`read_pdf` text, 20 pages, 28k characters) as context, and the
  production verbatim guard (a value not in the page text is dropped and scores as a miss).
  Production asks this call only for the critical fields Azure DI and the regex pass missed.
  Asking for all 75 fields measures the model on the whole form. 40 of the 75 keys are not
  in the field catalogue and get their key in words as the caption.
- `evals.predict_assets`: every row through `classify_with_model`, the production model path
  (the residual loop with `classify/v1`, at most 3 tools and 4 model calls, citation
  verification, the review policy), with the rules table and the cache skipped. The
  reference corpus is the four IRS PDFs cut into the ingest's 400-token windows, searched by
  BM25 (production adds a vector leg). The input is what the row carries: the component name
  and `nonresidential`, with no material, attachment or room. A failed loop is scored as
  what production writes: 1250, the building bucket (y39).

Prices are `basis.llm.cost` (section 12 and 14 sources). Every run's `run.json`
`models_reported` names only the requested model (Azure was unset, so nothing fell back),
with no provider errors except those counted below. Efforts: extraction at `low` for every
reasoning model. For classification, the OpenAI reasoning models ran at `none` and Claude at
`low` (see the first finding below).

### 17.1 Field extraction

| Model | effort | Fields right, repeat 1 | mean of 3 | critical (12), mean of 3 | pass^3 | critical pass^3 | before the guard, mean of 3 | cost per study | seconds per call |
|---|---|---|---|---|---|---|---|---|---|
| gpt-4o-mini (current default) | - | 0.253 | 0.249 | 0.167 | 0.240 | 0.167 | 0.262 | $0.0025 | 12.6 |
| gpt-5.2 (Bryan's `.env`) | low | 0.867 | 0.880 | 1.000 | 0.867 | 1.000 | 0.880 | $0.0591 | 33.5 |
| gpt-5.4-mini | low | 0.813 | 0.782 | 0.889 | 0.707 | 0.833 | 0.800 | $0.0197 | 15.4 |
| **gpt-6-luna** | low | 0.867 | **0.924** | **1.000** | 0.853 | 1.000 | 0.924 | **$0.0024** | 17.0 |
| gpt-6.1-sol | low | 0.880 | 0.880 | 1.000 | 0.880 | 1.000 | 0.880 | $0.0376 | 22.9 |
| claude-sonnet-5-5 | low | 0.947 | 0.916 | 1.000 | 0.853 | 1.000 | 0.916 | $0.0610 | 14.0 |
| claude-opus-5-5 | low | 0.973 | 0.911 | 1.000 | 0.880 | 1.000 | 0.911 | $0.1221 | 18.9 |

Paired over the 75 fields (mean of 3 per field, bootstrap 95% interval), gpt-6-luna minus:
Opus +0.013 [-0.009, 0.036], Sonnet +0.009 [-0.018, 0.044], gpt-5.2 +0.044 [-0.027, 0.12],
gpt-6.1-sol +0.044 [0.004, 0.089], gpt-5.4-mini +0.142 [0.062, 0.227].

Reading:

- `gpt-4o-mini`, the default, returns null for about 50 of the 75 fields in one call. Two of
  its 12 critical fields are right on average. Every current model reads the form. The
  gpt-4o-mini number measures the model and not the text: 55 of the 75 values are printed
  verbatim in the text sent, and the other 20 are numbers printed with thousands separators.
- The top four (luna, Sonnet, Opus, gpt-5.2) are tied within noise, and all get the 12
  critical fields right on every repeat. gpt-6-luna has the highest mean at the lowest price:
  $0.0024 per study, 25 times cheaper than Sonnet and 50 times cheaper than Opus.
- Remaining misses are mostly conventions, not reading errors. Two fields are disputed
  labels that every model misses the same way. `reconciliation.indicated_value_cost_approach`
  is labelled 692831, but the reconciliation section prints 682,831; the cost approach page
  prints 692,831, so the document contradicts itself, and every model copies the
  reconciliation figure. `cost_approach.effective_date_of_cost_data` is labelled `2025-01`
  as text, but the form prints `01/2025`, so a correct copy cannot match. Excluding the two,
  luna is at 0.950, Sonnet 0.941, Opus 0.936 and sol 0.904. The private labels are left for
  Bryan to decide. The other convention is the condition rating: the URAR prints
  "Concrete C3" in the improvements grid, and the labels keep the material only. gpt-6.1-sol
  keeps the suffix on all 7 such fields, more than its whole gap to luna (about 3.4 fields);
  Opus loses 4.7 fields per call to it and luna 2.3. A prompt line ("material only, without the C1-C6 rating") would
  close it, as a new prompt version.
- The verbatim guard costs nothing on the current models (0 drops). It drops about one value
  per call from gpt-4o-mini and gpt-5.4-mini (`$680,000` where the text prints `680,000`).

### 17.2 Asset classification

| Model | effort | Bucket right, repeat 1 | mean of 3 | pass^3 | macro-F1, repeat 1 | bucket right, answered loops only | citations verified | rows with a verified citation | needs review | loop failures (of 138) | cost per study (46 rows) | seconds per row |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| always y39 (the failure fallback) | - | 0.457 | 0.457 | 0.457 | 0.157 | - | - | - | - | - | $0 | - |
| rules table (`lookup`) | - | 0.087 | - | - | 0.175 | - | - | - | - | - | $0 | - |
| gpt-4o-mini (current default) | - | 0.565 | 0.580 | 0.565 | 0.446 | 0.580 | 0.663 | 0.761 | 0.51 | 0 | $0.073 | 5.5 |
| gpt-5.2 | none | 0.522 | 0.507 | 0.457 | 0.383 | 0.648 | 0.892 | 0.761 | 0.75 | 30 | $0.874 | 8.2 |
| gpt-5.4-mini | none | 0.674 | 0.630 | 0.565 | 0.522 | 0.649 | 0.882 | 0.891 | 0.43 | 7 | $0.269 | 4.0 |
| gpt-6-luna | none | 0.609 | 0.630 | 0.587 | 0.449 | 0.659 | 0.873 | 0.891 | 0.57 | 6 | $0.052 | 6.2 |
| gpt-6.1-sol | - | not runnable (below) | | | | | | | | 5 of 5 in the probe | | |
| **claude-sonnet-5-5** | low | 0.674 | **0.688** | 0.630 | 0.600 | 0.688 | 0.964 | 0.957 | 0.46 | 0 | **$1.247** | 5.9 |
| claude-opus-5-5 | low | 0.696 | 0.703 | 0.696 | 0.541 | 0.703 | 0.959 | 1.000 | 0.36 | 0 | $1.919 | 9.1 |

Per bucket (mean of 3), y39 / y15 / y5: gpt-4o-mini 0.97 / 0.17 / 0.24, gpt-6-luna 1.00 /
0.67 / 0.22, Sonnet 1.00 / 0.94 / 0.22, Opus 1.00 / 0.83 / 0.33. Paired over the 46 rows:
Opus minus luna +0.072 [0.014, 0.152], Sonnet minus luna +0.058 [0.000, 0.130], Opus minus
Sonnet +0.014 [-0.043, 0.072], Sonnet minus gpt-4o-mini +0.109 [0.014, 0.210], luna minus
gpt-4o-mini +0.051 [-0.043, 0.152].

Reading:

- The production loop had never completed a search on a real API. The assistant turn that
  made the tool calls was replayed as `{"tool_calls": [...]}` JSON text, not as
  `Message.tool_calls`, and OpenAI answers the next call with 400 "messages with role 'tool'
  must be a response to a preceeding message with 'tool_calls'". So every residual
  classification that searched ended in `provider_error` and was written as y39 with
  `needs_review` (the 2-row smoke reproduced it on gpt-4o-mini). The scripted unit tests
  could not see it: they asserted on the JSON text. Fixed in `basis/classify/residual.py`,
  and the replayed history is now checked against both providers' wire mappings
  (`test_replayed_history_is_valid_on_the_openai_and_anthropic_wires`). Every number above
  is after the fix. The Claude 5 loop works without replaying thinking blocks: 0 failures
  in 276 rows, against the caveat in the Anthropic provider docstring.
- The newer OpenAI models cannot run this loop at their reasoning efforts on Chat
  Completions. gpt-5.4-mini, gpt-6-luna and gpt-6.1-sol answer 400 "Function tools with
  reasoning_effort are not supported ... use /v1/responses or set reasoning_effort to
  'none'" (also with no effort given, since their default is not `none`). gpt-6.1-sol does
  not accept `none`, so it cannot run the classifier at all until the OpenAI provider speaks
  the Responses API. The other three ran at `none`, so the OpenAI rows are those models
  without reasoning and the Claude rows are with it. This is the comparison production can
  run today, not a like-for-like reasoning comparison.
- 43 of the OpenAI loop failures have one cause. `Classification.asset_class` has
  `max_length=16`, OpenAI's strict schema does not enforce `maxLength`, and answers like
  "Building or Building Component" fail pydantic in the provider (`ProviderBadResponse`)
  before the loop's JSON retry can see them. gpt-5.2 lost 30 of 138 rows to this. On the
  loops that answered, the four OpenAI models score 0.58 to 0.66. Not fixed here, so the
  runs stay comparable; a follow-up is proposed.
- The y5 rows decide the ranking, and they are mostly unreachable from this input. Every
  model gets the y39 rows right. 10 of the 18 y5 rows (cabinets and millwork, fireplaces,
  raised paneling, wood flooring, wall coverings, half walls, door and window guards, the
  kitchen and utility sink, security and exterior lighting, access control) are wrong on
  every repeat of all six models, almost always as y39 (172 of 180 answers). Those are the engineer's calls on decorative, removable or business-use
  finishes, made with facts the row does not carry (the component name is the whole
  input). The upper bound of this dataset is therefore well below 1. Models separate on the
  y15 land improvements (driveway, fencing, retaining walls, pads: gpt-4o-mini 0.17,
  Sonnet 0.94) and on the few y5 rows with a clear authority (appliances, carpet, window
  treatments).
- Evidence follows the accuracy. Claude's citations pass the production check 96% of the
  time and nearly every row carries one. The OpenAI models are at 87% to 89%, gpt-4o-mini
  at 66%. An unverified citation sends the row to review, so Opus flags 36% of rows,
  Sonnet 46%, gpt-4o-mini 51% and gpt-5.2 75%.
- In production the classifier only sees what the rules table misses. The rules table
  classifies 4 of these 46 report rows (it is keyed on the vision vocabulary, not on report
  captions), so per study the classifier cost lands on most components.

### 17.3 Recommendations

Rule: the cheapest model whose mean accuracy over three repeats is not significantly below
the most accurate (paired bootstrap, 95%).

- **Field extraction: `gpt-6-luna` at `low` effort.** It has the highest mean (0.924), every
  critical field right on every repeat, and costs $0.0024 per study, the cheapest model
  measured (gpt-4o-mini costs the same and scores 0.249). Wiring it needs `TEXT_MODEL=gpt-6-luna`
  plus two things the extraction activity does not pass yet: `reasoning_effort` (the
  pipeline sends none, so the model's default applies) and a larger
  `EXTRACTION_FALLBACK_MAX_TOKENS` than 800, since a reasoning model spends output on
  thinking (section 12). Both are in `basis/config.py` and the activity, which other
  sessions own.
- **Asset classification: `claude-sonnet-5-5` at `low` effort.** It is tied with Opus
  (+0.014 [-0.04, 0.07]) at 65% of the price ($1.25 against $1.92 per 46 rows), and
  significantly better than gpt-4o-mini (+0.11) with 0 loop failures and 96% verified
  citations. gpt-6-luna is the cheap alternative ($0.05): 0.63, behind Opus significantly
  and behind Sonnet at the boundary. Opus is the choice if review load counts more than
  spend: 36% of rows flagged against 46%, pass^3 0.70 against 0.63, every row with a
  verified citation. Ten points fewer flags is about 4.6 fewer rows per study for the
  engineer, which is worth more than $0.67 at any engineering rate; Bryan's call.
  `CLASSIFY_MODEL=claude-sonnet-5-5` works with the loop as it is (`ANTHROPIC_EFFORT` low,
  the provider's thinking headroom over `LoopLimits.max_tokens`). Checked on 10 rows at the
  production limits (1024 tokens, 60 s timeout): 0 failures, 6.1 s per row.
- **Either way, the residual loop fix in this PR is what makes the classifier work at all.**
  Before it, every model-path classification that searched fell back to y39.

Follow-ups (not done here): an OpenAI Responses API path in the provider, so gpt-6.1-sol and
the reasoning efforts can run tool loops; `asset_class` length tolerance (truncate, or a
larger limit) so a long class name is not a loop failure; richer classifier input (material,
attachment, room, use) before judging y5; the condition-suffix prompt line for extraction;
and resolving the two disputed labels.

Spend for this section: $14.21 for the runs ($6.12 Opus, $3.92 Sonnet, $2.80 gpt-5.2, $0.87
gpt-5.4-mini, $0.23 gpt-4o-mini, $0.16 gpt-6-luna, $0.11 gpt-6.1-sol; classification $13.30,
extraction $0.91), plus $1.10 of smoke, calibration and production-limit checks. Total
about $15.31.
Prediction directories: `text_fields_<model>` and `text_assets_<model>` under
`$BASIS_EVAL_DATA_DIR/predictions`, each with `run.json` and the raw records. The tables come
from `docs/review_2026-10-15/repro/text_step_table.py` (run from `backEnd/`).

## 18. Production configuration (2026-10-16)

What the pipeline runs with no model setting changed: the default of each setting in
`backEnd/basis/config.py` is the configuration the named section measured best. Every row
is one setting away from the configuration before it (the last column), and the worker logs
the configuration in effect at startup (`basis/worker/main.py`). Measured on the one client
study of sections 6 to 17 (52 photos, about 10 rooms, one appraisal, 46 asset rows); each
choice should be re-measured on the held-out study before it is trusted further.

| Step | Model | Prompt | Effort | Settings (default) | Section | Measured | Previous behaviour |
|---|---|---|---|---|---|---|---|
| Per-photo countables | gpt-6.1-sol | `photo_countables/v3` (19; v2 before) | low | `VISION_MODEL`, `VISION_PHOTO_MODE=decomposed`, `VISION_REASONING_EFFORT=low`, budget 6000 | 11.2, 12.1 | (see the fusion row) | `VISION_PHOTO_MODE=single`, `VISION_MODEL=gpt-4o-mini` |
| Per-photo surfaces and room type | gpt-6.1-sol | `photo_surfaces/v2` (19; v1 before) | low | as above | 11, 12.1 | merged with the countables call (`basis/vision/merge.py`) | as above |
| Count fusion: second countables call | claude-opus-5-5 | `photo_countables/v3` (19) | medium | `VISION_COUNT_FUSION=per_class`, `VISION_FUSION_MODEL`, `VISION_FUSION_REASONING_EFFORT`; table `section15.4/2026-10-16` (`basis/vision/fusion.py`) | 15.4 | count MAE 0.124 vs 0.180 (LOO), $2.20 per 52 photos | `VISION_COUNT_FUSION=off` |
| Room inventory, multi-photo rooms | claude-sonnet-5-5 | `room_inventory/v3` | low | `ROOM_RECONCILE=true`, `ROOM_PROMPT_VERSION=v3`, `ROOM_MODEL`, `ROOM_REASONING_EFFORT` | 16.5 | room count MAE 0.72 to 0.43 (sol input), 0.34 (Opus input) | `ROOM_RECONCILE=false` (per-photo rows become assets) |
| Room inventory, one-photo rooms | none (the photo's analysis) | - | - | `ROOM_SINGLE_PHOTO=per_photo` | 16.5 | part of the row above | `ROOM_SINGLE_PHOTO=reconcile` (a call per room) |
| Material second look on wainscoting, wall finish, tile | claude-sonnet-5-5 (the room model) | `surface_material/v3` | low | `ROOM_MATERIALS=sensitive` | 16.5 | material present on 9 of 9 report rows that need one; $0.33 per run with the row above | `ROOM_MATERIALS=none` |
| Object localization | SAM 3 (Replicate) | phrase set `v3` | - | `REPLICATE_API_TOKEN`; per-class phrases and count thresholds (`basis/vision/localize.py`, `DEFAULT_PHRASE_SET`) | 10.2, 19 | instance masks for review; counts not fused (15.3); mAP@0.5 0.76 out of sample (19) | token unset: skipped; phrase set v1 before 19 |
| Extraction fallback | gpt-6-luna | `appraisal_field_fallback/v1` | low | `TEXT_MODEL`, `EXTRACTION_REASONING_EFFORT`, budget 8000 (`EXTRACTION_FALLBACK_MAX_TOKENS`) | 17.1, 17.3 | 0.924 of 75 fields, 12 of 12 critical, $0.0024 per study | `TEXT_MODEL=gpt-4o-mini` (budget 800) |
| Asset classification (residual tool loop) | claude-sonnet-5-5 | `classify/v1` | low | `CLASSIFY_MODEL`, `CLASSIFY_REASONING_EFFORT` | 17.2, 17.3 | 0.688 bucket accuracy, 96% verified citations, $1.25 per 46 rows | `CLASSIFY_MODEL=gpt-4o-mini` |

Notes on the wiring (code, not measurement):

- Fusion is a third call in the decomposed step, concurrent with the other two. If Opus stays
  unavailable after retries (or is unusable, not configured, over budget, or slower than 80 s),
  the photo keeps gpt-6.1-sol's counts and is flagged `count_fusion_fallback:<why>`; it never
  fails. Both raw answers and the source, model and counts per class are stored with the
  analysis.
- The room step is a new activity, `reconcile_rooms`, after the photo stage (workflow patch
  `reconcile-rooms-v1`); its inventory rows replace a room's per-photo rows as the input of
  `process_assets`. It reconciles the stored component rows, so reviewer corrections carried into
  a revision are reconciled. A room is rebuilt only when its photos change. If the activity
  fails, the rooms review is flagged and the per-photo rows are used.
- `wallpaper` and `painted_masonry` are component materials now; before, the second look stored
  them as `other` + note and `concrete` + note. With the `report_rows` evidence rules this also
  lets a wallpaper `wall_finish` evidence Wall Coverings and a painted masonry wall evidence
  Painting, which `other` and `concrete` did not (not re-scored here).
- The classifier settings refuse an OpenAI reasoning model at any effort but `none` (Chat
  Completions refuses function tools with a reasoning effort, 17.2) and gpt-6.1-sol at all. An
  `asset_class` longer than 16 characters is clipped instead of failing the loop (the cause of
  43 OpenAI loop failures in 17.2).
- No new measurement was run for this section: the OpenAI account was out of credit, and the
  wiring is tested offline (unit, activity and workflow lifecycle tests with fake providers).

## 19. SAM 3 per class and vocabulary completion (2026-10-16)

Two open items from sections 10 and 13, each measured on the 52 client photos. Branch
`claude/vision-leftovers-d42d59`.

### 19.1 SAM 3: phrases and count thresholds chosen per class

Question: section 10.3 found that phrase set v2 helps some classes and hurts others and that
one threshold does not suit every class. Which phrases and which count threshold per class,
chosen without selecting on the photos that score the choice?

Standard followed. A detector's scores are not calibrated alike across classes, so a
deployed detector gets one confidence cut-off per class, and an open-vocabulary one is tuned
through its prompts as well (section 10.2 showed both effects). Choosing among many phrase
subsets and thresholds on the same photos that score them overstates the result (Cawley and
Talbot 2010, "On over-fitting in model selection"); the remedy is nested evaluation: every
choice is made on 51 photos and scored on the 52nd (leave one photo out), so the reported
numbers include the cost of choosing. One threshold for every class (10.2) is the baseline.

Method.

1. **Phrase pool, one call per photo.** Phrase set `pool` (`basis/vision/localize.py`): every
   v1 and v2 phrase of the seven countable classes plus the ones the rejection reasons of
   10.2 suggest (37 phrases: `house window`, `glass window`; `room door`, `bedroom door`,
   `wooden door`, `door`; `entry door`, `entrance door`; `ceiling lamp`, `flush mount
   light`; `exterior light`, `porch lantern`, `hanging lantern`, `outdoor wall light`;
   `power outlet`, `outlet`; `cabinet`, `cabinet drawer`, `drawer`). Run once with
   `evals.predict_boxes --phrase-set pool --phrase-dedupe off`, so every phrase keeps its own
   instances (the pipeline's same-label dedupe would hide which phrase found what). 52
   photos, threshold 0.1, $0.60; four photos lost to the Replicate throttle were rerun with the
   same settings (`sam3_phrase_pool_v1_merged`).
2. **Offline scoring of any subset** (`evals/sam3_select.py`). SAM 3 answers each phrase
   independently (a phrase gives the same instances whatever else is prompted: `window`'s
   instances are identical in the saved v1 and v2 runs on all 52 photos, and the pool's
   differ from them only by the duplicates the dedupe removes), so a class's output under a phrase subset is the
   union of those phrases' instances deduplicated exactly as the localizer does (mask IoU
   0.8; a cached implementation checked equal to `suppress` on random cases). Check: the
   pool reproduces section 10.3 exactly for v1 (mAP 0.653, P/R 0.46/0.80, count MAE 1.50,
   every class AP) and for v2 (cabinet 0.74, light fixture 0.76, interior door 0.70, outdoor
   lighting 0.48).
3. **Rule, fixed before the results.** Phrases: the candidate subset (every subset of at most
   three pool phrases of the class, plus the v1 and v2 tables) with the highest AP@0.5 on the
   training photos, ties to fewer phrases. Threshold: on the training photos and those
   phrases, the value in 0.30 to 0.90 (step 0.05; 0.30 is the lowest the product's candidate
   floor allows) with the smallest total absolute count error, ties to the higher F1, then to
   the value nearest 0.5. Leave one photo out over the 52 photos; the shipped table is the
   same rule on all 52.

Results. Baseline: the v1 phrases at the product's 10.2 thresholds (window 0.6, every other
class 0.5), from the same pool. Count MAE as in 10.3 (over photo-classes where the gold or
the predicted count is non-zero); the paired column is the absolute count error over every
verified photo-class (52 x 7), which compares the two on the same pairs.

| Run | mAP@0.5 | Precision / recall at the class thresholds | Count MAE (pairs), signed bias | Abs count error per verified photo-class |
|---|---|---|---|---|
| v1 phrases, 10.2 thresholds (the product until now) | 0.653 | 0.48 / 0.77 | 1.39 (127), +0.91 | 0.486 |
| v2 phrases at 0.5 (10.3) | 0.653 | 0.47 / 0.82 | 1.41 (131), +1.08 | |
| **per class, leave one photo out (the estimate)** | **0.763** | **0.73 / 0.77** | **0.79 (102), +0.11** | **0.223** |
| per class, chosen on all 52 (in sample, the shipped table) | 0.778 | 0.77 / 0.77 | 0.65 (101), +0.00 | |
| shipped v3, fresh Replicate run through the pipeline's localizer (`sam3_photos_v3_merged`, $0.19) | 0.778 | 0.77 / 0.77 | 0.65 (101), +0.00 | |

The fresh run matches the offline in-sample row exactly on every class (and `evals.run` gives the
same mAP), which confirms that scoring phrase subsets from the pool reproduces what the
localizer returns; it is a check of the method, not a second out-of-sample estimate (same
photos). Paired bootstrap over photos (2,000 resamples), out-of-sample v3 minus v1: mAP +0.10 [+0.02,
+0.16]; count error per photo-class -0.26 [-0.39, -0.16]; precision +0.25 [+0.17, +0.32];
recall -0.00 [-0.07, +0.06]. Selection optimism (in sample minus out of sample) is small:
0.015 mAP, 0.14 count MAE.

Per class (out of sample; v1 baseline in brackets):

| Class (true objects) | v3 phrases | Count threshold | AP@0.5 | Precision / recall | Count MAE, bias | Same choice in folds |
|---|---|---|---|---|---|---|
| window (68) | `house window` | 0.65 | 0.833 (0.837) | 0.70 / 0.84 (0.65 / 0.84) | 1.07, +0.45 (1.31, +0.69) | 51 of 52 |
| interior door (25) | `room door` | 0.50 | 0.883 (0.848) | 0.95 / 0.72 (0.51 / 0.80) | 0.42, -0.32 (1.00, +0.64) | 52 |
| exterior door (6) | `front door` | 0.85 | 0.376 (0.184) | 1.00 / 0.17 (0.12 / 0.83) | 1.00, -1.00 (1.80, +1.70) | 52 |
| light fixture (24) | `recessed light`, `ceiling lamp` | 0.70 | 0.851 (0.614) | 0.74 / 0.71 (0.31 / 0.92) | 0.56, -0.06 (1.88, +1.88) | 44 |
| outdoor lighting (23) | `porch light`, `outdoor wall light` | 0.30 | 0.788 (0.665) | 0.72 / 0.78 (0.61 / 0.48) | 1.00, +0.20 (1.50, -0.50) | 51 |
| electrical outlet (21) | `wall outlet`, `power outlet` | 0.55 | 0.899 (0.881) | 0.79 / 0.90 (0.69 / 0.95) | 0.44, +0.19 (0.62, +0.50) | 50 |
| kitchen cabinet (24 units) | `kitchen cabinet`, `cabinet door` | 0.70 | 0.713 (0.545) | 0.59 / 0.71 (0.57 / 0.50) | 1.57, +0.71 (1.80, -0.60) | 50 |

Checks on the rule: allowing thresholds down to 0.15 changes nothing (outdoor lighting
still picks 0.30, so the product's candidate floor does not bind); capping subsets at one
phrase gives mAP 0.687, at two 0.764, at four 0.763 (three is not a tuned value).

Reading.

1. **The gain is precision, from phrasing.** Recall is unchanged (0.77); precision goes from
   0.48 to 0.73, and the over-count (bias +0.91 per photo-class) almost disappears. The
   phrase that names the object as the engineer means it filters the confusions 10.2 listed:
   `room door` drops appliance and cabinet doors and casings (interior-door precision 0.51 to
   0.95), `recessed light` + `ceiling lamp` drop the plug-in lamps and exterior lanterns that
   `light fixture` caught (0.31 to 0.74), `porch light` + `outdoor wall light` find the
   lanterns `outdoor light` missed (recall 0.48 to 0.78). Window phrasing barely matters
   (`house window` vs `window` AP 0.833 vs 0.837); its gain is the threshold.
2. **Exterior doors are not solved, and the rule says so.** Six true doors in five photos;
   most high-scoring detections are the neighbours' doors and garage doors of section 10.2.
   `front door` doubles AP (0.18 to 0.38), but no threshold separates the true doors, so the
   count-error rule picks 0.85, where almost nothing is counted (recall 0.17, bias -1). In the
   product that means exterior doors come from the per-photo model's count (section 15), and
   SAM 3's exterior-door masks are stored as uncounted candidates for the engineer to tap.
   Six objects cannot support a better choice.
3. **Cabinet units stay the weakest count.** AP rises 0.55 to 0.71 (`cabinet door` finds units),
   but the count error is driven by the two kitchen photos with 8 and 10 units, where one fold's
   different threshold moves the count by several units (out-of-sample MAE 1.57 against 0.86
   in sample). Counting cabinets stays with the per-photo model (section 15's fusion), as before.
4. **Limits.** One property; the per-class thresholds are tuned on 191 boxes, some classes on
   very few (exterior door 6); out-of-sample here means another photo of the same building,
   not another building. Re-measure on the next study before trusting the thresholds further.

Shipped: phrase set `v3` and its threshold table (`COUNT_THRESHOLD_SETS["v3"]`) are the
localizer's defaults (`DEFAULT_PHRASE_SET`); `basis.activities.localize_photo` prompts them,
records `phrase_set` and the thresholds with every localization, and stores candidates from
`CANDIDATE_THRESHOLD` 0.25 (below every count threshold). v1 and its 10.2 table stay
available for comparison runs (`--phrase-set v1`).

### 19.2 Vocabulary completion and the two conventions

Six labels join the component vocabulary (77 labels + `other`) for the report rows that had
none (section 13): `concrete_floor`, `half_wall` (surfaces), `bathroom_accessory`,
`window_guard`, `shed`, `concrete_pad` (objects). Each has a rules-table row with verbatim
citations (`RULES_VERSION` `2026-10-16.1`; RULES_RECITATION.md, "Rows added 2026-10-16"; the
`rule_citations` eval resolves 77 of 77), SAM 3 phrases, a definition in the released prompts
`photo_countables/v3` and `photo_surfaces/v2`, and a dated label-guide entry. Three rows are
building components (section 1250), which the table could not express before: a building
row is 27.5 years for residential rental and 39 for nonresidential (ATG Exhibit A gives
both). Bathroom accessories and sealed concrete floors are supported by Exhibit A as the
engineer treated them (39-year, nonresidential); half walls and window guards are section
1250 in Exhibit A but 5-year in the engineer's report, the shed's 7 years and the pads' 15
years rest on passages that do not name the item, so those four rows are contested (a hit
goes to the engineer).

Two conventions, decided:

- **Loose furniture (the FF&E row): listed as `furniture` with a count.** The engineer prices an
  FF&E row (5-year, 0.46% of this study), and a count of pieces per room is what it needs;
  filing loose furniture under `other` made even the gold inventory miss the row. It is still
  not a building component, so the harness keeps `furniture` (and `chair`, `desk`,
  `filing_cabinet`) out of the recognition and count scores (`photo_components`,
  `room_inventory`, `evals.run.UNSCORED_LABELS`), as it does `other`; only `report_rows`
  reads it. The countables prompt v3 lists it with a note naming the pieces; the room labels
  map `loose_furniture` to it (counts unchanged).
- **Painted drywall: not listed; Painting is not photo-derivable.** Plain painted drywall is the
  default finish of every finished room. Listing it adds one entry to every room inventory and
  carries no classification information: in this report Painting is 39-year, the same bucket
  as the walls under it, and the engineer prices it from wall area (plans, takeoff), not from
  photos. What the photo must establish is the exception (wallpaper, paneling, beadboard,
  painted block), which the 5-year wall rows score. So the Painting row moves to kind C
  (before: evidenced by a `wall_finish` with a drywall or paint material, which the models
  listed against their prompt's instruction). This lowers the whole-study cost coverage by
  Painting's 5.2% share, by convention, not by a vision change.

Label changes (private, `labels/LABEL_GUIDE.md` dated 2026-10-16; harness files rebuilt with
`work/convert_labels.py` and `work/build_report_rows.py`): bath accessories, the shed, the
garage's concrete floor and the shed's pad (was `patio`) now carry the new labels; loose
furniture is `furniture`; two half walls were added (photo 32, a drywall half wall with a cap;
photo 52, a carpeted knee wall at the stair opening, flagged for Bryan to verify). No window
guard is visible in the 52 photos.

Measured. New per-photo prompts on Claude Opus 5.5 (low effort; the OpenAI account was out of
credit, so gpt-6.1-sol, the production base model, was not re-run): one repeat of each call,
merged as in section 11, then the room step of section 16.5 (Sonnet 5.5, three repeats).

| Per-photo (one repeat, 52 photos) | countables v2 + surfaces v1 | countables v3 + surfaces v2 |
|---|---|---|
| Count MAE vs the verified boxes (312 photo-classes) | 0.154 | 0.157 |
| Label precision / recall / F1 (new labels) | 0.68 / 0.84 / 0.75 | 0.70 / 0.88 / 0.78 |
| Room type accuracy | 0.885 | 0.865 (one photo) |
| Cost | $2.23 (section 14) | $2.33 |

| Report rows (section 13's metric) | Before (39 photo-derivable rows, 6 gaps) | After (38 rows, 0 gaps) |
|---|---|---|
| Gold inventory (the ceiling) | 32 (0.82); missed FF&E | 37 (0.97); missed Door & Window Guards |
| Opus 5.5 per-photo v2 prompts, room step (`rooms16_final_v2_opus55`) | 31 (0.79); missed FF&E, Retaining Walls | 30 (0.79); misses the 6 new rows, FF&E, Retaining Walls |
| gpt-6.1-sol per-photo v2 prompts, room step (`rooms16_final_v2b_gpt61sol`) | 32 (0.82); missed FF&E | 31 (0.82); misses the 6 new rows, FF&E |
| **Opus 5.5 per-photo v3 prompts, room step (`rooms19_v3_opus55`)** | | **37 (0.97, [0.87, 1.00]); missed Door & Window Guards; same in 3 of 3 repeats** |
| Cost coverage of the photo-derivable rows | gold 0.968, Opus v2 run 0.967 | gold 0.997, Opus v3 run 0.997 |

The six gaps became five evidenced rows and one honest miss: half walls (photos 32, 52 and
two the labels do not have: a kitchen pony wall and a stair wall, to check), bathroom
accessories in five bathrooms, the shed and its pad, the garage's concrete floor. Door &
Window Guards stays missed in both the gold and the pipeline: nothing in the photos is a guard
(the engineer's row may be the storm door, which is an `exterior_door`, or something not
photographed). FF&E is now evidenced in 17 rooms with counts. Room counts on the new labels
(the same labels for both runs): absolute error on the six new labels 14 to 7 in each repeat,
on every other label 82 / 79 / 83 to 80 / 79 / 74; room count MAE 0.39 to 0.36 (paired by
room and label, bootstrap over rooms: CI of the difference covers zero in two of three
repeats). Under the old labels the v2 run's MAE was 0.346; the yardstick now has more labels,
so whole-room exact matches fell (0.15 to 0.09).

Shipped: `photo_countables/v3` and `photo_surfaces/v2` are the decomposed step's prompts
(`basis/vision/analyze.py`; the fusion call uses the same countables prompt). They need a
gpt-6.1-sol re-run when OpenAI credit returns, to confirm the base model's counts hold as
Opus's did.

Commands (from `backEnd/`; `BASIS_ENV=dev` with `REPLICATE_API_TOKEN` for the SAM 3 runs):

```bash
python -m evals.predict_boxes --dataset ~/basis-eval-data/detection --out ~/basis-eval-data/predictions/sam3_phrase_pool_v1 --phrase-set pool --phrase-dedupe off
python -m evals.sam3_select select --pool ~/basis-eval-data/predictions/sam3_phrase_pool_v1_merged --dataset ~/basis-eval-data/detection --out ~/basis-eval-data/predictions/sam3_select_v1
python -m evals.predict_boxes --dataset ~/basis-eval-data/detection --out ~/basis-eval-data/predictions/sam3_photos_v3
python -m evals.sam3_select score --predictions ~/basis-eval-data/predictions/sam3_photos_v3_merged --dataset ~/basis-eval-data/detection
BASIS_ENV=test BASIS_EVAL_DATA_DIR=~/basis-eval-data python -m evals.run --split dev --no-db --repeats 3 --predictions ~/basis-eval-data/predictions/rooms19_v3_opus55
```
