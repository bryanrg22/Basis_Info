"""The per-asset cost trail (``basis.domain.cost_trail``): built from the objects a row was
priced with, it records the base row, both index values with their store rows, the
location row or state mean with its table, and agrees with the row's amounts: base x year
x location rounded half up is the unit cost, unit cost x quantity the total. Pure;
synthetic rows only.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from basis.api.schemas.outputs import CostTrailOut
from basis.costs.indices.select import MANUAL_SOURCE, values_from_rows
from basis.domain import structure as s
from basis.domain.cost_table import parse_cost_table
from basis.domain.cost_trail import (
    STRUCTURE_RESIDUAL,
    STRUCTURE_ROUNDING,
    TRAIL_VERSION,
    UNIT_PRICE,
    UNIT_PRICE_ROUNDING,
    UNPRICED,
    TrailContext,
    check,
    json_safe,
    priced_trail,
    quantity_block,
    structure_block,
    structure_residual_trail,
)
from basis.domain.costs import (
    CENTS,
    CostBasis,
    CostEstimate,
    Markup,
    estimate_cost,
    year_factor_index,
)
from basis.domain.measurements import CostIndex, measurements_from_fields, resolve_cost_index
from basis.domain.takeoffs import Takeoff

D = Decimal
CONTEXT = TrailContext(intake_version=3, property_class="residential_rental")
STORE_ID = uuid.UUID("5f0c6a52-0000-4000-8000-0000000000a1")
CITY_ID = uuid.UUID("5f0c6a52-0000-4000-8000-0000000000b2")

TABLE = parse_cost_table(
    {
        "schema_version": 1,
        "name": "synthetic_2020",
        "base_year": 2020,
        "rows": {
            # Per square (100 SF) printed; priced per SF: x0.0111 leaves a base with four
            # decimals, which the trail must keep exactly (not the cent-rounded base).
            "shingle_roof": {
                "unit": "SF",
                "factor": "0.0111",
                "material": 100,
                "labor": 50,
                "equipment": 0,
                "source_unit": "Sq.",
                "source": "synthetic 07-0001, p. 4",
                "assumption": "architectural shingles over the whole roof",
            },
            "kitchen_cabinet": {
                "unit": "EA",
                "material": 400,
                "labor": 25,
                "source_unit": "Ea.",
                "source": "synthetic 12-0001, p. 2",
            },
        },
    }
)


def setting_index(cost_year: int = 2026, factor: str = "1.0") -> CostIndex:
    """The index ``TakeoffPlan.build`` makes from settings alone (``COST_INDEX=setting``)."""
    return CostIndex(cost_year, None, D(factor), None, year_factor_index(cost_year))


def store_row(year: int, value: str, **extra: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": STORE_ID,
        "source": MANUAL_SOURCE,
        "series": "historical",
        "year": year,
        "region": "US",
        "value": D(value),
        "as_of": date(year, 1, 1),
        "citation": f"RSMeans {year} Building Construction Costs p.778",
        "derived": None,
        "detail": None,
        "version": 2,
    }
    values.update(extra)
    return SimpleNamespace(**values)


def takeoff(component: str, quantity: str, unit: str, **extra: Any) -> Takeoff:
    return Takeoff(
        component=component,
        quantity=D(quantity),
        unit=unit,
        formula=f"{quantity} {unit}",
        inputs={"component": component, "room_area_source": "gla_share"},
        method="area" if unit in ("SF", "SY") else "count",
        **extra,
    )


def written(t: Takeoff) -> dict[str, Any]:
    """The takeoff inputs as ``process_assets`` writes them."""
    return {**t.inputs, "method": t.method, "assumptions": list(t.assumptions)}


def trail_of(
    t: Takeoff,
    index: CostIndex,
    label: str | None = None,
    basis: CostBasis = "bare",
    markup: Markup | None = None,
) -> tuple[CostEstimate, dict[str, Any]]:
    """Priced on the bare basis unless told (the numbers below are the bare rows'); the
    installed basis and the model's markup have their own tests."""
    estimate = estimate_cost(
        t,
        index.cost_year,
        index.location_factor,
        table=TABLE,
        label=label,
        year_source=index.year_source,
        location_source=index.location_source,
        year=index.year,
        basis=basis,
        markup=markup,
    )
    trail = priced_trail(quantity_block(t, written(t)), estimate, index, context=CONTEXT)
    return estimate, trail


def agrees(estimate: CostEstimate, trail: dict[str, Any]) -> None:
    """The trail's amounts are the estimate's, and its arithmetic holds."""
    assert trail["unit_cost"] == format(estimate.unit_cost, "f")
    assert trail["total"] == format(estimate.total, "f")
    assert trail["cost_year"] == estimate.cost_year
    assert D(trail["location_factor"]) == estimate.location_factor
    assert check(trail) == []
    readable(trail)


def readable(trail: dict[str, Any]) -> None:
    """JSON-safe as built, and the API's ``CostTrailOut`` reads it back (as JSONB returns
    it) with every Decimal string unchanged."""
    stored = json.loads(json.dumps(trail))
    assert stored == trail
    sent = CostTrailOut.model_validate(stored).model_dump(mode="json", by_alias=True)
    assert sent == stored


# --------------------------------------------------------------------------- the base row


def test_a_legacy_row_in_square_yards_records_the_per_sf_row_and_the_conversion() -> None:
    estimate, trail = trail_of(takeoff("carpet", "18.33", "SY"), setting_index())
    assert (trail["version"], trail["kind"]) == (TRAIL_VERSION, UNIT_PRICE)
    # standard carpet: 4.50 + 2.00 + 0.30 = 6.80 per SF, x9 per SY = 61.20.
    assert trail["base"] == {
        "unit_cost": "61.20",
        "unit": "SY",
        "table": "TYPICAL_UNIT_COSTS",
        "row": "carpet",
        "tier": "standard",
        "source": None,
        "base_year": 2020,
        "row_unit": "SF",
        "conversion": "x9 from per SF",
        "assumption": None,
        "basis": "bare",
        "basis_how": None,
        "markup": None,
    }
    assert trail["quantity"]["value"] == "18.33" and trail["quantity"]["unit"] == "SY"
    assert trail["rounding"] == UNIT_PRICE_ROUNDING
    assert (trail["missing"], trail["priced_per"], trail["structure"]) == (None, None, None)
    assert (trail["intake_version"], trail["property_class"]) == (3, "residential_rental")
    agrees(estimate, trail)


def test_a_table_row_keeps_its_exact_base_conversion_source_and_assumption() -> None:
    t = takeoff("roof", "2018.36", "SF")
    estimate, trail = trail_of(t, setting_index(factor="0.903"), label="shingle_roof")
    base = trail["base"]
    # (100 + 50) x 0.0111 = 1.6650 per SF: the exact value multiplied, not 1.67.
    assert base["unit_cost"] == "1.6650" and estimate.base_unit_cost == D("1.67")
    assert (base["table"], base["row"], base["tier"]) == ("synthetic_2020", "shingle_roof", None)
    assert base["source"] == "synthetic 07-0001, p. 4"
    assert (base["unit"], base["row_unit"]) == ("SF", "Sq.")
    assert base["conversion"] == "x0.0111 from per Sq."
    assert base["assumption"] == "architectural shingles over the whole roof"
    agrees(estimate, trail)
    # Why the exact base: 1.6650 x 1.0000 x 0.903 = 1.5035 -> 1.50 (the row), while the
    # cent-rounded base gives 1.67 x 0.903 = 1.508 -> 1.51, a cent off on every SF.
    assert estimate.unit_cost == D("1.50")
    assert (D("1.67") * D("0.903")).quantize(CENTS, ROUND_HALF_UP) == D("1.51")


def test_the_quantity_block_is_the_takeoff_as_written() -> None:
    t = takeoff("cabinet", "6", "EA", assumptions=("one per room at least",))
    inputs = {**written(t), "asset_correction": {"from_asset_id": "x", "after": {"bucket": "y5"}}}
    block = quantity_block(t, inputs)
    assert block == {
        "value": "6",
        "unit": "EA",
        "formula": "6 EA",
        "method": "count",
        "inputs": inputs,
        "assumptions": ["one per room at least"],
    }


# --------------------------------------------------------------------------- the year


def test_a_book_year_cites_the_book_and_has_no_store_row() -> None:
    index = resolve_cost_index(
        None, cost_year=2026, location_factor=D("1"), placed_in_service=date(2018, 5, 1)
    )
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "18", "EA"), index, "kitchen_cabinet")
    year = trail["year"]
    assert (year["cost_year"], year["factor"]) == (2018, "0.9322")
    assert year["index"] == {
        "year": 2018,
        "value": "222.9",
        "as_of": "2018-07-01",
        "citation": "RSMeans Building Construction Costs 2020 p.778",
        "estimated": False,
        "derived": None,
        "store": None,
    }
    assert (year["base"]["value"], year["base"]["as_of"]) == ("239.1", "2020-01-01")
    assert year["base"]["store"] is None and year["base"]["estimated"] is True
    assert (year["assumption"], year["conversion"]) == (None, None)
    assert year["from"] == "placed in service 2018-05-01 (intake)"
    assert estimate.year is not None and year["citation"] == estimate.year.citation()
    agrees(estimate, trail)


def test_a_store_year_names_its_row_and_keeps_the_factor_digits() -> None:
    values = values_from_rows([store_row(2024, "294.8")], priority=(MANUAL_SOURCE,))
    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2024, 3, 1),
        supplement=values.years,
        location_tables=values.location_tables,
    )
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "18", "EA"), index, "kitchen_cabinet")
    year = trail["year"]
    # 294.8 / 239.1 = 1.23295... -> 1.2330, kept as "1.2330" (not 1.233).
    assert year["factor"] == "1.2330"
    assert year["index"]["store"] == {"id": str(STORE_ID), "source": "manual", "version": 2}
    assert year["index"]["citation"] == "RSMeans 2024 Building Construction Costs p.778"
    assert year["base"]["store"] is None
    # 425 x 1.2330 x 1 = 524.025 -> 524.03 (half up); x 18 = 9432.54.
    assert (trail["unit_cost"], trail["total"]) == ("524.03", "9432.54")
    agrees(estimate, trail)
    round_trip = json.loads(json.dumps(trail))
    assert round_trip == trail and round_trip["year"]["factor"] == "1.2330"


def test_an_extrapolated_year_records_the_assumption() -> None:
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "2", "EA"), setting_index(2026))
    year = trail["year"]
    assert year["factor"] == "1.0000" and year["index"]["year"] == 2020
    assert (
        year["assumption"] is not None
        and "no published cost index for 2026" in (year["assumption"])
    )
    assert year["from"] == "COST_YEAR setting"
    assert trail["cost_year"] == 2026
    agrees(estimate, trail)


def test_a_converted_year_records_the_conversion() -> None:
    derived = "converted from BLS PPI WPUIP2311001 by the 2019-2020 overlap, +/- 3 %"
    values = values_from_rows(
        [store_row(2023, "280.0", source="bls_ppi", derived=derived, version=1)],
        priority=(MANUAL_SOURCE,),
    )
    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2023, 6, 1),
        supplement=values.years,
    )
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "1", "EA"), index, "kitchen_cabinet")
    assert trail["year"]["conversion"] == derived
    assert trail["year"]["index"]["derived"] == derived
    assert trail["year"]["index"]["store"]["source"] == "bls_ppi"
    agrees(estimate, trail)


# --------------------------------------------------------------------------- the location


def test_a_zip_city_records_the_row_the_zip_and_the_table() -> None:
    index = resolve_cost_index(
        None, cost_year=2026, location_factor=D("1"), state="NY", zip_code="12345"
    )
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "3", "EA"), index, "kitchen_cabinet")
    location = trail["location"]
    assert location == {
        "factor": "0.98",
        "from": index.location_source,
        "lookup": "zip",
        "zip": "12345",
        "lookup_from": "intake",
        "state": "NY",
        "city": "Schenectady",
        "zip_prefixes": "123",
        "cities": 1,
        "low": "0.98",
        "high": "0.98",
        "pages": "720",
        "table_year": 2020,
        "origin": "rsmeans_2020_book",
        "citation": "123 Schenectady 0.98, RSMeans Residential 2020 Location Factors - "
        "Residential p.720",
        "store": None,
    }
    agrees(estimate, trail)


def test_a_state_mean_records_the_cities_averaged_and_no_zip() -> None:
    m = measurements_from_fields([SimpleNamespace(
        section="subject", field="state", value_text="IL", value_num=None, value_date=None,
        source="azure", page=None, confidence=0.99,
    )])  # fmt: skip
    index = resolve_cost_index(m, cost_year=2026, location_factor=D("1"))
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "3", "EA"), index, "kitchen_cabinet")
    location = trail["location"]
    assert (location["lookup"], location["zip"], location["city"]) == ("state", None, None)
    assert location["lookup_from"] == "subject.state (azure)"
    assert (location["factor"], location["cities"], location["low"], location["high"]) == (
        "0.959",
        19,
        "0.92",
        "1.00",
    )
    assert location["store"] is None and location["table_year"] == 2020
    agrees(estimate, trail)


def test_a_store_city_row_names_its_store_row() -> None:
    city = store_row(
        2024,
        "1.05",
        id=CITY_ID,
        series="city",
        region="CA:900-902",
        citation="RSMeans Residential 2024 Location Factors p.720",
        detail={"city": "Los Angeles", "state": "CA", "zip_prefixes": "900-902"},
        version=1,
    )
    values = values_from_rows([city], priority=(MANUAL_SOURCE,))
    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2024, 12, 3),
        zip_code="90001",
        supplement=values.years,
        location_tables=values.location_tables,
    )
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "3", "EA"), index, "kitchen_cabinet")
    location = trail["location"]
    assert (location["table_year"], location["origin"]) == (2024, MANUAL_SOURCE)
    assert location["store"] == {"id": str(CITY_ID), "source": "manual", "version": 1}
    agrees(estimate, trail)


def test_a_setting_factor_records_only_the_factor_and_its_source() -> None:
    _, trail = trail_of(takeoff("kitchen_cabinet", "1", "EA"), setting_index(factor="1.15"))
    location = trail["location"]
    assert (location["factor"], location["from"]) == ("1.15", "COST_LOCATION_FACTOR setting")
    assert all(location[k] is None for k in location if k not in ("factor", "from"))


# --------------------------------------------------------------------------- other kinds


def test_an_unpriced_row_records_the_quantity_the_index_and_why() -> None:
    t = takeoff("patio", "1", "EA")
    index = resolve_cost_index(
        None, cost_year=2026, location_factor=D("1"), state="NY", zip_code="12345"
    )
    missing = "missing: no cost row for 'patio' per EA"
    trail = priced_trail(
        quantity_block(t, written(t)),
        None,
        index,
        context=CONTEXT,
        missing=missing,
        priced_per="SF",
    )
    assert trail["kind"] == UNPRICED
    assert (trail["unit_cost"], trail["total"], trail["base"], trail["rounding"]) == (
        None,
        None,
        None,
        None,
    )
    assert (trail["missing"], trail["priced_per"]) == (missing, "SF")
    assert trail["year"]["cost_year"] == 2026 and trail["location"]["factor"] == "0.98"
    assert trail["quantity"]["value"] == "1"
    assert check(trail) == []
    readable(trail)


def concealed(rcn_source: s.RcnSource) -> tuple[s.BuildingRcn, list[s.ConcealedRow]]:
    rows = [
        SimpleNamespace(section=k.split(".")[0], field=k.split(".")[1], value_num=D(v),
                        value_text=None, value_date=None, source="azure", page=None,
                        confidence=0.99)
        for k, v in {
            "improvements.gla_sqft": "2400",
            "improvements.stories": "2",
            "cost_approach.total_cost_new": "300000",
            "cost_approach.dwelling_cost": "300000",
        }.items()
    ]  # fmt: skip
    m = measurements_from_fields(rows)
    approach = s.cost_approach_from_fields(rows)
    building = s.building_rcn(
        m, approach, cost_year=2026, location_factor=D("0.98"), rcn_source=rcn_source
    )
    assert building is not None
    carve = s.carve_out(s.split_components(building.amount, building.model), [])
    small = s.small_system_inputs(m, approach, [])
    assert m.gla_sf is not None
    return building, s.concealed_rows(building, carve, m.gla_sf.value, small)


@pytest.mark.parametrize("rcn_source", ["appraisal_total", "square_foot"])
def test_a_building_residual_records_its_rcn_and_the_factors_it_used(
    rcn_source: s.RcnSource,
) -> None:
    building, rows = concealed(rcn_source)
    row = next(r for r in rows if r.rcn is not None and r.quantity > 0)
    assert row.rcn is not None
    unit_cost = (row.rcn / row.quantity).quantize(D("0.01"))  # as process_assets writes it
    sqft = building.source == "square_foot_model"
    index = resolve_cost_index(
        None, cost_year=2026, location_factor=D("1"), state="NY", zip_code="12345"
    )
    t = Takeoff(row.label, row.quantity, row.unit, row.formula, method=row.method)
    trail = structure_residual_trail(
        quantity_block(t, {**row.inputs, "concealed": True}, row.assumptions),
        unit_cost=unit_cost,
        total=row.rcn,
        cost_year=2026,
        location_factor=index.location_factor if sqft else None,
        index=index if sqft else None,
        structure=structure_block(row, building),
        context=CONTEXT,
    )
    assert (trail["kind"], trail["base"], trail["rounding"]) == (
        STRUCTURE_RESIDUAL,
        None,
        STRUCTURE_ROUNDING,
    )
    assert (trail["total"], trail["unit_cost"]) == (format(row.rcn, "f"), format(unit_cost, "f"))
    structure = trail["structure"]
    assert (structure["label"], structure["system"], structure["rcn"]) == (
        row.label,
        row.system,
        format(row.rcn, "f"),
    )
    assert structure["building"]["amount"] == format(building.amount, "f")
    assert structure["building"]["source"] == building.source
    assert trail["quantity"]["assumptions"] == list(row.assumptions)
    if sqft:
        assert trail["year"]["cost_year"] == 2026 and trail["location"]["factor"] == "0.98"
        assert trail["location_factor"] == "0.98"
    else:
        # The appraiser's cost new: no location factor applied; its time adjustment is the
        # building's own (in its inputs), not the study's index.
        assert (trail["year"], trail["location"], trail["location_factor"]) == (None, None, None)
        assert "total_cost_new" in structure["building"]["inputs"]
    assert check(trail) == []
    readable(trail)


def test_a_small_system_is_priced_like_a_label_with_its_structure_block() -> None:
    building, rows = concealed("appraisal_total")
    small = next(r for r in rows if r.rcn is None)
    block = structure_block(small, building)
    assert block["rcn"] is None and block["building"] is None
    t = Takeoff(small.label, small.quantity, small.unit, small.formula, method=small.method)
    trail = priced_trail(
        quantity_block(t, small.inputs, small.assumptions),
        None,
        setting_index(),
        context=CONTEXT,
        missing=f"missing: no cost row for {small.label!r} per {small.unit}",
        structure=block,
    )
    assert trail["kind"] == UNPRICED and trail["structure"]["label"] == small.label
    readable(trail)


# --------------------------------------------------------------------------- JSON


def test_json_safe_keeps_decimal_digits_and_refuses_unknown_types() -> None:
    assert json_safe({"a": D("1.2330"), "b": (D("1E+2"), date(2024, 1, 1)), "c": None}) == {
        "a": "1.2330",
        "b": ["100", "2024-01-01"],
        "c": None,
    }
    with pytest.raises(TypeError):
        json_safe(object())


def test_check_reports_a_trail_that_does_not_add_up() -> None:
    estimate, trail = trail_of(takeoff("kitchen_cabinet", "18", "EA"), setting_index())
    assert check(trail) == []
    broken = {**trail, "unit_cost": "425.01"}
    assert any("base x (1 + markup) x year x location" in p for p in check(broken))
    assert any("unit_cost x quantity" in p for p in check(broken))
    assert check({**trail, "base": None}) == [
        "a unit_price trail needs its base, year and location"
    ]
    assert estimate.unit_cost == D("425.00")


def test_the_installed_basis_and_the_model_markup_are_recorded() -> None:
    """STRUCTURE_MODEL.md 9.3: a legacy row on the installed basis records how it got there
    (the section's markups on material, labor, equipment), and a row inside the model the
    general contractor's O&P spread on it; base x (1 + markup) x year x location is the
    unit cost."""
    markup = Markup(Decimal("0.200129"), "model item 10 over components 1-9")
    estimate, trail = trail_of(
        takeoff("carpet", "20", "SF"), setting_index(), basis="installed", markup=markup
    )
    base = trail["base"]
    assert (base["basis"], base["markup"]) == (
        "installed",
        {"rate": "0.200129", "source": "model item 10 over components 1-9"},
    )
    assert base["basis_how"].startswith("installed: material x1.10 + labor x1.72")
    assert check(trail) == []
    bare, bare_trail = trail_of(takeoff("carpet", "20", "SF"), setting_index())
    assert bare_trail["base"]["basis"] == "bare" and bare_trail["base"]["markup"] is None
    assert Decimal(base["unit_cost"]) > Decimal(bare_trail["base"]["unit_cost"])
    assert estimate.unit_cost > bare.unit_cost
    # The table's printed Total Incl O&P column, when it has one, is the installed base.
    agrees(estimate, trail)
