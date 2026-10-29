"""``python -m evals.e2e_report``: a completed study's export against the engineer's cost
segregation report, row by row, bucket by bucket, and in dollars (EVAL_REPORT section 34).

Inputs (formats in ``evals/README.md``):

* the study export the pipeline writes at completion (``rev-<n>.json`` of
  :mod:`basis.activities.export_report`, or its ``rev-<n>.assets.csv``): every asset with
  label, room, material, bucket, quantity, cost and citations (:func:`load_export`);
* the row assignment (``e2e_rows_v1.json``, private): for each engineer row the pipeline
  labels, with room and material conditions, that are charged to it, and the row's kind;
  plus the vocabulary labels that land in no row. Unlike the ``report_rows`` evidence
  rules (any-of, many-to-many), an assignment charges every asset to **at most one** row:
  the loader checks that every vocabulary label is assigned or listed as unassigned, and
  the scorer refuses an export in which one asset matches two rows (:class:`E2ERows`);
* the engineer's report (``engineer_report_v1.json``, private): the rows with bucket and
  cost, the building basis B and closing costs CC, and, when printed, the replacement-cost
  total and the building-systems page (:class:`EngineerReport`).

Per engineer row: evidenced (any assigned asset) or missed; the pipeline's bucket by
asset count and by dollars (both reported; the count majority is the headline, ties by
dollars then by recovery period); bucket right; pipeline dollars (the assigned priced
assets' cost estimates) against the engineer's; the row's share of each side's own total.
Kind C rows (concealed: foundations, framing, utilities, ...) are *not produced* and never
a wrong bucket; assets charged to them are hints and count in the pipeline's totals only.
A kind A or B row with no assignable label is a *vocabulary gap*, not a miss.

What is and is not like-for-like. The bucket comparison is: the engineer's row has one
bucket and the pipeline's assets have one each. The dollar comparison is **not**: the
engineer's row cost is a pro-rata share of the purchase-price basis (replacement cost
new x one factor), the pipeline's is a replacement cost on today's takeoff. Until a basis
step scales the pipeline's rows, the raw dollars are reported as replacement cost against
allocated basis, and the comparable number is the share: each row (and bucket) as a
percentage of its own side's total. The deterministic tail (:mod:`evals.report_math`) is
also run on the pipeline's rows with the engineer's B and CC: what the pipeline's report
would print today. With the engineer's printed replacement-cost total the tail is checked
against the engineer's own numbers (rows, buckets, percentages, allocation block, systems
page) to the cent.

Where each quantity came from (EVAL_REPORT sections 35 and 37): every asset's
``takeoff_inputs`` name the source of its area or quantity (``gla_share``,
``appraisal_derived``, ``typical_for_room_type``, ...), tallied per row as ``sources``; an
export written since section 37 also carries ``summary.takeoff`` (the cost year and
location factor with their sources, the property class with its source), printed under
"Takeoff inputs". Neither changes a score: they say what the numbers rest on.

Usage (from ``backEnd/``)::

    python -m evals.e2e_report --export PATH/rev-3.json --out REPORT.md [--json OUT.json] \\
        [--rows LABELS/e2e_rows_v1.json] [--report LABELS/engineer_report_v1.json] [--data-free]

``--rows`` and ``--report`` default to ``$BASIS_EVAL_DATA_DIR/labels/``. ``--data-free``
writes the summary and tables without the engineer's dollars, shares or ratios (rates,
counts and share errors only), for the public report.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from basis.vision.schemas import COMPONENT_LABELS, ROOM_TYPES
from evals.datasets import ENV_VAR, data_dir
from evals.metrics import wilson_interval
from evals.report_math import (
    ZERO,
    Reproduction,
    Schedule,
    SystemLine,
    build_schedule,
    cents,
    check_reproduction,
    implied_rcn,
    ordered_buckets,
    systems_page,
)

Bucket = Literal["y5", "y7", "y15", "y27_5", "y39"]
RowKind = Literal["A", "B", "C"]
Outcome = Literal["evidenced", "missed", "vocabulary_gap", "not_produced"]
EXPORT_FORMATS: Final = frozenset({"basis-export/2", "basis-evidence/1"})
LABELS_DIR: Final = "labels"
ROWS_FILE: Final = "e2e_rows_v1.json"
REPORT_FILE: Final = "engineer_report_v1.json"
BUILDING_BUCKETS: Final = frozenset({"y27_5", "y39"})
_TOKEN: Final = re.compile(r"[^a-z0-9]+")


class E2EError(RuntimeError):
    pass


# ------------------------------------------------------------------ the study export


class ExportAsset(BaseModel):
    """One asset of the export, the fields the scorer reads (others are ignored)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    label: str | None = None
    material: str | None = None
    room_name: str | None = None
    room_type: str | None = None
    bucket: str
    method: str = ""
    needs_review: bool = False
    review_reason: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    total: Decimal | None = None
    takeoff_inputs: Any = None
    citation_count: int = 0

    @property
    def priced(self) -> bool:
        return self.total is not None

    @property
    def source(self) -> str:
        """Where the quantity came from: the takeoff's ``quantity_source`` (a property-level
        quantity derived from the appraisal), else its ``room_area_source`` (the room's
        area: a GLA share, a typical area, ...), else ``none`` (no takeoff inputs)."""
        inputs = self.takeoff_inputs if isinstance(self.takeoff_inputs, dict) else {}
        return str(inputs.get("quantity_source") or inputs.get("room_area_source") or "none")

    @property
    def unpriced_reason(self) -> str:
        """The first review reason (the cost step writes its code first), or ``unpriced``."""
        first = (self.review_reason or "").split(";")[0].strip()
        return first or "unpriced"


class StudyExport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    study_id: str
    revision: int
    property_name: str | None = None
    assets: list[ExportAsset]
    # ``summary.takeoff`` of an export written since EVAL_REPORT section 37; None before.
    takeoff: dict[str, Any] | None = None

    @property
    def total_priced(self) -> Decimal:
        return sum((a.total for a in self.assets if a.total is not None), ZERO)


def _asset_from_json(row: Mapping[str, Any]) -> ExportAsset:
    citations = row.get("citations")
    count = len(citations) if isinstance(citations, list) else int(row.get("citation_count", 0))
    return ExportAsset.model_validate({**row, "citation_count": count})


def _asset_from_csv(row: Mapping[str, str]) -> ExportAsset:
    def opt(key: str) -> str | None:
        value = row.get(key, "")
        return value if value else None

    return ExportAsset(
        id=row["asset_id"],
        label=opt("label"),
        material=opt("material"),
        room_name=opt("room"),
        room_type=opt("room_type"),
        bucket=row["bucket"],
        method=row.get("method", ""),
        needs_review=row.get("needs_review", "").lower() == "true",
        review_reason=opt("review_reason"),
        quantity=Decimal(row["quantity"]) if opt("quantity") else None,
        unit=opt("unit"),
        total=Decimal(row["total"]) if opt("total") else None,
        citation_count=int(row.get("citation_count") or 0),
    )


def load_export(path: Path) -> StudyExport:
    """The export at ``path``: the JSON document (``basis-export/2``, or a bare evidence
    pack ``basis-evidence/1``) or the assets CSV next to it."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        stem = path.name
        study_id = path.parent.name
        revision = int(m.group(1)) if (m := re.match(r"rev-(\d+)", stem)) else 0
        return StudyExport(
            study_id=study_id, revision=revision, assets=[_asset_from_csv(r) for r in rows]
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    fmt = document.get("format")
    if fmt not in EXPORT_FORMATS:
        raise E2EError(f"{path}: not a study export (format {fmt!r})")
    return StudyExport(
        study_id=str(document["study_id"]),
        revision=int(document["revision"]),
        property_name=document.get("property_name"),
        assets=[_asset_from_json(a) for a in document["assets"]],
        takeoff=_takeoff_block(document.get("summary")),
    )


def _takeoff_block(summary: Any) -> dict[str, Any] | None:
    block = summary.get("takeoff") if isinstance(summary, dict) else None
    return block if isinstance(block, dict) else None


# ------------------------------------------------------------------ the row assignment


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _tokens(material: str | None) -> set[str]:
    return {t for t in _TOKEN.split((material or "").lower()) if t}


class Assignment(_Model):
    """One pipeline label charged to a row, under optional conditions. ``room_types`` /
    ``materials`` are allow-lists that reject an unknown room or material;
    ``exclude_room_types`` / ``exclude_materials`` are deny-lists that accept it, so a
    label split between two rows by complementary lists is charged exactly once."""

    label: str
    room_types: list[str] | None = None
    exclude_room_types: list[str] = Field(default_factory=list)
    materials: list[str] | None = None  # material tokens, any-of
    exclude_materials: list[str] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _label_in_vocabulary(cls, value: str) -> str:
        if value == "other" or value not in COMPONENT_LABELS:
            raise ValueError(f"{value!r} is not a component label (or is 'other')")
        return value

    @field_validator("room_types", "exclude_room_types")
    @classmethod
    def _room_types_known(cls, value: list[str] | None) -> list[str] | None:
        for room_type in value or []:
            if room_type not in ROOM_TYPES:
                raise ValueError(f"{room_type!r} is not a room type")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Assignment:
        if self.room_types is not None and not self.room_types:
            raise ValueError(f"{self.label}: room_types must be null or non-empty")
        if self.materials is not None and not self.materials:
            raise ValueError(f"{self.label}: materials must be null or non-empty")
        if set(self.room_types or []) & set(self.exclude_room_types):
            raise ValueError(f"{self.label}: a room type is both required and excluded")
        if set(self.materials or []) & set(self.exclude_materials):
            raise ValueError(f"{self.label}: a material is both required and excluded")
        return self

    def accepts(self, asset: ExportAsset) -> bool:
        if asset.label != self.label:
            return False
        if self.room_types is not None and asset.room_type not in self.room_types:
            return False
        if asset.room_type is not None and asset.room_type in self.exclude_room_types:
            return False
        tokens = _tokens(asset.material)
        if self.materials is not None and not (tokens & set(self.materials)):
            return False
        return not (tokens & set(self.exclude_materials))


class E2ERow(_Model):
    """One engineer row: its kind and the assignments that charge pipeline assets to it.
    A kind C row may carry assignments (hints charged to it for the totals); it is never
    scored. A kind A or B row with no assignment is a vocabulary gap."""

    id: str
    system: str
    kind: RowKind
    assign: list[Assignment] = Field(default_factory=list)
    note: str | None = None

    @property
    def labels(self) -> list[str]:
        return sorted({a.label for a in self.assign})

    @property
    def photo_derivable(self) -> bool:
        return self.kind != "C"

    @property
    def vocabulary_gap(self) -> bool:
        return self.photo_derivable and not self.assign

    def accepts(self, asset: ExportAsset) -> bool:
        return any(a.accepts(asset) for a in self.assign)


class UnassignedLabel(_Model):
    label: str
    reason: str

    @field_validator("label")
    @classmethod
    def _label_known(cls, value: str) -> str:
        if value not in COMPONENT_LABELS:
            raise ValueError(f"{value!r} is not a component label")
        return value


class E2ERows(_Model):
    schema_version: int = 1
    note: str | None = None
    rows: list[E2ERow]
    unassigned: list[UnassignedLabel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _complete_and_unique(self) -> E2ERows:
        seen: set[str] = set()
        for row in self.rows:
            if row.id in seen:
                raise ValueError(f"row id {row.id!r} appears twice")
            seen.add(row.id)
        assigned = {a.label for r in self.rows for a in r.assign}
        unassigned = [u.label for u in self.unassigned]
        if len(set(unassigned)) != len(unassigned):
            raise ValueError("an unassigned label repeats")
        both = sorted(assigned & set(unassigned))
        if both:
            raise ValueError(f"labels both assigned and unassigned: {both}")
        missing = sorted(set(COMPONENT_LABELS) - assigned - set(unassigned))
        if missing:
            raise ValueError(f"vocabulary labels neither assigned nor unassigned: {missing}")
        return self

    @property
    def unassigned_reasons(self) -> dict[str, str]:
        return {u.label: u.reason for u in self.unassigned}

    def row(self, row_id: str) -> E2ERow:
        return next(r for r in self.rows if r.id == row_id)


@dataclass(frozen=True)
class AssignmentResult:
    """Every asset charged to one row, or to nothing with the reason."""

    by_row: dict[str, list[ExportAsset]]  # row id -> assets, rows in label order
    unassigned_listed: dict[str, list[ExportAsset]]  # label -> assets (label unassigned)
    unassigned_no_rule: dict[str, list[ExportAsset]]  # label -> assets (no condition met)

    @property
    def assigned(self) -> list[ExportAsset]:
        return [a for assets in self.by_row.values() for a in assets]

    @property
    def unassigned(self) -> list[ExportAsset]:
        return [
            a
            for d in (self.unassigned_listed, self.unassigned_no_rule)
            for v in d.values()
            for a in v
        ]


def assign_assets(rows: E2ERows, assets: Iterable[ExportAsset]) -> AssignmentResult:
    """Charge each asset to the one row that accepts it. Raises :class:`E2EError` naming
    every asset two rows accept (the label file must keep rows disjoint)."""
    by_row: dict[str, list[ExportAsset]] = {r.id: [] for r in rows.rows}
    listed: dict[str, list[ExportAsset]] = {}
    no_rule: dict[str, list[ExportAsset]] = {}
    reasons = rows.unassigned_reasons
    conflicts: list[str] = []
    for asset in assets:
        takers = [r for r in rows.rows if r.accepts(asset)]
        if len(takers) > 1:
            conflicts.append(
                f"asset {asset.id} ({asset.label}, {asset.room_type}, {asset.material}): "
                + ", ".join(r.id for r in takers)
            )
            continue
        if takers:
            by_row[takers[0].id].append(asset)
        elif asset.label in reasons or asset.label is None:
            listed.setdefault(asset.label or "other", []).append(asset)
        else:
            no_rule.setdefault(asset.label, []).append(asset)
    if conflicts:
        raise E2EError("assets accepted by more than one row:\n" + "\n".join(conflicts))
    return AssignmentResult(by_row, listed, no_rule)


# ------------------------------------------------------------------ the engineer's report


class EngineerRow(_Model):
    id: str
    system: str
    group: str | None = None
    bucket: Bucket
    cost: Decimal = Field(ge=0)


class EngineerSystem(_Model):
    """One line of the building-systems page: the rows it sums, by share."""

    name: str
    rows: dict[str, Decimal]
    depreciable_cost: Decimal
    replacement_cost: Decimal | None = None


class EngineerReport(_Model):
    schema_version: int = 1
    note: str | None = None
    property_class: Literal["residential_rental", "nonresidential"] = "nonresidential"
    building_basis: Decimal = Field(ge=0)
    closing_costs: Decimal = Field(ge=0)
    replacement_cost_total: Decimal | None = None
    rows: list[EngineerRow]
    systems: list[EngineerSystem] = Field(default_factory=list)
    # Printed on the summary pages, when given: bucket -> percent of the total (tenths),
    # and the pro-rata allocation block (bucket -> dollars) for the basis and the closing
    # costs. Checked by the reproduction, not used to score.
    bucket_percent: dict[str, Decimal] = Field(default_factory=dict)
    allocation_building: dict[str, Decimal] = Field(default_factory=dict)
    allocation_closing: dict[str, Decimal] = Field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return cents(self.building_basis + self.closing_costs)

    @property
    def building_bucket(self) -> str:
        return "y39" if self.property_class == "nonresidential" else "y27_5"

    @property
    def costs(self) -> dict[str, Decimal]:
        return {r.id: r.cost for r in self.rows}

    @property
    def buckets(self) -> dict[str, str]:
        return {r.id: r.bucket for r in self.rows}

    @property
    def bucket_totals(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for r in self.rows:
            totals[r.bucket] = totals.get(r.bucket, ZERO) + r.cost
        return {b: totals[b] for b in ordered_buckets(totals)}

    def row(self, row_id: str) -> EngineerRow:
        return next(r for r in self.rows if r.id == row_id)

    @model_validator(mode="after")
    def _reconciles(self) -> EngineerReport:
        seen: set[str] = set()
        for row in self.rows:
            if row.id in seen:
                raise ValueError(f"row id {row.id!r} appears twice")
            seen.add(row.id)
        if not self.rows:
            raise ValueError("no rows")
        if sum((r.cost for r in self.rows), ZERO) != self.total:
            raise ValueError(
                f"rows sum to {sum((r.cost for r in self.rows), ZERO)}, not basis + closing "
                f"costs {self.total}"
            )
        foreign = {b for b in BUILDING_BUCKETS if b != self.building_bucket}
        if any(r.bucket in foreign for r in self.rows):
            raise ValueError(f"a {self.property_class} report cannot have {sorted(foreign)} rows")
        if self.systems:
            charged: dict[str, Decimal] = {}
            for system in self.systems:
                for row_id, share in system.rows.items():
                    if row_id not in seen:
                        raise ValueError(f"system {system.name!r} names unknown row {row_id!r}")
                    charged[row_id] = charged.get(row_id, ZERO) + share
            bad = sorted(k for k in seen if charged.get(k, ZERO) != 1)
            if bad:
                raise ValueError(f"rows not charged to systems with shares summing to 1: {bad}")
            dep = sum((s.depreciable_cost for s in self.systems), ZERO)
            if dep != self.total:
                raise ValueError(f"systems' depreciable costs sum to {dep}, not {self.total}")
        buckets = set(self.bucket_totals)
        for name, printed in (
            ("bucket_percent", self.bucket_percent),
            ("allocation_building", self.allocation_building),
            ("allocation_closing", self.allocation_closing),
        ):
            unknown = sorted(set(printed) - buckets)
            if unknown:
                raise ValueError(f"{name} names buckets with no rows: {unknown}")
        if self.allocation_building and sum(self.allocation_building.values(), ZERO) != (
            self.building_basis
        ):
            raise ValueError("the printed building allocation does not sum to the basis")
        if self.allocation_closing and sum(self.allocation_closing.values(), ZERO) != (
            self.closing_costs
        ):
            raise ValueError(
                "the printed closing-cost allocation does not sum to the closing costs"
            )
        return self


def load_rows(path: Path) -> E2ERows:
    return E2ERows.model_validate_json(path.read_text(encoding="utf-8"))


def load_report(path: Path) -> EngineerReport:
    return EngineerReport.model_validate_json(path.read_text(encoding="utf-8"))


def default_labels_path(name: str, directory: str | Path | None = None) -> Path:
    return data_dir(directory) / LABELS_DIR / name


# ------------------------------------------------------------------ scoring


def normalize_bucket(bucket: str, building_bucket: str) -> str:
    """The pipeline's building bucket (27.5 or 39 years by the deployment's property
    class) compared as the engineer's: the only difference the class makes is the period
    of the building rows (``basis.domain.rules.bucket_for``)."""
    return building_bucket if bucket in BUILDING_BUCKETS else bucket


def majority_bucket(
    assets: Sequence[ExportAsset], building_bucket: str
) -> tuple[str | None, str | None, dict[str, int], dict[str, Decimal]]:
    """``(by count, by dollars, count tally, dollar tally)``: the bucket most assets carry
    (ties: more dollars, then the shorter recovery period) and the bucket carrying the most
    priced dollars (ties: more assets, then the shorter period); ``None`` without assets,
    and ``None`` by dollars when no asset is priced."""
    counts: Counter[str] = Counter()
    dollars: dict[str, Decimal] = {}
    for a in assets:
        b = normalize_bucket(a.bucket, building_bucket)
        counts[b] += 1
        dollars.setdefault(b, ZERO)
        if a.total is not None:
            dollars[b] += a.total
    if not counts:
        return None, None, {}, {}
    order = {b: i for i, b in enumerate(ordered_buckets(counts))}
    by_count = max(counts, key=lambda b: (counts[b], dollars[b], -order[b]))
    by_dollars = (
        max(dollars, key=lambda b: (dollars[b], counts[b], -order[b]))
        if any(a.total is not None for a in assets)
        else None
    )
    return by_count, by_dollars, dict(counts), dollars


@dataclass
class RowScore:
    row: E2ERow
    engineer: EngineerRow
    outcome: Outcome
    assets: list[ExportAsset] = field(default_factory=list)
    bucket_by_count: str | None = None
    bucket_by_dollars: str | None = None
    tally_count: dict[str, int] = field(default_factory=dict)
    tally_dollars: dict[str, Decimal] = field(default_factory=dict)
    pipeline_cost: Decimal = ZERO
    pipeline_share: Decimal = ZERO  # of the pipeline's priced total
    engineer_share: Decimal = ZERO  # of the engineer's total

    @property
    def scored(self) -> bool:
        """Scored for its bucket: a photo-derivable row with assigned assets."""
        return self.outcome == "evidenced"

    @property
    def priced(self) -> int:
        return sum(a.priced for a in self.assets)

    @property
    def unpriced(self) -> int:
        return len(self.assets) - self.priced

    @property
    def unpriced_reasons(self) -> dict[str, int]:
        return dict(Counter(a.unpriced_reason for a in self.assets if not a.priced))

    @property
    def labels(self) -> dict[str, int]:
        return dict(Counter(a.label or "other" for a in self.assets))

    @property
    def sources(self) -> dict[str, int]:
        """The assigned assets by where their quantity came from (:attr:`ExportAsset.source`)."""
        return dict(sorted(Counter(a.source for a in self.assets).items()))

    @property
    def bucket_right_by_count(self) -> bool | None:
        if not self.scored:
            return None
        return self.bucket_by_count == self.engineer.bucket

    @property
    def bucket_right_by_dollars(self) -> bool | None:
        if not self.scored or self.bucket_by_dollars is None:
            return None
        return self.bucket_by_dollars == self.engineer.bucket

    @property
    def cost_ratio(self) -> Decimal | None:
        """Pipeline dollars over engineer dollars (replacement cost over allocated basis:
        not like-for-like; see the module docstring)."""
        if not self.scored or self.engineer.cost == 0:
            return None
        return self.pipeline_cost / self.engineer.cost

    @property
    def cost_error(self) -> Decimal:
        return abs(self.pipeline_cost - self.engineer.cost)

    @property
    def share_error(self) -> Decimal:
        return abs(self.pipeline_share - self.engineer_share)


@dataclass(frozen=True)
class BucketScore:
    bucket: str
    engineer_cost: Decimal
    engineer_share: Decimal
    pipeline_cost: Decimal  # all priced assets of the export in this bucket
    pipeline_share: Decimal
    pipeline_assets: int
    pipeline_unpriced: int
    engineer_rows: int
    rows_scored: int
    rows_right: int

    @property
    def share_error(self) -> Decimal:
        return abs(self.pipeline_share - self.engineer_share)


@dataclass
class E2EScore:
    export: StudyExport
    labels: E2ERows
    report: EngineerReport
    rows: list[RowScore]
    buckets: list[BucketScore]
    assignment: AssignmentResult
    pipeline_schedule: Schedule | None  # the pipeline's rows through the tail with B and CC
    engineer_schedule: Schedule | None  # the engineer's implied RCNs through the tail
    engineer_systems: list[SystemLine]
    reproduction: Reproduction | None
    summary: dict[str, Any]


def _share(part: Decimal, whole: Decimal) -> Decimal:
    return part / whole if whole else ZERO


def _pct(value: Decimal) -> Decimal:
    return (value * 100).quantize(Decimal("0.01"))


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else math.nan


def score(export: StudyExport, rows: E2ERows, report: EngineerReport) -> E2EScore:
    """Score one export. Row ids of the assignment and the report must agree."""
    if {r.id for r in rows.rows} != {r.id for r in report.rows}:
        diff = sorted({r.id for r in rows.rows} ^ {r.id for r in report.rows})
        raise E2EError(f"row ids of the assignment and the report differ: {diff}")
    building = report.building_bucket
    assignment = assign_assets(rows, export.assets)
    pipeline_total = export.total_priced
    engineer_total = report.total

    row_scores: list[RowScore] = []
    for engineer_row in report.rows:
        row = rows.row(engineer_row.id)
        assets = assignment.by_row[row.id]
        if not row.photo_derivable:
            outcome: Outcome = "not_produced"
        elif row.vocabulary_gap:
            outcome = "vocabulary_gap"
        elif assets:
            outcome = "evidenced"
        else:
            outcome = "missed"
        by_count, by_dollars, tally_c, tally_d = majority_bucket(assets, building)
        cost = sum((a.total for a in assets if a.total is not None), ZERO)
        row_scores.append(
            RowScore(
                row=row,
                engineer=engineer_row,
                outcome=outcome,
                assets=assets,
                bucket_by_count=by_count,
                bucket_by_dollars=by_dollars,
                tally_count=tally_c,
                tally_dollars=tally_d,
                pipeline_cost=cost,
                pipeline_share=_share(cost, pipeline_total),
                engineer_share=_share(engineer_row.cost, engineer_total),
            )
        )

    # Buckets: the pipeline's dollars per bucket are every priced asset of the export
    # (assigned or not), which is what the pipeline's own summary prints.
    pipe_cost: dict[str, Decimal] = {}
    pipe_n: Counter[str] = Counter()
    pipe_unpriced: Counter[str] = Counter()
    for a in export.assets:
        b = normalize_bucket(a.bucket, building)
        pipe_n[b] += 1
        pipe_cost.setdefault(b, ZERO)
        if a.total is None:
            pipe_unpriced[b] += 1
        else:
            pipe_cost[b] += a.total
    engineer_buckets = report.bucket_totals
    bucket_scores = [
        BucketScore(
            bucket=b,
            engineer_cost=engineer_buckets.get(b, ZERO),
            engineer_share=_share(engineer_buckets.get(b, ZERO), engineer_total),
            pipeline_cost=pipe_cost.get(b, ZERO),
            pipeline_share=_share(pipe_cost.get(b, ZERO), pipeline_total),
            pipeline_assets=pipe_n.get(b, 0),
            pipeline_unpriced=pipe_unpriced.get(b, 0),
            engineer_rows=sum(r.engineer.bucket == b for r in row_scores),
            rows_scored=sum(r.scored and r.engineer.bucket == b for r in row_scores),
            rows_right=sum(
                bool(r.bucket_right_by_count) and r.engineer.bucket == b for r in row_scores
            ),
        )
        for b in ordered_buckets({*engineer_buckets, *pipe_cost})
    ]

    # The deterministic tail on the pipeline's rows: every row's assigned dollars as its
    # RCN, scaled to the engineer's basis (what a basis step would print today).
    pipeline_rcn = {r.row.id: r.pipeline_cost for r in row_scores}
    pipeline_schedule = (
        build_schedule(
            pipeline_rcn,
            report.buckets,
            report.building_basis,
            report.closing_costs,
            absorb_residual=True,
        )
        if any(pipeline_rcn.values())
        else None
    )
    engineer_schedule = None
    systems: list[SystemLine] = []
    reproduction = None
    if report.replacement_cost_total is not None:
        rcn = implied_rcn(report.costs, report.replacement_cost_total, engineer_total)
        engineer_schedule = build_schedule(
            rcn, report.buckets, report.building_basis, report.closing_costs
        )
        printed_systems = None
        if report.systems:
            systems = systems_page(
                engineer_schedule, rcn, {s.name: dict(s.rows) for s in report.systems}
            )
            printed_systems = {
                s.name: (s.depreciable_cost, s.replacement_cost)
                for s in report.systems
                if s.replacement_cost is not None
            }
        reproduction = check_reproduction(
            engineer_schedule,
            report.costs,
            printed_bucket_totals=engineer_buckets,
            printed_bucket_percent=report.bucket_percent,
            printed_allocation_building=report.allocation_building,
            printed_allocation_closing=report.allocation_closing,
            printed_total=engineer_total,
            systems=systems,
            printed_systems=printed_systems,
        )

    summary = summarize(export, report, row_scores, bucket_scores, assignment, reproduction)
    return E2EScore(
        export=export,
        labels=rows,
        report=report,
        rows=row_scores,
        buckets=bucket_scores,
        assignment=assignment,
        pipeline_schedule=pipeline_schedule,
        engineer_schedule=engineer_schedule,
        engineer_systems=systems,
        reproduction=reproduction,
        summary=summary,
    )


def summarize(
    export: StudyExport,
    report: EngineerReport,
    rows: Sequence[RowScore],
    buckets: Sequence[BucketScore],
    assignment: AssignmentResult,
    reproduction: Reproduction | None,
) -> dict[str, Any]:
    """The summary block: counts, rates and errors. Keys under ``classification`` and
    ``shares`` are data-free (no client dollars); ``dollars`` holds the engineer's."""
    visible = [r for r in rows if r.row.photo_derivable]
    evidenced = [r for r in rows if r.outcome == "evidenced"]
    missed = [r for r in rows if r.outcome == "missed"]
    gaps = [r for r in rows if r.outcome == "vocabulary_gap"]
    concealed = [r for r in rows if r.outcome == "not_produced"]
    right_count = [r for r in evidenced if r.bucket_right_by_count]
    scored_dollars = [r for r in evidenced if r.bucket_right_by_dollars is not None]
    right_dollars = [r for r in scored_dollars if r.bucket_right_by_dollars]
    engineer_total = report.total

    def share(items: Sequence[RowScore]) -> float:
        return float(_share(sum((r.engineer.cost for r in items), ZERO), engineer_total))

    low, high = wilson_interval(len(right_count), len(evidenced))
    ev_low, ev_high = wilson_interval(len(evidenced), len(visible))
    kind_c_assets = [a for r in concealed for a in r.assets]
    unassigned = assignment.unassigned
    priced_rows = [r for r in evidenced if r.priced]
    ratios = sorted(r.cost_ratio for r in priced_rows if r.cost_ratio is not None)
    tvd = sum((r.share_error for r in rows), ZERO) / 2
    return {
        "export": {
            "study_id": export.study_id,
            "revision": export.revision,
            "assets": len(export.assets),
            "priced": sum(a.priced for a in export.assets),
            "unpriced": sum(not a.priced for a in export.assets),
            "building_bucket_compared_as": report.building_bucket,
            **({"takeoff": export.takeoff} if export.takeoff is not None else {}),
        },
        "assignment": {
            "assigned": len(assignment.assigned),
            "assigned_to_scored_rows": sum(len(r.assets) for r in visible),
            "assigned_to_kind_c_rows": len(kind_c_assets),
            "unassigned": len(unassigned),
            "unassigned_listed_labels": {
                k: len(v) for k, v in sorted(assignment.unassigned_listed.items())
            },
            "unassigned_no_rule_matched": {
                k: len(v) for k, v in sorted(assignment.unassigned_no_rule.items())
            },
            "unassigned_priced_dollars": str(
                cents(sum((a.total for a in unassigned if a.total is not None), ZERO))
            ),
        },
        "classification": {
            "rows": len(rows),
            "photo_visible_rows": len(visible),
            "kind_c_rows": len(concealed),
            "kind_c_engineer_share": share(concealed),
            "evidenced": len(evidenced),
            "missed": len(missed),
            "missed_rows": [r.engineer.system for r in missed],
            "vocabulary_gap": len(gaps),
            "vocabulary_gap_rows": [r.engineer.system for r in gaps],
            "evidenced_of_photo_visible": _rate(len(evidenced), len(visible)),
            "evidenced_interval": [ev_low, ev_high],
            "bucket_right_by_count": len(right_count),
            "bucket_right_of_evidenced": _rate(len(right_count), len(evidenced)),
            "bucket_right_interval": [low, high],
            "bucket_right_of_photo_visible": _rate(len(right_count), len(visible)),
            "bucket_right_of_all_rows": _rate(len(right_count), len(rows)),
            "bucket_right_by_dollars": len(right_dollars),
            "bucket_right_by_dollars_of_scored": _rate(len(right_dollars), len(scored_dollars)),
            "wrong_bucket_rows": [
                f"{r.engineer.system} ({r.engineer.bucket} vs {r.bucket_by_count})"
                for r in evidenced
                if not r.bucket_right_by_count
            ],
            "engineer_share_on_rows_bucket_right": share(right_count),
            "engineer_share_on_evidenced_rows": share(evidenced),
            "engineer_share_on_wrong_bucket_rows": share(
                [r for r in evidenced if not r.bucket_right_by_count]
            ),
            "rows_with_unpriced_assets": sum(r.unpriced > 0 for r in evidenced),
            "evidenced_rows_fully_unpriced": sum(r.priced == 0 for r in evidenced),
        },
        "shares": {
            "comparison": (
                "each row as a share of its own side's total (pipeline: all priced assets; "
                "engineer: basis + closing costs); the raw dollars are replacement cost "
                "against allocated basis and are not like-for-like"
            ),
            "mean_abs_row_share_error_pct": float(
                _pct(sum((r.share_error for r in rows), ZERO) / len(rows))
            )
            if rows
            else math.nan,
            "max_abs_row_share_error_pct": float(
                _pct(max((r.share_error for r in rows), default=ZERO))
            ),
            "total_variation_distance_pct": float(_pct(tvd)),
            "bucket_share_error_pct": {b.bucket: float(_pct(b.share_error)) for b in buckets},
            "max_bucket_share_error_pct": float(
                _pct(max((b.share_error for b in buckets), default=ZERO))
            ),
            "pipeline_bucket_share_pct": {b.bucket: float(_pct(b.pipeline_share)) for b in buckets},
        },
        "dollars": {
            "caveat": "replacement cost (pipeline) against allocated basis (engineer)",
            "pipeline_total_priced": str(cents(export.total_priced)),
            "pipeline_dollars_on_scored_rows": str(
                cents(sum((r.pipeline_cost for r in evidenced), ZERO))
            ),
            "engineer_total": str(engineer_total),
            "engineer_dollars_on_evidenced_rows": str(
                cents(sum((r.engineer.cost for r in evidenced), ZERO))
            ),
            "pipeline_over_engineer_total": float(_share(export.total_priced, engineer_total)),
            "median_row_cost_ratio": float(ratios[len(ratios) // 2]) if ratios else math.nan,
            "rows_with_a_cost_ratio": len(ratios),
            "engineer_bucket_share_pct": {b.bucket: float(_pct(b.engineer_share)) for b in buckets},
        },
        "reproduction": None
        if reproduction is None
        else {
            "compared": reproduction.compared,
            "exact": reproduction.exact,
            "deviations": len(reproduction.deviations),
            "max_abs_deviation": str(reproduction.max_abs_deviation),
            "items_off": [d.item for d in reproduction.deviations],
        },
    }


# ------------------------------------------------------------------ output


def _f(value: Decimal | float | None, places: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if math.isnan(value) else f"{value:.{places}f}"
    return f"{value:,.{places}f}"


def _p(value: Decimal) -> str:
    return f"{_pct(value)}%"


def _rate_s(value: float) -> str:
    return "" if math.isnan(value) else f"{value:.3f}"


def _kv(label: str, value: object) -> str:
    return f"| {label} | {value} |"


def _interval(pair: Sequence[float]) -> str:
    return f"[{_rate_s(pair[0])}, {_rate_s(pair[1])}]"


def _summary_lines(result: E2EScore, data_free: bool) -> list[str]:
    s = result.summary
    c, sh, d, asg = s["classification"], s["shares"], s["dollars"], s["assignment"]
    wrong = c["wrong_bucket_rows"]
    lines = [
        "| metric | value |",
        "|---|---|",
        _kv(
            "rows",
            f"{c['rows']} ({c['photo_visible_rows']} photo-visible, "
            f"{c['kind_c_rows']} concealed, kind C)",
        ),
        _kv(
            "concealed rows' share of the engineer's total",
            f"{c['kind_c_engineer_share'] * 100:.1f}% (not produced; never a wrong bucket)",
        ),
        _kv(
            "evidenced (an asset charged)",
            f"{c['evidenced']} of {c['photo_visible_rows']} photo-visible "
            f"({_rate_s(c['evidenced_of_photo_visible'])} {_interval(c['evidenced_interval'])})",
        ),
        _kv("missed", f"{c['missed']}: {', '.join(c['missed_rows']) or '-'}"),
        _kv(
            "vocabulary gap (no label charges the row)",
            f"{c['vocabulary_gap']}: {', '.join(c['vocabulary_gap_rows']) or '-'}",
        ),
        _kv(
            "bucket right, majority by count",
            f"{c['bucket_right_by_count']} of {c['evidenced']} evidenced "
            f"({_rate_s(c['bucket_right_of_evidenced'])} "
            f"{_interval(c['bucket_right_interval'])}); of {c['photo_visible_rows']} "
            f"photo-visible {_rate_s(c['bucket_right_of_photo_visible'])}; of all "
            f"{c['rows']} {_rate_s(c['bucket_right_of_all_rows'])}",
        ),
        _kv(
            "bucket right, majority by dollars",
            f"{c['bucket_right_by_dollars']} "
            f"({_rate_s(c['bucket_right_by_dollars_of_scored'])} of the evidenced rows "
            "with a priced asset)",
        ),
        _kv("wrong bucket", f"{len(wrong)}: {'; '.join(wrong) or '-'}"),
        _kv(
            "engineer's share on rows bucket-right / evidenced / wrong",
            f"{c['engineer_share_on_rows_bucket_right'] * 100:.1f}% / "
            f"{c['engineer_share_on_evidenced_rows'] * 100:.1f}% / "
            f"{c['engineer_share_on_wrong_bucket_rows'] * 100:.1f}%",
        ),
        _kv(
            "evidenced rows with an unpriced asset / with none priced",
            f"{c['rows_with_unpriced_assets']} / {c['evidenced_rows_fully_unpriced']}",
        ),
        _kv(
            "assets charged to a row / to a concealed row / to none",
            f"{asg['assigned']} / {asg['assigned_to_kind_c_rows']} / {asg['unassigned']}",
        ),
        _kv(
            "unassigned, by label",
            f"listed: {asg['unassigned_listed_labels']}; "
            f"no rule matched: {asg['unassigned_no_rule_matched']}",
        ),
        _kv("share comparison", sh["comparison"]),
        _kv(
            "mean / max abs row share error",
            f"{sh['mean_abs_row_share_error_pct']:.2f} / "
            f"{sh['max_abs_row_share_error_pct']:.2f} points",
        ),
        _kv("total variation distance (rows)", f"{sh['total_variation_distance_pct']:.1f} points"),
        _kv(
            "bucket share error (points)",
            f"{sh['bucket_share_error_pct']} (max {sh['max_bucket_share_error_pct']:.1f})",
        ),
        _kv("pipeline bucket shares", sh["pipeline_bucket_share_pct"]),
    ]
    if not data_free:
        lines += [
            _kv(
                f"dollars ({d['caveat']})",
                f"pipeline {d['pipeline_total_priced']} (on scored rows "
                f"{d['pipeline_dollars_on_scored_rows']}) vs engineer {d['engineer_total']} "
                f"(on evidenced rows {d['engineer_dollars_on_evidenced_rows']}); ratio "
                f"{d['pipeline_over_engineer_total']:.3f}; median row ratio "
                f"{_f(d['median_row_cost_ratio'], 3)} over {d['rows_with_a_cost_ratio']} rows",
            ),
            _kv("engineer bucket shares", d["engineer_bucket_share_pct"]),
        ]
    if s["reproduction"] is not None:
        r = s["reproduction"]
        off = f" ({'; '.join(r['items_off'])})" if r["items_off"] else ""
        lines.append(
            _kv(
                "reproduction of the printed report from implied RCNs, B and CC",
                f"{r['compared']} numbers compared, {r['deviations']} off, "
                f"largest {r['max_abs_deviation']}{off}",
            )
        )
    return lines


def _bucket_lines(result: E2EScore, data_free: bool) -> list[str]:
    if data_free:
        lines = [
            "| bucket | engineer rows | scored | right | pipeline assets | unpriced "
            "| pipeline share | share error |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for b in result.buckets:
            lines.append(
                f"| {b.bucket} | {b.engineer_rows} | {b.rows_scored} | {b.rows_right} | "
                f"{b.pipeline_assets} | {b.pipeline_unpriced} | {_p(b.pipeline_share)} | "
                f"{_p(b.share_error)} |"
            )
        return lines
    lines = [
        "| bucket | engineer rows | scored | right | engineer $ | engineer share "
        "| pipeline $ (all priced) | pipeline share | share error | pipeline assets | unpriced |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for b in result.buckets:
        lines.append(
            f"| {b.bucket} | {b.engineer_rows} | {b.rows_scored} | {b.rows_right} | "
            f"{_f(b.engineer_cost)} | {_p(b.engineer_share)} | {_f(b.pipeline_cost)} | "
            f"{_p(b.pipeline_share)} | {_p(b.share_error)} | {b.pipeline_assets} | "
            f"{b.pipeline_unpriced} |"
        )
    return lines


def _row_lines(result: E2EScore, data_free: bool) -> list[str]:
    head = (
        "| row | kind | outcome | engineer bucket | pipeline (count) | pipeline ($) | right "
        "| assets (priced / unpriced)"
    )
    if data_free:
        lines = [
            head + " | pipeline share | share error | labels | sources |",
            "|---" * 12 + "|",
        ]
    else:
        lines = [
            head + " | engineer $ | pipeline $ | ratio | engineer share | pipeline share "
            "| share error | labels | tally | sources |",
            "|---" * 17 + "|",
        ]
    for r in result.rows:
        right = (
            "" if r.bucket_right_by_count is None else ("yes" if r.bucket_right_by_count else "NO")
        )
        reasons = f" {r.unpriced_reasons}" if r.unpriced else ""
        common = (
            f"| {r.engineer.system} | {r.row.kind} | {r.outcome} | {r.engineer.bucket} | "
            f"{r.bucket_by_count or '-'} | {r.bucket_by_dollars or '-'} | {right} | "
            f"{len(r.assets)} ({r.priced} / {r.unpriced}{reasons}) |"
        )
        sources = r.sources or ""
        if data_free:
            lines.append(
                f"{common} {_p(r.pipeline_share)} | {_p(r.share_error)} | {r.labels or ''} | "
                f"{sources} |"
            )
        else:
            lines.append(
                f"{common} {_f(r.engineer.cost)} | {_f(r.pipeline_cost)} | {_f(r.cost_ratio)} | "
                f"{_p(r.engineer_share)} | {_p(r.pipeline_share)} | {_p(r.share_error)} | "
                f"{r.labels or ''} | {r.tally_count or ''} | {sources} |"
            )
    return lines


def _takeoff_lines(export: StudyExport) -> list[str]:
    """What the export's quantities and prices were drawn from (``summary.takeoff``), and
    the per-asset tally of quantity sources, which every export carries."""
    tally = dict(sorted(Counter(a.source for a in export.assets).items()))
    lines = ["| input | value |", "|---|---|", _kv("quantity sources (assets)", tally)]
    block = export.takeoff
    if block is None:
        lines.append(
            _kv(
                "cost index",
                "not recorded in this export (written before EVAL_REPORT section 37); the "
                "cost rows' `cost_source` text names the year and location factor used",
            )
        )
        return lines
    year, factor = block.get("cost_year"), block.get("location_factor")
    lines += [
        _kv("cost year", f"{year} ({block.get('cost_year_source') or 'source not recorded'})"),
        _kv(
            "location factor",
            f"{factor} ({block.get('location_source') or 'source not recorded'})",
        ),
        _kv(
            "property class",
            f"{block.get('property_class') or '-'} "
            f"({block.get('property_class_source') or 'source not recorded'})",
        ),
        _kv("site items awaiting a measurement", block.get("needs_measurement", 0)),
    ]
    return lines


def _schedule_lines(schedule: Schedule) -> list[str]:
    lines = ["| bucket | total | percent | building | closing costs |", "|---|---|---|---|---|"]
    for b in schedule.bucket_order:
        lines.append(
            f"| {b} | {_f(schedule.bucket_totals[b])} | {schedule.bucket_percent[b]}% | "
            f"{_f(schedule.allocation_building[b])} | {_f(schedule.allocation_closing[b])} |"
        )
    return lines


def _unassigned_lines(result: E2EScore) -> list[str]:
    lines = ["| label | reason | assets | priced | dollars |", "|---|---|---|---|---|"]
    reasons = result.labels.unassigned_reasons
    listed = sorted(result.assignment.unassigned_listed.items())
    no_rule = sorted(result.assignment.unassigned_no_rule.items())
    for label, assets in listed + no_rule:
        dollars = _f(sum((a.total for a in assets if a.total is not None), ZERO))
        if label in reasons or label == "other":
            why = reasons.get(label, "no label")
        else:
            why = f"no rule matched: {dict(Counter((a.room_type, a.material) for a in assets))}"
        lines.append(
            f"| {label} | {why} | {len(assets)} | {sum(a.priced for a in assets)} | {dollars} |"
        )
    return lines


def render_markdown(result: E2EScore, *, data_free: bool = False) -> str:
    """The report. ``data_free`` leaves out the engineer's dollars, shares and ratios."""
    e = result.summary["export"]
    out = [
        f"# End-to-end: study {e['study_id']} rev {e['revision']} against the engineer's report",
        "",
        f"Export: {e['assets']} assets, {e['priced']} priced, {e['unpriced']} unpriced. The "
        f"pipeline's building bucket is compared as `{e['building_bucket_compared_as']}` "
        "(the deployment's property class only changes the building rows' period).",
        "",
        "## Summary",
        "",
        *_summary_lines(result, data_free),
        "",
        "## Takeoff inputs",
        "",
        *_takeoff_lines(result.export),
        "",
        "## Buckets",
        "",
        *_bucket_lines(result, data_free),
        "",
        "## Rows",
        "",
        *_row_lines(result, data_free),
    ]
    ps = result.pipeline_schedule
    if ps is not None:
        absorbed = f" absorbed by {ps.absorbed_by}" if ps.absorbed_by else ""
        out += [
            "",
            "## The pipeline's rows through the deterministic tail",
            "",
            "Each row's assigned dollars as its RCN, scaled to the engineer's basis and closing "
            "costs: the schedule a basis step would print from today's output.",
            "",
            f"Factor {ps.factor:.6f} on {_f(ps.rcn_total)} of RCN; rounding residual "
            f"{ps.residual}{absorbed}.",
            "",
            *_schedule_lines(ps),
        ]
        if not data_free:
            out += ["", "| row | engineer | pipeline scaled | difference |", "|---|---|---|---|"]
            for r in result.rows:
                scaled = ps.rows[r.row.id]
                out.append(
                    f"| {r.engineer.system} | {_f(r.engineer.cost)} | {_f(scaled)} | "
                    f"{_f(scaled - r.engineer.cost)} |"
                )
    es, rep = result.engineer_schedule, result.reproduction
    if es is not None and rep is not None and not data_free:
        out += [
            "",
            "## Reproduction of the engineer's report",
            "",
            f"Implied RCNs (row cost x printed RCN total / T), factor {es.factor:.9f}, rows "
            "scaled back, buckets, percentages, the allocation block (column footing cents "
            f"{es.allocation_adjustment}) and the systems page: {rep.compared} numbers "
            f"compared, {len(rep.deviations)} off, largest deviation {rep.max_abs_deviation}.",
            "",
            *_schedule_lines(es),
        ]
        if result.engineer_systems:
            out += ["", "| system | depreciable | replacement |", "|---|---|---|"]
            out += [
                f"| {line.name} | {_f(line.depreciable_cost)} | {_f(line.replacement_cost)} |"
                for line in result.engineer_systems
            ]
        if rep.deviations:
            out += ["", "Deviations:", ""]
            out += [
                f"- {dv.item}: reproduced {dv.reproduced} vs printed {dv.printed} ({dv.difference})"
                for dv in rep.deviations
            ]
    out += ["", "## Unassigned assets", "", *_unassigned_lines(result)]
    return "\n".join(out) + "\n"


def to_json(result: E2EScore) -> dict[str, Any]:
    """The summary, the rows and the buckets as plain JSON (Decimals as strings)."""

    def dec(value: Decimal | None) -> str | None:
        return None if value is None else str(cents(value))

    return {
        "summary": result.summary,
        "rows": [
            {
                "id": r.row.id,
                "system": r.engineer.system,
                "kind": r.row.kind,
                "outcome": r.outcome,
                "engineer_bucket": r.engineer.bucket,
                "bucket_by_count": r.bucket_by_count,
                "bucket_by_dollars": r.bucket_by_dollars,
                "bucket_right_by_count": r.bucket_right_by_count,
                "bucket_right_by_dollars": r.bucket_right_by_dollars,
                "assets": len(r.assets),
                "priced": r.priced,
                "unpriced": r.unpriced,
                "unpriced_reasons": r.unpriced_reasons,
                "labels": r.labels,
                "sources": r.sources,
                "tally_count": r.tally_count,
                "tally_dollars": {k: str(v) for k, v in r.tally_dollars.items()},
                "engineer_cost": dec(r.engineer.cost),
                "pipeline_cost": dec(r.pipeline_cost),
                "cost_ratio": None if r.cost_ratio is None else float(r.cost_ratio),
                "engineer_share": float(r.engineer_share),
                "pipeline_share": float(r.pipeline_share),
                "share_error": float(r.share_error),
                "pipeline_scaled": (
                    dec(result.pipeline_schedule.rows[r.row.id])
                    if result.pipeline_schedule
                    else None
                ),
            }
            for r in result.rows
        ],
        "buckets": [
            {
                "bucket": b.bucket,
                "engineer_cost": dec(b.engineer_cost),
                "engineer_share": float(b.engineer_share),
                "pipeline_cost": dec(b.pipeline_cost),
                "pipeline_share": float(b.pipeline_share),
                "share_error": float(b.share_error),
                "pipeline_assets": b.pipeline_assets,
                "pipeline_unpriced": b.pipeline_unpriced,
                "engineer_rows": b.engineer_rows,
                "rows_scored": b.rows_scored,
                "rows_right": b.rows_right,
            }
            for b in result.buckets
        ],
    }


# ------------------------------------------------------------------ CLI


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.e2e_report", description=__doc__)
    parser.add_argument("--export", type=Path, required=True, help="rev-<n>.json or .assets.csv")
    parser.add_argument(
        "--rows", type=Path, default=None, help=f"default $%s/labels/{ROWS_FILE}" % ENV_VAR
    )
    parser.add_argument(
        "--report", type=Path, default=None, help=f"default $%s/labels/{REPORT_FILE}" % ENV_VAR
    )
    parser.add_argument("--out", type=Path, default=None, help="Markdown report")
    parser.add_argument("--json", type=Path, default=None, help="rows, buckets and summary as JSON")
    parser.add_argument("--data-free", action="store_true", help="no engineer dollars or shares")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows = load_rows(args.rows or default_labels_path(ROWS_FILE))
        report = load_report(args.report or default_labels_path(REPORT_FILE))
        export = load_export(args.export)
        result = score(export, rows, report)
    except (E2EError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = render_markdown(result, data_free=args.data_free)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(to_json(result), indent=1) + "\n", encoding="utf-8")
    c, sh = result.summary["classification"], result.summary["shares"]
    print(
        f"{export.study_id} rev {export.revision}: evidenced {c['evidenced']}/"
        f"{c['photo_visible_rows']}, bucket right {c['bucket_right_by_count']}/{c['evidenced']}, "
        f"max bucket share error {sh['max_bucket_share_error_pct']:.1f} points",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
