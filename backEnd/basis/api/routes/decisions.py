"""``POST /v1/studies/{id}/decisions``: approve or reject a review stage (S1_SPEC section 5).

One transaction records the review, the corrections, the stage transition
(``DECISION_TRANSITIONS``) and a ``workflow_signal`` outbox row; the workflow is signalled
asynchronously from the outbox, hence 202. A decision for a stage the study is not at, or
for a revision that is not its current one, is 409 (stale). A decision for a gate the
workflow has not opened yet (the study is at the stage but ``processing``: since v0.9 the
photos run behind the appraisal review, so the rooms review opens later; or ``failed``) is
409 ``gate-not-open``; an approval that lands on the next review stage leaves the study
``processing`` until the workflow opens that gate. ``Idempotency-Key`` is
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

Count values (manual add, :mod:`basis.domain.counts`) are checked with the types, before
anything is written: a ``component`` ``count`` and a ``room_component``'s ``after`` (a
component added to the room named by ``entity_id``) that ``apply_corrections`` would refuse
are a 422 located at ``["body", "corrections", <i>, "after"]``.

Two gates for rooms (v0.12, patch ``components-gate-v1``): a study whose workflow has the
components gate (``studies.components_gate``) reviews the grouping at ``rooms_review``
(``room``, ``photo_room``: a photo moved to the room ``after.room_id``, which must be a room
of the decided revision, else 422 at ``after``) and the components at ``components_review``
(``component``, ``room_component``, ``photo``); a study without it keeps the single rooms
review (:func:`basis.workflow.contracts.correction_types`).
"""

from __future__ import annotations

import uuid
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
from basis.domain.counts import count_problem, room_component_problems
from basis.domain.rooms import room_field_problems
from basis.workflow.contracts import correction_types

DECISION_ROLE: Final = OrgRole.ENGINEER
# Pydantic-style error ``type`` values of the two correction checks.
CORRECTION_TYPE_ERROR: Final = "correction_type"
CORRECTION_TARGET_ERROR: Final = "correction_target"
CORRECTION_VALUE_ERROR: Final = "correction_value"
APPROVAL_CORRECTIONS_ERROR: Final = "approval_with_corrections"

writes = write_router(tags=["reviews"])


def _correction_error(index: int, member: str, message: str, kind: str) -> dict[str, Any]:
    return {"loc": ("body", "corrections", index, member), "msg": message, "type": kind}


def check_correction_types(
    stage: str, corrections: Sequence[CorrectionIn], *, components_gate: bool = True
) -> None:
    """422 naming every correction whose type the decided stage does not accept (in the
    study's flow: ``components_gate``)."""
    allowed = correction_types(stage, components_gate=components_gate)
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
    """422 naming every correction whose target is not in the study at ``revision`` (and
    every ``photo_room`` whose ``after.room_id`` is not a room of it)."""
    if not corrections:
        return
    moves = {
        index: uuid.UUID(str(item.after["room_id"]))
        for index, item in enumerate(corrections)
        if item.entity_type == "photo_room"
    }
    missing = await reviews.missing_targets(
        study.study.id,
        revision,
        [(item.entity_type, item.entity_id) for item in corrections]
        + [("room", room_id) for room_id in moves.values()],
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
    ] + [
        _correction_error(
            index,
            "after",
            f"room {room_id} is not in revision {revision} of this study",
            CORRECTION_TARGET_ERROR,
        )
        for index, room_id in moves.items()
        if ("room", room_id) in missing
    ]
    if errors:
        raise RequestValidationError(errors)


def check_correction_values(corrections: Sequence[CorrectionIn]) -> None:
    """422 naming every count the rooms review would refuse (:mod:`basis.domain.counts`): a
    ``component`` ``count`` that is not a whole number from 1, or a ``room_component`` that
    is not a known label with a count (and known material and attachment), and a
    ``photo_room`` that is not exactly a ``room_id`` UUID, and a ``room`` whose ``name``,
    ``room_type`` or ``floor`` it could not store (:mod:`basis.domain.rooms`). Refused here, because
    ``apply_corrections`` refusing one would start the next revision without any of the
    rejection's corrections."""
    errors = []
    for index, item in enumerate(corrections):
        problems: list[str] = []
        if item.entity_type == "component" and "count" in item.after:
            problem = count_problem(item.after["count"])
            problems = [problem] if problem is not None else []
        elif item.entity_type == "room_component":
            problems = room_component_problems(item.after)
        elif item.entity_type == "photo_room" and not _is_move(item.after):
            problems = ["a photo_room correction takes exactly a room_id (a room id)"]
        elif item.entity_type == "room":
            problems = room_field_problems(item.after)
        errors += [
            _correction_error(index, "after", problem, CORRECTION_VALUE_ERROR)
            for problem in problems
        ]
    if errors:
        raise RequestValidationError(errors)


def _is_move(after: dict[str, Any]) -> bool:
    if set(after) != {"room_id"} or not isinstance(after["room_id"], str):
        return False
    try:
        uuid.UUID(after["room_id"])
    except ValueError:
        return False
    return True


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
                    "type": APPROVAL_CORRECTIONS_ERROR,
                }
            ]
        )


@writes.post(
    "/studies/{study_id}/decisions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve or reject the current review stage",
    description=(
        "Records the decision and queues it for the workflow (202). A stage the study is "
        "not at, or a revision that is not its current one, is 409; so is a gate the "
        "workflow has not opened yet (`gate-not-open`: the study is at the stage but "
        "`processing` or `failed`). Each correction's "
        "`entity_type` must be one the stage accepts (appraisal_review: extraction_field; "
        "rooms_review: room, photo_room; components_review: component, room_component, "
        "photo; assets_review: asset; a study whose `components_gate` is false has the single "
        "rooms_review: room, component, room_component, photo) and "
        "its `entity_id` must exist in the study at the decided revision (a photo or "
        "photo_room: a photo document of the study; a room_component: the room it adds a "
        "component to); a `photo_room` `after` is exactly a `room_id` of that revision; a "
        "`component` `count` must be a whole number from 1, and a `room_component` "
        "`after` a taxonomy `label` with a `count` (optional `material`, `attachment` from "
        "their vocabularies); otherwise 422 with one `errors` entry per offending "
        'correction, located at `["body", "corrections", <index>, "entity_type" | '
        '"entity_id" | "after"]`.'
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
    check_correction_types(
        body.stage, body.corrections, components_gate=access.study.components_gate is True
    )
    check_no_corrections_on_approve(body.decision, body.corrections)
    check_correction_values(body.corrections)
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
