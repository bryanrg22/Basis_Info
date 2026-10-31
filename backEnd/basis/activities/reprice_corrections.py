"""The two assets-review corrections that rebuild the assets step without classifying
again (docs/api/REPRICE.md).

* ``reprice`` (``entity_id`` = the study): carry every uncorrected asset's classification
  from the rejected revision and price it again with the study's current intake and cost
  index store. ``after`` is ``{"fingerprint": "<64 hex>"}``: the inputs the engineer's
  impact preview was computed on (:func:`basis.costs.reprice.fingerprint`).
* ``structure_model`` (``entity_id`` = the study): the engineer's confirmation or
  correction of the structure model's RSMeans square-foot model, ``after`` a subset of
  ``model_class``, ``building_type``, ``living_area_sf``; it implies the carry.

Both are recorded by ``POST /v1/studies/{id}/assets/reprice`` (never by the decisions
route, so a rebuild always follows a preview), carried to ``process_assets`` with the
rejection's corrections, and checked here by the API, ``apply_corrections`` and
``process_assets`` with the same functions.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any, Final, get_args

from basis.activities._support import validation_error
from basis.activities.corrections import parse_number
from basis.domain.structure import BuildingType, Class, ModelOverride
from basis.errors.codes import E

REPRICE: Final = "reprice"
STRUCTURE_MODEL: Final = "structure_model"
# The study-level correction types: their ``entity_id`` is the study itself.
STUDY_TARGETED: Final = frozenset({REPRICE, STRUCTURE_MODEL})
STRUCTURE_MODEL_FIELDS: Final = frozenset({"model_class", "building_type", "living_area_sf"})
FINGERPRINT: Final = re.compile(r"[0-9a-f]{64}")
MAX_LIVING_AREA_SF: Final = Decimal(100_000)


def check_study_target(entity_type: str, entity_id: str | uuid.UUID, study_id: uuid.UUID) -> None:
    """A study-level correction names its own study."""
    if str(entity_id) != str(study_id):
        raise validation_error(
            f"a {entity_type} correction's entity_id is the study ({study_id}), not {entity_id}",
            E.CORRECT_STUDY_TARGET,
        )


def check_reprice(after: dict[str, Any]) -> str:
    """Exactly ``{"fingerprint": <64 lowercase hex>}``; returns the fingerprint."""
    value = after.get("fingerprint")
    if (
        set(after) != {"fingerprint"}
        or not isinstance(value, str)
        or not FINGERPRINT.fullmatch(value)
    ):
        raise validation_error(
            "a reprice correction takes exactly a fingerprint (64 lowercase hex characters)",
            E.CORRECT_REPRICE_SHAPE,
        )
    return value


def parse_structure_model(after: dict[str, Any], source: str) -> ModelOverride:
    """The override of a ``structure_model`` correction: known members only, at least one,
    a class and type from the RSMeans vocabularies and a living area in (0, 100,000] SF."""
    unknown = set(after) - STRUCTURE_MODEL_FIELDS
    if unknown or not after:
        raise validation_error(
            "a structure_model correction takes one or more of "
            f"{', '.join(sorted(STRUCTURE_MODEL_FIELDS))}"
            + (f" (unknown: {', '.join(sorted(unknown))})" if unknown else ""),
            E.CORRECT_STRUCTURE_MODEL,
        )
    model_class = after.get("model_class")
    if model_class is not None and model_class not in get_args(Class):
        raise validation_error(
            f"model_class {model_class!r} is not one of {', '.join(get_args(Class))}",
            E.CORRECT_STRUCTURE_MODEL,
        )
    building_type = after.get("building_type")
    if building_type is not None and building_type not in get_args(BuildingType):
        raise validation_error(
            f"building_type {building_type!r} is not one of {', '.join(get_args(BuildingType))}",
            E.CORRECT_STRUCTURE_MODEL,
        )
    area = parse_number(after.get("living_area_sf"), "living_area_sf")
    if area is not None and not Decimal(0) < area <= MAX_LIVING_AREA_SF:
        raise validation_error(
            f"living_area_sf {area} must be above 0 and at most {MAX_LIVING_AREA_SF}",
            E.CORRECT_STRUCTURE_MODEL,
        )
    if model_class is None and building_type is None and area is None:
        raise validation_error(
            "a structure_model correction sets at least one member", E.CORRECT_STRUCTURE_MODEL
        )
    return ModelOverride(
        model_class=model_class, building_type=building_type, living_area_sf=area, source=source
    )


def override_record(override: ModelOverride) -> dict[str, str | None]:
    return override.record()


__all__ = [
    "REPRICE",
    "STRUCTURE_MODEL",
    "STRUCTURE_MODEL_FIELDS",
    "STUDY_TARGETED",
    "check_reprice",
    "check_study_target",
    "override_record",
    "parse_structure_model",
]
