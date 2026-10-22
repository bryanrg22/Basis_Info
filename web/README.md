# Basis web frontend

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
`#viewer/<study>/<photo document>`, `#asset-detail/<study>/<asset>`.

## What live mode connects

| Screen | API |
|---|---|
| Sign in | Firebase Auth (official JS SDK 10.14.1); the ID token is the API bearer token |
| Firm access | `GET /v1/me` after sign-in decides the screen: no firm and no invitation is "Not a member of a firm yet" (signed in as, sign out / another account); invitations show the join page (`POST /v1/invitations/{id}/accept` or `/decline`; expired, revoked, declined, wrong-account and unverified-email states from the API's problem types). The sidebar names the firm and the role (Admin / Employee / Viewer for the API's owner / engineer / viewer); several firms get a switcher in the top bar, and the studies list and Start a study use the current firm (`org_id`) |
| Team & invitations (`#team`, admins only) | `GET …/orgs/{id}/members` with role change (`POST …/members/{user}/role`) and removal (`DELETE`), `GET …/orgs/{id}/invitations` with create (email and role, each role's scope shown next to it) and revoke (`DELETE`). The last-admin, existing-member and pending-invitation refusals are shown as the API answers them. Anyone else opening `#team` sees "Admin access required" |
| Studies, Library | `GET /v1/studies` (all pages). Search by name, status filters and sort run in the browser |
| Start a study | `POST /v1/studies`; per file: SHA-256 in the browser, `POST …/documents`, `PUT` to the signed URL (with progress), `POST …/complete`; then `POST …/start`. A retry after a failure reuses the study, the confirmed files and the idempotency keys |
| Progress | `GET …/workflow`, polled with backoff (1.5 s doubling to 15 s while unchanged, paused in a hidden tab); `POST …/retry`, `POST …/cancel` |
| Appraisal review | `GET …/fields` (by section, page, confidence, critical); the cited page drawn inline with PDF.js from `GET …/documents/{id}/content`; `extraction_field` corrections |
| Rooms review | `GET …/rooms` (photos, inventories, `suggested_counts`); `room` (rename, retype, merge), `component` (label, count, material, attachment), `room_component` (add a component the inventory is missing) and `photo` (re-analyze) corrections. A row whose label the photos show more of than the room counts says "Photos show N" with **Use N**; labels the photos show and the inventory lacks get **Add N to the room**; **Add a component to this room** takes a taxonomy label, a count and optional material and attachment (see "Adding what the pipeline missed") |
| Photo components | `GET …/photos/{doc}/objects`; SAM 3 masks decoded in the browser (`live/rle.js`) and drawn on a canvas (`live/mask-layer.js`): **Spotlight** (default: one component, glow, numbered pins), **Show all** (every counted instance, tinted by component), one instance (score, size, where its outline came from, delete). **Add object** (`live/manual-add.js`): **Draw a box** (the default) or **Tap it** to preview and cycle the candidates under the tap. Delete: `DELETE` (manual objects only; the API keeps the model's) |
| Assets review | `GET …/assets`, `GET …/evidence?asset_id=` (photos with their instances highlighted, citations with their document, page, section and verification, `live/citations.js`; review reasons as sentences, `live/review-reasons.js`); `asset` corrections (category, class, recovery period, IRS section) |
| Schedule | totals by recovery class from `GET …/assets`; a CSV of the table |
| Files | the study's documents; open and download through the API |

### Adding what the pipeline missed

On a photo, **Add object** opens with **Draw a box**: drag a box around the object and
`POST …/objects` (`kind: "box"`) sends it. The API takes the stored SAM 3 instance of that
component the box matches (IoU 0.5), else asks SAM 3 for the box (one call, a few seconds:
the add bar shows "Outlining it with SAM 3…"), else stores the box alone. The instance panel
says which: **Outline from SAM 3**, **Outline from SAM 3 (your box)** or **Box only** with
the reason (no match, the call failed or timed out). Measured on the real photos
(`backEnd/evals/manual_add.py`): a box outlines the right object for 42 of the 43 objects the
pipeline missed at once, and the 43rd after "Add anyway"; never a wrong one.

**Tap it** shows the instances SAM 3 stored under the tap, highest score first, one at a
time ("1 of 3", score, counted or not). Tap the same spot again or press → / ← to see the
next, then **Use this one** (`kind: "candidate"` with its id); Enter confirms, Escape clears.
A tap over nothing switches to Draw a box.

When the API answers that the selection is an object already counted (`created: false`,
`duplicate_of`), that object is highlighted and a dialog offers **Use the existing one** or
**Add anyway** (the same selection with `allow_duplicate: true`, for two objects one
outline covers).

Counts reach the assets through the rooms review: the API suggests, per component, the most
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
* Moving a single photo to another room (only whole-room merges), adding a room.
* Editing a component's count directly (counts follow the photo's objects), or an
  asset's quantity and cost (asset corrections take category, class, recovery period
  and IRS section only).
* "Reviewed" marks per field, photo or asset (the gate decision is the only review state).
* Field locations on the page (a field has a page number, no box).
* Report or export of the schedule (no endpoint): the CSV is built in the browser.
* Activity history with attribution and revision diffs (`runs` and `latest_reviews` exist).

API data the design does not show yet:

* Uncounted SAM 3 candidates per photo (the tap-to-add pool), each object's phrase.
* `confidence`, `method`, `irs_section`, the takeoff formula and the cost
  row, location factor and cost year (shown on the asset page; the assets table shows few).
* Per-photo localization status and model version (shown as a caption only).
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
