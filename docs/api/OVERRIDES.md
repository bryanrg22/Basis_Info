# Engineer overrides: Not counted, the unit price, the traced measurement, and what the app may offer

The engineer owns every value Basis drafts. Two overrides the app needed and the API lacked
(web/FRONTEND_BACKLOG.md section 14) are built here, each behind a setting and reported by
`GET /v1/capabilities`. Both follow the same rule: the model's or the cost table's value is
kept beside the engineer's, with who, when and why; nothing is deleted. Measured and
argued in docs/review_2026-10-15/EVAL_REPORT.md section 90. A third, the traced measurement
(section 4), follows in section 97. Code: `basis/api/routes/capabilities.py`,
`basis/api/routes/objects.py`, `basis/domain/price_overrides.py`, `basis/activities/apply_corrections.py`,
`basis/activities/process_assets.py`, `basis/domain/cost_trail.py`, `basis/domain/traced.py`.

## 1. `GET /v1/capabilities`

Any signed-in user, with or without a firm (401 without a sign-in).

```json
{"object_exclusion": true, "unit_cost_override": true, "traced_measurement": true,
 "room_create": false}
```

| Member | Setting | Default | Meaning |
|---|---|---|---|
| `object_exclusion` | `OBJECT_EXCLUSION` | true | section 2 is accepted |
| `unit_cost_override` | `UNIT_COST_OVERRIDE` | true | section 3 is accepted |
| `traced_measurement` | `TRACED_MEASUREMENT` | true | section 4 is accepted |
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

## 4. The traced measurement: `measurement` on `asset` corrections

Today's measured quantity is three typed fields (`quantity`, `quantity_unit`,
`quantity_source`, EVAL_REPORT 78): a number and a sentence about where it came from. An
engineer who sizes a driveway on aerial imagery or a site plan can send the shape itself
instead. The server computes the quantity from it and keeps the shape, the image it was traced
on (what it is, its date, its pixel size), the scale, the method and an expected error, and
flags what a reviewer should look at. The IRS guide wants take-offs "carefully documented"
(Publication 5653, ch. 4 C.8); the research and the error figures are
docs/review_2026-10-15/REMOTE_INSPECTION_AND_MEASUREMENT.md sections 2, 5 and 7.2; the method
was chosen in EVAL_REPORT 97. A typed number keeps working exactly as before.

On `POST /v1/studies/{id}/decisions` (a `reject` at `assets_review`) and on a gate draft
(checked the same way):

```json
{"entity_type": "asset", "entity_id": "<asset>", "after": {"measurement": {
   "unit": "SF",
   "space": "image",
   "shapes": [{"kind": "area", "ring": [[120, 80], [940, 80], [940, 410], [120, 410]],
               "cutouts": [[[300, 200], [380, 200], [380, 260], [300, 260]]]}],
   "scale": {"points": [[120, 450], [460, 450]], "length": 34.0, "unit": "ft"},
   "image": {"source": "Site plan sheet C1 (Smith Surveying, 2019), uploaded by the owner",
             "document_id": "<a document of this study>", "captured_on": "2019-08-02"}}}}
```

```json
{"entity_type": "asset", "entity_id": "<asset>", "after": {"measurement": {
   "unit": "LF",
   "space": "lonlat",
   "shapes": [{"kind": "line", "path": [[-88.90012, 43.80031], [-88.89961, 43.80031],
                                        [-88.89961, 43.80072]]}],
   "image": {"source": "Wisconsin statewide orthoimagery, 6 inch, county viewer",
             "captured_on": "2020-04-18", "pixel_size_m": 0.1524}}},
 "reason": "Fence traced on the 2020 county flight"}
```

**The body.** Every key below; any other key, anywhere in the object, is refused.

| Member | Meaning |
|---|---|
| `unit` | `SF`, `SY` or `LF`. `EA` is counted, not traced |
| `space` | `lonlat`: WGS84 `[longitude, latitude]` in degrees (an aerial layer). `image`: `[x, y]` pixels of an uploaded image or plan, with `scale` |
| `shapes` | 1 to 50 of `{"kind": "area", "ring": [...], "cutouts": [[...], ...]}` or `{"kind": "line", "path": [...]}`, not mixed. A ring is 3 to 500 points with no closing point needed (a repeated first point at the end is dropped); a path 2 to 500 points; up to 20 cutouts per ring; at most 5,000 points in the whole measurement |
| `scale` | `{"points": [[x1, y1], [x2, y2]], "length": 34.0, "unit": "ft" or "m"}`: two pixels the engineer clicked and the real length between them. Required with `image`, refused with `lonlat` |
| `image.source` | 1 to 300 characters: what the image is and where it came from (stored trimmed). Required |
| `image.document_id` | optional: a document of this study (the uploaded plan) |
| `image.captured_on` | optional `YYYY-MM-DD`: when the image was flown or drawn |
| `image.pixel_size_m` | optional, `lonlat` only: metres per pixel on the ground |
| `height_ft` | optional: only with `SF` or `SY` and line shapes, a wall face (length x height) |

`SF` and `SY` take area shapes (the rings' areas less their cutouts) or line shapes with
`height_ft`; `LF` takes line shapes only. The correction may carry the measurement alone or
with a classification (`category`, `bucket`, `recovery_period`, `irs_section`) or a unit
price (section 3), as the typed quantity can; never with `quantity`, `quantity_unit` or
`quantity_source` (one way to state a quantity). A `reason` follows the rule of the fields it
comes with: a measurement alone needs none, as a typed quantity needs none; with a unit price,
the price's reason is required.

**How the quantity is computed** (`basis/domain/traced.py`). Image space is planar: the area
or length in pixels times the scale (feet per pixel = the scale's length over the distance
between its two points). Lon/lat space uses a local tangent plane on the WGS84 ellipsoid (the
meridional and prime-vertical radii of curvature at the shape's mean latitude): it matched
the ellipsoid's exact area to better than 0.0000002 % on every test shape from 100 SF to 10
acres at 25 to 49 degrees north, where screen pixels of a web map with no latitude correction
are 22 to 132 % too large and a mean-radius sphere (what Turf.js and OpenLayers do) 0.01 to
0.38 % off (EVAL_REPORT 97). The quantity is rounded half up to 0.01, SY is SF over 9, and it
must be above 0 and at most 10,000,000.

**The expected error**, stored with the measurement as a percentage bound: one pixel of
uncertainty on every edge. An area is off by at most perimeter x pixel size / area (the
cutouts' perimeters included), a line by 2 x pixel size / length per line. In image space the
pixel is one pixel at the scale, plus the scale's own uncertainty: 2 pixels over the scale's
distance in pixels, counted twice for an area. In lon/lat space the pixel is
`image.pixel_size_m`; without it the error is unknown (`null`). A wall's height is typed, so
its uncertainty is not counted.

**Refused** with a 422 naming the correction, before anything is written, every problem in
plain words with where it is (for example `measurement.shapes[0].ring crosses or touches
itself (two of its edges meet)`):

| `errors[].type` at `["body", "corrections", <i>, "after"]` | When |
|---|---|
| `correction_value` | an unknown key; a unit other than SF, SY, LF; a space other than lonlat, image; a number that is not a finite JSON number (a boolean is not one); a longitude outside -180 to 180 or a latitude outside -85 to 85; a pixel outside 0 to 100,000; `length` or `height_ft` not above 0 or above 100,000; `pixel_size_m` not above 0, above 100, or in image space; too few or too many shapes, points or cutouts; a ring that crosses or touches itself, folds back, repeats a corner or has no area; a cutout with a corner outside its ring; a shape with no area left once its cutouts are out; the scale's points under 2 pixels apart, or the scale missing in image space or given in lon/lat; a missing, blank or over-300-character `source`; a `document_id` that is not a UUID; a `captured_on` that is not a `YYYY-MM-DD` date; `height_ft` where it does not belong or missing where it does; area and line shapes mixed; a quantity that rounds to 0 or exceeds 10,000,000; the typed quantity's fields beside it; `measurement` on a correction that is not an `asset` one; any measurement while `TRACED_MEASUREMENT=false` |
| `correction_target` | `image.document_id` is not a document of this study (checked after the targets) |

Strings with NUL, numbers that are NaN or infinite, an object nested more than 8 levels and
an object under any field other than `measurement` are refused by the request schema itself
(422 `BASIS-API-013`). The workflow's activities check the same rules (`BASIS-CORRECT-046`),
so a draft, a decision and the worker agree.

**What it changes.** `process_assets` replaces the asset's takeoff exactly as a typed
quantity does: the missing-input blocker it supplies is lifted, the takeoff's `quantity` and
`unit` are the computed ones, its `formula` reads, for example, "5,844.12 SF traced by the
engineer on Wisconsin statewide orthoimagery (captured 2020-04-18), 1 area of 15 corners;
expected error up to 3.40 %", and its `inputs` (`takeoff_inputs` in the export, `quantity.inputs`
in the cost trail) carry `quantity_source: "engineer"`, `measured_by_engineer` (the image's
source text), `computed_quantity` (what the takeoff had computed, or null when that was a
missing input) and `measurement`: the body as stored (closing points dropped, text trimmed, a
document id normalized) plus `quantity`, `area_sf` or `length_ft`, `method`
(`local_tangent_plane_wgs84` or `image_scale`), `expected_error_pct` (a string, or null),
`shape_count`, `corner_count`, `cutout_count` and, in image space, `scale_ft_per_px`. The row
is then priced as any other (by the cost table, or by the engineer's unit price).

The asset is rebuilt from the correction as any asset correction is (`method: manual`, the
corrected asset's classification unless the correction changes it), exactly as for a typed
quantity: so at approval it becomes a firm convention like every corrected asset
(REVIEW_RULES.md 7.4). A unit price alone is the only correction that is not.

**Review flags** (written on the asset's `review_reasons`; never a refusal, never a change of
the quantity):

| Reason | When | Setting | Code |
|---|---|---|---|
| `measure:IMAGE_UNDATED` | no `captured_on`: undated imagery cannot show the property as placed in service | none | BASIS-ASSETS-021 |
| `measure:IMAGE_AFTER_SERVICE` | `captured_on` is more than this many days after the intake's placed-in-service date: the image may show work done after the purchase | `MEASURE_IMAGE_AFTER_SERVICE_DAYS`, 365 | BASIS-ASSETS-022 |
| `measure:ERROR_UNKNOWN` | lon/lat with no `pixel_size_m` | none | BASIS-ASSETS-023 |
| `measure:COARSE` | the expected error is above this percentage | `MEASURE_ERROR_FLAG_PCT`, 10 (the worst case independent studies report for Google Earth on a driveway-sized area, REMOTE_INSPECTION_AND_MEASUREMENT.md section 5) | BASIS-ASSETS-024 |

**Not done** (open, the maintainer's): the measurement is not kept on the component row the
way a unit price is, so, like a typed quantity, it applies to the revision the rejection
builds and a later assets rejection that does not send it again recomputes the takeoff
(EVAL_REPORT 97 "Surprises"); a measurement on a concealed row of the structure model is
accepted and, like a typed quantity there, does not change the row's quantity; areas that
overlap each other and cutouts that overlap each other are not detected; nothing compares the
traced total with the lot; no exhibit (the image with the shape) is stored or printed; the
client report does not print the source.

## 5. Shapes against the app's sample API (`web/live/sample-api.js`, PROPOSED)

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

## 6. What is not built

`room_create`; report narrative edits; "Reset to AI" showing a row's model values on later
revisions (`ai: {...}`); the inventory item showing a component's price before the assets are
built (its `provenance` names the correction); a price on a concealed row (by design).

## 7. Deployment (order matters)

Migration 0031 before either process. Deploy the **worker before the API** (or both at once
with `UNIT_COST_OVERRIDE=false` and `TRACED_MEASUREMENT=false` until the worker is up): a worker without this release
refuses an `asset` or `component` correction carrying `unit_cost` (or an `asset` correction
carrying `measurement`: section 4 needs no migration) as an unknown field, and
the workflow then starts the next revision without any of the rejection's corrections
(`corrections.refused`). No Temporal patch: the workflow's code and command sequence are
unchanged (the prices are read and written by the activities, whose results are recorded in
history); the recorded histories replay (`tests/lifecycle/test_versioning.py`).

## 7. A row's age and its removal by a renovation (EVAL_REPORT 102)

Two more fields of an `asset` correction at the assets review (a `reject` on
`POST /v1/studies/{id}/decisions`, or a gate draft, checked the same way). Neither is a
classification: a correction carrying only these fields leaves the asset to the classifier, as a
price-only one does. Design: docs/review_2026-10-15/RENOVATIONS.md sections 4.1, 4.4 and 4.6.

**The row's age** (the seller's renovation before the sale):

```json
{"entity_type": "asset", "entity_id": "<asset>", "after": {
   "effective_age": 2, "economic_life": 20, "age_source": "appraisal_update_status",
   "age_source_detail": "Kitchen: Fully Updated, 1-5 years (appraisal p.10)"},
 "reason": "The kitchen was redone before the sale; the photographs agree"}
```

* exactly one of `effective_age` (years, a JSON number from 0 to below 200, at most two decimals)
  and `year_installed` (a whole year, 1800-2200; the age is then the years to the acquisition's
  placed-in-service year);
* `economic_life`: years, above 0 and at most 200, above the age (the age-life ratio below 1);
* `age_source`: `engineer_inspection`, `appraisal_update_status`, `aerial_history`, `permit`,
  `invoice` or `taxpayer_statement`; `age_source_detail`: 1 to 300 characters;
* `{"effective_age": null}` alone clears it; the reason is required either way.

The row's physical depreciation becomes `age / life` (six places) instead of the appraiser's
rate; the result lists it in `component_ages` and the report in section 8 ("Components
depreciated on their own age"). A year installed after the placed-in-service year, or an age that
reaches the life, withholds the result (blocker `component_age`).

**The row's removal by a renovation** (the buyer's renovation after service):

```json
{"entity_type": "asset", "entity_id": "<asset>", "after": {
   "removed_by_project": "kitchen-2025", "disposition_date": "2025-03-03", "removed_share": 1},
 "reason": "Demolition photographs of 2025-03-03 and the contractor's scope"}
```

* `removed_by_project`: a project key of an `after_service` project of the study's renovations
  (docs/api/RENOVATIONS.md); `disposition_date`: an ISO date; `removed_share`: above 0 and at
  most 1 (default 1, the whole line);
* `{"removed_by_project": null}` alone clears it; the reason (what shows the removal: the
  Practice Unit's "Relying on capitalization does not substantiate that a disposition occurred")
  is required either way.

The line stays in the acquisition; the result's `renovations.dispositions` takes it out at its
date with its depreciation allowed or allowable (docs/api/STUDY_RESULT.md, "Renovations").

| Status | `errors[].type` | Where | When |
|---|---|---|---|
| 422 | `correction_value` | `after` | a bad age or removal value, a key the renovations do not list as an after-service project |
| 422 | `correction_reason` | `reason` | no reason |

The workflow's activities check the same (`BASIS-CORRECT-047` for an age, `BASIS-CORRECT-048`
for a removal). **Where they live:** on the new row's takeoff (`takeoffs.inputs["component_age"]`,
`["removal"]`, each `{..., reason, from_asset_id, revision}`), carried into every later revision
by `basis/activities/line_annotations.py`: from the latest earlier revision with assets, a
component row by its component's `carried_from` chain, a concealed row of the structure model by
its label (an age or a removal may be set on a concealed row, unlike a unit price).

Deployment: no migration for these fields; the worker before the API (an older worker refuses the
fields as unknown, and the workflow then starts the revision without the rejection's
corrections).
