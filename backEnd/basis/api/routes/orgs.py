"""Invite-only firms: ``GET /v1/me``, owner administration, answering invitations.

A firm is an organization (``orgs``). Its owners (the admin role) manage members and
invitations under ``/v1/orgs/{org_id}``; everyone else gets 404 (not a member: orgs are
not enumerable) or 403 (a member below owner), like studies.

* Membership writes lock the firm's row and re-check the caller's role under the lock
  (``OrgRepository``), so two owners demoting each other concurrently leave one owner,
  and the last owner cannot be demoted or removed (409 ``last-owner``).
* Invitations are bound to an email; no email is sent. The invitee answers in the app
  with ``POST /v1/invitations/{id}/accept`` or ``/decline``, signed in with that email,
  verified (``InvitationRepository.answer``).

Every route is on the standard read or write routers: rate limited per user, and writes
take an ``Idempotency-Key``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.orgs import (
    InvitationAnswerOut,
    InvitationCreate,
    InvitationList,
    InvitationOut,
    InviteeInvitationOut,
    MemberList,
    MemberOut,
    MembershipOut,
    MeOut,
    MeUser,
    RoleChange,
)
from basis.api.users import CurrentUser, CurrentUserDep, org_access, require_org_role
from basis.config import Settings
from basis.db.enums import OrgRole
from basis.db.repositories import InvitationRepository, Membership, OrgRepository
from basis.db.session import SessionDep

ADMIN_ROLE = OrgRole.OWNER

reads = read_router(tags=["firms"])
writes = write_router(tags=["firms"])

OwnedOrg = Annotated[uuid.UUID, Depends(org_access(ADMIN_ROLE))]
UserIdPath = Annotated[uuid.UUID, Path(description="The member's user id.")]
InvitationIdPath = Annotated[uuid.UUID, Path(description="Invitation id.")]


async def lock_as_owner(session: AsyncSession, user: CurrentUser, org_id: uuid.UUID) -> None:
    """Take the firm's lock, then check again that the caller is still an owner."""
    await OrgRepository(session).lock(org_id)
    await require_org_role(session, user, org_id, ADMIN_ROLE)


# --------------------------------------------------------------------------- me


@reads.get(
    "/me",
    tags=["me"],
    summary="You: your firms and the invitations waiting for you",
    description="The client's source of truth after sign-in. No memberships and no "
    "pending invitations: you are not a member of a firm yet (the study list answers 403 "
    "`no-firm`).",
)
async def get_me(user: CurrentUserDep, session: SessionDep) -> MeOut:
    principal = user.principal
    memberships = await OrgRepository(session).memberships(user.id)
    pending = []
    if principal.email_verified and principal.email:
        pending = await InvitationRepository(session).pending_for_email(principal.email)
    return MeOut(
        user=MeUser(id=user.id, email=user.user.email, display_name=user.user.display_name),
        email_verified=principal.email_verified,
        memberships=[MembershipOut.of(m) for m in memberships],
        pending_invitations=[InviteeInvitationOut.of(view) for view in pending],
    )


# --------------------------------------------------------------------------- members


@reads.get(
    "/orgs/{org_id}/members",
    summary="List the firm's members (owner only)",
    responses=problems(403, 404),
)
async def list_members(org_id: OwnedOrg, session: SessionDep) -> MemberList:
    members = await OrgRepository(session).members(org_id)
    return MemberList(items=[MemberOut.of(m) for m in members])


@writes.post(
    "/orgs/{org_id}/members/{user_id}/role",
    summary="Change a member's role (owner only)",
    description="409 `last-owner` when it would demote the firm's last owner.",
    responses=problems(403, 404),
)
async def change_member_role(
    org_id: OwnedOrg,
    user_id: UserIdPath,
    body: RoleChange,
    user: CurrentUserDep,
    session: SessionDep,
) -> MemberOut:
    await lock_as_owner(session, user, org_id)
    member = await OrgRepository(session).set_role(org_id, user_id, body.role)
    return MemberOut.of(member)


@writes.delete(
    "/orgs/{org_id}/members/{user_id}",
    summary="Remove a member (owner only)",
    description="Access ends at once; the member's studies stay in the firm and keep their "
    "creator. 409 `last-owner` for the last owner (an owner may remove themselves while "
    "another owner remains).",
    responses=problems(403, 404),
)
async def remove_member(
    org_id: OwnedOrg, user_id: UserIdPath, user: CurrentUserDep, session: SessionDep
) -> MemberOut:
    await lock_as_owner(session, user, org_id)
    member = await OrgRepository(session).remove(org_id, user_id)
    return MemberOut.of(member)


# --------------------------------------------------------------------------- invitations


def invitation_ttl(request: Request) -> timedelta:
    settings: Settings = request.app.state.settings
    return timedelta(days=settings.invitation_ttl_days)


@reads.get(
    "/orgs/{org_id}/invitations",
    summary="List the firm's invitations, every status (owner only)",
    responses=problems(403, 404),
)
async def list_invitations(org_id: OwnedOrg, session: SessionDep) -> InvitationList:
    views = await InvitationRepository(session).list_for_org(org_id)
    return InvitationList(items=[InvitationOut.of(view) for view in views])


@writes.post(
    "/orgs/{org_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    summary="Invite an email to the firm (owner only)",
    description="No email is sent (`delivery` is `not_sent`): the invitee sees the "
    "invitation in `GET /v1/me` once signed in with that email, verified. 409 "
    "`already-member` when a member has the email; 409 `invitation-pending` while a pending "
    "invitation for it exists (revoke it to change the role). An expired, revoked or "
    "declined invitation does not block a new one.",
    responses=problems(403, 404),
)
async def create_invitation(
    org_id: OwnedOrg,
    body: InvitationCreate,
    user: CurrentUserDep,
    session: SessionDep,
    request: Request,
) -> InvitationOut:
    await lock_as_owner(session, user, org_id)
    view = await InvitationRepository(session).create(
        org_id, body.email, body.role, user.id, invitation_ttl(request)
    )
    return InvitationOut.of(view)


@writes.delete(
    "/orgs/{org_id}/invitations/{invitation_id}",
    summary="Revoke an invitation (owner only)",
    description="Revoking again is a no-op. 409 `invitation-accepted` or "
    "`invitation-declined` once answered (remove the member instead).",
    responses=problems(403, 404),
)
async def revoke_invitation(
    org_id: OwnedOrg,
    invitation_id: InvitationIdPath,
    user: CurrentUserDep,
    session: SessionDep,
) -> InvitationOut:
    await lock_as_owner(session, user, org_id)
    view = await InvitationRepository(session).revoke(org_id, invitation_id, user.id)
    return InvitationOut.of(view)


# --------------------------------------------------------------------------- answering

_ANSWER_DESCRIPTION = (
    "Signed in with the invited email, verified. 404 when the invitation does not exist or "
    "is addressed to another email (the two are indistinguishable); 403 `email-unverified` "
    "when your sign-in does not verify the invited email; 409 `invitation-expired`, "
    "`invitation-revoked`, `invitation-declined` or `invitation-accepted` once it cannot be "
    "answered. Answering again the same way returns the same result."
)


async def _answer(
    invitation_id: uuid.UUID, user: CurrentUser, session: AsyncSession, *, accept: bool
) -> InvitationAnswerOut:
    principal = user.principal
    answer = await InvitationRepository(session).answer(
        invitation_id,
        user_id=user.id,
        email=principal.email,
        email_verified=principal.email_verified,
        accept=accept,
    )
    membership = None
    if answer.role is not None:
        membership = MembershipOut.of(
            Membership(answer.view.invitation.org_id, answer.view.org_name, answer.role)
        )
    return InvitationAnswerOut(
        invitation=InviteeInvitationOut.of(answer.view), membership=membership
    )


@writes.post(
    "/invitations/{invitation_id}/accept",
    summary="Accept an invitation and join the firm",
    description=_ANSWER_DESCRIPTION + " Accepting never lowers a role you already have.",
    responses=problems(403, 404),
)
async def accept_invitation(
    invitation_id: InvitationIdPath, user: CurrentUserDep, session: SessionDep
) -> InvitationAnswerOut:
    return await _answer(invitation_id, user, session, accept=True)


@writes.post(
    "/invitations/{invitation_id}/decline",
    summary="Decline an invitation",
    description=_ANSWER_DESCRIPTION,
    responses=problems(403, 404),
)
async def decline_invitation(
    invitation_id: InvitationIdPath, user: CurrentUserDep, session: SessionDep
) -> InvitationAnswerOut:
    return await _answer(invitation_id, user, session, accept=False)
