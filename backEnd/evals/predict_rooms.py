"""``python -m evals.predict_rooms``: group photos into rooms and reconcile each room.

The per-room step of the photo pipeline (VISION_RESEARCH.md steps 2 and 3) on a directory
of photos, with the providers configured in the environment:

1. per-photo analysis: :func:`basis.vision.analyze.analyze_photo_detailed` on every photo
   once, or, with ``--per-photo DIR``, the analyses of one repeat
   (``--per-photo-repeat``, default 1) of an earlier ``evals.predict_photos`` run read
   from ``DIR/photo_raw.jsonl`` (no per-photo calls);
2. grouping: :func:`basis.vision.rooms.group_photos` (file names, ``--aliases FILE`` =
   ``{"view room_id": "room_id"}``, room type voted by the per-photo model);
3. the deterministic baseline :func:`basis.vision.reconcile.heuristic_merge` per room;
4. the reconciliation call :func:`basis.vision.reconcile.reconcile_room` per room,
   ``--repeats N`` times (the per-photo analyses are shared by the repeats, so the
   repeats measure the reconciliation call's own run-to-run variation).

Output, in what ``evals.run --predictions`` scores (``room_inventory``)::

    <out>/room_inventory.json            {"<room_id>": {"<label>": count} | {"repeats": [...]}}
    <out>/room_inventory.heuristic.json  {"<room_id>": {"<label>": count}}
    <out>/rooms.json                     the grouping: per room its id, name, floor, type,
                                         photos and the heuristic objects (indices + files)
    <out>/room_raw.jsonl                 one line per (room, repeat): images, source,
                                         review flag, every object with its image indices
                                         AND file names, dropped objects, and per call the
                                         files, attempts, model, response, raw, cost
    <out>/photo_raw.jsonl                the per-photo records (only when computed here;
                                         reusable with --per-photo)
    <out>/run.json                       summary: counts, models, prompts, spend, rejects

Usage (from ``backEnd/``, ``BASIS_ENV=dev`` and provider credentials in the environment)::

    python -m evals.predict_rooms --photos ~/basis-eval-data/inputs/photos --out out/rooms
    python -m evals.predict_rooms --photos ... --out ... --per-photo out/vision --repeats 3

Nothing here touches the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.errors import ProviderRateLimited, ProviderUnavailable
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget
from basis.llm.recorder import MemoryRecorder
from basis.vision.analyze import VisionContext, analyze_photo_detailed, released_prompt
from basis.vision.prep import ImageRejected, PreparedImage, prepare_image
from basis.vision.reconcile import (
    InventoryObject,
    ReconcileContext,
    RoomInventory,
    RoomPhoto,
    RoomResult,
    heuristic_merge,
    reconcile_room,
)
from basis.vision.rooms import AliasCycleError, PhotoInput, RoomGroup, group_photos
from basis.vision.schemas import PhotoAnalysis
from evals.predict_photos import RETRY_DELAYS_S, photo_files


class InputError(ValueError):
    """An input file (aliases, per-photo output) cannot be used; exit code 2."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_rooms", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True, help="directory of photos")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--repeats", type=int, default=1, help="reconciliation runs per room")
    parser.add_argument("--aliases", type=Path, default=None, help='{"room_id": "room_id"}')
    parser.add_argument(
        "--per-photo", type=Path, default=None, help="an evals.predict_photos output directory"
    )
    parser.add_argument("--per-photo-repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=10.0, help="stop when exceeded")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    return args


def load_aliases(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read aliases {path}: {exc}") from None
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise InputError(f"aliases {path} must be a JSON object of room_id -> room_id")
    return data


def load_per_photo(directory: Path, repeat: int) -> dict[str, PhotoAnalysis | None]:
    """``{file: analysis}`` for one repeat of a ``predict_photos`` run (``None`` for a
    photo that run rejected)."""
    path = directory / "photo_raw.jsonl"
    if not path.is_file():
        raise InputError(f"{path} does not exist")
    found: dict[str, PhotoAnalysis | None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("repeat") != repeat:
            continue
        analysis = record.get("analysis")
        found[record["file"]] = None if analysis is None else PhotoAnalysis.model_validate(analysis)
    if not found:
        raise InputError(f"{path} has no records for repeat {repeat}")
    return found


def object_record(obj: InventoryObject, files: Sequence[str]) -> dict[str, Any]:
    """An object with the file names of its image indices, for the engineer's check."""
    return {**obj.model_dump(mode="json"), "files": [files[i - 1] for i in obj.image_indices]}


def room_record(group: RoomGroup, repeat: int, result: RoomResult) -> dict[str, Any]:
    return {
        "room_id": group.room_id,
        "name": group.name,
        "floor": group.floor,
        "room_type": group.room_type.value,
        "repeat": repeat,
        "files": result.files,
        "images": len(result.files),
        "source": result.source,
        "needs_review": result.needs_review,
        "review_reason": result.review_reason,
        "objects": [object_record(o, result.files) for o in result.inventory.objects],
        "dropped": [
            {"reason": d.reason, "call": d.call, "object": d.object} for d in result.dropped
        ],
        "calls": [
            {
                "call": number,
                "images": call.images,
                "first_index": call.first_index,
                "files": call.files,
                "attempts": call.attempts,
                "provider": call.provider,
                "model": call.model,
                "cost_usd": round(call.cost_usd, 6),
                "errors": call.errors,
                "response": call.response,
                "raw": call.raw,
            }
            for number, call in enumerate(result.calls, start=1)
        ],
        "cost_usd": round(sum(c.cost_usd for c in result.calls), 6),
    }


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


async def reconcile_with_retries(
    room_id: str,
    photos: list[RoomPhoto],
    ctx: ReconcileContext,
    delays: Sequence[float] | None = None,
) -> RoomResult:
    """``reconcile_room`` with the photo runner's bounded retry on rate limits and outages
    (``evals.predict_photos.RETRY_DELAYS_S``): one provider timeout or 5xx on one room must
    not abort a whole run."""
    for delay in RETRY_DELAYS_S if delays is None else delays:
        try:
            return await reconcile_room(room_id, photos, ctx)
        except (ProviderRateLimited, ProviderUnavailable) as exc:
            print(
                f"transient provider error on room {room_id}, retrying in {delay:g}s: {exc}",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)
    return await reconcile_room(room_id, photos, ctx)


async def run(args: argparse.Namespace) -> int:
    try:
        aliases = load_aliases(args.aliases)
        group_photos([], aliases)  # refuses an alias cycle before any model call
        per_photo = (
            load_per_photo(args.per_photo, args.per_photo_repeat) if args.per_photo else None
        )
    except (InputError, AliasCycleError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = load_settings()
    pool = ProviderPool.from_settings(settings)
    if pool is None:
        print("no model provider configured (OPENAI_API_KEY or AZURE_OPENAI_*)", file=sys.stderr)
        return 2
    files = photo_files(args.photos)
    if not files:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(remaining_usd=args.budget_usd)
    recorder = MemoryRecorder()

    def call_context(activity: str) -> CallContext:
        return CallContext(
            study_id=uuid.uuid4(), run_id=None, activity=activity, budget=budget, recorder=recorder
        )

    semaphore = asyncio.Semaphore(args.concurrency)
    images: dict[str, PreparedImage] = {}
    rejected: dict[str, str] = {}
    for file in files:
        try:
            images[file.name] = prepare_image(file.read_bytes())
        except ImageRejected as exc:
            rejected[file.name] = f"{exc.reason}: {exc}"
    accepted = [f for f in files if f.name in images]

    analyses: dict[str, PhotoAnalysis | None]
    missing_per_photo: list[str] = []
    if per_photo is not None:
        missing_per_photo = [f.name for f in accepted if f.name not in per_photo]
        analyses = {f.name: per_photo.get(f.name) for f in accepted}
    else:
        vision = VisionContext(
            call=call_context("evals.predict_rooms.photo"),
            provider=pool,
            prompt=released_prompt(),
            model=settings.vision_model,
        )

        async def analyze(file: Path) -> dict[str, Any]:
            async with semaphore:
                started = time.monotonic()
                result = await analyze_photo_detailed(file.read_bytes(), vision)
                return {
                    "file": file.name,
                    "repeat": 1,
                    "analysis": result.analysis.model_dump(mode="json"),
                    "provider": result.provider,
                    "model": result.model,
                    "attempts": result.attempts,
                    "errors": result.errors,
                    "seconds": round(time.monotonic() - started, 2),
                }

        records = await asyncio.gather(*(analyze(f) for f in accepted))
        with (args.out / "photo_raw.jsonl").open("w", encoding="utf-8") as raw:
            for record in records:
                raw.write(json.dumps(record, sort_keys=True) + "\n")
        analyses = {r["file"]: PhotoAnalysis.model_validate(r["analysis"]) for r in records}
        print(f"per-photo: {len(records)} photos, spent ${budget.spent_usd:.4f}", file=sys.stderr)

    groups = group_photos([PhotoInput(f.name, analyses.get(f.name)) for f in accepted], aliases)
    heuristic: dict[str, RoomInventory] = {}
    for group in groups:
        heuristic[group.room_id] = RoomInventory(
            objects=heuristic_merge([p.analysis for p in group.photos])
        )
    _write_json(
        args.out / "rooms.json",
        [
            {
                "room_id": g.room_id,
                "name": g.name,
                "floor": g.floor,
                "room_type": g.room_type.value,
                "photos": [p.file for p in g.photos],
                "heuristic_objects": [
                    object_record(o, [p.file for p in g.photos])
                    for o in heuristic[g.room_id].objects
                ],
            }
            for g in groups
        ],
    )
    _write_json(
        args.out / "room_inventory.heuristic.json",
        {room_id: inventory.counts() for room_id, inventory in heuristic.items()},
    )

    rooms_prompt = prompts.latest("room_inventory")
    reconcile_ctx = ReconcileContext(
        call=call_context("evals.predict_rooms.room"),
        provider=pool,
        prompt=rooms_prompt,
        model=settings.vision_model,
    )

    async def reconcile(group: RoomGroup, repeat: int) -> dict[str, Any]:
        async with semaphore:
            photos = [RoomPhoto(p.file, images[p.file], p.analysis) for p in group.photos]
            result = await reconcile_with_retries(group.room_id, photos, reconcile_ctx)
            return room_record(group, repeat, result)

    inventories: dict[str, list[dict[str, int]]] = {g.room_id: [] for g in groups}
    review = 0
    with (args.out / "room_raw.jsonl").open("w", encoding="utf-8") as raw:
        for repeat in range(1, args.repeats + 1):
            records = await asyncio.gather(*(reconcile(g, repeat) for g in groups))
            for record in records:
                raw.write(json.dumps(record, sort_keys=True) + "\n")
                counts: dict[str, int] = {}
                for obj in record["objects"]:
                    counts[obj["label"]] = counts.get(obj["label"], 0) + obj["count"]
                inventories[record["room_id"]].append(counts)
                review += bool(record["needs_review"])
            print(
                f"repeat {repeat}/{args.repeats}: {len(records)} rooms, "
                f"spent ${budget.spent_usd:.4f}",
                file=sys.stderr,
            )

    def fold(values: list[dict[str, int]]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    _write_json(args.out / "room_inventory.json", {r: fold(v) for r, v in inventories.items()})
    models = sorted({r.model for r in recorder.records})
    _write_json(
        args.out / "run.json",
        {
            "photos": len(files),
            "rejected": rejected,
            "rooms": len(groups),
            "repeats": args.repeats,
            "aliases": aliases,
            "per_photo_source": (
                f"{args.per_photo}/photo_raw.jsonl repeat {args.per_photo_repeat}"
                if args.per_photo
                else "computed"
            ),
            "per_photo_missing": missing_per_photo,
            "room_results_needing_review": review,
            "vision_model_setting": settings.vision_model,
            "models_reported": models,
            "spent_usd": round(budget.spent_usd, 6),
            "prompts": {
                "photo_analysis": None if args.per_photo else released_prompt().version_id,
                "room_inventory": rooms_prompt.version_id,
            },
        },
    )
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
