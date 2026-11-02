# Components gate integration — October 23, 2026

## Source and merge

Merged `origin/main` at `5de0d81` (PR #68) into `codex/vision-review-workbench`.
Read main's `web/README.md`, `docs/api/openapi.json`, gate helpers and changed live
controllers before resolving the merge. No new endpoints or backend behavior were added
by this UI integration. Main's backend, schema and migrations are included by the merge.
The public marketing page was not edited in this pass.

## Result

- New studies use Upload, Appraisal, Rooms, Components, Assets, Schedule. The live upload
  page also shows six steps and the four separate review gates; it no longer inherits the
  mock five-step bar or links to a previously viewed study.
- `components_gate: false` retains five steps and the combined Rooms review. Null/unstarted
  studies use the new six-step presentation, following `live/gates.js` on main.
- Rooms for a new study contains grouping controls: rename/retype/floor, merge, and move
  photo. A move stages `photo_room` with `after: {room_id}`. Component inventory and
  re-analysis controls belong to Components; the legacy Rooms review retains them.
- Main's `components_processing` / “Preparing components” state, routing, polling and
  gate revalidation remain in place.
- The redesigned fitted photo viewer opens editing at `components_review`, or at
  `rooms_review` for legacy studies. Its return/inventory links go to Components for the
  new flow and Rooms for the legacy flow. A photo inspected during grouping returns to
  Rooms and offers inspection only. Count corrections use the same gate as object edits.
- Kept main's `row.count + (suggested - inventory_count)` behavior for Use N. No replacement
  object mutation or unsupported endpoint was introduced.
- Browser QA exposed a decision-key collision across studies: the old key used only stage,
  revision and decision. It now includes the study and exact request payload. Identical
  retries reuse their key; another study or changed corrections/comment get a new key.

## Executed checks

`node --test web/tests/*.test.cjs`: **109 passed, 0 failed**. Changed JavaScript syntax
checks and Git whitespace checks passed. New renderer-level regressions execute the live
review and shared viewer code: grouping-only Rooms, Components ownership, legacy combined
review, read-only stages, pending-row removal at the correct gate, aggregate Use N,
script ordering and decision retry key isolation.

Browser checks used the real merged FastAPI routes, Postgres migrated to `0009`, local
storage and Firebase Auth emulator in an isolated test database/API on `127.0.0.1:8015`.
The shared API/database on port 8000 was left unchanged. All photographs were generated
synthetic diagrams; no client materials or paid provider calls were used. The isolated
API supplies deterministic workflow progress from the fixture study row. These checks
verify UI/API persistence and progress presentation, not a complete Temporal/provider run.

Verified in the browser and, for mutations, in the database:

- Six-step grouping: rename and merge staged/removed; photo move submitted through
  Request corrections and persisted as `rooms_review` / `photo_room` with only `room_id`.
  Pending corrections disabled approval.
- Components: no grouping controls; Use 8 adjusted a row from 1 to 2 while its other row
  remained 6. The viewer displayed total 8 and retained the pending correction. A missing
  window staged `room_component`. Submission persisted both at `components_review`.
- At Components, selected an uncounted stored window candidate, added it through the
  object POST (10 to 11 objects), then deleted the manual addition through DELETE
  (back to 10). Next photo and Back to components navigation worked.
- Legacy: exactly five steps, room controls and inventories together, editable photo
  viewer with Back to rooms. Its Use 8 correction persisted at `rooms_review` after the
  idempotency fix. A subsequent second study decision succeeded in the same session.
- Preparing components: Components active in the six-step bar, matching progress heading
  and timeline. Completed photo review had no Add control and remained inspectable.
- Live upload: six steps, distinct Rooms / Components explanations, no stale study IDs
  in the new-study step links. Mock design remains five steps as documented on main.
- Desktop measured 1280×720; narrow view measured 390×844. No horizontal document overflow;
  the narrow six-step bar scrolls within its own region. Read-only photo navigation fits.
  No captured console warnings/errors on the final fresh build. Viewport override reset.

## Screenshots and review

Local gallery: http://127.0.0.1:3115/gate-flow/

Fresh editable synthetic examples (the tested mutation fixtures have advanced revisions):

- Six-step Components: http://127.0.0.1:3100/?mode=live&api=http%3A%2F%2F127.0.0.1%3A8015#components/802ddff1-ebdc-44bb-b1ae-693098de5521
- Legacy Rooms: http://127.0.0.1:3100/?mode=live&api=http%3A%2F%2F127.0.0.1%3A8015#rooms/ec84bd09-c1bc-4398-8cea-fddc804f0f21

Preview services and synthetic data are local. No push, production deployment or full
paid study execution is claimed by this review.
