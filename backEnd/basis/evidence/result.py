"""The study result: the basis worksheet, the structure model, the depreciated schedule, the
reconciliation and the allocation (docs/api/STUDY_RESULT.md). One representation for the
export summary (``summary.basis`` in ``basis.evidence.pack``) and
``GET /v1/studies/{id}/result``.

:func:`compute_result` is pure: the revision's assets (category, label, bucket, replacement
cost new, whether the row is a concealed row the structure model wrote), the study's intake
(or none) and the structure inputs (the appraisal's readings and the cost index, or none)
give :class:`StudyResultOut`. Without an intake the schedule is still returned and
``missing`` says what is absent; nothing is fabricated.

With structure inputs (docs/review_2026-10-15/STRUCTURE_MODEL.md): the building's RCN is
recomputed from the appraisal, split into the model's components and carved against the
itemized rows, so the result shows top-down, itemized and concealed per component; every row
is depreciated at the appraiser's rate; the RCNLD total is reconciled to the purchase price
less land, and the basis is allocated pro rata on RCNLD only when the two agree within the
square-foot method's tolerance. Otherwise ``allocation`` is null, ``missing`` says
``allocation`` and the reconciliation's diagnosis says why. Without structure inputs (a
caller that has none) the allocation is the uniform factor on RCN as before, with the
``uniform_factor`` check.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from basis.config import Settings
from basis.costs.indices.select import IndexValues, book_values, load_index_values
from basis.db.enums import Bucket
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.room import RoomRepository
from basis.domain.basis import (
    BUCKET_ORDER,
    CAPITALIZED,
    ONE,
    REPORT_ROW_SYSTEMS,
    Allocation,
    BasisError,
    BasisWorksheet,
    Check,
    RcnRow,
    allocate,
    worksheet,
)
from basis.domain.costs import year_factor_index
from basis.domain.intake import StudyIntake
from basis.domain.measurements import (
    BuildingMeasurements,
    CostIndex,
    measurements_from_fields,
    resolve_cost_index,
)
from basis.domain.structure import (
    CATEGORIES,
    COMPONENT_NAMES,
    DEFAULT_SPLIT,
    ENGINEER_ROWS,
    SMALL_SYSTEM_LABELS,
    SYSTEM_LABELS,
    BuildingRcn,
    CarveOut,
    Depreciation,
    ItemizedRow,
    Reconciliation,
    SplitMethod,
    StructureInputs,
    building_rcn,
    carve_out,
    cost_approach_from_fields,
    depreciate,
    depreciation,
    inside_model,
    mapping_for,
    reconcile,
    split_building,
    split_shares,
)
from basis.domain.structure import Check as StructureCheck
from basis.evidence.pricing import (
    PricingStatusOut,
    current_pricing,
    load_recorded,
    pricing_method,
    pricing_status,
    structure_inputs_as_priced,
)

NOT_PRODUCED_REASON: Final = (
    "no concealed row of the structure model priced this system (STRUCTURE_MODEL.md 3.5-3.6)"
)
# The concealed systems in the order the result lists them when absent.
NOT_PRODUCED: Final[tuple[tuple[str, str], ...]] = tuple(
    (ENGINEER_ROWS[label], CATEGORIES[label]) for label in SYSTEM_LABELS
)


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    """One asset of the revision as the result sees it."""

    asset_id: str
    category: str
    bucket: str
    rcn: Decimal | None  # None: not priced
    label: str | None = None
    # A row the structure model wrote (``takeoffs.inputs.concealed``); never carved.
    concealed: bool = False


# --------------------------------------------------------------------------- response models


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    severity: Literal["ok", "warning", "error", "not_checked"]
    message: str


class AcquisitionCostOut(BaseModel):
    category: str
    amount: Decimal
    description: str | None
    authority: str = Field(description="Why the category is capitalized.")


class WorksheetOut(BaseModel):
    """``price + capitalized acquisition costs − land`` with every check."""

    purchase_price: Decimal
    acquisition_costs: list[AcquisitionCostOut]
    capitalized_costs: Decimal = Field(description="CC: the sum of the accepted costs.")
    land: Decimal
    land_method: str
    land_source: str = Field(description="Where the land figure comes from, in words.")
    building_basis: Decimal = Field(description="B = purchase price − land.")
    total_basis: Decimal = Field(description="T = B + CC: the depreciable basis.")
    property_use: str
    recovery_period: Decimal = Field(description="27.5 or 39 (years) for the building.")
    placed_in_service_date: date
    acquisition_date: date | None
    state: str | None
    zip: str | None
    location_source: str | None
    checks: list[CheckOut]


class ScheduleBucketOut(BaseModel):
    bucket: Bucket
    assets: int
    rcn: Decimal
    rcnld: Decimal | None = Field(
        default=None, description="RCN less depreciation; null undepreciated."
    )


class ScheduleOut(BaseModel):
    """The revision's replacement-cost schedule, with or without an intake."""

    assets: int
    priced: int
    rcn_total: Decimal
    rcnld_total: Decimal | None = Field(
        default=None, description="The priced rows' RCN less depreciation; null undepreciated."
    )
    concealed: int = Field(default=0, description="Rows the structure model wrote.")
    concealed_rcn: Decimal = Field(default=Decimal("0.00"), description="Their RCN.")
    by_bucket: list[ScheduleBucketOut]
    unpriced: list[uuid.UUID] = Field(description="Assets with no cost (no quantity or no row).")
    land: list[uuid.UUID] = Field(
        description="Assets classified as land: not depreciable, not in the schedule."
    )


class AllocatedAssetOut(BaseModel):
    asset_id: uuid.UUID
    category: str
    bucket: Bucket
    rcn: Decimal
    rcnld: Decimal | None = Field(
        default=None, description="RCN less depreciation (when depreciated)."
    )
    allocated: Decimal
    rounding_cents: int = Field(
        description="Cents this row gained (+) or lost (−) against plain half-up rounding so "
        "the rows sum to the total."
    )
    label: str | None = None
    concealed: bool = False


class BucketAllocationOut(BaseModel):
    bucket: Bucket
    rows: int
    rcn: Decimal
    rcnld: Decimal | None = None
    allocated: Decimal
    percent: Decimal = Field(description="Of the total, to 0.1.")
    share: Decimal = Field(description="Of the total, to 6 places.")
    building: Decimal = Field(description="B × allocated / T (the pro-rata block).")
    closing_costs: Decimal = Field(description="allocated − building; sums to CC.")


class SystemAllocationOut(BaseModel):
    system: str
    rcn: Decimal
    rcnld: Decimal | None = None
    allocated: Decimal


class AllocationOut(BaseModel):
    building_basis: Decimal
    closing_costs: Decimal
    total: Decimal
    rcn_total: Decimal
    rcnld_total: Decimal | None = Field(
        default=None, description="ΣRCNLD when the factor scaled RCNLD (`depreciated`)."
    )
    depreciated: bool = Field(
        default=False,
        description="True: the factor is T / ΣRCNLD (the structure model's method); false: "
        "T / ΣRCN (the uniform factor the ATG objects to).",
    )
    factor: Decimal = Field(description="To 9 places (applied at full precision).")
    rows_sum: Decimal = Field(
        description="The allocated rows' sum; equals `total` by construction."
    )
    residual_cents: int = Field(description="Cents moved by the largest-remainder rule in all.")
    by_bucket: list[BucketAllocationOut]
    by_system: list[SystemAllocationOut] | None = Field(
        description="The twelve building systems (every row is mapped by its label; a row "
        "with no label is Miscellaneous Building Components)."
    )
    by_system_reason: str | None
    assets: list[AllocatedAssetOut] = Field(description="Every row, in asset id order.")
    checks: list[CheckOut]


class NotProducedOut(BaseModel):
    system: str
    reason: str


class ModelOut(BaseModel):
    name: str
    cls: str = Field(description="economy, average, custom or luxury")
    type: str = Field(description="1 story ... 3 story")
    source: str
    class_source: str
    type_source: str


class BuildingRcnOut(BaseModel):
    amount: Decimal
    source: Literal["appraisal_cost_approach", "square_foot_model"]
    formula: str
    inputs: dict[str, str]
    assumptions: list[str]
    checks: list[CheckOut]
    needs_review: bool
    model: ModelOut


class ComponentOut(BaseModel):
    component: str
    name: str
    share: Decimal = Field(description="Of the building RCN after Overhead is spread, to 6 places.")
    top_down: Decimal
    itemized: Decimal
    itemized_assets: int
    unpriced_assets: int
    residual: Decimal = Field(description="top_down − itemized, clamped at zero.")
    clamped: Decimal = Field(description="The excess when the itemized rows exceed the model.")
    concealed: Decimal = Field(description="The concealed rows written against this component.")


class ConcealedOut(BaseModel):
    asset_id: uuid.UUID
    label: str
    system: str = Field(description="The engineer's row.")
    category: str
    bucket: Bucket
    component: str | None
    rcn: Decimal | None


class StructureOut(BaseModel):
    """The structure model as recomputed from the appraisal for this result."""

    building_rcn: BuildingRcnOut | None
    components: list[ComponentOut]
    concealed: list[ConcealedOut] = Field(description="The concealed rows of the schedule.")
    outside_model: Decimal = Field(description="Priced itemized rows the model does not include.")


class DepreciationOut(BaseModel):
    rate: Decimal = Field(description="0..1, applied to every row.")
    method: Literal["appraisal_physical_depreciation", "age_life"]
    source: str
    checks: list[CheckOut]


class CauseOut(BaseModel):
    code: str
    cause: str = Field(description="The guide's words (ATG ch. 3 D(6)).")
    finding: str = Field(description="What this study's data says about it.")


class ComponentLineOut(BaseModel):
    component: str
    top_down: Decimal
    itemized: Decimal
    concealed: Decimal
    clamped: Decimal


class ReconciliationOut(BaseModel):
    status: Literal["reconciled", "unreconciled", "not_checked"]
    building_basis: Decimal = Field(description="B: the purchase price less land.")
    rcn_total: Decimal
    rcnld_total: Decimal | None
    gap: Decimal | None = Field(description="B − RCNLD.")
    gap_share: Decimal | None = Field(description="Of B, to 6 places.")
    tolerance: Decimal
    implied_uniform_factor: Decimal | None = Field(
        description="T / ΣRCN: what the uniform step-up would have been; a diagnostic only."
    )
    by_component: list[ComponentLineOut]
    unassigned_remainders: list[ComponentLineOut] = Field(
        description="Components whose residual has no concealed row (Specialties)."
    )
    outside_model: Decimal
    unpriced_assets: int
    appraisal_check: CheckOut
    causes: list[CauseOut]
    checks: list[CheckOut]


class CostIndexStatusOut(BaseModel):
    """The time index the revision's dollars rest on (COST_INDICES.md section 10): what the
    engineer must enter, when the cost year has no loaded index."""

    cost_year: int
    status: Literal["published", "missing", "converted"] = Field(
        description="`published`: the cost year's index is loaded (RSMeans' value, cited); "
        "`missing`: no index for the year, the latest loaded one is applied and every priced "
        "row is flagged `cost:YEAR_INDEX_MISSING`; `converted`: the year's value was converted "
        "from another series (flagged `cost:YEAR_INDEX_CONVERTED`)."
    )
    factor: Decimal = Field(description="The year factor applied (index / Jan. 1, 2020 index).")
    applied: str = Field(description="The index value and citation the factor rests on.")
    needed: str | None = Field(
        description="When not `published`: the value the engineer must enter, in words."
    )
    how: str | None = Field(description="When not `published`: where to enter it.")
    assumption: str | None = Field(description="The assumption written on every priced row.")


class StudyResultOut(BaseModel):
    """``GET /v1/studies/{id}/result`` and the export's ``summary.basis``."""

    revision: int
    intake: bool = Field(description="Whether the study has an intake.")
    missing: list[str] = Field(
        description="What the result lacks: `intake` (no basis without it), `building_rcn` "
        "(no appraisal cost approach and no living area), `depreciation` (no rate), "
        "`allocation` (nothing priced, or the reconciliation is not within tolerance), "
        "`cost_index` (no loaded index for the cost year: `cost_index` says which value to "
        "enter), `repricing` (the recorded pricing is stale: `pricing` says why), or nothing."
    )
    cost_index: CostIndexStatusOut | None = Field(
        default=None,
        description="The time index the dollars rest on; null without structure inputs.",
    )
    worksheet: WorksheetOut | None
    schedule: ScheduleOut
    structure: StructureOut | None = None
    depreciation: DepreciationOut | None = None
    reconciliation: ReconciliationOut | None = None
    allocation: AllocationOut | None
    not_produced: list[NotProducedOut] = Field(
        description="Concealed systems with no priced row in this schedule, so the gap to the "
        "basis is explicit."
    )
    pricing: PricingStatusOut | None = Field(
        default=None,
        description="What the revision's dollars were priced with (recorded on its cost rows) "
        "against what the study's current intake and index store give. The structure, the "
        "reconciliation and the allocation use the recorded pricing, never a newer index. "
        "When `stale`, the allocation is withheld (`missing` lists `repricing`) until a "
        "reprice builds a revision priced with the current inputs. Null without structure "
        "inputs.",
    )


# --------------------------------------------------------------------------- compute


def _checks(checks: Iterable[Check | StructureCheck]) -> list[CheckOut]:
    return [
        CheckOut.model_validate(
            {
                "code": c.code,
                "severity": getattr(c.severity, "value", c.severity),
                "message": c.message,
            }
        )
        for c in checks
    ]


def worksheet_out(ws: BasisWorksheet, intake: StudyIntake) -> WorksheetOut:
    return WorksheetOut(
        purchase_price=ws.purchase_price,
        acquisition_costs=[
            AcquisitionCostOut(
                category=c.category.value,
                amount=c.amount,
                description=c.description,
                authority=CAPITALIZED[c.category],
            )
            for c in ws.acquisition_costs
        ],
        capitalized_costs=ws.capitalized_costs,
        land=ws.land,
        land_method=ws.land_method.value,
        land_source=ws.land_source,
        building_basis=ws.building_basis,
        total_basis=ws.total_basis,
        property_use=ws.property_use.value,
        recovery_period=ws.recovery_period,
        placed_in_service_date=ws.placed_in_service_date,
        acquisition_date=ws.acquisition_date,
        state=intake.state,
        zip=intake.zip,
        location_source=intake.location_source,
        checks=_checks(ws.checks),
    )


def allocation_out(allocation: Allocation, rows: dict[str, ScheduleRow]) -> AllocationOut:
    return AllocationOut(
        building_basis=allocation.building_basis,
        closing_costs=allocation.closing_costs,
        total=allocation.total,
        rcn_total=allocation.rcn_total,
        rcnld_total=allocation.rcnld_total,
        depreciated=allocation.depreciated,
        factor=allocation.factor,
        rows_sum=allocation.rows_sum,
        residual_cents=allocation.residual_cents,
        by_bucket=[
            BucketAllocationOut(
                bucket=Bucket(t.bucket),
                rows=t.rows,
                rcn=t.rcn,
                rcnld=t.rcnld,
                allocated=t.allocated,
                percent=t.percent,
                share=t.share,
                building=t.building,
                closing_costs=t.closing_costs,
            )
            for t in allocation.by_bucket
        ],
        by_system=(
            None
            if allocation.by_system is None
            else [
                SystemAllocationOut(
                    system=s.system, rcn=s.rcn, rcnld=s.rcnld, allocated=s.allocated
                )
                for s in allocation.by_system
            ]
        ),
        by_system_reason=allocation.by_system_reason,
        assets=[
            AllocatedAssetOut(
                asset_id=uuid.UUID(r.id),
                category=rows[r.id].category,
                bucket=Bucket(r.bucket),
                rcn=r.rcn,
                rcnld=r.rcnld,
                allocated=r.allocated,
                rounding_cents=r.rounding_cents,
                label=rows[r.id].label,
                concealed=rows[r.id].concealed,
            )
            for r in allocation.rows
        ],
        checks=_checks(allocation.checks),
    )


def _structure_out(
    building: BuildingRcn | None,
    carve: CarveOut | None,
    concealed: list[ScheduleRow],
    concealed_by_component: dict[str, Decimal],
    split: SplitMethod = DEFAULT_SPLIT,
) -> StructureOut:
    components: list[ComponentOut] = []
    if building is not None and carve is not None:
        shares = split_shares(building.model, split)
        for c in carve.components:
            components.append(
                ComponentOut(
                    component=c.component,
                    name=COMPONENT_NAMES[c.component],
                    share=shares[c.component],
                    top_down=c.top_down,
                    itemized=c.itemized,
                    itemized_assets=c.itemized_assets,
                    unpriced_assets=c.unpriced_assets,
                    residual=c.residual,
                    clamped=c.clamped,
                    concealed=concealed_by_component.get(c.component, Decimal("0.00")),
                )
            )
    return StructureOut(
        building_rcn=(
            None
            if building is None
            else BuildingRcnOut(
                amount=building.amount,
                source=building.source,
                formula=building.formula,
                inputs=building.inputs,
                assumptions=list(building.assumptions),
                checks=_checks(building.checks),
                needs_review=building.needs_review,
                model=ModelOut(
                    name=building.model.name,
                    cls=building.model.cls,
                    type=building.model.type,
                    source=building.model.cite(),
                    class_source=building.model_choice.class_source,
                    type_source=building.model_choice.type_source,
                ),
            )
        ),
        components=components,
        concealed=[
            ConcealedOut(
                asset_id=uuid.UUID(r.asset_id),
                label=r.label or "",
                system=ENGINEER_ROWS.get(r.label or "", r.category),
                category=r.category,
                bucket=Bucket(r.bucket),
                component=mapping_for(r.label).component,
                rcn=r.rcn,
            )
            for r in concealed
        ],
        outside_model=Decimal("0.00") if carve is None else carve.outside_model,
    )


def _reconciliation_out(r: Reconciliation) -> ReconciliationOut:
    def line(
        component: str, top: Decimal, item: Decimal, conc: Decimal, clamp: Decimal
    ) -> ComponentLineOut:
        return ComponentLineOut(
            component=component, top_down=top, itemized=item, concealed=conc, clamped=clamp
        )

    by_component = {c.component: c for c in r.by_component}
    return ReconciliationOut(
        status=r.status,
        building_basis=r.building_basis,
        rcn_total=r.rcn_total,
        rcnld_total=r.rcnld_total,
        gap=r.gap,
        gap_share=r.gap_share,
        tolerance=r.tolerance,
        implied_uniform_factor=r.implied_uniform_factor,
        by_component=[
            line(c.component, c.top_down, c.itemized, c.concealed, c.clamped)
            for c in r.by_component
        ],
        unassigned_remainders=[
            line(
                component,
                by_component[component].top_down,
                by_component[component].itemized,
                Decimal("0.00"),
                by_component[component].clamped,
            )
            for component, _ in r.unassigned_remainders
            if component in by_component
        ],
        outside_model=r.outside_model,
        unpriced_assets=r.unpriced_assets,
        appraisal_check=_checks([r.appraisal_check])[0],
        causes=[
            CauseOut(code=code, cause=cause, finding=finding) for code, cause, finding in r.causes
        ],
        checks=_checks(r.checks),
    )


def cost_index_status(structure: StructureInputs) -> CostIndexStatusOut:
    """What the cost year's time factor rests on, and what to enter when it is missing."""
    year = structure.year or year_factor_index(structure.cost_year)
    cost_year = structure.cost_year
    if year.assumption is not None:
        status: Literal["published", "missing", "converted"] = "missing"
        needed: str | None = (
            f"the {cost_year} RSMeans Historical Cost Index (Jan. 1, 1993 = 100; the July "
            f"{cost_year} value, as the books' table lists past years) with its citation"
        )
        how: str | None = (
            "POST /v1/cost-indices (series historical, the year, the value, as_of, citation), "
            "python -m basis.costs.refresh enter, or the refresh job with COST_INDEX_FETCH=on "
            "(Gordian publishes the quarterly value free); then rerun the assets step"
        )
    elif year.conversion is not None:
        status, needed, how = (
            "converted",
            f"the published {cost_year} RSMeans Historical Cost Index (the stored value is a "
            "conversion from another series; a published value outranks it)",
            "POST /v1/cost-indices or python -m basis.costs.refresh enter",
        )
    else:
        status, needed, how = "published", None, None
    return CostIndexStatusOut(
        cost_year=cost_year,
        status=status,
        factor=year.factor,
        applied=year.citation(),
        needed=needed,
        how=how,
        assumption=year.assumption or year.conversion,
    )


def compute_result(
    rows: Iterable[ScheduleRow],
    intake: StudyIntake | None,
    revision: int,
    structure: StructureInputs | None = None,
    pricing: PricingStatusOut | None = None,
) -> StudyResultOut:
    """The result of one revision (pure). Rows are taken in asset id order. ``structure``
    is the revision's structure inputs as priced (:func:`load_result_inputs`); a stale
    ``pricing`` withholds the allocation."""
    ordered = sorted(rows, key=lambda r: r.asset_id)
    by_id = {r.asset_id: r for r in ordered}
    land = [uuid.UUID(r.asset_id) for r in ordered if r.bucket == Bucket.LAND.value]
    schedule_rows = [r for r in ordered if r.bucket != Bucket.LAND.value]
    priced = [r for r in schedule_rows if r.rcn is not None]
    unpriced = [uuid.UUID(r.asset_id) for r in schedule_rows if r.rcn is None]
    rcn_of = {r.asset_id: r.rcn for r in priced if r.rcn is not None}
    concealed = [r for r in schedule_rows if r.concealed]
    itemized = [r for r in schedule_rows if not r.concealed]
    zero = Decimal("0.00")

    # The structure model, recomputed from the appraisal (the pure pieces of
    # basis.domain.structure); the concealed rows themselves are in the schedule.
    building: BuildingRcn | None = None
    carve: CarveOut | None = None
    depr: Depreciation | None = None
    if structure is not None:
        building = building_rcn(
            structure.measurements,
            structure.cost_approach,
            cost_year=structure.cost_year,
            year_source=structure.year_source,
            location_factor=structure.location_factor,
            location_source=structure.location_source,
            rcn_source=structure.rcn_source,
            year=structure.year,
            model_override=structure.model_override,
            modifications=structure.modifications,
        )
        depr = depreciation(structure.cost_approach, structure.depreciation_method)
        if building is not None:
            # The small systems inside the model are carved from their component like the
            # itemized rows (STRUCTURE_MODEL.md section 3.6).
            carve = carve_out(
                split_building(building, structure.split),
                [ItemizedRow(r.label, r.rcn) for r in itemized]
                + [
                    ItemizedRow(r.label, r.rcn)
                    for r in concealed
                    if r.label in SMALL_SYSTEM_LABELS and inside_model(r.label)
                ],
                carve=structure.carve,
            )
    concealed_by_component: dict[str, Decimal] = {}
    for r in concealed:
        component = mapping_for(r.label).component
        if component is not None and r.rcn is not None:
            concealed_by_component[component] = concealed_by_component.get(component, zero) + r.rcn

    # Depreciation on every priced row.
    rcnld_of: dict[str, Decimal] = {}
    if depr is not None and priced:
        values = depreciate([rcn_of[r.asset_id] for r in priced], depr.rate)
        rcnld_of = {r.asset_id: v for r, v in zip(priced, values, strict=True)}

    by_bucket = [
        ScheduleBucketOut(
            bucket=Bucket(bucket),
            assets=sum(r.bucket == bucket for r in schedule_rows),
            rcn=sum((rcn_of[r.asset_id] for r in priced if r.bucket == bucket), zero),
            rcnld=(
                sum((rcnld_of[r.asset_id] for r in priced if r.bucket == bucket), zero)
                if rcnld_of
                else None
            ),
        )
        for bucket in BUCKET_ORDER
        if any(r.bucket == bucket for r in schedule_rows)
    ]
    rcn_total = sum(rcn_of.values(), zero)
    rcnld_total = sum(rcnld_of.values(), zero) if rcnld_of else None
    schedule = ScheduleOut(
        assets=len(schedule_rows),
        priced=len(priced),
        rcn_total=rcn_total,
        rcnld_total=rcnld_total,
        concealed=len(concealed),
        concealed_rcn=sum((r.rcn for r in concealed if r.rcn is not None), zero),
        by_bucket=by_bucket,
        unpriced=unpriced,
        land=land,
    )
    produced = {r.label for r in concealed if r.rcn is not None and r.label}
    not_produced = [
        NotProducedOut(
            system=ENGINEER_ROWS[label], reason=f"{CATEGORIES[label]}; {NOT_PRODUCED_REASON}"
        )
        for label in SYSTEM_LABELS
        if label not in produced
    ]

    missing: list[str] = []
    ws_out: WorksheetOut | None = None
    alloc_out: AllocationOut | None = None
    reconciliation_out: ReconciliationOut | None = None
    index_out = None if structure is None else cost_index_status(structure)
    if index_out is not None and index_out.status == "missing":
        missing.append("cost_index")
    if structure is not None and building is None:
        missing.append("building_rcn")
    if structure is not None and depr is None:
        missing.append("depreciation")
    if intake is None:
        missing.append("intake")
    else:
        ws = worksheet(intake.to_basis_inputs())
        ws_out = worksheet_out(ws, intake)
        rcn_rows = [
            RcnRow(
                id=r.asset_id,
                bucket=r.bucket,
                rcn=rcn_of[r.asset_id],
                label=r.category,
                systems=REPORT_ROW_SYSTEMS.get(r.category) or ((mapping_for(r.label).system, ONE),),
                rcnld=rcnld_of.get(r.asset_id),
            )
            for r in priced
        ]
        if structure is not None:
            reconciliation = reconcile(
                building_basis=ws.building_basis,
                total_basis=ws.total_basis,
                rcn_total=rcn_total,
                rcnld_total=rcnld_total,
                building=building,
                carve=carve,
                concealed_by_component=concealed_by_component,
                unpriced_assets=len(unpriced),
                land_checks=[
                    StructureCheck(c.code, c.severity.value, c.message)
                    for c in ws.checks
                    if c.code.startswith("land_")
                ],
                depreciation_=depr,
                purchase_price=ws.purchase_price,
                pricing=structure.pricing(),
            )
            reconciliation_out = _reconciliation_out(reconciliation)
            allocate_rows = reconciliation.status == "reconciled"
            depreciated = True
            if pricing is not None and pricing.stale:
                # Reconciled against dollars the current inputs no longer give: withheld
                # until a reprice (the reconciliation itself is still shown).
                allocate_rows = False
                missing.append("repricing")
        else:
            allocate_rows = True
            depreciated = False
        if allocate_rows:
            try:
                allocation = allocate(
                    rcn_rows, ws.building_basis, ws.capitalized_costs, depreciated=depreciated
                )
            except BasisError:
                # No rows, or a schedule whose RCN is zero: nothing to allocate to.
                missing.append("allocation")
            else:
                alloc_out = allocation_out(allocation, by_id)
        else:
            missing.append("allocation")
    structure_out = (
        None
        if structure is None
        else _structure_out(building, carve, concealed, concealed_by_component, structure.split)
    )
    return StudyResultOut(
        revision=revision,
        intake=intake is not None,
        missing=missing,
        cost_index=index_out,
        worksheet=ws_out,
        schedule=schedule,
        structure=structure_out,
        depreciation=(
            None
            if depr is None
            else DepreciationOut(
                rate=depr.rate, method=depr.method, source=depr.source, checks=_checks(depr.checks)
            )
        ),
        reconciliation=reconciliation_out,
        allocation=alloc_out,
        not_produced=not_produced,
        pricing=pricing,
    )


# --------------------------------------------------------------------------- loading


def concealed_label(inputs: Any) -> tuple[str | None, bool]:
    """``(label, concealed)`` from a takeoff's inputs: the structure model writes
    ``concealed: true`` and the row's ``label``."""
    if not isinstance(inputs, dict):
        return None, False
    concealed = inputs.get("concealed") is True
    label = inputs.get("label")
    return (label if isinstance(label, str) else None), concealed


async def load_schedule(
    session: AsyncSession, study_id: uuid.UUID, revision: int
) -> list[ScheduleRow]:
    """The revision's assets with their cost estimates' totals, labels and concealed flags."""
    repo = AssetRepository(session)
    assets = await repo.list_for_revision(study_id, revision)
    ids = [a.id for a in assets]
    costs = await repo.cost_estimates(ids)
    takeoffs = await repo.takeoffs(ids)
    components = {c.id: c for c in await RoomRepository(session).components(study_id, revision)}
    rows: list[ScheduleRow] = []
    for asset in assets:
        cost = costs.get(asset.id)
        takeoff = takeoffs.get(asset.id)
        component = components.get(asset.component_id) if asset.component_id else None
        label, concealed = concealed_label(takeoff.inputs if takeoff else None)
        rows.append(
            ScheduleRow(
                asset_id=str(asset.id),
                category=asset.category,
                bucket=asset.bucket.value,
                rcn=None if cost is None or cost.total is None else cost.total,
                label=component.label if component is not None else label,
                concealed=concealed,
            )
        )
    return rows


async def load_structure_inputs(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    intake: StudyIntake | None,
    settings: Settings,
) -> StructureInputs | None:
    """The structure inputs of a revision: the appraisal's readings from its extraction
    fields and the study's cost index; None when no extraction has fields."""
    repo = ExtractionRepository(session)
    fields = []
    for extraction in await repo.list_for_revision(study_id, revision):
        fields.extend(await repo.fields(extraction.id))
    if not fields:
        return None
    measurements: BuildingMeasurements | None = measurements_from_fields(fields)
    if measurements is not None and measurements.is_empty:
        measurements = None
    cost_approach = cost_approach_from_fields(fields)
    values = await load_index_values(session, settings)
    return structure_inputs(measurements, cost_approach, intake, settings, values)


async def load_result_inputs(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    intake: StudyIntake | None,
    intake_version: int | None,
    settings: Settings,
) -> tuple[StructureInputs | None, PricingStatusOut | None]:
    """The structure inputs of the revision *as it was priced* (the year, location and
    square-foot model recorded on its cost rows; :mod:`basis.evidence.pricing`) and its
    pricing status against the current intake and index store. ``(None, None)`` when the
    revision has no extraction fields."""
    live = await load_structure_inputs(session, study_id, revision, intake, settings)
    if live is None:
        return None, None
    recorded = await load_recorded(session, study_id, revision)
    values = await load_index_values(session, settings)
    index = resolve_index(live.measurements, intake, settings, values)
    current = current_pricing(
        live,
        intake_version=intake_version if intake is not None else None,
        property_class=intake.property_use.value if intake is not None else settings.property_class,
        location_citation=None if index.location is None else index.location.citation(),
        method=pricing_method(settings),
    )
    status = pricing_status(recorded, current, live)
    return structure_inputs_as_priced(live, recorded), status


def resolve_index(
    measurements: BuildingMeasurements | None,
    intake: StudyIntake | None,
    settings: Settings,
    values: IndexValues | None = None,
) -> CostIndex:
    """The cost index as ``process_assets`` resolves it (``TakeoffPlan.build``)."""
    setting_factor = Decimal(str(settings.cost_location_factor))
    values = values or book_values()
    if settings.cost_index == "study" and (measurements is not None or intake is not None):
        return resolve_cost_index(
            measurements,
            cost_year=settings.cost_year,
            location_factor=setting_factor,
            placed_in_service=None if intake is None else intake.placed_in_service_date,
            state=None if intake is None else intake.state,
            zip_code=None if intake is None else intake.zip,
            supplement=values.years,
            location_tables=values.location_tables,
        )
    return CostIndex(
        settings.cost_year,
        None,
        setting_factor,
        None,
        year_factor_index(settings.cost_year, values.years),
    )


def structure_inputs(
    measurements: BuildingMeasurements | None,
    cost_approach: Any,
    intake: StudyIntake | None,
    settings: Settings,
    values: IndexValues | None = None,
) -> StructureInputs:
    """The inputs with the cost index resolved as ``process_assets`` resolves it, from
    the store's ``values`` (the released book's alone when None)."""
    index = resolve_index(measurements, intake, settings, values)
    return StructureInputs(
        measurements=measurements,
        cost_approach=cost_approach,
        cost_year=index.cost_year,
        year_source=index.year_source,
        location_factor=index.location_factor,
        location_source=index.location_source,
        year=index.year,
        rcn_source=settings.structure_rcn_source,
        modifications=settings.structure_modifications == "on",
        specialties_residual=settings.structure_specialties_residual,
        cost_basis=settings.cost_basis,
        overhead_allocation=settings.structure_overhead_allocation,
    )


__all__ = [
    "NOT_PRODUCED",
    "AllocationOut",
    "ReconciliationOut",
    "ScheduleRow",
    "StructureOut",
    "StudyResultOut",
    "WorksheetOut",
    "compute_result",
    "concealed_label",
    "load_schedule",
    "load_result_inputs",
    "load_structure_inputs",
    "resolve_index",
    "structure_inputs",
]
