# Study navigation and appraisal source QA

Verified locally on 2026-10-24 in the isolated redesign worktree.

## Behavior

- Selection follows the page being viewed (`aria-current="page"`), separately from workflow progress. Completed steps keep their checkmark and Completed label.
- Both legacy five-step and new six-step studies retain their existing gates. The active review shows Needs review even when an earlier completed page is selected.
- Completed Upload opens uploaded files. Uploads that have not completed retain the existing progress destination; the new-study form remains at Upload.
- Selecting an appraisal field uses its source document and cited page. PDF.js renders a selectable text layer; exact value matches receive highlights and the PDF pane scrolls to the first match.
- Multiple matches are explicitly reported. Missing text, missing pages, invalid page references and unavailable documents do not receive invented highlights. The original extracted value remains the source target while a correction is staged.
- Completed appraisal corrections are blocked by both presentation and the action handler.

## Automated verification

`node --test web/tests/*.test.cjs`: 116 passed, 0 failed.

Navigation regressions cover completed-stage revisits, both gate versions, future-step availability, processing/failed states, drafts, finished studies and child routes. PDF matching covers split text runs, numeric formatting and boundaries, repeated values, missing values, dates and literal punctuation. A direct-action regression checks that completed appraisals cannot stage corrections.

## Browser verification

The real six-step API initially verified Rooms selected and completed while Components remained the current review. Docker then became unavailable (`Docker Desktop is unable to start`). Subsequent tests used the real frontend and PDF.js with the isolated, read-only HTTP fixture in `tests/fixtures/navigation-source-server.py`; these do not establish live backend integration for the user's study.

Verified with the fixture:

- Five- and six-step completed Rooms and Appraisal selection.
- Completed Upload opens files; Study progress returns to progress.
- Keyboard field selection, source-page navigation, highlighting and expanded PDF at the cited page.
- Currency, decimal, date, repeated values, a 90-degree rotated PDF page, missing text, no page reference, out-of-range page and a blank page without searchable text.
- Closed appraisal hides Correct value. At the open gate, a locally staged correction leaves the original PDF value highlighted and disables approval until corrections are resolved. No backend decision was submitted.
- 390-pixel mobile layout: no horizontal document overflow, selected step remains visible, source panel comes into view after field selection, and Open PDF fits its header.
- Final browser console error list was empty.

Screenshots are saved in the task artifact folder under `interface-review/navigation-source/`: `rooms-completed.jpg`, `legacy-rooms-completed.jpg`, `uploaded-files.jpg`, `appraisal-source.jpg`, `mobile-selected-step.jpg`, and `mobile-source.jpg`.

## Design and implementation references

- [USWDS step indicator](https://designsystem.digital.gov/components/step-indicator/): explicit completed/current status; page selection is represented separately here because completed pages can be revisited.
- [PDF.js examples](https://mozilla.github.io/pdf.js/examples/): page viewport, device-pixel rendering and PDF text geometry.
- [PDF.js text layer styles](https://github.com/mozilla/pdf.js/blob/master/web/text_layer_builder.css): geometry and page rotation for text overlays.

Limitations: no OCR or semantic field bounding boxes are invented; the source highlight is an exact text match on the backend-cited page. Actual study re-verification remains pending Docker recovery.
