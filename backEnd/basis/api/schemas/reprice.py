"""Request and response bodies of the assets reprice (docs/api/REPRICE.md): the impact
preview and the engineer's explicit rebuild of the assets step."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Final

from pydantic import Field, model_validator

from basis.api.schemas.common import NO_NUL_PATTERN, RequestModel, ResponseModel, Revision
from basis.api.schemas.reviews import ReviewOut
from basis.db.enums import Bucket, Stage
from basis.domain.structure import BuildingType, Class
from basis.evidence.pricing import PricedWithOut, PricingStatusOut

MAX_REASON: Final = 4000
FINGERPRINT_PATTERN: Final = r"^[0-9a-f]{64}$"
MAX_LIVING_AREA_SF: Final = 100_000

Reason = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_REASON,
        pattern=NO_NUL_PATTERN,
        description="Why the assets are rebuilt (recorded on the correction and the event); "
        "not blank.",
    ),
]


class StructureModelIn(RequestModel):
    """The engineer's confirmation or correction of the structure model's RSMeans
    square-foot model (class of construction, building type, living area), when the
    appraisal fields cannot say it: the class is a judgment from the quality rating, not a
    field of the appraisal. A member left out keeps the model's own choice."""

    model_class: Class | None = Field(
        default=None, description="RSMeans class of construction: economy, average, custom, luxury."
    )
    building_type: BuildingType | None = Field(
        default=None, description="RSMeans building type: 1 story ... 3 story."
    )
    living_area_sf: Annotated[
        int | None,
        Field(gt=0, le=MAX_LIVING_AREA_SF, description="Living area the model is sized by, SF."),
    ] = None

    @model_validator(mode="after")
    def _one_member(self) -> StructureModelIn:
        if self.model_class is None and self.building_type is None and self.living_area_sf is None:
            raise ValueError("give at least one of model_class, building_type, living_area_sf")
        return self


class RepriceIn(RequestModel):
    revision: Revision = Field(description="The assets review revision being rebuilt.")
    fingerprint: Annotated[str, Field(pattern=FINGERPRINT_PATTERN)] = Field(
        description="The `fingerprint` of the preview the engineer reviewed: 409 "
        "`reprice-inputs-changed` when the intake, the index store or the model inputs "
        "changed since."
    )
    reason: Reason
    structure_model: StructureModelIn | None = None

    @model_validator(mode="after")
    def _reason_not_blank(self) -> RepriceIn:
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


class AmountsOut(ResponseModel):
    quantity: Decimal | None
    unit: str | None
    unit_cost: Decimal | None
    total: Decimal | None


class RepriceAssetOut(ResponseModel):
    asset_id: uuid.UUID | None = Field(
        description="The asset of the current revision; null for a row the rebuild adds."
    )
    label: str | None
    category: str
    bucket: Bucket
    concealed: bool
    before: AmountsOut | None = Field(description="Null for a row the rebuild adds.")
    after: AmountsOut | None = Field(description="Null for a row the rebuild drops.")
    change: Decimal | None = Field(description="`after.total - before.total` when both priced.")


class ReconciliationSummaryOut(ResponseModel):
    status: str | None = Field(description="The reconciliation's status; null without one.")
    rcn_total: Decimal
    rcnld_total: Decimal | None
    building_basis: Decimal | None
    gap: Decimal | None = Field(description="RCNLD less the building basis.")
    allocated: bool = Field(description="An allocation is produced (false: withheld).")
    missing: list[str]


class RepricePreviewOut(ResponseModel):
    """What a rebuild of the assets step would change, computed by the same code the
    rebuild runs, in a transaction that is rolled back: nothing is written."""

    revision: int
    to_revision: int
    fingerprint: str = Field(description="Send back with the POST.")
    allowed: bool = Field(description="A rebuild can be requested now.")
    blocked: str | None = Field(description="Why not, when `allowed` is false.")
    blocked_code: str | None
    pricing: PricingStatusOut | None = Field(
        description="The revision's recorded pricing against the current inputs; null when "
        "the revision has no appraisal fields (no structure inputs)."
    )
    assets: list[RepriceAssetOut]
    reclassified: int = Field(
        description="Components with no classification to carry (no asset in the revision): "
        "the rebuild classifies them (rules and conventions; the model when needed)."
    )
    before: ReconciliationSummaryOut
    after: ReconciliationSummaryOut


class RepriceAccepted(ResponseModel):
    """202: the rebuild is recorded as an assets-review rejection carrying a `reprice`
    correction and queued for the workflow (outbox)."""

    review: ReviewOut
    current_stage: Stage
    revision: int = Field(description="The study's revision after the request (the rebuild's).")
    fingerprint: str
    correction_ids: list[uuid.UUID]


__all__ = [
    "PricedWithOut",
    "PricingStatusOut",
    "RepriceAccepted",
    "RepriceAssetOut",
    "RepriceIn",
    "RepricePreviewOut",
    "StructureModelIn",
]
