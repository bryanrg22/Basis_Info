"""Which uploaded documents a study's appraisal is read from (docs/api/UAD36_INTAKE.md).

One rule for ``ingest_appraisal`` (the study corpus), ``extract_fields`` (the field values and
highlight regions) and the API, so the three never disagree about the source:

* **The extraction source** is the study's first confirmed UAD 3.6 delivery (an appraisal
  declared ``application/zip``, ``application/xml`` or ``text/xml``), else its first confirmed
  appraisal (a PDF: a legacy 1004 or a UAD 3.6 report, read unchanged). "First" is the order
  the documents were registered in. Declared types are trustworthy: the upload confirmation
  refuses bytes of another kind (:func:`basis.extraction.package.check_declared`).
* **The report PDF** is the PDF the corpus is ingested from and the regions are drawn on: a
  PDF source is its own report PDF; a ZIP's is the PDF inside it (chosen by
  :mod:`basis.extraction.package`); a bare XML's is its **companion**, the study's latest
  confirmed appraisal PDF (``application/pdf``, latest ``uploaded_at``, then latest
  ``created_at``) at the time the study's appraisal is ingested. ``ingest_appraisal`` picks it
  once, returns it, and the workflow passes it to every ``extract_fields`` of the study, so a
  PDF uploaded later never pairs with fields read earlier. No companion: the XML is extracted
  with no corpus and no regions (``no_report_pdf``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Final, Literal

from basis.db.enums import DocumentKind
from basis.db.models import Document
from basis.extraction.confidence import review_gate
from basis.extraction.package import is_zip, looks_like_xml
from basis.extraction.results import ExtractionResult, ValidationIssue

PDF_TYPE: Final = "application/pdf"
UAD36_TYPES: Final = frozenset({"application/zip", "application/xml", "text/xml"})
XML_TYPES: Final = frozenset({"application/xml", "text/xml"})
NO_REPORT_PDF: Final = "no_report_pdf"

UploadKind = Literal["pdf", "zip", "xml"]


def uploaded_appraisals(documents: Sequence[Document]) -> list[Document]:
    """The confirmed appraisal documents, in registration order (as listed)."""
    return [d for d in documents if d.kind is DocumentKind.APPRAISAL and d.uploaded_at is not None]


def extraction_source(documents: Sequence[Document]) -> Document | None:
    """The appraisal the fields are read from (module doc)."""
    appraisals = uploaded_appraisals(documents)
    for document in appraisals:
        if document.content_type in UAD36_TYPES:
            return document
    return appraisals[0] if appraisals else None


def companion_pdf(documents: Sequence[Document], source: Document) -> Document | None:
    """The latest confirmed appraisal PDF other than ``source`` (a bare XML's report PDF)."""
    pdfs = [
        d
        for d in uploaded_appraisals(documents)
        if d.id != source.id and d.content_type == PDF_TYPE
    ]
    if not pdfs:
        return None
    return max(pdfs, key=lambda d: (d.uploaded_at or d.created_at, d.created_at))


def is_bare_xml(document: Document) -> bool:
    return document.content_type in XML_TYPES


def report_pdf_for(documents: Sequence[Document], source: Document) -> Document | None:
    """The document whose PDF is the report of ``source``: itself, unless it is a bare XML."""
    return companion_pdf(documents, source) if is_bare_xml(source) else source


def upload_kind(data: bytes) -> UploadKind:
    """What the bytes are, as the extraction dispatches on them (never the label)."""
    if is_zip(data):
        return "zip"
    if looks_like_xml(data):
        return "xml"
    return "pdf"


def with_source(
    result: ExtractionResult,
    *,
    document_id: uuid.UUID,
    kind: UploadKind,
    pdf_document_id: uuid.UUID | None,
) -> ExtractionResult:
    """``result`` with its source identity in ``raw["source"]`` (what
    ``GET /v1/studies/{id}/extraction`` reports), and, for a bare XML with no report PDF, a
    ``no_report_pdf`` warning so the reviewer is told the values were not checked against
    the printed report (no regions, no ``xml_pdf_mismatch`` check)."""
    pdf_id = None if pdf_document_id is None else str(pdf_document_id)
    source: dict[str, Any] = {
        "document_id": str(document_id),
        "upload_kind": kind,
        "pdf_document_id": pdf_id,
    }
    raw = {**result.raw, "source": source}
    if pdf_id is not None or kind != "xml":
        return result.model_copy(update={"raw": raw})
    issue = ValidationIssue(
        rule=NO_REPORT_PDF,
        message=(
            "The UAD 3.6 XML was uploaded without its report PDF: its values have no "
            "highlight regions and were not checked against the printed report."
        ),
        severity="warning",
    )
    issues = [*result.issues, issue]
    gate = review_gate(
        result.fields, issues, threshold=float(result.raw.get("review_threshold", 0.90))
    )
    raw["issues"] = [i.model_dump(mode="json") for i in issues]
    raw["review_reasons"] = list(gate.reasons)
    return result.model_copy(
        update={
            "issues": issues,
            "overall_confidence": gate.overall_confidence,
            "needs_review": gate.needs_review,
            "review_reasons": list(gate.reasons),
            "raw": raw,
        }
    )


def recorded_pdf(raw: object) -> tuple[bool, str | None]:
    """``(known, pdf_document_id)`` recorded on an extraction's ``raw`` by :func:`with_source`;
    ``known`` is False for extractions stored before the source was recorded."""
    if not isinstance(raw, dict) or not isinstance(raw.get("source"), dict):
        return False, None
    value = raw["source"].get("pdf_document_id")
    return True, value if isinstance(value, str) else None
