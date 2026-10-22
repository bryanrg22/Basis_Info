"""Users and org membership.

Firms are invite-only: a user is created on first sight (the ``users`` row is needed for
audit and to accept invitations) but joins no org until an invitation is accepted
(``basis.db.repositories.invitation``). Orgs and memberships created before that rule
(the former personal workspaces) are kept as they are.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ColumnClause

from basis.db.enums import OrgRole
from basis.db.models import OrgMember, User

# ``org_members.role`` in rank order, as a SQL array: array_position() is the rank.
_RANKED_ROLES: Final = "ARRAY[" + ", ".join(f"'{role.value}'" for role in OrgRole) + "]"
# On conflict, keep whichever of the stored and the offered role ranks higher.
_HIGHER_ROLE: Final[ColumnClause[str]] = literal_column(
    f"CASE WHEN array_position({_RANKED_ROLES}, excluded.role) > "
    f"array_position({_RANKED_ROLES}, org_members.role) "
    "THEN excluded.role ELSE org_members.role END"
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_by_firebase_uid(
        self, uid: str, email: str | None, display_name: str | None
    ) -> User:
        """Create or refresh the user for a verified Firebase uid.

        * First sight: insert the user, and nothing else (no org, no membership).
        * Later: update ``last_seen_at``, and ``email``/``display_name`` when the token
          carries them (a missing claim never erases a stored value).

        Race-safe without relying on undocumented system columns: ``INSERT ... ON
        CONFLICT DO NOTHING RETURNING`` returns a row only for the transaction that
        actually inserted it. A concurrent first login waits on the unique index, inserts
        nothing, and takes the update path, so exactly one user is created.
        """
        if not uid:
            raise ValueError("firebase uid is required")
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

        changes: dict[str, object] = {"last_seen_at": func.now()}
        if email is not None:
            changes["email"] = email
        if display_name is not None:
            changes["display_name"] = display_name
        return (
            await self._session.scalars(
                update(User)
                .where(User.firebase_uid == uid)
                .values(changes)
                .returning(User)
                .execution_options(synchronize_session=False, populate_existing=True)
            )
        ).one()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_firebase_uid(self, uid: str) -> User | None:
        stmt = select(User).where(User.firebase_uid == uid)
        return (await self._session.scalars(stmt)).one_or_none()

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
