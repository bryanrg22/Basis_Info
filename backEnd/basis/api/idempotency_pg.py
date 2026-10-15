"""PostgreSQL-backed :class:`~basis.api.idempotency.IdempotencyStore` (S1_SPEC section 5).

It adapts the store protocol used by the ``idempotency`` dependency to
:class:`~basis.db.repositories.IdempotencyRepository` (table ``idempotency_keys``):

=====================  ==========================================================
protocol               repository
=====================  ==========================================================
``reserve``            ``claim`` with the in-flight lease (10 minutes)
``put``                ``complete`` with the 24 h replay window and the fingerprint
``release``            ``release`` (only the reservation with this fingerprint)
``get``                ``get``
=====================  ==========================================================

Transactions: every call runs in its **own** short transaction, committed before the
call returns, never in the request's transaction. The reservation must be visible to a
concurrent retry while the first request is still running (otherwise the retry would
block on the uncommitted insert or run the handler a second time), and the completed
response is written after the request's transaction has committed.

Known window: the handler's transaction commits, then ``put`` runs. If ``put`` fails (the
database went away between the two), the key stays in flight until the 10-minute lease
ends; a retry before that gets 409, and a retry after it runs the handler again. The
review table's own unique ``idempotency_key`` (and the stage compare-and-set) make a
repeated decision fail with 409 instead of applying twice.

Scoping: the protocol keys records by the Firebase uid; the table keys them by
``users.id`` (``"<user_id>:<client key>"``). The uid is resolved to the user id here, so
the user must already exist: routes declare ``get_current_user`` before ``idempotency``,
and ``get_current_user`` commits the user in its own transaction.

Stored response: ``{"headers": [[name, value], ...], "body": <text>}`` when the body is
UTF-8 (all JSON bodies are), else ``"body_b64"``. Replays return the exact bytes.
"""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Iterable
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.idempotency import StoredResponse, UserKey
from basis.db.models import IdempotencyKey, User
from basis.db.repositories import IdempotencyRepository
from basis.db.session import session_scope

BODY_TEXT: Final = "body"
BODY_B64: Final = "body_b64"
HEADERS: Final = "headers"

logger = logging.getLogger(__name__)


class UnknownUserError(RuntimeError):
    """The principal has no ``users`` row; ``get_current_user`` did not run first."""


def encode_response(body: bytes, headers: Iterable[tuple[str, str]]) -> dict[str, Any]:
    stored: dict[str, Any] = {HEADERS: [[name, value] for name, value in headers]}
    try:
        stored[BODY_TEXT] = body.decode("utf-8")
    except UnicodeDecodeError:
        stored[BODY_B64] = base64.b64encode(body).decode("ascii")
    return stored


def decode_response(stored: Any) -> tuple[bytes, tuple[tuple[str, str], ...]]:
    if not isinstance(stored, dict):
        return b"", ()
    if BODY_B64 in stored:
        body = base64.b64decode(stored[BODY_B64])
    else:
        body = str(stored.get(BODY_TEXT, "")).encode("utf-8")
    headers = tuple((str(name), str(value)) for name, value in stored.get(HEADERS, ()))
    return body, headers


def to_stored_response(record: IdempotencyKey) -> StoredResponse:
    body, headers = (b"", ()) if record.status_code is None else decode_response(record.response)
    return StoredResponse(
        request_hash=record.request_hash,
        status=record.status_code,
        body=body,
        headers=headers,
        expires_at=record.expires_at.timestamp(),
    )


class PostgresIdempotencyStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def _user_id(self, session: AsyncSession, uid: str) -> uuid.UUID:
        user_id = await session.scalar(select(User.id).where(User.firebase_uid == uid))
        if user_id is None:
            raise UnknownUserError(
                "no users row for this principal; declare get_current_user before "
                "the idempotency dependency"
            )
        return user_id

    async def get(self, user_key: UserKey) -> StoredResponse | None:
        async with session_scope(self._sessionmaker) as session:
            user_id = await self._user_id(session, user_key.uid)
            record = await IdempotencyRepository(session).get(user_id, user_key.key)
            return None if record is None else to_stored_response(record)

    async def reserve(
        self, user_key: UserKey, request_hash: str, ttl: timedelta, *, route: str = ""
    ) -> StoredResponse | None:
        async with session_scope(self._sessionmaker) as session:
            user_id = await self._user_id(session, user_key.uid)
            claim = await IdempotencyRepository(session).claim(
                user_id, user_key.key, route or "unknown", request_hash, ttl
            )
            return None if claim.created else to_stored_response(claim.record)

    async def put(
        self,
        user_key: UserKey,
        request_hash: str,
        status: int,
        body: bytes,
        headers: Iterable[tuple[str, str]],
        ttl: timedelta,
    ) -> None:
        async with session_scope(self._sessionmaker) as session:
            user_id = await self._user_id(session, user_key.uid)
            stored = await IdempotencyRepository(session).complete(
                user_id,
                user_key.key,
                status,
                encode_response(body, headers),
                ttl=ttl,
                request_hash=request_hash,
            )
        if not stored:
            # The reservation expired and was taken over (or purged) while the handler ran.
            logger.warning("idempotency reservation no longer held; response not stored")

    async def release(self, user_key: UserKey, *, request_hash: str | None = None) -> None:
        async with session_scope(self._sessionmaker) as session:
            user_id = await self._user_id(session, user_key.uid)
            await IdempotencyRepository(session).release(
                user_id, user_key.key, request_hash=request_hash
            )
