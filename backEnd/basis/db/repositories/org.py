"""Firms (``orgs``) and their members: listing, role changes, removal, last-owner rule.

A firm is an ``orgs`` row; ``owner`` is its admin role, ``engineer`` its employee role and
``viewer`` read-only. A firm has at least one owner at all times: demoting or removing
the last owner raises :class:`~basis.db.errors.LastOwnerError`.

Concurrency: every membership write takes the firm's row lock first (:meth:`lock`,
``SELECT ... FOR UPDATE`` on ``orgs``), so writes to one firm's memberships run one at a
time. The caller re-reads its own role after taking the lock (``basis.api.routes.orgs``):
two owners demoting each other at once cannot both succeed, because the second one runs
after the first has committed and is no longer an owner. Under READ COMMITTED each
statement after the lock wait sees what the first transaction committed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import OrgRole
from basis.db.errors import LastOwnerError, NotVisibleError
from basis.db.models import Invitation, Org, OrgMember, User

DETAIL_LAST_OWNER = "A firm must keep at least one owner; make another member an owner first."
DETAIL_MEMBER_NOT_FOUND = "Member not found."

# Owners first, then engineers, then viewers.
_ROLE_ORDER = case(
    {role.value: rank for rank, role in enumerate(reversed(list(OrgRole)))},
    value=OrgMember.role,
)


_MembersSelect = Select[uuid.UUID, str | None, str | None, str, datetime, datetime | None]


@dataclass(frozen=True, slots=True)
class Member:
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    role: OrgRole
    joined_at: datetime
    last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class Membership:
    org_id: uuid.UUID
    org_name: str
    role: OrgRole


@dataclass(frozen=True, slots=True)
class FirmSummary:
    """One firm for the operator CLI: owners' emails, member and open-invitation counts."""

    org: Org
    owners: list[str]
    members: int
    open_invitations: int


class OrgRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, name: str) -> Org:
        org = Org(name=name)
        self._session.add(org)
        await self._session.flush()
        await self._session.refresh(org)
        return org

    async def get(self, org_id: uuid.UUID) -> Org | None:
        return await self._session.get(Org, org_id)

    async def by_name(self, name: str) -> list[Org]:
        """Firms named exactly ``name`` (names are not unique), oldest first."""
        stmt = select(Org).where(Org.name == name).order_by(Org.created_at, Org.id)
        return list((await self._session.scalars(stmt)).all())

    async def lock(self, org_id: uuid.UUID) -> bool:
        """Take the firm's row lock until the transaction ends; False if it does not exist."""
        stmt = select(Org.id).where(Org.id == org_id).with_for_update()
        return (await self._session.scalar(stmt)) is not None

    async def role_of(self, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgRole | None:
        stmt = select(OrgMember.role).where(
            OrgMember.org_id == org_id, OrgMember.user_id == user_id
        )
        role = await self._session.scalar(stmt)
        return None if role is None else OrgRole(role)

    async def memberships(self, user_id: uuid.UUID) -> list[Membership]:
        """Every firm the user belongs to, with its name, by name."""
        rows = await self._session.execute(
            select(Org.id, Org.name, OrgMember.role)
            .join(OrgMember, OrgMember.org_id == Org.id)
            .where(OrgMember.user_id == user_id)
            .order_by(Org.name, Org.id)
        )
        return [Membership(org_id, name, OrgRole(role)) for org_id, name, role in rows]

    def _members_query(self, org_id: uuid.UUID) -> _MembersSelect:
        return (
            select(
                User.id,
                User.email,
                User.display_name,
                OrgMember.role,
                OrgMember.created_at,
                User.last_seen_at,
            )
            .join(OrgMember, OrgMember.user_id == User.id)
            .where(OrgMember.org_id == org_id)
        )

    async def members(self, org_id: uuid.UUID) -> list[Member]:
        """The firm's members: owners first, then by email."""
        stmt = self._members_query(org_id).order_by(_ROLE_ORDER, func.lower(User.email), User.id)
        return [_member(row) for row in await self._session.execute(stmt)]

    async def member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Member | None:
        stmt = self._members_query(org_id).where(OrgMember.user_id == user_id)
        row = (await self._session.execute(stmt)).one_or_none()
        return None if row is None else _member(row)

    async def owner_count(self, org_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            OrgMember.org_id == org_id, OrgMember.role == OrgRole.OWNER.value
        )
        return int(await self._session.scalar(stmt) or 0)

    async def member_with_email(self, org_id: uuid.UUID, email: str) -> uuid.UUID | None:
        """A member whose stored email normalizes to ``email`` (already normalized)."""
        stmt = (
            select(User.id)
            .join(OrgMember, OrgMember.user_id == User.id)
            .where(OrgMember.org_id == org_id, func.lower(func.btrim(User.email)) == email)
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def set_role(self, org_id: uuid.UUID, user_id: uuid.UUID, role: OrgRole) -> Member:
        """Change a member's role. Call with the firm locked (:meth:`lock`).

        Raises ``NotVisibleError`` for a non-member and ``LastOwnerError`` when the last
        owner would be demoted. Setting the current role is a no-op.
        """
        current = await self.member(org_id, user_id)
        if current is None:
            raise NotVisibleError(DETAIL_MEMBER_NOT_FOUND)
        if current.role is role:
            return current
        if current.role is OrgRole.OWNER and await self.owner_count(org_id) <= 1:
            raise LastOwnerError(DETAIL_LAST_OWNER)
        await self._session.execute(
            update(OrgMember)
            .where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
            .values(role=role.value)
        )
        updated = await self.member(org_id, user_id)
        assert updated is not None
        return updated

    async def remove(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Member:
        """Remove a member (access ends at once). Call with the firm locked (:meth:`lock`).

        Their studies stay in the firm with their ``created_by`` unchanged. Raises
        ``NotVisibleError`` for a non-member and ``LastOwnerError`` for the last owner
        (including an owner removing themselves).
        """
        current = await self.member(org_id, user_id)
        if current is None:
            raise NotVisibleError(DETAIL_MEMBER_NOT_FOUND)
        if current.role is OrgRole.OWNER and await self.owner_count(org_id) <= 1:
            raise LastOwnerError(DETAIL_LAST_OWNER)
        await self._session.execute(
            delete(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
        )
        return current

    async def summaries(self) -> list[FirmSummary]:
        """Every firm, oldest first, for ``python -m basis.admin list-firms``."""
        orgs = list((await self._session.scalars(select(Org).order_by(Org.created_at))).all())
        out: list[FirmSummary] = []
        for org in orgs:
            members = await self.members(org.id)
            open_invitations = await self._session.scalar(
                select(func.count()).where(
                    Invitation.org_id == org.id,
                    Invitation.accepted_at.is_(None),
                    Invitation.declined_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at > func.now(),
                )
            )
            out.append(
                FirmSummary(
                    org=org,
                    owners=[m.email or str(m.user_id) for m in members if m.role is OrgRole.OWNER],
                    members=len(members),
                    open_invitations=int(open_invitations or 0),
                )
            )
        return out


def _member(
    row: tuple[uuid.UUID, str | None, str | None, str, datetime, datetime | None],
) -> Member:
    user_id, email, display_name, role, joined_at, last_seen_at = row
    return Member(user_id, email, display_name, OrgRole(role), joined_at, last_seen_at)
