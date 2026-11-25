"""The lifecycle deadlock rerun (``deadlock_rerun.py``): once, for TMPRL1101 only.

Each case runs an inner pytest session (pytester) whose conftest applies the same rule to its
own directory. A real TMPRL1101 is not provoked here: a workflow made to hold its thread past
2 s is left un-evictable by the SDK and its worker's shutdown hangs (measured 2026-11-25), so
the inner tests raise the exception the client raises, with the SDK's own message.
"""

from __future__ import annotations

import pytest
from deadlock_rerun import LIFECYCLE_DIR

pytest_plugins = ["pytester"]

DEADLOCK_MESSAGE = (
    "[TMPRL1101] Potential deadlock detected: workflow didn't yield within 2 second(s)."
)

CONFTEST = f"""
import sys
sys.path.insert(0, {str(LIFECYCLE_DIR)!r})
from pathlib import Path
from deadlock_rerun import mark_deadlock_reruns

def pytest_collection_modifyitems(config, items):
    mark_deadlock_reruns(items, Path(__file__).parent / "lifecycle")
"""

TESTS = f"""
from pathlib import Path
from temporalio.client import WorkflowQueryFailedError

MESSAGE = {DEADLOCK_MESSAGE!r}
RUNS = Path(__file__).with_name("runs")


def count(name):
    marker = RUNS / name
    RUNS.mkdir(exist_ok=True)
    runs = int(marker.read_text()) + 1 if marker.exists() else 1
    marker.write_text(str(runs))
    return runs


def test_deadlock_once():
    if count("once") == 1:
        raise WorkflowQueryFailedError(MESSAGE)


def test_deadlock_always():
    count("always")
    raise WorkflowQueryFailedError(MESSAGE)


def test_deadlock_wrapped():
    # The harness's wait_for or an assertion may carry it as the cause of another error.
    if count("wrapped") == 1:
        try:
            raise WorkflowQueryFailedError(MESSAGE)
        except WorkflowQueryFailedError as err:
            raise AssertionError("timed out waiting for the account error") from err


def test_other_failure():
    count("other")
    raise WorkflowQueryFailedError("Workflow query failed: not a deadlock")


def test_assertion():
    count("assertion")
    assert 1 == 2
"""


def runs(pytester: pytest.Pytester, name: str) -> int:
    return int((pytester.path / "lifecycle" / "runs" / name).read_text())


def outcomes(result: pytest.RunResult) -> dict[str, int]:
    """The summary line's counts (``assert_outcomes`` does not know ``rerun``), "rerun" as 0
    when there was none."""
    counts = result.parseoutcomes()
    counts.pop("deselected", None)
    counts.pop("warnings", None)
    counts.setdefault("rerun", 0)
    return counts


@pytest.fixture
def inner(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(CONFTEST)
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    pytester.mkdir("lifecycle")
    (pytester.path / "lifecycle" / "test_inner.py").write_text(TESTS)
    (pytester.path / "test_outside.py").write_text(
        "from temporalio.client import WorkflowQueryFailedError\n\n"
        "def test_outside_deadlock():\n"
        f"    raise WorkflowQueryFailedError({DEADLOCK_MESSAGE!r})\n"
    )
    return pytester


def test_a_deadlock_once_is_rerun_and_passes(inner: pytest.Pytester) -> None:
    result = inner.runpytest_inprocess("lifecycle", "-k", "deadlock_once")
    assert outcomes(result) == {"passed": 1, "rerun": 1}
    assert runs(inner, "once") == 2


def test_a_deadlock_every_time_fails_after_exactly_one_rerun(inner: pytest.Pytester) -> None:
    result = inner.runpytest_inprocess("lifecycle", "-k", "deadlock_always")
    # Once: a second timeout is taken as real (the policy, pinned here on purpose).
    assert outcomes(result) == {"failed": 1, "rerun": 1}
    assert runs(inner, "always") == 2


def test_a_deadlock_carried_as_the_cause_is_rerun(inner: pytest.Pytester) -> None:
    result = inner.runpytest_inprocess("lifecycle", "-k", "deadlock_wrapped")
    assert outcomes(result) == {"passed": 1, "rerun": 1}
    assert runs(inner, "wrapped") == 2


@pytest.mark.parametrize("name", ["other_failure", "assertion"])
def test_any_other_failure_is_not_rerun(inner: pytest.Pytester, name: str) -> None:
    result = inner.runpytest_inprocess("lifecycle", "-k", name)
    assert outcomes(result) == {"failed": 1, "rerun": 0}
    assert runs(inner, name.split("_")[0]) == 1


def test_a_test_outside_the_directory_is_not_rerun(inner: pytest.Pytester) -> None:
    result = inner.runpytest_inprocess("test_outside.py")
    assert outcomes(result) == {"failed": 1, "rerun": 0}


def test_this_directorys_tests_carry_the_rerun(request: pytest.FixtureRequest) -> None:
    """The real conftest marks the real lifecycle tests, this one included."""
    marker = request.node.get_closest_marker("flaky")
    assert marker is not None
    assert marker.kwargs["reruns"] == 1
    assert marker.kwargs["only_rerun"] == [r"\[TMPRL1101\] Potential deadlock detected"]
