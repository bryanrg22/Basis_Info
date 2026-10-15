"""``POST /v1/studies/{id}/decisions``: approve or reject a review stage (S1_SPEC section 5).

One transaction records the review, the corrections, the stage transition
(``DECISION_TRANSITIONS``) and a ``workflow_signal`` outbox row; the workflow is signalled
asynchronously from the outbox, hence 202. A decision for a stage the study is not at, or
for a revision that is not its current one, is 409 (stale). ``Idempotency-Key`` is
required: a retry with the same key and body replays the 202, and the key is also stored
on the review row as a second guard.

Correction targets are checked here, so the API does not accept a correction that the
workflow's update validator would refuse or that ``apply_corrections`` would drop: each
correction's ``entity_type`` must be one the decided stage accepts
(``basis.workflow.contracts.CORRECTION_TYPES``), and its ``entity_id`` must exist in the
study at the decided revision (``ReviewRepository.missing_targets``). Either failure is a
422 validation problem whose ``errors`` name the offending index
(``["body", "corrections", <i>, "entity_type" | "entity_id"]``). The type check runs
before anything is written. The existence check runs after the compare-and-set
transition, inside the same transaction, so a stale decision is still a 409 and the
targets are checked against the revision the study is verified to be at; a failure rolls
the whole transaction back and, like every validation failure, is not stored under the
idempotency key.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Final

from fastapi import Depends, Request, status
from fastapi.exceptions import RequestValidationError

from basis.api.idempotency import IDEMPOTENCY_HEADER, parse_idempotency_key
from basis.api.routes.common import problems, write_router
from basis.api.schemas.reviews import CorrectionIn, DecisionAccepted, DecisionIn, ReviewOut
from basis.api.users import CurrentUserDep, study_access
from basis.db.enums import OrgRole, Stage
from basis.db.repositories import CorrectionInput, ReviewRepository, VisibleStudy
from basis.db.session import SessionDep
from basis.workflow.contracts import CORRECTION_TYPES

DECISION_ROLE: Final = OrgRole.ENGINEER
# Pydantic-style error ``type`` values of the two correction checks.
CORRECTION_TYPE_ERROR: Final = "correction_type"
CORRECTION_TARGET_ERROR: Final = "correction_target"

writes = write_router(tags=["reviews"])


def _correction_error(index: int, member: str, message: str, kind: str) -> dict[str, Any]:
    return {"loc": ("body", "corrections", index, member), "msg": message, "type": kind}


def check_correction_types(stage: str, corrections: Sequence[CorrectionIn]) -> None:
    """422 naming every correction whose type the decided stage does not accept."""
    allowed = CORRECTION_TYPES[stage]
    errors = [
        _correction_error(
            index,
            "entity_type",
            f"{stage} does not accept {item.entity_type!r} corrections "
            f"(allowed: {', '.join(sorted(allowed))})",
            CORRECTION_TYPE_ERROR,
        )
        for index, item in enumerate(corrections)
        if item.entity_type not in allowed
    ]
    if errors:
        raise RequestValidationError(errors)


async def check_correction_targets(
    reviews: ReviewRepository,
    study: VisibleStudy,
    revision: int,
    corrections: Sequence[CorrectionIn],
) -> None:
    """422 naming every correction whose target is not in the study at ``revision``."""
    if not corrections:
        return
    missing = await reviews.missing_targets(
        study.study.id, revision, [(item.entity_type, item.entity_id) for item in corrections]
    )
    errors = [
        _correction_error(
            index,
            "entity_id",
            f"{item.entity_type} {item.entity_id} is not in revision {revision} of this study",
            CORRECTION_TARGET_ERROR,
        )
        for index, item in enumerate(corrections)
        if (item.entity_type, item.entity_id) in missing
    ]
    if errors:
        raise RequestValidationError(errors)


def check_no_corrections_on_approve(decision: str, corrections: Sequence[CorrectionIn]) -> None:
    """422: an approval cannot carry corrections.

    The workflow's update validator refuses such a decision, and because a validator
    refusal is final for the outbox relay, the decision would be recorded by the API but
    never applied. Reject with corrections instead.
    """
    if decision == "approve" and corrections:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", "corrections"),
                    "msg": "an approval cannot carry corrections; reject with corrections",
                    "type": CORRECTION_TYPE_ERROR,
                }
            ]
        )


@writes.post(
    "/studies/{study_id}/decisions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve or reject the current review stage",
    description=(
        "Records the decision and queues it for the workflow (202). A stage the study is "
        "not at, or a revision that is not its current one, is 409. Each correction's "
        "`entity_type` must be one the stage accepts (appraisal_review: extraction_field; "
        "rooms_review: room, component, photo; assets_review: asset) and its `entity_id` "
        "must exist in the study at the decided revision (a photo: a photo document of the "
        "study); otherwise 422 with one `errors` entry per offending correction, located at "
        '`["body", "corrections", <index>, "entity_type" | "entity_id"]`.'
    ),
    responses=problems(403, 404),
)
async def record_decision(
    body: DecisionIn,
    request: Request,
    access: Annotated[VisibleStudy, Depends(study_access(DECISION_ROLE))],
    user: CurrentUserDep,
    session: SessionDep,
) -> DecisionAccepted:
    # Already validated by the idempotency dependency; read again for the review row.
    key = parse_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER))
    check_correction_types(body.stage, body.corrections)
    check_no_corrections_on_approve(body.decision, body.corrections)
    reviews = ReviewRepository(session)
    record = await reviews.record_decision(
        study_id=access.study.id,
        stage=Stage(body.stage),
        revision=body.revision,
        decision=body.decision,
        reviewer_id=user.id,
        idempotency_key=key,
        comment=body.comment,
        corrections=[
            CorrectionInput(
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                before=item.before,
                after=item.after,
                reason=item.reason,
            )
            for item in body.corrections
        ],
    )
    await check_correction_targets(reviews, access, body.revision, body.corrections)
    return DecisionAccepted(
        review=ReviewOut.model_validate(record.review),
        current_stage=record.study.current_stage,
        revision=record.study.revision,
        corrections_recorded=len(record.corrections),
    )
