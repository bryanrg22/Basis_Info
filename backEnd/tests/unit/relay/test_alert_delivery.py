"""How the relay delivers an ``alert`` notification (docs/api/RETENTION_AND_ALERTS.md 2.3):
rendered and handed to the email sender with ``ALERT_EMAIL_TO`` as the recipient and the
alert's key as the idempotency key; without a recipient, logged at WARNING with
BASIS-ALERT-004 and delivered; a sender's transient failure is retried, a final refusal is
dead (both BASIS-ALERT-003); a malformed alert is dead; other notification types still go
to the log. No database: ``Relay._deliver`` with a fake sender.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from basis.db.models import OutboxMessage
from basis.db.repositories.alert import STUDY_FAILED
from basis.errors.codes import E
from basis.notify import EmailMessage, EmailRejectedError, EmailSendError, SendResult
from basis.relay.delivery import Outcome, Relay, Result

STUDY = str(uuid.uuid4())


class Sender:
    provider = "fake"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> SendResult:
        if self.error is not None:
            raise self.error
        self.sent.append(message)
        return SendResult(self.provider, f"<{message.idempotency_key}@fake>")


def alert(**extra: Any) -> OutboxMessage:
    payload: dict[str, Any] = {
        "type": "alert",
        "kind": STUDY_FAILED,
        "key": "study-failed:wf:run:record_failure:extracting:r1:1",
        "study_id": STUDY,
        "stage": "extracting",
        "revision": 1,
        "reason": "boom [BASIS-WF-007]",
        "code": "BASIS-WF-007",
        "marked": True,
        "at": datetime.now(UTC).isoformat(),
        **extra,
    }
    return OutboxMessage(id=uuid.uuid4(), kind="notification", payload=payload, attempts=0)


def relay(sender: Sender | None, to: str | None) -> Relay:
    return Relay(None, None, email_sender=sender, alert_to=to, env="staging")  # type: ignore[arg-type]


async def deliver(r: Relay, message: OutboxMessage) -> Result:
    return await r._deliver(message)


async def test_an_alert_is_rendered_and_sent_to_the_configured_address() -> None:
    sender = Sender()
    result = await deliver(relay(sender, "ops@example.test"), alert())

    assert result.outcome is Outcome.DELIVERED and result.error is None
    (email,) = sender.sent
    assert email.to == "ops@example.test"
    assert email.idempotency_key == "study-failed:wf:run:record_failure:extracting:r1:1"
    assert "BASIS-WF-007" in email.subject and STUDY in email.text


async def test_without_a_recipient_the_alert_is_logged_and_delivered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = Sender()
    with caplog.at_level(logging.WARNING, logger="basis.relay.delivery"):
        result = await deliver(relay(sender, None), alert())

    assert result.outcome is Outcome.DELIVERED
    assert sender.sent == []
    record = next(r for r in caplog.records if r.message == "alert (no ALERT_EMAIL_TO)")
    assert record.error_code == E.ALERT_NO_RECIPIENT
    assert STUDY in record.alert_text


async def test_a_transient_sender_failure_is_retried() -> None:
    result = await deliver(relay(Sender(EmailSendError("smtp down")), "ops@x.test"), alert())
    assert result.outcome is Outcome.RETRY
    assert result.error is not None and "smtp down" in result.error


async def test_a_final_refusal_is_dead() -> None:
    result = await deliver(
        relay(Sender(EmailRejectedError("550 no such user")), "o@x.test"), alert()
    )
    assert result.outcome is Outcome.DEAD
    assert result.error is not None and "550" in result.error


async def test_a_malformed_alert_is_dead() -> None:
    sender = Sender()
    result = await deliver(relay(sender, "ops@x.test"), alert(kind="weather"))
    assert result.outcome is Outcome.DEAD and sender.sent == []
    result = await deliver(relay(sender, "ops@x.test"), alert(study_id=None))
    assert result.outcome is Outcome.DEAD


async def test_other_notification_types_still_go_to_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = Sender()
    message = OutboxMessage(
        id=uuid.uuid4(),
        kind="notification",
        payload={"type": "review_pending", "study_id": STUDY},
        attempts=0,
    )
    with caplog.at_level(logging.INFO, logger="basis.relay.delivery"):
        result = await deliver(relay(sender, "ops@x.test"), message)
    assert result.outcome is Outcome.DELIVERED and sender.sent == []
    assert any(record.message == "notification" for record in caplog.records)


def test_the_relay_defaults_to_the_log_sender() -> None:
    r = Relay(None, None)  # type: ignore[arg-type]
    assert r.email_sender.provider == "log" and r.alert_to is None
