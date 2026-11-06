"""The study report, its inputs and the engineer's signature (STUDY_REPORT_SPEC 11.2, 11.13;
docs/api/REPORT.md).

* ``GET .../report``: the files the export step wrote next to the export, served to anyone
  who can view the study. The revision defaults to the latest one exported. Each file's
  sha256 is the one recorded (on ``export.written``, or the signature), so it is the
  ``ETag`` and a repeated request answers 304. ``format=pdf`` serves the signed report when
  the revision is signed (``variant=draft`` asks for the draft). When the export skipped
  the report (no allocation to report, ``E.REPORT_NOT_PRODUCED``), the answer is a 404
  naming the reason. ``profile=<vendor>`` with ``csv`` or ``xlsx`` renders the schedule in
  a vendor's import layout now, from the stored export (:mod:`basis.report.vendors`); its
  ``ETag`` is the sha256 of the bytes, which are deterministic.
* The inputs: the firm's profile (``/orgs/{id}/profile``, owners write) and the study's
  answers (``.../report-answers``, engineers write), each written whole with ``If-Match``
  on its version, as the intake is (:mod:`basis.api.preconditions`).
* ``GET .../report/state``: what the current revision's report still lacks.
* ``GET .../report/preview``: the current revision's report rendered now with the stored
  inputs and no signature; ``ETag`` is the sha256 of the bytes.
* ``POST .../report/sign``: the engineer signs the current revision. The preview rendered
  again must have the sha256 the engineer reviewed (rendering is deterministic), so the
  signer signs exactly what they saw; the final PDF, the signature row and the
  ``report.signed`` event are written together under the study's row lock.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from basis.activities.export_report import build_report_files, report_uri
from basis.api.errors import Conflict, NotFound, PreconditionFailed
from basis.api.preconditions import ETAG_HEADER, intake_etag, parse_if_match
from basis.api.routes.common import (
    get_storage,
    get_storage_bucket,
    problems,
    read_router,
    write_router,
)
from basis.api.schemas.report import (
    FirmProfileOut,
    ReportAnswersOut,
    ReportSkip,
    ReportStateOut,
    SignatureOut,
    SignIn,
)
from basis.api.users import (
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    org_access,
    require_study_access,
    study_access,
)
from basis.config import Settings
from basis.db.enums import OrgRole, StudyStatus
from basis.db.errors import ConflictError, PreconditionFailedError, StaleStateError
from basis.db.models import SignerDetailsRow, Study, StudyEvent
from basis.db.repositories.report import ReportRepository
from basis.db.repositories.signer import SignerRepository
from basis.db.repositories.study import VisibleStudy
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.repositories.user import UserRepository
from basis.db.session import SessionDep, session_scope
from basis.errors.codes import E
from basis.evidence.export import sha256_hex
from basis.report import vendors
from basis.report.inputs import (
    CERTIFICATION_VERSION,
    Engineer,
    FirmProfile,
    ReportInputs,
    Signature,
    SignerDetails,
    StudyAnswers,
    missing_for_final,
)
from basis.report.model import ReportError, build_report
from basis.report.schedule import rows
from basis.storage import ObjectNotFound, Storage

reads = read_router(tags=["report"])
writes = write_router(tags=["report"])
StorageDep = Annotated[Storage, Depends(get_storage)]
BucketDep = Annotated[str, Depends(get_storage_bucket)]
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
PDF: Final = "application/pdf"
SIGNED_EVENT: Final = "report.signed"
IfMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-Match",
        description='The entity tag from the matching `GET` (`"3"`), or `*` (any existing '
        "document). Optional.",
    ),
]


def _codes(text: str) -> str:
    """A description naming error codes by their ``E`` names (``{REPORT_CHANGED}``)."""
    return text.format_map({k: v for k, v in vars(E).items() if not k.startswith("_")})


def _etag_matches(request: Request, etag: str) -> bool:
    return etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]


async def _exports(session: AsyncSession, study_id: uuid.UUID) -> list[StudyEvent]:
    events = await StudyEventRepository(session).list_for_study(
        study_id, event_types=["export.written"]
    )
    return [e for e in events.items if e.event_type == "export.written"]


def _report_detail(event: StudyEvent) -> dict[str, Any]:
    report: dict[str, Any] = (event.detail or {}).get("report") or {}
    return report


async def _profile_file(
    storage: Storage, event: StudyEvent, profile: vendors.Profile, format: str
) -> bytes:
    """The schedule of the export ``event`` wrote, in ``profile``'s layout, rendered now."""
    try:
        data = await storage.read(str((event.detail or {}).get("uri")))
    except ObjectNotFound:
        raise NotFound(
            "The export's bytes are missing from storage.", code=E.REPORT_NOT_FOUND
        ) from None
    try:
        schedule = rows(build_report(json.loads(data)))
    except ReportError as exc:
        raise NotFound(f"The export has no schedule: {exc}", code=exc.code) from exc
    if format == "csv":
        return vendors.to_csv(schedule, profile)
    return vendors.to_xlsx(schedule, profile)


# --------------------------------------------------------------------- the files


@reads.get(
    "/studies/{study_id}/report",
    summary="The study report (PDF) or the preparer's depreciation schedule (CSV, XLSX)",
    description="Viewer role. `format=pdf` is the signed report when the revision is "
    "signed, else the draft the export wrote; `variant=draft` always asks for the draft. "
    "`profile` (csv and xlsx; ignored for pdf) is the schedule's import layout: `generic` is "
    "the file the export wrote; a vendor profile is rendered now from the stored export "
    "(docs/api/REPORT.md lists them; none is verified against the vendor's software yet).",
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
    variant: Annotated[
        Literal["final", "draft"],
        Query(description="pdf only: `final` (the signed report when there is one) or `draft`."),
    ] = "final",
    profile: Annotated[
        vendors.ProfileName,
        Query(
            description="csv and xlsx only (ignored for pdf): the import layout (`generic`, "
            "`fixed_assets_cs`, `sage`, `proconnect`, `cch`, `bloomberg`)."
        ),
    ] = "generic",
) -> Response:
    suffix, content_type = FILES[format]
    layout = vendors.GENERIC if format == "pdf" else vendors.PROFILES[profile]
    async with session_scope(sessionmaker) as session:
        await require_study_access(session, user, study_id, OrgRole.VIEWER)
        written = [e for e in await _exports(session, study_id) if revision in (None, e.revision)]
        latest = written[-1] if written else None
        signature = (
            await ReportRepository(session).signature(study_id, latest.revision)
            if latest is not None
            and latest.revision is not None
            and format == "pdf"
            and variant == "final"
            else None
        )
    if latest is None:
        raise NotFound("No export for this study and revision.", code=E.REPORT_NOT_FOUND)
    report = _report_detail(latest)
    entry: dict[str, Any] | None
    if signature is not None:
        entry = {"uri": signature.pdf_uri, "sha256": signature.pdf_sha256}
        suffix = "report-signed.pdf"
    else:
        if "skipped" in report:
            skipped = report["skipped"]
            raise NotFound(
                f"The export has no report: {skipped.get('message')}",
                code=str(skipped.get("code") or E.REPORT_NOT_FOUND),
            )
        entry = (report.get("files") or {}).get(suffix)
        if not entry:
            raise NotFound("The export has no report files.", code=E.REPORT_NOT_FOUND)
    rendered: bytes | None = None
    if layout is not vendors.GENERIC:
        rendered = await _profile_file(storage, latest, layout, format)
        etag = f'"{sha256_hex(rendered)}"'
        suffix = f"schedule-{layout.name}.{format}"
    else:
        etag = f'"{entry["sha256"]}"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    if rendered is not None:
        data = rendered
    else:
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


# --------------------------------------------------------------------- firm profile


@reads.get(
    "/orgs/{org_id}/profile",
    summary="The firm's report profile: letterhead and signing engineer (members)",
    description=_codes(
        '`ETag: "<version>"`: send it back as `If-Match` on the `PUT`. 404 '
        "{REPORT_PROFILE_NOT_FOUND} while the firm has none."
    ),
    responses=problems(403, 404),
)
async def get_firm_profile(
    org_id: Annotated[uuid.UUID, Depends(org_access(OrgRole.VIEWER))],
    session: SessionDep,
    response: Response,
) -> FirmProfileOut:
    row = await ReportRepository(session).firm_profile(org_id)
    if row is None:
        raise NotFound("The firm has no report profile yet.", code=E.REPORT_PROFILE_NOT_FOUND)
    response.headers[ETAG_HEADER] = intake_etag(row.version)
    return FirmProfileOut.model_validate(row)


@writes.put(
    "/orgs/{org_id}/profile",
    summary="Set or replace the firm's report profile (owner)",
    description=_codes(
        "The whole profile (`logo_png` as base64, a PNG of at most 512 KB). "
        "`If-Match` with the `ETag` of the `GET` writes only while it is current (412 "
        "{REPORT_PROFILE_PRECONDITION_FAILED} with the current `ETag` otherwise); `*` only "
        "over an existing profile. A signed report keeps the profile it was signed with; "
        "`GET .../report/state` of a signed study then lists `firm` in "
        "`changed_since_signing`."
    ),
    responses=problems(403, 404, 412),
)
async def put_firm_profile(
    body: FirmProfile,
    org_id: Annotated[uuid.UUID, Depends(org_access(OrgRole.OWNER))],
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> FirmProfileOut:
    condition = parse_if_match(if_match)
    try:
        written = await ReportRepository(session).write_firm_profile(
            org_id, body, user.id, precondition=None if condition is None else condition.allows
        )
    except PreconditionFailedError as exc:
        current = intake_etag(exc.version)
        raise PreconditionFailed(
            f"The firm profile changed since you read it (its ETag is now {current}); nothing "
            "was written. Read it again, reapply your change, and send the new ETag.",
            headers={ETAG_HEADER: current},
            code=E.REPORT_PROFILE_PRECONDITION_FAILED,
        ) from exc
    response.headers[ETAG_HEADER] = intake_etag(written.row.version)
    return FirmProfileOut.model_validate(written.row)


# --------------------------------------------------------------------- study answers


@reads.get(
    "/studies/{study_id}/report-answers",
    summary="The study's answers for the report and their version (ETag)",
    description="Viewer role. `report_answers` is null until an engineer saves them; the "
    '`ETag` is `"0"` then.',
    responses=problems(404),
)
async def get_report_answers(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))], response: Response
) -> ReportAnswersOut:
    study = access.study
    response.headers[ETAG_HEADER] = intake_etag(study.report_answers_version)
    return ReportAnswersOut(
        study_id=study.id,
        report_answers=study.report_answers,
        report_answers_version=study.report_answers_version,
    )


@writes.put(
    "/studies/{study_id}/report-answers",
    summary="Set or replace the study's answers for the report (engineer)",
    description=_codes(
        "The taxpayer, who photographed the property and when (an engineer's site "
        "visit, or photographs the taxpayer supplied: the report states which), interviews and "
        "prior depreciation. `If-Match` as on the intake "
        "(412 {REPORT_ANSWERS_PRECONDITION_FAILED}). 409 `stale-state` on an archived study. "
        "A signed revision keeps the answers it was signed with; `GET .../report/state` then "
        "lists `study` in `changed_since_signing`."
    ),
    responses=problems(403, 404, 409, 412),
)
async def put_report_answers(
    body: StudyAnswers,
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.ENGINEER))],
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> ReportAnswersOut:
    condition = parse_if_match(if_match)
    try:
        written = await ReportRepository(session).write_answers(
            access.study.id, body, precondition=None if condition is None else condition.allows
        )
    except StaleStateError as exc:
        raise Conflict(
            "The study is archived; its report answers cannot change.",
            type_suffix="stale-state",
            code=E.STUDY_STALE_STATE,
        ) from exc
    except PreconditionFailedError as exc:
        current = intake_etag(exc.version)
        raise PreconditionFailed(
            f"The report answers changed since you read them (their ETag is now {current}); "
            "nothing was written. Read them again, reapply your change, and send the new ETag.",
            headers={ETAG_HEADER: current},
            code=E.REPORT_ANSWERS_PRECONDITION_FAILED,
        ) from exc
    study = written.study
    await StudyEventRepository(session).record(
        study.id,
        "study.report_answers_set",
        actor="engineer",
        actor_id=user.id,
        stage=study.current_stage,
        revision=study.revision,
        entity_type="study",
        entity_id=study.id,
        before=None if written.before is None else written.before.model_dump(mode="json"),
        after=body.model_dump(mode="json"),
        detail={
            "version_before": written.version_before,
            "version_after": study.report_answers_version,
            "if_match": condition is not None,
        },
    )
    response.headers[ETAG_HEADER] = intake_etag(study.report_answers_version)
    return ReportAnswersOut(
        study_id=study.id,
        report_answers=study.report_answers,
        report_answers_version=study.report_answers_version,
    )


# --------------------------------------------------------------------- state, preview, sign


@dataclass(frozen=True, slots=True)
class CurrentExport:
    event: StudyEvent
    json: bytes


async def _current_export(session: AsyncSession, storage: Storage, study: Study) -> CurrentExport:
    """The export of the study's current revision with a report; 409 ``E.REPORT_NOT_SIGNABLE``
    otherwise (archived, not exported, the report skipped, the bytes gone)."""
    if study.status is StudyStatus.ARCHIVED:
        raise Conflict("The study is archived.", code=E.REPORT_NOT_SIGNABLE)
    written = [e for e in await _exports(session, study.id) if e.revision == study.revision]
    if not written:
        raise Conflict(
            f"Revision {study.revision} has not been exported yet: approve the assets review "
            "so the export runs.",
            code=E.REPORT_NOT_SIGNABLE,
        )
    event = written[-1]
    skipped = _report_detail(event).get("skipped")
    if skipped:
        raise Conflict(
            f"The export of revision {study.revision} has no report: {skipped.get('message')}",
            code=E.REPORT_NOT_SIGNABLE,
        )
    try:
        data = await storage.read(str((event.detail or {}).get("uri")))
    except ObjectNotFound:
        raise Conflict(
            "The export's bytes are missing from storage; export again.",
            code=E.REPORT_NOT_SIGNABLE,
        ) from None
    return CurrentExport(event=event, json=data)


async def _render_pdf(
    session: AsyncSession,
    storage: Storage,
    export: CurrentExport,
    study: Study,
    inputs: ReportInputs,
) -> bytes:
    files = await build_report_files(
        session, storage, export.json, study.id, study.revision, inputs
    )
    if files.skipped is not None:
        raise Conflict(
            f"The report could not be rendered: {files.skipped[1]}", code=files.skipped[0]
        )
    return files.files["report.pdf"]


@dataclass(frozen=True, slots=True)
class Signer:
    """What a signature prints and where it came from: ``version`` is the saved details'
    version, ``None`` when the details were entered at signing."""

    engineer: Engineer
    version: int | None


def _signer(saved: SignerDetailsRow | None, sent: Engineer | None, *, required: bool) -> Signer:
    """The details a signing prints (EVAL_REPORT 91). The signer's own saved details always
    win; an ``engineer`` sent with them must name the same details (the signer attests to what
    they sent, so printing anything else would print what they did not attest). Without saved
    details, the ``engineer`` sent is printed as before, unless the deployment requires saved
    details. Never the firm profile's engineer: that would print one licensee's name and
    license on another's signature."""
    if saved is not None:
        details = saved.details()
        if sent is not None and not details.matches(sent):
            raise Conflict(
                "The name, credentials or license you signed with differ from your saved "
                "signer details, which are what the report prints. Sign with your saved "
                "details, or change them first and open the preview again.",
                code=E.REPORT_SIGNER_DETAILS_MISMATCH,
            )
        return Signer(engineer=details.engineer(), version=saved.version)
    if sent is not None and not required:
        return Signer(engineer=sent, version=None)
    raise Conflict(
        "Save your signer details (your name, credentials and license) in your account "
        "first: every report you sign prints them. Then open the preview again and sign.",
        code=E.REPORT_SIGNER_DETAILS_MISSING,
    )


async def _signature_out(session: AsyncSession, row: Any) -> SignatureOut:
    """The signature with the signer's name and email next to ``signed_by``."""
    who = (await UserRepository(session).identities([row.signed_by])).get(row.signed_by)
    return SignatureOut.model_validate(row).model_copy(
        update={
            "signed_by_name": who.display_name if who else None,
            "signed_by_email": who.email if who else None,
        }
    )


@reads.get(
    "/studies/{study_id}/report/state",
    summary="Where the current revision's report stands: exported, missing, signed",
    description="Viewer role. Once the revision is signed, `changed_since_signing` lists "
    "the inputs written after the signature (`firm`, `study`): the signed PDF is what was "
    "signed, and the stored inputs no longer match it (docs/api/REPORT.md).",
    responses=problems(404),
)
async def report_state(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))], session: SessionDep
) -> ReportStateOut:
    study = access.study
    repo = ReportRepository(session)
    written = [e for e in await _exports(session, study.id) if e.revision == study.revision]
    report = _report_detail(written[-1]) if written else {}
    signature = await repo.signature(study.id, study.revision)
    inputs = await repo.inputs(study)
    firm = await repo.firm_profile(study.org_id)
    firm_version = firm.version if firm else 0
    missing = list(missing_for_final(inputs))
    changed: list[Any] | None = None
    signer_now: SignerDetails | None = None
    if signature is not None:
        missing = [m for m in missing if m != "signature"]
        saved = await SignerRepository(session).get(signature.signed_by)
        signer_now = saved.details() if saved else None
        changed = repo.changed_since_signing(
            signature,
            firm_profile_version=firm_version,
            report_answers_version=study.report_answers_version,
            signer_now=signer_now,
        )
    skipped = report.get("skipped")
    return ReportStateOut(
        study_id=study.id,
        revision=study.revision,
        exported=bool(written),
        skipped=ReportSkip(code=skipped["code"], message=skipped["message"]) if skipped else None,
        files=sorted((report.get("files") or {}).keys()),
        missing_for_final=missing,
        signature=None if signature is None else await _signature_out(session, signature),
        firm_profile_version=firm_version,
        report_answers_version=study.report_answers_version,
        changed_since_signing=changed,
        signer_details=signer_now,
    )


@reads.get(
    "/studies/{study_id}/report/preview",
    summary="The current revision's report rendered now, unsigned (what the engineer signs)",
    description=_codes(
        "Viewer role. The report as it would be signed, from the current "
        "revision's export and the stored inputs, without the signature block (a DRAFT until "
        "signed). It names you as the preparer when you may sign and have saved signer "
        "details (`PUT /v1/me/signer`), since that is the report you would sign; otherwise the "
        "firm profile's engineer, or a placeholder. `ETag` is the sha256 of the bytes: "
        "`POST .../report/sign` takes it as `reviewed_sha256`. 409 {REPORT_NOT_SIGNABLE} when "
        "the current revision has no report."
    ),
    response_class=Response,
    responses={200: {"description": "The PDF, inline."}, **problems(404, 409)},
)
async def report_preview(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))],
    user: CurrentUserDep,
    session: SessionDep,
    storage: StorageDep,
) -> Response:
    study = access.study
    export = await _current_export(session, storage, study)
    saved = (
        await SignerRepository(session).get(user.id)
        if access.role.rank >= OrgRole.ENGINEER.rank
        else None
    )
    inputs = await ReportRepository(session).inputs(
        study, preparer=saved.details().engineer() if saved else None
    )
    pdf = await _render_pdf(session, storage, export, study, inputs)
    name = f"study-{str(study.id)[:8]}-rev-{study.revision}-report-preview.pdf"
    return Response(
        content=pdf,
        media_type=PDF,
        headers={
            ETAG_HEADER: f'"{sha256_hex(pdf)}"',
            "Cache-Control": "private, no-cache",
            "Content-Disposition": f'inline; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@writes.post(
    "/studies/{study_id}/report/sign",
    summary="Sign the current revision's report (engineer)",
    description=_codes(
        "The engineer's affirmative act (STUDY_REPORT_SPEC 11.13): `attest: true`, "
        "the certification version shown, the name and credentials attested, and the `ETag` of "
        "the preview reviewed. Checks in order, writing nothing unless all pass: the current "
        "revision has a report (409 {REPORT_NOT_SIGNABLE}), the firm profile and the study "
        "answers exist (409 {REPORT_INPUTS_MISSING}), the revision is not signed "
        "(409 {REPORT_ALREADY_SIGNED}), the signer has details to sign with "
        "(409 {REPORT_SIGNER_DETAILS_MISSING}; 409 {REPORT_SIGNER_DETAILS_MISMATCH} when "
        "`engineer` differs from the signer's saved details), the preview rendered now has the "
        "reviewed sha256 (412 {REPORT_CHANGED} with the current `ETag`). The report prints the "
        "signer's saved details (`PUT /v1/me/signer`); without any, the `engineer` sent, "
        "unless the deployment requires saved details. Then the final PDF is rendered with "
        "the signature and stored, and the signature and the `report.signed` event are "
        "recorded."
    ),
    status_code=status.HTTP_201_CREATED,
    responses=problems(403, 404, 409, 412),
)
async def sign_report(
    body: SignIn,
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.ENGINEER))],
    user: CurrentUserDep,
    session: SessionDep,
    storage: StorageDep,
    bucket: BucketDep,
    request: Request,
) -> SignatureOut:
    settings: Settings = request.app.state.settings
    repo = ReportRepository(session)
    study = await repo.lock_study(access.study.id)
    if body.revision != study.revision:
        raise Conflict(
            f"Revision {body.revision} is not the study's current revision ({study.revision}).",
            code=E.REPORT_NOT_SIGNABLE,
        )
    export = await _current_export(session, storage, study)
    saved = await SignerRepository(session).get(user.id)
    inputs = await repo.inputs(study, preparer=saved.details().engineer() if saved else None)
    firm = await repo.firm_profile(study.org_id)
    missing = [m for m in missing_for_final(inputs) if m != "signature"]
    if missing:
        raise Conflict(
            f"A final report needs the {' and '.join(missing)} inputs; save them, preview "
            "again and sign.",
            code=E.REPORT_INPUTS_MISSING,
        )
    if await repo.signature(study.id, study.revision) is not None:
        raise Conflict(
            f"Revision {study.revision} is already signed.", code=E.REPORT_ALREADY_SIGNED
        )
    signer = _signer(saved, body.engineer, required=settings.report_signer_details_required)
    preview = await _render_pdf(session, storage, export, study, inputs)
    current = sha256_hex(preview)
    if current != body.reviewed_sha256:
        raise PreconditionFailed(
            "The report changed since you reviewed it (an input or the export changed); "
            "nothing was signed. Open the preview again, review it, and sign with its ETag.",
            headers={ETAG_HEADER: f'"{current}"'},
            code=E.REPORT_CHANGED,
        )
    signature_id = uuid.uuid4()
    signed_at = await repo.now()
    final_inputs = inputs.model_copy(
        update={
            "signature": Signature(
                engineer=signer.engineer, signed_at=signed_at, signature_id=signature_id
            )
        }
    )
    pdf = await _render_pdf(session, storage, export, study, final_inputs)
    pdf_uri = report_uri(bucket, study.id, study.revision, f"report.signed.{signature_id}.pdf")
    pdf_sha256 = sha256_hex(pdf)
    engineer = signer.engineer.model_dump(mode="json")
    letterhead = firm.profile.letterhead() if firm else None
    export_sha256 = str((export.event.detail or {}).get("sha256") or sha256_hex(export.json))
    # A unique path per signature: a write whose transaction then fails leaves an orphan,
    # never a different file at a signed path.
    await storage.put(pdf_uri, pdf, PDF)
    try:
        row = await repo.add_signature(
            study.id,
            study.revision,
            signature_id=signature_id,
            signed_by=user.id,
            signed_at=signed_at,
            engineer=engineer,
            statement_version=CERTIFICATION_VERSION,
            export_sha256=export_sha256,
            reviewed_sha256=body.reviewed_sha256,
            pdf_uri=pdf_uri,
            pdf_sha256=pdf_sha256,
            # The inputs' versions under the same lock as the render: what was signed.
            firm_profile_version=firm.version if firm else 0,
            report_answers_version=study.report_answers_version,
            signer_details_version=signer.version,
            letterhead=letterhead,
        )
    except ConflictError as exc:  # pragma: no cover - checked above under the same lock
        raise Conflict(str(exc), code=E.REPORT_ALREADY_SIGNED) from exc
    await StudyEventRepository(session).record(
        study.id,
        SIGNED_EVENT,
        actor="engineer",
        actor_id=user.id,
        stage=study.current_stage,
        revision=study.revision,
        entity_type="report_signature",
        entity_id=row.id,
        detail={
            "engineer": engineer,
            "statement_version": CERTIFICATION_VERSION,
            "export_sha256": export_sha256,
            "reviewed_sha256": body.reviewed_sha256,
            "pdf_sha256": pdf_sha256,
            "pdf_uri": pdf_uri,
            "signer_email": user.user.email,
            "firm_profile_version": row.firm_profile_version,
            "report_answers_version": row.report_answers_version,
            # "saved": the signer's own saved details; "entered": typed at signing.
            "signer_details": "saved" if signer.version is not None else "entered",
            "signer_details_version": signer.version,
            "letterhead": letterhead,
        },
    )
    return await _signature_out(session, row)


__all__ = ["reads", "writes"]
