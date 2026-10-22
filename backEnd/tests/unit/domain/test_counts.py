"""Count corrections (:mod:`basis.domain.counts`): what a count and an added component take."""

from __future__ import annotations

from typing import Any

import pytest

from basis.domain.counts import MAX_COUNT, count_problem, room_component_problems


@pytest.mark.parametrize("value", [1, 2, MAX_COUNT])
def test_whole_numbers_from_one_are_counts(value: int) -> None:
    assert count_problem(value) is None


@pytest.mark.parametrize("value", [0, -1, MAX_COUNT + 1, True, False, 2.0, 2.5, "2", None, [2]])
def test_anything_else_is_not_a_count(value: Any) -> None:
    problem = count_problem(value)
    assert problem is not None and "whole number from 1" in problem


def test_an_added_component_needs_a_taxonomy_label_and_a_count() -> None:
    assert room_component_problems({"label": "window", "count": 3}) == []
    assert (
        room_component_problems(
            {"label": "light_fixture", "count": 1, "material": "metal", "attachment": "permanent"}
        )
        == []
    )
    assert room_component_problems({"label": "window", "count": 1, "material": None}) == []


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        ({"count": 1}, "needs label"),
        ({"label": "window"}, "needs count"),
        ({"label": "skylight", "count": 1}, "unknown component label 'skylight'"),
        ({"label": "window", "count": 0}, "whole number from 1"),
        ({"label": "window", "count": 1, "material": "oak"}, "unknown material 'oak'"),
        ({"label": "window", "count": 1, "attachment": "glued"}, "unknown attachment 'glued'"),
        ({"label": "window", "count": 1, "colour": "red"}, "unknown room_component fields"),
    ],
)
def test_every_problem_of_an_added_component_is_named(after: dict[str, Any], expected: str) -> None:
    problems = room_component_problems(after)
    assert any(expected in p for p in problems), problems
