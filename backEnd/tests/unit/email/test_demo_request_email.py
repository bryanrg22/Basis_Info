"""The demo request email (no database; docs/api/DEMO_REQUESTS.md section 4).

* The text has who asked, their email, firm, role (as the form labels it), when (UTC), the
  page and the note with its line breaks, and says to reply to the visitor; the subject is
  "Demo request: <name>, <company>", each part cut at 80 characters; the key is
  ``demo-request-{id}``.
* A request without a note or page says so; a very long note is cut in the email only.
* Every role the table allows has a label.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

from basis.db.models import DEMO_REQUEST_ROLES, DemoRequest
from basis.email.demo_request import MAX_NOTE_CHARS, ROLE_LABELS, render_demo_request

TO = "founder@basis.example"


def request(**overrides: object) -> DemoRequest:
    values: dict[str, object] = {
        "id": uuid.UUID("11111111-2222-4333-8444-555555555555"),
        "seq": 1,
        "name": "Dana Whitfield",
        "email": "dana@acme-eng.example",
        "company": "Acme Engineering",
        "role": "analyst_staff",
        "note": "Forty studies a year.\nMostly multifamily.",
        "page": "https://basis.example/directions/demo.html",
        "created_at": datetime(2026, 11, 12, 13, 4, tzinfo=timezone(timedelta(hours=-5))),
        **overrides,
    }
    return DemoRequest(**values)


def test_the_email_has_everything_needed_to_reply() -> None:
    email = render_demo_request(request(), TO)
    assert email.to == TO
    assert email.subject == "Demo request: Dana Whitfield, Acme Engineering"
    assert email.idempotency_key == "demo-request-11111111-2222-4333-8444-555555555555"
    assert email.text == (
        "Someone asked for a demo on the Basis site.\n"
        "\n"
        "Name:     Dana Whitfield\n"
        "Email:    dana@acme-eng.example\n"
        "Company:  Acme Engineering\n"
        "Role:     Analyst or staff\n"
        "Received: 2026-11-12 18:04 UTC\n"
        "Page:     https://basis.example/directions/demo.html\n"
        "\n"
        "Note:\n"
        "Forty studies a year.\n"
        "Mostly multifamily.\n"
        "\n"
        "Reply to dana@acme-eng.example to set up the meeting.\n"
        "Request 11111111-2222-4333-8444-555555555555; every request: GET /v1/ops/demo-requests.\n"
    )


def test_absent_note_and_page_are_said_and_long_parts_are_cut() -> None:
    email = render_demo_request(request(note=None, page=None, name="N" * 200), TO)
    assert "Page:     (not sent)" in email.text and "Note:\n(none)\n" in email.text
    assert email.subject == f"Demo request: {'N' * 77}..., Acme Engineering"
    long_note = render_demo_request(request(note="x" * (MAX_NOTE_CHARS + 50)), TO)
    assert "x" * (MAX_NOTE_CHARS - 3) + "...\n" in long_note.text


def test_every_role_has_its_form_label() -> None:
    assert set(ROLE_LABELS) == set(DEMO_REQUEST_ROLES)
    assert ROLE_LABELS["owner_partner"] == "Owner or partner"
    assert render_demo_request(request(created_at=datetime(2026, 1, 1, tzinfo=UTC)), TO)
