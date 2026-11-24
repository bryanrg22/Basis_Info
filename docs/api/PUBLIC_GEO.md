# Public records of the property: `/v1/studies/{id}/property/public`, `GET /v1/ops/geo-incidents`

Government imagery, parcels and lidar around a study's pin, gathered in the background by a
worker of its own (`python -m basis.geo`) and kept with the study's files. The design and when
each source is preferred are in `docs/review_2026-10-15/PROPERTY_CAPTURE.md` section 10; the
measurements in EVAL_REPORT 101. Off by default (`PUBLIC_GEO=false`); `GET /v1/capabilities`
says `public_geo`.

## 1. Asking for them

`POST /v1/studies/{id}/property/public` (engineer), body `{"force": false}` (optional):

| Answer | When |
|---|---|
| 202 with the gather, `status: queued` | Accepted; the workflow `public-geo-{gather id}` was started. |
| 200 with the recent gather | A gather at the pin's current point (within 1 m) succeeded within `GEO_RECENT_HOURS` (12) and `force` is false. Confirming the pin or editing its address does not call for a new gather. |
| 409 `geo-off` (`BASIS-GEO-001`) | `PUBLIC_GEO` is off. |
| 422 `geo-no-point` (`BASIS-GEO-002`) | No pin, or a pin with only an address (the sources are asked by point). |
| 409 `geo-in-progress` (`BASIS-GEO-003`) | One is queued or running (one per study, a unique index). |
| 503 (`BASIS-GEO-004`) | The workflow did not start; the gather is marked failed so nothing is left queued. Repeat. |

`POST .../property/captures` (docs/api/PROPERTY_CAPTURE.md) also queues a gather when
`PUBLIC_GEO` is on and the pin has a point. A gather that cannot be queued or started there is
logged with `BASIS-GEO-004` and never fails the capture.

## 2. Reading them

`GET /v1/studies/{id}/property/public` (viewer):

| Member | Meaning |
|---|---|
| `enabled` | `PUBLIC_GEO`. |
| `gather` | The newest gather: `status` (`queued`, `running`, `succeeded`, `failed`), `code`, `message` (what to tell the engineer), times, `pin_version`, `stale` (the pin moved more than 1 m since; a confirmation or an address edit does not count), and `sources`: every source asked with its `status` (`ok`; `blank`: the point is outside its coverage; `none`: nothing there; `failed` with its `code`), `seconds` and `year`. |
| `current` | The best public top-down image at the point (pixel size first, then leaf-off within four years of the newest, then recency: `basis/geo/registry.py`), or null. |
| `history` | One view per year, newest first; the current view appears in it as its year's view. |
| `parcels` | The parcel under the pin (`under_pin: true`) and the same owner's adjacent ones: `parcel_id`, `owner`, `address`, `area_sqft` / `area_acres` (the polygon on the WGS84 ellipsoid), `layer_acres` (the layer's own figure), values, `year_built`, `building_area_sqft`, `stories` (null where the state's layer has none), `geometry` (GeoJSON, lon/lat), `credit`, `terms_class`. |
| `site_check` | The parcel against the appraisal's site area (`site.area_sqft`), computed when read so a later extraction counts: `status` `agree`, `adjacent_match` (`BASIS-GEO-013`, a note: the owner's adjacent parcels together match), `disagree` (`BASIS-GEO-012`, `flag: true`: show it as a review flag), `no_appraisal`, `no_parcel`; `difference_pct`, the figures compared, `tolerance_pct` (`GEO_SITE_TOLERANCE_PCT`, 5). Null until a gather succeeded. |
| `lidar` | The federal 3DEP project at the point (`project`, `year`, `points`, `density_per_m2`) and the building under the pin: `footprint_m2` / `footprint_sqft` (the roof's outline as flown, overhangs included), `peak_m` (99th percentile above the ground around it), `max_m`, `eave_m`, `ground_elevation_m`; `building` null with `code` `BASIS-GEO-016` when no building points are near the pin. |

A public view (`PublicViewOut`):

| Member | Meaning |
|---|---|
| `kind`, `label`, `source_id` | `current` or `history`; the registry entry (`basis/geo/sources.json`). |
| `year`, `year_label`, `leaf_off` | The imagery's year (read at the point where a mosaic mixes years), the source's range when it mixes them, the season as the source states it. |
| `lat`, `lon`, `pixel_size_m` | The centre (the pin) and ground metres per pixel there. |
| `crs`, `bbox`, `bbox_lonlat`, `width`, `height` | The exact georeference: north up in EPSG:3857, `bbox` in its metres. |
| `credit`, `terms_class`, `terms_url` | Show and print the credit with the image. `terms_class`: `public-domain`, `open-with-credit`, `no-terms-stated`, `restricted`. |
| `content_type`, `sha256`, `captured_at`, `stale` | The image; `stale` when the pin moved more than 1 m since. |

To measure on a public view: exactly as on a property-capture view (PROPERTY_CAPTURE.md section
3): east = `(x - width / 2) * pixel_size_m`, north = `(height / 2 - y) * pixel_size_m`, then to
lon/lat around `lat`, `lon`; Mercator is conformal, so the pixel is square on the ground and the
scale changes by about one part in 10^5 across a view. For the exact inverse use `bbox`
(`basis.geo.geometry.pixel_to_lonlat`). Send the view's `source_id`, `year` and `pixel_size_m`
with PR #178's `measurement`.

`GET .../property/public/views/{view_id}/image` (viewer): the JPEG or PNG as the source served it
(stitched tiles are re-encoded as JPEG), `ETag` its sha256 (304 on `If-None-Match`),
`Cache-Control: private, max-age=3600`. Stored under `gs://<bucket>/studies/<id>/public/<gather
id>/`, so the retention purge removes it with the study's files; after a purge it answers 404.

## 3. Failures (operators)

Each source is asked on its own: one failing never stops the others. A failure is the source's
outcome in `gather.sources` with its code, a `geo.source_failed` event on the study's timeline, a
log line from the geo worker with `error_code`, and a row in `geo_incidents` per code and source
(occurrences, first and last seen, the newest occurrence's study, gather, request URL, HTTP
status and detail).

| Code | What |
|---|---|
| 005 | HTTP error after the retries (429 and 5xx retried), or the connection failed |
| 006 | No answer within `GEO_REQUEST_TIMEOUT_S` after the retries |
| 007 | An answer that is not usable (an error document, an image that does not decode) |
| 008 | No imagery source covers the point (an outcome, not an incident) |
| 009 | No parcel under the point, or no open parcel layer for the state (an outcome) |
| 010 | No lidar project covers the point (an outcome) |
| 011 | A lidar tile or the project's metadata could not be read |
| 015 | The gather step itself failed after its retries: the gather is `failed` |
| 016 | Lidar covers the point but has no building points near the pin (an outcome) |

`GET /v1/ops/geo-incidents` (operators, `OPS_OPERATOR_UIDS`): the incident rows, newest first,
with the catalogue's meaning of each code.

Timeline events: `geo.requested` (engineer, or system when the capture queued it), `geo.started`,
`geo.source_failed`, `geo.site_area_checked` (the check's result, `code` 012 or 013 when it is
not `agree`), `geo.completed` (`BASIS-GEO-014`, views and failures counted), `geo.failed`.

## 4. Gentleness

These are public government servers. One request at a time per host and at least
`GEO_HOST_INTERVAL_S` (1 s) between two, across every gather of the worker process; 10 s to
connect and `GEO_REQUEST_TIMEOUT_S` (60 s) for an answer; `GEO_RETRIES` (3) more tries of a
timeout, a connection error, 429 or 5xx, waiting 2, 4, 8 s (times 0.5 to 1.5) or the server's
`Retry-After` when longer (at most 120 s); never a retry of another 4xx. One geo worker per
deployment (the spacing is per process). The measured gather made 43 requests in 67 s
(EVAL_REPORT 101).

## 5. Running it

```bash
uv sync --locked --group dev          # includes the geo group (laspy, lazrs, geographiclib)
PUBLIC_GEO=true uv run python -m basis.geo
uv run python -m basis.geo.retest --out results.json   # re-test every registry source live
```

The worker needs Postgres, Temporal and storage like the study worker, and outbound HTTPS to the
sources in `basis/geo/sources.json`, raw.githubusercontent.com (the lidar index) and
s3-us-west-2.amazonaws.com (the lidar tiles).
