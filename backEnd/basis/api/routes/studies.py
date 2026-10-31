"""``/v1/studies``: create, list, get, archive (S1_SPEC section 5); the intake
(``GET`` and ``PUT /v1/studies/{id}/intake``, docs/api/STUDY_RESULT.md).

The intake is versioned (``studies.intake_version``, :mod:`basis.api.preconditions`): ``GET
.../intake`` answers it with ``ETag: "<version>"``, and ``PUT .../intake`` with ``If-Match``
writes only while that tag is still the current one (412 ``precondition-failed`` otherwise,
nothing written), so an engineer cannot overwrite a colleague's intake unknowingly. Without
``If-Match`` the write is accepted (clients that predate the tag), unless the deployment sets
``INTAKE_REQUIRE_IF_MATCH`` (428 ``precondition-required``). Every write increments the
version; ``study.intake_set`` records the intake before and after and, in ``detail``, the
version before and after and whether the write was conditional.

Invalidation: ``GET .../result`` prices with the intake as it is now and checks its pricing
against the intake version each priced row recorded (docs/api/STUDY_RESULT.md), so a client
holding a version can tell whether the numbers it shows still follow the intake it saved.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.errors import (
    Forbidden,
    NotFound,
    PreconditionFailed,
    PreconditionRequired,
    Unprocessable,
)
from basis.api.preconditions import ETAG_HEADER, intake_etag, parse_if_match
from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.documents import DocumentOut
from basis.api.schemas.reviews import ReviewOut
from basis.api.schemas.studies import (
    StudyCreate,
    StudyDetail,
    StudyIntakeOut,
    StudyList,
    StudyOut,
)
from basis.api.users import (
    DETAIL_ORG_NOT_FOUND,
    CurrentUser,
    CurrentUserDep,
    require_firm,
    study_access,
)
from basis.config import Settings
from basis.db.enums import OrgRole, Stage
from basis.db.errors import PreconditionFailedError
from basis.db.repositories import (
    DocumentRepository,
    ReviewRepository,
    StudyRepository,
    VisibleStudy,
)
from basis.db.repositories.study import MAX_PAGE_SIZE
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import SessionDep
from basis.domain.intake import StudyIntake, StudyIntakeIn, with_location
from basis.errors.codes import E

DEFAULT_PAGE_SIZE: Final = 50
MAX_CURSOR_LENGTH: Final = 512
CREATE_ROLE: Final = OrgRole.ENGINEER
_STAGE_ORDER: Final = {stage: index for index, stage in enumerate(Stage)}

reads = read_router(tags=["studies"])
writes = write_router(tags=["studies"])


async def choose_org(
    session: AsyncSession, user: CurrentUser, org_id: uuid.UUID | None
) -> uuid.UUID:
    """The org a new study belongs to; the caller needs ``engineer`` or higher there.

    No membership at all: 403 ``no-firm``. With ``org_id``: 404 when the caller is not a
    member (orgs are not enumerable), 403 when their role is too low. Without it: the
    single org where they may create studies; 422 when there are several (the client must
    choose), 403 when there are none.
    """
    memberships = await require_firm(session, user)
    if org_id is not None:
        role = memberships.get(org_id)
        if role is None:
            raise NotFound(DETAIL_ORG_NOT_FOUND, code=E.API_ORG_NOT_FOUND)
        if role.rank < CREATE_ROLE.rank:
            raise Forbidden(
                f"Creating a study needs the {CREATE_ROLE.value} role or higher in the "
                f"organization; you are {role.value}."
            )
        return org_id
    eligible = sorted(org for org, role in memberships.items() if role.rank >= CREATE_ROLE.rank)
    if len(eligible) == 1:
        return eligible[0]
    if not eligible:
        raise Forbidden("You have no organization in which you can create studies.")
    raise Unprocessable(
        "You can create studies in several organizations; set org_id.",
        type_suffix="org-required",
    )


@writes.post(
    "/studies",
    status_code=status.HTTP_201_CREATED,
    summary="Create a study",
    responses=problems(403, 404),
)
async def create_study(
    body: StudyCreate, user: CurrentUserDep, session: SessionDep, response: Response
) -> StudyOut:
    org_id = await choose_org(session, user, body.org_id)
    intake = None
    if body.intake is not None:
        # A new study has no appraisal yet: the location is the intake's or nothing.
        intake = with_location(body.intake, appraisal_state=None, appraisal_zip=None)
    study = await StudyRepository(session).create(
        org_id, user.id, body.property_name, body.room_aliases, intake
    )
    await StudyEventRepository(session).record(
        study.id,
        "study.created",
        actor="engineer",
        actor_id=user.id,
        stage=study.current_stage,
        revision=study.revision,
        detail={"intake": intake is not None},
    )
    response.headers["Location"] = f"/v1/studies/{study.id}"
    return StudyOut.model_validate(study)


async def stored_intake(
    session: AsyncSession, study_id: uuid.UUID, body: StudyIntakeIn
) -> StudyIntake:
    """The document to store: state/zip from the body, else from the appraisal's subject
    fields (the newest extraction) when they parse."""
    state, zip_code = (None, None)
    if body.state is None or body.zip is None:
        state, zip_code = await StudyRepository(session).subject_location(study_id)
    return with_location(body, appraisal_state=state, appraisal_zip=zip_code)


ETAG_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "headers": {
            ETAG_HEADER: {
                "description": 'The intake\'s strong entity tag, its version in quotes ("3").',
                "schema": {"type": "string"},
            }
        }
    }
}
IfMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-Match",
        description='The intake\'s entity tag from `GET .../intake` (`"3"`), or `*` (any '
        "existing intake). Optional unless the deployment requires it.",
    ),
]


@reads.get(
    "/studies/{study_id}/intake",
    summary="The study's intake and its version (ETag)",
    description='Viewer role. `ETag: "<intake_version>"` (a strong entity tag; 0 while there '
    "is no intake): send it back as `If-Match` on `PUT .../intake` so the write fails with "
    "412 instead of overwriting a change made since this read (docs/api/STUDY_RESULT.md).",
    responses={**problems(404), **ETAG_RESPONSE},
)
async def get_intake(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))], response: Response
) -> StudyIntakeOut:
    study = access.study
    response.headers[ETAG_HEADER] = intake_etag(study.intake_version)
    return StudyIntakeOut(
        study_id=study.id, intake=study.intake, intake_version=study.intake_version
    )


@writes.put(
    "/studies/{study_id}/intake",
    summary="Set or replace the study's intake (engineer)",
    description="The whole intake, validated as one document (docs/api/STUDY_RESULT.md): a "
    "non-capitalizable cost category, a land method without its input, or a use that fails "
    "the 80 % test is a 422 naming the rule. `state`/`zip` left out are filled from the "
    "appraisal's subject fields. No `Idempotency-Key` is needed. Optimistic concurrency: "
    "with `If-Match` (the `ETag` of `GET .../intake`) the write happens only while that tag "
    "is current; otherwise 412 `precondition-failed` with the current `ETag`, nothing "
    "written (re-read, reapply, retry). `If-Match: *` writes only over an existing intake. "
    "Without `If-Match` the write is accepted, unless the deployment requires it "
    "(`INTAKE_REQUIRE_IF_MATCH`: 428 `precondition-required`). Every write increments "
    "`intake_version`; the response carries the new `ETag`. The result's pricing is checked "
    "against the intake version. 409 `stale-state` on an archived study.",
    responses={**problems(403, 404, 409, 412, 428), **ETAG_RESPONSE},
)
async def put_intake(
    body: StudyIntakeIn,
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.ENGINEER))],
    user: CurrentUserDep,
    session: SessionDep,
    request: Request,
    response: Response,
    if_match: IfMatchHeader = None,
) -> StudyOut:
    settings: Settings = request.app.state.settings
    condition = parse_if_match(if_match)
    if condition is None and settings.intake_require_if_match:
        raise PreconditionRequired(
            "This deployment requires If-Match on intake writes: read the intake with GET "
            "/v1/studies/{id}/intake and send its ETag as If-Match.",
            code=E.STUDY_INTAKE_PRECONDITION_REQUIRED,
        )
    intake = await stored_intake(session, access.study.id, body)
    try:
        written = await StudyRepository(session).write_intake(
            access.study.id,
            intake,
            precondition=None if condition is None else condition.allows,
        )
    except PreconditionFailedError as exc:
        current = intake_etag(exc.version)
        if condition is not None and condition.any_current:
            detail = (
                "If-Match: * needs an existing intake and this study has none; send the PUT "
                "without If-Match to create it."
            )
        else:
            detail = (
                f"The intake changed since you read it (its ETag is now {current}); nothing "
                "was written. Read it again with GET /v1/studies/{id}/intake, reapply your "
                "change, and send the new ETag as If-Match."
            )
        raise PreconditionFailed(
            detail, headers={ETAG_HEADER: current}, code=E.STUDY_INTAKE_PRECONDITION_FAILED
        ) from exc
    study = written.study
    await StudyEventRepository(session).record(
        study.id,
        "study.intake_set",
        actor="engineer",
        actor_id=user.id,
        stage=study.current_stage,
        revision=study.revision,
        entity_type="study",
        entity_id=study.id,
        before=None if written.before is None else written.before.model_dump(mode="json"),
        after=intake.model_dump(mode="json"),
        detail={
            "version_before": written.version_before,
            "version_after": study.intake_version,
            "if_match": condition is not None,
        },
    )
    response.headers[ETAG_HEADER] = intake_etag(study.intake_version)
    return StudyOut.model_validate(study)


@reads.get(
    "/studies",
    summary="List studies visible to you, newest first",
    description="403 `no-firm` when you are not a member of any firm (see `GET /v1/me`).",
    responses=problems(403),
)
async def list_studies(
    user: CurrentUserDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size.")] = (
        DEFAULT_PAGE_SIZE
    ),
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_CURSOR_LENGTH,
            description="Opaque `next_cursor` from the previous page.",
        ),
    ] = None,
) -> StudyList:
    page = await StudyRepository(session).list_for_user(user.id, limit, cursor)
    if not page.items:
        # Only a member of no firm at all sees no studies for that reason; tell them so.
        await require_firm(session, user)
    return StudyList(
        items=[StudyOut.model_validate(study) for study in page.items],
        next_cursor=page.next_cursor,
    )


@reads.get(
    "/studies/{study_id}",
    summary="Get a study with its documents and latest review per stage",
    responses=problems(404),
)
async def get_study(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))], session: SessionDep
) -> StudyDetail:
    study = access.study
    documents = await DocumentRepository(session).list_for_study(study.id)
    latest = await ReviewRepository(session).latest_by_stage(study.id)
    reviews = [latest[stage] for stage in sorted(latest, key=_STAGE_ORDER.__getitem__)]
    return StudyDetail(
        **StudyOut.model_validate(study).model_dump(),
        documents=[DocumentOut.model_validate(document) for document in documents],
        latest_reviews=[ReviewOut.model_validate(review) for review in reviews],
    )


@writes.delete(
    "/studies/{study_id}",
    summary="Archive a study (soft delete; owner only)",
    responses=problems(403, 404),
)
async def archive_study(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.OWNER))],
    user: CurrentUserDep,
    session: SessionDep,
) -> StudyOut:
    study = await StudyRepository(session).archive(access.study.id)
    await StudyEventRepository(session).record(
        study.id,
        "study.archived",
        actor="engineer",
        actor_id=user.id,
        stage=study.current_stage,
        revision=study.revision,
    )
    return StudyOut.model_validate(study)
