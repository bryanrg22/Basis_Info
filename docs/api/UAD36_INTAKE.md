# UAD 3.6 (MISMO 3.6) appraisal intake

The redesigned URAR (UAD 3.6) is mandatory for GSE submissions from 2026-11-02. A UAD 3.6
delivery is a ZIP holding the MISMO 3.6 XML (root `MESSAGE`, namespace
`http://www.mismo.org/residential/2009/schemas`), the PDF of the report and an `Images` folder.

## What an appraisal upload may be

`POST /v1/studies/{id}/documents` with `kind: "appraisal"` accepts `content_type`
`application/pdf`, `application/zip`, `application/xml` or `text/xml` (same 50 MB limit).

| Upload | Values from | Study corpus (evidence, citations) and highlight regions from |
|---|---|---|
| PDF (legacy 1004 or UAD 3.6) | the PDF (Azure DI layout + text layer + model fallback), read by its form's captions (below) | the PDF |
| UAD 3.6 ZIP | the XML | the ZIP's report PDF (the one the XML names in `VIEW_FILE/FOREIGN_OBJECT/ObjectURL`, else the only one) |
| bare MISMO 3.6 XML | the XML | its **companion PDF** (below); none if the study has no appraisal PDF |

UAD 3.6 support does not change the PDF path: a legacy 1004 or a UAD 3.6 report uploaded as a
PDF alone is ingested and extracted as before. The one difference for PDFs: a file declared
`application/pdf` whose bytes are not a PDF is now refused when the upload is confirmed
(below) instead of failing the study at ingestion.

### Checked when the upload is confirmed

`POST .../documents/{doc_id}/complete` reads every appraisal (`basis/extraction/package.py`,
`check_declared`): the bytes must be a PDF, a MISMO 3.6 XML or a safe UAD 3.6 ZIP, **of the kind
the declared `content_type` names** (a ZIP declared `application/pdf` is refused). Otherwise the
answer is `422` with problem type `.../invalid-appraisal` and a `detail` naming the problem; the
stored object is deleted and the document stays unconfirmed (`uploaded_at: null`), so the
workflow never sees it. Photos and other documents are not read.

```json
{
  "type": "https://basis.invalid/problems/invalid-appraisal",
  "title": "Unprocessable Content",
  "status": 422,
  "detail": "Not a usable appraisal: the ZIP holds no PDF of the report. The object was deleted; the document stays unconfirmed."
}
```

Refused (the package rules apply again at ingestion, as a non-retryable `ValidationError` that
fails the study at `extracting` with the reason `ingest_appraisal: ValidationError: appraisal
<id>: <problem>`, for anything confirmed before this check existed):
an XML with a DOCTYPE, entity declarations or external entities; a root that is not a MISMO
`MESSAGE`; an XML over 25 MB; a ZIP with an absolute, `..`, drive or backslash path, an
encrypted entry, more than 2,000 entries, an entry that expands past 60 MB (whatever its header
claims) or 120 MB in all, no MISMO XML or more than one, **no PDF**, several PDFs the XML does
not choose between, or a chosen PDF that is not a PDF. A ZIP or an XML never reaches a PDF
parser.

### Which document is the source, which PDF is the report

One rule (`basis/activities/appraisal_sources.py`) for the corpus, the extraction and the API:

* **Extraction source**: the study's first confirmed UAD 3.6 delivery (an appraisal declared
  `application/zip`, `application/xml` or `text/xml`, in registration order), else its first
  confirmed appraisal. A study with a PDF and an XML is read from the XML whichever was
  registered first.
* **Companion PDF of a bare XML**: the study's **latest confirmed** appraisal declared
  `application/pdf` (latest `uploaded_at`, then latest `created_at`), other than the XML, **at
  the moment the study's appraisal is ingested** (the start of the workflow). It is chosen
  once: `ingest_appraisal` returns it and the workflow passes it to every `extract_fields` of
  the study (`report_pdf`), so a PDF uploaded after the start (before a rejection, or while a
  failed stage waits for its retry) never pairs with the XML. Upload the XML and its PDF
  before starting the study. The frontend may register the XML before its companion (as it
  does) or after it.
* **No companion**: the XML is extracted alone. No study corpus is written (`corpus.status:
  "none"`), the classifier's study search answers that no study corpus exists (its citations
  are to the reference corpus only), fields have no regions, and the extraction carries the
  warning issue `no_report_pdf` (so the appraisal review is flagged). The study runs to
  completion; the evidence endpoint lists reference citations only.

Every corpus chunk's `doc_id` is the document its PDF was read from: the PDF itself, **the
ZIP** (its report PDF is inside it), or the companion PDF. `GET .../documents/{doc_id}/pdf`
returns the PDF to render for any of them.

### The PDF to render: `GET /v1/studies/{id}/documents/{doc_id}/pdf`

A confirmed appraisal's PDF, `application/pdf`: a PDF document's own bytes, or the report PDF
inside a UAD 3.6 ZIP (the same member the extraction and the corpus use; `Content-Disposition`
names it). Strong `ETag` = the document's sha256 (`If-None-Match` -> 304), cached like
`/content`. `409 .../no-report-pdf` for a bare XML (render the extraction's
`pdf_document_id`), a photo, or a package that no longer reads; `409 .../upload-missing`
before confirmation; `404` for an unknown document.

### The extraction of a revision: `GET /v1/studies/{id}/extraction[?revision=n]`

Viewer access; the current revision by default; `404` when the revision has no extraction
(only the first revision and appraisal rejections extract; a rooms or assets rejection does
not), like `/fields` being empty there.

```json
{
  "revision": 1,
  "extraction_id": "6f1c...",
  "document_id": "0b7e...",
  "upload_kind": "xml",
  "pdf_document_id": "9d42...",
  "regions_available": true,
  "form": null,
  "method": "mismo_xml+azure_di",
  "needs_review": true,
  "overall_confidence": 1.0,
  "review_reasons": ["xml_pdf_mismatch:subject.county"],
  "issues": [
    {
      "rule": "xml_pdf_mismatch",
      "severity": "warning",
      "keys": ["subject.county"],
      "message": "the XML value 'Faketown' is not printed on the PDF next to County"
    }
  ],
  "notes": ["the report PDF is the study's separately uploaded appraisal PDF"],
  "corpus": {
    "status": "ingested",
    "corpus_version_id": "51aa...",
    "document_id": "9d42...",
    "chunks": 31
  },
  "created_at": "2026-10-28T17:02:11Z"
}
```

| Member | Meaning |
|---|---|
| `document_id` | The extraction source (the fields' `document_id`). |
| `upload_kind` | `pdf` (legacy 1004 or UAD 3.6 PDF), `zip` (UAD 3.6 delivery), `xml` (bare MISMO 3.6 XML). |
| `pdf_document_id` | The document whose PDF this revision's regions are drawn on: `document_id` for `pdf` and `zip`, the companion for `xml`, `null` when there is none. Render it with `.../documents/{pdf_document_id}/pdf`. It is recorded on the extraction itself, so fields are never paired with another PDF, and it is the same for every revision of a study. |
| `regions_available` | At least one field has a region. |
| `form` | A PDF's printed form: `urar_1004_2005`, `uad36_urar` or `unknown` (read as 1004); `null` for `zip`/`xml`. |
| `issues` | `rule`, `severity` (`error`/`warning`), `keys`, `message`. Reader issues: `xml_pdf_mismatch`, `no_report_pdf`, `azure_di_rejected` (a PDF upload Azure DI refused: text layer only), `sources_disagree`; validator rules (`missing_critical`, ...). Kept through a rejection's corrections unless the reviewer corrected every key they name. |
| `review_reasons` | Why the gate flagged it: `missing:<key>`, `low_confidence:<key>=<c><<t>`, `<rule>[:<keys>]`. |
| `notes` | Reader notes, informational (not stable text). |
| `corpus.status` | `ingested` (the report PDF is searchable evidence), `scanned` (no text layer: an empty corpus), `none` (no corpus: a bare XML with no PDF). `corpus.document_id` is the citations' `doc_id`. |

Each item of `GET /v1/studies/{id}/fields` also carries `pdf_document_id` (the same value, per
field), next to `document_id`.

## A UAD 3.6 report uploaded as a PDF alone

`basis/extraction/forms.py` tells the two URARs apart by their page footers: the legacy 1004's
"Fannie Mae Form 1004 / Freddie Mac Form 70 March 2005" and the redesigned URAR's A-1 footer
("Fannie Mae | Freddie Mac" with the Appraiser or Client Reference ID). Two or more UAD 3.6
pages that outnumber 1004 pages make a UAD 3.6 report; anything else takes the 1004 path,
unchanged. The result records the form in `raw["form"]` (with the pages that carry each footer
and, for UAD 3.6, the catalogue keys the form does not carry) and the method gets `+uad36`.

On a UAD 3.6 PDF (`basis/extraction/uad36_pdf.py`), Azure DI key-value pairs and the text
layer are mapped by the A-1 captions, scoped by the A-1 section printed above them and, for a
caption several boxes share, by the nearest printed A-1 subsection ("Company Name" under
"Client/Lender" vs under "Appraiser"); the comparable grids are never read. Keys whose value
combines several printed boxes or a table row are left to the model fallback, which uses the
prompt `appraisal_field_fallback_uad36` and is told each key's UAD 3.6 captions and sections.
Keys the redesigned form does not carry (`no_equivalent` / `unresolved` in `uad36_map`) are
neither extracted, asked nor required by the review gate (the form has no total room count).

## How XML values and PDF regions combine

* `basis/extraction/uad36_map.py` (generated by `python -m evals.appraisal.uad36_spec` from the
  GSE URAR Delivery Specification, Appendix A-1 v1.4, and the Redesign-to-Legacy Cross
  Reference Guide, Appendix G-1 v1.4) names, per catalogue key, the MISMO data points (A-1
  Unique IDs, xPaths), the enumerations' report display text, the captions, and why a key has
  no UAD 3.6 equivalent (30 keys: tax year, census tract, neighborhood land use, FEMA map, ...).
* `basis/extraction/mismo36.py` reads the subject property only
  (`PROPERTY[@ValuationUseType="SubjectProperty"]`; comparables never), narrows parties by role
  and cost lines, data sources and signatures through the xlink relationships, renders each
  value as the report prints it (`$489,000`, `09/20/2019`, enumerations as their A-1 display
  text), and cleans it with the production cleaners: fields have `source: "mismo_xml"` and
  confidence 1.0. An approach the Scope of Work marks not developed leaves its keys empty.
  For a 2-4 unit property the per-unit keys (occupant when units differ, bedrooms, bathrooms)
  stay empty, `improvements.gla_sqft` is the gross building finished area and
  `improvements.units` the unit count.
* Each value is then located on the report PDF's Azure DI layout (the ZIP's, or the companion
  chosen at ingestion; an earlier revision's analysis is reused only for the same PDF): the words it prints as, the
  occurrence nearest one of its UAD 3.6 captions, on a page where a caption is printed. That
  box is the field's `regions` (`GET /v1/studies/{id}/fields`, `docs/api/FIELD_REGIONS.md`);
  `page` is the first located page.

## The `xml_pdf_mismatch` issue

An XML value the PDF does not print on any page where its caption is printed (or anywhere,
when no caption is found) gets no region and a `warning` issue `xml_pdf_mismatch` naming the
key: the delivery's XML and PDF disagree and the reviewer is told, rather than the XML being
silently preferred. Derived values (a basement finish percentage, a unit count, `Yes`/`No`)
are located through the printed values they come from and never raise it. Known limit: a one-
or two-digit value found anywhere on its caption's page passes.

## Measured (replay, 2026-10-27)

`python -m evals.appraisal.run --set gse_d1|client_uad36 --mode replay --system mismo_xml`;
the 12 GSE Appendix D-1 samples and one client appraisal converted to UAD 3.6 by us (its XML
was generated from its truth, so agreement there tests consistency, not real-world accuracy).
Numbers and caveats: `evals/appraisal` run reports and the EVAL notes of this change.
