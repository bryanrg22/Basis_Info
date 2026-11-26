"""``POST /v1/studies/{id}/property/site-drafts``: "Draft it" in the measure tool
(docs/api/MEASURE_DRAFTS.md, EVAL_REPORT 111; AI_MEASUREMENT_PLAN step 6).

The engineer drags a box over a driveway, patio, deck, pool, walk or parking area on a top-down
view of the property and asks Basis to draft its outline. The route runs
:func:`basis.vision.site_items.draft_if_enabled` (SAM 3 from the box alone, no phrase: the
setup EVAL_REPORT 106.3 measured best, a median area error of 13.6 % on the county's 3-inch
ortho) on the whole view, as measured, and answers with the draft as a measurement record the
tool shows dashed beside the engineer's edit (``drawn_by: model:sam3@site-v1``, ``verified:
false``), or a coded refusal in plain words. Nothing is stored on the study but the paid call
(``llm_calls``) and a ``measurement.drafted`` event: the draft becomes the study's only when
the engineer sends it as an ``asset`` correction (docs/api/OVERRIDES.md section 4), edited or
not, verified or not.

Refused before any call (nothing spent): 409 ``site-drafts-off`` (``SITE_DRAFTS`` off or
``TRACED_MEASUREMENT`` off), 404 for a view or an asset that is not the study's, 409
``gate-not-open`` unless the study waits at the assets review (a draft goes with its
corrections), 409 ``budget-exhausted`` when the study's budget has less than
``SITE_DRAFT_RESERVE_USD`` left, 503 when no segmentation model is configured, 429 above
``SITE_DRAFTS_PER_MIN`` a person. A view SAM 3 must not draft on (not top-down, coarser than
``SITE_DRAFT_MAX_PIXEL_M``) and a fence are 200 refusals, also with no call.

Transactions: the checks read in one short transaction; the image is read from storage and the
call made with no connection held (EVAL_REPORT section 32); the call's ledger row and the event
are written after it, so a billed call is recorded even when the draft is a refusal. The call
is bounded by ``SITE_DRAFT_TIMEOUT_S``; a timeout or a failed call is the refusal
``E.SITE_SEGMENTER_FAILED``. An idempotent replay (the same ``Idempotency-Key``) returns the stored
answer without calling again. CPU work (decoding, the mask's outline) runs on threads.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import time
import uuid
from decimal import Decimal
from typing import Annotated, Any, Final

from fastapi import Depends, Request

from basis.api.errors import (
    Conflict,
    NotFound,
    ServiceUnavailable,
    TooManyRequests,
    Unprocessable,
)
from basis.api.ratelimit import WINDOW, RateLimiter, get_rate_limiter
from basis.api.routes.common import problems, write_router
from basis.api.schemas.site_drafts import DraftRefusalOut, SiteDraftIn, SiteDraftOut
from basis.api.users import (
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    get_sessionmaker,
    require_study_access,
)
from basis.config import Settings
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.models import CaptureView, GeoView
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.repositories.property import PropertyRepository
from basis.db.repositories.public_geo import PublicGeoRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.domain import traced
from basis.errors.codes import E
from basis.llm.call import CallContext, step_entry
from basis.llm.provider import Budget
from basis.llm.recorder import KIND_LOCALIZATION, STATUS_ERROR, STATUS_OK, LlmCallRecorder
from basis.storage import ObjectNotFound, Storage
from basis.vision import site_items as si
from basis.vision.localize import Box, LocalizeCall

writes = write_router(tags=["property"])

ACTIVITY: Final = "site_draft"
EVENT: Final = "measurement.drafted"
DRAFT_ROLE: Final = OrgRole.ENGINEER

# What the engineer reads for each refusal (never a provider's name: AGENTS.md). Keyed by the
# code; the code travels beside it.
REFUSAL_TEXT: Final[dict[str, str]] = {
    E.SITE_NOT_TOP_DOWN: "This picture is not a straight-down view, so nothing can be drafted "
    "on it. Choose the close aerial or a sharp public image.",
    E.SITE_VIEW_TOO_COARSE: "This picture is too coarse to draft on (about {pixel} cm a "
    "pixel). Choose a sharper picture, or trace it by hand.",
    E.SITE_NOTHING_FOUND: "Basis found nothing to outline in the box. Draw the box closer "
    "around the item, or trace it by hand.",
    E.SITE_AT_EDGE: "The outline Basis found runs off the edge of the picture, so its area "
    "would be short. Choose a picture that shows all of it, or trace it by hand.",
    E.SITE_TOO_SMALL: "The outline Basis found is smaller than a small stoop. Draw the box "
    "around the whole item, or trace it by hand.",
    E.SITE_SHAPE_INVALID: "The outline Basis found is not a shape that can be measured. Trace "
    "it by hand.",
    E.SITE_SEGMENTER_FAILED: "Drafting is unavailable right now. Trace it by hand, or try "
    "again in a minute.",
    E.SITE_NOT_DRAFTED: "Fences are not drafted from above (too thin, and often under trees). "
    "Trace it by hand, along the lot line where it runs on it.",
}


def plain_refusal(refused: si.Refused, view: si.TopDownView) -> DraftRefusalOut:
    text = REFUSAL_TEXT.get(refused.code, REFUSAL_TEXT[E.SITE_SEGMENTER_FAILED])
    pixel = "?" if view.pixel_size_m is None else f"{view.pixel_size_m * 100:.0f}"
    return DraftRefusalOut(code=refused.code, message=text.format(pixel=pixel))


async def drafts_rate_limit(
    request: Request,
    user: CurrentUserDep,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """``SITE_DRAFTS_PER_MIN`` drafts a person per minute (each is a paid call), counted
    apart from the mutation limit every write has."""
    settings: Settings = request.app.state.settings
    limit = settings.site_drafts_per_min
    decision = await limiter.hit(f"site-drafts:uid:{user.principal.uid}", limit, WINDOW)
    if not decision.allowed:
        raise TooManyRequests(
            f"Rate limit of {limit} drafts per minute exceeded.",
            retry_after=math.ceil(decision.retry_after),
            code=E.MEASURE_DRAFTS_RATE_LIMITED,
        )


def capture_view(row: CaptureView) -> si.TopDownView | None:
    """A property capture's view as a top-down picture, or None when it is not one (tilted,
    turned, street level, or with no pixel size)."""
    if (
        row.pixel_size_m is None
        or abs(row.tilt_deg) > 1e-9
        or abs(row.heading_deg) > 1e-9
        or row.kind not in ("aerial", "history")
    ):
        return None
    return si.TopDownView(
        data=b"",
        width=row.width,
        height=row.height,
        pixel_size_m=row.pixel_size_m,
        georef=si.CentredGeoref(row.lat, row.lon, row.width, row.height, row.pixel_size_m),
        kind="capture",
        source=row.source,
        id=str(row.id),
        label=row.label,
        captured_on=row.imagery_on,
    )


def public_view(row: GeoView) -> si.TopDownView:
    """A public view (north-up Web Mercator). It carries a year only, so the draft is marked
    undated, as the measure tool marks a trace on it."""
    return si.TopDownView(
        data=b"",
        width=row.width,
        height=row.height,
        pixel_size_m=row.pixel_size_m,
        georef=si.MercatorGeoref(
            (row.bbox[0], row.bbox[1], row.bbox[2], row.bbox[3]), row.width, row.height
        ),
        kind="public",
        source=row.credit,
        id=str(row.id),
        label=row.label,
    )


def _not_top_down(view: si.TopDownView | None, label: str) -> si.TopDownView:
    """A stand-in for a view that is not top-down, so its refusal reads like every other."""
    if view is not None:
        return view
    return si.TopDownView(
        data=b"",
        width=1,
        height=1,
        pixel_size_m=None,
        georef=si.CentredGeoref(0.0, 0.0, 1, 1, 1.0),
        kind="capture",
        source=label,
        label=label,
    )


def assets_review_open(status: StudyStatus, stage: Stage) -> bool:
    return stage is Stage.ASSETS_REVIEW and status in (
        StudyStatus.WAITING_REVIEW,
        StudyStatus.STALLED,
    )


async def record_call(
    request: Request,
    study_id: uuid.UUID,
    asset_id: uuid.UUID,
    revision: int,
    stage: str,
    budget: Budget,
    call: LocalizeCall | None,
    *,
    failed: str | None,
    params: dict[str, Any],
    latency_ms: int,
) -> None:
    """The SAM 3 call in the paid-call ledger (``kind = localization``, activity
    ``site_draft``, no run: it is the API's), so the study's spend counts it (EVAL_REPORT 40,
    as the photo box prompt's)."""
    localizer = request.app.state.localizer
    settings: Settings = request.app.state.settings
    recorder = LlmCallRecorder(
        get_sessionmaker(request),
        storage=request.app.state.storage,
        bucket=request.app.state.storage_bucket,
        build_sha=settings.git_sha,
    )
    ctx = CallContext(
        study_id=study_id,
        run_id=None,
        activity=ACTIVITY,
        budget=budget,
        recorder=recorder,
        stage=stage,
        revision=revision,
        entity_type="asset",
        entity_id=asset_id,
    )
    seconds = 0.0 if call is None else call.predict_seconds
    version = getattr(localizer, "version", None)
    await recorder.record(
        step_entry(
            ctx,
            kind=KIND_LOCALIZATION,
            provider=str(getattr(localizer, "provider", "replicate")),
            model=f"{getattr(localizer, 'model', 'sam3')}@{version}",
            quantity=seconds,
            unit="second",
            unit_price_usd=float(getattr(localizer, "usd_per_second", 0.0) or 0.0),
            cost_usd=0.0 if call is None else call.cost_usd,
            status=STATUS_ERROR if failed is not None else STATUS_OK,
            error=failed,
            latency_ms=latency_ms,
            params=params,
            response={"status": None if call is None else call.status},
            component=f"{__name__}.draft",
        )
    )


@writes.post(
    "/studies/{study_id}/property/site-drafts",
    summary="Draft a site item's outline from the engineer's box (Draft it)",
    description="Runs SAM 3 on the named top-down view with the engineer's box (no phrase; "
    "EVAL_REPORT 106.3) and answers with the draft as a measurement record (`drawn_by: "
    "model:sam3@site-v1`, `verified: false`) or a coded refusal in plain words "
    "(`BASIS-SITE-<nnn>`). Engineers, while the study waits at the assets review. 409 "
    "`site-drafts-off` (`SITE_DRAFTS` or `TRACED_MEASUREMENT` off), 404 for a view or asset "
    "not the study's, 409 `gate-not-open`, 409 `budget-exhausted`, 503 when drafting is not "
    "configured, 429 above `SITE_DRAFTS_PER_MIN`. The paid call is in the study's spend; "
    "nothing else is stored until the engineer sends the draft as a correction "
    "(docs/api/MEASURE_DRAFTS.md).",
    dependencies=[Depends(drafts_rate_limit)],
    responses=problems(403, 404, 409, 429, 503),
)
async def draft_site_item(
    body: SiteDraftIn,
    request: Request,
    study_id: StudyIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
) -> SiteDraftOut:
    settings: Settings = request.app.state.settings
    if settings.site_drafts != "on" or not settings.traced_measurement:
        # Before the study is read: the same answer for every study while it is off. Access
        # is still checked first so a stranger learns nothing.
        async with session_scope(sessionmaker) as session:
            await require_study_access(session, user, study_id, DRAFT_ROLE)
        raise Conflict(
            "Drafting site items is turned off on this deployment.",
            type_suffix="site-drafts-off",
            code=E.MEASURE_SITE_DRAFTS_OFF,
        )
    # 1. Read (no locks): access, the gate, the asset, the view, the budget.
    async with session_scope(sessionmaker) as session:
        access = await require_study_access(session, user, study_id, DRAFT_ROLE)
        study = access.study
        asset = await AssetRepository(session).get(study.id, body.asset_id)
        if asset is None or asset.revision != study.revision:
            raise NotFound(
                "No such asset at the study's current revision.", code=E.MEASURE_ASSET_NOT_FOUND
            )
        if not assets_review_open(study.status, study.current_stage):
            raise Conflict(
                "Drafts go with the assets review's corrections: the study is not waiting at "
                "the assets review.",
                type_suffix="gate-not-open",
                code=E.MEASURE_GATE_NOT_OPEN,
            )
        uri: str
        view: si.TopDownView | None
        if body.view.kind == "capture":
            row = await PropertyRepository(session).view(study.id, body.view.id)
            if row is None:
                raise NotFound("No such view of this study.", code=E.MEASURE_VIEW_NOT_FOUND)
            uri, view = row.uri, capture_view(row)
            label = row.label
        else:
            public = await PublicGeoRepository(session).view(study.id, body.view.id)
            if public is None:
                raise NotFound("No such view of this study.", code=E.MEASURE_VIEW_NOT_FOUND)
            uri, view, label = public.uri, public_view(public), public.label
        spent = await LlmCallRepository(session).spent_usd(study.id)
        revision, stage = study.revision, study.current_stage.value
    remaining = Decimal(str(settings.study_budget_usd)) - spent
    shown = _not_top_down(view, label)
    family = body.family
    if view is not None and (body.box.x2 > view.width or body.box.y2 > view.height):
        raise Unprocessable(
            f"The box runs outside the {view.width} x {view.height} view: draw it on the picture.",
            type_suffix="validation",
        )
    prompt = si.SitePrompt(
        family,
        box=Box(
            x1=min(1.0, body.box.x1 / shown.width),
            y1=min(1.0, body.box.y1 / shown.height),
            x2=min(1.0, body.box.x2 / shown.width),
            y2=min(1.0, body.box.y2 / shown.height),
        ),
    )
    draft_settings = si.DraftSettings.from_settings(settings)
    scaled = si.apply_capture_scale(shown, draft_settings.capture_scale)
    # 2. Refusals that need no call (a fence, a view that is not top-down or too coarse).
    early = si.view_refusal(scaled, draft_settings)
    if family not in si.DRAFTED:
        return _refused(body, si.Refused(family, prompt, E.SITE_NOT_DRAFTED, ""), scaled)
    if early is not None:
        return _refused(body, si.Refused(family, prompt, *early), scaled)
    if remaining < Decimal(str(settings.site_draft_reserve_usd)):
        raise Conflict(
            f"The study's budget has ${max(remaining, Decimal(0)):.2f} left, under the "
            f"${settings.site_draft_reserve_usd:.2f} a draft needs: trace it by hand.",
            type_suffix="budget-exhausted",
            code=E.MEASURE_BUDGET_EXHAUSTED,
        )
    segmenter = request.app.state.localizer
    if segmenter is None:
        raise ServiceUnavailable(
            "Drafting is unavailable on this deployment; trace it by hand.",
            retry_after=60,
            code=E.MEASURE_DRAFTING_UNAVAILABLE,
        )
    storage: Storage = request.app.state.storage
    try:
        data = await storage.read(uri)
    except ObjectNotFound:
        raise NotFound(
            "The view's image is no longer stored (the study's files were purged).",
            code=E.MEASURE_VIEW_NOT_FOUND,
        ) from None
    # 3. The call, with nothing held, bounded; then its ledger row and the event.
    started = time.monotonic()
    failed: str | None = None
    try:
        async with asyncio.timeout(settings.site_draft_timeout_s):
            drafted = await si.draft_site_items(
                dataclasses.replace(scaled, data=data), [prompt], segmenter, draft_settings
            )
    except TimeoutError:
        failed = f"no answer within {settings.site_draft_timeout_s:g} s"
        drafted = si.Drafted((si.Refused(family, prompt, E.SITE_SEGMENTER_FAILED, failed),))
    latency_ms = round((time.monotonic() - started) * 1000)
    result = drafted.results[0]
    if isinstance(result, si.Refused) and result.code == E.SITE_SEGMENTER_FAILED:
        failed = failed or result.reason
    params = {
        "family": family,
        "box": prompt.box.model_dump(mode="json") if prompt.box else None,
        "view": {"kind": body.view.kind, "id": str(body.view.id)},
        "pipeline": si.PIPELINE_VERSION,
        "threshold": draft_settings.threshold,
        "tolerance_m": draft_settings.tolerance_m,
        "timeout_s": settings.site_draft_timeout_s,
    }
    if drafted.call is not None or failed is not None:
        await record_call(
            request,
            study_id,
            body.asset_id,
            revision,
            stage,
            Budget(remaining_usd=float(max(remaining, Decimal(0)))),
            drafted.call,
            failed=failed,
            params=params,
            latency_ms=latency_ms,
        )
    answer = (
        _drafted(body, result) if isinstance(result, si.Draft) else _refused(body, result, scaled)
    )
    async with session_scope(sessionmaker) as session:
        await StudyEventRepository(session).record(
            study_id,
            EVENT,
            actor="engineer",
            actor_id=user.id,
            revision=revision,
            entity_type="asset",
            entity_id=body.asset_id,
            code=None if answer.refusal is None else answer.refusal.code,
            detail={
                "status": answer.status,
                "family": family,
                "view": {"kind": body.view.kind, "id": str(body.view.id)},
                "drawn_by": draft_settings.drawn_by,
                "quantity": answer.quantity,
                "unit": answer.unit,
                "cost_usd": None if drafted.call is None else round(drafted.call.cost_usd, 6),
            },
        )
    return answer


def _refused(body: SiteDraftIn, refused: si.Refused, view: si.TopDownView) -> SiteDraftOut:
    return SiteDraftOut(
        status="refused",
        family=body.family,
        measurement=None,
        quantity=None,
        unit=body.unit,
        expected_error_pct=None,
        refusal=plain_refusal(refused, view),
    )


def _drafted(body: SiteDraftIn, draft: si.Draft) -> SiteDraftOut:
    """The draft in the asset's unit: the same outline measured again in SY when asked (the
    record is the server's own computation either way)."""
    record = draft.measurement
    if body.unit != draft.unit:
        stored = {k: v for k, v in record.items() if k in traced.BODY_KEYS}
        record = traced.measure({**stored, "unit": body.unit}).record()
    return SiteDraftOut(
        status="drafted",
        family=body.family,
        measurement=record,
        quantity=str(record["quantity"]),
        unit=body.unit,
        expected_error_pct=format(draft.expected_error_pct, "f"),
        refusal=None,
    )


__all__ = ["REFUSAL_TEXT", "writes"]
