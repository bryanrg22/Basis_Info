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

Removing a component row (``component_remove``, wherever ``component`` is accepted;
:mod:`basis.domain.removals`): ``entity_id`` is a component of the decided revision (a
reconciled inventory row, an engineer's added row or a photo's row), ``after`` is exactly
``{}`` and ``reason`` is required. Refused with a 422 naming the correction: a blank or
missing reason (``["body", "corrections", <i>, "reason"]``, ``correction_reason``), fields in
``after`` (``correction_value``), the same row removed twice or removed and corrected
(``correction_duplicate``), a row the rejection rebuilds anyway (an inventory row of a room
with a photo flagged for re-analysis in the same decision, or of a merged room, or a flagged
photo's own row: ``reconcile_rooms`` rebuilds those and corrections of rebuilt rows are not
carried; ``correction_rebuilt``), and the last inventory row of a reconciled room
(``correction_last_inventory``). It never touches a photo's detected objects
(``photo_objects``): deleting one of those is ``DELETE
/v1/studies/{id}/photos/{document_id}/objects/{object_id}``.

Adding an appraisal field (``extraction_field_add`` at the appraisal review;
:mod:`basis.extraction.added_fields`): ``entity_id`` is the extraction of the decided
revision, ``after`` a catalogue ``key``, exactly one value of the key's type, a ``page`` and
the printed ``evidence``; ``reason`` is required. A bad ``after`` is ``correction_value``
(a page past the PDF's page count included), a key the extraction already has
``correction_field_exists`` (correct it with ``extraction_field``), the same key twice
``correction_duplicate``.

The checks that need no database run with the types, before anything is written; the
rebuilt, last-row, existing-key and page checks run after the target check, in the same
transaction, so a stale decision is still a 409.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Final

from fastapi import Depends, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.activities.reprice_corrections import STUDY_TARGETED
from basis.api.idempotency import IDEMPOTENCY_HEADER, parse_idempotency_key
from basis.api.routes.common import problems, write_router
from basis.api.schemas.reviews import CorrectionIn, DecisionAccepted, DecisionIn, ReviewOut
from basis.api.users import CurrentUserDep, study_access
from basis.db.enums import OrgRole, Stage
from basis.db.repositories import CorrectionInput, ReviewRepository, VisibleStudy
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.room import RoomRepository
from basis.db.session import SessionDep
from basis.domain import removals
from basis.domain.counts import count_problem, room_component_problems
from basis.domain.rooms import room_field_problems
from basis.errors.codes import E
from basis.extraction import added_fields
from basis.workflow.contracts import correction_types

DECISION_ROLE: Final = OrgRole.ENGINEER
# Pydantic-style error ``type`` values of the two correction checks.
CORRECTION_TYPE_ERROR: Final = "correction_type"
CORRECTION_TARGET_ERROR: Final = "correction_target"
CORRECTION_VALUE_ERROR: Final = "correction_value"
APPROVAL_CORRECTIONS_ERROR: Final = "approval_with_corrections"
CORRECTION_REASON_ERROR: Final = "correction_reason"
CORRECTION_DUPLICATE_ERROR: Final = "correction_duplicate"
CORRECTION_REBUILT_ERROR: Final = "correction_rebuilt"
CORRECTION_LAST_INVENTORY_ERROR: Final = "correction_last_inventory"
CORRECTION_FIELD_EXISTS_ERROR: Final = "correction_field_exists"
FIELD_ADD: Final = "extraction_field_add"
# The correction types that remove or add a row: engineer evidence, so a reason is required.
REASON_REQUIRED: Final = frozenset({removals.COMPONENT_REMOVE, FIELD_ADD})

writes = write_router(tags=["reviews"])


def _correction_error(index: int, member: str, message: str, kind: str) -> dict[str, Any]:
    return {"loc": ("body", "corrections", index, member), "msg": message, "type": kind}


def check_correction_types(
    stage: str, corrections: Sequence[CorrectionIn], *, components_gate: bool = True
) -> None:
    """422 naming every correction whose type the decided stage does not accept (in the
    study's flow: ``components_gate``)."""
    # The assets rebuild (``reprice``, ``structure_model``) is recorded by
    # ``POST .../assets/reprice`` only, after its impact preview and fingerprint check.
    allowed = correction_types(stage, components_gate=components_gate) - STUDY_TARGETED
    errors = [
        _correction_error(
            index,
            "entity_type",
            f"{item.entity_type!r} corrections are requested with POST "
            "/v1/studies/{id}/assets/reprice (after its preview)"
            if item.entity_type in STUDY_TARGETED
            else f"{stage} does not accept {item.entity_type!r} corrections "
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


def check_removals_and_additions(corrections: Sequence[CorrectionIn]) -> None:
    """422 naming every ``component_remove`` and ``extraction_field_add`` the workflow would
    refuse on its face (module doc): a missing reason, a bad ``after``, a duplicate. Needs
    no database, so it runs before anything is written."""
    errors: list[dict[str, Any]] = []
    removed: set[uuid.UUID] = set()
    corrected = {item.entity_id for item in corrections if item.entity_type == "component"}
    added: set[tuple[uuid.UUID, str]] = set()
    for index, item in enumerate(corrections):
        if item.entity_type not in REASON_REQUIRED:
            continue
        if item.reason is None or not item.reason.strip():
            errors.append(
                _correction_error(
                    index,
                    "reason",
                    f"a {item.entity_type} correction needs a reason (it removes or adds a row)",
                    CORRECTION_REASON_ERROR,
                )
            )
        if item.entity_type == removals.COMPONENT_REMOVE:
            if item.after:
                errors.append(
                    _correction_error(
                        index,
                        "after",
                        "a component_remove correction takes no fields (after must be {})",
                        CORRECTION_VALUE_ERROR,
                    )
                )
            if item.entity_id in removed or item.entity_id in corrected:
                errors.append(
                    _correction_error(
                        index,
                        "entity_id",
                        f"component {item.entity_id} is already removed or corrected by this "
                        "decision; send one correction per row",
                        CORRECTION_DUPLICATE_ERROR,
                    )
                )
            removed.add(item.entity_id)
            continue
        problems = added_fields.problems(item.after)
        errors += [
            _correction_error(index, "after", problem.message, CORRECTION_VALUE_ERROR)
            for problem in problems
        ]
        key = item.after.get(added_fields.KEY)
        if not problems and isinstance(key, str):
            if (item.entity_id, key) in added:
                errors.append(
                    _correction_error(
                        index,
                        "after",
                        f"{key} is already added to extraction {item.entity_id} by this decision",
                        CORRECTION_DUPLICATE_ERROR,
                    )
                )
            added.add((item.entity_id, key))
    if errors:
        raise RequestValidationError(errors)


async def check_removal_targets(
    session: AsyncSession, study_id: uuid.UUID, revision: int, corrections: Sequence[CorrectionIn]
) -> None:
    """422 naming every ``component_remove`` that the rejection would undo
    (:mod:`basis.domain.removals`): a row it rebuilds (an inventory row of a room with a photo
    flagged by this decision or of a merged room, a flagged photo's row), and the last
    inventory row of a reconciled room. Runs after the targets are known to exist."""
    indexed = [
        (index, item)
        for index, item in enumerate(corrections)
        if item.entity_type == removals.COMPONENT_REMOVE
    ]
    if not indexed:
        return
    repo = RoomRepository(session)
    rows = {c.id: removals.as_row(c) for c in await repo.components(study_id, revision)}
    assignments = await repo.photo_rooms(study_id, revision)
    room_of_photo = {pr.document_id: pr.room_id for pr in assignments}
    flagged = {item.entity_id for item in corrections if item.entity_type == "photo"}
    merged: set[uuid.UUID] = set()
    for item in corrections:
        if item.entity_type == "room" and "merge_into" in item.after:
            merged.add(item.entity_id)
            try:
                merged.add(uuid.UUID(str(item.after["merge_into"])))
            except ValueError:
                pass  # apply_corrections refuses the merge itself
    rebuilt = removals.rebuilt_rooms(flagged, room_of_photo, merged)
    emptied = removals.emptied_rooms(rows.values(), {item.entity_id for _, item in indexed})
    errors: list[dict[str, Any]] = []
    for index, item in indexed:
        row = rows.get(item.entity_id)
        if row is None:  # pragma: no cover - the target check ran first
            continue
        problem = removals.rebuilt_problem(row, flagged_photos=flagged, rebuilt=rebuilt)
        if problem is not None:
            errors.append(_correction_error(index, "entity_id", problem, CORRECTION_REBUILT_ERROR))
        elif row.room_id in emptied:
            errors.append(
                _correction_error(
                    index,
                    "entity_id",
                    removals.emptied_message(row),
                    CORRECTION_LAST_INVENTORY_ERROR,
                )
            )
    if errors:
        raise RequestValidationError(errors)


async def check_field_add_targets(
    session: AsyncSession, study_id: uuid.UUID, corrections: Sequence[CorrectionIn]
) -> None:
    """422 naming every ``extraction_field_add`` whose key the extraction already has, or
    whose page is past the appraisal PDF's page count when the extraction recorded it
    (:func:`basis.extraction.added_fields.target_problems`). Runs after the target check."""
    extractions = ExtractionRepository(session)
    keys: dict[uuid.UUID, set[str]] = {}
    errors: list[dict[str, Any]] = []
    for index, item in enumerate(corrections):
        if item.entity_type != FIELD_ADD:
            continue
        extraction = await extractions.get(study_id, item.entity_id)
        if extraction is None:  # pragma: no cover - the target check ran first
            continue
        if extraction.id not in keys:
            keys[extraction.id] = {
                f"{f.section}.{f.field}" for f in await extractions.fields(extraction.id)
            }
        added = added_fields.parse(item.after)
        for problem in added_fields.target_problems(
            added.key,
            added.page,
            existing_keys=keys[extraction.id],
            page_count=added_fields.page_count(extraction.raw),
        ):
            kind = (
                CORRECTION_FIELD_EXISTS_ERROR
                if problem.code == E.CORRECT_FIELD_ADD_EXISTS
                else CORRECTION_VALUE_ERROR
            )
            errors.append(_correction_error(index, "after", problem.message, kind))
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
        "`entity_type` must be one the stage accepts (appraisal_review: extraction_field, "
        "extraction_field_add; rooms_review: room, photo_room; components_review: component, "
        "component_remove, room_component, photo; assets_review: asset; a study whose "
        "`components_gate` is false has the single rooms_review: room, component, "
        "component_remove, room_component, photo) and "
        "its `entity_id` must exist in the study at the decided revision (a photo or "
        "photo_room: a photo document of the study; a room_component: the room it adds a "
        "component to); a `photo_room` `after` is exactly a `room_id` of that revision; a "
        "`component` `count` must be a whole number from 1, and a `room_component` "
        "`after` a taxonomy `label` with a `count` (optional `material`, `attachment` from "
        "their vocabularies); otherwise 422 with one `errors` entry per offending "
        'correction, located at `["body", "corrections", <index>, "entity_type" | '
        '"entity_id" | "after" | "reason"]`. '
        "A `component_remove` (an erroneous room inventory component row: `entity_id` a "
        "component of the revision, `after` exactly `{}`, `reason` required) leaves the row "
        "out of the next revision; refused: the same row removed twice or also corrected "
        "(`correction_duplicate`), a row the rejection rebuilds anyway (an inventory row of a "
        "room whose photo the same decision flags for re-analysis, or of a merged room, or a "
        "flagged photo's row: `correction_rebuilt`), a room's last inventory row "
        "(`correction_last_inventory`). It never deletes a photo's detected object (that is "
        "`DELETE .../photos/{document_id}/objects/{object_id}`). An `extraction_field_add` "
        "(an appraisal field the extraction missed: `entity_id` the extraction, `after` "
        "`{key, value_text | value_num | value_date, page, evidence}`, `reason` required) "
        "is refused for an unknown key, a value of the wrong type, a page past the PDF "
        "(`correction_value`), a key the extraction has (`correction_field_exists`: correct "
        "it with `extraction_field`) or a key added twice (`correction_duplicate`); a missing "
        "reason is `correction_reason` at `reason`."
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
    check_removals_and_additions(body.corrections)
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
    await check_removal_targets(session, access.study.id, body.revision, body.corrections)
    await check_field_add_targets(session, access.study.id, body.corrections)
    return DecisionAccepted(
        review=ReviewOut.model_validate(record.review),
        current_stage=record.study.current_stage,
        revision=record.study.revision,
        corrections_recorded=len(record.corrections),
    )
