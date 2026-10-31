"""What a revision was priced with, read back from its cost trails, and the comparison with
the current inputs (``basis.evidence.pricing``): the result uses the recorded year factor,
never a newer store value; every input change is a named reason; a property class change
asks for a new classification. Pure; synthetic rows only.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from basis.costs.indices.select import MANUAL_SOURCE, values_from_rows
from basis.domain.cost_trail import TrailContext, priced_trail, quantity_block
from basis.domain.costs import estimate_cost, year_factor_index
from basis.domain.measurements import (
    BuildingMeasurements,
    CostIndex,
    Reading,
    resolve_cost_index,
)
from basis.domain.structure import ModelOverride, StructureInputs, building_rcn
from basis.domain.takeoffs import Takeoff
from basis.evidence.pricing import (
    current_pricing,
    pricing_status,
    recorded_pricing,
    structure_inputs_as_priced,
    year_from_trail,
)

D = Decimal
STORE_ID = uuid.UUID("5f0c6a52-0000-4000-8000-0000000000c3")
GLA = BuildingMeasurements(gla_sf=Reading(D(1800), "improvements.gla", "azure"))


def store_row(year: int, value: str, version: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=STORE_ID,
        source=MANUAL_SOURCE,
        series="historical",
        year=year,
        region="US",
        value=D(value),
        as_of=date(year, 7, 1),
        citation=f"Gordian, the actual July {year} Historical Index",
        derived=None,
        detail=None,
        version=version,
    )


def index_for(year: int, *rows: SimpleNamespace) -> CostIndex:
    values = values_from_rows(rows, priority=(MANUAL_SOURCE, "rsmeans_2020_book"))
    return resolve_cost_index(
        GLA,
        cost_year=year,
        location_factor=D("1.0"),
        placed_in_service=date(year, 3, 1),
        state="CA",
        supplement=values.years,
        location_tables=values.location_tables,
    )


def trail_for(index: CostIndex, intake_version: int | None = 2) -> dict[str, Any]:
    takeoff = Takeoff(
        component="carpet",
        quantity=D("120.00"),
        unit="SF",
        formula="room area",
        inputs={"component": "carpet"},
        method="area",
        assumptions=(),
    )
    cost = estimate_cost(
        takeoff,
        index.cost_year,
        index.location_factor,
        year_source=index.year_source,
        location_source=index.location_source,
        year=index.year,
    )
    return priced_trail(
        quantity_block(takeoff, dict(takeoff.inputs)),
        cost,
        index,
        context=TrailContext(intake_version, "residential_rental"),
    )


def structure_for(index: CostIndex) -> StructureInputs:
    return StructureInputs(
        measurements=GLA,
        cost_approach=None,
        cost_year=index.cost_year,
        year_source=index.year_source,
        location_factor=index.location_factor,
        location_source=index.location_source,
        year=index.year,
        rcn_source="square_foot",
    )


def current_of(index: CostIndex, intake_version: int | None = 2, cls: str = "residential_rental"):
    return current_pricing(
        structure_for(index),
        intake_version=intake_version,
        property_class=cls,
        location_citation=None if index.location is None else index.location.citation(),
    )


def test_year_factor_reads_back_exactly_as_recorded() -> None:
    index = index_for(2024, store_row(2024, "294.8"))
    trail = trail_for(index)
    year = year_from_trail(trail["year"])
    assert index.year is not None
    assert year.factor == index.year.factor == D("1.2330")
    assert year.index.index == D("294.8")
    assert year.index.store is not None and year.index.store.version == 1
    assert year.citation() == index.year.citation()


def test_nothing_recorded_is_not_stale_and_says_so() -> None:
    index = index_for(2024, store_row(2024, "294.8"))
    assert recorded_pricing([None, None]) is None
    status = pricing_status(None, current_of(index), structure_for(index))
    assert status.reasons == ["not_recorded"]
    assert not status.stale and status.remedy is None
    assert status.priced_with.recorded is False


def test_same_inputs_are_not_stale() -> None:
    index = index_for(2024, store_row(2024, "294.8"))
    recorded = recorded_pricing([trail_for(index)])
    status = pricing_status(recorded, current_of(index), structure_for(index))
    assert status.reasons == [] and not status.stale and status.remedy is None
    assert status.priced_with == status.current


def test_a_new_store_version_is_index_updated_and_never_applied() -> None:
    priced = index_for(2024, store_row(2024, "290.0"))
    now = index_for(2024, store_row(2024, "294.8", version=2))
    recorded = recorded_pricing([trail_for(priced)])
    live = structure_for(now)
    status = pricing_status(recorded, current_of(now), live)
    assert status.reasons == ["index_updated"] and status.stale and status.remedy == "reprice"
    assert status.priced_with.year_store_version == 1
    assert status.current.year_store_version == 2
    # The result's structure is computed with the recorded factor, not the store's new one.
    as_priced = structure_inputs_as_priced(live, recorded)
    assert as_priced is not None and as_priced.year is not None and priced.year is not None
    assert as_priced.year.factor == priced.year.factor != now.year.factor  # type: ignore[union-attr]
    before = building_rcn(GLA, None, cost_year=2024, year=as_priced.year, rcn_source="square_foot")
    applied = building_rcn(GLA, None, cost_year=2024, year=priced.year, rcn_source="square_foot")
    assert before is not None and applied is not None and before.amount == applied.amount


def test_intake_cost_year_and_class_changes_are_named() -> None:
    priced = index_for(2023)
    now = index_for(2024, store_row(2024, "294.8"))
    recorded = recorded_pricing([trail_for(priced, intake_version=2)])
    status = pricing_status(
        recorded, current_of(now, intake_version=3, cls="nonresidential"), structure_for(now)
    )
    assert status.reasons == ["intake_changed", "cost_year_changed", "property_class_changed"]
    assert status.remedy == "reject_assets"


def test_override_is_read_from_the_concealed_rows() -> None:
    index = index_for(2024, store_row(2024, "294.8"))
    override = ModelOverride("custom", "2 story", D(2000), "correction c1")
    recorded = recorded_pricing([trail_for(index)], [{"structure_model": override.record()}])
    assert recorded is not None and recorded.model_override == override
    as_priced = structure_inputs_as_priced(structure_for(index), recorded)
    assert as_priced is not None and as_priced.model_override == override
    status = pricing_status(recorded, current_of(index), structure_for(index))
    assert (status.priced_with.model_class, status.priced_with.building_type) == (
        "custom",
        "2 story",
    )
    assert status.priced_with.living_area_sf == D(2000)


def test_setting_priced_revision_round_trips() -> None:
    index = CostIndex(2026, None, D("1.0"), None, year_factor_index(2026))
    recorded = recorded_pricing([trail_for(index, intake_version=None)])
    assert recorded is not None and recorded.intake_version is None
    status = pricing_status(
        recorded,
        current_pricing(
            structure_for(index),
            intake_version=None,
            property_class="residential_rental",
            location_citation=None,
        ),
        structure_for(index),
    )
    assert not status.stale
