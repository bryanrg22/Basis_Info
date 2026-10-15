"""Users and org membership."""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from basis.db.enums import OrgRole
from basis.db.models import Org, OrgMember, User

PERSONAL_ORG_SUFFIX: Final = "(personal)"


def personal_org_name(email: str | None, display_name: str | None) -> str:
    owner = (display_name or "").strip() or (email or "").strip() or "Personal workspace"
    return f"{owner} {PERSONAL_ORG_SUFFIX}"


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_by_firebase_uid(
        self, uid: str, email: str | None, display_name: str | None
    ) -> User:
        """Create or refresh the user for a verified Firebase uid.

        * First sight: insert the user, a personal org, and an ``owner`` membership.
        * Later: update ``last_seen_at``, and ``email``/``display_name`` when the token
          carries them (a missing claim never erases a stored value).

        Race-safe without relying on undocumented system columns: ``INSERT ... ON
        CONFLICT DO NOTHING RETURNING`` returns a row only for the transaction that
        actually inserted it. A concurrent first login waits on the unique index, inserts
        nothing, and takes the update path, so exactly one personal org is created.
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
            org = Org(name=personal_org_name(email, display_name))
            self._session.add(org)
            await self._session.flush()
            self._session.add(
                OrgMember(org_id=org.id, user_id=inserted.id, role=OrgRole.OWNER.value)
            )
            await self._session.flush()
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
