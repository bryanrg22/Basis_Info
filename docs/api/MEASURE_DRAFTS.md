# AI-drafted measurements: what an asset carries, how the engineer verifies, what the report waits for

Step 6 of `docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md` (EVAL_REPORT 110). Basis drafts the
quantities its measured methods beat today's formulas on, prices each at once as a **draft**,
flags it for review, and withholds the report while an unverified draft could move more dollars
than `MEASURE_DRAFT_VERIFY_USD` (the maintainer's pricing rule (a), plan section 8.3). Both
drafts are off by default; with them off every row is what it was (byte for byte, tested).

| Setting | Default | What it drafts | Measured in |
|---|---|---|---|
| `MEASURE_DRAFTS_EXTERIOR` | false | roof, siding and gutter rows from the building shell a gather stored (`GEO_SHELL`, docs/api/PUBLIC_GEO.md) around the **confirmed** pin | EVAL_REPORT 105 (wall face 32.5 % -> 6.4 % mean absolute error, gutter 49 % -> 6 %) |
| `MEASURE_DRAFTS_INTERIOR` | false | floor, wall, trim, countertop and cabinet-run rows of each kitchen, bedroom, bathroom and living room with photos (one vision call a room) | EVAL_REPORT 107 (wall area 25 -> 12 %, countertop 313 -> 21 %, cabinet run 270 -> 29 %) |
| `MEASURE_INTERIOR_MODEL`, `_REASONING_EFFORT`, `_MAX_TOKENS` | `claude-sonnet-5-5`, `low`, 1200 | the room call's model (section 107's pick; prompt `measure_interior_reference/v1`) | EVAL_REPORT 107.7 |
| `MEASURE_DRAFT_VERIFY_USD` | 250 | the exposure above which an unverified draft holds the report | EVAL_REPORT 110.4 |

`MEASURE_DRAFTS_INTERIOR` must be set on the API (it starts the workflow and puts the flag on its
input, patch `measure-drafts-v1`) and on the worker (the activity honours its own setting).

## 1. On the asset: `GET .../assets` (`takeoff.inputs`)

A drafted row's takeoff carries:

| `takeoff.inputs.` | Meaning |
|---|---|
| `quantity_source` | `draft` (today's rows say `appraisal_derived` or name the room area's source) |
| `drawn_by` | `model:lidar-shell@v1` (exterior) or `model:<model>@measure_interior_reference-v1` (interior). Operators' to see: the formula and every customer-facing sentence say "drafted by Basis" |
| `draft` | the draft's record (below) |
| `measurement` | **exterior only**: the shape as a measurement record (docs/api/OVERRIDES.md section 4, the same record an engineer's trace makes): the walls as one closed line with `height_ft` the main eave (siding), the roof's outline as an area (roof), the eave edges as lines (gutter), in `lonlat`, `drawn_by` as above, `verified: false`, `image.source` naming the lidar project and the gather, `image.pixel_size_m` 0.5 (the lidar grid; no capture date, so `measure:IMAGE_UNDATED`). The measure tool draws it dashed with its edges and a Verified box (EVAL_REPORT 104) |
| `draft_factor`, `shape_quantity` | the factor applied to the shape (siding 0.85: the 15 % openings share; roof: the lidar planes' slope factor, `roof_area / roof_plan`), and the shape's own quantity. The row's quantity is the shape's times the factor |
| `replaced_formula`, `replaced_quantity` / `replaced_missing_input` | what today's formulas wrote (or the missing input that held the row: an interior countertop's `cabinet_run`) |
| `draft_expected_error_pct` | the method's measured mean absolute error for that quantity |
| `draft_not_used` (+ `draft_not_used_code` `BASIS-MEASURE-004`) | on a roof, siding or gutter row with `MEASURE_DRAFTS_EXTERIOR` on and no draft: why (no pin, the pin not confirmed or moved since the gather, no shell stored, a part the shell refused, a shell stored before the outline was kept) |

`takeoff.inputs.draft`:

```json
{"drawn_by": "model:claude-sonnet-5-5@measure_interior_reference-v1",
 "verified": false,
 "quantity": "42.00", "unit": "SF",
 "expected_error_pct": "20.8", "error_source": "EVAL_REPORT 107.4: countertop, 3 kitchens",
 "source": "3 photos of the room",
 "replaces": {"missing_input": "cabinet_run"},
 "photos": ["<document id>", "..."],
 "answer": {"floor_area_sf": "235.0", "countertop_sf": "42.0", "...": "..."},
 "notes": "...", "confidence": "low", "room_fully_seen": false,
 "exposure_usd": "229.67", "verify_above_usd": "250", "blocks": false}
```

`replaces` is today's `{formula, quantity}` or `{missing_input}`; an exterior record adds
`factor` and `factor_reason`. `exposure_usd` is the expected error times the row's priced total
(`null` when the cost table cannot price the row: it moves no dollars, and its own `unpriced`
blocker holds the report); `blocks` is true while it is pending and above `verify_above_usd`
(the threshold when it was priced: a new threshold applies at the next pricing).

The row's review reasons carry `measure:DRAFT_UNVERIFIED` (`BASIS-MEASURE-001`) while the draft is
pending, so it needs review whatever its exposure.

## 2. In the result: `GET .../result`

While any row's draft `blocks`, `blockers` has one entry:

```json
{"code": "measure:draft_unverified",
 "message": "3 measurements drafted by Basis are not verified and could each move more than $250.00 (its expected error times its price)",
 "assets": 3,
 "fix": "verify or edit this measurement in the measure tool"}
```

and, as for every blocker, the allocation is withheld, so no report is rendered or signed
(docs/api/STUDY_RESULT.md). It is not recorded for the operators (`client_errors`): verifying is
the engineer's ordinary review step, not a missing input.

## 3. Verifying, editing, replacing: the existing `asset` correction at the assets review

| The engineer sends | The row becomes | `takeoff.inputs.draft` |
|---|---|---|
| the stored `measurement` back with `verified: true` (an untouched draft; the measure tool's "Verified") | the same quantity (shape x factor), `quantity_source: engineer` | `verified: true`; no `blocks` |
| an edit: `measurement` with `drawn_by: engineer` and the draft as its `original` | the edited shape x the draft's factor (the engineer checked the outline; the slope and the openings stay the draft's) | `replaced_by: engineer`, `engineer_quantity`; the draft's own numbers unchanged; the measurement keeps the draft as `original` |
| a typed `quantity`, `quantity_unit`, `quantity_source` | the typed quantity | `replaced_by: engineer`; an exterior draft's shape kept as `draft.measurement` |
| the stored measurement back with `verified: false` | still the draft | unchanged (still pending) |

A row with a quantity correction is now processed first among the rows of its kind, so a quantity
measured once per property or room lands on the corrected row (EVAL_REPORT 110.6: the rows' order
is by id, new each revision; before, the corrected row could come second, get 0, and the other row
price the draft again). This ordering applies with either draft setting on; with both off the order
is today's (which can double count a corrected multi-row quantity: reported in EVAL_REPORT 110.6).

An interior draft has no shape (the model answers numbers): it is verified by a typed quantity or
by the engineer's own trace on the room's photo.

The factor applied to an engineer's measurement is the factor of the draft planned for that row in
the new revision. When none is planned (the setting turned off since, or the shell no longer drafts
that part), the measurement is taken as it is, as for any trace (docs/api/OVERRIDES.md section 4):
a verified roof outline then prices its plan area, not its surface. A fresh trace with no draft as
its `original` is likewise taken as it is.

## 4. The room measurements step (`measure_rooms`, patch `measure-drafts-v1`)

Runs once per execution, before its first `process_assets`, on the rooms of a measured type with
photos; each room one call (one more on an unusable answer), every call in `llm_calls` about its
room, the study's budget checked for the worst case first (`BASIS-MEASURE-005`). A refused or
failed room (`BASIS-MEASURE-002`) keeps today's rows; a failed step (`BASIS-MEASURE-003`) flags the
assets review and every room keeps today's rows. Later revisions (an assets rejection, a reprice)
use the same drafts, followed along `rooms.carried_from`, while the room has exactly the photos
measured and its type; a reopened execution measures again on its first assets run.

## 5. What the app shows and lacks

See `web/FRONTEND_BACKLOG.md` (section 23).

## Part two. Draft it: `POST /v1/studies/{id}/property/site-drafts`

The measure tool's "Draft it" (EVAL_REPORT 111; `docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md`
step 6). The engineer drags a box over a driveway, patio, deck, pool, walk or parking area on a
top-down view of the property and asks Basis to draft its outline. The route runs SAM 3 on the
whole view with the box alone, no phrase (`basis.vision.site_items`, the setup EVAL_REPORT 106.3
measured best: a median area error of 13.6 % on the county's 3-inch ortho, driveways 11.8 %), and
answers with the draft as a measurement record, or a coded refusal in plain words. Nothing is stored
on the study but the paid call and an event: the draft becomes the study's only when the engineer
sends it as an `asset` correction (docs/api/OVERRIDES.md section 4), as it is or edited, verified
or not. Off by default (`SITE_DRAFTS=off`); `GET /v1/capabilities` says `site_drafts` (true only
with `TRACED_MEASUREMENT` on too).

### 6. The request

Engineers (a viewer is 403, a stranger 404), with an `Idempotency-Key` (every `POST`):

```json
{"asset_id": "<an asset of the study's current revision>",
 "view": {"kind": "capture", "id": "<a view of GET .../property>"},
 "box": {"x1": 98.5, "y1": 375.1, "x2": 336.9, "y2": 477.3},
 "family": "driveway",
 "unit": "SF"}
```

| Member | Meaning |
|---|---|
| `asset_id` | The asset the draft is for (the paid call is recorded against it). |
| `view` | `kind` `capture` (a view of `GET .../property`: the close aerial or a dated one) or `public` (a view of `GET .../property/public`), and its `id`. |
| `box` | The box in the view's own pixels (x right, y down), 0 to 100,000, at least 4 pixels a side, left to right and top to bottom, inside the view's frame (422 otherwise). It is sent to SAM 3 as it is, normalized to the view. |
| `family` | `driveway`, `parking`, `walk`, `patio`, `pool_deck`, `pool`, `deck` or `fence`. A fence is answered with the refusal `BASIS-SITE-008` and no call (EVAL_REPORT 106.6). |
| `unit` | `SF` (default) or `SY`: the unit of the asset the draft is for; the same outline is measured in it. |

### 7. The answer

200, one of:

```json
{"status": "drafted", "family": "driveway", "unit": "SF", "quantity": "1002.39",
 "expected_error_pct": "33",
 "measurement": {"unit": "SF", "space": "lonlat", "shapes": [{"kind": "area", "ring": [[...]]}],
                 "image": {"source": "...", "pixel_size_m": 0.0762, "captured_on": "2025-06-02"},
                 "drawn_by": "model:sam3@site-v1", "verified": false,
                 "view": {"kind": "public", "width": 977, "height": 977, "id": "...", "label": "..."},
                 "quantity": "1002.39", "area_sf": "1002.39", "method": "local_tangent_plane_wgs84",
                 "expected_error_pct": "3.12", "shape_count": 1, "corner_count": 9, "cutout_count": 0},
 "refusal": null}
```

```json
{"status": "refused", "family": "patio", "unit": "SF", "quantity": null,
 "expected_error_pct": null, "measurement": null,
 "refusal": {"code": "BASIS-SITE-003",
             "message": "Basis found nothing to outline in the box. Draw the box closer around the item, or trace it by hand."}}
```

* `measurement` is the record `basis.domain.traced.measure` makes of the draft (the body plus what
  it computes); the tool takes its body (the computed keys removed) as the trace's base, so an
  untouched draft is sent as it is and an edit keeps it as `original`.
* `expected_error_pct` (top level) is the larger of the outline's one-pixel bound and the method's
  measured 90th percentile for the family (driveway 33, patio 46, deck 28, pool 53; the largest for
  a family not measured): a starting outline, not a measurement. The record's own
  `expected_error_pct` is `traced`'s one-pixel bound, what the server stores once it is sent.
* A public view carries a year only, so its draft is undated (`measure:IMAGE_UNDATED` once sent).
* `refusal.message` never names a provider (AGENTS.md).

| Refusal | When | Call made |
|---|---|---|
| `BASIS-SITE-001` | the view is not a straight-down one (an oblique or street view, no pixel size) | no |
| `BASIS-SITE-002` | its pixel is coarser than `SITE_DRAFT_MAX_PIXEL_M` (0.16 m) | no |
| `BASIS-SITE-003` | SAM 3 found nothing overlapping the box (box IoU 0.5) | yes |
| `BASIS-SITE-004` | the outline runs off the picture's edge | yes |
| `BASIS-SITE-005` | the outline covers less than 3 m² | yes |
| `BASIS-SITE-006` | the outline is not a measurable shape | yes |
| `BASIS-SITE-007` | the call failed, or did not answer within `SITE_DRAFT_TIMEOUT_S` (30 s) | yes (recorded as an error) |
| `BASIS-SITE-008` | a fence | no |

### 8. Refused before any call (nothing spent)

| Answer | When |
|---|---|
| 409 `site-drafts-off` (`BASIS-MEASURE-006`) | `SITE_DRAFTS` or `TRACED_MEASUREMENT` is off (after the access check: a stranger still gets 404). |
| 404 (`BASIS-MEASURE-007`) | The view is not a view of this study (or its image was purged). |
| 404 (`BASIS-MEASURE-008`) | The asset is not an asset of the study's current revision. |
| 409 `gate-not-open` (`BASIS-MEASURE-009`) | The study is not waiting at the assets review: a draft goes with its corrections. |
| 409 `budget-exhausted` (`BASIS-MEASURE-010`) | `STUDY_BUDGET_USD` less the study's recorded spend is under `SITE_DRAFT_RESERVE_USD` (0.05). |
| 503 (`BASIS-MEASURE-011`), `Retry-After` | No segmentation model is configured (`LOCALIZER_URL` or `REPLICATE_API_TOKEN`). |
| 429 (`BASIS-MEASURE-012`), `Retry-After` | More than `SITE_DRAFTS_PER_MIN` (6) drafts by one person in a minute, besides the mutation limit. |

### 9. What is recorded

* The SAM 3 call in `llm_calls` (`kind` `localization`, `activity` `site_draft`, `entity_type`
  `asset` and the asset, the study's stage and revision, no run), so `GET .../spend` and the
  study's budget count it; a failed or timed-out call is recorded with `status` `error`.
* A `measurement.drafted` event on the timeline (actor the engineer; `entity_id` the asset; `code`
  the refusal's, if any; `detail` the status, family, view, `drawn_by`, quantity, unit and cost).
* The same `Idempotency-Key` answers again from the stored response without a second call.

Transactions: the checks read in one short transaction; the view's image is read and the call made
with no connection held (EVAL_REPORT 32); the ledger row and the event are written after it. CPU
work (decoding the view, tracing the mask's outline) runs on threads.

### 10. Not built

The draft is not stored until sent (a reload before sending loses it, as an unsent trace is kept
only by the assets review's draft); a vision model proposing the boxes (M2v) is not offered (EVAL
106.5: the two models measured put their boxes too far off); the draft's method error (the 90th
percentile) is not stored with the measurement once sent, only `traced`'s one-pixel bound.
