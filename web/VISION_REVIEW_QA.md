# Photo component workspace: changes and verification

October 22, 2026. Branch `codex/vision-review-workbench`, based on main `2728042`
(including PR #65). Only `web/` changed. No backend implementation, API contract,
client photos, credentials, `design-review/` or `frontend/` changes are included.

## The twelve reported problems

1. **Cannot deselect a type:** All types and the inspector back control clear the type;
   Deselect object clears only the instance. Both paths were exercised in the browser.
2. **Overlapping, inert pins:** 36px button targets, separated by a grid with leader
   lines. A cluster picker handles insufficient space. All 38 live targets selected the
   correct row; the dense phone fixture opened a nine-item picker and selected object 32.
3. **Object click jumps / no row selection:** one shared selection updates the mounted
   canvas and inspector. Actual mask hits cycle with repeated clicks or `[ / ]`.
   The matching row is selected; only the inspector scrolls to reveal it.
4. **Type click scrolls the page:** selection no longer rebuilds the app shell.
   The old viewer reached document scrollY 1019 after instance selection; the new live
   viewer stayed at 0 throughout the 38-target pass. This is an observed local comparison,
   not a claim that every old selection always jumped.
5. **Scores and photo percentages:** retained in the data but absent from rendered
   viewer copy. A low-confidence object can say Needs a look. The room inventory's
   numerical confidence column was also removed; its remaining rows all have five cells.
6. **Model footer:** removed from the customer viewer. No model name/version/object ID
   is displayed. Mock geometry is explicitly illustrative; live geometry is API data.
7. **Broken Show all styling:** replaced by All types, Hide/Show outlines and Pan with
   shared selected/hover/focus states, keyboard labels and reduced-motion styling.
8. **Tiny hidden navigation:** previous/next, room photo count, arrow keys, and a visible
   numbered filmstrip. File names remain in accessible labels/tooltips, not overlapping
   captions. Both photos and navigation boundaries were checked.
9. **Clipped photo / page-sized inspector:** fitted canvas with independent inspector
   scrolling, buttons/wheel/double-click zoom, Pan and Fit. Phone uses a vertical inspection
   layout and hides editing. Exact viewport measurements and limits are below.
10. **No single-object actions/add:** contextual Confirm, Mark wrong, Change type,
    Delete and Add controls. Real box/candidate additions and manual deletion work through
    the existing API. Unsupported writes open an explicit unavailable explanation and
    do not claim to save. Their proposed contracts are in [README](README.md).
11. **Wrong / duplicate outlines:** visible Refine, Split, Merge, Keep my box and Draw
    shape controls; overlap cycling exposes nested objects. Mock mode demonstrates these
    edits. Live refinement, replacement, polygon and atomic split/merge need new API
    capabilities. Duplicate Use the existing one / Add anyway uses the real contract.
12. **Read-only after review:** one calm notice and no edit controls. A completed local
    study still allowed selection and navigation; Delete did nothing and opened no dialog.

## Before / after

All repository screenshots below show generated synthetic kitchen diagrams in a
disposable local QA firm. They contain no client photographs. Full mock/photo viewport
screenshots are delivered separately outside Git because fixture photo licensing is unknown.

Original viewer after selection:

![Original viewer after selection](screenshots/vision-review/before-live-selected.jpg)

New employee overview and selected component:

![Employee overview](screenshots/vision-review/live-engineer-overview.jpg)
![Employee focused component](screenshots/vision-review/live-engineer-focused.jpg)

## Add and fix: what the engineer sees

1. Choose a type, then drag a box. The draft stays on the photo with a Your selection
   label. Escape cancels without writing.
2. Find outline & add shows progress and uses the published POST. **This API saves
   immediately**, so the UI warns before submission. It cannot provide a true pre-save
   model preview. Keep addition acknowledges the persisted result; removal is available.
3. A returned plain box is a normal counted object. Keep my box acknowledges an already
   box-only result. Discarding a returned mask requires geometry replacement API support.
4. Pick a found object previews candidates, Previous/Next cycles, and Use this object
   sends the exact candidate. A duplicate offers Use the existing one / Add anyway.
5. Room inventory displays the aggregate count and photo suggestion. Edit count stages
   a correction against a real inventory entry, preserving the other entries. A missing
   label stages room_component. The correction remains pending until Request corrections
   on Room inventory; it is not an approval or a durable local draft.
6. Refine/Split/Merge and other unavailable live edits explain the limitation without
   changing the study. Mock-only simulations are labelled previews.

![Local box selection before submission](screenshots/vision-review/live-box-draft.jpg)
![Stored candidate preview](screenshots/vision-review/live-candidate-preview.jpg)
![Duplicate choices](screenshots/vision-review/live-duplicate.jpg)
![Real employee addition](screenshots/vision-review/live-engineer-added.jpg)
![Pending room count correction](screenshots/vision-review/live-room-count.jpg)
![Explicit live refinement limitation](screenshots/vision-review/live-refine-unavailable.jpg)
![Read-only study](screenshots/vision-review/live-read-only-selected.jpg)

Some flow captures precede the final copy/overview-pin polish; the employee overview
and focused captures show the final layout. Older Confirm wording on a newly added
object is now Keep addition, because the POST already saved it.

## Executed checks

* `node --test web/tests/*.test.cjs`: **92/92 passed**, including eight new model tests
  for taxonomy, shared selection, overlap cycling, dense pin packing, polygon hit tests,
  portrait/panorama/invalid fit math, anchored zoom and aggregate corrected room totals.
* Changed JavaScript syntax checks and `git diff --check`: passed.
* Mock and live browser console: no captured errors or warnings in the final passes.
* Real local API, Auth emulator, Postgres and local blob bytes, first as admin and then
  as employee. Only a disposable QA firm and generated images were used. Stored candidate
  RLE add, plain-box result, exact candidate selection, duplicate reuse/override, reload
  persistence, manual deletion, count staging, overlap cycling, zoom/pan, photo navigation,
  Escape cancellation and read-only behavior were exercised.
* Dense live photo: all **38 pins** clicked across five types, matching the selected
  instance ID each time, with document scrollY 0. Employee addition raised 38 to 39;
  reload retained it, and manual deletion returned to 38.
* Aggregate room count: multiple entries totaling 6 edited to 7 while preserving the
  other entry. Pending missing-type correction and the Room inventory five-column layout
  were checked. No workflow decision was submitted against the synthetic seeded studies.
* Mock Draw shape, finish/add, plain-box replacement, type change, mark wrong, split,
  merge and deletion were exercised. These do not establish live backend capabilities.
* Exact sizes: **1280×800, 1440×900, 1920×1080, 1024×768, 768×1024, 390×844**.
  Images loaded, document x/y overflow and scrollY were 0, pins remained at least 36px,
  and filmstrip bottoms were inside each viewport. All-types, selected and add states
  were captured on larger sizes; the phone exposes inspection rather than drawing.
  See [viewport measurements](screenshots/vision-review/viewport-checks.json).
* A 38-object phone fixture verified the cluster picker: 23 direct pins plus a nine-item
  picker for 32 cabinets. Choosing cabinet 32 selected the corresponding row with no x
  overflow or document jump.

## What remains unverified or requires backend work

* The six exact viewport passes used **mock mode's shared renderer**. Live behavior was
  verified in Chrome at approximately 1512×805. Auth emulator popup/redirect did not
  complete in the in-app browser; exact-size live authentication testing remains open.
* Physical tablet/pen gestures, hardware pinch, screen-reader traversal, a complete
  WCAG audit and every possible zoom/pan/drawing combination were not verified. Pinch
  handling and geometry tests exist; this is not device certification.
* No paid provider inference was started. Adds used stored synthetic candidates, so new
  box-prompt inference latency/quality, provider timeout behavior and real client-photo
  segmentation quality are not established by this QA pass.
* No end-to-end Temporal reprocessing, priced inventory regeneration, final gate approval
  or report generation was run. Counts were staged and suggestions read, not submitted.
* True pre-save outline preview, persisted confirm/wrong marks, model-object deletion,
  label/geometry/polygon replacement, positive/negative refinement and atomic split/merge
  remain unavailable live. [README](README.md#api-work-still-required--no-invented-calls-in-the-frontend)
  specifies the proposed contract additions and required audit/count semantics.
* Local API initially ran an older contract. It was rebuilt from unchanged main and the
  existing local migration 0008 was applied before duplicate/suggestion checks. No backend
  source or cloud deployment was modified; staging remains parked.
* This branch is local and has not been pushed or deployed. The preview server is serving
  this worktree on port 3100; reverting that server to another checkout changes the preview.
