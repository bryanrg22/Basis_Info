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
   flagged `citation_unverified`). Load the six reference PDFs once (about 16 minutes on
   CPU, no API cost), then restart the worker (`docs/deploy/STAGING.md` section 1.8):

   ```sh
   docker compose … exec worker python -m basis.retrieval.ingest --pdf=/path/IRS_PUB_946.pdf --doc-id=IRS_PUB_946 …
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
| Studies, Library | `GET /v1/studies` (all pages). Search by name, status filters and sort run in the browser |
| Start a study | `POST /v1/studies`; per file: SHA-256 in the browser, `POST …/documents`, `PUT` to the signed URL (with progress), `POST …/complete`; then `POST …/start`. A retry after a failure reuses the study, the confirmed files and the idempotency keys |
| Progress | `GET …/workflow`, polled with backoff (1.5 s doubling to 15 s while unchanged, paused in a hidden tab); `POST …/retry`, `POST …/cancel` |
| Appraisal review | `GET …/fields` (by section, page, confidence, critical); the cited page drawn inline with PDF.js from `GET …/documents/{id}/content`; `extraction_field` corrections |
| Rooms review | `GET …/rooms` (photos, inventories); `room` (rename, retype, merge), `component` (label, material, attachment) and `photo` (re-analyze) corrections |
| Photo components | `GET …/photos/{doc}/objects`; SAM 3 masks decoded in the browser (`live/rle.js`) and drawn on a canvas (`live/mask-layer.js`): **Spotlight** (default: one component, glow, numbered pins), **Show all** (every counted instance, tinted by component), one instance (score, size, delete). **Add object**: tap (`POST` a point, matched to what SAM 3 found) or draw a box. Delete: `DELETE` (manual objects only; the API keeps the model's) |
| Assets review | `GET …/assets`, `GET …/evidence?asset_id=` (photos with their instances highlighted, citations with verification); `asset` corrections (category, class, recovery period, IRS section) |
| Schedule | totals by recovery class from `GET …/assets`; a CSV of the table |
| Files | the study's documents; open and download through the API |

Decisions: `POST …/decisions` with the stage and revision. Approve is disabled while
corrections are pending (the API refuses approve-with-corrections); Request corrections
sends them and the stage runs again as the next revision. Every POST and DELETE carries an
`Idempotency-Key`; errors are `application/problem+json` and show their request id.

## Still mock-only in live mode

Marked in the UI with a "Mock" or "Not saved" tag, or hidden:

* Team & access, owner overview, invitations, roles (`team-review.js`, `TEAM_HANDOFF.md`)
* Editing presence and leases (the banner is hidden in live mode)
* Study drafts saved in the browser (their leave-page guard is off in live mode)
* Activity history
* Client name and property address on Start a study (shown, not sent)
* Workspace settings and cost sources

## Gaps

Fields the design shows that the API does not have:

* The signed-in user's own record (no `GET /v1/me`): name, role, workspace. The studies
  list cannot say "created by you", and owner/engineer views cannot be chosen.
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
* `review_reasons`, `confidence`, `method`, `irs_section`, the takeoff formula and the cost
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
node --test web/tests/*.test.cjs            # unit tests (design models, RLE masks, API client)
node web/tests/live-smoke.mjs --origin http://127.0.0.1:3100   # against the local stack
```

`live-smoke.mjs` signs in to the Auth emulator, checks CORS for the origin, creates a study,
replays the create with the same idempotency key, uploads a generated PDF and PNG through
signed URLs, reads them back, checks error problems and archives the study. It uses only
synthetic files and does not start the workflow unless given `--start`.
