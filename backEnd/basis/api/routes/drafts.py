"""``GET``/``PUT``/``DELETE /v1/studies/{id}/drafts/{stage}``: an engineer's pending
corrections at a review gate, kept on the server (docs/api/GATE_DRAFTS_AND_RESUME.md).

Before this route the corrections an engineer lined up at a gate lived in the browser until
``POST .../decisions`` sent them (``web/live/live-review.js``): a reload or a switch of study
lost them. A draft is one document per ``(study, stage, user)`` (``review_drafts``,
:mod:`basis.db.repositories.review_draft`): the ``comment`` and ``corrections`` of a
:class:`~basis.api.schemas.drafts.DraftIn`, saved whole by every ``PUT``.

* **Validated as a decision would be.** The body is the decision route's own
  ``CorrectionIn``, and the ``PUT`` runs the decision route's checks
  (:mod:`basis.api.routes.decisions`: the type for the stage in the study's flow, the target
  in the named revision, count and room values, removals and added fields), so a draft that
  would be refused at submit time is refused at save time with the same 422 ``errors``. The
  one decision-only rule, "an approval cannot carry corrections", does not apply: the
  decision is chosen at submit time (a draft with corrections is sent as a rejection).
* **Per user.** Two engineers draft apart; ``GET`` lists the others' drafts on the stage
  (who, since when, how many corrections; not their content), so a reviewer knows a
  colleague is mid-way.
* **Optimistic concurrency, as the intake's** (:mod:`basis.api.preconditions`): the draft's
  ``version`` is its strong entity tag. ``GET`` answers ``ETag: "<version>"`` (``"0"`` while
  the caller has none); ``PUT`` and ``DELETE`` take ``If-Match`` and answer 412
  ``precondition-failed`` with the current ``ETag`` when it does not hold, nothing written.
  Without ``If-Match`` the write is accepted (last save wins, within one engineer's own
  drafts). The ``ETag`` is the caller's draft's version only: the others' list is not part
  of it, and the ``GET`` is ``Cache-Control: no-store`` so no cache ever serves it.
* **Stale drafts.** A draft is saved against the study's current ``revision`` (409
  ``draft-stale`` for another, or for a stage already decided at that revision). ``GET``
  still returns a draft whose revision the study has left, or whose stage was decided at that
  revision, but with ``stale: true`` and the reason, never as current: its corrections name
  rows that are no longer the ones under review. The client shows it and lets the engineer
  carry what still applies or discard it.
* **Submission.** ``POST .../decisions`` deletes the submitting user's draft on the stage in
  the decision's transaction (``review.decided`` records ``draft_discarded``).

No timeline event is recorded for a draft (:mod:`basis.db.repositories.study_event`): the
timeline is the record of what happened to the study and the audit trail of the engineer
input that changed it; a draft changes nothing (its corrections enter the trail as
``correction.recorded`` when the decision is submitted), it is saved on every edit (an
``append-only`` row per keystroke would bury the trail), and it is deleted when submitted or
discarded. Who is drafting since when is on the row itself (``user_id``, ``updated_at``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.errors import Conflict, NotFound, PreconditionFailed
from basis.api.preconditions import ETAG_HEADER, intake_etag, parse_if_match
from basis.api.routes.common import problems, read_router, write_router
from basis.api.routes.decisions import (
    check_correction_targets,
    check_correction_types,
    check_correction_values,
    check_field_add_targets,
    check_removal_targets,
    check_removals_and_additions,
)
from basis.api.schemas.drafts import DraftIn, DraftOut, DraftsOut, OtherDraftOut, StaleReason
from basis.api.schemas.reviews import CorrectionIn, ReviewStage
from basis.api.users import CurrentUserDep, study_access
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.errors import NotVisibleError, PreconditionFailedError
from basis.db.models import ReviewDraft, Study
from basis.db.repositories import ReviewDraftRepository, ReviewRepository, VisibleStudy
from basis.db.session import SessionDep
from basis.errors.codes import E

DRAFT_ROLE: Final = OrgRole.ENGINEER
NO_STORE: Final = "no-store"
COMMENT: Final = "comment"
CORRECTIONS: Final = "corrections"

reads = read_router(tags=["drafts"])
writes = write_router(tags=["drafts"])

StagePath = Annotated[
    ReviewStage,
    Path(description="The review stage the draft is for (a review stage only)."),
]
IfMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-Match",
        description='The draft\'s entity tag from `GET .../drafts/{stage}` (`"3"`; `"0"` to '
        "create only while you have none), or `*` (only over an existing draft). Optional.",
    ),
]
ETAG_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "headers": {
            ETAG_HEADER: {
                "description": 'Your draft\'s strong entity tag, its version in quotes ("3"; '
                '"0" while you have none).',
                "schema": {"type": "string"},
            }
        }
    }
}


def document_of(body: DraftIn) -> dict[str, Any]:
    """The stored JSON document: the comment and the corrections as validated."""
    return {
        COMMENT: body.comment,
        CORRECTIONS: [item.model_dump(mode="json") for item in body.corrections],
    }


def corrections_of(draft: ReviewDraft) -> list[CorrectionIn]:
    return [CorrectionIn.model_validate(item) for item in draft.document.get(CORRECTIONS, [])]


async def stale_reason(
    drafts: ReviewDraftRepository, study: Study, draft: ReviewDraft
) -> StaleReason | None:
    if draft.revision != study.revision:
        return "revision"
    if await drafts.is_decided(study.id, draft.stage, draft.revision):
        return "decided"
    return None


async def drafts_out(
    session: AsyncSession, study: Study, stage: Stage, user_id: uuid.UUID, response: Response
) -> DraftsOut:
    """The caller's draft and the others' on the stage, with the ``ETag`` and no caching."""
    drafts = ReviewDraftRepository(session)
    own = await drafts.get(study.id, stage, user_id)
    draft_out: DraftOut | None = None
    if own is not None:
        reason = await stale_reason(drafts, study, own)
        draft_out = DraftOut(
            study_id=own.study_id,
            stage=own.stage,
            revision=own.revision,
            user_id=own.user_id,
            comment=own.document.get(COMMENT),
            corrections=corrections_of(own),
            version=own.version,
            stale=reason is not None,
            stale_reason=reason,
            created_at=own.created_at,
            updated_at=own.updated_at,
        )
    others = []
    for other in await drafts.others(study.id, stage, user_id):
        reason = await stale_reason(drafts, study, other.draft)
        others.append(
            OtherDraftOut(
                user_id=other.user.id,
                display_name=other.user.display_name,
                email=other.user.email,
                revision=other.draft.revision,
                corrections=len(other.draft.document.get(CORRECTIONS, [])),
                has_comment=bool(other.draft.document.get(COMMENT)),
                stale=reason is not None,
                updated_at=other.draft.updated_at,
            )
        )
    response.headers[ETAG_HEADER] = intake_etag(0 if own is None else own.version)
    response.headers["Cache-Control"] = NO_STORE
    return DraftsOut(
        study_id=study.id,
        stage=stage,
        current_revision=study.revision,
        draft=draft_out,
        others=others,
    )


def precondition_failed(exc: PreconditionFailedError, *, any_current: bool) -> PreconditionFailed:
    current = intake_etag(exc.version)
    if any_current and exc.version == 0:
        detail = (
            "If-Match: * needs an existing draft and you have none on this stage; send the "
            'request without If-Match (or with "0") to create one.'
        )
    else:
        detail = (
            f"Your draft on this stage changed since you read it (its ETag is now {current}); "
            "nothing was written. Read it again with GET /v1/studies/{id}/drafts/{stage}, merge "
            "your change, and send the new ETag as If-Match."
        )
    return PreconditionFailed(
        detail, headers={ETAG_HEADER: current}, code=E.STUDY_DRAFT_PRECONDITION_FAILED
    )


@reads.get(
    "/studies/{study_id}/drafts/{stage}",
    summary="Your pending corrections for a review stage, and whose other drafts exist",
    description="Engineer role. `draft` is your saved draft on the stage (null when none), with "
    "`stale` and `stale_reason` when the study has moved to another revision or the stage was "
    "decided at the draft's revision (its corrections name rows no longer under review: carry "
    "what still applies, or `DELETE` it). `others` lists the other engineers' drafts on the "
    'stage (who, since when, how many corrections). `ETag: "<version>"` is your draft\'s '
    'version (`"0"` while none): send it as `If-Match` on `PUT`/`DELETE`. `Cache-Control: '
    "no-store` (docs/api/GATE_DRAFTS_AND_RESUME.md).",
    responses={**problems(403, 404), **ETAG_RESPONSE},
)
async def get_draft(
    stage: StagePath,
    access: Annotated[VisibleStudy, Depends(study_access(DRAFT_ROLE))],
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
) -> DraftsOut:
    return await drafts_out(session, access.study, Stage(stage), user.id, response)


@writes.put(
    "/studies/{study_id}/drafts/{stage}",
    summary="Save your pending corrections for a review stage (engineer)",
    description="The whole draft each time (`revision`, `comment`, `corrections`), checked exactly "
    "as `POST .../decisions` checks a decision's corrections: `entity_type` for the stage in "
    "the study's flow, `entity_id` in the named revision, values, required reasons, removals "
    "and added fields (422 with one `errors` entry per offending correction, the same "
    "`type` values). `revision` must be the study's current one and the stage undecided at it, "
    "else 409 `draft-stale`. No `Idempotency-Key` is needed. Optimistic concurrency as on the "
    'intake: with `If-Match` (the `ETag` of the `GET`; `"0"` creates only while you have none) '
    "the write happens only while that tag is current, else 412 `precondition-failed` with the "
    "current `ETag`, nothing written; without it the save is accepted. Every save increments "
    "the version; the response carries the new `ETag`. A draft is yours alone: another "
    "engineer's draft on the stage is separate. 409 `study-archived` on an archived study.",
    responses={**problems(403, 404, 409, 412), **ETAG_RESPONSE},
)
async def put_draft(
    stage: StagePath,
    body: DraftIn,
    access: Annotated[VisibleStudy, Depends(study_access(DRAFT_ROLE))],
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> DraftsOut:
    study = access.study
    if study.status is StudyStatus.ARCHIVED:
        raise Conflict("The study is archived.", type_suffix="study-archived")
    review_stage = Stage(stage)
    # The checks that need no database, in the decision route's order.
    check_correction_types(stage, body.corrections, components_gate=study.components_gate is True)
    check_correction_values(body.corrections)
    check_removals_and_additions(body.corrections)
    drafts = ReviewDraftRepository(session)
    if body.revision != study.revision:
        raise Conflict(
            f"The draft names revision {body.revision} but the study is at revision "
            f"{study.revision}; its corrections would name rows no longer under review. Reload "
            "the gate and draft against the current revision.",
            type_suffix="draft-stale",
            code=E.STUDY_DRAFT_STALE,
        )
    if await drafts.is_decided(study.id, review_stage, body.revision):
        raise Conflict(
            f"{stage} of this study is already decided at revision {body.revision}; a draft "
            "for it could never be submitted.",
            type_suffix="draft-stale",
            code=E.STUDY_DRAFT_STALE,
        )
    # The target checks, against the revision the draft names (as the decision's run against
    # the revision the study is verified to be at).
    reviews = ReviewRepository(session)
    await check_correction_targets(reviews, access, body.revision, body.corrections)
    await check_removal_targets(session, study.id, body.revision, body.corrections)
    await check_field_add_targets(session, study.id, body.corrections)
    condition = parse_if_match(if_match)
    try:
        await drafts.write(
            study.id,
            review_stage,
            user.id,
            revision=body.revision,
            document=document_of(body),
            precondition=None if condition is None else condition.allows,
        )
    except PreconditionFailedError as exc:
        raise precondition_failed(
            exc, any_current=condition is not None and condition.any_current
        ) from exc
    return await drafts_out(session, study, review_stage, user.id, response)


@writes.delete(
    "/studies/{study_id}/drafts/{stage}",
    summary="Discard your pending corrections for a review stage (engineer)",
    description="Deletes your draft on the stage (404 when you have none) and answers the "
    "stage's remaining drafts (yours null). `If-Match` as on `PUT`: the delete happens only "
    "while the tag is current (412 otherwise, nothing deleted). Another engineer's draft is "
    "never touched. Submitting a decision deletes your draft on its stage by itself.",
    responses={**problems(403, 404, 412), **ETAG_RESPONSE},
)
async def delete_draft(
    stage: StagePath,
    access: Annotated[VisibleStudy, Depends(study_access(DRAFT_ROLE))],
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> DraftsOut:
    review_stage = Stage(stage)
    condition = parse_if_match(if_match)
    try:
        await ReviewDraftRepository(session).discard(
            access.study.id,
            review_stage,
            user.id,
            precondition=None if condition is None else condition.allows,
        )
    except NotVisibleError:
        raise NotFound(
            "You have no draft on this stage of the study.", code=E.STUDY_DRAFT_NOT_FOUND
        ) from None
    except PreconditionFailedError as exc:
        raise precondition_failed(
            exc, any_current=condition is not None and condition.any_current
        ) from exc
    return await drafts_out(session, access.study, review_stage, user.id, response)
