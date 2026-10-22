"""``/v1/studies/{id}/photos/{document_id}/objects``: a photo's localized objects.

* ``GET`` (viewer): the active instances of the photo at the study's current revision (or
  ``revision``), counted and uncounted candidates alike (each with its mask, so the review
  can preview the candidates under a tap), its localization outcome and the counted
  instances per label.
* ``POST`` (engineer, ``Idempotency-Key``): an engineer's selection, accepted only while
  the study waits at ``rooms_review`` (409 otherwise), on the current revision, once the
  photo's localization is recorded. What a selection becomes is decided by
  :mod:`basis.vision.selection` (measured there):

  - a drawn ``box`` with a label: the stored instance of the label it overlaps at box IoU
    >= 0.5; otherwise SAM 3 is prompted with the label's phrases and the box
    (:meth:`~basis.vision.localize.Localizer.segment_prompt`) and the returned instance
    overlapping the box best at IoU >= 0.5 with score >= 0.5 is stored with its mask
    (``mask_source: "sam_box_prompt"``); otherwise the box itself (``"none"``, with
    ``mask_reason``);
  - a tap (``point``) with a label: the stored instance under it (highest score); nothing
    under it is 422 (draw a box instead);
  - a ``candidate`` by id: that stored instance.

  An uncounted candidate becomes a manual instance with its mask, score and label (201,
  ``derived_from``, ``mask_source: "candidate"``). Before anything is stored, the duplicate
  guard: a selection that is, or covers at intersection over the smaller >= 0.5, a counted
  instance of its label is that instance (200, ``created: false``, ``duplicate_of``)
  unless ``allow_duplicate`` (then counted instances are also skipped when resolving).
* ``DELETE .../objects/{object_id}`` (engineer, ``Idempotency-Key``, ``rooms_review``
  only): soft-deletes a manual instance (``deleted_at``/``deleted_by``); a model instance
  is 409 (it is evidence of what the model found; an engineer adds, the model's rows stay).

Who and when: ``created_by``/``created_at`` on a manual row, ``deleted_by``/``deleted_at``
on a deleted one.

Transactions of a ``POST``. The SAM 3 call takes seconds (a cold or throttled model up to
``OBJECT_PROMPT_TIMEOUT_S``), so it runs with no transaction open and no connection held
(EVAL_REPORT section 32: every connection-holding wait was a pool deadlock under load):

1. read (no locks): access, the photo, the stage, the stored instances; decide whether
   the selection needs SAM 3 (a box that matches no stored instance);
2. if so, the call, bounded by ``OBJECT_PROMPT_TIMEOUT_S``, then its record
   (``object_prompts``: phrases, box, outcome, model version, predict seconds and cost,
   as ``localize_photo`` records a photo's call) in its own short transaction, so a billed
   call is recorded even if the add is then refused;
3. write: the study row ``FOR SHARE`` (a decision that moves the gate cannot commit
   between the check and the write) and the photo's transaction-scoped advisory lock
   (writes to one photo are serialized); the stage and revision checked again (409 if a
   decision moved the study meanwhile); the selection resolved again against the rows as
   they are now (another engineer's add may have landed: the call's instance is then
   still guarded against it), and the row written.

A failed, timed-out or unconfigured call does not fail the add: the box is stored without
an outline and ``mask_reason`` says why. The engineer's box is the evidence they meant to
record; refusing it would make an add depend on a model host's availability, and the
reason tells them the outline is missing (they can delete the box and draw it again). An
unexpected exception is not caught: it is a 500, the idempotency key is released, and the
retry runs the selection again. An idempotent replay returns the stored response without
running the handler, so the model is never called twice for one key.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, Path, Query, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.errors import Conflict, NotFound, Unprocessable
from basis.api.routes.common import get_storage, problems, read_router, write_router
from basis.api.schemas.common import MAX_REVISION
from basis.api.schemas.objects import (
    BoxOut,
    BoxSelection,
    CandidateSelection,
    LocalizationOut,
    ManualSelectionIn,
    ManualSelectionOut,
    MaskOut,
    PhotoObjectOut,
    PhotoObjectsOut,
    PointSelection,
)
from basis.api.users import (
    CurrentUser,
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    require_study_access,
    study_access,
)
from basis.config import Settings
from basis.db.enums import DocumentKind, OrgRole, Stage, StudyStatus
from basis.db.models import Document, PhotoObject, Study
from basis.db.repositories import DocumentRepository, VisibleStudy
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository, counts
from basis.db.session import SessionDep, session_scope
from basis.storage import ObjectNotFound, Storage
from basis.vision.localize import (
    DEFAULT_PHRASE_SET,
    DEFAULT_THRESHOLD,
    PROMPT_BOX_IOU,
    Box,
    Instance,
    Localizer,
    LocalizerError,
    Point,
    Rle,
    prompt_phrases,
)
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.schemas import ComponentLabel
from basis.vision.selection import (
    Candidate,
    Counted,
    Duplicate,
    Plan,
    Prompt,
    Stored,
    Unresolved,
    bare_box,
    duplicate_of,
    plan_box,
    plan_candidate,
    plan_point,
)

EDIT_ROLE: Final = OrgRole.ENGINEER
EDIT_STAGE: Final = Stage.ROOMS_REVIEW
MAX_REASON: Final = 500
NO_LOCALIZER: Final = "no localizer is configured (LOCALIZER_URL or REPLICATE_API_TOKEN)"

reads = read_router(tags=["objects"])
writes = write_router(tags=["objects"])

DocumentIdPath = Annotated[uuid.UUID, Path(description="Photo document id.")]
ObjectIdPath = Annotated[uuid.UUID, Path(description="Object id.")]


def get_localizer(request: Request) -> Localizer | None:
    localizer: Localizer | None = request.app.state.localizer
    return localizer


LocalizerDep = Annotated[Localizer | None, Depends(get_localizer)]
StorageDep = Annotated[Storage, Depends(get_storage)]


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
        mask_source=row.mask_source,  # type: ignore[arg-type]
        mask_reason=row.mask_reason,
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


def stored_of(rows: Sequence[PhotoObject]) -> list[Stored]:
    return [Stored(instance=as_instance(row), counted=row.counted) for row in rows]


def drawn_box(selection: BoxSelection) -> Box:
    b = selection.box
    left, right = sorted((b.x1, b.x2))
    top, bottom = sorted((b.y1, b.y2))
    return Box(x1=left, y1=top, x2=right, y2=bottom)


def plan(rows: Sequence[PhotoObject], selection: ManualSelectionIn) -> Plan:
    """What the selection resolves to among the photo's active rows (404 for an unknown
    candidate id)."""
    stored = stored_of(rows)
    if isinstance(selection, CandidateSelection):
        index = next((i for i, row in enumerate(rows) if row.id == selection.candidate_id), None)
        if index is None:
            raise NotFound("No instance with that id on this photo at the current revision.")
        return plan_candidate(stored, index)
    if isinstance(selection, PointSelection):
        point = Point(x=selection.point.x, y=selection.point.y)
        return plan_point(stored, selection.label, point, allow_duplicate=selection.allow_duplicate)
    return plan_box(
        stored, selection.label, drawn_box(selection), allow_duplicate=selection.allow_duplicate
    )


# --------------------------------------------------------------------------- SAM 3


@dataclass(frozen=True)
class PromptOutcome:
    """A box prompt: the instance kept (if any) and, when the localizer was called, what
    to record (``status`` ``done`` or ``failed``); ``reason`` explains a missing outline."""

    instance: Instance | None
    reason: str | None
    status: Literal["done", "failed"] | None = None
    params: dict[str, Any] | None = None
    returned: int = 0
    model_version: str | None = None
    predict_seconds: float | None = None
    cost_usd: float | None = None


async def prompt_box(
    localizer: Localizer | None,
    storage: Storage,
    gcs_uri: str,
    label: ComponentLabel,
    box: Box,
    timeout_s: float,
) -> PromptOutcome:
    """SAM 3 prompted with the label's phrases and the drawn box, on the photo prepared as
    for its localization (same pixels, so the mask lines up). Never raises for the
    localizer, the photo or the clock (module doc)."""
    if localizer is None:
        return PromptOutcome(instance=None, reason=NO_LOCALIZER)
    try:
        image = await asyncio.to_thread(prepare_image, await storage.read(gcs_uri))
    except ObjectNotFound:
        return PromptOutcome(instance=None, reason="the photo file is missing from storage")
    except ImageRejected as exc:
        return PromptOutcome(instance=None, reason=f"the photo cannot be prepared: {exc}")
    phrases = [p.text for p in prompt_phrases(label, DEFAULT_PHRASE_SET)]
    params: dict[str, Any] = {
        "phrases": phrases,
        "phrase_set": DEFAULT_PHRASE_SET,
        "box": box.model_dump(mode="json"),
        "threshold": DEFAULT_THRESHOLD,
        "min_iou": PROMPT_BOX_IOU,
        "timeout_s": timeout_s,
    }
    version = getattr(localizer, "version", None)
    model_version = str(version) if version is not None else None
    try:
        async with asyncio.timeout(timeout_s):
            prompted = await localizer.segment_prompt(
                image, label=label, boxes=[box], threshold=DEFAULT_THRESHOLD
            )
    except TimeoutError:
        reason = f"SAM 3 did not answer within {timeout_s:g} s"
        return PromptOutcome(None, reason, "failed", params, model_version=model_version)
    except LocalizerError as exc:
        reason = f"SAM 3 failed: {type(exc).__name__}: {exc}"[:MAX_REASON]
        return PromptOutcome(None, reason, "failed", params, model_version=model_version)
    kept = prompted.instances[0] if prompted.instances else None
    missing = (
        None
        if kept is not None
        else f"SAM 3 returned {prompted.returned} {label.value} instance(s), none overlapping "
        f"the box at IoU >= {PROMPT_BOX_IOU:g} with score >= {DEFAULT_THRESHOLD:g}"
    )
    return PromptOutcome(
        instance=kept,
        reason=missing,
        status="done",
        params=params,
        returned=prompted.returned,
        model_version=model_version,
        predict_seconds=round(prompted.predict_seconds, 3),
        cost_usd=round(prompted.cost_usd, 6),
    )


# --------------------------------------------------------------------------- helpers


async def photo_of(session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = await DocumentRepository(session).get(study_id, document_id)
    if document is None or document.kind is not DocumentKind.PHOTO:
        raise NotFound("Photo not found.")
    return document


def check_editable(study: Study) -> int:
    """The revision a manual edit applies to; 409 unless the study waits at rooms_review."""
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
        # analyzed and localized behind the appraisal review (EVAL_REPORT 27).
        raise Conflict(
            f"The rooms review is not open yet (status {study.status.value}): the photos "
            "are still being analyzed.",
            type_suffix="gate-not-open",
        )
    return study.revision


async def editable_revision(
    session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID
) -> int:
    """:func:`check_editable` on the study row read ``FOR SHARE`` (a decision's
    compare-and-set waits for this transaction), then the photo's advisory lock until the
    transaction ends."""
    study = (
        await session.scalars(select(Study).where(Study.id == study_id).with_for_update(read=True))
    ).one()
    revision = check_editable(study)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"photo-objects:{document_id}:{revision}"},
    )
    return revision


async def localized_rows(
    repo: PhotoObjectRepository, study_id: uuid.UUID, document_id: uuid.UUID, revision: int
) -> list[PhotoObject]:
    if await repo.localization(document_id, revision) is None:
        # The rooms review opens with SAM 3 still running (EVAL_REPORT 30): until the
        # photo's own instances are stored a selection cannot resolve against them.
        raise Conflict(
            "The photo is still being localized; its objects appear when that finishes.",
            type_suffix="localization-pending",
        )
    return await repo.active(study_id, revision, [document_id])


def manual_from(row: PhotoObject, user: CurrentUser) -> NewObject:
    """A stored candidate promoted to a manual instance with its mask, score and label."""
    return NewObject(
        label=row.label,
        phrase=row.phrase,
        score=row.score,
        x1=row.x1,
        y1=row.y1,
        x2=row.x2,
        y2=row.y2,
        area=row.area,
        mask=row.mask,
        source="manual",
        derived_from=row.id,
        created_by=user.id,
        mask_source="candidate",
    )


def manual_new(
    instance: Instance,
    user: CurrentUser,
    *,
    mask_source: str,
    mask_reason: str | None = None,
    prompt_id: uuid.UUID | None = None,
) -> NewObject:
    """A SAM 3 instance (``sam_box_prompt``) or a bare box (``none``) as a manual row."""
    box = instance.box
    outlined = mask_source == "sam_box_prompt"
    return NewObject(
        label=instance.label.value,
        phrase=instance.phrase if outlined else None,
        score=instance.score if outlined else None,
        x1=box.x1,
        y1=box.y1,
        x2=box.x2,
        y2=box.y2,
        area=instance.area,
        mask=instance.mask.model_dump(mode="json") if outlined and instance.mask else None,
        source="manual",
        created_by=user.id,
        mask_source=mask_source,
        mask_reason=None if outlined else mask_reason,
        prompt_id=prompt_id,
    )


def nothing_at_point(selection: ManualSelectionIn) -> Unprocessable:
    label = selection.label.value if isinstance(selection, PointSelection) else "matching"
    return Unprocessable(
        f"No {label} instance is under that point; draw a box around it instead.",
        type_suffix="nothing-at-point",
    )


def duplicate_answer(
    rows: Sequence[PhotoObject], found: Duplicate, response: Response
) -> ManualSelectionOut:
    response.status_code = status.HTTP_200_OK
    row = rows[found.index]
    return ManualSelectionOut(
        object=object_out(row),
        created=False,
        duplicate_of=row.id,
        overlap=round(found.overlap, 4),
    )


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
    summary="Add an engineer's selection (a box, a tap or a candidate) at rooms_review",
    description=(
        "A `box` resolves to the stored instance of its label it overlaps at IoU >= 0.5; "
        "with none, SAM 3 is prompted with the label's phrases and the box and the "
        "instance overlapping the box best at IoU >= 0.5 with score >= 0.5 is stored with "
        "its outline (`mask_source: sam_box_prompt`), else the box alone (`none`, "
        "`mask_reason` says why; a failed or timed-out call never fails the add). A tap "
        "(`point`) resolves to the stored instance under it, 422 when there is none (draw "
        "a box). A `candidate` names a stored instance by id. An uncounted candidate "
        "becomes a manual instance with its outline (201, `mask_source: candidate`). A "
        "selection that is, or covers at intersection over the smaller >= 0.5, a counted "
        "instance of its label adds nothing (200, `created: false`, `duplicate_of`) unless "
        "`allow_duplicate`. Only while the study waits at `rooms_review`, on its current "
        "revision, once the photo's localization is recorded (409 otherwise; "
        "`localization-pending` while SAM 3 still runs)."
    ),
    responses={
        200: {"description": "The selection is a counted instance already; nothing added."},
        **problems(403, 404),
    },
)
async def add_object(
    body: ManualSelectionIn,
    request: Request,
    study_id: StudyIdPath,
    document_id: DocumentIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
    localizer: LocalizerDep,
    response: Response,
) -> ManualSelectionOut:
    settings: Settings = request.app.state.settings
    # 1. Read: does the selection need SAM 3? No locks, no transaction left open.
    async with session_scope(sessionmaker) as session:
        access = await require_study_access(session, user, study_id, EDIT_ROLE)
        gcs_uri = (await photo_of(session, study_id, document_id)).gcs_uri
        revision = check_editable(access.study)
        repo = PhotoObjectRepository(session)
        first = plan(await localized_rows(repo, study_id, document_id, revision), body)
    if isinstance(first, Unresolved):
        raise nothing_at_point(body)

    # 2. The SAM 3 call, with nothing held, and its record.
    prompted: PromptOutcome | None = None
    prompt_id: uuid.UUID | None = None
    if isinstance(first, Prompt) and isinstance(body, BoxSelection):
        prompted = await prompt_box(
            localizer,
            storage,
            gcs_uri,
            body.label,
            drawn_box(body),
            settings.object_prompt_timeout_s,
        )
        if prompted.status is not None:
            async with session_scope(sessionmaker) as session:
                record = await PhotoObjectRepository(session).record_prompt(
                    study_id=study_id,
                    document_id=document_id,
                    revision=revision,
                    label=body.label.value,
                    params=prompted.params or {},
                    status=prompted.status,
                    created_by=user.id,
                    reason=prompted.reason if prompted.status == "failed" else None,
                    returned=prompted.returned,
                    kept_score=prompted.instance.score if prompted.instance else None,
                    model_version=prompted.model_version,
                    predict_seconds=prompted.predict_seconds,
                    cost_usd=prompted.cost_usd,
                )
                prompt_id = record.id

    # 3. Write, under the study's share lock and the photo's lock, resolved again.
    async with session_scope(sessionmaker) as session:
        await require_study_access(session, user, study_id, EDIT_ROLE)
        if await editable_revision(session, study_id, document_id) != revision:
            raise Conflict(
                "The study moved to another revision while the object was being outlined.",
                type_suffix="stale-state",
            )
        repo = PhotoObjectRepository(session)
        rows = await localized_rows(repo, study_id, document_id, revision)
        stored = stored_of(rows)
        allow = body.allow_duplicate
        now = plan(rows, body)
        if isinstance(now, Counted):
            return duplicate_answer(rows, Duplicate(index=now.index, overlap=1.0), response)
        if isinstance(now, Unresolved):  # a manual row under the tap was deleted meanwhile
            raise nothing_at_point(body)
        if isinstance(now, Candidate):
            new = manual_from(rows[now.index], user)
            instance = stored[now.index].instance
        else:
            assert isinstance(body, BoxSelection)
            if prompted is None:  # a stored instance matched in step 1 but is gone now
                prompted = PromptOutcome(
                    instance=None, reason="the stored instance the box matched is gone"
                )
            if prompted.instance is not None:
                instance = prompted.instance
                new = manual_new(instance, user, mask_source="sam_box_prompt", prompt_id=prompt_id)
            else:
                instance = bare_box(body.label, drawn_box(body))
                new = manual_new(
                    instance,
                    user,
                    mask_source="none",
                    mask_reason=prompted.reason,
                    prompt_id=prompt_id,
                )
        if not allow:
            found = duplicate_of(stored, instance)
            if found is not None:
                return duplicate_answer(rows, found, response)
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
