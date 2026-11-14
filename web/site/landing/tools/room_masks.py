"""Pack the masks the rooms figure draws when a room card is clicked.

Inputs: what Basis's localizer found on each room's lead photo, src/data/<photo>.json, written by
tools/localize_photos.py (SAM 3, the product's phrases and count thresholds). Only counted
instances are drawn, and only on the lead photo. Kitchen: the instances the kitchen figure shows
after the engineer's review (b/kitchen.js GROUPS: the stair edge marked Not counted is left out,
the four drawn bar stools are in). Exterior: tools/make_split.py's EXCLUDE (masks that failed the
crop check) left out.

Output: src/data/room-masks.json, {room: {photo, masks: [[label, path], ...]}}, paths in image
fractions rounded to 3 places (a 1/1000 step is under half a pixel on a 340-pixel tile).

Rerun after any localize_photos.py run:  python3 web/site/landing/tools/room_masks.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DIRS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_split import EXCLUDE  # noqa: E402

KITCHEN_LABELS = {"light_fixture", "countertop", "stove", "electrical_outlet", "interior_door"}
KITCHEN_NOT_COUNTED = {"k22"}
ROOMS = {"kitchen": "kitchen", "living": "living", "bedroom": "bedroom", "exterior": "exterior"}


def keep(room: str, inst: dict) -> bool:
    if room == "kitchen":
        if inst["source"] == "manual":
            return True
        return inst["counted"] and inst["label"] in KITCHEN_LABELS and inst["id"] not in KITCHEN_NOT_COUNTED
    if room == "exterior" and inst["id"] in EXCLUDE:
        return False
    return inst["counted"] and inst["source"] == "model"


def short(path: str) -> str:
    return re.sub(r"\d+\.\d+", lambda m: f"{float(m.group()):.3f}".rstrip("0").rstrip(".") or "0", path)


def main() -> None:
    out = {}
    for room, photo in ROOMS.items():
        data = json.loads((DIRS / "b" / "data" / f"{photo}.json").read_text())
        masks = [[i["label"], short(i["path"])] for i in data["instances"] if keep(room, i)]
        masks.sort(key=lambda m: m[0])
        out[room] = {"photo": data["photo"]["name"], "masks": masks}
        print(f"{room}: {len(masks)} masks")
    dest = DIRS / "b" / "data" / "room-masks.json"
    dest.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    print(f"wrote {dest.relative_to(DIRS)} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
