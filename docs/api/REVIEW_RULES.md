# Review rules: what is flagged, what each gate approves, what going back redoes

This page is for two readers: an engineer at a firm who reviews studies in Basis, and a
developer who changes the code behind the review. It says, gate by gate, which items Basis
marks **needs review**, why, at what threshold, what the engineer checks and can change, what
approving does, and what is kept or done again when the engineer goes back.

Every statement was checked against the code on `main` at 3b4eed8 (2026-11-05). The "Where in
the code" column names the place a developer can read to confirm it (paths are under
`backEnd/basis/`). When the code changes, this page changes with it.

## 1. Words used on this page

| Word | Meaning |
|---|---|
| **Gate** | A point where the study stops and waits for the engineer. There are four, in this order: **appraisal**, **rooms**, **components**, **assets**. |
| **Flag** ("needs review") | A mark Basis puts on an item it is unsure of, with a **reason** (a short text such as `missing:improvements.gla_sqft`) and, for most reasons, an error **code** (`BASIS-CHECK-020`) listed in `docs/ERROR_CODES.md`. A flag points the engineer at the item. It never stops anything. |
| **Approve** | The engineer accepts the gate as it is. An approval cannot carry changes. |
| **Request corrections** | The engineer sends changes (the API calls them *corrections* and the decision *reject*). Basis starts a new **revision** with the changes applied and opens the same gate again. |
| **Revision** | A numbered copy of the study's results. Every request for corrections, reopen and restore makes a new one. Old revisions are never changed. |
| **Reopen** | Take a **completed** study back to a gate of your choice, on a new revision (`POST /v1/studies/{id}/reopen`). |
| **Restore** | Make an earlier revision the current one again, as a new revision (`POST /v1/studies/{id}/revisions/{n}/restore`). |
| **Blocker** | A missing input that the dollars would depend on. Unlike a flag, a blocker withholds the report until it is fixed. |
| **Convention** | A firm's earlier ruling on how to classify a component, learned from approved studies and reused on the next ones. |

## 2. The four rules that matter most

1. **Every gate opens on every study, flagged or not.** A flag only tells the engineer where
   to look first. (`StudyWorkflow._await_decision` waits at each gate unconditionally;
   `workflow/study_workflow.py`.)
2. **A flag never blocks approving, and never blocks signing.** The decision route and the
   workflow do not read any flag. What withholds the report is a **blocker** (section 7.3).
   (`api/routes/decisions.py`, `StudyWorkflow.validate_decide`; no `needs_review` check in
   either.)
3. **An approval carries no changes.** To change anything, request corrections. An approval
   with corrections is refused (422 `approval_with_corrections`;
   `StudyWorkflow.validate_decide`, `api/routes/decisions.py`).
4. **Going back never deletes anything.** A request for corrections, a reopen and a restore
   each add a revision. Earlier revisions, their reports and their signatures stay as they
   were (`activities/apply_corrections.py`, `activities/carry_revision.py`).

## 3. The gates at a glance

| Gate | The engineer checks | Changes the engineer can send | Approving moves the study to | Where in the code |
|---|---|---|---|---|
| Appraisal | The fields read from the appraisal, above all the nine critical ones (section 4.1) | Correct a field's value; add a field the reading missed | the rooms gate (it opens when the photos are done) | `workflow/contracts.py` `CORRECTION_TYPES`; `db/repositories/review.py` `DECISION_TRANSITIONS` |
| Rooms | How the photos are grouped into rooms: names, types, floors, which photo is in which room | Rename a room or change its type or floor; merge two rooms; move a photo to another room | building each room's inventory, then the components gate | same |
| Components | Each room's components and counts, and the photos Basis was unsure of | Correct a component (label, material, attachment, count); remove a wrong row; add a component to a room; send a photo to be analyzed again | classifying, measuring and pricing the assets, then the assets gate | same |
| Assets | Each asset's class, recovery period, quantity and cost, and the study result | Correct an asset (category, bucket, recovery period, IRS section, measured quantity); reprice with the current inputs; set the building model | learning the firm's conventions, then the export (report and schedule) and `completed` | same |

A study started before the components gate existed (patch `components-gate-v1`) has one
rooms gate that takes both the grouping and the component changes
(`SINGLE_ROOMS_GATE_TYPES` in `workflow/contracts.py`). Every study started now has four
gates.

While a gate waits, Basis sends a reminder every **7 days** (setting
`review_reminder_days`) and marks the study **stalled** after **30 days** (setting
`review_deadline_days`). A late decision still works. (`StudyWorkflow._await_decision`;
`config.py`.)

## 4. Appraisal gate

### 4.1 When the appraisal is flagged

The appraisal is flagged as a whole (one flag on the extraction, with every reason listed)
when any of the following holds. The reasons appear in `review_reasons` and their codes in
`review_codes` on `GET /v1/studies/{id}/extraction`.

The **nine critical fields** are: property address, contract price, year built, total rooms,
bedrooms, bathrooms, gross living area (GLA), final market value, and the effective date of
the appraisal. When a UAD 3.6 report is read from its PDF, the total room count is not
required (that form does not print it; its XML does). (`extraction/field_mappings.py`, fields
marked `critical=True`, `CRITICAL_FIELDS`; `extraction/uad36_pdf.py` `NOT_CARRIED`.)

| Flag (reason) | When | Threshold and where it is set | Code | Where in the code |
|---|---|---|---|---|
| `missing:<field>` | A critical field is empty | none | BASIS-CHECK-020 | `extraction/confidence.py` `review_gate` |
| `low_confidence:<field>=<c><<t>` | A critical field's confidence is below the threshold | **0.90**, setting `extraction_review_threshold` (`EXTRACTION_REVIEW_THRESHOLD`). The threshold is stored on the extraction, so a later correction is judged by the same number | BASIS-CHECK-021 | `review_gate`; `config.py:308`; `activities/extract_fields.py` `_with_corrections` |
| `type:<field>` | A value does not read as its type (a number, a date) | none | BASIS-CHECK-023 | `extraction/validation.py` `validate_fields` |
| `range:<field>` | A number is outside its plausible range (table below), a percentage is outside 0 to 100, or a date is before 1900-01-01 or more than a year after today | code constants, no setting | BASIS-CHECK-024 | `validate_fields`, `_ranges`, `EARLIEST_DATE` |
| `rooms_vs_bed_bath:...` | Total rooms is less than bedrooms plus bathrooms | none | none (no catalogue code) | `validate_fields` |
| `value_vs_cost:...` | The final market value differs from the cost approach's value by more than **50 %** | code constant | none (no catalogue code) | `validate_fields` |
| `value_vs_contract:...` | The final market value differs from the contract price by more than **20 %** | code constant | BASIS-CHECK-026 | `validate_fields` |
| `basement_vs_gla:...` | The basement area is more than **1.5 times** the GLA | code constant | BASIS-CHECK-025 | `validate_fields` |
| `sources_disagree:<field>` | Two readers of the PDF give different values for a critical field (older `pipeline` reading only) | none | BASIS-CHECK-027 | `extraction/sections.py` |
| `azure_di_rejected` | The document reader refused the PDF; only its text layer was read | none | BASIS-CHECK-028 | `extraction/pipeline.py` |
| `xml_pdf_mismatch:<field>` | A UAD 3.6 XML value is not printed next to its caption on the report PDF | none | BASIS-CHECK-029 | `extraction/mismo36.py` `locate_fields` |
| `no_report_pdf` | A UAD 3.6 XML arrived without its report PDF | none | BASIS-CHECK-030 | `activities/appraisal_sources.py` |

Any one reason flags the appraisal, whatever its severity: severity only tells the engineer
whether a value is unusable (error) or suspicious (warning). No appraisal uploaded at all is
also flagged, with no fields to show (`activities/extract_fields.py`).

Plausible ranges (`extraction/validation.py` `_ranges`; "year" is the current year):

| Field | Range | Field | Range |
|---|---|---|---|
| Year built | 1700 to year + 1 | Tax year | 1900 to year + 1 |
| GLA (sq ft) | 100 to 50,000 | Basement area (sq ft) | 0 to 50,000 |
| Total rooms | 1 to 200 | Bedrooms, bathrooms | 0 to 100 |
| Stories | 1 to 150 | Effective age (years) | 0 to 300 |
| Garage cars, fireplaces | 0 to 50 | Site area (sq ft) | 1 to 500,000,000 |
| Contract, offering, final and appraised value ($) | 1 to 100,000,000 | Days on market | 0 to 10,000 |
| Remaining economic life (years) | 0 to 200 | | |

### 4.2 Why a PDF appraisal is always flagged today

Each field carries a confidence that depends on who read it:

| Read by | Confidence | Where in the code |
|---|---|---|
| A UAD 3.6 XML file | 1.0 | `extraction/mismo36.py` `CONFIDENCE` |
| The document reader (Azure Document Intelligence) | the reader's own score | `extraction/azure_di.py` |
| The PDF's text layer or a table | 0.80 | `extraction/field_mappings.py` `REGEX_CONFIDENCE`, `TABLE_CONFIDENCE` |
| A language model, including the default `grounded` reading | at most 0.60 | `field_mappings.py` `LLM_CONFIDENCE_CAP`; `extraction/grounded.py` |
| The engineer (a correction or an added field) | 1.0 | `activities/extract_fields.py` `_with_corrections` |

The default reading of a PDF appraisal (`EXTRACTION_METHOD=grounded`) has a model read every
field, so every critical field it reads is at 0.60, below 0.90. **Every PDF appraisal is
therefore flagged, with a `low_confidence` reason for each critical field**. A field the
engineer corrects is at 1.0, so the flag clears only when every critical field has been
corrected in the same request for corrections (section 4.5 explains why the same one) and no
other reason holds. This is on purpose: a model's value is a suggestion the engineer confirms
(`tests/unit/extraction/test_grounded_extraction.py` asserts it). A UAD 3.6 XML appraisal is
flagged only for the other reasons in the table. The 0.90
threshold comes from the legacy code; no evaluation has tuned it (section 10).

### 4.3 What the engineer does

* **Correct a field** (`extraction_field`): a new text, number or date value. It is written
  as the engineer's value with confidence 1.
* **Add a field the reading missed** (`extraction_field_add`): the field, one value, the
  page it is printed on, the printed words as evidence, and a reason (required). A field the
  extraction already has is corrected instead (422 `correction_field_exists`).

(`activities/corrections.py` `EXTRACTION_FIELD_VALUES`; `extraction/added_fields.py`;
`api/routes/decisions.py`.)

### 4.4 What approving records and does

The decision (who, when) is stored, the engineer's unsent draft for the gate is discarded, and
the study moves to the rooms gate, which opens once the photo work running in the background
is done. The approved fields become the inputs of everything after: the rooms' floor areas,
the building's cost, the cost year and the location factor. (`db/repositories/review.py`;
`StudyWorkflow._lifecycle`; `activities/process_assets.py` module doc.)

### 4.5 Requesting corrections at the appraisal gate

A new revision starts. The rooms, photo assignments, components and photo outlines are copied
as they are. The appraisal is **read again** from the same document: the document reader's
earlier analysis is reused (not paid again), the model reading runs again, and then this
request's corrections and added fields overwrite the fields they name. The flag is worked out
again over the corrected fields, and the appraisal gate opens again. (`activities/
apply_corrections.py` table; `activities/extract_fields.py` module doc.)

**Corrections from an earlier request are not re-applied.** Each request reads the document
again and applies only its own corrections, so a value corrected in the first request shows
the reading's value again after the second; correct it again. (`activities/extract_fields.py`,
"What survives a second appraisal rejection".)

## 5. Rooms gate

### 5.1 What is flagged

Nothing at the rooms gate is flagged per room: a room's flag comes from the room step, which
runs only after this gate is approved. What the engineer sees here:

* **Photos Basis was unsure of** (the workflow's `flagged_photos` list on
  `GET /v1/studies/{id}/workflow`). A photo is on it when its analysis is flagged or failed:

| Photo flag (reason) | When | Threshold and where it is set | Code | Where in the code |
|---|---|---|---|---|
| `ambiguous` | The model could not tell the room | none | BASIS-CHECK-040 | `vision/analyze.py` `review` |
| `low_room_confidence:<c>` | The model's own confidence in the room is below the threshold | **0.5**, code constant `ROOM_REVIEW_THRESHOLD` (no setting) | BASIS-CHECK-041 | `vision/analyze.py:109`, `review` |
| `invalid_model_output[:<calls>]` | The model's answer was unusable twice; the photo gets no components | 2 attempts, code constant `MAX_ATTEMPTS` | BASIS-CHECK-042 | `vision/analyze.py`; `vision/schemas.py` `ambiguous_analysis` |
| `count_fusion_fallback:<why>` | The second counting model failed or took longer than **80 s**; the first model's counts stand | `FUSION_DEADLINE_S`, code constant | BASIS-CHECK-043 | `vision/analyze.py` `_apply_fusion` |
| (no reason) | The analysis failed for good (an unreadable image, a photo the provider refuses) or the study's budget ran out before it; the photo has no rows | the study budget, setting `study_budget_usd` | BASIS-BUDGET-005 when the budget was already spent | `StudyWorkflow._analyze_one`, `_flag` |

* **The Unassigned room**: a photo the model could not place, and whose file name names no
  room, is put there so the engineer can move it (`vision/rooms.py` `UNASSIGNED`).

The photo's own reason is kept in its stored analysis; no API route returns it today, only the
photo's id in `flagged_photos`.

### 5.2 What the engineer does

* **Room** (`room`): a new name, room type or floor; or merge the room into another
  (`merge_into`).
* **Move a photo** (`photo_room`): to another room of the same revision.

(`activities/corrections.py` `ROOM_FIELDS`, `PHOTO_ROOM_FIELDS`.)

### 5.3 What approving does

The grouping is fixed. Basis builds each room's inventory from its photos (the room step,
model calls), lets the photo outlines still running finish, and opens the components gate. A
photo analyzed again later stays in the room approved here. (`StudyWorkflow.
_grouping_then_components`; `activities/analyze_photo.py` `keep_room`.)

### 5.4 Requesting corrections at the rooms gate

A new revision starts with the appraisal, the rooms (renames and merges applied), the photo
assignments (moves applied), the components and the photo outlines copied. **No photo is
analyzed again**: the room types and floors of the file-name rooms are voted again from the
photos' earlier answers (no model call), and the rooms gate opens again.
(`activities/apply_corrections.py` table; `activities/reconcile_rooms.py` `vote_only`.)

## 6. Components gate

### 6.1 What is flagged

* **A room**, when the room step was unsure of its inventory (`needs_review` on each room of
  `GET /v1/studies/{id}/rooms`, from the room's inventory rows):

| Room flag (reason) | When | Threshold and where it is set | Code | Where in the code |
|---|---|---|---|---|
| `dropped_objects:<n>` | The model listed objects that point at no photo of the room; they were dropped | none | BASIS-CHECK-050 | `vision/reconcile.py` `reconcile_room`; `vision/room_step.py` `room_call` |
| `invalid_model_output` | The model's answer was unusable twice; the room's inventory is the simple merge of its photos' lists | 2 attempts | BASIS-CHECK-052 | `vision/reconcile.py` `reconcile_room` |

A room with more than **10** photos is read in several calls of at most 10 (code constant
`MAX_IMAGES_PER_CALL`, `vision/reconcile.py:69`); that alone is not a flag. A one-photo room
takes its photo's list as its inventory and records the reason `single_photo`, but **is not
flagged** (`vision/room_step.py` `_reconcile_path`; setting `room_single_photo`).

* **The photos** of section 5.1, still in `flagged_photos`.
* **The whole gate** is flagged (in `needs_review_stages`) when the room step flagged a room,
  when it failed (rooms then keep their photos' own lists), or when a photo was skipped
  because the budget ran out (`StudyWorkflow._reconcile_rooms`, `_flag`).

The photo outlines (SAM 3) never flag anything; a failed outline is logged and ignored
(`StudyWorkflow._localize_one`).

### 6.2 What the engineer does

* **Correct a component** (`component`): label, material, attachment, count (a whole number
  from 1 to 10,000).
* **Remove a wrong row** (`component_remove`): a reason is required. Refused when the row is
  rebuilt by the same request anyway (a row of a room whose photo is sent for re-analysis, or
  of a merged room) or is the room's last inventory row.
* **Add a component to a room** (`room_component`): label and count, optionally material and
  attachment.
* **Send a photo to be analyzed again** (`photo`): any photo of the study, including one that
  failed.
* While the gate is open, the engineer can also draw or tap objects on a photo, and delete
  their own; those apply at once, not through the decision (`api/routes/objects.py`).

(`activities/corrections.py`; `domain/counts.py` `MAX_COUNT`; `domain/removals.py`;
`api/routes/decisions.py`.)

### 6.3 What approving does

Basis builds the assets: it classifies every component (the firm's conventions first, then
the rules table, then a model for the rest), measures it, prices it, and opens the assets gate
(`activities/process_assets.py`).

### 6.4 Requesting corrections at the components gate

A new revision starts with the appraisal, the rooms and every photo's room copied, and the
components and outlines of every photo **not** sent for re-analysis copied with the
corrections, removals and additions applied. The photos sent for re-analysis are analyzed and
outlined again (model calls) in the rooms approved at the rooms gate. Then the room step runs
again, and **only rooms whose photos changed are rebuilt**; the other rooms keep their
inventories with the corrections applied. A correction of a row that is rebuilt would be lost,
so it is refused up front (422 `correction_rebuilt`). (`activities/apply_corrections.py`
table; `activities/reconcile_rooms.py`; `api/routes/decisions.py`.)

## 7. Assets gate

### 7.1 What is flagged

Each asset is flagged on its own (`needs_review`, `review_reasons`, `review_codes` on
`GET /v1/studies/{id}/assets`, which can filter by `needs_review`). An asset gathers every
reason that applies.

**Classification** (`classify/router.py`):

| Flag (reason) | When | Code |
|---|---|---|
| `citation_unverified` | A citation could not be found in the reference text, or the model's answer (or a cited convention) has no verified citation | BASIS-ASSETS-007 |
| `missing_evidence` | The model answered without searching the references | BASIS-ASSETS-008 |
| `rule_contested:<rule>` | The rules-table row used is marked contested (the authorities disagree) | BASIS-ASSETS-005 |
| `model:<reason>` | The model itself asked for review | BASIS-ASSETS-009 |
| `classifier_unavailable` | No model is configured for classification; the asset is put in the building class, marked `Unclassified` | BASIS-ASSETS-006 |
| `tool_cap_exceeded`, `iteration_cap`, `invalid_output`, `timeout`, `budget_exceeded`, `provider_error:<type>`, `no_answer` | The model gave no usable answer within its limits: **3** reference look-ups, **4** turns, **60 s** (code constants in `classify/residual.py` `LoopLimits`, no setting); the asset is put in the building class, marked `Unclassified`, confidence 0 | BASIS-CHECK-060 to 066 |
| `convention_conflict:<tier>` | The firm's own rulings for this component disagree with equal weight, so the rules table or the model decided instead | BASIS-ASSETS-014 |
| `validation:<check>` | The class is inconsistent: an IRS section that cannot hold the bucket (1245: 5, 7, 15 years; 1250: 15, 27.5, 39), land, a missing or unknown section, a building bucket of the other property class, a recovery period that is not the bucket's, a structural-looking label as personal property, a personal-property-looking label in the building | BASIS-CHECK-001 to 008 |

There is **no confidence threshold** for classification: a low model confidence does not flag
an asset by itself. A model answer with no search or no verified citation has its confidence
capped at 0.3 (`MISSING_EVIDENCE_CONFIDENCE`) and is flagged for that reason. An asset served
by a firm convention is **not** flagged for disagreeing with the rules table or with the
label checks (overriding them is what a convention is for); it is flagged only for an
inconsistent class or an unverified citation (`_from_cache`, `errors_only=True`).

**Measuring and pricing** (`activities/process_assets.py`, `domain/cross_validation.py`):

| Flag (reason) | When | Threshold (code constants unless named) | Code |
|---|---|---|---|
| `cross:SECTION_1245_SF_LARGE` | A section 1245 asset measured in square feet is larger than **100 sq ft** | `SECTION_1245_SF_THRESHOLD` | BASIS-CHECK-010 |
| `cross:COST_ROW_MISSING` | The cost table has no row for the label | none | BASIS-CHECK-012 |
| `cross:COST_NEEDS_QUANTITY` | The cost table prices the label per area or length, but the takeoff only counts it | none | BASIS-CHECK-011 |
| `cross:COST_BELOW_RANGE` / `cross:COST_ABOVE_RANGE` | The unit cost is below **half** the typical minimum, or above **twice** the typical maximum, for the 12 labels with a typical range | `LOW_TOLERANCE` 0.5, `HIGH_TOLERANCE` 2, `COST_RANGES` | BASIS-CHECK-013, 014 |
| `cross:COST_TOTAL_MISMATCH` | The total differs from quantity times unit cost by more than **$0.01** | in `check_takeoff_cost` | BASIS-CHECK-015 |
| `input:<what>` | A quantity would stand on a missing input (no GLA, no stories, no count, a cabinet run not measured, ...): the row is written **unpriced** | none; `domain/money_inputs.py` `INPUTS` names each input and where to supply it | none (no catalogue code); also a blocker |
| `cost:not_priced` | The row could not be priced for another named reason (a cost year before the index, a bad location factor) | none | none (no catalogue code) |
| `cost:YEAR_INDEX_MISSING` | No cost index is loaded for the study's cost year; the latest one is applied | none | BASIS-COST-001 |
| `cost:YEAR_INDEX_CONVERTED` | The year's index is converted from a public price series, not published by RSMeans | none | BASIS-COST-002 |
| `cost:LOCATION_STATE_MISMATCH` | The study's ZIP is listed under another state; the state's average factor is used | none | BASIS-COST-006 |
| `takeoff:upper_bound` | The quantity is an upper bound (landscaping: the whole unbuilt lot) | none | BASIS-ASSETS-010 |

**Concealed building rows** (the structure model, setting `structure_step`, on by default;
`domain/structure.py`):

| Flag (reason) | When | Code |
|---|---|---|
| `structure:building_rcn_review` | The building's cost new needs review. With the default `structure_rcn_source=square_foot` it **always** does, so every concealed row carved from it carries this reason; with the appraisal's figure, only when its checks warn | none (no catalogue code) |
| `structure:component_clamped` | A row's itemized assets exceeded its share of the building, so the share was clamped | none |
| `structure:residual_zero` | Nothing is left in the row after the itemized assets | none |
| `structure:residual_unidentified` | The specialties residual row, always: unidentified content stays with the building until the engineer identifies it | none |
| `structure:assumed_quantity` | A small system's quantity is assumed (exterior locks with no door in the photos; site utility lines, always) | none |
| `input:stories` | The smoke alarm row cannot be counted without the number of stories | none |

Checks marked "info" never flag: a mechanical-room note, a 39-year asset counted in units, a
unit unusual for its bucket, a cost that includes a year or location adjustment
(`domain/validators.py` `Issue.needs_review`; `domain/cross_validation.py`).

### 7.2 What the engineer does

* **Correct an asset** (`asset`): category, bucket, recovery period, IRS section, and a
  measured quantity (`quantity`, `quantity_unit` EA/SF/LF/SY, `quantity_source`, given
  together). The corrected asset is rebuilt with the engineer's values, no model call, and
  goes through the class checks and the measuring and pricing checks again, so it can still be
  flagged.
* **Reprice** (`POST .../assets/reprice`): rebuild the prices with the current intake and cost
  indexes, keeping every classification.
* **Set the building model** (`structure_model`): the building type or class the square-foot
  model uses.

(`activities/corrections.py` `ASSET_FIELDS`; `activities/process_assets.py` `_write_manual`;
`api/routes/reprice.py`.)

### 7.3 Flags and blockers are different

A flag asks the engineer to look; a **blocker** withholds the report. The study result
(`GET /v1/studies/{id}/result`, `blockers`) lists one blocker for each of: a row unpriced for a
missing input (`input:<what>`), rows unpriced because the cost table lacks them, rows marked
`Unclassified`, no appraisal fields at all, a location or cost year taken from a setting rather
than the study, no building cost new, no depreciation inputs, and no cost index loaded for the
year. While any blocker remains there is no allocation, so no report is produced and none can
be signed. (`evidence/result.py` `report_blockers`, `compute_result`; docs/api/STUDY_RESULT.md.)

### 7.4 What approving does

1. **The firm learns from the study** (the conventions step): every asset the engineer
   corrected becomes the firm's ruling for that component; every model answer with at least
   one verified citation becomes an approved answer, **even if it was flagged**; every asset
   served by a convention adds one approval to it (if the convention still gives that
   answer). Rules-table answers are never stored. A
   failure here is logged and does not stop the study. (`activities/promote_conventions.py`.)
2. Basis waits for every photo outline to be recorded, writes the export (the report and the
   preparer's schedule, unless a blocker withholds the report) and marks the study
   `completed`. Signing is a separate step (docs/api/REPORT.md).

So approving an asset as it stands is a ruling: the next study of the same firm will reuse
it.

### 7.5 Requesting corrections at the assets gate

A new revision starts with everything up to the components copied. Then the assets step runs
again for the whole revision:

* a **corrected** asset takes the engineer's values, with no model call;
* **every other component is classified again from the start** (conventions and rules cost
  nothing; the model is called and paid again, and its answer can differ), then measured and
  priced again. Classifications are carried over only by a reprice.

(`activities/process_assets.py` `_prepare`, `_rebuild`.)

## 8. Going back

| What | Who and when | What is copied into the new revision | What runs again | Gates passed again | Where in the code |
|---|---|---|---|---|---|
| Request corrections at a gate | Engineer, at the open gate | Everything the gate does not recompute, with the corrections applied | See 4.5, 5.4, 6.4, 7.5 | The same gate, then the ones after it | `activities/apply_corrections.py`; `StudyWorkflow._reject` |
| Reopen at the appraisal gate | Engineer, on a `completed` study | The appraisal and its fields, the rooms, photo assignments, components (photos' and engineer-added) and photo outlines | Room step, assets, export (the appraisal is not read again unless corrections are requested there) | appraisal, rooms, components, assets | `workflow/contracts.py` `CARRIED_THROUGH`, `RECOMPUTED_AFTER`; `activities/carry_revision.py` |
| Reopen at the rooms gate | same | the same | Room step, assets, export | rooms, components, assets | same |
| Reopen at the components gate | same | the same, plus each room's inventory | Assets, export | components, assets | same |
| Reopen at the assets gate | same | the same, plus the assets with their quantities, costs and citations | Export (and the conventions step, which skips what this study already taught) | assets | same |
| Restore revision *n* | Engineer, on a `completed` study, *n* not the current revision | Revision *n*'s rows, up to the last gate *n* reached | Everything after that gate | That gate and the ones after it | `api/routes/revisions.py` `restore_revision`; `db/repositories/revision.py` `gate_reached` |

What a reopen or restore never does: analyze a photo again, outline it again, or re-vote the
rooms. Flags are copied with the rows they are on, so a flagged asset or appraisal is still
flagged in the new revision. The earlier revision's export, report and signature are kept.
The steps after the gate run again in full: the room step rebuilds every room's inventory
after a reopen at the appraisal or rooms gate (model calls), and the assets step classifies
every component again after a reopen at the appraisal, rooms or components gate.
(docs/api/REVISIONS.md; `StudyWorkflow._resumed`.)

The app's report page offers one reopen today: **Start a new revision**, which reopens at the
assets gate (`web/live/live-report.js`). A "Change <step>" control for the other gates is not
in `web/` on `main`; the API supports all four.

## 9. The study-wide lists on the progress page

`GET /v1/studies/{id}/workflow` returns two lists from the running workflow:

* `flagged_photos`: every photo whose analysis was flagged or failed (section 5.1).
* `needs_review_stages`: the gates with something to look at. A gate is added when:

| Gate | Added when | Where in the code |
|---|---|---|
| `appraisal_review` | The appraisal is flagged (section 4.1) | `StudyWorkflow._extract` |
| `rooms_review` | The rooms' re-vote failed | `StudyWorkflow._reconcile_rooms` |
| `components_review` | The room step failed or flagged a room, or a photo was skipped for budget | `_reconcile_rooms`, `_flag` |
| `assets_review` | Any asset is flagged, or the assets step was skipped because the budget could not cover it | `StudyWorkflow._process_assets` |
| any gate | The corrections sent from it were refused when applied (the revision then starts without them and the gate asks again) | `StudyWorkflow._reject` |

Neither list is ever emptied while the execution runs: a gate or photo stays listed after a
later revision fixes it. A reopen or restore starts a new execution with empty lists.
(`StudyWorkflow._mark_needs_review`, `_flag`.)

## 10. Not measured, not verified

* **Thresholds not tuned on data**: the appraisal threshold 0.90 (legacy value), the photo
  room-confidence floor 0.5 (marked "eval-tuned later" in the code), the cost ranges and their
  tolerances (legacy table). No EVAL_REPORT section chose them.
* **Not verified in a running app**: what the screens show for each flag. This page reads the
  API and the code, not `web/`.
* Every rule above was read in the code. The appraisal and photo thresholds were also run
  through the real functions on 2026-11-05 (no model calls): critical fields at 0.60, 0.80 and
  0.89 are flagged and at 0.90 are not; a 20.25 % value gap to the contract price is flagged
  and 20.0 % is not; room confidence 0.49 is flagged and 0.5 is not.

## 11. Docs that disagree with the code

Not fixed here; listed so they can be.

1. **README.md, "Workflow lifecycle"** shows three gates (no components gate), says a rooms
   rejection re-analyzes the flagged photos, and lists `room`, `component`, `photo` at the
   rooms gate. The code has four gates; a rooms rejection re-analyzes nothing; photo
   re-analysis is a components-gate correction (`workflow/contracts.py` `CORRECTION_TYPES`).
2. **README.md, "Object localization"** says the rooms gate waits for every outline and that
   objects are edited at the rooms gate. The outlines run behind the rooms gate
   (`rooms_gate_waits_for_localization`, default false) and objects are edited at the
   components gate (`api/routes/objects.py` `EDIT_STAGE`).
3. **README.md, "Room step"** says a rooms rejection rebuilds the rooms of flagged photos; it
   is a components rejection that does.
4. **docs/api/WORKFLOW.md** says the photos run "analysis, localization, the room step" in
   parallel with the appraisal; with the components gate the room step runs after the rooms
   gate is approved.
5. **`needs_review_stages`** is described as "Stages whose output needs an engineer's
   attention because the budget ran out" (`api/schemas/workflow.py`, `openapi.json`). The
   workflow also adds a gate for a flagged appraisal, a failed or flagged room step, flagged
   assets and refused corrections (section 9).
6. **`vision/analyze.py` module doc** says `single` is the default photo mode; the setting
   `vision_photo_mode` defaults to `decomposed` (`config.py:278`).
7. **The error catalogue** lists `single_photo` (BASIS-CHECK-051) as a flag, but it does not
   flag the room. It has no code for `rooms_vs_bed_bath`, `value_vs_cost`, `input:<what>`,
   `cost:not_priced` or any `structure:` reason, so `review_codes` leaves those out.
8. **AGENTS.md's settings rule** (every setting in `config.py`'s header and `.env.example`):
   `EXTRACTION_REVIEW_THRESHOLD`, `REVIEW_REMINDER_DAYS` and `REVIEW_DEADLINE_DAYS` are in
   neither.
