"""basis.vision.camera: a photo's focal length, 35 mm equivalent and camera model from its EXIF,
and nothing else (EVAL_REPORT 111). A photo carrying the location, dates, the owner's name and
serial numbers gives back exactly the three fields; missing, zero, absurd or malformed tags
are left out; a file that is not an image gives nothing; a HEIC opens through the same plugin."""

from __future__ import annotations

import dataclasses
import io
import math

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from basis.vision import camera as cam

GPS_IFD = 0x8825


def jpeg(**tags: object) -> bytes:
    """A small JPEG with the IFD0 (``make``, ``model``, ``software``, ``artist``), Exif
    (``focal``, ``focal35``, ``taken``, ``serial``, ``lens_serial``, ``owner``) and GPS tags."""
    image = Image.new("RGB", (64, 48), (90, 120, 60))
    exif = Image.Exif()
    for key, tag in (("make", 0x010F), ("model", 0x0110), ("software", 0x0131), ("artist", 0x013B)):
        if key in tags:
            exif[tag] = tags[key]
    sub = exif.get_ifd(cam.EXIF_IFD)
    for key, tag in (
        ("focal", cam.TAG_FOCAL_LENGTH),
        ("focal35", cam.TAG_FOCAL_35MM),
        ("taken", 0x9003),
        ("serial", 0xA431),
        ("lens_serial", 0xA435),
        ("owner", 0xA430),
    ):
        if key in tags:
            sub[tag] = tags[key]
    if tags.get("gps"):
        gps = exif.get_ifd(GPS_IFD)
        gps[1], gps[2] = "N", (IFDRational(38), IFDRational(52), IFDRational(12))
        gps[3], gps[4] = "W", (IFDRational(77), IFDRational(25), IFDRational(48))
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


FULL = {
    "make": "samsung",
    "model": "SM-J727U",
    "software": "J727UVLS8CUF1",
    "artist": "A. Owner",
    "focal": IFDRational(37, 10),
    "focal35": 28,
    "taken": "2022:01:01 17:02:03",
    "serial": "R58M123456",
    "lens_serial": "L-998877",
    "owner": "A. Owner",
    "gps": True,
}


def test_exactly_the_three_fields_and_never_the_location() -> None:
    data = jpeg(**FULL)
    # The file does carry the location, dates, names and serials ...
    with Image.open(io.BytesIO(data)) as im:
        assert im.getexif().get_ifd(GPS_IFD)
    got = cam.read_camera(data)
    # ... and the result holds the three fields alone.
    assert got == cam.Camera(3.7, 28.0, "samsung SM-J727U")
    assert [f.name for f in dataclasses.fields(cam.Camera)] == [
        "focal_length_mm",
        "focal_length_35mm",
        "camera_model",
    ]
    text = repr(dataclasses.asdict(got))
    for private in ("38", "77", "Owner", "R58M", "L-998877", "2022", "J727UVLS"):
        assert private not in text.replace("SM-J727U", "")


@pytest.mark.parametrize(
    ("make", "model", "expected"),
    [
        ("Apple", "iPhone 15 Pro", "Apple iPhone 15 Pro"),
        ("Canon", "Canon EOS R6", "Canon EOS R6"),  # the model starts with the make
        ("OLYMPUS IMAGING CORP.", "SP510UZ", "OLYMPUS IMAGING CORP. SP510UZ"),
        (None, "Pixel 8", "Pixel 8"),
        ("NIKON", None, "NIKON"),
        ("  Sony\x00\n", " ILCE-7M3\x07 ", "Sony ILCE-7M3"),
        ("X" * 80, "Y" * 80, ("X" * 80 + " " + "Y" * 80)[: cam.MAX_MODEL_CHARS]),
    ],
)
def test_the_camera_model_is_make_and_model_cleaned(
    make: str | None, model: str | None, expected: str
) -> None:
    tags = {k: v for k, v in (("make", make), ("model", model)) if v is not None}
    assert cam.read_camera(jpeg(**tags)).camera_model == expected


@pytest.mark.parametrize(
    ("focal", "focal35", "expected"),
    [
        (IFDRational(0, 1), 0, (None, None)),  # 0: "unknown" in the standard
        (IFDRational(5, 0), 26, (None, 26.0)),  # a zero denominator
        (IFDRational(4000, 1), 6000, (None, None)),  # beyond any lens
        (IFDRational(6765, 1000), 24, (6.765, 24.0)),
        (IFDRational(50, 1), None, (50.0, None)),
    ],
)
def test_focal_lengths_out_of_range_or_unknown_are_left_out(
    focal: IFDRational, focal35: int | None, expected: tuple[float | None, float | None]
) -> None:
    tags: dict[str, object] = {"focal": focal}
    if focal35 is not None:
        tags["focal35"] = focal35
    got = cam.read_camera(jpeg(**tags))
    assert (got.focal_length_mm, got.focal_length_35mm) == expected


def test_no_exif_and_not_an_image_give_nothing() -> None:
    plain = io.BytesIO()
    Image.new("RGB", (32, 32)).save(plain, format="PNG")
    assert cam.read_camera(plain.getvalue()).empty
    assert cam.read_camera(b"").empty
    assert cam.read_camera(b"%PDF-1.7 not a photo").empty
    assert cam.read_camera(jpeg(**FULL)[:300]).empty  # cut short inside the EXIF block


def test_a_heic_opens_through_the_same_plugin_and_gives_no_fields_without_exif() -> None:
    # Apple's own encodings with no EXIF block: a HEIC opens and yields no invented fields.
    from fixtures import heic_fixture

    for name in ("photo.heic", "rotated_90cw.heic", "alpha.heic"):
        assert cam.read_camera(heic_fixture(name)).empty


def test_a_heic_with_an_iphones_exif_gives_the_lens_and_never_the_location() -> None:
    # lens.heic: Apple's encoder (sips) wrote the EXIF into the HEIF container's Exif item,
    # re-written big-endian, with a GPS block at 0 N 0 E (tooling/fixtures/make_heic_fixtures.py;
    # EVAL_REPORT 111.4). Pillow reaches it through the HEIF plugin the same way as a JPEG's.
    from fixtures import heic_fixture

    data = heic_fixture("lens.heic")
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "HEIF"
        assert image.getexif().get_ifd(0x8825)  # the location is there to be refused
    got = cam.read_camera(data)
    assert got == cam.Camera(6.765, 24.0, "Apple iPhone 15 Pro")
    assert [f.name for f in dataclasses.fields(got)] == [
        "focal_length_mm",
        "focal_length_35mm",
        "camera_model",
    ]
    assert got.focal_length_35mm is not None
    assert math.isclose(cam.hfov_deg(got.focal_length_35mm, 320, 240), 71.6, abs_tol=0.1)


def test_the_field_of_view_is_on_the_diagonal() -> None:
    assert cam.FRAME_35MM_DIAGONAL_MM == pytest.approx(43.2666, abs=1e-4)
    # A 36 x 24 frame's own width: 2 atan(18 / f).
    assert cam.hfov_deg(50.0, 36, 24) == pytest.approx(math.degrees(2 * math.atan(18 / 50)))
    # The same lens on a portrait photo sees the narrower side across.
    assert cam.hfov_deg(28.0, 3024, 4032) < cam.hfov_deg(28.0, 4032, 3024)
    for bad in ((0, 4, 3), (28, 0, 3), (28, 4, -1)):
        with pytest.raises(ValueError):
            cam.hfov_deg(*bad)
