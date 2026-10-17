"""The tables of EVAL_REPORT section 28 (cost sweep), from saved runs.

Run from ``backEnd/`` (it imports the harness, so every number is the harness's)::

    BASIS_ENV=test uv run --no-sync python ../docs/review_2026-10-15/repro/cost_sweep_table.py \\
        photos --photos ~/basis-eval-data/inputs/photos \\
        --aliases ~/basis-eval-data/labels/room_aliases_v1.json \\
        BASELINE_MERGED_RUN CANDIDATE_MERGED_RUN ...
    ... cost_sweep_table.py rooms BASELINE_ROOM_RUN CANDIDATE_ROOM_RUN ...
    ... cost_sweep_table.py assets BASELINE_ASSET_RUN CANDIDATE_ASSET_RUN ...

The first run named is the baseline (the current default); every other run is compared
with it by :func:`evals.metrics.paired_bootstrap` (10,000 resamples, seeded) over the
items of the step: photos, rooms or asset rows. Each item's value is its mean over the
three repeats, so the comparison is paired and the repeats' noise is averaged in.

``photos`` (a merged ``evals.merge_photo_runs`` output whose room type comes from the
surfaces call):

* room accuracy: mean over repeats, pass^3, and the paired difference over the 52 photos;
* surfaces P / R / F1: micro over photos, labels of
  :data:`basis.vision.localize.SURFACE_LABELS` only (the countables call is the same run in
  every merge, so the surfaces call is the only thing that differs), mean over repeats;
  the paired differences are over per-photo F1 (two empty sets agree: 1.0) and, for
  precision and recall, over photos with the ratio pooled (:func:`_ratio_diff`);
* all labels P / R / F1: the harness's ``photo_components`` (repeat 1), as in section 12;
* report rows: the deterministic heuristic room inventory
  (:func:`basis.vision.reconcile.heuristic_merge`, grouping by file name and aliases, as
  ``evals.predict_rooms`` writes it) built from each per-photo repeat, scored by the
  harness's ``report_rows``: rows evidenced and material present (of the rows that need a
  material), the mean over the three per-photo repeats;
* room-level surfaces P / R: the surface labels of the same heuristic inventory against the
  room labels (``room_inventory``), pooled over rooms and the three repeats; the paired
  differences resample rooms;
* cost per 52 photos and seconds per photo: the surfaces source run's ``run.json`` and raw
  records (the second source of the merge).

``rooms`` (``evals.predict_rooms`` outputs on the same per-photo input):

* count MAE, exact-count rate, signed bias and label F1 over (room, label) pairs as the
  harness's ``room_inventory`` scores them (``other`` and loose furniture left out on both
  sides, :data:`evals.run.UNSCORED_LABELS`), the mean of the per-repeat values; the same
  on the multi-photo rooms (the only rooms a call touches with ``--single-photo-rooms
  per-photo``);
* paired difference of the per-room count MAE (mean absolute error over the room's pairs,
  averaged over repeats), over all rooms and over the multi-photo rooms;
* report rows evidenced (repeat 1) and pass^3, material present (repeat 1);
* labelled surfaces with the right material: (room, surface) pairs whose photos' labels
  record a material (:data:`GOLD_MATERIAL` maps the labels' free text to the enum values
  it accepts), right when the run's most common material over the repeats is accepted
  (a surface the run does not list is wrong);
* surfaces with the baseline's material: of the (room, wainscoting / wall finish / tile)
  pairs both runs list, those whose most common material over the repeats is the
  baseline's (the second look's answer; the old ``other`` + note and ``concrete`` + note
  encodings read as ``wallpaper`` and ``painted_masonry``);
* cost per run (``spent_usd`` / repeats); ``predict_rooms`` records no per-call latency.

``assets`` (``evals.predict_assets`` outputs):

* the columns of section 17.2 plus the needs-review rate over (row, repeat) records; the
  harness's ``citation_validity`` and ``rows_with_verified_citation`` are repeat 1 (as in
  17.2), so each is shown with its value over all three repeats too (verified / claimed
  summed over every record; the per-row mean of "at least one verified citation");
* paired differences over the 46 rows of: bucket right, rows with a verified citation and
  needs review (each the per-row mean over repeats), and citation validity as a ratio of
  sums (the bootstrap resamples rows and recomputes verified / claimed).
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from basis.domain.rules import normalize_label
from basis.vision.localize import SURFACE_LABELS
from basis.vision.reconcile import RoomInventory, heuristic_merge
from basis.vision.rooms import PhotoInput, group_photos
from evals import datasets as ds
from evals.metrics import inventory_metrics, paired_bootstrap
from evals.predict_photos import photo_files
from evals.predict_rooms import load_aliases, load_per_photo, object_record
from evals.run import (
    HEURISTIC_INVENTORY_FILE,
    INVENTORY_FILE,
    UNSCORED_LABELS,
    _inventory,
    _repeats,
    _scored,
    load_room_materials,
    score_assets,
    score_photo_components,
    score_report_rows,
)

SURFACES = frozenset(str(label.value) for label in SURFACE_LABELS)


def _diff(a: Sequence[float], b: Sequence[float]) -> str:
    d = paired_bootstrap(a, b)
    return f"{d.mean:+.3f} [{d.low:+.3f}, {d.high:+.3f}] p={d.p_value:.3f}"


def _rows(result: Any) -> dict[str, Any]:
    return {r.metric: r for r in result.rows}


def _run(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((directory / "run.json").read_text())
    return data


# ------------------------------------------------------------------------------- photos


def _surfaces_source(merged: Path) -> Path:
    run = _run(merged)
    return Path(run["room_from"]) if "room_from" in run else merged


def _photo_seconds(directory: Path) -> float:
    records = [json.loads(x) for x in (directory / "photo_raw.jsonl").read_text().splitlines()]
    return statistics.mean(float(r["seconds"]) for r in records if "seconds" in r)


def _heuristic_report(
    merged: Path,
    repeat: int,
    photos: Path,
    aliases: dict[str, str],
    data: dict[str, Any],
    work: Path,
) -> tuple[int, int, int, int, list[tuple[int, int, int]]]:
    """(rows evidenced, rows scored, material present, rows needing a material, per
    labelled room the surface labels' (tp, fp, fn) against the room labels) for the
    heuristic room inventory built from one per-photo repeat."""
    analyses = load_per_photo(merged, repeat)
    files = [f.name for f in photo_files(photos)]
    groups = group_photos([PhotoInput(f, analyses.get(f)) for f in files], aliases)
    out = work / f"{merged.name}_r{repeat}"
    out.mkdir(parents=True)
    heuristic = {
        g.room_id: RoomInventory(objects=heuristic_merge([p.analysis for p in g.photos]))
        for g in groups
    }
    (out / "rooms.json").write_text(
        json.dumps(
            [
                {
                    "room_id": g.room_id,
                    "photos": [p.file for p in g.photos],
                    "heuristic_objects": [
                        object_record(o, [p.file for p in g.photos])
                        for o in heuristic[g.room_id].objects
                    ],
                }
                for g in groups
            ]
        )
    )
    saved = {room: inv.counts() for room, inv in heuristic.items()}
    rooms = data["room_inventory"]
    result = score_report_rows(
        data["report_rows"],
        "dev",
        1,
        saved,
        rooms,
        load_room_materials(out, HEURISTIC_INVENTORY_FILE),
    )
    m = _rows(result)
    coverage, material = m["row_coverage"], m["material_present_rate"]
    room_counts = []
    for room in sorted(rooms.rooms, key=lambda r: r.room_id):
        if room.split != "dev":
            continue
        truth = {k for k, v in _scored(room.counts()).items() if v > 0} & SURFACES
        listed = heuristic.get(room.room_id)
        found = {o.label.value for o in listed.objects} & SURFACES if listed else set()
        room_counts.append((len(truth & found), len(found - truth), len(truth - found)))
    return (
        round(coverage.value * coverage.n),
        coverage.n,
        round(material.value * material.n),
        material.n,
        room_counts,
    )


def _surface_scores(
    merged: Path, data: dict[str, Any]
) -> tuple[
    list[float],
    tuple[float, float, float],
    list[float],
    list[float],
    list[tuple[int, int, int]],
]:
    """Per photo: room right (mean over repeats), surfaces F1 (mean over repeats); micro
    surfaces P/R/F1 (mean over repeats); per photo room right on every repeat; per photo
    surface (tp, fp, fn) summed over the repeats."""
    rooms = json.loads((merged / "photo_rooms.json").read_text())
    comps = json.loads((merged / "photo_components.json").read_text())
    photos = [p for p in data["photo_components"].photos if p.split == "dev"]
    room_truth = {p.file: p.room_type for p in data["photo_rooms"].photos if p.split == "dev"}
    repeats = int(_run(merged).get("repeats") or 1)
    room_right, room_all, f1s = [], [], []
    counts: list[tuple[int, int, int]] = []
    micro = np.zeros((repeats, 3))
    for photo in photos:
        truth = {c.label for c in photo.components if c.label in SURFACES} - UNSCORED_LABELS
        per_repeat_f1 = []
        photo_counts = np.zeros(3, dtype=int)
        for i, raw in enumerate(_repeats(comps.get(photo.file), repeats)):
            predicted = {normalize_label(str(x)) for x in raw or []} & SURFACES
            tp, fp, fn = len(truth & predicted), len(predicted - truth), len(truth - predicted)
            micro[i] += (tp, fp, fn)
            photo_counts += (tp, fp, fn)
            per_repeat_f1.append(1.0 if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn))
        f1s.append(statistics.mean(per_repeat_f1))
        counts.append((int(photo_counts[0]), int(photo_counts[1]), int(photo_counts[2])))
        if photo.file in room_truth:
            hits = [
                ("ambiguous" if v is None else str(v)) == room_truth[photo.file]
                for v in _repeats(rooms.get(photo.file), repeats)
            ]
            room_right.append(statistics.mean(hits))
            room_all.append(1.0 if all(hits) else 0.0)
    p = statistics.mean(tp / (tp + fp) if tp + fp else 0.0 for tp, fp, _ in micro)
    r = statistics.mean(tp / (tp + fn) if tp + fn else 0.0 for tp, _, fn in micro)
    f = statistics.mean(2 * tp / (2 * tp + fp + fn) for tp, fp, fn in micro)
    return room_right, (p, r, f), f1s, room_all, counts


def photos_table(runs: list[Path], photos: Path, aliases_path: Path) -> None:
    data: dict[str, Any] = dict(ds.load_available(ds.data_dir()))
    aliases = load_aliases(aliases_path)
    print(
        "| Run (surfaces model) | Room accuracy, mean of 3 | pass^3 | Surfaces P / R / F1 "
        "| All labels P / R / F1 (repeat 1) | Report rows evidenced, mean of 3 "
        "| Material present, mean of 3 | Room-level surfaces P / R "
        "| Cost per 52 photos (surfaces call) "
        "| Seconds per photo | models |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    Counts = list[tuple[int, int, int]]
    base: tuple[list[float], list[float], Counts, Counts] | None = None
    diffs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for merged in runs:
            room_right, (p, r, f), f1s, room_all, counts = _surface_scores(merged, data)
            repeats = int(_run(merged).get("repeats") or 1)
            harness = _rows(
                score_photo_components(
                    data["photo_components"],
                    "dev",
                    repeats,
                    json.loads((merged / "photo_components.json").read_text()),
                )
            )
            reports = [
                _heuristic_report(merged, i, photos, aliases, data, Path(tmp))
                for i in range(1, repeats + 1)
            ]
            # per labelled room, the surface (tp, fp, fn) summed over the per-photo repeats
            rooms = [
                (sum(c[0] for c in col), sum(c[1] for c in col), sum(c[2] for c in col))
                for col in zip(*(x[4] for x in reports), strict=True)
            ]
            tp, fp, fn = (sum(c[i] for c in rooms) for i in range(3))
            source = _surfaces_source(merged)
            src = _run(source)
            print(
                f"| `{merged.name}` | {statistics.mean(room_right):.3f} "
                f"| {statistics.mean(room_all):.3f} | {p:.3f} / {r:.3f} / {f:.3f} "
                f"| {harness['precision'].value:.2f} / {harness['recall'].value:.2f} / "
                f"{harness['f1'].value:.2f} "
                f"| {statistics.mean(x[0] for x in reports):.1f} of {reports[0][1]} "
                f"| {statistics.mean(x[2] for x in reports):.1f} of {reports[0][3]} "
                f"| {tp / (tp + fp):.3f} / {tp / (tp + fn):.3f} "
                f"| ${src['spent_usd'] / int(src.get('repeats') or 1):.3f} "
                f"| {_photo_seconds(source):.1f} | {', '.join(src.get('models_reported', []))} |"
            )
            if base is None:
                base = (room_right, f1s, counts, rooms)
                continue

            def precision(c: Counts) -> list[tuple[int, int]]:
                return [(t, t + false_pos) for t, false_pos, _ in c]

            def recall(c: Counts) -> list[tuple[int, int]]:
                return [(t, t + miss) for t, _, miss in c]

            diffs.append(
                f"- `{merged.name}` minus baseline: room accuracy "
                f"{_diff(room_right, base[0])}; surfaces F1 per photo {_diff(f1s, base[1])}; "
                f"surfaces precision {_ratio_diff(precision(counts), precision(base[2]))}, "
                f"recall {_ratio_diff(recall(counts), recall(base[2]))} (over photos); "
                f"room-level precision {_ratio_diff(precision(rooms), precision(base[3]))}, "
                f"recall {_ratio_diff(recall(rooms), recall(base[3]))} (over rooms)"
            )
    print()
    print("\n".join(diffs))


# -------------------------------------------------------------------------------- rooms


def _room_errors(
    saved: dict[str, Any], gold: dict[str, dict[str, int]], repeats: int
) -> tuple[list[Any], dict[str, list[float]]]:
    runs: list[dict[str, dict[str, int]]] = [{} for _ in range(repeats)]
    for room in gold:
        for i, value in enumerate(_repeats(saved.get(room), repeats)):
            runs[i][room] = _scored(_inventory(value))
    metrics = [inventory_metrics(gold, run) for run in runs]
    per_room: dict[str, list[float]] = {}
    for room in gold:
        values = []
        for m in metrics:
            pairs = m.per_room[room].pairs
            values.append(statistics.mean(abs(g - p) for _, g, p in pairs) if pairs else 0.0)
        per_room[room] = values
    return metrics, per_room


MATERIAL_SURFACES = frozenset({"wainscoting", "wall_finish", "tile"})


def _material(obj: dict[str, Any]) -> str:
    """The material, with the pre-section-18 encodings (``other`` + note ``wallpaper``,
    ``concrete`` + note ``painted masonry``) read as the enum values that replaced them."""
    material = str(obj.get("material") or "unknown")
    note = str(obj.get("note") or "").lower()
    if material == "other" and note.startswith("wallpaper"):
        return "wallpaper"
    if material == "concrete" and note.startswith("painted mason"):
        return "painted_masonry"
    return material


def _surface_materials(run: Path) -> dict[tuple[str, str], str]:
    """The most common material per (room, surface label) over every repeat's answer."""
    seen: dict[tuple[str, str], list[str]] = {}
    for line in (run / "room_raw.jsonl").read_text().splitlines():
        record = json.loads(line)
        for obj in record["objects"]:
            if obj["label"] in MATERIAL_SURFACES:
                seen.setdefault((record["room_id"], obj["label"]), []).append(_material(obj))
    return {key: max(set(v), key=v.count) for key, v in seen.items()}


# The per-photo labels record surface materials in free text; the enum values each accepts.
GOLD_MATERIAL: dict[str, frozenset[str]] = {
    "painted_block": frozenset({"painted_masonry"}),
    "ceramic_tile": frozenset({"ceramic", "porcelain"}),
    "ceramic_subway_tile": frozenset({"ceramic", "porcelain"}),
    "vinyl_or_tile": frozenset({"vinyl", "ceramic", "porcelain"}),
    "paint": frozenset({"drywall"}),
    "gypsum": frozenset({"drywall"}),
    "gypsum_unfinished": frozenset({"drywall"}),
    "painted_wood": frozenset({"wood"}),
    "reclaimed_wood_plank": frozenset({"wood"}),
    "embossed_wallpaper": frozenset({"wallpaper"}),
}


def _gold_materials(data: dict[str, Any], run: Path) -> dict[tuple[str, str], frozenset[str]]:
    """Accepted materials per (room, surface label) from the per-photo labels of the
    room's photos (``rooms.json`` of the run gives the grouping)."""
    by_file = {p.file: p for p in data["photo_components"].photos}
    gold: dict[tuple[str, str], frozenset[str]] = {}
    for room in json.loads((run / "rooms.json").read_text()):
        for file in room["photos"]:
            for c in by_file[file].components if file in by_file else ():
                if c.label in MATERIAL_SURFACES and c.material in GOLD_MATERIAL:
                    key = (room["room_id"], c.label)
                    gold[key] = gold.get(key, frozenset()) | GOLD_MATERIAL[c.material]
    return gold


def rooms_table(runs: list[Path]) -> None:
    data: dict[str, Any] = dict(ds.load_available(ds.data_dir()))
    labels = [r for r in data["room_inventory"].rooms if r.split == "dev"]
    gold = {r.room_id: _scored(r.counts()) for r in labels}
    print(
        "| Run (reconciler = materials model) | Count MAE | Exact-count rate | Signed bias "
        "| Label F1 | Multi-photo rooms: MAE / exact / bias | Report rows (repeat 1), pass^3 "
        "| Material present | Labelled surfaces with the right material "
        "| Surfaces with the baseline's material | Cost per run | models |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    base_materials: dict[tuple[str, str], str] | None = None
    base: tuple[list[float], list[float]] | None = None
    diffs: list[str] = []
    for run in runs:
        info = _run(run)
        repeats = int(info.get("repeats") or 1)
        saved = json.loads((run / INVENTORY_FILE).read_text())
        rooms_meta = json.loads((run / "rooms.json").read_text())
        multi = {r["room_id"] for r in rooms_meta if len(r["photos"]) > 1} & set(gold)
        metrics, per_room = _room_errors(saved, gold, repeats)
        multi_metrics, _ = _room_errors(saved, {k: gold[k] for k in multi}, repeats)

        def avg(ms: list[Any], attr: str) -> float:
            return statistics.mean(float(getattr(m, attr)) for m in ms)

        report = _rows(
            score_report_rows(
                data["report_rows"],
                "dev",
                repeats,
                saved,
                data["room_inventory"],
                load_room_materials(run, INVENTORY_FILE),
            )
        )
        coverage, material = report["row_coverage"], report["material_present_rate"]
        stable = report.get(f"pass^{repeats}")
        materials = _surface_materials(run)
        if base_materials is None:
            base_materials = materials
        shared = sorted(set(materials) & set(base_materials))
        agree = sum(materials[k] == base_materials[k] for k in shared)
        gold_materials = _gold_materials(data, run)
        right = sum(materials.get(k) in v for k, v in gold_materials.items())
        print(
            f"| `{run.name}` | {avg(metrics, 'count_mae'):.3f} "
            f"| {avg(metrics, 'exact_count_rate'):.2f} | {avg(metrics, 'signed_bias'):+.2f} "
            f"| {avg(metrics, 'label_f1'):.2f} "
            f"| {avg(multi_metrics, 'count_mae'):.2f} / "
            f"{avg(multi_metrics, 'exact_count_rate'):.2f} / "
            f"{avg(multi_metrics, 'signed_bias'):+.2f} "
            f"| {round(coverage.value * coverage.n)} of {coverage.n}"
            f"{f', {stable.value:.2f}' if stable else ''} "
            f"| {round(material.value * material.n)} of {material.n} "
            f"| {right} of {len(gold_materials)} | {agree} of {len(shared)} "
            f"| ${info['spent_usd'] / repeats:.3f} "
            f"| {', '.join(info.get('models_reported', []))} |"
        )
        room_mae = [statistics.mean(per_room[r]) for r in gold]
        multi_mae = [statistics.mean(per_room[r]) for r in sorted(multi)]
        if base is None:
            base = (room_mae, multi_mae)
        else:
            diffs.append(
                f"- `{run.name}` minus baseline, per-room count MAE: all {len(room_mae)} rooms "
                f"{_diff(room_mae, base[0])}; {len(multi_mae)} multi-photo rooms "
                f"{_diff(multi_mae, base[1])}"
            )
    print()
    print("\n".join(diffs))


# ------------------------------------------------------------------------------- assets


def _ratio_diff(
    a: list[tuple[int, int]], b: list[tuple[int, int]], iterations: int = 10_000
) -> str:
    """Paired bootstrap over items of a pooled ratio: sum(numerator) / sum(denominator) of
    (a) minus that of (b), items given as ``(numerator, denominator)``."""
    arr_a, arr_b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)

    def ratio(x: np.ndarray) -> np.ndarray:
        out: np.ndarray = x[..., 0].sum(axis=-1) / np.maximum(x[..., 1].sum(axis=-1), 1)
        return out

    observed = float(ratio(arr_a) - ratio(arr_b))
    idx = np.random.default_rng(0).integers(0, len(a), size=(iterations, len(a)))
    sample = ratio(arr_a[idx]) - ratio(arr_b[idx])
    low, high = np.quantile(sample, [0.025, 0.975])
    return f"{observed:+.3f} [{low:+.3f}, {high:+.3f}]"


def assets_table(runs: list[Path]) -> None:
    data = ds.load("assets")
    assert isinstance(data, ds.Assets)
    truth = {a.id: a.bucket for a in data.assets if a.split == "dev"}
    ids = sorted(truth)
    print(
        "| Model | effort | Bucket right, repeat 1 | mean of 3 | pass^3 | macro-F1, repeat 1 "
        "| citations verified, repeat 1 / all 3 | rows with a verified citation, repeat 1 / "
        "mean of 3 | needs review, mean of 3 "
        "| loop failures | cost per study (46 rows) | seconds per row | models |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    base: dict[str, Any] | None = None
    diffs: list[str] = []
    for run in runs:
        info = _run(run)
        k = int(info["repeats"])
        raw = [json.loads(x) for x in (run / "assets_raw.jsonl").read_text().splitlines()]
        by_row: dict[str, list[dict[str, Any]]] = {}
        for record in raw:
            by_row.setdefault(record["id"], []).append(record)
        m = _rows(score_assets(data, "dev", k, json.loads((run / "assets.json").read_text())))
        right = [
            statistics.mean(r["prediction"]["bucket"] == truth[i] for r in by_row[i]) for i in ids
        ]
        verified_row = [
            statistics.mean(r["prediction"]["citations"]["verified"] > 0 for r in by_row[i])
            for i in ids
        ]
        review = [statistics.mean(bool(r["needs_review"]) for r in by_row[i]) for i in ids]
        cites = [
            (
                sum(r["prediction"]["citations"]["verified"] for r in by_row[i]),
                sum(r["prediction"]["citations"]["claimed"] for r in by_row[i]),
            )
            for i in ids
        ]
        print(
            f"| {info['model_requested']} | {info['reasoning_effort'] or '-'} "
            f"| {m['bucket_accuracy'].value:.3f} | {m['bucket_pass^1'].value:.3f} "
            f"| {m['bucket_pass^3'].value:.3f} | {m['bucket_macro_f1'].value:.3f} "
            f"| {m['citation_validity'].value:.3f} / "
            f"{sum(v for v, _ in cites) / max(sum(c for _, c in cites), 1):.3f} "
            f"| {m['rows_with_verified_citation'].value:.3f} / {statistics.mean(verified_row):.3f} "
            f"| {statistics.mean(review):.2f} | {len(info.get('loop_failures', []))} of "
            f"{len(raw)} | ${info['spent_usd'] / k:.3f} | {info['seconds_per_row']:.1f} "
            f"| {', '.join(info.get('models_reported', []))} |"
        )
        current = {"right": right, "verified": verified_row, "review": review, "cites": cites}
        if base is None:
            base = current
        else:
            diffs.append(
                f"- {info['model_requested']} minus baseline: bucket right "
                f"{_diff(right, base['right'])}; citation validity "
                f"{_ratio_diff(cites, base['cites'])}; rows with a verified citation "
                f"{_diff(verified_row, base['verified'])}; needs review "
                f"{_diff(review, base['review'])}"
            )
    print()
    print("\n".join(diffs))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("photos", "rooms", "assets"))
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--photos", type=Path, default=None)
    parser.add_argument("--aliases", type=Path, default=None)
    args = parser.parse_args()
    runs = [r.expanduser() for r in args.runs]
    if args.step == "photos":
        photos_table(runs, args.photos, args.aliases)
    elif args.step == "rooms":
        rooms_table(runs)
    else:
        assets_table(runs)


if __name__ == "__main__":
    main()
