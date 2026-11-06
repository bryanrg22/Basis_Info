"""Each engineer's own signer details (EVAL_REPORT 91; migration ``0032``).

One row per user, written whole by that user with a version (its entity tag), as the firm
profile is. The user's row lock serializes the writes, including the first, which inserts:
two first writes at once give one insert and, with ``If-Match``, one 412.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.errors import NotVisibleError, PreconditionFailedError
from basis.db.models import SignerDetailsRow, User
from basis.db.repositories.study import IntakePrecondition
from basis.report.inputs import SIGNER_FIELDS, SignerDetails


@dataclass(frozen=True, slots=True)
class SignerWrite:
    row: SignerDetailsRow
    before: SignerDetails | None
    version_before: int
    changed: list[str]


class SignerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> SignerDetailsRow | None:
        return await self._session.get(SignerDetailsRow, user_id, populate_existing=True)

    async def for_users(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, SignerDetails]:
        """The saved details of each of ``user_ids`` that has some (one query)."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        rows = await self._session.scalars(
            select(SignerDetailsRow).where(SignerDetailsRow.user_id.in_(ids))
        )
        return {row.user_id: row.details() for row in rows}

    async def write(
        self,
        user_id: uuid.UUID,
        details: SignerDetails,
        *,
        precondition: IntakePrecondition | None = None,
    ) -> SignerWrite:
        """Set or replace the user's details and increment the version (0 while there are
        none, so the first write makes it 1). ``precondition`` is checked under the user's row
        lock against the version read there; false -> ``PreconditionFailedError``."""
        locked = await self._session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        if locked is None:
            raise NotVisibleError(f"user {user_id} not found")
        existing = await self.get(user_id)
        seen = existing.version if existing else 0
        before = existing.details() if existing else None
        if precondition is not None and not precondition(seen, existing is not None):
            raise PreconditionFailedError(
                f"the signer details of user {user_id} are at version {seen}", version=seen
            )
        changed = [
            key
            for key in SIGNER_FIELDS
            if (None if before is None else getattr(before, key)) != getattr(details, key)
        ]
        values = details.model_dump()
        if existing is None:
            row = SignerDetailsRow(user_id=user_id, version=1, **values)
            self._session.add(row)
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            existing.version = seen + 1
            row = existing
        await self._session.flush()
        await self._session.refresh(row)
        return SignerWrite(row=row, before=before, version_before=seen, changed=changed)


__all__ = ["SignerRepository", "SignerWrite"]
