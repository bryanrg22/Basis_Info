"""crop_check.py: crop the photo to every mask and label the page draws, to look at each one.

    python3 web/site/landing/tools/crop_check.py --data web/site/landing/src/data/kitchen.json \
        --out .ui-climb/r3/crops/kitchen [--only k75,k76] [--all]

For each instance (the ones the page shows, ``"shown": true``, unless ``--all``): the photo
cropped to the instance's box with 12 % padding, the mask's outline drawn on it, and the area
outside the mask dimmed, so a crop that shows anything other than the named object is
visible at a glance. Writes one PNG per instance, ``overlay.png`` (the whole photo with every
outline), and ``sheet.png`` (all crops on one page, captioned with id, label, score and
whether it is counted). Needs only Pillow and numpy.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
DIRS = ROOT / "web" / "site" / "landing"
COLOURS = {"counted": (64, 210, 140), "candidate": (240, 190, 60), "manual": (120, 180, 255)}


def polygons(path: str, w: int, h: int) -> list[list[tuple[float, float]]]:
    out = []
    for part in re.findall(r"M([^Z]+)Z", path or ""):
        nums = [float(v) for v in part.split()]
        out.append([(nums[i] * w, nums[i + 1] * h) for i in range(0, len(nums), 2)])
    return out


def font(size: int) -> ImageFont.ImageFont:
    for f in ("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--only", default="")
    p.add_argument("--all", action="store_true")
    p.add_argument("--cell", type=int, default=300)
    p.add_argument("--labels", default="", help="label rects measured on the page (probe-b3.mjs kitchen-labels.json)")
    p.add_argument("--rows", default="", help="row of each instance id, 'id:row,...', to crop each label with its group")
    p.add_argument("--title", default="")
    a = p.parse_args()
    data = json.loads(Path(a.data).read_text())
    photo = Image.open((DIRS / data["photo"]["src"]).resolve()).convert("RGB")
    W, H = photo.size
    only = {s for s in a.only.split(",") if s}
    items = [i for i in data["instances"] if (a.all or i.get("shown")) and (not only or i["id"] in only)]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f_small, f_big = font(13), font(15)

    overlay = photo.copy()
    d = ImageDraw.Draw(overlay)
    cells = []
    for inst in items:
        kind = "manual" if inst.get("source") == "manual" else "counted" if inst["counted"] else "candidate"
        col = COLOURS[kind]
        polys = polygons(inst["path"], W, H)
        for poly in polys:
            d.line(poly + [poly[0]], fill=col, width=2)
        x1, y1, x2, y2 = inst["box"]
        bw, bh = (x2 - x1) * W, (y2 - y1) * H
        pad = 0.12 * max(bw, bh) + 6
        box = (max(0, int(x1 * W - pad)), max(0, int(y1 * H - pad)), min(W, int(x2 * W + pad)), min(H, int(y2 * H + pad)))
        # dim outside the mask
        m = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(m)
        for poly in polys:
            md.polygon(poly, fill=255)
        dim = Image.blend(photo, Image.new("RGB", (W, H), (0, 0, 0)), 0.62)
        lit = Image.composite(photo, dim, m)
        ld = ImageDraw.Draw(lit)
        for poly in polys:
            ld.line(poly + [poly[0]], fill=col, width=2)
        crop = lit.crop(box)
        crop.thumbnail((a.cell, a.cell), Image.Resampling.LANCZOS)
        crop.save(out / f"{inst['id']}.png")
        cells.append((inst, crop, kind))
        # page-side label text, if the page shows one
    overlay.save(out / "overlay.png")

    cols = 5
    cw, ch = a.cell, a.cell + 44
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw + (cols + 1) * 10, max(1, rows) * ch + (rows + 1) * 10 + 40), (245, 243, 236))
    sd = ImageDraw.Draw(sheet)
    sd.text((10, 12), a.title or f"{data['photo']['name']}: {len(cells)} masks the page draws, each cropped to its box (outside the mask dimmed)", fill=(19, 38, 28), font=f_big)
    for n, (inst, crop, kind) in enumerate(cells):
        r, c = divmod(n, cols)
        x, y = 10 + c * (cw + 10), 50 + r * (ch + 10)
        sheet.paste(crop, (x + (cw - crop.width) // 2, y + (cw - crop.height) // 2))
        label = (inst.get("display") or inst["label"]).replace("_", " ")
        sd.text((x, y + cw + 4), f"{inst['id']} {label}"[: max(8, cw // 8)], fill=(19, 38, 28), font=f_small)
        sd.text((x, y + cw + 22), f"{inst['score']:.2f} {'drawn' if kind == 'manual' else kind}"[: max(8, cw // 8)], fill=(83, 102, 90), font=f_small)
    sheet.save(out / "sheet.png")
    print(f"{len(cells)} crops -> {out}/sheet.png, overlay.png")
    if a.labels:
        label_sheet(a, data, photo, out, f_small, f_big)


def label_sheet(a, data, photo, out, f_small, f_big) -> None:
    """Each label the page shows, drawn where the page put it (magenta), with the outlines of the
    group it names (green), cropped to both: a label must sit beside or on its own group and on
    nothing else."""
    W, H = photo.size
    labels = json.loads(Path(a.labels).read_text())["labels"]
    row_of = dict(kv.split(":") for kv in a.rows.split(",") if kv)
    by_id = {i["id"]: i for i in data["instances"]}
    cells = []
    for lab in labels:
        members = [by_id[i] for i, r in row_of.items() if r == lab["row"] and i in by_id]
        xs = [lab["x1"], lab["x2"]] + [v for m in members for v in (m["box"][0], m["box"][2])]
        ys = [lab["y1"], lab["y2"]] + [v for m in members for v in (m["box"][1], m["box"][3])]
        # keep the crop near the label: at most 0.3 of the photo around it
        cx, cy = (lab["x1"] + lab["x2"]) / 2, (lab["y1"] + lab["y2"]) / 2
        x1, x2 = max(min(xs), cx - .2), min(max(xs), cx + .2)
        y1, y2 = max(min(ys), cy - .16), min(max(ys), cy + .16)
        pad = .02
        box = (int(max(0, x1 - pad) * W), int(max(0, y1 - pad) * H), int(min(1, x2 + pad) * W), int(min(1, y2 + pad) * H))
        img = photo.copy()
        d = ImageDraw.Draw(img)
        for m in members:
            for poly in polygons(m["path"], W, H):
                d.line(poly + [poly[0]], fill=COLOURS["counted"], width=2)
        d.rectangle((lab["x1"] * W, lab["y1"] * H, lab["x2"] * W, lab["y2"] * H), outline=(255, 0, 200), width=3)
        crop = img.crop(box)
        crop.thumbnail((a.cell * 2, a.cell), Image.Resampling.LANCZOS)
        cells.append((lab, crop))
    cols, cw, ch = 3, a.cell * 2, a.cell + 26
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cw + 10) + 10, rows * (ch + 10) + 50), (245, 243, 236))
    sd = ImageDraw.Draw(sheet)
    sd.text((10, 12), f"{data['photo']['name']}: {len(cells)} labels as the page places them (magenta) with the outlines they name (green)", fill=(19, 38, 28), font=f_big)
    for n, (lab, crop) in enumerate(cells):
        r, c = divmod(n, cols)
        x, y = 10 + c * (cw + 10), 46 + r * (ch + 10)
        sheet.paste(crop, (x, y))
        sd.text((x, y + crop.height + 4), f"\"{lab['text']}\" names {lab['row']}", fill=(19, 38, 28), font=f_small)
    sheet.save(out / "labels.png")
    print(f"{len(cells)} labels -> {out}/labels.png")


if __name__ == "__main__":
    main()
