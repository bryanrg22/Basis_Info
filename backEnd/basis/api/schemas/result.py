"""Response body of ``GET /v1/studies/{id}/result``: one representation shared with the
export's ``summary.basis`` (:mod:`basis.evidence.result`)."""

from __future__ import annotations

from basis.evidence.result import (
    AcquisitionCostOut,
    AllocatedAssetOut,
    AllocationOut,
    BucketAllocationOut,
    CheckOut,
    NotProducedOut,
    ScheduleBucketOut,
    ScheduleOut,
    StudyResultOut,
    SystemAllocationOut,
    WorksheetOut,
)

__all__ = [
    "AcquisitionCostOut",
    "AllocatedAssetOut",
    "AllocationOut",
    "BucketAllocationOut",
    "CheckOut",
    "NotProducedOut",
    "ScheduleBucketOut",
    "ScheduleOut",
    "StudyResultOut",
    "SystemAllocationOut",
    "WorksheetOut",
]
