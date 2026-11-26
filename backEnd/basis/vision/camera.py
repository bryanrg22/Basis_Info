"""The camera fields a photo's EXIF gives the facade measurement, and nothing else
(EVAL_REPORT 109 and 111; docs/api/OVERRIDES.md section 4.1).

Measuring a wall on a photo (:mod:`basis.geo.facade`) needs the photo's field of view: with it
the wall's height follows from its known width at 1.9 % (EVAL_REPORT 109.3); without it only
the wall's own vanishing points can give it (3.8 %, unstable on oblique photos), and a photo
whose verticals are parallel cannot be measured at all (``E.GEO_FACADE_NO_FOCAL``). The model
copy of a photo drops every metadata block (:mod:`basis.vision.prep`), so the field of view is
read here, once, when the upload is confirmed, and kept as three fields of the document:

* ``focal_length_mm``: EXIF ``FocalLength`` (tag 0x920A, the Exif IFD), the lens's real focal
  length in millimetres;
* ``focal_length_35mm``: EXIF ``FocalLengthIn35mmFilm`` (tag 0xA405), the focal length a 36 x 24
  mm frame would need for the same angle of view. CIPA DC-008 (Exif 2.3, section 4.6.5) defines
  it on the frame's diagonal, so the horizontal field of view of a ``width`` x ``height`` photo
  is ``2 atan(width / hypot(width, height) x 43.27 / 2 / f35)`` (:func:`hfov_deg`; 43.27 mm is
  the 36 x 24 frame's diagonal). A value of 0 means "unknown" in the standard and is dropped;
* ``camera_model``: ``Make`` and ``Model`` (tags 0x010F and 0x0110 of IFD0), the make dropped
  when the model already starts with it, for the reviewer (which camera took it).

Never read or kept: the GPS IFD (0x8825: where the photo was taken, a client's location), the
dates, the owner's name, the body and lens serial numbers, the software, the maker notes. The
function returns a value with exactly the three fields, and the document stores exactly those
(a test refuses any other).

A tag that is missing, of the wrong type, not finite, or outside a plausible range
(``(0, MAX_FOCAL_MM]`` and ``(0, MAX_FOCAL_35MM]``) is left out, never guessed. Text is
cleaned of control characters and capped at :data:`MAX_MODEL_CHARS`. Reading the EXIF parses
the header only (no pixel is decoded); a photo that is not an image Pillow can open gives no
fields (its upload is confirmed as before: a photo without EXIF is not a bad photo).
"""

from __future__ import annotations

import io
import math
import re
import struct
from dataclasses import dataclass
from typing import Any, Final

from PIL import Image, UnidentifiedImageError

from basis.vision import heif

heif.register()

TAG_MAKE: Final = 0x010F
TAG_MODEL: Final = 0x0110
EXIF_IFD: Final = 0x8769
TAG_FOCAL_LENGTH: Final = 0x920A
TAG_FOCAL_35MM: Final = 0xA405
# Plausible bounds: the longest lens a phone or a field camera carries, and its 35 mm figure.
MAX_FOCAL_MM: Final = 2000.0
MAX_FOCAL_35MM: Final = 5000.0
MAX_MODEL_CHARS: Final = 100
FRAME_35MM_DIAGONAL_MM: Final = math.hypot(36.0, 24.0)
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True, slots=True)
class Camera:
    """What the facade measurement may know about the camera. Exactly these three fields."""

    focal_length_mm: float | None = None
    focal_length_35mm: float | None = None
    camera_model: str | None = None

    @property
    def empty(self) -> bool:
        return (
            self.focal_length_mm is None
            and self.focal_length_35mm is None
            and self.camera_model is None
        )


def _number(value: Any, limit: float) -> float | None:
    """A positive, finite EXIF rational or integer up to ``limit``; None otherwise."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:  # an unreduced (numerator, denominator)
        num, den = value
        if not isinstance(num, int | float) or not isinstance(den, int | float) or den == 0:
            return None
        value = num / den
    try:
        number = float(value)  # an int, a float, or Pillow's IFDRational
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if not math.isfinite(number) or not 0 < number <= limit:
        return None
    return round(number, 3)


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return None
    cleaned = " ".join(_CONTROL.sub(" ", value).split())
    return cleaned or None


def _model(make: Any, model: Any) -> str | None:
    make_text, model_text = _text(make), _text(model)
    if model_text is None:
        return None if make_text is None else make_text[:MAX_MODEL_CHARS]
    if make_text and not model_text.lower().startswith(make_text.lower()):
        model_text = f"{make_text} {model_text}"
    return model_text[:MAX_MODEL_CHARS]


def read_camera(data: bytes) -> Camera:
    """The three camera fields of a photo's EXIF (module doc); empty when there are none or
    the bytes are not an image Pillow can open. CPU and parsing: call it on a thread."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            exif = image.getexif()
            ifd0 = {k: exif.get(k) for k in (TAG_MAKE, TAG_MODEL)}
            sub = exif.get_ifd(EXIF_IFD)
            focal = sub.get(TAG_FOCAL_LENGTH)
            focal35 = sub.get(TAG_FOCAL_35MM)
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        TypeError,
        KeyError,
        struct.error,
    ):
        return Camera()
    return Camera(
        focal_length_mm=_number(focal, MAX_FOCAL_MM),
        focal_length_35mm=_number(focal35, MAX_FOCAL_35MM),
        camera_model=_model(ifd0[TAG_MAKE], ifd0[TAG_MODEL]),
    )


def hfov_deg(focal_length_35mm: float, width: float, height: float) -> float:
    """The horizontal field of view of a ``width`` x ``height`` photo from its 35 mm
    equivalent focal length (CIPA DC-008: defined on the diagonal)."""
    if not focal_length_35mm > 0 or not width > 0 or not height > 0:
        raise ValueError("the focal length and the photo's size must be positive")
    half = width / math.hypot(width, height) * FRAME_35MM_DIAGONAL_MM / 2
    return math.degrees(2 * math.atan(half / focal_length_35mm))


__all__ = [
    "FRAME_35MM_DIAGONAL_MM",
    "MAX_FOCAL_35MM",
    "MAX_FOCAL_MM",
    "MAX_MODEL_CHARS",
    "Camera",
    "hfov_deg",
    "read_camera",
]
