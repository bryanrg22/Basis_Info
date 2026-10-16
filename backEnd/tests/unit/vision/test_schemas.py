"""``PhotoAnalysis`` schema: vocabularies, bounds, normalization, strict JSON schema."""

from __future__ import annotations

from typing import Any

import pytest
from fixtures import load_fixture_json
from pydantic import ValidationError

from basis.llm.providers import strict_json_schema
from basis.vision.schemas import (
    COMPONENT_LABELS,
    ROOM_TYPES,
    Component,
    ModelPhotoAnalysis,
    PhotoAnalysis,
    RoomType,
    ambiguous_analysis,
    normalize_room_type,
)


def component(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "label": "carpet",
        "count": 1,
        "material": "fabric",
        "attachment": "permanent",
        "confidence": 0.8,
    }
    base.update(changes)
    return base


def test_room_types_include_the_non_room_answers() -> None:
    assert {"ambiguous", "exterior", "mechanical", "other"} <= set(ROOM_TYPES)


def test_vocabulary_has_about_seventy_labels_plus_other() -> None:
    assert 55 <= len(COMPONENT_LABELS) - 1 <= 85
    assert COMPONENT_LABELS[-1] == "other"
    assert len(set(COMPONENT_LABELS)) == len(COMPONENT_LABELS)
    assert all(label == label.lower() and " " not in label for label in COMPONENT_LABELS)


def test_the_report_row_labels_of_eval_report_19_are_in_the_vocabulary() -> None:
    """The six labels the engineer's report rows needed (EVAL_REPORT sections 13 and 19);
    the model may answer them and they no longer fall back to ``other``."""
    added = {
        "concrete_floor",
        "half_wall",
        "bathroom_accessory",
        "window_guard",
        "shed",
        "concrete_pad",
    }
    assert added <= set(COMPONENT_LABELS)
    assert len(COMPONENT_LABELS) - 1 == 77
    for label in added:
        component = Component.model_validate(
            {
                "label": label.replace("_", " ").title(),
                "count": 1,
                "material": "concrete",
                "attachment": "permanent",
                "confidence": 0.9,
            }
        )
        assert component.label.value == label and component.note is None


def test_fixture_vocabulary_is_covered() -> None:
    vocabulary = load_fixture_json("photos", "labels.json")["vocabulary"]
    assert set(vocabulary["room_type"]) <= set(ROOM_TYPES)
    assert set(vocabulary["component"]) <= set(COMPONENT_LABELS)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("kitchen", RoomType.KITCHEN),
        ("Living Room", RoomType.LIVING_ROOM),
        ("mechanical_room", RoomType.MECHANICAL),
        ("utility room", RoomType.MECHANICAL),
        ("outside", RoomType.EXTERIOR),
        ("unknown", RoomType.AMBIGUOUS),
        ("sauna", RoomType.OTHER),
        (None, RoomType.AMBIGUOUS),
    ],
)
def test_room_type_normalization(text: str | None, expected: RoomType) -> None:
    assert normalize_room_type(text) is expected


def test_unknown_room_type_becomes_other() -> None:
    analysis = PhotoAnalysis(room_type="wine_cellar", room_confidence=0.7)  # type: ignore[arg-type]
    assert analysis.room_type is RoomType.OTHER


def test_unknown_component_label_becomes_other_with_a_note() -> None:
    item = Component(**component(label="Wet Bar"))
    assert item.label.value == "other" and item.note == "Wet Bar"
    assert Component(**component(label="Ceiling Fan")).label.value == "ceiling_fan"


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_confidence_bounds(value: float) -> None:
    with pytest.raises(ValidationError):
        Component(**component(confidence=value))
    with pytest.raises(ValidationError):
        PhotoAnalysis(room_type="kitchen", room_confidence=value)  # type: ignore[arg-type]


def test_count_and_list_bounds() -> None:
    with pytest.raises(ValidationError):
        Component(**component(count=0))
    with pytest.raises(ValidationError):
        PhotoAnalysis(
            room_type="kitchen",  # type: ignore[arg-type]
            room_confidence=0.9,
            components=[Component(**component())] * 61,
        )


def test_unknown_material_or_attachment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Component(**component(material="unobtainium"))
    with pytest.raises(ValidationError):
        Component(**component(attachment="glued"))


def test_extra_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Component(**component(price=100))


def test_model_output_maps_to_an_analysis_that_is_not_flagged() -> None:
    model = ModelPhotoAnalysis(
        room_type="garage",  # type: ignore[arg-type]
        room_confidence=0.9,
        floor=1,
        room_ordinal=1,
        components=[Component(**component(label="garage_door", material="steel"))],
        description="A garage.",
    )
    analysis = model.to_analysis()
    assert analysis.room_type is RoomType.GARAGE and not analysis.needs_review


def test_ambiguous_analysis() -> None:
    analysis = ambiguous_analysis("invalid_model_output")
    assert analysis.room_type is RoomType.AMBIGUOUS and analysis.needs_review
    assert analysis.components == [] and analysis.room_confidence == 0.0


def test_the_model_schema_is_strict_and_hides_review_fields() -> None:
    schema = strict_json_schema(ModelPhotoAnalysis)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "needs_review" not in schema["properties"]
    room_ref = schema["properties"]["room_type"]
    room = schema["$defs"]["RoomType"] if "$ref" in room_ref else room_ref
    assert "ambiguous" in room["enum"]
    component_def = schema["$defs"]["Component"]
    assert component_def["additionalProperties"] is False
    assert set(component_def["required"]) == set(component_def["properties"])
    label = schema["$defs"]["ComponentLabel"]
    assert label["enum"] == list(COMPONENT_LABELS)


def test_over_long_note_and_description_are_cut_not_fatal() -> None:
    """Strict JSON schemas drop maxLength, so a talkative model's 250-character note must
    not fail the photo (28% of one gpt-6.1-sol countables run was lost this way)."""
    item = Component.model_validate(
        {
            "label": "other",
            "note": "x" * 250,
            "count": 2,
            "material": "unknown",
            "attachment": "freestanding",
            "confidence": 0.5,
        }
    )
    assert len(item.note or "") == 200 and item.note.endswith("…")
    analysis = ModelPhotoAnalysis.model_validate(
        {
            "room_type": "kitchen",
            "room_confidence": 0.9,
            "floor": 1,
            "room_ordinal": 1,
            "components": [],
            "description": "y" * 700,
        }
    )
    assert len(analysis.description) == 500
