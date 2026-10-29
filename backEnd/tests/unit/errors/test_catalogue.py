"""The error-code catalogue is complete and alive (docs/ERROR_CODES.md).

* Every constant on ``E`` has a catalogue entry and every entry a constant; codes are well
  formed and unique; a problem type suffix shared by several entries names its generic.
* No dead entries: every ``error``/``refusal``/``outcome`` code is referenced by the code
  base (``E.<NAME>`` outside ``basis.errors``), and every ``flag`` entry's ``signal`` is a
  string the code base writes on rows (the review-reason vocabulary).
* Every problem type suffix the API raises is catalogued, and every ``ApplicationError``
  type the activities raise has a fallback code.
* The rendered document is current (``python -m basis.errors.catalogue --write``).
* The two live-run failures of EVAL_REPORT 38 map to codes: a heartbeat timeout on
  ``ingest_appraisal`` and ``intake-required`` on start.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    RetryState,
    TimeoutType,
)
from temporalio.exceptions import TimeoutError as ActivityTimeoutError

from basis.errors import catalogue
from basis.errors.catalogue import (
    BY_PROBLEM,
    BY_TEMPORAL_TYPE,
    ENTRIES,
    GENERIC_FOR_PROBLEM,
    TIMEOUT_CODES,
    check_consistent,
    code_for_problem,
    code_for_reason,
    codes_for_reasons,
    render,
)
from basis.errors.codes import CODE_PATTERN, E, all_codes, code_in, tag
from basis.workflow.activity_options import NON_RETRYABLE_ERROR_TYPES
from basis.workflow.study_workflow import _failure

PACKAGE = Path(catalogue.__file__).resolve().parents[1]  # basis/
SOURCES = [p for p in PACKAGE.rglob("*.py") if "errors" not in p.relative_to(PACKAGE).parts]


def referenced_names() -> set[str]:
    """Every ``E.<NAME>`` read anywhere in ``basis`` outside ``basis.errors``."""
    names: set[str] = set()
    for path in SOURCES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "E"
            ):
                names.add(node.attr)
    return names


def source_text() -> str:
    return "\n".join(p.read_text() for p in SOURCES)


def test_catalogue_is_internally_consistent() -> None:
    assert check_consistent() == []
    assert len(ENTRIES) == len(all_codes())
    assert all(CODE_PATTERN.fullmatch(e.code) for e in ENTRIES)


def test_every_code_is_alive() -> None:
    """A code is alive when the code base reaches it: ``E.<NAME>`` at a raise site, the
    problem type suffix it is the generic for (raised somewhere as a literal), a Temporal
    error or timeout type it is the fallback for, or (a flag) the review-reason string it
    stands for being written on rows."""
    names = referenced_names()
    by_code = {code: name for name, code in all_codes().items()}
    text = source_text()
    raised_suffixes = set(re.findall(r'"([a-z0-9-]+)"', text)) | {
        "invitation-accepted",
        "invitation-declined",
        "invitation-revoked",
        "invitation-expired",
    }
    mapped = set(BY_TEMPORAL_TYPE.values()) | set(TIMEOUT_CODES.values())
    dead = []
    for entry in ENTRIES:
        name = by_code[entry.code]
        if entry.kind == "flag":
            assert entry.signal, entry.code
            # The review-reason string (or its prefix) is written by the code base.
            literal = entry.signal.rstrip(":").split(":")[-1]
            if literal not in text:
                dead.append(f"{entry.code}: signal {entry.signal!r} never written")
            continue
        via_problem = (
            entry.problem is not None
            and BY_PROBLEM.get(entry.problem) == entry.code
            and entry.problem in raised_suffixes
        )
        if name not in names and not via_problem and entry.code not in mapped:
            dead.append(f"{entry.code}: E.{name} never referenced")
    assert dead == []


def test_no_unknown_code_literal_in_the_code_base() -> None:
    """Codes are referenced by name, never typed as strings outside the codes module."""
    literals = set(re.findall(r"BASIS-[A-Z]+-\d{3}", source_text()))
    assert literals == set()


def test_every_problem_type_suffix_the_api_raises_is_catalogued() -> None:
    suffixes = set(re.findall(r'type_suffix *= *"([a-z0-9-]+)"', source_text()))
    # ``invitation-{status}`` is built from the invitation's status at its raise site.
    suffixes |= {
        "invitation-accepted",
        "invitation-declined",
        "invitation-revoked",
        "invitation-expired",
    }
    assert suffixes - set(BY_PROBLEM) == set()
    for suffix in GENERIC_FOR_PROBLEM:
        assert code_for_problem(suffix) == GENERIC_FOR_PROBLEM[suffix]
    assert code_for_problem("no-such-suffix") is None


def test_every_application_error_type_has_a_fallback() -> None:
    types = set(
        re.findall(
            r"type=([A-Z_]+_ERROR|[A-Z_]+_UNAVAILABLE|[A-Z_]+_CONFIGURED|SIMULATED_FAILURE)",
            source_text(),
        )
    )
    assert types  # the pattern finds the activities' constants
    for name in NON_RETRYABLE_ERROR_TYPES:
        if name != "ValidationError":  # a ValidationError always carries its own code
            assert name in BY_TEMPORAL_TYPE, name
    assert set(TIMEOUT_CODES) == {t.name for t in TimeoutType}


def test_document_is_current() -> None:
    assert catalogue.DOCUMENT_PATH.read_text() == render(), (
        "docs/ERROR_CODES.md is stale; run: uv run python -m basis.errors.catalogue --write"
    )


def test_tag_and_code_in() -> None:
    message = tag(E.PHOTO_FILE_MISSING, "photo file x is missing")
    assert message == "photo file x is missing [BASIS-PHOTO-002]"
    assert tag(E.PHOTO_NOT_FOUND, message) == message  # never tagged twice
    assert code_in(f"analyze_photo: ValidationError: {message}") == E.PHOTO_FILE_MISSING
    assert code_in("no code here") is None and code_in(None) is None


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("validation:SECTION_BUCKET_MISMATCH", E.CHECK_SECTION_BUCKET_MISMATCH),
        ("cross:COST_ROW_MISSING", E.CHECK_CROSS_COST_ROW_MISSING),
        ("rule_contested:kitchen_cabinet", E.ASSETS_RULE_CONTESTED),
        ("citation_unverified", E.ASSETS_CITATION_UNVERIFIED),
        ("missing:subject.gla", E.CHECK_EXTRACTION_MISSING),
        ("low_confidence:subject.gla=0.40<0.90", E.CHECK_EXTRACTION_LOW_CONFIDENCE),
        ("sources_disagree:subject.gla", E.CHECK_EXTRACTION_SOURCES_DISAGREE),
        ("missing_critical", E.CHECK_EXTRACTION_MISSING_CRITICAL),
        ("takeoff:upper_bound", E.ASSETS_TAKEOFF_UPPER_BOUND),
        ("model:requested review", E.ASSETS_MODEL_REQUESTED_REVIEW),
        ("ambiguous", E.CHECK_PHOTO_AMBIGUOUS),
        ("count_fusion_fallback:ProviderNotConfigured", E.CHECK_PHOTO_COUNT_FUSION_FALLBACK),
        ("invalid_model_output:photo_countables/v3", E.CHECK_PHOTO_INVALID_MODEL_OUTPUT),
        ("provider_error:ProviderUnavailable", E.CHECK_CLASSIFY_PROVIDER_ERROR),
        ("something the catalogue does not know", None),
    ],
)
def test_reasons_map_to_codes(reason: str, code: str | None) -> None:
    assert code_for_reason(reason) == code


def test_room_reading_of_a_shared_reason() -> None:
    assert code_for_reason("invalid_model_output", room=True) == E.CHECK_ROOM_INVALID_MODEL_OUTPUT
    assert codes_for_reasons(["dropped_objects:2", "dropped_objects:3", "x"], room=True) == [
        E.CHECK_ROOM_DROPPED_OBJECTS
    ]


# --------------------------------------------------------------------------- live failures


def activity_error(cause: BaseException, activity_type: str) -> ActivityError:
    error = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="w",
        activity_type=activity_type,
        activity_id=f"{activity_type}:r1",
        retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
    )
    error.__cause__ = cause
    return error


def test_the_heartbeat_timeout_of_the_live_run_has_a_code() -> None:
    """EVAL_REPORT 38: ``ingest_appraisal: activity Heartbeat timeout`` was free text."""
    cause = ActivityTimeoutError(
        "activity Heartbeat timeout", type=TimeoutType.HEARTBEAT, last_heartbeat_details=[]
    )
    reason, code = _failure(activity_error(cause, "ingest_appraisal"))
    assert code == E.WF_HEARTBEAT_TIMEOUT
    assert reason == "ingest_appraisal: activity Heartbeat timeout [BASIS-WF-001]"
    assert code_in(reason) == code


def test_the_intake_refusal_of_the_live_run_has_a_code() -> None:
    assert code_for_problem("intake-required") == E.STUDY_INTAKE_REQUIRED
    assert code_for_problem("appraisal-required") == E.STUDY_APPRAISAL_REQUIRED


def test_failure_reason_keeps_its_prefix_and_carries_the_code() -> None:
    """``<activity>: <type>: ...`` is what the lifecycle tests and the progress page read;
    the code sits inside the message."""
    tagged = ApplicationError(
        tag(E.PROVIDER_UNAVAILABLE, "appraisal x: HTTP 503"), type="ProviderUnavailable"
    )
    reason, code = _failure(activity_error(tagged, "extract_fields"))
    assert reason.startswith("extract_fields: ProviderUnavailable: ")
    assert code == E.PROVIDER_UNAVAILABLE and code in reason
    # An error from before the catalogue (no code in the message): its type's fallback.
    legacy = ApplicationError("No module named 'aiohttp'", type="ProviderUnavailable")
    reason, code = _failure(activity_error(legacy, "extract_fields"))
    assert (
        reason
        == "extract_fields: ProviderUnavailable: No module named 'aiohttp' [BASIS-PROVIDER-007]"
    )
    assert code == E.PROVIDER_UNAVAILABLE
    unknown = ApplicationError("boom", type="SomethingNew")
    assert _failure(activity_error(unknown, "x"))[1] == E.WF_ACTIVITY_FAILED
    cancelled = CancelledError("cancelled")
    assert _failure(activity_error(cancelled, "x"))[1] == E.WF_ACTIVITY_CANCELLED
    plain = RuntimeError("disk full")
    reason, code = _failure(activity_error(plain, "export_report"))
    assert code == E.WF_UNEXPECTED_ERROR and reason == "export_report: disk full [BASIS-WF-007]"
