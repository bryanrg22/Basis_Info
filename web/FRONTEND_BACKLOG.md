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

## Testing

No automated test loads any live screen (`web/tests/*.test.cjs` cover the helper
modules only; `live-smoke.mjs` walks the API without rendering). A screen-level harness
(jsdom or Playwright against the local stack, `web/README.md` "Live mode, locally") is
the gap to close alongside the forms above.
