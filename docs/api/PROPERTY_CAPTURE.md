# Property pictures: `/v1/studies/{id}/property`, `GET /v1/ops/capture-incidents`

Dated views of a study's property, taken in the background by a worker of their own
(`python -m basis.capture`) and kept with the study's files. The design and the engineer's use
are in `docs/review_2026-10-15/PROPERTY_CAPTURE.md`; the measurements in EVAL_REPORT 98 and 99
(history, imagery dates, leaf-off, street level, clean views). Off by
default (`PROPERTY_CAPTURE=false`); `GET /v1/capabilities` says `property_capture`.

## 1. The pin

`GET /v1/studies/{id}/property` (viewer) answers the pin, the newest capture with its views,
and `enabled`. The `ETag` is the pin's version (`"0"` and `pin: null` while none).

`PUT /v1/studies/{id}/property/pin` (engineer) writes the whole pin:

| Member | Meaning |
|---|---|
| `address` | What the capture searches for when there is no point (1 to 300 characters). |
| `lat`, `lon` | The point every view is centred on. Both or neither. |
| `confirmed` | The engineer's word that the point is on the right house. Needs a point. |

Rules: a point that differs from the stored one is the engineer's (`source: engineer`); no
point clears it and the next capture searches the address; a new address with a point the
search had found clears that point too; moving the point withdraws an earlier confirmation
unless the same request confirms it; an unchanged document writes nothing. `If-Match` with the
`ETag` writes only while it is current: 412 `BASIS-CAPTURE-016` with the current `ETag`
otherwise; `*` only over a saved pin. Each write records `property.pin_set` (engineer, before
and after) on the timeline.

The capture's search fills a pin that has only an address: `source: search`,
`place_label` (what the search matched), `confirmed: false`, version + 1, and the
`property.pin_placed` event. It never moves a point that is already there.

## 2. Captures

`POST /v1/studies/{id}/property/captures` (engineer), body `{"force": false}` (optional):

| Answer | When |
|---|---|
| 202 with the capture, `status: queued` | Accepted; the workflow `property-capture-{capture id}` was started. |
| 200 with the recent capture | A capture taken at the pin's current point (within 1 m) succeeded within `CAPTURE_RECENT_HOURS` (12) and `force` is false. Confirming the pin or editing its address without moving the point does not call for a new capture. |
| 409 `capture-off` (`BASIS-CAPTURE-013`) | `PROPERTY_CAPTURE` is off. |
| 409 `capture-in-progress` (`BASIS-CAPTURE-014`) | One is queued, waiting or running (one per study, a unique index). |
| 422 `capture-no-address` (`BASIS-CAPTURE-015`) | No pin. |
| 503 (`BASIS-CAPTURE-017`) | The workflow did not start; the capture is marked failed so nothing is left queued. Repeat. |

The capture row is committed before the workflow starts, as `POST .../start` does. A pause is
not a refusal: a capture accepted while the breaker is open waits and resumes on its own.

A capture (`CaptureOut`):

| Member | Meaning |
|---|---|
| `status` | `queued`, `waiting`, `running`, `succeeded`, `failed`. |
| `code` | The `BASIS-CAPTURE-<nnn>` of the last pause or of the failure (docs/ERROR_CODES.md). |
| `message` | What to tell the engineer. Never names the imagery provider. |
| `resume_at` | While `waiting`: when it runs again. Show it in the engineer's own time. |
| `pin_version` | The pin it was taken around. |
| `views` | The stored views. |
| `notes` | Parts that did not work while the capture went on: `[{part, code, message}]`, `part` one of `history`, `street`, `date`. The capture still succeeded; `message` is the engineer's sentence. |
| `measure_on` | The id of the view suggested for measuring paved areas: the newest leaf-off top-down view, else the newest dated top-down view, else null. The engineer may pick another. |

`message` by state: queued "Property pictures are queued."; running "Taking property
pictures."; waiting on a pause "Property pictures are paused; they will resume on their own.";
waiting for another capture "Waiting for another property's pictures to finish; this one is
next."; succeeded "Property pictures are ready."; failed, per code (no place: "The address did
not lead to a place. Check the address or drop the pin on the house, then take the pictures
again."; blank, timeout, crash, gave up: take them again later).

`GET .../property/captures?limit=20` (viewer, newest first, at most 50) and
`GET .../property/captures/{capture_id}` (viewer).

## 3. Views

| Member | Meaning |
|---|---|
| `kind`, `label` | `aerial` (`aerial-close`, `aerial-wide`), `oblique` (`side-south`, `side-west`, `side-north`, `side-east`: the face seen), `history` (`history-YYYY-MM-DD`: a dated top-down view with `aerial-close`'s camera, so the same georeference), `street` (`street-front`: a ground-level picture; off by default, see section 7). |
| `lat`, `lon`, `ground_alt_m` | The point looked at (the view's centre) and its altitude. |
| `range_m`, `fov_deg`, `heading_deg`, `tilt_deg` | The camera: distance to the point, vertical field of view, compass heading it looks along, tilt from straight down. |
| `pixel_size_m` | Metres per pixel at the centre, top-down views only: `2 * range * tan(fov / 2) / height`. Null for tilted views, which must not be measured on. EVAL_REPORT 98: 2.0 to 2.6 % finer than the imagery's own scale bar; not checked against a measured length. |
| `imagery_date`, `imagery_on` | The imagery date as the viewer's status bar shows it (`M/D/YYYY`) and as a date; null when it could not be read. |
| `imagery_date_note` | How it was read (`status-bar`; a history date adds `, matches the address`), or why it was not (no text reader installed, nothing readable, the status bar and the page address disagreed). |
| `leaf` | `leaf-off`, `leaf-on` or `unknown` on the imagery date, by the month and the property's latitude (EVAL_REPORT 99). `unknown` between seasons, for a date known only to the year (31 December), outside 37 to 49.5° N, or undated. |
| `green_share` | Top-down views: the share of green vegetation pixels (0 to 1), supporting evidence for `leaf`, not the label itself. |
| `camera_lat`, `camera_lon` | Street-level views: where the camera stood; `lat`, `lon` are the house it looks at and `heading_deg` points from one to the other; `tilt_deg` 90 is the horizon. |
| `source` | The provenance line (`CAPTURE_SOURCE_TEXT`). |
| `pin_version`, `stale` | `stale`: taken at another point than the pin's current one (more than 1 m away). A confirmation or an address edit raises the pin's version without making views stale. |
| `width`, `height`, `sha256`, `captured_at` | The image: 1440 x 900, free of the viewer's interface (EVAL_REPORT 99: a taller viewport cropped symmetrically, so the centre and the metres per pixel are unchanged). Views taken before carry the viewer's toolbar and status bar. |

`GET .../property/views/{view_id}/image` (viewer): the PNG, `ETag` its sha256 (304 on
`If-None-Match`), `Cache-Control: private, max-age=3600`. The images live under
`gs://<bucket>/studies/<id>/captures/<capture id>/`, so the retention purge removes them with
the study's other files; after a purge the image answers 404.

To measure on a top-down view (FRONTEND_BACKLOG 18): convert a pixel `(x, y)` to ground metres
from the centre `(width / 2, height / 2)` with `pixel_size_m` (east = `(x - width / 2) * s`,
north = `(height / 2 - y) * s` for heading 0), then to lon/lat around the view's `lat`, `lon`;
send the shape as PR #178's `measurement` with the view's id, `pixel_size_m` and
`imagery_date` as its source.

## 4. Failures, pauses and what they leave (operators)

Every pause and failure has its own code (area `CAPTURE`): a log line from the capture worker
with `error_code`; a `capture.paused` or `capture.failed` event on the study's timeline (code,
the engineer's sentence, `resume_at`, the signal, and the evidence prefix); a row in
`capture_incidents` per code (occurrences, first and last seen, the newest occurrence's study,
capture, address, HTTP status, detail and evidence); and for a page signal the evidence itself
next to the capture's files: `evidence/<time>-<code>/page.png`, `page.html`, `page.json`
(address, HTTP status, title, the failed responses).

| Code | What | Breaker |
|---|---|---|
| 001 | The unusual-traffic page | Opens |
| 002 | A reCAPTCHA | Opens |
| 003 | A consent or sign-in wall | Opens |
| 004 | HTTP 429 on the page or an imagery/data host (not the feedback or survey endpoints) | Opens |
| 005 | A blank or black view | No; the capture fails |
| 006 | The search found no place | No; the capture fails |
| 007 | A load or the imagery timed out | No; the capture fails |
| 008 | The browser crashed, or the step failed after its retries | No; the capture fails |
| 009 | The breaker was open | Waits for it |
| 010 | A cap was spent | Waits for the window |
| 011 | Waited longer than `CAPTURE_MAX_WAIT_HOURS` | Fails |
| 018 | History could not be taken (no history control, or no date read while stepping) | No; a note, the capture goes on |
| 019 | Street level: no panorama near the property | No; a note, no retry |
| 020 | Street level: the panorama refused (tiles 401/403, lookup 5xx) or black | No; a note, no retry |
| 021 | A view's imagery date could not be read | No; one note per capture, the view keeps `imagery_date` null |

018 to 021 leave a `capture.part_failed` event (code, the engineer's sentence, `part`, and the
evidence for 018 to 020) instead of `capture.failed`.

A trip of the breaker raises one `capture_blocked` alert (`BASIS-ALERT-005`, keyed
`capture-blocked:<trip>:<opened at>`), emailed by the relay like the other alerts; a signal
while it is open raises none.

`GET /v1/ops/capture-incidents` (operators, `OPS_OPERATOR_UIDS`): the incident rows, newest
first, with the catalogue's meaning of each code. `GET /v1/ops/health` carries the state as
`property_capture` (docs/api/OPS_HEALTH.md section 7).

## 5. Gentleness

One capture at a time across every worker (a lease row); 8 to 20 seconds at random between two
navigations; 6 page loads an hour and 30 a day; 120 camera moves an hour (a move changes the
page's address without loading it); the breaker opens for an hour on a block signal, doubling
per consecutive trip up to a day, and closes on the next success. Every number is a setting
(`CAPTURE_*`, `basis/config.py`), counted in the database so a restart resets nothing.

## 6. History, dates and street level (EVAL_REPORT 99)

- **History** (`CAPTURE_HISTORY`, on): after today's views the camera returns to
  `aerial-close`'s and the viewer's history control is switched on. Its "Older images" arrow
  steps through every date (one camera move each, governed and spaced like any move; no page
  load), and each date is read off the status bar and checked against the page address. Kept:
  the newest date, every date within `CAPTURE_HISTORY_WINDOW_DAYS` (730) of the study's
  placed-in-service date (the intake's, else the appraisal's effective date), and the leaf-off
  dates, up to `CAPTURE_HISTORY_MAX_VIEWS` (6); the walk stops after
  `CAPTURE_HISTORY_MAX_STEPS` (40) steps. A black date is skipped.
- **Clean views** (`CAPTURE_CLEAN_VIEWS`, on): the viewport is `CAPTURE_FRAME_MARGIN_PX` (170)
  taller above and below; the stored view is the band between, and every range is lengthened
  by the same ratio, so `pixel_size_m` is EVAL_REPORT 98's (0.0841 m at `aerial-close`). The
  status bar's strip is kept beside the view (`<label>.status.png`) as the date's evidence.
- **Street level** (`CAPTURE_STREET_VIEW`, **off**): one more page load of the panorama nearest
  the pin, the camera aimed at the house, stored as `street-front`. EVAL_REPORT 99 found the
  service refuses a headless browser (lookup 500, tiles 403); 99.1 found it renders in a headed
  one, so street level needs `CAPTURE_HEADLESS=false` (a real window; on a server the capture
  image runs the worker under a virtual display). Refused, it records 020 and goes on.
- **The date reader** is tesseract, a system program (`CAPTURE_TESSERACT`, else `tesseract`
  on the path). The capture worker's image needs it installed; without it every date is null
  with the reason and the capture records 021 once.

## 7. Running it

```bash
uv sync --locked --group dev          # includes the capture group (playwright==1.64.1)
uv run playwright install chromium    # once per machine (the browser is not in the lockfile)
PROPERTY_CAPTURE=true uv run python -m basis.capture
```

The worker needs Postgres, Temporal and storage like the study worker, and a browser that can
render WebGL. Verified on macOS with Metal (EVAL_REPORT 98); a Linux container's software
rendering reports WebGL2 but has not rendered the imagery yet.
