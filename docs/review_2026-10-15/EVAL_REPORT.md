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
