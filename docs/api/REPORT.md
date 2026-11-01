# The study report and the preparer's schedule

What the export step writes for the engineer to sign and the preparer to import, and how to
fetch it. Design and decisions: `docs/review_2026-10-15/STUDY_REPORT_SPEC.md` section 11.
Code: `basis/report/` (model, schedule, exhibit, render, the Typst template and the bundled
IBM Plex Sans), `basis/activities/export_report.py`, `basis/api/routes/report.py`,
`basis/db/repositories/report.py` (inputs and signatures, migration 0020); the client is
`web/live/live-report.js` (the Schedule screen's "Client report").

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
site visit only when there was one) and the engineer's signature in Basis (below). The
export step's draft carries the inputs stored when it ran.

## Fetching

`GET /v1/studies/{id}/report?format=pdf|csv|xlsx[&revision=n][&variant=final|draft]`, for a
viewer or above of the study. The default revision is the latest exported. The response is
the file as an attachment, with the recorded sha256 as a strong `ETag` (`If-None-Match` gives
304). `format=pdf` is the signed report once the revision is signed (file name
`...-report-signed.pdf`), else the draft; `variant=draft` always gives the draft.

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

## Inputs

Written whole, each with a version that is its entity tag (`ETag: "<n>"`); `If-Match` makes
the write conditional exactly as on the intake (412 with the current `ETag` when someone saved
since; `*` only over an existing document).

| Route | Role | Body |
|---|---|---|
| `GET`, `PUT /v1/orgs/{org_id}/profile` | member reads; owner writes | `name`, `address`, `phone`, `logo_png` (base64 PNG, at most 512 KB), `engineer` (`name`, `credentials`, `license_number`, `license_state`), `limiting_conditions`. 404 `BASIS-REPORT-013` until saved; 412 `BASIS-REPORT-011`. |
| `GET`, `PUT /v1/studies/{id}/report-answers` | viewer reads; engineer writes | `taxpayer_name`, `photographs` (`engineer_site_visit` or `taxpayer_supplied`), `photographed_by`, `photographed_on`, `interviews`, `prior_depreciation`. 412 `BASIS-REPORT-012`; a `study.report_answers_set` timeline event. |

## Signing

1. `GET /v1/studies/{id}/report/state`: the current revision, whether it was exported or its
   report skipped, the files, `missing_for_final` (`firm`, `study`, `signature`) and the
   signature.
2. `GET /v1/studies/{id}/report/preview`: the current revision's report rendered now from its
   export and the stored inputs, unsigned (a DRAFT). Its `ETag` is the sha256 of the bytes;
   rendering is deterministic, so the same inputs give the same tag.
3. `POST /v1/studies/{id}/report/sign` (engineer or owner; `Idempotency-Key`):

   ```json
   {"revision": 1, "reviewed_sha256": "<the preview's ETag, unquoted>",
    "engineer": {"name": "A. Engineer", "credentials": "PE", "license_number": "123", "license_state": "NY"},
    "attest": true, "statement_version": 1}
   ```

   Checked in order, nothing written unless all pass:

   | Case | Answer |
   |---|---|
   | Archived, not the current revision, not exported, report skipped | 409 `BASIS-REPORT-007` |
   | Firm profile or study answers not saved | 409 `BASIS-REPORT-008` |
   | Already signed | 409 `BASIS-REPORT-009` |
   | The preview rendered now is not the one reviewed (an input or the export changed) | 412 `BASIS-REPORT-010`, the current preview's `ETag` |
   | `attest` not true, another `statement_version` | 422 |

   Then the final PDF (the signature block: time in UTC, signature id, name, credentials,
   license, firm) is stored at `rev-<n>.report.signed.<signature id>.pdf`, and the
   `report_signatures` row (one per revision, never updated) and the `report.signed` event
   (signer's email, the sha256 of the export, the reviewed preview and the signed PDF) are
   written in one transaction. The answer is the signature (201).

A firm profile or answers changed after signing do not change the signed PDF. A new revision
is signed on its own. What the signature is and is not (ESIGN/UETA elements, no PAdES
certificate, no PE seal): STUDY_REPORT_SPEC 11.13.
