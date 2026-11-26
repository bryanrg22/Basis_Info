# Draft it: `POST /v1/studies/{id}/property/site-drafts`

The measure tool's "Draft it" (EVAL_REPORT 111; `docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md`
step 6). The engineer drags a box over a driveway, patio, deck, pool, walk or parking area on a
top-down view of the property and asks Basis to draft its outline. The route runs SAM 3 on the
whole view with the box alone, no phrase (`basis.vision.site_items`, the setup EVAL_REPORT 106.3
measured best: a median area error of 13.6 % on the county's 3-inch ortho, driveways 11.8 %), and
answers with the draft as a measurement record, or a coded refusal in plain words. Nothing is stored
on the study but the paid call and an event: the draft becomes the study's only when the engineer
sends it as an `asset` correction (docs/api/OVERRIDES.md section 4), as it is or edited, verified
or not. Off by default (`SITE_DRAFTS=off`); `GET /v1/capabilities` says `site_drafts` (true only
with `TRACED_MEASUREMENT` on too).

## 1. The request

Engineers (a viewer is 403, a stranger 404), with an `Idempotency-Key` (every `POST`):

```json
{"asset_id": "<an asset of the study's current revision>",
 "view": {"kind": "capture", "id": "<a view of GET .../property>"},
 "box": {"x1": 98.5, "y1": 375.1, "x2": 336.9, "y2": 477.3},
 "family": "driveway",
 "unit": "SF"}
```

| Member | Meaning |
|---|---|
| `asset_id` | The asset the draft is for (the paid call is recorded against it). |
| `view` | `kind` `capture` (a view of `GET .../property`: the close aerial or a dated one) or `public` (a view of `GET .../property/public`), and its `id`. |
| `box` | The box in the view's own pixels (x right, y down), 0 to 100,000, at least 4 pixels a side, left to right and top to bottom, inside the view's frame (422 otherwise). It is sent to SAM 3 as it is, normalized to the view. |
| `family` | `driveway`, `parking`, `walk`, `patio`, `pool_deck`, `pool`, `deck` or `fence`. A fence is answered with the refusal `BASIS-SITE-008` and no call (EVAL_REPORT 106.6). |
| `unit` | `SF` (default) or `SY`: the unit of the asset the draft is for; the same outline is measured in it. |

## 2. The answer

200, one of:

```json
{"status": "drafted", "family": "driveway", "unit": "SF", "quantity": "1002.39",
 "expected_error_pct": "33",
 "measurement": {"unit": "SF", "space": "lonlat", "shapes": [{"kind": "area", "ring": [[...]]}],
                 "image": {"source": "...", "pixel_size_m": 0.0762, "captured_on": "2025-06-02"},
                 "drawn_by": "model:sam3@site-v1", "verified": false,
                 "view": {"kind": "public", "width": 977, "height": 977, "id": "...", "label": "..."},
                 "quantity": "1002.39", "area_sf": "1002.39", "method": "local_tangent_plane_wgs84",
                 "expected_error_pct": "3.12", "shape_count": 1, "corner_count": 9, "cutout_count": 0},
 "refusal": null}
```

```json
{"status": "refused", "family": "patio", "unit": "SF", "quantity": null,
 "expected_error_pct": null, "measurement": null,
 "refusal": {"code": "BASIS-SITE-003",
             "message": "Basis found nothing to outline in the box. Draw the box closer around the item, or trace it by hand."}}
```

* `measurement` is the record `basis.domain.traced.measure` makes of the draft (the body plus what
  it computes); the tool takes its body (the computed keys removed) as the trace's base, so an
  untouched draft is sent as it is and an edit keeps it as `original`.
* `expected_error_pct` (top level) is the larger of the outline's one-pixel bound and the method's
  measured 90th percentile for the family (driveway 33, patio 46, deck 28, pool 53; the largest for
  a family not measured): a starting outline, not a measurement. The record's own
  `expected_error_pct` is `traced`'s one-pixel bound, what the server stores once it is sent.
* A public view carries a year only, so its draft is undated (`measure:IMAGE_UNDATED` once sent).
* `refusal.message` never names a provider (AGENTS.md).

| Refusal | When | Call made |
|---|---|---|
| `BASIS-SITE-001` | the view is not a straight-down one (an oblique or street view, no pixel size) | no |
| `BASIS-SITE-002` | its pixel is coarser than `SITE_DRAFT_MAX_PIXEL_M` (0.16 m) | no |
| `BASIS-SITE-003` | SAM 3 found nothing overlapping the box (box IoU 0.5) | yes |
| `BASIS-SITE-004` | the outline runs off the picture's edge | yes |
| `BASIS-SITE-005` | the outline covers less than 3 m² | yes |
| `BASIS-SITE-006` | the outline is not a measurable shape | yes |
| `BASIS-SITE-007` | the call failed, or did not answer within `SITE_DRAFT_TIMEOUT_S` (30 s) | yes (recorded as an error) |
| `BASIS-SITE-008` | a fence | no |

## 3. Refused before any call (nothing spent)

| Answer | When |
|---|---|
| 409 `site-drafts-off` (`BASIS-MEASURE-001`) | `SITE_DRAFTS` or `TRACED_MEASUREMENT` is off (after the access check: a stranger still gets 404). |
| 404 (`BASIS-MEASURE-002`) | The view is not a view of this study (or its image was purged). |
| 404 (`BASIS-MEASURE-003`) | The asset is not an asset of the study's current revision. |
| 409 `gate-not-open` (`BASIS-MEASURE-004`) | The study is not waiting at the assets review: a draft goes with its corrections. |
| 409 `budget-exhausted` (`BASIS-MEASURE-005`) | `STUDY_BUDGET_USD` less the study's recorded spend is under `SITE_DRAFT_RESERVE_USD` (0.05). |
| 503 (`BASIS-MEASURE-006`), `Retry-After` | No segmentation model is configured (`LOCALIZER_URL` or `REPLICATE_API_TOKEN`). |
| 429 (`BASIS-MEASURE-007`), `Retry-After` | More than `SITE_DRAFTS_PER_MIN` (6) drafts by one person in a minute, besides the mutation limit. |

## 4. What is recorded

* The SAM 3 call in `llm_calls` (`kind` `localization`, `activity` `site_draft`, `entity_type`
  `asset` and the asset, the study's stage and revision, no run), so `GET .../spend` and the
  study's budget count it; a failed or timed-out call is recorded with `status` `error`.
* A `measurement.drafted` event on the timeline (actor the engineer; `entity_id` the asset; `code`
  the refusal's, if any; `detail` the status, family, view, `drawn_by`, quantity, unit and cost).
* The same `Idempotency-Key` answers again from the stored response without a second call.

Transactions: the checks read in one short transaction; the view's image is read and the call made
with no connection held (EVAL_REPORT 32); the ledger row and the event are written after it. CPU
work (decoding the view, tracing the mask's outline) runs on threads.

## 5. Not built

The draft is not stored until sent (a reload before sending loses it, as an unsent trace is kept
only by the assets review's draft); a vision model proposing the boxes (M2v) is not offered (EVAL
106.5: the two models measured put their boxes too far off); the draft's method error (the 90th
percentile) is not stored with the measurement once sent, only `traced`'s one-pixel bound.
