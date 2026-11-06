// The cost segregation study report (STUDY_REPORT_SPEC section 11). Every value comes from
// data.json, formatted by basis.report.render; this file only places it. Template version 1.
#let d = json("data.json")

#let ink = rgb("#1f2a37")
#let muted = rgb("#5b6675")
#let rule = rgb("#d5dae1")
#let band = rgb("#f2f4f7")
#let accent = rgb("#1d4e89")

#set document(title: "Cost Segregation Study: " + d.property_name, author: d.firm.name)
#set text(font: "IBM Plex Sans", size: 9.5pt, fill: ink, lang: "en")
#set par(justify: false, leading: 0.62em, spacing: 0.9em)
#set heading(numbering: (..n) => if n.pos().len() == 1 { str(n.pos().first()) + "." } else { none })
#show heading.where(level: 1): it => {
  v(10pt)
  block(below: 8pt)[#text(13pt, weight: "semibold", fill: accent)[#it]]
}
#show heading.where(level: 2): it => block(above: 10pt, below: 5pt)[#text(10.5pt, weight: "semibold")[#it.body]]
#set table(stroke: (x, y) => (bottom: 0.5pt + rule), inset: (x: 5pt, y: 4pt))
#let th(..cells) = table.header(..cells.pos().map(c => text(weight: "semibold", size: 8.5pt, fill: muted)[#c]))

#let draft-mark = if d.draft {
  place(center + horizon, rotate(-30deg, text(90pt, weight: "bold", fill: rgb(200, 30, 30, 22))[DRAFT]))
}

#set page(
  paper: "us-letter",
  margin: (x: 0.85in, top: 0.9in, bottom: 0.8in),
  background: draft-mark,
  header: context if counter(page).get().first() > 1 [
    #set text(7.5pt, fill: muted)
    #d.firm.name #h(1fr) Cost segregation study · #d.property_name
    #v(-4pt)
    #line(length: 100%, stroke: 0.4pt + rule)
  ],
  footer: context [
    #set text(7.5pt, fill: muted)
    #if d.draft [DRAFT: not for filing #h(6pt)] Study #d.study_id.slice(0, 8), revision #d.revision
    #h(1fr) Page #counter(page).display() of #counter(page).final().first()
  ],
)

#let kv(rows) = table(
  columns: (34%, 1fr),
  stroke: (x, y) => (bottom: 0.4pt + rule),
  ..rows.map(r => (text(fill: muted)[#r.at(0)], [#r.at(1)])).flatten(),
)
#let note(body) = block(fill: band, inset: 8pt, radius: 3pt, width: 100%)[#set text(8.5pt); #body]

// ---------------------------------------------------------------- cover
#if d.firm.logo { image("logo.png", height: 0.6in) } else { text(11pt, weight: "semibold", fill: accent)[#d.firm.name] }
#v(1.3in)
#text(26pt, weight: "semibold")[Cost Segregation Study]
#v(2pt)
#text(13pt, fill: muted)[#d.property_name]
#v(0.5in)
#kv((
  ("Prepared for", d.taxpayer),
  ("Prepared by", d.engineer.name + ", " + d.engineer.credentials + " · " + d.firm.name),
  ("Study date", d.report_date),
  ("Property use", d.property_use),
  ("Approach", "Detailed engineering cost estimate (IRS Publication 5653, chapter 3, C.2)"),
))
#v(0.4in)
#table(
  columns: (1fr, auto, auto, auto),
  align: (left, right, right, right),
  th([Property class], [IRC §], [Allocated], [Share of basis]),
  ..d.classes.map(c => ([#c.name], [#c.section], [#c.amount], [#c.share])).flatten(),
  table.cell(stroke: none)[*Depreciable basis*], [], [*#d.depreciable*], [*100.0 %*],
  table.cell(stroke: none)[Land (not depreciable)], [], [#d.land], [],
)
#v(6pt)
#text(fill: muted)[Accelerated property (5, 7 and 15-year): *#d.accelerated* (#d.accelerated_share of the depreciable basis).]
#if d.draft {
  v(0.3in)
  note[*Draft.* This report is not final until the engineer signs it in Basis. Missing: #d.missing.join(", ").]
}
#pagebreak()

#outline(title: [Contents], depth: 1, indent: 1em)
#pagebreak()

// ---------------------------------------------------------------- 1
= Summary letter
#d.report_date

#d.taxpayer

Re: Cost segregation study of #d.property_name

We performed a cost segregation study of the property named above to allocate its depreciable basis among the property classes of the Modified Accelerated Cost Recovery System (MACRS). The study uses the detailed engineering cost estimate approach described in IRS Publication 5653, the Cost Segregation Audit Techniques Guide (the "ATG"): every component is identified, quantified and priced from one published cost source, and the estimate is reconciled to the actual cost of the property.

Of the depreciable basis of *#d.depreciable*, *#d.accelerated* (#d.accelerated_share) is personal property and land improvements with recovery periods of 5, 7 and 15 years. The classes, their basis and the depreciation terms are in sections 6 and 8; every item is listed in Appendix 1, and the preparer's schedule accompanies this report as a spreadsheet.

#v(8pt)
#d.engineer.name, #d.engineer.credentials \
#d.firm.name

// ---------------------------------------------------------------- 2
= Property description
#kv(d.facts.map(f => (f.label, f.value)) + (("Use for this study", d.property_use),))
#text(8pt, fill: muted)[Physical facts are taken from the appraisal and checked against the photographs.]

// ---------------------------------------------------------------- 3
= Basis and land allocation
The depreciable basis is the purchase price plus the capitalized acquisition costs, less the value of the land (ATG chapter 4 C.10; land is not depreciable).
#table(
  columns: (1fr, auto, 40%),
  align: (left, right, left),
  th([Line], [Amount], [Source]),
  ..d.basis_lines.map(b => ([#b.label], [#b.amount], text(8pt, fill: muted)[#b.source])).flatten(),
)
Capitalized acquisition costs are indirect costs: they are allocated to the classes in proportion to each class's share (ATG chapter 4 C.11). The general contractor's overhead and profit is part of each unit cost, in proportion to the cost source's building model.

// ---------------------------------------------------------------- 4
= Methodology and engineering procedures
== Approach
The study follows the detailed engineering cost estimate approach. Components visible in photographs were identified and counted by component type and room; quantities come from counts, the appraisal's measurements and the rooms' areas. Building systems that photographs cannot show (foundation, framing, wiring, piping, ductwork) come from the cost source's square-foot model for the building's type, size and quality, and every line is then reconciled to the actual cost (Exhibit B).
== Inspection
#if d.photographs == none [
  [Who photographed the property, and when, is to be entered before the report is final.]
] else if d.photographs.site_visit [
  The property was inspected and photographed on site by #d.photographs.by on #d.photographs.on (#d.photograph_count photographs reviewed).
] else [
  No site visit was made. The analysis relies on #d.photograph_count photographs supplied by #d.photographs.by, taken on #d.photographs.on. The ATG notes that a quality study includes a site visit (chapter 4 C.3); items no photograph shows are estimated (section 7).
]
== Interviews
#if d.interviews == none [[Interviews are to be entered before the report is final.]] else if d.interviews.len() == 0 [None were conducted.] else [
  #table(columns: (auto, auto, auto, 1fr), th([Name], [Role], [Date], [Topics]),
    ..d.interviews.map(i => ([#i.name], [#i.role], [#i.on], [#i.topics])).flatten())
]
== Cost data and adjustments
#table(
  columns: (22%, 1fr, 34%),
  th([Item], [Value], [Source]),
  ..d.methodology.map(m => (text(fill: muted)[#m.label], [#m.value], text(7.5pt, fill: muted)[#m.source])).flatten(),
)
== Reconciliation
The replacement cost new, less physical depreciation, is reconciled to the building basis: status *#d.reconciliation_status*, a difference of #d.gap_share against a tolerance of #d.tolerance. The allocation is the depreciated cost of each line scaled to the basis; no class is stepped up pro rata (ATG chapter 3 D).

// ---------------------------------------------------------------- 5
= Legal analysis
== Section 1245 and section 1250 property
Property is classified by its use and its attachment to the building. Tangible personal property (IRC § 1245) includes property contained in or attached to a building that is not a structural component (Treas. Reg. § 1.48-1(c)). Structural components (Treas. Reg. § 1.48-1(e)(2)) are parts of the building that relate to its operation or maintenance: walls, floors, ceilings, windows and doors, central heating and air conditioning, plumbing and plumbing fixtures, electrical wiring and lighting fixtures, and similar items; they are section 1250 property with the building.

Whether an item attached to the building is personal property follows the factors of _Whiteco Industries, Inc. v. Commissioner_, 65 T.C. 664 (1975): whether it can be moved and is meant to be, whether it is designed or constructed to remain permanently, how much work and time removing it takes, and how much damage removal causes to it and to the building. _Hospital Corporation of America v. Commissioner_, 109 T.C. 21 (1997), confirms that the Investment Tax Credit rules on structural components continue to apply to MACRS. Electrical and plumbing systems serving specific equipment rather than the building's operation are personal property with that equipment.
== Recovery periods
Recovery periods follow the asset classes of Rev. Proc. 87-56 and IRC § 168(e). Each item's class and authority are in Appendix 1; where the ATG's residential and lodging matrices (chapter 7) name an item, the study follows them. #d.property_use.
== Positions taken on this property
Items on which practitioners commonly differ are marked contested in Appendix 1 (#d.contested items); each carries the passage relied on. #if d.conventions_used != "0" [#d.conventions_used items follow the firm's prior rulings for the same item in the same context.]

// ---------------------------------------------------------------- 6
= Summary of property units
#table(
  columns: (1fr, auto, auto, auto),
  align: (left, right, right, right),
  th([Property class], [Items], [Allocated], [Share]),
  ..d.classes.map(c => ([#c.name], [#c.items], [#c.amount], [#c.share])).flatten(),
)
== By category
#table(
  columns: (1fr, auto, auto, auto, auto),
  align: (left, center, right, right, right),
  th([Category], [Class (years)], [Items], [Replacement cost], [Allocated]),
  ..d.categories.map(c => ([#c.name], [#c.class], [#c.items], [#c.rcn], [#c.allocated])).flatten(),
)

// ---------------------------------------------------------------- 7
= Schedule of property units
Every item is listed in Appendix 1 with its location, quantity, CSI MasterFormat line of the cost source, replacement cost, allocated cost, class and asset class. Items marked _estimated_ are building systems no photograph shows; their quantities come from the cost source's building model. Items marked _contested_ are positions on which practitioners differ (section 5). #if d.engineer_prices != "0" [Items marked _engineer's price_ (#d.engineer_prices) are priced at the unit price the engineer set, from the source shown, instead of the cost table; the table's price is kept in the cost trail. ]Section 1245 property is the 5 and 7-year classes.
#if d.awaiting.len() > 0 [
  == Items awaiting a measurement
  The following were identified but not priced, and are not in the totals:
  #table(columns: (1fr, 1fr), th([Item], [Location]), ..d.awaiting.map(w => ([#w.description], [#w.location])).flatten())
]

// ---------------------------------------------------------------- 8
= Depreciation schedule
#table(
  columns: (1fr, auto, auto, auto, auto, auto, auto),
  align: (left, right, right, center, center, center, right),
  th([Class], [Basis], [Life (GDS / ADS)], [Method], [Convention], [In service], [Bonus default]),
  ..d.schedule.map(s => ([#s.name (§ #s.section)], [#s.basis], [#s.life / #s.ads], [#s.method], [#s.convention], [#s.placed], [#s.bonus])).flatten(),
)
#note[
  Methods, conventions and lives: #d.pub946; ADS lives: #d.pub946_ads. For 5, 7 and 15-year property the convention is #d.mid_quarter.
  #for s in d.schedule.filter(s => s.bonus_note != "") [ #s.name: #s.bonus_note ]
  Bonus depreciation rates are the statutory defaults for the dates shown (acquired #d.schedule.first().acquired, placed in service #d.schedule.first().placed); the elections are the preparer's.
]
#if d.look_back != none [
  == Look-back
  The property was placed in service before this study, and #d.look_back.claimed of depreciation was claimed for #d.look_back.years using #d.look_back.method. Reclassifying it is a change in accounting method: Form 3115 under Rev. Proc. 2025-23, section 6.01 (DCN 7), with a section 481(a) adjustment (ATG chapter 4 C.13).
]
Related issues (ATG chapter 4 C.13): section 263A reaches property the taxpayer produces; an existing building acquired by purchase has no self-constructed costs to capitalize under it. No sampling was used.

// ---------------------------------------------------------------- 9
= Assumptions and limiting conditions
#let standard = (
  "The purchase price, closing costs and dates are those supplied by the taxpayer and the closing statement; the land value and the property's physical facts are those of the appraisal or the source named in section 3.",
  "Building systems that cannot be seen are estimated from the cost source's square-foot model for the building's type, size and quality.",
  "Costs are RSMeans 2020 costs, including the installing contractor's overhead and profit, brought to the cost year with the published historical cost index and to the location with the published location factor.",
  "This study is not an appraisal of the property and does not express an opinion of its market value.",
  "This study is not tax or legal advice. Elections, conventions and the treatment on the taxpayer's returns are the responsibility of the taxpayer and the preparer.",
)
#for c in (if d.limiting != none { d.limiting } else { standard }) [- #c]
#if d.unpriced_systems.len() > 0 [
  - These building systems have no priced line in the schedule; their cost is inside the reconciliation gap (Exhibit B), not in any class: #d.unpriced_systems.map(u => u.system).join(", ").
]
#if d.flags.len() > 0 [
  == Open review items at approval
  #table(columns: (auto, 1fr), th([Check], [Finding]), ..d.flags.map(f => ([#f.code], [#f.message])).flatten())
]

// ---------------------------------------------------------------- 10
= Certification
I certify that, to the best of my knowledge and belief:
- I developed the analysis in this report, and the statements of fact in it are true and correct.
- #if d.photographs == none [[the inspection statement]] else if d.photographs.site_visit [I inspected the property on #d.photographs.on.] else [I did not inspect the property; the analysis relies on photographs supplied by #d.photographs.by.]
- The recovery periods follow Rev. Proc. 87-56 and IRC § 168.
- My compensation is not contingent on the results of this study, and I have no present or prospective interest in the property.

#v(14pt)
#if d.signed != none [
  #block(stroke: 0.6pt + accent, inset: 10pt, radius: 3pt)[
    *Signed in Basis* · #d.signed.at #if d.signed.id != "" [· signature #d.signed.id] \
    #d.engineer.name, #d.engineer.credentials #if d.engineer.license != "" [· #d.engineer.license] \
    #d.firm.name
  ]
] else [
  #line(length: 45%, stroke: 0.5pt + muted)
  #d.engineer.name, #d.engineer.credentials \
  #text(fill: muted)[Not yet signed]
]

// ---------------------------------------------------------------- exhibits
#pagebreak()
#heading(numbering: none)[Exhibit A: Photographs with identified components]
#if d.photos.len() == 0 [
  The #d.photograph_count photographs, with every counted component outlined and numbered, are kept in the study record.
] else [
  #for p in d.photos [
    #block(breakable: false, below: 12pt)[
      #align(center, image("img/" + p.file, width: 100%, height: 3.75in, fit: "contain"))
      #text(8pt, fill: muted)[#p.caption]
    ]
  ]
]

#heading(numbering: none)[Exhibit B: Cost source reconciliation]
The building's replacement cost from the cost source's square-foot model, by system, against the itemized lines and the concealed lines the study produced for that system.
#table(
  columns: (1fr, auto, auto, auto),
  align: (left, right, right, right),
  th([System], [Building model], [Itemized], [Concealed]),
  ..d.reconciliation.map(r => ([#r.component], [#r.top_down], [#r.itemized], [#r.concealed])).flatten(),
)

#heading(numbering: none)[Exhibit C: Quality elements of the ATG]
#table(
  columns: (auto, 1fr, auto),
  th([ATG ch. 4], [Element], [Where this report answers it]),
  ..d.checklist.map(c => ([#c.element], [#c.title], [#c.where])).flatten(),
)

// ---------------------------------------------------------------- appendix
#set page(flipped: true, margin: (x: 0.6in, top: 0.8in, bottom: 0.7in))
#heading(numbering: none)[Appendix 1: Detailed schedule of property units]
#set text(7.5pt)
#table(
  columns: (auto, 1.5fr, 1.3fr, auto, auto, auto, auto, auto, auto),
  align: (right, left, left, right, left, right, right, center, center),
  th([\#], [Item], [Location], [Quantity], [MasterFormat], [Replacement cost], [Allocated], [Class], [Asset class]),
  ..d.items.map(i => (
    [#i.line],
    [#i.description#if i.estimated [ #text(fill: muted)[_(estimated)_]]#if i.contested [ #text(fill: rgb("#a4461d"))[_(contested)_]]#if i.engineer_price != "" [ #linebreak() #text(6.5pt, fill: muted)[_engineer's price:_ #i.engineer_price]]],
    [#i.location#if i.seen_in != "" [ #linebreak() #text(6.5pt, fill: muted)[seen in #i.seen_in]]],
    [#i.quantity], [#i.masterformat], [#i.rcn], [#i.allocated], [#i.class], [#i.asset_class],
  )).flatten(),
  table.cell(colspan: 6, stroke: none)[*Total*], [*#d.depreciable*], [], [],
)
