"""Missing-input records for the operators (EVAL_REPORT 78; Bryan, 2026-11-02).

When a study's dollars are held by a missing input (:mod:`basis.domain.money_inputs`: the
appraisal lacks a field, a room has no measured area, the cost table has no row, ...), that is
not a fault in the software, but it is where a firm new to the product gets stuck. Each
blocker of a revision is written to the store the browser's errors use (``client_errors``,
kind ``study_input``, code ``E.STUDY_INPUT_MISSING``), so an operator sees, per firm, who hit what
on which study, with the detail to diagnose it and help:

* who: the firm, and the person whose decision started the run (the latest review of the
  study; the study's creator before any), with their email; the study's creator too;
* where: the study (name, stage, status), the revision, the stage the engineer fixes it at;
* what: the blocker's code, message, how many assets it holds and which (up to 200 ids), and
  where it is fixed;
* the inputs: the intake's location and dates (no money), and every document the study has
  (name, kind, size, sha256, uploader, upload time and storage URI) so an operator can look at
  the files the firm uploaded.

One record per study, revision and blocker (``event_id`` is a UUID5 of the three, so a retried
activity records nothing twice); records group into one issue per firm and blocker, which an
operator marks ``in_progress`` or ``resolved`` (docs/api/CLIENT_ERRORS.md). Recording never
fails the study: :func:`record_input_blockers` runs in a savepoint and logs a failure.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.config import Settings
from basis.db.models import Org, Review, Study, User
from basis.db.repositories.client_error import ClientErrorRepository, NewClientError
from basis.db.repositories.document import DocumentRepository
from basis.domain.money_inputs import codes_in
from basis.errors.codes import E
from basis.evidence.result import (
    UNCLASSIFIED_PREFIX,
    ScheduleRow,
    compute_result,
    load_result_inputs,
    load_schedule,
)

logger = logging.getLogger(__name__)

KIND: Final = "study_input"
NAMESPACE: Final = uuid.UUID("2b0e7f4c-5d3a-4e1b-9c6f-0a8d7e5b3c21")
MAX_ASSET_IDS: Final = 200
# The stage the engineer resolves a blocker at, by its code (the fix names it too).
FIX_STAGE: Final[dict[str, str]] = {
    "input:count": "components_review",
    "input:room_type": "rooms_review",
    "unclassified": "assets_review",
    "unpriced": "assets_review",
    "input:exterior_area": "assets_review",
    "input:room": "assets_review",
    "input:cabinet_run": "assets_review",
}


def event_id(study_id: uuid.UUID, revision: int, code: str) -> uuid.UUID:
    """The record's id: one per study, revision and blocker."""
    return uuid.uuid5(NAMESPACE, f"{study_id}:{revision}:{code}")


def held_assets(rows: Sequence[ScheduleRow], code: str) -> list[str]:
    """The ids of the assets a blocker holds (none for a study-level blocker)."""
    if code.startswith("input:"):
        wanted = code[len("input:") :]
        return [r.asset_id for r in rows if wanted in codes_in(r.review_reason)]
    if code == "unpriced":
        return [r.asset_id for r in rows if r.rcn is None and not codes_in(r.review_reason)]
    if code == "unclassified":
        return [r.asset_id for r in rows if r.category.startswith(UNCLASSIFIED_PREFIX)]
    return []


async def responsible(session: AsyncSession, study: Study) -> tuple[uuid.UUID, dict[str, Any]]:
    """The person to attribute the run to: the reviewer of the study's latest decision (it
    started the run), else the study's creator; with what they did."""
    review = await session.scalar(
        select(Review)
        .where(Review.study_id == study.id)
        .order_by(Review.created_at.desc())
        .limit(1)
    )
    if review is None:
        return study.created_by, {"role": "created the study"}
    return review.reviewer_id, {
        "role": "decided the review that started this run",
        "stage": review.stage.value,
        "revision": review.revision,
        "decision": review.decision.value,
        "decided_at": review.created_at.isoformat(),
    }


async def _person(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    user = await session.get(User, user_id)
    return {
        "user_id": str(user_id),
        "email": None if user is None else user.email,
        "name": None if user is None else user.display_name,
    }


def _intake(study: Study) -> dict[str, Any] | None:
    """Where and when, not money: what the cost year and location were resolved from."""
    intake = study.intake
    if intake is None:
        return None
    return {
        "state": intake.state,
        "zip": intake.zip,
        "location_source": intake.location_source,
        "placed_in_service_date": intake.placed_in_service_date.isoformat(),
        "property_use": intake.property_use.value,
        "land_method": intake.land_method.value,
    }


async def record_input_blockers(
    session: AsyncSession, study_id: uuid.UUID, revision: int, settings: Settings
) -> int:
    """Record the revision's blockers for the operators; returns how many records were new.
    Runs in a savepoint: a failure is logged and the caller's transaction goes on."""
    try:
        async with session.begin_nested():
            return await _record(session, study_id, revision, settings)
    except Exception:  # noqa: BLE001 - diagnostics never fail the study
        logger.warning(
            "missing-input records were not written",
            extra={"study_id": str(study_id), "revision": revision},
            exc_info=True,
        )
        return 0


async def _record(
    session: AsyncSession, study_id: uuid.UUID, revision: int, settings: Settings
) -> int:
    study = await session.get(Study, study_id)
    if study is None:
        return 0
    rows = await load_schedule(session, study_id, revision)
    structure, pricing = await load_result_inputs(
        session, study_id, revision, study.intake, study.intake_version, settings
    )
    result = compute_result(rows, study.intake, revision, structure, pricing)
    if not result.blockers:
        return 0
    user_id, why = await responsible(session, study)
    person = {**(await _person(session, user_id)), **why}
    creator = await _person(session, study.created_by)
    firm = await session.get(Org, study.org_id)
    documents = [
        {
            "document_id": str(d.id),
            "kind": d.kind.value,
            "filename": d.filename,
            "content_type": d.content_type,
            "bytes": d.bytes,
            "sha256": d.sha256,
            "uploaded_by": None if d.uploaded_by is None else str(d.uploaded_by),
            "uploaded_at": None if d.uploaded_at is None else d.uploaded_at.isoformat(),
            "storage_uri": d.gcs_uri,
        }
        for d in await DocumentRepository(session).list_for_study(study_id)
    ]
    now = datetime.now(UTC)
    repo = ClientErrorRepository(session)
    written = 0
    for blocker in result.blockers:
        held = held_assets(rows, blocker.code)
        event = {
            "source": "server",
            "blocker": blocker.model_dump(mode="json"),
            "fix_stage": FIX_STAGE.get(
                blocker.code,
                "appraisal_review" if blocker.code.startswith("input:") else "assets_review",
            ),
            "asset_ids": held[:MAX_ASSET_IDS],
            "assets_held": len(held),
            "all_blockers": [b.code for b in result.blockers],
            "missing": list(result.missing),
            "study": {
                "study_id": str(study.id),
                "name": study.property_name,
                "status": study.status.value,
                "current_stage": study.current_stage.value,
                "revision": revision,
                "created_by": creator,
            },
            "firm": {"org_id": str(study.org_id), "name": None if firm is None else firm.name},
            "person": person,
            "intake": _intake(study),
            "documents": documents,
        }
        record = await repo.record(
            NewClientError(
                event_id=event_id(study_id, revision, blocker.code),
                occurred_at=now,
                user_id=user_id,
                kind=KIND,
                message=f"{study.property_name}: {blocker.message}",
                event=event,
                org_id=study.org_id,
                study_id=study_id,
                revision=revision,
                stage=study.current_stage.value,
                error_type=blocker.code,
                code=E.STUDY_INPUT_MISSING,
                build_sha=settings.git_sha,
                group=f"{study.org_id}:{blocker.code}",
            )
        )
        written += record is not None
    logger.info(
        "missing inputs recorded for the operators",
        extra={
            "study_id": str(study_id),
            "revision": revision,
            "blockers": [b.code for b in result.blockers],
            "error_code": E.STUDY_INPUT_MISSING,
        },
    )
    return written


__all__ = ["KIND", "event_id", "held_assets", "record_input_blockers", "responsible"]
