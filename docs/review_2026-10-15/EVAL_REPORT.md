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
| Room inventory, multi-photo rooms | gpt-6-luna (31; claude-sonnet-5-5 before) | `room_inventory/v3` | low | `ROOM_RECONCILE=true`, `ROOM_PROMPT_VERSION=v3`, `ROOM_MODEL`, `ROOM_REASONING_EFFORT` | 16.5, 28.2 | room count MAE 0.72 to 0.43 (sol input), 0.34 (Opus input); 28.2: recommended split, gpt-6-luna reconciling and Sonnet for the second look (materials decide IRS buckets): `ROOM_MODEL=gpt-6-luna`, the second look staying on `ROOM_MATERIALS_MODEL` (29.1) | `ROOM_RECONCILE=false` (per-photo rows become assets) |
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

## 31. Room reconciler on gpt-6-luna (2026-10-17)

Section 28.2 measured gpt-6-luna as the room-step reconciler within the noise of Sonnet 5.5
(paired over the 33 rooms, luna minus Sonnet +0.003 [-0.025, +0.030] on count error) but
worse as the material reader (14 of 20 labelled surfaces against 17), and recommended the
split: luna reconciles, Sonnet keeps the material second look. PR 56 added
`ROOM_MATERIALS_MODEL`, so the split is expressible. Before changing the default, the exact
production configuration was run again: luna at low effort with the reconciler's production
output budget (4,000 tokens; the section 28.2 runs used 6,000), Sonnet 5.5 low for the
second look, the same Opus per-photo input as section 16, three repeats, scored by
`evals.run` on the current labels.

| Room step | Room count MAE | Exact | Rooms exact | Label F1 | Report rows (of 38) | Materials present | Cost per study |
|---|---|---|---|---|---|---|---|
| Sonnet reconciles and looks (default before) | 0.392 | 0.678 | 5 of 33 | 0.872 | 30, the same rows | 1.000 | $0.31 |
| luna reconciles, Sonnet looks (default now) | 0.401 | 0.659 | 4 of 33 | 0.860 | 30, the same rows | 1.000 | $0.14 |

No answer was truncated or rejected at the 4,000-token budget, the two configurations
evidence and miss exactly the same report rows, and the count difference is inside section
28.2's interval. The rows here are 30 of 38 rather than 37 because this per-photo input
predates the section 19 vocabulary; both rows use the same input. `ROOM_MODEL` now defaults
to `gpt-6-luna`; `ROOM_MODEL=claude-sonnet-5-5` restores the previous behaviour. Spend: $0.43.
Predictions: `rooms_split_luna_sonnet_prod` (private).

## 32. Load and capacity (2026-10-17)

Question: how much load does Basis take before launch, what breaks first, and what do the
model providers allow? The method (tool, metrics, SLOs, commands) is
`docs/perf/LOAD_TESTING.md`; the harness is `tooling/load/` and
`backEnd/tests/load/`. Everything below ran on fake providers except 32.8 (one capped
real-provider probe). The run summaries are in `docs/perf/runs/`.

Answer, in short:

- **The first bottleneck was a defect, not a limit**: every POST and DELETE held two database
  connections at once, so five concurrent mutations on one API process deadlocked its pool and
  were answered 503 (32.3). The same class of defect, a connection held through seconds of
  work, was in the worker twice: `process_assets` (minutes of classification) and
  `ingest_appraisal` (embedding). With five to ten concurrent studies they starved the
  worker's pool, failed and retried attempts (each retry paying its model calls again), and
  held studies failed (32.4). All three are fixed in this branch, with regression tests that
  fail on the old code.
- **Capacity at the production defaults**: one worker process completes about **6.6 studies an
  hour**, whatever the load, because `LLM_CONCURRENCY=4` runs four model calls at a time and a
  study is about 2,200 call-seconds; N concurrent studies all finish after about N x 9 minutes
  (32.5). Temporal does not show it until its slots fill at 25 studies.
- **Tuned**: `LLM_CONCURRENCY=64` and `CLASSIFY_CONCURRENCY=16` give about **55 an hour**, then
  `LOCALIZER_CONCURRENCY=3` (SAM 3) binds; with 12, **104 an hour**, bound by the 64-call cap
  per process (32.6). The providers are far from binding: our accounts allow about **337
  studies an hour**, the tightest being Anthropic Sonnet input tokens (classifier), then
  Replicate's 600 creates a minute (692 an hour; 69 while the credit is under $10, which is
  where the account is now) (32.9). A capped real probe of three studies saw throttling only
  from Replicate (32.8).
- **The read path**: Postgres-backed reads stay under 160 ms p95 at 200 readers on one API
  process; the status poll of finished studies does not (p95 4.4 s at 200), because each one
  is a Temporal query that the worker answers by replaying the study's history (32.7).

### 32.1 Setup

- **Stack**: `tooling/load/compose.load.yml` (Postgres 16 + pgvector with
  `pg_stat_statements`, Temporal dev server 1.32), and as host processes one API
  (`uvicorn`, one process, like one Cloud Run instance), one worker and the relay
  (`tooling/load/stack.sh`). One laptop (Apple silicon), so absolute CPU numbers are the
  laptop's; the shape of the curves is what transfers.
- **Load**: Locust (`StudyEngineer`) plays the frontend for whole studies, each user its
  own engineer: create, 53 x (register, upload to the signed URL, confirm), start, poll
  every 5 s, read each gate's screen, approve. A step of N studies starts N engineers at
  once (a burst: the worst case for every shared resource) and ends when all N finished.
- **The worker on fake providers** (`fake_worker.py`): the production worker with the
  providers, Azure DI and SAM 3 replaced by fakes that sleep the latencies of the 550 model
  calls, 52 SAM 3 predictions and the Azure DI analyses of the live study (27.8), behind
  the real provider pool. The study is synthetic: 52 fixture photos named like the client
  package (33 rooms), the synthetic URAR.
- **Configuration**: the production defaults of the day (main at v0.10.0). Section 31 (PR 58,
  merged while these runs went on) has since moved the room reconciler from Sonnet to
  gpt-6-luna. The room step is about 2% of a study's model call-seconds (9 of 550 calls), so
  the capacity numbers below stand; the recorded cost per study falls by about $0.2 (the
  fake's `room_inventory` cost was $0.22 on Sonnet), and the Sonnet ceiling of 32.9 rises from
  337 to 348 studies an hour (54 k fewer Sonnet input tokens a study; `ceilings.py
  --room-model gpt-6-luna`).
- **Time scale**: steps ran at `LOAD_LATENCY_SCALE=0.1` (and the clients' `--time-scale
  0.1`): ten times faster, same proportions. "Real" times below are measured / 0.1.

### 32.2 Calibration: one fake study against the live one

| | Live study (27.8) | Fake worker, scale 1.0 | Fake, scale 0.1 (x10) |
|---|---|---|---|
| Appraisal gate open | +93 s | +121 s | +130 s |
| Rooms gate open | +397 s | +382 s | +362 s |
| Assets gate open | +587 s | +558 s | +584 s |
| Completed | 596 s | 564 s | 609 s |
| Model calls (photo / room + looks / classifier / fallback) | 156 / 34 / 359 / 1 | 156 / 32 / 334 / 1 | |
| Rooms / assets (classifier share) | 33 / 280 (55%) | 33 / 310 (54%) | |
| Verified citations | 379 | 364 | |
| Recorded cost | $6.75 ($6.43 with surfaces on gpt-6-luna) | $6.25 ($6.06 models + $0.19 fake SAM 3) | |

The fake study is within 5% of the live one end to end (the appraisal gate opens later
because the fake samples Azure DI from the 72 to 82 s range). Inside, `analyze_photo`
averaged 32 s against about 13 s of its own calls, and the photo stage kept 3.8 model calls
in flight on average, live and fake alike: `LLM_CONCURRENCY=4` already binds one study
(32.5). At scale 0.1 the same study takes 609 s x 10 against 564 s at real speed (8% longer):
the compression holds, and what it adds (uncompressed HTTP, Postgres, Temporal work)
makes the compressed steps slightly pessimistic.

### 32.3 First bottleneck: every mutation held two database connections (fixed)

The first step with more than one study failed before any study started: five studies
created at once on a freshly started API were all answered **503** after 10 s ("database
pool checkout timed out"); ten at once, 10 of 10. One `POST /v1/studies` on an API with
`DB_POOL_SIZE=1` is a 503 by itself, while a `GET` works: a mutation needs two
connections at the same time.

Traced (pool checkout events with the session that took them): the request's own
transaction (`SessionDep`) was still open, holding connection 1, when `IdempotentRoute`
stored the response for the `Idempotency-Key` in the store's own transaction
(connection 2). FastAPI closes function-scoped dependencies only after the route handler
returns, and the route class stored the response inside the handler. So every POST and
DELETE held two connections, and `DB_POOL_SIZE` concurrent mutations on one API process
(5 by default) each held one and waited for a second: a pool deadlock until
`pool_timeout`. This is the classic nested-checkout deadlock (HikariCP's pool sizing
note: with C connections per task the pool needs T x (C - 1) + 1 for T tasks; only C = 1
is safe at any concurrency). It is not a load-test artefact: Cloud Run sends up to 80
concurrent requests to one instance, and a browser that uploads photos six at a time
would hit it with a single engineer.

The same ordering was also a correctness bug: the stored 201 committed before the request's
own data, so a commit that failed afterwards left a 201 to replay for a study that did not
exist.

Fix (`basis/api/idempotency.py`): the response is stored from a callback on FastAPI's
function-scoped exit stack, registered before the dependencies are solved, so it runs
after `SessionDep` commits (or, on a failed commit, releases the key) and before the
response is sent. Every mutation now holds one connection at a time. Tests
(`tests/api/test_connection_use.py`, all three fail on the old code): one connection per
mutation; 8 concurrent creates on a pool of 2 all 201 within the pool timeout; a failed
commit answers 500 and releases the key, and the retry with the same key creates the study
once.

### 32.4 Second and third: connections held through model calls and embedding (fixed)

With the API fixed and every worker setting at its default, five concurrent studies ran
into the worker's pool. `process_assets` ran the whole classification (334 model calls,
about three minutes at real speed) inside `run_once`'s transaction: one pooled connection
and the activity key's row lock held throughout, while each classifier search needs a
second connection from the same pool (`DB_POOL_SIZE=5`, no overflow). When five studies
reached the assets stage together, their five transactions held the pool and the searches
timed out ("QueuePool limit of size 5 overflow 0 reached"). Each failed attempt is retried
from scratch, paying for its classification again, and a study whose three attempts fail is
held failed at `assets_processing` until `POST /retry`:

| Concurrent studies (defaults, before this fix) | Completed | Held failed at assets | Failed `process_assets` attempts | Studies/hour (real) |
|---|---|---|---|---|
| 5 | 4 | 1 | 9 | 4.1 |
| 10 | 4 | 6 | 30 | 2.7 |
| 25 | 0 by the clients' 18-minute cutoff (5 still running) | 23 | 105 timeouts, also in `reconcile_rooms`, `transition` and `record_failure` | - |

The money of a failed attempt is invisible: `process_assets` writes its `llm_calls` rows in
the transaction that rolled back, so a held study shows $2.36 recorded (photos and rooms)
whatever its failed attempts spent on the classifier, and the study budget never sees that
spend (follow-up 32.12).

Fix (`basis/activities/process_assets.py`), the pattern `analyze_photo` and
`extract_fields` already follow: the revision is read in a short session, the model
classifies with no connection held, then the assets, takeoffs, costs, citations and calls
are written and the key completed in one transaction (a revision that already has assets,
a replay or a concurrent attempt that won the key, is still returned as stored without
recomputing). Test: `test_no_connection_is_held_while_the_model_classifies` (0
connections checked out at each model call; 1 before the fix); the 23 existing
`process_assets` tests and the lifecycle and database-outage suites pass unchanged.

The same pattern, found by the real-provider probe (32.8: a transaction open for 26 s):
`ingest_appraisal` downloaded, parsed and embedded the appraisal inside `run_once`'s
transaction. The fake worker had used the hash embedder, so it never showed; with the
production embedder (`LOAD_EMBEDDER=real`, local CPU, no spend) and 10 studies starting
together, the ingests held the pool for 8 s each and 21 attempts timed out waiting for a
connection, 17 of them `analyze_photo` (each retried photo pays its model calls again). Fix:
`basis.retrieval.ingest` splits `ingest_documents` into `prepare_documents` (parse, chunk,
embed; no store) and `write_prepared` (every write, unchanged order); the activity prepares
with nothing checked out and writes the version in `run_once`'s transaction, raising a
failed preparation inside it so the key records the failure as before. Test:
`test_no_connection_is_held_while_the_appraisal_is_embedded` (1 before). The same step
after the fix: 0 failed attempts, longest transaction 0.7 s (8 s before).

| Real embedder, 10 studies (tuned settings of 32.6) | Failed attempts | Longest transaction | Studies/hour |
|---|---|---|---|
| Before the `ingest_appraisal` fix | 21 (pool timeouts) | 8.0 s | 64.8 |
| After | 0 | 0.7 s | 72.3 |

### 32.5 Capacity at the production defaults (after the fixes)

The API and `process_assets` fixes (the hash embedder of these steps never held the pool in
`ingest_appraisal`), every setting at its default (`LLM_CONCURRENCY=4`,
`CLASSIFY_CONCURRENCY=4`, `PHOTO_CONCURRENCY=5`, `LOCALIZER_CONCURRENCY=3`, `DB_POOL_SIZE=5`),
one worker process:

| Run | Studies | Completed | Held failed | Failed attempts | Makespan (min, real) | Studies/hour | Run p50 (s) | Run p95 (s) | Appraisal gate p95 (s) | Assets gate p95 (s) | Activity slots max / backlog max | Schedule-to-start <= 1 s | PG conns max / idle in tx | Worker CPU % p95 / max | Worker RSS MB | Cost / completed study |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c-s1 | 1 | 1 | 0 | 0 | 10.2 | 5.9 | 609 | 609 | 130 | 584 | 7 / 0 | 100% | 7 / 1 | 20 / 29 | 415 | $6.07 |
| c-s5 | 5 | 5 | 0 | 0 | 45.8 | 6.6 | 2,745 | 2,746 | 288 | 2,737 | 32 / 0 | 100% | 11 / 3 | 21 / 51 | 463 | $6.07 |
| c-s10 | 10 | 10 | 0 | 0 | 90.9 | 6.6 | 5,451 | 5,455 | 489 | 5,435 | 62 / 0 | 100% | 11 / 5 | 24 / 67 | 430 | $6.07 |
| c-s25 | 25 | 25 | 0 | 0 | 226.9 | 6.6 | 13,590 | 13,602 | 1,545 | 13,593 | 100 / 102 | 20% | 11 / 6 | 25 / 108 | 537 | $6.07 |

API latency in the same steps, with the tuned steps of 32.6 for comparison (the clients poll
every 0.5 s at scale 0.1, so the API sees ten times the real polling rate of N engineers):

| Run | Polls | Poll p50 ms | Poll p95 ms | Poll p99 ms | Reads | Reads worst p95 ms | Reads worst p99 ms | Mutations | Mutations worst p95 ms | Mutations worst p99 ms | 429s | Other errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c-s1 | 111 | 31 | 49 | 95 | 64 | 65 | 65 | 112 | 61 | 76 | 1 | 0 |
| c-s5 | 2,533 | 34 | 41 | 91 | 315 | 100 | 100 | 558 | 77 | 190 | 5 | 0 |
| c-s10 | 9,987 | 39 | 49 | 84 | 642 | 120 | 120 | 1,120 | 110 | 210 | 10 | 0 |
| c-s25 | 62,341 | 42 | 66 | 100 | 1,622 | 250 | 290 | 2,825 | 180 | 240 | 50 | 0 |
| b-s25 | 7,333 | 23 | 90 | 200 | 1,621 | 100 | 150 | 2,825 | 170 | 220 | 50 | 0 |
| l12-s25 | 4,207 | 30 | 52 | 130 | 1,625 | 95 | 130 | 2,834 | 180 | 250 | 59 | 0 |

Reading:

- **Every study completes, and throughput stops at about 6.6 studies an hour per worker
  process from 5 studies on.** A study is about 2,200 seconds of model calls (32.2); the
  provider pool runs 4 at a time per process, so one process finishes at most
  4 x 3,600 / 2,200 = 6.5 studies an hour, whatever the number of studies waiting. More
  studies only wait longer: N concurrent studies finish together after about N x 9 minutes
  (the pool serves them all at once, so the first does not finish early).
- **This is the bottleneck of the system as configured, and Temporal does not show it.**
  The calls wait in the pool's semaphore inside the worker: the activity has its slot and
  has started, so activity schedule-to-start stays under 100 ms and the task-queue backlog
  at 0 while `analyze_photo` takes 32 s (1 study), 160 s (5), 315 s (10) and 620 s (25) of
  execution, real time, for about 13 s of its own calls. The slots fill only at 25 studies (100 of 100 used), where
  photo activities queue behind the pool while holding slots. The signal to alert on is
  activity execution time against its intrinsic time, which needs a pool-wait metric
  (follow-up).
- Nothing else is near a limit at this rate: the worker's CPU is 20-25% (p95) of one core
  even compressed ten times, Postgres CPU under 17%, 11 connections, no transaction open
  longer than about a second; the
  slowest statements are the inserts of photo objects and components (0.1 ms mean). The API
  answers the status poll at p95 41-49 ms and every other read under 120 ms at ten times the
  real poll rate.
- It is a setting, not a ceiling of the design: the next section raises it.

### 32.6 Raising the pool: the next limits

`LLM_CONCURRENCY=64` (its maximum: `Settings` caps it at 64; 256 is refused at startup) and
`CLASSIFY_CONCURRENCY=16`, everything else at its default, one worker process. `blim`, `h`
and `l12` also emulate our accounts' rate limits (`LOAD_PROVIDER_LIMITS`, section 3 of
LOAD_TESTING.md); `h` and `l12` run at scale 0.5 instead of 0.1:

| Run | Studies | Completed | Held failed | Failed attempts | Makespan (min, real) | Studies/hour | Run p50 (s) | Run p95 (s) | Appraisal gate p95 (s) | Assets gate p95 (s) | Activity slots max / backlog max | Schedule-to-start <= 1 s | PG conns max / idle in tx | Worker CPU % p95 / max | Worker RSS MB | Cost / completed study |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| b-s5 | 5 | 5 | 0 | 0 | 5.7 | 52.3 | 333 | 344 | 123 | 282 | 45 / 0 | 100% | 11 / 4 | 58 / 58 | 473 | $6.07 |
| b-s10 | 10 | 10 | 0 | 0 | 11.1 | 54.2 | 653 | 664 | 145 | 429 | 87 / 0 | 100% | 11 / 5 | 78 / 86 | 508 | $6.07 |
| b-s25 | 25 | 25 | 0 | 0 | 26.9 | 55.7 | 1,586 | 1,608 | 290 | 979 | 100 / 123 | 56% | 11 / 7 | 102 / 104 | 519 | $6.07 |
| blim-s25 | 25 | 25 | 0 | 0 | 26.9 | 55.7 | 1,586 | 1,606 | 282 | 965 | 100 / 129 | 53% | 11 / 6 | 100 / 103 | 515 | $6.07 |
| h-s25 | 25 | 25 | 0 | 0 | 26.1 | 57.4 | 1,536 | 1,566 | 208 | 848 | 100 / 125 | 41% | 12 / 5 | 39 / 81 | 532 | $6.15 |
| l12-s25 | 25 | 25 | 0 | 0 | 14.4 | 104.0 | 854 | 864 | 218 | 858 | 100 / 126 | 25% | 11 / 5 | 42 / 88 | 535 | $6.15 |

Reading:

- **Eight to nine times the throughput from one setting**: 52 to 57 studies an hour per
  worker process instead of 6.6, every study completed, no failed attempt, the same $6.07
  recorded per study. At 25 concurrent studies the assets gate opens at p95 14 to 16 minutes
  (defaults: 3.8 hours), inside the proposed 20-minute objective.
- **The providers do not throttle it**: with our accounts' limits emulated, 25 studies at
  once never spent a bucket (0 emulated 429s). At 57 studies an hour the classifier sends
  about 1.7 M Sonnet input tokens a minute on average against our 10 M.
- **Worker CPU is not the limit either.** At scale 0.1 the worker sat at one core (p95
  102%), but the compression inflates the system's own work ten times; at scale 0.5 the
  same step used 39% (p95) and finished as fast (57.4 against 55.7 studies an hour). At
  real speed the worker would use about 20% of a core at this rate.
- **What binds is SAM 3: `LOCALIZER_CONCURRENCY=3` per process.** 25 studies are 1,300
  predictions; at 3 in flight and about 3.6 s each that is 26 minutes, which is the
  makespan (a study exports only once every photo is localized). The waiting localizations
  also hold Temporal activity slots (up to 3 per study, 75 of the worker's 100 at 25 studies), so
  `analyze_photo` tasks queue in Temporal: the activity backlog reaches 125 and only about
  40% of activities start within 1 s. This is the first time Temporal itself shows the
  load, and it is not a Temporal limit.
- **Checked by changing only that setting** (`l12`: `LOCALIZER_CONCURRENCY=12`, scale 0.5):
  104 studies an hour, the makespan falls from 26 to 14 minutes, `localize_photo` from 37 s
  to 1.9 s, and completion follows the assets gate within seconds. 104 is the 64-call
  pool's own bound (64 x 3,600 / 2,200 = 105): the provider pool binds again, and the
  Temporal slots stay full as its symptom (activities wait in the pool holding slots).
  Twelve localizations in flight are about 90 Replicate creates a minute at this rate:
  inside the documented 600 a minute, above the under-$10 tier's 60.
- At 5 and 10 studies the rate is the study's own length (about 5.5 minutes each with
  nothing waiting), not a capacity: those steps did not fill the pool.
- The model spend is $6.055 per study in every step, before and after tuning: nothing is
  retried under load. (The fake SAM 3 prices its scaled sleep, so the recorded SAM 3 cost
  varies with the time scale, $0.02 to $0.19 a study; the real one is about $0.03.)
- The 3 was chosen in section 30 for Replicate's under-$10 tier (60 creates a minute,
  burst 5). With the account above $10 the documented limit is 600 a minute, and the fake's
  3.6 s per prediction is the 27.8 timing; compact output (section 30.3) is faster, so the
  real headroom at 3 is somewhat larger than shown here.

### 32.7 The read path: engineers reviewing and polling

`ReviewReader` users on one API process (one Cloud Run instance) over the studies the steps
above created (25 engineers, completed studies), 3 minutes per step, wait 2 to 5 s between
actions (a status poll, opening a study, paging its assets, rooms, fields and evidence,
opening photos' objects). Readers share the 25 engineers' identities, so the per-engineer
read limit was raised for this test only (`RATE_LIMIT_READS_PER_MIN`); the worker ran idle
(no study in flight):

| Readers | Requests/s | Status poll p50 / p95 / p99 ms | Other reads, worst p95 ms | List p95 ms | API CPU p95 | Idle worker CPU p95 | Errors |
|---|---|---|---|---|---|---|---|
| 25 | 11 | 130 / 240 / 540 | 97 | 120 | 14% | 34% | 0 |
| 100 | 43 | 200 / 640 / 1,600 | 87 | 410 | 39% | 104% | 2 connection resets |
| 200 | 75 | 1,700 / 4,400 / 5,100 | 160 | 930 | 71% | 129% | 9 connection resets (0.07%) |

- **Every read that comes from Postgres stays fast** at 200 readers and 75 requests a
  second on one process: assets, rooms, fields, evidence and objects p95 86 to 160 ms; the
  API process at 71% of a core (p95), Postgres at 7 connections. One instance serves
  about 200 reviewing engineers; Cloud Run adds instances beyond that.
- **The status poll does not**: `GET .../workflow` is p95 240 ms at 25 readers,
  640 ms at 100 and 4,400 ms at 200, and it took the idle worker to 1.3
  cores. The route asks Temporal for the execution (`describe`) and then queries the
  workflow (`status` query), and a query is answered by a worker. A completed study's
  workflow is no longer in the worker's cache, so each query replays its history (760
  events, 164 KB) on the worker: about 75 ms of the worker's CPU per poll at every reader
  count (2.8, 10.8 and 17.7 polls a second), so the cost is linear in the poll rate. During the study
  steps the same route was p95 41 to 66 ms at 46 polls a second, because running workflows
  are cached. So the read path's first bottleneck is polling finished studies, and it lands
  on the process that runs the studies. Proposed (follow-up): answer the status of a closed
  execution from Postgres (the study row already has its stage and status) instead of a
  query, and query only running ones.
- `GET /v1/studies` (an engineer's list) is p95 930 ms at 200 readers while its
  p50 stays at 43 ms: requests queued behind the slow polls on the one event loop.

### 32.8 One real-provider probe

Three concurrent studies of 20 synthetic photos each on the real providers, every model
default unchanged (gpt-6.1-sol decomposed with Opus fusion, gpt-6-luna surfaces and
extraction, Sonnet room step and classifier, Azure DI, SAM 3 on Replicate), the pool
raised as in 32.6 (`LLM_CONCURRENCY=64`, `CLASSIFY_CONCURRENCY=16`; `LOCALIZER_CONCURRENCY`
at its default 3) so the providers see concurrency, and `STUDY_BUDGET_USD=4` as the hard cap
of each study (`tooling/load/stack.sh up DIR real`; worker log: `llm_providers=openai+anthropic
localizer=ReplicateSam3Localizer azure_di=True`):

- **All three completed in 159 s** (appraisal gate +48 s, assets gate +64 to +74 s). Spend
  **$3.69 recorded** ($1.23 a study: Sonnet classifier $1.58, Opus fusion $1.39, sol $0.41,
  Sonnet room step $0.25, luna $0.02, SAM 3 $0.04) plus three Azure DI analyses of the
  synthetic URAR (not recorded; cents). Under $4 of the $15 allowed.
- **Throttling: only Replicate.** OpenAI and Anthropic answered every call (0 429s with up to
  about 45 photo calls and 48 classifier calls in flight). Replicate answered 10 creates with
  429 (retried after 5 to 15 s); the 60 predictions ran at one a second (60 in 59 s), which is
  what section 30 measured for the under-$10 tier (60 a minute, burst 5), so the account's
  credit is probably below $10 again (the localizer logs only the status, not Replicate's
  message).
- **Latency under concurrency is no worse than the single live study**; the profile the fakes
  sample is conservative:

| Call | Live study, alone (27.8): p50 / p95 ms | Probe, 3 concurrent: p50 / p95 ms |
|---|---|---|
| countables, gpt-6.1-sol | 11,325 / 19,541 | 5,891 / 8,156 |
| countables fusion, claude-opus-5-5 | 6,501 / 10,975 | 4,237 / 6,919 |
| surfaces, gpt-6-luna (live: sol) | 5,947 / 15,806 | 5,674 / 7,156 |
| classify, claude-sonnet-5-5 | 1,480 / 3,710 | 1,693 / 2,921 |
| room inventory, claude-sonnet-5-5 | 4,715 / 9,125 | 3,677 / 4,511 |

  (The synthetic photos are simpler than the client's, which likely explains part of the
  faster per-photo calls.)
- It found the third held transaction (32.4): `ingest_appraisal` 28 s with the real
  embedder, the longest open transaction 26 s.
- The status polls at real speed met six connection errors in 93 ("remote end closed",
  "reset by peer"): uvicorn closes an idle keep-alive connection after 5 s by default and the
  client polls every 5 s, so it sometimes reuses a connection the server is closing (the
  compressed steps poll every 0.5 s and never meet it). A browser retries such a request; still,
  `--timeout-keep-alive` above the poll interval (and, behind Google's front end, above its
  idle timeout) removes it. Tuning item, not changed here (32.12).

### 32.9 Provider ceilings

Our accounts' limits, read from the response headers (`provider_limits.py`, one request
of a few tokens per model; `docs/perf/runs/provider_limits_2026-10-17.json`), against
what one live-shaped study takes of each (the 550 calls of the 27.8 profile, surfaces on
gpt-6-luna; `ceilings.py`):

| Provider / model | Per study | Our limit | Ceiling (studies/hour) | Binding |
|---|---|---|---|---|
| Anthropic `claude-sonnet-5-5` (room step, classifier) | 393 requests, 1.78 M input, 74 k output tokens | 10,000 RPM, 10 M ITPM, 2 M OTPM | **337** | input tokens |
| Replicate SAM 3 | 52 creates | 600 creates/min (60 under $10 credit) | 692 (**69** under $10) | creates |
| OpenAI `gpt-6.1-sol` (countables) | 52 requests, 153 k tokens | 10,000 RPM, 4 M TPM | 1,565 | tokens |
| Anthropic `claude-opus-5-5` (count fusion) | 52 requests, 240 k input tokens | 10,000 RPM, 10 M ITPM, 2 M OTPM | 2,498 | input tokens |
| OpenAI `gpt-6-luna` (surfaces, extraction) | 53 requests, 128 k tokens | 10,000 RPM, 10 M TPM | 4,704 | tokens |
| Azure Document Intelligence | 1 analyze | 15 analyze/s per resource | 54,000 | analyze/s |

- The tightest provider is Anthropic on Sonnet input tokens, about **337 studies an hour**,
  because the classifier sends 4,700 uncached input tokens per call (the same system prompt
  and tool definitions every time). No request sets `cache_control`; cached tokens do not
  count toward Anthropic's ITPM, so prompt caching on the classifier would raise this
  ceiling several times and cut its cost (follow-up). Anthropic and OpenAI are both on
  tiers far above our use: one worker at the defaults sends at most four calls at a time.
- These are sustained rates. The classification of one study is concentrated in two to
  three minutes, so a burst of studies that classify together meets the limit sooner:
  at the live classification speed (about 0.55 M input tokens a minute per study) 10 M ITPM
  is about 18 studies classifying in the same minute, and fewer with a faster classifier
  (32.6 ran 25 at once on the emulated limits without a 429).
- OpenAI counts the tokens a request actually uses, not its `max_completion_tokens`
  (measured: a sol request with a 6,000-token budget took 4 to 17 tokens off
  `x-ratelimit-remaining-tokens`), so the 6,000-token reasoning budget costs no TPM.
- Replicate's under-$10 throttle (EVAL_REPORT 30) is the one limit we can fall into by
  accident: keep the credit auto-reloaded above $20.

### 32.10 Uploads are throttled by the per-engineer limit

One study's upload is 108 mutations (create, 53 x register and confirm, start); the
per-engineer limit is 60 mutations a minute (`RATE_LIMIT_MUTATIONS_PER_MIN`, S1). So every
52-photo study took at least 61 s to upload and met at least one 429 in every step above,
at 1 study as at 25; the system answered each request in about 15 ms. Five engineers
uploading at once, the limit only changed (`upload-only` steps, scale 1.0):

| Mutation limit per engineer | Upload of 53 documents (p50 / max) | 429s |
|---|---|---|
| 60 a minute (default) | 61.5 s / 61.5 s | 5 |
| 300 a minute | 2.9 s / 2.9 s | 0 |

Setting `RATE_LIMIT_MUTATIONS_PER_MIN=300` keeps the abuse guard (a client is still held to
5 a second) and lets a whole study upload without throttling. Not changed here: it is the
product's API contract (S1_SPEC section 5), so the default is a decision to take with the
frontend, which must keep honoring `Retry-After` either way.

### 32.11 What to rerun on staging, and how

Staging is live (PR 59, `docs/deploy/STAGING.md`), deployed at commit 5a6f076 (main after
PRs 58, 60 and 61, so with the three fixes above). It runs in the GCP project
`basis-staging-512000`; Firebase Auth stays in `cost-seg-basis`. The API is
`https://staging-basis-api-138338018557.us-central1.run.app` (also
`https://staging-basis-api-zweq2vtmtq-uc.a.run.app`). What its shape means for these steps:

| Part | Staging | What to expect, from this section |
|---|---|---|
| API | Cloud Run, 0 to 4 instances, 1 vCPU / 1 GiB, concurrency 40, `DB_POOL_SIZE=5` | up to 160 concurrent requests and 20 connections; one instance served 200 readers locally (32.7), and mutations now hold one connection (32.3) |
| Worker | one worker-pool instance, 1 vCPU / 2 GiB, the defaults (`LLM_CONCURRENCY=4`, `PHOTO_CONCURRENCY=5`), relay and Cloud SQL proxy sidecars | about 6.6 studies an hour (32.5) until the settings of 32.12 are applied |
| Database | Cloud SQL db-g1-small, `max_connections` 50 | the API's 20 plus the worker's and relay's pools fit |
| Temporal | self-hosted 1.31.4 on an e2-medium VM, namespace `basis-staging`, 8 history shards | not measured here: the local runs used the dev server |
| Providers | real; Replicate still under $10 of credit | about $6.40 a real-size study; SAM 3 at 60 creates a minute (32.8) |

Staging is parked between uses (worker pool at 0 instances, the Temporal VM and Cloud SQL
stopped): unpark it first with the commands in `docs/deploy/STAGING.md`, and park it again
after the run. Nothing below works, or should be sent, while it is parked.

The steps, in order:

1. Mint tokens for 25 test users. This creates `load-eng-0..24` in the `cost-seg-basis`
   Firebase project and needs an account allowed to sign custom tokens, so Bryan runs it:

   ```bash
   backEnd/.venv/bin/python tooling/load/mint_tokens.py --project cost-seg-basis \
     --api-key "$FIREBASE_WEB_API_KEY" -n 25 > ~/basis-load/staging_tokens.json
   ```

2. The API's write path at 25 concurrent engineers, with the GCS signed uploads and Cloud
   SQL, without starting a study (no model spend):

   ```bash
   API_URL=https://staging-basis-api-138338018557.us-central1.run.app \
   TOKEN_FILE=~/basis-load/staging_tokens.json LOCUST_ARGS=--upload-only \
     tooling/load/run_remote.sh RUN_DIR/staging-upload-25 25
   ```

3. The read path, over the studies of step 4. The staging API keeps the per-engineer limit of
   240 reads a minute, so either run one reader per engineer (`-u 25 --reader-pool 25`) or
   raise `RATE_LIMIT_READS_PER_MIN` on the staging API for the test, as 32.7 did locally,
   before 200 readers share the 25 identities:

   ```bash
   cd backEnd && .venv/bin/python -m locust -f ../tooling/load/locustfile.py --headless \
     ReviewReader --host https://staging-basis-api-138338018557.us-central1.run.app \
     -u 200 -r 20 -t 5m --reader-pool 25 --token-file ~/basis-load/staging_tokens.json \
     --csv RUN_DIR/staging-read-200/locust
   ```

4. Whole studies on the real providers, capped: 1 then 3 concurrent studies of 20 photos
   with `STUDY_BUDGET_USD=4` set on the staging worker (about $1.30 a study as in 32.8, at
   most $12 for the step):

   ```bash
   API_URL=https://staging-basis-api-138338018557.us-central1.run.app \
   TOKEN_FILE=~/basis-load/staging_tokens.json LOCUST_ARGS="--photos 20" \
     tooling/load/run_remote.sh RUN_DIR/staging-real-3 3 1.0
   ```

   With `PG_DSN` (through the Cloud SQL proxy) and `TEMPORAL_ADDRESS` set the sampler records
   Cloud SQL connections and the Temporal backlog too. Compare with 32.5 (pool-bound at
   about 6.5 studies an hour per worker process) and 32.8 (latencies on real providers).

5. For the worker at 10 and 25 studies on staging, without the model spend, the
   fake-provider worker has to run there: a `load` image target with `tests/load` (follow-up)
   deployed as a second worker pool on its own task queue. Until then, the local numbers
   (32.5, 32.6) are the worker's capacity.


### 32.12 Against the objectives, what to set before launch, follow-ups

The proposed SLOs (LOAD_TESTING.md section 5) at 25 concurrent studies on one worker and one
API process, after the fixes:

| SLO | Objective | Defaults (`c-s25`) | Tuned (`l12-s25`) |
|---|---|---|---|
| Status poll, studies running | p95 < 200 ms | 66 ms, met | 52 ms, met |
| Status poll, finished studies (reviewers) | p95 < 200 ms | 240 ms at 25 readers, 4.4 s at 200: **missed** (32.7) | same route |
| Other reads | p95 < 300 ms | 250 ms, met | 95 ms, met |
| Mutations | p95 < 500 ms | 180 ms, met | 180 ms, met |
| A study's upload without a 429 | every study | 2 per study: **missed** (32.10) | met with `RATE_LIMIT_MUTATIONS_PER_MIN=300` |
| Appraisal gate | p95 < 3 min | 26 min: **missed** | 3.6 min: missed narrowly (Azure DI is 72 to 82 s of it) |
| Assets gate | p95 < 20 min | 3.8 h: **missed** | 14.3 min, met |
| Activity schedule-to-start | p95 < 1 s | 20% within 1 s: **missed** | 25% within 1 s: **missed** (slots full, 32.6) |
| Cost per study | < $8 | $6.06, met | $6.06, met |

What to set for launch (settings only; the three fixes are in this branch):

1. `LLM_CONCURRENCY=64` and `CLASSIFY_CONCURRENCY=16` on the worker: one process goes from 6.6
   to about 55 studies an hour with no provider throttling (32.6).
2. `LOCALIZER_CONCURRENCY=12` with the Replicate account kept above $20 (auto-reload), or
   SAM 3 on the self-hosted service of section 30: the next factor of two (104 an hour).
3. More worker processes beyond that, not a larger pool: one process is capped at 64 calls
   (`Settings`) and 100 activity slots (the SDK's default; full at 25 studies). The
   providers allow about 337 studies an hour (Sonnet input tokens, 32.9), so about three
   processes at 104 an hour reach that ceiling before prompt caching. `DB_POOL_SIZE=5` per process was enough
   for every step once the three fixes were in (11 connections for the whole stack).
4. `RATE_LIMIT_MUTATIONS_PER_MIN=300`, with the frontend (32.10).
5. The API's uvicorn `--timeout-keep-alive` above the 5 s poll interval (32.8).

Follow-ups, not done here:

- **Status of finished studies from Postgres**, not a Temporal query (32.7): the read path's
  first bottleneck, and it lands on the worker.
- **A metric for the provider pool's wait** (time from `complete()` to holding the
  semaphore), so the bottleneck of 32.5 is visible on a dashboard; Temporal's own metrics
  show it only once the slots are full.
- **A setting for the worker's activity slots** (`max_concurrent_activities`), and with it a
  separate task queue or slot budget for `localize_photo`, whose waits hold slots that
  `analyze_photo` needs (32.6).
- **Prompt caching on the classifier** (`cache_control` on its system prompt and tools): the
  tightest provider ceiling (32.9) and about two thirds of a study's model cost.
- **Record the model calls of failed attempts**: `process_assets` writes its `llm_calls` in the
  transaction that a failure rolls back, so retried spend is invisible to the cost record and
  to the study budget (32.4).
- **A load image target** (`tests/load` in a `load` stage of the Dockerfile) so the
  fake-provider worker can run on staging (32.11, step 5).

### 32.13 Commands and data

From the repository root (`backEnd/.venv` from `uv sync --group dev --group load`):

```bash
docker compose -p basis-load -f tooling/load/compose.load.yml up -d
tooling/load/run_step.sh RUN_DIR/c-s25 25 0.1                                   # 32.5
LLM_CONCURRENCY=64 CLASSIFY_CONCURRENCY=16 tooling/load/run_step.sh RUN_DIR/b-s25 25 0.1   # 32.6
LLM_CONCURRENCY=64 CLASSIFY_CONCURRENCY=16 LOCALIZER_CONCURRENCY=12 \
  LOAD_PROVIDER_LIMITS=docs/perf/runs/provider_limits_2026-10-17.json \
  tooling/load/run_step.sh RUN_DIR/l12-s25 25 0.5                                  # 32.6
LOAD_EMBEDDER=real LLM_CONCURRENCY=64 CLASSIFY_CONCURRENCY=16 LOCALIZER_CONCURRENCY=12 \
  tooling/load/run_step.sh RUN_DIR/emb2-s10 10 0.1                                 # 32.4
RATE_LIMIT_MUTATIONS_PER_MIN=300 LOCUST_ARGS=--upload-only tooling/load/run_step.sh RUN_DIR/u300 5  # 32.10
python3 tooling/load/compare.py RUN_DIR/c-s1 RUN_DIR/c-s5 RUN_DIR/c-s10 RUN_DIR/c-s25    # the tables
python3 tooling/load/ceilings.py docs/perf/runs/provider_limits_2026-10-17.json         # 32.9
```

The read steps and the probe are in LOAD_TESTING.md section 6. Every run's summary
(`summary.md`, `summary.json`, the Locust CSVs, `studies.jsonl`, the top statements) is under
`docs/perf/runs/<run>/`; the raw samples and process logs stayed local (they hold nothing
private, but are large). Spend for this section: $3.69 recorded for the probe plus three
Azure DI analyses, and five header reads of a few tokens each.

## 33. Appraisal extraction: harness, sweep, highlights, UAD 3.6 (2026-10-27)

Question: how accurately does Basis read an appraisal today, which method and model should
read it, can the review page highlight exactly where each value is printed, and is the
redesigned URAR (UAD 3.6, mandatory for GSE submissions from 2026-11-02) handled? The
harness is `backEnd/evals/appraisal/` (contract `SPEC.md`); the research behind it is
`APPRAISAL_RESEARCH.md`. Every number below is from the harness on private data (truth,
recordings and runs under `BASIS_EVAL_DATA_DIR/appraisal/`); no client value is in the
repository.

Answer, in short:

- **Today's pipeline reads 0.835 of the fields** across 41 appraisals (client 1004 0.910,
  synthetic 1004s 0.885, the client converted to UAD 3.6 0.776, the 12 GSE UAD 3.6 samples
  0.620). The measured defects: checkboxes lost (Azure DI page lines carry no marks), values
  from the wrong box (Azure DI key-value pairs: foundation "Walls", an MLS number as the data
  source, site area = GLA on UAD 3.6), row cells joined (a depreciation figure glued to its neighbour into one number), the
  comparables grid read as the subject, the cost-data date as the effective date, validators
  blind (0 of 13 planted inconsistencies found, 31 false alarms), and no highlight boxes at
  all (the pipeline dropped Azure DI's geometry) (33.2).
- **Checkbox labels read from the page geometry** (the words right after a box, else the
  header over a column of boxes) find the checked option 0.958 / 0.985 of the time on the
  client and synthetic 1004s, against 0.875 / 0.849 for Azure DI's own key-value pairing,
  which reads the utilities grid wrong (33.4). With the labels in its context the luna
  fallback reaches 0.906 on all 41 documents (33.5).
- **Method and model sweep** (33.5): on a 16-document subset, one grounded call on
  **gpt-6.1-sol is the most accurate single method: 0.940 of all fields, every critical field
  right, $0.072 a document**; Sonnet + Opus voting 0.928 at $0.27; the cheapest strong option
  is a luna vote (one grounded call, then checkbox_context for what it left) at 0.924 on all
  41 documents for $0.006. Two answers of luna to the identical request differ on 4.6% of
  field outcomes, so a one-run gap under two points is noise; the vote leads held across a
  repeat.
- **Highlights** (33.6): every field now carries `regions` (page, box in page fractions) from
  Azure DI's word, mark and pair boxes, chosen by the field's caption; **99.3%** of correctly
  read values get the box on the value (median box: one word). Stored in
  `extraction_fields.regions` (migration 0010), returned by `GET /v1/studies/{id}/fields`
  (`docs/api/FIELD_REGIONS.md`).
- **UAD 3.6** (33.7, 33.8): a MISMO 3.6 XML reader mapped from the GSE's own specifications
  (Appendix A-1 delivery specification and G-1 cross-reference, v1.4) reads **0.989** of the
  GSE samples' verified fields (0.958 on its first, spec-only pass); uploads accept the UAD 3.6
  ZIP or a bare XML, and the values are located on the PDF for highlighting. For PDF-only
  reports the pipeline now recognizes the form by its page footers (41 of 41 correct) and
  reads a UAD 3.6 PDF by its A-1 captions: production on the GSE samples goes from 0.619 to
  0.848, the legacy 1004 path unchanged.
- **Reliability** (33.10): Azure DI answered a poll with `Retry-After: 1587` for an analysis it
  finished in about 60 s, and the SDK's poller slept on it until the timeout failed the
  document; production's layout call had the same exposure. The delay is now capped.

### 33.1 Harness

- **Sets** (41 documents, 5,437 scored fields): `client` (the client's legacy 1004, all 138
  fields labelled with pages), `client_uad36` (the same appraisal converted to a UAD 3.6
  package by `uad36_package.py`), `gse_d1` (the 12 GSE Appendix D-1 UAD 3.6 samples, truth
  generated from their XML by `gse_d1.py` and anchored to what the PDF prints), and
  `synthetic_1004` (27 generated 1004s, 3 of them scanned).
- **Truth**: every catalogue key has a status per document (`present`, `absent`,
  `not_developed`, `unscorable`), "truth is what the box says" (SPEC section 1).
- **Scoring**: correct / wrong / missed / spurious / correct-absent per field; accuracy,
  wrong-value rate, coverage, page and grounding, display, review-gate recall, highlight
  (the first region's words hold the value), clustered intervals by document.
- **Runs**: `run.py --mode live|replay`. Live runs record every Azure DI analysis and model
  answer under `recordings/<pdf sha256>/`; replay answers from them for free. A later live run
  of the same request adds a take (`<hash>.take2.json`) and never replaces the first, so a
  replay keeps reproducing the first run (33.3).
- **Regression baseline**: `baseline.json`, the production replay's outcome per field; a run
  exits 1 when a field regresses.

### 33.2 Today's pipeline (production, replay)

| Set | Accuracy | Notes |
|---|---|---|
| client (1004) | 0.910 | 111 of 122 present fields |
| client_uad36 | 0.776 | UAD 3.6 boxes read by 1004 captions |
| gse_d1 (12) | 0.620 | critical 0.925, high 0.471 |
| synthetic_1004 (27) | 0.885 | critical 1.000 |
| all 41 | 0.835 | |

Review gate at 0.7: recall 10 of 13 errors at 68% review load; values Azure DI reads with high
confidence from the wrong box are not flagged. Display: 64.5% of client values render as the
canonical form; 76% grounded.

### 33.3 Defects found in the harness and its truth

- **Checkbox guard**: the grounded systems required the option's words next to its mark in
  the quote; on column grids (utilities: Public / Other under headers) it dropped 1,217 right
  luna answers to catch 287 wrong ones. Replaced by the geometric labels (33.4).
- **Section routing**: `llm_sections` sent each group the pages of its headings; Azure DI reads
  the 1004's vertical headings as single letters, so 900 of 954 neighborhood/site and 498 of
  694 subject/contract fields of the synthetic 1004s were never shown to the model. Each call
  now gets every page.
- **Recordings overwritten**: the sweep re-ran production's request live and replaced its
  recording, so the accepted baseline no longer replayed. That is how the noise was measured:
  two luna answers to the identical request flipped 254 of 5,538 field outcomes (36 of 41
  documents) at identical accuracy. Takes are now appended.
- **Truth**: `gse_d1.py` took the parties from the first SERVICE; Condo1's first SERVICE is a
  data collection, so borrower, owner, lender, appraiser, company, license and state were
  marked absent although page 3 prints them. Fixed; the 7 Condo1 fields are the only change
  of the regeneration (old file kept).

### 33.4 Checkbox labels from the page geometry

`basis/extraction/marks.py`. Measured on the forms: words of one label lie 0.0029-0.0045 page
widths apart, the next caption or an Other write-in 0.0057 or more, so a label is the words
right after the box with gaps of at most 0.005; a box with no words after it in a column of
boxes takes the header over the column; a lower-case sentence option wraps to the next line
(skipping the form's single-letter side labels). The text the model gets carries each label
(`:selected:[Public]`); a checkbox answer must name a checked box in its quote or on its
page, and an option printed as text (UAD 3.6) must be in the quote and not on a line with
boxes.

| Checked option found among the checked boxes' labels | Geometry | Azure DI key-value pairs |
|---|---|---|
| client (24 checkbox fields) | 0.958 | 0.875 |
| synthetic_1004 (657) | 0.985 | 0.849 |

### 33.5 Method and model sweep

Methods (`evals/appraisal/systems/`): `production`; `checkbox_context` (production with the
labelled page text and every checkbox key asked); `llm_all` (one grounded call for every key:
status, value, page, verbatim quote, checked mechanically); `llm_pages` (plus page images);
`llm_sections` (one call per section group); `azure_1004` (Azure DI's prebuilt 1004 model)
and `production_plus_1004`; `vote` (the first voter's value, the second's where the first has
none). Effort low throughout.

All 41 documents, luna, after the harness fixes:

| System | Accuracy | Wrong | Missed | $ / doc |
|---|---|---|---|---|
| production | 0.835 | 392 | 312 | 0.002 |
| azure_1004 alone | 0.339 | 18 | 2,682 | 0.118 (Azure) |
| production_plus_1004 | 0.875 | | | 0.120 |
| checkbox_context | 0.906 | 205 | 195 | 0.002 |
| llm_all | 0.867 | 53 | 512 | 0.004 |
| llm_pages | 0.866 | 28 | 646 | 0.007 |
| llm_sections | 0.890 | 55 | 439 | 0.009 |
| vote llm_all -> checkbox_context | 0.924 | 103 | 119 | 0.006 |

Azure DI's 1004 model is precise (18 wrong of 1,460 values) but leaves most fields empty, and
does nothing for UAD 3.6.

16-document subset (both clients, 6 GSE, 8 synthetic including 2 scans), all fields:

| System | Model | Accuracy | Wrong | Missed | Critical | $ / doc |
|---|---|---|---|---|---|---|
| production | luna | 0.825 | 147 | 140 | 0.948 | 0.002 |
| checkbox_context | luna / Haiku / Sonnet / Opus / Sol | 0.884 / 0.876 / 0.876 / 0.909 / 0.911 | | | | 0.002-0.160 |
| llm_all | luna / Haiku / Sonnet / Sol | 0.843 / 0.866 / 0.903 / **0.940** | 24 / 44 / 34 / 33 | | 0.948 / 0.833 / 0.990 / **1.000** | 0.004-0.110 |
| vote llm_all -> cc, luna (2 runs) | | 0.910 / 0.900 | 48 | 51 | 0.969 | 0.006 |
| vote llm_all Sonnet -> cc Opus (2 runs) | | 0.928 / 0.927 | 60 | 27 | 0.958 | 0.270 |
| vote llm_all Sol -> cc Sol | | 0.934 | 40 | 15 | 0.969 | 0.12 |

Sol's single grounded call has the best overall score, the fewest wrong values and every
critical field right; votes fill its misses (44 to 15) but added spurious values, mostly the
two patterns the presence rules now remove (33.9). One Sol run: its lead over the votes is
within the measured noise.

### 33.6 Highlight regions

`basis/extraction/regions.py` (with `geometry.py`, `marks.py`): an Azure DI pair's own value box
when that pair produced the value; for a checkbox, the checked box and its label in the
field's caption row; otherwise the value's words on its cited page nearest the field's
caption; never a box on another page. The stored analysis now keeps word, mark and pair boxes
(about 0.95 MB per appraisal, 1.6 MB at most, from 0.25 MB); a stored analysis without them is
analyzed again once, so an older study's next revision gets boxes too.

| | Correct box | Wrong box | Wrong page | No box |
|---|---|---|---|---|
| production, 41 documents | 0.993 (3,224 / 3,248) | 3 | 19 | 2 |

The 19 wrong pages are the model's page citations. Box size: median 0.05% of the page, 95th
percentile 0.22%. The scorer was corrected on the way (dashes, an "x" in "50 x 150", Other
write-ins, numbers printed with their unit, wrapped lines; scans have no text layer to check
against). On the running stack the client study's fields were backfilled (119 of 124 boxed,
values unchanged) and the review page draws them (`web/live/live-review.js`, falls back to
its text search when a field has no box).

### 33.7 UAD 3.6 XML reader

`basis/extraction/mismo36.py`, `package.py`, map `uad36_map.py` generated by
`evals/appraisal/uad36_spec.py` from A-1 / G-1 (catalogue label -> G-1 legacy 1004 field ->
MISMO 3.6 xPath; A-1 for types, enumeration display text and report captions). 100 catalogue
keys mapped, 30 with no UAD 3.6 equivalent (reasons in the map), 4 unresolved.
Subject only (never comparables), approaches not developed left empty, the unit count from the
subject's "Units Excluding ADUs" and "Accessory Dwelling Units". An XML value the PDF does not
print near its caption is an `xml_pdf_mismatch` issue (it found "Mixed use" in a GSE XML whose
PDF prints "Mixed used").

| | gse_d1 verified | client_uad36 |
|---|---|---|
| mismo_xml | 0.989 (first spec-only pass 0.958) | 0.941 |
| highlight correct | 0.937 | 0.950 |

Caveats: the reader was corrected after its first measurement where A-1 / G-1 supported the
truth's reading, so 0.958 is the cleaner number; client_uad36's XML was generated from its
truth, so it measures consistency; real appraisal-software exports can only be measured after
2026-11-02.

### 33.8 UAD 3.6 as a PDF

`basis/extraction/forms.py` recognizes the form by its page footers (UAD 3.6 documents carry
the A-1 footer on 18-30 pages and the 1004 footer on none; legacy documents the reverse; 41 of
41). On a UAD 3.6 PDF, `uad36_pdf.py` maps Azure DI pairs and the text layer by A-1 captions
within their printed section and subsection, never reads the comparables grids, does not ask
the 34 keys the form does not carry, and the fallback gets its own prompt
(`appraisal_field_fallback_uad36/v1`). Caption mapping alone: 392 correct, 11 wrong from pairs;
97 correct, 0 wrong from the text layer.

| production, verified fields | before | after |
|---|---|---|
| gse_d1 | 0.619 | 0.848 |
| client_uad36 | 0.776 | 0.847 |
| legacy sets | unchanged | unchanged |

After the presence rules (33.9) and the unit-count fix below, re-recorded live on luna for
the 13 UAD 3.6 documents (headline accuracy, present fields):

| luna, UAD 3.6 | gse_d1 | client_uad36 | units correct |
|---|---|---|---|
| production | 0.854 | 0.859 | 13 / 13 |
| checkbox_context | 0.901 | 0.918 | 11 / 13 |
| llm_all | 0.833 | 0.894 | 11 / 13 |

The number of units: G-1 links the 1004's Units box only to the UAD 3.6 ADU Yes/No in each
Unit Interior, which cannot give a count (units were missed on every UAD 3.6 document). It is
now read from the subject's own A-1 counts, "Units Excluding ADUs" (0100.0022) and "Accessory
Dwelling Units" (0100.0019), in the PDF path and the XML reader alike (13 of 13 without a
model). checkbox_context and llm_all still let the model's answer replace it on two documents.

### 33.9 Presence rules

`basis/extraction/presence.py`, applied once after the fallback (and by the grounded systems):
an approach's indicated value or a cost-approach line printed as 0 or without a number ("Not
Necessary for Credible Results") is not developed; the certification page's restated
appraised value and effective date are kept only when cited on a page with the signature
block. Removed spurious values: production 93, checkbox_context 78, llm_all luna 31, Sol 10,
luna vote 78; no field broken in any system. Production on all 41 documents now reads 0.879
(headline, 3,750 of 4,266) with 61 spurious values, against 0.835 and 199 on the day it was
first measured; the regression baseline is the production replay at the branch tip. Not implemented: a UAD 3.6 basement area of 0 is
printed on the form ("Finished Below Grade 0 Sq. Ft."), so the GSE truth's not_developed
convention there is a truth decision, not a reader defect.

### 33.10 Azure DI polling

`basis/extraction/azure_di.py`: the poller is the SDK's own `AsyncLROBasePolling` with the
server's Retry-After capped at 5 s. Live: the 32-page form that timed out at 180 s and at 900 s
finishes in 59 s; a 6-page layout analysis in 33 s.

### 33.11 Spend and what is open

Spend for this section: $20.19 of model calls over 45 live runs (the largest: the Sonnet +
Opus vote repeat $4.33, checkbox_context on Opus $2.56) and about $11.6 of Azure DI (layout
$5.23 once for the 41 documents, the 1004 model $4.60, probes and checks about $1.7; up to
$1.3 more if Azure billed the client-side timeouts): about $32, above the $30 approved.

Open:
- Wire the winner into production behind a setting (`llm_all` on Sol, or the luna vote as the
  cheap default) and repeat Sol once more before making it the default. Done for `llm_all` on
  Sol (`EXTRACTION_METHOD=grounded`, 33.12); the repeat was not run.
- Units on UAD 3.6 in checkbox_context (it asks every checkbox key and overrides the reader).
- The GSE basement-area convention (33.9) and four disputed GSE truth readings (SF2 listing,
  Condo1 special assessments, condo design style, "Parking Garage" as a garage).
- Validators (cost arithmetic, cross-box), the review gate's thresholds, the critical-field
  ranking for cost segregation.
- The frontend viewer for a ZIP / XML upload; which document the workflow uses when a study
  has both an XML and a PDF.

### 33.12 Production default: the grounded reading on Sol (2026-10-28)

Decision (Bryan): production reads an appraisal PDF with `llm_all` on gpt-6.1-sol at effort
low, the most accurate single method of 33.5. The single repeat 33.11 asked for was not run
(no budget left); the lead over the votes is within the measured noise.

- **Settings** (`basis/config.py`): `EXTRACTION_METHOD` = `grounded` (default) | `pipeline`
  (the reading of 33.2, unchanged); `EXTRACTION_GROUNDED_MODEL` = `gpt-6.1-sol`,
  `EXTRACTION_GROUNDED_REASONING_EFFORT` = `low` (gpt-6.1-sol refuses `none`: refused at
  startup), `EXTRACTION_GROUNDED_MAX_TOKENS` = 32000 (the sweep's). The worker logs the method
  at startup. A UAD 3.6 XML or ZIP upload is still read by the XML reader (33.7).
- **Code**: the method is `basis/extraction/grounded.py` (moved from the sweep; the sweep's
  `llm_all` / `llm_pages` / `llm_sections` are adapters over it), run by
  `basis/extraction/pipeline.py` after the Azure DI analysis. On top of what 33.5 measured,
  production derives `site.area` / `site.area_acres` from the site area read (as the pipeline
  always did), and boxes each value by the pipeline's highlight rules (33.6: the checked box,
  else the value's words nearest the caption), falling back to the quote's words: on the 15
  replayed documents 0.996 of the checked highlights are on the value (1,195 of 1,200),
  against 0.857 for the quote's words the sweep used. Values are unaffected.
- **Errors**: the pipeline fallback's contract (best effort). When the grounded reading cannot
  run (no Azure DI, no word geometry, no provider) or its call does not answer (provider error,
  no structured answer, the study budget), the pipeline reads the document on the same Azure DI
  analysis, with a note and the attempt in `extractions.raw["grounded"]`. An account or
  configuration error stops the stage, as before.
- **Same requests as the sweep**: replayed with no network, `--system production` (the
  activity's path) on the 16-document subset sends the recorded `llm_all` requests byte for
  byte: client_a and the 8 synthetic documents replay; the 7 UAD 3.6 documents miss, the same
  7 request hashes as the sweep's own `llm_all` replay at this commit. Their recordings were
  made before the units fix (`1185298`), which changed the UAD 3.6 description of
  `improvements.units` (caption "ADU" -> "Units Excluding ADUs", "Accessory Dwelling Units");
  with that one description restored, 15 of 16 replay (TwoToFour1 has no recording of the
  UAD 3.6-only request). On every replayed document the fields equal the sweep's `llm_all`
  on every asked key (value, page, confidence, quote; the region by the rule above); the only
  differences are the derived site-area fields (13 more correct on the 9 documents, 20 on the
  15, none wrong).

| replay, gpt-6.1-sol low | documents | headline accuracy | spurious |
|---|---|---|---|
| `llm_all`, units description as recorded (predictions identical to the 33.9 run) | 15 of 16 | 0.953 (1,423 / 1,493) | 7 |
| `production`, units description as recorded | 15 of 16 | 0.967 (1,443 / 1,493) | 7 |
| `llm_all`, this commit | 9 of 16 | 0.979 (1,054 / 1,077) | 5 |
| `production`, this commit | 9 of 16 | 0.991 (1,067 / 1,077) | 5 |

The 33.9 run scored 0.951 (1,420 / 1,493, 10 spurious) on the truth of that day; its
predictions are unchanged, and the GSE truth has been revised since (scored on the truth of
2026-10-28 evening, still being edited at the time of writing).

- **Cost and latency** (the live run, 16 documents): $0.072 a document ($0.064-0.079),
  13,600 input / 4,500 output tokens (at most 19,235 / 4,952), model latency 28 s (25-32 s)
  on top of the Azure DI analysis; the pipeline's luna fallback was about $0.002 a document
  and 21 s on the GSE documents.
  One appraisal per study: about $0.07 more per study (per revision re-read). The call
  reserves about $0.35 of the study budget before it is sent (32,000 output tokens at
  $10/M); `STUDY_BUDGET_USD` (default 25) covers it. The 60 s `LLM_REQUEST_TIMEOUT` is about
  twice the slowest call measured.
- **Baseline**: `baseline.json` stays the pipeline's (`--system pipeline`, which the
  `eval_data` regression test replays; its predictions on all 41 documents equal the
  previous `production` system's). The grounded default has its own
  `baseline.grounded.json` (`--baseline`), accepted from the `production` replay of the 9
  documents that replay (EVAL SPEC section 9).
- **Open**: re-record the grounded call live for the 7 UAD 3.6 documents of the subset (about
  $0.50) and for the 25 documents with no Sol recording (about $1.80), then accept the
  grounded baseline on all 41.

## 34. End-to-end: the study output against the engineer's report (2026-10-29)

Question (WORKFLOW_REVIEW_2026-10-29 section 6, step 1): the stages are measured one by
one; is the *finding* measured? Given a completed study's export and the engineer's cost
segregation report, how many of the engineer's rows does the pipeline produce, with the
right bucket, and how far are its dollars and its bucket shares from the engineer's? Until
now the engineer's cost per row sat in the label file unscored, and the only row-level
comparison was a private one-off that let one asset answer two rows. Branch
`eval/e2e-scorer`; scorer `backEnd/evals/e2e_report.py` (CLI `python -m evals.e2e_report`),
arithmetic `backEnd/evals/report_math.py`; formats in `evals/README.md`. Every number below
is from the scorer on the private data; no client dollar amount appears here.

### 34.1 Design

**Inputs.** (1) The export the pipeline already writes at completion
(`basis.activities.export_report`: `rev-<n>.json`, format `basis-export/2`, or its assets
CSV): every asset with label, room and room type, material, bucket, method, quantity, cost
estimate, review reason and citations. The scorer has a typed loader for it and changes
nothing in the exporter. (2) A reviewed row-assignment label (private,
`labels/e2e_rows_v1.json`): for each of the engineer's 46 rows the pipeline labels charged
to it, with room-type and material conditions, and the row's kind (29 A photo-countable,
9 B surfaces, 8 C concealed). (3) The engineer's printed report (private,
`labels/engineer_report_v1.json`): 46 rows with bucket and cost, the building basis B and
closing costs CC, the printed replacement-cost total, the 12-row building-systems page, the
bucket percentages and the pro-rata allocation block.

**One-to-one assignment.** The `report_rows` evidence rules (section 13) are any-of and
many-to-many: they say what *proves* a row, and an appliance proves both the Appliances row
and the dedicated-circuit row. A dollar comparison needs each asset charged once. The
assignment lists, per row, `{label, room_types | exclude_room_types, materials |
exclude_materials}`; allow-lists reject an unknown room or material and deny-lists accept
it, so a label split between two rows by complementary lists (a sink by room: kitchen and
utility sinks against bathroom plumbing; a wall finish by material: wood paneling, soft
wall coverings, painted walls) is charged exactly once. The loader requires every
vocabulary label to be assigned somewhere or listed as unassigned with a reason
(`other`, mirrors, landscaping, patios, pools, vinyl and laminate floors: the engineer
has no row for them); the scorer refuses an export in which one asset meets two rows. A
kind C row may carry hints (a smoke detector, painted walls) that count in the totals but
are never scored; a kind A or B row no label can charge is a *vocabulary gap*, not a miss.
The decisions per row are in the private `labels/E2E_ROWS_NOTES.md`.

**Metrics.** Per engineer row: evidenced (any asset charged) or missed; the pipeline's
bucket by asset count (the headline; ties by dollars, then the shorter period) and by
dollars (reported beside it); bucket right; priced and unpriced assets with the cost
step's reasons; pipeline dollars (the charged assets' estimates) against the engineer's,
ratio and absolute error; and each row as a share of its own side's total. Per bucket and
in total the same, over every priced asset of the export. Classification is reported
three ways (of the evidenced rows, of the 38 photo-visible rows, of all 46) with Wilson
intervals, and weighted by the engineer's cost share; kind C rows are "not produced" with
their share of the engineer's total, never a wrong bucket. The pipeline's building bucket
(27.5-year under the deployment's `residential_rental` default) is compared as the
engineer's 39-year bucket, the only effect of the property class
(`basis.domain.rules.bucket_for`).

**What is and is not like-for-like.** The bucket is: one per engineer row, one per
pipeline asset. The dollars are not: the engineer's row is replacement cost new times one
factor that lands the schedule on the purchase-price basis; the pipeline's is a
replacement cost on a placeholder takeoff. The raw dollar comparison is therefore labelled
*replacement cost against allocated basis* and the comparable number is the share (row or
bucket as a percentage of each side's own total). The scorer also runs the pipeline's rows
through the deterministic tail with the engineer's B and CC: the schedule a basis step
would print from today's output.

**The deterministic tail** (`report_math`; WORKFLOW_REVIEW section 2): T = B + CC; f =
T / sum(RCN); row = RCN x f to the cent (the rounding residual reported, absorbed by the
largest row on request); bucket totals and tenth-percentages; the allocation block
building_b = B x bucket_b / T and closing_b = CC x bucket_b / T with each column footed
to B and CC by charging its rounding cent to the largest bucket; the systems page by row
share. All `Decimal`, half-up. A printed report that does not list RCNs per row is
inverted: implied RCN = cost x printed RCN total / T, then everything is recomputed and
compared with the printed numbers item by item. A unit test on six fabricated rows proves
the arithmetic by hand (`tests/unit/evals/test_report_math.py`); the private run proves the
reproduction.

### 34.2 Results on the existing completed export

Export `11217e4e` rev-3 (E2E_ARTIFACTS, private: the client property, 2026-10-19, today's
models, residential_rental, no room aliases, no components gate, no reference corpus so no
citations): 376 assets, 249 priced.

| Metric | Value |
|---|---|
| Rows | 46: 38 photo-visible, 8 concealed (kind C) |
| Concealed rows' share of the engineer's total | 39.7% (not produced; foundations, structural and interior framing, site utilities, fire protection, data, access control, painting) |
| Evidenced | 35 of 38 photo-visible, 0.921 [0.79, 0.97] |
| Missed | 2: window guards (no guard visible in the photos), dryer vents (no vent assigned to the laundry; the dryer is an appliance) |
| Vocabulary gap | 1: the dedicated-circuit row (the pipeline produces appliances, not circuits) |
| Bucket right, majority by count | 24 of 35 evidenced, 0.686 [0.52, 0.81]; 0.632 of the 38 photo-visible rows; 0.522 of all 46 |
| Bucket right, majority by dollars | 17 of the 25 evidenced rows with a priced asset (0.680); the two verdicts agree on every row that has one |
| Engineer's cost share on rows bucket-right / evidenced / wrong | 42.9% / 59.7% / 16.8% |
| Wrong buckets | 11, identical to the one-off's and to the second run: moldings, fireplaces, raised paneling, wall coverings, half walls (5 vs 39); exterior lighting (5 vs 15); FF&E (5 vs 7); electrical, plumbing, ceramic tile, restroom millwork (39 vs 5) |
| Evidenced rows with an unpriced asset / with no priced asset | 12 / 10 (five site rows, half walls, roofing, facade, gutters, FF&E: `COST_NEEDS_QUANTITY` labels, a half wall without a cost row, and furniture) |
| Assets charged to a scored row / to a concealed row / to none | 310 / 11 / 66 (43 `other`, 11 landscaping, 8 mirrors, 1 patio, 1 laminate floor, 2 tiles of a non-ceramic material) |
| Conflicts (an asset two rows accept) | 0 |
| Pipeline bucket shares (of its priced total) | 5-year 45.1%, 7-year 5.9%, 15-year 0.8%, 39-year 48.2% |
| Bucket share error (points) | 5-year 30.7, 7-year 5.5, 15-year 4.2, 39-year 32.0 |
| Row share error | mean 2.5 points, largest 12.7 (a concealed row); total variation distance between the two row distributions 56.9 points |
| Dollars (replacement cost vs allocated basis) | the pipeline's priced total is 0.32 of the engineer's total; median row ratio 0.60 over the 25 rows with a priced asset |
| Pipeline rows through the tail with the engineer's B and CC | the schedule it would print: 5-year 38.3%, 7-year 6.1%, 15-year 0.3%, 39-year 55.3% |
| Reproduction of the engineer's printed report | 87 numbers (46 rows, total, 4 bucket totals, 4 percentages, 8 allocation cells, 24 systems-page cells): 86 to the cent, 1 within a cent (a system's replacement cost, the precision limit of RCNs implied from rounded costs) |

The second run, `0bfbcee5` (same code and models, with the reference corpus: 368 assets,
243 priced, 466 verified citations; scored from its stored assets through a private
converter because the study still waits at its assets review), gives the same 35 of 38,
24 of 35 and the same 11 wrong rows; bucket share errors 31.2 / 2.7 / 4.3 / 29.6 points;
priced total 0.30 of the engineer's. The JSON and CSV loaders give identical scores.

**Cross-check against the one-off** (`compare_e2e_to_engineer.py`, E2E_ARTIFACTS 2.1: 37
of 38 evidenced, 26 of 37 right, 30 assets in two rows, 69 in none). The two rows lost are
exactly the two the many-to-many rules answered twice: the dedicated-circuit row (evidenced
by the appliances, which one-to-one belong to Appliances) and the dryer-vent row (evidenced
by the dryer, an appliance). Both were "right" under the evidence rules, so bucket-right
drops by exactly those two; the 11 wrong rows are unchanged. Four assets gained a row
(three countertops to the millwork rows, a bathroom closet shelf to restroom millwork) and the
painted walls moved from the 5-year wall rows to Painting (kind C), which is what the
one-off complained about.

**The allocation block's rule.** Rounding B x bucket / T to the cent leaves each column a
cent off B or CC; the engineer's printed block foots because the 39-year cell gives or takes
the cent. "Largest bucket absorbs" reproduces the printed block; largest-remainder
apportionment (the usual way to make rounded shares foot) would move the cent to a
different bucket and does not. A report builder must use the former to match.

### 34.3 Reading

1. **Classification is measured now, and it is the same number three ways.** 0.686 of
   the evidenced rows, 0.632 of the photo-visible rows, 0.522 of all 46; by dollars rather
   than count the verdicts agree on every row. The eleven misses are conventions to settle
   with the engineer, not noise (identical in both runs and in the one-off): about a sixth
   of the engineer's dollars.
2. **Dollars are not comparable yet, and the scorer says so in three places.** The
   pipeline prices under a third of the engineer's total (placeholder 150 sq ft rooms,
   127 unpriced assets, no concealed systems, no basis step) and its bucket shares are 30
   points off on both large buckets. The share-basis view, and the pipeline's own schedule
   through the tail (38 / 6 / 0 / 55), are the honest statements of today's output.
3. **The tail is a solved problem.** Given 46 RCNs, their buckets, B and CC, every printed
   number of the engineer's report comes back to the cent (one cell within a cent because
   the RCNs are implied, not printed). The report builder's arithmetic can be tested
   exactly; what remains is producing the RCNs.
4. **Coverage is the vocabulary's and the takeoff's, not the scorer's.** Ten evidenced
   rows carry no dollars because their labels need an area or a length nobody measures;
   eight rows are concealed systems nothing produces. Together they are about 53% of the
   engineer's total, and no classification or pricing change moves them.
5. **Limits.** One property, one export of an old configuration (residential_rental, no
   aliases, no components gate, no corpus), so the intervals are wide and the assignment
   was written from this report's rows; a second property will add rows and rules. The
   row assignment carries three judgment calls recorded in the notes (countertops to
   millwork, furniture to FF&E, painted walls to Painting).

### 34.4 What the next builds are expected to change, and what the export must add

- **Measured quantities** (the takeoff build): the ten fully unpriced rows and the 127
  unpriced assets become priced; the 15-year bucket share (0.8% today against a driveway
  that is the largest site row) and the 39-year share (roof, facade, gutters) should move
  first. The scorer's per-row unpriced counts and the `COST_NEEDS_QUANTITY` tallies are the
  check. The export already carries `takeoff_inputs` (area, source, count); nothing more is
  needed for the scorer, but the area *source* per room (appraisal GLA, sketch, typical)
  should stay in it so a run on measured areas can be told from one on placeholders.
- **Basis and land math** (the basis build): once the pipeline scales its rows to a basis,
  the dollar comparison becomes like-for-like and the scorer's "replacement cost vs
  allocated basis" caveat goes. The export should add a basis section (purchase price,
  land, B, CC, T, the factor, and the scaled cost beside each asset's replacement cost) so
  the scorer reads the scaled rows instead of re-deriving them through `report_math`; the
  reproduction check then runs on the pipeline's own schedule.
- **Concealed systems** (step 5 of the review): the eight kind C rows (39.7% of the
  engineer's total) stay "not produced" until a systems model or an appraisal-based
  carve-out exists; the scorer will then score them as kind A rows with an area source.
- **Export header**: the property class in effect (today inferred from the bucket values)
  and the rule id and rationale per asset (section 27.5's follow-up), so a wrong bucket can
  be traced to a rule or a model call from the export alone.

Commands (from `backEnd/`):

```bash
BASIS_ENV=test BASIS_EVAL_DATA_DIR=~/basis-eval-data uv run python -m evals.e2e_report \
    --export ~/basis-eval-data/work/e2e_artifacts/export_11217e4e/rev-3.json \
    --out ~/basis-eval-data/reports/E2E_SCORE_11217e4e.md --json OUT.json [--data-free]
uv run pytest tests/unit/evals/test_e2e_report.py tests/unit/evals/test_report_math.py -q
```

## 35. Measured quantities: the takeoff reads the appraisal (2026-10-29)

Branch `feat/measured-takeoff`. WORKFLOW_REVIEW 2026-10-29, section 3, items 2 and 3: every
area item was priced on a typical room area (kitchen 150 SF, bathroom 75, everything else
150), the 11 exterior and site labels the cost table prices per SF or LF could not be priced
at all, and the cost year and location factor were deployment settings. The appraisal's
areas, stories, garage, site, state and effective date were extracted and unused. Research
and the assumption table: `TAKEOFF_RESEARCH.md` (data-free).

### 35.1 Method

`basis.domain.measurements` reads the revision's extraction fields into
`BuildingMeasurements` (each value with its field key and extraction source) and derives:

* **Room floor areas.** Each above-grade room gets `GLA x weight / W`, the weight its room
  type's typical area (`basis.domain.rooms.ROOM_WEIGHTS_SF`, the legacy residential typical
  areas extended to the vision vocabulary) and `W` the larger of the appraisal's room program
  (one kitchen, the bedrooms, the full and half baths from the URAR `full.half` count, the
  other rooms at 200 SF) and the sum of the study's above-grade weights. The shares never
  sum to more than the GLA (49 photographed rooms share one floor instead of adding floor),
  and unphotographed rooms are counted. Basement rooms (type `basement` or floor 0) are
  sized at the same scale, capped by the finished basement area (finish 100% when not
  printed); the garage room takes the cost approach's garage line, else cars x 240 SF;
  exterior rooms keep the typical area. Without a GLA every room is the typical area, as
  before.
* **Property-level quantities**, measured once per study: footprint = GLA / stories (1 when
  not printed), ground footprint = footprint + garage; a 1.5:1 rectangle's perimeter; roof =
  (ground footprint + perimeter x 1 ft overhang) x 1.118 (6/12 pitch); siding = ground
  perimeter x 9 ft + dwelling perimeter x 9 ft per upper story, less 15% openings; gutters =
  two long eaves of a gable roof; landscaping = site - ground footprint, an upper bound that
  sends the asset to review (`takeoff:upper_bound`).
* **Not derivable** (driveway, sidewalk, patio, fence, retaining wall, concrete pad, parking
  lot): still `COST_NEEDS_QUANTITY`, now with the measurement named on the takeoff
  (`needs_measurement`). No default is invented (ATG chapter 4 C.6).
* **Cost index.** `location_factor_for_state(subject.state)` and the year of the appraisal's
  effective date (the placed-in-service date once the intake carries it: `study.intake`,
  the basis-allocation branch), each falling back to the setting with the reason; both and
  their sources are written in the cost row's `source_row`.

Every takeoff records its inputs (`room_area_source`, `room_area_formula`, the field keys
and sources, the constants used) and assumptions, so the export can show "room area = GLA x
150 / 1,390 (kitchen weight / program weight)". Settings: `TAKEOFF_AREAS` (default
`appraisal`; `typical` is the behaviour before this section) and `COST_INDEX` (default
`study`; `setting`). Without extraction fields the activity's output is byte-identical to
before (regression test; the lifecycle and node suites pass unchanged).

| Assumption | Value | Source (TAKEOFF_RESEARCH section 4) |
|---|---|---|
| room weights | kitchen 150, bath 75, bedroom 150, living 250, dining 150, family 250, office 120, laundry 50, hallway 50, stairway 40, foyer 60, storage 30, mechanical 40, attic/basement/other 150, garage 240 | legacy typical areas; room-size guides |
| other program room | 200 SF; half bath 40 SF | mean of the living-type weights; a 5 x 8 powder room |
| stories | 1 when not printed | recorded assumption |
| footprint aspect ratio | 1.5:1 | rectangular footprint (AH 531 shape classes) |
| roof pitch factor | 1.118 (6/12) | pitch multiplier tables |
| eave overhang | 1 ft | typical eave |
| story wall height | 9 ft | 8 ft ceiling + floor structure |
| wall openings | 15% | residential window-to-wall ratio 12-15% |
| gutters | two long eaves (gable) | gutters run along eaves, not rakes |
| garage per car | 240 SF | a 12 x 20 ft one-car garage |
| basement finish | 100% when not printed | a photographed basement room is finished |
| site area in acres | 43,560 SF/acre | definition |

### 35.2 Re-pricing the stored export (free, no model calls)

`~/basis-eval-data/work/reprice_measured.py` (private) re-ran the takeoff and the pricing on
the 376 stored assets of the client study export `11217e4e` rev 3 (E2E_ARTIFACTS A1) with
the study's own revision-3 extraction fields, then `compare_e2e_to_engineer.py` on the
result. Every input was present in the extraction (GLA, site area, state from Azure DI;
stories, basement area and finish, garage line and cars, room counts and the effective
date from the model fallback), so no fallback was used. Classification unchanged.

| | before | after |
|---|---|---|
| assets priced | 249 of 376 | **283 of 376** |
| `cross:COST_NEEDS_QUANTITY` | 49 | **15** (driveway 6, sidewalk 3, fence 2, pads 2, retaining wall 1, patio 1) |
| `cross:COST_ROW_MISSING` | 78 | 78 |
| priced total | 1.00 | 1.33 x |
| priced total / engineer's basis | 0.32 | 0.42 |
| engineer rows evidenced / bucket right | 37 / 26 | 37 / 26 (unchanged) |

Bucket shares of the priced total, against the engineer's shares of the basis (the
comparable metric; dollars are replacement cost vs allocated basis and are not compared):

| Bucket | Engineer | Before | After (all priced) | After (assets matched to an engineer row) |
|---|---|---|---|---|
| 5-year | 14.3% | 45.1% | 35.3% | 38.1% |
| 7-year | 0.4% | 5.9% | 4.9% | 5.7% |
| 15-year | 5.1% | 0.8% | 12.0% | 0.8% |
| 39-year | 80.2% | 48.2% | 47.8% | 55.4% |

Row movers (pipeline / engineer dollar ratio per engineer row, before -> after): Roofing
Systems 0 -> 0.37, Exterior Facade 0 -> 0.28, Gutter Systems 0 -> 0.37 (the three now
priced, each measured once across their 7, 12 and 4 photo assets); Flooring - Sealed
Concrete 0.88 -> 3.39 (the garage floor takes the garage area and the basement floor its
finished share instead of 150 SF each); Ceiling Systems 0.02 -> 0.01; Wood Flooring 0.52 ->
0.48; Carpet 0.98 -> 0.97; Ceramic Tile 2.75 -> 2.75; the two wall rows 47 -> 56 and 26 ->
30 (the rule has no material filter: every painted wall still answers them, now on larger
walls); every EA row x 1.11 (the state's location factor together with the year factor of
the appraisal's effective date against the 2026 default). The 15-year share's jump to 12.0%
is one asset: the landscaping upper bound (the whole unbuilt lot as sod), which matches no
engineer row (the engineer priced no landscaping) and is the reason that quantity is
flagged for review; on the engineer's rows the 15-year share stays 0.8% because the
driveway (4.5% of the engineer's basis, the largest 15-year row), the sidewalks, the fence,
the walls and the pads still need a measurement no appraisal field gives.

What the re-pricing says: the room-level items barely moved (carpet 0.97, tile 1.00,
hardwood 0.91, baseboard 1.03), so on this house the GLA shares land near the typical
areas; the measured roof, siding and gutters come out at 0.28-0.37 of the engineer's rows,
consistent with the other 39-year building rows (HVAC 0.35, doors 0.55, electrical 0.22),
i.e. a replacement-cost estimate scaled to the basis would need the basis step (section
36 when it lands) to be comparable; and the 15-year rows are a measurement problem, not a
pricing one.

### 35.3 Open

* Site measurement for the seven `needs_measurement` labels (a site plan, aerial imagery,
  or the engineer's own measurements) and the landscaping scope (which part of the lot is a
  land improvement).
* The sketch page: exterior dimensions and room dimensions would replace the allocation
  with measurements.
* The RSMeans city cost index by ZIP and the historical cost index by year (27.7).
* The intake fields (`placed_in_service_date`, `state`, `zip`) overriding the appraisal's,
  once the basis-allocation branch lands them; the export carrying the takeoff inputs so
  the end-to-end scorer can show the area source per row.

## 36. Basis and land allocation (2026-10-29)

Question: the review of 2026-10-29 (`WORKFLOW_REVIEW_2026-10-29.md` section 3, items 4 and 6)
found no basis or land math anywhere in the code and no served result: the pipeline priced what
the photos show and stopped. What does a defensible allocation step look like, does it reproduce
the design partner's printed worksheet exactly, and what does it say about the pipeline's own
replacement-cost schedule? Research: `BASIS_RESEARCH.md`. Code: `basis/domain/basis.py` (pure
Decimal arithmetic), `basis/domain/intake.py` (the intake document), `basis/evidence/result.py`
(the result shared by the export's `summary.basis` and `GET /v1/studies/{id}/result`), migration
`0011_study_intake`. Contract: `docs/api/STUDY_RESULT.md`. No model calls; the private
measurement is `BASIS_EVAL_DATA_DIR/reports/BASIS_STEP_RESULTS.md`. No client dollar appears
here.

**Method.** `B = price − land`, `CC = Σ capitalized acquisition costs`, `T = B + CC`; one
factor `f = T / ΣRCN` on every row, bucket totals and percentages, `B` and `CC` split pro rata
per bucket, and the twelve building systems when every row is a report row of the taxonomy. The
land comes from one chosen source in the ATG's order (the appraisal's site value, a land
appraisal, or a flat percentage with its source); the assessor's ratio is only a check, and
there is no residual method. Every worksheet carries its checks (land share 5–60 %, hierarchy,
site-value tolerance 5 %, assessor ratio, value date within 365 days, the 80 % test, placed in
service), and every allocation carries the `uniform_factor` check: a warning for a uniform
write-down (ATG ch. 3 A(6)) and an error for a step-up (ch. 3 D(3)–(5)). Non-capitalizable
settlement lines (points, loan costs, escrows, prepaid interest, insurance, tax prorations) are
refused at the intake with the reason. Rounding is stated once in the module: half up to the
cent; residual cents to the largest remainders (ties by schedule order); the pro-rata building
column reconciled the same way with closing = bucket − building; split parts rounded with the
last part the remainder.

**Deterministic reproduction (the engineer's 46 RCNs, B and CC).** Feeding the allocation the
engineer's building basis, closing costs and the 46 replacement costs recovered from their
printed rows reproduces the printed worksheet: the total, all 46 rows, the four bucket totals
and percentages, the 2×5 pro-rata block (both row totals) and the twelve-system depreciable
costs and their total, to the cent: **90 of 91 printed numbers** (87 of 88 by the anatomy's
count). The one miss is one cent on one system's replacement-cost column: the engineer derived
that column by dividing the rounded depreciable dollars by the factor, which on the one row split
80/20 between two systems differs by a cent from summing the split replacement costs; Basis sums
the replacement costs (the primary data) and reports the difference rather than copying the
artifact. Two of the printed pro-rata cells are not plain half-up rounding (one would round the
other way); the largest-remainder rule reproduces both because the column must sum to B. Half-up
rounding alone already sums the 46 rows to T on this study (no residual cent moved).

**The pipeline's schedule (export 11217e4e rev-3, 376 assets, 249 priced).** Allocated to the
same basis, with the pipeline's 27.5-year rows counted as the building: the schedule's
replacement cost new covers **0.317** of the basis, so the factor is a **3.15 step-up** and the
`uniform_factor` check is an error, as it should be: the schedule is missing the concealed
building systems (about a third of the engineer's dollars), the unpriceable site labels and the
measured quantities. Bucket shares against the engineer's: 5-year +30.7 points, 7-year +5.5,
15-year −4.2, building −32.0; mean absolute share error 18.1 points. These are the same shares
as the schedule's RCN shares (the uniform factor cannot change them), so the number to move is
the schedule, not the allocation: the measured takeoff (quantities from the appraisal's areas)
and a building-systems cost are the next two steps (spec 5.3, review section 6 items 2 and 5).
The twelve-system regrouping is not produced for the pipeline's schedule: its categories are
the rules table's, not the report-row vocabulary (`by_system_reason` says so).

**With the ATG-preferred land** (the appraisal's site value instead of the engineer's flat
percentage) the depreciable basis is about 0.6 % lower on this study and every land check is
`ok`; with the flat percentage the worksheet warns twice (`land_hierarchy`: the site value was
available; `land_flat_percentage`: a rule of thumb) and the site-value check passes at −4 %
inside the 5 % tolerance. That is spec question 9.2 for the engineer, now with the numbers.

**Tests.** 62 unit cases on the arithmetic and checks, 48 on the intake document, 5 on the
result, API tests for the intake routes (create, replace, appraisal defaults, 9 refusals, roles,
archived) and the result route (with and without intake, history, access), the migration round
trip; the OpenAPI contract test covers the new routes with a valid intake substituted for the
generated body. `tests/LEDGER.md`.

**Open.** Per-class depreciation before reconciliation (ATG A(6)) needs the appraisal's effective
age, condition and remaining life per class; a depreciation schedule export (CSV/XLSX) and the
report PDF; the intake is not yet required before `start` (a study without one runs and its
result says `missing: ["intake"]`); the engineer's closing-cost categories and the source of
their land percentage (spec questions 9.2).

## 37. The intake drives the takeoff, and a study cannot start without it (2026-10-29)

Section 35 left a hook: the takeoff read the appraisal's effective date and state because
the intake (section 36, `feat/basis-allocation`) landed in parallel; section 36 left the
intake optional (a study without one ran and its result said `missing: ["intake"]`).
WORKFLOW_REVIEW 2026-10-29 section 5 names what the output cannot be produced without and
asks that it become the required intake. This section closes both. No model calls; no client
dollar appears here.

### 37.1 What changed

**The takeoff reads the intake** (`process_assets`, `basis.domain.measurements.resolve_cost_index`).
Order of preference, each source written on the cost row and on every takeoff:

| Input | 1st | 2nd | 3rd |
|---|---|---|---|
| cost year | the intake's `placed_in_service_date` (`placed in service 2024-03-01 (intake)`) | the appraisal's effective date (section 35) | `COST_YEAR` setting |
| location factor | the intake's `state` (`state NY (intake)`) | the appraisal's `subject.state` (section 35) | `COST_LOCATION_FACTOR` setting |
| property class (27.5 / 39, the rules table's class, the typical-area family) | the intake's `property_use` (`intake property_use`) | `PROPERTY_CLASS` setting | |
| room areas and property quantities | the appraisal's fields (section 35, unchanged) | the typical areas | |

A candidate that was given but unusable (a placed-in-service year outside the 2020–2026
factor table, a state with no factor) is named after a semicolon in the source, so the
reviewer sees why the next one applied. `COST_INDEX=setting` still ignores the year and the
state; the use is not an index setting, so the intake's class applies under either value.
The `PROPERTY_CLASS` setting now matters only for a study with no intake (one started before
this section). A revision with no intake and no extraction fields writes exactly what it wrote
before (regression test): section 35's byte-identical claim holds.

**Required before `start`** (`POST /v1/studies/{id}/start`; `docs/api/STUDY_RESULT.md`,
"Required before `start`"). The minimum set is the review's: contract price, closing costs,
the land source with its value, the use, the placed-in-service date, and the state. The
answer for a study that lacks any of them is `422 intake-required` with one `errors` entry
per missing field (`loc ["intake", <field>]`, `type missing`, `detail` saying what needs it),
checked before the appraisal's `409 appraisal-required` so a client without an intake step
sees it first. Two consequences of the document's own rules (section 36): `acquisition_costs`
is now a required member (an explicit `[]` says the closing statement has no capitalizable
line; before, an omitted list silently meant `CC = 0`), and on a stored intake the only field
that can be missing is `state`, because the appraisal fills it only when an extraction already
exists at `PUT` time, which is after the start. A ZIP alone does not satisfy it: the index is
by state and the city index is the follow-up of section 35.3. Studies started before this
section have no intake and run on the settings, as before.

**The export says what the quantities rest on** (`summary.takeoff`, `basis.evidence.pack.takeoff_summary`).
Read back from the stored rows, not recomputed: the cost rows' year and factor, the takeoff
inputs' `cost_year_source`, `location_source`, `property_class` and `property_class_source`
(written by `process_assets` whenever a source came from the study; null on rows written
before this section or priced from settings alone), the tally of quantity sources
(`gla_share`, `appraisal_derived`, `typical_for_room_type`, ...) and the count of site items
awaiting a measurement. The end-to-end scorer (`evals.e2e_report`) prints it under "Takeoff
inputs", adds a `sources` column to the row table (each engineer row's assets by quantity
source), and says when an export predates the block.

### 37.2 The scorer on the stored export is unchanged

`python -m evals.e2e_report` on the client study export `11217e4e` rev 3 (E2E_ARTIFACTS A1;
written before sections 35–37) before and after this change, JSON output compared member by
member: `summary` identical, `buckets` identical, every row identical except the new
`sources` member. Headline unchanged: evidenced 35 of 38, bucket right 24 of 35, max bucket
share error 32.0 points. The new column says what section 35.2 measured another way: of the
376 assets, 192 were priced on `default_150_sf` and 184 on `typical_for_room_type`, none on a
measured area, and the cost index is "not recorded in this export". A fresh run of the same
study would show `gla_share` and `appraisal_derived` per row and the intake's year and state
in the block; that run is the like-for-like live run of WORKFLOW_REVIEW section 6, item 8.

### 37.3 Tests

`tests/LEDGER.md`: `resolve_cost_index` with the intake's state and date ahead of the
appraisal's, an unusable intake value falling through with the miss recorded, the intake
alone with no appraisal field (3 cases); `missing_for_start` and the required
`acquisition_costs` (3); `process_assets` with an intake (the index and the class from the
intake, every takeoff carrying the sources; an intake without a state; the intake alone; the
`setting` index with the intake's use; no source keys without an intake or fields: 5); the
export's `summary.takeoff` from rows with and without the sources (1); the scorer's `sources`
column and block with the scores unchanged (4); the start route with no intake, a partial
intake, a complete intake set later (3); the lifecycle of a study with a complete intake
through start, the gates and the export on the real workflow (1). Gates: ruff, format, mypy,
`tests/unit`, `tests/api tests/integration/api tests/integration/db`, all green; the OpenAPI
document regenerated (`start` documents 409 and 422; `acquisition_costs` required).

### 37.4 Open

* No client has an intake step yet: the vision review workbench (the frontend on its own
  branch) starts a study without one and will now get `422 intake-required`. The intake
  form (price, closing-statement lines, land source, use, dates, state) is the next
  frontend task; until then `PUT /v1/studies/{id}/intake` by hand lets a study start.
* Nonresidential by use is not commercial by building: the typical-area fallback
  (`ROOM_AREA_ESTIMATES`, used only when no GLA is extracted) switches to the commercial
  family for a nonresidential intake, as it did for the `PROPERTY_CLASS` setting before. A
  short-term-rental house with no GLA would get office-sized rooms. The family should follow
  the building type, not the tax class; left as before until the sketch reader replaces the
  fallback.
* The RSMeans city cost index by ZIP (35.3); `zip` is stored for it.
* The per-row `sources` on a fresh export, and the intake-driven year and state against the
  engineer's cost source, wait for the like-for-like live run.

## 38. Two live end-to-end runs: the pipeline alone, then the engineer at every gate (2026-10-29)

The like-for-like run WORKFLOW_REVIEW 2026-10-29 section 6 item 8 asked for, on the local
stack built from main `9c8f443` (sections 34–37 all in): the client property's appraisal and
52 photos uploaded through the API, the intake set from the engineer's worksheet, every gate
driven through `POST /decisions`, the export scored with `evals.e2e_report`. Two runs on the
same inputs: **run 1** approves every gate untouched (the system alone); **run 2** acts as the
engineer at each gate, correcting the stage's output against the verified labels with the
API's own `corrections` (a reject carrying them, then an approve of the next revision). The
private write-ups with the client's figures are `reports/E2E_LIVE_RUN1_2026-10-29.md` and
`E2E_LIVE_RUN2_2026-10-29.md` under `BASIS_EVAL_DATA_DIR`; the drivers are private too
(`work/live_e2e/`: they carry the intake). Worker configuration: photo analysis gpt-6.1-sol
decomposed with gpt-6-luna surfaces, count fusion claude-opus-5-5, room step gpt-6-luna with
claude-sonnet-5-5 materials, extraction grounded on gpt-6.1-sol, classifier claude-sonnet-5-5,
RSMeans 2020 table, SAM 3 on Replicate; providers openai+anthropic (the stale Azure OpenAI
keys in the checkout's `.env` had to be blanked: with them set, Azure is the primary for every
gpt model).

### 38.1 What the engineer step posted (run 2)

| gate | corrections | verified after |
|---|---|---|
| appraisal_review | 3 `extraction_field` (2 wrong values, 1 spurious cleared) | 0 of 126 present truth fields wrong; 5 truth fields have no row and **cannot be added** |
| rooms_review | 18 `room`: 16 merges (the labels' aliases) + 2 `room_type` | 33 rooms = the labels' 33 |
| components_review | 81: 53 `component` count fixes + 28 `room_component` additions | 1 remaining; **53 extra rows cannot be removed** (no such correction; counts are ≥ 1) |
| assets_review | approve only | |

### 38.2 Timeline and spend

| | run 1 | run 2 |
|---|---|---|
| wall clock | 29.6 min, of which **19 min lost to defect A** (below); 10.5 min of work | 11.0 min |
| extraction (Azure DI + one Sol call) | 84 s | 2 × ~90 s (the appraisal reject re-ran the model call) |
| photo localization + analysis, 52 photos (parallel with extraction) | ~5 min | 4.7 min |
| room step | 25 s | 64 s |
| classification + takeoff + cost | 296 s (486 Sonnet calls, 382 assets) | 217 s (386 calls, 310 assets) |
| each gate (reject → next revision's gate) | | 10 s (rooms, components), 3 min (appraisal) |
| spend (llm_calls + Replicate) | $7.67 | $6.63 |
| of which classifier / Opus count fusion / Sol photos / extraction / room step / SAM 3 | 5.41 / 1.47 / 0.51 / 0.08 / 0.14 / 0.04 | 4.29 / 1.48 / 0.51 / 0.15 / 0.15 / 0.04 |

Order observed: extraction and the photo stage start together at `start`; the appraisal
gate opens when extraction ends; the rooms gate shows the per-photo grouping before any room
step call (the step runs after the grouping is approved); the components gate 25–64 s later;
classification after the components approval; export in seconds. The ledger (`llm_calls`)
recorded every call with tokens and cost (724 and 528 rows, no nulls); `classification_cache`
was empty in both runs.

### 38.3 Scores (evals.e2e_report, data-free)

| metric | old export 10-19 | run 1 | run 2 |
|---|---|---|---|
| assets / priced | 376 / 249 | 382 / 291 | 310 / 244 |
| evidenced rows (of 38 photo-visible) | 35 | 35 | 36 |
| bucket right by count (of evidenced) | 24 | 24 | 24 |
| wrong-bucket rows | 11 | the same 11 | the same 11 + 1 |
| mean / max row share error (pts) | 2.47 / 12.66 | 2.08 / 12.66 | 2.00 / 12.66 |
| total variation distance, rows (pts) | 56.9 | 47.8 | 46.1 |
| bucket share error y5 / y7 / y15 / y39 (pts) | 30.7 / 5.5 / 4.2 / 32.0 | 23.0 / 2.1 / 6.9 / 32.0 | 23.7 / 2.0 / 6.1 / 31.8 |
| pipeline bucket shares y5 / y7 / y15 / y39 (%) | 45.1 / 5.9 / 0.8 / 48.2 | 37.4 / 2.5 / 11.9 / 48.2 | 38.0 / 2.4 / 11.2 / 48.5 |
| quantity sources (gla / basement / garage / appraisal-derived / 150 sf default) | – | 236 / 47 / 8 / 34 / 57 | 226 / 44 / 8 / 7 / 25 |
| basis allocation uniform factor | – | 2.44 (step-up: error, as designed) | 2.35 |

Reading. (1) Sections 35–37 moved the pricing, not the classification: against the October
export, 42 more assets priced, the row-share distance 57 → 48 points, y5 and y7 shares toward
the engineer's, y15 overshooting; the vision and classification outcome is the same (same
rows evidenced, same 24 right, the same 11 wrong, which are rule conventions). (2) The
engineer's corrections fix the inventory, not the finding: merging 49 rooms into 33 and
bringing the counts to the labels' removed 72 assets (windows 29 → 19, siding 12 → 2, roof
9 → 2) and moved the row distance by 1.7 points; no row changed bucket, and y39 stayed at 48
against the engineer's share. (3) The remaining gap is in three places the gates do not
reach: the 11 classification conventions (a sixth of the engineer's dollars; the engineer's
rulings, WORKFLOW_REVIEW item 7), the concealed building systems (two fifths; nothing produces
them, so the basis step must scale the schedule up 2.3–2.4×, and its `uniform_factor` check
reports the error), and the site items that need a measurement (driveway, sidewalks, fence,
retaining wall: evidenced in both runs, priced in neither). (4) Quantities from the appraisal
hold: roof, siding and gutters land within 0.3–1.8 share points of the engineer's in both runs.

### 38.4 Defects and gaps found by running it

- **Defect A (blocking on a fresh worker).** `ingest_appraisal` embeds the appraisal with
  sentence-transformers all-MiniLM-L6-v2; a worker that has never loaded the model downloads
  it (88 MB) inside the first `embed` call with no heartbeat, longer than the activity's 30 s
  heartbeat; three attempts fail, the stage fails and the study waits for `POST /retry`. The
  photo stage pauses meanwhile. The previous container only worked because it had cached the
  model days earlier. Fix in flight: warm the embedder at worker start, heartbeat before the
  first batch, bake the model into the image.
- A truth field with no extracted row cannot be added at the appraisal review (5 of 126).
- No correction removes an inventory row at the components review.
- Rejecting the appraisal review re-runs the model extraction on the same PDF instead of
  applying the corrections to the stored answer.
- The rooms gate proposes nothing: 49 rooms for 52 photos, one per filename stem; the
  aliases and the appraisal's room count are not consulted, so 16 merges are made by hand.
- 486 classifier calls for 382 assets with an empty `classification_cache`: 71 % of the spend.
- The stale Azure OpenAI variables in `backEnd/.env` make Azure the primary for gpt models on
  any stack that loads that file.
- The ledger stores tokens, cost, latency, model and prompt version per call but not the
  rendered prompt, the response, the cache/reasoning token split, the entity a call was
  about, Azure DI page cost or a per-study rollup (a build is in flight).

### 38.5 What this settles and what is next

The workflow runs end to end on today's defaults in about 11 minutes and $7 per study, with
every step accounted for in the ledger, and the engineer's output format (the allocation by
bucket) is produced from the two inputs plus the intake. Between the system alone and the
engineer at every gate there is almost no difference in the finding; the difference the
engineer makes is in the inventory. The next moves, in order of effect on the finding: the
engineer's rulings on the 11 conventions (deterministic, no model spend); a cost model or
cost-approach carve-out for the concealed systems; measurements for the site items; then the
second property.

## 39. A fresh worker ingests its first appraisal: the embedding model is loaded before polling (2026-10-29)

In the live end-to-end run of 2026-10-29 (section 34's follow-up) a worker on a freshly
built container (main `9c8f443`) failed `ingest_appraisal` three times with
`activity Heartbeat timeout`. The first `embed` of the process downloaded and loaded
`sentence-transformers/all-MiniLM-L6-v2` inside the activity's first batch
(`asyncio.to_thread`), nothing heartbeat until that batch returned, and the activity's
heartbeat timeout is 30 s (`activity_options.py`). The stage failed, the study parked at
`failed_stage: extracting` until a manual `POST /v1/studies/{id}/retry`, the photo stage
paused with it, and 19 minutes were lost; the retry passed in 2 s because the model was
cached by then. The container the previous runs used had cached the model days earlier,
which is why nothing had shown this. No model calls; no client dollar appears here.

### 39.1 Reproduction

`SentenceTransformerEmbedder().embed(64 texts)` on main's code, timed on this machine
(M-series, home network) with `HF_HOME` pointed at an empty directory, then again on the
same directory:

| Cache state | First `embed` | Second `embed` |
|---|---|---|
| empty (download 90.9 MB `model.safetensors` + tokenizer, then load) | **186.8 s** | 0.01 s |
| same run repeated later the same afternoon (download fallback, 39.4) | **298.5 s** | 0.03 s |
| warm (files present, Hub reachable: one HEAD per file) | 7.8 s | 0.01 s |
| warm, `HF_HUB_OFFLINE=1` | 6.7 s | 0.01 s |

So a cold cache is six to ten times the heartbeat window, and even a warm load is a quarter
of it on a fast laptop; a throttled Cloud Run vCPU would not have much margin either. In a
fresh container of main's image (`basis-backend:before`, built from `main:backEnd`) the first
`embed` with the Hub unreachable raises transformers' `OSError: couldn't connect ... and
couldn't find them in the cached files`; with the network on it is the download above
(39.4).

### 39.2 The standard

Hugging Face's own guidance for deployed code is to populate the cache ahead of time and
read it without the Hub: `HF_HOME` sets where the cache lives, `HF_HUB_OFFLINE` makes the
library "make no HTTP calls to the Hugging Face Hub" and read only cached files, and the
documentation notes that "even if the latest version of a file is cached, calling
`hf_hub_download` still triggers a HTTP request to check that a new version is not
available" (the 1.1 s between the warm rows above). `SentenceTransformer` takes `revision`
and `local_files_only` for the same purpose per model. Temporal's guidance for a long or
blocking activity is to heartbeat from the activity on a timer independent of the blocking
work, so that the heartbeat timeout detects a dead worker and not a slow step. Both are
applied below; neither replaces the other.

### 39.3 What changed

Three layers, each enough on its own for the failure above, in the order a request meets
them:

1. **The image bakes the model** (`backEnd/Dockerfile`). After the dependency layer and
   before the code is copied (so a code change does not re-download it), the build loads
   the model the way the worker does, at a pinned revision, into `HF_HOME=/opt/hf`, owned by
   the non-root user: 88 MB, `basis-backend` 519 → 603 MB. The model name and revision are
   two `ARG`s; `tests/unit/retrieval/test_embed.py` fails when they drift from the code's
   `DEFAULT_MODEL` / `DEFAULT_MODEL_REVISION`. CI's image job gained a second smoke step
   that warms the model inside the built image with `HF_HUB_OFFLINE=1`.
2. **The worker loads the model before it polls** (`basis.worker.main.warm_embedder`, next
   to the BM25 warm-up, before `build_worker`). The log line says where the weights came
   from and how long it took: `embedding model warm: sentence-transformers/all-MiniLM-L6-v2
   from cache in 4.8 s` (with `source`, `seconds` and the revision as fields). A model that
   cannot be loaded stops the worker with a CRITICAL line (`/health` stays `starting`, the
   startup probe fails): a worker without the model would fail every study at ingestion,
   and a worker that is not polling is visible where a parked study is not. The hash
   embedder (dev, test) logs `needs no warm-up`.
3. **The embedding loop heartbeats on its own** (`basis.retrieval.ingest._embed`):
   `progress` (the ingest activity's heartbeat) is called before the first batch, so the
   first `embed` of a process is preceded by a heartbeat whatever it loads, after every
   batch as before, and every `EMBED_PROGRESS_S = 5 s` while a batch runs on its thread
   (three reports per 30 s window; no finer than the worker's 5 s send throttle). Against a
   real Temporal dev server with a 1 s heartbeat timeout, an embedder that blocks 2.5 s per
   batch completes on its first attempt with the reports and fails with a `HEARTBEAT`
   timeout without them (`tests/integration/retrieval/test_embed_heartbeat.py`).

Underneath, `basis.retrieval.embed` pins the default model to Hub commit
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (`ModelSpec.revision`; the corpus embeddings
must come from exactly the weights that embed the queries, and the corpus was embedded by
this revision) and loads it cache-first: `SentenceTransformer(name, revision=pin,
local_files_only=True)`, no request to the Hub; when that raises transformers' `OSError`
(the revision is not cached) it downloads the pin with a warning naming the Dockerfile. The
download therefore stays as the fallback the task asked for, and a cached worker needs no
egress to the Hub at all. `Embedder.warm()` is on the protocol; `model_is_cached` takes the
revision (the real-model tests skip on it as before).

**Visible and recoverable without an operator.** `GET /v1/studies/{id}/workflow` already
reported `status.failed_stage` and `status.failure` (section 27); `docs/api/WORKFLOW.md`
now documents the route, the retry (`POST /v1/studies/{id}/retry`: 202 with the stage that
runs again, 409 `study-not-failed` / `workflow-closed` / `study-archived`), a client's poll
loop, and the note that the photo stage schedules nothing more while a stage waits for a
retry and resumes with it (the rows already written are reused).

### 39.4 Verification

| Measurement | Before (main `9c8f443`, `basis-backend:before`) | After (this branch, `basis-backend:warm`) |
|---|---|---|
| fresh container, Hub unreachable, first embed | `OSError` (no cache in the image) | `warm()` **2.9 s from cache**, as user `basis`, `HF_HUB_OFFLINE=1` and the endpoint a dead port; the 64-text embed after it 0.10 s |
| fresh container, network on, first embed | **137.7 s** (download + load, inside the activity) | 0.10 s after the warm-up (the load happened before polling) |
| worker start (container, Temporal dev server on the host, no database) | model loaded by the first study's `ingest_appraisal` | `embedding model warm ... from cache in 4.8 s` → `worker started` → `/health` `ok`; SIGTERM drains and stops in 2 s |
| host, cache warm, Hub endpoint a dead port | 7.8 s with a HEAD per file (fails if the Hub is unreachable) | 6.7 s, `source=cache`, no request made |
| host, cache empty (the fallback) | 186.8 s / 298.5 s | 298.5 s with the warning, `source=download` |
| image content size | 519 MB | 603 MB (+88 MB cache) |

Tests (all run here; the full suite's count is in the PR):

| File | Adds |
|---|---|
| `tests/unit/retrieval/test_ingest_embed.py` (new, 6) | a report before the first batch, after each batch, every `every` s during one; nothing for nothing; a short count; cancellation stops the ticks; the interval fits the timeout and the throttle |
| `tests/integration/retrieval/test_embed_heartbeat.py` (new, 3) | real dev server, 1 s heartbeat timeout, 2.5 s batches: first attempt with the reports, `HEARTBEAT` timeout without |
| `tests/unit/retrieval/test_embed.py` (+5) | the pin and `local_files_only` load, the download fallback with its warning, `warm` once per process, `model_is_cached` at the pin, the Dockerfile's ARGs equal the code's pin (a stand-in `sentence_transformers` module: no torch) |
| `tests/unit/worker/test_worker_parts.py` (+5) | `warm_embedder` logs source and duration off the event loop; the hash embedder's no-op; CRITICAL and re-raise; `run_worker` warms before `build_worker` and polls only after; refuses to start without the model |
| `tests/integration/retrieval/test_real_embedder.py` (+1, regression) | the real cached model warms with `HF_HUB_OFFLINE=1`, `source=cache` at the pin, within 30 s; the first embed after it is faster than the warm-up |

The CI skip for the real model (not cached → skip, never a download in tests) is unchanged.

### 39.5 Decisions a reviewer may ask about

* **Refuse to start, not degrade.** The BM25 warm-up degrades (the index is built on first
  use) because a failure there costs seconds on the first classification. A missing
  embedding model costs every study its first stage; the codebase's rule for that class is
  fail at startup (`check_models_served`, the cost table, the prompt manifest). With the
  model baked in, the only way to hit it is an image built without the bake or a changed
  `HF_HOME`, which should stop a deploy.
* **The pin lives in code and the Dockerfile, with a test keeping them equal**, rather than
  the Dockerfile importing the code (that would put the 88 MB layer after the code layer and
  re-download on every build) or the code reading the Dockerfile.
* **`local_files_only` per load rather than `HF_HUB_OFFLINE=1` in the image**: the
  environment variable would also forbid the fallback download the task keeps; the
  argument gives the cached path no network and the uncached path a download.
* **A 5 s ticker rather than a per-batch heartbeat alone.** A per-batch heartbeat does not
  cover the batch that loads the model, nor a batch waiting behind nine other studies on the
  encode lock (section 32.4 ran ten at once); the ticker covers both and costs one
  `asyncio.wait` per 5 s.
* **CI proves the bake with `HF_HUB_OFFLINE=1`, not `--network none`**: on this Docker
  Desktop `--network none` wedged container creation twice (containers stuck in `Created`);
  the library's offline mode is the documented switch and portable.

### 39.6 Left open

* The API serves from the same image and never embeds (the worker's `process_assets` embeds
  the evidence queries), so it pays nothing for the layer; the ingest job
  (`python -m basis.retrieval.ingest`) gains the same no-download start.
* Local `docker compose` images need a rebuild to pick up the layer; until then those workers
  take the warned download path once per container, then run from the cache.
* The CI image job is still `continue-on-error` (Stage 5 makes it a gate); the new smoke
  step reports in its summary like the first.
* Updating the model revision is a deliberate change: the pin, the Dockerfile ARG, and a
  re-ingest of every corpus the queries must match (`corpus_versions.embedding_model`
  records the model name, not the revision; recording the revision there is a follow-up).

## 40. The paid-call ledger: every call, every token, every dollar, with its prompt and answer (2026-10-29)

The live run of section 34 (study 422de6fb, 724 model calls, $7.67) showed `llm_calls`
complete for what it stored and far short of what the product needs: "input and output
tokens, cost per study, exact cost and tokens at every single step, prompts being saved, the
exact model and component we are calling in our system; super detailed, save everything."
This section makes `llm_calls` that ledger. No model was called for it; no client dollar
appears here (the fake lifecycle run and the unit fixtures only).

### 40.1 The standard

OpenTelemetry's GenAI semantic conventions (the registry, read 2026-10-29; the spec now lives
in `open-telemetry/semantic-conventions-genai`) name what a model call is recorded with, and
the two providers' usage objects say what is reported. The ledger's columns follow them:

| OpenTelemetry | Column | Source |
|---|---|---|
| `gen_ai.provider.name`, `server.address` | `provider`, `endpoint` | the provider; the SDK client's base URL (Azure: the resource endpoint) |
| `gen_ai.request.model` / `gen_ai.response.model` | `requested_model` / `model` | the request; `completion.model` (a dated snapshot) / `message.model` |
| `gen_ai.response.id`, `gen_ai.response.finish_reasons` | `response_id`, `finish_reason` | `completion.id` + `choices[0].finish_reason`; `message.id` + `stop_reason` |
| `gen_ai.request.max_tokens`, `.temperature`, effort, output type | `max_output_tokens`, `temperature`, `reasoning_effort`, `response_format`, `request_params` | the body as sent: `max_completion_tokens` or `max_tokens` (on Claude the answer budget plus the thinking headroom), the effort in effect (the provider's default when the request set none), no temperature on reasoning models, the schema's name, the tool names, the images counted and sized |
| `gen_ai.usage.input_tokens` (includes cached), `.output_tokens` (includes reasoning), `.cache_read.input_tokens`, `.cache_creation.input_tokens`, `.reasoning.output_tokens` | the five token columns | OpenAI: `prompt_tokens` with `prompt_tokens_details.cached_tokens`, `completion_tokens` with `completion_tokens_details.reasoning_tokens`. Anthropic: `input_tokens` is the uncached part alone, beside `cache_creation_input_tokens` and `cache_read_input_tokens` (summed into the total; no reasoning split) |
| `gen_ai.prompt.name` | `prompt_version`, `prompt_name`, `prompt_sha256` | the released prompt file and its manifest hash |
| `code.function.name` / `code.namespace` | `component` | the nearest caller of `call_model` outside `basis.llm`, read from the stack (`basis.vision.analyze.call_until_usable`, `basis.classify.residual.run_residual`, ...) |
| `gen_ai.input.messages`, `gen_ai.system_instructions`, `gen_ai.output.messages` (opt-in, sensitive) | `request_uri`/`request_sha256`, `response_uri`/`response_sha256` | stored out of band through the storage abstraction |

Not in the conventions, needed here: `stage`, `activity`, `activity_attempt`, `revision`,
`run_id`, `build_sha` (where in the workflow), `entity_type`/`entity_id` (what the call was
about), `kind` (`chat`, `document_analysis`, `localization`, `segmentation`), `status`/`error`
(a billed call that failed), `quantity`/`unit`/`unit_price_usd` (the non-token steps),
`price_row`/`pricing_version` (how the cost was computed), `elapsed_ms` (the call's wall time
with the provider's retry and the pool's fallback; `latency_ms` is the answering round trip).

Prices, checked 2026-10-29 (`basis/llm/cost.py`, `PRICING_VERSION = "2026-10-29"`): the
providers bill each part of the usage at its own rate, so the table now carries a cached-input
and a cache-write price per row. OpenAI's cached-input column per model (gpt-4.1 $0.50,
gpt-4o $1.25, the gpt-5 and gpt-6 families at a tenth of base, gpt-6.1-sol $0.10); Anthropic's
cache writes at 1.25x base (the 5-minute cache) and hits at 0.025x on Claude Fable 5.1
($0.25), 0.05x on Opus 5.5 ($0.20) and Sonnet 5.5 ($0.10), 0.1x on Haiku 5.5. Azure AI
Document Intelligence S0 pay-as-you-go, prebuilt-layout: $10 per 1,000 pages
(`AZURE_DI_USD_PER_PAGE`, default 0.01; the official page renders its prices client-side and
every secondary source agrees; the key-value pairs feature is not a priced add-on, the add-on
row lists High Resolution, Font and Formula). Replicate: predict seconds at the hardware's
rate, as the localizer already computed. Not modelled: batch and `inference_geo` multipliers
(unused), Haiku 5.5's over-100K-token tier.

### 40.2 Before and after

| | Before (section 34's rows) | After (migration `0012_call_ledger`) |
|---|---|---|
| per model call | study, run, activity, provider, the answering model, `prompt_version`, input and output tokens, cost, latency, time | plus: stage, revision, attempt, Python module and function, prompt name and sha256, worker build; the entity (photo document, room, component, appraisal); the requested model, endpoint, response id, request id, finish reason; effort, output cap, temperature, response schema, image count and bytes, tool names (`request_params`); cache-read, cache-write and reasoning tokens; price row and table version; wall time; status and error; the stored request and response (URI + sha256) |
| Anthropic cache tokens | folded into `input_tokens`, the split lost, a cache read priced as plain input | kept apart and priced at the cache rates (regression test: 4,000 cached tokens on Opus 5.5 cost $0.0008, not $0.016) |
| OpenAI cached and reasoning tokens | not read | read from the usage details, priced (cached) and recorded (reasoning) |
| a call that failed after reaching the model | a row with usage and cost, nothing saying it failed | `status = error` with the exception and the finish reason |
| the classifier's calls (`process_assets`) | rows written by the activity in its work transaction (lost with a rollback; no parameters); the tool calls of the loop traced in memory and discarded | recorded by the ledger as each call returns, about the component classified; the stored response lists the tool calls that answer asked for (name, arguments, refused or not, the chunks returned, the tool's duration and the size of its result) |
| Azure Document Intelligence (`extract_fields`) | not recorded; not in the study's spend | `kind = document_analysis`: pages analyzed at the list price, model id and API version, latency, about the appraisal document; a refused document is an error row with no page billed |
| SAM 3 (`localize_photo`, the engineer's box prompts in the API) | `photo_localizations.cost_usd` and `object_prompts.cost_usd` only | `kind = localization`: one row per prediction with the prediction id, predict seconds at the backend's rate, the phrases sent; a failed Replicate prediction (still billed) is an error row |
| the surface segmenter (Replicate Mask2Former) | charged to the budget, recorded nowhere | `kind = segmentation`, one row per prediction |
| prompts and answers | per-photo analyses kept the raw answers under `analyses/`; nothing for the other steps; no prompt anywhere | `studies/<id>/llm/<call id>.request.json` (system prompt, messages as sent, each image replaced by its ref, media type, byte size and sha256, the tool definitions, the response schema) and `.response.json` (the raw provider response, the usage as reported, the cost); API-key-shaped strings redacted before writing |
| served | `budget_remaining_usd` in the workflow view | `GET /v1/studies/{id}/spend` (the rollup by stage, activity, component, model and kind: calls, errors, tokens of every kind, dollars, latency, pages or seconds), `GET /v1/studies/{id}/calls` (the rows, oldest first, keyset pages, `activity`/`kind`/`entity_id` filters), the export's `summary.spend` and `rev-<n>.calls.jsonl` (one line per call: the row and the stored prompt and answer); `docs/api/SPEND.md` |

`ingest_appraisal` does not call Azure Document Intelligence (it reads the PDF's text layer
and chunks it); only `extract_fields` does, once per document (the analysis is reused across
revisions). Rows written before this migration keep `kind = chat`, `status = ok` and null in
every new column: the information was not recorded then.

### 40.3 How it is wired

`basis.llm.call.CallContext` carries who pays and where the call comes from (stage, revision,
attempt, entity); each activity builds it from one read (`_model_calls.call_origin`: the run
row, the study's current stage, `activity.info().attempt`) and narrows it per entity
(`ctx.about("room", room.id)` in the room step, `about("component", ...)` in the classifier,
the photo document in `analyze_photo` and `localize_photo`, the appraisal document in
`extract_fields`). `call_model` records the response or the error (both now carry the response
id, finish reason, endpoint and the parameters as sent) and names the caller from the stack.
The recorder (`basis.llm.recorder.LlmCallRecorder`, built by `Deps.ledger()` with the
process's storage, bucket and `git_sha`) prices the entry (`price_entry`: the token parts
by the table, a metered step by what the provider charged), archives the two payloads, then
writes the row in its own short transaction, as before, so a rolled-back or retried activity
never loses a spent call; a storage failure is logged and the row is still written without
its references. The non-token steps go through the same recorder (`step_entry`): the
extraction pipeline around `azure.analyze`, `localize_photo` over the localizer's
`LocalizeCall`s (a failed prediction now carries its call on the exception), the crop step
over the segmenter's (the protocol gained a `calls` out-parameter), and the objects route for
the engineer's prompts (activity `object_prompt`, no run).

Rollups are one `GROUP BY` per grouping over the study's rows (`LlmCallRepository.spend`),
ordered by cost; the page is a keyset over `(created_at, id)` served by the new index. The
export reads the rollup into `summary.spend` and builds the transcript from the rows and their
stored payloads; the ledger is append-only and the export makes no paid call, so two exports
with no call between them stay byte-identical (the existing test).

### 40.4 Storage per study

Measured with the released prompts and a fixture photo (`MemoryRecorder`, the exact bytes the
recorder writes): a per-photo call stores a 5.7 KB request (the 4.7 KB countables prompt, the
image as a 150-byte record; the 86 KB of base64 are not stored) and a 1.5 KB response; a
classification call 3.3 KB and 0.8 KB; the grounded extraction over six pages 21 KB and 9 KB.
Applied to the live run's mix (724 calls, 2.8 M input tokens, 165 K output tokens; about
0.3 M of the input tokens are images, which the archive does not store): requests about
4 bytes per text token, 10 MB; responses 165 K tokens plus the raw envelope, about 1.2 MB;
the 724 rows with their `request_params`, about 1 MB in Postgres; the export's transcript
duplicates the payloads, about 11 MB per revision. Call it 12 MB of storage and 1 MB of
database per study per revision, against the 50 to 150 MB the study's photos already occupy;
a class-A write per payload at GCS prices is a tenth of a cent per study. These are
estimates from the measured per-call sizes, not a measurement of a live study.

### 40.5 Tests and gates

`tests/LEDGER.md`: the price table prices every usage part at its own rate on both providers
and refuses the pipeline a model without a row (4); the Anthropic provider keeps the cache
split and prices it (the fold regression) and reports the response identity and the sent
parameters (2); the OpenAI providers read the cached and reasoning details and carry the
identity on a bad response too (3); `call_model` names the calling component, archives both
payloads, writes an error row for a bad response, redacts API keys in the prompt and the
answer while keeping hex ids, and replaces image bytes by their record (5); the classifier's loop with a ledger records each call about its component with the tool calls it asked for, their outcome and duration (1); the recorder on
Postgres writes every column and the payload references, sums the spend across a rolled-back
caller, and rolls up and pages metered steps with model calls (3); the two routes (3); the
export's `summary.spend` and `rev-1.calls.jsonl` (in the export test); the extraction tests
see the Azure DI row beside the model calls (6 updated); the schema spec (19 JSONB columns,
the new not-null columns) and the migration history to `0012`; the fake lifecycle run with a
fake localizer: every row has its component, stage, attempt, build and run, every model call
its prompt, parameters and payloads, every call its entity, the analysis and the three
predictions priced per page and per second, the rollup summing to the rows in every grouping,
the export carrying the same totals and one transcript line per call (1).

Gates (`backEnd/`): `ruff check`, `ruff format --check`, `mypy` and
`env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY uv run --no-sync pytest -q` (the full suite as CI
runs it, Docker up): all green on 2026-10-29 (4,946 passed, 23 skipped, 36 subtests, 11 min 52 s; Docker up for the testcontainers suites). The OpenAPI document was regenerated.

### 40.6 Open

* The live re-run with the ledger on (section 34's study, same defaults) has not been made:
  no paid call was allowed from this branch. The storage figures above are extrapolated from
  measured per-call sizes; the first live run should be read back with
  `GET /v1/studies/{id}/calls` and the transcript and the numbers corrected here.
* `request_id` (the provider's `request-id` header) is read from the SDK's `_request_id`;
  the mock transports in the tests send none, so it is verified only as "carried when
  present".
* The pool's fallback: the row names the provider that answered; which provider was tried
  first is in the worker log only.
* The price table is still a Python constant. A row change bumps `PRICING_VERSION`; a stored
  row keeps the version it was priced with, so a later table never rewrites history.
## 41. Every study's history made legible: error codes, a per-study timeline, the AI-versus-engineer audit trail (2026-10-29)

Bryan's asks (2026-10-29): pinpoint every step and assign error codes to every way it can fail,
with one place that says what happened, what the user sees and how to fix it; timestamps and
durations on every step so slow steps can be found; and a full audit trail of what the AI did
versus what the engineer changed afterwards, at which step, when, by whom, so AI-made and
engineer-modified rows are always distinguishable. Section 38 showed the gap on live runs: a
stage failure surfaced only as the free text `ingest_appraisal: activity Heartbeat timeout`, and
a study's timeline had to be reconstructed from the worker's log. No model calls; no client
dollar appears here. Section 40 is the paid-call ledger (PR #88), built in parallel: nothing here
touches `llm_calls`.

### 41.1 Standards read first

RFC 9457 keeps `type` as a problem's primary identifier and allows named extension members
(section 3.2), so the code is a `code` member beside `type`, never a replacement. The
PostgreSQL SQLSTATE appendix (a class, then a member; numbers never reused), Stripe's error-code
table (one row per identifier with meaning and next step) and Google AIP-193's `ErrorInfo` (a
stable identifier beside the human text) shaped the catalogue. OpenTelemetry's span conventions
(a start, an end, a status set only on failure, a low-cardinality `error.type`) shaped a step;
its exceptions guidance keeps the exception class as `error.type` and the domain code in its own
attribute, which is why logs carry `error_code`. Append-only audit tables (before/after JSON, a
database-stamped time, written in the same transaction as the change, UPDATE/DELETE forbidden by a
trigger) are the convention `reviews` and `corrections` already follow; W3C PROV's
`wasDerivedFrom` / `wasAttributedTo` is the vocabulary behind `provenance`. Temporal's history
cannot give per-attempt timing (`ActivityTaskStarted` is written when the attempts end), so step
timings are recorded on the worker.

### 41.2 The error-code catalogue (`docs/ERROR_CODES.md`)

| | |
|---|---|
| codes | 213 in 18 areas: API 31, FIRM 10, UPLOAD 10, STUDY 17, OBJECT 9, WF 17, PROVIDER 10, BUDGET 5, INGEST 6, EXTRACT 8, PHOTO 3, LOCALIZE 3, ROOMS 2, CORRECT 26, ASSETS 11, EXPORT 1, CHECK 41, INF 3 |
| by kind | 76 refusals (an API 4xx), 79 errors (a step failed), 47 flags (a check put a row in front of the engineer), 11 outcomes (a recorded state) |
| by retry | 150 need a change first, 39 repeat the request, 19 `POST .../retry` after the fix, 5 retried by Temporal |
| format | `BASIS-<AREA>-<nnn>`; numbers never reused |
| source of truth | `basis/errors/codes.py` (one constant per code) and `basis/errors/catalogue.py` (step, component, what happened, what you see, how to fix, retry; the problem type suffix, the Temporal type or the review-reason string it stands for); the document is rendered from it (`uv run python -m basis.errors.catalogue --write`) |

Where the code shows, so the catalogue can be looked up from any of them:

* every problem response: `code` next to `type` (`{"type": ".../intake-required", "code":
  "BASIS-STUDY-001", ...}`); the seven 401 details, the six 503 sites and the 404s that shared a
  suffix each got their own code (passed at the raise site; a suffix shared by several causes
  names its generic in `GENERIC_FOR_PROBLEM`);
* every activity failure: ` [BASIS-...]` at the end of the message, so it rides
  `activity_results.error`, the study's `failure` / `failure_reason`, the worker log and
  Temporal's failure unchanged (the way PostgreSQL prints the SQLSTATE on every log line);
  `failure_code` beside the text on the status view, `WorkflowProgress`, the study row
  (`studies.failure_code`) and `RecordFailureInput`. At the end, not the front: the first run
  of the full suite put it in front and twelve tests that read `<activity>: <type>: <message>`
  by its prefix failed (the account-stop and config-stop lifecycle tests among them), which is
  the "add, never rename" rule making itself heard;
* a Temporal failure with no exception of ours: coded by its type. The live run's
  `ingest_appraisal: activity Heartbeat timeout` is now
  `ingest_appraisal: activity Heartbeat timeout [BASIS-WF-001]`, `failure_code BASIS-WF-001`
  (`tests/unit/errors/test_catalogue.py::test_the_heartbeat_timeout_of_the_live_run_has_a_code`);
  the other live refusal, `intake-required` on start, is `BASIS-STUDY-001`;
* `review_codes` beside `review_reasons` on `/assets` and `/extraction`: the codes behind
  `validation:SECTION_BUCKET_MISMATCH`, `cross:COST_ROW_MISSING`, `rule_contested:<key>`,
  `missing:<key>` and the rest of the vocabulary (`codes_for_reasons`);
* the API log line of every problem and the relay's refusals carry `error_code`.

No dead entries: `test_every_code_is_alive` walks `basis/` and refuses a code that no raise site
references, no problem suffix maps to, no Temporal type falls back to and no review-reason string
stands for; `test_no_unknown_code_literal_in_the_code_base` refuses a `BASIS-` literal typed
outside the codes module; `test_every_problem_type_suffix_the_api_raises_is_catalogued` refuses
an uncatalogued `type_suffix`; `test_document_is_current` refuses a stale document.

### 41.3 The per-study timeline (`study_events`, migration 0013)

One append-only table (the `forbid_mutation()` trigger of `reviews` and `corrections`), one row
per occurrence, written in the same transaction as the change where there is one; `seq` is the
order. Writers: the study routes (created, intake set, archived, upload confirmed, started,
retry and cancel requested), `StudyRepository.transition` (`stage.entered`, by the engineer's
decision or the workflow, with the stage and revision it left), `WorkflowStateRepository`
(`gate.opened`, `stage.failed` with the code, `stage.failure_cleared`, `study.stalled`),
`ReviewRepository.record_decision` (`review.decided`, one `correction.recorded` per correction),
the activities (`run.started/ended`, `review.reminded`, `export.written`, `revision.carried`,
`corrections.refused` with the refusal's code, `inventory.rebuilt` when the room step replaces a
room's rows, and `entity.corrected/added/moved/merged` naming the row each applied correction
produced), the objects route (`entity.added/deleted` for manual instances), and the worker's
activity interceptor: one `activity.completed/failed/cancelled` row per attempt with
`started_at`, a monotonic `duration_ms`, the attempt and, on a failure, the code and the error.
The interceptor, not `run_once`, because `analyze_photo`'s provider errors and a bad input id
fail before `run_once` runs and must be on the timeline too.

Served by `GET /v1/studies/{id}/timeline` (keyset on `seq`, `event_type` filters),
`GET /v1/studies/{id}/timeline/steps` (per activity id: attempts, first start, last end, summed
duration, last outcome and code; per stage visit: entered, left, duration, gate opened,
decided, review wait) and the export's `summary.timeline`, which leaves out the export step's
own events so that exporting a revision twice stays byte-identical
(`test_same_revision_exports_byte_identical_output` caught the first version). Documented in
`docs/api/TIMELINE_AND_AUDIT.md`.

**The fake run** (`tests/lifecycle/test_timeline_audit.py`, the real workflow on the
time-skipping server with the offline providers, three photos, approved straight through): 34
events, 21 steps, 8 stage visits in the export. The set of step ids equals the set of activity
ids Temporal scheduled; every step's duration matches its own start and end within 2 s and fits
inside Temporal's scheduled-to-completed window; the stage durations sum to the run.

| step | duration | step | duration |
|---|---|---|---|
| `record_run_start` | 26 ms | `ingest_appraisal:r1` | 310 ms |
| `transition:extracting:r1` | 24 ms | `extract_fields:r1` | 225 ms |
| `list_photos` | 12 ms | `reconcile_rooms:r1:vote` | 11 ms |
| `analyze_photo:<a>:r1` | 281 ms | `reconcile_rooms:r1` | 5 ms |
| `analyze_photo:<b>:r1` | 300 ms | `process_assets:r1` | 49 ms |
| `analyze_photo:<c>:r1` | 299 ms | `transition:<each of 8 stages>` | 6 to 14 ms |
| `localize_photo:<a,b,c>:r1` (skipped: no localizer) | 18, 20, 67 ms | | |

| stage visit | duration | gate opened |
|---|---|---|
| extracting r1 | 606 ms | |
| appraisal_review r1 | 63 ms | yes |
| rooms_review r1 | 59 ms | yes |
| components_processing r1 | 20 ms | |
| components_review r1 | 32 ms | yes |
| assets_processing r1 | 65 ms | |
| assets_review r1 | 45 ms | yes |
| exporting r1 | open at export | |

(The offline providers answer in milliseconds; on a live run the same rows carry the minutes.
The review waits are the decision's distance from the gate opening, here the test's own.)

### 41.4 The AI-versus-engineer audit trail

Every revision-scoped row the API returns (`/fields`, `/rooms` with its photos and inventory,
`/assets`; the takeoff and cost share the asset's) and every row of the export carries
`provenance`: `model` (the pipeline wrote it and no engineer changed it; the rules table and the
cache count as the model's), `engineer_added` (a `room_component` correction: the component and
its asset) or `engineer_modified` (a correction applied when the row was copied into its
revision), with the correction, the review and its stage and revision, the time, the reviewer,
`before` (read from the copied row), `after`, `changed` and `carried_from`. The source of truth
is the append-only `corrections` table joined to each row's lineage (`components.raw.
carried_from`, the new `rooms.carried_from`, an extraction's `raw.carried_from` or the same
document's extraction at the previous revision, a photo by its document, an asset through its
component): a correction at revision r targets a row of r and is applied to its copy at r+1, so
walking a row's ancestors finds the correction that produced it, the nearest one winning; a row
carried unchanged afterwards keeps the engineer's origin. The events are the same link forward;
the resolver does not need them, so studies from before the table resolve too.
`GET /v1/studies/{id}/audit` lists every engineer action in time order: decisions, corrections
(`modified`, `added`, `moved`, `merged`, `reanalyzed`), manual objects added and deleted, intake
changes, each with before and after and the rows the correction produced.

**The test study** (the same file, four photos, the components gate, each decision recorded
through `ReviewRepository.record_decision` as `POST .../decisions` records it and then delivered
like the relay; one correction of each kind): an extraction field's value at the appraisal
review, the Kitchen renamed and a bathroom photo moved into it at the rooms review, a cabinet's
material and count and an added light fixture at the components review, the cabinet asset's
bucket at the assets review. Five revisions, 88 events, 39 steps, 16 stage visits (eight with a
gate and a decision, `[reject, approve] x 4`), 14 audit items:

| at | kind | action | row | stage | changed | produced |
|---|---|---|---|---|---|---|
| 20:08:23.803 | correction | modified | extraction_field r1 | appraisal_review | value_text | 1 |
| 20:08:23.803 | decision | rejected | review r1 | appraisal_review | | |
| 20:08:24.140 | decision | approved | review r2 | appraisal_review | | |
| 20:08:24.224 | correction | modified | room r2 | rooms_review | name | 1 |
| 20:08:24.224 | correction | moved | photo_room r2 | rooms_review | room_id | 1 |
| 20:08:24.224 | decision | rejected | review r2 | rooms_review | | |
| 20:08:24.398 | decision | approved | review r3 | rooms_review | | |
| 20:08:24.475 | correction | modified | component r3 | components_review | count, material | 1 |
| 20:08:24.475 | correction | added | room_component r3 | components_review | attachment, count, label, material | 1 |
| 20:08:24.475 | decision | rejected | review r3 | components_review | | |
| 20:08:24.701 | decision | approved | review r4 | components_review | | |
| 20:08:24.828 | correction | modified | asset r4 | assets_review | bucket, recovery_period | 1 |
| 20:08:24.828 | decision | rejected | review r4 | assets_review | | |
| 20:08:25.054 | decision | approved | review r5 | assets_review | | |

Every row of revision 5 resolves: the renamed room, the moved photo, the corrected cabinet
(`before {count: 6, material: wood}`, `after {count: 8, material: laminate}`), the corrected
field and the re-bucketed asset (`before bucket y5`) are `engineer_modified` with the stage and
revision of their correction; the light fixture and its asset are `engineer_added`; every other
room, photo, component, field and asset is `model`. The export carries the three origins.

### 41.5 Tests and gates

`tests/LEDGER.md`: `tests/unit/errors/test_catalogue.py` (26), `tests/unit/audit/test_timeline.py`
(3), `tests/api/test_timeline.py` (2: provenance, audit and timeline on a seeded two-revision
study with one correction of each kind including a merged room and a deleted manual object;
the `code` member on seven kinds of problem), `tests/integration/db/test_migrations.py` (0012:
append-only, checks, downgrade), `tests/lifecycle/test_timeline_audit.py` (2, above). Existing
tests adjusted for the additive members only (`failure_code`, the problem's `code`, 21 JSONB
columns, head 0012). Gates from `backEnd/`: `ruff check`, `ruff format --check`, `mypy`, the
full suite with Docker (`env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY uv run --no-sync pytest -q`),
all green; `docs/api/openapi.json` regenerated (three new operations, the new members).

### 41.6 Open

* Migration numbering: this branch's `study_events` migration was `0012` until the ledger's
  `0012_call_ledger` (PR #88) merged first; it is `0013_study_events`, revising 0012.
* No live run through the stack with the table yet (worker, Temporal, a study); the lifecycle
  runs are the evidence. The like-for-like live run of section 38 would be the first study whose
  timeline needs no worker log.
* Nothing in the frontend reads `/timeline`, `/audit`, `provenance`, `review_codes` or `code`
  yet; the review workbench is where the catalogue's "how to fix" should surface.
* `inventory.rebuilt` lists the ids of the rows the room step replaced, not their contents; a
  before-image would double the rows for little: the replaced rows were the model's.

## 42. Classification by context: the rules follow the guide, the firm's rulings follow the engineer (2026-10-29)

Design: `CLASSIFICATION_CONVENTIONS.md`. Branch `claude/classification-conventions`.
Private artifacts: `work/e2e_artifacts/reclassified_2026-10-29/` (the two rescored exports,
their change lists, `E2E_SCORE_run1.md`, `E2E_SCORE_run2.md`, `COMPARE_before_after.md`).

### 42.1 Why

Section 38 left 12 of 36 evidenced engineer rows in the wrong bucket after two live runs,
and the engineer's corrections at the gates did not move them. Reading the rules table
against the IRS Audit Techniques Guide (ATG) showed why: 26 rows carried a "contested" note
saying the guide puts the item with the building while the row kept a legacy 5-year
treatment (plumbing fixtures, light fixtures, ceiling fans, cemented tile, restroom
cabinetry, and more), and furniture took the office asset class (00.11, 7 years) by its name
instead of the activity it serves (57.0, 5 years, ATG p.297 and p.308). The model path had
these right (outlets, baseboards and wall finishes went to the building); the rule rows,
which fire before the model, did not.

The guide's test is not the item's name. It is "the manner of attachment to the land or the
structure and how permanently the property is designed to remain in place" (Rev. Rul.
75-178, ATG p.20) and whether the item "relates to the operation or maintenance of the
building" (p.19-20), applied item by item in the residential matrix (Exhibit A, pp.296-322)
by **function and attachment**: outlets of general applicability vs outlets dedicated to an
appliance (p.301, p.311); fixtures that provide substantially all the illumination vs accent
lighting (p.313); cemented tile and wood flooring vs strippable VCT and carpet (p.306-307);
a mirror attached with permanent adhesive vs a hanging one (p.317, p.191); residential
furnishings (57.0) vs rental-office furniture (00.11) (p.307-308).

### 42.2 What changed

**The rules table has a context dimension.** `lookup` takes the component's room type, and a
row may carry variants keyed on room, attachment and property class; the first matching
variant is the effective rule and the hit records which one fired (`context`). The decision
rule for every row: an item the guide names takes the guide's bucket; an item no document
names keeps its bucket and its contested note. 15 rows moved to the building row (toilet,
bathtub, shower, sink, water heater, garbage disposal, light fixture, ceiling fan, smoke
detector, thermostat, tile, hardwood floor, bathroom vanity, closet shelving, mirror);
furniture, chair, desk and filing cabinet moved to 57.0 with an office variant at 00.11;
kitchen cabinets and counters keep their row and take the building row in a bathroom; a
removable or freestanding mirror and a freestanding shelf are furnishings; a new
`accent_lighting` row (track, neon, decorative, landscape lighting) is 5-year. The contested
set went from 26 rows to 10 (kitchen cabinet, countertop, doorbell, laminate, hardwood
floor, deck, half wall, window guard, shed, concrete pad). Every row and variant is cited:
106 citation claims, 58 distinct excerpts, each verbatim on its PDF page and inside one
production chunk (the real-corpus integration test resolves all 106). `RULES_VERSION` is
`2026-10-29.1`, so no cached classification made under the old table is served.
`RULES_RECITATION.md` records the judgment for every changed row.

**The firm's rulings are a layer above the rules** (section 42.5).

### 42.3 Measured on the two live runs, without a paid run

`evals.reclassify_export` re-runs `lookup` with the context key over a stored export and
replaces the classification where a rule now hits (the router takes a rule before the model,
so this is what the pipeline does today); model-classified assets keep their stored answer;
quantities and dollars are untouched. The scorer of section 38 then reads the new export.

| metric | run 1 before | run 1 after | run 2 before | run 2 after | engineer |
|---|---|---|---|---|---|
| assets whose bucket moved | | 87 of 382 | | 86 of 310 | |
| bucket right by count (of evidenced rows) | 24 of 35 | **27 of 35** | 24 of 36 | **27 of 36** | |
| bucket right by dollars | 20 | 21 | 20 | 21 | |
| bucket share y5 / y7 / y15 / y39 (%) | 37.4 / 2.5 / 11.9 / 48.2 | 17.7 / 2.5 / 11.9 / 67.9 | 38.0 / 2.4 / 11.2 / 48.5 | **16.3 / 2.4 / 11.2 / 70.2** | 14.4 / 0.4 / 5.1 / 80.2 |
| bucket share error y5 / y39 (points) | 23.0 / 32.0 | 3.3 / 12.3 | 23.7 / 31.8 | **2.0 / 10.1** | |
| row-share distance (points) | 47.8 | 47.8 | 46.1 | 46.1 | |

What moved (run 2): light fixtures 13, sinks 9, hardwood floors 8, tile 6, toilets 6,
bathtubs 5, mirrors 5 (the two `removable` ones stayed 5-year through the variant), ceiling
fans 4, vanities 3, showers 2, closet shelving 1, a kitchen cabinet in a bathroom 1 (the
room variant), a smoke detector 1, all to the building; furniture 22 from 7-year to 5-year
(the one in the office stayed 7-year). The row-share distance does not move because rows
kept their dollars; only their buckets changed.

Rows fixed: Electrical, Plumbing, Ceramic Tile Flooring, Cabinets / Millwork - Restrooms,
FF&E (five). Rows that flipped the other way, as the design predicted: **Wood Flooring**
(the engineer's 5-year; the guide lists wood flooring as a permanent covering, p.306) and
**Specialty Plumbing - Kitchen & Utility Sink / Laundry** (the engineer's 5-year; the guide
puts kitchen sinks under building plumbing, p.311 and p.315). Those two, and the seven rows
still wrong from before (moldings, fireplaces, paneling, wall coverings, half walls, window
guards, exterior lighting), are the firm's conventions: defensible positions the guide does
not take. They are not rule changes; they are what section 42.5 carries forward once the
engineer approves them at a gate. The 39-year share is 70 against the engineer's 80; the
remaining 10 points are the concealed systems section 38 found (nothing produces them) and
these nine conventions.

### 42.4 What this does not change

The classifier prompt (`classify/v1`) is unchanged; a v2 that states the function and
attachment tests should be measured with the `assets` eval before it becomes the default.
`outdoor_lighting` stays a 15-year land improvement (the guide gives building-mounted,
accent and pole-mounted exterior lighting three different answers, p.313, and the vision
step does not tell them apart). Every number above is one property.

### 42.5 Live: two studies in sequence on the local stack (2026-10-30)

Stack rebuilt from the branch (`8661710`), migrations through 0014. Driver
`work/live_e2e/e2e_live3.py` (private). Study A ran with run 2's engineer corrections at
the appraisal, rooms and components gates, plus the engineer's 5-year rulings at the assets
gate for the nine convention rows (43 `asset` corrections, each with a reason naming the
row); approving that gate promoted the revision's rulings. Study B was the same property
with every gate approved as it opened (run 1's behaviour), at the same firm.

| | run 1 (pipeline alone) | run 2 (corrected gates) | **study A** (corrected + rulings) | **study B** (pipeline + conventions) | engineer |
|---|---|---|---|---|---|
| assets | 382 | 310 | 314 | 381 | |
| evidenced rows (of 38) | 35 | 36 | 35 | 35 | |
| bucket right by count | 24 of 35 | 24 of 36 | **35 of 35** | **34 of 35** | |
| wrong-bucket rows | 11 | 12 | 0 | 1 (Fireplaces) | |
| bucket shares y5 / y7 / y15 / y39 (%) | 37.4 / 2.5 / 11.9 / 48.2 | 38.0 / 2.4 / 11.2 / 48.5 | 31.3 / 2.4 / 11.1 / 55.1 | 24.5 / 2.5 / 11.9 / 61.1 | 14.4 / 0.4 / 5.1 / 80.2 |
| 39-year share error (points) | 32.0 | 31.8 | 25.1 | 19.2 | |
| classification: model calls / assets | 486 / 382 | 386 / 310 | 694 / 314 (two passes) | **157 / 381** | |
| assets classified from a convention | 0 | 0 | 0 | **151** | |
| classifier spend | $5.41 | $4.29 | $7.68 | **$1.76** | |
| study spend (ledger, incl. Azure DI and SAM 3) | $7.67 | $6.63 | $10.16 | **$4.01** | |
| workflow wall clock | 29.6 min (19 min on defect A) | 11.0 min | 9.5 min | 7.7 min | |

What the conventions layer did, verified in the database and the API:

- Study A's approval promoted **113 conventions** for the firm (113 `convention.promoted`
  events): the 43 manual rulings (13 of them against the rules table: hardwood floors,
  half walls, exterior lighting, the kitchen and garage sinks; the rest on labels with no
  rule row, such as baseboards and fireplaces) and 70 engineer-approved model answers with
  verified citations. Each row carries the reviewer, the study, the approval time and
  whether it agrees with the rules table.
- Study B served **151 of 381 assets from those conventions** (`method = cache`, 151 hits
  recorded), 12 of them rulings against the rules table and 36 of them uncited rulings, all
  with the provenance on the asset in `/assets` and in the export. The model was called 65
  times instead of 158 on study A's first pass; the classifier cost 23 % of run 1's for
  the same inputs, and the study 52 %. Study B's approval added an approval to every
  convention it let stand and promoted its own cited model answers (120 events; the firm
  now holds 233 approvals over its rows).
- Study B's classification, with no engineer in the loop, put 34 of 35 evidenced rows in
  the engineer's bucket, against 24 in run 1 with the same inputs.

Findings, in order of weight:

1. **The context key is too specific for rulings.** Study B's two fireplaces came out of
   the photo step with an unknown material where study A's had `wood` and `other`, so the
   keys differed and no convention matched: the one wrong row. Nine convention-row assets
   in study B had no ruling for the same reason (a room or material the first study never
   showed: a baseboard in the lobby, hardwood in the living room read without a material).
   The material and attachment the vision step reads vary between runs, so a ruling keyed
   on them does not generalize as far as the engineer meant it to. The fix is a key
   hierarchy: look for the exact key, then the same label and room without material and
   attachment, then the label alone, serving the most specific ruling the firm has; a
   ruling that was meant to depend on the material (carpet vs tile) still can, because the
   label carries it. Not built here.
2. **Rejecting the assets gate re-classifies every untouched asset at full price.** Study
   A's reject with 43 rulings cost a second classification pass: 316 model calls, $3.47,
   three minutes, for the 271 assets the engineer did not touch. Within one study nothing
   carries a revision's classifications forward. Carrying the untouched assets'
   classifications across an assets-gate reject (the way rooms are carried) removes most of
   that cost; the firm conventions do not help here because they are written only on
   approval.
3. **A ruling needs no citation, and the export says so.** 36 of the served conventions are
   uncited engineer rulings; each export record carries `citation: "firm convention,
   uncited"` and the reason the engineer gave. Whether an examiner accepts a firm's
   convention is the firm's risk; the system makes the position visible rather than
   dressing it as the guide's.
4. **The ledger now records the non-token paid steps** (Azure Document Intelligence,
   $0.20 per study; SAM 3, $0.04), so the study totals above are complete where section
   38's were not.
5. **The driver, not the product:** the first attempt at study A crashed at the appraisal
   gate on an import (the eval normalizer needs the backend environment); the study waited
   at the gate for 30 minutes and was resumed with a `--study` option, so the photo stage
   was not paid twice. The stale-decision 409 loop of run 2 is fixed in this driver.

### 42.6 What the research says about the method (2026-10-30)

The IRS guide (chapter 3, PDF pp.27-33) names six study methods and ranks the engineering
ones highest. Ours is the **detailed engineering cost estimate approach**: inventory from
inspection, quantity takeoffs, unit costs from published estimating guides with the volume
and page cited, reconciled to the purchase price less land. The self-service products in
this market (KBKG's Cost Seg Pro, Titan Echo's EchoLite) extrapolate a personal-property
share from a database of past studies, which the guide calls the sampling or modeling
approach and rates below the engineering approaches. Outside tax, insurance estimating
(Xactimate: a regionally priced line-item database; Verisk's and Tractable's photo-to-
line-item products) has the same shape as our photo step, room step and cost table; and
customs classification systems use the same three layers this change built: deterministic
rules first, a model for what the rules miss, an expert who confirms or overrides, and the
reviewer's outcome fed back so the next case is answered from it.

Two things the guide says that we do not yet do:

- **No pro-rata step-up.** "It is not an appropriate cost estimation methodology to apply a
  pro-rata step-up factor to the individual costs estimated for the various improvements"
  (p.32-33): when the estimated replacement cost does not tie to the purchase price less
  land, the gap is evidence that assets, quantities or factors are missing, and must be
  analysed, not scaled. Our basis step applies a uniform factor (2.4 on this property) and
  flags it as an error by design (section 37). The missing mass is known: the concealed
  systems (39.7 % of the engineer's dollars) and physical depreciation. Producing them is
  what makes the allocation acceptable, not the factor.
- **Sourced indices.** The year and location factors are placeholder tables inherited from
  the legacy code, not RSMeans' historical and city cost indexes or Marshall & Swift's
  equivalents. The mechanics (unit cost x quantity x year x location, every factor and its
  source on the asset) are the standard's; the numbers are not yet.

## 43. Every evaluation run leaves the study's record: ledger, payloads, log, manifest; a registry answers "was this tested?" from files (2026-10-29)

Sections 40 and 41 gave a study a complete ledger of its paid calls and a legible history. The
evaluation side had neither: the 187 run directories under `predictions/` (plus 83 sibling
`.log` files, the "272" of the brief, and 164 nested appraisal runs) kept only a hand-named
directory and a `run.json` summary (model, effort, prompt id, spend). The per-call records of
the `MemoryRecorder` every runner used were thrown away at exit; the terminal output was kept
only when someone redirected it; nothing said which commit, release, label file, price table
or settings a run exercised, so "has `photo_countables/v3` on gpt-6.1-sol been tested against
`photo_components_v1`?" meant grepping this report or running it again. This section makes
every eval and agent test leave the record a study leaves, labels runs by release and
component version, backfills what the old directories can still say, and adds the registry
that answers the question from files. No model was called for it (`--dry-run`, the fixture
providers and the existing run directories); no client figure appears here.

### 43.1 The standard

MLflow records a run's code as the `mlflow.source.git.commit` tag beside its params and
metrics; Weights & Biases writes `wandb-metadata.json` (git commit and remote, program, host,
arguments) and a `diff.patch` of uncommitted changes; Neptune the same two files. None
records a dirty flag as such (users log it), and none knows what a "component version" is: a
prompt's hash, a price table's date, an embedder's pinned revision are ours to name. The
ledger's columns already follow the OpenTelemetry GenAI conventions (section 40); the
conventions represent an evaluation result as a `gen_ai.evaluation.result` event
(`gen_ai.evaluation.name`, `score.value`, `score.label`, `explanation`, the `gen_ai.response.id`
it judges) and define no dataset attribute. What was taken: one manifest per run with the
commit, a dirty flag (tracked changes; the untracked count beside it), the nearest release
tag, the program and its arguments, the host; the component versions as Basis already
defines them (prompt `name/vN` + manifest sha256, `PRICING_VERSION`, `RULES_VERSION`, the
embedder model + `ModelSpec.revision`, the configured cost table's name, year and sha256);
the label files by name, `_vN` version and sha256; and the metrics as `evals.run` already
computes them, written next to the run they score.

### 43.2 Before and after

| | Before | After (`evals/record.py`, EVAL_REPORT 42) |
|---|---|---|
| per call | a `MemoryRecorder` row in memory, discarded at exit; the runner's own raw file (`photo_raw.jsonl`, `room_raw.jsonl`, `fields_raw.jsonl`) with the parsed answer | `llm_calls.jsonl`: one line per paid call with every `llm_calls` column (`basis.llm.recorder.ledger_columns`, the dict `LlmCallRepository.add_record` writes; a unit test holds the key set equal to the table's) plus `created_at`; `llm/<call id>.request.json` / `.response.json` built and redacted by the production archive's functions; metered steps too (SAM 3 predictions in `predict_boxes`, Molmo in `predict_points`, Azure DI in the appraisal runner) |
| terminal output | kept when redirected by hand (83 of 187 directories) | `console.log`: stdout, stderr and logging through one tee, API-key-shaped strings redacted, from `begin` to `finish` |
| what passed | the scorer's metrics, when a report was kept | `results.json` + `report.md`: every case `passed` / `failed` / `test_error`, the three counts never folded; expected-error cases with the code expected, the code observed and matched yes/no |
| where to find a run | a hand-named directory under `predictions/` | the same directory, linked from `verification/<component>/<version>/<run id>/`; one shape for every component |
| what ran, on what | `run.json`: model, effort, prompt id, spend; the directory name | `manifest.json`: harness module + its file sha256 + command + options; git sha, dirty, untracked, branch, release (nearest reachable tag), commits since it; Basis version, price table version, rules table version, embedder model + revision, cost table identity; every prompt `name/vN/sha256` with its call count (declared prompts that made no call included); models requested and answered, providers; the request parameters in effect; the model settings (no secret, no address); the inputs (file or directory, file count, sha256 over sorted names + hashes) and dataset files (name, `_vN` version, sha256); split, repeats; calls, errors, tokens of every kind, dollars; status (`ok`/`stopped`/`error`), exit code, error; the `run.json` summary |
| a run that failed or was stopped | the decorator's `run.json` with `complete: false`; the log, if any, cut off | the same, plus the manifest (`status: stopped` or `error`, the exception) and the log: `stops_on_account_error` finishes a pending record on every exit; the undecorated runners wrap their body |
| which labels a score refers to | the report (`eval.md`) when it was kept; label files editable in place (`room_inventory_v1.json` was, on 2026-10-16) | `scores.jsonl` appended by `evals.run --predictions DIR`: per dataset the label file, version and sha256, the scorer's git state and options, every metric; the latest scoring per dataset is what the registry reports |
| the database | `eval_runs(dataset, split, git_sha, model, prompt_version)` | plus `git_dirty`, `release`, `run_dir`, `label_version`, `label_sha256`, `manifest` (JSONB, the whole scores entry); migration `0015_eval_run_manifest`, nullable, no backfill: rows written before carry NULL because the information was not recorded |
| derived runs (merge, aggregate, fuse, combine, select, manual add) | `run.json` with the sources' paths and summed spend | the same, plus manifest and log naming each source directory (hashed) and the source's own manifest id |
| answering "was X tested on Z against W" | grep this report; re-run | `python -m evals.registry --component photo_countables/v3 --model gpt-6.1-sol --labels photo_components_v1` (Markdown or JSON); `--matrix` for prompt version x model with the label versions seen in each cell |

### 43.3 How it is wired

One helper, `evals.record.EvalRun`: `begin(out, harness=__name__, args=args, settings=settings)`
creates the directory, reads the git state of the harness's own checkout (not the process
cwd), snapshots the environment and settings, tees stdout, stderr and the root logger into
`console.log` through a redacting writer, and attaches itself to the argparse namespace. Its
`recorder` is a `FileLedgerRecorder`: the same `Recorder` protocol as `MemoryRecorder`, so
`CallContext` and every production call path (`call_model`, the classifier's residual loop,
`analyze_photo_detailed`, the room step, the extraction pipeline) record into it unchanged;
it prices each entry with `price_entry`, archives the two payloads with `payload_bytes` /
`priced_payload` (the production functions: same bytes, same redaction, images as ref + size +
sha256), writes the row as `ledger_columns(record) + created_at`, and keeps the records in
memory as before (the runners' `recorder.records` reads still work). `input()` / `dataset()` /
`prompt()` / `note()` declare what the run read; `finish(summary)` writes `run.json`
(the runner's dict, unchanged keys) and `manifest.json`, stops the tee. The manifest's calls
and components are computed from the ledger file as it stands, not from memory, because
`predict_marks --from-raw` and `predict_points --from-raw` redo a step over their own output
directory: the file then holds an earlier run's rows too, the manifest describes the file and
adds `calls.this_run` and `notes.previous_manifest`.

Wired into every runner that writes a run directory: `predict_photos`, `predict_rooms`,
`predict_fields`, `predict_assets` (which had no recorder at all: its classifier loop now
records through the production `ClassifyContext`, 4 rows for the test's 4 calls, prompt
`classify/v1` with its sha256), `predict_tiles`, `verify_boxes`, `predict_surface_crops`
(`predict` and `apply`), `predict_marks`, `predict_points`, `predict_multiview` (its own
per-room `calls.jsonl` stays), `replay_rooms`, `predict_boxes` (one `localization` row per
SAM 3 prediction, as `localize_photo` records them; the production function was replicated,
not imported, because its module imports temporalio and pins the production phrase set),
`predict_detector` (no paid call: manifest, log, inputs, detector + checkpoint + device),
`appraisal/run.py` (truth files as datasets, each PDF as an input, the recorded `git_sha` kept
in `run.json`), and the derive-a-run tools `merge_photo_runs`, `aggregate_runs`,
`fuse_counts`, `combine_rooms`, `sam3_select`, `manual_add`. `evals.run --predictions DIR`
appends `scores.jsonl` and, with `DATABASE_URL`, writes the manifest columns on `eval_runs`;
its report header names the release and the dirty flag.

Two choices a reviewer may ask about. `ledger_columns` lives in `basis.llm.recorder` and the
repository calls it (a lazy import: the recorder imports the repository), so the production
row and the evaluation line cannot drift apart; the test holds `set(keys) == table columns -
{created_at}`. The dirty flag counts tracked modifications only, with the untracked count
beside it: this checkout carries unrelated untracked notes, and W&B's `diff.patch` is likewise
`git diff` of tracked files.

### 43.4 The verification tree, the results, the expected errors, the console

Bryan's added requirements, folded into the design: "a verification folder ... the different
components ... the different versions and then the files underneath them"; results that show
"what passed, what failed, like what was a test error"; for a test that provokes a failure,
"what error we're trying to get, did it happen, yes or no"; and a console whose text is saved
with the run.

**The tree.** `<data dir>/verification/<component>/<version>/<run id>/` holds every run, the
same shape for every component, browsable with Finder: `manifest.json`, `results.json`,
`report.md`, `llm_calls.jsonl`, `llm/`, `console.log`, `run.json`, `scores.jsonl` and the
runner's prediction files. Components are the system's own names: a released prompt
(`photo_countables`, `photo_surfaces`, `room_inventory`, `surface_material`, `house_inventory`,
`classify`, `appraisal_fields_grounded`, ...), `detection`, `photo_analysis` (merged per-photo
runs), `photo_counts`, `appraisal_fields`, `provider_errors`, `scoring`; the version is the
prompt, rule or table version exercised (`v3`), a detector's name, SAM 3's phrase set
(`sam3-v3`), the release for `provider_errors`, else `unversioned`. The default is the prompt
with the most calls; a runner declares otherwise where that would be wrong (`predict_rooms`
files under `room_inventory`, not the `surface_material` second look that makes more calls).
The run directory stays where `--out` put it (`predictions/<run id>` by convention: EVAL_REPORT,
the repro scripts and the README all name those paths) and the tree holds a relative symbolic
link made at `finish()`; a run written inside the tree needs none. The 351 existing runs were
linked in by the backfill (43.6), not moved. `verification/README.md`, written once by the
helper, explains the layout to whoever opens the folder.

**Results.** `results.json` lists every case with an outcome of exactly one of `passed` (the
system under test did what the case asked), `failed` (it did not) or `test_error` (the harness
or an API failed: a rate limit after the retries, an outage, an account stop, a missing
recording, an exception; the case says nothing about the system). The three are never folded:
the summary carries all three counts, the counts per case kind, and the expected-error tally
(`matched`, `mismatched` when another code occurred, `not_observed` when none did), and
`report.md` renders the same with the summary line
`results: N passed, M failed, K test errors (T cases)` and one row per case. Each runner
records one case per unit of work (photo x repeat, room x repeat, document, asset row, crop,
image); `evals.run --predictions` adds one `score` case per labelled item (passed by the
metric's per-item criterion, failed otherwise, test_error when the run itself recorded a test
error for that item) through `record.add_cases`, which merges by id and rewrites results,
report and the manifest's summary. For a prediction run, `passed` from the runner means "a
usable answer was produced"; correctness is the scorer's case.

**Expected errors.** `python -m evals.provoke_errors` provokes, at no cost, the provider
failures the product maps to error codes (section 41, `docs/ERROR_CODES.md`): a key the
provider did not issue (`BASIS-PROVIDER-001`), a model that does not exist and a parameter the
model rejects (`BASIS-PROVIDER-002`), a request that cannot finish in time
(`BASIS-PROVIDER-007`), a budget that cannot cover one call (`BASIS-BUDGET-001`), on OpenAI and
Anthropic. Each scenario is a case of kind `expected_error` with the expected code, the code
that occurred (`record.error_code`: the exception's class along its MRO, or a `[BASIS-...]` tag
in the message) and `matched` yes or no: `passed` when they match, `failed` when another code
or no error occurred, `test_error` when the provider is not configured or the harness raised.
`--dry-run` runs the scenarios against scripted providers that raise what each expects, plus
one deliberate mismatch and one unconfigured provider, so the record is exercised without a
network: 7 passed, 1 failed, 2 test errors, 7 matched, 1 mismatched, 1 not observed, filed
under `provider_errors/v0.10.0/`. The live run needs the keys and was not made here (no call
from this branch); an account without credit cannot be provoked on demand and is covered by
the unit tests of `evals.account_stop` only.

**Console.** Every runner's progress and results go to the terminal and to `console.log`
through one tee (stdout, stderr and the root logger; API-key-shaped strings redacted), so the
two never disagree; the results summary line is printed the same way. The log ends when the
runner finishes its record, so the final `wrote ...` line of a runner prints just before.

### 43.5 The registry

`python -m evals.registry` scans `predictions/*` and `appraisal/runs/**` (the appraisal sweeps
nest their cells), reads `manifest.json`, `run.json`, `scores.jsonl`, `eval.md` and
`score.json`, and filters by component (a prompt name or `name/vN`, `detector:x`,
`embedder:<model>`), model (requested or answered, `claude-*` for a prefix), label file stem,
dataset, release, harness, status and start date; Markdown to stdout, `--json` and
`--markdown` to files. A scoring report's baseline systems (`printed_text`, `rules_table`,
`filename`, ...) describe the baseline, not the run, so only datasets scored with system
`predictions` count as a run's scores and labels. On the backfilled directories (43.5) the
motivating question has an answer, and it is "no":

```
$ python -m evals.registry --component photo_countables/v3 --model gpt-6.1-sol --labels photo_components_v1
0 runs
$ python -m evals.registry --component photo_countables --matrix
| component             | claude-fable-5-1 | claude-opus-5-5 | gpt-4.1 | gpt-6.1-sol |
| `photo_countables/v1` | -                | -               | 1 (unscored) | -       |
| `photo_countables/v2` | 1 (photo_components_v1,photo_rooms_v1) | 4 (photo_components_v1,photo_rooms_v1) | 1 (photo_components_v1,photo_rooms_v1) | 3 (unscored) |
| `photo_countables/v3` | -                | 1 (unscored)    | -       | -           |
```

`photo_countables/v3` was run once, on claude-opus-5-5 (`vision_countables_v3_opus55`,
section 29), and no `eval.md` was kept for it; the three gpt-6.1-sol runs of `v2` were scored
through merged directories (`vision_decomposed_v2*`), which the matrix attributes to the merge
tool, not to the prompt (a merged run's `run.json` names no prompt). Both are facts about the
record, now visible; the next v3 run on sol will show in the cell with its label version.

### 43.6 Backfill of the existing runs

`python -m evals.backfill_manifests --write --note docs/review_2026-10-15/EVAL_RUN_BACKFILL.md`
wrote a manifest with `backfilled: true` into the 351 run directories that had none (187 under
`predictions/`, 164 under `appraisal/runs/`), each field with its source (`sources`) and
`unknown` where the files do not say, and linked every one into the verification tree (351
links, 351 distinct targets, none broken; a nested appraisal cell is linked as
`<sweep>__<cell>` because the sweeps reuse cell names, and a run of a name already filed in a
slot takes a short hash suffix rather than replacing it). Nothing was moved or deleted, a
runner-written manifest is never touched, and a second pass keeps every one (`--force`
rewrites backfills only). The tree files the old runs by the prompt their calls used
(`prompt_versions` before the declared fallback `prompt_version`), a merge by its sources'
prompts (`photo_analysis/photo_countables-v2+photo_surfaces-v1`), an appraisal run that made
no model call under `appraisal_fields/<system>` (`mismo_xml`, `azure_1004`), the classifier
runs under `classify/v1` (the one released version); 10 `room_inventory` runs and 7 merged
runs whose `run.json` names no prompt sit under `unversioned`, and the one empty directory
under `unknown`. Recovered: the harness for 350 of 351 (from each runner's `run.json` key set, else
from the files present; the one unknown is an empty directory left by a killed run), prompts
for 263 with an exact sha256 (released prompt versions are immutable, so the manifest's hash
is the hash at run time), models for 269, effort and output budget where `run.json` had them,
spend and call counts, directory birth and modification times, the sibling log for 125, the
EVAL_REPORT sections citing 112 by name or by a prefix that encodes the section
(`cost28_`, `rooms16_`, `arch26_`, `s29_`), and for the 164 appraisal runs the commit the
runner recorded from the start and its release (`v0.10.0`). Not recoverable, and marked so:
the commit of every non-appraisal run (made from feature branches; the manifest gives the
`main` commit at the directory's last write as a lower bound and the release tags before and
after), the dirty flag of every run, the label files' content at scoring time (names and
versions from `eval.md` for 40 runs; `room_inventory_v1.json` was edited on 2026-10-16, a
`.bak` exists), the per-call records and payloads, the price and rules table versions, and the
terminal output of 226 runs. The note lists the counts, the harness per run and the runs
without a `run.json`; the registry reads a backfilled manifest like any other and shows
`backfilled` in its record column.

### 43.7 Tests and gates

`tests/LEDGER.md`: `ledger_columns` key set equals the table's, every column carries the entry
and the pricing, a metered step fills quantity and unit (3); `evals.record`: the ledger line
has every column and the payloads exist under the referenced paths with matching sha256 and
no image bytes, API keys redacted in ledger, payloads and log, metered steps, the tee and its
restoration, `git_state` on a tiny tagged repository (release, dirty, untracked, describe,
`GIT_SHA` fallback, harness checkout not cwd), the manifest's every section without secrets,
`sha256_tree` order-independence, `label_version`, the decorator finishing a pending run as
stopped / error / ok, a leaked log released, `score_entry` and `append_scores`, a rerun into
the same directory (20); `evals.registry` on synthetic directories: scan order and nesting,
manifest and `run.json`-only runs, `eval.md` with baselines left out, stopped and appraisal
runs, every filter, Markdown and JSON, the matrix, the CLI (10); `evals.backfill_manifests`:
harness from keys then files, report mentions by section, a full backfill with sources and
unknowns, an unknown sha, idempotence and `--force`, the note, the CLI (8); every runner's
end-to-end test asserts its manifest (harness, call count, prompts with sha256, inputs), one
ledger line per call and the log; `evals.run`: `scores.jsonl` from a dry run with the label
sha256 or None, the manifest columns through a fake session and through Postgres, the report
header (3); migration `0015` round trip and downgrade (1). Client data never enters the repo:
every fixture is synthetic, and the client-data guard is unchanged.

Gates (`backEnd/`): `ruff check`, `ruff format --check`, `mypy` (289 files) and
`env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY pytest -q` (the full suite, Docker up), plus
`pre-commit run --all-files` from the repository root. On 2026-10-29, after the additions of
43.4: ruff, format and mypy clean (290 files); the full suite 5,101 passed, 23 skipped (the
private-data and emulator tests), 39 subtests, 13 min, with one failure in
`tests/failure/test_worker_kill.py` (a Temporal worker-kill timing test, `3 == 2` attempts,
unrelated to this change) that passes alone (3 passed); the first pass before 43.4 was 5,067
passed with no failure. Every pre-commit hook passes on this branch's files; `detect-secrets`
also reports 17 lines that are not in `.secrets.baseline` and are the same on `main` (test
fixtures, the embedder's pinned revision, the vendored pdf.js), none touched here: the
baseline is stale and is left for its own change. The placeholder keys the redaction tests
and the expected-error runner use carry `pragma: allowlist secret`.

Added for 43.4: `evals.record`: `error_code` along the exception's MRO and from a tag, cases
summarised with three distinct outcomes and rendered with expected / observed / matched, the
summary line through the tee, `add_cases` merging by id, `tree_location` (declared, busiest
prompt, harness), the tree link (relative, replaced on a rerun, none inside the tree or
without a data directory, two runs of one name never share a slot), the tree README (8 more
tests); `evals.provoke_errors` dry run: every scripted scenario matched, the deliberate
mismatch `failed`, the unconfigured provider `test_error`, filed under `provider_errors/<release>`,
console and `console.log` agree and hold no key, `--only`, a harness exception leaves a
manifest with status `error`, every live scenario names a catalogued code (4); the registry
reads tree component, version and results and scans runs written inside the tree once (1
more); the backfill files runs by called prompt, merge sources, no-call appraisal systems and
links them with unique names (extended); every runner's test asserts its cases (counts,
ids, kinds, the BASIS code of a provoked test error) and its tree slot, with symlink tests
for the declaring runners; `evals.run`: a `score` case per labelled item on a dry run with
derived counts, the run's own `test_error` carried to the item, per-dataset case counts in
`scores.jsonl`, `component` / `component_version` through a fake session and Postgres, the
`0015` migration round trip.

### 43.8 Open

* The log ends at `finish()`: a runner's last `wrote ...` line now prints just before the
  record is finished, so the files land a moment after the message; a line printed by
  `main()` after the run function returns (the derive-a-run tools, the appraisal runner) is not
  in `console.log`. The manifest's `status` and `exit_code` say how the run ended.
* The appraisal replay re-records a priced `document_analysis` row from the recording
  (pre-existing ledger semantics), so a replay's manifest shows a non-zero `calls.cost_usd`
  while `run.json` keeps `azure_di_cost_usd: 0.0`; and its CLI logs a warning twice (the
  `basicConfig` handler and the record's).
* Backfilled runs carry what the files say and no more; the exact commits of the pre-appraisal
  runs are gone. A run made from now on records its own.
* `eval_runs` rows are written only by `evals.run`; the predict runners write files alone. A
  registry over the database (the same question, SQL) would need the predict runners to
  write `eval_runs` rows too, or a loader from `manifest.json`; the file registry is enough
  for one data directory.
* The registry reads one data directory. The private bucket mirror (`gs://basis-eval-private`)
  is not scanned; `gsutil rsync` of the run directories keeps it the same set of files.

## 44. The structure model: a complete replacement cost that reconciles to the price (2026-10-30)

Question (section 42.6; WORKFLOW_REVIEW 2026-10-29 section 6, step 5): the basis step scaled
the photo-visible schedule to the depreciable basis by one factor (2.39 on run 3A, 2.50 on
run 3B), which the IRS guide calls "not an appropriate cost estimation methodology" (ATG ch. 3
D(3)), and the eight concealed systems (39.7 % of the engineer's dollars) were "not produced".
What does the guide ask for, which of the candidate ways of producing the building's
replacement cost new, splitting it into systems, carving the itemized assets out and
depreciating it scores closest to the engineer's report, and what does the chosen one do to
the 39-year share and the reconciliation? Research and design: `STRUCTURE_MODEL.md`. Code:
`basis/domain/structure.py` (pure), the concealed rows in `process_assets`, the result in
`basis/evidence/result.py`, thirteen rules rows (`RULES_VERSION` 2026-10-30.1), the scorer,
the offline replay `evals/structure_replay.py` and the sweep `evals/structure_sweep.py`. No
paid run: both live exports of section 42.5 were replayed through every variant offline. No
client dollar amount appears here; the shares and ratios are from `evals.e2e_report` on the
replayed exports (private, `work/e2e_artifacts/structure_2026-10-30/`).

### 44.1 What the standard says, in one paragraph

ATG ch. 3 A(6): for an acquisition the costs are estimated "using the construction cost data,
methods, and techniques normally employed for a property appraisal", as replacement cost new
including indirect costs, adjusted for age and condition, and "the total replacement cost new
less depreciation (RCNLD) of the acquired items of property, along with the fair market value
(FMV) of the land, should reasonably approximate the total purchase price". D(3)-(6): a
pro-rata step-up "merely to reconcile to the total purchase price" is not acceptable; a gap
"is indicative of inaccuracies" and its source must be found among five causes (land FMV,
assets or quantities missed, cost estimates and factors, depreciation factors, indirect
costs). Ch. 4 C.11: indirect costs pro rata. C.2(2) names "R. S. Means or Marshall Valuation
Service" as the reliable published sources. The appraisal's cost approach is a replacement
cost from a cost service with the appraiser's own depreciation (age-life: on the client
appraisal the printed physical depreciation over the printed cost new equals effective age
over effective age plus remaining economic life, 15 / 70). RSMeans Residential Costs 2020 (in
the corpus) prices a dwelling per square foot of living area by class, type and size (the
facing page's matrix), lists the ten components of every model with their dollars and
percentage, and states the method's accuracy: "a variation of +/- 10% should be expected"
(printed p.15). The guide's residential matrix names every concealed system: foundations
(p.299), exterior walls and framing (p.321), roof (p.317), interior partitions (p.321), paint
(p.320), plumbing (p.315), HVAC (p.310), electrical (pp.301, 303), fire alarm (p.305), site
utilities (p.319) as building components; data cabling (p.300) as 57.0 five-year; door locks
and keyless entry under Doors and Security Systems - Building (pp.300, 318), where the
engineer's report has Access Control as 5-year.

### 44.2 What was built

1. **Building RCN**, three ways: the appraisal's printed total cost new (cross-checked against
   the sum of its parts; on the client appraisal the parts exceed the total by 1.4 %, a
   warning); the sum of those parts; the RSMeans square-foot model for the class (UAD
   quality Q1-Q6, else the cost service's words) and type (stories), sized by the facing
   page's matrix (the wood-siding row of all 18 models transcribed, interpolated between
   columns) with the basement and garage additions. Each brought to the cost year by the same
   year factors as every RSMeans row.
2. **Split** into the model's nine components, two ways: the model page's dollars per square
   foot (all 18 models transcribed from the page images; each foots to its bottom line,
   pinned by a test), the Overhead component spread pro rata (ATG C.11); or the NAHB 2024
   stage shares (Cost of Constructing a Home, Eye on Housing 2025-01: foundations 10.5,
   framing 16.6, exterior finishes 13.4, major systems 19.2, interior finishes 24.1 of the
   construction cost; site work and other spread pro rata, final steps outside the building;
   the model's proportions within a stage). Marshall & Swift's segregated-cost percentages are
   the third candidate: the handbook is licensed and not in the corpus, so the variant is
   reported as not sourced, never defaulted.
3. **Every label mapped** to one of the twelve building systems and to the model component
   it belongs to, or to none (appliances, fireplaces, furnishings, treatments, site): the
   `by_system` page is always built.
4. **Carve-out**, on or off: each component's top-down figure less the itemized assets mapped
   to it (the hybrid); or the top-down figures left whole (the itemized rows then counted
   twice). The residual is written as a concealed asset with its label, quantity (the living
   area), formula, inputs and assumptions; a residual below zero clamps and is reported. The
   Interiors residual splits into partitions and painting by the RSMeans drywall wall
   assembly (1.47 against 0.75 per SF of wall, printed p.214); Mechanical into plumbing and
   HVAC by the three-fixture bathroom and the gas heating system (printed pp.250, 268).
   Specialties (cabinets, sink, water heater) has no concealed content, so its residual is
   reported as an unassigned remainder, never written (ATG C.4(3)).
5. **Four small systems** with code-derived quantities priced from the cost table's row for
   the label: fire alarm (IRC R314.3: bedrooms + stories + basement, less detectors the
   photographs found), data cabling (TIA-570: one outlet per room), access control (one
   keyed lockset and deadbolt per exterior door), site utilities (a 50 LF lateral per public
   service, an assumption sent to review). The eval cost table gained four rows cited to the
   RSMeans pages (STRUCTURE_MODEL.md 3.6).
6. **Depreciation**, three ways: the appraiser's physical depreciation over the cost new;
   the age-life ratio of the effective age and remaining economic life; none (a baseline).
   One rate on every row, recorded per row (`rcnld`), with the `depreciation_uniform`
   warning (the guide expects it to differ by class).
7. **Reconciliation**: RCNLD against the purchase price less land; within 10 % the basis is
   allocated pro rata on RCNLD (the factor is then near 1), outside it the allocation is
   withheld and the result carries the gap, the by-component picture (top-down, itemized,
   concealed, clamped), the unassigned remainders, the dollars outside the model, the
   unpriced count, the appraisal's own reconciliation, and the guide's five causes each with
   what the data says. The old uniform factor is reported as `implied_uniform_factor` only.

The concealed rows are ordinary assets (no component; `takeoffs.inputs.concealed`), classified
by the rules table through the same router as a component, so a firm convention can override
them (Access Control is the case in point: the row follows the guide, building, contested; the
engineer's 5-year ruling becomes the firm's convention after one review), and an engineer's
correction of one at the assets gate is matched by its label in the next revision (the
classification is the engineer's, the quantity and cost are recomputed). Every assumption is
on the asset (`assumptions`, `source`, the split and whether the row was carved).
`STRUCTURE_STEP=off` restores the previous behaviour; `STRUCTURE_RCN_SOURCE` selects the RCN
source.

### 44.3 The sweep: every variant, scored the same way on both live exports

`python -m evals.structure_sweep <export A> <export B> --cost-table <table>` runs the 54
variants (3 RCN sources x 3 splits x carve on/off x 3 depreciations) through
`evals.structure_replay` and `evals.e2e_report` on each export and ranks the ones that are
sourced and reconcile on both by the mean absolute bucket-share error against the engineer's
shares (14.4 / 0.4 / 5.1 / 80.2 %), then the row-share distance, then the gap. Every variant is
deterministic: no model call, cost $0, 7-11 ms each. The 18 Marshall & Swift variants are
reported as not sourced. The photo-visible rows' scores (35 of 35 and 34 of 35 right, section
42.5) are unchanged under every variant (the regression check). The 36 sourced variants on
each export, abridged (the full table is the sweep's `sweep.md`):

| Variant (RCN + split + carve + depreciation) | Run 3A: status, gap % of B | 39-year share | bucket error, mean / max (points) | TVD | Run 3B: status, gap | 39-year share | bucket error, mean / max | TVD |
|---|---|---|---|---|---|---|---|---|
| square_foot + rsmeans + carve + physical **(pick)** | reconciled, +8.0 | 83.9 % | 2.06 / 3.6 | 21.1 | reconciled, +7.2 | 86.7 % | 3.50 / 6.5 | 21.8 |
| square_foot + rsmeans + carve + age-life | identical to the row above: the two rates coincide (21.4 %) on this appraisal | | | | | | | |
| square_foot + nahb + carve + physical | reconciled, +6.0 | 84.2 % | 2.23 / 4.0 | 24.8 | reconciled, +5.2 | 87.0 % | 3.64 / 6.8 | 24.7 |
| appraisal_total + rsmeans + carve + physical **(runner-up)** | reconciled, +3.4 | 84.6 % | 2.43 / 4.4 | 21.1 | reconciled, +2.6 | 87.4 % | 3.79 / 7.1 | 22.2 |
| appraisal_parts + rsmeans + carve + physical | reconciled, +2.3 | 84.8 % | 2.52 / 4.6 | 21.1 | reconciled, +1.5 | 87.5 % | 3.86 / 7.3 | 22.3 |
| appraisal_total + nahb + carve + physical | reconciled, +1.3 | 85.0 % | 2.59 / 4.7 | 24.6 | reconciled, +0.5 | 87.7 % | 3.92 / 7.4 | 24.6 |
| appraisal_total + rsmeans + top-down + physical | unreconciled, -19.8 | 87.6 % | 3.84 / 7.4 | 23.2 | unreconciled, -18.5 | 89.6 % | 4.84 / 9.4 | 24.6 |
| square_foot + rsmeans + top-down + physical | unreconciled, -15.2 | 87.1 % | 3.60 / 6.9 | 22.8 | unreconciled, -13.9 | 89.2 % | 4.65 / 9.0 | 24.3 |
| any + any + any + none | not checked (no rate: RCN against the basis) | as its depreciated twin | | | not checked | | | |
| any + marshall_swift + any + any | not sourced | | | | not sourced | | | |
| before (section 42.5: no structure rows, uniform step-up) | step-up 2.39 (error) | 55.1 % | 12.5 / 25.1 | 47.8 | step-up 2.50 (error) | 61.1 % | 9.6 / 19.2 | 48.9 |

Kind C rows: every reconciled variant produces all eight, priced, with seven in the
engineer's bucket (Access Control: engineer 5-year, rules building, contested). The ranking
over both exports: square_foot + rsmeans + carve (mean bucket error 2.78 points, TVD 21.4,
gap 7.6 %) ahead of square_foot + nahb (2.94, 24.7, 5.6), appraisal_total + rsmeans (3.11,
21.6, 3.0), appraisal_parts + rsmeans (3.19), appraisal_total + nahb (3.25) and
appraisal_parts + nahb (3.33). The depreciation axis cannot be separated on this property:
the appraiser's physical depreciation and the age-life ratio are the same rate, so each pair
ties; `none` never reconciles (RCN undepreciated is a quarter above the basis). The
top-down-only variants never reconcile (the itemized rows counted twice put RCNLD 14-20 %
over the basis), which is the carve-out's justification in numbers.

**Pick:** the RSMeans square-foot model sized by the matrix, the RSMeans split, the
carve-out, the appraiser's physical depreciation (with age-life as its check): the smallest
bucket-share error on both exports, and the one that rests on the published source the
guide names (C.2(2)), with the volume, page and matrix cell on the asset. **Runner-up:** the
appraisal's printed total with the same split, carve and depreciation: 0.3 points worse on
the shares, 2.5 times tighter on the reconciliation (3 % against 8 % of B, both inside the
10 % the square-foot method claims for itself); it is one setting away
(`STRUCTURE_RCN_SOURCE=appraisal_total`) and the appraiser's figures remain on the result as
the independent check (`appraisal_reconciles`). One property decides this ranking; the gap
between the two is smaller than what a second property could move, and the sweep reruns on
any export in seconds.

### 44.4 Reading

1. **The 39-year share is now the engineer's, within 4 to 7 points, from the other side.**
   The missing mass was the concealed systems, as section 34 predicted; adding them from a
   sourced building RCN moves the building share from 55-61 % to 84-87 %, and the remaining
   difference is the 5-year side being light (11.3 and 8.4 % against 14.4 %): the engineer's
   5-year rows the pipeline under-prices or does not price (cabinets and millwork, moldings,
   FF&E, window treatments; the Specialties remainder the model reports is about 4.7 % of the
   building RCN and is exactly the cabinet, sink and water-heater content the itemized rows
   leave unpriced), not the structure.
2. **The allocation is one the guide accepts.** RCNLD reconciles to the price less land
   within the square-foot method's own accuracy under every carved, sourced, depreciated
   variant; the basis is apportioned pro rata on RCNLD (factor 1.01-1.09, not 2.4), and the
   diagnosis is on the result for the gap that remains.
3. **The axes that matter are the carve-out and depreciation; the RCN source and the split
   move the shares by tenths of a point.** Not carving, or not depreciating, fails the
   reconciliation outright; between the two sourced RCNs and the two sourced splits the
   bucket shares differ by under half a point, so the choice is made on the guide's
   preference for a cited published source and reported with its runner-up.
4. **Seven of the eight concealed rows take the engineer's bucket.** Access Control is the
   one disagreement, by design: the guide names door locks and keyless entry on the building
   side; the row is contested and goes to the engineer, whose ruling the conventions layer
   carries forward.
5. **What this does not settle.** One rate of depreciation for every row (A(6) asks for
   differences by class; the data to do it is not in the appraisal, and this property cannot
   even separate the two rates); the Interiors and Mechanical splits are national assemblies
   rather than the house's wall area and bathroom count; the square-foot model's basement
   and garage additions are at the model's representative size and its exterior wall is wood
   siding; the small systems' quantities rest on code rules and two assumptions (lateral
   length, door count without a photograph); the Marshall & Swift split needs the licensed
   handbook. STRUCTURE_MODEL.md section 6.

### 44.5 Tests and gates

`tests/unit/domain/test_structure.py` (the 18 models foot and their matrix cells meet the
page, class and type, the three RCN sources, the matrix interpolation and clamps, the two
sourced splits and the unsourced one, every label mapped, the carve-out, its clamp and the
top-down-only variant, the small systems' quantities, the three depreciation methods, the
three reconciliation verdicts); `tests/unit/domain/test_basis.py` (the depreciated
allocation); `tests/unit/evidence/test_result.py` (the structure path: reconciled, withheld,
missing inputs, the default source); `tests/unit/domain/test_rules_table.py` (the thirteen
rows, the contested and building sets, the version); `tests/unit/evals/test_e2e_report.py`
(structure labels, the `produced` outcome); `tests/unit/activities/test_process_assets.py`
(the concealed rows written, a correction of one applied by label);
`tests/lifecycle/test_intake_takeoff.py` (the export carries the thirteen rows, the
depreciation and a reconciled allocation on the synthetic appraisal); the API, export,
lifecycle and failure counts updated (the components' assets plus thirteen concealed).
`tests/LEDGER.md` has each.

## 45. Cost indices: every priced dollar rests on a cited RSMeans index (2026-10-30)

Question (section 44.4's open item; the brief of 2026-10-30): every asset's dollars are the
RSMeans Residential 2020 unit cost times the quantity times a year factor and a location
factor, and the two factors were hand-typed tables from the legacy code with no source
(2024 = 1.15, CA = 1.15: 32 % of every total that a reviewer could not trace). What does
RSMeans publish for time and location, how is it applied, what rule covers a cost year the
books have no index for, and what does the change do to the two live studies? Research and
the rules: `COST_INDICES.md`. Code: `basis/domain/cost_indices.py`, the transcribed tables
`basis/data/cost_indices/rsmeans_2020.json` (sha256 in `manifest.json`), `costs.py`,
`measurements.py`, `structure.py`, `process_assets`, the offline replay
`evals/reprice_export.py`. No paid run: both live exports of section 42.5 were repriced and
replayed offline. No client dollar amount appears here; ratios and shares only (artifacts:
`work/e2e_artifacts/cost_indices_2026-10-30/`, private).

### 45.1 What the standard says, in one paragraph

The 2020 books' costs are U.S. national averages as of January 1, 2020 (Residential p.11; the
Historical Cost Index's "Jan 2020 = 239.1, est." line, Building p.778). Time: the Historical
Cost Index (Building p.778; Jan. 1, 1993 = 100; one value per year, July 1, 1970-2019) moves a
cost by `Index A / Index B`, so a 2020 cost's year factor is the cost year's index over 239.1.
Location: the Residential book's Location Factors (pp.718-722), one factor per city by the
first three digits of the ZIP ("simply multiply the base cost by the factor for that city";
"for a city not listed, use the factor for a nearby city with similar economic
characteristics"), which the book says is the City Cost Index's weighted-average row over 100
(p.287) and the table to use when "the project covers multiple trades". Marshall & Swift
publishes the same two multipliers monthly (current cost multiplier by district, local
multiplier by ZIP) rather than a historical table, and never asks the user to extrapolate.
The IRS guide asks that cost sources be "clearly referenced, including identification of the
specific volume, page, and item number" (ATG ch. 3 C.2(2)) and says nothing about a missing
year.

### 45.2 What was built

* **The tables, transcribed and versioned.** The PDFs' text layer dropped every numeric
  column, so both tables were transcribed from the page images: a two-pass OCR (names as
  text, the factor column upscaled with a digits-only whitelist, joined by line) and then
  every row compared with the image in 36 zoomed strips. 651 U.S. city rows (51 states and
  D.C., 908 ZIP prefixes, each naming one city), 51 index years (1970-2020). The OCR draft was
  right on about 85 % of rows; 68 unreadable values and nine plausible-but-wrong ones
  (Davenport .39 for .99, Butte .37 for .97, Norristown .30 for .90, ...) were corrected from
  the image and nine dropped rows added. Internal checks: the per-state row counts equal the
  pages', and the index's two printed columns (1993 base, 2020 base) agree with each other
  for all 51 years. Twenty location rows and thirteen index values read from the images are
  pinned in the tests. The file is public reference data (published index numbers and place
  names; the tests check no five-digit number or extra field is in it) and immutable once
  released (the loader refuses a file whose sha256 is not the manifest's).
* **The lookups.** Year factor = HCI(cost year) / 239.1 to four decimals (2019 0.9711, 2018
  0.9322, 2010 0.7675, 1970 0.1200); before 1970 `UnknownCostYear`. Location: the city row by
  ZIP prefix (the intake's ZIP, else the appraisal's `subject.zip`), else the mean of the
  state's listed cities to three decimals (CA 1.001 over 37 cities, NY 0.976 over 32, IL 0.959
  over 19), recorded as such. The legacy state table was not residential RSMeans: NY 1.20
  against 0.976, HI 1.30 against 1.165, MS 0.80 against 0.942, CA 1.15 against 1.001.
* **The rule for a year after the last published index** (COST_INDICES.md section 4): the
  latest index applies (factor 1.0000 on the 2020 books), the assumption is written on every
  priced row (`takeoff_inputs.cost_year_assumption`, the `cost_source` text, the export's
  `summary.takeoff`) and the asset is flagged `cost:YEAR_INDEX_EXTRAPOLATED` (BASIS-CHECK-016),
  on itemized rows, the square-foot building rows and the small systems alike, until the firm
  loads the year's published index through `COST_INDEX_SUPPLEMENT_PATH` (a JSON of later
  Historical Cost Index values, each with its own source), which lifts the assumption and the
  flag. Trending by a rate was rejected: no book prints the number and neither service
  extrapolates. Refusing the year was rejected: every current study would price nothing.
* **Every cost row cites both factors**: `(base 2020, x0.9322 for 2018 = HCI 222.9 (July 2018)
  / 239.1 (Jan 2020, est.), RSMeans Building Construction Costs 2020 p.778, from placed in
  service 2018-05-01 (intake); x0.99 location from zip 90001 (intake): 900-902 Los Angeles
  0.99, RSMeans Residential 2020 Location Factors - Residential p.718)`. The structure model's
  square-foot RCN takes the same year factor (with a `cost_year_index` warning when
  extrapolated) and the appraisal's cost new is trended from its cost-data date by the same
  index ratio when both years have one.
* Settings: `COST_YEAR` 1970-2100 (was 2020-2026), `COST_INDEX_SUPPLEMENT_PATH`. The function
  signatures the callers use are unchanged; `estimate_cost` gained an optional `year` (the
  study's resolved factor) and `resolve_cost_index` gained `zip_code` and `supplement`.

### 45.3 The two live exports, repriced offline

`evals.reprice_export` prices every itemized asset of a stored export again from its cost
table row with the new factors (checking that the old unit cost over the old factors is the
row's base: 245 of 245 and 288 of 288 rows agree to the cent, no row unpriced), drops the
concealed rows, and `evals.structure_replay` then rebuilds them on the same factors;
`evals.e2e_report` scores the result as in section 44. Both studies: cost year 2024 (placed in
service 2024-12-03), California, no ZIP on the intake. Before = section 44's artifacts (the
same replay with the placeholder factors).

| | Run 3A before | Run 3A after | Run 3B before | Run 3B after |
|---|---|---|---|---|
| Year factor (2024) | 1.15 (placeholder) | 1.0000 (Jan 2020 index, assumption, flagged) | 1.15 | 1.0000 |
| Location factor (CA) | 1.150 (placeholder) | 1.001 (mean of 37 cities, .94-1.07) | 1.150 | 1.001 |
| Priced itemized total, after / before | | 0.7561 | | 0.7560 |
| Bucket shares y5 / y7 / y15 / y39 (%) | 11.28 / 0.86 / 3.99 / 83.87 | 11.28 / 0.87 / 3.96 / 83.90 | 8.38 / 0.86 / 4.02 / 86.74 | 8.38 / 0.86 / 3.99 / 86.78 |
| Bucket share error, mean / max (points) | 2.07 / 3.6 | 2.08 / 3.7 | 3.50 / 6.5 | 3.52 / 6.5 |
| Row-share distance (TVD, points) | 21.05 | 21.05 | 21.80 | 21.80 |
| Photo-visible rows evidenced / bucket right | 35 / 35 | 35 / 35 | 35 / 34 | 35 / 34 |
| Kind C rows produced / bucket right | 8 / 7 | 8 / 7 | 8 / 7 | 8 / 7 |
| Reconciliation | reconciled, RCNLD 8.0 % below B | unreconciled, 30.4 % below B | reconciled, 7.2 % below | unreconciled, 29.8 % below |
| Implied uniform factor on RCNLD | 0.856 | 1.131 | 0.848 | 1.121 |
| Allocation | 1.089 on RCNLD | withheld (`missing: allocation`) | 1.080 | withheld |

The "before" column is the sweep's cell for the production variant (square_foot + rsmeans +
carve + appraisal_physical, section 44.3, `sweep.json`), the same replay with the placeholder
factors.

### 45.4 Reading

* **The shares do not move, the dollars do.** The two factors multiply every row alike (the
  itemized rows, the square-foot model and the small systems), so the bucket shares, the
  bucket errors and the row-share distance are unchanged to within 0.05 points (the hundredths
  that differ are the four small systems, priced from the cost table against a residual that
  scales with the model). Every priced dollar is 24.4 % lower (1.001 / 1.3225), and every
  priced row now says why, with the book and page.
* **The reconciliation now tells the truth about the missing year.** With the placeholder
  factors the RCNLD landed 8 % under the price less land and the ATG check passed; the implied
  factor of 0.86 says the placeholders overstated the 2024 cost by about a seventh. With the
  Jan. 1, 2020 index the RCNLD is 30 % under and the check fails; the implied factor of 1.13
  says the 2020 dollars understate 2024 California by about an eighth once the location is the
  book's 1.001. Neither number is the 2024 index; the one that is sits in the 2024 book (or the
  quarterly RSMeans Construction Cost Index), and the pipeline now asks for it on every row
  instead of inventing it. Until it is loaded the allocation is withheld, as section 44 designed
  for an unreconciled study, and the gap is the diagnosis the guide asks for (ATG ch. 3 D(5):
  "incorrect cost estimates and factors").
* **What the firm does.** Put the 2024 (and 2025, 2026) Historical Cost Index values in the
  supplement file with their source; the assumption and the flag disappear, the reconciliation
  reruns on the real index. If the client's intake carries the ZIP, the city factor replaces the
  state mean (Los Angeles .99, San Francisco 1.03, Sacramento .97: the state's cities span
  .94-1.07, so the ZIP is worth up to six points on every dollar).
* **Honesty about precision.** The book's factors are two decimals and its indices one; the
  product carries four on the ratio and three on a state mean so the arithmetic is
  reproducible, not because the inputs are that precise.

### 45.5 Tests and gates

`tests/unit/domain/test_cost_indices.py` (67 tests: the manifest, every state and year, the
ratios, the ZIP lookup, the spot checks read from the images, the supplement, the parser);
`test_cost_tables.py` rewritten on the indices (17); `test_measurements.py` (+3, 5 rewritten),
`test_structure.py` (+1, 2 rewritten), `test_result.py`, `test_cost_table.py`,
`test_cross_validation.py`, `test_pack.py`; the integration set's `test_process_assets.py`,
`tests/lifecycle/test_intake_takeoff.py` and `tests/node/test_process_assets.py` pin the new
row text, factors and flags; the lifecycle study runs its worker with a synthetic 2024 index
in `COST_INDEX_SUPPLEMENT_PATH` (the firm's remedy, end to end: no assumption, the concealed
rows carved from an RCN priced at the same factor as the itemized rows, reconciled), which is
how a defect was caught: the structure rows written by `process_assets` had not been handed the
resolved year factor and priced at the book's alone while the result used the supplement. Gates from `backEnd/`: `ruff check`, `ruff format --check`,
`mypy basis evals` clean; unit `-m "not integration and not slow"`: 4135 passed; the
integration set with Docker: see the PR for the tally. Client data: none in the repository
(the data file holds published index numbers and place names; `tooling/check_no_client_data.py`
clean).
## 46. The key hierarchy: a firm's ruling serves the next study's component however the photo step read it (2026-10-30)

Design: `CLASSIFICATION_CONVENTIONS.md` section 9. Branch `claude/conventions-tiers`.
Private artifacts: `work/e2e_artifacts/conventions_tiers_2026-10-30/` (the firm's
`classification_cache` dump, `replay.md`, `replay.json`, the tiered export, both exports
through the structure replay, and their `E2E_SCORE_*.md`).

### 46.1 Why

Section 42.5, finding 1. On the live pair of studies the first study's approval wrote 113
conventions and the second study, with no engineer, was served 151 of 381 assets from them
and scored 34 of 35 visible engineer rows. The one wrong row (Fireplaces) and nine assets
on the firm's ruling labels that no convention reached came from the exact context key
not matching: study B's fireplaces came out of the photo step with no material where study
A's rulings were keyed on `wood` and `other`; a hardwood floor in the living room was read
without a material; a baseboard sat in a lobby the first study never showed. The material
and attachment the vision step reads vary between runs, so a ruling keyed on all six parts
did not generalize as far as the engineer meant it.

### 46.2 What changed

The table still holds one row per exact key and the writer still writes exact keys only.
The reader derives three tiers from them and serves the most specific one with a
candidate: the exact key; the same label and room whatever material and attachment the
photo step read (`label_room`); the label alone (`label`). Property class and rules version
are never relaxed. Four decisions, each measured on the live pair before it was written
down (46.3):

1. **Labels whose material is the item keep it at every tier**: the generic floors the
   rules table resolves by material, and the surface finishes `wall_finish` and
   `ceiling_finish` (wallpaper and wood paneling are coverings, drywall and painted
   masonry are the wall). Every other label carries the item.
2. **The catch-all `other` never generalizes.** A ruling on a bench says nothing about an
   awning.
3. **A label-only match does not outrank an uncontested rule row.** A ruling from another
   room is weaker than the guide's answer for this room; it beats the model and a
   contested row. The same label in the same room (tier 2) beats every rule row: it is the
   engineer's ruling on this item here, read with another material.
4. **Rulings that disagree inside a tier**: the answer whose strongest ruling has the most
   approvals wins; equally approved disagreeing rulings serve nothing, no coarser tier is
   consulted, and the rules table or the model classifies the asset with
   `convention_conflict:<tier>` (`BASIS-ASSETS-014`) so the engineer settles it; the
   engineer's ruling becomes the exact row, which wins next time.

Approvals and hits are counted on the row that served: `process_assets` records the hit on
the served key and a `cache` asset let stand at the next approval confirms the row named in
its `convention.key`, not a row for its own key (there is none). The asset's `convention`
gains `matched_tier` (`GET /assets`, the export; an asset classified before this change
reads as `exact`) and the rationale says `matched: label and room (ruling keyed ...)`. The
prefetch is by label (`get_for_labels`), which also reaches the concealed rows' labels
(the structure model's views were not prefetched before, so a ruling on a concealed row
could never be served). No migration.

### 46.3 Measured on the live pair, without a paid run

`evals.conventions_replay`: study B's stored export (run 3B, 381 assets) and the firm's
113 rows as of study A's approval (the local stack's `classification_cache`, filtered to
the rows study A seeded, one approval each; re-keyed from `2026-10-29.1` to the running
`2026-10-30.1`, which only added the concealed-row labels). Every asset goes through the
production router with the rows indexed as `process_assets` indexes them; a convention
that serves replaces the classification, dollars are untouched, `evals.e2e_report` scores
the result against the engineer's rows. The exact-tier replay reproduces the stored
export's 151 `cache` assets exactly (the regression check).

| | stored (exact key) | **tiered** (section 9) | plain (three tiers, no guards) |
|---|---|---|---|
| served from a convention: exact / label_room / label | 151 / 0 / 0 | 151 / **21 / 19** | 151 / 32 / 26 |
| newly served (were model or rule) | | 40 (36 model, 4 rule) | 58 |
| buckets moved | | 9 | 23 |
| label-tier matches an uncontested rule row outranked | | 7 (bathroom sinks) | 0 |
| conflicts flagged | | 0 | 15 (`other`) |
| bucket right by count (of 35 evidenced rows) | 34 | **35** | 35 |
| wrong rows | Fireplaces | none | none |
| classification: model / rule / convention | 65 / 165 / 151 | **29 / 161 / 191** | 23 / 149 / 209 |

What moved under the tiered policy, every one to the engineer's bucket: the two
fireplaces (tier 2: the living-room and bedroom rulings keyed on `wood` and `other`), the
living-room hardwood floor read without a material (tier 2), the kitchen sink read as
porcelain where the ruling said ceramic (tier 2), the exterior light read without a
material (tier 2), the lobby and mechanical-room baseboards, the lobby hardwood floor and
the living-room wainscoting (tier 3: rooms the first study never showed; baseboard and
wainscoting have no rule row, hardwood's is contested). Fireplaces goes to 3 of 3 assets
5-year and the row flips to right; Wood Flooring goes from 5 of 7 to 7 of 7; the other 31
newly served assets had the bucket the model had already given them, so what changed for
them is 36 model calls that are not made (65 to 29).

The guards, measured:

- **The rule-row gate (decision 3)**: with the plain label tier, the firm's two sink
  rulings (the kitchen sink and the garage utility sink, 5-year, against the guide) serve
  the seven bathroom sinks, which the guide puts with the building (ATG p.315) and the
  engineer's Plumbing row agrees (18 of 18 assets 39-year in the stored export). The gate
  blocks exactly those seven and nothing else.
- **The catch-all exception (decision 2)**: without it, 16 `other` assets are served from
  unrelated rulings (a bench's 15-year to an awning, a removable item's 39-year to a towel)
  and 15 conflict.
- **Surface finishes keep their material (decision 1)**: with the material relaxed for
  `wall_finish`, the firm's wallpaper and wood rulings (5-year) and its drywall, painted
  masonry and fiberglass rulings (building) disagreed on six of study B's walls and all six
  went to the model flagged; keeping the material serves four of them from the matching
  material's ruling (the bucket the model had given) and leaves the two unruled materials
  (ceramic, vinyl) to the model, unflagged. Conflicts under the tiered policy: zero.
- **Assets on the firm's ruling labels still without a ruling**: the seven bathroom sinks
  (right: the engineer agrees with the rule) and the two wall finishes above. The nine of
  section 42.5 are all served.

Bucket shares: on the export alone the 39-year share drops from 61.1 % to 55.5 % (the
engineer's is 80.2 %) because the newly served items are priced heavy on the pipeline's
side and the export predates the structure model; through `evals.structure_replay` (section
44) the stored export scores 34 of 35 with a 39-year share of 86.7 % (error 6.5 points) and
the tiered one **35 of 35 with 84.8 % (error 4.6 points; 5-year 10.3 % against 14.4 %,
error 4.0)**. Every number above is one property and one firm's 113 rulings.

### 46.4 What this does not change

The writer (exact keys only); the rules table; the classifier prompt. A ruling of another
firm never matches at any tier. The unattended approval of study B counted as an approval
of every convention it let stand (section 42.5: 233 approvals over the firm's rows); the
replay sets the rows back to one approval each, which is the state at study A's approval,
and whether an approval with no engineer in the loop should count is a product question
this change leaves open. One asset per label and room is the measure here; the tier that
serves a component is on the asset, so a later study can be audited the same way.

### 46.5 Tests and gates

`tests/unit/classify/test_router.py` (the tier keys, each tier, precedence, the rule-row
gate, the conflict and its flag, the most approved answer, the generic floor and the
surface finish, the catch-all, another firm; 24 tests), `tests/node/test_process_assets.py`
(the next study's wool carpet, cellar garage door and vinyl floor; hits, the export's
`matched_tier`, the approval confirming the served rows), `tests/lifecycle/test_conventions.py`
(the engineer re-reads a material at the components gate of the second study; the ruling
still serves it on the real workflow), `tests/unit/evals/test_conventions_replay.py` (the
three policies, the regression check, the conflict, the rebuilt conventions, the CLI),
`tests/api/test_outputs.py` and `tests/unit/errors/test_catalogue.py` updated.
`tests/LEDGER.md` has each. Gates: `ruff check`, `ruff format --check`, `mypy basis`, the
unit set and the integration set (see the PR for the tallies).


## 47. The cost index store: every study gets a sourced year index without a code change, and a value is never fetched twice (2026-10-30)

Follows section 45 (the indices from the 2020 books, PR #96), which left every 2024 study
priced at the Jan. 1, 2020 index with the assumption on every row and the reconciliation 30 %
under the basis, because no later Historical Cost Index was in the corpus and the only way to
load one was a file path in the settings. Bryan's rule for what should happen instead: "if we
already searched for 2024 and we've already used it, we don't have to go back and get it
again. We could just put it on or save it somewhere so that we can check first there."
Section 46 is PR #95's (the conventions key hierarchy). Design: COST_INDICES.md sections 9-12;
the API: docs/api/COST_INDICES.md. No paid run: both live exports of section 42.5 were
repriced and replayed offline. No client dollar amount appears here; ratios and shares only
(artifacts: `work/e2e_artifacts/cost_index_store_2026-10-30/`, private).

### 47.1 Research: where the index is published, and why no conversion

The brief asked for one public fallback series with a documented conversion to the RSMeans
base and its error quantified on 2020-2024. The research (every URL in the session's
hand-over note) found something better than a stand-in: **Gordian publishes the actual
RSMeans Historical Cost Index free**, one landing page per cost-book year
(`https://www.rsmeans.com/landing-pages/<year>-rsmeans-cost-index`), as four lines of the form
"The actual July 2024 Historical Index = 294.8" (January, April, July, October), verified by
fetching every page 2020-2026 on 2026-10-30. The July values (the ones the books print as a
past year's value) are 2020 234.6, 2021 257.5, 2022 297.1, 2023 293.7, 2024 294.8, 2025 300.7,
2026 314.5; a Virginia DMAS worksheet reprints the same July series and Gordian's Quarterly
Construction Cost Insights reports state the base ("a 30-city national average with a base of
100 on January 1, 1993"). The books' 2015-2019 values on a third-party page that lists the
same series match the 2020 book exactly, which validates the series identity.

The stand-ins were measured anyway, as the ratio of each series to its 2020 value against the
HCI's July ratio (1.098 / 1.266 / 1.252 / 1.257 / 1.282 for 2021-2025): ENR's Building Cost
Index annual average is within 3.4 % where it is public (2021-2023; 2024-2025 are behind a
subscription); Turner's annual average lags by 7 to 13 % in 2021-2023 and 2 to 4 % after;
Turner's fourth quarter by 4 to 10 % then within 2 %; the BLS Producer Price Index for
construction inputs (goods) overshoots by 8 to 12 % every year (materials only, where the HCI
includes installation); the Census constant-quality house index by 4 to 9 %. No free series
tracks the HCI within 2 % every year, and the one number that sets every dollar is free on
Gordian's own page. Decision: the public source is the HCI itself (`rsmeans_public`), read from
the page with the line as printed, the URL and the page's sha256 on the row; no fetcher
converts. The `derived` column and the `cost:YEAR_INDEX_CONVERTED` flag exist for a firm that
enters a converted value by hand for a year no page covers, with the error band from the table
above in the row. RSMeans Data Online (the subscription; no public API documentation, data
integration sold through sales) is a stub that reads its credentials from the settings and
records itself unavailable without them.

### 47.2 What was built

* **The store** (migration `0016_cost_indices`, `basis/db/repositories/cost_index.py`):
  `cost_indices`, one append-only row per obtained value keyed by `(source, series, year,
  region, version)` with the value as published, `as_of`, the citation, URL, the document's
  sha256, who obtained it and when, and the ledger row that added it; `cost_index_runs`, one
  append-only row per attempt (what was asked for, what arrived, what failed, who). A value is
  written only when its key is new or its current version differs (`plan` then `insert` under
  one advisory lock): never twice, whoever asks; a revision is the next version and the old row
  stays, so a completed study reproduces. Seeded from the released 2020-book file (51 years,
  651 location rows, each citing its page) by `python -m basis.costs.refresh seed`,
  idempotently; an empty store prices exactly as the file alone (tests pin both).
* **Selection** (`basis/costs/indices/select.py`): the store's current rows become the values
  a study prices with, by source rank (a manual entry first, then `COST_INDEX_SOURCES` in
  order, then the released book; a converted value never outranks a published one), the book
  under everything; the location table of the latest year at or before the cost year. The
  per-study choice (the intake's placed-in-service year, the appraisal's effective date as
  the cross-check, the intake's ZIP or state, then the appraisal's) is unchanged and the
  citations on every takeoff and in `cost_source` are byte-identical to section 45's for the
  seeded store (the regression check).
* **The missing-year rule**, now an outcome code: no loaded index for the cost year means the
  latest loaded one is applied, the assumption is on every priced row, every priced row is
  flagged `cost:YEAR_INDEX_MISSING` (BASIS-COST-001; the study completes), and
  `summary.basis.cost_index` says `status: missing`, the factor applied, the value the engineer
  must enter in words and where; `missing` lists `cost_index`. Never a silent fallback, never a
  stopped study (BASIS-CHECK-016 of section 45 is retired into it; it was never released).
* **Acquisition, in order**: the store; else the configured fetchers (`basis/costs/indices/
  <source>.py`, one protocol: `name`, `network`, `unavailable`, `wanted`, `fetch`), each asked
  only for the years the store lacks from that source; else the flag. Three sources:
  `rsmeans_2020_book` (offline, the seed), `rsmeans_public` (Gordian's pages; the year's value
  is the July figure as the books list past years, an earlier quarter stands in marked
  provisional until July appears; HTML comments and scripts stripped first because the 2026
  page carries the previous year's lines in a comment; a month printed twice with different
  values fails the year rather than guessing), `rsmeans_api` (the stub). Manual entry:
  `POST /v1/cost-indices` (a firm owner; validation of series, year, region, value, `as_of`,
  citation; the row and a ledger row with the user; the same value twice is 409 and nothing is
  written) and `GET /v1/cost-indices`, `GET /v1/cost-indices/runs`; the CLI's `enter`,
  `import`, `export`.
* **Refresh**: `python -m basis.costs.refresh` (cron or Cloud Scheduler, monthly or on
  demand; the relay's pattern of a process with its own entrypoint), one ledger row per
  source per run, a timeout per fetch, exit 1 on a failed source; a second run adds nothing.
  It never touches a study: a value that arrives later reaches a study when its assets step
  reruns. Network fetches happen only there, never in a request. Two settings, not
  assumptions: `COST_INDEX_FETCH` (default off: the seeded store and manual entries only) and
  `COST_INDEX_SOURCES` (default `rsmeans_2020_book,rsmeans_public`).
* The offline replays take the store's values: `evals.reprice_export --supplement` and, new,
  `evals.structure_replay --supplement` (section 45's replay had priced the square-foot RCN
  from the book alone: the first replay here showed the building bucket's share falling while
  the itemized rows rose, which is how the gap was found; the assets step itself already
  handed the structure rows the resolved factor, section 45).

### 47.3 The two live exports, replayed with the published July 2024 index

Both studies: cost year 2024 (placed in service 2024-12-03), California, no ZIP on the intake.
"Section 45" = the same replay with the 2020 index applied to 2024 (the missing-year rule);
"now" = the July 2024 Historical Cost Index from Gordian's page (294.8; year factor 294.8 /
239.1 = 1.2330), entered as the store's value, every other input unchanged. "Placeholder" is
section 44's run with the hand-typed 1.15 x 1.15.

| | Run 3A, section 45 | Run 3A, now | Run 3B, section 45 | Run 3B, now |
|---|---|---|---|---|
| Year factor (2024) | 1.0000 (Jan 2020 index, assumption, flagged) | 1.2330 (July 2024 = 294.8, cited; no assumption, no flag) | 1.0000 | 1.2330 |
| Location factor (CA) | 1.001 (mean of 37 cities) | 1.001 | 1.001 | 1.001 |
| Priced itemized total, as a share of the placeholder's | 0.7561 | 0.9336 | 0.7560 | 0.9336 |
| Bucket shares y5 / y7 / y15 / y39 (%) | 11.28 / 0.87 / 3.96 / 83.90 | 11.27 / 0.86 / 4.01 / 83.85 | 8.38 / 0.86 / 3.99 / 86.78 | 8.37 / 0.86 / 4.04 / 86.73 |
| Bucket share error, mean / max (points) | 2.08 / 3.7 | 2.06 / 3.6 | 3.52 / 6.5 | 3.49 / 6.5 |
| Row-share distance (TVD, points) | 21.05 | 21.05 | 21.80 | 21.79 |
| Photo-visible rows evidenced / bucket right | 35 / 35 | 35 / 35 | 35 / 34 | 35 / 34 |
| Kind C rows produced / bucket right | 8 / 7 | 8 / 7 | 8 / 7 | 8 / 7 |
| RCN / B, RCNLD / B | 0.886, 0.696 | 1.092, 0.858 | 0.893, 0.702 | 1.102, 0.866 |
| Reconciliation | unreconciled, RCNLD 30.4 % below B | unreconciled, 14.2 % below B | unreconciled, 29.8 % below | unreconciled, 13.4 % below |
| Implied uniform factor on RCNLD | 1.131 | 0.917 | 1.121 | 0.909 |
| Allocation | withheld | withheld (`missing: allocation`) | withheld | withheld |

### 47.4 Reading

* **The year factor is now a published number with its page on every row**, and the
  assumption and the flag are gone from both studies. The factor the placeholder had guessed
  (1.15) was 7 % low against the index's own 1.233; section 45's honest 1.0000 was 19 % low.
* **The shares do not move** (the factor multiplies every itemized row and the square-foot
  RCN alike): bucket errors and the row-share distance are unchanged to within 0.05 points,
  and the photo-visible and kind C scores are identical. The regression check holds.
* **The reconciliation halves its gap and still does not reconcile.** With the dollars
  escalated to 2024 the replacement cost new is 9-10 % above the basis and the depreciated
  total 13-14 % below it, outside the 10 % tolerance, so the allocation stays withheld and the
  diagnosis stands: the implied uniform factor moved from 1.13 (the schedule short of the
  basis) to 0.91 (the schedule, depreciated at the appraiser's 21 % physical rate, short by the
  depreciation itself). The remaining gap is not the index: it is the depreciation rate and the
  5-year side (section 44.4's cabinets, FF&E and window treatments), which the engineer's
  report allocates without depreciating. That is a structure-model question for the next
  section, not a cost-index one; the index did what it could, and the result now says so with
  `cost_index.status: published` instead of a `missing` note.
* **What the firm does now**: nothing in code. On the deployment, `python -m
  basis.costs.refresh seed` once, then either `COST_INDEX_FETCH=on` and the monthly job, or
  `POST /v1/cost-indices` with the year's value and its citation. The 2024 study reruns its
  assets step and prices on 1.2330 with the page cited.

### 47.5 Tests and gates

Unit: `tests/unit/costs/` (the book fetcher, the public page parser against a recorded copy of
the 2024 page with the 2026 page's comment decoy, the selection and its priority, the CLI
parser and the export round trip; 25 tests), the domain tests updated for the store-first
lookup, the result's `cost_index`, the metadata of the two tables. Integration with Docker:
`tests/integration/db/test_cost_index_store.py` (append-only, versioned, never twice; the
refresh job seeds once and adds nothing twice, skips a network source while fetching is off
with the reason, asks once when on, records a failure without writing; the manual path; 11
tests), `tests/api/test_cost_indices.py` (owner-only entry, validation, 409 on a repeat with
nothing written, revision, listing, ledger, idempotency; 15 tests), migration 0016 forward and
back, the OpenAPI contract on the new routes, and the lifecycle study whose 2024 index now
arrives through the store's manual path. Gates from `backEnd/`: `ruff check`, `ruff format
--check`, `mypy basis evals` clean; unit `-m "not integration and not slow"` and the
integration set: see the PR for the tallies. Client data: none in the repository (the recorded
page is a public marketing page's index block; `tooling/check_no_client_data.py` clean).

## 48. Rooms open named, typed and floored: the name's words first, then every photo's vote (2026-10-30)

The live runs of section 38 opened the Rooms review on the per-photo grouping: 49 rooms for
52 photos, every name carrying its floor ("2nd Floor Bed 2"), and some rooms typed
`ambiguous` or wrong although their file names said what they were; the engineer fixed two
room types by hand besides the 16 merges. Since PR #93 the room type also picks
classification variants and is part of the firm conventions' key, so a wrong type now moves
buckets, not only the review screen. This section makes a room's three fields independent and
measures them on the client property's 52 photos, before and after, without a paid run.
Section 47 is PR #97's. No client name, address or amount appears here; room descriptions
are generic.

### 48.1 What the Rooms review showed, and why (verified in code and data)

Read from the local stack's database (revision 1 of the four live studies of 2026-10-29/30,
read-only) and the stored per-photo run `vision_decomposed_v2_gpt61sol` (3 repeats):

* **Names kept the floor.** The room's name was the file name's remainder (`display_name`),
  floor prefix included, though the floor already had its own column: 36 of the 49 rooms.
* **The type was the model's alone.** A file-name room's type was the per-photo model's
  majority (`_vote_room_type` at creation, `vote_identity` in `reconcile_rooms`' vote); the
  file name was read for the room key and the floor, never for the type. A one-photo room whose
  photo answered `ambiguous` therefore opened as Ambiguous: the stored run answered `ambiguous`
  11 times in 156 (a mechanical room named after its equipment, a bedroom's seating area, the
  three basement bedrooms), and the live studies 1 to 2 rooms each. Wrong confident answers
  went through the same way (basement bedrooms as `storage` or `basement`, a bedroom's seating
  area as `living_room`, an entrance as `lobby`): 4 to 7 rooms per repeat with a wrong or
  ambiguous type.
* **A photo with no component never voted.** Its answer existed only in its `components`
  rows' `raw` (`_answer` in `reconcile_rooms`); 3 of the stored run's 156 answers listed no
  component. Such a room kept the answer of whichever photo was analyzed first.
* **`ROOM_RECONCILE=false` skipped the vote** (the activity returned before planning), so
  those rooms kept the first photo's answer too.
* **Exterior views took the model's floor**: 0 to 3 of the 13 site views per live study were
  `floor 1` (5 in the four studies).
* Photos left without a room: none with descriptive file names (every name is a room hint);
  with camera names the model's identity groups and an `ambiguous` answer goes to the
  Unassigned room (1 to 4 photos of 52).

### 48.2 The standard

The RESO Data Dictionary 2.0 keeps a listing's `RoomType` (33 values, the ordinal inside the
value: "Bedroom 2", "Bathroom 1") and `RoomLevel` (Basement, First, Second, Third, and the
relative Main, Lower, Upper) as separate fields (dd.reso.org/DD2.0/lookups/RoomType,
/RoomLevel, read 2026-10-30). Basis's `rooms` already had the three columns; what was missing
was filling them from the right evidence each. The relative levels have no number, so they
are not read: an unknown floor stays null.

### 48.3 What changed (`basis.vision.rooms`, `reconcile_rooms`, migration 0017)

* `room_name`: the file name without the floor prefix the labelling rule identified
  (`<n>st/nd/rd/th Floor`, `Basement`), ordinal and words kept: "2nd Floor Bedroom 2" is
  `Bedroom 2` on floor `2`, "Basement Bath" `Bath` on `0`. A name with no identified floor is
  unchanged, and so is a name that is only a floor ("Basement").
* `name_room_type`: the type the name says, from a fixed vocabulary of common US residential
  and small-commercial room terms (the RESO lookup's among them), longest phrase first, words
  after a locator ("to", "from", "off", ...) ignored, the last match winning (the head of an
  English noun phrase is last: "Bed 2 Bath" is a bathroom, "Bed 3 Seating Area" a bedroom).
  A name with no room word ("Extra Area Seating Area", "Access from 1st Floor") says nothing.
* `resolve_room`, one function for creation, the vote, `group_photos` and the eval: type =
  the name's, else the per-photo answers' majority (`ambiguous` abstains); floor = a file name's,
  else none for exterior and parking (a floor does not apply to the grounds), else the answers'
  most common, else null; `0` is kept as the basement. The name is never voted.
* `photo_room_answers` (migration 0017): `analyze_photo` records each analysis' room type,
  confidence, floor and ordinal, so every analyzed photo votes, components or not; a photo
  carried into a revision answers from its latest analysis. Photos analyzed before the
  migration vote from their component rows as before (no backfill).
* The vote runs with the room step off too, and every change is a `room.voted` event (actor
  `model`, before/after, the source of each field: `file_name`, `model` or `none`;
  docs/api/TIMELINE_AND_AUDIT.md).
* Unchanged on purpose: room keys (`group_key`), aliases, assignments and the merge path; a
  room a reviewer corrected (`source = manual`) is never voted and no room is ever renamed, so
  rooms grouped before this change keep their names; rooms are never merged because names or
  types match (two "Bath" rooms on floors 1 and 0 stay two rooms).
* Found while testing the round trip: a `room` correction with a numeric `floor` (`0`) passed
  `POST /decisions` and then failed `apply_corrections` on the database write (asyncpg
  `DataError`), so the rejection would never apply. Now a whole number is stored as its text,
  and a value the activity cannot store (empty name, `true`, `2.5`, out of range) is a 422 at
  the route (`BASIS-CORRECT-027` in the activity).
* The export's assets CSV gains a last column `room_floor`, since two rooms may now share a
  name.

### 48.4 Measured: the 52 photos, no model call (`evals.room_metadata`)

The grouping scored is `initial_rooms`, the function the activities' writes equal (an
integration test compares the database after `assign_room_by_name`, the answer rows and the
vote with it). "Before" is main `7aa78c3`'s production path replayed by a scratch script
(creation by the first photo in file order, then `vote_room` on the photos with component
rows). Truth: the room labels (`room_inventory_v1.json`, 33 rooms; for a view the labels alias
into another room, its photos' labels), with `entry` as hallway and `stairs` as stairway. The
name is scored against the engineer's own words without the leading floor, which checks the
format, not a model. Ranges are over repeats.

| answers | scenario | | rooms | name | room type | floor | all three | Ambiguous rooms | photos unassigned |
|---|---|---|---|---|---|---|---|---|---|
| live studies (4) | file names | before | 49 | 13 | 43–45 | 45–49 | 9–13 | 1–2 | 0 |
| | | after | 49 | 49 | 48–49 | 48–49 | 48–49 | 0 | 0 |
| | with the 16 aliases | before | 33 | 3 | 29–31 | 31–33 | 1–3 | 0–1 | 0 |
| | | after | 33 | 33 | 32–33 | 32–33 | 32–33 | 0 | 0 |
| | camera names | both | 17–19 | – | – | – | – | 0 | 1–2 |
| stored run (3) | file names | before | 49 | 13 | 42–44 | 49 | 13 | 3–4 | 0 |
| | | after | 49 | 49 | 49 | 49 | 49 | 0 | 0 |
| | with the 16 aliases | before | 33 | 3 | 29–30 | 33 | 3 | 1–2 | 0 |
| | | after | 33 | 33 | 33 | 33 | 33 | 0 | 0 |
| | camera names | both | 13–14 | – | – | – | – | 0 | 3–4 |

Per photo (the type of the room each photo is in against its own label), file names: 45–48
of 52 before, 51–52 after. All three fields right over every room and repeat: 46 of 196 → 194
of 196 (live), 39 of 147 → 147 of 147 (stored run). The three misses left are model-only
fields (the name says nothing, or no file name gives a floor): a seating area typed `hallway`
in one live study (labelled living room), and the mechanical room given floor `0` by the model
in one live study, where the room label has no floor but its photos are labelled basement.
The room count does not change (49, or 33 after the merges): proposing the merges is not this
change. The camera-name rows do not change either: the model-identity path was left as it was.

**What the number does not prove.** The vocabulary was written knowing these 52 names, so the
file-name rows are a regression, not out-of-sample evidence; the 30 other names in the tests
(listing and inspection terms) were written with it too. A second client package is the test.

### 48.5 Not done, and what it touches

* **Downstream not replayed**: the voted type is the room step's input (its path and prompt)
  and the classifier's context, so rooms whose type changed here (the basement bedrooms, the
  mechanical room, a seating area, an entrance) will get different room-step and convention
  keys; neither was rerun (both are paid or need a live study).
* **Approval with unplaced photos**: this branch does not add the "every photo in a room before
  Rooms approval" guard; the separate room-management work (not yet on main) implements it at
  `POST /decisions`, and the rooms written here are compatible with its rule (no assignment, or
  the room named exactly `Unassigned`).
* Floors written as words ("Second Floor", "Floor 2", "Ground Floor") are not read, so such
  a name keeps its words and the floor comes from the photos or stays null; the room key rule
  is the labeller's and was not changed.
* The model-identity path (camera names) still takes the model's floor for exterior views.
* Rooms grouped before this change keep the floor in their names until regrouped.

### 48.6 Tests and gates

`tests/unit/vision/test_room_metadata.py` (names, the vocabulary on the package's and 30 other
names, sources per field, basement `0`, unknown floors, the grounds, the vote with
component-less photos, multiple floors and duplicate types kept apart, aliases, failed
analyses, camera names and the Unassigned tray, corrected-floor parsing);
`tests/unit/activities/test_reconcile_rooms.py` (the answer row, the `room.voted` event and
its retry, a carried photo's answer, a reviewer's rename, refloor and move surviving the vote,
a re-analysis and a retry across the two gates, the vote with the room step off, legacy names
and legacy rows); `tests/api/test_room_metadata_api.py` (`GET /rooms` → `POST /decisions` →
`apply_corrections` → the vote → `GET /rooms` at the next revision with provenance; the 422s;
the database equal to `initial_rooms`); `tests/unit/evals/test_room_metadata.py`. Gates: see
the PR.
