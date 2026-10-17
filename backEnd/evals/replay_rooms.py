"""``python -m evals.replay_rooms``: the production room step on stored per-photo outputs.

What ``reconcile_rooms`` does per room, without the database: the room step's configuration
is built from the settings exactly as the activity builds it (``ROOM_*``, ``ROOM_STEP_MODE``,
``ROOM_CALL_*``, ``MATERIALS_SECOND_LOOK``, ``SURFACE_*``), and each room goes through
:func:`basis.vision.room_step.inventory_room` with its voted room type (which picks the path,
:func:`basis.vision.room_step.room_path`) and then, with surface crops on,
:func:`basis.vision.surface_crops.look_at_room`. So flipping a setting here is flipping it
in the worker (EVAL_REPORT 26.10: confirm on a second package before making it the default).

Inputs as the activity sees them: the photos grouped by file name and ``--aliases``
(:func:`basis.vision.rooms.group_photos`, equal to the production assignment, EVAL_REPORT
27.1), each room typed by its photos' vote; each photo's analysis is one repeat of a stored
per-photo run (``--per-photo DIR``, its ``photo_raw.jsonl``) put through the stored-row
round trip the activity reads (:func:`basis.vision.room_step.analysis_from_rows`).

Output (what ``evals.run --predictions`` scores)::

    <out>/room_inventory.json   {"<room_id>": {label: count}} (one repeat) or {"repeats": [...]}
    <out>/room_raw.jsonl        per (room, repeat): path, step, fallback, objects with
                                materials and files, calls, second looks, crops, cost
    <out>/run.json              the configuration in effect, paths taken, spend, models

Transient provider errors are retried per room (``evals.predict_rooms.with_retries``); an
account error stops the run (``evals.account_stop``).
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

from basis.config import Settings, load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatProvider
from basis.llm.recorder import MemoryRecorder
from basis.vision import room_step
from basis.vision import surface_crops as crops
from basis.vision.prep import prepare_image
from basis.vision.reconcile import RoomInventory, RoomPhoto
from basis.vision.rooms import PhotoInput, RoomGroup, group_photos, optional_value
from basis.vision.schemas import PhotoAnalysis
from basis.vision.surface_segmenter import SurfaceSegmenter
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_photos import photo_files
from evals.predict_rooms import InputError, load_aliases, load_per_photo, with_retries


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.replay_rooms", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--per-photo", type=Path, required=True, help="a stored per-photo run")
    parser.add_argument("--per-photo-repeat", type=int, default=1)
    parser.add_argument("--aliases", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4, help="rooms at a time")
    parser.add_argument("--budget-usd", type=float, default=10.0)
    return parser.parse_args(argv)


def stored_rows(analysis: PhotoAnalysis) -> list[dict[str, Any]]:
    """A photo's analysis as ``analyze_photo`` stores its component rows
    (:func:`basis.vision.rooms.write_photo`), in the shape ``reconcile_rooms`` reads back."""
    return [
        {
            "label": c.label.value,
            "material": optional_value(c.material.value),
            "attachment": optional_value(c.attachment.value),
            "confidence": c.confidence,
            "raw": {"count": c.count, "note": c.note},
        }
        for c in analysis.components
    ]


def step_config(settings: Settings) -> room_step.RoomStepConfig:
    """The activity's configuration (``ReconcileRooms.run``)."""
    return room_step.load_config(
        prompt_version=settings.room_prompt_version,
        model=settings.room_model,
        reasoning_effort=settings.room_reasoning_effort,
        single_photo=settings.room_single_photo,
        materials=settings.room_materials,
        mode=settings.room_step_mode,
        room_call_model=settings.room_call_model,
        room_call_reasoning_effort=settings.room_call_reasoning_effort,
    )


def room_record(
    group: RoomGroup,
    repeat: int,
    built: room_step.RoomStepResult,
    found: crops.RoomCrops | None,
) -> dict[str, Any]:
    result = built.result
    return {
        "room_id": group.room_id,
        "room_type": group.room_type.value,
        "repeat": repeat,
        "files": result.files,
        "path": built.path,
        "step": built.step,
        "room_call_fallback": built.room_call_fallback,
        "needs_review": result.needs_review,
        "review_reason": result.review_reason,
        "objects": [
            {**o.model_dump(mode="json"), "files": result.files_for(o)}
            for o in result.inventory.objects
        ],
        "dropped": [{"reason": d.reason, "object": d.object} for d in result.dropped],
        "calls": [
            {
                "files": c.files,
                "attempts": c.attempts,
                "model": c.model,
                "cost_usd": round(c.cost_usd, 6),
                "errors": c.errors,
                "response": c.response,
            }
            for c in result.calls
        ],
        "second_look": [look.record() for look in built.looks],
        "surface_crops": None if found is None else found.record(),
        "cost_usd": round(built.cost_usd + (found.cost_usd if found else 0.0), 6),
    }


@stops_on_account_error
async def run(
    args: argparse.Namespace,
    provider: ChatProvider | None = None,
    segmenter: SurfaceSegmenter | None = None,
) -> int:
    """``provider`` and ``segmenter`` inject fakes in tests; by default the settings'."""
    try:
        aliases = load_aliases(args.aliases)
        per_photo = load_per_photo(args.per_photo, args.per_photo_repeat)
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = load_settings()
    if provider is None:
        provider = guard(ProviderPool.from_settings(settings))
        if provider is None:
            print("no model provider configured", file=sys.stderr)
            return 2
    config = step_config(settings)
    surface_crops_on = settings.materials_second_look == "surface_crops"
    crop_prompt = (
        prompts.load(crops.PROMPT_NAME, crops.PROMPT_VERSION) if surface_crops_on else None
    )
    if surface_crops_on and segmenter is None:
        from basis.worker.main import build_surface_segmenter

        segmenter = build_surface_segmenter(settings)
    files = [f for f in photo_files(args.photos) if per_photo.get(f.name) is not None]
    originals = {f.name: f.read_bytes() for f in files}
    analyses = {f.name: per_photo[f.name] for f in files}
    groups = group_photos([PhotoInput(f.name, analyses[f.name]) for f in files], aliases)
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = MemoryRecorder()
    call = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.replay_rooms",
        budget=budget,
        recorder=recorder,
    )
    gate = asyncio.Semaphore(args.concurrency)

    async def one(group: RoomGroup, repeat: int) -> dict[str, Any]:
        room_type = group.room_type.value
        photos = []
        for p in group.photos:
            analysis = analyses[p.file]
            assert analysis is not None
            rows = stored_rows(analysis)
            photos.append(
                RoomPhoto(
                    file=p.file,
                    image=prepare_image(originals[p.file]),
                    analysis=room_step.analysis_from_rows(
                        room_type, rows, analysis.room_confidence
                    ),
                )
            )
        async with gate:
            built = await with_retries(
                group.room_id,
                lambda: room_step.inventory_room(
                    group.room_id,
                    photos,
                    config,
                    call=call,
                    provider=provider,
                    room_type=room_type,
                ),
            )
            found: crops.RoomCrops | None = None
            if crop_prompt is not None:
                objects, found = await crops.look_at_room(
                    [(p.file, originals[p.file]) for p in group.photos],
                    built.result.inventory.objects,
                    segmenter,
                    crops.CropContext(
                        call=call,
                        provider=provider,
                        prompt=crop_prompt,
                        model=settings.surface_crop_model,
                        reasoning_effort=settings.surface_crop_reasoning_effort,
                    ),
                )
                built.result.inventory = RoomInventory(objects=objects)
        return room_record(group, repeat, built, found)

    started = time.monotonic()
    inventories: dict[str, list[dict[str, int]]] = {g.room_id: [] for g in groups}
    records: list[dict[str, Any]] = []
    for repeat in range(1, args.repeats + 1):
        done = await asyncio.gather(*(one(g, repeat) for g in groups))
        for record in done:
            counts: dict[str, int] = {}
            for obj in record["objects"]:
                counts[obj["label"]] = counts.get(obj["label"], 0) + obj["count"]
            inventories[record["room_id"]].append(counts)
        records += done
        print(f"repeat {repeat}/{args.repeats}: spent ${budget.spent_usd:.4f}", file=sys.stderr)
    wall = time.monotonic() - started
    (args.out / "room_inventory.json").write_text(
        json.dumps(
            {r: (v[0] if len(v) == 1 else {"repeats": v}) for r, v in inventories.items()},
            indent=1,
            sort_keys=True,
        )
    )
    with (args.out / "room_raw.jsonl").open("w", encoding="utf-8") as raw:
        for record in sorted(records, key=lambda r: (r["repeat"], r["room_id"])):
            raw.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    paths: dict[str, int] = {}
    for record in records:
        key = record["path"] + (":fallback" if record["room_call_fallback"] else "")
        paths[key] = paths.get(key, 0) + 1
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "per_photo_source": f"{args.per_photo}/photo_raw.jsonl repeat "
                f"{args.per_photo_repeat}",
                "photos": len(files),
                "rooms": len(groups),
                "repeats": args.repeats,
                "aliases": aliases,
                "room_step_mode": config.mode,
                "room_call": None
                if config.room_call is None
                else {
                    "prompt": config.room_call.prompt.version_id,
                    "model": config.room_call.model,
                    "reasoning_effort": config.room_call.reasoning_effort,
                },
                "reconcile_room_types": sorted(room_step.RECONCILE_ROOM_TYPES),
                "reconcile": {
                    "prompt": config.room_prompt.version_id,
                    "model": config.model,
                    "reasoning_effort": config.reasoning_effort,
                    "single_photo": config.single_photo,
                    "materials": config.materials,
                },
                "materials_second_look": settings.materials_second_look,
                "surface_crop_model": settings.surface_crop_model if surface_crops_on else None,
                "segmenter": None if segmenter is None else segmenter.name,
                "paths": paths,
                "rooms_by_path": {r["room_id"]: r["path"] for r in records if r["repeat"] == 1},
                "models_reported": sorted({r.model for r in recorder.records}),
                "spent_usd": round(budget.spent_usd, 6),
                "wall_seconds": round(wall, 1),
            },
            indent=1,
        )
    )
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
