# Property pictures: capturing views of the property inside Basis (2026-11-23)

Design note for the property capture (built on branch `claude/property-capture`). The API is in
`docs/api/PROPERTY_CAPTURE.md`, the measurements in EVAL_REPORT 98, and what the app still needs
in `web/FRONTEND_BACKLOG.md` section 18. Background is in the research note
`REMOTE_INSPECTION_AND_MEASUREMENT.md` (PR #176), sections 4 and 11.

## 1. Answer first

1. **What it does.** As soon as a study has an address, a separate worker (`python -m
   basis.capture`) opens the property in a headless browser. It takes six dated views and keeps
   them with the study's files:
   - two straight down: a close one to measure on, and a wide one for the lot and its setting;
   - four tilted ones, one for each side of the building.

   Each view carries its camera, and the top-down ones carry their metres per pixel, so a shape
   traced on them becomes square feet or linear feet (through PR #178's `measurement`).
2. **It is gentle and stops at the first sign of trouble.**
   - One capture at a time across every worker. A random 8 to 20 s between navigations.
   - Caps: 6 page loads an hour, 30 a day, 120 camera moves an hour.
   - The camera moves without reloading the page, so a whole property costs one page load.
   - Any block signal (the unusual-traffic page, a reCAPTCHA, a consent or sign-in wall, HTTP
     429 where it matters) opens a breaker. It stays open an hour, doubling each time it trips
     again in a row, up to a day. Every capture waits for it and resumes on its own.
3. **Every failure is tracked the way user errors are.** Each kind has its own code. It shows
   up in the logs, on the study's timeline, and in one counted record per code for operators,
   with evidence attached (screenshot, HTML and page record). A breaker trip raises one alert,
   and `GET /v1/ops/health` shows the state.
4. **Verified live** on one property (EVAL_REPORT 98): seven page loads in all and no block
   signal.
   - The real runs found and fixed two defects that the fake-browser tests could not catch:
     - an Escape key press after a camera move sent the viewer to its projects page;
     - a point loaded without its ground altitude placed every camera below the ground.
   - Both found point and engineer point now give the right scale.
   - The pixel size reads 1.9 to 2.6 % finer than the viewer's own scale bar.
5. **Added by EVAL_REPORT 99** (section 9 below):
   - **History**: the dated history of the straight-down view, stepped with the viewer's own
     arrow (one camera move a date, no page load). The capture keeps the newest date, the
     dates around the placed-in-service date and the leaf-off dates, at most 6.
   - **The imagery date of every view**, read off the status bar (tesseract, 56 of 56 views
     right), and a **leaf-off label** by the month and the latitude.
   - **Clean views**: no toolbar, buttons or status bar in a stored view; same centre, same
     metres per pixel.
   - **Street View**: built, but the service refuses a headless browser (panorama lookup 500,
     tiles 403), so it is off and records its own code when tried.
6. **Not built.** A Linux render of the viewer (the image exists; it has not run the viewer).

## 2. How it works

```
engineer  PUT .../property/pin {address}          -> property_pins (version 1)
          POST .../property/captures              -> property_captures (queued) -> workflow
capture worker (own task queue, one activity at a time, one lease across workers)
          ask the governor (breaker, caps) -> wait the spacing -> one page load (a search)
          -> check for a block -> wait for the imagery -> close the panel and the tips
          -> place the found point on the pin (unconfirmed)
          -> for each planned view: governor, spacing, move the camera (no load), check,
             wait, screenshot, refuse a blank/black/white frame, store with its camera
          -> completed (breaker closed, last success)
on a pause the workflow sleeps on a durable timer until the time the activity named, then runs
it again; the views already stored are kept and only the missing ones are taken
```

- **Why a workflow of its own.** The capture is a `PropertyCaptureWorkflow` on
  `CAPTURE_TASK_QUEUE`, not a step of the study workflow:
  - the study workflow needs no patch;
  - a long pause is a Temporal timer, not a worker holding a slot;
  - the browser lives only in the capture worker's image.
- **The breaker.** It follows the open / half-open / closed circuit breaker of Hystrix and
  Resilience4j, with an exponential open time. The shared state is one Postgres row read
  `FOR UPDATE`, so two workers seeing a signal at once trip it once.
- **The caps.** They count a navigation log in the database, so a restarted worker starts no
  new budget.

## 3. The views and what each is for

| View | Camera | For |
|---|---|---|
| `aerial-close` | Straight down, 120 m (about 8.4 cm a pixel) | Measuring paved areas, walks, patio, fence |
| `aerial-wide` | Straight down, 350 m (about 24.5 cm a pixel) | The lot in its setting: access, parking, neighbours |
| `side-south`, `-west`, `-north`, `-east` | Tilted 60°, 150 m, looking N, E, S, W | Reading each face of the building and the site. Not for measuring: the tilt has no single scale, and `pixel_size_m` is null |

Added by EVAL_REPORT 99 (section 9):

| View | Camera | For |
|---|---|---|
| `history-YYYY-MM-DD` | `aerial-close`'s, on that imagery date | What existed at the purchase date; before and after a renovation; a leaf-off picture to measure paved areas on |
| `street-front` | At the nearest panorama, aimed at the house | Reading the front. Off: refused headless |

With clean views (the default) every range above is lengthened by 1240 / 900 so the stored
1440 x 900 band keeps the same ground and metres per pixel: `aerial-close` is taken at 165.3 m.

## 4. How failures are tracked, reusing what exists

| Today's system | Reused how |
|---|---|
| The `BASIS-<AREA>-<nnn>` catalogue | New area `CAPTURE`, codes 001 to 017, plus `BASIS-ALERT-005`; 018 to 021 for the parts EVAL_REPORT 99 added (history unavailable, no panorama, panorama refused, date unread). `docs/ERROR_CODES.md` is regenerated from it. |
| Structured logs with `error_code` | `capture signal`, `capture paused`, `capture failed`, `capture breaker opened` |
| The study timeline (`study_events`) | Events `capture.requested`, `.started`, `.paused`, `.failed`, `.completed`, `.part_failed` (a part that did not work while the capture went on), `property.pin_set`, `property.pin_placed`. The code goes in `code`; the engineer's sentence, the resume time, the part and the evidence go in `detail`. |
| Client-error issues (EVAL_REPORT 65) | The same grouping (one row per issue, with count and first and last seen), in its own table `capture_incidents`, keyed by code. See below for why not reuse the table itself. |
| Operator alerts (`raise_once`, outbox, relay) | Kind `capture_blocked`, keyed by the trip number and the time it opened: exactly one per trip. Rendered by `notify/render.py`. |
| `GET /v1/ops/health` | A `property_capture` member: on or off, the worker, the breaker, usage against the caps, the last success. |

Why not reuse the `client_errors` table itself: its `user_id` is `NOT NULL` and points at a user,
its `kind` is a closed list of browser events, and its retention is a user's 180 days. A capture
failure has no user.

The evidence (screenshot, HTML, `page.json`) is stored with the capture's files under the study's
storage prefix. The retention purge removes it with the study, and it never enters the
repository.

## 5. What the engineer reads

Plain sentences that never name the imagery provider, for example "Property pictures are paused;
they will resume on their own." The time it resumes is `resume_at`, which the app shows in the
engineer's own time zone (the server does not know it). For example: "Property pictures are
paused until 3:00 pm; they will resume on their own."

## 6. Decisions (Bryan's, recorded 2026-11-23)

1. **Google's terms.** The research note quotes Google's terms as forbidding three things:
   tracing on its imagery (3.2.3(c)), storing it (3.2.3(a)), and putting it in a generated
   document. It adds that Google Earth may not be embedded in another app.
   - **Bryan knowingly accepted, for the MVP demo only,** capturing and storing Google Earth
     views and letting engineers trace on them.
   - The named fallback, when this goes beyond the demo: measure on public-domain imagery
     (state and county aerial programs, USGS, NAIP; research note section 12). Google views are
     then only for looking, shown and not stored.
   - Nothing in the code depends on the source, so the fallback changes only where the
     top-down views come from.
2. **The source text** shown with each view stays the placeholder `CAPTURE_SOURCE_TEXT`
   ("Aerial imagery viewed online; date as the imagery shows it"). It is his to change, and it
   must not name the provider. The provider appears only on the operator side: the evidence and
   the incident's address.
3. **Captures are per study only.** One property captured for two studies is captured twice.
   Sharing across studies, or across firms, is a tenancy question left for later.

## 7. How the Property area changes how an engineer uses Basis (needs for the design)

These are needs for Bryan's design, not a screen. The API side is FRONTEND_BACKLOG 18.

1. **Where it appears in the study flow.** At intake, as soon as there is an address. The
   engineer types or confirms it (from the appraisal's subject address once the intake reads
   it), and the capture starts in the background. Nothing waits on it: the appraisal, photos and
   gates go on, and the Property area fills in when the views arrive, usually within two
   minutes.
2. **Confirming the pin before anything is measured.** The first thing the engineer sees is the
   point the search found, on the wide aerial, with the matched address.
   - They confirm it is the right house, or drag it to the right building and confirm.
   - Until it is confirmed, the measuring views should say so and not offer measuring.
   - Moving the pin makes the existing views stale and offers to take them again.
3. **The capture's status and pauses.**
   - Queued, taking pictures, ready, or paused with "they will resume on their own" and the
     local time.
   - A failure says what to do in one sentence. A missed address, for example, says to correct
     it or drop the pin.
   - The engineer never needs to know about breakers or caps.
4. **The views and what each is for.**
   - **Today's close aerial is the one to measure on.** It shows its pixel size and, once the
     date is read, its imagery date.
   - **The wide aerial** is for the site in context.
   - **The four side views** are for reading the building's faces and roof.
   - **History:** see item 7.
   - **Street level:** see item 8.
5. **From a "needs measurement" item straight to the aerial.** A row the takeoff could not size
   (a driveway, walks, a patio, a fence) links to the close aerial. The engineer draws the shape
   there, and the area or length lands on that row through PR #178's `measurement`:
   - in `lonlat`, computed from the view's centre, `pixel_size_m` and heading 0;
   - with the view's source, its imagery date as `captured_on` (or nothing, which raises
     `measure:IMAGE_UNDATED`), and `pixel_size_m`.

   The row shows the quantity, the image and its date beside it, and the engineer stays the
   owner of the number (EVAL_REPORT 78 and the engineer-overrides rule).
6. **The audit trail.** Every view keeps its camera, pin version, capture time and sha256, and
   the timeline records who set the pin and who confirmed it. "Show how you measured it if the
   question comes up in an audit" (research note 11.2) is then answered inside Basis.
7. **History in a study** (EVAL_REPORT 99). The Property area shows the dated top-down views
   as a strip of dates, each with its leaf label, beside today's close aerial.
   - **The placed-in-service date.** The view nearest the intake's placed-in-service date (or
     the appraisal's effective date) is marked "at placed in service". It answers "what was
     there when it was placed in service": a patio or a fence that appears only later is not
     part of the purchase.
   - **Before and after.** Two dates side by side (or one over the other with a slider) show
     a renovation: a new drive, an added deck, a demolished shed. The engineer decides what
     the dates mean; Basis only shows them with their dates.
   - **Measuring.** `measure_on` names the suggested view: the newest leaf-off one, where trees
     do not hide the pavement. Measuring on it means the quantity is as of that date, which
     the row shows (`captured_on` is the view's `imagery_on`). If the property changed after
     that date, the engineer measures on a newer view instead; the choice is theirs.
   - **Undated or unknown.** A view whose date could not be read says "undated" and is never
     suggested; a 31 December date is shown as "year only".
   - **Nothing found.** When history could not be taken the capture's note says so in one
     sentence, and today's views are unaffected.
8. **Street level in a study** (when it works; off today). One picture of the front from the
   nearest street, with its date and "looking south from the street" from its heading. It is
   for reading the front (materials, entrances, the drive's apron), never for measuring.
   Today the capture's note reads "Street-level pictures are not available yet."

## 8. Verified and not verified

**Verified:**
- 54 unit tests of the runner, signals, camera, governor and health with a fake browser, plus
  2 tests of the alert email.
- mypy, ruff, the catalogue and the OpenAPI document.
- Four live runs on one property (EVAL_REPORT 98):
  - the found point and the engineer's point;
  - six clean views each;
  - the real page's HTML raised no signal;
  - the survey endpoint's 429 was ignored;
  - scale checked against the viewer's bar on four top-down views.

**Not run** (Docker unavailable this session):
- the Postgres store and API integration tests;
- the migration test and `alembic check`;
- the worker against a real Temporal;
- the Linux render.

**Verified since (EVAL_REPORT 99):**
- the Postgres store, the routes and migrations 0036 and 0037 on a local Postgres 18 (no
  Docker);
- the breaker against a real headless browser on five block pages served locally;
- history, dates, leaf labels, clean views and street level's refusal on the probe property
  (two full captures, Metal and SwiftShader).

**Not verified:**
- a block signal from the real site (none occurred; the breaker is tested with the fake
  browser and with locally served block pages in a real browser);
- the first-visit notice on a fresh profile (dismissed once in probe 2, its close button's name
  not confirmed);
- the pixel size against a length measured on the ground.

## 9. History, imagery dates, leaf-off, street level and clean views (EVAL_REPORT 99)

1. **History.** Earth's historical imagery control opens a row of dated dots; its "Older
   images" arrow steps to the next older date and is disabled at the oldest. Two ways were
   compared on the probe property in one page load:
   - **the arrow**, one camera move a date: it listed all 13 dates (the 12 found by hand and
     a 1985 one) in 12 presses, and the page address then carried each date exactly;
   - **setting the date in the page address** (`history.pushState` and `popstate`, as the
     camera moves): one move a date as well, and it renders a date it is given, but it cannot
     list the dates, and an address asking for a date the viewer does not have shows the
     newest imagery at or before it (asked 2019-01-01, showed 2017-04-22) while the address
     and the header keep the date asked for.
   Shipped: the arrow. A capture walks every date once (13 moves here), screenshots each, and
   keeps the planned ones; nothing is loaded again.
2. **The imagery date.** Read off the status bar by tesseract on the strip scaled up four
   times: 56 of 56 views right (EVAL_REPORT 99's table), about 0.06 s each. A history date
   must also agree with the date in the page address, so a misread digit cannot become a
   wrong date; it then stays null with the reason.
3. **Leaf-off.** The month against the latitude (shipped) agreed with the hand check on 6 of 6
   dates; a green-pixel share alone on 4 of 6. One property is a small key; the month rule's
   autumn edge was written knowing the key. The share is stored beside the label as
   evidence. A 31 December date is "year only": the viewer's 2005-12-31 picture is in full
   leaf.
4. **Clean views.** The viewer offers no way to hide all its interface (only the menu bar).
   The viewport is made 170 px taller above and below and the stored view is the band
   between: hovering the viewport's centre reads the camera's own point in the status bar at
   900 and at 1130 px tall, so the centre does not move, and the metres per pixel follow the
   whole viewport's height (hover grid: 0.0842 to 0.0846 m/px against 0.0841 by the formula
   at 900 px, 0.0671 to 0.0676 against 0.0670 at 1130 px).
5. **Street level.** Every headless attempt was refused: the panorama lookup
   (`/maps/photometa/si/v1`) answered 500, the tiles 403, in both of Chromium's headless modes
   and in Earth's own street-level mode, while WebGL rendered Earth fine in the same browser.
   It is built behind `CAPTURE_STREET_VIEW` (off) and records `BASIS-CAPTURE-020` with its
   evidence when tried. Making it work would mean presenting the browser as something it is
   not; that is Bryan's decision (section 6 of EVAL_REPORT 99).
