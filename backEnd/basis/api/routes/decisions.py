"""``POST /v1/studies/{id}/decisions``: approve or reject a review stage (S1_SPEC section 5).

One transaction records the review, the corrections, the stage transition
(``DECISION_TRANSITIONS``) and a ``workflow_signal`` outbox row; the workflow is signalled
asynchronously from the outbox, hence 202. A decision for a stage the study is not at, or
for a revision that is not its current one, is 409 (stale). ``Idempotency-Key`` is
required: a retry with the same key and body replays the 202, and the key is also stored
on the review row as a second guard.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Request, status

from basis.api.idempotency import IDEMPOTENCY_HEADER, parse_idempotency_key
from basis.api.routes.common import problems, write_router
from basis.api.schemas.reviews import DecisionAccepted, DecisionIn, ReviewOut
from basis.api.users import CurrentUserDep, study_access
from basis.db.enums import OrgRole, Stage
from basis.db.repositories import CorrectionInput, ReviewRepository, VisibleStudy
from basis.db.session import SessionDep

DECISION_ROLE: Final = OrgRole.ENGINEER

writes = write_router(tags=["reviews"])


@writes.post(
    "/studies/{study_id}/decisions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve or reject the current review stage",
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
    record = await ReviewRepository(session).record_decision(
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
    return DecisionAccepted(
        review=ReviewOut.model_validate(record.review),
        current_stage=record.study.current_stage,
        revision=record.study.revision,
        corrections_recorded=len(record.corrections),
    )
