"""Invitations to join a firm: create, list, revoke, accept, decline.

The GitHub organization-invitation pattern: an invitation is bound to an email, not to a
secret link. The invitee is emailed (below) and sees the invitation after signing in with
that email (``GET /v1/me``), then answers it in the app. Answering needs the token's
``email_verified`` claim and the same normalized email.

* The email goes through the transactional outbox: :meth:`InvitationRepository.create`
  enqueues a ``notification`` message of type ``invitation`` in the transaction that
  inserts the row (so an invitation that commits is emailed, and one that rolls back is
  not) and sets ``email_queued_at``. The relay (``basis.relay.delivery``) renders it,
  hands it to the configured sender and records the outcome here: :meth:`record_emailed`
  (``emailed_at``, once) or :meth:`record_email_error` (the last failure). The delivery
  status the API shows is derived (:func:`email_delivery`): ``not_sent`` (created before
  emails existed), ``queued``, ``sent``, ``failed``.

* Emails are normalized with :func:`normalize_email` (trimmed, lower case) and stored that
  way. :data:`EMAIL_PATTERN` is what the API and the CLI accept.
* Status is derived, never stored (:func:`invitation_status`): ``accepted``,
  ``declined``, ``expired`` (``expires_at`` passed before it was answered, including one
  revoked after it had expired), ``revoked``, else ``pending``.
* One open invitation per firm and email (``uq_invitations_org_id_email_open``).
  :meth:`InvitationRepository.create` raises ``InvitationPendingError`` while a pending
  one exists and closes an expired one before inviting again.
* Lock order, for every writer: the firm's ``orgs`` row (``OrgRepository.lock``) first,
  then invitation rows. :meth:`InvitationRepository.answer` takes the firm lock, then the
  invitation row (``SELECT ... FOR UPDATE``), so concurrent accepts, declines, revokes and
  member removals of one firm apply one at a time and cannot deadlock.
  Accepting grants the role without lowering a higher one the user already has
  (``UserRepository.grant_membership``). Answering again the same way is idempotent; a
  removed member who replays an accepted invitation does not get the membership back.
* Every outcome is recorded on the firm's history (``org_events``,
  :mod:`basis.db.repositories.org_event`) in the same transaction: ``invitation.created``,
  ``invitation.revoked`` (with why), ``invitation.accepted``, ``invitation.declined``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from basis.db.enums import OrgRole
from basis.db.errors import (
    AlreadyMemberError,
    EmailUnverifiedError,
    InvitationClosedError,
    InvitationPendingError,
    NotVisibleError,
)
from basis.db.models import Invitation, Org, User
from basis.db.repositories.org import OrgRepository
from basis.db.repositories.org_event import OrgEventRepository
from basis.db.repositories.outbox import INVITATION_NOTIFICATION, NOTIFICATION, OutboxRepository
from basis.db.repositories.user import UserRepository, normalize_email

MAX_EMAIL_LENGTH: Final = 254  # RFC 5321 section 4.5.3.1.3 (a path is at most 256 octets)
# Visible ASCII except "@" on both sides of exactly one "@", and a dot in the domain.
# Deliberately simple: it rejects what cannot be an address, not every invalid one.
_PART: Final = r"[!-?A-~]+"
EMAIL_CORE: Final = rf"{_PART}@{_PART}\.{_PART}"
# The request form: surrounding spaces and tabs are allowed and trimmed.
EMAIL_PATTERN: Final = rf"^[ \t]*{EMAIL_CORE}[ \t]*$"
_EMAIL: Final = re.compile(EMAIL_CORE)

DETAIL_NOT_FOUND: Final = "Invitation not found."
DETAIL_UNVERIFIED: Final = (
    "Your sign-in does not confirm that you own this email address. Sign in with a "
    "verified account (Google sign-in confirms it) to answer the invitation."
)


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REVOKED = "revoked"
    EXPIRED = "expired"


class EmailDelivery(StrEnum):
    """Where the invitation email stands (derived, never stored)."""

    NOT_SENT = "not_sent"  # created before invitation emails existed: nothing was queued
    QUEUED = "queued"  # enqueued for the relay; not handed to the sender yet
    SENT = "sent"  # the sender accepted it (the log sender only logged it)
    FAILED = "failed"  # the last attempt failed (``email_error``); the relay may still retry


MAX_EMAIL_ERROR_LENGTH: Final = 2000


_CLOSED_DETAIL: Final = {
    InvitationStatus.ACCEPTED: "This invitation was already accepted.",
    InvitationStatus.DECLINED: "This invitation was declined.",
    InvitationStatus.REVOKED: "This invitation was revoked by the firm.",
    InvitationStatus.EXPIRED: "This invitation has expired. Ask the firm's owner for a new one.",
}


def valid_email(raw: str) -> str:
    """The normalized email, or ``ValueError`` when it is not an address we accept."""
    email = normalize_email(raw)
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL.fullmatch(email):
        raise ValueError(f"not an email address: {raw!r}")
    return email


def email_delivery(invitation: Invitation) -> EmailDelivery:
    if invitation.emailed_at is not None:
        return EmailDelivery.SENT
    if invitation.email_error is not None:
        return EmailDelivery.FAILED
    if invitation.email_queued_at is not None:
        return EmailDelivery.QUEUED
    return EmailDelivery.NOT_SENT


def invitation_status(invitation: Invitation, now: datetime) -> InvitationStatus:
    if invitation.accepted_at is not None:
        return InvitationStatus.ACCEPTED
    if invitation.declined_at is not None:
        return InvitationStatus.DECLINED
    # Revoking an invitation that had already expired changed nothing for the invitee.
    if invitation.expires_at <= (invitation.revoked_at or now):
        return InvitationStatus.EXPIRED
    if invitation.revoked_at is not None:
        return InvitationStatus.REVOKED
    return InvitationStatus.PENDING


@dataclass(frozen=True, slots=True)
class InvitationView:
    """An invitation with its firm's name, who sent it, and its status."""

    invitation: Invitation
    org_name: str
    invited_by_name: str | None
    invited_by_email: str | None
    status: InvitationStatus

    @property
    def delivery(self) -> EmailDelivery:
        return email_delivery(self.invitation)


@dataclass(frozen=True, slots=True)
class Answer:
    """The outcome of accepting or declining: the invitation, and for an acceptance the
    caller's role in the firm now (``None`` if they were removed after accepting)."""

    view: InvitationView
    role: OrgRole | None


_Inviter = aliased(User)
_ViewsSelect = Select[Invitation, str, str | None, str | None]
# models.INVITATION_OPEN as an expression (the partial unique index's predicate).
_OPEN_PREDICATE: Final = and_(
    Invitation.accepted_at.is_(None),
    Invitation.declined_at.is_(None),
    Invitation.revoked_at.is_(None),
)


def _views_query() -> _ViewsSelect:
    return (
        select(Invitation, Org.name, _Inviter.display_name, _Inviter.email)
        .join(Org, Org.id == Invitation.org_id)
        .outerjoin(_Inviter, _Inviter.id == Invitation.created_by)
    )


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def now(self) -> datetime:
        """The transaction's timestamp: one clock for every check in the request."""
        return (await self._session.execute(select(func.now()))).scalar_one()

    async def _views(self, stmt: _ViewsSelect) -> list[InvitationView]:
        now = await self.now()
        rows = await self._session.execute(stmt)
        return [
            InvitationView(invitation, org_name, name, email, invitation_status(invitation, now))
            for invitation, org_name, name, email in rows
        ]

    async def view(self, invitation_id: uuid.UUID) -> InvitationView:
        views = await self._views(_views_query().where(Invitation.id == invitation_id))
        if not views:
            raise NotVisibleError(DETAIL_NOT_FOUND)
        return views[0]

    async def create(
        self,
        org_id: uuid.UUID,
        email: str,
        role: OrgRole,
        created_by: uuid.UUID | None,
        ttl: timedelta,
    ) -> InvitationView:
        """Invite ``email`` (normalized here) to the firm as ``role`` for ``ttl``. Call with
        the firm locked (``OrgRepository.lock``): it may lock an invitation row.

        Raises ``ValueError`` for an invalid email, ``AlreadyMemberError`` when a member
        of the firm has that email, ``InvitationPendingError`` while a pending invitation
        for it exists. An expired open invitation is closed (revoked by ``created_by``;
        its status stays ``expired``) and replaced. Revoked, declined and accepted ones
        do not block a new invitation.

        The invitation email is enqueued in this transaction (one outbox message per
        invitation row, so re-inviting after expiry or revocation emails again, and a
        refused request enqueues nothing); ``email_queued_at`` says so.
        """
        email = valid_email(email)
        if await OrgRepository(self._session).member_with_email(org_id, email) is not None:
            raise AlreadyMemberError(
                "Someone with this email is already a member of the firm; change their "
                "role in the member list instead."
            )
        replaced: uuid.UUID | None = None
        for _ in range(2):
            inserted = await self._session.scalar(
                pg_insert(Invitation)
                .values(
                    org_id=org_id,
                    email=email,
                    role=role.value,
                    created_by=created_by,
                    expires_at=func.now() + ttl,
                    email_queued_at=func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[Invitation.org_id, Invitation.email],
                    index_where=_OPEN_PREDICATE,
                )
                .returning(Invitation.id)
            )
            if inserted is not None:
                view = await self.view(inserted)
                await OutboxRepository(self._session).enqueue(
                    NOTIFICATION,
                    {
                        "type": INVITATION_NOTIFICATION,
                        "invitation_id": str(inserted),
                        "org_id": str(org_id),
                        "email": email,
                    },
                )
                await OrgEventRepository(self._session).record(
                    org_id,
                    "invitation.created",
                    actor_id=created_by,
                    entity_type="invitation",
                    entity_id=inserted,
                    after=_invitation_json(view.invitation),
                    detail={"replaced": None if replaced is None else str(replaced)},
                )
                return view
            existing = await self._session.scalar(
                select(Invitation)
                .where(Invitation.org_id == org_id, Invitation.email == email, _OPEN_PREDICATE)
                .with_for_update()
            )
            if existing is None:  # answered or revoked meanwhile: try again
                continue
            now = await self.now()
            if invitation_status(existing, now) is not InvitationStatus.EXPIRED:
                raise InvitationPendingError(
                    "This email already has a pending invitation to the firm; revoke it to "
                    "invite again with another role."
                )
            existing.revoked_at = now
            existing.revoked_by = created_by
            await self._session.flush()
            await OrgEventRepository(self._session).record(
                org_id,
                "invitation.revoked",
                actor_id=created_by,
                entity_type="invitation",
                entity_id=existing.id,
                before=_invitation_json(existing),
                detail={"reason": "expired"},
            )
            replaced = existing.id
        raise InvitationPendingError(
            "This email already has a pending invitation to the firm."
        )  # pragma: no cover - only under repeated concurrent invitations

    async def record_emailed(self, invitation_id: uuid.UUID) -> bool:
        """The relay handed the email to the sender: ``emailed_at`` now, the error cleared.
        Once only: False when it was already recorded (a redelivered outbox row)."""
        result = await self._session.execute(
            update(Invitation)
            .where(Invitation.id == invitation_id, Invitation.emailed_at.is_(None))
            .values(emailed_at=func.now(), email_error=None)
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

    async def record_email_error(self, invitation_id: uuid.UUID, error: str) -> None:
        """The last send attempt failed (or the message was not sent); kept until a later
        attempt succeeds. Never overwrites a recorded success."""
        await self._session.execute(
            update(Invitation)
            .where(Invitation.id == invitation_id, Invitation.emailed_at.is_(None))
            .values(email_error=error[:MAX_EMAIL_ERROR_LENGTH])
            .execution_options(synchronize_session=False)
        )

    async def list_for_org(self, org_id: uuid.UUID) -> list[InvitationView]:
        """Every invitation of the firm, newest first."""
        return await self._views(
            _views_query()
            .where(Invitation.org_id == org_id)
            .order_by(Invitation.created_at.desc(), Invitation.id)
        )

    async def pending_for_email(self, email: str) -> list[InvitationView]:
        """Pending (open, unexpired) invitations to ``email`` (normalized here), newest first."""
        return await self._views(
            _views_query()
            .where(
                Invitation.email == normalize_email(email),
                _OPEN_PREDICATE,
                Invitation.expires_at > func.now(),
            )
            .order_by(Invitation.created_at.desc(), Invitation.id)
        )

    async def revoke(
        self, org_id: uuid.UUID, invitation_id: uuid.UUID, revoked_by: uuid.UUID
    ) -> InvitationView:
        """Revoke a pending invitation of the firm; revoking again is a no-op. Call with the
        firm locked (``OrgRepository.lock``).

        ``NotVisibleError`` if it is not this firm's; ``InvitationClosedError`` once
        accepted or declined (remove the member instead). An expired one is closed and
        stays ``expired``.
        """
        invitation = await self._session.scalar(
            select(Invitation)
            .where(Invitation.id == invitation_id, Invitation.org_id == org_id)
            .with_for_update()
        )
        if invitation is None:
            raise NotVisibleError(DETAIL_NOT_FOUND)
        status = invitation_status(invitation, await self.now())
        if status in (InvitationStatus.ACCEPTED, InvitationStatus.DECLINED):
            raise InvitationClosedError(_CLOSED_DETAIL[status], status=status.value)
        if invitation.revoked_at is None:
            invitation.revoked_at = await self.now()
            invitation.revoked_by = revoked_by
            await self._session.flush()
            await OrgEventRepository(self._session).record(
                org_id,
                "invitation.revoked",
                actor_id=revoked_by,
                entity_type="invitation",
                entity_id=invitation.id,
                before=_invitation_json(invitation),
                detail={"reason": "expired" if status is InvitationStatus.EXPIRED else "revoked"},
            )
        return await self.view(invitation_id)

    async def answer(
        self,
        invitation_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        email: str | None,
        email_verified: bool,
        accept: bool,
    ) -> Answer:
        """Accept or decline as the caller (their token's email and verification).

        * Not found, or addressed to another email: ``NotVisibleError`` (the two are
          indistinguishable, so invitations cannot be probed).
        * Addressed to the caller's email but the token does not verify it:
          ``EmailUnverifiedError``.
        * Already answered the same way by the caller: the same answer again.
        * Otherwise anything but pending: ``InvitationClosedError`` with the status.

        Locks, in the order every writer of a firm uses: the firm's row
        (``OrgRepository.lock``), then the invitation's. Removing a member takes the firm
        lock, deletes the membership and revokes their invitations; an accept that locked
        the invitation first and then needed the membership row deadlocked with it.
        """
        org_id = await self._session.scalar(
            select(Invitation.org_id).where(Invitation.id == invitation_id)
        )
        invitation = None
        if org_id is not None:
            await OrgRepository(self._session).lock(org_id)
            invitation = await self._session.scalar(
                select(Invitation).where(Invitation.id == invitation_id).with_for_update()
            )
        if invitation is None or email is None or invitation.email != normalize_email(email):
            raise NotVisibleError(DETAIL_NOT_FOUND)
        if not email_verified:
            raise EmailUnverifiedError(DETAIL_UNVERIFIED)
        now = await self.now()
        status = invitation_status(invitation, now)
        firms = OrgRepository(self._session)
        if accept and status is InvitationStatus.ACCEPTED and invitation.accepted_by == user_id:
            return Answer(
                await self.view(invitation_id), await firms.role_of(invitation.org_id, user_id)
            )
        if not accept and status is InvitationStatus.DECLINED and invitation.declined_by == user_id:
            return Answer(await self.view(invitation_id), None)
        if status is not InvitationStatus.PENDING:
            detail = _CLOSED_DETAIL[status]
            if status is InvitationStatus.ACCEPTED:
                detail = "This invitation was already accepted by another account."
            raise InvitationClosedError(detail, status=status.value)
        role: OrgRole | None = None
        events = OrgEventRepository(self._session)
        if accept:
            role_before = await firms.role_of(invitation.org_id, user_id)
            role = await UserRepository(self._session).grant_membership(
                invitation.org_id, user_id, OrgRole(invitation.role)
            )
            invitation.accepted_at = now
            invitation.accepted_by = user_id
            await self._session.flush()
            await events.record(
                invitation.org_id,
                "invitation.accepted",
                actor_id=user_id,
                subject_user_id=user_id,
                entity_type="invitation",
                entity_id=invitation.id,
                after={"email": invitation.email, "role": role.value},
                detail={"role_before": None if role_before is None else role_before.value},
            )
        else:
            invitation.declined_at = now
            invitation.declined_by = user_id
            await self._session.flush()
            await events.record(
                invitation.org_id,
                "invitation.declined",
                actor_id=user_id,
                subject_user_id=user_id,
                entity_type="invitation",
                entity_id=invitation.id,
                before=_invitation_json(invitation),
            )
        return Answer(await self.view(invitation_id), role)


def _invitation_json(invitation: Invitation) -> dict[str, Any]:
    return {
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at.isoformat(),
    }
