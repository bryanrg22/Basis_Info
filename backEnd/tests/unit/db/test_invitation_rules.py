"""Invitation rules without a database: email normalization and validation, the request
pattern (the OpenAPI contract) agreeing with the server's check, and derived status."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from basis.db.models import Invitation
from basis.db.repositories.invitation import (
    EMAIL_PATTERN,
    InvitationStatus,
    invitation_status,
    normalize_email,
    valid_email,
)

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
GOOD = ["a@b.co", "Drew.Lee+cs@Example.COM", "x_y-z@sub.example.test", "o'brien@firm.ie"]
BAD = ["", "a", "a@b", "@b.co", "a@", "a@@b.co", "a b@c.co", "a@b .co", "ä@b.co", "a@b.cö"]


@pytest.mark.parametrize("raw", GOOD)
def test_valid_emails_are_normalized(raw: str) -> None:
    assert valid_email(f"  {raw}\t") == raw.lower()
    assert normalize_email(f" {raw} ") == raw.lower()


@pytest.mark.parametrize("raw", BAD)
def test_invalid_emails_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        valid_email(raw)


def test_the_request_pattern_accepts_exactly_what_the_server_does() -> None:
    # fullmatch: JSON Schema (ECMA) and pydantic's regex end "$" at the end of the text;
    # Python's re.match would also let "$" match before a final newline.
    pattern = re.compile(EMAIL_PATTERN)
    for raw in GOOD:
        assert pattern.fullmatch(f" {raw}\t") and pattern.fullmatch(raw)
    for raw in BAD:
        assert not pattern.fullmatch(raw)
    assert not pattern.fullmatch("a@b.co\n")  # surrounding spaces and tabs only
    with pytest.raises(ValueError):
        valid_email("x" * 250 + "@b.co")  # longer than 254 (the schema's maxLength too)


def invitation(**outcome: object) -> Invitation:
    return Invitation(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        email="a@b.co",
        role="engineer",
        created_at=NOW - timedelta(days=1),
        expires_at=outcome.pop("expires_at", NOW + timedelta(days=13)),  # type: ignore[arg-type]
        **outcome,
    )


def test_status_is_derived() -> None:
    who = uuid.uuid4()
    before = NOW - timedelta(hours=1)
    assert invitation_status(invitation(), NOW) is InvitationStatus.PENDING
    accepted = invitation(accepted_at=before, accepted_by=who)
    assert invitation_status(accepted, NOW) is InvitationStatus.ACCEPTED
    declined = invitation(declined_at=before, declined_by=who)
    assert invitation_status(declined, NOW) is InvitationStatus.DECLINED
    assert invitation_status(invitation(revoked_at=before, revoked_by=who), NOW) is (
        InvitationStatus.REVOKED
    )
    assert invitation_status(invitation(expires_at=NOW), NOW) is InvitationStatus.EXPIRED
    # Answered before it expired: the answer stands after expiry.
    late = NOW + timedelta(days=30)
    assert invitation_status(accepted, late) is InvitationStatus.ACCEPTED
    assert invitation_status(declined, late) is InvitationStatus.DECLINED
    # Revoked before expiry stays revoked; revoked (closed) after expiry reads expired.
    revoked = invitation(revoked_at=before, revoked_by=who)
    assert invitation_status(revoked, late) is InvitationStatus.REVOKED
    closed = invitation(expires_at=before, revoked_at=NOW, revoked_by=None)
    assert invitation_status(closed, NOW) is InvitationStatus.EXPIRED
