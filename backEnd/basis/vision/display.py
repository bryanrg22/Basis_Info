"""A photo as a browser can show it (``GET .../documents/{id}/image``, EVAL_REPORT 81).

JPEG, PNG and WebP are shown by every browser, so they are served as uploaded. HEIC and
HEIF are shown by Safari only (Chrome, Edge and Firefox do not decode them), so they are
converted here to a JPEG:

* decoded as for the model (:func:`basis.vision.prep.decode_photo`: the same byte and pixel
  limits, HEIC through :mod:`basis.vision.heif`, the container's rotation applied);
* the longest side at most :data:`DISPLAY_MAX_SIDE` = 4096 px: an iPhone's default
  12-megapixel photo (4032 x 3024) keeps every pixel, and a 24-megapixel one (5712 x 4284)
  becomes a JPEG of 1.6 to 2.0 MB instead of 2.8 to 3.6 MB at full size, about the size of
  the HEIC itself;
* quality :data:`DISPLAY_JPEG_QUALITY` = 90, the colour profile kept (an iPhone photo is in
  Display P3, and dropping the profile would show it washed out), every other metadata
  block dropped (EXIF with the GPS position, XMP): the original, with its metadata, stays
  available from ``.../content``.

The rule is versioned (:data:`DISPLAY_RULE`) and its name is part of the response's ETag,
so a change to the rule never leaves a browser on a stale cached copy.

Decoding a 24-megapixel HEIC takes about 0.2 s and 370 MB at its peak (the same photo as a
JPEG: 238 MB), so conversions run one at a time per process (:data:`_one_at_a_time`), on a
thread, never on the event loop: the API's container has 1 GiB, and a review page asks for
six photos at once.
"""

from __future__ import annotations

import io
import threading
from typing import Final

from PIL import Image, ImageOps

from basis.vision.prep import _to_rgb, decode_photo

DISPLAY_MAX_SIDE: Final = 4096
DISPLAY_JPEG_QUALITY: Final = 90
DISPLAY_RULE: Final = "display-v1"
# The photo types a browser shows as they are, and the ones converted (ImageType in
# basis.api.schemas.documents lists what an upload may declare).
SHOWN_AS_UPLOADED: Final = frozenset({"image/jpeg", "image/png", "image/webp"})
CONVERTED: Final = frozenset({"image/heic", "image/heif"})

_one_at_a_time = threading.BoundedSemaphore(1)


def display_jpeg(data: bytes) -> bytes:
    """The HEIC or HEIF ``data`` as a JPEG a browser shows (module doc); raises
    :class:`~basis.vision.prep.ImageRejected` when the bytes cannot be read. CPU work: call
    it on a thread."""
    with _one_at_a_time:
        image = decode_photo(data)
        profile = image.info.get("icc_profile")
        oriented = ImageOps.exif_transpose(image) or image
        rgb = _to_rgb(oriented)
        rgb.thumbnail((DISPLAY_MAX_SIDE, DISPLAY_MAX_SIDE), Image.Resampling.LANCZOS)
        clean = Image.new("RGB", rgb.size)
        clean.paste(rgb)  # a fresh image carries no info dict (EXIF, XMP, comments)
        out = io.BytesIO()
        options: dict[str, object] = {"quality": DISPLAY_JPEG_QUALITY}
        if profile:
            options["icc_profile"] = profile
        clean.save(out, format="JPEG", **options)
        return out.getvalue()


__all__ = [
    "CONVERTED",
    "DISPLAY_JPEG_QUALITY",
    "DISPLAY_MAX_SIDE",
    "DISPLAY_RULE",
    "SHOWN_AS_UPLOADED",
    "display_jpeg",
]
