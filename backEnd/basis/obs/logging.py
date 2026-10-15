"""Structured JSON logging for Cloud Logging, with request/study/run context.

One JSON object per line on stdout. Cloud Run forwards stdout to Cloud Logging, which
recognises these keys in ``jsonPayload``:

* ``severity``: DEBUG / INFO / WARNING / ERROR / CRITICAL (DEFAULT below DEBUG)
* ``message``: display text
* ``timestamp``: RFC 3339, UTC, microseconds
* ``logging.googleapis.com/trace``: trace correlation (emitted when ``trace`` is bound)
* ``stack_trace``: exception text; Error Reporting groups entries that carry it

Context (``request_id``, ``study_id``, ``run_id``, ``activity`` and anything else) lives
in a ``contextvars.ContextVar`` holding an immutable mapping, so concurrent requests and
asyncio tasks never see each other's fields.

Redaction: any record attribute or context key whose name matches
``(?i)(key|token|secret|password|authorization)`` is replaced by ``"***"``, recursively
inside dicts and lists. Message text itself is not scanned; never interpolate secrets
into messages.
"""

from __future__ import annotations

import json as jsonlib
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final, TextIO

REDACTED: Final = "***"
SENSITIVE_NAME: Final = re.compile(r"(?i)(key|token|secret|password|authorization)")
TRACE_FIELD: Final = "trace"
CLOUD_TRACE_KEY: Final = "logging.googleapis.com/trace"
CONTEXT_ATTR: Final = "basis_context"

_EMPTY: Final[Mapping[str, Any]] = MappingProxyType({})
_context: ContextVar[Mapping[str, Any]] = ContextVar("basis_log_context", default=_EMPTY)

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
# ``color_message`` is uvicorn's ANSI-coloured duplicate of the message.
_STANDARD_ATTRS: Final = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName", "color_message", CONTEXT_ATTR}
)
_RESERVED_KEYS: Final = frozenset(
    {"severity", "message", "logger", "timestamp", "stack_trace", CLOUD_TRACE_KEY}
)
_UVICORN_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access")
_HANDLER_MARK: Final = "_basis_handler"


# --------------------------------------------------------------------------- context


def bind(**fields: Any) -> None:
    """Add fields to the logging context of the current task/thread."""
    _context.set(MappingProxyType({**_context.get(), **fields}))


def unbind(*names: str) -> None:
    """Remove fields from the logging context; unknown names are ignored."""
    current = _context.get()
    _context.set(MappingProxyType({k: v for k, v in current.items() if k not in names}))


def clear_context() -> None:
    """Remove every field from the logging context."""
    _context.set(_EMPTY)


def get_context() -> dict[str, Any]:
    """A copy of the current logging context."""
    return dict(_context.get())


@contextmanager
def bound(**fields: Any) -> Iterator[None]:
    """Bind fields for the duration of a block, then restore the previous context."""
    token: Token[Mapping[str, Any]] = _context.set(MappingProxyType({**_context.get(), **fields}))
    try:
        yield
    finally:
        _context.reset(token)


# --------------------------------------------------------------------------- redaction


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive keys masked, recursing into containers."""
    if isinstance(value, Mapping):
        return {
            k: REDACTED if isinstance(k, str) and SENSITIVE_NAME.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class ContextFilter(logging.Filter):
    """Attach a snapshot of the logging context to the record at emit time."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, CONTEXT_ATTR):
            setattr(record, CONTEXT_ATTR, get_context())
        return True


class RedactionFilter(logging.Filter):
    """Mask sensitive ``extra=`` attributes and context keys on the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in list(vars(record)):
            if name in _STANDARD_ATTRS:
                continue
            if SENSITIVE_NAME.search(name):
                setattr(record, name, REDACTED)
            else:
                setattr(record, name, redact(getattr(record, name)))
        context = getattr(record, CONTEXT_ATTR, None)
        if isinstance(context, Mapping):
            setattr(record, CONTEXT_ATTR, redact(context))
        return True


# --------------------------------------------------------------------------- formatting


def severity_for(levelno: int) -> str:
    """Map a stdlib level number to a Cloud Logging severity name."""
    if levelno >= logging.CRITICAL:
        return "CRITICAL"
    if levelno >= logging.ERROR:
        return "ERROR"
    if levelno >= logging.WARNING:
        return "WARNING"
    if levelno >= logging.INFO:
        return "INFO"
    if levelno >= logging.DEBUG:
        return "DEBUG"
    return "DEFAULT"


def rfc3339(created: float) -> str:
    """UTC RFC 3339 timestamp with microseconds and a ``Z`` suffix."""
    return (
        datetime.fromtimestamp(created, tz=UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    context = getattr(record, CONTEXT_ATTR, None)
    fields: dict[str, Any] = dict(context) if isinstance(context, Mapping) else get_context()
    for name, value in vars(record).items():
        if name not in _STANDARD_ATTRS:
            fields[name] = value
    return fields


class JsonFormatter(logging.Formatter):
    """One JSON object per record, Cloud Logging field names."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": severity_for(record.levelno),
            "message": record.getMessage(),
            "logger": record.name,
            "timestamp": rfc3339(record.created),
        }
        for name, value in _fields(record).items():
            if name == TRACE_FIELD:
                payload[CLOUD_TRACE_KEY] = value
            elif name not in _RESERVED_KEYS:
                payload[name] = value
        stack = self._stack_trace(record)
        if stack:
            payload["stack_trace"] = stack
        return jsonlib.dumps(payload, default=str, ensure_ascii=False)

    def _stack_trace(self, record: logging.LogRecord) -> str:
        parts: list[str] = []
        if record.exc_info:
            parts.append(self.formatException(record.exc_info))
        elif record.exc_text:
            parts.append(record.exc_text)
        if record.stack_info:
            parts.append(self.formatStack(record.stack_info))
        return "\n".join(parts)


class TextFormatter(logging.Formatter):
    """Human-readable lines for local development, same fields and redaction."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = _fields(record)
        if fields:
            rendered = " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
            first, newline, rest = line.partition("\n")
            line = f"{first} [{rendered}]{newline}{rest}"
        return line


# --------------------------------------------------------------------------- setup


def build_handler(stream: TextIO | None = None, *, json: bool = True) -> logging.Handler:
    """A stream handler with context capture, redaction and the chosen formatter."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter() if json else TextFormatter())
    setattr(handler, _HANDLER_MARK, True)
    return handler


def configure_logging(
    level: str | int = "INFO", json: bool = True, *, stream: TextIO | None = None
) -> logging.Handler:
    """Install the Basis handler on the root logger. Idempotent.

    Only handlers previously installed by this function are replaced; other handlers on
    the root logger (for example pytest's capture handler) are left in place. Uvicorn's
    own handlers are removed so its records propagate to the root and are formatted the
    same way.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
            existing.close()
    handler = build_handler(stream, json=json)
    root.addHandler(handler)
    root.setLevel(level.upper() if isinstance(level, str) else level)
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    return handler
