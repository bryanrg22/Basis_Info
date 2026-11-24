# Renovations: a component's own age, a renovation's own schedule, and what it removed (2026-11-23)

Data-free. Measured in EVAL_REPORT section 102. API: `docs/api/RENOVATIONS.md`, the corrections
in `docs/api/OVERRIDES.md` section 7, the result in `docs/api/STUDY_RESULT.md` "Renovations",
the report in `docs/api/REPORT.md` "Renovations".

**Question.** Basis studied a property as one thing bought on one date. Real properties are
renovated: by the seller before the sale (the buyer bought a newer kitchen), by the buyer before
placing the property in service (bought, renovated, then rented), and by the buyer after it was
in service (a remodel two years later, which also throws away components the acquisition study
priced). What does the standard ask for in each case, what did Basis do, and what is built?

Code: `basis/domain/component_age.py`, `basis/domain/renovations.py`, `basis/evidence/renovations.py`,
`basis/report/macrs.py`, `basis/activities/line_annotations.py`, `basis/api/routes/renovations.py`,
migration `0039_study_renovations`.

## 1. The three cases

| Case | What the taxpayer placed in service | Dates | Dispositions |
|---|---|---|---|
| The seller renovated before the sale | the property as bought, some parts newer than others | one: the buyer's | none for the buyer |
| The buyer renovated before placing it in service | the property as renovated | one: when it was ready for use | none (nothing replaced was ever in the buyer's service) |
| The buyer renovated after placing it in service | the building, then the renovation as a separate asset | the building's, then the renovation's own | the components the renovation removed |

## 2. The standard, with its sources

Quotes were checked against the documents on 2026-11-23 (text extracted from the IRS PDFs;
Treas. Reg. 1.168(i)-8 from Cornell LII).

**A renovation after the building is in service is its own asset.** Treas. Reg.
1.168(i)-8(c)(4)(ii)(D): "If the taxpayer places in service an improvement or addition to an
asset after the taxpayer placed the asset in service, ... the improvement or addition and, if
applicable, its structural components are a separate asset." The cost segregation guide (IRS
Publication 5653, rev. 2-2025, "ATG") treats "(4) Newly constructed property, which includes
remodels of existing properties and additions to existing properties" as its own case (p.26),
priced from actual costs: "Construction documentation, such as construction drawings,
specifications, contracts, job reports, change orders, payment requests, and vendor and supplier
invoices, are used to determine unit costs" (p.28); estimates only to split a lump sum:
"Estimates should be reconciled to an actual cost, either to a line-item cost or to an
individual system cost" (p.30). Dates: "Request copies of the building permit and certificate of
occupancy (C of O), which can assist in establishing the construction start date and the placed
in service date" (p.58). Evidence: "Request any available photographs that document the
condition of the property prior to the" construction (p.28).

**Qualified improvement property (QIP).** ATG p.87: "QIP is defined as any improvement to an
interior portion of a building if the improvement is placed in service after the date the
building was first placed in service. However, QIP does not include any improvement attributable
to the enlargement of the building, any elevator or escalator, or the internal structural
framework of the building." It is limited to nonresidential real property (p.156) and to
improvements "made by the taxpayer" (p.87, CARES Act). ATG p.157: "The recovery period for QIP
placed in service after December 31, 2017, was reduced from 39 years to 15 years for GDS"; ADS 20
years (p.87). Pub 946 (2025) Table A-8 prints the 15-year straight-line, half-year rates.

**Bonus depreciation keys on the renovation's own acquisition date.** Notice 2026-11 section
2.02(1): OBBBA section 70301 provides "a permanent 100 percent additional first year
depreciation deduction for qualified property acquired and placed in service ... after January
19, 2025"; section 3.03: whether property is "acquired after January 19, 2025" follows rules
consistent with Reg. 1.168(k)-2(b)(5): the written binding contract's date, and for
self-constructed property when "physical work of a significant nature begins" (section
2.03(2)(c)). An election to deduct 40 percent instead applies to property placed in service in the
first taxable year ending after January 19, 2025 (section 2.02(1)).

**A removed component is a disposition; a portion of the building needs an election.** Reg.
1.168(i)-8(d)(1): "a disposition includes a disposition of a portion of an asset only if the
taxpayer makes the election"; (d)(2)(ii): the election is made "on the taxpayer's timely filed,
including extensions, original Federal tax return" for the year of the disposition and "may not
be made through the filing of an application for change in accounting method" (except the
examination case of (d)(2)(iii)). The unadjusted basis of the disposed portion: "the taxpayer
may use any reasonable method", including "A study allocating the cost of the asset to its
individual components" ((f)(3)(i)(C)). The IRS Practice Unit DCE-P-002 ("Examining a Taxpayer
Electing a Partial Disposition of a Building", last update 1/16/2025) adds what an examiner
checks: "Relying on capitalization does not substantiate that a disposition occurred" (p.8); "An
addition to a building does not substantiate that a disposition occurred" (p.8); "A partial
disposition cannot occur before the building is PIS" (p.9); "the unadjusted basis of a portion of
an asset may not exceed the unadjusted basis remaining in the entire asset" (p.21); the
depreciation allowed or allowable is computed with the parent's "Depreciation method, Recovery
period, and Convention ... Including the portion of any additional first-year depreciation
deduction that is attributable to the disposed portion" (p.22); and a used building's replaced
component "was not new when the building was PIS", so its basis is reduced "to take into account
the age and condition" (p.21, Example 2). Its worked example (p.23): a $150,000 elevator of a
39-year building placed in service 7/1/2011, disposed of 6/30/2014: "the depreciation allowed
on the elevator disposed of is $11,220", adjusted basis $138,780.

**Depreciation in the year of disposition.** Pub 946 (2025), chapter 4: "For property for which
you used a half-year convention, the depreciation deduction for the year of the disposition is
half the depreciation determined for the full year"; under the mid-month convention the year's
depreciation is multiplied by the months in service, the month of disposition counting as half,
over 12 (its example: $100,000 of residential rental property placed in service July 2, 2023 and
sold March 2, 2025: "Multiply $3,636 by the fraction, 2.5 over 12, to get your 2025 depreciation
deduction of $757.50"). Property "placed in service and disposed of in the same year" cannot be
depreciated ("Excepted Property"). "Figuring MACRS deductions without using the tables will
generally result in a slightly different amount than using the tables", which is why the
dispositions use the tables (section 4.4).

**The seller's renovation: depreciation by condition.** ATG p.38: "a quality study always
accounts for the physical deterioration and functional obsolescence of assets"; p.67: "Older
items may be physically deteriorated or functionally or economically obsolete and should be
assigned a value commensurate with their condition or use", and "The value of used components
must be reduced from the replacement cost new value in proportion to the observed economic
obsolescence or physical depreciation as compared to similar new assets." The UAD 3.6 appraisal
records, per room, an update status (`RoomUpdateStatusType`: FullyUpdated, PartiallyUpdated,
NotUpdated) and its time frame (`RoomUpdatedTimeframeType`: LessThanOneYear, OneToFiveYears,
FiveToTenYears, TenOrMoreYears; GSE URAR Delivery Specification Appendix A-1, unique IDs
0700.0036 and 0700.0034), with overall statuses for the bathrooms and the flooring.

## 3. What Basis did before

* One acquisition, one placed-in-service date (`domain/intake.py`), every schedule line with the
  same dates (`report/model.py`), the bonus default from the one acquisition date
  (`report/tax.py`).
* One depreciation rate for every row, the appraiser's for the improvements as a whole
  (`domain/structure.py` `depreciation`), with the check `depreciation_uniform` saying a
  per-class age-life "needs the ages of finishes and equipment".
* An engineer could remove an inventory row (`domain/removals.py`), which is a correction of the
  inventory, not a tax disposition. Nothing modelled partial dispositions, QIP, or a second
  schedule; an engineer's unit price from an invoice was still scaled by the purchase-price
  factor, so invoice-priced renovation rows inside a study would have been wrong.

## 4. The design

### 4.1 The seller's renovation: a row's own age

An `asset` correction at the assets review carries a row's `effective_age` or `year_installed`,
its `economic_life`, the `age_source` (engineer_inspection, appraisal_update_status,
aerial_history, permit, invoice, taxpayer_statement) and `age_source_detail`, with the
correction's reason (`docs/api/OVERRIDES.md` section 7). The row's physical depreciation becomes
the age-life ratio on its own life, `age / life` to six places (the guide's age-life method,
which `structure.depreciation` already uses for the building as a whole); every other row keeps
the appraiser's rate; the rows are depreciated one by one and foot to the cent
(`structure.depreciate_by_row`). A year installed is an age as of the acquisition's
placed-in-service year. The reconciliation and its 10 % test are unchanged: they now compare a
schedule that carries the newer components at their condition.

Granularity: a row, not a system or a room. The rows are what the engineer reviews and corrects,
the correction mechanism already carries row decisions, and a room-level age would still have to
say which of the room's rows it covers (the UAD "PartiallyUpdated" status says some, not which).

The UAD 3.6 statuses become a suggestion only (`component_age.suggestion`: a range of ages for a
room's finishes, or for some of its components), never applied. **Not built:** the extraction
does not yet store the two room fields (the generated catalogue map has no key for them), so no
suggestion reaches the engineer yet (section 7).

Judgement in it: which components were renewed, their age and economic life (the engineer's).
Note: the appraiser's rate is for the improvements as a whole; when a few rows become newer, the
remaining rows arguably carry more than the blended rate. Basis does not redistribute it: the
reconciliation shows the effect, and the engineer can age the older rows too.

### 4.2 The renovation projects document

`studies.renovations` (migration 0039) is one versioned document per study, `PUT
/v1/studies/{id}/renovations` with `If-Match` like the intake. Not in the intake: the intake locks
when the study starts, and the photographs' tags and the invoices are known later. A project:
`key`, `kind` (`after_service` or `before_service`), `scope`, `addition` (and `removals_stated`),
`contract_date`, `work_start_date`, `placed_in_service_date`, `invoice_total`, `invoices`
(uploaded documents with amounts), the CPA's `capitalized_amount` with `capitalized_confirmed_by`,
`after_photos`, `qip` (`auto` or `off`) and `aerial` (section 5). The document also carries the
bonus the CPA says was claimed on the acquisition (`acquisition_bonus_rate`) and exterior
components' dated views.

Validations: dates in order (placed in service not before work start or the contract), invoices
sum to the total, the capitalized amount not above it and confirmed by someone, no photograph or
invoice in two projects, every file a document of the study (invoices of kind `other`, photographs
of kind `photo`), no after-service project placed in service on or before the building, none
started before it, no before-service project started before the acquisition or after the
building's date. The document is refused once the current revision is exported (reopen to change
it), because the export, the report and the schedule were computed with it.

### 4.3 The buyer's renovation after service: its own schedule

`process_assets` reads the document when it builds the assets: a component of a photograph tagged
after a project belongs to the project (a room-level row when every photograph of its room is
tagged after the same project; a room that mixes tags keeps its room-level rows in the
acquisition, flagged `renovation:ROOM_MIXED`). Every takeoff records the version
(`renovations_version`) and a project's row its key (`renovation_project`). A project's rows are
priced from the cost table like any row and **left out of the structure model's carve**: the
concealed rows are the building the taxpayer bought.

The result (`basis/evidence/renovations.py`) leaves the project rows out of the acquisition
entirely (its reconciliation and allocation are what they would be without them) and gives each
after-service project a schedule: the CPA's capitalized amount split across the project's rows in
proportion to their estimates, `capitalized / sum of estimates` being the project's own factor
(never the purchase price's), with the acquisition's largest-remainder rounding. The estimates
only apportion: a gap above 10 % between them and the actual cost is a warning (a component of
the work may be missing from the photographs), never a withheld result. When the document
changes after the assets were built, the result is withheld (`renovations_changed`) until a
reprice rebuilds them.

QIP: a project row whose rules classification is 39-year (a nonresidential building component)
prints as qualified improvement property (15-year straight line, half-year, ADS 20) when it is an
interior portion (the structure model's interiors, specialties, mechanical and electrical, less
the exterior condenser, outdoor lighting and gutters) and none of the exclusions applies: the
project is an addition (enlargement), the label is an elevator or escalator, the row is the
structural framing or the foundation (internal structural framework). Residential rental
property is never QIP. `qip: off` is the CPA's override. A mechanical row outside the building
(a rooftop unit) is the CPA's to confirm.

The bonus default of a project follows its own contract date, else its work-start date
(`tax.project_bonus_default`), through the existing `bonus_default`: 100 % for property acquired
after 2025-01-19, the phase-down otherwise. Nothing in `bonus_default` changed.

### 4.4 Dispositions

The engineer marks an acquisition line removed by a project with an `asset` correction
(`removed_by_project`, `disposition_date`, optional `removed_share`; OVERRIDES section 7). A
correction rather than a list of asset ids in the document: asset ids are per revision, and a
correction is how every decision on a line is carried from one revision to the next (section
4.6). The line stays in the acquisition (it was bought); the disposition takes it out at its
date.

For each, the result gives: the parent (`building` for a 27.5- or 39-year line, its structural
components being the building's; `own_asset` for a 5, 7 or 15-year line) and its placed-in-service
date; the unadjusted basis, the line's allocated amount in the acquisition study times the share
(Reg. 1.168(i)-8(f)(3)(i)(C), "a study allocating the cost of the asset to its individual
components"); the depreciation allowed or allowable to the disposition date under the line's own
method, life and convention with its share of the bonus (`basis/report/macrs.py`: the Pub 946
(2025) tables A-1, A-6, A-7a and A-8, the year of disposition at half a year or by months; the
bonus rate is the CPA's `acquisition_bonus_rate`, else the statutory default for the acquisition's
dates); the adjusted basis left (the loss on an abandonment, (e)(2)); the treatment
(`election_required` for a portion of the building, `whole_asset` for a line that is its own
asset); and the tax year. Dispositions spanning years are totalled by year: each year's election
is made on that year's return.

**The treatment split is an inference, not a sourced rule.** The regulation requires the
election for a portion of an asset; that a 5-year carpet or a 15-year paving line priced as its
own line in the acquisition study is its own asset (so its removal is a disposition of a whole
asset, recognized without the election) follows from treating each schedule line as an asset,
which is how the schedule is filed. The CPA confirms it.

Checks, each a blocker: the project is listed and is after-service; an addition removes nothing
unless the engineer states it (`removals_stated`, the Practice Unit's HVAC example); no
disposition before the building's placed-in-service date; the disposed basis does not exceed the
building's remaining basis; the acquisition's bonus is known (the CPA's rate, or the intake's
acquisition date); a removal is on an acquisition line, not a project's row. A disposition date
outside the project's work is a warning. Mid-quarter is not applied: the schedule prints
half-year and the 40 % test is the preparer's (as `tax.mid_quarter_note` says).

### 4.5 Bought, renovated, then placed in service

A `before_service` project has no date of its own and no dispositions; its photographs are the
property as placed in service, so they are the acquisition's. Its capitalized cost enters the
intake as the acquisition cost line `improvements_before_service` (Reg. 1.263(a)-3), allocated
pro rata over the classes like the closing costs, and added to the reconciliation's target (price
less land plus the improvements), because the photographs show the renovated building. The
result checks that the before-service projects' capitalized amounts equal the intake's lines.
**Simplification:** the pro rata spread gives every row its share of the improvement's cost; a
better method would price the renovated components from the invoices inside the acquisition
(section 7).

### 4.6 Carrying the engineer's line decisions across revisions

A row's age and its removal are written on its takeoff (`takeoffs.inputs["component_age"]`,
`["removal"]`) and carried into every later revision by `basis/activities/line_annotations.py`:
from the latest earlier revision that has assets, a component row by its component's
`carried_from` chain, a concealed row by its label, with the rejection's own corrections on top
(a record replaces, a null clears). Neither makes the asset a manual classification. Measured
in `tests/unit/activities/test_process_assets_renovations.py`: carried through an ordinary
rejection, cleared by a null, a concealed row's by label.

## 5. Evidence and the dated aerial views

A project may carry `aerial` (the last dated view without the work and the first with it, each a
date and a view reference), and the document `exterior_brackets` per exterior component label.
The result warns (`aerial_bracket`) when a project's placed-in-service date or a component's year
installed falls outside its bracket. Views corroborate; they never set a date, a cost or a
disposition. The report's evidence list names the invoices, the count of after photographs and
the dated views "as corroboration only". The property views themselves come from another branch:
nothing here depends on it.

**Not built:** reading a photograph's EXIF date taken as a suggestion for its tag (the originals
keep their EXIF; the model and display copies are stripped, `vision/prep.py`).

## 6. What is people's judgement, and so an input

| Decision | Whose | Where it enters |
|---|---|---|
| Whether an outlay is capitalized (betterment, restoration, adaptation; the safe harbors) | the CPA | `capitalized_amount`, `capitalized_confirmed_by` |
| Whether a component was truly removed, and when | the engineer, with evidence | the `removed_by_project` correction and its reason |
| Whether to make the partial disposition election, and the character of the loss | the CPA | the report says the election is theirs, on the timely original return |
| Election required or whole asset | the CPA confirms Basis's inference | `treatment` |
| The bonus actually claimed on the acquisition | the CPA | `acquisition_bonus_rate` (else the statutory default) |
| A component's effective age and economic life | the engineer | the age correction |
| Which photographs show the finished work | the engineer | `after_photos` |
| QIP for a borderline row | the CPA | `qip: off`, or the row's class at the assets review |
| Section 280B (a whole structure demolished) | the CPA | not modelled |

## 7. Not built

The UAD 3.6 update-status fields in the extraction (the suggestion function exists, nothing
feeds it); the EXIF date-taken suggestion; pricing a before-service renovation's components from
its invoices (the cost line is spread pro rata); the mid-quarter convention; a disposition of a
renovation's own row by a later renovation; the property views of the aerial history (another
branch); a disposition table in the vendor import profiles beyond the schedule rows (the rows
carry each renovation's dates; the dispositions are in the report and the result).
