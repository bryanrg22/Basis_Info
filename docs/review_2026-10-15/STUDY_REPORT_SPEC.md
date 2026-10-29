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
