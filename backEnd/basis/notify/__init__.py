"""Operator alerts: the rendered messages and the heartbeat check.

The email boundary (:class:`EmailSender`, :class:`EmailMessage`, the senders and
:func:`build_email_sender`) is ``basis.email``, shared with the invitation emails; it is
re-exported here so the alert code and its tests name one place.
"""

from basis.email import (
    EmailMessage,
    EmailRejectedError,
    EmailSender,
    EmailSendError,
    LoggingEmailSender,
    SendResult,
    build_email_sender,
)
from basis.notify.render import render_alert

__all__ = [
    "EmailMessage",
    "EmailRejectedError",
    "EmailSendError",
    "EmailSender",
    "LoggingEmailSender",
    "SendResult",
    "build_email_sender",
    "render_alert",
]
