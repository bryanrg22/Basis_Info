"""``python -m evals.openimages``: build a ``detection_boxes`` dataset from Open Images V7
validation (VISION_DETECTION_RESEARCH.md, step 0: measure before labelling our own).

Open Images annotations are CC BY 4.0 and the images are listed as CC BY 2.0
(attribution required: every selected image's author, licence and Flickr page go into
``MANIFEST.json``). Nothing here touches client data.

Inputs, downloaded once into ``--cache`` (default ``<out>/cache``) and reused:

* ``oidv7-class-descriptions-boxable.csv``: ``LabelName`` (a Freebase id) -> display name.
* ``validation-annotations-bbox.csv`` (the V5 path: the V7 boxes are the V5 boxes, and
  ``v7/oidv7-val-annotations-bbox.csv`` is not served, HTTP 403 on 2026-10-15): one row
  per box, ``XMin/XMax/YMin/YMax`` normalized, ``IsGroupOf``.
* ``oidv7-val-annotations-human-imagelabels.csv``: human-verified image labels
  (``Confidence`` 1 present, 0 absent). Open Images is exhaustive only per verified label,
  so an image's ``verified`` list is the labels whose every source class is boxed in it or
  verified absent; a class verified present without boxes is left unverified. The scorer
  ignores predictions of unverified labels, as the Open Images protocol does.
* ``validation-images-with-rotation.csv`` (2018_04 path): author, licence and page per
  image, and ``Rotation``. Images with a rotation are skipped, so a box never has to be
  rotated to match the pixels.

Display names map to our labels by :data:`OI_LABELS` (Light switch has no label and is not
used). ``Door`` maps to the special gold label ``door``, scored against the union of
``interior_door`` and ``exterior_door`` predictions (``evals.metrics.CLASS_UNION``).
Known approximations, to read the per-class numbers with: Open Images ``Lamp`` includes
table and floor lamps (``light_fixture`` is fixed lighting), ``Cabinetry`` is any cabinet
(``kitchen_cabinet``), ``Curtain`` -> ``window_treatment``.

Selection is deterministic and balanced: for each label in order, images containing at
least one box of it are taken in ascending ``ImageID`` order (ids are hashes, so this is a
fixed pseudo-random sample) until ``--per-label`` selected images contain the label
(images already chosen for an earlier label count). Images are downloaded from the public
bucket ``open-images-dataset.s3.amazonaws.com/validation/<ImageID>.jpg`` with a bounded
retry; a failure is skipped and listed. Width and height are read from the file.

Output::

    <out>/detection_boxes_v1.json   the dataset (document = image id, split = dev)
    <out>/images/<ImageID>.jpg      the photos
    <out>/MANIFEST.json             sources, sha256 of every input CSV and image,
                                    attribution, per-label counts, skipped images

Usage (from ``backEnd/``)::

    uv run python -m evals.openimages --out ~/basis-eval-data/openimages --per-label 25
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
from PIL import Image, UnidentifiedImageError

from evals.datasets import DetectionBox, DetectionBoxes, DetectionImageLabel

CLASSES_URL: Final = (
    "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv"
)
BOXES_URL: Final = "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv"
IMAGE_LABELS_URL: Final = (
    "https://storage.googleapis.com/openimages/v7/oidv7-val-annotations-human-imagelabels.csv"
)
IMAGES_META_URL: Final = (
    "https://storage.googleapis.com/openimages/2018_04/validation/"
    "validation-images-with-rotation.csv"
)
IMAGE_URL: Final = "https://open-images-dataset.s3.amazonaws.com/validation/{image_id}.jpg"
DATASET_FILE: Final = "detection_boxes_v1.json"
RETRY_DELAYS_S: tuple[float, ...] = (1.0, 3.0, 9.0)

OI_LABELS: Final[dict[str, str]] = {
    "Window": "window",
    "Door": "door",
    "Power plugs and sockets": "electrical_outlet",
    "Ceiling fan": "ceiling_fan",
    "Lamp": "light_fixture",
    "Toilet": "toilet",
    "Sink": "sink",
    "Bathtub": "bathtub",
    "Shower": "shower",
    "Cabinetry": "kitchen_cabinet",
    "Countertop": "countertop",
    "Fireplace": "fireplace",
    "Stairs": "stairs",
    "Oven": "oven",
    "Gas stove": "stove",
    "Refrigerator": "refrigerator",
    "Dishwasher": "dishwasher",
    "Microwave oven": "microwave",
    "Washing machine": "washer",
    "Curtain": "window_treatment",
    "Window blind": "blinds",
    "Porch": "porch",
    "Swimming pool": "pool",
}


class OpenImagesError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageMeta:
    license: str
    author: str
    page: str
    rotation: float


@dataclass(frozen=True)
class Downloaded:
    path: Path
    width: int
    height: int
    sha256: str


# --------------------------------------------------------------------------- parsing


def _rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def read_classes(path: Path) -> dict[str, str]:
    """``{LabelName: DisplayName}``; the header row is optional (older files have none)."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.reader(handle) if row]
    if rows and rows[0][:2] == ["LabelName", "DisplayName"]:
        rows = rows[1:]
    return {row[0]: row[1] for row in rows if len(row) >= 2}


def class_map(classes: Mapping[str, str], mapping: Mapping[str, str] = OI_LABELS) -> dict[str, str]:
    """``{LabelName: our label}`` for the mapped display names; a mapped name missing from
    the class file is an error (the mapping would silently lose a class)."""
    by_name = {name: mid for mid, name in classes.items()}
    missing = sorted(set(mapping) - set(by_name))
    if missing:
        raise OpenImagesError(f"display names not in the class file: {missing}")
    return {by_name[name]: label for name, label in mapping.items()}


def read_boxes(path: Path, mids: Mapping[str, str]) -> dict[str, list[tuple[str, DetectionBox]]]:
    """``{ImageID: [(LabelName, box)]}`` for the mapped classes."""
    boxes: dict[str, list[tuple[str, DetectionBox]]] = defaultdict(list)
    for row in _rows(path):
        mid = row["LabelName"]
        label = mids.get(mid)
        if label is None:
            continue
        boxes[row["ImageID"]].append(
            (
                mid,
                DetectionBox(
                    label=label,
                    x1=float(row["XMin"]),
                    y1=float(row["YMin"]),
                    x2=float(row["XMax"]),
                    y2=float(row["YMax"]),
                    group_of=row.get("IsGroupOf") == "1",
                ),
            )
        )
    return dict(boxes)


def read_image_labels(path: Path, mids: Mapping[str, str]) -> dict[str, dict[str, bool]]:
    """``{ImageID: {LabelName: verified present}}`` for the mapped classes (human labels)."""
    labels: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in _rows(path):
        if row["LabelName"] in mids:
            labels[row["ImageID"]][row["LabelName"]] = float(row["Confidence"]) > 0.5
    return dict(labels)


def read_image_meta(path: Path, ids: Iterable[str] | None = None) -> dict[str, ImageMeta]:
    wanted = set(ids) if ids is not None else None
    meta: dict[str, ImageMeta] = {}
    for row in _rows(path):
        image_id = row["ImageID"]
        if wanted is not None and image_id not in wanted:
            continue
        rotation = row.get("Rotation") or ""
        meta[image_id] = ImageMeta(
            license=row.get("License", ""),
            author=row.get("Author", ""),
            page=row.get("OriginalLandingURL", ""),
            rotation=float(rotation) if rotation.strip() else 0.0,
        )
    return meta


def verified_labels(
    boxed: Iterable[str], human: Mapping[str, bool], mids: Mapping[str, str]
) -> list[str]:
    """Our labels exhaustively annotated in one image: every Open Images class mapping to
    the label is boxed there or verified absent (a class verified present without a box
    is not exhaustive)."""
    boxed_mids = set(boxed)
    sources: dict[str, list[str]] = defaultdict(list)
    for mid, label in mids.items():
        sources[label].append(mid)
    return sorted(
        label
        for label, members in sources.items()
        if all(m in boxed_mids or human.get(m) is False for m in members)
    )


def select_images(
    boxes: Mapping[str, Sequence[tuple[str, DetectionBox]]],
    per_label: int,
    labels: Sequence[str] | None = None,
    exclude: Iterable[str] = (),
) -> list[str]:
    """Balanced, deterministic: per label (sorted), the lowest image ids containing it
    until ``per_label`` selected images do. Returns the ids in ascending order."""
    if per_label < 1:
        raise ValueError("per_label must be at least 1")
    skip = set(exclude)
    by_label: dict[str, list[str]] = defaultdict(list)
    for image_id in sorted(boxes):
        if image_id in skip:
            continue
        for label in sorted({box.label for _, box in boxes[image_id]}):
            by_label[label].append(image_id)
    chosen: set[str] = set()
    for label in sorted(labels if labels is not None else by_label):
        candidates = by_label.get(label, [])
        have = sum(1 for image_id in candidates if image_id in chosen)
        for image_id in candidates:
            if have >= per_label:
                break
            if image_id not in chosen:
                chosen.add(image_id)
                have += 1
    return sorted(chosen)


# --------------------------------------------------------------------------- downloads


async def _get(client: httpx.AsyncClient, url: str, delays: Sequence[float] | None = None) -> bytes:
    waits = RETRY_DELAYS_S if delays is None else delays
    last = ""
    for attempt in range(len(waits) + 1):
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.content
            last = f"HTTP {response.status_code}"
            if response.status_code != 429 and response.status_code < 500:
                break
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        if attempt < len(waits):
            await asyncio.sleep(waits[attempt])
    raise OpenImagesError(f"GET {url} failed: {last}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def ensure_file(
    client: httpx.AsyncClient, url: str, cache: Path, delays: Sequence[float] | None = None
) -> Path:
    """``cache/<file name of url>``, downloaded only when it is not there yet."""
    path = cache / url.rsplit("/", 1)[1]
    if path.exists():
        return path
    cache.mkdir(parents=True, exist_ok=True)
    data = await _get(client, url, delays)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(data)
    partial.replace(path)
    return path


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        return image.size


async def download_images(
    client: httpx.AsyncClient,
    ids: Sequence[str],
    directory: Path,
    *,
    concurrency: int = 8,
    delays: Sequence[float] | None = None,
) -> tuple[dict[str, Downloaded], dict[str, str]]:
    """Every image of ``ids`` into ``directory`` (kept when already there and readable);
    ``(downloaded, {id: reason})`` with failures skipped."""
    directory.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    done: dict[str, Downloaded] = {}
    failed: dict[str, str] = {}

    async def one(image_id: str) -> None:
        path = directory / f"{image_id}.jpg"
        async with semaphore:
            try:
                if path.exists():
                    data = path.read_bytes()
                else:
                    data = await _get(client, IMAGE_URL.format(image_id=image_id), delays)
                width, height = _image_size(data)
            except (OpenImagesError, UnidentifiedImageError, OSError) as exc:
                failed[image_id] = str(exc)[:200]
                path.unlink(missing_ok=True)
                return
            if not path.exists():
                path.write_bytes(data)
            done[image_id] = Downloaded(path, width, height, hashlib.sha256(data).hexdigest())

    await asyncio.gather(*(one(i) for i in ids))
    return done, failed


# --------------------------------------------------------------------------- build


def build_dataset(
    selected: Sequence[str],
    boxes: Mapping[str, Sequence[tuple[str, DetectionBox]]],
    human: Mapping[str, Mapping[str, bool]],
    mids: Mapping[str, str],
    downloaded: Mapping[str, Downloaded],
) -> DetectionBoxes:
    images = []
    for image_id in selected:
        if image_id not in downloaded:
            continue
        got = downloaded[image_id]
        image_boxes = boxes.get(image_id, [])
        images.append(
            DetectionImageLabel(
                file=f"{image_id}.jpg",
                width=got.width,
                height=got.height,
                document=image_id,
                split="dev",
                verified=verified_labels(
                    (mid for mid, _ in image_boxes), human.get(image_id, {}), mids
                ),
                boxes=[box for _, box in image_boxes],
            )
        )
    return DetectionBoxes(images=images)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.openimages", description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--cache", type=Path, default=None, help="CSV cache (default <out>/cache)")
    parser.add_argument("--per-label", type=int, default=25)
    parser.add_argument(
        "--labels", default=None, help="comma-separated labels to select for (default: all)"
    )
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args(argv)
    if args.per_label < 1:
        parser.error("--per-label must be at least 1")
    return args


async def run(args: argparse.Namespace, client: httpx.AsyncClient | None = None) -> int:
    out: Path = args.out
    cache: Path = args.cache or out / "cache"
    own = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    try:
        paths = {
            name: await ensure_file(http, url, cache)
            for name, url in (
                ("classes", CLASSES_URL),
                ("boxes", BOXES_URL),
                ("image_labels", IMAGE_LABELS_URL),
                ("images_meta", IMAGES_META_URL),
            )
        }
        mids = class_map(read_classes(paths["classes"]))
        boxes = read_boxes(paths["boxes"], mids)
        meta = read_image_meta(paths["images_meta"], boxes)
        rotated = sorted(i for i in boxes if i not in meta or meta[i].rotation)
        labels = [x.strip() for x in args.labels.split(",")] if args.labels else None
        unknown = sorted(set(labels or ()) - set(mids.values()))
        if unknown:
            print(f"labels not in the Open Images mapping: {unknown}", file=sys.stderr)
            return 2
        selected = select_images(boxes, args.per_label, labels, exclude=rotated)
        human = read_image_labels(paths["image_labels"], mids)
        downloaded, failed = await download_images(
            http, selected, out / "images", concurrency=args.concurrency
        )
    except OpenImagesError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if own:
            await http.aclose()
    dataset = build_dataset(selected, boxes, human, mids, downloaded)
    (out / DATASET_FILE).write_text(dataset.model_dump_json(indent=1), encoding="utf-8")
    per_label: dict[str, int] = defaultdict(int)
    for image in dataset.images:
        for label in {b.label for b in image.boxes}:
            per_label[label] += 1
    manifest = {
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {
            "classes": CLASSES_URL,
            "boxes": BOXES_URL,
            "image_labels": IMAGE_LABELS_URL,
            "images_meta": IMAGES_META_URL,
            "images": IMAGE_URL,
        },
        "csv_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "mapping": OI_LABELS,
        "per_label_requested": args.per_label,
        "images_per_label": dict(sorted(per_label.items())),
        "images": len(dataset.images),
        "boxes": sum(len(i.boxes) for i in dataset.images),
        "skipped_rotated_or_unlisted": len(rotated),
        "failed_downloads": dict(sorted(failed.items())),
        "files": {
            f"images/{image_id}.jpg": downloaded[image_id].sha256 for image_id in sorted(downloaded)
        },
        "dataset_sha256": sha256_file(out / DATASET_FILE),
        "attribution": {
            image_id: {
                "author": meta[image_id].author,
                "license": meta[image_id].license,
                "page": meta[image_id].page,
            }
            for image_id in sorted(downloaded)
            if image_id in meta
        },
        "licence_note": "Annotations CC BY 4.0 (Google); images as listed per image "
        "(CC BY 2.0), no representation by the publishers that the licence is correct.",
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(
        f"wrote {out / DATASET_FILE}: {len(dataset.images)} images, {manifest['boxes']} boxes, "
        f"{len(failed)} failed downloads",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
