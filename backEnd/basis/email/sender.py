"""The transactional email boundary: one message in, one send out.

The split every mature framework makes (Django's ``EmailMessage`` and ``EMAIL_BACKEND``,
Rails' ``ActionMailer`` and its ``delivery_method``): the application renders a
:class:`EmailMessage` (``basis.email.invitation``) and hands it to an :class:`EmailSender`,
which only transports it. Rendering is tested without a transport, and the transport is
chosen by configuration (``EMAIL_PROVIDER``), never by the caller.

Implementations:

* :class:`LoggingEmailSender` (``EMAIL_PROVIDER=log``, the default): logs the rendered
  message at INFO and sends nothing. Development, tests, and a deployment that has no mail
  provider yet (the invitee still sees the invitation in ``GET /v1/me``).
* :class:`SmtpEmailSender` (``EMAIL_PROVIDER=smtp``): the standard library's ``smtplib``
  over STARTTLS (or implicit TLS on port 465), run in a worker thread. Every transactional
  provider (Postmark, SES, Resend, SendGrid, Google Workspace) exposes an SMTP relay, so a
  firm plugs in whichever it has with ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USERNAME``,
  ``SMTP_PASSWORD`` and ``EMAIL_FROM``; nothing is signed up for here.
* A provider's HTTP API (``EMAIL_PROVIDER=<name>``) is the documented slot: implement
  :class:`EmailSender`, map :attr:`EmailMessage.idempotency_key` to the provider's
  idempotency header (Resend ``Idempotency-Key``; Postmark and SES have none, so the
  deterministic ``Message-ID`` below is what they get), and add the name to
  :func:`build_email_sender`.

Idempotency. The relay delivers at least once; the outbox row is marked delivered in the
transaction after the send, so a crash between the two sends the message again. The
message therefore carries a key that names exactly one intended send
(``invitation-{invitation id}``): an HTTP provider that honours it answers the retry
without sending, and SMTP gets it as the ``Message-ID`` (``<key@from-domain>``), which
receiving servers use to collapse duplicates. A key never covers two recipients or two
distinct emails to one recipient (a second invitation is a second key).

Errors. :class:`EmailSendError` is transient (the relay retries with backoff);
:class:`EmailRejectedError` is final (the relay dead-letters the message and records the
reason on the invitation): a recipient the server refuses outright will not be accepted on
the next try.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, parseaddr
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from basis.config import Settings

logger = logging.getLogger(__name__)

PROVIDER_LOG: Final = "log"
PROVIDER_SMTP: Final = "smtp"
# smtplib's socket timeout: below the relay's 30 s delivery timeout, so a hung server is
# reported by the sender (and retried) rather than by the relay cancelling a thread it
# cannot interrupt.
SMTP_TIMEOUT_S: Final = 20.0
IMPLICIT_TLS_PORT: Final = 465
RECENT_SENT: Final = 50


class EmailSendError(Exception):
    """The send failed in a way that may succeed later (connection, 4xx, timeout)."""


class EmailRejectedError(EmailSendError):
    """The server refused the message for good (every recipient rejected, 5xx on data)."""


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A rendered plain-text email. ``idempotency_key`` names this one send."""

    to: str
    subject: str
    text: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("to", "subject", "text", "idempotency_key"):
            if not getattr(self, name).strip():
                raise ValueError(f"EmailMessage.{name} is empty")
        if any(c in self.to for c in "\r\n,;") or any(c in self.subject for c in "\r\n"):
            raise ValueError("EmailMessage.to and .subject must be single-line, one address")


@dataclass(frozen=True, slots=True)
class SendResult:
    """What the transport reported: its name and the message id it assigned or was given."""

    provider: str
    message_id: str


class EmailSender(Protocol):
    @property
    def provider(self) -> str: ...

    async def send(self, message: EmailMessage) -> SendResult:
        """Deliver ``message`` once. Raises :class:`EmailSendError` (retry) or
        :class:`EmailRejectedError` (final)."""
        ...


def from_domain(from_address: str) -> str:
    """The domain of ``EMAIL_FROM`` (``Name <a@b.test>`` or ``a@b.test``), for Message-ID."""
    _, address = parseaddr(from_address)
    return address.rsplit("@", 1)[-1] if "@" in address else "localhost"


def message_id(message: EmailMessage, from_address: str) -> str:
    """The deterministic ``Message-ID`` for ``message``: the same send keeps the same id."""
    return f"<{message.idempotency_key}@{from_domain(from_address)}>"


# --------------------------------------------------------------------------- log


class LoggingEmailSender:
    """Logs the message; sends nothing. The body is logged too (INFO), so an operator reading
    the relay's log can forward the invitation by hand."""

    provider = PROVIDER_LOG

    def __init__(self, from_address: str | None = None) -> None:
        self.from_address = from_address or "Basis <no-reply@localhost>"
        # The last messages "sent", for tests; bounded, since a relay runs for weeks.
        self.sent: deque[EmailMessage] = deque(maxlen=RECENT_SENT)

    async def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        logger.info(
            "email (not sent: EMAIL_PROVIDER=log)",
            extra={
                "email_to": message.to,
                "email_from": self.from_address,
                "email_subject": message.subject,
                "email_idempotency": message.idempotency_key,
                "email_text": message.text,
            },
        )
        return SendResult(self.provider, message_id(message, self.from_address))


# --------------------------------------------------------------------------- smtp


class SmtpEmailSender:
    """``smtplib`` in a thread: STARTTLS on any port but 465, implicit TLS on 465, plain only
    when ``starttls`` is off (local test servers). Credentials are optional (a relay that
    authenticates by network)."""

    provider = PROVIDER_SMTP

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        timeout_s: float = SMTP_TIMEOUT_S,
    ) -> None:
        if not host or not from_address:
            raise ValueError("SMTP needs a host and a from address")
        self.host = host
        self.port = port
        self.from_address = from_address
        self.username = username
        self.password = password
        self.starttls = starttls
        self.timeout_s = timeout_s

    def mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = self.from_address
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=False, usegmt=True)
        mime["Message-ID"] = message_id(message, self.from_address)
        mime["Auto-Submitted"] = "auto-generated"  # RFC 3834: no vacation replies
        mime.set_content(message.text)
        return mime

    def _send_sync(self, message: EmailMessage) -> None:
        mime = self.mime(message)
        context = ssl.create_default_context()
        try:
            if self.port == IMPLICIT_TLS_PORT:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout_s, context=context
                )
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout_s)
            with client:
                client.ehlo()
                if self.starttls and self.port != IMPLICIT_TLS_PORT:
                    client.starttls(context=context)
                    client.ehlo()
                if self.username:
                    client.login(self.username, self.password or "")
                client.send_message(mime)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = {code for code, _ in exc.recipients.values()}
            if codes and all(500 <= code < 600 for code in codes):
                raise EmailRejectedError(f"recipient refused: {_smtp_text(exc)}") from exc
            raise EmailSendError(f"recipient deferred: {_smtp_text(exc)}") from exc
        except smtplib.SMTPDataError as exc:
            if 500 <= exc.smtp_code < 600:
                raise EmailRejectedError(f"message refused: {_smtp_text(exc)}") from exc
            raise EmailSendError(f"message deferred: {_smtp_text(exc)}") from exc
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise EmailSendError(f"{type(exc).__name__}: {_smtp_text(exc)}") from exc

    async def send(self, message: EmailMessage) -> SendResult:
        await asyncio.to_thread(self._send_sync, message)
        return SendResult(self.provider, message_id(message, self.from_address))


def _smtp_text(exc: BaseException) -> str:
    text = str(exc)
    return text if len(text) <= 300 else text[:297] + "..."


# --------------------------------------------------------------------------- factory


def build_email_sender(settings: Settings) -> EmailSender:
    """The sender ``EMAIL_PROVIDER`` names. Settings validation already refused an SMTP
    configuration without a host or a from address."""
    if settings.email_provider == PROVIDER_SMTP:
        assert settings.smtp_host is not None and settings.email_from is not None
        password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            from_address=settings.email_from,
            username=settings.smtp_username,
            password=password,
            starttls=settings.smtp_starttls,
        )
    return LoggingEmailSender(settings.email_from)
