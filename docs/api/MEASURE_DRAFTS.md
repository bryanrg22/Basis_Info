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
