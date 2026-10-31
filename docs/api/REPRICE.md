# Rebuilding the assets: the reprice

How an engineer applies corrected inputs (a new cost index value, a changed intake, the
structure model's class, type or living area) to a priced revision, and why nothing
applies them silently. Code: `basis/costs/reprice.py`, `basis/evidence/pricing.py`,
`basis/activities/reprice_corrections.py`, `basis/activities/process_assets.py`.

## 1. What never happens

A priced revision's dollars do not change. Each cost row records how it was priced
(`cost_estimates.trail`, docs/api/COST_INDICES.md "Per-asset cost trail"), and the rows
cannot be updated (migration 0018). Entering an index value (`POST /v1/cost-indices`) or
changing the intake (`PUT /v1/studies/{id}/intake`) leaves every priced revision as it was.

The result (`GET /v1/studies/{id}/result`, and the export's `summary.basis`) computes the
building's replacement cost, the carve-out and the reconciliation with the year factor,
location factor and square-foot model **recorded on the revision's rows**, so the top-down
figure and the rows it is compared with rest on the same index. Before this change the
result recomputed the building with the store's current values while the rows kept the old
ones; a fresh index changed the reconciliation's figures without ever being applied to the
rows.

## 2. `pricing` on the result

```
"pricing": {
  "priced_with": {recorded, cost_year, year_factor, year_citation, year_store_version,
                  location_factor, location_citation, intake_version, property_class,
                  model_class, building_type, living_area_sf},
  "current":     {... what a rebuild would use now ...},
  "stale": true,
  "reasons": ["index_updated"],
  "remedy": "reprice"
}
```

* `reasons`: `intake_changed` (the intake version moved since the pricing),
  `cost_year_changed`, `index_updated` (the cost year's index value, factor or store version
  differs), `location_changed`, `property_class_changed`, and `not_recorded` (priced before
  the trail existed; not stale by itself: its factors are unknown and are not reconstructed
  from rounded amounts, so such a revision keeps the old behaviour and says so).
* `remedy`: `reprice`, or `reject_assets` when the property class changed (a 27.5-year
  classification cannot be carried into a 39-year building: the assets are classified again
  by rejecting the assets review).
* A stale revision's allocation is **withheld** (`missing` lists `repricing`): the
  reconciliation was computed on dollars the current inputs no longer give. Withholding when
  the reconciliation fails is unchanged.

## 3. The preview: `GET /v1/studies/{id}/assets/reprice`

Engineer role. Optional `revision` (default: current), and `model_class`, `building_type`,
`living_area_sf` to preview a square-foot model set by the engineer.

It runs the rebuild's own code (the carry of the revision into the next, then
`process_assets` with the `reprice` correction) inside a SAVEPOINT that is rolled back:
nothing is written, no event is recorded, and no model is called (no provider is wired; a
component with nothing to carry, normally none, is counted in `reclassified`). Response:
`fingerprint`, `allowed`/`blocked`/`blocked_code`, `pricing`, per-asset `before`/`after`
(quantity, unit, unit cost, total) and `change`, the reconciliation `before` and `after`
(status, RCN, RCNLD, building basis, gap, allocated, missing).

## 4. The rebuild: `POST /v1/studies/{id}/assets/reprice`

Engineer role, `Idempotency-Key`. Body:

```json
{"revision": 3, "fingerprint": "<from the preview>", "reason": "July 2024 HCI entered",
 "structure_model": {"model_class": "custom"}}
```

* 409 `reprice-inputs-changed` (BASIS-API-040): the fingerprint is not the current one (the
  intake, the store, the square-foot model or a pricing setting changed since the preview).
* 409 `reprice-reclassify` (BASIS-API-041): the property class changed.
* 409 `gate-not-open` (BASIS-API-042), or the decision route's stale refusals: the study is
  not waiting at the assets review of that revision.
* 422: a blank reason, an unknown member, a class or type outside the RSMeans vocabulary.

202 records, in one transaction: an assets-review **rejection** (the review row, comment =
reason), the `reprice` correction (`entity_id` = the study, `after` = `{fingerprint}`), the
`structure_model` correction when given, the `assets.reprice_requested` event (before = the
recorded pricing, after = the current one, the reasons, the fingerprint, the correction
ids) and the outbox signal. The workflow handles it as any assets rejection:
`apply_corrections` carries the revision, `process_assets` rebuilds it, the assets review
opens on the new revision. Status: the study's stage and the timeline.

The decisions route refuses both types (422 at `entity_type`): a rebuild always follows a
preview.

## 5. What `process_assets` does with a `reprice` correction

Every component carried from an asset of the rejected revision (`carried_from`) keeps that
asset's classification: category, bucket, recovery period, section, method (a manual one
stays manual), confidence, convention and its verified citations as verified. No model
call. The review reasons the pricing writes (`cross:`, `cost:`, `takeoff:`, `structure:`)
are dropped and written again by the new pricing; the classification's own stay. The
takeoff records `carried_classification` (`from_asset_id`, `from_revision`, `method`). The
concealed rows are matched by label the same way. `asset` corrections in the same rejection
still apply. Quantities, prices, the structure model and the trail are computed with the
current intake and index store.

If the property class changed between the request and the rebuild (the intake changed in
between), nothing is carried: every asset is classified again and
`assets.reprice_reclassified` (BASIS-ASSETS-020) says why.

## 6. The structure model correction

The square-foot model's class of construction comes from the appraisal's quality rating,
its type from the stories and its size from the GLA. The engineer's judgment that a Q3
house is RSMeans "average" rather than "custom" is not an appraisal fact, so it cannot be
entered as an `extraction_field` correction without misstating the appraisal. A
`structure_model` correction (`model_class`, `building_type`, `living_area_sf`) sets it;
the concealed rows' takeoff inputs (and trail) record it with its source, the class and type
sources read "confirmed by" or "set by", and the result reads it back for that revision. The
room takeoffs keep the appraisal's GLA.

## 7. Deployment (order matters)

Deploy the **worker before the API** (or both at once, with the API's reprice route unused
until the worker is up). A worker without this release has the old correction-type set: its
update validator refuses a `reprice` decision, the relay treats a validator refusal as final,
and the study's row would say `assets_processing` at the new revision while the workflow
still waits at the old assets review. No Temporal patch is needed with the new worker: the
workflow's path is the existing assets rejection, and the correction-type set is read by the
update validator, which does not run on replay. Migration 0018 must be applied before
either process starts (the worker writes `cost_estimates.trail`, the API reads
`studies.intake_version`).
