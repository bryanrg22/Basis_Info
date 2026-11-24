"""The report and the preparer's schedule with renovations (EVAL_REPORT 102): each renovation's
schedule with its own dates and bonus default, footing per schedule and in total; the
disposition table with the method statement and the election note; the components on their
own age; the evidence list; the PDF renders with them and, without them, prints nothing new.
Fabricated values on the synthetic export of ``support``."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pymupdf
import pytest

from basis.db.enums import Bucket
from basis.domain.renovations import StudyRenovations
from basis.errors.codes import E
from basis.evidence.renovations import ProjectRow, compute
from basis.report.model import ReportError, build_report
from basis.report.render import render_pdf
from basis.report.schedule import COLUMNS, rows, to_csv, to_xlsx

from .support import _asset, export
from .test_model import full_inputs

D = Decimal
KEY = "kitchen-2026"
PROJECT = {
    "key": "kitchen-2026",
    "kind": "after_service",
    "scope": "Kitchen remodel with new cabinets and tile",
    "contract_date": "2026-01-05",
    "work_start_date": "2026-01-20",
    "placed_in_service_date": "2026-03-15",
    "invoice_total": "6000.00",
    "invoices": [
        {"document_id": "00000000-0000-4000-8000-00000000f001", "amount": "6000.00",
         "invoice_date": "2026-03-10", "description": "Acme Kitchens final invoice"}
    ],
    "capitalized_amount": "6000.00",
    "capitalized_confirmed_by": "A. Preparer, CPA",
    "after_photos": ["00000000-0000-4000-8000-00000000f101"],
    "aerial": {
        "last_without": {"date": "2025-10-01", "reference": "view 3"},
        "first_with": {"date": "2026-04-01", "reference": "view 4"},
    },
}  # fmt: skip


def with_renovations() -> dict[str, Any]:
    """The support export plus a kitchen remodel: two new rows (estimates 2,000 and 3,000,
    capitalized 6,000) and the carpet line (allocated 1,000) removed on 2026-01-20, and the
    window line (6,200) depreciated on its own age."""
    doc = export()
    doc["assets"] = [
        *doc["assets"],
        _asset("r1", "kitchen_cabinet", "y5", room="Kitchen", total="2000.00"),
        _asset("r2", "tile", "y27_5", room="Kitchen", total="3000.00", section="1250"),
    ]
    out, blockers = compute(
        StudyRenovations.model_validate({"projects": [PROJECT]}),
        1,
        1,
        intake=None,
        acquisition=None,
        project_rows=[
            ProjectRow("r1", "kitchen_cabinet", "Cabinets", "y5", D("2000.00"), KEY),
            ProjectRow("r2", "tile", "Tile", "y27_5", D("3000.00"), KEY),
        ],
    )  # fmt: skip
    assert out is not None and blockers == []
    renovations = out.model_dump(mode="json")
    from basis.report.macrs import allowed
    from basis.report.tax import TERMS

    carpet = allowed(D("1000.00"), TERMS[Bucket.Y5], date(2025, 3, 1), date(2026, 1, 20), D(1))
    renovations["dispositions"] = [
        {
            "asset_id": "a1", "label": "carpet", "category": "Carpet", "project": "kitchen-2026",
            "bucket": "y5", "parent": "own_asset", "parent_placed_in_service": "2025-03-01",
            "disposition_date": "2026-01-20", "tax_year": 2026, "share": "1",
            "unadjusted_basis": str(carpet.basis), "bonus": str(carpet.bonus),
            "depreciation_by_year": [], "allowed_or_allowable": str(carpet.total),
            "adjusted_basis": str(carpet.adjusted_basis), "treatment": "whole_asset",
            "method": carpet.note, "reason": "removed",
        }
    ]  # fmt: skip
    basis = doc["summary"]["basis"]
    basis["renovations"] = renovations
    basis["component_ages"] = [
        {
            "asset_id": "a7", "label": "window", "category": "Window", "effective_age": "3",
            "year_installed": 2022, "economic_life": "30", "rate": "0.100000",
            "replaced_rate": "0.10", "source": "permit", "source_detail": "permit 22-114",
            "reason": "r", "rcn": "6500.00", "rcnld": "5850.00",
        }
    ]  # fmt: skip
    return doc


def text_of(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(" ".join(page.get_text() for page in doc).split())


def test_each_schedule_has_its_own_dates_and_foots() -> None:
    report = build_report(with_renovations(), full_inputs())
    (r,) = report.renovations
    assert (r.acquired, r.acquired_source) == (date(2026, 1, 5), "contract date")
    assert r.placed_in_service == date(2026, 3, 15) and r.capitalized == D("6000.00")
    assert [i.allocated for i in r.items] == [D("2400.00"), D("3600.00")]
    # Lines continue after the acquisition's.
    assert [i.line for i in r.items] == [len(report.items) + 1, len(report.items) + 2]
    schedule = rows(report)
    by_line = {row.values[0]: dict(zip(COLUMNS, row.values, strict=True)) for row in schedule}
    first = by_line[str(r.items[0].line)]
    assert (first["date_acquired"], first["date_placed_in_service"]) == ("2026-01-05", "2026-03-15")
    assert first["statutory_bonus_rate"] == "1.00" and "Notice 2026-11" in first["bonus_source"]
    tile = by_line[str(r.items[1].line)]
    assert tile["recovery_period"] == "27.5" and tile["statutory_bonus_rate"] == ""
    acquisition = [row for row in schedule if row.values[11] == "2024-06-18"]
    assert sum((row.basis for row in acquisition), D(0)) == report.depreciable_basis
    assert sum((row.basis for row in schedule), D(0)) == D("61000.00") + D("6000.00")
    assert to_csv(schedule) == to_csv(rows(build_report(with_renovations(), full_inputs())))
    assert to_xlsx(schedule) == to_xlsx(rows(build_report(with_renovations(), full_inputs())))


def test_the_dispositions_and_ages_are_lines_of_the_acquisition() -> None:
    report = build_report(with_renovations(), full_inputs())
    (d,) = report.dispositions
    carpet = next(i for i in report.items if "a1" in i.asset_ids)
    assert d.line == carpet.line and d.adjusted == D("0.00") and d.allowed == D("1000.00")
    (a,) = report.ages
    window = next(i for i in report.items if "a7" in i.asset_ids)
    assert a.line == window.line and a.year_installed == 2022
    assert any(f.label == "Component ages" for f in report.methodology)


def test_a_renovation_schedule_that_does_not_foot_is_refused() -> None:
    doc = with_renovations()
    doc["summary"]["basis"]["renovations"]["projects"][0]["rows"][0]["allocated"] = "2400.01"
    with pytest.raises(ReportError) as info:
        build_report(doc)
    assert info.value.code == E.REPORT_TOTALS_DO_NOT_TIE


def test_a_project_row_missing_from_the_export_is_refused() -> None:
    doc = with_renovations()
    doc["assets"] = [a for a in doc["assets"] if a["id"] != "r2"]
    with pytest.raises(ReportError) as info:
        build_report(doc)
    assert info.value.code == E.REPORT_ASSET_UNACCOUNTED


def test_the_pdf_prints_the_renovation_the_dispositions_and_the_election_note() -> None:
    text = text_of(
        render_pdf(build_report(with_renovations(), full_inputs(), report_date=date(2026, 10, 31)))
    )
    for expected in (
        "Renovations placed in service after the building",
        "Kitchen remodel with new cabinets and tile",
        "2026-03-15",
        "$6,000.00",
        "$2,400.00",
        "Dispositions of replaced components",
        "partial disposition election",
        "cannot be made later by filing Form 3115",
        "Components depreciated on their own age",
        "permit 22-114",
        "Renovation evidence",
        "corroboration only",
        "1 invoices",
    ):
        assert expected in text, expected


def test_without_renovations_nothing_new_is_printed() -> None:
    text = text_of(
        render_pdf(build_report(export(), full_inputs(), report_date=date(2026, 10, 31)))
    )
    for absent in (
        "Renovations placed",
        "Dispositions of replaced",
        "own age",
        "Renovation evidence",
    ):
        assert absent not in text
    report = build_report(export(), full_inputs())
    assert report.renovations == () and report.dispositions == () and report.ages == ()


def test_the_same_renovations_render_to_the_same_bytes() -> None:
    a = render_pdf(build_report(with_renovations(), full_inputs(), report_date=date(2026, 10, 31)))
    b = render_pdf(
        build_report(
            copy.deepcopy(with_renovations()), full_inputs(), report_date=date(2026, 10, 31)
        )
    )
    assert a == b
