# Appraisal extraction evaluation: spec (v2)

The contract the truth files, the generators, the scorer and the runner share. The v1
dataset (`appraisal_fields_v1.json`, one document, 75 fields, no pages, no absent fields)
stays readable by `evals.run`; v2 replaces it for appraisal work.

Why v2 (EVAL_REPORT 33): v1 cannot see wrong boxes, lost checkboxes, wrong pages, values
the review page shows raw, spurious values for sections the appraiser did not develop, or
whether the review gate catches the errors. Research behind every choice:
`docs/review_2026-10-15/APPRAISAL_RESEARCH.md` (sections 4 and 5).

## 1. Principles

1. **Truth is what the box says, not what is right.** Each catalogue key belongs to one
   box of the form. When the document contradicts itself (the cost section prints 692,831
   and the reconciliation 682,831), each key's truth is its own box's print, and the
   contradiction is an `expected_issue` the validators must raise.
2. **Absence is a value.** Every catalogue key has a truth status for every document:
   `present`, `absent` (the document does not carry the box at all, e.g. no certification
   pages), `not_developed` (the box exists and the appraiser left it blank, `0`, `N/A` or
   checked "not developed"; e.g. the income approach), or `unscorable` (a human could not
   decide; excluded, with a note). A printed `0` is `not_developed` only for a valuation
   approach or a cost-approach line the appraiser skipped; elsewhere (HOA dues, special
   assessments, concessions) `$0` is a real value and the field is `present` with 0; the same
   holds when such a box prints `No` or `None` (UAD 3.6 "Known Sales Concessions No"), so the
   currency normalizer reads `no` / `none` as 0 and only `n/a` or an empty box as no value. A value predicted for `absent` / `not_developed` is a
   *spurious* value, scored as an error.
3. **Documents are the independent unit.** Intervals are clustered by document; a claim
   about accuracy needs many documents (rule of three: 0 errors in 59 documents bounds the
   error rate at 5 %). One client appraisal supports regression, not an accuracy claim.
4. **Client data never enters the repository.** Truth files, PDFs, recordings and run
   outputs live under `BASIS_EVAL_DATA_DIR/appraisal/`. The repository holds code, this
   spec and tests on fabricated data only. GSE D-1 samples (copyright Fannie Mae / Freddie
   Mac, fabricated data) and the blank Form 70 are also kept out of the repository until
   their terms are confirmed.

## 2. Files (all under `BASIS_EVAL_DATA_DIR/appraisal/`)

```
appraisal/
  forms/form70_blank.pdf                 legacy URAR 1004 (Freddie Form 70), 6 pages, blank
  client/<doc>/appraisal.pdf             real client appraisals (client_a = inputs/appraisal.pdf)
  gse_d1/<scenario>/appraisal.{pdf,xml}  12 UAD 3.6 samples (SF1-5, Condo1-2, Coop1, MH1-2, TwoToFour1-2)
  synthetic_1004/<doc>/appraisal.pdf     generated legacy 1004s (section 6)
  truth/<set>.json                       one truth file per set: client.json, gse_d1.json, synthetic_1004.json
  recordings/<sha256 of the PDF>/        azure_di.json (raw SDK result.as_dict()), llm/<request hash>.json
  runs/<run id>/                         predictions.json, score.json, report.md, run.json
```

## 3. Truth file format

```json
{
  "schema_version": 2,
  "set": "client",
  "documents": [{
    "document": "client_a",
    "source": "client | gse_d1 | synthetic",
    "form": "urar_1004_2005 | uad36_urar",
    "split": "dev | regression | held_out",
    "pdf": "client/client_a/appraisal.pdf",
    "sha256": "...",
    "pages": 20,
    "fields": [
      {"key": "cost_approach.site_value", "status": "present", "type": "currency",
       "value": 85000, "printed": "85,000", "page": 3, "also_on": [],
       "quote": "Opinion of Site Value = $ 85,000", "verified": true, "note": null},
      {"key": "appraiser.name", "status": "absent", "note": "no certification pages"},
      {"key": "reconciliation.indicated_value_income_approach", "status": "not_developed",
       "printed": "0", "page": 3}
    ],
    "expected_issues": [
      {"rule": "cost_approach_total", "keys": ["cost_approach.indicated_value"],
       "note": "729,071 - 156,240 + 25,000 + 85,000 = 682,831, printed 692,831"}
    ]
  }]
}
```

Field entry:

| Key | Meaning |
|---|---|
| `key` | a catalogue key from `basis.extraction.field_mappings.FIELDS` (`section.field`). Every catalogue key must have an entry per document (status `unscorable` at worst). Keys outside the catalogue are allowed with `"extra": true` (fields cost seg needs that the catalogue lacks, e.g. `improvements.garage_type`); they are scored but reported apart. |
| `status` | `present` / `absent` / `not_developed` / `unscorable` (principle 2). |
| `type` | `text`, `int`, `float`, `currency`, `percent`, `date`, `month`, `bool`, `enum`, `enum_multi`, `area_sqft`, `baths` (section 4). Required when present. |
| `value` | the normalized truth (section 4). Required when present. |
| `printed` | the characters as printed in the box (for display and grounding checks). |
| `page` | 1-based page of the box this key belongs to (the authoritative box). |
| `also_on` | other pages where the *same fact* is legitimately printed (photo-page headers, the sales grid subject column, the sketch). A cited page in `also_on` is "page lenient", not "page strict". |
| `quote` | a short verbatim span of the page text containing the value (the box caption plus value where possible). |
| `verified` | `true` when a human checked it (or it comes from GSE XML / the generator); `false` = needs Bryan. Unverified fields are scored and reported apart. |
| `tier` | optional override of the cost-seg tier (section 5). |
| `note` | free text. |

`expected_issues`: validation findings a correct pipeline must raise on this document
(arithmetic, cross-box conflicts, implausible values). `rule` names are free strings
until the validators exist; the scorer matches on `rule` and the overlap of `keys`.

## 4. Types and normalization

The scorer normalizes the prediction and compares to `value`. Comparisons are **exact
after normalization** unless the entry carries `tolerance` (absolute) — printed numbers
have no tolerance.

| Type | `value` | Prediction normalization |
|---|---|---|
| `text` | string | NFKC, casefold, collapse whitespace, strip trailing `.`/`,`. Correct = equal; also report ANLS (normalized Levenshtein similarity, 0.5 threshold) as a secondary metric. |
| `int` / `float` | number | first number in the string, commas removed; `int` exact. |
| `currency` | integer dollars (or decimal when cents are printed) | strip `$`, `=`, spaces, commas; `-x` negative; `(x)` negative except after `=$` or `$` in the cost approach, where the parentheses are the form's subtraction marks (`=$( 156,240)` is 156240); `no`, `none` → 0; `n/a`, empty → no value. |
| `percent` | number 0-100 | strip `%`. |
| `date` | ISO `YYYY-MM-DD` | M/D/YYYY, M/D/YY (century: not more than a year ahead), ISO, "Month D, YYYY". |
| `month` | `YYYY-MM` | MM/YYYY, M/YY, ISO month. |
| `bool` | true / false | yes/no/x/checked/selected. |
| `enum` | one canonical code (vocabulary below) | case- and punctuation-folded match against the code and its synonyms. `other` codes carry text: `{"code": "other", "text": "Infloor"}`, correct when the code matches and the text matches as `text`. |
| `enum_multi` | sorted list of codes | set equality. |
| `area_sqft` | integer square feet (a derived total, e.g. UAD 3.6 below-grade finished + unfinished, carries `tolerance`) | numbers with units: `sq ft`, `sf`, `sq.ft.` as is; `ac`/`acres` × 43,560 rounded. A bare number is square feet. Truth for a box printed in acres is `{"sqft": 60548, "printed_unit": "ac"}`; correct within the rounding of the printed acres (±0.005 ac). |
| `baths` | `{"full": 2, "half": 1}` | UAD notation `2.1` (= 2 full, 1 half); `2.5` read as 2 full 1 half; `{"full","half"}` objects. |

Enum vocabularies (legacy 1004 checkboxes; UAD 3.6 converters map onto the same codes or
use `extra` keys):

| Key | Codes |
|---|---|
| `neighborhood.location` | urban, suburban, rural |
| `neighborhood.built_up` | over_75, 25_75, under_25 |
| `neighborhood.growth` | rapid, stable, slow |
| `neighborhood.property_values` | increasing, stable, declining |
| `neighborhood.demand_supply` | shortage, in_balance, over_supply |
| `neighborhood.marketing_time` | under_3_mths, 3_6_mths, over_6_mths |
| `subject.occupant` | owner, tenant, vacant |
| `subject.property_rights_appraised` | fee_simple, leasehold, other |
| `subject.assignment_type` | purchase, refinance, other |
| `site.zoning_compliance` | legal, legal_nonconforming, no_zoning, illegal |
| `improvements.units` | one, one_with_accessory_unit |
| `improvements.type` | det, att, s_det_end_unit |
| `improvements.foundation_type` (multi) | concrete_slab, crawl_space, full_basement, partial_basement |
| `improvements.heating_type` (multi) | fwa, hwbb, radiant, other (an appraiser can check two boxes; `other` carries its text as `{"code": "other", "text": ...}` inside the list) |
| `improvements.car_storage` (multi) | none, driveway, garage, carport, attached, detached, built_in |
| `reconciliation.appraisal_made` | as_is, subject_to_completion, subject_to_repairs, subject_to_inspection |
| `site.utilities_*` | public, other (with text), none |
| `site.off_site_improvements_*` | public, private |

The scorer's synonym table lives in code (`evals/appraisal/normalize.py`) and is unit
tested; adding a synonym is a reviewed change, never a silent widening.

## 5. Cost-seg tiers

Reported per tier so a change that helps rooms but hurts site value is visible. From the
research (IRS ATG p.27, 37, 64-67; Fannie B4-1.3-10):

* **critical**: `cost_approach.site_value`, `listing_and_contract.contract_price`,
  `listing_and_contract.contract_date`, `reconciliation.final_market_value`,
  `reconciliation.effective_date_of_appraisal`, `subject.property_address`.
* **high**: the cost-approach block (`cost_approach.*` amounts, depreciation, site
  improvements, indicated value), `improvements.gla_sqft`, `improvements.basement_area_sqft`,
  `improvements.year_built`, `improvements.effective_age_years`,
  `cost_approach.remaining_economic_life`, `improvements.quality`, `improvements.condition`,
  `improvements.units`, `improvements.type`, `improvements.stories`, garage fields,
  `site.area_sqft`.
* **medium**: zoning, highest and best use, amenities and features, assessor's parcel,
  taxes, flood zone.
* **low**: everything else, including rooms, bedrooms, bathrooms (validators only).

The production `critical` flag is reported as its own column so the current gate can be
judged against the cost-seg tiers.

## 6. Synthetic legacy 1004s

Generated onto `forms/form70_blank.pdf` (no AcroForm: values are drawn at measured
positions, checkboxes marked with an `X` inside the box glyph, like appraisal software
output). The generator (`evals/appraisal/synth_1004.py`) is seeded, writes the PDF and its
truth entry, and draws values the way real software prints them (no `Caption:` colons;
`$` and commas; dates `MM/DD/YYYY`; acres or sq ft; UAD `2.1` baths; `=$` in the cost
section). A profile per document picks the edge cases: cost approach developed or not,
site value present or blank, income approach `0` vs blank, half baths, acres vs sq ft,
multiple car-storage boxes, "Other" heating with text, an arithmetic error planted in the
cost section (`expected_issues`), a value repeated in a header block, long legal
descriptions. A `scan` variant rasterizes the pages (no text layer, mild noise) to
exercise the OCR path. Truth is exact by construction (`verified: true`).

## 7. Runner

`python -m evals.appraisal.run --set client|gse_d1|synthetic_1004|all --mode live|replay`

* **live**: the production `basis.extraction.pipeline.extract_appraisal` with the
  configured Azure DI client and text model (exactly as the `extract_fields` activity
  builds its context). Records the raw Azure DI response and every model request/response
  under `recordings/<pdf sha256>/`. Costs are summed from the recorder and printed.
* **replay**: no network. Azure DI comes from the recording; the model is a
  `ReplayProvider` that answers each request from `llm/<hash>.json`, the hash taken over
  the request's model, messages, response schema name and effort. A request with no
  recording fails the document with `missing_recording` (it means the prompt or the
  context changed: re-run live). Replay is deterministic and is what the regression tests
  run.
* `--repeats N` (live) for pass^k; `--azure-only` / `--no-azure` systems for ablations.
* `--system NAME` (default `production`) runs one of the sweep's alternative extraction
  systems (`evals/appraisal/systems/`, listed in `evals/README.md`); each adds per-field
  regions, the reader a value came from and, for the grounded model systems, the quote. The
  1004 model's analysis is recorded as `recordings/<sha>/azure_1004.json`.
* Output `runs/<id>/predictions.json`: per document, per key, every stored column
  (`value_text`, `value_num`, `value_date`, `confidence`, `source`, `page`), plus the
  document's `issues`, `review_reasons`, `needs_review`, and the display string (section 8).

## 8. Metrics (`evals/appraisal/score.py`)

Per field (document × key), one **outcome**:

| Outcome | Truth | Prediction |
|---|---|---|
| `correct` | present | value, equal after normalization |
| `wrong` | present | value, not equal |
| `missed` | present | no value |
| `spurious` | absent / not_developed | a value (a `0` or `N/A` for not_developed counts as correct-absent only if the pipeline marks it not developed; today it does not) |
| `correct_absent` | absent / not_developed | no value |

Reported, overall and by tier, type, source (`azure_di`, `llm`, `regex`, `table`,
`manual`), section, set and document:

* **accuracy** = correct / present; **wrong-value rate** = (wrong + spurious) / values
  predicted (the reviewer's risk); **coverage** = predicted / present; spurious rate.
* **page strict** (cited page = `page`) and **page lenient** (in `page` ∪ `also_on`),
  over correct predictions with a page.
* **grounded**: the stored `value_text` occurs (whitespace- and case-folded) in the cited
  page's text layer. Over all predictions with a page.
* **display**: the string the review page shows (today `value_text`, else the formatted
  number or date, mirroring `web/live/live-review.js` `fieldValue`) equals the canonical
  display of the truth: currency `$85,000`; int `1,740`; area `1,740 sq ft` (acres printed:
  `60,548 sq ft (1.39 ac)`); percent `80%`; date `MM/DD/YYYY`; month `MM/YYYY`; bool
  `Yes`/`No`; enum the form's label (`Suburban`); baths `2.1`. Reported, not gated, until the
  review-page fix lands.
* **review gate**: a field is *flagged* at threshold t when `confidence < t` or it is named
  by a review reason or issue. For t in {0.6, 0.7 (UI), 0.9 (gate)}: **error recall** =
  flagged errors / errors (wrong + spurious + missed critical), **review load** = flagged /
  predicted, plus the risk-coverage curve over all thresholds. Document level: did
  `needs_review` fire on every document with a critical-tier error?
* **issues**: expected_issues recall, and issues raised on documents/keys with no expected
  issue (false alarms), per rule.
* **intervals**: Wilson 95 % for proportions at the document level when n documents ≥ 2;
  field-level rates carry a document-clustered standard error (the existing
  `evals.metrics` helper). Repeats: pass^k per field.

`score.json` holds every number; `report.md` renders the tables (no client values in any
table: keys, outcomes and counts only, so a report can be quoted in EVAL_REPORT).

## 9. Regression tests

* `tests/evals/test_appraisal_regression.py` (marker `eval_data`, skipped in CI): replays
  every set and asserts no field moves from `correct`/`correct_absent` to anything else
  against the accepted baseline `BASIS_EVAL_DATA_DIR/appraisal/baseline.json` (keys and
  outcomes only). Improvements are accepted by re-writing the baseline with
  `--accept-baseline`, reviewed like a snapshot.
* `baseline.json` is the **`pipeline`** system's (the reading before EVAL_REPORT section 33;
  `--system pipeline`), which is what the regression test replays. The production default
  became the grounded reading (`EXTRACTION_METHOD=grounded`, `--system production`) in
  section 33.12; its outputs differ from the pipeline's by design, so a `production` replay
  compared with `baseline.json` lists differences, not regressions. Its own baseline is a
  separate file (`--baseline baseline.grounded.json`), accepted only from a replay in which
  no document failed: today only the documents with a recording of the current grounded
  request replay (section 33.12), so it covers those documents until the others are recorded
  live. `baseline.json` is never re-accepted from a `production` run.
* Unit tests (CI) for the normalizers, the scorer, the replay provider and the generator,
  on fabricated values only.
