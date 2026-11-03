"""Write the synthetic HEIC fixtures in ``backEnd/tests/fixtures/heic/`` (EVAL_REPORT 81).

macOS only: no Python package encodes HEIC without the GPL x265 encoder, so Apple's
``sips`` (ImageIO, the encoder an iPhone's photos come from) encodes pictures drawn here.
Nothing is taken from a photo: each picture is coloured rectangles on a plain ground.

* ``photo.heic``: 320 x 240, red block top left, green block bottom right.
* ``rotated_90cw.heic``: the same bytes with the container's rotation (``irot``) set to
  270 degrees anticlockwise, which a viewer shows turned 90 degrees clockwise: 240 x 320,
  the red block top right. ``sips`` cannot write a rotation, so its angle byte is set here
  (ISO/IEC 23008-12, ``irot``: the low two bits count quarter turns anticlockwise); Apple's
  decoder reads the result as described (checked when the fixture was made).
* ``alpha.heic``: 200 x 150, a yellow ellipse on a transparent ground.

From ``backEnd/``: ``uv run python ../tooling/fixtures/make_heic_fixtures.py``. The bytes
depend on the macOS version, so the tests check what the files decode to, not their hashes;
after regenerating, re-pin the three files in ``tooling/client_data_exceptions.txt``.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[2] / "backEnd" / "tests" / "fixtures" / "heic"
ANTICLOCKWISE_270 = 3


def encode(picture: Image.Image, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "picture.png"
        picture.save(source)
        subprocess.run(
            ["sips", "-s", "format", "heic", str(source), "--out", str(target)],
            check=True,
            capture_output=True,
        )


def with_rotation(data: bytes, quarter_turns: int) -> bytes:
    """``data`` with its ``irot`` box set to ``quarter_turns`` anticlockwise."""
    at = data.find(b"irot")
    if at < 4 or struct.unpack(">I", data[at - 4 : at])[0] != 9:
        raise SystemExit("no 9-byte irot box in the encoded photo")
    out = bytearray(data)
    out[at + 4] = (out[at + 4] & ~3) | quarter_turns
    return bytes(out)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("make_heic_fixtures.py needs macOS (sips)")
    OUT.mkdir(parents=True, exist_ok=True)
    photo = Image.new("RGB", (320, 240), (40, 90, 160))
    draw = ImageDraw.Draw(photo)
    draw.rectangle((0, 0, 79, 59), fill=(230, 30, 30))
    draw.rectangle((240, 180, 319, 239), fill=(30, 200, 60))
    encode(photo, OUT / "photo.heic")
    rotated = with_rotation((OUT / "photo.heic").read_bytes(), ANTICLOCKWISE_270)
    (OUT / "rotated_90cw.heic").write_bytes(rotated)
    alpha = Image.new("RGBA", (200, 150), (0, 0, 0, 0))
    ImageDraw.Draw(alpha).ellipse((20, 20, 180, 130), fill=(250, 200, 0, 255))
    encode(alpha, OUT / "alpha.heic")
    for path in sorted(OUT.glob("*.heic")):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
