# The study report and the preparer's schedule

What the export step writes for the engineer to sign and the preparer to import, and how to
fetch it. Design and decisions: `docs/review_2026-10-15/STUDY_REPORT_SPEC.md` section 11.
Code: `basis/report/` (model, schedule, exhibit, render, the Typst template and the bundled
IBM Plex Sans), `basis/activities/export_report.py`, `basis/api/routes/report.py`.

## Files

After the assets review is approved, the export step writes, next to `rev-<n>.json`:

| File | What |
|---|---|
| `rev-<n>.report.pdf` | The report, in ATG chapter 4 D.1-D.9 order with Exhibits A (photographs with every counted object outlined and numbered, captioned with the report lines), B (cost source reconciliation by system) and C (the C.1-C.13 checklist), and Appendix 1 (every line). |
| `rev-<n>.schedule.csv`, `rev-<n>.schedule.xlsx` | The preparer's schedule, one row per report line: IRC section, asset class, life (GDS and ADS), method, convention, dates acquired and placed in service, basis, bonus eligibility, statutory bonus default and its source, authority. Rows sum to the depreciable basis to the cent. |

The `export.written` event records them under `detail.report`: `files` (uri and sha256 of
each) and `draft`, or `skipped` (`code`, `message`) when there is nothing to report.

## What is checked before anything is written

* The allocation exists. When the result withheld it (`summary.basis.allocation` is null;
  `summary.basis.missing` names why), the report is skipped with `BASIS-REPORT-002` and the
  export completes as before.
* Every total ties to the cent: lines, classes, categories, schedule rows, and price plus
  capitalized costs less land, all against the depreciable basis (`BASIS-REPORT-003`).
* Every export asset is on exactly one line: a priced line, folded into the line its
  quantity was measured with (`takeoff_inputs.measured_with_asset`: the site-wide
  landscaping, the roof), or listed as awaiting a measurement outside the totals
  (`BASIS-REPORT-004`).

Systems with no priced line (`summary.basis.not_produced`) are disclosed in the limiting
conditions, not refused.

## Draft and final

The report is a DRAFT (watermark, placeholders) until it has the firm's profile, the study's
answers (taxpayer; who photographed the property and when, so the certification states a
site visit only when there was one) and the engineer's signature in Basis. Storing those
inputs and the signing action are the next change; until then every report is a draft.

## Fetching

`GET /v1/studies/{id}/report?format=pdf|csv|xlsx[&revision=n]`, for a viewer or above of the
study. The default revision is the latest exported. The response is the file as an
attachment, with the recorded sha256 as a strong `ETag` (`If-None-Match` gives 304).

| Case | Answer |
|---|---|
| Nothing exported, revision never exported, file or bytes missing | 404 `BASIS-REPORT-006` |
| The export skipped the report | 404 with the skip's code (usually `BASIS-REPORT-002`) and its message |
| Another firm's study | 404 |

## Determinism

The same export, inputs and photographs give the same bytes. Typst renders with the bundled
font only, and the PDF timestamp is the report date, the day the assets review was approved.
The XLSX has pinned document properties and zip times. Measured: Mac and the Linux worker
image give byte-identical PDFs (STUDY_REPORT_SPEC 11.8.1).
