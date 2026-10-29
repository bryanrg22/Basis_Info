"""UAD 3.6 intake through the whole study-start path (docs/api/UAD36_INTAKE.md).

The real StudyWorkflow on the time-skipping Temporal test server, the real activities on
Postgres and local storage, offline providers (the fixture Azure DI result, the hash embedder,
the scripted classifier; no paid call). Each upload kind goes start -> ingest_appraisal ->
extract_fields -> the appraisal review, and the test checks what reached the database: which
document the corpus chunks came from, the extraction's source and report PDF, the issues.

* a legacy 1004 PDF and a UAD 3.6 PDF: ingested and extracted as PDFs, unchanged;
* a UAD 3.6 ZIP: the package's PDF is the corpus (chunks under the ZIP's id), the XML the values;
* an XML with its companion PDF: the PDF is the corpus and the regions, the XML the values;
  the companion is pinned at ingestion (a PDF uploaded later, then a rejection or a retry,
  never changes it);
* an XML alone: no corpus, no regions, ``no_report_pdf``; the study still runs to completion
  (rooms, components, assets with reference-only citations, the evidence, the export);
* malformed packages: the study fails at ``extracting`` with the package's reason, ingestion
  tried once (non-retryable), extraction never scheduled;
* a transient ingestion error is retried by Temporal; a duplicate start joins the running
  execution and ingests once.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pymupdf
import pytest
from lifecycle_support import Harness
from lifecycletest_support import (
    DEFAULT_PHOTOS,
    SeededStudy,
    add_appraisal,
    make_deps,
    sample_appraisal_pdf,
    seed_study,
)
from sqlalchemy import select
from temporalio import activity
from uad36_fakes import azure_printing, fabricated_xml, fabricated_zip

from basis.activities.appraisal_sources import NO_REPORT_PDF
from basis.activities.extract_fields import ExtractFields
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.db.enums import Corpus, StudyStatus
from basis.db.models import Asset, Chunk, Citation, CorpusVersion, Extraction, ExtractionField
from basis.db.repositories.evidence import EvidenceRepository
from basis.db.session import session_scope
from basis.extraction import package
from basis.workflow.contracts import (
    CorrectionInput,
    ExtractFieldsInput,
    ExtractFieldsOutput,
    IngestAppraisalInput,
    IngestAppraisalOutput,
)
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PDF = sample_appraisal_pdf()


def uad36_pdf() -> bytes:
    """A two-page redesigned URAR: the A-1 footer on every page (basis.extraction.forms)."""
    doc = pymupdf.open()
    for number in (1, 2):
        page = doc.new_page()
        page.insert_text((72, 72), "Uniform Residential Appraisal Report")
        page.insert_text((72, 100), "Property Address 77 Redesign Ave")
        page.insert_text((72, 128), "Year Built 2004")
        page.insert_text((72, 740), f"Appraisal Version #4   Page {number} of 2")
        page.insert_text((72, 755), "Fannie Mae | Freddie Mac September 2024")
        page.insert_text((72, 770), "Appraiser Reference ID AA12345  Client Reference ID 1234567")
    data: bytes = doc.tobytes()
    doc.close()
    return data


class Counting:
    """The real ``ingest_appraisal``, counting calls; the first ``transient`` calls fail with
    a retryable error (a storage or database blip)."""

    def __init__(self, harness: Harness, transient: int = 0) -> None:
        self.real = IngestAppraisal(harness.deps)
        self.transient = transient
        self.calls = 0

    @activity.defn(name="ingest_appraisal")
    async def run(self, input: IngestAppraisalInput) -> IngestAppraisalOutput:
        self.calls += 1
        if self.calls <= self.transient:
            raise ConnectionError("storage blip")
        return await self.real.run(input)


class PrintingAzure:
    """An Azure DI layout that prints the fabricated XML's market value (so it is located)."""

    def __init__(self) -> None:
        self.seen: list[bytes] = []

    async def analyze(self, pdf: bytes) -> Any:
        self.seen.append(pdf)
        return azure_printing([["Opinion", "of", "Market", "Value", "$300,000"]])


class SeenExtract:
    """The real ``extract_fields`` (with its own deps), recording each input."""

    def __init__(self, harness: Harness, **deps: Any) -> None:
        self.real = ExtractFields(
            make_deps(harness.sessionmaker, harness.deps.storage, harness.settings, **deps)
            if deps
            else harness.deps
        )
        self.inputs: list[ExtractFieldsInput] = []
        self.fail_first = False

    @activity.defn(name="extract_fields")
    async def run(self, input: ExtractFieldsInput) -> ExtractFieldsOutput:
        self.inputs.append(input)
        if self.fail_first and len(self.inputs) == 1:
            from temporalio.exceptions import ApplicationError

            raise ApplicationError("broken", type="ProviderUnavailable", non_retryable=True)
        return await self.real.run(input)


async def seeded(
    harness: Harness, *appraisals: tuple[str, bytes]
) -> tuple[SeededStudy, list[uuid.UUID]]:
    study = await seed_study(
        harness.sessionmaker, photos=DEFAULT_PHOTOS, appraisal=None, storage=harness.deps.storage
    )
    ids = [
        await add_appraisal(harness.sessionmaker, study, name, data, harness.deps.storage)
        for name, data in appraisals
    ]
    return study, ids


async def extractions(harness: Harness, study_id: uuid.UUID) -> list[Extraction]:
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.scalars(
            select(Extraction).where(Extraction.study_id == study_id).order_by(Extraction.revision)
        )
        return list(rows.all())


async def corpus(harness: Harness, study_id: uuid.UUID) -> tuple[int, set[str], int]:
    """(study corpus versions, the documents their chunks came from, chunks)."""
    async with session_scope(harness.sessionmaker) as session:
        versions = list(
            (
                await session.scalars(
                    select(CorpusVersion.id).where(
                        CorpusVersion.corpus == Corpus.STUDY.value,
                        CorpusVersion.study_id == study_id,
                    )
                )
            ).all()
        )
        docs = list(
            (
                await session.scalars(
                    select(Chunk.doc_id).where(Chunk.corpus_version_id.in_(versions))
                )
            ).all()
        )
    return len(versions), set(docs), len(docs)


async def regions(harness: Harness, extraction_id: uuid.UUID) -> int:
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.scalars(
            select(ExtractionField).where(ExtractionField.extraction_id == extraction_id)
        )
        return sum(1 for r in rows if r.regions)


async def to_appraisal_review(harness: Harness, study: SeededStudy) -> tuple[Any, Extraction]:
    started = await harness.start(study)
    await harness.wait_awaiting(started.handle, "appraisal_review", 1)
    [extraction] = await extractions(harness, study.study_id)
    return started.handle, extraction


# --------------------------------------------------------------------------- PDFs


async def test_a_legacy_1004_pdf_is_ingested_and_extracted_as_before(harness: Harness) -> None:
    study, [pdf_id] = await seeded(harness, ("appraisal.pdf", PDF))
    async with harness.worker():
        _, extraction = await to_appraisal_review(harness, study)
    assert extraction.document_id == pdf_id and extraction.method.startswith("azure_di")
    # The synthetic sample prints no form footer: the legacy path ("unknown" reads as 1004).
    assert extraction.raw["form"]["form"] != "uad36_urar" and "+uad36" not in extraction.method
    assert extraction.raw["source"] == {
        "document_id": str(pdf_id),
        "upload_kind": "pdf",
        "pdf_document_id": str(pdf_id),
    }
    versions, docs, chunks = await corpus(harness, study.study_id)
    assert (versions, docs) == (1, {str(pdf_id)}) and chunks > 0


async def test_a_uad36_pdf_is_ingested_and_extracted_as_a_pdf(harness: Harness) -> None:
    study, [pdf_id] = await seeded(harness, ("urar.pdf", uad36_pdf()))
    extract = SeenExtract(harness, azure_di=None, llm=None)
    async with harness.worker(extract_fields=extract.run):
        _, extraction = await to_appraisal_review(harness, study)
    assert extraction.raw["form"]["form"] == "uad36_urar" and "+uad36" in extraction.method
    assert extraction.raw["source"]["upload_kind"] == "pdf"
    assert extraction.raw["source"]["pdf_document_id"] == str(pdf_id)
    versions, docs, chunks = await corpus(harness, study.study_id)
    assert (versions, docs) == (1, {str(pdf_id)}) and chunks > 0
    [seen] = extract.inputs
    assert seen.report_pdf is not None and seen.report_pdf.document_id == str(pdf_id)


# --------------------------------------------------------------------------- UAD 3.6 deliveries


async def test_a_delivery_zip_feeds_the_corpus_its_pdf_and_the_fields_its_xml(
    harness: Harness,
) -> None:
    delivery = fabricated_zip({"m.xml": fabricated_xml(), "report.pdf": PDF, "Images/a.jpg": b"x"})
    study, [zip_id] = await seeded(harness, ("delivery.zip", delivery))
    async with harness.worker():
        _, extraction = await to_appraisal_review(harness, study)
    assert extraction.document_id == zip_id and extraction.method.startswith("mismo_xml")
    assert extraction.raw["source"] == {
        "document_id": str(zip_id),
        "upload_kind": "zip",
        "pdf_document_id": str(zip_id),
    }
    versions, docs, chunks = await corpus(harness, study.study_id)
    assert (versions, docs) == (1, {str(zip_id)}) and chunks > 0
    async with session_scope(harness.sessionmaker) as session:
        sources = set(
            (
                await session.scalars(
                    select(ExtractionField.source).where(
                        ExtractionField.extraction_id == extraction.id
                    )
                )
            ).all()
        )
    assert sources == {"mismo_xml"}


async def test_an_xml_and_its_pdf_stay_paired_through_a_rejection_and_a_later_upload(
    harness: Harness,
) -> None:
    study, [xml_id, pdf_id] = await seeded(
        harness, ("report.xml", fabricated_xml()), ("report.pdf", PDF)
    )
    azure = PrintingAzure()
    extract = SeenExtract(harness, azure_di=azure, llm=None)
    async with harness.worker(extract_fields=extract.run):
        handle, first = await to_appraisal_review(harness, study)
        assert first.document_id == xml_id
        assert first.raw["source"]["pdf_document_id"] == str(pdf_id)
        versions, docs, _ = await corpus(harness, study.study_id)
        assert (versions, docs) == (1, {str(pdf_id)})
        assert await regions(harness, first.id) > 0  # located on the companion's layout
        # A newer PDF arrives while the review is open; the reviewer rejects with a fix.
        await add_appraisal(
            harness.sessionmaker, study, "newer.pdf", PDF + b"\n% newer\n", harness.deps.storage
        )
        async with session_scope(harness.sessionmaker) as session:
            target = await session.scalar(
                select(ExtractionField)
                .where(ExtractionField.extraction_id == first.id)
                .order_by(ExtractionField.section, ExtractionField.field)
                .limit(1)
            )
        assert target is not None
        fix = CorrectionInput(
            entity_type="extraction_field",
            entity_id=str(target.id),
            after={"value_text": "Corrected by the reviewer"},
        )
        await harness.wait_photos(handle, 3)
        await harness.decide(
            handle, harness.decision("appraisal_review", 1, "reject", corrections=[fix])
        )
        await harness.wait_awaiting(handle, "appraisal_review", 2)
    first_again, second = await extractions(harness, study.study_id)
    assert second.revision == 2 and second.document_id == xml_id
    assert second.raw["source"]["pdf_document_id"] == str(pdf_id)  # not the newer PDF
    assert [i.report_pdf.document_id for i in extract.inputs if i.report_pdf] == [
        str(pdf_id),
        str(pdf_id),
    ]
    assert (await corpus(harness, study.study_id))[:2] == (1, {str(pdf_id)})
    # Analyzed once, the companion's bytes; revision 2 reused that analysis (same PDF).
    assert azure.seen == [PDF]


async def test_a_retried_extraction_keeps_the_companion_chosen_before_the_failure(
    harness: Harness,
) -> None:
    study, [_, pdf_id] = await seeded(
        harness, ("report.xml", fabricated_xml()), ("report.pdf", PDF)
    )
    extract = SeenExtract(harness)
    extract.fail_first = True
    async with harness.worker(extract_fields=extract.run):
        started = await harness.start(study)
        handle = started.handle
        await harness.wait_for(handle, lambda s: s.failed_stage is not None, "the failure")
        await add_appraisal(
            harness.sessionmaker, study, "newer.pdf", PDF + b"\n% newer\n", harness.deps.storage
        )
        await handle.execute_update(StudyWorkflow.retry)
        await harness.wait_awaiting(handle, "appraisal_review", 1)
    [extraction] = await extractions(harness, study.study_id)
    assert extraction.raw["source"]["pdf_document_id"] == str(pdf_id)
    assert [i.report_pdf.document_id for i in extract.inputs if i.report_pdf] == [str(pdf_id)] * 2
    assert (await corpus(harness, study.study_id))[:2] == (1, {str(pdf_id)})


async def test_an_xml_alone_reaches_review_without_a_corpus_and_completes(
    harness: Harness,
) -> None:
    study, [xml_id] = await seeded(harness, ("report.xml", fabricated_xml()))
    async with harness.worker():
        handle, extraction = await to_appraisal_review(harness, study)
        assert extraction.document_id == xml_id and extraction.needs_review
        assert extraction.raw["source"] == {
            "document_id": str(xml_id),
            "upload_kind": "xml",
            "pdf_document_id": None,
        }
        assert NO_REPORT_PDF in extraction.raw["review_reasons"]
        assert await regions(harness, extraction.id) == 0
        assert await corpus(harness, study.study_id) == (0, set(), 0)
        status = await harness.status(handle)
        assert "appraisal_review" in status.needs_review_stages
        # Every later stage runs without a study corpus or a PDF.
        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed" and result.export_uri
    async with session_scope(harness.sessionmaker) as session:
        assets = (
            await session.scalars(select(Asset).where(Asset.study_id == study.study_id))
        ).all()
        cited = (
            await session.scalars(
                select(Chunk.corpus_version_id)
                .join(Citation, Citation.chunk_id == Chunk.id)
                .where(Citation.study_id == study.study_id)
            )
        ).all()
        study_versions = (
            await session.scalars(
                select(CorpusVersion.id).where(CorpusVersion.corpus == Corpus.STUDY.value)
            )
        ).all()
        page = await EvidenceRepository(session).page(study.study_id, 1, limit=50)
    assert assets and cited and not set(cited) & set(study_versions)
    assert len(page.items) == len(assets)
    row = await harness.study_row(study.study_id)
    assert row.status is StudyStatus.COMPLETED


# --------------------------------------------------------------------------- refusals


BAD_PACKAGES = {
    "zip-without-pdf": ("d.zip", fabricated_zip({"m.xml": fabricated_xml()}), "holds no PDF"),
    "zip-with-two-pdfs": (
        "d.zip",
        fabricated_zip({"m.xml": fabricated_xml(), "a.pdf": PDF, "b.pdf": PDF}),
        "several PDFs",
    ),
    "zip-without-xml": ("d.zip", fabricated_zip({"r.pdf": PDF}), "0 MISMO XML"),
    "zip-traversal": (
        "d.zip",
        fabricated_zip({"../../m.xml": fabricated_xml(), "r.pdf": PDF}),
        "unsafe path",
    ),
    "hostile-xml": (
        "r.xml",
        b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "x">]><MESSAGE>&a;</MESSAGE>',
        "DOCTYPE",
    ),
    "not-mismo-xml": ("r.xml", b"<?xml version='1.0'?><VALUATION_RESPONSE/>", "MESSAGE|MISMO"),
}


@pytest.mark.parametrize("case", sorted(BAD_PACKAGES))
async def test_a_bad_package_fails_the_study_at_ingestion_without_retrying(
    harness: Harness, case: str
) -> None:
    name, data, reason = BAD_PACKAGES[case]
    study, _ = await seeded(harness, (name, data))
    ingest = Counting(harness)
    extract = SeenExtract(harness)
    async with harness.worker(ingest_appraisal=ingest.run, extract_fields=extract.run):
        started = await harness.start(study)
        status = await harness.wait_for(
            started.handle, lambda s: s.failed_stage is not None, "the failure"
        )
    assert status.failed_stage == "extracting" and status.failure is not None
    assert status.failure.startswith("ingest_appraisal: ValidationError")
    assert re.search(reason, status.failure), status.failure
    assert ingest.calls == 1 and extract.inputs == []
    row = await harness.study_row(study.study_id)
    assert row.status is StudyStatus.FAILED and row.failure_reason is not None
    assert await corpus(harness, study.study_id) == (0, set(), 0)


async def test_an_archive_over_the_limits_fails_the_study(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(package, "MAX_MEMBER_BYTES", 10_000)
    bomb = fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": b"%PDF-1.7\n" + b"0" * 500_000})
    study, _ = await seeded(harness, ("bomb.zip", bomb))
    async with harness.worker():
        started = await harness.start(study)
        status = await harness.wait_for(
            started.handle, lambda s: s.failed_stage is not None, "the failure"
        )
    assert status.failure is not None and "larger than 10000 bytes" in status.failure


# --------------------------------------------------------------------------- retries, starts


async def test_a_transient_ingestion_error_is_retried(harness: Harness) -> None:
    delivery = fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": PDF})
    study, [zip_id] = await seeded(harness, ("d.zip", delivery))
    ingest = Counting(harness, transient=1)
    async with harness.worker(ingest_appraisal=ingest.run):
        _, extraction = await to_appraisal_review(harness, study)
    assert ingest.calls == 2 and extraction.document_id == zip_id
    assert (await corpus(harness, study.study_id))[:2] == (1, {str(zip_id)})


async def test_a_duplicate_start_joins_the_running_study_and_ingests_once(
    harness: Harness,
) -> None:
    study, [xml_id, pdf_id] = await seeded(
        harness, ("report.xml", fabricated_xml()), ("report.pdf", PDF)
    )
    ingest = Counting(harness)
    async with harness.worker(ingest_appraisal=ingest.run):
        first = await harness.start(study)
        second = await harness.start(study)
        await harness.wait_awaiting(first.handle, "appraisal_review", 1)
        assert (await first.handle.describe()).run_id == (await second.handle.describe()).run_id
    assert ingest.calls == 1
    [extraction] = await extractions(harness, study.study_id)
    assert (extraction.document_id, extraction.raw["source"]["pdf_document_id"]) == (
        xml_id,
        str(pdf_id),
    )
    assert (await corpus(harness, study.study_id))[:2] == (1, {str(pdf_id)})
