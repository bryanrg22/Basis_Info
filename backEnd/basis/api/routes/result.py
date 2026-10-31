"""``GET /v1/studies/{id}/result``: the basis worksheet and the allocated schedule of a
revision (docs/api/STUDY_RESULT.md).

Computed from the study's intake, the revision's priced assets and the appraisal's
structure inputs priced as the revision was priced (the year, location and square-foot model
recorded on its cost rows: ``pricing``; the same function writes the export's
``summary.basis`` at ``export_report``). An intake or index changed after the pricing makes
``pricing.stale`` true and withholds the allocation until a reprice; it never mixes a newer
index into the stored rows' figures. Viewer access; ``revision``
as the other output reads (an earlier revision is history; a later one is 404). Without an
intake the schedule alone is returned and ``missing`` says so.
"""

from __future__ import annotations

from basis.api.routes.common import problems, read_router
from basis.api.routes.outputs import RevisionQuery, ViewerStudy, _revision
from basis.api.schemas.result import StudyResultOut
from basis.config import get_settings
from basis.db.session import SessionDep
from basis.evidence.result import compute_result, load_result_inputs, load_schedule

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
    settings = get_settings()
    structure = pricing = None
    if settings.structure_step == "on":
        structure, pricing = await load_result_inputs(
            session,
            access.study.id,
            at,
            access.study.intake,
            access.study.intake_version,
            settings,
        )
    return compute_result(rows, access.study.intake, at, structure, pricing)
