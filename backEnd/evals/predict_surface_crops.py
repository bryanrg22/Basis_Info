"""``python -m evals.predict_surface_crops``: surface materials from segmentation-guided crops
(EVAL_REPORT section 26, experiment c; :mod:`basis.vision.surface_crops`).

``predict``: for every photo of a segmenter run (``evals.predict_detector --detector
mask2former``, its ``surfaces_raw.jsonl``) and every wall, floor and ceiling covering at
least :data:`~basis.vision.surface_crops.MIN_SHARE` of the photo, one call with the photo
and the enlarged, greyed-around crop (prompt ``surface_crop_material``)::

    <out>/surface_crops.json   {"<file>": {"floor": {"repeats": [answer, ...]}, ...}}
    <out>/crops_raw.jsonl      one line per (file, surface, repeat): answer, model, cost,
                               seconds, crop box, attempts, errors
    <out>/run.json

``score``: material accuracy per surface kind (floor, wall, wainscot band, ceiling)
against the per-photo labels (``photo_components_v1``), for crop runs (``--crops NAME=DIR``)
per-photo runs (``--per-photo NAME=DIR``, the surfaces in their ``photo_raw.jsonl``) and
crops with the per-photo surfaces where no crop answered (``--combine NAME=CROPS,PHOTOS``),
per repeat, averaged (a ``--crops`` directory may also be an ``evals.replay_rooms`` run with
surface crops on: the crops the room step asked). Over the (photo, kind) pairs with a
labelled class: *hit* = some predicted class is a labelled one; *exact* = the predicted
classes are the labelled ones; *answered* = something was predicted.

``apply``: a room run (``evals.predict_rooms``) with the materials of its wall_finish,
wainscoting, tile and ceiling_finish objects replaced by the crops' majority answer over
the room's photos (repeat r of the crops for repeat r of the rooms), written to a new
directory that ``evals.run`` scores for ``report_rows``. Objects are not added or removed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import is_anthropic_model
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatProvider, ChatRequest
from basis.llm.recorder import MemoryRecorder
from basis.vision import surface_crops as sc
from basis.vision.localize import Rle
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_multiview import call_with_retries

KINDS: tuple[sc.Kind, ...] = ("floor", "wall", "wall_lower", "ceiling")


# --------------------------------------------------------------------------- predict


def build_request(
    surface: sc.Surface,
    photo: bytes,
    crop: bytes,
    model: str,
    prompt: prompts.Prompt,
    ctx: CallContext,
    effort: str | None,
) -> ChatRequest:
    """:func:`basis.vision.surface_crops.build_request` (the pipeline's request)."""
    return sc.build_request(
        surface,
        photo,
        crop,
        model=model,
        prompt=prompt,
        study_id=str(ctx.study_id),
        activity=ctx.activity,
        effort=effort,
    )


def surfaces_to_ask(record: Mapping[str, Any]) -> list[tuple[sc.Surface, np.ndarray]]:
    """The surfaces of one ``surfaces_raw.jsonl`` record large enough to ask about."""
    found: list[tuple[sc.Surface, np.ndarray]] = []
    for surface in sc.SURFACES:
        raw = record.get("surfaces", {}).get(surface)
        if raw is None:
            continue
        mask = Rle.model_validate(raw).decode()
        if mask.mean() >= sc.MIN_SHARE:
            found.append((surface, mask))
    return found


@stops_on_account_error
async def predict(args: argparse.Namespace, provider: ChatProvider | None = None) -> int:
    settings = load_settings()
    if provider is None:
        provider = guard(ProviderPool.from_settings(settings))
        if provider is None:
            print("no model provider configured", file=sys.stderr)
            return 2
    records = [
        json.loads(line)
        for line in (args.surfaces / "surfaces_raw.jsonl").read_text().splitlines()
        if line.strip()
    ]
    prompt = prompts.load(sc.PROMPT_NAME, args.prompt)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = MemoryRecorder()
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.predict_surface_crops",
        budget=budget,
        recorder=recorder,
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    args.out.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, sc.Surface, bytes, bytes, list[int]]] = []
    for record in records:
        path = args.photos / record["file"]
        photo = path.read_bytes()
        image = Image.open(path).convert("RGB")
        for surface, mask in surfaces_to_ask(record):
            crop = sc.crop_image(image, mask)
            box = sc.mask_box(mask)
            if crop is None or box is None:
                continue
            jobs.append((record["file"], surface, photo, sc.jpeg_bytes(crop), list(box)))
    answers: dict[str, dict[str, list[Any]]] = {}
    raw_lines: list[dict[str, Any]] = []

    async def one(job: tuple[str, sc.Surface, bytes, bytes, list[int]], repeat: int) -> None:
        file, surface, photo, crop, box = job
        request = build_request(
            surface, photo, crop, args.model, prompt, ctx, args.reasoning_effort
        )
        async with semaphore:
            started = time.monotonic()
            parsed, record = await call_with_retries(provider, request, ctx)
        answer = parsed.model_dump(mode="json") if isinstance(parsed, sc.CropAnswer) else None
        answers.setdefault(file, {}).setdefault(surface, []).append((repeat, answer))
        raw_lines.append(
            {
                "file": file,
                "surface": surface,
                "repeat": repeat,
                "box": box,
                "answer": answer,
                "seconds": round(time.monotonic() - started, 2),
                **{k: v for k, v in record.items() if k != "raw"},
            }
        )

    started = time.monotonic()
    for repeat in range(1, args.repeats + 1):
        await asyncio.gather(*(one(job, repeat) for job in jobs))
        print(
            f"repeat {repeat}/{args.repeats}: {len(jobs)} crops, spent ${budget.spent_usd:.4f}",
            file=sys.stderr,
        )
    wall = time.monotonic() - started
    out = {
        file: {
            surface: {"repeats": [a for _, a in sorted(values, key=lambda v: v[0])]}
            for surface, values in by_surface.items()
        }
        for file, by_surface in answers.items()
    }
    (args.out / "surface_crops.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    with (args.out / "crops_raw.jsonl").open("w", encoding="utf-8") as raw:
        for line in sorted(raw_lines, key=lambda r: (r["repeat"], r["file"], r["surface"])):
            raw.write(json.dumps(line, sort_keys=True) + "\n")
    models = sorted({str(getattr(r, "model", "")) for r in recorder.records} - {""})
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "surfaces": str(args.surfaces),
                "photos": len(records),
                "crops": len(jobs),
                "repeats": args.repeats,
                "model_requested": args.model,
                "reasoning_effort": args.reasoning_effort
                or (settings.anthropic_effort if is_anthropic_model(args.model) else None),
                "prompt": prompt.version_id,
                "models_reported": models,
                "spent_usd": round(budget.spent_usd, 6),
                "wall_seconds": round(wall, 1),
                "unusable": sum(1 for r in raw_lines if r["answer"] is None),
            },
            indent=1,
        )
    )
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- score


def truth_classes(components_file: Path) -> dict[str, dict[sc.Kind, set[str]]]:
    data = json.loads(components_file.read_text(encoding="utf-8"))
    return {photo["file"]: sc.component_classes(photo["components"]) for photo in data["photos"]}


def replay_crops(directory: Path) -> dict[str, dict[str, dict[str, list[Any]]]]:
    """The crops an ``evals.replay_rooms`` run asked (``room_raw.jsonl``, surface crops on),
    in this module's ``surface_crops.json`` shape, repeat by repeat."""
    data: dict[str, dict[str, dict[str, list[Any]]]] = {}
    for line in (directory / "room_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        looked = record.get("surface_crops") or {}
        for photo in looked.get("photos", []):
            for crop in photo["crops"]:
                surfaces = data.setdefault(photo["file"], {})
                surfaces.setdefault(crop["surface"], {"repeats": []})["repeats"].append(
                    crop["answer"]
                )
    return data


def crop_predictions(directory: Path) -> list[dict[str, dict[sc.Kind, set[str]]]]:
    """Per repeat: file -> kind -> classes from a crop run, or from the crops of an
    ``evals.replay_rooms`` run with ``MATERIALS_SECOND_LOOK=surface_crops``."""
    path = directory / "surface_crops.json"
    data: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else replay_crops(directory)
    )
    repeats = max(len(v["repeats"]) for s in data.values() for v in s.values())
    out: list[dict[str, dict[sc.Kind, set[str]]]] = []
    for r in range(repeats):
        per_file: dict[str, dict[sc.Kind, set[str]]] = {}
        for file, surfaces in data.items():
            kinds: dict[sc.Kind, set[str]] = {k: set() for k in KINDS}
            for surface, value in surfaces.items():
                answer = value["repeats"][r] if r < len(value["repeats"]) else None
                if answer is None:
                    continue
                parsed = sc.CropAnswer.model_validate(answer)
                for kind, classes in sc.answer_classes(surface, parsed).items():
                    kinds[kind] |= classes
            per_file[file] = kinds
        out.append(per_file)
    return out


def per_photo_predictions(directory: Path) -> list[dict[str, dict[sc.Kind, set[str]]]]:
    """Per repeat: file -> kind -> classes from a per-photo run's ``photo_raw.jsonl``."""
    by_repeat: dict[int, dict[str, dict[sc.Kind, set[str]]]] = {}
    for line in (directory / "photo_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        analysis = record.get("analysis")
        if analysis is None:
            continue
        by_repeat.setdefault(int(record.get("repeat", 1)), {})[record["file"]] = (
            sc.component_classes(analysis["components"])
        )
    return [by_repeat[r] for r in sorted(by_repeat)]


def with_fallback(
    primary: Sequence[Mapping[str, Mapping[sc.Kind, set[str]]]],
    fallback: Sequence[Mapping[str, Mapping[sc.Kind, set[str]]]],
) -> list[dict[str, dict[sc.Kind, set[str]]]]:
    """Per repeat, file and kind: the primary source's classes, or the fallback's where the
    primary gave none (a surface too small to crop, or answered ``unknown``). Repeats pair up
    in order; the shorter list is cycled."""
    count = max(len(primary), len(fallback))
    out: list[dict[str, dict[sc.Kind, set[str]]]] = []
    for r in range(count):
        a, b = primary[r % len(primary)], fallback[r % len(fallback)]
        merged: dict[str, dict[sc.Kind, set[str]]] = {}
        for file in sorted(set(a) | set(b)):
            merged[file] = {
                kind: set(a.get(file, {}).get(kind, set())) or set(b.get(file, {}).get(kind, set()))
                for kind in KINDS
            }
        out.append(merged)
    return out


def material_accuracy(
    truth: Mapping[str, Mapping[sc.Kind, set[str]]],
    repeats: Sequence[Mapping[str, Mapping[sc.Kind, set[str]]]],
) -> dict[str, dict[str, float]]:
    """Per kind (and ``all``): hit, exact and answered rates, averaged over repeats."""
    result: dict[str, dict[str, list[float]]] = {}
    for predicted in repeats:
        tallies: dict[str, list[tuple[bool, bool, bool]]] = {}
        for file, kinds in truth.items():
            for kind in KINDS:
                gold = kinds.get(kind, set())
                if not gold:
                    continue
                mine = predicted.get(file, {}).get(kind, set())
                row = (bool(mine & gold), mine == gold, bool(mine))
                tallies.setdefault(kind, []).append(row)
                tallies.setdefault("all", []).append(row)
        for name, rows in tallies.items():
            entry = result.setdefault(name, {"hit": [], "exact": [], "answered": [], "n": []})
            entry["hit"].append(sum(r[0] for r in rows) / len(rows))
            entry["exact"].append(sum(r[1] for r in rows) / len(rows))
            entry["answered"].append(sum(r[2] for r in rows) / len(rows))
            entry["n"].append(float(len(rows)))
    return {k: {m: statistics.fmean(v) for m, v in e.items()} for k, e in result.items()}


def parse_named(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected NAME=DIR, got {value!r}")
    return name, Path(path).expanduser()


def score(args: argparse.Namespace) -> int:
    truth = truth_classes(args.data_dir / "photo_components_v1.json")
    systems: list[tuple[str, list[dict[str, dict[sc.Kind, set[str]]]]]] = [
        (name, crop_predictions(d)) for name, d in args.crops
    ] + [(name, per_photo_predictions(d)) for name, d in args.per_photo]
    for name, pair in args.combine:
        crops_dir, _, photos_dir = str(pair).partition(",")
        systems.append(
            (
                name,
                with_fallback(
                    crop_predictions(Path(crops_dir)), per_photo_predictions(Path(photos_dir))
                ),
            )
        )
    lines = [
        "| Source | Floor hit / exact (n) | Wall hit (n) | Wainscot hit (n) | Ceiling hit (n) "
        "| All hit / exact | Answered |",
        "|---|---|---|---|---|---|---|",
    ]
    results: dict[str, Any] = {}
    for name, repeats in systems:
        acc = material_accuracy(truth, repeats)
        results[name] = acc

        def cell(kind: str, exact: bool = False, acc: dict[str, Any] = acc) -> str:
            e = acc.get(kind)
            if e is None:
                return "-"
            text = f"{e['hit']:.2f}" + (f" / {e['exact']:.2f}" if exact else "")
            return f"{text} ({int(e['n'])})"

        lines.append(
            f"| {name} | {cell('floor', True)} | {cell('wall')} | {cell('wall_lower')} | "
            f"{cell('ceiling')} | {acc['all']['hit']:.2f} / {acc['all']['exact']:.2f} | "
            f"{acc['all']['answered']:.2f} |"
        )
    print("\n".join(lines))
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
    return 0


# --------------------------------------------------------------------------- apply

# The pipeline's tables and rule (basis.vision.surface_crops), so ``apply`` is what runs.
TO_MATERIAL = sc.TO_MATERIAL
LABEL_KIND = sc.LABEL_KIND
majority = sc.majority


def apply(args: argparse.Namespace) -> int:
    crops = crop_predictions(args.crops)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in ("room_inventory.json", "rooms.json", "room_inventory.heuristic.json"):
        if (args.rooms / name).is_file():
            shutil.copy(args.rooms / name, args.out / name)
    changed = 0
    lines: list[str] = []
    for line in (args.rooms / "room_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        repeat = int(record.get("repeat", 1))
        predicted = crops[(repeat - 1) % len(crops)]
        files = record.get("files") or record.get("images") or []
        files = [f if isinstance(f, str) else f.get("file", "") for f in files]
        for obj in record.get("objects", []):
            kind = LABEL_KIND.get(str(obj.get("label")))
            if kind is None:
                continue
            seen = [c for f in files for c in sorted(predicted.get(f, {}).get(kind, set()))]
            allowed = sc.FLOOR_TILE_CLASSES if kind == "floor" else None
            choice = majority(seen, allowed)
            if choice is None or choice not in TO_MATERIAL:
                continue
            if obj.get("material") != TO_MATERIAL[choice]:
                obj["material_before_crops"] = obj.get("material")
                obj["material"] = TO_MATERIAL[choice]
                changed += 1
        lines.append(json.dumps(record, sort_keys=True))
    (args.out / "room_raw.jsonl").write_text("\n".join(lines) + "\n")
    (args.out / "run.json").write_text(
        json.dumps(
            {"rooms": str(args.rooms), "crops": str(args.crops), "materials_changed": changed},
            indent=1,
        )
    )
    print(f"wrote {args.out}: {changed} materials changed", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- cli


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.predict_surface_crops", description=__doc__
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--surfaces", type=Path, required=True, help="segmenter run directory")
    p.add_argument("--photos", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--prompt", default="v1")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--budget-usd", type=float, default=10.0)
    s = sub.add_parser("score")
    s.add_argument("--data-dir", type=Path, required=True)
    s.add_argument("--crops", type=parse_named, action="append", default=[])
    s.add_argument("--per-photo", type=parse_named, action="append", default=[])
    s.add_argument(
        "--combine",
        type=parse_named,
        action="append",
        default=[],
        help="NAME=CROPS_DIR,PER_PHOTO_DIR: crop answers, the per-photo surfaces where none",
    )
    s.add_argument("--json", type=Path, default=None)
    a = sub.add_parser("apply")
    a.add_argument("--rooms", type=Path, required=True, help="an evals.predict_rooms run")
    a.add_argument("--crops", type=Path, required=True, help="a predict run of this module")
    a.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "predict":
        return asyncio.run(predict(args))
    if args.mode == "score":
        return score(args)
    return apply(args)


if __name__ == "__main__":
    raise SystemExit(main())
