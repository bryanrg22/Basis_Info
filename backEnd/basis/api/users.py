"""The database user behind a request, and study authorization (S1_SPEC section 4).

* :func:`get_current_user` turns the verified token principal into a ``users`` row,
  creating the user, a personal org and an ``owner`` membership on first sight
  (``UserRepository.upsert_by_firebase_uid``).
* :func:`require_study_access` (and the :func:`study_access` dependency factory) checks
  membership in the study's org: **404** when the study does not exist or the user is not
  a member of its org (the two are indistinguishable, so ids cannot be probed), **403**
  when the user is a member but their role is below the one the route needs
  (``viewer`` < ``engineer`` < ``owner``).

Why the user upsert has its own transaction instead of the request's ``SessionDep``:

1. ``UPDATE users SET last_seen_at`` takes the user's row lock until the transaction
   ends. Inside the request transaction every concurrent request of the same user would
   queue on that lock for the whole request.
2. The Postgres idempotency store runs in its own transactions and resolves the uid to
   ``users.id``; on a first request the user must already be committed for it to see it.
3. A user (and their personal org) that has authenticated exists even if the request
   they sent fails; nothing about the request should roll that back.

``last_seen_at`` is refreshed at most every :data:`LAST_SEEN_RESOLUTION` (or when the
token's email or name changed), so a read request does not write on every call.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.auth import CurrentPrincipal
from basis.api.errors import Forbidden, NotFound, ServiceUnavailable
from basis.auth.firebase import Principal
from basis.db.enums import OrgRole
from basis.db.models import User
from basis.db.repositories import StudyRepository, UserRepository, VisibleStudy
from basis.db.session import SESSIONMAKER_STATE_ATTR, SessionDep, session_scope

LAST_SEEN_RESOLUTION: Final = timedelta(minutes=5)
DETAIL_NO_DATABASE: Final = "The database is not configured."
DETAIL_STUDY_NOT_FOUND: Final = "Study not found."


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated caller: the ``users`` row and the token it was resolved from."""

    user: User
    principal: Principal

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, SESSIONMAKER_STATE_ATTR, None
    )
    if sessionmaker is None:
        raise ServiceUnavailable(DETAIL_NO_DATABASE)
    return sessionmaker


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


def _is_current(user: User, principal: Principal, now: datetime) -> bool:
    """True when the stored row needs no write for this request."""
    if user.last_seen_at is None or now - user.last_seen_at >= LAST_SEEN_RESOLUTION:
        return False
    if principal.email is not None and principal.email != user.email:
        return False
    return principal.display_name is None or principal.display_name == user.display_name


async def get_current_user(
    principal: CurrentPrincipal, sessionmaker: SessionmakerDep
) -> CurrentUser:
    async with session_scope(sessionmaker) as session:
        users = UserRepository(session)
        existing = await users.get_by_firebase_uid(principal.uid)
        if existing is not None and _is_current(existing, principal, datetime.now(UTC)):
            return CurrentUser(user=existing, principal=principal)
        user = await users.upsert_by_firebase_uid(
            principal.uid, principal.email, principal.display_name
        )
    return CurrentUser(user=user, principal=principal)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def require_study_access(
    session: AsyncSession, user: CurrentUser, study_id: uuid.UUID, min_role: OrgRole
) -> VisibleStudy:
    """The study and the caller's role in its org; 404 if not visible, 403 if role too low."""
    visible = await StudyRepository(session).get_visible(study_id, user.id)
    if visible is None:
        raise NotFound(DETAIL_STUDY_NOT_FOUND)
    if visible.role.rank < min_role.rank:
        raise Forbidden(
            f"This action needs the {min_role.value} role or higher in the study's "
            f"organization; you are {visible.role.value}."
        )
    return visible


StudyIdPath = Annotated[uuid.UUID, Path(description="Study id.")]


def study_access(min_role: OrgRole) -> Callable[..., Awaitable[VisibleStudy]]:
    """Dependency factory: ``Depends(study_access(OrgRole.ENGINEER))`` for ``{study_id}``."""

    async def dependency(
        study_id: StudyIdPath, user: CurrentUserDep, session: SessionDep
    ) -> VisibleStudy:
        return await require_study_access(session, user, study_id, min_role)

    dependency.__name__ = f"study_access_{min_role.value}"
    return dependency
