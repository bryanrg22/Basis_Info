"""Studies: creation, visibility through org membership, the library listing (search,
filters, sort, keyset pages, a total), stage transitions, archive and unarchive.

The listing (``GET /v1/studies``, docs/api/STUDY_LIBRARY.md) sorts by one of
:data:`SORTS` and pages with a keyset cursor that encodes the sort it was issued under and
the last row's sort key, so a page never skips or repeats a row while rows are inserted or
updated between requests, and a cursor cannot be replayed under another sort (the keys
would not compare). The same filters give ``total``, the count of matching rows.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import EllipsisType
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, and_, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import OrgRole, Stage, StudyStatus, status_for_stage
from basis.db.errors import (
    IntakeLockedError,
    InvalidCursorError,
    NotVisibleError,
    PreconditionFailedError,
    StaleStateError,
)
from basis.db.models import Extraction, ExtractionField, OrgMember, Run, Study
from basis.db.repositories.study_event import Actor, StudyEventRepository
from basis.domain.intake import StudyIntake

MAX_PAGE_SIZE: Final = 100

StudySort = Literal["updated_desc", "created_desc", "name_asc"]
SORTS: Final[tuple[StudySort, ...]] = ("updated_desc", "created_desc", "name_asc")
DEFAULT_SORT: Final[StudySort] = "updated_desc"
# A name sorts by its lower-cased form so case never splits the alphabet; ties by id.
_NAME_KEY: Final = func.lower(Study.property_name)


@dataclass(frozen=True, slots=True)
class StudyPage:
    items: list[Study]
    next_cursor: str | None
    total: int = 0


@dataclass(frozen=True, slots=True)
class StudyFilters:
    """What ``GET /v1/studies`` narrows the listing by (docs/api/STUDY_LIBRARY.md)."""

    q: str | None = None  # case-insensitive substring of the property name (trimmed)
    statuses: Sequence[StudyStatus] = ()
    created_by: uuid.UUID | None = None
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class VisibleStudy:
    study: Study
    role: OrgRole


@dataclass(frozen=True, slots=True)
class RestartRecord:
    """The study row reset for a new execution (:meth:`StudyRepository.restart`): the study
    as it is now, where it came from, and whether an earlier restart had already reset it
    (its start failed or is in flight: nothing was changed again)."""

    study: Study
    from_stage: Stage
    from_revision: int
    reused: bool


@dataclass(frozen=True, slots=True)
class ResumeRecord:
    """The study row moved to a gate on a new revision for a resumed execution
    (:meth:`StudyRepository.begin_revision_at`, docs/api/REVISIONS.md): the study as it is
    now, the revision copied into it, where it came from, and whether an earlier request had
    already made this exact reset (its start is in flight or answered 503: nothing changed
    again)."""

    study: Study
    from_stage: Stage
    from_revision: int
    source_revision: int
    reused: bool


# The events that mark a revision begun for a resumed execution (docs/api/REVISIONS.md).
REOPENED_EVENT: Final = "study.reopened"
RESTORED_EVENT: Final = "revision.restored"


@dataclass(frozen=True, slots=True)
class PendingResume:
    """A reset made for a resumed execution whose start has not happened (the last resume
    event at the study's current revision, with no ``run.started`` after it): a repeat of
    the same request reuses it (:meth:`StudyRepository.pending_resume`)."""

    stage: Stage
    source_revision: int
    reason: str
    from_stage: Stage
    from_revision: int

    def matches(self, stage: Stage, source_revision: int | None) -> bool:
        reason = "reopen" if source_revision is None else "restore"
        return (
            self.stage is stage
            and self.reason == reason
            and (source_revision is None or self.source_revision == source_revision)
        )


@dataclass(frozen=True, slots=True)
class Unarchived:
    """What :meth:`StudyRepository.unarchive` did: the study as it is now, and whether this
    call restored it. ``restored`` is decided under the study's row lock, not from a read
    taken before it, so the caller records the change exactly when it made one."""

    study: Study
    restored: bool


@dataclass(frozen=True, slots=True)
class Archived:
    """What :meth:`StudyRepository.archive` did: the study as it is now, and whether this
    call archived it (decided under the row lock, as :class:`Unarchived` is)."""

    study: Study
    archived: bool


@dataclass(frozen=True, slots=True)
class IntakeWrite:
    """An intake write: the study after it, and the intake and version it replaced."""

    study: Study
    before: StudyIntake | None
    version_before: int


# ``(current version, whether the study has an intake) -> may the write proceed``.
IntakePrecondition = Callable[[int, bool], bool]


def encode_cursor(sort: StudySort, study: Study) -> str:
    """Opaque keyset cursor: the sort and the row's sort key (``{"s", "k", "id"}``)."""
    key: str = (
        study.property_name.lower()
        if sort == "name_asc"
        else (study.updated_at if sort == "updated_desc" else study.created_at).isoformat()
    )
    raw = json.dumps({"s": sort, "k": key, "id": str(study.id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, sort: StudySort) -> tuple[Any, uuid.UUID]:
    """The sort key and id a cursor names, for ``sort``; ``InvalidCursorError`` when it
    does not decode or was issued under another sort (its key is not comparable)."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        issued, study_id = data["s"], uuid.UUID(data["id"])
        key: Any = data["k"]
        if issued in SORTS and issued != "name_asc":
            key = datetime.fromisoformat(key)
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if issued != sort:
        raise InvalidCursorError(f"the cursor was issued for sort={issued}, not {sort}")
    if (sort == "name_asc" and not isinstance(key, str)) or (
        sort != "name_asc" and key.tzinfo is None
    ):
        raise InvalidCursorError("invalid pagination cursor")
    return key, study_id


def _escape_like(text: str) -> str:
    """``text`` as a literal inside a LIKE pattern (``\\`` is the escape character)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filter_clauses(filters: StudyFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    statuses = list(filters.statuses)
    if statuses:
        clauses.append(Study.status.in_(statuses))
    # Archived studies are hidden unless asked for, by the switch or by naming the status.
    if not filters.include_archived and StudyStatus.ARCHIVED not in statuses:
        clauses.append(Study.status != StudyStatus.ARCHIVED)
    q = (filters.q or "").strip()
    if q:
        clauses.append(Study.property_name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if filters.created_by is not None:
        clauses.append(Study.created_by == filters.created_by)
    return clauses


class StudyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        org_id: uuid.UUID,
        created_by: uuid.UUID,
        property_name: str,
        room_aliases: dict[str, str] | None = None,
        intake: StudyIntake | None = None,
    ) -> Study:
        study = Study(
            org_id=org_id,
            created_by=created_by,
            property_name=property_name,
            room_aliases=dict(room_aliases or {}),
            intake=intake,
            # The intake's version: 0 while there is none, 1 for the first one (migration 0018).
            intake_version=0 if intake is None else 1,
        )
        self._session.add(study)
        await self._session.flush()
        await self._session.refresh(study)
        return study

    async def get(self, study_id: uuid.UUID) -> Study | None:
        """Unscoped lookup for trusted callers (the worker). The API uses get_for_user."""
        return await self._session.get(Study, study_id)

    async def get_visible(self, study_id: uuid.UUID, user_id: uuid.UUID) -> VisibleStudy | None:
        """The study and the user's role in its org, or None when the user is not a member."""
        stmt = (
            select(Study, OrgMember.role)
            .join(
                OrgMember,
                and_(OrgMember.org_id == Study.org_id, OrgMember.user_id == user_id),
            )
            .where(Study.id == study_id)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        study, role = row
        return VisibleStudy(study=study, role=OrgRole(role))

    async def get_for_user(self, study_id: uuid.UUID, user_id: uuid.UUID) -> Study | None:
        visible = await self.get_visible(study_id, user_id)
        return None if visible is None else visible.study

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        cursor: str | None = None,
        *,
        include_archived: bool = False,
        filters: StudyFilters | None = None,
        sort: StudySort = DEFAULT_SORT,
    ) -> StudyPage:
        """Studies in every org the user belongs to, narrowed by ``filters``, in ``sort``
        order, keyset-paginated; ``total`` counts every row the filters match.

        ``updated_desc`` (the default): ``(updated_at, id)`` descending, so a study moves
        to the top when anything about it changes; ``created_desc``: ``(created_at, id)``
        descending, the order before sorting existed; ``name_asc``: ``(lower(name), id)``
        ascending. The cursor carries the sort it was issued under and the last row's key
        (:func:`encode_cursor`); one issued under another sort is ``InvalidCursorError``.
        ``include_archived`` here is the keyword clients used before ``filters``; either
        switch shows archived studies.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        filters = filters or StudyFilters()
        if include_archived and not filters.include_archived:
            filters = StudyFilters(filters.q, filters.statuses, filters.created_by, True)
        member = and_(OrgMember.org_id == Study.org_id, OrgMember.user_id == user_id)
        clauses = _filter_clauses(filters)
        total = await self._session.scalar(
            select(func.count()).select_from(Study).join(OrgMember, member).where(*clauses)
        )
        stmt = select(Study).join(OrgMember, member).where(*clauses)
        if sort == "name_asc":
            stmt = stmt.order_by(_NAME_KEY, Study.id)
        elif sort == "created_desc":
            stmt = stmt.order_by(Study.created_at.desc(), Study.id.desc())
        else:
            stmt = stmt.order_by(Study.updated_at.desc(), Study.id.desc())
        if cursor is not None:
            key, after_id = decode_cursor(cursor, sort)
            if sort == "name_asc":
                stmt = stmt.where(tuple_(_NAME_KEY, Study.id) > (key, after_id))
            elif sort == "created_desc":
                stmt = stmt.where(tuple_(Study.created_at, Study.id) < (key, after_id))
            else:
                stmt = stmt.where(tuple_(Study.updated_at, Study.id) < (key, after_id))
        rows = list((await self._session.scalars(stmt.limit(limit + 1))).all())
        items = rows[:limit]
        next_cursor = encode_cursor(sort, items[-1]) if len(rows) > limit else None
        return StudyPage(items=items, next_cursor=next_cursor, total=int(total or 0))

    async def transition(
        self,
        study_id: uuid.UUID,
        from_stage: Stage,
        to_stage: Stage,
        expected_revision: int,
        *,
        new_revision: int | None = None,
        actor: Actor = "system",
        actor_id: uuid.UUID | None = None,
    ) -> Study:
        """Compare-and-set the study's stage (and optionally its revision).

        The move is recorded as a ``stage.entered`` event in the same transaction
        (``actor``: the engineer whose decision moved it, else the workflow).

        ``UPDATE studies ... WHERE id = :id AND current_stage = :from AND revision =
        :expected AND status <> 'archived'``. Under READ COMMITTED a concurrent
        transition holding the row lock makes this statement wait and then re-check the
        WHERE clause against the committed row, so of two racing transitions from the
        same state exactly one updates a row. Zero rows -> ``StaleStateError`` (or
        ``NotVisibleError`` when the study does not exist).

        ``status`` follows the stage (see ``status_for_stage``).
        """
        if new_revision is not None and new_revision < expected_revision:
            raise ValueError("new_revision cannot be lower than expected_revision")
        if from_stage == to_stage and new_revision in (None, expected_revision):
            raise ValueError("a transition must change the stage or the revision")
        values: dict[str, object] = {
            "current_stage": to_stage,
            "status": status_for_stage(to_stage),
        }
        if new_revision is not None:
            values["revision"] = new_revision
        stmt = (
            update(Study)
            .where(
                Study.id == study_id,
                Study.current_stage == from_stage,
                Study.revision == expected_revision,
                Study.status != StudyStatus.ARCHIVED,
            )
            .values(values)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one_or_none()
        if study is not None:
            await StudyEventRepository(self._session).record(
                study_id,
                "stage.entered",
                actor=actor,
                actor_id=actor_id,
                stage=to_stage,
                revision=study.revision,
                before={"stage": from_stage.value, "revision": expected_revision},
                after={"stage": to_stage.value, "revision": study.revision},
            )
            return study
        current = await self._session.execute(
            select(Study.current_stage, Study.revision, Study.status).where(Study.id == study_id)
        )
        found = current.one_or_none()
        if found is None:
            raise NotVisibleError(f"study {study_id} not found")
        stage, revision, status = found
        raise StaleStateError(
            f"study {study_id} is at {stage.value} revision {revision} ({status.value}); "
            f"expected {from_stage.value} revision {expected_revision}"
        )

    async def mark_started(
        self,
        study_id: uuid.UUID,
        workflow_id: str,
        expected_revision: int,
        *,
        actor_id: uuid.UUID | None = None,
    ) -> Study:
        """``uploading -> extracting`` and record the study's workflow id, at most once.

        The compare-and-set is :meth:`transition` (same row lock and re-check), followed
        by setting ``workflow_id`` in the same transaction. When the transition loses to
        a concurrent start that recorded this same ``workflow_id``, the study as that
        start left it is returned, so concurrent starts converge instead of one failing.
        Any other stale state raises ``StaleStateError``.
        """
        try:
            await self.transition(
                study_id,
                Stage.UPLOADING,
                Stage.EXTRACTING,
                expected_revision,
                actor="engineer" if actor_id is not None else "system",
                actor_id=actor_id,
            )
        except StaleStateError:
            current = await self._session.get(Study, study_id, populate_existing=True)
            if current is not None and current.workflow_id == workflow_id:
                return current
            raise
        stmt = (
            update(Study)
            .where(Study.id == study_id)
            .values(workflow_id=workflow_id)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        started = (await self._session.scalars(stmt)).one()
        await StudyEventRepository(self._session).record(
            study_id,
            "study.started",
            actor="engineer" if actor_id is not None else "system",
            actor_id=actor_id,
            stage=Stage.EXTRACTING,
            revision=started.revision,
            detail={"workflow_id": workflow_id},
        )
        return started

    async def restart(self, study_id: uuid.UUID, *, actor_id: uuid.UUID) -> RestartRecord:
        """Reset the study row for a new execution of its workflow (``POST .../restart``,
        docs/api/GATE_DRAFTS_AND_RESUME.md): ``extracting``/``processing`` at ``revision + 1``,
        the failure cleared, recorded as ``stage.entered`` and ``study.restarted``.

        The new revision keeps the cancelled execution's rows as history (outputs are
        grouped by revision; the unique keys on extractions, reviews and the per-revision
        tables mean the new run cannot collide with them) and makes every gate draft for the
        old revision stale. The row lock (``SELECT ... FOR UPDATE``) serializes two restarts;
        the second finds the first's ``study.restarted`` event with no ``run.started`` after
        it and reuses the reset instead of bumping the revision a second time, so the row
        stays at the revision the execution the first restart started (or failed to start,
        when the route's Temporal call answered 503) was given. Archived: ``StaleStateError``;
        unknown: ``NotVisibleError``; no workflow: ``ValueError`` (the route checks first).
        """
        locked = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if locked is None:
            raise NotVisibleError(f"study {study_id} not found")
        if locked.status is StudyStatus.ARCHIVED:
            raise StaleStateError(f"study {study_id} is archived; it cannot be restarted")
        if locked.workflow_id is None:
            raise ValueError(f"study {study_id} has no workflow to restart")
        events = StudyEventRepository(self._session)
        page = await events.list_for_study(study_id, event_types=["study.restarted", "run.started"])
        last = page.items[-1] if page.items else None
        if (
            last is not None
            and last.event_type == "study.restarted"
            and isinstance(last.before, dict)
            and last.revision == locked.revision
        ):
            return RestartRecord(
                study=locked,
                from_stage=Stage(last.before["stage"]),
                from_revision=int(last.before["revision"]),
                reused=True,
            )
        # Captured before the UPDATE: its RETURNING refreshes this same identity-mapped row.
        from_stage, from_revision, from_status = (
            locked.current_stage,
            locked.revision,
            locked.status,
        )
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.status != StudyStatus.ARCHIVED)
            .values(
                current_stage=Stage.EXTRACTING,
                status=status_for_stage(Stage.EXTRACTING),
                revision=Study.revision + 1,
                failure_reason=None,
                failure_code=None,
            )
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one()
        before = {
            "stage": from_stage.value,
            "revision": from_revision,
            "status": from_status.value,
        }
        await events.record(
            study_id,
            "stage.entered",
            actor="engineer",
            actor_id=actor_id,
            stage=Stage.EXTRACTING,
            revision=study.revision,
            before={"stage": from_stage.value, "revision": from_revision},
            after={"stage": Stage.EXTRACTING.value, "revision": study.revision},
        )
        await events.record(
            study_id,
            "study.restarted",
            actor="engineer",
            actor_id=actor_id,
            stage=Stage.EXTRACTING,
            revision=study.revision,
            workflow_id=locked.workflow_id,
            before=before,
            after={"stage": Stage.EXTRACTING.value, "revision": study.revision},
            detail={"workflow_id": locked.workflow_id},
        )
        return RestartRecord(
            study=study, from_stage=from_stage, from_revision=from_revision, reused=False
        )

    async def begin_revision_at(
        self,
        study_id: uuid.UUID,
        stage: Stage,
        *,
        actor_id: uuid.UUID,
        source_revision: int | None = None,
        plan: dict[str, Any] | None = None,
    ) -> ResumeRecord:
        """Move a ``completed`` study to review gate ``stage`` on ``revision + 1`` (status
        ``processing`` until the workflow opens the gate) for a resumed execution
        (docs/api/REVISIONS.md): a reopen (``source_revision`` None: the completed revision is
        copied) or a restore (``source_revision`` the revision copied). Recorded as
        ``stage.entered`` and ``study.reopened`` / ``revision.restored`` (``detail``: the
        source revision, the reason and ``plan``, the recompute warning).

        The row lock serializes concurrent requests. A study that is no longer completed is
        ``StaleStateError``, unless the last of the resume, restart and ``run.started``
        events on its timeline is this same reset (same stage, source and reason at the
        current revision, no ``run.started`` after it): an earlier request made it and its
        start did not happen yet (in flight, or 503), so it is reused without a second
        revision bump; Temporal's start then admits exactly one of them. Archived:
        ``StaleStateError``; unknown: ``NotVisibleError``; no workflow: ``ValueError``.
        """
        locked = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if locked is None:
            raise NotVisibleError(f"study {study_id} not found")
        if locked.status is StudyStatus.ARCHIVED:
            raise StaleStateError(f"study {study_id} is archived; unarchive it first")
        if locked.workflow_id is None:
            raise ValueError(f"study {study_id} has no workflow; nothing to reopen")
        reason = "reopen" if source_revision is None else "restore"
        event_type = REOPENED_EVENT if source_revision is None else RESTORED_EVENT
        events = StudyEventRepository(self._session)
        pending = await self.pending_resume(study_id, locked.revision)
        if pending is not None and pending.matches(stage, source_revision):
            return ResumeRecord(
                study=locked,
                from_stage=pending.from_stage,
                from_revision=pending.from_revision,
                source_revision=pending.source_revision,
                reused=True,
            )
        completed = (
            locked.current_stage is Stage.COMPLETED and locked.status is StudyStatus.COMPLETED
        )
        if not completed:
            raise StaleStateError(
                f"study {study_id} is at {locked.current_stage.value} revision "
                f"{locked.revision} ({locked.status.value}), not completed"
            )
        from_stage, from_revision = locked.current_stage, locked.revision
        source = from_revision if source_revision is None else source_revision
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.status == StudyStatus.COMPLETED)
            .values(
                current_stage=stage,
                status=StudyStatus.PROCESSING,
                revision=Study.revision + 1,
                failure_reason=None,
                failure_code=None,
            )
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one()
        before = {"stage": from_stage.value, "revision": from_revision}
        after = {"stage": stage.value, "revision": study.revision}
        await events.record(
            study_id,
            "stage.entered",
            actor="engineer",
            actor_id=actor_id,
            stage=stage,
            revision=study.revision,
            before=before,
            after=after,
        )
        await events.record(
            study_id,
            event_type,
            actor="engineer",
            actor_id=actor_id,
            stage=stage,
            revision=study.revision,
            workflow_id=locked.workflow_id,
            before=before,
            after=after,
            detail={
                "from_revision": source,
                "reason": reason,
                "workflow_id": locked.workflow_id,
                **(plan or {}),
            },
        )
        return ResumeRecord(
            study=study,
            from_stage=from_stage,
            from_revision=from_revision,
            source_revision=source,
            reused=False,
        )

    async def pending_resume(self, study_id: uuid.UUID, revision: int) -> PendingResume | None:
        """The reset a resumed execution has not started from yet: the last of the study's
        ``study.reopened`` / ``revision.restored`` / ``study.restarted`` / ``run.started``
        events is a resume at ``revision`` (``run.started`` after it would mean the execution
        took it; a restart after it replaced it). None otherwise."""
        page = await StudyEventRepository(self._session).list_for_study(
            study_id,
            event_types=[REOPENED_EVENT, RESTORED_EVENT, "study.restarted", "run.started"],
        )
        last = page.items[-1] if page.items else None
        if (
            last is None
            or last.event_type not in (REOPENED_EVENT, RESTORED_EVENT)
            or last.revision != revision
            or last.stage is None
            or not isinstance(last.detail, dict)
            or not isinstance(last.before, dict)
            or not isinstance(last.detail.get("from_revision"), int)
        ):
            return None
        return PendingResume(
            stage=last.stage,
            source_revision=int(last.detail["from_revision"]),
            reason=str(last.detail.get("reason")),
            from_stage=Stage(last.before["stage"]),
            from_revision=int(last.before["revision"]),
        )

    async def set_intake(self, study_id: uuid.UUID, intake: StudyIntake) -> Study:
        """Set or replace the study's intake unconditionally (:meth:`write_intake`)."""
        return (await self.write_intake(study_id, intake)).study

    async def write_intake(
        self,
        study_id: uuid.UUID,
        intake: StudyIntake,
        *,
        precondition: IntakePrecondition | None = None,
    ) -> IntakeWrite:
        """Set or replace the study's intake (``PUT /v1/studies/{id}/intake``) and increment
        ``intake_version``, the intake's entity tag.

        ``SELECT ... FOR UPDATE`` takes the study's row lock first, so the version and the
        intake read here are the ones the write replaces (the event's ``before``) and a
        concurrent write waits for this transaction to end. ``precondition`` (the route's
        ``If-Match``) is then checked against that locked version: false ->
        ``PreconditionFailedError`` and nothing is written. The ``UPDATE`` also carries
        ``WHERE intake_version = :seen``, so the write is a compare-and-set even without the
        lock. Of two concurrent writes with the same expected version, the second sees the
        first's committed version once the lock is released and fails. Archived studies are
        left alone (``StaleStateError``, checked before the precondition); an unknown study
        is ``NotVisibleError``.
        """
        locked = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if locked is None:
            raise NotVisibleError(f"study {study_id} not found")
        if locked.status is StudyStatus.ARCHIVED:
            raise StaleStateError(f"study {study_id} is archived; its intake cannot change")
        if locked.current_stage is not Stage.UPLOADING:
            # Started: the run read this intake and the result and the report are priced
            # from it (docs/api/STUDY_RESULT.md, "Locked once the study starts").
            raise IntakeLockedError(
                f"study {study_id} has started (stage {locked.current_stage.value}); its "
                "intake is locked"
            )
        before, seen = locked.intake, locked.intake_version
        if precondition is not None and not precondition(seen, before is not None):
            raise PreconditionFailedError(
                f"the intake of study {study_id} is at version {seen}", version=seen
            )
        stmt = (
            update(Study)
            .where(
                Study.id == study_id,
                Study.status != StudyStatus.ARCHIVED,
                Study.intake_version == seen,
            )
            .values(intake=intake, intake_version=Study.intake_version + 1)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one_or_none()
        if study is None:  # pragma: no cover - the row lock makes this unreachable
            raise PreconditionFailedError(
                f"the intake of study {study_id} changed while it was written", version=seen
            )
        return IntakeWrite(study=study, before=before, version_before=seen)

    async def subject_location(self, study_id: uuid.UUID) -> tuple[str | None, str | None]:
        """``(state, zip)`` as the appraisal's subject fields read them, from the newest
        extraction of the study; ``None`` for a field no extraction has."""
        stmt = (
            select(ExtractionField.field, ExtractionField.value_text)
            .join(Extraction, Extraction.id == ExtractionField.extraction_id)
            .where(
                Extraction.study_id == study_id,
                ExtractionField.section == "subject",
                ExtractionField.field.in_(("state", "zip")),
                ExtractionField.value_text.is_not(None),
            )
            .order_by(Extraction.revision.desc(), Extraction.created_at.desc())
        )
        found: dict[str, str] = {}
        for field, value in (await self._session.execute(stmt)).all():
            if isinstance(value, str):
                found.setdefault(field, value)
        return found.get("state"), found.get("zip")

    async def runs(self, study_id: uuid.UUID) -> list[Run]:
        """The study's workflow runs (``runs`` rows), oldest first."""
        stmt = select(Run).where(Run.study_id == study_id).order_by(Run.started_at, Run.id)
        return list((await self._session.scalars(stmt)).all())

    async def archive(
        self, study_id: uuid.UUID, *, expected_workflow_id: str | None | EllipsisType = ...
    ) -> Archived:
        """Soft delete. Idempotent: archiving an archived study returns it unchanged,
        ``archived`` false, so the caller records the change exactly when it made one
        (two archives at once recorded two events before this; EVAL_REPORT 62).

        ``expected_workflow_id`` is the workflow id the caller read before deciding the
        study may be archived (none: never started; or one whose execution it found
        closed). It is compared under the row lock: a study that was started in between
        (``workflow_id`` set since) is ``StaleStateError`` and stays as it is, so an archive
        can never land on a study whose execution has just begun (the route refuses to
        archive a running study: docs/api/STUDY_LIBRARY.md). Left out: no comparison."""
        locked = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if locked is None:
            raise NotVisibleError(f"study {study_id} not found")
        if locked.status is StudyStatus.ARCHIVED:
            return Archived(locked, archived=False)
        if expected_workflow_id is not ... and locked.workflow_id != expected_workflow_id:
            raise StaleStateError(
                f"study {study_id} was started while the archive was decided "
                f"(workflow {locked.workflow_id}); it is not archived"
            )
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.status != StudyStatus.ARCHIVED)
            .values(status=StudyStatus.ARCHIVED)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return Archived((await self._session.scalars(stmt)).one(), archived=True)

    async def unarchive(self, study_id: uuid.UUID) -> Unarchived:
        """Undo :meth:`archive`: the status the study had before it was archived, derived
        the way the workflow derives it (``status_for_stage`` of its stage, ``failed`` while
        a failure is recorded on it; a ``stalled`` study comes back ``waiting_review`` and
        the reminder step marks it stalled again if the review is still waiting). Idempotent:
        unarchiving a study that is not archived returns it unchanged, ``restored`` false.

        Whether the study is archived is read under its row lock. Concurrent unarchives of
        one study serialize on that lock: the first restores it, the others find it restored
        and report ``restored`` false, whatever each read before the lock (a guard taken from
        the route's earlier, unlocked read recorded three events for three requests;
        EVAL_REPORT 56)."""
        locked = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if locked is None:
            raise NotVisibleError(f"study {study_id} not found")
        if locked.status is not StudyStatus.ARCHIVED:
            return Unarchived(locked, restored=False)
        restored = (
            StudyStatus.FAILED
            if locked.failure_reason is not None
            else status_for_stage(locked.current_stage)
        )
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.status == StudyStatus.ARCHIVED)
            .values(status=restored)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return Unarchived((await self._session.scalars(stmt)).one(), restored=True)
