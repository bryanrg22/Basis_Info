"""Dataset loaders: the synthetic smoke dataset and files under BASIS_EVAL_DATA_DIR."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.domain.rules import RULES, RULES_VERSION
from evals import datasets as ds

BOX = {"label": "window", "x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.3}


def test_synthetic_smoke_builds_all_six_datasets_from_the_fixtures() -> None:
    data = ds.synthetic_smoke()
    # detection_boxes comes from Open Images (evals.openimages); there is no synthetic one,
    # so neither is the photo_counts dataset derived from it.
    assert set(data) == set(ds.DATASETS) - {"detection_boxes", "photo_counts"}
    fields = data["appraisal_fields"]
    assert isinstance(fields, ds.AppraisalFields)
    [doc] = fields.documents
    assert doc.document == "SYN_URAR_0001" and len(doc.fields) == 110
    by_key = {f.field: f for f in doc.fields}
    taxes = by_key["subject.real_estate_taxes"]
    assert (taxes.type, taxes.expected, taxes.printed) == ("currency", 4321, "$4,321")
    assert sum(f.critical for f in doc.fields) == 6

    rooms = data["photo_rooms"]
    assert isinstance(rooms, ds.PhotoRooms) and len(rooms.photos) == 8
    assert {p.room_type for p in rooms.photos} >= {"kitchen", "ambiguous", "mechanical"}

    components = data["photo_components"]
    assert isinstance(components, ds.PhotoComponentsSet)
    kitchen = next(p for p in components.photos if p.file.endswith("01_kitchen.jpg"))
    assert [(c.label, c.count) for c in kitchen.components] == [
        ("kitchen_cabinet", 6),
        ("countertop", 1),
    ]

    assets = data["assets"]
    assert isinstance(assets, ds.Assets)
    assert [(a.component, a.section) for a in assets.assets] == [
        ("carpet", "1245"),
        ("kitchen_cabinet", "1245"),
        ("bathtub", "1250"),
        ("toilet", "1250"),
        ("ac_condenser", "1250"),
        ("garage_door", "1250"),
    ]

    qrels = data["retrieval_qrels"]
    assert isinstance(qrels, ds.RetrievalQrels) and len(qrels.queries) == 20
    assert qrels.qrels()["q01"] == {"SYN_PUB_A:1": 2}

    inventory = data["room_inventory"]
    assert isinstance(inventory, ds.RoomInventorySet) and len(inventory.rooms) == 8
    kitchen_room = next(r for r in inventory.rooms if r.room_id == "x_kitchen")
    assert (kitchen_room.name, kitchen_room.floor, kitchen_room.room_type) == (
        "kitchen",
        1,
        "kitchen",
    )
    assert kitchen_room.photos == ["photos/01_kitchen.jpg"]
    assert kitchen_room.counts() == {"kitchen_cabinet": 6, "countertop": 1}
    bedroom = next(r for r in inventory.rooms if r.room_id == "x_bedroom")
    assert "other" in {o.label for o in bedroom.objects} and "other" not in bedroom.counts()


def test_rule_citations_are_every_citation_of_the_rules_table() -> None:
    data = ds.rule_citations()
    assert data.rules_version == RULES_VERSION
    expected = [
        (rule.key, c.doc_id, c.page, c.excerpt, rule.contested is not None)
        for rule in RULES.values()
        for c in rule.citations
    ]
    assert [(c.rule, c.doc_id, c.page, c.excerpt, c.contested) for c in data.citations] == (
        expected
    )
    assert {c.rule for c in data.citations} == set(RULES)  # every row has a citation


def write(directory: Path, name: str, payload: object) -> None:
    (directory / ds.DATASET_FILES[name]).write_text(json.dumps(payload))


def test_loaders_read_the_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(
        tmp_path,
        "photo_rooms",
        {
            "photos": [
                {"file": "a.jpg", "room_type": "kitchen", "document": "pkg1"},
                {"file": "b.jpg", "room_type": "bath", "split": "held_out"},
            ]
        },
    )
    write(
        tmp_path,
        "retrieval_qrels",
        {
            "queries": [
                {"id": "q1", "query": "57.0", "relevant": [{"doc_id": "D", "page": 3, "grade": 2}]},
                {"id": "q2", "query": "x", "relevant": [], "split": "regression"},
            ]
        },
    )
    monkeypatch.setenv(ds.ENV_VAR, str(tmp_path))
    rooms = ds.load("photo_rooms")
    assert isinstance(rooms, ds.PhotoRooms)
    assert [(p.document, p.split) for p in rooms.photos] == [
        ("pkg1", "dev"),
        ("package", "held_out"),
    ]
    available = ds.load_available()
    assert set(available) == {"photo_rooms", "retrieval_qrels", "rule_citations"}
    qrels = available["retrieval_qrels"]
    assert isinstance(qrels, ds.RetrievalQrels)
    assert qrels.qrels("dev") == {"q1": {"D:3": 2}}
    assert qrels.qrels("regression") == {"q2": {}}


def test_missing_directory_file_and_unknown_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ds.ENV_VAR, raising=False)
    with pytest.raises(ds.DatasetError, match=ds.ENV_VAR):
        ds.data_dir()
    with pytest.raises(ds.DatasetError, match="not a directory"):
        ds.data_dir(tmp_path / "nope")
    with pytest.raises(ds.DatasetError, match="missing dataset file"):
        ds.load("assets", tmp_path)
    with pytest.raises(ds.DatasetError, match="unknown dataset"):
        ds.load("photos", tmp_path)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("assets", {"assets": [{"id": "1", "component": "carpet", "bucket": "y99"}]}),
        ("photo_rooms", {"photos": [{"file": "a.jpg"}]}),
        (
            "retrieval_qrels",
            {
                "queries": [
                    {"id": "q", "query": "x", "relevant": [{"doc_id": "D", "page": 0, "grade": 1}]}
                ]
            },
        ),
        (
            "appraisal_fields",
            {
                "documents": [
                    {"document": "d", "fields": [{"field": "f", "expected": 1, "type": "money"}]}
                ]
            },
        ),
        ("photo_components", {"photos": [{"file": "a", "components": [], "extra": 1}]}),
        (
            "room_inventory",
            {
                "rooms": [
                    {
                        "room_id": "1_kitchen",
                        "room_type": "kitchen",
                        "photos": ["a.jpg"],
                        "objects": [{"label": "sink", "count": 1, "photos": ["b.jpg"]}],
                    }
                ]
            },
        ),
        (
            "detection_boxes",
            {
                "images": [
                    {"file": "a.jpg", "width": 10, "height": 10, "boxes": [BOX | {"label": "lamp"}]}
                ]
            },
        ),
        (
            "detection_boxes",
            {
                "images": [
                    {"file": "a.jpg", "width": 10, "height": 10, "boxes": [BOX | {"x2": 0.05}]}
                ]
            },
        ),
        (
            "detection_boxes",
            {
                "images": [
                    {"file": "a.jpg", "width": 10, "height": 10, "boxes": [BOX | {"y2": 1.5}]}
                ]
            },
        ),
        (
            "detection_boxes",
            {
                "images": [
                    {"file": "a.jpg", "width": 10, "height": 10, "boxes": []},
                    {"file": "a.jpg", "width": 10, "height": 10, "boxes": []},
                ]
            },
        ),
        (
            "room_inventory",
            {
                "rooms": [
                    {"room_id": "r", "room_type": "kitchen", "photos": [], "objects": []},
                    {"room_id": "r", "room_type": "bathroom", "photos": [], "objects": []},
                ]
            },
        ),
        (
            "room_inventory",
            {
                "rooms": [
                    {
                        "room_id": "r",
                        "room_type": "kitchen",
                        "photos": [],
                        "objects": [{"label": "sink", "count": 0}],
                    }
                ]
            },
        ),
    ],
)
def test_malformed_files_are_rejected(tmp_path: Path, name: str, payload: object) -> None:
    write(tmp_path, name, payload)
    with pytest.raises(ValidationError):
        ds.load(name, tmp_path)


def test_detection_boxes_load_with_component_labels_and_door(tmp_path: Path) -> None:
    write(
        tmp_path,
        "detection_boxes",
        {
            "schema_version": 1,
            "images": [
                {
                    "file": "0a1b.jpg",
                    "width": 1024,
                    "height": 768,
                    "document": "0a1b",
                    "verified": ["window", "door"],
                    "boxes": [BOX, BOX | {"label": "door", "group_of": True}],
                }
            ],
        },
    )
    data = ds.load("detection_boxes", tmp_path)
    assert isinstance(data, ds.DetectionBoxes)
    [image] = data.images
    assert (image.split, image.verified) == ("dev", ["window", "door"])
    assert [(b.label, b.group_of) for b in image.boxes] == [("window", False), ("door", True)]


def test_corpus_pdfs_are_keyed_by_file_stem() -> None:
    pdfs = ds.corpus_pdfs(ds.SYNTHETIC_DIR / "corpus")
    assert list(pdfs) == ["SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C"]


def test_photo_counts_derive_from_verified_non_group_boxes() -> None:
    boxes = ds.DetectionBoxes.model_validate(
        {
            "images": [
                {
                    "file": "a.jpg",
                    "width": 10,
                    "height": 10,
                    "verified": ["window", "interior_door", "exterior_door", "electrical_outlet"],
                    "boxes": [
                        {"label": "window", "x1": 0, "y1": 0, "x2": 0.5, "y2": 0.5},
                        {"label": "window", "x1": 0.5, "y1": 0, "x2": 1, "y2": 0.5},
                        {"label": "window", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "group_of": True},
                        {"label": "interior_door", "x1": 0, "y1": 0.5, "x2": 0.5, "y2": 1},
                        {"label": "exterior_door", "x1": 0.5, "y1": 0.5, "x2": 1, "y2": 1},
                        {"label": "sink", "x1": 0, "y1": 0.5, "x2": 0.5, "y2": 1},
                    ],
                },
                {
                    "file": "b.jpg",
                    "width": 10,
                    "height": 10,
                    "boxes": [{"label": "toilet", "x1": 0, "y1": 0, "x2": 1, "y2": 1}],
                },
            ]
        }
    )
    by_file = {p.file: p.counts for p in ds.photo_counts(boxes).photos}
    # group boxes are not counted; an unverified label (sink) is not a class of the photo;
    # the two door labels pool into one "door" class; verified-absent classes are zero
    assert by_file["a.jpg"] == {"window": 2, "door": 2, "electrical_outlet": 0}
    assert by_file["b.jpg"] == {"toilet": 1}  # no verified list: every boxed label
    assert "photo_counts" in ds.DERIVED_DATASETS
