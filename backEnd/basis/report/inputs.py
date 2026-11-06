"""What the report needs that no document carries (STUDY_REPORT_SPEC 11.6, 11.11).

* :class:`FirmProfile`: once per firm. The letterhead (name, address, logo, limiting
  conditions). Its ``engineer`` is deprecated (EVAL_REPORT 91): a report names its signer from
  the signer's own :class:`SignerDetails`; the firm's engineer is only a draft's placeholder.
* :class:`SignerDetails`: once per user. The name, credentials and license an engineer signs
  with, saved by that engineer and used whenever they sign (a seal is the individual
  licensee's, never the firm's: NCEES Model Law 140.10 D, Model Rules 240.20).
* :class:`StudyAnswers`: per study. The taxpayer, who photographed the property and when (an
  engineer's site visit or photographs the taxpayer supplied: the report states which and
  never claims a visit that did not happen), interviews, and prior depreciation for a
  look-back study.
* :class:`Signature`: the engineer's final approval inside Basis, stamped on the final PDF.

Every field a final report needs is listed by :func:`missing_for_final`; without them the
report renders as a DRAFT with marked placeholders.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, field_validator, model_validator

from basis.domain.intake import TEXT_PATTERN, Money

# One line, no control characters (the intake's pattern): JSONB cannot store NUL, and the
# report prints each value on a line.
Text = Annotated[str, Field(min_length=1, max_length=500, pattern=TEXT_PATTERN)]
LongText = Annotated[str, Field(min_length=1, max_length=4000, pattern=TEXT_PATTERN)]
Photographer = Literal["engineer_site_visit", "taxpayer_supplied"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The certification the signer attests to (templates/report.typ, section 10). Bumped when its
# wording changes, so a signature records which text was signed.
CERTIFICATION_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# The jurisdictions that license professional engineers in the United States: the member
# boards of NCEES (the 50 states, the District of Columbia, Guam, the Northern Mariana Islands,
# Puerto Rico and the U.S. Virgin Islands; ncees.org/about, read 2026-11-05), by USPS code.
# American Samoa (AS) has no NCEES member board, so no PE license a report cites comes from it.
# An enum in the OpenAPI document, so a client (and the contract test) knows the codes.
LicensingJurisdiction = Literal[
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "GU", "MP", "PR", "VI",
]  # fmt: skip
US_LICENSING_JURISDICTIONS: Final = frozenset(get_args(LicensingJurisdiction))
LicenseNumber = Annotated[str, Field(min_length=1, max_length=50, pattern=TEXT_PATTERN)]


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
    logo_png: Annotated[Base64Bytes, Field(max_length=524_288)] | None = Field(
        default=None, description="a PNG, at most 512 KB, base64 in JSON"
    )
    engineer: Engineer | None = Field(
        default=None,
        description="Deprecated: a report names the engineer who signs it from their own "
        "signer details (`PUT /v1/me/signer`). Still accepted and returned so existing clients "
        "keep working; printed only as the preparer of an unsigned draft whose reader has no "
        "saved details, never on a signed report.",
        json_schema_extra={"deprecated": True},
    )
    limiting_conditions: tuple[LongText, ...] | None = Field(
        default=None, description="replaces the template's statements when set"
    )

    @field_validator("logo_png")
    @classmethod
    def _png(cls, value: bytes | None) -> bytes | None:
        if value is not None and not value.startswith(PNG_SIGNATURE):
            raise ValueError("the logo must be a PNG")
        return value

    def letterhead(self) -> dict[str, str | None]:
        """What a signature keeps of the profile (EVAL_REPORT 91): the name, address and phone
        as printed, and the logo's sha256 (not its bytes; the signed PDF has the image)."""
        return {
            "name": self.name,
            "address": self.address,
            "phone": self.phone,
            "logo_sha256": hashlib.sha256(self.logo_png).hexdigest() if self.logo_png else None,
        }


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class SignerDetails(_Model):
    """What one engineer signs with: their own name, credentials and license, saved once by
    them (``PUT /v1/me/signer``) and printed on every report they sign. A license belongs to
    the individual licensee (NCEES Model Law 140.10 D: the seal carries the jurisdiction, the
    licensee's name and the license number), so it is never the firm's to set. The license is
    optional (a cost segregation study need not be prepared by a PE: the ATG's element C.1
    asks for an individual with expertise and experience), but a number comes with its state."""

    name: Text = Field(description="Your name as it is printed on the reports you sign.")
    credentials: Text = Field(description="e.g. 'PE', 'PE, CCSP'")
    license_number: LicenseNumber | None = Field(
        default=None, description="Your license number, as the licensing board issued it."
    )
    license_state: LicensingJurisdiction | None = Field(
        default=None,
        description="The two-letter code of the jurisdiction that issued the license: a US "
        "state, DC, GU, MP, PR or VI (the NCEES member boards). Required with "
        "`license_number`, and only with it.",
    )

    @field_validator("name", "credentials", "license_number", "license_state", mode="before")
    @classmethod
    def _trimmed(cls, value: object) -> object:
        return _strip(value)

    @model_validator(mode="after")
    def _license_pair(self) -> SignerDetails:
        if (self.license_number is None) != (self.license_state is None):
            raise ValueError("license_number and license_state go together: give both or neither")
        return self

    def engineer(self) -> Engineer:
        """The details as the report and the signature carry them."""
        return Engineer(
            name=self.name,
            credentials=self.credentials,
            license_number=self.license_number,
            license_state=self.license_state,
        )

    def matches(self, engineer: Engineer | Mapping[str, Any]) -> bool:
        """Whether ``engineer`` (what a client sent to sign with, or what a signature
        recorded) names these details: name, credentials and license, compared trimmed.
        ``email`` is not part of them."""
        values = (
            [engineer.get(key) for key in SIGNER_FIELDS]
            if isinstance(engineer, Mapping)
            else [getattr(engineer, key) for key in SIGNER_FIELDS]
        )
        return tuple(_strip(value) for value in values) == (
            self.name,
            self.credentials,
            self.license_number,
            self.license_state,
        )


SIGNER_FIELDS: Final = ("name", "credentials", "license_number", "license_state")


class Interview(_Model):
    name: Text
    role: Text
    on: date | None = None
    topics: Text | None = None


class PriorDepreciation(_Model):
    """A look-back study: what was claimed on the property before (Form 3115, DCN 7)."""

    tax_years: Text = Field(description="e.g. '2022-2024'")
    depreciation_claimed: Money
    method_used: Text = Field(description="e.g. '27.5-year SL on the whole building'")


class StudyAnswers(_Model):
    taxpayer_name: Text
    photographs: Photographer
    photographed_by: Text = Field(description="the person who took the photographs")
    photographed_on: date
    interviews: tuple[Interview, ...] = ()
    prior_depreciation: PriorDepreciation | None = None


class Signature(_Model):
    """The final approval: who signed and when (the time printed on the certification), and
    the signature's id (``report_signatures.id``), printed so the PDF names its record."""

    engineer: Engineer
    signed_at: datetime
    signature_id: uuid.UUID | None = None


class ReportInputs(_Model):
    firm: FirmProfile | None = None
    study: StudyAnswers | None = None
    signature: Signature | None = None
    # Who an unsigned report names as its preparer: the reader's own signer details when the
    # reader may sign (the preview is what they sign), else the firm's deprecated engineer.
    # A signature's engineer always wins (basis.report.render.view).
    preparer: Engineer | None = None


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
    "CERTIFICATION_VERSION",
    "Engineer",
    "FirmProfile",
    "Interview",
    "Photographer",
    "PriorDepreciation",
    "SIGNER_FIELDS",
    "US_LICENSING_JURISDICTIONS",
    "ReportInputs",
    "Signature",
    "SignerDetails",
    "StudyAnswers",
    "missing_for_final",
]
