"""Operator alerts: the email boundary, the rendered messages, the heartbeat check.

The transport protocol here (:class:`EmailSender`, :class:`EmailMessage`) has the same
shape as ``basis.email.sender`` on the firm-admin branch (invitation emails); when both
have landed, this module imports those names from ``basis.email`` and keeps only the
alert rendering and the heartbeat monitor.
"""

from basis.notify.render import render_alert
from basis.notify.sender import (
    EmailMessage,
    EmailRejectedError,
    EmailSender,
    EmailSendError,
    LoggingEmailSender,
    SendResult,
    build_email_sender,
)

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
