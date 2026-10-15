"""Idempotency keys for mutating requests (S1_SPEC §5).

Keys are scoped per user: the stored primary key is ``"<user_id>:<client key>"`` (or
``"anonymous:<client key>"``), so two users choosing the same client key never collide
even though ``idempotency_keys.key`` is a single-column primary key.

Lifecycle used by the middleware:

1. :meth:`IdempotencyRepository.claim` before running the handler. ``created=True``
   means this request owns the key and must run; ``created=False`` returns the stored
   record: compare ``request_hash`` (mismatch -> 422); a stored ``status_code`` means
   replay the stored response; ``status_code IS NULL`` means the first request is still
   in flight (-> 409).
2. :meth:`complete` with the response to store, or :meth:`release` when the handler
   failed so the client can retry with the same key.

Expired keys (``expires_at <= now()``) are treated as absent and are overwritten by the
next claim; :meth:`purge_expired` removes them in bulk.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from basis.db.models import IdempotencyKey

DEFAULT_TTL: Final = timedelta(hours=24)
MAX_CLIENT_KEY_LENGTH: Final = 128
_CLAIM_ATTEMPTS: Final = 3


def scoped_key(user_id: uuid.UUID | None, key: str) -> str:
    """The stored form of a client idempotency key (also used for ``reviews``)."""
    if not key or len(key) > MAX_CLIENT_KEY_LENGTH:
        raise ValueError(f"idempotency key must be 1..{MAX_CLIENT_KEY_LENGTH} characters")
    owner = "anonymous" if user_id is None else str(user_id)
    return f"{owner}:{key}"


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    record: IdempotencyKey
    created: bool


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        user_id: uuid.UUID | None,
        key: str,
        route: str,
        request_hash: str,
        ttl: timedelta = DEFAULT_TTL,
    ) -> IdempotencyClaim:
        """Insert the key, or return the live record that already holds it.

        One statement: ``INSERT ... ON CONFLICT (key) DO UPDATE ... WHERE expires_at <=
        now()``, so an expired record is replaced atomically and a live one is left
        alone (no row returned, then it is read).
        """
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        stored = scoped_key(user_id, key)
        values = {
            "key": stored,
            "user_id": user_id,
            "route": route,
            "request_hash": request_hash,
            "expires_at": func.now() + ttl,
        }
        insert = pg_insert(IdempotencyKey).values(values)
        upsert = (
            insert.on_conflict_do_update(
                index_elements=[IdempotencyKey.key],
                set_={
                    "user_id": insert.excluded.user_id,
                    "route": insert.excluded.route,
                    "request_hash": insert.excluded.request_hash,
                    "status_code": None,
                    "response": None,
                    "created_at": func.now(),
                    "expires_at": insert.excluded.expires_at,
                },
                where=IdempotencyKey.expires_at <= func.now(),
            )
            .returning(IdempotencyKey)
            .execution_options(populate_existing=True)
        )
        for _ in range(_CLAIM_ATTEMPTS):
            created = (await self._session.scalars(upsert)).one_or_none()
            if created is not None:
                return IdempotencyClaim(record=created, created=True)
            existing = await self._get_live(stored)
            if existing is not None:
                return IdempotencyClaim(record=existing, created=False)
            # The conflicting row expired or was purged between the two statements.
        raise RuntimeError("could not claim idempotency key after retries")

    async def get(self, user_id: uuid.UUID | None, key: str) -> IdempotencyKey | None:
        return await self._get_live(scoped_key(user_id, key))

    async def complete(
        self, user_id: uuid.UUID | None, key: str, status_code: int, response: Any
    ) -> bool:
        """Store the response of the request that owns the key. False if not in flight."""
        stmt = (
            update(IdempotencyKey)
            .where(
                IdempotencyKey.key == scoped_key(user_id, key),
                IdempotencyKey.status_code.is_(None),
            )
            .values(status_code=status_code, response=response)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return bool(getattr(result, "rowcount", 0))

    async def release(self, user_id: uuid.UUID | None, key: str) -> bool:
        """Drop an in-flight key (the handler failed) so the client may retry."""
        stmt = (
            delete(IdempotencyKey)
            .where(
                IdempotencyKey.key == scoped_key(user_id, key),
                IdempotencyKey.status_code.is_(None),
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return bool(getattr(result, "rowcount", 0))

    async def purge_expired(self) -> int:
        stmt = (
            delete(IdempotencyKey)
            .where(IdempotencyKey.expires_at <= func.now())
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return int(getattr(result, "rowcount", 0))

    async def _get_live(self, stored: str) -> IdempotencyKey | None:
        stmt = (
            select(IdempotencyKey)
            .where(IdempotencyKey.key == stored, IdempotencyKey.expires_at > func.now())
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).one_or_none()
