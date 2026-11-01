"""The report's inputs, state and signature (STUDY_REPORT_SPEC 11.13; docs/api/REPORT.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from basis.api.schemas.common import ResponseModel
from basis.report.inputs import CERTIFICATION_VERSION, Engineer, FirmProfile, StudyAnswers

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FirmProfileOut(ResponseModel):
    """``GET /v1/orgs/{id}/profile``: the profile and its version (also the ``ETag``)."""

    org_id: uuid.UUID
    profile: FirmProfile
    version: int
    updated_at: datetime


class ReportAnswersOut(ResponseModel):
    """``GET /v1/studies/{id}/report-answers``: the answers (``null`` until saved) and their
    version (also the ``ETag``; 0 while there are none)."""

    study_id: uuid.UUID
    report_answers: StudyAnswers | None
    report_answers_version: int


class SignIn(BaseModel):
    """``POST .../report/sign``: the engineer's affirmative act (STUDY_REPORT_SPEC 11.13)."""

    model_config = ConfigDict(extra="forbid")

    revision: Annotated[int, Field(ge=1, description="The revision signed: the current one.")]
    reviewed_sha256: Sha256 = Field(
        description="The `ETag` of `GET .../report/preview` the engineer reviewed, unquoted."
    )
    engineer: Engineer = Field(description="The name and credentials the signer attests to.")
    attest: Literal[True] = Field(
        description="Must be true: the signer affirms the certification (section 10)."
    )
    statement_version: Annotated[
        int,
        Field(
            ge=CERTIFICATION_VERSION,
            le=CERTIFICATION_VERSION,
            description="The certification text's version the client showed.",
        ),
    ]


class SignatureOut(ResponseModel):
    id: uuid.UUID
    study_id: uuid.UUID
    revision: int
    signed_by: uuid.UUID
    signed_at: datetime
    engineer: dict[str, Any]
    statement_version: int
    export_sha256: str
    reviewed_sha256: str
    pdf_sha256: str
    firm_profile_version: int | None = Field(
        description="The firm profile's version the report was signed with (null on a "
        "signature recorded before it was kept)."
    )
    report_answers_version: int | None = Field(
        description="The report answers' version the report was signed with (null as above)."
    )


class ReportSkip(BaseModel):
    code: str
    message: str


class ReportStateOut(BaseModel):
    """``GET .../report/state``: where the current revision's report stands."""

    study_id: uuid.UUID
    revision: int = Field(description="The study's current revision.")
    exported: bool = Field(description="The current revision was exported.")
    skipped: ReportSkip | None = Field(
        description="Why the export wrote no report (no allocation, a total that did not tie)."
    )
    files: list[str] = Field(description="The files exported: `report.pdf`, `schedule.csv` ...")
    missing_for_final: list[str] = Field(
        description="What a final report still lacks: `firm`, `study`, `signature`."
    )
    signature: SignatureOut | None
    firm_profile_version: int = Field(description="0 while the firm has no profile.")
    report_answers_version: int
    changed_since_signing: list[Literal["firm", "study"]] | None = Field(
        default=None,
        description="Once signed: the inputs written after the signature (`firm`: the firm "
        "profile, `study`: the report answers). The signed PDF is unchanged; the stored "
        "inputs no longer match it. `[]` when none changed; null while unsigned, or when the "
        "signature predates the record of the versions it was signed with.",
    )


__all__ = [
    "FirmProfileOut",
    "ReportAnswersOut",
    "ReportSkip",
    "ReportStateOut",
    "SignIn",
    "SignatureOut",
]
