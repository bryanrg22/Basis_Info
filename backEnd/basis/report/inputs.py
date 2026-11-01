"""What the report needs that no document carries (STUDY_REPORT_SPEC 11.6, 11.11).

* :class:`FirmProfile`: once per firm. The letterhead and the signing engineer's credentials.
* :class:`StudyAnswers`: per study. The taxpayer, who photographed the property and when (an
  engineer's site visit or photographs the taxpayer supplied: the report states which and
  never claims a visit that did not happen), interviews, and prior depreciation for a
  look-back study.
* :class:`Signature`: the engineer's final approval inside Basis, stamped on the final PDF.

Every field a final report needs is listed by :func:`missing_for_final`; without them the
report renders as a DRAFT with marked placeholders.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=500)]
LongText = Annotated[str, Field(min_length=1, max_length=4000)]
Photographer = Literal["engineer_site_visit", "taxpayer_supplied"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Engineer(_Model):
    name: Text
    credentials: Text = Field(description="e.g. 'PE, CCSP'")
    license_number: Text | None = None
    license_state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    email: Text | None = None


class FirmProfile(_Model):
    name: Text
    address: Text | None = None
    phone: Text | None = None
    logo_png: bytes | None = Field(
        default=None, description="a PNG, at most 512 KB", max_length=524_288
    )
    engineer: Engineer | None = None
    limiting_conditions: tuple[LongText, ...] | None = Field(
        default=None, description="replaces the template's statements when set"
    )


class Interview(_Model):
    name: Text
    role: Text
    on: date | None = None
    topics: Text | None = None


class PriorDepreciation(_Model):
    """A look-back study: what was claimed on the property before (Form 3115, DCN 7)."""

    tax_years: Text = Field(description="e.g. '2022-2024'")
    depreciation_claimed: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
    method_used: Text = Field(description="e.g. '27.5-year SL on the whole building'")


class StudyAnswers(_Model):
    taxpayer_name: Text
    photographs: Photographer
    photographed_by: Text = Field(description="the person who took the photographs")
    photographed_on: date
    interviews: tuple[Interview, ...] = ()
    prior_depreciation: PriorDepreciation | None = None


class Signature(_Model):
    """The final approval: who signed and when (the time printed on the certification)."""

    engineer: Engineer
    signed_at: datetime


class ReportInputs(_Model):
    firm: FirmProfile | None = None
    study: StudyAnswers | None = None
    signature: Signature | None = None


def missing_for_final(inputs: ReportInputs) -> tuple[str, ...]:
    """What a final (not DRAFT) report still lacks, as stable field names."""
    missing: list[str] = []
    if inputs.firm is None:
        missing.append("firm")
    if inputs.study is None:
        missing.append("study")
    if inputs.signature is None:
        missing.append("signature")
    return tuple(missing)


__all__ = [
    "Engineer",
    "FirmProfile",
    "Interview",
    "Photographer",
    "PriorDepreciation",
    "ReportInputs",
    "Signature",
    "StudyAnswers",
    "missing_for_final",
]
