# The cost segregation study Basis delivers: spec (draft, 2026-10-26)

What Basis hands the engineer to sign and the taxpayer's preparer to file, where every number comes from,
and what is missing today. Sources: IRS Publication 5653 (Cost Segregation Audit Techniques Guide, rev.
2-6-2025; "ATG"), `APPRAISAL_RESEARCH.md`, the design partner's study for the client package, and the
pipeline's output on that package. Client values are not in this file; a mock report built from the client
data lives in the private eval folder (`reports/mock_study_report_client_a.pdf`, builder
`work/build_mock_report.py`).

## 1. Input and output

| | What | Format |
|---|---|---|
| Input | Appraisal | PDF (legacy URAR 1004, or the redesigned URAR under UAD 3.6, mandatory for GSE submissions from 2026-11-02); sometimes the MISMO XML or UAD 3.6 ZIP |
| Input | Site photographs | JPEG |
| Input | Intake answers | price, closing costs, dates, use (section 4.1) |
| Output | **Cost segregation report** | PDF the engineer signs (section 2) |
| Output | **Depreciation schedule** | CSV/XLSX for the preparer's fixed-asset software (section 2, item 7) |
| Output | Evidence export | canonical JSON + assets CSV (exists: `basis/activities/export_report.py`) |

UAD 3.6 is the appraisal's format, written by appraisers for lenders. Basis reads it and never writes it.
There is no standard format for a cost segregation report: "there is no standard or prescribed format"
(ATG ch. 4 D(1)). The ATG lists what a quality report contains instead (section 2).

## 2. Report contents: ATG elements mapped to Basis data

| # | Report section | ATG | Content | Basis source | Today |
|---|---|---|---|---|---|
| 0 | Cover and executive summary | D.1 | preparer, study date, taxpayer, property, totals by class (land, land improvements, building, personal property) | intake, appraisal fields, allocation | no report; bucket totals in the export |
| 1 | Basis and land allocation | ch. 6 (real estate allocations), C.10 | price + capitalized acquisition costs − land = depreciable basis; land method and source; cross-checks | intake, appraisal (site value, final value, dates) | **missing** |
| 2 | Property description | D.2 | physical description, use, legal description, location | appraisal fields with page refs, photos, intake (use) | fields extracted, used by nothing |
| 3 | Methodology and engineering procedures | D.6, C.2, C.3, C.8 | approach, inspection record, quantities, unit cost source, escalation and location factor, reconciliation | pipeline settings and run record | partly in the run record |
| 4 | Legal analysis | C.7, D.2 | §1245 vs §1250 tests, Whiteco factors, HCA, Rev. Proc. 87-56, residential-rental test | reference corpus, citations | citations per asset exist |
| 5 | Summary of property units by class | D.5, C.5, C.9 | totals by class and category, common nomenclature | assets | categories are free text (C.5 fails) |
| 6 | Detailed schedule of property units | D.5, D.3, C.6, C.12 | item, location, quantity, RSMeans line (MasterFormat number = standard numbering), RCN, allocated cost, class, authority | assets, takeoffs, cost estimates, citations | rows exist; no allocated cost |
| 7 | Depreciation schedule for the preparer | D.3, C.13 | per class: basis, recovery period, method, convention, placed-in-service date; Form 3115 note for a look-back | allocation, intake dates | **missing** |
| 8 | Exhibit A: photographs with identified components | D.6 (record of inspections), D.9 | each photo with outlines and the schedule numbers; items not visible are marked estimated | photo objects (SAM 3 masks), rooms | masks exist; no exhibit |
| 9 | Assumptions and limiting conditions | D.7 | reliance on the appraisal and price, estimates for concealed systems, not an appraisal | template + run facts | missing |
| 10 | Certification | D.8, C.1 | the engineer signs that they developed the analysis; credentials | engineer (human) | missing |
| 11 | Exhibits | D.9, D.4 | appraisal excerpts (cost approach, sketch), cost source reconciliation, basis worksheet | appraisal pages, cost rows | missing |

The design partner's study for the client package has sections 0 (totals only), 5, 6 (by category, not by
item), a building-systems cost table, photographs (without identification) and the appraisal's sketch. It has
no narrative, methodology, legal analysis, land allocation worksheet, assumptions or certification in the
copy we have (question 9.1). Basis can exceed it on D.2, D.6, D.7 and on per-item evidence.

## 3. Which appraisal fields feed what

| Use | Appraisal fields | Why |
|---|---|---|
| Land value | `cost_approach.site_value` (and "not developed") | land is valued first (ATG ch. 3 p.37) |
| Basis corroboration | `listing_and_contract.contract_price`, `contract_date`, `reconciliation.final_market_value` | the price comes from the closing statement; the appraisal corroborates it |
| Dates | `reconciliation.effective_date_of_appraisal`, `contract_date` | value as of the purchase; the acquisition date drives the bonus depreciation rate (preparer) |
| Building replacement cost (interim, 5.3) | `cost_approach.total_cost_new`, `dwelling_cost`, `basement_cost`, `garage_cost`, `mechanicals_misc`, `site_improvements_value`, `effective_date_of_cost_data` | RCN of the improvements; site improvements stand in for land improvements the photos miss (driveways) |
| Building cost model (target, 5.3) | `improvements.gla_sqft`, `basement_area_sqft`, `basement_finish_percent`, `stories`, `design_style`, `type`, `units`, `garage_cars` + garage type, `foundation_type`, `exterior_walls`, `roof_surface`, `heating_type`, `cooling`, `quality`, outbuildings | size and type select the square-foot model and its systems |
| Depreciation per class | `improvements.year_built`, `effective_age_years`, `condition`, `cost_approach.remaining_economic_life`, `depreciation_physical` | ATG ch. 3 A(6): adjustments differ by item |
| Location | `subject.city`, `state`, `zip` | RSMeans city cost index (5.6) |
| Description | address, county, legal description, APN, zoning, site area, view | report section 2 |
| Validators only | rooms, bedrooms, bathrooms, neighborhood checkboxes | do not move the allocation |

This is the field priority the evaluation harness scores as tiers (`backEnd/evals/appraisal/SPEC.md`
section 5).

## 4. Inputs Basis does not have today

### 4.1 Intake (the taxpayer or their CPA)

| Input | Used for | Notes |
|---|---|---|
| Purchase price and closing statement | basis (5.1) | the appraisal's contract price corroborates, it does not substitute |
| Capitalized acquisition costs | basis | title, recording, legal fees; the design partner allocates them pro rata |
| Acquisition (contract/closing) date | bonus rate, cost year | |
| Placed-in-service date | depreciation start, convention, cost year | ready and available for rent, not the appraisal date |
| Use | 27.5 vs 39 years | residential rental only when ≥ 80 % of gross rental income is from dwelling units; transient lodging (a bed-and-breakfast) is excluded → 39 years |
| Prior depreciation (look-back) | Form 3115 | when the property is already on the books |
| Land figure if no appraisal site value | land (5.2) | assessor's land and improvement values, land appraisal |

### 4.2 Data

* A building-systems cost model (RSMeans square-foot models or equivalent) for structure, foundations,
  framing, roofing, exterior skin, electrical, plumbing, HVAC, interior construction (5.3).
* City cost indexes and historical cost indexes (5.6).

## 5. The math

### 5.1 Depreciable basis

```
depreciable basis = purchase price + capitalized acquisition costs − land
```

Allocated across land improvements (15 years), personal property (5 and 7 years) and the building and its
structural components (27.5 or 39 years). The allocated lines must sum to the basis exactly (ATG C.10).

### 5.2 Land

Order of preference, each recorded with its source and page:

1. The appraisal's opinion of site value, as of a date close to the purchase (ATG p.37, p.64: land at highest
   and best use, valued first). Checked: land share of price 5–60 %; effective date within 12 months of the
   contract.
2. A land appraisal or land comparables.
3. The assessor's land-to-total ratio applied to the price. Never alone: "be wary if a cost segregation study
   relies solely on local assessed values" (ATG p.64).
4. Never: price minus the improvements' value (residual land; ATG p.64), nor a rule-of-thumb percentage
   without support.

When the appraisal's cost approach is "not developed" and there is no site value, the study raises
`land_value_not_in_appraisal` and the engineer chooses 2 or 3. The model never invents a land value.

### 5.3 Replacement cost new (RCN)

**Target: detailed engineering cost estimate (ATG ch. 3 C.2).** Every item, including the building systems
nobody photographs, is priced from one cost source with one technique, escalated and located the same way.
Photo-identified items give the §1245 and land-improvement lines; the systems model gives the rest; the two
are reconciled so nothing is counted twice (for example plumbing fixtures are part of plumbing). This is what
the design partner's study does: a component table plus a building-systems table, all at RCN.

**Interim (what the mock uses): carve-out from the appraisal's cost approach.**

```
building RCN        = appraisal total cost new
personal property   = Σ RCN of photo-identified 5- and 7-year items
structure RCN       = building RCN − personal property
land improvements   = max(appraisal as-is site improvements, Σ RCN of photo-identified 15-year items)
total RCN           = structure RCN + personal property + land improvements
```

Caveat, stated in the report: mixing a per-square-foot building figure with unit-cost components "can produce a
skewed result in favor of § 1245 property" (ATG ch. 3 C.4(3)). It also inherits the appraisal's arithmetic
(on the client package the cost approach contradicts itself in four places; the extraction validators must
surface these before the engineer relies on it).

### 5.4 Depreciation and reconciliation to basis

Target: RCN less depreciation per item or class (effective age over expected life for the class, condition),
then scale the RCNLD lines so they sum to the basis (ATG ch. 3 A(6): RCNLD plus land should approximate the
price; adjustments "are generally expected to be different among the various items").

Interim and design partner: one factor, `basis / total RCN`, on every line. Simple and exact, but it gives a
carpet the same depreciation as the foundation.

### 5.5 Classification

Per item, rules first (asset classes, the ATG residential and lodging matrices), a model that must cite
otherwise, contested items flagged. The building's class comes from the intake's use (4.1), not from the
photos or the appraisal. Lodging changes item classes too: compare the design partner's choices (section 8).

### 5.6 Location and cost year

Location changes three things:

1. **Costs.** RSMeans unit costs are national; the city cost index for the property's ZIP adjusts them.
   Today the pipeline applies one global location factor (1.000) and the current cost year (2026) to every
   study (`basis/config.py`, used in `process_assets.py`). Both must come from the study: the location from
   the appraisal's ZIP, the cost year from the placed-in-service date.
2. **Land value**, through the appraisal.
3. **Tax treatment outside the federal return.** Recovery periods are federal and do not depend on
   location; states differ on conforming to bonus depreciation (the preparer's concern).

Stories, basement, garage, quality and GLA change the building cost model (5.3). They do not change classes.

## 6. Exhibit A: photographs with identified components

Each photograph with the outline of every counted object and a badge with its schedule number; a caption with
the room and the item numbers. Items no photograph shows (wiring, structure, concealed plumbing) are marked
"estimated" in the schedule. This is the ATG's record of inspection (D.6) and evidence most studies do not
carry. Built from `photo_objects` (masks) and the rooms' inventories; the mock shows six photos.

## 7. Gaps today, in the order they block a report

Status 2026-10-31 (live run 4 on main 9c430aa, EVAL_REPORT 50):

* Closed: 1 (basis worksheet, land, allocation that ties to the basis to the cent: #94, #97, #100), 2 (the
  RSMeans square-foot systems model), 3 (location and cost year come from the study, with a cited index:
  #96, #97), 5 (use is an intake question), 8 (the structure model reads the appraisal's fields).
* Open: 4 (186 free-text categories on one study, the same item under two names) and 6 (no PDF, no
  schedule). 7 is improved: 144 of 384 assets were flagged on run 4.

The list below is the 2026-10-26 original.

1. No basis or land allocation, no reconciliation to a total (5.1, 5.2). Basis prices only what the photos
   show (on the client package about a fifth of the engineer's replacement cost new for the building) and stops.
2. No building-systems cost (5.3).
3. Location factor and cost year are global settings (5.6).
4. Free-text asset categories (one component under three names); ATG C.5 asks for a common nomenclature.
   Use the component label vocabulary plus the MasterFormat number already in each cost row.
5. Building class assumes residential rental; use is not an intake question (4.1).
6. No report PDF, no depreciation schedule export.
7. In one local run of the client package every asset (376 of 376) was flagged for review; the flags need to separate real doubt from routine.
8. Appraisal fields are extracted but nothing reads them (`APPRAISAL_RESEARCH.md`; the extraction harness being built
   scores the fields section 3 needs).

## 8. What the design partner's study shows about method

* Building basis = price − land at a flat percentage of price; closing costs added and allocated pro rata.
  The appraisal's own site value was not used (question 9.2).
* Component and system costs at RCN, scaled to the basis by one factor.
* Classes for a bed-and-breakfast: 39-year building; 5-year includes moldings, raised paneling, electric
  fireplaces, communication/data, access control, carpet, wood flooring, cabinets, window treatments,
  appliances, FF&E; ceramic tile, bath fixtures and interior lighting stay in the building; the driveway is
  most of the 15-year class. Basis's rules differ on trim, fireplaces (5-year when electric), tile and bath
  fixtures. These are the rule rows to review with the engineer.

## 9. Open questions

1. Is the design partner's full deliverable longer than the copy we have (narrative, methodology,
   certification)?
2. How did they set land (assessor ratio, rule of thumb), and would they accept the appraisal's site value?
3. Which of the classification differences in section 8 are firm practice, and which are judgment calls?
4. Does the preparer want the schedule as CSV, XLSX, or a specific fixed-asset import format?

## 10. How it will be verified

* The extraction harness (`backEnd/evals/appraisal/SPEC.md`) for every appraisal field section 3 uses.
* The basis and allocation math as unit tests with hand-computed cases (exact reconciliation, land methods,
  not-developed cost approach, look-back).
* The client package against the design partner's study: per-class totals and per-category differences,
  explained line by line (the mock's internal page is the first version of this comparison).
* A second client package before any claim beyond one property.

## 11. Build design (2026-10-31): the report and the schedule

What gets built, how it is checked, and what needs a decision first. Nothing below is built yet. Section
11.10 lists the decisions.

### 11.1 What the standard asks, and what is public

* **The IRS guide.** Pub 5653, rev. 2-2025, chapter 4 "Principal Elements of a Quality Cost Segregation
  Study and Report". It has thirteen study elements, C.1 to C.13, and nine report elements, D.1 to D.9.
  "There is no standard or prescribed format" (D(1)). This is the only public, citable checklist.
* **ASCSP.** Its Minimum Quality Standards (MQS 2009-01, with a report appendix in MQS 2011-1) are
  members-only and say they are aligned with the ATG. We cite the ATG, not the MQS.
* **Public sample reports.** Three were read (sources in the research notes of EVAL_REPORT 51, to be
  written with the build):
  * an engineered study, 34 pages: summary letter, narrative, engineering procedures, certification,
    methodology, classification, limiting conditions, photos, and exhibits for reconciled costs,
    indirect costs, take-offs and basis reconciliation;
  * an automated single-family study, 247 pages, one page per item;
  * a duplex study, 75 pages, built with ReportLab.

  The automated sample applies 100 % bonus because the placed-in-service date is after 2025-01-19, but
  its own settlement statement is dated 2024-06-18. The rate depends on the **acquisition** date
  (P.L. 119-21), so the schedule carries acquisition and placed-in-service dates separately (11.5).

### 11.2 The deliverables

| File | For | Written by |
|---|---|---|
| `rev-<n>.report.pdf` | the engineer to sign and the taxpayer to keep | the export step, after the assets review is approved |
| `rev-<n>.schedule.csv`, `rev-<n>.schedule.xlsx` | the preparer's fixed-asset software | the export step |
| `rev-<n>.json`, `rev-<n>.assets.csv` | evidence and audit | the export step (exists) |

Everything is derived from the export document, the one canonical record of the approved revision. The
report adds no number of its own. Generation is a pure function of the export, the report inputs (11.6),
the template version and the renderer version. Exporting twice gives byte-identical files, as the JSON
already does.

### 11.3 The report, section by section

The order follows ATG D.1 to D.9. Every C element is answered somewhere, and an appendix maps each one to
its page (11.3.1), which no sample we read has.

| # | Section | ATG | Content (source) |
|---|---|---|---|
| 1 | Cover and summary letter | D.1 | taxpayer, property, study date, preparer and firm; totals by class (land, land improvements, building, personal property) with % of basis (`summary.basis.allocation.by_bucket`, `worksheet`) |
| 2 | Property description | D.2 | address, legal description, APN, use, size, year built, effective age, stories, basement, garage, site, from appraisal fields with page references; one exterior photo |
| 3 | Basis and land | D.4, C.10, C.11 | price, capitalized acquisition costs and their allocation, land and its method and source, building basis (`worksheet`, its `checks`) |
| 4 | Methodology and engineering procedures | D.6, C.2, C.3, C.4, C.8 | detailed engineering cost estimate; inspection record (who photographed, when, how many photos); documents reviewed; take-off sources; RSMeans edition, cost year and index with citation, location factor; the structure model; depreciation method; reconciliation result (`takeoff`, `cost_index`, `structure`, `depreciation`, `reconciliation`) |
| 5 | Legal analysis | C.7, C.12, D.2 | § 1245 vs § 1250 tests (Treas. Reg. 1.48-1(e)(2), Whiteco factors, HCA), Rev. Proc. 87-56 classes, the residential-rental test with the intake's answer, and the property-specific positions taken (contested items, firm conventions), each with its cited passage |
| 6 | Summary of property units | D.5, C.5, C.6, C.9 | per class, then per category in a controlled vocabulary (11.4), with RCN, RCNLD and allocated cost |
| 7 | Detailed schedule | D.3, D.5, C.6, C.12 | every item: number, description, location (floor and room), quantity and unit, MasterFormat line, RCN, allocated cost, class, asset class (57.0, 00.3...), authority; "estimated" for concealed systems; § 1245 items reclassified from § 1250 marked (C.12) |
| 8 | Depreciation schedule | D.3, C.13 | per class: basis, GDS life, method, convention, placed in service, acquisition date, bonus eligibility facts (11.5); look-back and Form 3115 note when applicable |
| 9 | Assumptions and limiting conditions | D.7 | reliance on the appraisal and the price, estimates for concealed systems, not an appraisal, not tax advice, and the open review flags left at approval (none at approval means none listed) |
| 10 | Certification | D.8, C.1 | the signer developed the analysis; credentials; whether a site visit was made (11.10, question 1); fee not contingent on the result; no interest in the property; signature block |
| A | Exhibit A: photographs with identified components | D.6, D.9 | each photo with the outline of every counted object and its schedule numbers (STUDY_REPORT_SPEC section 6); needs `photo_objects` in the export (11.7) |
| B | Exhibit B: cost source reconciliation | D.9 | top-down building RCN against the itemized and concealed rows per system, the gap and the tolerance (`reconciliation.by_component`) |
| C | Exhibit C: quality-element checklist | C.1-C.13 | each element, where the report answers it, and what Basis did (11.3.1) |

#### 11.3.1 The quality-element checklist

C.1 → section 10. C.2 → section 4. C.3 → section 4 and exhibit A. C.4 → section 4 (interviews recorded in
the report inputs, or "none"). C.5 → sections 6 and 7 (controlled vocabulary). C.6 → section 7
(MasterFormat). C.7 → section 5. C.8 → section 4 and the cost trail. C.9 → section 6. C.10 → section 3 and
exhibit B. C.11 → section 3 (closing costs pro rata; GC overhead pro rata in each unit cost). C.12 →
section 7. C.13 → section 8 (§ 263A considered: it reaches produced property, and an acquired existing building has
no self-constructed costs; change in accounting method for a look-back; no sampling).

### 11.4 Common nomenclature (C.5) and numbering (C.6)

On run 4, 384 assets carried 186 distinct categories, with the same item under two names ("Window
(structural component)", "Windows (structural component)"). The report groups by the rules-table key, or
the component label where there is no rule row, with one display name per key, and by the systems of the
structure model. The free-text category stays in the evidence JSON.

The MasterFormat line appears today only inside the cost source text ("RSMeans Residential 2020 08 71
20.40 0400 ..."). The build parses it into a structured `masterformat` field on the cost trail, so it can be
tested; it is not parsed again inside the report.

### 11.5 The preparer's schedule

There is no common import layout for fixed-asset software. The shared denominator is Form 4562 Part III
B: classification, month and year placed in service, basis, recovery period, convention, method. The
columns:

`line, description (45 characters, no quotes or commas: Fixed Assets CS's limit), irc_section,
asset_class, property_class (5/7/15/27.5/39), date_acquired, date_placed_in_service, basis (allocated,
cents), method (200DB/150DB/SL), convention (HY/MQ/MM), gds_life, ads_life, real_property (Y/N),
bonus_eligible (Y/N), statutory_bonus_rate, rate_source, source_line`.

* Basis per class ties to the report to the cent, and the sum ties to the depreciable basis.
* Method and convention follow Pub 946 Table 4-1:
  * 5 and 7 years: 200 % DB;
  * 15 years: 150 % DB;
  * 27.5 and 39 years: straight line, mid-month.

  For 5, 7 and 15 years the convention is half-year, or mid-quarter when more than 40 % of the year's
  other basis was placed in the last quarter. Basis cannot know the taxpayer's other assets, so the column
  defaults to HY with a note and the preparer confirms it.
* Bonus: the statutory default rate comes from the **acquisition** date (Pub 946 2024: 60 % in 2024; P.L.
  119-21: 100 % for property acquired and placed in service after 2025-01-19; for property acquired before
  it, 40 % when placed in service in 2025, and 20 % in 2026 per Notice 2026-11, to be confirmed against an
  IRS table before release).
  It is labeled as the default and cites its source. Elections are the preparer's.
* Vendor formats (Fixed Assets CS, Sage, Drake, CCH, Bloomberg) are column mappings over this one schedule.
  They were to be added when a CPA partner provides a template; 11.5.1 (2026-11-01) builds five from the
  vendors' published import documentation instead, each marked unverified until a preparer confirms it.

#### 11.5.1 Vendor import profiles (2026-11-01)

No CPA partner has sent a template yet. So that the first one can import without a round trip, the
schedule gets one profile per package CPAs use most for cost segregation, each built from the
vendor's own published import documentation and nothing else. Where a vendor publishes only the
mechanism (a mapping dialog) and not the layout, the profile is a plainly headed file for that
mechanism, and this section says so. **None of the five is verified against the real software**: each
is built to the published specification, and `docs/api/REPORT.md` says which (all) wait for a
preparer's confirmation. The profile is rendered on request from the stored export
(`GET /v1/studies/{id}/report?format=csv|xlsx&profile=<name>`); the export step still writes only the
generic files.

What every package has in common, and what the profiles therefore map from the generic row: a
description, the date placed in service (and acquired), the cost, a method, a convention (explicit, or
implied by method and life), a recovery period, and the bonus election. Every profile's rows carry the
line's allocated cost unchanged, so they sum to the depreciable basis to the cent as the generic rows
do (`vendor_rows` checks it).

**Thomson Reuters Fixed Assets CS** (`profile=fixed_assets_cs`; XLSX or CSV). Public: the Excel
import's accepted fields and limits.

* Sources: [Import asset data from an Excel spreadsheet](https://www.thomsonreuters.com/en-us/help/fixed-assets-cs/conversion/import-asset-data-from-an-excel-spreadsheet);
  [Examples of asset data that Fixed Assets CS verifies before importing](https://www.thomsonreuters.com/en-us/help/fixed-assets-cs/integration/examples-of-asset-data-that-fixed-assets-cs-verifi);
  [Creating an Excel spreadsheet template to import](https://www.thomsonreuters.com/content/helpandsupp/en-us/help/fixed-assets-cs/import-from-excel/create-an-excel-spreadsheet-template-to-import.html);
  the conversion guides [Sage Fixed Assets to Fixed Assets CS](https://www.thomsonreuters.com/content/dam/helpandsupp/en-us/Topics/fixed-assets/Conversion/files/convguide_fas_to_fixa.pdf) and
  [BNA Fixed Assets Desktop to Fixed Assets CS](https://www.thomsonreuters.com/en-us/help/fixed-assets-cs/conversion/depr-conversion-guide-asset-bna-fixed-assets) (for how it treats 168(k) and mid-quarter).
* File: an Excel spreadsheet (the template is exported as XLS; the help also describes a CSV), one
  header row, columns mapped to fields in the Import Spreadsheet window, so column order is free. "Only
  complete asset information can be imported to set up a new asset." "Don't use commas or quotation
  marks in any cells."
* Fields and limits (verbatim where quoted): Description "0-45 characters, no quotation marks or
  commas"; Memo one and two 0-45; Date In Service, Date Acquired "Any Excel date format, such as
  MM/DD/YY"; Cost -9999999.99 to 99999999.99; Method, a CS list ("M or MACRS", "M SL or MACRS SL Class
  Life", "M SL ADS or MACRS SL ADS Life", "M 150 GDS or MACRS 150% & Farm", "SL or Straight Line",
  "150 DB or 150% DB", "200 DB or 200% DB", "L or Land", ...); Life 1-99; ADS Life 1-99; Real property
  "R = Real property", blank otherwise; Force Mid Qtr "Y or YES = Yes; N or NO = No (blank defaults to
  No)"; New / Used "N = New; U = Used", blank defaults to New; Activity (a list by return type: 1040
  "A", "C", "E", "F", ...; 1120 "Page 1", "Rental", ...).
* Bonus: no import field. "Fixed Assets CS automatically complies with section 168(k) (bonus
  depreciation) for qualifying assets. You can elect out of bonus depreciation on a single asset, per
  class life, per treatment, or per client."
* The profile: Description (the generic cut, already 45 and clean), Date In Service, Date Acquired,
  Cost, Method `M` for every class (MACRS: the package applies 200 % DB to 5 and 7-year, 150 % DB to
  15-year and straight-line mid-month to 27.5 and 39-year property from the life and the real-property
  mark, as Pub 946 Table 4-1 does; `M SL`/`150 DB`/`200 DB` are the non-MACRS or elective methods),
  Life (5, 7, 15, 27.5, 39), ADS Life, Real property (`R` for 27.5 and 39-year, else blank), Force Mid
  Qtr `N` (the schedule's half-year default; the preparer's 40 % test), New / Used `N`, Memo one
  (the report line, IRC section and asset class), Memo two (the statutory bonus default and its
  source, 45 characters). Activity is left out: it depends on the return the preparer files. **Not
  verified**: the reading of `M` plus life as the class method is the package's documented design,
  not an import we ran.

**Sage Fixed Assets Depreciation** (`profile=sage`; XLSX or CSV). Public and complete: the Custom
Import Helper's field list, formats and codes.

* Sources: [Sage Fixed Assets Depreciation User's Guide for U.S. Companies](https://docs.sage.com/docs/en/customer/sfa/Guides/open/fasaagd.pdf) (last updated 2020-01-16): Appendix C "Custom Import Helper" (C-1 critical fields, C-12 "List of Importable Fields", C-14 "Field Specifications"), chapter 6 "Depreciation Method" (the code table, 6-6), Appendix A "Types of Property" and "Averaging Conventions";
  Sage KB [How to do a custom import](https://us-kb.sage.com/portal/app/portlets/results/view2.jsp?k2dockey=221924950016892) (the five critical fields, "MF200 needs to be in two separate columns", dates "MM/DD/YYYY", 168 Allow % "20, 30, 40, 50, 60, 80, or 100").
* File: "ASCII files (comma delimited)" or "Excel Spreadsheet files"; fields mapped in the helper
  ("The order of the fields within a record is not important"); the helper "only creates new assets".
  "Format all columns to General Text."
* Critical fields, all five for any book imported: Property Type, Placed-in-Service Date, Acquisition
  Value, Depreciation Method, Estimated Life. "Declining balance methods require a depreciation
  percentage in addition to the depreciation method. These depreciation methods are MF, MA, MT, MI, MR,
  DC, DE, DI, DB, DD, and DH." "The 'Plus 168' depreciation methods require a value in the 168
  Allowance % field. If you use depreciation method MA, MR, SB, or AA, then a value ... must appear."
* Formats (C-12): Description "Alphanumeric - 80"; Location, Department, Vendor 25; Asset ID 50;
  dates "MM/DD/YYYY" ("Date - 10"); "Numeric (currency) fields are formatted with nine (or ten) digits
  and two decimals (123456789.12)"; Estimated Life and ADS Life "YYMM" ("enter 5 1/2 years as 0506";
  a 1-3 character value is read as Y, YY or YMM); Declining Balance % "100, 125, 150, 175, or 200";
  168 Allow % "Numeric - 2"; Mid-Quarter Flag "Y" for mid-quarter or "N" for half-year "for
  depreciation methods MF, MA, MT, MI, MR, AD, or AA", blank "if using other depreciation methods";
  Property Type one uppercase letter: "P Personal property, general", "R Real property, general",
  A, T, Q, S, C, E, F, H, Z, V.
* Method codes (6-6; "A depreciation method code is a combination of the depreciation method and
  averaging convention"): MF "MACRS formula", MA "MACRS formula plus 168 Allowance", MT "MACRS table",
  AD "ADS straight-line MACRS", AA "ADS straight-line MACRS plus 168 Allowance", SL, SF, SH, DB, DH,
  ..., "All depreciation method codes must be in uppercase letters."
* Bonus: the method itself. MA with 168 Allow % claims the allowance; MF does not.
* The profile: Asset ID (the line), Description, Location (25), Property Type (`R` for the 27.5 and
  39-year classes, `P` otherwise: 15-year land improvements are IRC 1250 property but take the
  half-year convention, which the guide ties to MACRS property other than residential rental and
  nonresidential real property), Placed-In-Service Date and Acquisition Date (MM/DD/YYYY),
  Acquisition Value, Depreciation Method (`MA` with 168 Allow % when the schedule's statutory default
  rate is above zero, the default election in every package; `MF` otherwise: the preparer electing out
  changes `MA` to `MF`), Declining Balance % (200, 150; 100 for the straight-line real property
  classes), 168 Allow % (the default rate as a whole number: 40, 60, 100, ...), Estimated Life
  (`0500`, `0700`, `1500`, `2706`, `3900`), ADS Life (`0900`, `1000`, `2000`, `3000`, `4000`),
  Mid-Quarter Flag (`N` for the half-year classes, blank for real property). **Not verified**: that
  the helper accepts `MF` with `100` for real property (the listed percentages include 100 and the
  guide's example data is not public) and the 168 Allow % values above 100 % phase-down rates the
  2020 guide predates (the KB lists 20-100).

**Intuit ProConnect Tax, and Lacerte** (`profile=proconnect`; XLSX for ProConnect, CSV for Lacerte).
Public and complete: Intuit's template and data rules.

* Sources: [How to import tax data from a spreadsheet in ProConnect Tax](https://accountants.intuit.com/support/en-us/help-article/import-export-data-files/import-tax-data-spreadsheet-proconnect/L1e3s4KNr_US_en_US) with its
  [Depreciation (fixed assets) template](https://digitalasset.intuit.com/DOCUMENT/A0rjGM1Sa/Depreciation_Template.xlsx) and
  [data rules](https://digitalasset.intuit.com/DOCUMENT/A8P4ffPL9/Depreciation_data_rules.xlsx) (read 2026-11-01);
  [Common questions about Fixed Asset Import in Lacerte](https://accountants.intuit.com/support/en-us/help-article/partnership/common-questions-fixed-asset-import/L2oMOnRf4_US_en_US).
* File: ProConnect "Only Excel files (.xls or .xlsx) or comma-separated value files (.csv)", first
  worksheet only, columns matched to fields by heading. Lacerte: "Only .CSV and .XLS files are
  supported" ("2007 .XLSX files aren't supported"); required columns Description, Date placed in
  service, Cost or basis, Category ("must be set up numerically"); method optional (the "Lacerte
  Method" numbers, or the wizard derives one).
* The template's 17 headings in order: Description of Property, Form Number, Activity Number,
  Category Number (1-8), Date Placed in Service, Cost or Basis, Current Sec 179, Method, Prior Sec
  179, Prior Special Depreciation Allowance, Prior Depreciation or Amortization, Prior Bonus
  Depreciation, AMT Prior Depreciation (MACRS only), Book Cost Basis, Book Method, Book Life, Book
  Prior Depreciation.
* Data rules: Description "Alpha Numeric + Special Characters", "Max characters: 25"; Date "mm/dd/yyyy"
  (a negative date "prints various"); Cost or Basis "Numeric only" 1 to 999999999; Category 1
  Automobiles/transportation equip., 2 Furniture and fixtures, 3 Machinery and equip, 4 Buildings, 5
  Improvements, 6 Land, 7 Miscellaneous, 8 Amortization; Form Number by return type (1040: 3 "Sch. E
  rental", 17 "Form 4562 only"; 1065: 3 "Form 8825"; ...); Method table: 53 "5 yr % Personal", 57
  "7 yr % Personal", 66 "15 yr % Property", 85 "27.5 yr SL Res. RE", 87 "31.5/39 year SL Non. RE"
  (the "%" methods are the declining-balance class methods, the "SL" ones the elections).
* Bonus: no current-year column (only "Prior Special Depreciation Allowance"); the program applies
  the special allowance to qualifying property unless the preparer elects out.
* The profile: the 17 headings in the template's order so the heading match is automatic;
  Description cut to 25; Form Number and Activity Number blank (they depend on the return); Category
  3 for 5-year, 2 for 7-year, 5 for 15-year, 4 for 27.5 and 39-year (reporting groups only); the
  date as mm/dd/yyyy; Cost or Basis to the cent (the rules give integer bounds; whole-dollar rounding
  is the preparer's); Method 53/57/66/85/87; every prior-year and book column blank. **Not
  verified** against either program.

**CCH ProSystem fx Fixed Assets** (`profile=cch`; XLSX or CSV). Public: the mechanism only.

* Sources: CCH KB [How do I import a Microsoft Excel spreadsheet into CCH ProSystem fx Fixed Assets?](https://support.cch.com/kb/solution.aspx/sw20138);
  KB [Creative Solutions Conversion Table](https://support.cch.com/kb/solution.aspx/sw32681) (how it reads lives and bonus on conversion);
  CCH Axcess Tax help [Importing CCH ProSystem fx Fixed Asset Data](https://download.cchaxcess.com/pfxbrowserhelp/TaxHelp/Content/ImportExport/IE_Fixed%20Assets.htm) (the return-side import, not the layout).
* File: "The Microsoft Excel spreadsheet should contain: Column Headings. Data for all books in a
  single Worksheet. One row of data per asset. No empty columns between columns of data." Imported
  through File > Import > Various File Types > User Defined Import with a translation table that
  links each column heading to a Fixed Assets field; "Some data points, like method, must have each
  unique entry in the Microsoft Excel column matched to a corresponding option in Fixed Assets."
  "The minimum columns needed to be matched are the two required columns: date in service and
  depreciation method." The sample file and translation table ship in the product's `maps` folder
  and are not published.
* Not public: the field names, the method and convention option names, date and length limits.
* The profile: a plainly headed sheet for the translation table: Asset Number, Description,
  Location, Date In Service, Date Acquired, Cost, Method (`MACRS 200DB`, `MACRS 150DB`, `MACRS SL`:
  one distinct value per option the preparer maps once), Convention (`Half-Year`, `Mid-Month`),
  Life, ADS Life, IRC Section, Asset Class, Bonus Default % (the statutory default; the conversion
  notes say the package's "Special Allowance option will be applied for all qualified assets" by
  default), Authority. **Built to the mechanism, not to a published layout.**

**Bloomberg Tax Fixed Assets** (`profile=bloomberg`; CSV). Public: the mechanism only.

* Sources: [Fixed Assets Quick Start Guide](https://assets.bbhub.io/bna/sites/9/2020/10/FAQuickStartGuide.pdf) (2020): "The Import utility allows you to import fixed asset data from spreadsheet applications that can save in CSV (comma separated value) or other comma delimited formats"; the Import wizard maps the file's columns to asset fields; "For more information, see Procedure Help and review the Fixed Assets Import Guide" (not published); the MQ Policy "drop-down box allows you to either force a particular convention, such as Half-Year or Mid-Quarter, or allow the application to solve ... or ... leave the convention As Is" and "Prior year columns are automatically set to As Is during import from other fixed asset programs"; Section 179 "Choose either Maximum or None".
  Thomson Reuters' [BNA conversion guide](https://www.thomsonreuters.com/en-us/help/fixed-assets-cs/conversion/depr-conversion-guide-asset-bna-fixed-assets) names BNA method codes (SL, SLMM, SLF, DB175, ...) and conventions (MQ, AMQ) from the outside.
* Not public: the Import Guide, so the field names, the method and convention codes and the date
  format.
* The profile: the same plainly headed layout as CCH's, as a CSV (the format the utility reads),
  with the convention written as the guide names it (`Half-Year`, `Mid-Month`) and MM/DD/YYYY dates.
  **Built to the mechanism, not to a published layout.**

Not included: Drake (its fixed-asset import is a Drake-to-Drake file), Intuit ProSeries (its own
Fixed Asset Manager; the same mapped-CSV mechanism), and UltraTax CS (it reads Fixed Assets CS
directly).

### 11.6 Report inputs Basis does not have yet

Asked once per firm, with a few per study:

* **Firm, once:** name, address, logo (optional), the engineer's name and credentials (license number and
  state), standard limiting conditions (the template's by default, editable).
* **Study:** taxpayer name, who took the photographs and when (a site visit by the engineer, or photos
  supplied by the taxpayer: the report states which and does not claim a visit that did not happen),
  people interviewed (optional), prior depreciation for a look-back (optional).

Without them the report renders with marked placeholders and a DRAFT watermark. "Final" needs every
required input and the assets review approved.

### 11.7 Data the build adds to the export

* `photo_objects` per photo: outline polygon or box, label, schedule line numbers. Exhibit A draws from
  these.
* The structured `masterformat` field (11.4).
* The report inputs (11.6), stored with the study and the firm and versioned like the intake.

### 11.8 Rendering

Two-stage, as the standard practice for generated documents:

1. `basis.report.model`: export → `StudyReport` (pydantic), with every number computed and every
   invariant checked:
   * allocated rows sum to the basis;
   * class totals equal the schedule;
   * each section's figures appear in the source document.

   A failed invariant stops the report with a `BASIS-REPORT-<nnn>` code; the schedule is never shipped
   with a broken total.
2. A template renders the model. The candidates, as measured on this Mac by the research (400-row table,
   repeated header, "page X of Y", two separate processes):

| | Typst (typst-py) | WeasyPrint | ReportLab |
|---|---|---|---|
| License | Apache-2.0 | BSD-3 | BSD (PLUS is paid) |
| Byte-identical output | with a fixed timestamp | by default | with `invariant=1` |
| System dependencies | none (fonts bundled; system fonts can be ignored) | Pango, HarfBuzz, fonts | none |
| Table of contents, repeated headers | `outline()`, `table.header` | CSS `target-counter`, `thead` | `multiBuild`, `repeatRows` |
| Accessibility | tagged PDF, PDF/UA-1 (0.14) | partial | manual |

**Proposed: Typst.** Fonts are hermetic, so the same bytes come out on a Mac, in CI and in the Docker image.
It needs no system libraries, and its tables, outline and page counters are built in. The cost is a
62 MB wheel and a templating language the team does not know yet.

The first build step is a bake-off on the real content: run 4's 384-item schedule and 52 photos with
outlines, rendered by Typst and by WeasyPrint. It measures render time, file size, byte identity across
two processes and the Docker image, and page-by-page appearance. ReportLab is the fallback. The bake-off
result goes to EVAL_REPORT before the template is written.

#### 11.8.1 Bake-off result (2026-10-31): Typst

Run 4's real content went through both renderers with the same document, font file and photos:

* the 384-item schedule in landscape with a repeated header;
* the class summary with "page X of Y";
* 47 photographs with their 328 counted objects outlined and numbered (the outlines drawn by Pillow
  beforehand, so both renderers only place images).

Scripts and outputs are private, in the scratchpad.

| | Typst 0.15 (typst-py) | WeasyPrint 70.0 |
|---|---|---|
| Pages | 61 | 62 |
| Render time (Mac, 2 runs) | 0.17 s, 0.10 s | 1.40 s, 1.13 s |
| Render time (Linux, worker image, Debian 13) | 0.13 s, 0.11 s | 1.18 s, 1.29 s |
| Size | 3.62 MB | 2.96 MB |
| Byte-identical, two processes, same machine | yes (Mac and Linux) | yes (Mac and Linux) |
| **Byte-identical, Mac against Linux** | **yes** | **no** (Pango 1.58.2 on the Mac, 1.56.3 in the image) |
| Fonts | embedded, subset | embedded, subset |
| System libraries | none | Pango, HarfBuzz (missing from the Mac's default library path; Homebrew's needed `DYLD_FALLBACK_LIBRARY_PATH`) |

The pages look alike: the template, not the engine, decides the look. Typst is chosen for three reasons:

* It gives the same bytes on a laptop, in CI and in the worker image, so golden files written anywhere
  hold everywhere.
* It is ten times faster.
* It has no system dependencies.

The 22 % size difference is in the images and is closed by the image settings. The font is
bundled with the code (an OFL font), with `ignore_system_fonts`.

The bake-off also found two things the report model must handle:

* Unmeasured site items print as "0.0000 SF, 0.00". They must read "awaiting measurement" and stay
  out of the totals' denominators.
* One landscaping row carries 58,047 SF, the size of a whole site, which needs checking against the
  takeoff.

### 11.9 How it is checked

* **Model.** Golden JSON of `StudyReport` for a synthetic study (`tests/fixtures/synthetic/`), plus
  invariant tests with hand-computed cases: ties to the cent, rounding residue, a look-back, a
  residential versus nonresidential building, a land method from each source, a missing report input.
* **Schedule.** Totals tie; every row's method, convention and life follows Pub 946 Table 4-1 (property
  tests over every class); the bonus default for acquisition dates either side of 2025-01-20 and 2024;
  description limits; CSV and XLSX carry the same rows.
* **PDF.**
  * Byte identity across two renders.
  * Text extraction finds every summary figure on its page.
  * Every schedule line appears exactly once.
  * Page-image goldens on the synthetic study.
  * Fonts are embedded.
* **Client package, private.** The PDF rendered from run 4's export is put side by side with the
  engineer's 12-page report: the class totals and the category comparison. Its layout is reviewed by
  Bryan and, if possible, by the design partner.
* **Regression.** The export JSON stays byte-identical for the same revision. The report is an addition.

### 11.10 Decisions needed before building

1. **Site visit.** Who takes the photographs in Basis's workflow: the engineer on site, or the taxpayer?
   The certification and C.3 must say which. The ATG says a quality study includes a site visit.
2. **Signature.** Does the engineer sign inside Basis (an approval that stamps name, credentials and time
   onto the final PDF), or download, sign and upload? The first fits the existing approval gate.
3. **Whose report.** The firm's letterhead with "prepared using Basis" in the methodology, or Basis's
   name on the cover?
4. **Item detail.** All 384 items in the PDF (about 12 landscape pages), or the category summary in the
   PDF and every item in the schedule files and an appendix?
5. **Bonus.** Print the statutory default rate with its source and the dates, or only the dates and
   eligibility, leaving the rate entirely to the preparer?
6. **Preparer format.** Which fixed-asset software do the design partner's CPAs use? A template from them
   decides the first vendor mapping.

### 11.11 Decided (Bryan, 2026-10-31)

1. **Site visit:** either. Each study records who took the photographs and when, and the report states it
   (an engineer's site visit, or photographs supplied by the taxpayer and no visit).
2. **Signature:** inside Basis. The engineer's final approval stamps name, credentials and time on the final
   PDF; before it the PDF is a DRAFT.
3. **Item detail:** the category summary in the body, every item in a landscape appendix, and the schedule
   files.
4. **Bonus:** the statutory default rate from the acquisition date, with its source, labeled as the
   preparer's election.
5. **Whose report (default, not asked):** the firm's letterhead, and "prepared using Basis" in the
   methodology.
6. **Preparer format (default, not asked):** the generic Form 4562 schedule (11.5) in CSV and XLSX until a CPA
   partner sends a vendor template.

### 11.12 As built (2026-10-31)

| Part | Where | Notes |
|---|---|---|
| Report model | `basis/report/model.py` | as 11.3 and 11.4 |
| Schedule | `basis/report/schedule.py` | as 11.5 |
| Tax terms | `basis/report/tax.py` | Pub 946 Table 4-1 and B-1; the bonus default keyed on the acquisition date, with the 2017-09-27 and 2025-01-19 boundaries tested on both sides |
| Exhibit A | `basis/report/exhibit.py` | mask edges drawn with Pillow, a box when there is no mask, numbered badges; captions give the report lines per label run |
| Render | `basis/report/render.py`, `templates/report.typ` | |
| Export step | `basis/activities/export_report.py` | |
| Route | `GET /v1/studies/{id}/report` | `docs/api/REPORT.md` |

Differences from the design:

* **Systems without a priced line** (`not_produced`) are disclosed in the limiting conditions.
  They do not stop the report: the report stops only when the allocation itself was
  withheld. Section 11.8's wording read `not_produced` as "withheld"; the result defines it
  as the unpriced systems.
* **Quantities measured once for the whole property** fold into one line, "Whole property",
  with every room the item was seen in: the site-wide landscaping and the roof, gutters and
  siding. On run 4 this took the schedule from 295 to 263 lines and removed every
  zero-valued line.
* **Typical unit costs.** 59 of run 4's priced lines come from the typical unit costs, not
  an RSMeans line. The schedule prints "typical unit cost" in their MasterFormat column
  instead of leaving it blank.
* **The structured `masterformat` field on the cost trail (11.4)** is not added. The line
  numbers are read from the cost source in one tested function (`masterformat_of`). The
  recorded trails cannot be rewritten (migration 0018), so a field would cover only new
  revisions.
* **Report inputs and signing (11.6, 11.11 items 1-2) are not in this change.** Every
  report is a DRAFT with marked placeholders until they land. `basis/report/inputs.py`
  defines the shapes the next change stores.

Measured on run 4's export (private copy; EVAL_REPORT section 51):

* 263 lines and 89 items awaiting a measurement;
* 67 categories (the export had 186 free-text names); 89 distinct item descriptions;
* every total ties to the cent;
* 52 photographs composed in 1.0 s; the PDF rendered in 0.2 s, 45 pages, 3.7 MB.

### 11.13 Report inputs and signing: design (2026-10-31)

Builds 11.6 and 11.11 items 1 and 2: the firm profile, the per-study answers, and the engineer's
signature inside Basis, which turns the DRAFT into the final report.

**What a valid electronic signature needs.** ESIGN (15 U.S.C. 7001 et seq.) and UETA give an electronic
signature the effect of a handwritten one. Practice and the statute summaries reduce what has to be shown
to four things:

1. intent: an affirmative act to sign;
2. attribution: the act of an identified person;
3. association: the signature is logically attached to the record it signs;
4. retention: the record can be reproduced later.

Neither statute requires a hash or an audit trail. Signing services use both as their evidence. Basis
maps the four like this:

| Element | In Basis |
|---|---|
| Intent | `POST .../report/sign` with `attest: true` and the certification's `statement_version`. The client shows the certification text and a checkbox. |
| Attribution | the signed-in user, who must hold the engineer or owner role (authenticated by Firebase), with uid, email and the name and credentials the engineer attests to |
| Association | the signature row records the sha256 of the export, of the reviewed preview and of the signed PDF. The PDF prints the signature id, time, name and credentials. |
| Retention | the signed PDF is stored at its own path and never overwritten. The signature row cannot be updated (`forbid_mutation()`), and a `report.signed` event goes in the timeline. |

**Out of scope, stated rather than implied:**
- A PAdES certificate signature inside the PDF.
- A PE seal. State engineering boards govern seals, a cost segregation report is not a sealed design
  document, and no source we found requires a seal on one.

A firm that needs either can still sign outside Basis. The certification says "Signed in Basis", not
"sealed".

**Storage** (migration 0020):
- `firm_profiles`:
  - one row per firm (`org_id` primary key);
  - `profile` holds the `FirmProfile` document, with the logo as base64 at most 512 KB;
  - `version` is the entity tag, as for the intake;
  - also `updated_by` and `updated_at`.
- `studies.report_answers` (`StudyAnswers`) and `studies.report_answers_version`, beside the intake and
  versioned the same way.
- `report_signatures`:
  - columns: `id`, `study_id`, `revision`, `signed_by`, `signed_at`, `engineer` (the attested name and
    credentials), `statement_version`, `export_sha256`, `reviewed_sha256`, `pdf_uri`, `pdf_sha256`;
  - unique on `(study_id, revision)`: one signature per revision;
  - updates refused by `forbid_mutation()`. Deletes stay allowed so a purge can remove a study.

**API:**

| Route | Role | What |
|---|---|---|
| `GET /v1/orgs/{org_id}/profile` | member | the profile and its `ETag`. 404 when there is none yet. |
| `PUT /v1/orgs/{org_id}/profile` | owner | the whole profile. `If-Match` as on the intake. |
| `GET /v1/studies/{id}/report-answers` | viewer | the answers and their `ETag` |
| `PUT /v1/studies/{id}/report-answers` | engineer | the whole answers document. `If-Match`. |
| `GET /v1/studies/{id}/report/state` | viewer | the latest exported revision, whether its report was skipped, what a final report still lacks, and the signature, if any |
| `GET /v1/studies/{id}/report/preview` | viewer | the current revision's report rendered now, with the stored inputs and no signature. `ETag` is the sha256 of the bytes. |
| `POST /v1/studies/{id}/report/sign` | engineer | signs the current revision (below) |
| `GET /v1/studies/{id}/report` | viewer | as before, but `format=pdf` serves the signed PDF when the revision is signed (`variant=draft` asks for the draft) |

**Signing** checks, in order, and writes nothing unless all pass:
1. The study is not archived.
2. The current revision was exported and its report was not skipped (409 `report-not-exported`).
3. The firm profile and the study answers exist (409 `report-inputs-missing`, naming the missing ones).
4. The revision is not already signed (409 `report-already-signed`).
5. The preview rendered now, from the same export and the same inputs, has the sha256 the engineer
   reviewed (412 `report-changed`). Rendering is deterministic (11.8.1), so this proves the signer saw
   exactly this report: the same text and figures, without the signature block.

Then Basis renders the final PDF with the signature, stores `rev-<n>.report.signed.pdf`, and inserts the
signature row and the `report.signed` event in one transaction.

A new revision, from reopening the study, needs its own signature; the old one stays with its revision.
The schedule files carry no signature and are unchanged.

**The export step** reads the stored inputs, so the draft it writes already carries the firm and the
study's answers when they exist.

**Client (`web/live`):** a Report view for an exported study. It has:
- the status, and what a final report still lacks;
- the study answers form (engineers);
- the firm profile form (owners; read-only for others);
- preview and download links for the PDF, CSV and XLSX;
- a Sign panel: name, credentials and license prefilled from the firm profile, the certification text,
  a checkbox, then Sign. Sign is only enabled once the preview has been opened, because its `ETag` is the
  `reviewed_sha256`.

#### 11.13.1 As built (2026-10-31)

As designed above, with these specifics:

* **Code:**
  * `basis/db/repositories/report.py` holds the profile, answers and signature writes.
  * `basis/api/routes/report.py` and `basis/api/schemas/report.py` hold the routes.
  * The client is `web/live/report.js` (the rules, tested with node) and `web/live/live-report.js`.
  * The error codes are BASIS-REPORT-007 to 013. The API is documented in `docs/api/REPORT.md`.
* **Signed PDF path.** It is `rev-<n>.report.signed.<signature id>.pdf`, one path per signature. A write
  whose transaction then fails leaves an orphan file; it never puts a second file at a signed path.
* **Already signed.** A second signing attempt is refused before anything is rendered or stored. The
  unique key on `(study_id, revision)` backs the check.
* **The study row lock.** Signing holds it while it renders twice (about 2 s on run 4), so a
  concurrent signing waits, then is refused.
* **Signature time.** `signed_at` is the database's transaction time. The PDF prints it in UTC.
* **A changed report.** The client reloads the inputs and asks for the preview again; Sign stays
  disabled until the preview is opened.

Checked on a copy of the dev database migrated to 0020 (run 4, 52 photographs; the copy, not the dev
database itself):
- The firm profile and the study details were added in the app.
- The preview opened: 45 pages, 3.7 MB, 0.8 to 1.0 s, byte-identical twice.
- Signing with the box unchecked was refused in the client.
- The study details were changed through the API while the sign dialog was open. Signing was then
  refused with 412 BASIS-REPORT-010, and Sign was disabled again.
- After the preview was opened again, the report was signed.
- The signed PDF has no DRAFT mark. Its certification states the photographs the taxpayer supplied,
  and the signature block shows the time, the id, the name, credentials and license, and the firm.
- `report.signed` is in the timeline with the signer's email.
