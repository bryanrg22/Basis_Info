"""Users and org membership.

Firms are invite-only: a user is created on first sight (the ``users`` row is needed for
audit and to accept invitations) but joins no org until an invitation is accepted
(``basis.db.repositories.invitation``). Orgs and memberships created before that rule
(the former personal workspaces) are kept as they are.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import ColumnElement, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ColumnClause

from basis.db.enums import OrgRole
from basis.db.models import OrgMember, User, UserLinkedAccount

logger = logging.getLogger(__name__)


def normalize_email(raw: str) -> str:
    """Trimmed and lower case: the stored and compared form (invitations and sign-in
    linking alike)."""
    return raw.strip().lower()


# Seed for the uid's advisory lock (hashtextextended), so it never shares a key with
# another feature's lock on the same text.
_LINK_LOCK_SEED: Final = 7_734_001

# ``org_members.role`` in rank order, as a SQL array: array_position() is the rank.
_RANKED_ROLES: Final = "ARRAY[" + ", ".join(f"'{role.value}'" for role in OrgRole) + "]"
# On conflict, keep whichever of the stored and the offered role ranks higher.
_HIGHER_ROLE: Final[ColumnClause[str]] = literal_column(
    f"CASE WHEN array_position({_RANKED_ROLES}, excluded.role) > "
    f"array_position({_RANKED_ROLES}, org_members.role) "
    "THEN excluded.role ELSE org_members.role END"
)


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """A user as shown next to their id: the display name and email the token carried."""

    display_name: str | None
    email: str | None


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_by_firebase_uid(
        self,
        uid: str,
        email: str | None,
        display_name: str | None,
        *,
        email_verified: bool = False,
        link_verified_email: bool = False,
    ) -> User:
        """Create or refresh the user for a verified Firebase uid.

        * A uid linked to an existing user (``user_linked_accounts``): refresh that user.
        * First sight, with ``link_verified_email`` and a verified email that a firm member
          already has: link the uid to that member (:meth:`_link_on_sign_in`) instead of
          creating a second user for the same person.
        * Otherwise first sight: insert the user, and nothing else (no org, no membership).
        * Later: update ``last_seen_at``, and ``email``/``display_name`` when the token
          carries them (a missing claim never erases a stored value).

        Race-safe without relying on undocumented system columns: ``INSERT ... ON
        CONFLICT DO NOTHING RETURNING`` returns a row only for the transaction that
        actually inserted it. A concurrent first login waits on the unique index, inserts
        nothing, and takes the update path, so exactly one user is created. The linking
        path takes a transaction-scoped advisory lock on the uid first, so concurrent first
        logins either all link or all insert, never one of each.
        """
        if not uid:
            raise ValueError("firebase uid is required")
        linked = await self._linked_user_id(uid)
        if linked is None and link_verified_email and email_verified and email:
            await self._session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(uid, _LINK_LOCK_SEED)))
            )
            linked = await self._linked_user_id(uid)
            if linked is None and await self.get_by_firebase_uid(uid) is None:
                linked = await self._link_on_sign_in(uid, email)
        if linked is not None:
            return await self._touch(User.id == linked, email, display_name)
        inserted = (
            await self._session.scalars(
                pg_insert(User)
                .values(
                    firebase_uid=uid,
                    email=email,
                    display_name=display_name,
                    last_seen_at=func.now(),
                )
                .on_conflict_do_nothing(index_elements=[User.firebase_uid])
                .returning(User)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if inserted is not None:
            return inserted
        return await self._touch(User.firebase_uid == uid, email, display_name)

    async def _touch(
        self, which: ColumnElement[bool], email: str | None, display_name: str | None
    ) -> User:
        """Update ``last_seen_at``, and ``email``/``display_name`` when given."""
        changes: dict[str, object] = {"last_seen_at": func.now()}
        if email is not None:
            changes["email"] = email
        if display_name is not None:
            changes["display_name"] = display_name
        return (
            await self._session.scalars(
                update(User)
                .where(which)
                .values(changes)
                .returning(User)
                .execution_options(synchronize_session=False, populate_existing=True)
            )
        ).one()

    async def _linked_user_id(self, uid: str) -> uuid.UUID | None:
        return await self._session.scalar(
            select(UserLinkedAccount.user_id).where(UserLinkedAccount.firebase_uid == uid)
        )

    async def _link_on_sign_in(self, uid: str, email: str) -> uuid.UUID | None:
        """Link ``uid`` to the firm member whose stored email is ``email`` (normalized), or
        return None. Only a user who belongs to a firm qualifies: membership is reached only
        through a verified email (an accepted invitation, ``POST /v1/orgs``), so the stored
        address was proven by that person. Several such users (an earlier duplicate): the
        one seen most recently. Called with the uid's advisory lock held."""
        address = normalize_email(email)
        target = await self._session.scalar(
            select(User.id)
            .where(func.lower(func.btrim(User.email)) == address)
            .where(select(OrgMember.user_id).where(OrgMember.user_id == User.id).exists())
            .order_by(User.last_seen_at.desc().nulls_last(), User.created_at.desc())
            .limit(1)
        )
        if target is None:
            return None
        self._session.add(
            UserLinkedAccount(firebase_uid=uid, user_id=target, email=address, linked_by="sign_in")
        )
        await self._session.flush()
        logger.info(
            "sign-in account linked to an existing user",
            extra={"user_id": str(target), "linked_by": "sign_in"},
        )
        return target

    async def link_account(self, uid: str, user_id: uuid.UUID, email: str | None) -> bool:
        """Operator link (``basis.admin link-account``): map ``uid`` to ``user_id``. False
        when the uid is already a user of its own or already linked (nothing changes)."""
        if not uid:
            raise ValueError("firebase uid is required")
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(uid, _LINK_LOCK_SEED)))
        )
        if await self._linked_user_id(uid) is not None or await self._session.scalar(
            select(User.id).where(User.firebase_uid == uid)
        ):
            return False
        self._session.add(
            UserLinkedAccount(
                firebase_uid=uid,
                user_id=user_id,
                email=normalize_email(email) if email else None,
                linked_by="operator",
            )
        )
        await self._session.flush()
        return True

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def identities(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, UserIdentity]:
        """``{user id: (display name, email)}`` for the ids that exist: how the API names a
        study's creator or a report's signer next to the id."""
        wanted = {user_id for user_id in user_ids}
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(list(wanted)))
        )
        return {user_id: UserIdentity(name, email) for user_id, name, email in rows}

    async def get_by_firebase_uid(self, uid: str) -> User | None:
        """The user for ``uid``: the one created with it, or the one it is linked to."""
        stmt = select(User).where(User.firebase_uid == uid)
        user = (await self._session.scalars(stmt)).one_or_none()
        if user is not None:
            return user
        linked = await self._linked_user_id(uid)
        return None if linked is None else await self._session.get(User, linked)

    async def memberships(self, user_id: uuid.UUID) -> dict[uuid.UUID, OrgRole]:
        """``{org_id: role}`` for every org the user belongs to."""
        rows = await self._session.execute(
            select(OrgMember.org_id, OrgMember.role).where(OrgMember.user_id == user_id)
        )
        return {org_id: OrgRole(role) for org_id, role in rows}

    async def add_membership(self, org_id: uuid.UUID, user_id: uuid.UUID, role: OrgRole) -> None:
        """Add the user to the org, or change their role if already a member."""
        stmt = pg_insert(OrgMember).values(org_id=org_id, user_id=user_id, role=role.value)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[OrgMember.org_id, OrgMember.user_id],
                set_={"role": stmt.excluded.role},
            )
        )

    async def grant_membership(
        self, org_id: uuid.UUID, user_id: uuid.UUID, role: OrgRole
    ) -> OrgRole:
        """Add the user to the org with ``role``, never lowering a role they already have.

        ``add_membership`` semantics (one row per org and user, race-safe upsert), except
        that an existing higher role is kept: accepting an engineer invitation does not
        demote an owner. Returns the role the user has afterwards.
        """
        stmt = pg_insert(OrgMember).values(org_id=org_id, user_id=user_id, role=role.value)
        result = await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[OrgMember.org_id, OrgMember.user_id],
                set_={"role": _HIGHER_ROLE},
            ).returning(OrgMember.role)
        )
        return OrgRole(result.scalar_one())
