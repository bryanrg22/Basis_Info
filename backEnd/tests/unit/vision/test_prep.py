"""Image preparation: resize, EXIF strip, re-encode, explicit rejections."""

from __future__ import annotations

import io

import pytest
from fixtures import fixture_path
from PIL import Image

from basis.vision import prep
from basis.vision.prep import ImageRejected, prepare_image


def jpeg(size: tuple[int, int], mode: str = "RGB", **save: object) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else None).save(out, format="JPEG", **save)  # type: ignore[arg-type]
    return out.getvalue()


def png(size: tuple[int, int], mode: str) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size).save(out, format="PNG")
    return out.getvalue()


def test_a_fixture_photo_keeps_its_size() -> None:
    image = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())
    assert (image.width, image.height) == (800, 600) and not image.resized
    assert image.original_format == "JPEG"
    assert Image.open(io.BytesIO(image.jpeg)).format == "JPEG"


def test_oversize_is_downscaled_to_1024_on_the_long_side() -> None:
    image = prepare_image(fixture_path("malformed", "oversize.jpg").read_bytes())
    assert (image.original_width, image.original_height) == (6000, 4000)
    assert (image.width, image.height) == (1024, 683) and image.resized


def test_portrait_is_limited_on_its_long_side() -> None:
    image = prepare_image(jpeg((1500, 3000)))
    assert (image.width, image.height) == (512, 1024)


def test_tiny_is_rejected_not_upscaled() -> None:
    with pytest.raises(ImageRejected) as caught:
        prepare_image(fixture_path("malformed", "tiny.jpg").read_bytes())
    assert caught.value.reason == "too_small"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty"),
        (b"definitely not an image", "not_an_image"),
        (b"\xff\xd8\xff\xe0 truncated jpeg header", "not_an_image"),
    ],
)
def test_unreadable_input_is_rejected(data: bytes, reason: str) -> None:
    with pytest.raises(ImageRejected) as caught:
        prepare_image(data)
    assert caught.value.reason == reason


def test_a_pdf_is_not_an_image() -> None:
    with pytest.raises(ImageRejected) as caught:
        prepare_image(fixture_path("urar_sample.pdf").read_bytes())
    assert caught.value.reason == "not_an_image"


def test_too_many_bytes_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prep, "MAX_IMAGE_BYTES", 100)
    with pytest.raises(ImageRejected) as caught:
        prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())
    assert caught.value.reason == "too_large_bytes"


def test_too_many_pixels_is_rejected_from_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prep, "MAX_PIXELS", 1000 * 1000)
    with pytest.raises(ImageRejected) as caught:
        prepare_image(jpeg((2000, 1000)))
    assert caught.value.reason == "too_many_pixels"


def test_exif_including_gps_is_removed_and_orientation_applied() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 degrees clockwise to display
    exif[0x010F] = "Camera maker"
    exif[0x8825] = {2: (40.0, 26.0, 46.0)}  # GPS IFD: latitude
    source = jpeg((400, 200), exif=exif.tobytes())
    assert Image.open(io.BytesIO(source)).getexif()  # the input carries EXIF
    image = prepare_image(source)
    out = Image.open(io.BytesIO(image.jpeg))
    assert not out.getexif() and "exif" not in out.info and "icc_profile" not in out.info
    assert (image.width, image.height) == (200, 400)  # rotated as the camera intended


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P", "L", "CMYK"])
def test_any_mode_becomes_rgb_jpeg(mode: str) -> None:
    data = png((100, 80), mode) if mode != "CMYK" else jpeg((100, 80), "CMYK")
    out = Image.open(io.BytesIO(prepare_image(data).jpeg))
    assert out.mode == "RGB" and out.format == "JPEG"


def test_output_is_deterministic() -> None:
    data = fixture_path("photos", "02_bathroom.jpg").read_bytes()
    assert prepare_image(data).sha256 == prepare_image(data).sha256


def test_base64_round_trip() -> None:
    import base64

    image = prepare_image(fixture_path("photos", "03_bedroom.jpg").read_bytes())
    assert base64.b64decode(image.base64) == image.jpeg
