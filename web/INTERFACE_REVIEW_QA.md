# Shared interface pass — October 22, 2026

Continues the vision workspace on `codex/vision-review-workbench`, starting from
`ff68d04`. Remote main was refreshed before work and matched the existing base `2728042`.
Only `web/` changes are included. Backend implementation and the approved sidebar
mechanics are preserved. This pass focuses on the engineer workflow and public entry
pages; it does not redesign owner administration or add missing backend capabilities.

## What changed

* **Shared controls and surfaces:** `interface-system.css` defines the ink, secondary
  text, accent, line, paper, wash, radius and motion tokens. Navigation keeps the sage
  glass; evidence and forms use solid surfaces. Buttons, inputs, select popups, tabs,
  badges, table rows and dialogs have consistent states and visible focus.
* **Home and Library:** compact headings, a quieter next-review surface, clearer register
  typography, consistent actions/search, and responsive pagination. Live data keeps its
  actual API fields; mock-only client/address/cover-image fields were not fabricated live.
* **Start study:** one unified mixed-file area, clearer identity fields and a quieter
  supporting workflow column. Grouping and upload controllers remain the existing ones.
* **Appraisal:** desktop fit within the viewport, independent extracted-field and document
  scrolling, a fixed place for decisions in the layout, smaller navigation, and clearer
  selected fields. On narrower screens these sections stack. Field selection restores the
  internal list position and keyboard focus without intentionally scrolling the document.
* **Rooms:** separate named room sections remain. Solid surfaces, larger readable titles,
  clearer photo actions and inventory tables replace overlapping green treatments.
* **Assets, detail, verification and schedule:** consistent numerical rows, quieter metrics,
  distinct evidence surfaces and common decision controls. Numeric confidence is hidden
  in appraisal/detail views, while the response data remains available to the model.
* **Files, study record, Activity and Settings:** common type, contrast, spacing and working
  surfaces. Existing mock/local-only labels remain honest about unsupported persistence.
* **Public entry:** the landing navigation clears the measured review bar while scrolling;
  the hero, feature sections, pricing layout, FAQs and sign-in share the same controls and
  palette. Historical pricing and existing destinations remain unchanged.
* **Behavior found during QA:** an older responsive rule hid the live asset filter; the
  underlying filter also lacked its change handler. Both were fixed and checked against
  four real local API assets. The filter now says No flags rather than implying an engineer
  reviewed the results. Live review pages no longer show a misleading local-draft banner.
* **Read-only:** a closed gate shows its explanatory notice without disabled Approve /
  Request corrections / Correct value controls. Open-gate correction and approval rules
  continue to use the existing controllers and endpoints.

## Design reasoning

The plan keeps the existing identity and spends emphasis on the work: a property photo,
source document, or selected asset. Inter remains the typeface; the working scale is
12/13/14/18/30px. Main colors are ink `#213b30`, secondary `#53675d`, accent `#3c6b54`,
line `#d5dfd8`, wash `#f2f6f3` and white. Spacing uses an 8px rhythm, with 180ms feedback.

The initial plan was reviewed against the user's objections: more rounded green cards
would repeat the problem. The implemented pass instead separates navigation material
from solid evidence, reduces redundant headers, and gives each task its own layout.

References checked for this pass:

* [Linear's redesign](https://linear.app/now/how-we-redesigned-the-linear-ui): clearer
  hierarchy, alignment and navigation density while keeping established workflows.
* [Geist tables](https://vercel.com/geist/table): semantic comparable rows, aligned numbers,
  clear empty states and pagination; descriptive metadata stays outside data tables.
* [WAI combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/): retain the
  existing select-only controller and its expanded state, options and keyboard behavior.
  This was a styling/availability fix, not a new dropdown implementation.

## Verified

* **92/92 Node tests passed**, including the existing upload, library, review, API-client
  and vision-model suites. Changed JavaScript syntax checks and `git diff --check` passed.
* **Browser layout observations:** 17 routes were visited with no reported horizontal
  document overflow or broken visible images. The original claim of six exact viewport
  sizes is withdrawn: a follow-up found the browser ignored viewport overrides. Those
  observations do not establish six-size responsive coverage. The comparison gallery
  records the actual dimensions of its retained screenshots.
* **Appraisal at 1280×800:** selected the last field; the source changed to page 8,
  the internal list scrolled, document scrollY remained 0 and document height remained 800.
* **Pinned sidebar at 1024×768:** appraisal panels and decisions remained within the
  viewport, with no horizontal document overflow. Expand/collapse and account → Profile
  settings navigation were exercised.
* **Library:** sort menu selection, zero-result search and clear recovery. The 240-study
  fixture displayed 1–10 of 240, then 11–20 of 240 after Next page; reset to six afterward.
* **Mixed local files:** selected the existing generated sample PDF and PNG together.
  The generic PDF was grouped as supporting documents and the image as photos. Changing
  the PDF group to Appraisal and removing both selections worked. No upload was submitted.
* **Public navigation:** Pricing anchor reached its section; after scrolling, the bar top
  was 56.5px and the review bar bottom 44.5px, leaving the full navigation visible.
* **Live API as employee:** used the disposable Vision QA firm. Created two synthetic
  local studies with 23 extracted fields, the existing generated eight-page sample PDF,
  and four costed assets. No paid inference or workflow execution was started.
* **Live appraisal:** real authenticated PDF bytes rendered as Page 1 of 8; field/source
  selection stayed in sync and restored focus. A sample correction was staged, displayed
  in the decision summary and disabled approval. No decision was sent.
* **Live assets:** No flags produced the empty state; All assets restored all four rows.
  Review navigated to asset detail, with numeric confidence absent; the classification
  correction dialog opened. Closed-study appraisal had one notice, zero visible decision
  controls and no local-draft banner.
* Final mock and live browser logs had no captured warnings or errors.

## Limits

* The live Chrome viewport during this pass was approximately 784×853. Exact six-size
  coverage is mock/shared-layout coverage, not six-size live authentication coverage.
* The broad screenshots were inspected for representative desktop and phone layouts;
  dimension/image checks cover all 102 captures. They are not a full accessibility audit,
  physical-device certification or proof of every possible state.
* Real OS file dragging was not newly automated; local chooser selection, regrouping and
  removal were checked. The existing drop handlers are preserved and their visual drag
  state is styled. No network upload, start, cancel, approval, correction submission,
  Temporal reprocessing, paid model call or report generation was performed in this pass.
* Activity, presence, profile/company preferences and fields without API support remain
  mock/local previews as documented in README. Owner administration was not redesigned.
* Test corrections remain in memory only. Test studies are synthetic local fixtures,
  not evidence of a completed cost-segregation pipeline or correct tax classifications.
* Source is committed locally; no push, merge or deployment is implied. Full screenshot
  packs are outside Git. Repository image evidence contains only synthetic/generated data.

## Landing component preview follow-up — October 22

Removed the separate hand-drawn pendant/cabinet SVG from `miniProduct`. Landing,
its second evidence preview, and sign-in now mount `BasisMaskLayer` with the same
photo fixture used by `vision-workbench`. The fixture factory returns new objects
for each consumer, so review edits cannot contaminate the public preview. Overview
mode filters the existing component boxes without applying Spotlight's green glow.
These remain explicitly illustrative sample boxes, not exported SAM masks or a new
analysis result. No client photo or live API request was introduced.

Verified in browser: Cabinets and Lights select their groups; selecting Lights again
returns to All components; the second landing preview remains independent; Enter
activates Countertop and displays one object. Open photo review navigates to the
full viewer with ten objects; Light fixture still exposes three instances. Sign-in
mounts one preview canvas. No captured console errors. All 92 existing tests pass,
changed scripts pass syntax checks, and `git diff --check` passes. Browser observations
were at actual 780×797 and 1280×720 viewports; no phone-size claim is made.
