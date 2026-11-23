# Property pictures: `/v1/studies/{id}/property`, `GET /v1/ops/capture-incidents`

Dated views of a study's property, taken in the background by a worker of their own
(`python -m basis.capture`) and kept with the study's files. The design and the engineer's use
are in `docs/review_2026-10-15/PROPERTY_CAPTURE.md`; the measurements in EVAL_REPORT 98. Off by
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
| 200 with the recent capture | The same pin version was captured within `CAPTURE_RECENT_HOURS` (12) and `force` is false. |
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
| `kind`, `label` | `aerial` (`aerial-close`, `aerial-wide`), `oblique` (`side-south`, `side-west`, `side-north`, `side-east`: the face seen). `history` and `street` are reserved, not taken yet. |
| `lat`, `lon`, `ground_alt_m` | The point looked at (the view's centre) and its altitude. |
| `range_m`, `fov_deg`, `heading_deg`, `tilt_deg` | The camera: distance to the point, vertical field of view, compass heading it looks along, tilt from straight down. |
| `pixel_size_m` | Metres per pixel at the centre, top-down views only: `2 * range * tan(fov / 2) / height`. Null for tilted views, which must not be measured on. EVAL_REPORT 98: 2.0 to 2.6 % finer than the imagery's own scale bar; not checked against a measured length. |
| `imagery_date` | As the imagery showed it (its own format), null when it did not. |
| `source` | The provenance line (`CAPTURE_SOURCE_TEXT`). |
| `pin_version`, `stale` | `stale`: taken around an earlier pin than the current one. |
| `width`, `height`, `sha256`, `captured_at` | The image. |

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

## 6. Running it

```bash
uv sync --locked --group dev          # includes the capture group (playwright==1.64.1)
uv run playwright install chromium    # once per machine (the browser is not in the lockfile)
PROPERTY_CAPTURE=true uv run python -m basis.capture
```

The worker needs Postgres, Temporal and storage like the study worker, and a browser that can
render WebGL. Verified on macOS with Metal (EVAL_REPORT 98); a Linux container's software
rendering reports WebGL2 but has not rendered the imagery yet.
