"""The study's renovation projects (docs/api/RENOVATIONS.md, EVAL_REPORT section 102).

``GET /v1/studies/{id}/renovations`` (viewer) and ``PUT`` (engineer): one document written
whole, versioned like the intake (``ETag``/``If-Match``), refused once the current revision
is exported (409 ``renovations-locked``: reopen to change it). The body's own rules are
:class:`basis.domain.renovations.StudyRenovationsIn` (a 422 before anything is read); then the
files it names must be the study's (invoices of kind ``other``, photographs of kind ``photo``)
and its dates agree with the intake's (422 ``E.STUDY_RENOVATIONS_DOCUMENT``).

What the projects change is read at ``GET .../result``: the rows of a project's photographs
leave the acquisition once the assets are built again (the result names the reprice to run).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Response
from sqlalchemy import select

from basis.api.errors import Conflict, FieldError, PreconditionFailed, Unprocessable
from basis.api.preconditions import ETAG_HEADER, intake_etag, parse_if_match
from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.renovations import RenovationsOut
from basis.api.users import CurrentUserDep, study_access
from basis.db.enums import DocumentKind, OrgRole
from basis.db.errors import PreconditionFailedError, RenovationsLockedError, StaleStateError
from basis.db.models import Document
from basis.db.repositories.study import StudyRepository, VisibleStudy
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import SessionDep
from basis.domain.renovations import StudyRenovations, StudyRenovationsIn, date_problems
from basis.errors.codes import E

reads = read_router(tags=["renovations"])
writes = write_router(tags=["renovations"])

IfMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-Match",
        description='The entity tag from `GET .../renovations` (`"3"`), or `*` (any existing '
        "document). Optional.",
    ),
]


def _out(access_study: object) -> RenovationsOut:
    return RenovationsOut.model_validate(
        {
            "study_id": access_study.id,  # type: ignore[attr-defined]
            "renovations": access_study.renovations,  # type: ignore[attr-defined]
            "renovations_version": access_study.renovations_version,  # type: ignore[attr-defined]
        }
    )


@reads.get(
    "/studies/{study_id}/renovations",
    summary="The study's renovation projects and their version (ETag)",
    description="Viewer role. `renovations` is null until an engineer saves them; the `ETag` "
    'is `"0"` then.',
    responses=problems(404),
)
async def get_renovations(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))], response: Response
) -> RenovationsOut:
    response.headers[ETAG_HEADER] = intake_etag(access.study.renovations_version)
    return _out(access.study)


@writes.put(
    "/studies/{study_id}/renovations",
    summary="Set or replace the study's renovation projects (engineer)",
    description=(
        "The projects (after or before the building was placed in service), their dates, "
        "invoices, the CPA's capitalized amount and the photographs tagged after each, the bonus "
        "the CPA says was claimed on the acquisition, and exterior components' dated views. "
        "`If-Match` as on the intake (412 `{STUDY_RENOVATIONS_PRECONDITION_FAILED}`). 409 "
        "`renovations-locked` (`{STUDY_RENOVATIONS_LOCKED}`) once the current revision is "
        "exported; 409 `stale-state` on an archived study; 422 `{STUDY_RENOVATIONS_DOCUMENT}` "
        "for a file that is not the study's or dates that contradict the intake."
    ).format_map({k: v for k, v in vars(E).items() if not k.startswith("_")}),
    responses=problems(403, 404, 409, 412, 422),
)
async def put_renovations(
    body: StudyRenovationsIn,
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.ENGINEER))],
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> RenovationsOut:
    condition = parse_if_match(if_match)
    study = access.study
    errors: list[FieldError] = []
    invoices, photos = body.document_ids()
    wanted = invoices | photos
    kinds: dict[object, DocumentKind] = {}
    if wanted:
        rows = (
            await session.execute(
                select(Document.id, Document.kind).where(
                    Document.study_id == study.id, Document.id.in_(wanted)
                )
            )
        ).all()
        kinds = {row.id: row.kind for row in rows}
    for index, project in enumerate(body.projects):
        for i, invoice in enumerate(project.invoices):
            if kinds.get(invoice.document_id) is not DocumentKind.OTHER:
                errors.append(
                    FieldError(
                        loc=["body", "projects", index, "invoices", i, "document_id"],
                        detail=f"{invoice.document_id} is not a document of this study of kind "
                        "other (upload the invoice first)",
                        type="renovation_document",
                    )
                )
        for i, photo in enumerate(project.after_photos):
            if kinds.get(photo) is not DocumentKind.PHOTO:
                errors.append(
                    FieldError(
                        loc=["body", "projects", index, "after_photos", i],
                        detail=f"{photo} is not a photograph of this study",
                        type="renovation_document",
                    )
                )
    intake = study.intake
    for problem in date_problems(
        body,
        building_placed_in_service=None if intake is None else intake.placed_in_service_date,
        acquisition_date=None if intake is None else intake.acquisition_date,
    ):
        errors.append(
            FieldError(loc=["body", problem.field], detail=problem.message, type="renovation_date")
        )
    if errors:
        raise Unprocessable(
            "The renovations name files that are not the study's, or dates the intake "
            "contradicts; nothing was written.",
            type_suffix="validation",
            errors=errors,
            code=E.STUDY_RENOVATIONS_DOCUMENT,
        )
    document = StudyRenovations.model_validate(body.model_dump())
    try:
        written = await StudyRepository(session).write_renovations(
            study.id, document, precondition=None if condition is None else condition.allows
        )
    except StaleStateError as exc:
        raise Conflict(
            "The study is archived; its renovations cannot change.",
            type_suffix="stale-state",
            code=E.STUDY_STALE_STATE,
        ) from exc
    except RenovationsLockedError as exc:
        raise Conflict(
            "The study's current revision has been exported with these renovations; reopen it "
            "at the assets review to change them.",
            type_suffix="renovations-locked",
            code=E.STUDY_RENOVATIONS_LOCKED,
        ) from exc
    except PreconditionFailedError as exc:
        current = intake_etag(exc.version)
        raise PreconditionFailed(
            f"The renovations changed since you read them (their ETag is now {current}); "
            "nothing was written. Read them again, reapply your change, and send the new ETag.",
            headers={ETAG_HEADER: current},
            code=E.STUDY_RENOVATIONS_PRECONDITION_FAILED,
        ) from exc
    saved = written.study
    await StudyEventRepository(session).record(
        saved.id,
        "study.renovations_set",
        actor="engineer",
        actor_id=user.id,
        stage=saved.current_stage,
        revision=saved.revision,
        entity_type="study",
        entity_id=saved.id,
        before=None if written.before is None else written.before.model_dump(mode="json"),
        after=document.model_dump(mode="json"),
        detail={
            "version_before": written.version_before,
            "version_after": saved.renovations_version,
            "if_match": condition is not None,
        },
    )
    response.headers[ETAG_HEADER] = intake_etag(saved.renovations_version)
    return _out(saved)


__all__ = ["reads", "writes"]
