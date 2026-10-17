"""``/v1/studies/{id}/photos/{document_id}/objects``: a photo's localized objects.

* ``GET`` (viewer): the active instances of the photo at the study's current revision (or
  ``revision``), its localization outcome and the counted instances per label.
* ``POST`` (engineer, ``Idempotency-Key``): an engineer's manual selection, accepted only
  while the study waits at ``rooms_review`` (409 otherwise), on the current revision. A
  tap (``point``) or a drawn ``box`` with a label is resolved against the stored
  instances of that label with :func:`basis.vision.localize.pick_prompted`: the instance
  under the tap (highest score when several cover it), or the one a box overlaps best,
  accepted only at IoU >= :data:`BOX_MATCH_IOU` (the PASCAL VOC / COCO match criterion;
  ``pick_prompted`` alone takes any overlap, which would swallow a box drawn next to an
  existing instance). A resolved instance that is already counted is returned as is (200,
  ``created: false``); an uncounted candidate becomes a manual instance with its mask and
  score (201, ``derived_from`` the candidate). A box that resolves to nothing is stored as a
  manual box (201, no mask); a tap that resolves to nothing is 422 (draw a box instead).
* ``DELETE .../objects/{object_id}`` (engineer, ``Idempotency-Key``, ``rooms_review``
  only): soft-deletes a manual instance (``deleted_at``/``deleted_by``); a model instance
  is 409 (it is evidence of what the model found; an engineer adds, the model's rows stay).

Who and when: ``created_by``/``created_at`` on a manual row, ``deleted_by``/``deleted_at``
on a deleted one. The study row is read ``FOR SHARE`` in the same transaction, so a
decision that moves the gate cannot commit between the stage check and the write, and
writes to one photo are serialized by a transaction-scoped advisory lock.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Final

from fastapi import Depends, Path, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.errors import Conflict, NotFound, Unprocessable
from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.common import MAX_REVISION
from basis.api.schemas.objects import (
    BoxOut,
    BoxSelection,
    LocalizationOut,
    ManualSelectionIn,
    ManualSelectionOut,
    MaskOut,
    PhotoObjectOut,
    PhotoObjectsOut,
    PointSelection,
)
from basis.api.users import CurrentUserDep, study_access
from basis.db.enums import DocumentKind, OrgRole, Stage, StudyStatus
from basis.db.models import Document, PhotoObject, Study
from basis.db.repositories import DocumentRepository, VisibleStudy
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository, counts
from basis.db.session import SessionDep
from basis.vision.localize import Box, Instance, Point, Rle, box_iou, pick_prompted
from basis.vision.schemas import ComponentLabel

EDIT_ROLE: Final = OrgRole.ENGINEER
EDIT_STAGE: Final = Stage.ROOMS_REVIEW
BOX_MATCH_IOU: Final = 0.5

reads = read_router(tags=["objects"])
writes = write_router(tags=["objects"])

DocumentIdPath = Annotated[uuid.UUID, Path(description="Photo document id.")]
ObjectIdPath = Annotated[uuid.UUID, Path(description="Object id.")]


# --------------------------------------------------------------------------- conversion


def object_out(row: PhotoObject) -> PhotoObjectOut:
    return PhotoObjectOut(
        id=row.id,
        document_id=row.document_id,
        revision=row.revision,
        label=row.label,
        phrase=row.phrase,
        score=row.score,
        box=BoxOut(x1=row.x1, y1=row.y1, x2=row.x2, y2=row.y2),
        mask=MaskOut.model_validate(row.mask) if row.mask is not None else None,
        area=row.area,
        source=row.source,  # type: ignore[arg-type]
        counted=row.counted,
        derived_from=row.derived_from,
        created_by=row.created_by,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        deleted_by=row.deleted_by,
    )


def as_instance(row: PhotoObject) -> Instance:
    """A stored row as a localizer instance (a manual box without a score scores 1)."""
    return Instance(
        label=ComponentLabel(row.label),
        phrase=row.phrase or row.label,
        score=row.score if row.score is not None else 1.0,
        box=Box(x1=row.x1, y1=row.y1, x2=row.x2, y2=row.y2),
        mask=Rle.model_validate(row.mask) if row.mask is not None else None,
        area=row.area,
        source="manual" if row.source == "manual" else "model",
    )


def resolve(
    rows: Sequence[PhotoObject], selection: PointSelection | BoxSelection
) -> PhotoObject | None:
    """The stored instance of the selection's label the tap or box resolves to, if any."""
    same_label = [row for row in rows if row.label == selection.label.value]
    candidates = [as_instance(row) for row in same_label]
    if isinstance(selection, PointSelection):
        points, boxes = [Point(x=selection.point.x, y=selection.point.y)], []
    else:
        points, boxes = [], [drawn_box(selection)]
    picked = pick_prompted(candidates, points, boxes)
    if not picked:
        return None
    [chosen] = picked
    if boxes and box_iou(boxes[0], chosen.box) < BOX_MATCH_IOU:
        return None
    # pick_prompted returns copies marked manual; find the stored row it came from.
    for row, candidate in zip(same_label, candidates, strict=True):
        if candidate.model_copy(update={"source": "manual"}) == chosen:
            return row
    raise AssertionError("pick_prompted returned an instance that is not a candidate")


def drawn_box(selection: BoxSelection) -> Box:
    b = selection.box
    left, right = sorted((b.x1, b.x2))
    top, bottom = sorted((b.y1, b.y2))
    return Box(x1=left, y1=top, x2=right, y2=bottom)


# --------------------------------------------------------------------------- helpers


async def photo_of(session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = await DocumentRepository(session).get(study_id, document_id)
    if document is None or document.kind is not DocumentKind.PHOTO:
        raise NotFound("Photo not found.")
    return document


async def editable_revision(
    session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID
) -> int:
    """The revision a manual edit applies to; 409 unless the study waits at rooms_review.

    Holds the study row ``FOR SHARE`` (a decision's compare-and-set waits for this
    transaction) and the photo's advisory lock until the transaction ends.
    """
    study = (
        await session.scalars(select(Study).where(Study.id == study_id).with_for_update(read=True))
    ).one()
    if study.status is StudyStatus.ARCHIVED:
        raise Conflict("The study is archived.", type_suffix="study-archived")
    if study.current_stage is not EDIT_STAGE:
        raise Conflict(
            f"Objects can be edited only while the study waits at {EDIT_STAGE.value}; it is "
            f"at {study.current_stage.value}.",
            type_suffix="stale-state",
        )
    if study.status not in (StudyStatus.WAITING_REVIEW, StudyStatus.STALLED):
        # At rooms_review but the gate is not open yet: the photos are still being
        # analyzed and localized behind the appraisal review (EVAL_REPORT 26).
        raise Conflict(
            f"The rooms review is not open yet (status {study.status.value}): the photos "
            "are still being analyzed.",
            type_suffix="gate-not-open",
        )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"photo-objects:{document_id}:{study.revision}"},
    )
    return study.revision


# --------------------------------------------------------------------------- routes


@reads.get(
    "/studies/{study_id}/photos/{document_id}/objects",
    summary="A photo's localized objects and counts",
    responses=problems(404),
)
async def list_objects(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))],
    document_id: DocumentIdPath,
    session: SessionDep,
    revision: Annotated[
        int | None,
        Query(ge=1, le=MAX_REVISION, description="Revision; default the study's current one."),
    ] = None,
) -> PhotoObjectsOut:
    study_id = access.study.id
    await photo_of(session, study_id, document_id)
    at = revision if revision is not None else access.study.revision
    repo = PhotoObjectRepository(session)
    rows = await repo.active(study_id, at, [document_id])
    localization = await repo.localization(document_id, at)
    return PhotoObjectsOut(
        document_id=document_id,
        revision=at,
        localization=LocalizationOut.model_validate(localization) if localization else None,
        counts=counts(rows),
        items=[object_out(row) for row in rows],
    )


@writes.post(
    "/studies/{study_id}/photos/{document_id}/objects",
    status_code=status.HTTP_201_CREATED,
    summary="Add an engineer's selection (a tap or a box) at rooms_review",
    description=(
        "Resolved against the photo's stored instances of the label: the instance under "
        "the tap, or the one the box overlaps at IoU >= 0.5. A counted instance is returned "
        "as is (200, `created: false`); an uncounted candidate becomes a manual instance "
        "with its mask (201); a box that resolves to nothing is stored as a manual box "
        "(201); a tap that resolves to nothing is 422. Only while the study waits at "
        "`rooms_review`, on its current revision (409 otherwise)."
    ),
    responses={
        200: {"description": "Resolved to an instance already counted; nothing added."},
        **problems(403, 404),
    },
)
async def add_object(
    body: ManualSelectionIn,
    access: Annotated[VisibleStudy, Depends(study_access(EDIT_ROLE))],
    document_id: DocumentIdPath,
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
) -> ManualSelectionOut:
    study_id = access.study.id
    await photo_of(session, study_id, document_id)
    revision = await editable_revision(session, study_id, document_id)
    repo = PhotoObjectRepository(session)
    rows = await repo.active(study_id, revision, [document_id])
    match = resolve(rows, body)
    if match is not None and match.counted:
        response.status_code = status.HTTP_200_OK
        return ManualSelectionOut(object=object_out(match), created=False)
    if match is not None:
        new = NewObject(
            label=match.label,
            phrase=match.phrase,
            score=match.score,
            x1=match.x1,
            y1=match.y1,
            x2=match.x2,
            y2=match.y2,
            area=match.area,
            mask=match.mask,
            source="manual",
            derived_from=match.id,
            created_by=user.id,
        )
    elif isinstance(body, BoxSelection):
        box = drawn_box(body)
        new = NewObject(
            label=body.label.value,
            x1=box.x1,
            y1=box.y1,
            x2=box.x2,
            y2=box.y2,
            area=box.area,
            source="manual",
            created_by=user.id,
        )
    else:
        raise Unprocessable(
            f"No {body.label.value} instance is under that point; draw a box around it instead.",
            type_suffix="nothing-at-point",
        )
    row = await repo.add(study_id=study_id, document_id=document_id, revision=revision, new=new)
    return ManualSelectionOut(object=object_out(row), created=True)


@writes.delete(
    "/studies/{study_id}/photos/{document_id}/objects/{object_id}",
    summary="Delete a manual instance at rooms_review",
    description=(
        "Soft delete: the row keeps who deleted it and when. Only manual instances (409 for "
        "a model instance), only while the study waits at `rooms_review`, on its current "
        "revision."
    ),
    responses=problems(403, 404),
)
async def delete_object(
    access: Annotated[VisibleStudy, Depends(study_access(EDIT_ROLE))],
    document_id: DocumentIdPath,
    object_id: ObjectIdPath,
    user: CurrentUserDep,
    session: SessionDep,
) -> PhotoObjectOut:
    study_id = access.study.id
    await photo_of(session, study_id, document_id)
    revision = await editable_revision(session, study_id, document_id)
    repo = PhotoObjectRepository(session)
    row = await repo.get(study_id, object_id)
    if (
        row is None
        or row.document_id != document_id
        or row.revision != revision
        or row.deleted_at is not None
    ):
        raise NotFound("Object not found.")
    if row.source != "manual":
        raise Conflict(
            "Only manual instances can be deleted; model instances stay as found.",
            type_suffix="not-manual",
        )
    return object_out(await repo.soft_delete(row, user.id))
