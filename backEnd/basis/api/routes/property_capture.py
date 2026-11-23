"""Property pictures: the pin and the captures of a study's property
(docs/api/PROPERTY_CAPTURE.md, EVAL_REPORT 98).

* ``GET /v1/studies/{id}/property`` (viewer): the pin (``ETag`` its version, ``"0"`` while
  none), the newest capture with its views, and whether captures are on.
* ``PUT /v1/studies/{id}/property/pin`` (engineer, ``If-Match``): the address, the point and
  the engineer's confirmation that the point is on the right house.
* ``POST /v1/studies/{id}/property/captures`` (engineer): queue a capture. 409
  ``capture-off`` while ``PROPERTY_CAPTURE`` is off, 422 ``capture-no-address`` without a pin,
  409 ``capture-in-progress`` while one is queued, waiting or running, 200 with the recent
  capture when the same pin was captured within ``CAPTURE_RECENT_HOURS`` (unless ``force``),
  else 202. The row is committed first, then the workflow started (the study start's order);
  when the start fails the row is marked failed (``E.CAPTURE_UNAVAILABLE``) and the answer is
  503. A pause (breaker, caps) is not a refusal: the capture is accepted and waits.
* ``GET .../property/captures`` and ``.../captures/{capture_id}`` (viewer).
* ``GET .../property/views/{view_id}/image`` (viewer): the PNG, ``ETag`` its sha256.
* ``GET /v1/ops/capture-incidents`` (operators): the grouped failures, newest first.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import Depends, Header, Path, Query, Request, Response, status

from basis.api.errors import (
    Conflict,
    NotFound,
    PreconditionFailed,
    ServiceUnavailable,
    Unprocessable,
)
from basis.api.operators import require_operator
from basis.api.preconditions import ETAG_HEADER, intake_etag, parse_if_match
from basis.api.routes.common import get_storage, problems, read_router, write_router
from basis.api.routes.report import IfMatchHeader
from basis.api.schemas.property import (
    CaptureIn,
    CaptureListOut,
    CaptureOut,
    IncidentListOut,
    IncidentOut,
    PinIn,
    PinOut,
    PropertyOut,
    ViewOut,
)
from basis.api.users import (
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    require_study_access,
    study_access,
)
from basis.capture import messages
from basis.capture.starter import CaptureStarter, CaptureStartError
from basis.config import Settings
from basis.db.enums import OrgRole
from basis.db.errors import PreconditionFailedError
from basis.db.models import CaptureView, PropertyCapture, PropertyPin
from basis.db.repositories import VisibleStudy
from basis.db.repositories.property import CaptureActiveError, PinDocument, PropertyRepository
from basis.db.session import SessionDep, session_scope
from basis.errors.catalogue import BY_CODE
from basis.errors.codes import E
from basis.storage import ObjectNotFound, Storage

logger = logging.getLogger(__name__)

reads = read_router(tags=["property"])
writes = write_router(tags=["property"])
ops_reads = read_router(tags=["ops"])

ViewerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))]
EngineerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.ENGINEER))]
CaptureIdPath = Annotated[uuid.UUID, Path(description="Capture id.")]
ViewIdPath = Annotated[uuid.UUID, Path(description="View id.")]
PNG: Final = "image/png"


def _pin_out(pin: PropertyPin | None) -> PinOut | None:
    return None if pin is None else PinOut.model_validate(pin)


def message_for(row: PropertyCapture) -> str:
    if row.status == "queued":
        return messages.QUEUED
    if row.status == "running":
        return messages.RUNNING
    if row.status == "succeeded":
        return messages.COMPLETED
    if row.status == "waiting":
        return messages.WAITING_TURN if row.code is None else messages.PAUSED
    return row.reason or messages.failed(row.code or "")


def _view_out(view: CaptureView, pin: PropertyPin | None) -> ViewOut:
    return ViewOut.model_validate(
        {
            **{name: getattr(view, name) for name in ViewOut.model_fields if hasattr(view, name)},
            "stale": pin is not None and view.pin_version != pin.version,
        }
    )


async def _capture_out(
    repo: PropertyRepository, row: PropertyCapture, pin: PropertyPin | None
) -> CaptureOut:
    views = await repo.views(row.id)
    return CaptureOut(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        code=row.code,
        message=message_for(row),
        resume_at=row.resume_at if row.status == "waiting" else None,
        requested_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        pin_version=row.pin_version,
        views=[_view_out(v, pin) for v in views],
    )


@reads.get(
    "/studies/{study_id}/property",
    summary="The property's pin and its newest pictures",
    description='The pin (`ETag` its version; `"0"` and `pin: null` while none), the newest '
    "capture with its views, and whether this deployment takes captures.",
)
async def get_property(
    access: ViewerStudy, session: SessionDep, request: Request, response: Response
) -> PropertyOut:
    settings: Settings = request.app.state.settings
    repo = PropertyRepository(session)
    pin = await repo.pin(access.study.id)
    newest = await repo.captures(access.study.id, limit=1)
    response.headers[ETAG_HEADER] = intake_etag(pin.version if pin else 0)
    return PropertyOut(
        pin=_pin_out(pin),
        capture=await _capture_out(repo, newest[0], pin) if newest else None,
        enabled=settings.property_capture,
    )


@writes.put(
    "/studies/{study_id}/property/pin",
    summary="Set the property's address, point and confirmation",
    description="The whole pin. A point you send that differs from the stored one is yours "
    "(`source: engineer`); no point clears it, and the next capture searches the address. "
    "`confirmed` needs a point; moving the point withdraws an earlier confirmation unless "
    "this request confirms it. Views taken around an earlier pin are marked `stale`. "
    f"`If-Match` with the `ETag` of the `GET` (412 {E.CAPTURE_PIN_PRECONDITION_FAILED} with "
    "the current `ETag` otherwise; `*` only over a saved pin).",
    responses=problems(403, 404, 412),
)
async def put_pin(
    body: PinIn,
    access: EngineerStudy,
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> PinOut:
    condition = parse_if_match(if_match)
    try:
        written = await PropertyRepository(session).write_pin(
            access.study.id,
            PinDocument(body.address, body.lat, body.lon, body.confirmed),
            user_id=user.id,
            precondition=None if condition is None else condition.allows,
        )
    except PreconditionFailedError as exc:
        current = intake_etag(exc.version)
        raise PreconditionFailed(
            f"The pin changed since you read it (its ETag is now {current}); nothing was "
            "written. Read it again, reapply your change, and send the new ETag.",
            headers={ETAG_HEADER: current},
            code=E.CAPTURE_PIN_PRECONDITION_FAILED,
        ) from exc
    logger.info(
        "property pin set",
        extra={
            "study_id": str(access.study.id),
            "version_before": written.version_before,
            "version_after": written.row.version,
            "changed": written.changed,
        },
    )
    response.headers[ETAG_HEADER] = intake_etag(written.row.version)
    out = _pin_out(written.row)
    assert out is not None
    return out


@writes.post(
    "/studies/{study_id}/property/captures",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Take pictures of the property (in the background)",
    description="202 with the queued capture. It runs on the capture worker and may pause "
    "(`status: waiting`, `resume_at`) and resume on its own; poll `GET .../property`. "
    "200 with the recent capture when the same pin was captured within "
    "`CAPTURE_RECENT_HOURS` (send `force: true` to take them again). 409 `capture-off`, 409 "
    "`capture-in-progress`, 422 `capture-no-address`, 503 when the workflow cannot start.",
    responses={200: {"model": CaptureOut}, **problems(403, 404, 409, 422, 503)},
)
async def start_capture(
    study_id: StudyIdPath,
    request: Request,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    response: Response,
    body: CaptureIn | None = None,
) -> CaptureOut:
    settings: Settings = request.app.state.settings
    force = body.force if body is not None else False
    async with session_scope(sessionmaker) as session:
        access = await require_study_access(session, user, study_id, OrgRole.ENGINEER)
        if not settings.property_capture:
            raise Conflict(
                "Property pictures are turned off on this deployment.",
                type_suffix="capture-off",
                code=E.CAPTURE_OFF,
            )
        repo = PropertyRepository(session)
        pin = await repo.pin(access.study.id)
        if pin is None:
            raise Unprocessable(
                "Set the property's address or drop its pin first (PUT .../property/pin).",
                type_suffix="capture-no-address",
                code=E.CAPTURE_NO_ADDRESS,
            )
        if not force and settings.capture_recent_hours > 0:
            since = datetime.now(UTC) - timedelta(hours=settings.capture_recent_hours)
            recent = await repo.recent_success(
                access.study.id, since=since, pin_version=pin.version
            )
            if recent is not None:
                response.status_code = status.HTTP_200_OK
                return await _capture_out(repo, recent, pin)
        try:
            row = await repo.create_capture(
                access.study.id, user_id=user.id, pin_version=pin.version
            )
        except CaptureActiveError as exc:
            raise Conflict(
                f"A capture of this property is already in progress ({exc.capture_id}).",
                type_suffix="capture-in-progress",
                code=E.CAPTURE_IN_PROGRESS,
            ) from exc
        capture_id = row.id
    starter: CaptureStarter | None = getattr(request.app.state, "capture_starter", None)
    try:
        if starter is None:
            raise CaptureStartError("no workflow service")
        await starter.start(str(capture_id))
    except CaptureStartError as exc:
        async with session_scope(sessionmaker) as session:
            await PropertyRepository(session).mark_unstarted(
                capture_id,
                code=E.CAPTURE_UNAVAILABLE,
                reason=messages.failed(E.CAPTURE_UNAVAILABLE),
            )
        logger.warning(
            "capture start failed",
            extra={"error_code": E.CAPTURE_UNAVAILABLE, "capture_id": str(capture_id)},
        )
        raise ServiceUnavailable(
            "Property pictures could not start; try again in a minute.",
            retry_after=30,
            code=E.CAPTURE_UNAVAILABLE,
        ) from exc
    logger.info("capture queued", extra={"study_id": str(study_id), "capture_id": str(capture_id)})
    async with session_scope(sessionmaker) as session:
        repo = PropertyRepository(session)
        queued = await repo.capture(study_id, capture_id)
        assert queued is not None
        return await _capture_out(repo, queued, await repo.pin(study_id))


@reads.get(
    "/studies/{study_id}/property/captures",
    summary="The property's captures, newest first",
)
async def list_captures(
    access: ViewerStudy,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CaptureListOut:
    repo = PropertyRepository(session)
    pin = await repo.pin(access.study.id)
    rows = await repo.captures(access.study.id, limit=limit)
    return CaptureListOut(items=[await _capture_out(repo, row, pin) for row in rows])


@reads.get(
    "/studies/{study_id}/property/captures/{capture_id}",
    summary="One capture with its views",
    responses=problems(404),
)
async def get_capture(
    access: ViewerStudy, capture_id: CaptureIdPath, session: SessionDep
) -> CaptureOut:
    repo = PropertyRepository(session)
    row = await repo.capture(access.study.id, capture_id)
    if row is None:
        raise NotFound("No such capture of this study.")
    return await _capture_out(repo, row, await repo.pin(access.study.id))


@reads.get(
    "/studies/{study_id}/property/views/{view_id}/image",
    summary="One view's image (PNG)",
    description="`ETag` is the image's sha256; `If-None-Match` with it answers 304.",
    response_class=Response,
    responses={200: {"content": {PNG: {}}}, 304: {"description": "Not modified."}, **problems(404)},
)
async def get_view_image(
    access: ViewerStudy,
    view_id: ViewIdPath,
    session: SessionDep,
    storage: Annotated[Storage, Depends(get_storage)],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    view = await PropertyRepository(session).view(access.study.id, view_id)
    if view is None:
        raise NotFound("No such view of this study.")
    etag = f'"{view.sha256}"'
    headers = {ETAG_HEADER: etag, "Cache-Control": "private, max-age=3600"}
    if if_none_match is not None and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    try:
        data = await storage.read(view.uri)
    except ObjectNotFound as exc:
        raise NotFound(
            "The view's image is no longer stored (the study's files were purged)."
        ) from exc
    return Response(content=data, media_type=PNG, headers=headers)


@ops_reads.get(
    "/ops/capture-incidents",
    summary="Property-capture failures, grouped by code (operators)",
    description="One row per `BASIS-CAPTURE-<nnn>` seen: count, first and last seen, and the "
    "newest occurrence's study, capture, page, HTTP status and evidence prefix. Platform "
    "operators only (`OPS_OPERATOR_UIDS`).",
    dependencies=[Depends(require_operator)],
    responses=problems(401, 403),
)
async def list_capture_incidents(session: SessionDep) -> IncidentListOut:
    rows = await PropertyRepository(session).incidents()
    return IncidentListOut(
        items=[
            IncidentOut.model_validate(
                {
                    **{n: getattr(r, n) for n in IncidentOut.model_fields if hasattr(r, n)},
                    "what": BY_CODE[r.code].what if r.code in BY_CODE else "",
                }
            )
            for r in rows
        ]
    )


__all__ = ["ops_reads", "reads", "writes"]
