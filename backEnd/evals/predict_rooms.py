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
   repeats measure the per-room calls' own run-to-run variation), with the released
   ``--room-prompt`` version (default ``v1``, pinned so a new release changes nothing
   until it is asked for) on ``--model`` (default ``VISION_MODEL``);
5. optionally (``--overlap``) the pairwise photo-overlap judgments of
   :mod:`basis.vision.overlap` per multi-photo room and repeat, either passed to the
   reconciliation call as hints (``hint``) or turned into the inventory by
   :func:`basis.vision.overlap.overlap_merge` instead of the call (``merge``);
6. optionally (``--single-photo-rooms per-photo``) no per-room call for a one-photo room:
   its per-photo analysis is its inventory (there is nothing to de-duplicate);
7. optionally (``--materials disagree|sensitive``) the surface material second look of
   :mod:`basis.vision.surface_material` on the room's inventory.

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
    <out>/results.json, report.md        one case per (room, repeat): passed (an inventory),
                                         failed (unusable model output after the retry, the
                                         heuristic stood in), test_error (the provider or
                                         the harness failed; the run then fails or stops);
                                         one failed case per photo the preparation rejected
    <out>/llm_calls.jsonl, llm/, console.log, manifest.json      the run record (evals.record)

The run files in the verification tree under ``room_inventory`` / the room prompt's version
(``--overlap merge`` makes no room-inventory call: ``overlap_merge_<overlap version>``).

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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.errors import ProviderRateLimited, ProviderRequestRejected, ProviderUnavailable
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget
from basis.vision import overlap as overlap_step
from basis.vision import surface_material
from basis.vision.analyze import VisionContext, analyze_photo_detailed, released_prompt
from basis.vision.prep import ImageRejected, PreparedImage, prepare_image
from basis.vision.reconcile import (
    INVALID_OUTPUT,
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
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_photos import RETRY_DELAYS_S, photo_files
from evals.record import FAILED, PASSED, TEST_ERROR, EvalRun

# What a room inventory files under in the verification tree.
COMPONENT = "room_inventory"


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
    parser.add_argument(
        "--room-prompt", default="v1", help="released room_inventory version (default v1)"
    )
    parser.add_argument(
        "--model", default=None, help="model of the per-room calls (default: VISION_MODEL)"
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="reasoning models only: none/minimal/low/medium/high (default: the model's)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="output budget of every per-room call (default: each step's; raise it for "
        "reasoning models)",
    )
    parser.add_argument(
        "--overlap",
        choices=("none", "hint", "merge"),
        default="none",
        help="pairwise photo-overlap judgments: as hints to the call, or as the merge",
    )
    parser.add_argument("--overlap-prompt", default="v1", help="released photo_overlap version")
    parser.add_argument(
        "--overlap-model", default=None, help="model of the overlap judgments (default: --model)"
    )
    parser.add_argument(
        "--single-photo-rooms",
        choices=("reconcile", "per-photo"),
        default="reconcile",
        help="one-photo rooms: the reconciliation call (default) or the per-photo analysis "
        "as is (nothing to de-duplicate, no call)",
    )
    parser.add_argument(
        "--materials",
        choices=("none", "disagree", "sensitive"),
        default="none",
        help="surface material second look: which surfaces (basis.vision.surface_material)",
    )
    parser.add_argument(
        "--materials-prompt", default="v3", help="released surface_material version"
    )
    parser.add_argument(
        "--materials-model", default=None, help="model of the second look (default: --model)"
    )
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


def room_record(
    group: RoomGroup,
    repeat: int,
    result: RoomResult,
    overlap: overlap_step.RoomOverlap | None = None,
    looks: Sequence[surface_material.SecondLook] = (),
) -> dict[str, Any]:
    extra_cost = (overlap.cost_usd if overlap is not None else 0.0) + sum(
        look.cost_usd for look in looks
    )
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
        "overlap": (None if overlap is None else [p.record(result.files) for p in overlap.pairs]),
        "second_look": [look.record() for look in looks],
        "cost_usd": round(sum(c.cost_usd for c in result.calls) + extra_cost, 6),
    }


def room_outcome(record: Mapping[str, Any]) -> str:
    """The case outcome of a room record (:func:`room_record`, or ``evals.replay_rooms``'s):
    ``failed`` when the model's output was unusable and the heuristic stood in
    (``invalid_model_output`` after the retry, or overlap judgments that failed), else
    ``passed``: an inventory was produced (a room flagged for dropped objects has one)."""
    reason = str(record.get("review_reason") or "")
    unusable = reason.startswith(INVALID_OUTPUT) or "failed_pairs" in reason
    return FAILED if record.get("needs_review") and unusable else PASSED


def room_detail(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the case keeps of a room record: the source (a replay record names its
    ``step`` instead), the review reason, the sizes."""
    return {
        "source": record.get("source", record.get("step")),
        "review_reason": record.get("review_reason"),
        "objects": len(record.get("objects") or []),
        "images": len(record.get("files") or []),
        "calls": len(record.get("calls") or []),
    }


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


async def with_retries[T](
    room_id: str,
    step: Callable[[], Awaitable[T]],
    delays: Sequence[float] | None = None,
) -> T:
    """One per-room step with the photo runner's bounded retry on rate limits and outages
    (``evals.predict_photos.RETRY_DELAYS_S``): one provider timeout or 5xx on one room must
    not abort a whole run."""
    for delay in RETRY_DELAYS_S if delays is None else delays:
        try:
            return await step()
        except (ProviderRateLimited, ProviderUnavailable) as exc:
            print(
                f"transient provider error on room {room_id}, retrying in {delay:g}s: {exc}",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)
    return await step()


async def reconcile_with_retries(
    room_id: str,
    photos: list[RoomPhoto],
    ctx: ReconcileContext,
    delays: Sequence[float] | None = None,
    hints: Callable[[range], str | None] | None = None,
) -> RoomResult:
    """``reconcile_room`` under :func:`with_retries`."""
    return await with_retries(
        room_id, lambda: reconcile_room(room_id, photos, ctx, hints=hints), delays
    )


@stops_on_account_error
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
    pool = guard(ProviderPool.from_settings(settings))
    if pool is None:
        print(
            "no model provider configured (OPENAI_API_KEY, ANTHROPIC_API_KEY or AZURE_OPENAI_*)",
            file=sys.stderr,
        )
        return 2
    files = photo_files(args.photos)
    if not files:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    eval_run = EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    eval_run.input("photos", args.photos)
    if args.aliases is not None:
        eval_run.input("aliases", args.aliases)
    if args.per_photo:
        eval_run.input("per_photo", args.per_photo)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = eval_run.recorder

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
            # Rejected once, before any repeat: the case is the file.
            eval_run.case(file.name, FAILED, kind="photo", detail=rejected[file.name])
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
        eval_run.prompt(vision.prompt)

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

    model = args.model or settings.vision_model
    rooms_prompt = prompts.load(COMPONENT, args.room_prompt)
    eval_run.prompt(rooms_prompt)
    # Where the run files: the room step, at the room prompt's version (surface_material
    # or photo_overlap may make more calls, so the default would name the wrong component).
    eval_run.component = COMPONENT
    eval_run.version = rooms_prompt.version
    reconcile_ctx = ReconcileContext(
        call=call_context("evals.predict_rooms.room"),
        provider=pool,
        prompt=rooms_prompt,
        model=model,
        reasoning_effort=args.reasoning_effort,
        **({"max_tokens": args.max_tokens} if args.max_tokens else {}),
    )
    overlap_prompt = (
        prompts.load(overlap_step.PROMPT_NAME, args.overlap_prompt)
        if args.overlap != "none"
        else None
    )
    eval_run.prompt(overlap_prompt)
    if overlap_prompt is not None and args.overlap == "merge":
        eval_run.version = f"overlap_merge_{overlap_prompt.version}"  # no room-inventory call
    overlap_ctx = (
        overlap_step.OverlapContext(
            call=call_context("evals.predict_rooms.overlap"),
            provider=pool,
            prompt=overlap_prompt,
            model=args.overlap_model or model,
            reasoning_effort=args.reasoning_effort,
            **({"max_tokens": args.max_tokens} if args.max_tokens else {}),
        )
        if overlap_prompt is not None
        else None
    )
    materials_prompt = (
        prompts.load(surface_material.PROMPT_NAME, args.materials_prompt)
        if args.materials != "none"
        else None
    )
    eval_run.prompt(materials_prompt)
    materials_ctx = (
        surface_material.MaterialContext(
            call=call_context("evals.predict_rooms.material"),
            provider=pool,
            prompt=materials_prompt,
            model=args.materials_model or model,
            reasoning_effort=args.reasoning_effort,
            **({"max_tokens": args.max_tokens} if args.max_tokens else {}),
        )
        if materials_prompt is not None
        else None
    )

    async def judge_with_retries(
        photos: Sequence[RoomPhoto], a: int, b: int, ctx: overlap_step.OverlapContext
    ) -> overlap_step.PairJudgment:
        return await with_retries(
            f"pair {photos[a].file} / {photos[b].file}",
            lambda: overlap_step.judge_pair(photos, a, b, ctx),
        )

    async def reconcile(group: RoomGroup, repeat: int) -> dict[str, Any]:
        try:
            return await inventory(group, repeat)
        except Exception as exc:
            # Propagates as before (the run fails, or stops on an account error); the case
            # says the provider or the harness failed on this room, except an input the
            # provider refused, which is the room's failure.
            outcome = FAILED if isinstance(exc, ProviderRequestRejected) else TEST_ERROR
            eval_run.case(f"{group.room_id}#{repeat}", outcome, kind="room", observed_error=exc)
            raise

    async def inventory(group: RoomGroup, repeat: int) -> dict[str, Any]:
        async with semaphore:
            photos = [RoomPhoto(p.file, images[p.file], p.analysis) for p in group.photos]
            overlap: overlap_step.RoomOverlap | None = None
            if overlap_ctx is not None and len(photos) > 1:
                overlap = await overlap_step.room_overlap(photos, overlap_ctx, judge_with_retries)
            if len(photos) == 1 and args.single_photo_rooms == "per-photo":
                result = RoomResult(
                    room_id=group.room_id,
                    files=[photos[0].file],
                    inventory=RoomInventory(objects=heuristic_merge([photos[0].analysis])),
                    source="heuristic",
                    review_reason="single_photo",
                )
            elif args.overlap == "merge":
                assert overlap is not None or len(photos) == 1
                objects = (
                    overlap_step.overlap_merge([p.analysis for p in photos], overlap)
                    if overlap is not None
                    else heuristic_merge([p.analysis for p in photos])
                )
                result = RoomResult(
                    room_id=group.room_id,
                    files=[p.file for p in photos],
                    inventory=RoomInventory(objects=objects),
                    source="heuristic",
                    review_reason="overlap_merge",
                )
                if overlap is not None and overlap.failed:
                    result.needs_review = True
                    result.review_reason = f"overlap_merge:failed_pairs:{len(overlap.failed)}"
            else:
                current = overlap
                hints = (
                    (lambda indices: overlap_step.render_hints(current, indices))
                    if current is not None
                    else None
                )
                result = await reconcile_with_retries(
                    group.room_id, photos, reconcile_ctx, hints=hints
                )
            looks: list[surface_material.SecondLook] = []
            if materials_ctx is not None:
                objects, looks = await with_retries(
                    group.room_id,
                    lambda: surface_material.second_look(
                        result.inventory.objects, photos, materials_ctx, args.materials
                    ),
                )
                result.inventory = RoomInventory(objects=objects)
            return room_record(group, repeat, result, overlap, looks)

    inventories: dict[str, list[dict[str, int]]] = {g.room_id: [] for g in groups}
    review = 0
    with (args.out / "room_raw.jsonl").open("w", encoding="utf-8") as raw:
        for repeat in range(1, args.repeats + 1):
            records = await asyncio.gather(*(reconcile(g, repeat) for g in groups))
            for record in records:
                raw.write(json.dumps(record, sort_keys=True) + "\n")
                eval_run.case(
                    f"{record['room_id']}#{repeat}",
                    room_outcome(record),
                    kind="room",
                    detail=room_detail(record),
                )
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
    report = {
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
        "room_model": model,
        "overlap_model": None if overlap_ctx is None else overlap_ctx.model,
        "materials_model": None if materials_ctx is None else materials_ctx.model,
        "reasoning_effort": args.reasoning_effort,
        "overlap": args.overlap,
        "single_photo_rooms": args.single_photo_rooms,
        "materials": args.materials,
        "models_reported": models,
        "spent_usd": round(budget.spent_usd, 6),
        "prompts": {
            "photo_analysis": None if args.per_photo else released_prompt().version_id,
            "room_inventory": None if args.overlap == "merge" else rooms_prompt.version_id,
            "photo_overlap": None if overlap_prompt is None else overlap_prompt.version_id,
            "surface_material": (None if materials_prompt is None else materials_prompt.version_id),
        },
    }
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    eval_run.finish(report)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
