"""The invitation email and the senders (no database).

* Rendering: the text names the firm, the inviter, the role, the invited address, the app
  URL when configured and the expiry; the idempotency key is ``invitation-{id}``; the CLI's
  invitations (no inviter) read "Basis set up ..."; an :class:`EmailMessage` refuses empty
  or multi-line headers.
* :class:`LoggingEmailSender` logs the rendered message and sends nothing.
* :class:`SmtpEmailSender` against a local SMTP server written here: the message is
  delivered once with a deterministic ``Message-ID``; a 5xx on the recipient is
  :class:`EmailRejectedError` (final), a 4xx or a connection failure :class:`EmailSendError`
  (retried).
* :func:`build_email_sender` picks the sender ``EMAIL_PROVIDER`` names.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email import message_from_bytes

import pytest

from basis.config import Settings
from basis.db.enums import OrgRole
from basis.db.models import Invitation
from basis.db.repositories.invitation import InvitationStatus, InvitationView
from basis.email import (
    EmailMessage,
    EmailRejectedError,
    EmailSendError,
    LoggingEmailSender,
    SmtpEmailSender,
    build_email_sender,
)
from basis.email.invitation import idempotency_key, render_invitation
from basis.email.sender import message_id

INVITATION_ID = uuid.UUID("3f2a0d2e-1b7c-4c3a-9a1e-5d6f7a8b9c0d")


def view(
    *,
    role: OrgRole = OrgRole.ENGINEER,
    inviter_name: str | None = "Olivia Owner",
    inviter_email: str | None = "olivia@example.test",
    status: InvitationStatus = InvitationStatus.PENDING,
) -> InvitationView:
    invitation = Invitation(
        id=INVITATION_ID,
        org_id=uuid.uuid4(),
        email="drew@example.test",
        role=role.value,
        created_by=None if inviter_email is None else uuid.uuid4(),
        created_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
        expires_at=datetime(2026, 11, 1, 12, tzinfo=UTC) + timedelta(days=14),
    )
    return InvitationView(invitation, "Harbor Cost Seg", inviter_name, inviter_email, status)


# --------------------------------------------------------------------------- rendering


def test_the_invitation_text_says_who_what_where_and_until_when() -> None:
    message = render_invitation(view(), "https://app.example.test")

    assert message.to == "drew@example.test"
    assert message.subject == "Olivia Owner invited you to Harbor Cost Seg on Basis"
    assert message.idempotency_key == f"invitation-{INVITATION_ID}"
    text = message.text
    assert "Olivia Owner (olivia@example.test) invited you to join Harbor Cost Seg" in text
    assert "as an engineer (creates and reviews studies)." in text
    assert "sign in to Basis at https://app.example.test with this email address" in text
    assert "(drew@example.test)" in text
    assert "expires on 15 November 2026 (UTC)" in text
    assert "nothing happens until you accept" in text
    assert "http" not in text.replace("https://app.example.test", "")  # no other link


def test_without_an_app_url_the_text_says_sign_in_to_basis() -> None:
    text = render_invitation(view(), None).text
    assert "To accept, sign in to Basis with this email address" in text
    assert "http" not in text


@pytest.mark.parametrize(
    ("role", "phrase"),
    [
        (OrgRole.OWNER, "as an owner (the firm's admin: members, invitations and every study)"),
        (OrgRole.VIEWER, "as a viewer (reads studies)"),
    ],
)
def test_the_role_is_explained(role: OrgRole, phrase: str) -> None:
    assert phrase in render_invitation(view(role=role), None).text


def test_an_operator_invitation_names_basis_as_the_inviter() -> None:
    message = render_invitation(view(inviter_name=None, inviter_email=None), None)
    assert message.subject == "You are invited to Harbor Cost Seg on Basis"
    assert "Basis set up Harbor Cost Seg and invited you to join it" in message.text


def test_an_inviter_with_only_an_email_or_only_a_name() -> None:
    by_email = render_invitation(view(inviter_name=None), None)
    assert by_email.subject == "olivia@example.test invited you to Harbor Cost Seg on Basis"
    assert "olivia@example.test invited you to join" in by_email.text
    by_name = render_invitation(view(inviter_email=None), None)
    assert "Olivia Owner invited you to join" in by_name.text
    assert "Olivia Owner (" not in by_name.text and "()" not in by_name.text


def test_the_key_is_one_per_invitation() -> None:
    assert idempotency_key(INVITATION_ID) == f"invitation-{INVITATION_ID}"
    assert idempotency_key(uuid.uuid4()) != idempotency_key(INVITATION_ID)


@pytest.mark.parametrize(
    "fields",
    [
        {"to": ""},
        {"to": "a@b.test\nBcc: c@d.test"},
        {"to": "a@b.test, c@d.test"},
        {"subject": "line\r\nbreak"},
        {"subject": "  "},
        {"text": ""},
        {"idempotency_key": ""},
    ],
)
def test_a_message_with_an_empty_or_injected_header_is_refused(fields: dict[str, str]) -> None:
    base = {"to": "a@b.test", "subject": "s", "text": "t", "idempotency_key": "k"}
    with pytest.raises(ValueError):
        EmailMessage(**{**base, **fields})


def test_message_id_is_deterministic_and_uses_the_from_domain() -> None:
    message = render_invitation(view(), None)
    assert message_id(message, "Basis <no-reply@basis.example>") == (
        f"<invitation-{INVITATION_ID}@basis.example>"
    )
    assert message_id(message, "no-reply@basis.example") == message_id(
        message, "Basis <no-reply@basis.example>"
    )
    assert message_id(message, "nonsense").endswith("@localhost>")


# --------------------------------------------------------------------------- log sender


async def test_the_log_sender_logs_and_sends_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="basis.email.sender")
    sender = LoggingEmailSender("Basis <no-reply@basis.example>")
    message = render_invitation(view(), "https://app.example.test")

    result = await sender.send(message)

    assert (result.provider, result.message_id) == (
        "log",
        f"<invitation-{INVITATION_ID}@basis.example>",
    )
    assert list(sender.sent) == [message]
    [record] = [r for r in caplog.records if r.message.startswith("email (not sent")]
    assert record.email_to == "drew@example.test"  # type: ignore[attr-defined]
    assert record.email_subject == message.subject  # type: ignore[attr-defined]
    assert "sign in to Basis at https://app.example.test" in record.email_text  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- smtp sender


class FakeSmtpServer:
    """Enough of RFC 5321 for one message: EHLO, MAIL, RCPT, DATA, QUIT, no TLS.
    ``rcpt_code`` is the reply to RCPT TO (250 accepts; 550 refuses; 450 defers)."""

    def __init__(self, rcpt_code: int = 250) -> None:
        self.rcpt_code = rcpt_code
        self.messages: list[tuple[str, list[str], bytes]] = []
        self.sessions = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.sessions += 1
        sender, recipients, data = "", [], b""

        def reply(line: str) -> None:
            writer.write(f"{line}\r\n".encode())

        reply("220 fake.test ESMTP")
        await writer.drain()
        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode().rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                reply("250-fake.test")
                reply("250 8BITMIME")
            elif verb == "MAIL":
                sender = line.split(":", 1)[1].strip()
                reply("250 OK")
            elif verb == "RCPT":
                if self.rcpt_code == 250:
                    recipients.append(line.split(":", 1)[1].strip())
                reply(f"{self.rcpt_code} {'OK' if self.rcpt_code == 250 else 'no'}")
            elif verb == "DATA":
                reply("354 go")
                await writer.drain()
                lines: list[bytes] = []
                while True:
                    chunk = await reader.readline()
                    if chunk.rstrip(b"\r\n") == b".":
                        break
                    lines.append(chunk)
                data = b"".join(lines)
                self.messages.append((sender, recipients, data))
                reply("250 queued")
            elif verb == "QUIT":
                reply("221 bye")
                await writer.drain()
                break
            else:
                reply("502 no")
            await writer.drain()
        writer.close()


@pytest.fixture
async def smtp_server() -> AsyncIterator[tuple[FakeSmtpServer, int]]:
    fake = FakeSmtpServer()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield fake, port
    finally:
        server.close()
        await server.wait_closed()


def smtp_sender(port: int) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="127.0.0.1",
        port=port,
        from_address="Basis <no-reply@basis.example>",
        starttls=False,
        timeout_s=5,
    )


async def test_smtp_delivers_the_message_once_with_a_stable_message_id(
    smtp_server: tuple[FakeSmtpServer, int],
) -> None:
    fake, port = smtp_server
    message = render_invitation(view(), "https://app.example.test")

    result = await smtp_sender(port).send(message)

    assert (result.provider, result.message_id) == (
        "smtp",
        f"<invitation-{INVITATION_ID}@basis.example>",
    )
    [(sender, recipients, data)] = fake.messages
    assert sender == "<no-reply@basis.example>" and recipients == ["<drew@example.test>"]
    mime = message_from_bytes(data)
    assert mime["To"] == "drew@example.test"
    assert mime["From"] == "Basis <no-reply@basis.example>"
    assert mime["Subject"] == message.subject
    assert mime["Message-ID"] == result.message_id
    assert mime["Auto-Submitted"] == "auto-generated"
    assert "sign in to Basis at https://app.example.test" in mime.get_payload()
    assert fake.sessions == 1


async def test_smtp_refused_recipient_is_final_and_deferred_is_retried(
    smtp_server: tuple[FakeSmtpServer, int],
) -> None:
    fake, port = smtp_server
    message = render_invitation(view(), None)

    fake.rcpt_code = 550
    with pytest.raises(EmailRejectedError, match="recipient refused"):
        await smtp_sender(port).send(message)
    fake.rcpt_code = 450
    with pytest.raises(EmailSendError, match="recipient deferred") as deferred:
        await smtp_sender(port).send(message)
    assert not isinstance(deferred.value, EmailRejectedError)
    assert fake.messages == []


async def test_smtp_connection_failure_is_retried() -> None:
    with pytest.raises(EmailSendError, match="ConnectionRefusedError") as exc:
        await smtp_sender(1).send(render_invitation(view(), None))  # nothing listens on port 1
    assert not isinstance(exc.value, EmailRejectedError)


def test_smtp_needs_a_host_and_a_from_address() -> None:
    with pytest.raises(ValueError):
        SmtpEmailSender(host="", port=25, from_address="a@b.test")
    with pytest.raises(ValueError):
        SmtpEmailSender(host="h", port=25, from_address="")


# --------------------------------------------------------------------------- factory


def test_build_email_sender_follows_the_provider_setting() -> None:
    log = build_email_sender(Settings(_env_file=None, env="test"))
    assert isinstance(log, LoggingEmailSender) and log.from_address.endswith("@localhost>")
    named = build_email_sender(Settings(_env_file=None, env="test", email_from="a@b.test"))
    assert isinstance(named, LoggingEmailSender) and named.from_address == "a@b.test"
    smtp = build_email_sender(
        Settings(
            _env_file=None,
            env="test",
            email_provider="smtp",
            email_from="Basis <no-reply@basis.example>",
            smtp_host="smtp.example.test",
            smtp_port=2525,
            smtp_username="u",
            smtp_password="p",  # pragma: allowlist secret
            smtp_starttls=False,
        )
    )
    assert isinstance(smtp, SmtpEmailSender)
    assert (smtp.host, smtp.port, smtp.username, smtp.password, smtp.starttls) == (
        "smtp.example.test",
        2525,
        "u",
        "p",
        False,
    )
