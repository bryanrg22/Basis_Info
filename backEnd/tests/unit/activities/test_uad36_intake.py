"""UAD 3.6 intake in ``ingest_appraisal`` and ``extract_fields`` (docs/api/UAD36_INTAKE.md).

Activity level, real Postgres and storage, offline providers. The report PDF a ZIP, an XML
with its companion PDF or an XML alone is ingested from; the source identity on every chunk
and on the extraction; the companion pinned by ingestion (a PDF uploaded later is not used);
malformed packages refused before any PDF parser, never retried; the business key keeps a
retried ingestion on the PDF it first chose.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycletest_support import (
    SeededStudy,
    add_appraisal,
    count_rows,
    sample_appraisal_chunks,
    sample_appraisal_pdf,
    seed_run,
    seed_study,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError
from uad36_fakes import azure_printing, fabricated_xml, fabricated_zip

from basis.activities import Deps
from basis.activities.appraisal_sources import NO_REPORT_PDF
from basis.activities.extract_fields import ExtractFields, _with_corrections
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.db.models import Chunk, CorpusVersion, Extraction, ExtractionField
from basis.db.session import session_scope
from basis.extraction import package
from basis.extraction.results import ExtractedField, ExtractionResult
from basis.storage import LocalStorage
from basis.workflow.contracts import (
    CorrectionInput,
    ExtractFieldsInput,
    IngestAppraisalInput,
    ReportPdf,
)

from .conftest import Env

pytestmark = pytest.mark.integration

PDF = sample_appraisal_pdf()


def ingest(study_id: uuid.UUID, key: str = "k-ingest", revision: int = 1) -> IngestAppraisalInput:
    return IngestAppraisalInput(study_id=str(study_id), revision=revision, idempotency_key=key)


def extract(
    study: SeededStudy,
    document_id: uuid.UUID,
    report_pdf: ReportPdf | None,
    *,
    revision: int = 1,
    corrections: tuple[CorrectionInput, ...] = (),
) -> ExtractFieldsInput:
    return ExtractFieldsInput(
        study_id=str(study.study_id),
        document_id=str(document_id),
        revision=revision,
        idempotency_key=f"k-extract-{revision}",
        corrections=corrections,
        report_pdf=report_pdf,
    )


async def empty_study(
    sessionmaker: async_sessionmaker[AsyncSession], storage: LocalStorage
) -> SeededStudy:
    study = await seed_study(sessionmaker, storage=storage, appraisal=None, photos=())
    await seed_run(sessionmaker, study.study_id)
    return study


async def chunk_docs(sessionmaker: async_sessionmaker[AsyncSession]) -> set[str]:
    async with session_scope(sessionmaker) as session:
        return set((await session.scalars(select(Chunk.doc_id).distinct())).all())


class StaticAzure:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[bytes] = []

    async def analyze(self, pdf_bytes: bytes) -> Any:
        self.calls += 1
        self.seen.append(pdf_bytes)
        return azure_printing([["Opinion", "of", "Market", "Value", "$300,000"]])


def offline(deps: Deps, azure: StaticAzure) -> Deps:
    from lifecycletest_support import make_deps

    return make_deps(deps.sessionmaker, deps.storage, deps.settings, azure_di=azure, llm=None)


# --------------------------------------------------------------------------- ingestion


async def test_a_delivery_zip_ingests_its_report_pdf_under_the_zip_id(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    zip_id = await add_appraisal(
        sessionmaker,
        study,
        "delivery.zip",
        fabricated_zip({"m.xml": fabricated_xml(), "report.pdf": PDF, "Images/a.jpg": b"\xff"}),
        storage,
    )
    out = await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert (out.document_id, out.pdf_document_id, out.status) == (
        str(zip_id),
        str(zip_id),
        "ingested",
    )
    assert out.chunks == sample_appraisal_chunks() and out.corpus_version_id is not None
    assert await chunk_docs(sessionmaker) == {str(zip_id)}


async def test_an_xml_with_its_companion_pdf_ingests_the_pdf(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    xml_id = await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    pdf_id = await add_appraisal(sessionmaker, study, "report.pdf", PDF, storage)
    out = await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert (out.document_id, out.pdf_document_id, out.status) == (
        str(xml_id),
        str(pdf_id),
        "ingested",
    )
    assert await chunk_docs(sessionmaker) == {str(pdf_id)}


async def test_the_xml_is_the_source_even_when_its_pdf_was_registered_first(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    pdf_id = await add_appraisal(sessionmaker, study, "report.pdf", PDF, storage)
    xml_id = await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    out = await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert (out.document_id, out.pdf_document_id) == (str(xml_id), str(pdf_id))


async def test_an_xml_alone_has_no_corpus_and_says_so(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    xml_id = await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    activity = IngestAppraisal(deps)
    out = await activity_env.run(activity.run, ingest(study.study_id))
    again = await activity_env.run(activity.run, ingest(study.study_id))
    other_key = await activity_env.run(activity.run, ingest(study.study_id, key="k-other"))
    assert out == again == other_key
    assert (out.document_id, out.corpus_version_id, out.chunks) == (str(xml_id), None, 0)
    assert (out.pdf_document_id, out.status) == (None, "no_pdf")
    assert await count_rows(sessionmaker, CorpusVersion) == 0


async def test_a_retried_ingestion_keeps_the_pdf_it_chose_when_a_newer_one_arrives(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    first_pdf = await add_appraisal(sessionmaker, study, "report.pdf", PDF, storage)
    activity = IngestAppraisal(deps)
    first = await activity_env.run(activity.run, ingest(study.study_id))
    await add_appraisal(sessionmaker, study, "newer.pdf", PDF + b"\n% newer\n", storage)
    retried = await activity_env.run(activity.run, ingest(study.study_id, key="k-retry"))
    assert first == retried and retried.pdf_document_id == str(first_pdf)
    assert await count_rows(sessionmaker, CorpusVersion) == 1


MANY_PDFS = {"m.xml": fabricated_xml(), "a.pdf": PDF, "b.pdf": PDF}


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("d.zip", fabricated_zip({"m.xml": fabricated_xml()}), "holds no PDF"),
        ("d.zip", fabricated_zip(MANY_PDFS), "several PDFs"),
        ("d.zip", fabricated_zip({"notes.xml": b"<notes/>", "r.pdf": PDF}), "0 MISMO XML"),
        ("d.zip", fabricated_zip({"../m.xml": fabricated_xml(), "r.pdf": PDF}), "unsafe path"),
        ("d.zip", b"PK\x03\x04garbage", "not a readable ZIP"),
        ("d.zip", fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": b"nope"}), "not a PDF"),
        (
            "r.xml",
            b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "x">]><MESSAGE>&a;</MESSAGE>',
            "DOCTYPE",
        ),
        ("r.xml", b"<MESSAGE><unclosed></MESSAGE>", "XML"),
    ],
)
async def test_a_bad_package_is_refused_before_any_pdf_parser_and_not_retried(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
    name: str,
    content: bytes,
    message: str,
) -> None:
    study = await empty_study(sessionmaker, storage)
    await add_appraisal(sessionmaker, study, name, content, storage)
    with pytest.raises(ApplicationError, match=message) as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert isinstance(caught.value.__cause__, package.InvalidPackage)  # not a PDF parser's
    assert await count_rows(sessionmaker, CorpusVersion) == 0


async def test_a_zip_bomb_is_refused_by_the_archive_limits(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package, "MAX_MEMBER_BYTES", 10_000)
    study = await empty_study(sessionmaker, storage)
    bomb = fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": b"%PDF-1.7\n" + b"0" * 200_000})
    await add_appraisal(sessionmaker, study, "bomb.zip", bomb, storage)
    with pytest.raises(ApplicationError, match="larger than|expands past") as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert caught.value.non_retryable


async def test_a_companion_that_is_not_a_pdf_is_refused(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    fake = await add_appraisal(sessionmaker, study, "report.pdf", b"not a pdf at all", storage)
    with pytest.raises(ApplicationError, match=f"{fake}.*not a PDF") as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert caught.value.non_retryable


# --------------------------------------------------------------------------- extraction


async def stored(sessionmaker: async_sessionmaker[AsyncSession], extraction_id: str) -> Extraction:
    async with session_scope(sessionmaker) as session:
        row = await session.get(Extraction, uuid.UUID(extraction_id))
        assert row is not None
        return row


async def field_rows(
    sessionmaker: async_sessionmaker[AsyncSession], extraction_id: str
) -> dict[str, ExtractionField]:
    async with session_scope(sessionmaker) as session:
        rows = await session.scalars(
            select(ExtractionField).where(ExtractionField.extraction_id == uuid.UUID(extraction_id))
        )
        return {f"{r.section}.{r.field}": r for r in rows}


async def test_the_pinned_report_pdf_wins_over_a_newer_upload(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    xml_id = await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    pinned = await add_appraisal(sessionmaker, study, "report.pdf", PDF, storage)
    newer = PDF + b"\n% newer\n"
    await add_appraisal(sessionmaker, study, "newer.pdf", newer, storage)
    azure = StaticAzure()
    out = await activity_env.run(
        ExtractFields(offline(deps, azure)).run,
        extract(study, xml_id, ReportPdf(document_id=str(pinned))),
    )
    assert out.extraction_id is not None and azure.seen == [PDF]
    row = await stored(sessionmaker, out.extraction_id)
    assert row.raw["source"] == {
        "document_id": str(xml_id),
        "upload_kind": "xml",
        "pdf_document_id": str(pinned),
    }
    assert (await field_rows(sessionmaker, out.extraction_id))[
        "reconciliation.final_market_value"
    ].regions


async def test_an_xml_without_a_report_pdf_is_flagged_and_has_no_regions(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    xml_id = await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    # A PDF exists, but ingestion chose none (it arrived later): the pin is "no PDF".
    await add_appraisal(sessionmaker, study, "late.pdf", PDF, storage)
    azure = StaticAzure()
    out = await activity_env.run(
        ExtractFields(offline(deps, azure)).run, extract(study, xml_id, ReportPdf(document_id=None))
    )
    assert out.extraction_id is not None and out.needs_review and azure.calls == 0
    row = await stored(sessionmaker, out.extraction_id)
    assert row.raw["source"]["pdf_document_id"] is None
    assert NO_REPORT_PDF in row.raw["review_reasons"]
    assert [i["rule"] for i in row.raw["issues"]].count(NO_REPORT_PDF) == 1
    rows = await field_rows(sessionmaker, out.extraction_id)
    assert rows and all(r.regions == [] for r in rows.values())
    assert rows["reconciliation.final_market_value"].source == "mismo_xml"


async def test_a_zip_is_its_own_report_pdf(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    zip_id = await add_appraisal(
        sessionmaker,
        study,
        "d.zip",
        fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": PDF}),
        storage,
    )
    await add_appraisal(sessionmaker, study, "other.pdf", PDF + b"% other", storage)
    azure = StaticAzure()
    out = await activity_env.run(
        ExtractFields(offline(deps, azure)).run,
        extract(study, zip_id, ReportPdf(document_id=str(zip_id))),
    )
    assert out.extraction_id is not None and azure.seen == [PDF]
    row = await stored(sessionmaker, out.extraction_id)
    assert row.raw["source"] == {
        "document_id": str(zip_id),
        "upload_kind": "zip",
        "pdf_document_id": str(zip_id),
    }


async def test_a_pinned_pdf_of_another_study_is_refused(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
) -> None:
    study = await empty_study(sessionmaker, storage)
    xml_id = await add_appraisal(sessionmaker, study, "report.xml", fabricated_xml(), storage)
    with pytest.raises(ApplicationError, match="not a confirmed appraisal") as caught:
        await activity_env.run(
            ExtractFields(deps).run,
            extract(study, xml_id, ReportPdf(document_id=str(uuid.uuid4()))),
        )
    assert caught.value.non_retryable
    assert await count_rows(sessionmaker, Extraction) == 0


class _Target:
    def __init__(self, key: str) -> None:
        self.section, self.field = key.split(".", 1)
        self.page = 1


class _Repo:
    def __init__(self, key: str) -> None:
        self.key = key

    async def get_field(self, study_id: uuid.UUID, field_id: uuid.UUID) -> _Target:
        return _Target(self.key)


async def test_corrections_keep_reader_warnings_and_the_forms_missing_keys() -> None:
    """Regression: after a rejection with corrections, an ``xml_pdf_mismatch`` on a key the
    reviewer did not correct and a ``no_report_pdf`` stayed out of the issues, and a UAD 3.6
    PDF's keys the form does not carry came back as ``missing:`` reasons."""
    value = ExtractedField(
        key="reconciliation.final_market_value",
        raw_text="$300,000",
        value_text="$300,000",
        confidence=1.0,
        source="mismo_xml",
    )
    result = ExtractionResult(
        doc_id="d",
        method="mismo_xml",
        page_count=1,
        fields={value.key: value},
        issues=[],
        raw={
            "review_threshold": 0.9,
            "form": {"form": "uad36_urar", "not_on_form": ["improvements.total_rooms"]},
        },
    )
    from basis.extraction.results import ValidationIssue

    result.issues.extend(
        [
            ValidationIssue(
                rule="xml_pdf_mismatch", message="m", keys=("subject.county",), severity="warning"
            ),
            ValidationIssue(rule=NO_REPORT_PDF, message="n", severity="warning"),
        ]
    )
    correction = CorrectionInput(
        entity_type="extraction_field",
        entity_id=str(uuid.uuid4()),
        after={"value_text": "$310,000"},
    )
    out = await _with_corrections(
        _Repo("reconciliation.final_market_value"),  # type: ignore[arg-type]
        uuid.uuid4(),
        result,
        (correction,),
    )
    rules = [i.rule for i in out.issues]
    assert "xml_pdf_mismatch" in rules and NO_REPORT_PDF in rules
    assert "missing:improvements.total_rooms" not in out.review_reasons
