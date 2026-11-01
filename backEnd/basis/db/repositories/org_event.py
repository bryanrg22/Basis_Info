"""The firm's own history (``org_events``): what happened to a firm that is not about a
study, in order (docs/api/TIMELINE_AND_AUDIT.md "Firm activity").

Append-only (``forbid_mutation()``), one row per occurrence, written in the same transaction
as the change it records by the repository that makes the change, so the operator CLI and
the API record alike and a rollback removes both. ``GET /v1/orgs/{org_id}/activity`` (owner)
reads it newest first, keyset-paginated on ``seq``.

Event vocabulary (``event_type``; the actor in brackets; ``subject_user_id`` the member the
event is about):

* ``org.created`` [engineer: the creator, also the subject, from ``POST /v1/orgs``; system
  from the operator CLI]: ``after.name``;
* ``org.renamed`` [engineer: an owner]: ``before``/``after`` ``{name}``;
* ``invitation.created`` [engineer, or system from the CLI]: ``entity_id`` the invitation;
  ``after`` ``{email, role, expires_at}``; ``detail.replaced`` the expired invitation it
  closed, when it closed one;
* ``invitation.revoked`` [engineer, or system from the CLI]: ``entity_id`` the invitation;
  ``before`` ``{email, role}``; ``detail.reason`` ``revoked`` (an owner revoked it),
  ``member_removed`` (the member's removal revoked their open invitations) or ``expired``
  (re-inviting closed an expired one);
* ``invitation.accepted`` [engineer: the invitee, also the subject]: ``entity_id`` the
  invitation; ``after`` ``{email, role}`` the role the member has afterwards;
  ``detail.role_before`` the role they already had (null when they joined);
* ``invitation.declined`` [engineer: the invitee, also the subject]: ``entity_id`` the
  invitation; ``before`` ``{email, role}``;
* ``member.role_changed`` [engineer]: ``before``/``after`` ``{role}``;
* ``member.removed`` [engineer]: ``before`` ``{role}``; ``detail.invitations_revoked`` the
  ids of the open invitations the removal revoked; ``detail.self`` when an owner left;
* ``profile.set`` [engineer]: the firm's report profile written (``PUT
  /v1/orgs/{org_id}/profile``); ``detail`` ``{version_before, version_after, if_match,
  changed}``, the profile's fields that changed. The profile itself is not copied into the
  event (its logo is up to 512 KB); the ``firm_profiles`` row holds the current one.

What is not here: a member's study actions (``study_events``, the per-study timeline and
``GET /v1/me/activity``) and the cost index store's runs (``cost_index_runs``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import OrgEvent
from basis.db.repositories.study_event import request_id

OrgActor = Literal["engineer", "system"]

MAX_ORG_ACTIVITY_PAGE: Final = 200


@dataclass(frozen=True, slots=True)
class OrgEventPage:
    items: list[OrgEvent]
    next_seq: int | None


class OrgEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        org_id: uuid.UUID,
        event_type: str,
        *,
        actor_id: uuid.UUID | None,
        subject_user_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        before: Any | None = None,
        after: Any | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OrgEvent:
        """Append one event (flushed, not committed: the caller's transaction decides).
        ``actor_type`` follows ``actor_id``: a user is an ``engineer``, none the ``system``
        (the operator CLI)."""
        row = OrgEvent(
            org_id=org_id,
            event_type=event_type,
            actor_type="engineer" if actor_id is not None else "system",
            actor_id=actor_id,
            subject_user_id=subject_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id(),
            before=before,
            after=after,
            detail=detail,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_org(
        self,
        org_id: uuid.UUID,
        *,
        before_seq: int | None = None,
        limit: int = 50,
        event_types: Sequence[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> OrgEventPage:
        """The firm's events newest first (``seq`` descending), optionally below
        ``before_seq`` (keyset), between ``since`` and ``until`` (inclusive, on
        ``created_at``) and of ``event_types``; ``next_seq`` is the cursor for the next page
        (None on the last)."""
        if not 1 <= limit <= MAX_ORG_ACTIVITY_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_ORG_ACTIVITY_PAGE}")
        stmt = select(OrgEvent).where(OrgEvent.org_id == org_id)
        if before_seq is not None:
            stmt = stmt.where(OrgEvent.seq < before_seq)
        if event_types:
            stmt = stmt.where(OrgEvent.event_type.in_(list(event_types)))
        if since is not None:
            stmt = stmt.where(OrgEvent.created_at >= since)
        if until is not None:
            stmt = stmt.where(OrgEvent.created_at <= until)
        stmt = stmt.order_by(OrgEvent.seq.desc()).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        items = rows[:limit]
        return OrgEventPage(items=items, next_seq=items[-1].seq if len(rows) > limit else None)
