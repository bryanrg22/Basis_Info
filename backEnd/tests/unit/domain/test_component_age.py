"""A component's own age (EVAL_REPORT 102, ``basis.domain.component_age``): each rule of the
correction, the record, the clearing correction, the age as of the placed-in-service year,
the age-life rate, and the UAD 3.6 suggestion (never applied). Fabricated values."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.domain import component_age as ca

GOOD = {
    "effective_age": 2,
    "economic_life": 20,
    "age_source": "appraisal_update_status",
    "age_source_detail": "Kitchen Fully Updated, 1-5 years",
}


def test_a_complete_age_has_no_problem_and_is_recorded() -> None:
    assert ca.problems(GOOD, "the appraisal and the photographs") == []
    rec = ca.record(GOOD, " the appraisal ", from_asset_id="a1", revision=2)
    assert rec == {
        "effective_age": "2",
        "year_installed": None,
        "economic_life": "20",
        "source": "appraisal_update_status",
        "source_detail": "Kitchen Fully Updated, 1-5 years",
        "reason": "the appraisal",
        "from_asset_id": "a1",
        "revision": 2,
    }
    age = ca.from_inputs({ca.INPUT_KEY: rec})
    assert age is not None and age.age_at(None) == Decimal(2)


def test_a_year_installed_is_an_age_as_of_the_placed_in_service_year() -> None:
    after = {**GOOD, "year_installed": 2021}
    del after["effective_age"]
    assert ca.problems(after, "r") == []
    age = ca.from_inputs({ca.INPUT_KEY: ca.record(after, "r", from_asset_id="a", revision=1)})
    assert age is not None
    assert age.age_at(2025) == Decimal(4)
    assert age.age_at(None) is None  # no intake: nothing to date it against


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"year_installed": 2020}, "exactly one of"),  # both
        ({"effective_age": None}, "exactly one of"),  # neither, with other fields
        ({"effective_age": -1}, "from 0 to below 200"),
        ({"effective_age": "2"}, "from 0 to below 200"),  # a string is not a number
        ({"effective_age": True}, "from 0 to below 200"),
        ({"effective_age": 2.125}, "two decimals"),
        ({"economic_life": 0}, "above 0"),
        ({"economic_life": None}, "above 0"),
        ({"effective_age": 20}, "not below economic_life"),
        ({"age_source": "guess"}, "age_source must be one of"),
        ({"age_source_detail": "  "}, "age_source_detail"),
        ({"age_source_detail": "x" * 301}, "age_source_detail"),
    ],
)
def test_each_rule_refuses(change: dict[str, object], fragment: str) -> None:
    after = {**GOOD, **change}
    found = ca.problems(after, "reason")
    assert any(fragment in p for p in found), found


def test_a_bad_year_installed_is_refused() -> None:
    after = {k: v for k, v in GOOD.items() if k != "effective_age"}
    for year in (1799, 2201, 2020.5, "2020", True):
        found = ca.problems({**after, "year_installed": year}, "r")
        assert any("calendar year" in p for p in found)


def test_the_reason_is_required_to_set_and_to_clear() -> None:
    assert ca.problems(GOOD, None) == ["a component age needs the correction's reason"]
    assert ca.problems({"effective_age": None}, " ") == [
        "a component age needs the correction's reason"
    ]


def test_clearing_takes_the_age_alone_and_records_nothing() -> None:
    assert ca.problems({"effective_age": None}, "replaced after all") == []
    assert ca.problems({"year_installed": None}, "r") == []
    assert ca.record({"effective_age": None}, "r", from_asset_id="a", revision=3) is None
    # Clearing with another field is not a clear: it is an incomplete age.
    assert ca.problems({"effective_age": None, "economic_life": 20}, "r")


def test_a_correction_without_age_fields_is_not_checked() -> None:
    assert ca.problems({"bucket": "y5"}, None) == []
    assert not ca.has_age({"unit_cost": 1})
    rest, age = ca.split({"bucket": "y5", **GOOD})
    assert rest == {"bucket": "y5"} and age == GOOD


def test_the_rate_is_age_over_life_to_six_places() -> None:
    assert ca.age_life_rate(Decimal(2), Decimal(20)) == Decimal("0.100000")
    assert ca.age_life_rate(Decimal(1), Decimal(3)) == Decimal("0.333333")
    assert ca.age_life_rate(Decimal(2), Decimal(3)) == Decimal("0.666667")


@pytest.mark.parametrize(
    ("status", "timeframe", "span", "applies"),
    [
        ("FullyUpdated", "LessThanOneYear", (Decimal(0), Decimal(1)), "the room's finishes"),
        ("FullyUpdated", "OneToFiveYears", (Decimal(1), Decimal(5)), "the room's finishes"),
        (
            "PartiallyUpdated",
            "FiveToTenYears",
            (Decimal(5), Decimal(10)),
            "some of the room's components",
        ),
        ("FullyUpdated", "TenOrMoreYears", (Decimal(10), None), "the room's finishes"),
        ("PartiallyUpdated", None, (None, None), "some of the room's components"),
        ("NotUpdated", "OneToFiveYears", (None, None), "none"),
    ],
)
def test_the_uad_update_status_is_a_suggestion(
    status: str, timeframe: str | None, span: tuple[object, object], applies: str
) -> None:
    s = ca.suggestion("Kitchen", "LevelOne", status, timeframe)
    assert (s.age_from, s.age_to) == span and s.applies_to == applies
    if applies != "none" and timeframe is not None:
        assert "suggestion only" in s.note
