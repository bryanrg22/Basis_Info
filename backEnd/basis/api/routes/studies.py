"""``/v1/studies``: create, list, get, archive (S1_SPEC section 5)."""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.errors import Forbidden, NotFound, Unprocessable
from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.documents import DocumentOut
from basis.api.schemas.reviews import ReviewOut
from basis.api.schemas.studies import StudyCreate, StudyDetail, StudyList, StudyOut
from basis.api.users import CurrentUser, CurrentUserDep, study_access
from basis.db.enums import OrgRole, Stage
from basis.db.repositories import (
    DocumentRepository,
    ReviewRepository,
    StudyRepository,
    UserRepository,
    VisibleStudy,
)
from basis.db.repositories.study import MAX_PAGE_SIZE
from basis.db.session import SessionDep

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

    With ``org_id``: 404 when the caller is not a member (orgs are not enumerable), 403
    when their role is too low. Without it: the single org where they may create studies;
    422 when there are several (the client must choose), 403 when there are none.
    """
    memberships = await UserRepository(session).memberships(user.id)
    if org_id is not None:
        role = memberships.get(org_id)
        if role is None:
            raise NotFound("Organization not found.")
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
    study = await StudyRepository(session).create(
        org_id, user.id, body.property_name, body.room_aliases
    )
    response.headers["Location"] = f"/v1/studies/{study.id}"
    return StudyOut.model_validate(study)


@reads.get("/studies", summary="List studies visible to you, newest first")
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
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.OWNER))], session: SessionDep
) -> StudyOut:
    study = await StudyRepository(session).archive(access.study.id)
    return StudyOut.model_validate(study)
