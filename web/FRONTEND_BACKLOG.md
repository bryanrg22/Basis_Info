# Frontend backlog: what the API offers that `web/live` does not use yet (2026-11-01)

A brief for the frontend work (Bryan, with Codex). The backend side of each item is built,
tested and documented; the links are the contracts. `docs/api/openapi.json` is the source
of truth. Items are in the order they matter for a first firm.

## Rules that apply to every screen

- **Never name a provider.** Nothing a customer or engineer sees says OpenAI, Anthropic,
  Azure, Replicate or SAM 3. An outage reads as "Basis is temporarily unavailable". The
  operator page (`web/developer/`, `GET /v1/ops/health`) is the only place with provider
  names.
- **Every write is conditional.** The intake, the firm profile, the report answers and the
  gate drafts carry an `ETag`; send it back as `If-Match` and handle 412 by reloading and
  asking the user to reapply (`docs/api/STUDY_RESULT.md`, `REPORT.md`).
- **Every error has a code.** Problem responses carry `code` (`BASIS-<AREA>-<nnn>`,
  `docs/ERROR_CODES.md`) and `request_id`; show the message, keep the code for support.

- **Errors are recorded.** `live/errors.js` (loaded first) reports every error a user hits
  to `POST /v1/client-errors` with the user, firm, study, screen, last actions, stack and
  the failed request; a screen reports its own with `Basis.reportError(err, context)`
  (`docs/api/CLIENT_ERRORS.md`). The operator page `developer/client-errors.html` lists
  them; the system-health page (not in the repository yet) should link to it.

## 1. Blocking: nothing can start a study

- **Intake form.** `POST /v1/studies/{id}/start` refuses 422 `intake-required` without a
  complete intake, and the app never sends one (`live-app.js` creates the study with
  `property_name` only). Required: purchase price, acquisition costs (may be empty), land
  method and its value (appraisal site value, land appraisal value, or flat percentage
  with its source), property use (residential rental needs the 80 % dwelling-unit share),
  placed-in-service date, state. Optional: acquisition date, zip, appraisal effective
  date, assessor land and improvement values, transient lodging. Contract:
  `docs/api/STUDY_RESULT.md`, `GET`/`PUT /v1/studies/{id}/intake`. Once the study starts
  the intake is locked (409 `intake-locked`); changing it means a reopen.

## 2. Resume and history

- **Gate drafts.** Corrections typed at a gate are lost on reload today. Save them with
  `PUT /v1/studies/{id}/drafts/{stage}` as the user edits, load on entry, delete on submit
  (the API deletes the submitter's draft itself). `docs/api/` drafts section.
- **Resumable uploads.** On a draft study, list documents from `GET /v1/studies/{id}`:
  an unconfirmed one (`uploaded_at` null) gets a new signed URL from
  `POST .../documents/{doc}/upload-url`; re-registering the same bytes is a 409 to treat
  as "already there".
- **Restart a cancelled study:** `POST .../restart` (owner).
- **Reopen, history, restore** (`docs/api/REVISIONS.md`): a completed study's
  "Reopen at…" action with the warning the API returns (what will be recomputed);
  a Versions panel from `GET .../revisions`; a diff view from
  `GET .../revisions/{a}/diff/{b}`; "Restore this version".
- **Per-user activity** (`GET /v1/me/activity`, 90-day default window, paginated) for
  the user's own history page; **firm activity** (`GET /v1/orgs/{id}/activity`, owners)
  for the admin page. Both replace the "Mock" tags on `#activity` and `#admin`.
- **Audit and timeline** for a study (`GET .../audit`, `GET .../timeline`): the
  `#audit` screen is still the design mock.

## 3. Library

- `GET /v1/studies` now takes `q`, `status` (repeatable), `created_by`,
  `include_archived`, `sort` and returns `total`; the app downloads everything and
  filters in the browser. Move search, filters and sort to the server
  (`docs/api/STUDY_LIBRARY.md`). Add Archive (`DELETE`) and Unarchive (`POST .../unarchive`).
- Studies carry `created_by_name` and `created_by_email`: show the person, not the id.

## 4. Firm onboarding

- **Create a firm** (`POST /v1/orgs`, any verified sign-in) and **rename**
  (`PATCH /v1/orgs/{id}`, owner): the "not a member" screen should offer "Create your
  firm" instead of only "wait for an invitation". `docs/api/FIRMS.md`.
- **Invitations are emailed** now; the list shows `emailed_at` and the last error. Show
  it on the Team page.
- **Firm profile form:** add `limiting_conditions` (editable list) and the engineer's
  `email`. **Study details form:** add interviews and prior depreciation (look-back
  studies).
- **Retention:** the firm's `retention_days` (owners) once #115 lands; show the purge rule.

## 5. Report

- The schedule download has vendor profiles: `GET .../report?format=csv|xlsx&profile=…`
  (`fixed_assets_cs`, `sage`, `proconnect`, `cch`, `bloomberg`, default `generic`);
  add a picker next to the download buttons (`docs/api/REPORT.md`).
- `report/state` now says when the answers or the profile changed after signing; show it.

## 6. Smaller

- Reprice (`GET`/`POST .../assets/reprice`): a "Rebuild prices" action after an intake
  or cost-index change.
- Spend (`GET .../spend`, owners): a cost line on the study page.
- Result (`GET .../result`): the basis worksheet and the reconciliation, today only in
  the PDF.
- `room_aliases` is always sent as `{}`.

## 7. Photos the analysis skipped (found in live run 6, EVAL_REPORT 63)

- A photo whose analysis failed for good (provider outage, budget) is in the workflow
  view's `flagged_photos` (`GET .../workflow`, `docs/api/WORKFLOW.md`) but has no room, so
  the rooms page never shows it and the study page's note ("they stay visible in the rooms
  review") is wrong for it. Show the unplaced photos as a section of their own on the rooms
  page with the re-analyze action (a `photo` correction at the rooms gate), name them in
  the note, and count them out of "N of M analyzed" (`completed_photos` includes them).
  The API side landed with EVAL_REPORT 64: `GET .../rooms` returns `unplaced`, the photos
  no room of the revision holds, each with `analyzed` (false: the analysis never produced
  a result for it), the same list on every page. Render it as its own section on the rooms
  page with the re-analyze action.

## 8. Found by the live walkthrough of every screen (EVAL_REPORT 64)

Plain bugs an engineer would hit on the first day, in the order they bite:

- **Activity page shows invented history in live mode** (`#activity`): the mock feed
  (Alex Morgan, Harbor House, "Illustrative events") renders for a signed-in engineer and
  nothing calls `GET /v1/me/activity`. Until the page is wired (section 2), live mode must
  show "not connected yet", never the mock events.
- **Settings in live mode are the mock** (`#settings`): Profile shows the mock user,
  Workspace the mock firm, Cost sources "preview". Wire Profile to `/v1/me`, Workspace to
  the firm (`PATCH /v1/orgs/{id}`, section 4), Cost sources to `docs/api/COST_INDICES.md`.
- **Flagged photos never reach a completed study's page**: the workflow view is polled
  only while a study processes, so on a completed study the "N flagged photos" note and the
  Workflow line ("—") are empty. Fetch `GET .../workflow` once on a completed study too, and
  count flagged photos out of "N of M analyzed" (`completed_photos` includes them).
- **Provider and layer names shown to the engineer** (Bryan's rule): the photo viewer
  prints "SAM 3 · … · model 1bf97763"; the assets table and asset page print the method
  token (`cache`, `rule`, `llm`, `manual`). Say "Basis outlines", and "firm convention",
  "rules table", "model", "engineer".
- ~~Stale copy on Team & invitations ("No email is sent…")~~ fixed with EVAL_REPORT 64;
  `emailed_at` and the last send error are still not shown (section 4).
- **Broken image on the landing page**: `assets/kitchen.webp` is referenced by
  `app.js` (the hero preview) and is not in `web/assets` (two 404s on every load).
- **Report preview header** says "Local PDF · not uploaded" for an API-rendered preview.
- **Schedule table**: unpriced assets print "0.0000 SF  $0.00" on some rows and "—" on
  others; one presentation for "not priced".

## 9. Found by the Report & sign verification (EVAL_REPORT 72)

- **The results page does not follow the export**: approving the assets review sends the
  engineer to Results, which does not poll (only the progress page does), so the step stays
  "In progress" and the report step "Not started" until they open another screen. Poll
  `GET .../workflow` on Results while the study is `processing`, or send the approval to the
  progress page as the other gates do.
- ~~No way to make the new revision a changed signed report needs~~: fixed with EVAL_REPORT 72
  (Report & sign offers "Start a new revision", `POST .../reopen` at the assets review, and
  lists every earlier revision's signed PDF).
- ~~The firm profile's expected 404 before the owner saves it was recorded as a client error on
  every Report & sign load~~: fixed with EVAL_REPORT 72 (`api.request(..., {expect:[404]})`).
- **Rail on narrow screens**: the sidebar rail stays at 320 px and takes a fifth of the width
  (Codex noted it in EVAL_REPORT 67); the report step itself fits with no horizontal scroll.

## 10. Missing inputs (EVAL_REPORT 78)

* **Blockers on the result and the report screens.** `GET .../result` `blockers` lists every
  reason the report is withheld, each with `message`, `assets` and `fix`
  (docs/api/STUDY_RESULT.md). Show them where the engineer would look for the report, with a
  link to the screen each fix names.
* **Unpriced rows on the assets review.** A row with an `input:<code>` review reason shows
  "missing input" with the reason's text from its cost row (`source_row`), not a $0.
* **Measured quantity.** On an unpriced asset that needs one (`input:exterior_area`,
  `input:room`, `input:cabinet_run`, `cross:COST_NEEDS_QUANTITY`), offer quantity, unit and
  "where measured" fields; they go into the `asset` correction (`quantity`, `quantity_unit`,
  `quantity_source`) with the rejection.

## 11. Support opened a file (EVAL_REPORT 79)

* **The study history row.** `GET .../timeline` has `operator.document_accessed` rows with
  `actor_type` `operator` (Basis support) and `detail` `filename` and `reason`. The results
  page's history shows the actor word ("Operator") and the event name. It should read "Basis
  support opened <filename>: <reason>" so the firm understands who looked at its file and why
  (docs/api/OPS_DOCUMENTS.md 4).

## 12. iPhone photos (HEIC) before upload (EVAL_REPORT 81)

* **The upload list's preview.** `web/upload-review.js` previews a chosen photo from the local
  file (`URL.createObjectURL(file)`). Chrome, Edge and Firefox cannot show a HEIC, so an iPhone
  photo shows a broken image there until it is uploaded (Safari shows it). After the upload the
  app shows every photo from `GET .../documents/{id}/image`, a JPEG for HEIC
  (docs/api/PHOTOS.md), so only this pre-upload preview is affected. Show the photo icon instead
  when the browser cannot decode the file (the `<img>`'s `error` event), or name the format.

## 13. Found by the UI review of every screen (November 2026)

What the engineer asked for that the API cannot do yet, and what it offers that the app does
not use. Nothing here is built in the app; each needs the backend first or is a follow-up.

- **Remove an asset.** An asset correction takes category, recovery class and period, IRS
  section and a measured quantity (positive only, `check_quantity`); nothing removes or
  excludes an asset. The Assets page offers Edit only. Needs an audited exclusion (who, why)
  that the allocation and the report respect.
- **Change an approved step while the study is still in review.** `POST .../reopen` reopens
  completed studies only (409 `study-not-completed`). Mid-study, the only way back is to
  request corrections at the step that is open. The completed-study path ("Change
  Components" and friends) is built on the read-only review pages and on Report & sign.
- **Change type, confirm, mark wrong or delete a found object on a photo.** The proposed
  object operations in `web/README.md` ("API work still required") are still proposals. In
  live mode the photo viewer offers what works: the room inventory correction (label and
  count) for a type, and Delete for objects an engineer added. A per-object correction of
  Basis's own detections needs the object PATCH the README proposes.
- **Remove a component from a room.** A `component` correction's count is 1 to the maximum
  (`basis/domain/counts.py`, `count_problem`), so a type the photos do not show cannot be taken
  out of a room's inventory; the engineer can relabel it or lower it to 1. Needs a removal (count
  0 or an exclusion) that the assets then drop.
- **Unpriced-asset guidance.** `BASIS-CHECK-012`'s fix text (the generated error catalogue)
  says "Price it by hand, or add the row to the cost table", but an asset correction cannot set
  a price; the catalogue text should name the cost-table route only, until an override exists.
- **Unit cost.** An asset correction cannot change the unit cost; the cost follows the cost
  table. An engineer's override would need a priced-row correction with its source.
- **Which result checks the engineer has dealt with.** `GET .../result` reports each worksheet
  and structure check as `ok`, `warning`, `error` or `not_checked`, with no acknowledged or
  resolved state. Results therefore shows every warning as "to review", even on an approved
  study where the engineer has already looked at it. Telling decided checks from open ones
  needs an acknowledgement (who, when, note) recorded against the revision.
- **What the signed report said.** `GET .../report/state` gives the firm profile and report
  answers versions the signature used, not their values, and `profile.set` firm activity names
  the changed fields only (owner only). After a change, Report & sign shows "signed with version
  1 · now version 2" and links to the signed PDF's first page. Showing the signed and current
  value of each changed field needs the signed inputs kept with the signature.

## Testing

No automated test loads any live screen (`web/tests/*.test.cjs` cover the helper
modules only; `live-smoke.mjs` walks the API without rendering). A screen-level harness
(jsdom or Playwright against the local stack, `web/README.md` "Live mode, locally") is
the gap to close alongside the forms above.
