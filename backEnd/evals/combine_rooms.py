"""``python -m evals.combine_rooms``: one room run's inventories with chosen rooms taken from
another run (EVAL_REPORT section 26, combination f).

``--base DIR`` supplies every room; ``--override DIR`` supplies the rooms named by
``--rooms`` (room ids) or, with ``--exterior``, the rooms whose id starts with ``x_`` (the
file-name rule's no-floor token: site and exterior views, :func:`basis.vision.rooms.name_hint`).
Both ``room_inventory.json`` (repeats included) and the ``room_raw.jsonl`` lines (materials,
which ``report_rows`` reads) are taken from the run that supplies the room, repeat by repeat
(a run with fewer repeats is cycled, as ``evals.run`` cycles saved predictions). The rule is
fixed by the caller before scoring; nothing here looks at the labels.

Output in ``--out``: ``room_inventory.json``, ``room_raw.jsonl``, ``run.json`` (base,
override, the rooms taken), and ``console.log`` / ``manifest.json`` / ``results.json`` /
``report.md``, the run record (:mod:`evals.record`; filed under ``room_inventory`` at the
base run's tree version, one passed case per room written, a test error per room the
override lacks).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals import record
from evals.merge_photo_runs import source_manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.combine_rooms", description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--override", type=Path, required=True)
    parser.add_argument("--rooms", nargs="*", default=[])
    parser.add_argument("--exterior", action="store_true", help="override every x_ room")
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def _repeats(value: Any) -> list[Any]:
    return list(value["repeats"]) if isinstance(value, dict) and "repeats" in value else [value]


def _raw(directory: Path) -> dict[tuple[str, int], dict[str, Any]]:
    lines = (directory / "room_raw.jsonl").read_text(encoding="utf-8").splitlines()
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for line in lines:
        if line.strip():
            record = json.loads(line)
            out[(str(record["room_id"]), int(record.get("repeat", 1)))] = record
    return out


def base_version(base: Path) -> str:
    """The version the base run is filed under in the verification tree (its manifest's
    ``tree.version``; the combination changes nothing about the prompt), else unversioned."""
    tree = source_manifest(base).get("tree") or {}
    version = tree.get("version")
    return str(version) if version else record.UNVERSIONED


def combine(args: argparse.Namespace) -> int:
    run = record.EvalRun.begin(
        args.out,
        harness=__name__,
        args=args,
        component="room_inventory",
        version=base_version(args.base),
    )
    try:
        return _combine(args, run)
    except BaseException as exc:
        record.finish_pending(args, exit_code=1, error=exc)
        raise


def _combine(args: argparse.Namespace, run: record.EvalRun) -> int:
    run.input("base", args.base)
    run.input("override", args.override)
    run.note(exterior=args.exterior)
    base = json.loads((args.base / "room_inventory.json").read_text(encoding="utf-8"))
    other = json.loads((args.override / "room_inventory.json").read_text(encoding="utf-8"))
    chosen = set(args.rooms) | ({r for r in base if r.startswith("x_")} if args.exterior else set())
    missing = sorted(chosen - set(other))
    if missing:
        print(f"rooms not in {args.override}: {missing}", file=sys.stderr)
        for room in missing:
            run.case(room, record.TEST_ERROR, kind="room", detail=f"not in {args.override}")
        run.finish(None, exit_code=2)
        return 2
    repeats = max(len(_repeats(v)) for v in [*base.values(), *other.values()])
    base_raw, other_raw = _raw(args.base), _raw(args.override)
    inventory: dict[str, Any] = {}
    lines: list[dict[str, Any]] = []
    for room in sorted(set(base) | chosen):
        source, raw = (other, other_raw) if room in chosen else (base, base_raw)
        values = _repeats(source[room])
        per_repeat = [values[r % len(values)] for r in range(repeats)]
        inventory[room] = per_repeat[0] if repeats == 1 else {"repeats": per_repeat}
        run.case(
            room,
            record.PASSED,
            kind="room",
            detail={"from": "override" if room in chosen else "base", "repeats": repeats},
        )
        raw_repeats = sorted(rep for (rid, rep) in raw if rid == room)
        for r in range(1, repeats + 1):
            if raw_repeats:
                row = dict(raw[(room, raw_repeats[(r - 1) % len(raw_repeats)])])
                row["repeat"] = r
                row["combined_from"] = str(args.override if room in chosen else args.base)
                lines.append(row)
    (args.out / "room_inventory.json").write_text(json.dumps(inventory, indent=1, sort_keys=True))
    (args.out / "room_raw.jsonl").write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines)
    )
    print(f"wrote {args.out}: {len(chosen)} rooms from {args.override}", file=sys.stderr)
    run.finish({"base": str(args.base), "override": str(args.override), "rooms": sorted(chosen)})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return combine(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
