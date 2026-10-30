# The structure model: a complete replacement cost that reconciles to the price (2026-10-30)

Data-free. Question: the basis step (EVAL_REPORT sections 36, 37, 42.6) sums the priced
assets' replacement cost new (RCN) and scales them to the depreciable basis by one factor
(2.4 on the client property), which the IRS Cost Segregation Audit Techniques Guide
(Publication 5653, "ATG") says is "not an appropriate cost estimation methodology". What
does the guide ask for instead, how do estimators derive the replacement cost of the
building systems that photographs cannot show (foundations, framing, partitions, painting,
site utilities, fire alarm, data cabling, access control: 39.7 % of the engineer's dollars
on the client property), how is that figure depreciated and reconciled to the purchase
price less land, and what does the allocation do when the two do not agree?

Code: `basis/domain/structure.py` (pure), the concealed rows written by
`basis/activities/process_assets.py`, the result in `basis/evidence/result.py`
(`summary.basis` of the export and `GET /v1/studies/{id}/result`), rules rows in
`basis/domain/rules.py`, the offline replay `evals/structure_replay.py`. Page numbers are
PDF pages of the corpus documents unless marked "printed".

## 1. What the standard says

**The guide (ATG chapter 3).** A(6), p.27: when cost records are not available (an
acquisition) the costs "must be estimated using the construction cost data, methods, and
techniques normally employed for a property appraisal"; the estimates are replacement cost
new, "which include indirect costs", adjusted "for the asset's age and condition at the
acquisition date, including physical depreciation, functional obsolescence, and economic
obsolescence", adjustments that "are generally expected to be different among the various
items of acquired property"; and "the total replacement cost new less depreciation (RCNLD)
of the acquired items of property, along with the fair market value (FMV) of the land,
should reasonably approximate the total purchase price". C.2, pp.29-30: the detailed
engineering cost estimate approach prices every item from "reliable published sources
(e.g., R. S. Means or Marshall Valuation Service)" with "the specific volume, page, and item
number" cited, and "if detailed cost estimates are prepared methodically, then the total
RCNLD of the buildings, land improvements, and personal property should reasonably
approximate the total purchase price of the property less the FMV of the land". C.2(4): the
inspection documents "the size, and types of building systems (HVAC, plumbing, fire
protection, electrical, data and communications, etc.)". C.4(3), p.31: mixing single-unit
costs for the personal property with a gross-square-foot cost for the building "can produce
a skewed result in favor of § 1245 property". D(3)-(6), pp.32-33: "It is not an appropriate
cost estimation methodology to apply a pro-rata step-up factor to the individual costs
estimated for the various improvements and personal property merely to reconcile to the
total purchase price"; a sum of RCNLD that "does not tie" to the price less land "is
indicative of inaccuracies in the cost segregation study"; "an analysis of the available data
and methodology should be conducted to assess and determine the source of the difference",
whose typical causes are an incorrect land FMV, assets or quantities not identified,
incorrect cost estimates and factors, incorrect depreciation or obsolescence factors, and
indirect costs not accounted for.

**Indirect costs (ATG chapter 4 C.11, p.40; chapter 3 C.1(3)(i), p.29).** "Indirect costs
must be allocated proportionately to the basis of the specific assets to which they relate";
project-wide indirect costs "on a pro rata basis", costs specific to one asset to that asset.
The contractor's overhead, profit and plans in a square-foot model are project-wide.

**Classification of the concealed systems (ATG Exhibit A, the residential rental matrix,
pp.297-322).** Concrete Footings and Foundations - Building, p.299, section 1250. Walls -
Exterior ("studs, framing, sheetrock, insulation, windows, doors, exterior façade"), p.321,
section 1250. Walls - Interior ("non-load bearing partitions regardless of height (typically
constructed of studs and sheetrock or other materials)"; "rough carpentry and plaster, dry
wall or gypsum board, and other finishes"), p.321, section 1250. Roof ("joists, rafters,
deck, shingles, vapor barrier, skylights, trusses, girders, and gutters"), p.317, section
1250. Wall Coverings - Permanent ("Includes interior and exterior paint"), p.320, section
1250. Plumbing - Building, p.315; HVAC - Residence, p.310; Electrical Branch Circuits -
Building, p.301, and Electrical - Primary and Secondary EDS - Building, p.303: section 1250.
Fire Protection and Alarm Systems, p.305, section 1250. Site Utilities ("the systems that
are used to distribute utility services from the property line to the building. Includes
water, sanitary sewer, gas, and electrical services"), p.319, section 1250. Data Cable and
Wiring ("All components of a telephone, internet, cable TV, satellite TV, and/or Wi-Fi
systems"), p.300, section 1245, asset class 57.0, 5 years. Access control: Doors, p.300
("door hardware (such as doorknobs, closers, kick plates, hinges, locks, automatic openers,
etc.). Includes computerized door locks, encoders, computers, and other associated hardware
of the computerized lock system") and Security Systems - Building, p.318 ("window and door
locks; card key access systems; keyless entry systems"), both section 1250. The engineer's
report puts Access Control in 5-year property; the guide names the item on the building
side, so the row is a building row marked contested (the rules table's decision rule,
CLASSIFICATION_CONVENTIONS.md section 4) and a hit goes to the engineer, whose ruling the
firm-conventions layer then carries forward.

**Square-foot models (RSMeans Residential Costs 2020, in the corpus).** The residential
square-foot section prices a dwelling by class of construction (economy, average, custom,
luxury), building type (1, 1-1/2, 2, 2-1/2, 3 story, bi-level, tri-level), exterior wall and
living area ("Base cost per square foot of living area"), with additions for a finished or
unfinished basement per square foot of living area and lump sums for garages, baths and
fireplaces (the model's facing page). Every model page lists ten components with the cost
per square foot of living area and its "% of Total (rounded)": 1 Site Work (site preparation
for slab, trench excavation for the foundation wall), 2 Foundation, 3 Framing (exterior
walls, roof, floor, "2" x 4" interior partitions"), 4 Exterior Walls (siding, housewrap,
insulation, windows, exterior doors), 5 Roofing (shingles, felt, gutters, flashings), 6
Interiors (gypsum wallboard walls and ceilings primed and painted, interior doors, flooring,
stairway), 7 Specialties (kitchen cabinets and countertop, kitchen sink, water heater), 8
Mechanical (a three-fixture bathroom, the heating system), 9 Electrical (service, wiring,
receptacles, switches, door bell, appliance circuits, "communications cabling", lighting
fixtures), 10 Overhead (the contractor's overhead and profit, and plans or design or
architect's fees by class). The "How to Use" page (printed p.1-7; the sample entry is the
Average 2 Story model) defines the columns; the instructions (printed p.15, PDF p.29) state
the method's accuracy: "No residence will match a model exactly. ... At this level of
estimating, a variation of +/- 10% should be expected." The models are national (the
location factor by the first three digits of the ZIP code adjusts them) and the components
include the installing contractor's overhead and profit.

**Marshall & Swift.** The Residential Cost Handbook's segregated method prices the
superstructure component by component (foundation, frame, floor, plumbing, electrical) and
its calculator method is the square-foot method with refinements for shape, story height and
quality; the Michigan Assessors' Manual reproduces the segregated method's structure
(foundation applied to the total floor area including basements). The handbook is licensed
and not in the corpus; the RSMeans residential models in the corpus are the equivalent
published source and are the one every priced row already cites, so one source and one
technique serve the whole schedule (ATG C.2(2): "consistent estimating techniques and unit
cost data sources are used for all of the items").

**The appraisal's cost approach.** The URAR cost approach (Fannie Mae form 1004, the
"Cost Approach to Value" section) reports the opinion of site value, the dwelling's square
feet and cost per square foot, the basement and garage the same way, extras ("mechanicals
and miscellaneous" on the client appraisal), the total estimate of cost new, physical,
functional and external depreciation, the depreciated cost of improvements, the "as is" value
of site improvements, the source of cost data and its effective date, and the remaining
economic life. The appraiser's cost new is a replacement cost from a cost service at the
property's location, and the depreciation is the appraiser's own conclusion, usually by
the age-life method (effective age over total economic life; on the client appraisal
15 / (15 + 55) = 21.4 %, which is exactly the printed physical depreciation over the printed
cost new). The guide names appraisals among the construction-based documents an estimate
rests on (C.2(2)) and the appraisal's methods as the ones to use when costs must be estimated
(A(6)). The appraiser's cost new and the RSMeans square-foot model are therefore both
admissible sources for the building's RCN; which one the product uses by default was decided
by the sweep of section 7 (the square-foot model, the appraisal's figure one setting away and
always on the result as the independent check).

**Depreciation.** The age-life method (The Appraisal of Real Estate; the URAR's own inputs)
expresses accrued depreciation as effective age over total economic life (effective age plus
remaining economic life), applied to the replacement cost new. The appraiser gives all three
numbers: the dollar figure of physical depreciation, the effective age (the improvements
section) and the remaining economic life (the cost approach). The guide asks for
depreciation that differs "among the various items" because of their "varying expected
useful lives"; a per-class age-life needs an effective age and a life per class that neither
the appraisal nor the photographs give (a carpet's or a boiler's installation date), so this
build applies the appraiser's rate to every row, says so in a check, and leaves the per-class
refinement open (section 6).

## 2. What the product did and what changes

Before: `summary.basis.allocation` scaled every priced row by `T / ΣRCN` (2.39 on run 3A,
2.50 on run 3B of 2026-10-29), `uniform_factor` was an error by design, `by_system` was
null because no asset carried a building system, and `not_produced` listed seven concealed
systems with no dollars. Bucket shares were those of the photo-visible items alone:
39-year 55-61 % against the engineer's 80.2 %.

After, in order:

1. **A top-down building RCN** (`building_rcn`): the RSMeans square-foot model for the
   house's class, type and size (the sweep's pick, section 7), else the appraisal's cost
   approach (the printed total cost new, cross-checked against the sum of its parts; the
   runner-up, and the source whenever no living area sizes a model, or when
   `STRUCTURE_RCN_SOURCE` says so), else nothing (the result says `missing: ["building_rcn"]`
   and the allocation is withheld).
2. **The split into the ten RSMeans components** by the model page's dollars per square foot
   (section 3.3), with Overhead spread pro rata over the other nine (ATG C.11).
3. **A building system and a model component for every asset** (section 3.4), so
   `by_system` is filled and every bottom-up item can be subtracted from its component.
4. **The carve-out**: for each component, top-down minus the itemized assets mapped to it;
   the residual is the concealed remainder, written as an asset of its own with the
   quantity, unit, formula, inputs, assumptions and source (section 3.5). A negative residual
   clamps to zero and is reported (the itemized prices exceed the model: a diagnosis, not an
   adjustment).
5. **The small concealed systems the model does not break out** (fire alarm, data cabling,
   access control, site utilities): a code- or standard-derived quantity priced from the cost
   table's unit cost for the label, carved from their component where the model includes
   them (section 3.6).
6. **Depreciation** RCN -> RCNLD at the appraiser's physical depreciation rate (else the
   age-life ratio of the appraiser's effective age and remaining economic life), the same
   rate on every row, recorded per row (section 3.7).
7. **Reconciliation** of the RCNLD total to the purchase price less land (B), with the gap,
   its share, the by-system picture (top-down, itemized, concealed) and the guide's list of
   causes, each tied to what the data says (section 3.8). Within the square-foot method's
   stated accuracy (10 %) the allocation is pro rata on RCNLD to T = B + CC (the closing
   costs are an indirect cost of the acquisition, allocated pro rata, ATG C.11); outside it
   the allocation is withheld (`missing: ["allocation"]`), the implied factor is reported as
   a diagnostic, and the engineer corrects the inputs the diagnosis points at.

The uniform factor is no longer an allocation method: `allocation.factor` is the RCNLD ->
basis factor (near 1 by construction), and the old `T / ΣRCN` appears only as
`reconciliation.implied_uniform_factor`.

## 3. The model

### 3.1 Inputs

From the appraisal's extraction fields (`basis.domain.measurements` already reads the GLA,
stories, basement, garage, site, year built, state and effective date; `structure.py` reads
the rest through the same `FieldRow` protocol):

| Input | Fields, in order of preference |
|---|---|
| living area | `improvements.gla_sqft`, `cost_approach.dwelling_sqft` |
| stories | `improvements.stories` |
| basement area, finish | `improvements.basement_area_sqft` / `cost_approach.basement_sqft`; `improvements.basement_finish_percent` |
| garage | `cost_approach.garage_sqft`, `improvements.garage_cars` |
| quality | `improvements.quality` (UAD Q1-Q6), else `cost_approach.quality_rating_from_cost_service` (Marshall & Swift words) |
| cost new | `cost_approach.total_cost_new`; the parts `dwelling_cost` (else `dwelling_sqft` x `dwelling_cost_per_sqft`), `basement_cost`, `garage_cost`, `mechanicals_misc` |
| cost data date | `cost_approach.effective_date_of_cost_data` (a month) |
| depreciation | `cost_approach.depreciation_physical` (else `cost_approach.depreciation`), `improvements.effective_age_years`, `cost_approach.remaining_economic_life` |
| site improvements, site value | `cost_approach.site_improvements_value`, `cost_approach.site_value` (the reconciliation's independent check) |
| rooms | `improvements.bedrooms`, `improvements.total_rooms` (the quantity rules of 3.6) |
| utilities | `site.utilities_water`, `utilities_sewer`, `utilities_gas`, `utilities_electric` (a public service is a lateral) |

The intake gives the cost year (placed-in-service year) and state as it does for every cost
row (EVAL_REPORT 37).

### 3.2 Class and type

Class of construction from the UAD quality rating, else the cost service's words, else
`average` with `needs_review`:

| UAD quality | Cost service | RSMeans class | Why |
|---|---|---|---|
| Q1, Q2 | Excellent, Very Good | luxury | "Unique residence built from an architect's plan ... extraordinary materials and workmanship" (RSMeans, every Luxury page) matches Q1 (unique, architect-designed) and Q2 (custom, high quality) |
| Q3 | Good | custom | "A distinct residence from designer's plans ... materials and workmanship are above average" matches Q3 ("above standard", "individual or readily available designer plans") |
| Q4 | Average | average | "Simple design from standard plans ... materials and workmanship are average" matches Q4 (stock plans, standard quality) |
| Q5, Q6 | Fair, Low | economy | "Mass produced from stock plans ... materials and workmanship are sufficient to meet codes" |

Building type from the stories: 1 -> "1 story", 1.5 -> "1-1/2 story", 2 -> "2 story",
2.5 -> "2-1/2 story", 3 or more -> "3 story"; a missing count is "1 story" with
`needs_review`. Bi-level and tri-level models are not transcribed (a split-level reads as
the nearest story count, recorded as an assumption).

### 3.3 The ten components, as transcribed

Dollars per square foot of living area, material plus installation, from the model pages
(each page was read as an image; the ten rows sum to the page's bottom line, which a unit
test pins). The page's "% of Total" column is the same split rounded to 0.1 %.

| Model (PDF page, printed) | Site | Found. | Framing | Ext. walls | Roofing | Interiors | Spec. | Mech. | Elec. | Overhead | Total $/SF (living area) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Economy 1 story (39, 25) | 2.31 | 15.31 | 14.02 | 16.39 | 5.02 | 26.02 | 4.89 | 8.68 | 3.24 | 14.37 | 110.25 (1200 SF) |
| Economy 1-1/2 story (41, 27) | 1.73 | 10.34 | 17.67 | 22.76 | 3.64 | 28.67 | 3.66 | 7.39 | 3.01 | 14.83 | 113.70 (1600) |
| Economy 2 story (43, 29) | 1.39 | 8.28 | 15.86 | 22.48 | 2.52 | 28.27 | 2.94 | 6.60 | 2.87 | 13.69 | 104.90 (2000) |
| Average 1 story (53, 39) | 1.78 | 14.05 | 17.64 | 19.62 | 5.16 | 27.60 | 6.57 | 7.93 | 4.26 | 17.79 | 122.40 (1600) |
| Average 1-1/2 story (55, 41) | 1.58 | 10.20 | 19.24 | 21.60 | 3.75 | 31.93 | 5.83 | 7.40 | 4.12 | 17.95 | 123.60 (1800) |
| Average 2 story (57, 43) | 1.43 | 8.51 | 18.19 | 22.53 | 2.59 | 31.94 | 5.27 | 7.03 | 4.02 | 17.24 | 118.75 (2000) |
| Average 2-1/2 story (59, 45) | 0.89 | 6.06 | 18.01 | 19.32 | 2.29 | 31.19 | 3.27 | 5.71 | 3.64 | 15.37 | 105.75 (3200) |
| Average 3 story (61, 47) | 0.96 | 5.66 | 17.81 | 21.49 | 1.72 | 32.39 | 3.51 | 5.86 | 3.69 | 15.81 | 108.90 (3000) |
| Custom 1 story (75, 61) | 1.33 | 15.31 | 16.88 | 16.37 | 9.47 | 30.11 | 10.51 | 11.27 | 5.33 | 23.32 | 139.90 (2400) |
| Custom 1-1/2 story (77, 63) | 1.14 | 10.85 | 18.07 | 16.07 | 6.86 | 33.61 | 8.97 | 9.96 | 5.20 | 22.17 | 132.90 (2800) |
| Custom 2 story (79, 65) | 1.14 | 9.24 | 17.11 | 17.42 | 4.75 | 34.51 | 8.97 | 10.19 | 5.20 | 21.72 | 130.25 (2800) |
| Custom 2-1/2 story (81, 67) | 0.99 | 7.66 | 18.32 | 18.05 | 4.22 | 36.62 | 7.85 | 9.40 | 5.12 | 21.67 | 129.90 (3200) |
| Custom 3 story (83, 69) | 1.07 | 7.20 | 18.11 | 20.10 | 3.16 | 37.95 | 8.40 | 9.78 | 5.16 | 22.22 | 133.15 (3000) |
| Luxury 1 story (93, 79) | 1.23 | 16.96 | 17.69 | 17.08 | 10.24 | 29.87 | 11.88 | 11.34 | 6.73 | 29.53 | 152.55 (2800) |
| Luxury 1-1/2 story (95, 81) | 1.23 | 12.55 | 19.79 | 18.30 | 7.42 | 34.94 | 11.88 | 11.34 | 6.73 | 29.82 | 154.00 (2800) |
| Luxury 2 story (97, 83) | 1.07 | 10.20 | 18.50 | 18.55 | 5.14 | 34.82 | 10.37 | 10.44 | 6.65 | 27.76 | 143.50 (3200) |
| Luxury 2-1/2 story (99, 85) | 1.15 | 9.07 | 20.43 | 21.56 | 4.57 | 39.34 | 11.07 | 10.88 | 6.69 | 29.94 | 154.70 (3000) |
| Luxury 3 story (101, 87) | 1.15 | 8.22 | 20.00 | 22.99 | 3.41 | 39.60 | 11.07 | 10.88 | 6.69 | 29.74 | 153.75 (3000) |

Overhead is spread over the nine other components in proportion to their dollars (ATG
C.11: project-wide indirect costs pro rata), so each component's share of the building RCN
is `component / (total - overhead)`. On the Custom 2 story model the shares after spreading
are: site work 1.05 %, foundation 8.51 %, framing 15.76 %, exterior walls 16.05 %, roofing
4.38 %, interiors 31.79 %, specialties 8.26 %, mechanical 9.39 %, electrical 4.79 %.

The square-foot model (the default source) reads the base cost per square foot of living
area from the facing page's matrix ("Base cost per square foot of living area", the Wood
Siding - Wood Frame row, eleven living areas per model, all 18 models transcribed from the
page images into `BASE_COST_BY_AREA`; the cell at the model's representative area equals
the model page's bottom line, pinned by a test), interpolated linearly between columns and
clamped at the ends (an assumption when clamped). To it are added the facing page's finished
and unfinished basement additions (per square foot of living area, at the model's
representative area) prorated by the basement's finish percentage and by the basement's
share of the ground-floor area, and the facing page's attached-garage modification per car
(Economy "One Car Attached Garage" $14,543; Average $15,712; Custom "Two Car Attached
Garage" $30,463, Luxury $35,170). The base costs are on a 2020 national basis and take the
year and location factors of every other RSMeans row (EVAL_REPORT 35, 37; since 45 the
Historical Cost Index ratio and the Residential Location Factor, COST_INDICES.md). The exterior wall
is taken as wood siding (the other rows are not transcribed) and the row is marked
`needs_review` so the engineer confirms the class, type and wall.

### 3.4 Every asset mapped to a system and a component

`LABEL_SYSTEMS` maps every component label of the vision vocabulary (78 labels) and every
structure label (3.5) to one of the design partner's twelve building systems (the
`by_system` page) and to the RSMeans component it is part of, or to none when the model
does not include it (fireplaces, appliances, furniture, window treatments, site
improvements, bathroom accessories, mirrors, garbage disposals, `other`): those are
additive, never carved. Rules of thumb used in the table: interior doors, flooring, stairs,
ceilings, wall finishes and trim are Interiors; windows, exterior and garage doors and siding
are Exterior Walls; roof and gutters are Roofing; kitchen cabinets, countertops, kitchen
sinks and water heaters are Specialties; bathroom fixtures, vanities, HVAC units, vents,
condensers and thermostats are Mechanical; outlets, light fixtures, fans, panels, doorbells,
detectors, security systems and outdoor lighting are Electrical; a sprinkler system is
Mechanical (plumbing). `by_system` is therefore always filled; an asset with no label (a
manual addition before this change) counts as Miscellaneous Building Components and the
reason names it.

### 3.5 The concealed rows

One asset per residual, with a label the rules table classifies, the system and component
on the takeoff inputs, and the quantity the model is per (square feet of living area for the
building components):

| Label | Category (the engineer's row) | Residual of | Bucket (ATG Exhibit A) |
|---|---|---|---|
| `foundation` | Foundations | Site Work + Foundation | building, p.299 |
| `structural_framing` | Structural Framing (exterior wall, roof and floor framing; the model's partition studs stay here) | Framing | building, p.321, p.317 |
| `exterior_wall_assembly` | Exterior Façade / Building Skin (sheathing, housewrap, insulation, trim) | Exterior Walls | building, p.321 |
| `roofing_assembly` | Roofing Systems (felt, flashings, drip edge) | Roofing | building, p.317 |
| `interior_partition` | Interior Framing / Partitions (gypsum board walls and ceilings, taped and finished) | Interiors, 66.2 % of the residual | building, p.321 |
| `painting` | Painting (primer and two coats) | Interiors, 33.8 % of the residual | building, p.320 |
| `plumbing_rough_in` | Plumbing (supply, drain, waste and vent piping) | Mechanical, 46.0 % of the residual | building, p.315 |
| `hvac_distribution` | HVAC (heating plant, ductwork or piping) | Mechanical, 54.0 % of the residual | building, p.310 |
| `electrical_wiring` | Electrical (service, branch wiring) | Electrical | building, p.301, p.303 |

The Interiors residual is split by the RSMeans drywall wall system (Residential 2020,
Assemblies 6-04 "Drywall & Thincoat Wall Systems", printed p.214, PDF p.228): per square foot
of wall, gypsum wallboard $0.84 + taped and finished joints $0.54 + corners $0.09 = $1.47
against painting, primer and two coats, $0.75, so painting is 0.75 / 2.22 = 33.8 % of the
drywall-and-paint assembly (the trim and base moldings on that page are itemized rows:
`baseboard`, `crown_molding`). The Mechanical residual is split by the two systems the
model's Mechanical component names: the three-fixture bathroom with vanity (Assemblies
8-12, printed p.250, PDF p.264: $5,041.55 per bathroom) against the gas-fired hot-air
heating system, one zone, 1,200 SF building (Assemblies 8-60, printed p.268, PDF p.282:
$5,929.90), so plumbing is 5,041.55 / 10,971.45 = 46.0 %. Specialties has no concealed
content (cabinets, a sink and a water heater are all visible), so its residual is reported in
the reconciliation as an unassigned remainder and never written as an asset: assigning an
unexplained remainder to a 5-year row would be the skew the guide warns of (C.4(3)).

### 3.6 The small systems: quantity rules

| Label | Category | Quantity | Rule | Carved from | Bucket |
|---|---|---|---|---|---|
| `fire_alarm_system` | Fire Protection & Alarm | bedrooms + stories (+1 with a basement), less the `smoke_detector` assets the photographs found | IRC R314.3 (2021): a smoke alarm in each sleeping room, outside each separate sleeping area, and on each additional story including basements; the "outside each sleeping area" alarm is counted once per story | Electrical | building, p.305 |
| `data_cabling` | Communication / Data | total rooms (the URAR count excludes bathrooms) | TIA-570 residential cabling: one outlet per room at the minimum grade; recorded as an assumption (the standard is not in the corpus) | Electrical ("communications cabling") | 5-year, p.300 |
| `access_control` | Access Control | exterior doors (the `exterior_door` assets, else 2) | one keyed lockset with deadbolt per exterior door; the count of doors is an assumption when no photograph shows one | Exterior Walls ("doors, frame and hardware") | building, contested (p.300, p.318) |
| `site_utilities` | Site Utilities | public services listed on the appraisal (water, sewer, gas; electric is the utility's drop) x 50 LF | the lateral length is an assumption (no site plan); `needs_review` | none (the model's site work is the foundation trench) | building, p.319 |

Each is priced from the cost table's row for its label (`COST_TABLE_PATH`, the engineer's
licensed table; the eval table carries rows cited to RSMeans Residential 2020: smoke
detector 28 46 11.27 5200, printed p.624; deadbolt and keyed lockset 08 71 20.40/20.41,
printed p.474; utility trenching water, sewer and gas service per LF, printed p.107; and the
Building Construction Costs 2020 telephone receptacle with 20 ft of wire, Division 26). A
missing row leaves the asset unpriced with the reason, like any other label.

### 3.7 Depreciation

`rate = physical depreciation / total cost new` when the appraisal prints both (the
appraiser's conclusion), else `effective age / (effective age + remaining economic life)`
(the appraiser's inputs), else none (`missing: ["depreciation"]`; the reconciliation then
compares RCN to the basis and says so). The age-life ratio is always computed beside the
dollar rate as the `depreciation_age_life` check (they agree on the client appraisal to the
tenth of a percent). The rate applies to every row, concealed or itemized, personal property
or building: `rcnld = rcn x (1 - rate)` to the cent, the residual cents by the
largest-remainder rule so the rows foot to the total. The `depreciation_uniform` check is a
warning on every result: the guide expects the adjustment to differ by class (A(6)); the
engineer's report applies one write-down too.

### 3.8 Reconciliation and allocation

```
RCN_total   = Σ itemized RCN + Σ concealed RCN            (land rows excluded)
RCNLD_total = RCN_total x (1 - rate)
B           = purchase price - land                        (the worksheet)
gap         = B - RCNLD_total ;  gap_share = gap / B
```

`reconciliation.status` is `reconciled` when |gap_share| <= 10 % (RSMeans Residential 2020,
printed p.15: the square-foot method's expected variation), else `unreconciled`; without a
building RCN or a depreciation rate it is `not_checked`. The document carries: the two
totals and the gap; `by_system` (per RSMeans component: top-down, itemized, concealed,
clamped); `unassigned_remainders` (Specialties, and any component whose residual was
clamped); `outside_model` (the dollars of assets the model does not include, by system);
`unpriced_assets`; the appraisal's own check (depreciated cost of improvements plus site
improvements against the price less the appraisal's site value, when printed); and the
guide's six causes (D(6)), each with what the data says about it (the land checks of the
worksheet; the unpriced count and the clamped components; the cost source of the building
RCN; the depreciation source; the overhead spread).

When reconciled, `allocation.rows[i] = rcnld_i x T / RCNLD_total` (T = B + CC; the
largest-remainder rule as before; `factor` is now that ratio, within 1 +/- 10 % by
construction), the bucket block, the pro-rata B/CC block and `by_system` as before. When
unreconciled, `allocation` is null and `missing` says `allocation`: the schedule (RCN and
RCNLD per row and bucket) is complete and the engineer fixes what the diagnosis names
(the land figure, a missing quantity, an unpriced row) rather than letting a factor hide
it. `implied_uniform_factor` (T / ΣRCN) is reported in the reconciliation as the number the
old method would have used.

## 7. The sweep: which way is better (EVAL_REPORT 44.3)

Bryan's standing instruction (every component tested and compared, accuracy before cost)
makes step 2 a sweep, not one design. `evals/structure_sweep.py` runs every variant through
the replay and the e2e scorer on both stored exports and ranks the sourced, reconciling ones
by the mean absolute bucket-share error against the engineer's shares, then the row-share
distance, then the gap; the photo-visible rows' scores are the regression check and must
not move. The axes, each a parameter of the pure functions and of `StructureInputs`:

| Axis | Variants | Source |
|---|---|---|
| RCN source (`rcn_source`) | `square_foot` (RSMeans model by class, type and size), `appraisal_total` (the printed total), `appraisal_parts` (the sum of the parts) | RSMeans Residential 2020 model and facing pages; the URAR cost approach |
| Split (`split`) | `rsmeans` (the model page's components, Overhead pro rata), `nahb` (the NAHB 2024 stage shares, the model's proportions within a stage), `marshall_swift` | RSMeans model pages; NAHB Cost of Constructing a Home 2024 (Eye on Housing, 2025-01: foundations 10.5, framing 16.6, exterior finishes 13.4, major systems 19.2, interior finishes 24.1 %, site work 7.6 and other 2.1 spread, final steps 6.5 outside the building); Marshall & Swift is licensed and not in the corpus, so that variant is reported as not sourced and never defaulted |
| Carve-out (`carve`) | the hybrid (itemized rows subtracted from each component) or top-down only (components left whole) | ATG C.4(3) |
| Depreciation (`depreciation_method`) | `appraisal_physical`, `age_life`, `none` | the URAR's depreciation and its inputs |

Result on the two live exports (44.3): the square-foot model with the RSMeans split, the
carve-out and the appraiser's depreciation has the smallest bucket-share error on both
(2.06 and 3.50 points against 2.43 and 3.79 for the appraisal's figure), the two depreciation
methods tie (the same rate on this appraisal), the NAHB split is a few tenths behind, and
neither top-down-only nor undepreciated variants reconcile. The production default is the
pick (`DEFAULT_RCN_SOURCE`, `STRUCTURE_RCN_SOURCE=square_foot`); the appraisal's figure is
the runner-up and the setting's other value, and its own depreciated cost stays on every
result as the independent check. Cost per variant is zero (deterministic); runtime 7-11 ms.

## 4. Sources

| Number or rule | Source |
|---|---|
| Step-up rejected; RCNLD + land FMV approximates the price; the six causes | ATG ch. 3 A(6) p.27, C.2(3) p.30, D(3)-(6) pp.32-33 |
| Indirect costs pro rata | ATG ch. 4 C.11 p.40; ch. 3 C.1(3)(i) p.29 |
| Mixing unit costs and gross square-foot costs skews toward § 1245 | ATG ch. 3 C.4(3) p.31 |
| The ten components and their dollars per SF, 18 models | RSMeans Residential Costs 2020, square-foot section, the pages in 3.3 (read as images) |
| Base cost per SF of living area by size (wood siding), 18 models | the facing pages (printed pp.24-86, even), read as images |
| NAHB stage shares | NAHB, Cost of Constructing a Home in 2024, Eye on Housing (eyeonhousing.org, 2025-01-23) |
| +/- 10 % accuracy of the square-foot method | RSMeans Residential Costs 2020, "Square Foot Section Instructions", printed p.15 (PDF p.29) |
| Basement additions, garage modifications, representative living areas | the facing pages (printed pp.24-86, even) |
| Drywall 1.47 vs paint 0.75 per SF of wall | RSMeans Residential 2020 Assemblies 6-04, printed p.214 |
| Bathroom system 5,041.55; heating system 5,929.90 | Assemblies 8-12 printed p.250; 8-60 printed p.268 |
| Class from UAD quality | Fannie Mae UAD quality ratings Q1-Q6 against the RSMeans class descriptions on each model page |
| Smoke alarm locations | IRC R314.3 (2021) |
| One telecommunications outlet per room | TIA-570 residential (assumption; not in the corpus) |
| Site utility laterals | ATG Exhibit A p.319 (what the system is); RSMeans Residential 2020 printed p.107 (per LF); 50 LF is an assumption |
| Age-life depreciation | The Appraisal of Real Estate (age-life method); the URAR's effective age and remaining economic life fields |
| Buckets of the concealed rows | ATG Exhibit A pp.299, 300, 301, 303, 305, 310, 315, 317, 318, 319, 320, 321 (verbatim excerpts on the rules rows, RULES_RECITATION.md) |

## 5. Assumptions as named constants (`basis/domain/structure.py`)

| Constant | Value | Why |
|---|---|---|
| `RECONCILIATION_TOLERANCE` | 0.10 | RSMeans printed p.15 |
| `PAINTING_SHARE_OF_INTERIORS_RESIDUAL` | 0.75 / 2.22 | Assemblies 6-04 |
| `PLUMBING_SHARE_OF_MECHANICAL_RESIDUAL` | 5,041.55 / 10,971.45 | Assemblies 8-12 and 8-60 |
| `LATERAL_LENGTH_FT` | 50 | no site plan; `needs_review` |
| `EXTERIOR_DOORS_DEFAULT` | 2 | front and rear; used only when no photograph shows an exterior door |
| `DEFAULT_CLASS` | average | the median class; `needs_review` when used |
| `DEFAULT_RCN_SOURCE` | square_foot | the sweep's pick (section 7); `STRUCTURE_RCN_SOURCE` overrides |
| `DEFAULT_SPLIT`, `DEFAULT_DEPRECIATION` | rsmeans, appraisal_physical | the sweep's pick |
| `DEFAULT_TYPE` | 1 story | `needs_review` when used |
| `GARAGE_CARS_IN_MODIFICATION` | 1 for economy and average, 2 for custom and luxury | the facing pages' garage lines |
| `COST_APPROACH_PARTS_TOLERANCE` | 0.01 | the printed total against the sum of its parts (a warning, not a refusal; the client appraisal's parts differ by 1.4 %) |

## 6. What this does not do (open)

* Per-class depreciation (A(6)): one rate for every row; a carpet gets the building's
  rate. The per-class refinement needs the ages of finishes and equipment, which an
  inspection can give and the appraisal does not.
* The split-level models (bi-level, tri-level) and the exterior-wall rows other than wood
  siding of the matrix are not transcribed; the basement and garage additions are at the
  model's representative size; the square-foot rows are marked for review.
* The Marshall & Swift segregated-cost split needs the licensed handbook; the sweep reports
  it as not sourced.
* Functional and external obsolescence are read from the appraisal when printed but not
  applied: the client appraisal prints none; the physical rate is the one used.
* The Interiors and Mechanical splits are national assemblies, not the house's own
  quantities (wall area, number of bathrooms); a takeoff from a floor plan would replace
  them.
* The engineer's Access Control row is 5-year; the rules row follows the guide (building,
  contested) and a firm convention can override it.
* The API's asset list (`GET /v1/studies/{id}/assets`) shows a concealed asset with no
  component and no label; the export and the result carry its label in the takeoff inputs.
