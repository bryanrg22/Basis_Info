"""HEIC and HEIF photos (an iPhone's format) are read everywhere a photo is opened as
uploaded, and shown to a browser as JPEG (EVAL_REPORT 81).

The fixtures (``tests/fixtures/heic/``) are coloured rectangles encoded by Apple's own
encoder: a red block marks the picture's top left, so where it lands after decoding says
whether the rotation was applied, and applied once.

* :func:`basis.vision.prep.prepare_image`: the model's copy of a HEIC, upright, stripped,
  alpha on white; a cut-short or damaged HEIC is refused as not an image, and the pixel
  limit is checked from the header as for a JPEG.
* :func:`basis.vision.display.display_jpeg`: the browser's copy, upright, at most 4096 px,
  the colour profile kept and EXIF dropped; one conversion at a time per process.
* The other readers of the uploaded bytes: the surface crops and the report's exhibit.
* The registration is idempotent; JPEG preparation is unchanged (regression).
"""

from __future__ import annotations

import io
import struct
import threading
import time

import numpy as np
import pytest
from fixtures import fixture_path, heic_fixture
from PIL import Image, ImageCms

from basis.report.exhibit import compose
from basis.vision import display, heif, prep
from basis.vision.display import DISPLAY_MAX_SIDE, display_jpeg
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.surface_crops import _open_rgb

RED = (230, 30, 30)
# prepare_image(01_kitchen.jpg) on main at eef2241, before decode_photo was split out.
JPEG_PREPARED_SHA256 = "78c38bcd6769f4d8883537474517a0fea7de20244907b7785f0f5bf925b4a65f"


def red_corner(image: Image.Image) -> str:
    """Which corner holds the red block: ``tl``, ``tr``, ``bl`` or ``br``."""
    pixels = np.asarray(image.convert("RGB")).astype(int)
    h, w, _ = pixels.shape
    corners = {
        "tl": pixels[: h // 6, : w // 6],
        "tr": pixels[: h // 6, -w // 6 :],
        "bl": pixels[-h // 6 :, : w // 6],
        "br": pixels[-h // 6 :, -w // 6 :],
    }
    return max(corners, key=lambda k: float((corners[k][..., 0] - corners[k][..., 2]).mean()))


def jpeg(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG"
    return image


# --------------------------------------------------------------------------- the model's copy


def test_a_heic_is_prepared_like_a_jpeg() -> None:
    prepared = prepare_image(heic_fixture("photo.heic"))
    assert prepared.original_format == "HEIF"
    assert (prepared.width, prepared.height) == (320, 240)
    out = jpeg(prepared.jpeg)
    assert out.mode == "RGB" and red_corner(out) == "tl"


def test_the_containers_rotation_is_applied_once() -> None:
    prepared = prepare_image(heic_fixture("rotated_90cw.heic"))
    assert (prepared.width, prepared.height) == (240, 320)
    assert (prepared.original_width, prepared.original_height) == (240, 320)
    assert red_corner(jpeg(prepared.jpeg)) == "tr"


def test_the_plugin_reports_exif_orientation_one_after_rotating() -> None:
    """What keeps ``exif_transpose`` from turning the picture again (heif module doc)."""
    heif.register()
    image = Image.open(io.BytesIO(heic_fixture("rotated_90cw.heic")))
    assert image.size == (240, 320)
    assert image.getexif().get(0x0112) in (None, 1)


def test_a_transparent_heic_is_composited_on_white() -> None:
    prepared = prepare_image(heic_fixture("alpha.heic"))
    out = np.asarray(jpeg(prepared.jpeg)).astype(int)
    assert out[2, 2].min() >= 245  # the transparent corner is white
    r, g, b = out[75, 100]
    assert r > 200 and g > 160 and b < 80  # the yellow ellipse


def test_the_prepared_heic_carries_no_metadata() -> None:
    out = jpeg(prepare_image(heic_fixture("photo.heic")).jpeg)
    assert "exif" not in out.info and "icc_profile" not in out.info


@pytest.mark.parametrize("name", ["photo.heic", "rotated_90cw.heic", "alpha.heic"])
def test_a_heic_cut_short_anywhere_is_not_an_image(name: str) -> None:
    """Every length short of the whole file is refused, never decoded with blank tiles
    (heif module doc), and never raised as anything but :class:`ImageRejected`."""
    data = heic_fixture(name)
    for keep in range(1, len(data)):
        with pytest.raises(ImageRejected) as caught:
            prepare_image(data[:keep])
        assert caught.value.reason == "not_an_image", keep


def test_box_sizes_decide_cut_short() -> None:
    box = struct.pack(">I", 16) + b"free" + b"\0" * 8
    large = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 24) + b"\0" * 8
    to_end = struct.pack(">I", 0) + b"mdat" + b"\0" * 5
    assert not heif.cut_short(box + large + to_end)
    assert heif.cut_short(box + large[:-1])  # the 64-bit size says one byte more
    assert heif.cut_short(box + large[:12])  # the 64-bit size itself cut
    assert heif.cut_short(box + b"\0\0\0")  # a box header cut
    assert heif.cut_short(struct.pack(">I", 4) + b"free")  # a size below its own header
    assert not heif.cut_short(heic_fixture("photo.heic"))


def test_damaged_image_data_is_not_an_image() -> None:
    data = bytearray(heic_fixture("photo.heic"))
    data[-200:] = b"\x00" * 200  # the coded picture, zeroed
    with pytest.raises(ImageRejected) as caught:
        prepare_image(bytes(data))
    assert caught.value.reason == "not_an_image"


def test_the_pixel_limit_is_read_from_the_heic_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prep, "MAX_PIXELS", 320 * 240 - 1)
    with pytest.raises(ImageRejected) as caught:
        prepare_image(heic_fixture("photo.heic"))
    assert caught.value.reason == "too_many_pixels"


def test_jpeg_preparation_is_unchanged() -> None:
    """Regression: the same JPEG gives the same prepared bytes as before the refactor
    (sha256 recorded from main at eef2241)."""
    prepared = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())
    assert prepared.original_format == "JPEG"
    assert prepared.sha256 == JPEG_PREPARED_SHA256


def test_registering_twice_is_harmless() -> None:
    heif.register()
    heif.register()
    assert Image.registered_extensions()[".heic"] == "HEIF"
    assert prepare_image(heic_fixture("photo.heic")).width == 320


# --------------------------------------------------------------------------- the browser's copy


def test_the_browser_copy_is_an_upright_jpeg() -> None:
    plain = jpeg(display_jpeg(heic_fixture("photo.heic")))
    rotated = jpeg(display_jpeg(heic_fixture("rotated_90cw.heic")))
    assert plain.size == (320, 240) and red_corner(plain) == "tl"
    assert rotated.size == (240, 320) and red_corner(rotated) == "tr"


def test_the_browser_copy_is_at_most_the_display_side(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DISPLAY_MAX_SIDE == 4096
    monkeypatch.setattr(display, "DISPLAY_MAX_SIDE", 100)
    assert jpeg(display_jpeg(heic_fixture("photo.heic"))).size == (100, 75)


def test_the_browser_copy_keeps_the_colour_profile_and_drops_exif(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    exif[0x0112] = 1
    exif[0x8825] = {1: "N"}  # a GPS block

    def decoded(data: bytes) -> Image.Image:
        image = Image.new("RGB", (80, 60), RED)
        image.info.update(icc_profile=profile, exif=exif.tobytes())
        return image

    monkeypatch.setattr(display, "decode_photo", decoded)
    out = jpeg(display_jpeg(b"heic"))
    assert out.info.get("icc_profile") == profile
    assert "exif" not in out.info


def test_an_unreadable_heic_has_no_browser_copy() -> None:
    with pytest.raises(ImageRejected):
        display_jpeg(heic_fixture("photo.heic")[:200])


def test_conversions_run_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    inside = 0
    most = 0
    count = threading.Lock()
    start = threading.Barrier(4)

    def decoded(data: bytes) -> Image.Image:
        nonlocal inside, most
        with count:
            inside += 1
            most = max(most, inside)
        time.sleep(0.05)
        with count:
            inside -= 1
        return Image.new("RGB", (80, 60), RED)

    def convert() -> None:
        start.wait()
        display_jpeg(b"heic")

    monkeypatch.setattr(display, "decode_photo", decoded)
    threads = [threading.Thread(target=convert) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most == 1


def test_a_failed_conversion_releases_the_slot() -> None:
    with pytest.raises(ImageRejected):
        display_jpeg(b"not a photo")
    assert jpeg(display_jpeg(heic_fixture("photo.heic"))).size == (320, 240)


# --------------------------------------------------------------------------- other readers


def test_the_surface_crops_open_a_heic() -> None:
    image = _open_rgb(heic_fixture("rotated_90cw.heic"))
    assert image.mode == "RGB" and image.size == (240, 320) and red_corner(image) == "tr"


def test_the_report_exhibit_draws_on_a_heic() -> None:
    out = jpeg(compose(heic_fixture("rotated_90cw.heic"), []))
    assert out.size == (240, 320) and red_corner(out) == "tr"
