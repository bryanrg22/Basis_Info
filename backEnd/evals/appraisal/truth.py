"""Truth files for the appraisal extraction evaluation (format: ``SPEC.md`` section 3).

One file per set under ``BASIS_EVAL_DATA_DIR/appraisal/truth/<set>.json``. Every catalogue
key (:data:`basis.extraction.field_mappings.FIELDS`) has an entry per document, so a value
the pipeline invents for a box the document does not carry is visible (status ``absent`` or
``not_developed``). Keys outside the catalogue are allowed with ``extra: true``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from basis.extraction.field_mappings import FIELDS
from evals.datasets import Split, data_dir

SCHEMA_VERSION: Final = 2
CATALOGUE_KEYS: Final = frozenset(spec.key for spec in FIELDS)
SETS: Final = ("client", "client_uad36", "gse_d1", "synthetic_1004")

Status = Literal["present", "absent", "not_developed", "unscorable"]
TruthType = Literal[
    "text",
    "int",
    "float",
    "currency",
    "percent",
    "date",
    "month",
    "bool",
    "enum",
    "enum_multi",
    "area_sqft",
    "baths",
]
Tier = Literal["critical", "high", "medium", "low"]
Source = Literal["client", "gse_d1", "synthetic"]
Form = Literal["urar_1004_2005", "uad36_urar"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldTruth(_Model):
    key: str
    status: Status
    type: TruthType | None = None
    value: Any = None
    printed: str | None = None
    page: int | None = Field(default=None, ge=1)
    also_on: tuple[int, ...] = ()
    quote: str | None = None
    verified: bool = True
    tier: Tier | None = None
    tolerance: float | None = Field(default=None, ge=0)
    extra: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> FieldTruth:
        if self.extra == (self.key in CATALOGUE_KEYS):
            raise ValueError(
                f"{self.key}: extra must be true exactly when the key is not in the catalogue"
            )
        if self.status == "present":
            if self.type is None or self.value is None:
                raise ValueError(f"{self.key}: a present field needs a type and a value")
        elif self.value is not None:
            raise ValueError(f"{self.key}: only a present field carries a value")
        if self.page is not None and self.page in self.also_on:
            raise ValueError(f"{self.key}: also_on repeats the page")
        return self


class ExpectedIssue(_Model):
    rule: str
    keys: tuple[str, ...] = ()
    note: str | None = None


class DocumentTruth(_Model):
    document: str
    source: Source
    form: Form
    split: Split = "dev"
    pdf: str  # relative to BASIS_EVAL_DATA_DIR/appraisal/
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pages: int = Field(ge=1)
    fields: tuple[FieldTruth, ...]
    expected_issues: tuple[ExpectedIssue, ...] = ()
    note: str | None = None

    @model_validator(mode="after")
    def _every_catalogue_key_once(self) -> DocumentTruth:
        keys = [f.key for f in self.fields]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ValueError(f"{self.document}: duplicate keys {duplicates}")
        missing = sorted(CATALOGUE_KEYS - set(keys))
        if missing:
            raise ValueError(f"{self.document}: no truth for {missing}")
        for f in self.fields:
            for page in (f.page, *f.also_on):
                if page is not None and page > self.pages:
                    raise ValueError(f"{self.document}: {f.key} page {page} > {self.pages}")
        return self

    def field(self, key: str) -> FieldTruth:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)


class TruthSet(_Model):
    schema_version: Literal[2] = SCHEMA_VERSION
    set: str
    documents: tuple[DocumentTruth, ...]

    @model_validator(mode="after")
    def _unique_documents(self) -> TruthSet:
        names = [d.document for d in self.documents]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.set}: duplicate document names")
        return self


def appraisal_dir(explicit: str | Path | None = None) -> Path:
    return data_dir(explicit) / "appraisal"


def load_truth(name: str, directory: str | Path | None = None) -> TruthSet:
    path = appraisal_dir(directory) / "truth" / f"{name}.json"
    return TruthSet.model_validate(json.loads(path.read_text()))


def dump_truth(truth: TruthSet, path: Path) -> None:
    """Write a truth file deterministically (sorted keys within each document)."""
    data = truth.model_dump(mode="json", exclude_defaults=False)
    for doc in data["documents"]:
        doc["fields"].sort(key=lambda f: f["key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
