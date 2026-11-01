"""The report's inputs and signatures (STUDY_REPORT_SPEC 11.13; migration ``0020``).

* The firm profile: one row per firm, written whole with a version (its entity tag). The
  firm's row lock serializes writes, including the first one, which inserts.
* The study's answers: ``studies.report_answers``, written like the intake (the study's row
  lock, then the precondition, then a compare-and-set on the version).
* Signatures: one per revision. :meth:`ReportRepository.add_signature` runs under the
  study's row lock (:meth:`lock_study`), so of two concurrent signings exactly one inserts;
  the unique key backs it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import StudyStatus
from basis.db.errors import (
    ConflictError,
    NotVisibleError,
    PreconditionFailedError,
    StaleStateError,
)
from basis.db.models import FirmProfileRow, ReportSignature, Study
from basis.db.repositories.org import OrgRepository
from basis.db.repositories.org_event import OrgEventRepository
from basis.db.repositories.study import IntakePrecondition
from basis.report.inputs import FirmProfile, ReportInputs, Signature, StudyAnswers


@dataclass(frozen=True, slots=True)
class ProfileWrite:
    row: FirmProfileRow
    before: FirmProfile | None
    version_before: int


@dataclass(frozen=True, slots=True)
class AnswersWrite:
    study: Study
    before: StudyAnswers | None
    version_before: int


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ firm profile

    async def firm_profile(self, org_id: uuid.UUID) -> FirmProfileRow | None:
        return await self._session.get(FirmProfileRow, org_id, populate_existing=True)

    async def write_firm_profile(
        self,
        org_id: uuid.UUID,
        profile: FirmProfile,
        user_id: uuid.UUID,
        *,
        precondition: IntakePrecondition | None = None,
    ) -> ProfileWrite:
        """Set or replace the firm's profile and increment its version (0 while there is
        none, so the first write makes it 1). ``precondition`` is checked under the firm's
        row lock against the version read there; false -> ``PreconditionFailedError``."""
        if not await OrgRepository(self._session).lock(org_id):
            raise NotVisibleError(f"org {org_id} not found")
        existing = await self.firm_profile(org_id)
        seen = existing.version if existing else 0
        before = existing.profile if existing else None
        if precondition is not None and not precondition(seen, existing is not None):
            raise PreconditionFailedError(
                f"the profile of org {org_id} is at version {seen}", version=seen
            )
        # The fields the write changed: on the first write, the ones it set.
        changed = sorted(
            key
            for key in type(profile).model_fields
            if (None if before is None else getattr(before, key)) != getattr(profile, key)
        )
        if existing is None:
            row = FirmProfileRow(org_id=org_id, profile=profile, version=1, updated_by=user_id)
            self._session.add(row)
            await self._session.flush()
            await self._session.refresh(row)
        else:
            existing.profile = profile
            existing.version = seen + 1
            existing.updated_by = user_id
            await self._session.flush()
            await self._session.refresh(existing)
            row = existing
        await OrgEventRepository(self._session).record(
            org_id,
            "profile.set",
            actor_id=user_id,
            entity_type="firm_profile",
            entity_id=org_id,
            detail={
                "version_before": seen,
                "version_after": row.version,
                "if_match": precondition is not None,
                "changed": changed,
            },
        )
        return ProfileWrite(row=row, before=before, version_before=seen)

    # ------------------------------------------------------------------ study answers

    async def write_answers(
        self,
        study_id: uuid.UUID,
        answers: StudyAnswers,
        *,
        precondition: IntakePrecondition | None = None,
    ) -> AnswersWrite:
        """Set or replace the study's report answers, as ``write_intake`` does the intake."""
        locked = await self.lock_study(study_id)
        if locked.status is StudyStatus.ARCHIVED:
            raise StaleStateError(f"study {study_id} is archived; its answers cannot change")
        before, seen = locked.report_answers, locked.report_answers_version
        if precondition is not None and not precondition(seen, before is not None):
            raise PreconditionFailedError(
                f"the report answers of study {study_id} are at version {seen}", version=seen
            )
        stmt = (
            update(Study)
            .where(Study.id == study_id, Study.report_answers_version == seen)
            .values(report_answers=answers, report_answers_version=seen + 1)
            .returning(Study)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        study = (await self._session.scalars(stmt)).one_or_none()
        if study is None:  # pragma: no cover - the row lock makes this unreachable
            raise PreconditionFailedError(
                f"the report answers of study {study_id} changed while written", version=seen
            )
        return AnswersWrite(study=study, before=before, version_before=seen)

    # ------------------------------------------------------------------ signatures

    async def lock_study(self, study_id: uuid.UUID) -> Study:
        """The study under its row lock until the transaction ends."""
        study = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if study is None:
            raise NotVisibleError(f"study {study_id} not found")
        return study

    async def signature(self, study_id: uuid.UUID, revision: int) -> ReportSignature | None:
        stmt = select(ReportSignature).where(
            ReportSignature.study_id == study_id, ReportSignature.revision == revision
        )
        return await self._session.scalar(stmt)

    async def add_signature(
        self,
        study_id: uuid.UUID,
        revision: int,
        *,
        signature_id: uuid.UUID,
        signed_by: uuid.UUID,
        signed_at: datetime,
        engineer: dict[str, Any],
        statement_version: int,
        export_sha256: str,
        reviewed_sha256: str,
        pdf_uri: str,
        pdf_sha256: str,
    ) -> ReportSignature:
        """Insert the revision's signature. Call under :meth:`lock_study`; a revision that
        already has one is ``ConflictError``."""
        if await self.signature(study_id, revision) is not None:
            raise ConflictError(f"revision {revision} of study {study_id} is already signed")
        row = ReportSignature(
            id=signature_id,
            study_id=study_id,
            revision=revision,
            signed_by=signed_by,
            signed_at=signed_at,
            engineer=engineer,
            statement_version=statement_version,
            export_sha256=export_sha256,
            reviewed_sha256=reviewed_sha256,
            pdf_uri=pdf_uri,
            pdf_sha256=pdf_sha256,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # ------------------------------------------------------------------ the inputs

    async def inputs(self, study: Study, *, signature: Signature | None = None) -> ReportInputs:
        """What the report renders with: the firm's profile, the study's answers and the
        given signature (none: a draft)."""
        firm = await self.firm_profile(study.org_id)
        return ReportInputs(
            firm=firm.profile if firm else None,
            study=study.report_answers,
            signature=signature,
        )

    async def now(self) -> datetime:
        """The database's transaction time, the signature's ``signed_at``."""
        value: datetime = (await self._session.execute(select(func.now()))).scalar_one()
        return value


__all__ = ["AnswersWrite", "ProfileWrite", "ReportRepository"]
