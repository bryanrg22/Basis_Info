"""UAD 3.6 intake through the API (docs/api/UAD36_INTAKE.md).

* ``POST .../documents/{id}/complete`` reads an appraisal: a PDF, a MISMO 3.6 XML or a safe
  UAD 3.6 ZIP of its declared type is confirmed; anything else is 422 ``invalid-appraisal``,
  the object deleted and the document left unconfirmed (photos are not read).
* ``GET .../documents/{id}/pdf``: the PDF to render (a ZIP's report PDF; 409 for an XML).
* ``GET .../extraction``: the revision's source, report PDF, issues and study corpus; each
  field of ``GET .../fields`` names its ``pdf_document_id``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer, sha256_hex
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uad36_fakes import fabricated_xml, fabricated_zip

from basis.db.enums import DocumentKind, Stage
from basis.db.models import Chunk, CorpusVersion, Document, Extraction, ExtractionField
from basis.db.repositories import DocumentRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage, ObjectNotFound

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"report body " * 100
ZIP = fabricated_zip({"m.xml": fabricated_xml(), "report.pdf": PDF, "Images/a.jpg": b"\xff"})
XML = fabricated_xml()


async def complete(
    api: Api, storage: LocalStorage, study_id: str, data: bytes, filename: str, content_type: str
) -> tuple[Any, dict[str, Any]]:
    ticket = (
        await api.request_upload(
            "alice", study_id, data, filename=filename, content_type=content_type
        )
    ).json()
    storage.put_signed(ticket["upload"]["url"], data, ticket["upload"]["headers"])
    response = await api.post(
        "alice", f"/v1/studies/{study_id}/documents/{ticket['document']['id']}/complete"
    )
    return response, ticket["document"]


# --------------------------------------------------------------------------- confirmation


@pytest.mark.parametrize(
    ("data", "filename", "content_type"),
    [
        (PDF, "appraisal.pdf", "application/pdf"),
        (ZIP, "delivery.zip", "application/zip"),
        (XML, "report.xml", "application/xml"),
        (XML, "report.xml", "text/xml"),
    ],
)
async def test_a_usable_appraisal_is_confirmed(
    api: Api, storage: LocalStorage, data: bytes, filename: str, content_type: str
) -> None:
    study = await api.create_study("alice")
    response, _ = await complete(api, storage, study["id"], data, filename, content_type)
    assert response.status_code == 200, response.text
    assert response.json()["uploaded_at"] is not None


BAD = {
    "zip-without-pdf": (fabricated_zip({"m.xml": XML}), "application/zip", "holds no PDF"),
    "zip-two-pdfs": (
        fabricated_zip({"m.xml": XML, "a.pdf": PDF, "b.pdf": PDF}),
        "application/zip",
        "several PDFs",
    ),
    "zip-no-xml": (fabricated_zip({"r.pdf": PDF}), "application/zip", "0 MISMO XML"),
    "zip-traversal": (
        fabricated_zip({"../m.xml": XML, "r.pdf": PDF}),
        "application/zip",
        "unsafe path",
    ),
    "corrupt-zip": (b"PK\x03\x04garbage", "application/zip", "not a readable ZIP"),
    "hostile-xml": (
        b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "x">]><MESSAGE>&a;</MESSAGE>',
        "application/xml",
        "DOCTYPE",
    ),
    "zip-labelled-pdf": (ZIP, "application/pdf", "is a ZIP but was declared"),
    "pdf-labelled-xml": (PDF, "application/xml", "is a PDF but was declared"),
    "not-an-appraisal": (b"hello world", "application/pdf", "not a PDF"),
}


@pytest.mark.parametrize("case", sorted(BAD))
async def test_an_unusable_appraisal_is_refused_at_confirmation(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    case: str,
) -> None:
    data, content_type, reason = BAD[case]
    study = await api.create_study("alice")
    response, document = await complete(api, storage, study["id"], data, "a.bin", content_type)
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["type"].endswith("/invalid-appraisal") and reason in problem["detail"]
    async with sessionmaker() as session:
        row = await session.get(Document, uuid.UUID(document["id"]))
    assert row is not None and row.uploaded_at is None
    with pytest.raises(ObjectNotFound):
        await storage.read(row.gcs_uri)
    # Unconfirmed: the workflow never sees it, and alone it cannot start the study
    # (test_workflow_routes.py, ``appraisal-unconfirmed``).


async def test_photos_are_not_read_as_appraisals(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    ticket = (
        await api.request_upload(
            "alice", study["id"], b"PK\x03\x04", kind="photo", content_type="image/png"
        )
    ).json()
    storage.put_signed(ticket["upload"]["url"], b"PK\x03\x04", ticket["upload"]["headers"])
    response = await api.post(
        "alice", f"/v1/studies/{study['id']}/documents/{ticket['document']['id']}/complete"
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- report PDF


def pdf_path(study_id: str, document_id: str) -> str:
    return f"/v1/studies/{study_id}/documents/{document_id}/pdf"


async def test_the_report_pdf_of_a_pdf_a_zip_and_an_xml(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    _, pdf = await complete(api, storage, sid, PDF, "a.pdf", "application/pdf")
    _, package = await complete(api, storage, sid, ZIP, "d.zip", "application/zip")
    _, xml = await complete(api, storage, sid, XML, "r.xml", "application/xml")

    own = await api.get("alice", pdf_path(sid, pdf["id"]))
    assert own.status_code == 200 and own.content == PDF
    assert own.headers["content-type"] == "application/pdf"

    inner = await api.get("alice", pdf_path(sid, package["id"]))
    assert inner.status_code == 200 and inner.content == PDF
    assert inner.headers["content-type"] == "application/pdf"
    assert inner.headers["etag"] == f'"{sha256_hex(ZIP)}"'
    assert 'filename="report.pdf"' in inner.headers["content-disposition"]
    cached = await api.client.get(
        pdf_path(sid, package["id"]),
        headers={**bearer("alice"), "If-None-Match": inner.headers["etag"]},
    )
    assert cached.status_code == 304

    none = await api.get("alice", pdf_path(sid, xml["id"]))
    assert none.status_code == 409 and none.json()["type"].endswith("/no-report-pdf")


async def test_no_report_pdf_for_a_photo_or_an_unconfirmed_upload(api: Api) -> None:
    study = await api.create_study("alice")
    ticket = (await api.request_upload("alice", study["id"], PDF)).json()
    pending = await api.get("alice", pdf_path(study["id"], ticket["document"]["id"]))
    assert pending.status_code == 409 and pending.json()["type"].endswith("/upload-missing")
    missing = await api.get("alice", pdf_path(study["id"], str(uuid.uuid4())))
    assert missing.status_code == 404


# --------------------------------------------------------------------------- extraction


async def seed_extraction(
    api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession], *, with_pdf: bool
) -> tuple[str, uuid.UUID, uuid.UUID | None, uuid.UUID]:
    """An XML extraction at revision 1 (and its companion PDF and corpus when ``with_pdf``);
    the study is at revision 2, which has no extraction."""
    study = await api.create_study("alice")
    sid = uuid.UUID(study["id"])
    await seed.stage(sid, Stage.ROOMS_REVIEW, revision=2)
    async with session_scope(sm) as session:
        documents = DocumentRepository(session)
        ids: dict[str, uuid.UUID] = {}
        for name, content_type in (("r.xml", "application/xml"), ("r.pdf", "application/pdf")):
            ids[name] = uuid.uuid4()
            await documents.create(
                study_id=sid,
                kind=DocumentKind.APPRAISAL,
                filename=name,
                content_type=content_type,
                size_bytes=10,
                sha256=uuid.uuid4().hex * 2,
                gcs_uri=f"gs://basis-local/studies/{sid}/{ids[name]}/{name}",
                uploaded_by=None,
                document_id=ids[name],
            )
        pdf_id = ids["r.pdf"] if with_pdf else None
        issues = [
            {
                "rule": "xml_pdf_mismatch",
                "message": "subject.county prints differently",
                "keys": ["subject.county"],
                "severity": "warning",
            }
        ]
        if not with_pdf:
            issues.append(
                {"rule": "no_report_pdf", "message": "no PDF", "keys": [], "severity": "warning"}
            )
        extraction = Extraction(
            study_id=sid,
            document_id=ids["r.xml"],
            revision=1,
            method="mismo_xml+azure_di" if with_pdf else "mismo_xml",
            overall_confidence=1.0,
            needs_review=True,
            raw={
                "source": {
                    "document_id": str(ids["r.xml"]),
                    "upload_kind": "xml",
                    "pdf_document_id": None if pdf_id is None else str(pdf_id),
                },
                "issues": issues,
                "review_reasons": [f"{i['rule']}" for i in issues],
                "notes": ["a note"],
            },
        )
        session.add(extraction)
        await session.flush()
        session.add(
            ExtractionField(
                extraction_id=extraction.id,
                study_id=sid,
                section="subject",
                field="county",
                value_text="Faketown",
                confidence=1.0,
                source="mismo_xml",
                page=1 if with_pdf else None,
                regions=[{"page": 1, "bbox": [0.1, 0.1, 0.2, 0.2]}] if with_pdf else [],
            )
        )
        if with_pdf:
            version = CorpusVersion(corpus="study", study_id=sid, version=1, embedding_model="h")
            session.add(version)
            await session.flush()
            session.add(
                Chunk(
                    corpus_version_id=version.id,
                    doc_id=str(pdf_id),
                    chunk_key=f"{pdf_id}:1:0",
                    page_start=1,
                    page_end=1,
                    text="Property Address 100 Test Ln",
                )
            )
            await session.flush()
            from datetime import UTC, datetime

            version.published_at = datetime.now(UTC)
        return str(sid), ids["r.xml"], pdf_id, extraction.id


async def test_the_extraction_names_its_source_report_pdf_issues_and_corpus(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, xml_id, pdf_id, extraction_id = await seed_extraction(
        api, seed, sessionmaker, with_pdf=True
    )
    response = await api.get("alice", f"/v1/studies/{sid}/extraction?revision=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction_id"] == str(extraction_id) and body["revision"] == 1
    assert (body["document_id"], body["upload_kind"], body["pdf_document_id"]) == (
        str(xml_id),
        "xml",
        str(pdf_id),
    )
    assert body["regions_available"] is True and body["form"] is None
    assert body["issues"] == [
        {
            "rule": "xml_pdf_mismatch",
            "severity": "warning",
            "keys": ["subject.county"],
            "message": "subject.county prints differently",
        }
    ]
    assert body["review_reasons"] == ["xml_pdf_mismatch"] and body["notes"] == ["a note"]
    assert body["corpus"]["status"] == "ingested" and body["corpus"]["chunks"] == 1
    assert body["corpus"]["document_id"] == str(pdf_id)

    fields = (await api.get("alice", f"/v1/studies/{sid}/fields?revision=1")).json()["items"]
    assert [(f["document_id"], f["pdf_document_id"]) for f in fields] == [
        (str(xml_id), str(pdf_id))
    ]
    # The current revision (2) has no extraction.
    current = await api.get("alice", f"/v1/studies/{sid}/extraction")
    assert current.status_code == 404


async def test_an_xml_without_a_pdf_has_no_report_pdf_regions_or_corpus(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, xml_id, _, _ = await seed_extraction(api, seed, sessionmaker, with_pdf=False)
    body = (await api.get("alice", f"/v1/studies/{sid}/extraction?revision=1")).json()
    assert (body["pdf_document_id"], body["regions_available"]) == (None, False)
    assert {i["rule"] for i in body["issues"]} == {"xml_pdf_mismatch", "no_report_pdf"}
    assert body["corpus"] == {
        "status": "none",
        "corpus_version_id": None,
        "document_id": None,
        "chunks": 0,
    }
    [field] = (await api.get("alice", f"/v1/studies/{sid}/fields?revision=1")).json()["items"]
    assert field["pdf_document_id"] is None and field["regions"] == []


async def test_an_extraction_stored_before_the_source_was_recorded(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, xml_id, _, extraction_id = await seed_extraction(api, seed, sessionmaker, with_pdf=False)
    async with session_scope(sessionmaker) as session:
        row = await session.get(Extraction, extraction_id)
        assert row is not None
        document_id = row.document_id
        row.raw = {}  # a legacy PDF extraction: no source recorded
    body = (await api.get("alice", f"/v1/studies/{sid}/extraction?revision=1")).json()
    assert (body["upload_kind"], body["pdf_document_id"]) == ("pdf", str(document_id))
    assert body["issues"] == [] and body["review_reasons"] == []
