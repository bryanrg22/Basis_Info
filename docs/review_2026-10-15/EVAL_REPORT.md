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
  Measured in section 22 (in-memory index checked against pgvector).
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
| Per-photo surfaces and room type | gpt-6-luna (28.1; gpt-6.1-sol before) | `photo_surfaces/v2` (19; v1 before) | low | `VISION_SURFACES_MODEL`; effort and budget as above | 11, 12.1, 28.1 | merged with the countables call (`basis/vision/merge.py`); room accuracy and surface F1 equal to gpt-6.1-sol, $0.019 against $0.336 per 52 photos (28.1) | `VISION_SURFACES_MODEL=gpt-6.1-sol` |
| Count fusion: second countables call | claude-opus-5-5 | `photo_countables/v3` (19) | medium | `VISION_COUNT_FUSION=per_class`, `VISION_FUSION_MODEL`, `VISION_FUSION_REASONING_EFFORT`; table `section15.4/2026-10-16` (`basis/vision/fusion.py`) | 15.4 | count MAE 0.124 vs 0.180 (LOO), $2.20 per 52 photos | `VISION_COUNT_FUSION=off` |
| Room inventory, multi-photo rooms | claude-sonnet-5-5 | `room_inventory/v3` | low | `ROOM_RECONCILE=true`, `ROOM_PROMPT_VERSION=v3`, `ROOM_MODEL`, `ROOM_REASONING_EFFORT` | 16.5, 28.2 | room count MAE 0.72 to 0.43 (sol input), 0.34 (Opus input); 28.2: recommended split, gpt-6-luna reconciling and Sonnet for the second look (materials decide IRS buckets): `ROOM_MODEL=gpt-6-luna`, the second look staying on `ROOM_MATERIALS_MODEL` (29.1) | `ROOM_RECONCILE=false` (per-photo rows become assets) |
| Room inventory, one-photo rooms | none (the photo's analysis) | - | - | `ROOM_SINGLE_PHOTO=per_photo` | 16.5 | part of the row above | `ROOM_SINGLE_PHOTO=reconcile` (a call per room) |
| Material second look on wainscoting, wall finish, tile | claude-sonnet-5-5 | `surface_material/v3` | low | `ROOM_MATERIALS=sensitive`, `ROOM_MATERIALS_MODEL`, `ROOM_MATERIALS_REASONING_EFFORT` (29.1; the room model before) | 16.5 | material present on 9 of 9 report rows that need one; $0.33 per run with the row above | `ROOM_MATERIALS=none` |
| Object localization | SAM 3 (Replicate) | phrase set `v3` | - | `REPLICATE_API_TOKEN`; per-class phrases and count thresholds (`basis/vision/localize.py`, `DEFAULT_PHRASE_SET`) | 10.2, 19 | instance masks for review; counts not fused (15.3); mAP@0.5 0.76 out of sample (19) | token unset: skipped; phrase set v1 before 19 |
| Extraction fallback | gpt-6-luna | `appraisal_field_fallback/v1` | low | `TEXT_MODEL`, `EXTRACTION_REASONING_EFFORT`, budget 8000 (`EXTRACTION_FALLBACK_MAX_TOKENS`) | 17.1, 17.3 | 0.924 of 75 fields, 12 of 12 critical, $0.0024 per study | `TEXT_MODEL=gpt-4o-mini` (budget 800) |
| Asset classification (residual tool loop) | claude-sonnet-5-5 | `classify/v1` | low | `CLASSIFY_MODEL`, `CLASSIFY_REASONING_EFFORT` | 17.2, 17.3, 28.3 | 0.688 bucket accuracy, 96% verified citations, $1.25 per 46 rows; 28.3: no cheaper model matched (gpt-6-luna, gpt-5.6-luna, gpt-5.6-terra), so kept | `CLASSIFY_MODEL=gpt-4o-mini` |
| **Measured, off by default:** room inventory of interior rooms (one call with all the room's photos) | claude-opus-5-5 | `room_photos_inventory/v2` | high | `ROOM_STEP_MODE=reconcile` (default; `room_call` switches it on), `ROOM_CALL_MODEL`, `ROOM_CALL_REASONING_EFFORT`; exterior, parking and mechanical rooms keep the rows above | 26.4, 26.6, 26.7, 29 | room count MAE 0.272 against 0.358 through the wired code (29.2), $1.24 per study for the whole room step | (today: the two room-inventory rows above) |
| **Measured, off by default:** material from segmentation-guided surface crops | claude-sonnet-5-5 (Mask2Former Swin-L ADE20K masks, local CPU) | `surface_crop_material/v1` | low | `MATERIALS_SECOND_LOOK=per_photo` (default; `surface_crops` switches it on), `SURFACE_CROP_MODEL`, `SURFACE_CROP_REASONING_EFFORT`, `SURFACE_SEGMENTER=local` (`replicate`: not measured) | 26.3, 29 | material hit 0.79 against 0.64 (26.3; 29.3 through the wired code); crop answer where a crop exists, the room's material elsewhere | (today: the per-photo and second-look materials) |

Notes on the wiring (code, not measurement):

- Since 28.1 the decomposed step's surfaces call has its own model (`VISION_SURFACES_MODEL`,
  `basis.vision.analyze.VisionContext.surfaces_model`); `VISION_MODEL` is the countables call
  and the single-call step. Switching to the Opus 5.5 fallback of 12.1 therefore takes both
  settings (`VISION_MODEL=claude-opus-5-5`, `VISION_SURFACES_MODEL=claude-opus-5-5`); with
  `VISION_MODEL` alone, the surfaces call stays on gpt-6-luna.
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
- The two "measured, off by default" rows (section 29) were measured on this one study with
  a rule chosen on it (26.7), so they ship switched off: confirming them on the second client
  package is setting `ROOM_STEP_MODE=room_call` and `MATERIALS_SECOND_LOOK=surface_crops`
  and re-running `evals.replay_rooms` there (29.5).

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
## 20. Two-column parsing (2026-10-16)

The ingestion parser (`basis/retrieval/parse.py`) grouped words into lines by height alone,
so on IRS Pub 527 and Pub 946 (two 255 pt columns on the same baselines, an 18 pt gutter) the
left and right columns were joined into one line per baseline ("Generally, if you can
depreciate intangible property, you for the section 179 deduction and the special
depreciation ..."). Every chunk of those pages mixed two texts, which breaks verbatim
excerpts and citation checks.

**Fix.** Per page, `detect_columns` looks for a vertical whitespace gutter: the x projection
of the words (the projection-profile step of XY-cut layout analysis) and the widest run at
the lowest coverage, with at most 20% of the rows crossing it (full-width titles, spanning
tables, footers). The gutter is accepted only when both columns are at least 30% of the text
width and 0.6 of each other, hold 3 or more rows, their median row fills half the column, and
70% of their tokens are words rather than numbers. Words and ruled tables that cross the
gutter cut the page into bands; each band between two cuts is read left column, then right
column. Running headers and footers are found on the old full-width lines first (so a header
split by the gutter is still one repeated key), then their words are removed. A page with no
accepted gutter takes the old path unchanged. Pages read by column are marked
`ParsedPage.columns == 2`.

**Pages read by column** (private corpus, `~/basis-eval-data/corpus`):

| Document | Pages | Two-column | Note |
|---|---|---|---|
| IRS Pub 527 (2024) | 37 | 30 | the rest: worksheets and the index |
| IRS Pub 946 (2024) | 112 | 66 | pages 69-111 are the rate tables (single column) |
| IRS Cost Segregation ATG | 348 | 0 | single column |
| Rev. Proc. 87-56 | 42 | 0 | description \| class life \| GDS \| ADS table, kept row by row (the balance and number guards) |
| RSMeans Building 2020 | 964 | 92 | OCR text; prose pages improve, some unit-price pages are split too (word share 0.70-0.71 against IRS's lowest 0.73) |
| RSMeans Residential 2020 | 802 | 69 | as above; the document body size moves from 7.5 to 8.0 pt |
| Study appraisal and engineer report (inputs) | 32 | 0 | |

**Single-column output is unchanged.** On every page with `columns == 1` of the eight private
PDFs and the synthetic fixtures, every element (type, text, box, size, weight, ordinal), table,
body size and furniture key equals the pre-change parser's output (field by field, against
`main` at 8908ed6). The test suite locks the fixtures with SHA-256 digests of the pre-change
output.

**Verbatim sentences.** Sentences of 40 to 200 characters from pymupdf's page text (which reads
these PDFs column by column), checked with the production matcher
(`basis.classify.verify.excerpt_supported`) against the parser's page text:

| Document | Pages | Sentences | Before | After |
|---|---|---|---|---|
| Pub 527 | two-column | 941 | 0.102 | 0.914 |
| Pub 946 | two-column | 1672 | 0.095 | 0.885 |
| Pub 527 | all | 1071 | 0.124 | 0.838 |
| Pub 946 | all | 2039 | 0.086 | 0.733 |
| Cost Segregation ATG | all (single column) | 2172 | 0.594 | 0.594 |
| Rev. Proc. 87-56 | all (single column) | 185 | 0.476 | 0.476 |

The rest (about 9% on two-column pages) is not explained yet. Suspected causes: hyphenated
line breaks, word boundaries that differ between pymupdf and pdfplumber, and sentences that
run across a spanning band.

**Retrieval and citations.** `python -m evals.run --corpus-text parser` (new option) reads
the corpus through the production parse + chunk path instead of pymupdf. Dev split, 50 queries,
65 rules-table citations; only `retrieval_qrels_v1.json` and the corpus were loaded, because
the other private label files no longer validate against this branch's vocabulary:

| System | ndcg_10 | recall_5 | recall_10 | mrr | resolved_fraction |
|---|---|---|---|---|---|
| pymupdf page text (the earlier baseline) | 0.4225 | 0.3823 | 0.5180 | 0.5567 | 1.000 (65/65) |
| parser, before | 0.4102 | 0.3793 | 0.4953 | 0.5282 | 1.000 (65/65) |
| parser, after | 0.4163 | 0.3833 | 0.5020 | 0.5367 | 1.000 (65/65) |
| paired delta after - before (SE) | +0.006 (0.010) | +0.004 (0.004) | +0.007 (0.007) | +0.009 (0.021) | 0 |

The retrieval change is within noise (ndcg_10: 5 queries up, 3 down, 42 the same). Page-level
BM25 is a bag of words, so word order hardly moves it; the gain is in the text itself (the
table above) and in what an embedding or a citation sees. `rule_citations` cannot move: all 65
citations are in the single-column ATG (59) and Rev. Proc. 87-56 (6), so here it only confirms
that nothing regressed. A citation to Pub 527 or Pub 946 would have failed before on most
pages.

**Ingestion.** `python -m basis.retrieval.ingest --corpus reference --fresh --max-pages 2000`
over the six corpus PDFs into a throwaway pgvector database (not the live study's): version 1,
7,466 chunks (Pub 527: 132 text, 8 table; Pub 946: 301 text, 141 table), MiniLM embeddings,
179 s. The stored page 10 chunk of Pub 946 contains "Generally, if you can depreciate
intangible property, you usually use the straight line method of depreciation." intact.

**Limits.** Only two columns are recognized; a narrow heading that spans both columns without
crossing the gutter is read as part of the left column; the pymupdf fallback (pages with no
pdfplumber words) is unchanged.
## 21. Detector and language model together (2026-10-16)

Question (Bryan): should SAM 3 run before the language model, then the model, then SAM 3
again? Today the vision model counts first and SAM 3 runs after it, prompted with the model's
labels, only to draw highlight masks. This section measures the other orders and the loop
against the current best per-photo counts (section 15.4, "4c": per class, the better of
`gpt-6.1-sol` at low effort and `claude-opus-5-5` at medium effort on `photo_countables/v2`,
chosen leave-one-photo-out: count MAE 0.124, exact 0.899, count precision 0.92, recall 0.87).
Same yardstick: the 52 photos of the one study, 191 hand-verified objects in six count
classes, `photo_counts` of `evals.run`; three repeats unless stated; "Diff" is the paired
bootstrap over photos of section 15 (mean per-photo count error minus the baseline's, 95%
interval, 5,000 resamples), now a module (`evals.compare_counts`).

### 21.1 What the literature says, and what was tested

- Set-of-Mark prompting (Yang et al. 2023, arXiv 2310.11441; github.com/roboflow/som): a
  detector or segmenter proposes regions, each is drawn on the image with a number, and the
  model answers by number; zero-shot GPT-4V with marks beat fine-tuned grounding models on
  referring and grounding tasks. Tested as experiment 1 (SAM 3 first).
- SAM 3 Agent (the SAM 3 paper, arXiv 2511.16719, and its documentation): a multimodal model
  turns the request into noun phrases, calls SAM 3, inspects and filters the masks, and calls
  SAM 3 again where something is missing; SAM-Veteran (ICLR 2026) trains that proposer.
  Tested as experiment 2 (the loop: SAM 3 again for the objects the model adds).
- Point-then-count (arXiv 2603.21746): a model that emits one point per object before it counts
  beats direct counting, the gap growing with the count; the pointers it studies place more than
  94% of their points on the object, Molmo strongest. "Assessing visual enumeration"
  (arXiv 2512.15254): Claude 4.5 Sonnet and Gemini 2.5 Pro count better when asked to locate,
  label, then count; GPT-5 does not. Tested as experiment 3 (the model points first, SAM 3
  masks the points) and, since the same call states counts after pointing, experiment 4
  ("locate, label, then count" without a detector).
- Exemplar counters (CountGD, T-Rex2, GeCo) are not hosted on Replicate; self-hosting is out of
  scope for this pass.
- Temperature voting was not run. `gpt-6.1-sol` accepts only its default temperature and Claude
  with thinking requires 1, and the counting literature reports that vision-language models are
  confidently wrong on counts, so sampling more answers repeats the same error (section 15.3
  measured it: repeat-to-repeat noise is the small part of the error; the median of three gains
  0.010).
- Not repeated (section 15): count fusion of raw SAM 3 and model counts, a yes/no check of each
  SAM 3 crop, tiling, majority vote over repeats.

### 21.2 How each ordering was built

Code: `basis/vision/marks.py` (mark selection, drawing, the answer schemas, Molmo point
parsing, points resolved to masks), `evals/predict_marks.py` (experiments 1 and 2),
`evals/predict_points.py` (experiments 3 and 4), `evals/compare_counts.py` (the paired
bootstrap), prompts `photo_marks/v1` and `photo_points/v1` (released in the manifest). The
pipeline (`basis/vision/analyze.py`, `localize.py`) is unchanged. Every runner writes the base
run's counts with the seven countable labels replaced (the other labels, and so label F1 and room
type, stay the baseline's: 0.70 and 0.90 for every row; the harness's component labels were
revised after section 15, which is why the baseline's label F1 now reads 0.70 against 0.741
there).

- Proposals: the saved SAM 3 runs of section 10 (v1 and v2 phrase sets, no new SAM 3 calls),
  pooled; countable labels at score >= 0.3 (recall-oriented: at 0.3 the marks cover 94% of the
  verified objects, at 0.5 only 86%); one mark per object across labels (greedy, mask IoU > 0.6:
  "interior door" and "front door" on the same door are one mark); numbered in reading order.
  8.9 marks per photo on average, 51 at most. The verified boxes were themselves built from SAM
  3's boxes (section 10.2), so the coverage figures flatter SAM 3; the count scores do not
  depend on that.
- Experiment 1 (SAM 3 first, Set-of-Mark): the photo enlarged to 1024 px with the marks drawn
  (variants: boxes or mask outlines; with or without the unmarked photo before it), and the list
  "number: detector's guess". The model gives every mark a label or `duplicate`,
  `part_or_group` or `not_countable`, and lists every object no mark covers with its label and
  an approximate centre (x, y as fractions of the image). Count = kept + added.
- Experiment 2 (the loop): each added point takes the highest-scoring saved SAM 3 candidate of
  its label family under it (score >= 0.1; `pick_prompted`); a point inside a kept mark of its
  family, or whose mask overlaps a counted mask by more than mask IoU 0.5, is a duplicate; the
  count is the kept marks plus the masked additions ("masked"). Points with no saved candidate
  were to get a fresh SAM 3 point prompt (`--prompt-sam`, implemented and unit-tested); the
  Replicate account ran out of credit during experiment 3 (402 "insufficient credit"), so that
  step was not run and the masked count leaves such points out (1.3% of counted objects for the
  winner; the "all" variant that counts them scores 0.140, row 2c).
- Experiment 3 (the model first, then SAM 3): one `photo_points/v1` call per photo (Opus,
  sol), or Molmo-7B-D on Replicate (`zsxkib/molmo-7b`, one "Point to every ..." query per class,
  greedy: temperature 0.1, top-k 1, one repeat since repeats would be identical); points resolved
  to masks as in experiment 2. Counts three ways: the points, the masks only, masks plus
  unmasked points.
- Experiment 4 ("locate, label, then count", no detector): the counts the same `photo_points`
  call states after listing its points.

### 21.3 Every system against the current best

Count MAE, exact rate, signed bias, count precision / recall over (photo, class, repeat), 936
pairs. Cost is one prediction of 52 photos for the counting step only (the baseline's two
countables calls: $0.47 sol + $1.40 Opus; the shared surfaces call, $0.33, is in neither);
SAM 3 is the saved runs (a production call is about $0.003 and 3.2 s per photo, added to the
SoM rows' seconds, which are sequential: SAM 3, then the model). Masks: share of counted
objects that come with a SAM 3 mask.

| System | Count MAE | Exact | Bias | Count P / R | Diff vs 4c [95% CI] | $ / 52 | s / photo | Masks |
|---|---|---|---|---|---|---|---|---|
| **Current best (4c)**: sol low + Opus medium per class | **0.124** | **0.899** | -0.04 | 0.92 / 0.87 | 0 | $1.87 | 11.1 | none (highlights separate) |
| sol low alone | 0.179 | 0.865 | +0.03 | 0.84 / 0.87 | +0.056 [+0.021, +0.092] | $0.47 | 11.1 | none |
| Opus medium alone | 0.150 | 0.889 | -0.06 | 0.92 / 0.83 | +0.026 [+0.002, +0.054] | $1.40 | 7.4 | none |
| 1a. SoM, boxes + photo, sol | 0.179 | 0.851 | +0.08 | 0.81 / 0.92 | +0.056 [+0.011, +0.105] | $0.41 | 3.2 + 16.8 | 94% |
| 1b. SoM, boxes + photo, Opus | 0.150 | 0.872 | +0.05 | 0.85 / 0.92 | +0.026 [-0.009, +0.059] | $1.31 | 3.2 + 10.0 | 97% |
| 1c. SoM, outlines + photo, sol | 0.161 | 0.861 | +0.07 | 0.83 / 0.93 | +0.037 [-0.003, +0.081] | $0.42 | 3.2 + 12.7 | 89% |
| 1d. SoM, outlines + photo, Opus | 0.140 | 0.882 | +0.03 | 0.87 / 0.91 | +0.016 [-0.011, +0.044] | $1.34 | 3.2 + 10.9 | 97% |
| 1e. SoM, boxes, marked photo only, sol | 0.254 | 0.829 | +0.11 | 0.75 / 0.88 | +0.130 [+0.068, +0.205] | $0.38 | 3.2 + 14.2 | 92% |
| 1f. SoM, boxes + photo, marks at >= 0.5, sol | 0.171 | 0.870 | +0.05 | 0.83 / 0.90 | +0.047 [-0.001, +0.100] | $0.37 | 3.2 + 9.5 | 83% |
| 1g. 1d with marks at >= 0.5, Opus | 0.155 | 0.879 | +0.01 | 0.87 / 0.88 | +0.031 [-0.002, +0.069] | $1.17 | 3.2 + 7.9 | 90% |
| 1h. 1c with marks at >= 0.5, sol | 0.179 | 0.861 | +0.04 | 0.83 / 0.88 | +0.056 [+0.010, +0.107] | $0.37 | 3.2 + 9.2 | 82% |
| 2a. Loop on 1a (sol), masked | 0.171 | 0.857 | +0.06 | 0.83 / 0.91 | +0.047 [+0.006, +0.094] | as 1a | + 0 | 100% |
| 2b. Loop on 1b (Opus), masked | 0.149 | 0.873 | +0.04 | 0.86 / 0.91 | +0.025 [-0.011, +0.059] | as 1b | + 0 | 100% |
| **2c. Loop on 1d (Opus, outlines), masked** | **0.138** | **0.885** | +0.02 | 0.88 / 0.90 | **+0.014 [-0.014, +0.042]** | **$1.34** | **3.2 + 10.9** | **100%** |
| 2d. Loop on 1c (sol, outlines), masked | 0.153 | 0.870 | +0.04 | 0.85 / 0.91 | +0.029 [-0.013, +0.073] | as 1c | + 0 | 100% |
| 2e. Loop on 1f (sol, >= 0.5), masked | 0.151 | 0.881 | +0.00 | 0.88 / 0.88 | +0.027 [-0.010, +0.069] | as 1f | + 0 | 100% |
| 2f. Loop on 1g (Opus, outlines, >= 0.5), masked | 0.151 | 0.880 | -0.01 | 0.88 / 0.87 | +0.027 [-0.004, +0.065] | as 1g | + 0 | 100% |
| 3a. Opus points, count = points | 0.166 | 0.876 | -0.05 | 0.90 / 0.82 | +0.042 [+0.012, +0.076] | $0.81 | 6.5 | 70% |
| 3b. Opus points, masks only | 0.275 | 0.834 | -0.24 | 0.95 / 0.58 | +0.151 [+0.082, +0.233] | $0.81 | 6.5 | 100% |
| 3c. Opus points, masks + unmasked points | 0.171 | 0.875 | -0.08 | 0.91 / 0.80 | +0.047 [+0.015, +0.087] | $0.81 | 6.5 | 70% |
| 3d. sol points, count = points | 0.186 | 0.859 | +0.04 | 0.82 / 0.88 | +0.062 [+0.019, +0.109] | $0.27 | 11.4 | 92% |
| 3e. sol points, masks + unmasked points | 0.179 | 0.864 | +0.02 | 0.84 / 0.87 | +0.056 [+0.018, +0.100] | $0.27 | 11.4 | 92% |
| 3f. Molmo-7B points (37 photos, 1 repeat) | 1.293 | 0.658 | +0.68 | 0.32 / 0.60 | +1.152 [+0.698, +1.701] | $0.66 | 14.5 | 54% |
| 3g. Molmo-7B points, masks only (37 photos) | 0.545 | 0.739 | -0.18 | 0.69 / 0.53 | +0.404 [+0.248, +0.598] | $0.66 | 14.5 | 100% |
| 4a. Opus "locate, label, then count" (stated) | 0.167 | 0.875 | -0.05 | 0.90 / 0.82 | +0.043 [+0.013, +0.077] | $0.81 | 6.5 | n/a |
| 4b. sol "locate, label, then count" (stated) | 0.186 | 0.859 | +0.04 | 0.82 / 0.88 | +0.062 [+0.019, +0.109] | $0.27 | 11.4 | n/a |
| 5a. Per-class fusion LOO: sol, Opus, 1b | 0.131 | 0.893 | -0.02 | 0.91 / 0.87 | +0.007 [+0.000, +0.016] | $3.18 | 13.2 | partly |
| 5b. Per-class fusion LOO: sol, Opus, 2c | 0.139 | 0.889 | -0.02 | 0.90 / 0.87 | +0.015 [+0.001, +0.032] | $3.21 | 14.1 | partly |

Against the same model alone (paired over photos): SoM changes nothing for Opus with boxes
(1b: +0.000 [-0.048, +0.044]) and helps a little, not significantly, with outlines and the loop
(2c: -0.012 [-0.057, +0.031]); for sol, outlines -0.018 [-0.056, +0.016], and the loop with
marks at >= 0.5, -0.029 [-0.069, +0.010] (2e). Pointing first does not help either model (Opus points +0.016
[-0.011, +0.043], sol +0.006 [-0.029, +0.044]). Every model run reported only the model it
asked for (`models_reported` in each `run.json`), and no model call failed; Molmo lost 15 of 52
photos to the Replicate credit running out, so its rows are scored on the 37 it answered.

### 21.4 The best new system's per-class counts (2c)

SAM 3 marks at >= 0.3, outlines, the unmarked photo first, Opus 5.5 at medium effort keeps,
relabels or rejects each mark and adds misses; added points masked from the saved SAM 3
candidates; the count is the masked objects.

| Class | Current best (4c) MAE / exact / P / R | 2c MAE / exact / P / R | Opus alone MAE / exact / P / R |
|---|---|---|---|
| window | 0.24 / 0.83 / 0.91 / 0.91 | 0.24 / 0.83 / 0.89 / 0.93 | 0.24 / 0.83 / 0.91 / 0.91 |
| door | 0.10 / 0.90 / 0.96 / 0.86 | 0.15 / 0.85 / 0.86 / 0.89 | 0.10 / 0.90 / 0.96 / 0.86 |
| light fixture | 0.12 / 0.90 / 0.94 / 0.81 | 0.16 / 0.85 / 0.81 / 0.86 | 0.11 / 0.91 / 0.92 / 0.83 |
| exterior light | 0.08 / 0.92 / 0.91 / 0.90 | 0.08 / 0.92 / 0.91 / 0.91 | 0.24 / 0.86 / 0.90 / 0.51 |
| electrical outlet | 0.12 / 0.90 / 0.92 / 0.78 | 0.08 / 0.93 / 0.92 / 0.87 | 0.12 / 0.90 / 0.88 / 0.81 |
| kitchen cabinet | 0.09 / 0.94 / 0.93 / 0.88 | 0.11 / 0.93 / 0.88 / 0.89 | 0.08 / 0.94 / 0.93 / 0.89 |
| all six | 0.124 / 0.899 / 0.92 / 0.87 | 0.138 / 0.885 / 0.88 / 0.90 | 0.150 / 0.889 / 0.92 / 0.83 |

Where the marks help: the misses section 15 left. Opus with marks finds the exterior lanterns it
misses on its own (exterior-light recall 0.51 to 0.91, equal to sol's, so one model now covers
what took two) and more outlets (recall 0.81 to 0.87, MAE 0.12 to 0.08). Where they hurt: precision on look-alikes. The model keeps a mark it would not
have counted unprompted: lamps and reflections as light fixtures (0.92 to 0.81 precision),
door-like marks (0.96 to 0.86), cabinet fragments (0.93 to 0.88). The two effects cancel on
windows. In 2c's answers, 42% of the marks are kept, 36% rejected as `not_countable`, 17% as
`part_or_group`, 6% as `duplicate`; 79% of the kept marks match a verified box (IoU >= 0.5,
same class family) against 42% of all marks; the kept marks cover 79% of the verified objects.
The model relabelled 15 interior "light fixture" proposals as exterior lights and 12 interior
doors as exterior doors, the confusions section 10.2 found in SAM 3 alone.

### 21.5 Reading: what did not work, and why

- Set-of-Mark with boxes (1a, 1b) raises recall (0.87 to 0.92) and lowers precision by as much,
  so neither model gains. The marks pull the model's attention to every proposal, and at the
  recall-oriented threshold more than half of the proposals are wrong (42% box precision). The
  model rejects most of them, but not enough of the plausible ones.
- The unmarked photo must come first (1e: without it, +0.130 and significantly worse than sol
  alone): boxes and tags cover small objects (outlets fell to 0.61 precision, windows to 0.77),
  the occlusion the SoM authors avoid by drawing light, semi-transparent masks. Mask outlines
  cover less of the object than boxes and help both models (1c, 1d).
- Fewer, surer marks (>= 0.5) do not help on top of outlines (1g, 1h): exterior lights and
  outlets are the classes SAM 3 scores low (section 10.2), and with their marks gone the model
  goes back to missing them (Opus exterior-light recall 0.91 to 0.74).
- The loop (experiment 2) does what it should for the highlight layer: every counted object has a
  mask, and duplicates the model adds on top of a kept mark are caught by mask overlap. As a
  count it changes little (Opus 0.140 to 0.138, sol up to 0.020 better), because the model adds
  few objects (18 or 19 in 156 Opus answers) once it has the marks.
- Pointing first (experiment 3) does not reproduce the gains reported for it. The stated counts
  are nearly the point counts (4a 0.167 against 3a 0.166: the model counts its own points), and both are slightly worse
  than the same model's direct counts. The pointers are poorly grounded on these photos: 64% of
  Opus's points and 79% of sol's fall inside a verified box of their class (the paper's pointers:
  above 94%), so turning points into SAM 3 masks loses objects (3b: Opus recall 0.58) instead of
  confirming them.
- Molmo-7B (3f, 3g) is far off on these photos: it points at every cabinet door and drawer front
  (cabinet count error 4.6 a photo-class pair), misses half the windows of the exterior shots, and 30% of its
  points fall in a verified box of their class. The prompts were one short query per class; the
  paper's protocol is not tuned for building components, and a 7B pointer is not a substitute
  for the frontier models here.
- Adding the SoM counts to the per-class fusion (5a, 5b) is worse out of sample than the fusion
  without them. Fitted on all 52 photos, 5b would take outlets as max(sol, SoM) and exterior lights
  from SoM; held out, the rule flips on exactly the photos where SoM's advantage lies (one wide
  exterior shot carries most of 1b's window gain), the selection optimism of section 15.3.

### 21.6 Recommendation

Keep the order: the model counts first, and SAM 3 runs after it for the highlight masks. No
detector-first or loop ordering beat the current best: the closest, 2c (SAM 3 first, Opus
reviews outlined marks and adds misses, SAM 3 masks the additions), is 0.014 behind (95% CI
-0.014 to +0.042, not significant), and nothing here is significantly better than the model
alone. One property and 191 objects bound this: none of the differences between 2c, 4c and Opus
alone is resolvable on this set.

What 2c is good for, and the case for adopting it later, is the highlight layer, not the count:
with one model call instead of two ($1.34 against $1.87 per 52 photos, about 14 s per photo
sequential against 11 s in parallel) it gives counts close to the current best together with a
mask for every counted object, and the masks it shows are the reviewed ones (79% match a verified
object, against 42% of SAM 3's raw proposals at the same threshold and 0.46 precision at the
pipeline's 0.5, section 10.3). Today the highlight masks come from SAM 3 alone and need not agree with the
count. Before wiring it: settle the window and door conventions of section 15.3 (doors and light
fixtures are where 2c loses), re-measure on the second client package, and run the SAM 3 point
prompt for the 1-2% of added objects without a saved candidate (implemented; not run because
the Replicate credit ran out).

Spend for this section: $21.47 (Anthropic $14.14: SoM boxes $3.94, outlines $4.02, outlines at
0.5 $3.52, points $2.44, smoke tests $0.22; OpenAI $6.64: five SoM variants $5.83, points
$0.81; Replicate $0.69: Molmo $0.66, smoke $0.03; no new SAM 3 calls). The Replicate account
returned 402 "insufficient credit" during the Molmo run and needs a top-up before any further
SAM 3 or Molmo work. Scoring note: `report_rows_v1.json` in the data directory now names labels
that are not in the vocabulary on main (written by another session), so `evals.run` fails to load
it; this section was scored with a data directory that links every dataset file but that one.
Prediction directories under `$BASIS_EVAL_DATA_DIR/predictions`: `som_{box,outline}_orig_*`,
`som_box_marked_gpt61sol_low`, `som_*_t05_*` (each with `loop_masked/` and `loop_all/`),
`points_{opus55_med,gpt61sol_low,molmo7b}` (with `stated/`, `masked/`, `masked_all/`), the
fusions `counts_fused_sol_opus_somopus_loo` and `counts_fused_sol_opus_somoutline_loo`, and the
best new system `som_outline_orig_opus55_med/loop_masked` (2c, with its `eval.md`).

## 22. Retrieval: BM25, vectors and the hybrid (2026-10-16)

Until now `retrieval_qrels` scored one system, `bm25_pages` (BM25 over one chunk per page).
Production stores 400-token chunks with MiniLM embeddings and has a hybrid search, but
neither the vectors nor the fusion had been measured. This section scores four systems on
the same 50 queries and decides what to do with the hybrid.

**What the literature leads one to expect.** On BEIR (Thakur et al., NeurIPS 2021
datasets track), BM25 is a strong zero-shot baseline and dense retrievers trained on MS
MARCO often fall below it out of domain. all-MiniLM-L6-v2 is a general sentence model,
trained on about 1.17 billion sentence pairs (Reddit, S2ORC, Stack Exchange, PAQ and
others); none of them is tax or construction-cost text. Hybrids usually beat both sides.
Bruch, Gai and Ingber ("An Analysis of Fusion Functions for Hybrid Retrieval", ACM TOIS
2023) found a tuned convex combination of normalized scores beats RRF, RRF more sensitive
to its k than assumed, and the convex weight cheap to tune on a few queries. On statistics:
Smucker, Allan and Carterette (CIKM 2007) found the paired t-test, the bootstrap and the
randomization test agree on TREC runs, and their 2009 follow-up found the tests disagree
more as topics get fewer. Voorhees and Buckley (SIGIR 2002) showed that with 50 topics,
differences of a few points of MAP often flip on another topic set.

**Method.** `python -m evals.run --corpus-text parser --retrieval-systems all
--fusion-sweep` (new flags), dev split, 50 queries, 173 judgments at doc + page:

* `bm25_pages_parser`: the existing baseline (BM25 over the parser's page text, one item
  per page).
* `bm25_chunks`: the store's BM25 index (`Bm25Index`, IRS tokenizer, Lucene IDF) over the
  7,466 production chunks.
* `vector_chunks`: MiniLM query embedding, cosine against every chunk embedding, exact.
* `hybrid`: `basis.retrieval.hybrid.hybrid_search` itself with the configured fusion
  (RRF, k = 60; each side gives `2k` candidates).

The chunk systems take the top 50 chunks and read them as pages (first occurrence kept),
cut at 10; every query got 10 pages. Paired differences: 95% percentile bootstrap of the
mean per-query difference (10,000 resamples, seed 0) and a two-sided sign-flip
randomization p. The real-corpus test (gap 1 below) checks that the harness's in-memory
index ranks as the database does: BM25, vector (exact against pgvector HNSW with
`iterative_scan = strict_order`) and hybrid gave identical top-10 pages on all 50 queries.
Two full runs gave identical numbers.

| System | nDCG@10 | recall@5 | recall@10 | MRR | holes@10 |
|---|---|---|---|---|---|
| `bm25_pages_parser` (section 20) | 0.416 (0.043) | 0.383 | 0.502 | 0.537 | 0.826 |
| `bm25_chunks` | 0.415 (0.042) | 0.368 | 0.549 | 0.508 | 0.808 |
| `vector_chunks` (MiniLM) | 0.384 (0.041) | 0.338 | 0.475 | 0.464 | 0.830 |
| `hybrid` (RRF, k = 60) | **0.450** (0.038) | 0.379 | **0.571** | **0.539** | 0.800 |

Standard errors in brackets. The hole rate is now per query: the share of top-10 pages
with no judgment for that query. It used to count a page judged for any query as judged,
which gave 0.66 for `bm25_pages_parser`; 0.83 is the right number.

**Paired differences** (row minus column system, per query; nDCG@10 unless stated):

| Difference | Mean | 95% interval | p | wins / losses / ties |
|---|---|---|---|---|
| hybrid - vector | +0.067 | [+0.016, +0.118] | 0.015 | 28 / 15 / 7 |
| hybrid - vector, recall@10 | +0.096 | [+0.040, +0.154] | 0.002 | 17 / 4 / 29 |
| hybrid - bm25_chunks | +0.035 | [-0.014, +0.087] | 0.19 | 24 / 19 / 7 |
| hybrid - bm25_chunks, MRR | +0.030 | [-0.047, +0.108] | 0.46 | 18 / 11 / 21 |
| hybrid - bm25_pages_parser | +0.034 | [-0.024, +0.097] | 0.29 | 20 / 21 / 9 |
| hybrid - bm25_pages_parser, recall@10 | +0.069 | [-0.021, +0.162] | 0.15 | 15 / 11 / 24 |
| vector - bm25_chunks | -0.032 | [-0.112, +0.052] | 0.46 | 17 / 29 / 4 |
| bm25_chunks - bm25_pages_parser | -0.001 | [-0.041, +0.042] | 0.97 | 16 / 21 / 13 |
| bm25_chunks - bm25_pages_parser, recall@10 | +0.047 | [-0.001, +0.100] | 0.075 | 9 / 3 / 38 |

Vectors alone are the weakest system (BM25 is better on 29 queries, vectors on 17, and
vectors leave 12 queries with no relevant page in the top 10 against BM25's 7). The hybrid
is clearly better than vectors alone and has the highest mean on every metric but
recall@5 (0.379 against `bm25_pages_parser`'s 0.383), but its
gain over BM25 (+0.035 nDCG@10) is inside the noise of 50 queries.

**Fusion weights.** Weighted fusion (max-normalized, `w * bm25 + (1 - w) * vector`) over
the same candidates, and RRF:

| Fusion | nDCG@10 | condensed nDCG@10 |
|---|---|---|
| weighted, w = 0.0 (vector only) | 0.384 | 0.764 |
| weighted 0.1 / 0.2 | 0.424 / 0.434 | 0.812 / 0.820 |
| **weighted 0.3** | **0.469** | 0.818 |
| weighted 0.4 / 0.5 / 0.6 | 0.461 / 0.461 / 0.458 | 0.819 / 0.826 / 0.814 |
| weighted 0.7 / 0.8 / 0.9 | 0.447 / 0.445 / 0.433 | 0.811 / 0.821 / 0.829 |
| weighted 1.0 (BM25 only) | 0.415 | 0.808 |
| **RRF, k = 60 (production)** | 0.450 | **0.832** |

* With unjudged pages counted as misses (the harness's metric), w = 0.3 is best and
  w = 0.3 to 0.6 form a plateau. Choosing w by leave-one-out (best mean on 49 queries,
  scored on the 50th) picks 0.3 in all 50 folds, so the 0.469 is not only in-sample;
  5-fold cross-validation repeated 20 times gives 0.461 on average (0.438 to 0.469).
  Weighted 0.3 minus RRF: +0.019 [+0.003, +0.037], p = 0.037 (18 / 14 / 18); recall@10
  +0.006 [-0.020, +0.036] and MRR +0.020 [-0.022, +0.064] are not distinguishable.
* With condensed lists (Sakai, SIGIR 2007: unjudged pages removed before cutting at 10,
  the usual check when judgments are incomplete), the order flips: RRF is best and
  weighted 0.3 minus RRF is -0.014 [-0.038, +0.008], p = 0.23.
* RRF's k matters little here: k = 1, 5, 10, 20, 60, 100 give 0.438, 0.439, 0.444, 0.446,
  0.450, 0.446; the default 60 is the best of them.

So the 0.019 advantage of weighted 0.3 depends on how the 80% of unjudged pages are
treated, and the qrels pool came from the legacy BM25 and vector indexes (295 and 187
candidates), not from these systems.

**Why vectors are weak here (found while measuring).** MiniLM truncates its input at 256
word pieces, and the chunks are 400 cl100k tokens: 5,885 of the 7,466 chunks (79%) are
longer than 256 word pieces (median 328, 90th percentile 407), so the vector side never
sees the end of most chunks; the 80-token overlap covers only part of what is cut.
The corpus is also dominated by the two RSMeans cost books (6,208 of the 7,466 chunks,
OCR text, section 20), where codes and unit names favour lexical matching.

**Recommendation.**

1. **Keep the hybrid; do not drop either side.** It is the best system on every metric and
   under both ways of treating unjudged pages; vectors alone are clearly worse than the
   hybrid, and BM25 alone is better on average only on recall@5, by 0.004. Dropping
   vectors is not supported
   (hybrid minus BM25 is positive under both evaluations, though not significant);
   dropping BM25 is ruled out.
2. **Keep RRF (k = 60) as the default; do not change the weights.** The best weighted
   setting (0.3) wins under one treatment of unjudged pages and loses under the other,
   by less than 0.02 either way; that is not evidence for changing a default. The fusion
   is now a setting so the decision can be revisited without a code change:
   `RETRIEVAL_FUSION` (`rrf` | `weighted`, default `rrf`) and `RETRIEVAL_BM25_WEIGHT`
   (default 0.5, unused by RRF), bound on `basis.retrieval.Retrieval` and used by its new
   `hybrid` method. The `hybrid_search` defaults equal the setting defaults (a unit test
   keeps them so).
3. **Wire the hybrid into production.** The residual classifier's `search_reference` tool
   and `resolve_rule_citations` call `store.search_bm25` directly
   (`basis/classify/tools.py`, `basis/classify/router.py`), so production retrieval is
   BM25 today and the measured hybrid gain is not being used. Passing
   `search=Retrieval.hybrid` bound to the store (the `search` parameter already exists) is
   the change; it should be measured on the `assets` eval, since only the classifier's
   answers matter. Not done here.
4. **Fix the vector input before tuning fusion.** Embed chunks no longer than 256 word
   pieces (smaller embedding windows mapped to the same chunk, or a model with a longer
   window), then re-run this section.
5. **Judge the holes.** About 400 of the 500 top-10 pages of each system are unjudged.
   Judging the union of the four systems' top 10 (and of weighted 0.3) is what would let
   the fusion question be answered.

**What 50 queries can and cannot show.** The standard deviation of the per-query nDCG@10
difference between the hybrid and BM25 is about 0.18, so the standard error of the mean
difference is about 0.026 and, at 80% power and alpha 0.05, only differences of about
0.07 are reliably detected. Detecting the observed +0.035 would need about 210 queries.
Differences between fusion settings have a smaller spread (SE about 0.009 for weighted 0.3
versus RRF) but are themselves around 0.02, and the condensed-list check shows that
incomplete judgments move them by as much. All 50 queries are in the `dev` split (no
held-out set), and the weight sweep was evaluated on the same queries it was chosen on;
leave-one-out and cross-validation correct for the choice, not for the queries being the
only ones. The queries are one engineer's, written for this corpus; recall is a lower bound
while 80% of the top 10 is unjudged.

### 22.1 Gap 1: the real corpus as an automated test

`tests/integration/retrieval/test_real_corpus.py` (markers `integration`, `slow`,
`eval_data`; skipped with the reason unless `BASIS_EVAL_DATA_DIR` has the six corpus PDFs
and MiniLM is cached; CI deselects `eval_data`; `tooling/evidence/capture.py` runs it as
the optional `real_corpus` set when the data is present). It runs
`python -m basis.retrieval.ingest --corpus reference --fresh --max-pages 2000` as a
subprocess into the emptied test container and checks:

| Check | Result |
|---|---|
| Published reference versions | 1 (version 1, `sentence-transformers/all-MiniLM-L6-v2`) |
| Chunks (pinned baseline, 1% tolerance) | 7,466: ATG 348 pages, 346 text + 282 table; Pub 527 37, 132 + 8; Pub 946 112, 301 + 141; Rev. Proc. 87-56 42, 48 + 0; RSMeans Building 964, 3,775 + 0; RSMeans Residential 802, 2,433 + 0 |
| Versions pinned with the baseline | `PARSER_VERSION = "2026-10-16.two-column"`, `CHUNKER_VERSION = "cl100k_base-400/80-page-v1"` (new constants; a change fails the test until re-pinned) |
| Embeddings | every chunk 384-d, unit length (within 1e-3) |
| Pages and text | every chunk on one page, none empty |
| Two-column sentences whole in one chunk | Pub 527: 0.923 of 937 sentences on 30 pages (pinned >= 0.91); Pub 946: 0.892 of 1,659 on 66 pages (pinned >= 0.88) |
| Rules-table citations through `resolve_rule_citations` | 77 of 77 |
| Harness index vs store, top-10 pages, 50 queries | BM25 50, vector 50, hybrid 50 identical |
| Ingest wall time / cold BM25 build | 150 s and 181 s in two runs / 0.9 s (7,466 chunks) |

The sentence check is section 20's (sentences of 40 to 200 characters of pymupdf's page
text, `excerpt_supported`), but stricter: the sentence must be whole in one chunk, as a
citation must, not just somewhere on the page; the rates are close to section 20's page
rates (0.914, 0.885). The chunk counts match section 20's ingest (7,466; Pub 527 132 + 8,
Pub 946 301 + 141).

## 23. Live end-to-end run (2026-10-16)

Question: does the pipeline of section 18 work outside the test harness, and do its outputs
score like the offline evaluation? The client study (the appraisal and 52 photos) was run
through the running system: API, Temporal workflow and worker, outbox relay, Postgres, Azure
Document Intelligence, Anthropic and Replicate, uploaded with the signed-upload flow and
approved at the three gates through the API with no corrections. The run log, the redacted
payloads, the gate contents and the Temporal UI and API screenshots are in
`docs/test-evidence/v0.7.0-live/`.

Two configurations, `PROPERTY_CLASS=nonresidential` in both:

- **Defaults** (run 3, after the OpenAI account was refilled): section 18 unchanged: gpt-6.1-sol
  decomposed with per-class count fusion on claude-opus-5-5, gpt-6-luna extraction fallback,
  Sonnet 5.5 room step and classifier, SAM 3. The recorder shows each step answered only by its
  configured model (`payloads/run3_defaults/models_by_step.json`; Azure OpenAI was unset, so
  nothing could fall back). Its references are the offline winners: 15.1 row 4c, 16.5 on the sol
  input, 17.1 luna, 17.2 Sonnet.
- **Claude overrides** (runs 1 and 2, while the account had no credit): per-photo calls on
  `claude-opus-5-5` (decomposed, low, budget 1200), fusion off, extraction fallback on
  `claude-sonnet-5-5`. Its references are the offline runs of the same models (15.1 row 1b, 16.5
  on the Opus input, 17 Sonnet).

One live run is one repeat; the offline numbers are means over three repeats unless marked.

### 23.1 What happened

The first run (v0.7.0 as tagged) **failed**: `aiohttp`, which the Azure DI async client needs,
was not installed, so `extract_fields` failed all three attempts within 16 seconds of the start; the
workflow carried on through the photos, SAM 3 and the room step and failed when it awaited
the appraisal, 17 minutes and $2.67 later. `POST /start` then answered 202 for the dead study.
With the dependency installed the second run (Claude overrides) **completed** in 15 min 57 s from `start`: photos
in 118 s (10.9 s each, five at a time), extraction in 94 s, SAM 3 in 705 s (Replicate's
throttle: 91 HTTP 429s, 4 activity retries, all 52 photos localized), the room step in 29 s,
assets in 167 s, export at once; each decision reached the workflow through the relay on its
first attempt within 2 s. It cost $5.95 ($5.80 Anthropic, $0.15 Replicate; Azure DI not
recorded), which the workflow's own budget matches ($25 - $19.07).

Four bugs, each fixed in its own commit with a test (`docs/test-evidence/v0.7.0-live/`
README): the missing `aiohttp` (`azure-core[aio]`); 409 `workflow-closed` instead of 202 when
starting a study whose workflow ended unsuccessfully; dates printed `M/D/YY` (the effective
date of appraisal, a critical field, was dropped as "not a date" and the extraction scored
confidence 0); a site area printed in acres stored as square feet with the decimals cut
(`1.39 ac` -> 1). The last two were re-checked on the real appraisal through the production
extraction path after the fix: effective date 2025-01-29, site area 60,548 sq ft, no
validation issue, confidence 0.6.

The third run, on the defaults and the fixed code, **completed** in 14 min 34 s for $5.74
(Anthropic $4.84, OpenAI $0.78, Replicate $0.11; the budget agrees). Photos took 310 s (three
calls each, 29 s mean), SAM 3 649 s, extraction 87 s (confidence 0.6, 40 fields, as in the
recheck). Count fusion fused 51 photos; on one, Opus listed an item with count 0 on both
attempts, the answer failed validation and the photo kept sol's counts (flagged
`count_fusion_fallback`). That is the fifth bug: a 0-count item now means "not visible" and is
dropped instead of failing the answer.

### 23.2 Live against offline

Scored by `evals.run` on prediction files exported from the completed study's database rows
(`docs/review_2026-10-15/repro/live_export.py`; the mapping rules are in its docstring), on the
label set as it stood for sections 15 to 17. The private label files were regenerated by
another session during this run; the set used here was rebuilt from the label builders'
backups and reproduces section 16.5's row exactly (0.34 / 0.72 / 0.90 / 31 of 39).

Defaults (run 3):

| Step | Metric | Offline winner | Live, defaults |
|---|---|---|---|
| Per-photo counts (6 verified classes, 312 pairs) | count MAE / exact / bias | 0.124 / 0.899 / -0.04 (15.1 4c) | **0.135 / 0.897 / -0.02** |
| | count precision / recall | 0.92 / 0.87 | 0.90 / 0.87 |
| Per-photo labels / room type | label F1 / accuracy | 0.741 / 0.90 | **0.740 / 0.88** |
| Per-photo step cost | $ per 52 photos | $2.20 | $2.20 |
| Room inventory (33 rooms) | count MAE / exact / bias | 0.43 / 0.63 / +0.10 (16.5, sol input) | **0.60 / 0.51 / -0.23** (14 rooms) |
| | rooms exact / label F1 | 2.7 of 33 / 0.85 | 2 of 33 / 0.69 |
| Report rows (39 photo-derivable) | rows evidenced / material present | 32 of 39 / 9 of 9 | **32 of 39** / 9 of 9 |
| Appraisal fields (75 labelled) | accuracy / critical | 0.924 / 12 of 12 (17.1 luna, all 75 asked) | **0.307 / 8 of 12** (23 right, 0 wrong, 52 not extracted) |
| Asset buckets (46 schedule rows) | accuracy | 0.688 (17.2 Sonnet) | 0.500 (32 answered, 14 abstain) |
| | on the 32 rows photos answer | 22 of 32 (17.2 repeat 1, same rows) | **23 of 32** |
| | citations verified / needs review | 96% / 46% of rows | 263 of 263 / 175 of 192 assets |

Claude overrides (run 2):

| Step | Metric | Offline (same models) | Live, overrides |
|---|---|---|---|
| Per-photo counts (6 verified classes, 312 pairs) | count MAE / exact / bias | 0.166 / 0.876 / -0.05 (15.1 1b) | **0.157 / 0.872 / -0.02** |
| | count precision / recall | 0.90 / 0.83 | 0.88 / 0.86 |
| Per-photo labels | label F1 | 0.763 | **0.766** |
| Per-photo room type | accuracy | 0.88 | **0.90** |
| Per-photo step cost | $ per 52 photos | $2.23 | $2.22 |
| Room inventory (33 rooms) | count MAE / exact / bias | 0.34 / 0.72 / +0.06 (16.5) | **0.68 / 0.45 / -0.09** (21 rooms) |
| | rooms exact / label F1 | 6.7 of 33 / 0.90 | 3 of 33 / 0.68 |
| Report rows (39 photo-derivable) | rows evidenced / material present | 31 of 39 / 9 of 9 | **32 of 39** / 9 of 9 (cost coverage 0.97) |
| Appraisal fields (75 labelled) | accuracy / critical | 0.916 / 12 of 12 (17.1, all 75 asked of the model) | **0.307 / 8 of 12** (23 right, 0 wrong, 52 not extracted) |
| Asset buckets (46 schedule rows) | accuracy | 0.688 (17.2; repeat 1 0.674) | 0.500 (32 answered, 14 abstain) |
| | on the 32 rows photos answer | 22 of 32 (17.2 repeat 1, same rows) | **23 of 32** |
| | citations verified / needs review | 96% / 46% of rows | 261 of 261 / 186 of 203 assets |

Per photo, both live runs are the offline measurement: every difference is inside the spread
of section 15's repeats (the fused counts at 0.135 against 0.124 include the one photo that
fell back to sol's counts). The pipeline's two-call step, merge and storage add nothing and lose
nothing. The other three rows differ, and each difference has one cause.

### 23.3 Rooms: the grouping, not the models

Production assigns a photo to a room by the vision model's room identity (type, floor,
ordinal: `basis/vision/rooms.py` `assign_room`). Section 16 grouped photos by file name and the
engineer's 16 room aliases (`group_photos`), which only the eval runners call. Live, the
model's identity made 21 rooms of the 33 labelled: two-photo rooms are reconciled correctly,
but one "Bedroom" and one "Bathroom" with no floor collect several real rooms, and the
reconciliation call then de-duplicates objects that are in different rooms (bias -0.09).
Re-running the section 16.5 room step offline on the **same live per-photo outputs**
(`evals.predict_rooms --per-photo` on the analyses the worker stored, one repeat each):

| Grouping | count MAE | exact | bias | rooms exact | label F1 | report rows | cost |
|---|---|---|---|---|---|---|---|
| Model room identity (production, live) | 0.68 | 0.45 | -0.09 | 3 of 33 | 0.68 | 32 of 39 | $0.29 |
| File name, no aliases (49 rooms, summed into the labelled 33) | 0.65 | 0.61 | +0.51 | 4 of 33 | 0.89 | 32 of 39 | $0.19 |
| File name and the engineer's aliases (section 16) | **0.40** | 0.69 | +0.09 | 4 of 33 | 0.89 | 32 of 39 | $0.33 |
| Defaults (run 3, sol + fusion input): model room identity, live | 0.60 | 0.51 | -0.23 | 2 of 33 | 0.69 | 32 of 39 | $0.35 |
| Defaults: file name and aliases, same live input | **0.45** | 0.65 | +0.05 | 4 of 33 | 0.85 | 32 of 39 | $0.32 |

The first three rows are run 2's per-photo outputs (Opus); the last two run 3's (offline
reference on the sol input: 0.43).

With section 16's grouping the live per-photo outputs land at 0.40 and 0.45, against 0.34 and
0.43 offline (means of three repeats; section 16 treats single differences under about 0.1 as
noise). On the defaults the model's identity made only 14 rooms, so the de-duplication across
real rooms is stronger (bias -0.23). Without the aliases the
views of one room become separate rooms and their objects are counted twice (+0.51). So the
room step's measured accuracy depends on a grouping production does not do, and on the
engineer's room merges, which the product has no input for. Choosing the grouping (the
model's identity, file names when the package has them, the engineer merging rooms at
rooms_review before the inventory is built) is a product decision; it is proposed as its own
piece of work, not changed here.

### 23.4 Appraisal fields: coverage, not accuracy

Every field the pipeline extracted and the labels cover was right (23 of 23 after mapping
five catalogue keys to the label file's names for the same boxes, `CATALOGUE_TO_LABEL` in the
exporter). The pipeline stored 39 fields (38 from Azure DI key-value pairs, 1 from the model
fallback) against 75 labelled. Section 17.1 measured the model reading the whole document for
all 75 fields; production asks the model only for the six fields the catalogue marks critical
when Azure DI and the regex pass miss them. The four labelled critical fields it misses are
the site area as printed text (the pipeline stores square feet) and the room, bedroom and
bathroom counts, which Azure DI does not pair with their values on this form and which the
catalogue does not mark critical. Before the date fix, the effective date of appraisal was
also lost (the fallback answered it correctly, as printed).

### 23.5 Assets: a different input, and the review load

Section 17.2 classified the engineer's 46 schedule captions; production classifies what the
photos produced: 203 component assets in run 2 (80 by the rules table, 123 by the classifier
loop, 292 model calls, $3.27 against $1.25 for 46 rows; run 3: 192 assets, 277 calls, $3.08). To score, each schedule row takes the majority
bucket of the assets whose label evidences it (`report_rows` labels and room types); 14 rows
abstain (7 not photo-derivable, 6 vocabulary gaps, 1 not evidenced). On the 32 rows the photos
answer the live pipeline is right on 23, the offline captions on 22: the same. Every one of
the 261 citations verified (96% offline). But 186 of 203 assets are flagged for review, 106 of
them `COST_ROW_MISSING`: the static cost table has no row for windows, interior doors, wall
and ceiling finishes and most labels, so the assets review would be nearly every row.

### 23.6 Reading

- The system works end to end on real data once `aiohttp` is installed: upload, start, three
  gates, decisions through the relay, export, evidence with verified citations, objects
  editing. Nothing in the harness could see the missing dependency, the 202 for a failed study
  or the two cleaner bugs, because the tests fake Azure DI and Temporal and use synthetic dates
  and areas.
- The per-photo step scores live as it did offline. The room step does not, because the
  grouping differs (23.3); the text steps measure differently from their offline tasks (23.4,
  23.5).
- The waits are structural: SAM 3 under Replicate's throttle is 74% of the wall time, and the
  appraisal gate waits for it although it reviews only the extraction (done at +94 s). An
  extraction failure is noticed only after the photo stage and fails the whole study, which
  then cannot be restarted.
- Not verified here: Azure DI's cost (not recorded by the pipeline), a deployment on Cloud Run,
  repeats of the live run, and a second client package.

Spend for this section: about $15.8 (Anthropic $14.04: run 3 $4.84, run 2 $5.80, the failed
run 1 $2.55, the grouping runs and the extraction recheck $0.85; OpenAI $0.78 (run 3);
Replicate $0.38; Azure DI about $0.60 at list price, not recorded).

Commands (from `backEnd/`; the stack and settings as in the evidence README; the predictions
and labels are private):

```bash
uv run python ../docs/review_2026-10-15/repro/live_client.py OUT
uv run python ../docs/review_2026-10-15/repro/live_export.py OUT/db_final OUT/documents.json \
    ~/basis-eval-data/predictions/live_e2e_v070
BASIS_EVAL_DATA_DIR=~/basis-eval-data/work/labels_pre_2026-10-16 uv run python -m evals.run \
    --predictions ~/basis-eval-data/predictions/live_e2e_v070 --no-db
python -m evals.predict_rooms --photos ~/basis-eval-data/inputs/photos \
    --out ~/basis-eval-data/predictions/live_e2e_v070_rooms_filename \
    --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    --per-photo ~/basis-eval-data/predictions/live_e2e_v070_perphoto --repeats 1 \
    --room-prompt v3 --model claude-sonnet-5-5 --reasoning-effort low \
    --single-photo-rooms per-photo --materials sensitive
```
## 24. Over-long `asset_class`: keep the class code (2026-10-16)

Follow-up to 17.2 and 18. Branch `claude/asset-class-code`.

What the field is. `Classification.asset_class` is the Rev. Proc. 87-56 class the model may
name (`57.0`, `00.3`, `00.11`, the form the rules table uses). It reaches
`ClassificationResult` and the eval output only: the `assets` table has no such column, the
cache of approved assets writes `None`, and no validation or review rule reads it. So no
column width constrains it, and the 16-character limit is the schema's own.

What the 43 failures were. In the 17.2 `run.json` files, the values that failed the limit
were `00.3 Land Improvements` (21), `57.0 Distributive Trades and Services` (10), `00.11
Office Furniture, Fixtures, and Equipment` (3), `00.11 Office Furniture, ... and Equipment - 7
Year` (2), `Nonresidential Real Property - 39 Year` (2), `Millwork - General` (2), `Building
or Building Component` (1), `00.3 Land Improvement` (1) and `00.3 Land Improvement – 15
Years` (1). 38 of 43 start with the class code; every accepted answer in 17.2 was a bare code
or null.

Change. Section 18 clipped any value over 16 characters (`00.3 Land Imp...`). Now a value
that starts with a class code (two digits, a dot, one or two digits) keeps the code alone, at
any length, so the field has one form; other text over 16 characters is still clipped. No
review flag is added: the field is not stored or used in a decision, and a flag would add
reviewer work for nothing. `tests/unit/classify/test_asset_class.py` has every distinct
failing value as a case.

Measurement: `evals.predict_assets --model gpt-6-luna --reasoning-effort none --max-tokens
6000 --repeats 3`, the 17.2 settings, OpenAI direct (Azure unset; `models_reported` is
gpt-6-luna only).

| gpt-6-luna, none | Bucket right, repeat 1 | mean of 3 | pass^3 | macro-F1, repeat 1 | citations verified | rows with a verified citation | needs review | loop failures (of 138) | provider errors | spend (3 repeats) | seconds per row |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 17.2 (no clip) | 0.609 | 0.630 | 0.587 | 0.449 | 0.873 | 0.891 | 0.57 | 6 | 6 | $0.156 | 6.2 |
| this section | 0.630 | 0.659 | 0.587 | 0.483 | 0.883 | 0.935 | 0.54 | **0** | 0 | $0.153 | 6.7 |

Reading: the loop failures go from 6 to 0, as expected. The mean of 3 moves to 0.659, the
17.2 "answered loops only" figure, which is what removing the y39 fallback rows predicts. One
run of 46 rows; the 95% interval on bucket accuracy is [0.49, 0.75], so the accuracy change is
not a measured improvement. The answered `asset_class` values were `00.3` (16), `57.0` (12),
`00.11` (3), `37.11` (1) and null (106); how many came through the code rule is not
recorded, since the raw model text is not saved. gpt-5.2 (30 failures) and gpt-5.4-mini (7)
were not re-run.

## 25. Retrieval follow-ups: embedding window and the hybrid in classification (2026-10-16)

Section 22 left two findings: 79% of the chunks are longer than MiniLM's input window, so
the vector side never sees most chunk tails, and production does not use the hybrid at
all (the classifier's `search_reference` tool and `resolve_rule_citations` call
`store.search_bm25`). This section measures both fixes.

**What the literature leads one to expect.** A bi-encoder truncates at its window, so text
past it is not embedded at all: sentence-transformers truncates at `max_seq_length` (256
word pieces for all-MiniLM-L6-v2, which was trained on 128). The two standard fixes are
chunks that fit the window and a model with a longer one. The small CPU models with a
512-token window and 384-d vectors (so the `chunks.embedding vector(384)` column stays)
are bge-small-en-v1.5 (33M parameters; MTEB retrieval average 51.7 on its model card),
gte-small (49.5) and e5-small-v2 (49.0) on the same table; MiniLM-L6 is not on it. Each is trained with its
own input format: e5 needs `query: ` and `passage: ` prefixes (its card says omitting them
degrades results), bge-v1.5 recommends an instruction on short queries only, gte uses none.
Late chunking (Günther et al., 2024) needs a long-context model (8k tokens) and is out of
scope for a CPU embedder. Gains from a better embedder on BEIR-style benchmarks are a few
points of nDCG@10, and section 22 found that 50 queries detect about 0.07 reliably.

**Method.** The section 22 harness and statistics, unchanged (50 dev queries, 173
judgments at doc + page, top 50 chunks read as pages cut at 10, paired percentile bootstrap
with 10,000 resamples and sign-flip randomization p). Variants:

* (a) MiniLM with 200-token chunks and 40 overlap (`RETRIEVAL_CHUNK_TOKENS=200`,
  `RETRIEVAL_CHUNK_OVERLAP=40`, new settings);
* (b) bge-small-en-v1.5 (with its query instruction, and without), e5-small-v2 (with its
  prefixes) and gte-small, on the current 400 / 80 chunks;
* (c) each in the hybrid (RRF, k = 60) and in weighted fusion at 0.3 (section 22's best).

The embedder now carries the model's prefixes (`basis.retrieval.embed.MODEL_SPECS`;
`Embedder.embed` for stored text, the new `Embedder.embed_query` for queries; the hybrid
embeds queries with `embed_query`). The variants were run with a scratch driver on the
`evals.retrieval` index and `hybrid_search`; the default, e5 and 200 / 40 rows were re-run
through `python -m evals.run --corpus-text parser --retrieval-systems
bm25_pages,bm25_chunks,vector_chunks,hybrid` with the settings (`EMBEDDING_MODEL`,
`RETRIEVAL_CHUNK_*`) and gave the same numbers to the third decimal (current 0.3835 /
0.4501, e5 0.4267 / 0.4653, MiniLM 200 / 40 0.3978 / 0.4438, vector / hybrid nDCG@10). Timings are one run each on an Apple M4 Pro (12 cores), CPU, batch 32, with the
machine otherwise idle.

### 25.1 Embedding window

How much of each chunk the embedder sees (word pieces of the BERT uncased vocabulary,
which all five models share, special tokens and the passage prefix included):

| Chunks | Embedder window | Chunks | Longer than the window | Median / p90 / max word pieces |
|---|---|---|---|---|
| 400 / 80 | 256 (MiniLM) | 7,466 | 5,885 (79%) | 328 / 407 / 2,503 |
| 200 / 40 | 256 (MiniLM) | 14,204 | 160 (1.1%; 98 text, 62 table surrogates) | 169 / 207 / 1,587 |
| 400 / 80 | 512 (bge, e5, gte) | 7,466 | 60 (0.8%; 36 text, 24 table surrogates) | 328 / 407 / 2,503 |

The residue longer than the window is chunks whose characters cost cl100k far fewer tokens
than word pieces (dot leaders, long codes, OCR runs) and table surrogates whose single row
exceeds the budget (rows are never split, section 20).

Vector-only and hybrid (nDCG@10 standard error in brackets; holes = share of the top-10
pages with no judgment for that query):

| Variant | System | nDCG@10 | recall@5 | recall@10 | MRR | holes@10 |
|---|---|---|---|---|---|---|
| MiniLM, 400 / 80 (current) | vector | 0.383 (0.041) | 0.338 | 0.475 | 0.464 | 0.830 |
| | hybrid | 0.450 (0.038) | 0.379 | 0.571 | 0.539 | 0.800 |
| MiniLM, 200 / 40 | vector | 0.398 (0.041) | 0.385 | 0.492 | 0.491 | 0.826 |
| | hybrid | 0.444 (0.039) | 0.404 | 0.538 | 0.558 | 0.808 |
| bge-small, 400 / 80, query instruction | vector | 0.408 (0.044) | 0.393 | 0.483 | 0.484 | 0.830 |
| | hybrid | 0.443 (0.042) | 0.399 | 0.561 | 0.508 | 0.804 |
| bge-small, 400 / 80, no instruction | vector | 0.392 (0.043) | 0.357 | 0.459 | 0.472 | 0.842 |
| | hybrid | 0.439 (0.043) | 0.406 | 0.540 | 0.517 | 0.814 |
| **e5-small-v2, 400 / 80** | vector | **0.427** (0.042) | 0.390 | 0.503 | 0.518 | 0.826 |
| | hybrid | **0.465** (0.041) | **0.424** | **0.589** | 0.537 | 0.796 |
| gte-small, 400 / 80 | vector | 0.426 (0.044) | 0.372 | 0.497 | 0.511 | 0.826 |
| | hybrid | 0.458 (0.040) | 0.396 | 0.581 | 0.539 | 0.798 |
| BM25 chunks, 400 / 80 (for reference) | - | 0.415 (0.042) | 0.368 | 0.549 | 0.508 | 0.808 |
| BM25 chunks, 200 / 40 | - | 0.410 (0.042) | 0.358 | 0.543 | 0.491 | 0.812 |

The vector row of the current configuration is 0.383 here and 0.384 in section 22: the
same rankings (0.3835, rounded either way). Weighted fusion at 0.3 gives 0.469 (current), 0.466,
0.467, 0.449, 0.462 and 0.453 in table order: no variant moves it either.

**Paired differences** (variant minus the current MiniLM 400 / 80, per query):

| Difference | Metric | Mean | 95% interval | p | wins / losses / ties |
|---|---|---|---|---|---|
| MiniLM 200 / 40, vector | nDCG@10 | +0.014 | [-0.026, +0.056] | 0.50 | 20 / 20 / 10 |
| MiniLM 200 / 40, hybrid | nDCG@10 | -0.006 | [-0.046, +0.034] | 0.76 | 19 / 21 / 10 |
| MiniLM 200 / 40, hybrid | recall@10 | -0.033 | [-0.099, +0.032] | 0.33 | 8 / 11 / 31 |
| bge-small, vector | nDCG@10 | +0.025 | [-0.038, +0.088] | 0.45 | 22 / 18 / 10 |
| bge-small, hybrid | nDCG@10 | -0.007 | [-0.046, +0.031] | 0.71 | 23 / 18 / 9 |
| bge-small, no instruction, vector | nDCG@10 | +0.009 | [-0.048, +0.066] | 0.77 | 21 / 19 / 10 |
| e5-small-v2, vector | nDCG@10 | +0.043 | [-0.013, +0.097] | 0.13 | 29 / 15 / 6 |
| e5-small-v2, vector | MRR | +0.055 | [-0.035, +0.145] | 0.25 | 16 / 13 / 21 |
| **e5-small-v2, hybrid** | nDCG@10 | **+0.015** | [-0.020, +0.048] | 0.40 | 27 / 15 / 8 |
| e5-small-v2, hybrid | recall@10 | +0.018 | [-0.032, +0.069] | 0.50 | 11 / 7 / 32 |
| e5-small-v2, hybrid | MRR | -0.001 | [-0.070, +0.063] | 0.97 | 12 / 9 / 29 |
| gte-small, vector | nDCG@10 | +0.042 | [-0.014, +0.101] | 0.16 | 23 / 17 / 10 |
| gte-small, hybrid | nDCG@10 | +0.008 | [-0.029, +0.043] | 0.69 | 24 / 19 / 7 |
| e5-small-v2: hybrid - its BM25 | nDCG@10 | +0.050 | [-0.004, +0.105] | 0.086 | 26 / 16 / 8 |

**Condensed lists** (Sakai 2007, as in section 22: unjudged pages removed before cutting
at 10), nDCG@10 vector / hybrid: current 0.764 / **0.832**, MiniLM 200 / 40 0.783 / 0.817,
bge 0.790 / 0.826, bge without instruction 0.733 / 0.811, e5 0.801 / 0.826, gte 0.763 /
0.810 (BM25 0.808). Under this treatment the current hybrid is the best of all, and e5's
hybrid minus it is -0.006 [-0.039, +0.023].

**Cost** (one corpus embedding, 7,466 or 14,204 chunks; one query embedding; vectors are
`vector(384)`, 4 x 384 + 8 = 1,544 bytes each, so the dimension and the Alembic column do
not change):

| Variant | Corpus embedding | Per query | Vector storage (heap, before the HNSW index) |
|---|---|---|---|
| MiniLM, 400 / 80 (current) | 40 s | 4.0 ms | 11.5 MB |
| MiniLM, 200 / 40 | 52 s | 3.6 ms | 21.9 MB (and twice the chunks for BM25 and HNSW) |
| bge-small, 400 / 80 | 130 s | 6.6 ms | 11.5 MB |
| e5-small-v2, 400 / 80 | 112 s | 6.7 ms | 11.5 MB |
| gte-small, 400 / 80 | 1,121 s | 9.4 ms | 11.5 MB |

gte-small's corpus time, ten times bge's on the same architecture size, was measured twice
(1,183 s under load, 1,121 s idle); the cause was not investigated.

**Reading.**

* Embedding whole chunks helps the vector side, as expected, but by less than 50 queries
  can confirm: the best, e5-small-v2, adds +0.043 nDCG@10 alone (p = 0.13, better on 29
  queries, worse on 15), gte +0.042, bge +0.025, smaller chunks for MiniLM +0.014. The
  model's input format matters: bge without its query instruction loses most of its gain
  (+0.009).
* In the hybrid the gains mostly vanish: e5 +0.015 [-0.020, +0.048], gte +0.008, bge
  -0.007, MiniLM 200 / 40 -0.006. BM25 already finds most of what the better vectors add,
  and on condensed lists the current hybrid stays the best (e5 -0.006).
* Smaller chunks are not free: twice the rows, BM25 over shorter documents is slightly
  worse (0.410 against 0.415), and the hybrid's recall@10 drops 0.033.

**Decision: keep the defaults** (MiniLM, 400 / 80). The rule was to change them only if a
variant beat the current hybrid outside the noise; none does, under either treatment of
unjudged pages, and the best candidate costs 2.8 times the embedding time and a
re-embedding of every corpus. The window and the embedder are now settings
(`RETRIEVAL_CHUNK_TOKENS`, `RETRIEVAL_CHUNK_OVERLAP`, `EMBEDDING_MODEL` with
`MODEL_SPECS` prefixes), so e5-small-v2 is one setting and a `--fresh` re-ingest away if
more judgments (section 22's recommendation 5) show its +0.015 to be real. The
real-corpus test's pins (`CHUNKER_VERSION = cl100k_base-400/80-page-v1`, 7,466 chunks,
384-d) therefore stand; the test now also checks the ingest summary's chunker version and
removes `RETRIEVAL_CHUNK_*` from the subprocess environment, as it already removed
`EMBEDDING_MODEL`.

### 25.2 The hybrid in classification

`RETRIEVAL_SEARCH` (`bm25` | `hybrid`, default `bm25`) chooses what the classifier searches
with. `Retrieval.classifier_search(store)` gives `None` for `bm25` (the classifier then
calls `store.search_bm25` itself, exactly as before) and `Retrieval.hybrid` bound to the
store for `hybrid`; `process_assets` passes it as `ClassifyContext.search`, which both the
`search_reference` tool and `resolve_rule_citations` already used when set.

**Method.** `evals.predict_assets` gained `--store parser` (the production parse and chunk
at the configured window, MiniLM embeddings, the store's `Bm25Index`, in the
`evals.retrieval` index) and `--search bm25 | hybrid` (the same `classifier_search`
production binds). Both arms: claude-sonnet-5-5 at effort low, 6,000 output tokens, the 46
`assets` rows, three repeats, the four IRS PDFs (`IRS_*.pdf`, 1,258 chunks, as in section
17), everything else equal. Section 17's run (the historical pymupdf store, same model) is
shown for reference; it was not re-run. Per-row paired bootstrap over the 46 rows of the
mean over three repeats.

| Arm | Bucket right, mean of 3 | repeat 1 | pass^3 | citations verified, 3 repeats (repeat 1) | rows with a verified citation, 3 repeats (repeat 1) | needs review | searches per row | loop failures (of 138) | cost per study | seconds per row |
|---|---|---|---|---|---|---|---|---|---|---|
| `--store parser --search bm25` | 0.652 | 0.652 | 0.609 | 0.989, 176 / 178 (1.000, 57) | 0.986 (0.978) | 0.457 | 1.12 | 0 | $1.209 | 5.4 |
| `--store parser --search hybrid` | 0.652 | 0.652 | 0.609 | 0.995, 182 / 183 (1.000, 62) | 1.000 (1.000) | 0.435 | 1.08 | 0 | $1.139 | 5.2 |
| section 17 (pymupdf store, BM25) | 0.688 | 0.674 | 0.630 | 0.970, 164 / 169 (0.964, 55) | 0.957 (0.957) | 0.464 | 1.07 | 0 | $1.247 | 5.9 |

The repeat-1 figures are what `evals.run --predictions` reports (`citation_validity`,
`rows_with_verified_citation`); the three-repeat figures are over every repeat.

| Difference (per row, mean of 3 repeats) | Mean | 95% interval | p | wins / losses / ties |
|---|---|---|---|---|
| hybrid - bm25, bucket right | +0.000 | [-0.022, +0.022] | 1.00 | 1 / 1 / 44 |
| hybrid - bm25, has a verified citation | +0.014 | [+0.000, +0.043] | 1.00 | 1 / 0 / 45 |
| hybrid - bm25, citation validity | +0.004 | [-0.007, +0.019] | 0.75 | 2 / 1 / 43 |
| hybrid - bm25, needs review | -0.022 | [-0.094, +0.051] | 0.71 | 4 / 7 / 35 |
| section 17 - bm25 (parser store), bucket right | +0.036 | [+0.000, +0.080] | 0.19 | 5 / 1 / 40 |

Per bucket (mean of 3), y39 / y15 / y5: bm25 1.00 / 0.83 / 0.19, hybrid 1.00 / 0.83 / 0.17.

**Rules-table citations through each search.** `evals.run` now scores `rule_citations`
through `resolve_rule_citations` itself over the chunk systems' index (all six PDFs, as
the real-corpus test ingests them), once with each classifier search (`resolve_bm25`,
`resolve_hybrid`):

| Chunks and embedder | BM25 | Hybrid (RRF) | Lost by the hybrid |
|---|---|---|---|
| MiniLM, 400 / 80 (current) | 77 / 77 | 74 / 77 (0.961) | mirror (ATG p317), tile (p306), pool (p298) |
| e5-small-v2, 400 / 80 | 77 / 77 | 73 / 77 (0.948) | the same three and water_heater (p315) |
| MiniLM, 200 / 40 | 77 / 77 | 75 / 77 (0.974) | doorbell (p301), pool (p298) |

This is a real cost of the hybrid, and the mechanism is RRF's. The resolver asks for 10
hits (`RULE_RESOLVE_K`) and keeps the first on the cited page that contains the excerpt
verbatim; an exact excerpt is a lexical query, and BM25 ranks the right chunk first for
pool and sixth or seventh for the other two. RRF scores a chunk at `1/(60 + rank)` summed
over the lists that hold it (rank 1-based), so any chunk both retrievers place moderately
high beats a chunk only BM25 finds, even BM25's first (1/61): for pool all ten fused slots
went to chunks in both lists. For all three current losses the right chunk is not in
MiniLM's top 50 at all.

**Reading.** The search does not change the answers: 44 of the 46 rows have the same
share of right buckets over the three repeats, one row is better with each search. The
hybrid's citations verify a little more often (every row has a verified citation in every
repeat, against 0.986), which is the direction section 22 predicts, but the difference is
one row. The classifier's errors are
elsewhere: y5 rows (personal property) are right 0.17 to 0.22 of the time with either
search and either store. The row carries only the component name and the property class
(section 17), and better passages did not move those rows here. The parser store against section 17's pymupdf store (+0.036 for the old
run, p = 0.19) is a different day's samples of the same model on a different store and is
inside the noise too.

**Decision: keep `RETRIEVAL_SEARCH=bm25`.** The rule was to switch if the hybrid won. On
the product measure it ties (bucket accuracy identical, citation measures +0.004 to +0.014,
inside the noise), and for rules-table citations it is worse: 3 of 77 no longer resolve,
and each would flag its rule's assets `citation_unverified` for review. Section 22's
recommendation 3 is done (the wiring exists and is measured); it stays off. If the hybrid
is turned on later for the model's `search_reference` tool, `resolve_rule_citations`
should keep BM25 (exact excerpt lookup is BM25's job), or fuse over the union of both
candidate lists before the page and verbatim filter; that split is a follow-up, not done
here, and the setting's comment says so. The cheaper next steps for classification
accuracy are the y5 rows, not retrieval.

### 25.3 What 50 queries and 46 rows can show

The largest retrieval effect here, e5's vector-only +0.043, has p = 0.13; section 22's
power estimate (differences of about 0.07 are reliably detected) holds, and the hybrid
differences (0.006 to 0.015) would need several hundred queries. The qrels pool is still
legacy BM25 and vector candidates, about 80% of each top 10 is unjudged, and the vector
variants surface different unjudged pages, which favours systems like the pool's sources.
On the assets eval 44 of the 46 rows had the same per-row score under both searches, so
a larger row set, not more repeats, is what would separate them.

**Real-corpus test.** Before merging main, on this branch: 7,466 chunks with the pinned
per-document counts, 77 of 77 rules-table citations, two-column sentence rates 0.923 and
0.892, BM25 identical to the store on 50 of 50 queries. Its HNSW check at the production
`ef_search` failed once (43 of 50 against the 45 required) and passed on the next run
(50); on PR 42's own commit it also gave 43. That flake is the one PR 45 fixed (the check
now compares at `hnsw.ef_search = 1000` and records the production-setting agreement
separately); after merging main the test passes 6 of 6: the same counts, citations and sentence rates,
near-exact vector and hybrid agreement 50 of 50, and 43 of 50 at the production setting
(recorded, not asserted).

## 26. Vision architecture: alternatives measured (2026-10-16)

Question (Bryan): do not be biased toward the pipeline we have. Find how the problem "a
property's photos to the engineer's component schedule" is solved elsewhere and test the
credible alternatives on the real study; the target is the vision output closest to the
engineer's report, as the best automatic first pass for the engineer to edit. The survey
behind the experiments, with sources, is `VISION_ARCHITECTURE_SURVEY.md` in this folder. Its
short version: no insurance, real-estate or cost-segregation vendor publishes count or
material accuracy from ordinary photos, and all of them keep a human reviewer; trained
indoor segmenters (ADE20K) know about a third of our countable classes and no materials;
SAM 3 leads every open-vocabulary detector on the published benchmarks; cross-view counting
by vision-language models is a known weak spot, but small image groups are its least
degraded regime; one long call tends to lose recall; more thinking does not reliably help
perception.

Current pipeline (the baseline throughout): per photo, `photo_countables/v3` and
`photo_surfaces/v2` on Opus 5.5, rooms from file names and aliases, the Sonnet 5.5 room step
of section 16.5 (`rooms19_v3_opus55`, 37 of 38 rows); for per-photo counts, the per-class
fusion 4c of section 15 (sol low + Opus medium, count MAE 0.124). Three repeats everywhere a
model is called; detectors are deterministic, so one pass. Nothing is fitted except the
detectors' phrases and thresholds, which are chosen leave one photo out exactly as SAM 3's
were (section 19).

### 26.1 How closeness to the engineer's report is scored

`report_rows` (section 13) is at its ceiling for most systems here (37 of 38 photo-derivable
rows; the miss, Door & Window Guards, is not visible in any photo), so it cannot separate
them. What the engineer's report provides: a cost per row (46 rows) and a recovery period;
no quantities (no units, linear feet or square feet per row; pages 4 to 12 are the photos).
So a quantity metric against the report itself is not possible. The new scorer
`evals.score_schedule` measures what can be measured:

- **Row set**: recall over the 38 photo-derivable rows, and precision against a schedule
  template of the engineer's 46 row names plus 20 *distractor* rows the engineer did not use
  (countertops, mirrors, ceiling fans, decks, vinyl flooring, pool, elevators...; private file
  `work/schedule_template_v1.json`). A room-inventory system lists a row when its
  `report_rows` rule is met (a distractor: when one of its labels is present); a schedule
  system lists the rows it writes. Even the human inventory lists 5 distractors (precision
  0.88): the engineer folds ceiling fans into Electrical, countertops into millwork.
- **Row quantities**: for the 17 rows that need a count, the reference is the hand-labelled
  room inventory put through the same rule (distinct objects of the row's labels in the rooms
  the rule allows). Reported: MAE, WAPE (sum |error| / sum reference) and the cost-weighted
  relative error (each row's |error| / reference, capped at 1, weighted by the row's share of
  the study's cost: the share of the engineer's dollars on count rows a quantity would misstate
  if unit costs were uniform within a row).
- **Room counts over every repeat**: `evals.run` scores the room inventory on repeat 1 only;
  the scorer averages the per-(room, label) error over all three repeats and compares systems
  by a paired bootstrap over the 33 rooms (5,000 resamples).
- Materials: a per-surface material accuracy against the materials written in the per-photo
  labels (`evals.predict_surface_crops score`, 58 labelled photo surfaces: 32 floors, 12
  walls, 3 wainscot bands, 11 ceilings).

### 26.2 Trained segmentation and open-vocabulary detectors (experiments a and b)

`evals.predict_detector` runs each model locally through `transformers` (safetensors, no
remote code) and writes SAM 3's pool format; `evals.sam3_select` then picks phrases and a
count threshold per class leaving one photo out. The segmenters' candidate phrases are their
own ADE20K class names (`--candidates observed`; `basis.vision.ade20k` maps 39 classes, with
door and light classes split interior/exterior by the share of sky and outdoor classes in the
photo); the detectors are prompted with SAM 3's phrase pool, one phrase per pass.

| System (191 verified boxes, 52 photos) | mAP@0.5 (LOO) | Count MAE, per-photo yardstick | Diff vs 4c [95% CI] | Count P / R | Label F1 (photo) | s / photo | Cost |
|---|---|---|---|---|---|---|---|
| **Current best counts (4c, VLM)** | n/a | **0.124** | 0 | 0.92 / 0.87 | 0.73 | 11 | $1.87 / 52 |
| SAM 3 v3, per class LOO (section 19) | 0.763 | 0.260 | +0.136 [+0.062, +0.225] | 0.77 / 0.82 | n/a | 3.2 | ~$0.15 |
| Mask2Former Swin-L, ADE20K panoptic | 0.219 | 0.542 | +0.418 [+0.287, +0.567] | 0.60 / 0.36 | 0.39 | 0.15 (MPS) | $0 local |
| OneFormer Swin-L, ADE20K panoptic | 0.189 | 0.526 | +0.402 [+0.268, +0.556] | 0.62 / 0.36 | 0.32 | 1.3 (CPU) | $0 local |
| OWLv2-L (SAM 3 phrase pool) | 0.663 | 0.407 | +0.283 [+0.187, +0.392] | 0.67 / 0.65 | n/a | 1.7 (MPS) | $0 local |
| Grounding DINO-B (SAM 3 phrase pool) | 0.439 | 0.452 | +0.328 [+0.204, +0.478] | 0.70 / 0.46 | n/a | 24 (MPS) | $0 local |
| LLMDet-B (SAM 3 phrase pool; 26.12) | 0.279 | 0.529 | +0.405 [+0.275, +0.565] | 0.66 / 0.28 | n/a | 22 (MPS) | $0 local |

No detector wins a class against the VLM counts (per class: OWLv2 ties on light fixtures,
0.12 MAE; OneFormer 0.15 there; every other class is worse by 0.2 to 0.9). The segmenters
never fire on outlets (not an ADE20K class) or exterior lights (outdoor fixtures are not
segmented as "light"), find under a third of the windows (exterior windows are absorbed into
"building" and "house"), and merge a run of cabinet doors into one "cabinet" segment, where
the engineer counts units. Their label sets (F1 0.32 to 0.39) cover only the classes ADE20K
has. SAM 3 stays the best detector on our photos, as on the published benchmarks.
The Swin-L checkpoints of LLMDet and MM-Grounding-DINO give vertically squashed boxes under
`transformers` 5.19 (26.12), so the LLMDet row is the Swin-B checkpoint.

### 26.3 Materials from segmentation-guided crops (experiment c)

`evals.predict_surface_crops`: Mask2Former's wall, floor and ceiling masks (at least 2% of
the photo), the surface cropped to its box, enlarged to 768 px, the rest blended to grey; one
call per surface with the whole photo first and the crop second, closed-choice prompt
`surface_crop_material/v1` (a wall also gets its lower band: wainscot or chair-rail
wallpaper). Three repeats on Sonnet 5.5 (low) and gpt-6.1-sol (low); 86 crops per repeat.

| Source | Floor hit (32) | Wall hit (12) | Wainscot (3) | Ceiling (11) | All hit / exact (58) | Answered | Diff vs per-photo [95% CI] | $ / study |
|---|---|---|---|---|---|---|---|---|
| Per-photo surfaces call, Opus (current) | 0.78 | 0.58 | 1.00 | 0.18 | 0.64 / 0.62 | 0.69 | 0 | (in the per-photo cost) |
| Crops, Sonnet low | 0.66 | 0.83 | 0.67 | 0.64 | 0.69 / 0.67 | 0.74 | +0.052 [-0.103, +0.207] | $0.58 |
| Crops, sol low | 0.69 | 0.78 | 0.67 | 0.64 | 0.70 / 0.68 | 0.79 | +0.057 [-0.098, +0.207] | $0.38 |
| **Crops, else per-photo (Sonnet)** | 0.78 | 0.83 | 1.00 | 0.73 | **0.79 / 0.78** | 0.88 | **+0.155 [+0.034, +0.276]** | $0.58 |
| Crops, else per-photo (sol) | 0.79 | 0.78 | 1.00 | 0.73 | 0.79 / 0.77 | 0.90 | +0.149 [+0.023, +0.276] | $0.38 |

The crops read walls and ceilings better (painted block, plank walls; the per-photo call
rarely lists a ceiling at all). Their floor loss is coverage, not reading: 10 of the 11
missed floors were never asked (the floor region was under 2% of the photo, or segmented as
"rug"), one was answered wrongly (hardwood read as carpet). Taking the crop's answer where a
crop exists and the per-photo answer elsewhere is the significant gain. On the report rows
nothing changes: put into the current room run (`arch26_rooms19_crops_sonnet`, 22 materials
changed) the rows stay 37 of 38; every material-dependent row was already evidenced. The
gain is in material accuracy per surface, which the engineer's area takeoff needs and the
rows cannot see on this study. Apple's DMS46 material model was not run (TorchScript, GPU-only
sample code, model licence not read, CC-BY-NC labels).

### 26.4 One call per room with all its photos (experiment d)

`evals.predict_multiview --scope room`: every photo of a room (file names and the aliases,
as the pipeline groups them; 33 rooms, 1 to 13 photos) in one call, no per-photo analysis;
the prompt (`room_photos_inventory/v2`) carries the counting rules of `room_inventory/v3` and
the label definitions of `photo_countables/v3` and `photo_surfaces/v2`. The first version
(v1) copied `room_inventory/v3`'s outdated rule that files furniture and bath accessories as
`other` and lost FF&E and Bathroom Accessories; it is kept in the table as a loss.

Per label (absolute room-count error per repeat, summed over rooms; gold totals in brackets),
Opus high against the current pipeline: windows [50] 10.0 against 17.0, wall finishes [9] 1.3
against 8.0 (the current per-photo call lists plain painted walls), kitchen cabinet units [23]
0.3 against 5.0, light fixtures [15] 1.3 against 5.7, bath accessories [9] 1.3 against 5.0,
mirrors 1.7 against 3.0; worse on baseboards (3.3 against 1.0), outlets (4.0 against 3.0),
interior doors (5.3 against 4.3) and the exterior site items (retaining wall, sidewalk) of
the 13-photo exterior group, which the call drops in some repeats. On objects alone (surfaces
and site work left out) the room count MAE is 0.230 against 0.360 (paired over rooms
-0.085 [-0.153, -0.020]); at medium effort 0.288 (-0.054 [-0.112, +0.007]).

What does not improve: the per-row totals on the rows the engineer prices. The WAPE gain
(0.175 to 0.086) is almost all FF&E, a low-cost row: the current pipeline counts 40 loose
furniture pieces against 68 labelled, the room call 70 to 74. Without FF&E both sit near 0.10,
and the cost-weighted error is 0.062 to 0.079 against 0.064. Per-room errors of opposite sign
cancel in a whole-house total (a window counted in the wrong room is still one window).

### 26.5 The whole property in one call, and the schedule in one call (experiment d)

`--scope house`: all 52 photos, captioned with their room, and the room list in one call
(`house_inventory/v2`), returning every room's inventory. `--scope schedule`: all 52 photos
and the schedule template in one call (`house_schedule/v2`), returning rows with recovery
period, quantity, unit and evidence images. Both are worse than one call per room: the
property call under-lists (room label recall 0.83 to 0.85; on sol it misses four rows) and
the schedule call misstates quantities badly (cost-weighted error 0.42 to 0.50 against
0.064), lists 1.7 to 2.7 rows photos cannot establish (Foundations-type rows) and misses three
to four photo-derivable rows. Its recovery periods agree with the engineer's on 75 to 80% of
the rows it lists. This is what the survey predicted (recall falls when everything is asked
at once; MM-NIAH, extraction studies), and it rules out "photos straight to schedule" as the
first pass. The house calls needed a 600 s request timeout (the default 60 s timed out every
property-wide Opus call; the first attempts were stopped and are not in any table).

### 26.6 Reasoning effort (experiment e)

Opus 5.5 at high effort on the countables prompt was already measured (section 15, 1d: 0.144
against 0.150 at medium, alone; no gain inside the fusion, 4d). New:

- `photo_countables/v2` at **max** effort (output budget 4,000 + 4,096 thinking headroom):
  **untestable at the current output budget, and not measured.** Corrected after the fact
  (26.12): the account ran out of credit during the run's first repeat, so 294 of its 306
  attempts were rejected by the API ("credit balance is too low", HTTP 400) and only 12 reached
  the model: 7 answered, 5 were cut off at the output limit. The 0.583 it scores is the
  rejections, not max effort; the earlier reading of this run ("149 of 156 truncated") was
  wrong. Five truncations in twelve still say max effort does not reliably finish a per-photo
  count inside the pipeline's budget (Anthropic's effort documentation warns that max can
  overthink structured output), so it is not re-run.
- The room call at **high** effort (the experiment the survey suggests: cross-view
  de-duplication is where thinking might help): room count MAE 0.236 against 0.269 at medium
  and 0.350 for the current pipeline (-0.112 [-0.187, -0.048] against the current), label F1
  0.92, for $1.35 per study against $1.18. Report rows 36 of 38 (pass^3 0.95).

### 26.7 Combinations (experiment f)

- **Room calls for the interior, the current pipeline for the exterior group** (`x_` rooms:
  the 13-photo property group, the shed, the mechanicals; `evals.combine_rooms --exterior`).
  The rule came from looking at the room calls' misses on this study (exterior site items), so
  it is in-sample. High effort: rows 37 of 38 (the ceiling), room count MAE 0.277 (-0.067
  [-0.110, -0.023]), objects 0.271 (-0.062 [-0.113, -0.006]), WAPE 0.090, cost-weighted 0.085.
  Medium: 0.284 (-0.061 [-0.107, -0.010]).
- Crop materials on top of the room calls (`arch26_room_v2_opus55_med_crops`): rows unchanged.

### 26.8 Every architecture against the current pipeline

Counts = per-photo count MAE on the 191 verified objects (section 15 yardstick; n/a where the
architecture has no per-photo counts). Room counts = room-inventory count MAE over three
repeats, with the paired difference over rooms. Rows = report rows evidenced of 38 (repeat
1) and pass^3. Quantities = WAPE and cost-weighted relative error of the 17 count rows
against the labelled inventory. Cost per study (52 photos, one prediction) for the vision
step it replaces; seconds = wall time of one study's calls in this harness (concurrency 4).

| Architecture | Counts (photo) | Label F1 (room) | Room counts [Diff, 95% CI] | Rows (pass^3) | Row precision | Qty WAPE / cost-weighted | Materials hit | $ / study | Wall s |
|---|---|---|---|---|---|---|---|---|---|
| **Current pipeline** (per photo Opus + room step; 4c counts) | **0.124** | 0.900 | 0.350 | **37 (0.97)** | 0.86 | 0.175 / **0.064** | 0.64 | ~$2.65 (+$1.87 for 4c counts) | not re-measured |
| a. ADE20K segmenter as counter (best: OneFormer) | 0.526 | n/a | n/a | n/a | n/a | n/a | n/a | $0 | 68 |
| b. Open-vocabulary detector (best: OWLv2) | 0.407 | n/a | n/a | n/a | n/a | n/a | n/a | $0 | 88 |
| c. Crop materials, else per-photo, on the current rooms | 0.124 | 0.900 | 0.350 | 37 (0.97) | 0.86 | 0.175 / 0.064 | **0.79** | +$0.38 to $0.58 | +50 to 80 |
| d1. Room call v1, Opus medium (old furniture rule) | n/a | 0.821 | 0.420 [+0.114: +0.066, +0.163] | 36 (0.92) | 0.87 | 0.335 / 0.081 | n/a | $1.03 | 63 |
| d2. Room call v2, Opus medium | n/a | 0.917 | 0.269 [-0.068: -0.115, -0.017] | 36 (0.92) | 0.90 | **0.086** / 0.062 | n/a | $1.18 | 79 |
| d3. Room call v2, sol low | n/a | 0.868 | 0.341 [-0.015: -0.079, +0.050] | 36 (0.95) | 0.89 | 0.096 / 0.111 | n/a | $0.39 | 115 |
| d4. Whole property, Opus medium | n/a | 0.888 | 0.335 [-0.032: -0.125, +0.049] | 36 (0.92) | 0.90 | 0.137 / 0.155 | n/a | $0.49 | 126 |
| d5. Whole property, sol low | n/a | 0.884 | 0.451 [+0.026: -0.058, +0.118] | 34 (0.89) | 0.88 | 0.199 / 0.210 | n/a | $0.17 | 189 |
| d6. Photos to schedule, Opus medium | n/a | n/a | n/a | 34.7 (avg) | 0.85 | 0.345 / 0.424 | n/a | $0.34 | 82 |
| d7. Photos to schedule, sol low | n/a | n/a | n/a | 34.3 (avg) | 0.85 | 0.608 / 0.501 | n/a | $0.10 | 80 |
| e1. Countables at max effort, Opus (untestable at the current output budget; see 26.12) | not measured (294/306 attempts rejected for credit, 5/12 truncated) | n/a | n/a | n/a | n/a | n/a | n/a | $0.57 recorded (real: 26.12) | not measured |
| **e2. Room call v2, Opus high** | n/a | **0.923** | **0.236 [-0.112: -0.187, -0.048]** | 36 (0.95) | 0.89 | 0.086 / 0.079 | n/a | $1.35 | 90 |
| **f. e2 inside, current pipeline outside** (in-sample rule) | n/a | 0.898 | 0.277 [-0.067: -0.110, -0.023] | **37 (0.97)** | 0.86 | 0.090 / 0.085 | n/a | ~$1.35 + exterior per-photo | ~90 |

Many systems are compared with one baseline on
one study, so a single interval that just excludes zero is weak evidence; e2's (-0.187 to
-0.048) and c's material gain are the ones that clear it comfortably.

### 26.9 Reading

1. **Per-photo-then-reconcile is not the best way to count a room.** Giving the model every
   photo of one room at once, with no per-photo answers to reconcile, counts the room's
   objects better (objects 0.230 against 0.360 at high effort) and costs half as much. The
   pixels, not lists from separate photos, are what de-duplicating needs; the reconciliation
   call of section 16 sees per-photo lists and inherits their conventions (the painted-wall
   listings, the furniture undercount).
2. **But a better room count is not yet a closer schedule.** On the rows the engineer prices,
   the per-row totals are as close as the current pipeline's (cost-weighted 0.06 to 0.08
   against 0.064); the visible gain is FF&E. Room calls also drop exterior site items in the
   big exterior group, which is why the interior-only combination is needed to keep 37 of 38
   rows.
3. **Bigger calls lose.** One call for the property, or for the schedule, under-lists and
   miscounts, and the schedule call invents rows photos cannot show. Small groups of images are
   the working regime.
4. **Trained detectors are not the missing piece.** Neither ADE20K segmenters nor
   open-vocabulary detectors approach the frontier VLMs' counts on any class, and SAM 3 remains
   the best detector, so it stays for highlights only.
5. **Materials improve with focus.** A crop of the segmented surface reads walls and ceilings
   better than the per-photo call; with the per-photo answer as fallback it is a significant
   gain (+0.155) at $0.4 to $0.6 per study, but it changes no report row on this study.
6. **Effort.** Medium to high helps the room call; max was not measurable (26.12).

### 26.10 Recommendation

**Change a stage, not the architecture.** Keep the per-photo calls (they give the per-photo
counts, label sets and room types the highlights and the review screen use, and the exterior
inventory), keep SAM 3 for masks only, keep the rules table. Replace the room step: for every
interior room (file-name grouping and aliases, as today), one Opus 5.5 call at high effort
with all the room's photos (`room_photos_inventory/v2`) produces the room inventory, instead
of the reconciliation call over per-photo lists; exterior and site groups keep today's
per-photo + room step. Add the crop material second look (Mask2Former surface masks, crop
answer where one exists, per-photo surface otherwise). Do not adopt the whole-property or
photos-to-schedule calls, the trained segmenters or the open-vocabulary detectors.

What this buys on this study: room object counts 0.27 against 0.36 (paired over rooms), row
coverage at the ceiling, surface materials 0.79 against 0.64. What it does not: the per-row
totals the engineer prices are as close as today's except FF&E. Before wiring it: the
interior/exterior split was chosen on this study; re-measure on the second client package
with the rule fixed in advance; settle the baseboard and outlet conventions where the room
call is worse; the pipeline owner wires it (the room step is `basis/vision/room_step.py`,
owned by the live-run follow-ups session).

### 26.11 Spend, data and commands

Spend recorded by the runners: $19.81 (Anthropic $16.61: room calls v1 $3.08, v2 medium
$3.55, v2 high $4.04, property call $1.47, schedule call $1.02, Sonnet crops $1.74, max-effort
countables $1.72; OpenAI $3.14: room $1.18, property $0.51, schedule $0.31, crops $1.14; a
smoke test $0.06; local detectors $0). Not in it: the first property and schedule attempts
stopped at the 60 s timeout (Opus, about six calls) and a stopped sol v1 room run. The
recorded total is the real spend: see 26.12 for the check.

Prediction directories under `$BASIS_EVAL_DATA_DIR/predictions` (private): detectors
`arch26_det_{mask2former,oneformer,owlv2,gdino,llmdet}` with `_select` (the leave-one-photo-out
choice, `detection_boxes.json`, `photo_counts.json`); crops `arch26_crops_m2f_{sonnet55,gpt61sol}_low`
(with `material_accuracy.json`); multi-image `arch26_{room_v1,room_v2,house_v2,schedule_v2}_*`,
`arch26_room_v2_opus55_high`; effort `arch26_countables_v2_opus55_max`; combinations
`arch26_combo_roomscope{,_high}_interior_current_exterior`, `arch26_rooms19_crops_sonnet`,
`arch26_room_v2_opus55_med_crops`; the scores `arch26_schedule_scores.{md,json}`. The
schedule template is `work/schedule_template_v1.json`.

Commands (from `backEnd/`):

```bash
P=~/basis-eval-data/predictions
uv run python -m evals.predict_detector --photos ~/basis-eval-data/inputs/photos \
    --out $P/arch26_det_oneformer --detector oneformer
uv run python -m evals.sam3_select select --pool $P/arch26_det_oneformer \
    --dataset ~/basis-eval-data/detection --out $P/arch26_det_oneformer_select \
    --candidates observed --grid-min 0.05
uv run python -m evals.predict_multiview --photos ~/basis-eval-data/inputs/photos \
    --out $P/arch26_room_v2_opus55_high --scope room --prompt v2 --model claude-opus-5-5 \
    --reasoning-effort high --repeats 3 --aliases ~/basis-eval-data/labels/room_aliases_v1.json
uv run python -m evals.predict_surface_crops predict --surfaces $P/arch26_det_mask2former \
    --photos ~/basis-eval-data/inputs/photos --out $P/arch26_crops_m2f_sonnet55_low \
    --model claude-sonnet-5-5 --reasoning-effort low --repeats 3
uv run python -m evals.predict_surface_crops score --data-dir ~/basis-eval-data \
    --per-photo opus=$P/vision_decomposed_v3_opus55 \
    --combine crops_else_photo=$P/arch26_crops_m2f_sonnet55_low,$P/vision_decomposed_v3_opus55
uv run python -m evals.combine_rooms --base $P/arch26_room_v2_opus55_high \
    --override $P/rooms19_v3_opus55 --exterior --out $P/arch26_combo_roomscope_high_interior_current_exterior
uv run python -m evals.score_schedule --data-dir ~/basis-eval-data \
    --template ~/basis-eval-data/work/schedule_template_v1.json \
    --baseline current=$P/rooms19_v3_opus55 --system room_high=$P/arch26_room_v2_opus55_high
```
(For the property-wide calls set `LLM_REQUEST_TIMEOUT=600`.)

### 26.12 Follow-up: the cost check, the max-effort run, LLMDet (2026-10-16, later)

**Cost recording is correct.** Once the account was topped up, one probe call reproduced a
truncated max-effort answer (`photo_countables/v2`, Opus 5.5, effort max, the kitchen photo,
answer budget 200 so the wire limit is 4,296): the API returned `stop_reason` max_tokens, a
single thinking block, and usage of 4,264 input and 4,296 output tokens (`output_tokens_details`:
4,296 thinking), and the recorder charged exactly that ($0.103). Truncated answers and their
thinking tokens are recorded and charged; nothing needs fixing there. (A comparison against the
Anthropic console was not possible from this session: the console needs Bryan's login.)

**What the max-effort run really was.** Its raw records show 294 of 306 attempts rejected with
HTTP 400 "Your credit balance is too low", 7 usable answers and 5 truncations; the mean time per
photo was 7 s, not the 200 s of the one photo first looked at. The account ran out during the
run's first repeat (the high-effort room run was going at the same time), and the run's
recorded $1.72 is the real cost of the 12 attempts that reached the model. Section 26.6 and the
26.8 table are corrected: max effort was not measured, and is not re-run.

**A different defect this exposed (not fixed here).** The Anthropic provider maps every 4xx
other than 429 to `ProviderBadResponse`, which the vision step treats as an unusable model
answer: it retries once and marks the photo `invalid_model_output` / `needs_review`. A billing
or authentication rejection is therefore reported as 156 bad photos instead of one account
error, in the harness and in production alike (`basis/llm/anthropic_provider.py`, `map_error`).
It belongs in its own small change: classify billing, authentication and permission errors as
a non-retryable provider failure that stops the run or the activity, with a test.
*Fixed in PR 53:* such rejections (and OpenAI's out-of-quota 429s, which were retried and
sent to Azure) are now `ProviderAccountError` (`basis/llm/errors.py`): never retried, never
sent to the other provider; the evaluation runners stop at the first one with
`run.json` `complete: false` (`evals/account_stop.py`), and a study fails at its stage with
the provider's reason until it is retried (workflow patch `provider-account-stop-v1`).

**LLMDet.** The LLMDet-L run (Swin-L, CPU, three minutes per photo) scored mAP 0.027, which is
not the model: its raw boxes are squashed vertically (on the kitchen photo a window predicted
0.34 to 0.38 high where the verified box is 0.23 to 0.46), already in `pred_boxes` before
post-processing, with the preprocessing identical to Grounding DINO's, no weight-loading
warnings, and the same squash from MM-Grounding-DINO-L, while LLMDet-B returns the right box
(score 0.68). So the two Swin-L checkpoints do not run correctly under `transformers` 5.19
here; the run is kept as `arch26_det_llmdet_large_invalid_boxes` and not scored. LLMDet-B
(54.3 LVIS minival AP against 56.6 for L) on MPS, 22 s per photo, selected per class leaving
one photo out like the others: mAP 0.279, count MAE 0.529 (+0.405 [+0.275, +0.565] against the
current best), below OWLv2 (0.663 / 0.407) and SAM 3 (0.763 / 0.260); it wins no class (best:
interior doors, AP 0.53). The conclusion of 26.2 stands: no open-vocabulary detector replaces
SAM 3 or the VLM counts on these photos.

Spend for this follow-up: the probe $0.10; no other Claude run was needed (every room-call and
crop-material run of section 26 had completed its three repeats); LLMDet-base local, $0.
## 27. Live-run follow-ups (2026-10-16)

Section 23 ran the client study through the running system and found six gaps the harness
could not see. Each is fixed in its own commit with tests (branch
`claude/quizzical-saha-e892c3`); this section gives before and after for each (section 26, the vision architecture study,
landed first).

| # | Gap (section 23) | Before | After (offline replays on the stored live outputs; live run in 27.8) |
|---|---|---|---|
| 1 | Rooms grouped by the model's room identity | 14 rooms of 33, count MAE 0.60 (live, defaults) | file name + the engineer's aliases, model identity only for names that say nothing: 33 rooms, count MAE **0.37** on the same live per-photo outputs (one repeat) |
| 2 | Extraction asked the model only for 6 critical fields | 23 right / 0 wrong / 52 not extracted of 75 | **72 right / 2 wrong / 1 not extracted** on every one of 3 repeats, 12 of 12 critical, $0.0024 per study |
| 3 | An extraction failure surfaced after the photo stage, failed the study, no restart | 17 min and $2.67 spent, study dead | failure recorded at once (no new photo work), status `failed`, `POST /v1/studies/{id}/retry` resumes the stage |
| 4 | The appraisal gate waited for SAM 3 | gate at +681 s after start (live run 3; extraction done at +87 s); SAM 3 was 74% of run 2's wall time | gate opens when the extraction is done; photos and SAM 3 continue behind it; the rooms gate waits for them |
| 5 | No read routes for fields, rooms, assets | the client read the database | `GET /fields`, `/rooms` (with inventories), `/assets`, paginated |
| 6 | 166 of 203 assets `COST_ROW_MISSING` (106 for that alone) | 186 of 203 need review | **24** `COST_ROW_MISSING` (22 unidentified `other`), **94** need review |

The client study was then run live again on the real defaults, through the running system
(27.8): it completed in 9 min 56 s from `start` for $6.75, with 33 rooms, 72 of 75 fields,
the appraisal gate open at +93 s while 39 photos were still being analyzed, and half the
assets needing review instead of 91%.

### 27.1 Rooms: file names first, the engineer's aliases, the model's identity as a fallback

Production now groups photos the way section 16 measured (`ROOM_GROUPING=filename`, the
default; `model` is the previous behaviour):

- `analyze_photo` puts a photo whose file name names a room (`is_room_hint`: a word of three
  or more letters that is not a camera, phone or app word, so `IMG_1234.JPG`,
  `PXL_20250101_120000.jpg`, a UUID or a bare `photo 2` are not) in the room `group_photos`
  gives it among the study's photo names, after the study's room aliases. The room is keyed
  by its canonical file-name room id (`rooms.group_key`, migration 0005) under an advisory
  lock, so concurrent photos of one room share it.
- A photo whose name says nothing falls back to the model's room identity, as before.
- `reconcile_rooms` re-votes each file-name room's type and floor from its photos' stored
  answers with `group_photos`'s rules (ambiguous abstains, a floor in the file name wins), keeps a
  room the reviewer corrected, and reconciles a file-name room even when every photo
  answered ambiguous (by name it is one room).
- The engineer's aliases are a study field (`room_aliases` on `POST /v1/studies`, returned on
  the study) and can be extended at the rooms review: a `room` correction
  `{"merge_into": <room id>}` moves the room's photos and per-photo rows into the target in
  the next revision, rebuilds the merged inventory, and records the merge as an alias, so a
  photo re-analyzed later joins the target.

On the 52 real file names the production assignment (`name_group`, one photo at a time)
equals `group_photos` for every photo: 33 rooms with the 16 aliases, 49 without.

The room step replayed on run 3's stored per-photo outputs (sol + Opus fusion) with that
grouping, Sonnet 5.5 at its production settings, one repeat ($0.30):

| Grouping | count MAE | exact | bias | label F1 | report rows |
|---|---|---|---|---|---|
| Model room identity (production before; live run 3) | 0.60 | 0.51 | -0.23 | 0.69 | 32 of 39 |
| File name + aliases (section 23.3, same input) | 0.45 | 0.65 | +0.05 | 0.85 | 32 of 39 |
| **File name + aliases, production grouping (this replay)** | **0.37** | **0.68** | **+0.05** | **0.86** | 32 of 39 |
| Offline reference (16.5, sol input, mean of 3) | 0.43 | 0.63 | +0.10 | 0.85 | |

The two file-name rows are two repeats of the same grouping (section 16 treats differences
under about 0.1 as noise); the gain over the model's identity is the grouping. The aliases
are the engineer's merges; without them each view of a room is its own room and its objects
are counted twice (23.3: +0.51 bias), which is why the product takes them at creation and
at the rooms review.

### 27.2 Extraction: every empty field, room counts critical, the site area as printed

- `EXTRACTION_FALLBACK_SCOPE=missing` (default): one model call for every catalogue field
  that Azure DI and the regex pass left empty or that failed its per-field rule, with the
  whole document as context (`EXTRACTION_FALLBACK_CONTEXT_CHARS`, 60,000, section 17.1's)
  and 8,000 output tokens; `critical` is the previous behaviour. Fields Azure DI read are
  never replaced; the verbatim guard and the 0.6 confidence cap stay.
- The catalogue gained the boxes the labels have and it did not (one-unit price and age
  ranges, Foundation Walls, Floors, Walls, the cost approach's quantity rows, the cost
  service's quality and date); ambiguous captions ("Low", "Sq.Ft." in a cost row) are
  model-only (`captioned=False`), never matched from Azure DI or the regex pass.
- Room, bedroom and bathroom counts are critical.
- The site area is kept three ways from one reading: `site.area_sqft` (converted),
  `site.area` (as printed, "1.39 ac") and `site.area_acres` (when printed in acres).
- Cleaners: the materials/condition boxes drop the UAD rating the form prints after the
  material ("Concrete C3" -> "Concrete"); the cost-data month is `YYYY-MM`.
- The verbatim guard joins a currency sign to its amount: Azure DI's text prints "=$" and
  the figure on separate lines, and every cost-approach value the model returned
  ("$460,900") had been dropped as not in the text.

Measured on the real appraisal with the live run's stored Azure DI analysis (reused, as
the pipeline reuses an earlier revision's) and gpt-6-luna at low effort, scored with the
section 23.4 exporter's mapping (`repro/extraction_replay.py`; the exporter now maps 14
more catalogue keys to the label file's names for the same boxes, each listed with its URAR
caption):

| Fallback scope | right | wrong | not extracted | critical right | cost per study |
|---|---|---|---|---|---|
| Live run 3 (before) | 23 | 0 | 52 | 8 of 12 | $0.0004 |
| `critical` (this branch, 3 repeats) | 28 | 0 | 47 | 12 of 12 | $0.0006 |
| **`missing` (default, 3 repeats)** | **72** | **2** | **1** | **12 of 12** | **$0.0024** |

The same three fields on every repeat: `reconciliation.indicated_value_cost_approach` is the
disputed label of section 17.1 (the reconciliation prints 682,831, the cost approach
692,831; the model copies the reconciliation figure, which is what the box says);
`improvements.heating` reads FWA where the form checks "Other: Infloor" (Azure DI's text
interleaves the check-box marks with the options); `subject.property_rights` is answered
"not found". So 72 of 75 with one wrong value that is the model's (heating) and one the label's.
A prompt line on check-box marks (tried as `appraisal_field_fallback/v2`, 3 repeats)
changed nothing and was not released. Every model value is capped at confidence 0.6, so it
reaches the appraisal review as a suggestion.

One finding on the way: a dev `.env` that sets `OPENAI_MODEL` and Azure-first routing
silently ran the first replay on Azure's gpt-5-nano (36 to 61 right, 3 to 5 wrong). The
worker logs the model in effect at start; the replays here were re-run on the defaults.

### 27.3 Fail fast and restart

Workflow patch `stage-failure-retry-v1` (histories recorded before it replay unchanged):
the photo stage runs in the background while the appraisal is ingested and extracted. A
permanent extraction failure is recorded at once (`record_failure`: `status = failed`,
`studies.failure_reason`, migration 0006; the status query carries `failed_stage` and
`failure`), the photo stage schedules nothing more (the analyses already running finish
and keep their rows), and the workflow waits for the `retry` update, which
`POST /v1/studies/{id}/retry` (engineer) sends: 409 `study-not-failed` while nothing failed,
409 `workflow-closed` for an execution that has ended. The failure is cleared and the stage
runs again with fresh activity ids; analyzed photos are reused. `process_assets` and
`export_report` failures are held and retried the same way. A first version cancelled the
photo activities in flight; a cancel that crossed an activity's completion made the test
server refuse the workflow task and the worker's eviction hung (1 run in 4), so the stage
drains instead (15 consecutive green runs). Lifecycle tests: the failure is recorded before
any photo finishes, nothing is scheduled until the retry, the retried study completes and
its history replays; later-stage failures are held and retried; a held study can be
cancelled.

### 27.4 Gate timing

Workflow patch `appraisal-gate-early-v1`: the appraisal review opens as soon as the
extraction is done; the rooms review opens after the photo stage (analysis, SAM 3
localization, the room step). An appraisal rejection waits for the photo stage before it
starts the next revision, which carries the photo outputs over. Because the API moves the
study on a decision before the workflow does, an approval that lands on the next review
stage now leaves the study `processing` until the workflow opens that gate (its transition
converges the status to `waiting_review`), and a decision or an object edit on a gate the
workflow has not opened is 409 `gate-not-open`. The lifecycle test holds one photo in its
analysis to make the timing deterministic: in history order, extraction done < appraisal
gate open < last analysis done, and every analysis, localization and the room step < rooms
gate. On live run 3's timings the appraisal gate would open at about +90 s (the extraction was
done at +87 s) instead of +681 s; live it opened at +93 s with 39 photos still in flight (27.8).

### 27.5 Read routes

`GET /v1/studies/{id}/fields`, `/rooms` and `/assets` (viewer): the current revision or
`?revision=`, keyset pages with an opaque cursor that stays on its revision, one query per
table per page. Fields carry the catalogue label and critical flag; rooms their photos (in
file-name order) and inventory (the reconciled rows with the photos each object was seen
in, or the per-photo rows of a room without one); assets their component, room, takeoff,
cost, citation counts and review reasons (`needs_review` filter). The ids are the
correction `entity_id`s. OpenAPI regenerated; the Schemathesis contract test covers them.

### 27.6 Cost rows

Cost segregation prices each component from a published unit cost (the IRS Cost
Segregation Audit Techniques Guide, chapter 4: quantity takeoffs priced from a cost source
such as RSMeans, adjusted to the year and the location). The product now loads the
engineer's unit-cost table from `COST_TABLE_PATH` (`basis.domain.cost_table`, JSON by
component label: the takeoff unit, a factor from the source unit, bare material, labor and
equipment, Total Incl O&P for reference, the source line and any assumption), validated
when the worker starts. RSMeans line items are licensed, so the table is not in this
public repository: it was built privately from RSMeans Residential 2020 (60 labels) and
Building 2020 (signage, shed, window guard), each number read from the rendered page (the
PDFs' text layer is poor OCR), two of them spot-checked against the page images here.
Choices: bare costs, the basis of the six legacy rows (O&P is kept per row); the legacy rows
still win where they exist; kitchen cabinets are priced per unit (24-inch base cabinet), as
the room labels count them since 16.1, instead of the legacy perimeter run; wall and ceiling
finishes are painting lines (drywall is the wall's line) measured from the room area;
conversions such as per square to per SF or a 24-inch countertop per LF to per SF are row
factors. A row in another unit than the takeoff (site items priced per area or length that
the takeoff only counts) is reported as `COST_NEEDS_QUANTITY`, not `COST_ROW_MISSING`.

Re-priced on the live runs' stored assets, classification and other reasons as stored
(`repro/cost_review_load.py`):

| Reason | run 2: before | after | run 3: before | after |
|---|---|---|---|---|
| assets needing review | 186 of 203 | **94** | 175 of 192 | **82** |
| `cross:COST_ROW_MISSING` | 166 | **24** | 156 | **21** |
| ... flagged for that alone | 106 | | 105 | |
| `cross:COST_NEEDS_QUANTITY` | 0 | 13 | 0 | 10 |
| `model:` (the classifier's own doubts) | 34 | 34 | 28 | 28 |
| `rule_contested:*` | 43 | 43 | 42 | 42 |
| `cross:SECTION_1245_SF_LARGE` | 15 | 15 | 11 | 11 |
| other (`citation_unverified`, `validation:*`, `missing_evidence`) | 7 | 7 | 3 | 3 |
| assets priced | 37 | 166 | 36 | 161 |

What still has no row: 22 and 19 unidentified `other` components (nothing to price until
the engineer names them), a porch or two (the only RSMeans porch figure is a square-foot
model cost, left out), one generic `appliance`. The 13 and 10 `COST_NEEDS_QUANTITY` are roof,
siding, gutter, patio, driveway, sidewalk, fence, retaining wall and landscaping: their
quantity has to be measured (site area, roof area) before they can be priced. The year
factor is still the legacy escalation table on the 2020 base, not RSMeans' Historical Cost
Index (Building 2020 has one; Residential does not), and the location factor is still the
setting; both are follow-ups.

### 27.7 Still open

- Follow-ups: price `COST_NEEDS_QUANTITY` items from measured quantities (site area from
  the appraisal, roof area); price FF&E (`furniture`) from purchase records, which RSMeans
  does not cover; the RSMeans Historical Cost Index and City Cost Index for the year and
  location factors; a room-merge UI at the rooms review (the API takes `merge_into`);
  whether the heating check box needs Azure DI's selection-mark structure rather than its
  text.
- One live run is one repeat; Replicate was not throttled on this run (its credit was
  above the throttling threshold), so the photo stage is faster than in section 23 for a
  reason unrelated to this work.

### 27.8 The live re-run

The client study (the appraisal and 52 photos) through the running stack on the branch
(`c7b53d4`), the defaults of section 18 unchanged (gpt-6.1-sol decomposed with per-class
fusion on Opus 5.5, gpt-6-luna extraction, Sonnet 5.5 room step and classifier, SAM 3 on
Replicate), `PROPERTY_CLASS=nonresidential`, the private RSMeans table at
`COST_TABLE_PATH`, the engineer's 16 room aliases sent with the study, each gate approved
through the API with no corrections. Scored with `live_export.py` and `evals.run` on the
pinned label set, as in section 23 (one repeat):

| Step | Metric | Section 23 (run 3, defaults) | **Live, this branch** | Offline reference |
|---|---|---|---|---|
| Timing | appraisal gate open, from `start` | +681 s | **+93 s** (39 of 52 photos still in flight) | |
| | rooms gate / assets gate / completed | +685 s / +869 s / 874 s | +397 s / +587 s / **596 s** | |
| Per-photo counts (312 pairs) | count MAE / exact | 0.135 / 0.897 | **0.135 / 0.891** | 0.124 (15.1 4c) |
| Rooms | rooms made / count MAE / exact / bias | 14 / 0.60 / 0.51 / -0.23 | **33 / 0.44 / 0.66 / +0.04** | 0.43 (16.5) |
| | label F1 / report rows | 0.69 / 32 of 39 | **0.84 / 33 of 39** | 0.85 / 32 |
| Appraisal fields (75) | right / wrong / not extracted | 23 / 0 / 52 | **72 / 2 / 1** | 0.924 (17.1) |
| | critical (12) | 8 of 12 | **12 of 12** | 12 of 12 |
| Assets | assets / needing review | 192 / 175 (91%) | **280 / 140 (50%)** | |
| | `COST_ROW_MISSING` / priced | 156 / 36 | **51 / 214** | |
| | schedule rows answered right | 23 of 32 | **23 of 33** (bucket accuracy 0.50 of 46) | 0.688 (17.2) |
| | citations verified | 263 of 263 | **379 of 379** | |
| Cost | the study | $5.74 | **$6.75** | |

Reading:

- Every fix shows live. The appraisal gate opens with the extraction; the rooms gate waits
  for the photo stage. Production made the 33 labelled rooms, and the room step scored as the
  offline evaluation did (0.44 against 0.43). The extraction read 72 of 75 fields, the same
  three misses as 27.2. The review load halved.
- The per-photo step is unchanged (0.135, as in section 23), as expected: nothing in it
  changed.
- More assets (280 against 192): with 33 rooms instead of 14, objects that the model's
  identity had merged across rooms are now counted in their own rooms. So the classifier
  made 359 calls ($3.99, against $3.08), which is most of the cost difference.
- What still has no cost row live: 30 unidentified `other`, 17 `furniture` (FF&E, a label
  added after section 23, which cost segregation prices from purchase records, not from
  RSMeans), two half walls (the only RSMeans figure is an assembly, left out), one generic
  appliance, one porch. Fifteen site items are `COST_NEEDS_QUANTITY`. The other review
  reasons: `rule_contested` 84, the classifier's own doubts 39,
  `SECTION_1245_SF_LARGE` 14, `STRUCTURAL_AS_PERSONAL` 4, `citation_unverified` 2. Only 2
  assets were flagged for a missing cost row alone (section 23: 105).
- The photo stage was faster than in section 23 (SAM 3 not throttled), so the absolute
  timings are not a like-for-like comparison. The appraisal gate's +93 s is independent of
  that: it no longer waits for the photos at all.

Commands (from `backEnd/`, a stack as in section 23 with `COST_TABLE_PATH` set):

```bash
ROOM_ALIASES=~/basis-eval-data/labels/room_aliases_v1.json PG_DB=basis_s25 API=http://127.0.0.1:8210 \
    python ../docs/review_2026-10-15/repro/live_client.py OUT
BASIS_EVAL_DATA_DIR=~/basis-eval-data/work/labels_pre_2026-10-16 \
    python ../docs/review_2026-10-15/repro/live_export.py OUT/db_final OUT/documents.json PRED
BASIS_EVAL_DATA_DIR=~/basis-eval-data/work/labels_pre_2026-10-16 python -m evals.run \
    --predictions PRED --no-db
```

Spend for this section: $6.75 for the live run ($5.79 Anthropic, $0.85 OpenAI, $0.12
Replicate; Azure DI not recorded), $0.30 Anthropic for the room replay, about $0.03 OpenAI
for the extraction replays, about $0.01 Azure OpenAI (the gpt-5-nano replay by mistake);
the RSMeans extraction made no model calls.

Commands (from `backEnd/`; the predictions, labels and cost table are private):

```bash
# 27.1 room step on the stored live per-photo outputs, production grouping = group_photos
python -m evals.predict_rooms --photos ~/basis-eval-data/inputs/photos \
    --out ~/basis-eval-data/predictions/s25_rooms_replay_defaults \
    --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    --per-photo ~/basis-eval-data/predictions/live_e2e_v070_defaults_perphoto --repeats 1 \
    --room-prompt v3 --model claude-sonnet-5-5 --reasoning-effort low \
    --single-photo-rooms per-photo --materials sensitive
# 27.2 extraction on the stored Azure DI analysis
uv run python ../docs/review_2026-10-15/repro/extraction_replay.py \
    ~/basis-eval-data/inputs/appraisal.pdf AZURE_DI_JSON LABELS_JSON OUT --scope missing --repeats 3
# 27.6 review load
uv run python ../docs/review_2026-10-15/repro/cost_review_load.py ASSETS_JSON \
    --cost-table ~/basis-eval-data/costs/unit_costs_rsmeans_2020.json
```

## 28. Cost sweep: the cheapest model that matches, step by step (2026-10-16)

Question (Bryan's rule): at every step where accuracy is not the binding constraint, which is
the cheapest model whose measured accuracy is not below the best? The exception is the
per-photo identification and counting of components (the countables call on gpt-6.1-sol and
the per-class fusion call on Opus 5.5, sections 12.1 and 15), which was not touched. Three
steps were re-run: the per-photo surfaces-and-room-type call, the room step's reconciler and
material second look, and the asset classifier. Candidates: the models of sections 12 to 17
plus three new on the account, gpt-5.6-luna ($0.20 / $1.20 per million tokens in / out),
gpt-5.6-terra ($2 / $12) and gpt-5.6-sol ($4 / $20) (developers.openai.com/api/docs/pricing,
standard tier, short context, checked 2026-10-16; now rows of `basis.llm.cost` with tests).
gpt-5.6-sol was not run: it costs twice gpt-6.1-sol, which is already the most expensive
OpenAI model in each comparison below.

Rule, as in 17.3: three repeats per configuration, one change at a time, a paired bootstrap
(10,000 resamples, 95% interval) over the step's items against the current default, and the
cheapest candidate whose interval on every measure the product depends on includes zero or
favours it. One property, so "within the noise" means within the noise of 52 photos, 33 rooms
or 46 asset rows; every recommendation below needs the held-out study before it is trusted
further. Every run's `run.json` `models_reported` names only the requested model (Azure
unset, so nothing fell back), with no provider errors. The tables are
`docs/review_2026-10-15/repro/cost_sweep_table.py` over the run directories (`cost28_*` and
`rooms_cmp_*` under `$BASIS_EVAL_DATA_DIR/predictions`).

### 28.1 Surfaces and room type (the decomposed step's second call)

The countables call is fixed (`vision_countables_v2b_gpt61sol`, the run of 12.1); only the
surfaces call changes, and the room type comes from it (`merge_photo_runs --room-from 2`).
Measured on both releases of the surfaces prompt: `photo_surfaces/v1` (the prompt of 12.1, so
the existing gpt-6.1-sol run is the baseline) and `photo_surfaces/v2` (the released prompt
since section 19; gpt-6.1-sol re-run for the baseline). Low effort, output budget 6000.
"Surfaces" are the labels of `basis.vision.localize.SURFACE_LABELS` (floors, wall and ceiling
finishes, trim, roof, siding, site surfaces); report rows and material present are scored on
the deterministic heuristic room inventory built from each per-photo repeat (no room call, so
only the surfaces call differs); room-level P / R is that inventory's surface labels against
the room labels.

| Surfaces call | Room accuracy, mean of 3 | Room pass^3 | Surfaces P / R / F1, per photo | All labels P / R / F1 (repeat 1) | Room-level surfaces P / R | Report rows evidenced (of 38), mean of 3 | Material present (of 8) | Cost per 52 photos | Seconds per photo |
|---|---|---|---|---|---|---|---|---|---|
| **v1**: gpt-6.1-sol (12.1, baseline) | 0.891 | 0.865 | 0.580 / 0.772 / 0.663 | 0.66 / 0.82 / 0.73 | 0.707 / 0.869 | 30.3 | 7.3 | $0.329 | 7.7 |
| v1: gpt-6-luna | 0.897 | 0.846 | 0.655 / 0.719 / 0.685 | 0.70 / 0.79 / 0.75 | 0.788 / 0.820 | 31.3 | 8.0 | **$0.018** | 4.1 |
| v1: gpt-5.6-luna | 0.885 | 0.846 | 0.607 / 0.746 / 0.669 | 0.68 / 0.80 / 0.73 | 0.725 / 0.865 | 32.0 | 8.0 | $0.036 | 3.6 |
| **v2**: gpt-6.1-sol (baseline) | 0.872 | 0.865 | 0.588 / 0.804 / 0.679 | 0.66 / 0.83 / 0.74 | 0.721 / 0.919 | 33.0 | 8.0 | $0.336 | 6.8 |
| **v2: gpt-6-luna** | 0.878 | 0.846 | 0.650 / 0.731 / 0.688 | 0.69 / 0.80 / 0.74 | 0.783 / 0.860 | 32.7 | 8.0 | **$0.019** | 4.1 |
| v2: gpt-5.6-luna | 0.885 | 0.846 | 0.616 / 0.754 / 0.678 | 0.68 / 0.82 / 0.74 | 0.717 / 0.878 | 33.0 | 8.0 | $0.038 | 3.8 |

Paired differences against gpt-6.1-sol on the same prompt (photos for room accuracy, F1 and
per-photo P / R; rooms for room-level P / R):

| | Room accuracy | Surfaces F1 per photo | Precision, per photo | Recall, per photo | Room-level precision | Room-level recall |
|---|---|---|---|---|---|---|
| v1, gpt-6-luna | +0.006 [-0.058, +0.071] | +0.012 [-0.035, +0.058] | **+0.074** [+0.029, +0.123] | **-0.053** [-0.102, -0.003] | **+0.081** [+0.015, +0.138] | -0.050 [-0.127, +0.018] |
| v1, gpt-5.6-luna | -0.006 [-0.064, +0.058] | +0.013 [-0.031, +0.057] | +0.027 [-0.018, +0.070] | -0.026 [-0.075, +0.025] | +0.018 [-0.034, +0.072] | -0.005 [-0.059, +0.063] |
| v2, gpt-6-luna | +0.006 [-0.064, +0.077] | -0.001 [-0.037, +0.034] | **+0.062** [+0.024, +0.103] | **-0.073** [-0.116, -0.032] | **+0.062** [+0.016, +0.101] | -0.059 [-0.139, +0.004] |
| v2, gpt-5.6-luna | +0.013 [-0.045, +0.077] | -0.021 [-0.079, +0.033] | +0.028 [-0.021, +0.076] | **-0.050** [-0.102, -0.003] | -0.004 [-0.064, +0.054] | -0.041 [-0.095, +0.013] |

Reading:

- gpt-6-luna matches gpt-6.1-sol on room type, on surface F1, on the product measures (report
  rows 32.7 against 33.0 of 38 on v2, material present 8 of 8) at one eighteenth of the price
  and in about 60% of the time. It answers 52 photos for $0.019 against $0.336.
- It is not the same model with a discount: it lists fewer surfaces. Per photo it trades
  recall for precision, significantly both ways on both prompts (v2: precision +0.06, recall
  -0.07). The extra misses are ceiling finishes (33 against 24 of 156 records), gutters (9
  answers over four exterior photos, none for sol) and a few carpets and railings; the false
  positives it no longer makes are baseboards, sidewalks and stairs listed in photos the
  labels do not list them for. At the room level, where several photos of one room fill each
  other's gaps and where the assets come from, the recall loss is -0.06 with an interval that
  reaches zero ([-0.139, +0.004]), while the precision gain stays significant. The one
  product-level difference: on one of the three v2 repeats the heuristic inventory has no
  sidewalk, so the Sidewalks / Curbs row is missed there (pass^3 32 against 33 rows).
- gpt-5.6-luna is not the safer cheap choice it looked like on v1: on v2 it also loses
  recall significantly (-0.05) without gpt-6-luna's precision gain, at twice its price.
- Room type: 19 (gpt-6-luna) and 18 (gpt-5.6-luna) wrong answers of 156 against 20 for sol
  on v2, of the same kind: the basement bedrooms (as living room, basement or ambiguous) and
  the mechanical room (as ambiguous; the luna models also say basement there on three
  answers).

Recommendation: **the surfaces call on gpt-6-luna** (low effort, budget 6000), with the
countables call kept on gpt-6.1-sol. New setting `VISION_SURFACES_MODEL` (default
`gpt-6-luna`; `gpt-6.1-sol` restores the previous behaviour); `VISION_REASONING_EFFORT` and
`VISION_MAX_TOKENS` apply to both calls, and the surfaces call's budget follows its model's
family when `VISION_MAX_TOKENS` is unset (`Settings.vision_surfaces_output_budget`). The
recall trade is the thing to watch on the held-out study: if it shows missed surface rows
there, `VISION_SURFACES_MODEL=gpt-6.1-sol` is one setting away.

### 28.2 Room step: reconciler and material second look

Input: repeat 1 of `vision_decomposed_v2_opus55` for every run, as in 16.5;
`--room-prompt v3 --single-photo-rooms per-photo --materials sensitive`, low effort, three
repeats; the reconciler and the second look on the same model unless the row says otherwise.
The baseline is the recommended run of 16.5 (`rooms16_final_v2_opus55`, Sonnet 5.5). Its count
MAE is 0.388 here, not section 16's 0.34, because the room labels have since gained the six
labels of section 19 (half walls, bathroom accessories and the rest), which no per-photo input
of section 16 lists; every row below is scored against the same current labels, so the
comparison is like for like (against the label set pinned before that change,
`work/labels_pre_2026-10-16`, the baseline is 0.342 and gpt-6-luna 0.348, the same paired
difference). Count measures are the harness's (`other` and loose furniture
left out on both sides). "Labelled surfaces with the right material": the 20 (room, wall
finish / wainscoting / tile) pairs whose photos' labels record a material, right when the
run's most common answer over the repeats is that material (`GOLD_MATERIAL` in the script maps
the labels' free text, such as `painted_block`, to the enum, `painted_masonry`); three of the
20 are not listed by any run (the per-photo input misses them).

| Reconciler (= material model) | Count MAE | Exact-count rate | Signed bias | Label F1 | 8 multi-photo rooms: MAE / exact / bias | Report rows (of 38), pass^3 | Material present (of 8) | Labelled surfaces with the right material (of 20) | Cost per run |
|---|---|---|---|---|---|---|---|---|---|
| **claude-sonnet-5-5** (16.5, baseline) | 0.388 | 0.68 | +0.00 | 0.87 | 0.56 / 0.61 / -0.09 | 30, 0.79 | 8 | **17** | $0.314 |
| gpt-6.1-sol | 0.390 | 0.66 | +0.10 | 0.87 | 0.55 / 0.57 / +0.20 | 33, 0.82 | 8 | 14 | $0.263 |
| gpt-5.6-terra | 0.426 | 0.67 | +0.03 | 0.85 | 0.65 / 0.59 / -0.02 | 31, 0.79 | 8 | 12 | about $0.36 (repriced; see below) |
| gpt-6-luna | 0.393 | 0.66 | +0.08 | 0.86 | 0.56 / 0.57 / +0.14 | 30, 0.79 | 8 | 14 | **$0.014** |
| gpt-6-luna, second look on Sonnet 5.5 | 0.437 | 0.65 | +0.05 | 0.86 | 0.69 / 0.53 / +0.05 | 30, 0.79 | 8 | 16 | $0.157 |
| gpt-5.6-luna | 0.401 | 0.67 | +0.05 | 0.86 | 0.59 / 0.59 / +0.04 | 30, 0.79 | 8 | 12 | $0.030 |

Paired bootstrap over rooms of the per-room count MAE (each room's mean absolute error over
its labels, averaged over the repeats), candidate minus Sonnet: gpt-6.1-sol +0.016 [-0.024,
+0.067] (multi-photo rooms +0.064 [-0.100, +0.270]); gpt-5.6-terra +0.023 [-0.001, +0.052]
(multi +0.094 [+0.005, +0.188]); gpt-6-luna +0.003 [-0.025, +0.030] (multi +0.012 [-0.106,
+0.124]); gpt-6-luna with the Sonnet second look +0.021 [-0.003, +0.054] (multi +0.086
[-0.009, +0.204]); gpt-5.6-luna +0.001 [-0.031, +0.037] (multi +0.005 [-0.129, +0.150]). The
two gpt-6-luna rows run the same reconciler (the second look changes materials, never
counts), so their difference is the reconciler's run-to-run noise: 0.393 against 0.437 overall
and 0.56 against 0.69 on the multi-photo rooms. Pooled over its six repeats, gpt-6-luna minus
Sonnet is +0.012 [-0.012, +0.040] over all rooms and +0.049 [-0.052, +0.157] over the 8
multi-photo rooms.

Reading:

- **Counts: every candidate but gpt-5.6-terra is within the noise of Sonnet.** The call only
  touches the 8 multi-photo rooms (the 25 one-photo rooms take the per-photo analysis), and
  on 8 rooms the interval is about ±0.15 MAE, so "within the noise" is weak evidence. Section
  16's finding that a small model makes the inventory worse (gpt-4.1-mini, 0.62 against 0.42)
  does not repeat with gpt-6-luna: it counts the kitchen's cabinet units as Sonnet does (21 on
  every repeat against 19 to 21; 18 in the labels) and the exterior's windows at 20 to 26 on
  five of its six repeats (Sonnet 20; 30 in the labels), with one repeat at 11, the run-to-run
  noise above. gpt-5.6-luna counts the kitchen at 12 on every repeat. gpt-5.6-terra is the
  worst reconciler (exterior windows 11 to 16) and the most expensive: about $0.36 per run,
  more than Sonnet, with 26,600 reasoning tokens over the three repeats' reconciliation calls
  against 13,200 for gpt-6-luna and 4,600 for gpt-6.1-sol. Its `run.json` records $4.79 for
  three repeats ($1.60 per run) because it ran before the gpt-5.6 price rows existed, so every
  call was charged at the unknown-model rate ($10 / $50). Repriced at $2 / $12: the
  reconciliation calls' recorded tokens (106,029 in, 46,385 out) cost $0.77 instead of $3.38.
  The second looks record a cost but no tokens, so their $1.41 reprices to between $0.28
  (all input) and $0.34 (all output). Total: $1.05 to $1.11, about $0.36 per run.
- **Materials: Sonnet's second look is the best measured.** 17 of 20 labelled surfaces right,
  against 14 for gpt-6.1-sol and gpt-6-luna and 12 for the gpt-5.6 models. The cheaper
  models' errors are the ones section 16 named as the trap: painted walls called `wallpaper`
  (the front entrance and the stair hall on sol, the stair hall on luna), which would evidence
  Wall Coverings on a painted wall; the painted block bedroom as `brick` (sol) or `other`
  (luna); the basement bath's painted wainscot as `ceramic` (luna). The hall's wallpaper
  wainscot, the surface behind the Wall Coverings row, is right on every model but
  gpt-5.6-luna, which calls it `wood`, the error gpt-4.1-mini made in section 16. Report rows
  evidenced do not separate the runs (presence is checked, not the material's correctness),
  which is why the material column is the one to read here.
- **The cheapest configuration that matches on both is gpt-6-luna for the reconciliation and
  Sonnet 5.5 for the second look**: counts within the noise (above), 16 of 20 materials (the
  one difference from Sonnet's 17: the upstairs access-stair tile, answered `fabric` on most
  repeats of this run and `ceramic` on Sonnet's),
  half the price ($0.157 against $0.314 per run, of which the second looks are $0.148).

Materials fall under the accuracy exception, with component identification and counting: the
material decides the IRS bucket of a row (a painted wall read as wallpaper moves it from the
39-year building to a 5-year wall covering). So the material second look stays on Sonnet 5.5
whatever it costs, and only the reconciliation is open to the cost rule.

Recommended configuration: **gpt-6-luna low for the reconciliation, Sonnet 5.5 low for the
material second look** (16 of 20 materials, counts within the noise, $0.157 against $0.314 per
run, about $0.16 less per study). It needs a `ROOM_MATERIALS_MODEL` setting: the production room
step ran both calls on `ROOM_MODEL`. The room-call-wiring PR (PR 56, section 29.1) added that
setting, defaulting to Sonnet 5.5 low, and left `ROOM_MODEL` unchanged, so **this PR leaves
the room step on Sonnet 5.5 for both calls** and the split is one setting away:
`ROOM_MODEL=gpt-6-luna` moves the reconciliation only. (Before that setting, moving `ROOM_MODEL`
to gpt-6-luna moved the second look too and lost three of the 17 right materials.)

### 28.3 Asset classification

`evals.predict_assets --repeats 3 --max-tokens 6000`, the 46 rows of `assets`, the production
model path (residual tool loop, `classify/v1`, citation verification, review policy) with the
rules table and cache bypassed, as in 17.2. The baseline was re-run (Sonnet 5.5, low effort) on
this branch's code, since the classifier, the retrieval code and `asset_class` (section 24)
changed after 17.2; its numbers reproduce 17.2 (bucket 0.696 against 0.688, citation validity
over three repeats 0.964 against 0.970). OpenAI models run at reasoning effort `none`:
gpt-5.6-luna, like gpt-6-luna and gpt-5.4-mini in 17.2, answers 400 "Function tools with
reasoning_effort are not supported ... in /v1/chat/completions" at `low` (checked on one row),
so effort `none` is the only configuration production can run without the Responses API.

The harness's `citation_validity` and `rows_with_verified_citation` are repeat 1 (as in 17.2);
both are shown over the three repeats too, and the paired comparisons use the three-repeat
values.

| Model | effort | Bucket right, repeat 1 | mean of 3 | pass^3 | macro-F1, repeat 1 | Citations verified, repeat 1 / all 3 | Rows with a verified citation, repeat 1 / mean of 3 | Needs review, mean of 3 | Loop failures (of 138) | Cost per study (46 rows) | Seconds per row |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **claude-sonnet-5-5** (re-run, baseline) | low | 0.717 | 0.696 | 0.652 | 0.704 | 0.942 / **0.964** | 0.913 / 0.942 | **0.43** | 0 | $1.282 | 5.1 |
| claude-sonnet-5-5 (17.2) | low | 0.674 | 0.688 | 0.630 | 0.600 | 0.964 / 0.970 | 0.957 / 0.957 | 0.46 | 0 | $1.247 | 5.9 |
| claude-opus-5-5 (17.2) | low | 0.696 | 0.703 | 0.696 | 0.541 | 0.959 / 0.982 | 1.000 / 1.000 | 0.36 | 0 | $1.919 | 9.1 |
| gpt-6-luna (with 24's fix) | none | 0.674 | 0.652 | 0.630 | 0.522 | 0.914 / 0.946 | 0.935 / 0.957 | 0.55 | **0** | **$0.052** | 6.2 |
| gpt-5.6-luna | none | 0.739 | **0.717** | 0.674 | 0.584 | 0.932 / 0.884 | 0.957 / 0.957 | 0.43 | 0 | $0.083 | 5.2 |
| gpt-5.6-terra | none | 0.696 | 0.696 | 0.652 | 0.651 | 0.924 / 0.933 | 0.935 / 0.957 | 0.51 | 0 | $0.639 | 5.2 |

Paired over the 46 rows, candidate minus the Sonnet re-run (bucket right, rows with a
verified citation and needs review as per-row means over the repeats; citation validity as
verified / claimed pooled over the resampled rows):

| | Bucket right | Citation validity | Rows with a verified citation | Needs review |
|---|---|---|---|---|
| gpt-6-luna | -0.043 [-0.109, +0.000] | -0.018 [-0.061, +0.024] | +0.014 [-0.036, +0.065] | **+0.123** [+0.014, +0.239] |
| gpt-5.6-luna | +0.022 [-0.022, +0.072] | **-0.080** [-0.132, -0.029] | +0.014 [-0.043, +0.072] | +0.007 [-0.116, +0.138] |
| gpt-5.6-terra | -0.000 [-0.058, +0.058] | -0.031 [-0.076, +0.013] | +0.014 [-0.036, +0.072] | +0.080 [-0.029, +0.188] |
| claude-opus-5-5 (17.2) | +0.007 [-0.051, +0.072] | +0.017 [-0.015, +0.053] | **+0.058** [+0.014, +0.109] | -0.065 [-0.145, +0.007] |
| claude-sonnet-5-5 (17.2), same configuration | -0.007 [-0.036, +0.014] | +0.006 [-0.026, +0.043] | +0.014 [-0.036, +0.072] | +0.036 [-0.014, +0.087] |

Reading:

- **gpt-6-luna with section 24's fix**: 0 loop failures (6 in 17.2), as expected; bucket
  accuracy 0.652, at the edge of significance below Sonnet (-0.043 [-0.109, +0.000]), and it
  sends significantly more rows to review (55% against 43%, +12 points, about 34 more of the
  280 assets of a live study, 27.8). Its own doubts drive that: 69 of its 138 answers flag
  themselves, against Sonnet's 54 (unverified citations 10 against 9). Cheapest by far
  ($0.05 per 46 rows) but not matching.
- **gpt-5.6-luna has the best bucket accuracy measured (0.717) and fails on citations.** 88%
  of its citations pass the production check against Sonnet's 96%, significantly lower: 28
  of its 138 answers carry an unverified citation (Sonnet 9); of the 27 citations dropped, 16
  quote an excerpt that is not on the cited chunk and 7 one too short to check. An unverified
  citation is never shown as verified (it sends the row to review), so the risk is review
  work and a weaker evidence pack rather than a wrong citation reaching the engineer; but the
  citation is the product's defence of every asset, so a model that cites less reliably does
  not match.
- **gpt-5.6-terra is within the noise of Sonnet on every measure, at half the price** ($0.64
  against $1.28 per 46 rows), and its point estimates are worse on both product measures:
  citation validity 0.933 against 0.964 (unverified citations 18 against 9) and needs review
  51% against 43%. The interval on review load is mostly positive ([-0.03, +0.19]); its point
  estimate is about 22 more rows to review on a live study's 280 assets, against about $2
  saved at the live study's classifier spend (27.8: $3.99), so the switch pays only if an
  engineer reviews a row for less than about $0.09.
- Sonnet's re-run reproduces 17.2 within the noise (17.2 minus the re-run: bucket -0.007,
  citation validity +0.006), so the comparison is not distorted by the code that changed
  since.

Recommendation: **keep claude-sonnet-5-5 at low effort.** No cheaper model matched on the
measures the product depends on: gpt-6-luna on review load, gpt-5.6-luna on citation validity,
and gpt-5.6-terra is not shown to be worse but is worse on both product measures' point
estimates at a saving smaller than its review cost. If spend comes to matter more than review
load, gpt-5.6-terra (`CLASSIFY_MODEL=gpt-5.6-terra`, `CLASSIFY_REASONING_EFFORT=none`) is the
candidate to re-measure on the held-out study. The other lever is the Responses API in the
OpenAI provider (17.3's follow-up): it would let the OpenAI models run the loop with
reasoning, which none of these rows had.

### 28.4 Recommended default per step, and the cost per study

| Step | Before | After | Section | Cost per study, before → after |
|---|---|---|---|---|
| Per-photo countables and count fusion | gpt-6.1-sol low + Opus 5.5 medium per class | unchanged (accuracy binds) | 12.1, 15 | unchanged |
| **Per-photo surfaces and room type** | gpt-6.1-sol low | **gpt-6-luna low** (`VISION_SURFACES_MODEL`) | 28.1 | $0.336 → **$0.019** per 52 photos |
| Room step, reconciliation | Sonnet 5.5 low | **recommended gpt-6-luna low** (`ROOM_MODEL=gpt-6-luna`, the second look on `ROOM_MATERIALS_MODEL`, 29.1); default still Sonnet | 28.2 | $0.314 per run (→ $0.157 with the split) |
| Room step, material second look | Sonnet 5.5 low | unchanged: materials decide IRS buckets (accuracy exception), and Sonnet is the best measured | 28.2 | (in the row above) |
| Extraction fallback | gpt-6-luna low | unchanged (17.1: cheapest and best) | 17.1 | $0.0024 |
| Asset classification | Sonnet 5.5 low | unchanged (no cheaper model matched) | 28.3 | $1.28 per 46 rows ($3.99 live, 27.8) |

On the live study of 27.8 ($6.75), the surfaces change saves about $0.32 (the gpt-6.1-sol
surfaces calls on 52 photos), so about $6.43 per study, and about $6.27 once the room step's
split lands. These are offline per-step costs applied to the live total, not a new live run.
The steps that cost the most per study (count fusion on Opus and the classifier on Sonnet) are
the ones where accuracy binds or where no cheaper model matched.

Spend for this section: $8.07 recorded in the runs' `run.json` (OpenAI $3.78: assets
gpt-5.6-terra $1.92, gpt-5.6-luna $0.25, gpt-6-luna $0.16; surfaces gpt-6.1-sol v2 $1.01,
gpt-5.6-luna $0.11 + $0.11, gpt-6-luna $0.05 + $0.06; rooms gpt-5.6-luna $0.09 and
gpt-6-luna $0.03 of the split run; Anthropic $4.29: the Sonnet assets re-run $3.85 and the
split run's second looks $0.44), plus about $0.05 of one-row and one-photo smoke checks. The
three `rooms_cmp_*` runs (gpt-6.1-sol $0.79, gpt-6-luna $0.04, gpt-5.6-terra about $1.08
repriced, $4.79 as recorded at the unknown-model rate) were made by the orchestrating session
and are reused here.

Commands (from `backEnd/`, `BASIS_ENV=dev`, OpenAI and Anthropic keys as in section 14,
Azure unset):

```bash
P=~/basis-eval-data/predictions
# 28.1 surfaces call (v1 and v2), then merged with the fixed countables run
python -m evals.predict_photos --photos ~/basis-eval-data/inputs/photos \
    --out $P/cost28_surfaces_v2_gpt6luna --repeats 3 --concurrency 4 \
    --prompt-name photo_surfaces --prompt v2 --model gpt-6-luna --reasoning-effort low --max-tokens 6000
python -m evals.merge_photo_runs --runs $P/vision_countables_v2b_gpt61sol \
    $P/cost28_surfaces_v2_gpt6luna --room-from 2 --out $P/cost28_decomposed_v2b_sol_surfv2_gpt6luna
# 28.2 room step (the split run; the others drop --materials-model)
python -m evals.predict_rooms --photos ~/basis-eval-data/inputs/photos \
    --out $P/cost28_rooms_gpt6luna_matsonnet --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    --per-photo $P/vision_decomposed_v2_opus55 --per-photo-repeat 1 --repeats 3 --concurrency 2 \
    --room-prompt v3 --model gpt-6-luna --reasoning-effort low --single-photo-rooms per-photo \
    --materials sensitive --materials-model claude-sonnet-5-5
# 28.3 classification (Claude at low; OpenAI at none)
python -m evals.predict_assets --out $P/cost28_assets_gpt56terra --model gpt-5.6-terra \
    --reasoning-effort none --max-tokens 6000 --repeats 3 --concurrency 4
# tables
BASIS_ENV=test python ../docs/review_2026-10-15/repro/cost_sweep_table.py photos \
    --photos ~/basis-eval-data/inputs/photos --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    $P/cost28_decomposed_v2b_sol_surfv2_gpt61sol $P/cost28_decomposed_v2b_sol_surfv2_gpt6luna \
    $P/cost28_decomposed_v2b_sol_surfv2_gpt56luna
BASIS_ENV=test python ../docs/review_2026-10-15/repro/cost_sweep_table.py rooms \
    $P/rooms16_final_v2_opus55 $P/rooms_cmp_gpt61sol $P/rooms_cmp_gpt56terra $P/rooms_cmp_gpt6luna \
    $P/cost28_rooms_gpt6luna_matsonnet $P/cost28_rooms_gpt56luna
BASIS_ENV=test python ../docs/review_2026-10-15/repro/cost_sweep_table.py assets \
    $P/cost28_assets_claudesonnet55 $P/cost28_assets_gpt6luna $P/cost28_assets_gpt56luna \
    $P/cost28_assets_gpt56terra $P/text_assets_claude-sonnet-5-5 $P/text_assets_claude-opus-5-5
```

Wiring note (code, not measurement): with Azure OpenAI configured, the Azure provider maps a
request's model name to a deployment by `VISION_MODEL` and `TEXT_MODEL` only, so a
`gpt-6-luna` request (the extraction fallback today, and now the surfaces call) goes to the
text deployment, whatever model that deployment is. This is the routing gotcha of 27.2, not new
here; the measured configuration and the live runs have Azure unset.

## 29. Room-stage options wired, measured, off by default (2026-10-16)

Section 26.10 recommended two changes to the room stage and asked for them to be confirmed on
a second client package before they become the default (the interior/exterior split was chosen
on this study). They are now wired behind settings whose defaults keep today's behaviour, so
that confirmation is a settings flip, and replayed through the wired code to check that it
gives what the study measured.

### 29.1 What was wired

- `ROOM_STEP_MODE` (`reconcile` default | `room_call`), `ROOM_CALL_MODEL` (`claude-opus-5-5`),
  `ROOM_CALL_REASONING_EFFORT` (`high`): in `reconcile_rooms`, a room whose voted type is not
  exterior, parking or mechanical (`room_step.room_path`) gets one `room_photos_inventory/v2`
  call with all its photos (`room_step.room_call`, the request of `multiview.build_request`,
  scope `room`, answer budget 6,000), one-photo rooms included as 26.4 measured; its answer
  is checked like the reconciliation call's (objects pointing at no image dropped, the room
  flagged) and stored the same way (`step="room_call"`); no material second look follows it
  (26.7 measured the room call's own materials). Exterior, parking and mechanical rooms keep
  the section 16.5 path. An answer unusable twice falls back to that path; the failed call's
  record and cost are kept and the room says `room_call_fallback`. Rows and the stored record
  say which `path` every room took, the record the room's cost; the activity's output counts
  `room_calls` and `room_call_fallbacks`. Transient errors still retry the activity, an
  exhausted budget and a provider account error (PR 53) still stop it.
- The rule is fixed on the room's voted type, not on the file names' no-floor token (`x_`)
  the study used: on this study they pick the same three rooms (`x_property_front` and
  `x_shed` voted exterior, `x_mechanicals` mechanical), and the type rule still works for a
  package named without floors (`Kitchen 1.jpg` is `x_kitchen_1` by the file-name rule) or not
  named at all. Fixed here, before the second package is seen.
- `MATERIALS_SECOND_LOOK` (`per_photo` default | `surface_crops`), `SURFACE_CROP_MODEL`
  (`claude-sonnet-5-5`), `SURFACE_CROP_REASONING_EFFORT` (`low`), `SURFACE_SEGMENTER`
  (`local` | `replicate`): after a room's inventory, each photo is segmented, every wall,
  floor and ceiling of at least 2% of the photo is asked about (`surface_crop_material/v1`,
  the study's request, now shared by `evals.predict_surface_crops`), and the room's wall
  finish, wainscoting, tile and ceiling finish take the crops' majority material
  (`surface_crops.apply_crops`, the study's `apply`, checked equal by a test); objects no
  crop speaks to keep their material. A segmenter failure, an answer unusable twice or a
  provider outage leaves that photo or surface on its previous material and flags the room
  (`surface_crops_flags`); an account error or an exhausted budget still stops the activity.
- The segmenter: `local` is the study's checkpoint (`facebook/mask2former-swin-large-ade-panoptic`,
  panoptic post-processing at 0.05, the union of each surface class), loaded in the worker
  on first use and run on the CPU in a thread. On the 52 photos its masks equal the study's
  stored masks exactly (IoU 1.000 on all 98 surface masks, the same "asked or not" decision
  on all 156 photo-surfaces; 34 s for 52 photos after a 22 s load). `replicate` uses the only
  Replicate deployment with a structured output, `oitoito/mask2former` (Swin-S ADE20K
  *semantic*, a smaller checkpoint, so **not measured**): on the kitchen photo its masks
  overlap the local ones at IoU 0.79 (wall), 0.98 (ceiling), 0.96 (floor), $0.0005, after a
  99 s cold start; its segmentation arrives inline as a data URI. `MATERIALS_SECOND_LOOK=
  surface_crops` with `replicate` and no token is refused at startup.
- `ROOM_MATERIALS_MODEL` (`claude-sonnet-5-5`) and `ROOM_MATERIALS_REASONING_EFFORT`
  (`low`): the material second look's own model and effort, which until now followed
  `ROOM_MODEL`. Section 28.2 measured gpt-6-luna reconciling rooms within the noise of Sonnet
  (paired +0.003 [-0.025, +0.030] over 33 rooms) but reading materials worse (14 of 20
  labelled surfaces against 17), and luna reconciling with Sonnet looking at 16 of 20 for
  $0.157 a run against $0.314. The defaults equal today's (both Sonnet low), so nothing
  changes until `ROOM_MODEL=gpt-6-luna` is set; then the second look stays on Sonnet. Each
  second look records the model that answered it, and an inventory row's `model` joins the
  reconciler's and the looks' (`claude-sonnet-5-5+gpt-6-luna`), so no new row field.
- `evals.replay_rooms` runs `room_step.inventory_room` and the crop look exactly as the
  activity configures them, from the settings, on a stored per-photo run grouped by file
  name and aliases (equal to the production grouping, 27.1).

### 29.2 Room counts through the wired code

One repeat of `evals.replay_rooms` with `ROOM_STEP_MODE=room_call` (everything else at the
production defaults: the exterior groups get Sonnet 5.5 `room_inventory/v3` and the second
look), scored with `evals.run` (`room_inventory`, repeat 1, current labels). Two per-photo
inputs: the one the study's combination used (Opus 5.5 per-photo, `vision_decomposed_v3_opus55`,
which the exterior groups reconcile) and the latest live run's (run 3, sol + Opus fusion,
`live_e2e_v070_defaults_perphoto`). The interior rooms do not read the per-photo outputs.

| Run | Per-photo input | Count MAE | Exact counts | Rooms exact | Label F1 | Report rows | $ | Wall s |
|---|---|---|---|---|---|---|---|---|
| **Wired, `room_call`** (`s29_replay_room_call_opus_input`) | study's (Opus) | **0.272** | 0.808 | 0.333 | 0.929 | **37 of 38** | 1.24 | 86 |
| Study's combination f, repeats 1 / 2 / 3 (26.7) | study's (Opus) | 0.292 / 0.274 / 0.265 | 0.779 (r1) | | | 37 of 38 | ~1.35 | |
| Current pipeline (`rooms19_v3_opus55`, r1) | study's (Opus) | 0.358 | 0.720 | | | 37 of 38 | | |
| **Wired, `room_call`** (`s29_replay_room_call_defaults`) | live run 3 | **0.319** | 0.783 | 0.333 | 0.919 | 35 of 38 | 1.24 | 85 |
| Current pipeline on the same input (27.1 replay) | live run 3 | 0.406 | 0.655 | | | 32 of 38 | 0.30 | |

The 30 interior rooms took the room call and exactly `x_property_front`, `x_shed` and
`x_mechanicals` the reconcile path, in both runs. Split by path (absolute count error summed
over the (room, label) pairs):

| Run | Interior (196 pairs) | Exterior (28 to 30 pairs) |
|---|---|---|
| Wired, study's input | 30 | 31 |
| Wired, live input | 30 | 42 |
| Study's room call at high effort, repeats 1 / 2 / 3 | 35 / 30 / 32 | |
| Study's combination, repeats 1 / 2 / 3 (exterior from `rooms19_v3_opus55`) | 35 / 30 / 32 | 31 / 32 / 27 |
| 27.1 replay (current path, live input) | 74 | 27 |

Reading: the interior rooms, the part this change replaces, reproduce the study (30 against
30 to 35). On the study's own per-photo input the whole room step reproduces section 26.7's
combination (0.272 against 0.265 to 0.292, the 37 of 38 ceiling). On the live input the MAE is
0.319, above the 0.26 to 0.29 expected, and all of the difference is the unchanged exterior
path: the 12-photo property-front group's windows (30 labelled) came back 10 in this repeat;
three more repeats of that one room through the same code gave 15, 21 and 21 (the
reconciliation call splits the 12 photos into two calls and keeps the larger count), so the
reconcile path's window count on that group moves by 10 from run to run. With a typical
repeat (21) the run is about 0.27. Its two extra missed rows (Movable Shed, Exterior Equipment
Concrete Pads) come from the shed group, which the live per-photo outputs describe without
`shed` or `concrete_pad`; the 27.1 replay on that input misses them too, the study's Opus
per-photo input does not.

### 29.3 Materials through the wired code

One repeat with both options on (`ROOM_STEP_MODE=room_call`, `MATERIALS_SECOND_LOOK=surface_crops`,
`SURFACE_SEGMENTER=local`) on the study's per-photo input (`s29_replay_room_call_crops_opus_input`).
The crops the room step asked were scored with `evals.predict_surface_crops score` (which now
reads a replay run's crops) against the per-photo labels (58 labelled surfaces), next to
section 26.3's Sonnet run:

| Source | Floor hit / exact (32) | Wall (12) | Wainscot (3) | Ceiling (11) | All hit / exact | Answered |
|---|---|---|---|---|---|---|
| Per-photo surfaces call, Opus (today) | 0.78 / 0.75 | 0.58 | 1.00 | 0.18 | 0.64 / 0.62 | 0.69 |
| Crops, wired | 0.66 / 0.62 | 0.83 | 0.67 | 0.64 | 0.69 / 0.67 | 0.74 |
| Crops, study (26.3, Sonnet low) | 0.66 / 0.62 | 0.83 | 0.67 | 0.64 | 0.69 / 0.67 | 0.74 |
| **Crops, else per-photo, wired** | 0.78 / 0.75 | 0.83 | 1.00 | 0.73 | **0.79 / 0.78** | 0.88 |
| Crops, else per-photo, study (26.3) | 0.78 / 0.75 | 0.83 | 1.00 | 0.73 | 0.79 / 0.78 | 0.88 |

The wired step asked the same 86 crops as the study's run (43 photos with a surface of at
least 2%), all answered, no room flagged, and reproduces 26.3 surface for surface. In the
rooms it changed 9 materials (wall finish, wainscoting, tile and ceiling finish objects). The
same run is a second repeat of the room call: count MAE 0.269, report rows 37 of 38 (only Door
& Window Guards, which no photo shows). $1.79 for the whole room step with crops (Opus room
calls, Sonnet exterior reconciliation, second looks and crops), 164 s with the local segmenter
on the CPU.

### 29.4 Tests

Settings (defaults, overrides, refused values and combinations); the room call in
`room_step` (path rule per room type, the request, the checks, the fallbacks, the errors that
propagate); a byte-for-byte regression of the reconcile path against a golden written by the
same scenarios on `main` before this change (`test_room_step_unchanged.py`; it fails on a
one-token change of the reconciliation call); the crop pipeline and both segmenters offline;
`reconcile_rooms` on Postgres in both modes, with every fallback, the account-error stop and
the budget stop, and the regression that rows and records at the defaults carry exactly their
previous fields; `evals.replay_rooms` offline; the whole workflow at `room_call`, alone and
with the crops (`tests/lifecycle/test_room_stage_options.py`); the production-defaults
lifecycle now pins both settings at their defaults. `ROOM_MATERIALS_MODEL`: the settings, the
second look on its own model in `room_step`, and `reconcile_rooms` with the reconciler on
gpt-6-luna and the look on Sonnet (a test that fails when the activity does not pass the
setting through).

### 29.5 To confirm on the second package (manual)

With the second client package's photos, per-photo run, room aliases and labels in the eval
data directory:

```bash
cd backEnd
ROOM_STEP_MODE=room_call python -m evals.replay_rooms --photos PHOTOS --per-photo PER_PHOTO \
    --aliases ALIASES --out $P/s29_second_room_call --repeats 3
python -m evals.replay_rooms --photos PHOTOS --per-photo PER_PHOTO --aliases ALIASES \
    --out $P/s29_second_reconcile --repeats 3
python -m evals.run --predictions $P/s29_second_room_call --repeats 3 --no-db
python -m evals.run --predictions $P/s29_second_reconcile --repeats 3 --no-db
```

then the same with `MATERIALS_SECOND_LOOK=surface_crops` and the crop score of 29.3
(`python -m evals.predict_surface_crops score --data-dir DATA --combine
wired=REPLAY_DIR,PER_PHOTO`: the scorer reads a replay run's crops directly). Switch
the defaults only if the room call still wins without the exterior rule being changed.

### 29.6 Spend, data and commands

Spend: $1.24 (room call, live input), $1.24 (room call, study's input), $0.19 (three repeats
of the property-front group), $1.79 (room call and crops, study's input), $0.0005 Replicate (one
segmentation); local segmentation $0. Predictions (private): `s29_replay_room_call_defaults`,
`s29_replay_room_call_opus_input`, `s29_replay_room_call_crops_opus_input`, with logs.

```bash
P=~/basis-eval-data/predictions
ROOM_STEP_MODE=room_call python -m evals.replay_rooms --photos ~/basis-eval-data/inputs/photos \
    --per-photo $P/vision_decomposed_v3_opus55 --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    --out $P/s29_replay_room_call_opus_input
ROOM_STEP_MODE=room_call python -m evals.replay_rooms --photos ~/basis-eval-data/inputs/photos \
    --per-photo $P/live_e2e_v070_defaults_perphoto --aliases ~/basis-eval-data/labels/room_aliases_v1.json \
    --out $P/s29_replay_room_call_defaults
ROOM_STEP_MODE=room_call MATERIALS_SECOND_LOOK=surface_crops python -m evals.replay_rooms \
    --photos ~/basis-eval-data/inputs/photos --per-photo $P/vision_decomposed_v3_opus55 \
    --aliases ~/basis-eval-data/labels/room_aliases_v1.json --out $P/s29_replay_room_call_crops_opus_input
python -m evals.run --data-dir ~/basis-eval-data --predictions $P/s29_replay_room_call_opus_input --no-db

## 30. SAM 3 throughput (2026-10-17)

Question: SAM 3 was 72 to 74% of a study's wall time in sections 23 and 27. How do we cut
that, and where should SAM 3 run? The decision document is `SAM3_HOSTING.md`; this section
has the measurements. All of them use the client study's 52 photos, each prompted as the
product step does: `object_labels` of the photo's labels from live run 3, the v3 phrase
set, 13 phrases a photo on average, threshold 0.25. Commands:
`repro/sam3_throughput.py`.

### 30.1 Where the time went

The share was Replicate's low-credit throttle, not SAM 3. Run 3 of section 23 ran at 6
creates a minute: its last localization finished 339 s after its last analysis. The 27.8
re-run was not throttled, and SAM 3 trailed the last analysis by 3 s. In both runs the
rooms gate waited for the last localization, then for the room step. When the throttle is
on, every second of it lands on the rooms gate.

### 30.2 Replicate: per call, concurrency, 429s, cost

Per prediction, from Replicate's own timestamps:

* **Queue** is 0.01 to 0.03 s when a worker is free. A cold worker takes 78 to 96 s; in
  27.8 the first localizations also waited 89 to 92 s. When the shared pool is busy the
  p95 rises to 13 to 56 s.
* **Compute** (billed `predict_time`) with full masks: p50 about 1.9 s, mean 1.9 to 5.1 s.

During this section the account had under $10 of credit. Every 429 said so: creates are
"reduced to 60 requests per minute with a burst of 5". That tier bounds any client to
about 1 photo a second.

Wall time for all 52 photos submitted at once (each value one repeat, repeats interleaved):

| Concurrency | Before (full masks) | 429s | After (compact output) | 429s |
|---|---|---|---|---|
| 1 | 362 / 175 / 192 s | 0 | 76 s | 0 |
| 3 | 175 / 65 / 80 s | 0 | 63 / 56 s | 13 / 0 |
| 6 | 82 / 64 / 139 s | 1 / 10 / 6 | 59 / 59 s | 23 / 22 |
| 12 | 102 / 58 / 118 s | 10 / 33 / 25 | 60 / 57 s | 39 / 35 |
| Replicate cost of the 52 | $0.098 to $0.251 | | $0.028 to $0.031 | |

The repeats differ because the pool warmed up over the night: the first full-mask repeat
had a predict mean of 5.1 s against 1.9 s later. After the change every concurrency of 3
or more is at the tier's bound. `LOCALIZER_CONCURRENCY` therefore defaults to 3: it is as
fast as 6 or 12 with the fewest 429s.

### 30.3 Batching and compact output

* **Images per prediction: one.** The model's input schema takes a single `image`.
* **Phrases per prediction: all of them already.** `prompts` is a list, so one call per
  photo is already the minimum number of predictions.
* **Output size: this is where the time and money were.** The default output is a dense
  `1 x H x W` mask per instance, written as JSON, one file per prompt. The input options
  `offset_masks` (each mask cut to its box, with the box's corner) and `split_output: false`
  (one file) change that. The pasted-back masks are identical pixel for pixel (50 of 50
  instances on one photo).

Over the 52 photos, compact output against full masks:

| | Full masks | Compact | Ratio |
|---|---|---|---|
| Result per photo | 20.6 MB | 0.75 MB | 27x smaller |
| Download | 1.0 s | 0.24 s | 4x shorter |
| Billed predict time, mean (warm runs) | 2.1 s | 0.57 s | 3.7x shorter |
| Cost per study | about $0.11 | $0.03 | |

The predict time falls because the model spends most of its "predict" time writing JSON.
The localizer now asks for compact output (`model_input`). It also retries a result file
that answers 404 right after its prediction succeeded: this happened once in 52 photos,
and that photo was recorded as failed before the change.

### 30.4 The rooms gate no longer waits for SAM 3

Workflow patch `localize-background-v1` makes three changes:

* The photo stage is done once every photo is analyzed and the room step has run, so the
  rooms review opens then. No step after the gate reads SAM 3's output. Each photo's masks
  appear in the review when its localization is recorded; the objects endpoint already
  reports the per-photo status.
* A selection on a photo that has no localization yet answers 409 `localization-pending`.
* A rejection at any gate waits for the localizations still running, because the next
  revision carries the objects over. So does the drain after a failed extraction, and the
  export waits until every localization is recorded.

`ROOMS_GATE_WAITS_FOR_LOCALIZATION=true` restores the wait. Executions recorded before the
patch replay without it.

Tests, in `tests/lifecycle/test_localize_background.py`, with one localization held by the
test:

* the gate opens while the held localization is still running;
* the export waits for it;
* the setting restores the wait;
* a rooms rejection waits for it and carries its objects into revision 2;
* at most `localize_concurrency` localizations run at once.

The gate test fails with the background mode turned off, and the concurrency test fails
with the old semaphore.

### 30.5 Arrival replay: SAM 3 at the workflow's pace

In the workflow SAM 3 is fed one photo at a time, as each analysis finishes, not 52 at
once. The 27.8 run's analysis completion times (15 to 358 s) were replayed against each
target, every photo localized at the second its analysis had finished:

| Target | Lag after each analysis (mean / max) | Done after the last analysis | 429s | Cost |
|---|---|---|---|---|
| Replicate, full masks, c=4 (before) | 4.2 / 12.5 s | 2.4 s | 0 | $0.103 |
| Replicate, compact, c=3 (after) | 1.6 / 2.8 s | 1.4 s | 0 | $0.029 |
| Cloud Run L4, compact, c=3 | 2.0 / 3.6 s | 1.4 s | 0 | $0.030 busy time |

All three were warm (the cold boot of 30.2 is in addition). At the workflow's pace,
Replicate's tier is not the bound, even under $10.

### 30.6 Study wall time, before and after

These are projections from recorded runs. Each recorded run's activity timings are kept,
and the gate is moved to "last analysis, then the room step". The engineer's review time
is excluded, since the gates were approved at once. No new live end-to-end run was made:
it needs the OpenAI and Anthropic accounts, which this session did not check.

| Recorded run | Account state | Rooms gate before | Rooms gate after | Completed before | Completed after |
|---|---|---|---|---|---|
| 27.8 (branch of PR 49) | not throttled | +397 s | +394 s | 596 s | about 593 s |
| 23, run 3 | throttled, 6 a minute | +685 s | +342 s | 874 s | about 650 s |

* **Unthrottled** (27.8), the change is a few seconds: SAM 3 already trailed the analysis
  by 3 s. The study is bound by the photo analysis (358 s), the room step (36 s) and the
  assets (about 190 s).
* **Throttled** (run 3), the rooms gate opens 343 s (50%) earlier. The completion moves
  less because the export still waits for the throttled SAM 3 (about 649 s).

SAM 3's share of the time to the rooms gate is now 0 in both cases; its share of the
completed run is 0 unthrottled. Its cost per study is $0.03, down from $0.10 to $0.25.

### 30.7 Hosting

The public Cog image was deployed to Cloud Run as `staging-sam3` in us-east4, with one
L4, scale to zero and authentication on. us-central1 had no GPU quota for the project.

| | Replicate, compact | Cloud Run L4 |
|---|---|---|
| 52 photos at once | 56 to 63 s at c=3 (rate tier) | 88 s at c=1, 40.5 s at c=3 (3 instances) |
| Predict per photo, mean / p95 | 0.57 / 0.85 s (L40S) | 1.25 / 1.9 s (L4) |
| Cold start | 78 to 96 s | 32 s (two 409s while Cog loads; retried) |
| Cost of a lone study | $0.03 | $0.03 busy, plus 6 to 10 min of idle billing per instance used ($0.10 to $0.17 each) |

AWS was not deployed: the configured account's G-instance quota is 0 vCPUs. Its prices
and shape are in `SAM3_HOSTING.md`.

**Recommendation: Replicate**, with compact output, the gate change, `LOCALIZER_CONCURRENCY`
3 and credit kept at $20 or more (auto-reload). The cheapest throughput fix is credit:
under $5 the account drops to 6 creates a minute. Cloud Run stays one setting away
(`LOCALIZER_URL`, `LOCALIZER_GOOGLE_AUTH`) for volume, residency, or if the tier cannot be
kept.

Spend for this section:

* Replicate: about $2.00 (19 sweeps, 2 replays, a smoke run and probes; 429s are not billed).
* Cloud Run: about $0.95 of instance time, idle tails included (Cloud Monitoring
  `billable_instance_time`: 2,562 s by the cold-start test, plus that instance's tail).
* Cloud Build: under 2 minutes.
* No language-model calls.

Commands (from `backEnd/`; the photos and labels are private):

```bash
# a concurrency sweep point (add --full-masks for the code before this section)
uv run --no-sync python ../docs/review_2026-10-15/repro/sam3_throughput.py \
    --photos ~/basis-eval-data/inputs/photos \
    --components ~/basis-eval-data/predictions/live_e2e_v070_defaults/photo_components.json \
    --concurrency 3 --out OUT
# the 27.8 arrival replay (ARRIVALS: {file: seconds when its analysis finished})
uv run --no-sync python ../docs/review_2026-10-15/repro/sam3_throughput.py ... \
    --arrivals ARRIVALS.json --concurrency 3 --out OUT
# Cloud Run
LOCALIZER_TOKEN=$(gcloud auth print-identity-token) uv run --no-sync python \
    ../docs/review_2026-10-15/repro/sam3_throughput.py ... --target url --url SERVICE_URL
```
