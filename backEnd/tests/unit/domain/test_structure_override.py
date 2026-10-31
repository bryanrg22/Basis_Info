"""The engineer's square-foot model (``ModelOverride``, a ``structure_model`` correction):
the class, type and living area it sets replace the model's own choice in the building RCN,
the source names the correction, a confirmation of the model's choice reads "confirmed", the
record round-trips, and without it nothing changes. Pure."""

from __future__ import annotations

from decimal import Decimal

import pytest
from temporalio.exceptions import ApplicationError as ValidationError

from basis.activities.reprice_corrections import (
    check_reprice,
    check_study_target,
    parse_structure_model,
)
from basis.domain import structure as s
from basis.domain.measurements import BuildingMeasurements, Reading, TextReading

D = Decimal
M = BuildingMeasurements(
    gla_sf=Reading(D(1800), "improvements.gla", "azure"),
    stories=Reading(D(1), "improvements.stories", "azure"),
)
Q3 = s.CostApproach(quality_uad=TextReading("Q3", "improvements.quality", "azure"))


def rcn(override: s.ModelOverride | None = None) -> s.BuildingRcn:
    found = s.building_rcn(M, Q3, cost_year=2020, rcn_source="square_foot", model_override=override)
    assert found is not None
    return found


def test_without_override_nothing_changes() -> None:
    assert rcn().amount == rcn(None).amount
    choice = s.select_model(M, Q3)
    assert choice == s.select_model(M, Q3, None)


def test_class_and_type_replace_the_choice() -> None:
    base = rcn()
    assert (base.model.cls, base.model.type) == ("custom", "1 story")  # Q3, one story
    changed = rcn(s.ModelOverride(model_class="luxury", building_type="2 story", source="c1"))
    assert (changed.model.cls, changed.model.type) == ("luxury", "2 story")
    assert changed.amount != base.amount
    assert "set by c1" in changed.model_choice.class_source
    assert "set by c1" in changed.model_choice.type_source
    assert not changed.model_choice.needs_review or changed.model_choice.assumptions == ()


def test_confirming_the_choice_reads_confirmed_and_keeps_the_amount() -> None:
    base = rcn()
    same = rcn(
        s.ModelOverride(model_class=base.model.cls, building_type=base.model.type, source="c2")
    )
    assert same.amount == base.amount
    assert "confirmed by c2" in same.model_choice.class_source


def test_living_area_sizes_the_model() -> None:
    bigger = rcn(s.ModelOverride(living_area_sf=D(2400), source="c3"))
    assert bigger.amount > rcn().amount
    assert bigger.inputs["gla_sf"] == "2400"
    inputs = s.StructureInputs(
        measurements=M,
        cost_approach=Q3,
        cost_year=2020,
        model_override=s.ModelOverride(living_area_sf=D(2400)),
    )
    assert inputs.living_area == D(2400)


def test_record_round_trips() -> None:
    override = s.ModelOverride("luxury", "3 story", D("2150.5"), "c4")
    assert s.ModelOverride.from_record(override.record()) == override
    assert s.ModelOverride.from_record(None) is None
    # A recorded value outside the vocabulary is dropped, never trusted.
    assert s.ModelOverride.from_record({"model_class": "palace"}) == s.ModelOverride()


def test_parse_structure_model() -> None:
    parsed = parse_structure_model({"model_class": "custom", "living_area_sf": 2000}, "c5")
    assert parsed == s.ModelOverride("custom", None, D(2000), "c5")
    for bad in (
        {},
        {"model_class": "palace"},
        {"building_type": "4 story"},
        {"living_area_sf": 0},
        {"living_area_sf": "abc"},
        {"living_area_sf": 200_001},
        {"exterior": "brick"},
        {"model_class": None},
    ):
        with pytest.raises(ValidationError):
            parse_structure_model(bad, "c")


def test_reprice_shape_and_study_target() -> None:
    import uuid

    print_ = "a" * 64
    assert check_reprice({"fingerprint": print_}) == print_
    for bad in ({}, {"fingerprint": "A" * 64}, {"fingerprint": print_, "x": 1}, {"fingerprint": 1}):
        with pytest.raises(ValidationError):
            check_reprice(bad)
    study = uuid.uuid4()
    check_study_target("reprice", str(study), study)
    with pytest.raises(ValidationError):
        check_study_target("reprice", str(uuid.uuid4()), study)
