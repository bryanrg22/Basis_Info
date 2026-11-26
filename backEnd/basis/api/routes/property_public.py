"""Public geographic sources of a study's property (docs/api/PUBLIC_GEO.md, EVAL_REPORT 101).

* ``GET /v1/studies/{id}/property/public`` (viewer): the newest gather, its current public view
  and the history (one view per year), the parcels, the site check against the appraisal
  (computed when read, so a later extraction is checked too) and the lidar summary, each with
  its source and credit.
* ``POST /v1/studies/{id}/property/public`` (engineer): gather. 409 ``geo-off`` while
  ``PUBLIC_GEO`` is off, 422 ``geo-no-point`` without a point on the pin, 409
  ``geo-in-progress`` while one is queued or running, 200 with the recent gather when the same
  pin was gathered within ``GEO_RECENT_HOURS`` (unless ``force``), else 202. The row is committed
  first, then the workflow started; when the start fails the row is marked failed
  (``E.GEO_UNAVAILABLE``) and the answer is 503. ``POST .../property/captures`` also queues one
  (basis.geo.starter.CaptureStarterWithGeo).
* ``GET .../property/public/views/{view_id}/image`` (viewer): the image, ``ETag`` its sha256.
* ``GET /v1/ops/geo-incidents`` (operators): failures grouped by code and source.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import Depends, Header, Path, Request, Response, status

from basis.api.errors import Conflict, NotFound, ServiceUnavailable, Unprocessable
from basis.api.operators import require_operator
from basis.api.routes.common import get_storage, problems, read_router, write_router
from basis.api.routes.property_capture import same_point
from basis.api.schemas.public_geo import (
    GatherIn,
    GatherOut,
    GeoIncidentListOut,
    GeoIncidentOut,
    LidarOut,
    ParcelOut,
    PublicPropertyOut,
    PublicViewOut,
    SiteCheckOut,
)
from basis.api.users import (
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    require_study_access,
    study_access,
)
from basis.config import Settings
from basis.db.enums import OrgRole
from basis.db.models import GeoGather, GeoParcel, GeoView, PropertyPin
from basis.db.repositories import VisibleStudy
from basis.db.repositories.public_geo import GeoActiveError, PublicGeoRepository
from basis.db.session import SessionDep, session_scope
from basis.domain import traced
from basis.errors.catalogue import BY_CODE
from basis.errors.codes import E
from basis.geo import facade
from basis.geo.geometry import M2_PER_ACRE, SQFT_PER_M2, from_mercator
from basis.geo.site_check import check
from basis.geo.starter import GeoStarter, GeoStartError
from basis.storage import ObjectNotFound, Storage

logger = logging.getLogger(__name__)

reads = read_router(tags=["property"])
writes = write_router(tags=["property"])
ops_reads = read_router(tags=["ops"])

ViewerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))]
ViewIdPath = Annotated[uuid.UUID, Path(description="Public view id.")]

MESSAGES: Final = {
    "queued": "Public records are queued.",
    "running": "Gathering public records.",
    "succeeded": "Public records are ready.",
}


def _message(row: GeoGather) -> str:
    if row.status in MESSAGES:
        return MESSAGES[row.status]
    return row.reason or "The public records could not be gathered; ask for them again."


def _gather_out(row: GeoGather, pin: PropertyPin | None) -> GatherOut:
    return GatherOut(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        code=row.code,
        message=_message(row),
        requested_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        pin_version=row.pin_version,
        stale=gather_is_stale(row, pin),
        sources=list(row.sources or []),
    )


def gather_is_stale(row: GeoGather, pin: PropertyPin | None) -> bool:
    """Gathered at another point than the pin's current one (more than 1 m away). A
    confirmation or an address edit raises the pin's version without moving the point and
    leaves the gather current, as for the capture's views."""
    if pin is None:
        return False
    if pin.lat is None or pin.lon is None:
        return True
    return not same_point(row.lat, row.lon, pin.lat, pin.lon)


def _view_out(view: GeoView, stale: bool) -> PublicViewOut:
    west, south = from_mercator(view.bbox[0], view.bbox[1])
    east, north = from_mercator(view.bbox[2], view.bbox[3])
    return PublicViewOut.model_validate(
        {
            **{n: getattr(view, n) for n in PublicViewOut.model_fields if hasattr(view, n)},
            "bbox": list(view.bbox),
            "bbox_lonlat": [west, south, east, north],
            "stale": stale,
        }
    )


def _parcel_out(p: GeoParcel) -> ParcelOut:
    return ParcelOut.model_validate(
        {
            **{n: getattr(p, n) for n in ParcelOut.model_fields if hasattr(p, n)},
            "area_sqft": p.area_m2 * SQFT_PER_M2,
            "area_acres": p.area_m2 / M2_PER_ACRE,
        }
    )


def _shell_out(record: object) -> dict[str, object] | None:
    """The stored shell (``basis.geo.shell.as_record``) as the API shows it, with each compass
    side's width (``basis.geo.facade.sides``, EVAL_REPORT 111); None when there is none."""
    if not isinstance(record, dict):
        return None
    ring = record.get("wall_outline") or []
    found: list[dict[str, object]] = []
    if isinstance(ring, list) and len(ring) >= 3:
        points = [(float(lon), float(lat)) for lon, lat in ring]
        lats = [lat for _, lat in points]
        feet = traced.plane(points, points[0], (min(lats) + max(lats)) / 2)
        metres = [(x * 0.3048, y * 0.3048) for x, y in feet]
        found = [
            {"side": n, "azimuth_deg": round(a, 2), "width_m": w, "edges": e}
            for n, a, w, e in facade.sides(metres)
        ]
    return {
        "code": record.get("code"),
        "wall_outline": ring if isinstance(ring, list) else [],
        "overhang_m": record.get("overhang_m"),
        "perimeter_m": record.get("perimeter_m"),
        "eave_main_m": record.get("eave_main_m"),
        "sides": found,
    }


def _lidar_out(summary: dict[str, object] | None) -> LidarOut | None:
    if not summary:
        return None
    building = summary.get("building")
    if isinstance(building, dict):
        building = {**building, "footprint_sqft": float(building["footprint_m2"]) * SQFT_PER_M2}
    return LidarOut.model_validate(
        {**summary, "building": building, "shell": _shell_out(summary.get("shell"))}
    )


@reads.get(
    "/studies/{study_id}/property/public",
    summary="The property's public records: images, parcels, lidar",
    description="The newest gather of the public sources: the current public top-down view, "
    "one view per year, the parcel under the pin (and the owner's adjacent ones), the check of "
    "the parcel's area against the appraisal's site area, and the lidar heights and footprint, "
    "each with its source, credit and terms.",
)
async def get_public(
    access: ViewerStudy, session: SessionDep, request: Request
) -> PublicPropertyOut:
    settings: Settings = request.app.state.settings
    repo = PublicGeoRepository(session)
    pin = await repo.pin(access.study.id)
    row = await repo.newest(access.study.id)
    if row is None:
        return PublicPropertyOut(
            enabled=settings.public_geo,
            gather=None,
            current=None,
            history=[],
            parcels=[],
            site_check=None,
            lidar=None,
        )
    stale = gather_is_stale(row, pin)
    views = await repo.views(row.id)
    parcels = await repo.parcels(row.id)
    current = next((v for v in views if v.kind == "current"), None)
    history = sorted(
        (v for v in views if v.kind == "history" or v is current),
        key=lambda v: (-(v.year or 0), v.label),
    )
    site = None
    if row.status == "succeeded":
        result = check(
            parcels,
            await repo.appraisal_site_sqft(access.study.id),
            tolerance_pct=settings.geo_site_tolerance_pct,
        )
        site = SiteCheckOut.model_validate(
            {
                **{n: getattr(result, n) for n in SiteCheckOut.model_fields},
                "parcels": list(result.parcels),
            }
        )
    return PublicPropertyOut(
        enabled=settings.public_geo,
        gather=_gather_out(row, pin),
        current=None if current is None else _view_out(current, stale),
        history=[_view_out(v, stale) for v in history],
        parcels=[_parcel_out(p) for p in parcels],
        site_check=site,
        lidar=_lidar_out(row.lidar),
    )


@writes.post(
    "/studies/{study_id}/property/public",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Gather the property's public records (in the background)",
    description="202 with the queued gather; poll `GET .../property/public`. 200 with the recent "
    "gather when the same pin was gathered within `GEO_RECENT_HOURS` (send `force: true` to "
    "gather again). 409 `geo-off`, 409 `geo-in-progress`, 422 `geo-no-point`, 503 when the "
    "workflow cannot start.",
    responses={200: {"model": GatherOut}, **problems(403, 404, 409, 422, 503)},
)
async def start_gather(
    study_id: StudyIdPath,
    request: Request,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    response: Response,
    body: GatherIn | None = None,
) -> GatherOut:
    settings: Settings = request.app.state.settings
    force = body.force if body is not None else False
    async with session_scope(sessionmaker) as session:
        access = await require_study_access(session, user, study_id, OrgRole.ENGINEER)
        if not settings.public_geo:
            raise Conflict(
                "Public records are turned off on this deployment.",
                type_suffix="geo-off",
                code=E.GEO_OFF,
            )
        repo = PublicGeoRepository(session)
        pin = await repo.pin(access.study.id)
        if pin is None or pin.lat is None or pin.lon is None:
            raise Unprocessable(
                "Drop the pin on the house first (PUT .../property/pin with lat and lon); public "
                "records are looked up by point.",
                type_suffix="geo-no-point",
                code=E.GEO_NO_POINT,
            )
        if not force and settings.geo_recent_hours > 0:
            since = datetime.now(UTC) - timedelta(hours=settings.geo_recent_hours)
            recent = next(
                (
                    row
                    for row in await repo.recent_successes(access.study.id, since=since)
                    if not gather_is_stale(row, pin)
                ),
                None,
            )
            if recent is not None:
                response.status_code = status.HTTP_200_OK
                return _gather_out(recent, pin)
        try:
            row = await repo.create_gather(access.study.id, user_id=user.id, pin=pin)
        except GeoActiveError as exc:
            raise Conflict(
                f"Public records for this property are already being gathered ({exc.gather_id}).",
                type_suffix="geo-in-progress",
                code=E.GEO_IN_PROGRESS,
            ) from exc
        gather_id = row.id
    starter: GeoStarter | None = getattr(request.app.state, "geo_starter", None)
    try:
        if starter is None:
            raise GeoStartError("no workflow service")
        await starter.start(str(gather_id))
    except GeoStartError as exc:
        async with session_scope(sessionmaker) as session:
            await PublicGeoRepository(session).mark_unstarted(gather_id, code=E.GEO_UNAVAILABLE)
        logger.warning(
            "public geo start failed",
            extra={"error_code": E.GEO_UNAVAILABLE, "gather_id": str(gather_id)},
        )
        raise ServiceUnavailable(
            "Public records could not start; try again in a minute.",
            retry_after=30,
            code=E.GEO_UNAVAILABLE,
        ) from exc
    logger.info("public geo queued", extra={"study_id": str(study_id), "gather_id": str(gather_id)})
    async with session_scope(sessionmaker) as session:
        repo = PublicGeoRepository(session)
        queued = await repo.gather(study_id, gather_id)
        assert queued is not None
        return _gather_out(queued, await repo.pin(study_id))


@reads.get(
    "/studies/{study_id}/property/public/views/{view_id}/image",
    summary="One public view's image",
    description="`ETag` is the image's sha256; `If-None-Match` with it answers 304.",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}, "image/png": {}}},
        304: {"description": "Not modified."},
        **problems(404),
    },
)
async def get_public_image(
    access: ViewerStudy,
    view_id: ViewIdPath,
    session: SessionDep,
    storage: Annotated[Storage, Depends(get_storage)],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    view = await PublicGeoRepository(session).view(access.study.id, view_id)
    if view is None:
        raise NotFound("No such public view of this study.")
    etag = f'"{view.sha256}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=3600"}
    if if_none_match is not None and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    try:
        data = await storage.read(view.uri)
    except ObjectNotFound as exc:
        raise NotFound("The image is no longer stored (the study's files were purged).") from exc
    return Response(content=data, media_type=view.content_type, headers=headers)


@ops_reads.get(
    "/ops/geo-incidents",
    summary="Public-source failures, grouped by code and source (operators)",
    description="One row per `BASIS-GEO-<nnn>` and source: count, first and last seen, and the "
    "newest occurrence's study, gather, request and HTTP status. Platform operators only.",
    dependencies=[Depends(require_operator)],
    responses=problems(401, 403),
)
async def list_geo_incidents(session: SessionDep) -> GeoIncidentListOut:
    rows = await PublicGeoRepository(session).incidents()
    return GeoIncidentListOut(
        items=[
            GeoIncidentOut.model_validate(
                {
                    **{n: getattr(r, n) for n in GeoIncidentOut.model_fields if hasattr(r, n)},
                    "what": BY_CODE[r.code].what if r.code in BY_CODE else "",
                }
            )
            for r in rows
        ]
    )


__all__ = ["ops_reads", "reads", "writes"]
