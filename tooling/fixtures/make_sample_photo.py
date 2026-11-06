"""Write ``web/assets/sample-photo.webp``, the sample API's stand-in for a missing photo.

The sample API (``web/live/sample-api.js``) serves five room photos from ``web/assets/``
that are not in git (listing-style images of unknown license, ``web/README.md``). In a
checkout without them it serves this picture instead, so sample mode and the web tests work
from a clean clone. Nothing is taken from a photo: a plain ground with a lighter frame and
two blocks, 1200 x 800 (the photos' 3:2).

From the repository root: ``python3 tooling/fixtures/make_sample_photo.py``. Pillow's WebP
encoder is deterministic for a given libwebp; after regenerating, re-pin the file in
``tooling/client_data_exceptions.txt``.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[2] / "web" / "assets" / "sample-photo.webp"


def main() -> None:
    picture = Image.new("RGB", (1200, 800), (226, 230, 226))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((60, 60, 1140, 740), outline=(200, 207, 201), width=6)
    draw.rectangle((180, 420, 560, 660), fill=(207, 214, 208))
    draw.rectangle((640, 200, 1020, 660), fill=(214, 220, 215))
    picture.save(OUT, "WEBP", quality=60, method=6)
    print(f"{OUT}: {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
