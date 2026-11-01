"""The transactional email boundary: one rendered message in, one send out.

The split every mature framework makes (Django's ``EmailMessage`` and ``EMAIL_BACKEND``,
Rails' ``ActionMailer`` and its ``delivery_method``): the application renders an
:class:`EmailMessage` and hands it to an :class:`EmailSender`, which only transports it, so
rendering is tested without a transport and the transport is chosen by configuration.

This module is the alert side of a boundary being built in two places at once: the
firm-admin branch defines the same ``EmailMessage``, ``SendResult``, ``EmailSender``,
``EmailSendError``, ``EmailRejectedError`` and ``LoggingEmailSender`` in
``basis.email.sender`` with an SMTP implementation and ``EMAIL_PROVIDER``/``EMAIL_FROM``
settings. The shapes are identical on purpose; at the merge this file is replaced by
``from basis.email.sender import ...`` and :func:`build_email_sender` by that module's.

Idempotency: the relay delivers at least once (the outbox row is marked after the send),
so every message carries a key naming exactly one intended send (the alert's key); an
HTTP provider that honours it answers the retry without sending, and SMTP gets it as the
``Message-ID``, which receiving servers use to collapse duplicates.

Errors: :class:`EmailSendError` is transient (the relay retries with backoff);
:class:`EmailRejectedError` is final (the relay dead-letters the row).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import parseaddr
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from basis.config import Settings

logger = logging.getLogger(__name__)

PROVIDER_LOG: Final = "log"


class EmailSendError(Exception):
    """The send failed in a way that may succeed later (connection, 4xx, timeout)."""


class EmailRejectedError(EmailSendError):
    """The server refused the message for good (recipient rejected, 5xx on data)."""


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
    """The domain of the from address (``Name <a@b.test>`` or ``a@b.test``), for Message-ID."""
    _, address = parseaddr(from_address)
    return address.rsplit("@", 1)[-1] if "@" in address else "localhost"


def message_id(message: EmailMessage, from_address: str) -> str:
    """The deterministic ``Message-ID`` for ``message``: the same send keeps the same id."""
    return f"<{message.idempotency_key}@{from_domain(from_address)}>"


class LoggingEmailSender:
    """Logs the message at INFO and sends nothing: development, tests, and a deployment with
    no mail provider yet. ``sent`` keeps what was "sent" for tests."""

    provider = PROVIDER_LOG

    def __init__(self, from_address: str | None = None) -> None:
        self.from_address = from_address or "Basis <no-reply@localhost>"
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        logger.info(
            "email (not sent: log sender)",
            extra={
                "email_to": message.to,
                "email_from": self.from_address,
                "email_subject": message.subject,
                "email_idempotency": message.idempotency_key,
                "email_text": message.text,
            },
        )
        return SendResult(self.provider, message_id(message, self.from_address))


def build_email_sender(settings: Settings) -> EmailSender:
    """The configured sender. Until the SMTP sender (``basis.email``, firm-admin branch)
    lands, the only transport is the log; the merge replaces this with that module's
    factory, which reads ``EMAIL_PROVIDER`` and ``EMAIL_FROM``."""
    return LoggingEmailSender(getattr(settings, "email_from", None))
