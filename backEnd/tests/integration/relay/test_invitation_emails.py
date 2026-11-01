"""Invitation emails through the outbox and the relay, against PostgreSQL (docs/api/FIRMS.md).

* ``InvitationRepository.create`` enqueues the email in the transaction that inserts the
  invitation: a rolled-back create leaves neither; a committed one leaves exactly one
  ``notification`` row of type ``invitation``; a refused create (member, pending) enqueues
  nothing.
* The relay renders it once, hands it to the sender once, records ``emailed_at`` and marks
  the row delivered; a second poll, and a duplicate outbox row for the same invitation,
  send nothing more.
* A sender failure is retried with the outbox's backoff and recorded as ``email_error``
  (``delivery: failed``) until a later attempt succeeds, which clears it; a rejected
  recipient is dead at once; an invitation revoked before the relay ran is not sent.
* The log sender writes the message to the log, with ``APP_URL`` in the text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pytest
from dbtest_support import Factory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import RecordingGateway, make_due, outbox_row, outbox_rows

from basis.db.enums import OrgRole, OutboxStatus
from basis.db.errors import AlreadyMemberError, InvitationPendingError
from basis.db.models import Invitation
from basis.db.repositories import (
    EmailDelivery,
    InvitationRepository,
    OrgRepository,
    OutboxRepository,
)
from basis.db.session import session_scope
from basis.email import (
    EmailMessage,
    EmailRejectedError,
    EmailSendError,
    LoggingEmailSender,
    SendResult,
)
from basis.relay.delivery import Relay

pytestmark = pytest.mark.integration

TTL = timedelta(days=14)
APP = "https://app.example.test"


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


async def firm_with_owner(sm: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope(sm) as session:
        factory = Factory(session)
        owner = await factory.user("olivia", "olivia@example.test")
        owner.display_name = "Olivia Owner"
        org = await factory.org("Harbor Cost Seg")
        await factory.member(org.id, owner, OrgRole.OWNER)
        return org.id, owner.id


async def invite(
    sm: async_sessionmaker[AsyncSession],
    org_id: uuid.UUID,
    created_by: uuid.UUID | None,
    email: str = "drew@example.test",
) -> uuid.UUID:
    async with session_scope(sm) as session:
        await OrgRepository(session).lock(org_id)
        view = await InvitationRepository(session).create(
            org_id, email, OrgRole.ENGINEER, created_by, TTL
        )
        return view.invitation.id


async def invitation(sm: async_sessionmaker[AsyncSession], invitation_id: uuid.UUID) -> Invitation:
    async with sm() as session:
        row = await session.get(Invitation, invitation_id)
        assert row is not None
        return row


def relay_with(sm: async_sessionmaker[AsyncSession], sender: object, **kwargs: object) -> Relay:
    return Relay(sm, RecordingGateway(), email_sender=sender, app_url=APP, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- enqueue


async def test_the_email_is_enqueued_in_the_invitations_transaction(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    org, owner = await firm_with_owner(sm)

    # Rolled back: neither the invitation nor the message exists.
    async with sm() as session:
        async with session.begin():
            view = await InvitationRepository(session).create(
                org, "gone@example.test", OrgRole.VIEWER, owner, TTL
            )
            assert view.delivery is EmailDelivery.QUEUED
            rolled_back = view.invitation.id
            await session.rollback()
    assert await outbox_rows(sm) == []
    async with sm() as session:
        assert await session.get(Invitation, rolled_back) is None

    # Committed: one message, naming the invitation.
    invitation_id = await invite(sm, org, owner)
    [message] = await outbox_rows(sm)
    assert (message.kind, message.status) == ("notification", OutboxStatus.PENDING)
    assert message.payload == {
        "type": "invitation",
        "invitation_id": str(invitation_id),
        "org_id": str(org),
        "email": "drew@example.test",
    }
    row = await invitation(sm, invitation_id)
    assert row.email_queued_at is not None and row.emailed_at is None and row.email_error is None


async def test_a_refused_invitation_enqueues_nothing(sm: async_sessionmaker[AsyncSession]) -> None:
    org, owner = await firm_with_owner(sm)
    await invite(sm, org, owner)
    async with session_scope(sm) as session:
        invitations = InvitationRepository(session)
        with pytest.raises(InvitationPendingError):
            await invitations.create(org, "drew@example.test", OrgRole.OWNER, owner, TTL)
        with pytest.raises(AlreadyMemberError):
            await invitations.create(org, "olivia@example.test", OrgRole.VIEWER, owner, TTL)
    assert len(await outbox_rows(sm)) == 1


# --------------------------------------------------------------------------- deliver


async def test_the_relay_sends_once_and_records_it(sm: async_sessionmaker[AsyncSession]) -> None:
    org, owner = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, owner)
    sender = RecordingSender()
    relay = relay_with(sm, sender)

    report = await relay.run_once()

    assert (report.claimed, report.delivered) == (1, 1)
    [email] = sender.sent
    assert email.to == "drew@example.test"
    assert email.subject == "Olivia Owner invited you to Harbor Cost Seg on Basis"
    assert email.idempotency_key == f"invitation-{invitation_id}"
    assert f"sign in to Basis at {APP} with this email address (drew@example.test)" in email.text
    row = await invitation(sm, invitation_id)
    assert row.emailed_at is not None and row.email_error is None
    async with sm() as session:
        assert (
            await InvitationRepository(session).view(invitation_id)
        ).delivery is EmailDelivery.SENT
    [message] = await outbox_rows(sm)
    assert message.status is OutboxStatus.DELIVERED and message.last_error is None

    # Polling again, and a duplicate message for the same invitation, send nothing more.
    await make_due(sm)
    assert (await relay.run_once()).claimed == 0
    async with session_scope(sm) as session:
        duplicate = await OutboxRepository(session).enqueue(
            "notification", {"type": "invitation", "invitation_id": str(invitation_id)}
        )
    assert (await relay.run_once()).delivered == 1
    assert len(sender.sent) == 1
    assert (await outbox_row(sm, duplicate.id)).status is OutboxStatus.DELIVERED
    assert (await invitation(sm, invitation_id)).emailed_at == row.emailed_at


async def test_a_sender_failure_is_retried_and_shown_until_it_succeeds(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    org, owner = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, owner)
    sender = RecordingSender(fail=EmailSendError("SMTPConnectError: 421 busy"))
    relay = relay_with(sm, sender)

    assert (await relay.run_once()).retried == 1
    [message] = await outbox_rows(sm)
    assert (message.status, message.attempts) == (OutboxStatus.PENDING, 1)
    assert message.last_error == "EmailSendError: SMTPConnectError: 421 busy"
    row = await invitation(sm, invitation_id)
    assert row.emailed_at is None and row.email_error == message.last_error
    async with sm() as session:
        view = await InvitationRepository(session).view(invitation_id)
    assert view.delivery is EmailDelivery.FAILED and view.status.value == "pending"

    sender.fail = None
    await make_due(sm)
    assert (await relay.run_once()).delivered == 1
    row = await invitation(sm, invitation_id)
    assert row.emailed_at is not None and row.email_error is None
    assert len(sender.sent) == 1


async def test_a_rejected_recipient_is_dead_at_once(sm: async_sessionmaker[AsyncSession]) -> None:
    org, owner = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, owner)
    relay = relay_with(sm, RecordingSender(fail=EmailRejectedError("recipient refused: 550 no")))

    assert (await relay.run_once()).dead == 1
    [message] = await outbox_rows(sm)
    assert (message.status, message.attempts) == (OutboxStatus.DEAD, 1)
    row = await invitation(sm, invitation_id)
    assert row.email_error == "email rejected: recipient refused: 550 no"
    assert row.emailed_at is None


async def test_failures_dead_letter_after_max_attempts(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    org, owner = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, owner)
    relay = relay_with(sm, RecordingSender(fail=EmailSendError("down")), max_attempts=3)

    for _ in range(3):
        await relay.run_once()
        await make_due(sm)

    [message] = await outbox_rows(sm)
    assert (message.status, message.attempts) == (OutboxStatus.DEAD, 3)
    assert (await invitation(sm, invitation_id)).email_error == "EmailSendError: down"


async def test_an_invitation_revoked_before_the_relay_ran_is_not_sent(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    org, owner = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, owner)
    async with session_scope(sm) as session:
        await OrgRepository(session).lock(org)
        await InvitationRepository(session).revoke(org, invitation_id, owner)
    sender = RecordingSender()

    report = await relay_with(sm, sender).run_once()

    assert (report.delivered, sender.sent) == (1, [])
    [message] = await outbox_rows(sm)
    assert message.status is OutboxStatus.DELIVERED
    assert message.last_error == "not sent: the invitation was revoked before it was emailed"
    row = await invitation(sm, invitation_id)
    assert row.emailed_at is None and row.email_error == message.last_error


async def test_an_invitation_that_no_longer_exists_is_dead(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(sm) as session:
        await OutboxRepository(session).enqueue(
            "notification", {"type": "invitation", "invitation_id": str(uuid.uuid4())}
        )
    sender = RecordingSender()
    report = await relay_with(sm, sender).run_once()
    assert (report.dead, sender.sent) == (1, [])
    [message] = await outbox_rows(sm)
    assert message.last_error is not None and message.last_error.endswith("not found")


async def test_an_address_the_message_rules_refuse_is_dead_not_retried(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """The invitation pattern admits a comma (visible ASCII); an email header cannot carry
    one. Retrying would not change that: dead at once, the reason on the invitation."""
    org, owner = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, owner, "a,b@example.test")
    sender = RecordingSender()

    report = await relay_with(sm, sender).run_once()

    assert (report.dead, sender.sent) == (1, [])
    row = await invitation(sm, invitation_id)
    assert row.email_error is not None and row.email_error.startswith("cannot render")
    assert row.emailed_at is None


async def test_the_log_sender_logs_the_invitation(
    sm: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    org, _ = await firm_with_owner(sm)
    invitation_id = await invite(sm, org, None)  # as the operator CLI invites
    sender = LoggingEmailSender()

    assert (await relay_with(sm, sender).run_once()).delivered == 1

    [email] = sender.sent
    assert email.subject == "You are invited to Harbor Cost Seg on Basis"
    [logged] = [r for r in caplog.records if r.message.startswith("email (not sent")]
    assert logged.email_to == "drew@example.test"  # type: ignore[attr-defined]
    assert APP in logged.email_text  # type: ignore[attr-defined]
    assert any(
        r.message == "invitation email sent" and r.provider == "log"  # type: ignore[attr-defined]
        for r in caplog.records
    )
    assert (await invitation(sm, invitation_id)).emailed_at is not None
