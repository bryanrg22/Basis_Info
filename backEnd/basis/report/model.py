"""The study report as data: an approved export plus the report inputs, every figure
computed here and every total checked (STUDY_REPORT_SPEC section 11).

:func:`build_report` is pure: the same export, inputs and date give the same
:class:`StudyReport`. It adds no number of its own: each figure comes from the export
(``summary.basis``: the worksheet, the allocation, the reconciliation, the structure model)
or is a sum of figures that do, and :func:`check` proves the sums tie to the cent before
anything is rendered. A failed check raises :class:`ReportError` with a ``BASIS-REPORT``
code: a schedule with a broken total is never shipped.

Lines: one per priced asset (its allocated share from ``allocation.assets``); an unpriced
asset whose quantity was measured with another one (``takeoff_inputs.measured_with_asset``,
the site-wide landscaping) is folded into that line as "also seen in"; any other unpriced
asset is listed as awaiting a measurement, outside the totals. Categories use the rules
table's name for the asset's row (one name per row, ATG C.5), else the component label.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from basis.db.enums import Bucket
from basis.domain.rules import RULES, canonical_key, lookup
from basis.errors.codes import E
from basis.report.inputs import ReportInputs, missing_for_final
from basis.report.tax import BonusDefault, ClassTerms, bonus_default, terms

TEMPLATE_VERSION: Final = "1"
CENT: Final = Decimal("0.01")
EXPORT_FORMATS: Final = frozenset({"basis-export/2"})
CLASS_ORDER: Final = (Bucket.Y5, Bucket.Y7, Bucket.Y15, Bucket.Y27_5, Bucket.Y39)
# "08 71 20.40 0400" or "32 92 23.10 0200" in a cost source (RSMeans line numbers follow
# CSI MasterFormat: division, section, subsection, line).
MASTERFORMAT: Final = re.compile(r"\b(\d{2} \d{2} \d{2}(?:\.\d{2})?(?: \d{4})?)\b")


class ReportError(ValueError):
    """The export cannot be reported: ``code`` names why (``BASIS-REPORT-<nnn>``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{message} [{code}]")
        self.code = code


def money(value: Any) -> Decimal:
    """A stored amount as cents; ``None`` and ``""`` are zero."""
    if value is None or value == "":
        return Decimal(0)
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def masterformat_of(source: str | None) -> tuple[str, ...]:
    """The MasterFormat line numbers a cost source names, in order, without repeats."""
    seen: list[str] = []
    for found in MASTERFORMAT.findall(source or ""):
        if found not in seen:
            seen.append(found)
    return tuple(seen)


def category_name(label: str | None, material: str | None = None) -> tuple[str, str]:
    """``(key, display name)`` of an item's category: the rules-table row the label resolves
    to (its category, the table's one name for it), else the component label."""
    hit = lookup(label, material)
    key = hit.key if hit is not None else None
    if key is None and label:
        resolved = canonical_key(label)
        key = resolved[0] if resolved else None
    if key is not None and key in RULES:
        return key, RULES[key].category
    name = (label or "other").strip().lower()
    return name, name.replace("_", " ").capitalize()


# ------------------------------------------------------------------ the report's parts


@dataclass(frozen=True, slots=True)
class ClassLine:
    bucket: Bucket
    name: str
    irc_section: str
    allocated: Decimal
    share: Decimal  # of the depreciable basis, 4 places
    items: int


@dataclass(frozen=True, slots=True)
class BasisLine:
    label: str
    amount: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class Fact:
    label: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class ItemLine:
    line: int
    description: str
    category_key: str
    category: str
    location: str
    quantity: str
    masterformat: tuple[str, ...]
    rcn: Decimal
    allocated: Decimal
    bucket: Bucket
    asset_class: str | None
    asset_class_source: str
    authority: str
    estimated: bool  # concealed: not seen in a photograph (the structure model)
    contested: bool
    also_in: tuple[str, ...]
    asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AwaitingLine:
    description: str
    location: str
    reason: str
    asset_id: str


@dataclass(frozen=True, slots=True)
class CategoryLine:
    key: str
    name: str
    bucket: Bucket
    items: int
    rcn: Decimal
    allocated: Decimal


@dataclass(frozen=True, slots=True)
class ScheduleLine:
    """One preparer's schedule row: a class, or an item when the schedule is itemized."""

    terms: ClassTerms
    basis: Decimal
    acquired: date | None
    placed_in_service: date
    bonus: BonusDefault | None


@dataclass(frozen=True, slots=True)
class Flag:
    severity: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class UnpricedSystem:
    """A building system with no priced line in the schedule (``summary.basis.not_produced``):
    disclosed, so the remaining gap to the basis is explicit."""

    system: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReconciliationLine:
    component: str
    top_down: Decimal
    itemized: Decimal
    concealed: Decimal


@dataclass(frozen=True, slots=True)
class StudyReport:
    study_id: str
    revision: int
    template_version: str
    report_date: date | None
    draft: bool
    missing_for_final: tuple[str, ...]
    inputs: ReportInputs
    property_name: str | None
    property_facts: tuple[Fact, ...]
    property_use: str
    recovery_period: str
    basis_lines: tuple[BasisLine, ...]
    land: Decimal
    land_method: str
    land_source: str
    depreciable_basis: Decimal
    classes: tuple[ClassLine, ...]
    accelerated: Decimal
    categories: tuple[CategoryLine, ...]
    items: tuple[ItemLine, ...]
    awaiting: tuple[AwaitingLine, ...]
    schedule: tuple[ScheduleLine, ...]
    methodology: tuple[Fact, ...]
    reconciliation: tuple[ReconciliationLine, ...]
    unpriced_systems: tuple[UnpricedSystem, ...]
    reconciliation_status: str
    gap_share: Decimal
    tolerance: Decimal
    flags: tuple[Flag, ...]
    contested: int
    conventions_used: int
    photographs: int
    look_back: bool
    checklist: tuple[tuple[str, str, str], ...] = field(default=())


# ------------------------------------------------------------------ building it

PROPERTY_FIELDS: Final = (
    ("property_address", "Address"),
    ("city", "City"),
    ("state", "State"),
    ("zip", "ZIP"),
    ("county", "County"),
    ("legal_description", "Legal description"),
    ("assessors_parcel", "Assessor's parcel number"),
    ("zoning_classification", "Zoning"),
    ("area_sqft", "Site area (sq ft)"),
    ("type", "Type"),
    ("design_style", "Design / style"),
    ("stories", "Stories"),
    ("year_built", "Year built"),
    ("effective_age_years", "Effective age (years)"),
    ("gla_sqft", "Gross living area (sq ft)"),
    ("basement_area_sqft", "Basement (sq ft)"),
    ("basement_finish_percent", "Basement finished (%)"),
    ("garage_cars", "Garage (cars)"),
    ("foundation_type", "Foundation"),
    ("exterior_walls", "Exterior walls"),
    ("roof_surface", "Roof surface"),
    ("heating_type", "Heating"),
    ("cooling", "Cooling"),
    ("quality", "Quality"),
    ("condition", "Condition"),
)

CHECKLIST: Final = (
    ("C.1", "Preparation by an individual with expertise and experience", "Section 10"),
    ("C.2", "Detailed description of the methodology", "Section 4"),
    ("C.3", "Use of appropriate documentation", "Section 4, Exhibit A"),
    ("C.4", "Interviews conducted with appropriate parties", "Section 4"),
    ("C.5", "Use of common nomenclature", "Sections 6 and 7"),
    ("C.6", "Use of a standard numbering system", "Section 7 (CSI MasterFormat)"),
    ("C.7", "Explanation of the legal analysis", "Section 5"),
    ("C.8", "Determination of unit costs and engineering take-off", "Section 4, Section 7"),
    ("C.9", "Organization of assets into lists or groups", "Section 6"),
    (
        "C.10",
        "Reconciliation of total allocated costs to total actual costs",
        "Section 3, Exhibit B",
    ),
    ("C.11", "Explanation of the treatment of indirect costs", "Section 3"),
    ("C.12", "Identification and listing of section 1245 property", "Section 7"),
    ("C.13", "Consideration of related issues", "Section 8"),
)


def _fields(document: Mapping[str, Any]) -> dict[str, str]:
    """The appraisal's extracted fields by name (the first value when a name repeats)."""
    out: dict[str, str] = {}
    for extraction in document.get("extraction") or []:
        for f in extraction.get("fields") or []:
            name = f.get("field")
            value = f.get("value_text") or f.get("value_num") or f.get("value_date")
            if name and value not in (None, "") and name not in out:
                out[str(name)] = str(value)
    return out


def _date(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _location(asset: Mapping[str, Any]) -> str:
    floor, room = asset.get("room_floor"), asset.get("room_name")
    if room and floor not in (None, ""):
        level = "basement" if str(floor) == "0" else f"floor {floor}"
        return f"{room} ({level})"
    return str(room or "Site")


def _quantity(asset: Mapping[str, Any]) -> str:
    q = asset.get("quantity")
    unit = asset.get("unit") or ""
    if q in (None, ""):
        return ""
    n = Decimal(str(q))
    text = f"{n.normalize():f}" if n == n.to_integral_value() else f"{n.quantize(Decimal('0.01'))}"
    return f"{text} {unit}".strip()


def _asset_class(
    asset: Mapping[str, Any], property_class: str, bucket: Bucket
) -> tuple[str | None, str]:
    """The Rev. Proc. 87-56 class of an item: its rules-table row's when the row gives the
    item's bucket, else the class's default."""
    hit = lookup(
        asset.get("label"),
        asset.get("material"),
        asset.get("attachment"),
        property_class,
        asset.get("room_type"),
    )
    if hit is not None and hit.bucket is bucket and hit.asset_class:
        return hit.asset_class, f"rules table row {hit.key}"
    default = terms(bucket).default_asset_class
    return default, ("the class's default" if default else "real property (IRC 168(e)(2))")


def _description(name: str, material: str | None) -> str:
    """The item's category name (one name per row, ATG C.5) with its material when the
    name does not already say it."""
    m = (material or "").replace("_", " ").strip().lower()
    if not m or m in ("other", "unknown") or m in name.lower():
        return name
    return f"{name} ({m})"


def _percent(value: Any) -> str:
    try:
        return f"{Decimal(str(value)) * 100:.1f} %"
    except ArithmeticError:
        return str(value)


def _authority(asset: Mapping[str, Any]) -> str:
    refs = []
    for c in asset.get("citations") or []:
        ref = c.get("reference") or c.get("doc_id")
        if ref and ref not in refs:
            refs.append(str(ref))
    return "; ".join(refs[:2]) or (asset.get("method") or "")


def build_report(
    document: Mapping[str, Any],
    inputs: ReportInputs | None = None,
    *,
    report_date: date | None = None,
) -> StudyReport:
    """The report of an approved export (``basis-export/2``). Raises :class:`ReportError`
    when the export has no allocation to report or a total does not tie."""
    if document.get("format") not in EXPORT_FORMATS:
        raise ReportError(E.REPORT_NOT_AN_EXPORT, f"not a study export: {document.get('format')!r}")
    inputs = inputs or ReportInputs()
    basis = (document.get("summary") or {}).get("basis") or {}
    allocation = basis.get("allocation")
    if not allocation:
        why = [str(m) for m in basis.get("missing") or []] or ["no allocation in the export"]
        # EVAL_REPORT 78: each missing input, unpriced or unclassified group, named.
        named = [
            f"{b.get('message')} ({b.get('assets')} assets): {b.get('fix')}"
            if b.get("assets")
            else f"{b.get('message')}: {b.get('fix')}"
            for b in basis.get("blockers") or []
        ]
        raise ReportError(
            E.REPORT_NOT_PRODUCED,
            "the allocation was withheld (missing: "
            + ", ".join(why)
            + ")"
            + ("; " + "; ".join(named) if named else ""),
        )
    worksheet = basis["worksheet"]
    reconciliation = basis.get("reconciliation") or {}
    property_class = (document.get("summary") or {}).get("takeoff", {}).get("property_class") or (
        "nonresidential" if worksheet.get("recovery_period") == "39" else "residential_rental"
    )
    assets = list(document.get("assets") or [])
    by_id = {str(a["id"]): a for a in assets}
    allocated = {str(a["asset_id"]): a for a in allocation.get("assets") or []}

    # ---- lines
    folded: dict[str, list[str]] = defaultdict(list)
    awaiting: list[AwaitingLine] = []
    for a in assets:
        aid = str(a["id"])
        host = (a.get("takeoff_inputs") or {}).get("measured_with_asset")
        unpriced = aid not in allocated or money(allocated[aid].get("allocated")) == 0
        if host and str(host) in allocated and str(host) != aid and unpriced:
            # Its quantity was measured once with another asset (the site-wide landscaping):
            # it is that line, seen in another photograph.
            folded[str(host)].append(aid)
            allocated.pop(aid, None)
            continue
        if aid in allocated:
            continue
        else:
            awaiting.append(
                AwaitingLine(
                    description=(a.get("label") or "item").replace("_", " "),
                    location=_location(a),
                    reason=a.get("review_reason") or "no price: awaiting a measurement",
                    asset_id=aid,
                )
            )
    raw: list[ItemLine] = []
    for aid, row in allocated.items():
        a = by_id.get(aid)
        if a is None:
            raise ReportError(
                E.REPORT_ASSET_UNACCOUNTED, f"allocated asset {aid} is not in the export"
            )
        bucket = Bucket(row["bucket"])
        key, name = category_name(a.get("label"), a.get("material"))
        cls, cls_source = _asset_class(a, property_class, bucket)
        seen_in = sorted({_location(by_id[o]) for o in (aid, *folded.get(aid, []))})
        whole = bool(folded.get(aid))
        raw.append(
            ItemLine(
                line=0,
                description=_description(name, a.get("material")),
                category_key=key,
                category=name,
                location=(
                    "Building (concealed)"
                    if row.get("concealed")
                    else "Whole property"
                    if whole
                    else _location(a)
                ),
                quantity=_quantity(a),
                masterformat=masterformat_of(a.get("cost_source")),
                rcn=money(row.get("rcn")),
                allocated=money(row.get("allocated")),
                bucket=bucket,
                asset_class=cls,
                asset_class_source=cls_source,
                authority=_authority(a),
                estimated=bool(row.get("concealed")),
                contested=str(a.get("review_reason") or "").find("rule_contested") >= 0,
                also_in=tuple(seen_in) if whole else (),
                asset_ids=(aid, *sorted(folded.get(aid, []))),
            )
        )
    order = {b: i for i, b in enumerate(CLASS_ORDER)}
    raw.sort(
        key=lambda i: (
            order.get(i.bucket, 9),
            i.category,
            i.location,
            i.description,
            i.asset_ids[0],
        )
    )
    items = tuple(
        ItemLine(**{**{f: getattr(i, f) for f in ItemLine.__dataclass_fields__}, "line": n})
        for n, i in enumerate(raw, start=1)
    )

    # ---- classes and categories
    depreciable = money(allocation["total"])
    class_lines = []
    for bucket in CLASS_ORDER:
        mine = [i for i in items if i.bucket is bucket]
        if not mine:
            continue
        total = sum((i.allocated for i in mine), Decimal(0))
        t = terms(bucket)
        class_lines.append(
            ClassLine(
                bucket=bucket,
                name=t.name,
                irc_section=t.irc_section,
                allocated=total,
                share=(total / depreciable).quantize(Decimal("0.0001"))
                if depreciable
                else Decimal(0),
                items=len(mine),
            )
        )
    groups: dict[tuple[str, Bucket], list[ItemLine]] = defaultdict(list)
    for i in items:
        groups[(i.category_key, i.bucket)].append(i)
    categories = tuple(
        sorted(
            (
                CategoryLine(
                    key=k,
                    name=v[0].category,
                    bucket=b,
                    items=len(v),
                    rcn=sum((i.rcn for i in v), Decimal(0)),
                    allocated=sum((i.allocated for i in v), Decimal(0)),
                )
                for (k, b), v in groups.items()
            ),
            key=lambda c: (order.get(c.bucket, 9), c.name),
        )
    )

    # ---- basis, schedule, facts
    land = money(worksheet.get("land"))
    price = money(worksheet.get("purchase_price"))
    capitalized = money(worksheet.get("capitalized_costs"))
    basis_lines = [BasisLine("Purchase price", price, "intake (closing statement)")]
    for c in worksheet.get("acquisition_costs") or []:
        basis_lines.append(
            BasisLine(
                f"Acquisition cost: {c.get('description') or c.get('category')}",
                money(c.get("amount")),
                str(c.get("authority") or "intake"),
            )
        )
    basis_lines.append(BasisLine("Less land", -land, str(worksheet.get("land_source") or "")))
    basis_lines.append(BasisLine("Depreciable basis", depreciable, "allocated below"))
    acquired = _date(worksheet.get("acquisition_date"))
    placed = _date(worksheet.get("placed_in_service_date"))
    if placed is None:
        raise ReportError(E.REPORT_TOTALS_DO_NOT_TIE, "the worksheet has no placed-in-service date")
    schedule = tuple(
        ScheduleLine(
            terms=terms(c.bucket),
            basis=c.allocated,
            acquired=acquired,
            placed_in_service=placed,
            bonus=bonus_default(acquired, placed) if terms(c.bucket).bonus_eligible else None,
        )
        for c in class_lines
    )
    fields_ = _fields(document)
    facts = tuple(
        Fact(label, fields_[name], "appraisal")
        for name, label in PROPERTY_FIELDS
        if name in fields_
    )
    takeoff = (document.get("summary") or {}).get("takeoff") or {}
    cost_index = basis.get("cost_index") or {}
    depreciation = basis.get("depreciation") or {}
    structure = (basis.get("structure") or {}).get("building_rcn") or {}
    photos = len(document.get("photos") or [])
    model = structure.get("model") or {}
    methodology = (
        Fact(
            "Cost source",
            "RSMeans Residential Costs 2020, unit costs including the installing "
            "contractor's overhead and profit",
            "cost table",
        ),
        Fact(
            "Cost year",
            f"{cost_index.get('cost_year')}: costs brought from 2020 by the RSMeans "
            f"Historical Cost Index, factor {cost_index.get('factor')}",
            str(cost_index.get("applied") or ""),
        ),
        Fact(
            "Location",
            f"factor {takeoff.get('location_factor')}",
            str(takeoff.get("location_source") or ""),
        ),
        Fact(
            "Building systems",
            "RSMeans square-foot model"
            + (
                f" ({model.get('name') or model.get('class') or ''})"
                if isinstance(model, Mapping) and (model.get("name") or model.get("class"))
                else ""
            )
            + "; see Exhibit B",
            str(structure.get("source") or ""),
        ),
        Fact(
            "Physical depreciation",
            f"{_percent(depreciation.get('rate'))}, from the appraisal's cost approach, "
            "checked against the age-life method",
            str(depreciation.get("source") or ""),
        ),
        Fact(
            "Quantities",
            ", ".join(
                f"{v} {k.replace('_', ' ')}"
                for k, v in sorted((takeoff.get("area_sources") or {}).items())
            ),
            "take-off",
        ),
    )
    recon_lines = tuple(
        ReconciliationLine(
            component=str(r.get("component")).replace("_", " "),
            top_down=money(r.get("top_down")),
            itemized=money(r.get("itemized")),
            concealed=money(r.get("concealed")),
        )
        for r in reconciliation.get("by_component") or []
    )
    flags = tuple(
        Flag(str(c.get("severity")), str(c.get("code")), str(c.get("message")))
        for group in (
            worksheet.get("checks"),
            reconciliation.get("checks"),
            depreciation.get("checks"),
            allocation.get("checks"),
        )
        for c in group or []
        if c.get("severity") not in ("ok", None)
    )
    missing = missing_for_final(inputs)
    look_back = inputs.study is not None and inputs.study.prior_depreciation is not None
    report = StudyReport(
        study_id=str(document["study_id"]),
        revision=int(document["revision"]),
        template_version=TEMPLATE_VERSION,
        report_date=report_date,
        draft=bool(missing),
        missing_for_final=missing,
        inputs=inputs,
        property_name=document.get("property_name"),
        property_facts=facts,
        property_use=str(worksheet.get("property_use") or property_class),
        recovery_period=str(worksheet.get("recovery_period") or ""),
        basis_lines=tuple(basis_lines),
        land=land,
        land_method=str(worksheet.get("land_method") or ""),
        land_source=str(worksheet.get("land_source") or ""),
        depreciable_basis=depreciable,
        classes=tuple(class_lines),
        accelerated=sum(
            (c.allocated for c in class_lines if terms(c.bucket).bonus_eligible), Decimal(0)
        ),
        categories=categories,
        items=items,
        awaiting=tuple(awaiting),
        schedule=schedule,
        methodology=methodology,
        reconciliation=recon_lines,
        unpriced_systems=tuple(
            UnpricedSystem(str(n.get("system")), str(n.get("reason")))
            for n in basis.get("not_produced") or []
            if isinstance(n, Mapping)
        ),
        reconciliation_status=str(reconciliation.get("status") or ""),
        gap_share=Decimal(str(reconciliation.get("gap_share") or 0)),
        tolerance=Decimal(str(reconciliation.get("tolerance") or 0)),
        flags=flags,
        contested=sum(1 for i in items if i.contested),
        conventions_used=sum(1 for a in assets if a.get("convention")),
        photographs=photos,
        look_back=look_back,
        checklist=CHECKLIST,
    )
    check(report, price=price, capitalized=capitalized, export_assets=assets)
    return report


def check(
    report: StudyReport,
    *,
    price: Decimal,
    capitalized: Decimal,
    export_assets: Sequence[Mapping[str, Any]],
) -> None:
    """Every total ties to the cent, every export asset is on exactly one line."""
    items_sum = sum((i.allocated for i in report.items), Decimal(0))
    if items_sum != report.depreciable_basis:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE,
            f"the items sum to {items_sum}, the depreciable basis is {report.depreciable_basis}",
        )
    if price + capitalized - report.land != report.depreciable_basis:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE,
            f"price {price} + capitalized costs {capitalized} - land {report.land} is not the "
            f"depreciable basis {report.depreciable_basis}",
        )
    if sum((c.allocated for c in report.classes), Decimal(0)) != report.depreciable_basis:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE, "the classes do not sum to the depreciable basis"
        )
    if sum((c.allocated for c in report.categories), Decimal(0)) != report.depreciable_basis:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE, "the categories do not sum to the depreciable basis"
        )
    if sum((s.basis for s in report.schedule), Decimal(0)) != report.depreciable_basis:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE, "the schedule does not sum to the depreciable basis"
        )
    placed = [aid for i in report.items for aid in i.asset_ids] + [
        w.asset_id for w in report.awaiting
    ]
    exported = [str(a["id"]) for a in export_assets]
    if sorted(placed) != sorted(exported):
        missing = sorted(set(exported) - set(placed))
        twice = sorted({p for p in placed if placed.count(p) > 1})
        raise ReportError(
            E.REPORT_ASSET_UNACCOUNTED,
            f"assets not on exactly one line: missing {missing[:5]}, repeated {twice[:5]}",
        )


__all__ = [
    "CHECKLIST",
    "TEMPLATE_VERSION",
    "AwaitingLine",
    "BasisLine",
    "CategoryLine",
    "ClassLine",
    "Fact",
    "Flag",
    "ItemLine",
    "ReconciliationLine",
    "ReportError",
    "ScheduleLine",
    "StudyReport",
    "UnpricedSystem",
    "build_report",
    "category_name",
    "check",
    "masterformat_of",
    "money",
]
