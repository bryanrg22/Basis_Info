"""The alert emails and the sender boundary (docs/api/RETENTION_AND_ALERTS.md section 2), no I/O.

* ``render_alert`` turns a ``study_failed`` payload into one plain-text message naming the
  study, stage, code, time and reason, and a ``heartbeat_stale`` payload into one naming
  the service and its last heartbeat; the alert's key is the message's idempotency key; a
  payload without a key, kind, study or service is ``MalformedAlert`` (dead-lettered).
* Nothing secret or long: the reason is cut at 600 characters, the environment is named
  only outside dev and test.
* ``EmailMessage`` refuses empty fields and multi-recipient or multi-line headers;
  ``LoggingEmailSender`` sends nothing, keeps what it was given and returns a deterministic
  Message-ID built from the key.
* The keys: a failure's activity key, a service plus its last heartbeat (or ``never``).
* ``ALERT_EMAIL_TO`` must be one bare address.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from basis.config import ConfigError, Settings
from basis.db.repositories.alert import (
    HEARTBEAT_STALE,
    STUDY_FAILED,
    heartbeat_stale_key,
    study_failed_key,
)
from basis.notify import EmailMessage, LoggingEmailSender, render_alert
from basis.notify.render import MAX_REASON_CHARS, MalformedAlert

STUDY = str(uuid.uuid4())
AT = datetime(2026, 11, 1, 14, 30, 5, tzinfo=UTC)


def failed_payload(**extra: object) -> dict[str, object]:
    return {
        "type": "alert",
        "kind": STUDY_FAILED,
        "key": "study-failed:wf:run:record_failure:extracting:r1:1",
        "study_id": STUDY,
        "stage": "extracting",
        "revision": 1,
        "reason": "IngestError: Azure DI timed out [BASIS-INGEST-003]",
        "code": "BASIS-INGEST-003",
        "marked": True,
        "at": AT.isoformat(),
        **extra,
    }


def stale_payload(**extra: object) -> dict[str, object]:
    return {
        "type": "alert",
        "kind": HEARTBEAT_STALE,
        "key": "heartbeat-stale:worker:2026-11-01T14:00:00+00:00",
        "service": "worker",
        "last_heartbeat_at": "2026-11-01T14:00:00+00:00",
        "stale_for_s": 1805.0,
        "threshold_s": 300.0,
        "at": AT.isoformat(),
        **extra,
    }


def test_study_failed_renders_the_facts_and_the_next_step() -> None:
    email = render_alert(failed_payload(), "ops@example.test", env="staging")

    assert email.to == "ops@example.test"
    assert email.subject == "[Basis] (staging) Study failed at extracting: BASIS-INGEST-003"
    assert email.idempotency_key == "study-failed:wf:run:record_failure:extracting:r1:1"
    for needle in (STUDY, "extracting (revision 1)", "BASIS-INGEST-003", "2026-11-01 14:30:05 UTC"):
        assert needle in email.text
    assert "Azure DI timed out" in email.text
    assert "POST /v1/studies/{id}/retry" in email.text
    assert "moved on" not in email.text


def test_study_failed_says_when_the_row_had_moved_on_and_hides_the_env_in_dev() -> None:
    email = render_alert(failed_payload(marked=False, code=None), "ops@example.test", env="dev")

    assert email.subject == "[Basis] Study failed at extracting: no code"
    assert "not marked failed" in email.text


def test_a_long_reason_is_cut() -> None:
    email = render_alert(failed_payload(reason="x" * 5000), "ops@example.test")
    line = next(line for line in email.text.splitlines() if line.startswith("Reason:"))
    assert len(line) < MAX_REASON_CHARS + 20 and line.endswith("...")


def test_heartbeat_stale_renders_the_service_and_the_silence() -> None:
    email = render_alert(stale_payload(), "ops@example.test", env="staging")

    assert email.subject == "[Basis] (staging) worker heartbeat is stale"
    assert "last heartbeat 2026-11-01 14:00:00 UTC (1805 s ago)" in email.text
    assert "threshold is 300.0 s" in email.text
    assert "GET /v1/ops/health" in email.text
    assert email.idempotency_key == "heartbeat-stale:worker:2026-11-01T14:00:00+00:00"


def test_heartbeat_never_seen() -> None:
    email = render_alert(
        stale_payload(last_heartbeat_at=None, stale_for_s=None, key="heartbeat-stale:relay:never"),
        "ops@example.test",
    )
    assert "never written a heartbeat" in email.text


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "alert", "kind": STUDY_FAILED},  # no key
        failed_payload(study_id=None),
        stale_payload(service=None),
        {"type": "alert", "kind": "weather", "key": "k"},
    ],
)
def test_malformed_alerts_are_refused(payload: dict[str, object]) -> None:
    with pytest.raises(MalformedAlert):
        render_alert(payload, "ops@example.test")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"to": ""},
        {"subject": " "},
        {"text": ""},
        {"idempotency_key": ""},
        {"to": "a@x.test, b@x.test"},
        {"to": "a@x.test\nBcc: c@x.test"},
        {"subject": "line\r\nX-Injected: 1"},
    ],
)
def test_email_message_validation(kwargs: dict[str, str]) -> None:
    values = {"to": "a@x.test", "subject": "s", "text": "t", "idempotency_key": "k", **kwargs}
    with pytest.raises(ValueError):
        EmailMessage(**values)


async def test_logging_sender_sends_nothing_and_names_the_message_by_its_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = LoggingEmailSender("Basis <alerts@basis.test>")
    message = EmailMessage(to="a@x.test", subject="s", text="t", idempotency_key="key-1")
    with caplog.at_level("INFO", logger="basis.email.sender"):
        result = await sender.send(message)

    assert result.provider == "log"
    assert result.message_id == "<key-1@basis.test>"
    assert list(sender.sent) == [message]
    assert any("not sent" in record.message for record in caplog.records)


def test_keys() -> None:
    assert study_failed_key("wf:run:record_failure:extracting:r1:1") == (
        "study-failed:wf:run:record_failure:extracting:r1:1"
    )
    assert heartbeat_stale_key("worker", None) == "heartbeat-stale:worker:never"
    assert heartbeat_stale_key("worker", AT) == "heartbeat-stale:worker:2026-11-01T14:30:05+00:00"
    # The same instant in another zone names the same period.
    from datetime import timedelta, timezone

    local = AT.astimezone(timezone(timedelta(hours=-7)))
    assert heartbeat_stale_key("worker", local) == heartbeat_stale_key("worker", AT)


@pytest.mark.parametrize("bad", ["ops", "Ops <ops@x.test>", "a@x.test,b@x.test", "a@x.test b"])
def test_alert_email_to_must_be_one_bare_address(bad: str) -> None:
    with pytest.raises(ConfigError):
        Settings(_env_file=None, alert_email_to=bad)


def test_alert_settings_defaults() -> None:
    settings = Settings(_env_file=None, alert_email_to="  ops@example.test ")
    assert settings.alert_email_to == "ops@example.test"
    assert settings.alert_heartbeat_stale_seconds == 300.0
    assert settings.alert_check_seconds == 60.0
    assert Settings(_env_file=None, alert_email_to="").alert_email_to is None
