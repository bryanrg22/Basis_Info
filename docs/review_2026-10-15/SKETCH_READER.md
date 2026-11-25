# Reading the appraisal's building sketch (research and design, 2026-11-23)

The takeoff measures nothing: the footprint is GLA / stories, the perimeter is a 1.5:1
rectangle of that footprint, and roof, siding and gutters follow (TAKEOFF_RESEARCH.md
section 3). TAKEOFF_RESEARCH section 5 and EVAL_REPORT 35.3 and 87.8 each name the appraisal's
sketch page as the first thing worth reading instead. This note is the research and the
design of the reader that was built (`backEnd/basis/extraction/sketch.py`, the `sketch`
readings of `backEnd/basis/domain/measurements.py`, the setting `TAKEOFF_SKETCH`); EVAL_REPORT
section 96 is what it measured. Section 6 is the second reader, built after it: the outline
from the PDF's own line work (`backEnd/basis/extraction/sketch_outline.py`, the setting
`TAKEOFF_SKETCH_OUTLINE`), measured in EVAL_REPORT section 100.

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

**The perimeter from the drawing's line work: yes on this PDF.** The client PDF is born
digital and its sketch pages are vector drawings: walls are heavy strokes. Tracing them into
closed loops (a private script, EVAL_REPORT 96.4) gives four loops on three pages whose areas
reproduce the table's printed areas to a tenth of a square foot at one scale per page, with
every one of 41 walls within 0.02 ft of a half foot. That is a reader with a strong
self-check and no model call. It reads a different thing (the PDF's drawing operators, which
Azure's analysis does not keep), it has been seen on one document from one tool, and a
scanned sketch has no line work at all. Section 96 measured what it would add; section 6
below is the reader that was then built, and EVAL_REPORT 100 what it measured.

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
* **Flagged at the appraisal gate.** The extraction raises `gla_vs_sketch` (a warning,
  BASIS-CHECK-031) when the sketch's living area is outside the tolerance of the GLA box:
  exactly the sketches the takeoff refuses. The engineer corrects the figure that is wrong;
  the flag is judged again on the corrected fields.
* **Refused.** Nothing changes except one input, `sketch_check`, on the roof, siding, gutter
  and landscaping takeoffs, which says the sketch was not used and prints the two figures and
  the tolerance. No substitute value is involved either way: both paths are published methods
  on inputs the study has (EVAL_REPORT 78).
* **A perimeter, when something reads one.** `sketch.ground_perimeter_ft` and
  `sketch.upper_perimeter_ft` replace the two rectangles in the roof and the siding when they
  are present with an accepted sketch footprint. The outline reader of section 6 writes them
  (source `drawing`) when `TAKEOFF_SKETCH_OUTLINE` is on and the drawing can be read; an
  engineer's measurement can land in the same two fields. One guard applies to the reader's
  figure only: when the takeoff's garage is not the sketch's (the sketch draws none and the
  cost approach prices one), the outline does not include that garage's walls, so the
  rectangle stays and the roof and siding rows say why (`sketch_outline`).

## 5. Not confirmed, and open

* ANSI Z765's own text, Fannie Mae's glossary wording verbatim and its 2025 FAQ were not read
  (paywall, 403); the points above come from Fannie Mae's Selling Guide pages, its 2022 fact
  sheet and a la mode's help.
* The default area-type lists of TOTAL and Apex, and any other tool's layout (RapidSketch,
  PhoenixSketch, CubiCasa). Whether tools export line work as vectors is taken up in
  section 6.1: not always, and under UAD 3.6 apparently never.
* The reader has seen one client appraisal and four public samples, all from two layouts.
* What Basis does when the sketch and the GLA disagree was decided on 2026-11-23: the
  appraisal is flagged (`gla_vs_sketch`) and the engineer corrects the figure that is wrong
  (EVAL_REPORT 96.6).

## 6. The outline, from the PDF's line work (research and design, 2026-11-23)

Section 2 left the perimeter derived: the text does not carry the outline, and the drawing
does. This section is the research and the design of the reader that takes it from the
drawing (`basis.extraction.sketch_outline`); EVAL_REPORT 100 is what it measured.

### 6.1 Research

**How a sketch gets into the report PDF.** Two ways, and only one leaves line work.

* *Printed by the forms software as drawing operators.* The client PDF's producer is
  "wPDF4 by WPCubed GmbH"; WPCubed describes wPDF as an EMF (Windows metafile) to PDF
  converter that keeps "the vector and text information" (vendor page, read). That fits what
  is in the file: the sketch pages are strokes and text, with no image. No a la mode document
  was found that says TOTAL prints sketches this way; it is what this one PDF shows.
* *Placed as a picture.* TOTAL Sketch exports "all major image file formats" (a la mode help,
  read); a forum thread describes bringing a CubiCasa plan into TOTAL as a scan; another
  describes Apex sketches as "grainy" in the final report of a different forms package
  (search snippet only). Under the redesigned URAR the delivery is a ZIP of the XML, a PDF
  and an "Images" folder whose accepted types are raster only (avif, bmp, gif, heic, heif,
  jpeg, png, tiff, webp; Freddie Mac UCDP FAQ, read; a search snippet showed a shorter list
  for the same page), and the job aid for photos and images gives no vector option for the
  sketch. So a UAD 3.6 report's sketch is expected to be an image.

The stored documents agree (EVAL_REPORT 100.1): of the seven PDFs that carry an area table,
one has vector sketch pages (the client's 1004) and six carry the sketch as an image (the
two UAD 3.6 rebuilds of the client appraisal and four GSE sample reports). Vector line work
is a property of born-digital reports from forms software that prints its own sketcher's
drawing, not of appraisals in general.

**What `pdfplumber` exposes** (0.11.10 over pdfminer.six 20260107; README and installed
source read). pdfminer's `paint_path` classifies each painted subpath: one segment (`m l`)
is a *line*; a closed axis-aligned four-segment path (the `re` operator arrives this way) is
a *rect*; everything else, including a polyline of straight segments, is a *curve*. Each
object carries `stroke` and `fill` (the paint operator), `linewidth`, `stroking_color`,
`dash` (pdfminer's `(pattern, phase)`, `None` when never set) and, since 0.11.0, `path`
(the operators with their points) and `pts`, in page coordinates from the top-left. Two
consequences for a reader: walls can arrive as any of the three kinds, so all three are
read; and `linewidth` is the `w` operand scaled by the transformation in force (pdfminer.six
changelog, 20251227), so a wall's width depends on the page's drawing scale. The client's
four sketch pages carry walls at four different widths, and on one page the form's frame is
heavier than the walls: a fixed "heavy" threshold, which the private script used, does not
generalise.

**How closed loops are found from segments.** The standard construction is a planar
straight-line graph and its faces:

* *Noding*: segments must meet only at endpoints, so they are split where they cross or
  where one ends on another (PostGIS `ST_Polygonize` requires noded input and points to
  `ST_Node`; the JTS `Polygonizer` javadoc says the same).
* *Dangles and cut edges*: JTS removes edges that end at a node of degree one, then edges
  whose two directed halves lie on the same ring (a line joining two rings); Shapely's
  `polygonize_full` returns them as "dangles" and "cuts".
* *The face walk*: with the edges around each vertex sorted by angle, each directed edge is
  followed to the next edge at its head; every walk closes into a face, and the unbounded
  face is told apart by orientation (the doubly connected edge list of de Berg et al.,
  *Computational Geometry*; CGAL's arrangements; the description at cp-algorithms was read).
* *Area*: the surveyor's (shoelace) formula (Braden, *College Mathematics Journal* 1986).

The floor-plan analysis literature mostly starts from pixels and has to find walls first:
Ahmed et al. (ICDAR 2011) separate thick, medium and thin lines by erosion and take the
thick ones as walls; Liu et al. (ICCV 2017) predict junctions with a network and solve an
integer program for walls that close into rooms. Both rest on line weight or learning
because nothing in a floor plan says which loop is which. An appraisal sketch has something
they do not: under the drawing, the same page prints each area and the rectangles it was
summed from. That table is what this reader checks a loop against, so it needs neither a
weight threshold nor a model.

**How walls are dimensioned.** ANSI Z765 as restated by Fannie Mae: measured to the nearest
inch or tenth of a foot. a la mode's help says the standard does not govern the sketch's wall
labels. The client's walls land on half feet. No published rounding setting of any sketching
tool was found.

### 6.2 What the client PDF's sketch pages are made of

Looked at object by object before designing (figures in EVAL_REPORT 100.1):

| seen | consequence |
|---|---|
| walls are straight, solid, stroked lines of one width per page; the width differs page to page, and the form's frame is heavier than the walls on one page | loops are sought within one stroke style at a time, never by a weight threshold |
| the wall style draws only walls; thinner styles draw a deck (solid, sharing two of the house's walls), a shed, stairs, a window | keeping styles apart keeps the stairs from cutting the house into pieces |
| the covered porch is dashed, and the dashes are not a PDF dash pattern: each is a short filled, stroked bar | a dash pattern is refused outright; the bars are loops of their own that no row describes |
| the garage and the house share a wall, drawn twice, at different lengths (the house's corner sits on the garage's wall) | segments are split where another ends on them, and duplicates merged |
| endpoints that coincide differ by up to 0.0001 pt | endpoints are snapped (0.05 pt) |
| the table is below the drawing, under a ruled line; the form's frame encloses both | only strokes wholly above the table's heading are the drawing |
| the first-floor page has 143 closed loops in all; two are the house and the garage | area alone cannot say which loop is which (EVAL_REPORT 100.3) |

### 6.3 The reader

1. **Strokes.** On a page whose text layer carries the table's heading: every straight
   stroked segment wholly above the heading, from lines, rectangles and polyline paths. A
   path with a curve operator is not read (a curved wall leaves its loop open, so nothing is
   believed). A dash pattern is not a wall. A page with no heading in its text layer, or no
   solid stroke above it, has no line work: `raster` when it carries an image.
2. **Loops.** Per stroke style (width, colour): snap endpoints, split segments where they
   meet or cross, merge duplicates, drop every edge a face walk passes in both directions
   (dangles and cut edges, repeated until none is left), and take the bounded faces. Corners
   are the points where the outline turns. The result does not depend on drawing order.
3. **Belief.** One scale (points per foot) per page. A loop is believed for a verified row
   when (a) its area at the page's scale is the row's printed area, within the rounding of
   the printed figure and 0.05 % of coordinate noise, and (b) **it shows the row's printed
   dimensions**: for every rectangle the row's calculation prints (`16 x 10`), one side is a
   distance between two of the loop's corners across the page and the other a distance
   between two corners up it. The area fixes the scale; the dimensions then have to be in
   the drawing. Every `(row, loop)` pair proposes a scale; the scale at which the most rows
   are reproduced, one loop each, is the page's; two different scales reproducing the same
   number of rows is `ambiguous_scale` and nothing on the page is believed.

   Two weaker rules were measured on the same page and set aside (EVAL_REPORT 100.3):
   *two rows reproduced by area at one scale* holds for a pair of small loops as well as for
   the walls, and *every wall on the printed precision* (whole, half or tenth feet) passes
   16 wrong pairings of a row with a loop. The printed-dimension rule passes the two real
   ones and nothing else.
   It is a necessary condition, not a proof: a different set of rectangles whose sides also
   happen to be corner-to-corner distances would pass. Lengths are compared within 0.1 pt of
   coordinate noise at the page's scale, never more than 0.05 ft (a loop drawn too small to
   resolve that cannot show a dimension), plus the scale's own uncertainty from the rounding
   of the printed areas.
4. **The ground perimeter** (`sketch.ground_perimeter_ft`). Read only when the table gave a
   ground floor area (the takeoff uses the one with the other). The first-floor loops and
   the loops of every garage the table does not call detached, all on one page, as one
   outline: the sum of their perimeters less twice the length along which two of them
   coincide. Nothing is written when a row has no believed loop, when the table's garage
   could not be verified, when the ground story is spread over two pages, when two outlines
   cover the same ground, or when an outline shares no wall with the rest (**a garage drawn
   apart, or touching only at a corner, is not assumed attached**).
5. **The upper perimeter** (`sketch.upper_perimeter_ft`), only with a ground perimeter: the
   loops of every living row above the first floor. Loops of one floor on one page are joined
   where the page shows a shared wall, and are separate masses where it shows none.

   **Loops of one floor on separate pages are summed as separate masses.** Two pages have
   two scales and two origins: nothing places one block against the other, so a wall they
   share cannot be seen. Three choices were measured on the client property, where the
   second floor is two blocks on two pages (EVAL_REPORT 100.4): withholding the upper
   perimeter (the upper floors stay a 1.5:1 rectangle of their area) leaves the siding 18 %
   under the summed figure; assuming the blocks adjoin along the wall the ground story
   shares, 5.5 % under. The sum is exact when the blocks are separate masses and too long by
   twice the shared wall when they adjoin; the rectangle is short in either case, by more.
   So the sum is written, the field's text and the takeoff's siding row say that parts of a
   floor drawn on separate pages were counted as separate masses, and `separate_pages` in
   the trace marks it. It is a reading with a stated direction of error, not a substitute:
   each outline was read and checked.
6. **Fields and trace.** Each field carries its value in feet (the printed lengths when every
   wall lands on the printed precision, the drawn lengths otherwise), the page, source
   `drawing` and confidence 0.80. `raw["sketch"]["outline"]` keeps, per page: status
   (`read`, `raster`, `no_strokes`, `no_loop`, `no_verified_row`, `no_match`,
   `ambiguous_scale`), stroke and loop counts by style, the scale, each matched row's loop
   in feet (wall lengths, perimeter, outline), and the reason for every field withheld.
7. **Setting.** `TAKEOFF_SKETCH_OUTLINE` (default `off`), effective only with
   `TAKEOFF_SKETCH=on`. It gates the reader; the PDF's line work is opened on the thread
   that reads the sketch, only for the sketch pages. Off, the extraction and the takeoff are
   byte for byte those without it (EVAL_REPORT 100.5).

### 6.4 Limits, and not confirmed

* One vector document from one tool has been read. Every other stored sketch is an image.
* Not read by design: curved walls; a drawing turned off the page's axes (the dimension
  check compares distances across and up the page); a table laid out beside or above its
  drawing; walls drawn in more than one style; a loop with a wall-style island inside it.
  Each ends in "nothing written", not in a wrong figure.
* The printed-dimension check is necessary, not sufficient (step 3).
* Not confirmed: that TOTAL prints its sketch through wPDF as vectors in general; what Apex,
  RapidSketch, PhoenixSketch, ACI or ClickFORMS put in the PDF (no vendor documentation
  found); pdfminer's line-width behaviour before 20251227 (inferred from its changelog); the
  methods of Dosch et al. 2000, Macé et al. 2010 and de las Heras et al. 2014 beyond search
  snippets (the publishers blocked the fetch), so they are not cited above; any published
  rounding rule for wall labels.

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
* WPCubed, wPDF (EMF to PDF, vector and text kept; vendor page).
  https://www.wpcubed.com/pdf/products/wpdf/
* a la mode, exporting sketches.
  http://help.alamode.com/kb/guide/en/export-sketches-d3QZcAG4c2/Steps/4260034
* Appraisers Forum, importing a CubiCasa sketch into TOTAL.
  https://appraisersforum.com/forums/threads/importing-cubicasa-sketch-into-total.234269
* Freddie Mac, UCDP FAQ (the UAD 3.6 submission ZIP and its image types).
  https://sf.freddiemac.com/faqs/ucdp-faq ; UAD photo and image job aid (2026-06-23).
  https://sf.freddiemac.com/docs/pdf/uad-photo-and-image-job-aid.pdf
* pdfplumber README and changelog. https://github.com/jsvine/pdfplumber ; pdfminer.six
  changelog. https://github.com/pdfminer/pdfminer.six/blob/master/CHANGELOG.md
* JTS Polygonizer.
  https://locationtech.github.io/jts/javadoc/org/locationtech/jts/operation/polygonize/Polygonizer.html
  ; PostGIS ST_Polygonize. https://postgis.net/docs/ST_Polygonize.html ; Shapely
  polygonize_full. https://shapely.readthedocs.io/en/stable/reference/shapely.polygonize_full.html
* CGAL, 2D arrangements. https://doc.cgal.org/latest/Arrangement_on_surface_2/index.html ;
  finding the faces of a planar graph. https://cp-algorithms.com/geometry/planar.html ; de Berg,
  Cheong, van Kreveld, Overmars, *Computational Geometry*, 3rd ed., section 2.2 (not read; cited
  through https://en.wikipedia.org/wiki/Doubly_connected_edge_list).
* Ahmed, Liwicki, Weber, Dengel, "Improved automatic analysis of architectural floor plans",
  ICDAR 2011. https://www-live.dfki.de/fileadmin/user_upload/import/5675_4520a864.pdf
* Liu, Wu, Kohli, Furukawa, "Raster-to-Vector: revisiting floorplan transformation", ICCV 2017.
  https://art-programmer.github.io/floorplan-transformation/paper.pdf
* Braden, "The surveyor's area formula", *College Mathematics Journal* 17(4), 1986 (not read;
  through https://en.wikipedia.org/wiki/Shoelace_formula).
