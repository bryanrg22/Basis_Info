"""Which uploaded documents a study's appraisal is read from (pure rules, no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from uad36_fakes import fabricated_xml

from basis.activities.appraisal_sources import (
    NO_REPORT_PDF,
    companion_pdf,
    extraction_source,
    recorded_pdf,
    report_pdf_for,
    with_source,
)
from basis.db.enums import DocumentKind
from basis.db.models import Document
from basis.extraction.mismo36 import extract_mismo

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def doc(
    content_type: str,
    minute: int,
    *,
    uploaded: int | None = 0,
    kind: DocumentKind = DocumentKind.APPRAISAL,
) -> Document:
    """A document registered at ``T0 + minute`` and confirmed ``uploaded`` minutes later."""
    created = T0 + timedelta(minutes=minute)
    return Document(
        id=uuid.uuid4(),
        kind=kind,
        content_type=content_type,
        created_at=created,
        uploaded_at=None if uploaded is None else created + timedelta(minutes=uploaded),
    )


def test_a_pdf_alone_is_the_source_and_its_own_report() -> None:
    pdf = doc("application/pdf", 0)
    photo = doc("image/jpeg", 1, kind=DocumentKind.PHOTO)
    assert extraction_source([pdf, photo]) is pdf
    assert report_pdf_for([pdf, photo], pdf) is pdf


def test_the_first_uad36_delivery_is_the_source_whatever_its_place() -> None:
    pdf, xml, zip_ = doc("application/pdf", 0), doc("text/xml", 1), doc("application/zip", 2)
    assert extraction_source([pdf, xml, zip_]) is xml
    assert extraction_source([pdf, zip_]) is zip_
    assert report_pdf_for([pdf, zip_], zip_) is zip_  # the ZIP holds its own PDF


def test_unconfirmed_documents_are_ignored() -> None:
    xml, pdf = doc("application/xml", 0, uploaded=None), doc("application/pdf", 1)
    assert extraction_source([xml, pdf]) is pdf
    assert extraction_source([xml]) is None


def test_the_companion_is_the_latest_confirmed_appraisal_pdf() -> None:
    xml = doc("application/xml", 0)
    early = doc("application/pdf", 1, uploaded=10)  # confirmed last
    late = doc("application/pdf", 2, uploaded=1)
    other = doc("application/pdf", 3, kind=DocumentKind.OTHER, uploaded=20)
    pending = doc("application/pdf", 4, uploaded=None)
    docs = [xml, early, late, other, pending]
    assert companion_pdf(docs, xml) is early and report_pdf_for(docs, xml) is early
    assert companion_pdf([xml], xml) is None


def test_with_source_flags_an_xml_without_a_pdf_and_records_the_identity() -> None:
    xml_id, pdf_id = uuid.uuid4(), uuid.uuid4()
    result = extract_mismo(fabricated_xml(), str(xml_id), layout=None)
    paired = with_source(result, document_id=xml_id, kind="xml", pdf_document_id=pdf_id)
    assert paired.raw["source"] == {
        "document_id": str(xml_id),
        "upload_kind": "xml",
        "pdf_document_id": str(pdf_id),
    }
    assert NO_REPORT_PDF not in paired.review_reasons and recorded_pdf(paired.raw) == (
        True,
        str(pdf_id),
    )
    alone = with_source(result, document_id=xml_id, kind="xml", pdf_document_id=None)
    assert alone.needs_review and NO_REPORT_PDF in alone.review_reasons
    assert NO_REPORT_PDF in alone.raw["review_reasons"]
    assert [i["rule"] for i in alone.raw["issues"]].count(NO_REPORT_PDF) == 1
    assert recorded_pdf({}) == (False, None)
