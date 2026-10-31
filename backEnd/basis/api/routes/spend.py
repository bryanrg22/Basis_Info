"""``GET /v1/studies/{id}/spend`` and ``/calls``: the paid-call ledger (EVAL_REPORT 40).

``/spend`` rolls the study's ``llm_calls`` rows up by stage, activity, component, model and
kind (calls, tokens of every kind, dollars, latency; non-token kinds also sum their pages or
seconds); ``/calls`` pages the rows themselves, oldest first, with their request and response
references (``activity``, ``kind`` and ``entity_id`` filters). Every study revision's calls
are listed (spend is not per revision). docs/api/SPEND.md.

Access: what the firm pays per call is the owner's to see. Both routes need the firm role
``SPEND_ACCESS_ROLE`` (``owner`` by default; ``engineer`` or ``viewer`` widen it) in the study's
firm, checked here at request time like :func:`basis.api.users.study_access`: a member below
it gets 403, a non-member (or an unknown study) 404, so ids still cannot be probed. The client
hiding spend from engineers and viewers is presentation; this check is the access control.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import Depends, Query, Request

from basis.api.routes.common import problems, read_router
from basis.api.schemas.common import NO_NUL_PATTERN
from basis.api.schemas.spend import CallOut, CallsPageOut, SpendLineOut, StudySpendOut
from basis.api.users import CurrentUserDep, StudyIdPath, require_study_access
from basis.config import Settings
from basis.db.enums import OrgRole
from basis.db.models import LlmCall
from basis.db.repositories import VisibleStudy
from basis.db.repositories.llm_call import MAX_CALL_PAGE, LlmCallRepository, SpendGroup
from basis.db.session import SessionDep
from basis.llm.recorder import KINDS

DEFAULT_PAGE: Final = 50
MAX_CURSOR_LENGTH: Final = 1024
MAX_FILTER_LENGTH: Final = 200

reads = read_router(tags=["spend"])


async def spend_access(
    study_id: StudyIdPath, request: Request, user: CurrentUserDep, session: SessionDep
) -> VisibleStudy:
    """The study, when the caller's role in its firm is at least ``SPEND_ACCESS_ROLE``
    (read per request from the app's settings): 404 for a non-member, 403 below the role."""
    settings: Settings = request.app.state.settings
    role = OrgRole(settings.spend_access_role)
    return await require_study_access(session, user, study_id, role)


SpendStudy = Annotated[VisibleStudy, Depends(spend_access)]
Limit = Annotated[int, Query(ge=1, le=MAX_CALL_PAGE, description="Rows per page.")]
Cursor = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_CURSOR_LENGTH,
        description="Opaque `next_cursor` from the previous page.",
    ),
]
ActivityFilter = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_FILTER_LENGTH,
        pattern=NO_NUL_PATTERN,
        description="Only this activity.",
    ),
]
KindFilter = Annotated[
    str | None,
    Query(description=f"Only this kind: one of {', '.join(KINDS)}.", pattern="|".join(KINDS)),
]
EntityFilter = Annotated[
    uuid.UUID | None,
    Query(
        description="Only calls about this entity (a photo document, room, component or "
        "appraisal id)."
    ),
]


def line_out(group: SpendGroup) -> SpendLineOut:
    return SpendLineOut(
        key=group.key,
        calls=group.calls,
        errors=group.errors,
        input_tokens=group.input_tokens,
        cache_read_input_tokens=group.cache_read_input_tokens,
        cache_creation_input_tokens=group.cache_creation_input_tokens,
        output_tokens=group.output_tokens,
        reasoning_output_tokens=group.reasoning_output_tokens,
        cost_usd=group.cost_usd,
        latency_ms_total=group.latency_ms_total,
        latency_ms_max=group.latency_ms_max,
        quantity=group.quantity,
    )


def call_out(row: LlmCall) -> CallOut:
    return CallOut(
        id=row.id,
        created_at=row.created_at,
        kind=row.kind,
        status=row.status,
        error=row.error,
        run_id=row.run_id,
        stage=row.stage,
        revision=row.revision,
        activity=row.activity,
        activity_attempt=row.activity_attempt,
        component=row.component,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        provider=row.provider,
        endpoint=row.endpoint,
        requested_model=row.requested_model,
        model=row.model,
        response_id=row.response_id,
        request_id=row.request_id,
        finish_reason=row.finish_reason,
        prompt_version=row.prompt_version,
        prompt_name=row.prompt_name,
        prompt_sha256=row.prompt_sha256,
        reasoning_effort=row.reasoning_effort,
        max_output_tokens=row.max_output_tokens,
        temperature=row.temperature,
        response_format=row.response_format,
        image_count=row.image_count,
        image_bytes=row.image_bytes,
        request_params=row.request_params if isinstance(row.request_params, dict) else None,
        input_tokens=row.input_tokens,
        cache_read_input_tokens=row.cache_read_input_tokens,
        cache_creation_input_tokens=row.cache_creation_input_tokens,
        output_tokens=row.output_tokens,
        reasoning_output_tokens=row.reasoning_output_tokens,
        quantity=row.quantity,
        unit=row.unit,
        unit_price_usd=row.unit_price_usd,
        cost_usd=row.cost_usd,
        price_row=row.price_row,
        pricing_version=row.pricing_version,
        latency_ms=row.latency_ms,
        elapsed_ms=row.elapsed_ms,
        build_sha=row.build_sha,
        request_uri=row.request_uri,
        request_sha256=row.request_sha256,
        response_uri=row.response_uri,
        response_sha256=row.response_sha256,
    )


@reads.get(
    "/studies/{study_id}/spend",
    summary="What the study has spent, by stage, activity, component, model and kind",
    description="Firm owners by default (`SPEND_ACCESS_ROLE`; docs/api/SPEND.md): a member "
    "below that role is 403, a non-member 404.",
    responses=problems(403, 404),
)
async def get_spend(access: SpendStudy, session: SessionDep) -> StudySpendOut:
    spend = await LlmCallRepository(session).spend(access.study.id)
    return StudySpendOut(
        study_id=access.study.id,
        total=line_out(spend.total),
        by_stage=[line_out(g) for g in spend.by_stage],
        by_activity=[line_out(g) for g in spend.by_activity],
        by_component=[line_out(g) for g in spend.by_component],
        by_model=[line_out(g) for g in spend.by_model],
        by_kind=[line_out(g) for g in spend.by_kind],
    )


@reads.get(
    "/studies/{study_id}/calls",
    summary="The study's paid calls, oldest first, with their request and response references",
    description="Firm owners by default (`SPEND_ACCESS_ROLE`; docs/api/SPEND.md): a member "
    "below that role is 403, a non-member 404.",
    responses=problems(403, 404),
)
async def list_calls(
    access: SpendStudy,
    session: SessionDep,
    activity: ActivityFilter = None,
    kind: KindFilter = None,
    entity_id: EntityFilter = None,
    limit: Limit = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> CallsPageOut:
    page = await LlmCallRepository(session).page(
        access.study.id,
        limit=limit,
        cursor=cursor,
        activity=activity,
        kind=kind,
        entity_id=entity_id,
    )
    return CallsPageOut(items=[call_out(row) for row in page.items], next_cursor=page.next_cursor)
