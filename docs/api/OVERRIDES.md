# Engineer overrides: Not counted, the unit price, and what the app may offer

The engineer owns every value Basis drafts. Two overrides the app needed and the API lacked
(web/FRONTEND_BACKLOG.md section 14) are built here, each behind a setting and reported by
`GET /v1/capabilities`. Both follow the same rule: the model's or the cost table's value is
kept beside the engineer's, with who, when and why; nothing is deleted. Measured and
argued in docs/review_2026-10-15/EVAL_REPORT.md section 90. Code: `basis/api/routes/capabilities.py`,
`basis/api/routes/objects.py`, `basis/domain/price_overrides.py`, `basis/activities/apply_corrections.py`,
`basis/activities/process_assets.py`, `basis/domain/cost_trail.py`.

## 1. `GET /v1/capabilities`

Any signed-in user, with or without a firm (401 without a sign-in).

```json
{"object_exclusion": true, "unit_cost_override": true, "room_create": false}
```

| Member | Setting | Default | Meaning |
|---|---|---|---|
| `object_exclusion` | `OBJECT_EXCLUSION` | true | section 2 is accepted |
| `unit_cost_override` | `UNIT_COST_OVERRIDE` | true | section 3 is accepted |
| `room_create` | none | always false | the rooms review has no `room_create` correction yet |

Each flag is what its route enforces at that moment: a route whose flag is false refuses
(409 or 422), it never drops the request silently. A client that gets anything but a 200
offers none of them (the app asks once per session).

## 2. Not counted: `PATCH /v1/studies/{study_id}/photos/{document_id}/objects/{object_id}`

Engineer role. Only while the study waits at its edit stage (`components_review`;
`rooms_review` for a study whose workflow has the single rooms review), on the current
revision, once the photo is localized: the same gate as `POST` and `DELETE` on the objects
(docs/api/PHOTOS.md has the photos; `basis/api/routes/objects.py` the gate). No
`Idempotency-Key`: the request sets a state, so a repeat changes nothing (as
`PATCH /v1/orgs/{id}`).

```json
{"counted": false, "reason": "A reflection in the window, not a second window"}
{"counted": true}
{"counted": true, "reason": "It is a second window after all"}
```

* `counted: false` with a `reason` (1 to 500 characters, not blank; stored trimmed) marks a
  **counted** instance Not counted, a model detection or an engineer's own object alike. The
  row stays (a model detection is the photo's evidence): it gets `excluded_at`,
  `excluded_by`, `exclusion_reason` and `counted: false` (migration 0033).
* `counted: true` counts it again: the exclusion is cleared, `counted` is true. The reason is
  optional and goes on the timeline.
* The answer (200) is the object as `GET .../objects` lists it.

Every object in `GET .../objects`, in `GET .../evidence` and in the answers of `POST`,
`DELETE` and `PATCH` carries

```json
"exclusion": {"by": "<user id>", "by_name": "Erin Engineer", "at": "2026-11-05T18:30:00Z",
              "reason": "A reflection in the window, not a second window"}
```

or `null`. `by_name` is the person's display name now (null when they have none).

What an exclusion changes: the instance leaves every count of counted instances, because
each reads `counted`: the per-label `counts` of `GET .../objects`, the rooms'
`suggested_counts` (`GET /v1/studies/{id}/rooms`), the report's photo exhibit (only counted
outlines are drawn) and the duplicate guard of `POST .../objects` (an excluded instance
guards nothing, so a box drawn over it of its own label is promoted from it, `derived_from`,
like any candidate). It does not change a room's inventory row: an inventory count is the
engineer's decision at the components review (a `component` correction's `count`), and the
suggested count beside it now leaves the excluded instance out.

**Changing a detection's type** is this route and an add: mark the model's box Not counted
(reason "Type changed to mirror: ...") and `POST .../objects` a box of the new label at the
same place. There is no relabel endpoint: the model's detection stays as it was found.

| Status | `type` ends with | Code | When |
|---|---|---|---|
| 422 | `validation` | `BASIS-API-013` | `counted` is not a JSON boolean (`errors[].loc` `["body"]`); a missing, blank or over-500-character reason to exclude (`["body", "not_counted", "reason"]`); a reason over 500 to count again (`["body", "counted", "reason"]`); an unknown member |
| 403 | `forbidden` | | a viewer |
| 404 | `not-found` | `BASIS-OBJECT-005` | no such photo in the study (or a study the caller cannot see) |
| 404 | `not-found` | `BASIS-OBJECT-004` | no such object on this photo at the current revision (another photo's, a deleted one, one of an older revision) |
| 409 | `stale-state` | `BASIS-OBJECT-007` | the study is not at its edit stage |
| 409 | `gate-not-open` | `BASIS-OBJECT-008` | at the stage, but the photos are still being prepared |
| 409 | `localization-pending` | `BASIS-OBJECT-001` | the photo's localization is not recorded yet |
| 409 | `study-archived` | | the study is archived |
| 409 | `not-counted` | `BASIS-OBJECT-010` | excluding an uncounted candidate (a model instance under its count threshold): it is in no count |
| 409 | `not-excluded` | `BASIS-OBJECT-011` | counting again a candidate nobody excluded: add it instead (`POST .../objects`, `candidate`) |
| 409 | `object-exclusion-off` | `BASIS-OBJECT-012` | `OBJECT_EXCLUSION=false` |

Repeating the current state (excluding with the same reason, counting a counted object)
answers 200 and records nothing; excluding an excluded object with a new reason replaces the
reason, person and time (the event keeps the old ones).

Timeline and audit (docs/api/TIMELINE_AND_AUDIT.md): `object.excluded` and `object.restored`
events, with `before`/`after` the counted state and the exclusion; the audit listing's
`object_excluded` and `object_restored` items.

**Revisions.** A rejection, a reopen and a restore copy a photo's objects into the new
revision (`PhotoObjectRepository.carry`) with their exclusions, so an exclusion holds in
every later revision until someone counts the object again; the decided revision's rows are
never changed. A photo the same rejection flags for re-analysis (a `photo` correction) is
localized again: its new detections start counted, and its old objects, excluded or added,
stay with the old revision (as an engineer's added objects always have).

## 3. The unit price: `unit_cost` on `asset` and `component` corrections

On `POST /v1/studies/{id}/decisions` (and a gate draft, which is checked the same way), a
`reject` at `components_review` may carry `component` corrections, and at `assets_review`
`asset` corrections, with

```json
{"entity_type": "component", "entity_id": "<component>", "after": {
   "unit_cost": 512.40, "unit_cost_source": "Acme Cabinets quote 2024-118, installed"},
 "reason": "Contractor's quote on file; the table line is a stock cabinet"}
```

* `unit_cost`: a JSON number above 0 and at most 10,000,000, in dollars per **takeoff unit**
  (the asset's `takeoff.unit`: EA, SF, LF, SY) to the cent (at most two decimals: the price
  used is the price entered, never rounded); `null` clears an override.
* `unit_cost_source`: 1 to 300 characters, where the price comes from (a quote, an invoice, a
  guide's line); required with a number, absent or null when clearing.
* `reason`: required, not blank, for setting and for clearing.
* The price may come alone or with the correction's other fields (a `component`'s `count`,
  an `asset`'s `bucket` or measured quantity); a price alone changes no identification (the
  component's label, count and confidence stay) and no classification (the asset is
  classified as an uncorrected one is: a price-only `asset` correction is not a manual
  classification and does not become a firm convention).

Refused with a 422 naming the correction, before anything is written: a bad value
(`correction_value` at `after`), a missing reason (`correction_reason` at `reason`), a price on
a concealed row of the structure model (an asset with no component: its total is a share of
the building's replacement cost, not a unit price; correct the square-foot model instead,
docs/api/REPRICE.md section 6; `correction_value` at `after`), and any price while
`UNIT_COST_OVERRIDE=false`. The workflow's activities check the same rules
(`BASIS-CORRECT-044`, `BASIS-CORRECT-045`).

**Where it lives.** `apply_corrections` stores the price on the component row of the new
revision (`components.raw`: `unit_cost_override` for a component's, `asset_unit_cost_override`
for an asset's, each `{unit_cost, source, reason, by, by_name, at, level, correction_id,
revision}` read from the correction row) and records an `entity.corrected` event on the new
component (`detail.level`, `detail.asset_id` for an asset's). Every later copy of the
component carries it: later rejections at any gate, a reprice, a reopen and a restore at the
components review or after. A clearing correction removes it.

**What prices.** `process_assets` prices a component's asset at the asset's own price if one
is set, else the component's (the asset's wins), else the cost table:

* `cost.unit_cost` is the engineer's price and `cost.total` it times the quantity, rounded
  half up to the cent; no year, location or overhead factor is applied (the price is taken
  as the price at the study's cost year and place);
* `cost.override` is `{unit_cost, source, reason, by, by_name, at, level, correction_id,
  revision}`;
* `cost.trail.kind` is `engineer_price` and `cost.trail.replaced` the cost table's own pricing
  of the same quantity: a `unit_price` with its base row, unit cost and total, or an
  `unpriced` with why (docs/api/COST_INDICES.md, "Per-asset cost trail");
* `cost.source_row` reads "engineer's unit price 512.4 per EA: <source> (set by <name> on
  <date>; reason: <reason>); replaces: <the table's own source_row>";
* the review reasons that describe the table's price (`cost:YEAR_INDEX_MISSING`,
  `cost:YEAR_INDEX_CONVERTED`, `cost:LOCATION_STATE_MISMATCH`, `cross:COST_ROW_MISSING`,
  `cross:COST_NEEDS_QUANTITY`) are not written for that row; the classification's and the
  quantity's are, and so is the range check on the price itself (`cross:COST_ABOVE_RANGE` or
  `BELOW`, a guard against a typo).

**Fail-safe money (EVAL_REPORT 78).** A price is not a quantity: a row whose quantity is a
missing input (`input:<code>`) stays unpriced with its blocker until the quantity is measured,
and its trail carries the waiting `override`. A row the cost table could not price (no row,
or a row in another unit than the takeoff) is priced by the engineer's price and its
`unpriced` blocker goes: an engineer's price with its source is the engineer's evidence, not
a substitute value.

**Everything downstream** reads the cost rows: the structure model's carve-out (an itemized
row inside the model takes its engineer's total), the reconciliation, the allocation, the
result, the export (`assets[].cost_trail`), the client report (Appendix 1 marks the line
"engineer's price: $<price> per <unit>: <source>", section 7 says what the mark means, and the
methodology counts the lines) and the depreciation schedule (each line's basis is its
allocated cost; the CSV and XLSX columns are unchanged, so a study without an engineer's price
exports byte-identical files).

## 4. Shapes against the app's sample API (`web/live/sample-api.js`, PROPOSED)

| Sample | API | Why |
|---|---|---|
| `room_create: true` | `room_create: false` | not built here (section 1) |
| `cost.override.unit_cost` a number | a string with the digits as entered (`"512.4"`) | every money value of the API is a decimal string |
| `cost.override` `{unit_cost, source, reason, by, by_name, at}` | the same plus `level`, `correction_id`, `revision` | additive: which override won, and the correction it came from |
| a price-only override clears the asset's review reasons | only the table's pricing reasons go | the classification and quantity reasons are not the price's to clear |
| `unit_cost` any number > 0 | at most two decimals | the price used is the price entered, to the cent |
| a reason error at `["body", "reason"]` | `["body", "not_counted", "reason"]` | the body is a union by `counted`, so the published schema says the reason is required |
| `counted: false` on a candidate excludes it | 409 `not-counted` | a candidate counts nothing |
| a PATCH needs no key | no key (the same) | a state, not an action |

## 5. What is not built

`room_create`; report narrative edits; "Reset to AI" showing a row's model values on later
revisions (`ai: {...}`); the inventory item showing a component's price before the assets are
built (its `provenance` names the correction); a price on a concealed row (by design).

## 6. Deployment (order matters)

Migration 0031 before either process. Deploy the **worker before the API** (or both at once
with `UNIT_COST_OVERRIDE=false` until the worker is up): a worker without this release
refuses an `asset` or `component` correction carrying `unit_cost` as an unknown field, and
the workflow then starts the next revision without any of the rejection's corrections
(`corrections.refused`). No Temporal patch: the workflow's code and command sequence are
unchanged (the prices are read and written by the activities, whose results are recorded in
history); the recorded histories replay (`tests/lifecycle/test_versioning.py`).
