"""``python -m evals.measure_drafts_rooms``: the production room-measurement step on a stored
study's photos (EVAL_REPORT 110.3), paid only when asked.

For each room of a measured type (:data:`basis.domain.measure_drafts.INTERIOR_ROOM_TYPES`) in
the given ``rooms_api.json`` files (the rooms review's rooms, each with its photos' file names;
rooms with the same name, type and photos are measured once), the photos are read from
``--photos``, prepared as production prepares a photo, and sent with the production request
(:func:`basis.activities.measure_rooms.room_request`) and per-room rule
(:func:`basis.activities.measure_rooms.draft_room`: one more attempt on an unusable answer, a
refusal or a provider error never a number) on the production settings' provider pool (OpenAI
and Anthropic; no Azure). Every call is in the run's ledger (``llm_calls.jsonl``) under one
budget. Without ``--paid`` it prints the worst-case estimate (what the budget reserves: two
attempts a room) and makes no call; ``--paid`` refuses to start when the estimate is above
``--cap-usd``. ``--replay <run>`` reads a paid run's answers instead (no call).

Writes ``answers.json`` (each room's draft by name, type and photos) into ``--out``, which
belongs under ``$BASIS_EVAL_DATA_DIR`` (the photos and the answers are client data).
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
from typing import Any, Final

from basis.activities.measure_rooms import (
    ATTEMPTS,
    MESSAGE_TYPE,
    PlannedRoom,
    draft_room,
    room_request,
)
from basis.config import get_settings
from basis.domain.measure_drafts import INTERIOR_ROOM_TYPES
from basis.domain.rooms import normalize_room_type
from basis.llm import prompts
from basis.llm.call import CallContext, estimate_request_usd
from basis.llm.provider import Budget, ChatRequest
from basis.vision.measure_interior import PROMPT, PROMPT_VERSION
from basis.vision.prep import prepare_image
from evals import record

COMPONENT: Final = "measure_drafts"
VERSION: Final = "v1"
NAMESPACE: Final = uuid.UUID("3c1f4a52-6f0e-4d0b-9c55-110110110110")


def room_key(name: str, room_type: str, photos: Sequence[str]) -> str:
    return f"{name}|{room_type}|{'|'.join(sorted(photos))}"


def rooms_of(paths: Sequence[Path]) -> list[tuple[str, PlannedRoom]]:
    """The distinct measured rooms of the ``rooms_api.json`` files, each with its key."""
    seen: dict[str, PlannedRoom] = {}
    for path in paths:
        for room in json.loads(path.read_text(encoding="utf-8")):
            kind = normalize_room_type(room.get("room_type"))
            files = sorted(p["filename"] for p in room.get("photos") or [])
            if kind not in INTERIOR_ROOM_TYPES or not files:
                continue
            key = room_key(str(room["name"]), kind, files)
            seen.setdefault(
                key,
                PlannedRoom(
                    uuid.uuid5(NAMESPACE, key),
                    kind,
                    tuple(uuid.uuid5(NAMESPACE, f) for f in files),
                    tuple(files),
                ),
            )
    return sorted(seen.items())


def prepared(photos: Path, room: PlannedRoom) -> list[bytes]:
    return [prepare_image((photos / name).read_bytes()).jpeg for name in room.uris]


def provider(settings: Any) -> Any:
    from basis.llm.pool import ProviderPool
    from basis.llm.providers import AzureOpenAIProvider, providers_from_settings
    from evals.account_stop import guard

    chosen = [
        p for p in providers_from_settings(settings) if not isinstance(p, AzureOpenAIProvider)
    ]
    if not chosen:
        raise ValueError("the room measurements need ANTHROPIC_API_KEY (or OPENAI_API_KEY)")
    return guard(ProviderPool(chosen, concurrency=settings.llm_concurrency))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rooms", type=Path, nargs="+", help="rooms_api.json files")
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--paid", action="store_true", help="make the calls")
    parser.add_argument("--cap-usd", type=float, default=1.0)
    parser.add_argument("--replay", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="measure only the rooms of this name (repeatable): keeps a paid run under the cap",
    )
    return parser.parse_args(argv)


async def measure(
    rooms: Sequence[tuple[str, PlannedRoom]],
    requests: dict[str, ChatRequest],
    call: CallContext,
    pool: Any,
    concurrency: int,
) -> dict[str, dict[str, Any]]:
    gate = asyncio.Semaphore(concurrency)

    async def one(key: str, room: PlannedRoom) -> tuple[str, dict[str, Any]]:
        async with gate:
            started = time.monotonic()
            draft = await draft_room(pool, room, requests[key], call)
            out = draft.model_dump(mode="json")
            out["seconds"] = round(time.monotonic() - started, 2)
            return key, out

    return dict(await asyncio.gather(*(one(k, r) for k, r in rooms)))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    run = record.EvalRun.begin(
        args.out,
        harness=__name__,
        args=args,
        settings=settings,
        component=COMPONENT,
        version=VERSION,
    )
    try:
        for path in args.rooms:
            run.input(path.parent.name + "/" + path.name, path)
        rooms = rooms_of(args.rooms)
        if args.only:
            rooms = [(k, r) for k, r in rooms if k.split("|")[0] in set(args.only)]
        run.prompt(prompts.load(PROMPT, PROMPT_VERSION))
        requests = {
            key: room_request(settings, room, prepared(args.photos, room), "evals")
            for key, room in rooms
        }
        for key, request in requests.items():
            print(
                f"  {key.split('|')[0]} ({key.count('|') - 1} photos): worst case "
                f"${estimate_request_usd(request) * ATTEMPTS:.4f}"
            )
        worst = sum(estimate_request_usd(r) for r in requests.values()) * ATTEMPTS
        photos = sum(len(r.uris) for _, r in rooms)
        print(
            f"{len(rooms)} rooms, {photos} photos on {settings.measure_interior_model} "
            f"({settings.measure_interior_reasoning_effort}, {settings.measure_interior_max_tokens}"
            f" tokens): worst case ${worst:.4f} ({ATTEMPTS} attempts a room)"
        )
        summary: dict[str, Any] = {
            "rooms": len(rooms),
            "photos": photos,
            "estimate_worst_usd": round(worst, 4),
            "model": settings.measure_interior_model,
            "effort": settings.measure_interior_reasoning_effort,
            "max_tokens": settings.measure_interior_max_tokens,
            "message_types": sorted({MESSAGE_TYPE[r.room_type] for _, r in rooms}),
        }
        answers: dict[str, dict[str, Any]] = {}
        if args.replay is not None:
            answers = json.loads((args.replay / "answers.json").read_text(encoding="utf-8"))
            run.input("replay", args.replay / "answers.json")
        elif args.paid:
            if worst > args.cap_usd:
                raise ValueError(f"the estimate ${worst:.4f} is above the cap ${args.cap_usd}")
            budget = Budget(remaining_usd=args.cap_usd)
            call = CallContext(
                study_id=uuid.uuid4(),
                run_id=None,
                activity="measure_rooms",
                budget=budget,
                recorder=run.recorder,
            )
            answers = asyncio.run(
                measure(rooms, requests, call, provider(settings), args.concurrency)
            )
            summary["spent_usd"] = round(budget.spent_usd, 6)
            summary["calls"] = budget.calls
        for key, room in rooms:
            got = answers.get(key)
            if got is None:
                continue
            run.case(
                key.split("|")[0],
                "passed" if got.get("status") == "drafted" else "failed",
                detail={"status": got.get("status"), "room_type": room.room_type},
            )
        if answers:
            (args.out / "answers.json").write_text(json.dumps(answers, indent=1) + "\n")
            summary["drafted"] = sum(a.get("status") == "drafted" for a in answers.values())
            summary["cost_usd"] = round(sum(a.get("cost_usd") or 0 for a in answers.values()), 6)
        run.finish(summary)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0


__all__ = ["main", "room_key", "rooms_of"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
