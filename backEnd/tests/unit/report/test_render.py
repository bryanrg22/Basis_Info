"""``basis.report.render``: the PDF carries every figure of the report, states the inputs
honestly, and renders to the same bytes every time (STUDY_REPORT_SPEC 11.8, 11.9)."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime

import pymupdf
import pytest
from PIL import Image

from basis.errors.codes import E
from basis.report.inputs import ReportInputs, Signature, StudyAnswers
from basis.report.model import ReportError, build_report
from basis.report.render import ExhibitPhoto, render_pdf, usd, view

from .support import export
from .test_model import ENGINEER, full_inputs


def text_of(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def pages_of(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


def fonts_of(pdf: bytes) -> set[str]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return {f[3] for page in doc for f in page.get_fonts()}


def test_every_figure_of_the_report_is_in_the_pdf() -> None:
    report = build_report(export(), full_inputs(), report_date=date(2026, 10, 31))
    text = " ".join(text_of(render_pdf(report)).split())
    for figure in (
        "$61,000.00",
        "$9,500.00",
        "$1,800.00",
        "$3,000.00",
        "$56,200.00",
        "$4,800.00",
        "$50,000.00",
    ):
        assert figure in text, figure
    for line in report.items:
        assert usd(line.allocated) in text and line.description in text
    for words in (
        "Example Holdings LLC",
        "Example Engineering",
        "A. Engineer",
        "No site visit was made",
        "I did not inspect the property",
        "Signed in Basis",
        "09 68 10.10 0100",
        "typical unit cost",
        "(estimated)",
        "(contested)",
        "40 %",
        "Whole property",
        "seen in Front, Rear, Side",
        "the site value is 14 months older than the contract",
        "Exhibit C",
        "C.13",
    ):
        assert words in text, words
    assert "DRAFT" not in text


def test_a_report_without_inputs_says_so_and_never_claims_a_visit() -> None:
    text = " ".join(text_of(render_pdf(build_report(export()))).split())
    assert "DRAFT" in text and "Missing: firm, study, signature" in text
    assert "[taxpayer name]" in text and "Not yet signed" in text
    assert "I inspected" not in text and "Signed in Basis" not in text


def test_a_site_visit_is_stated_only_when_it_happened() -> None:
    visit = full_inputs().model_copy(
        update={
            "study": StudyAnswers(
                taxpayer_name="T",
                photographs="engineer_site_visit",
                photographed_by="A. Engineer",
                photographed_on=date(2025, 2, 20),
            )
        }
    )
    text = " ".join(text_of(render_pdf(build_report(export(), visit))).split())
    assert "I inspected the property on February 20, 2025" in text
    assert "No site visit was made" not in text


def test_the_same_report_renders_to_the_same_bytes_with_the_bundled_font() -> None:
    report = build_report(export(), full_inputs(), report_date=date(2026, 10, 31))
    pdf = render_pdf(report)
    assert pdf == render_pdf(build_report(export(), full_inputs(), report_date=date(2026, 10, 31)))
    assert pdf.startswith(b"%PDF-")
    assert {f.split("+")[-1] for f in fonts_of(pdf)} <= {
        "IBMPlexSans",
        "IBMPlexSans-Bold",
        "IBMPlexSans-SemiBold",
        "IBMPlexSans-Italic",
    }
    assert pages_of(pdf) == 8  # cover, contents, sections 1-10, exhibits, appendix


def test_photographs_go_into_exhibit_a_and_unsafe_names_are_refused() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 140, 160)).save(buffer, "JPEG")
    photo = ExhibitPhoto("photo-01.jpg", buffer.getvalue(), "Kitchen: 1 refrigerator")
    report = build_report(export())
    with_photo = render_pdf(report, (photo,))
    assert "Kitchen: 1 refrigerator" in text_of(with_photo)
    assert pages_of(with_photo) >= pages_of(render_pdf(report))
    with pytest.raises(ReportError) as e:
        render_pdf(report, (ExhibitPhoto("../x.jpg", b"", ""),))
    assert e.value.code == E.REPORT_RENDER_FAILED


def test_the_view_holds_only_formatted_text() -> None:
    data = view(build_report(export(), full_inputs(), report_date=date(2026, 10, 31)))
    assert data["depreciable"] == "$61,000.00" and data["report_date"] == "October 31, 2026"
    assert data["signed"] == {"at": "October 31, 2026 15:00 UTC"}
    assert data["classes"][0] == {
        "name": "5-year property",
        "section": "1245",
        "amount": "$1,800.00",
        "share": "3.0 %",
        "items": "2",
    }
    unsigned = ReportInputs(
        signature=Signature(engineer=ENGINEER, signed_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    assert view(build_report(export(), unsigned))["engineer"]["name"] == "A. Engineer"


def test_unpriced_systems_are_disclosed_in_the_limiting_conditions() -> None:
    doc = export()
    doc["summary"]["basis"]["not_produced"] = [
        {"system": "Fire Protection & Alarm", "reason": "no cost row"},
        {"system": "Site Utilities", "reason": "no cost row"},
    ]
    text = " ".join(text_of(render_pdf(build_report(doc))).split())
    assert "no priced line in the schedule" in text
    assert "Fire Protection & Alarm, Site Utilities" in text
    assert "no priced line" not in " ".join(text_of(render_pdf(build_report(export()))).split())
