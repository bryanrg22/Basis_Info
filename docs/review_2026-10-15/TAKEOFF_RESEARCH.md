# Deriving quantities for a residential study without site measurements (2026-10-29)

Data-free. Question: how does a cost segregation engineer or estimator arrive at the
quantities a unit cost is applied to (room floor areas, roof area, siding area, gutter
length, site items) when the study has an appraisal and photos but no plans, no site plan
and no field measurements? What does the product do today, what should it do, and what
is each assumption's source?

## 1. What the standard says

**The IRS Audit Techniques Guide (Publication 5653, rev. 6/2022).** Chapter 4 describes the
"detailed engineering cost estimate approach" for acquired property (C.2): the same steps as
the detailed engineering approach from actual cost records, except that costs are
estimated. Step C.1(3)(g) is "prepare quantity take-offs for all assets" and (h) "apply unit
costs to each asset". C.2(2): estimates are based on construction-based documents
("blueprints, specifications, contracts, job reports, change orders, payment requests,
invoices, appraisals") and "reliable published sources (e.g., R. S. Means or Marshall
Valuation Service)", with the source "clearly referenced, including identification of the
specific volume, page, and item number". C.8(2): in a quality study "take-offs are carefully
documented to show derived unit costs"; (3): "cost estimates are always reconciled to an
acquisition price or a total project cost", and a quality study "minimizes this controversy
by clearly explaining and documenting the methodology used to assign costs to each asset".
C.6 warns against the "rule of thumb" approach (percentages with no documentation). C.2(4)
recommends a field inspection for every study. Chapter 4 D lists the elements of a quality
study, among them the take-off documentation (C.8) and the reconciliation (C.10).

So the standard does not require measured quantities; it requires that every quantity be
derived by a documented method from documented inputs, with the cost source cited, and
that the total reconcile to the basis. An estimated quantity with its formula and its inputs
shown is acceptable; an unexplained constant is not.

**Estimating practice.** The RSMeans square-foot method (RSMeans Square Foot Costs; the
Penn State building-industry text, chapter 7 "Modeled Square Foot Cost Estimating") prices
a building from its floor area, with adjustments for the perimeter (the exterior wall) and
the story height; the RSMeans online square-foot estimator derives the perimeter from the
entered area and the building type when it is not given. The Marshall & Swift residential
method (as reproduced in the California Assessors' Handbook AH 531, Marshall & Swift based)
computes area and perimeter for rectangular and angular footprints and classes the shape by
its perimeter-to-area ratio. Both are the methods cost segregation engineers use when the
acquisition has no cost records (ATG C.2): a square-foot model for the concealed building
systems and unit costs on take-offs for the components that can be identified.

Vendor descriptions of practice on acquired residential property (Engineered Tax
Services "Can cost segregation be done without architectural plans?"; WCG "cost
segregation study methods") agree: a study without plans relies on the site inspection,
the photos, the appraisal's areas and a cost database; quantities are derived from the
building's square footage and counts, not measured. Where a sketch with dimensions exists
(the URAR's sketch page), room areas are read from it; where it does not, floor area is
allocated to rooms in proportion to their typical size. The appraisal's sketch is a later
input (the product does not read it yet); this note covers the allocation.

## 2. What the product does today, and what changes

Today (`basis/domain/rooms.py`, `basis/domain/takeoffs.py`, `process_assets`): every room
is a typical area by room type (kitchen 150, bathroom 75, bedroom 150, office 120, hallway
50, storage 30, everything else 150 square feet); area and perimeter items are measured
from it once per room; the 11 exterior and site labels the cost table prices per SF or LF
(roof, siding, gutter, driveway, sidewalk, patio, fence, retaining wall, concrete pad,
parking lot, landscaping) have no quantity at all (`COST_NEEDS_QUANTITY`). The appraisal's
areas (GLA, basement, garage, site), stories and location are extracted and unused; the
cost year and the location factor are deployment settings.

Change: the takeoff reads the appraisal's extraction fields and derives (a) each room's
floor area from the GLA, (b) the roof, siding and gutter quantities from the footprint,
(c) the landscaping area from the site area, (d) the garage area from the cost approach's
garage line or the car count, with every input, formula and assumption recorded on the
asset's takeoff. Items with no derivable input (driveway, sidewalk, patio, fence, retaining
wall, concrete pad, parking lot) stay `COST_NEEDS_QUANTITY`, now with the measurement
named. The cost index comes from the study: the state's factor and the appraisal's
effective-date year (the placed-in-service date once the intake carries it).

## 3. The derivations

Notation: GLA = gross living area above grade (square feet); S = stories above grade;
B = basement area; f = basement finish fraction; G = garage area; A = site area.

### 3.1 Room floor areas: proportional allocation of the GLA

Each above-grade room is given a share of the GLA proportional to its typical area
(weight) among the house's rooms:

    area_i = GLA x w_i / W,   W = max(W_program, W_study)

where `w_i` is the typical area of the room's type (section 4), `W_study` is the sum of the
weights of the study's above-grade rooms, and `W_program` is the sum of the weights of the
rooms the appraisal lists (the URAR room count, bedrooms and baths: one kitchen, the
bedrooms, the full and half baths, and the remaining rooms at the "other room" weight).
Two properties make the choice defensible:

* Conservation: the room areas never sum to more than the GLA, however many rooms the
  photos produce (the room grouping over-counted 49 rooms against 33 labelled on the client
  study; with W_study in the denominator the extra rooms share the same floor instead of
  adding floor).
* The unphotographed rooms are counted: the program denominator keeps a photographed
  kitchen from being charged a third of the house when only two rooms were photographed.
  The ratio k = GLA / W is the house's scale: how much larger (or smaller) than typical its
  rooms are, circulation and wall thickness included.

Basement rooms (room type `basement`, or a room on floor 0) are sized at the same scale,
`w_i x k`, capped so that together they do not exceed the finished basement area `B x f`
(f = 1 when the finish is not printed; a photographed basement room is finished). A garage
room takes the garage area (3.5). Exterior and parking "rooms" have no floor area; a
surface item in one of them keeps today's typical area, recorded as such.

Without a GLA every room keeps today's typical area, recorded as `typical_for_room_type`
or `default_150_sf` exactly as before.

### 3.2 Footprint and perimeter

    footprint = GLA / S            (S = 1 when stories are not printed)
    ground footprint = footprint + G   (an attached garage shares the roof and the ground-story walls)

The perimeter of a rectangle of area F and aspect ratio r (long side = r x short side):

    short = sqrt(F / r),  perimeter = 2 x short x (1 + r)

with r = 1.5 (a square gives 4 sqrt(F); a 1.5:1 rectangle 4.08 sqrt(F); the choice matters
by 2% and is recorded). The legacy perimeter estimate for a room (`4 x sqrt(area)`) is the
r = 1 case and is unchanged.

### 3.3 Roof

    roof plan area = ground footprint + perimeter x overhang
    roof area      = roof plan area x pitch factor

Pitch factor = sqrt(1 + (rise/12)^2): 1.054 at 4/12, 1.118 at 6/12, 1.202 at 8/12 (the
roofing calculators and the roof-pitch multiplier tables agree; the formula is the secant
of the slope). The default is 6/12 (1.118), the common residential pitch; the overhang is
1 ft per side. Waste (10% gable, 15% hip in the estimating guides) is an ordering allowance,
not an installed quantity, and is not applied.

### 3.4 Siding (exterior wall)

    gross wall = ground perimeter x H + dwelling perimeter x H x (S - 1)
    siding     = gross wall x (1 - openings share)

H = 9 ft per story (an 8 ft ceiling plus the floor structure; the exterior wall of a story
is taller than its ceiling). Openings: the residential window-to-wall ratio is 12-15%
(Penn State PHRC study of Pennsylvania detached houses, about 12%; the IRC 2000 15% glazing
target; GridLAB-D's residential default 0.15); doors add a little. 15% is deducted. Gable
ends are not added (they roughly offset the deduction of the garage's shared wall). The
estimating guides' approach, perimeter x height less openings, is the same.

### 3.5 Gutters

Gutters run along the eaves (the horizontal drip edges), not the rakes. For a gable roof,
the eaves are the two long sides; for a hip roof, the whole perimeter. The roof style is
not an extraction field, so the gable case (the more common) is assumed:

    gutter length = 2 x (long side + 2 x overhang)

The hip case (the full perimeter) is about 60% longer; the assumption is recorded on the
takeoff for the reviewer.

### 3.6 Garage

    garage area = cost approach garage line, else cars x 240 SF

240 SF (12 x 20 ft) is the common one-car size (Angi, Reolink); two-car garages are built
at 360-480 SF, so cars x 240 is a lower bound for a two-car garage, recorded as an
assumption and used only when the cost approach has no garage line.

### 3.7 Landscaping

    landscaping = site area - ground footprint

Hardscape (driveway, walks, patio) is not deducted because its area is the unknown of
3.8; the landscaping is therefore an upper bound and says so. A site area printed in acres
is converted at 43,560 SF per acre (the extraction already derives `site.area_sqft`).

### 3.8 Not derivable from the appraisal: needs a measurement

Driveway, sidewalk, patio, fence, retaining wall, concrete pad, parking lot. The URAR
prints "Driveway surface", "Patio/Deck" and "Fence" as materials, never as areas; the
legacy 1004 sketch has no site plan. An estimator measures these on site or from aerial
imagery. The product leaves them `COST_NEEDS_QUANTITY` and names the measurement in the
takeoff (`needs_measurement`: "driveway area in SF"). No default is invented: a default
with no input is the "rule of thumb" the ATG objects to (C.6).

### 3.9 Cost index

* Location (COST_INDICES.md, EVAL_REPORT 45; before that a legacy state table with no
  source): the RSMeans Residential 2020 Location Factor of the study's city by the first
  three digits of the ZIP (the intake's `zip`, else the appraisal's `subject.zip`), else the
  mean of the state's listed cities (the intake's `state`, required before a study starts
  since EVAL_REPORT section 37, else the appraisal's `subject.state`), else the
  `COST_LOCATION_FACTOR` setting when none of them is in the table. The row cites the city
  row or the cities averaged, with the book and page.
* Year: the placed-in-service year (the intake's `placed_in_service_date`), else the year
  of the appraisal's effective date (the purchase appraisal is dated at or near the
  acquisition), else the `COST_YEAR` setting; a year before the Historical Cost Index
  (1970) falls back to the next source with the reason recorded; a year after the latest
  published index is priced at that index with the assumption on every row and the asset
  flagged (COST_INDICES.md section 4).
* Use: the intake's `property_use` is the property class (27.5 or 39 years for the
  building rows, and the typical-area family when no GLA is extracted), in place of the
  `PROPERTY_CLASS` setting.

Both factors and their sources are written on the cost row (`source_row`).

## 4. Assumptions as named constants

| Constant | Value | Used for | Source |
|---|---|---|---|
| `ROOM_WEIGHTS_SF` | kitchen 150, bathroom 75, bedroom 150, living_room 250, dining_room 150, family_room 250, office 120, laundry 50, hallway 50, stairway 40, closet 20, lobby (foyer) 60, storage 30, mechanical 40, attic 150, basement 150, other 150 | allocation weights (3.1); the typical areas where the legacy table has them | legacy `ROOM_AREA_ESTIMATES` (kept unchanged for the fallback); design-industry room-size guides: bedrooms 100-150, full bath 40-80, kitchen 150-200, living 250-400 SF |
| `OTHER_ROOM_SF` | 200 | the appraisal's rooms that are not the kitchen or bedrooms, in `W_program` | the mean of the living, dining, family and office weights |
| `HALF_BATH_SF` | 40 | half baths in `W_program` | a 5 x 8 ft powder room |
| `DEFAULT_STORIES` | 1 | footprint when stories are not printed | recorded assumption |
| `FOOTPRINT_ASPECT_RATIO` | 1.5 | perimeter from a footprint (3.2) | rectangular footprint; AH 531 (Marshall & Swift) shape classes; 2% from a square |
| `ROOF_PITCH_FACTOR` | 1.118 | roof area (3.3) | 6/12 pitch, sqrt(1 + 0.25); the roof-pitch multiplier tables |
| `EAVE_OVERHANG_FT` | 1 | roof plan area, gutter length | typical 12-inch eave |
| `STORY_WALL_HEIGHT_FT` | 9 | siding (3.4) | 8 ft ceiling plus the floor structure |
| `WALL_OPENINGS_SHARE` | 0.15 | siding (3.4) | residential window-to-wall ratio 12-15% (PHRC; IRC 2000; GridLAB-D default) |
| `GUTTER_EAVES` | 2 (gable) | gutters (3.5) | gable roof: the two long eaves |
| `GARAGE_SF_PER_CAR` | 240 | garage area from the car count (3.6) | one-car garage 12 x 20 ft |
| `SF_PER_ACRE` | 43,560 | site area printed in acres | definition |
| `BASEMENT_FINISH_DEFAULT` | 100% | finished basement area when the finish is not printed | a photographed basement room is finished |

Every constant is written into the takeoff's `inputs` and `assumptions` when it is used,
so the export can show, for a kitchen carpet: "room area 199.6 SF = GLA 1,850 x 150 / 1,390
(kitchen weight / program weight, scale 1.33)" and, for a roof: "roof 2,214 SF = (1,850 / 2
stories + 480 garage + 180.6 x 1 ft overhang) x 1.118 (6/12 pitch)".

## 5. What this does not do (follow-ups)

* Read the sketch page: the URAR sketch carries the exterior dimensions and often the room
  dimensions; a reader would replace 3.1 and 3.2 with measured values.
* Aerial or site-plan measurement for 3.8.
* The RSMeans city cost index by ZIP and the historical cost index by year (EVAL_REPORT
  27.7).
* Concealed systems from a square-foot model (WORKFLOW_REVIEW section 6, item 5).

## Sources

* IRS, Cost Segregation Audit Techniques Guide, Publication 5653 (rev. 6/2022), chapter 4
  C.1-C.2, C.6, C.8, C.10, D. https://www.irs.gov/pub/irs-pdf/p5653.pdf
* RSMeans, Square Foot Costs (annual); "Using the Square Foot Estimator", RSMeans Data
  Online. https://www.rsmeans.com/using-square-foot-estimator-rsmeans-online
* Penn State, Introduction to the Building Industry, chapter 7 "Modeled Square Foot (SF)
  Cost Estimating". https://psu.pb.unizin.org/introductiontothebuildingindustry/chapter/modeled-square-foot-cost-estimating/
* California State Board of Equalization, Assessors' Handbook AH 531, Residential Building
  Costs (Marshall & Swift based): area computation, shape classification.
  https://www.boe.ca.gov/proptaxes/pdf/ah53121.pdf
* Roof pitch multipliers: https://myrooff.com/roof-pitch-multiplier ;
  https://www.vertigraph.com/tools/roofing-square-calculator
* Siding take-off (perimeter x height less openings): Maine DOE CTE "Math in CTE:
  Construction, Wood Siding". https://www.maine.gov/doe/sites/maine.gov.doe/files/inline-files/CTE%20-%20Math%20in%20CTE%20Construction%20Wood%20Siding%20-%2002.18.2025.pdf
* Window-to-wall ratio: Penn State PHRC, "Glazing ratios of new houses in Pennsylvania"
  (about 12%; IRC 2000 target 15%). https://www.phrc.psu.edu/assets/docs/Publications/A68.pdf ;
  GridLAB-D residential default 0.15. https://gridlab-d.shoutwiki.com/wiki/Window_wall_ratio
* Gutters along eaves, gable vs hip: https://www.angi.com/articles/measure-gutters.htm
* Garage sizes: https://angi.com/articles/single-car-garage-size.htm ;
  https://reolink.com/blog/standard-garage-size/
* Room sizes: https://blog.designfiles.co/average-room-size/
* Practice without plans: https://engineeredtaxservices.com/?p=25216 ;
  https://wcginc.com/kb-rental-property/cost-segregation-study-methods/
