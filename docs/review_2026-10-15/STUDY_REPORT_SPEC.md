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
  They are added when a CPA partner provides a template; none is guessed.

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
