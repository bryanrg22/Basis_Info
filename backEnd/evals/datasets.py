"""Evaluation dataset loaders (formats in ``evals/README.md``).

Real datasets live outside the repository under ``BASIS_EVAL_DATA_DIR`` (client data never
enters the repo); :func:`synthetic_smoke` builds the same five datasets from
``tests/fixtures/synthetic/`` so the harness can be exercised anywhere (``--dry-run``).

Every item has a ``split`` (``dev``, ``regression``, ``held_out``) and a ``document``, the
cluster used for clustered standard errors (photos and fields from one property are not
independent).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.metrics import FieldType

Split = Literal["dev", "regression", "held_out"]
ENV_VAR: Final = "BASIS_EVAL_DATA_DIR"
DATASET_FILES: Final[dict[str, str]] = {
    "appraisal_fields": "appraisal_fields_v1.json",
    "photo_rooms": "photo_rooms_v1.json",
    "photo_components": "photo_components_v1.json",
    "assets": "assets_v1.json",
    "retrieval_qrels": "retrieval_qrels_v1.json",
}
SYNTHETIC_DIR: Final = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldLabel(_Model):
    field: str
    expected: str | int | float | bool | None
    type: FieldType = "text"
    absolute: float | None = None  # tolerance overrides; defaults per type in metrics
    relative: float | None = None
    critical: bool = False
    printed: str | None = None  # the value as printed in the document, when known


class AppraisalDocument(_Model):
    document: str
    split: Split = "dev"
    fields: list[FieldLabel]


class AppraisalFields(_Model):
    schema_version: int = 1
    documents: list[AppraisalDocument]


class PhotoRoom(_Model):
    file: str
    document: str = "package"
    split: Split = "dev"
    room_type: str
    floor: int | str | None = None


class PhotoRooms(_Model):
    schema_version: int = 1
    photos: list[PhotoRoom]


class ComponentLabel(_Model):
    label: str
    material: str | None = None
    attachment: str | None = None
    count: int = Field(default=1, ge=1)


class PhotoComponents(_Model):
    file: str
    document: str = "package"
    split: Split = "dev"
    components: list[ComponentLabel]


class PhotoComponentsSet(_Model):
    schema_version: int = 1
    photos: list[PhotoComponents]


class AssetLabel(_Model):
    id: str
    component: str
    material: str | None = None
    attachment: str | None = None
    property_class: Literal["residential_rental", "nonresidential"] = "residential_rental"
    section: Literal["1245", "1250"] | None = None
    bucket: Literal["y5", "y7", "y15", "y27_5", "y39"] | None = None
    amount_usd: float | None = None
    document: str = "package"
    split: Split = "dev"


class Assets(_Model):
    schema_version: int = 1
    assets: list[AssetLabel]


class Judgment(_Model):
    doc_id: str
    page: int = Field(ge=1)
    grade: int = Field(ge=0)


class Query(_Model):
    id: str
    query: str
    relevant: list[Judgment]
    split: Split = "dev"
    codes: list[str] = Field(default_factory=list)


class RetrievalQrels(_Model):
    schema_version: int = 1
    queries: list[Query]

    def qrels(self, split: str | None = None) -> dict[str, dict[str, int]]:
        """``{query id: {"doc_id:page": grade}}`` (the unit is doc + page)."""
        return {
            q.id: {f"{j.doc_id}:{j.page}": j.grade for j in q.relevant}
            for q in self.queries
            if split is None or q.split == split
        }


Dataset = AppraisalFields | PhotoRooms | PhotoComponentsSet | Assets | RetrievalQrels
MODELS: Final[dict[str, type[BaseModel]]] = {
    "appraisal_fields": AppraisalFields,
    "photo_rooms": PhotoRooms,
    "photo_components": PhotoComponentsSet,
    "assets": Assets,
    "retrieval_qrels": RetrievalQrels,
}


class DatasetError(RuntimeError):
    pass


def data_dir(explicit: str | Path | None = None) -> Path:
    value = explicit if explicit is not None else os.environ.get(ENV_VAR)
    if not value:
        raise DatasetError(f"{ENV_VAR} is not set and no data directory was given")
    path = Path(value)
    if not path.is_dir():
        raise DatasetError(f"{path} is not a directory")
    return path


def _read[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DatasetError(f"missing dataset file {path}") from None


def load(name: str, directory: str | Path | None = None) -> BaseModel:
    """One dataset by name from the data directory, validated."""
    if name not in DATASET_FILES:
        raise DatasetError(f"unknown dataset {name!r}; known: {', '.join(DATASET_FILES)}")
    return _read(data_dir(directory) / DATASET_FILES[name], MODELS[name])


def load_available(directory: str | Path | None = None) -> dict[str, BaseModel]:
    """Every dataset file present in the data directory (missing ones are skipped)."""
    root = data_dir(directory)
    return {
        name: _read(root / filename, MODELS[name])
        for name, filename in DATASET_FILES.items()
        if (root / filename).exists()
    }


# --------------------------------------------------------------------------- synthetic

# Table B-1 of the synthetic corpus (SYN_PUB_B p.3) -> component labels of the vocabulary.
_SYNTHETIC_COMPONENTS: Final[dict[str, tuple[str, str | None, str | None]]] = {
    "Tack-down carpet": ("carpet", None, "removable"),
    "Kitchen cabinets (freestanding)": ("kitchen_cabinet", None, "freestanding"),
    "Bathtub": ("bathtub", None, None),
    "Toilet": ("toilet", None, None),
    "Central AC condenser": ("ac_condenser", None, None),
    "Garage door": ("garage_door", None, None),
}
_FIELD_TYPES: Final[dict[str, FieldType]] = {
    "text": "text",
    "int": "int",
    "float": "float",
    "currency": "currency",
    "percent": "percent",
    "date": "date",
    "bool": "bool",
}


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def synthetic_smoke(fixtures: Path = SYNTHETIC_DIR) -> dict[str, BaseModel]:
    """The five datasets built from the synthetic fixtures (all ``dev``)."""
    urar = _json(fixtures / "urar_sample.expected.json")
    fields = AppraisalFields(
        documents=[
            AppraisalDocument(
                document=urar["doc_id"],
                fields=[
                    FieldLabel(
                        field=f["key"],
                        expected=f["value"],
                        type=_FIELD_TYPES[f["type"]],
                        critical=bool(f.get("critical")),
                        printed=f.get("text"),
                    )
                    for f in urar["fields"]
                ],
            )
        ]
    )
    labels = _json(fixtures / "photos" / "labels.json")
    rooms = PhotoRooms(
        photos=[
            PhotoRoom(
                file=p["file"], document="synthetic", room_type=p["room_type"], floor=p["floor"]
            )
            for p in labels["photos"]
        ]
    )
    components = PhotoComponentsSet(
        photos=[
            PhotoComponents(
                file=p["file"],
                document="synthetic",
                components=[
                    ComponentLabel(label=c["label"], count=c.get("count", 1))
                    for c in p["components"]
                ],
            )
            for p in labels["photos"]
        ]
    )
    manifest = _json(fixtures / "corpus" / "manifest.json")
    table = next(t for d in manifest["documents"] for t in d.get("tables", []) if t["id"] == "B-1")
    assets = Assets(
        assets=[
            AssetLabel(
                id=f"B-1:{i}",
                component=_SYNTHETIC_COMPONENTS[row[0]][0],
                material=_SYNTHETIC_COMPONENTS[row[0]][1],
                attachment=_SYNTHETIC_COMPONENTS[row[0]][2],
                section=row[1],
                document="SYN_PUB_B",
            )
            for i, row in enumerate(table["rows"], start=1)
        ]
    )
    qrels = RetrievalQrels.model_validate(
        {
            "queries": [
                {k: v for k, v in q.items() if k in {"id", "query", "relevant", "codes"}}
                for q in _json(fixtures / "corpus" / "qrels.json")["queries"]
            ]
        }
    )
    return {
        "appraisal_fields": fields,
        "photo_rooms": rooms,
        "photo_components": components,
        "assets": assets,
        "retrieval_qrels": qrels,
    }


def corpus_pdfs(directory: Path) -> dict[str, Path]:
    """``{doc_id: pdf}`` for the reference PDFs in ``directory`` (doc id = file stem)."""
    return {p.stem: p for p in sorted(directory.glob("*.pdf"))}
