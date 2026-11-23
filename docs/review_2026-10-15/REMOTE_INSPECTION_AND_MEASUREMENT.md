# Looking at the property and measuring it inside Basis (2026-11-22)

Research and design note. Nothing here is built. Question, from Bryan and a co-founder: engineers
leave Basis to measure the driveway in Google Earth Pro and to look at the house on a listing or a
virtual tour. Can both happen inside Basis, with the engineer in control of every number and the
evidence kept for an audit? What may we legally show, trace on, store and print?

Companion documents: `TAKEOFF_RESEARCH.md` (how quantities are derived today), EVAL_REPORT 35
(measured takeoff), 78 (no substitute value reaches dollars), 87 (which measuring constants
matter). No client figure appears here; the one-property check is given in percentages.

## 1. Answer first

1. **Engineers do this on every study, not only when a client leaves something out.** No client
   document carries a driveway's area or a fence's length. Satellite imagery is the routine way
   to size the site, and photos, video or a tour stand in for the site visit.
2. **Google cannot be the measuring surface.** Its terms forbid tracing on its imagery, storing
   it, putting it in a generated document, and showing it next to a non-Google map. Google Earth
   cannot be embedded in another app at all. Google's 3D view and Street View can be *shown* in
   Basis, on a screen of their own, and nothing from them can be kept.
3. **Looking and measuring are two features with two different rule sets.** Looking can use
   Google. Measuring needs imagery we are allowed to trace on and print.
4. **Only public-domain aerial imagery clearly permits the whole measuring workflow** (show,
   trace, store the still, print it in the client's PDF). Two commercial sources (Esri, Azure
   Maps) allow the PDF and are unclear on tracing; the high-resolution aerial vendors need a
   negotiated partner agreement.
5. **The safest first build needs no imagery licence:** the engineer uploads an image or plan
   they have the right to use, sets the scale from one known length, and traces. The same tool
   reads a site plan, a survey, a floor plan or a blueprint.
6. **A hands-on check on one property (section 11) found the free public sources match or beat
   Google Earth for measuring** where a state flies sharp imagery, and that neither has a real
   3D view of a small-town house.
7. **What Basis would add that no product found does:** the AI's original kept beside the
   engineer's edit, a source record on every quantity that travels into the report, and the
   measuring and inspection evidence in one audit file.

## 2. What the standard says

**The IRS** (Cost Segregation Audit Technique Guide, Publication 5653, rev. 2-2025; read as text,
quotes checked):

* It sets no measuring method and no tolerance: the Service "has not established any
  requirements or standards for the preparation of cost segregation studies" (ch. 3 D(1)).
* Without drawings, "field inspection of the property is a critical step" and should document
  "the size, and types of building systems" (ch. 3 C.2(4)). It does not say in person.
* A quality study for acquired property "includes a site visit, as well as photographic
  evidence" (ch. 4 C.3), and its take-offs "are carefully documented to show derived unit
  costs" (ch. 4 C.8). The report includes "a record of inspections" (ch. 4 D).
* Examiners ask whether photographs or video were "relied upon in classifying property"
  (ch. 5), and request the take-off schedule "showing use and location".
* Chief Counsel Advice 199921045: a study may not rest on "estimates or assumptions that have
  no supporting records".

So the bar is evidence, not precision: a stated method, a quantity specific to the property,
and the record behind it.

**Practice** (firms' own descriptions and the founders' interview notes; no firm publishes a
measured accuracy):

* On-site firms measure room by room and count; for the site they add "aerial/satellite imagery
  (for site dimensions)".
* Remote firms use 20 to 40 owner photos, a 5 to 10 minute video or a recorded video call,
  county records for square footage, and satellite imagery for the outside. One states that
  "Every inspection is recorded and kept on file with the study".
* A design-partner engineer told the founders she measures the 15-year site items in Google
  Earth Pro, estimates lengths on photos by eye and keeps her own average measurements.
* Low-cost software skips measuring: one uses ratios with no area inputs, another takes the
  owner's percentages and "a best estimate".

Not confirmed: how often engineers re-check a number the client did supply. Ask the design
partners (section 9).

## 3. What Basis does today (read in the code, 2026-11-22)

* A study accepts an appraisal and photos. Other files upload as `other` and nothing reads them
  (`basis/db/enums.py` `DocumentKind`).
* Site items (driveway, sidewalk, patio, fence, retaining wall, concrete pad, parking lot) get
  no quantity. The row is unpriced, the report is withheld, and the blocker names the
  measurement (`basis/domain/measurements.py` `NEEDS_MEASUREMENT`; EVAL_REPORT 78).
* The engineer supplies it as three fields on an asset correction: `quantity`, `quantity_unit`,
  and `quantity_source`, free text up to 300 characters (`basis/activities/corrections.py`
  `check_quantity`). No image, no shape, no imagery date is kept, and the client report does
  not print the source.
* There is no map, no measuring tool, no link to a listing or tour, and no public-record
  lookup. The intake page's address suggestions are design fixtures (`web/address-lookup.js`).

Basis is safe here (nothing invented reaches dollars) and unhelpful (the measuring happens
somewhere else and only a sentence about it comes back).

## 4. What we may use: imagery and 3D sources

A reading of published terms on 2026-11-22, not legal advice. "Trace" means the engineer draws
a shape on the image and Basis keeps the shape and its area.

| Source | Show in a paid app | Trace and measure | Keep the still | Print in the client's PDF | Published price | Pixel size |
|---|---|---|---|---|---|---|
| Google Maps Platform (satellite, Photorealistic 3D Tiles) | Yes | **No** | **No** | **No** | $0.60 to $7 per 1,000 loads; 3D $6 | Not published |
| Google Street View | Yes | No | No (a panorama ID may be stored) | No | $14 per 1,000 | n/a |
| Google Earth, Earth Pro | **No** (cannot be embedded) | Only in Google's own app | Only in Google's own app | Unclear | Free; Pro downloads end 2027-06-25 | Not published |
| Mapbox Satellite | Yes | No (commercial tracing excluded) | No | No (print rights cover street maps only) | Not captured | Not published |
| Esri World Imagery | Yes | **Unclear** | Yes, as a static image | Yes ("a report for a client") | 2M tiles free, then $0.15 per 1,000 | 30 cm US, 15 cm some metros |
| Azure Maps | Yes | **Unclear** | Yes, as an unaltered screenshot | Yes ("sending PDF Prints to customers") | 1,000 free a month; paid rate not read | Not published |
| Nearmap | Not under standard terms | Internal use only; no machine learning on it | During the term | No | From $3,948 per user per year | 4 to 7.5 cm |
| EagleView | Embeddable viewer; terms not public | Unclear | Yes | "occasional, not systematic" | $13.75 to $105 per report (roofs and walls) | 1 inch |
| State and county aerial programs | **Yes** where the metadata sets no limits | **Yes** | **Yes** | **Yes** | Free | 3 to 6 inch where flown |
| USGS high-resolution aerial, USDA NAIP | **Yes** (public domain) | **Yes** | **Yes** | **Yes** | Free | 6 inch to 1 m; NAIP 30 to 60 cm |
| USGS 3DEP lidar | **Yes** (public domain) | **Yes** | **Yes** | **Yes** | Free | Heights to about 10 cm |
| Open building outlines (Microsoft, Overture) | Yes, with credit | Yes | Yes | Yes | Free | Outlines only |

The clauses that decide it:

* **Google, no tracing** (Terms of Service 3.2.3(c)): "Customer will not create content based on
  Google Maps Content", for example "trace or digitize roadways, building outlines".
* **Google, no documents** (FAQ): "If your application generates a document, either in electronic
  or printed form, no data from Google Maps Platform, including images, may be included".
* **Google, no storing** (3.2.3(a)): will not "pre-fetch, index, store, reshare, or rehost".
* **Google, not beside another map** (3.2.3(e)): will not "display Street View imagery and
  non-Google Maps on the same screen".
* **Google 3D, viewing only** (Map Tiles policy): "You may not use Map Tiles API for any
  non-visualization use cases", and overlays may not be "extracted, traced, or otherwise
  derived by hand or machine" from the tiles.
* **Google Earth** (Geo Guidelines): "Google Earth may not be embedded online or in apps."
* **Google's own drawing tools** were withdrawn from the Maps JavaScript API in May 2026, and
  45° imagery with them.
* **Esri**: static maps may be used "to include in a presentation or a report for a client";
  a separate Esri document grants the right "to trace features"; the developer agreement says
  output is "for visualization purposes and only within Customer Applications". Those three do
  not agree. Ask Esri in writing.
* **Azure Maps**: prints may be used for "sending PDF Prints to customers", but one may not
  "Alter a screen shot in any way except to resize", and tracing is not addressed. Ask Microsoft
  in writing.
* **Nearmap**: derivative works "for internal purposes only", and no "machine learning
  products ... computer vision" on the imagery. A partner agreement would be needed; its terms
  are not public.

Two side notes worth knowing:

* Today's hand method sits in an ambiguity. Google's guidelines say Earth content "may not be
  used for any commercial or promotional purposes" and also list "Business documents such as
  company reports" as allowed in print. Earth Pro stops being downloadable on 2027-06-25.
* Listing photos and listing tours are not ours to show. Ownership is split among
  photographers, agents and listing services; the large portals' terms bar automated
  collection; licensed access runs through each listing service. The owner uploading their own
  photos, video or scan is the clean route, and Basis already takes the photos.

## 5. How accurate a traced measurement is

Independent studies (not vendors), measured against tape, wheel or survey:

| What was measured | Method | Typical error |
|---|---|---|
| Lengths under 12 ft | Google Earth Pro | 3.4 to 5.5 %, outliers to 24 to 50 % |
| Lengths of 25 to 50 ft | Google Earth Pro | about 1.8 % |
| Lengths of 100 to 250 ft | Google Earth Pro | 0.6 to 0.7 % |
| Rectangles under 250 SF | Google Earth | 13.7 % |
| Rectangles of 500 to 1,600 SF (a driveway) | Google Earth | 4.0 % (worst 10.3 %) |
| Rectangles of 500 to 1,600 SF | 3-inch aerial | 2.4 % (worst 5.0 %) |

So the method engineers use today is good to about 4 % on a driveway, and that is the baseline
any Basis tool has to match.

Pixel size sets the floor. By formula (area error grows with perimeter times edge uncertainty;
no small-object study was found at 30 cm or coarser), a 20 x 40 ft driveway traced at one pixel
of edge uncertainty is off by 1 to 4 % at 7.5 cm pixels, 3 to 7 % at 15 cm, 6 to 15 % at 30 cm
and 11 to 30 % at 60 cm. A 150 ft fence is under 2 % at any of them. Free national imagery at
60 cm is therefore fine for a fence and poor for a driveway; state 3 to 6 inch imagery is good
for both where it exists.

Other error sources the tool must show rather than hide: trees over the pavement (national
imagery is flown with leaves on), a sloped drive (plan area understates the surface by 0.5 % at
a 10 % grade, 2 % at 20 %), a wall's height (not visible from above; the engineer enters it), and
the image's date against the placed-in-service date (a driveway poured after the purchase is
not part of this study).

For comparison, on the one client property the appraisal's sketch page shows today's formulas
are 5 % low on the ground footprint, 31 % low on the first-story wall length and about 8 % low
on the roof (TAKEOFF_RESEARCH section 3.2 assumes a rectangle; the house is not one).

## 6. How established tools do it

Property measuring tools (paving, landscaping, roofing) and plan takeoff tools share one
pattern:

1. Find the property by address and confirm its boundary.
2. Choose the image; show its date and provider.
3. Scale first: automatic on georeferenced imagery, set from a known length on a plan. An
   auto-detected scale is never trusted without the user confirming it.
4. Named, coloured items of three kinds: area, length, count.
5. Draw by clicking corners; subtract cut-outs; move corners later.
6. AI pre-draws shapes by surface type into the same list, edited with the same tools.
7. The list and the image are one object: click a row and the shape lights up, and the reverse.
8. Slope, pitch or height is typed onto a shape, not drawn.
9. Export: the image with its shapes and legend, a table, and a read-only link.
10. State where the tool is weak (tree cover, shadow, old imagery) and leave the check with the
    user.

The ones nearest our problem license their imagery (several name Nearmap) rather than use
Google. One lets the user "upload your own" image.

Not found in any product reached: the AI's original kept beside the edited shape with a reason;
a per-quantity source record in the export; confidence per shape; inspection media and
measurements in one audit file; a measured quantity reconciled against a second source.

## 7. Proposed design

### 7.1 Two features

**Look** (inspect the property). Evidence comes only from media the owner or engineer supplies:
photos (today), plus a video walkthrough, a floor-plan or lidar scan export, and plans. A
Google 3D and Street View panel can sit on its own screen for viewing, with no other map on that
screen and nothing saved from it except the panorama ID and the date viewed. If that screen is
not worth its constraint, a "View on Google Maps" link is always allowed.

**Measure** (size an item). The engineer traces on an image Basis is allowed to keep:

* **An upload** (site plan, survey, floor plan, blueprint, or an aerial image the engineer has
  the right to use), scaled from one known length the engineer clicks. No imagery licence.
* **Public aerial imagery** found by address: the best state or county layer where one exists
  and carries no restriction, then USGS, then NAIP, with the pixel size and date always shown.
* **A commercial layer** only after Esri or Microsoft confirm tracing in writing, or a partner
  agreement is signed with an aerial vendor.

### 7.2 One record behind every quantity

Today a quantity has a formula and inputs (derived) or a sentence (typed). The proposal is one
record shape for all of them:

| Field | Meaning |
|---|---|
| kind | appraisal sketch, traced on an upload, traced on aerial imagery, formula, typed |
| value, unit | the quantity as used in the price |
| shape | the corners the engineer clicked, in map or page coordinates |
| image | which file or imagery layer, its capture date and pixel size |
| scale | how it was set: georeferenced, or the known length used |
| modifiers | height for a wall, slope for a drive, cut-outs |
| expected error | from the pixel size and the shape's perimeter, shown as a range |
| drawn by, at | the engineer, or the model and its version |
| replaces | the previous record, kept, with the reason for the change |
| exhibit | the image with the shape, stored for the report where the source permits |

Rules that follow from what is already decided:

* The engineer can replace any quantity; the AI's or the formula's original is kept (the
  track-changes rule). A reason is required, as for a unit price today.
* A quantity with no record stays unpriced and withholds the report (EVAL_REPORT 78). A traced
  shape is a record; a guess is not.
* A typed number still works. It is the lowest kind and says so.
* Area and length are computed from the corners on the earth's surface, not from screen pixels.
* Checks become review flags, not silent fixes: image dated after the placed-in-service date,
  image coarser than the item can bear, traced areas summing to more than the lot, a sketch
  total disagreeing with the printed living area.
* The report gains a measurement exhibit per traced item: the image, the shape, the value, the
  image's source and date, who measured it.

### 7.3 What the screen needs (the design is Bryan's)

Stated as needs, not layout:

* On every quantity, a visible answer to "where did this number come from", one click from the
  evidence and back.
* The list of measurements and the image in one view, highlighting each other.
* The image's date and pixel size, and the expected error, next to the value.
* The AI's draft visibly a draft, edited with the same tools as a manual shape.
* The original still visible after an edit, with who changed it and why.
* The missing-measurement blocker opening the measuring tool on that item.

### 7.4 AI pre-drawing, later

On public imagery we may run our own outlining (the photo localizer already outlines objects);
on Google or Nearmap imagery we may not. It ships only after it is measured against the hand
trace on the same properties, like every other step.

## 8. How we would measure it before shipping

* Answer key that costs nothing: appraisal sketch pages print the house and garage dimensions.
  Trace the same outline on each imagery source and compare, per source and pixel size.
* Second key: a small set of driveways measured on the ground by a design partner.
* Report per source: error on area and length, by item size; time per item against Google Earth
  Pro; the share of addresses with a 6-inch-or-better public layer; cost per study.
* Pre-drawing: the model's shape against the engineer's, before and after edits.

## 9. Order of work and what Bryan decides

1. Read the appraisal's sketch page (proposed as its own task): measured building dimensions
   from a document we already have.
2. The measurement record, the upload-and-scale tracer, and the report exhibit. No imagery
   licence; covers site plans, surveys and blueprints.
3. Public aerial imagery by address, with a coverage check on real addresses first.
4. Written confirmation from Esri and Microsoft; a conversation with an aerial vendor about a
   partner agreement and its price.
5. AI pre-drawing on imagery we may analyse.

Decisions that are Bryan's:

* The Look screen: a Google-only viewing panel, or a link out.
* Whether to pursue a commercial imagery agreement now or after step 3 shows the coverage gap.
* Who writes to Esri, Microsoft and the aerial vendors.
* The screen designs.

Five questions for the design partners:

1. For which items do you open Google Earth Pro on every study, and for which only sometimes?
2. When the client sends plans or a survey, do you still check them against imagery?
3. What do you keep as proof of a measurement, and has an examiner ever asked for it?
4. How do you size a kitchen's cabinet run or a room's flooring with only photos?
5. Would you share one study's takeoff worksheet with its quantities? The printed report we
   have shows dollars only.

## 10. Verified and not verified

Verified by reading the saved primary text: the Google, Mapbox, Esri, Microsoft and Nearmap
clauses quoted in section 4; the Earth Pro retirement date; the withdrawal of Google's drawing
tools; the IRS quotes; the code facts in section 3.

Not verified:

* Any vendor's answer on tracing or printing beyond its published terms; partner terms and
  prices for the aerial vendors; Esri's static-map rate; Azure's paid tile rate.
* Public high-resolution coverage for the addresses our customers actually have.
* The accuracy table's percentages were computed by the researcher from the papers' tables and
  the pixel-size figures are formula only.
* The founders' interview notes are second-hand; how often engineers re-check client-supplied
  numbers is not known.
* Whether a listing service would license its photos for this use.
* Nothing in section 7 has been built or tested, and the sketch check is one property.

## 11. Hands-on check: Google Earth and the alternatives on one property (2026-11-23)

Done by hand on one client property in a small Wisconsin town, in Google Earth on the web (the
free Standard plan), then with public sources only. The screenshots show the client's property
and are kept in the private eval data (`$BASIS_EVAL_DATA_DIR/work/earth_research_2026-11-22/`),
not here.

### 11.1 What Google Earth gave

* **History:** twelve dated images from 1992 to 2026. The earliest shows the lot before the
  house was built, which is the "what existed on the purchase date" check.
* **The newest image is the worst for measuring:** flown in August, with tree canopy over the
  driveway. Two of the twelve dates are leaf-off and sharp; an engineer has to go looking for
  them.
* **Measuring:** click corners, get area, perimeter and an elevation estimate, and save the
  shape to a project. Google states the results "are estimates, may not be 100% accurate, and
  are not substitutes for onsite or survey-grade measurements".
* **3D:** none. Google has no 3D buildings for this town; tilting the view tilts the flat
  photo. The "3D view of the house" exists only where Google has flown its 3D mesh.
* **Street View:** coverage on the street, with the house set back behind trees. No panorama
  loaded in the capture browser, so there is no screenshot of it.
* **Parcels and building outlines** are paid data layers ($75 and $150 per user per month), and
  the terms say the customer will not "download, export or copy Data layers".
* Google Earth Pro, the desktop program the cost segregation tutorials recommend, stops being
  downloadable on 2027-06-25.

### 11.2 What engineers use it for

Sourcing is thin: one detailed cost segregation tutorial (a software vendor's), the rest from
neighbouring trades. In order of how often each is mentioned: tracing paved areas for square
feet; historical imagery for what existed when; Street View when there was no visit; lawn and
landscaped area; perimeter and linear feet of curb and fence; lot boundary and acreage; roof
features; and saving the named shapes so that, in the tutorial's words, "you can show how you
measured it in Google Earth if the question comes up in an audit". No source was found for
counting parking stalls, poles or trees in it, or for reading stories from it.

The feature to match is therefore not the globe. It is a dated top-down image, a traced shape
with its area and length, and the shape saved with the study.

### 11.3 The same tasks without Google, on the same property

| Task | Source tried | What it gave |
|---|---|---|
| A sharp current image | The state's public leaf-off aerial service | 6-inch pixels, flown leaf-off in 2020: sharper for the driveway than Google's current image |
| The same, anywhere in the US | Federal NAIP (public domain) | 60 cm pixels: the house and drive are blurs; not usable for tracing a driveway |
| History | The same state service by year (2005 to 2022, plus a 2010 leaf-off) and federal NAIP by year | A dated series back to 2005; older federal scans exist but are not corrected for measuring |
| Measuring | Our own calculation on the clicked corners of the state image | Driveway about 5,800 SF and a fence line about 126 LF; pixel size puts the area within about 4 % |
| Lot boundary | The state's public parcel map ("public domain. There are no use restrictions") | Two parcels under one owner whose acres sum exactly to the appraisal's site area |
| Height and shape | Federal lidar (public domain), 2018 survey, 3.4 points per square metre | 27,000 points for the lot, roofs already labelled; roof peak about 10 m above ground; building length agrees with the appraisal's sketch |
| Street level | Open street-photo services | No dependable coverage in small towns |
| History, second opinion | Esri's imagery history viewer | Ten versions since 2014; not cleared for use outside Esri's products |

The driveway and fence figures are a rough hand trace made to prove the path works, not a
measurement of record. The state imagery service carries no licence statement, and the county
owns the 6-inch source: confirm the terms with the state and the county before relying on it.

### 11.4 Building our own 3D view

| Route | Allowed? | What it looks like | Useful for measuring? |
|---|---|---|---|
| Rebuild 3D from another provider's imagery or screenshots | No. Google's terms name it ("create 3D building models from 45° Imagery"); Apple, Microsoft, Esri, Nearmap and EagleView bar extraction or derivative works; none of the standard terms read permits it | Would look best | Would be good |
| Public lidar, public imagery and open building outlines | Yes | A dotted roof and tree shapes on terrain, or flat-topped boxes; no walls, windows or signs | Yes for footprint, height, roof form and slope |
| The owner's own phone video or photos, rebuilt into 3D | Yes, on the permissively licensed tools (several popular ones are research-only) | A good look-around outside when the capture succeeds; interiors often fail | Poor unless the phone has lidar or a known length is given |
| Guided phone scan with lidar (room scanner style) | Yes | Boxes, no appearance | Best of the owner routes indoors; needs a recent Pro iPhone and a native app |

Taking screenshots of a different service does not change the answer: every commercial imagery
licence read forbids deriving a model from it, and changing the capture method does not change
the licence. The only sources free of that are government data and media the owner makes.

### 11.5 What this changes in section 7

* Add a **date slider** to the measuring image, always showing the capture date, with leaf-off
  dates marked.
* Add a **parcel outline** where the state publishes one, and compare its area with the
  appraisal's site area as a check.
* Add a **height panel** from public lidar where it exists: footprint, roof peak, slope.
* Keep **Look** to owner-supplied media plus a link out to Street View. Do not promise a 3D
  model of the house.
* Imagery quality is per state and county. The first build step is a coverage table for the
  states our design partners work in.

## 12. Public imagery and parcels by state: 32 states surveyed (2026-11-23)

Question: section 11 found sharp, free, traceable imagery for one Wisconsin property. How common
is that? Method: for the 30 most populous states plus Hawaii and Wisconsin, find the best
publicly served aerial imagery and the statewide parcel layer, request an 80 m x 60 m image at
a city point and a small-town point with no key or login, and read the pixel size and the use
terms from the service or its metadata. Eight of the services were re-checked by a second
request (pixel sizes matched). The other 18 states were not surveyed.

"Sharp" means 6-inch (15 cm) pixels or better, which section 5 puts at roughly 3 to 7 % on a
driveway. "Terms" is what the service or its metadata says, not legal advice; no agency was
asked.

### 12.1 Result

| Group | States | Count |
|---|---|---|
| Sharp, statewide or nearly, permissive terms | CT, IL (outside the Chicago area), IN, KY, MA, MD, MO, NC, NJ, OH, PA | 11 |
| Sharp but partial, or terms not stated or restrictive | FL, LA, MN, OK, SC, TN, TX, VA, WI | 9 |
| 1 ft (30 cm) only for most of the state | MI, NY, OR | 3 |
| Federal imagery only (30 to 60 cm) | AL, AZ, CA, CO, GA, HI, NV, UT, WA | 9 |

The four largest states are not in the first group: California has only 60 cm federal imagery
statewide, Texas is sharp in about 45 of 254 counties and not in its largest metros, Florida is
served county by county up to 2021 with no stated terms, and New York is 1 ft for about 70 % of
its area. Public imagery alone therefore covers about a third of the surveyed states well. The
upload-and-trace tool (section 7.1) and a commercial imagery agreement are still needed for the
rest, and for California first.

### 12.2 State by state

| State | Sharpest public imagery | Coverage | Leaf-off | Terms as stated | Open statewide parcels |
|---|---|---|---|---|---|
| AL | None from the state | Federal 60 cm | No | Public domain (federal) | No |
| AZ | Federal 30 cm; one county's own | Statewide at 30 cm | No | None stated | No; two large counties |
| CA | None from the state | Federal 60 cm | No | Public domain (federal) | Parcel numbers and addresses only |
| CO | Federal 60 cm; Denver area 6 in | Denver area only | No | Denver set open with credit | 44 of 64 counties, totals only |
| CT | 3 in, 2023 | Statewide | Yes | "No restrictions apply to these data" | Yes, public domain, with year built and living area |
| FL | 6 in, by county, to 2021 | County by county | Not stated | None stated | Yes, with values, year built, living area |
| GA | None from the state since 2010 | Federal 60 cm | No | Public domain (federal) | No |
| HI | 60 cm satellite | Statewide | n/a | Commercial use prohibited | Yes, identifiers and acres |
| IL | 6 in, 2025 | About 75 % of the state; not Chicago and its collar counties | Looks leaf-off | "in the public domain" | No |
| IN | 6 in, some 3 in, 2022 to 2025 | Statewide | Looks leaf-off | Public domain (CC0) | Outlines and addresses only |
| KY | 3 in, 2022 to 2024 | Statewide | Yes | Public domain | No |
| LA | 6 in from 2010 everywhere; newer in places | Partial for recent years | Looks leaf-off | "For viewing and download purposes"; some layers viewing only | No |
| MA | 15 cm, 2025 | Statewide | Yes | "No restrictions apply to these data" | Yes, with year built, building area, stories |
| MD | 6 in statewide, 3 in in places | Statewide | Looks leaf-off | Free to distribute with credit | Yes, with year built, structure area, stories |
| MI | 30 cm | Statewide | Not stated | "view only" | No |
| MN | 2 to 9 in where a county contributes | About 20 of 87 counties, including the Twin Cities | Looks leaf-off | Use "for any purpose" (general disclaimer) | 60 of 87 counties, with year built and finished area |
| MO | 6 in, 2023 to 2024 | Statewide | Yes | Open with credit (from a registry entry, not the service) | No |
| NC | 6 in, four-year cycle | Statewide | Looks leaf-off | "free and unrestricted use policy" | Yes, with values and structure year |
| NJ | 6 in, 2025, as files; 1 ft as a live service | Statewide | Yes | Open, credit requested | Yes, with values and year built |
| NV | Federal 60 cm; Reno 6 in | Reno only | Not stated | None stated | Viewable, export forbidden |
| NY | 1 ft; 6 in in about 30 % of the area | Statewide | Yes | Disclaimer only | 38 of 62 counties, rich building fields |
| OH | 3 to 6 in, 2017 to 2026 | Statewide | Yes | "available for public use at the user's own risk" | Outlines and land use only |
| OK | 15 cm, 2025 | Eastern 60 % | Yes | None stated | No |
| OR | 1 ft, summer 2024 | Statewide | No | None stated | Not confirmed |
| PA | 6 in, two cycles 2018 to 2023 | Statewide | Spring, mixed | "No restrictions apply to these data" | No |
| SC | 6 in, 2023 | Statewide | Yes | Disclaimer only | No |
| TN | 6 in in about a quarter of the state, 1 ft elsewhere | Statewide | Looks leaf-off | Copyrighted by the state; no resale | 86 of 95 counties, outlines only |
| TX | 3 to 6 in regional flights | About 45 of 254 counties; not Houston, Dallas, San Antonio | Yes | Public domain (CC0) | Yes as a download, with values, year built, area |
| UT | 15 cm exists but is licensed to public bodies | Federal 60 cm for us | n/a | "may not be used in publicly-available applications" | Yes, with building fields in some counties |
| VA | 1 ft; 6 in and 3 in in about 7 % of the area | Statewide | Spring | None stated; a statutory notice is required on documents | Outlines only |
| WA | Federal 60 cm; county imagery is vendor-sourced | Federal only | No | County sets unclear | Yes, with values, no building fields |
| WI | 3 to 6 in by county, 2018 to 2025 | Statewide | Yes | None stated; counties own the source | Yes, public domain, values but no building fields |

### 12.3 What the survey changes

* **Per-state plumbing.** No two states serve imagery the same way: one image service, one layer
  per county, tiles only, or files in a bucket. A measuring map needs a small registry of
  sources (address, pixel size, year, terms) rather than one integration.
* **History** is served in most of the sharp states, year by year, so the date slider of
  section 11.5 is realistic there.
* **Parcels with building facts** (year built, building area, sometimes stories) are open in
  Connecticut, Florida, Maryland, Massachusetts, New Jersey, North Carolina, Texas and parts
  of Minnesota, New York and Utah. That is a second source to check the appraisal against.
* **Terms still need an answer in writing** wherever the table says "none stated", "disclaimer
  only" or "view only", and one state (Virginia) regulates who may show measurements on a
  document, which needs a legal read before a traced exhibit is produced there.
* **Not verified:** the 18 states not surveyed; coverage shares, which are counts from index
  layers rather than published figures; "looks leaf-off", which is read from test images; and
  any agency's answer on commercial use.

## Sources

* IRS, Cost Segregation Audit Technique Guide, Publication 5653 (rev. 2-2025).
  https://www.irs.gov/pub/irs-pdf/p5653.pdf ; Chief Counsel Advice 199921045.
  https://www.irs.gov/pub/irs-wd/9921045.pdf
* Google Maps Platform Terms of Service. https://cloud.google.com/maps-platform/terms ; FAQ.
  https://developers.google.com/maps/faq ; Map Tiles API policies.
  https://developers.google.com/maps/documentation/tile/policies ; deprecations.
  https://developers.google.com/maps/deprecations ; pricing.
  https://developers.google.com/maps/billing-and-pricing/pricing
* Google Geo Guidelines.
  https://about.google/brand-resource-center/products-and-services/geo-guidelines/ ; Earth Pro
  update notice. https://support.google.com/earth/answer/168344
* Mapbox Product Terms (2026-10-20). https://www.mapbox.com/legal/product-terms
* Esri: static maps. https://doc.arcgis.com/en/arcgis-online/reference/static-maps.htm ;
  Location Platform agreement E204.
  https://www.esri.com/content/dam/esrisites/en-us/media/legal/platform/platform-legal.pdf ;
  pricing. https://location.arcgis.com/pricing/
* Microsoft Product Terms, Azure Maps.
  https://www.microsoft.com/licensing/terms/productoffering/MicrosoftAzure/MCA
* USDA NAIP. https://catalog.data.gov/dataset/national-agriculture-imagery-program-naip-imagery ;
  https://registry.opendata.aws/naip/ ; USGS lidar. https://registry.opendata.aws/usgs-lidar/
* Accuracy: Harrington et al. 2017 (SAE) and Kulhavy et al. 2019 on Google Earth lengths and
  areas; ASPRS Positional Accuracy Standards, editions 1 and 2.
* Practice: https://overlineiq.com/blog/diy-cost-segregation-study-guide ;
  https://www.recostseg.com/site-visit/ ;
  https://virtualcostsegregation.com/blog/no-site-visit-cost-segregation-accuracy/ ;
  https://www.costsegregation.com/faq/
* Measuring tools: Aspire PropertyIntel, SiteRecon, Roofr, Bluebeam Revu, Togal.AI, Kreo (their
  help and product pages, read 2026-11-22).
