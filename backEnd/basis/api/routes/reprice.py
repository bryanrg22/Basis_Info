"""``/v1/studies/{id}/assets/reprice``: the impact preview and the engineer's explicit
rebuild of the assets step with the current intake and cost index store
(:mod:`basis.costs.reprice`; docs/api/REPRICE.md).

* ``GET``: what a rebuild would change (per asset, the reconciliation before and after,
  the pricing reasons) and the ``fingerprint`` of the inputs; nothing is written. Engineer
  role, like the decisions it leads to.
* ``POST`` (``Idempotency-Key``): records the rebuild as an assets-review rejection with a
  ``reprice`` correction (and a ``structure_model`` one when the body sets the square-foot
  model), 202. 409 ``reprice-inputs-changed`` when the fingerprint is not the current one,
  ``reprice-reclassify`` when the property class changed (the assets must be classified
  again: reject the assets review), ``gate-not-open`` / stale when the study is not at that
  revision's open assets review.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Final

from fastapi import Depends, Query, Request, status

from basis.activities.deps import Deps
from basis.api.errors import Conflict
from basis.api.idempotency import IDEMPOTENCY_HEADER, parse_idempotency_key
from basis.api.routes.common import (
    get_storage,
    get_storage_bucket,
    problems,
    read_router,
    write_router,
)
from basis.api.routes.outputs import RevisionQuery, _revision
from basis.api.schemas.reprice import (
    MAX_LIVING_AREA_SF,
    RepriceAccepted,
    RepriceIn,
    RepricePreviewOut,
    StructureModelIn,
)
from basis.api.schemas.reviews import ReviewOut
from basis.api.users import CurrentUserDep, study_access
from basis.config import get_settings
from basis.costs.reprice import STRUCTURE_MODEL_SOURCE, RepriceRefused, preview, request
from basis.db.enums import OrgRole
from basis.db.repositories import VisibleStudy
from basis.db.session import SESSIONMAKER_STATE_ATTR, SessionDep
from basis.domain.structure import BuildingType, Class, ModelOverride

REPRICE_ROLE: Final = OrgRole.ENGINEER

reads = read_router(tags=["reviews"])
writes = write_router(tags=["reviews"])

EngineerStudy = Annotated[VisibleStudy, Depends(study_access(REPRICE_ROLE))]


def _override(body: StructureModelIn | None) -> ModelOverride | None:
    if body is None:
        return None
    return ModelOverride(
        model_class=body.model_class,
        building_type=body.building_type,
        living_area_sf=None if body.living_area_sf is None else Decimal(body.living_area_sf),
        source=STRUCTURE_MODEL_SOURCE,
    )


def _deps(request: Request) -> Deps:
    """No model provider and no retrieval: the preview never makes a paid call."""
    return Deps(
        sessionmaker=getattr(request.app.state, SESSIONMAKER_STATE_ATTR),
        storage=get_storage(request),
        bucket=get_storage_bucket(request),
        settings=get_settings(),
    )


@reads.get(
    "/studies/{study_id}/assets/reprice",
    summary="Preview a rebuild of the assets with the current intake and cost indices",
    description="Runs the rebuild's own code (carry, classifications carried, pricing) in a "
    "transaction that is rolled back: nothing is written and no model is called. Returns "
    "the per-asset change, the reconciliation before and after, why the pricing is stale and "
    "the `fingerprint` to send with the POST. `model_class`, `building_type` and "
    "`living_area_sf` preview a square-foot model set by the engineer.",
    responses=problems(403, 404),
)
async def preview_reprice(
    access: EngineerStudy,
    session: SessionDep,
    request_: Request,
    revision: RevisionQuery = None,
    model_class: Annotated[Class | None, Query(description="RSMeans class to preview.")] = None,
    building_type: Annotated[
        BuildingType | None, Query(description="RSMeans building type to preview.")
    ] = None,
    living_area_sf: Annotated[
        int | None, Query(gt=0, le=MAX_LIVING_AREA_SF, description="Living area to preview, SF.")
    ] = None,
) -> RepricePreviewOut:
    override = None
    if model_class is not None or building_type is not None or living_area_sf is not None:
        override = ModelOverride(
            model_class=model_class,
            building_type=building_type,
            living_area_sf=None if living_area_sf is None else Decimal(living_area_sf),
            source=STRUCTURE_MODEL_SOURCE,
        )
    study = access.study
    return await preview(session, _deps(request_), study, _revision(access, revision), override)


@writes.post(
    "/studies/{study_id}/assets/reprice",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rebuild the assets with the current intake and cost indices (engineer)",
    description="Records an assets-review rejection carrying a `reprice` correction (and a "
    "`structure_model` correction when `structure_model` is set); the workflow carries the "
    "revision and rebuilds the assets with every classification kept and no model call. "
    "Status: the study's stage (`assets_processing`, then `assets_review` of the new "
    "revision) and the timeline (`assets.reprice_requested`). 409 `reprice-inputs-changed`, "
    "`reprice-reclassify`, `gate-not-open` or stale; `Idempotency-Key` required.",
    responses=problems(403, 404),
)
async def request_reprice(
    body: RepriceIn,
    request_: Request,
    access: EngineerStudy,
    user: CurrentUserDep,
    session: SessionDep,
) -> RepriceAccepted:
    key = parse_idempotency_key(request_.headers.get(IDEMPOTENCY_HEADER))
    try:
        record = await request(
            session,
            get_settings(),
            access.study,
            revision=body.revision,
            sent_fingerprint=body.fingerprint,
            reason=body.reason,
            reviewer_id=user.id,
            idempotency_key=key,
            override=_override(body.structure_model),
        )
    except RepriceRefused as refused:
        raise Conflict(str(refused), type_suffix=refused.type_suffix, code=refused.code) from None
    return RepriceAccepted(
        review=ReviewOut.model_validate(record.review),
        current_stage=record.study.current_stage,
        revision=record.study.revision,
        fingerprint=body.fingerprint,
        correction_ids=[c.id for c in record.corrections],
    )


__all__ = ["reads", "writes"]
