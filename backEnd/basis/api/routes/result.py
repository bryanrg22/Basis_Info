"""``GET /v1/studies/{id}/result``: the basis worksheet and the allocated schedule of a
revision (docs/api/STUDY_RESULT.md).

Computed live from the study's intake and the revision's priced assets (the same function
writes the export's ``summary.basis`` at ``export_report``), so an intake set after the
export changes what this route answers, not the stored export. Viewer access; ``revision``
as the other output reads (an earlier revision is history; a later one is 404). Without an
intake the schedule alone is returned and ``missing`` says so.
"""

from __future__ import annotations

from basis.api.routes.common import problems, read_router
from basis.api.routes.outputs import RevisionQuery, ViewerStudy, _revision
from basis.api.schemas.result import StudyResultOut
from basis.db.session import SessionDep
from basis.evidence.result import compute_result, load_schedule

reads = read_router(tags=["outputs"])


@reads.get(
    "/studies/{study_id}/result",
    summary="The basis worksheet and the allocated schedule at a revision",
    responses=problems(404),
)
async def get_result(
    access: ViewerStudy, session: SessionDep, revision: RevisionQuery = None
) -> StudyResultOut:
    at = _revision(access, revision)
    rows = await load_schedule(session, access.study.id, at)
    return compute_result(rows, access.study.intake, at)
