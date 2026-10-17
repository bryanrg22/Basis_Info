"""``basis.vision.surface_crops``: segmentation-guided surface crops and the material classes
the report rows distinguish (EVAL_REPORT section 26)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from basis.vision import surface_crops as sc


def test_mask_box_widens_by_the_margin_and_clamps() -> None:
    mask = np.zeros((100, 200), dtype=bool)
    mask[40:60, 10:50] = True
    # margin 0.05 of the longer side (200) = 10 px
    assert sc.mask_box(mask, margin=0.05) == (0, 30, 60, 70)
    assert sc.mask_box(np.zeros((5, 5), dtype=bool)) is None


def test_crop_greys_out_everything_but_the_surface_and_scales_the_long_side() -> None:
    image = Image.new("RGB", (200, 100), (255, 0, 0))
    mask = np.zeros((100, 200), dtype=bool)
    mask[:, 50:150] = True  # the middle half is the surface
    crop = sc.crop_image(image, mask, side=400)
    assert crop is not None
    # box: columns 50..150 widened by 0.04 x 200 = 8 px each side -> 116 x 100, scaled to 400
    assert crop.size == (400, 345)
    pixels = np.asarray(crop)
    middle = pixels[:, 150:250].reshape(-1, 3).mean(axis=0)
    right = pixels[:, -10:].reshape(-1, 3).mean(axis=0)
    assert middle[0] > 240 and middle[1] < 15  # untouched red
    # 25% red + 75% grey(128): R = 0.25*255 + 0.75*128 = 159.75, G = 96
    assert right[0] == pytest.approx(160, abs=3) and right[1] == pytest.approx(96, abs=3)


def test_crop_rejects_a_mask_of_another_size() -> None:
    with pytest.raises(ValueError):
        sc.crop_image(Image.new("RGB", (10, 10)), np.ones((5, 5), dtype=bool))


def test_canonical_maps_answers_and_label_free_text_onto_the_same_classes() -> None:
    assert sc.canonical("gypsum") == sc.canonical("paint") == sc.canonical("plaster")
    assert sc.canonical("painted_drywall") == "painted_drywall"
    assert sc.canonical("embossed_wallpaper") == "wallpaper"
    assert sc.canonical("painted_block") == "painted_masonry"
    assert sc.canonical("painted_wood") == sc.canonical("wood") == "wood"
    assert sc.canonical("ceramic_subway_tile") == "ceramic_tile"
    for empty in (None, "", "unknown", "none", "other"):
        assert sc.canonical(empty) is None


def test_component_classes_reads_floor_labels_and_surface_materials() -> None:
    found = sc.component_classes(
        [
            {"label": "carpet", "material": "carpet"},
            {"label": "tile", "material": "ceramic tile"},
            {"label": "wall_finish", "material": "painted_block"},
            {"label": "wainscoting", "material": "embossed_wallpaper"},
            {"label": "ceiling_finish", "material": None},
            {"label": "window", "material": "glass"},
        ]
    )
    assert found == {
        "floor": {"carpet", "ceramic_tile"},
        "wall": {"painted_masonry"},
        "wall_lower": {"wallpaper"},
        "ceiling": set(),
    }
    # tile reads its material: stone stays stone
    assert sc.floor_class("tile", "stone") == "stone"
    assert sc.floor_class("window", "glass") is None


def test_answer_classes_reads_the_lower_band_only_for_walls() -> None:
    wall = sc.CropAnswer(
        material=sc.CropMaterial.PAINTED_DRYWALL,
        lower_finish=sc.LowerFinish.WALLPAPER,
        confidence=0.8,
        evidence="pattern",
    )
    assert sc.answer_classes("wall", wall) == {
        "wall": {"painted_drywall"},
        "wall_lower": {"wallpaper"},
    }
    plain = wall.model_copy(update={"lower_finish": sc.LowerFinish.NONE})
    assert sc.answer_classes("wall", plain) == {"wall": {"painted_drywall"}}
    floor = wall.model_copy(update={"material": sc.CropMaterial.UNKNOWN})
    assert sc.answer_classes("floor", floor) == {}


def test_long_evidence_is_clipped_not_rejected() -> None:
    answer = sc.CropAnswer.model_validate(
        {"material": "carpet", "lower_finish": "none", "confidence": 1, "evidence": "x" * 500}
    )
    assert len(answer.evidence) <= 200
