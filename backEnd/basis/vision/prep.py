"""Image preparation before any model call (S3_SPEC section 4).

* Reads JPEG, PNG, WebP and the other formats Pillow knows, and HEIC/HEIF, an iPhone's
  format (:mod:`basis.vision.heif`, EVAL_REPORT 81).
* Rejects, with an explicit :class:`ImageRejected` (the activity makes it non-retryable),
  input that cannot be analyzed: empty, over :data:`MAX_IMAGE_BYTES`, not a decodable
  image, more than :data:`MAX_PIXELS` pixels (a decompression bomb guard that is checked
  from the header, before decoding), or smaller than :data:`MIN_SIDE` on either side
  (``malformed/tiny.jpg``: an 8 x 8 image is never upscaled into a confident answer).
* Applies the EXIF orientation, then drops every metadata block (EXIF including GPS, ICC
  profile, comments): the photo's location and camera never reach a provider.
* Converts to RGB (alpha is composited on white) and downsizes so the longest side is at
  most :data:`MAX_SIDE` = 1024 px (``malformed/oversize.jpg`` 6000 x 4000 becomes
  1024 x 683); smaller images keep their size.
* Re-encodes as baseline JPEG, quality :data:`JPEG_QUALITY`. The output is deterministic
  for the same input and Pillow version.

The 1024 px limit keeps a "high detail" image at four 512 px tiles (765 input tokens on
the GPT-4o family), which is what ``basis.llm.cost.IMAGE_TOKENS`` budgets for.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from dataclasses import dataclass
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

from basis.vision import heif

heif.register()

MAX_SIDE: Final = 1024
MIN_SIDE: Final = 64
MAX_IMAGE_BYTES: Final = 25 * 1024 * 1024
MAX_PIXELS: Final = 80_000_000
JPEG_QUALITY: Final = 85


class ImageRejected(ValueError):
    """The bytes cannot be analyzed; ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PreparedImage:
    jpeg: bytes
    width: int
    height: int
    original_width: int
    original_height: int
    original_format: str | None

    @property
    def base64(self) -> str:
        return base64.b64encode(self.jpeg).decode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.jpeg).hexdigest()

    @property
    def resized(self) -> bool:
        return (self.width, self.height) != (self.original_width, self.original_height)


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def decode_photo(data: bytes) -> Image.Image:
    """The photo decoded, after the byte and pixel limits; :class:`ImageRejected` when it
    cannot be read. Shared with the browser copy (:mod:`basis.vision.display`)."""
    if not data:
        raise ImageRejected("empty", "the photo file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRejected(
            "too_large_bytes", f"the photo is {len(data)} bytes; the limit is {MAX_IMAGE_BYTES}"
        )
    # No ``warnings.catch_warnings`` here: it swaps the process-wide filter list, which is
    # not thread-safe, and this runs on worker threads (:func:`prepare_image_in_thread`).
    # ``MAX_PIXELS`` sits below Pillow's own warning threshold (``Image.MAX_IMAGE_PIXELS``,
    # 89,478,485), so every image Pillow would warn about is refused by the check below
    # from its header, before any pixel is decoded; Pillow's ``DecompressionBombError``
    # (twice its threshold) is raised from ``open`` and refused as not an image.
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise ImageRejected(
                "too_many_pixels",
                f"the photo is {width} x {height}; the limit is {MAX_PIXELS} pixels",
            )
        if image.format == "HEIF" and heif.cut_short(data):
            raise ImageRejected("not_an_image", "the photo cannot be decoded: it is cut short")
        image.load()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError):
        raise ImageRejected("not_an_image", "the photo is not a readable image") from None
    # libheif reports a damaged HEIC as EOFError ("Unexpected end of file"), not an OSError.
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        raise ImageRejected("not_an_image", f"the photo cannot be decoded: {exc}") from None
    return image


def prepare_image(data: bytes) -> PreparedImage:
    """Validate, orient, strip, downsize and re-encode one photo."""
    image = decode_photo(data)
    width, height = image.size
    original_format = image.format
    if min(width, height) < MIN_SIDE:
        raise ImageRejected(
            "too_small",
            f"the photo is {width} x {height}; at least {MIN_SIDE} px per side is needed",
        )
    oriented = ImageOps.exif_transpose(image) or image
    rgb = _to_rgb(oriented)
    rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", rgb.size)
    clean.paste(rgb)  # a fresh image carries no info dict (EXIF, ICC, comments)
    out = io.BytesIO()
    clean.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=False, progressive=False)
    return PreparedImage(
        jpeg=out.getvalue(),
        width=clean.width,
        height=clean.height,
        original_width=width,
        original_height=height,
        original_format=original_format,
    )


async def prepare_image_in_thread(data: bytes) -> PreparedImage:
    """:func:`prepare_image` on a worker thread, for async code (activities, routes).

    Decoding, orienting, downsizing and re-encoding a 12-megapixel photo is about 0.08 s
    of CPU. On the event loop it holds every other task of the worker, Temporal's
    workflow activations included: the SDK times an activation with a 2 s timer on that
    loop, so photo activities started together (20 photos held it 1.7 s on a fast laptop)
    can make a quick activation read as a deadlock (``TMPRL1101``). Pillow releases the
    GIL while it decodes and resamples, so the threads also run in parallel. A test
    (``tests/unit/test_event_loop_image_work.py``) refuses a direct call inside
    ``async def``.
    """
    return await asyncio.to_thread(prepare_image, data)
