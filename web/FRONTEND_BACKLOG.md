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
- **Demo requests (operators).** `GET /v1/ops/demo-requests` lists the public site's
  "Book a demo" requests newest first (name, email, company, role, note, page, when, from
  which address, and whether the email went out: `notification` queued, sent or failed with
  `notify_error`), operators only, with a keyset cursor (`docs/api/DEMO_REQUESTS.md`
  section 5). The developer page could list them beside the client errors; nothing in the
  app reads them yet. The public form (`web/site/directions/demo.html`, on the landing branch) posts
  to `POST /v1/demo-requests`; on release its `basis-api` meta must name the deployed API.

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

- **Firms are set up by Basis, not self-serve.** `POST /v1/orgs` is off by default on the backend
  (**landed in #162**, `FIRM_SELF_SERVE`; 403 `firm-self-serve-off`, `BASIS-FIRM-011`); firms are created with `python -m basis.admin create-firm`, which also invites
  their first owner. The "not a member" screen offers no way to create a firm: it says to ask the
  firm's admin for an invitation, or to contact Basis to set up a new firm, with "Check for an
  invitation" and "Use another account" (`web/live/live-team.js`). The sample API has no
  `POST /v1/orgs`. **Rename** (`PATCH /v1/orgs/{id}`, owner) is unchanged; see section 15.
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
- **Change type or exclude a found object on a photo; remove a component.** Superseded by section
  14: removing an inventory row is the supported `component_remove` correction (reason required),
  now offered as Remove row; excluding and retyping a model detection is `object_exclusion`
  (**landed in #159**).
- **Unpriced-asset guidance.** `BASIS-CHECK-012`'s fix text (the generated error catalogue)
  says "Price it by hand, or add the row to the cost table". Pricing by hand exists since #159
  (an engineer's unit price on the asset or its component), so the text now matches; the app's
  Assets page should point an unpriced row at that price field.
- **Unit cost.** **Landed in #159**; section 14 (`unit_cost_override`).
- **Which result checks the engineer has dealt with.** `GET .../result` reports each worksheet
  and structure check as `ok`, `warning`, `error` or `not_checked`, with no acknowledged or
  resolved state. Results therefore shows every warning as "to review", even on an approved
  study where the engineer has already looked at it. Telling decided checks from open ones
  needs an acknowledgement (who, when, note) recorded against the revision.
- **What the signed report said.** `GET .../report/state` gives the firm profile and report
  answers versions the signature used, not their values, and `profile.set` firm activity names
  the changed fields only (owner only). After a change, Report & sign shows "signed with version
  1 · now version 2" and links to the signed PDF's first page. Showing the signed and current
  value of each changed field needs the signed inputs kept with the signature. **Partly landed
  in #158**: the signature now keeps the letterhead it printed (`letterhead`: name, address,
  phone, logo sha256) and the signer details' version, and `report/state` carries the signer's
  details now (`signer`); the report answers' signed values are still versions only.
- **Delivery state in the study list.** Home and Library read `GET .../report/state` once per
  finished study (six at a time) to tell Update required, Needs signature and Signed apart;
  until a study's answer arrives it reads Checking… and counts as neither Report to sign nor
  Finished. `GET /v1/studies` carrying each study's delivery state would make that one request.

## 14. Engineer overrides: what the app needs from the API (November 2026)

Engineers must be able to override everything Basis decides. The app builds every override in
live mode where the API supports it (component type/count/material/attachment, remove a row,
add a row, rename and merge rooms, move a photo, asset classification and measured quantity,
add or delete an engineer's object). Four things the API lacks are built fully against the
in-browser sample API (`index.html?mode=live&api=sample`, `web/live/sample-api.js`, its
`PROPOSED` section) and appear in live mode only when the API says it offers them. Delete the
`PROPOSED` section once the backend has these.

**Landed 2026-11-06:** capabilities, Not counted and the unit price in #159 (`docs/api/OVERRIDES.md`;
PATCH is allowed from the browser since #159); each engineer's signer details and the signed
letterhead in #158 (`docs/api/SIGNERS.md`). Not built: create a room, the report narrative, Reset
to AI after sending. The sample's P0 to P2 can go once the app reads the real routes; keep P3.

* **Capabilities** (**landed in #159**: `object_exclusion` and `unit_cost_override` true by
  default, settings `OBJECT_EXCLUSION` and `UNIT_COST_OVERRIDE`; `room_create` always false).
  `GET /v1/capabilities` (any member) answers
  `{"object_exclusion": bool, "unit_cost_override": bool, "room_create": bool}`. The app asks once
  per session, outside the error capture; anything but a 200 means none, so today's API (404)
  shows exactly what it showed before.
* **Not counted on a photo object** (`object_exclusion`; **landed in #159**: 409 on an uncounted
  candidate; the 422 path of a bad body names the variant, e.g. `["body","not_counted","reason"]`).
  `PATCH /v1/studies/{study_id}/photos/{document_id}/objects/{object_id}` with
  `{"counted": false, "reason": "<1-500 chars>"}`; `{"counted": true}` counts it again. Engineer
  role, at the edit stage only (409 otherwise), model and engineer-added instances alike; nothing
  is deleted. The object (and every instance in `GET .../objects`) carries
  `exclusion: {"by", "by_name", "at", "reason"}` or `null`; excluded instances are
  `counted: false` and leave the per-label counts. The app draws them struck through with who
  and why.
  **Changing a detection's type uses the same concept**: the app marks the AI's box Not counted
  (reason "Type changed to <label>: <why>") and adds an engineer object with the new label at the
  same box (`POST .../objects`, box + label, SAM 3 outlines it, the duplicate guard applies). So the
  backend needs this one new concept, not a relabel endpoint.
* **Unit price override** (`unit_cost_override`; **landed in #159**). Two correction fields on `asset` and
  `component` corrections (decision route, a `reject`): `unit_cost` (number > 0, at most
  10,000,000, USD per takeoff unit; `null` clears it) and `unit_cost_source` (1-300 chars,
  required with a number); the correction's `reason` is required. A `component` override prices
  every asset taken off that component. The next revision's asset reports `cost.unit_cost` = the
  override and `cost.override: {"unit_cost", "source", "reason", "by", "by_name", "at", "level",
  "correction_id", "revision"}`. Built on the backend branch of the overrides
  (`docs/api/OVERRIDES.md` there), whose shape the sample API now answers in: `unit_cost` a
  decimal string as entered (`"512.4"`, like every money value), at most two decimals in the
  request, `level` `asset` (wins) or `component`, and `cost.trail.kind` `engineer_price` with the
  table's own pricing kept as `trail.replaced`; a price alone clears only the table's pricing
  review reasons.
* **Create a room** (`room_create`; **not built**, the capability is false). `rooms_review` accepts only `room` and `photo_room`
  corrections (`backEnd/basis/workflow/contracts.py`), so the API cannot create a room. Main's live
  Rooms editor sent `room_create` for New room and `room_delete` for Remove, both refused with 422.
  Fixed in the app: Remove is now Merge (`room` `{"merge_into": <id>}`, supported) and New room
  shows only with this capability. Proposed: `{"entity_type": "room_create", "entity_id":
  "<client uuid>", "before": null, "after": {"name", "room_type", "floor"}}` at `rooms_review`;
  `photo_room` corrections in the same decision may name that id; empty rooms are dropped.
* **Report narrative text** (not built; requested November 2026). The engineer should be able to
  edit the report's prose (the property description, the methodology and the findings Basis
  writes), with the AI's text kept and shown struck through beside the engineer's, who changed
  it and why, and Reset to AI, like every other override. Needs the narrative sections as data
  (a `GET .../report/narrative` with section id, the generated text and its version), a write
  that records the engineer's text with a reason against the next revision, and the export to
  render the engineer's text where it exists. The report must say the section was edited.
* **Reset to AI after sending** (**not built**). The app's Reset to AI drops a row's unsent corrections. After a
  correction is applied the rows do not say what the AI found, so the app cannot show the AI's
  original value struck through on later revisions. Wanted: each corrected row carries its
  model values (`ai: {label, count, material, attachment, unit_cost}`), and a correction that
  restores them.
* **The letterhead a report was signed with** (**landed in #158**: `SignatureOut.letterhead`
  `{name, address, phone, logo_sha256}` as signed, and `signer_details_version`). "Changed after signing" compares the signed
  engineer and license with the profile now (`SignatureOut.engineer`), but the signature keeps
  only `firm_profile_version` for the letterhead, so the app can show today's firm name and
  address but not the signed ones (it links page 1 of the signed PDF instead). Wanted: the
  signature (or `GET .../report/state`) carries the signed profile's `name`, `address` and
  `phone`, or a `GET /v1/orgs/{org_id}/profile?version=<n>` for a past version.

## 15. Firm pages: what the app needs from the API (November 2026)

**Merge gate for PR #160: backend #161 must ship first** (done: #161 merged before #160, 2026-11-06). This frontend delegates search to
`q` and promises #161's fields; an older property-name-only API cannot satisfy that contract.
#161 does not require this frontend. After it ships, update the sample's base search contract
and remove the P4 search proposal; retain the still-proposed summary/file-number additions.

Home, Library, Activity, Team, Settings, Report & sign and onboarding are built for an admin and an
engineer against the in-browser sample API (`index.html?mode=live&api=sample&role=admin|engineer`,
`&onboarding=invited|nofirm|new-firm`; `web/live/sample-api.js`, its `PROPOSED` section, P4 to P7).
Against today's API each page shows what exists and says what it cannot (noted per item). Delete the
proposed routes from the sample once the backend has them.

* **P4. What identifies a study, and search over it.** (Search **landed in #161**; `summary` and
  the file number are **not built**.) `StudyOut` (list and detail) carries
  `summary: {address, city, state, zip, county, parcel_number, client, borrower, taxpayer,
  file_number}` or `null`: the appraisal's subject address, county and assessor's parcel number
  (`subject.property_address`, `subject.city`, `subject.state`, `subject.zip`, `subject.county`,
  `subject.assessors_parcel`, UAD36_INTAKE / `uad36_map.py`), its lender/client and borrower
  (`subject.lender_client`, `subject.borrower`), the taxpayer from the report answers, and a new
  firm file number (one optional line on the study, settable at Start a study and afterwards; the
  app's Start a study already shows a Client field it cannot send). `GET /v1/studies?q=` matches
  every word, case- and accent-insensitive, against the property name, each of those fields and the
  creator's name and email. Backend PR #161 implements server search over the property name,
  intake state/ZIP, taxpayer, appraisal address/county/borrower/owner/parcel, creator and signer
  (all words, case/accent-insensitive; 200 characters, 10 distinct words). It does not add
  `summary`, a file number, or lender/client search. **The Library now sends `q` to the server**
  after a 250 ms debounce and follows every cursor page (no 5,000-row cutoff), with
  `include_archived=true` so the Archived filter still finds matches. Status, engineer and sort controls apply to those server
  results. A match is never discarded for lacking a local field; `match` only annotates the
  returned fields. Pending and error states do not claim zero matches; changing or clearing a
  query cannot show an older response. The frontend checks the 200-code-point limit; the
  server validates distinct words using its Unicode casefold; a query-field 422 is shown as an
  actionable limit message without a retry button. Abandoned searches stop before another page
  request, and cache keys use opaque sequences so a typed name/address never enters load-error
  log context. Pending searches preserve previous rows/chips with an explicit busy notice;
  whitespace-only changes do not refetch. Counts are announced through the persistent status
  node outside the app. Until `summary` exists, the extra
  identity columns are still unavailable. The broader file/client examples remain sample-only.
* **P5. The member directory for every member.** (Each member's `signer` **landed in #158**; the
  list is still owners only, so the directory for engineers and viewers is **not built**.) `GET /v1/orgs/{org_id}/members` answers every
  member, read-only below owner (today owners only, `OwnedOrg`), and each member carries `signer`
  (P6's details, or `null`). The engineer's Team page lists colleagues with their role, signer
  credentials and open studies; today it falls back to the people the firm's studies name, and
  says the full list is shown to admins. Activity and Home resolve actor names from it.
* **P6. Each engineer's signer details.** (**Landed in #158**. A stale tag is 412
  `BASIS-REPORT-016`, not the sample's `BASIS-FIRM-011`, which the API now uses for
  `firm-self-serve-off` (#162); the sample should answer `BASIS-REPORT-016`.) `GET /v1/me/signer` → `{"signer": {"name",
  "credentials", "license_number", "license_state"} | null, "version", "updated_at"}` with
  `ETag: "<version>"`; `PUT /v1/me/signer` with `If-Match` (412 with the current ETag when it
  changed; a new code, `BASIS-FIRM-011` in the sample). The form currently limits name and
  credentials to 200 / 100 characters; the license number up to 50 and its two-letter jurisdiction go together (50 states,
  DC, GU, MP, PR, VI). Backend PR #158 implements the `signer` envelope above, adds `user_id`,
  accepts up to 500 characters for name/credentials and uses its report conflict codes. Settings
  edits it;
  onboarding asks for it once after an invitation is accepted; Report & sign shows it read-only
  ("Signing as … Edit in Settings") and signs with it: the app sends it as `engineer` in
  `POST .../report/sign`, which matches #158's `SignIn.engineer` field (the personal profile envelope
  and report state use `signer`). #158 also renders the saved signer in the preview and uses their
  saved details when signing; the sample PDF remains static. Without P6 nothing is stored per
  person: the sign dialog asks for the signer's details each time, prefilled with their name (and with the firm profile's engineer of
  record only when its email is theirs), and Settings says so instead of showing a form.
* **P7. The firm's activity for every member.** (**Not built**; the app's fallback to
  `/v1/me/activity` and the firm's own events works against the real API.) `GET /v1/orgs/{org_id}/feed?since&until&actor_id&
  study_id&limit&cursor` → `{org_id, items, since, until, next_cursor}`: the `study_events` of every
  study of the firm, newest first, each item an `ActivityItemOut` plus `actor_name` and
  `property_address`, default window 30 days, 200 a page. Today `/v1/me/activity` is the caller's
  own events and `/v1/orgs/{id}/activity` is owners only and about the firm, not its studies; the
  app falls back to "Your activity" and an admin also gets the firm events. The app shows decisions,
  gate openings, signatures, the report written, uploads (a creation, its uploads and its start read
  as one entry), reopenings, archive and retries; it leaves out activities, stage moves and the
  corrections a decision already counts (`firm-model.js` `studyEntry`).
* **Delivery state on the study list** (section 13): Home, the Library and the study page read
  `GET .../report/state` once per finished study, six at a time; 14 studies is fine, a firm's year
  is not.
* **Invitations: resend.** An invitation is emailed once (FIRMS.md 3.1); the Team page's Resend
  revokes and invites again, which is a new invitation row. A `POST .../invitations/{id}/resend`
  that re-queues the email on the same row would keep one row per person.
* **A firm's own name in Settings**: `PATCH /v1/orgs/{org_id}` exists; the letterhead form saves the
  report profile (`PUT .../profile`), whose `name` is printed on reports. Renaming the firm itself
  is not on the page yet.

## 16. The appraisal's sketch (EVAL_REPORT 96; `TAKEOFF_SKETCH`, default off)

Nothing here is visible until the setting is on. With it on:

* **Sketch fields at the appraisal review.** The extraction's fields gain a `sketch` section:
  `living_area_sqft`, `ground_floor_area_sqft`, `garage_area_sqft`, `patio_area_sqft`, each with
  `page`, `source` (`azure` or `regex`) and confidence 0.80, and no `regions` (nothing to
  highlight yet). They are not in the field catalogue, so the app has no label for them and the
  "add a missing field" correction refuses their keys. The app does not show or group them.
* **The sketch against the GLA** (`gla_vs_sketch`, BASIS-CHECK-031; decided 2026-11-23). When
  the sketch's living area and the GLA box differ by more than the tolerance, the extraction is
  flagged: `review_reasons` gains `gla_vs_sketch:improvements.gla_sqft,sketch.living_area_sqft`,
  `review_codes` gains BASIS-CHECK-031, and the issue's message gives both figures, the page of
  the sketch and what to do. The app has no wording for this reason and does not show the
  sketch fields beside the GLA, which is where the engineer would correct one of them (either is
  an ordinary `extraction_field` correction by field id). The takeoff repeats the outcome on
  `takeoff_inputs.sketch_check` of the roof, siding, gutter and landscaping assets.
* **The browser's error catalogue is behind.** `web/live/error-catalogue.js` has 304 of the
  backend's 311 codes: BASIS-DEMO-001 to 006 (section 95) and BASIS-CHECK-031 are missing.
  `python3 web/scripts/sync-error-catalogue.py backEnd` adds exactly those seven and changes no
  existing entry (checked 2026-11-23, not committed: the file is the app's).
* **Where a quantity came from.** `takeoff_inputs.footprint_source: "sketch"` and formulas that
  read "1200 SF ground floor on the appraisal sketch" on the roof, siding and gutter rows; a
  patio the sketch dimensions arrives as a measured SF quantity instead of `needs_measurement`.
* **The whole reading** (every row, its rectangles, why a field was withheld) is in the
  extraction's `raw.sketch`, which no route returns.
* **The outline** (EVAL_REPORT 100; `TAKEOFF_SKETCH_OUTLINE`, default off, with
  `TAKEOFF_SKETCH`). When the appraisal PDF's sketch is line work, the `sketch` section gains
  `ground_perimeter_ft` and `upper_perimeter_ft` with `source: "drawing"` (a new value of a
  field's `source`), the page, confidence 0.80 and no `regions`; `raw_text` says which rows'
  outlines were joined and whether parts of a floor on separate pages were summed. The roof
  and siding rows then cite them (`takeoff_inputs.perimeter_field`, `ground_perimeter_field`,
  `upper_perimeter_field`) and their assumptions say the perimeter was read from the sketch's
  outline. The app shows none of it. `raw.sketch.outline` (no route returns it) also keeps each
  believed outline as corner points in feet (`matched[].outline_ft`), enough to draw the
  footprint beside the page, and the reason when nothing could be read (`raster` for a sketch
  that is a picture).

## 21. Built from sections 17 to 20 (the Property area, November 2026)

Sections 17 (the traced measurement, PR #178), 18 (property pictures, PRs #180 and #181), 19
(public records, the public geo sources branch, no PR yet) and 20 (renovations, PR #183) are on
their branches,
not on main yet; when they merge, delete from them what is listed here as built. The app's side
is `live/property.js` (`web/README.md`, "The Property area"; design in `DESIGN_NOTES.md`,
"Property"), built against the sample API's PROPOSED P8 and P9, and shown only when
`GET /v1/capabilities` says `property_capture`, `public_geo` or `traced_measurement`.

**Built:**

* 17: offering the tracing only with `traced_measurement`; the `measurement` on an `asset`
  correction in lon/lat with the image's source, `captured_on` and `pixel_size_m`, never with the
  typed quantity; the server's computation in the browser for the live readout; the shape's
  problems in the server's words before sending; the traced quantity on the row; the four
  `measure:` flags' sentences (`live/review-reasons.js`) and, after saving, the flags it will raise.
* 18: the capability; the pin card (confirm, move by clicking the house, the address) with
  `If-Match` and the 412 reload; the status in plain words with `resume_at` in local time,
  polling every 10 s while it runs; Take them again (`force` when the pin moved or the capture failed) with 409 and
  429/503 `Retry-After`; the views with date, season, pixel size and source; the four sides; street
  level as "not available" with the note; the history strip (leaf, year only, undated, at placed in
  service, the suggested view); the compare; measuring opened on `measure_on`; the notes; no
  measuring on an unconfirmed pin.
* 19: the public image with credit and year, the series by year with the coarse years marked,
  the parcel outlines (under the pin and adjacent) with the lot area and the layer's acres, the
  site check (agree, adjacent, the flagged disagreement linking to the appraisal), the lidar peak,
  eaves and outline, the gather status and Gather them again when stale.
* 20: the aerial bracket input: from the compare, a project's `aerial` or a component label's
  `exterior_brackets` entry, with `If-Match` and the 412 reload.

**Not built:**

* 17: a later assets rejection does not send the measurement again (the backend's "Not done";
  the maintainer decides whether the app repeats it or the backend keeps it); measuring on an
  uploaded plan (`space: image` with a scale) and `document_id`; showing a sent row's
  `takeoff_inputs.measurement` (the shape over the picture) on the asset page; the 422's per-shape
  `detail` placed next to the shape (it shows as the send's error message).
* 18: prefilling the pin from the appraisal's subject address (the form prefills from the study's
  summary when the API has one); choosing history dates (the API does not offer it);
  `green_share`; the capture list beyond the last successful one; the operator incidents page.
* 19: the site check as a review flag at the appraisal gate beside `site.area_sqft` (the
  Property area shows it and links to the appraisal); `gather.sources` per source; a public view's
  `terms_url`.
* 20: the projects editor (key, kind, scope, dates, invoices, capitalized amount, QIP), tagging
  photographs, component ages and removals at the assets review, the renovation schedule, the
  reprice after a change. The bracket form lists the projects the document already has and the
  outside components of the assets.
* Asked of the backend: compare the pin's point, not its version, when marking views stale
  (confirming the pin makes every view stale today); an `image.view_id` on the measurement.

## 17. The traced measurement: what the API offers and the app does not use (EVAL_REPORT 97)

The backend accepts a measurement the engineer **traced** instead of typed
(`docs/api/OVERRIDES.md` section 4); the tracing screen is the maintainer's design
(`docs/review_2026-10-15/REMOTE_INSPECTION_AND_MEASUREMENT.md` section 7.3 has the needs).
Nothing below is built in the app.

* **The capability.** `GET /v1/capabilities` has `traced_measurement` (setting
  `TRACED_MEASUREMENT`, default true). Offer tracing only when it is true; the typed
  quantity, unit and "where measured" fields (section 10) stay as they are.
* **The correction.** An `asset` correction's `after.measurement`: `unit` (SF, SY, LF),
  `space` (`lonlat` degrees, or `image` pixels with a `scale`: two clicked points and the
  known length in ft or m), `shapes` (areas with optional cutouts, or lines; a wall face is
  lines with `height_ft`), and `image` (`source` required; optional `document_id` of the
  uploaded plan, `captured_on`, and `pixel_size_m` for aerial imagery). Never with
  `quantity`, `quantity_unit` or `quantity_source`. A bad shape is a 422 at
  `["body", "corrections", <i>, "after"]` whose `detail` names what is wrong and where
  (`measurement.shapes[0].ring crosses or touches itself ...`): show it next to the shape.
* **What comes back.** After the rejection, the asset's `takeoff_inputs.measurement` holds the
  stored shape and the computed `quantity`, `area_sf` or `length_ft`, `method`,
  `expected_error_pct` (a string, or null when unknown), `shape_count`, `corner_count`,
  `cutout_count` and `scale_ft_per_px`; the takeoff's `formula` is a sentence for the row
  ("800.00 SF traced by the engineer on ... (captured ...), 1 area of 4 corners; expected
  error up to 7.00 %"). Showing the value, the image's date and pixel size and the expected
  error next to the quantity is section 7.3's need.
* **The flags.** `measure:IMAGE_UNDATED`, `measure:IMAGE_AFTER_SERVICE`,
  `measure:ERROR_UNKNOWN`, `measure:COARSE` have their sentences in
  `web/live/review-reasons.js` and their codes (BASIS-ASSETS-021 to 024) in the generated
  catalogue; the review list shows them like any other reason.
* **A later rejection.** Like a typed quantity, a traced one applies to the revision its
  rejection builds; a later assets rejection that does not send it again recomputes the
  takeoff (OVERRIDES.md section 4, "Not done"). Until the backend keeps it, the app would have
  to send it again with every later assets rejection, or the maintainer decides to store it
  on the component as the unit price is.

## 18. Property pictures: what the API offers and the app does not use (EVAL_REPORT 98, 99)

The backend takes dated views of the study's property in the background
(`docs/api/PROPERTY_CAPTURE.md`). How the Property area changes the engineer's work is in
`docs/review_2026-10-15/PROPERTY_CAPTURE.md` section 7. The screens are the maintainer's design.
Nothing below is built in the app.

* **The capability.** `GET /v1/capabilities` has `property_capture` (setting
  `PROPERTY_CAPTURE`, default false). Show the Property area only when it is true.
* **The pin, at intake.** As soon as the study has an address:
  - `PUT /v1/studies/{id}/property/pin` with `{address}` and `If-Match` (the `ETag` of
    `GET .../property`, `"0"` before the first save);
  - then `POST .../property/captures` (`{}`), which answers 202 and runs in the background.

  A 412 carries the current `ETag`: read again and reapply. 422 `capture-no-address` means
  there is no pin yet. 409 `capture-in-progress` means one is already running: poll instead.
  409 `capture-off` means the setting is off.
* **Confirming the house.** After the capture, `pin.source` is `search` and `pin.confirmed` is
  false. Show `pin.place_label` and the point on `aerial-wide`. The engineer either confirms
  (`PUT` with the same `lat`/`lon` and `confirmed: true`) or moves the point (`PUT` with the new
  `lat`/`lon`, `confirmed: true`). Views with `stale: true` were taken around an earlier pin:
  offer to take them again (`POST .../captures` with `{"force": true}`). Do not offer measuring
  on an unconfirmed pin.
* **Status.** Poll `GET .../property` while `capture.status` is `queued`, `waiting` or
  `running` (every 10 to 30 s is plenty).
  - Show `capture.message` as given. It never names the imagery provider.
  - While `waiting`, show `resume_at` in the engineer's local time: "Property pictures are
    paused until 3:00 pm; they will resume on their own."
  - On `failed`, show the message and a "Take them again" action.
  - `code` is for support, through the error catalogue.
* **The views.** `capture.views[]`, each with `label`, `kind`, its camera, `pixel_size_m` (null
  on tilted views), `imagery_date` / `imagery_on` (read off the viewer's status bar since
  EVAL_REPORT 99; null with `imagery_date_note` saying why), `leaf`, `source` (show it with the
  image) and `stale`. Views taken since EVAL_REPORT 99 have no viewer interface in them, so the
  date must be shown beside the image, not looked for in it. The image is at
  `GET .../property/views/{view_id}/image` (PNG, `ETag`, 304). `aerial-close` is the one to
  measure on, `aerial-wide` gives context, and `side-*` show each face.
* **Measuring from a "needs measurement" row.** A row with a takeoff blocker for an area or a
  length (driveway, walks, patio, fence) links to `aerial-close`. The engineer traces there.
  Convert pixels to lon/lat around the view's `lat`/`lon` with `pixel_size_m`:
  - east metres = `(x - width / 2) * pixel_size_m`;
  - north metres = `(height / 2 - y) * pixel_size_m` (heading 0);
  - longitude = `lon + east / (111320 * cos(lat))`, latitude = `lat + north / 110540`.

  Send PR #178's `measurement` (section 17) with `space: lonlat` and `image.source` (the view's
  `source`). Set `image.pixel_size_m` to the view's value, and `image.captured_on` to the
  view's `imagery_on` when it has one; leave it out when it is null, which flags
  `measure:IMAGE_UNDATED`.
  Asked of the backend, not built: an `image.view_id` on the measurement, so the row links back
  to the exact view.
* **History (EVAL_REPORT 99).** Views with `kind: history` (`label` `history-YYYY-MM-DD`) are
  top-down views on earlier imagery dates, with `aerial-close`'s camera (the same
  georeference: a shape traced on one lines up on another). Order them by `imagery_on`.
  - Mark each with `leaf` (`leaf-off` "trees bare", `leaf-on` "trees in leaf", `unknown`), and a
    31 December date as "year only".
  - Mark the one nearest the study's placed-in-service date (the intake's) "at placed in
    service".
  - Offer two dates side by side, or one over the other with a slider, for before and after.
  - Measuring: `capture.measure_on` is the suggested view's id (the newest leaf-off top-down
    view, else the newest dated one). Open the measuring tool on it by default and let the
    engineer switch to any other top-down view (`pixel_size_m` not null). Send that view's
    `imagery_on` as `captured_on`.
  - `green_share` is supporting evidence; show it only in a detail view, if at all.
* **Notes.** `capture.notes[]` (`part`, `code`, `message`) are parts that did not work while
  the capture succeeded (history, street level, an unread date). Show each `message` as given,
  quietly, under the views; they need no action.
* **Street level.** `kind: street` (`street-front`) with `camera_lat`/`camera_lon` (where the
  camera stood) and `heading_deg` (towards the house): not taken today (`CAPTURE_STREET_VIEW`
  is off; EVAL_REPORT 99 found it refused headless). Design a place for one ground-level
  picture of the front, never offered for measuring.
* **Not built in the API:**
  - prefilling the pin's address from the appraisal's subject address;
  - choosing which history dates to keep from the app (the plan is the worker's).

  Design the area so those can join it.

## Testing

`web/tests/firm-pages.test.cjs` renders the firm pages (Home, Library, Activity, Team,
Settings), the join page and the sign dialog in a VM, on the sample API through the real API
client, for each role and against the sample with its PROPOSED section removed (today's API).
Those tests do not run a browser: what a render does to the DOM (typed values kept, focus,
layout) is checked by the Playwright captures. The other live screens are covered by their
helper modules' tests only; a browser harness against the local stack (`web/README.md`
"Live mode, locally") is still the gap for them.
