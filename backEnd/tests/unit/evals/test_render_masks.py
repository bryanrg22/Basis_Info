"""``evals.render_masks``: instance outlines and labels drawn on the prepared photo."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from basis.vision.localize import Box, Instance, Rle
from basis.vision.prep import prepare_image
from basis.vision.schemas import ComponentLabel
from evals import render_masks

W, H = 200, 150


def photo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (W, H), (40, 40, 40)).save(out, format="JPEG", quality=95)
    return out.getvalue()


def window(score: float = 0.9, with_mask: bool = True) -> Instance:
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[50:100, 60:140] = 1
    return Instance(
        label=ComponentLabel.WINDOW,
        phrase="window",
        score=score,
        box=Box(x1=0.3, y1=1 / 3, x2=0.7, y2=2 / 3),
        mask=Rle.encode(mask) if with_mask else None,
        area=80 * 50 / (W * H),
    )


def test_render_outlines_the_mask_and_leaves_the_rest() -> None:
    base = Image.open(io.BytesIO(prepare_image(photo()).jpeg))
    for instance in (window(), window(with_mask=False)):
        drawn = np.asarray(render_masks.render(base, [instance]), dtype=int)
        original = np.asarray(base.convert("RGB"), dtype=int)
        assert drawn.shape == (H, W, 3)
        edge, far, inside = drawn[75, 60], drawn[5, 190], drawn[75, 100]
        colour = render_masks.colour(ComponentLabel.WINDOW)
        assert np.abs(edge - np.array(colour)).sum() < 60  # the crisp outline
        assert np.abs(far - original[5, 190]).sum() < 6  # untouched far away
        assert np.abs(inside - original[75, 100]).sum() < 6  # the inside is not filled
        assert np.abs(drawn[75, 54] - original[75, 54]).sum() > 20  # the glow outside
    below = render_masks.render(base, [window(score=0.2)], min_score=0.5)
    assert np.array_equal(np.asarray(below), np.asarray(base.convert("RGB")))


def test_main_writes_a_png_of_the_prepared_size(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(photo())
    record = {
        "file": "a.jpg",
        "repeat": 1,
        "image": {"width": W, "height": H},
        "instances": [window().model_dump(mode="json")],
    }
    other = {"file": "missing.jpg", "repeat": 1, "image": {}, "instances": []}
    raw = tmp_path / "instances_raw.jsonl"
    raw.write_text("\n".join(json.dumps(r) for r in (record, other, record | {"repeat": 2})))
    out = tmp_path / "render"
    assert render_masks.main(["--raw", str(raw), "--photos", str(photos), "--out", str(out)]) == 0
    assert [p.name for p in out.iterdir()] == ["a.r1.png"]
    with Image.open(out / "a.r1.png") as image:
        assert (image.format, image.size) == ("PNG", (W, H))


def test_a_size_mismatch_is_refused(tmp_path: Path) -> None:
    record = {"file": "a.jpg", "image": {"width": 10, "height": 10}, "instances": []}
    try:
        render_masks.render_record(photo(), record, 0.5)
    except ValueError as exc:
        assert "10 x 10" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a size mismatch")
