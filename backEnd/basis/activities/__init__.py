"""Temporal activities of the StudyWorkflow (S2_SPEC section 4), one module each.

Every activity: ``@activity.defn(name=...)`` with a pydantic input and output from
``basis.workflow.contracts``; claims its idempotency key in ``activity_results`` and
returns the stored output when re-executed (``_support.run_once``); writes its rows and
its completion in one transaction; heartbeats inside loops; propagates cancellation after
rolling back; and raises ``ApplicationError(non_retryable=True)`` with type
``ValidationError`` for input that can never succeed (``BudgetExceededError`` when the
study's LLM budget is exhausted).

Dependencies are bound once by the worker through :class:`~basis.activities.deps.Deps`
(see that module for why the activities are classes).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.deps import Deps
from basis.activities.export_report import ExportReport
from basis.activities.extract_fields import ExtractFields
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.activities.list_photos import ListPhotos
from basis.activities.localize_photo import LocalizePhoto
from basis.activities.mark_stalled import MarkStalled
from basis.activities.notify_review_pending import NotifyReviewPending
from basis.activities.process_assets import ProcessAssets
from basis.activities.reconcile_rooms import ReconcileRooms
from basis.activities.record_failure import RecordFailure
from basis.activities.record_run_end import RecordRunEnd
from basis.activities.record_run_start import RecordRunStart
from basis.activities.transition import Transition

# Registered name -> class. The names are the strings the workflow schedules; the unit
# test tests/unit/activities/test_registry.py checks each class's @activity.defn name.
ACTIVITY_CLASSES: Final[dict[str, type[Any]]] = {
    "record_run_start": RecordRunStart,
    "record_run_end": RecordRunEnd,
    "transition": Transition,
    "list_photos": ListPhotos,
    "ingest_appraisal": IngestAppraisal,
    "extract_fields": ExtractFields,
    "analyze_photo": AnalyzePhoto,
    "localize_photo": LocalizePhoto,
    "reconcile_rooms": ReconcileRooms,
    "apply_corrections": ApplyCorrections,
    "process_assets": ProcessAssets,
    "export_report": ExportReport,
    "notify_review_pending": NotifyReviewPending,
    "mark_stalled": MarkStalled,
    "record_failure": RecordFailure,
}
ACTIVITY_NAMES: Final = tuple(ACTIVITY_CLASSES)


def build_activities(deps: Deps) -> dict[str, Callable[..., Any]]:
    """``{activity name: bound activity}`` for every activity, bound to ``deps``."""
    return {name: cls(deps).run for name, cls in ACTIVITY_CLASSES.items()}


__all__ = ["ACTIVITY_CLASSES", "ACTIVITY_NAMES", "Deps", "build_activities"]
