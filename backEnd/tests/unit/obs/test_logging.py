"""Invariants for basis.obs.logging (BUILD_PLAN 6.1, section 8).

* Every line is one JSON object with Cloud Logging's severity/message/timestamp keys.
* Bound context (request_id, study_id, run_id, activity) appears on every line and is
  gone after it is cleared; concurrent tasks never see each other's context.
* Values under sensitive names are never written.
* Exceptions are written as ``stack_trace`` so Error Reporting picks them up.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from basis.obs.logging import (
    CLOUD_TRACE_KEY,
    REDACTED,
    bind,
    bound,
    clear_context,
    configure_logging,
    get_context,
    redact,
    rfc3339,
    severity_for,
    unbind,
)

RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class Capture:
    """Root logger configured by configure_logging, writing into a buffer."""

    def __init__(self, stream: io.StringIO) -> None:
        self.stream = stream
        self.logger = logging.getLogger("basis.tests.logging")

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line]

    def last(self) -> dict[str, Any]:
        lines = self.lines()
        assert lines, "nothing was logged"
        return lines[-1]


@pytest.fixture
def capture() -> Iterator[Capture]:
    root = logging.getLogger()
    previous_level = root.level
    stream = io.StringIO()
    handler = configure_logging("DEBUG", stream=stream)
    try:
        yield Capture(stream)
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous_level)


# --------------------------------------------------------------------------- shape


def test_line_is_single_json_object_with_cloud_logging_keys(capture: Capture) -> None:
    capture.logger.info("hello %s", "world")
    raw = capture.stream.getvalue()
    assert raw.count("\n") == 1
    entry = json.loads(raw)
    assert entry["severity"] == "INFO"
    assert entry["message"] == "hello world"
    assert entry["logger"] == "basis.tests.logging"
    assert RFC3339_UTC.match(entry["timestamp"]), entry["timestamp"]


def test_timestamp_is_utc_and_close_to_now(capture: Capture) -> None:
    capture.logger.info("now")
    logged = datetime.fromisoformat(capture.last()["timestamp"].replace("Z", "+00:00"))
    assert logged.tzinfo is not None
    assert abs((datetime.now(UTC) - logged).total_seconds()) < 5


def test_rfc3339_known_value() -> None:
    assert rfc3339(0.0) == "1970-01-01T00:00:00.000000Z"
    assert rfc3339(1_700_000_000.25) == "2023-11-14T22:13:20.250000Z"


@pytest.mark.parametrize(
    ("level", "severity"),
    [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ],
)
def test_severity_names(capture: Capture, level: int, severity: str) -> None:
    capture.logger.log(level, "x")
    assert capture.last()["severity"] == severity


@pytest.mark.parametrize(
    ("levelno", "severity"),
    [(5, "DEFAULT"), (15, "DEBUG"), (25, "INFO"), (35, "WARNING"), (45, "ERROR"), (60, "CRITICAL")],
)
def test_custom_levels_map_down(levelno: int, severity: str) -> None:
    assert severity_for(levelno) == severity


def test_message_with_newlines_stays_one_line(capture: Capture) -> None:
    capture.logger.info("line one\nline two")
    raw = capture.stream.getvalue()
    assert raw.count("\n") == 1
    assert json.loads(raw)["message"] == "line one\nline two"


def test_non_json_extra_is_stringified(capture: Capture) -> None:
    class Thing:
        def __str__(self) -> str:
            return "thing!"

    capture.logger.info("x", extra={"thing": Thing()})
    assert capture.last()["thing"] == "thing!"


def test_extra_cannot_overwrite_reserved_keys(capture: Capture) -> None:
    capture.logger.warning("real", extra={"severity": "DEBUG", "timestamp": "forged"})
    entry = capture.last()
    assert entry["severity"] == "WARNING"
    assert entry["timestamp"] != "forged"


# --------------------------------------------------------------------------- context


def test_bound_request_id_appears(capture: Capture) -> None:
    bind(request_id="req-1", study_id="study-9", run_id="run-3", activity="extract")
    capture.logger.info("working")
    entry = capture.last()
    assert entry["request_id"] == "req-1"
    assert entry["study_id"] == "study-9"
    assert entry["run_id"] == "run-3"
    assert entry["activity"] == "extract"


def test_cleared_context_is_absent(capture: Capture) -> None:
    bind(request_id="req-1")
    clear_context()
    capture.logger.info("after clear")
    assert "request_id" not in capture.last()
    assert get_context() == {}


def test_unbind_removes_only_named_fields() -> None:
    bind(request_id="r", study_id="s")
    unbind("study_id", "never_bound")
    assert get_context() == {"request_id": "r"}


def test_get_context_returns_a_copy() -> None:
    bind(request_id="r")
    snapshot = get_context()
    snapshot["request_id"] = "mutated"
    assert get_context() == {"request_id": "r"}


def test_bound_restores_previous_context() -> None:
    bind(request_id="outer")
    with bound(request_id="inner", run_id="run-1"):
        assert get_context() == {"request_id": "inner", "run_id": "run-1"}
    assert get_context() == {"request_id": "outer"}


def test_bound_restores_on_exception() -> None:
    with pytest.raises(RuntimeError), bound(request_id="inner"):
        raise RuntimeError("boom")
    assert get_context() == {}


async def test_concurrent_tasks_do_not_share_context(capture: Capture) -> None:
    async def work(name: str) -> None:
        bind(request_id=name)
        await asyncio.sleep(0)
        capture.logger.info("from %s", name)

    await asyncio.gather(*(work(f"req-{i}") for i in range(20)))
    ours = [e for e in capture.lines() if e["logger"] == capture.logger.name]
    assert len(ours) == 20
    for entry in ours:
        assert entry["message"] == f"from {entry['request_id']}"
    assert get_context() == {}


def test_trace_field_becomes_cloud_trace_key(capture: Capture) -> None:
    trace = "projects/basis-staging/traces/0123456789abcdef0123456789abcdef"
    bind(trace=trace)
    capture.logger.info("traced")
    entry = capture.last()
    assert entry[CLOUD_TRACE_KEY] == trace
    assert "trace" not in entry


def test_no_trace_key_without_bound_trace(capture: Capture) -> None:
    capture.logger.info("untraced")
    assert CLOUD_TRACE_KEY not in capture.last()


# --------------------------------------------------------------------------- redaction


def test_api_key_extra_is_redacted(capture: Capture) -> None:
    capture.logger.info("calling provider", extra={"api_key": "fake-provider-key-1"})
    entry = capture.last()
    assert entry["api_key"] == REDACTED
    assert "fake-provider-key-1" not in capture.stream.getvalue()


def test_bound_authorization_is_redacted(capture: Capture) -> None:
    bind(authorization="Bearer eyJhbGciOi.secret.token", request_id="req-1")
    capture.logger.info("request")
    entry = capture.last()
    assert entry["authorization"] == REDACTED
    assert entry["request_id"] == "req-1"
    assert "eyJhbGciOi" not in capture.stream.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "API_KEY",
        "token",
        "access_token",
        "client_secret",
        "password",
        "db_password",
        "Authorization",
        "x_api_key",
    ],
)
def test_every_sensitive_name_is_redacted(capture: Capture, name: str) -> None:
    capture.logger.info("x", extra={name: "s3cr3t-value"})
    assert capture.last()[name] == REDACTED
    assert "s3cr3t-value" not in capture.stream.getvalue()


def test_nested_sensitive_keys_are_redacted(capture: Capture) -> None:
    capture.logger.info(
        "x",
        extra={"request": {"headers": {"Authorization": "Bearer abc"}, "path": "/health"}},
    )
    entry = capture.last()
    assert entry["request"] == {"headers": {"Authorization": REDACTED}, "path": "/health"}
    assert "Bearer abc" not in capture.stream.getvalue()


def test_nested_in_list_is_redacted() -> None:
    assert redact([{"token": "t"}, {"ok": 1}]) == [{"token": REDACTED}, {"ok": 1}]


def test_innocent_fields_are_not_redacted(capture: Capture) -> None:
    capture.logger.info("x", extra={"study_id": "s-1", "page_count": 12})
    entry = capture.last()
    assert entry["study_id"] == "s-1"
    assert entry["page_count"] == 12


def test_redaction_does_not_alter_bound_context() -> None:
    bind(token="keep-in-memory")
    stream = io.StringIO()
    handler = configure_logging("INFO", stream=stream)
    try:
        logging.getLogger("basis.tests.logging").info("x")
    finally:
        logging.getLogger().removeHandler(handler)
    assert get_context() == {"token": "keep-in-memory"}
    assert "keep-in-memory" not in stream.getvalue()


# --------------------------------------------------------------------------- exceptions


def test_logger_exception_includes_stack_trace(capture: Capture) -> None:
    try:
        raise ValueError("bad value 42")
    except ValueError:
        capture.logger.exception("failed to parse")
    entry = capture.last()
    assert entry["severity"] == "ERROR"
    assert entry["message"] == "failed to parse"
    assert "Traceback (most recent call last)" in entry["stack_trace"]
    assert "ValueError: bad value 42" in entry["stack_trace"]
    assert capture.stream.getvalue().count("\n") == 1


def test_no_stack_trace_without_exception(capture: Capture) -> None:
    capture.logger.error("plain error")
    assert "stack_trace" not in capture.last()


def test_stack_info_included(capture: Capture) -> None:
    capture.logger.warning("where am I", stack_info=True)
    assert "Stack (most recent call last)" in capture.last()["stack_trace"]


# --------------------------------------------------------------------------- setup


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    first = configure_logging("INFO", stream=io.StringIO())
    second = configure_logging("INFO", stream=io.StringIO())
    try:
        assert first not in root.handlers
        assert second in root.handlers
        assert len(root.handlers) == len(before) + 1
    finally:
        root.removeHandler(second)


def test_configure_logging_keeps_foreign_handlers() -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    handler = configure_logging("INFO", stream=io.StringIO())
    try:
        assert foreign in root.handlers
    finally:
        root.removeHandler(handler)
        root.removeHandler(foreign)


def test_configure_logging_sets_level(capture: Capture) -> None:
    root = logging.getLogger()
    previous = root.level
    handler = configure_logging("warning", stream=capture.stream)
    try:
        assert root.level == logging.WARNING
        capture.logger.info("dropped")
        capture.logger.warning("kept")
        assert [e["message"] for e in capture.lines()] == ["kept"]
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def test_uvicorn_loggers_propagate_to_root(capture: Capture) -> None:
    logging.getLogger("uvicorn.error").info("server started")
    assert capture.last()["logger"] == "uvicorn.error"
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).propagate is True
        assert logging.getLogger(name).handlers == []


def test_uvicorn_color_message_is_dropped(capture: Capture) -> None:
    logging.getLogger("uvicorn.error").info(
        "Started server process [%d]",
        42,
        extra={"color_message": "Started server process [\x1b[36m%d\x1b[0m]"},
    )
    entry = capture.last()
    assert entry["message"] == "Started server process [42]"
    assert "color_message" not in entry


def test_text_mode_has_context_and_redaction() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    previous = root.level
    handler = configure_logging("INFO", json=False, stream=stream)
    try:
        bind(request_id="req-7", api_key="fake-provider-key-2")
        logging.getLogger("basis.tests.logging").info("hello")
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    out = stream.getvalue()
    assert "hello" in out
    assert "request_id=req-7" in out
    assert "fake-provider-key-2" not in out
    assert f"api_key={REDACTED}" in out
