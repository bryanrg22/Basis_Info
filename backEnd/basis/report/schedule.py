"""The preparer's depreciation schedule: one row per report line, as CSV and XLSX
(STUDY_REPORT_SPEC 11.5).

The columns are the common denominator of the fixed-asset packages (Form 4562 Part III B:
classification, date placed in service, basis, recovery period, convention, method) plus
what the report proves (the line, its class, its authority, the statutory bonus default
with its source). The description is cut to 45 characters without quotes or commas, the
limit of Fixed Assets CS's import. Every row's basis is the line's allocated cost, so the
rows sum to the depreciable basis to the cent (:func:`rows` checks it). Vendor layouts are
column mappings over these rows, added when a preparer supplies a template.

Both files are byte-identical for the same report: the CSV has fixed columns and order, the
XLSX has its document properties pinned (no creation time).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from basis.errors.codes import E
from basis.report.model import ReportError, StudyReport
from basis.report.tax import terms

DESCRIPTION_LIMIT: Final = 45
COLUMNS: Final = (
    "line",
    "description",
    "location",
    "irc_section",
    "asset_class",
    "recovery_period",
    "method",
    "convention",
    "gds_life",
    "ads_life",
    "real_property",
    "date_acquired",
    "date_placed_in_service",
    "basis",
    "bonus_eligible",
    "statutory_bonus_rate",
    "bonus_source",
    "authority",
)
_UNSAFE: Final = re.compile(r"[\"',\r\n\t]+")


@dataclass(frozen=True, slots=True)
class Row:
    values: tuple[str, ...]
    basis: Decimal


def description(text: str) -> str:
    """At most 45 characters, no quotes, commas or line breaks."""
    clean = _UNSAFE.sub(" ", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean[:DESCRIPTION_LIMIT].rstrip()


def _date(value: date | None) -> str:
    return value.isoformat() if value else ""


def rows(report: StudyReport) -> tuple[Row, ...]:
    """The schedule rows, one per report line, checked to sum to the depreciable basis."""
    by_bucket = {s.terms.bucket: s for s in report.schedule}
    out: list[Row] = []
    for item in report.items:
        t = terms(item.bucket)
        line = by_bucket[item.bucket]
        bonus = line.bonus
        out.append(
            Row(
                values=(
                    str(item.line),
                    description(item.description),
                    description(item.location),
                    t.irc_section,
                    item.asset_class or "",
                    f"{t.gds_life.normalize():f}",
                    t.method,
                    t.convention,
                    f"{t.gds_life.normalize():f}",
                    f"{t.ads_life.normalize():f}",
                    "Y" if t.real_property else "N",
                    _date(line.acquired),
                    _date(line.placed_in_service),
                    f"{item.allocated:.2f}",
                    "Y" if t.bonus_eligible else "N",
                    "" if bonus is None or bonus.rate is None else f"{bonus.rate:.2f}",
                    "" if bonus is None else f"{bonus.source} ({bonus.reason}; preparer elects)",
                    item.authority,
                ),
                basis=item.allocated,
            )
        )
    total = sum((r.basis for r in out), Decimal(0))
    if total != report.depreciable_basis:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE,
            f"the schedule rows sum to {total}, the depreciable basis is "
            f"{report.depreciable_basis}",
        )
    return tuple(out)


def to_csv(schedule: Iterable[Row]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in schedule:
        writer.writerow(row.values)
    return buffer.getvalue().encode("utf-8")


FIXED_TIME: Final = datetime(2000, 1, 1)


def to_xlsx(schedule: Iterable[Row], *, title: str = "Depreciation schedule") -> bytes:
    """The same rows as an XLSX workbook: basis and rates as numbers, dates as dates,
    with fixed document properties so the bytes do not change between runs."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Schedule"
    sheet.append(list(COLUMNS))
    numeric = {"basis", "statutory_bonus_rate", "recovery_period", "gds_life", "ads_life", "line"}
    dates = {"date_acquired", "date_placed_in_service"}
    for row in schedule:
        cells: list[object] = []
        for name, value in zip(COLUMNS, row.values, strict=True):
            if value and name in numeric:
                cells.append(Decimal(value) if "." in value else int(value))
            elif value and name in dates:
                cells.append(date.fromisoformat(value))
            else:
                cells.append(value)
        sheet.append(cells)
    for cell in sheet["N"][1:]:
        cell.number_format = "#,##0.00"
    sheet.freeze_panes = "A2"
    props = workbook.properties
    props.title = title
    props.creator = "Basis"
    props.created = FIXED_TIME
    props.modified = FIXED_TIME
    props.lastModifiedBy = "Basis"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _pin_zip_times(buffer.getvalue())


def _pin_zip_times(data: bytes) -> bytes:
    """Rewrite the XLSX zip with fixed member times (openpyxl stamps the save time)."""
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            fixed = zipfile.ZipInfo(info.filename, date_time=(2000, 1, 1, 0, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = info.external_attr
            target.writestr(fixed, source.read(info.filename))
    return out.getvalue()


__all__ = ["COLUMNS", "DESCRIPTION_LIMIT", "Row", "description", "rows", "to_csv", "to_xlsx"]
