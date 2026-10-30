"""Evaluation dataset loaders (formats in ``evals/README.md``).

Real datasets live outside the repository under ``BASIS_EVAL_DATA_DIR`` (client data never
enters the repo); :func:`synthetic_smoke` builds the same five datasets from
``tests/fixtures/synthetic/`` so the harness can be exercised anywhere (``--dry-run``);
the sixth, ``room_inventory``, is built there from the fixture photos with every photo its
own room (named by :func:`basis.vision.rooms.name_hint`). ``detection_boxes`` (boxes per
component instance, built from Open Images by :mod:`evals.openimages`) has no synthetic
counterpart: the dry run reports it as missing. One more dataset, ``rule_citations``, has
no file: it is every ``(doc_id, page, excerpt)`` claim of the rules table
(:mod:`basis.domain.rules`), built from the code (:func:`rule_citations`) and checked
against the corpus PDFs. ``report_rows`` is the product-level checklist: one item per row
of the engineer's asset schedule with the evidence rule the photo pipeline must satisfy
(COMPONENT_TAXONOMY.md section 1); the synthetic copy is built from the synthetic assets.

Every item has a ``split`` (``dev``, ``regression``, ``held_out``) and a ``document``, the
cluster used for clustered standard errors (photos and fields from one property are not
independent).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from basis.domain.rules import RULES_VERSION, hit_name, table_hits
from basis.vision.rooms import display_name, name_hint
from basis.vision.schemas import COMPONENT_LABELS, ROOM_TYPES, Material
from evals.metrics import FieldType

Split = Literal["dev", "regression", "held_out"]
ENV_VAR: Final = "BASIS_EVAL_DATA_DIR"
DATASET_FILES: Final[dict[str, str]] = {
    "appraisal_fields": "appraisal_fields_v1.json",
    "photo_rooms": "photo_rooms_v1.json",
    "photo_components": "photo_components_v1.json",
    "assets": "assets_v1.json",
    "retrieval_qrels": "retrieval_qrels_v1.json",
    "room_inventory": "room_inventory_v1.json",
    "detection_boxes": "detection_boxes_v1.json",
    "report_rows": "report_rows_v1.json",
}
# Gold labels a detection dataset may use besides the component vocabulary: Open Images'
# Door does not say interior or exterior (scored against both, evals.metrics.CLASS_UNION).
DETECTION_EXTRA_LABELS: Final = frozenset({"door"})

# Datasets built from code rather than read from a file (always available).
DERIVED_DATASETS: Final[tuple[str, ...]] = ("rule_citations", "photo_counts")
DATASETS: Final[tuple[str, ...]] = (*DATASET_FILES, *DERIVED_DATASETS)
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


class RuleCitationItem(_Model):
    """One citation claim of one rules-table row."""

    rule: str
    doc_id: str
    page: int = Field(ge=1)
    excerpt: str
    contested: bool = False


class RuleCitations(_Model):
    schema_version: int = 1
    rules_version: str
    citations: list[RuleCitationItem]


def rule_citations() -> RuleCitations:
    """Every citation of every rules-table row and of every row's variants, in table order
    (a variant's ``rule`` is ``key[context]``, e.g. ``kitchen_cabinet[room=bathroom]``)."""
    return RuleCitations(
        rules_version=RULES_VERSION,
        citations=[
            RuleCitationItem(
                rule=hit_name(hit),
                doc_id=claim.doc_id,
                page=claim.page,
                excerpt=claim.excerpt,
                contested=hit.contested is not None,
            )
            for hit in table_hits()
            for claim in hit.citations
        ],
    )


class InventoryObjectLabel(_Model):
    """One distinct object (or several identical ones) and the photos it is visible in."""

    label: str
    count: int = Field(default=1, ge=1)
    photos: list[str] = Field(default_factory=list)
    note: str | None = None


class RoomInventoryLabel(_Model):
    room_id: str
    name: str = ""
    floor: int | None = None
    room_type: str
    photos: list[str]
    document: str = "package"
    split: Split = "dev"
    objects: list[InventoryObjectLabel]

    @model_validator(mode="after")
    def _object_photos_belong_to_the_room(self) -> RoomInventoryLabel:
        unknown = sorted({f for o in self.objects for f in o.photos} - set(self.photos))
        if unknown:
            raise ValueError(f"room {self.room_id}: objects cite photos not in the room: {unknown}")
        return self

    def counts(self, exclude: frozenset[str] = frozenset({"other"})) -> dict[str, int]:
        """``{label: count}``; several objects with one label are distinct and add up."""
        totals: dict[str, int] = {}
        for obj in self.objects:
            if obj.label not in exclude:
                totals[obj.label] = totals.get(obj.label, 0) + obj.count
        return totals


class RoomInventorySet(_Model):
    schema_version: int = 1
    rooms: list[RoomInventoryLabel]

    @model_validator(mode="after")
    def _unique_room_ids(self) -> RoomInventorySet:
        seen: set[str] = set()
        for room in self.rooms:
            if room.room_id in seen:
                raise ValueError(f"room_id {room.room_id!r} appears twice")
            seen.add(room.room_id)
        return self


def _detection_label(value: str) -> str:
    if value not in COMPONENT_LABELS and value not in DETECTION_EXTRA_LABELS:
        raise ValueError(f"{value!r} is not a component label (or 'door')")
    return value


class DetectionBox(_Model):
    """One instance, normalized to the image; ``group_of`` marks a box around several
    instances (Open Images' IsGroupOf): it is never a miss and its count is unknown."""

    label: str
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)
    group_of: bool = False

    _label = field_validator("label")(_detection_label)

    @model_validator(mode="after")
    def _ordered(self) -> DetectionBox:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError("a box needs x1 <= x2 and y1 <= y2")
        return self


class DetectionImageLabel(_Model):
    file: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    document: str = "package"
    split: Split = "dev"
    # Labels exhaustively annotated in this image (boxed, or verified absent); None means
    # every label is (our own labelling). Open Images is exhaustive only per verified label.
    verified: list[str] | None = None
    boxes: list[DetectionBox]


class DetectionBoxes(_Model):
    schema_version: int = 1
    images: list[DetectionImageLabel]

    @model_validator(mode="after")
    def _unique_files(self) -> DetectionBoxes:
        seen: set[str] = set()
        for image in self.images:
            if image.file in seen:
                raise ValueError(f"file {image.file!r} appears twice")
            seen.add(image.file)
        return self


Bucket = Literal["y5", "y7", "y15", "y27_5", "y39"]
RowKind = Literal["A", "B", "C"]
MATERIAL_VALUES: Final = frozenset(m.value for m in Material)
_MATERIAL_TOKEN: Final = re.compile(r"^[a-z][a-z0-9]*$")


def _component_label(value: str) -> str:
    if value == "other" or value not in COMPONENT_LABELS:
        raise ValueError(f"{value!r} is not a component label (or is 'other')")
    return value


class ReportRow(_Model):
    """One row of the engineer's asset schedule and the evidence rule the photo pipeline
    must satisfy for it (COMPONENT_TAXONOMY.md section 1).

    ``kind`` A (countable objects) or B (surfaces: the material is the question) rows are
    photo-derivable; a kind C row is not (appraisal, plans, walk-through) and is never a
    miss. A photo-derivable row is evidenced by any of ``labels`` in a room that passes the
    room-type filters, with a material in ``materials[label]`` where that label is
    constrained. A photo-derivable row with no label in the vocabulary yet names the
    label it needs in ``missing_label`` and scores as a vocabulary gap, not a miss.
    """

    id: str
    system: str  # the row's name in the report
    bucket: Bucket
    cost_share: float = Field(ge=0.0, le=1.0)  # of the study's total cost
    kind: RowKind
    labels: list[str] = Field(default_factory=list)  # any-of
    room_types: list[str] | None = None  # only rooms of these types count (None: any)
    exclude_room_types: list[str] = Field(default_factory=list)
    # label -> material tokens that evidence the row (any one of them among the tokens of a
    # reported material: "ceramic" matches the model's "ceramic" and a label's "ceramic_tile")
    materials: dict[str, list[str]] = Field(default_factory=dict)
    count_required: bool = False
    material_required: bool = False
    missing_label: str | None = None  # the vocabulary label this row needs, when none exists
    note: str | None = None
    document: str = "package"
    split: Split = "dev"

    @field_validator("labels")
    @classmethod
    def _labels_in_vocabulary(cls, value: list[str]) -> list[str]:
        for label in value:
            _component_label(label)
        if len(set(value)) != len(value):
            raise ValueError("labels repeat")
        return value

    @field_validator("room_types", "exclude_room_types")
    @classmethod
    def _room_types_known(cls, value: list[str] | None) -> list[str] | None:
        for room_type in value or []:
            if room_type not in ROOM_TYPES:
                raise ValueError(f"{room_type!r} is not a room type")
        return value

    @model_validator(mode="after")
    def _rule_is_consistent(self) -> ReportRow:
        if self.kind == "C":
            if self.labels or self.missing_label or self.count_required or self.material_required:
                raise ValueError(f"row {self.id}: a kind C row has no evidence rule")
            return self
        if bool(self.labels) == bool(self.missing_label):
            raise ValueError(
                f"row {self.id}: a kind {self.kind} row needs labels or a missing_label, not both"
            )
        unknown = sorted(set(self.materials) - set(self.labels))
        if unknown:
            raise ValueError(f"row {self.id}: materials for labels not in the rule: {unknown}")
        for label, allowed in self.materials.items():
            bad = sorted(t for t in allowed if not _MATERIAL_TOKEN.match(t))
            if bad or not allowed:
                raise ValueError(f"row {self.id}: bad material tokens for {label}: {bad}")
        if self.room_types is not None and not self.room_types:
            raise ValueError(f"row {self.id}: room_types must be null or non-empty")
        if set(self.room_types or []) & set(self.exclude_room_types):
            raise ValueError(f"row {self.id}: a room type is both required and excluded")
        return self

    @property
    def photo_derivable(self) -> bool:
        return self.kind != "C"

    def material_allowed(self, label: str, reported: Sequence[str]) -> bool:
        """Whether the materials reported for ``label`` satisfy the rule: no constraint on
        the label, or an allowed token among the tokens of any reported material."""
        allowed = self.materials.get(label)
        if allowed is None:
            return True
        tokens = {t for m in reported for t in re.split(r"[^a-z0-9]+", m.lower()) if t}
        return bool(tokens & set(allowed))

    @property
    def vocabulary_gap(self) -> bool:
        return self.photo_derivable and not self.labels

    def room_allowed(self, room_type: str | None) -> bool:
        """Whether a room of ``room_type`` can evidence this row (unknown type: only when
        the rule has no room filter)."""
        if self.room_types is None and not self.exclude_room_types:
            return True
        if room_type is None:
            return False
        if self.room_types is not None and room_type not in self.room_types:
            return False
        return room_type not in self.exclude_room_types


class ReportRows(_Model):
    schema_version: int = 1
    rows: list[ReportRow]

    @model_validator(mode="after")
    def _ids_unique_and_shares_sum_to_one(self) -> ReportRows:
        seen: set[str] = set()
        for row in self.rows:
            if row.id in seen:
                raise ValueError(f"row id {row.id!r} appears twice")
            seen.add(row.id)
        total = sum(r.cost_share for r in self.rows)
        if self.rows and abs(total - 1.0) > 0.01:
            raise ValueError(f"cost shares sum to {total:.4f}, not 1")
        return self


class PhotoCountLabel(_Model):
    """True object counts of one photo, per class, from its verified boxes."""

    file: str
    document: str = "package"
    split: Split = "dev"
    counts: dict[str, int]


class PhotoCounts(_Model):
    """Derived from ``detection_boxes``: per photo, the number of non-group boxes of every
    verified class (zero when verified absent; a photo without a ``verified`` list has one
    class per boxed label and no zeros). Door classes are pooled: ``door``,
    ``interior_door`` and ``exterior_door`` become one ``door`` class counting all three,
    scored against the same union of predictions (the model's interior/exterior split is
    not what the count measures). Scores a per-photo model's counts
    (``evals.predict_photos``) against the same yardstick as the detector."""

    schema_version: int = 1
    photos: list[PhotoCountLabel]


DOORS: Final = frozenset({"door", "interior_door", "exterior_door"})


def photo_counts(boxes: DetectionBoxes) -> PhotoCounts:
    photos = []
    for image in boxes.images:
        labels = (
            list(image.verified)
            if image.verified is not None
            else sorted({b.label for b in image.boxes})
        )
        # One "door" class: the verified list names the box labels (interior_door,
        # exterior_door) so the detection scorer keeps them, while the count is pooled.
        classes = sorted({"door" if label in DOORS else label for label in labels})
        counts = {
            label: sum(
                1
                for b in image.boxes
                if not b.group_of and (b.label == label or (label == "door" and b.label in DOORS))
            )
            for label in classes
        }
        photos.append(
            PhotoCountLabel(
                file=image.file, document=image.document, split=image.split, counts=counts
            )
        )
    return PhotoCounts(photos=photos)


Dataset = (
    AppraisalFields
    | PhotoRooms
    | PhotoComponentsSet
    | Assets
    | RetrievalQrels
    | RoomInventorySet
    | DetectionBoxes
    | ReportRows
    | RuleCitations
    | PhotoCounts
)
MODELS: Final[dict[str, type[BaseModel]]] = {
    "appraisal_fields": AppraisalFields,
    "photo_rooms": PhotoRooms,
    "photo_components": PhotoComponentsSet,
    "assets": Assets,
    "retrieval_qrels": RetrievalQrels,
    "room_inventory": RoomInventorySet,
    "detection_boxes": DetectionBoxes,
    "report_rows": ReportRows,
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
    """Every dataset file present in the data directory (missing ones are skipped), plus
    the derived datasets."""
    root = data_dir(directory)
    found: dict[str, BaseModel] = {
        name: _read(root / filename, MODELS[name])
        for name, filename in DATASET_FILES.items()
        if (root / filename).exists()
    }
    found["rule_citations"] = rule_citations()
    if "detection_boxes" in found:
        found["photo_counts"] = photo_counts(found["detection_boxes"])  # type: ignore[arg-type]
    return found


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
    """The six datasets built from the synthetic fixtures (all ``dev``), plus the derived
    ``rule_citations`` (scored against the synthetic corpus on a dry run)."""
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
        "room_inventory": synthetic_room_inventory(labels),
        "report_rows": synthetic_report_rows(assets),
        "rule_citations": rule_citations(),
    }


def synthetic_room_inventory(labels: dict[str, Any]) -> RoomInventorySet:
    """Every fixture photo is its own room (the fixtures are eight different scenes), with
    the photo's labelled components as the room's objects."""
    return RoomInventorySet(
        rooms=[
            RoomInventoryLabel(
                room_id=name_hint(p["file"]).room_id,
                name=display_name(p["file"]),
                floor=p["floor"],
                room_type=p["room_type"],
                photos=[p["file"]],
                document="synthetic",
                objects=[
                    InventoryObjectLabel(
                        label=c["label"], count=c.get("count", 1), photos=[p["file"]]
                    )
                    for c in p["components"]
                ],
            )
            for p in labels["photos"]
        ]
    )


def synthetic_report_rows(assets: Assets) -> ReportRows:
    """A report checklist for the synthetic study: one kind A row per synthetic asset
    (evidenced by its own label; section 1245 rows are 5-year, 1250 rows 27.5-year
    residential), one vocabulary-gap row and one row photos cannot establish, with equal
    cost shares."""
    rows = [
        ReportRow(
            id=a.id,
            system=a.component.replace("_", " ").capitalize(),
            bucket="y5" if a.section == "1245" else "y27_5",
            cost_share=0.0,
            kind="A",
            labels=[a.component],
            count_required=True,
            document=a.document,
        )
        for a in assets.assets
    ]
    rows += [
        ReportRow(
            id="B-1:shed",
            system="Movable shed",
            bucket="y7",
            cost_share=0.0,
            kind="A",
            missing_label="shed",
            document="SYN_PUB_B",
        ),
        ReportRow(
            id="B-1:foundation",
            system="Foundations",
            bucket="y39",
            cost_share=0.0,
            kind="C",
            document="SYN_PUB_B",
        ),
    ]
    share = 1.0 / len(rows)
    return ReportRows(rows=[r.model_copy(update={"cost_share": share}) for r in rows])


def corpus_pdfs(directory: Path) -> dict[str, Path]:
    """``{doc_id: pdf}`` for the reference PDFs in ``directory`` (doc id = file stem)."""
    return {p.stem: p for p in sorted(directory.glob("*.pdf"))}
