"""``evals.openimages`` on tiny synthetic CSVs: parsing, the label mapping, verified labels,
balanced deterministic selection, downloads (served by ``httpx.MockTransport``; nothing is
fetched from the network), the dataset and the manifest."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from evals import openimages as oi
from evals.datasets import DetectionBoxes

CLASSES = """LabelName,DisplayName
/m/win,Window
/m/door,Door
/m/plug,Power plugs and sockets
/m/fan,Ceiling fan
/m/lamp,Lamp
/m/toilet,Toilet
/m/sink,Sink
/m/tub,Bathtub
/m/shower,Shower
/m/cab,Cabinetry
/m/counter,Countertop
/m/fire,Fireplace
/m/stairs,Stairs
/m/oven,Oven
/m/stove,Gas stove
/m/fridge,Refrigerator
/m/dish,Dishwasher
/m/micro,Microwave oven
/m/wash,Washing machine
/m/curtain,Curtain
/m/blind,Window blind
/m/porch,Porch
/m/pool,Swimming pool
/m/switch,Light switch
/m/cat,Cat
"""
HEADER = (
    "ImageID,Source,LabelName,Confidence,XMin,XMax,YMin,YMax,"
    "IsOccluded,IsTruncated,IsGroupOf,IsDepiction,IsInside\n"
)
BOXES = HEADER + "\n".join(
    [
        "c3,xclick,/m/win,1,0.1,0.3,0.2,0.4,0,0,0,0,0",
        "c3,xclick,/m/win,1,0.5,0.9,0.0,0.5,0,0,1,0,0",
        "a1,xclick,/m/win,1,0.0,0.2,0.0,0.2,0,0,0,0,0",
        "a1,xclick,/m/plug,1,0.4,0.45,0.6,0.65,0,0,0,0,0",
        "b2,xclick,/m/door,1,0.2,0.5,0.1,0.9,0,0,0,0,0",
        "b2,xclick,/m/cat,1,0.0,1.0,0.0,1.0,0,0,0,0,0",
        "d4,xclick,/m/win,1,0.1,0.2,0.1,0.2,0,0,0,0,0",
        "e5,xclick,/m/switch,1,0.1,0.2,0.1,0.2,0,0,0,0,0",
        "f6,xclick,/m/plug,1,0.1,0.2,0.1,0.2,0,0,0,0,0",
    ]
)
IMAGE_LABELS = """ImageID,Source,LabelName,Confidence
a1,verification,/m/door,0
a1,verification,/m/toilet,1
b2,verification,/m/win,0
b2,verification,/m/cat,1
c3,verification,/m/plug,0
"""
META_COLUMNS = (
    "ImageID,Subset,OriginalURL,OriginalLandingURL,License,AuthorProfileURL,Author,Title,"
    "OriginalSize,OriginalMD5,Thumbnail300KURL,Rotation"
)
CC_BY = "https://creativecommons.org/licenses/by/2.0/"
META = "\n".join(
    [META_COLUMNS]
    + [
        f"{image_id},validation,u,https://flickr/{image_id},{CC_BY},p,{author},t,1,m,t,{rotation}"
        for image_id, author, rotation in [
            ("a1", "Ann", "0.0"),
            ("b2", "Bob", ""),
            ("c3", "Cy", "0.0"),
            ("d4", "Di", "90.0"),
            ("f6", "Fe", "0.0"),
        ]
    ]
)


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def jpeg(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "gray").save(out, format="JPEG")
    return out.getvalue()


def test_classes_and_mapping(tmp_path: Path) -> None:
    classes = oi.read_classes(write(tmp_path, "c.csv", CLASSES))
    assert classes["/m/plug"] == "Power plugs and sockets"
    no_header = oi.read_classes(write(tmp_path, "n.csv", "/m/win,Window\n"))
    assert no_header == {"/m/win": "Window"}
    mids = oi.class_map(classes)
    assert mids["/m/win"] == "window" and mids["/m/door"] == "door"
    assert mids["/m/plug"] == "electrical_outlet" and mids["/m/lamp"] == "light_fixture"
    assert "/m/switch" not in mids and "/m/cat" not in mids  # Light switch is not used
    assert len(mids) == len(oi.OI_LABELS) == 23
    with pytest.raises(oi.OpenImagesError, match="Window"):
        oi.class_map({"/m/door": "Door"})


def test_the_mapping_uses_component_labels_or_door() -> None:
    from basis.vision.schemas import COMPONENT_LABELS

    assert set(oi.OI_LABELS.values()) - set(COMPONENT_LABELS) == {"door"}
    assert oi.OI_LABELS["Cabinetry"] == "kitchen_cabinet"
    assert oi.OI_LABELS["Washing machine"] == "washer"
    assert oi.OI_LABELS["Swimming pool"] == "pool"


def test_boxes_are_parsed_for_mapped_classes_only(tmp_path: Path) -> None:
    mids = oi.class_map(oi.read_classes(write(tmp_path, "c.csv", CLASSES)))
    boxes = oi.read_boxes(write(tmp_path, "b.csv", BOXES), mids)
    assert set(boxes) == {"a1", "b2", "c3", "d4", "f6"}  # e5 has only a light switch
    [(mid, door)] = boxes["b2"]  # the cat is dropped
    assert mid == "/m/door"
    assert (door.label, door.x1, door.y1, door.x2, door.y2) == ("door", 0.2, 0.1, 0.5, 0.9)
    assert [b.group_of for _, b in boxes["c3"]] == [False, True]


def test_verified_labels_need_every_source_boxed_or_verified_absent(tmp_path: Path) -> None:
    mids = oi.class_map(oi.read_classes(write(tmp_path, "c.csv", CLASSES)))
    human = oi.read_image_labels(write(tmp_path, "h.csv", IMAGE_LABELS), mids)
    assert human == {
        "a1": {"/m/door": False, "/m/toilet": True},
        "b2": {"/m/win": False},
        "c3": {"/m/plug": False},
    }
    # a1: window and plug boxed, door verified absent; toilet verified present but not
    # boxed -> not exhaustive.
    assert oi.verified_labels(["/m/win", "/m/plug"], human["a1"], mids) == [
        "door",
        "electrical_outlet",
        "window",
    ]
    two_sources = {"/m/a": "light_fixture", "/m/b": "light_fixture"}
    assert oi.verified_labels(["/m/a"], {}, two_sources) == []
    assert oi.verified_labels(["/m/a"], {"/m/b": False}, two_sources) == ["light_fixture"]


def test_selection_is_balanced_deterministic_and_skips_excluded(tmp_path: Path) -> None:
    mids = oi.class_map(oi.read_classes(write(tmp_path, "c.csv", CLASSES)))
    boxes = oi.read_boxes(write(tmp_path, "b.csv", BOXES), mids)
    # door: b2. electrical_outlet: a1, f6. window: a1, c3, d4 (a1 already counts).
    assert oi.select_images(boxes, 1) == ["a1", "b2"]
    assert oi.select_images(boxes, 2) == ["a1", "b2", "c3", "f6"]
    assert oi.select_images(boxes, 2, exclude=["a1"]) == ["b2", "c3", "d4", "f6"]
    assert oi.select_images(boxes, 5, labels=["door"]) == ["b2"]
    assert oi.select_images(boxes, 2) == oi.select_images(dict(reversed(boxes.items())), 2)
    with pytest.raises(ValueError):
        oi.select_images(boxes, 0)


def server(
    images: dict[str, bytes], failing: set[str] | None = None
) -> tuple[list[str], httpx.MockTransport]:
    seen: list[str] = []
    files = {
        oi.CLASSES_URL: CLASSES,
        oi.BOXES_URL: BOXES,
        oi.IMAGE_LABELS_URL: IMAGE_LABELS,
        oi.IMAGES_META_URL: META,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url in files:
            return httpx.Response(200, content=files[url].encode())
        image_id = url.rsplit("/", 1)[1].removesuffix(".jpg")
        if failing and image_id in failing:
            return httpx.Response(503)
        if image_id in images:
            return httpx.Response(200, content=images[image_id])
        return httpx.Response(404)

    return seen, httpx.MockTransport(handler)


async def test_run_builds_the_dataset_and_manifest(tmp_path: Path) -> None:
    images = {"a1": jpeg(64, 48), "b2": jpeg(80, 60), "c3": b"not an image", "f6": jpeg(90, 90)}
    seen, transport = server(images)
    out = tmp_path / "oi"
    args = oi.parse_args(["--out", str(out), "--per-label", "2"])
    async with httpx.AsyncClient(transport=transport) as client:
        assert await oi.run(args, client) == 0
    data = DetectionBoxes.model_validate_json((out / oi.DATASET_FILE).read_text())
    # d4 is rotated (excluded before selection); c3 is unreadable (skipped).
    assert [i.file for i in data.images] == ["a1.jpg", "b2.jpg", "f6.jpg"]
    a1 = data.images[0]
    assert (a1.width, a1.height, a1.document, a1.split) == (64, 48, "a1", "dev")
    assert a1.verified == ["door", "electrical_outlet", "window"]
    assert [b.label for b in a1.boxes] == ["window", "electrical_outlet"]
    assert (out / "images" / "a1.jpg").read_bytes() == images["a1"]
    assert not (out / "images" / "c3.jpg").exists()
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["images"] == 3 and manifest["boxes"] == 4
    assert list(manifest["failed_downloads"]) == ["c3"]
    assert manifest["skipped_rotated_or_unlisted"] == 1
    assert manifest["files"]["images/b2.jpg"] == hashlib.sha256(images["b2"]).hexdigest()
    assert manifest["csv_sha256"]["boxes"] == hashlib.sha256(BOXES.encode()).hexdigest()
    assert manifest["attribution"]["a1"]["author"] == "Ann"
    assert manifest["images_per_label"] == {"door": 1, "electrical_outlet": 2, "window": 1}

    # A second run reuses the cached CSVs and the images already on disk.
    seen.clear()
    async with httpx.AsyncClient(transport=transport) as client:
        assert await oi.run(args, client) == 0
    assert seen == [oi.IMAGE_URL.format(image_id="c3")]


async def test_download_retries_then_skips(tmp_path: Path) -> None:
    seen, transport = server({"a1": jpeg(64, 64)}, failing={"b2"})
    async with httpx.AsyncClient(transport=transport) as client:
        done, failed = await oi.download_images(
            client, ["a1", "b2", "zz"], tmp_path, delays=(0.0, 0.0)
        )
    assert set(done) == {"a1"} and (done["a1"].width, done["a1"].height) == (64, 64)
    assert "HTTP 503" in failed["b2"] and "HTTP 404" in failed["zz"]
    assert sum(1 for u in seen if u.endswith("b2.jpg")) == 3  # retried
    assert sum(1 for u in seen if u.endswith("zz.jpg")) == 1  # a 404 is not retried


async def test_unknown_label_and_missing_csv_exit_2(tmp_path: Path) -> None:
    _, transport = server({})
    async with httpx.AsyncClient(transport=transport) as client:
        args = oi.parse_args(["--out", str(tmp_path / "o"), "--labels", "window,lamp"])
        assert await oi.run(args, client) == 2

    def nothing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(nothing)) as client:
        assert await oi.run(oi.parse_args(["--out", str(tmp_path / "p")]), client) == 2
    with pytest.raises(SystemExit):
        oi.parse_args(["--out", "x", "--per-label", "0"])
