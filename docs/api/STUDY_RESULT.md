# Study intake and result: basis and land allocation

The purchase price, the capitalized acquisition costs, the land, the use and the dates are not
in the appraisal or the photographs: the taxpayer or their CPA supplies them (the **intake**).
From them Basis computes the depreciable basis and scales the revision's replacement-cost
schedule to it (the **result**). Method and sources: `docs/review_2026-10-15/BASIS_RESEARCH.md`;
arithmetic: `basis/domain/basis.py`; the intake rules: `basis/domain/intake.py`.

```
B = purchase price − land            (building basis)
CC = Σ capitalized acquisition costs
T = B + CC                           (the depreciable basis)
f = T / Σ RCN;  row_i = RCN_i × f;  Σ row_i = T to the cent
```

## The intake

Set at creation (`POST /v1/studies`, optional `intake` member) or later with
`PUT /v1/studies/{id}/intake` (engineer role; the whole document each time; idempotent by
itself, no `Idempotency-Key`). `GET /v1/studies/{id}` returns it as `intake` (null until set).
**A study cannot start without it** (below). The takeoff reads
`study.intake.placed_in_service_date` (the cost year), `.state` (the location cost index) and
`.property_use` (27.5 or 39 years for the building and the classification rules' property
class); `.zip` is recorded for the city cost index, a follow-up.

```json
{
  "purchase_price": "650000.00",
  "acquisition_costs": [
    {"category": "title_insurance_owner", "amount": "1250.00"},
    {"category": "recording_fees", "amount": "180.00", "description": "county recorder"}
  ],
  "land_method": "appraisal_site_value",
  "appraisal_site_value": "90000.00",
  "appraisal_effective_date": "2025-03-14",
  "assessor_land_value": "70000",
  "assessor_improvement_value": "430000",
  "property_use": "residential_rental",
  "dwelling_unit_income_share": "1",
  "transient_lodging": false,
  "placed_in_service_date": "2025-05-01",
  "acquisition_date": "2025-04-15",
  "state": "NY",
  "zip": "12345"
}
```

| Field | Rule |
|---|---|
| `purchase_price` | dollars to the cent, above 0; the contract price from the closing statement |
| `acquisition_costs[]` | required, `[]` when the closing statement has no capitalizable line (so `CC = 0` is a statement, not an omission); `{category, amount, description?}`, at most 100. Capitalized categories (Reg. 1.263(a)-2(f), Pub. 551): `abstract_fees`, `title_search`, `title_insurance_owner`, `legal_fees`, `recording_fees`, `survey`, `transfer_taxes`, `broker_commission`, `appraisal_buyer`, `inspection`, `utility_connection`, `seller_obligations_paid`, `other_facilitative`. Refused with the reason in `errors[].detail`: `points`, `loan_origination`, `mortgage_insurance`, `loan_assumption_fee`, `credit_report`, `appraisal_lender`, `prepaid_interest`, `escrow_taxes`, `escrow_insurance`, `casualty_insurance`, `property_tax_proration`, `prepaid_rent`, `utilities_before_closing`, `refinancing_fees` |
| `land_method` | `appraisal_site_value` (needs `appraisal_site_value`; `appraisal_effective_date` for the date check), `land_appraisal` (needs `land_appraisal_value`; `land_appraisal_date`), `flat_percentage` (needs `flat_land_percentage`, percent of the price, and `flat_land_source`). There is no assessor method and no residual: `assessor_land_value` + `assessor_improvement_value` (both or neither, not both 0) are a check only |
| `property_use` | `residential_rental` (27.5 years) needs `dwelling_unit_income_share` ≥ 0.8 and `transient_lodging: false`; otherwise `nonresidential` (39 years) |
| `placed_in_service_date` | required; when the property was ready and available for its use |
| `acquisition_date` | optional; not after `placed_in_service_date` |
| `state`, `zip` | `^[A-Z]{2}$`, `^\d{5}(-\d{4})?$`; when left out they are filled from the appraisal's `subject.state` / `subject.zip` (newest extraction) and the stored `location_source` says `appraisal`; `intake` when the client gave either |

A violated rule is a `422` problem of type `.../validation` with the rule in `errors[].detail`,
for example `points is not a capitalized acquisition cost: points are prepaid interest, deducted
over the loan's term (Pub. 527); a loan cost, not basis (Pub. 551)`. Nothing is stored on a 422.
`403` for a viewer, `404` for a study not visible, `409 stale-state` for an archived study.

### Required before `start`

`POST /v1/studies/{id}/start` refuses a study whose intake lacks a field the output cannot be
produced without (WORKFLOW_REVIEW 2026-10-29 section 5; `basis.domain.intake.missing_for_start`):
the contract price, the closing costs, the land source with its value, the use, the
placed-in-service date and the state. The answer is `422` of type `.../intake-required` with one
`errors` entry per missing field (`loc` `["intake", <field>]`, `type` `missing`, `detail` saying
what needs it); nothing changes on the study. It is checked before the appraisal (`409
appraisal-required`), so a client without an intake step sees it first.

```json
{
  "type": "https://basis.invalid/problems/intake-required",
  "title": "Unprocessable Content",
  "status": 422,
  "detail": "The study's intake lacks 1 field(s) a start needs: state. Replace it with PUT /v1/studies/{id}/intake (docs/api/STUDY_RESULT.md).",
  "instance": "/v1/studies/5f0c6a52-.../start",
  "errors": [
    {"loc": ["intake", "state"],
     "detail": "the location cost index is by state; the appraisal's subject.state is read only after the study starts, so the intake must carry it (zip is recorded for the city index)",
     "type": "missing"}
  ]
}
```

With no intake at all, `errors` lists all six (`purchase_price`, `acquisition_costs`,
`land_method`, `property_use`, `placed_in_service_date`, `state`). The document's own rules make
the first five present on any stored intake, so on a stored intake the one field that can be
missing is `state`: the appraisal fills it only when an extraction already exists at `PUT` time,
which is after the start, and a `zip` alone does not locate the cost index (the index is by
state; the RSMeans city index by ZIP is a follow-up). Studies started before this rule
(EVAL_REPORT section 37) have no intake and run on the deployment's `PROPERTY_CLASS`, `COST_YEAR`
and `COST_LOCATION_FACTOR` settings, as before.

## The result

`GET /v1/studies/{id}/result[?revision=n]` (viewer role). Computed live from the stored intake
and the revision's assets with their cost estimates; the same document is written into the
export at `export_report` as `summary.basis`, so the stored export is the frozen copy and this
route follows a later intake change.

```json
{
  "revision": 3,
  "intake": true,
  "missing": [],
  "worksheet": {
    "purchase_price": "650000.00",
    "acquisition_costs": [{"category": "recording_fees", "amount": "180.00", "description": null,
                            "authority": "Reg. 1.263(a)-2(f)(2)(ii)(H): title registration; Pub. 551"}],
    "capitalized_costs": "180.00",
    "land": "90000.00",
    "land_method": "appraisal_site_value",
    "land_source": "the appraisal's opinion of site value as of 2025-03-14",
    "building_basis": "560000.00",
    "total_basis": "560180.00",
    "property_use": "residential_rental",
    "recovery_period": "27.5",
    "placed_in_service_date": "2025-05-01",
    "acquisition_date": "2025-04-15",
    "state": "NY", "zip": "12345", "location_source": "intake",
    "checks": [
      {"code": "land_share_of_price", "severity": "ok", "message": "land is 13.8 % of the price (expected 5–60 %)"},
      {"code": "land_hierarchy", "severity": "ok", "message": "appraisal_site_value is the best available source"},
      {"code": "land_vs_appraisal_site_value", "severity": "ok", "message": "land is the site value"},
      {"code": "land_vs_assessor_ratio", "severity": "ok", "message": "the assessor's ratio 14.0 % of the price implies land 91000.00; the chosen land differs by -1.1 %"},
      {"code": "land_value_date", "severity": "ok", "message": "the land value is dated 2025-03-14, 32 days from 2025-04-15 (window 365 days)"},
      {"code": "recovery_period", "severity": "ok", "message": "residential rental property, 27.5 years: 100.0 % of gross rental income from dwelling units, not transient (§168(e)(2)(A))"},
      {"code": "placed_in_service_date", "severity": "ok", "message": "placed in service 2025-05-01 (ready and available for its use; mid-month convention)"}
    ]
  },
  "schedule": {
    "assets": 3, "priced": 2, "rcn_total": "16477.00",
    "by_bucket": [{"bucket": "y5", "assets": 2, "rcn": "16477.00"}, {"bucket": "y27_5", "assets": 1, "rcn": "0.00"}],
    "unpriced": ["…asset id…"], "land": []
  },
  "structure": {"building_rcn": {"amount": "…", "source": "appraisal_cost_approach", "…": "…"},
                "components": ["…"], "concealed": ["…"], "outside_model": "…"},
  "depreciation": {"rate": "0.214300", "method": "appraisal_physical_depreciation", "source": "…", "checks": ["…"]},
  "reconciliation": {"status": "reconciled", "building_basis": "560000.00", "rcnld_total": "…", "gap": "…",
                     "gap_share": "…", "tolerance": "0.10", "implied_uniform_factor": "…", "causes": ["…"], "checks": ["…"]},
  "allocation": {
    "building_basis": "560000.00", "closing_costs": "180.00", "total": "560180.00",
    "rcn_total": "16477.00", "rcnld_total": "…", "depreciated": true,
    "factor": "1.037161514", "rows_sum": "560180.00", "residual_cents": 0,
    "by_bucket": [{"bucket": "y5", "rows": 2, "rcn": "16477.00", "allocated": "560180.00", "percent": "100.0",
                   "share": "1.000000", "building": "560000.00", "closing_costs": "180.00"}],
    "by_system": null,
    "by_system_reason": "2 of 2 rows are not mapped to a building system; the schedule is grouped by bucket only",
    "assets": [{"asset_id": "…", "category": "Carpet", "bucket": "y5", "rcn": "1368.52", "allocated": "46526.52", "rounding_cents": 0}, "…"],
    "checks": [{"code": "uniform_factor", "severity": "error", "message": "the schedule's replacement cost new 16477.00 is below the basis 560180.00: scaling every row up by 33.997693755 is the pro-rata step-up the ATG rejects (ch. 3 D(3)–(5)); the schedule is missing items (see not_produced)"}]
  },
  "not_produced": [{"system": "Foundations", "reason": "concealed building system; priced from areas and a systems cost model; no building-systems cost model yet (STUDY_REPORT_SPEC 5.3); …"}, "…"]
}
```

* `worksheet`: the basis with every check (`ok`, `warning`, `error`, `not_checked`). Checks:
  `land_share_of_price` (5–60 %), `land_hierarchy` (a preferred source was available),
  `land_flat_percentage` (always a warning: the rule-of-thumb approach), `land_vs_appraisal_site_value`
  (within 5 %), `land_vs_assessor_ratio` (within 5 %), `land_value_date` (within 365 days of
  the acquisition), `recovery_period`, `placed_in_service_date`.
* `schedule`: the revision's replacement cost new by bucket, whatever the intake; `unpriced`
  assets have no cost (no quantity or no cost row); `land` assets are not depreciable and
  stay out of the allocation.
* `structure` (the structure model, docs/review_2026-10-15/STRUCTURE_MODEL.md; present when
  the study has an appraisal extraction and `STRUCTURE_STEP=on`): `building_rcn` (the
  building's replacement cost new: `source` `square_foot_model` (the RSMeans model sized by
  the matrix, the default) or `appraisal_cost_approach` (`STRUCTURE_RCN_SOURCE`, or whenever
  no living area sizes a model), with `formula`, `inputs`, `assumptions`, `checks`,
  `needs_review` and the model chosen), `components` (the nine RSMeans components: `share`, `top_down`, `itemized`,
  `residual`, `clamped`, `concealed`), `concealed` (the schedule's concealed rows: label, the
  engineer's row as `system`, bucket, component, RCN) and `outside_model` (priced itemized
  rows the model does not include).
* `depreciation`: the rate on every row (`appraisal_physical_depreciation`, else `age_life`),
  its source and checks (`depreciation_age_life`, `depreciation_uniform`); each priced row's
  `rcnld` is in `allocation.assets` and `schedule.by_bucket`.
* `reconciliation`: `status` `reconciled` (|gap| within `tolerance`, 10 %), `unreconciled`
  or `not_checked`; `building_basis` (B), `rcn_total`, `rcnld_total`, `gap` (B − RCNLD),
  `gap_share`; `implied_uniform_factor` (T / ΣRCN: what the old step-up would have been, a
  diagnostic only); `by_component`, `unassigned_remainders` (Specialties), `outside_model`,
  `unpriced_assets`, `appraisal_check` (the appraiser's own depreciated cost against the price
  less the appraisal's site value); `causes`: the guide's five (ATG ch. 3 D(6)) each with what
  this study's data says; `checks`.
* `allocation`: one factor on every priced row: with `depreciated: true` (the structure path,
  only when the reconciliation is `reconciled`) the factor is `T / ΣRCNLD` and the
  `rcnld_factor` check says so; without structure inputs it is `T / ΣRCN` and the
  `uniform_factor` check is a warning for a write-down (f < 1, ATG ch. 3 A(6)) and an error
  for a step-up (f > 1, ATG ch. 3 D(3)–(5)). `by_bucket` with `percent` (to 0.1), `rcnld` and
  the pro-rata block (`building` + `closing_costs` = `allocated`; the columns sum to B and
  CC); `by_system` (the twelve building systems; every row is mapped by its label, a row with
  no label is Miscellaneous Building Components); `assets` in asset-id order with `rcnld`,
  `label`, `concealed` and the cents the largest-remainder rule moved; `rows_sum` equals
  `total` by construction.
* `not_produced`: the concealed systems no priced structure row answers in this schedule (all
  thirteen when the structure model did not run), so the gap is explicit.
* `missing`: `["intake"]` (no worksheet, no allocation; the schedule is still returned),
  `building_rcn` (no cost approach and no living area), `depreciation` (no rate), or
  `allocation` (nothing priced, or the reconciliation is `unreconciled` or `not_checked`: the
  allocation is withheld rather than scaled, and the diagnosis says what to fix). Nothing is
  fabricated.

All money is a string with two decimals; the factor has nine places and is applied at full
precision; rows round half up and the residual cents go to the largest remainders (the rule is
stated in `basis/domain/basis.py`). The example's allocation block mixes two states for
brevity: a reconciled structure path carries `rcnld` on every row and a factor near 1.

## What the takeoff drew on: `summary.takeoff` in the export

Next to `summary.basis`, the export's summary carries `takeoff`: what the revision's quantities
and prices were drawn from, read back from the stored rows (EVAL_REPORT section 37). Every asset
row already carries its own `takeoff_inputs` (`room_area_source`: `gla_share`,
`basement_share`, `garage_area`, `typical_for_room_type`, `default_150_sf`; `quantity_source:
appraisal_derived` for the roof, siding, gutters and landscaping; `needs_measurement` for a site
item the appraisal cannot size) and its `cost_source` text; `summary.takeoff` is the study-level
view the end-to-end scorer prints.

```json
{
  "cost_year": 2024,
  "location_factor": "1.200",
  "cost_year_source": "placed in service 2024-03-01 (intake)",
  "location_source": "state NY (intake)",
  "property_class": "nonresidential",
  "property_class_source": "intake property_use",
  "area_sources": {"appraisal_derived": 4, "gla_share": 212, "typical_for_room_type": 9},
  "needs_measurement": 15
}
```

* `cost_year`, `location_factor`: the cost rows' values (one per revision; null when the rows
  disagree or none has one).
* `cost_year_source`, `location_source`: in order of preference the intake (`placed in service
  <date> (intake)`, `state <XX> (intake)`), the appraisal (`appraisal effective date <date>
  (<field> (<source>))`, `state <XX> (subject.state (<source>))`), the setting (`COST_YEAR
  setting`, `COST_LOCATION_FACTOR setting`); a candidate that was given but unusable (a year
  outside the 2020–2026 factor table, a state with no factor) is named after a semicolon.
* `property_class`, `property_class_source`: `intake property_use` or `PROPERTY_CLASS setting`.
* The four source members are null on an export whose rows were written before section 37, or
  priced from the settings alone with no intake: nothing is inferred from text.
* `area_sources`: the assets by quantity source; `needs_measurement`: the site items awaiting one.
