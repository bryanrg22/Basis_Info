"""The demo request email: plain text to ``DEMO_REQUEST_NOTIFY_EMAIL``, rendered from a
``demo_requests`` row (docs/api/DEMO_REQUESTS.md).

Plain and short on purpose: who asked, their firm, role and email, the note, the page the
form was on and when, so the reader can reply to the visitor without opening anything else.
Everything in it is what the visitor typed, validated as single-line text (the note may have
line breaks), so the subject cannot carry a header break; the note is cut at
:data:`MAX_NOTE_CHARS` only to keep the message short (the whole note stays in the row).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Final

from basis.db.models import DemoRequest
from basis.email.sender import EmailMessage

KEY_PREFIX: Final = "demo-request-"
SUBJECT_PREFIX: Final = "Demo request:"
MAX_SUBJECT_PART: Final = 80
MAX_NOTE_CHARS: Final = 4000
ROLE_LABELS: Final[dict[str, str]] = {
    "owner_partner": "Owner or partner",
    "engineer": "Engineer",
    "analyst_staff": "Analyst or staff",
    "other": "Other",
}


def idempotency_key(demo_request_id: uuid.UUID) -> str:
    """``demo-request-{id}``: one key per stored request, so one send per request."""
    return f"{KEY_PREFIX}{demo_request_id}"


def _short(text: str) -> str:
    return text if len(text) <= MAX_SUBJECT_PART else text[: MAX_SUBJECT_PART - 3] + "..."


def render_demo_request(request: DemoRequest, to: str) -> EmailMessage:
    received = request.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    role = ROLE_LABELS.get(request.role, request.role)
    note = (request.note or "").strip()
    if len(note) > MAX_NOTE_CHARS:
        note = note[: MAX_NOTE_CHARS - 3] + "..."
    lines = [
        "Someone asked for a demo on the Basis site.",
        "",
        f"Name:     {request.name}",
        f"Email:    {request.email}",
        f"Company:  {request.company}",
        f"Role:     {role}",
        f"Received: {received}",
        f"Page:     {request.page or '(not sent)'}",
        "",
        "Note:",
        note or "(none)",
        "",
        f"Reply to {request.email} to set up the meeting.",
        f"Request {request.id}; every request: GET /v1/ops/demo-requests.",
        "",
    ]
    return EmailMessage(
        to=to,
        subject=f"{SUBJECT_PREFIX} {_short(request.name)}, {_short(request.company)}",
        text="\n".join(lines),
        idempotency_key=idempotency_key(request.id),
    )
