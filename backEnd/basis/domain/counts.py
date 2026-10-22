"""Count corrections at the rooms review (manual add): the values a component's count and a
component added to a room take.

Pure, so both sides check the same rules: the decision route refuses a bad value with a 422
before the decision is recorded (the workflow would otherwise start the next revision
without any of the rejection's corrections and flag the stage, ``StudyWorkflow._reject``),
and ``apply_corrections`` checks them again as a ``ValidationError``.

* ``count`` (a ``component`` or ``room_component`` correction): a whole JSON number from 1
  to :data:`MAX_COUNT`; ``true`` is not 1.
* A ``room_component`` correction's ``after``: ``label`` from the component taxonomy
  (:class:`~basis.vision.schemas.ComponentLabel`) and ``count`` are required; ``material``
  and ``attachment`` are optional values of their vocabularies (the rules table is looked
  up by label, material and attachment, so a free-text material would match no rule).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from basis.vision.schemas import Attachment, ComponentLabel, Material

# A count is a number of objects in one room; anything near this is a typo.
MAX_COUNT: Final = 10_000
ROOM_COMPONENT_FIELDS: Final = frozenset({"label", "material", "attachment", "count"})
ROOM_COMPONENT_REQUIRED: Final = frozenset({"label", "count"})
_VOCABULARIES: Final = {
    "material": frozenset(m.value for m in Material),
    "attachment": frozenset(a.value for a in Attachment),
}
_LABELS: Final = frozenset(label.value for label in ComponentLabel)


def count_problem(value: Any) -> str | None:
    """Why ``value`` is not a count, or None when it is one."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_COUNT:
        return f"count {value!r} must be a whole number from 1 to {MAX_COUNT}"
    return None


def room_component_problems(after: Mapping[str, Any]) -> list[str]:
    """Why ``after`` is not a component to add to a room (empty when it is one)."""
    problems: list[str] = []
    unknown = set(after) - ROOM_COMPONENT_FIELDS
    if unknown:
        problems.append(f"unknown room_component fields: {', '.join(sorted(unknown))}")
    missing = ROOM_COMPONENT_REQUIRED - set(after)
    if missing:
        problems.append(f"room_component needs {', '.join(sorted(missing))}")
    if "label" in after and after["label"] not in _LABELS:
        problems.append(f"unknown component label {after['label']!r}")
    if "count" in after and (problem := count_problem(after["count"])) is not None:
        problems.append(problem)
    for name, vocabulary in _VOCABULARIES.items():
        value = after.get(name)
        if value is not None and value not in vocabulary:
            problems.append(f"unknown {name} {value!r}")
    return problems


__all__ = [
    "MAX_COUNT",
    "ROOM_COMPONENT_FIELDS",
    "ROOM_COMPONENT_REQUIRED",
    "count_problem",
    "room_component_problems",
]
