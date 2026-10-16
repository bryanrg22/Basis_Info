"""``basis.vision.merge``: the one merge rule for decomposed per-photo answers.

The pipeline (``analyze_photo_decomposed``) and the harness (``evals.merge_photo_runs``)
must merge the same way, so the harness's measured result is what the pipeline ships.
"""

from __future__ import annotations

from typing import Any

from basis.vision import merge
from basis.vision.schemas import (
    MAX_COMPONENTS,
    MAX_DESCRIPTION_CHARS,
    Component,
    ModelPhotoAnalysis,
    RoomType,
)
from evals import merge_photo_runs


def component(
    label: str,
    count: int = 1,
    material: str = "unknown",
    attachment: str = "permanent",
    conf: float = 0.5,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "note": note,
        "count": count,
        "material": material,
        "attachment": attachment,
        "confidence": conf,
    }


def analysis(
    room: str,
    components: list[dict[str, Any]],
    *,
    confidence: float = 0.9,
    floor: int | None = 1,
    ordinal: int = 1,
    description: str = "",
) -> ModelPhotoAnalysis:
    return ModelPhotoAnalysis(
        room_type=room,  # type: ignore[arg-type]
        room_confidence=confidence,
        floor=floor,
        room_ordinal=ordinal,
        components=[Component.model_validate(c) for c in components],
        description=description,
    )


def test_the_harness_merge_is_the_pipeline_merge() -> None:
    assert merge_photo_runs.merge_components is merge.merge_components


def test_merge_components_is_a_union_with_the_largest_count_and_the_first_description() -> None:
    merged = merge.merge_components(
        [
            [
                component("window", 2, "wood", "permanent", 0.6, note="bay"),
                component("other", note="sofa"),
                component("carpet"),
            ],
            [
                component("window", 5, "vinyl", "removable", 0.9),
                component("other", note="lamp"),
                component("tile"),
            ],
        ]
    )
    by_label = {c["label"]: c for c in merged if c["label"] != "other"}
    assert list(by_label) == ["window", "carpet", "tile"]  # first-seen order
    window = by_label["window"]
    assert (window["count"], window["confidence"]) == (5, 0.9)
    assert (window["material"], window["attachment"], window["note"]) == (
        "wood",
        "permanent",
        "bay",
    )
    assert [c["note"] for c in merged if c["label"] == "other"] == ["sofa", "lamp"]


def test_merge_components_leaves_its_inputs_alone() -> None:
    first = [component("window", 2, "wood")]
    second = [component("window", 5, "vinyl")]
    merge.merge_components([first, second])
    assert first == [component("window", 2, "wood")]
    assert second == [component("window", 5, "vinyl")]


def test_merge_components_of_nothing_is_nothing() -> None:
    assert merge.merge_components([]) == []
    assert merge.merge_components([[], []]) == []


def test_merge_analyses_takes_the_room_from_the_surfaces_answer() -> None:
    countables = analysis(
        "kitchen",
        [
            component("kitchen_cabinet", 6, "wood", conf=0.9),
            component("countertop", 2, "granite", conf=0.5),
            component("other", note="bar stools"),
        ],
        confidence=0.95,
        floor=1,
        ordinal=1,
        description="Six cabinets, two countertops.",
    )
    surfaces = analysis(
        "dining_room",
        [
            component("tile", 1, "ceramic", conf=0.8),
            component("countertop", 1, "quartz", conf=0.8),  # a label both list
        ],
        confidence=0.7,
        floor=2,
        ordinal=2,
        description="Ceramic tile floor.",
    )
    merged = merge.merge_analyses(countables, surfaces)
    result = merged.analysis
    assert merged.dropped == 0
    assert (result.room_type, result.room_confidence) == (RoomType.DINING_ROOM, 0.7)
    assert (result.floor, result.room_ordinal) == (2, 2)
    assert [c.label.value for c in result.components] == [
        "kitchen_cabinet",
        "countertop",
        "tile",
        "other",
    ]
    countertop = result.components[1]
    assert (countertop.count, countertop.confidence) == (2, 0.8)
    assert countertop.material.value == "granite"  # the countables call's description
    assert result.description == "Six cabinets, two countertops. Ceramic tile floor."
    assert not result.needs_review  # review is the caller's job


def test_merge_analyses_keeps_the_schema_caps() -> None:
    half = MAX_COMPONENTS // 2
    countables = analysis(
        "bedroom", [component("other", note=f"c{i}") for i in range(half)], description="a" * 400
    )
    surfaces = analysis(
        "bedroom",
        [component("other", note=f"s{i}") for i in range(half + 1)],
        description="b" * 400,
    )
    merged = merge.merge_analyses(countables, surfaces)
    assert merged.dropped == 1
    assert len(merged.analysis.components) == MAX_COMPONENTS
    assert len(merged.analysis.description) == MAX_DESCRIPTION_CHARS
    assert merged.analysis.description.startswith("a" * 400 + " b")


def test_merge_analyses_with_one_empty_description_has_no_stray_space() -> None:
    merged = merge.merge_analyses(
        analysis("kitchen", [], description=""), analysis("kitchen", [], description="Tile.")
    )
    assert merged.analysis.description == "Tile."
    assert merged.analysis.components == []
