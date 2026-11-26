"""``python -m evals.measure_drafts_replay``: the AI-drafted measurements replayed through the
pipeline's own functions on stored exports and on the HABS houses (EVAL_REPORT 110).

**On the stored exports** (EVAL_REPORT 87's: a client study run twice). Each export's takeoff is
recomputed under today's rules (:func:`evals.takeoff_constants.retakeoff`, then the cabinet-run
rule of EVAL_REPORT 78 that holds a countertop or a cabinet run priced per foot unpriced, as
``process_assets`` writes it now: the regression line, *today*); then, as ``process_assets``
does with the settings on (:mod:`basis.domain.measure_drafts`):

* *exterior*: the roof, siding and gutter rows from the client's lidar shell (re-derived from the
  cached points of EVAL_REPORT 105.3 by :func:`basis.geo.shell.derive`, stored as the gather
  stores it), the footprint and everything else today's;
* *interior*: each room the paid room run measured (``--answers``, keyed by room name, type and
  photos as ``evals.measure_drafts_rooms`` writes them) sizes its floor, wall, trim, countertop
  and cabinet rows; rooms not measured keep today's rows;
* *both*.

Every setup is priced as EVAL_REPORT 49 prices (``evals.reprice_export``, installed cost with
the general contractor's O&P pro rata), replayed through the structure model
(``evals.structure_replay``, production) and scored against the engineer's report
(``evals.e2e_report``): the itemized total, the class shares, the reconciliation gap, the
blockers it leaves (``input:cabinet_run`` rows), and each draft's exposure (its expected error
times its priced total), swept over thresholds (``--thresholds``).

**On the HABS houses** (EVAL_REPORT 103/105's truth): the shell of each house with lidar,
drafted by :func:`basis.domain.measure_drafts.exterior_drafts`, against today's formulas on the
plan's GLA (EVAL_REPORT 105's M0), per row (siding before openings, roof, gutter), in percent of
the truth and in dollars at the cost table's unit price.

Deterministic, no model call, $0 (the room answers are read). Client figures go only to
``--out`` (under ``$BASIS_EVAL_DATA_DIR``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

from basis.domain import measure_drafts as md
from basis.domain import measurements as ms
from basis.domain.cost_indices import supplement_for
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.costs import CostError, estimate_cost
from basis.domain.money_inputs import GUESSED_RUNS
from basis.domain.rooms import normalize_room_type
from basis.domain.structure import inside_model
from basis.domain.takeoffs import Takeoff, spec_for, takeoff_key
from basis.geo import shell
from evals import measure_eval as me
from evals import measure_points, record
from evals import measure_truth as mt
from evals.e2e_report import default_labels_path, load_report, load_rows
from evals.measure_drafts_rooms import room_key
from evals.reprice_export import reprice
from evals.structure_replay import PRODUCTION, ReplayError
from evals.structure_sweep import Cell, baseline_scores, run_cell
from evals.takeoff_constants import engineer_shares, priced_summary, retakeoff

COMPONENT: Final = "measure_drafts"
VERSION: Final = "v1"
CENT: Final = Decimal("0.01")
SETUPS: Final = ("today", "exterior", "interior", "both")
SHELL_LABELS: Final = ("siding", "roof", "gutter")
BUILDING_BUCKETS: Final = frozenset({"y27_5", "y39"})
THRESHOLDS: Final = (0, 25, 50, 100, 150, 200, 250, 500, 1000, 2500)
HABS_HOUSES: Final = ("il0491", "mo1222", "mo1223", "mo1224", "mo1857", "va1914", "ga0157")
FT2: Final = 1 / (0.3048 * 0.3048)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- the exports


def cabinet_rule(document: dict[str, Any]) -> int:
    """EVAL_REPORT 78 on an export priced before it: a countertop or a cabinet run measured as
    the room's perimeter is not priced (``input:cabinet_run``). Returns the rows it held."""
    held = 0
    for asset in document["assets"]:
        inputs = asset.get("takeoff_inputs") or {}
        key = takeoff_key(str(inputs.get("component") or asset.get("label") or ""))
        if key not in GUESSED_RUNS or spec_for(key).method != "perimeter":
            continue
        if inputs.get("measured_with_asset") or inputs.get("quantity_source") == "engineer":
            continue
        asset["quantity"] = "0.0000"
        asset["total"] = None
        asset["unit_cost"] = None
        asset["takeoff_inputs"] = {**inputs, "missing_input": "cabinet_run"}
        reasons = [r for r in str(asset.get("review_reason") or "").split("; ") if r]
        asset["review_reason"] = "; ".join(dict.fromkeys([*reasons, "input:cabinet_run"]))
        held += 1
    return held


@dataclass
class DraftRow:
    asset_id: str
    label: str
    room: str | None
    bucket: str
    kind: str  # exterior | interior
    today_quantity: str
    quantity: str
    unit: str
    error_pct: Decimal
    replaced_missing: str | None
    total: Decimal | None = None

    @property
    def exposure(self) -> Decimal | None:
        return None if self.total is None else _q(abs(self.total) * self.error_pct / 100)

    def moves_classes(self) -> bool:
        """Whether its dollars can move between recovery classes: a building-class row inside
        the square-foot model is carved from a building-class component (EVAL_REPORT 87.1)."""
        return not (self.bucket in BUILDING_BUCKETS and inside_model(self.label))


def _set(asset: dict[str, Any], quantity: Decimal, record_: dict[str, Any], kind: str) -> None:
    inputs = dict(asset.get("takeoff_inputs") or {})
    missing = inputs.pop("missing_input", None)
    inputs["quantity_source"] = md.QUANTITY_SOURCE
    inputs[md.DRAFT] = record_
    asset["takeoff_inputs"] = inputs
    asset["quantity"] = format(quantity.quantize(Decimal("0.0001")), "f")
    reasons = [
        r
        for r in str(asset.get("review_reason") or "").split("; ")
        if r and r != f"input:{missing}"
    ]
    asset["review_reason"] = "; ".join(dict.fromkeys([*reasons, md.DRAFT_UNVERIFIED]))
    if asset.get("total") is None:
        asset["total"] = "0"  # priced by the repricing below; put back when it cannot be
        asset["unit_cost"] = "0"
        asset["_was_unpriced"] = True
    elif asset.get("unit_cost") is not None:
        asset["total"] = str(_q(Decimal(str(asset["unit_cost"])) * quantity))
    asset["_draft_kind"] = kind


def apply_exterior(document: dict[str, Any], source: md.ShellSource) -> list[DraftRow]:
    drafts, _ = md.exterior_drafts(source, {}, {})
    rows = []
    for asset in document["assets"]:
        inputs = asset.get("takeoff_inputs") or {}
        key = takeoff_key(str(inputs.get("component") or asset.get("label") or ""))
        draft = drafts.get(key)
        if draft is None or inputs.get("measured_with_asset"):
            continue
        if inputs.get("quantity_source") not in ("appraisal_derived", None):
            continue
        today = str(asset.get("quantity"))
        _set(asset, draft.quantity, draft.record, "exterior")
        rows.append(
            DraftRow(
                str(asset["id"]),
                key,
                None,
                str(asset.get("bucket")),
                "exterior",
                today,
                str(draft.quantity),
                draft.unit,
                md.EXTERIOR_ERROR_PCT[key][0],
                None,
            )
        )
    return rows


def room_keys(
    document: Mapping[str, Any], rooms_api: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Each export room's answer key (its name, type and photos' file names)."""
    filename = {
        p["document_id"]: p["filename"] for room in rooms_api for p in room.get("photos") or []
    }
    photos: dict[str, list[str]] = {}
    for photo in document.get("photos") or []:
        name = filename.get(photo.get("document_id"))
        if name is not None and photo.get("room_id") is not None:
            photos.setdefault(str(photo["room_id"]), []).append(name)
    out = {}
    for room in document["rooms"]:
        kind = normalize_room_type(room.get("room_type"))
        if room["id"] in photos:
            out[str(room["id"])] = room_key(str(room["name"]), kind, photos[room["id"]])
    return out


def apply_interior(
    document: dict[str, Any],
    answers: Mapping[str, Mapping[str, Any]],
    rooms_api: Sequence[Mapping[str, Any]],
) -> tuple[list[DraftRow], int]:
    """The measured rooms' rows drafted; returns the rows and the rooms drafted."""
    keys = room_keys(document, rooms_api)
    names = {str(r["id"]): str(r["name"]) for r in document["rooms"]}
    drafted_rooms: dict[str, md.RoomDraft] = {}
    for room_id, key in keys.items():
        answer = answers.get(key)
        if answer is None or answer.get("status") != "drafted":
            continue
        kind = key.split("|")[1]
        drafted_rooms[room_id] = md.RoomDraft(
            room_type=kind,
            quantities=md.answer_quantities(answer["quantities"], kind),
            model=str(answer["model"]),
            photos=tuple(answer["photos"]),
        )
    rows = []
    for asset in document["assets"]:
        room = drafted_rooms.get(str(asset.get("room_id")))
        inputs = asset.get("takeoff_inputs") or {}
        if room is None or inputs.get("measured_with_asset"):
            continue
        key = takeoff_key(str(inputs.get("component") or asset.get("label") or ""))
        if inputs.get("method") not in ("area", "perimeter") or spec_for(key).method == "count":
            continue
        today = Takeoff(
            component=key,
            quantity=Decimal(str(asset.get("quantity") or "0")),
            unit=str(asset.get("unit")),
            formula=str(asset.get("formula") or ""),
            inputs={
                k: v for k, v in inputs.items() if isinstance(v, str | int | bool) or v is None
            },
            method=str(inputs.get("method")),  # type: ignore[arg-type]
        )
        missing = inputs.get("missing_input")
        draft = md.room_takeoff(key, room, today, missing if isinstance(missing, str) else None)
        if draft is None:
            continue
        old = str(asset.get("quantity"))
        _set(asset, draft.quantity, draft.record, "interior")
        rows.append(
            DraftRow(
                str(asset["id"]),
                key,
                names.get(str(asset.get("room_id"))),
                str(asset.get("bucket")),
                "interior",
                old,
                str(draft.quantity),
                draft.unit,
                md.INTERIOR_ERROR_PCT[md.ROW_QUANTITY[key]][0],
                missing if isinstance(missing, str) else None,
            )
        )
    return rows, len(drafted_rooms)


def after_reprice(document: dict[str, Any], rows: Sequence[DraftRow]) -> None:
    """Put a draft row the repricing could not price back to unpriced, and record each draft
    row's priced total."""
    by_id = {str(a["id"]): a for a in document["assets"]}
    for asset in document["assets"]:
        if asset.pop("_was_unpriced", False) and (asset.get("takeoff_inputs") or {}).get(
            "reprice_error"
        ):
            asset["total"] = None
            asset["unit_cost"] = None
        asset.pop("_draft_kind", None)
    for row in rows:
        asset = by_id.get(row.asset_id)
        total = (
            None if asset is None or asset.get("total") is None else Decimal(str(asset["total"]))
        )
        row.total = total


@dataclass
class SetupResult:
    export: str
    setup: str
    seconds: float
    priced: int
    priced_total: str
    itemized_shares: dict[str, float]
    cabinet_run_blockers: int
    drafts: list[DraftRow] = field(default_factory=list)
    rooms_drafted: int = 0
    cell: Cell | None = None


def cabinet_blockers(document: Mapping[str, Any]) -> int:
    return sum("input:cabinet_run" in str(a.get("review_reason") or "") for a in document["assets"])


def run_export(
    name: str,
    document: dict[str, Any],
    *,
    setup: str,
    source: md.ShellSource | None,
    answers: Mapping[str, Mapping[str, Any]],
    rooms_api: Sequence[Mapping[str, Any]],
    table: CostTable,
    supplement: Any,
    rows: Any,
    report: Any,
    baseline: tuple[int, int],
) -> SetupResult:
    started = time.perf_counter()
    taken, _ = retakeoff(document)
    cabinet_rule(taken)
    drafts: list[DraftRow] = []
    rooms = 0
    if setup in ("exterior", "both") and source is not None:
        drafts += apply_exterior(taken, source)
    if setup in ("interior", "both"):
        found, rooms = apply_interior(taken, answers, rooms_api)
        drafts += found
    repriced, _ = reprice(
        taken,
        cost_table=table,
        state=None,
        zip_code=None,
        supplement=supplement,
        cost_basis="installed",
        overhead="pro_rata",
    )
    after_reprice(repriced, drafts)
    cell = run_cell(
        name,
        repriced,
        PRODUCTION,
        cost_table=table,
        rows=rows,
        report=report,
        baseline=baseline,
        supplement=supplement,
        label=setup,
    )
    priced = priced_summary(repriced["assets"])
    return SetupResult(
        export=name,
        setup=setup,
        seconds=round(time.perf_counter() - started, 3),
        priced=priced["priced"],
        priced_total=priced["total"],
        itemized_shares=priced["shares"],
        cabinet_run_blockers=cabinet_blockers(repriced),
        drafts=drafts,
        rooms_drafted=rooms,
        cell=cell,
    )


def sweep_rows(
    drafts: Sequence[DraftRow], thresholds: Sequence[int], *, moving_only: bool = False
) -> list[dict[str, Any]]:
    chosen = [d for d in drafts if d.moves_classes()] if moving_only else list(drafts)
    swept = md.sweep(
        [(d.total, d.error_pct) for d in chosen],
        [Decimal(t) for t in thresholds],
    )
    out = []
    for s in swept:
        out.append(
            {
                "threshold_usd": int(s.threshold),
                "drafts": s.drafts,
                "held": s.held,
                "held_usd": str(_q(s.held_usd)),
                "drafted_usd": str(_q(s.drafted_usd)),
                "exposure_held_usd": str(s.exposure_held_usd),
                "exposure_usd": str(s.exposure_usd),
                "exposure_held_share_pct": round(
                    float(s.exposure_held_usd / s.exposure_usd * 100) if s.exposure_usd else 0.0, 1
                ),
            }
        )
    return out


# --------------------------------------------------------------------------- the HABS houses


def unit_price(table: CostTable, label: str) -> Decimal | None:
    """The cost table's installed unit price of ``label`` (2020 base, no location factor)."""
    spec = {"roof": "SF", "siding": "SF", "gutter": "LF"}[label]
    try:
        cost = estimate_cost(
            Takeoff(component=label, quantity=Decimal(1), unit=spec, formula="1"),
            2020,
            Decimal(1),
            table=table,
            label=label,
            basis="installed",
        )
    except CostError:
        return None
    return cost.unit_cost


def habs_rows(geo: Path, truth_dir: Path, table: CostTable) -> list[dict[str, Any]]:
    """Per house and row: the truth, today's formula and the draft, in their units and in
    dollars at the cost table's unit price."""
    houses = {h.id: h for h in mt.load_all(truth_dir)}
    prices = {label: unit_price(table, label) for label in SHELL_LABELS}
    out = []
    for house_id in HABS_HOUSES:
        house = houses.get(house_id)
        got = me.house_points(geo, house_id)
        if house is None or got is None:
            continue
        points, meta = got
        s = shell.derive(
            points, house.location.lon, house.location.lat, me.mercator_box(meta["box_lonlat"])
        )
        drafts, skipped = md.exterior_drafts(
            md.ShellSource(house_id, str(meta.get("project")), None, shell.as_record(s)), {}, {}
        )
        m = me.appraisal_inputs(house)
        today = {
            # Today's siding before its 15 % openings: the truth is the gross face.
            "siding": float(ms.siding_area(m).quantity / (1 - ms.WALL_OPENINGS_SHARE)),  # type: ignore[union-attr]
            "roof": float(ms.roof_area(m).quantity),  # type: ignore[union-attr]
            "gutter": float(ms.gutter_length(m).quantity),  # type: ignore[union-attr]
        }
        truths = mt.truths(house)
        truth_key = {"siding": "wall_area_gross_sf", "roof": "roof_area_sf", "gutter": "gutter_ft"}
        for label in SHELL_LABELS:
            t = truths.get(truth_key[label])
            draft = drafts.get(label)
            if draft is not None and draft.shape is not None:
                value = float(draft.shape.quantity) if label == "siding" else float(draft.quantity)
            else:
                value = None
            price = prices[label]
            row: dict[str, Any] = {
                "house": house_id,
                "standing": house.standing.status if hasattr(house.standing, "status") else None,
                "row": label,
                "truth": None if t is None else t.value,
                "truth_tier": None if t is None else t.tier,
                "today": today[label],
                "draft": value,
                "skipped": skipped.get(label),
                "unit_price": None if price is None else str(price),
            }
            if t is not None:
                row["today_pct"] = me.error_pct(today[label], t.value)
                if value is not None:
                    row["draft_pct"] = me.error_pct(value, t.value)
                if price is not None:
                    p = float(price)
                    row["today_usd"] = (today[label] - t.value) * p
                    if value is not None:
                        row["draft_usd"] = (value - t.value) * p
            out.append(row)
    return out


def habs_summary(rows: Sequence[Mapping[str, Any]], houses: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for label in SHELL_LABELS:
        both = [
            r
            for r in rows
            if r["row"] == label and r["house"] in houses and "draft_pct" in r and "today_pct" in r
        ]
        if not both:
            continue
        entry: dict[str, Any] = {
            "row": label,
            "n": len(both),
            "today_signed": statistics.fmean(r["today_pct"] for r in both),
            "today_abs": statistics.fmean(abs(r["today_pct"]) for r in both),
            "draft_signed": statistics.fmean(r["draft_pct"] for r in both),
            "draft_abs": statistics.fmean(abs(r["draft_pct"]) for r in both),
            "closer": sum(abs(r["draft_pct"]) < abs(r["today_pct"]) for r in both),
        }
        if all("draft_usd" in r for r in both):
            entry["today_abs_usd"] = statistics.fmean(abs(r["today_usd"]) for r in both)
            entry["draft_abs_usd"] = statistics.fmean(abs(r["draft_usd"]) for r in both)
        out.append(entry)
    return out


# --------------------------------------------------------------------------- the run


def client_shell(points_dir: Path, gather: Path, pin: tuple[float, float]) -> dict[str, Any]:
    box = json.loads(gather.read_text(encoding="utf-8"))["finished"]["lidar"]["box_lonlat"]
    s = shell.derive(measure_points.load_points(points_dir), pin[1], pin[0], me.mercator_box(box))
    return shell.as_record(s)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exports", type=Path, nargs="+", help="rev-<n>.json files")
    parser.add_argument("--rooms", type=Path, action="append", required=True, help="per export")
    parser.add_argument("--answers", type=Path, required=True, help="the room run's answers")
    parser.add_argument("--points", type=Path, required=True, help="the client's cached points")
    parser.add_argument("--gather", type=Path, required=True, help="the client's gather result")
    parser.add_argument("--pin", type=float, nargs=2, required=True, metavar=("LAT", "LON"))
    parser.add_argument("--project", default=None)
    parser.add_argument("--cost-table", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, default=None)
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--geo", type=Path, required=True, help="the HABS houses' cached points")
    parser.add_argument("--truth", type=Path, default=mt.FIXTURES)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def _result_row(r: SetupResult) -> dict[str, Any]:
    c = r.cell
    return {
        "export": r.export,
        "setup": r.setup,
        "seconds": r.seconds,
        "priced": r.priced,
        "priced_total": r.priced_total,
        "itemized_shares": r.itemized_shares,
        "cabinet_run_blockers": r.cabinet_run_blockers,
        "rooms_drafted": r.rooms_drafted,
        "drafts": len(r.drafts),
        "final_shares": None if c is None else c.bucket_share_pct,
        "final_share_error": None if c is None else c.bucket_error_pct,
        "mean_class_error": None if c is None else c.mean_bucket_error_pct,
        "row_distance_pct": None if c is None else c.tvd_pct,
        "status": None if c is None else c.status,
        "gap_pct": None if c is None else c.gap_share_pct,
        "draft_rows": [
            {
                "label": d.label,
                "room": d.room,
                "bucket": d.bucket,
                "kind": d.kind,
                "today": d.today_quantity,
                "draft": d.quantity,
                "unit": d.unit,
                "error_pct": str(d.error_pct),
                "replaced_missing": d.replaced_missing,
                "total": None if d.total is None else str(d.total),
                "exposure": None if d.exposure is None else str(d.exposure),
                "moves_classes": d.moves_classes(),
            }
            for d in r.drafts
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        if len(args.rooms) != len(args.exports):
            raise ValueError("give one --rooms file per export, in the same order")
        rows_path = default_labels_path("e2e_rows_v1.json", args.labels_dir)
        report_path = default_labels_path("engineer_report_v1.json", args.labels_dir)
        rows, report = load_rows(rows_path), load_report(report_path)
        run.dataset(rows_path)
        run.dataset(report_path)
        table = load_cost_table(str(args.cost_table))
        run.input("cost_table", args.cost_table)
        supplement = supplement_for(None if args.supplement is None else str(args.supplement))
        answers = json.loads(args.answers.read_text(encoding="utf-8"))
        run.input("answers", args.answers)
        shell_record = client_shell(args.points, args.gather, tuple(args.pin))
        source = md.ShellSource("client", args.project, None, shell_record)
        results: list[SetupResult] = []
        for path, rooms_path in zip(args.exports, args.rooms, strict=True):
            document = json.loads(path.read_text(encoding="utf-8"))
            rooms_api = json.loads(rooms_path.read_text(encoding="utf-8"))
            name = path.parent.parent.name if path.parent.name == "export" else path.stem
            run.input(name, path)
            baseline = baseline_scores(document, name, rows, report)
            for setup in SETUPS:
                result = run_export(
                    name,
                    document,
                    setup=setup,
                    source=source,
                    answers=answers,
                    rooms_api=rooms_api,
                    table=table,
                    supplement=supplement,
                    rows=rows,
                    report=report,
                    baseline=baseline,
                )
                results.append(result)
                run.case(
                    f"{name}:{setup}",
                    "passed" if result.cell is not None and result.cell.sourced else "failed",
                    detail={"drafts": len(result.drafts), "priced_total": result.priced_total},
                )
        sweeps = {}
        for r in results:
            if r.setup == "both":
                sweeps[r.export] = {
                    "all": sweep_rows(r.drafts, THRESHOLDS),
                    "class_moving": sweep_rows(r.drafts, THRESHOLDS, moving_only=True),
                }
        habs = habs_rows(args.geo, args.truth, table)
        standing = ("il0491", "mo1222", "mo1223", "mo1224", "mo1857", "va1914")
        payload = {
            "shell": {
                k: v
                for k, v in shell_record.items()
                if k not in ("wall_outline", "roof_outline", "edges")
            },
            "results": [_result_row(r) for r in results],
            "sweeps": sweeps,
            "engineer_shares_pct": engineer_shares(report),
            "habs": habs,
            "habs_summary_standing": habs_summary(habs, standing),
            "habs_summary_all": habs_summary(habs, HABS_HOUSES),
        }
        (args.out / "replay.json").write_text(json.dumps(payload, indent=1, default=str) + "\n")
        for r in results:
            c = r.cell
            print(
                f"{r.export:28s} {r.setup:9s} drafts {len(r.drafts):3d} priced {r.priced:3d} "
                f"${r.priced_total:>12s} cabinet_run {r.cabinet_run_blockers} "
                f"class err {None if c is None else round(c.mean_bucket_error_pct or 0, 3)} "
                f"gap {None if c is None else round(c.gap_share_pct or 0, 2)} "
                f"{None if c is None else c.status}"
            )
        run.finish({"exports": len(args.exports), "setups": list(SETUPS), "cost_usd": 0})
    except (ReplayError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0


__all__ = [
    "DraftRow",
    "apply_exterior",
    "apply_interior",
    "cabinet_rule",
    "habs_rows",
    "habs_summary",
    "main",
    "room_keys",
    "sweep_rows",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
