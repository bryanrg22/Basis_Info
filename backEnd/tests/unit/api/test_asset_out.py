"""How a stored asset becomes ``AssetOut`` (no database): the takeoff's stored inputs,
method and assumptions are exposed; a cost row's stored trail is mapped as recorded (a
NULL trail, a row priced before it existed, is ``trail: null`` with ``trail_recorded``
false); the old fields are unchanged.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from basis.api.routes.outputs import asset_out
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import Asset, CostEstimate, Takeoff
from basis.db.repositories.outputs import AssetRow
from basis.domain.cost_trail import TrailContext, json_safe, priced_trail, quantity_block
from basis.domain.costs import estimate_cost
from basis.domain.measurements import resolve_cost_index
from basis.domain.takeoffs import Takeoff as DomainTakeoff

D = Decimal
SID = uuid.UUID("5f0c6a52-0000-4000-8000-000000000001")
AID = uuid.UUID("5f0c6a52-0000-4000-8000-000000000002")
OLD_COST = {
    "unit_cost": "61.20",
    "total": "1121.80",
    "source_row": "TYPICAL_UNIT_COSTS[carpet][standard] per SF (base 2020, ...)",
    "location_factor": "1.000",
    "cost_year": 2018,
}


def asset() -> Asset:
    return Asset(
        id=AID,
        study_id=SID,
        revision=1,
        component_id=None,
        category="Carpet",
        bucket=Bucket.Y5,
        recovery_period=D("5"),
        irs_section="1245",
        method=ClassificationMethod.RULE,
        confidence=0.9,
        needs_review=False,
        review_reason=None,
    )


def priced() -> tuple[DomainTakeoff, dict[str, Any], dict[str, Any]]:
    """A carpet takeoff in SY, its written inputs and its trail, priced for 2018 in ZIP
    12345 (intake)."""
    t = DomainTakeoff(
        component="carpet",
        quantity=D("18.33"),
        unit="SY",
        formula="bedroom 150 SF / 9 x 1.10",
        inputs={"component": "carpet", "room_area_source": "gla_share"},
        method="area",
        assumptions=("10 % waste",),
    )
    inputs = {**t.inputs, "method": t.method, "assumptions": list(t.assumptions)}
    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2018, 5, 1),
        zip_code="12345",
    )
    estimate = estimate_cost(
        t,
        index.cost_year,
        index.location_factor,
        year=index.year,
        year_source=index.year_source,
        location_source=index.location_source,
        basis="bare",
    )
    trail = priced_trail(
        quantity_block(t, inputs),
        estimate,
        index,
        context=TrailContext(intake_version=2, property_class="residential_rental"),
    )
    return t, inputs, json.loads(json.dumps(trail))  # as JSONB gives it back


def row(takeoff: Takeoff | None, cost: CostEstimate | None) -> AssetRow:
    return AssetRow(asset(), None, None, takeoff, cost, 0, 0)


def test_a_stored_trail_is_mapped_as_recorded() -> None:
    t, inputs, trail = priced()
    cost = CostEstimate(
        study_id=SID,
        asset_id=AID,
        unit_cost=D(trail["unit_cost"]),
        total=D(trail["total"]),
        source_row="row",
        location_factor=D(trail["location_factor"]),
        cost_year=trail["cost_year"],
        trail=trail,
    )
    takeoff = Takeoff(
        study_id=SID, asset_id=AID, quantity=t.quantity, unit=t.unit, formula=t.formula,
        inputs=inputs,
    )  # fmt: skip
    out = asset_out(row(takeoff, cost))
    assert out.cost is not None and out.cost.trail is not None and out.cost.trail_recorded
    mapped = out.cost.trail
    assert mapped.kind == "unit_price" and mapped.intake_version == 2
    assert mapped.base is not None and mapped.base.unit_cost == D("61.20")
    assert mapped.base.conversion == "x9 from per SF"
    assert mapped.year is not None and mapped.year.factor == D("0.9322")
    assert mapped.year.index.as_of == date(2018, 7, 1) and mapped.year.index.store is None
    assert mapped.location is not None and mapped.location.zip == "12345"
    assert mapped.location.city == "Schenectady" and mapped.location.lookup == "zip"
    assert mapped.quantity.inputs == inputs
    # The JSON the route sends: the trail's own strings, `from` by its name.
    body = out.model_dump(mode="json", by_alias=True)
    sent = body["cost"]["trail"]
    assert sent["year"]["factor"] == "0.9322" and sent["base"]["unit_cost"] == "61.20"
    assert sent["year"]["from"] == "placed in service 2018-05-01 (intake)"
    assert sent["location"]["from"].startswith("zip 12345 (intake)")
    assert sent["unit_cost"] == body["cost"]["unit_cost"]
    assert sent["total"] == body["cost"]["total"]
    assert body["cost"]["trail_recorded"] is True
    # The takeoff's stored inputs, method and assumptions.
    assert body["takeoff"] == {
        "quantity": "18.33",
        "unit": "SY",
        "formula": "bedroom 150 SF / 9 x 1.10",
        "method": "area",
        "inputs": json_safe(inputs),
        "assumptions": ["10 % waste"],
    }


def test_a_legacy_row_has_a_null_trail_and_its_old_fields_unchanged() -> None:
    cost = CostEstimate(
        study_id=SID,
        asset_id=AID,
        unit_cost=D(OLD_COST["unit_cost"]),
        total=D(OLD_COST["total"]),
        source_row=OLD_COST["source_row"],
        location_factor=D(OLD_COST["location_factor"]),
        cost_year=OLD_COST["cost_year"],
    )
    takeoff = Takeoff(
        study_id=SID, asset_id=AID, quantity=D("6.0000"), unit="EA",
        formula="max(count=6, min_per_room=1)",
    )  # fmt: skip
    body = asset_out(row(takeoff, cost)).model_dump(mode="json", by_alias=True)
    assert body["cost"] == {**OLD_COST, "trail": None, "trail_recorded": False}
    # A takeoff stored without inputs: the new members are empty, the old ones as before.
    assert body["takeoff"] == {
        "quantity": "6.0000",
        "unit": "EA",
        "formula": "max(count=6, min_per_room=1)",
        "method": None,
        "inputs": None,
        "assumptions": [],
    }


def test_an_unpriced_row_maps_its_missing_reason() -> None:
    t, inputs, _ = priced()
    index = resolve_cost_index(None, cost_year=2026, location_factor=D("1"), state="IL")
    trail = json.loads(
        json.dumps(
            priced_trail(
                quantity_block(t, inputs),
                None,
                index,
                context=TrailContext(None, "nonresidential"),
                missing="missing: no cost row for 'carpet' per SY",
            )
        )
    )
    cost = CostEstimate(
        study_id=SID, asset_id=AID, unit_cost=None, total=None,
        source_row="missing: no cost row for 'carpet' per SY", cost_year=2026,
        location_factor=D("0.959"), trail=trail,
    )  # fmt: skip
    out = asset_out(row(None, cost))
    assert out.takeoff is None and out.cost is not None and out.cost.trail is not None
    mapped = out.cost.trail
    assert (mapped.kind, mapped.unit_cost, mapped.total, mapped.base) == (
        "unpriced",
        None,
        None,
        None,
    )
    assert mapped.missing == cost.source_row and mapped.intake_version is None
    assert mapped.location is not None and mapped.location.cities == 19
    assert mapped.location.store is None and mapped.location.zip is None
