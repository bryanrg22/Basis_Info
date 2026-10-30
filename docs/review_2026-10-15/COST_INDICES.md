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
`basis/activities/process_assets.py`, the offline replay `evals/reprice_export.py`. Sections
9-12 (2026-10-30, later the same day): the cost index store (`basis/costs/`, migration
`0016_cost_indices`, `basis/db/repositories/cost_index.py`), the selection by study, the
acquisition of a value the store lacks and the refresh job. Page numbers are the books' printed
pages unless marked "PDF" (Residential: printed = PDF - 14; Building: printed = PDF - 16).

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
* **The latest loaded index, flagged** (adopted). A cost year nobody has loaded an index for
  takes the latest loaded index of an earlier year (factor 1.0000 while the store holds only
  the 2020 books), the assumption is written on every priced row
  (`takeoff_inputs.cost_year_assumption`, the `cost_source` text), the asset is flagged
  `cost:YEAR_INDEX_MISSING` (BASIS-COST-001) so the engineer sees it at the assets gate, and
  `summary.basis.cost_index` names the value to enter (section 10). The study never stops and
  nothing is invented. The value arrives through the store (section 11: the firm enters it
  with its citation, or the refresh job reads Gordian's free quarterly page), which lifts the
  assumption and the flag on the next run of the assets step. A year before the table (before
  1970) is refused.

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
  inputs and the `cost:YEAR_INDEX_MISSING` review reason on every priced row (itemized,
  square-foot building rows and small systems) when the year has no loaded index; the export's
  `summary.takeoff` carries `cost_year_assumption`.
* `structure.py`: the square-foot model and the cost-approach trending use the Historical Cost
  Index; `year_factor_formula` names the index values.
* Settings: `COST_YEAR` accepts 1970-2100 (was 2020-2026). (The `COST_INDEX_SUPPLEMENT_PATH`
  file of the first version was replaced by the store the same day, section 9; its JSON shape
  survives as the import and export format of `python -m basis.costs.refresh`.)
* Error catalogue: BASIS-COST-001 `COST_YEAR_INDEX_MISSING` (section 10).
* `evals/reprice_export.py`: prices a stored export again with the indices (EVAL_REPORT 45).

## 8. What this does not do (follow-ups)

* **Load the 2021-2026 indices.** Done the same day by the store (sections 9-12): the values
  are Gordian's own, read from its free quarterly pages, or entered by the firm with their
  citation; EVAL_REPORT 47 replays both live studies with the July 2024 value.
* **The ZIP of the live studies.** The client study's intake gave the state and no ZIP, so the
  state mean applied; the intake form asks for the ZIP (the field exists since section 37) and
  the city factor applies when it is given.
* **A nearby-city rule** for a ZIP prefix the table lacks (the book's instruction) and the CCI's
  material / installation split are not implemented; both need a judgment or a trade split the
  takeoff does not carry.
* **The index's own precision**: the book's factors are two decimals and its indices one;
  the product keeps four decimals on the ratio and three on a state mean, which is more
  precision than the inputs carry and is stated here so no one reads it as accuracy.

## 9. The store: every value the product has obtained, with its citation (2026-10-30)

Bryan's rule for the design: "if we already searched for 2024 and we've already used it, we
don't have to go back and get it again. We could just put it on or save it somewhere so that
we can check first there." The store is that place, and it is also the ledger a reviewer asks
for: which index value priced this study, where it came from, who obtained it and when.

**Table `cost_indices`** (migration `0016_cost_indices`; `basis/db/models.py`,
`basis/db/repositories/cost_index.py`). One row per obtained value, keyed by `(source,
series, year, region, version)`:

* `source`: where the value came from: `rsmeans_2020_book` (the released file of section 6),
  `rsmeans_public` (Gordian's free quarterly pages, section 11), `manual` (entered by a firm
  owner through the API or the CLI), `rsmeans_api` (the subscription, a stub);
* `series`: `historical` (the Historical Cost Index, Jan. 1, 1993 = 100, national, one value
  per year) or `city` (a location factor, the City Cost Index's weighted average over 100);
* `region`: `US` for a national value (not NULL: the key's unique constraint needs a value),
  a state code for a state value, `<state>:<ZIP prefixes>` as the book prints them for a city
  row (`CA:900-902`; the city's name in `detail`);
* `value` (NUMERIC(14, 4), as published), `as_of` (the date the value is for: July 1 for a
  year's Historical Cost Index, the quarter's first day for a quarterly figure, the book's
  cost basis for a location table), `citation` (publication, table and page, or the page's
  URL and the line as printed), `url`, `document_sha256` (the fetched document's hash),
  `detail` (the quarters read, the city name and page, an estimate flag);
* `derived`: NULL for a published value; for a value converted from another series, the
  conversion and its error band (a manual entry may carry one; no fetcher produces one,
  section 11);
* `version`, `obtained_at`, `obtained_by` (the user of a manual entry), `obtained_by_name`
  (their name or email, else the fetcher's name), `run_id` (the ledger row that added it).

**Append-only.** The table carries the same `forbid_mutation()` trigger as `reviews`,
`corrections` and `study_events`: a row is never updated or deleted. A revised value (a later
book prints the actual July figure where the earlier one printed an estimate; a typo in a
manual entry) is the next `version` of the same key and the old row stays, so a completed
study reproduces from the rows it cited. The repository writes a row only when the key has no
row yet or its current version carries a different value or citation (`plan` then `insert`,
under one advisory lock), so a value is never written twice, whoever asks.

**Table `cost_index_runs`**: one append-only row per attempt to obtain values: a refresh run
per source (what was asked for, what arrived, what failed, the document's hash), a manual
entry, a file import; `trigger`, `actor_type` / `actor_id` / `actor_name`, `status` (`ok`,
`skipped` with the reason, `failed` with the errors), `years_wanted`, `years_added`,
`rows_added`. The ledger shows when a value arrived and why one did not.

**Seeding.** The migration seeds nothing: `python -m basis.costs.refresh seed` (or the first
refresh run) appends the released file's 51 Historical Cost Index years and 651 location
rows, each citing its book and page and carrying the file's sha256, idempotently (a second
run adds nothing). The data file stays the citation source and the loader's manifest check
stays; the store is the record of what the product obtained, and an empty store prices
exactly as the file alone does (the tests pin both).

## 10. Selection by the study

`basis/costs/indices/select.py` turns the store's current rows into the values a study
prices with (`IndexValues`): for each year of the Historical Cost Index, the value of the
best-ranked source that holds it; for each year that has location rows, one table. The rank:
a manual entry first (the firm's own, cited), then the configured sources in
`COST_INDEX_SOURCES` order, then the released book, then any other source; a value marked
`derived` never outranks a published one. The released book's values are the baseline under
everything, so a year the store does not mention prices as before.

The per-study choice is unchanged from section 5 and lives in
`basis.domain.measurements.resolve_cost_index`: the cost year is the intake's placed-in-service
year (the appraisal's effective date as the cross-check, both extracted already), the location
row is the intake's ZIP or state, then the appraisal's; the location table is the one of the
latest year at or before the cost year (the 2020 book's until a later table is entered; its
year is in every row's citation). The factor, the index values and the citation go onto every
takeoff's `cost_year_source` / `location_source` and the export's `cost_source`, as before.

**The missing-year rule, restated** (section 4): no loaded index for the cost year means the
latest loaded index of an earlier year is applied, the assumption is written on every priced
row, every priced row is flagged `cost:YEAR_INDEX_MISSING` (BASIS-COST-001, outcome-style:
the study completes), and `summary.basis.cost_index` says `status: missing`, the factor
applied, the value the engineer must enter in words and where to enter it; `missing` lists
`cost_index`. Never a silent fallback, never a stopped study. A year whose stored value is a
conversion from another series is priced on it with `cost:YEAR_INDEX_CONVERTED`
(BASIS-COST-002) and `status: converted` until a published value arrives.

The store is read once per run of the assets step (`load_index_values` in
`process_assets`) and once per result (`load_structure_inputs`), inside their transactions;
nothing in a request fetches anything.

## 11. Acquisition: the store, then the sources, then the flag

The order is: check the store; else ask the configured fetchers, in order, only for the years
the store lacks from that source; else flag. A fetcher is one module in
`basis/costs/indices/<source>.py` satisfying one protocol (`protocol.py`: `name`, `network`,
`unavailable(settings)`, `wanted(have, today)`, `fetch(wanted, timeout)`), registered by
name in `registry.py`. Three exist.

**`rsmeans_2020_book`** (`book.py`): the released file as a fetcher, offline, always
available; the seed of section 9.

**`manual`** is not a fetcher but the entry path: `POST /v1/cost-indices` (a firm owner;
docs/api/COST_INDICES.md) and `python -m basis.costs.refresh enter` / `import`. The body
names the series, year, region, value, `as_of` and the citation (publication, page or URL as
printed: what ATG ch. 3 C.2(2) asks of a cost source); the row records the user and the time
and the ledger row says the same; the same value and citation twice is refused (409) and
nothing is written.

**`rsmeans_public`** (`rsmeans_public.py`): the public source, chosen by the research below.
Gordian publishes the actual RSMeans Historical Cost Index, free and without a login, on one
landing page per cost-book year, `https://www.rsmeans.com/landing-pages/<year>-rsmeans-cost-
index`, as four lines of the form "The actual July 2024 Historical Index = 294.8" (January,
April, July, October; verified for every year 2020-2026 on 2026-10-30; the values are
corroborated by a Virginia DMAS worksheet that reprints the July values and by Gordian's
Quarterly Construction Cost Insights reports, which state the base: "The index values are
based on a 30-city national average with a base of 100 on January 1, 1993"). It is the same
series the books print, so no conversion is needed: the stored value is RSMeans' own number,
with the page as its citation, the line as printed in `detail.wording` and the page's sha256
on the row. The year's value is the **July** figure, the convention of the books' Historical
Cost Index table (one value per past year, July 1; the 2020 book's own 2019 line is July 2019
= 232.2); until a year's July value is published the latest earlier quarter stands in, marked
provisional (`detail.provisional`, the quarter's date in `as_of`), and the next refresh asks
for that year again. The 2020 book's cost basis stays Jan. 1, 2020 = 239.1 (the book's
estimate for its own costs); the actual July 2020 value (234.6) is a different point in time
and is stored as 2020's July value like any other year's, ranked under the book's by default.
Parsing is strict: HTML comments and scripts are removed first (the 2026 page carries the
previous year's lines inside a comment), a month printed twice with different values fails
the year rather than guessing, and a page without the pattern fails the year with the reason;
a failure is a ledger row, never a stored value. Tested against a recorded copy of the 2024
page (`tests/fixtures/recorded/`), no network in tests.

**`rsmeans_api`** (`rsmeans_api.py`): a stub. RSMeans Data Online is a subscription (Core,
Complete and Complete Plus tiers on rsmeans.com; no free tier, no public developer
documentation; data integration "via API or a flat file" is sold through sales). The fetcher
reads `RSMEANS_API_URL` / `RSMEANS_API_KEY` and reports itself unavailable without them, so
a run records why no RSMeans API value arrived; with them it records that the client is not
implemented, rather than scraping.

**Why no converted fallback.** The brief named candidates for a free stand-in: the Turner
Building Cost Index (quarterly, free, one PDF per quarter, non-residential, "labor rates and
productivity, material prices and the competitive condition of the marketplace"), the BLS
Producer Price Index for construction inputs (public API, goods or goods plus services), ENR's
Building Cost Index (annual averages public to 2023 through reprints), the Census constant-
quality new-house index. The error of each as a stand-in for the Historical Cost Index was
measured on 2021-2025, where both are published (ratio to the 2020 value; the HCI's July
values 257.5 / 297.1 / 293.7 / 294.8 / 300.7 over 234.6 are 1.098 / 1.266 / 1.252 / 1.257 /
1.282):

| Series (ratio to 2020) | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|
| ENR BCI annual average | +0.3 % | -2.0 % | +3.4 % | not free | not free |
| Turner annual average | -7.2 % | -13.1 % | -6.8 % | -3.6 % | -1.6 % |
| Turner fourth quarter | -4.3 % | -10.2 % | n/a | -2.0 % | +0.6 % |
| BLS PPI inputs to construction, goods | +8.3 % | +11.1 % | +11.6 % | +11.7 % | +11.6 % |
| BLS PPI inputs to residential construction, goods | +7 to +11 % | | | | |
| Census new houses sold, constant quality | +4 to +9 % | | | | |

No free series tracks the HCI within 2 % every year: materials-only indices overshoot the
2021-2022 jump (the HCI includes installation), Turner lags it. A converted value would carry
a 2-13 % error on the one number that sets every dollar, while the real value is free on
Gordian's own page. So the public source is the HCI itself, and no fetcher converts. The
`derived` column and `cost:YEAR_INDEX_CONVERTED` remain for a firm that enters a converted
value by hand for a year no page covers: the conversion (the series, the ratio, the error band
from the table) goes in `derived` and every priced row is flagged until a published value
replaces it. The research notes with every URL are in the session's hand-over
(`side-session-notes/.../cost-index-store-47.md`).

## 12. Refresh: a scheduled job that never touches a study

`python -m basis.costs.refresh` (cron or Cloud Scheduler, monthly, or on demand; the pattern
of the outbox relay: a process with its own entrypoint, not a request) asks each enabled
fetcher for the years the store lacks from that source, with a timeout
(`COST_INDEX_FETCH_TIMEOUT_S`), appends what comes back and writes one `cost_index_runs` row
per source per run. A second run adds nothing (the tests pin it). It never touches a study: a
study prices with whatever the store holds when its assets step runs, and a value that
arrives later reaches it when the step reruns (a rejection at the assets gate, or
`POST /retry`). Network fetches happen only here and in the CLI, never inside a request.

Two decisions are settings, not assumptions: **`COST_INDEX_FETCH`** (default `off`: the job
asks only the offline sources, so a deployment prices from the seeded store and the firm's
manual entries until the firm turns fetching on; `on`: the enabled network fetchers too) and
**`COST_INDEX_SOURCES`** (default `rsmeans_2020_book,rsmeans_public`: which fetchers the job
asks and the order a year's value is preferred in; a manual entry always comes first). A
network source is recorded as `skipped` with the reason while fetching is off or its
credentials are missing (BASIS-COST-004); a fetch that raises or reads nothing is a `failed`
row with the errors (BASIS-COST-005) and the job exits 1 so the scheduler sees it.

The CLI also seeds (`seed`), enters a value (`enter`), imports a supplement-format file
(`import`), exports the store's current Historical Cost Index values in the same format
(`export`: what `evals.reprice_export --supplement` and `evals.structure_replay --supplement`
read, so an offline replay prices with exactly the store's values), lists the rows (`list`)
and the ledger (`runs`).

## Sources (sections 1-8)

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

## Sources (sections 9-12, all read 2026-10-30)

* Gordian, RSMeans data cost index landing pages,
  https://www.rsmeans.com/landing-pages/2020-rsmeans-cost-index through
  `.../2026-rsmeans-cost-index`: "The actual {Month} {Year} Historical Index = {value}", four
  quarters per year (July values: 2020 234.6, 2021 257.5, 2022 297.1, 2023 293.7, 2024 294.8,
  2025 300.7, 2026 314.5).
* Gordian, Quarterly Construction Cost Insights Report, Q4 2023
  (https://www.gordian.com/uploads/2023/12/Q4.2023.GordianQuarterlyInsightsReport_FINAL.pdf)
  and Q1 2025 (https://www.gordian.com/uploads/2025/02/Gordian-QuarterlyReport-2025-Q1-FINAL.pdf):
  the HCI's definition and base ("a 30-city national average with a base of 100 on January 1,
  1993"); HCI total 295.4 (Oct 2023), 293.9 (Jan 2025).
* Virginia DMAS, RSMeans data worksheet 2026
  (https://www.dmas.virginia.gov/media/zggognll/rsmeansdata-2026.pdf): the July 2019-2025
  Historical Cost Index values 232.2, 234.6, 257.5, 297.1, 293.7, 294.8, 300.7.
* Gordian, RSMeans Data Online tiers (https://www.rsmeans.com/products/online): Core, Complete,
  Complete Plus; free trial only.
* Turner Construction, Turner Building Cost Index (https://www.turnerconstruction.com/cost-index;
  one PDF per quarter): annual averages 2015-2025 943, 989, 1038, 1096, 1156, 1177, 1199, 1295,
  1373, 1426, 1485.
* U.S. Bureau of Labor Statistics, Producer Price Index, inputs to construction industries,
  goods (WPUIP2300001, June 1986 = 100) and inputs to residential construction, goods
  (WPUIP2311001), annual averages 2015-2025 through the Public Data API v2
  (https://api.bls.gov/publicAPI/v2/timeseries/data/): 211.4, 207.9, 216.4, 231.1, 231.2,
  228.2, 271.156, 321.113, 318.772, 320.445, 326.436 and 208.7, 206.1, 214.3, 228.3, 227.9,
  226.3, 266.382, 312.357, 311.403, 315.199, 322.069.
* ENR, First Quarterly Cost Report 2024 (reprint,
  https://communities.howardhughes.com/wp-content/uploads/2024/04/4-Engineering-News-Record-First-Quarterly-Cost-Report.pdf):
  Building Cost Index annual averages 2015-2023 5517, 5645, 5831, 6019, 6136, 6281, 6912, 7792,
  8130 (1913 = 100).
* U.S. Census Bureau, price index of new single-family houses sold, constant quality
  (https://www.census.gov/construction/nrs/xls/price_sold_cust.xlsx, 2005 = 100).
