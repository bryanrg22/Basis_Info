"""Studies: creation, visibility through org membership, keyset listing, stage transitions."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import and_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import OrgRole, Stage, StudyStatus, status_for_stage
from basis.db.errors import InvalidCursorError, NotVisibleError, StaleStateError
from basis.db.models import Extraction, ExtractionField, OrgMember, Run, Study
from basis.domain.intake import StudyIntake

MAX_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class StudyPage:
    items: list[Study]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class VisibleStudy:
    study: Study
    role: OrgRole


def encode_cursor(created_at: datetime, study_id: uuid.UUID) -> str:
    """Opaque keyset cursor for ``(created_at, id)``."""
    raw = json.dumps({"t": created_at.isoformat(), "id": str(study_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        created_at = datetime.fromisoformat(data["t"])
        study_id = uuid.UUID(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if created_at.tzinfo is None:
        raise InvalidCursorError("invalid pagination cursor")
    return created_at, study_id


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
    ) -> StudyPage:
        """Studies in every org the user belongs to, newest first, keyset-paginated."""
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        stmt = (
            select(Study)
            .join(
                OrgMember,
                and_(OrgMember.org_id == Study.org_id, OrgMember.user_id == user_id),
            )
            .order_by(Study.created_at.desc(), Study.id.desc())
            .limit(limit + 1)
        )
        if not include_archived:
            stmt = stmt.where(Study.status != StudyStatus.ARCHIVED)
        if cursor is not None:
            after_created_at, after_id = decode_cursor(cursor)
            stmt = stmt.where(tuple_(Study.created_at, Study.id) < (after_created_at, after_id))
        rows = list((await self._session.scalars(stmt)).all())
        items = rows[:limit]
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
        )
        return StudyPage(items=items, next_cursor=next_cursor)

    async def transition(
        self,
        study_id: uuid.UUID,
        from_stage: Stage,
        to_stage: Stage,
        expected_revision: int,
        *,
        new_revision: int | None = None,
    ) -> Study:
        """Compare-and-set the study's stage (and optionally its revision).

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
        self, study_id: uuid.UUID, workflow_id: str, expected_revision: int
    ) -> Study:
        """``uploading -> extracting`` and record the study's workflow id, at most once.

        The compare-and-set is :meth:`transition` (same row lock and re-check), followed
        by setting ``workflow_id`` in the same transaction. When the transition loses to
        a concurrent start that recorded this same ``workflow_id``, the study as that
        start left it is returned, so concurrent starts converge instead of one failing.
        Any other stale state raises ``StaleStateError``.
        """
        try:
            await self.transition(study_id, Stage.UPLOADING, Stage.EXTRACTING, expected_revision)
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
        return (await self._session.scalars(stmt)).one()

    async def set_intake(self, study_id: uuid.UUID, intake: StudyIntake) -> Study:
        """Set or replace the study's intake (``PUT /v1/studies/{id}/intake``); archived
        studies are left alone (``StaleStateError``)."""
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.status != StudyStatus.ARCHIVED)
            .values(intake=intake)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one_or_none()
        if study is not None:
            return study
        existing = await self._session.get(Study, study_id, populate_existing=True)
        if existing is None:
            raise NotVisibleError(f"study {study_id} not found")
        raise StaleStateError(f"study {study_id} is archived; its intake cannot change")

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

    async def archive(self, study_id: uuid.UUID) -> Study:
        """Soft delete. Idempotent: archiving an archived study returns it unchanged."""
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.status != StudyStatus.ARCHIVED)
            .values(status=StudyStatus.ARCHIVED)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one_or_none()
        if study is not None:
            return study
        existing = await self._session.get(Study, study_id, populate_existing=True)
        if existing is None:
            raise NotVisibleError(f"study {study_id} not found")
        return existing
