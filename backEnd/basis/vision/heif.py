"""HEIC and HEIF decoding for Pillow: the format an iPhone saves photos in (EVAL_REPORT 81).

Pillow cannot read HEIC by itself. ``pi-heif`` is the decode-only build of ``pillow-heif``
(libheif with the libde265 HEVC decoder; it leaves out the GPL x265 encoder that the full
build bundles), and :func:`register` adds it to Pillow, so ``Image.open`` reads a HEIC like a
JPEG. Every module that opens a photo as uploaded calls it at import: the preparation for
the model (:mod:`basis.vision.prep`), the surface crops (:mod:`basis.vision.surface_crops`)
and the report's photo exhibit (:mod:`basis.report.exhibit`).

Orientation: a HEIC stores its rotation in the container (``irot``/``imir``), and an iPhone
also writes the EXIF orientation. The plugin applies the container's transforms while it
decodes and reports EXIF orientation 1, so ``ImageOps.exif_transpose`` does not rotate the
picture a second time. Measured on 43 iPhone photos (26 with EXIF orientation 6) against
Apple's own decoder: same size and orientation, mean pixel difference at most
0.11 of 255.

A file cut short is refused (:func:`cut_short`). Pillow refuses a JPEG that ends early, but
libheif decodes an iPhone photo (a grid of tiles) with the missing tiles left blank and
no error: an iPhone photo cut to 70 % of its bytes decoded "successfully" with
every row wrong. The check reads the file's top-level boxes (ISO/IEC 14496-12 section 4.2:
a 32-bit size, 1 for a 64-bit size that follows the type, 0 for "to the end of the file")
and refuses a file shorter than they declare. Damage inside the coded picture is not
detectable this way, as for a JPEG.

Embedded thumbnails, depth maps and auxiliary images (an iPhone's HDR gain map) are turned
off: nothing here uses them, and with thumbnails off Pillow's ``draft`` never swaps the
small embedded preview in for the photo.
"""

from __future__ import annotations

import struct
import threading

import pi_heif

_lock = threading.Lock()
_registered = False


def register() -> None:
    """Teach Pillow to open HEIC and HEIF; safe to call any number of times."""
    global _registered
    with _lock:
        if not _registered:
            pi_heif.register_heif_opener(thumbnails=False, depth_images=False, aux_images=False)
            _registered = True


def cut_short(data: bytes) -> bool:
    """True when the HEIF ``data`` ends before its last top-level box does (module doc)."""
    at, end = 0, len(data)
    while at < end:
        if end - at < 8:
            return True
        size = struct.unpack_from(">I", data, at)[0]
        header = 8
        if size == 1:
            if end - at < 16:
                return True
            size = struct.unpack_from(">Q", data, at + 8)[0]
            header = 16
        elif size == 0:
            return False  # the last box runs to the end of the file
        if size < header:
            return True  # not a box: the length cannot be trusted
        at += size
    return at > end


__all__ = ["cut_short", "register"]
