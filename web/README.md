# Basis web frontend

## Primary frontend

This checkout combines the approved workflow redesign with the current report
preview and signing controls. Start this version with:

```sh
python3 web/serve.py --port 3100
```

The launcher serves the `web/` directory beside itself, independent of the terminal
working directory, and disables stale development caches. Open
`http://127.0.0.1:3100/?mode=live&signin=redirect#dashboard`.

Recovery and current verification limits are recorded in
[PRIMARY_FRONTEND_QA.md](PRIMARY_FRONTEND_QA.md). Earlier sections below are dated
implementation notes; their backend-gap statements must be checked against the
current server.

## UAD 3.6 frontend integration (PR #75)

The live intake accepts an appraisal PDF (legacy or UAD), a UAD ZIP, or MISMO XML
with an optional companion PDF, with the API's 50 MiB per-file limit. It validates
actual bytes before creating a study and registers XML before its companion PDF;
the backend selects the first-created uploaded appraisal. Photos still upload
concurrently. Unsupported supporting files are called out instead of silently skipped.

`live/appraisal-source.js` validates and resolves sources. ZIP previews use vendored
zip.js 2.24.0, select the XML-referenced report (or the sole PDF), and enforce bounded
streaming reads and safe names. Values are never re-extracted in the frontend. XML
without a PDF has a data-only state. Local selections, file-card previews, and appraisal
review reuse the continuous PDF viewer; original package downloads are preserved.

API regions are drawn on every referenced page and the first region determines the
initial page. Legacy fields without regions retain text matching. XML values without
regions receive a review cue and do not fall back to potentially unrelated text hits.
The API does not currently expose the underlying extraction mismatch issues or a
persistent source-PDF document ID; the XML companion resolver follows the documented
latest-confirmed-appraisal-PDF rule.

Backend handoff remains open: PR #75 adds ZIP/XML extraction, but
`StudyWorkflow._ingest_and_extract` still calls `IngestAppraisal._prepare`, which feeds
original bytes to `SourcePdf` before extraction. ZIP/XML study starts need this backend
connection completed. The frontend does not bypass the ingestion workflow. Extraction
issues such as `xml_pdf_mismatch` also need an API contract before precise warnings can
be shown. These backend changes are assigned to the separate Claude Code session.

Validation: `node --test web/tests/*.test.cjs`. The new source/upload tests cover format
selection, MIME types, XML-first registration, retries, archive selection and limits,
and region validation. Browser verification used synthetic API responses (ZIP, XML,
XML plus PDF, and PDF) because Docker/API were stopped. No paid extraction or real study
mutation was performed. Native Chrome file selection was blocked by the extension's
file-access setting; synthetic selection exercised the same intake processing/preview.

## Continuous PDF reports

The upload/file preview and live appraisal source use the same continuous PDF.js
`PDFViewer` (vendored version 5.6.205). All pages are laid out in document order;
PDF.js renders visible/nearby pages and evicts offscreen canvases. Page controls jump
within the report, scrolling updates the current page, and trackpad pinch keeps its
anchor. Selecting an appraisal field jumps to its cited page and highlights exact
text matches. Viewers cancel rendering and release their worker on close/navigation.

## Components gate integration (October 23)

The live redesign follows main’s six-step gate flow while preserving five-step legacy
studies. `vision-workbench.js` uses `BasisGates.editStage` for editing and pending count
corrections, and returns to Components for the new flow or Rooms for legacy studies.
See [COMPONENTS_GATE_QA.md](COMPONENTS_GATE_QA.md) for merge decisions, executed checks,
screenshots and the isolated test environment. The public marketing site is unchanged.

## Shared interface system (October 22)

`interface-system.css` applies the fitted viewer's visual standards across the engineer
workflow and public entry pages. The approved sidebar and vision layout retain ownership
of their mechanics. `interface-system.js` measures the review bar for sticky navigation,
groups appraisal rows into an independently scrolling region, preserves field selection
focus/scroll across renders, and adds table navigation semantics. Existing controllers
continue to own data, corrections, uploads and API calls.

See [INTERFACE_REVIEW_QA.md](INTERFACE_REVIEW_QA.md) for page coverage, live checks,
research references and remaining limits. This pass does not turn mock Activity,
presence or profile preferences into backend features.

## Photo component workspace (October 22)

`vision-workbench.js` now renders the photo viewer in **both modes**. `vision-model.js`
owns selection, overlap hit testing, collision-free pin placement, fit/zoom math, room
totals and the shared component vocabulary. `live/mask-layer.js` still decodes and draws
the real RLE masks. `vision-workbench.css` scopes the viewport layout to this page;
other pages and the existing sidebar retain their layout.

Open `#viewer` in mock mode (`?fixture=dense#viewer` adds a 38-object stress fixture), or `?mode=live#viewer/<study>/<document>` in live mode.
Select a type or an object; **All types** clears the filter. Pins and inspector rows
select the same object. Repeat a click on overlapping outlines, or use **[ / ]** to
cycle. Arrow keys browse room photos; input fields and drawing tools keep their keys.
Use zoom buttons, wheel/pinch, double-click, Pan and Fit. Escape cancels a drawing;
Delete offers removal of a selected manual object. Phones expose inspection and room
review, with editing controls hidden.

### Rooms photos and Components review

Rooms photo links use `#viewer/<study>/<document>/rooms`: the original photo fills
one canvas, with a Back to rooms button, room selector, filmstrip, download and
zoom/pan controls. No component lookup, masks, inspector or read-only notice is
mounted. The viewer follows unsaved room assignments and names for the current
study/revision; returning to Rooms preserves that in-memory draft.

Components links use `#viewer/<study>/<document>/components`, retaining object
outlines, review tools and gate enforcement. The origin survives photo/room
navigation and reloads. Untagged legacy links retain their component behavior,
except six-step studies currently at Rooms review, which default to plain photos.

### What actually saves today

* Drawing a box is initially a local selection. **Find outline & add** sends the existing
  `POST /v1/studies/{study_id}/photos/{document_id}/objects`, with `kind: box`, `label`
  and normalized `box`. The response is already saved by the API. The page explicitly
  says this before submission, shows progress on the box, then offers **Keep addition**
  and removal. Keeping an addition only acknowledges that saved result; it is not a
  separate persisted review decision.
* **Pick a found object** previews the stored candidates and cycles them before sending
  `kind: candidate, candidate_id`. A duplicate offers **Use the existing one** and
  **Add anyway** (`allow_duplicate: true`). A returned box-only result is counted normally;
  **Keep my box** acknowledges it without sending another write.
* Manual removal uses the existing `DELETE .../objects/{object_id}`. The server owns
  authorization, gate checks, attribution, soft deletion and duplicate protection.
* Room suggestions come from `GET .../rooms`. Displayed totals include all inventory
  entries of a type. Editing a total stages a `component` correction against a real
  entry (preserving the others); a missing type stages `room_component`. These are
  clearly pending until sent through **Request corrections** on the Components review (the Rooms review for a legacy study), using
  the existing `POST .../decisions` rejection. They never silently approve or change
  the priced count. Pending corrections are in memory, not durable drafts.
* Local fixture edits are labelled design previews and stay in the current tab. Their
  boxes and polygons are illustrative geometry, not model output.

### API work still required — no invented calls in the frontend

The following are **proposed contract extensions**, not endpoints available today.
The live controls explain the missing capability and make no mutation. The backend
owner must agree their final schemas, revision preconditions and audit behavior.

1. **Preview without saving:** extend `POST .../objects` with a non-persisting preview
   operation returning a draft outline/token; add explicit commit/discard operations.
   Today the POST both computes and persists, so a true pre-save preview is impossible.
2. **Confirm / mark wrong:** a per-object review write, e.g. a proposed
   `PATCH .../objects/{object_id}` accepting `review_status: confirmed|wrong`, with
   actor/time and review revision. Room approval is already available separately.
3. **Change type / replace geometry / force plain box:** a proposed object PATCH
   accepting `label`, normalized `box`, optional RLE `mask` or `polygon`, and an explicit
   clear-mask operation. Preserve the original detection and reconcile room suggestions.
   Current box POST has no force-box flag; the client does not discard a returned mask.
4. **Refine an outline:** a proposed `POST .../objects/{object_id}/refine` taking
   positive/negative normalized points and returning a non-persisting replacement
   preview, followed by explicit commit. No such route is called by this page.
5. **Split / merge:** an atomic replacement operation, e.g. a proposed
   `POST .../objects/replace` with `remove_ids` and replacement objects (label, box,
   mask/polygon), expected revision, and one attributed count reconciliation. Do not
   simulate this with unrelated DELETE/POST requests that can partly fail.
6. **Delete a found object:** extend the existing DELETE policy beyond manual records
   with an audited exclusion/tombstone, preserving source evidence and updating counts.
   Today the API deliberately refuses model-object deletion.

Mock mode demonstrates type changes, confirmation/flags, polygon replacement, box
fallback and basic box split/merge; none of those simulations is used as live persistence.
See [VISION_REVIEW_QA.md](VISION_REVIEW_QA.md) for the issue-by-issue changes and evidence.

Plain HTML, CSS and JavaScript, no build step. Two modes on the same pages:

* **Mock** (the default): the design review. Every screen uses the fixtures in
  `review-data.js`. No network, nothing is saved. This is where the design is edited.
* **Live** (`?mode=live`): the study screens read and write the Basis API (`/v1`, see
  `docs/api/openapi.json`) with a Firebase sign-in.

The design files (`app.js`, `*-review.js`, `*.css`, `index.html`) are the source of truth
for the look. Live mode is a separate layer in `live/`, loaded after them: it replaces the
study screens' render functions with versions that use API data and reuses the design's
helpers and CSS classes. Restyling the design restyles live mode. Changing a mock screen's
markup does not change its live version; see "Editing the design" below.

`DESIGN_NOTES.md` is the design review's own README; `TEAM_HANDOFF.md` describes the
proposed team features.

## Mock mode

```sh
cd web && python3 -m http.server 3100 --bind 127.0.0.1
```

Open http://127.0.0.1:3100/#landing.

The mock screens show five sample room photos (`assets/bedroom.webp`, `exterior.webp`,
`kitchen.webp`, `kitchen-detail.webp`, `living.webp`). They are not in git (listing-style
images of unknown license); copy them into `web/assets/` from the design review folder
(`design-review/assets/`). Without them the mock shows empty image frames; live mode does
not use them.

## Live mode, locally

1. The stack (Postgres, Temporal, API, worker, relay), with the frontend overlay. The
   overlay allows the frontend's origin (CORS), lets the browser upload to local storage,
   accepts the Auth emulator's tokens and raises the rate limits for one engineer
   uploading a 50-photo study:

   ```sh
   docker compose -f docker-compose.yml -f docker-compose.web.yml up --build -d
   docker compose -f docker-compose.yml -f docker-compose.web.yml exec api alembic upgrade head
   ```

   `backEnd/.env` holds the provider keys (OpenAI, Anthropic, Replicate, Azure Document
   Intelligence) and `COST_TABLE_PATH`. A full study on the real providers costs about $6.

   A new database has no reference corpus, so assets get no citations (every asset is
   flagged `citation_unverified`). Load the six reference PDFs once (4 minutes locally, 16
   on the staging job; no API cost), then restart the worker (`docs/deploy/STAGING.md`
   section 1.8). Mount the PDFs into the worker read-only (`/corpus`) and pass each file's
   stem as its `--doc-id`: the classification rules cite documents by those ids
   (`backEnd/basis/domain/rules.py`), so another id leaves rule citations unresolved.

   ```sh
   docker compose … exec worker python -m basis.retrieval.ingest \
     --pdf=/corpus/IRS_IRS_COST_SEG_ATG__2024.pdf --doc-id=IRS_IRS_COST_SEG_ATG__2024 …   # all six
   docker compose … exec worker python -m basis.retrieval.ingest --list-versions        # 7,466 chunks
   docker compose … restart worker
   ```

2. The Firebase Auth emulator, in the demo project `demo-basis` (a `demo-` project never
   reaches real Firebase; the API accepts emulator tokens only with `BASIS_ENV=dev`):

   ```sh
   firebase emulators:start --only auth --project demo-basis
   ```

3. The frontend, then open http://127.0.0.1:3100/?mode=live:

   ```sh
   cd web && python3 -m http.server 3100 --bind 127.0.0.1
   ```

Sign in with **Continue with Google** (the emulator offers test identities) or with a
local test account (email and password; shown only when the emulator is configured).
Browsers that block pop-ups can use `?mode=live&signin=redirect`.

4. A firm. Firms are invite-only: a new account belongs to no firm and sees "Not a member
   of a firm yet". Create the first firm and invite its admin from the command line (there
   is no sign-up and no HTTP route for it), then sign in with Google as that email and
   accept the invitation on the join screen:

   ```sh
   docker compose … exec api python -m basis.admin create-firm --name "Basis" --owner-email you@example.com
   docker compose … exec api python -m basis.admin list-firms
   ```

   Accepting needs a verified email: the emulator's Google provider verifies it, its
   email/password accounts do not (they can sign in but cannot accept). The admin then
   invites everyone else on **Team & invitations** (`#team`). No email is sent: an invited
   person sees the invitation when they sign in with that address. A membership belongs to
   the emulator account (its uid): if the emulator restarts without its saved state, the same
   email is a new account and needs a new invitation (`python -m basis.admin invite`).

### Error capture (`live/errors.js`)

Loaded first on the page. Every error a user hits (an uncaught exception, a rejected
promise, a failed `/v1` call, a `console.error`, a resource that failed to load, an explicit
`Basis.reportError(err, context)`) is recorded with the user, firm, study, screen, the last
50 actions, the stack and source location, the failed request's status, code and request
id, the build and the browser, and sent in batches to `POST /v1/client-errors`
(`docs/api/CLIENT_ERRORS.md`). Nothing secret is recorded: no token, no request body (field
names only), no query values, ids replaced by `{id}`. In mock mode events are kept in
memory and never sent. Operators read them on `developer/client-errors.html`
(`GET /v1/ops/client-errors`, `OPS_OPERATOR_UIDS`): the issues they group into with a status a
person sets (and a regression the ingest sets), the records, a detail view, and **Copy for an
agent**, one plain-text brief to paste into Claude Code or Codex. Tests: `tests/errors.test.cjs`,
`tests/client-errors-page.test.cjs`.

### Configuration (`live/config.js`)

| Setting | Default | Notes |
|---|---|---|
| `mode` | `mock` | `?mode=live` switches; the query string survives navigation |
| `apiBase` | `http://127.0.0.1:8000` | `?api=https://…` overrides |
| `firebase.projectId` / `apiKey` / `authDomain` | the `demo-basis` emulator project | for cost-seg-basis: its web app config |
| `firebase.emulatorHost` | `http://127.0.0.1:9099` | `null` for real Firebase |
| `signInFlow` | `popup` | `redirect` also works (`?signin=redirect`) |
| `uploadConcurrency`, `pollInitialMs`, `pollMaxMs` | 3, 1.5 s, 15 s | |

A deployed frontend sets `window.BASIS_CONFIG_OVERRIDES` before `live/config.js` loads, or
edits the defaults. The API's `CORS_ALLOWED_ORIGINS` must list the frontend's origin.

### Routes

`#<screen>/<study id>[/<document or asset id>]`, e.g. `#rooms/<study>`,
`#components/<study>`, `#viewer/<study>/<photo document>`, `#asset-detail/<study>/<asset>`.

### Review gates

A study's step bar is **Upload · Appraisal · Rooms · Components · Assets · Results**. The
rooms review approves the grouping (room names, types and floors, merges, a photo moved to
another room); Basis then counts each room's components on the approved rooms (the progress
page says "Preparing components", about a minute for a 33-room study) and the components
review opens with the inventories, the counts and each photo's component page. A study
started before the components review existed (`components_gate: false` on the study) keeps
its five steps and its single rooms review, where all of this happens on one page, as
before. `live/gates.js` holds these rules without the DOM (`web/tests/gates.test.cjs`).
`#components` is a live screen only; mock mode is unchanged.

## What live mode connects

| Screen | API |
|---|---|
| Sign in | Firebase Auth (official JS SDK 10.14.1); the ID token is the API bearer token |
| Firm access | `GET /v1/me` after sign-in decides the screen: no firm and no invitation is "Not a member of a firm yet" (signed in as, sign out / another account); invitations show the join page (`POST /v1/invitations/{id}/accept` or `/decline`; expired, revoked, declined, wrong-account and unverified-email states from the API's problem types). The sidebar names the firm and the role (Admin / Employee / Viewer for the API's owner / engineer / viewer); several firms get a switcher in the top bar, and the studies list and Start a study use the current firm (`org_id`) |
| Team & invitations (`#team`, admins only) | `GET …/orgs/{id}/members` with role change (`POST …/members/{user}/role`) and removal (`DELETE`), `GET …/orgs/{id}/invitations` with create (email and role, each role's scope shown next to it) and revoke (`DELETE`). The last-admin, existing-member and pending-invitation refusals are shown as the API answers them. Anyone else opening `#team` sees "Admin access required" |
| Studies, Library | `GET /v1/studies` (all pages). Search by name, status filters and sort run in the browser |
| Start a study | `POST /v1/studies`; per file: SHA-256 in the browser, `POST …/documents`, `PUT` to the signed URL (with progress), `POST …/complete`; then `POST …/start`. A retry after a failure reuses the study, the confirmed files and the idempotency keys |
| Progress | `GET …/workflow`, polled with backoff (1.5 s doubling to 15 s while unchanged, paused in a hidden tab); `POST …/retry`, `POST …/cancel`. After the rooms review is approved the study is at `components_processing`: "Preparing components" until the components review opens |
| Appraisal review | `GET …/fields` (by section, page, confidence, critical); the cited page drawn inline with PDF.js from `GET …/documents/{id}/content`; `extraction_field` corrections |
| Rooms review | `GET …/rooms` (the rooms and their photos); `room` (rename, retype, merge) and `photo_room` (**Move to another room**: `after: {room_id}`) corrections. Nothing is analyzed again: a rejection regroups and the rooms review opens again |
| Components review (`#components`) | `GET …/rooms` (photos, inventories, `suggested_counts`); `component` (label, count, material, attachment), `room_component` (add a component the inventory is missing) and `photo` (re-analyze, in the room the rooms review approved) corrections. A row whose label the photos show more of than the room counts says "Photos show N" with **Use N**; **Add a component to this room** takes a taxonomy label, a count and optional material and attachment (see "Adding what the pipeline missed"). For a study with the single rooms review all of this is on the rooms page, with the rooms corrections |
| Photo components | Shared `vision-workbench.js`: `GET …/photos/{doc}/objects`, real RLE masks, type/instance selection, overlap cycling, fit/zoom/pan, filmstrip; box/candidate additions through `POST …/objects`, manual removal through `DELETE`. Edits open at Components review, or Rooms review for a legacy study. Scores, model names and IDs stay out of customer copy. |
| Assets review | `GET …/assets`, `GET …/evidence?asset_id=` (photos with their instances highlighted, citations with their document, page, section and verification, `live/citations.js`; review reasons as sentences, `live/review-reasons.js`); `asset` corrections (category, class, recovery period, IRS section) |
| Results | `GET …/result` basis worksheet, reconciliation and allocation; JSON and allocated-row CSV downloads |
| Files | the study's documents; open and download through the API |

### Adding what the pipeline missed

The current photo flow is documented in **Photo component workspace** above. A box
selection is local until **Find outline & add**. That POST saves immediately; candidate
selection is previewed locally before its POST. Model internals remain in the response,
not the engineer-facing copy. Duplicate protection offers Use the existing one / Add anyway.

**Pick a found object** previews the stored candidates under the tap, one at a time.
Tap again or use Previous/Next (or arrow keys) to cycle, then **Use this object**
(`kind: "candidate"` with its id). Enter confirms, Escape clears. A tap over nothing
switches to Draw box.

When the API answers that the selection is an object already counted (`created: false`,
`duplicate_of`), that object is highlighted and a dialog offers **Use the existing one** or
**Add anyway** (the same selection with `allow_duplicate: true`, for two objects one
outline covers).

Counts reach the assets through the components review (the single rooms review for a study
without it): the API suggests, per component, the most
instances counted in one photo of the room (SAM 3's and the ones added) against the room's
count; **Use N**, an edited count, or an added component are corrections like any other,
sent with **Request corrections**. The next revision's assets are built with them (the rules
table classifies an added component by its label, material and attachment), and the assets
review shows the new quantities. Mock mode is unchanged.

Decisions: `POST …/decisions` with the stage and revision. Approve is disabled while
corrections are pending (the API refuses approve-with-corrections); Request corrections
sends them and the stage runs again as the next revision. Every POST and DELETE carries an
`Idempotency-Key`; errors are `application/problem+json` and show their request id.
Entering a review screen re-reads the study (`GET /v1/studies/{id}`) in the background,
so a gate that opened while the app was elsewhere shows its decision bar without a reload.

## Still mock-only in live mode

Marked in the UI with a "Mock" or "Not saved" tag, or hidden:

* The owner overview (`#admin`) and its workload figures (`team-review.js`, `workspace-review.js`)
* Editing presence and leases (the banner is hidden in live mode)
* Study drafts saved in the browser (their leave-page guard is off in live mode)
* Activity history
* Client name and property address on Start a study (shown, not sent)
* Workspace settings and cost sources

## Gaps

Fields the design shows that the API does not have:

* Creator-scoped visibility (`TEAM_HANDOFF.md`): the API shows every member of a firm all of
  the firm's studies; "an engineer sees only their own studies" is not enforced, so the
  design's My studies / Workspace studies split is not shown in live mode.
* Invitation email delivery: none (no mail service). The admin tells the person to sign in.
* Creating or renaming a firm in the app (the operator CLI creates firms); a firm's
  settings page.
* Creator and last-editor display names (studies carry `created_by` ids only).
* Client name, property address and type, a cover photo per study.
* Server-side study search, filters and sort; counts per status.
* Adding a room (a photo can be moved to another existing room, or rooms merged).
* Editing a component's count directly (counts follow the photo's objects), or an
  asset's quantity and cost (asset corrections take category, class, recovery period
  and IRS section only).
* "Reviewed" marks per field, photo or asset (the gate decision is the only review state).
* Field locations on the page (a field has a page number, no box).
* A client-facing PDF report document (not supplied by the result JSON/CSV).
* Cross-study activity history. Per-study timeline and engineer audit are connected in Study history.

API data the design does not show yet:

* Each object's phrase (uncounted candidates are available through Pick a found object).
* `confidence`, `method`, `irs_section`, the takeoff formula and the cost
  row, location factor and cost year (shown on the asset page; the assets table shows few).
* Per-photo localization status and model version (retained as data, hidden from the viewer).
* Room `source`, `group_key` and the study's `room_aliases`.
* Workflow `runs`, `decisions_applied`, `needs_review_stages`, `flagged_photos` by id.
* `failure_reason` beyond the progress page.

## Editing the design

* Mock screens live in the design files; edit them freely.
* Live screens are in `live/live-app.js` and `live/live-review.js` and use the same CSS
  classes. If a mock screen's structure changes (new sections, renamed classes), mirror it
  in its live version.
* `live/live.css` styles only elements the design has no class for (live badges, the
  mask canvas, the add-object bar).
* Bump the `?v=` on the `live/` tags in `index.html` when they change.

## Tests

```sh
node --test web/tests/*.test.cjs            # unit tests (design models, RLE masks, API client, citations, review reasons, firms, manual add)
node web/tests/live-smoke.mjs --origin http://127.0.0.1:3100 --owner-email you@example.com   # against the local stack
```

`live-smoke.mjs` checks that a new account is refused (403 `no-firm`), signs in to the Auth
emulator as the firm admin given by `--owner-email` (an owner in the local database), invites a
throwaway user who accepts, checks CORS for the origin, creates a study,
replays the create with the same idempotency key, uploads a generated PDF and PNG through
signed URLs, reads them back, checks error problems, archives the study and removes the
throwaway user from the firm. It uses only synthetic files and does not start the workflow
unless given `--start`.

### Room and component overview

Rooms and Components open with collapsed room cards containing up to three photo
thumbnails and photo totals. Components also shows inventory totals, the number
of component types, and at most three type/count previews. These aggregate rows
with the same label and reflect pending label/count corrections and room additions,
without summing detections across photos. Generic Other entries remain in the
totals and full inventory; named types take priority in the compact preview. Each card expands independently with its heading button (Enter/Space
works); expansion stays local to that study, revision and page. Expanded cards
retain the full photo gallery and editable inventory. Inventory tables flow with
the page instead of creating nested vertical scroll areas. Missing-component
additions remain available through **Add a component to this room**.

Table wrappers throughout the workspace allow vertical scrolling to continue to
the page. Wide tables keep horizontal scrolling and receive a keyboard focus
target only while they overflow; resizing updates that focus target. Asset and
schedule tables keep all rows in the document rather than a vertical table pane.

### Sidebar hover preview

The slim sidebar previews its labels on mouse hover and contracts on pointer
exit, even if a previous mouse click left focus on a sidebar control. Keyboard
focus keeps labels available until focus leaves; Escape dismisses the preview.
Browser tab/window blur, page hiding, pointer cancellation and mobile breakpoint
changes clear temporary previews and pending timers. An explicitly pinned sidebar
stays open. Closing the account menu also rechecks whether the preview should close.

### Organized asset and appraisal reviews

`review-organization.js` is the read-only presentation model for the live review
pages. `live/review-layout.js` renders their grouping, disclosure and search controls;
`review-layout.css` scopes their styling. The gate decisions, corrections, API calls,
asset detail view and PDF source controller remain in `live/live-review.js`.

Assets start with collapsed recovery-class groups and can also be grouped by room.
Each group includes a room photo when available, asset/review counts and the sum of
priced rows. Missing prices are reported separately; zero is a valid priced cost.
All recovery classes remain visible, including classes with no prices. Search and
status filters narrow the groups, which expand to show every matching row with
flagged assets first. Tables use normal page scrolling, retaining horizontal and
keyboard scrolling on narrow screens. Expanded groups persist per study/revision.

Appraisal fields start with property identity, location, room counts, living area,
year built and property/purchase values. Remaining fields are grouped by subject
in collapsible sections. Every API field appears once, including unknown sections.
Search includes field names, values and staged corrections. The key-fields filter
uses the catalogue's `critical` flag; Needs a look uses the existing confidence
threshold. Searching, filtering and toggling sections update only the left pane;
selecting a field keeps the original source-page navigation and highlight behavior.
The source pane starts alongside the review title, with only page position and an
Expand control above the document. Successful source matches are announced without
repeating the field; missing or ambiguous matches remain visible. Read-only status
appears under the study name. The field footer advances to the next matching section
(and back to the overview from the last), selecting its first field and keeping
keyboard focus in the field browser. Editable reviews retain Correct value.

Component cards on the live Components page use `live/component-previews.js` to
show the same counted-object overlays as the photo workspace, including compact
thumbnails. Previews use the existing revision-keyed photo-objects API/cache, lazy
loading and at most three concurrent requests. The shared mask layer runs without
pointer handlers in thumbnails; review links still open the full interactive viewer.
Rooms and Uploaded files retain original photos. Missing outlines leave the photo
visible with an unavailable/empty state on expanded previews.


Live study navigation starts at Study progress for all existing-study links on Home,
Library and Activity. Home shows attention/in-progress queues and recent studies;
Library retains the searchable status-filtered catalogue. The overview has a compact status bar above the heading and a detailed
ordered list of logical steps, with availability from `gates.js`, current actions,
creator (resolved by user ID where available) and timestamps. Review pages retain
the horizontal step bar; their study breadcrumb returns to progress. Legacy studies
keep their five-step flow. Start/retry/cancel and decision behavior stay in the live
controller. Client details are not fabricated when the API has no stored values.

Activity uses actual study creation and latest-update timestamps. It is not a full
audit trail and does not attribute updates to an actor the API has not identified.
Results (formerly Schedule) now reads the basis, reconciliation and allocation from
`/result`. It preserves the server recovery-class and building-system totals and flags
unpriced assets. PDF report generation remains separate work.


### Room placement management

`room-placement.js` projects and diffs room drafts; `live/room-placement.js` provides drag/drop, create/edit/remove, the unmatched-photo tray and Undo for the two-gate Rooms review. All photos must be placed and changes saved before approval. Save uses the existing revision-correction decision API; the isolated backend implementation and contract are in the room-management worktree's `docs/ROOM_MANAGEMENT.md`. Original uploads are retained. `tests/fixtures/room-placement.html` exercises the tray with synthetic data and no API writes. Native selects in these dialogs use `data-native="true"` to retain keyboard and screen-reader access inside the modal.

### Live results, provenance and study records

`live/result-view.js` renders backend amounts and source evidence; `live/live-results.js`
connects Results, Concealed systems, Study history, owner-facing Study spend, and Cost
indices. Study progress links to these records. Results now uses `/result` rather than
calling an asset RCN sum “allocated cost.” Missing amounts remain unknown; allocation
withholding and legacy results without reconciliation are explicit. Tables use the
page's vertical scroll. JSON and allocated-row CSV are downloads, not a PDF report.

`live/intake-view.js` edits basis inputs through the existing PUT contract, preserving
drafts on errors and checking the latest intake before saving. `live/error-help.js`
uses the generated 221-code catalogue; review codes are deduplicated and are not
positionally paired with reason strings. Regenerate it with
`python3 web/scripts/sync-error-catalogue.py /path/to/current/backend/repo/backEnd`.

Cost-index entries are append-only, require a citation, and retain the same idempotency
key for an identical retry. The API store is deployment-wide, shared by firms. Saving a
value does not automatically reprice assets. The UI's owner-only spend visibility does
not replace API authorization. See `docs/frontend-results/CLAUDE_HANDOFF.md` for these
remaining backend contracts and the older local server's integration limit.

Run `node --test web/tests/*.test.cjs`. The synthetic browser fixture is
`tests/fixtures/results.html`, with `results-mobile.html` for a 390px viewport;
it never writes to the live API. See `docs/frontend-results/QA.md` for observed checks.


### Internal developer health

Open `http://127.0.0.1:3100/developer/`. The live app's local development toolbar also
links to **System health**. This standalone page works independently of the app's
sign-in / firm gate so API outages remain visible. It shows 25 infrastructure, model,
provider and processing checks and a horizontal workflow map with both parallel
branches and all four engineer checkpoints. Workflow service chips open their health
rows. Search, attention filtering, keyboard disclosures, timestamps and refresh controls
are included. No paid inference is initiated by this page.

The public `/health` response supplies API, database and Temporal observations.
Everything else remains unknown until an authenticated operator feed is connected.
`GET /v1/ops/health` is a **proposed** contract, not an existing backend implementation;
see [`developer/backend-contract.md`](developer/backend-contract.md) for Claude's handoff.
Missing, malformed, expired, configuration-only and denied results never become green.
A check turning green proves that reported check only, not the full workflow.

The diagnostics token can be sent only to the trusted `BASIS_CONFIG_OVERRIDES.apiBase`
(or the default `http://127.0.0.1:8000`). An `api` URL parameter alone cannot change the
trusted recipient. Backend operator authorization is required; hiding a link is not
access control. The page sends read-only GETs, refreshes every 30 seconds, and pauses
polling while hidden. It does not use provider API keys.

Each workflow step's dialog ends with **Run records**: the evaluation registry's runs for the
step's components (`developer/workflow-content.json` maps them), read from the operator route
`GET /v1/ops/eval-runs` (`docs/api/EVAL_RUNS.md`) once diagnostics are connected, one table per
label file and split, every figure with its source and "Not recorded" for what the files do
not say, and the manifest, report, ledger and console log downloaded through the authenticated
client. A host without evaluation data says so; nothing about runs is bundled in `web/`.

`tests/fixtures/developer-health.html` runs the actual page/controller with isolated
synthetic responses for healthy, partial failure, stale, missing endpoint, denied and
unreachable states. Its 390px toggle checks the narrow layout. Details and verification
are in `../docs/developer-health/QA.md`.
