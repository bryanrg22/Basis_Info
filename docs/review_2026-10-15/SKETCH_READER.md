# Reading the appraisal's building sketch (research and design, 2026-11-23)

The takeoff measures nothing: the footprint is GLA / stories, the perimeter is a 1.5:1
rectangle of that footprint, and roof, siding and gutters follow (TAKEOFF_RESEARCH.md
section 3). TAKEOFF_RESEARCH section 5 and EVAL_REPORT 35.3 and 87.8 each name the appraisal's
sketch page as the first thing worth reading instead. This note is the research and the
design of the reader that was built (`backEnd/basis/extraction/sketch.py`, the `sketch`
readings of `backEnd/basis/domain/measurements.py`, the setting `TAKEOFF_SKETCH`); EVAL_REPORT
section 96 is what it measured.

## 1. What a sketch page is, and what it prints

**The measuring standard.** ANSI Z765-2021 is the standard for a detached or attached
single-family house. As Fannie Mae's fact sheet and a la mode's help page restate it (the
standard's own text is sold, and was not read): the house is measured to the nearest inch or
tenth of a foot and the finished area is reported to the nearest whole square foot; a level
that is partly or wholly below grade is below-grade area and is reported apart from the
above-grade area; garages and unfinished areas are not finished area; stairs count on the
floor they descend from and an opening to the floor below does not count; finished area needs
a 7 ft ceiling. Fannie Mae has required it since April 2022 (Selling Guide B4-1.3-05, read in
its 2025-06-04 version), and says the output of a sketching tool must conform to it. It does
not cover two- to four-unit buildings.

**The exhibit.** Fannie Mae's exhibit list (Selling Guide B4-1.2-01, 2025-09-03) asks for a
floor plan or footprint sketch "and calculations". Its glossary describes a footprint sketch
as software-generated, showing every exterior wall with dimensions to the tenth of a foot,
every level, and the calculations that show how the square footage was derived (paraphrased;
the page was fetched, not character-checked). So the sketch is where the living area on the
form is computed: the two are the same number when both are right.

**What the tools print.** Two public sample prints were inspected, and the stored analyses of
the client appraisal and the GSE's sample reports (section 2):

| tool or form | heading | rows | calculation |
|---|---|---|---|
| a la mode TOTAL Sketch | "Area Calculations Summary", then "Living Area" and "Non-living Area", with a "Calculation Details" column | one per area: "First Floor", "Second Floor", "2 Car Attached", "Basement", a porch or patio by name; each page ends its living rows with "Total Living Area (Rounded):" | `12 × 5 = 60` per rectangle; a triangle as `0.5 × a × b = c`; areas as whole or two-decimal "Sq ft" |
| the redesigned URAR's sample reports (UAD 3.6) | "Area Calculations Summary", then "Total Above Grade", "Total Below Grade", "Non-Living Area" (one sample has no sub-headings) | "Floor 1", "Level 2", "B1 Finished Below Grade", "Garage", "Accessory Dwelling Unit", per-unit rows on a multi-unit building | `50.00 x 30.00 = 1500.00`, areas as "1500.00 Sq. Ft." |
| Apex Sketch | "AREA CALCULATIONS SUMMARY" with columns Code, Description, Net Size, Net Totals (GLA1, GLA2, GAR, P/P) and a breakdown page | one per code | on the breakdown page, `52.30 x 8.80 = 460.24` under each code |

TOTAL also has an area type it calls Non-Calculated, whose square footage it does not count
toward any total; the client appraisal uses it for the porch.

**The redesigned URAR (UAD 3.6).** Its Sketch section (`backEnd/basis/extraction/uad36_map.py`,
section 7) carries only a Measurement Standard and a Sketch Commentary as data; the sketch
itself is an image exhibit. The areas are structured elsewhere on that form (finished and
unfinished, above and below grade), not per rectangle and with no outline. A UAD 3.6 report
therefore gives the reader the same thing a 1004 does: a printed table on a sketch page.

**How closely the sketch must match the form.** No numeric tolerance was found in any GSE
document. The published checks are qualitative (levels and room detail must match; Collateral
Underwriter flags materially different areas across reports, threshold unpublished). The
project's own appraisal evaluation already carried a rule of this kind
(`backEnd/evals/appraisal/systems/validators.py`, `gla_vs_sketch`: every printed living-area
line within 1 SF of the GLA box), which was never part of production and compares page totals,
not their sum.

**How accurate a sketch is.** Only vendor figures were found: two appraisers measuring the same
285 houses differed by 3.9 % on average (Clear Capital, 2021). No independent study of sketch
area or perimeter accuracy was found. A sketch is the appraiser's measurement, not a survey.

## 2. What can be read from the text, and what cannot

Measured on the stored Azure Document Intelligence analyses under
`$BASIS_EVAL_DATA_DIR/appraisal/recordings/` (42 documents; no new call) and on the client
PDF's text layer.

**The table: yes.** Seven of the 42 analyses carry an "Area Calculations Summary" (the client
appraisal, its two UAD 3.6 rebuilds, four public sample reports); the reader finds every row
of each. On the client appraisal, Azure's lines and the PDF's text layer give the same six
rows on the same four pages. The damage seen in the text is confined to the calculation
column and is survivable because the arithmetic is printed twice: a calculation split over
two or three lines, an equals sign read as a dash, a multiplication sign or an operand lost.
A row is trusted only when its rectangles' printed results sum to its printed area.

**The perimeter from the edge labels: no.** The drawing's edge labels (`24'`) are in the
text, and three things stop them from giving an outline:

1. They are incomplete. On the client's first-floor page the text layer has 22 labels and
   Azure's lines 20: labels printed along a vertical wall are rotated, and OCR reads some of
   them as `00` or `Co`. The text layer has them all but prints the rotated ones reversed
   (a 24 ft label would read `'42`).
2. They are not assigned. A label does not say which outline it belongs to: the dwelling's,
   the garage's and the porch's labels are one list, in reading order.
3. Not every wall is labelled. One wall of the dwelling's outline carries no label.

The sum of that page's labels is 7 % more than the ground story's outline and 53 % more than
the dwelling's. Nothing in the text says which labels to drop.

**The perimeter from the drawing's line work: yes on this PDF, not built.** The client PDF is
born digital and its sketch pages are vector drawings: walls are heavy strokes. Tracing them
into closed loops (a private script, EVAL_REPORT 96.4) gives four loops on three pages whose
areas reproduce the table's printed areas to a tenth of a square foot at one scale per page,
with every one of 41 walls within 0.02 ft of a half foot. That is a reader with a strong
self-check (a loop is believed only when its area reproduces a printed row), and no model
call. It was not built here: it reads a different thing (the PDF's drawing operators, which
Azure's analysis does not keep), it has been seen on one document from one tool, and a scanned
sketch has no line work at all. It is the next step, and section 96 measures what it would add.

**A vision model on the page image** was not tried: nothing paid was run.

## 3. The reader

`basis.extraction.sketch` (pure functions over page lines; no I/O, no model):

1. **Find the table.** A page is a sketch page when a line contains "Area Calculations
   Summary"; only the text after it is read (the drawing, with its room names and edge labels,
   comes first on every layout seen).
2. **Read rows.** A line is a section heading ("Living Area", "Total Above Grade",
   "Non-living Area", "Total Below Grade"), a label, an area ("1200 Sq ft", "1500.00 Sq.
   Ft."), calculation text (digits and `x × = -` only), or a text-layer line holding a label,
   an area and a calculation together. A row is the last label before an area, that area, and
   the calculation text up to the next label. "Total Living Area (Rounded):" is kept as the
   page's printed living total, not as a row.
3. **Read rectangles by position**, not by punctuation: operands, then a result, so a lost
   sign does not lose the line; a leading minus is a deduction.
4. **Verify.** A row is verified when its results sum to its printed area, allowing half a
   square foot of rounding per rectangle. An unverified row stays in the stored reading and
   never becomes a field.
5. **Classify.** The label decides first: garage (attached when it says so, detached when it
   says so), basement or below grade, patio, porch, deck. A living row is one whose label
   names a floor ("First Floor", "Floor 2", "Level 1", "Main Level") under a living heading or
   under none. A row under a living heading that names no floor (an accessory unit, a room in
   an outbuilding, a common area) is not counted: the footprint needs to know which rows are
   the ground floor, and section 96.2 measures this rule against counting every row.
6. **Write fields** in a `sketch` section, each with its page and source (`azure`, or `regex`
   when only the text layer was available), at the table readers' confidence (0.80):
   `sketch.living_area_sqft` (every living row verified, and each page's rows equal to the
   page's printed total), `sketch.ground_floor_area_sqft` (the level-1 rows),
   `sketch.garage_area_sqft` (garage rows not labelled detached),
   `sketch.patio_area_sqft` (rows labelled as a patio). The whole reading, with every row's
   rectangles and the reason each missing field was withheld, is stored in the extraction's
   `raw["sketch"]`.

Not parsed: the Apex layout (found, yields no row, so no field), a table with areas but no
calculation column (no row verifies), hand-drawn or image-only sketches without OCR text.

## 4. How the takeoff uses it

`TAKEOFF_SKETCH` (default `off`) gates both ends: with `on` the extraction writes the fields
and `measurements_from_fields` reads them.

* **The cross-check.** The sketch is used when its living area is within
  `TAKEOFF_SKETCH_GLA_TOLERANCE` of the printed GLA (a fraction, default 0.01). Section 96.2
  is the evidence for the default: three public single-family samples agree to the square
  foot, and the two disagreements seen are 10 % and 12.5 %, so 1 % separates them with room
  for per-level rounding or a form figure rounded to ten. The check runs at takeoff time
  against the fields in effect, so a GLA the engineer corrects at the appraisal gate is the
  GLA the sketch is checked against.
* **Accepted.** The ground floor is the dwelling's footprint in place of GLA / stories; the
  sketch's garage replaces the cost approach's garage line (and sizes the garage room); the
  upper floors' walls are sized on the sketch's own upper-floor area (living area less ground
  floor) instead of on a second copy of the ground floor; a dimensioned patio is a measured
  quantity instead of `needs_measurement`. A GLA and a story count are still required, as
  today. The perimeter is still the 1.5:1 rectangle, on the better footprint; the gutters still
  run on that rectangle's long side.
* **Refused.** Nothing changes except one input, `sketch_check`, on the roof, siding, gutter
  and landscaping takeoffs, which says the sketch was not used and prints the two figures and
  the tolerance. No substitute value is involved either way: both paths are published methods
  on inputs the study has (EVAL_REPORT 78).
* **A perimeter, when something reads one.** `sketch.ground_perimeter_ft` and
  `sketch.upper_perimeter_ft` replace the two rectangles in the roof and the siding when they
  are present with an accepted sketch footprint. Nothing writes them yet (section 2); they are
  there so the outline reader, or an engineer's measurement, has a place to land, and so
  section 96 could measure what they change through the production formulas.

## 5. Not confirmed, and open

* ANSI Z765's own text, Fannie Mae's glossary wording verbatim and its 2025 FAQ were not read
  (paywall, 403); the points above come from Fannie Mae's Selling Guide pages, its 2022 fact
  sheet and a la mode's help.
* The default area-type lists of TOTAL and Apex, any other tool's layout (RapidSketch,
  PhoenixSketch, CubiCasa), and whether tools always export line work as vectors.
* The reader has seen one client appraisal and four public samples, all from two layouts.
* What Basis should do when the sketch and the GLA disagree is not settled (section 96.6).

## Sources

* Fannie Mae Selling Guide B4-1.3-05, Improvements Section of the Appraisal Report (2025-06-04).
  https://selling-guide.fanniemae.com/sel/b4-1.3-05/improvements-section-appraisal-report
* Fannie Mae Selling Guide B4-1.2-01, Appraisal Report Forms and Exhibits (2025-09-03).
  https://selling-guide.fanniemae.com/sel/b4-1.2-01/appraisal-report-forms-and-exhibits
* Fannie Mae Selling Guide E-3-06, glossary F (footprint sketch).
  https://guide-selling.fanniemae.com/sel/e-3-06/acronyms-and-glossary-defined-terms-f
* Fannie Mae, ANSI fact sheet (2022-03-31), as hosted by NYSAR.
  https://www.nysar.com/wp-content/uploads/2022/04/Fannie_Mae_ANSI_Fact_Sheet_updated_033122.pdf
* a la mode, ANSI compliance: measuring, rounding and reporting in sketches.
  http://help.alamode.com/kb/guide/en/8014-ansi-compliance-measuring-rounding-and-reporting-in-sketches-LO0Q95T6cl/Steps/4257196
* North Carolina Real Estate Commission, residential square footage guidelines against ANSI.
  https://bulletins.ncrec.gov/ncrec-residential-square-footage-guidelines-vs-ansi-standard
* Sample prints: TOTAL Sketch https://media.hd.pics/1/nc4av0aflj.pdf ; Apex
  https://media.hd.pics/r/v0p1exx32f.pdf
* DataMaster, UAD 3.6 section 7 Sketch. https://datamasterusa.com/uad36/b1/07-sketch
* Freddie Mac, UAD ANSI reporting requirements job aid (2026-06-23).
  https://sf.freddiemac.com/docs/pdf/uad-ansi-reporting-requirements-job-aid.pdf ; UAD lessons
  learned job aid. https://sf.freddiemac.com/docs/pdf/uad-lessons-learned-job-aid.pdf
* Clear Capital, automated floor plans and digital GLA (vendor, 2021).
  https://www.clearcapital.com/automated-floor-plans-and-digital-gla-the-key-to-unlocking-appraisal-modernization
