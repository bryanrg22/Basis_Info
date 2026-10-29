# Basis and land allocation in a residential cost segregation: what the standard says (2026-10-29)

Data-free. What practitioners do, what the Code, the regulations, the IRS publications and the
Cost Segregation Audit Techniques Guide (Publication 5653, rev. 2-6-2025; "ATG") say, and what
`basis/domain/basis.py` therefore does. Page numbers are the ATG's printed page numbers.

## 1. The chain

```
depreciable basis = purchase price + capitalized acquisition costs − land
```

* **Lump-sum purchases are apportioned by fair market value.** Reg. §1.167(a)-5: for property
  acquired for a lump sum (buildings and land), "the basis for depreciation cannot exceed an amount
  which bears the same proportion to the lump sum" as the depreciable property's value at
  acquisition bears to the whole. Publication 551 and Publication 527 say the same in plain words:
  allocate the cost by the ratio of each asset's FMV to the FMV of the whole; when the FMVs are
  uncertain, "you can allocate the basis based on their assessed values for real estate tax
  purposes" (Pub. 551, "Allocating the Basis"). The ATG repeats the regulation (p.64, Real Estate
  Allocations).
* **Land is valued first, at highest and best use.** ATG p.37: "Land value is always determined first
  and is based on 'highest and best use'. ... The balance of the purchase price is then allocated to
  the building and to other assets based on their value as of the date of purchase." ATG p.67 (the
  examiner's checklist): "The appraisal should indicate the value of the land and improvements
  separately. Land included in the purchase price is valued first."
* **Reconciliation to the total is required.** ATG ch. 4 C.10 (p.40): "A quality study always
  reconciles total allocated costs to total actual costs", with one estimating technique on all the
  items that reconcile to the purchase price.

## 2. Land: the methods and the ATG's position on each

| Method | Standing | Source |
|---|---|---|
| The appraisal's opinion of site value (the cost approach's "opinion of site value"), as of a date close to the purchase | preferred: an opinion of FMV by a qualified appraiser, land valued first at highest and best use | ATG p.37, p.64 ("any opinion of value should be developed and reported by appropriate standards of the professional appraisal practice (typically USPAP)"), p.67 |
| A land appraisal or land comparables | acceptable: the same standard of value, a second source when the cost approach was not developed | ATG p.64 |
| The assessor's land-to-total ratio applied to the price | a cross-check, not a basis: "Examiners should also be wary if a cost segregation study relies solely on local assessed values rather than appropriately determining fair market values" (ATG p.64). Pub. 551 allows assessed values only when the FMVs are uncertain | ATG p.64; Pub. 551 |
| A flat percentage of the price by market or rule of thumb | the design partner's practice on the client package; undocumented, it is the ATG's "rule of thumb" approach, which "lacks sufficient documentation to support its allocation" (ch. 3 C.6, p.32). Accepted only with its source recorded, and flagged | ATG p.32 |
| Residual: price minus the estimated value of the improvements | never: "it is inappropriate to estimate the value of the land by subtracting the estimated value of the improvements from the lump real estate price" (ATG p.64) | ATG p.64 |

So the hierarchy in `STUDY_REPORT_SPEC.md` section 5.2 is the ATG's. The checks that go with it:
land share of the price inside a plausible band (the spec's 5–60 %); the appraisal's effective
date within 12 months of the acquisition (value "as of the date of purchase", ATG p.37); the chosen
land against the appraisal's site value when both exist (the engineer's flat percentage on the
client package differs from the appraiser's site value by about 4 %); the assessor ratio against
the chosen land when assessed values are given.

## 3. Capitalized acquisition costs

Reg. §1.263(a)-2(f)(1): "a taxpayer must capitalize amounts paid to facilitate the acquisition of
real or personal property." Paragraph (f)(2)(ii) lists the inherently facilitative amounts:
appraisal ("securing an appraisal or determining the value or price of property"), negotiating and
tax advice on the acquisition, application and bidding fees, preparing and reviewing the documents
that effectuate the acquisition, "examining and evaluating the title of property", regulatory
approvals and permits, "conveying property between the parties, including sales and transfer taxes,
and title registration costs", finders' fees and brokers' commissions, and "architectural,
geological, survey, engineering, environmental, or inspection services pertaining to particular
properties". For real property, costs of deciding whether and which property to buy do not
facilitate the acquisition (f)(2)(iii)(A) and are not capitalized.

Publication 551 ("Settlement costs") gives the practitioner's list. Included in basis: abstract
fees, charges for installing utility services, legal fees (title search, sales contract, deed),
recording fees, surveys, transfer taxes, owner's title insurance, and amounts the seller owed that
the buyer agrees to pay (back taxes or interest, recording or mortgage fees, charges for
improvements or repairs, sales commissions). Not included: "the fees and costs for getting a loan on
property": points (discount points, loan origination fees), mortgage insurance premiums, loan
assumption fees, the cost of a credit report, "fees for an appraisal required by a lender"; casualty
insurance premiums; rent and utilities for occupancy before closing; refinancing fees; and
"amounts placed in escrow for the future payment of items such as taxes and insurance". Points are
prepaid interest, deducted over the loan's term (Pub. 527). Real estate taxes for the buyer's part
of the year are deductible under §164 (apportioned by Reg. §1.164-6), not basis.

The appraisal fee is the one item the two sources split: an appraisal the buyer commissions to set
the price is facilitative (Reg. §1.263(a)-2(f)(2)(ii)(B)); an appraisal the lender requires is a
loan cost (Pub. 551). The intake therefore has two categories, `appraisal_buyer` and
`appraisal_lender`, and refuses the second.

How the capitalized costs are spread over the asset classes: the ATG treats acquisition-wide
indirect costs as allocated pro rata ("This allocation is normally done on a pro rata basis for
indirect costs applicable to the entire project", ch. 3 p.29; ch. 4 C.11, p.40–41: "Indirect costs
must be allocated proportionately to the basis of the specific assets to which they relate";
"A quality study explains the purpose of each indirect cost and describes its allocation"). Costs
that relate only to land (surveying and sub-dividing land, general grading) go to land only
(p.40). Title, recording, legal and transfer costs relate to the whole conveyance and are spread pro
rata over every class, which is what the design partner's 2×5 allocation block does.

## 4. 27.5 or 39 years

* §168(c): residential rental property 27.5 years, nonresidential real property 39 years; both
  straight line, mid-month convention (§168(d)(2); Pub. 527).
* §168(e)(2)(A): residential rental property is "any building or structure if 80 percent or more
  of the gross rental income from such building or structure" is "rental income from dwelling
  units". A dwelling unit is a house or apartment used to provide living accommodations, but not "a
  unit in a hotel, motel, or other establishment more than one-half of the units in which are used
  on a transient basis". §168(e)(2)(B): nonresidential real property is §1250 property that is not
  residential rental property (and not property with a class life under 27.5 years). ATG p.86
  restates both.
* A single-family house let to tenants is residential rental (27.5). A bed-and-breakfast, inn or a
  house let by the night as transient lodging fails the dwelling-unit definition, so its building is
  nonresidential (39), and the ATG's lodging matrices (ch. 7) then govern the §1245 items. That is
  why the design partner's client study carries a 39-year building: the use is the taxpayer's answer
  (the appraisal only says "short-term rental potential"), so it is an intake question, never
  inferred from photos or the appraisal.
* The 80 % test is a per-tax-year income test. The intake records the taxpayer's expected share of
  gross rental income from dwelling units and whether the units are let on a transient basis; the
  worksheet turns them into the recovery period and refuses `residential_rental` when the test
  fails.

## 5. Placed in service

Depreciation starts when the property is placed in service, not when it is bought or appraised.
Reg. §1.167(a)-11(e)(1)(i): property is first placed in service when it is "first placed in a
condition or state of readiness and availability for a specifically assigned function". Pub. 527:
"You place property in service in a rental activity when it is ready and available for a specific
use in that activity", with the example of a house ready for rent in July and rented in September,
placed in service in July. The mid-month convention then takes half a month in the month placed in
service (§168(d)(2)). The acquisition (contract or closing) date is a different date: it fixes the
bonus depreciation rate (§168(k); ATG ch. 6 H.5, H.7) and the cost year for the estimate. The intake
takes both; the placed-in-service date is required because the result is unusable without it, the
acquisition date is optional (the appraisal's contract date corroborates it).

## 6. From the RCN schedule to the basis

The ATG expects a cost segregation of an acquired building to estimate replacement cost new per
item and then depreciate each for its own age and condition before reconciling to the price: "These
estimated property costs (replacement cost new (RCN)), which include indirect costs, are then
adjusted for the asset's age and condition at the acquisition date ... These adjustments are
generally expected to be different among the various items of acquired property" (ch. 3 A(6),
p.27). It objects to a uniform reconciling factor applied only to make the estimates land on the
price: "It is not an appropriate cost estimation methodology to apply a pro-rata step-up factor to
the individual costs estimated for the various improvements and personal property merely to
reconcile to the total purchase price" (ch. 3 D(3), p.32), and "Applying a pro-rata step-up factor
to the improvements and personal property is not an acceptable method of establishing the
depreciable basis of the acquired items" (D(5), p.33). The passage describes a step-up (estimates
below the price); the objection is to the uniform factor hiding whatever the estimates got wrong
("is the difference attributable to items of depreciable property or to land?", p.33), and the
listed causes (p.33: a wrong land FMV, missed items, wrong cost factors, wrong depreciation factors,
indirect costs) apply in both directions.

The practice on the client package (the design partner's study, `OUTPUT_REPORT_ANATOMY.md` in the
private eval folder) is the simpler interim method of `STUDY_REPORT_SPEC.md` section 5.4: one factor
`f = T / ΣRCN` on every row, so the rows reconcile to `T` exactly, bucket totals and percentages
follow, and `B` and `CC` are split pro rata per bucket. `basis/domain/basis.py` implements that
method exactly because it is what the report must reproduce, and records the ATG's objection as a
check on every result: `uniform_factor` is a warning when `f < 1` (a uniform write-down in place of
per-item depreciation, A(6)) and an error when `f > 1` (the rejected step-up: the schedule is
missing items, D(3)–(5)). Per-class depreciation before reconciliation is the next step and needs
the appraisal's effective age, condition and remaining economic life per class (spec section 3).

The arithmetic conventions (every number `Decimal`, rounding `ROUND_HALF_UP` to the cent, the
largest-remainder rule for the residual cents so that every printed column sums to its total) are
stated in the module docstring and tested with hand-computed cases.

## 7. Sources

* IRS Publication 5653, Cost Segregation Audit Techniques Guide (rev. 2-6-2025): ch. 3 A(6) p.27,
  C.6 p.32, D(3)–(6) pp.32–33, p.37; ch. 4 C.10–C.11 pp.40–41; ch. 5/6 Real Estate Allocations p.64,
  examiner checklist p.67; recovery periods p.86; bonus depreciation ch. 6 H.
* 26 U.S.C. §168(c), (d)(2), (e)(2)(A)–(B), (k).
* 26 CFR §1.167(a)-5 (apportionment of basis); §1.167(a)-11(e)(1)(i) (placed in service);
  §1.263(a)-2(f) (amounts paid to facilitate an acquisition); §1.164-6 (apportionment of taxes).
* IRS Publication 551, Basis of Assets: "Settlement costs", "Allocating the Basis".
* IRS Publication 527, Residential Rental Property: placed in service, points, dividing basis between
  land and buildings, dwelling units, the 80 % test, the mid-month convention.
* Rev. Proc. 87-56 (asset classes; 57.0 for the §1245 property of residential rental property).
