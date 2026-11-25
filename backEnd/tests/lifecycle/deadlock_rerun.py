"""One rerun for a lifecycle test that Temporal's deadlock timer failed, and for nothing else.

The Python SDK fails a workflow activation that has not finished 2 s after it started
(``[TMPRL1101] Potential deadlock detected``). The 2 s are wall-clock time on the worker's
event loop, so a test process the machine does not schedule for 2 s trips it with no code at
fault. What was measured (2026-11-25):

* CI run 45484668835, attempt 1: ``test_account_stop.py::
  test_an_account_error_at_the_appraisal_review_is_recorded_there`` failed with TMPRL1101
  2.5 s after its worker started; the rerun of the job passed, and the test passed 4 of 4
  times locally on the same commit.
* Locally every study activation takes under 1 ms (the SDK's ``activate`` timed across
  ``test_account_stop.py`` and ``test_happy_path.py``: 585 activations, the longest 0.9 ms).
* With every one of 12 cores kept busy, a watchdog on the event loop saw the loop late by
  1.08 s while it sat idle in ``select``: the process was not scheduled, nothing of ours ran.
  All 124 lifecycle tests still passed under that load.
* Temporal keeps the 2 s fixed on purpose (temporalio/sdk-python#1050, closed: "we want to
  keep this limited purposefully"). The only off switch is ``debug_mode``, which in SDK 1.35
  runs activations inline on the event loop and may disable the sandbox, so the tests would
  stop running the workflow the way production does. Not used.
* Asking the status query again was tried and rejected: after a TMPRL1101 every later query to
  that run hangs (on the time-skipping test server and on the real dev server; the SDK logs
  "Eviction processed, but 2 tasks remain"). A rerun starts over with a fresh worker and a
  fresh workflow instead.

A real deadlock (workflow code that does not yield) fails again on the rerun, so it still
fails the test; any other failure is not rerun at all (``only_rerun``).
``pytest-rerunfailures`` matches the pattern with ``re.search`` against
``"<exception type>: <message>"`` for every exception in the chain.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final

import pytest

LIFECYCLE_DIR: Final = Path(__file__).resolve().parent
DEADLOCK_PATTERN: Final = r"\[TMPRL1101\] Potential deadlock detected"
DEADLOCK_RERUNS: Final = 1
DEADLOCK_RERUN: Final = pytest.mark.flaky(reruns=DEADLOCK_RERUNS, only_rerun=[DEADLOCK_PATTERN])


def mark_deadlock_reruns(items: Iterable[pytest.Item], directory: Path = LIFECYCLE_DIR) -> None:
    """Give every collected test under ``directory`` the deadlock rerun.

    A conftest's ``pytest_collection_modifyitems`` receives every item of the session, not
    only its own directory's, hence the path filter.
    """
    root = directory.resolve()
    for item in items:
        if item.path.resolve().is_relative_to(root):
            item.add_marker(DEADLOCK_RERUN)
