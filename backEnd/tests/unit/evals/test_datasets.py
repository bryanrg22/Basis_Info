"""Dataset loaders: the synthetic smoke dataset and files under BASIS_EVAL_DATA_DIR."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals import datasets as ds


def test_synthetic_smoke_builds_all_five_datasets_from_the_fixtures() -> None:
    data = ds.synthetic_smoke()
    assert set(data) == set(ds.DATASET_FILES)
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
    assert set(available) == {"photo_rooms", "retrieval_qrels"}
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
    ],
)
def test_malformed_files_are_rejected(tmp_path: Path, name: str, payload: object) -> None:
    write(tmp_path, name, payload)
    with pytest.raises(ValidationError):
        ds.load(name, tmp_path)


def test_corpus_pdfs_are_keyed_by_file_stem() -> None:
    pdfs = ds.corpus_pdfs(ds.SYNTHETIC_DIR / "corpus")
    assert list(pdfs) == ["SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C"]
