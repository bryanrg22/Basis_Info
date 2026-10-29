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
The takeoff reads `study.intake.placed_in_service_date`, `.state` and `.zip`.

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
| `acquisition_costs[]` | `{category, amount, description?}`, at most 100. Capitalized categories (Reg. 1.263(a)-2(f), Pub. 551): `abstract_fees`, `title_search`, `title_insurance_owner`, `legal_fees`, `recording_fees`, `survey`, `transfer_taxes`, `broker_commission`, `appraisal_buyer`, `inspection`, `utility_connection`, `seller_obligations_paid`, `other_facilitative`. Refused with the reason in `errors[].detail`: `points`, `loan_origination`, `mortgage_insurance`, `loan_assumption_fee`, `credit_report`, `appraisal_lender`, `prepaid_interest`, `escrow_taxes`, `escrow_insurance`, `casualty_insurance`, `property_tax_proration`, `prepaid_rent`, `utilities_before_closing`, `refinancing_fees` |
| `land_method` | `appraisal_site_value` (needs `appraisal_site_value`; `appraisal_effective_date` for the date check), `land_appraisal` (needs `land_appraisal_value`; `land_appraisal_date`), `flat_percentage` (needs `flat_land_percentage`, percent of the price, and `flat_land_source`). There is no assessor method and no residual: `assessor_land_value` + `assessor_improvement_value` (both or neither, not both 0) are a check only |
| `property_use` | `residential_rental` (27.5 years) needs `dwelling_unit_income_share` ≥ 0.8 and `transient_lodging: false`; otherwise `nonresidential` (39 years) |
| `placed_in_service_date` | required; when the property was ready and available for its use |
| `acquisition_date` | optional; not after `placed_in_service_date` |
| `state`, `zip` | `^[A-Z]{2}$`, `^\d{5}(-\d{4})?$`; when left out they are filled from the appraisal's `subject.state` / `subject.zip` (newest extraction) and the stored `location_source` says `appraisal`; `intake` when the client gave either |

A violated rule is a `422` problem of type `.../validation` with the rule in `errors[].detail`,
for example `points is not a capitalized acquisition cost: points are prepaid interest, deducted
over the loan's term (Pub. 527); a loan cost, not basis (Pub. 551)`. Nothing is stored on a 422.
`403` for a viewer, `404` for a study not visible, `409 stale-state` for an archived study.

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
  "allocation": {
    "building_basis": "560000.00", "closing_costs": "180.00", "total": "560180.00",
    "rcn_total": "16477.00", "factor": "33.997693755", "rows_sum": "560180.00", "residual_cents": 0,
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
* `allocation`: one factor on every priced row; `by_bucket` with `percent` (to 0.1) and the
  pro-rata block (`building` + `closing_costs` = `allocated`; the columns sum to B and CC);
  `by_system` (the twelve building systems) only when every row's category is a report row
  of the taxonomy, else `by_system_reason`; `assets` in asset-id order with the cents the
  largest-remainder rule moved; `rows_sum` equals `total` by construction. The
  `uniform_factor` check is a warning for a write-down (f < 1: one factor instead of per-item
  depreciation, ATG ch. 3 A(6)) and an error for a step-up (f > 1: the schedule is incomplete,
  ATG ch. 3 D(3)–(5)).
* `not_produced`: the systems the pipeline cannot cost today (the concealed building systems
  and what needs a site plan), so the gap between `schedule.rcn_total` and `total` is explicit.
* `missing`: `["intake"]` (no worksheet, no allocation; the schedule is still returned) or
  `["allocation"]` (an intake but nothing priced). Nothing is fabricated.

All money is a string with two decimals; the factor has nine places and is applied at full
precision; rows round half up and the residual cents go to the largest remainders (the rule is
stated in `basis/domain/basis.py`).
