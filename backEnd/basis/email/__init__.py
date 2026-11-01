"""Transactional email: the sender boundary (:mod:`basis.email.sender`) and the invitation
message (:mod:`basis.email.invitation`). Delivered through the outbox by the relay."""

from basis.email.sender import (
    PROVIDER_LOG,
    PROVIDER_SMTP,
    EmailMessage,
    EmailRejectedError,
    EmailSender,
    EmailSendError,
    LoggingEmailSender,
    SendResult,
    SmtpEmailSender,
    build_email_sender,
)

__all__ = [
    "PROVIDER_LOG",
    "PROVIDER_SMTP",
    "EmailMessage",
    "EmailRejectedError",
    "EmailSendError",
    "EmailSender",
    "LoggingEmailSender",
    "SendResult",
    "SmtpEmailSender",
    "build_email_sender",
]
