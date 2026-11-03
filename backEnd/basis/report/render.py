"""The study report as a PDF: :class:`~basis.report.model.StudyReport` through the Typst
template (STUDY_REPORT_SPEC 11.8; the bake-off in 11.8.1 chose Typst).

Every figure is formatted here, so the template only places text: it does no arithmetic and
no rounding. Fonts are the bundled IBM Plex Sans (OFL) with the system's fonts ignored, and
the PDF's timestamp is the report date, so the same report renders to the same bytes on a
laptop, in CI and in the worker image.

Photographs for Exhibit A are passed in (:class:`ExhibitPhoto`: the JPEG with the counted
objects already outlined and numbered, and its caption); without them the exhibit says
where the photographs are.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Final

from basis.errors.codes import E
from basis.report.inputs import ReportInputs
from basis.report.model import ReportError, StudyReport
from basis.report.tax import PUB_946, PUB_946_ADS, mid_quarter_note

PACKAGE: Final = "basis.report"


@dataclass(frozen=True, slots=True)
class ExhibitPhoto:
    name: str  # a file name: letters, digits, '-', '_', '.' only
    jpeg: bytes
    caption: str


def usd(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def pct(share: Decimal) -> str:
    return f"{share * 100:.1f} %"


PROPERTY_USE: Final = {
    "residential_rental": (
        "Residential rental property (IRC 168(e)(2)(A): 80 % or more of gross rental income "
        "from dwelling units)"
    ),
    "nonresidential": "Nonresidential real property (IRC 168(e)(2)(B))",
}
PHOTOGRAPHS: Final = {
    "engineer_site_visit": "site visit",
    "taxpayer_supplied": "taxpayer supplied",
}


def view(report: StudyReport, photos: tuple[ExhibitPhoto, ...] = ()) -> dict[str, Any]:
    """The template's data: strings and flags only."""
    inputs: ReportInputs = report.inputs
    firm = inputs.firm
    study = inputs.study
    sig = inputs.signature
    engineer = sig.engineer if sig else (firm.engineer if firm else None)
    return {
        "draft": report.draft,
        "missing": list(report.missing_for_final),
        "template_version": report.template_version,
        "study_id": report.study_id,
        "revision": report.revision,
        "report_date": report.report_date.strftime("%B %-d, %Y")
        if report.report_date
        else "[study date]",
        "property_name": report.property_name or "[property]",
        "firm": {
            "name": firm.name if firm else "[firm name]",
            "address": (firm.address if firm else None) or "",
            "phone": (firm.phone if firm else None) or "",
            "logo": bool(firm and firm.logo_png),
        },
        "engineer": {
            "name": engineer.name if engineer else "[engineer name]",
            "credentials": engineer.credentials if engineer else "[credentials]",
            "license": (
                f"License {engineer.license_number} ({engineer.license_state})"
                if engineer and engineer.license_number
                else ""
            ),
        },
        "signed": (
            {
                "at": sig.signed_at.astimezone(UTC).strftime("%B %-d, %Y %H:%M UTC"),
                "id": str(sig.signature_id) if sig.signature_id else "",
            }
            if sig
            else None
        ),
        "taxpayer": study.taxpayer_name if study else "[taxpayer name]",
        "photographs": (
            {
                "kind": PHOTOGRAPHS[study.photographs],
                "by": study.photographed_by,
                "on": study.photographed_on.strftime("%B %-d, %Y"),
                "site_visit": study.photographs == "engineer_site_visit",
            }
            if study
            else None
        ),
        "interviews": (
            [
                {
                    "name": i.name,
                    "role": i.role,
                    "on": i.on.isoformat() if i.on else "",
                    "topics": i.topics or "",
                }
                for i in study.interviews
            ]
            if study
            else None
        ),
        "look_back": (
            {
                "years": study.prior_depreciation.tax_years,
                "claimed": usd(study.prior_depreciation.depreciation_claimed),
                "method": study.prior_depreciation.method_used,
            }
            if study and study.prior_depreciation
            else None
        ),
        "property_use": PROPERTY_USE.get(report.property_use, report.property_use),
        "facts": [{"label": f.label, "value": f.value} for f in report.property_facts],
        "basis_lines": [
            {"label": b.label, "amount": usd(b.amount), "source": b.source}
            for b in report.basis_lines
        ],
        "land": usd(report.land),
        "land_source": report.land_source,
        "depreciable": usd(report.depreciable_basis),
        "accelerated": usd(report.accelerated),
        "accelerated_share": pct(report.accelerated / report.depreciable_basis)
        if report.depreciable_basis
        else "",
        "classes": [
            {
                "name": c.name,
                "section": c.irc_section,
                "amount": usd(c.allocated),
                "share": pct(c.share),
                "items": str(c.items),
            }
            for c in report.classes
        ],
        "categories": [
            {
                "name": c.name,
                "class": c.bucket.value.replace("y", "").replace("_", "."),
                "items": str(c.items),
                "rcn": usd(c.rcn),
                "allocated": usd(c.allocated),
            }
            for c in report.categories
        ],
        "items": [
            {
                "line": str(i.line),
                "description": i.description,
                "location": i.location,
                "seen_in": ", ".join(i.also_in),
                "quantity": i.quantity,
                "masterformat": ", ".join(i.masterformat) or "typical unit cost",
                "rcn": usd(i.rcn),
                "allocated": usd(i.allocated),
                "class": i.bucket.value.replace("y", "").replace("_", "."),
                "asset_class": i.asset_class or "",
                "estimated": i.estimated,
                "contested": i.contested,
            }
            for i in report.items
        ],
        "awaiting": [
            {"description": w.description, "location": w.location} for w in report.awaiting
        ],
        "schedule": [
            {
                "name": s.terms.name,
                "section": s.terms.irc_section,
                "basis": usd(s.basis),
                "life": f"{s.terms.gds_life.normalize():f}",
                "ads": f"{s.terms.ads_life.normalize():f}",
                "method": s.terms.method,
                "convention": s.terms.convention,
                "acquired": s.acquired.isoformat() if s.acquired else "[acquisition date]",
                "placed": s.placed_in_service.isoformat(),
                "bonus": (
                    "not eligible"
                    if s.bonus is None
                    else (f"{s.bonus.rate * 100:.0f} %" if s.bonus.rate is not None else "see note")
                ),
                "bonus_note": "" if s.bonus is None else f"{s.bonus.reason}. {s.bonus.source}.",
            }
            for s in report.schedule
        ],
        "mid_quarter": mid_quarter_note(
            (s.terms, s.basis, s.placed_in_service) for s in report.schedule
        ),
        "pub946": PUB_946,
        "pub946_ads": PUB_946_ADS,
        "methodology": [
            {"label": f.label, "value": f.value, "source": f.source} for f in report.methodology
        ],
        "reconciliation": [
            {
                "component": r.component,
                "top_down": usd(r.top_down),
                "itemized": usd(r.itemized),
                "concealed": usd(r.concealed),
            }
            for r in report.reconciliation
        ],
        "unpriced_systems": [
            {"system": u.system, "reason": u.reason} for u in report.unpriced_systems
        ],
        "reconciliation_status": report.reconciliation_status,
        "gap_share": pct(report.gap_share),
        "tolerance": pct(report.tolerance),
        "flags": [
            {"severity": f.severity, "code": f.code, "message": f.message} for f in report.flags
        ],
        "contested": str(report.contested),
        "conventions_used": str(report.conventions_used),
        "photograph_count": str(report.photographs),
        "limiting": list(firm.limiting_conditions) if firm and firm.limiting_conditions else None,
        "checklist": [{"element": c, "title": t, "where": w} for c, t, w in report.checklist],
        "photos": [{"file": p.name, "caption": p.caption} for p in photos],
    }


def _timestamp(report: StudyReport) -> int:
    day = report.report_date or date(2000, 1, 1)
    return int(datetime.combine(day, time(12), tzinfo=UTC).timestamp())


def render_pdf(report: StudyReport, photos: tuple[ExhibitPhoto, ...] = ()) -> bytes:
    """The report as PDF bytes; the same report and photographs give the same bytes."""
    import typst

    with tempfile.TemporaryDirectory(prefix="basis-report-") as tmp:
        root = Path(tmp)
        source = resources.files(PACKAGE)
        (root / "report.typ").write_text(
            (source / "templates" / "report.typ").read_text(encoding="utf-8"), encoding="utf-8"
        )
        fonts = root / "fonts"
        fonts.mkdir()
        for font in (source / "fonts").iterdir():
            if font.name.endswith(".ttf"):
                (fonts / font.name).write_bytes(font.read_bytes())
        images = root / "img"
        images.mkdir()
        for p in photos:
            if not p.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
                raise ReportError(E.REPORT_RENDER_FAILED, f"unsafe photograph name {p.name!r}")
            (images / p.name).write_bytes(p.jpeg)
        firm = report.inputs.firm
        if firm and firm.logo_png:
            (root / "logo.png").write_bytes(firm.logo_png)
        (root / "data.json").write_text(
            json.dumps(view(report, photos), sort_keys=True), encoding="utf-8"
        )
        try:
            pdf = typst.compile(
                str(root / "report.typ"),
                root=str(root),
                font_paths=[str(fonts)],
                ignore_system_fonts=True,
                timestamp=_timestamp(report),
            )
        except Exception as exc:  # typst raises its own error types
            raise ReportError(E.REPORT_RENDER_FAILED, f"the report did not render: {exc}") from exc
    assert isinstance(pdf, bytes)
    return pdf


__all__ = ["ExhibitPhoto", "pct", "render_pdf", "usd", "view"]
