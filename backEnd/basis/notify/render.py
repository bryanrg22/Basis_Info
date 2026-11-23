"""The alert emails: plain text rendered from an ``alert`` notification payload.

Plain text on purpose (it renders everywhere and carries no tracking), and nothing in it
that is not already in the database row: the study id, the stage, the error code and the
failure reason the engineer sees in the app (the reason is the activity's error message,
which never contains document content or keys: ``basis.errors.codes``), or the service
name and the heartbeat times.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from basis.db.repositories.alert import CAPTURE_BLOCKED, HEARTBEAT_STALE, STUDY_FAILED
from basis.email import EmailMessage

SUBJECT_PREFIX: Final = "[Basis]"
MAX_REASON_CHARS: Final = 600


class MalformedAlert(ValueError):
    """The payload is not an alert this module can render (dead-lettered by the relay)."""


def _when(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "unknown time"
    try:
        return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return value


def _reason(value: object) -> str:
    text = str(value or "").strip() or "(no reason recorded)"
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 3] + "..."


def render_alert(payload: Mapping[str, Any], to: str, *, env: str | None = None) -> EmailMessage:
    """``EmailMessage`` for an ``alert`` payload (``basis.db.repositories.alert``)."""
    kind = payload.get("kind")
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise MalformedAlert("alert payload has no key")
    where = f" ({env})" if env and env not in ("dev", "test") else ""
    if kind == STUDY_FAILED:
        study_id = payload.get("study_id")
        if not study_id:
            raise MalformedAlert("study_failed alert has no study_id")
        stage = payload.get("stage") or "unknown stage"
        revision = payload.get("revision")
        code = payload.get("code") or "no code"
        marked = payload.get("marked", True)
        subject = f"{SUBJECT_PREFIX}{where} Study failed at {stage}: {code}"
        text = (
            f"A study failed and is waiting for a retry.\n\n"
            f"Study:     {study_id}\n"
            f"Stage:     {stage} (revision {revision})\n"
            f"Code:      {code} (docs/ERROR_CODES.md)\n"
            f"When:      {_when(payload.get('at'))}\n"
            f"Reason:    {_reason(payload.get('reason'))}\n"
            + (
                ""
                if marked
                else "\nThe study row had moved on when the failure was recorded, so it is not "
                "marked failed; the failure itself is on its timeline.\n"
            )
            + "\nNext: GET /v1/studies/{id}/timeline?event_type=stage.failed names the step; "
            "fix the cause and POST /v1/studies/{id}/retry.\n"
        )
        return EmailMessage(to=to, subject=subject, text=text, idempotency_key=key)
    if kind == HEARTBEAT_STALE:
        service = payload.get("service")
        if not service:
            raise MalformedAlert("heartbeat_stale alert has no service")
        last = payload.get("last_heartbeat_at")
        stale_for = payload.get("stale_for_s")
        threshold = payload.get("threshold_s")
        subject = f"{SUBJECT_PREFIX}{where} {service} heartbeat is stale"
        seen = "never written a heartbeat" if last is None else f"last heartbeat {_when(last)}"
        stale = "" if stale_for is None else f" ({int(stale_for)} s ago)"
        text = (
            f"The {service} has {seen}{stale}; the alert threshold is {threshold} s.\n\n"
            f"Checked:   {_when(payload.get('at'))}\n"
            f"Service:   {service}\n\n"
            "The process has died, hangs, or cannot reach the database. GET /v1/ops/health "
            "shows what the API sees; check the Cloud Run worker pool's instances and the "
            "worker, relay and cloud-sql-proxy container logs, then restart it. One alert is "
            "sent per stale period; the next is sent only after the service came back and "
            "went stale again.\n"
        )
        return EmailMessage(to=to, subject=subject, text=text, idempotency_key=key)
    if kind == CAPTURE_BLOCKED:
        trip = payload.get("trip")
        code = payload.get("code")
        if not isinstance(trip, int) or not code:
            raise MalformedAlert("capture_blocked alert has no trip or code")
        subject = f"{SUBJECT_PREFIX}{where} Property pictures paused: {code} (trip {trip})"
        text = (
            "A block signal opened the property-capture breaker. Every capture waits until "
            "it closes and then resumes on its own; nothing needs resetting.\n\n"
            f"Code:      {code} (docs/ERROR_CODES.md)\n"
            f"Signal:    {_reason(payload.get('reason'))}\n"
            f"Trip:      {trip} in a row (each doubles the wait)\n"
            f"Opened:    {_when(payload.get('opened_at'))}\n"
            f"Open until: {_when(payload.get('open_until'))}\n"
            f"Study:     {payload.get('study_id') or 'none'}\n"
            f"Page:      {payload.get('url') or 'unknown'} "
            f"(HTTP {payload.get('http_status') or 'n/a'})\n"
            f"Evidence:  {payload.get('evidence') or 'not stored'}\n\n"
            "Next: read the evidence (screenshot, HTML, page record) from GET "
            "/v1/ops/capture-incidents; if trips keep repeating, lower the CAPTURE_* caps or "
            "set PROPERTY_CAPTURE=false. GET /v1/ops/health shows the breaker.\n"
        )
        return EmailMessage(to=to, subject=subject, text=text, idempotency_key=key)
    raise MalformedAlert(f"unknown alert kind {kind!r}")
