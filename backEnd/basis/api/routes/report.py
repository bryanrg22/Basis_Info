"""The study report and the preparer's schedule (STUDY_REPORT_SPEC 11.2): the files the
export step wrote next to the export, served to anyone who can view the study.

The revision defaults to the latest one exported. Each file's sha256 is the one recorded
on the ``export.written`` event, so it is the ``ETag`` and a repeated request answers 304.
When the export skipped the report (no allocation to report, ``E.REPORT_NOT_PRODUCED``), the
answer is a 404 naming the reason.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from fastapi import Depends, Query, Request, Response, status

from basis.api.errors import NotFound
from basis.api.routes.common import get_storage, problems, read_router
from basis.api.users import CurrentUserDep, SessionmakerDep, StudyIdPath, require_study_access
from basis.db.enums import OrgRole
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.storage import ObjectNotFound, Storage

reads = read_router(tags=["report"])
StorageDep = Annotated[Storage, Depends(get_storage)]
Format = Literal["pdf", "csv", "xlsx"]
FILES: Final[dict[str, tuple[str, str]]] = {
    "pdf": ("report.pdf", "application/pdf"),
    "csv": ("schedule.csv", "text/csv; charset=utf-8"),
    "xlsx": (
        "schedule.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}
CACHE_CONTROL: Final = "private, max-age=3600"


@reads.get(
    "/studies/{study_id}/report",
    summary="The study report (PDF) or the preparer's depreciation schedule (CSV, XLSX)",
    response_class=Response,
    responses={
        200: {"description": "The file, with `Content-Disposition: attachment`."},
        304: {"description": "`If-None-Match` matched the file's sha256."},
        **problems(404),
    },
)
async def study_report(
    request: Request,
    study_id: StudyIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
    format: Annotated[
        Format, Query(description="pdf (the report), csv or xlsx (the schedule)")
    ] = "pdf",
    revision: Annotated[
        int | None, Query(ge=1, le=10_000, description="Default: the latest exported revision.")
    ] = None,
) -> Response:
    suffix, content_type = FILES[format]
    async with session_scope(sessionmaker) as session:
        await require_study_access(session, user, study_id, OrgRole.VIEWER)
        events = (
            await StudyEventRepository(session).list_for_study(
                study_id, event_types=["export.written"]
            )
        ).items
    written = [
        e
        for e in events
        if e.event_type == "export.written" and (revision is None or e.revision == revision)
    ]
    if not written:
        raise NotFound("No export for this study and revision.", code=E.REPORT_NOT_FOUND)
    latest = written[-1]
    report = (latest.detail or {}).get("report") or {}
    if "skipped" in report:
        skipped = report["skipped"]
        raise NotFound(
            f"The export has no report: {skipped.get('message')}",
            code=str(skipped.get("code") or E.REPORT_NOT_FOUND),
        )
    entry = (report.get("files") or {}).get(suffix)
    if not entry:
        raise NotFound("The export has no report files.", code=E.REPORT_NOT_FOUND)
    etag = f'"{entry["sha256"]}"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    try:
        data = await storage.read(entry["uri"])
    except ObjectNotFound:
        raise NotFound(
            "The report's bytes are missing from storage.", code=E.REPORT_NOT_FOUND
        ) from None
    name = f"study-{str(study_id)[:8]}-rev-{latest.revision}-{suffix}"
    headers["Content-Disposition"] = f'attachment; filename="{name}"'
    headers["X-Content-Type-Options"] = "nosniff"
    return Response(content=data, media_type=content_type, headers=headers)
