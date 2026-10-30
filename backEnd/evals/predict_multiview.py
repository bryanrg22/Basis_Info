"""``python -m evals.predict_multiview``: room inventories or the schedule from calls that
see many photos at once (EVAL_REPORT section 26, :mod:`basis.vision.multiview`).

``--scope room``: one call per room with all its photos (no per-photo step);
``--scope house``: one call with every photo, captioned by room, returning every room's
inventory; ``--scope schedule``: one call with every photo and a schedule template
(``--template FILE``, a JSON list of row names, or ``{"rows": [{"name": ...}]}``)
returning schedule rows. Rooms come from the file names and ``--aliases`` exactly as
``evals.predict_rooms`` groups them (:func:`basis.vision.rooms.name_hint`,
:func:`basis.vision.rooms.resolve_aliases`).

Output (what ``evals.run --predictions`` and ``evals.score_schedule`` read)::

    <out>/room_inventory.json   room / house: {"<room_id>": {"repeats": [{label: count}]}}
    <out>/room_raw.jsonl        room / house: one line per (room, repeat): objects with
                                material, image indices and files (report_rows reads the
                                materials from here)
    <out>/schedule.json         schedule: {"repeats": [[row, ...], ...]}
    <out>/calls.jsonl           every call: scope, room, files, attempts, model, cost,
                                seconds, dropped entries, errors, the raw response
    <out>/run.json              summary: model, effort, prompt, spend, seconds, models
    <out>/llm_calls.jsonl, llm/, run.log, manifest.json   the run record (evals.record)

An unusable answer is retried once (as the reconciliation call does); a room or repeat
whose answer stays unusable is recorded with an empty inventory and an error, never
filled in from another source.
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

from pydantic import BaseModel

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.cost import cost_usd, is_anthropic_model
from basis.llm.errors import (
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatProvider, ChatRequest
from basis.vision import multiview
from basis.vision.multiview import CallSettings, Checked, Photo, Scope
from basis.vision.prep import prepare_image
from basis.vision.reconcile import ModelRoomInventory
from basis.vision.rooms import name_hint, resolve_aliases
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_photos import RETRY_DELAYS_S, photo_files
from evals.record import EvalRun

MAX_ATTEMPTS = 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_multiview", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scope", choices=("room", "house", "schedule"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--prompt", default="v1", help="released version of the scope's prompt")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--aliases", type=Path, default=None, help='{"room_id": "room_id"}')
    parser.add_argument("--template", type=Path, default=None, help="schedule row names (JSON)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=10.0)
    return parser.parse_args(argv)


def read_template(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) else data
    names = [r["name"] if isinstance(r, dict) else str(r) for r in rows]
    if not names or len(set(names)) != len(names):
        raise ValueError(f"{path}: the template needs unique row names")
    return names


def room_of(file: str, aliases: dict[str, str]) -> str:
    room = name_hint(file).room_id
    return aliases.get(room, room)


def group(photos: Sequence[Photo]) -> dict[str, list[Photo]]:
    rooms: dict[str, list[Photo]] = {}
    for p in photos:
        rooms.setdefault(p.room_id, []).append(p)
    return rooms


async def call_with_retries(
    provider: ChatProvider, request: ChatRequest, ctx: CallContext
) -> tuple[BaseModel | None, dict[str, Any]]:
    """The parsed answer (or None after :data:`MAX_ATTEMPTS` unusable ones) and a record
    of the attempts; transient errors are retried with the photo runner's delays."""
    record: dict[str, Any] = {"attempts": 0, "errors": [], "cost_usd": 0.0, "raw": []}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        record["attempts"] = attempt
        started = time.monotonic()
        response = None
        for delay in (*RETRY_DELAYS_S, None):
            try:
                response = await call_model(provider, request, ctx)
                break
            except ProviderBadResponse as exc:
                record["errors"].append(f"attempt {attempt}: {exc}")
                if exc.usage is not None:
                    record["cost_usd"] += cost_usd(exc.model or request.model, exc.usage)
                break
            except ProviderRequestRejected as exc:
                # The input refused: no answer and no second attempt (same input, same
                # refusal). Configuration and account errors propagate and stop the run.
                record["errors"].append(f"attempt {attempt}: {exc}")
                record["seconds"] = round(time.monotonic() - started, 2)
                return None, record
            except (ProviderRateLimited, ProviderUnavailable) as exc:
                if delay is None:
                    raise
                print(f"transient provider error, retrying in {delay:g}s: {exc}", file=sys.stderr)
                await asyncio.sleep(delay)
        record["seconds"] = round(time.monotonic() - started, 2)
        if response is None:
            continue
        record["cost_usd"] += cost_usd(response.model, response.usage)
        record["model"], record["provider"] = response.model, response.provider
        record["raw"].append(response.raw)
        if response.parsed is None:
            record["errors"].append(f"attempt {attempt}: no structured answer")
            continue
        return response.parsed, record
    return None, record


def inventories(checked: Checked) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for room, objects in checked.rooms.items():
        counts: dict[str, int] = {}
        for obj in objects:
            counts[obj.label.value] = counts.get(obj.label.value, 0) + obj.count
        out[room] = counts
    return out


def raw_objects(checked: Checked, room: str, files: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            **obj.model_dump(mode="json"),
            "files": [files[i - 1] for i in obj.image_indices],
        }
        for obj in checked.rooms.get(room, [])
    ]


@stops_on_account_error
async def run(args: argparse.Namespace, provider: ChatProvider | None = None) -> int:
    settings = load_settings()
    if provider is None:
        provider = guard(ProviderPool.from_settings(settings))
        if provider is None:
            print("no model provider configured", file=sys.stderr)
            return 2
    scope: Scope = args.scope
    template = read_template(args.template) if args.template else []
    if scope == "schedule" and not template:
        print("--scope schedule needs --template", file=sys.stderr)
        return 2
    aliases = (
        resolve_aliases(json.loads(args.aliases.read_text(encoding="utf-8")))
        if args.aliases
        else {}
    )
    files = photo_files(args.photos)
    photos = [Photo(f.name, room_of(f.name, aliases), prepare_image(f.read_bytes())) for f in files]
    prompt = prompts.load(multiview.PROMPTS[scope], args.prompt)
    args.out.mkdir(parents=True, exist_ok=True)
    rec = EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    rec.input("photos", args.photos)
    if args.aliases:
        rec.input("aliases", args.aliases)
    if args.template:
        rec.input("template", args.template)
    rec.prompt(prompt)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = rec.recorder
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.predict_multiview",
        budget=budget,
        recorder=recorder,
    )
    call_settings = CallSettings(
        model=args.model,
        prompt=prompt,
        study_id=str(ctx.study_id),
        activity=ctx.activity,
        max_tokens=args.max_tokens or multiview.MAX_TOKENS[scope],
        reasoning_effort=args.reasoning_effort,
    )
    rooms = group(photos)
    semaphore = asyncio.Semaphore(args.concurrency)
    per_room: dict[str, list[dict[str, int]]] = {room: [] for room in rooms}
    raw_lines: list[dict[str, Any]] = []
    schedules: list[list[dict[str, Any]]] = []
    calls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    async def one_room(room: str, repeat: int) -> None:
        mine = rooms[room]
        async with semaphore:
            request = multiview.build_request("room", mine, call_settings)
            parsed, record = await call_with_retries(provider, request, ctx)
        checked = (
            multiview.check_room(parsed, room, len(mine))
            if isinstance(parsed, ModelRoomInventory)
            else Checked(rooms={room: []})
        )
        if parsed is None:
            errors.append({"room": room, "repeat": repeat, "errors": record["errors"]})
        per_room[room].append(inventories(checked).get(room, {}))
        raw_lines.append(
            {
                "room_id": room,
                "repeat": repeat,
                "files": [p.file for p in mine],
                "objects": raw_objects(checked, room, [p.file for p in mine]),
                "dropped": checked.dropped,
                "usable": parsed is not None,
            }
        )
        calls.append({"scope": "room", "room": room, "repeat": repeat, **record})

    async def one_property(repeat: int) -> None:
        request = multiview.build_request(scope, photos, call_settings, template)
        parsed, record = await call_with_retries(provider, request, ctx)
        names = [p.file for p in photos]
        if parsed is None:
            errors.append({"repeat": repeat, "errors": record["errors"]})
        if scope == "house":
            checked = (
                multiview.check_house(parsed, photos)
                if isinstance(parsed, multiview.ModelHouseInventory)
                else Checked(rooms={room: [] for room in rooms})
            )
            counts = inventories(checked)
            for room in rooms:
                per_room[room].append(counts.get(room, {}))
                raw_lines.append(
                    {
                        "room_id": room,
                        "repeat": repeat,
                        "files": [p.file for p in rooms[room]],
                        "objects": raw_objects(checked, room, names),
                        "usable": parsed is not None,
                    }
                )
        else:
            checked = (
                multiview.check_schedule(parsed, photos, template)
                if isinstance(parsed, multiview.ModelSchedule)
                else Checked()
            )
            schedules.append(checked.rows)
        calls.append({"scope": scope, "repeat": repeat, "dropped": checked.dropped, **record})

    started = time.monotonic()
    for repeat in range(1, args.repeats + 1):
        if scope == "room":
            await asyncio.gather(*(one_room(room, repeat) for room in rooms))
        else:
            await one_property(repeat)
        print(f"repeat {repeat}/{args.repeats}: spent ${budget.spent_usd:.4f}", file=sys.stderr)
    wall = time.monotonic() - started

    if scope in ("room", "house"):
        (args.out / "room_inventory.json").write_text(
            json.dumps(
                {r: (v[0] if len(v) == 1 else {"repeats": v}) for r, v in per_room.items()},
                indent=1,
                sort_keys=True,
            )
        )
        with (args.out / "room_raw.jsonl").open("w", encoding="utf-8") as raw:
            for line in sorted(raw_lines, key=lambda r: (r["repeat"], r["room_id"])):
                raw.write(json.dumps(line, sort_keys=True) + "\n")
    else:
        (args.out / "schedule.json").write_text(
            json.dumps({"repeats": schedules}, indent=1, sort_keys=True)
        )
    with (args.out / "calls.jsonl").open("w", encoding="utf-8") as out:
        for call in calls:
            out.write(json.dumps(call, sort_keys=True, default=str) + "\n")
    models = sorted({str(getattr(r, "model", "")) for r in recorder.records} - {""})
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    rec.finish(  # run.json and the manifest
        {
            "scope": scope,
            "photos": len(photos),
            "rooms": len(rooms),
            "repeats": args.repeats,
            "model_requested": args.model,
            "reasoning_effort": args.reasoning_effort
            or (settings.anthropic_effort if is_anthropic_model(args.model) else None),
            "max_tokens": call_settings.max_tokens,
            "prompt": prompt.version_id,
            "aliases": aliases,
            "template_rows": len(template) or None,
            "models_reported": models,
            "spent_usd": round(budget.spent_usd, 6),
            "wall_seconds": round(wall, 1),
            "seconds_per_repeat": round(wall / args.repeats, 1),
            "calls": len(calls),
            "errors": errors,
        }
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
