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
6. **What Basis would add that no product found does:** the AI's original kept beside the
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
