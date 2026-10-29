"""The study result: the basis worksheet and the RCN schedule allocated to it
(docs/api/STUDY_RESULT.md). One representation for the export summary (``summary.basis`` in
``basis.evidence.pack``) and ``GET /v1/studies/{id}/result``.

:func:`compute_result` is pure: the revision's priced assets (category, bucket, replacement
cost new) and the study's intake (or none) give :class:`StudyResultOut`. Without an intake
the schedule is still returned and ``missing`` says what is absent; nothing is fabricated.
``not_produced`` names what the pipeline cannot cost today (the concealed building systems,
STUDY_REPORT_SPEC 5.3) so the gap between the schedule and the basis is explicit: with a
schedule far below the basis the ``uniform_factor`` check is an error (a step-up).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Bucket
from basis.db.repositories.asset import AssetRepository
from basis.domain.basis import (
    BUCKET_ORDER,
    CAPITALIZED,
    REPORT_ROW_SYSTEMS,
    Allocation,
    BasisError,
    BasisWorksheet,
    Check,
    RcnRow,
    allocate,
    worksheet,
)
from basis.domain.intake import StudyIntake

# What photographs cannot establish and no cost model prices yet (COMPONENT_TAXONOMY kind C).
NOT_PRODUCED: Final[tuple[tuple[str, str], ...]] = (
    ("Foundations", "concealed building system; priced from areas and a systems cost model"),
    ("Structural Framing", "concealed building system; priced from areas and a systems cost model"),
    (
        "Interior Framing / Partitions",
        "concealed building system; priced from areas and a systems cost model",
    ),
    ("Site Utilities", "concealed; priced from a site plan and a systems cost model"),
    (
        "Fire Protection & Alarm",
        "a detector in a photo is not the system; priced from a systems cost model",
    ),
    ("Communication / Data", "concealed cabling; priced from a systems cost model"),
    ("Access Control", "not photo-derivable; from the walk-through"),
)
NOT_PRODUCED_REASON: Final = (
    "no building-systems cost model yet (STUDY_REPORT_SPEC 5.3); the schedule holds only "
    "what the photographs show and price"
)


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    """One priced asset of the revision as the allocation sees it."""

    asset_id: str
    category: str
    bucket: str
    rcn: Decimal | None  # None: not priced


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


class ScheduleOut(BaseModel):
    """The revision's replacement-cost schedule, with or without an intake."""

    assets: int
    priced: int
    rcn_total: Decimal
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
    allocated: Decimal
    rounding_cents: int = Field(
        description="Cents this row gained (+) or lost (−) against plain half-up rounding so "
        "the rows sum to the total."
    )


class BucketAllocationOut(BaseModel):
    bucket: Bucket
    rows: int
    rcn: Decimal
    allocated: Decimal
    percent: Decimal = Field(description="Of the total, to 0.1.")
    share: Decimal = Field(description="Of the total, to 6 places.")
    building: Decimal = Field(description="B × allocated / T (the pro-rata block).")
    closing_costs: Decimal = Field(description="allocated − building; sums to CC.")


class SystemAllocationOut(BaseModel):
    system: str
    rcn: Decimal
    allocated: Decimal


class AllocationOut(BaseModel):
    building_basis: Decimal
    closing_costs: Decimal
    total: Decimal
    rcn_total: Decimal
    factor: Decimal = Field(description="T / ΣRCN to 9 places (applied at full precision).")
    rows_sum: Decimal = Field(
        description="The allocated rows' sum; equals `total` by construction."
    )
    residual_cents: int = Field(description="Cents moved by the largest-remainder rule in all.")
    by_bucket: list[BucketAllocationOut]
    by_system: list[SystemAllocationOut] | None = Field(
        description="The twelve building systems when every row maps to one; else null."
    )
    by_system_reason: str | None
    assets: list[AllocatedAssetOut] = Field(description="Every row, in asset id order.")
    checks: list[CheckOut]


class NotProducedOut(BaseModel):
    system: str
    reason: str


class StudyResultOut(BaseModel):
    """``GET /v1/studies/{id}/result`` and the export's ``summary.basis``."""

    revision: int
    intake: bool = Field(description="Whether the study has an intake.")
    missing: list[str] = Field(
        description="What the result lacks: `intake` (no basis without it), `allocation` "
        "(an intake but no priced asset to allocate to), or nothing."
    )
    worksheet: WorksheetOut | None
    schedule: ScheduleOut
    allocation: AllocationOut | None
    not_produced: list[NotProducedOut] = Field(
        description="Systems the pipeline cannot cost today, so the schedule's gap to the basis "
        "is explicit."
    )


# --------------------------------------------------------------------------- compute


def _checks(checks: Iterable[Check]) -> list[CheckOut]:
    return [CheckOut(code=c.code, severity=c.severity.value, message=c.message) for c in checks]


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


def allocation_out(allocation: Allocation, categories: dict[str, str]) -> AllocationOut:
    return AllocationOut(
        building_basis=allocation.building_basis,
        closing_costs=allocation.closing_costs,
        total=allocation.total,
        rcn_total=allocation.rcn_total,
        factor=allocation.factor,
        rows_sum=allocation.rows_sum,
        residual_cents=allocation.residual_cents,
        by_bucket=[
            BucketAllocationOut(
                bucket=Bucket(t.bucket),
                rows=t.rows,
                rcn=t.rcn,
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
                SystemAllocationOut(system=s.system, rcn=s.rcn, allocated=s.allocated)
                for s in allocation.by_system
            ]
        ),
        by_system_reason=allocation.by_system_reason,
        assets=[
            AllocatedAssetOut(
                asset_id=uuid.UUID(r.id),
                category=categories[r.id],
                bucket=Bucket(r.bucket),
                rcn=r.rcn,
                allocated=r.allocated,
                rounding_cents=r.rounding_cents,
            )
            for r in allocation.rows
        ],
        checks=_checks(allocation.checks),
    )


def compute_result(
    rows: Iterable[ScheduleRow], intake: StudyIntake | None, revision: int
) -> StudyResultOut:
    """The result of one revision (pure). Rows are taken in asset id order."""
    ordered = sorted(rows, key=lambda r: r.asset_id)
    land = [uuid.UUID(r.asset_id) for r in ordered if r.bucket == Bucket.LAND.value]
    schedule_rows = [r for r in ordered if r.bucket != Bucket.LAND.value]
    priced = [r for r in schedule_rows if r.rcn is not None]
    unpriced = [uuid.UUID(r.asset_id) for r in schedule_rows if r.rcn is None]
    rcn_of = {r.asset_id: r.rcn for r in priced if r.rcn is not None}
    by_bucket = [
        ScheduleBucketOut(
            bucket=Bucket(bucket),
            assets=sum(r.bucket == bucket for r in schedule_rows),
            rcn=sum((rcn_of[r.asset_id] for r in priced if r.bucket == bucket), Decimal("0.00")),
        )
        for bucket in BUCKET_ORDER
        if any(r.bucket == bucket for r in schedule_rows)
    ]
    schedule = ScheduleOut(
        assets=len(schedule_rows),
        priced=len(priced),
        rcn_total=sum(rcn_of.values(), Decimal("0.00")),
        by_bucket=by_bucket,
        unpriced=unpriced,
        land=land,
    )
    not_produced = [
        NotProducedOut(system=s, reason=f"{why}; {NOT_PRODUCED_REASON}") for s, why in NOT_PRODUCED
    ]
    missing: list[str] = []
    ws_out: WorksheetOut | None = None
    alloc_out: AllocationOut | None = None
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
                systems=REPORT_ROW_SYSTEMS.get(r.category, ()),
            )
            for r in priced
        ]
        try:
            allocation = allocate(rcn_rows, ws.building_basis, ws.capitalized_costs)
        except BasisError:
            # No rows, or a schedule whose RCN is zero: nothing to allocate to.
            missing.append("allocation")
        else:
            alloc_out = allocation_out(allocation, {r.asset_id: r.category for r in priced})
    return StudyResultOut(
        revision=revision,
        intake=intake is not None,
        missing=missing,
        worksheet=ws_out,
        schedule=schedule,
        allocation=alloc_out,
        not_produced=not_produced,
    )


async def load_schedule(
    session: AsyncSession, study_id: uuid.UUID, revision: int
) -> list[ScheduleRow]:
    """The revision's assets with their cost estimates' totals."""
    repo = AssetRepository(session)
    assets = await repo.list_for_revision(study_id, revision)
    costs = await repo.cost_estimates([a.id for a in assets])
    rows: list[ScheduleRow] = []
    for asset in assets:
        cost = costs.get(asset.id)
        rows.append(
            ScheduleRow(
                asset_id=str(asset.id),
                category=asset.category,
                bucket=asset.bucket.value,
                rcn=None if cost is None or cost.total is None else cost.total,
            )
        )
    return rows


__all__ = [
    "NOT_PRODUCED",
    "AllocationOut",
    "ScheduleRow",
    "StudyResultOut",
    "WorksheetOut",
    "compute_result",
    "load_schedule",
]
