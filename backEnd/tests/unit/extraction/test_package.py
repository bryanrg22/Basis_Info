"""Upload recognition (:mod:`basis.extraction.package`) and the upload-aware pipeline entry."""

from __future__ import annotations

import pytest
from uad36_fakes import azure_printing, fabricated_xml, fabricated_zip

from basis.extraction import package
from basis.extraction.package import InvalidPackage, read_appraisal_upload

PDF = b"%PDF-1.7\n% fabricated\n"


def test_a_pdf_a_bare_xml_and_a_delivery_zip_are_recognized() -> None:
    assert read_appraisal_upload(PDF).kind == "pdf"
    bare = read_appraisal_upload(fabricated_xml())
    assert bare.kind == "xml" and bare.pdf is None
    delivery = read_appraisal_upload(
        fabricated_zip(
            {"report.xml": fabricated_xml(), "report.pdf": PDF, "Images/front.jpg": b"\xff\xd8"}
        )
    )
    assert delivery.kind == "zip" and delivery.pdf == PDF and delivery.xml == fabricated_xml()
    assert "Images/front.jpg" in delivery.members and delivery.pdf_name == "report.pdf"


def test_a_zip_without_a_report_pdf_is_refused() -> None:
    """A UAD 3.6 delivery always carries its report PDF; an XML alone is uploaded as XML."""
    with pytest.raises(InvalidPackage, match="holds no PDF"):
        read_appraisal_upload(fabricated_zip({"m.xml": fabricated_xml(), "Images/a.jpg": b"x"}))


@pytest.mark.parametrize(
    ("data", "declared", "ok"),
    [
        (PDF, "application/pdf", True),
        (fabricated_xml(), "application/xml", True),
        (fabricated_xml(), "text/xml", True),
        (fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": PDF}), "application/zip", True),
        (fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": PDF}), "application/pdf", False),
        (PDF, "application/zip", False),
        (fabricated_xml(), "application/pdf", False),
        (PDF, "image/png", False),
    ],
)
def test_the_declared_type_must_name_what_the_bytes_are(
    data: bytes, declared: str, ok: bool
) -> None:
    if ok:
        assert package.check_declared(data, declared).kind == package.DECLARED_KINDS[declared]
    else:
        with pytest.raises(InvalidPackage, match="but was declared"):
            package.check_declared(data, declared)


def test_the_pdf_the_xml_names_is_chosen_among_several() -> None:
    xml = fabricated_xml().replace(
        b"</DOCUMENT>",
        b"<VIEWS><VIEW><VIEW_FILES><VIEW_FILE><FOREIGN_OBJECT><ObjectURL>\\\\b.pdf</ObjectURL>"
        b"</FOREIGN_OBJECT></VIEW_FILE></VIEW_FILES></VIEW></VIEWS></DOCUMENT>",
    )
    chosen = read_appraisal_upload(
        fabricated_zip({"m.xml": xml, "a.pdf": PDF + b"a", "b.pdf": PDF + b"b"})
    )
    assert chosen.pdf == PDF + b"b"
    with pytest.raises(InvalidPackage, match="several PDFs"):
        read_appraisal_upload(
            fabricated_zip({"m.xml": fabricated_xml(), "a.pdf": PDF, "b.pdf": PDF})
        )


@pytest.mark.parametrize("name", ["../evil.xml", "/abs/report.xml", "a\\b.xml", "C:/x.xml"])
def test_path_traversal_in_a_zip_is_refused(name: str) -> None:
    with pytest.raises(InvalidPackage, match="unsafe path"):
        read_appraisal_upload(fabricated_zip({name: fabricated_xml(), "r.pdf": PDF}))


def test_a_zip_bomb_is_refused_however_small_its_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package, "MAX_MEMBER_BYTES", 10_000)
    bomb = fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": PDF + b"0" * 50_000})
    with pytest.raises(InvalidPackage, match="larger than|expands past"):
        read_appraisal_upload(bomb)


def test_a_zip_without_one_mismo_xml_or_with_too_many_entries_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(InvalidPackage, match="0 MISMO XML"):
        read_appraisal_upload(fabricated_zip({"notes.xml": b"<notes/>", "r.pdf": PDF}))
    with pytest.raises(InvalidPackage, match="2 MISMO XML"):
        read_appraisal_upload(
            fabricated_zip({"a.xml": fabricated_xml(), "b.xml": fabricated_xml()})
        )
    monkeypatch.setattr(package, "MAX_MEMBERS", 2)
    with pytest.raises(InvalidPackage, match="entries"):
        read_appraisal_upload(fabricated_zip({"a": b"", "b": b"", "c": b""}))


def test_an_encrypted_member_and_a_corrupt_zip_are_refused() -> None:
    raw = bytearray(fabricated_zip({"m.xml": fabricated_xml()}))
    for signature, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.index(signature) + flag_at
        raw[at] |= 0x1  # the "encrypted" general purpose bit
    with pytest.raises(InvalidPackage, match="encrypted"):
        read_appraisal_upload(bytes(raw))
    with pytest.raises(InvalidPackage, match="not a readable ZIP"):
        read_appraisal_upload(b"PK\x03\x04garbage")


def test_hostile_or_unknown_bytes_are_refused() -> None:
    with pytest.raises(InvalidPackage, match="DOCTYPE"):
        read_appraisal_upload(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>')
    with pytest.raises(InvalidPackage, match="not a PDF"):
        read_appraisal_upload(b"hello")


async def test_the_pipeline_reads_a_delivery_zip_from_its_xml_and_locates_on_its_pdf() -> None:
    from basis.extraction.pipeline import ExtractionContext, extract_appraisal_upload
    from basis.llm.call import CallContext
    from basis.llm.prompts import latest
    from basis.llm.provider import Budget
    from basis.llm.recorder import MemoryRecorder

    class Azure:
        calls = 0

        async def analyze(self, pdf: bytes) -> object:
            Azure.calls += 1
            return azure_printing([["Opinion", "of", "Market", "Value", "$300,000"]])

    from basis.extraction import pipeline

    pages = type("Pdf", (), {"page_count": 1})()
    original = pipeline.read_pdf
    pipeline.read_pdf = lambda data: pages  # type: ignore[assignment,return-value]
    try:
        import uuid

        ctx = ExtractionContext(
            call=CallContext(
                study_id=uuid.uuid4(),
                run_id=None,
                activity="test",
                budget=Budget(remaining_usd=1.0),
                recorder=MemoryRecorder(),
            ),
            provider=None,
            azure=Azure(),  # type: ignore[arg-type]
            prompt=latest("appraisal_field_fallback"),
            text_model="none",
        )
        data = fabricated_zip({"m.xml": fabricated_xml(), "r.pdf": PDF})
        result = await extract_appraisal_upload(data, "doc", ctx)
    finally:
        pipeline.read_pdf = original
    value = result.fields["reconciliation.final_market_value"]
    assert value.source == "mismo_xml" and value.regions and value.page == 1
    assert Azure.calls == 1 and result.raw["upload_kind"] == "zip" and "azure_di" in result.raw
