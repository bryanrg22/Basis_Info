# From the two inputs to the finding: what works, what is measured, what is missing (2026-10-29)

Question from Bryan: we have tested extraction, rooms and components "to the max". Is the whole
system tested? How do we get from the client's input files to their output file, information-wise?
What is required, what is used, how are values assigned and sourced, and does our output come
close? This review answers from the code, the engineer's report and the existing run artifacts.
Every code claim is cited in `/private` notes behind it (the three investigations of this date:
pipeline coverage, output-report anatomy, end-to-end artifacts; the private versions with the
client's figures are under `BASIS_EVAL_DATA_DIR/reports/`). No client values appear here.

## 1. Which file is which

| Role | File (client package) | Ours |
|---|---|---|
| Input | the property's legacy 1004 appraisal (20 pages) | `inputs/appraisal.pdf`, truth `appraisal/truth/client.json` |
| Input | the site photos (52) | `inputs/photos/` |
| **Output** | the engineer's finished cost segregation (12 pages) | `inputs/engineer_report.pdf` |

Everything else under the eval data is derived by us (labels, truth, the UAD 3.6 re-expression,
the mock report).

## 2. What the output actually is

**Format.** There is no industry-standard file or layout for a cost segregation report (IRS ATG,
Publication 5653, ch. 3 D(1), ch. 4 A(1) and D(1)). The ATG lists the elements a quality report
should contain (D.1–D.9). The engineer's deliverable is a 12-page PDF assembled from three
spreadsheet tables, eight of the site photos (uncaptioned) and the appraisal's sketch page. It
carries D.5 (the schedule of property units, at unit-group level), part of D.1 and D.9, and none
of D.2–D.4 or D.6–D.8: no narrative, methodology, cost source, quantities, unit costs, asset-class
codes, citations, certification, preparer, date or even the property's identity. In ATG terms it
is a Property Unit Summary without the Property Unit Detail. It is not a signable report; the
engineer may hold a fuller one.

**The numbers.** The whole report is a deterministic function of three inputs:

- 46 component rows, each with a replacement cost new (RCN) and one recovery bucket
  (5 / 7 / 15 / 39-year; 18 / 1 / 6 / 21 rows);
- one building basis B (the purchase price less a flat land share);
- one closing-cost figure CC.

Then: total T = B + CC; a single factor f = T / ΣRCN; every row = RCN × f; bucket totals and
percentages; a 2×5 allocation block splitting B and CC pro rata by bucket; and a page of the
same dollars regrouped by trade (12 building systems). Every printed sum, percentage, pro-rata
cell and the factor reconcile to the cent (verified independently). So the reproduction test for a
report builder is exact: given the 46 RCNs, B and CC, produce 88 numbers to the cent. The real
test is producing the 46 RCNs and their buckets.

**What it uses from the appraisal.** Visibly, only the sketch. Arithmetically, the contract
price (B is the price less a round land percentage). Not used: the appraiser's site value, the
cost approach, the appraised value, dates, comparables, neighborhood, zoning, legal, parcel. The
physical description (areas, stories, foundation, materials, heating, fireplaces, appliances,
outbuildings, driveway) plausibly informed the cost model but leaves no trace. Of the 46 rows, 38
are supported by what the photos show (29 directly, 9 with vocabulary we added) and 8 cannot come
from photos at all: foundations, framing, interior partitions, site utilities, fire protection,
data cabling, access control, window guards. Those 8 are about a third of the study's dollars.

**How values were assigned.** The report does not say. The RCNs are not round, so they were
computed (a unit cost × a quantity, and a square-foot systems model for the concealed building
systems), not typed in; no quantity or unit cost is shown, so measured vs estimated is unknowable
from the report. The uniform factor f (about 0.71 here) is a reconciliation to basis applied
equally to every row; it is not depreciation. The ATG objects to a uniform reconciling factor
(ch. 3 A(6), D(3)–(5)), which applies to this method and to our mock.

**Things the output needs that are not in the two inputs:** the closing statement (CC), the
source of the land percentage, the cost database and its settings (cost year, location index,
quality), site measurements for the 15-year items (the driveway is the largest; the sketch has no
site plan), allowances for concealed systems, the use decision (39 vs 27.5 years), and the
property's identity and dates.

## 3. Is the whole system tested?

The plumbing is: lifecycle tests run start → four gates → assets → export, rejections, retries,
restarts, budget and cancellation. Each model step has a measured eval on the one property:

| Step | Measured against | Result |
|---|---|---|
| Appraisal field extraction | 41 documents (human and XML truth) | 0.967 replay on the client; 0.879 production across sets; critical fields 1.000 |
| Per-photo counts and labels | verified boxes, photo labels | count MAE 0.12–0.14, F1 0.74 |
| Masks (SAM 3) | verified boxes | mAP 0.76 |
| Room grouping and inventory | hand-labelled rooms | inventory MAE 0.44, F1 0.84 |
| Materials | photo surface labels | present 9/9 rows; hit 0.64 |
| Report-row coverage | the engineer's 46 rows, presence only | 33 of the 39 photo-visible rows |
| Classification (bucket) | the engineer's buckets | 0.688 on captions; 23 of 33 rows live |
| Citations | the reference corpus, verbatim | 100% verified live |

**Not measured, and not built, is the part that produces the finding:**

1. **No stage reads the appraisal.** All 134 fields are stored with page, box, source and quote,
   shown at the review and exported, and then nothing downstream consumes a single one. GLA, site
   value, cost approach, year built, stories, location: unused.
2. **Quantities are constants.** Pricing multiplies a unit cost by a "typical area" per room type
   (kitchen 150 sq ft, bath 75, everything else 150). No room is ever measured; GLA is not
   consulted; garage and basement are 150 sq ft rooms. Eleven exterior and site labels (roof,
   siding, gutters, driveway, fence, patio, landscaping…) cannot be priced at all because they
   need an area or length nobody measures.
3. **Location does nothing.** The cost location factor is a global setting fixed at 1.0; a
   per-state table exists but nothing calls it. The cost year is a default, not the placed-in-
   service date.
4. **There is no basis or land math.** No purchase price → land → depreciable basis → closing
   costs → scaling anywhere in the code.
5. **Nothing produces the concealed systems** (the 8 rows, about a third of the dollars).
6. **There is no result.** No results endpoint, no report, no depreciation schedule; the only
   aggregate is a JSON summary written to storage that no route serves.
7. **Dollars have never been compared.** The engineer's cost per row is in our label file,
   loaded and never scored. Row presence and bucket were compared; dollars, bucket totals and
   percentages were not.

## 4. Does our output come close?

A free comparison exists: a completed local study of the client's property (2026-10-19, today's
model defaults except the old property class, no room aliases, no components gate, luna
extraction) exported 376 assets, 249 of them priced. Against the engineer's 46 rows, by the same
evidence rule the live runs used:

- **Rows evidenced:** 37 of the 38 photo-visible rows (window guards missed; nothing visible).
- **Bucket right:** 26 of the 37 evidenced rows (0.565 of all 46). Identical in a second run, so
  the 11 misses are conventions, not noise: moldings, fireplaces, the two wall rows (painted walls
  answer them; the rule has no material filter), half walls, exterior lighting, FF&E, general light
  fixtures, plumbing fixtures, ceramic tile, bathroom vanities. Together about a sixth of the
  engineer's dollars.
- **Dollars: not comparable yet.** The pipeline's priced total is under a third of the
  engineer's basis, and its bucket shares are far off (5-year share about three times the
  engineer's), because the two are different quantities: the engineer's rows are a pro-rata share
  of the purchase-price basis; the pipeline's are replacement costs on placeholder 150 sq ft areas,
  with 127 of 376 assets unpriced, 30 assets landing in two engineer rows, 69 in none, and
  49 rooms where the labels have 33.

So: the system classifies what it can see, with verified citations, at about 70% bucket accuracy
on the rows it answers; it cannot yet size, price, total, allocate or report a study.

## 5. Bryan's specific questions

**Required values for a study to continue.** Today none are enforced: a missing critical field
sets "needs review" and zero confidence, the gate still opens, and the engineer can approve. The
only hard requirement is one confirmed appraisal upload. What the *output* actually requires
(section 2): contract price; a land allocation (from the appraisal's site value, or the firm's
ratio); closing costs (intake, not in the appraisal); the building's areas (GLA, basement, garage,
site) for quantities; the property's use (27.5 vs 39); the state or ZIP for the cost index; and
the placed-in-service date for the cost year. Those should become the required intake; a study
with a different appraisal must still supply them.

**8 rooms in the appraisal, 9–10 from the photos.** No cross-check exists. The photo side wins
silently: assets are built per photo room, rooms without photos do not exist in the study, and
the engineer merges or moves photos at the rooms review. The appraisal's counts should become a
check at that gate (the room grouping over-counted 49 rooms against 33 labelled without aliases).

**Location, stories, garage, backyard, basement.** All are extracted or labelled; none change
a quantity or a price today. Garage and basement are 150 sq ft rooms; the appraisal's areas and
the cost approach's garage line are unused. Backyard and site items classify as 15-year property
but cannot be priced (no area). Location and stories are extracted and unused. The system is
capable of classifying these; it is not capable of sizing or pricing them.

**Sources.** Extraction values carry source, confidence, page, highlight box and a verbatim
quote; assets carry method, confidence, review reasons and verified citations with the excerpt,
document and pages; costs carry a readable source row (table, line, year and location factors,
assumption). The API exposes all of that except the rule id and the model's rationale on an
asset, and the takeoff's area inputs. Showing "source" per value in the frontend is feasible
today for fields, citations and cost rows.

**The basement-0 and similar rules.** Those are answer-key policies for scoring the evaluation
(what counts as correct), not product behaviour. The product rules that matter are different:
the classification rules table (53 rows, 26 of them contested) and the cost table. Keeping a
record of what the engineer changes is a later feature, noted and deferred.

## 6. The main test, and the order of work

The main test is: from the two inputs, produce the engineer's 46 rows with their buckets and
dollars, the bucket totals and percentages, and the total, and score each. To get there:

1. **End-to-end scorer** (free to build, measurable now on the existing export): a one-to-one
   allocation of pipeline assets to engineer rows (the presence rules are many-to-many today),
   and per row: present, bucket right, dollar error; per bucket and total: dollar and share
   error; the 8 concealed rows reported as "not produced".
2. **Measured quantities.** Read GLA, basement, garage and site areas from the extraction into
   the takeoff; distribute GLA over rooms (sketch later); size roofs and site items from site area
   and footprint. This unblocks the 11 unpriceable labels.
3. **Basis and land math** with intake (price, closing costs, dates, use), land from the site
   value with the stated checks, scaling to basis, exact reconciliation; unit tests with hand
   cases.
4. **Location and cost year** from the study (state/ZIP index; placed-in-service date).
5. **Concealed systems:** a building-systems cost or an explicit carve-out from the appraisal's
   cost approach, so the schedule ties to a total.
6. **A served result and the report:** a summary route (totals by bucket, basis worksheet), then
   the report and the depreciation schedule (CSV/XLSX), with the rule id and rationale stored on
   each asset.
7. **Engineer decisions:** the 11 disputed bucket conventions, the 26 contested rules, the
   source of the land percentage, their cost source and settings, whether a fuller deliverable
   exists.
8. **Then a like-for-like live run** of the client's property on today's defaults (nonresidential,
   aliases, Sol extraction, components gate; none of the existing runs has all four), ideally three
   repeats, about $7–8 each, and a second property before any number is trusted.

Steps 1–6 are build work; nothing paid is needed until step 8. The one existing export gives a
free first measurement of steps 1–3 as they land.
