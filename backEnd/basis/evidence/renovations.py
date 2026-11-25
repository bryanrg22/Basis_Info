"""The renovation schedules and the dispositions of a study's result (RENOVATIONS.md section 4;
docs/api/STUDY_RESULT.md "Renovations").

Pure. :func:`compute` takes the acquisition's allocated rows, the rows of each project (the
components of its after photographs, priced from the cost table only to apportion), the
renovations document and the intake, and gives :class:`RenovationsOut` and its blockers:

* a project schedule per ``after_service`` project: the CPA's capitalized amount split across the
  project's rows in proportion to their estimates, with the project's own factor
  (``capitalized / sum of estimates``; :func:`basis.domain.basis.allocate` with no closing costs),
  never the purchase price's: "Estimates should be reconciled to an actual cost" (ATG p.30). The
  rows keep the classification the rules gave them; an interior 39-year row of a nonresidential
  building becomes qualified improvement property unless an exclusion applies (:func:`qip`);
  the bonus default follows the project's own contract or work-start date
  (:func:`basis.report.tax.project_bonus_default`);
* a disposition per acquisition line an engineer marked removed by a project: the line's
  allocated amount in the acquisition study is the unadjusted basis disposed of (the guide's
  "Study allocating the cost of the asset to its individual components", Practice Unit
  DCE-P-002 p.21), the depreciation allowed or allowable to the disposition date under the
  line's own method, life and convention with its share of the bonus
  (:func:`basis.report.macrs.allowed`), the adjusted basis left (the loss on an abandonment),
  and the treatment: ``election_required`` for a portion of the building (a 27.5- or 39-year
  line: a disposition of a portion counts "only if the taxpayer makes the election", Reg.
  1.168(i)-8(d)(1)), ``whole_asset`` for a line that is an asset of its own (5, 7 or 15-year).
  That split is an inference from the regulation, not a sourced rule: the CPA confirms it;
* the before-service projects, which are part of the acquisition: listed, with the check that
  their capitalized amounts are the intake's ``improvements_before_service`` lines.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

from basis.db.enums import Bucket
from basis.domain.basis import BasisError, CostCategory, PropertyUse, RcnRow, allocate
from basis.domain.component_age import ComponentAge
from basis.domain.intake import StudyIntake
from basis.domain.money_inputs import Blocker
from basis.domain.renovations import (
    AerialBracket,
    ComponentBracket,
    Invoice,
    RenovationProject,
    StudyRenovations,
    date_problems,
)
from basis.domain.structure import mapping_for
from basis.report.macrs import allowed
from basis.report.tax import (
    QIP_AUTHORITY,
    QIP_TERMS,
    bonus_default,
    project_bonus_default,
    terms,
)

ZERO: Final = Decimal("0.00")
ESTIMATE_GAP_WARNING: Final = Decimal("0.10")
# Labels outside the building's interior although the model maps them to an interior
# component (an exterior condensing unit, exterior lighting).
EXTERIOR_LABELS: Final = frozenset({"ac_condenser", "outdoor_lighting", "gutter"})
INTERIOR_COMPONENTS: Final = frozenset({"interiors", "specialties", "mechanical", "electrical"})
STRUCTURAL_COMPONENTS: Final = frozenset({"framing", "foundation"})
REAL_PROPERTY: Final = frozenset({Bucket.Y27_5.value, Bucket.Y39.value})


@dataclass(frozen=True, slots=True)
class AcquisitionLine:
    """An acquisition row as the dispositions need it."""

    asset_id: str
    label: str | None
    category: str
    bucket: str
    allocated: Decimal
    removal_project: str | None
    disposition_date: date | None
    removed_share: Decimal
    removal_reason: str


@dataclass(frozen=True, slots=True)
class ProjectRow:
    asset_id: str
    label: str | None
    category: str
    bucket: str
    rcn: Decimal | None
    project: str
    removal_project: str | None = None


# --------------------------------------------------------------------------- output models


class RenovationCheckOut(BaseModel):
    code: str
    severity: Literal["ok", "warning", "error"]
    message: str


class BonusOut(BaseModel):
    rate: Decimal | None
    reason: str
    source: str


class RenovationRowOut(BaseModel):
    asset_id: str
    label: str | None
    category: str
    bucket: Bucket
    schedule_class: str = Field(
        description="The class the schedule prints: the bucket, or `qip` (15-year straight line)."
    )
    estimate: Decimal = Field(description="The cost table's (or the engineer's) estimate.")
    allocated: Decimal = Field(description="Its share of the capitalized amount.")
    rounding_cents: int
    qip_reason: str


class RenovationClassOut(BaseModel):
    schedule_class: str
    name: str
    rows: int
    allocated: Decimal


class ProjectScheduleOut(BaseModel):
    key: str
    kind: Literal["after_service", "before_service"]
    scope: str
    addition: bool
    contract_date: date | None
    work_start_date: date
    placed_in_service_date: date | None
    invoice_total: Decimal
    invoices: int
    invoice_documents: list[Invoice] = Field(
        description="The invoices, as the document lists them."
    )
    after_photos: int = Field(description="Photographs tagged after the project.")
    aerial: AerialBracket | None = Field(description="The dated views bracketing the work.")
    capitalized_amount: Decimal | None
    capitalized_confirmed_by: str | None
    bonus: BonusOut | None = Field(description="The project's statutory bonus default.")
    estimate_total: Decimal | None = Field(description="The sum of the rows' estimates.")
    factor: Decimal | None = Field(description="capitalized / estimates, to 9 places.")
    estimate_gap_share: Decimal | None = Field(
        description="(capitalized - estimates) / capitalized, to 6 places: how far the "
        "estimates were from the actual cost (they only apportion it)."
    )
    rows: list[RenovationRowOut]
    by_class: list[RenovationClassOut]
    rows_sum: Decimal | None = Field(description="Equals `capitalized_amount` by construction.")


class DispositionYearOut(BaseModel):
    year: int
    amount: Decimal


class DispositionOut(BaseModel):
    asset_id: str = Field(description="The acquisition line removed.")
    label: str | None
    category: str
    project: str
    bucket: Bucket
    parent: str = Field(
        description="`building` (the line is a structural component of the building, its "
        "asset) or `own_asset` (5, 7 or 15-year: an asset of its own)."
    )
    parent_placed_in_service: date
    disposition_date: date
    tax_year: int
    share: Decimal = Field(description="The share of the line removed (1: all of it).")
    unadjusted_basis: Decimal = Field(
        description="The line's allocated amount in the acquisition study times the share "
        "(Reg. 1.168(i)-8(f)(3): a study allocating the cost to the components)."
    )
    bonus: Decimal = Field(description="Its share of the bonus taken when placed in service.")
    depreciation_by_year: list[DispositionYearOut]
    allowed_or_allowable: Decimal = Field(description="Bonus plus every year's deduction.")
    adjusted_basis: Decimal = Field(
        description="unadjusted basis - allowed or allowable: the loss on an abandonment."
    )
    treatment: Literal["election_required", "whole_asset"] = Field(
        description="`election_required`: a portion of the building, recognized only with the "
        "partial disposition election on the timely filed original return for the year; "
        "`whole_asset`: a line that is its own asset. An inference from Reg. 1.168(i)-8, the "
        "CPA's to confirm."
    )
    method: str
    reason: str


class DispositionTaxYearOut(BaseModel):
    year: int
    lines: int
    unadjusted_basis: Decimal
    allowed_or_allowable: Decimal
    adjusted_basis: Decimal
    election_lines: int = Field(description="Lines needing the election in this year's return.")


class RenovationsOut(BaseModel):
    """The renovation projects of the result (RENOVATIONS.md)."""

    version: int = Field(description="`studies.renovations_version` read with the document.")
    built_with_version: int | None = Field(
        description="The version the revision's assets were built with (their takeoffs); "
        "another value than `version` withholds the allocation until a reprice."
    )
    projects: list[ProjectScheduleOut]
    dispositions: list[DispositionOut]
    dispositions_by_year: list[DispositionTaxYearOut]
    exterior_brackets: list[ComponentBracket] = Field(
        description="Exterior components' dated views (corroboration only)."
    )
    acquisition_bonus_rate: Decimal | None = Field(
        description="The bonus the CPA says was claimed on the acquisition (null: the "
        "statutory default)."
    )
    renovations_basis: Decimal = Field(
        description="The after-service projects' capitalized amounts (their schedules' total)."
    )
    checks: list[RenovationCheckOut]


# --------------------------------------------------------------------------- QIP


def qip(
    project: RenovationProject, property_use: PropertyUse, bucket: str, label: str | None
) -> tuple[bool, str]:
    """Whether a renovation row is qualified improvement property, with the reason: only for a
    nonresidential building, a 39-year row (the rules' classification of a building
    component), an interior portion, and none of the three exclusions (ATG pp.156-157)."""
    if project.qip == "off":
        return False, "QIP turned off for this project (the CPA's call)"
    if property_use is not PropertyUse.NONRESIDENTIAL:
        return False, "not nonresidential real property: QIP applies only to nonresidential"
    if bucket != Bucket.Y39.value:
        return False, "not a 39-year building component: kept as classified"
    if project.addition:
        return False, "excluded: attributable to the enlargement of the building (an addition)"
    text = (label or "").lower()
    if "elevator" in text or "escalator" in text:
        return False, "excluded: an elevator or escalator"
    component = mapping_for(label).component
    if component in STRUCTURAL_COMPONENTS or text == "structural_framing":
        return False, "excluded: the internal structural framework of the building"
    if component not in INTERIOR_COMPONENTS or text in EXTERIOR_LABELS:
        return False, "not an improvement to an interior portion of the building"
    return True, QIP_AUTHORITY


# --------------------------------------------------------------------------- the computation


def _check(
    code: str, severity: Literal["ok", "warning", "error"], message: str
) -> RenovationCheckOut:
    return RenovationCheckOut(code=code, severity=severity, message=message)


def _blocker(code: str, message: str, fix: str, assets: int = 0) -> Blocker:
    return Blocker(code, message, assets, fix)


def improvements_before_service(intake: StudyIntake | None) -> Decimal:
    if intake is None:
        return ZERO
    return sum(
        (
            c.amount
            for c in intake.acquisition_costs
            if c.category is CostCategory.IMPROVEMENTS_BEFORE_SERVICE
        ),
        ZERO,
    )


def compute(
    renovations: StudyRenovations | None,
    version: int,
    built_with: int | None,
    *,
    intake: StudyIntake | None,
    acquisition: Sequence[AcquisitionLine] | None,
    project_rows: Iterable[ProjectRow],
    ages: Sequence[tuple[str, ComponentAge]] | None = None,
) -> tuple[RenovationsOut | None, list[Blocker]]:
    """The renovations of a result and the blockers they add. ``acquisition`` is None when the
    acquisition's allocation is withheld (no disposition can be computed then). None and no
    blockers when the study has no renovations document and no row names a project."""
    rows = list(project_rows)
    removals = [a for a in acquisition or () if a.removal_project is not None]
    if renovations is None and not rows and not removals and built_with is None:
        return None, []
    blockers: list[Blocker] = []
    checks: list[RenovationCheckOut] = []
    doc = renovations or StudyRenovations(projects=[])
    if built_with != (version if renovations is not None else None):
        blockers.append(
            _blocker(
                "renovations_changed",
                f"the renovation projects changed since the assets were built (version "
                f"{built_with} recorded, {version} now)",
                "reprice the assets (POST /v1/studies/{id}/assets/reprice) so the projects' "
                "photographs move between the schedules",
            )
        )
    building_pis = None if intake is None else intake.placed_in_service_date
    for problem in date_problems(
        doc,
        building_placed_in_service=building_pis,
        acquisition_date=None if intake is None else intake.acquisition_date,
    ):
        blockers.append(_blocker("renovation_dates", problem.message, f"correct {problem.field}"))
    use = intake.property_use if intake is not None else PropertyUse.RESIDENTIAL_RENTAL

    # ---- the project schedules
    projects_out: list[ProjectScheduleOut] = []
    renovations_basis = ZERO
    by_project: dict[str, list[ProjectRow]] = {}
    for r in rows:
        by_project.setdefault(r.project, []).append(r)
    unknown_rows = sorted(set(by_project) - {p.key for p in doc.projects})
    for key in unknown_rows:
        blockers.append(
            _blocker(
                "renovation_unknown_project",
                f"{len(by_project[key])} rows were built for project {key}, which the "
                "renovations no longer list",
                "reprice the assets",
                len(by_project[key]),
            )
        )
    for project in doc.projects:
        mine = by_project.get(project.key, [])
        out = _project(project, mine, use, blockers, checks)
        if project.kind == "after_service" and out.rows_sum is not None:
            renovations_basis += out.rows_sum
        projects_out.append(out)

    # ---- the dated views (corroboration: warnings, never a blocker)
    for project in doc.projects:
        bracket = project.aerial
        pis = project.placed_in_service_date
        if bracket is None or pis is None:
            continue
        if not bracket.last_without.date < pis or pis > bracket.first_with.date:
            checks.append(
                _check(
                    "aerial_bracket",
                    "warning",
                    f"project {project.key}: placed in service {pis}, outside its dated views "
                    f"(without the work {bracket.last_without.date}, with it "
                    f"{bracket.first_with.date})",
                )
            )
    as_of = None if intake is None else intake.placed_in_service_date.year
    for item in doc.exterior_brackets:
        for line_age in ages or ():
            label, age = line_age
            if label != item.label:
                continue
            year = age.year_installed
            if year is None and age.effective_age is not None and as_of is not None:
                year = as_of - int(age.effective_age)
            if year is None:
                continue
            if (
                year < item.bracket.last_without.date.year
                or year > item.bracket.first_with.date.year
            ):
                checks.append(
                    _check(
                        "aerial_bracket",
                        "warning",
                        f"{label}: installed in {year} by its age, outside its dated views "
                        f"(without it {item.bracket.last_without.date}, with it "
                        f"{item.bracket.first_with.date})",
                    )
                )

    # ---- before-service projects against the intake
    before = [p for p in doc.projects if p.kind == "before_service"]
    if before or improvements_before_service(intake) > 0:
        stated = sum((p.capitalized_amount or ZERO for p in before), ZERO)
        line = improvements_before_service(intake)
        if stated != line:
            blockers.append(
                _blocker(
                    "renovation_before_service",
                    f"the before-service projects capitalize {stated}, the intake's "
                    f"improvements_before_service lines {line}",
                    "make the intake's improvements_before_service lines the projects' "
                    "capitalized amounts (the intake is locked once the study starts: restart "
                    "or reopen with the corrected intake)",
                )
            )

    # ---- dispositions
    dispositions: list[DispositionOut] = []
    if removals:
        dispositions = _dispositions(doc, acquisition or (), intake, blockers, checks)
    for r in rows:
        if r.removal_project is not None:
            blockers.append(
                _blocker(
                    "renovation_removal",
                    f"asset {r.asset_id} is a row of project {r.project}, not an acquisition "
                    "line: only an acquisition line can be removed by a project",
                    "clear removed_by_project on it at the assets review",
                    1,
                )
            )
    years: dict[int, list[DispositionOut]] = {}
    for d in dispositions:
        years.setdefault(d.tax_year, []).append(d)
    by_year = [
        DispositionTaxYearOut(
            year=year,
            lines=len(items),
            unadjusted_basis=sum((d.unadjusted_basis for d in items), ZERO),
            allowed_or_allowable=sum((d.allowed_or_allowable for d in items), ZERO),
            adjusted_basis=sum((d.adjusted_basis for d in items), ZERO),
            election_lines=sum(d.treatment == "election_required" for d in items),
        )
        for year, items in sorted(years.items())
    ]
    if len(by_year) > 1:
        checks.append(
            _check(
                "dispositions_by_year",
                "warning",
                "the dispositions fall in "
                + ", ".join(str(y.year) for y in by_year)
                + ": each year's election is made on that year's return",
            )
        )
    return (
        RenovationsOut(
            version=version,
            built_with_version=built_with,
            projects=projects_out,
            dispositions=dispositions,
            dispositions_by_year=by_year,
            exterior_brackets=list(doc.exterior_brackets),
            acquisition_bonus_rate=doc.acquisition_bonus_rate,
            renovations_basis=renovations_basis,
            checks=checks,
        ),
        blockers,
    )


def _project(
    project: RenovationProject,
    rows: list[ProjectRow],
    use: PropertyUse,
    blockers: list[Blocker],
    checks: list[RenovationCheckOut],
) -> ProjectScheduleOut:
    base: dict[str, Any] = {
        "key": project.key,
        "kind": project.kind,
        "scope": project.scope,
        "addition": project.addition,
        "contract_date": project.contract_date,
        "work_start_date": project.work_start_date,
        "placed_in_service_date": project.placed_in_service_date,
        "invoice_total": project.invoice_total,
        "invoices": len(project.invoices),
        "invoice_documents": list(project.invoices),
        "after_photos": len(project.after_photos),
        "aerial": project.aerial,
        "capitalized_amount": project.capitalized_amount,
        "capitalized_confirmed_by": project.capitalized_confirmed_by,
    }
    empty = ProjectScheduleOut(
        **base,
        bonus=None,
        estimate_total=None,
        factor=None,
        estimate_gap_share=None,
        rows=[],
        by_class=[],
        rows_sum=None,
    )
    if project.kind == "before_service":
        return empty
    pis = project.placed_in_service_date
    assert pis is not None
    found = project_bonus_default(project.contract_date, project.work_start_date, pis)
    bonus = BonusOut(rate=found.rate, reason=found.reason, source=found.source)
    empty = empty.model_copy(update={"bonus": bonus})
    if project.contract_date is not None and project.contract_date > project.work_start_date:
        checks.append(
            _check(
                "renovation_contract_after_work",
                "warning",
                f"project {project.key}: the contract ({project.contract_date}) is dated after "
                f"work began ({project.work_start_date}); the bonus keys on the contract date",
            )
        )
    if not project.invoices:
        checks.append(
            _check(
                "renovation_invoices",
                "warning",
                f"project {project.key}: no invoice documents are attached; the guide prices new "
                "construction from actual cost records (ATG p.28)",
            )
        )
    if project.capitalized_amount is None:
        blockers.append(
            _blocker(
                "renovation_capitalized",
                f"project {project.key}: the capitalized amount is not confirmed",
                "enter capitalized_amount and capitalized_confirmed_by on the project (the CPA "
                "decides what is capitalized)",
            )
        )
        return empty
    priced = [r for r in rows if r.rcn is not None and r.rcn > 0]
    if not priced:
        blockers.append(
            _blocker(
                "renovation_rows",
                f"project {project.key}: no priced component in its after photographs",
                "tag the project's photographs after it, or add its components at the "
                "components review",
            )
        )
        return empty
    if len(priced) != len(rows):
        # Unpriced rows are the result's own `unpriced` blocker; the schedule waits for them.
        return empty
    try:
        allocation = allocate(
            [
                RcnRow(id=r.asset_id, bucket=r.bucket, rcn=r.rcn or ZERO, label=r.category)
                for r in priced
            ],
            project.capitalized_amount,
            ZERO,
        )
    except BasisError as exc:  # pragma: no cover - priced rows above zero always allocate
        blockers.append(
            _blocker("renovation_rows", f"project {project.key}: {exc}", "check the rows")
        )
        return empty
    by_id = {r.asset_id: r for r in priced}
    out_rows: list[RenovationRowOut] = []
    for a in allocation.rows:
        r = by_id[a.id]
        is_qip, why = qip(project, use, r.bucket, r.label)
        out_rows.append(
            RenovationRowOut(
                asset_id=r.asset_id,
                label=r.label,
                category=r.category,
                bucket=Bucket(r.bucket),
                schedule_class="qip" if is_qip else r.bucket,
                estimate=a.rcn,
                allocated=a.allocated,
                rounding_cents=a.rounding_cents,
                qip_reason=why,
            )
        )
    classes: dict[str, list[RenovationRowOut]] = {}
    for row in out_rows:
        classes.setdefault(row.schedule_class, []).append(row)
    order = ["y5", "y7", "y15", "qip", "y27_5", "y39"]
    by_class = [
        RenovationClassOut(
            schedule_class=c,
            name=QIP_TERMS.name if c == "qip" else terms(c).name,
            rows=len(classes[c]),
            allocated=sum((r.allocated for r in classes[c]), ZERO),
        )
        for c in order
        if c in classes
    ]
    estimate_total = allocation.rcn_total
    gap = ((project.capitalized_amount - estimate_total) / project.capitalized_amount).quantize(
        Decimal("0.000001")
    )
    if abs(gap) > ESTIMATE_GAP_WARNING:
        checks.append(
            _check(
                "renovation_estimate_gap",
                "warning",
                f"project {project.key}: the estimates ({estimate_total}) are {gap * 100:.1f} % "
                f"from the capitalized amount ({project.capitalized_amount}); they only apportion "
                "it, but a large gap can mean a component of the work is missing from the "
                "photographs",
            )
        )
    return ProjectScheduleOut(
        **base,
        bonus=bonus,
        estimate_total=estimate_total,
        factor=allocation.factor,
        estimate_gap_share=gap,
        rows=out_rows,
        by_class=by_class,
        rows_sum=allocation.rows_sum,
    )


def _bonus_words(doc: StudyRenovations) -> str:
    if doc.acquisition_bonus_rate is not None:
        return "; the bonus rate the CPA gave"
    return "; the statutory default bonus rate"


def _dispositions(
    doc: StudyRenovations,
    acquisition: Sequence[AcquisitionLine],
    intake: StudyIntake | None,
    blockers: list[Blocker],
    checks: list[RenovationCheckOut],
) -> list[DispositionOut]:
    if intake is None:
        blockers.append(
            _blocker(
                "renovation_removal",
                "a line is marked removed but the study has no intake (no placed-in-service date)",
                "enter the intake",
                sum(a.removal_project is not None for a in acquisition),
            )
        )
        return []
    removals = [a for a in acquisition if a.removal_project is not None]
    pis = intake.placed_in_service_date
    default = bonus_default(intake.acquisition_date, pis)
    bonus_rate = doc.acquisition_bonus_rate
    if bonus_rate is None:
        bonus_rate = default.rate
    # The building (every 27.5/39-year line) is one asset; each other line is its own.
    building_basis = sum(
        (
            a.allocated
            for a in {x.asset_id: x for x in acquisition}.values()
            if a.bucket in REAL_PROPERTY
        ),
        ZERO,
    )
    out: list[DispositionOut] = []
    disposed_from_building = ZERO
    for line in sorted(removals, key=lambda a: (a.disposition_date or pis, a.asset_id)):
        project = doc.project(line.removal_project or "")
        when = line.disposition_date
        assert when is not None
        fix = "correct the removal at the assets review"
        if project is None:
            blockers.append(
                _blocker(
                    "renovation_removal",
                    f"asset {line.asset_id} is removed by project {line.removal_project}, which "
                    "the renovations do not list",
                    fix,
                    1,
                )
            )
            continue
        if project.kind == "before_service":
            blockers.append(
                _blocker(
                    "renovation_removal",
                    f"asset {line.asset_id}: project {project.key} was done before the building "
                    "was placed in service; nothing it replaced was ever in service, so there "
                    "is no disposition (Practice Unit DCE-P-002)",
                    fix,
                    1,
                )
            )
            continue
        if project.addition and not project.removals_stated:
            blockers.append(
                _blocker(
                    "renovation_removal",
                    f'asset {line.asset_id}: project {project.key} is an addition; "An addition '
                    'to a building does not substantiate that a disposition occurred" (Practice '
                    "Unit DCE-P-002) unless the engineer states the components it replaced",
                    "set removals_stated on the project, or clear the removal",
                    1,
                )
            )
            continue
        if when < pis:
            blockers.append(
                _blocker(
                    "renovation_removal",
                    f"asset {line.asset_id}: disposed of on {when}, before the building was "
                    f'placed in service ({pis}): "A partial disposition cannot occur before the '
                    'building is PIS" (Practice Unit DCE-P-002)',
                    fix,
                    1,
                )
            )
            continue
        project_pis = project.placed_in_service_date
        if when < project.work_start_date or (project_pis is not None and when > project_pis):
            checks.append(
                _check(
                    "disposition_outside_project",
                    "warning",
                    f"asset {line.asset_id}: disposed of on {when}, outside project "
                    f"{project.key}'s work ({project.work_start_date} to {project_pis})",
                )
            )
        if line.bucket not in REAL_PROPERTY and line.bucket != Bucket.LAND.value:
            parent = "own_asset"
        elif line.bucket in REAL_PROPERTY:
            parent = "building"
        else:
            blockers.append(
                _blocker("renovation_removal", f"asset {line.asset_id} is land", fix, 1)
            )
            continue
        t = terms(line.bucket)
        if t.bonus_eligible and bonus_rate is None:
            blockers.append(
                _blocker(
                    "renovation_bonus",
                    f"asset {line.asset_id}: the bonus taken on the acquisition is not known "
                    f"({default.reason})",
                    "enter acquisition_bonus_rate on the renovations (the CPA's, from the return), "
                    "or the intake's acquisition_date",
                    1,
                )
            )
            continue
        basis = (line.allocated * line.removed_share).quantize(Decimal("0.01"))
        if parent == "building":
            remaining = building_basis - disposed_from_building
            if basis > remaining:
                blockers.append(
                    _blocker(
                        "renovation_removal",
                        f"asset {line.asset_id}: disposing of {basis} would exceed the building's "
                        f"remaining basis {remaining}",
                        fix,
                        1,
                    )
                )
                continue
            disposed_from_building += basis
        result = allowed(basis, t, pis, when, bonus_rate if t.bonus_eligible else None)
        if result.adjusted_basis == 0:
            checks.append(
                _check(
                    "disposition_fully_depreciated",
                    "warning",
                    f"asset {line.asset_id}: fully depreciated by {when}: no loss",
                )
            )
        out.append(
            DispositionOut(
                asset_id=line.asset_id,
                label=line.label,
                category=line.category,
                project=project.key,
                bucket=Bucket(line.bucket),
                parent=parent,
                parent_placed_in_service=pis,
                disposition_date=when,
                tax_year=when.year,
                share=line.removed_share,
                unadjusted_basis=result.basis,
                bonus=result.bonus,
                depreciation_by_year=[
                    DispositionYearOut(year=y, amount=a) for y, a in result.by_year
                ],
                allowed_or_allowable=result.total,
                adjusted_basis=result.adjusted_basis,
                treatment="election_required" if parent == "building" else "whole_asset",
                method=result.note + (_bonus_words(doc) if t.bonus_eligible else ""),
                reason=line.removal_reason,
            )
        )
    return out


__all__ = [
    "AcquisitionLine",
    "DispositionOut",
    "DispositionTaxYearOut",
    "ProjectRow",
    "ProjectScheduleOut",
    "RenovationsOut",
    "compute",
    "improvements_before_service",
    "qip",
]
