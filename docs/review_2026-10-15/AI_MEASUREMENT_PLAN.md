# AI-drafted measurements, verified by the engineer (plan, 2026-11-25)

**Ask (Bryan, 2026-11-25).** Measuring is the point of Basis: make it as easy as possible.
Use Google Earth exactly the way an engineer does (trace lines and areas, read lengths, check
the history), let the AI draft the measurements from the Google Earth captures and the property
photos together, and let the engineer verify and change any of them. On the page with the
components, show the measurements on the shape the way Fusion 360's Measure tool does: a label
on every side. Research the standard first, compare methods and models side by side on ground
truth, record everything. Google's terms are set aside for the MVP.

This note is the research and the plan. Nothing in it is built yet. The full research reports
(sources, every claim labelled verified, search-summary or inferred) are kept in
`$BASIS_EVAL_DATA_DIR/work/measure_research/` (`cv_methods.md`, `products_and_practice.md`,
`ground_truth.md`); they hold no client data.

## 1. Answer first

1. **No product and no paper measures a house from ordinary photos plus an aerial and publishes
   the error.** The pieces exist (aerial tracing, lidar roofs, single-view metrology, metric
   depth, photo reconstruction); combining them for one house is new. So we measure, we do
   not assume.
2. **The chat models cannot measure.** On VSI-Bench room size GPT-5 scores 50.5 and
   Gemini-2.5-pro 51.8 (of 100, mean relative accuracy); on Q-Spatial GPT-4o is within a
   factor of two only 61 to 72 % of the time, against 90 % for people. They are good at
   deciding *what* to measure and where (labels, prompts, which photo shows the countertop),
   not at the number.
3. **The biggest error we have is the building shell, and free government data fixes most of
   it.** Today's perimeter is 31 % low on the client house because it assumes a 1.5:1 rectangle.
   A footprint (assessor, lidar or aerial trace with the eave overhang removed) plus 3DEP lidar
   heights gives perimeter, eave height, roof plan, pitch, siding area and gutter length with
   no model call. Published lidar pitch error is about 3.8 degrees (about 5 % of roof area on
   a 6/12 roof).
4. **Site items (driveway, patio, walks, parking, fence) are traced on the aerial.** SAM with a
   box or point prompt is a good tracing assistant (text prompts hurt on overhead imagery,
   arXiv 2607.09583); the engineer drags the corners. Boundary placement dominates the error:
   one pixel along a 6 x 10 m driveway at 8.4 cm is 4.5 % of its area. Fences are poor from
   above (1 to 3 pixels wide, under trees): the lot line is the prior, the photos confirm.
5. **Interiors need a known length.** Single-photo depth models get the shape right and the
   scale wrong (35 to 71 % on room height in one small 2026 phone study; 5.6 % on good DSLR
   photos). Scale from objects of known size (an 80 in door, a 34.5 in base cabinet, a 36 in
   counter) is the standard fix.
6. **The engineer's tool is the product.** Every measuring product found (EagleView, Nearmap,
   Hover, Roofr, Attentive, SiteRecon, Bluebeam, PlanSwift, Togal) has the same pattern: scale
   first, AI or person draws, a length on every edge, drag and snap corners, the AI draft kept,
   a verified state. The one independent study of an AI takeoff (ASC 2026, Togal) found areas
   consistently underestimated, and a University of Kansas study found users "trust AI results
   too readily": the verification step has to be real, not a click-through.
7. **Two traps to measure, not assume:** the aerial outline is the roof edge, not the walls
   (siding perimeter too long by about 8 x the overhang); and the Google straight-down view's
   pixel size (8.4 cm) has never been checked against a known length.

## 2. What Basis has today (read in the code, main a530125)

* **The measurement record exists** (EVAL 97): `basis/domain/traced.py` takes area or line
  shapes in lon/lat (local tangent plane, exact to 2e-7 %) or in image pixels with a clicked
  scale, computes the quantity and an expected error, raises four review flags, and stores
  the shape in `takeoffs.inputs["measurement"]`. Nothing records who drew it (engineer or a
  model) or what it replaced.
* **The trace tool** (`web/live/property.js`) clicks corners on a straight-down capture or a
  public view and shows one total. No length on any edge, no area label, no editing after a
  shape is closed (only Undo, Clear, trace again), lon/lat only (the API's image space is
  unused), interior items are filtered out, and a trace is lost on reload.
* **Quantities not traced come from formulas**: room areas are shares of the appraisal's GLA,
  perimeters are `4 x sqrt(area)`, the building footprint is GLA / stories as a 1.5:1
  rectangle, roof and siding follow from that; driveway, sidewalk, patio, concrete pad,
  parking lot, fence and retaining wall have no source and stay unpriced until the engineer
  types or traces a number (`NEEDS_MEASUREMENT`, `basis/domain/measurements.py:116`).
* **SAM 3 draws masks on the photos for countable items only**; it is never prompted for
  surfaces (floors, walls, roof, siding, driveway). The photos carry no scale and no depth
  (EXIF stripped, HEIF depth not loaded).
* **Lidar** gives roof peak, eave height and a roof-outline area, but no outline polygon is
  stored. **Parcels** are stored as polygons.
* Found in passing: the wainscoting row's formula sentence says "x 25/12 ft depth" while its
  quantity uses 2 ft of height (`basis/domain/takeoffs.py:241-243`). The number is right, the
  sentence is wrong.

## 3. The standard to match (products and CAD)

1. Scale first and shown: automatic on a georeferenced aerial, from a known length on a plan
   or photo, and checkable on a second known length.
2. A length label on every edge (EagleView labels every segment over 5 ft), area and perimeter
   on the shape, totals computed from unrounded values.
3. Corners can be dragged, added on an edge and deleted; snapping to corners, the lot line and
   the building outline.
4. Typing a length on an edge moves the far corner along that edge (Fusion's sketch
   dimensions), with the label then matching the geometry. AutoCAD's override, where the label
   text stops matching the drawing, is the thing not to copy.
5. Cut-outs as child shapes; a wall face as a line times a typed height; slope typed onto an
   area.
6. Colour by asset type with a legend; the list and the picture highlight each other.
7. The AI draft kept unchanged beside the edit (SiteRecon's locked base view); a verified state
   per measurement (Roofr's "Mark as done").
8. The imagery date on each measurement; the uncertainty shown.
9. The deliverable carries the diagram with its labels.

Not found in any product, and Basis can do: the AI's value and the engineer's per edge, with
the reason; agreement across sources (aerial, lidar, sketch, photos) shown per quantity; a
check that a site item existed at the placed-in-service date (dated history); error bars
weighted by the dollars they move; signed bias published per asset type.

## 4. Ground truth for the evaluation

| Tier | Quantities | Truth | Truth error | Size |
|---|---|---|---|---|
| A. Real US houses | footprint, wall lengths, perimeter, eave and ridge height, roof plan, some site items | HABS measured drawings (Library of Congress, public domain): dimension strings to the inch (e.g. Miller House, Springfield IL: 32'-4" x 54'-3", gutter 22'-9", ridge 27'-7") | about 0.1 to 0.3 % on printed dimensions; site items scaled off the drawing are worse | 10 first, then 25 to 30 |
| B. Paving, many | driveway, walk, parking area | County planimetric layers (Fairfax VA, Montgomery MD, DC); areas must be reprojected (stored Web Mercator areas are 1.64 x too large) | about 1 ft at the edge: a bias check, not a ranking | about 100 shapes |
| C. Interiors | room sizes, floor area, ceiling height, cabinet and counter runs | ARKitScenes (laser scans plus iPad video; commercial grant under 700M MAU, wording to confirm with Apple) | millimetre class | 30 to 50 rooms, 10+ kitchens |
| D. Our own, like production | everything, with real owner photos | client property sketch (4 pages, private); design partners' takeoff worksheets; 8 to 12 laser-measured homes of staff or friendly owners | about 1 cm a reading | 8 to 12 |

Size: with every method run on the same houses, 18 houses separate two methods 2 points apart
when methods disagree by about 3 % per house; at 5 % it takes about 49. The first 10 houses
measure that spread before we commit to more.

Ask the design partners for: their takeoff worksheets with how each number was obtained, their
site photos, drawings or surveys, any EagleView or Hover reports, visit dates, and the
tolerance they accept per quantity.

## 5. The methods compared (one table per sub-step, as every eval here)

Each runs on the same houses through `evals/` with a run record; per quantity: signed and
absolute % error against truth, cost, tokens, latency; later, the engineer's edit time.

| Id | Method | Quantities | Cost |
|---|---|---|---|
| M0 | Today's formulas (baseline) | all | $0 |
| M1 | Building shell from government data: footprint (assessor or lidar walls; aerial trace less the overhang), 3DEP eave and ridge, roof planes and pitch by plane fitting; roof = plan / cos(pitch), siding = perimeter x eave height - openings (openings counted from the photos), gutter from the eave edges | perimeter, wall height, roof, siding, gutter | $0, CPU |
| M2 | Aerial trace by SAM 3 with visual prompts (box or point), on the Google straight-down view and on the state/USGS ortho, simplified polygons, area from the pixel size | driveway, patio, walks, parking, pool deck, roof outline | $0 on the laptop |
| M2v | M2 with the prompts proposed by a vision model (which surfaces exist, where) instead of a click | same | about $0.01 a property |
| M3 | Owner exterior photos scaled by the known wall lengths of M1 (per-facade rectification; multi-view reconstruction as the variant) | facade areas by material, opening sizes, trim | $0, CPU (the novel one) |
| M4 | Interiors: depth-model shape (MoGe-2, MIT; Depth Anything 3 metric, Apache) scaled by detected objects of known size, reconciled to GLA | room areas, counter SF, cabinet LF, floor SF | $0 to $0.01 a photo |
| M5 | A vision model asked for each quantity directly (Claude, GPT, Gemini, at the production settings) | all | about $0.05 a property |
| M6 | The sketch readers (EVAL 96, 100) where the appraisal has a sketch | footprint, perimeter | $0 |

Licences checked: MoGe-2, Depth Anything 3 Metric-Large, COLMAP, gsplat, MapAnything (Apache
checkpoint) and SAM 3 allow commercial use (SAM 3 with use restrictions); MASt3R, DUSt3R and
UniDepth are non-commercial and are left out.

Also measured once, before anything traces on Google for money: the straight-down view's real
pixel size and its relief displacement, against HABS dimensions and county lines on the same
houses.

## 6. The engineer's tool (the screen; design is Bryan's to approve)

On the asset page (and the Property page), for any asset with an SF, SY or LF quantity:

* The picture to measure on: the straight-down capture, a dated one, a public ortho, or a photo
  of the item (interior items included: a photo with a known length sets the scale).
* The AI's draft drawn as a dashed shape with a length on every edge, area and perimeter in the
  middle, the source and date, and the expected error ("up to 4 %").
* The engineer drags, adds or deletes corners (snapping to corners, the lot line and the
  building outline), or clicks an edge's label and types the length: the far corner moves
  along the edge.
* "Verified" per measurement; the AI's original stays beside the edit (per edge: AI 54'-3",
  engineer 54'-0"), and the record says who drew it and what it replaced.
* A measurement survives later revisions (today a traced quantity is lost on the next assets
  rejection: EVAL 97.6) and survives a page reload (today it lives in browser memory).

## 7. Order of work

1. **Ground truth and the harness** (free): 10 HABS houses with captures (about 2 to 3 Google
   page loads a house, so within the 30-a-day cap over a few days), the pixel-size check, the
   harness and M0 / M6. Record as an EVAL_REPORT section.
2. **The tool** (free): per-edge labels, area and perimeter, drag/add/delete corners, typed
   lengths, verified state, persistence; image space for photos with a known length. Tests
   in `web/tests`, a live walk-through, phone checks.
3. **M1, the building shell** (free), against M0 and M6 on the HABS houses. If it wins, it
   drafts the shell quantities behind a setting. *Done (EVAL_REPORT 105, against M0): wall
   face 32.5 -> 6.4 %, wall height 19.0 -> 2.3 %, perimeter 16.0 -> 5.8 % on six houses; the
   footprint stays with the GLA (porches); `basis/geo/shell.py` behind `GEO_SHELL`, off; the
   takeoff wiring is still to do.*
4. **M2 / M2v, site items**, on HABS site plans and the county shapes; the Google view against
   the public ortho.
5. **M4 interiors** on ARKitScenes, then **M3 facades** (the novel one), then **M5** as the
   floor every other method must beat. *M3 done (EVAL_REPORT 109): single-view metrology on a
   photo scaled by the wall's length reads openings 5 % off (an engineer's corners; 7 % with
   gpt-6.1-sol placing them) against the 15 % share's 17 %, every window and door counted;
   the net face is no better than the 15 % share on M1's eave (2.5 %); 8 facades, 9 measured
   photos, 8 of them on one house; `basis/geo/facade.py` behind `GEO_FACADE`, off; the
   depth-model variant not run; the EXIF focal length must be kept at upload before wiring.*
   *M4 done (EVAL_REPORT 107, 31 rooms, 12 kitchens, truth
   checked against a second scan and the laser): no depth setup beats today's formulas (owners'
   photos do not show the whole room: single-view floor area 33 to 47 % low, even at the right
   scale); a vision model asked with rulers (claude-sonnet-5-5) does: length 11 %, perimeter 10 %,
   wall area 12 %, countertop 21 %, cabinet run 29 % against today's 18, 12, 25, 313 and 270 %
   given the true area, $0.009 a room; multi-view with a door (perimeter 10 %, 9 rooms) is the
   geometry to take further. Nothing shipped; the production M4d step is still to build.*
6. Wire the winner per quantity into the pipeline behind settings, each as a draft with its
   expected error, the engineer verifying in the tool of step 2. *Pipeline half done (EVAL_REPORT
   110): `MEASURE_DRAFTS_EXTERIOR` (roof, siding, gutters from M1's shell; siding 32.5 -> 6.4 %
   through the shipped code) and `MEASURE_DRAFTS_INTERIOR` (M4d, the `measure_rooms` step, patch
   `measure-drafts-v1`), both off; each draft priced with `measure:DRAFT_UNVERIFIED`, and the
   report held (`measure:draft_unverified`) while an unverified draft's expected error x price is
   above `MEASURE_DRAFT_VERIFY_USD` ($250, decision 8.3 (a)); the app's display of the factor,
   the held drafts and the interior drafts is still to build (FRONTEND_BACKLOG 23).*

## 8. Decisions for Bryan

1. **The tool's screen**: the per-edge labels, typed lengths and verified state above, on the
   asset page as well as the Property page. Approve, or say what to change.
2. **Paid budget** for M2v and M5 on the eval set: about $5 to $15 in all (30 houses, a few
   models). Nothing paid runs without the yes.
3. **Does an unverified AI measurement price the report?** (a) It prices at once with a
   "draft" flag and the report waits until every draft above a dollar threshold is verified;
   (b) nothing prices until verified; (c) it prices and the engineer may sign without
   verifying. Recommended: (a); (c) conflicts with the fail-safe money rule (EVAL 78) as soon
   as a draft is far off.
4. **Our own truth set (tier D)**: 8 to 12 homes measured with a laser measure (3 to 5 hours
   each), and the ask to the design partners for their worksheets.
5. **ARKitScenes**: confirm with Apple that the commercial grant covers the dataset, or use it
   for research only.
