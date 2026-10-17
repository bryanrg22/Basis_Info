"""``python -m evals.score_schedule``: how close a system's output is to the engineer's
schedule, row by row, with quantities (EVAL_REPORT section 26).

``report_rows`` (``evals.run``) asks whether each of the engineer's photo-derivable rows
has evidence; every architecture that lists objects reaches its ceiling on this study, so
it cannot separate them. This scorer adds what the engineer prices from:

* **Row set.** Recall over the engineer's photo-derivable rows (kind A and B), and
  precision against a schedule template that holds the engineer's rows plus *distractor*
  rows the engineer did not use (``--template``, private: each distractor names the labels
  that would evidence it). A room-inventory system lists a row when the row's evidence rule
  (``report_rows``, :func:`evals.run.row_outcome`; a distractor's labels in any room) is
  met; a schedule system (``evals.predict_multiview --scope schedule``) lists the rows it
  writes. Kind C rows a schedule writes are counted apart (photos cannot establish them).
* **Quantities.** The engineer's report gives a cost per row and no quantities (no units,
  linear or square feet), so the reference quantity of a row that needs a count
  (``count_required``) is the hand-labelled room inventory (``room_inventory_v1``) put
  through the same rule: the number of distinct objects of the row's labels in the rooms
  the rule allows. Per system: the row quantity, the absolute error, WAPE (sum |error| /
  sum reference) and the cost-weighted relative error (sum over rows of the row's cost
  share x min(1, |error| / max(reference, 1)), divided by the rows' total share: the share
  of the engineer's dollars on count rows the quantity would misstate, if unit costs were
  uniform within a row). A schedule row's quantity counts only when its unit is ``each``.
* **Buckets** (schedule systems): the share of listed engineer rows given the engineer's
  recovery period.

Every metric is averaged over repeats (a saved prediction's ``repeats``). Paired comparison
against ``--baseline``: per count row, the mean over repeats of |error| / max(reference, 1),
the difference to the baseline's, a 95% bootstrap interval over rows (rows are the unit:
one property).

Usage (from ``backEnd/``)::

    python -m evals.score_schedule --data-dir ~/basis-eval-data \\
        --template ~/basis-eval-data/work/schedule_template_v1.json \\
        --baseline current=P/rooms19_v3_opus55 --system house=P/arch26_house_opus55 \\
        --out report.md
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from basis.vision.multiview import normalize_system
from evals import datasets as ds
from evals.run import (
    INVENTORY_FILE,
    RoomMaterials,
    _inventory,
    _scored,
    gold_materials,
    load_room_materials,
    materials_for_repeats,
    row_outcome,
)

BUCKETS: Final[dict[str, str]] = {"5": "y5", "7": "y7", "15": "y15", "27.5": "y27_5", "39": "y39"}


@dataclass(frozen=True)
class Distractor:
    name: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Template:
    names: tuple[str, ...]
    distractors: tuple[Distractor, ...]

    @classmethod
    def read(cls, path: Path) -> Template:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["rows"] if isinstance(data, dict) else data
        names = tuple(r["name"] if isinstance(r, dict) else str(r) for r in rows)
        distractors = tuple(
            Distractor(r["name"], tuple(r.get("labels", ())))
            for r in rows
            if isinstance(r, dict) and r.get("distractor")
        )
        return cls(names, distractors)


@dataclass
class RepeatScore:
    """One repeat of one system."""

    listed: set[str] = field(default_factory=set)  # engineer row ids listed (kind A/B)
    listed_c: set[str] = field(default_factory=set)  # engineer kind C rows listed
    distractors: set[str] = field(default_factory=set)
    quantities: dict[str, float | None] = field(default_factory=dict)  # row id -> quantity
    buckets_right: int = 0
    buckets_listed: int = 0


@dataclass
class SystemScore:
    name: str
    repeats: list[RepeatScore]
    # room-inventory systems: per room, the mean |count error| over its (label, repeat) pairs
    room_errors: dict[str, float] | None = None
    room_mae: float | None = None


# --------------------------------------------------------------------------- inventories


def inventory_quantity(
    row: ds.ReportRow,
    inventory: Mapping[str, Mapping[str, int]],
    room_types: Mapping[str, str],
    materials: RoomMaterials | None,
) -> int:
    """Distinct objects of the row's labels in the rooms its rule allows (with the
    material rule applied), i.e. the sum of the counts behind :func:`row_outcome`."""
    outcome = row_outcome(row, inventory, room_types, materials)
    return sum(e.count for e in outcome.evidence)


def score_inventory(
    rows: Sequence[ds.ReportRow],
    template: Template,
    inventory: Mapping[str, Mapping[str, int]],
    room_types: Mapping[str, str],
    materials: RoomMaterials | None,
) -> RepeatScore:
    score = RepeatScore()
    for row in rows:
        if not row.photo_derivable:
            continue
        outcome = row_outcome(row, inventory, room_types, materials)
        if outcome.evidenced:
            score.listed.add(row.id)
        if row.count_required:
            score.quantities[row.id] = float(sum(e.count for e in outcome.evidence))
    present = {label for counts in inventory.values() for label, n in counts.items() if n > 0}
    score.distractors = {d.name for d in template.distractors if present & set(d.labels)}
    return score


def room_count_errors(
    gold: Mapping[str, Mapping[str, int]], inventories: Sequence[Mapping[str, Mapping[str, int]]]
) -> tuple[dict[str, float], float]:
    """The ``room_inventory`` count errors of every repeat (``evals.run`` scores repeat 1
    only): per (room, label) over the union of the gold and predicted scored labels (the
    unscored labels, furniture and ``other``, left out; a missing room is empty). Returns the
    per-room mean over its pairs and repeats, and the MAE over all pairs."""
    per_room: dict[str, list[int]] = {}
    for inventory in inventories:
        for room, truth in gold.items():
            guess = _scored(dict(inventory.get(room, {})))
            for label in sorted(set(truth) | set(guess)):
                per_room.setdefault(room, []).append(abs(guess.get(label, 0) - truth.get(label, 0)))
    pairs = [e for errors in per_room.values() for e in errors]
    return (
        {room: statistics.fmean(errors) for room, errors in per_room.items() if errors},
        statistics.fmean(pairs) if pairs else 0.0,
    )


def read_inventories(directory: Path, inventory_file: str) -> list[dict[str, dict[str, int]]]:
    """``{room: {label: count}}`` per repeat (a saved prediction's ``repeats`` unpacked)."""
    data = json.loads((directory / inventory_file).read_text(encoding="utf-8"))
    repeats = max(
        (len(v["repeats"]) for v in data.values() if isinstance(v, dict) and "repeats" in v),
        default=1,
    )
    out: list[dict[str, dict[str, int]]] = []
    for r in range(repeats):
        out.append(
            {
                room: _inventory(
                    value["repeats"][r % len(value["repeats"])]
                    if isinstance(value, dict) and "repeats" in value
                    else value
                )
                for room, value in data.items()
            }
        )
    return out


# --------------------------------------------------------------------------- schedules


def score_schedule_rows(
    rows: Sequence[ds.ReportRow], template: Template, written: Sequence[Mapping[str, Any]]
) -> RepeatScore:
    by_name = {normalize_system(r.system): r for r in rows}
    distractor_names = {normalize_system(d.name): d.name for d in template.distractors}
    score = RepeatScore()
    quantities: dict[str, float] = {}
    for entry in written:
        key = normalize_system(str(entry["system"]))
        row = by_name.get(key)
        if row is None:
            if key in distractor_names:
                score.distractors.add(distractor_names[key])
            continue
        if not row.photo_derivable:
            score.listed_c.add(row.id)
            continue
        if row.id not in score.listed:
            score.buckets_listed += 1
            score.buckets_right += BUCKETS.get(str(entry.get("bucket")), "") == row.bucket
        score.listed.add(row.id)
        if row.count_required and entry.get("unit") == "each":
            quantities[row.id] = quantities.get(row.id, 0.0) + float(entry.get("quantity", 0))
    for row in rows:
        if row.photo_derivable and row.count_required:
            score.quantities[row.id] = quantities.get(row.id, 0.0)
    return score


# --------------------------------------------------------------------------- metrics


def relative_error(predicted: float | None, reference: float) -> float:
    if predicted is None:
        return 1.0
    return min(1.0, abs(predicted - reference) / max(reference, 1.0))


def summarize(
    system: SystemScore, rows: Sequence[ds.ReportRow], reference: Mapping[str, float]
) -> dict[str, Any]:
    derivable = [r for r in rows if r.photo_derivable]
    count_rows = [r for r in derivable if r.count_required]
    out: dict[str, list[float]] = {}

    def add(key: str, value: float) -> None:
        out.setdefault(key, []).append(value)

    for rep in system.repeats:
        listed = len(rep.listed) + len(rep.distractors)
        add("row_recall", len(rep.listed) / len(derivable))
        add("row_precision", len(rep.listed) / listed if listed else 0.0)
        add("distractors", float(len(rep.distractors)))
        add("kind_c_listed", float(len(rep.listed_c)))
        add(
            "cost_recall",
            sum(r.cost_share for r in derivable if r.id in rep.listed)
            / sum(r.cost_share for r in derivable),
        )
        errors = [abs((rep.quantities.get(r.id) or 0.0) - reference[r.id]) for r in count_rows]
        add("quantity_mae", statistics.fmean(errors))
        add("quantity_wape", sum(errors) / max(sum(reference[r.id] for r in count_rows), 1.0))
        add(
            "quantity_cost_weighted_error",
            sum(
                r.cost_share * relative_error(rep.quantities.get(r.id), reference[r.id])
                for r in count_rows
            )
            / sum(r.cost_share for r in count_rows),
        )
        if rep.buckets_listed:
            add("bucket_accuracy", rep.buckets_right / rep.buckets_listed)
    result: dict[str, Any] = {k: statistics.fmean(v) for k, v in out.items()}
    result["repeats"] = len(system.repeats)
    result["missed_rows"] = sorted(
        {r.system for r in derivable for rep in system.repeats if r.id not in rep.listed}
    )
    result["distractor_rows"] = sorted({d for rep in system.repeats for d in rep.distractors})
    result["per_row_quantity"] = {
        r.system: {
            "reference": reference[r.id],
            "predicted": [rep.quantities.get(r.id) for rep in system.repeats],
        }
        for r in count_rows
    }
    return result


def row_errors(
    system: SystemScore, rows: Sequence[ds.ReportRow], reference: Mapping[str, float]
) -> dict[str, float]:
    """Per count row: mean over repeats of the capped relative error."""
    return {
        r.id: statistics.fmean(
            relative_error(rep.quantities.get(r.id), reference[r.id]) for rep in system.repeats
        )
        for r in rows
        if r.photo_derivable and r.count_required
    }


def paired_bootstrap(
    system: Mapping[str, float],
    baseline: Mapping[str, float],
    resamples: int = 5000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Mean difference (system - baseline) over rows with a 95% percentile interval."""
    keys = sorted(set(system) & set(baseline))
    diffs = [system[k] - baseline[k] for k in keys]
    if not diffs:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(diffs) for _ in diffs) for _ in range(resamples))
    return (
        statistics.fmean(diffs),
        means[int(0.025 * resamples)],
        means[int(0.975 * resamples) - 1],
    )


# --------------------------------------------------------------------------- cli


def load_system(
    name: str,
    directory: Path,
    rows: Sequence[ds.ReportRow],
    template: Template,
    room_types: Mapping[str, str],
    inventory_file: str,
    gold_counts: Mapping[str, Mapping[str, int]] | None = None,
) -> SystemScore:
    if (directory / "schedule.json").is_file():
        data = json.loads((directory / "schedule.json").read_text(encoding="utf-8"))
        return SystemScore(
            name, [score_schedule_rows(rows, template, written) for written in data["repeats"]]
        )
    inventories = read_inventories(directory, inventory_file)
    materials = materials_for_repeats(
        load_room_materials(directory, inventory_file), len(inventories)
    )
    score = SystemScore(
        name,
        [
            score_inventory(rows, template, inv, room_types, mat)
            for inv, mat in zip(inventories, materials, strict=True)
        ],
    )
    if gold_counts is not None:
        score.room_errors, score.room_mae = room_count_errors(gold_counts, inventories)
    return score


def parse_system(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected NAME=DIR, got {value!r}")
    return name, Path(path).expanduser()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.score_schedule", description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--baseline", type=parse_system, default=None)
    parser.add_argument("--system", type=parse_system, action="append", default=[])
    parser.add_argument("--inventory-file", default=INVENTORY_FILE)
    parser.add_argument("--out", type=Path, default=None, help="Markdown report")
    parser.add_argument("--json", type=Path, default=None, help="all metrics as JSON")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    report_rows = ds.load("report_rows", args.data_dir)
    gold = ds.load("room_inventory", args.data_dir)
    components = ds.load("photo_components", args.data_dir)
    assert isinstance(report_rows, ds.ReportRows)
    assert isinstance(gold, ds.RoomInventorySet)
    assert isinstance(components, ds.PhotoComponentsSet)
    rows = [r for r in report_rows.rows if r.split == "dev"]
    template = Template.read(args.template)
    room_types = {room.room_id: room.room_type for room in gold.rooms}
    gold_inventory = {
        room.room_id: _inventory(
            {
                label: sum(o.count for o in room.objects if o.label == label)
                for label in {o.label for o in room.objects}
            }
        )
        for room in gold.rooms
    }
    gold_score = score_inventory(
        rows, template, gold_inventory, room_types, gold_materials(gold, components)
    )
    reference = {k: v or 0.0 for k, v in gold_score.quantities.items()}
    systems = [SystemScore("gold_inventory", [gold_score])]
    pairs = ([args.baseline] if args.baseline else []) + list(args.system)
    gold_counts = {room.room_id: _scored(room.counts()) for room in gold.rooms}
    for name, directory in pairs:
        systems.append(
            load_system(
                name, directory, rows, template, room_types, args.inventory_file, gold_counts
            )
        )
    results = {s.name: summarize(s, rows, reference) for s in systems}
    for s in systems:
        results[s.name]["room_count_mae"] = s.room_mae
    if args.baseline:
        base = next(s for s in systems if s.name == args.baseline[0])
        base_errors = row_errors(base, rows, reference)
        for s in systems[2:]:
            results[s.name]["quantity_error_vs_baseline"] = paired_bootstrap(
                row_errors(s, rows, reference), base_errors
            )
            if s.room_errors is not None and base.room_errors is not None:
                results[s.name]["room_error_vs_baseline"] = paired_bootstrap(
                    s.room_errors, base.room_errors
                )
    lines = [
        "| System | Row recall | Row precision | Distractors | Kind C listed | Cost recall | "
        "Qty MAE | Qty WAPE | Qty cost-weighted error | vs baseline [95% CI] | Bucket acc. | "
        "Room count MAE (all repeats) | vs baseline, per room [95% CI] |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        diff = r.get("quantity_error_vs_baseline")
        lines.append(
            f"| {name} | {r['row_recall']:.3f} | {r['row_precision']:.3f} | "
            f"{r['distractors']:.1f} | {r['kind_c_listed']:.1f} | {r['cost_recall']:.3f} | "
            f"{r['quantity_mae']:.2f} | {r['quantity_wape']:.3f} | "
            f"{r['quantity_cost_weighted_error']:.3f} | "
            + (f"{diff[0]:+.3f} [{diff[1]:+.3f}, {diff[2]:+.3f}]" if diff else "")
            + f" | {r.get('bucket_accuracy', float('nan')):.2f} | "
            + (f"{r['room_count_mae']:.3f}" if r.get("room_count_mae") is not None else "")
            + " | "
            + (
                "{:+.3f} [{:+.3f}, {:+.3f}]".format(*r["room_error_vs_baseline"])
                if r.get("room_error_vs_baseline")
                else ""
            )
            + " |"
        )
    text = "\n".join(lines)
    print(text)
    for name, r in results.items():
        print(f"{name}: missed {r['missed_rows']}; distractors {r['distractor_rows']}")
    if args.out:
        args.out.write_text(text + "\n")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1, default=list))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
