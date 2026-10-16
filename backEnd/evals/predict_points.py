"""``python -m evals.predict_points``: the model points at every object first, then SAM 3 masks.

Point-then-count (arXiv 2603.21746: emitting one point per object before counting beats
direct counting, more so as counts grow) and "locate, label, then count" (arXiv 2512.15254).
Two kinds of pointer (``--pointer``):

* ``chat`` (default): one ``photo_points`` call per photo on ``--model`` (any provider of the
  pool); the answer lists every object with its label and centre, then states counts
  (:class:`basis.vision.marks.PointAnswer`).
* ``molmo``: Molmo-7B-D on Replicate (``zsxkib/molmo-7b``, ``--molmo-version``), one call per
  photo and query of :data:`MOLMO_QUERIES` ("Point to ..."), its ``<point>`` tags parsed
  (:func:`basis.vision.marks.parse_molmo_points`). Molmo is asked for light fixtures once; on
  photos the ``--base`` run calls ``exterior`` they count as ``outdoor_lighting`` (the label
  convention of ``photo_countables/v2``), elsewhere as ``light_fixture``; doors are scored
  pooled, so its doors are all ``interior_door``.

Then SAM 3 from the points (:func:`basis.vision.marks.resolve_points`): each point takes the
highest-scoring saved candidate of its label family under it (``--pool`` runs, at least
``--pool-min-score``), or with ``--prompt-sam`` the instance a fresh SAM 3 point prompt returns;
points whose masks overlap by more than ``--overlap`` (or share one) are one object.

Output in ``--out`` (each a predictions directory, with ``--base`` merged as in
``evals.predict_marks``): the top level counts the points; ``stated/`` the model's stated counts
(chat only); ``masked/`` the distinct masks; ``masked_all/`` the distinct masks plus points no
mask was found for. ``points_raw.jsonl`` and ``run.json`` (models reported, spend, seconds,
mask coverage).

Usage (from ``backEnd/``)::

    python -m evals.predict_points --photos DIR --out OUT --model claude-opus-5-5 \\
        --reasoning-effort medium --pool P/sam3_photos_off_v3 --base P/counts_fused_... --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import is_anthropic_model
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatRequest, Message
from basis.llm.recorder import MemoryRecorder
from basis.vision import marks as mk
from basis.vision.localize import (
    L40S_USD_PER_SECOND,
    Instance,
    LocalizerError,
    Point,
    build_replicate_localizer,
)
from basis.vision.prep import prepare_image
from evals.predict_marks import (
    image_part,
    load_instances,
    prompt_sam,
    resolved_record,
    write_predictions,
    write_raw,
)
from evals.predict_photos import photo_files
from evals.predict_tiles import ask_structured

PROMPT: Final = "photo_points"
USER_TEXT: Final = "Point at every countable object in this photo, then count them."
MOLMO_VERSION: Final = "76ebd700864218a4ca97ac1ccff068be7222272859f9ea2ae1dd4ac073fa8de8"
# (label, query); "light" becomes light_fixture or outdoor_lighting by the photo's room type.
MOLMO_QUERIES: Final[tuple[tuple[str, str], ...]] = (
    ("window", "Point to every window."),
    ("interior_door", "Point to every door."),
    ("light", "Point to every light fixture."),
    ("electrical_outlet", "Point to every electrical outlet."),
    ("kitchen_cabinet", "Point to every cabinet door and every drawer stack."),
)


def molmo_label(label: str, room: str | None) -> str:
    if label != "light":
        return label
    return "outdoor_lighting" if room == "exterior" else "light_fixture"


def base_rooms(base: Path | None) -> dict[str, list[str]]:
    """``{file: [room per repeat]}`` from the base run's ``photo_rooms.json``."""
    if base is None or not (base / "photo_rooms.json").exists():
        return {}
    data = json.loads((base / "photo_rooms.json").read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for file, value in data.items():
        out[file] = list(value["repeats"]) if isinstance(value, dict) else [value]
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_points", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pointer", choices=("chat", "molmo"), default="chat")
    parser.add_argument("--base", type=Path, default=None)
    parser.add_argument("--prompt", default="v1")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--molmo-version", default=MOLMO_VERSION)
    parser.add_argument("--molmo-temperature", type=float, default=0.1)
    parser.add_argument(
        "--molmo-interval", type=float, default=0.0, help="seconds between Replicate creates"
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--budget-usd", type=float, default=8.0)
    parser.add_argument("--only", nargs="+", default=None)
    parser.add_argument("--pool", type=Path, action="append", required=True)
    parser.add_argument("--pool-min-score", type=float, default=0.1)
    parser.add_argument("--overlap", type=float, default=mk.DEFAULT_OVERLAP)
    parser.add_argument("--prompt-sam", action="store_true")
    parser.add_argument("--from-raw", type=Path, default=None, help="redo only the SAM step")
    return parser.parse_args(argv)


async def ask_chat(
    args: argparse.Namespace, photos: Mapping[str, Path]
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    settings = load_settings()
    pool = ProviderPool.from_settings(settings)
    if pool is None:
        print("no model provider configured", file=sys.stderr)
        return None
    prompt = prompts.load(PROMPT, args.prompt)
    budget = Budget(remaining_usd=args.budget_usd)
    recorder = MemoryRecorder()
    call = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.predict_points",
        budget=budget,
        recorder=recorder,
    )
    model = args.model or settings.vision_model
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(file: str, repeat: int) -> dict[str, Any]:
        request = ChatRequest(
            model=model,
            system=prompt.text,
            messages=(
                Message(
                    role="user", content=USER_TEXT, images=(image_part(photos[file].read_bytes()),)
                ),
            ),
            response_model=mk.PointAnswer,
            max_tokens=args.max_tokens,
            temperature=0.0,
            reasoning_effort=args.reasoning_effort,
            prompt_version=prompt.version_id,
            study_id=str(call.study_id),
            activity=call.activity,
        )
        async with semaphore:
            started = time.monotonic()
            try:
                parsed, attempts, errors, reported = await ask_structured(pool, call, request)
            except Exception as exc:  # noqa: BLE001 - transient after retries: photo missing
                parsed, attempts, errors, reported = None, 0, [repr(exc)], None
            seconds = round(time.monotonic() - started, 2)
        record: dict[str, Any] = {
            "file": file,
            "repeat": repeat,
            "attempts": attempts,
            "errors": errors,
            "model": reported,
            "seconds": seconds,
            "points": None,
            "stated": None,
        }
        if isinstance(parsed, mk.PointAnswer):
            record["points"] = [o.model_dump() for o in parsed.objects]
            record["stated"] = mk.stated_counts(parsed)
        return record

    records: list[dict[str, Any]] = []
    for repeat in range(1, args.repeats + 1):
        records += await asyncio.gather(*(one(f, repeat) for f in sorted(photos)))
        print(f"repeat {repeat}: spent ${budget.spent_usd:.4f}", file=sys.stderr)
    answered = [r for r in records if r["points"] is not None]
    info = {
        "pointer": "chat",
        "prompt": prompt.version_id,
        "model_requested": model,
        "models_reported": sorted({str(r.model) for r in recorder.records} - {""}),
        "reasoning_effort": args.reasoning_effort
        or (settings.anthropic_effort if is_anthropic_model(model) else None),
        "max_tokens": args.max_tokens,
        "calls": len(recorder.records),
        "spent_usd": round(budget.spent_usd, 6),
        "usd_per_photo": round(budget.spent_usd / len(records), 6) if records else None,
        "model_seconds_per_photo": round(statistics.mean(r["seconds"] for r in answered), 2)
        if answered
        else None,
    }
    return records, info


async def ask_molmo(
    args: argparse.Namespace, photos: Mapping[str, Path]
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    token = os.environ.get("REPLICATE_API_TOKEN", "")
    if not token:
        print("REPLICATE_API_TOKEN is not set", file=sys.stderr)
        return None
    budget = Budget(remaining_usd=args.budget_usd)
    replicate = build_replicate_localizer(
        token, args.molmo_version, concurrency=args.concurrency, budget=budget
    )
    rooms = base_rooms(args.base)
    # Replicate throttles low-credit accounts to 6 prediction creates a minute; pacing the
    # creates avoids retry storms that exhaust the localizer's bounded retries.
    pace_lock = asyncio.Lock()
    last_create = [0.0]

    async def pace() -> None:
        async with pace_lock:
            wait = last_create[0] + args.molmo_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            last_create[0] = time.monotonic()

    async def one(file: str, repeat: int) -> dict[str, Any]:
        image = prepare_image(photos[file].read_bytes())
        uri = f"data:image/jpeg;base64,{image.base64}"
        room_runs = rooms.get(file, [])
        room = room_runs[(repeat - 1) % len(room_runs)] if room_runs else None
        points: list[dict[str, Any]] = []
        raw: dict[str, str] = {}
        errors: list[str] = []
        seconds = 0.0
        for label, query in MOLMO_QUERIES:
            input_ = {
                "image": uri,
                "text": query,
                "temperature": args.molmo_temperature,
                "top_k": 1,
                "max_new_tokens": 1000,
            }
            try:
                async with replicate._semaphore:
                    await pace()
                    prediction, call = await replicate._predict(input_, (query,))
            except LocalizerError as exc:
                errors.append(f"{query}: {exc}")
                continue
            seconds += call.predict_seconds
            output = prediction.get("output")
            text = "".join(output) if isinstance(output, list) else str(output or "")
            raw[query] = text
            for x, y in mk.parse_molmo_points(text):
                points.append({"label": molmo_label(label, room), "x": x, "y": y})
        return {
            "file": file,
            "repeat": repeat,
            "attempts": 1,
            "errors": errors,
            "model": "molmo-7b-d",
            "seconds": round(seconds, 2),
            "points": points if not errors else None,
            "stated": None,
            "molmo_raw": raw,
        }

    records: list[dict[str, Any]] = []
    for repeat in range(1, args.repeats + 1):
        records += await asyncio.gather(*(one(f, repeat) for f in sorted(photos)))
        print(f"repeat {repeat}: spent ${budget.spent_usd:.4f}", file=sys.stderr)
    await replicate.client.aclose()
    answered = [r for r in records if r["points"] is not None]
    info = {
        "pointer": "molmo",
        "molmo_version": args.molmo_version,
        "molmo_temperature": args.molmo_temperature,
        "molmo_top_k": 1,
        "models_reported": ["molmo-7b-d"],
        "usd_per_second": L40S_USD_PER_SECOND,
        "calls": len(records) * len(MOLMO_QUERIES),
        "spent_usd": round(budget.spent_usd, 6),
        "usd_per_photo": round(budget.spent_usd / len(records), 6) if records else None,
        "model_seconds_per_photo": round(statistics.mean(r["seconds"] for r in answered), 2)
        if answered
        else None,
        "errors": [{"file": r["file"], "errors": r["errors"]} for r in records if r["errors"]],
    }
    return records, info


async def run(args: argparse.Namespace) -> int:
    photos = {p.name: p for p in photo_files(args.photos)}
    if args.only:
        photos = {k: v for k, v in photos.items() if k in set(args.only)}
    args.out.mkdir(parents=True, exist_ok=True)
    if args.from_raw is not None:
        records = [
            json.loads(line)
            for line in args.from_raw.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = [r for r in records if r["file"] in photos]
        previous = args.out / "run.json"
        info: dict[str, Any] = json.loads(previous.read_text()) if previous.exists() else {}
        info["from_raw"] = str(args.from_raw)
    else:
        asked = await (ask_molmo if args.pointer == "molmo" else ask_chat)(args, photos)
        if asked is None:
            return 2
        records, info = asked
        write_raw(args.out / "points_raw.jsonl", records)
    repeats = max((int(r["repeat"]) for r in records), default=1)
    files = sorted(photos)

    def empty() -> dict[str, list[dict[str, int] | None]]:
        return {f: [None] * repeats for f in files}

    pointed, stated, masked, masked_all = empty(), empty(), empty(), empty()
    pool = load_instances(args.pool, args.pool_min_score)
    needs: dict[tuple[str, int, int], tuple[str, Point]] = {}
    resolved_by: dict[tuple[str, int], list[mk.Resolved]] = {}
    for r in records:
        if r.get("points") is None:
            continue
        pts = [(str(p["label"]), Point(x=float(p["x"]), y=float(p["y"]))) for p in r["points"]]
        resolved = mk.resolve_points(pts, pool.get(r["file"], []), overlap=args.overlap)
        resolved_by[(r["file"], int(r["repeat"]))] = resolved
        for i, res in enumerate(resolved):
            if res.status == "unmasked":
                needs[(r["file"], int(r["repeat"]), i)] = (res.label, res.point)
    sam_usd = 0.0
    prompted: dict[tuple[str, int, int], Instance | None] = {}
    if args.prompt_sam and needs:
        print(f"SAM 3 point prompts: {len(needs)}", file=sys.stderr)
        prompted, sam_usd = await prompt_sam(photos, needs)
    statuses: dict[str, int] = {}
    for r in records:
        if r.get("points") is None:
            continue
        key = (r["file"], int(r["repeat"]))
        index = int(r["repeat"]) - 1
        if prompted:
            pts = [(str(p["label"]), Point(x=float(p["x"]), y=float(p["y"]))) for p in r["points"]]
            mine = {i: v for (f, rep, i), v in prompted.items() if (f, rep) == key}
            resolved_by[key] = mk.resolve_points(
                pts, pool.get(r["file"], []), overlap=args.overlap, prompted=mine
            )
        resolved = resolved_by[key]
        pointed[r["file"]][index] = mk.add_counts(*({str(p["label"]): 1} for p in r["points"]))
        if r.get("stated") is not None:
            stated[r["file"]][index] = r["stated"]
        masked[r["file"]][index] = mk.resolved_counts(resolved, include_unmasked=False)
        masked_all[r["file"]][index] = mk.resolved_counts(resolved, include_unmasked=True)
        r["resolved"] = [resolved_record(x) for x in resolved]
        for x in resolved:
            statuses[x.status] = statuses.get(x.status, 0) + 1
    write_predictions(args.out, pointed, args.base)
    if any(v is not None for runs in stated.values() for v in runs):
        write_predictions(args.out / "stated", stated, args.base)
    write_predictions(args.out / "masked", masked, args.base)
    write_predictions(args.out / "masked_all", masked_all, args.base)
    write_raw(args.out / "points_raw.jsonl", records)
    with_mask = statuses.get("pool", 0) + statuses.get("prompted", 0)
    info.update(
        {
            "photos": len(files),
            "repeats": repeats,
            "base": str(args.base) if args.base else None,
            "pool": [str(p) for p in args.pool],
            "pool_min_score": args.pool_min_score,
            "overlap": args.overlap,
            "prompt_sam": args.prompt_sam,
            "sam_point_prompts": len(prompted),
            "sam_spent_usd": round(sam_usd, 6),
            "point_status": dict(sorted(statuses.items())),
            "mask_coverage_all": round(with_mask / (with_mask + statuses.get("unmasked", 0)), 4)
            if with_mask + statuses.get("unmasked", 0)
            else None,
        }
    )
    text = json.dumps(info, indent=1, sort_keys=True)
    for sub in ("", "stated", "masked", "masked_all"):
        if (args.out / sub).is_dir():
            (args.out / sub / "run.json").write_text(text)
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
