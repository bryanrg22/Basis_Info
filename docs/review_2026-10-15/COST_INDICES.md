# Cost indices: the year and location factors every priced asset rests on (2026-10-30)

Data-free. Question: every dollar the pipeline prices is `unit cost x quantity x year factor x
location factor` (EVAL_REPORT sections 27, 35, 37, 44). The unit costs are RSMeans Residential
2020 lines with the book's line number and page on every row, and the structure model is the
same book's square-foot models (section 44). The two factors were not: `YEAR_ADJUSTMENT_FACTORS`
(2020-2026, e.g. 2024 = 1.15) and `REGIONAL_COST_FACTORS` (one number per state, e.g. CA = 1.15)
were hand-typed tables inherited from the legacy `agentic/agents/cost_agent.py` with no source,
so a reviewer following the IRS guide's rule that cost sources be "clearly referenced, including
identification of the specific volume, page, and item number" (ATG ch. 3 C.2(2)) could trace the
unit cost and not the 32 % the two factors added on top of it. What does RSMeans publish for
time and location, how do estimators apply it, what does Marshall & Swift do instead, and what
rule covers a cost year the book has no index for?

Code: `basis/domain/cost_indices.py` (the lookups), `basis/data/cost_indices/rsmeans_2020.json`
(the transcribed tables, sha256 in `manifest.json`), `basis/domain/costs.py` (the estimate),
`basis/domain/measurements.py` (`resolve_cost_index`), `basis/domain/structure.py` (the
square-foot model and the appraisal's cost-approach trending), the review reason written by
`basis/activities/process_assets.py`, the offline replay `evals/reprice_export.py`. Page numbers
are the books' printed pages unless marked "PDF" (Residential: printed = PDF - 14; Building:
printed = PDF - 16).

## 1. What RSMeans publishes

**The books' cost basis.** "All costs represent U.S. national averages and are given in U.S.
dollars. The City Cost Index (CCI) with RSMeans data can be used to adjust costs to a particular
location" (Residential 2020 p.11). The square-foot section says the same and names the method:
"The data contained in this data set are a national average. Construction costs are different
throughout the country. To allow for this difference, a location factor based upon the first
three digits of the ZIP code" is applied (p.29). The 2020 books' costs are as of January 1,
2020: the Historical Cost Index table (below) sets "Jan 2020 = 239.1 (est.)" as the current
index and leaves the quarterly 2020 lines blank "to write in the actual index figures as they
become available through the quarterly RSMeans Construction Cost Indexes" (Building p.778).

**The Historical Cost Index (time).** Building Construction Costs 2020, p.778 (PDF 794), table
"Historical Cost Indexes": "the RSMeans historical cost index based on Jan. 1, 1993 = 100 as well
as the computed value of an index based on Jan. 1, 2020 costs." One value per year, July 1, from
1970 (28.7) to 2019 (232.2), and Jan 1, 2020 estimated at 239.1 (= 100.0 on the 2020 base). Its
use: "The 'Historical Cost Index' can be used to convert national average building costs at a
particular time to the approximate building costs for some other time", by

    Index for Year A / Index for Year B x Cost in Year B = Cost in Year A

(the page's worked example: a $900,000 building in 2020 cost 28.7 / 239.1 = .120 x $900,000 =
$108,000 in 1970). The Residential book has no Historical Cost Index table of its own; its
reference section points at the same index ("Historical Cost Indexes: These indexes provide you
with data to adjust construction costs over time", p.7 of either book), and both books' costs
are the same January 1, 2020 national average, so the Building book's table is the one that
moves a Residential 2020 cost in time.

**The City Cost Index (location, by trade).** Building 2020 pp.779-811 (PDF 795-827), "City Cost
Indexes": "a City Cost Index number is a percentage ratio of a specific city's cost to the
national average cost of the same item at a stated time period"; "The 30 City Average Index is
the average of 30 major U.S. cities and serves as a national average"; "Index figures for both
material and installation are based on the 30 major city average of 100 and represent the cost
relationship as of July 1, 2019" (p.779). Material, installation and total per MasterFormat
division per city, with a weighted average row. Its use: `City Index / 100 = % of National
Average`, and between cities `Index for City A / Index for City B x Cost in City B = Cost in
City A`.

**The Location Factors (location, one number per city).** Residential 2020 pp.718-722 (PDF
732-736), "Location Factors - Residential": "Costs shown in Residential Costs with RSMeans data
are based on national averages for materials and installation. To adjust these costs to a
specific location, simply multiply the base cost by the factor for that city. The data is
arranged alphabetically by state and postal zip code numbers. For a city not listed, use the
factor for a nearby city with similar economic characteristics" (p.718). One column,
"Residential", per three-digit ZIP prefix group: 651 U.S. rows over 51 states and D.C. (908
prefixes), Guam and Puerto Rico, then the Canadian factors in Canadian dollars. The relation to
the CCI is stated by the book: "All figures in the two tables are derived from the same research.
The last row of data in the CCI, the weighted average, is the same as the numbers reported for
each location in the location factor table" (p.287); and which to use: "Use the Location Factors
to adjust estimate totals if the project covers multiple trades. Use the City Cost Indexes (CCI)
for single trade projects or projects where a more detailed analysis is required" (p.287; the
same rule p.239).

## 2. How estimators apply them

The books' own sample estimate (Residential p.239, Building p.23) prices every line at the
national average, sums, adds the general contractor's markups, and applies one location factor
to the total: "The Location Factor with RSMeans data is added at the bottom of the estimate to
adjust the cost of the work to a specific location." Time is the other dimension: a cost from a
book of year B is moved to year A by the Historical Cost Index ratio above; the order does not
matter because both are multipliers on the same national-average dollars. A cost segregation
study prices a whole house (every trade), so the Location Factor, not a single trade's CCI
line, is the book's choice for it; the CCI's trade split would matter for a single-trade
estimate or when the question is which trades make a city expensive.

The product's pipeline is the same arithmetic per row rather than once on the total, which
gives the same total (the factors are multipliers) and keeps every asset's dollars traceable on
its own row, as the guide's "specific volume, page, and item number" asks of each item.

## 3. What Marshall & Swift does instead

The Marshall Valuation Service and the Residential Cost Handbook also publish national-average
costs and two multipliers, but refreshed rather than tabulated: "Current Cost Multipliers are
furnished monthly to keep costs up-to-date in each of three districts (see map), and Local
Multipliers convert the costs to specific localities" (California State Board of Equalization
appraisal training material on the Marshall & Swift cost approach); the software's help says the
same of the base: "the costs in the Marshall Valuation Service are National Average costs. You
must apply the Local Multiplier to adjust the cost to the local area", with the local multiplier
selected by ZIP code from the service's Section 99 (Swift Estimator field help). The user of a
Marshall & Swift book is expected to open the current month's multiplier page for the valuation
date; the service does not ask the user to extrapolate a stale multiplier, it ships a new one.
Both services therefore agree on the model the product now follows: national-average unit
costs, a published time multiplier for the valuation date, a published location multiplier by
ZIP, each cited to its page.

## 4. What the IRS guide asks, and the rule for a year the book has no index for

ATG ch. 3 C.2(2) (PDF pp.29-30): "When estimates are required, they are based on costing data,
either from contractors or from reliable published sources (e.g., R. S. Means or Marshall
Valuation Service). The sources of estimating data are clearly referenced, including
identification of the specific volume, page, and item number. Further, consistent estimating
techniques and unit cost data sources are used for all of the items that comprise the actual
cost." The guide asks for the source and the page, not for a particular index; it does not
address a missing year.

The corpus holds the 2020 books, so the last published index available to the product is
January 1, 2020, while the studies run so far are placed in service in 2024. Three rules were
weighed:

* **Trend by a rate** (the index's own growth, 3.0 % a year over 2015-2019, or an outside
  series). A number no book prints; neither RSMeans (which leaves blank lines for the actual
  quarterly figures) nor Marshall & Swift (monthly multipliers) extrapolates, and the ATG's
  "specific volume, page, and item number" has nothing to point at. The 2020-2024 period is also
  the one where a 2015-2019 trend would have been most wrong. Rejected.
* **Refuse** a year after the last index (`UnknownCostYear`). Every current study would price
  nothing. Rejected.
* **The latest published index, flagged** (adopted). A cost year after the last index takes
  that index (factor 1.0000 on the 2020 books), the assumption is written on every priced row
  (`takeoff_inputs.cost_year_assumption`, the `cost_source` text) and the asset is flagged
  `cost:YEAR_INDEX_EXTRAPOLATED` (BASIS-CHECK-016) so the engineer sees it at the assets gate,
  until the firm loads the year's published index: `COST_INDEX_SUPPLEMENT_PATH` names a JSON
  file of later Historical Cost Index values, each with its own source (the year's book and
  page, or the quarterly RSMeans Construction Cost Index), which lifts the assumption and the
  flag. A year before the table (before 1970) is refused.

The same rule prices the structure model's square-foot RCN (a `cost_year_index` warning on the
building RCN and the assumption on the concealed rows) and the appraisal's cost approach: the
appraiser's cost new is moved from its "effective date of cost data" to the cost year by the
same Historical Cost Index ratio when both years have a published index, else taken as printed
with the reason recorded (as before, with the reason now naming the missing index).

## 5. The rules adopted, in one place

* **Year factor** = Historical Cost Index(cost year) / 239.1 (Jan. 1, 2020, the books' cost
  basis), four decimals: 2020 = 1.0000, 2019 = 0.9711, 2018 = 0.9322, 2010 = 0.7675, 1970 =
  0.1200. A year before 1970 raises `UnknownCostYear`; a year after the latest loaded index is
  extrapolated as in section 4.
* **Location factor**: the Residential Location Factor of the study's city, found by the first
  three digits of the ZIP (the intake's ZIP, else the appraisal's `subject.zip`); when no ZIP is
  given or its prefix is not in the table, the arithmetic mean of the state's listed cities
  (three decimals: CA 1.001 over 37 cities, NY 0.976 over 32, IL 0.959 over 19), recorded as such
  on the row. The book's rule for an unlisted city ("a nearby city with similar economic
  characteristics") needs a judgment the product does not make; the state mean is the documented
  stand-in, and the row says which cities it averages. The order of candidates is the intake's
  ZIP, the intake's state, the appraisal's ZIP, the appraisal's state, the `COST_LOCATION_FACTOR`
  setting; a candidate that was given but could not be used is named after a semicolon
  (section 3.9 of TAKEOFF_RESEARCH.md, unchanged in spirit).
* **Every cost row cites both**: `(base 2020, x0.9322 for 2018 = HCI 222.9 (July 2018) / 239.1
  (Jan 2020, est.), RSMeans Building Construction Costs 2020 p.778, from placed in service
  2018-05-01 (intake); x0.99 location from zip 90001 (intake): 900-902 Los Angeles 0.99,
  RSMeans Residential 2020 Location Factors - Residential p.718)`.
* **The cost table's base year** must be 2020 (as before): the indices are relative to the
  2020 books' costs.

## 6. The data file and how it was transcribed

`basis/data/cost_indices/rsmeans_2020.json` (schema 1, `name: rsmeans_2020`) holds the two
tables with their book, table name and printed pages; `manifest.json` records its sha256 and the
loader refuses a file whose hash is not the released one (a released file is never edited: the
next one is added, like the prompts). The file is public reference data: published index
numbers and the book's place names, no client data (the unit tests check that no field beyond
the schema's and no five-digit number is in it).

The page text of both PDFs is an OCR layer that dropped the numeric columns, so the tables were
transcribed from the page images: each page rendered at 200 dpi, each half-page OCRed twice
(the city column as text, the factor column upscaled three times with a digits-only whitelist)
and joined by line, then every row compared against the image by eye in 36 zoomed strips. The
OCR draft was right on about 85 % of rows; 68 values it could not read and nine it read as a
plausible but wrong number (Davenport .39 for .99, Butte .37 for .97, Norristown .30 for .90,
Watertown SD and Huntsville TX .26 for .96, Fredericksburg .90 for .95, Williston, Mankato,
Chillicothe) were corrected from the image, and nine rows the OCR dropped (San Mateo, Twin
Falls, Waycross, Long Island City, Williamsport, Richland, Kenosha, Beloit, Portage) were added
from it. Checks on the result: 651 rows, the per-state counts equal the page's (CA 37, TX 37, PA
36, NY 32, ...), every ZIP prefix names one city, and the Historical Cost Index's two printed
columns (1993 base and 2020 base) agree with each other within the book's rounding for all 51
years. Twenty location rows and thirteen index values read directly from the images are pinned
in `tests/unit/domain/test_cost_indices.py`.

Not transcribed: Guam (969, 1.34) and Puerto Rico (009, .68), which no state code in the
intake selects, and the Canadian factors (Canadian dollars). The City Cost Index's trade split
(pp.779-811 of the Building book) is not transcribed either: the Location Factor is the CCI's
weighted-average row (p.287), which is what a whole-house estimate uses.

## 7. What changed in the product

* `basis/domain/costs.py`: `YEAR_ADJUSTMENT_FACTORS` and `REGIONAL_COST_FACTORS` are gone;
  `year_factor`, `location_factor_for_state` and `estimate_cost` keep their signatures and read
  the indices; `estimate_cost` takes the study's resolved `year` (so the supplement reaches it)
  and returns `year_assumption`; `UnknownCostYear` now means before 1970.
* `resolve_cost_index` takes `zip_code` and `supplement`; `CostIndex` carries the resolved
  `year` (factor, index values, assumption) and `location` (the row or the state mean).
* `process_assets` passes the intake's ZIP, writes `cost_year_assumption` into the takeoff
  inputs and the `cost:YEAR_INDEX_EXTRAPOLATED` review reason on every priced row (itemized,
  square-foot building rows and small systems) when the year is extrapolated; the export's
  `summary.takeoff` carries `cost_year_assumption`.
* `structure.py`: the square-foot model and the cost-approach trending use the Historical Cost
  Index; `year_factor_formula` names the index values.
* Settings: `COST_YEAR` accepts 1970-2100 (was 2020-2026); `COST_INDEX_SUPPLEMENT_PATH`.
* Error catalogue: BASIS-CHECK-016 `CHECK_COST_YEAR_INDEX_EXTRAPOLATED`.
* `evals/reprice_export.py`: prices a stored export again with the indices (EVAL_REPORT 45).

## 8. What this does not do (follow-ups)

* **Load the 2021-2026 indices.** The one number that makes a 2024 study's dollars right is the
  2024 Historical Cost Index, which is in the 2024 book (or the quarterly RSMeans Construction
  Cost Index), not in the corpus. The supplement file is the hook; until the firm fills it, every
  2024 row is flagged and the reconciliation reports the gap (EVAL_REPORT 45.4).
* **The ZIP of the live studies.** The client study's intake gave the state and no ZIP, so the
  state mean applied; the intake form asks for the ZIP (the field exists since section 37) and
  the city factor applies when it is given.
* **A nearby-city rule** for a ZIP prefix the table lacks (the book's instruction) and the CCI's
  material / installation split are not implemented; both need a judgment or a trade split the
  takeoff does not carry.
* **The index's own precision**: the book's factors are two decimals and its indices one;
  the product keeps four decimals on the ratio and three on a state mean, which is more
  precision than the inputs carry and is stated here so no one reads it as accuracy.

## Sources

* Residential Costs with RSMeans data, 2020 (Gordian, 60170): p.7 (reference section
  contents), p.11 ("How to Use the Cost Data": national averages, the CCI), p.29 (square-foot
  section: the location factor by the first three digits of the ZIP), p.239 (sample estimate,
  location adjustment), p.287 (national average; Location Factors versus the CCI; the weighted
  average row), pp.718-722 ("Location Factors - Residential"). Corpus
  `RSMEANS_RSMEANS_RESIDENTIAL_2020.pdf` (PDF pages = printed + 14).
* Building Construction Costs with RSMeans data, 2020 (Gordian): p.778 ("Historical Cost
  Indexes": the table, the time adjustment formula and example), p.779 ("How to Use the City Cost
  Indexes": definition, the 30-city average, July 1, 2019 basis, the formulas), pp.780-811 (the
  CCI tables), pp.822-827 ("Location Factors - Commercial"). Corpus
  `RSMEANS_RSMEANS_BUILDING_2020.pdf` (PDF pages = printed + 16).
* IRS Cost Segregation Audit Techniques Guide (Publication 5653, 2024), chapter 3 C.2(2),
  PDF pp.29-30: the cost-source citation rule. Corpus `IRS_IRS_COST_SEG_ATG__2024.pdf`.
* Marshall & Swift: California State Board of Equalization, appraisal training course 2B class
  material "Marshall & Swift" (Current Cost Multipliers monthly by district; Local Multipliers
  by locality), https://www.boe.ca.gov/CountyPortal/Training/Uploads/Course%20002B/Class%20Materials/Marshall%20and%20Swift/Marshall%20&%20Swift.pdf ;
  CoreLogic Swift Estimator field help, "Cost Adjustments: Local Multiplier" (national-average
  costs; the local multiplier by ZIP from Section 99),
  https://www.swiftestimator.com/SE7Help/CE/help/FieldHelp/CostAdjustments_LocalMultiplier.htm .
  Both read 2026-10-30; the Marshall & Swift books themselves are not in the corpus.
