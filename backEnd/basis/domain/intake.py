"""The study intake: what the taxpayer or their CPA tells Basis that no document carries
(STUDY_REPORT_SPEC section 4.1), validated once and stored on the study as one document.

``StudyIntakeIn`` is the request body (``POST /v1/studies`` ``intake``, ``PUT
/v1/studies/{id}/intake``); ``StudyIntake`` is what is stored (``studies.intake``) and what the
takeoff and the allocation read: ``study.intake.placed_in_service_date``, ``.state``, ``.zip``,
``.purchase_price`` and so on. The rules here are the ones a client can be told about in a 422
(a non-capitalizable cost category, a land method without its input, a use that fails the 80 %
test); the arithmetic and its checks are :mod:`basis.domain.basis`.

:func:`missing_for_start` is the gate ``POST /v1/studies/{id}/start`` applies: the fields the
output cannot be produced without (WORKFLOW_REVIEW 2026-10-29 section 5; EVAL_REPORT section
36), as a structured list the route returns in a 422.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from basis.domain.basis import (
    DWELLING_UNIT_INCOME_MIN,
    NOT_CAPITALIZED,
    AcquisitionCost,
    BasisInputs,
    CostCategory,
    LandInputs,
    LandMethod,
    PropertyUse,
    UseInputs,
)

MAX_ACQUISITION_COSTS: Final = 100
MAX_MONEY: Final = Decimal("1000000000000")  # 10^12
MAX_TEXT: Final = 200
STATE_PATTERN: Final = r"^[A-Z]{2}$"
ZIP_PATTERN: Final = r"^[0-9]{5}(-[0-9]{4})?$"
# One line, no control characters, at least one visible character (the API's LINE_PATTERN).
TEXT_PATTERN: Final = r"^[^\x00-\x1f\x7f-\x9f]*[^\x00-\x1f\x7f-\x9f\s][^\x00-\x1f\x7f-\x9f]*$"

Money = Annotated[
    Decimal,
    Field(
        ge=0,
        le=MAX_MONEY,
        max_digits=15,
        decimal_places=2,
        description="Dollars to the cent, as a JSON number or a numeric string.",
    ),
]
PositiveMoney = Annotated[
    Decimal, Field(gt=0, le=MAX_MONEY, max_digits=15, decimal_places=2, description="Dollars.")
]
Percent = Annotated[
    Decimal,
    Field(gt=0, lt=100, max_digits=9, decimal_places=6, description="Percent (12 means 12 %)."),
]
Fraction = Annotated[
    Decimal, Field(ge=0, le=1, max_digits=7, decimal_places=6, description="A share, 0..1.")
]
Text = Annotated[str, Field(min_length=1, max_length=MAX_TEXT, pattern=TEXT_PATTERN)]
LocationSource = Literal["intake", "appraisal"]


class AcquisitionCostIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: CostCategory = Field(
        description="A settlement-statement line. Capitalized categories add to basis "
        "(Reg. 1.263(a)-2(f); Pub. 551): abstract_fees, title_search, title_insurance_owner, "
        "legal_fees, recording_fees, survey, transfer_taxes, broker_commission, appraisal_buyer, "
        "inspection, utility_connection, seller_obligations_paid, other_facilitative. The loan "
        "and occupancy categories (points, loan_origination, mortgage_insurance, "
        "loan_assumption_fee, credit_report, appraisal_lender, prepaid_interest, escrow_taxes, "
        "escrow_insurance, casualty_insurance, property_tax_proration, prepaid_rent, "
        "utilities_before_closing, refinancing_fees) are refused with the reason."
    )
    amount: Money
    description: Text | None = None

    @field_validator("category")
    @classmethod
    def _capitalized(cls, value: CostCategory) -> CostCategory:
        reason = NOT_CAPITALIZED.get(value)
        if reason is not None:
            raise ValueError(f"{value.value} is not a capitalized acquisition cost: {reason}")
        return value


class StudyIntakeIn(BaseModel):
    """The intake as the client sends it."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "purchase_price": "650000.00",
                    "acquisition_costs": [
                        {"category": "title_insurance_owner", "amount": "1250.00"},
                        {"category": "recording_fees", "amount": "180.00"},
                    ],
                    "land_method": "appraisal_site_value",
                    "appraisal_site_value": "90000.00",
                    "appraisal_effective_date": "2025-03-14",
                    "assessor_land_value": "70000",
                    "assessor_improvement_value": "430000",
                    "property_use": "residential_rental",
                    "dwelling_unit_income_share": "1",
                    "transient_lodging": False,
                    "placed_in_service_date": "2025-05-01",
                    "acquisition_date": "2025-04-15",
                    "state": "NY",
                    "zip": "12345",
                }
            ]
        },
    )

    purchase_price: PositiveMoney = Field(
        description="The contract price from the closing statement (the appraisal's contract "
        "price corroborates it)."
    )
    acquisition_costs: list[AcquisitionCostIn] = Field(
        max_length=MAX_ACQUISITION_COSTS,
        description="Capitalized acquisition costs from the closing statement, by category; "
        "allocated pro rata over every class (ATG ch. 4 C.11). Required: send an empty list "
        "when the closing statement has no capitalizable line, so that `CC = 0` is a statement "
        "and not an omission.",
    )
    land_method: LandMethod = Field(
        description="Where the land figure comes from, in order of preference: "
        "`appraisal_site_value` (needs `appraisal_site_value`), `land_appraisal` (needs "
        "`land_appraisal_value`), `flat_percentage` (needs `flat_land_percentage` and "
        "`flat_land_source`). The assessor's values are only a check (ATG p.64); land is never "
        "the price less the improvements."
    )
    appraisal_site_value: PositiveMoney | None = Field(
        default=None, description="The appraisal's opinion of site value (cost approach)."
    )
    appraisal_effective_date: date | None = Field(
        default=None, description="The appraisal's effective date (checked against the purchase)."
    )
    land_appraisal_value: PositiveMoney | None = None
    land_appraisal_date: date | None = None
    assessor_land_value: Money | None = Field(
        default=None, description="The assessor's land value, with `assessor_improvement_value`."
    )
    assessor_improvement_value: Money | None = None
    flat_land_percentage: Percent | None = Field(
        default=None, description="Land as a percent of the price (12 means 12 %)."
    )
    flat_land_source: Text | None = Field(
        default=None, description="Where the percentage comes from (required with it)."
    )
    property_use: PropertyUse = Field(
        description="`residential_rental` (27.5 years: 80 % or more of gross rental income from "
        "dwelling units, none transient; §168(e)(2)(A)) or `nonresidential` (39 years; a "
        "bed-and-breakfast or short-term rental let on a transient basis is nonresidential)."
    )
    dwelling_unit_income_share: Fraction | None = Field(
        default=None,
        description="Share (0..1) of gross rental income from dwelling units; required for "
        "`residential_rental` and must be at least 0.8.",
    )
    transient_lodging: bool = Field(
        default=False,
        description="More than half the units are let on a transient basis (hotel, motel, "
        "B&B); such units are not dwelling units, so the use cannot be `residential_rental`.",
    )
    placed_in_service_date: date = Field(
        description="When the property was ready and available for its use (Pub. 527), not the "
        "contract or appraisal date; depreciation starts here (mid-month convention) and the "
        "cost year follows it."
    )
    acquisition_date: date | None = Field(
        default=None, description="Contract or closing date (bonus depreciation rate)."
    )
    state: Annotated[str, Field(pattern=STATE_PATTERN)] | None = Field(
        default=None,
        description="Two-letter state (location cost index). Defaults to the appraisal's "
        "`subject.state` when absent.",
    )
    zip: Annotated[str, Field(pattern=ZIP_PATTERN)] | None = Field(
        default=None,
        description="ZIP code (location cost index). Defaults to the appraisal's `subject.zip` "
        "when absent.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> StudyIntakeIn:
        method = self.land_method
        if method is LandMethod.APPRAISAL_SITE_VALUE and self.appraisal_site_value is None:
            raise ValueError("land_method appraisal_site_value needs appraisal_site_value")
        if method is LandMethod.LAND_APPRAISAL and self.land_appraisal_value is None:
            raise ValueError("land_method land_appraisal needs land_appraisal_value")
        if method is LandMethod.FLAT_PERCENTAGE:
            if self.flat_land_percentage is None:
                raise ValueError("land_method flat_percentage needs flat_land_percentage")
            if self.flat_land_source is None:
                raise ValueError(
                    "land_method flat_percentage needs flat_land_source (a rule-of-thumb "
                    "percentage without a source is not accepted: ATG ch. 3 C.6)"
                )
        for name, value in (
            ("appraisal_site_value", self.appraisal_site_value),
            ("land_appraisal_value", self.land_appraisal_value),
        ):
            if value is not None and value >= self.purchase_price:
                raise ValueError(f"{name} must be below purchase_price")
        if (self.assessor_land_value is None) != (self.assessor_improvement_value is None):
            raise ValueError(
                "assessor_land_value and assessor_improvement_value go together (the ratio)"
            )
        if (
            self.assessor_land_value is not None
            and self.assessor_improvement_value is not None
            and self.assessor_land_value + self.assessor_improvement_value <= 0
        ):
            raise ValueError("assessor_land_value and assessor_improvement_value cannot both be 0")
        if self.property_use is PropertyUse.RESIDENTIAL_RENTAL:
            if self.transient_lodging:
                raise ValueError(
                    "residential_rental cannot be transient lodging: units let on a transient "
                    "basis are not dwelling units (§168(e)(2)(A)(ii)); use nonresidential"
                )
            if self.dwelling_unit_income_share is None:
                raise ValueError(
                    "residential_rental needs dwelling_unit_income_share (the 80 % test)"
                )
            if self.dwelling_unit_income_share < DWELLING_UNIT_INCOME_MIN:
                raise ValueError(
                    "residential_rental fails the 80 % test of §168(e)(2)(A): "
                    f"dwelling_unit_income_share is {self.dwelling_unit_income_share}; use "
                    "nonresidential"
                )
        if (
            self.acquisition_date is not None
            and self.placed_in_service_date < self.acquisition_date
        ):
            raise ValueError("placed_in_service_date cannot be before acquisition_date")
        return self

    def to_basis_inputs(self) -> BasisInputs:
        return BasisInputs(
            purchase_price=self.purchase_price,
            acquisition_costs=tuple(
                AcquisitionCost(c.category, c.amount, c.description) for c in self.acquisition_costs
            ),
            land_method=self.land_method,
            land=LandInputs(
                appraisal_site_value=self.appraisal_site_value,
                appraisal_effective_date=self.appraisal_effective_date,
                land_appraisal_value=self.land_appraisal_value,
                land_appraisal_date=self.land_appraisal_date,
                assessor_land_value=self.assessor_land_value,
                assessor_improvement_value=self.assessor_improvement_value,
                flat_percentage=self.flat_land_percentage,
                flat_source=self.flat_land_source,
            ),
            use=UseInputs(
                use=self.property_use,
                dwelling_unit_income_share=self.dwelling_unit_income_share,
                transient_lodging=self.transient_lodging,
            ),
            placed_in_service_date=self.placed_in_service_date,
            acquisition_date=self.acquisition_date,
        )


class StudyIntake(StudyIntakeIn):
    """The stored intake: the request plus where the location came from."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra={})

    location_source: LocationSource | None = Field(
        default=None,
        description="`intake` when the client gave state or zip, `appraisal` when both were "
        "filled from the appraisal's subject fields, null when neither had them.",
    )


def with_location(
    intake: StudyIntakeIn, *, appraisal_state: str | None, appraisal_zip: str | None
) -> StudyIntake:
    """The stored document: state/zip from the intake, else from the appraisal's subject
    fields when they parse as a state code and a ZIP."""
    state, zip_code = intake.state, intake.zip
    source: LocationSource | None = "intake" if state or zip_code else None
    if (
        state is None
        and appraisal_state
        and re.fullmatch(STATE_PATTERN, appraisal_state.strip().upper())
    ):
        state = appraisal_state.strip().upper()
        source = source or "appraisal"
    if zip_code is None and appraisal_zip:
        found = re.search(r"[0-9]{5}(?:-[0-9]{4})?", appraisal_zip)
        if found is not None:
            zip_code = found.group(0)
            source = source or "appraisal"
    data = intake.model_dump()
    data.update(state=state, zip=zip_code, location_source=source)
    return StudyIntake.model_validate(data)


# ------------------------------------------------------------------ required before start


@dataclass(frozen=True, slots=True)
class MissingField:
    """One intake field a study cannot start without: its name and what needs it."""

    field: str
    why: str


# What the output requires that no document carries (WORKFLOW_REVIEW section 5: contract
# price, land, closing costs, use, placed-in-service date, location). The document's own
# rules (``StudyIntakeIn``) already make the first five present on any stored intake, so on
# a stored intake only the location can be missing; with no intake at all, every one is.
START_REQUIRED: Final[tuple[MissingField, ...]] = (
    MissingField(
        "purchase_price", "the contract price: the basis starts from it (B = price − land)"
    ),
    MissingField(
        "acquisition_costs",
        "the capitalized closing costs, CC (an empty list when the closing statement has none)",
    ),
    MissingField(
        "land_method",
        "where the land figure comes from, with its value (appraisal_site_value, "
        "land_appraisal_value, or flat_land_percentage with flat_land_source): land is taken "
        "out of the price first (ATG p.37)",
    ),
    MissingField(
        "property_use",
        "27.5 or 39 years for the building (§168(c)) and the classification rules' property class",
    ),
    MissingField(
        "placed_in_service_date",
        "depreciation starts here (Reg. 1.167(a)-11(e)); the takeoff's cost year follows it",
    ),
    MissingField(
        "state",
        "the location cost index is by state; the appraisal's subject.state is read only after "
        "the study starts, so the intake must carry it (zip is recorded for the city index)",
    ),
)


def missing_for_start(intake: StudyIntake | None) -> list[MissingField]:
    """The required fields ``intake`` lacks, in :data:`START_REQUIRED` order; empty when the
    study may start. ``None`` (no intake) lacks every one."""
    if intake is None:
        return list(START_REQUIRED)
    present = {
        "purchase_price": intake.purchase_price is not None,
        "acquisition_costs": intake.acquisition_costs is not None,
        "land_method": intake.land_method is not None,
        "property_use": intake.property_use is not None,
        "placed_in_service_date": intake.placed_in_service_date is not None,
        "state": intake.state is not None,
    }
    return [field for field in START_REQUIRED if not present[field.field]]


__all__ = [
    "MAX_ACQUISITION_COSTS",
    "START_REQUIRED",
    "STATE_PATTERN",
    "ZIP_PATTERN",
    "AcquisitionCostIn",
    "LocationSource",
    "MissingField",
    "StudyIntake",
    "StudyIntakeIn",
    "missing_for_start",
    "with_location",
]
