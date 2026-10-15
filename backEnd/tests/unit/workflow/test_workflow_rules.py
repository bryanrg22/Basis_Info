"""Static guards on the workflow code: activity options and determinism rules.

* Every activity the workflow schedules is registered by the worker and has explicit
  options (timeouts, retries, heartbeat); retry counts follow S2_SPEC section 1.
* The workflow module imports only the standard library, the SDK, pydantic and its own
  package, and never calls the nondeterministic APIs Temporal forbids in workflow code.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio.workflow import ActivityCancellationType

import basis.workflow.study_workflow as study_workflow
from basis.activities import ACTIVITY_NAMES
from basis.workflow.activity_options import (
    ACTIVITY_CANCELLATION,
    DB_ATTEMPTS,
    NON_RETRYABLE_ERROR_TYPES,
    OPTIONS,
    PROVIDER_ATTEMPTS,
    options_for,
)

SOURCE = Path(study_workflow.__file__).read_text()
TREE = ast.parse(SOURCE)

PROVIDER_ACTIVITIES = {
    "ingest_appraisal",
    "extract_fields",
    "analyze_photo",
    "process_assets",
    "export_report",
}
LOOPING_ACTIVITIES = {"ingest_appraisal", "extract_fields", "analyze_photo", "process_assets"}


def scheduled_activity_names() -> set[str]:
    """First string argument of every ``self._activity("name", ...)`` call."""
    names = set()
    for node in ast.walk(TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_activity"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


def test_every_scheduled_activity_is_registered_and_configured() -> None:
    scheduled = scheduled_activity_names()
    assert scheduled == set(ACTIVITY_NAMES)
    assert set(OPTIONS) == set(ACTIVITY_NAMES)


@pytest.mark.parametrize("name", sorted(ACTIVITY_NAMES))
def test_retry_policy_is_explicit(name: str) -> None:
    options = options_for(name)
    policy = options.retry_policy
    expected = PROVIDER_ATTEMPTS if name in PROVIDER_ACTIVITIES else DB_ATTEMPTS
    assert policy.maximum_attempts == expected
    assert list(policy.non_retryable_error_types or []) == list(NON_RETRYABLE_ERROR_TYPES)
    assert options.start_to_close > timedelta(0)
    assert options.schedule_to_close >= options.start_to_close * expected
    if name in LOOPING_ACTIVITIES:
        assert options.heartbeat is not None and options.heartbeat <= timedelta(seconds=30)
    else:
        assert options.heartbeat is None


def test_non_retryable_types_are_validation_and_budget() -> None:
    assert set(NON_RETRYABLE_ERROR_TYPES) == {"ValidationError", "BudgetExceededError"}


def test_cancellation_waits_for_the_activity() -> None:
    assert ACTIVITY_CANCELLATION is ActivityCancellationType.WAIT_CANCELLATION_COMPLETED


def test_unknown_activity_has_no_options() -> None:
    with pytest.raises(KeyError):
        options_for("nope")


ALLOWED_TOP_LEVEL = {"asyncio", "collections", "datetime", "typing", "pydantic", "temporalio"}
ALLOWED_BASIS = {"basis.workflow.activity_options", "basis.workflow.contracts"}


def test_workflow_imports_only_deterministic_modules() -> None:
    for node in ast.walk(TREE):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module.startswith("basis"):
                assert module in ALLOWED_BASIS, module
            elif module != "__future__":
                assert module.split(".")[0] in ALLOWED_TOP_LEVEL, module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in ALLOWED_TOP_LEVEL, alias.name


FORBIDDEN_CALLS = ("datetime.now", "datetime.utcnow", "time.time", "uuid.uuid4", "asyncio.sleep")


def called_names() -> set[str]:
    return {ast.unparse(node.func) for node in ast.walk(TREE) if isinstance(node, ast.Call)}


@pytest.mark.parametrize("forbidden", FORBIDDEN_CALLS)
def test_workflow_avoids_nondeterministic_calls(forbidden: str) -> None:
    assert not any(name.endswith(forbidden) for name in called_names())


def test_workflow_does_not_use_random() -> None:
    assert not any(name.startswith("random.") for name in called_names())


def test_workflow_uses_workflow_time_only() -> None:
    assert "workflow.now()" in SOURCE
