"""Demo request emails through the outbox and the relay, against PostgreSQL
(docs/api/DEMO_REQUESTS.md section 4).

* ``DemoRequestRepository.record`` enqueues the email in the transaction that stores the
  request: a rolled-back record leaves neither; a committed one exactly one
  ``notification`` row of type ``demo_request`` that carries only the id; a duplicate in the
  window enqueues nothing.
* The relay renders it, sends it once to ``DEMO_REQUEST_NOTIFY_EMAIL`` under the key
  ``demo-request-{id}``, records ``notified_at`` and marks the row delivered; a second poll,
  and a duplicate outbox row for the same request, send nothing more.
* Without a recipient nothing is sent, the row is delivered, the request says why
  (``notify_error``), and the warning carries the code and the id only.
* A sender failure is retried with the outbox's backoff and shown on the request until a
  later attempt succeeds, which clears it; a refused recipient is dead at once; a request
  purged before the relay ran is not sent; a malformed payload is dead.
* With the log sender (the default) the rendered email is written to the relay's log.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import RecordingGateway, make_due, outbox_row, outbox_rows

from basis.db.enums import OutboxStatus
from basis.db.models import DemoRequest
from basis.db.repositories import OutboxRepository
from basis.db.repositories.demo_request import DemoRequestRepository, NewDemoRequest
from basis.db.session import session_scope
from basis.email import (
    EmailMessage,
    EmailRejectedError,
    EmailSendError,
    LoggingEmailSender,
    SendResult,
)
from basis.errors.codes import E
from basis.obs.logging import configure_logging
from basis.relay.delivery import Relay

pytestmark = pytest.mark.integration

FOUNDER = "founder@basis.example"


class RecordingSender:
    provider = "recording"

    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> SendResult:
        if self.fail is not None:
            raise self.fail
        self.sent.append(message)
        return SendResult(self.provider, f"<{message.idempotency_key}@recording>")


def new(**overrides: Any) -> NewDemoRequest:
    values: dict[str, Any] = {
        "name": "Dana Whitfield",
        "email": "dana@acme-eng.example",
        "company": "Acme Engineering",
        "role": "owner_partner",
        "note": "Forty studies a year.\nMostly multifamily.",
        "page": "https://basis.example/directions/demo.html",
        "remote_addr": "203.0.113.7",
        **overrides,
    }
    return NewDemoRequest(**values)


async def record(sm: async_sessionmaker[AsyncSession], **overrides: Any) -> uuid.UUID:
    async with session_scope(sm) as session:
        recorded = await DemoRequestRepository(session).record(new(**overrides))
        return recorded.request.id


async def request_row(
    sm: async_sessionmaker[AsyncSession], demo_request_id: uuid.UUID
) -> DemoRequest:
    async with sm() as session:
        row = await session.get(DemoRequest, demo_request_id)
        assert row is not None
        return row


def relay_with(
    sm: async_sessionmaker[AsyncSession], sender: object, to: str | None = FOUNDER, **kwargs: Any
) -> Relay:
    return Relay(sm, RecordingGateway(), email_sender=sender, demo_request_to=to, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    previous = root.level
    stream = io.StringIO()
    handler = configure_logging("DEBUG", stream=stream)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous)


# --------------------------------------------------------------------------- enqueue


async def test_the_email_is_enqueued_in_the_requests_transaction(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    async with sm() as session:
        async with session.begin():
            recorded = await DemoRequestRepository(session).record(new(email="gone@x.example"))
            assert recorded.created
            rolled_back = recorded.request.id
            await session.rollback()
    assert await outbox_rows(sm) == []
    async with sm() as session:
        assert await session.get(DemoRequest, rolled_back) is None

    demo_request_id = await record(sm)
    [message] = await outbox_rows(sm)
    assert (message.kind, message.status) == ("notification", OutboxStatus.PENDING)
    assert message.payload == {"type": "demo_request", "demo_request_id": str(demo_request_id)}

    # The same email inside the window: the first request, nothing new enqueued.
    async with session_scope(sm) as session:
        again = await DemoRequestRepository(session).record(new(note="again"))
    assert (again.created, again.request.id) == (False, demo_request_id)
    assert len(await outbox_rows(sm)) == 1


# --------------------------------------------------------------------------- deliver


async def test_the_relay_sends_once_and_records_it(sm: async_sessionmaker[AsyncSession]) -> None:
    demo_request_id = await record(sm)
    sender = RecordingSender()
    relay = relay_with(sm, sender)

    report = await relay.run_once()

    assert (report.claimed, report.delivered) == (1, 1)
    [email] = sender.sent
    assert email.to == FOUNDER
    assert email.subject == "Demo request: Dana Whitfield, Acme Engineering"
    assert email.idempotency_key == f"demo-request-{demo_request_id}"
    for line in (
        "Name:     Dana Whitfield",
        "Email:    dana@acme-eng.example",
        "Company:  Acme Engineering",
        "Role:     Owner or partner",
        "Page:     https://basis.example/directions/demo.html",
        "Forty studies a year.\nMostly multifamily.",
        "Reply to dana@acme-eng.example",
    ):
        assert line in email.text
    row = await request_row(sm, demo_request_id)
    assert row.notified_at is not None and row.notify_error is None
    [message] = await outbox_rows(sm)
    assert message.status is OutboxStatus.DELIVERED and message.last_error is None

    # Polling again, and a duplicate message for the same request, send nothing more.
    await make_due(sm)
    assert (await relay.run_once()).claimed == 0
    async with session_scope(sm) as session:
        duplicate = await OutboxRepository(session).enqueue(
            "notification", {"type": "demo_request", "demo_request_id": str(demo_request_id)}
        )
    assert (await relay.run_once()).delivered == 1
    assert len(sender.sent) == 1
    assert (await outbox_row(sm, duplicate.id)).status is OutboxStatus.DELIVERED
    assert (await request_row(sm, demo_request_id)).notified_at == row.notified_at


async def test_without_a_recipient_nothing_is_sent_and_the_request_says_why(
    sm: async_sessionmaker[AsyncSession], log_stream: io.StringIO
) -> None:
    demo_request_id = await record(sm)
    sender = RecordingSender()

    report = await relay_with(sm, sender, to=None).run_once()

    assert (report.delivered, sender.sent) == (1, [])
    [message] = await outbox_rows(sm)
    assert message.status is OutboxStatus.DELIVERED
    assert message.last_error == "not sent: DEMO_REQUEST_NOTIFY_EMAIL is not set"
    row = await request_row(sm, demo_request_id)
    assert row.notified_at is None and row.notify_error == message.last_error
    [warning] = [
        e
        for e in map(json.loads, log_stream.getvalue().splitlines())
        if e["message"].startswith("demo request not emailed")
    ]
    assert warning["severity"] == "WARNING"
    assert warning["error_code"] == E.DEMO_REQUEST_NO_RECIPIENT
    assert warning["demo_request_id"] == str(demo_request_id)
    assert "acme" not in log_stream.getvalue().lower()


async def test_a_sender_failure_is_retried_and_shown_until_it_succeeds(
    sm: async_sessionmaker[AsyncSession], log_stream: io.StringIO
) -> None:
    demo_request_id = await record(sm)
    sender = RecordingSender(fail=EmailSendError("SMTPConnectError: 421 busy"))
    relay = relay_with(sm, sender)

    assert (await relay.run_once()).retried == 1
    [message] = await outbox_rows(sm)
    assert (message.status, message.attempts) == (OutboxStatus.PENDING, 1)
    assert message.last_error == "demo request email failed: SMTPConnectError: 421 busy"
    row = await request_row(sm, demo_request_id)
    assert row.notified_at is None and row.notify_error == message.last_error
    assert '"error_code": "' + E.DEMO_REQUEST_EMAIL_FAILED in log_stream.getvalue()

    sender.fail = None
    await make_due(sm)
    assert (await relay.run_once()).delivered == 1
    row = await request_row(sm, demo_request_id)
    assert row.notified_at is not None and row.notify_error is None
    assert len(sender.sent) == 1


async def test_a_refused_recipient_is_dead_at_once(sm: async_sessionmaker[AsyncSession]) -> None:
    demo_request_id = await record(sm)
    relay = relay_with(sm, RecordingSender(fail=EmailRejectedError("recipient refused: 550 no")))

    assert (await relay.run_once()).dead == 1
    [message] = await outbox_rows(sm)
    assert (message.status, message.attempts) == (OutboxStatus.DEAD, 1)
    row = await request_row(sm, demo_request_id)
    assert row.notify_error == "email rejected: recipient refused: 550 no"
    assert row.notified_at is None


async def test_a_request_purged_before_the_relay_ran_is_not_sent(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    demo_request_id = await record(sm)
    async with session_scope(sm) as session:
        await session.execute(delete(DemoRequest).where(DemoRequest.id == demo_request_id))
    sender = RecordingSender()

    report = await relay_with(sm, sender).run_once()

    assert (report.delivered, sender.sent) == (1, [])
    [message] = await outbox_rows(sm)
    assert message.last_error == "not sent: the demo request was purged first"


@pytest.mark.parametrize(
    "payload", [{}, {"demo_request_id": "not-a-uuid"}, {"demo_request_id": None}]
)
async def test_a_malformed_payload_is_dead(
    sm: async_sessionmaker[AsyncSession], payload: dict[str, Any]
) -> None:
    async with session_scope(sm) as session:
        await OutboxRepository(session).enqueue("notification", {"type": "demo_request", **payload})
    sender = RecordingSender()
    report = await relay_with(sm, sender).run_once()
    assert (report.dead, sender.sent) == (1, [])
    [message] = await outbox_rows(sm)
    assert message.last_error is not None and message.last_error.startswith("malformed payload")


async def test_the_log_sender_writes_the_email_to_the_log(
    sm: async_sessionmaker[AsyncSession], log_stream: io.StringIO
) -> None:
    demo_request_id = await record(sm)
    sender = LoggingEmailSender()
    assert (await relay_with(sm, sender).run_once()).delivered == 1
    [email] = sender.sent
    assert email.to == FOUNDER
    [line] = [
        e
        for e in map(json.loads, log_stream.getvalue().splitlines())
        if e["message"] == "email (not sent: EMAIL_PROVIDER=log)"
    ]
    assert line["email_to"] == FOUNDER
    assert line["email_idempotency"] == f"demo-request-{demo_request_id}"
    assert "Dana Whitfield" in line["email_text"]
    assert (await request_row(sm, demo_request_id)).notified_at is not None
