# Renovation projects: `GET` and `PUT /v1/studies/{study_id}/renovations`

A renovation the taxpayer made after placing the building in service is a separate asset with
its own placed-in-service date, priced from its invoices, and it may remove components the
acquisition study priced; one made before the building was placed in service is part of the
acquisition. Design and sources: `docs/review_2026-10-15/RENOVATIONS.md`; measured in
`docs/review_2026-10-15/EVAL_REPORT.md` section 102. Code: `basis/domain/renovations.py` (the
document), `basis/api/routes/renovations.py`, `basis/evidence/renovations.py` (what the result
makes of it), migration `0039_study_renovations`.

The ages of the seller's renovated components and the lines a renovation removed are
corrections at the assets review, not part of this document: `docs/api/OVERRIDES.md` section 7.

## 1. Reading

`GET /v1/studies/{study_id}/renovations`, viewer role or above.

```json
{"study_id": "5f0c6a52-...", "renovations": null, "renovations_version": 0}
```

`ETag` is the version in quotes (`"0"` while there is none). 404 for a study the caller cannot
see (another firm's study answers exactly as a missing one).

## 2. Writing

`PUT /v1/studies/{study_id}/renovations`, engineer role or above, the whole document:

```json
{
  "projects": [
    {
      "key": "kitchen-2025",
      "kind": "after_service",
      "scope": "Kitchen remodel: cabinets, countertops and flooring replaced",
      "addition": false,
      "contract_date": "2025-02-10",
      "work_start_date": "2025-03-03",
      "placed_in_service_date": "2025-05-30",
      "invoice_total": "38500.00",
      "invoices": [{"document_id": "<an upload of kind other>", "amount": "38500.00",
                    "invoice_date": "2025-05-28", "description": "Final invoice"}],
      "capitalized_amount": "38500.00",
      "capitalized_confirmed_by": "A. Preparer, CPA",
      "after_photos": ["<photo document id>", "..."],
      "qip": "auto",
      "aerial": {"last_without": {"date": "2024-10-01", "reference": "view 12"},
                 "first_with": {"date": "2025-06-15", "reference": "view 13"}}
    }
  ],
  "acquisition_bonus_rate": null,
  "exterior_brackets": []
}
```

| Member | Meaning |
|---|---|
| `key` | The engineer's short key (`^[a-z0-9][a-z0-9-]{0,39}$`); removal corrections name it. Unique. |
| `kind` | `after_service`: its own schedule and dispositions. `before_service`: bought, renovated, then placed in service; part of the acquisition (no date of its own, no photographs tagged, no dispositions; its capitalized cost is the intake line `improvements_before_service`). |
| `addition` | The project enlarged the building: never QIP, and no dispositions unless `removals_stated` is true. |
| `contract_date` | The written binding contract: the bonus acquisition date (Notice 2026-11 section 3.03). Optional. |
| `work_start_date` | When physical work of a significant nature began: the bonus acquisition date without a contract. Required. |
| `placed_in_service_date` | Required for `after_service`, absent for `before_service`. |
| `invoice_total`, `invoices` | Positive; the invoices (uploads of kind `other`), when listed, sum to the total. |
| `capitalized_amount`, `capitalized_confirmed_by` | What the CPA confirms is capitalized, at most the invoice total, with who confirmed it. Until it is given the project's schedule is withheld (blocker `renovation_capitalized`). |
| `after_photos` | Photographs (uploads of kind `photo`) of the finished work: their components are the project's. A photograph in one project only. |
| `qip` | `auto` (the rules of RENOVATIONS.md 4.3) or `off` (the CPA's call). |
| `aerial` | The last dated view without the work and the first with it: corroboration only. |
| `acquisition_bonus_rate` | The bonus actually claimed on the acquisition's 5, 7 and 15-year property (0..1, the CPA's); null: the statutory default for the intake's dates. |
| `exterior_brackets` | `[{label, bracket}]`: a component label's dated views; a row's year installed outside it is a warning. |

`If-Match` as on the intake (docs/api/STUDY_RESULT.md): the current tag or `*` (over an existing
document) writes and increments `renovations_version`; another tag is 412; absent writes. The
answer is the stored document with its new `ETag`. The `study.renovations_set` timeline event
keeps the document before and after with `detail` `{version_before, version_after, if_match}`.

| Status | Code | When |
|---|---|---|
| 422 `validation` | `BASIS-API-013` | the body's own rules (above): unknown member, a date order, invoices not summing, a duplicate key, photograph or invoice, a capitalized amount above the total or without who confirmed it, a before-service project with a date or photographs |
| 422 `validation` | `BASIS-STUDY-040` | a file that is not the study's (or an invoice not of kind `other`, a photograph not of kind `photo`), or dates the intake contradicts: an after-service project placed in service on or before the building, or started before it; a before-service project started before the acquisition or after the building's date (`errors[].loc` names the member) |
| 403 `forbidden` | | a viewer |
| 404 `not-found` | | no such study, or one the caller cannot see |
| 409 `renovations-locked` | `BASIS-STUDY-038` | the current revision is exporting or complete: reopen the study (docs/api/REVISIONS.md) at the assets review to change it |
| 409 `stale-state` | `BASIS-STUDY-004` | the study is archived |
| 412 `precondition-failed` | `BASIS-STUDY-039` | `If-Match` is not the current tag; the `ETag` names it |

**When they can change.** From creation until the assets review is approved. The result reads
the document as it is now; the assets were built with the version recorded on their takeoffs,
and a different version withholds the allocation (blocker `renovations_changed`) until a reprice
(`POST /v1/studies/{id}/assets/reprice`) builds them again: tagging photographs moves rows between
the schedules, which only the assets step can do (the structure model must not credit a
project's rows against the building).

## 3. What the result does with it

`GET /v1/studies/{id}/result` gains `renovations` (docs/api/STUDY_RESULT.md, "Renovations"): each
project's schedule, the dispositions, the before-service projects, and the checks. Absent for a
study with no renovations document, so a study without renovations answers byte for byte as
before. The report and the preparer's schedule print them (docs/api/REPORT.md, "Renovations").

## 4. Blockers it can add to the result

| Code | Fix |
|---|---|
| `renovations_changed` | reprice the assets |
| `renovation_dates` | correct the project's dates |
| `renovation_capitalized` | enter the CPA's capitalized amount and who confirmed it |
| `renovation_rows` | tag the project's photographs, or add its components at the components review |
| `renovation_unknown_project` | reprice (the rows name a project no longer listed) |
| `renovation_removal` | correct the removal (unknown or before-service project, an addition without stated removals, a date before the building was in service, more than the building's remaining basis, a project's own row) |
| `renovation_bonus` | enter `acquisition_bonus_rate`, or the intake's acquisition date |
| `renovation_before_service` | make the intake's `improvements_before_service` lines the before-service projects' capitalized amounts |
| `component_age` | correct or clear the row's age (OVERRIDES section 7) |

Warnings (in `renovations.checks`, never withholding): `renovation_invoices` (no invoice
attached), `renovation_estimate_gap` (estimates more than 10 % from the actual cost),
`renovation_contract_after_work`, `disposition_outside_project`, `disposition_fully_depreciated`,
`dispositions_by_year`, `aerial_bracket`.

## 5. Deployment

Migration 0039 before either process; the worker before the API (an older worker ignores the
document and writes no project membership; an older API refuses the age and removal fields).
