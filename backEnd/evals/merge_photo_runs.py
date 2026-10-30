"""``python -m evals.merge_photo_runs``: combine several ``evals.predict_photos`` runs into one.

Task decomposition (EVAL_REPORT section 11): instead of one call per photo that asks for
everything, one call per photo per task (``photo_countables``: find and count the countable
objects; ``photo_surfaces``: name the surfaces and materials), each with its own released
prompt. This tool merges the per-photo outputs of N such runs into one run's worth of
prediction files, so ``evals.run --predictions`` scores the combination exactly as it
scores a single run:

* ``photo_rooms.json``: the room type of one run (``--room-from N``, 1-based, default the
  first; every prompt answers that question, so take it from the pass that answers it best).
* ``photo_components.json``: per photo the union of the runs' label sets, per repeat.
* ``photo_counts.json``: per photo the merged ``{label: count}``, per repeat.
* ``photo_raw.jsonl``: one merged record per (photo, repeat): components are the union by
  label with the largest count, material and attachment from the run that listed the label
  first (:func:`basis.vision.merge.merge_components`, the same code the pipeline's
  decomposed step runs); ``model`` and ``prompt`` name every source; ``seconds`` and the
  cost add up.
* ``run.json``: the sources, their prompts and the summed spend.
* ``console.log``, ``manifest.json``, ``results.json``, ``report.md``: the run record
  (:mod:`evals.record`), filed under ``photo_analysis`` at the sources' prompt versions
  joined (:func:`source_version`, ``photo_countables-v2+photo_surfaces-v1``); one case per
  merged (photo, repeat), a test error when some source run lacks it.

Usage (from ``backEnd/``)::

    python -m evals.merge_photo_runs --runs out/countables out/surfaces --out out/decomposed
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from basis.vision.merge import merge_components
from evals import record
from evals.predict_photos import label_counts

__all__ = [
    "main",
    "merge_components",
    "merge_records",
    "merge_runs",
    "parse_args",
    "read_raw",
    "source_manifest",
    "source_manifest_id",
    "source_version",
]


def source_manifest(run: Path) -> dict[str, Any]:
    """A source run's ``manifest.json`` as a dict (empty when there is none or it is unreadable)."""
    path = Path(run) / record.MANIFEST_FILE
    if not path.is_file():
        return {}
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def source_manifest_id(run: Path) -> str | None:
    """The ``id`` of a source run's ``manifest.json`` (``None`` when it has none)."""
    found = source_manifest(run).get("id")
    return str(found) if found is not None else None


# The components that are not a prompt: a run filed under one of these carries the versions
# it was made from in its tree version already (``photo_countables-v2+photo_surfaces-v1``).
_DERIVED_COMPONENTS = frozenset(record.HARNESS_COMPONENTS.values())


def _source_versions(run: Path) -> list[str]:
    """What one source run exercised, as ``<prompt>-<version>`` names: the prompts its
    manifest lists (those that made calls, else every declared one), else the component
    and version it is filed under in the verification tree; empty when the manifest says
    nothing."""
    manifest = source_manifest(run)
    components = manifest.get("components") or {}
    prompts = [p for p in components.get("prompts") or [] if isinstance(p, dict) and p.get("name")]
    called = [p for p in prompts if isinstance(p.get("calls"), int) and p["calls"] > 0]
    names = [f"{p['name']}-{p.get('version') or record.UNVERSIONED}" for p in (called or prompts)]
    if names:
        return names
    tree = manifest.get("tree") or {}
    component, version = tree.get("component"), tree.get("version")
    if not component or component == record.UNKNOWN:
        return []
    if component in _DERIVED_COMPONENTS:  # its version already joins its sources' names
        return [v for v in str(version or "").split("+") if v and v != record.UNVERSIONED]
    return [f"{component}-{version or record.UNVERSIONED}"]


def source_version(paths: Sequence[Path]) -> str:
    """The version a run derived from ``paths`` files under: every source's prompt versions
    (:func:`_source_versions`), deduplicated in order and joined with ``+``
    (``photo_countables-v2+photo_surfaces-v1``); ``unversioned`` when no source says."""
    seen: list[str] = []
    for path in paths:
        for name in _source_versions(Path(path)):
            if name not in seen:
                seen.append(name)
    return "+".join(seen) if seen else record.UNVERSIONED


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.merge_photo_runs", description=__doc__)
    parser.add_argument(
        "--runs", type=Path, nargs="+", required=True, help="predict_photos outputs"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--room-from",
        type=int,
        default=1,
        help="1-based index of the run in --runs whose room type is kept (default 1)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.room_from <= len(args.runs):
        parser.error("--room-from must index one of --runs")
    return args


def read_raw(run: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """``{(file, repeat): record}`` for the records that carry an analysis."""
    records: dict[tuple[str, int], dict[str, Any]] = {}
    path = run / "photo_raw.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "analysis" in record:
            records[(record["file"], int(record["repeat"]))] = record
    return records


def merge_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    analysis = dict(first["analysis"])
    analysis["components"] = merge_components([r["analysis"]["components"] for r in records])
    return {
        "file": first["file"],
        "repeat": first["repeat"],
        "analysis": analysis,
        "provider": first.get("provider"),
        "model": sorted({str(r.get("model")) for r in records}),
        "attempts": sum(int(r.get("attempts", 1)) for r in records),
        "errors": [e for r in records for e in r.get("errors", [])],
        "seconds": round(sum(float(r.get("seconds", 0.0)) for r in records), 2),
        "image": first.get("image"),
        "sources": len(records),
    }


def merge_runs(runs: Sequence[Path], out: Path, room_from: int = 1) -> dict[str, Any]:
    """``room_from`` is the 1-based index of the run whose room type the merge keeps."""
    run = record.EvalRun.begin(
        out, harness=__name__, component="photo_analysis", version=source_version(runs)
    )
    try:
        summary = _merge(runs, out, room_from, run)
    except BaseException as exc:
        run.finish(None, status="error", exit_code=1, error=repr(exc))
        raise
    run.finish(summary)
    return summary


def _merge(runs: Sequence[Path], out: Path, room_from: int, run: record.EvalRun) -> dict[str, Any]:
    for i, source in enumerate(runs, 1):
        run.input(f"run{i}", source)
    run.note(
        room_from=room_from,
        sources=[{"run": str(p), "manifest_id": source_manifest_id(p)} for p in runs],
    )
    raws = [read_raw(source) for source in runs]
    keys = sorted(set(raws[0]))
    rooms: dict[str, list[str]] = {}
    components: dict[str, list[list[str]]] = {}
    counts: dict[str, list[dict[str, int]]] = {}
    with (out / "photo_raw.jsonl").open("w", encoding="utf-8") as raw_out:
        for key in keys:
            present = [raw[key] for raw in raws if key in raw]
            absent = [i for i, raw in enumerate(raws, 1) if key not in raw]
            if absent:
                run.case(
                    f"{key[0]}#{key[1]}",
                    record.TEST_ERROR,
                    kind="photo",
                    detail=f"missing in run{', run'.join(str(i) for i in absent)}",
                )
            else:
                run.case(f"{key[0]}#{key[1]}", record.PASSED, kind="photo")
            merged = merge_records(present)
            room_source = raws[room_from - 1].get(key)
            if room_source is not None:
                merged["analysis"]["room_type"] = room_source["analysis"]["room_type"]
            raw_out.write(json.dumps(merged, sort_keys=True) + "\n")
            file = key[0]
            rooms.setdefault(file, []).append(merged["analysis"]["room_type"])
            components.setdefault(file, []).append(
                sorted({c["label"] for c in merged["analysis"]["components"]})
            )
            counts.setdefault(file, []).append(label_counts(merged["analysis"]["components"]))

    def fold(values: list[Any]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    (out / "photo_rooms.json").write_text(
        json.dumps({f: fold(v) for f, v in rooms.items()}, indent=1, sort_keys=True)
    )
    (out / "photo_components.json").write_text(
        json.dumps({f: fold(v) for f, v in components.items()}, indent=1, sort_keys=True)
    )
    (out / "photo_counts.json").write_text(
        json.dumps({f: fold(v) for f, v in counts.items()}, indent=1, sort_keys=True)
    )
    sources = []
    spent = 0.0
    for source in runs:
        summary_path = source / "run.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        sources.append(
            {
                "run": str(source),
                "prompt": summary.get("prompt"),
                "spent_usd": summary.get("spent_usd"),
            }
        )
        spent += float(summary.get("spent_usd") or 0.0)
    summary = {
        "merged_from": sources,
        "room_from": str(runs[room_from - 1]),
        "photos": len(rooms),
        "repeats": max((len(v) for v in rooms.values()), default=0),
        "spent_usd": round(spent, 6),
        "missing_in_some_run": sorted(
            {f"{k[0]}#{k[1]}" for raw in raws[1:] for k in keys if k not in raw}
        ),
    }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = merge_runs(args.runs, args.out, room_from=args.room_from)
    print(
        f"wrote {args.out}: {summary['photos']} photos, {summary['repeats']} repeats",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
