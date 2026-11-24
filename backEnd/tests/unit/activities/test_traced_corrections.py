"""A traced measurement on a correction (EVAL_REPORT 97), without a database.

The activity's checks (``check_asset``, ``check_measurement``, ``check_component``), the
decision route's value check (``check_correction_values``: what the API refuses with a 422
before anything is written, and the setting), the request schema (only ``measurement``
takes an object, and it must be storable JSON), and the takeoff it replaces
(``traced_by_engineer`` against ``measured_by_engineer``: the same inputs, a typed quantity's
takeoff unchanged)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from basis.activities.corrections import (
    check_asset,
    check_component,
    check_measurement,
    measurement_problems,
)
from basis.activities.process_assets import (
    TakeoffPlan,
    _takeoff,
    measured_by_engineer,
    traced_by_engineer,
)
from basis.api.routes.decisions import check_correction_values
from basis.api.schemas.reviews import CorrectionIn
from basis.config import Settings
from basis.db.models import Component, Room
from basis.domain.money_inputs import MISSING_INPUT
from basis.errors.codes import E

D = Decimal
STUDY = uuid.uuid4()

MEASUREMENT: dict[str, Any] = {
    "unit": "SF",
    "space": "image",
    "shapes": [{"kind": "area", "ring": [[0, 0], [100, 0], [100, 200], [0, 200]]}],
    "scale": {"points": [[0, 0], [100, 0]], "length": 20, "unit": "ft"},
    "image": {"source": "site plan sheet C1", "captured_on": "2020-04-18"},
}
TYPED = {"quantity": "800", "quantity_unit": "SF", "quantity_source": "site plan sheet C1"}
BAD = {**MEASUREMENT, "unit": "EA"}


def code_of(after: dict[str, Any]) -> str:
    with pytest.raises(ApplicationError) as raised:
        check_asset(after)
    return str(raised.value)


# --------------------------------------------------------------------------- the activity


@pytest.mark.parametrize(
    "after",
    [
        {"measurement": MEASUREMENT},
        {"measurement": MEASUREMENT, "bucket": "y15", "recovery_period": "15"},
        {"measurement": MEASUREMENT, "unit_cost": 6.5, "unit_cost_source": "paving quote"},
    ],
    ids=["alone", "with a classification", "with a unit price"],
)
def test_a_measurement_is_accepted_alone_or_with_other_fields(after: dict[str, Any]) -> None:
    check_asset(after)
    shape = check_measurement(after)
    assert shape is not None and shape.quantity == D("800.00")


def test_no_measurement_is_none() -> None:
    assert check_measurement({"bucket": "y5"}) is None
    assert measurement_problems({"bucket": "y5"}) == []


@pytest.mark.parametrize(
    "typed",
    [TYPED, {"quantity": "800"}, {"quantity_source": "plan"}],
    ids=["the whole triple", "a quantity", "a source"],
)
def test_a_measurement_with_a_typed_quantity_is_refused(typed: dict[str, Any]) -> None:
    message = code_of({"measurement": MEASUREMENT, **typed})
    assert E.CORRECT_MEASUREMENT in message and "one way to state a quantity" in message


def test_a_bad_measurement_is_refused_with_its_code_and_message() -> None:
    message = code_of({"measurement": BAD})
    assert E.CORRECT_MEASUREMENT in message and "measurement.unit must be SF, SY or LF" in message
    assert code_of({"measurement": None}).count(E.CORRECT_MEASUREMENT) == 1


def test_a_component_correction_cannot_carry_a_measurement() -> None:
    with pytest.raises(ApplicationError) as raised:
        check_component({"measurement": MEASUREMENT})
    assert E.CORRECT_UNKNOWN_FIELDS in str(raised.value)


# --------------------------------------------------------------------------- the API's check


def item(after: dict[str, Any], entity_type: str = "asset") -> CorrectionIn:
    return CorrectionIn(entity_type=entity_type, entity_id=uuid.uuid4(), after=after)


def errors(items: list[CorrectionIn], **kw: bool) -> list[dict[str, Any]]:
    with pytest.raises(RequestValidationError) as raised:
        check_correction_values(items, **kw)
    return list(raised.value.errors())


def test_the_route_accepts_what_the_activity_accepts() -> None:
    check_correction_values([item({"measurement": MEASUREMENT}), item({"bucket": "y5"})])


def test_the_route_names_each_bad_measurement_at_after() -> None:
    found = errors([item({"bucket": "y5"}), item({"measurement": BAD})])
    assert [(e["loc"], e["type"]) for e in found] == [
        (("body", "corrections", 1, "after"), "correction_value")
    ]
    assert "measurement.unit must be SF, SY or LF" in found[0]["msg"]


def test_the_route_refuses_a_measurement_with_the_typed_quantity_once() -> None:
    [found] = errors([item({"measurement": MEASUREMENT, "quantity": "800"})])
    assert "one way to state a quantity" in found["msg"]


@pytest.mark.parametrize("entity_type", ["component", "room", "extraction_field", "photo"])
def test_the_route_refuses_a_measurement_on_another_entity(entity_type: str) -> None:
    [found] = errors([item({"measurement": MEASUREMENT}, entity_type)])
    assert f"a {entity_type} correction cannot carry it" in found["msg"]


def test_with_the_setting_off_the_route_refuses_any_measurement() -> None:
    [found] = errors([item({"measurement": MEASUREMENT})], traced_measurement=False)
    assert "turned off on this deployment" in found["msg"]
    check_correction_values([item(TYPED)], traced_measurement=False)  # the typed path stays


# --------------------------------------------------------------------------- the schema


def test_only_measurement_takes_an_object() -> None:
    assert (
        CorrectionIn.model_validate(
            {
                "entity_type": "asset",
                "entity_id": str(uuid.uuid4()),
                "after": {"measurement": MEASUREMENT},
            }
        ).after["measurement"]["unit"]
        == "SF"
    )
    with pytest.raises(ValidationError, match="bucket is not a field that takes an object"):
        CorrectionIn.model_validate(
            {"entity_type": "asset", "entity_id": str(uuid.uuid4()), "after": {"bucket": {}}}
        )


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ({"image": {"source": "a\x00b"}}, "a string with NUL"),
        ({"shapes": [[float("nan"), 0]]}, "a number that is not finite"),
        ({"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}, "nested deeper than 8 levels"),
        ({"k\x00": 1}, "a key with NUL"),
    ],
)
def test_a_measurement_postgres_could_not_store_is_refused_by_the_schema(
    value: dict[str, Any], why: str
) -> None:
    with pytest.raises(ValidationError, match=why):
        CorrectionIn.model_validate(
            {
                "entity_type": "asset",
                "entity_id": str(uuid.uuid4()),
                "after": {"measurement": value},
            }
        )


# --------------------------------------------------------------------------- the takeoff


def exterior_takeoff() -> Any:
    """A bedroom's carpet with no appraisal fields: no measured area (``input:gla``)."""
    at = Room(id=uuid.uuid4(), study_id=STUDY, room_type="bedroom", floor="1", name="Bedroom")
    plan = TakeoffPlan.build(Settings(_env_file=None, basis_env="test"), None, [at])  # type: ignore[call-arg]
    carpet = Component(
        id=uuid.uuid4(), study_id=STUDY, room_id=at.id, label="carpet", raw={"count": 1}
    )
    return _takeoff(carpet, at, None, plan, {}, uuid.uuid4())


def test_a_traced_shape_replaces_the_takeoff_as_a_typed_quantity_does() -> None:
    missing = exterior_takeoff()
    assert missing.inputs.get(MISSING_INPUT)
    shape = check_measurement({"measurement": MEASUREMENT})
    assert shape is not None
    t = traced_by_engineer(missing, shape)
    typed = measured_by_engineer(missing, TYPED)
    assert (t.quantity, t.unit, t.method) == (typed.quantity, typed.unit, typed.method)
    assert t.inputs == typed.inputs  # the same flat inputs: engineer, the source, no computed
    assert t.inputs["quantity_source"] == "engineer"
    assert t.inputs["measured_by_engineer"] == "site plan sheet C1"
    assert t.inputs["computed_quantity"] is None and MISSING_INPUT not in t.inputs
    assert t.formula.startswith("800.00 SF traced by the engineer on site plan sheet C1")
    assert t.assumptions == ()


def test_a_typed_quantitys_takeoff_is_what_it_was() -> None:
    """The regression line: the formula and inputs of a typed quantity as written before
    EVAL_REPORT 97 (tests/unit/activities/test_missing_inputs.py holds the original)."""
    t = measured_by_engineer(exterior_takeoff(), TYPED)
    assert t.formula == "800.00 SF measured by the engineer (site plan sheet C1)"
    assert (t.quantity, t.unit, t.method, t.assumptions) == (D("800.00"), "SF", "area", ())
    assert {
        k: t.inputs[k] for k in ("quantity_source", "measured_by_engineer", "computed_quantity")
    } == {
        "quantity_source": "engineer",
        "measured_by_engineer": "site plan sheet C1",
        "computed_quantity": None,
    }
