"""Timeouts, retry policies and heartbeat timeouts for every activity, in one place.

S2_SPEC section 1 and BUILD_PLAN 13.1:

* ``start_to_close_timeout`` is always set (one attempt's limit).
* ``schedule_to_close_timeout`` caps the total time across retries, so a failing
  activity cannot retry forever even if ``maximum_attempts`` were raised.
* ``maximum_attempts`` is explicit: 5 for activities that only touch the database, 3 for
  activities that will call providers (Azure DI, OpenAI, Replicate) from Stage 3 on.
* ``ValidationError``, ``BudgetExceededError`` and ``ProviderAccountError`` never retry:
  retrying cannot fix a bad input, an exhausted budget, or a provider account with no
  credit or a bad key. Activities also raise them with ``non_retryable=True``;
  the list here is the backstop for an error raised without that flag.
* Cancellation waits for the activity to acknowledge (``WAIT_CANCELLATION_COMPLETED``):
  when a study is cancelled, ``record_run_end`` runs only after every in-flight activity
  has stopped (it learns of the cancellation at its next heartbeat, within ~5 s), so no
  activity is still writing rows for a run already marked ``cancelled``. An activity on a
  dead worker resolves at its heartbeat timeout instead.
* A heartbeat timeout is set only on activities that loop (photos, pages, components);
  they heartbeat at least every few seconds, so a crashed worker is detected in
  ``heartbeat_timeout`` instead of after the full ``start_to_close_timeout``.

This module is imported by the workflow, so it uses only the standard library and the
Temporal SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityCancellationType

VALIDATION_ERROR: Final = "ValidationError"
BUDGET_EXCEEDED_ERROR: Final = "BudgetExceededError"
PROVIDER_ACCOUNT_ERROR: Final = "ProviderAccountError"
NON_RETRYABLE_ERROR_TYPES: Final = (VALIDATION_ERROR, BUDGET_EXCEEDED_ERROR, PROVIDER_ACCOUNT_ERROR)

ACTIVITY_CANCELLATION: Final = ActivityCancellationType.WAIT_CANCELLATION_COMPLETED

DB_ATTEMPTS: Final = 5
PROVIDER_ATTEMPTS: Final = 3


def _retry(maximum_attempts: int) -> RetryPolicy:
    return RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=maximum_attempts,
        non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
    )


@dataclass(frozen=True, slots=True)
class ActivityOptions:
    start_to_close: timedelta
    maximum_attempts: int
    heartbeat: timedelta | None = None

    @property
    def retry_policy(self) -> RetryPolicy:
        return _retry(self.maximum_attempts)

    @property
    def schedule_to_close(self) -> timedelta:
        """Every attempt at its full timeout plus the largest possible backoff between them."""
        backoff = timedelta(seconds=30) * (self.maximum_attempts - 1)
        return self.start_to_close * self.maximum_attempts + backoff


_DB_30S: Final = ActivityOptions(timedelta(seconds=30), DB_ATTEMPTS)

OPTIONS: Final[dict[str, ActivityOptions]] = {
    "record_run_start": _DB_30S,
    "record_run_end": _DB_30S,
    "transition": _DB_30S,
    "list_photos": _DB_30S,
    "notify_review_pending": _DB_30S,
    "mark_stalled": _DB_30S,
    "record_failure": _DB_30S,
    "apply_corrections": ActivityOptions(timedelta(minutes=1), DB_ATTEMPTS),
    "ingest_appraisal": ActivityOptions(
        timedelta(minutes=10), PROVIDER_ATTEMPTS, heartbeat=timedelta(seconds=30)
    ),
    "extract_fields": ActivityOptions(
        timedelta(minutes=5), PROVIDER_ATTEMPTS, heartbeat=timedelta(seconds=30)
    ),
    "analyze_photo": ActivityOptions(
        timedelta(minutes=2), PROVIDER_ATTEMPTS, heartbeat=timedelta(seconds=30)
    ),
    # SAM 3 on Replicate: the localizer's own backoff on 429/5xx waits up to ~4 minutes
    # (RETRY_DELAYS_S, the throttle answers 429 for minutes) and a prediction is canceled
    # after 5, so one attempt gets 12; heartbeats continue while it waits.
    "localize_photo": ActivityOptions(
        timedelta(minutes=12), PROVIDER_ATTEMPTS, heartbeat=timedelta(seconds=30)
    ),
    # One reconciliation call per multi-photo room (several concurrently) and a material
    # second look per sensitive surface; the provider's timeout is 60 s per call.
    "reconcile_rooms": ActivityOptions(
        timedelta(minutes=10), PROVIDER_ATTEMPTS, heartbeat=timedelta(seconds=30)
    ),
    "process_assets": ActivityOptions(
        timedelta(minutes=10), PROVIDER_ATTEMPTS, heartbeat=timedelta(seconds=30)
    ),
    "export_report": ActivityOptions(timedelta(minutes=5), PROVIDER_ATTEMPTS),
}


def options_for(activity_name: str) -> ActivityOptions:
    """The options for ``activity_name``; ``KeyError`` for an unknown activity."""
    return OPTIONS[activity_name]
