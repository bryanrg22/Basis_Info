"""The study intake document (``basis.domain.intake``): what a client can send, what is
refused with a reason, the appraisal defaults for the location, and the hand-off to the
worksheet. Fabricated values only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from basis.domain.basis import NOT_CAPITALIZED, CostCategory, worksheet
from basis.domain.intake import StudyIntake, StudyIntakeIn, with_location

D = Decimal


def body(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "purchase_price": "650000.00",
        "acquisition_costs": [
            {"category": "title_insurance_owner", "amount": "1250.00"},
            {"category": "recording_fees", "amount": 180},
        ],
        "land_method": "appraisal_site_value",
        "appraisal_site_value": "90000.00",
        "appraisal_effective_date": "2025-03-14",
        "property_use": "residential_rental",
        "dwelling_unit_income_share": "1",
        "placed_in_service_date": "2025-05-01",
        "acquisition_date": "2025-04-15",
        "state": "NY",
        "zip": "12345",
    }
    values.update(overrides)
    return values


def errors(**overrides: Any) -> str:
    with pytest.raises(ValidationError) as info:
        StudyIntakeIn.model_validate(body(**overrides))
    return str(info.value)


def test_valid_intake_parses_money_as_decimal() -> None:
    intake = StudyIntakeIn.model_validate(body())
    assert intake.purchase_price == D("650000.00")
    assert [c.amount for c in intake.acquisition_costs] == [D("1250.00"), D("180")]
    assert intake.acquisition_costs[0].category is CostCategory.TITLE_INSURANCE_OWNER
    assert intake.placed_in_service_date == date(2025, 5, 1)
    assert intake.transient_lodging is False


def test_to_basis_inputs_feeds_the_worksheet() -> None:
    ws = worksheet(StudyIntakeIn.model_validate(body()).to_basis_inputs())
    assert ws.capitalized_costs == D("1430.00")
    assert ws.land == D("90000.00")
    assert ws.building_basis == D("560000.00")
    assert ws.total_basis == D("561430.00")
    assert ws.recovery_period == D("27.5")


def test_flat_percentage_intake() -> None:
    intake = StudyIntakeIn.model_validate(
        body(
            land_method="flat_percentage",
            appraisal_site_value=None,
            flat_land_percentage="12",
            flat_land_source="the firm's percentage for the market",
            property_use="nonresidential",
            dwelling_unit_income_share=None,
            transient_lodging=True,
        )
    )
    ws = worksheet(intake.to_basis_inputs())
    assert ws.land == D("78000.00") and ws.recovery_period == D("39")


@pytest.mark.parametrize("category", sorted(NOT_CAPITALIZED, key=str))
def test_non_capitalized_categories_are_refused_naming_the_reason(category: CostCategory) -> None:
    message = errors(acquisition_costs=[{"category": category.value, "amount": "10.00"}])
    assert f"{category.value} is not a capitalized acquisition cost" in message
    assert NOT_CAPITALIZED[category][:25] in message


def test_unknown_category_is_refused() -> None:
    assert "category" in errors(acquisition_costs=[{"category": "tip", "amount": "1"}])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"appraisal_site_value": None}, "needs appraisal_site_value"),
        ({"land_method": "land_appraisal"}, "needs land_appraisal_value"),
        ({"land_method": "flat_percentage", "flat_land_source": "x"}, "needs flat_land_percentage"),
        (
            {"land_method": "flat_percentage", "flat_land_percentage": "12"},
            "needs flat_land_source",
        ),
        ({"land_method": "assessor_ratio"}, "land_method"),
        ({"land_method": "residual"}, "land_method"),
        ({"appraisal_site_value": "650000.00"}, "must be below purchase_price"),
        ({"assessor_land_value": "1000"}, "go together"),
        ({"assessor_land_value": "0", "assessor_improvement_value": "0"}, "cannot both be 0"),
        ({"property_use": "residential_rental", "transient_lodging": True}, "transient"),
        ({"dwelling_unit_income_share": None}, "needs dwelling_unit_income_share"),
        ({"dwelling_unit_income_share": "0.79"}, "fails the 80 % test"),
        ({"placed_in_service_date": "2025-04-14"}, "cannot be before acquisition_date"),
        ({"purchase_price": "0"}, "greater than 0"),
        ({"purchase_price": "1.005"}, "decimal"),
        ({"purchase_price": "x"}, "decimal"),
        ({"state": "ny"}, "pattern"),
        ({"state": "NYC"}, "pattern"),
        ({"zip": "1234"}, "pattern"),
        ({"zip": "12345-67"}, "pattern"),
        (
            {
                "flat_land_percentage": "100",
                "land_method": "flat_percentage",
                "flat_land_source": "x",
            },
            "less than 100",
        ),
        ({"unexpected": 1}, "Extra inputs"),
        (
            {"acquisition_costs": [{"category": "survey", "amount": "1", "extra": 1}]},
            "Extra inputs",
        ),
        (
            {"acquisition_costs": [{"category": "survey", "amount": "-1"}]},
            "greater than or equal to 0",
        ),
        (
            {"acquisition_costs": [{"category": "survey", "amount": "1", "description": "a\nb"}]},
            "pattern",
        ),
        ({"acquisition_costs": [{"category": "survey", "amount": "1"}] * 101}, "at most 100"),
    ],
)
def test_refusals(overrides: dict[str, Any], message: str) -> None:
    assert message in errors(**overrides)


def test_nonresidential_needs_no_income_share() -> None:
    intake = StudyIntakeIn.model_validate(
        body(property_use="nonresidential", dwelling_unit_income_share=None)
    )
    assert worksheet(intake.to_basis_inputs()).recovery_period == D("39")


def test_location_source_rules() -> None:
    given = StudyIntakeIn.model_validate(body())
    stored = with_location(given, appraisal_state="CA", appraisal_zip="99999")
    assert (stored.state, stored.zip, stored.location_source) == ("NY", "12345", "intake")

    none = StudyIntakeIn.model_validate(body(state=None, zip=None))
    stored = with_location(none, appraisal_state=" ny ", appraisal_zip="12345-6789 ")
    assert (stored.state, stored.zip, stored.location_source) == ("NY", "12345-6789", "appraisal")

    stored = with_location(none, appraisal_state="New York", appraisal_zip="no zip here")
    assert (stored.state, stored.zip, stored.location_source) == (None, None, None)

    stored = with_location(none, appraisal_state=None, appraisal_zip=None)
    assert stored.location_source is None

    half = StudyIntakeIn.model_validate(body(zip=None))
    stored = with_location(half, appraisal_state="CA", appraisal_zip="54321")
    assert (stored.state, stored.zip, stored.location_source) == ("NY", "54321", "intake")


def test_stored_document_round_trips_through_json() -> None:
    stored = with_location(
        StudyIntakeIn.model_validate(body()), appraisal_state=None, appraisal_zip=None
    )
    document = stored.model_dump(mode="json")
    assert document["purchase_price"] == "650000.00"
    assert document["placed_in_service_date"] == "2025-05-01"
    assert StudyIntake.model_validate(document) == stored


def test_stored_document_refuses_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        StudyIntake.model_validate({**body(), "location_source": "intake", "x": 1})
    with pytest.raises(ValidationError):
        StudyIntakeIn.model_validate({**body(), "location_source": "intake"})
