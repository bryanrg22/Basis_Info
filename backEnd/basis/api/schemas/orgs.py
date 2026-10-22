"""Firm (organization) membership and invitation bodies, and ``GET /v1/me``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import ConfigDict, Field

from basis.api.schemas.common import RequestModel, ResponseModel
from basis.db.enums import OrgRole
from basis.db.repositories import InvitationStatus, InvitationView, Member, Membership
from basis.db.repositories.invitation import EMAIL_PATTERN, MAX_EMAIL_LENGTH

# No mail service exists: an invitation is never emailed. The invitee sees it in the app
# after signing in with the invited email (GET /v1/me).
DELIVERY_NOT_SENT: Final = "not_sent"

ROLE_DESCRIPTION: Final = (
    "`owner` (the firm's admin: members, invitations, every study), `engineer` (creates "
    "and reviews studies) or `viewer` (reads studies)."
)


class MembershipOut(ResponseModel):
    org_id: uuid.UUID
    org_name: str
    role: OrgRole = Field(description=ROLE_DESCRIPTION)

    @classmethod
    def of(cls, membership: Membership) -> MembershipOut:
        return cls(org_id=membership.org_id, org_name=membership.org_name, role=membership.role)


class MeUser(ResponseModel):
    id: uuid.UUID
    email: str | None
    display_name: str | None


class InviteeInvitationOut(ResponseModel):
    """An invitation as its invitee sees it."""

    id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    role: OrgRole = Field(description=ROLE_DESCRIPTION)
    status: InvitationStatus
    invited_by_name: str | None = Field(
        description="Who sent it; null (with `invited_by_email`) when Basis set the firm up."
    )
    invited_by_email: str | None
    created_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, view: InvitationView) -> InviteeInvitationOut:
        invitation = view.invitation
        return cls(
            id=invitation.id,
            org_id=invitation.org_id,
            org_name=view.org_name,
            role=OrgRole(invitation.role),
            status=view.status,
            invited_by_name=view.invited_by_name,
            invited_by_email=view.invited_by_email,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
        )


class MeOut(ResponseModel):
    user: MeUser
    email_verified: bool = Field(
        description="Whether your sign-in confirms your email. Invitations are listed and "
        "can be answered only when it does (Google sign-in confirms it)."
    )
    memberships: list[MembershipOut] = Field(
        description="Your firms, by name. Empty: you are not a member of any firm yet."
    )
    pending_invitations: list[InviteeInvitationOut] = Field(
        description="Pending invitations to your verified email, newest first; empty while "
        "`email_verified` is false."
    )


class MemberOut(ResponseModel):
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    role: OrgRole = Field(description=ROLE_DESCRIPTION)
    joined_at: datetime
    last_seen_at: datetime | None

    @classmethod
    def of(cls, member: Member) -> MemberOut:
        return cls(
            user_id=member.user_id,
            email=member.email,
            display_name=member.display_name,
            role=member.role,
            joined_at=member.joined_at,
            last_seen_at=member.last_seen_at,
        )


class MemberList(ResponseModel):
    items: list[MemberOut] = Field(description="Owners first, then by email.")


class RoleChange(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "engineer"}]})

    role: OrgRole = Field(description=ROLE_DESCRIPTION)


class InvitationCreate(RequestModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"email": "drew@example.com", "role": "engineer"}]}
    )

    email: Annotated[
        str,
        Field(
            max_length=MAX_EMAIL_LENGTH,
            pattern=EMAIL_PATTERN,
            description="The invitee's email. Trimmed and lower-cased; the invitee must sign "
            "in with this address, verified, to accept.",
        ),
    ]
    role: OrgRole = Field(description=ROLE_DESCRIPTION)


class InvitationOut(InviteeInvitationOut):
    """An invitation as the firm's owners see it."""

    email: str = Field(description="Normalized: trimmed, lower case.")
    created_by: uuid.UUID | None
    accepted_at: datetime | None
    accepted_by: uuid.UUID | None
    declined_at: datetime | None
    revoked_at: datetime | None
    revoked_by: uuid.UUID | None
    delivery: Literal["not_sent"] = Field(
        default=DELIVERY_NOT_SENT,
        description="No email is sent. The invitee sees the invitation when they sign in "
        "with the invited email.",
    )

    @classmethod
    def of(cls, view: InvitationView) -> InvitationOut:
        invitation = view.invitation
        return cls(
            **InviteeInvitationOut.of(view).model_dump(),
            email=invitation.email,
            created_by=invitation.created_by,
            accepted_at=invitation.accepted_at,
            accepted_by=invitation.accepted_by,
            declined_at=invitation.declined_at,
            revoked_at=invitation.revoked_at,
            revoked_by=invitation.revoked_by,
        )


class InvitationList(ResponseModel):
    items: list[InvitationOut] = Field(description="Every status, newest first.")


class InvitationAnswerOut(ResponseModel):
    invitation: InviteeInvitationOut
    membership: MembershipOut | None = Field(
        description="After accepting: your membership in the firm (an existing higher role "
        "is kept). Null after declining, or if you were removed after accepting."
    )
