"""``basis.vision.ade20k``: ADE20K class names read as component labels (EVAL_REPORT 26)."""

from __future__ import annotations

from basis.vision import ade20k
from basis.vision.schemas import ComponentLabel

L = ComponentLabel


def test_names_are_normalized_across_checkpoint_spellings() -> None:
    assert ade20k.normalize("Window ") == "window"
    assert ade20k.normalize("trash_can") == "trash can"
    assert ade20k.normalize("screen-door") == "screen door"
    # Mask2Former spells it windowpane, OneFormer window: both are windows
    assert ade20k.target("windowpane") == ade20k.target("window ")
    assert ade20k.target("windowpane") is not None
    assert ade20k.target("windowpane").label(exterior=False) is L.WINDOW  # type: ignore[union-attr]


def test_inside_or_outside_classes_follow_the_photo() -> None:
    door, light = ade20k.target("door"), ade20k.target("light")
    assert door is not None and light is not None
    assert door.label(exterior=False) is L.INTERIOR_DOOR
    assert door.label(exterior=True) is L.EXTERIOR_DOOR
    assert light.label(exterior=False) is L.LIGHT_FIXTURE
    assert light.label(exterior=True) is L.OUTDOOR_LIGHTING
    street = ade20k.target("streetlight")
    assert street is not None and street.label(exterior=False) is L.OUTDOOR_LIGHTING


def test_furniture_people_and_surfaces_are_not_objects() -> None:
    for name in ("bed ", "sofa", "person", "car", "wall", "floor", "ceiling", "rug"):
        assert ade20k.target(name) is None, name
    assert ade20k.surface("Wall") == "wall"
    assert ade20k.surface("floor") == "floor"
    assert ade20k.surface("rug") is None


def test_exterior_by_sky_or_by_a_large_share_of_outdoor_classes() -> None:
    assert ade20k.is_exterior({"sky": 0.02})
    assert not ade20k.is_exterior({"sky": 0.019, "wall": 0.6})
    # a close exterior shot of a sign: no sky, mostly ground, trees and building
    assert ade20k.is_exterior({"earth": 0.15, "tree": 0.06, "building": 0.05})
    assert not ade20k.is_exterior({"plant": 0.3, "wall": 0.5, "tree": 0.1})
    assert not ade20k.is_exterior({})


def test_every_target_is_a_vocabulary_label() -> None:
    for name, target in ade20k.CLASS_MAP.items():
        assert ade20k.normalize(name) == name
        assert target.interior in ComponentLabel and target.exterior in ComponentLabel
        assert L.OTHER not in (target.interior, target.exterior)
